import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.runtime.encoding import (
    TolerantSubprocess,
    decode_output,
    install_tolerant_subprocess,
)


class DecodeOutputTests(unittest.TestCase):
    def test_decodes_utf8_bytes(self):
        self.assertEqual(decode_output("中兴通讯".encode("utf-8")), "中兴通讯")

    def test_decodes_gbk_bytes(self):
        self.assertEqual(decode_output("中文测试".encode("gbk")), "中文测试")

    def test_prefers_utf8_over_gbk(self):
        # UTF-8 bytes are not valid GBK, so they must be read as UTF-8.
        self.assertEqual(decode_output("股价28.06元".encode("utf-8")), "股价28.06元")

    def test_never_raises_on_invalid_bytes(self):
        self.assertIsInstance(decode_output(b"\xff\xfe\x00\xad"), str)

    def test_handles_none_and_str(self):
        self.assertEqual(decode_output(None), "")
        self.assertEqual(decode_output("already text"), "already text")

    def test_handles_empty_bytes(self):
        self.assertEqual(decode_output(b""), "")


class TolerantSubprocessTests(unittest.TestCase):
    def test_text_mode_decodes_utf8_child_output(self):
        tolerant = TolerantSubprocess(subprocess)
        script = (
            "import sys; sys.stdout.buffer.write("
            "'中兴通讯'.encode('utf-8'))"
        )
        result = tolerant.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.stdout, "中兴通讯")

    def test_text_mode_decodes_gbk_child_output(self):
        tolerant = TolerantSubprocess(subprocess)
        script = (
            "import sys; sys.stdout.buffer.write("
            "'中文测试'.encode('gbk'))"
        )
        result = tolerant.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.stdout, "中文测试")

    def test_binary_mode_is_untouched(self):
        tolerant = TolerantSubprocess(subprocess)
        result = tolerant.run(
            [sys.executable, "-c", "print('ok')"],
            capture_output=True,
        )

        self.assertIsInstance(result.stdout, bytes)

    def test_string_input_is_encoded_as_utf8(self):
        tolerant = TolerantSubprocess(subprocess)
        result = tolerant.run(
            [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
            input="中兴通讯",
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.stdout, "中兴通讯")

    def test_delegates_other_attributes(self):
        tolerant = TolerantSubprocess(subprocess)

        self.assertIs(tolerant.TimeoutExpired, subprocess.TimeoutExpired)
        self.assertEqual(tolerant.DEVNULL, subprocess.DEVNULL)


class InstallTolerantSubprocessTests(unittest.TestCase):
    def test_patches_agent_backends(self):
        import deepagents.backends.filesystem as filesystem
        import deepagents.backends.local_shell as local_shell

        install_tolerant_subprocess()

        self.assertIsInstance(local_shell.subprocess, TolerantSubprocess)
        self.assertIsInstance(filesystem.subprocess, TolerantSubprocess)

    def test_is_idempotent(self):
        import deepagents.backends.local_shell as local_shell

        install_tolerant_subprocess()
        first = local_shell.subprocess
        install_tolerant_subprocess()

        self.assertIs(local_shell.subprocess, first)


if __name__ == "__main__":
    unittest.main()
