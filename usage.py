"""Read-only Factory usage. Credentials never cross the desktop bridge."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import urllib.error
import urllib.request

import switcher as core


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        return None


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def date(value):
    if value is None:
        return None
    try:
        if number(value):
            return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.isoformat() if parsed.tzinfo else None
    except (ValueError, TypeError, AttributeError, OverflowError, OSError):
        return None


def read_credentials(account_id):
    path = core.backup_path(account_id)
    current = core.current_files()
    if core.auth_file(current) and core.snapshot_identity(current)["account_id"] == account_id:
        files = current
    else:
        files = core.read_snapshot(path)["files"]
    if core.snapshot_identity(files)["account_id"] != account_id:
        raise core.SwitchError("账号备份身份不匹配。")
    return core.snapshot_credentials(files)


def request(credentials, endpoint):
    region = (credentials.get("whoami") or {}).get("region")
    host = "api.eu.factory.ai" if region == "eu" else "api.factory.ai"
    headers = {"Authorization": "Bearer " + credentials["access_token"], "Accept": "application/json"}
    if credentials.get("active_organization_id"):
        headers["X-Factory-Org-Id"] = credentials["active_organization_id"]
    req = urllib.request.Request("https://" + host + endpoint, headers=headers, method="GET")
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=10) as response:
        data = json.loads(response.read(1024 * 1024))
    if not isinstance(data, dict):
        raise ValueError("Unexpected usage format")
    return data


def normalize_limits(data):
    pools = []
    limits = data.get("limits") or {}
    for key, label in (("standard", "Standard"), ("core", "Droid Core"), ("extraUsage", "额外用量")):
        windows = []
        for window, title in (("fiveHour", "5 小时"), ("weekly", "周"), ("monthly", "月")):
            bucket = (limits.get(key) or {}).get(window)
            if not isinstance(bucket, dict) or not number(bucket.get("usedPercent")):
                continue
            windows.append({"label": title, "used_percent": bucket["usedPercent"],
                            "resets_at": date(bucket.get("windowEnd"))})
        if windows:
            pools.append({"label": label, "windows": windows})
    return pools


def normalize_subscription(data):
    usage = data.get("usage") or {}
    pools = []
    for key, label in (("standard", "Standard"), ("premium", "Premium")):
        bucket = usage.get(key) or {}
        used, limit = bucket.get("orgTotalTokensUsed"), bucket.get("totalAllowance")
        if not number(used) or not number(limit) or (limit == 0 and used == 0):
            continue
        pools.append({"label": label, "scope": "组织额度", "used": used, "limit": limit,
                      "used_percent": used / limit * 100 if limit else None,
                      "resets_at": date(usage.get("endDate"))})
        user_used, user_limit = bucket.get("userTokens"), bucket.get("userLimit")
        if number(user_used) and number(user_limit):
            pools.append({"label": label, "scope": "个人额度", "used": user_used, "limit": user_limit,
                          "used_percent": user_used / user_limit * 100 if user_limit else None,
                          "resets_at": date(usage.get("endDate"))})
    return pools


def fetch(account_id):
    result = {"account_id": account_id, "checked_at": datetime.now(timezone.utc).isoformat()}
    try:
        credentials = read_credentials(account_id)
        data = request(credentials, "/api/billing/limits")
        pools = normalize_limits(data)
        if not pools:
            pools = normalize_subscription(request(credentials, "/api/organization/subscription/usage"))
        if not pools:
            return {**result, "status": "unavailable", "error": "此账号暂未提供用量数据"}
        return {**result, "status": "ok", "pools": pools}
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            message = "登录已过期，请切换账号并在 Factory 登录后重试"
        elif exc.code == 403:
            message = "此账号无权读取用量"
        elif exc.code == 429:
            message = "查询过于频繁，请稍后刷新"
        else:
            message = "用量查询失败（HTTP " + str(exc.code) + "）"
    except (urllib.error.URLError, TimeoutError):
        message = "无法连接用量接口，请稍后刷新"
    except Exception:
        # Never return server bodies, tokens, credential errors or raw exception text.
        message = "无法读取账号用量，请刷新或重新保存账号"
    return {**result, "status": "error", "error": message}
