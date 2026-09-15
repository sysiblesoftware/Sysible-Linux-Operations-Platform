"""Reading a checkout's update state, and pulling it forward.

Every git invocation here is an argv list against a directory this process chose
(see apps.py) — never a string a caller supplied, and never through a shell.

`ls-remote` rather than `fetch` for the CHECK, deliberately: fetch writes refs
into the live deployment repo, which collides with the pull an update performs
and, if it is killed mid-way (timeout, container stop), strands *.lock files
under .git/refs that break every later ref update. A read-only remote query
cannot do either, so the periodic check is safe to run as often as the console
asks for it.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

REMOTE_TIMEOUT = float(os.environ.get("SYSIBLE_UPDATER_REMOTE_TIMEOUT", "12"))
LOCAL_TIMEOUT = float(os.environ.get("SYSIBLE_UPDATER_LOCAL_TIMEOUT", "10"))

# The remote query is the expensive half of a status check — a network round-trip
# per product — and GET /api/status runs one for EVERY product. Administration's
# header pill polls that endpoint on every page it renders, so simply browsing the
# console meant four ls-remote calls to GitHub each time, and an unreachable remote
# stalled every admin page for REMOTE_TIMEOUT per product. A release does not
# appear more than once every few minutes, so remember the answer briefly. Set the
# TTL to 0 to disable; the console's "Check now" always asks for a fresh one, so a
# cached answer is never the only answer available.
REMOTE_TTL = float(os.environ.get("SYSIBLE_UPDATER_REMOTE_TTL", "300"))
# Failures expire sooner: a remote that was down is usually back long before a
# release lands, and caching the failure for the full TTL would keep showing
# "couldn't check" after the network came back.
REMOTE_FAIL_TTL = float(os.environ.get("SYSIBLE_UPDATER_REMOTE_FAIL_TTL", "30"))

_remote_cache: dict[tuple[str, str, str], tuple[float, str | None, str | None]] = {}
_remote_lock = threading.Lock()


def _ls_remote(root: Path, remote: str, rbranch: str,
               fresh: bool = False) -> tuple[str | None, str | None]:
    """(sha, error) for the upstream tip, memoised for REMOTE_TTL.

    `fresh` skips the cache (and refills it) — that is what the console's explicit
    re-check asks for, so a cached answer never becomes the only answer.
    """
    key = (str(root), remote, rbranch)
    now = time.monotonic()
    if not fresh:
        with _remote_lock:
            hit = _remote_cache.get(key)
        if hit:
            at, sha, err = hit
            ttl = REMOTE_TTL if err is None else REMOTE_FAIL_TTL
            if ttl > 0 and now - at < ttl:
                return sha, err
    ls = _git(root, "ls-remote", remote, rbranch, timeout=REMOTE_TIMEOUT)
    if ls.returncode != 0:
        # Surface git's ACTUAL complaint (auth, unknown host, TLS, proxy) —
        # "network or auth" tells an operator nothing they can fix.
        detail = " ".join((ls.stderr or ls.stdout or "").split())[:200]
        err = f"git ls-remote {remote} {rbranch} failed" + (f": {detail}" if detail else "")
        result = (None, err)
    else:
        sha = (ls.stdout.split() or [""])[0].strip()
        result = (sha, None) if sha else (None, "the upstream branch does not exist on the remote")
    with _remote_lock:
        _remote_cache[key] = (now, result[0], result[1])
    return result


def forget_remote(root: Path) -> None:
    """Drop cached remote state for one checkout — called after a pull, so the
    next status reflects what was just fetched rather than a pre-pull answer."""
    with _remote_lock:
        for key in [k for k in _remote_cache if k[0] == str(root)]:
            del _remote_cache[key]


def _git(root: Path, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), "-c", f"safe.directory={root}", *args],
        capture_output=True, text=True, timeout=timeout or LOCAL_TIMEOUT)


def _short(sha: str) -> str:
    return (sha or "")[:7]


def status(root: Path, fresh: bool = False) -> dict:
    """{checked, available, current, latest, branch, reason} for one checkout.

    Never raises: a checkout we cannot read is reported with a reason the
    operator can act on, because "couldn't check" and "up to date" must never
    look the same on screen.

    `fresh` forces the remote query instead of taking the memoised answer.
    """
    try:
        head = _git(root, "rev-parse", "HEAD")
        if head.returncode != 0:
            return {"checked": False, "reason": "not a readable git checkout"}
        cur = head.stdout.strip()
        branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        up = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").stdout.strip()
        if not up or "/" not in up:
            return {"checked": False, "reason": "no upstream branch is configured",
                    "current": _short(cur), "branch": branch}
        remote, rbranch = up.split("/", 1)
        latest, error = _ls_remote(root, remote, rbranch, fresh=fresh)
        if error:
            return {"checked": False, "current": _short(cur), "branch": branch,
                    "reason": error}
        return {"checked": True, "available": latest != cur, "current": _short(cur),
                "latest": _short(latest), "branch": branch}
    except subprocess.TimeoutExpired:
        return {"checked": False, "reason": f"the remote did not answer within {REMOTE_TIMEOUT:g}s"}
    except Exception as e:                                   # pragma: no cover
        return {"checked": False, "reason": str(e)[:160]}


def dirty(root: Path) -> bool:
    """True when the checkout has local modifications. A pull would fail or
    clobber them, so the console refuses instead of trying."""
    r = _git(root, "status", "--porcelain")
    return r.returncode == 0 and bool(r.stdout.strip())
