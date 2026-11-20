"""Durable facts about the user, kept across conversations.

A conversation thread is short-lived: it starts, it ends, and the frontend
may drop it.  Some things about the user are not -- which markets they
follow, how much risk they tolerate, the position they hold, how they like
answers written.  Those live here instead, and are injected into the system
prompt of every request.

Preferences are stored in the same SQLite database as the conversation
history.  Two kinds exist:

* ``manual`` -- typed by the user through the settings panel.
* ``auto`` -- extracted from a conversation by the model.

Both can be edited or deleted by the user; the extraction only ever adds.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from src.runtime.logging_config import get_logger
from src.services.db import get_connection

logger = get_logger(__name__)

MAX_PREFERENCES = 60
MAX_CONTENT_CHARS = 200

CATEGORY_INVESTMENT = "investment"
CATEGORY_MARKET = "market"
CATEGORY_STYLE = "style"
CATEGORY_PROFILE = "profile"

VALID_CATEGORIES = (
    CATEGORY_INVESTMENT,
    CATEGORY_MARKET,
    CATEGORY_STYLE,
    CATEGORY_PROFILE,
    "general",
)

SOURCE_MANUAL = "manual"
SOURCE_AUTO = "auto"

# Phrases that suggest the user is stating something durable.  The extractor
# only runs when one of these shows up, so ordinary questions cost nothing.
_PREFERENCE_SIGNALS = re.compile(
    r"(记住|以后都|以后请|下次|从今以后|我(?:最)?(?:喜欢|偏好|习惯|只关注|只看|一般|倾向|"
    r"不(?:喜欢|看|做)|持有|重仓|仓位|风险承受|投资风格|交易风格|资金|账户|持仓)|"
    r"我是(?:一[名个]|个)?(?:.{0,12})?(?:投资者|股民|基金经理|分析师)|"
    r"帮我(?:记住|存一下))",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize(content: str) -> str:
    return re.sub(r"\s+", " ", content).strip()


def has_preference_signal(text: str) -> bool:
    """Whether the message looks like it states a durable preference."""
    if not text:
        return False
    return bool(_PREFERENCE_SIGNALS.search(text))


def list_preferences(include_disabled: bool = True) -> list[dict]:
    """Return every stored preference, newest last."""
    try:
        connection = get_connection()
    except Exception:  # noqa: BLE001
        logger.debug("preference store unavailable", exc_info=True)
        return []

    query = (
        "SELECT id, category, content, source, enabled, created_at, updated_at "
        "FROM user_preferences"
    )
    if not include_disabled:
        query += " WHERE enabled = 1"
    query += " ORDER BY id"

    try:
        rows = connection.execute(query).fetchall()
    except Exception:  # noqa: BLE001
        logger.exception("failed to list preferences")
        return []

    return [
        {
            "id": row[0],
            "category": row[1],
            "content": row[2],
            "source": row[3],
            "enabled": bool(row[4]),
            "created_at": row[5],
            "updated_at": row[6],
        }
        for row in rows
    ]


def enabled_preferences() -> list[dict]:
    """Return the preferences that should be injected into the prompt."""
    return [item for item in list_preferences(include_disabled=False) if item["enabled"]]


def add_preference(content: str, category: str = "general", source: str = SOURCE_MANUAL) -> dict | None:
    """Store a preference; return the stored record, or None when rejected."""
    text = _normalize(content)
    if not text:
        return None
    if len(text) > MAX_CONTENT_CHARS:
        text = text[:MAX_CONTENT_CHARS].rstrip()
    if category not in VALID_CATEGORIES:
        category = "general"

    existing = list_preferences()
    for item in existing:
        if item["content"] == text:
            # Already known -- re-enable it instead of storing a duplicate.
            if not item["enabled"]:
                update_preference(item["id"], enabled=True)
                return next(
                    (i for i in list_preferences() if i["id"] == item["id"]), None
                )
            return item

    if len(existing) >= MAX_PREFERENCES:
        logger.warning("preference limit reached (%d); skipping", MAX_PREFERENCES)
        return None

    try:
        connection = get_connection()
    except Exception:  # noqa: BLE001
        logger.debug("preference store unavailable", exc_info=True)
        return None

    now = _now()
    try:
        with connection:
            cursor = connection.execute(
                "INSERT INTO user_preferences "
                "(category, content, source, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (category, text, source, now, now),
            )
            preference_id = cursor.lastrowid
    except Exception:  # noqa: BLE001
        logger.exception("failed to store preference")
        return None

    logger.info("preference stored id=%s category=%s source=%s", preference_id, category, source)
    return next((i for i in list_preferences() if i["id"] == preference_id), None)


def update_preference(preference_id: int, **fields) -> dict | None:
    """Update a preference's content, category or enabled flag."""
    try:
        connection = get_connection()
    except Exception:  # noqa: BLE001
        return None

    assignments: list[str] = []
    values: list = []

    if "content" in fields and fields["content"] is not None:
        text = _normalize(str(fields["content"]))
        if not text:
            return None
        assignments.append("content = ?")
        values.append(text[:MAX_CONTENT_CHARS])

    if "category" in fields and fields["category"] is not None:
        category = str(fields["category"])
        if category not in VALID_CATEGORIES:
            category = "general"
        assignments.append("category = ?")
        values.append(category)

    if "enabled" in fields and fields["enabled"] is not None:
        assignments.append("enabled = ?")
        values.append(1 if fields["enabled"] else 0)

    if not assignments:
        return next((i for i in list_preferences() if i["id"] == preference_id), None)

    assignments.append("updated_at = ?")
    values.append(_now())
    values.append(preference_id)

    try:
        with connection:
            connection.execute(
                f"UPDATE user_preferences SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
    except Exception:  # noqa: BLE001
        logger.exception("failed to update preference %s", preference_id)
        return None

    return next((i for i in list_preferences() if i["id"] == preference_id), None)


def delete_preference(preference_id: int) -> bool:
    """Remove a preference; return whether a row was deleted."""
    try:
        connection = get_connection()
    except Exception:  # noqa: BLE001
        return False

    try:
        with connection:
            cursor = connection.execute(
                "DELETE FROM user_preferences WHERE id = ?", (preference_id,)
            )
            removed = cursor.rowcount > 0
    except Exception:  # noqa: BLE001
        logger.exception("failed to delete preference %s", preference_id)
        return False

    if removed:
        logger.info("preference deleted id=%s", preference_id)
    return removed


def build_preference_context() -> str:
    """Render the enabled preferences for injection into the system prompt."""
    preferences = enabled_preferences()
    if not preferences:
        return ""

    lines = []
    for item in preferences:
        label = {
            CATEGORY_INVESTMENT: "投资",
            CATEGORY_MARKET: "市场",
            CATEGORY_STYLE: "风格",
            CATEGORY_PROFILE: "个人",
        }.get(item["category"], "其他")
        lines.append(f"- [{label}] {item['content']}")

    if not lines:
        return ""
    return "\n".join(lines)
