import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from threading import RLock
from typing import List, Optional, Sequence

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver

from src.agent.selected_skills_middleware import SelectedSkillsMiddleware
from src.config.settings import get_settings
from src.mcp.client import load_mcp_tools_for_agent
from src.runtime.encoding import install_tolerant_subprocess
from src.runtime.logging_config import get_logger, preview, setup_logging
from src.services.hook_runner import HookToolMiddleware


setup_logging()
logger = get_logger(__name__)
settings = get_settings()
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV_PATH = settings["env_path"]
DEFAULT_SKILLS_DIR = os.path.abspath(os.path.join(PROJECT_ROOT, "..", ".github", "skills"))
_AGENT_CACHE = {}
_AGENT_CACHE_LOCK = RLock()

load_dotenv(ENV_PATH)


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
    chosen_model = (model_name or os.getenv("CHAT_MODEL") or "gpt-oss:120b-cloud").strip()
    return ChatOpenAI(
        model=chosen_model,
        temperature=temperature,
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_api_base=os.getenv("OPENAI_API_BASE"),
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

    backend = LocalShellBackend(
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
    middleware = [HookToolMiddleware(cfg.hooks or {}, project_root)]
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
        checkpointer=MemorySaver(),
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
