import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.extension_registry import ExtensionRegistry
from src.services.hook_runner import HookExecutionError, HookRunner


HOOK_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".stock"
    / "hooks"
    / "scripts"
    / "validate-stock-data.js"
)
NODE_AVAILABLE = subprocess.run(
    ["node", "--version"], capture_output=True, check=False
).returncode == 0


def _run_hook_output(output: str) -> subprocess.CompletedProcess[str]:
    context = {"tool_output": {"output": output}}
    return subprocess.run(
        ["node", str(HOOK_SCRIPT)],
        input=json.dumps(context),
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )


def _run_hook(latest_bar_date: str | None) -> subprocess.CompletedProcess[str]:
    recent_bars = [{"date": latest_bar_date}] if latest_bar_date is not None else [{}]
    stock_data = {
        "analysis_date": datetime.now().strftime("%Y-%m-%d"),
        "stocks": [{"code": "TSLA", "recent_bars": recent_bars}],
    }
    return _run_hook_output(f"[INFO] data sources ready\n{json.dumps(stock_data)}")


class StockDataFreshnessHookTests(unittest.TestCase):
    @unittest.skipUnless(NODE_AVAILABLE, "Node.js is required for the hook")
    def test_accepts_recent_price_bars(self):
        today = datetime.now().date()

        result = _run_hook(today.strftime("%Y-%m-%d"))

        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(NODE_AVAILABLE, "Node.js is required for the hook")
    def test_rejects_price_bars_older_than_seven_days(self):
        stale_date = (datetime.now().date() - timedelta(days=8)).strftime("%Y-%m-%d")

        result = _run_hook(stale_date)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("maximum is 7", result.stderr)

    @unittest.skipUnless(NODE_AVAILABLE, "Node.js is required for the hook")
    def test_rejects_missing_price_bar_date(self):
        result = _run_hook(None)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing or invalid price-bar date", result.stderr)

    @unittest.skipUnless(NODE_AVAILABLE, "Node.js is required for the hook")
    def test_skips_validation_when_the_tool_produced_no_output(self):
        result = _run_hook_output("<no output>\n\nExit code: 2")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipping validation", result.stdout)

    @unittest.skipUnless(NODE_AVAILABLE, "Node.js is required for the hook")
    def test_skips_validation_when_output_was_redirected_to_a_file(self):
        result = _run_hook_output("exit=0")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no stock JSON payload", result.stdout)

    @unittest.skipUnless(NODE_AVAILABLE, "Node.js is required for the hook")
    def test_registered_post_tool_hook_blocks_stale_data(self):
        registry = ExtensionRegistry()
        skills_root = (
            Path(registry.project_root)
            / "stock-server"
            / ".stock"
            / "skills"
        )
        runner = HookRunner(
            registry.hooks,
            str(skills_root),
            plugin_root=str(registry.project_root),
        )
        stale_date = (datetime.now().date() - timedelta(days=8)).strftime("%Y-%m-%d")
        context = {
            "tool": "Bash",
            "tool_input": {
                "command": "python /tmp/stock_data_fetcher.py --stocks TSLA"
            },
            "tool_output": {
                "output": json.dumps({
                    "analysis_date": datetime.now().strftime("%Y-%m-%d"),
                    "stocks": [{
                        "code": "TSLA",
                        "recent_bars": [{"date": stale_date}],
                    }],
                })
            },
        }

        with self.assertRaisesRegex(HookExecutionError, "maximum is 7"):
            runner.run(
                "PostToolUse",
                context,
                agent_id="stock-analyst",
                command_id=None,
            )


if __name__ == "__main__":
    unittest.main()
