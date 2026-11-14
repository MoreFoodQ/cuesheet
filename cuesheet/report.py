"""把各端額度整理成「派工參考」，並輸出成人看的文字表。"""
from __future__ import annotations

import unicodedata
from datetime import datetime

from .common import now_utc, parse_iso

LEVEL_TEXT = {"ok": "正常", "low": "偏低", "exhausted": "用完", "unknown": "未知"}
STATUS_TEXT = {"ok": "即時", "stale": "舊快照", "unavailable": "無法取得"}


def evaluate_agents(providers: dict, agents_cfg: dict) -> list[dict]:
    th = agents_cfg.get("thresholds", {})
    low, exhausted = th.get("low_percent", 20), th.get("exhausted_percent", 1)
    out = []
    for agent in agents_cfg.get("agents", []):
        prov = providers.get(agent["provider"]) or {}
        by_id = {b["id"]: b for b in prov.get("buckets", [])}
        row = {
            "id": agent["id"], "label": agent["label"], "provider": agent["provider"],
            "models": agent.get("models", []), "remaining_percent": None, "limiting_bucket": None,
            "resets_at": None, "level": "unknown", "data_status": prov.get("status", "unavailable"),
            "notes": list(filter(None, [agent.get("note")])),
        }
        if not prov:
            row["notes"].append(f"這次沒有查 {agent['provider']} 這一端")
            out.append(row)
            continue
        if prov.get("status") not in ("ok", "stale"):
            row["notes"].append(f"{prov['label']} 無法取得：{prov.get('error')}")
            out.append(row)
            continue
        known = []
        optional = set(agent.get("optional_buckets", []))
        for bid in agent.get("buckets", []) + sorted(optional):
            b = by_id.get(bid)
            if b is None:
                if bid not in optional:
                    row["notes"].append(f"來源沒有額度桶 {bid}")
            elif b["remaining_percent"] is None:
                row["notes"].append(f"{b['label']} 目前數值未知")
            else:
                known.append(b)
        if known:
            worst = min(known, key=lambda b: b["remaining_percent"])
            row["remaining_percent"] = worst["remaining_percent"]
            row["limiting_bucket"] = worst["id"]
            row["limiting_label"] = worst["label"]
            row["resets_at"] = worst["resets_at"]
            r = worst["remaining_percent"]
            row["level"] = "exhausted" if r <= exhausted else "low" if r < low else "ok"
        out.append(row)
    return out


def unmapped_buckets(providers: dict, agents_cfg: dict) -> list[dict]:
    """來源有回傳、但 agents.json 沒有任何代理在看的額度（通常是新模型的獨立額度）。"""
    used = {b for a in agents_cfg.get("agents", []) for b in a.get("buckets", []) + a.get("optional_buckets", [])}
    return [{"provider": name, "id": b["id"], "label": b["label"], "remaining_percent": b["remaining_percent"]}
            for name, p in providers.items() if p.get("status") in ("ok", "stale")
            for b in p.get("buckets", []) if b["id"] not in used]


RUNNER_STATUS = {"ok": "正常", "upgradable": "可升級", "missing": "不存在", "unknown": "未知"}


def attention_items(report: dict) -> list[str]:
    items = []
    for b in report.get("unmapped_buckets", []):
        items.append(f"{b['label']}（{b['provider']}，剩 {fmt_pct(b['remaining_percent'])}）沒有代理在看。"
                     "要派工給它，請在 agents.json 加一個代理。")
    models = report.get("models") or {}
    for m in models.get("new", []):
        items.append(f"新模型：{m['name']}（{m['id']}，{m['platform']}）")
    for r in models.get("runners", []):
        if r["status"] == "missing":
            hint = f"同系列最新是 {r['suggestion']}" if r["suggestion"] else "找不到同系列的模型"
            items.append(f"接力代理 {r['runner']} 的模型 {r['model']} 已不存在，{hint}。執行 cue models --update 更新。")
        elif r["status"] == "upgradable":
            items.append(f"接力代理 {r['runner']} 有同系列較新版本：{r['model']} → {r['suggestion']}。"
                         "執行 cue models --update 更新。")
        if r.get("note"):
            items.append(f"接力代理 {r['runner']}：{r['note']}")
    for platform, cat in (models.get("catalog") or {}).items():
        if cat.get("status") != "ok":
            items.append(f"讀不到 {platform} 的模型清單：{cat.get('error')}")
    return items


# ---------- 文字輸出 ----------

def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def fmt_time(iso: str | None) -> str:
    dt = parse_iso(iso)
    if not dt:
        return "—"
    local = dt.astimezone()
    delta = dt - now_utc()
    secs = int(delta.total_seconds())
    if secs <= 0:
        rel = "已過"
    else:
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        rel = f"{d} 天 {h} 小時後" if d else f"{h} 小時 {m} 分後" if h else f"{m} 分後"
    return f"{local:%m/%d %H:%M}（{rel}）"


def fmt_pct(x) -> str:
    return "—" if x is None else f"{x:.0f}%"


def render(report: dict) -> str:
    gen = parse_iso(report["generated_at"])
    lines = [f"CueSheet　{gen.astimezone():%Y-%m-%d %H:%M}（本機時間）", ""]

    lines.append("■ 派工參考（剩餘＝該代理相關額度裡最少的那一個）")
    rows = [("狀態", "代理", "剩餘", "卡在", "重置")]
    for a in report["agents"]:
        rows.append((f"[{LEVEL_TEXT[a['level']]}]", a["label"], fmt_pct(a["remaining_percent"]),
                     a.get("limiting_label") or "—", fmt_time(a["resets_at"])))
    widths = [max(_width(r[i]) for r in rows) for i in range(4)]
    for r in rows:
        lines.append("  " + "  ".join(_pad(r[i], widths[i]) for i in range(4)) + "  " + r[4])
    for a in report["agents"]:
        for n in a["notes"]:
            lines.append(f"    · {a['label']}：{n}")
    lines.append("")

    for p in report["providers"].values():
        head = f"■ {p['label']}　[{STATUS_TEXT[p['status']]}]"
        if p.get("plan"):
            head += f"　方案：{p['plan']}"
        lines.append(head)
        lines.append(f"  來源：{p['source']}")
        if p.get("error"):
            lines.append(f"  說明：{p['error']}")
        if p["buckets"]:
            w = max(_width(b["label"]) for b in p["buckets"])
            for b in p["buckets"]:
                line = (f"  {_pad(b['label'], w)}  已用 {_pad(fmt_pct(b['used_percent']), 4)}"
                        f"  剩 {_pad(fmt_pct(b['remaining_percent']), 4)}  重置 {fmt_time(b['resets_at'])}")
                if b.get("note"):
                    line += f"　※{b['note']}"
                lines.append(line)
        for n in p.get("notes", []):
            lines.append(f"  · {n}")
        lines.append("")

    items = attention_items(report)
    if items:
        lines.append("■ 需要注意")
        lines.extend(f"  · {i}" for i in items)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
