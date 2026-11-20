"""Process-wide checkpoint storage for conversation threads.

Every agent built by the server keeps its conversation state in a
checkpointer.  With an in-memory saver that state dies with the process, and
because agents are cached per configuration fingerprint -- and the router
picks a different set of skills on nearly every question -- one thread ends
up spread over several savers, so it loses its history as soon as the routing
changes.

This module hands out a single SQLite-backed saver instead.  All agents share
one database file, so a ``thread_id`` keeps its history no matter which
skills the router selects, and the history survives a restart.

Set ``STOCK_CHECKPOINT_DB`` to point the database somewhere else, or
``STOCK_CHECKPOINT_DB=:memory:`` to fall back to in-memory storage.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from threading import RLock

from langgraph.checkpoint.memory import MemorySaver

from src.runtime.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_DB_NAME = "checkpoints.db"
_MEMORY_MARKER = ":memory:"

_saver = None
_saver_lock = RLock()
_saver_kind = "none"


def _default_db_path() -> Path:
    backend_root = Path(__file__).resolve().parents[2]
    return backend_root / "data" / DEFAULT_DB_NAME


def _resolve_db_path() -> str:
    configured = (os.getenv("STOCK_CHECKPOINT_DB") or "").strip()
    if configured:
        return configured
    return str(_default_db_path())


def _build_saver():
    """Create the shared saver, falling back to memory when SQLite fails."""
    global _saver_kind

    db_path = _resolve_db_path()
    if db_path == _MEMORY_MARKER:
        logger.info("checkpoint store: using in-memory storage")
        _saver_kind = "memory"
        return MemorySaver()

    path = Path(db_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Import lazily so deployments that do not install the sqlite extra
        # still start (they fall back to in-memory storage).
        from langgraph.checkpoint.sqlite import SqliteSaver

        # The saver keeps a live connection, so it must outlive this call.
        # ``check_same_thread=False`` is safe because SqliteSaver serialises
        # access with its own lock.
        connection = sqlite3.connect(str(path), check_same_thread=False)
        saver = SqliteSaver(connection)
        logger.info("checkpoint store: sqlite at %s", path)
        _saver_kind = "sqlite"
        return saver
    except Exception:  # noqa: BLE001 - persistence is best effort
        logger.exception(
            "checkpoint store: sqlite unavailable at %s, falling back to memory", db_path
        )
        _saver_kind = "memory"
        return MemorySaver()


def get_checkpointer():
    """Return the shared checkpointer, creating it on first use."""
    global _saver
    with _saver_lock:
        if _saver is None:
            _saver = _build_saver()
        return _saver


def checkpoint_store_kind() -> str:
    """Return ``"sqlite"`` or ``"memory"`` for diagnostics."""
    get_checkpointer()
    return _saver_kind


def checkpoint_db_path() -> str:
    """Return the configured database path (informational)."""
    return _resolve_db_path()


def close_checkpointer() -> None:
    """Close the shared saver's database connection.

    Windows refuses to delete a file that still has an open handle, so the
    connection has to be released before the database can be removed.
    """
    global _saver
    with _saver_lock:
        saver = _saver
        _saver = None
    if saver is None:
        return
    connection = getattr(saver, "conn", None)
    if connection is None:
        return
    try:
        connection.close()
    except Exception:  # noqa: BLE001 - closing is best effort
        logger.debug("failed to close the checkpoint database", exc_info=True)
