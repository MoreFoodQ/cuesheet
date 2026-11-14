"""CueSheet：本機 AI 代理額度監控 CLI。

用法：
  python cue.py                  查三端、印出表格、寫入 data/latest.json
  python cue.py --json           改印 JSON
  python cue.py --only codex     只查某幾端（claude,codex,gemini）
  python cue.py --max-age 300    latest.json 未滿 300 秒就直接沿用
  python cue.py --agent luna     只看一個代理；結束碼 0 正常／1 偏低／2 用完／3 未知
  python cue.py relay <任務>     依額度接力執行 relay.json 裡的任務（見 cuesheet/relay.py）
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

from cuesheet import __version__, claude, codex, gemini  # noqa: E402
from cuesheet.common import now_iso, now_utc, parse_iso  # noqa: E402
from cuesheet.report import LEVEL_TEXT, evaluate_agents, fmt_pct, fmt_time, render  # noqa: E402

PROVIDERS = {"claude": claude, "codex": codex, "gemini": gemini}
EXIT_CODES = {"ok": 0, "low": 1, "exhausted": 2, "unknown": 3}
SCHEMA_VERSION = 1


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def collect(names: list[str], cfg: dict, timeout: float) -> dict:
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = {n: pool.submit(PROVIDERS[n].collect, cfg, timeout) for n in names}
        return {n: f.result() for n, f in futures.items()}


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

    return relay.main(argv, root=ROOT, get_agents=get_agents, recheck_agent=recheck, load_json=load_json)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if sys.argv[1:2] == ["relay"]:
        return run_relay(sys.argv[2:])
    ap = argparse.ArgumentParser(prog="cue", description="CueSheet：查看 Claude Code、Codex、agy 的剩餘額度")
    ap.add_argument("--version", action="version", version=f"CueSheet {__version__}")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    ap.add_argument("--only", default="claude,codex,gemini", help="要查的端，逗號分隔")
    ap.add_argument("--out", default=str(ROOT / "data" / "latest.json"), help="JSON 寫入位置")
    ap.add_argument("--no-write", action="store_true", help="不寫檔")
    ap.add_argument("--max-age", type=float, default=0, help="快取秒數；latest.json 夠新就不重查")
    ap.add_argument("--agent", help="只看這個代理（agents.json 的 id），以結束碼回報狀態")
    ap.add_argument("--timeout", type=float, default=90, help="每端逾時秒數")
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
        providers = collect(names, cfg, args.timeout)
        report = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now_iso(),
            "providers": providers,
            "agents": evaluate_agents(providers, agents_cfg),
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
