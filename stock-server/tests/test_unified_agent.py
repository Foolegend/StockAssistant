import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agent.UnifiedAgent import AgentConfig, build_agent


class UnifiedAgentTests(unittest.TestCase):
    def test_agent_config_accepts_model_and_components(self):
        skill_dir = Path(__file__).resolve().parents[1] / "config" / "skills" / "stock-analysis"
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
        config = AgentConfig(skill_dirs=[], mcp_servers=[])

        with patch("src.agent.UnifiedAgent.make_llm"), patch(
            "src.agent.UnifiedAgent.create_deep_agent"
        ) as create_deep_agent:
            build_agent(config)

        self.assertEqual(create_deep_agent.call_args.kwargs["skills"], [])

    def test_missing_explicit_skill_directory_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = AgentConfig(skill_dirs=[str(Path(temp_dir) / "missing-skills")])
            with patch("src.agent.UnifiedAgent.make_llm"):
                with self.assertRaises(FileNotFoundError):
                    build_agent(config)


if __name__ == "__main__":
    unittest.main()
