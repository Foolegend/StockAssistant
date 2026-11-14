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

    def test_missing_explicit_skill_directory_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = AgentConfig(skill_dirs=[str(Path(temp_dir) / "missing-skills")])
            with patch("src.agent.UnifiedAgent.make_llm"):
                with self.assertRaises(FileNotFoundError):
                    build_agent(config)


if __name__ == "__main__":
    unittest.main()
