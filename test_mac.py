"""Synthetic macOS regression tests; never access the real Keychain or Factory."""
import base64
import contextlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import mac_backend as mac
import switcher as core
import usage


class MacBackendTests(unittest.TestCase):
    def test_keychain_missing_and_access_denied_are_distinct(self):
        with patch.object(mac, "run", return_value=subprocess.CompletedProcess([], 44, b"", b"")):
            self.assertIsNone(mac.keychain_read("service", "account"))
        with patch.object(mac, "run", return_value=subprocess.CompletedProcess([], 36, b"", b"private-output")):
            with self.assertRaises(core.SwitchError) as error:
                mac.keychain_read("service", "account")
            self.assertNotIn("private-output", str(error.exception))

    def test_factory_key_selection(self):
        with patch.object(mac, "keychain_read", return_value=base64.b64encode(b"K" * 32)) as read:
            self.assertEqual(mac.auth_key("auth.v2.loginkeychain"), b"K" * 32)
            read.assert_called_with("Factory CLI", "auth-encryption-key-security-cli")
            mac.auth_key("auth.v2.keyring")
            read.assert_called_with("Factory CLI", "auth-encryption-key")

    def test_new_backup_key_uses_stdin_not_process_arguments(self):
        value = base64.b64encode(b"S" * 32)
        with patch.object(mac, "exclusive", return_value=contextlib.nullcontext()), \
             patch.object(mac, "keychain_read", side_effect=[None, value]), \
             patch.object(mac.os, "urandom", return_value=b"S" * 32), \
             patch.object(mac, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            self.assertEqual(mac.backup_key(Path("unused"), create=True), b"S" * 32)
            args, kwargs = run.call_args
            self.assertEqual(args[0], ["/usr/bin/security", "-i"])
            self.assertIn(value, kwargs["input"])
            self.assertNotIn(b" -U", kwargs["input"])

    def test_missing_decryption_key_is_never_replaced(self):
        with patch.object(mac, "exclusive", return_value=contextlib.nullcontext()), \
             patch.object(mac, "keychain_read", return_value=None), patch.object(mac, "run") as run:
            with self.assertRaises(core.SwitchError):
                mac.backup_key(Path("unused"), create=False)
            run.assert_not_called()

    def test_authenticated_backup_roundtrip_and_tamper_detection(self):
        with patch.object(mac, "backup_key", return_value=b"K" * 32):
            encrypted = mac.protect(b"synthetic-secret", Path("unused"))
            self.assertNotIn(b"synthetic-secret", encrypted)
            self.assertEqual(mac.protect(encrypted, Path("unused"), True), b"synthetic-secret")
            changed = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
            with self.assertRaises(core.SwitchError):
                mac.protect(changed, Path("unused"), True)
            with self.assertRaises(core.SwitchError):
                mac.protect(b"windows-dpapi", Path("unused"), True)

    def test_process_detection_and_refusal_of_standalone_droid(self):
        app = Path("/Applications/Factory.app")
        root = str(app.resolve())
        sample = (f"1 0 {root}/Contents/MacOS/Factory\n"
                  f"2 1 {root}/Contents/Resources/bin/droid\n"
                  "3 0 /Users/example/.local/bin/droid\n"
                  "4 0 /usr/bin/python3\n")
        found = mac.parse_processes(sample, app)
        self.assertEqual([p["ProcessId"] for p in found], [1, 2, 3])
        with patch.object(mac, "run") as run:
            with self.assertRaises(core.SwitchError):
                mac.request_quit(app, found)
            run.assert_not_called()

    def test_quit_path_is_argument_not_applescript_source(self):
        with patch.object(mac, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            mac.request_quit(Path('/Applications/Factory.app'), [{"Bundled": True}])
            argv = run.call_args.args[0]
            self.assertNotIn('/Applications/Factory.app', argv[2])
            self.assertEqual(argv[-1], str(Path('/Applications/Factory.app')))

    @unittest.skipUnless(os.name == 'posix', 'Native POSIX locking requires macOS/Linux')
    def test_native_lock_rejects_a_second_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            with mac.exclusive(Path(folder)):
                with self.assertRaises(core.SwitchError):
                    with mac.exclusive(Path(folder)):
                        self.fail('second owner acquired lock')
            with mac.exclusive(Path(folder)):
                pass


class MacSwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.home = root / 'home'
        self.home.mkdir()
        self.auth_names = ('auth.v2.keyring', 'auth.v2.loginkeychain')
        values = {'MACOS': True, 'mac_backend': mac, 'HOME': self.home,
                  'STORE': root / 'store', 'AUTH_FILES': self.auth_names,
                  'FILES': self.auth_names + ('org-managed-settings.cache.json', 'org-managed-settings.cache.json.backup'),
                  'UNSUPPORTED': ('auth.v2.file', 'auth.v2.key'), 'BACKUP_SUFFIX': '.keychain'}
        for name, value in values.items():
            p = patch.object(core, name, value, create=True)
            p.start(); self.addCleanup(p.stop)
        for target, value in [('backup_key', b'B' * 32), ('auth_key', b'A' * 32)]:
            p = patch.object(mac, target, return_value=value)
            p.start(); self.addCleanup(p.stop)

    def login(self, subject):
        claims = base64.urlsafe_b64encode(json.dumps({'sub': subject, 'email': subject + '@example.test'}).encode()).rstrip(b'=')
        payload = json.dumps({'access_token': 'header.' + claims.decode() + '.signature'}).encode()
        nonce = os.urandom(16)
        sealed = AESGCM(b'A' * 32).encrypt(nonce, payload, None)
        return b':'.join(base64.b64encode(v) for v in (nonce, sealed[-16:], sealed[:-16]))

    def test_mac_switch_roundtrip_uses_login_keychain_and_refreshes_snapshot(self):
        file = self.home / 'auth.v2.loginkeychain'
        a, b = self.login('A'), self.login('B')
        file.write_bytes(a); info_a = core.capture('A')
        file.write_bytes(b); info_b = core.capture('B')
        self.assertEqual(core.backup_path(info_a['account_id']).suffix, '.keychain')
        with patch.object(core, 'check_launch_environment'), patch.object(core, 'stop_factory'), \
             patch.object(core, 'processes', return_value=[]), patch.object(core, 'start_factory'):
            core.activate(info_a['account_id'])
            self.assertEqual(file.read_bytes(), a)
            self.assertFalse((self.home / 'auth.v2.keyring').exists())
            core.activate(info_b['account_id'])
            self.assertEqual(file.read_bytes(), b)
        self.assertEqual(usage.read_credentials(info_a['account_id'])['access_token'].split('.')[0], 'header')

    def test_mac_failed_launch_restores_previous_login(self):
        file = self.home / 'auth.v2.loginkeychain'
        a, b = self.login('A'), self.login('B')
        file.write_bytes(a); target = core.capture('A')
        file.write_bytes(b)
        with patch.object(core, 'check_launch_environment'), patch.object(core, 'stop_factory'), \
             patch.object(core, 'processes', return_value=[]), \
             patch.object(core, 'start_factory', side_effect=core.SwitchError('synthetic')):
            with self.assertRaises(core.SwitchError):
                core.activate(target['account_id'])
        self.assertEqual(file.read_bytes(), b)
        self.assertEqual(core.read_snapshot(core.recovery_path())['files']['auth.v2.loginkeychain'], b)

    def test_ambiguous_auth_sources_refuse_before_mutation(self):
        for name in self.auth_names:
            (self.home / name).write_bytes(b'unchanged')
        with self.assertRaises(core.SwitchError):
            core.current_files()
        self.assertTrue(all((self.home / n).read_bytes() == b'unchanged' for n in self.auth_names))


if __name__ == '__main__':
    unittest.main()
