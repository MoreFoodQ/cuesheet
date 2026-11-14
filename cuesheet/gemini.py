"""Gemini（Antigravity 的 agy CLI）：用 `agy -p "/usage" --output-format json` 讀額度。

agy 的 changelog 說明 print 模式下的 /usage 不會開模型回合、不花額度。
舊版 agy 若不認得 /usage，會把它當成一般提問送給模型（會花少量額度），
所以這裡檢查 num_turns 與 command 欄位，不符就標成無法取得並提醒更新。

週期還沒開始的額度桶（重置後還沒用過）會回「剩 100%、重置時間＝現在＋一個週期」，
而且沒有 description。這是真實狀態，不是讀取失敗（2026-11-07 實測：用了一點之後
同一個桶就出現固定的重置時間與 description）。這裡照實顯示，只加註說明。
"""
from __future__ import annotations

from datetime import timedelta

from .common import WINDOW_LABELS, find_exe, make_bucket, now_utc, parse_iso, provider_result, run_json, to_iso

WINDOW_SECONDS = {"5h": 5 * 3600, "weekly": 7 * 86400}
WINDOW_SLACK = 180  # 秒


def window_not_started(b: dict, checked_at) -> bool:
    """剩 100%、沒有 description，而且重置時間剛好是查詢時間加上一個完整週期。"""
    secs = WINDOW_SECONDS.get(b.get("window"))
    reset = parse_iso(to_iso(b.get("reset_time")))
    if b.get("remaining_fraction") != 1 or b.get("description") or not secs or not reset:
        return False
    return abs((reset - checked_at - timedelta(seconds=secs)).total_seconds()) <= WINDOW_SLACK

SOURCE = 'agy -p "/usage" --output-format json（官方 CLI，不花額度）'
DEFAULT_PATHS = (r"%LOCALAPPDATA%\agy\bin\agy.exe",)
GROUP_LABELS = {
    "Gemini Models": "Gemini 組（Flash＋Pro）",
    "Claude and GPT models": "Claude／GPT 組",
    "Gemini 4 Argon": "Argon 專屬",
}


def _new() -> dict:
    return provider_result("gemini", "Antigravity agy（Gemini 等）", SOURCE)


def parse(out: dict, checked_at=None) -> dict:
    """解析 agy /usage 的 JSON 輸出。"""
    res = _new()
    checked_at = checked_at or now_utc()
    cmd = out.get("command") or {}
    if out.get("num_turns") or cmd.get("name") != "usage":
        res["error"] = ("agy 沒有把 /usage 當成指令（可能被當成一般提問送給模型，已花少量額度）。"
                        "請更新 agy，或確認沒有在 Git Bash 下手動轉換參數。")
        return res
    if out.get("status") != "SUCCESS":
        res["error"] = f"agy /usage 狀態：{out.get('status')}"
        return res

    data = cmd.get("data") or {}
    groups = []
    for group in data.get("groups") or []:
        gname = group.get("name", "")
        groups.append({"name": gname, "description": group.get("description")})
        for b in group.get("buckets") or []:
            frac = b.get("remaining_fraction")
            window = b.get("window") or "unknown"
            note = "此額度桶沒有數值（可能已停用）" if frac is None else None
            if frac is not None and window_not_started(b, checked_at):
                note = "這個週期還沒開始用；從第一次使用起算，重置時間會在那時才固定"
            label = f"{GROUP_LABELS.get(gname, gname)} {WINDOW_LABELS.get(window, b.get('name', window))}"
            bucket = make_bucket(
                b.get("id") or f"{gname}:{b.get('name')}", label, window,
                remaining_percent=None if frac is None else frac * 100, resets_at=b.get("reset_time"), note=note)
            bucket["source_label"] = f"{gname}｜{b.get('name')}"
            res["buckets"].append(bucket)
    res["extra"]["groups"] = groups
    res["extra"]["usage_description"] = data.get("description")
    res["notes"].append("同一組的模型共用每週與 5 小時額度，依 token 成本扣；"
                        "便宜的模型（Flash）同樣額度能做更多事。Pro 方案的 Argon 另有獨立週額度。")
    if not res["buckets"]:
        res["error"] = "/usage 有回應，但沒有任何額度桶"
        return res
    res["status"] = "ok"
    return res


def collect(cfg: dict, timeout: float) -> dict:
    exe = find_exe("agy", tuple(cfg.get("agy_paths", [])) + DEFAULT_PATHS)
    if not exe:
        res = _new()
        res["error"] = "找不到 agy 執行檔"
        return res
    try:
        checked_at = now_utc()
        out = run_json([exe, "-p", "/usage", "--output-format", "json"], timeout)
    except Exception as exc:  # noqa: BLE001
        res = _new()
        res["error"] = f"呼叫 agy 失敗：{exc}"
        return res
    return parse(out, checked_at)
