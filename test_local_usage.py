import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import gui
import local_usage
import switcher as core


class LocalUsageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.home = Path(self.folder.name)
        self.root = self.home / "sessions" / "project"
        self.root.mkdir(parents=True)
        self.reader = local_usage.LocalUsageReader()

    def session(self, session_id, settings, events=None, folder=None):
        root = folder or self.root
        root.mkdir(parents=True, exist_ok=True)
        path = root / (session_id + ".settings.json")
        path.write_text(json.dumps(settings), encoding="utf-8")
        if events is not None:
            transcript = root / (session_id + ".jsonl")
            header = {"type": "session_start", "id": session_id, "title": "会话标题",
                      "owner": "private-email", "organizationId": "private-org", "cwd": "private-path"}
            transcript.write_text("\n".join(json.dumps(event) for event in [header, *events]) + "\n", encoding="utf-8")
        return path

    def test_direct_counters_not_inclusive_children_or_backup(self):
        self.session("parent", {"tokenUsage": {"inputTokens": 100, "outputTokens": 20},
                    "inclusiveTokenUsage": {"inputTokens": 130},
                    "childInclusiveTokenUsageBySessionId": {"child": {"inputTokens": 30}}})
        child = self.session("child", {"tokenUsage": {"inputTokens": 30}})
        child.with_suffix(".json.bak").write_text('{"tokenUsage":{"inputTokens":9000}}', encoding="utf-8")
        result = self.reader.fetch(self.home)
        self.assertEqual(result["totals"]["inputTokens"], 130)
        self.assertEqual(result["totals"]["outputTokens"], 20)
        self.assertEqual(result["session_count"], 2)

    def test_real_total_matches_factory_five_counters_and_excludes_credits(self):
        parent = {field: (i + 1) * 10 for i, field in enumerate(local_usage.TOTAL_FIELDS)}
        parent["factoryCredits"] = 9999999
        child = {field: i + 1 for i, field in enumerate(local_usage.TOTAL_FIELDS)}
        self.session("parent", {"tokenUsage": parent, "inclusiveTokenUsage": {
            field: parent[field] + child[field] for field in local_usage.TOTAL_FIELDS},
            "childInclusiveTokenUsageBySessionId": {"child": child}})
        self.session("child", {"tokenUsage": child})
        result = self.reader.fetch(self.home)
        self.assertEqual(result["total_tokens"], 165)
        self.assertEqual(result["total_session_count"], 2)
        self.assertEqual({s["session_id"]: s["total_tokens"] for s in result["sessions"]},
                         {"parent": 150, "child": 15})

    def test_real_total_requires_complete_counters_with_partial_coverage(self):
        self.session("missing-output", {"tokenUsage": {
            "inputTokens": 100, "cacheCreationTokens": 0, "cacheReadTokens": 0, "thinkingTokens": 0}})
        self.session("only-output", {"tokenUsage": {"outputTokens": 200}})
        missing = self.reader.fetch(self.home)
        self.assertIsNone(missing["total_tokens"])
        self.assertEqual(missing["total_session_count"], 0)
        self.session("complete-zero", {"tokenUsage": dict.fromkeys(local_usage.TOTAL_FIELDS, 0)})
        result = self.reader.fetch(self.home)
        self.assertEqual(result["total_tokens"], 0)
        self.assertEqual(result["total_session_count"], 1)
        self.assertEqual(result["session_count"], 3)

    def test_real_total_does_not_round_unsafe_aggregates(self):
        counters = dict.fromkeys(local_usage.TOTAL_FIELDS, 0)
        counters["inputTokens"] = local_usage.MAX_SAFE_INTEGER
        self.session("large", {"tokenUsage": counters})
        self.session("small", {"tokenUsage": dict(counters, inputTokens=1)})
        result = self.reader.fetch(self.home)
        self.assertIsNone(result["total_tokens"])
        self.assertEqual(result["total_session_count"], 2)
        self.assertEqual(local_usage.total_tokens(counters), local_usage.MAX_SAFE_INTEGER)
        self.assertIsNone(local_usage.total_tokens(dict(counters, outputTokens=1)))

    def test_missing_invalid_and_zero_are_distinct(self):
        self.session("one", {"tokenUsage": {"inputTokens": 0, "outputTokens": None,
                    "cacheReadTokens": True, "cacheCreationTokens": -1,
                    "thinkingTokens": float("nan"), "factoryCredits": "42"}})
        self.session("two", {"tokenUsage": {"inputTokens": 10, "outputTokens": 1.5,
                    "cacheReadTokens": float("inf"), "thinkingTokens": 10**400}})
        self.session("unknown", {"inclusiveTokenUsage": {"inputTokens": 9000}})
        result = self.reader.fetch(self.home)
        self.assertEqual(result["totals"]["inputTokens"], 10)
        self.assertEqual(result["field_counts"]["inputTokens"], 2)
        self.assertEqual(result["recorded_session_count"], 2)
        self.assertEqual(result["used_session_count"], 1)
        self.assertIsNone(result["totals"]["outputTokens"])
        zero = next(s for s in result["sessions"] if s["session_id"] == "one")
        self.assertEqual(zero["usage"]["inputTokens"], 0)
        self.assertIsNone(zero["usage"]["outputTokens"])

    def test_duplicate_session_id_uses_one_latest_snapshot(self):
        old = self.session("same", {"tokenUsage": {"inputTokens": 900}})
        new = self.session("same", {"tokenUsage": {"inputTokens": 100}}, folder=self.root / "copy")
        os.utime(old, ns=(1_000_000_000, 1_000_000_000))
        os.utime(new, ns=(2_000_000_000, 2_000_000_000))
        result = self.reader.fetch(self.home)
        self.assertEqual(result["totals"]["inputTokens"], 100)
        self.assertEqual(result["session_count"], 1)
        self.assertEqual(result["duplicate_count"], 1)

    def test_invalid_settings_do_not_erase_other_sessions_or_echo_contents(self):
        self.session("good", {"tokenUsage": {"outputTokens": 7}, "secret": "private-secret"})
        (self.root / "broken.settings.json").write_text('{"private-secret":', encoding="utf-8")
        self.session("not-dict", ["private-secret"])
        result = self.reader.fetch(self.home)
        self.assertEqual(result["totals"]["outputTokens"], 7)
        self.assertEqual(result["skipped_count"], 2)
        self.assertNotIn("private-secret", json.dumps(result))

    def test_message_dates_and_mixed_models_not_file_dates_or_current_model(self):
        path = self.session("mixed", {"tokenUsage": {"inputTokens": 20}, "model": "last-model"}, [
            {"type": "message", "timestamp": "2026-10-01T23:00:00+08:00",
             "message": {"modelId": "first-model", "content": "private-message"}},
            {"type": "message", "timestamp": "2026-10-02T02:00:00Z", "message": {"modelId": "last-model"}},
            {"type": "todo_state", "timestamp": "2099-01-01T00:00:00Z"},
            {"type": "message", "timestamp": "2026-10-03T02:00:00", "message": {}},
        ])
        transcript = path.with_name("mixed.jsonl")
        with transcript.open("a", encoding="utf-8") as stream:
            stream.write('{"type":"message"')  # incomplete line from live writer
        result = self.reader.fetch(self.home)
        row = result["sessions"][0]
        self.assertEqual(row["title"], "会话标题")
        self.assertEqual(row["models"], ["first-model", "last-model"])
        self.assertEqual(row["started_at"], "2026-10-01T15:00:00+00:00")
        self.assertEqual(row["last_active_at"], "2026-10-02T02:00:00+00:00")
        for secret in ("private-email", "private-org", "private-path", "private-message"):
            self.assertNotIn(secret, json.dumps(result))

    def test_refresh_updates_counters_and_transcript_without_modifying_any_file(self):
        path = self.session("active", {"tokenUsage": {"outputTokens": 10}}, [
            {"type": "message", "timestamp": "2026-10-03T01:00:00Z", "message": {"modelId": "one"}}])
        before = {p: p.read_bytes() for p in self.root.iterdir()}
        self.reader.fetch(self.home)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.iterdir()})
        path.write_text('{"tokenUsage":{"outputTokens":25}}', encoding="utf-8")
        transcript = path.with_name("active.jsonl")
        with transcript.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "message", "timestamp": "2026-10-03T02:00:00Z",
                                     "message": {"modelId": "two"}}) + "\n")
        result = self.reader.fetch(self.home)
        self.assertEqual(result["totals"]["outputTokens"], 25)
        self.assertEqual(result["sessions"][0]["models"], ["one", "two"])

    def test_missing_transcript_and_session_directory(self):
        empty = self.reader.fetch(self.home / "missing")
        self.assertEqual(empty["session_count"], 0)
        self.assertTrue(all(value is None for value in empty["totals"].values()))
        self.session("no-transcript", {"tokenUsage": {"inputTokens": 0}})
        row = self.reader.fetch(self.home)["sessions"][0]
        self.assertIsNone(row["last_active_at"])
        self.assertEqual(row["title"], "")

    def test_linked_settings_outside_sessions_are_not_read(self):
        outside = self.home / "outside.json"
        outside.write_text('{"tokenUsage":{"inputTokens":999}}', encoding="utf-8")
        try:
            (self.root / "linked.settings.json").symlink_to(outside)
        except OSError:
            self.skipTest("Symlink privilege unavailable")
        result = self.reader.fetch(self.home)
        self.assertEqual(result["session_count"], 0)
        self.assertEqual(result["skipped_count"], 1)

    def test_bridge_reads_do_not_hold_other_locks_and_reject_overlapping_scan(self):
        api = gui.DesktopApi()
        entered, release = threading.Event(), threading.Event()
        def fetch(home):
            entered.set()
            release.wait(3)
            return {"totals": {"inputTokens": 1}}
        with patch.object(api._local_usage_reader, "fetch", side_effect=fetch) as reader:
            api._lock.acquire()
            api._usage_lock.acquire()
            worker = threading.Thread(target=api.get_local_usage)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(api._blocking)
                self.assertFalse(api.get_local_usage()["ok"])
                reader.assert_called_once_with(core.HOME)
            finally:
                release.set()
                worker.join(3)
                api._lock.release()
                api._usage_lock.release()
        self.assertFalse(api._local_usage_lock.locked())

    def test_bridge_failure_is_sanitized_and_lock_released(self):
        api = gui.DesktopApi()
        with patch.object(api._local_usage_reader, "fetch", side_effect=OSError("private-secret")):
            result = api.get_local_usage()
        self.assertFalse(result["ok"])
        self.assertNotIn("private-secret", json.dumps(result))
        self.assertFalse(api._local_usage_lock.locked())


if __name__ == "__main__":
    unittest.main()
