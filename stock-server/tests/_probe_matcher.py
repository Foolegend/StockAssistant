import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.hook_matcher import matches_hook, validate_hook_matcher

cases = [
    ("*", {}),
    ("", {"tool": "Bash"}),
    ('tool == "Bash"', {"tool": "Bash"}),
    ('tool == "Bash"', {"tool": "Write"}),
    ('tool_input.command matches "git push"', {"tool_input": {"command": "git push --force"}}),
    ('!(tool == "Bash")', {"tool": "Write"}),
    ('tool == "Bash" || tool == "Write"', {"tool": "Write"}),
    ('tool == "Bash" && tool_input.command matches "git"', {"tool": "Bash", "tool_input": {"command": "git push"}}),
    ('tool == "Bash" && tool_input.command matches "git"', {"tool": "Bash", "tool_input": {"command": "ls"}}),
    ('tool_name == "Bash"', {"tool_name": "Bash"}),
    ('tool == "Bash" &&', {"tool": "Bash"}),
    ('tool != "Bash"', {"tool": "Bash"}),
    ('foo == "Bash"', {"foo": "Bash"}),
    ('tool == Bash', {"tool": "Bash"}),
    ('tool == "Bash" extra', {"tool": "Bash"}),
    ('tool_input.missing == "x"', {"tool_input": {}}),
    ('tool_input.command matches "git"', {"tool_input": {}}),
]

for expr, ctx in cases:
    try:
        print(repr(expr), "->", matches_hook(expr, ctx))
    except Exception as error:  # noqa: BLE001
        print(repr(expr), "-> ERROR", type(error).__name__, error)

print("--- validate ---")
for expr in ['tool == "Bash"', 'tool != "Bash"', '*', '', '(tool == "Bash")']:
    try:
        validate_hook_matcher(expr)
        print(repr(expr), "-> valid")
    except Exception as error:  # noqa: BLE001
        print(repr(expr), "-> ERROR", type(error).__name__, error)
