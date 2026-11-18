"""cue rank：一個模型在各榜的位置，以及同等級或更強、更省、你能不能用的模型。

不同榜單的分數不相加、不換算。每個榜單一張表，各自標明單位與成本定義。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import boards
from .report import LEVEL_TEXT, _pad, _width

AGY_EFFORT = re.compile(r"^(.*)-(minimal|low|medium|high|xhigh|max)$")


# ---------- 名稱對照 ----------

def load_model_map(root: Path) -> dict:
    merged = {"aliases": {}, "ignore": {}}
    for name in ("model_map.json", "model_map.local.json"):
        try:
            data = json.loads((root / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for board, table in (data.get("aliases") or {}).items():
            merged["aliases"].setdefault(board, {}).update(table)
        for board, keys in (data.get("ignore") or {}).items():
            merged["ignore"].setdefault(board, []).extend(keys)
    return merged


def apply_map(rows: list[dict], mm: dict) -> list[dict]:
    out = []
    for r in rows:
        for scope in ("*", r["board"]):
            if r["key"] in (mm["ignore"].get(scope) or []):
                break
        else:
            alias = (mm["aliases"].get(r["board"]) or {}).get(r["key"]) or (mm["aliases"].get("*") or {}).get(r["key"])
            out.append({**r, "key": alias} if alias else r)
    return out


# ---------- 你能不能用 ----------

def split_cli_id(platform: str, model_id: str) -> tuple[str, str | None]:
    if platform == "agy":
        m = AGY_EFFORT.match(model_id)
        if m:
            return boards.norm_key(m.group(1)), m.group(2)
    return boards.norm_key(model_id), None


def build_availability(catalog: dict, runners: dict, agents: dict) -> dict:
    """回傳 {key: {"platforms": {平台: [強度...] 或 None}, "runners": [(代理, 強度, 額度狀態)]}}。"""
    avail: dict = {}
    for platform, cat in (catalog or {}).items():
        for m in cat.get("models", []):
            key, effort = split_cli_id(platform, m["id"])
            entry = avail.setdefault(key, {"platforms": {}, "runners": []})
            cli_base = AGY_EFFORT.match(m["id"]).group(1) if platform == "agy" and AGY_EFFORT.match(m["id"]) else m["id"]
            entry.setdefault("name", cli_base)
            efforts = entry["platforms"].setdefault(platform, set())
            if effort:
                efforts.add(effort)
            elif m.get("efforts"):
                efforts.update(m["efforts"])
            for alias in m.get("aliases", []):
                avail.setdefault(boards.norm_key(alias), entry)
    for rid, r in (runners or {}).items():
        if not r.get("model") or not r.get("platform"):
            continue
        key, effort = split_cli_id(r["platform"], r["model"])
        info = (agents or {}).get(r.get("quota_agent", rid))
        avail.setdefault(key, {"platforms": {}, "runners": []})["runners"].append(
            (rid, r.get("effort") or effort, info))
    return avail


def availability_text(key: str, effort: str | None, avail: dict) -> str:
    entry = avail.get(key)
    if not entry or not entry["platforms"]:
        return "無訂閱"
    for rid, r_effort, info in entry["runners"]:
        if effort is None or r_effort in (None, effort):
            if info:
                return f"已設定 {rid}，{LEVEL_TEXT[info['level']]}" + (
                    f" {info['remaining_percent']:.0f}%" if info.get("remaining_percent") is not None else "")
            return f"已設定 {rid}"
    platforms = []
    for p, efforts in entry["platforms"].items():
        if effort and efforts and effort not in efforts:
            continue
        platforms.append(p)
    if not platforms:
        return "平台有，但沒有這個強度"
    return f"平台有（{'、'.join(platforms)}），未設定"


def resolve_model(query: str, catalog: dict, rows: list[dict]) -> tuple[str | None, str | None, list[str]]:
    """把使用者輸入（opus、opus-5.5、gpt-6.2-sol、gemini-4-argon-medium、hydrogen）解析成（模型鍵, 強度, 候選）。"""
    effort = None
    m = AGY_EFFORT.match(query.strip().lower())
    if m:
        query, effort = m.group(1), m.group(2)
    q = boards.norm_key(query)
    known = {}
    for platform, cat in (catalog or {}).items():
        for model in cat.get("models", []):
            key, _ = split_cli_id(platform, model["id"])
            known[key] = True
            for alias in model.get("aliases", []):
                if boards.norm_key(alias) == q:
                    return key, effort, []
    board_keys = {r["key"] for r in rows}
    if q in known or q in board_keys:
        return q, effort, []
    hits = sorted(k for k in set(known) | board_keys if q in k)
    in_catalog = [k for k in hits if k in known]
    if len(in_catalog) == 1:
        return in_catalog[0], effort, []
    if len(hits) == 1:
        return hits[0], effort, []
    return None, effort, in_catalog or hits


# ---------- 排名 ----------

def pick_baseline(rows: list[dict], key: str, effort: str | None, by_effort: bool):
    mine = [r for r in rows if r["key"] == key and r["score"] is not None]
    if not mine:
        return None, "這個榜沒有這個模型"
    if not by_effort:
        return mine[0], "這個榜不分推理強度"
    exact = [r for r in mine if r["effort"] == effort]
    if exact:
        return exact[0], None
    order = boards.EFFORTS
    want = order.index(effort) if effort in order else order.index("medium")
    ranked = sorted((r for r in mine if r["effort"] in order),
                    key=lambda r: (abs(order.index(r["effort"]) - want), -order.index(r["effort"])))
    if not ranked:
        return mine[0], "強度未標"
    return ranked[0], f"沒有 {effort or 'medium'}，改用 {ranked[0]['effort']} 對照"


def rank_board(board: str, rows: list[dict], key: str, effort: str | None, avail: dict, *,
               margin: float = 3.0, show_all: bool = False, limit: int = 15) -> dict:
    spec = boards.BOARDS[board]
    rows = [r for r in rows if r["board"] == board and r["score"] is not None]
    base, note = pick_baseline(rows, key, effort, spec["by_effort"])
    result = {"board": board, "title": spec["title"], "what": spec["what"], "unit": spec["unit"],
              "cost_label": spec["cost"], "attribution": spec["attribution"], "note": note,
              "baseline": base, "rows": []}
    if base is None:
        return result
    floor = base["score"] - margin
    picked = []
    for r in rows:
        is_base = r is base
        if not is_base and r["score"] < floor:
            continue
        if not show_all and not is_base and r["key"] not in avail:
            continue
        picked.append({**r, "is_base": is_base, "name": (avail.get(r["key"]) or {}).get("name") or r["key"],
                       "mark": "★" if is_base else ("↑" if r["score"] > base["score"] else "≈"),
                       "available": availability_text(r["key"], r["effort"], avail),
                       "cheaper": (r["cost"] is not None and base["cost"] is not None and r["cost"] < base["cost"]
                                   and not is_base)})
    picked.sort(key=lambda r: -r["score"])
    if len(picked) > limit:
        keep = picked[:limit]
        if not any(r["is_base"] for r in keep):
            keep[-1] = next(r for r in picked if r["is_base"])
        picked = keep
    result["rows"] = picked
    return result


def summarize(results: list[dict]) -> list[dict]:
    """哪些模型在多個榜上「同等或更強，而且更省」。只數有成本的榜。"""
    tally: dict = {}
    for res in results:
        for r in res["rows"]:
            if r["is_base"] or not r["cheaper"]:
                continue
            name = r["name"] + (f" ({r['effort']})" if r["effort"] else "")
            t = tally.setdefault(name, {"model": name, "boards": [], "available": r["available"]})
            t["boards"].append(res["title"])
    return sorted(tally.values(), key=lambda t: -len(t["boards"]))


def fmt_score(r: dict, unit: str) -> str:
    s = f"{r['score']:.1f}" + ("%" if unit == "%" else "")
    if r.get("ci"):
        s += f" ({r['ci'][0]:.1f}–{r['ci'][1]:.1f})"
    return s


def fmt_cost(r: dict, base: dict) -> str:
    if r["cost"] is None:
        return "—"
    s = f"${r['cost']:.2f}"
    if not r["is_base"] and base.get("cost"):
        diff = (r["cost"] - base["cost"]) / base["cost"] * 100
        s += f" ({diff:+.0f}%)"
    return s


def render(query_label: str, results: list[dict], summary: list[dict], prices: dict, caches: dict,
           margin: float) -> str:
    lines = [f"CueSheet rank　基準：{query_label}", f"列出範圍：分數不低於基準 {margin:g} 以內，加上所有更高分的；預設只列你訂閱裡有的模型（--all 列全部）", ""]
    keys_seen, names = [], {}
    for res in results:
        lines.append(f"■ {res['title']}　{res['what']}")
        if res["baseline"] is None:
            lines.append(f"  {res['note']}")
            lines.append("")
            continue
        unit_note = "分數單位：" + ("百分比" if res["unit"] == "%" else res["unit"])
        cost_note = f"；成本：{res['cost_label']}" if res["cost_label"] else "；沒有成本資料"
        lines.append(f"  {unit_note}{cost_note}" + (f"　⚠ {res['note']}" if res["note"] else ""))
        table = [("", "模型（強度）", "分數", "成本（與基準比）", "你能不能用")]
        for r in res["rows"]:
            name = r["name"] + (f" ({r['effort']})" if r["effort"] else "")
            if r["key"] not in keys_seen:
                keys_seen.append(r["key"])
                names[r["key"]] = r["name"]
            table.append((r["mark"], name, fmt_score(r, res["unit"]), fmt_cost(r, res["baseline"]), r["available"]))
        widths = [max(_width(row[i]) for row in table) for i in range(4)]
        for row in table:
            lines.append("  " + "  ".join(_pad(row[i], widths[i]) for i in range(4)) + "  " + row[4])
        lines.append(f"  出處：{res['attribution']}")
        lines.append("")

    if summary:
        lines.append("■ 同等或更強、而且更省（依榜單上的每題成本，不是訂閱額度）")
        for t in summary[:8]:
            lines.append(f"  · {t['model']}：{len(t['boards'])} 個榜（{'、'.join(t['boards'])}）｜{t['available']}")
        lines.append("")

    price_rows = [(k, prices[k]) for k in keys_seen if k in prices]
    if price_rows:
        lines.append("■ API 牌價（每百萬 token，USD；不等於訂閱額度）")
        for k, p in price_rows:
            lines.append(f"  {names.get(k, k)}：輸入 ${p['input']}，輸出 ${p['output']}")
        lines.append("  出處：models.dev（MIT）")
        lines.append("")

    lines.append("資料日期：" + "；".join(
        f"{s} {c.get('board_date') or (c.get('fetched_at') or '')[:10]}" + ("（舊資料）" if c.get("status") == "stale" else "")
        for s, c in caches.items() if c.get("status") in ("ok", "stale")))
    lines.append("★ 基準　↑ 分數更高　≈ 分數相近（在範圍內但略低）")
    return "\n".join(lines).rstrip() + "\n"
