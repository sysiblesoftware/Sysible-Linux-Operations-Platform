"""Running one update at a time, with a readable log.

An update is `git pull --ff-only` followed by `docker compose up -d --build` — a
long job (a rebuild can take minutes) that the browser must not block on. So it
runs on a worker thread and the console polls; the job's output is streamed into
a bounded in-memory buffer it can tail.

ONE AT A TIME, globally. Two concurrent `compose up --build` runs on the same
host fight over the daemon, the build cache and the container names, and a second
update of the SAME product would race the first one's git pull. A single lock is
both simpler and correct.

Nothing here takes a command from a caller: the argv lists are literals and the
only variable is a directory chosen by apps.py from the allowlist.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

# Bound the log: a --build pours out a lot, and this lives in memory.
MAX_LOG_LINES = int(os.environ.get("SYSIBLE_UPDATER_MAX_LOG_LINES", "600"))
STEP_TIMEOUT = float(os.environ.get("SYSIBLE_UPDATER_STEP_TIMEOUT", "1800"))

_lock = threading.Lock()
_job: dict | None = None            # the current or most recent job
_job_lock = threading.Lock()


def current() -> dict | None:
    with _job_lock:
        return dict(_job) if _job else None


def _set(**fields) -> None:
    with _job_lock:
        if _job is not None:
            _job.update(fields)


def _log(line: str) -> None:
    with _job_lock:
        if _job is None:
            return
        buf = _job["log"]
        buf.append(line.rstrip("\n"))
        if len(buf) > MAX_LOG_LINES:
            del buf[:len(buf) - MAX_LOG_LINES]


def _run(argv: list[str], cwd: Path) -> int:
    """Run one step, streaming its output into the job log. Returns the exit code."""
    _log(f"$ {' '.join(argv)}")
    try:
        p = subprocess.Popen(argv, cwd=str(cwd), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
    except FileNotFoundError as e:
        _log(f"! {e}")
        return 127
    try:
        assert p.stdout is not None
        for line in p.stdout:
            _log(line)
        return p.wait(timeout=STEP_TIMEOUT)
    except subprocess.TimeoutExpired:
        p.kill()
        _log(f"! step timed out after {STEP_TIMEOUT:g}s")
        return 124


def _worker(key: str, root: Path, compose: Path, actor: str) -> None:
    try:
        rc = _run(["git", "-C", str(root), "-c", f"safe.directory={root}",
                   "pull", "--ff-only"], root)
        if rc != 0:
            _set(state="failed", finished=time.time(),
                 message="git pull failed — the checkout may have local changes "
                         "or its branch may have diverged.")
            return
        # --build so the image actually picks the new code up; -d so we return.
        rc = _run(["docker", "compose", "up", "-d", "--build"], compose)
        if rc != 0:
            _set(state="failed", finished=time.time(),
                 message="docker compose up --build failed — see the log.")
            return
        _set(state="succeeded", finished=time.time(),
             message=f"{key} updated and restarted.")
    except Exception as e:                                   # pragma: no cover
        _set(state="failed", finished=time.time(), message=str(e)[:200])
    finally:
        _lock.release()


def start(key: str, root: Path, compose: Path, actor: str) -> tuple[bool, str]:
    """Begin an update. Returns (started, message). Refuses while one is running."""
    global _job
    if not _lock.acquire(blocking=False):
        running = current() or {}
        return False, (f"An update of {running.get('app', 'another product')} is already "
                       "running — wait for it to finish.")
    with _job_lock:
        _job = {"app": key, "actor": actor, "state": "running",
                "started": time.time(), "finished": None, "message": "", "log": [],
                # Updating SLOP means `compose up` recreates THIS container part way
                # through, so the job can never report its own success. Flag it so the
                # console reads a dropped connection as "restarting", not "failed".
                "self_update": key == "slop"}
    threading.Thread(target=_worker, args=(key, root, compose, actor),
                     name=f"update-{key}", daemon=True).start()
    return True, f"Updating {key}…"
