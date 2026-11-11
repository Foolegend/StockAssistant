import json
import os
import subprocess
from typing import Any


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
        command_id: str,
    ) -> None:
        payload = json.dumps(
            {**context, "event": event},
            ensure_ascii=False,
        )
        for hook_id, hook in self.hooks.items():
            if not hook.get("enabled", True) or hook["event"] != event:
                continue
            if hook.get("agent_ids") and agent_id not in hook["agent_ids"]:
                continue
            if hook.get("command_ids") and command_id not in hook["command_ids"]:
                continue

            try:
                result = subprocess.run(
                    hook["command"],
                    cwd=self.working_directory,
                    env=os.environ.copy(),
                    input=payload,
                    capture_output=True,
                    text=True,
                    timeout=hook.get("timeout_seconds", 10),
                    check=False,
                    shell=False,
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
