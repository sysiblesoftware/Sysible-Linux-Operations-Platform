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

# ---- container lifecycle -----------------------------------------------------
# Restart/stop/start, so an operator can recover a wedged service from the GUI
# instead of needing a shell on the host. Same discipline as an update: the
# action is a KEY from this table, never a string from the caller, and the argv
# is a literal. Nothing here interpolates anything a request supplied.
ACTIONS: dict[str, tuple[list[str], str]] = {
    "restart": (["docker", "compose", "restart"], "restarted"),
    "stop":    (["docker", "compose", "stop"], "stopped"),
    "start":   (["docker", "compose", "start"], "started"),
    # Recreate from the images already built — picks up a compose/env change
    # without the minutes a --build costs.
    "recreate": (["docker", "compose", "up", "-d"], "recreated"),
}

# Stopping the stack that serves this page would take the console, the gateway
# and this updater down together, leaving no way back except a shell on the host.
# Restart is fine — it comes back on its own.
_NO_STOP = {"slop"}


def action_refusal(key: str, action: str) -> str | None:
    """Why this action is not offered here, or None when it is allowed."""
    if action not in ACTIONS:
        return f"unknown action '{action}'"
    if action == "stop" and key in _NO_STOP:
        return ("stopping SLOP would take down the gateway, this console and the "
                "updater itself — there would be no way back except a shell on the "
                "host. Use Restart, or stop it from the host with 'sysible_ctl slop stop'.")
    return None


def _action_worker(key: str, compose: Path, argv: list[str], verb: str) -> None:
    try:
        rc = _run(argv, compose)
        if rc != 0:
            _set(state="failed", finished=time.time(),
                 message=f"{' '.join(argv[:3])} failed — see the log.")
            return
        _set(state="succeeded", finished=time.time(), message=f"{key} {verb}.")
    except Exception as e:                                   # pragma: no cover
        _set(state="failed", finished=time.time(), message=str(e)[:200])
    finally:
        _lock.release()


def start_action(key: str, action: str, compose: Path, actor: str) -> tuple[bool, str]:
    """Begin a lifecycle action. Shares the update lock: a restart during a
    `compose up --build` of the same stack would fight over container names."""
    global _job
    refusal = action_refusal(key, action)
    if refusal:
        return False, refusal
    argv, verb = ACTIONS[action]
    if not _lock.acquire(blocking=False):
        running = current() or {}
        return False, (f"An update of {running.get('app', 'another product')} is already "
                       "running — wait for it to finish.")
    with _job_lock:
        _job = {"app": key, "actor": actor, "state": "running", "action": action,
                "started": time.time(), "finished": None, "message": "", "log": [],
                # Restarting SLOP recreates THIS container mid-request, so the job
                # can never report its own success — the console must read a dropped
                # connection as "restarting", not "failed".
                "self_update": key == "slop"}
    threading.Thread(target=_action_worker, args=(key, compose, argv, verb),
                     name=f"{action}-{key}", daemon=True).start()
    return True, f"{action.capitalize()}ing {key}…"

def services(compose: Path) -> list[dict]:
    """Per-service state for one stack, so the console can show what is actually
    running rather than just offering buttons. Read-only and short-timeout: this
    is on the page-load path, so a slow daemon must degrade to an empty list, not
    hang Administration."""
    import json as _json
    try:
        p = subprocess.run(["docker", "compose", "ps", "--all", "--format", "json"],
                           cwd=str(compose), capture_output=True, text=True, timeout=15)
    except Exception:
        return []
    if p.returncode != 0:
        return []
    rows = []
    # compose emits either one JSON array or one object per line, by version.
    raw = (p.stdout or "").strip()
    try:
        parsed = _json.loads(raw)
        items = parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        items = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(_json.loads(line))
            except Exception:
                continue
    for it in items:
        if not isinstance(it, dict):
            continue
        rows.append({
            "service": it.get("Service") or it.get("Name") or "",
            "name": it.get("Name") or "",
            "state": (it.get("State") or "").lower(),
            "status": it.get("Status") or "",
            "health": (it.get("Health") or "").lower(),
        })
    rows.sort(key=lambda r: r["service"])
    return rows
