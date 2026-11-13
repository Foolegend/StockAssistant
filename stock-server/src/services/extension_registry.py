import json
import re
from pathlib import Path
from typing import Any

import yaml


SERVER_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = SERVER_ROOT / "config"
PROJECT_ROOT = SERVER_ROOT.parent
EXTENSION_DIR_NAMES = (".github", ".claude", ".cursor")
_EXTENSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_PROJECT_RUNTIME_GUIDANCE = """
## Project definition compatibility
- These Agent, Command, and Skill files are instructions, not executable editor plugins.
- For an Agent-tool delegation request, use the registered `task` tool and the matching loaded subagent.
- For a Skill-tool request, read and follow the matching skill in the loaded Skills catalog.
- `Bash` requests may use the available `execute` tool within the configured workspace.
- AskUserQuestion requests must be handled by asking the user in the normal chat and waiting for their reply.
- Do not claim to use unavailable web search, fetch, MCP, or other editor-specific tools.
- Editor-specific tool and permission metadata are not security boundaries here; the runtime's actual server-side tools and workspace determine access.
- Native editor settings, permissions, and memory files are not imported or executed. Hooks are loaded only from project `hooks` JSON files.
"""
HOOK_EVENTS = {"before_agent", "after_agent", "on_error"}


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


def _read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    content = path.read_text(encoding="utf-8-sig")
    if not content.startswith("---"):
        return {}, content.strip()

    parts = content.split("---", 2)
    if len(parts) != 3:
        raise ValueError(f"Invalid YAML frontmatter in {path}")
    metadata = yaml.safe_load(parts[1]) or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"YAML frontmatter in {path} must be an object")
    return metadata, parts[2].strip()


def _normalized_id(value: str, source: Path) -> str:
    extension_id = value.strip()
    if not _EXTENSION_ID.fullmatch(extension_id) or any(part in (".", "..") for part in extension_id.split("/")):
        raise ValueError(f"Invalid extension id '{extension_id}' in {source}")
    return extension_id


