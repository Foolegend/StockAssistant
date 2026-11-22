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

from src.api.chat import (
    AgentChatRequest,
    _active_runs,
    _active_runs_lock,
    agent_chat,
    app,
    cancel_agent_run,
)
from src.services.component_dependencies import ResolvedComponents
from src.services.component_router import ComponentSelection


def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://api.minimax.cn/v1/chat/completions")
    response = httpx.Response(429, request=request, headers={"Retry-After": "7"})
    return RateLimitError("rate limit exceeded(RPM)", response=response, body=None)


class ChatApiRateLimitTests(unittest.TestCase):
    def test_rate_limit_returns_429_with_friendly_message(self):
        selection = ComponentSelection(
            agent_id="investment-analyst",
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


class ChatApiSlashCommandTests(unittest.TestCase):
    def test_slash_command_bypasses_llm_routing(self):
        request = AgentChatRequest(question="/stock-report 中兴通讯")
        resolved = ResolvedComponents(
            skill_ids=["stock-analysis"],
            mcp_server_ids=["quant-tools"],
            referenced_agent_ids=[],
        )
        with patch("src.api.chat.select_components") as select_components, patch(
            "src.api.chat.resolve_component_dependencies", return_value=resolved
        ), patch("src.api.chat.make_llm", return_value=object()), patch(
            "src.api.chat.ask_agent", return_value="报告完成"
        ) as ask_agent, patch("src.api.chat.hook_runner.run"):
            response = agent_chat(request)

        select_components.assert_not_called()
        data = response["data"]
        self.assertEqual(data["command"], {"id": "stock-report", "name": "/stock-report"})
        self.assertEqual(data["agent_id"], "investment-analyst")
        self.assertEqual(
            data["mcp_servers"],
            [{"id": "quant-tools", "name": "quant-tools"}],
        )
        self.assertIn("## Command: /stock-report", ask_agent.call_args.args[0])
        self.assertIn("中兴通讯", ask_agent.call_args.args[0])

    def test_slash_skill_uses_pinned_skill_without_router(self):
        request = AgentChatRequest(question="/stock-analysis 分析 TSLA")
        resolved = ResolvedComponents(
            skill_ids=["stock-analysis"],
            mcp_server_ids=[],
            referenced_agent_ids=[],
        )
        with patch("src.api.chat.select_components") as select_components, patch(
            "src.api.chat.resolve_component_dependencies", return_value=resolved
        ), patch("src.api.chat.make_llm", return_value=object()), patch(
            "src.api.chat.ask_agent", return_value="分析完成"
        ) as ask_agent, patch("src.api.chat.hook_runner.run"):
            response = agent_chat(request)

        select_components.assert_not_called()
        self.assertIsNone(response["data"]["agent_id"])
        self.assertIsNone(response["data"]["command"])
        self.assertEqual(ask_agent.call_args.args[0], "分析 TSLA")
        self.assertEqual(
            ask_agent.call_args.kwargs["config"].pinned_skills,
            ["stock-analysis"],
        )

    def test_unknown_slash_command_returns_400(self):
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.api.chat.ask_agent") as ask_agent:
            response = client.post(
                "/agent/chat",
                json={"question": "/does-not-exist"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("未知的斜杠命令", response.json()["detail"])
        ask_agent.assert_not_called()

    def test_stream_reports_command_and_mcp_usage(self):
        resolved = ResolvedComponents(
            skill_ids=["stock-analysis"],
            mcp_server_ids=["quant-tools"],
            referenced_agent_ids=[],
        )
        client = TestClient(app)
        with patch(
            "src.api.chat.resolve_component_dependencies", return_value=resolved
        ), patch("src.api.chat.make_llm", return_value=object()), patch(
            "src.api.chat.stream_agent", return_value=("完成", False)
        ), patch("src.api.chat.hook_runner.run"):
            response = client.post(
                "/agent/chat/stream",
                json={"question": "/stock-report 中兴通讯", "run_id": "cmd-stream"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('"command": {"id": "stock-report"', response.text)
        self.assertIn('"mcp_servers": [{"id": "quant-tools"', response.text)


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
