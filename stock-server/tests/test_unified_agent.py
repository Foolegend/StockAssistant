import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agent.UnifiedAgent import (
    AgentConfig,
    PLUGIN_ROOT,
    _ProjectRootShellBackend,
    _split_thinking,
    _tool_command,
    _to_virtual_tool_path,
    build_agent,
    make_llm,
    stream_agent,
)
from src.agent.selected_skills_middleware import SelectedSkillsMiddleware
from src.services.hook_runner import HookToolMiddleware


class UnifiedAgentTests(unittest.TestCase):
    def test_agent_config_accepts_model_and_components(self):
        skill_dir = (
            Path(__file__).resolve().parents[2]
            / "stock-server"
            / ".stock"
            / "skills"
            / "stock-analysis"
        )
        config = AgentConfig(
            model_name="gpt-oss:120b-cloud",
            skill_dirs=[str(skill_dir)],
            mcp_servers=[],
        )

        self.assertEqual(config.model_name, "gpt-oss:120b-cloud")
        self.assertEqual(config.skill_dirs[0], os.path.normpath(str(skill_dir)))
        self.assertEqual(config.mcp_servers, [])

        agent = build_agent(config)
        self.assertIsNotNone(agent)

    def test_explicit_empty_skill_list_is_preserved(self):
        hooks = {
            "before-tool": {
                "event": "PreToolUse",
                "matcher": '*',
                "command": ["python", "-c", "pass"],
            }
        }
        config = AgentConfig(skill_dirs=[], mcp_servers=[], hooks=hooks)

        with patch("src.agent.UnifiedAgent.make_llm"), patch(
            "src.agent.UnifiedAgent.create_deep_agent"
        ) as create_deep_agent:
            build_agent(config)

        self.assertEqual(create_deep_agent.call_args.kwargs["skills"], [])
        middleware = create_deep_agent.call_args.kwargs["middleware"]
        self.assertIsInstance(middleware[0], HookToolMiddleware)
        self.assertEqual(middleware[0].hook_runner.hooks, hooks)

    def test_hook_plugin_root_is_independent_of_agent_workspace(self):
        with tempfile.TemporaryDirectory() as workspace_root:
            config = AgentConfig(
                project_root=workspace_root,
                skill_dirs=[],
                mcp_servers=[],
                hooks={},
            )
            with patch("src.agent.UnifiedAgent.make_llm"), patch(
                "src.agent.UnifiedAgent.create_deep_agent"
            ) as create_deep_agent:
                build_agent(config)

        middleware = create_deep_agent.call_args.kwargs["middleware"]
        hook_middleware = next(
            middleware_item
            for middleware_item in middleware
            if isinstance(middleware_item, HookToolMiddleware)
        )
        self.assertEqual(hook_middleware.hook_runner.working_directory, workspace_root)
        self.assertEqual(hook_middleware.hook_runner.plugin_root, PLUGIN_ROOT)

    def test_selected_skill_ids_use_filtering_middleware(self):
        with tempfile.TemporaryDirectory() as skill_root:
            config = AgentConfig(
                skill_dirs=[skill_root],
                selected_skill_ids=["stock-analysis"],
                mcp_servers=[],
            )
            with patch("src.agent.UnifiedAgent.make_llm"), patch(
                "src.agent.UnifiedAgent.create_deep_agent"
            ) as create_deep_agent:
                build_agent(config)

        self.assertIsNone(create_deep_agent.call_args.kwargs["skills"])
        middleware = create_deep_agent.call_args.kwargs["middleware"]
        self.assertIsInstance(middleware[0], SelectedSkillsMiddleware)
        self.assertEqual(middleware[0].selected_skill_ids, ("stock-analysis",))

    def test_skill_dirs_use_backend_virtual_paths(self):
        with tempfile.TemporaryDirectory() as project_root:
            skills_dir = Path(project_root) / "stock-server" / ".stock" / "skills"
            skills_dir.mkdir(parents=True)
            config = AgentConfig(
                skill_dirs=[str(skills_dir)],
                project_root=project_root,
                mcp_servers=[],
            )
            with patch("src.agent.UnifiedAgent.make_llm"), patch(
                "src.agent.UnifiedAgent.create_deep_agent"
            ) as create_deep_agent:
                build_agent(config)

        self.assertEqual(
            create_deep_agent.call_args.kwargs["skills"],
            ["/stock-server/.stock/skills"],
        )

    def test_selected_skills_use_backend_virtual_paths(self):
        with tempfile.TemporaryDirectory() as project_root:
            skills_dir = Path(project_root) / "stock-server" / ".stock" / "skills"
            skills_dir.mkdir(parents=True)
            config = AgentConfig(
                skill_dirs=[str(skills_dir)],
                project_root=project_root,
                selected_skill_ids=["stock-analysis"],
                selected_skill_directories={str(skills_dir): ["stock-analysis"]},
                mcp_servers=[],
            )
            with patch("src.agent.UnifiedAgent.make_llm"), patch(
                "src.agent.UnifiedAgent.create_deep_agent"
            ) as create_deep_agent:
                build_agent(config)

        self.assertIsNone(create_deep_agent.call_args.kwargs["skills"])
        middleware = create_deep_agent.call_args.kwargs["middleware"]
        self.assertIsInstance(middleware[0], SelectedSkillsMiddleware)
        self.assertEqual(middleware[0].sources, ["/stock-server/.stock/skills"])
        self.assertEqual(
            middleware[0]._filtered_backend._directories_by_source,
            {"/stock-server/.stock/skills": {"stock-analysis"}},
        )

    def test_subagent_skill_paths_use_backend_virtual_paths(self):
        with tempfile.TemporaryDirectory() as project_root:
            skills_dir = Path(project_root) / "stock-server" / ".stock" / "skills"
            skills_dir.mkdir(parents=True)
            config = AgentConfig(
                skill_dirs=[],
                project_root=project_root,
                subagents=[
                    {
                        "name": "helper",
                        "description": "helper agent",
                        "skills": [str(skills_dir)],
                    }
                ],
                mcp_servers=[],
            )
            with patch("src.agent.UnifiedAgent.make_llm"), patch(
                "src.agent.UnifiedAgent.create_deep_agent"
            ) as create_deep_agent:
                build_agent(config)

        self.assertEqual(
            create_deep_agent.call_args.kwargs["subagents"][0]["skills"],
            ["/stock-server/.stock/skills"],
        )

    @unittest.skipUnless(os.name == "nt", "Windows host path convention")
    def test_windows_host_path_inside_root_maps_to_virtual_path(self):
        with tempfile.TemporaryDirectory() as project_root:
            host_path = os.path.join(project_root, ".tmp", "probe.py")
            self.assertEqual(
                _to_virtual_tool_path(host_path, project_root),
                "/.tmp/probe.py",
            )

    @unittest.skipUnless(os.name == "nt", "Windows host path convention")
    def test_git_bash_style_path_inside_root_maps_to_virtual_path(self):
        with tempfile.TemporaryDirectory() as project_root:
            drive, tail = os.path.splitdrive(project_root)
            posix_path = "/" + drive[0].lower() + tail.replace("\\", "/") + "/.tmp/probe.py"
            self.assertEqual(
                _to_virtual_tool_path(posix_path, project_root),
                "/.tmp/probe.py",
            )

    @unittest.skipUnless(os.name == "nt", "Windows host path convention")
    def test_host_path_outside_root_is_left_for_backend_to_reject(self):
        with tempfile.TemporaryDirectory() as project_root:
            outside = os.path.join(tempfile.gettempdir(), "outside.py")
            self.assertEqual(_to_virtual_tool_path(outside, project_root), outside)

    @unittest.skipUnless(os.name == "nt", "Windows host path convention")
    def test_backend_writes_host_absolute_path_into_project_root(self):
        with tempfile.TemporaryDirectory() as project_root:
            backend = _ProjectRootShellBackend(
                root_dir=project_root,
                env={},
                virtual_mode=True,
            )
            target = os.path.join(project_root, ".tmp", "probe.py")

            result = backend.write(target, "print('ok')")

            self.assertIsNone(result.error)
            self.assertTrue((Path(project_root) / ".tmp" / "probe.py").exists())
            self.assertFalse((Path(project_root) / "d").exists())

            read_result = backend.read(target)
            self.assertIsNone(read_result.error)
            self.assertIn("print('ok')", read_result.file_data["content"])

            tmp_result = backend.write("/tmp/alias.py", "print('tmp')")

            self.assertIsNone(tmp_result.error)
            self.assertTrue((Path(project_root) / ".tmp" / "alias.py").exists())
            self.assertFalse((Path(project_root) / "tmp").exists())

    def test_relative_and_virtual_paths_are_unchanged(self):
        with tempfile.TemporaryDirectory() as project_root:
            self.assertEqual(
                _to_virtual_tool_path(".tmp/probe.py", project_root),
                ".tmp/probe.py",
            )
            self.assertEqual(
                _to_virtual_tool_path("/.tmp/probe.py", project_root),
                "/.tmp/probe.py",
            )
            self.assertEqual(
                _to_virtual_tool_path("/tmp/probe.py", project_root),
                "/.tmp/probe.py",
            )

    def test_execute_tool_command_is_reused_verbatim(self):
        self.assertEqual(
            _tool_command("execute", {"command": "python probe.py"}, "/root"),
            "python probe.py",
        )

    @unittest.skipUnless(os.name == "nt", "Windows shell convention")
    def test_file_tools_get_host_path_commands(self):
        with tempfile.TemporaryDirectory() as project_root:
            target = os.path.join(project_root, "stock", "report.md")
            self.assertEqual(
                _tool_command(
                    "read_file",
                    {"file_path": "/stock/report.md"},
                    project_root,
                ),
                f'type "{target}"',
            )
            self.assertEqual(
                _tool_command("ls", {"path": "/stock"}, project_root),
                f'dir "{os.path.join(project_root, "stock")}"',
            )

    def test_mutating_file_tools_have_no_local_command(self):
        self.assertEqual(_tool_command("write_file", {"file_path": "/x"}, "/root"), "")
        self.assertEqual(_tool_command("edit_file", {"file_path": "/x"}, "/root"), "")

    def test_split_thinking_removes_think_blocks(self):
        answer, thought = _split_thinking(
            "<think>Check the filing first.</think>\n\nFinal answer"
        )

        self.assertEqual(answer, "Final answer")
        self.assertEqual(thought, "Check the filing first.")

    def test_quant_tool_command_carries_base64_arguments(self):
        command = _tool_command("normalize_symbol", {"code": "600519"}, "/root")

        self.assertIn("quant_tool_run.py", command)
        encoded = command.rsplit(" ", 1)[-1]
        self.assertEqual(
            json.loads(base64.b64decode(encoded).decode("utf-8")),
            {"code": "600519"},
        )

    def test_make_llm_configures_retries_and_timeout(self):
        with patch.dict(
            os.environ,
            {"OPENAI_MAX_RETRIES": "7", "OPENAI_TIMEOUT_SECONDS": "90"},
        ):
            llm = make_llm()

        self.assertEqual(llm.max_retries, 7)
        self.assertEqual(llm.request_timeout, 90)

    def test_missing_explicit_skill_directory_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = AgentConfig(skill_dirs=[str(Path(temp_dir) / "missing-skills")])
            with patch("src.agent.UnifiedAgent.make_llm"):
                with self.assertRaises(FileNotFoundError):
                    build_agent(config)


