"""Linux evdev capture and uinput injection for the RC001/RC003 remote."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import glob
import os
import select
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .config import AppConfig
from .keymap import (
    BUTTON_KEY_CODES,
    CODE_TO_BUTTON,
    VOICE_KEY_CODES,
    KeySequenceError,
    action_sequence,
    all_injectable_key_codes,
    classify_key,
    parse_key_sequence,
)
from .logging import RuntimeLogger


EV_SYN = 0
EV_KEY = 1
SYN_REPORT = 0
BUS_USB = 0x03
UINPUT_VENDOR = 0x1209
UINPUT_PRODUCT = 0x5341
UINPUT_IOCTL_BASE = ord("U")
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_DEV_SETUP = 0x405C5503
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
EVIOCGRAB = 0x40044590
EVENT_STRUCT = struct.Struct("llHHi")

REMOTE_NAMES = (
    "mi rc",
    "xiaomi bluetooth remote 2",
    "xiaomi bluetooth remote 2 pro",
    "小米蓝牙语音遥控器",
)


class InputEvent(ctypes.Structure):
    _fields_ = [
        ("bustype", ctypes.c_uint16),
        ("vendor", ctypes.c_uint16),
        ("product", ctypes.c_uint16),
        ("version", ctypes.c_uint16),
    ]


class UInputSetup(ctypes.Structure):
    _fields_ = [
        ("id", InputEvent),
        ("name", ctypes.c_char * 80),
        ("ff_effects_max", ctypes.c_uint32),
    ]


@dataclass(frozen=True)
class RemoteEventDevice:
    path: str
    name: str
    vendor: int
    product: int


class UInputKeyboard:
    def __init__(self, logger: RuntimeLogger) -> None:
        self.logger = logger
        self._fd: Optional[int] = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self._fd is not None

    def open(self) -> bool:
        if self._fd is not None:
            return True
        if not os.path.exists("/dev/uinput"):
            self.logger.write("INPUT INJECT phase=open result=failed reason=uinput_missing")
            return False
        try:
            descriptor = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            self.logger.write("INPUT INJECT phase=open result=failed error=%s" % type(error).__name__)
            return False
        try:
            fcntl.ioctl(descriptor, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(descriptor, UI_SET_EVBIT, EV_SYN)
            for code in all_injectable_key_codes():
                try:
                    fcntl.ioctl(descriptor, UI_SET_KEYBIT, int(code))
                except OSError:
                    pass
            setup = UInputSetup()
            setup.id.bustype = BUS_USB
            setup.id.vendor = UINPUT_VENDOR
            setup.id.product = UINPUT_PRODUCT
            setup.id.version = 1
            setup.name = b"SayAll Linux Remote"
            setup.ff_effects_max = 0
            fcntl.ioctl(descriptor, UI_DEV_SETUP, setup)
            fcntl.ioctl(descriptor, UI_DEV_CREATE)
        except OSError as error:
            try:
                os.close(descriptor)
            except OSError:
                pass
            self.logger.write("INPUT INJECT phase=create result=failed error=%s" % type(error).__name__)
            return False
        self._fd = descriptor
        self.logger.write("INPUT INJECT phase=ready result=ok")
        return True

    def close(self) -> None:
        with self._lock:
            descriptor = self._fd
            self._fd = None
        if descriptor is None:
            return
        try:
            fcntl.ioctl(descriptor, UI_DEV_DESTROY)
        except OSError:
            pass
        try:
            os.close(descriptor)
        except OSError:
            pass
        self.logger.write("INPUT INJECT phase=close result=ok")

    def hold_sequence(self, sequence: Sequence[int]) -> bool:
        with self._lock:
            if self._fd is None:
                return False
            for code in sequence:
                if not self._emit_locked(EV_KEY, int(code), 1):
                    return False
            return self._emit_locked(EV_SYN, SYN_REPORT, 0)

    def release_sequence(self, sequence: Sequence[int]) -> bool:
        with self._lock:
            if self._fd is None:
                return False
            for code in reversed(sequence):
                if not self._emit_locked(EV_KEY, int(code), 0):
                    return False
            return self._emit_locked(EV_SYN, SYN_REPORT, 0)

    def tap_sequence(self, sequence: Sequence[int], hold_seconds: float = 0.012) -> bool:
        if not self.hold_sequence(sequence):
            return False
        time.sleep(hold_seconds)
        return self.release_sequence(sequence)

    def tap_code(self, code: int) -> bool:
        return self.tap_sequence((int(code),))

    def emit_raw(self, event_type: int, code: int, value: int) -> bool:
        with self._lock:
            if self._fd is None:
                return False
            self._emit_locked(event_type, code, value)
            if event_type != EV_SYN:
                self._emit_locked(EV_SYN, SYN_REPORT, 0)
            return True

    def _emit_locked(self, event_type: int, code: int, value: int) -> bool:
        assert self._fd is not None
        payload = EVENT_STRUCT.pack(0, 0, event_type, code, value)
        try:
            os.write(self._fd, payload)
            return True
        except OSError:
            return False


def find_remote_event_devices() -> List[RemoteEventDevice]:
    devices: List[RemoteEventDevice] = []
    for sys_path in sorted(glob.glob("/sys/class/input/event*")):
        device_path = Path(sys_path) / "device"
        try:
            name = (device_path / "name").read_text(encoding="utf-8", errors="replace").strip()
            vendor = int((device_path / "id" / "vendor").read_text().strip(), 16)
            product = int((device_path / "id" / "product").read_text().strip(), 16)
        except (OSError, ValueError):
            continue
        matches_identity = vendor == 0x2717 and product == 0x32B8
        matches_name = any(candidate in name.strip().lower() for candidate in REMOTE_NAMES)
        if not matches_identity and not matches_name:
            continue
        if not (device_path / "capabilities" / "key").exists():
            continue
        devices.append(
            RemoteEventDevice(
                path="/dev/input/" + Path(sys_path).name,
                name=name,
                vendor=vendor,
                product=product,
            )
        )
    return devices


class InputBindingManager:
    def __init__(
        self,
        config: AppConfig,
        logger: RuntimeLogger,
        on_state: Callable[[str], None],
        on_last_event: Callable[[str], None],
        on_voice_down: Callable[[float], None],
        on_voice_up: Callable[[], None],
    ) -> None:
        self.config = config
        self.logger = logger
        self.on_state = on_state
        self.on_last_event = on_last_event
        self.on_voice_down = on_voice_down
        self.on_voice_up = on_voice_up
        self.injector = UInputKeyboard(logger)
        self._devices: Dict[int, RemoteEventDevice] = {}
        self._buffers: Dict[int, bytearray] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._active_buttons: Set[str] = set()
        self._repeat_stops: Dict[str, threading.Event] = {}
        self._voice_sequence: Optional[Tuple[int, ...]] = None
        self._voice_held = False
        self._last_scan_message = ""
        self._last_scan_log_at = 0.0
        self._grab_failures: Dict[str, float] = {}

    def start(self) -> None:
        if not self.config.mapping_enabled:
            self.on_state("已关闭（系统直通）")
            self.logger.write("INPUT BINDING phase=start result=skipped reason=disabled")
            return
        try:
            self._voice_sequence = parse_key_sequence(self.config.voice_trigger)
        except KeySequenceError:
            self._voice_sequence = parse_key_sequence("F5")
            self.logger.write("INPUT BINDING phase=voice_trigger result=fallback reason=invalid")
        if not self.injector.open():
            self.on_state("权限不足（系统直通）")
            return
        self._rescan()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sayall-evdev", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2)
        with self._lock:
            device_fds = list(self._devices.keys())
        for descriptor in device_fds:
            self._release_device(descriptor)
        if self._voice_held and self._voice_sequence is not None:
            self.injector.release_sequence(self._voice_sequence)
        self._voice_held = False
        self._cancel_all()
        self.injector.close()
        self.logger.write("INPUT BINDING phase=stop result=ok")

    def refresh(self) -> None:
        if not self.config.mapping_enabled or not self.injector.available:
            return
        self._rescan()

    def _rescan(self) -> None:
        candidates = find_remote_event_devices()
        with self._lock:
            current_paths = {device.path for device in self._devices.values()}
        added = 0
        for candidate in candidates:
            if candidate.path in current_paths:
                continue
            descriptor = self._grab_device(candidate)
            if descriptor is not None:
                added += 1
        with self._lock:
            count = len(self._devices)
        if count == 0:
            self._set_scan_status("等待遥控器按键接口", "no_device")
        elif added:
            self.on_state("按键映射已就绪")
            self._last_scan_message = "ready"
            self.logger.write("INPUT BINDING phase=grab result=ok devices=%d" % count)

    def _set_scan_status(self, message: str, reason: str) -> None:
        now = time.monotonic()
        if message != self._last_scan_message:
            self._last_scan_message = message
            self.on_state(message)
        if now - self._last_scan_log_at >= 30.0:
            self._last_scan_log_at = now
            self.logger.write("INPUT BINDING phase=scan result=waiting reason=%s" % reason)

    def _grab_device(self, candidate: RemoteEventDevice) -> Optional[int]:
        if not self.injector.available:
            return None
        now = time.monotonic()
        previous_failure = self._grab_failures.get(candidate.path, 0.0)
        if now - previous_failure < 30.0:
            return None
        try:
            descriptor = os.open(candidate.path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as error:
            self._grab_failures[candidate.path] = now
            self.logger.write("INPUT GRAB phase=open result=failed error=%s" % type(error).__name__)
            return None
        try:
            fcntl.ioctl(descriptor, EVIOCGRAB, 1)
        except OSError as error:
            try:
                os.close(descriptor)
            except OSError:
                pass
            self._grab_failures[candidate.path] = now
            self._set_scan_status("无权读取遥控器按键", "permission")
            self.logger.write("INPUT GRAB phase=grab result=failed error=%s" % type(error).__name__)
            return None
        self._grab_failures.pop(candidate.path, None)
        with self._lock:
            self._devices[descriptor] = candidate
            self._buffers[descriptor] = bytearray()
        return descriptor

    def _release_device(self, descriptor: int) -> None:
        with self._lock:
            self._devices.pop(descriptor, None)
            self._buffers.pop(descriptor, None)
            remaining = len(self._devices)
        try:
            fcntl.ioctl(descriptor, EVIOCGRAB, 0)
        except OSError:
            pass
        try:
            os.close(descriptor)
        except OSError:
            pass
        if remaining == 0:
            self._cancel_all()
            self._set_scan_status("等待遥控器按键接口", "device_disconnected")

    def _cancel_all(self) -> None:
        with self._lock:
            self._active_buttons.clear()
            stops = list(self._repeat_stops.values())
            self._repeat_stops.clear()
        for stop in stops:
            stop.set()
        if self._voice_held and self._voice_sequence is not None:
            self.injector.release_sequence(self._voice_sequence)
        self._voice_held = False

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                descriptors = list(self._devices.keys())
            if not descriptors:
                time.sleep(0.5)
                self._rescan()
                continue
            try:
                readable, _, _ = select.select(descriptors, [], [], 0.5)
            except (OSError, ValueError):
                readable = []
            for descriptor in readable:
                self._read_device(descriptor)

    def _read_device(self, descriptor: int) -> None:
        try:
            chunk = os.read(descriptor, EVENT_STRUCT.size * 64)
        except BlockingIOError:
            return
        except OSError as error:
            if error.errno in (errno.ENODEV, errno.EIO):
                self._release_device(descriptor)
            return
        if not chunk:
            self._release_device(descriptor)
            return
        with self._lock:
            buffer = self._buffers.setdefault(descriptor, bytearray())
            buffer.extend(chunk)
            events = []
            while len(buffer) >= EVENT_STRUCT.size:
                event = EVENT_STRUCT.unpack(bytes(buffer[:EVENT_STRUCT.size]))
                del buffer[:EVENT_STRUCT.size]
                events.append(event)
        for _seconds, _microseconds, event_type, code, value in events:
            if event_type == EV_KEY and value in (0, 1):
                self._handle_key(code, value == 1)

    def _handle_key(self, code: int, pressed: bool) -> None:
        kind, button = classify_key(code)
        if kind == "voice":
            if pressed and not self._voice_held and self._voice_sequence is not None:
                self._voice_held = True
                self.injector.hold_sequence(self._voice_sequence)
                self.on_voice_down(time.monotonic())
                self.on_last_event("语音键按下")
            elif not pressed and self._voice_held:
                self._voice_held = False
                if self._voice_sequence is not None:
                    self.injector.release_sequence(self._voice_sequence)
                self.on_voice_up()
                self.on_last_event("语音键松开")
            return
        if kind == "passthrough":
            if pressed:
                self.injector.tap_code(code)
            return
        if button is None:
            return
        if pressed:
            with self._lock:
                if button in self._active_buttons:
                    self.logger.write("INPUT BINDING phase=press result=deduplicated")
                    return
                self._active_buttons.add(button)
            action = self.config.bindings.get(button, "disabled")
            sequence = action_sequence(action)
            if sequence is not None:
                self.injector.tap_sequence(sequence)
            self.on_last_event("按键：%s" % button)
            if button in self.config.hold_repeat_buttons and sequence is not None:
                self._start_repeat(button, sequence)
            return
        with self._lock:
            self._active_buttons.discard(button)
            stop = self._repeat_stops.pop(button, None)
        if stop is not None:
            stop.set()

    def _start_repeat(self, button: str, sequence: Sequence[int]) -> None:
        with self._lock:
            previous = self._repeat_stops.pop(button, None)
            stop = threading.Event()
            self._repeat_stops[button] = stop
        if previous is not None:
            previous.set()

        def repeat() -> None:
            if stop.wait(0.4):
                return
            while not stop.wait(0.08):
                with self._lock:
                    if button not in self._active_buttons:
                        return
                self.injector.tap_sequence(sequence, hold_seconds=0.005)

        threading.Thread(target=repeat, name="sayall-repeat", daemon=True).start()
