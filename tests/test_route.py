import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cuesheet import relay, route, tiers  # noqa: E402


def row(key, effort, score, cost):
    return {"board": "aa_intelligence", "key": key, "effort": effort, "score": score, "cost": cost}


# 2026-11-18 AA 實際分數
ROWS = [row("claude-opus-5-5", "medium", 48.2, 2.45), row("gpt-6-2-sol", "medium", 52.7, 0.41),
        row("gemini-4-argon", "medium", 47.6, 4.31), row("gpt-6-luna", "medium", 18.5, 0.04),
        row("gemini-3-8-flash", "medium", 30.1, 6.26), row("gemini-4-hydrogen", "high", 50.8, 1.87)]
RUNNERS = {
    "claude": {"platform": "claude", "model": "claude-opus-5-5", "effort": "medium"},
    "sol": {"platform": "codex", "model": "gpt-6.2-sol", "effort": "medium"},
    "argon": {"platform": "agy", "model": "gemini-4-argon-medium", "effort": "medium"},
    "luna": {"platform": "codex", "model": "gpt-6-luna"},
    "flash": {"platform": "agy", "model": "gemini-3.8-flash-medium"},
}
BASE = ["claude", "sol", "argon"]
AGENTS = {k: {"level": "ok", "remaining_percent": 80, "resets_at": None} for k in RUNNERS}


def sample(agent, status="clean", delta=2.0, task="t"):
    r = RUNNERS[agent]
    return {"task": task, "agent": agent, "model": r["model"], "effort": r.get("effort"),
            "quota_agent": agent, "status": status, "delta": {"five_hour": delta}}


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.cfg = tiers.load(ROOT)

    def order(self, task, samples=()):
        task = {"name": "t", **task}
        return route.auto_order(task, RUNNERS, BASE, ROWS, self.cfg, route.usage_stats(list(samples)), AGENTS)

    def test_runner_tier_reads_agy_effort_from_id(self):
        self.assertEqual(route.runner_tier(RUNNERS["argon"], ROWS, self.cfg)["tier"], "L3")
        luna = route.runner_tier(RUNNERS["luna"], ROWS, self.cfg)
        self.assertEqual(luna["tier"], "L5")
        self.assertIn("medium", luna["note"])

    def test_hard_keeps_baseline_tier_or_better_strongest_first(self):
        res = self.order({"difficulty": "hard"})
        self.assertEqual(res["order"], ["sol", "claude", "argon"])  # Flash、Luna 不會被派去做難的

    def test_easy_weakest_first(self):
        res = self.order({"difficulty": "easy"})
        self.assertEqual(res["order"][:2], ["luna", "flash"])
        self.assertEqual(res["order"][-1], "sol")

    def test_normal_without_samples_uses_fixed_order(self):
        res = self.order({"difficulty": "normal"})
        self.assertEqual(res["order"], ["claude", "sol", "argon"])
        self.assertTrue(res["fallback"])

    def test_normal_with_enough_clean_samples_prefers_most_runs_left(self):
        samples = [sample("claude", delta=10) for _ in range(5)] + [sample("sol", delta=1) for _ in range(5)] \
            + [sample("argon", delta=4) for _ in range(5)] + [sample("sol", status="concurrent", delta=50)]
        res = self.order({"difficulty": "normal"}, samples)
        self.assertFalse(res["fallback"])
        self.assertEqual(res["order"], ["sol", "argon", "claude"])

    def test_requires_shell_keeps_codex_only(self):
        res = self.order({"difficulty": "hard", "requires_shell": True})
        self.assertEqual(res["order"], ["sol"])

    def test_explicit_tier_range(self):
        res = self.order({"min_tier": "L5", "max_tier": "L4", "difficulty": "easy"})
        self.assertEqual(set(res["order"]), {"luna", "flash"})

    def test_unranked_excluded_unless_override(self):
        runners = {**RUNNERS, "new": {"platform": "codex", "model": "gpt-9", "effort": "medium"}}
        task = {"name": "t", "difficulty": "easy"}
        res = route.auto_order(task, runners, BASE, ROWS, self.cfg, {}, AGENTS)
        self.assertNotIn("new", res["order"])
        cfg = {**self.cfg, "overrides": {"gpt-9:medium": "L2"}}
        res = route.auto_order(task, runners, BASE, ROWS, cfg, {}, AGENTS)
        self.assertIn("new", res["order"])

    def test_bad_difficulty(self):
        with self.assertRaises(ValueError):
            self.order({"difficulty": "extreme"})

    def test_stats_only_count_clean(self):
        st = route.usage_stats([sample("sol", delta=1), sample("sol", delta=3), sample("sol", "window_reset", 40)])
        s = st[("t", "sol", "gpt-6.2-sol", "medium")]
        self.assertEqual((s["clean"], s["total"]), (2, 3))
        self.assertEqual(s["avg"]["five_hour"], 2.0)
        self.assertFalse(s["enough"])
        self.assertIsNone(route.runs_left(s, AGENTS["sol"]))


class RelayAutoTest(unittest.TestCase):
    def test_dry_run_prints_route_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runners.json").write_text(json.dumps({"default_order": BASE, "runners": RUNNERS}), encoding="utf-8")
            (root / "relay.json").write_text(json.dumps({"tasks": {"t": {
                "prompt": "hi", "workdir": tmp, "difficulty": "hard"}}}), encoding="utf-8")
            calls = []

            def route_fn(task, runners, base, agents):
                calls.append(sorted(agents))
                return route.auto_order(task, runners, base, ROWS, tiers.load(ROOT), {}, agents)

            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            orig = relay.build_command
            relay.build_command = lambda r, p, w, c: (["x"], None)
            try:
                with redirect_stdout(buf):
                    code = relay.main(["t", "--dry-run"], root=root, get_agents=lambda o, r: AGENTS,
                                      route_fn=route_fn)
            finally:
                relay.build_command = orig
            out = buf.getvalue()
            self.assertEqual(code, 0)
            self.assertEqual(calls, [sorted(AGENTS)])
            self.assertIn("難度：難", out)
            self.assertIn("✗ flash", out)
            self.assertLess(out.index("[1] sol"), out.index("[2] claude"))

    def test_stats_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runners.json").write_text(json.dumps({"runners": RUNNERS}), encoding="utf-8")
            p = root / "data" / "relay" / "usage_samples.jsonl"
            p.parent.mkdir(parents=True)
            p.write_text("\n".join(json.dumps(sample("sol", delta=1)) for _ in range(5)), encoding="utf-8")
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = relay.main(["--stats"], root=root, get_agents=lambda o, r: AGENTS)
            self.assertEqual(code, 0)
            self.assertIn("估計還能跑 80 次", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
