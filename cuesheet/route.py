"""依任務難度與等級挑接力的模型，以及訂閱額度的真實消耗統計。

只有任務寫了 difficulty、min_tier 或 max_tier 才會自動挑；沒寫的任務照固定順序，行為不變。

- hard：等級不低於基準（預設接力的第一位），分數高的先上。
- normal：等級不低於基準。候選都有足夠的乾淨樣本時，挑「估計還能跑最多次」的；不夠時照固定順序。
- easy：從最弱的往上派，把高階模型的額度留給難的工作。
- 排除：AA 沒有分數（且沒有人工指定等級）的模型、做不到任務需求（requires_shell）的平台。
- 額度用完的代理由接力本身跳過（relay.plan_order）。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import tiers as tiers_mod
from .rank import split_cli_id

DIFFICULTIES = ("hard", "normal", "easy")
SAMPLE_MIN = 5
# 無人模式下能執行 shell 指令的平台：Claude Code（acceptEdits）與 agy 需要核准的工具會被拒絕
SHELL_PLATFORMS = {"codex"}


def wants_auto(task: dict) -> bool:
    return bool(task.get("difficulty") or task.get("min_tier") or task.get("max_tier"))


# ---------- 額度消耗樣本 ----------

def load_samples(path: Path) -> list[dict]:
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def usage_stats(samples: list[dict]) -> dict:
    """依（任務, 代理, 模型, 強度）分組。只用 clean 樣本算平均：總消耗 ÷ 次數。"""
    stats: dict = {}
    for s in samples:
        key = (s.get("task"), s.get("agent"), s.get("model"), s.get("effort"))
        st = stats.setdefault(key, {"task": key[0], "agent": key[1], "model": key[2], "effort": key[3],
                                    "total": 0, "clean": 0, "by_status": {}, "sum": {}, "quota_agent": s.get("quota_agent")})
        st["total"] += 1
        st["by_status"][s.get("status")] = st["by_status"].get(s.get("status"), 0) + 1
        if s.get("status") == "clean":
            st["clean"] += 1
            for bid, d in (s.get("delta") or {}).items():
                st["sum"][bid] = st["sum"].get(bid, 0) + (d or 0)
    for st in stats.values():
        st["avg"] = {bid: round(v / st["clean"], 2) for bid, v in st["sum"].items()} if st["clean"] else {}
        st["enough"] = st["clean"] >= SAMPLE_MIN
    return stats


def runs_left(stat: dict | None, info: dict | None) -> float | None:
    """估計還能跑幾次：剩餘 % ÷ 每次最多吃掉的 %。樣本不足或讀不到額度時回傳 None；每次幾乎不耗額度時回傳 inf。"""
    if not stat or not stat["enough"] or not info or info.get("remaining_percent") is None:
        return None
    worst = max(stat["avg"].values(), default=0)
    if worst <= 0:
        return float("inf")
    return info["remaining_percent"] / worst


def stat_for(stats: dict, task: str, rid: str, runner: dict) -> dict | None:
    return stats.get((task, rid, runner.get("model"), runner.get("effort")))


# ---------- 等級 ----------

def runner_tier(runner: dict, rows: list[dict], tier_cfg: dict) -> dict:
    """runner 的模型與強度 → {"tier", "score", "cost", "note"}。"""
    if not runner.get("platform") or not runner.get("model"):
        return {"tier": tiers_mod.UNRANKED, "score": None, "cost": None, "note": "自訂指令，無法分級"}
    key, id_effort = split_cli_id(runner["platform"], runner["model"])
    effort, note = runner.get("effort") or id_effort, None
    if not effort:
        effort, note = "medium", "沒有指定強度，以 medium 計"
    board = tier_cfg.get("board", "aa_intelligence")
    mine = [r for r in rows if r["board"] == board and r["key"] == key and r["effort"] == effort]
    mine.sort(key=lambda r: (bool(r.get("dropped")), r["score"] is None))
    row = mine[0] if mine else None
    score = row["score"] if row else None
    tier = tiers_mod.tier_of(score, tier_cfg, key, effort)
    if row and row.get("dropped"):
        note = "；".join(filter(None, [note, "已從 AA 下榜，用最後一次的分數"]))
    return {"tier": tier, "score": score, "cost": row["cost"] if row else None, "note": note}


def tier_index(tier: str, tier_cfg: dict) -> int | None:
    ids = [t["id"] for t in tier_cfg["tiers"]]  # 由強到弱
    return ids.index(tier) if tier in ids else None


# ---------- 挑選 ----------

def auto_order(task: dict, runners: dict, base_order: list[str], rows: list[dict], tier_cfg: dict,
               stats: dict, agents: dict, unavailable: dict | None = None) -> dict:
    """回傳 {"order": [...], "lines": [說明], "fallback": bool}。

    unavailable：{代理: 原因}，例如選用代理的模型不在使用者帳號裡。"""
    unavailable = unavailable or {}
    difficulty = task.get("difficulty") or "normal"
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"difficulty 只能是 {'、'.join(DIFFICULTIES)}")
    info = {rid: runner_tier(r, rows, tier_cfg) for rid, r in runners.items()}
    lines = []

    baseline = task.get("baseline") or (base_order[0] if base_order else None)
    base_tier = info.get(baseline, {}).get("tier")
    min_tier, max_tier = task.get("min_tier"), task.get("max_tier")
    if not min_tier and difficulty in ("hard", "normal"):
        if tier_index(base_tier or "", tier_cfg) is None:
            return {"order": list(base_order), "fallback": True,
                    "lines": [f"基準 {baseline} 沒有等級（{base_tier or '找不到'}），改用固定順序"]}
        min_tier = base_tier
    for t in (min_tier, max_tier):
        if t and tier_index(t, tier_cfg) is None:
            raise ValueError(f"不認得的等級 {t}（tiers.json 有：{'、'.join(x['id'] for x in tier_cfg['tiers'])}）")
    lo = tier_index(min_tier, tier_cfg) if min_tier else len(tier_cfg["tiers"]) - 1
    hi = tier_index(max_tier, tier_cfg) if max_tier else 0
    label = {"hard": "難", "normal": "一般", "easy": "簡單"}[difficulty]
    rng = f"{tiers_mod.label(tier_cfg['tiers'][hi]['id'], tier_cfg)} ～ {tiers_mod.label(tier_cfg['tiers'][lo]['id'], tier_cfg)}"
    lines.append(f"難度：{label}　基準 {baseline}（{base_tier}）　可用等級 {rng}")

    picked, excluded = [], []
    for rid, r in runners.items():
        ti = info[rid]
        idx = tier_index(ti["tier"], tier_cfg)
        if rid in unavailable:
            excluded.append((rid, unavailable[rid]))
        elif idx is None:
            excluded.append((rid, f"未分級（{ti['note'] or 'AA 沒有這個模型與強度的分數'}）"))
        elif not hi <= idx <= lo:
            excluded.append((rid, f"{ti['tier']} 不在範圍內"))
        elif task.get("requires_shell") and r.get("platform") not in SHELL_PLATFORMS:
            excluded.append((rid, "任務需要 shell，這個平台在無人模式下做不到"))
        else:
            picked.append(rid)

    st = {rid: stat_for(stats, task["name"], rid, runners[rid]) for rid in picked}
    left = {rid: runs_left(st[rid], agents.get(runners[rid].get("quota_agent", rid))) for rid in picked}
    fixed = {rid: (base_order.index(rid) if rid in base_order else len(base_order)) for rid in picked}
    score = {rid: info[rid]["score"] if info[rid]["score"] is not None else -1 for rid in picked}
    cost = {rid: info[rid]["cost"] if info[rid]["cost"] is not None else float("inf") for rid in picked}
    fallback = False
    if difficulty == "hard":
        picked.sort(key=lambda rid: (-score[rid], cost[rid]))
        how = "分數高的先上"
    elif difficulty == "easy":
        picked.sort(key=lambda rid: (score[rid], cost[rid]))
        how = "最弱但夠用的先上"
    elif picked and all(left[rid] is not None for rid in picked):
        picked.sort(key=lambda rid: (-left[rid], fixed[rid]))
        how = "依乾淨樣本估計還能跑最多次的先上"
    else:
        picked.sort(key=lambda rid: (fixed[rid], cost[rid]))
        how = f"乾淨樣本不足（每個至少 {SAMPLE_MIN} 筆），照固定順序"
        fallback = True
    lines.append(f"排序：{how}")
    for rid in picked:
        ti, s = info[rid], st[rid]
        r = runners[rid]
        sample = ("沒有樣本" if not s else
                  f"樣本 {s['clean']}/{SAMPLE_MIN}" if not s["enough"] else
                  "估計還能跑很多次" if left[rid] == float("inf") else
                  f"估計還能跑 {left[rid]:.0f} 次" if left[rid] is not None else f"樣本 {s['clean']} 筆，讀不到額度")
        aa = f"AA {ti['score']:.1f}" if ti["score"] is not None else "人工指定"
        model = " ".join(filter(None, [r.get("model"), r.get("effort")]))
        lines.append(f"  ✓ {rid}　{model}　{ti['tier']}（{aa}）　{sample}"
                     + (f"　※{ti['note']}" if ti["note"] else ""))
    for rid, why in excluded:
        lines.append(f"  ✗ {rid}　{why}")
    return {"order": picked, "lines": lines, "fallback": fallback}


def render_stats(stats: dict, agents: dict, runners: dict, task: str | None = None) -> str:
    rows = [s for s in stats.values() if task is None or s["task"] == task]
    if not rows:
        return "還沒有額度消耗樣本。接力每跑一次會記一筆（data/relay/usage_samples.jsonl）。"
    out = [f"額度消耗（只算乾淨樣本；至少 {SAMPLE_MIN} 筆才拿來自動挑選）"]
    rows.sort(key=lambda s: (s["task"] or "", s["agent"] or ""))
    for s in rows:
        avg = "、".join(f"{b} {v:g}%" for b, v in s["avg"].items()) or "—"
        info = agents.get((runners.get(s["agent"]) or {}).get("quota_agent", s["agent"])) or agents.get(s["quota_agent"])
        left = runs_left(s, info)
        left_text = ("樣本不足" if not s["enough"] else "很多次" if left == float("inf")
                     else f"{left:.0f} 次" if left is not None else "讀不到額度")
        others = "、".join(f"{k} {v}" for k, v in s["by_status"].items() if k != "clean")
        out.append(f"  {s['task']}　{s['agent']}（{' '.join(filter(None, [s['model'], s['effort']]))}）")
        out.append(f"      乾淨 {s['clean']}／共 {s['total']} 筆" + (f"（排除：{others}）" if others else "")
                   + f"　平均每次 {avg}　估計還能跑 {left_text}")
    return "\n".join(out)
