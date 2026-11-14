import json
import os
import subprocess
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest

from src.services.hook_matcher import matches_hook


class HookExecutionError(RuntimeError):
    pass


class HookRunner:
    def __init__(self, hooks: dict[str, dict[str, Any]], working_directory: str) -> None:
        self.hooks = hooks
        self.working_directory = working_directory

    def run(
        self,
        event: str,
        context: dict[str, Any],
        *,
        agent_id: str,
        command_id: str | None,
    ) -> None:
        payload = json.dumps(
            {**context, "event": event},
            ensure_ascii=False,
        )
        for hook_id, hook in self.hooks.items():
            if not hook.get("enabled", True) or hook["event"] != event:
                continue
            matcher = hook.get("matcher")
            if matcher and not matches_hook(matcher, context):
                continue
            if hook.get("agent_ids") and agent_id not in hook["agent_ids"]:
                continue
            if hook.get("command_ids") and command_id not in hook["command_ids"]:
                continue

            hook_env = os.environ.copy()
            hook_env["STOCK_PLUGIN_ROOT"] = self.working_directory
            command = hook["command"]
            if hook.get("shell", False) and os.name == "nt":
                command = command.replace(
                    "${STOCK_PLUGIN_ROOT}",
                    "%STOCK_PLUGIN_ROOT%",
                )
            try:
                result = subprocess.run(
                    command,
                    cwd=self.working_directory,
                    env=hook_env,
                    input=payload,
                    capture_output=True,
                    text=True,
                    timeout=hook.get("timeout_seconds", 10),
                    check=False,
                    shell=hook.get("shell", False),
                )
            except subprocess.TimeoutExpired as error:
                raise HookExecutionError(
                    f"Hook '{hook_id}' timed out after {hook.get('timeout_seconds', 10)} seconds"
                ) from error
            except OSError as error:
                raise HookExecutionError(f"Could not start hook '{hook_id}': {error}") from error

            if result.returncode != 0:
                detail = result.stderr.strip()
                suffix = f": {detail[:500]}" if detail else ""
                raise HookExecutionError(
                    f"Hook '{hook_id}' exited with code {result.returncode}{suffix}"
                )


class HookToolMiddleware(AgentMiddleware):
    _TOOL_NAME_ALIASES = {
        "edit_file": "Edit",
        "execute": "Bash",
        "write_file": "Write",
    }

    def __init__(self, hooks: dict[str, dict[str, Any]], working_directory: str) -> None:
        self.hook_runner = HookRunner(hooks, working_directory)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        tool_call = request.tool_call
        tool_name = tool_call["name"]
        hook_context = {
            "tool": self._TOOL_NAME_ALIASES.get(tool_name, tool_name),
            "tool_name": self._TOOL_NAME_ALIASES.get(tool_name, tool_name),
            "tool_input": tool_call.get("args", {}),
        }
        self.hook_runner.run(
            "PreToolUse",
            hook_context,
            agent_id="",
            command_id=None,
        )

        result = handler(request)

        tool_output = getattr(result, "content", str(result))
        if not isinstance(tool_output, str):
            tool_output = json.dumps(tool_output, ensure_ascii=False, default=str)
        self.hook_runner.run(
            "PostToolUse",
            {
                **hook_context,
                "tool_output": {"output": tool_output},
                "tool_response": tool_output,
            },
            agent_id="",
            command_id=None,
        )
        return result
