"""Application-wide logging configuration.

Every log line carries a request id so a single client request can be followed
through component routing, agent execution, MCP tools and hooks.  The id is
taken from the ``X-Request-ID`` request header when the client sends one,
otherwise a short uuid is generated per request.

Logs go to the console *and* to a rotating UTF-8 file under ``logs/`` so a
traceback survives the terminal window being closed (and is not mangled by the
Windows console code page).

Environment overrides
---------------------
``STOCK_LOG_LEVEL``      ``DEBUG`` / ``INFO`` / ``WARNING`` / ``ERROR`` (default ``INFO``)
``STOCK_LOG_DIR``        log directory (default ``<stock-server>/logs``)
``STOCK_LOG_FILE``       log file name (default ``stock-server.log``)
``STOCK_LOG_MAX_BYTES``  rotation threshold in bytes (default 10 MiB)
``STOCK_LOG_BACKUPS``    rotated files to keep (default 5)
``STOCK_LOG_CONSOLE``    set to ``0``/``false`` to disable console output
"""

from __future__ import annotations

import contextvars
import logging
import logging.handlers
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

BACK_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_DIR = BACK_ROOT / "logs"

LOG_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)-7s | req=%(request_id)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_HANDLER_MARKER = "_stock_log_handler"
_REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "stock_request_id", default="-"
)


def new_request_id() -> str:
    """Return a short id suitable for correlating one request."""
    return uuid.uuid4().hex[:12]


def get_request_id() -> str:
    """Return the request id bound to the current context (``-`` outside one)."""
    return _REQUEST_ID.get()


@contextmanager
def request_context(request_id: str | None = None) -> Iterator[str]:
    """Bind a request id for the duration of the ``with`` block."""
    token = _REQUEST_ID.set(request_id or new_request_id())
    try:
        yield _REQUEST_ID.get()
    finally:
        _REQUEST_ID.reset(token)


class _RequestIdFilter(logging.Filter):
    """Attach the current request id to every record handled by our handlers."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _REQUEST_ID.get()
        return True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def preview(value: object, limit: int = 300) -> str:
    """Collapse a payload into a single short line, safe for log output."""
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[:limit] + f"...({len(text)} chars)"


def _coerce_level(value: str | int | None) -> int:
    if isinstance(value, int):
        return value
    text = str(value or "INFO").strip().upper()
    if text.isdigit():
        return int(text)
    level = logging.getLevelName(text)
    return level if isinstance(level, int) else logging.INFO


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except ValueError:
        return default


def _build_file_handler(log_dir: Path) -> logging.Handler | None:
    file_name = os.getenv("STOCK_LOG_FILE") or "stock-server.log"
    path = log_dir / file_name
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            path,
            maxBytes=_env_int("STOCK_LOG_MAX_BYTES", 10 * 1024 * 1024),
            backupCount=_env_int("STOCK_LOG_BACKUPS", 5),
            encoding="utf-8",
        )
    except OSError as error:  # pragma: no cover - depends on the filesystem
        logging.getLogger(__name__).warning(
            "File logging disabled, cannot open %s: %s", path, error
        )
        return None
    return handler


def _build_console_handler() -> logging.Handler | None:
    if not _env_flag("STOCK_LOG_CONSOLE", True):
        return None
    handler = logging.StreamHandler(stream=sys.stdout)
    stream = getattr(handler, "stream", None)
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            # Keep the console code page, but never crash on an unencodable char.
            reconfigure(errors="backslashreplace")
        except (ValueError, OSError):  # pragma: no cover - platform dependent
            pass
    return handler


def _remove_own_handlers(logger: logging.Logger) -> None:
    """Drop handlers from a previous setup_logging() call, keep foreign ones."""
    for existing in list(logger.handlers):
        if getattr(existing, _HANDLER_MARKER, False):
            logger.removeHandler(existing)
            existing.close()


def _attach(logger: logging.Logger, handler: logging.Handler) -> None:
    setattr(handler, _HANDLER_MARKER, True)
    logger.addHandler(handler)


_configured = False


def setup_logging(
    level: str | int | None = None,
    log_dir: str | os.PathLike[str] | None = None,
    force: bool = False,
) -> logging.Logger:
    """Configure console + rotating file logging for the whole process.

    Idempotent: repeated calls (module import, application startup) replace the
    handlers created here rather than stacking duplicates.  ``force=True``
    reconfigures even if the settings have not changed, which tests use to
    redirect the log file.
    """
    global _configured
    root = logging.getLogger()
    level_value = _coerce_level(level if level is not None else os.getenv("STOCK_LOG_LEVEL", "INFO"))
    if _configured and not force and root.handlers:
        return root

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    request_filter = _RequestIdFilter()

    _remove_own_handlers(root)
    _remove_own_handlers(logging.getLogger("uvicorn"))

    console_handler = _build_console_handler()
    if console_handler is not None:
        console_handler.setFormatter(formatter)
        console_handler.addFilter(request_filter)
        _attach(root, console_handler)

    file_handler = _build_file_handler(Path(log_dir) if log_dir else DEFAULT_LOG_DIR)
    if file_handler is not None:
        file_handler.setFormatter(formatter)
        file_handler.addFilter(request_filter)
        _attach(root, file_handler)
        # uvicorn.error propagates to the "uvicorn" logger, so a single handler
        # there captures startup/error lines once.  "uvicorn.access" is left
        # alone: giving it a handler makes uvicorn treat access logging as
        # enabled again (see H11Protocol.access_log).
        _attach(logging.getLogger("uvicorn"), file_handler)

    root.setLevel(level_value)

    # Third-party HTTP clients are chatty at INFO and drown the app's own logs.
    for name in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.captureWarnings(True)
    _configured = True
    return root
