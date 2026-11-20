import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException
from langgraph.checkpoint.memory import MemorySaver

from src.agent.UnifiedAgent import delete_thread_memory
from src.api.chat import delete_agent_thread
from src.services.thread_cleanup import (
    collect_thread_archive_paths,
    delete_archive_files,
    resolve_archive_file,
)


def _checkpoint(channel_values: dict) -> dict:
    return {
        "v": 4,
        "id": "c1",
        "ts": "2026-01-01T00:00:00+00:00",
        "channel_values": channel_values,
        "channel_versions": {key: "1" for key in channel_values},
        "versions_seen": {},
    }


class ThreadCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.history = self.root / "conversation_history"
        self.history.mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _put(self, saver: MemorySaver, thread_id: str, channel_values: dict):
        config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": "c1",
            }
        }
        saver.put(
            config,
            _checkpoint(channel_values),
            {},
            {key: "1" for key in channel_values},
        )

    def test_collects_summarization_and_eviction_paths_from_state(self):
        saver = MemorySaver()
        self._put(
            saver,
            "thread-1",
            {
                "messages": [
                    {
                        "type": "human",
                        "additional_kwargs": {
                            "lc_evicted_to": "/conversation_history/offload.md"
                        },
                    }
                ],
                "_summarization_event": {
                    "file_path": "/conversation_history/summary.md"
                },
            },
        )

        paths = collect_thread_archive_paths(saver, "thread-1")

        self.assertIn("/conversation_history/summary.md", paths)
        self.assertIn("/conversation_history/offload.md", paths)

    def test_ignores_threads_without_checkpoints(self):
        saver = MemorySaver()
        self.assertEqual(collect_thread_archive_paths(saver, "missing"), [])

    def test_resolve_only_accepts_paths_inside_the_history_directory(self):
        target = resolve_archive_file("/conversation_history/a.md", self.root)
        self.assertEqual(target, (self.history / "a.md").resolve())

        # Paths that escape the history directory must be rejected.
        self.assertIsNone(resolve_archive_file("/other/a.md", self.root))
        self.assertIsNone(resolve_archive_file("/conversation_history/../a.md", self.root))
        self.assertIsNone(resolve_archive_file("", self.root))

    def test_delete_archive_files_removes_only_known_history_files(self):
        archive = self.history / "summary.md"
        archive.write_text("summary", encoding="utf-8")
        outside = self.root / "keep.md"
        outside.write_text("keep", encoding="utf-8")

        removed = delete_archive_files(
            ["/conversation_history/summary.md", "/keep.md"], self.root
        )

        self.assertEqual(len(removed), 1)
        self.assertFalse(archive.exists())
        self.assertTrue(outside.exists())

    def test_delete_thread_memory_clears_checkpoints_and_archives(self):
        saver = MemorySaver()
        self._put(
            saver,
            "thread-1",
            {
                "messages": [],
                "_summarization_event": {
                    "file_path": "/conversation_history/summary.md"
                },
            },
        )
        archive = self.history / "summary.md"
        archive.write_text("summary", encoding="utf-8")

        with patch(
            "src.agent.UnifiedAgent._cached_agents",
            return_value=[_FakeAgent(saver, self.root)],
        ):
            summary = delete_thread_memory("thread-1")

        self.assertEqual(summary["cleared_agents"], 1)
        self.assertEqual(summary["deleted_files"], 1)
        self.assertFalse(archive.exists())
        self.assertEqual(list(saver.list({"configurable": {"thread_id": "thread-1"}})), [])

    def test_delete_thread_memory_sweeps_every_cached_agent(self):
        first = MemorySaver()
        second = MemorySaver()
        for saver in (first, second):
            self._put(saver, "thread-1", {"messages": []})

        agents = [_FakeAgent(first), _FakeAgent(second)]
        with patch("src.agent.UnifiedAgent._cached_agents", return_value=agents):
            summary = delete_thread_memory("thread-1")

        self.assertEqual(summary["cleared_agents"], 2)
        for saver in (first, second):
            self.assertEqual(list(saver.list({"configurable": {"thread_id": "thread-1"}})), [])

    def test_api_endpoint_rejects_a_blank_thread_id(self):
        with self.assertRaises(HTTPException) as error:
            delete_agent_thread("   ")
        self.assertEqual(error.exception.status_code, 400)

    def test_api_endpoint_reports_the_deletion(self):
        with patch(
            "src.api.chat.delete_thread_memory",
            return_value={
                "thread_id": "thread-1",
                "cleared_agents": 1,
                "deleted_files": 2,
            },
        ):
            response = delete_agent_thread("thread-1")

        self.assertEqual(response["code"], 0)
        self.assertEqual(response["data"]["thread_id"], "thread-1")
        self.assertEqual(response["data"]["deleted_files"], 2)


class _FakeAgent:
    """Minimal stand-in for a compiled agent exposing a checkpointer."""

    def __init__(self, checkpointer, cwd=None):
        self.checkpointer = checkpointer
        self.backend = _FakeBackend(cwd)


class _FakeBackend:
    def __init__(self, cwd):
        self.cwd = cwd


if __name__ == "__main__":
    unittest.main()
