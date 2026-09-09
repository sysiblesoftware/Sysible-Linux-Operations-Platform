"""Read the Controller's enrolled-host list, so the console can show hosts that
have not backed anything up YET.

Without this the page lists only hosts that have already reported a snapshot, so
a correctly-wired-but-not-yet-captured fleet is indistinguishable from a broken
one: both render "No host has reported a config backup yet." That single sentence
covered "config backup is off", "the token is wrong", "the agents are old" and
"it just hasn't run yet", and there was no way to tell from the page which.

IDENTITY. Exactly the rule the Visualizer uses: the caller's OWN identity, as the
gateway asserted it, is re-stamped onto the upstream request along with the shared
secret — never anything the browser sent us. So the Controller applies its own
RBAC to the real human, and this cannot be used to see a fleet you could not
already see. A refusal is reported as a note, not retried with anything stronger.

Never raises. A Controller that is down or not configured costs the extra hosts
and a line of explanation, not the page.
"""
from __future__ import annotations

import os

# DEFENSIVE on purpose. This module is an ENRICHMENT — it makes the console show
# hosts that have not captured yet — and an enrichment must never be able to stop
# Flashback serving. Importing httpx at module scope without it being in
# requirements.txt is exactly what happened: backend.app failed to import,
# uvicorn never started, and every Flashback page became a 502 at the gateway.
# A missing dependency now disables the host import and reports it as a note.
try:
    import httpx
except ImportError:                                  # pragma: no cover
    httpx = None

# Where the Controller's BFF listens. Same default and env var the Visualizer
# uses, so one platform install configures both.
_CONTROLLER = os.getenv("SLOP_CONTROLLER_UPSTREAM", "host.docker.internal:8800")
_SSO_SECRET = os.getenv("SYSIBLE_SSO_SHARED_SECRET", "")
_TIMEOUT = float(os.getenv("SYSIBLE_FLASHBACK_CONTROLLER_TIMEOUT_S", "6"))


def _url() -> str:
    u = (_CONTROLLER or "").strip().rstrip("/")
    if u.startswith(("http://", "https://")):
        return u
    # The Controller serves its own self-signed cert on the host — the same
    # reason the gateway uses tls_insecure_skip_verify on that hop.
    return f"https://{u}"


def configured() -> bool:
    return bool(httpx is not None and _SSO_SECRET and _CONTROLLER)


def unavailable_reason() -> str | None:
    """Why the host import is off, when it is off for a reason worth showing."""
    if httpx is None:
        return ("the host list needs httpx, which is not installed in this "
                "Flashback image — rebuild it (docker compose up -d --build flashback)")
    return None


def list_hosts(identity) -> tuple[list, str | None]:
    """(agent hosts, note). Only agent hosts: config capture is done BY the
    agent, so an SSH-only host has nothing that could ever report a snapshot and
    listing it as "no backup yet" would be a promise we cannot keep."""
    if not configured():
        # A standalone Flashback has no Controller to ask and needs no note; a
        # missing dependency is a real fault and gets one.
        return [], unavailable_reason()
    headers = {
        "Accept": "application/json",
        "X-Sysible-Auth": _SSO_SECRET,
        "X-Sysible-User": identity.user,
        "X-Sysible-Role": identity.role,
    }
    try:
        with httpx.Client(timeout=_TIMEOUT, verify=False, follow_redirects=False) as c:
            r = c.get(f"{_url()}/api/hosts", headers=headers)
    except Exception as e:
        return [], f"could not reach the Controller for its host list ({type(e).__name__})"
    if r.status_code in (401, 403):
        return [], f"the Controller did not permit this host list for role '{identity.role}'"
    if r.status_code >= 400:
        return [], f"the Controller returned HTTP {r.status_code} for its host list"
    try:
        data = r.json()
    except Exception:
        return [], "the Controller returned a malformed host list"
    rows = (data or {}).get("hosts") if isinstance(data, dict) else data
    out = []
    for h in rows or []:
        if not isinstance(h, dict) or h.get("kind") != "agent":
            continue
        hid = str(h.get("id") or "").strip()
        if hid:
            # Keep the environment and address: the console groups by environment
            # the way the EE panel does, and a bare host id tells an operator
            # nothing about which box it is.
            out.append({"host_id": hid, "label": str(h.get("label") or hid),
                        "environment": str(h.get("environment") or ""),
                        "address": str(h.get("address") or "")})
    return out, None


def request_capture(identity, host_id: str) -> tuple[bool, str]:
    """"Back up now": ask the Controller to have this host snapshot its config.

    Agents are outbound-only, so nothing can reach in — the Controller parks the
    request and hands it over on the host's next config-backup poll. Same identity
    rule as the host list: the caller's own, so the Controller applies its own
    RBAC rather than trusting us."""
    if not configured():
        return False, unavailable_reason() or "no Controller is configured"
    headers = {
        "Accept": "application/json",
        "X-Sysible-Auth": _SSO_SECRET,
        "X-Sysible-User": identity.user,
        "X-Sysible-Role": identity.role,
    }
    try:
        with httpx.Client(timeout=_TIMEOUT, verify=False, follow_redirects=False) as c:
            r = c.post(f"{_url()}/api/host/{host_id}/backup-now", headers=headers)
    except Exception as e:
        return False, f"could not reach the Controller ({type(e).__name__})"
    if r.status_code in (401, 403):
        return False, f"the Controller did not permit this for role '{identity.role}'"
    if r.status_code >= 400:
        return False, f"the Controller returned HTTP {r.status_code}"
    return True, "Requested — the host captures on its next check-in (within a minute)."
