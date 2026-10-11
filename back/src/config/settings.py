import os
from pathlib import Path


BACK_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = BACK_ROOT / "config" / ".env"


def get_settings():
    return {
        "project_root": str(BACK_ROOT),
        "env_path": str(ENV_PATH),
        "model": os.getenv("CHAT_MODEL", "gpt-oss:120b-cloud"),
        "openai_api_key": os.getenv("OPENAI_API_KEY"),
        "openai_api_base": os.getenv("OPENAI_API_BASE"),
    }
