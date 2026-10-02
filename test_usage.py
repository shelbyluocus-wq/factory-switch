import io
import json
import threading
import unittest
import urllib.error
from unittest.mock import patch

import gui
import usage
import switcher as core


class UsageTests(unittest.TestCase):
    def test_missing_is_not_zero_and_values_are_validated(self):
        pools = usage.normalize_limits({"limits": {"standard": {
            "fiveHour": {"usedPercent": 0, "windowEnd": None},
            "weekly": {"usedPercent": 123, "windowEnd": "2026-10-06T10:00:00Z"},
            "monthly": {"usedPercent": None}}, "core": {
            "fiveHour": {"usedPercent": True}, "weekly": {"usedPercent": float("nan")}}}})
        self.assertEqual(len(pools), 1)
        self.assertEqual([w["used_percent"] for w in pools[0]["windows"]], [0, 123])
        self.assertEqual(usage.normalize_limits({}), [])

    def test_subscription_does_not_mix_personal_and_org_usage(self):
        pools = usage.normalize_subscription({"usage": {"endDate": None, "standard": {
            "userTokens": 10, "userLimit": 50, "orgTotalTokensUsed": 100,
            "totalAllowance": 200}, "premium": {"orgTotalTokensUsed": 0, "totalAllowance": 0}}})
        self.assertEqual([(p["scope"], p["used_percent"]) for p in pools], [("组织额度", 50), ("个人额度", 20)])
        self.assertIsNone(pools[0]["resets_at"])

    def test_legacy_fallback_and_whitelisted_output(self):
        with patch.object(usage, "read_credentials", return_value={}), patch.object(usage, "request", side_effect=[
            {}, {"usage": {"standard": {"orgTotalTokensUsed": 0, "totalAllowance": 20000000}}, "secret": "synthetic-secret"}
        ]) as request:
            result = usage.fetch("a" * 24)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["pools"][0]["used_percent"], 0)
        self.assertEqual(request.call_count, 2)
        self.assertNotIn("synthetic-secret", json.dumps(result))

    def test_http_and_network_failures_do_not_echo_bodies(self):
        for error in [urllib.error.HTTPError("https://api.factory.ai", 401, "synthetic-secret", {}, io.BytesIO(b"private")),
                      urllib.error.URLError("synthetic-secret"), ValueError("synthetic-secret")]:
            with self.subTest(error=type(error).__name__), patch.object(usage, "read_credentials", side_effect=error):
                result = usage.fetch("a" * 24)
                self.assertEqual(result["status"], "error")
                self.assertNotIn("synthetic-secret", json.dumps(result))

    def test_current_credentials_preferred_and_saved_identity_checked(self):
        with patch.object(core, "current_files", return_value={"auth.v2.keyring": b"fresh"}), \
             patch.object(core, "identity", return_value={"account_id": "a" * 24}), \
             patch.object(core, "decrypt_credentials", return_value={}) as decrypt, \
             patch.object(core, "read_snapshot") as read:
            usage.read_credentials("a" * 24)
            decrypt.assert_called_once_with(b"fresh")
            read.assert_not_called()
        with patch.object(core, "current_files", return_value={}), \
             patch.object(core, "read_snapshot", return_value={"files": {"auth.v2.keyring": b"wrong"}}), \
             patch.object(core, "identity", return_value={"account_id": "b" * 24}), \
             patch.object(core, "decrypt_credentials") as decrypt:
            with self.assertRaises(core.SwitchError):
                usage.read_credentials("a" * 24)
            decrypt.assert_not_called()

    def test_read_only_official_host_and_redirect_refused(self):
        response = io.BytesIO(b'{"limits": {}}')
        with patch.object(usage.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = response
            usage.request({"access_token": "synthetic-token", "whoami": {"region": "eu"},
                           "active_organization_id": "org-example"}, "/api/billing/limits")
            req = build.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url, "https://api.eu.factory.ai/api/billing/limits")
            self.assertEqual(req.method, "GET")
            self.assertEqual(req.get_header("X-factory-org-id"), "org-example")
            self.assertIsNone(build.call_args.args[0].redirect_request(req, None, 302, "", {}, "https://elsewhere.test"))

    def test_usage_does_not_hold_action_lock_or_allow_duplicate_queries(self):
        api = gui.DesktopApi()
        entered, release = threading.Event(), threading.Event()
        def fetch(account_id):
            entered.set()
            release.wait(3)
            return {"status": "ok"}
        with patch.object(usage, "fetch", side_effect=fetch):
            worker = threading.Thread(target=api.get_usage, args=("a" * 24,))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(api._lock.locked())
                self.assertFalse(api.get_usage("b" * 24)["ok"])
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(api.get_usage("../bad")["ok"])
        self.assertFalse(api.get_usage(None)["ok"])


if __name__ == "__main__":
    unittest.main()
