import hashlib
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from threading import Event
from typing import Callable, List, Optional, Sequence

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from dotenv import dotenv_values, load_dotenv
from langchain_openai import ChatOpenAI
from openai import RateLimitError

from src.agent.selected_skills_middleware import SelectedSkillsMiddleware
from src.config.settings import get_settings
from src.services.checkpoint_store import get_checkpointer
from src.mcp.client import load_mcp_tools_for_agent
from src.runtime.encoding import install_tolerant_subprocess
from src.runtime.logging_config import get_logger, preview, setup_logging
from src.services.hook_runner import HookToolMiddleware


setup_logging()
logger = get_logger(__name__)
settings = get_settings()
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN_ROOT = os.path.abspath(os.path.join(PROJECT_ROOT, os.pardir))
ENV_PATH = settings["env_path"]
DEFAULT_SKILLS_DIR = os.path.abspath(os.path.join(PROJECT_ROOT, "..", ".github", "skills"))
_AGENT_CACHE = {}
_AGENT_CACHE_LOCK = RLock()

load_dotenv(ENV_PATH)


def _ensure_env_loaded() -> None:
    """Fill missing credentials from ``.env`` right before the model is built.

    ``load_dotenv`` runs once at import time and, by default, does not override
    variables that already exist in ``os.environ`` -- including an exported
    *empty* ``OPENAI_API_KEY``, which the OpenAI SDK treats as missing
    credentials.  Re-reading the file here means a transient startup state (or
    an empty exported variable) no longer breaks every model call until the
    process is restarted.
    """
    if (os.getenv("OPENAI_API_KEY") or "").strip():
        return
    for key, value in dotenv_values(ENV_PATH).items():
        if value and not (os.getenv(key) or "").strip():
            os.environ[key] = value
    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        logger.error(
            "OPENAI_API_KEY 未配置，请在 %s 中设置 OPENAI_API_KEY 后重启服务", ENV_PATH
        )


def _normalize_path(path: Optional[str]) -> Optional[str]:
    if path is None:
        return None
    value = os.path.expanduser(str(path)).strip()
    if not value:
        return None
    return os.path.normpath(value)


def _to_backend_path(path: str, root: str) -> str:
    """Convert a host path under ``root`` to the backend's virtual path.

    ``LocalShellBackend(virtual_mode=True)`` resolves every file operation
    relative to ``root``, so ``/repo/stock-server/.stock/skills`` must be passed
    as ``/stock-server/.stock/skills``.  Without this, ``SkillsMiddleware``
    looks for ``<root>/<absolute path>`` and reports ``path_not_found``.

    Paths outside ``root`` cannot be represented; they are returned unchanged.
    """
    try:
        relative = os.path.relpath(path, root)
    except ValueError:  # different drive on Windows
        return path
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return path
    return "/" + relative.replace(os.sep, "/")


_WINDOWS_ABS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_POSIX_DRIVE_PATH = re.compile(r"^/([A-Za-z])/")


def _to_virtual_tool_path(path: str, root: str) -> str:
    """Map host-absolute paths back to the backend's virtual path form.

    File tools run with ``virtual_mode=True`` where ``/`` means ``root``.  A
    model that copies a host path (``D:\\repo\\.tmp\\x`` or the Git-Bash form
    ``/d/repo/.tmp/x``) would otherwise create ``<root>/d/repo/...`` while the
    shell resolves the same command line against the real host path.  Translate
    paths that fall inside ``root``; leave anything else untouched so the
    backend still rejects it.
    """
    if not isinstance(path, str) or not path:
        return path
    # Models habitually use the Unix temp dir; keep that intent but store the
    # files under the project's ignored ``.tmp`` directory.
    if path == "/tmp" or path.startswith("/tmp/"):
        path = "/.tmp" + path[len("/tmp"):]
    candidate = path
    drive_match = _POSIX_DRIVE_PATH.match(candidate)
    if drive_match:
        candidate = drive_match.group(1) + ":\\" + candidate[3:].replace("/", "\\")
    if not _WINDOWS_ABS_PATH.match(candidate):
        return path
    try:
        relative = os.path.relpath(candidate, root)
    except ValueError:  # different drive on Windows
        return path
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return path
    return "/" + relative.replace(os.sep, "/")


