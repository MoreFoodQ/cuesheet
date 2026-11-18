"""訂閱額度的真實消耗：接力每次執行前後各記一次額度，存成樣本。

用乾淨樣本的「總消耗 ÷ 次數」估計每個模型做一件事吃掉幾 % 額度（統計與挑選在 route.py）。

樣本標記（只有 clean 會算進成本）：
- clean：前後都是即時資料、沒跨重置、任務完成、沒偵測到其他工作
- stale_snapshot：前後任一次不是即時資料
- incomplete：任務沒完成（失敗、逾時、額度用完、權限、模型不存在）
- window_reset：執行期間額度重置（重置時間變了或用量變少）
- capped：執行後用量到 99% 以上
- concurrent：執行期間同一家的 CLI 有其他 session 在寫紀錄
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .common import now_iso, parse_iso

RESET_TOLERANCE_SECONDS = 120
CAPPED_PERCENT = 99

# 各 CLI 寫 session 紀錄的位置；執行期間被改過的檔案數量，用來判斷有沒有其他工作同時在用
SESSION_DIRS = {
    "claude": Path.home() / ".claude" / "projects",
    "codex": Path.home() / ".codex" / "sessions",
    "agy": Path.home() / ".gemini" / "antigravity-cli" / "conversations",
}
EXE_PLATFORM = {"claude": "claude", "codex": "codex", "agy": "agy"}


def snapshot(provider_result: dict, bucket_ids: list[str]) -> dict:
    """從 provider 查詢結果取出這個代理會用到的額度桶。"""
    by_id = {b["id"]: b for b in provider_result.get("buckets", [])}
    return {
        "status": provider_result.get("status", "unavailable"),
        "taken_at": now_iso(),
        "buckets": {bid: {"used": by_id[bid]["used_percent"], "resets_at": by_id[bid]["resets_at"]}
                    for bid in bucket_ids if bid in by_id},
    }


def deltas(before: dict, after: dict) -> dict:
    out = {}
    for bid, b in before.get("buckets", {}).items():
        a = after.get("buckets", {}).get(bid)
        if a is None or b["used"] is None or a["used"] is None:
            continue
        out[bid] = round(a["used"] - b["used"], 2)
    return out


def _reset_happened(b: dict, a: dict) -> bool:
    rb, ra = parse_iso(b.get("resets_at")), parse_iso(a.get("resets_at"))
    if rb and ra and abs((ra - rb).total_seconds()) > RESET_TOLERANCE_SECONDS:
        return True
    return b.get("used") is not None and a.get("used") is not None and a["used"] < b["used"]


def classify(before: dict, after: dict, attempt_result: str, concurrent: int) -> str:
    if before.get("status") != "ok" or after.get("status") != "ok":
        return "stale_snapshot"
    if attempt_result != "success":
        return "incomplete"
    for bid, b in before.get("buckets", {}).items():
        a = after.get("buckets", {}).get(bid)
        if a and _reset_happened(b, a):
            return "window_reset"
    if any((a.get("used") or 0) >= CAPPED_PERCENT for a in after.get("buckets", {}).values()):
        return "capped"
    if concurrent > 0:
        return "concurrent"
    return "clean"


def session_activity(platform: str | None, t0: float, t1: float, dirs: dict = SESSION_DIRS) -> int | None:
    """執行期間（t0～t1，epoch 秒）這家 CLI 有幾個 session 紀錄檔被寫過；讀不到回傳 None。"""
    root = dirs.get(platform or "")
    if not root or not root.is_dir():
        return None
    count = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            try:
                mtime = os.stat(os.path.join(dirpath, name)).st_mtime
            except OSError:
                continue
            if t0 <= mtime <= t1 + 5:
                count += 1
    return count


def platform_of(runner: dict) -> str | None:
    if runner.get("platform"):
        return runner["platform"]
    exe = Path(str(runner.get("exe", ""))).stem.lower()
    return EXE_PLATFORM.get(exe)


def build_sample(*, task: str, agent: str, runner: dict, quota_agent: str, before: dict, after: dict,
                 attempt: dict, started_at: str, finished_at: str, files_touched: int | None) -> dict:
    """files_touched：執行期間這家 CLI 被寫過的 session 檔數。本次執行自己會寫一個，超過一個視為有其他工作。"""
    concurrent = max(0, (files_touched or 0) - 1)
    return {
        "task": task,
        "agent": agent,
        "platform": platform_of(runner),
        "model": runner.get("model"),
        "effort": runner.get("effort"),
        "quota_agent": quota_agent,
        "started_at": started_at,
        "finished_at": finished_at,
        "seconds": attempt.get("seconds"),
        "result": attempt.get("result"),
        "before": before,
        "after": after,
        "delta": deltas(before, after),
        "session_files_touched": files_touched,
        "status": classify(before, after, attempt.get("result", ""), concurrent),
    }


def append(path: Path, sample: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
