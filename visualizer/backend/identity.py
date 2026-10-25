"""Sysible Visualizer — request identity + authorization.

Visualizer is a SLOP module: in production it sits behind the SLOP Caddy gateway,
which authenticates the browser at the shared IdP and stamps every proxied request
with the caller's identity plus a shared-secret proof:

    X-Sysible-User: <username>
    X-Sysible-Role: <role>          role in {superuser, operator, auditor}
    X-Sysible-Auth: <shared secret> proves the request came through the gateway

TRUST BOUNDARY: in gateway mode Visualizer is reachable ONLY through the gateway,
which stamps X-Sysible-Auth with the shared secret on every request it forwards. A
browser hitting Visualizer directly can't know that secret, so it can never spoof an
identity — the constant-time secret match IS the boundary.

Visualizer is READ-ONLY: it never mutates another app, so every role — including
auditor — may view it. What each viewer can SEE is decided by the upstream app,
because Visualizer forwards the caller's own identity on every fetch (see
sources.py) rather than querying as itself.

Standalone/dev (no gateway configured): Visualizer falls back to a single local
identity so it's usable on a laptop. This is UNAUTHENTICATED — only for a trusted
local run — and a warning is logged at startup, mirroring the other CE modules'
"trust off by default" posture.
"""
from __future__ import annotations

import hmac
import os

# ---- gateway SSO trust -----------------------------------------------------
_TRUST_GATEWAY = os.getenv("SYSIBLE_VISUALIZER_TRUST_GATEWAY_AUTH", "0") == "1"
_SSO_SECRET = os.getenv("SYSIBLE_SSO_SHARED_SECRET", "")

# ---- standalone/dev fallback identity --------------------------------------
_LOCAL_USER = os.getenv("SYSIBLE_VISUALIZER_LOCAL_USER", "local")
_LOCAL_ROLE = os.getenv("SYSIBLE_VISUALIZER_LOCAL_ROLE", "superuser")

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
    """Why current() refused, as (code, operator-facing sentence). Mirrors
    Flashback's: it names the wiring fault and reveals nothing secret."""
    if not gateway_configured():
        return ("open", "Visualizer is running in unauthenticated local mode.")
    proof = request.headers.get("x-sysible-auth", "")
    if not proof:
        return ("no-proof",
                "This request carried no gateway proof header. Either you reached "
                "Visualizer directly (it is only meant to be used through the SLOP "
                "gateway at /visualizer/), or the gateway is not stamping this route.")
    if not hmac.compare_digest(proof, _SSO_SECRET):
        return ("bad-proof",
                "The gateway's proof header did not match. SYSIBLE_SSO_SHARED_SECRET "
                "differs between the gateway and Visualizer — set the same value for "
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
        return "Visualizer: SSO gateway trust ON — identity taken from the SLOP gateway."
    return ("Visualizer: SSO gateway trust OFF — running in UNAUTHENTICATED local mode "
            f"as '{_LOCAL_USER}' ({_LOCAL_ROLE}). Do not expose this on a network.")