class _ProjectRootShellBackend(LocalShellBackend):
    """LocalShellBackend that tolerates host-absolute paths from the model."""

    def _resolve_path(self, key: str):
        normalized = _to_virtual_tool_path(key, str(self.cwd))
        try:
            return super()._resolve_path(normalized)
        except ValueError as error:
            # On Windows, Path.resolve() can keep the ``\\?\`` extended prefix
            # when the directory tree changes while it resolves (for example
            # two parallel write_file calls creating the same directory).  The
            # prefix makes an in-root path fail the textual containment check,
            # so retry once with the prefix removed before giving up.
            vpath = normalized if normalized.startswith("/") else "/" + normalized
            if ".." in vpath or vpath.startswith("~"):
                raise
            full = (self.cwd / vpath.lstrip("/")).resolve()
            text = str(full)
            if text.startswith("\\\\?\\"):
                text = text[4:]
            full = Path(text)
            try:
                full.relative_to(self.cwd)
            except ValueError:
                raise error from None
            return full


@dataclass
class AgentConfig:
    model_name: Optional[str] = None
    skill_dirs: Optional[List[str]] = None
    selected_skill_ids: Optional[List[str]] = None
    selected_skill_directories: Optional[dict[str, List[str]]] = None
    mcp_servers: Optional[List[dict | str]] = None
    hooks: Optional[dict[str, dict]] = None
    system_prompt: Optional[str] = None
    project_root: Optional[str] = None
    subagents: Optional[List[dict]] = None
    temperature: float = 0.2
    debug: bool = False

    def __post_init__(self):
        if self.skill_dirs is not None:
            self.skill_dirs = self._normalize_list(self.skill_dirs)
        if self.selected_skill_ids is not None:
            self.selected_skill_ids = self._normalize_list(self.selected_skill_ids)
        if self.mcp_servers is not None:
            self.mcp_servers = self._normalize_list(self.mcp_servers)

    @staticmethod
    def _normalize_list(value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return [item for item in value if item]


def default_system_prompt() -> str:
    return """
## 角色设定
你是一位专业、高效、多领域的超级智能助手，具备强大的知识整合与问题解决能力。你善于理解用户意图，提供准确、清晰、有温度的回答。

## 核心任务
- 根据用户提问，结合可用的 tools、skills 和知识库提供高质量解答。
- 优先遵循：准确性 > 实用性 > 简洁性 > 友好性。
- 遇到不明确的问题时，主动澄清；遇到复杂问题时，分步骤拆解。

## 交互风格
- 默认使用用户提问的语言。
- 复杂内容采用结构化排版，必要时用列表、代码块、表格展示。
- 保持专业但不刻板，具备耐心与反馈意识。
"""


def make_llm(model_name: Optional[str] = None, temperature: float = 0.2):
    _ensure_env_loaded()
    chosen_model = (model_name or os.getenv("CHAT_MODEL") or "gpt-oss:120b-cloud").strip()
    return ChatOpenAI(
        model=chosen_model,
        temperature=temperature,
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_api_base=os.getenv("OPENAI_API_BASE"),
        # Providers such as MiniMax answer 429 when the per-minute quota is
        # exhausted.  The SDK retries 429/5xx automatically; raise the default
        # (2) so short RPM spikes are absorbed instead of failing the request.
        max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "5")),
        request_timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "120")),
    )


def load_mcp_tools_from_servers(mcp_servers: Sequence[dict | str]) -> list:
    return load_mcp_tools_for_agent(mcp_servers)


def ensure_skill_dirs(skill_dirs: Optional[Sequence[str]] = None) -> list[str]:
    configured = list(skill_dirs) if skill_dirs is not None else []
    if skill_dirs is None and os.path.isdir(DEFAULT_SKILLS_DIR):
        configured = [DEFAULT_SKILLS_DIR]

    normalized = []
    for skill_dir in configured:
        candidate = _normalize_path(skill_dir)
        if candidate is None:
            raise ValueError("Skill directories must be non-empty paths")
        if not os.path.isdir(candidate):
            raise FileNotFoundError(f"Skill directory does not exist: {candidate}")
        normalized.append(os.path.abspath(candidate))

    return normalized


