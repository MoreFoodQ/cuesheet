"""子程序逾時、agy 重試、即時查詢失敗時退回上次結果。"""
import importlib.util
import json
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cuesheet import common, gemini, models  # noqa: E402

spec = importlib.util.spec_from_file_location("cue_main", ROOT / "cue.py")
cue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cue)

PY = sys.executable
# 父程序開一個繼承輸出管線、會活 8 秒的子程序（模擬 agy 的背景更新程序）
SPAWN_CHILD = ("import subprocess,sys;"
               "subprocess.Popen([sys.executable,'-c','import time;time.sleep(8)']);")


def fixture(name):
    return json.loads((ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8"))


class RunCaptureTest(unittest.TestCase):
    def test_output(self):
        code, out, err = common.run_capture([PY, "-c", "print('{\"a\": 1}')"], 30)
        self.assertEqual(code, 0)
        self.assertEqual(common.last_json_line(out), {"a": 1})

    def test_timeout_returns_even_if_child_holds_pipe(self):
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            common.run_capture([PY, "-c", SPAWN_CHILD + "import time;time.sleep(30)"], 2)
        self.assertLess(time.monotonic() - start, 7)

    def test_parent_exits_but_child_keeps_pipe(self):
        start = time.monotonic()
        code, out, _ = common.run_capture([PY, "-c", SPAWN_CHILD + "print('{\"ok\": true}', flush=True)"], 30)
        self.assertLess(time.monotonic() - start, common.PIPE_GRACE + 3)
        self.assertEqual(common.last_json_line(out), {"ok": True})

    def test_run_json_without_json(self):
        with self.assertRaises(RuntimeError):
            common.run_json([PY, "-c", "print('hello')"], 30)


class AgyRetryTest(unittest.TestCase):
    def setUp(self):
        self.orig = (gemini.run_json, gemini.find_exe, models.run_capture, models.find_exe)
        gemini.find_exe = models.find_exe = lambda *a, **k: "agy"

    def tearDown(self):
        gemini.run_json, gemini.find_exe, models.run_capture, models.find_exe = self.orig

    def test_usage_retries_once(self):
        calls = []

        def flaky(cmd, timeout):
            calls.append(1)
            if len(calls) == 1:
                raise common.CommandTimeout("90 秒內沒有結束")
            return fixture("agy_usage.json")
        gemini.run_json = flaky
        res = gemini.collect({}, 90)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("重試後成功" in n for n in res["notes"]))

    def test_usage_gives_up_after_two(self):
        def always(cmd, timeout):
            raise common.CommandTimeout("90 秒內沒有結束")
        gemini.run_json = always
        res = gemini.collect({}, 90)
        self.assertEqual(res["status"], "unavailable")
        self.assertIn("試了 2 次", res["error"])
        self.assertEqual(res["error"].count("90 秒內沒有結束"), 1)

    def test_models_retries_once(self):
        calls = []

        def flaky(cmd, timeout):
            calls.append(1)
            if len(calls) == 1:
                return 1, "", "Fetching available models..."
            return 0, "gemini-4-neon-medium\tGemini 4 Neon (Medium)\n", ""
        models.run_capture = flaky
        self.assertEqual(models.list_agy({}, 30)[0]["id"], "gemini-4-neon-medium")
        self.assertEqual(len(calls), 2)


class FallbackTest(unittest.TestCase):
    NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    def prev(self, checked_hours_ago, status="ok"):
        checked = (self.NOW - timedelta(hours=checked_hours_ago)).isoformat()
        return {"providers": {"gemini": {
            "provider": "gemini", "status": status, "checked_at": checked, "extra": {}, "error": None, "notes": [],
            "buckets": [
                {"id": "w", "label": "每週", "window": "weekly", "used_percent": 85, "remaining_percent": 15,
                 "resets_at": (self.NOW + timedelta(days=3)).isoformat(), "note": None},
                {"id": "5h", "label": "5 小時", "window": "5h", "used_percent": 40, "remaining_percent": 60,
                 "resets_at": (self.NOW - timedelta(minutes=5)).isoformat(), "note": None}]}}}

    def failed(self):
        return {"gemini": {"provider": "gemini", "status": "unavailable", "checked_at": self.NOW.isoformat(),
                           "buckets": [], "extra": {}, "error": "呼叫 agy 失敗", "notes": []}}

    def test_uses_recent_result(self):
        out = cue.fall_back_to_previous(self.failed(), self.prev(2), now=self.NOW)["gemini"]
        self.assertEqual(out["status"], "stale")
        self.assertIn("呼叫 agy 失敗", out["error"])
        weekly, five = out["buckets"]
        self.assertEqual(weekly["remaining_percent"], 15)
        self.assertIsNone(five["remaining_percent"])  # 已過重置時間，不拿舊數字
        self.assertIn("observed_at", out["extra"])

    def test_too_old_is_ignored(self):
        out = cue.fall_back_to_previous(self.failed(), self.prev(30), now=self.NOW)["gemini"]
        self.assertEqual(out["status"], "unavailable")

    def test_stale_chain_keeps_first_time(self):
        prev = self.prev(1, status="stale")
        prev["providers"]["gemini"]["extra"]["observed_at"] = (self.NOW - timedelta(hours=30)).isoformat()
        out = cue.fall_back_to_previous(self.failed(), prev, now=self.NOW)["gemini"]
        self.assertEqual(out["status"], "unavailable")

    def test_ok_is_untouched(self):
        cur = {"gemini": {**self.failed()["gemini"], "status": "ok"}}
        self.assertIs(cue.fall_back_to_previous(cur, self.prev(1), now=self.NOW)["gemini"], cur["gemini"])

    def test_no_previous(self):
        self.assertEqual(cue.fall_back_to_previous(self.failed(), None, now=self.NOW)["gemini"]["status"],
                         "unavailable")


if __name__ == "__main__":
    unittest.main()
