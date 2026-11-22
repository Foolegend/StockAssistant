import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.slash_commands import SlashCommandError, plan_slash_invocation


class FakeRegistry:
    def __init__(self):
        self.commands = {
            "report": {
                "id": "report",
                "name": "/report",
                "enabled": True,
                "user_invocable": True,
                "agent_id": "analyst",
                "skill_ids": ["stock-analysis"],
                "mcp_server_ids": ["quant-tools"],
            },
            "reports:daily": {
                "id": "reports:daily",
                "name": "/reports:daily",
                "enabled": True,
                "user_invocable": True,
                "agent_id": None,
                "skill_ids": [],
                "mcp_server_ids": [],
            },
            "disabled": {
                "id": "disabled",
                "name": "/disabled",
                "enabled": False,
                "user_invocable": True,
                "agent_id": None,
                "skill_ids": [],
                "mcp_server_ids": [],
            },
        }
        self.skills = {
            "stock-analysis": {
                "id": "stock-analysis",
                "name": "stock-analysis",
                "enabled": True,
                "user_invocable": True,
            },
            "hidden-skill": {
                "id": "hidden-skill",
                "name": "hidden-skill",
                "enabled": True,
                "user_invocable": False,
            },
        }
        self.skill_aliases = {
            "stock-analysis": "stock-analysis",
            "hidden-skill": "hidden-skill",
        }

    def command_input(self, command, arguments):
        return f"## Command: {command['name']}\n\nBody: {arguments}"


class SlashCommandTests(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()

    def test_non_slash_question_is_not_an_invocation(self):
        self.assertIsNone(plan_slash_invocation("分析 TSLA", self.registry))

    def test_command_plan_expands_prompt_and_selection(self):
        plan = plan_slash_invocation("/report 分析 TSLA", self.registry)

        self.assertIsNotNone(plan)
        self.assertEqual(plan.invocation.kind, "command")
        self.assertEqual(plan.invocation.id, "report")
        self.assertEqual(plan.invocation.arguments, "分析 TSLA")
        self.assertEqual(plan.question, "## Command: /report\n\nBody: 分析 TSLA")
        self.assertEqual(plan.selection.agent_id, "analyst")
        self.assertEqual(plan.selection.skill_ids, ["stock-analysis"])
        self.assertEqual(plan.selection.mcp_server_ids, ["quant-tools"])
        self.assertEqual(plan.pinned_skills, ["stock-analysis"])
        self.assertEqual(plan.command["id"], "report")

    def test_namespaced_command_uses_colon(self):
        plan = plan_slash_invocation("/reports:daily", self.registry)

        self.assertIsNotNone(plan)
        self.assertEqual(plan.invocation.id, "reports:daily")
        self.assertEqual(plan.invocation.arguments, "")

    def test_skill_id_is_invocable(self):
        plan = plan_slash_invocation("/stock-analysis 分析 TSLA", self.registry)

        self.assertIsNotNone(plan)
        self.assertEqual(plan.invocation.kind, "skill")
        self.assertEqual(plan.selection.skill_ids, ["stock-analysis"])
        self.assertEqual(plan.question, "分析 TSLA")
        self.assertEqual(plan.pinned_skills, ["stock-analysis"])

    def test_skill_without_arguments_gets_default_question(self):
        plan = plan_slash_invocation("/stock-analysis", self.registry)

        self.assertIsNotNone(plan)
        self.assertIn("stock-analysis", plan.question)

    def test_unknown_command_lists_available_names(self):
        with self.assertRaisesRegex(SlashCommandError, "未知的斜杠命令"):
            plan_slash_invocation("/nope", self.registry)

    def test_malformed_slash_is_rejected(self):
        with self.assertRaisesRegex(SlashCommandError, "格式无效"):
            plan_slash_invocation("/", self.registry)

    def test_disabled_command_is_rejected(self):
        with self.assertRaisesRegex(SlashCommandError, "不可用"):
            plan_slash_invocation("/disabled", self.registry)

    def test_non_invocable_skill_is_rejected(self):
        with self.assertRaisesRegex(SlashCommandError, "不可用"):
            plan_slash_invocation("/hidden-skill", self.registry)

    def test_command_takes_precedence_over_skill_with_same_name(self):
        self.registry.skills["report"] = {
            "id": "report",
            "name": "report",
            "enabled": True,
            "user_invocable": True,
        }

        plan = plan_slash_invocation("/report", self.registry)

        self.assertIsNotNone(plan)
        self.assertEqual(plan.invocation.kind, "command")


if __name__ == "__main__":
    unittest.main()
