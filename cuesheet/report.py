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
    return "\n".join(lines).rstrip() + "\n"
