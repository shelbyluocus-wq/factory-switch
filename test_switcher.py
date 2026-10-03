import tempfile
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

import switcher as s


@unittest.skipIf(s.MACOS, "Windows process handling")
class WindowsProcessTests(unittest.TestCase):
    def test_already_stopped_does_not_request_close(self):
        with patch.object(s, "processes", return_value=[]), \
             patch.object(s, "powershell", side_effect=s.SwitchError("unexpected close")) as close:
            s.stop_factory()
            close.assert_not_called()

    def test_desktop_exiting_before_close_is_success(self):
        # Execute the real PowerShell command with a unique absent process name
        # so this test cannot close a user's Factory window.
        run = s.powershell
        absent = "FactorySwitchAbsent" + uuid.uuid4().hex
        before = [{"Name": "factory-desktop.exe", "ExecutablePath": ""}]
        with patch.object(s, "processes", side_effect=[before, []]), \
             patch.object(s, "powershell", side_effect=lambda code: run(code.replace("factory-desktop", absent))):
            s.stop_factory()

    def test_real_powershell_errors_are_not_hidden_by_later_success(self):
        with self.assertRaises(s.SwitchError):
            s.powershell("Write-Error 'synthetic-process-query-failure'; '[]'")

    def test_standalone_cli_still_blocks_closing(self):
        cli = [{"Name": "droid.exe", "ExecutablePath": "C:\\tools\\droid.exe"}]
        with patch.object(s, "processes", return_value=cli), patch.object(s, "powershell") as close:
            with self.assertRaisesRegex(s.SwitchError, "独立 Droid CLI"):
                s.stop_factory()
            close.assert_not_called()

    def test_desktop_that_remains_running_still_blocks_switching(self):
        desktop = [{"Name": "factory-desktop.exe", "ExecutablePath": ""}]
        with patch.object(s, "processes", return_value=desktop), patch.object(s, "powershell"), \
             patch.object(s.time, "monotonic", side_effect=[0, 19]):
            with self.assertRaisesRegex(s.SwitchError, "Factory 尚未完全退出"):
                s.stop_factory()


class SafetyTests(unittest.TestCase):
    def setUp(self):
        if s.MACOS:
            key = patch("mac_backend.backup_key", return_value=b"T" * 32)
            key.start()
            self.addCleanup(key.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.store = self.root / "store"
        self.a = "a" * 24
        self.b = "b" * 24
        for name, value in [("HOME", self.home), ("STORE", self.store)]:
            item = patch.object(s, name, value)
            item.start()
            self.addCleanup(item.stop)

    def test_dpapi_round_trip_and_tampering(self):
        encrypted = s.protect(b"synthetic-secret")
        self.assertNotIn(b"synthetic-secret", encrypted)
        self.assertEqual(s.protect(encrypted, True), b"synthetic-secret")
        damaged = bytearray(encrypted)
        damaged[-1] ^= 1
        with self.assertRaises(s.SwitchError):
            s.protect(bytes(damaged), True)

    def test_refuse_writes_while_running(self):
        path = self.home / "auth.v2.keyring"
        path.write_bytes(b"original")
        with patch.object(s, "processes", return_value=[{"pid": 1}]):
            with self.assertRaises(s.SwitchError):
                s.replace_files({"auth.v2.keyring": b"replacement"})
        self.assertEqual(path.read_bytes(), b"original")

    def test_refuse_unsupported_storage(self):
        (self.home / "auth.v2.file").write_bytes(b"other-format")
        with self.assertRaises(s.SwitchError):
            s.current_files()

    def test_refuse_writer_lock(self):
        (self.home / "auth.v2.write.lock").mkdir()
        with self.assertRaises(s.SwitchError):
            s.current_files()

    def test_path_traversal_rejected(self):
        with self.assertRaises(s.SwitchError):
            s.backup_path("../../elsewhere")

    def test_new_login_does_not_overwrite_recovery_when_logged_out(self):
        journal = s.recovery_path()
        s.atomic_write(journal, b"existing-recovery")
        with patch.object(s, "check_launch_environment"), patch.object(s, "stop_factory") as stop:
            with self.assertRaises(s.SwitchError):
                s.activate(None)
            stop.assert_not_called()
        self.assertEqual(journal.read_bytes(), b"existing-recovery")

    def test_launch_failure_restores_prior_state(self):
        old = {"auth.v2.keyring": b"account-a", "org-managed-settings.cache.json": b"policy-a"}
        new = {"auth.v2.keyring": b"account-b"}
        for name, value in old.items():
            (self.home / name).write_bytes(value)
        s.atomic_write(s.backup_path(self.b), s.encode_snapshot(new, {"account_id": self.b}, "B"))
        identify = lambda data: {"account_id": self.a if data == b"account-a" else self.b, "email": ""}
        with patch.object(s, "check_launch_environment"), patch.object(s, "stop_factory"), \
             patch.object(s, "processes", return_value=[]), patch.object(s, "identity", side_effect=identify), \
             patch.object(s, "start_factory", side_effect=s.SwitchError("synthetic-launch-failure")):
            with self.assertRaises(s.SwitchError):
                s.activate(self.b)
        self.assertEqual(s.current_files(), old)
        self.assertEqual(s.read_snapshot(s.recovery_path())["files"], old)

    def test_same_account_uses_latest_refreshed_state(self):
        s.atomic_write(s.backup_path(self.a), s.encode_snapshot({"auth.v2.keyring": b"old-token"}, {"account_id": self.a}, "A"))
        (self.home / "auth.v2.keyring").write_bytes(b"refreshed-token")
        with patch.object(s, "check_launch_environment"), patch.object(s, "stop_factory"), \
             patch.object(s, "processes", return_value=[]), \
             patch.object(s, "identity", return_value={"account_id": self.a, "email": ""}), patch.object(s, "start_factory"):
            s.activate(self.a)
        self.assertEqual((self.home / "auth.v2.keyring").read_bytes(), b"refreshed-token")


if __name__ == "__main__":
    unittest.main()
