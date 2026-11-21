import re
import time
from contextlib import asynccontextmanager
from queue import Empty, Queue
from threading import Event, Lock, Thread
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from openai import RateLimitError
from pydantic import BaseModel, ConfigDict, Field

from src.agent.UnifiedAgent import (
    AgentConfig,
    ask_agent,
    default_system_prompt,
    delete_thread_memory,
    make_llm,
    stream_agent,
)
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
from src.services import retention
from src.services.db import utc_now
from src.services.thread_history import (
    append_messages,
    build_history_context,
    clear_messages,
    list_threads,
    load_messages,
)
from src.services.preference_extractor import extract_preferences
from src.services.preferences import (
    add_preference,
    build_preference_context,
    delete_preference,
    has_preference_signal,
    list_preferences,
    update_preference,
)

setup_logging()
logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    retention.start_worker()
    try:
        yield
    finally:
        retention.stop_worker()


app = FastAPI(title="StockAssistant Agent", lifespan=lifespan)
extension_registry = ExtensionRegistry()
hook_runner = HookRunner(extension_registry.hooks, str(extension_registry.project_root))
_active_runs: dict[str, Event] = {}
_active_runs_lock = Lock()
_GREETING_ONLY = re.compile(
    r"^(?:你好|你好呀|您好|您好呀|嗨|哈喽|hello|hi|hey|goodmorning|"
    r"goodafternoon|goodevening)$",
    re.IGNORECASE,
)

logger.info(
    "extension registry loaded: root=%s agents=%d skills=%d mcp_servers=%d hooks=%d",
    extension_registry.stock_extension_root,
    len(extension_registry.agents),
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


@app.exception_handler(RateLimitError)
async def handle_rate_limit(request: Request, exc: RateLimitError):
    """Return a clean 429 instead of a 500 stack trace when the LLM is throttled."""
    headers = {}
    response = getattr(exc, "response", None)
    retry_after = response.headers.get("retry-after") if response is not None else None
    if retry_after:
        headers["Retry-After"] = retry_after
    logger.warning(
        "model rate limited path=%s status=%s retry_after=%s",
        request.url.path,
        getattr(exc, "status_code", None),
        retry_after,
    )
    return JSONResponse(
        status_code=429,
        content={
            "code": 429,
            "message": "模型服务触发限流（429 RPM 超限），请稍后重试",
            "data": None,
        },
        headers=headers,
    )


@app.get("/")
def home():
    return {"message": "StockAssistant Agent 已启动"}


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., description="用户提问")
    thread_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str = Field(default_factory=lambda: str(uuid4()))


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


def _extract_preferences(question: str, answer: str) -> None:
    """Pull durable preferences out of a finished turn, in the background.

    Extraction costs one extra model call, so it runs off the request thread:
    the user already has the answer and should not wait for bookkeeping.
    Failures are swallowed on purpose -- remembering something is a bonus and
    must never turn a successful answer into an error.
    """
    if not has_preference_signal(question):
        return

    def run() -> None:
        try:
            extract_preferences(question, answer)
        except Exception:  # noqa: BLE001
            logger.warning("preference extraction failed", exc_info=True)

    Thread(target=run, daemon=True).start()


def _system_prompt_with_preferences() -> str:
    """The base system prompt plus the user's durable preferences."""
    base = default_system_prompt()
    context = build_preference_context()
    if not context:
        return base
    return (
        f"{base}\n\n"
        "## 用户长期偏好\n"
        "以下是用户此前明确表达、长期有效的偏好，请在回答中遵循：\n"
        f"{context}\n\n"
        "这些偏好跨会话有效。若用户本次的明确要求与之冲突，以本次要求为准。"
    )


