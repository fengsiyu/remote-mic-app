"""Minimal Chinese and English strings for the Linux client."""

from __future__ import annotations

import locale
import os
from typing import Dict


ZH: Dict[str, str] = {
    "product": "无线麦 SayAll",
    "subtitle": "Linux amd64 · Ubuntu 20.04 及以上",
    "status": "运行状态",
    "bluetooth": "蓝牙连接",
    "remote": "遥控器",
    "voice": "语音链路",
    "audio": "虚拟麦克风",
    "mapping": "按键映射",
    "last_event": "最近事件",
    "remote_section": "遥控器连接",
    "remote_hint": "先在系统蓝牙设置中配对名称为 MI RC、Xiaomi Bluetooth Remote 2/2 Pro 或小米蓝牙语音遥控器的设备。",
    "refresh": "刷新",
    "open_bluetooth": "打开系统蓝牙设置",
    "audio_section": "语音输出",
    "audio_hint": "在目标语音工具中选择麦克风“SayAll”。按下遥控器语音键后，SayAll 会立即开始收音，松开后完整排空尾音。",
    "prepare_audio": "重新准备麦克风",
    "voice_section": "语音键与音量",
    "voice_trigger": "语音工具触发按键",
    "voice_trigger_hint": "默认 F5。需要自定义组合键时，系统会安装 uinput 权限规则；未授权时保持系统直通。",
    "volume": "输出音量",
    "volume_hint": "0% 静音，100% 保持原始幅度，1000% 约提升 20 dB。",
    "mapping_section": "基础按键映射",
    "mapping_hint": "仅在具备 input 与 uinput 权限时接管遥控器按键；没有权限时原生按键继续直通。",
    "mapping_enabled": "启用遥控器按键映射",
    "save": "保存",
    "saved": "设置已保存",
    "permissions": "权限说明",
    "permissions_text": "若按键映射显示权限不足，请退出后重新登录，并确认当前用户在 input 组中。Wayland 与 X11 均通过 Linux 输入子系统执行。",
    "open": "打开设置",
    "quit": "退出",
    "reconnect": "重新连接",
    "ready": "就绪",
    "not_ready": "未就绪",
    "scanning": "正在扫描",
    "no_device": "尚未发现遥控器",
    "show_window": "打开无线麦 SayAll",
    "button_power": "电源",
    "button_up": "上",
    "button_left": "左",
    "button_ok": "确定",
    "button_right": "右",
    "button_down": "下",
    "button_back": "返回",
    "button_volume_up": "音量 +",
    "button_home": "主页",
    "button_volume_down": "音量 −",
    "button_menu": "菜单",
    "button_tv": "TV",
}

EN: Dict[str, str] = {
    "product": "SayAll",
    "subtitle": "Linux amd64 · Ubuntu 20.04 or later",
    "status": "Status",
    "bluetooth": "Bluetooth",
    "remote": "Remote",
    "voice": "Voice link",
    "audio": "Virtual microphone",
    "mapping": "Button mapping",
    "last_event": "Last event",
    "remote_section": "Remote connection",
    "remote_hint": "Pair MI RC, Xiaomi Bluetooth Remote 2/2 Pro, or Xiaomi Bluetooth Voice Remote in system Bluetooth settings first.",
    "refresh": "Refresh",
    "open_bluetooth": "Open Bluetooth settings",
    "audio_section": "Voice output",
    "audio_hint": "Choose the SayAll microphone in the target voice tool. Press the remote voice key to start immediately; release to drain the complete tail.",
    "prepare_audio": "Prepare microphone",
    "voice_section": "Voice key and volume",
    "voice_trigger": "Voice tool trigger",
    "voice_trigger_hint": "F5 by default. Custom combinations require the packaged uinput permission rule; otherwise native keys pass through.",
    "volume": "Output volume",
    "volume_hint": "0% mutes, 100% keeps the decoded level, and 1000% is about +20 dB.",
    "mapping_section": "Basic button mapping",
    "mapping_hint": "Keys are captured only when input and uinput permissions are available. Without permission, native keys continue to pass through.",
    "mapping_enabled": "Enable remote button mapping",
    "save": "Save",
    "saved": "Settings saved",
    "permissions": "Permissions",
    "permissions_text": "If mapping reports missing permission, sign out and back in and verify the current user is in the input group. Both Wayland and X11 use the Linux input subsystem.",
    "open": "Open settings",
    "quit": "Quit",
    "reconnect": "Reconnect",
    "ready": "Ready",
    "not_ready": "Not ready",
    "scanning": "Scanning",
    "no_device": "No remote found yet",
    "show_window": "Open SayAll",
    "button_power": "Power",
    "button_up": "Up",
    "button_left": "Left",
    "button_ok": "OK",
    "button_right": "Right",
    "button_down": "Down",
    "button_back": "Back",
    "button_volume_up": "Volume +",
    "button_home": "Home",
    "button_volume_down": "Volume −",
    "button_menu": "Menu",
    "button_tv": "TV",
}


def language_code() -> str:
    value = os.environ.get("LANGUAGE") or os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    if not value:
        try:
            value = locale.getdefaultlocale()[0] or ""
        except (ValueError, TypeError):
            value = ""
    return "zh" if value.lower().startswith("zh") else "en"


class Localization:
    def __init__(self) -> None:
        self.language = language_code()
        self.strings = ZH if self.language == "zh" else EN

    def text(self, key: str) -> str:
        return self.strings.get(key, key)

    def button(self, name: str) -> str:
        return self.text("button_" + name)
