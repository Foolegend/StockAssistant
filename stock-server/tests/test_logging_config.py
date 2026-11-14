import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.runtime.logging_config import (
    _HANDLER_MARKER,
    get_logger,
    get_request_id,
    preview,
    request_context,
    setup_logging,
)


class LoggingConfigTests(unittest.TestCase):
    def setUp(self):
        self._previous_console = os.environ.get("STOCK_LOG_CONSOLE")
        os.environ["STOCK_LOG_CONSOLE"] = "0"
        self._log_dir = tempfile.TemporaryDirectory()
        self.root = logging.getLogger()

    def tearDown(self):
        for handler in list(self.root.handlers):
            if getattr(handler, _HANDLER_MARKER, False):
                self.root.removeHandler(handler)
                handler.close()
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logger = logging.getLogger(name)
            for handler in list(logger.handlers):
                if getattr(handler, _HANDLER_MARKER, False):
                    logger.removeHandler(handler)
        if self._previous_console is None:
            os.environ.pop("STOCK_LOG_CONSOLE", None)
        else:
            os.environ["STOCK_LOG_CONSOLE"] = self._previous_console
        self._log_dir.cleanup()

    def _log_file(self):
        return os.path.join(self._log_dir.name, "stock-server.log")

    def test_records_carry_request_id_and_reach_the_file(self):
        setup_logging(level="INFO", log_dir=self._log_dir.name, force=True)
        logger = get_logger("test.logging")

        with request_context("req-abc123"):
            self.assertEqual(get_request_id(), "req-abc123")
            logger.info("handled question: %s", "分析 TSLA")

        self.assertEqual(get_request_id(), "-")
        content = open(self._log_file(), encoding="utf-8").read()
        self.assertIn("req=req-abc123", content)
        self.assertIn("test.logging", content)
        self.assertIn("handled question: 分析 TSLA", content)

    def test_console_and_file_handlers_are_both_attached(self):
        os.environ.pop("STOCK_LOG_CONSOLE", None)
        setup_logging(level="INFO", log_dir=self._log_dir.name, force=True)

        handlers = [h for h in self.root.handlers if getattr(h, _HANDLER_MARKER, False)]
        kinds = {type(handler).__name__ for handler in handlers}
        self.assertEqual(len(handlers), 2)
        self.assertIn("StreamHandler", kinds)
        self.assertIn("RotatingFileHandler", kinds)

    def test_setup_logging_is_idempotent(self):
        setup_logging(level="INFO", log_dir=self._log_dir.name, force=True)
        setup_logging(level="INFO", log_dir=self._log_dir.name, force=False)

        marked = [h for h in self.root.handlers if getattr(h, _HANDLER_MARKER, False)]
        self.assertEqual(len(marked), 1)

    def test_uvicorn_loggers_write_to_the_same_file(self):
        setup_logging(level="INFO", log_dir=self._log_dir.name, force=True)
        logging.getLogger("uvicorn.error").error("boom from uvicorn")

        self.assertIn("boom from uvicorn", open(self._log_file(), encoding="utf-8").read())

    def test_repeated_setup_does_not_duplicate_lines(self):
        setup_logging(level="INFO", log_dir=self._log_dir.name, force=True)
        setup_logging(level="INFO", log_dir=self._log_dir.name, force=True)
        get_logger("test.logging").info("only once")

        content = open(self._log_file(), encoding="utf-8").read()
        self.assertEqual(content.count("only once"), 1)

    def test_preview_collapses_whitespace_and_truncates(self):
        self.assertEqual(preview("a\n\n b\tc"), "a b c")
        truncated = preview("x" * 500, limit=10)
        self.assertTrue(truncated.startswith("x" * 10))
        self.assertIn("500 chars", truncated)


if __name__ == "__main__":
    unittest.main()
