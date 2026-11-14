"""cue relay：排程任務的接力執行。

任務寫在 relay.json。執行時先查額度，照任務的順序挑第一個有額度的代理執行；
跑到一半額度用完，就換下一個代理接手。本模組本身不呼叫任何模型，只負責調度。

結束碼：0 成功、1 任務本身失敗（不是額度問題）、2 沒有代理有額度、3 設定錯誤或任務正在執行中。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .common import CREATE_NO_WINDOW, find_exe, now_iso, now_utc, parse_iso
from .report import LEVEL_TEXT, fmt_pct, fmt_time

# 預設的執行方式。{prompt} 與 {workdir} 會被替換；stdin=True 表示提示詞從標準輸入送進去
# （Windows 上的 codex 是 .cmd 批次檔，從參數傳多行文字會被改壞）。
DEFAULT_RUNNERS = {
    "claude": {"exe": "claude", "stdin": True,
               "args": ["-p", "--permission-mode", "acceptEdits", "--output-format", "text"]},
    "luna": {"exe": "codex", "stdin": True,
             "args": ["exec", "--skip-git-repo-check", "-s", "workspace-write",
                      "-m", "gpt-6-luna", "-C", "{workdir}"]},
    "flash": {"exe": "agy", "stdin": False,
              "args": ["-p", "{prompt}", "--model", "gemini-3.8-flash-medium", "--mode", "accept-edits"]},
    "argon": {"exe": "agy", "stdin": False,
              "args": ["-p", "{prompt}", "--model", "gemini-4-argon-medium", "--effort", "medium",
                       "--mode", "accept-edits"]},
}
EXE_FALLBACKS = {"agy": (r"%LOCALAPPDATA%\agy\bin\agy.exe",)}

# 三家 CLI 額度用完時的訊息（2026-11 從各 CLI 的紀錄與執行檔確認）
QUOTA_PATTERNS = re.compile(
    r"usage limit reached|hit your usage limit|reached your (?:\w+ )?usage limit"
    r"|Individual quota reached|exhausted your capacity|quota will reset|rate limit reached",
    re.IGNORECASE)

# 不要叫接手的代理「先檢查資料夾」：agy 無人模式下列目錄要用 shell 指令，會被權限擋下（2026-11-14 實測）
HANDOVER_NOTE = ("\n\n（接力說明：上一位代理 {prev} 因額度用完而中斷，這個任務改由你完成。"
                 "工作資料夾裡可能有它留下的部分結果，要覆蓋或沿用都可以。）")

# 無人模式下需要人按「允許」的工具會被自動拒絕。agy 這時結束碼仍是 0，只在 stderr 留訊息。
PERMISSION_PATTERNS = re.compile(r"auto-denied|cannot prompt for|requires? approval|permission denied by",
                                 re.IGNORECASE)

EXIT_OK, EXIT_TASK_FAILED, EXIT_NO_AGENT, EXIT_CONFIG = 0, 1, 2, 3


class ConfigError(Exception):
    pass


# ---------- 設定 ----------

def load_task(config: dict, name: str, base: Path) -> dict:
    tasks = config.get("tasks") or {}
    if name not in tasks:
        raise ConfigError(f"relay.json 裡沒有任務「{name}」（現有：{', '.join(tasks) or '無'}）")
    task = dict(tasks[name])
    task["name"] = name
    if "prompt_file" in task:
        path = Path(task["prompt_file"])
        path = path if path.is_absolute() else base / path
        try:
            task["prompt"] = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"讀不到 prompt_file：{exc}") from exc
    if not task.get("prompt"):
        raise ConfigError(f"任務「{name}」沒有 prompt 或 prompt_file")
    workdir = Path(task.get("workdir") or ".")
    task["workdir"] = str(workdir if workdir.is_absolute() else (base / workdir).resolve())
    if not Path(task["workdir"]).is_dir():
        raise ConfigError(f"工作資料夾不存在：{task['workdir']}")
    if not task.get("order"):
        raise ConfigError(f"任務「{name}」沒有 order（代理順序）")
    task.setdefault("min_remaining_percent", 20)
    task.setdefault("timeout_minutes", 30)
    task.setdefault("handover_on", "quota")
    if task["handover_on"] not in ("quota", "any_failure"):
        raise ConfigError("handover_on 只能是 quota 或 any_failure")
    return task


def runners_from(config: dict) -> dict:
    runners = {k: dict(v) for k, v in DEFAULT_RUNNERS.items()}
    for key, val in (config.get("runners") or {}).items():
        runners[key] = {**runners.get(key, {}), **val}
    return runners


def build_command(runner: dict, prompt: str, workdir: str, cfg: dict) -> tuple[list[str], str | None]:
    exe_name = runner["exe"]
    exe = find_exe(exe_name, tuple(cfg.get(f"{exe_name}_paths", [])) + EXE_FALLBACKS.get(exe_name, ()))
    if not exe:
        raise ConfigError(f"找不到 {exe_name} 執行檔")
    args = [a.replace("{workdir}", workdir) for a in runner.get("args", [])]
    if runner.get("stdin"):
        return [exe, *args], prompt
    if not any("{prompt}" in a for a in args):
        raise ConfigError(f"{exe_name} 的 args 沒有 {{prompt}}，又沒有設定 stdin")
    return [a.replace("{prompt}", prompt) for a in [exe, *args]], None


# ---------- 選人 ----------

def plan_order(task: dict, agents: dict, runners: dict) -> tuple[list[str], list[dict]]:
    """回傳（上場順序, 被跳過的代理與原因）。

    第一輪：剩餘量達到門檻的代理。第二輪：偏低或讀不到額度的代理（可能還能跑）。
    已用完的代理不上場。
    """
    first, second, skipped = [], [], []
    for aid in task["order"]:
        if aid not in runners:
            skipped.append({"agent": aid, "reason": "沒有這個代理的執行方式（runners）"})
            continue
        quota_id = runners[aid].get("quota_agent", aid)
        info = agents.get(quota_id)
        if info is None or info["level"] == "unknown":
            second.append(aid)
            continue
        if info["level"] == "exhausted":
            skipped.append({"agent": aid, "reason": f"額度用完，{fmt_time(info['resets_at'])} 重置"})
        elif info["remaining_percent"] is not None and info["remaining_percent"] >= task["min_remaining_percent"]:
            first.append(aid)
        else:
            second.append(aid)
    return first + second, skipped


# ---------- 執行 ----------

def execute(cmd: list[str], stdin_text: str | None, workdir: str, timeout: float) -> dict:
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd, cwd=workdir, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", creationflags=CREATE_NO_WINDOW)
    timed_out = False
    try:
        out, err = proc.communicate(stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        out, err = proc.communicate()
    return {"exit_code": proc.returncode, "stdout": out or "", "stderr": err or "",
            "timed_out": timed_out, "seconds": round(time.monotonic() - started, 1)}


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                       creationflags=CREATE_NO_WINDOW)
    else:
        proc.kill()


def hit_quota(result: dict) -> bool:
    return bool(QUOTA_PATTERNS.search(result["stdout"][-20000:] + "\n" + result["stderr"][-20000:]))


def blocked_by_permission(result: dict) -> bool:
    return bool(PERMISSION_PATTERNS.search(result["stderr"][-5000:]))


def succeeded(result: dict, task: dict, started_at: float) -> bool:
    if result["exit_code"] != 0 or result["timed_out"]:
        return False
    if blocked_by_permission(result) and not result["stdout"].strip():
        return False
    target = task.get("success_file")
    if target:
        path = Path(task["workdir"]) / target
        return path.exists() and path.stat().st_mtime >= started_at - 1
    return True


class Lock:
    """同一個任務同時只跑一份；超過任務逾時兩倍的鎖視為殘留。"""

    def __init__(self, path: Path, stale_seconds: float):
        self.path, self.stale = path, stale_seconds

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime < self.stale:
            raise ConfigError(f"任務正在執行中（{self.path.name} 存在）")
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass


def run_task(task: dict, agents: dict, runners: dict, cfg: dict, data_dir: Path, *,
             dry_run: bool = False, echo=print, execute_fn=execute, recheck=None) -> dict:
    """依額度選代理並執行。recheck(agent_id) 回傳最新的代理額度（用來判斷失敗是不是因為額度用完）。"""
    order, skipped = plan_order(task, agents, runners)
    run = {"task": task["name"], "started_at": now_iso(), "dry_run": dry_run,
           "skipped": skipped, "attempts": [], "outcome": None, "agent": None}
    for s in skipped:
        echo(f"  跳過 {s['agent']}：{s['reason']}")
    if not order:
        run["outcome"] = "no_agent"
        echo("沒有代理有額度，這次不執行。")
        return run

    log_dir = data_dir / "relay" / "logs"
    prompt, prev = task["prompt"], None
    for aid in order:
        info = agents.get(runners[aid].get("quota_agent", aid))
        state = (f"{LEVEL_TEXT[info['level']]}，剩 {fmt_pct(info['remaining_percent'])}"
                 if info else "額度未知")
        this_prompt = prompt + (HANDOVER_NOTE.format(prev=prev) if prev else "")
        cmd, stdin_text = build_command(runners[aid], this_prompt, task["workdir"], cfg)
        if dry_run:
            echo(f"  [{len(run['attempts']) + 1}] {aid}（{state}）："
                 f"{' '.join(cmd[:1] + [a if len(a) < 60 else a[:57] + '...' for a in cmd[1:]])}"
                 f"{'  ＜提示詞從標準輸入送入' if stdin_text is not None else ''}")
            run["attempts"].append({"agent": aid, "state": state})
            continue

        echo(f"→ {aid} 上場（{state}）")
        started = time.time()
        result = execute_fn(cmd, stdin_text, task["workdir"], task["timeout_minutes"] * 60)
        attempt = {"agent": aid, "exit_code": result["exit_code"], "seconds": result["seconds"],
                   "timed_out": result["timed_out"]}
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{datetime.now():%Y%m%d-%H%M%S}_{task['name']}_{aid}.log"
        log_path.write_text(f"$ {' '.join(cmd[:1] + cmd[1:6])} ...\n\n--- stdout ---\n{result['stdout']}"
                            f"\n--- stderr ---\n{result['stderr']}", encoding="utf-8")
        attempt["log"] = str(log_path)

        if succeeded(result, task, started):
            attempt["result"] = "success"
            run["attempts"].append(attempt)
            run["outcome"], run["agent"] = "success", aid
            echo(f"✓ {aid} 完成（{result['seconds']} 秒）")
            return run

        quota = hit_quota(result)
        if not quota and recheck is not None:
            latest = recheck(runners[aid].get("quota_agent", aid))
            quota = bool(latest and latest["level"] == "exhausted")
        if quota:
            attempt["result"] = "quota"
        elif result["timed_out"]:
            attempt["result"] = "timeout"
        elif blocked_by_permission(result):
            attempt["result"] = "permission"
        else:
            attempt["result"] = "failed"
        run["attempts"].append(attempt)
        reason = {"timeout": f"超過 {task['timeout_minutes']} 分鐘",
                  "permission": "需要的工具權限在無人模式下被拒絕",
                  "failed": f"結束碼 {result['exit_code']}"
                  + ("，沒有寫出 success_file" if result["exit_code"] == 0 else "")}.get(attempt["result"])
        if quota:
            echo(f"✗ {aid} 額度用完，換下一位")
            prev = aid
            continue
        if task["handover_on"] == "any_failure":
            echo(f"✗ {aid} 失敗（{reason}），換下一位")
            prev = aid
            continue
        run["outcome"], run["agent"] = "failed", aid
        echo(f"✗ {aid} 失敗（{reason}），不是額度問題，停止接力。紀錄：{log_path}")
        return run

    if dry_run:
        run["outcome"] = "dry_run"
        return run
    all_quota = all(a.get("result") == "quota" for a in run["attempts"])
    run["outcome"] = "no_agent" if all_quota else "failed"
    echo("所有代理都試過了，沒有人完成。")
    return run


def next_reset(task: dict, agents: dict, runners: dict) -> str | None:
    times = []
    for aid in task["order"]:
        info = agents.get(runners.get(aid, {}).get("quota_agent", aid))
        dt = parse_iso((info or {}).get("resets_at"))
        if dt and dt > now_utc():
            times.append(dt)
    return min(times).isoformat(timespec="seconds") if times else None


# ---------- 指令 ----------

def main(argv: list[str], *, root: Path, get_agents, recheck_agent=None, load_json=None) -> int:
    ap = argparse.ArgumentParser(prog="cue relay", description="依額度接力執行 relay.json 裡的任務")
    ap.add_argument("task", nargs="?", help="任務名稱")
    ap.add_argument("--config", default=str(root / "relay.json"), help="任務設定檔（預設 relay.json）")
    ap.add_argument("--list", action="store_true", help="列出任務")
    ap.add_argument("--dry-run", action="store_true", help="只顯示會派給誰、怎麼執行，不真的跑")
    args = ap.parse_args(argv)

    config_path = Path(args.config)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError:
        print(f"找不到 {config_path}。請把 relay.example.json 複製成 relay.json 再編輯。")
        return EXIT_CONFIG
    except json.JSONDecodeError as exc:
        print(f"{config_path.name} 格式錯誤：{exc}")
        return EXIT_CONFIG

    if args.list or not args.task:
        for name, t in (config.get("tasks") or {}).items():
            print(f"{name}：{t.get('description', '')}　順序 {' → '.join(t.get('order', []))}")
        return EXIT_OK

    cfg = load_json(root / "config.json", {}) if load_json else {}
    runners = runners_from(config)
    try:
        task = load_task(config, args.task, config_path.parent)
        lock = Lock(root / "data" / "relay" / f"{args.task}.lock", task["timeout_minutes"] * 120)
        with (lock if not args.dry_run else _NoLock()):
            print(f"任務 {args.task}　{now_utc().astimezone():%m/%d %H:%M}")
            agents = get_agents(task["order"], runners)
            run = run_task(task, agents, runners, cfg, root / "data", dry_run=args.dry_run,
                           recheck=recheck_agent)
    except ConfigError as exc:
        print(f"設定錯誤：{exc}")
        return EXIT_CONFIG

    if run["outcome"] == "no_agent":
        reset = next_reset(task, agents, runners)
        run["next_reset"] = reset
        if reset:
            print(f"最早 {fmt_time(reset)} 有代理重置額度。")
    if not args.dry_run:
        history = root / "data" / "relay" / "history.jsonl"
        history.parent.mkdir(parents=True, exist_ok=True)
        run["finished_at"] = now_iso()
        with history.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(run, ensure_ascii=False) + "\n")
    return {"success": EXIT_OK, "dry_run": EXIT_OK, "failed": EXIT_TASK_FAILED,
            "no_agent": EXIT_NO_AGENT}[run["outcome"]]


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
