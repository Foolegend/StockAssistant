import json
import re
from pathlib import Path
from typing import Any


SERVER_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = SERVER_ROOT / "config"
SKILLS_ROOT = CONFIG_ROOT / "skills"
_EXTENSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _read_collection(path: Path, collection_name: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not path.exists():
        return {}, []

    with path.open(encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict) or not isinstance(payload.get(collection_name, []), list):
        raise ValueError(f"{path} must contain a '{collection_name}' array")

    entries = payload.get(collection_name, [])
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"Every entry in {path} must be an object")

    return payload, entries


def _skill_metadata(skill_file: Path) -> tuple[str, str]:
    content = skill_file.read_text(encoding="utf-8")
    frontmatter = ""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) == 3:
            frontmatter = parts[1]

    name_match = re.search(r"(?m)^name:\s*(.+?)\s*$", frontmatter)
    skill_id = name_match.group(1).strip().strip("\"'") if name_match else skill_file.parent.name

    description_match = re.search(r"(?m)^description:\s*(.*)$", frontmatter)
    description = ""
    if description_match:
        description_value = description_match.group(1).strip()
        if description_value in ("|", ">"):
            lines = []
            for line in frontmatter[description_match.end():].splitlines():
                if line.strip() and not line[:1].isspace():
                    break
                if line.strip():
                    lines.append(line.strip())
            description = " ".join(lines)
        else:
            description = description_value.strip("\"'")

    if not description:
        body = content.split("---", 2)[-1].strip() if frontmatter else content.strip()
        description = next((line.lstrip("# ").strip() for line in body.splitlines() if line.strip()), "")

    return skill_id, description


