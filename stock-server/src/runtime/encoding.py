"""Windows-safe decoding for subprocess output.

On a Chinese Windows install the preferred encoding is ``cp936`` (GBK), yet many
child processes emit UTF-8: Python children started with a UTF-8 locale, Node,
Git, and anything proxying web data.  ``subprocess.run(..., text=True)`` decodes
with the *locale* encoding, so the first byte that does not fit raises
``UnicodeDecodeError`` inside the reader thread.  That kills the reader thread
and truncates the captured output, which is exactly what happened to the
``execute`` tool while analysing a Chinese A-share ticker.

The helpers below decode child output tolerantly: UTF-8 first, then the
Windows ANSI code page, and never raise.
"""

from __future__ import annotations

import importlib
import locale
import subprocess
from typing import Any


def _candidate_encodings() -> tuple[str, ...]:
    """Return the encodings to try, most specific first."""
    candidates: list[str] = ["utf-8"]
    try:
        preferred = locale.getpreferredencoding(False)
    except Exception:  # pragma: no cover - locale lookups rarely fail
        preferred = ""
    for encoding in (preferred, "gbk", "cp936"):
        if encoding and encoding.lower() not in {c.lower() for c in candidates}:
            candidates.append(encoding)
    # latin-1 never fails, so it acts as the final safety net.
    candidates.append("latin-1")
    return tuple(candidates)


def decode_output(data: bytes | str | None) -> str:
    """Decode subprocess output without ever raising ``UnicodeDecodeError``."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    for encoding in _candidate_encodings():
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


class TolerantSubprocess:
    """Proxy around :mod:`subprocess` whose text mode decodes tolerantly.

    Only ``run`` is intercepted; every other attribute is delegated to the real
    :mod:`subprocess` module so ``TimeoutExpired``, ``DEVNULL`` and friends keep
    working unchanged.
    """

    def __init__(self, real: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def run(self, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        text_mode = bool(kwargs.get("text") or kwargs.get("universal_newlines"))
        if not text_mode:
            return self._real.run(*args, **kwargs)

        kwargs.pop("text", None)
        kwargs.pop("universal_newlines", None)
        kwargs.pop("encoding", None)
        kwargs.pop("errors", None)

        payload = kwargs.get("input")
        if isinstance(payload, str):
            kwargs["input"] = payload.encode("utf-8")

        result = self._real.run(*args, **kwargs)
        return subprocess.CompletedProcess(
            result.args,
            result.returncode,
            decode_output(result.stdout),
            decode_output(result.stderr),
        )


_TOLERANT_TARGETS = (
    "deepagents.backends.local_shell",
    "deepagents.backends.filesystem",
)


def install_tolerant_subprocess(targets: tuple[str, ...] = _TOLERANT_TARGETS) -> None:
    """Make the given modules decode subprocess output tolerantly.

    The agent backends call ``subprocess.run(..., text=True)`` directly, so the
    locale codec leaks into their output handling.  Swapping the ``subprocess``
    reference inside those modules keeps the fix local to the agent runtime.
    """
    for target in targets:
        try:
            module = importlib.import_module(target)
        except ImportError:  # pragma: no cover - deepagents is a hard dependency
            continue
        current = getattr(module, "subprocess", None)
        if current is None or isinstance(current, TolerantSubprocess):
            continue
        module.subprocess = TolerantSubprocess(current)
