"""Ask the Controller to apply a host's desired state.

Baseline does not talk to hosts. The Controller holds the only authenticated
channel to every enrolled agent, so "Apply now" in this console is a request TO
the Controller, not an action on a host.

IDENTITY. The caller's OWN identity, as the SLOP gateway asserted it, is
re-stamped onto the upstream request along with the shared secret — never
anything the browser sent us. The Controller then applies its own RBAC to the
real human, so this cannot be used to configure a host you could not already
configure. Exactly the rule Flashback's controller client follows.

Never raises. A Controller that is down costs the button, not the page.
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote

# DEFENSIVE, for the same reason Flashback's is: this module is one BUTTON. A
# missing dependency must never stop Baseline serving — that exact mistake once
# made every Flashback page a 502 at the gateway.
try:
    import httpx
except ImportError:                                  # pragma: no cover
    httpx = None

_CONTROLLER = os.getenv("SLOP_CONTROLLER_UPSTREAM", "host.docker.internal:8800")
_SSO_SECRET = os.getenv("SYSIBLE_SSO_SHARED_SECRET", "")
_TIMEOUT = 20.0

# What the Controller accepts as a host id, plus our env: form. Anything else is
# refused HERE rather than interpolated into a URL.
_TARGET_RE = re.compile(r"^(env:)?[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def configured() -> bool:
    return bool(httpx and _SSO_SECRET and _CONTROLLER)


def request_apply(identity, target: str, preview: bool = False) -> tuple[bool, str]:
    """(ok, message). Asks the Controller to run this target's desired state."""
    if not _TARGET_RE.match(target or ""):
        return False, "That target name is not one this controller would accept."
    if httpx is None:
        return False, ("Baseline cannot reach the Controller from this build "
                       "(httpx is missing), so Apply has to be run from the "
                       "Controller's own console.")
    if not _SSO_SECRET:
        return False, ("Baseline has no SLOP shared secret, so it cannot prove "
                       "to the Controller who is asking. Re-run install.sh.")
    url = f"https://{_CONTROLLER}" if ":443" in _CONTROLLER else f"http://{_CONTROLLER}"
    try:
        with httpx.Client(timeout=_TIMEOUT, verify=False) as c:
            r = c.post(f"{url}/api/host/{quote(target, safe='')}/baseline-apply",
                       json={"preview": bool(preview)},
                       headers={"X-Sysible-User": identity.user,
                                "X-Sysible-Role": identity.role,
                                "X-Sysible-Auth": _SSO_SECRET})
    except Exception as e:
        return False, f"The Controller is unreachable ({type(e).__name__})."
    if r.status_code == 404:
        return False, (f"The Controller does not know a host called {target!r}, "
                       "or it is not running a build that supports Baseline yet.")
    if r.status_code in (401, 403):
        return False, "The Controller refused this request for your account."
    if r.status_code >= 400:
        try:
            return False, (r.json() or {}).get("detail") or f"HTTP {r.status_code}."
        except Exception:
            return False, f"The Controller returned HTTP {r.status_code}."
    try:
        body = r.json() or {}
    except Exception:
        body = {}
    return True, body.get("detail") or ("Preview requested." if preview
                                        else "Apply requested.")
