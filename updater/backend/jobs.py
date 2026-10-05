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

from . import detached, git

# Bound the log: a --build pours out a lot, and this lives in memory.
MAX_LOG_LINES = int(os.environ.get("SYSIBLE_UPDATER_MAX_LOG_LINES", "600"))
STEP_TIMEOUT = float(os.environ.get("SYSIBLE_UPDATER_STEP_TIMEOUT", "1800"))

_lock = threading.Lock()
_job: dict | None = None            # the current or most recent job
_job_lock = threading.Lock()


# Which products' jobs would kill the process running them. SLOP's compose
# project owns this container, so anything that recreates it is in this set.
SELF_AFFECTING = {"slop"}


def current() -> dict | None:
    """The job in flight, or the most recent one.

    A SLOP job runs in a container that outlives this process (see detached.py),
    and after it restarts us there is no in-memory job left to report — so the
    helper is asked. Without this the console loses every self-update the moment
    it succeeds, which is the one outcome it should be able to state.
    """
    with _job_lock:
        j = dict(_job) if _job else None
    if j is None or (j.get("detached") and j.get("state") == "running"):
        fresh = detached.status()
        if fresh is not None:
            _forget_after_detached_pull(fresh)
            return fresh
    return j


_forgotten: set[str] = set()


def _forget_after_detached_pull(job: dict) -> None:
    """The helper did the `git pull`, not us, so nothing in THIS process knows the
    checkout moved. Usually moot — the update restarts us and the memo starts
    empty — but a `recreate` that leaves this container alone would otherwise keep
    answering "update available" from a pre-pull memo, right after the operator
    watched the update succeed."""
    if job.get("state") != "succeeded" or job.get("action") is not None:
        return
    from . import apps
    root = apps.checkout_dir("slop")
    if root is None or str(root) in _forgotten:
        return
    _forgotten.add(str(root))
    git.forget_remote(root)


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


def _update_one(key: str, root: Path, compose: Path) -> tuple[bool, str]:
    """Pull one checkout forward and rebuild its containers. (ok, message).

    Factored out of _worker so the all-products run executes exactly the same
    steps — a queue that drifted from the single update would be a second code
    path nobody tests.
    """
    rc = _run(["git", "-C", str(root), "-c", f"safe.directory={root}",
               "pull", "--ff-only"], root)
    # The pull moved HEAD, so whatever was memoised about this checkout's remote
    # describes the world before it. Drop it either way: a failed pull may still
    # have fetched.
    git.forget_remote(root)
    if rc != 0:
        return False, ("git pull failed — the checkout may have local changes "
                       "or its branch may have diverged.")
    # --build so the image actually picks the new code up; -d so we return.
    rc = _run(["docker", "compose", "up", "-d", "--build"], compose)
    if rc != 0:
        return False, "docker compose up --build failed — see the log."
    return True, f"{key} updated and restarted."


def _worker(key: str, root: Path, compose: Path, actor: str) -> None:
    try:
        ok, message = _update_one(key, root, compose)
        _set(state="succeeded" if ok else "failed", finished=time.time(), message=message)
    except Exception as e:                                   # pragma: no cover
        _set(state="failed", finished=time.time(), message=str(e)[:200])
    finally:
        _lock.release()


def _queue_worker(items: list, actor: str) -> None:
    """Update several products, one after another, in this one job.

    One failure does not strand the rest. A host where SLEP's checkout has local
    edits should still get Connect and the Controller updated — stopping at the
    first problem is how "update everything" becomes "update nothing".
    """
    done = []
    try:
        for i, (key, root, compose) in enumerate(items):
            _set(app=key, index=i)
            if key in SELF_AFFECTING:
                # SLOP recreates this very container, so nothing can follow it and
                # nothing here can report the outcome — the detached helper owns
                # the job from this point (see detached.py and current()).
                started, message = detached.spawn(detached.SCRIPT_UPDATE, compose)
                done.append({"key": key, "ok": bool(started), "message": message})
                _set(done=list(done))
                if started:
                    with _job_lock:
                        globals()["_job"] = None     # current() reads the helper
                return
            try:
                ok, message = _update_one(key, root, compose)
            except Exception as e:                           # pragma: no cover
                ok, message = False, str(e)[:200]
            _log(("+ " if ok else "! ") + message)
            done.append({"key": key, "ok": ok, "message": message})
            _set(done=list(done))
        failed = [d["key"] for d in done if not d["ok"]]
        _set(state="failed" if failed else "succeeded", finished=time.time(),
             message=(f"{len(done) - len(failed)} of {len(done)} updated; "
                      f"{', '.join(failed)} failed — see the log."
                      if failed else
                      f"All {len(done)} updated and restarted."))
    except Exception as e:                                   # pragma: no cover
        _set(state="failed", finished=time.time(), message=str(e)[:200])
    finally:
        _lock.release()


