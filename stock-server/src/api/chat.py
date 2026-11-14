from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from src.agent.UnifiedAgent import AgentConfig, ask_agent, default_system_prompt, make_llm
from src.services.component_dependencies import resolve_component_dependencies
from src.services.component_router import select_components
from src.services.extension_registry import ExtensionRegistry
from src.services.hook_runner import HookExecutionError, HookRunner

app = FastAPI(title="StockAssistant Agent")
extension_registry = ExtensionRegistry()
hook_runner = HookRunner(extension_registry.hooks, str(extension_registry.project_root))

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


@app.post("/agent/chat")
def agent_chat(req: AgentChatRequest):
    try:
        routing_model = make_llm(temperature=0)
        selection = select_components(req.question, extension_registry, routing_model)
        resolved = resolve_component_dependencies(
            req.question,
            selection,
            extension_registry,
            routing_model,
        )
    except ValueError as error:
        raise HTTPException(status_code=502, detail=f"Component routing failed: {error}") from error

    try:
        agent, command = extension_registry.resolve(selection.agent_id, selection.command_id)
    except (KeyError, ValueError) as error:
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
        hook_runner.run(
            "after_agent",
            {**hook_context, "answer": answer},
            agent_id=agent["id"],
            command_id=command["id"] if command else None,
        )
    except Exception as error:
        try:
            hook_runner.run(
                "on_error",
                {**hook_context, "error": str(error)},
                agent_id=agent["id"],
                command_id=command["id"] if command else None,
            )
        except HookExecutionError as hook_error:
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
