import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_usage import LocalUsageReader, MAX_SAFE_INTEGER, TOTAL_FIELDS
from usage_timeline import LOG_FIELDS, MARKER, UsageTimelineReader


class UsageTimelineTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.home = Path(self.folder.name)
        self.logs = self.home / "logs"
        self.logs.mkdir()
        self.path = self.logs / "droid-log-single.log"
        # Use the host's local zone: boundaries must work on Windows and macOS/UTC.
        local_zone = datetime.now().astimezone().tzinfo
        self.now = datetime(2026, 10, 3, 12, tzinfo=local_zone)
        self.reader = UsageTimelineReader()
        self.values = {"inputTokens": 10, "outputTokens": 2, "cacheCreationTokens": 3,
                       "cacheReadTokens": 100, "thinkingTokens": 4}
        self.sessions = [{"session_id": "one", "title": "跨天会话",
                          "usage": dict(self.values, factoryCredits=999)}]

    def line(self, days=0, hour=1, identity="request-1", values=None, session="one", **extra):
        when = (self.now - timedelta(days=days)).replace(hour=hour)
        body = {key: (values or self.values)[field] for field, key in LOG_FIELDS.items()}
        body.update({"tags": {"sessionId": session, "modelId": "model-one"},
                     "upstreamResponseId": identity, "totalInputTokens": 999999,
                     "credential": "private-secret", "organizationId": "private-org"})
        body.update(extra)
        stamp = when.astimezone(timezone.utc).isoformat().encode()
        return b"[" + stamp + b"]" + MARKER + json.dumps(body).encode() + b"\n"

    def fetch(self):
        return self.reader.fetch(self.home, self.sessions, self.now)

    def test_calendar_ranges_use_requests_in_cross_day_session(self):
        lines = [self.line(days=d, identity=str(d)) for d in (0, 1, 6, 7, 29, 30)]
        lines.append(self.line(hour=23, identity="future"))
        self.path.write_bytes(b"".join(lines))
        result = self.fetch()
        for key, count in (("today", 1), ("7d", 3), ("30d", 5)):
            with self.subTest(key=key):
                period = result[key]
                self.assertEqual(period["request_count"], count)
                self.assertEqual(period["total_tokens"], 119 * count)
                self.assertEqual(period["session_count"], 1)
                self.assertEqual(period["totals"]["cacheReadTokens"], 100 * count)
                self.assertIsNone(period["totals"]["factoryCredits"])
        self.assertEqual(result["today"]["range_start"], "2026-10-03")
        self.assertEqual(result["7d"]["range_start"], "2026-09-27")
        self.assertEqual(result["30d"]["range_start"], "2026-09-04")

    def test_midnight_and_utc_date_are_converted_to_local_calendar(self):
        midnight = self.now.replace(hour=0, minute=0)
        before = midnight - timedelta(seconds=1)
        today = self.line()
        stamp = lambda when: when.astimezone(timezone.utc).isoformat().encode()
        today = today.replace(stamp(self.now.replace(hour=1)), stamp(midnight))
        yesterday = self.line(identity="before").replace(stamp(self.now.replace(hour=1)), stamp(before))
        self.path.write_bytes(today + yesterday)
        result = self.fetch()
        self.assertEqual(result["today"]["request_count"], 1)
        self.assertEqual(result["7d"]["request_count"], 2)

    def test_restore_messages_backups_and_unknown_sessions_are_ignored(self):
        restored = self.line().replace(MARKER, b" INFO: [Agent] Token usage restored from settings | Context: ")
        self.path.write_bytes(restored + self.line(session="deleted") + self.line())
        for name in ("droid-log-single.log.bak", "droid-log-single.log.2026-13-03"):
            (self.logs / name).write_bytes(self.line(identity=name))
        result = self.fetch()["today"]
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(result["total_tokens"], 119)
        self.assertTrue(result["reconciled"])
        self.assertNotIn("private-secret", json.dumps(result))
        self.assertNotIn("private-org", json.dumps(result))
        self.assertNotIn("upstreamResponseId", json.dumps(result))

    def test_duplicate_reports_and_rotated_files_count_once(self):
        self.path.write_bytes(self.line() + self.line(identity="request-2"))
        rotation = self.logs / "droid-log-single.log.2026-10-02"
        rotation.write_bytes(self.line(days=1) + self.line(identity="request-2", values=dict(self.values, outputTokens=5)))
        result = self.fetch()
        self.assertEqual(result["today"]["request_count"], 1)
        self.assertEqual(result["today"]["total_tokens"], 122)
        self.assertEqual(result["7d"]["request_count"], 2)
        self.assertEqual(result["7d"]["total_tokens"], 241)
        self.assertEqual(result["7d"]["conflicting_log_count"], 0)

    def test_conflicting_report_is_flagged_and_not_added_twice(self):
        self.path.write_bytes(self.line(hour=1) + self.line(hour=2, values=dict(self.values, inputTokens=9, outputTokens=3)))
        result = self.fetch()["today"]
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(result["total_tokens"], 119)
        self.assertEqual(result["conflicting_log_count"], 1)
        self.assertFalse(result["reconciled"])

    def test_missing_invalid_and_unsafe_counters_are_not_zero(self):
        lines = [self.line(identity=str(i), outputTokens=value) for i, value in enumerate((None, -1, True, "2", 1.5, MAX_SAFE_INTEGER))]
        lines.append(self.line().replace(b'"reasoningTokens": 4', b'"absent": 4'))
        lines.append(b"[invalid]" + MARKER + b"{}\n")
        self.path.write_bytes(b"".join(lines))
        result = self.fetch()["today"]
        self.assertFalse(result["available"])
        self.assertIsNone(result["total_tokens"])
        self.assertTrue(all(value is None for value in result["totals"].values()))
        self.assertEqual(result["invalid_log_count"], 8)

    def test_no_logs_unavailable_but_empty_range_of_known_history_is_zero(self):
        empty = self.fetch()["today"]
        self.assertFalse(empty["available"])
        self.assertIsNone(empty["total_tokens"])
        self.path.write_bytes(self.line(days=31))
        result = self.fetch()["today"]
        self.assertTrue(result["available"])
        self.assertEqual(result["request_count"], 0)
        self.assertEqual(result["total_tokens"], 0)
        self.assertIsNone(result["totals"]["factoryCredits"])

    def test_unchanged_cache_append_and_unfinished_line(self):
        self.path.write_bytes(self.line())
        self.fetch()
        with patch.object(Path, "open", side_effect=AssertionError("Unchanged log reopened")):
            self.assertEqual(self.fetch()["today"]["request_count"], 1)
        second = self.line(identity="second")
        with self.path.open("ab") as stream:
            stream.write(second[:-10])
        self.assertEqual(self.fetch()["today"]["request_count"], 1)
        with self.path.open("ab") as stream:
            stream.write(second[-10:])
        result = self.fetch()["today"]
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(result["invalid_log_count"], 0)

    def test_truncation_and_removed_rotation_drop_old_records(self):
        self.path.write_bytes(self.line() + self.line(identity="second"))
        rotation = self.logs / "droid-log-single.log.2026-10-02"
        rotation.write_bytes(self.line(identity="rotated"))
        self.assertEqual(self.fetch()["today"]["request_count"], 3)
        self.path.write_bytes(self.line(identity="new"))
        rotation.unlink()
        self.assertEqual(self.fetch()["today"]["request_count"], 1)
        self.assertEqual(len(self.reader._files), 1)

    def test_rewritten_larger_file_does_not_keep_stale_requests(self):
        self.path.write_bytes(self.line(identity="old"))
        self.assertEqual(self.fetch()["today"]["request_count"], 1)
        self.path.write_bytes(self.line(identity="new") + self.line(identity="new-2"))
        result = self.fetch()["today"]
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(result["total_tokens"], 238)

    def test_read_failure_is_labeled_and_other_files_remain_available(self):
        self.path.write_bytes(self.line())
        original = self.reader._read
        rotation = self.logs / "droid-log-single.log.2026-10-02"
        rotation.write_bytes(self.line(identity="rotated"))
        def read(path, root):
            if path == rotation:
                raise PermissionError("Locked log")
            return original(path, root)
        with patch.object(self.reader, "_read", side_effect=read):
            result = self.fetch()["today"]
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(result["skipped_log_count"], 1)
        self.assertFalse(result["reconciled"])

    def test_safe_fields_but_unsafe_sum_is_not_rounded(self):
        values = dict.fromkeys(TOTAL_FIELDS, 0)
        values["inputTokens"] = MAX_SAFE_INTEGER
        self.path.write_bytes(self.line(values=values) + self.line(identity="second"))
        result = self.fetch()["today"]
        self.assertIsNone(result["total_tokens"])
        self.assertIsNone(result["sessions"][0]["total_tokens"])
        self.assertIsNone(result["totals"]["inputTokens"])

    def test_local_reader_includes_periods_without_changing_all_counters(self):
        root = self.home / "sessions" / "project"
        root.mkdir(parents=True)
        (root / "one.settings.json").write_text(json.dumps({"tokenUsage": dict(self.values, factoryCredits=999)}))
        self.path.write_bytes(self.line())
        result = LocalUsageReader().fetch(self.home)
        self.assertEqual(result["total_tokens"], 119)
        self.assertEqual(result["totals"]["factoryCredits"], 999)
        self.assertEqual(result["periods"]["7d"]["source"], "request_logs")
        self.assertIsNone(result["periods"]["7d"]["totals"]["factoryCredits"])


if __name__ == "__main__":
    unittest.main()
