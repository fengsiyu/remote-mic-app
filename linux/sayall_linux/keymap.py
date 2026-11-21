"""Key names, remote HID mappings and executor actions for Linux."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


KEY_A = 30
KEY_B = 48
KEY_C = 46
KEY_D = 32
KEY_E = 18
KEY_F = 33
KEY_G = 34
KEY_H = 35
KEY_I = 23
KEY_J = 36
KEY_K = 37
KEY_L = 38
KEY_M = 50
KEY_N = 49
KEY_O = 24
KEY_P = 25
KEY_Q = 16
KEY_R = 19
KEY_S = 31
KEY_T = 20
KEY_U = 22
KEY_V = 47
KEY_W = 17
KEY_X = 45
KEY_Y = 21
KEY_Z = 44
KEY_1 = 2
KEY_2 = 3
KEY_3 = 4
KEY_4 = 5
KEY_5 = 6
KEY_6 = 7
KEY_7 = 8
KEY_8 = 9
KEY_9 = 10
KEY_0 = 11
KEY_ENTER = 28
KEY_ESC = 1
KEY_BACKSPACE = 14
KEY_TAB = 15
KEY_SPACE = 57
KEY_MINUS = 12
KEY_EQUAL = 13
KEY_LEFTBRACE = 26
KEY_RIGHTBRACE = 27
KEY_BACKSLASH = 43
KEY_SEMICOLON = 39
KEY_APOSTROPHE = 40
KEY_GRAVE = 41
KEY_COMMA = 51
KEY_DOT = 52
KEY_SLASH = 53
KEY_INSERT = 110
KEY_DELETE = 111
KEY_HOME = 102
KEY_END = 107
KEY_PAGEUP = 104
KEY_PAGEDOWN = 109
KEY_LEFT = 105
KEY_RIGHT = 106
KEY_UP = 103
KEY_DOWN = 108
KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_LEFTALT = 56
KEY_LEFTMETA = 125
KEY_RIGHTCTRL = 97
KEY_RIGHTSHIFT = 54
KEY_RIGHTALT = 100
KEY_RIGHTMETA = 126
KEY_F1 = 59
KEY_F2 = 60
KEY_F3 = 61
KEY_F4 = 62
KEY_F5 = 63
KEY_F6 = 64
KEY_F7 = 65
KEY_F8 = 66
KEY_F9 = 67
KEY_F10 = 68
KEY_F11 = 87
KEY_F12 = 88
KEY_MUTE = 113
KEY_VOLUMEDOWN = 114
KEY_VOLUMEUP = 115
KEY_PLAYPAUSE = 164
KEY_NEXTSONG = 163
KEY_PREVIOUSSONG = 165
KEY_STOPCD = 166
KEY_POWER = 116
KEY_MENU = 139
KEY_COMPOSE = 127
KEY_BACK = 158
KEY_HOMEPAGE = 172
KEY_TV = 377


KEY_NAMES: Dict[str, int] = {
    "ESC": KEY_ESC,
    "ESCAPE": KEY_ESC,
    "RETURN": KEY_ENTER,
    "ENTER": KEY_ENTER,
    "BACKSPACE": KEY_BACKSPACE,
    "TAB": KEY_TAB,
    "SPACE": KEY_SPACE,
    "INSERT": KEY_INSERT,
    "DELETE": KEY_DELETE,
    "HOME": KEY_HOME,
    "END": KEY_END,
    "PAGEUP": KEY_PAGEUP,
    "PAGEDOWN": KEY_PAGEDOWN,
    "UP": KEY_UP,
    "DOWN": KEY_DOWN,
    "LEFT": KEY_LEFT,
    "RIGHT": KEY_RIGHT,
    "MUTE": KEY_MUTE,
    "VOLUMEUP": KEY_VOLUMEUP,
    "VOLUMEDOWN": KEY_VOLUMEDOWN,
    "PLAYPAUSE": KEY_PLAYPAUSE,
    "NEXT": KEY_NEXTSONG,
    "PREVIOUS": KEY_PREVIOUSSONG,
    "STOP": KEY_STOPCD,
    "MINUS": KEY_MINUS,
    "EQUAL": KEY_EQUAL,
    "LEFTBRACE": KEY_LEFTBRACE,
    "RIGHTBRACE": KEY_RIGHTBRACE,
    "BACKSLASH": KEY_BACKSLASH,
    "SEMICOLON": KEY_SEMICOLON,
    "APOSTROPHE": KEY_APOSTROPHE,
    "GRAVE": KEY_GRAVE,
    "COMMA": KEY_COMMA,
    "DOT": KEY_DOT,
    "SLASH": KEY_SLASH,
}

for _letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    KEY_NAMES[_letter] = globals()["KEY_" + _letter]
for _digit in "0123456789":
    KEY_NAMES[_digit] = globals()["KEY_" + _digit]
for _number in range(1, 13):
    KEY_NAMES["F%d" % _number] = globals()["KEY_F%d" % _number]

MODIFIER_NAMES: Dict[str, int] = {
    "CTRL": KEY_LEFTCTRL,
    "CONTROL": KEY_LEFTCTRL,
    "CTRL_L": KEY_LEFTCTRL,
    "CTRL_R": KEY_RIGHTCTRL,
    "SHIFT": KEY_LEFTSHIFT,
    "SHIFT_L": KEY_LEFTSHIFT,
    "SHIFT_R": KEY_RIGHTSHIFT,
    "ALT": KEY_LEFTALT,
    "OPTION": KEY_LEFTALT,
    "ALT_L": KEY_LEFTALT,
    "ALT_R": KEY_RIGHTALT,
    "META": KEY_LEFTMETA,
    "SUPER": KEY_LEFTMETA,
    "WIN": KEY_LEFTMETA,
    "META_L": KEY_LEFTMETA,
    "META_R": KEY_RIGHTMETA,
}

MODIFIER_ORDER = (
    KEY_LEFTCTRL,
    KEY_RIGHTCTRL,
    KEY_LEFTALT,
    KEY_RIGHTALT,
    KEY_LEFTSHIFT,
    KEY_RIGHTSHIFT,
    KEY_LEFTMETA,
    KEY_RIGHTMETA,
)


class KeySequenceError(ValueError):
    pass


def parse_key_sequence(value: str) -> Tuple[int, ...]:
    parts = [part.strip().upper() for part in value.split("+") if part.strip()]
    if not parts:
        raise KeySequenceError("empty key sequence")
    modifiers: List[int] = []
    keys: List[int] = []
    for part in parts:
        if part in MODIFIER_NAMES:
            code = MODIFIER_NAMES[part]
            if code not in modifiers:
                modifiers.append(code)
        elif part in KEY_NAMES:
            keys.append(KEY_NAMES[part])
        else:
            raise KeySequenceError("unknown key name")
    if len(keys) != 1:
        raise KeySequenceError("exactly one non-modifier key is required")
    ordered_modifiers = [code for code in MODIFIER_ORDER if code in modifiers]
    return tuple(ordered_modifiers + keys)


ACTION_SEQUENCES: Dict[str, str] = {
    "escape": "ESC",
    "return": "RETURN",
    "backspace": "BACKSPACE",
    "arrow_up": "UP",
    "arrow_down": "DOWN",
    "arrow_left": "LEFT",
    "arrow_right": "RIGHT",
    "tab": "TAB",
    "space": "SPACE",
    "volume_up": "VOLUMEUP",
    "volume_down": "VOLUMEDOWN",
    "volume_mute": "MUTE",
    "play_pause": "PLAYPAUSE",
    "next_track": "NEXT",
    "previous_track": "PREVIOUS",
    "show_desktop": "META+D",
    "app_switcher": "ALT+TAB",
    "context_menu": "SHIFT+F10",
}


BUTTON_KEY_CODES: Dict[str, Tuple[int, ...]] = {
    "power": (KEY_POWER,),
    "up": (KEY_UP,),
    "left": (KEY_LEFT,),
    "ok": (KEY_ENTER,),
    "right": (KEY_RIGHT,),
    "down": (KEY_DOWN,),
    "back": (KEY_BACK, KEY_BACKSPACE),
    "volume_up": (KEY_VOLUMEUP,),
    "home": (KEY_HOMEPAGE, KEY_HOME),
    "volume_down": (KEY_VOLUMEDOWN,),
    "menu": (KEY_COMPOSE, KEY_MENU),
    "tv": (KEY_TV,),
}

VOICE_KEY_CODES = frozenset((KEY_F5,))

CODE_TO_BUTTON: Dict[int, str] = {}
for _button, _codes in BUTTON_KEY_CODES.items():
    for _code in _codes:
        CODE_TO_BUTTON[_code] = _button


def action_sequence(action: str) -> Optional[Tuple[int, ...]]:
    if action == "disabled":
        return None
    spec = ACTION_SEQUENCES.get(action)
    if spec is None:
        return None
    return parse_key_sequence(spec)


def all_injectable_key_codes() -> Tuple[int, ...]:
    codes = set(KEY_NAMES.values()) | set(MODIFIER_NAMES.values()) | set(CODE_TO_BUTTON.keys())
    for spec in ACTION_SEQUENCES.values():
        try:
            codes.update(parse_key_sequence(spec))
        except KeySequenceError:
            pass
    return tuple(sorted(codes))


@dataclass(frozen=True)
class RemoteInputEvent:
    code: int
    pressed: bool
    monotonic_time: float


def classify_key(code: int) -> Tuple[str, Optional[str]]:
    if code in VOICE_KEY_CODES:
        return "voice", None
    button = CODE_TO_BUTTON.get(code)
    if button is not None:
        return "button", button
    return "passthrough", None
