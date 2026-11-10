"""Automatic updates: when, and whether anything came of it.

Asked for directly: "add an auto update option for slop and all components".

What this module is NOT is a second way to update. It decides only WHEN, and
then presses exactly the button an operator would: the same list-building as
/api/update-all, the same refusals, the same job, the same log. A scheduler that
grew its own idea of what is updatable would be a second answer to the question
the status screen already answers, and the two would disagree on the day it
mattered.

Three things it owes an operator, which is most of what is here:

  * it must never surprise them. The platform's own update recreates the
    gateway, this console and the updater itself -- so a run in the middle of
    someone's afternoon logs them out mid-task. The default hour is 03:00 local
    and the screen says when the next one is.

  * it must never lie about having run. A run that was refused (a dirty
    checkout) or found nothing to do is RECORDED as such, with the reason. "Last
    run: Tuesday 03:00" over a checkout that has been refusing for three weeks
    is the kind of green that gets believed.

  * it must be stoppable, from the screen, without a shell on the host.

State lives in a file, not in the checkout: writing to the git tree would make
it dirty, and a dirty checkout is the thing that REFUSES updates. That would be
a scheduler that switches itself off the first time it runs.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

STATE_PATH = Path(os.environ.get("SYSIBLE_UPDATER_STATE", "/data/autoupdate.json"))

# How often the thread wakes to look at the clock. Not how often it updates.
TICK_SECONDS = int(os.environ.get("SYSIBLE_UPDATER_TICK", "60") or 60)

CADENCES = ("daily", "weekly")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")

DEFAULTS: dict = {
    "enabled": False,          # opt-in. An update that reboots the console is
                               # not something to switch on for someone.
    "cadence": "weekly",
    "hour": 3,                 # local time on the host
    "weekday": 6,              # Sunday, for the weekly cadence
    "include_platform": True,  # the Operations Platform itself
    "last": None,              # {at, outcome, detail, products}
}

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()


def _read() -> dict:
    out = dict(DEFAULTS)
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            out.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        # No state, or state this build cannot read. Defaults are OFF, so the
        # failure mode of an unreadable file is "does not update by itself",
        # which is the right way round.
        pass
    return _clean(out)


def _clean(s: dict) -> dict:
    """Bring a settings dict back inside its own rules.

    Called on the way in AND on the way out: a hand-edited file is as much a
    caller as the API is, and an hour of 99 would otherwise mean a schedule that
    never comes due while the screen cheerfully says it is on.
    """
    s["enabled"] = bool(s.get("enabled"))
    s["include_platform"] = bool(s.get("include_platform", True))
    s["cadence"] = s["cadence"] if s.get("cadence") in CADENCES else DEFAULTS["cadence"]
    try:
        s["hour"] = min(23, max(0, int(s.get("hour", 3))))
    except (TypeError, ValueError):
        s["hour"] = DEFAULTS["hour"]
    try:
        s["weekday"] = min(6, max(0, int(s.get("weekday", 6))))
    except (TypeError, ValueError):
        s["weekday"] = DEFAULTS["weekday"]
    last = s.get("last")
    s["last"] = last if isinstance(last, dict) else None
    return s


def _write(s: dict) -> None:
    s = _clean(dict(s))
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2)
    os.replace(tmp, STATE_PATH)   # atomic: a torn file reads as "off"


def settings() -> dict:
    with _lock:
        return _read()


def save(changes: dict) -> dict:
    """Merge a caller's changes in. Only the keys above; `last` is ours."""
    with _lock:
        s = _read()
        for k in ("enabled", "cadence", "hour", "weekday", "include_platform"):
            if k in changes:
                s[k] = changes[k]
        _write(s)
        return _read()


def _record(outcome: str, detail: str, products: list[str] | None = None,
            at: float | None = None) -> None:
    """What the last run did, including when it did nothing.

    `at` exists so this can be driven against a clock. due() is entirely about
    comparing the recorded time with the current hour, and a scheduler whose
    "have I already run in this window" logic cannot be tested without waiting
    a real day is one nobody can trust.

    `outcome` is one of: updated, nothing, refused, failed. "nothing" is a real
    answer -- everything was already current -- and it is deliberately not
    spelled the same as "updated", because a screen that says "last run:
    updated" every night about a platform that has not moved in a month teaches
    an operator to stop reading it.
    """
    with _lock:
        s = _read()
        s["last"] = {"at": time.time() if at is None else float(at),
                     "outcome": outcome, "detail": detail,
                     "products": products or []}
        _write(s)


