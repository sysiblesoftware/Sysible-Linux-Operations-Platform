"""Running a job that will kill the process running it.

SLOP updates itself. `docker compose up -d --build` in this checkout recreates
every service in the project — and the updater IS one of those services. The
compose client doing the work is a child of this process, inside that container,
so the moment compose gets to `updater` the daemon stops the container and takes
the client down with it, mid-run.

The code already knew the job could not report its own success: `self_update` on
the job exists so the console reads the dropped connection as "restarting"
instead of "failed". What it did not do was make the WORK survive. Whatever
compose had reached is where the stack stays, and the window it dies in is not
academic: compose stops and removes a container before creating its replacement,
so a kill landing in that gap leaves a service with no container at all. Lose the
gateway that way and the platform has no front door — and the console that would
have told you is behind it.

So a self-affecting job does not run here. It runs in a throwaway container
started with `docker run`, which is NOT part of the compose project and which
compose therefore never touches. It keeps working while this container is
replaced underneath it, and because it outlives us, its log and exit code are
still there to be read when we come back — which is how the console can report
how an update went, rather than simply losing it.

SAFETY. The same rule as everywhere else in this service: nothing here takes a
path, an image or a command from a caller. The scripts are module constants with
no interpolation at all, and the only directory involved is the one apps.py
derived from the allowlist.
"""
from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

# One name, reused: a helper from a previous run is removed before a new one
# starts, so its log survives exactly until the next job needs the name.
HELPER = "sysible-slop-selfupdate"

# CONSTANTS. No f-strings, no interpolation, nothing derived from a request.
# `-w` puts us in the checkout, so git needs no path; safe.directory='*' because
# the checkout is owned by whoever cloned it on the host, not by this container.
SCRIPT_UPDATE = ("set -e; "
                 "git -c safe.directory='*' pull --ff-only; "
                 "exec docker compose up -d --build")
SCRIPT_ACTIONS = {
    "restart":  "exec docker compose restart",
    "start":    "exec docker compose start",
    "stop":     "exec docker compose stop",
    "recreate": "exec docker compose up -d",
}

# How long a finished helper's result is worth reporting. Past this the console
# would be showing the outcome of an update nobody is still waiting on.
RESULT_TTL = float(os.environ.get("SYSIBLE_UPDATER_RESULT_TTL", "900"))
_STATUS_TTL = 3.0
_status_cache: tuple[float, dict | None] = (0.0, None)


def _docker(*args: str, timeout: float = 20) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          timeout=timeout)


def _own_image() -> str | None:
    """The image THIS container runs, so the helper is the same code we are.

    Asked of the daemon rather than hard-coded: the compose file may name the
    image whatever it likes, and a helper started from a guess is a helper that
    does not exist.
    """
    override = (os.environ.get("SYSIBLE_UPDATER_IMAGE") or "").strip()
    if override:
        return override
    cid = ""
    try:
        with open("/etc/hostname", encoding="utf-8") as fh:
            cid = fh.read().strip()
    except OSError:
        cid = (os.environ.get("HOSTNAME") or "").strip()
    if cid:
        p = _docker("inspect", "-f", "{{.Config.Image}}", cid)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    # The name compose builds it under. A last resort, not the first guess.
    p = _docker("image", "inspect", "-f", "{{.Id}}", "sysible-slop-updater")
    return "sysible-slop-updater" if p.returncode == 0 else None


def spawn(script: str, cwd: Path, action: str = "update") -> tuple[bool, str]:
    """Start the detached helper. Returns (started, message).

    `action` is recorded as a container label so the job can still name itself
    after we have been restarted — otherwise a restart handed off here comes back
    reading "Update of slop", which is not what anybody pressed. It is one of a
    fixed set of keys, never a string from a request.
    """
    if action != "update" and action not in SCRIPT_ACTIONS:     # pragma: no cover
        action = "update"
    image = _own_image()
    if not image:
        return False, ("cannot find this service's own image to run the update "
                       "with — update from the host instead: sysible_ctl slop update")
    _docker("rm", "-f", HELPER)          # a finished helper from last time
    argv = [
        "run", "-d", "--name", HELPER,
        # Not restart:unless-stopped — this is a one-shot. A restart policy would
        # re-run the whole update every time the daemon came back.
        "--restart", "no",
        "--label", "sysible.role=selfupdate",
        "--label", f"sysible.action={action}",
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        # The checkout is bind-mounted at the SAME path inside this container and
        # on the host (see docker-compose.yml), so one path serves for both ends.
        "-v", f"{cwd}:{cwd}",
        "-w", str(cwd),
    ]
    # Compose reads these from the project's .env too; passing them through keeps
    # the helper resolving ${VAR} exactly as this container would.
    for var in ("SYSIBLE_SRC_DIR", "SYSIBLE_SLOP_DIR", "SYSIBLE_SSO_SHARED_SECRET",
                "SYSIBLE_UPDATER_SECRET"):
        val = os.environ.get(var)
        if val:
            argv += ["-e", f"{var}={val}"]
    argv += [image, "sh", "-c", script]
    p = _docker(*argv, timeout=60)
    if p.returncode != 0:
        return False, (p.stderr or p.stdout or "docker run failed").strip()[:300]
    _invalidate()
    return True, "started"


