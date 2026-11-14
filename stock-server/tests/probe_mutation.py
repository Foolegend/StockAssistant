"""Mutation check: temporarily break hook_matcher logic and confirm tests fail."""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "services" / "hook_matcher.py"
ORIGINAL = SOURCE.read_text(encoding="utf-8")

MUTATIONS = [
    (
        "wildcard no longer matches everything",
        'return _MatcherParser(matcher or "*", context).evaluate()',
        'return _MatcherParser(matcher or "tool == \\"__never__\\"", context).evaluate()',
    ),
    (
        "equality becomes case-insensitive",
        "return actual == expected",
        "return str(actual).casefold() == str(expected).casefold()",
    ),
    (
        "matches operator becomes exact equality",
        'return re.search(expected, "" if actual is None else str(actual)) is not None',
        'return str(actual) == expected',
    ),
    (
        "and becomes or",
        "result = result and right",
        "result = result or right",
    ),
    (
        "or becomes and",
        "result = result or right",
        "result = result and right",
    ),
    (
        "negation is dropped",
        "return not self._parse_unary()",
        "return self._parse_unary()",
    ),
    (
        "missing nested field resolves to empty string instead of None",
        'return re.search(expected, "" if actual is None else str(actual)) is not None',
        'return re.search(expected, "" if actual is None else str(actual)) is not None and actual is not None',
    ),
    (
        "quoted literal requirement removed",
        'if not literal_token or not literal_token.startswith(\'"\'):',
        'if False:',
    ),
    (
        "field validation is loosened",
        'if not variable or not re.fullmatch(r"tool(?:_input\\.[A-Za-z_][A-Za-z0-9_]*)?", variable):',
        'if not variable:',
    ),
    (
        "unexpected trailing tokens are ignored",
        "if self.index != len(self.tokens):",
        "if False:",
    ),
]

root = Path(__file__).resolve().parents[1]
results = []
try:
    for name, old, new in MUTATIONS:
        if old not in ORIGINAL:
            results.append((name, "MUTATION-NOT-APPLIED"))
            continue
        SOURCE.write_text(ORIGINAL.replace(old, new, 1), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_hook_matcher"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        caught = proc.returncode != 0
        results.append((name, "CAUGHT" if caught else "SURVIVED"))
finally:
    SOURCE.write_text(ORIGINAL, encoding="utf-8")

print()
for name, status in results:
    print(f"{status:20} {name}")
survived = [name for name, status in results if status != "CAUGHT"]
print()
print(f"mutations: {len(results)}, caught: {len(results) - len(survived)}, survived: {len(survived)}")
if survived:
    print("SURVIVORS:", survived)
