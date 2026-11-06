"""共用工具：時間轉換、額度桶格式、子程序呼叫。"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

WINDOW_LABELS = {"5h": "5 小時", "weekly": "每週"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def to_iso(value) -> str | None:
    """epoch 秒或 ISO 字串 → UTC ISO 字串；無法解析就回傳 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def window_from_minutes(minutes) -> str:
    if minutes == 300:
        return "5h"
    if minutes == 10080:
        return "weekly"
    return f"{minutes}m" if minutes else "unknown"


def make_bucket(bucket_id: str, label: str, window: str, *, used_percent=None,
                remaining_percent=None, resets_at=None, note: str | None = None) -> dict:
    """統一的額度桶格式。百分比只由來源換算，不補值。"""
    if used_percent is None and remaining_percent is not None:
        used_percent = 100 - remaining_percent
    if remaining_percent is None and used_percent is not None:
        remaining_percent = 100 - used_percent
    rnd = lambda x: None if x is None else round(float(x), 1)
    return {
        "id": bucket_id,
        "label": label,
        "window": window,
        "used_percent": rnd(used_percent),
        "remaining_percent": rnd(remaining_percent),
        "resets_at": to_iso(resets_at),
        "note": note,
    }


def provider_result(provider: str, label: str, source: str) -> dict:
    return {
        "provider": provider,
        "label": label,
        "status": "unavailable",  # ok / stale / unavailable
        "source": source,
        "checked_at": now_iso(),
        "plan": None,
        "buckets": [],
        "extra": {},
        "error": None,
        "notes": [],
    }


def find_exe(name: str, fallbacks: tuple[str, ...] = ()) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for path in fallbacks:
        path = os.path.expandvars(os.path.expanduser(path))
        if os.path.isfile(path):
            return path
    return None


def run_jsonl(cmd: list[str], messages: list[dict], want, timeout: float) -> dict:
    """啟動程序、逐行送出 JSON，回傳第一個讓 want(obj) 為真的輸出行，之後結束程序。"""
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW,
    )
    lines: queue.Queue = queue.Queue()
    err_tail: list[str] = []

    def read_out():
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    def read_err():
        for line in proc.stderr:
            err_tail.append(line.rstrip())
            del err_tail[:-5]

    threading.Thread(target=read_out, daemon=True).start()
    threading.Thread(target=read_err, daemon=True).start()
    try:
        for msg in messages:
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"{timeout:.0f} 秒內沒有回應")
            try:
                line = lines.get(timeout=min(left, 1.0))
            except queue.Empty:
                continue
            if line is None:
                tail = " / ".join(err_tail[-3:])
                raise RuntimeError(f"程序提前結束（exit {proc.poll()}）{('：' + tail) if tail else ''}")
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if want(obj):
                return obj
    finally:
        try:
            proc.kill()
        except OSError:
            pass


def run_json(cmd: list[str], timeout: float) -> dict:
    """執行一次性指令，取最後一行 JSON 物件。"""
    done = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
    )
    for line in reversed(done.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    tail = (done.stderr or done.stdout).strip().splitlines()[-3:]
    raise RuntimeError(f"沒有 JSON 輸出（exit {done.returncode}）：{' / '.join(tail)}")
