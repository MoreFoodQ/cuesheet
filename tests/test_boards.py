import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cuesheet import boards, rank, relay  # noqa: E402

# 2026-11-18 各來源的資料節錄（數字取自當天實際下載的資料）
ARENA = [
    {"model_name": "Claude Opus 5.5 (Max)", "score": 0.1819, "score_ci_lower": 0.162, "score_ci_upper": 0.202,
     "cost_per_task_p50": 5.65, "category": "overall", "leaderboard_publish_date": "2026-11-15"},
    {"model_name": "Claude Opus 5.5 (High)", "score": 0.1335, "score_ci_lower": 0.123, "score_ci_upper": 0.144,
     "cost_per_task_p50": 2.12, "category": "overall", "leaderboard_publish_date": "2026-11-15"},
    {"model_name": "Gemini 4 Hydrogen (High)", "score": 0.1491, "score_ci_lower": 0.13, "score_ci_upper": 0.168,
     "cost_per_task_p50": 1.53, "category": "overall", "leaderboard_publish_date": "2026-11-15"},
    {"model_name": "GPT-6.1 Sol (Medium)", "score": 0.0804, "score_ci_lower": 0.07, "score_ci_upper": 0.091,
     "cost_per_task_p50": 0.37, "category": "overall", "leaderboard_publish_date": "2026-11-15"},
    {"model_name": "Hy4", "score": 0.05, "category": "overall", "leaderboard_publish_date": "2026-11-15"},
]
CURSOR_CSV = """Model version,Score,Reasoning level,Cost per task
claude-opus-5-5_medium,0.525,Medium,2.91
claude-opus-5-5_high,0.56,High,3.97
gemini-4-hydrogen_high,0.543,High,5.59
gemini-4-argon_medium,0.528,Medium,4.39
gpt-6.1-sol_medium,0.40,Medium,0.50
claude-opus-5-5-fallback_max,0.6,Max,9
"""
ECI_CSV = """Model,Display name,eci,eci_ci_low,eci_ci_high
Claude Opus 5.5,Claude Opus 5.5,168.7,165.87,171.89
GPT-6.1 Sol,GPT-6.1 Sol,168.6,165.7,171.9
"""
OPENROUTER_CSV = """benchmark,model_slug,provider_name,accuracy,avg_cost_per_task_usd
gpqa_diamond,anthropic/claude-opus-5.5,Amazon Bedrock,0.90,0.06
gpqa_diamond,anthropic/claude-opus-5.5,Anthropic,0.9011,0.054
gpqa_diamond,openai/gpt-6.1-sol,OpenAI,0.944,0.02
tau_bench_verified_airline,openai/gpt-6.1-sol,OpenAI,0.7,0.4
"""


def epoch_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("epoch_capabilities_index/eci_scores.csv", ECI_CSV)
        z.writestr("cursorbench_external.csv", CURSOR_CSV)
    return buf.getvalue()


CATALOG = {
    "claude": {"status": "ok", "models": [
        {"id": "claude-opus-5-5", "name": "Opus 5.5", "aliases": ["opus"], "efforts": ["low", "medium", "high", "max"]}]},
    "codex": {"status": "ok", "models": [
        {"id": "gpt-6.1-sol", "name": "GPT-6.1-Sol", "efforts": ["low", "medium", "high"]},
        {"id": "gpt-6.2-sol", "name": "GPT-6.2-Sol", "efforts": ["medium"]}]},
    "agy": {"status": "ok", "models": [
        {"id": "gemini-4-hydrogen-high", "name": "Gemini 4 Hydrogen (High)"},
        {"id": "gemini-4-argon-medium", "name": "Gemini 4 Argon (Medium)"},
        {"id": "claude-opus-5-5-medium", "name": "Claude Opus 5.5 (Medium)"}]},
}
RUNNERS = {"claude": {"platform": "claude", "model": "claude-opus-5-5", "effort": "medium"},
           "argon": {"platform": "agy", "model": "gemini-4-argon-medium", "effort": "medium"}}
AGENTS = {"claude": {"level": "ok", "remaining_percent": 70.0}, "argon": {"level": "low", "remaining_percent": 12.0}}


def all_rows():
    return (boards.parse_arena(ARENA) + boards.parse_epoch(epoch_zip())
            + boards.parse_openrouter(OPENROUTER_CSV))


