from __future__ import annotations

import unittest

from sayall_linux.atvv import (
    ATVVCapabilities,
    FrameAccumulator,
    IMAADPCMDecoder,
    audio_coverage_percent,
    expected_frame_count,
    microphone_close,
    microphone_extend,
    microphone_open,
    parse_capabilities,
    parse_stream_start,
    parse_sync,
    postprocess_pcm,
)


class CapabilityTests(unittest.TestCase):
    def test_parses_v11_audio_capabilities(self):
        caps = parse_capabilities(bytes([0x0B, 0x01, 0x00, 0x03, 0x00, 0x00, 0x78]))
        self.assertIsNotNone(caps)
        self.assertEqual(caps.version, 0x0100)
        self.assertEqual(caps.selected_codec, 0x02)
        self.assertEqual(caps.sample_rate, 16000)
        self.assertEqual(caps.frame_size, 120)
        self.assertTrue(caps.supports_audio)

    def test_parses_legacy_audio_capabilities(self):
        caps = parse_capabilities(bytes([0x0B, 0x00, 0x01, 0x00, 0x03, 0x00, 0x78, 0x00, 0x03]))
        self.assertIsNotNone(caps)
        self.assertEqual(caps.version, 0x0001)
        self.assertEqual(caps.sample_rate, 16000)

    def test_rejects_invalid_capabilities(self):
        self.assertIsNone(parse_capabilities(b""))
        self.assertIsNone(parse_capabilities(bytes([0x0A, 0, 0, 0])))

    def test_command_shapes_match_protocol(self):
        caps = ATVVCapabilities(0x0100, 0x03, 0, 120, 0x02, 16000)
        self.assertEqual(microphone_open(caps), bytes([0x0C, 0x00]))
        self.assertEqual(microphone_close(caps, 7), bytes([0x0D, 0x07]))
        self.assertEqual(microphone_extend(caps, 7), bytes([0x0E, 0x07]))

    def test_parses_stream_and_sync(self):
        self.assertEqual(parse_stream_start(bytes([0x04, 0x00, 0x02, 0x09])), (0, 2, 9))
        self.assertEqual(parse_sync(bytes([0x0A, 0, 0, 0, 0xFF, 0xFE, 0x10])), (-2, 16))

    def test_realtime_helpers(self):
        self.assertEqual(expected_frame_count(1500, 240), 100)
        self.assertEqual(audio_coverage_percent(24000, 1500), 100.0)


class DecoderTests(unittest.TestCase):
    def test_high_nibble_decoding_is_deterministic(self):
        decoder = IMAADPCMDecoder()
        self.assertEqual(decoder.decode(b"\x00\x00"), [0, 0, 0, 0])
        self.assertEqual(decoder.predictor, 0)
        self.assertEqual(decoder.step_index, 0)

    def test_low_nibble_mode_changes_order(self):
        high = IMAADPCMDecoder()
        low = IMAADPCMDecoder()
        low.low_nibble_first = True
        self.assertNotEqual(high.decode(b"\x21\x43"), low.decode(b"\x21\x43"))

    def test_frame_accumulator_preserves_partial_tail(self):
        accumulator = FrameAccumulator()
        self.assertEqual(accumulator.append(b"1234", 3), [b"123"])
        self.assertEqual(accumulator.pending_bytes, 1)
        self.assertEqual(accumulator.append(b"56", 3), [b"456"])
        self.assertEqual(accumulator.pending_bytes, 0)

    def test_postprocessor_clamps_extreme_gain(self):
        output = postprocess_pcm([32767, 32767, 32767], gain_db=100.0)
        self.assertEqual(output, [32767, 32767, 32767])
        output = postprocess_pcm([-32768, -32768, -32768], gain_db=-100.0)
        self.assertEqual(output, [-2068, -2068, -2068])


if __name__ == "__main__":
    unittest.main()
