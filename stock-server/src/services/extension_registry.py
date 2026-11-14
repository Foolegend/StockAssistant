import json
import re
from pathlib import Path
from typing import Any

import yaml

from src.services.hook_matcher import validate_hook_matcher


SERVER_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = SERVER_ROOT / "config"
PROJECT_ROOT = SERVER_ROOT.parent
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


def _read_markdown_metadata(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as file:
        first_line = file.readline()
        if not first_line.startswith("---"):
            return {}

        frontmatter_lines = []
        for line in file:
            if line.strip() == "---":
                break
            frontmatter_lines.append(line)
        else:
            raise ValueError(f"Invalid YAML frontmatter in {path}")

    metadata = yaml.safe_load("".join(frontmatter_lines)) or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"YAML frontmatter in {path} must be an object")
    return metadata


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


def _read_native_hooks_config(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict) or not isinstance(payload.get("hooks"), dict):
        raise ValueError(f"{path} must contain a 'hooks' object")

    entries = []
    root_id = path.parent.parent.name.lstrip(".")
    for event, registrations in payload["hooks"].items():
        if not isinstance(event, str) or not event.strip() or not isinstance(registrations, list):
            raise ValueError(f"Every event in {path} must map to an array")
        for registration_index, registration in enumerate(registrations):
            if not isinstance(registration, dict):
                raise ValueError(f"Every hook registration in {path} must be an object")
            matcher = registration.get("matcher", "*") or "*"
            commands = registration.get("hooks")
            if not isinstance(matcher, str) or not isinstance(commands, list):
                raise ValueError(f"Hook registrations in {path} must define a matcher and hooks array")

            for command_index, hook in enumerate(commands):
                hook_id = (
                    f"{root_id}-{event.lower()}-{registration_index}-{command_index}"
                )
                if not isinstance(hook, dict) or hook.get("type") != "command":
                    raise ValueError(f"Hook '{hook_id}' in {path} must have type 'command'")
                command = hook.get("command")
                if not isinstance(command, str) or not command.strip():
                    raise ValueError(f"Hook '{hook_id}' in {path} must define a command")

                entry = {
                    "id": hook_id,
                    "name": registration.get("description") or hook_id,
                    "event": event,
                    "matcher": matcher,
                    "command": command,
                    "shell": True,
                }
                if "timeout" in hook:
                    entry["timeout_seconds"] = hook["timeout"]
                entries.append(entry)
    return entries


