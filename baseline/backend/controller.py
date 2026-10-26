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


def _url() -> str:
    return (f"https://{_CONTROLLER}" if ":443" in _CONTROLLER
            else f"http://{_CONTROLLER}")


def list_targets(identity) -> tuple[list, list, str | None]:
    """(hosts, environments, note) — what an operator can assign a state TO.

    The assign box used to be a free-text field you typed a host id into, which
    means knowing the fleet by heart and getting it exactly right: a typo
    assigns a state to a host that does not exist, and nothing says so until
    somebody notices the host was never configured.

    Baseline does not know the fleet — the Controller does, and this is the
    same host list its own console uses. Both agent and SSH hosts are offered,
    because the Controller dispatches a Baseline apply over either.

    Never raises, and a note rather than an error: a Controller that is down
    costs the LIST, not the ability to assign. The console keeps its free-text
    box for exactly that case.
    """
    if not configured():
        return [], [], unavailable_reason()
    headers = {"Accept": "application/json",
               "X-Sysible-Auth": _SSO_SECRET,
               "X-Sysible-User": identity.user,
               "X-Sysible-Role": identity.role}
    try:
        with httpx.Client(timeout=_TIMEOUT, verify=False, follow_redirects=False) as c:
            r = c.get(f"{_url()}/api/hosts", headers=headers)
    except Exception as e:
        return [], [], (f"could not reach the Controller ({type(e).__name__}), so this "
                        "is the fleet it knows about rather than a list — type the "
                        "target instead")
    if r.status_code in (401, 403):
        return [], [], (f"the Controller did not permit a host list for role "
                        f"'{identity.role}' — type the target instead")
    if r.status_code >= 400:
        return [], [], (f"the Controller returned HTTP {r.status_code} for its host "
                        "list — type the target instead")
    try:
        data = r.json()
    except Exception:
        return [], [], "the Controller returned a malformed host list"
    rows = (data or {}).get("hosts") if isinstance(data, dict) else data
    hosts, envs = [], set()
    for h in rows or []:
        if not isinstance(h, dict):
            continue
        hid = str(h.get("id") or "").strip()
        if not hid or not _TARGET_RE.match(hid):
            # A host the Controller would accept but this service would refuse
            # to build a URL from is not one to offer.
            continue
        env = str(h.get("environment") or "").strip()
        if env and _TARGET_RE.match(f"env:{env}"):
            envs.add(env)
        hosts.append({"id": hid, "label": str(h.get("label") or hid),
                      "environment": env, "kind": str(h.get("kind") or ""),
                      "address": str(h.get("address") or ""),
                      "online": h.get("online")})
    hosts.sort(key=lambda h: ((h["environment"] or "~").lower(), h["label"].lower()))
    return hosts, sorted(envs, key=str.lower), None


def unavailable_reason() -> str | None:
    if httpx is None:                                # pragma: no cover
        return ("this build of Baseline has no HTTP client, so it cannot ask the "
                "Controller anything")
    if not _SSO_SECRET:
        return ("Baseline has no SLOP shared secret, so it cannot prove to the "
                "Controller who is asking. Re-run install.sh.")
    if not _CONTROLLER:                              # pragma: no cover
        return "no Controller address is configured"
    return None


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
    url = _url()
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
