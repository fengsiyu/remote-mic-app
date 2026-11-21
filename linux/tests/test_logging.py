import os
import stat
import tempfile
import unittest
from pathlib import Path

from sayall_linux.logging import RuntimeLogger


class RuntimeLoggerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "sayall.log"

    def tearDown(self):
        self.directory.cleanup()

    def test_log_file_is_private(self):
        logger = RuntimeLogger(self.path)
        logger.write("APP phase=start result=ok")
        logger.close()

        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(0o600, mode)

    def test_control_characters_are_not_written(self):
        logger = RuntimeLogger(self.path)
        logger.write("INPUT phase=test", {"value": "line1\nline2\ttab"})
        logger.close()

        line = self.path.read_text(encoding="utf-8")
        self.assertIn("value=invalid", line)
        self.assertEqual(1, line.count("\n"))

    def test_rotation_keeps_a_single_archive(self):
        logger = RuntimeLogger(self.path)
        logger.MAX_BYTES = 64
        logger.write("APP phase=first result=ok")
        logger.write("APP phase=second result=ok")
        logger.write("APP phase=third result=ok")
        logger.close()

        rotated = self.path.with_suffix(self.path.suffix + ".1")
        self.assertTrue(rotated.exists())
        self.assertEqual(0o600, stat.S_IMODE(os.stat(rotated).st_mode))
        self.assertIn("third", self.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
