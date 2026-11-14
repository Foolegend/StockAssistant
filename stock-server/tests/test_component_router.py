import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.component_router import ComponentSelection, select_components


class FakeStructuredModel:
    def __init__(self, selection):
        self.selection = selection
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return self.selection


class FakeModel:
    def __init__(self, selection):
        self.selection = selection
        self.schema = None
        self.method = None
        self.structured_model = FakeStructuredModel(selection)

    def with_structured_output(self, schema, *, method):
        self.schema = schema
        self.method = method
        return self.structured_model


class FakeRegistry:
    agents = {
        "stock-analyst": {
            "id": "stock-analyst",
            "skill_ids": ["stock-analysis"],
            "mcp_server_ids": [],
        }
    }
    commands = {
        "analyze-stock": {
            "id": "analyze-stock",
            "agent_ids": ["stock-analyst"],
        }
    }
    skills = {"stock-analysis": {"id": "stock-analysis"}}
    mcp_servers = {"market-data": {"id": "market-data"}}

    def catalog(self):
        return {
            "agents": [
                {
                    "id": "stock-analyst",
                    "name": "Stock analyst",
                    "description": "Analyze stocks",
                    "skills": [{"id": "stock-analysis", "name": "Stock analysis"}],
                    "mcp_servers": [],
                }
            ],
            "commands": [
                {
                    "id": "analyze-stock",
                    "name": "Analyze stock",
                    "description": "Analyze a ticker",
                    "agent_ids": ["stock-analyst"],
                }
            ],
            "skills": [
                {"id": "stock-analysis", "name": "Stock analysis", "description": "Stock research"}
            ],
            "mcp_servers": [
                {"id": "market-data", "name": "Market data", "description": "Market quotes"}
            ],
        }


class ComponentRouterTests(unittest.TestCase):
    def test_selects_registered_agent_command_skill_and_mcp(self):
        model = FakeModel(
            {
                "agent_id": "stock-analyst",
                "command_id": "analyze-stock",
                "skill_ids": ["stock-analysis"],
                "mcp_server_ids": ["market-data"],
            }
        )

        selection = select_components("Analyze TSLA", FakeRegistry(), model)

        self.assertIsInstance(selection, ComponentSelection)
        self.assertEqual(selection.agent_id, "stock-analyst")
        self.assertEqual(selection.command_id, "analyze-stock")
        self.assertEqual(selection.skill_ids, ["stock-analysis"])
        self.assertEqual(selection.mcp_server_ids, ["market-data"])
        self.assertIs(model.schema, ComponentSelection)
        self.assertEqual(model.method, "function_calling")

    def test_rejects_unregistered_component_ids(self):
        model = FakeModel(
            {
                "agent_id": "unknown-agent",
                "command_id": None,
                "skill_ids": [],
                "mcp_server_ids": [],
            }
        )

        with self.assertRaisesRegex(ValueError, "unknown agent"):
            select_components("Analyze TSLA", FakeRegistry(), model)

    def test_rejects_command_not_available_to_selected_agent(self):
        model = FakeModel(
            {
                "agent_id": "stock-analyst",
                "command_id": "analyze-stock",
                "skill_ids": [],
                "mcp_server_ids": [],
            }
        )
        registry = FakeRegistry()
        registry.commands = {
            "analyze-stock": {"id": "analyze-stock", "agent_ids": ["another-agent"]}
        }

        with self.assertRaisesRegex(ValueError, "not available"):
            select_components("Analyze TSLA", registry, model)

    def test_rejects_unregistered_skill_and_mcp_ids(self):
        for field, selected_id, message in (
            ("skill_ids", ["unknown-skill"], "unknown skill"),
            ("mcp_server_ids", ["unknown-server"], "unknown MCP server"),
        ):
            selection = {
                "agent_id": "stock-analyst",
                "command_id": None,
                "skill_ids": [],
                "mcp_server_ids": [],
            }
            selection[field] = selected_id

            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                select_components("Analyze TSLA", FakeRegistry(), FakeModel(selection))
