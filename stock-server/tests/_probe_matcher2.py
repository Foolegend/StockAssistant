import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.hook_matcher import matches_hook

cases = [
    (None, {}),
    ("   ", {"tool": "Bash"}),
    ('tool == "Bash" && (tool_input.file_path matches "\\\\.(md|txt)$")', {"tool": "Bash", "tool_input": {"file_path": "a.md"}}),
    ('tool == "Bash" && (tool_input.file_path matches "\\\\.(md|txt)$")', {"tool": "Bash", "tool_input": {"file_path": "a.py"}}),
    ('tool_input.value == "5"', {"tool_input": {"value": 5}}),
    ('tool_input.value == "5"', {"tool_input": {"value": "5"}}),
    ('tool_input.flag == "true"', {"tool_input": {"flag": True}}),
    ('tool_input.count matches "12"', {"tool_input": {"count": 123}}),
    ('tool == "Bash" || tool == "Write" && tool_input.x == "1"', {"tool": "Write", "tool_input": {"x": "1"}}),
    ('tool == "Bash" || tool == "Write" && tool_input.x == "1"', {"tool": "Write", "tool_input": {"x": "2"}}),
    ('!(tool == "Bash" || tool == "Write")', {"tool": "Read"}),
    ('tool == "Bash" == "Write"', {"tool": "Bash"}),
    ('tool == "Bash" & tool == "Write"', {"tool": "Bash"}),
    ('tool == "say \\"hi\\""', {"tool": 'say "hi"'}),
    ('tool == "Bash" #comment', {"tool": "Bash"}),
    ('tool_input.a_b1 == "x"', {"tool_input": {"a_b1": "x"}}),
    ('tool_input.a-b == "x"', {"tool_input": {}}),
    ('* && tool == "Bash"', {"tool": "Bash"}),
    ('* && tool == "Write"', {"tool": "Bash"}),
]

for expr, ctx in cases:
    try:
        print(repr(expr), "->", matches_hook(expr, ctx))
    except Exception as error:  # noqa: BLE001
        print(repr(expr), "-> ERROR", type(error).__name__, error)
