import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cuesheet import relay  # noqa: E402

PY = sys.executable


def agent(level, remaining, resets_at="2099-01-01T00:00:00+00:00"):
    return {"level": level, "remaining_percent": remaining, "resets_at": resets_at}


def fake_runners(*ids):
    # 每個代理都用 python 本身當執行檔，提示詞從標準輸入送入
    return {i: {"exe": PY, "stdin": True, "args": ["-c", "pass"]} for i in ids}


class FakeExecute:
    """依代理名稱回傳預先設定的結果，並記下每次收到的提示詞。"""

    def __init__(self, results):
        self.results, self.calls = results, []

    def __call__(self, cmd, stdin_text, workdir, timeout):
        aid = self.next_agent
        self.calls.append((aid, stdin_text))
        exit_code, out = self.results[aid]
        return {"exit_code": exit_code, "stdout": out, "stderr": "", "timed_out": False, "seconds": 0.1}


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.task = {"name": "t", "prompt": "做事", "workdir": str(self.dir), "order": ["a", "b", "c"],
                     "min_remaining_percent": 20, "timeout_minutes": 1, "handover_on": "quota"}
        self.runners = fake_runners("a", "b", "c")

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, agents, results, **kw):
        fake = FakeExecute(results)
        order, _ = relay.plan_order(self.task, agents, self.runners)

        def execute(cmd, stdin_text, workdir, timeout):
            fake.next_agent = order[len(fake.calls)]
            return fake(cmd, stdin_text, workdir, timeout)

        run = relay.run_task(self.task, agents, self.runners, {}, self.dir, echo=lambda *_: None,
                             execute_fn=execute, **kw)
        return run, fake

    def test_plan_order(self):
        agents = {"a": agent("exhausted", 0), "b": agent("low", 10), "c": agent("ok", 90)}
        order, skipped = relay.plan_order(self.task, agents, self.runners)
        self.assertEqual(order, ["c", "b"])
        self.assertEqual([s["agent"] for s in skipped], ["a"])

    def test_unknown_quota_goes_last(self):
        agents = {"a": agent("unknown", None), "b": agent("ok", 50)}
        self.task["order"] = ["a", "b"]
        order, _ = relay.plan_order(self.task, agents, self.runners)
        self.assertEqual(order, ["b", "a"])

    def test_handover_on_quota(self):
        agents = {"a": agent("ok", 80), "b": agent("ok", 60), "c": agent("ok", 50)}
        run, fake = self.run_with(agents, {"a": (1, "Error: You've hit your usage limit."), "b": (0, "done")})
        self.assertEqual(run["outcome"], "success")
        self.assertEqual(run["agent"], "b")
        self.assertEqual([c[0] for c in fake.calls], ["a", "b"])
        self.assertNotIn("接力說明", fake.calls[0][1])
        self.assertIn("接力說明", fake.calls[1][1])
        self.assertIn("a", fake.calls[1][1])

    def test_task_failure_stops(self):
        agents = {"a": agent("ok", 80), "b": agent("ok", 60), "c": agent("ok", 50)}
        run, fake = self.run_with(agents, {"a": (1, "SyntaxError")})
        self.assertEqual(run["outcome"], "failed")
        self.assertEqual(len(fake.calls), 1)

    def test_recheck_detects_quota(self):
        agents = {"a": agent("ok", 3), "b": agent("ok", 60), "c": agent("ok", 50)}
        run, fake = self.run_with(agents, {"a": (1, "connection closed"), "b": (0, "done")},
                                  recheck=lambda aid: agent("exhausted", 0))
        self.assertEqual(run["agent"], "b")

    def test_any_failure_hands_over(self):
        self.task["handover_on"] = "any_failure"
        agents = {"a": agent("ok", 80), "b": agent("ok", 60), "c": agent("ok", 50)}
        run, _ = self.run_with(agents, {"a": (1, "SyntaxError"), "b": (0, "done")})
        self.assertEqual(run["agent"], "b")

    def test_all_out_of_quota(self):
        agents = {"a": agent("ok", 80), "b": agent("ok", 60), "c": agent("ok", 50)}
        msg = (1, "Individual quota reached. Resets in 4h.")
        run, fake = self.run_with(agents, {"a": msg, "b": msg, "c": msg})
        self.assertEqual(run["outcome"], "no_agent")
        self.assertEqual(len(fake.calls), 3)

    def test_nobody_has_quota(self):
        agents = {k: agent("exhausted", 0) for k in "abc"}
        run, fake = self.run_with(agents, {})
        self.assertEqual(run["outcome"], "no_agent")
        self.assertEqual(fake.calls, [])

    def test_success_file_must_be_written(self):
        self.task["success_file"] = "out.md"
        agents = {"a": agent("ok", 80), "b": agent("ok", 60), "c": agent("ok", 50)}
        run, _ = self.run_with(agents, {"a": (0, "done but wrote nothing"), "b": (0, "done")})
        self.assertEqual(run["outcome"], "failed")

    def test_permission_denied_is_not_success(self):
        result = {"exit_code": 0, "stdout": "", "timed_out": False, "seconds": 1,
                  "stderr": 'jetski: no output produced — a tool required the "command" permission '
                            "that headless mode cannot prompt for, so it was auto-denied."}
        self.assertFalse(relay.succeeded(result, self.task, time.time()))
        self.assertTrue(relay.blocked_by_permission(result))

    def test_real_process(self):
        # 真的啟動子程序：用 python 讀標準輸入的提示詞，寫出檔案
        self.task.update(order=["a"], success_file="out.txt")
        self.runners = {"a": {"exe": PY, "stdin": True,
                              "args": ["-c", "import sys; open('out.txt','w',encoding='utf-8').write(sys.stdin.read())"]}}
        run = relay.run_task(self.task, {"a": agent("ok", 90)}, self.runners, {}, self.dir, echo=lambda *_: None)
        self.assertEqual(run["outcome"], "success")
        self.assertEqual((self.dir / "out.txt").read_text(encoding="utf-8"), "做事")

    def test_build_command_argv(self):
        cmd, stdin = relay.build_command({"exe": PY, "args": ["-p", "{prompt}", "-C", "{workdir}"]},
                                         "多行\n提示", "W", {})
        self.assertEqual(cmd[1:], ["-p", "多行\n提示", "-C", "W"])
        self.assertIsNone(stdin)

    def test_build_command_needs_prompt(self):
        with self.assertRaises(relay.ConfigError):
            relay.build_command({"exe": PY, "args": ["-x"]}, "p", "W", {})

    def test_lock(self):
        path = self.dir / "t.lock"
        with relay.Lock(path, 60):
            with self.assertRaises(relay.ConfigError):
                with relay.Lock(path, 60):
                    pass
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