def _read_mcp_servers_config(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with path.open(encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict) or not isinstance(payload.get("mcpServers"), dict):
        raise ValueError(f"{path} must contain a 'mcpServers' object")

    entries = []
    for server_id, server in payload["mcpServers"].items():
        if not isinstance(server, dict):
            raise ValueError(f"Every entry in {path} must be an object")
        entry = {**server, "id": server_id}
        entry.setdefault("name", server_id)
        entries.append(entry)
    return entries


class ExtensionRegistry:
    def __init__(
        self,
        config_root: Path = CONFIG_ROOT,
        project_root: Path = PROJECT_ROOT,
    ) -> None:
        self.config_root = Path(config_root)
        self.project_root = Path(project_root).resolve()
        self.stock_extension_root = self.project_root / "stock-server" / ".stock"
        self.extension_roots = (self.stock_extension_root,)

        self.skills: dict[str, dict[str, Any]] = {}
        self.skill_aliases: dict[str, str] = {}
        self.skill_sources: list[str] = []

        mcp_servers = []
        for root in self.extension_roots:
            mcp_servers.extend(
                _read_mcp_servers_config(root / "mcp-configs" / "mcp-servers.json")
            )
        self.mcp_servers = _validate_entries(mcp_servers, "project MCP definitions")
        hook_entries = []
        for root in self.extension_roots:
            hook_entries.extend(_read_native_hooks_config(root / "hooks" / "hooks.json"))
        self.hooks = _validate_entries(hook_entries, "project hook definitions")
        self.agents: dict[str, dict[str, Any]] = {}
        self.commands: dict[str, dict[str, Any]] = {}

        self.agent_source_roots: dict[str, Path] = {}
        self._discover_project_definitions()
        self._validate_references()
        if not self.agents:
            raise ValueError(f"No agents found under {self.stock_extension_root}")

    def _discover_skills(self, skills_root: Path) -> None:
        if not skills_root.is_dir():
            return

        for skill_file in sorted(skills_root.rglob("SKILL.md")):
            metadata = _read_markdown_metadata(skill_file)
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
                "file_path": str(skill_file.resolve()),
                "directory_name": skill_file.parent.name,
                "source_root": str(source_root.resolve()),
                "local": False,
            }
            self.skill_aliases[skill_id] = skill_id
            self.skill_aliases[relative_skill_path] = skill_id
            if str(source_root.resolve()) not in self.skill_sources:
                self.skill_sources.append(str(source_root.resolve()))

    def _discover_project_definitions(self) -> None:
        for config_dir in self.extension_roots:
            self._discover_skills(config_dir / "skills")
            for agent_file in sorted((config_dir / "agents").rglob("*.md")):
                metadata = _read_markdown_metadata(agent_file)
                agent_id = metadata.get("name") or agent_file.stem
                if not isinstance(agent_id, str):
                    raise ValueError(f"Agent name in {agent_file} must be a string")
                agent_id = _normalized_id(agent_id, agent_file)
                if agent_id in self.agents:
                    raise ValueError(f"Duplicate agent id '{agent_id}' in {agent_file}")
                description = metadata.get("description", "")
                skill_ids = metadata.get("skills", [])
                server_ids = metadata.get("mcp_server_ids", [])
                display_name = metadata.get("display_name") or agent_id.replace("-", " ").title()
                if (
                    not isinstance(description, str)
                    or not isinstance(skill_ids, list)
                    or not all(isinstance(item, str) for item in skill_ids)
                    or not isinstance(server_ids, list)
                    or not all(isinstance(item, str) for item in server_ids)
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
                    "file_path": str(agent_file.resolve()),
                    "source_type": "project",
                }
                self.agent_source_roots[agent_id] = self.project_root

            for command_file in sorted((config_dir / "commands").rglob("*.md")):
                metadata = _read_markdown_metadata(command_file)
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
                    "file_path": str(command_file.resolve()),
                    "agent_ids": [],
                    "source_type": "project",
                    "project_root": str(self.project_root),
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

    def component_content(self, component_type: str, component_id: str) -> str:
        components = {
            "agent": self.agents,
            "command": self.commands,
            "skill": self.skills,
        }
        component = components.get(component_type, {}).get(component_id)
        if component is None:
            raise KeyError(f"Unknown {component_type} '{component_id}'")
        _, content = _read_markdown(Path(component["file_path"]))
        return content

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
            command = server.get("command")
            is_stdio_server = isinstance(command, str) and bool(command.strip())
            is_http_server = (
                server.get("type") == "http"
                and isinstance(server.get("url"), str)
                and bool(server["url"].strip())
            )
            if not is_stdio_server and not is_http_server:
                raise ValueError(
                    f"MCP server '{server_id}' must define a command or an HTTP URL"
                )
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
            if not isinstance(server.get("headers", {}), dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in server.get("headers", {}).items()
            ):
                raise ValueError(f"MCP server '{server_id}' headers must be a string-to-string object")

        for hook_id, hook in self.hooks.items():
            if not isinstance(hook.get("event"), str) or not hook["event"].strip():
                raise ValueError(f"Hook '{hook_id}' must define a non-empty event")
            command = hook.get("command")
            shell = hook.get("shell", False)
            if not isinstance(shell, bool):
                raise ValueError(f"Hook '{hook_id}' shell must be a boolean")
            valid_command = (
                isinstance(command, str) and bool(command.strip())
                if shell
                else isinstance(command, list)
                and bool(command)
                and all(isinstance(item, str) and item.strip() for item in command)
            )
            if not valid_command:
                raise ValueError(f"Hook '{hook_id}' command must be non-empty")
            timeout = hook.get("timeout_seconds", 10)
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
                raise ValueError(f"Hook '{hook_id}' timeout_seconds must be greater than 0 and at most 120")
            if not isinstance(hook.get("enabled", True), bool):
                raise ValueError(f"Hook '{hook_id}' enabled must be a boolean")
            matcher = hook.get("matcher")
            if matcher is not None:
                if not isinstance(matcher, str) or not matcher.strip():
                    raise ValueError(f"Hook '{hook_id}' matcher must be a non-empty string")
                validate_hook_matcher(matcher)
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
        }

    def resolve(
        self,
        agent_id: str,
        command_id: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        agent = self.agents.get(agent_id)
        command = self.commands.get(command_id) if command_id is not None else None
        if agent is None:
            raise KeyError(f"Unknown agent '{agent_id}'")
        if command_id is not None and command is None:
            raise KeyError(f"Unknown command '{command_id}'")
        allowed_agents = command.get("agent_ids", []) if command else []
        if allowed_agents and agent_id not in allowed_agents:
            raise ValueError(f"Command '{command_id}' is not available for agent '{agent_id}'")
        return agent, command

    def command_input(
        self,
        command: dict[str, Any] | None,
        question: str,
        *,
        additional_commands: list[dict[str, Any]] | None = None,
    ) -> str:
        commands = ([command] if command is not None else []) + (additional_commands or [])
        if not commands:
            return question
        if len(commands) == 1 and not additional_commands:
            parts = []
            if commands[0].get("instruction"):
                parts.append(commands[0]["instruction"])
            prompt = commands[0].get("prompt") or self.component_content(
                "command",
                commands[0]["id"],
            )
            if prompt:
                parts.append(prompt.replace("$ARGUMENTS", question))
            if not prompt or "$ARGUMENTS" not in prompt:
                parts.append(f"User request:\n{question}")
            if len(parts) > 1:
                return f"## Command: {commands[0]['name']}\n\n" + "\n\n".join(parts)
            return parts[0] if parts else question

        sections = []
        for index, nested_command in enumerate(commands):
            parts = []
            if nested_command.get("instruction"):
                parts.append(nested_command["instruction"])
            prompt = nested_command.get("prompt") or self.component_content(
                "command",
                nested_command["id"],
            )
            if prompt:
                parts.append(prompt.replace("$ARGUMENTS", question))
            if not prompt or "$ARGUMENTS" not in prompt:
                parts.append(f"User request:\n{question}")
            if parts:
                heading = "Command" if index == 0 and command is not None else "Referenced Command"
                sections.append(
                    f"## {heading}: {nested_command['name']}\n\n" + "\n\n".join(parts)
                )
        return "\n\n".join(sections) if sections else question

    def _subagents(
        self,
        selected_agent_id: str,
        referenced_agent_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        subagents = []
        for agent_id in referenced_agent_ids or []:
            agent = self.agents[agent_id]
            if agent_id == selected_agent_id or agent.get("source_type") != "project":
                continue
            subagent_prompt = self.component_content("agent", agent_id)
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
        *,
        skill_ids: list[str] | None = None,
        mcp_server_ids: list[str] | None = None,
        referenced_agent_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        system_prompt = (
            self.component_content("agent", agent["id"])
            if agent.get("source_type") == "project"
            else agent.get("system_prompt") or default_system_prompt
        )
        system_prompt = system_prompt or default_system_prompt
        if agent.get("source_type") == "project":
            system_prompt = f"{system_prompt}\n\n{_PROJECT_RUNTIME_GUIDANCE}".strip()
        instruction = command.get("instruction") if command else None
        if instruction:
            system_prompt = f"{system_prompt or ''}\n\n## 当前命令：{command['name']}\n{instruction}".strip()
        if referenced_agent_ids:
            referenced_agents = [
                f"- {agent_id}: {self.agents[agent_id].get('description', '')}"
                for agent_id in referenced_agent_ids
            ]
            system_prompt = (
                f"{system_prompt or ''}\n\n## 被引用的子 Agent\n"
                "以下已注册 Agent 可通过 task 工具委派；仅在任务需要时调用：\n"
                + "\n".join(referenced_agents)
            ).strip()

        agent_root = self.agent_source_roots.get(agent["id"])
        project_root = (
            (command.get("project_root") if command else None)
            or (str(agent_root) if agent_root else str(SERVER_ROOT))
        )
        selected_skill_ids = agent.get("skill_ids", []) if skill_ids is None else skill_ids
        selected_mcp_server_ids = (
            agent.get("mcp_server_ids", [])
            if mcp_server_ids is None
            else mcp_server_ids
        )
        skill_dirs = self._skill_source_paths(selected_skill_ids)
        selected_skill_directories: dict[str, list[str]] = {}
        for skill_id in selected_skill_ids:
            skill = self.skills[skill_id]
            directories = selected_skill_directories.setdefault(
                skill["source_root"],
                [],
            )
            if skill["directory_name"] not in directories:
                directories.append(skill["directory_name"])

        return {
            "model_name": agent.get("model") if agent.get("source_type") != "project" else None,
            "skill_dirs": skill_dirs,
            "selected_skill_ids": selected_skill_ids,
            "selected_skill_directories": selected_skill_directories,
            "mcp_servers": [self.mcp_servers[server_id] for server_id in selected_mcp_server_ids],
            "hooks": self.hooks,
            "system_prompt": system_prompt,
            "project_root": project_root,
            "subagents": self._subagents(agent["id"], referenced_agent_ids),
        }