def start(key: str, root: Path, compose: Path, actor: str) -> tuple[bool, str]:
    """Begin an update. Returns (started, message). Refuses while one is running."""
    global _job
    # The in-process lock cannot see a detached helper: it lives in another
    # container, and a restart of this one resets the lock while the work carries
    # on. Two `compose up --build` runs on the same project fight over container
    # names, so ask the helper first.
    if detached.running():
        return False, ("An update of slop is already running — wait for it to finish.")
    if not _lock.acquire(blocking=False):
        running = current() or {}
        return False, (f"An update of {running.get('app', 'another product')} is already "
                       "running — wait for it to finish.")
    if key in SELF_AFFECTING:
        started, message = detached.spawn(detached.SCRIPT_UPDATE, compose)
        _lock.release()                  # the helper is the lock from here on
        if not started:
            return False, f"could not start the update: {message}"
        with _job_lock:
            _job = None                  # current() reads the helper instead
        return True, ("Updating slop… this service restarts as part of it, so the "
                      "page will drop briefly.")
    with _job_lock:
        _job = {"app": key, "actor": actor, "state": "running",
                "started": time.time(), "finished": None, "message": "", "log": [],
                "self_update": False}
    threading.Thread(target=_worker, args=(key, root, compose, actor),
                     name=f"update-{key}", daemon=True).start()
    return True, f"Updating {key}…"

def start_many(items: list, actor: str) -> tuple[bool, str]:
    """Begin an update of several products, in the order given.

    One job, not several: the lock allows one at a time, so a client that fired
    them off in parallel would simply get refusals, and one that chained them from
    the browser would strand the rest the moment the tab was closed or reloaded.
    """
    global _job
    if not items:
        return False, "Nothing to update."
    if detached.running():
        return False, "An update of slop is already running — wait for it to finish."
    if not _lock.acquire(blocking=False):
        running = current() or {}
        return False, (f"An update of {running.get('app', 'another product')} is already "
                       "running — wait for it to finish.")
    keys = [k for k, _r, _c in items]
    with _job_lock:
        _job = {"app": keys[0], "actor": actor, "state": "running",
                "started": time.time(), "finished": None, "message": "", "log": [],
                "self_update": False, "queue": keys, "index": 0, "done": []}
    threading.Thread(target=_queue_worker, args=(items, actor),
                     name="update-all", daemon=True).start()
    return True, f"Updating {len(keys)} products…"


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
                "host. Use Restart, or stop it from the host with 'sysiblectl slop stop'.")
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
    if detached.running():
        return False, "An update of slop is already running — wait for it to finish."
    if not _lock.acquire(blocking=False):
        running = current() or {}
        return False, (f"An update of {running.get('app', 'another product')} is already "
                       "running — wait for it to finish.")
    # Restarting or recreating SLOP takes this container down with it, exactly as
    # an update does: `compose restart` stops every service in the project, and
    # the client asking for it is inside one of them. Same hand-off.
    if key in SELF_AFFECTING:
        script = detached.SCRIPT_ACTIONS.get(action)
        if script is None:                                   # pragma: no cover
            _lock.release()
            return False, f"'{action}' cannot be run on slop from here."
        started, message = detached.spawn(script, compose, action)
        _lock.release()
        if not started:
            return False, f"could not start {action}: {message}"
        with _job_lock:
            _job = None
        return True, (f"{action.capitalize()}ing slop… this service restarts as part "
                      "of it, so the page will drop briefly.")
    with _job_lock:
        _job = {"app": key, "actor": actor, "state": "running", "action": action,
                "started": time.time(), "finished": None, "message": "", "log": [],
                "self_update": False}
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
