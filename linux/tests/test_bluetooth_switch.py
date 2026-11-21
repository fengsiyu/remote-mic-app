import unittest

from sayall_linux.bluetooth import BluetoothRemoteClient, GattPaths, RemoteCandidate


class FakeLogger:
    def __init__(self):
        self.lines = []

    def write(self, event, fields=None):
        self.lines.append((event, fields or {}))


class BluetoothSwitchTests(unittest.TestCase):
    def make_client(self):
        client = BluetoothRemoteClient.__new__(BluetoothRemoteClient)
        client._devices = {
            "/org/bluez/hci0/dev_old": RemoteCandidate(
                "/org/bluez/hci0/dev_old", "Old Remote", True, True, True
            ),
            "/org/bluez/hci0/dev_new": RemoteCandidate(
                "/org/bluez/hci0/dev_new", "New Remote", True, True, True
            ),
            "/org/bluez/hci0/dev_unpaired": RemoteCandidate(
                "/org/bluez/hci0/dev_unpaired", "New Remote", False, False, False
            ),
        }
        client._selected_path = "/org/bluez/hci0/dev_old"
        client._preferred_path = "/org/bluez/hci0/dev_old"
        client._gatt = GattPaths("transmit", "audio", "control")
        client._notify_requested = True
        client._caps_requested = True
        client._connect_in_flight = None
        client.logger = FakeLogger()
        client.selected = []
        client.states = []
        client.on_selected_device = client.selected.append
        client.on_state = lambda status, detail, connected: client.states.append(
            (status, detail, connected)
        )
        client.calls = []
        client._stop_notify = lambda path: client.calls.append(("stop", path))
        client._disconnect = lambda path: client.calls.append(("disconnect", path))
        client._connect_device = lambda path, pair_if_needed: client.calls.append(
            ("connect", path, pair_if_needed)
        )
        return client

    def test_switching_tears_down_old_gatt_before_connecting_new_device(self):
        client = self.make_client()

        client.select_device("/org/bluez/hci0/dev_new")

        self.assertEqual(
            [
                ("stop", "audio"),
                ("stop", "control"),
                ("disconnect", "/org/bluez/hci0/dev_old"),
                ("connect", "/org/bluez/hci0/dev_new", False),
            ],
            client.calls,
        )
        self.assertIsNone(client._gatt)
        self.assertEqual(["/org/bluez/hci0/dev_new"], client.selected)
        self.assertEqual([("正在切换遥控器", "", False)], client.states)

    def test_unpaired_device_requests_pairing(self):
        client = self.make_client()

        client.select_device("/org/bluez/hci0/dev_unpaired")

        self.assertIn(("connect", "/org/bluez/hci0/dev_unpaired", True), client.calls)

    def test_reselecting_ready_device_is_a_noop(self):
        client = self.make_client()

        client.select_device("/org/bluez/hci0/dev_old")

        self.assertEqual([], client.calls)
        self.assertEqual([], client.selected)

    def test_unknown_device_is_rejected(self):
        client = self.make_client()

        client.select_device("/org/bluez/hci0/dev_missing")

        self.assertEqual([], client.calls)
        self.assertEqual([], client.selected)


if __name__ == "__main__":
    unittest.main()
