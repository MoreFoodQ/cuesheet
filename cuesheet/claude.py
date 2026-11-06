"""Claude Code：用 `claude -p` 串流模式的 get_usage 控制請求讀方案額度。

不送任何對話訊息，不會開模型回合，也不會留下 session 紀錄（--no-session-persistence）。
"""
from __future__ import annotations

from .common import find_exe, make_bucket, provider_result, run_jsonl

SOURCE = "claude -p 串流模式的 get_usage 控制請求（官方 CLI，不花額度）"
LABELS = {
    "five_hour": ("5 小時", "5h"),
    "seven_day": ("每週（全部模型）", "weekly"),
    "seven_day_opus": ("每週 Opus", "weekly"),
    "seven_day_sonnet": ("每週 Sonnet", "weekly"),
    "seven_day_oauth_apps": ("每週 OAuth 應用", "weekly"),
}


def _new() -> dict:
    return provider_result("claude", "Claude Code", SOURCE)


def parse(data: dict) -> dict:
    """解析 get_usage 回應裡的 response 欄位。"""
    res = _new()
    res["plan"] = data.get("subscription_type")
    if not data.get("rate_limits_available"):
        res["error"] = "這個登入方式沒有方案額度（API 金鑰、Bedrock、Vertex 或權限不足）"
        return res

    limits = data.get("rate_limits") or {}
    for key, val in limits.items():
        if not isinstance(val, dict) or val.get("utilization") is None or "resets_at" not in val:
            continue
        label, window = LABELS.get(key, (key, "weekly" if key.startswith("seven_day") else "unknown"))
        res["buckets"].append(make_bucket(key, label, window, used_percent=val["utilization"],
                                          resets_at=val.get("resets_at"),
                                          note=val.get("locked_reason")))
    for item in limits.get("model_scoped") or []:
        if isinstance(item, dict) and item.get("utilization") is not None:
            name = item.get("display_name") or "model"
            res["buckets"].append(make_bucket(f"model:{name}", f"每週 {name}", "weekly",
                                              used_percent=item["utilization"],
                                              resets_at=item.get("resets_at")))
    extra = limits.get("extra_usage") or {}
    res["extra"]["extra_usage_enabled"] = bool(extra.get("is_enabled"))
    if not res["buckets"]:
        res["error"] = "get_usage 有回應，但沒有任何額度欄位"
        return res
    res["status"] = "ok"
    return res


def collect(cfg: dict, timeout: float) -> dict:
    exe = find_exe("claude", tuple(cfg.get("claude_paths", [])))
    if not exe:
        res = _new()
        res["error"] = "找不到 claude 執行檔（PATH 與設定的路徑都沒有）"
        return res
    cmd = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
           "--verbose", "--no-session-persistence", "--strict-mcp-config"]
    request = {"type": "control_request", "request_id": "cuesheet",
               "request": {"subtype": "get_usage", "skip_behaviors": True}}
    try:
        reply = run_jsonl(cmd, [request], lambda d: d.get("type") == "control_response", timeout)
    except Exception as exc:  # noqa: BLE001 — 任何失敗都要寫進報告
        res = _new()
        res["error"] = f"呼叫 claude 失敗：{exc}"
        return res

    body = reply.get("response", {})
    if body.get("subtype") != "success":
        res = _new()
        res["error"] = f"get_usage 回傳錯誤：{body.get('error') or body}"
        return res
    return parse(body.get("response") or {})