def next_run(s: dict | None = None, now: datetime | None = None) -> datetime | None:
    """When the next automatic update is due, or None when it is switched off."""
    s = _clean(dict(s or settings()))
    if not s["enabled"]:
        return None
    now = now or datetime.now()
    nxt = now.replace(hour=s["hour"], minute=0, second=0, microsecond=0)
    if s["cadence"] == "daily":
        if nxt <= now:
            nxt += timedelta(days=1)
        return nxt
    # weekly: the next time that weekday comes round at that hour.
    ahead = (s["weekday"] - nxt.weekday()) % 7
    nxt += timedelta(days=ahead)
    if nxt <= now:
        nxt += timedelta(days=7)
    return nxt


def due(s: dict | None = None, now: datetime | None = None) -> bool:
    """Whether a run is owed RIGHT NOW.

    The window is the scheduled hour, and a run is owed only if the last one was
    before that hour began. That is what makes a missed window catch up -- a host
    switched off at 03:00 on Sunday updates when it comes back, within the hour --
    and what stops a tick every 60 seconds starting sixty updates.
    """
    s = _clean(dict(s or settings()))
    if not s["enabled"]:
        return False
    now = now or datetime.now()
    if now.hour != s["hour"]:
        return False
    if s["cadence"] == "weekly" and now.weekday() != s["weekday"]:
        return False
    last = (s.get("last") or {}).get("at")
    if last:
        window_start = now.replace(minute=0, second=0, microsecond=0)
        if datetime.fromtimestamp(last) >= window_start:
            return False
    return True


def run_now(actor: str = "the schedule") -> tuple[str, str]:
    """Do what the schedule is for: update everything that can be updated.

    Returns (outcome, detail) and records the same. Imported here rather than at
    module scope so this module can be read and tested without pulling in the
    Docker-socket machinery.
    """
    from . import apps, git, jobs

    s = settings()
    if jobs.current() and (jobs.current() or {}).get("state") == "running":
        detail = "an update was already running"
        _record("refused", detail)
        return "refused", detail

    items, blocked = [], []
    for key in apps.keys():
        if key == "slop" and not s["include_platform"]:
            continue
        if apps.is_vendored(key):
            # Built from the platform's tree: it has no pull of its own, and
            # updating the platform is what updates it. Listing it here would
            # queue the same git tree twice.
            continue
        root = apps.checkout_dir(key)
        if root is None:
            continue
        st = git.status(root)
        compose = apps.compose_dir(root)
        if not st.get("available"):
            continue
        if git.dirty(root):
            blocked.append(f"{key} (local changes)")
            continue
        if compose is None:
            blocked.append(f"{key} (no compose file)")
            continue
        items.append((key, root, compose))

    # The platform LAST, always: updating it recreates the gateway, this console
    # and the updater itself, so anything queued behind it would be killed
    # mid-pull.
    items.sort(key=lambda it: it[0] in jobs.SELF_AFFECTING)

    if not items:
        if blocked:
            detail = "nothing could be updated — blocked: " + ", ".join(blocked)
            _record("refused", detail)
            return "refused", detail
        detail = "everything was already up to date"
        _record("nothing", detail)
        return "nothing", detail

    started, message = jobs.start_many(items, actor)
    names = [k for k, _r, _c in items]
    if not started:
        _record("refused", message, names)
        return "refused", message
    detail = message + (("; blocked: " + ", ".join(blocked)) if blocked else "")
    _record("updated", detail, names)
    return "updated", detail


def _loop() -> None:
    while not _stop.wait(TICK_SECONDS):
        try:
            if due():
                run_now()
        except Exception as exc:                      # noqa: BLE001
            # A scheduler thread that dies takes automatic updates with it and
            # says nothing. Record and keep ticking.
            try:
                _record("failed", f"the scheduler hit an error: {exc}")
            except Exception:                         # noqa: BLE001
                pass


def start() -> None:
    """Begin ticking. Safe to call more than once."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="autoupdate", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
