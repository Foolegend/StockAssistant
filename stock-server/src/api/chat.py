import re
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from src.agent.UnifiedAgent import AgentConfig, ask_agent, default_system_prompt, make_llm
from src.runtime.logging_config import (
    get_logger,
    new_request_id,
    preview,
    request_context,
    setup_logging,
)
from src.services.component_dependencies import resolve_component_dependencies
from src.services.component_router import select_components
from src.services.extension_registry import ExtensionRegistry
from src.services.hook_runner import HookExecutionError, HookRunner

setup_logging()
logger = get_logger(__name__)

app = FastAPI(title="StockAssistant Agent")
extension_registry = ExtensionRegistry()
hook_runner = HookRunner(extension_registry.hooks, str(extension_registry.project_root))
_GREETING_ONLY = re.compile(
    r"^(?:你好|你好呀|您好|您好呀|嗨|哈喽|hello|hi|hey|goodmorning|"
    r"goodafternoon|goodevening)$",
    re.IGNORECASE,
)

logger.info(
    "extension registry loaded: root=%s agents=%d commands=%d skills=%d mcp_servers=%d hooks=%d",
    extension_registry.stock_extension_root,
    len(extension_registry.agents),
    len(extension_registry.commands),
    len(extension_registry.skills),
    len(extension_registry.mcp_servers),
    len(extension_registry.hooks),
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Give every request an id and log its outcome and latency."""
    request_id = request.headers.get("X-Request-ID") or new_request_id()
    client = request.client.host if request.client else "-"
    started = time.perf_counter()
    with request_context(request_id):
        logger.info("--> %s %s from %s", request.method, request.url.path, client)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "<-- %s %s failed after %.1f ms",
                request.method,
                request.url.path,
                (time.perf_counter() - started) * 1000,
            )
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "<-- %s %s %s %.1f ms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        response.headers["X-Request-ID"] = request_id
        return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {"message": "StockAssistant Agent 已启动"}


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., description="用户提问")
    thread_id: str = Field(default_factory=lambda: str(uuid4()))


@app.get("/agent/catalog")
def agent_catalog():
    return {
        "code": 0,
        "message": "success",
        "data": extension_registry.catalog(),
    }


def _is_greeting_only(question: str) -> bool:
    normalized = re.sub(r"[\s，。！？、,.!?;；:：]+", "", question).casefold()
    return bool(_GREETING_ONLY.fullmatch(normalized))


@app.post("/agent/chat")
def agent_chat(req: AgentChatRequest):
    logger.info(
        "chat request thread_id=%s question=%s",
        req.thread_id,
        preview(req.question),
    )
    if _is_greeting_only(req.question):
        answer = "你好！有什么我可以帮你？"
        logger.info("chat greeting handled without component routing thread_id=%s", req.thread_id)
        return {
            "code": 0,
            "message": "success",
            "data": {
                "answer": answer,
                "agent_id": None,
                "command_id": None,
                "skills": [],
                "model": None,
                "thread_id": req.thread_id,
            },
        }

    try:
        routing_model = make_llm(temperature=0)
        selection = select_components(req.question, extension_registry, routing_model)
        logger.info(
            "routing selection agent=%s command=%s skills=%s mcp_servers=%s",
            selection.agent_id,
            selection.command_id,
            selection.skill_ids,
            selection.mcp_server_ids,
        )
        resolved = resolve_component_dependencies(
            req.question,
            selection,
            extension_registry,
            routing_model,
        )
        logger.info(
            "routing resolved commands=%s skills=%s mcp_servers=%s referenced_agents=%s",
            resolved.command_ids,
            resolved.skill_ids,
            resolved.mcp_server_ids,
            resolved.referenced_agent_ids,
        )
    except ValueError as error:
        logger.exception("component routing failed")
        raise HTTPException(status_code=502, detail=f"Component routing failed: {error}") from error

    try:
        agent, command = extension_registry.resolve(selection.agent_id, selection.command_id)
    except (KeyError, ValueError) as error:
        logger.exception(
            "component resolve failed agent=%s command=%s",
            selection.agent_id,
            selection.command_id,
        )
        raise HTTPException(status_code=502, detail=f"Component routing failed: {error}") from error

    config = AgentConfig(
        **extension_registry.agent_runtime_config(
            agent,
            command,
            default_system_prompt(),
            skill_ids=resolved.skill_ids,
            mcp_server_ids=resolved.mcp_server_ids,
            referenced_agent_ids=resolved.referenced_agent_ids,
        )
    )
    logger.info(
        "resolved agent=%s command=%s model=%s skills=%s mcp_servers=%s",
        agent["id"],
        command["id"] if command else None,
        config.model_name,
        config.selected_skill_ids,
        config.mcp_servers,
    )

    additional_commands = [
        extension_registry.commands[command_id]
        for command_id in resolved.command_ids
        if command_id != selection.command_id
    ]
    question = extension_registry.command_input(
        command,
        req.question,
        additional_commands=additional_commands,
    )
    hook_context = {
        "question": req.question,
        "agent_id": agent["id"],
        "command_id": command["id"] if command else None,
        "thread_id": req.thread_id,
    }
    try:
        hook_runner.run(
            "before_agent",
            hook_context,
            agent_id=agent["id"],
            command_id=command["id"] if command else None,
        )
        answer = ask_agent(question, config=config, thread_id=req.thread_id)
        logger.info(
            "chat answer thread_id=%s agent=%s chars=%d answer=%s",
            req.thread_id,
            agent["id"],
            len(answer),
            preview(answer),
        )
        hook_runner.run(
            "after_agent",
            {**hook_context, "answer": answer},
            agent_id=agent["id"],
            command_id=command["id"] if command else None,
        )
    except Exception as error:
        logger.exception(
            "chat failed thread_id=%s agent=%s command=%s",
            req.thread_id,
            agent["id"],
            command["id"] if command else None,
        )
        try:
            hook_runner.run(
                "on_error",
                {**hook_context, "error": str(error)},
                agent_id=agent["id"],
                command_id=command["id"] if command else None,
            )
        except HookExecutionError as hook_error:
            logger.exception("on_error hook failed")
            raise HTTPException(
                status_code=500,
                detail=f"{error}; on_error hook failed: {hook_error}",
            ) from error
        if isinstance(error, HookExecutionError):
            raise HTTPException(status_code=500, detail=str(error)) from error
        raise

    return {
        "code": 0,
        "message": "success",
        "data": {
            "answer": answer,
            "agent_id": agent["id"],
            "command_id": command["id"] if command else None,
            "skills": [
                {"id": skill_id, "name": extension_registry.skills[skill_id]["name"]}
                for skill_id in resolved.skill_ids
            ],
            "model": config.model_name,
            "thread_id": req.thread_id,
        },
    }
