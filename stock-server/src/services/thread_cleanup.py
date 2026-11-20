"""Delete the server-side state that belongs to a conversation thread.

A conversation leaves three traces on the server:

* LangGraph checkpoints (messages, tool calls, summary events) held by the
  agent's ``MemorySaver``.
* ``conversation_history/*.md`` files that the deepagents filesystem and
  summarization middlewares write when they offload long messages.
* Nothing else -- the frontend keeps its own copy in ``localStorage``.

The archived files are named by the middleware (``session_<hex>`` today, but
older versions used the thread id), so the only reliable way to find them is
to read the paths the checkpoint state records: ``_summarization_event`` for
the summarization archive and the ``lc_evicted_to`` marker on individual
messages for the filesystem offloads.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

_SUMMARIZATION_EVENT_KEY = "_summarization_event"
_EVICTED_TO_KEY = "lc_evicted_to"
_CONVERSATION_HISTORY_DIR = "conversation_history"


def _message_archive_paths(messages: Iterable) -> list[str]:
    """Collect ``lc_evicted_to`` paths from a list of messages."""
    paths: list[str] = []
    for message in messages or []:
        if isinstance(message, dict):
            additional = message.get("additional_kwargs") or {}
        else:
            additional = getattr(message, "additional_kwargs", None) or {}
        path = additional.get(_EVICTED_TO_KEY)
        if isinstance(path, str) and path:
            paths.append(path)
    return paths


def _state_archive_paths(checkpoint) -> list[str]:
    """Collect archive paths recorded in a checkpoint's channel values."""
    if not checkpoint:
        return []
    values = checkpoint.get("channel_values") or {}

    paths: list[str] = []
    event = values.get(_SUMMARIZATION_EVENT_KEY)
    if isinstance(event, dict):
        path = event.get("file_path")
        if isinstance(path, str) and path:
            paths.append(path)

    paths.extend(_message_archive_paths(values.get("messages") or []))
    return paths


def collect_thread_archive_paths(checkpointer, thread_id: str) -> list[str]:
    """Return every archive path the thread's checkpoints refer to."""
    if checkpointer is None:
        return []
    try:
        checkpoints = list(checkpointer.list({"configurable": {"thread_id": thread_id}}))
    except Exception:  # noqa: BLE001 - a broken saver must not block deletion
        logger.exception("failed to list checkpoints for thread %s", thread_id)
        return []

    paths: list[str] = []
    for checkpoint in checkpoints:
        values = getattr(checkpoint, "checkpoint", None) or {}
        paths.extend(_state_archive_paths(values))
    # Keep the order stable while removing duplicates.
    return list(dict.fromkeys(paths))


def resolve_archive_file(path: str, project_root: Path) -> Path | None:
    """Map a virtual archive path onto a real file inside the project root.

    The middlewares write paths like ``/conversation_history/session_x.md``
    against the backend's virtual root, which is the project root.  Only files
    that live under ``<project_root>/conversation_history`` are considered, so
    a malformed path can never point the cleanup at an unrelated file.
    """
    if not path or not isinstance(path, str):
        return None

    relative = path.lstrip("/")
    if not relative.startswith(f"{_CONVERSATION_HISTORY_DIR}/"):
        return None

    candidate = (project_root / relative).resolve()
    history_root = (project_root / _CONVERSATION_HISTORY_DIR).resolve()
    if history_root != candidate.parent and history_root not in candidate.parents:
        return None
    return candidate


def delete_archive_files(paths: Iterable[str], project_root: Path) -> list[str]:
    """Delete the given archive files; return the ones actually removed."""
    removed: list[str] = []
    for path in paths:
        target = resolve_archive_file(path, project_root)
        if target is None:
            continue
        try:
            target.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            logger.exception("failed to delete archive file %s", target)
            continue
        removed.append(str(target))
        logger.info("deleted conversation archive %s", target)
    return removed


def delete_thread_memory(checkpointer, thread_id: str) -> bool:
    """Drop every checkpoint and write recorded for ``thread_id``."""
    if checkpointer is None:
        return False
    try:
        checkpointer.delete_thread(thread_id)
    except Exception:  # noqa: BLE001 - deletion must not fail the request
        logger.exception("failed to delete checkpoints for thread %s", thread_id)
        return False
    return True
