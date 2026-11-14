import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest

from src.runtime.encoding import decode_output
from src.runtime.logging_config import get_logger, preview
from src.services.hook_matcher import matches_hook


logger = get_logger(__name__)


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
        matched_any = False
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

            matched_any = True
            hook_env = os.environ.copy()
            hook_env["STOCK_PLUGIN_ROOT"] = self.working_directory
            command = hook["command"]
            if hook.get("shell", False) and os.name == "nt":
                command = command.replace(
                    "${STOCK_PLUGIN_ROOT}",
                    "%STOCK_PLUGIN_ROOT%",
                )
            started = time.perf_counter()
            logger.debug("hook start id=%s event=%s", hook_id, event)
            try:
                result = subprocess.run(
                    command,
                    cwd=self.working_directory,
                    env=hook_env,
                    input=payload.encode("utf-8"),
                    capture_output=True,
                    timeout=hook.get("timeout_seconds", 10),
                    check=False,
                    shell=hook.get("shell", False),
                )
                stderr = decode_output(result.stderr)
            except subprocess.TimeoutExpired as error:
                logger.error(
                    "hook timed out id=%s event=%s after %s s",
                    hook_id,
                    event,
                    hook.get("timeout_seconds", 10),
                )
                raise HookExecutionError(
                    f"Hook '{hook_id}' timed out after {hook.get('timeout_seconds', 10)} seconds"
                ) from error
            except OSError as error:
                logger.error("hook could not start id=%s event=%s: %s", hook_id, event, error)
                raise HookExecutionError(f"Could not start hook '{hook_id}': {error}") from error

            duration_ms = (time.perf_counter() - started) * 1000
            if result.returncode != 0:
                detail = stderr.strip()
                logger.error(
                    "hook failed id=%s event=%s exit=%d duration=%.1f ms stderr=%s",
                    hook_id,
                    event,
                    result.returncode,
                    duration_ms,
                    preview(detail or decode_output(result.stdout)),
                )
                suffix = f": {detail[:500]}" if detail else ""
                raise HookExecutionError(
                    f"Hook '{hook_id}' exited with code {result.returncode}{suffix}"
                )

            logger.info(
                "hook finished id=%s event=%s duration=%.1f ms stdout=%s",
                hook_id,
                event,
                duration_ms,
                preview(decode_output(result.stdout), limit=200),
            )

        if not matched_any:
            logger.debug("no hook matched event=%s", event)


_SKILL_PATH = re.compile(r"[/\\]([^/\\]+)[/\\]SKILL\.md$", re.IGNORECASE)


def _skills_referenced(args: Any) -> list[str]:
    """Skill directories opened by a tool call, e.g. ``.../skills/stock-analysis/SKILL.md``."""
    skill_ids: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            match = _SKILL_PATH.search(value)
            if match:
                skill_ids.append(match.group(1))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(args)
    return list(dict.fromkeys(skill_ids))


class HookToolMiddleware(AgentMiddleware):
    _TOOL_NAME_ALIASES = {
        "edit_file": "Edit",
        "execute": "Bash",
        "write_file": "Write",
    }

    def __init__(self, hooks: dict[str, dict[str, Any]], working_directory: str) -> None:
        self.hook_runner = HookRunner(hooks, working_directory)

    @staticmethod
    def _thread_id(request: ToolCallRequest) -> str:
        """Tool calls run in worker threads, so recover the conversation id
        from the run config instead of relying on context variables."""
        config = getattr(getattr(request, "runtime", None), "config", None) or {}
        configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
        return configurable.get("thread_id", "-")

    @staticmethod
    def _mcp_server(request: ToolCallRequest) -> str:
        metadata = getattr(getattr(request, "tool", None), "metadata", None) or {}
        if isinstance(metadata, dict):
            return str(metadata.get("mcp_server") or "-")
        return "-"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        tool_call = request.tool_call
        tool_name = tool_call["name"]
        thread_id = self._thread_id(request)
        mcp_server = self._mcp_server(request)
        started = time.perf_counter()
        for skill_id in _skills_referenced(tool_call.get("args", {})):
            logger.info("skill load skill=%s thread_id=%s via=%s", skill_id, thread_id, tool_name)
        logger.info(
            "tool start tool=%s mcp_server=%s thread_id=%s args=%s",
            tool_name,
            mcp_server,
            thread_id,
            preview(json.dumps(tool_call.get("args", {}), ensure_ascii=False, default=str)),
        )
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

        try:
            result = handler(request)
        except Exception:
            logger.exception(
                "tool failed tool=%s mcp_server=%s thread_id=%s duration=%.1f ms",
                tool_name,
                mcp_server,
                thread_id,
                (time.perf_counter() - started) * 1000,
            )
            raise

        tool_output = getattr(result, "content", str(result))
        if not isinstance(tool_output, str):
            tool_output = json.dumps(tool_output, ensure_ascii=False, default=str)
        logger.info(
            "tool finished tool=%s mcp_server=%s thread_id=%s duration=%.1f ms output=%s",
            tool_name,
            mcp_server,
            thread_id,
            (time.perf_counter() - started) * 1000,
            preview(tool_output),
        )
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
