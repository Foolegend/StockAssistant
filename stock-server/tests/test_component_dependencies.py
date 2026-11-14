import os
import sys
import unittest
from copy import deepcopy
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.component_router import ComponentSelection
from src.services.component_dependencies import resolve_component_dependencies


class FakeStructuredModel:
    def __init__(self, selection):
        self.selection = selection

    def invoke(self, messages):
        return self.selection


class FakeModel:
    def __init__(self, responses=None):
        self.responses = list(responses or [])

    def with_structured_output(self, schema, *, method):
        self.schema = schema
        self.method = method
        response = self.responses.pop(0) if self.responses else {"references": []}
        return FakeStructuredModel(response)


class FakeRegistry:
    _agents = {
        "root-agent": {
            "id": "root-agent",
            "name": "Root Agent",
            "description": "Root",
            "prompt": "Run @command:analysis and delegate to @agent:market-analyst.",
            "skill_ids": [],
            "mcp_server_ids": [],
        },
        "market-analyst": {
            "id": "market-analyst",
            "name": "Market Analyst",
            "description": "Analyze markets",
            "prompt": "",
            "skill_ids": ["analyst-skill"],
            "mcp_server_ids": [],
        },
    }
    _commands = {
        "analysis": {
            "id": "analysis",
            "name": "/analysis",
            "description": "Analyze a question",
            "prompt": "Use @skill:overview and @command:followup.",
            "instruction": None,
            "agent_ids": [],
        },
        "followup": {
            "id": "followup",
            "name": "/followup",
            "description": "Follow up",
            "prompt": "Return to @command:analysis.",
            "instruction": None,
            "agent_ids": [],
        },
    }
    _skills = {
        "overview": {
            "id": "overview",
            "name": "Overview",
            "description": "Overview skill",
            "content": "Also use @skill:deep-analysis.",
        },
        "deep-analysis": {
            "id": "deep-analysis",
            "name": "Deep Analysis",
            "description": "Detailed analysis",
            "content": "",
        },
        "analyst-skill": {
            "id": "analyst-skill",
            "name": "Analyst Skill",
            "description": "Analyst capability",
            "content": "",
        },
    }
    _mcp_servers = {}

    def __init__(self):
        self.agents = deepcopy(self._agents)
        self.commands = deepcopy(self._commands)
        self.skills = deepcopy(self._skills)
        self.mcp_servers = dict(self._mcp_servers)
        self.content_reads = []

    def catalog(self):
        return {
            "agents": list(self.agents.values()),
            "commands": list(self.commands.values()),
            "skills": list(self.skills.values()),
        }

    def component_content(self, component_type, component_id):
        self.content_reads.append((component_type, component_id))
        component = {
            "agent": self.agents,
            "command": self.commands,
            "skill": self.skills,
        }[component_type][component_id]
        if component_type == "skill":
            return component.get("content", "")
        return component.get("prompt", "")

    def resolve(self, agent_id, command_id):
        agent = self.agents[agent_id]
        command = self.commands.get(command_id) if command_id else None
        if command and command["agent_ids"] and agent_id not in command["agent_ids"]:
            raise ValueError("Command is not available for agent")
        return agent, command


class ComponentDependencyTests(unittest.TestCase):
    def test_expands_nested_references_and_stops_command_cycles(self):
        registry = FakeRegistry()
        selection = ComponentSelection(
            agent_id="root-agent",
            command_id="analysis",
            skill_ids=["overview"],
            mcp_server_ids=[],
        )

        resolved = resolve_component_dependencies(
            "Analyze the market",
            selection,
            registry,
            FakeModel(),
        )

        self.assertEqual(resolved.command_ids, ["analysis", "followup"])
        self.assertEqual(
            set(resolved.skill_ids),
            {"overview", "deep-analysis", "analyst-skill"},
        )
        self.assertEqual(resolved.referenced_agent_ids, ["market-analyst"])
        self.assertIn(("skill", "deep-analysis"), registry.content_reads)

    def test_dependency_discovery_reads_only_selected_component_bodies(self):
        registry = FakeRegistry()
        registry.agents["root-agent"]["prompt"] = "Handle the user request."
        selection = ComponentSelection(
            agent_id="root-agent",
            skill_ids=[],
            mcp_server_ids=[],
        )

        resolve_component_dependencies(
            "Do the task",
            selection,
            registry,
            FakeModel(),
        )

        self.assertIn(("agent", "root-agent"), registry.content_reads)
        self.assertNotIn(("agent", "market-analyst"), registry.content_reads)
        self.assertNotIn(("command", "analysis"), registry.content_reads)
        self.assertNotIn(("skill", "overview"), registry.content_reads)

    def test_infers_natural_language_agent_references(self):
        registry = FakeRegistry()
        registry.agents["root-agent"]["prompt"] = (
            "For this request, ask the equity analyst to review it."
        )
        selection = ComponentSelection(
            agent_id="root-agent",
            skill_ids=[],
            mcp_server_ids=[],
        )
        model = FakeModel(
            [
                {
                    "references": [
                        {"component_type": "agent", "component_id": "market-analyst"}
                    ]
                }
            ]
        )

        resolved = resolve_component_dependencies(
            "Review this company",
            selection,
            registry,
            model,
        )

        self.assertEqual(resolved.referenced_agent_ids, ["market-analyst"])

    def test_detects_registered_display_name_references(self):
        registry = FakeRegistry()
        registry.agents["root-agent"]["prompt"] = "Delegate this part to the Market Analyst."
        selection = ComponentSelection(
            agent_id="root-agent",
            skill_ids=[],
            mcp_server_ids=[],
        )

        resolved = resolve_component_dependencies(
            "Analyze the market",
            selection,
            registry,
            FakeModel(),
        )

        self.assertEqual(resolved.referenced_agent_ids, ["market-analyst"])

    def test_rejects_nested_command_incompatible_with_root_agent(self):
        registry = FakeRegistry()
        registry.agents["root-agent"]["prompt"] = "Run @command:followup."
        registry.commands["followup"]["agent_ids"] = ["market-analyst"]
        selection = ComponentSelection(
            agent_id="root-agent",
            skill_ids=[],
            mcp_server_ids=[],
        )

        with self.assertRaisesRegex(ValueError, "not available"):
            resolve_component_dependencies(
                "Do the task",
                selection,
                registry,
                FakeModel(),
            )

    def test_rejects_unregistered_model_inferred_dependencies(self):
        selection = ComponentSelection(
            agent_id="root-agent",
            skill_ids=[],
            mcp_server_ids=[],
        )
        model = FakeModel(
            [
                {
                    "references": [
                        {"component_type": "skill", "component_id": "not-registered"}
                    ]
                }
            ]
        )

        with self.assertRaisesRegex(ValueError, "unknown skill"):
            resolve_component_dependencies(
                "Do the task",
                selection,
                FakeRegistry(),
                model,
            )

    def test_rejects_dependency_chains_beyond_the_depth_limit(self):
        registry = FakeRegistry()
        registry.agents["root-agent"]["prompt"] = "Use @command:analysis."
        selection = ComponentSelection(
            agent_id="root-agent",
            skill_ids=[],
            mcp_server_ids=[],
        )

        with patch("src.services.component_dependencies.MAX_DEPENDENCY_DEPTH", 0):
            with self.assertRaisesRegex(ValueError, "depth limit"):
                resolve_component_dependencies(
                    "Do the task",
                    selection,
                    registry,
                    FakeModel(),
                )
