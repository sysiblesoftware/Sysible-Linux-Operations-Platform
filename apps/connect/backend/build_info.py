"""Which build this Connect is, and which instance.

Same reason as SLEP's: a stale image and a current one are indistinguishable from
the outside, so "I updated and my fix isn't here" has no answer on the screen. The
fleet-wide version of that question cost an evening.

SYSIBLE_REV is baked in at image build time (see deploy/Dockerfile), matching idp,
flashback, baseline, visualizer and updater. In a container there is no git
checkout, so SYSIBLE_REV is the authoritative answer; git is the fallback for a
source install.
"""
from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

REV = (os.getenv("SYSIBLE_REV") or "").strip()


def running_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _git(*args):
    try:
        out = subprocess.run(["git", "-C", str(running_dir()), *args],
                             capture_output=True, text=True, timeout=3)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 - no git in the image is normal
        pass
    return None


def info() -> dict:
    commit = _git("rev-parse", "HEAD")
    return {
        "rev": REV or None,
        "commit": commit,
        "commit_short": commit[:12] if commit else None,
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "running_dir": str(running_dir()),
        "host": socket.gethostname(),
        "pid": os.getpid(),
    }


def label() -> str:
    if REV:
        return "build %s" % REV
    short = (_git("rev-parse", "HEAD") or "")[:12]
    return "source %s" % short if short else "version unknown"
