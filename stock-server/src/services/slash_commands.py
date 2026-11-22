"""Deterministic `/command` and `/skill` invocation parsing.

Commands are user-invoked only: they never enter the LLM router. Skills can be
invoked either way, mirroring Claude Code's user-invocable / model-invocable
split.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal

from src.services.component_router import ComponentSelection


_SLASH = re.compile(r"^/([A-Za-z0-9][A-Za-z0-9._:-]*)(?:[ \t]+([\s\S]*))?$")


class SlashCommandError(ValueError):
    """Raised when a leading slash looks like an invocation but cannot resolve."""


@dataclass(frozen=True)
class SlashInvocation:
    kind: Literal["command", "skill"]
    id: str
    name: str
    slash_name: str
    arguments: str


@dataclass(frozen=True)
class SlashPlan:
    invocation: SlashInvocation
    question: str
    selection: ComponentSelection
    pinned_skills: list[str]
    command: dict[str, Any] | None


def _slash_token(question: str) -> tuple[str, str] | None:
    text = question.strip()
    if not text.startswith("/"):
        return None
    match = _SLASH.fullmatch(text)
    if match is None:
        raise SlashCommandError(
            "斜杠命令格式无效；请使用 /命令名 后接可选参数。"
        )
    return match.group(1), (match.group(2) or "").strip()


def _available_names(registry: Any) -> str:
    command_names = [
        command_id
        for command_id, command in registry.commands.items()
        if command.get("enabled", True) and command.get("user_invocable", True)
    ]
    skill_names = [
        skill_id
        for skill_id, skill in registry.skills.items()
        if skill.get("enabled", True) and skill.get("user_invocable", True)
    ]
    names = sorted(command_names) + sorted(skill_names)
    return "、".join(f"/{name}" for name in names[:10])


def _command_plan(
    command: dict[str, Any],
    arguments: str,
    registry: Any,
) -> SlashPlan:
    command_id = command["id"]
    pinned_skills = [
        registry.skills[skill_id]["name"]
        for skill_id in command.get("skill_ids", [])
        if skill_id in registry.skills
    ]
    selection = ComponentSelection(
        agent_id=command.get("agent_id"),
        skill_ids=list(command.get("skill_ids", [])),
        mcp_server_ids=list(command.get("mcp_server_ids", [])),
    )
    return SlashPlan(
        invocation=SlashInvocation(
            kind="command",
            id=command_id,
            name=command["name"],
            slash_name=command_id,
            arguments=arguments,
        ),
        question=registry.command_input(command, arguments),
        selection=selection,
        pinned_skills=pinned_skills,
        command=command,
    )


def _skill_plan(
    skill_id: str,
    skill: dict[str, Any],
    arguments: str,
) -> SlashPlan:
    return SlashPlan(
        invocation=SlashInvocation(
            kind="skill",
            id=skill_id,
            name=skill["name"],
            slash_name=skill_id,
            arguments=arguments,
        ),
        question=arguments or f"请使用 {skill['name']} 技能处理本次请求。",
        selection=ComponentSelection(skill_ids=[skill_id]),
        pinned_skills=[skill["name"]],
        command=None,
    )


def plan_slash_invocation(question: str, registry: Any) -> SlashPlan | None:
    """Resolve a leading `/name` to a command or skill execution plan."""
    parsed = _slash_token(question)
    if parsed is None:
        return None
    name, arguments = parsed

    command = registry.commands.get(name)
    if command is not None:
        if not command.get("enabled", True) or not command.get(
            "user_invocable", True
        ):
            raise SlashCommandError(f"命令 '/{name}' 当前不可用。")
        return _command_plan(command, arguments, registry)

    skill_id = registry.skill_aliases.get(name, name)
    skill = registry.skills.get(skill_id)
    if skill is not None:
        if not skill.get("enabled", True) or not skill.get(
            "user_invocable", True
        ):
            raise SlashCommandError(f"技能 '/{name}' 当前不可用。")
        return _skill_plan(skill_id, skill, arguments)

    available = _available_names(registry)
    hint = f"可用命令或技能：{available}" if available else "当前没有可用的命令或技能。"
    raise SlashCommandError(f"未知的斜杠命令 '/{name}'。{hint}")
