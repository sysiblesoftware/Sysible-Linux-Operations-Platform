"""Administration → Software updates: the IdP's client for the updater sidecar.

The IdP deliberately holds NO Docker socket. It asks the updater — a separate,
expose-only service on the compose network — which products are behind and, when
a superuser presses the button, to update one. The shared secret proves the call
came from inside the platform; the caller's username and role ride along so the
updater can enforce the superuser rule on its own side too, rather than trusting
ours.

stdlib urllib rather than a client library: the IdP is deliberately tiny (FastAPI
plus the standard library) and this is three small JSON calls.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

UPSTREAM = os.environ.get("SLOP_UPDATER_UPSTREAM", "updater:8080")
SECRET = os.environ.get("SYSIBLE_SSO_SHARED_SECRET", "")
TIMEOUT = float(os.environ.get("SLOP_UPDATER_TIMEOUT", "20"))


def configured() -> bool:
    """False when no updater is deployed. Administration then shows the update
    state it can determine and the command to run on the host, rather than a
    button that cannot work."""
    return bool(UPSTREAM and SECRET)


def _url(path: str) -> str:
    base = UPSTREAM.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = "http://" + base
    return base + path


def _call(path: str, user: str, role: str, method: str = "GET") -> tuple[dict | None, str | None]:
    """(data, error). Never raises — Administration must render even when the
    updater is missing, stopped, or mid-restart because it is updating SLOP."""
    if not configured():
        return None, "the updater service is not deployed"
    req = urllib.request.Request(_url(path), method=method, headers={
        "X-Sysible-Auth": SECRET,
        "X-Sysible-User": user,
        "X-Sysible-Role": role,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8") or "{}"), None
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8") or "{}").get("detail")
        except Exception:
            detail = None
        return None, detail or f"updater returned HTTP {e.code}"
    except Exception as e:
        return None, f"updater unreachable ({type(e).__name__})"


def status(user: str, role: str):
    return _call("/api/status", user, role)


def job(user: str, role: str):
    return _call("/api/job?tail=200", user, role)


def apply(key: str, user: str, role: str):
    # The key is validated again by the updater against its own allowlist; this
    # side never builds a path or a command from it.
    return _call(f"/api/update/{key}", user, role, method="POST")


def pending(apps) -> int:
    """How many products have an update waiting — what the header pill counts."""
    return sum(1 for a in apps or [] if a.get("available"))
