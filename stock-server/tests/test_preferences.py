import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from src.services import db
from src.services import preferences as prefs
from src.services.preference_extractor import (
    _parse_json_object,
    extract_preferences,
)
from src.api.chat import (
    PreferenceRequest,
    PreferenceUpdateRequest,
    _extract_preferences,
    _system_prompt_with_preferences,
    create_agent_preference,
    delete_agent_preference,
    agent_preferences,
    export_agent_thread,
    update_agent_preference,
)
from src.agent.UnifiedAgent import default_system_prompt


class PreferenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.env_patch = patch.dict(os.environ, {"STOCK_CHECKPOINT_DB": self.db_path})
        self.env_patch.start()
        self.conn_patch = patch.object(db, "_connection", None)
        self.conn_patch.start()
        self.conn_patch2 = patch.object(db, "_connection_path", None)
        self.conn_patch2.start()

    def tearDown(self):
        db.close_connection()
        self.conn_patch2.stop()
        self.conn_patch.stop()
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def test_add_and_list_round_trip(self):
        record = prefs.add_preference("只关注 A 股", category="market")
        self.assertIsNotNone(record)
        self.assertEqual(record["content"], "只关注 A 股")
        self.assertEqual(record["category"], "market")
        self.assertEqual(record["source"], "manual")
        self.assertTrue(record["enabled"])

        listed = prefs.list_preferences()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], record["id"])

    def test_duplicates_are_not_stored_twice(self):
        first = prefs.add_preference("偏好短线")
        second = prefs.add_preference("偏好短线")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(prefs.list_preferences()), 1)

    def test_whitespace_is_normalized(self):
        record = prefs.add_preference("  只 关注   A股  ")
        self.assertEqual(record["content"], "只 关注 A股")

    def test_blank_content_is_rejected(self):
        self.assertIsNone(prefs.add_preference("   "))

    def test_unknown_category_falls_back_to_general(self):
        record = prefs.add_preference("随便记一条", category="nonsense")
        self.assertEqual(record["category"], "general")

    def test_update_content_and_category(self):
        record = prefs.add_preference("旧内容", category="general")
        updated = prefs.update_preference(record["id"], content="新内容", category="style")
        self.assertEqual(updated["content"], "新内容")
        self.assertEqual(updated["category"], "style")

    def test_disable_excludes_from_prompt_context(self):
        record = prefs.add_preference("我持有茅台", category="investment")
        prefs.update_preference(record["id"], enabled=False)

        self.assertEqual(prefs.build_preference_context(), "")
        self.assertEqual(len(prefs.enabled_preferences()), 0)
        # Still visible in the management list.
        self.assertEqual(len(prefs.list_preferences()), 1)

    def test_adding_a_disabled_preference_reenables_it(self):
        record = prefs.add_preference("保持简洁")
        prefs.update_preference(record["id"], enabled=False)
        again = prefs.add_preference("保持简洁")
        self.assertEqual(again["id"], record["id"])
        self.assertTrue(again["enabled"])

    def test_delete_removes_the_row(self):
        record = prefs.add_preference("临时偏好")
        self.assertTrue(prefs.delete_preference(record["id"]))
        self.assertEqual(prefs.list_preferences(), [])
        self.assertFalse(prefs.delete_preference(record["id"]))

    def test_context_labels_categories(self):
        prefs.add_preference("只做 A 股", category="market")
        prefs.add_preference("偏好短线", category="style")
        context = prefs.build_preference_context()
        self.assertIn("[市场] 只做 A 股", context)
        self.assertIn("[风格] 偏好短线", context)

    def test_content_is_capped(self):
        record = prefs.add_preference("x" * 500)
        self.assertLessEqual(len(record["content"]), prefs.MAX_CONTENT_CHARS)

    def test_signal_detection(self):
        self.assertTrue(prefs.has_preference_signal("记住我只做 A 股"))
        self.assertTrue(prefs.has_preference_signal("以后都用中文回答"))
        self.assertTrue(prefs.has_preference_signal("我持有茅台"))
        self.assertFalse(prefs.has_preference_signal("今天大盘怎么样"))
        self.assertFalse(prefs.has_preference_signal("你好"))


class PreferenceExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.env_patch = patch.dict(os.environ, {"STOCK_CHECKPOINT_DB": self.db_path})
        self.env_patch.start()
        with patch.object(db, "_connection", None), patch.object(
            db, "_connection_path", None
        ):
            pass

    def tearDown(self):
        db.close_connection()
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def test_parse_json_object_handles_fences_and_noise(self):
        self.assertEqual(
            _parse_json_object('```json\n{"preferences": ["a"]}\n```'),
            {"preferences": ["a"]},
        )
        self.assertEqual(
            _parse_json_object('好的，结果如下：{"preferences": ["b"]} 完毕'),
            {"preferences": ["b"]},
        )
        self.assertIsNone(_parse_json_object("not json at all"))
        self.assertIsNone(_parse_json_object(""))

    def test_extraction_is_skipped_without_a_signal(self):
        class ExplodingModel:
            def invoke(self, *args, **kwargs):
                raise AssertionError("model must not be called")

        stored = extract_preferences("今天大盘怎么样", "先看指数", model=ExplodingModel())
        self.assertEqual(stored, [])

    def test_extraction_stores_returned_preferences(self):
        class FakeResponse:
            content = '{"preferences": ["只关注 A 股", "偏好短线"]}'

        class FakeModel:
            def invoke(self, *args, **kwargs):
                return FakeResponse()

        stored = extract_preferences("记住我只关注 A 股", "好的", model=FakeModel())

        self.assertEqual(len(stored), 2)
        contents = {item["content"] for item in prefs.list_preferences()}
        self.assertEqual(contents, {"只关注 A 股", "偏好短线"})
        self.assertEqual(stored[0]["source"], "auto")

    def test_extraction_tolerates_a_broken_model(self):
        class BrokenModel:
            def invoke(self, *args, **kwargs):
                raise RuntimeError("model down")

        self.assertEqual(extract_preferences("记住我喜欢蓝色", "好", model=BrokenModel()), [])

    def test_extraction_tolerates_non_json_answers(self):
        class ChattyModel:
            def invoke(self, *args, **kwargs):
                class R:
                    content = "我觉得没有需要记住的偏好。"
                return R()

        self.assertEqual(extract_preferences("记住我喜欢蓝色", "好", model=ChattyModel()), [])


class PromptInjectionTests(unittest.TestCase):
    def test_enabled_preferences_are_added_to_the_prompt(self):
        with patch(
            "src.api.chat.build_preference_context",
            return_value="- [市场] 只关注 A 股\n- [风格] 偏好短线",
        ):
            prompt = _system_prompt_with_preferences()

        self.assertIn("用户长期偏好", prompt)
        self.assertIn("只关注 A 股", prompt)
        self.assertIn("偏好短线", prompt)
        self.assertIn("以本次要求为准", prompt)

    def test_prompt_is_unchanged_without_preferences(self):
        with patch("src.api.chat.build_preference_context", return_value=""):
            prompt = _system_prompt_with_preferences()
        self.assertEqual(prompt, default_system_prompt())

    def test_extraction_runs_in_the_background_when_signalled(self):
        import threading

        called = threading.Event()
        with patch(
            "src.api.chat.extract_preferences",
            side_effect=lambda question, answer: called.set(),
        ):
            _extract_preferences("记住我只关注 A 股", "好的，已记住")

        self.assertTrue(called.wait(3), "extraction did not run")

    def test_extraction_is_skipped_without_a_signal(self):
        import time

        with patch("src.api.chat.extract_preferences") as extractor:
            _extract_preferences("今天大盘怎么样", "先看指数")
            time.sleep(0.2)
        extractor.assert_not_called()

    def test_extraction_failures_are_swallowed(self):
        import threading

        done = threading.Event()

        def boom(question, answer):
            try:
                raise RuntimeError("extractor down")
            finally:
                done.set()

        with patch("src.api.chat.extract_preferences", side_effect=boom):
            _extract_preferences("记住我喜欢蓝色", "好")

        self.assertTrue(done.wait(3))


class PreferenceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.env_patch = patch.dict(os.environ, {"STOCK_CHECKPOINT_DB": self.db_path})
        self.env_patch.start()
        db.close_connection()

    def tearDown(self):
        db.close_connection()
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def test_create_list_update_delete_flow(self):
        created = create_agent_preference(
            PreferenceRequest(content="只关注 A 股", category="market")
        )
        preference_id = created["data"]["preference"]["id"]

        listed = agent_preferences()
        self.assertEqual(len(listed["data"]["preferences"]), 1)

        updated = update_agent_preference(
            preference_id, PreferenceUpdateRequest(enabled=False)
        )
        self.assertFalse(updated["data"]["preference"]["enabled"])

        delete_agent_preference(preference_id)
        self.assertEqual(agent_preferences()["data"]["preferences"], [])

    def test_creating_blank_preference_returns_400(self):
        with self.assertRaises(HTTPException) as error:
            create_agent_preference(PreferenceRequest(content="   "))
        self.assertEqual(error.exception.status_code, 400)

    def test_updating_a_missing_preference_returns_404(self):
        with self.assertRaises(HTTPException) as error:
            update_agent_preference(999, PreferenceUpdateRequest(enabled=False))
        self.assertEqual(error.exception.status_code, 404)

    def test_deleting_a_missing_preference_returns_404(self):
        with self.assertRaises(HTTPException) as error:
            delete_agent_preference(999)
        self.assertEqual(error.exception.status_code, 404)


class ThreadExportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.env_patch = patch.dict(os.environ, {"STOCK_CHECKPOINT_DB": self.db_path})
        self.env_patch.start()
        db.close_connection()
        from src.services.thread_history import append_messages

        append_messages("export-t1", "user", "分析 TSLA")
        append_messages("export-t1", "assistant", "## 结论\n\n看好。")

    def tearDown(self):
        db.close_connection()
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def test_markdown_export(self):
        response = export_agent_thread("export-t1", format="markdown")
        body = response.body.decode("utf-8")
        self.assertEqual(response.media_type, "text/markdown; charset=utf-8")
        self.assertIn("# 会话记录 export-t1", body)
        self.assertIn("## 我", body)
        self.assertIn("## 研究助手", body)
        self.assertIn("分析 TSLA", body)
        self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_json_export(self):
        response = export_agent_thread("export-t1", format="json")
        import json

        payload = json.loads(response.body)
        self.assertEqual(payload["thread_id"], "export-t1")
        self.assertEqual(len(payload["messages"]), 2)
        self.assertEqual(payload["messages"][0]["role"], "user")

    def test_unknown_thread_returns_404(self):
        with self.assertRaises(HTTPException) as error:
            export_agent_thread("nope", format="markdown")
        self.assertEqual(error.exception.status_code, 404)

    def test_unsupported_format_returns_400(self):
        with self.assertRaises(HTTPException) as error:
            export_agent_thread("export-t1", format="pdf")
        self.assertEqual(error.exception.status_code, 400)

    def test_list_endpoint_returns_summaries(self):
        from src.api.chat import list_agent_threads

        response = list_agent_threads(limit=10, offset=0)
        threads = response["data"]["threads"]

        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0]["thread_id"], "export-t1")
        self.assertEqual(threads[0]["title"], "分析 TSLA")
        self.assertEqual(threads[0]["message_count"], 2)

    def test_list_endpoint_clamps_the_limit(self):
        from src.api.chat import list_agent_threads

        response = list_agent_threads(limit=99999, offset=-5)
        self.assertEqual(response["code"], 0)

    def test_messages_endpoint_returns_content_and_sources(self):
        from src.api.chat import get_agent_thread_messages

        response = get_agent_thread_messages("export-t1")
        messages = response["data"]["messages"]

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")
        self.assertIn("created_at", messages[0])
        # The assistant answer in setUp has no link, so sources is empty.
        self.assertEqual(messages[1]["sources"], [])

    def test_messages_endpoint_extracts_links_from_the_answer(self):
        from src.api.chat import get_agent_thread_messages
        from src.services.thread_history import append_messages

        append_messages("links-t1", "user", "找资料")
        append_messages(
            "links-t1",
            "assistant",
            "参考 [公告](https://example.com/a) 与 [研报](https://example.com/b)。",
        )

        response = get_agent_thread_messages("links-t1")
        sources = response["data"]["messages"][1]["sources"]

        self.assertEqual(
            sources,
            [
                {"title": "公告", "url": "https://example.com/a"},
                {"title": "研报", "url": "https://example.com/b"},
            ],
        )

    def test_messages_endpoint_404_for_unknown_thread(self):
        from src.api.chat import get_agent_thread_messages

        with self.assertRaises(HTTPException) as error:
            get_agent_thread_messages("never-seen")
        self.assertEqual(error.exception.status_code, 404)

    def test_messages_endpoint_400_for_blank_id(self):
        from src.api.chat import get_agent_thread_messages

        with self.assertRaises(HTTPException) as error:
            get_agent_thread_messages("   ")
        self.assertEqual(error.exception.status_code, 400)

    def test_bulk_markdown_export_contains_every_thread(self):
        from src.api.chat import export_all_threads
        from src.services.thread_history import append_messages

        append_messages("export-t2", "user", "第二条会话")
        append_messages("export-t2", "assistant", "好的")

        response = export_all_threads(format="markdown")
        body = response.body.decode("utf-8")

        self.assertIn("# 全部会话导出", body)
        self.assertIn("## 分析 TSLA", body)
        self.assertIn("## 第二条会话", body)
        self.assertIn("export-t1", body)
        self.assertIn("export-t2", body)
        self.assertIn("---", body)
        self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_bulk_json_export_contains_every_thread(self):
        import json

        from src.api.chat import export_all_threads
        from src.services.thread_history import append_messages

        append_messages("export-t2", "user", "第二条会话")

        response = export_all_threads(format="json")
        payload = json.loads(response.body)

        self.assertIn("exported_at", payload)
        ids = {item["thread_id"] for item in payload["threads"]}
        self.assertEqual(ids, {"export-t1", "export-t2"})
        for item in payload["threads"]:
            self.assertIn("messages", item)

    def test_bulk_export_rejects_unknown_format(self):
        from src.api.chat import export_all_threads

        with self.assertRaises(HTTPException) as error:
            export_all_threads(format="pdf")
        self.assertEqual(error.exception.status_code, 400)

    def test_bulk_export_404_when_there_is_nothing(self):
        from src.api.chat import export_all_threads

        with patch("src.api.chat.list_threads", return_value=[]):
            with self.assertRaises(HTTPException) as error:
                export_all_threads()
        self.assertEqual(error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