class NamingTest(unittest.TestCase):
    def test_norm_key(self):
        self.assertEqual(boards.norm_key("Claude Opus 5.5"), "claude-opus-5-5")
        self.assertEqual(boards.norm_key("GPT-6.1 Sol"), "gpt-6-1-sol")
        self.assertEqual(boards.norm_key("anthropic/claude-opus-5.5"), "claude-opus-5-5")
        self.assertEqual(boards.norm_key("gpt-6.2-sol"), "gpt-6-2-sol")
        self.assertNotEqual(boards.norm_key("gpt-6.1-sol"), boards.norm_key("gpt-6.2-sol"))

    def test_split_name(self):
        self.assertEqual(boards.split_name("Claude Opus 5.5 (Max)"), ("claude-opus-5-5", "max"))
        self.assertEqual(boards.split_name("GPT-6.1 Sol (xHigh)"), ("gpt-6-1-sol", "xhigh"))
        self.assertEqual(boards.split_name("Claude Opus 5.5 (Medium, Default Fallback)"), ("claude-opus-5-5", "medium"))
        self.assertEqual(boards.split_name("claude-opus-5-5_medium"), ("claude-opus-5-5", "medium"))
        self.assertEqual(boards.split_name("gemini-3.8-flash_high"), ("gemini-3-8-flash", "high"))
        self.assertEqual(boards.split_name("Hy4"), ("hy4", None))


class ParseTest(unittest.TestCase):
    def test_arena(self):
        rows = {r["raw"]: r for r in boards.parse_arena(ARENA)}
        r = rows["Claude Opus 5.5 (Max)"]
        self.assertEqual((r["key"], r["effort"], r["score"], r["cost"]), ("claude-opus-5-5", "max", 18.19, 5.65))
        self.assertEqual(r["ci"], [16.2, 20.2])

    def test_epoch(self):
        rows = boards.parse_epoch(epoch_zip())
        cursor = [r for r in rows if r["board"] == "epoch:cursorbench"]
        self.assertEqual(len(cursor), 5)  # fallback 那列排除
        med = next(r for r in cursor if r["key"] == "claude-opus-5-5" and r["effort"] == "medium")
        self.assertEqual((med["score"], med["cost"]), (52.5, 2.91))
        eci = next(r for r in rows if r["board"] == "epoch_eci" and r["key"] == "claude-opus-5-5")
        self.assertIsNone(eci["effort"])

    def test_openrouter_prefers_vendor_row(self):
        rows = {r["key"]: r for r in boards.parse_openrouter(OPENROUTER_CSV)}
        self.assertEqual(rows["claude-opus-5-5"]["cost"], 0.054)
        self.assertEqual(len(rows), 2)

    def test_models_dev(self):
        data = {"anthropic": {"models": {"claude-opus-5-5": {"cost": {"input": 4, "output": 20}}}},
                "openai": {"models": {"gpt-6.2-sol": {"cost": {"input": 2, "output": 10}}}},
                "someone-else": {"models": {"x": {"cost": {"input": 1}}}}}
        prices = boards.parse_models_dev(data)
        self.assertEqual(set(prices), {"claude-opus-5-5", "gpt-6-2-sol"})


class RefreshTest(unittest.TestCase):
    def test_failure_keeps_old_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            ok = {"arena": lambda: {"board_date": "2026-11-15", "rows": boards.parse_arena(ARENA)}}
            boards.refresh(d, ["arena"], force=True, fetchers=ok)

            def broken():
                raise OSError("site down")
            caches = boards.refresh(d, ["arena"], force=True, fetchers={"arena": broken})
            self.assertEqual(caches["arena"]["status"], "stale")
            self.assertEqual(len(caches["arena"]["rows"]), 5)
            self.assertIn("site down", caches["arena"]["error"])

    def test_first_failure_is_unavailable_and_others_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            def broken():
                raise OSError("down")
            caches = boards.refresh(Path(tmp), ["arena", "openrouter"], force=True,
                                    fetchers={"arena": broken,
                                              "openrouter": lambda: {"rows": boards.parse_openrouter(OPENROUTER_CSV)}})
            self.assertEqual(caches["arena"]["status"], "unavailable")
            self.assertEqual(caches["openrouter"]["status"], "ok")

    def test_fresh_cache_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            fetch = {"arena": lambda: calls.append(1) or {"rows": []}}
            boards.refresh(Path(tmp), ["arena"], fetchers=fetch)
            boards.refresh(Path(tmp), ["arena"], fetchers=fetch)
            self.assertEqual(len(calls), 1)


