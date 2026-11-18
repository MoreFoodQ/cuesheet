import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cuesheet import boards, rank, tiers  # noqa: E402

# Artificial Analysis 免費 API 的回應格式（照官方 OpenAPI 範例，數字是 2026-11-18 的榜單值）
PAGE1 = {"tier": "free", "intelligence_index_version": 5,
         "pagination": {"page": 1, "page_size": 2, "total_pages": 2, "has_more": True},
         "data": [
             {"name": "Claude Opus 5.5 (Medium, Default Fallback)", "slug": "claude-opus-5-5-medium",
              "evaluations": {"artificial_analysis_intelligence_index": 48.21},
              "artificial_analysis_intelligence_index_cost": {"cost_per_task": {"total_cost": 2.45}}},
             {"name": "GPT-6.2 Sol (Medium)", "slug": "gpt-6-2-sol-medium",
              "evaluations": {"artificial_analysis_intelligence_index": 52.68},
              "artificial_analysis_intelligence_index_cost": {"cost_per_task": {"total_cost": 0.41}}}]}
PAGE2 = {"tier": "free", "intelligence_index_version": 5,
         "pagination": {"page": 2, "page_size": 2, "total_pages": 2, "has_more": False},
         "data": [
             {"name": "Gemini 4 Hydrogen (High)", "slug": "gemini-4-hydrogen",
              "evaluations": {"artificial_analysis_intelligence_index": 50.79},
              "artificial_analysis_intelligence_index_cost": {"cost_per_task": {"total_cost": 1.87}}},
             {"name": "GPT-6 Luna (Medium)", "slug": "gpt-6-luna",
              "evaluations": {"artificial_analysis_intelligence_index": 18.45},
              "artificial_analysis_intelligence_index_cost": {"cost_per_task": {"total_cost": 0.05}}}]}


def fake_get(pages):
    calls = []

    def get(url, key):
        calls.append((url, key))
        page = int(url.rsplit("page=", 1)[1])
        return pages[page - 1], {"X-Ratelimit-Remaining": "97"}
    return get, calls


class AATest(unittest.TestCase):
    def test_parse_and_pagination(self):
        get, calls = fake_get([PAGE1, PAGE2])
        data = boards.fetch_aa("test-key", get=get)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(c[1] == "test-key" for c in calls))
        self.assertTrue(all("artificialanalysis.ai/api/v2/" in c[0] for c in calls))  # 只打 API，不碰網頁
        rows = {(r["key"], r["effort"]): r for r in data["rows"]}
        self.assertEqual(rows[("claude-opus-5-5", "medium")]["score"], 48.21)
        self.assertEqual(rows[("gpt-6-2-sol", "medium")]["cost"], 0.41)
        self.assertEqual(data["index_version"], 5)
        self.assertEqual(data["rate_limit_remaining"], "97")

    def test_no_key_is_disabled_not_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            caches = boards.refresh(Path(tmp), ["aa"], force=True, cfg={})
            self.assertEqual(caches["aa"]["status"], "disabled")
            self.assertIn("aa_api_key", caches["aa"]["error"])

    def test_dropped_models_are_kept_and_marked(self):
        get, _ = fake_get([PAGE1, PAGE2])
        first = boards.fetch_aa("k", get=get)
        only_page = {**PAGE1, "pagination": {"has_more": False}, "data": PAGE1["data"][:1]}
        get2, _ = fake_get([only_page])
        second = boards.fetch_aa("k", get=get2, previous=first)
        dropped = [r for r in second["rows"] if r.get("dropped")]
        self.assertEqual({r["key"] for r in dropped}, {"gpt-6-2-sol", "gemini-4-hydrogen", "gpt-6-luna"})

    def test_key_from_env_or_config(self):
        import os
        old = os.environ.pop(boards.AA_KEY_ENV, None)
        try:
            self.assertEqual(boards.aa_key({"aa_api_key": "from-config"}), "from-config")
            os.environ[boards.AA_KEY_ENV] = "from-env"
            self.assertEqual(boards.aa_key({"aa_api_key": "from-config"}), "from-env")
        finally:
            os.environ.pop(boards.AA_KEY_ENV, None)
            if old is not None:
                os.environ[boards.AA_KEY_ENV] = old

    def test_key_never_in_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            get, _ = fake_get([PAGE1, PAGE2])
            boards.refresh(Path(tmp), ["aa"], force=True,
                           fetchers={"aa": lambda: boards.fetch_aa("secret-key-123", get=get)})
            text = (Path(tmp) / "boards" / "aa.json").read_text(encoding="utf-8")
            self.assertNotIn("secret-key-123", text)


