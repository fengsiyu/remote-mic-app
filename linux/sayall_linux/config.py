"""Persistent Linux settings with validation and corruption preservation."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


DEFAULT_BINDINGS: Dict[str, str] = {
    "power": "escape",
    "up": "arrow_up",
    "left": "arrow_left",
    "ok": "return",
    "right": "arrow_right",
    "down": "arrow_down",
    "back": "backspace",
    "volume_up": "volume_up",
    "home": "show_desktop",
    "volume_down": "volume_down",
    "menu": "context_menu",
    "tv": "app_switcher",
}

ACTION_LABELS_ZH: Dict[str, str] = {
    "disabled": "不执行",
    "escape": "Esc",
    "return": "回车",
    "backspace": "退格",
    "arrow_up": "方向上",
    "arrow_down": "方向下",
    "arrow_left": "方向左",
    "arrow_right": "方向右",
    "tab": "Tab",
    "space": "空格",
    "volume_up": "音量加",
    "volume_down": "音量减",
    "volume_mute": "静音",
    "play_pause": "播放/暂停",
    "next_track": "下一首",
    "previous_track": "上一首",
    "show_desktop": "显示桌面",
    "app_switcher": "切换应用",
    "context_menu": "上下文菜单",
}

ACTION_LABELS_EN: Dict[str, str] = {
    "disabled": "Do nothing",
    "escape": "Esc",
    "return": "Return",
    "backspace": "Backspace",
    "arrow_up": "Arrow up",
    "arrow_down": "Arrow down",
    "arrow_left": "Arrow left",
    "arrow_right": "Arrow right",
    "tab": "Tab",
    "space": "Space",
    "volume_up": "Volume up",
    "volume_down": "Volume down",
    "volume_mute": "Mute",
    "play_pause": "Play / pause",
    "next_track": "Next track",
    "previous_track": "Previous track",
    "show_desktop": "Show desktop",
    "app_switcher": "Switch app",
    "context_menu": "Context menu",
}

VALID_BUTTONS = tuple(DEFAULT_BINDINGS.keys())
VALID_ACTIONS = frozenset(ACTION_LABELS_ZH.keys())


def config_home() -> Path:
    override = os.environ.get("XDG_CONFIG_HOME")
    return Path(override).expanduser() if override else Path.home() / ".config"


def state_home() -> Path:
    override = os.environ.get("XDG_STATE_HOME")
    return Path(override).expanduser() if override else Path.home() / ".local" / "state"


def data_home() -> Path:
    override = os.environ.get("XDG_DATA_HOME")
    return Path(override).expanduser() if override else Path.home() / ".local" / "share"


def default_config_path() -> Path:
    return config_home() / "sayall-linux" / "config.json"


def default_log_path() -> Path:
    return state_home() / "sayall-linux" / "sayall.log"


def default_fifo_path() -> Path:
    return data_home() / "sayall-linux" / "sayall-mic.fifo"


@dataclass
class AppConfig:
    schema: int = 1
    device_path: Optional[str] = None
    voice_trigger: str = "F5"
    output_volume_percent: int = 100
    mapping_enabled: bool = True
    hold_repeat_buttons: List[str] = field(
        default_factory=lambda: ["up", "down", "left", "right", "back", "volume_up", "volume_down"]
    )
    bindings: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_BINDINGS))
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)
    load_error: Optional[str] = field(default=None, repr=False)
    path: Path = field(default_factory=default_config_path, repr=False)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AppConfig":
        target = path or default_config_path()
        config = cls(path=target)
        if not target.exists():
            return config
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("root must be an object")
        except (OSError, ValueError, UnicodeError) as error:
            config.load_error = type(error).__name__
            return config
        config.raw = dict(payload)
        config.schema = _positive_int(payload.get("schema"), 1)
        device_path = payload.get("device_path")
        config.device_path = device_path if isinstance(device_path, str) and device_path.startswith("/org/bluez/") else None
        config.voice_trigger = _validated_trigger(payload.get("voice_trigger"), "F5")
        config.output_volume_percent = _clamped_int(payload.get("output_volume_percent"), 0, 1000, 100)
        config.mapping_enabled = payload.get("mapping_enabled") is not False
        hold_repeat = payload.get("hold_repeat_buttons")
        if isinstance(hold_repeat, list):
            config.hold_repeat_buttons = [item for item in hold_repeat if item in VALID_BUTTONS]
        bindings = payload.get("bindings")
        if isinstance(bindings, dict):
            merged = dict(DEFAULT_BINDINGS)
            for button, action in bindings.items():
                if button in VALID_BUTTONS and action in VALID_ACTIONS:
                    merged[button] = action
            config.bindings = merged
        return config

    def set_binding(self, button: str, action: str) -> bool:
        if button not in VALID_BUTTONS or action not in VALID_ACTIONS:
            return False
        if self.bindings.get(button) == action:
            return False
        self.bindings[button] = action
        return True

    def update(self, **changes: Any) -> None:
        for key, value in changes.items():
            if not hasattr(self, key):
                continue
            setattr(self, key, value)

    def to_dict(self) -> Dict[str, Any]:
        payload = dict(self.raw)
        payload.update(
            {
                "schema": 1,
                "device_path": self.device_path,
                "voice_trigger": _validated_trigger(self.voice_trigger, "F5"),
                "output_volume_percent": _clamped_int(self.output_volume_percent, 0, 1000, 100),
                "mapping_enabled": bool(self.mapping_enabled),
                "hold_repeat_buttons": [item for item in self.hold_repeat_buttons if item in VALID_BUTTONS],
                "bindings": {button: self.bindings.get(button, DEFAULT_BINDINGS[button]) for button in VALID_BUTTONS},
            }
        )
        return payload

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.load_error and self.path.exists():
            backup = self.path.with_suffix(self.path.suffix + ".corrupt")
            if not backup.exists():
                backup.write_bytes(self.path.read_bytes())
                try:
                    os.chmod(str(backup), 0o600)
                except OSError:
                    pass
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, self.path)
            self.raw = self.to_dict()
            self.load_error = None
        except OSError:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise


def _positive_int(value: Any, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _clamped_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return min(maximum, max(minimum, number))


def _validated_trigger(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    normalized = value.strip().upper()
    if not normalized or len(normalized) > 64:
        return fallback
    return normalized
