"""CueSheet：本機 AI 代理額度監控 CLI。

用法：
  python cue.py                  查三端、印出表格、寫入 data/latest.json
  python cue.py --json           改印 JSON
  python cue.py --only codex     只查某幾端（claude,codex,gemini）
  python cue.py --max-age 300    latest.json 未滿 300 秒就直接沿用
  python cue.py --agent luna     只看一個代理；結束碼 0 正常／1 偏低／2 用完／3 未知
  python cue.py relay <任務>     依額度接力執行 relay.json 裡的任務（見 cuesheet/relay.py）
  python cue.py models           列出三家目前的模型，檢查 runners.json 的接力模型
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from cuesheet import __version__, claude, codex, gemini, models, usage, versions  # noqa: E402
from cuesheet.common import now_iso, now_utc, parse_iso  # noqa: E402
from cuesheet.report import (LEVEL_TEXT, RUNNER_STATUS, VERSION_STATUS, _pad, _width,  # noqa: E402
                             evaluate_agents, fmt_pct, fmt_time, render, unmapped_buckets)

PROVIDERS = {"claude": claude, "codex": codex, "gemini": gemini}
EXIT_CODES = {"ok": 0, "low": 1, "exhausted": 2, "unknown": 3}
SCHEMA_VERSION = 1  # 只新增欄位，舊欄位不變
SEEN_PATH = ROOT / "data" / "models_seen.json"


def load_json(path: Path, default):
    try:
        # utf-8-sig：記事本存檔可能帶 BOM
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def collect(names: list[str], cfg: dict, timeout: float) -> dict:
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = {n: pool.submit(PROVIDERS[n].collect, cfg, timeout) for n in names}
        return {n: f.result() for n, f in futures.items()}


def model_check(cfg: dict, timeout: float) -> dict:
    """即時抓三家模型清單與 CLI 版本，檢查 runners.json，並記錄新模型。"""
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_versions = pool.submit(versions.check_versions, cfg)
        catalog = models.collect_models(cfg, timeout)
        cli_versions = fut_versions.result()
    runners_cfg = load_json(ROOT / "runners.json", {})
    return {
        "catalog": catalog,
        "runners": models.check_runners(runners_cfg.get("runners", {}), catalog),
        "new": models.update_seen(catalog, SEEN_PATH),
        "versions": cli_versions,
    }


def run_models(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="cue models", description="列出三家目前的模型，檢查接力用的模型（runners.json）")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    ap.add_argument("--update", action="store_true", help="把同系列較新的版本寫回 runners.json")
    ap.add_argument("--timeout", type=float, default=90)
    args = ap.parse_args(argv)

    check = model_check(load_json(ROOT / "config.json", {}), args.timeout)
    if args.json:
        print(json.dumps(check, ensure_ascii=True, indent=2))
        return 0

    used = {r["model"] for r in check["runners"]}
    new_ids = {m["id"] for m in check["new"]}
    print("■ CLI 版本")
    for v in check["versions"]["clis"]:
        state = VERSION_STATUS[v["status"]]
        latest = f"，最新 {v['latest']}（{v['channel']}）" if v.get("latest") else ""
        print(f"  [{state}] {v['label']}　已安裝 {v['installed'] or '—'}{latest}")
        if v["status"] == "outdated":
            print(f"      更新：{v['update']}")
        if v.get("note"):
            print(f"      {v['note']}")
    print()
    print("■ 接力模型（runners.json）")
    rows = [("狀態", "代理", "平台", "模型", "說明")]
    for r in check["runners"]:
        note = ""
        if r["status"] == "upgradable":
            note = f"同系列較新：{r['suggestion']}"
        elif r["status"] == "missing":
            note = f"平台上找不到；同系列最新：{r['suggestion'] or '無'}"
        rows.append((f"[{RUNNER_STATUS[r['status']]}]", r["runner"], r["platform"], r["model"],
                     " ".join(filter(None, [note, r.get("note")]))))
    widths = [max(_width(row[i]) for row in rows) for i in range(4)]
    for row in rows:
        print("  " + "  ".join(_pad(row[i], widths[i]) for i in range(4)) + "  " + row[4])
    print()
    for platform, cat in check["catalog"].items():
        print(f"■ {models.PLATFORM_LABELS[platform]}（{len(cat['models'])} 個模型）")
        if cat["status"] != "ok":
            print(f"  讀不到：{cat['error']}")
        for m in cat["models"]:
            mark = "★" if m["id"] in used else ("新" if m["id"] in new_ids else " ")
            extra = "（隱藏）" if m.get("hidden") else ""
            print(f"  {mark} {m['id']}　{m['name']}{extra}")
        print()
    print("★＝接力在用　新＝最近 7 天第一次出現")

    if args.update:
        changes = models.apply_updates(ROOT / "runners.json", check["runners"])
        for rid, old, new in changes:
            print(f"已更新 runners.json：{rid}　{old} → {new}")
        if not changes:
            print("runners.json 不需要更新。")
    return 0


def run_boards(argv: list[str]) -> int:
    from cuesheet import boards

    ap = argparse.ArgumentParser(prog="cue boards", description="更新與查看免金鑰的榜單資料（快取在 data/boards/）")
    ap.add_argument("--refresh", action="store_true", help="不管快取多新都重抓")
    args = ap.parse_args(argv)
    caches = boards.refresh(ROOT / "data", force=args.refresh, cfg=load_json(ROOT / "config.json", {}))
    status_text = {"ok": "正常", "stale": "舊資料", "unavailable": "無法取得", "disabled": "未啟用"}
    for source, c in caches.items():
        count = len(c.get("rows") or c.get("prices") or [])
        date = c.get("board_date") or "—"
        fetched = parse_iso(c.get("fetched_at"))
        fetched_text = f"{fetched.astimezone():%m/%d %H:%M}" if fetched else "—"
        print(f"[{status_text[c['status']]}] {source}　{count} 筆　榜單日期 {date}　抓取 {fetched_text}")
        print(f"    出處：{c['attribution']}")
        if c.get("error"):
            print(f"    錯誤：{c['error']}")
    return 0 if all(c["status"] != "unavailable" for c in caches.values()) else 1


def board_context(refresh: bool) -> dict:
    """cue rank 與 cue tiers 共用：榜單資料、三家模型清單、目前額度、可用性。"""
    from cuesheet import boards, rank

    cfg = load_json(ROOT / "config.json", {})
    agents_cfg = load_json(ROOT / "agents.json", {"agents": []})
    runners_cfg = load_json(ROOT / "runners.json", {}).get("runners", {})

    def current_agents():
        cached = load_json(ROOT / "data" / "latest.json", None)
        gen = parse_iso((cached or {}).get("generated_at"))
        if gen and now_utc() - gen < timedelta(minutes=60):
            return {a["id"]: a for a in cached.get("agents", [])}
        providers = collect(list(PROVIDERS), cfg, 90)
        return {a["id"]: a for a in evaluate_agents(providers, agents_cfg)}

    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_caches = pool.submit(boards.refresh, ROOT / "data", None, refresh, None, cfg)
        fut_catalog = pool.submit(models.collect_models, cfg, 90)
        fut_agents = pool.submit(current_agents)
        caches, catalog, agents = fut_caches.result(), fut_catalog.result(), fut_agents.result()
    rows = rank.apply_map(boards.all_rows(caches), rank.load_model_map(ROOT))
    return {"cfg": cfg, "runners": runners_cfg, "caches": caches, "catalog": catalog, "agents": agents,
            "rows": rows, "avail": rank.build_availability(catalog, runners_cfg, agents)}


def aa_status_line(caches: dict) -> str | None:
    aa = caches.get("aa") or {}
    if aa.get("status") == "disabled":
        return "※ 沒有 Artificial Analysis 資料：在 config.json 設定 aa_api_key（免費金鑰）後才有等級與 AA 分數。"
    if aa.get("status") in ("stale", "unavailable"):
        return f"※ Artificial Analysis 這次沒更新成功：{aa.get('error')}"
    return None


def run_tiers(argv: list[str]) -> int:
    from cuesheet import tiers

    ap = argparse.ArgumentParser(prog="cue tiers", description="依 AA 指數把你能用的模型分成 L1～L5")
    ap.add_argument("--all", action="store_true", help="也列出你訂閱裡沒有的模型")
    ap.add_argument("--refresh", action="store_true", help="重抓榜單資料")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    args = ap.parse_args(argv)

    ctx = board_context(args.refresh)
    tier_cfg = tiers.load(ROOT)
    groups = tiers.group(ctx["rows"], tier_cfg, ctx["avail"], show_all=args.all)
    if args.json:
        print(json.dumps({"tiers": tier_cfg["tiers"], "groups": groups}, ensure_ascii=True, indent=2))
        return 0
    from cuesheet import rank
    note = aa_status_line(ctx["caches"])
    if note:
        print(note)
        if ctx["caches"]["aa"].get("status") == "disabled":
            return 3
    print(f"CueSheet tiers　依 Artificial Analysis Intelligence Index（v{tier_cfg.get('index_version')}）分級"
          + ("" if args.all else "，只列你訂閱裡有的模型（--all 列全部）"))
    print()
    for t in tier_cfg["tiers"] + [{"id": tiers.UNRANKED, "min": None, "label": ""}]:
        rows = groups.get(t["id"]) or []
        if not rows:
            continue
        if t["id"] == tiers.UNRANKED:
            names = sorted({(ctx["avail"].get(r["key"]) or {}).get("name") or r["key"] for r in rows})
            print(f"■ 未分級（AA 沒有分數，共 {len(rows)} 個模型＋強度；要分級請在 tiers.local.json 的 overrides 指定）")
            print("  " + "、".join(names))
            print()
            continue
        head = f"■ {tiers.label(t['id'], tier_cfg)}" + (f"（AA {t['min']} 以上）" if t.get("min") is not None else "")
        print(head)
        table = [("模型（強度）", "AA 指數", "每題成本", "你能不能用")]
        for r in rows:
            name = ((ctx["avail"].get(r["key"]) or {}).get("name") or r["key"]) + (f" ({r['effort']})" if r["effort"] else "")
            table.append((name, f"{r['score']:.1f}" if r["score"] is not None else "—",
                          f"${r['cost']:.2f}" if r["cost"] is not None else "—",
                          rank.availability_text(r["key"], r["effort"], ctx["avail"])))
        widths = [max(_width(row[i]) for row in table) for i in range(3)]
        for row in table:
            print("  " + "  ".join(_pad(row[i], widths[i]) for i in range(3)) + "  " + row[3])
        print()
    print("資料來源：Artificial Analysis（artificialanalysis.ai）。等級界線在 tiers.json，可以調整。")
    return 0


def run_rank(argv: list[str]) -> int:
    from cuesheet import boards, rank, tiers

    ap = argparse.ArgumentParser(prog="cue rank", description="一個模型在各榜的位置，以及同等級或更強、更省的模型")
    ap.add_argument("model", help="模型，例如 opus、claude-opus-5-5、gpt-6.2-sol、gemini-4-argon-medium、hydrogen")
    ap.add_argument("--effort", help="推理強度（minimal/low/medium/high/xhigh/max）；不寫就用接力設定或 medium")
    ap.add_argument("--margin", type=float, default=3.0, help="比基準低多少以內算相近（預設 3）")
    ap.add_argument("--limit", type=int, default=15, help="每個榜最多列幾個，超過時留離基準最近的（預設 15）")
    ap.add_argument("--all", action="store_true", help="也列出你訂閱裡沒有的模型")
    ap.add_argument("--refresh", action="store_true", help="重抓榜單資料")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    args = ap.parse_args(argv)

    ctx = board_context(args.refresh)
    caches, catalog, rows, runners_cfg = ctx["caches"], ctx["catalog"], ctx["rows"], ctx["runners"]
    tier_cfg = tiers.load(ROOT)
    key, embedded_effort, candidates = rank.resolve_model(args.model, catalog, rows)
    if key is None:
        print(f"找不到「{args.model}」。" + (f"你是指：{'、'.join(candidates[:10])}？" if candidates else ""))
        return 3
    runner_effort = next((r.get("effort") for r in runners_cfg.values()
                          if r.get("model") and rank.split_cli_id(r.get("platform", ""), r["model"])[0] == key), None)
    effort = args.effort or embedded_effort or runner_effort or "medium"
    avail = ctx["avail"]
    results = [rank.rank_board(b, rows, key, effort, avail, margin=args.margin, show_all=args.all, limit=args.limit, tier_cfg=tier_cfg)
               for b in boards.BOARDS]
    summary = rank.summarize(results)
    if args.json:
        print(json.dumps({"model": key, "effort": effort, "boards": results, "summary": summary},
                         ensure_ascii=True, indent=2, default=list))
        return 0
    name = (avail.get(key) or {}).get("name") or key
    label = f"{name}（{effort}）" + ("" if args.effort or embedded_effort else "　※沒有指定強度，用 " + effort)
    aa_res = next((r for r in results if r["board"] == tier_cfg.get("board")), None)
    if aa_res and aa_res["baseline"] and not aa_res["note"]:
        base = aa_res["baseline"]
        tier_line = f"基準等級：{tiers.label(base['tier'], tier_cfg)}（AA {base['score']:.1f}）"
    else:
        tier_line = aa_status_line(caches) or "基準等級：未分級（AA 沒有這個模型與強度的分數）"
    print(rank.render(label, results, summary, boards.prices(caches), caches, args.margin, tier_line), end="")
    return 0


def run_relay(argv: list[str]) -> int:
    from cuesheet import relay

    cfg = load_json(ROOT / "config.json", {})
    agents_cfg = load_json(ROOT / "agents.json", {"agents": []})
    provider_of = {a["id"]: a["provider"] for a in agents_cfg.get("agents", [])}

    def agents_for(quota_ids) -> dict:
        names = sorted({provider_of[q] for q in quota_ids if q in provider_of})
        providers = collect(names, cfg, 90) if names else {}
        return {a["id"]: a for a in evaluate_agents(providers, agents_cfg)}

    def get_agents(order, runners):
        return agents_for({runners[a].get("quota_agent", a) for a in order if a in runners})

    def recheck(quota_id):
        return agents_for({quota_id}).get(quota_id)

    agent_buckets = {a["id"]: a.get("buckets", []) + a.get("optional_buckets", [])
                     for a in agents_cfg.get("agents", [])}

    def snapshot(quota_id):
        """執行前後各呼叫一次：即時查這個代理那一家的額度（不用快取）。"""
        provider = provider_of.get(quota_id)
        if not provider:
            return {"status": "unavailable", "buckets": {}, "error": f"agents.json 沒有代理 {quota_id}"}
        return usage.snapshot(collect([provider], cfg, 90)[provider], agent_buckets.get(quota_id, []))

    def route_fn(task, runners, base_order, agents):
        """任務寫了難度或等級時：讀 AA 快取（超過一天才重抓）、等級表與額度樣本，挑出上場順序。"""
        from cuesheet import boards, rank, route, tiers
        caches = boards.refresh(ROOT / "data", ["aa"], False, None, cfg)
        if not caches["aa"].get("rows"):
            return {"order": list(base_order), "fallback": True,
                    "lines": [aa_status_line(caches) or "沒有 AA 資料", "改用固定順序：" + " → ".join(base_order)]}
        rows = rank.apply_map(boards.all_rows(caches), rank.load_model_map(ROOT))
        stats = route.usage_stats(route.load_samples(ROOT / "data" / "relay" / "usage_samples.jsonl"))
        return route.auto_order(task, runners, base_order, rows, tiers.load(ROOT), stats, agents)

    return relay.main(argv, root=ROOT, get_agents=get_agents, recheck_agent=recheck, load_json=load_json,
                      snapshot_fn=snapshot, activity_fn=usage.session_activity, route_fn=route_fn)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if sys.argv[1:2] == ["relay"]:
        return run_relay(sys.argv[2:])
    if sys.argv[1:2] == ["models"]:
        return run_models(sys.argv[2:])
    if sys.argv[1:2] == ["rank"]:
        return run_rank(sys.argv[2:])
    if sys.argv[1:2] == ["boards"]:
        return run_boards(sys.argv[2:])
    if sys.argv[1:2] == ["tiers"]:
        return run_tiers(sys.argv[2:])
    ap = argparse.ArgumentParser(prog="cue", description="CueSheet：查看 Claude Code、Codex、agy 的剩餘額度")
    ap.add_argument("--version", action="version", version=f"CueSheet {__version__}")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    ap.add_argument("--only", default="claude,codex,gemini", help="要查的端，逗號分隔")
    ap.add_argument("--out", default=str(ROOT / "data" / "latest.json"), help="JSON 寫入位置")
    ap.add_argument("--no-write", action="store_true", help="不寫檔")
    ap.add_argument("--max-age", type=float, default=0, help="快取秒數；latest.json 夠新就不重查")
    ap.add_argument("--agent", help="只看這個代理（agents.json 的 id），以結束碼回報狀態")
    ap.add_argument("--timeout", type=float, default=90, help="每端逾時秒數")
    ap.add_argument("--no-models", action="store_true", help="不檢查模型清單（新模型、接力模型）")
    args = ap.parse_args()

    cfg = load_json(ROOT / "config.json", {})
    agents_cfg = load_json(ROOT / "agents.json", {"agents": []})
    out_path = Path(args.out)

    report = None
    if args.max_age > 0:
        cached = load_json(out_path, None)
        gen = parse_iso((cached or {}).get("generated_at"))
        if gen and now_utc() - gen < timedelta(seconds=args.max_age):
            report = cached

    if report is None:
        names = [n.strip() for n in args.only.split(",") if n.strip() in PROVIDERS]
        if not names:
            ap.error("--only 至少要有 claude、codex、gemini 其中一個")
        check_models = not (args.no_models or args.agent or set(names) != set(PROVIDERS))
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_quota = pool.submit(collect, names, cfg, args.timeout)
            fut_models = pool.submit(model_check, cfg, args.timeout) if check_models else None
            providers = fut_quota.result()
            model_info = fut_models.result() if fut_models else None
        report = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now_iso(),
            "providers": providers,
            "agents": evaluate_agents(providers, agents_cfg),
            "unmapped_buckets": unmapped_buckets(providers, agents_cfg),
            "models": model_info,
        }
        if not args.no_write:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = out_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(out_path)

    if args.agent:
        agent = next((a for a in report["agents"] if a["id"] == args.agent), None)
        if agent is None:
            print(f"沒有這個代理：{args.agent}（可用：{', '.join(a['id'] for a in report['agents'])}）")
            return 3
        if args.json:
            print(json.dumps(agent, ensure_ascii=True))
        else:
            print(f"{agent['label']}：{LEVEL_TEXT[agent['level']]}，剩 {fmt_pct(agent['remaining_percent'])}，"
                  f"重置 {fmt_time(agent['resets_at'])}")
            for n in agent["notes"]:
                print(f"  · {n}")
        return EXIT_CODES[agent["level"]]

    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        print(render(report), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