class TierTest(unittest.TestCase):
    def setUp(self):
        self.cfg = tiers.load(ROOT)

    def test_bands(self):
        self.assertEqual(tiers.tier_of(59.1, self.cfg), "L1")
        self.assertEqual(tiers.tier_of(52.68, self.cfg), "L2")
        self.assertEqual(tiers.tier_of(50.0, self.cfg), "L2")
        self.assertEqual(tiers.tier_of(48.21, self.cfg), "L3")
        self.assertEqual(tiers.tier_of(41.2, self.cfg), "L4")
        self.assertEqual(tiers.tier_of(18.45, self.cfg), "L5")

    def test_unranked_and_override(self):
        self.assertEqual(tiers.tier_of(None, self.cfg), tiers.UNRANKED)
        cfg = {**self.cfg, "overrides": {"gemini-3-8-flash:high": "L4"}}
        self.assertEqual(tiers.tier_of(None, cfg, "gemini-3-8-flash", "high"), "L4")

    def test_group_only_available(self):
        get, _ = fake_get([PAGE1, PAGE2])
        rows = boards.fetch_aa("k", get=get)["rows"]
        avail = {"claude-opus-5-5": {"platforms": {"claude": set()}, "runners": []},
                 "gpt-6-2-sol": {"platforms": {"codex": set()}, "runners": []}}
        groups = tiers.group(rows, self.cfg, avail)
        self.assertEqual([r["key"] for r in groups["L2"]], ["gpt-6-2-sol"])
        self.assertEqual([r["key"] for r in groups["L3"]], ["claude-opus-5-5"])
        self.assertEqual(groups["L5"], [])
        self.assertEqual(len(tiers.group(rows, self.cfg, avail, show_all=True)["L5"]), 1)

    def test_rank_shows_tier_on_aa_board(self):
        get, _ = fake_get([PAGE1, PAGE2])
        rows = boards.fetch_aa("k", get=get)["rows"]
        avail = {k: {"platforms": {"x": set()}, "runners": []}
                 for k in ("claude-opus-5-5", "gpt-6-2-sol", "gemini-4-hydrogen")}
        res = rank.rank_board("aa_intelligence", rows, "claude-opus-5-5", "medium", avail, tier_cfg=self.cfg)
        by = {r["key"]: r for r in res["rows"]}
        self.assertEqual(by["claude-opus-5-5"]["tier"], "L3")
        self.assertEqual(by["gpt-6-2-sol"]["tier"], "L2")
        self.assertTrue(by["gpt-6-2-sol"]["cheaper"])
        self.assertIsNone(res["note"])

    def test_limit_keeps_cheaper_rows(self):
        rows = [{"board": "aa_intelligence", "key": "base", "effort": "medium", "score": 48.0, "cost": 2.0}]
        rows += [{"board": "aa_intelligence", "key": f"pricey{i}", "effort": None, "score": 48.5 + i * 0.1, "cost": 9.0}
                 for i in range(5)]
        rows += [{"board": "aa_intelligence", "key": "cheap-far", "effort": "medium", "score": 52.7, "cost": 0.4}]
        avail = {r["key"]: {"platforms": {"x": set()}, "runners": []} for r in rows}
        res = rank.rank_board("aa_intelligence", rows, "base", "medium", avail, limit=3)
        keys = [r["key"] for r in res["rows"]]
        self.assertIn("base", keys)
        self.assertIn("cheap-far", keys)
        self.assertEqual(res["hidden_above"], 4)


if __name__ == "__main__":
    unittest.main()
