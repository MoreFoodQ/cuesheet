"""免金鑰的榜單與價格資料：抓取、解析、本機快取。

每個來源獨立快取在 data/boards/<來源>.json，獨立失敗：抓不到時保留舊資料並標成 stale。
原始資料只存在本機，不進 Git；輸出時一律附出處。

統一的資料列格式（row）：
  board     榜單 id（例如 epoch:cursorbench）
  raw       來源上的原始名稱
  key       正規化後的模型鍵（例如 claude-opus-5-5、gpt-6-2-sol、gemini-4-argon）
  effort    推理強度（minimal／low／medium／high／xhigh／max），來源沒分強度時為 None
  score     分數（已換成顯示單位，見 BOARDS[board]['unit']）
  ci        信賴區間 [低, 高] 或 None
  cost      成本（定義見 BOARDS[board]['cost']）或 None
"""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
import zipfile
from datetime import timedelta
from pathlib import Path

from .common import now_iso, now_utc, parse_iso

USER_AGENT = "cuesheet (+https://github.com/MoreFoodQ/cuesheet)"
MAX_AGE = timedelta(hours=24)
EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
EFFORT_WORDS = {"extra high": "xhigh", "x-high": "xhigh", "xhigh": "xhigh", "non-reasoning": "none",
                "no reasoning": "none", "default fallback": None}

ARENA_ROWS = ("https://datasets-server.huggingface.co/rows?dataset=lmarena-ai/leaderboard-dataset"
              "&config=agent&split=latest&offset={offset}&length=100")
EPOCH_ZIP = "https://epoch.ai/data/benchmark_data.zip"
OPENROUTER_INDEX = "https://openrouter.ai/api/v1/datasets/exports/index.json"
MODELS_DEV = "https://models.dev/api.json"

# Epoch 裡依推理強度分列、涵蓋目前主力模型的基準（2026-11-18 依涵蓋度挑選）
EPOCH_BENCHMARKS = {
    "cursorbench": {"file": "cursorbench_external.csv", "score": "Score", "cost": "Cost per task",
                    "title": "CursorBench", "what": "在 Cursor 裡完成真實程式工作"},
    "scicode": {"file": "scicode_external.csv", "score": "Score", "cost": None,
                "title": "SciCode", "what": "科學計算程式"},
    "critpt": {"file": "critpt_external.csv", "score": "Accuracy", "cost": None,
               "title": "CritPt", "what": "物理研究等級的推理題"},
}

BOARDS = {
    "arena_agent": {"title": "Arena Agent", "what": "真實使用者的 Agent 任務，相對改善率",
                    "unit": "%", "cost": "每題成本中位數（USD）", "by_effort": True,
                    "attribution": "LMArena leaderboard-dataset（CC BY 4.0）"},
    "epoch_eci": {"title": "Epoch Capabilities Index", "what": "跨多個基準的綜合能力分數",
                  "unit": "ECI", "cost": None, "by_effort": False,
                  "attribution": "Epoch AI（CC BY 4.0）"},
    **{f"epoch:{k}": {"title": f"{v['title']}（Epoch 彙整）", "what": v["what"], "unit": "%",
                      "cost": "每題成本（USD）" if v["cost"] else None, "by_effort": True,
                      "attribution": "Epoch AI（CC BY 4.0），原始來源見 Epoch"}
       for k, v in EPOCH_BENCHMARKS.items()},
    "openrouter:gpqa_diamond": {"title": "GPQA Diamond（OpenRouter 實測）", "what": "研究所等級科學選擇題",
                                "unit": "%", "cost": "每題成本（USD）", "by_effort": False,
                                "attribution": "OpenRouter（CC BY 4.0）"},
}
SOURCES = ["arena", "epoch", "openrouter", "models_dev"]


# ---------- 名稱正規化 ----------

