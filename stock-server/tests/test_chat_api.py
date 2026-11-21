import os
import sys
import unittest
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import RateLimitError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api.chat import _active_runs, _active_runs_lock, app, cancel_agent_run
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


class ChatApiStreamingTests(unittest.TestCase):
    def test_stream_emits_status_and_linked_sources(self):
        class FakeModel:
            def stream(self, _messages):
                return iter(
                    [
                        SimpleNamespace(
                            content="先核对公告：[公司公告](https://example.com/filing)"
                        )
                    ]
                )

        client = TestClient(app)
        with patch("src.api.chat.make_llm", return_value=FakeModel()):
            response = client.post(
                "/agent/chat/stream",
                json={"question": "你好", "run_id": "stream-test"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/event-stream; charset=utf-8")
        self.assertIn('event: status\ndata: {"stage": "routing"', response.text)
        self.assertIn("event: complete", response.text)
        self.assertIn('"title": "公司公告"', response.text)
        self.assertIn('"url": "https://example.com/filing"', response.text)

    def test_cancel_endpoint_signals_the_active_run(self):
        run_id = "cancel-test"
        stop_event = Event()
        with _active_runs_lock:
            _active_runs[run_id] = stop_event
        try:
            response = cancel_agent_run(run_id)
        finally:
            with _active_runs_lock:
                _active_runs.pop(run_id, None)

        self.assertTrue(stop_event.is_set())
        self.assertEqual(response["data"]["run_id"], run_id)


if __name__ == "__main__":
    unittest.main()
