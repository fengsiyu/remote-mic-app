"""GTK 3 settings window and status indicator for the Linux client."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from .application import SayAllApplication
from .bluetooth import RemoteCandidate
from .config import ACTION_LABELS_EN, ACTION_LABELS_ZH, DEFAULT_BINDINGS, VALID_ACTIONS, VALID_BUTTONS
from .controller import ControllerState


class SayAllWindow:
    def __init__(self, app: SayAllApplication, on_quit: Callable[[], None]) -> None:
        self.app = app
        self.localization = app.localization
        self.on_quit = on_quit
        self._updating = False
        self._volume_source = 0
        self._tray_available = False
        self.window = Gtk.Window(title=self.localization.text("product"))
        self.window.set_default_size(780, 820)
        self.window.set_size_request(660, 700)
        if self._icon_path().exists():
            self.window.set_icon_from_file(str(self._icon_path()))
        self.window.connect("delete-event", self._on_delete)
        self._install_css()
        self._build()
        self._build_indicator()
        self.app.add_state_listener(self.update_state)
        self.app.add_device_listener(self.update_devices)
        self.app.add_audio_listener(self._on_audio_status)
        self.update_state(self.app.controller.state)

    def _t(self, key: str) -> str:
        return self.localization.text(key)

    def _icon_path(self) -> Path:
        packaged = Path("/usr/share/icons/hicolor/256x256/apps/sayall-linux.png")
        if packaged.exists():
            return packaged
        return Path(__file__).resolve().parents[2] / "Resources" / "AppIcons" / "standard.png"

    def _install_css(self) -> None:
        css = b"""
        window { font-size: 12pt; }
        .sayall-title { font-size: 21pt; font-weight: 700; }
        .sayall-subtitle { font-size: 12pt; opacity: 0.78; }
        .section-title { font-size: 14pt; font-weight: 700; margin-top: 4px; }
        .status-name { font-weight: 600; }
        .status-value { font-weight: 500; }
        .hint { font-size: 12pt; opacity: 0.82; }
        .card { border-radius: 8px; padding: 10px; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _build(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.set_border_width(20)
        self.window.add(outer)

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=self._t("product"), xalign=0)
        title.get_style_context().add_class("sayall-title")
        subtitle = Gtk.Label(label=self._t("subtitle"), xalign=0)
        subtitle.get_style_context().add_class("sayall-subtitle")
        header.pack_start(title, False, False, 0)
        header.pack_start(subtitle, False, False, 0)
        outer.pack_start(header, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        scroll.add(content)
        outer.pack_start(scroll, True, True, 0)

        content.pack_start(self._build_status_section(), False, False, 0)
        content.pack_start(self._build_remote_section(), False, False, 0)
        content.pack_start(self._build_audio_section(), False, False, 0)
        content.pack_start(self._build_voice_section(), False, False, 0)
        content.pack_start(self._build_mapping_section(), False, False, 0)
        content.pack_start(self._build_permission_section(), False, False, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.pack_start(Gtk.Label(label=""), True, True, 0)
        refresh = Gtk.Button(label=self._t("refresh"))
        refresh.connect("clicked", lambda _button: self.app.refresh())
        footer.pack_end(refresh, False, False, 0)
        outer.pack_start(footer, False, False, 0)

    def _section_box(self, title_text: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("card")
        title = Gtk.Label(label=title_text, xalign=0)
        title.get_style_context().add_class("section-title")
        box.pack_start(title, False, False, 0)
        return box

    def _hint(self, text: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0)
        label.set_line_wrap(True)
        label.get_style_context().add_class("hint")
        return label

    def _build_status_section(self) -> Gtk.Box:
        box = self._section_box(self._t("status"))
        grid = Gtk.Grid(column_spacing=14, row_spacing=6)
        self.status_labels: Dict[str, Gtk.Label] = {}
        rows = [
            ("bluetooth", "bluetooth"),
            ("remote", "remote"),
            ("voice", "voice"),
            ("audio", "audio"),
            ("mapping", "mapping"),
            ("last_event", "last_event"),
        ]
        for row, (field, label_key) in enumerate(rows):
            name = Gtk.Label(label=self._t(label_key), xalign=0)
            name.get_style_context().add_class("status-name")
            value = Gtk.Label(label="…", xalign=0)
            value.set_selectable(True)
            value.get_style_context().add_class("status-value")
            grid.attach(name, 0, row, 1, 1)
            grid.attach(value, 1, row, 1, 1)
            self.status_labels[field] = value
        box.pack_start(grid, False, False, 0)
        return box

    def _build_remote_section(self) -> Gtk.Box:
        box = self._section_box(self._t("remote_section"))
        box.pack_start(self._hint(self._t("remote_hint")), False, False, 0)
        self.device_combo = Gtk.ComboBoxText()
        self.device_combo.append("", self._t("scanning"))
        self.device_combo.set_active_id("")
        self.device_combo.connect("changed", self._on_device_changed)
        box.pack_start(self.device_combo, False, False, 0)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        refresh = Gtk.Button(label=self._t("refresh"))
        refresh.connect("clicked", lambda _button: self.app.refresh())
        open_bluetooth = Gtk.Button(label=self._t("open_bluetooth"))
        open_bluetooth.connect("clicked", self._open_bluetooth_settings)
        buttons.pack_start(refresh, False, False, 0)
        buttons.pack_start(open_bluetooth, False, False, 0)
        box.pack_start(buttons, False, False, 0)
        return box

    def _build_audio_section(self) -> Gtk.Box:
        box = self._section_box(self._t("audio_section"))
        box.pack_start(self._hint(self._t("audio_hint")), False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.audio_detail = Gtk.Label(label=self._t("not_ready"), xalign=0)
        self.audio_detail.set_selectable(True)
        prepare = Gtk.Button(label=self._t("prepare_audio"))
        prepare.connect("clicked", lambda _button: self.app.prepare_audio())
        row.pack_start(self.audio_detail, True, True, 0)
        row.pack_end(prepare, False, False, 0)
        box.pack_start(row, False, False, 0)
        return box

    def _build_voice_section(self) -> Gtk.Box:
        box = self._section_box(self._t("voice_section"))
        box.pack_start(self._hint(self._t("voice_trigger_hint")), False, False, 0)
        trigger_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=self._t("voice_trigger"), xalign=0)
        self.trigger_entry = Gtk.Entry()
        self.trigger_entry.set_text(self.app.config.voice_trigger)
        save = Gtk.Button(label=self._t("save"))
        save.connect("clicked", self._save_trigger)
        self.trigger_entry.connect("activate", self._save_trigger)
        trigger_row.pack_start(label, False, False, 0)
        trigger_row.pack_start(self.trigger_entry, True, True, 0)
        trigger_row.pack_end(save, False, False, 0)
        box.pack_start(trigger_row, False, False, 0)

        volume_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        volume_label = Gtk.Label(label=self._t("volume"), xalign=0)
        self.volume_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1000, 20)
        self.volume_scale.set_value(self.app.config.output_volume_percent)
        self.volume_scale.set_digits(0)
        self.volume_scale.set_hexpand(True)
        self.volume_scale.connect("value-changed", self._on_volume_changed)
        volume_row.pack_start(volume_label, False, False, 0)
        volume_row.pack_start(self.volume_scale, True, True, 0)
        box.pack_start(volume_row, False, False, 0)
        self.volume_value = Gtk.Label(label="%d%%" % self.app.config.output_volume_percent, xalign=1)
        box.pack_start(self.volume_value, False, False, 0)
        box.pack_start(self._hint(self._t("volume_hint")), False, False, 0)
        return box

    def _build_mapping_section(self) -> Gtk.Box:
        box = self._section_box(self._t("mapping_section"))
        self.mapping_switch = Gtk.Switch()
        self.mapping_switch.set_active(self.app.config.mapping_enabled)
        self.mapping_switch.connect("state-set", self._on_mapping_switch)
        switch_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        switch_row.pack_start(Gtk.Label(label=self._t("mapping_enabled"), xalign=0), True, True, 0)
        switch_row.pack_end(self.mapping_switch, False, False, 0)
        box.pack_start(switch_row, False, False, 0)
        box.pack_start(self._hint(self._t("mapping_hint")), False, False, 0)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        labels = ACTION_LABELS_ZH if self.localization.language == "zh" else ACTION_LABELS_EN
        self.binding_combos: Dict[str, Gtk.ComboBoxText] = {}
        for index, button in enumerate(VALID_BUTTONS):
            column = 0 if index < 6 else 2
            row = index if index < 6 else index - 6
            label = Gtk.Label(label=self.localization.button(button), xalign=0)
            combo = Gtk.ComboBoxText()
            for action in sorted(VALID_ACTIONS):
                combo.append(action, labels.get(action, action))
            combo.set_active_id(self.app.config.bindings.get(button, DEFAULT_BINDINGS[button]))
            combo.connect("changed", self._on_binding_changed, button)
            self.binding_combos[button] = combo
            grid.attach(label, column, row, 1, 1)
            grid.attach(combo, column + 1, row, 1, 1)
        box.pack_start(grid, False, False, 0)
        return box

    def _build_permission_section(self) -> Gtk.Box:
        box = self._section_box(self._t("permissions"))
        box.pack_start(self._hint(self._t("permissions_text")), False, False, 0)
        return box

    def _build_indicator(self) -> None:
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
        except (ValueError, ImportError):
            try:
                gi.require_version("AppIndicator3", "0.1")
                from gi.repository import AppIndicator3 as AppIndicator
            except (ValueError, ImportError):
                self._tray_available = False
                return
        try:
            indicator = AppIndicator.Indicator.new(
                "sayall-linux", "audio-input-microphone", AppIndicator.IndicatorCategory.APPLICATION_STATUS
            )
            icon = self._icon_path()
            if icon.exists():
                indicator.set_icon_full(str(icon), self._t("product"))
            indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            menu = Gtk.Menu()
            self.tray_status = Gtk.MenuItem(label=self._t("product"))
            self.tray_status.set_sensitive(False)
            menu.append(self.tray_status)
            open_item = Gtk.MenuItem(label=self._t("show_window"))
            open_item.connect("activate", lambda _item: self.show())
            menu.append(open_item)
            reconnect = Gtk.MenuItem(label=self._t("reconnect"))
            reconnect.connect("activate", lambda _item: self.app.reconnect())
            menu.append(reconnect)
            quit_item = Gtk.MenuItem(label=self._t("quit"))
            quit_item.connect("activate", lambda _item: self.on_quit())
            menu.append(quit_item)
            menu.show_all()
            indicator.set_menu(menu)
            self.indicator = indicator
            self._tray_available = True
        except Exception:
            self._tray_available = False

    def show(self) -> None:
        self.window.show_all()
        self.window.present()

    def update_state(self, state: ControllerState) -> None:
        if self._updating:
            return
        self._updating = True
        values = {
            "bluetooth": state.bluetooth,
            "remote": state.remote,
            "voice": state.voice,
            "audio": state.audio,
            "mapping": state.mapping,
            "last_event": state.last_event,
        }
        for key, value in values.items():
            label = self.status_labels.get(key) if hasattr(self, "status_labels") else None
            if label is not None:
                label.set_text(value)
        if hasattr(self, "tray_status"):
            connected = "已连接" in state.bluetooth or "connected" in state.bluetooth.lower()
            prefix = "●" if connected else "○"
            self.tray_status.set_label("%s %s" % (self._t("product"), prefix))
        self._updating = False

    def update_devices(self, devices: List[RemoteCandidate], selected: Optional[str]) -> None:
        if self._updating:
            return
        self._updating = True
        self.device_combo.remove_all()
        if not devices:
            self.device_combo.append("", self._t("no_device"))
            self.device_combo.set_active_id("")
            self.device_combo.set_sensitive(False)
            self._updating = False
            return
        self.device_combo.set_sensitive(True)
        for device in devices:
            suffix = " · 已连接" if device.connected else ("" if device.paired else " · 需配对")
            self.device_combo.append(device.path, device.display_name + suffix)
        target = selected if selected in {device.path for device in devices} else devices[0].path
        self.device_combo.set_active_id(target)
        self._updating = False

    def _on_device_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._updating:
            return
        path = combo.get_active_id()
        if path:
            self.app.select_device(path)

    def _on_audio_status(self, status) -> None:
        if self.app.localization.language == "zh":
            text = "SayAll 已就绪" if status.ready else "未就绪：%s" % status.detail
        else:
            text = "SayAll ready" if status.ready else "Not ready: %s" % status.detail
        self.audio_detail.set_text(text)

    def _save_trigger(self, *_args) -> None:
        value = self.trigger_entry.get_text().strip()
        if not value:
            self.trigger_entry.set_text(self.app.config.voice_trigger)
            return
        self.app.set_voice_trigger(value)
        self.trigger_entry.set_text(self.app.config.voice_trigger)

    def _on_volume_changed(self, scale: Gtk.Scale) -> None:
        value = int(scale.get_value())
        self.volume_value.set_text("%d%%" % value)
        if self._volume_source:
            GLib.source_remove(self._volume_source)
        self._volume_source = GLib.timeout_add(250, self._commit_volume, value)

    def _commit_volume(self, value: int) -> bool:
        self._volume_source = 0
        self.app.set_volume(value)
        return False

    def _on_mapping_switch(self, _switch: Gtk.Switch, state: bool) -> bool:
        self.app.set_mapping_enabled(bool(state))
        return False

    def _on_binding_changed(self, combo: Gtk.ComboBoxText, button: str) -> None:
        if self._updating:
            return
        action = combo.get_active_id()
        if action:
            self.app.set_binding(button, action)

    def _open_bluetooth_settings(self, _button: Gtk.Button) -> None:
        if shutil.which("gnome-control-center"):
            try:
                subprocess.Popen(["gnome-control-center", "bluetooth"])
                return
            except OSError:
                pass
        if shutil.which("blueman-manager"):
            try:
                subprocess.Popen(["blueman-manager"])
                return
            except OSError:
                pass

    def _on_delete(self, *_args) -> bool:
        if self._tray_available:
            self.window.hide()
            return True
        self.on_quit()
        return True
