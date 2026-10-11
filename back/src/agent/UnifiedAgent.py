import os
import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver

from src.config.settings import get_settings
from src.mcp.client import load_mcp_tools_for_agent


settings = get_settings()
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV_PATH = settings["env_path"]
DEFAULT_SKILLS_DIR = os.path.join(PROJECT_ROOT, "config", "skills")

load_dotenv(ENV_PATH)


def _normalize_path(path: Optional[str]) -> Optional[str]:
    if path is None:
        return None
    value = os.path.expanduser(str(path)).strip()
    if not value:
        return None
    return os.path.normpath(value)


@dataclass
class AgentConfig:
    model_name: Optional[str] = None
    skill_dirs: Optional[List[str]] = None
    mcp_servers: Optional[List[str]] = None
    system_prompt: Optional[str] = None
    temperature: float = 0.2
    debug: bool = False

    def __post_init__(self):
        self.skill_dirs = self._normalize_list(self.skill_dirs)
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


def load_mcp_tools_from_servers(mcp_servers: Sequence[str]) -> list:
    return load_mcp_tools_for_agent(mcp_servers)


def ensure_skill_dirs(skill_dirs: Optional[Sequence[str]] = None) -> list[str]:
    configured = list(skill_dirs or [])
    if not configured and os.path.isdir(DEFAULT_SKILLS_DIR):
        configured = [DEFAULT_SKILLS_DIR]

    normalized = []
    for skill_dir in configured:
        candidate = _normalize_path(skill_dir)
        if candidate and os.path.isdir(candidate):
            normalized.append(candidate)

    return normalized


def build_agent(config: Optional[AgentConfig] = None, **overrides):
    cfg = config or AgentConfig()
    for key, value in overrides.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)

    model_name = cfg.model_name or os.getenv("CHAT_MODEL") or "gpt-oss:120b-cloud"
    llm = make_llm(model_name=model_name, temperature=cfg.temperature)

    skill_dirs = ensure_skill_dirs(cfg.skill_dirs)
    mcp_tools = load_mcp_tools_from_servers(cfg.mcp_servers)

    backend = LocalShellBackend(
        root_dir=PROJECT_ROOT,
        env={
            **os.environ.copy(),
            "PYTHONHASHSEED": "0",
            "SKILL_ROOT": os.path.join(PROJECT_ROOT, "config", "skills"),
            "PYTHON_EXEC": sys.executable,
        },
        virtual_mode=True,
        timeout=120,
        max_output_bytes=100000,
    )

    agent = create_deep_agent(
        model=llm,
        tools=mcp_tools,
        skills=skill_dirs or None,
        backend=backend,
        system_prompt=cfg.system_prompt or default_system_prompt(),
        checkpointer=MemorySaver(),
        debug=cfg.debug,
    )
    return agent


def ask_agent(question: str, config: Optional[AgentConfig] = None, thread_id: str = "default") -> str:
    agent = build_agent(config)
    result = agent.invoke(
        {"messages": [{"role": "user", "content": question}]},
        config={"configurable": {"thread_id": thread_id}},
    )

    messages = result.get("messages", []) if isinstance(result, dict) else []
    if not messages:
        return ""

    last_message = messages[-1]
    if hasattr(last_message, "content"):
        content = last_message.content
        if isinstance(content, list):
            return "\n".join(str(item) for item in content)
        return str(content)
    return str(last_message)
