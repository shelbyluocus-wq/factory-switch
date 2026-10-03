"""Read existing Droid counters without credentials, network calls or transcript edits."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path


FIELDS = ("inputTokens", "outputTokens", "cacheCreationTokens", "cacheReadTokens",
          "thinkingTokens", "factoryCredits")
# Factory Desktop 0.190.0 userStats.ax sums these five counters, including
# thinkingTokens as a separate term. Credits use a different weighted formula.
TOTAL_FIELDS = FIELDS[:-1]
MAX_SAFE_INTEGER = 2**53 - 1


def counter(value):
    # The desktop bridge uses JavaScript numbers. Never silently round a counter.
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0 <= value <= MAX_SAFE_INTEGER and math.isfinite(value)
            and (not isinstance(value, float) or value.is_integer()))


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def total_tokens(values):
    # Missing fields cannot be assumed to be zero or supplied by other sessions.
    if not all(counter(values.get(field)) for field in TOTAL_FIELDS):
        return None
    total = sum(int(values[field]) for field in TOTAL_FIELDS)
    return total if total <= MAX_SAFE_INTEGER else None


def text(value, limit=240):
    return value.strip()[:limit] if isinstance(value, str) else ""


class LocalUsageReader:
    def __init__(self):
        self._metadata_cache = {}
        from usage_timeline import UsageTimelineReader
        self._timeline_reader = UsageTimelineReader()

    def _metadata(self, path, session_id, root):
        empty = {"title": "", "started_at": None, "last_active_at": None, "models": []}
        try:
            if path.is_symlink() or not path.is_file():
                return empty
            path.resolve().relative_to(root)
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
            old = self._metadata_cache.get(path)
            if old and old[0] == signature:
                return old[1]
            result = dict(empty)
            models = set()
            with path.open(encoding="utf-8-sig") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except (ValueError, RecursionError):
                        continue  # A live writer may not have finished its last line.
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "session_start" and event.get("id") == session_id:
                        result["title"] = text(event.get("title"))
                    # Message times describe activity; file mtime does not.
                    if event.get("type") != "message":
                        continue
                    when = timestamp(event.get("timestamp"))
                    if when:
                        result["started_at"] = min(result["started_at"] or when, when)
                        result["last_active_at"] = max(result["last_active_at"] or when, when)
                    message = event.get("message")
                    if isinstance(message, dict):
                        model = text(message.get("modelId"), 120)
                        if model:
                            models.add(model)
            result["models"] = sorted(models)
            self._metadata_cache[path] = (signature, result)
            return result
        except (OSError, ValueError, UnicodeError):
            return empty

    def fetch(self, home: Path):
        root = (Path(home) / "sessions").resolve()
        result = {"checked_at": datetime.now(timezone.utc).isoformat(), "session_count": 0,
                  "recorded_session_count": 0, "used_session_count": 0, "skipped_count": 0,
                  "duplicate_count": 0, "totals": dict.fromkeys(FIELDS),
                  "field_counts": dict.fromkeys(FIELDS, 0), "sessions": [],
                  "total_tokens": None, "total_session_count": 0}
        if not root.exists():
            self._metadata_cache.clear()
            result["periods"] = self._timeline_reader.fetch(home, [])
            return result
        candidates = {}
        for path in root.rglob("*.settings.json"):
            try:
                if path.is_symlink() or not path.is_file():
                    result["skipped_count"] += 1
                    continue
                path.resolve().relative_to(root)
                session_id = path.name.removesuffix(".settings.json")
                stat = path.stat()
                if session_id in candidates:
                    result["duplicate_count"] += 1
                    if stat.st_mtime_ns <= candidates[session_id][0]:
                        continue
                candidates[session_id] = (stat.st_mtime_ns, path)
            except (OSError, ValueError):
                result["skipped_count"] += 1
        live_metadata = set()
        for session_id, (_, path) in candidates.items():
            try:
                with path.open("rb") as stream:
                    raw = stream.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError("Settings too large")
                settings = json.loads(raw)
                if not isinstance(settings, dict):
                    raise ValueError("Unsupported settings")
            except (OSError, ValueError, RecursionError):
                result["skipped_count"] += 1
                continue
            # inclusiveTokenUsage and child snapshots already include other sessions.
            # Only sum each unique session's own counters, never its inclusive copy.
            usage = settings.get("tokenUsage")
            values = {field: int(usage[field]) if isinstance(usage, dict)
                      and counter(usage.get(field)) else None for field in FIELDS}
            transcript = path.with_name(session_id + ".jsonl")
            metadata = self._metadata(transcript, session_id, root)
            live_metadata.add(transcript)
            row = {"session_id": session_id, **metadata, "usage": values,
                   "model_setting": text(settings.get("model"), 120),
                   "total_tokens": total_tokens(values)}
            result["sessions"].append(row)
            if row["total_tokens"] is not None:
                result["total_tokens"] = (result["total_tokens"] or 0) + row["total_tokens"]
                result["total_session_count"] += 1
            if any(value is not None for value in values.values()):
                result["recorded_session_count"] += 1
            if any(value is not None and value > 0 for value in values.values()):
                result["used_session_count"] += 1
            for field, value in values.items():
                if value is not None:
                    result["totals"][field] = (result["totals"][field] or 0) + value
                    result["field_counts"][field] += 1
        # Unknown time sorts last. Refresh keeps ordering independent of settings writes.
        result["sessions"].sort(key=lambda row: (row["last_active_at"] or "", row["session_id"]), reverse=True)
        result["session_count"] = len(result["sessions"])
        # A huge aggregate must not be sent as a misleading rounded JavaScript number.
        for field in FIELDS:
            if result["totals"][field] is not None and result["totals"][field] > MAX_SAFE_INTEGER:
                result["totals"][field] = None
        if result["total_tokens"] is not None and result["total_tokens"] > MAX_SAFE_INTEGER:
            result["total_tokens"] = None
        self._metadata_cache = {path: item for path, item in self._metadata_cache.items()
                                if path in live_metadata}
        result["periods"] = self._timeline_reader.fetch(home, result["sessions"],
                                    datetime.fromisoformat(result["checked_at"]))
        return result
