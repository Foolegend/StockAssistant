"""Extract durable preferences from a conversation turn.

The extractor only runs when the user's message actually looks like it states
something worth remembering -- a cheap keyword gate keeps ordinary questions
from paying for an extra model call.

The model is asked for a strict JSON payload so that a malformed answer
cannot corrupt the preference store; anything that fails to parse is dropped.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field

from src.runtime.logging_config import get_logger
from src.services.preferences import (
    CATEGORY_INVESTMENT,
    CATEGORY_MARKET,
    CATEGORY_PROFILE,
    CATEGORY_STYLE,
    SOURCE_AUTO,
    add_preference,
    has_preference_signal,
)

logger = get_logger(__name__)

_EXTRACTION_PROMPT = """从下面这轮对话中提取值得长期记住的用户偏好。

只提取明确、稳定、未来仍然成立的信息，例如：
- 关注的市场或板块（只做 A 股、长期关注新能源）
- 投资风格与风险偏好（短线、不追高、可承受较大回撤）
- 持仓与账户约束（持有茅台、成本价区间、资金量级）
- 长期身份（职业、机构角色）
- 对回答方式的长期要求（只要数据结论、不要长篇分析）

不要提取：
- 当天的行情、新闻等时效性信息
- 一次性任务细节或某只股票的单次买卖指令
- 助手说过的话
- 从未在对话中明确表达过的推测

如果这轮对话里没有值得长期记住的内容，返回空的 preferences 列表。

只输出 JSON，不要解释。"""

_CATEGORY_ALIASES = {
    "投资": CATEGORY_INVESTMENT,
    "投资偏好": CATEGORY_INVESTMENT,
    "investment": CATEGORY_INVESTMENT,
    "市场": CATEGORY_MARKET,
    "关注范围": CATEGORY_MARKET,
    "market": CATEGORY_MARKET,
    "风格": CATEGORY_STYLE,
    "表达偏好": CATEGORY_STYLE,
    "style": CATEGORY_STYLE,
    "个人": CATEGORY_PROFILE,
    "profile": CATEGORY_PROFILE,
    "general": "general",
}


class ExtractedPreferences(BaseModel):
    """The shape the extractor model has to answer with."""

    model_config = ConfigDict(extra="forbid")

    preferences: list[str] = Field(default_factory=list)


def _normalize_category(value: str) -> str:
    return _CATEGORY_ALIASES.get((value or "").strip().lower(), "general")


def _parse_json_object(text: str) -> dict | None:
    """Pull the first JSON object out of a model answer."""
    if not text:
        return None
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_preferences(question: str, answer: str, model=None) -> list[dict]:
    """Extract and store preferences from one turn of conversation.

    Returns the preferences that were newly stored.  Any failure -- the
    model erroring, the answer not being valid JSON, the store being
    unavailable -- leaves the conversation untouched.
    """
    if not has_preference_signal(question):
        return []

    if model is None:
        from src.agent.UnifiedAgent import make_llm

        model = make_llm(temperature=0)

    transcript = f"用户：{question}\n助手：{answer}"

    try:
        response = model.invoke(
            [
                {"role": "system", "content": _EXTRACTION_PROMPT},
                {"role": "user", "content": transcript},
            ]
        )
    except Exception:  # noqa: BLE001 - extraction must never break a chat
        logger.warning("preference extraction failed", exc_info=True)
        return []

    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "\n".join(str(item) for item in content)
    parsed = _parse_json_object(str(content))
    if parsed is None:
        logger.info("preference extraction returned no usable JSON")
        return []

    items = parsed.get("preferences")
    if not isinstance(items, list):
        return []

    stored: list[dict] = []
    for item in items:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text:
            continue
        category = _normalize_category(str(parsed.get("category", "general")))
        record = add_preference(text, category=category, source=SOURCE_AUTO)
        if record is not None:
            stored.append(record)

    if stored:
        logger.info("extracted %d preference(s) from a conversation turn", len(stored))
    return stored
