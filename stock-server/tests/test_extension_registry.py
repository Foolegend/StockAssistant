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

    def test_runtime_config_resolves_only_server_registered_components(self):
        registry = ExtensionRegistry(self.config_root, self.skills_root)
        agent, command = registry.resolve(None, None)

        runtime = registry.agent_runtime_config(agent, command, "Base instructions.")

        self.assertEqual(runtime["skill_dirs"], [str((self.skills_root / "sample-skill").resolve())])
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

        with patch("src.api.chat.ask_agent", return_value="分析完成") as ask_agent:
            response = agent_chat(request)

        self.assertEqual(response["data"]["answer"], "分析完成")
        self.assertEqual(response["data"]["agent_id"], "stock-analyst")
        self.assertEqual(response["data"]["command_id"], "stock-analysis")
        self.assertTrue(ask_agent.call_args.kwargs["config"].skill_dirs[0].endswith("stock-analysis"))

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
