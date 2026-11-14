import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.hook_matcher import matches_hook, validate_hook_matcher

cases = [
    'tool && "x"',
    'tool "x"',
    'tool || "x"',
    'tool ! "x"',
    'tool ( "x"',
    'tool * "x"',
    'tool matches "git"',
    'tool matches "["',
    'tool == "["',
]

for expr in cases:
    try:
        print(repr(expr), "->", matches_hook(expr, {"tool": "Bash"}))
    except Exception as error:  # noqa: BLE001
        print(repr(expr), "-> ERROR", type(error).__name__, error)

print("--- validate invalid regex ---")
try:
    validate_hook_matcher('tool matches "["')
    print("valid")
except Exception as error:  # noqa: BLE001
    print("ERROR", type(error).__name__, error)