class RankTest(unittest.TestCase):
    def setUp(self):
        self.rows = all_rows()
        self.avail = rank.build_availability(CATALOG, RUNNERS, AGENTS)

    def test_resolve_model(self):
        self.assertEqual(rank.resolve_model("opus", CATALOG, self.rows)[:2], ("claude-opus-5-5", None))
        self.assertEqual(rank.resolve_model("gemini-4-argon-medium", CATALOG, self.rows)[:2], ("gemini-4-argon", "medium"))
        self.assertEqual(rank.resolve_model("hydrogen", CATALOG, self.rows)[0], "gemini-4-hydrogen")
        key, _, candidates = rank.resolve_model("sol", CATALOG, self.rows)
        self.assertIsNone(key)
        self.assertIn("gpt-6-2-sol", candidates)

    def test_missing_effort_uses_nearest_and_says_so(self):
        res = rank.rank_board("arena_agent", self.rows, "claude-opus-5-5", "medium", self.avail)
        self.assertEqual(res["baseline"]["effort"], "high")
        self.assertIn("沒有 medium", res["note"])

    def test_board_without_efforts(self):
        res = rank.rank_board("epoch_eci", self.rows, "claude-opus-5-5", "medium", self.avail)
        self.assertIn("不分推理強度", res["note"])

    def test_not_on_board(self):
        res = rank.rank_board("arena_agent", self.rows, "gpt-6-2-sol", "medium", self.avail)
        self.assertIsNone(res["baseline"])

    def test_candidates_cheaper_and_availability(self):
        res = rank.rank_board("epoch:cursorbench", self.rows, "claude-opus-5-5", "medium", self.avail)
        by = {(r["key"], r["effort"]): r for r in res["rows"]}
        self.assertTrue(by[("claude-opus-5-5", "medium")]["is_base"])
        self.assertEqual(by[("gemini-4-hydrogen", "high")]["mark"], "↑")
        self.assertNotIn(("gpt-6-1-sol", "medium"), by)  # 40 分，低於基準 3 分以上
        self.assertIn("已設定 argon，偏低 12%", by[("gemini-4-argon", "medium")]["available"])
        self.assertIn("未設定", by[("gemini-4-hydrogen", "high")]["available"])
        self.assertEqual(by[("gemini-4-hydrogen", "high")]["name"], "gemini-4-hydrogen")

    def test_unavailable_models_hidden_unless_all(self):
        res = rank.rank_board("arena_agent", self.rows, "gpt-6-1-sol", "medium", self.avail, margin=100)
        self.assertNotIn("hy4", {r["key"] for r in res["rows"]})
        res = rank.rank_board("arena_agent", self.rows, "gpt-6-1-sol", "medium", self.avail, margin=100, show_all=True)
        self.assertIn("hy4", {r["key"] for r in res["rows"]})

    def test_summary_counts_cheaper_peers(self):
        results = [rank.rank_board(b, self.rows, "claude-opus-5-5", "high", self.avail) for b in boards.BOARDS]
        names = [t["model"] for t in rank.summarize(results)]
        self.assertIn("gemini-4-hydrogen (high)", names)

    def test_model_map_alias_and_ignore(self):
        mm = {"aliases": {"arena_agent": {"hy4": "gemini-4-hydrogen"}}, "ignore": {"*": ["gpt-6-1-sol"]}}
        rows = rank.apply_map(boards.parse_arena(ARENA), mm)
        keys = [r["key"] for r in rows]
        self.assertNotIn("gpt-6-1-sol", keys)
        self.assertNotIn("hy4", keys)

    def test_render_has_attribution_and_warnings(self):
        results = [rank.rank_board(b, self.rows, "claude-opus-5-5", "medium", self.avail) for b in boards.BOARDS]
        text = rank.render("claude-opus-5-5（medium）", results, rank.summarize(results), {},
                           {"arena": {"status": "ok", "board_date": "2026-11-15"}}, 3)
        self.assertIn("LMArena", text)
        self.assertIn("改用 high 對照", text)
        self.assertIn("這個榜沒有這個模型", text)


class RelayAuthTest(unittest.TestCase):
    def test_auth_failure_hands_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = {"name": "t", "prompt": "p", "workdir": tmp, "order": ["a", "b"],
                    "min_remaining_percent": 20, "timeout_minutes": 1, "handover_on": "quota"}
            py = sys.executable
            runners = {"a": {"exe": py, "stdin": True, "args": ["-c",
                             "print('Failed to authenticate: OAuth session expired'); raise SystemExit(1)"]},
                       "b": {"exe": py, "stdin": True, "args": ["-c", "pass"]}}
            agents = {k: {"level": "ok", "remaining_percent": 80, "resets_at": None} for k in "ab"}
            run = relay.run_task(task, agents, runners, {}, Path(tmp), echo=lambda *_: None)
            self.assertEqual(run["agent"], "b")
            self.assertEqual(run["attempts"][0]["result"], "auth")


if __name__ == "__main__":
    unittest.main()
