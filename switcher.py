"""Factory account switching for Windows/macOS. Never print/store plaintext tokens."""
from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
from ctypes import wintypes as w
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.request

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from errors import SwitchError

MACOS = sys.platform == "darwin"
if MACOS:
    import mac_backend
HOME = Path.home() / ".factory"
STORE = (Path.home() / "Library/Application Support/FactoryAccountSwitcher" if MACOS else
         Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FactoryAccountSwitcher")
APP = mac_backend.app_path() if MACOS else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Factory" / "factory-desktop.exe"
AUTH_FILES = ("auth.v2.keyring", "auth.v2.loginkeychain") if MACOS else ("auth.v2.keyring",)
BACKUP_SUFFIX = ".keychain" if MACOS else ".dpapi"
# Preserve settings/history; only replace authentication and account-specific policy caches.
FILES = AUTH_FILES + ("org-managed-settings.cache.json", "org-managed-settings.cache.json.backup")
UNSUPPORTED = ("auth.v2.file", "auth.v2.key") + (() if MACOS else ("auth.v2.loginkeychain",))
CREATE_NO_WINDOW = 0x08000000


def auth_file(files):
    found = [name for name in AUTH_FILES if name in files]
    if len(found) > 1:
        raise SwitchError("存在多套 Factory 登录文件，无法确定当前使用哪套，已停止操作。")
    return found[0] if found else None


def snapshot_identity(files):
    name = auth_file(files)
    if name is None:
        raise SwitchError("当前尚未登录，请先在 Factory 完成登录。")
    return identity(files[name]) if name == "auth.v2.keyring" else identity(files[name], name)


def snapshot_credentials(files):
    name = auth_file(files)
    if name is None:
        raise SwitchError("当前没有登录信息。")
    return decrypt_credentials(files[name]) if name == "auth.v2.keyring" else decrypt_credentials(files[name], name)


def recovery_path():
    return STORE / ("recovery" + BACKUP_SUFFIX)


class Credential(ctypes.Structure):
    _fields_ = [("Flags", w.DWORD), ("Type", w.DWORD), ("TargetName", w.LPWSTR),
                ("Comment", w.LPWSTR), ("LastWritten", w.FILETIME),
                ("CredentialBlobSize", w.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", w.DWORD), ("AttributeCount", w.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", w.LPWSTR), ("UserName", w.LPWSTR)]


def auth_key(filename="auth.v2.keyring") -> bytes:
    if MACOS:
        return mac_backend.auth_key(filename)
    api = ctypes.WinDLL("Advapi32", use_last_error=True)
    api.CredReadW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
    api.CredReadW.restype = w.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    ptr = ctypes.POINTER(Credential)()
    if not api.CredReadW("Factory CLI/auth-encryption-key", 1, 0, ctypes.byref(ptr)):
        raise SwitchError("无法读取本机 Factory 密钥；未修改任何登录文件。")
    try:
        value = ctypes.string_at(ptr.contents.CredentialBlob, ptr.contents.CredentialBlobSize)
        key = base64.b64decode(value, validate=True)
        if len(key) != 32:
            raise ValueError()
        return key
    except (ValueError, TypeError):
        raise SwitchError("Factory 密钥格式不支持。") from None
    finally:
        api.CredFree(ptr)


def decrypt_credentials(ciphertext: bytes, filename="auth.v2.keyring") -> dict:
    try:
        iv, tag, encrypted = [base64.b64decode(p, validate=True) for p in ciphertext.strip().split(b":")]
        return json.loads(AESGCM(auth_key(filename)).decrypt(iv, encrypted + tag, None))
    except SwitchError:
        raise
    except Exception:
        raise SwitchError("无法解密 Factory 登录文件。") from None


def identity(ciphertext: bytes, filename="auth.v2.keyring") -> dict:
    try:
        credentials = decrypt_credentials(ciphertext, filename)
        token = credentials["access_token"]
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        # Local identity hint, NOT a server-validated login status.
        subject = claims.get("sub") or claims.get("id")
        if not isinstance(subject, str) or not subject:
            raise ValueError()
        return {"account_id": hashlib.sha256(subject.encode()).hexdigest()[:24],
                "email": claims.get("email", ""), "expires_at": claims.get("exp"),
                "server_verified": False}
    except SwitchError:
        raise
    except Exception:
        raise SwitchError("无法识别当前登录格式或解密失败；未输出凭据。") from None


class Blob(ctypes.Structure):
    _fields_ = [("size", w.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(data: bytes, decrypt: bool = False) -> bytes:
    if MACOS:
        return mac_backend.protect(data, STORE, decrypt)
    buffer = ctypes.create_string_buffer(data)
    src = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    dst = Blob()
    crypt = ctypes.WinDLL("Crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("Kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ctypes.POINTER(Blob)]
    fn.restype = w.BOOL
    if not fn(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)):
        raise SwitchError("Windows 备份加密/解密失败。请使用创建备份的 Windows 账号。")
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        kernel.LocalFree(dst.data)


@contextlib.contextmanager
def exclusive():
    if MACOS:
        with mac_backend.exclusive(STORE):
            yield
        return
    kernel = ctypes.WinDLL("Kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
    kernel.CreateMutexW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateMutexW(None, False, "Local\\FactoryAccountSwitcherProof")
    error = ctypes.get_last_error()
    if not handle:
        raise SwitchError("无法创建切换锁。")
    try:
        if error == 183:
            raise SwitchError("另一个切换操作正在进行，请稍后重试。")
        yield
    finally:
        kernel.CloseHandle(handle)


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def current_files() -> dict[str, bytes]:
    if any((HOME / name).exists() for name in UNSUPPORTED):
        raise SwitchError("发现其他认证存储格式，验证版暂停操作以避免冲突。")
    if (HOME / "auth.v2.write.lock").exists():
        raise SwitchError("Factory 正在写登录状态，请稍后重试。")
    files = {name: (HOME / name).read_bytes() for name in FILES if (HOME / name).is_file()}
    auth_file(files)
    return files


def backup_path(account_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{24}", account_id):
        raise SwitchError("账号标识无效。")
    return STORE / "accounts" / (account_id + BACKUP_SUFFIX)


def encode_snapshot(files: dict, info: dict, label: str) -> bytes:
    payload = {"schema": 1, "identity": info, "label": label,
               "saved_at": datetime.now(timezone.utc).isoformat(),
               "files": {name: base64.b64encode(value).decode() for name, value in files.items()}}
    return protect(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def read_snapshot(path: Path) -> dict:
    value = json.loads(protect(path.read_bytes(), decrypt=True))
    if value.get("schema") != 1 or not set(value["files"]).issubset(FILES):
        raise SwitchError("备份格式不支持。")
    value["files"] = {name: base64.b64decode(data, validate=True) for name, data in value["files"].items()}
    return value


def capture(label: str = "") -> dict:
    files = current_files()
    if not auth_file(files):
        raise SwitchError("当前尚未登录，请先在 Factory 完成登录。")
    info = snapshot_identity(files)
    path = backup_path(info["account_id"])
    old_label = read_snapshot(path)["label"] if path.exists() else ""
    label = label or old_label or info["email"] or info["account_id"]
    encoded = encode_snapshot(files, info, label)
    # Verify encryption round trip BEFORE committing the snapshot.
    decoded = json.loads(protect(encoded, decrypt=True))
    checked_files = {name: base64.b64decode(value, validate=True) for name, value in decoded["files"].items()}
    if checked_files != files or decoded["identity"] != info:
        raise SwitchError("备份验证失败。")
    atomic_write(path, encoded)
    restored = read_snapshot(path)
    if restored["files"] != files:
        raise SwitchError("备份字节校验失败。")
    return {**info, "label": label, "saved_at": restored["saved_at"], "backup_verified": True}


def accounts() -> list[dict]:
    result = []
    for path in sorted((STORE / "accounts").glob("*" + BACKUP_SUFFIX)):
        value = read_snapshot(path)
        result.append({**value["identity"], "label": value["label"], "saved_at": value["saved_at"]})
    return result


def powershell(code: str) -> str:
    # Fail closed on real query errors, even if a later command succeeds.
    code = "$ErrorActionPreference='Stop'; " + code
    encoded = base64.b64encode(code.encode("utf-16-le")).decode()
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=45)
    if result.returncode:
        raise SwitchError("Windows 进程检查/操作失败；未继续切换。")
    return result.stdout.decode("utf-8-sig").strip()


def processes() -> list[dict]:
    if MACOS:
        return mac_backend.processes(APP)
    text = powershell("[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
                      "$items=@(Get-CimInstance Win32_Process -Filter \"Name='factory-desktop.exe' OR Name='droid.exe'\" | "
                      "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath); ConvertTo-Json -InputObject $items -Compress")
    return json.loads(text or "[]")


def stop_factory():
    # Do not kill CLI sessions or bypass the desktop application's quit guards.
    before = processes()
    if MACOS:
        mac_backend.request_quit(APP, before)
    else:
        if not before:
            return
        standalone = [p for p in before if p["Name"] == "droid.exe" and
                      "\\Factory\\app-" not in (p.get("ExecutablePath") or "")]
        if standalone:
            raise SwitchError("检测到独立 Droid CLI，请先正常退出，以免它写入旧账号状态。")
        # Filtering the process list also tolerates Factory exiting between
        # the initial check and this request; -Name would report an error.
        powershell("Get-Process -ErrorAction Stop | "
                   "Where-Object { $_.ProcessName -eq 'factory-desktop' -and $_.MainWindowHandle -ne 0 } | "
                   "ForEach-Object { [void]$_.CloseMainWindow() }")
    deadline = time.monotonic() + 18
    while True:
        if not processes():
            return
        if time.monotonic() >= deadline:
            raise SwitchError("Factory 尚未完全退出。请在 Factory 中正常退出后重试；登录文件未被替换。")
        time.sleep(1)


def start_factory():
    check_launch_environment()
    if MACOS:
        mac_backend.start(APP)
    else:
        subprocess.Popen([str(APP)], cwd=str(APP.parent), close_fds=True)


def check_launch_environment():
    if MACOS:
        mac_backend.validate_app(APP)
    elif not APP.is_file():
        raise SwitchError("找不到 Factory 启动程序。")
    if os.environ.get("FACTORY_HOME_OVERRIDE") or os.environ.get("FACTORY_API_KEY") or os.environ.get("FACTORY_DISABLE_KEYRING"):
        raise SwitchError("启动环境有 Factory 认证覆盖项，验证版暂停启动。")


def replace_files(files: dict[str, bytes]):
    if processes():
        raise SwitchError("检测到 Factory / Droid 仍在运行，拒绝替换登录文件。")
    current_files()  # Refuse an active writer or unsupported storage backend.
    for name in FILES:
        path = HOME / name
        if name in files:
            atomic_write(path, files[name])
        else:
            path.unlink(missing_ok=True)


def activate(account_id: str | None) -> dict:
    check_launch_environment()
    if account_id is None and not auth_file(current_files()):
        raise SwitchError("Factory 已处于未登录状态，请直接完成登录；原恢复点已保留。")
    target = read_snapshot(backup_path(account_id)) if account_id else None
    if target:
        actual = snapshot_identity(target["files"])
        if actual["account_id"] != account_id:
            raise SwitchError("目标备份的账号校验失败。")
    stop_factory()
    previous = current_files()
    if auth_file(previous):
        capture()  # Save refreshed tokens immediately before leaving this account.
        # A same-account round trip must restore the latest snapshot, not stale tokens.
        if target:
            target = read_snapshot(backup_path(account_id))
    journal = recovery_path()
    atomic_write(journal, encode_snapshot(previous, {}, "切换前恢复点"))
    try:
        replace_files(target["files"] if target else {})
        if target and snapshot_identity(current_files())["account_id"] != account_id:
            raise SwitchError("切换后的账号校验失败。")
        start_factory()
    except Exception:
        # Restore only while stopped; recovery.dpapi remains available after failures.
        if not processes():
            replace_files(previous)
        raise
    return {"launched": True, "local_account_id": account_id,
            "server_verified": False, "next_step": "请在 Factory 检查登录账号和连接状态" if target else "请在 Factory 登录第二个账号，然后保存当前账号"}


def recover() -> dict:
    check_launch_environment()
    snapshot = read_snapshot(recovery_path())
    stop_factory()
    # Keep any new login available before restoring the pre-switch state.
    if auth_file(current_files()):
        capture()
    replace_files(snapshot["files"])
    start_factory()
    return {"restored": True, "server_verified": False}


def status() -> dict:
    files = current_files()
    return {"local_identity": snapshot_identity(files) if auth_file(files) else None,
            "processes": [{"pid": p["ProcessId"], "name": p["Name"]} for p in processes()],
            "saved_accounts": accounts(), "storage": str(STORE)}


def verify() -> dict:
    """Read-only official whoami call; do not refresh/rotate Factory's tokens."""
    files = current_files()
    if not auth_file(files):
        raise SwitchError("当前没有登录信息。")
    credentials = snapshot_credentials(files)
    hint = snapshot_identity(files)
    region = (credentials.get("whoami") or {}).get("region")
    host = "api.eu.factory.ai" if region == "eu" else "api.factory.ai"
    headers = {"Authorization": "Bearer " + credentials["access_token"], "X-Factory-Whoami-Extended": "true"}
    if credentials.get("active_organization_id"):
        headers["X-Factory-Org-Id"] = credentials["active_organization_id"]

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None

    request = urllib.request.Request("https://" + host + "/api/cli/whoami", headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            data = json.load(response)
            user_id = data.get("userId")
            same = isinstance(user_id, str) and hashlib.sha256(user_id.encode()).hexdigest()[:24] == hint["account_id"]
            return {"http_status": response.status, "server_verified": same,
                    "account_matches": same, "local_account_id": hint["account_id"]}
    except urllib.error.HTTPError as exc:
        return {"http_status": exc.code, "server_verified": False, "local_account_id": hint["account_id"]}
    except (urllib.error.URLError, TimeoutError):
        return {"server_verified": False, "error": "无法连接 Factory 账号验证接口"}


def main():
    parser = argparse.ArgumentParser(description="Factory 本机切号验证工具")
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("status")
    subs.add_parser("verify")
    save = subs.add_parser("save")
    save.add_argument("--label", default="")
    switch = subs.add_parser("switch")
    switch.add_argument("account_id")
    subs.add_parser("new-login")
    subs.add_parser("recover")
    args = parser.parse_args()
    try:
        with exclusive():
            if args.command == "status":
                result = status()
            elif args.command == "verify":
                result = verify()
            elif args.command == "save":
                result = capture(args.label)
            elif args.command == "switch":
                result = activate(args.account_id)
            elif args.command == "new-login":
                result = activate(None)
            else:
                result = recover()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except SwitchError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception as exc:
        # Do not echo payloads, subprocess outputs, or token-bearing exception messages.
        print(json.dumps({"error": "操作失败，未输出敏感内容", "type": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
