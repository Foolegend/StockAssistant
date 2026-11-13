import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from src.api.chat import AgentChatRequest, agent_chat, agent_catalog, extension_registry
from src.services.extension_registry import ExtensionRegistry
from src.services.hook_runner import HookExecutionError, HookRunner


class ExtensionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_root = self.root / "config"
        self.config_root.mkdir(parents=True)
        self.skills_root = self.root / ".github" / "skills"
        self.skills_root.mkdir(parents=True)
        default_command = self.root / ".claude" / "commands" / "chat.md"
        default_command.parent.mkdir(parents=True)
        default_command.write_text(
            "---\nname: Chat\ndescription: General chat.\n---\nAnswer clearly.\n",
            encoding="utf-8",
        )
        agents_dir = self.root / ".github" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "assistant.md").write_text(
            "---\nname: assistant\ndisplay_name: Assistant\ndefault: true\n"
            "skills: [sample-skill]\nmcp_server_ids: [local-tools]\n---\n",
            encoding="utf-8",
        )
        mcp_dir = self.root / ".github" / "mcp"
        mcp_dir.mkdir(parents=True)
        (mcp_dir / "local-tools.json").write_text(
            json.dumps({
                "servers": [
                    {
                        "id": "local-tools",
                        "name": "Local tools",
                        "command": "node",
                        "args": ["server.js"],
                        "env": {"API_KEY": "${LOCAL_TOOLS_KEY}"},
                    }
                ]
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

        self.assertEqual(catalog["default_agent_id"], "assistant")
        self.assertEqual(catalog["default_command_id"], "chat")
        self.assertEqual(catalog["agents"][0]["name"], "Assistant")
        self.assertEqual(catalog["agents"][0]["skills"][0]["id"], "sample-skill")
        self.assertEqual(catalog["commands"][0]["name"], "Chat")
        self.assertEqual(catalog["mcp_servers"][0]["id"], "local-tools")
        self.assertEqual(catalog["skills"][0]["id"], "sample-skill")

    def test_runtime_config_resolves_only_server_registered_components(self):
        registry = ExtensionRegistry(self.config_root, project_root=self.root)
        agent, command = registry.resolve(None, None)

        runtime = registry.agent_runtime_config(agent, command, "Base instructions.")

        self.assertEqual(runtime["skill_dirs"], [str(self.skills_root.resolve())])
        self.assertEqual(runtime["mcp_servers"][0]["id"], "local-tools")
        self.assertIn("Base instructions.", runtime["system_prompt"])
        self.assertIn("Answer clearly.", registry.command_input(command, "hello"))

    def test_unknown_agent_is_rejected(self):
        registry = ExtensionRegistry(self.config_root, project_root=self.root)

        with self.assertRaises(KeyError):
            registry.resolve("not-registered", "chat")

    def test_unknown_skill_reference_fails_registry_initialization(self):
        (self.root / ".github" / "agents" / "assistant.md").write_text(
            "---\nname: assistant\ndisplay_name: Assistant\ndefault: true\n"
            "skills: [missing]\n---\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "unknown skill"):
            ExtensionRegistry(self.config_root, project_root=self.root)

    def test_registered_hooks_are_validated_and_listed_without_command_details(self):
        hooks_dir = self.root / ".github" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "audit.json").write_text(
            json.dumps({
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
            }),
            encoding="utf-8",
        )

        registry = ExtensionRegistry(self.config_root, project_root=self.root)

        self.assertEqual(registry.hooks["audit-chat"]["event"], "before_agent")
        self.assertEqual(
            registry.catalog()["hooks"],
            [{"id": "audit-chat", "name": "Audit chat", "event": "before_agent", "enabled": True}],
        )

    def test_hook_rejects_unknown_agent_reference(self):
        hooks_dir = self.root / ".cursor" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "audit.json").write_text(
            json.dumps({
                "hooks": [
                    {
                        "id": "audit-chat",
                        "name": "Audit chat",
                        "event": "before_agent",
                        "command": ["python", "scripts/audit.py"],
                        "agent_ids": ["missing"],
                    }
                ]
            }),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "unknown agent_id"):
            ExtensionRegistry(self.config_root, project_root=self.root)

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

    def test_discovers_project_definitions_across_supported_roots(self):
        claude_root = self.root / ".claude"
        agents_dir = claude_root / "agents"
        commands_dir = claude_root / "commands" / "workflows"
        skill_dir = claude_root / "skills" / "group" / "nested"
        github_root = self.root / ".github"
        cursor_root = self.root / ".cursor"
        agents_dir.mkdir(parents=True)
        commands_dir.mkdir(parents=True)
        skill_dir.mkdir(parents=True)
        (github_root / "agents").mkdir(parents=True, exist_ok=True)
        (github_root / "commands").mkdir(parents=True)
        (github_root / "mcp").mkdir(parents=True, exist_ok=True)
        (claude_root / "hooks").mkdir(parents=True)
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
        (github_root / "mcp" / "tools.json").write_text(
            json.dumps({
                "servers": [
                    {"id": "project-tools", "name": "Project tools", "command": "node"}
                ]
            }),
            encoding="utf-8",
        )
        (claude_root / "hooks" / "audit.json").write_text(
            json.dumps({
                "hooks": [
                    {
                        "id": "project-audit",
                        "name": "Project audit",
                        "event": "before_agent",
                        "command": ["python", "scripts/audit.py"],
                    }
                ]
            }),
            encoding="utf-8",
        )
        (claude_root / "hooks" / "hooks.json").write_text(
            json.dumps({"hooks": {"PreToolUse": []}}),
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

        self.assertEqual(agent["prompt"], "Help with focused tasks.")
        self.assertEqual(command["name"], "/workflows/run")
        self.assertIn("Work on Analyze the project.", registry.command_input(command, "Analyze the project."))
        self.assertIn(str((claude_root / "skills" / "group").resolve()), runtime["skill_dirs"])
        self.assertEqual(runtime["project_root"], str(self.root))
        self.assertIn("task", runtime["system_prompt"])
        self.assertIn("github-helper", registry.agents)
        self.assertIn("cursor-helper", registry.agents)
        self.assertIn("github-task", registry.commands)
        self.assertIn("cursor-task", registry.commands)
        self.assertIn("project-tools", registry.mcp_servers)
        self.assertIn("project-audit", registry.hooks)
        self.assertNotIn("external-agent", registry.agents)

    def test_default_registry_scans_project_root_skills(self):
        registry = ExtensionRegistry()

        catalog = registry.catalog()

        self.assertIn("stock-analysis", [skill["id"] for skill in catalog["skills"]])
        self.assertIn("stock-assistant", [agent["id"] for agent in catalog["agents"]])

    def test_chat_works_without_project_commands(self):
        (self.root / ".claude" / "commands" / "chat.md").unlink()
        registry = ExtensionRegistry(self.config_root, project_root=self.root)

        agent, command = registry.resolve(None, None)

        self.assertIsNone(registry.default_command_id)
        self.assertIsNone(command)
        self.assertEqual(registry.command_input(command, "hello"), "hello")
        self.assertEqual(
            registry.agent_runtime_config(agent, command)["project_root"],
            str(self.root),
        )

    def test_api_catalog_returns_registered_display_options(self):
        response = agent_catalog()

        self.assertEqual(response["code"], 0)
        self.assertIn("股票分析师", [agent["name"] for agent in response["data"]["agents"]])

    def test_api_uses_registered_agent_and_command(self):
        request = AgentChatRequest(
            question="检查构建",
            agent_id="stock-analyst",
            command_id=extension_registry.default_command_id,
        )

        with patch("src.api.chat.ask_agent", return_value="分析完成") as ask_agent, patch(
            "src.api.chat.hook_runner.run"
        ) as run_hook:
            response = agent_chat(request)

        self.assertEqual(response["data"]["answer"], "分析完成")
        self.assertEqual(response["data"]["agent_id"], "stock-analyst")
        self.assertEqual(response["data"]["command_id"], extension_registry.default_command_id)
        self.assertTrue(ask_agent.call_args.kwargs["config"].skill_dirs[0].endswith(".github\\skills"))
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
