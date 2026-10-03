"""Time ranges from completed Droid request logs, never from session file mtimes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from local_usage import FIELDS, TOTAL_FIELDS, counter, text, timestamp, total_tokens


MARKER = b" INFO: [Agent] Streaming result | Context: "
LOG_FIELDS = {"inputTokens": "inputTokens", "outputTokens": "outputTokens",
              "cacheCreationTokens": "cachedTokensWritten",
              "cacheReadTokens": "cacheReadInputTokens", "thinkingTokens": "reasoningTokens"}


def parse_request(line):
    prefix, marker, body = line.partition(MARKER)
    if not marker or not prefix.startswith(b"[") or not prefix.endswith(b"]"):
        return None
    when = timestamp(prefix[1:-1].decode("ascii"))
    data = json.loads(body)
    if not when or not isinstance(data, dict) or not isinstance(data.get("tags"), dict):
        raise ValueError("Invalid request record")
    session_id = data["tags"].get("sessionId")
    if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
        raise ValueError("Missing request session")
    values = {field: data.get(key) for field, key in LOG_FIELDS.items()}
    if not all(counter(value) for value in values.values()) or total_tokens(values) is None:
        raise ValueError("Incomplete request counters")
    values = {field: int(value) for field, value in values.items()}
    request_id = text(data.get("upstreamResponseId"), 512) or text(data.get("upstreamRequestId"), 512)
    identity = request_id or when + json.dumps(values, sort_keys=True)
    # Identifiers are only used internally for deduplication, never in the UI bridge.
    key = (session_id, hashlib.sha256(identity.encode()).digest())
    return key, {"session_id": session_id, "timestamp": when, "usage": values,
                 "model": text(data["tags"].get("modelId"), 120)}


def merge_request(old, new):
    if old is None:
        return new, False
    before, after = old["usage"], new["usage"]
    if before == after:
        return (old if old["timestamp"] <= new["timestamp"] else new), False
    if all(after[field] >= before[field] for field in TOTAL_FIELDS):
        return new, False  # A repeated final report must not count a request twice.
    if all(before[field] >= after[field] for field in TOTAL_FIELDS):
        return old, False
    return (new if new["timestamp"] >= old["timestamp"] else old), True


class UsageTimelineReader:
    def __init__(self):
        self._files = {}

    def _read(self, path, root):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Unsupported log path")
        path.resolve().relative_to(root)
        stat = path.stat()
        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        old = self._files.get(path)
        if old and old["signature"] == signature:
            return old
        append = (old and old["signature"][0] == stat.st_ino
                  and stat.st_size > old["signature"][1] and stat.st_size >= old["offset"])
        with path.open("rb") as stream:
            if append:
                # A log may be cleared and rewritten past its previous size between polls.
                # Verify two small anchors before treating growth as a pure append.
                head_matches = hashlib.sha256(stream.read(old["head_length"])).digest() == old["head_hash"]
                stream.seek(old["tail_start"])
                tail_matches = hashlib.sha256(stream.read(old["offset"] - old["tail_start"])).digest() == old["tail_hash"]
                append = head_matches and tail_matches
            records = dict(old["records"]) if append else {}
            invalid = old["invalid_count"] if append else 0
            conflicts = old["conflict_count"] if append else 0
            stream.seek(old["offset"] if append else 0)
            while True:
                offset = stream.tell()
                line = stream.readline(2 * 1024 * 1024 + 1)
                if not line:
                    break
                if not line.endswith(b"\n"):
                    if len(line) <= 2 * 1024 * 1024:
                        stream.seek(offset)  # Retry an unfinished line on the next append.
                        break
                    is_request = MARKER in line
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(2 * 1024 * 1024)
                    invalid += int(is_request)
                    continue
                if MARKER not in line:
                    continue
                try:
                    parsed = parse_request(line)
                    if parsed:
                        key, record = parsed
                        records[key], conflict = merge_request(records.get(key), record)
                        conflicts += int(conflict)
                except (ValueError, UnicodeError, RecursionError, OverflowError):
                    invalid += 1
            offset = stream.tell()
            head_length = min(offset, 4096)
            stream.seek(0)
            head_hash = hashlib.sha256(stream.read(head_length)).digest()
            tail_start = max(0, offset - 4096)
            stream.seek(tail_start)
            tail_hash = hashlib.sha256(stream.read(offset - tail_start)).digest()
        result = {"signature": signature, "offset": offset, "records": records,
                  "invalid_count": invalid, "conflict_count": conflicts,
                  "head_length": head_length, "head_hash": head_hash,
                  "tail_start": tail_start, "tail_hash": tail_hash}
        self._files[path] = result
        return result

    def fetch(self, home: Path, sessions, now=None):
        now = now or datetime.now(timezone.utc)
        local_date = now.astimezone().date()
        by_id = {row["session_id"]: row for row in sessions}
        records = {}
        skipped = invalid = conflicts = 0
        root = (Path(home) / "logs").resolve()
        live = set()
        if by_id and root.exists():
            for path in root.glob("droid-log-single.log*"):
                # Only the live log and dated rotations, never backups or arbitrary files.
                suffix = path.name.removeprefix("droid-log-single.log")
                if suffix and (len(suffix) != 11 or not suffix.startswith(".")):
                    continue
                if suffix:
                    try:
                        datetime.strptime(suffix[1:], "%Y-%m-%d")
                    except ValueError:
                        continue
                live.add(path)
                try:
                    file = self._read(path, root)
                    invalid += file["invalid_count"]
                    conflicts += file["conflict_count"]
                    for key, record in file["records"].items():
                        if record["session_id"] not in by_id:
                            continue
                        records[key], conflict = merge_request(records.get(key), record)
                        conflicts += int(conflict)
                except (OSError, ValueError):
                    skipped += 1
        self._files = {path: value for path, value in self._files.items() if path in live}
        history = {session_id: dict.fromkeys(TOTAL_FIELDS, 0) for session_id in by_id}
        dated = []
        for record in records.values():
            when = datetime.fromisoformat(record["timestamp"])
            if when > now:
                continue
            date = when.astimezone().date()
            dated.append((date, record))
            for field in TOTAL_FIELDS:
                history[record["session_id"]][field] += record["usage"][field]
        reconciled = bool(dated) and not (skipped or invalid or conflicts) and all(
            row["usage"].get(field) == history[session_id][field]
            for session_id, row in by_id.items() for field in TOTAL_FIELDS)
        periods = {}
        for key, days in (("today", 1), ("7d", 7), ("30d", 30)):
            start = local_date - timedelta(days=days - 1)
            selected = {}
            for date, record in dated:
                if not start <= date <= local_date:
                    continue
                session_id = record["session_id"]
                if session_id not in selected:
                    source = by_id[session_id]
                    selected[session_id] = {"session_id": session_id, "title": source["title"],
                        "model_setting": "", "models": [], "started_at": record["timestamp"],
                        "last_active_at": record["timestamp"], "usage": dict.fromkeys(TOTAL_FIELDS, 0),
                        "request_count": 0}
                row = selected[session_id]
                row["request_count"] += 1
                row["started_at"] = min(row["started_at"], record["timestamp"])
                row["last_active_at"] = max(row["last_active_at"], record["timestamp"])
                if record["model"] and record["model"] not in row["models"]:
                    row["models"].append(record["model"])
                for field in TOTAL_FIELDS:
                    row["usage"][field] += record["usage"][field]
            rows = sorted(selected.values(), key=lambda row: row["last_active_at"], reverse=True)
            totals = dict.fromkeys(TOTAL_FIELDS, 0 if dated else None)
            for row in rows:
                row["total_tokens"] = total_tokens(row["usage"])
                for field in TOTAL_FIELDS:
                    totals[field] += row["usage"][field]
                    if not counter(row["usage"][field]):
                        row["usage"][field] = None
                row["usage"]["factoryCredits"] = None
                row["models"].sort()
            total = sum(totals.values()) if dated else None
            totals = {field: value if counter(value) else None for field, value in totals.items()}
            totals["factoryCredits"] = None
            periods[key] = {"source": "request_logs", "available": bool(dated), "reconciled": reconciled,
                "range_start": start.isoformat(), "range_end": local_date.isoformat(),
                "checked_at": now.isoformat(), "session_count": len(rows),
                "used_session_count": sum(row["total_tokens"] is not None and row["total_tokens"] > 0 for row in rows),
                "total_session_count": sum(row["total_tokens"] is not None for row in rows),
                "recorded_session_count": len(rows), "total_tokens": total if counter(total) else None,
                "totals": totals, "field_counts": {field: sum(row["usage"].get(field) is not None for row in rows) for field in FIELDS},
                "request_count": sum(row["request_count"] for row in rows), "sessions": rows,
                "skipped_count": 0, "duplicate_count": 0, "skipped_log_count": skipped,
                "invalid_log_count": invalid, "conflicting_log_count": conflicts}
        return periods
