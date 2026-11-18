import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cuesheet import relay, usage, versions  # noqa: E402
from cuesheet.report import attention_items  # noqa: E402

PY = sys.executable


class VersionTest(unittest.TestCase):
    def test_parse_and_compare(self):
        self.assertEqual(versions.parse_version("codex-cli 0.170.0"), (0, 170, 0))
        self.assertEqual(versions.parse_version("2.1.311 (Claude Code)"), (2, 1, 311))
        self.assertEqual(versions.compare("0.164.0", "0.170.0"), "outdated")
        self.assertEqual(versions.compare("2.1.311", "2.1.311"), "ok")
        self.assertEqual(versions.compare("2.1.311", None), "unknown")

    def test_npm_channel(self):
        tags = {"stable": "2.1.311", "latest": "2.1.327"}
        row = versions.check_npm("claude", "@anthropic-ai/claude-code", "2.1.311", "stable", fetch=lambda p: tags)
        self.assertEqual((row["latest"], row["status"]), ("2.1.311", "ok"))
        row = versions.check_npm("claude", "@anthropic-ai/claude-code", "2.1.311", "latest", fetch=lambda p: tags)
        self.assertEqual((row["latest"], row["status"]), ("2.1.327", "outdated"))

    def test_network_failure_is_unknown(self):
        def boom(_):
            raise OSError("offline")
        row = versions.check_npm("codex", "@openai/codex", "0.170.0", "latest", fetch=boom)
        self.assertEqual(row["status"], "unknown")
        self.assertIn("offline", row["note"])

    def test_missing_cli(self):
        row = versions.check_npm("codex", "@openai/codex", None, "latest", fetch=lambda p: {"latest": "1.0.0"})
        self.assertEqual(row["status"], "unknown")

    def test_agy_status_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "update_status.json"
            path.write_text('{"success":true,"message":"Already on the latest version."}', encoding="utf-8")
            self.assertEqual(versions.check_agy("1.5.11", path)["status"], "ok")
            self.assertEqual(versions.check_agy("1.5.11", Path(tmp) / "missing.json")["status"], "unknown")

    def test_outdated_shows_in_attention(self):
        report = {"models": {"versions": {"clis": [
            {"label": "Codex CLI", "installed": "0.164.0", "latest": "0.170.0", "status": "outdated",
             "update": "npm install -g @openai/codex@latest"}]}}}
        text = "\n".join(attention_items(report))
        self.assertIn("0.164.0 → 0.170.0", text)


def snap(status="ok", used=10.0, resets="2026-11-18T18:00:00+00:00"):
    return {"status": status, "buckets": {"codex.5h": {"used": used, "resets_at": resets},
                                          "codex.weekly": {"used": 40.0, "resets_at": "2026-11-25T00:00:00+00:00"}}}


class ClassifyTest(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(usage.classify(snap(used=10), snap(used=12), "success", 0), "clean")

    def test_stale(self):
        self.assertEqual(usage.classify(snap(status="stale"), snap(used=12), "success", 0), "stale_snapshot")

    def test_incomplete(self):
        self.assertEqual(usage.classify(snap(used=10), snap(used=12), "quota", 0), "incomplete")

    def test_window_reset(self):
        self.assertEqual(usage.classify(snap(used=90), snap(used=3, resets="2026-11-18T23:00:00+00:00"),
                                        "success", 0), "window_reset")
        self.assertEqual(usage.classify(snap(used=50), snap(used=20), "success", 0), "window_reset")

    def test_capped(self):
        self.assertEqual(usage.classify(snap(used=97), snap(used=100), "success", 0), "capped")

    def test_concurrent(self):
        self.assertEqual(usage.classify(snap(used=10), snap(used=12), "success", 2), "concurrent")

    def test_deltas(self):
        self.assertEqual(usage.deltas(snap(used=10), snap(used=12.5)), {"codex.5h": 2.5, "codex.weekly": 0.0})

    def test_snapshot_keeps_only_agent_buckets(self):
        provider = {"status": "ok", "buckets": [
            {"id": "gemini-5h", "used_percent": 8.0, "resets_at": "x"},
            {"id": "3p-5h", "used_percent": 0.0, "resets_at": "y"}]}
        self.assertEqual(set(usage.snapshot(provider, ["gemini-5h", "gemini-weekly"])["buckets"]), {"gemini-5h"})


class SessionActivityTest(unittest.TestCase):
    def test_counts_files_written_in_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old.jsonl"
            old.write_text("x")
            os.utime(old, (time.time() - 3600, time.time() - 3600))
            t0 = time.time() - 1
            (root / "sub").mkdir()
            (root / "sub" / "new.jsonl").write_text("y")
            self.assertEqual(usage.session_activity("codex", t0, time.time(), {"codex": root}), 1)
            self.assertIsNone(usage.session_activity("agy", t0, time.time(), {"codex": root}))


class RelaySampleTest(unittest.TestCase):
    def test_relay_writes_one_sample_per_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            task = {"name": "t", "prompt": "做事", "workdir": tmp, "order": ["a", "b"],
                    "min_remaining_percent": 20, "timeout_minutes": 1, "handover_on": "quota"}
            runners = {"a": {"exe": PY, "stdin": True, "args": ["-c", "print('usage limit reached'); raise SystemExit(1)"]},
                       "b": {"exe": PY, "stdin": True, "args": ["-c", "pass"]}}
            agents = {"a": {"level": "ok", "remaining_percent": 80, "resets_at": None},
                      "b": {"level": "ok", "remaining_percent": 80, "resets_at": None}}
            calls = iter([10.0, 11.0, 20.0, 21.5])

            def snapshot_fn(quota_id):
                return snap(used=next(calls))

            run = relay.run_task(task, agents, runners, {}, data, echo=lambda *_: None,
                                 snapshot_fn=snapshot_fn, activity_fn=lambda p, t0, t1: 1)
            self.assertEqual(run["outcome"], "success")
            lines = (data / "relay" / "usage_samples.jsonl").read_text(encoding="utf-8").splitlines()
            samples = [json.loads(x) for x in lines]
            self.assertEqual([s["agent"] for s in samples], ["a", "b"])
            self.assertEqual([s["status"] for s in samples], ["incomplete", "clean"])
            self.assertEqual(samples[1]["delta"]["codex.5h"], 1.5)

    def test_snapshot_failure_does_not_break_relay(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = {"name": "t", "prompt": "p", "workdir": tmp, "order": ["a"],
                    "min_remaining_percent": 20, "timeout_minutes": 1, "handover_on": "quota"}
            runners = {"a": {"exe": PY, "stdin": True, "args": ["-c", "pass"]}}

            def broken(_):
                raise RuntimeError("network down")

            run = relay.run_task(task, {"a": {"level": "ok", "remaining_percent": 90, "resets_at": None}},
                                 runners, {}, Path(tmp), echo=lambda *_: None, snapshot_fn=broken)
            self.assertEqual(run["outcome"], "success")
            sample = json.loads((Path(tmp) / "relay" / "usage_samples.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(sample["status"], "stale_snapshot")


if __name__ == "__main__":
    unittest.main()
