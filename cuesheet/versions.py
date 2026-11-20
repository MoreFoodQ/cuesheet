"""CLI 版本檢查：只查不裝。

- Codex CLI：npm registry 的 dist-tags（latest）
- Claude Code：npm registry 的 dist-tags，依使用者設定的更新管道（stable 或 latest）比對
- agy：自己會在啟動時檢查並更新，讀它留下的 updater/update_status.json

CLI 太舊會拿不到新模型（2026-11-18：Codex CLI 0.164.0 的模型清單沒有 GPT-6.2 Sol，0.170.0 才有）。
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .common import find_exe, now_iso, run_capture

DIST_TAGS = "https://registry.npmjs.org/-/package/{pkg}/dist-tags"
AGY_FALLBACKS = (r"%LOCALAPPDATA%\agy\bin\agy.exe",)
AGY_STATUS = Path.home() / ".gemini" / "antigravity-cli" / "updater" / "update_status.json"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"

UPDATE_HINTS = {
    "claude": "claude update",
    "codex": "npm install -g @openai/codex@latest",
    "agy": "agy update",
}
LABELS = {"claude": "Claude Code", "codex": "Codex CLI", "agy": "Antigravity agy"}


def parse_version(text: str | None) -> tuple[int, ...] | None:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def fmt_version(v: tuple[int, ...] | None) -> str | None:
    return ".".join(map(str, v)) if v else None


def installed_version(name: str, cfg: dict, timeout: float = 20) -> str | None:
    fallbacks = tuple(cfg.get(f"{name}_paths", [])) + (AGY_FALLBACKS if name == "agy" else ())
    exe = find_exe(name, fallbacks)
    if not exe:
        return None
    try:
        _, out, err = run_capture([exe, "--version"], timeout)
    except (OSError, TimeoutError):
        return None
    return fmt_version(parse_version(out + " " + err))


def npm_dist_tags(pkg: str, timeout: float = 15) -> dict:
    req = urllib.request.Request(DIST_TAGS.format(pkg=pkg), headers={"User-Agent": "cuesheet"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def compare(installed: str | None, latest: str | None) -> str:
    a, b = parse_version(installed), parse_version(latest)
    if not a or not b:
        return "unknown"
    return "outdated" if a < b else "ok"


def claude_channel() -> str:
    try:
        return json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8")).get("autoUpdatesChannel") or "latest"
    except (OSError, json.JSONDecodeError):
        return "latest"


def check_npm(name: str, pkg: str, installed: str | None, channel: str, fetch=npm_dist_tags) -> dict:
    row = {"cli": name, "label": LABELS[name], "installed": installed, "latest": None, "channel": channel,
           "status": "unknown", "source": f"npm registry {pkg}", "note": None, "update": UPDATE_HINTS[name]}
    if not installed:
        row["note"] = "找不到執行檔或讀不到版本"
        return row
    try:
        tags = fetch(pkg)
    except Exception as exc:  # noqa: BLE001
        row["note"] = f"讀不到最新版本：{exc}"
        return row
    row["latest"] = tags.get(channel) or tags.get("latest")
    row["status"] = compare(installed, row["latest"])
    return row


def check_agy(installed: str | None, status_path: Path = AGY_STATUS) -> dict:
    row = {"cli": "agy", "label": LABELS["agy"], "installed": installed, "latest": None, "channel": "auto",
           "status": "unknown", "source": "agy 自動更新紀錄", "note": None, "update": UPDATE_HINTS["agy"]}
    if not installed:
        row["note"] = "找不到執行檔或讀不到版本"
        return row
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
        checked = datetime.fromtimestamp(status_path.stat().st_mtime, timezone.utc)
    except (OSError, json.JSONDecodeError):
        row["note"] = "agy 會在啟動時自己檢查更新；讀不到它的更新紀錄"
        return row
    message = data.get("message") or ""
    row["checked_at"] = checked.isoformat(timespec="seconds")
    if data.get("success") and "latest" in message.lower():
        row["status"] = "ok"
    row["note"] = f"agy 啟動時自己檢查更新，上次結果：{message}（{checked.astimezone():%m/%d %H:%M}）"
    return row


def check_versions(cfg: dict, timeout: float = 20, fetch=npm_dist_tags) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=3) as pool:
        inst = {n: pool.submit(installed_version, n, cfg, timeout) for n in ("claude", "codex", "agy")}
        inst = {n: f.result() for n, f in inst.items()}
    return {
        "checked_at": now_iso(),
        "clis": [
            check_npm("claude", "@anthropic-ai/claude-code", inst["claude"], claude_channel(), fetch),
            check_npm("codex", "@openai/codex", inst["codex"], "latest", fetch),
            check_agy(inst["agy"]),
        ],
    }
