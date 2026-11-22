import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langchain_core.tools import StructuredTool

from src.mcp.client import (
    load_mcp_tools_for_agent,
    mcp_status_snapshot,
    reset_mcp_status,
)


class McpClientTests(unittest.TestCase):
    def setUp(self):
        reset_mcp_status()

    def _quote_tool(self, calls):
        async def call_tool(**kwargs):
            calls.append(kwargs)
            return f"{kwargs['symbol']}:{kwargs['limit']}"

        return StructuredTool(
            name="quote",
            description="Fetch a quote.",
            args_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
            coroutine=call_tool,
            response_format="content",
        )

    def test_async_only_mcp_tools_support_sync_invocation(self):
        calls = []
        server = {"id": "market-data", "command": "python"}

        with patch(
            "src.mcp.client.load_mcp_tools",
            AsyncMock(return_value=[self._quote_tool(calls)]),
        ):
            tools = load_mcp_tools_for_agent([server])

        self.assertEqual(len(tools), 1)
        self.assertIsNotNone(tools[0].func)
        self.assertEqual(tools[0].metadata["mcp_server"], "market-data")
        self.assertEqual(tools[0].invoke({"symbol": "TSLA", "limit": 3}), "TSLA:3")
        self.assertEqual(calls, [{"symbol": "TSLA", "limit": 3}])

    def test_sync_capable_tools_are_returned_unchanged(self):
        def sync_tool(**kwargs):
            return "ok"

        tool = StructuredTool(
            name="sync",
            description="Already synchronous.",
            func=sync_tool,
            args_schema={"type": "object", "properties": {}},
        )
        server = {"id": "market-data", "command": "python"}

        with patch("src.mcp.client.load_mcp_tools", AsyncMock(return_value=[tool])):
            tools = load_mcp_tools_for_agent([server])

        self.assertIs(tools[0].func, sync_tool)
        self.assertEqual(tools[0].invoke({}), "ok")
        self.assertEqual(tools[0].metadata["mcp_server"], "market-data")
        status = mcp_status_snapshot()["market-data"]
        self.assertEqual(status["status"], "connected")
        self.assertEqual(status["tool_count"], 1)
        self.assertEqual(status["tools"], ["sync"])

    def test_failed_server_does_not_abort_healthy_servers(self):
        tool = StructuredTool(
            name="quote",
            description="Fetch a quote.",
            func=lambda **kwargs: "ok",
            args_schema={"type": "object", "properties": {}},
        )
        servers = [
            {"id": "broken", "command": "python"},
            {"id": "healthy", "command": "python"},
        ]

        with patch(
            "src.mcp.client.load_mcp_tools",
            AsyncMock(side_effect=[RuntimeError("boom"), [tool]]),
        ):
            tools = load_mcp_tools_for_agent(servers)

        self.assertEqual([item.name for item in tools], ["quote"])
        statuses = mcp_status_snapshot()
        self.assertEqual(statuses["broken"]["status"], "error")
        self.assertIn("boom", statuses["broken"]["error"])
        self.assertEqual(statuses["healthy"]["status"], "connected")
        self.assertEqual(statuses["healthy"]["tool_count"], 1)

    def test_missing_env_var_is_recorded_instead_of_aborting(self):
        server = {
            "id": "needs-env",
            "command": "python",
            "env": {"TOKEN": "${MISSING_MCP_TOKEN}"},
        }

        tools = load_mcp_tools_for_agent([server])

        self.assertEqual(tools, [])
        status = mcp_status_snapshot()["needs-env"]
        self.assertEqual(status["status"], "error")
        self.assertIn("MISSING_MCP_TOKEN", status["error"])


if __name__ == "__main__":
    unittest.main()