def _answer_directly(question: str, thread_id: str = "") -> str:
    """Answer a simple conversational message with the model, skipping routing.

    The thread's recent turns are supplied as context so that a conversation
    that mixes direct answers with agent runs still reads as one thread.
    """
    messages: list = [SystemMessage(content=_system_prompt_with_preferences())]
    history = build_history_context(thread_id) if thread_id else ""
    if history:
        messages.append(
            SystemMessage(
                content=f"以下是本次会话此前的对话记录，请据此保持上下文一致：\n{history}"
            )
        )
    messages.append(HumanMessage(content=question))

    response = make_llm().invoke(messages)
    content = response.content
    if isinstance(content, list):
        content = "\n".join(str(item) for item in content)
    answer = str(content).strip()
    return answer or "抱歉，我暂时没有生成回复，请稍后再试。"


def _direct_response(thread_id: str, answer: str) -> dict:
    return {
        "code": 0,
        "message": "success",
        "data": {
            "answer": answer,
            "sources": _extract_sources(answer),
            "agent_id": None,
            "skills": [],
            "model": None,
            "thread_id": thread_id,
        },
    }


def _extract_sources(answer: str) -> list[dict[str, str]]:
    sources = []
    seen_urls = set()
    for label, url in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", answer):
        if url not in seen_urls:
            sources.append({"title": label.strip(), "url": url})
            seen_urls.add(url)
    return sources


