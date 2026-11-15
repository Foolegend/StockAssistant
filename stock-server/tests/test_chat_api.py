import os
import sys
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import RateLimitError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api.chat import app
from src.services.component_dependencies import ResolvedComponents
from src.services.component_router import ComponentSelection


def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://api.minimax.cn/v1/chat/completions")
    response = httpx.Response(429, request=request, headers={"Retry-After": "7"})
    return RateLimitError("rate limit exceeded(RPM)", response=response, body=None)


class ChatApiRateLimitTests(unittest.TestCase):
    def test_rate_limit_returns_429_with_friendly_message(self):
        selection = ComponentSelection(
            agent_id="stock-analyst",
            skill_ids=["stock-analysis"],
        )
        resolved = ResolvedComponents(
            command_ids=[],
            skill_ids=["stock-analysis"],
            mcp_server_ids=[],
            referenced_agent_ids=[],
        )
        client = TestClient(app, raise_server_exceptions=False)

        with patch("src.api.chat.select_components", return_value=selection), patch(
            "src.api.chat.resolve_component_dependencies", return_value=resolved
        ), patch("src.api.chat.make_llm", return_value=object()), patch(
            "src.api.chat.ask_agent", side_effect=_rate_limit_error()
        ), patch("src.api.chat.hook_runner.run"):
            response = client.post("/agent/chat", json={"question": "分析 TSLA"})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers.get("Retry-After"), "7")
        payload = response.json()
        self.assertEqual(payload["code"], 429)
        self.assertIn("限流", payload["message"])


if __name__ == "__main__":
    unittest.main()
