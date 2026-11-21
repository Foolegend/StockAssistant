"""Expire conversation data that has not been touched for a while.

Conversations accumulate forever otherwise: every finished turn is written to
``thread_messages`` and, for agent runs, to the checkpoint tables.  A retention
window keeps the database bounded without the user having to delete anything.

Configuration (environment variables):

* ``STOCK_HISTORY_RETENTION_DAYS`` -- days to keep a conversation after its
  last message.  Defaults to 90.  Set to 0 or a negative number to disable.
* ``STOCK_HISTORY_CLEANUP_HOURS`` -- how often the sweep runs.  Defaults to 24.

The sweep also removes ``conversation_history/*.md`` archives whose filesystem
mtime is older than the cutoff.  Those archives are written by the deepagents
middlewares and are not linked to a thread id in the database, so age is the
only reliable signal.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, RLock, Thread

from src.runtime.logging_config import get_logger
from src.services.checkpoint_store import get_checkpointer
from src.services.thread_history import delete_threads, stale_thread_ids

logger = get_logger(__name__)

DEFAULT_RETENTION_DAYS = 90
DEFAULT_CLEANUP_HOURS = 24

# stock-server/src/services/retention.py -> repository root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ARCHIVE_DIR = PROJECT_ROOT / "conversation_history"

_worker: Thread | None = None
_worker_lock = RLock()
_stop = Event()


def retention_days() -> int:
    raw = (os.getenv("STOCK_HISTORY_RETENTION_DAYS") or "").strip()
    if not raw:
        return DEFAULT_RETENTION_DAYS
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid STOCK_HISTORY_RETENTION_DAYS=%r; using default", raw)
        return DEFAULT_RETENTION_DAYS


def cleanup_interval_seconds() -> float:
    raw = (os.getenv("STOCK_HISTORY_CLEANUP_HOURS") or "").strip()
    try:
        hours = float(raw) if raw else DEFAULT_CLEANUP_HOURS
    except ValueError:
        hours = DEFAULT_CLEANUP_HOURS
    return max(hours, 0.25) * 3600


def _cleanup_archives(cutoff_epoch: float) -> int:
    if not ARCHIVE_DIR.is_dir():
        return 0
    removed = 0
    for path in ARCHIVE_DIR.glob("*.md"):
        try:
            if path.stat().st_mtime >= cutoff_epoch:
                continue
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            logger.exception("failed to remove archive %s", path)
    return removed


def cleanup_expired(retention: int | None = None) -> dict:
    """Delete conversations older than the retention window.

    Returns a summary of what was removed.  Never raises: the sweep is a
    background chore, and a failure must not affect the service.
    """
    days = retention_days() if retention is None else retention
    if days <= 0:
        return {"enabled": False, "threads": 0, "messages": 0, "archives": 0}

    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff = cutoff_dt.isoformat(timespec="seconds")

    try:
        stale = stale_thread_ids(cutoff)
    except Exception:  # noqa: BLE001
        logger.exception("retention: failed to find stale threads")
        return {"enabled": True, "threads": 0, "messages": 0, "archives": 0, "error": True}

    if stale:
        # Drop the checkpoints first; their archive paths are not needed here
        # because archives are swept by age below.
        try:
            checkpointer = get_checkpointer()
            for thread_id in stale:
                checkpointer.delete_thread(thread_id)
        except Exception:  # noqa: BLE001
            logger.exception("retention: failed to clear checkpoints")

    try:
        messages = delete_threads(stale)
    except Exception:  # noqa: BLE001
        logger.exception("retention: failed to delete messages")
        messages = 0

    archives = _cleanup_archives(cutoff_dt.timestamp())

    summary = {
        "enabled": True,
        "cutoff": cutoff,
        "threads": len(stale),
        "messages": messages,
        "archives": archives,
    }
    if stale or archives:
        logger.info(
            "retention expired threads=%d messages=%d archives=%d cutoff=%s",
            len(stale),
            messages,
            archives,
            cutoff,
        )
    return summary


def _loop() -> None:
    interval = cleanup_interval_seconds()
    while not _stop.is_set():
        try:
            cleanup_expired()
        except Exception:  # noqa: BLE001 - keep the worker alive
            logger.exception("retention sweep failed")
        if _stop.wait(interval):
            break


def start_worker() -> None:
    """Start the periodic sweep (idempotent)."""
    global _worker
    if retention_days() <= 0:
        logger.info("conversation retention disabled")
        return
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return
        _stop.clear()
        _worker = Thread(target=_loop, name="conversation-retention", daemon=True)
        _worker.start()
        logger.info(
            "conversation retention started: keep %d days, sweep every %.1f h",
            retention_days(),
            cleanup_interval_seconds() / 3600,
        )


def stop_worker() -> None:
    """Ask the periodic sweep to stop."""
    global _worker
    _stop.set()
    with _worker_lock:
        worker = _worker
        _worker = None
    if worker is not None and worker.is_alive():
        worker.join(timeout=5)
