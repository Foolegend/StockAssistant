"""Run one quant-tools MCP tool locally and print its JSON result.

Usage::

    python quant_tool_run.py <tool_name> <base64-encoded JSON arguments>

The arguments are base64-encoded so the generated command can be pasted into
cmd.exe, PowerShell or Bash without shell quoting problems.
"""

from __future__ import annotations

import base64
import binascii
import importlib.util
import json
import sys
from pathlib import Path

SERVER_PATH = Path(__file__).with_name("quant_tools_server.py")


def _load_server():
    spec = importlib.util.spec_from_file_location("quant_tools_server", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SERVER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: python quant_tool_run.py <tool> <base64-json-args>",
            file=sys.stderr,
        )
        return 2

    tool_name, encoded = argv[1], argv[2]
    try:
        arguments = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"invalid base64 JSON arguments: {error}", file=sys.stderr)
        return 2
    if not isinstance(arguments, dict):
        print("arguments must be a JSON object", file=sys.stderr)
        return 2

    try:
        result = _load_server()._call_tool(
            {"name": tool_name, "arguments": arguments}
        )
    except Exception as error:  # noqa: BLE001 - surface tool errors to the caller
        print(f"tool call failed: {error}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
