import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cuesheet import gemini, models, relay  # noqa: E402
from cuesheet.report import attention_items, unmapped_buckets  # noqa: E402

# 2026-11-14 三家 CLI 回傳的模型清單（節錄）
CLAUDE_RAW = [
    {"value": "default", "displayName": "Default (recommended)"},
    {"value": "opus", "displayName": "Opus 5.5"},
    {"value": "claude-fable-5-2", "displayName": "Fable 5.2"},
    {"value": "claude-opus-5", "displayName": "Opus 5"},
    {"value": "claude-opus-4-8", "displayName": "Opus 4.8"},
    {"value": "claude-haiku-4-5-20251001", "displayName": "Haiku 4.5"},
]
CODEX_RAW = [
    {"id": "gpt-6.1-sol", "displayName": "GPT-6.1-Sol", "supportedReasoningEfforts": [
        {"reasoningEffort": "low"}, {"reasoningEffort": "medium"}, {"reasoningEffort": "high"}]},
    {"id": "gpt-6-sol", "displayName": "GPT-6-Sol"},
    {"id": "gpt-6-luna", "displayName": "GPT-6-Luna", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]},
]
AGY_TEXT = """gemini-4-argon-medium\tGemini 4 Argon (Medium)
gemini-4-hydrogen-medium\tGemini 4 Hydrogen (Medium)
gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)
gemini-pro-orchestrator-g1-pro\tOrchestrator (Argon)
"""


def catalog():
    return {
        "claude": {"status": "ok", "models": models.parse_claude_models(CLAUDE_RAW)},
        "codex": {"status": "ok", "models": models.parse_codex_models(CODEX_RAW)},
        "agy": {"status": "ok", "models": models.parse_agy_models(AGY_TEXT)},
    }


class ParseTest(unittest.TestCase):
    def test_claude_alias_becomes_full_id(self):
        ids = [m["id"] for m in catalog()["claude"]["models"]]
        self.assertIn("claude-opus-5-5", ids)
        self.assertNotIn("default", ids)
        opus = next(m for m in catalog()["claude"]["models"] if m["id"] == "claude-opus-5-5")
        self.assertEqual(opus["aliases"], ["opus"])

    def test_agy_lines(self):
        ids = [m["id"] for m in catalog()["agy"]["models"]]
        self.assertEqual(len(ids), 4)
        self.assertIn("gemini-4-hydrogen-medium", ids)

    def test_family(self):
        self.assertEqual(models.parse_family("claude", "claude-opus-5-5")["version"], (5, 5))
        self.assertEqual(models.parse_family("claude", "claude-haiku-4-5-20251001")["version"], (4, 5))
        self.assertEqual(models.parse_family("codex", "gpt-6.1-sol")["family"], "sol")
        info = models.parse_family("agy", "gemini-3.8-flash-medium")
        self.assertEqual((info["family"], info["version"], info["effort"]), ("flash", (3, 8), "medium"))
        self.assertIsNone(models.parse_family("agy", "gemini-pro-orchestrator-g1-pro"))


class CheckRunnersTest(unittest.TestCase):
    def check(self, runners):
        return {r["runner"]: r for r in models.check_runners(runners, catalog())}

    def test_current_models_are_ok(self):
        rows = self.check({
            "claude": {"platform": "claude", "model": "claude-opus-5-5"},
            "sol": {"platform": "codex", "model": "gpt-6.1-sol", "effort": "medium"},
            "argon": {"platform": "agy", "model": "gemini-4-argon-medium"},
        })
        self.assertEqual({k: v["status"] for k, v in rows.items()},
                         {"claude": "ok", "sol": "ok", "argon": "ok"})

    def test_older_version_is_upgradable(self):
        rows = self.check({"a": {"platform": "codex", "model": "gpt-6-sol"},
                           "b": {"platform": "claude", "model": "claude-opus-4-8"}})
        self.assertEqual((rows["a"]["status"], rows["a"]["suggestion"]), ("upgradable", "gpt-6.1-sol"))
        self.assertEqual((rows["b"]["status"], rows["b"]["suggestion"]), ("upgradable", "claude-opus-5-5"))

    def test_missing_model_suggests_same_family(self):
        rows = self.check({"a": {"platform": "codex", "model": "gpt-5.6-sol"},
                           "b": {"platform": "agy", "model": "gemini-3.5-flash-medium"},
                           "c": {"platform": "agy", "model": "gemini-9-unknown-medium"}})
        self.assertEqual((rows["a"]["status"], rows["a"]["suggestion"]), ("missing", "gpt-6.1-sol"))
        self.assertEqual(rows["b"]["suggestion"], "gemini-3.8-flash-medium")
        self.assertIsNone(rows["c"]["suggestion"])

    def test_unsupported_effort(self):
        rows = self.check({"luna": {"platform": "codex", "model": "gpt-6-luna", "effort": "ultra"}})
        self.assertIn("ultra", rows["luna"]["note"])

    def test_unreadable_platform(self):
        cat = catalog()
        cat["agy"] = {"status": "unavailable", "models": [], "error": "找不到 agy 執行檔"}
        rows = {r["runner"]: r for r in models.check_runners(
            {"argon": {"platform": "agy", "model": "gemini-4-argon-medium"}}, cat)}
        self.assertEqual(rows["argon"]["status"], "unknown")

    def test_apply_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runners.json"
            path.write_text(json.dumps({"runners": {"sol": {"platform": "codex", "model": "gpt-6-sol"}}}),
                            encoding="utf-8")
            rows = models.check_runners(json.loads(path.read_text(encoding="utf-8"))["runners"], catalog())
            self.assertEqual(models.apply_updates(path, rows), [("sol", "gpt-6-sol", "gpt-6.1-sol")])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["runners"]["sol"]["model"], "gpt-6.1-sol")


