import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services import checkpoint_store
from src.services.thread_history import (
    append_messages,
    build_history_context,
    clear_messages,
    close_history_connection,
    load_messages,
)


class CheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "checkpoints.db")
        self.env_patch = patch.dict(
            os.environ, {"STOCK_CHECKPOINT_DB": self.db_path}
        )
        self.env_patch.start()
        # Reset the module-level singleton so the new path takes effect.
        self.reset_patch = patch.object(checkpoint_store, "_saver", None)
        self.reset_patch.start()

    def tearDown(self):
        checkpoint_store.close_checkpointer()
        self.reset_patch.stop()
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def test_uses_sqlite_when_the_database_is_reachable(self):
        saver = checkpoint_store.get_checkpointer()
        self.assertEqual(checkpoint_store.checkpoint_store_kind(), "sqlite")
        self.assertTrue(hasattr(saver, "delete_thread"))

    def test_the_same_saver_is_returned_on_every_call(self):
        first = checkpoint_store.get_checkpointer()
        second = checkpoint_store.get_checkpointer()
        self.assertIs(first, second)

    def test_falls_back_to_memory_when_the_path_is_unusable(self):
        with patch.object(
            checkpoint_store,
            "_build_saver",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                checkpoint_store.get_checkpointer()

    def test_memory_marker_selects_in_memory_storage(self):
        with patch.dict(os.environ, {"STOCK_CHECKPOINT_DB": ":memory:"}):
            saver = checkpoint_store.get_checkpointer()
            self.assertEqual(checkpoint_store.checkpoint_store_kind(), "memory")
            self.assertTrue(hasattr(saver, "delete_thread"))


class ThreadHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "checkpoints.db")
        self.env_patch = patch.dict(
            os.environ, {"STOCK_CHECKPOINT_DB": self.db_path}
        )
        self.env_patch.start()

    def tearDown(self):
        close_history_connection()
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def test_messages_round_trip_in_order(self):
        append_messages("t1", "user", "第一条")
        append_messages("t1", "assistant", "第二条")
        append_messages("t1", "user", "第三条")

        messages = load_messages("t1")

        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "第一条"},
                {"role": "assistant", "content": "第二条"},
                {"role": "user", "content": "第三条"},
            ],
        )

    def test_threads_are_isolated_from_each_other(self):
        append_messages("t1", "user", "A 的消息")
        append_messages("t2", "user", "B 的消息")

        self.assertEqual(len(load_messages("t1")), 1)
        self.assertEqual(load_messages("t1")[0]["content"], "A 的消息")
        self.assertEqual(load_messages("t2")[0]["content"], "B 的消息")

    def test_history_survives_a_new_connection(self):
        append_messages("t1", "user", "跨连接保留")

        # A fresh connection sees the same rows.
        self.assertEqual(load_messages("t1")[0]["content"], "跨连接保留")

    def test_blank_content_is_ignored(self):
        append_messages("t1", "user", "   ")
        append_messages("", "user", "没有 thread id")

        self.assertEqual(load_messages("t1"), [])

    def test_context_renders_the_conversation_oldest_first(self):
        append_messages("t1", "user", "我叫张三")
        append_messages("t1", "assistant", "记住了")

        context = build_history_context("t1")

        self.assertIn("用户：我叫张三", context)
        self.assertIn("助手：记住了", context)
        self.assertLess(context.index("我叫张三"), context.index("记住了"))

    def test_context_is_empty_for_an_unknown_thread(self):
        self.assertEqual(build_history_context("never-seen"), "")

    def test_context_caps_the_number_of_messages(self):
        for index in range(60):
            append_messages("t1", "user", f"消息 {index}")

        self.assertLessEqual(len(load_messages("t1")), 40)

    def test_clear_removes_only_the_given_thread(self):
        append_messages("t1", "user", "要删除")
        append_messages("t2", "user", "要保留")

        removed = clear_messages("t1")

        self.assertEqual(removed, 1)
        self.assertEqual(load_messages("t1"), [])
        self.assertEqual(len(load_messages("t2")), 1)

    def test_clear_of_an_unknown_thread_is_harmless(self):
        self.assertEqual(clear_messages("never-seen"), 0)


if __name__ == "__main__":
    unittest.main()
