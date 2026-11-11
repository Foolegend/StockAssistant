import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from src.api.chat import AgentChatRequest, agent_chat, agent_catalog
from src.services.extension_registry import ExtensionRegistry
from src.services.hook_runner import HookExecutionError, HookRunner


class ExtensionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_root = self.root / "config"
        self.skills_root = self.config_root / "skills"
        self.skills_root.mkdir(parents=True)
        skill_dir = self.skills_root / "sample-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: sample-skill\ndescription: |\n  A test skill.\n  It has two lines.\n---\n# Sample\n",
            encoding="utf-8",
        )
        self.write_config(
            "agents.json",
            {
                "default_agent_id": "assistant",
                "agents": [
                    {
                        "id": "assistant",
                        "name": "Assistant",
                        "skill_ids": ["sample-skill"],
                        "mcp_server_ids": ["local-tools"],
                    }
                ],
            },
        )
        self.write_config(
            "commands.json",
            {
                "default_command_id": "chat",
                "commands": [{"id": "chat", "name": "Chat", "instruction": "Answer clearly."}],
            },
        )
        self.write_config(
            "mcp_servers.json",
            {
                "servers": [
                    {
                        "id": "local-tools",
                        "name": "Local tools",
                        "command": "node",
                        "args": ["server.js"],
                        "env": {"API_KEY": "${LOCAL_TOOLS_KEY}"},
                    }
                ]
            },
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_config(self, name, content):
        (self.config_root / name).write_text(json.dumps(content), encoding="utf-8")

    def test_catalog_uses_display_names_and_registered_ids(self):
        registry = ExtensionRegistry(self.config_root, self.skills_root)

        catalog = registry.catalog()

        self.assertEqual(catalog["default_agent_id"], "assistant")
        self.assertEqual(catalog["agents"][0]["name"], "Assistant")
        self.assertEqual(catalog["agents"][0]["skills"][0]["id"], "sample-skill")
        self.assertEqual(catalog["commands"][0]["name"], "Chat")
        self.assertEqual(catalog["mcp_servers"][0]["id"], "local-tools")
        self.assertEqual(catalog["skills"][0]["id"], "sample-skill")

    def test_runtime_config_resolves_only_server_registered_components(self):
        registry = ExtensionRegistry(self.config_root, self.skills_root)
        agent, command = registry.resolve(None, None)

        runtime = registry.agent_runtime_config(agent, command, "Base instructions.")

        self.assertEqual(runtime["skill_dirs"], [str(self.skills_root.resolve())])
        self.assertEqual(runtime["mcp_servers"][0]["id"], "local-tools")
        self.assertIn("Base instructions.", runtime["system_prompt"])
        self.assertIn("Answer clearly.", runtime["system_prompt"])

    def test_unknown_agent_is_rejected(self):
        registry = ExtensionRegistry(self.config_root, self.skills_root)

        with self.assertRaises(KeyError):
            registry.resolve("not-registered", "chat")

    def test_unknown_skill_reference_fails_registry_initialization(self):
        self.write_config(
            "agents.json",
            {
                "default_agent_id": "assistant",
                "agents": [
                    {
                        "id": "assistant",
                        "name": "Assistant",
                        "skill_ids": ["missing"],
                        "mcp_server_ids": [],
                    }
                ],
            },
        )

        with self.assertRaisesRegex(ValueError, "unknown skill"):
            ExtensionRegistry(self.config_root, self.skills_root)

    def test_registered_hooks_are_validated_and_listed_without_command_details(self):
        self.write_config(
            "hooks.json",
            {
                "hooks": [
                    {
                        "id": "audit-chat",
                        "name": "Audit chat",
                        "event": "before_agent",
                        "command": ["python", "scripts/audit.py"],
                        "agent_ids": ["assistant"],
                        "command_ids": ["chat"],
                    }
                ]
            },
        )

        registry = ExtensionRegistry(self.config_root, self.skills_root)

        self.assertEqual(registry.hooks["audit-chat"]["event"], "before_agent")
        self.assertEqual(
            registry.catalog()["hooks"],
            [{"id": "audit-chat", "name": "Audit chat", "event": "before_agent", "enabled": True}],
        )

    def test_hook_rejects_unknown_agent_reference(self):
        self.write_config(
            "hooks.json",
            {
                "hooks": [
                    {
                        "id": "audit-chat",
                        "name": "Audit chat",
                        "event": "before_agent",
                        "command": ["python", "scripts/audit.py"],
                        "agent_ids": ["missing"],
                    }
                ]
            },
        )

        with self.assertRaisesRegex(ValueError, "unknown agent_id"):
            ExtensionRegistry(self.config_root, self.skills_root)

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

    def test_discovers_claude_code_definitions_and_nested_skills(self):
        claude_root = self.root / "project" / ".claude"
        agents_dir = claude_root / "agents"
        commands_dir = claude_root / "commands" / "workflows"
        skill_dir = claude_root / "skills" / "group" / "nested"
        agents_dir.mkdir(parents=True)
        commands_dir.mkdir(parents=True)
        skill_dir.mkdir(parents=True)
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
        self.write_config(
            "extension_sources.json",
            {"claude_config_dirs": [str(claude_root)]},
        )

        registry = ExtensionRegistry(self.config_root, self.skills_root)

        agent, command = registry.resolve("helper-agent", "workflows/run")
        runtime = registry.agent_runtime_config(agent, command)

        self.assertEqual(agent["prompt"], "Help with focused tasks.")
        self.assertEqual(command["name"], "/workflows/run")
        self.assertIn("Work on Analyze the project.", registry.command_input(command, "Analyze the project."))
        self.assertIn(str((claude_root / "skills" / "group").resolve()), runtime["skill_dirs"])
        self.assertEqual(runtime["project_root"], str(claude_root.parent))
        self.assertIn("task", runtime["system_prompt"])

    def test_configured_claude_catalog_discovers_target_directory(self):
        registry = ExtensionRegistry()

        catalog = registry.catalog()

        self.assertIn("weather-agent", [agent["id"] for agent in catalog["agents"]])
        self.assertIn("time-command", [command["id"] for command in catalog["commands"]])
        self.assertIn("weather-fetcher", [
            skill["id"] for agent in catalog["agents"] for skill in agent["skills"]
        ])

    def test_api_catalog_returns_registered_display_options(self):
        response = agent_catalog()

        self.assertEqual(response["code"], 0)
        self.assertIn("股票分析师", [agent["name"] for agent in response["data"]["agents"]])

    def test_api_uses_registered_agent_and_command(self):
        request = AgentChatRequest(
            question="分析 TSLA",
            agent_id="stock-analyst",
            command_id="stock-analysis",
        )

        with patch("src.api.chat.ask_agent", return_value="分析完成") as ask_agent, patch(
            "src.api.chat.hook_runner.run"
        ) as run_hook:
            response = agent_chat(request)

        self.assertEqual(response["data"]["answer"], "分析完成")
        self.assertEqual(response["data"]["agent_id"], "stock-analyst")
        self.assertEqual(response["data"]["command_id"], "stock-analysis")
        self.assertTrue(ask_agent.call_args.kwargs["config"].skill_dirs[0].endswith("config\\skills"))
        self.assertEqual(
            [call.args[0] for call in run_hook.call_args_list],
            ["before_agent", "after_agent"],
        )

    def test_api_runs_error_hook_when_agent_fails(self):
        request = AgentChatRequest(question="hello")

        with patch("src.api.chat.ask_agent", side_effect=RuntimeError("agent failed")), patch(
            "src.api.chat.hook_runner.run"
        ) as run_hook:
            with self.assertRaisesRegex(RuntimeError, "agent failed"):
                agent_chat(request)

        self.assertEqual(
            [call.args[0] for call in run_hook.call_args_list],
            ["before_agent", "on_error"],
        )

    def test_api_rejects_unknown_agent(self):
        request = AgentChatRequest(question="hello", agent_id="not-registered")

        with self.assertRaises(HTTPException) as error:
            agent_chat(request)

        self.assertEqual(error.exception.status_code, 404)

    def test_chat_requests_get_unique_thread_ids_by_default(self):
        first = AgentChatRequest(question="first")
        second = AgentChatRequest(question="second")

        self.assertNotEqual(first.thread_id, second.thread_id)


if __name__ == "__main__":
    unittest.main()