class AgentStreamingTests(unittest.TestCase):
    class FakeAgent:
        def stream(self, _input, config=None, **kwargs):
            self.config = config
            self.kwargs = kwargs
            yield {
                "agent": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": "call-1",
                                    "name": "quote",
                                    "args": {},
                                    "type": "tool_call",
                                }
                            ],
                        )
                    ]
                }
            }
            yield {
                "tools": {
                    "messages": [
                        ToolMessage(content="latest quote", tool_call_id="call-1", name="quote")
                    ]
                }
            }
            yield {"agent": {"messages": [AIMessage(content="分析完成")]}}

    def test_stream_reports_actual_tool_start_and_completion(self):
        agent = self.FakeAgent()
        events = []
        with patch("src.agent.UnifiedAgent._cached_agent", return_value=agent):
            answer, cancelled = stream_agent(
                "分析 TSLA",
                thread_id="stream-thread",
                on_event=lambda event, data: events.append((event, data)),
            )

        self.assertEqual(answer, "分析完成")
        self.assertFalse(cancelled)
        self.assertEqual(
            [event for event, _data in events],
            ["tool_start", "tool_end"],
        )
        self.assertEqual(events[0][1]["tool"], "quote")
        self.assertEqual(events[0][1]["args"], "")
        self.assertEqual(events[1][1]["tool_id"], "call-1")
        self.assertEqual(events[1][1]["output"], "latest quote")
        self.assertFalse(events[1][1]["error"])
        self.assertIsInstance(events[1][1]["duration_ms"], int)
        self.assertEqual(agent.config["configurable"]["thread_id"], "stream-thread")
        self.assertEqual(agent.kwargs["stream_mode"], "updates")

    def test_stream_emits_thoughts_and_strips_them_from_the_answer(self):
        class ThinkingAgent:
            def stream(self, _input, config=None, **kwargs):
                yield {
                    "agent": {
                        "messages": [
                            AIMessage(
                                content="<think>Look up a quote.</think>",
                                tool_calls=[
                                    {
                                        "id": "call-1",
                                        "name": "quote",
                                        "args": {},
                                        "type": "tool_call",
                                    }
                                ],
                            )
                        ]
                    }
                }
                yield {
                    "tools": {
                        "messages": [
                            ToolMessage(
                                content="latest quote",
                                tool_call_id="call-1",
                                name="quote",
                            )
                        ]
                    }
                }
                yield {
                    "agent": {
                        "messages": [
                            AIMessage(
                                content="<think>Summarize the quote.</think>Final answer"
                            )
                        ]
                    }
                }

        events = []
        with patch(
            "src.agent.UnifiedAgent._cached_agent", return_value=ThinkingAgent()
        ):
            answer, cancelled = stream_agent(
                "分析 TSLA",
                on_event=lambda event, data: events.append((event, data)),
            )

        self.assertFalse(cancelled)
        self.assertEqual(answer, "Final answer")
        self.assertEqual(
            [event for event, _data in events],
            ["thought", "tool_start", "tool_end", "thought"],
        )
        self.assertEqual(events[0][1]["text"], "Look up a quote.")
        self.assertEqual(events[3][1]["text"], "Summarize the quote.")
        self.assertIsInstance(events[0][1]["duration_ms"], int)

    def test_stream_stops_before_requesting_the_next_graph_update(self):
        agent = self.FakeAgent()
        stop_event = Event()
        events = []

        def record_event(event, data):
            events.append((event, data))
            if event == "tool_start":
                stop_event.set()

        with patch("src.agent.UnifiedAgent._cached_agent", return_value=agent):
            answer, cancelled = stream_agent(
                "分析 TSLA",
                on_event=record_event,
                stop_event=stop_event,
            )

        self.assertIsNone(answer)
        self.assertTrue(cancelled)
        self.assertEqual([event for event, _data in events], ["tool_start"])


if __name__ == "__main__":
    unittest.main()