def norm_key(name: str) -> str:
    """Claude Opus 5.5 → claude-opus-5-5；GPT-6.1 Sol → gpt-6-1-sol；anthropic/claude-opus-5.5 → claude-opus-5-5。"""
    s = name.strip().lower()
    s = re.sub(r"^[a-z0-9-]+/", "", s)            # 去掉 openrouter 的廠商前綴
    s = re.sub(r"[.\s_/]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


def norm_effort(word: str | None) -> str | None:
    if not word:
        return None
    w = word.strip().lower()
    if w in EFFORT_WORDS:
        return EFFORT_WORDS[w]
    w = w.replace(" ", "")
    return w if w in EFFORTS else None


def split_name(name: str) -> tuple[str, str | None]:
    """「Claude Opus 5.5 (Max)」→（claude-opus-5-5, max）；「claude-opus-5-5_medium」→（claude-opus-5-5, medium）。"""
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", name)
    if m:
        base, inside = m.group(1), m.group(2)
        effort = None
        for part in inside.split(","):
            effort = norm_effort(part) or effort
        return norm_key(base), effort
    m = re.match(r"^(.*)_([a-z]+)$", name)
    if m and (norm_effort(m.group(2)) or m.group(2) == "unknown"):
        return norm_key(m.group(1)), norm_effort(m.group(2))
    return norm_key(name), None


# ---------- 下載 ----------

def http_get(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _num(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---------- 各來源解析 ----------

def parse_arena(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        key, effort = split_name(r.get("model_name", ""))
        score = _num(r.get("score"))
        lo, hi = _num(r.get("score_ci_lower")), _num(r.get("score_ci_upper"))
        out.append({"board": "arena_agent", "raw": r.get("model_name"), "key": key, "effort": effort,
                    "score": None if score is None else round(score * 100, 2),
                    "ci": [round(lo * 100, 2), round(hi * 100, 2)] if lo is not None and hi is not None else None,
                    "cost": _num(r.get("cost_per_task_p50"))})
    return out


def fetch_arena(get=http_get) -> dict:
    rows, offset, total, date = [], 0, None, None
    while total is None or offset < total:
        page = json.loads(get(ARENA_ROWS.format(offset=offset)).decode("utf-8"))
        total = page.get("num_rows_total", 0)
        batch = [x["row"] for x in page.get("rows", [])]
        if not batch:
            break
        rows += batch
        offset += len(batch)
    date = max((r.get("leaderboard_publish_date") or "" for r in rows), default=None)
    return {"board_date": date, "rows": parse_arena([r for r in rows if r.get("category", "overall") == "overall"])}


def parse_epoch(zip_bytes: bytes) -> list[dict]:
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    out = []
    eci = list(csv.DictReader(io.TextIOWrapper(z.open("epoch_capabilities_index/eci_scores.csv"), encoding="utf-8")))
    for r in eci:
        lo, hi = _num(r.get("eci_ci_low")), _num(r.get("eci_ci_high"))
        out.append({"board": "epoch_eci", "raw": r.get("Model"), "key": norm_key(r.get("Model", "")), "effort": None,
                    "score": _num(r.get("eci")), "ci": [lo, hi] if lo is not None and hi is not None else None,
                    "cost": None})
    for bid, spec in EPOCH_BENCHMARKS.items():
        try:
            rows = list(csv.DictReader(io.TextIOWrapper(z.open(spec["file"]), encoding="utf-8")))
        except KeyError:
            continue
        for r in rows:
            version = r.get("Model version", "")
            if "fallback" in version:
                continue
            key, effort = split_name(version)
            score = _num(r.get(spec["score"]))
            out.append({"board": f"epoch:{bid}", "raw": version, "key": key, "effort": effort,
                        "score": None if score is None else round(score * 100, 2), "ci": None,
                        "cost": _num(r.get(spec["cost"])) if spec["cost"] else None})
    return out


def fetch_epoch(get=http_get) -> dict:
    return {"board_date": None, "rows": parse_epoch(get(EPOCH_ZIP, timeout=120))}


VENDOR_PROVIDERS = {"anthropic": "Anthropic", "openai": "OpenAI", "google": "Google"}


def parse_openrouter(csv_text: str) -> list[dict]:
    """同一模型會有多個供應商的列，優先取原廠（Anthropic／OpenAI／Google）那一列。"""
    best = {}
    for r in csv.DictReader(io.StringIO(csv_text)):
        if r.get("benchmark") != "gpqa_diamond":
            continue
        slug = r.get("model_slug", "")
        vendor = slug.split("/")[0]
        own = VENDOR_PROVIDERS.get(vendor) == r.get("provider_name")
        prev = best.get(slug)
        if prev is None or (own and not prev[0]):
            best[slug] = (own, r)
    out = []
    for slug, (_own, r) in best.items():
        acc = _num(r.get("accuracy"))
        out.append({"board": "openrouter:gpqa_diamond", "raw": slug, "key": norm_key(slug), "effort": None,
                    "score": None if acc is None else round(acc * 100, 2), "ci": None,
                    "cost": _num(r.get("avg_cost_per_task_usd"))})
    return out


def fetch_openrouter(get=http_get) -> dict:
    index = json.loads(get(OPENROUTER_INDEX).decode("utf-8"))
    ds = next(d for d in index.get("datasets", []) if d.get("id") == "benchmark-leaderboard")
    manifest = json.loads(get(ds["latest_manifest_url"]).decode("utf-8"))
    text = get(manifest["files"]["csv"]["url"], timeout=120).decode("utf-8")
    return {"board_date": manifest.get("snapshot_date"), "rows": parse_openrouter(text)}


def parse_models_dev(data: dict) -> dict:
    """只留三家原廠的每百萬 token 價格：{key: {input, output, cache_read}}。"""
    prices = {}
    for vendor in ("anthropic", "openai", "google"):
        for mid, m in ((data.get(vendor) or {}).get("models") or {}).items():
            cost = m.get("cost") or {}
            if cost.get("input") is None:
                continue
            prices[norm_key(mid)] = {"vendor": vendor, "id": mid, "input": cost.get("input"),
                                     "output": cost.get("output"), "cache_read": cost.get("cache_read")}
    return prices


def fetch_models_dev(get=http_get) -> dict:
    return {"board_date": None, "prices": parse_models_dev(json.loads(get(MODELS_DEV, timeout=120).decode("utf-8")))}


FETCHERS = {"arena": fetch_arena, "epoch": fetch_epoch, "openrouter": fetch_openrouter,
            "models_dev": fetch_models_dev}
SOURCE_INFO = {
    "arena": "LMArena leaderboard-dataset（Hugging Face，CC BY 4.0）",
    "epoch": "Epoch AI benchmark_data.zip（CC BY 4.0）",
    "openrouter": "OpenRouter 每日匯出 benchmark-leaderboard（CC BY 4.0）",
    "models_dev": "models.dev api.json（MIT）",
}


# ---------- 快取 ----------

def cache_path(data_dir: Path, source: str) -> Path:
    return data_dir / "boards" / f"{source}.json"


def load_cache(data_dir: Path, source: str) -> dict | None:
    try:
        return json.loads(cache_path(data_dir, source).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_fresh(cache: dict | None, max_age: timedelta = MAX_AGE) -> bool:
    fetched = parse_iso((cache or {}).get("fetched_at"))
    return bool(cache and cache.get("status") == "ok" and fetched and now_utc() - fetched < max_age)


def refresh(data_dir: Path, sources=None, force: bool = False, fetchers=None) -> dict:
    """更新過期（或 force）的來源；失敗的來源保留舊資料並標 stale。回傳 {來源: 快取}。"""
    fetchers = fetchers or FETCHERS
    out = {}
    for source in sources or SOURCES:
        cache = load_cache(data_dir, source)
        if not force and is_fresh(cache):
            out[source] = cache
            continue
        try:
            payload = fetchers[source]()
            cache = {"source": source, "status": "ok", "fetched_at": now_iso(), "error": None,
                     "attribution": SOURCE_INFO[source], **payload}
        except Exception as exc:  # noqa: BLE001 — 單一來源失敗不影響其他來源
            if cache:
                cache = {**cache, "status": "stale", "error": str(exc)}
            else:
                cache = {"source": source, "status": "unavailable", "fetched_at": None, "error": str(exc),
                         "attribution": SOURCE_INFO[source], "rows": [], "prices": {}}
        path = cache_path(data_dir, source)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        out[source] = cache
    return out


def all_rows(caches: dict) -> list[dict]:
    return [r for c in caches.values() for r in (c.get("rows") or [])]


def prices(caches: dict) -> dict:
    return (caches.get("models_dev") or {}).get("prices") or {}
