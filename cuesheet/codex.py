"""Codex CLI：用 `codex app-server` 的 account/rateLimits/read 讀即時額度。

失敗時退回讀 ~/.codex/sessions 最新的 session 紀錄（只是最後一次使用時的快照，會標成 stale）。
注意：app-server 另有 account/rateLimitResetCredit/consume（會用掉重置點數），本程式絕不呼叫。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .common import (WINDOW_LABELS, find_exe, make_bucket, now_utc, parse_iso, provider_result,
                     run_jsonl, to_iso, window_from_minutes)

LIVE_SOURCE = "codex app-server 的 account/rateLimits/read（官方 CLI，不花額度）"


def _new(source: str = LIVE_SOURCE) -> dict:
    return provider_result("codex", "Codex CLI（含 Luna）", source)


def _buckets_from_snapshot(limit_id: str, snap: dict, keys: tuple[str, str, str]) -> list[dict]:
    mins_key, used_key, reset_key = keys
    out = []
    for slot in ("primary", "secondary"):
        win = snap.get(slot)
        if not isinstance(win, dict):
            continue
        window = window_from_minutes(win.get(mins_key))
        label = f"{limit_id} {WINDOW_LABELS.get(window, window)}"
        out.append(make_bucket(f"{limit_id}.{window}", label, window,
                               used_percent=win.get(used_key), resets_at=win.get(reset_key)))
    return out


def parse_live(result: dict) -> dict:
    """解析 account/rateLimits/read 的 result。"""
    res = _new()
    by_id = result.get("rateLimitsByLimitId") or {}
    if not by_id and result.get("rateLimits"):
        by_id = {result["rateLimits"].get("limitId") or "codex": result["rateLimits"]}
    for limit_id, snap in by_id.items():
        res["buckets"] += _buckets_from_snapshot(limit_id, snap, ("windowDurationMins", "usedPercent", "resetsAt"))
        res["plan"] = res["plan"] or snap.get("planType")
        if snap.get("rateLimitReachedType"):
            res["notes"].append(f"{limit_id} 已觸頂：{snap['rateLimitReachedType']}")
    credits = result.get("rateLimitResetCredits") or {}
    res["extra"] = {
        "ordinary_usage_allowed": result.get("ordinaryUsageAllowed"),
        "reset_credits_available": credits.get("availableCount"),
        "reset_credits_expire": [to_iso(c.get("expiresAt")) for c in credits.get("credits", [])
                                 if c.get("status") == "available"],
    }
    if credits.get("availableCount"):
        res["notes"].append(f"有 {credits['availableCount']} 次免費「額度全重置」可用（需手動在 Codex 使用，本程式不會動它）")
    if res["buckets"]:
        res["status"] = "ok"
    else:
        res["error"] = "有回應但沒有時間窗資料"
    return res


def parse_snapshot(rl: dict, observed_at: str, filename: str, live_error: str,
                   now: datetime | None = None) -> dict:
    """解析 session 紀錄裡的 rate_limits 快照；已過重置時間的額度標為未知。"""
    res = _new(f"~/.codex/sessions 最新紀錄（{filename}）")
    res["plan"] = rl.get("plan_type")
    now = now or now_utc()
    for b in _buckets_from_snapshot(rl.get("limit_id") or "codex", rl, ("window_minutes", "used_percent", "resets_at")):
        reset = parse_iso(b["resets_at"])
        if reset and reset <= now:
            b["used_percent"] = b["remaining_percent"] = None
            b["note"] = "快照之後已過重置時間，目前用量未知"
        res["buckets"].append(b)
    res["status"] = "stale"
    res["extra"]["observed_at"] = to_iso(observed_at)
    res["error"] = f"即時查詢失敗（{live_error}），改用最後一次使用時的快照"
    return res


def _live(exe: str, timeout: float) -> dict:
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientInfo": {"name": "cuesheet", "version": "0.1"}}},
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": {}},
    ]
    reply = run_jsonl([exe, "app-server"], msgs, lambda d: d.get("id") == 2, timeout)
    if "error" in reply:
        raise RuntimeError(f"account/rateLimits/read 回傳錯誤：{reply['error']}")
    return reply.get("result") or {}


def _latest_session_snapshot(codex_home: Path) -> tuple[dict, str, str] | None:
    """從最新的 session jsonl 找最後一筆有時間窗的 rate_limits。"""
    files = sorted((codex_home / "sessions").rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:20]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            rl = (obj.get("payload") or {}).get("rate_limits") or {}
            if rl.get("primary") or rl.get("secondary"):
                return rl, obj.get("timestamp", ""), path.name
    return None


def collect(cfg: dict, timeout: float) -> dict:
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    exe = find_exe("codex", tuple(cfg.get("codex_paths", [])))
    if exe:
        try:
            res = parse_live(_live(exe, timeout))
            if res["status"] == "ok":
                return res
            live_error = res["error"]
        except Exception as exc:  # noqa: BLE001
            live_error = str(exc)
    else:
        live_error = "找不到 codex 執行檔"

    snap = _latest_session_snapshot(codex_home)
    if not snap:
        res = _new()
        res["error"] = f"即時查詢失敗（{live_error}），session 紀錄裡也找不到 rate_limits"
        return res
    return parse_snapshot(*snap, live_error)
