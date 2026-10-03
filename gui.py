"""Factory Switch desktop shell. Only explicit, narrow operations cross the UI bridge."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
import threading
import time

import switcher as core
import usage


class DesktopApi:
    def __init__(self):
        self._window = None
        self._lock = threading.Lock()
        self._blocking = False
        self._verification = None
        self._history = []
        self._maximized = False
        self._usage_lock = threading.Lock()

    def _record(self, title, detail="", kind="success"):
        self._history.insert(0, {"title": title, "detail": detail, "kind": kind,
                                 "time": datetime.now(timezone.utc).isoformat()})
        self._history = self._history[:50]

    def _state(self):
        state = core.status()
        current = state.get("local_identity")
        verification = self._verification
        if verification and (not current or verification.get("local_account_id") != current["account_id"]
                             or time.time() - verification["checked_at"] > 300):
            verification = None
        state["verification"] = verification
        state["history"] = list(self._history)
        return state

    def _run(self, action, blocks_close=True):
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "error": "另一项操作正在进行，请稍候。"}
        try:
            self._blocking = blocks_close
            with core.exclusive():
                action()
                return {"ok": True, "state": self._state()}
        except core.SwitchError as exc:
            self._record("操作未完成", str(exc), "error")
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            error = "操作未完成（" + type(exc).__name__ + "），请刷新后重试。"
            self._record("操作未完成", error, "error")
            return {"ok": False, "error": error}
        finally:
            self._blocking = False
            self._lock.release()

    @staticmethod
    def _label(value):
        if not isinstance(value, str) or len(value.strip()) > 64:
            raise core.SwitchError("备注不能超过 64 个字符。")
        return value.strip()

    def get_state(self):
        return self._run(lambda: None, blocks_close=False)

    def get_usage(self, account_id):
        # Reads encrypted snapshots only; network I/O does not hold the action lock.
        if not isinstance(account_id, str):
            return {"ok": False, "error": "账号标识无效。"}
        try:
            core.backup_path(account_id)
        except core.SwitchError:
            return {"ok": False, "error": "账号标识无效。"}
        if not self._usage_lock.acquire(blocking=False):
            return {"ok": False, "error": "正在查询用量，请稍候。"}
        try:
            return {"ok": True, "usage": usage.fetch(account_id)}
        finally:
            self._usage_lock.release()

    def save_account(self, label=""):
        def action():
            account = core.capture(self._label(label))
            self._record("已保存账号", account["label"])
        return self._run(action)

    def rename_account(self, account_id, label):
        def action():
            name = self._label(label)
            if not name:
                raise core.SwitchError("请输入账号备注。")
            path = core.backup_path(account_id)
            snapshot = core.read_snapshot(path)
            snapshot["label"] = name
            snapshot["files"] = {key: base64.b64encode(value).decode() for key, value in snapshot["files"].items()}
            payload = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
            encrypted = core.protect(payload)
            if core.protect(encrypted, True) != payload:
                raise core.SwitchError("备注保存校验失败。")
            core.atomic_write(path, encrypted)
            self._record("已更新账号备注", name)
        return self._run(action)

    def switch_account(self, account_id):
        def action():
            target = core.read_snapshot(core.backup_path(account_id))
            self._verification = None
            core.activate(account_id)
            self._record("已切换账号", target["label"])
        return self._run(action)

    def begin_login(self):
        def action():
            self._verification = None
            core.activate(None)
            self._record("已打开登录入口", "在 Factory 完成登录后，保存新账号。", "info")
        return self._run(action)

    def verify_account(self):
        def action():
            result = core.verify()
            self._verification = {**result, "checked_at": time.time()}
            if result.get("server_verified"):
                self._record("账号验证通过", "Factory 服务端已确认当前账号。")
            else:
                self._record("账号验证未通过", result.get("error") or "HTTP " + str(result.get("http_status", "未知")), "error")
        return self._run(action)

    def window_action(self, action):
        if not self._window:
            return {"ok": False}
        if action == "close":
            if self._blocking:
                return {"ok": False, "error": "正在处理账号，请等待操作完成后关闭。"}
            self._window.destroy()
        elif action == "minimize":
            self._window.minimize()
        elif action == "maximize":
            self._window.restore() if self._maximized else self._window.maximize()
        else:
            return {"ok": False, "error": "不支持的窗口操作。"}
        return {"ok": True}

def main():
    import webview
    if "--smoke-test" in sys.argv:
        # Check the frozen application's resources and native imports without reading accounts.
        import importlib
        importlib.import_module("webview.platforms.cocoa" if core.MACOS else "webview.platforms.winforms")
        for name in ("index.html", "styles.css", "app.js"):
            if not (Path(__file__).parent / "ui" / name).read_text(encoding="utf-8"):
                raise RuntimeError("Missing UI resource")
        for name in ("app-icon.png", "app-icon.ico"):
            if not (Path(__file__).parent / "ui" / name).read_bytes():
                raise RuntimeError("Missing application icon")
        return
    api = DesktopApi()
    ui = Path(__file__).parent / "ui"
    # Inline local assets: no localhost API, remote CDN, or arbitrary filesystem routes.
    html = (ui / "index.html").read_text(encoding="utf-8")
    html = html.replace('src="app-icon.png"',
                        'src="data:image/png;base64,' + base64.b64encode((ui / "app-icon.png").read_bytes()).decode('ascii') + '"')
    html = html.replace('<link rel="stylesheet" href="styles.css">',
                        "<style>" + (ui / "styles.css").read_text(encoding="utf-8") + "</style>")
    html = html.replace('<script src="app.js" defer></script>',
                        "<script>" + (ui / "app.js").read_text(encoding="utf-8") + "</script>")
    if "--preview" in sys.argv:
        html = html.replace('<html', '<html data-preview="true"', 1)
    if core.MACOS:
        html = html.replace('<body', '<body class="macos"', 1)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("local.factory-account-switcher")
    window = webview.create_window("Factory Switch", html=html, js_api=api,
                                   width=640, height=600, min_size=(500, 340),
                                   background_color="#101114", frameless=not core.MACOS,
                                   resizable=True, easy_drag=False, shadow=False, text_select=True)
    api._window = window
    if sys.platform == "win32":
        from window_frame import ResizeFrame
        api._frame = ResizeFrame(window)
        window.events.shown += api._frame.install
    window.events.closing += lambda: not api._blocking
    window.events.maximized += lambda: setattr(api, "_maximized", True)
    window.events.restored += lambda: setattr(api, "_maximized", False)
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.start(gui="cocoa" if core.MACOS else "edgechromium", debug=False, private_mode=True,
                  icon=str(ui / "app-icon.ico") if sys.platform == "win32" else None)


if __name__ == "__main__":
    main()
