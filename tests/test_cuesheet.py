import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cuesheet import claude, codex, gemini  # noqa: E402
from cuesheet.report import evaluate_agents, render  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def by_id(res):
    return {b["id"]: b for b in res["buckets"]}


class ClaudeTest(unittest.TestCase):
    def test_parse(self):
        res = claude.parse(fixture("claude_get_usage.json"))
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["plan"], "pro")
        b = by_id(res)
        self.assertEqual(set(b), {"five_hour", "seven_day"})
        self.assertEqual(b["seven_day"]["remaining_percent"], 62.0)
        self.assertEqual(b["five_hour"]["resets_at"], "2026-11-06T18:00:00+00:00")

    def test_no_plan_limits(self):
        res = claude.parse({"subscription_type": None, "rate_limits_available": False})
        self.assertEqual(res["status"], "unavailable")
        self.assertEqual(res["buckets"], [])
        self.assertTrue(res["error"])


class CodexTest(unittest.TestCase):
    def test_parse_live(self):
        res = codex.parse_live(fixture("codex_rate_limits.json"))
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["plan"], "plus")
        b = by_id(res)
        self.assertEqual(b["codex.5h"]["used_percent"], 6.0)
        self.assertEqual(b["codex.weekly"]["remaining_percent"], 11.0)
        self.assertEqual(res["extra"]["reset_credits_available"], 2)

    def test_snapshot_after_reset_is_unknown(self):
        rl = {"limit_id": "codex", "plan_type": "plus",
              "primary": {"used_percent": 5.0, "window_minutes": 300, "resets_at": 1793985062},
              "secondary": {"used_percent": 89.0, "window_minutes": 10080, "resets_at": 1794237517}}
        later = datetime(2026, 11, 8, tzinfo=timezone.utc)  # 5 小時窗已過，每週窗還沒
        res = codex.parse_snapshot(rl, "2026-11-06T12:57:24Z", "rollout.jsonl", "timeout", now=later)
        self.assertEqual(res["status"], "stale")
        b = by_id(res)
        self.assertIsNone(b["codex.5h"]["remaining_percent"])
        self.assertEqual(b["codex.weekly"]["remaining_percent"], 11.0)


class GeminiTest(unittest.TestCase):
    def test_parse(self):
        res = gemini.parse(fixture("agy_usage.json"))
        self.assertEqual(res["status"], "ok")
        b = by_id(res)
        self.assertEqual(len(b), 5)
        self.assertAlmostEqual(b["gemini-argon-wtu-weekly"]["remaining_percent"], 27.6)
        self.assertEqual(b["3p-weekly"]["remaining_percent"], 0.4)

    def test_window_not_started_is_noted_not_hidden(self):
        checked = datetime(2026, 11, 7, 2, 2, 52, tzinfo=timezone.utc)
        out = {"status": "SUCCESS", "num_turns": 0, "command": {"name": "usage", "data": {"groups": [
            {"name": "Gemini 4 Argon", "buckets": [
                {"id": "gemini-argon-wtu-weekly", "window": "weekly", "remaining_fraction": 1,
                 "reset_time": "2026-11-14T02:02:56Z"}]},
            {"name": "Gemini Models", "buckets": [
                {"id": "gemini-weekly", "window": "weekly", "remaining_fraction": 0.9967,
                 "description": "You have used some of your weekly limit", "reset_time": "2026-11-14T02:01:28Z"}]}]}}}
        b = by_id(gemini.parse(out, checked))
        self.assertEqual(b["gemini-argon-wtu-weekly"]["remaining_percent"], 100.0)
        self.assertIn("還沒開始用", b["gemini-argon-wtu-weekly"]["note"])
        self.assertIsNone(b["gemini-weekly"]["note"])

    def test_rejects_model_turn(self):
        # 舊版 agy 把 /usage 當成一般提問：有 num_turns、沒有 command
        res = gemini.parse({"status": "SUCCESS", "num_turns": 1, "response": "..."})
        self.assertEqual(res["status"], "unavailable")
        self.assertEqual(res["buckets"], [])


class AgentsTest(unittest.TestCase):
    def setUp(self):
        self.providers = {
            "claude": claude.parse(fixture("claude_get_usage.json")),
            "codex": codex.parse_live(fixture("codex_rate_limits.json")),
            "gemini": gemini.parse(fixture("agy_usage.json")),
        }
        self.cfg = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))

    def agents(self):
        return {a["id"]: a for a in evaluate_agents(self.providers, self.cfg)}

    def test_levels_and_limiting_bucket(self):
        a = self.agents()
        self.assertEqual(a["claude"]["level"], "ok")
        self.assertEqual(a["luna"]["level"], "low")
        self.assertEqual(a["luna"]["limiting_bucket"], "codex.weekly")
        self.assertEqual(a["argon"]["limiting_bucket"], "gemini-argon-wtu-weekly")
        self.assertEqual(a["flash"]["limiting_bucket"], "gemini-weekly")
        self.assertEqual(a["agy-3p"]["level"], "exhausted")

    def test_optional_bucket_missing_is_silent(self):
        self.assertEqual(self.agents()["claude"]["notes"], [])

    def test_unavailable_provider_is_unknown(self):
        self.providers["gemini"] = gemini.parse({"num_turns": 1})
        a = self.agents()
        self.assertEqual(a["flash"]["level"], "unknown")
        self.assertIsNone(a["flash"]["remaining_percent"])

    def test_render(self):
        report = {"generated_at": "2026-11-06T13:00:00+00:00", "providers": self.providers,
                  "agents": evaluate_agents(self.providers, self.cfg)}
        text = render(report)
        self.assertIn("Luna", text)
        self.assertIn("[偏低]", text)


if __name__ == "__main__":
    unittest.main()
