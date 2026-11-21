from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sayall_linux.controller import VoiceController
from sayall_linux.logging import RuntimeLogger


class FakeAudio:
    def __init__(self):
        self.generations = []
        self.submitted = []
        self.finished = []

    def begin_generation(self, generation):
        self.generations.append(generation)

    def submit_samples(self, generation, samples):
        self.submitted.append((generation, len(samples)))
        return len(samples)

    def finish_generation(self, generation, completion):
        self.finished.append(generation)
        completion(sum(length for item_generation, length in self.submitted if item_generation == generation), 0)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temporary.name) / "controller.log"
        self.logger = RuntimeLogger(self.log_path)
        self.audio = FakeAudio()
        self.states = []
        self.sent = []
        self.controller = VoiceController(
            self.audio,
            self.logger,
            on_state=self.states.append,
            send_control=lambda payload: self.sent.append(payload) or True,
        )

    def tearDown(self):
        self.logger.close()
        self.temporary.cleanup()

    def test_remote_initiated_session_decodes_and_drains(self):
        self.controller.note_voice_key_down(100.0)
        self.controller.handle_control(bytes([0x0B, 0x01, 0x00, 0x03, 0x00, 0x00, 0x78]))
        self.controller.handle_control(bytes([0x04, 0x00, 0x02, 0x07]))
        self.controller.handle_audio(b"\x00" * 120)
        self.assertEqual(self.audio.generations, [1])
        self.assertEqual(self.audio.submitted[0][0], 1)
        self.assertGreater(self.audio.submitted[0][1], 0)
        self.controller.handle_control(bytes([0x00, 0x00]))
        self.assertEqual(self.audio.finished, [1])
        text = self.log_path.read_text(encoding="utf-8")
        self.assertIn("trigger_down_to_first_pcm_ms=", text)
        self.assertIn("completion=normal", text)
        self.assertIn("audio_interrupted_samples=0", text)

    def test_microphone_open_is_answered(self):
        self.controller.handle_control(bytes([0x0B, 0x01, 0x00, 0x03, 0x00, 0x00, 0x78]))
        self.controller.handle_control(bytes([0x08]))
        self.assertEqual(self.sent, [bytes([0x0C, 0x00])])

    def test_audio_without_start_creates_one_implicit_session(self):
        self.controller.handle_control(bytes([0x0B, 0x01, 0x00, 0x03, 0x00, 0x00, 0x78]))
        self.controller.handle_audio(b"\x00" * 120)
        self.controller.handle_audio(b"\x00" * 120)
        self.assertEqual(self.audio.generations, [1])

    def test_forced_stop_is_reported_as_forced(self):
        self.controller.handle_control(bytes([0x0B, 0x01, 0x00, 0x03, 0x00, 0x00, 0x78]))
        self.controller.handle_control(bytes([0x04, 0x00, 0x02, 0x01]))
        self.controller.handle_audio(b"\x00" * 120)
        self.controller.stop_stream(forced=True, reason="bluetooth_disconnected")
        text = self.log_path.read_text(encoding="utf-8")
        self.assertIn("completion=forced", text)


if __name__ == "__main__":
    unittest.main()