def _run_agent_chat(
    req: AgentChatRequest,
    emit=None,
    stop_event: Event | None = None,
    streaming: bool = False,
):
    def report(event: str, payload: dict):
        if emit is not None:
            emit(event, payload)

    def was_stopped() -> bool:
        if stop_event is not None and stop_event.is_set():
            report("cancelled", {"message": "已停止后续分析步骤"})
            return True
        return False

    logger.info(
        "chat request thread_id=%s question=%s",
        req.thread_id,
        preview(req.question),
    )
    report("status", {"stage": "routing", "message": "正在判断问题需要哪些分析能力"})
    selection = None
    resolved = None
    if _is_greeting_only(req.question):
        logger.info(
            "chat greeting bypassed component routing thread_id=%s",
            req.thread_id,
        )
    else:
        try:
            routing_model = make_llm(temperature=0)
            selection = select_components(req.question, extension_registry, routing_model)
            logger.info(
                "routing selection agent=%s skills=%s mcp_servers=%s",
                selection.agent_id,
                selection.skill_ids,
                selection.mcp_server_ids,
            )
            if (
                selection.agent_id
                or selection.skill_ids
                or selection.mcp_server_ids
            ):
                resolved = resolve_component_dependencies(
                    req.question,
                    selection,
                    extension_registry,
                    routing_model,
                )
                logger.info(
                    "routing resolved skills=%s mcp_servers=%s "
                    "referenced_agents=%s",
                    resolved.skill_ids,
                    resolved.mcp_server_ids,
                    resolved.referenced_agent_ids,
                )
        except ValueError as error:
            logger.exception("component routing failed")
            raise HTTPException(
                status_code=502,
                detail=f"Component routing failed: {error}",
            ) from error

    if resolved is None:
        if was_stopped():
            return None
        report("status", {"stage": "analysis", "message": "正在生成回复"})
        if streaming:
            answer_parts = []
            history = build_history_context(req.thread_id)
            stream_messages: list = [
                SystemMessage(content=_system_prompt_with_preferences())
            ]
            if history:
                stream_messages.append(
                    SystemMessage(
                        content=f"以下是本次会话此前的对话记录，请据此保持上下文一致：\n{history}"
                    )
                )
            stream_messages.append(HumanMessage(content=req.question))
            response = make_llm().stream(stream_messages)
            for chunk in response:
                if was_stopped():
                    return None
                content = getattr(chunk, "content", "")
                if isinstance(content, list):
                    answer_parts.extend(str(item) for item in content)
                else:
                    answer_parts.append(str(content))
            answer = "".join(answer_parts).strip()
            answer = answer or "抱歉，我暂时没有生成回复，请稍后再试。"
        else:
            answer = _answer_directly(req.question, req.thread_id)
        append_messages(req.thread_id, "user", req.question)
        append_messages(req.thread_id, "assistant", answer)
        _extract_preferences(req.question, answer)
        logger.info(
            "chat answered directly by model thread_id=%s reason=%s answer=%s",
            req.thread_id,
            "greeting" if selection is None else "no matching components",
            preview(answer),
        )
        return _direct_response(req.thread_id, answer)

    if was_stopped():
        return None

    # Pin only the router's direct skill choices, not their dependencies such
    # as pdf/xlsx.  The selected SKILL.md body is injected before the first
    # model call, so an old thread's workflow cannot silently take over.
    pinned_skill_names = [
        extension_registry.skills[skill_id]["name"]
        for skill_id in (selection.skill_ids or [])
        if skill_id in extension_registry.skills
    ]
    if pinned_skill_names:
        logger.info("routing pinned skills=%s", pinned_skill_names)

    if selection.agent_id is not None:
        agent = extension_registry.agents.get(selection.agent_id)
        if agent is None:
            logger.error(
                "component resolve failed agent=%s",
                selection.agent_id,
            )
            raise HTTPException(
                status_code=502,
                detail=f"Component routing failed: Unknown agent '{selection.agent_id}'",
            )
        agent_id = agent["id"]
        runtime_config = extension_registry.agent_runtime_config(
            agent,
            _system_prompt_with_preferences(),
            skill_ids=resolved.skill_ids,
            pinned_skills=pinned_skill_names,
            mcp_server_ids=resolved.mcp_server_ids,
            referenced_agent_ids=resolved.referenced_agent_ids,
        )
    else:
        agent_id = None
        runtime_config = extension_registry.generic_runtime_config(
            _system_prompt_with_preferences(),
            skill_ids=resolved.skill_ids,
            pinned_skills=pinned_skill_names,
            mcp_server_ids=resolved.mcp_server_ids,
            referenced_agent_ids=resolved.referenced_agent_ids,
        )

    config = AgentConfig(**runtime_config)
    logger.info(
        "resolved agent=%s model=%s skills=%s mcp_servers=%s",
        agent_id,
        config.model_name,
        config.selected_skill_ids,
        config.mcp_servers,
    )

    question = req.question
    hook_context = {
        "question": req.question,
        "agent_id": agent_id,
        "thread_id": req.thread_id,
    }
    report(
        "status",
        {
            "stage": "analysis",
            "message": "正在分析问题"
            if not config.mcp_servers
            else "正在分析问题，需要时会调用数据工具",
            "agent_id": agent_id,
            "skills": [
                {"id": skill_id, "name": extension_registry.skills[skill_id]["name"]}
                for skill_id in resolved.skill_ids
            ],
        },
    )
    try:
        hook_runner.run(
            "before_agent",
            hook_context,
            agent_id=agent_id,
        )
        if was_stopped():
            return None
        if streaming:
            answer, cancelled = stream_agent(
                question,
                config=config,
                thread_id=req.thread_id,
                on_event=emit,
                stop_event=stop_event,
            )
            if cancelled:
                report("cancelled", {"message": "已停止后续分析步骤"})
                return None
            answer = answer or ""
        else:
            answer = ask_agent(question, config=config, thread_id=req.thread_id)
        # Keep the shared history in step with the agent's own checkpoints so
        # that a later direct answer still knows what the thread discussed.
        append_messages(req.thread_id, "user", req.question)
        append_messages(req.thread_id, "assistant", answer)
        _extract_preferences(req.question, answer)
        logger.info(
            "chat answer thread_id=%s agent=%s chars=%d answer=%s",
            req.thread_id,
            agent_id,
            len(answer),
            preview(answer),
        )
        hook_runner.run(
            "after_agent",
            {**hook_context, "answer": answer},
            agent_id=agent_id,
        )
    except Exception as error:
        if isinstance(error, RateLimitError):
            logger.warning(
                "chat rate limited thread_id=%s agent=%s",
                req.thread_id,
                agent_id,
            )
        else:
            logger.exception(
                "chat failed thread_id=%s agent=%s",
                req.thread_id,
                agent_id,
            )
        try:
            hook_runner.run(
                "on_error",
                {**hook_context, "error": str(error)},
                agent_id=agent_id,
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
            "sources": _extract_sources(answer),
            "agent_id": agent_id,
            "skills": [
                {"id": skill_id, "name": extension_registry.skills[skill_id]["name"]}
                for skill_id in resolved.skill_ids
            ],
            "model": config.model_name,
            "thread_id": req.thread_id,
        },
    }


