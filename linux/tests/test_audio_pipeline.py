from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from sayall_linux.audio import OrderedPCMWriter
from sayall_linux.logging import RuntimeLogger


class FakeVirtualMic:
    write_fd = 1


class AudioPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.logger = RuntimeLogger(Path(self.temporary.name) / "test.log")

    def tearDown(self):
        self.logger.close()
        self.temporary.cleanup()

    def test_generation_completes_only_after_all_samples_are_written(self):
        writer = OrderedPCMWriter(FakeVirtualMic(), self.logger)
        writer._write_all = lambda _descriptor, _payload: True
        writer.start()
        completed = threading.Event()
        result = {}

        def completion(written, pending):
            result["written"] = written
            result["pending"] = pending
            completed.set()

        writer.begin_generation(1)
        writer.enqueue_pcm(1, b"\x00\x00" * 240)
        writer.enqueue_pcm(1, b"\x01\x00" * 120)
        writer.finish_generation(1, completion)
        self.assertTrue(completed.wait(2.0))
        self.assertEqual(result, {"written": 360, "pending": 0})
        writer.stop()

    def test_new_generation_is_not_mixed_with_previous_end_marker(self):
        writer = OrderedPCMWriter(FakeVirtualMic(), self.logger)
        writer._write_all = lambda _descriptor, _payload: True
        writer.start()
        completed = []

        writer.begin_generation(1)
        writer.begin_generation(2)
        writer.enqueue_pcm(1, b"\x00\x00" * 10)
        writer.finish_generation(1, lambda written, pending: completed.append((1, written, pending)))
        writer.enqueue_pcm(2, b"\x00\x00" * 20)
        writer.finish_generation(2, lambda written, pending: completed.append((2, written, pending)))
        deadline = time.monotonic() + 2.0
        while len(completed) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(completed, [(1, 10, 0), (2, 20, 0)])
        writer.stop()


if __name__ == "__main__":
    unittest.main()