def _validate_entries(entries: list[dict[str, Any]], source: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        extension_id = entry.get("id")
        if not isinstance(extension_id, str) or not _EXTENSION_ID.fullmatch(extension_id):
            raise ValueError(f"Each entry in {source} must have a valid 'id'")
        if extension_id in indexed:
            raise ValueError(f"Duplicate extension id '{extension_id}' in {source}")
        if not isinstance(entry.get("name"), str) or not entry["name"].strip():
            raise ValueError(f"Extension '{extension_id}' in {source} must have a display name")
        indexed[extension_id] = entry
    return indexed


class ExtensionRegistry:
    def __init__(
        self,
        config_root: Path = CONFIG_ROOT,
        skills_root: Path = SKILLS_ROOT,
    ) -> None:
        self.config_root = Path(config_root)
        self.skills_root = Path(skills_root)

        self.skills = self._discover_skills()
        _, mcp_servers = _read_collection(self.config_root / "mcp_servers.json", "servers")
        self.mcp_servers = _validate_entries(mcp_servers, "mcp_servers.json")
        self.agents_config, agents = _read_collection(self.config_root / "agents.json", "agents")
        self.agents = _validate_entries(agents, "agents.json")
        self.commands_config, commands = _read_collection(self.config_root / "commands.json", "commands")
        self.commands = _validate_entries(commands, "commands.json")

        self._validate_references()
        self.default_agent_id = self.agents_config.get("default_agent_id")
        self.default_command_id = self.commands_config.get("default_command_id")
        if not isinstance(self.default_agent_id, str) or self.default_agent_id not in self.agents:
            raise ValueError("agents.json default_agent_id must refer to a registered agent")
        if not isinstance(self.default_command_id, str) or self.default_command_id not in self.commands:
            raise ValueError("commands.json default_command_id must refer to a registered command")

    def _discover_skills(self) -> dict[str, dict[str, Any]]:
        skills: dict[str, dict[str, Any]] = {}
        if not self.skills_root.is_dir():
            return skills

        for skill_file in sorted(self.skills_root.glob("*/SKILL.md")):
            skill_id, description = _skill_metadata(skill_file)
            if not _EXTENSION_ID.fullmatch(skill_id):
                raise ValueError(f"Invalid skill id '{skill_id}' in {skill_file}")
            if skill_id in skills:
                raise ValueError(f"Duplicate skill id '{skill_id}'")
            skills[skill_id] = {
                "id": skill_id,
                "name": skill_id.replace("-", " ").title(),
                "description": description,
                "path": str(skill_file.parent.resolve()),
            }
        return skills

    def _validate_references(self) -> None:
        for agent_id, agent in self.agents.items():
            if not isinstance(agent.get("model"), (str, type(None))):
                raise ValueError(f"Agent '{agent_id}' model must be a string")
            if not isinstance(agent.get("system_prompt"), (str, type(None))):
                raise ValueError(f"Agent '{agent_id}' system_prompt must be a string")
            skill_ids = agent.get("skill_ids", [])
            server_ids = agent.get("mcp_server_ids", [])
            if not isinstance(skill_ids, list) or not all(isinstance(item, str) for item in skill_ids):
                raise ValueError(f"Agent '{agent_id}' skill_ids must be an array of IDs")
            if not isinstance(server_ids, list) or not all(isinstance(item, str) for item in server_ids):
                raise ValueError(f"Agent '{agent_id}' mcp_server_ids must be an array of IDs")
            for skill_id in skill_ids:
                if skill_id not in self.skills:
                    raise ValueError(f"Agent '{agent_id}' references unknown skill '{skill_id}'")
            for server_id in server_ids:
                if server_id not in self.mcp_servers:
                    raise ValueError(f"Agent '{agent_id}' references unknown MCP server '{server_id}'")
        for server_id, server in self.mcp_servers.items():
            if not isinstance(server.get("command"), str) or not server["command"].strip():
                raise ValueError(f"MCP server '{server_id}' must define a command")
            if not isinstance(server.get("args", []), list) or not all(
                isinstance(item, str) for item in server.get("args", [])
            ):
                raise ValueError(f"MCP server '{server_id}' args must be an array of strings")
            if not isinstance(server.get("env", {}), dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in server.get("env", {}).items()
            ):
                raise ValueError(f"MCP server '{server_id}' env must be a string-to-string object")
            if not isinstance(server.get("cwd", ""), str):
                raise ValueError(f"MCP server '{server_id}' cwd must be a string")
        for command_id, command in self.commands.items():
            if not isinstance(command.get("instruction"), (str, type(None))):
                raise ValueError(f"Command '{command_id}' instruction must be a string")
            agent_ids = command.get("agent_ids", [])
            if not isinstance(agent_ids, list) or not all(isinstance(item, str) for item in agent_ids):
                raise ValueError(f"Command '{command_id}' agent_ids must be an array of IDs")
            for agent_id in agent_ids:
                if agent_id not in self.agents:
                    raise ValueError(f"Command '{command_id}' references unknown agent '{agent_id}'")

    def catalog(self) -> dict[str, Any]:
        agents = []
        for agent_id, agent in self.agents.items():
            agents.append({
                "id": agent_id,
                "name": agent["name"],
                "description": agent.get("description", ""),
                "skills": [
                    {"id": skill_id, "name": self.skills[skill_id]["name"]}
                    for skill_id in agent.get("skill_ids", [])
                ],
            })

        return {
            "agents": agents,
            "commands": [
                {
                    "id": command_id,
                    "name": command["name"],
                    "description": command.get("description", ""),
                    "agent_ids": command.get("agent_ids", []),
                }
                for command_id, command in self.commands.items()
            ],
            "default_agent_id": self.default_agent_id,
            "default_command_id": self.default_command_id,
        }

    def resolve(self, agent_id: str | None, command_id: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
        selected_agent_id = agent_id or self.default_agent_id
        selected_command_id = command_id or self.default_command_id
        agent = self.agents.get(selected_agent_id)
        command = self.commands.get(selected_command_id)
        if agent is None:
            raise KeyError(f"Unknown agent '{selected_agent_id}'")
        if command is None:
            raise KeyError(f"Unknown command '{selected_command_id}'")
        allowed_agents = command.get("agent_ids", [])
        if allowed_agents and selected_agent_id not in allowed_agents:
            raise ValueError(f"Command '{selected_command_id}' is not available for agent '{selected_agent_id}'")
        return agent, command

    def agent_runtime_config(
        self,
        agent: dict[str, Any],
        command: dict[str, Any],
        default_system_prompt: str = "",
    ) -> dict[str, Any]:
        system_prompt = agent.get("system_prompt") or default_system_prompt
        instruction = command.get("instruction")
        if instruction:
            system_prompt = f"{system_prompt or ''}\n\n## 当前命令：{command['name']}\n{instruction}".strip()

        return {
            "model_name": agent.get("model"),
            "skill_dirs": [self.skills[skill_id]["path"] for skill_id in agent.get("skill_ids", [])],
            "mcp_servers": [self.mcp_servers[server_id] for server_id in agent.get("mcp_server_ids", [])],
            "system_prompt": system_prompt,
        }
