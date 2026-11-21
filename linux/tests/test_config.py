from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from sayall_linux.config import AppConfig, DEFAULT_BINDINGS


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "config.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_defaults_are_complete(self):
        config = AppConfig.load(self.path)
        self.assertEqual(config.voice_trigger, "F5")
        self.assertEqual(config.output_volume_percent, 100)
        self.assertEqual(config.bindings, DEFAULT_BINDINGS)
        self.assertTrue(config.mapping_enabled)

    def test_validation_clamps_and_ignores_unknown_actions(self):
        payload = {
            "output_volume_percent": 9000,
            "voice_trigger": "ctrl+alt+space",
            "bindings": {"up": "not_an_action", "down": "volume_down"},
            "unknown_future_field": {"keep": True},
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        config = AppConfig.load(self.path)
        self.assertEqual(config.output_volume_percent, 1000)
        self.assertEqual(config.voice_trigger, "CTRL+ALT+SPACE")
        self.assertEqual(config.bindings["up"], DEFAULT_BINDINGS["up"])
        self.assertEqual(config.bindings["down"], "volume_down")
        config.save()
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["unknown_future_field"], {"keep": True})

    def test_corrupt_configuration_is_preserved_before_write(self):
        self.path.write_text("{broken", encoding="utf-8")
        config = AppConfig.load(self.path)
        self.assertIsNotNone(config.load_error)
        config.output_volume_percent = 200
        config.save()
        backup = self.path.with_suffix(self.path.suffix + ".corrupt")
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(encoding="utf-8"), "{broken")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["output_volume_percent"], 200)

    def test_binding_update_is_controlled(self):
        config = AppConfig.load(self.path)
        self.assertTrue(config.set_binding("home", "context_menu"))
        self.assertFalse(config.set_binding("home", "context_menu"))
        self.assertFalse(config.set_binding("unknown", "escape"))
        self.assertFalse(config.set_binding("home", "unknown"))


if __name__ == "__main__":
    unittest.main()