def _validate_entries(entries: list[dict[str, Any]], source: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        extension_id = entry.get("id")
        if (
            not isinstance(extension_id, str)
            or not _EXTENSION_ID.fullmatch(extension_id)
            or any(part in (".", "..") for part in extension_id.split("/"))
        ):
            raise ValueError(f"Each entry in {source} must have a valid 'id'")
        if extension_id in indexed:
            raise ValueError(f"Duplicate extension id '{extension_id}' in {source}")
        if not isinstance(entry.get("name"), str) or not entry["name"].strip():
            raise ValueError(f"Extension '{extension_id}' in {source} must have a display name")
        indexed[extension_id] = entry
    return indexed


def _read_directory_collection(root: Path, directory_name: str, collection_name: str) -> list[dict[str, Any]]:
    entries = []
    directory = root / directory_name
    if not directory.is_dir():
        return entries

    for path in sorted(directory.rglob("*.json")):
        if root.name == ".claude" and directory_name == "hooks" and path.name == "hooks.json":
            continue
        _, collection_entries = _read_collection(path, collection_name)
        entries.extend(collection_entries)
    return entries


class ExtensionRegistry:
    def __init__(
        self,
        config_root: Path = CONFIG_ROOT,
        project_root: Path = PROJECT_ROOT,
    ) -> None:
        self.config_root = Path(config_root)
        self.project_root = Path(project_root).resolve()
        self.extension_roots = tuple(self.project_root / name for name in EXTENSION_DIR_NAMES)

        self.skills: dict[str, dict[str, Any]] = {}
        self.skill_aliases: dict[str, str] = {}
        self.skill_sources: list[str] = []

        mcp_servers = []
        for root in self.extension_roots:
            mcp_servers.extend(_read_directory_collection(root, "mcp", "servers"))
        self.mcp_servers = _validate_entries(mcp_servers, "project MCP definitions")
        hook_entries = []
        for root in self.extension_roots:
            hook_entries.extend(_read_directory_collection(root, "hooks", "hooks"))
        self.hooks = _validate_entries(hook_entries, "project hook definitions")
        self.agents: dict[str, dict[str, Any]] = {}
        self.default_agent_candidates: list[str] = []
        self.commands: dict[str, dict[str, Any]] = {}

        self.agent_source_roots: dict[str, Path] = {}
        self._discover_project_definitions()
        self._validate_references()
        if not self.agents:
            raise ValueError("No agents found under project .github, .claude, or .cursor directories")
        if len(self.default_agent_candidates) > 1:
            raise ValueError("Only one project Agent may declare 'default: true'")
        if self.default_agent_candidates:
            self.default_agent_id = self.default_agent_candidates[0]
        elif "stock-assistant" in self.agents:
            self.default_agent_id = "stock-assistant"
        else:
            self.default_agent_id = next(iter(self.agents))
        self.default_command_id = (
            "chat" if "chat" in self.commands else next(iter(self.commands), None)
        )

    def _discover_skills(self, skills_root: Path, *, local_source: bool = False) -> None:
        if not skills_root.is_dir():
            return

        for skill_file in sorted(skills_root.rglob("SKILL.md")):
            metadata, _ = _read_markdown(skill_file)
            skill_id = metadata.get("name") or skill_file.parent.name
            if not isinstance(skill_id, str):
                raise ValueError(f"Skill name in {skill_file} must be a string")
            skill_id = _normalized_id(skill_id, skill_file)
            if skill_id in self.skills:
                raise ValueError(f"Duplicate skill id '{skill_id}' in {skill_file}")
            description = metadata.get("description", "")
            if not isinstance(description, str):
                raise ValueError(f"Skill description in {skill_file} must be a string")

            source_root = skill_file.parent.parent
            relative_skill_path = skill_file.parent.relative_to(skills_root).as_posix()
            self.skills[skill_id] = {
                "id": skill_id,
                "name": metadata.get("name") or skill_id.replace("-", " ").title(),
                "description": description,
                "path": str(skill_file.parent.resolve()),
                "source_root": str(source_root.resolve()),
                "local": local_source,
            }
            self.skill_aliases[skill_id] = skill_id
            self.skill_aliases[relative_skill_path] = skill_id
            if str(source_root.resolve()) not in self.skill_sources:
                self.skill_sources.append(str(source_root.resolve()))

    def _discover_project_definitions(self) -> None:
        for config_dir in self.extension_roots:
            self._discover_skills(
                config_dir / "skills",
                local_source=config_dir.name == ".github",
            )

            for agent_file in sorted((config_dir / "agents").rglob("*.md")):
                metadata, prompt = _read_markdown(agent_file)
                agent_id = metadata.get("name") or agent_file.stem
                if not isinstance(agent_id, str):
                    raise ValueError(f"Agent name in {agent_file} must be a string")
                agent_id = _normalized_id(agent_id, agent_file)
                if agent_id in self.agents:
                    raise ValueError(f"Duplicate agent id '{agent_id}' in {agent_file}")
                description = metadata.get("description", "")
                skill_ids = metadata.get("skills", [])
                server_ids = metadata.get("mcp_server_ids", [])
                is_default = metadata.get("default", False)
                display_name = metadata.get("display_name") or agent_id.replace("-", " ").title()
                if (
                    not isinstance(description, str)
                    or not isinstance(skill_ids, list)
                    or not all(isinstance(item, str) for item in skill_ids)
                    or not isinstance(server_ids, list)
                    or not all(isinstance(item, str) for item in server_ids)
                    or not isinstance(is_default, bool)
                    or not isinstance(display_name, str)
                    or not display_name.strip()
                ):
                    raise ValueError(f"Invalid Agent metadata in {agent_file}")
                self.agents[agent_id] = {
                    "id": agent_id,
                    "name": display_name,
                    "description": description,
                    "skill_ids": skill_ids,
                    "mcp_server_ids": server_ids,
                    "prompt": prompt,
                    "source_type": "project",
                }
                if is_default:
                    self.default_agent_candidates.append(agent_id)
                self.agent_source_roots[agent_id] = config_dir.parent

            for command_file in sorted((config_dir / "commands").rglob("*.md")):
                metadata, prompt = _read_markdown(command_file)
                command_id = _normalized_id(
                    command_file.relative_to(config_dir / "commands").with_suffix("").as_posix(),
                    command_file,
                )
                if command_id in self.commands:
                    raise ValueError(f"Duplicate command id '{command_id}' in {command_file}")
                description = metadata.get("description", "")
                if not isinstance(description, str):
                    raise ValueError(f"Command description in {command_file} must be a string")
                display_name = metadata.get("name") or f"/{command_id}"
                if not isinstance(display_name, str):
                    raise ValueError(f"Command name in {command_file} must be a string")
                self.commands[command_id] = {
                    "id": command_id,
                    "name": display_name,
                    "description": description,
                    "instruction": None,
                    "prompt": prompt,
                    "agent_ids": [],
                    "source_type": "project",
                    "project_root": str(config_dir.parent),
                }

    def _resolve_skill_ids(self, owner_id: str, raw_skill_ids: list[str]) -> list[str]:
        resolved = []
        for skill_id in raw_skill_ids:
            resolved_id = self.skill_aliases.get(skill_id)
            if resolved_id is None:
                raise ValueError(f"Extension '{owner_id}' references unknown skill '{skill_id}'")
            resolved.append(resolved_id)
        return resolved

    def _skill_source_paths(self, skill_ids: list[str]) -> list[str]:
        sources = []
        for skill_id in skill_ids:
            source_root = self.skills[skill_id]["source_root"]
            if source_root not in sources:
                sources.append(source_root)
        return sources

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
            agent["skill_ids"] = self._resolve_skill_ids(agent_id, skill_ids)
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

        for hook_id, hook in self.hooks.items():
            if hook.get("event") not in HOOK_EVENTS:
                supported = ", ".join(sorted(HOOK_EVENTS))
                raise ValueError(f"Hook '{hook_id}' event must be one of: {supported}")
            command = hook.get("command")
            if not isinstance(command, list) or not command or not all(
                isinstance(item, str) and item.strip() for item in command
            ):
                raise ValueError(f"Hook '{hook_id}' command must be a non-empty array of strings")
            timeout = hook.get("timeout_seconds", 10)
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
                raise ValueError(f"Hook '{hook_id}' timeout_seconds must be greater than 0 and at most 120")
            if not isinstance(hook.get("enabled", True), bool):
                raise ValueError(f"Hook '{hook_id}' enabled must be a boolean")
            for field, registered_ids in (
                ("agent_ids", self.agents),
                ("command_ids", self.commands),
            ):
                ids = hook.get(field, [])
                if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
                    raise ValueError(f"Hook '{hook_id}' {field} must be an array of IDs")
                for extension_id in ids:
                    if extension_id not in registered_ids:
                        raise ValueError(
                            f"Hook '{hook_id}' references unknown {field[:-1]} '{extension_id}'"
                        )

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
        return {
            "agents": [
                {
                    "id": agent_id,
                    "name": agent["name"],
                    "description": agent.get("description", ""),
                    "skills": [
                        {"id": skill_id, "name": self.skills[skill_id]["name"]}
                        for skill_id in agent.get("skill_ids", [])
                    ],
                    "mcp_servers": [
                        {
                            "id": server_id,
                            "name": self.mcp_servers[server_id]["name"],
                            "description": self.mcp_servers[server_id].get("description", ""),
                        }
                        for server_id in agent.get("mcp_server_ids", [])
                    ],
                }
                for agent_id, agent in self.agents.items()
            ],
            "skills": [
                {
                    "id": skill_id,
                    "name": skill["name"],
                    "description": skill.get("description", ""),
                }
                for skill_id, skill in self.skills.items()
            ],
            "mcp_servers": [
                {
                    "id": server_id,
                    "name": server["name"],
                    "description": server.get("description", ""),
                }
                for server_id, server in self.mcp_servers.items()
            ],
            "hooks": [
                {
                    "id": hook_id,
                    "name": hook["name"],
                    "event": hook["event"],
                    "enabled": hook.get("enabled", True),
                }
                for hook_id, hook in self.hooks.items()
            ],
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

    def resolve(
        self,
        agent_id: str | None,
        command_id: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        selected_agent_id = agent_id or self.default_agent_id
        selected_command_id = command_id or self.default_command_id
        agent = self.agents.get(selected_agent_id)
        command = self.commands.get(selected_command_id) if selected_command_id is not None else None
        if agent is None:
            raise KeyError(f"Unknown agent '{selected_agent_id}'")
        if selected_command_id is not None and command is None:
            raise KeyError(f"Unknown command '{selected_command_id}'")
        allowed_agents = command.get("agent_ids", []) if command else []
        if allowed_agents and selected_agent_id not in allowed_agents:
            raise ValueError(f"Command '{selected_command_id}' is not available for agent '{selected_agent_id}'")
        return agent, command

    def command_input(self, command: dict[str, Any] | None, question: str) -> str:
        if command is None:
            return question
        parts = []
        if command.get("instruction"):
            parts.append(command["instruction"])
        if command.get("prompt"):
            parts.append(command["prompt"].replace("$ARGUMENTS", question))
        if not command.get("prompt") or "$ARGUMENTS" not in command.get("prompt", ""):
            parts.append(f"User request:\n{question}")
        if len(parts) > 1:
            return f"## Command: {command['name']}\n\n" + "\n\n".join(parts)
        return parts[0] if parts else question

    def _subagents(self, selected_agent_id: str) -> list[dict[str, Any]]:
        subagents = []
        for agent_id, agent in self.agents.items():
            if agent_id == selected_agent_id or agent.get("source_type") != "project":
                continue
            subagent_prompt = agent.get("prompt", "")
            if subagent_prompt:
                subagent_prompt = f"{subagent_prompt}\n\n{_PROJECT_RUNTIME_GUIDANCE}".strip()
            else:
                subagent_prompt = f"{agent.get('description', '')}\n\n{_PROJECT_RUNTIME_GUIDANCE}".strip()
            subagents.append({
                "name": agent_id,
                "description": agent.get("description", ""),
                "system_prompt": subagent_prompt,
                "skills": self._skill_source_paths(agent.get("skill_ids", [])),
            })
        return subagents

    def agent_runtime_config(
        self,
        agent: dict[str, Any],
        command: dict[str, Any] | None,
        default_system_prompt: str = "",
    ) -> dict[str, Any]:
        system_prompt = agent.get("prompt") or agent.get("system_prompt") or default_system_prompt
        if agent.get("source_type") == "project":
            system_prompt = f"{system_prompt}\n\n{_PROJECT_RUNTIME_GUIDANCE}".strip()
        instruction = command.get("instruction") if command else None
        if instruction:
            system_prompt = f"{system_prompt or ''}\n\n## 当前命令：{command['name']}\n{instruction}".strip()

        agent_root = self.agent_source_roots.get(agent["id"])
        project_root = (
            (command.get("project_root") if command else None)
            or (str(agent_root) if agent_root else str(SERVER_ROOT))
        )
        skill_dirs = self._skill_source_paths(agent.get("skill_ids", []))

        return {
            "model_name": agent.get("model") if agent.get("source_type") != "project" else None,
            "skill_dirs": skill_dirs,
            "mcp_servers": [self.mcp_servers[server_id] for server_id in agent.get("mcp_server_ids", [])],
            "system_prompt": system_prompt,
            "project_root": project_root,
            "subagents": self._subagents(agent["id"]),
        }
