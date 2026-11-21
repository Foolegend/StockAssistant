import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langgraph.checkpoint.memory import MemorySaver

from src.services import checkpoint_store, db, retention
from src.services.thread_history import append_messages, load_messages


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.archive_dir = Path(self.temp_dir.name) / "conversation_history"
        self.archive_dir.mkdir()

        self.env_patch = patch.dict(
            os.environ,
            {
                "STOCK_CHECKPOINT_DB": self.db_path,
                "STOCK_HISTORY_RETENTION_DAYS": "30",
            },
        )
        self.env_patch.start()
        db.close_connection()
        self.archive_patch = patch.object(retention, "ARCHIVE_DIR", self.archive_dir)
        self.archive_patch.start()

    def tearDown(self):
        self.archive_patch.stop()
        self.env_patch.stop()
        checkpoint_store.close_checkpointer()
        db.close_connection()
        self.temp_dir.cleanup()

    def _backdate(self, thread_id: str, days: int) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
            timespec="seconds"
        )
        with db.get_connection() as connection:
            connection.execute(
                "UPDATE thread_messages SET created_at = ? WHERE thread_id = ?",
                (cutoff, thread_id),
            )

    def test_expired_threads_are_removed_and_fresh_ones_kept(self):
        append_messages("old", "user", "旧会话")
        append_messages("new", "user", "新会话")
        self._backdate("old", 40)

        summary = retention.cleanup_expired()

        self.assertTrue(summary["enabled"])
        self.assertEqual(summary["threads"], 1)
        self.assertEqual(load_messages("old"), [])
        self.assertEqual(len(load_messages("new")), 1)

    def test_checkpoints_for_expired_threads_are_cleared(self):
        append_messages("old", "user", "旧会话")
        self._backdate("old", 40)

        saver = MemorySaver()
        config = {
            "configurable": {
                "thread_id": "old",
                "checkpoint_ns": "",
                "checkpoint_id": "c1",
            }
        }
        saver.put(
            config,
            {
                "v": 4,
                "id": "c1",
                "ts": "2026-01-01T00:00:00+00:00",
                "channel_values": {"messages": []},
                "channel_versions": {"messages": "1"},
                "versions_seen": {},
            },
            {},
            {"messages": "1"},
        )

        with patch.object(retention, "get_checkpointer", return_value=saver):
            retention.cleanup_expired()

        self.assertEqual(list(saver.list({"configurable": {"thread_id": "old"}})), [])

    def test_old_archives_are_removed_by_mtime(self):
        old_archive = self.archive_dir / "old.md"
        new_archive = self.archive_dir / "new.md"
        old_archive.write_text("old", encoding="utf-8")
        new_archive.write_text("new", encoding="utf-8")

        old_epoch = (datetime.now(timezone.utc) - timedelta(days=60)).timestamp()
        os.utime(old_archive, (old_epoch, old_epoch))

        summary = retention.cleanup_expired()

        self.assertEqual(summary["archives"], 1)
        self.assertFalse(old_archive.exists())
        self.assertTrue(new_archive.exists())

    def test_retention_can_be_disabled(self):
        append_messages("old", "user", "旧会话")
        self._backdate("old", 400)

        with patch.dict(os.environ, {"STOCK_HISTORY_RETENTION_DAYS": "0"}):
            summary = retention.cleanup_expired()

        self.assertFalse(summary["enabled"])
        self.assertEqual(len(load_messages("old")), 1)

    def test_invalid_configuration_falls_back_to_default(self):
        with patch.dict(os.environ, {"STOCK_HISTORY_RETENTION_DAYS": "not-a-number"}):
            self.assertEqual(
                retention.retention_days(), retention.DEFAULT_RETENTION_DAYS
            )

    def test_cleanup_interval_is_clamped(self):
        with patch.dict(os.environ, {"STOCK_HISTORY_CLEANUP_HOURS": "0"}):
            self.assertGreaterEqual(retention.cleanup_interval_seconds(), 900)

    def test_worker_start_and_stop_are_safe(self):
        retention.start_worker()
        retention.start_worker()  # idempotent
        retention.stop_worker()
        retention.stop_worker()


class RetentionEndpointTests(unittest.TestCase):
    """The retention worker is wired into the FastAPI lifespan."""

    def test_lifespan_starts_and_stops_the_worker(self):
        import asyncio

        from src.api.chat import lifespan

        calls = []

        with patch.object(retention, "start_worker", side_effect=lambda: calls.append("start")), patch.object(
            retention, "stop_worker", side_effect=lambda: calls.append("stop")
        ):
            async def run():
                async with lifespan(None):
                    pass

            asyncio.run(run())

        self.assertEqual(calls, ["start", "stop"])


if __name__ == "__main__":
    unittest.main()
