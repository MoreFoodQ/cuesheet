"""即時列出三家 CLI 目前提供的模型，並檢查接力設定（runners.json）裡的模型。

模型清單都用呼叫的方式取得，不寫死：
- Claude Code：`claude -p` 串流模式的 initialize 控制請求，回應裡的 models
- Codex CLI：`codex app-server` 的 model/list
- agy：`agy models`

「同系列較新版本」的判斷：從模型 id 拆出系列（opus、sol、argon…）和版本號，
同平台、同系列裡版本最大的就是最新版。
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

from .common import find_exe, now_iso, now_utc, parse_iso, run_jsonl

AGY_FALLBACKS = (r"%LOCALAPPDATA%\agy\bin\agy.exe",)
NEW_MODEL_DAYS = 7  # 第一次看到後幾天內算「新模型」


# ---------- 列模型 ----------

def _claude_id_from_name(name: str) -> str | None:
    """「Opus 5.5」→ claude-opus-5-5。別名（opus、sonnet）要靠顯示名稱換成完整 id。"""
    m = re.fullmatch(r"(Opus|Sonnet|Haiku|Fable)\s+(\d+(?:\.\d+)?)", name.strip())
    if not m:
        return None
    return f"claude-{m.group(1).lower()}-{m.group(2).replace('.', '-')}"


def parse_claude_models(models: list[dict]) -> list[dict]:
    out, seen = [], set()
    for m in models:
        value, name = m.get("value", ""), m.get("displayName", "")
        if value == "default":
            continue
        full = value if value.startswith("claude-") else (_claude_id_from_name(name) or value)
        if full in seen:
            continue
        seen.add(full)
        out.append({"id": full, "name": name, "aliases": [value] if value != full else []})
    return out


def parse_codex_models(data: list[dict]) -> list[dict]:
    return [{"id": m.get("id") or m.get("model"), "name": m.get("displayName", ""),
             "hidden": bool(m.get("hidden")),
             "efforts": [e.get("reasoningEffort") for e in m.get("supportedReasoningEfforts") or []]}
            for m in data if m.get("id") or m.get("model")]


def parse_agy_models(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 2 and re.match(r"^[a-z0-9][a-z0-9.\-]+$", parts[0]):
            out.append({"id": parts[0], "name": parts[1].strip()})
    return out


def list_claude(cfg: dict, timeout: float) -> list[dict]:
    exe = find_exe("claude", tuple(cfg.get("claude_paths", [])))
    if not exe:
        raise RuntimeError("找不到 claude 執行檔")
    reply = run_jsonl([exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                       "--verbose", "--no-session-persistence", "--strict-mcp-config"],
                      [{"type": "control_request", "request_id": "cuesheet-models",
                        "request": {"subtype": "initialize"}}],
                      lambda d: d.get("type") == "control_response", timeout)
    return parse_claude_models((reply.get("response", {}).get("response") or {}).get("models") or [])


def list_codex(cfg: dict, timeout: float) -> list[dict]:
    exe = find_exe("codex", tuple(cfg.get("codex_paths", [])))
    if not exe:
        raise RuntimeError("找不到 codex 執行檔")
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"clientInfo": {"name": "cuesheet", "version": "0.4"}}},
            {"jsonrpc": "2.0", "method": "initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "model/list", "params": {}}]
    reply = run_jsonl([exe, "app-server"], msgs, lambda d: d.get("id") == 2, timeout)
    if "error" in reply:
        raise RuntimeError(f"model/list 回傳錯誤：{reply['error']}")
    return parse_codex_models((reply.get("result") or {}).get("data") or [])


def list_agy(cfg: dict, timeout: float) -> list[dict]:
    import subprocess

    from .common import CREATE_NO_WINDOW
    exe = find_exe("agy", tuple(cfg.get("agy_paths", [])) + AGY_FALLBACKS)
    if not exe:
        raise RuntimeError("找不到 agy 執行檔")
    done = subprocess.run([exe, "models"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
    models = parse_agy_models(done.stdout)
    if not models:
        raise RuntimeError(f"agy models 沒有輸出模型（exit {done.returncode}）")
    return models


LISTERS = {"claude": list_claude, "codex": list_codex, "agy": list_agy}
PLATFORM_LABELS = {"claude": "Claude Code", "codex": "Codex CLI", "agy": "Antigravity agy"}


def collect_models(cfg: dict, timeout: float, platforms=("claude", "codex", "agy")) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    def one(p):
        try:
            return {"status": "ok", "models": LISTERS[p](cfg, timeout), "error": None}
        except Exception as exc:  # noqa: BLE001
            return {"status": "unavailable", "models": [], "error": str(exc)}

    with ThreadPoolExecutor(max_workers=len(platforms)) as pool:
        futures = {p: pool.submit(one, p) for p in platforms}
        return {p: {"checked_at": now_iso(), **f.result()} for p, f in futures.items()}


# ---------- 系列與版本 ----------

FAMILY_PATTERNS = {
    # claude-opus-5-5、claude-haiku-4-5-20251001
    "claude": re.compile(r"^claude-(?P<family>[a-z]+)-(?P<major>\d+)(?:-(?P<minor>\d{1,2}))?(?:-\d{8})?$"),
    # gpt-6.1-sol、gpt-6-luna
    "codex": re.compile(r"^gpt-(?P<major>\d+)(?:\.(?P<minor>\d+))?-(?P<family>[a-z]+)$"),
    # gemini-4-argon-medium、gemini-3.8-flash-high
    "agy": re.compile(r"^gemini-(?P<major>\d+)(?:\.(?P<minor>\d+))?-(?P<family>[a-z]+)-(?P<effort>[a-z]+)$"),
}


def parse_family(platform: str, model_id: str) -> dict | None:
    m = FAMILY_PATTERNS.get(platform, re.compile("$^")).match(model_id or "")
    if not m:
        return None
    return {"family": m.group("family"), "version": (int(m.group("major")), int(m.group("minor") or 0)),
            "effort": m.groupdict().get("effort")}


def newest_in_family(platform: str, family: str, models: list[dict], effort: str | None = None) -> dict | None:
    best = None
    for m in models:
        if m.get("hidden"):
            continue
        info = parse_family(platform, m["id"])
        if not info or info["family"] != family:
            continue
        if platform == "agy" and effort and info["effort"] != effort:
            continue
        if best is None or info["version"] > best[0]:
            best = (info["version"], m)
    return best[1] if best else None


def _model_ids(models: list[dict]) -> set[str]:
    ids = set()
    for m in models:
        ids.add(m["id"])
        ids.update(m.get("aliases", []))
    return ids


def check_runners(runners: dict, catalog: dict) -> list[dict]:
    """逐一檢查接力用的模型：存在嗎？同系列有沒有較新版本？推理強度支援嗎？"""
    rows = []
    for rid, r in runners.items():
        platform, model = r.get("platform"), r.get("model")
        if not platform or not model:
            continue
        row = {"runner": rid, "platform": platform, "model": model, "status": "unknown",
               "suggestion": None, "note": None}
        cat = catalog.get(platform) or {}
        if cat.get("status") != "ok":
            row["note"] = f"讀不到 {PLATFORM_LABELS.get(platform, platform)} 的模型清單：{cat.get('error')}"
            rows.append(row)
            continue
        models = cat["models"]
        info = parse_family(platform, model)
        family = r.get("family") or (info or {}).get("family")
        newest = newest_in_family(platform, family, models, (info or {}).get("effort")) if family else None
        if model in _model_ids(models):
            row["status"] = "ok"
            if newest and info and parse_family(platform, newest["id"])["version"] > info["version"]:
                row["status"], row["suggestion"] = "upgradable", newest["id"]
        else:
            row["status"] = "missing"
            row["suggestion"] = newest["id"] if newest else None
        effort = r.get("effort")
        if platform == "codex" and effort and row["status"] != "missing":
            m = next((x for x in models if x["id"] == model), None)
            if m and m.get("efforts") and effort not in m["efforts"]:
                row["note"] = f"{model} 不支援推理強度 {effort}（支援：{', '.join(m['efforts'])}）"
        rows.append(row)
    return rows


# ---------- 新模型提醒 ----------

def update_seen(catalog: dict, seen_path: Path, now=None) -> list[dict]:
    """記錄每個模型第一次出現的時間，回傳最近 NEW_MODEL_DAYS 天內新出現的模型。

    第一次執行只建立紀錄，不回報任何新模型。
    """
    now = now or now_utc()
    try:
        seen = json.loads(seen_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        seen = {}
    first_run = not seen
    fresh = []
    for platform, cat in catalog.items():
        if cat.get("status") != "ok":
            continue
        known = seen.setdefault(platform, {})
        for m in cat["models"]:
            if m["id"] not in known:
                known[m["id"]] = {"name": m.get("name"), "first_seen": now.isoformat(timespec="seconds"),
                                  "baseline": first_run}
            entry = known[m["id"]]
            first = parse_iso(entry["first_seen"])
            if not entry.get("baseline") and first and now - first < timedelta(days=NEW_MODEL_DAYS):
                fresh.append({"platform": platform, "id": m["id"], "name": m.get("name"),
                              "first_seen": entry["first_seen"]})
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
    return fresh


def apply_updates(runners_path: Path, rows: list[dict]) -> list[tuple[str, str, str]]:
    """把 upgradable／missing 且有建議的模型寫回 runners.json，回傳（代理, 舊, 新）。"""
    data = json.loads(runners_path.read_text(encoding="utf-8"))
    changes = []
    for row in rows:
        if row["status"] in ("upgradable", "missing") and row["suggestion"]:
            r = data.get("runners", {}).get(row["runner"])
            if r and r.get("model") == row["model"]:
                r["model"] = row["suggestion"]
                changes.append((row["runner"], row["model"], row["suggestion"]))
    if changes:
        runners_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changes