def _invalidate() -> None:
    global _status_cache
    _status_cache = (0.0, None)


def _inspect() -> dict | None:
    p = _docker("inspect", "-f",
                "{{.State.Running}}\t{{.State.ExitCode}}\t{{.State.StartedAt}}"
                "\t{{.State.FinishedAt}}\t{{index .Config.Labels \"sysible.action\"}}",
                HELPER)
    if p.returncode != 0:
        return None
    # rstrip the NEWLINE only. `.strip()` also eats the trailing tab, so a helper
    # with no action label (one started before that label existed) came back with
    # four fields, failed the length check, and read as "there is no helper" —
    # which throws away the result of the very update being reported on.
    parts = (p.stdout or "").rstrip("\r\n").split("\t")
    if len(parts) < 4:
        return None
    running, code, started, finished = parts[:4]
    action = parts[4] if len(parts) > 4 else ""
    return {"running": running.strip().lower() == "true",
            "exit_code": int(code) if code.strip().lstrip("-").isdigit() else None,
            "started_at": started, "finished_at": finished,
            "action": (action or "").strip() or "update"}


def logs(tail: int = 600) -> list[str]:
    p = _docker("logs", "--tail", str(int(tail)), HELPER, timeout=30)
    if p.returncode != 0:
        return []
    out = (p.stdout or "") + (p.stderr or "")
    return [ln for ln in out.splitlines()]


def running() -> bool:
    st = status()
    return bool(st and st.get("state") == "running")


def status(max_log: int = 600) -> dict | None:
    """The detached job as a job dict, or None when there is no helper.

    Memoised for a few seconds: /api/status is polled by every open
    Administration page and this costs two subprocesses.
    """
    global _status_cache
    now = time.time()
    ts, cached = _status_cache
    if now - ts < _STATUS_TTL:
        return dict(cached) if cached else None

    info = _inspect()
    if info is None:
        _status_cache = (now, None)
        return None
    act = info.get("action") or "update"
    verb = "update" if act == "update" else act
    if info["running"]:
        state = "running"
        message = f"the {verb} is running in a detached helper — this service restarts as part of it"
    elif info["exit_code"] == 0:
        state = "succeeded"
        message = ("slop updated and restarted." if act == "update"
                   else f"slop {act}ed.")
    else:
        state = "failed"
        message = f"the {verb} exited {info['exit_code']} — see the log."

    job = {"app": "slop", "actor": "", "state": state, "detached": True,
           "self_update": True, "message": message,
           "action": None if act == "update" else act,
           "started": _epoch(info["started_at"]), "finished":
               None if info["running"] else _epoch(info["finished_at"]),
           "log": logs(max_log)}
    # A long-finished helper is history, not news: reporting it forever would put
    # last week's update on the console of someone who just opened the page.
    if not info["running"] and job["finished"] and (now - job["finished"]) > RESULT_TTL:
        _status_cache = (now, None)
        return None
    _status_cache = (now, job)
    return dict(job)


def _epoch(stamp: str) -> float | None:
    """Docker's RFC3339 with NANOsecond precision -> epoch seconds, or None.

    fromisoformat takes at most microseconds on the Pythons this ships on, so the
    fraction is trimmed rather than handed over whole.
    """
    s = (stamp or "").strip()
    if not s or s.startswith("0001-01-01"):       # docker's "never"
        return None
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, _, rest = s.partition(".")
        digits = ""
        for ch in rest:
            if not ch.isdigit():
                break
            digits += ch
        s = f"{head}.{digits[:6].ljust(6, '0')}{rest[len(digits):]}"
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None
