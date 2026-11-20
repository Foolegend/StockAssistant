"""Read and write the plain message history of a conversation thread.

The agent path stores its history in LangGraph checkpoints, which only the
agent graph knows how to interpret.  The direct-answer path -- greetings and
questions no registered component matches -- never enters the graph, so it
needs a simpler store to keep multi-turn context.

Both paths share one database, so a conversation keeps a single history even
when the router sends some turns to an agent and answers others directly.
"""

from __future__ import annotations

import sqlite3

from src.runtime.logging_config import get_logger
from src.services.db import get_connection

logger = get_logger(__name__)

_MAX_HISTORY_MESSAGES = 40
_MAX_HISTORY_CHARS = 12000


def _connect() -> sqlite3.Connection:
    """Return the shared connection to the application tables."""
    return get_connection()


def close_history_connection() -> None:
    """Kept for symmetry with the other stores; the connection is shared."""
    from src.services.db import close_connection

    close_connection()


def append_messages(thread_id: str, role: str, content: str) -> None:
    """Append one message to a thread's history."""
    if not thread_id or not content.strip():
        return
    try:
        connection = _connect()
    except Exception:  # noqa: BLE001 - history is a nicety, not a requirement
        logger.debug("thread history unavailable; skipping append", exc_info=True)
        return

    try:
        with connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 FROM thread_messages WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            connection.execute(
                "INSERT INTO thread_messages (thread_id, seq, role, content) VALUES (?, ?, ?, ?)",
                (thread_id, row[0], role, content),
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
            "SELECT role, content FROM thread_messages WHERE thread_id = ? ORDER BY seq DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.exception("failed to load thread history for %s", thread_id)
        return []

    return [{"role": role, "content": content} for role, content in reversed(rows)]


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
