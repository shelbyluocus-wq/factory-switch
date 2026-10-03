"""Share local Factory transcripts without changing their IDs or message bytes."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from errors import SwitchError


@dataclass
class Change:
    path: Path
    before: bytes
    after: bytes | None


@dataclass
class SharingPlan:
    home: Path
    changes: list[Change]
    session_count: int
    applied: list[Change] = field(default_factory=list)
    backup: Path | None = None

    def apply(self, store, suffix, protect, atomic_write):
        if not self.changes:
            return
        # Validate the whole plan before saving a backup or replacing any file.
        for change in self.changes:
            if change.path.read_bytes() != change.before:
                raise SwitchError("会话文件在处理期间发生变化，已停止共享操作。")
        payload = json.dumps({
            "schema": 1, "kind": "local-session-sharing", "home": str(self.home),
            "files": {str(c.path.relative_to(self.home)): base64.b64encode(c.before).decode()
                      for c in self.changes},
        }, ensure_ascii=False).encode("utf-8")
        encrypted = protect(payload)
        if protect(encrypted, True) != payload:
            raise SwitchError("会话备份加密校验失败；会话未修改。")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.backup = store / "session-backups" / (stamp + "-" + uuid.uuid4().hex + suffix)
        atomic_write(self.backup, encrypted)
        if protect(self.backup.read_bytes(), True) != payload:
            raise SwitchError("会话备份字节校验失败；会话未修改。")
        try:
            for change in self.changes:
                if change.path.read_bytes() != change.before:
                    raise SwitchError("会话文件在处理期间发生变化，已停止共享操作。")
                if change.after is None:
                    change.path.unlink()  # Derived index; Factory rebuilds it on startup.
                else:
                    atomic_write(change.path, change.after)
                self.applied.append(change)
        except Exception:
            self.rollback(atomic_write)
            raise

    def rollback(self, atomic_write):
        # Never overwrite messages appended by a writer after application.
        for change in self.applied:
            actual = change.path.read_bytes() if change.path.exists() else None
            if actual != change.after:
                raise SwitchError("会话已有后续变化，未覆盖新内容；原始加密备份已保留。")
        for change in reversed(self.applied):
            atomic_write(change.path, change.before)
        self.applied.clear()


def plan_sharing(home: Path) -> SharingPlan:
    home = home.resolve()
    sessions = home / "sessions"
    changes = []
    if sessions.exists():
        # Refuse linked directories outside the user's Factory home.
        try:
            sessions.resolve().relative_to(home)
            for path in sorted(sessions.rglob("*.jsonl")):
                path.resolve().relative_to(sessions)
                if path.is_symlink():
                    raise ValueError()
                data = path.read_bytes()
                header, newline, body = data.partition(b"\n")
                if len(header) > 1024 * 1024:
                    raise ValueError()
                summary = json.loads(header)
                if (not isinstance(summary, dict) or summary.get("type") != "session_start"
                        or summary.get("id") != path.stem):
                    raise ValueError()
                if "organizationId" not in summary:
                    continue
                if not isinstance(summary["organizationId"], str) or not summary["organizationId"]:
                    raise ValueError()
                # Factory's legacy local-session path accepts no organizationId.
                # On resume, Droid may attach the active org again; repeat on each switch.
                del summary["organizationId"]
                line = json.dumps(summary, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ending = b"\r\n" if newline and header.endswith(b"\r") else newline
                changes.append(Change(path, data, line + ending + body))
        except (ValueError, OSError):
            raise SwitchError("本地会话格式或路径不支持共享；会话未修改。") from None
    count = len(changes)
    index = home / "sessions-index.json"
    if changes and index.exists():
        if index.is_symlink() or not index.is_file():
            raise SwitchError("本地会话索引路径不支持共享；会话未修改。")
        changes.append(Change(index, index.read_bytes(), None))
    return SharingPlan(home, changes, count)
