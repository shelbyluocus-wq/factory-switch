"""Synthetic transcript tests; never open the user's real Factory files."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import session_history
import switcher as core


class SessionSharingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.store = self.root / "store"
        for name, value in [("HOME", self.home), ("STORE", self.store)]:
            item = patch.object(core, name, value)
            item.start()
            self.addCleanup(item.stop)
        item = patch.object(core, "processes", return_value=[])
        item.start()
        self.addCleanup(item.stop)
        if core.MACOS:
            item = patch("mac_backend.backup_key", return_value=b"T" * 32)
            item.start()
            self.addCleanup(item.stop)

    def transcript(self, name, org, *, nested=False, crlf=False):
        folder = self.home / "sessions"
        if nested:
            folder /= "-example-project"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (name + ".jsonl")
        summary = {"type": "session_start", "id": name, "owner": "local-owner",
                   "title": "会话", "hostId": "original-host", "organizationId": org}
        ending = b"\r\n" if crlf else b"\n"
        body = b'{"type":"message","text":"synthetic-private-message"}\n'
        path.write_bytes(json.dumps(summary, ensure_ascii=False).encode() + ending + body)
        return path

    def test_all_orgs_shared_with_exact_message_bytes_and_encrypted_backup(self):
        a = self.transcript("a", "org-a", crlf=True)
        b = self.transcript("b", "org-b", nested=True)
        index = self.home / "sessions-index.json"
        index.write_bytes(b'{"version":7,"entries":[{"organizationId":"org-a"}]}')
        auth = self.home / "auth.v2.keyring"
        auth.write_bytes(b"unchanged-login")
        originals = {p: p.read_bytes() for p in (a, b, index)}
        plan = core.share_local_history()
        self.assertEqual(plan.session_count, 2)
        self.assertFalse(index.exists())
        self.assertEqual(auth.read_bytes(), b"unchanged-login")
        for path in (a, b):
            original, _, body = originals[path].partition(b"\n")
            header, _, current_body = path.read_bytes().partition(b"\n")
            expected = json.loads(original)
            expected.pop("organizationId")
            self.assertEqual(json.loads(header), expected)
            self.assertEqual(current_body, body)
        self.assertIn(b"\r\n", a.read_bytes())
        encrypted = plan.backup.read_bytes()
        self.assertNotIn(b"synthetic-private-message", encrypted)
        snapshot = json.loads(core.protect(encrypted, True))
        for path, data in originals.items():
            self.assertEqual(base64.b64decode(snapshot["files"][str(path.relative_to(self.home))]), data)
        plan.rollback(core.atomic_write)
        self.assertTrue(all(path.read_bytes() == data for path, data in originals.items()))

    def test_repeated_sharing_is_noop_until_droid_reattaches_org(self):
        path = self.transcript("a", "org-a")
        core.share_local_history()
        index = self.home / "sessions-index.json"
        index.write_bytes(b"rebuilt-index")
        self.assertEqual(core.share_local_history().session_count, 0)
        self.assertEqual(index.read_bytes(), b"rebuilt-index")
        header, _, body = path.read_bytes().partition(b"\n")
        summary = json.loads(header)
        summary["organizationId"] = "org-b"
        path.write_bytes(json.dumps(summary).encode() + b"\n" + body)
        self.assertEqual(core.share_local_history().session_count, 1)
        self.assertFalse(index.exists())

    def test_running_factory_refuses_before_reading_sessions(self):
        path = self.transcript("a", "org-a")
        before = path.read_bytes()
        with patch.object(core, "processes", return_value=[{"pid": 1}]):
            with self.assertRaises(core.SwitchError):
                core.share_local_history()
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.store.exists())

    def test_malformed_transcript_refuses_whole_batch(self):
        path = self.transcript("a", "org-a")
        before = path.read_bytes()
        (path.parent / "z.jsonl").write_bytes(b"malformed-private-content\n")
        with self.assertRaises(core.SwitchError) as error:
            core.share_local_history()
        self.assertNotIn("malformed-private-content", str(error.exception))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.store.exists())

    def test_escaping_link_refused(self):
        path = self.transcript("a", "org-a")
        outside = self.root / "outside.jsonl"
        outside.write_bytes(path.read_bytes())
        try:
            os.symlink(outside, path.parent / "linked.jsonl")
        except OSError:
            self.skipTest("Creating symlinks is unavailable")
        with self.assertRaises(core.SwitchError):
            core.share_local_history()
        self.assertEqual(path.read_bytes(), outside.read_bytes())

    def test_write_failure_rolls_back_earlier_transcripts(self):
        a = self.transcript("a", "org-a")
        b = self.transcript("b", "org-b")
        before = a.read_bytes()
        write = core.atomic_write
        def failing_write(path, data):
            if path == b:
                raise OSError("synthetic-write-failure")
            write(path, data)
        with patch.object(core, "atomic_write", side_effect=failing_write):
            with self.assertRaises(OSError):
                core.share_local_history()
        self.assertEqual(a.read_bytes(), before)
        self.assertEqual(len(list((self.store / "session-backups").glob("*"))), 1)

    def test_rollback_preserves_new_messages(self):
        path = self.transcript("a", "org-a")
        plan = core.share_local_history()
        with path.open("ab") as stream:
            stream.write(b'{"type":"message","text":"new-message"}\n')
        after = path.read_bytes()
        with self.assertRaises(core.SwitchError):
            plan.rollback(core.atomic_write)
        self.assertEqual(path.read_bytes(), after)

    def test_switch_launch_failure_restores_transcripts_index_and_login(self):
        path = self.transcript("a", "org-a")
        index = self.home / "sessions-index.json"
        index.write_bytes(b"original-index")
        before = path.read_bytes()
        account = "a" * 24
        (self.home / "auth.v2.keyring").write_bytes(b"refreshed-login")
        core.atomic_write(core.backup_path(account), core.encode_snapshot(
            {"auth.v2.keyring": b"old-login"}, {"account_id": account}, "A"))
        with patch.object(core, "check_launch_environment"), patch.object(core, "stop_factory"), \
             patch.object(core, "identity", return_value={"account_id": account, "email": ""}), \
             patch.object(core, "start_factory", side_effect=core.SwitchError("synthetic-launch-failure")):
            with self.assertRaises(core.SwitchError):
                core.activate(account)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(index.read_bytes(), b"original-index")
        self.assertEqual((self.home / "auth.v2.keyring").read_bytes(), b"refreshed-login")


if __name__ == "__main__":
    unittest.main()