def build_agent(config: Optional[AgentConfig] = None, **overrides):
    # The agent backends call subprocess.run(text=True), which decodes with the
    # OS code page (GBK on zh-CN Windows) and raises UnicodeDecodeError on
    # UTF-8 child output.  Make those calls decode tolerantly.
    install_tolerant_subprocess()

    cfg = config or AgentConfig()
    for key, value in overrides.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)

    started = time.perf_counter()
    model_name = cfg.model_name or os.getenv("CHAT_MODEL") or "gpt-oss:120b-cloud"
    llm = make_llm(model_name=model_name, temperature=cfg.temperature)

    skill_dirs = ensure_skill_dirs(cfg.skill_dirs)
    project_root = _normalize_path(cfg.project_root) or PROJECT_ROOT
    # Create the shared scratch directory up front so concurrent tool calls
    # never race to create it while the backend resolves paths inside it.
    os.makedirs(os.path.join(project_root, ".tmp"), exist_ok=True)
    # SkillsMiddleware 通过 backend 读文件；virtual_mode 下路径相对 project_root，
    # 必须把宿主机绝对路径转成后端虚拟路径，否则技能目录会报 path_not_found。
    skill_dirs = [_to_backend_path(path, project_root) for path in skill_dirs]
    mcp_tools = load_mcp_tools_from_servers(cfg.mcp_servers)
    logger.info(
        "building agent model=%s temperature=%s skill_dirs=%s selected_skills=%s "
        "mcp_servers=%s mcp_tools=%s hooks=%d subagents=%d project_root=%s",
        model_name,
        cfg.temperature,
        skill_dirs,
        cfg.selected_skill_ids,
        cfg.mcp_servers,
        [tool.name for tool in mcp_tools],
        len(cfg.hooks or {}),
        len(cfg.subagents or []),
        project_root,
    )

    backend = _ProjectRootShellBackend(
        root_dir=project_root,
        env={
            **os.environ.copy(),
            "PYTHONHASHSEED": "0",
            "SKILL_ROOT": DEFAULT_SKILLS_DIR,
            "CLAUDE_PROJECT_DIR": project_root,
            "PYTHON_EXEC": sys.executable,
            # Child processes must emit UTF-8 so the parent can decode them
            # consistently regardless of the Windows console code page.
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        },
        virtual_mode=True,
        timeout=120,
        max_output_bytes=100000,
    )
    middleware = [
        HookToolMiddleware(
            cfg.hooks or {},
            project_root,
            plugin_root=PLUGIN_ROOT,
        )
    ]
    if cfg.selected_skill_ids is not None:
        directories_by_source = cfg.selected_skill_directories
        if directories_by_source is not None:
            directories_by_source = {
                _to_backend_path(source, project_root): directories
                for source, directories in directories_by_source.items()
            }
        middleware.insert(
            0,
            SelectedSkillsMiddleware(
                backend,
                skill_dirs,
                cfg.selected_skill_ids,
                directories_by_source,
            ),
        )
        skill_dirs = None

    subagents = [
        {**spec, "skills": [_to_backend_path(path, project_root) for path in spec["skills"]]}
        if spec.get("skills")
        else spec
        for spec in (cfg.subagents or [])
    ]

    agent = create_deep_agent(
        model=llm,
        tools=mcp_tools,
        middleware=middleware,
        skills=skill_dirs,
        subagents=subagents or None,
        backend=backend,
        system_prompt=cfg.system_prompt or default_system_prompt(),
        checkpointer=get_checkpointer(),
        debug=cfg.debug,
    )
    logger.info("agent built in %.1f ms", (time.perf_counter() - started) * 1000)
    return agent


