import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException
from langchain_core.messages import ToolMessage
from pydantic import ValidationError

from src.api.chat import AgentChatRequest, agent_chat, agent_catalog, extension_registry
from src.mcp.client import load_mcp_tools_for_agent
from src.services.component_dependencies import ResolvedComponents
from src.services.component_router import ComponentSelection
from src.services.extension_registry import ExtensionRegistry
from src.services.hook_matcher import matches_hook
from src.services.hook_runner import HookExecutionError, HookRunner, HookToolMiddleware


class ExtensionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_root = self.root / "config"
        self.config_root.mkdir(parents=True)
        self.skills_root = self.root / "stock-server" / ".stock" / "skills"
        self.skills_root.mkdir(parents=True)
        chat_command = self.root / "stock-server" / ".stock" / "commands" / "chat.md"
        chat_command.parent.mkdir(parents=True)
        chat_command.write_text(
            "---\nname: Chat\ndescription: General chat.\n---\nAnswer clearly.\n",
            encoding="utf-8",
        )
        agents_dir = self.root / "stock-server" / ".stock" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "assistant.md").write_text(
            "---\nname: assistant\ndisplay_name: Assistant\n"
            "skills: [sample-skill]\nmcp_server_ids: [local-tools]\n---\n",
            encoding="utf-8",
        )
        mcp_config_dir = self.root / "stock-server" / ".stock" / "mcp-configs"
        mcp_config_dir.mkdir(parents=True, exist_ok=True)
        (mcp_config_dir / "mcp-servers.json").write_text(
            json.dumps({
                "mcpServers": {
                    "local-tools": {
                        "command": "node",
                        "args": ["server.js"],
                        "env": {"API_KEY": "${LOCAL_TOOLS_KEY}"},
                    }
                }
            }),
            encoding="utf-8",
        )
        skill_dir = self.skills_root / "sample-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: sample-skill\ndescription: |\n  A test skill.\n  It has two lines.\n---\n# Sample\n",
            encoding="utf-8",
        )
    def tearDown(self):
        self.temp_dir.cleanup()

    def write_config(self, name, content):
        (self.config_root / name).write_text(json.dumps(content), encoding="utf-8")

    def test_catalog_uses_display_names_and_registered_ids(self):
        registry = ExtensionRegistry(self.config_root, project_root=self.root)

        catalog = registry.catalog()

        self.assertEqual(catalog["agents"][0]["name"], "Assistant")
        self.assertEqual(catalog["agents"][0]["skills"][0]["id"], "sample-skill")
        self.assertEqual(catalog["commands"][0]["name"], "Chat")
        self.assertEqual(catalog["mcp_servers"][0]["id"], "local-tools")
        self.assertEqual(catalog["skills"][0]["id"], "sample-skill")
        self.assertNotIn("default_agent_id", catalog)
        self.assertNotIn("default_command_id", catalog)

    def test_component_bodies_are_read_only_when_requested(self):
        with patch(
            "src.services.extension_registry._read_markdown",
            side_effect=AssertionError("component body was read during discovery"),
        ):
            registry = ExtensionRegistry(self.config_root, project_root=self.root)

        self.assertNotIn("prompt", registry.agents["assistant"])
        self.assertNotIn("prompt", registry.commands["chat"])
        self.assertNotIn("content", registry.skills["sample-skill"])

        self.assertEqual(registry.component_content("agent", "assistant"), "")
        self.assertEqual(
            registry.component_content("command", "chat"),
            "Answer clearly.",
        )
        self.assertIn(
            "# Sample",
            registry.component_content("skill", "sample-skill"),
        )

    def test_component_body_is_read_from_disk_after_registry_discovery(self):
        registry = ExtensionRegistry(self.config_root, project_root=self.root)
        command_file = Path(registry.commands["chat"]["file_path"])
        command_file.write_text(
            "---\nname: Chat\ndescription: Updated summary.\n---\nUpdated body.\n",
            encoding="utf-8",
        )

        self.assertEqual(
            registry.component_content("command", "chat"),
            "Updated body.",
        )

    def test_github_duplicate_skill_is_ignored(self):
        github_skill = self.root / ".github" / "skills" / "sample-skill"
        github_skill.mkdir(parents=True)
        (github_skill / "SKILL.md").write_text(
            "---\nname: sample-skill\ndescription: GitHub definition.\n---\n# GitHub skill\n",
            encoding="utf-8",
        )

        registry = ExtensionRegistry(self.config_root, project_root=self.root)

        self.assertEqual(
            registry.skills["sample-skill"]["source_root"],
            str(self.skills_root.resolve()),
        )
        self.assertIn(
            "# Sample",
            registry.component_content("skill", "sample-skill"),
        )

    def test_duplicate_stock_skill_ids_raise_an_error(self):
        skills_root = self.root / "stock-server" / ".stock" / "skills"
        for directory in ("first", "second"):
            skill_dir = skills_root / directory
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: duplicate-skill\ndescription: Duplicate.\n---\n",
                encoding="utf-8",
            )

        with self.assertRaisesRegex(ValueError, "Duplicate skill id 'duplicate-skill'"):
            ExtensionRegistry(self.config_root, project_root=self.root)

    def test_cursor_files_are_not_loaded_or_validated(self):
        cursor_root = self.root / ".cursor"
        (cursor_root / "agents").mkdir(parents=True)
        (cursor_root / "agents" / "invalid.md").write_text(
            "---\nname: bad agent\nskills: invalid\n---\n",
            encoding="utf-8",
        )
        (cursor_root / "mcp-configs").mkdir()
        (cursor_root / "mcp-configs" / "mcp-servers.json").write_text(
            "{ invalid json",
            encoding="utf-8",
        )

        registry = ExtensionRegistry(self.config_root, project_root=self.root)

        self.assertNotIn("bad-agent", registry.agents)
        self.assertNotIn("invalid", registry.mcp_servers)

    def test_runtime_config_resolves_only_server_registered_components(self):
        registry = ExtensionRegistry(self.config_root, project_root=self.root)
        agent, command = registry.resolve("assistant", "chat")

        runtime = registry.agent_runtime_config(
            agent,
            command,
            "Base instructions.",
            skill_ids=[],
            mcp_server_ids=[],
        )

        self.assertEqual(runtime["skill_dirs"], [])
        self.assertEqual(runtime["selected_skill_ids"], [])
        self.assertEqual(runtime["selected_skill_directories"], {})
        self.assertEqual(runtime["mcp_servers"], [])
        self.assertIn("Base instructions.", runtime["system_prompt"])
        self.assertIn("Answer clearly.", registry.command_input(command, "hello"))

    def test_command_input_composes_nested_command_instructions(self):
        registry = ExtensionRegistry(self.config_root, project_root=self.root)
        root_command = {
            "id": "root",
            "name": "/root",
            "prompt": "Root task: $ARGUMENTS",
            "instruction": None,
        }
        nested_command = {
            "id": "nested",
            "name": "/nested",
            "prompt": "Nested task: $ARGUMENTS",
            "instruction": None,
        }

        result = registry.command_input(
            root_command,
            "analyze TSLA",
            additional_commands=[nested_command],
        )

        self.assertIn("Root task: analyze TSLA", result)
        self.assertIn("Nested task: analyze TSLA", result)

    def test_stock_root_uses_the_mcp_config_path(self):
        mcp_config_dir = self.root / "stock-server" / ".stock" / "mcp-configs"
        mcp_config_dir.mkdir(parents=True, exist_ok=True)
        (mcp_config_dir / "mcp-servers.json").write_text(
            json.dumps({
                "mcpServers": {
                    "local-tools": {"command": "node"},
                    "cursor-tools": {
                        "command": "node",
                        "args": ["server.js"],
                        "env": {"API_KEY": "${CURSOR_TOOLS_KEY}"},
                    },
                    "remote-tools": {
                        "type": "http",
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": "Bearer token"},
                    }
                }
            }),
            encoding="utf-8",
        )
        legacy_mcp_dir = self.root / "stock-server" / ".stock" / "mcp"
        legacy_mcp_dir.mkdir()
        (legacy_mcp_dir / "legacy-tools.json").write_text(
            json.dumps({
                "servers": [
                    {"id": "legacy-tools", "name": "Legacy tools", "command": "node"}
                ]
            }),
            encoding="utf-8",
        )

        registry = ExtensionRegistry(self.config_root, project_root=self.root)

        self.assertEqual(registry.mcp_servers["cursor-tools"]["name"], "cursor-tools")
        self.assertEqual(registry.mcp_servers["cursor-tools"]["command"], "node")
        self.assertEqual(registry.mcp_servers["cursor-tools"]["args"], ["server.js"])
        self.assertEqual(registry.mcp_servers["remote-tools"]["url"], "https://example.test/mcp")
        self.assertNotIn("legacy-tools", registry.mcp_servers)

    def test_http_mcp_server_uses_streamable_http_transport(self):
        observed = {}

        async def fake_load_mcp_tools(session, *, connection, server_name):
            observed["connection"] = connection
            observed["server_name"] = server_name
            return []

        with patch("src.mcp.client.load_mcp_tools", side_effect=fake_load_mcp_tools):
            load_mcp_tools_for_agent(
                [{
                    "id": "remote-tools",
                    "type": "http",
                    "url": "https://example.test/mcp",
                    "headers": {"Authorization": "Bearer token"},
                }]
            )

        self.assertEqual(observed["server_name"], "remote-tools")
        self.assertEqual(
            observed["connection"],
            {
                "transport": "streamable_http",
                "url": "https://example.test/mcp",
                "headers": {"Authorization": "Bearer token"},
            },
        )

    def test_unknown_agent_is_rejected(self):
        registry = ExtensionRegistry(self.config_root, project_root=self.root)

        with self.assertRaises(KeyError):
            registry.resolve("not-registered", "chat")

    def test_unknown_skill_reference_fails_registry_initialization(self):
        (self.root / "stock-server" / ".stock" / "agents" / "assistant.md").write_text(
            "---\nname: assistant\ndisplay_name: Assistant\n"
            "skills: [missing]\n---\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "unknown skill"):
            ExtensionRegistry(self.config_root, project_root=self.root)

    def test_registered_hooks_are_validated_and_listed_without_command_details(self):
        hooks_dir = self.root / "stock-server" / ".stock" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "hooks.json").write_text(
            json.dumps({
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": 'tool == "Bash"',
                            "hooks": [
                                {"type": "command", "command": "python -c \"print(1)\""}
                            ],
                            "description": "Audit chat",
                        }
                    ]
                }
            }),
            encoding="utf-8",
        )
        (hooks_dir / "audit.json").write_text(
            json.dumps({
                "hooks": [
                    {
                        "id": "legacy-audit",
                        "name": "Legacy audit",
                        "event": "before_agent",
                        "command": ["python", "scripts/audit.py"],
                    }
                ]
            }),
            encoding="utf-8",
        )

        registry = ExtensionRegistry(self.config_root, project_root=self.root)

        hook_id = "stock-pretooluse-0-0"
        self.assertEqual(list(registry.hooks), [hook_id])
        self.assertEqual(registry.hooks[hook_id]["event"], "PreToolUse")
        self.assertEqual(
            registry.catalog()["hooks"],
            [{"id": hook_id, "name": "Audit chat", "event": "PreToolUse", "enabled": True}],
        )

    def test_native_hook_rejects_unsupported_matcher_expression(self):
        hooks_dir = self.root / "stock-server" / ".stock" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "hooks.json").write_text(
            json.dumps({
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": 'tool != "Bash"',
                            "hooks": [
                                {"type": "command", "command": "python -c \"print(1)\""}
                            ],
                        }
                    ]
                }
            }),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "Invalid hook matcher"):
            ExtensionRegistry(self.config_root, project_root=self.root)

    def test_native_hooks_json_registers_tool_events_and_matchers(self):
        hooks_dir = self.root / "stock-server" / ".stock" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "hooks.json").write_text(
            json.dumps({
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": 'tool == "Bash" && tool_input.command matches "git push"',
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f'"{sys.executable}" -c "print(1)"',
                                }
                            ],
                            "description": "Check pushes",
                        }
                    ]
                }
            }),
            encoding="utf-8",
        )

        registry = ExtensionRegistry(self.config_root, project_root=self.root)

        hook = next(hook for hook in registry.hooks.values() if hook["event"] == "PreToolUse")
        self.assertEqual(hook["matcher"], 'tool == "Bash" && tool_input.command matches "git push"')
        self.assertEqual(hook["command"], f'"{sys.executable}" -c "print(1)"')
        self.assertTrue(hook["shell"])
        self.assertEqual(hook["name"], "Check pushes")

    def test_hook_runner_applies_native_matcher_before_running_command(self):
        marker = self.root / "matched-hook.txt"
        script = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('ran')"
        runner = HookRunner(
            {
                "block-push": {
                    "event": "PreToolUse",
                    "matcher": (
                        r'tool == "Write" && tool_input.file_path matches "\\.(md|txt)$" '
                        r'&& !(tool_input.file_path matches "README\\.md|CLAUDE\\.md")'
                    ),
                    "command": [sys.executable, "-c", script, str(marker)],
                }
            },
            str(self.root),
        )

        runner.run(
            "PreToolUse",
            {"tool": "Write", "tool_input": {"file_path": "README.md"}},
            agent_id="assistant",
            command_id=None,
        )
        self.assertFalse(marker.exists())

        runner.run(
            "PreToolUse",
            {"tool": "Write", "tool_input": {"file_path": "notes.txt"}},
            agent_id="assistant",
            command_id=None,
        )
        self.assertEqual(marker.read_text(encoding="utf-8"), "ran")

    def test_hook_runner_executes_native_shell_command(self):
        marker = self.root / "native-shell-hook.txt"
        script_path = self.root / "write_marker.py"
        script_path.write_text(
            "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('ran')",
            encoding="utf-8",
        )
        runner = HookRunner(
            {
                "native-shell": {
                    "event": "PreToolUse",
                    "matcher": "*",
                    "command": f'"{sys.executable}" "{script_path}" "{marker}"',
                    "shell": True,
                }
            },
            str(self.root),
        )

        runner.run(
            "PreToolUse",
            {"tool": "Bash", "tool_input": {"command": "git status"}},
            agent_id="assistant",
            command_id=None,
        )

        self.assertEqual(marker.read_text(encoding="utf-8"), "ran")

    def test_native_hook_shell_receives_stock_plugin_root_environment(self):
        runner = HookRunner(
            {
                "plugin-hook": {
                    "event": "PreToolUse",
                    "command": 'node "${STOCK_PLUGIN_ROOT}/stock-server/.stock/hooks/scripts/hooks/test.js"',
                    "shell": True,
                }
            },
            str(self.root),
        )
        result = type(
            "ProcessResult",
            (),
            {"returncode": 0, "stdout": b"", "stderr": b""},
        )()

        with patch.dict(
            os.environ,
            {"STOCK_PLUGIN_ROOT": str(self.root / "wrong-plugin")},
        ), patch("src.services.hook_runner.subprocess.run", return_value=result) as run:
            runner.run(
                "PreToolUse",
                {"tool": "Bash", "tool_input": {"command": "git status"}},
                agent_id="assistant",
                command_id=None,
            )

        self.assertEqual(run.call_args.kwargs["env"]["STOCK_PLUGIN_ROOT"], str(self.root))
        if os.name == "nt":
            self.assertIn("%STOCK_PLUGIN_ROOT%", run.call_args.args[0])

    def test_hook_tool_middleware_fires_around_agent_tool_calls(self):
        events = []
        contexts = []
        middleware = HookToolMiddleware({}, str(self.root))
        middleware.hook_runner.run = lambda event, context, **kwargs: (
            events.append((event, context["tool"])),
            contexts.append(context),
        )
        request = type(
            "ToolRequest",
            (),
            {"tool_call": {"name": "execute", "args": {"command": "npm run dev"}}},
        )()

        result = middleware.wrap_tool_call(
            request,
            lambda tool_request: events.append(("tool", tool_request.tool_call["name"])) or "done",
        )

        self.assertEqual(result, "done")
        self.assertEqual(
            events,
            [("PreToolUse", "Bash"), ("tool", "execute"), ("PostToolUse", "Bash")],
        )
        self.assertEqual(contexts[1]["tool_output"], {"output": "done"})

    def test_tool_middleware_logs_the_skill_file_being_read(self):
        middleware = HookToolMiddleware({}, str(self.root))
        middleware.hook_runner.run = lambda *args, **kwargs: None
        request = type(
            "ToolRequest",
            (),
            {
                "tool_call": {
                    "name": "read_file",
                    "args": {"file_path": ".stock/skills/stock-analysis/SKILL.md"},
                }
            },
        )()

        with self.assertLogs("src.services.hook_runner", level="INFO") as captured:
            middleware.wrap_tool_call(request, lambda tool_request: "content")

        self.assertTrue(
            any("skill load skill=stock-analysis" in line for line in captured.output)
        )

    def test_tool_middleware_logs_the_mcp_server_of_a_tool_call(self):
        middleware = HookToolMiddleware({}, str(self.root))
        middleware.hook_runner.run = lambda *args, **kwargs: None
        tool = type("Tool", (), {"metadata": {"mcp_server": "market-data"}})()
        request = type(
            "ToolRequest",
            (),
            {"tool_call": {"name": "quote", "args": {"symbol": "TSLA"}}, "tool": tool},
        )()

        with self.assertLogs("src.services.hook_runner", level="INFO") as captured:
            middleware.wrap_tool_call(request, lambda tool_request: "42")

        self.assertTrue(any("mcp_server=market-data" in line for line in captured.output))

    def test_pre_tool_hook_failure_prevents_tool_execution(self):
        middleware = HookToolMiddleware(
            {
                "block-tool": {
                    "event": "PreToolUse",
                    "matcher": '*',
                    "command": [sys.executable, "-c", "raise SystemExit(1)"],
                }
            },
            str(self.root),
        )
        request = type(
            "ToolRequest",
            (),
            {"tool_call": {"name": "execute", "args": {"command": "npm run dev"}}},
        )()
        executed = []

        with self.assertRaises(HookExecutionError):
            middleware.wrap_tool_call(request, lambda tool_request: executed.append(True))

        self.assertEqual(executed, [])

    def test_post_tool_hook_failure_returns_a_tool_error_instead_of_raising(self):
        middleware = HookToolMiddleware(
            {
                "reject-output": {
                    "event": "PostToolUse",
                    "matcher": "*",
                    "command": [sys.executable, "-c", "raise SystemExit(1)"],
                }
            },
            str(self.root),
        )
        request = type(
            "ToolRequest",
            (),
            {
                "tool_call": {
                    "id": "call-1",
                    "name": "execute",
                    "args": {"command": "python stock_data_fetcher.py --stocks TSLA"},
                }
            },
        )()

        result = middleware.wrap_tool_call(request, lambda tool_request: "payload")

        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.tool_call_id, "call-1")
        self.assertIn("rejected by a post-tool hook", result.content)

    def test_hook_runner_passes_json_context_and_uses_registered_filters(self):
        output_path = self.root / "hook-context.json"
        script = (
            "import json, pathlib, sys; "
            "pathlib.Path(sys.argv[1]).write_text(json.dumps(json.load(sys.stdin)))"
        )
        runner = HookRunner(
            {
                "audit-chat": {
                    "event": "after_agent",
                    "command": [sys.executable, "-c", script, str(output_path)],
                    "agent_ids": ["assistant"],
                    "command_ids": ["chat"],
                    "timeout_seconds": 5,
                }
            },
            str(self.root),
        )

        runner.run(
            "after_agent",
            {"question": "hello", "answer": "world"},
            agent_id="assistant",
            command_id="chat",
        )

        self.assertEqual(
            json.loads(output_path.read_text(encoding="utf-8")),
            {"question": "hello", "answer": "world", "event": "after_agent"},
        )

    def test_hook_runner_surfaces_nonzero_exit(self):
        runner = HookRunner(
            {
                "failing-hook": {
                    "event": "before_agent",
                    "command": [sys.executable, "-c", "raise SystemExit(3)"],
                }
            },
            str(self.root),
        )

        with self.assertRaisesRegex(HookExecutionError, "exited with code 3"):
            runner.run(
                "before_agent",
                {},
                agent_id="assistant",
                command_id="chat",
            )

    def test_discovers_only_stock_definitions(self):
        stock_root = self.root / "stock-server" / ".stock"
        agents_dir = stock_root / "agents"
        commands_dir = stock_root / "commands" / "workflows"
        skill_dir = stock_root / "skills" / "group" / "nested"
        github_root = self.root / ".github"
        cursor_root = self.root / ".cursor"
        agents_dir.mkdir(parents=True, exist_ok=True)
        commands_dir.mkdir(parents=True)
        skill_dir.mkdir(parents=True)
        (github_root / "agents").mkdir(parents=True, exist_ok=True)
        (github_root / "commands").mkdir(parents=True)
        (stock_root / "hooks").mkdir(parents=True)
        (cursor_root / "agents").mkdir(parents=True)
        (cursor_root / "commands").mkdir(parents=True)
        (agents_dir / "helper.md").write_text(
            "---\nname: helper-agent\ndescription: A helper agent.\nskills:\n  - group/nested\n---\nHelp with focused tasks.\n",
            encoding="utf-8",
        )
        (commands_dir / "run.md").write_text(
            "---\ndescription: Run a workflow.\nargument-hint: [topic]\n---\nWork on $ARGUMENTS.\n",
            encoding="utf-8",
        )
        (skill_dir / "SKILL.md").write_text(
            "---\nname: nested-skill\ndescription: A nested skill.\n---\n# Skill\n",
            encoding="utf-8",
        )
        (github_root / "agents" / "github-helper.md").write_text(
            "---\nname: github-helper\n---\nGitHub helper.\n",
            encoding="utf-8",
        )
        (github_root / "commands" / "github-task.md").write_text(
            "---\ndescription: GitHub command.\n---\nRun the GitHub task.\n",
            encoding="utf-8",
        )
        (cursor_root / "agents" / "cursor-helper.md").write_text(
            "---\nname: cursor-helper\n---\nCursor helper.\n",
            encoding="utf-8",
        )
        (cursor_root / "commands" / "cursor-task.md").write_text(
            "---\ndescription: Cursor command.\n---\nRun the Cursor task.\n",
            encoding="utf-8",
        )
        mcp_config_dir = stock_root / "mcp-configs"
        mcp_config_dir.mkdir(parents=True, exist_ok=True)
        (mcp_config_dir / "mcp-servers.json").write_text(
            json.dumps({
                "mcpServers": {
                    "local-tools": {"command": "node"},
                    "project-tools": {"command": "node"},
                }
            }),
            encoding="utf-8",
        )
        (stock_root / "hooks" / "hooks.json").write_text(
            json.dumps({
                "hooks": {
                    "PreToolUse": [],
                    "SessionStart": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {"type": "command", "command": "python -c \"print(1)\""}
                            ],
                        }
                    ],
                }
            }),
            encoding="utf-8",
        )
        external_root = self.root / "external"
        (external_root / "agents").mkdir(parents=True)
        (external_root / "agents" / "external.md").write_text(
            "---\nname: external-agent\n---\nMust not be discovered.\n",
            encoding="utf-8",
        )
        self.write_config(
            "extension_sources.json",
            {"claude_config_dirs": [str(external_root)]},
        )

        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(external_root)}):
            registry = ExtensionRegistry(self.config_root, project_root=self.root)

        agent, command = registry.resolve("helper-agent", "workflows/run")
        runtime = registry.agent_runtime_config(agent, command)

        self.assertEqual(
            registry.component_content("agent", "helper-agent"),
            "Help with focused tasks.",
        )
        self.assertEqual(command["name"], "/workflows/run")
        self.assertIn("Work on Analyze the project.", registry.command_input(command, "Analyze the project."))
        self.assertIn(str((stock_root / "skills" / "group").resolve()), runtime["skill_dirs"])
        self.assertEqual(runtime["project_root"], str(self.root))
        self.assertIn("task", runtime["system_prompt"])
        self.assertNotIn("github-helper", registry.agents)
        self.assertNotIn("cursor-helper", registry.agents)
        self.assertNotIn("github-task", registry.commands)
        self.assertNotIn("cursor-task", registry.commands)
        self.assertIn("project-tools", registry.mcp_servers)
        self.assertIn("SessionStart", {hook["event"] for hook in registry.hooks.values()})
        self.assertNotIn("external-agent", registry.agents)

    def test_default_registry_scans_project_root_extensions(self):
        registry = ExtensionRegistry()

        catalog = registry.catalog()

        self.assertIn("stock-analysis", [skill["id"] for skill in catalog["skills"]])
        self.assertEqual(
            [agent["id"] for agent in catalog["agents"]],
            ["stock-analyst"],
        )
        self.assertEqual(
            [command["id"] for command in catalog["commands"]],
            ["analyze-stock"],
        )
        self.assertEqual(catalog["commands"][0]["name"], "/analyze-stock")
        self.assertIn("github", registry.mcp_servers)
        self.assertEqual(len(registry.hooks), 1)
        stock_data_hook = next(iter(registry.hooks.values()))
        self.assertEqual(stock_data_hook["event"], "PostToolUse")
        self.assertTrue(matches_hook(
            stock_data_hook["matcher"],
            {
                "tool": "Bash",
                "tool_input": {
                    "command": "python /tmp/stock_data_fetcher.py --stocks TSLA"
                },
            },
        ))
        self.assertFalse(matches_hook(
            stock_data_hook["matcher"],
            {"tool": "Bash", "tool_input": {"command": "python some_other_script.py"}},
        ))

    def test_stock_analyst_workspace_is_limited_to_skill_files(self):
        agent = extension_registry.agents["stock-analyst"]

        runtime = extension_registry.agent_runtime_config(agent, None)

        self.assertEqual(
            runtime["project_root"],
            str(
                (
                    Path(extension_registry.project_root)
                    / "stock-server"
                    / ".stock"
                    / "skills"
                ).resolve()
            ),
        )

    def test_agent_workspace_root_cannot_escape_project_root(self):
        agent_file = self.root / "stock-server" / ".stock" / "agents" / "assistant.md"
        agent_file.write_text(
            "---\nname: assistant\nworkspace_root: ..\n---\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "workspace_root"):
            ExtensionRegistry(self.config_root, project_root=self.root)

    def test_chat_works_without_project_commands(self):
        (self.root / "stock-server" / ".stock" / "commands" / "chat.md").unlink()
        registry = ExtensionRegistry(self.config_root, project_root=self.root)

        agent, command = registry.resolve("assistant", None)

        self.assertIsNone(command)
        self.assertEqual(registry.command_input(command, "hello"), "hello")
        self.assertEqual(
            registry.agent_runtime_config(agent, command)["project_root"],
            str(self.root),
        )

    def test_api_catalog_returns_registered_display_options(self):
        response = agent_catalog()

        self.assertEqual(response["code"], 0)
        self.assertEqual(
            [agent["name"] for agent in response["data"]["agents"]],
            ["Stock Analyst"],
        )

    def test_api_routes_question_to_registered_components(self):
        request = AgentChatRequest(question="分析 TSLA")
        selection = ComponentSelection(
            agent_id="stock-analyst",
            command_id=None,
            skill_ids=["stock-analysis"],
            mcp_server_ids=[],
        )

        resolved = ResolvedComponents(
            command_ids=[],
            skill_ids=["stock-analysis"],
            mcp_server_ids=[],
            referenced_agent_ids=[],
        )
        with patch("src.api.chat.select_components", return_value=selection), patch(
            "src.api.chat.resolve_component_dependencies",
            return_value=resolved,
        ), patch("src.api.chat.make_llm", return_value=object()
        ), patch("src.api.chat.ask_agent", return_value="分析完成") as ask_agent, patch(
            "src.api.chat.hook_runner.run"
        ) as run_hook:
            response = agent_chat(request)

        self.assertEqual(response["data"]["answer"], "分析完成")
        self.assertEqual(response["data"]["agent_id"], "stock-analyst")
        self.assertIsNone(response["data"]["command_id"])
        self.assertEqual(
            response["data"]["skills"],
            [{
                "id": "stock-analysis",
                "name": extension_registry.skills["stock-analysis"]["name"],
            }],
        )
        self.assertEqual(ask_agent.call_args.args[0], "分析 TSLA")
        self.assertTrue(
            ask_agent.call_args.kwargs["config"].skill_dirs[0].endswith(
                os.path.join(".stock", "skills")
            )
        )
        self.assertEqual(
            ask_agent.call_args.kwargs["config"].selected_skill_ids,
            ["stock-analysis"],
        )
        self.assertEqual(
            ask_agent.call_args.kwargs["config"].selected_skill_directories,
            {
                str((Path(extension_registry.project_root) / "stock-server" / ".stock" / "skills").resolve()):
                ["stock-analysis"]
            },
        )
        self.assertEqual(ask_agent.call_args.kwargs["config"].mcp_servers, [])
        self.assertIn(
            "stock-analysis",
            ask_agent.call_args.kwargs["config"].system_prompt,
        )
        self.assertEqual(
            ask_agent.call_args.kwargs["config"].project_root,
            str(
                (
                    Path(extension_registry.project_root)
                    / "stock-server"
                    / ".stock"
                    / "skills"
                ).resolve()
            ),
        )
        self.assertEqual(
            [call.args[0] for call in run_hook.call_args_list],
            ["before_agent", "after_agent"],
        )

    def test_stock_analysis_command_loads_skill_and_passes_user_request(self):
        request = AgentChatRequest(question="/analyze-stock TSLA")
        selection = ComponentSelection(
            agent_id="stock-analyst",
            command_id="analyze-stock",
            skill_ids=[],
            mcp_server_ids=[],
        )
        resolved = ResolvedComponents(
            command_ids=["analyze-stock"],
            skill_ids=["stock-analysis"],
            mcp_server_ids=[],
            referenced_agent_ids=[],
        )

        with patch("src.api.chat.select_components", return_value=selection), patch(
            "src.api.chat.resolve_component_dependencies",
            return_value=resolved,
        ), patch("src.api.chat.make_llm", return_value=object()), patch(
            "src.api.chat.ask_agent", return_value="分析完成"
        ) as ask_agent, patch("src.api.chat.hook_runner.run"):
            response = agent_chat(request)

        self.assertEqual(response["data"]["command_id"], "analyze-stock")
        self.assertEqual(response["data"]["skills"][0]["id"], "stock-analysis")
        self.assertIn("@skill:stock-analysis", ask_agent.call_args.args[0])
        self.assertIn("/analyze-stock TSLA", ask_agent.call_args.args[0])
        self.assertEqual(
            ask_agent.call_args.kwargs["config"].selected_skill_ids,
            ["stock-analysis"],
        )

    def test_api_answers_greeting_without_routing_or_inspecting_project(self):
        request = AgentChatRequest(question=" 你好。 ", thread_id="greeting-thread")

        with patch("src.api.chat.select_components") as select_components, patch(
            "src.api.chat.make_llm"
        ) as make_llm, patch("src.api.chat.ask_agent") as ask_agent, patch(
            "src.api.chat.hook_runner.run"
        ) as run_hook:
            response = agent_chat(request)

        self.assertEqual(response["data"]["answer"], "你好！有什么我可以帮你？")
        self.assertIsNone(response["data"]["agent_id"])
        self.assertEqual(response["data"]["thread_id"], "greeting-thread")
        select_components.assert_not_called()
        make_llm.assert_not_called()
        ask_agent.assert_not_called()
        run_hook.assert_not_called()

    def test_api_runs_error_hook_when_agent_fails(self):
        request = AgentChatRequest(question="hello, help me debug a failing endpoint")
        selection = ComponentSelection(
            agent_id="stock-analyst",
            command_id=None,
            skill_ids=[],
            mcp_server_ids=[],
        )

        resolved = ResolvedComponents(
            command_ids=[],
            skill_ids=[],
            mcp_server_ids=[],
            referenced_agent_ids=[],
        )
        with patch("src.api.chat.select_components", return_value=selection), patch(
            "src.api.chat.resolve_component_dependencies",
            return_value=resolved,
        ), patch("src.api.chat.make_llm", return_value=object()
        ), patch("src.api.chat.ask_agent", side_effect=RuntimeError("agent failed")), patch(
            "src.api.chat.hook_runner.run"
        ) as run_hook:
            with self.assertRaisesRegex(RuntimeError, "agent failed"):
                agent_chat(request)

        self.assertEqual(
            [call.args[0] for call in run_hook.call_args_list],
            ["before_agent", "on_error"],
        )

    def test_api_surfaces_component_routing_failure(self):
        request = AgentChatRequest(question="hello, help me debug a failing endpoint")

        with patch(
            "src.api.chat.select_components",
            side_effect=ValueError("invalid router response"),
        ), patch("src.api.chat.make_llm", return_value=object()):
            with self.assertRaises(HTTPException) as error:
                agent_chat(request)

        self.assertEqual(error.exception.status_code, 502)
        self.assertIn("Component routing failed", error.exception.detail)

    def test_chat_request_rejects_user_supplied_component_ids(self):
        for field in ("agent_id", "command_id"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                AgentChatRequest(question="hello", **{field: "stock-analyst"})

    def test_chat_requests_get_unique_thread_ids_by_default(self):
        first = AgentChatRequest(question="first")
        second = AgentChatRequest(question="second")

        self.assertNotEqual(first.thread_id, second.thread_id)


if __name__ == "__main__":
    unittest.main()
