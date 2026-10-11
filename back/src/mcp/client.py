import asyncio
import os
import sys

from langchain_mcp_adapters.sessions import StdioConnection
from langchain_mcp_adapters.tools import load_mcp_tools


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_mcp_tools_for_agent(mcp_servers):
    async def _load():
        tools = []
        for server in mcp_servers or []:
            server_path = os.path.normpath(server)
            if not os.path.exists(server_path):
                continue

            connection: StdioConnection = {
                "transport": "stdio",
                "command": sys.executable,
                "args": [server_path],
                "env": os.environ.copy(),
                "cwd": os.path.dirname(server_path) or PROJECT_ROOT,
            }
            tools.extend(
                await load_mcp_tools(
                    None,
                    connection=connection,
                    server_name=os.path.basename(server_path),
                )
            )
        return tools

    return asyncio.run(_load())