class SeenTest(unittest.TestCase):
    def test_first_run_is_baseline_then_new_models_show(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seen.json"
            now = datetime(2026, 11, 14, tzinfo=timezone.utc)
            self.assertEqual(models.update_seen(catalog(), path, now), [])
            cat = catalog()
            cat["agy"]["models"].append({"id": "gemini-5-neon-medium", "name": "Gemini 5 Neon (Medium)"})
            fresh = models.update_seen(cat, path, now + timedelta(days=1))
            self.assertEqual([m["id"] for m in fresh], ["gemini-5-neon-medium"])
            self.assertEqual(models.update_seen(cat, path, now + timedelta(days=9)), [])


class GeminiLabelTest(unittest.TestCase):
    def test_group_label_follows_description(self):
        desc = "Models within this group: Gemini 4 Hydrogen, Gemini 3.8 Flash"
        self.assertEqual(gemini.group_label("Gemini Models", desc), "Gemini 組（Hydrogen、Flash）")
        self.assertEqual(gemini.group_label("Gemini 4 Argon", "For paid Pro plans, ..."), "Argon 專屬")
        self.assertEqual(gemini.group_label("New Group", None), "New Group")


class AttentionTest(unittest.TestCase):
    def test_unmapped_bucket_and_model_issues(self):
        providers = {"claude": {"status": "ok", "buckets": [
            {"id": "seven_day", "label": "每週", "remaining_percent": 27.0},
            {"id": "model:Fable", "label": "每週 Fable", "remaining_percent": 100.0}]}}
        agents_cfg = {"agents": [{"id": "claude", "buckets": ["seven_day"]}]}
        report = {"unmapped_buckets": unmapped_buckets(providers, agents_cfg), "models": {
            "new": [{"platform": "agy", "id": "gemini-5-neon-medium", "name": "Gemini 5 Neon"}],
            "runners": [{"runner": "sol", "model": "gpt-6-sol", "status": "upgradable", "suggestion": "gpt-6.1-sol"}],
            "catalog": {}}}
        text = "\n".join(attention_items(report))
        self.assertIn("每週 Fable", text)
        self.assertIn("Gemini 5 Neon", text)
        self.assertIn("gpt-6.1-sol", text)


class RelayModelTest(unittest.TestCase):
    def test_runner_built_from_platform(self):
        r = relay.resolve_runner("sol", {"platform": "codex", "model": "gpt-6.1-sol", "effort": "medium"})
        self.assertIn("gpt-6.1-sol", r["args"])
        self.assertIn("model_reasoning_effort=medium", r["args"])
        r = relay.resolve_runner("flash", {"platform": "agy", "model": "gemini-3.8-flash-medium"})
        self.assertNotIn("--effort", r["args"])

    def test_relay_json_overrides_only_model(self):
        base = {"runners": {"claude": {"platform": "claude", "model": "claude-opus-5-5", "effort": "medium"}}}
        runners = relay.runners_from({"runners": {"claude": {"model": "claude-fable-5-2"}}}, base)
        self.assertIn("claude-fable-5-2", runners["claude"]["args"])
        self.assertIn("--effort", runners["claude"]["args"])

    def test_model_missing_detection(self):
        for msg in ["There's an issue with the selected model (x).",
                    '{"code":"model_not_found"}',
                    "model gemini-x is not recognized as a known model"]:
            self.assertTrue(relay.model_missing({"exit_code": 1, "stdout": msg, "stderr": ""}))
        self.assertFalse(relay.model_missing({"exit_code": 0, "stdout": "model_not_found", "stderr": ""}))

    def test_repo_runners_file_is_valid(self):
        base = json.loads((ROOT / "runners.json").read_text(encoding="utf-8"))
        runners = relay.runners_from({}, base)
        self.assertEqual(base["default_order"], ["claude", "sol", "argon"])
        for rid in base["default_order"]:
            self.assertIn(rid, runners)


if __name__ == "__main__":
    unittest.main()
