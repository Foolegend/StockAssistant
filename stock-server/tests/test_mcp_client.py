import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langchain_core.tools import StructuredTool

from src.mcp.client import load_mcp_tools_for_agent


class McpClientTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
