import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.brain.UnifiedAgent import AgentConfig, build_agent


class UnifiedAgentTests(unittest.TestCase):
    def test_agent_config_accepts_model_and_components(self):
        config = AgentConfig(
            model_name="gpt-oss:120b-cloud",
            skill_dirs=["d:/project/StockAssistant/back/config/skills"],
            mcp_servers=["d:/project/StockAssistant/back/mcpServer/mcpServerExample.py"],
        )

        self.assertEqual(config.model_name, "gpt-oss:120b-cloud")
        self.assertTrue(config.skill_dirs[0].endswith("config/skills"))
        self.assertTrue(config.mcp_servers[0].endswith("mcpServerExample.py"))

        agent = build_agent(config)
        self.assertIsNotNone(agent)


if __name__ == "__main__":
    unittest.main()
