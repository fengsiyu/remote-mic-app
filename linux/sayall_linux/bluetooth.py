"""BlueZ D-Bus transport for Xiaomi RC001/RC003 ATVV remotes."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .atvv import (
    AUDIO_UUID,
    BATTERY_LEVEL_UUID,
    CONTROL_UUID,
    GET_CAPABILITIES_V10,
    MODEL_NUMBER_UUID,
    SERVICE_UUID,
    TRANSMIT_UUID,
)
from .logging import RuntimeLogger


BLUEZ_SERVICE = "org.bluez"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
ADAPTER_INTERFACE = "org.bluez.Adapter1"
DEVICE_INTERFACE = "org.bluez.Device1"
GATT_SERVICE_INTERFACE = "org.bluez.GattService1"
GATT_CHARACTERISTIC_INTERFACE = "org.bluez.GattCharacteristic1"
AGENT_INTERFACE = "org.bluez.Agent1"
AGENT_MANAGER_INTERFACE = "org.bluez.AgentManager1"
AGENT_PATH = "/com/sayall/LinuxAgent"

ACCEPTED_NAMES = {
    "mi rc",
    "xiaomi bluetooth remote 2",
    "xiaomi bluetooth remote 2 pro",
    "小米蓝牙语音遥控器",
}


@dataclass(frozen=True)
class RemoteCandidate:
    path: str
    display_name: str
    paired: bool
    connected: bool
    services_resolved: bool


@dataclass
class GattPaths:
    transmit: str
    audio: str
    control: str
    model_number: Optional[str] = None
    battery_level: Optional[str] = None


class BluetoothError(RuntimeError):
    pass


class BluetoothRemoteClient:
    def __init__(
        self,
        logger: RuntimeLogger,
        on_state: Callable[[str, str, bool], None],
        on_devices: Callable[[List[RemoteCandidate], Optional[str]], None],
        on_control: Callable[[bytes, float], None],
        on_audio: Callable[[bytes, float], None],
        on_model: Callable[[str], None],
        on_selected_device: Callable[[str], None],
    ) -> None:
        self.logger = logger
        self.on_state = on_state
        self.on_devices = on_devices
        self.on_control = on_control
        self.on_audio = on_audio
        self.on_model = on_model
        self.on_selected_device = on_selected_device
        self._dbus = None
        self._glib = None
        self._bus = None
        self._manager = None
        self._agent = None
        self._signal_matches: List[object] = []
        self._devices: Dict[str, RemoteCandidate] = {}
        self._objects: Dict[str, Dict[str, Dict[str, object]]] = {}
        self._preferred_path: Optional[str] = None
        self._selected_path: Optional[str] = None
        self._gatt: Optional[GattPaths] = None
        self._connect_in_flight: Optional[str] = None
        self._pair_in_flight: Optional[str] = None
        self._notify_requested = False
        self._caps_requested = False
        self._running = False
        self._poll_source = 0
        self._caps_source = 0
        self._last_connect_attempt: Dict[str, float] = {}
        self._remote_model: Optional[str] = None
        self._last_value_path: Dict[str, float] = {}

    @property
    def selected_path(self) -> Optional[str]:
        return self._selected_path

    def start(self, preferred_path: Optional[str]) -> None:
        if self._running:
            return
        try:
            import dbus
            import dbus.service
            from dbus.mainloop.glib import DBusGMainLoop
        except ImportError as error:
            self.on_state("蓝牙组件缺失", "请安装 python3-dbus", False)
            self.logger.write("BLE phase=start result=failed reason=dbus_missing error=%s" % type(error).__name__)
            return
        self._dbus = dbus
        try:
            from gi.repository import GLib
            self._glib = GLib
        except ImportError as error:
            self.on_state("界面组件缺失", "请安装 python3-gi", False)
            self.logger.write("BLE phase=start result=failed reason=gi_missing error=%s" % type(error).__name__)
            return
        DBusGMainLoop(set_as_default=True)
        try:
            self._bus = dbus.SystemBus()
        except dbus.DBusException as error:
            self.on_state("无法访问系统蓝牙", "请确认 BlueZ 服务正在运行", False)
            self.logger.write("BLE phase=start result=failed reason=system_bus error=%s" % error.get_dbus_name())
            self._bus = None
            return
        self._running = True
        self._preferred_path = preferred_path
        try:
            root = self._bus.get_object(BLUEZ_SERVICE, "/")
            self._manager = dbus.Interface(root, OBJECT_MANAGER)
        except dbus.DBusException as error:
            self.logger.write("BLE phase=start result=failed reason=bluez_unavailable error=%s" % error.get_dbus_name())
            self.on_state("BlueZ 服务不可用", "请安装并启动 bluez", False)
            return
        self._register_signals()
        self._register_agent()
        self._refresh_objects()
        self._start_discovery()
        self._poll_source = self._glib.timeout_add_seconds(5, self._poll)
        self.logger.write("BLE phase=start result=ok")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._glib is not None:
            for source in (self._poll_source, self._caps_source):
                if source:
                    try:
                        self._glib.source_remove(source)
                    except Exception:
                        pass
        self._poll_source = 0
        self._caps_source = 0
        self._stop_discovery()
        if self._gatt is not None:
            self._stop_notify(self._gatt.audio)
            self._stop_notify(self._gatt.control)
        self._gatt = None
        if self._bus is not None and self._agent is not None and self._dbus is not None:
            try:
                manager = self._bus.get_object(BLUEZ_SERVICE, "/org/bluez")
                manager_interface = self._dbus.Interface(manager, AGENT_MANAGER_INTERFACE)
                manager_interface.UnregisterAgent(AGENT_PATH)
            except self._dbus.DBusException:
                pass
        self._agent = None
        for match in self._signal_matches:
            try:
                match.remove()
            except Exception:
                pass
        self._signal_matches = []
        self._bus = None
        self._manager = None
        self.logger.write("BLE phase=stop result=ok")

    def select_device(self, path: str) -> None:
        if path not in self._devices:
            return
        if path == self._selected_path and self._gatt is not None:
            return
        self._preferred_path = path
        self.on_selected_device(path)
        if self._gatt is not None:
            previous = self._selected_path
            self._stop_notify(self._gatt.audio)
            self._stop_notify(self._gatt.control)
            self._gatt = None
            self._notify_requested = False
            self._caps_requested = False
            self.logger.write("BLE CONNECT phase=switch result=accepted")
            self.on_state("正在切换遥控器", "", False)
            if previous and previous != path:
                self._disconnect(previous)
        candidate = self._devices[path]
        self._connect_device(path, pair_if_needed=not candidate.paired)

    def refresh(self) -> None:
        self._refresh_objects()
        self._start_discovery()

    def write_control(self, payload: bytes) -> bool:
        if self._gatt is None or not self._running:
            return False
        return self._write_characteristic(self._gatt.transmit, payload)

    def _register_signals(self) -> None:
        assert self._bus is not None
        dbus = self._dbus
        try:
            self._signal_matches.append(
                self._bus.add_signal_receiver(
                    self._on_properties_changed,
                    dbus_interface=PROPERTIES,
                    signal_name="PropertiesChanged",
                    bus_name=BLUEZ_SERVICE,
                    path_keyword="path",
                )
            )
            self._signal_matches.append(
                self._bus.add_signal_receiver(
                    self._on_interfaces_added,
                    dbus_interface=OBJECT_MANAGER,
                    signal_name="InterfacesAdded",
                    bus_name=BLUEZ_SERVICE,
                )
            )
        except dbus.DBusException as error:
            self.logger.write("BLE SIGNALS phase=register result=failed error=%s" % error.get_dbus_name())

    def _register_agent(self) -> None:
        assert self._bus is not None
        dbus = self._dbus

        class PairingAgent(dbus.service.Object):
            def __init__(agent_self, bus, path):
                super().__init__(bus, path)
                agent_self._bus = bus

            @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
            def Release(agent_self):
                return None

            @dbus.service.method(AGENT_INTERFACE, in_signature="ou", out_signature="")
            def RequestConfirmation(agent_self, device, passkey):
                return None

            @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="s")
            def RequestPinCode(agent_self, device):
                return "0000"

            @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="u")
            def RequestPasskey(agent_self, device):
                return dbus.UInt32(0)

            @dbus.service.method(AGENT_INTERFACE, in_signature="ouq", out_signature="")
            def DisplayPasskey(agent_self, device, passkey, entered):
                return None

            @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
            def DisplayPinCode(agent_self, device, pincode):
                return None

            @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="")
            def RequestAuthorization(agent_self, device):
                return None

            @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
            def Cancel(agent_self):
                return None

        try:
            bus_name = dbus.service.BusName("com.sayall.LinuxAgent", self._bus, do_not_queue=True)
            self._agent = PairingAgent(self._bus, AGENT_PATH)
            self._agent_name = bus_name
            root = self._bus.get_object(BLUEZ_SERVICE, "/org/bluez")
            manager = dbus.Interface(root, AGENT_MANAGER_INTERFACE)
            manager.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
            try:
                manager.RequestDefaultAgent(AGENT_PATH)
            except dbus.DBusException:
                pass
            self.logger.write("BLE AGENT phase=register result=ok")
        except dbus.DBusException as error:
            self._agent = None
            self.logger.write("BLE AGENT phase=register result=skipped reason=bus error=%s" % error.get_dbus_name())

    def _refresh_objects(self) -> None:
        if self._manager is None:
            return
        try:
            objects = self._manager.GetManagedObjects()
        except self._dbus.DBusException as error:
            self.logger.write("BLE OBJECTS phase=refresh result=failed error=%s" % error.get_dbus_name())
            return
        self._objects = {
            str(path): {str(interface): dict(properties) for interface, properties in interfaces.items()}
            for path, interfaces in objects.items()
        }
        self._rebuild_candidates()
        self._emit_devices()
        self._consider_connection()

    def _rebuild_candidates(self) -> None:
        candidates: Dict[str, RemoteCandidate] = {}
        for path, interfaces in self._objects.items():
            properties = interfaces.get(DEVICE_INTERFACE)
            if properties is None or not self._is_matching_device(properties):
                continue
            name = str(properties.get("Alias") or properties.get("Name") or "小米蓝牙遥控器")
            candidates[path] = RemoteCandidate(
                path=path,
                display_name=name,
                paired=bool(properties.get("Paired", False)),
                connected=bool(properties.get("Connected", False)),
                services_resolved=bool(properties.get("ServicesResolved", False)),
            )
        self._devices = candidates

    def _is_matching_device(self, properties: Dict[str, object]) -> bool:
        uuids = properties.get("UUIDs") or []
        normalized_uuids = {str(value).lower() for value in uuids}
        if SERVICE_UUID in normalized_uuids:
            return True
        names = [str(properties.get("Name") or ""), str(properties.get("Alias") or "")]
        return any(name.strip().lower() in ACCEPTED_NAMES for name in names)

    def _emit_devices(self) -> None:
        values = sorted(self._devices.values(), key=lambda item: (not item.connected, not item.paired, item.display_name.lower()))
        self.on_devices(values, self._selected_path or self._preferred_path)

    def _consider_connection(self) -> None:
        if not self._devices or self._gatt is not None or self._connect_in_flight is not None:
            return
        target = self._selected_path or self._preferred_path
        if target not in self._devices:
            connected = [candidate for candidate in self._devices.values() if candidate.connected]
            paired = [candidate for candidate in self._devices.values() if candidate.paired]
            if connected:
                target = connected[0].path
            elif paired:
                target = paired[0].path
            else:
                return
        candidate = self._devices[target]
        if candidate.connected and candidate.services_resolved:
            self._selected_path = target
            self._setup_gatt(target)
            return
        now = time.monotonic()
        if now - self._last_connect_attempt.get(target, 0.0) < 3.0:
            return
        self._last_connect_attempt[target] = now
        self._connect_device(target, pair_if_needed=not candidate.paired)

    def _connect_device(self, path: str, pair_if_needed: bool) -> None:
        if self._bus is None or self._connect_in_flight is not None:
            return
        dbus = self._dbus
        self._connect_in_flight = path
        self._selected_path = path
        self.on_state("正在连接遥控器", "", False)
        try:
            device_object = self._bus.get_object(BLUEZ_SERVICE, path)
            properties = dbus.Interface(device_object, PROPERTIES)
            device = dbus.Interface(device_object, DEVICE_INTERFACE)
            try:
                properties.Set(DEVICE_INTERFACE, "Trusted", dbus.Boolean(True))
            except dbus.DBusException:
                pass
        except dbus.DBusException as error:
            self._connect_in_flight = None
            self.logger.write("BLE CONNECT phase=prepare result=failed error=%s" % error.get_dbus_name())
            return

        def connected():
            self._connect_in_flight = None
            self.logger.write("BLE CONNECT phase=connect result=ok")
            self.on_state("蓝牙已连接", "正在准备语音链路", True)
            self._refresh_objects()

        def connect_failed(error):
            self._connect_in_flight = None
            error_name = getattr(error, "get_dbus_name", lambda: "DBusException")()
            self.logger.write("BLE CONNECT phase=connect result=failed error=%s" % error_name)
            self.on_state("连接失败", "请先在系统蓝牙中配对遥控器", False)

        def pair_failed(error):
            self._pair_in_flight = None
            error_name = getattr(error, "get_dbus_name", lambda: "DBusException")()
            if error_name.endswith("AlreadyExists"):
                device.Connect(reply_handler=connected, error_handler=connect_failed, timeout=30)
            else:
                self._connect_in_flight = None
                self.logger.write("BLE PAIR phase=pair result=failed error=%s" % error_name)
                self.on_state("需要配对", "请在系统蓝牙设置中连接遥控器", False)

        def paired():
            self._pair_in_flight = None
            self.logger.write("BLE PAIR phase=pair result=ok")
            try:
                properties.Set(DEVICE_INTERFACE, "Trusted", dbus.Boolean(True))
            except dbus.DBusException:
                pass
            device.Connect(reply_handler=connected, error_handler=connect_failed, timeout=30)

        try:
            if pair_if_needed:
                self._pair_in_flight = path
                device.Pair(reply_handler=paired, error_handler=pair_failed, timeout=30)
            else:
                device.Connect(reply_handler=connected, error_handler=connect_failed, timeout=30)
        except dbus.DBusException as error:
            self._connect_in_flight = None
            self._pair_in_flight = None
            self.logger.write("BLE CONNECT phase=dispatch result=failed error=%s" % error.get_dbus_name())

    def _disconnect(self, path: str) -> None:
        if self._bus is None:
            return
        try:
            device_object = self._bus.get_object(BLUEZ_SERVICE, path)
            device = self._dbus.Interface(device_object, DEVICE_INTERFACE)
            device.Disconnect(reply_handler=lambda: None, error_handler=lambda _error: None, timeout=10)
        except self._dbus.DBusException:
            pass

    def _setup_gatt(self, path: str) -> None:
        if not self._running:
            return
        self._refresh_objects()
        gatt = self._find_gatt_paths(path)
        if gatt is None:
            self.logger.write("BLE GATT phase=discover result=failed reason=incomplete")
            self.on_state("语音服务不可用", "请断开后在系统蓝牙中重新连接", False)
            return
        self._gatt = gatt
        self._selected_path = path
        self._notify_requested = False
        self._caps_requested = False
        self.on_state("正在准备语音链路", "正在订阅语音数据", True)
        self._start_notify(gatt.audio)
        self._start_notify(gatt.control)
        if self._caps_source:
            try:
                self._glib.source_remove(self._caps_source)
            except Exception:
                pass
        self._caps_source = self._glib.timeout_add(250, self._request_capabilities)
        self._read_optional(gatt.model_number)
        self._read_optional(gatt.battery_level)
        self.logger.write("BLE GATT phase=discover result=ok")

    def _find_gatt_paths(self, device_path: str) -> Optional[GattPaths]:
        services = []
        for path, interfaces in self._objects.items():
            service = interfaces.get(GATT_SERVICE_INTERFACE)
            if service is None:
                continue
            if str(service.get("Device", "")) != device_path:
                continue
            services.append((path, str(service.get("UUID", "")).lower()))
        service_path = None
        for path, uuid in services:
            if uuid == SERVICE_UUID:
                service_path = path
                break
        if service_path is None:
            return None
        paths: Dict[str, str] = {}
        optional: Dict[str, Optional[str]] = {"model": None, "battery": None}
        for path, interfaces in self._objects.items():
            characteristic = interfaces.get(GATT_CHARACTERISTIC_INTERFACE)
            if characteristic is None:
                continue
            uuid = str(characteristic.get("UUID", "")).lower()
            service = str(characteristic.get("Service", ""))
            if service == service_path and uuid in (TRANSMIT_UUID, AUDIO_UUID, CONTROL_UUID):
                paths[uuid] = path
            if uuid == MODEL_NUMBER_UUID:
                optional["model"] = path
            if uuid == BATTERY_LEVEL_UUID:
                optional["battery"] = path
        if not all(uuid in paths for uuid in (TRANSMIT_UUID, AUDIO_UUID, CONTROL_UUID)):
            return None
        return GattPaths(
            transmit=paths[TRANSMIT_UUID],
            audio=paths[AUDIO_UUID],
            control=paths[CONTROL_UUID],
            model_number=optional["model"],
            battery_level=optional["battery"],
        )

    def _start_notify(self, path: str) -> None:
        if self._bus is None:
            return
        try:
            characteristic_object = self._bus.get_object(BLUEZ_SERVICE, path)
            characteristic = self._dbus.Interface(characteristic_object, GATT_CHARACTERISTIC_INTERFACE)
            characteristic.StartNotify()
        except self._dbus.DBusException as error:
            self.logger.write("BLE GATT phase=notify result=failed error=%s" % error.get_dbus_name())

    def _stop_notify(self, path: str) -> None:
        if self._bus is None:
            return
        try:
            characteristic_object = self._bus.get_object(BLUEZ_SERVICE, path)
            characteristic = self._dbus.Interface(characteristic_object, GATT_CHARACTERISTIC_INTERFACE)
            characteristic.StopNotify()
        except self._dbus.DBusException:
            pass

    def _request_capabilities(self) -> bool:
        self._caps_source = 0
        if not self._running or self._gatt is None:
            return False
        if not self._audio_and_control_notifying():
            self._caps_source = self._glib.timeout_add(200, self._request_capabilities)
            return False
        if self._caps_requested:
            return False
        self._caps_requested = True
        if self._write_characteristic(self._gatt.transmit, GET_CAPABILITIES_V10):
            self.logger.write("ATVV CAPS phase=request result=written")
        else:
            self.logger.write("ATVV CAPS phase=request result=failed")
            self._caps_requested = False
        return False

    def _audio_and_control_notifying(self) -> bool:
        if self._gatt is None:
            return False
        for path in (self._gatt.audio, self._gatt.control):
            properties = self._objects.get(path, {}).get(GATT_CHARACTERISTIC_INTERFACE, {})
            if not bool(properties.get("Notifying", False)):
                return False
        return True

    def _write_characteristic(self, path: str, payload: bytes) -> bool:
        if self._bus is None:
            return False
        dbus = self._dbus
        try:
            properties = self._objects.get(path, {}).get(GATT_CHARACTERISTIC_INTERFACE, {})
            flags = {str(flag) for flag in properties.get("Flags", [])}
            options = dbus.Dictionary(
                {"type": "command" if "write-without-response" in flags else "request"},
                signature="sv",
            )
            characteristic_object = self._bus.get_object(BLUEZ_SERVICE, path)
            characteristic = dbus.Interface(characteristic_object, GATT_CHARACTERISTIC_INTERFACE)
            characteristic.WriteValue(
                dbus.Array(payload, signature="y"),
                options,
                timeout=5,
                reply_handler=lambda: None,
                error_handler=self._write_failed,
            )
            return True
        except dbus.DBusException as error:
            self.logger.write("BLE GATT phase=write result=failed error=%s" % error.get_dbus_name())
            return False

    def _write_failed(self, error) -> None:
        error_name = getattr(error, "get_dbus_name", lambda: "DBusException")()
        self.logger.write("BLE GATT phase=write result=failed error=%s" % error_name)

    def _read_optional(self, path: Optional[str]) -> None:
        if path is None or self._bus is None:
            return
        dbus = self._dbus
        try:
            characteristic_object = self._bus.get_object(BLUEZ_SERVICE, path)
            characteristic = dbus.Interface(characteristic_object, GATT_CHARACTERISTIC_INTERFACE)
            characteristic.ReadValue(dbus.Dictionary({}, signature="sv"), timeout=5, reply_handler=lambda value: self._optional_read_completed(path, value), error_handler=lambda _error: None)
        except dbus.DBusException:
            pass

    def _optional_read_completed(self, path: str, value: object) -> None:
        if self._gatt is None or path != self._gatt.model_number:
            return
        try:
            model = bytes(value).decode("utf-8", errors="replace").strip().upper()
        except (TypeError, ValueError):
            return
        if model:
            self._remote_model = model
            if "ARN9" in model:
                self.logger.write("BLE MODEL phase=read result=ok family=arn9")
            else:
                self.logger.write("BLE MODEL phase=read result=ok family=rc")
            self.on_model(model)

    def _on_properties_changed(self, interface_name, changed, invalidated, path=None):
        if path is None:
            return
        path = str(path)
        interface_name = str(interface_name)
        properties = self._objects.setdefault(path, {}).setdefault(interface_name, {})
        properties.update(dict(changed))
        for name in invalidated:
            properties.pop(str(name), None)
        if interface_name == DEVICE_INTERFACE:
            self._rebuild_candidates()
            self._emit_devices()
            candidate = self._devices.get(path)
            if candidate is not None and candidate.connected:
                if candidate.services_resolved:
                    if self._gatt is None and (self._selected_path is None or path == self._selected_path):
                        self._selected_path = path
                        self._setup_gatt(path)
                else:
                    self.on_state("蓝牙已连接", "正在准备语音链路", False)
            elif candidate is not None and self._selected_path == path:
                self._gatt = None
                self.on_state("遥控器已断开", "等待重新连接", False)
        elif interface_name == GATT_CHARACTERISTIC_INTERFACE and self._gatt is not None:
            if "Value" in changed:
                self._handle_characteristic_value(path, bytes(changed["Value"]))
            if path in (self._gatt.audio, self._gatt.control) and "Notifying" in changed:
                if bool(changed["Notifying"]):
                    self._request_capabilities()

    def _on_interfaces_added(self, path, interfaces):
        path = str(path)
        self._objects[path] = {str(interface): dict(properties) for interface, properties in interfaces.items()}
        if DEVICE_INTERFACE in interfaces:
            self._rebuild_candidates()
            self._emit_devices()
            self._consider_connection()

    def _handle_characteristic_value(self, path: str, payload: bytes) -> None:
        if not payload or not self._running:
            return
        now = time.monotonic()
        self._last_value_path[path] = now
        assert self._gatt is not None
        if path == self._gatt.control:
            self.on_control(payload, now)
        elif path == self._gatt.audio:
            self.on_audio(payload, now)

    def _poll(self) -> bool:
        if not self._running:
            return False
        self._refresh_objects()
        self._consider_connection()
        if self._gatt is not None and not self._caps_requested:
            self._request_capabilities()
        return True

    def _start_discovery(self) -> None:
        if self._bus is None:
            return
        try:
            adapter = self._first_adapter()
            if adapter is None:
                return
            adapter_object = self._bus.get_object(BLUEZ_SERVICE, adapter)
            adapter_interface = self._dbus.Interface(adapter_object, ADAPTER_INTERFACE)
            adapter_interface.StartDiscovery()
            self.logger.write("BLE DISCOVERY phase=start result=ok")
        except self._dbus.DBusException as error:
            error_name = error.get_dbus_name()
            if error_name.endswith("InProgress"):
                return
            self.logger.write("BLE DISCOVERY phase=start result=failed error=%s" % error_name)

    def _stop_discovery(self) -> None:
        if self._bus is None:
            return
        try:
            adapter = self._first_adapter()
            if adapter is None:
                return
            adapter_object = self._bus.get_object(BLUEZ_SERVICE, adapter)
            adapter_interface = self._dbus.Interface(adapter_object, ADAPTER_INTERFACE)
            adapter_interface.StopDiscovery()
        except self._dbus.DBusException:
            pass

    def _first_adapter(self) -> Optional[str]:
        for path, interfaces in self._objects.items():
            if ADAPTER_INTERFACE in interfaces:
                return path
        return None
