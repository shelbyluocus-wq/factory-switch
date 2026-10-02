"""macOS Keychain, application lifecycle and advisory locks.

Factory credential names/format verified against Desktop 0.189.0's bundled code.
Native macOS execution still requires validation on a Mac.
"""
from __future__ import annotations

import base64
import contextlib
import os
from pathlib import Path
import plistlib
import subprocess

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from errors import SwitchError

PREFIX = b"FSW-MAC-1\0"
SERVICE = "Factory Account Switcher"
ACCOUNT = "backup-encryption-key"


def run(args, *, input=None):
    try:
        return subprocess.run(args, input=input, capture_output=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        raise SwitchError("macOS 系统操作失败或超时；未输出敏感内容。") from None


def keychain_read(service, account):
    result = run(["/usr/bin/security", "find-generic-password", "-s", service,
                  "-a", account, "-w"])
    if result.returncode == 44:  # errSecItemNotFound; permission denial is NOT absence.
        return None
    if result.returncode:
        raise SwitchError("无法读取 macOS 钥匙串，请解锁钥匙串并允许访问后重试。")
    return result.stdout.rstrip(b"\r\n")


def decode_key(value):
    try:
        key = base64.b64decode(value, validate=True)
        if len(key) != 32:
            raise ValueError()
        return key
    except (ValueError, TypeError):
        raise SwitchError("钥匙串密钥格式不支持。") from None


def auth_key(filename):
    account = {"auth.v2.keyring": "auth-encryption-key",
               "auth.v2.loginkeychain": "auth-encryption-key-security-cli"}.get(filename)
    if not account:
        raise SwitchError("不支持此 Factory 认证存储格式。")
    value = keychain_read("Factory CLI", account)
    if value is None:
        raise SwitchError("找不到 Factory 登录密钥，请先在这台 Mac 上完成登录。")
    return decode_key(value)


@contextlib.contextmanager
def exclusive(store, name="switch.lock"):
    import fcntl
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Keep the lock file: unlinking it could allow two processes to lock different inodes.
    fd = os.open(store / name, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SwitchError("另一个切换或备份操作正在进行，请稍后重试。") from None
        yield
    finally:
        os.close(fd)


def backup_key(store, *, create):
    with exclusive(store, "keychain.lock"):
        value = keychain_read(SERVICE, ACCOUNT)
        if value is None:
            if not create:
                raise SwitchError("找不到备份密钥，无法解密此 Mac 的账号备份。")
            value = base64.b64encode(os.urandom(32))
            # Secret travels on stdin, never in process arguments; never overwrite a key.
            command = (f'add-generic-password -s "{SERVICE}" -a "{ACCOUNT}" '
                       f'-w "{value.decode("ascii")}"\n').encode("ascii")
            result = run(["/usr/bin/security", "-i"], input=command)
            if result.returncode or keychain_read(SERVICE, ACCOUNT) != value:
                raise SwitchError("无法创建或校验备份密钥；未保存账号备份。")
        return decode_key(value)


def protect(data, store, decrypt=False):
    if decrypt and (not data.startswith(PREFIX) or len(data) < len(PREFIX) + 28):
        raise SwitchError("这不是有效的 macOS 备份；Windows 备份不能直接导入。")
    key = backup_key(store, create=not decrypt)
    try:
        if decrypt:
            start = len(PREFIX)
            return AESGCM(key).decrypt(data[start:start + 12], data[start + 12:], PREFIX)
        nonce = os.urandom(12)
        return PREFIX + nonce + AESGCM(key).encrypt(nonce, data, PREFIX)
    except Exception:
        raise SwitchError("备份解密失败：密钥不匹配或文件已损坏。") from None


def app_path():
    override = os.environ.get("FACTORY_SWITCHER_APP")
    if override:
        return Path(override).expanduser()
    candidates = [Path("/Applications/Factory.app"), Path.home() / "Applications/Factory.app"]
    return next((p for p in candidates if p.is_dir()), candidates[0])


def validate_app(app):
    try:
        with (app / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        executable = info["CFBundleExecutable"]
        if not isinstance(executable, str) or Path(executable).name != executable:
            raise ValueError()
        if not (app / "Contents/MacOS" / executable).is_file():
            raise ValueError()
        if info.get("CFBundleName") != "Factory" and info.get("CFBundleDisplayName") != "Factory":
            raise ValueError()
    except (OSError, ValueError, KeyError, plistlib.InvalidFileException):
        raise SwitchError("找不到有效的 Factory.app，请安装到 /Applications 或设置 FACTORY_SWITCHER_APP。") from None


def parse_processes(text, app):
    rows = []
    root = str(app.resolve()) + "/"
    for line in text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        pid, parent, executable = parts
        if not pid.isdigit() or not parent.isdigit():
            continue
        name = Path(executable).name
        bundled = executable.startswith(root)
        # Include another Factory installation too: it can write the same auth files.
        if bundled or name in ("droid", "Factory", "factory-desktop") or "/Factory.app/Contents/" in executable:
            rows.append({"ProcessId": int(pid), "ParentProcessId": int(parent),
                         "Name": name, "ExecutablePath": executable, "Bundled": bundled})
    return rows


def processes(app):
    result = run(["/bin/ps", "-axww", "-o", "pid=,ppid=,comm="])
    if result.returncode:
        raise SwitchError("无法检查 Factory / Droid 进程；未继续切换。")
    return parse_processes(result.stdout.decode("utf-8", errors="replace"), app)


def request_quit(app, before):
    if any(not p["Bundled"] for p in before):
        raise SwitchError("检测到独立 Droid 或其他位置的 Factory，请先正常退出后重试。")
    if not before:
        return
    # Pass the bundle path as argv, not interpolated AppleScript source.
    script = 'on run argv\n tell application (item 1 of argv) to quit\nend run'
    result = run(["/usr/bin/osascript", "-e", script, str(app)])
    if result.returncode:
        raise SwitchError("Factory 未正常退出，请手动退出 Factory 后重试。")


def start(app):
    result = run(["/usr/bin/open", "-a", str(app)])
    if result.returncode:
        raise SwitchError("macOS 未能启动 Factory。")