@app.post("/agent/chat")
def agent_chat(req: AgentChatRequest):
    return _run_agent_chat(req)


def _encode_sse(event: str, payload: dict) -> str:
    import json

    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/agent/chat/stream")
def agent_chat_stream(req: AgentChatRequest):
    stop_event = Event()
    with _active_runs_lock:
        if req.run_id in _active_runs:
            raise HTTPException(status_code=409, detail="该分析任务已在运行")
        _active_runs[req.run_id] = stop_event

    def event_stream():
        events: Queue = Queue()
        finished = object()

        def emit(event: str, payload: dict):
            events.put((event, payload))

        def run():
            try:
                result = _run_agent_chat(
                    req,
                    emit=emit,
                    stop_event=stop_event,
                    streaming=True,
                )
                if result is not None:
                    emit("complete", result["data"])
            except HTTPException as error:
                emit("error", {"message": str(error.detail), "status": error.status_code})
            except Exception as error:
                logger.exception("streaming chat failed run_id=%s", req.run_id)
                emit("error", {"message": str(error), "status": 500})
            finally:
                events.put((finished, None))

        worker = Thread(target=run, daemon=True)
        worker.start()
        try:
            while True:
                try:
                    event, payload = events.get(timeout=0.25)
                except Empty:
                    continue
                if event is finished:
                    break
                yield _encode_sse(event, payload)
        finally:
            if worker.is_alive():
                stop_event.set()
            with _active_runs_lock:
                if _active_runs.get(req.run_id) is stop_event:
                    del _active_runs[req.run_id]

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/agent/chat/runs/{run_id}/cancel")
def cancel_agent_run(run_id: str):
    with _active_runs_lock:
        stop_event = _active_runs.get(run_id)
    if stop_event is None:
        raise HTTPException(status_code=404, detail="分析任务不存在或已结束")
    stop_event.set()
    return {"code": 0, "message": "已请求停止当前分析步骤", "data": {"run_id": run_id}}


@app.get("/agent/threads")
def list_agent_threads(limit: int = 50, offset: int = 0):
    """List stored conversations, most recently updated first."""
    safe_limit = max(1, min(limit, 200))
    safe_offset = max(0, offset)
    return {
        "code": 0,
        "message": "success",
        "data": {"threads": list_threads(safe_limit, safe_offset)},
    }


@app.get("/agent/threads/{thread_id}/messages")
def get_agent_thread_messages(thread_id: str):
    """Return a stored conversation's messages."""
    if not thread_id.strip():
        raise HTTPException(status_code=400, detail="会话 ID 不能为空")

    messages = load_messages(thread_id, limit=1000)
    if not messages:
        raise HTTPException(status_code=404, detail="该会话没有可读取的记录")

    for message in messages:
        if message["role"] == "assistant":
            message["sources"] = _extract_sources(message["content"])

    return {
        "code": 0,
        "message": "success",
        "data": {"thread_id": thread_id, "messages": messages},
    }


