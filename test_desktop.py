import contextlib
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import gui
import switcher as core


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        self.api = gui.DesktopApi()
        self.identity = "a" * 24
        self.state = {"local_identity": {"account_id": self.identity}, "saved_accounts": [], "processes": []}
        self.status = patch.object(core, "status", return_value=self.state)
        self.status.start()
        self.addCleanup(self.status.stop)
        self.exclusive = patch.object(core, "exclusive", contextlib.nullcontext)
        self.exclusive.start()
        self.addCleanup(self.exclusive.stop)

    def test_mutual_exclusion_rejects_second_action(self):
        self.api._lock.acquire()
        try:
            with patch.object(core, "capture") as capture:
                self.assertFalse(self.api.save_account("test")["ok"])
                capture.assert_not_called()
        finally:
            self.api._lock.release()

    def test_close_is_blocked_during_account_mutation(self):
        self.api._window = Mock()
        self.api._run(lambda: self.assertFalse(self.api.window_action("close")["ok"]))
        self.api._window.destroy.assert_not_called()

    def test_read_only_focus_refresh_does_not_block_close(self):
        self.api._window = Mock()
        self.api._run(lambda: self.assertTrue(self.api.window_action("close")["ok"]), blocks_close=False)
        self.api._window.destroy.assert_called_once()

    def test_unexpected_error_does_not_echo_secret_payload(self):
        with patch.object(core, "capture", side_effect=ValueError("synthetic-private-token")):
            response = self.api.save_account("test")
        self.assertFalse(response["ok"])
        self.assertNotIn("synthetic-private-token", str(response))
        self.assertNotIn("synthetic-private-token", str(self.api._history))

    def test_verification_does_not_follow_a_different_account(self):
        self.api._verification = {"local_account_id": "b" * 24, "server_verified": True, "checked_at": time.time()}
        self.assertIsNone(self.api.get_state()["state"]["verification"])

    def test_old_verification_expires(self):
        self.api._verification = {"local_account_id": self.identity, "server_verified": True, "checked_at": time.time() - 301}
        self.assertIsNone(self.api.get_state()["state"]["verification"])

    def test_remark_change_preserves_encrypted_login_and_save_time(self):
        if core.MACOS:
            key = patch("mac_backend.backup_key", return_value=b"T" * 32)
            key.start()
            self.addCleanup(key.stop)
        with tempfile.TemporaryDirectory() as folder, patch.object(core, "STORE", Path(folder)):
            path = core.backup_path(self.identity)
            files = {"auth.v2.keyring": b"synthetic-encrypted-login"}
            core.atomic_write(path, core.encode_snapshot(files, {"account_id": self.identity}, "Old"))
            before = core.read_snapshot(path)
            self.assertTrue(self.api.rename_account(self.identity, "工作账号")["ok"])
            after = core.read_snapshot(path)
            self.assertEqual(after["label"], "工作账号")
            self.assertEqual(after["files"], files)
            self.assertEqual(after["saved_at"], before["saved_at"])

    def test_empty_remark_and_traversal_rejected(self):
        self.assertFalse(self.api.rename_account(self.identity, " ")["ok"])
        self.assertFalse(self.api.rename_account("../../outside", "Name")["ok"])


if __name__ == "__main__":
    unittest.main()
