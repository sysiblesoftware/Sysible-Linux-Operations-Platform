"""Sysible Baseline — request identity + authorization.

Same SSO shape as every other SLOP module: the gateway asserts the identity and
proves it with the shared secret. Lifted deliberately rather than reinvented —
the trust boundary is the platform's, not this service's.

Baseline is a SLOP module: in production it sits behind the SLOP Caddy gateway,
which authenticates the browser at the shared IdP and stamps every proxied request
with the caller's identity plus a shared-secret proof:

    X-Sysible-User: <username>
    X-Sysible-Role: <role>          role in {superuser, operator, auditor}
    X-Sysible-Auth: <shared secret> proves the request came through the gateway

TRUST BOUNDARY: in gateway mode Baseline is reachable ONLY through the gateway,
which stamps X-Sysible-Auth on every request it forwards. A browser hitting
Baseline directly can't know that secret, so it can never spoof an identity —
the constant-time secret match IS the boundary.

WHICH SECRET, AND WHY IT IS NOT THE PLATFORM-WIDE ONE. "A browser hitting
Baseline directly" was never the whole threat. docker-compose.yml publishes this
container's :8080 — the browser console AND the agent API, one app — on the
docker bridge, so that the Controller (a container in a different compose
project) can reach the agent endpoints. Anything else on that bridge can reach
the console routes at the same address. And the platform-wide
SYSIBLE_SSO_SHARED_SECRET is held by every service on the stack: the IdP, the
Visualizer, Flashback, the Controller, SLEP. So a flaw in ANY of them was one hop
from a forged Baseline SUPERUSER — read the secret out of your own environment,
POST to the bridge port with X-Sysible-User and X-Sysible-Role, and declare a
state. Baseline states run as root on every host they are assigned to.

SYSIBLE_BASELINE_GATEWAY_SECRET is held only by the gateway (which stamps it)
and the Visualizer (which reads Baseline's feeds directly, by design, carrying
the viewer's own identity). That is the same split, and the same reasoning,
that SYSIBLE_UPDATER_SECRET already makes for the updater — the component that
holds the host's Docker socket.

It FALLS BACK to the platform-wide secret when unset, so an install that has not
re-run install.sh keeps working rather than locking its operators out. The
fallback restores the hop, so it says so on every start instead of degrading
quietly.

Agents (the capture/restore side) are not browsers and never carry an SSO session;
they authenticate to the ingest/restore endpoints with a bearer agent token.

Standalone/dev (no gateway configured): Baseline falls back to a single local
identity so it's usable on a laptop. This is UNAUTHENTICATED — only for a trusted
local run — and a warning is logged at startup, mirroring the other CE modules'
"trust off by default" posture.
"""
from __future__ import annotations

import hmac
import os

from . import env

# ---- gateway SSO trust -----------------------------------------------------
_TRUST_GATEWAY = env.getenv("TRUST_GATEWAY_AUTH", "0") == "1"
# Baseline's OWN gateway secret, falling back to the platform-wide one. See the
# module docstring for why the two are not the same thing.
_SSO_SECRET = os.getenv("SYSIBLE_BASELINE_GATEWAY_SECRET", "")
_SHARED_FALLBACK = not _SSO_SECRET
if _SHARED_FALLBACK:
    _SSO_SECRET = os.getenv("SYSIBLE_SSO_SHARED_SECRET", "")
if _TRUST_GATEWAY and _SSO_SECRET and _SHARED_FALLBACK:
    print("[sysible-baseline] SYSIBLE_BASELINE_GATEWAY_SECRET is not set; falling back "
          "to the PLATFORM-WIDE SYSIBLE_SSO_SHARED_SECRET. Every service on this "
          "network holds that value, and this container's console port is published "
          "on the docker bridge — so any one of them can assert a Baseline superuser "
          "identity and declare a state that runs as root on the fleet. Re-run "
          "install.sh (or set SYSIBLE_BASELINE_GATEWAY_SECRET in the SLOP .env and "
          "recreate gateway + baseline + visualizer) to separate them.", flush=True)

# ---- agent bearer token (capture + restore polling) ------------------------
_AGENT_TOKEN = env.getenv("AGENT_TOKEN", "")

# ---- standalone/dev fallback identity --------------------------------------
_LOCAL_USER = env.getenv("LOCAL_USER", "local")
_LOCAL_ROLE = env.getenv("LOCAL_ROLE", "superuser")

ROLES = ("superuser", "operator", "auditor")
# Only these may take state-changing actions (queue a restore). 'auditor' is
# read-only oversight; anything unknown/empty fails closed.
_PRIVILEGED_ROLES = {"superuser", "operator"}


class Identity:
    __slots__ = ("user", "role")

    def __init__(self, user: str, role: str):
        self.user = user
        self.role = role

    @property
    def can_write(self) -> bool:
        return self.role in _PRIVILEGED_ROLES


def gateway_configured() -> bool:
    return bool(_TRUST_GATEWAY and _SSO_SECRET)


