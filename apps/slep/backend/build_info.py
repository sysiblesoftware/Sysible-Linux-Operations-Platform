"""Which build this is, and which INSTANCE you are looking at.

Two different questions, both unanswerable from SLEP's screens until now, and both
of them cost real time in one evening:

  * "I updated and my fix isn't here." A fix had shipped four days earlier and was
    on main. Nothing on the page could distinguish a stale image from a current one,
    so the next hour went into re-reading code that was already correct.

  * "Where is my project's main.tf?" A Terraform apply failed quoting real line
    numbers out of main.tf, while the container's /data/projects was empty. The
    apply was being run by a different SLEP than the one being inspected, and no
    screen on either of them said so.

A build stamp alone answers only the first. So this reports the instance too: the
data directory it is actually using and how many projects are in it. If two SLEPs
disagree about that, it is visible in one glance instead of an archaeology session.

SYSIBLE_REV is baked in at image build time (see deploy/Dockerfile), the same way
idp, flashback, baseline, visualizer and updater do it. In a container there is no
git checkout, so SYSIBLE_REV is the authoritative answer and git is the fallback
for a source install.
"""
from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

# Baked in at build time. Empty means nobody set it -- a bare `docker compose up`
# -- and we say "unknown" rather than inventing a value.
REV = (os.getenv("SYSIBLE_REV") or "").strip()


def running_dir() -> Path:
    """The install root the live backend package is imported from -- the
    authoritative answer to 'which directory is actually running', not a guess."""
    return Path(__file__).resolve().parent.parent


def _git(*args):
    try:
        out = subprocess.run(["git", "-C", str(running_dir()), *args],
                             capture_output=True, text=True, timeout=3)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 - git absent in the image is normal
        pass
    return None


def _head_commit():
    commit = _git("rev-parse", "HEAD")
    if commit:
        return commit
    # The git binary is not in the image; read .git/HEAD by hand for a source install.
    try:
        gitdir = running_dir() / ".git"
        head = (gitdir / "HEAD").read_text().strip()
        if head.startswith("ref:"):
            return (gitdir / head.split(" ", 1)[1].strip()).read_text().strip()
        return head or None
    except Exception:  # noqa: BLE001
        return None


# Commit at process start -- compared against the live HEAD to catch "pulled in
# this directory but never restarted". Captured once, at import.
_STARTED_COMMIT = _head_commit()


def info() -> dict:
    """Everything needed to answer 'which SLEP am I talking to, running what'."""
    from . import db
    current = _head_commit()
    projects = data_dir = None
    try:
        data_dir = str(db.DATA_DIR)
        projects = len(db.list_projects())
    except Exception:  # noqa: BLE001 - never let the identity banner break the app
        pass
    return {
        # The build. REV is baked in; commit is only meaningful for a source install.
        "rev": REV or None,
        "commit": current,
        "commit_short": current[:12] if current else None,
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")) if current else False,
        # Code on disk moved since this process started: pulled here, not restarted.
        "restart_needed": bool(_STARTED_COMMIT and current and current != _STARTED_COMMIT),
        # The instance. This is the half that a build stamp alone does not answer.
        "running_dir": str(running_dir()),
        "data_dir": data_dir,
        "projects": projects,
        "host": socket.gethostname(),
        "pid": os.getpid(),
    }


def label() -> str:
    """One short string for the header: what a person reads at a glance."""
    if REV:
        return "build %s" % REV
    short = (_head_commit() or "")[:12]
    if short:
        return "source %s%s" % (short, "+" if _git("status", "--porcelain") else "")
    return "version unknown"
