"""Shared access to the application's SQLite database.

The database lives next to the server (``stock-server/data/checkpoints.db``)
and holds everything the application persists:

* ``thread_messages`` -- the plain message history of each conversation.
* ``user_preferences`` -- durable facts about the user, kept across sessions.

LangGraph's own checkpoint tables live in the same file, but that connection
is owned by :mod:`src.services.checkpoint_store` because the saver needs to
control its own transactions.

One connection is shared by everything else so that the schema is created once
and the handle count stays low.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock

from src.runtime.logging_config import get_logger

logger = get_logger(__name__)

_connection: sqlite3.Connection | None = None
_connection_path: str | None = None
_connection_lock = RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS thread_messages (
    thread_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (thread_id, seq)
);

CREATE TABLE IF NOT EXISTS user_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL DEFAULT 'general',
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _db_path() -> str:
    from src.services.checkpoint_store import checkpoint_db_path

    return checkpoint_db_path()


def get_connection() -> sqlite3.Connection:
    """Return the shared connection, creating the schema on first use."""
    global _connection, _connection_path

    path = _db_path()
    if path == ":memory:":
        raise RuntimeError("in-memory checkpoint store has no persistent tables")

    with _connection_lock:
        if _connection is not None and _connection_path == path:
            return _connection
        if _connection is not None:
            try:
                _connection.close()
            except Exception:  # noqa: BLE001
                logger.debug("failed to close the previous connection", exc_info=True)
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(file_path), check_same_thread=False)
        connection.executescript(_SCHEMA)
        connection.commit()
        _connection = connection
        _connection_path = path
        return _connection


def close_connection() -> None:
    """Release the shared connection."""
    global _connection, _connection_path
    with _connection_lock:
        connection = _connection
        _connection = None
        _connection_path = None
    if connection is None:
        return
    try:
        connection.close()
    except Exception:  # noqa: BLE001
        logger.debug("failed to close the connection", exc_info=True)