def agent_configured() -> bool:
    """Whether an agent token is set at all. Callers use this to tell 'wrong
    token' from 'this deployment never configured one'."""
    return bool(_AGENT_TOKEN)


# The agent API takes host_id from the CALLER and authenticates with ONE token
# shared by whoever posts snapshots. That is safe as shipped, and only because of
# where it listens: the installer binds the agent port to the docker bridge, and
# only the Controller holds the token — managed hosts never see it, they talk to
# the Controller and it relays.
#
# Both of those are configuration. Bind the port to a wildcard so agents can post
# directly, which the compose file offers as an override, and the picture changes
# completely: any agent holding the token can then write ANY host's config
# history, and read the restore payloads queued for it. Demonstrated, not
# theorised — a snapshot posted under another host's id is accepted.
#
# So say so at startup rather than letting an override be a quiet cliff.
def agent_auth_ok(request) -> bool:
    """True if the request carries the correct agent bearer token.

    FAILS CLOSED when no token is configured. This used to return True, on the
    reasoning that an unconfigured deployment is a dev one — but the agent
    endpoints take host_id from the CALLER, so "open" meant anyone who could
    reach the service could read any host's stored configuration and queue a
    restore that overwrites a file on it. A missing token is a misconfiguration,
    never a grant.
    """
    if not _AGENT_TOKEN:
        return False
    sent = request.headers.get("authorization", "")
    if sent.lower().startswith("bearer "):
        sent = sent[7:].strip()
    else:
        # The non-Authorization fallback header, under both spellings. The
        # Controller is the only client, and Controller and Baseline are
        # SEPARATE deployments on separate upgrade schedules: a Controller
        # still sending the pre-rebrand x-sysible-flashback-agent must keep
        # authenticating here, or the desired-state channel stops fleet-wide at
        # the moment one half is updated and the other is not.
        #
        # Both names, once each. This listed x-sysible-baseline-agent TWICE, so
        # the loop checked one header and then checked it again and the shim the
        # comment describes did not exist.
        for name in ("x-sysible-baseline-agent", "x-sysible-flashback-agent"):
            sent = (request.headers.get(name) or "").strip()
            if sent:
                break
    return bool(sent) and hmac.compare_digest(sent, _AGENT_TOKEN)


def current(request) -> Identity | None:
    """Resolve the browser caller's identity. In gateway mode, trust the stamped
    headers ONLY when the shared-secret proof matches (constant-time); a mismatch or
    a direct hit returns None (unauthenticated). Standalone/dev returns the local
    fallback identity."""
    if gateway_configured():
        proof = request.headers.get("x-sysible-auth", "")
        if not proof or not hmac.compare_digest(proof, _SSO_SECRET):
            return None
        user = (request.headers.get("x-sysible-user") or "").strip()
        role = (request.headers.get("x-sysible-role") or "").strip().lower()
        if not user or role not in ROLES:
            return None
        return Identity(user, role)
    # No gateway configured → local dev identity (unauthenticated; logged at startup).
    return Identity(_LOCAL_USER, _LOCAL_ROLE if _LOCAL_ROLE in ROLES else "auditor")


def deny_reason(request) -> tuple[str, str]:
    """Why current() refused, as (code, operator-facing sentence).

    A browser that lands on Baseline and is told only "Not signed in." has no way
    to tell a direct hit apart from a misconfigured gateway, so this names the
    actual failure. It deliberately reveals NOTHING secret: whether a proof header
    was PRESENT is not the secret's value, and someone who can't produce the secret
    learns nothing they couldn't learn by trying. Each case maps to one fix.
    """
    if not gateway_configured():
        return ("open", "Baseline is running in unauthenticated local mode.")
    proof = request.headers.get("x-sysible-auth", "")
    if not proof:
        return ("no-proof",
                "This request carried no gateway proof header. Either you reached "
                "Baseline directly (it is only meant to be used through the SLOP "
                "gateway at /baseline/), or the gateway is not stamping this route.")
    if not hmac.compare_digest(proof, _SSO_SECRET):
        return ("bad-proof",
                "The gateway's proof header did not match. SYSIBLE_SSO_SHARED_SECRET "
                "differs between the gateway and Baseline — set the same value for "
                "both (it lives in the SLOP .env) and recreate both containers.")
    user = (request.headers.get("x-sysible-user") or "").strip()
    role = (request.headers.get("x-sysible-role") or "").strip().lower()
    if not user:
        return ("no-user",
                "The gateway proved itself but asserted no user. The gateway's "
                "forward_auth is not copying X-Sysible-User from the IdP.")
    if role not in ROLES:
        return ("bad-role",
                f"The gateway asserted an unusable role for {user!r}. Expected one "
                f"of: {', '.join(ROLES)}.")
    return ("ok", "")


def startup_notice() -> str:
    if gateway_configured():
        return "Baseline: SSO gateway trust ON — identity taken from the SLOP gateway."
    return ("Baseline: SSO gateway trust OFF — running in UNAUTHENTICATED local mode "
            f"as '{_LOCAL_USER}' ({_LOCAL_ROLE}). Do not expose this on a network.")