def _cached_agent(config: Optional[AgentConfig]):
    cfg = config or AgentConfig()
    cache_data = {
        "model_name": cfg.model_name,
        "skill_dirs": cfg.skill_dirs,
        "selected_skill_ids": cfg.selected_skill_ids,
        "selected_skill_directories": cfg.selected_skill_directories,
        "mcp_servers": cfg.mcp_servers,
        "hooks": cfg.hooks,
        "system_prompt": cfg.system_prompt,
        "project_root": cfg.project_root,
        "subagents": cfg.subagents,
        "temperature": cfg.temperature,
        "debug": cfg.debug,
    }
    fingerprint = hashlib.sha256(
        json.dumps(cache_data, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    with _AGENT_CACHE_LOCK:
        if fingerprint not in _AGENT_CACHE:
            logger.info("agent cache miss fingerprint=%s", fingerprint[:12])
            _AGENT_CACHE[fingerprint] = build_agent(cfg)
        else:
            logger.debug("agent cache hit fingerprint=%s", fingerprint[:12])
        return _AGENT_CACHE[fingerprint]


def _cached_agents() -> list:
    """Return every cached agent instance."""
    with _AGENT_CACHE_LOCK:
        return list(_AGENT_CACHE.values())


def ask_agent(question: str, config: Optional[AgentConfig] = None, thread_id: str = "default") -> str:
    agent = _cached_agent(config)
    logger.info(
        "agent invoke thread_id=%s question=%s",
        thread_id,
        preview(question),
    )
    started = time.perf_counter()
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config={"configurable": {"thread_id": thread_id}},
        )
    except RateLimitError as error:
        logger.warning(
            "agent invoke rate limited thread_id=%s after %.1f ms status=%s",
            thread_id,
            (time.perf_counter() - started) * 1000,
            getattr(error, "status_code", None),
        )
        raise
    except Exception:
        logger.exception(
            "agent invoke failed thread_id=%s after %.1f ms",
            thread_id,
            (time.perf_counter() - started) * 1000,
        )
        raise

    messages = result.get("messages", []) if isinstance(result, dict) else []
    logger.info(
        "agent finished thread_id=%s messages=%d duration=%.1f ms",
        thread_id,
        len(messages),
        (time.perf_counter() - started) * 1000,
    )
    if not messages:
        return ""

    last_message = messages[-1]
    if hasattr(last_message, "content"):
        content = last_message.content
        if isinstance(content, list):
            return "\n".join(str(item) for item in content)
        return str(content)
    return str(last_message)


def stream_agent(
    question: str,
    config: Optional[AgentConfig] = None,
    thread_id: str = "default",
    on_event: Optional[Callable[[str, dict], None]] = None,
    stop_event: Optional[Event] = None,
) -> tuple[Optional[str], bool]:
    """Run an agent while reporting real tool activity between graph updates."""
    agent = _cached_agent(config)
    logger.info(
        "agent stream thread_id=%s question=%s",
        thread_id,
        preview(question),
    )
    final_answer = ""
    tool_names = {}
    started = time.perf_counter()

    for update in agent.stream(
        {"messages": [{"role": "user", "content": question}]},
        config={"configurable": {"thread_id": thread_id}},
        stream_mode="updates",
    ):
        if stop_event is not None and stop_event.is_set():
            return None, True
        if not isinstance(update, dict):
            continue

        for node_update in update.values():
            if not isinstance(node_update, dict):
                continue
            messages = node_update.get("messages", [])
            if not isinstance(messages, list):
                messages = [messages]
            for message in messages:
                tool_calls = getattr(message, "tool_calls", []) or []
                for tool_call in tool_calls:
                    tool_id = tool_call.get("id")
                    tool_name = tool_call.get("name", "工具")
                    if tool_id:
                        tool_names[tool_id] = tool_name
                    if on_event is not None:
                        on_event(
                            "tool_start",
                            {"tool_id": tool_id, "tool": tool_name},
                        )

                if getattr(message, "type", None) == "tool":
                    tool_id = getattr(message, "tool_call_id", None)
                    if on_event is not None:
                        on_event(
                            "tool_end",
                            {
                                "tool_id": tool_id,
                                "tool": tool_names.get(
                                    tool_id,
                                    getattr(message, "name", "工具"),
                                ),
                            },
                        )
                elif not tool_calls and hasattr(message, "content"):
                    content = message.content
                    if isinstance(content, list):
                        final_answer = "\n".join(str(item) for item in content)
                    else:
                        final_answer = str(content)

    logger.info(
        "agent stream finished thread_id=%s chars=%d duration=%.1f ms",
        thread_id,
        len(final_answer),
        (time.perf_counter() - started) * 1000,
    )
    return final_answer.strip(), False


def _agent_project_root(agent) -> Path:
    """Best-effort project root of a cached agent.

    The offload middlewares write their archives relative to the backend's
    virtual root, which is the agent's project root.  Fall back to the
    process-wide root when it cannot be determined.
    """
    backend = getattr(agent, "backend", None)
    cwd = getattr(backend, "cwd", None)
    if cwd:
        return Path(str(cwd))
    return Path(PROJECT_ROOT)


def delete_thread_memory(thread_id: str) -> dict:
    """Erase every trace of a conversation thread from the server.

    All agents share one checkpointer, so deleting the thread there clears it
    everywhere.  The agent cache is swept as well so that the archive files
    the middlewares offloaded are removed too -- their paths live in the
    checkpoints, so they have to be read before the thread is dropped.

    Returns a summary of what was deleted.
    """
    from src.services.thread_cleanup import (
        collect_thread_archive_paths,
        delete_archive_files,
        delete_thread_memory as delete_saver_thread,
    )

    archive_paths: list[str] = []
    project_roots: list[Path] = []

    for agent in _cached_agents():
        project_roots.append(_agent_project_root(agent))

    # The shared saver is the one that actually stores the checkpoints.  It
    # must be swept even when no agent is cached, otherwise a thread that was
    # only ever answered directly would keep its history.  The archive paths
    # are read from it before the thread is dropped.
    shared = get_checkpointer()
    archive_paths.extend(collect_thread_archive_paths(shared, thread_id))
    cleared = 1 if delete_saver_thread(shared, thread_id) else 0

    removed_files: list[str] = []
    for project_root in project_roots or [Path(PROJECT_ROOT)]:
        removed_files.extend(
            delete_archive_files(dict.fromkeys(archive_paths), project_root)
        )

    logger.info(
        "thread memory deleted thread_id=%s agents=%d files=%d",
        thread_id,
        cleared,
        len(removed_files),
    )
    return {
        "thread_id": thread_id,
        "cleared_agents": cleared,
        "deleted_files": len(removed_files),
    }
