from __future__ import annotations

import unittest

from sayall_linux.keymap import (
    KEY_D,
    KEY_LEFTALT,
    KEY_LEFTCTRL,
    KEY_LEFTMETA,
    KEY_TAB,
    KeySequenceError,
    action_sequence,
    classify_key,
    parse_key_sequence,
)


class KeymapTests(unittest.TestCase):
    def test_parses_modifier_sequence_in_stable_order(self):
        self.assertEqual(parse_key_sequence("META+CTRL+D"), (KEY_LEFTCTRL, KEY_LEFTMETA, KEY_D))

    def test_rejects_unknown_or_multiple_keys(self):
        with self.assertRaises(KeySequenceError):
            parse_key_sequence("CTRL+NOT_A_KEY")
        with self.assertRaises(KeySequenceError):
            parse_key_sequence("CTRL+A+B")
        with self.assertRaises(KeySequenceError):
            parse_key_sequence("CTRL")

    def test_action_sequences_cover_basic_mapping(self):
        self.assertEqual(action_sequence("show_desktop"), (KEY_LEFTMETA, KEY_D))
        self.assertEqual(action_sequence("app_switcher"), (KEY_LEFTALT, KEY_TAB))
        self.assertIsNone(action_sequence("disabled"))

    def test_voice_key_and_buttons_are_distinct(self):
        self.assertEqual(classify_key(63)[0], "voice")
        self.assertEqual(classify_key(103)[1], "up")
        self.assertEqual(classify_key(200)[0], "passthrough")


if __name__ == "__main__":
    unittest.main()
