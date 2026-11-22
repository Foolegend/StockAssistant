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
            [(item["role"], item["content"]) for item in messages],
            [
                ("user", "第一条"),
                ("assistant", "第二条"),
                ("user", "第三条"),
            ],
        )
        self.assertTrue(all(item["created_at"] for item in messages))

    def test_message_metadata_round_trips(self):
        append_messages(
            "t1",
            "assistant",
            "答复",
            metadata={
                "usage": {
                    "command": {"id": "stock-report", "name": "/stock-report"},
                    "skills": [{"id": "stock-analysis", "name": "stock-analysis"}],
                }
            },
        )

        messages = load_messages("t1")

        self.assertEqual(
            messages[0]["metadata"]["usage"]["command"]["id"],
            "stock-report",
        )
        self.assertEqual(
            messages[0]["metadata"]["usage"]["skills"][0]["name"],
            "stock-analysis",
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

    def test_list_threads_summarises_conversations(self):
        from src.services.thread_history import list_threads

        append_messages("t1", "user", "分析 TSLA 的技术面")
        append_messages("t1", "assistant", "看起来不错")
        append_messages("t2", "user", "另一条会话")

        threads = list_threads()

        self.assertEqual(len(threads), 2)
        by_id = {item["thread_id"]: item for item in threads}
        self.assertEqual(by_id["t1"]["title"], "分析 TSLA 的技术面")
        self.assertEqual(by_id["t1"]["message_count"], 2)
        self.assertTrue(by_id["t1"]["updated_at"] >= by_id["t1"]["created_at"])

    def test_list_threads_truncates_long_titles(self):
        from src.services.thread_history import list_threads

        append_messages("t1", "user", "很长的标题" * 20)
        title = list_threads()[0]["title"]
        self.assertLessEqual(len(title), 42)
        self.assertTrue(title.endswith("…"))

    def test_list_threads_respects_limit_and_offset(self):
        from src.services.thread_history import list_threads

        for index in range(5):
            append_messages(f"t{index}", "user", f"会话 {index}")

        first_page = list_threads(limit=2, offset=0)
        second_page = list_threads(limit=2, offset=2)

        self.assertEqual(len(first_page), 2)
        self.assertEqual(len(second_page), 2)
        self.assertNotEqual(
            {item["thread_id"] for item in first_page},
            {item["thread_id"] for item in second_page},
        )

    def test_stale_threads_are_found_by_last_message_time(self):
        from src.services.thread_history import stale_thread_ids

        append_messages("old", "user", "旧会话")
        append_messages("new", "user", "新会话")

        future = "2999-01-01T00:00:00+00:00"
        self.assertIn("old", stale_thread_ids(future))
        self.assertIn("new", stale_thread_ids(future))

        past = "1970-01-01T00:00:00+00:00"
        self.assertEqual(stale_thread_ids(past), [])

    def test_delete_threads_removes_only_the_given_threads(self):
        from src.services.thread_history import delete_threads

        append_messages("t1", "user", "删我")
        append_messages("t2", "user", "留我")

        removed = delete_threads(["t1"])

        self.assertEqual(removed, 1)
        self.assertEqual(load_messages("t1"), [])
        self.assertEqual(len(load_messages("t2")), 1)


if __name__ == "__main__":
    unittest.main()
