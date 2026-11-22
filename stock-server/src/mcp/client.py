import asyncio
import copy
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.sessions import StdioConnection
from langchain_mcp_adapters.tools import load_mcp_tools

from src.runtime.logging_config import get_logger


logger = get_logger(__name__)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_MCP_STATUS: dict[str, dict] = {}
_MCP_STATUS_LOCK = RLock()


def _record_mcp_status(
    server_name: str,
    *,
    transport: str,
    status: str,
    tool_count: int = 0,
    tools: list[str] | None = None,
    error: str | None = None,
) -> None:
    entry = {
        "status": status,
        "transport": transport,
        "tool_count": tool_count,
        "tools": list(tools or []),
        "error": error,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with _MCP_STATUS_LOCK:
        _MCP_STATUS[server_name] = entry


def mcp_status_snapshot() -> dict[str, dict]:
    """Return the last known per-server load result (a defensive copy)."""
    with _MCP_STATUS_LOCK:
        return copy.deepcopy(_MCP_STATUS)


def reset_mcp_status() -> None:
    """Clear recorded statuses; intended for tests."""
    with _MCP_STATUS_LOCK:
        _MCP_STATUS.clear()


def _tag_server(tool: BaseTool, server_name: str) -> BaseTool:
    """Record which MCP server a tool belongs to so every call can be traced."""
    metadata = {**(getattr(tool, "metadata", None) or {}), "mcp_server": server_name}
    return tool.model_copy(update={"metadata": metadata})


def _with_sync_invocation(tool: BaseTool) -> BaseTool:
    """Give async-only MCP tools a synchronous entrypoint.

    ``load_mcp_tools`` builds ``StructuredTool`` instances that only define a
    coroutine, so a synchronous agent (``agent.invoke``) fails with
    ``NotImplementedError: StructuredTool does not support sync invocation``.
    LangGraph executes synchronous tools in worker threads that have no running
    event loop, so the coroutine can safely be driven there.
    """
    coroutine = getattr(tool, "coroutine", None)
    if coroutine is None or getattr(tool, "func", None) is not None:
        return tool

    def _run(**kwargs):
        return asyncio.run(coroutine(**kwargs))

    return tool.model_copy(update={"func": _run})


def _server_name(server: object) -> str:
    if isinstance(server, str):
        return Path(os.path.abspath(os.path.normpath(server))).stem
    return server.get("id") or server.get("name") or "mcp-server"


def _server_transport(server: object) -> str:
    if isinstance(server, dict) and server.get("type") == "http":
        return "streamable_http"
    return "stdio"


def _connection_for_server(server: object):
    """Build the transport connection and return (connection, name, transport)."""
    if isinstance(server, str):
        server_path = os.path.abspath(os.path.normpath(server))
        if not os.path.isfile(server_path):
            raise FileNotFoundError(f"MCP server script not found: {server_path}")
        connection: StdioConnection = {
            "transport": "stdio",
            "command": sys.executable,
            "args": [server_path],
            "env": os.environ.copy(),
            "cwd": os.path.dirname(server_path) or PROJECT_ROOT,
        }
        return connection, Path(server_path).stem, "stdio"

    command = server.get("command")
    if isinstance(command, str) and command.strip():
        cwd = server.get("cwd") or PROJECT_ROOT
        if not os.path.isabs(cwd):
            cwd = os.path.join(PROJECT_ROOT, cwd)

        server_env = os.environ.copy()
        for key, value in server.get("env", {}).items():
            if not isinstance(value, str):
                raise ValueError(f"MCP server environment value '{key}' must be a string")
            if value.startswith("${") and value.endswith("}"):
                variable_name = value[2:-1]
                if variable_name not in os.environ:
                    raise ValueError(
                        f"Environment variable '{variable_name}' required by MCP server "
                        f"'{server.get('id', 'unknown')}' is not set"
                    )
                server_env[key] = os.environ[variable_name]
            else:
                server_env[key] = value

        connection = {
            "transport": "stdio",
            "command": command,
            "args": server.get("args", []),
            "env": server_env,
            "cwd": os.path.abspath(cwd),
        }
    elif server.get("type") == "http" and isinstance(server.get("url"), str):
        connection = {
            "transport": "streamable_http",
            "url": server["url"],
        }
        if "headers" in server:
            connection["headers"] = server["headers"]
    else:
        raise ValueError(
            f"MCP server '{server.get('id', 'unknown')}' must define a command or an HTTP URL"
        )

    return connection, _server_name(server), connection["transport"]


def load_mcp_tools_for_agent(mcp_servers):
    async def _load():
        tools = []
        for server in mcp_servers or []:
            server_name = _server_name(server)
            transport = _server_transport(server)
            try:
                connection, server_name, transport = _connection_for_server(server)
            except Exception as error:
                logger.warning(
                    "MCP server %s configuration failed: %s", server_name, error
                )
                _record_mcp_status(
                    server_name,
                    transport=transport,
                    status="error",
                    error=str(error),
                )
                continue

            try:
                server_tools = await load_mcp_tools(
                    None,
                    connection=connection,
                    server_name=server_name,
                )
            except Exception as error:
                logger.exception(
                    "MCP server %s failed to load (transport=%s)",
                    server_name,
                    transport,
                )
                _record_mcp_status(
                    server_name,
                    transport=transport,
                    status="error",
                    error=str(error),
                )
                continue

            tool_names = [tool.name for tool in server_tools]
            logger.info(
                "MCP server %s loaded (transport=%s tools=%s)",
                server_name,
                transport,
                tool_names,
            )
            _record_mcp_status(
                server_name,
                transport=transport,
                status="connected",
                tool_count=len(tool_names),
                tools=tool_names,
            )
            tools.extend(
                _with_sync_invocation(_tag_server(tool, server_name))
                for tool in server_tools
            )
        return tools

    return asyncio.run(_load())
