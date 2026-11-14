import asyncio
import os
import sys
from pathlib import Path

from langchain_mcp_adapters.sessions import StdioConnection
from langchain_mcp_adapters.tools import load_mcp_tools


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_mcp_tools_for_agent(mcp_servers):
    async def _load():
        tools = []
        for server in mcp_servers or []:
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
                server_name = Path(server_path).stem
            else:
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

                server_name = server.get("id") or server.get("name")

            tools.extend(
                await load_mcp_tools(
                    None,
                    connection=connection,
                    server_name=server_name,
                )
            )
        return tools

    return asyncio.run(_load())
