"""Application composition for the Linux client."""

from __future__ import annotations

import signal
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from .audio import AudioPipeline, AudioStatus, OrderedPCMWriter, VirtualMic
from .bluetooth import BluetoothRemoteClient, RemoteCandidate
from .config import AppConfig, default_fifo_path, default_log_path
from .controller import ControllerState, VoiceController
from .input_linux import InputBindingManager
from .localization import Localization
from .logging import RuntimeLogger


class SayAllApplication:
    def __init__(self, headless: bool = False, echo_logs: bool = False) -> None:
        self.headless = headless
        self.localization = Localization()
        self.config = AppConfig.load()
        self.logger = RuntimeLogger(default_log_path(), echo=echo_logs)
        self.virtual_mic = VirtualMic(default_fifo_path(), self.logger)
        self.writer = OrderedPCMWriter(self.virtual_mic, self.logger)
        self.audio = AudioPipeline(
            self.virtual_mic,
            self.writer,
            self.logger,
            volume_getter=lambda: self.config.output_volume_percent,
        )
        self.controller = VoiceController(
            self.audio,
            self.logger,
            on_state=self._on_controller_state,
            send_control=lambda payload: self.bluetooth.write_control(payload),
            on_voice_activity=self._on_voice_activity,
        )
        self.bluetooth = BluetoothRemoteClient(
            self.logger,
            on_state=self._on_bluetooth_state,
            on_devices=self._on_devices,
            on_control=self._on_control,
            on_audio=self._on_audio,
            on_model=self._on_model,
            on_selected_device=self._on_selected_device,
        )
        self.input_manager: Optional[InputBindingManager] = None
        self.window = None
        self._devices: List[RemoteCandidate] = []
        self._selected_device_path: Optional[str] = None
        self._selected_device_name = "小米蓝牙遥控器"
        self._state_listeners: List[Callable[[ControllerState], None]] = []
        self._device_listeners: List[Callable[[List[RemoteCandidate], Optional[str]], None]] = []
        self._audio_listeners: List[Callable[[AudioStatus], None]] = []
        self._shutdown_lock = threading.Lock()
        self._shutting_down = False
        self._glib = None

    def bind_glib(self, glib) -> None:
        self._glib = glib

    def add_state_listener(self, listener: Callable[[ControllerState], None]) -> None:
        self._state_listeners.append(listener)

    def add_device_listener(self, listener: Callable[[List[RemoteCandidate], Optional[str]], None]) -> None:
        self._device_listeners.append(listener)

    def add_audio_listener(self, listener: Callable[[AudioStatus], None]) -> None:
        self._audio_listeners.append(listener)

    def start(self) -> None:
        self.logger.write(
            "APP phase=start result=ok",
            {"mode": "headless" if self.headless else "desktop", "config_load_error": self.config.load_error or "none"},
        )
        audio_status = self.audio.start()
        self._emit_audio_status(audio_status)
        self.controller.set_audio_state(self._audio_status_text(audio_status))
        self._start_input()
        self.bluetooth.start(self.config.device_path)
        self._emit_state(self.controller.state)

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._shutting_down:
                return
            self._shutting_down = True
        self.logger.write("APP phase=stop result=starting")
        self.controller.shutdown()
        if self.input_manager is not None:
            self.input_manager.stop()
            self.input_manager = None
        self.bluetooth.stop()
        self.audio.stop()
        try:
            self.config.save()
        except OSError as error:
            self.logger.write("CONFIG phase=save result=failed reason=shutdown error=%s" % type(error).__name__)
        self.logger.write("APP phase=stop result=ok")
        self.logger.close()

    def refresh(self) -> None:
        self.bluetooth.refresh()
        if self.input_manager is not None:
            self.input_manager.refresh()

    def reconnect(self) -> None:
        self.bluetooth.refresh()

    def select_device(self, path: str) -> None:
        self.bluetooth.select_device(path)

    def set_voice_trigger(self, value: str) -> None:
        self.config.voice_trigger = value.strip().upper() or "F5"
        self._save_config()
        self._restart_input()

    def set_volume(self, value: int) -> None:
        self.config.output_volume_percent = min(1000, max(0, int(value)))
        self._save_config()

    def set_binding(self, button: str, action: str) -> None:
        if self.config.set_binding(button, action):
            self._save_config()

    def set_mapping_enabled(self, enabled: bool) -> None:
        if self.config.mapping_enabled == enabled:
            return
        self.config.mapping_enabled = enabled
        self._save_config()
        self._restart_input()

    def prepare_audio(self) -> None:
        if self.controller.streaming:
            self.controller.update_state(last_event="语音会话中无法重启麦克风")
            return
        self.audio.stop()
        status = self.audio.start()
        self._emit_audio_status(status)
        self.controller.set_audio_state(self._audio_status_text(status))

    def _start_input(self) -> None:
        if self.input_manager is not None:
            self.input_manager.stop()
        self.input_manager = InputBindingManager(
            self.config,
            self.logger,
            on_state=self._on_mapping_state,
            on_last_event=self._on_last_event,
            on_voice_down=self.controller.note_voice_key_down,
            on_voice_up=self.controller.note_voice_key_up,
        )
        self.input_manager.start()

    def _restart_input(self) -> None:
        self._start_input()

    def _save_config(self) -> None:
        try:
            self.config.save()
        except OSError as error:
            self.logger.write("CONFIG phase=save result=failed error=%s" % type(error).__name__)

    def _audio_status_text(self, status: AudioStatus) -> str:
        if status.ready:
            return "SayAll 已就绪" if self.localization.language == "zh" else "SayAll ready"
        return "未就绪：%s" % status.detail

    def _on_controller_state(self, state: ControllerState) -> None:
        self._dispatch(lambda: self._emit_state(state))

    def _on_bluetooth_state(self, status: str, detail: str, connected: bool) -> None:
        def update() -> None:
            if connected:
                self.controller.update_state(bluetooth=status, remote=self._selected_device_name)
            else:
                self.controller.set_bluetooth_connected(False, status)
                self.controller.update_state(remote="未连接")
            self.logger.write("BLE UI phase=update result=ok reason=state")
        self._dispatch(update)

    def _on_devices(self, devices: List[RemoteCandidate], selected: Optional[str]) -> None:
        def update() -> None:
            self._devices = devices
            self._selected_device_path = selected
            selected_device = next((device for device in devices if device.path == selected), None)
            if selected_device is not None:
                self._selected_device_name = selected_device.display_name
            for listener in self._device_listeners:
                listener(devices, selected)
        self._dispatch(update)

    def _on_control(self, payload: bytes, received_at: float) -> None:
        self._dispatch(lambda: self.controller.handle_control(payload, received_at))

    def _on_audio(self, payload: bytes, received_at: float) -> None:
        self._dispatch(lambda: self.controller.handle_audio(payload, received_at))

    def _on_model(self, model: str) -> None:
        self._dispatch(lambda: self.controller.set_remote_model(model))

    def _on_selected_device(self, path: str) -> None:
        self.config.device_path = path
        self._save_config()

    def _on_mapping_state(self, text: str) -> None:
        self._dispatch(lambda: self.controller.set_mapping_state(text))

    def _on_last_event(self, text: str) -> None:
        self._dispatch(lambda: self.controller.update_state(last_event=text))

    def _on_voice_activity(self, active: bool) -> None:
        return

    def _emit_state(self, state: ControllerState) -> None:
        for listener in self._state_listeners:
            listener(state)

    def _emit_audio_status(self, status: AudioStatus) -> None:
        for listener in self._audio_listeners:
            listener(status)

    def _dispatch(self, callback: Callable[[], None]) -> None:
        if self._glib is not None:
            self._glib.idle_add(callback)
        else:
            callback()

    def run_headless(self) -> int:
        try:
            from gi.repository import GLib
        except ImportError:
            print("python3-gi is required")
            return 2
        self.bind_glib(GLib)
        loop = GLib.MainLoop()

        def quit_loop(*_args):
            loop.quit()

        signal.signal(signal.SIGINT, quit_loop)
        signal.signal(signal.SIGTERM, quit_loop)
        self.start()
        try:
            loop.run()
        finally:
            self.shutdown()
        return 0