@app.get("/agent/threads/export")
def export_all_threads(format: str = "markdown", limit: int = 500):
    """Export every stored conversation as one document."""
    if format not in {"markdown", "json"}:
        raise HTTPException(status_code=400, detail="不支持的导出格式")

    safe_limit = max(1, min(limit, 1000))
    threads = list_threads(limit=safe_limit, offset=0)
    if not threads:
        raise HTTPException(status_code=404, detail="没有可导出的会话记录")

    exported = []
    for thread in threads:
        messages = load_messages(thread["thread_id"], limit=1000)
        for message in messages:
            if message["role"] == "assistant":
                message["sources"] = _extract_sources(message["content"])
        exported.append({**thread, "messages": messages})

    if format == "json":
        return JSONResponse(
            content={"exported_at": utc_now(), "threads": exported},
            headers={"Content-Disposition": 'attachment; filename="conversations.json"'},
        )

    lines: list[str] = [
        "# 全部会话导出",
        "",
        f"- 导出时间：{utc_now()}",
        f"- 会话数：{len(exported)}",
        "",
    ]
    for item in exported:
        lines.extend(
            [
                f"## {item['title']}",
                "",
                f"- 会话 ID：{item['thread_id']}",
                f"- 最后更新：{item['updated_at']}",
                f"- 消息数：{item['message_count']}",
                "",
            ]
        )
        for message in item["messages"]:
            lines.append("### 我" if message["role"] == "user" else "### 研究助手")
            lines.append("")
            lines.append(message["content"] or "（无内容）")
            sources = message.get("sources") or []
            if sources:
                lines.append("")
                lines.append("参考来源：")
                for source in sources:
                    lines.append(f"- [{source['title']}]({source['url']})")
            lines.append("")
        lines.extend(["---", ""])

    return Response(
        content="\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="conversations.md"'},
    )


@app.delete("/agent/threads/{thread_id}")
def delete_agent_thread(thread_id: str):
    """Erase the server-side memory of a conversation thread."""
    if not thread_id.strip():
        raise HTTPException(status_code=400, detail="会话 ID 不能为空")
    summary = delete_thread_memory(thread_id)
    summary["cleared_messages"] = clear_messages(thread_id)
    return {
        "code": 0,
        "message": "success",
        "data": summary,
    }


class PreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., description="偏好内容")
    category: str = Field(default="general", description="偏好分类")


class PreferenceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(default=None, description="偏好内容")
    category: str | None = Field(default=None, description="偏好分类")
    enabled: bool | None = Field(default=None, description="是否启用")


@app.get("/agent/preferences")
def agent_preferences():
    """List the user's durable preferences."""
    return {
        "code": 0,
        "message": "success",
        "data": {"preferences": list_preferences()},
    }


@app.post("/agent/preferences")
def create_agent_preference(req: PreferenceRequest):
    """Store a preference the user typed in."""
    record = add_preference(req.content, category=req.category, source="manual")
    if record is None:
        raise HTTPException(status_code=400, detail="偏好内容不能为空")
    return {
        "code": 0,
        "message": "success",
        "data": {"preference": record},
    }


@app.patch("/agent/preferences/{preference_id}")
def update_agent_preference(preference_id: int, req: PreferenceUpdateRequest):
    """Edit or enable/disable a preference."""
    record = update_preference(
        preference_id,
        content=req.content,
        category=req.category,
        enabled=req.enabled,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="偏好不存在")
    return {
        "code": 0,
        "message": "success",
        "data": {"preference": record},
    }


@app.delete("/agent/preferences/{preference_id}")
def delete_agent_preference(preference_id: int):
    """Remove a preference."""
    if not delete_preference(preference_id):
        raise HTTPException(status_code=404, detail="偏好不存在")
    return {
        "code": 0,
        "message": "success",
        "data": {"id": preference_id},
    }


@app.get("/agent/threads/{thread_id}/export")
def export_agent_thread(thread_id: str, format: str = "markdown"):
    """Return a conversation as Markdown or JSON."""
    if not thread_id.strip():
        raise HTTPException(status_code=400, detail="会话 ID 不能为空")

    messages = load_messages(thread_id, limit=1000)
    if not messages:
        raise HTTPException(status_code=404, detail="该会话没有可导出的记录")

    if format == "json":
        return JSONResponse(
            content={"thread_id": thread_id, "messages": messages},
            headers={"Content-Disposition": f'attachment; filename="{thread_id}.json"'},
        )

    if format != "markdown":
        raise HTTPException(status_code=400, detail="不支持的导出格式")

    lines = [f"# 会话记录 {thread_id}", ""]
    for message in messages:
        speaker = "我" if message["role"] == "user" else "研究助手"
        lines.append(f"## {speaker}")
        lines.append("")
        lines.append(message["content"])
        lines.append("")

    body = "\n".join(lines)
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{thread_id}.md"'},
    )
