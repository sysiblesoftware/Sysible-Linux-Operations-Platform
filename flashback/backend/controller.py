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

import httpx

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
    return bool(_SSO_SECRET and _CONTROLLER)


def list_hosts(identity) -> tuple[list, str | None]:
    """(agent hosts, note). Only agent hosts: config capture is done BY the
    agent, so an SSH-only host has nothing that could ever report a snapshot and
    listing it as "no backup yet" would be a promise we cannot keep."""
    if not configured():
        return [], None                      # standalone Flashback: nothing to ask
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
            out.append({"host_id": hid, "label": str(h.get("label") or hid)})
    return out, None
