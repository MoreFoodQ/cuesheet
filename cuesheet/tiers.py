"""等級：依 Artificial Analysis 指數把「模型＋推理強度」分成 L1～L5。

- 設定用：固定區間（tiers.json），才不會出現 A、B 同級、B、C 同級，A、C 卻差很多的情況。
- 沒有 AA 分數的模型標「未分級」，除非 tiers.json 或 tiers.local.json 的 overrides 人工指定。
- 缺某個推理強度時不內插，因為各強度的分數不是線性的。
"""
from __future__ import annotations

import json
from pathlib import Path

UNRANKED = "未分級"


def load(root: Path) -> dict:
    data = json.loads((root / "tiers.json").read_text(encoding="utf-8"))
    try:
        local = json.loads((root / "tiers.local.json").read_text(encoding="utf-8"))
        data["overrides"] = {**data.get("overrides", {}), **local.get("overrides", {})}
        if local.get("tiers"):
            data["tiers"] = local["tiers"]
    except (OSError, json.JSONDecodeError):
        pass
    data["tiers"] = sorted(data["tiers"], key=lambda t: -t["min"])
    return data


def tier_of(score: float | None, cfg: dict, key: str | None = None, effort: str | None = None) -> str:
    override = (cfg.get("overrides") or {}).get(f"{key}:{effort}") or (cfg.get("overrides") or {}).get(key or "")
    if override:
        return override
    if score is None:
        return UNRANKED
    for t in cfg["tiers"]:
        if score >= t["min"]:
            return t["id"]
    return UNRANKED


def label(tier_id: str, cfg: dict) -> str:
    t = next((t for t in cfg["tiers"] if t["id"] == tier_id), None)
    return f"{tier_id} {t['label']}" if t else tier_id


def group(rows: list[dict], cfg: dict, avail: dict, show_all: bool = False) -> dict:
    """把 AA 的資料列依等級分組，只留使用者平台上有的模型（show_all 時全部）。"""
    board = cfg.get("board", "aa_intelligence")
    out: dict = {t["id"]: [] for t in cfg["tiers"]}
    out[UNRANKED] = []
    for r in rows:
        if r["board"] != board or r.get("dropped"):
            continue
        if not show_all and r["key"] not in avail:
            continue
        out.setdefault(tier_of(r["score"], cfg, r["key"], r["effort"]), []).append(r)
    for rows_ in out.values():
        rows_.sort(key=lambda r: -(r["score"] or 0))
    return out
