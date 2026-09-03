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
from pathlib import Path

REMOTE_TIMEOUT = float(os.environ.get("SYSIBLE_UPDATER_REMOTE_TIMEOUT", "12"))
LOCAL_TIMEOUT = float(os.environ.get("SYSIBLE_UPDATER_LOCAL_TIMEOUT", "10"))


def _git(root: Path, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), "-c", f"safe.directory={root}", *args],
        capture_output=True, text=True, timeout=timeout or LOCAL_TIMEOUT)


def _short(sha: str) -> str:
    return (sha or "")[:7]


def status(root: Path) -> dict:
    """{checked, available, current, latest, branch, reason} for one checkout.

    Never raises: a checkout we cannot read is reported with a reason the
    operator can act on, because "couldn't check" and "up to date" must never
    look the same on screen.
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
        ls = _git(root, "ls-remote", remote, rbranch, timeout=REMOTE_TIMEOUT)
        if ls.returncode != 0:
            # Surface git's ACTUAL complaint (auth, unknown host, TLS, proxy) —
            # "network or auth" tells an operator nothing they can fix.
            detail = " ".join((ls.stderr or ls.stdout or "").split())[:200]
            return {"checked": False, "current": _short(cur), "branch": branch,
                    "reason": f"git ls-remote {remote} {rbranch} failed"
                              + (f": {detail}" if detail else "")}
        latest = (ls.stdout.split() or [""])[0].strip()
        if not latest:
            return {"checked": False, "current": _short(cur), "branch": branch,
                    "reason": "the upstream branch does not exist on the remote"}
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
