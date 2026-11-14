import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agent.UnifiedAgent import AgentConfig, build_agent
from src.agent.selected_skills_middleware import SelectedSkillsMiddleware
from src.services.hook_runner import HookToolMiddleware


class UnifiedAgentTests(unittest.TestCase):
    def test_agent_config_accepts_model_and_components(self):
        skill_dir = Path(__file__).resolve().parents[2] / ".github" / "skills" / "stock-analysis"
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

    def test_missing_explicit_skill_directory_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = AgentConfig(skill_dirs=[str(Path(temp_dir) / "missing-skills")])
            with patch("src.agent.UnifiedAgent.make_llm"):
                with self.assertRaises(FileNotFoundError):
                    build_agent(config)


if __name__ == "__main__":
    unittest.main()
