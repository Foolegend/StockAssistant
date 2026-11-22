"""Read and write the plain message history of a conversation thread.

The agent path stores its history in LangGraph checkpoints, which only the
agent graph knows how to interpret.  The direct-answer path -- greetings and
questions no registered component matches -- never enters the graph, so it
needs a simpler store to keep multi-turn context.

Both paths share one database, so a conversation keeps a single history even
when the router sends some turns to an agent and answers others directly.
"""

from __future__ import annotations

import json
import sqlite3

from src.runtime.logging_config import get_logger
from src.services.db import get_connection, utc_now

logger = get_logger(__name__)

_MAX_HISTORY_MESSAGES = 40
_MAX_HISTORY_CHARS = 12000
_TITLE_CHARS = 42


def _connect() -> sqlite3.Connection:
    """Return the shared connection to the application tables."""
    return get_connection()


def close_history_connection() -> None:
    """Kept for symmetry with the other stores; the connection is shared."""
    from src.services.db import close_connection

    close_connection()


def append_messages(
    thread_id: str,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> None:
    """Append one message to a thread's history."""
    if not thread_id or not content.strip():
        return
    try:
        connection = _connect()
    except Exception:  # noqa: BLE001 - history is a nicety, not a requirement
        logger.debug("thread history unavailable; skipping append", exc_info=True)
        return

    try:
        serialized = json.dumps(metadata or {}, ensure_ascii=False)
        with connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 FROM thread_messages WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            connection.execute(
                "INSERT INTO thread_messages "
                "(thread_id, seq, role, content, created_at, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (thread_id, row[0], role, content, utc_now(), serialized),
            )
    except Exception:  # noqa: BLE001
        logger.exception("failed to append thread history for %s", thread_id)


def load_messages(thread_id: str, limit: int = _MAX_HISTORY_MESSAGES) -> list[dict]:
    """Return the thread's recent messages, oldest first."""
    if not thread_id:
        return []
    try:
        connection = _connect()
    except Exception:  # noqa: BLE001
        return []

    try:
        rows = connection.execute(
            "SELECT role, content, created_at, metadata FROM thread_messages "
            "WHERE thread_id = ? ORDER BY seq DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.exception("failed to load thread history for %s", thread_id)
        return []

    messages = []
    for role, content, created_at, metadata in reversed(rows):
        try:
            parsed = json.loads(metadata) if metadata else {}
        except (TypeError, ValueError):
            parsed = {}
        messages.append(
            {
                "role": role,
                "content": content,
                "created_at": created_at,
                "metadata": parsed if isinstance(parsed, dict) else {},
            }
        )
    return messages


def list_threads(limit: int = 50, offset: int = 0) -> list[dict]:
    """Return conversation summaries, most recently updated first."""
    try:
        connection = _connect()
    except Exception:  # noqa: BLE001
        return []

    try:
        rows = connection.execute(
            """
            SELECT m.thread_id,
                   COUNT(*) AS message_count,
                   MIN(m.created_at) AS created_at,
                   MAX(m.created_at) AS updated_at,
                   (SELECT t2.content
                      FROM thread_messages t2
                     WHERE t2.thread_id = m.thread_id AND t2.role = 'user'
                     ORDER BY t2.seq
                     LIMIT 1) AS title
              FROM thread_messages m
             GROUP BY m.thread_id
             ORDER BY updated_at DESC
             LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.exception("failed to list threads")
        return []

    threads = []
    for thread_id, message_count, created_at, updated_at, title in rows:
        text = (title or "").strip().replace("\n", " ")
        if len(text) > _TITLE_CHARS:
            text = text[: _TITLE_CHARS - 1] + "…"
        threads.append(
            {
                "thread_id": thread_id,
                "title": text or "未命名会话",
                "message_count": message_count,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
    return threads


def delete_threads(thread_ids: list[str]) -> int:
    """Delete every message belonging to the given threads.

    Returns the number of message rows removed.
    """
    if not thread_ids:
        return 0
    try:
        connection = _connect()
    except Exception:  # noqa: BLE001
        return 0

    placeholders = ",".join("?" for _ in thread_ids)
    try:
        with connection:
            cursor = connection.execute(
                f"DELETE FROM thread_messages WHERE thread_id IN ({placeholders})",
                list(thread_ids),
            )
            removed = cursor.rowcount
    except Exception:  # noqa: BLE001
        logger.exception("failed to delete expired threads")
        return 0
    return removed


def stale_thread_ids(cutoff: str) -> list[str]:
    """Threads whose most recent message predates ``cutoff`` (ISO text)."""
    try:
        connection = _connect()
    except Exception:  # noqa: BLE001
        return []

    try:
        rows = connection.execute(
            "SELECT thread_id FROM thread_messages "
            "GROUP BY thread_id HAVING MAX(created_at) < ?",
            (cutoff,),
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.exception("failed to find stale threads")
        return []
    return [row[0] for row in rows]


def build_history_context(thread_id: str, limit: int = _MAX_HISTORY_MESSAGES) -> str:
    """Render the thread's history as a compact transcript.

    Only the tail of the conversation is included, and the total size is
    capped, so a long thread cannot push the prompt past the context window.
    """
    messages = load_messages(thread_id, limit)
    if not messages:
        return ""

    lines: list[str] = []
    total = 0
    for message in reversed(messages):
        content = message["content"].strip()
        if not content:
            continue
        entry = f"{'用户' if message['role'] == 'user' else '助手'}：{content}"
        if total + len(entry) > _MAX_HISTORY_CHARS:
            break
        lines.append(entry)
        total += len(entry)

    if not lines:
        return ""
    return "\n".join(reversed(lines))


def clear_messages(thread_id: str) -> int:
    """Delete a thread's stored messages; return how many were removed."""
    if not thread_id:
        return 0
    try:
        connection = _connect()
    except Exception:  # noqa: BLE001
        return 0

    try:
        with connection:
            cursor = connection.execute(
                "DELETE FROM thread_messages WHERE thread_id = ?", (thread_id,)
            )
            removed = cursor.rowcount
    except Exception:  # noqa: BLE001
        logger.exception("failed to clear thread history for %s", thread_id)
        return 0
    return removed
