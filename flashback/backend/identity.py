"""Sysible Flashback — request identity + authorization.

Flashback is a SLOP module: in production it sits behind the SLOP Caddy gateway,
which authenticates the browser at the shared IdP and stamps every proxied request
with the caller's identity plus a shared-secret proof:

    X-Sysible-User: <username>
    X-Sysible-Role: <role>          role in {superuser, operator, auditor}
    X-Sysible-Auth: <shared secret> proves the request came through the gateway

TRUST BOUNDARY: in gateway mode Flashback is reachable ONLY through the gateway,
which stamps X-Sysible-Auth with the shared secret on every request it forwards. A
browser hitting Flashback directly can't know that secret, so it can never spoof an
identity — the constant-time secret match IS the boundary.

Agents (the capture/restore side) are not browsers and never carry an SSO session;
they authenticate to the ingest/restore endpoints with a bearer agent token.

Standalone/dev (no gateway configured): Flashback falls back to a single local
identity so it's usable on a laptop. This is UNAUTHENTICATED — only for a trusted
local run — and a warning is logged at startup, mirroring the other CE modules'
"trust off by default" posture.
"""
from __future__ import annotations

import hmac
import os

# ---- gateway SSO trust -----------------------------------------------------
_TRUST_GATEWAY = os.getenv("SYSIBLE_FLASHBACK_TRUST_GATEWAY_AUTH", "0") == "1"
_SSO_SECRET = os.getenv("SYSIBLE_SSO_SHARED_SECRET", "")

# ---- agent bearer token (capture + restore polling) ------------------------
_AGENT_TOKEN = os.getenv("SYSIBLE_FLASHBACK_AGENT_TOKEN", "")

# ---- standalone/dev fallback identity --------------------------------------
_LOCAL_USER = os.getenv("SYSIBLE_FLASHBACK_LOCAL_USER", "local")
_LOCAL_ROLE = os.getenv("SYSIBLE_FLASHBACK_LOCAL_ROLE", "superuser")

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


def agent_auth_ok(request) -> bool:
    """True if the request carries the correct agent bearer token. When no agent
    token is configured (dev), agent endpoints are open — same posture as the local
    fallback identity below."""
    if not _AGENT_TOKEN:
        return True
    sent = request.headers.get("authorization", "")
    if sent.lower().startswith("bearer "):
        sent = sent[7:].strip()
    else:
        sent = request.headers.get("x-sysible-flashback-agent", "").strip()
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


def startup_notice() -> str:
    if gateway_configured():
        return "Flashback: SSO gateway trust ON — identity taken from the SLOP gateway."
    return ("Flashback: SSO gateway trust OFF — running in UNAUTHENTICATED local mode "
            f"as '{_LOCAL_USER}' ({_LOCAL_ROLE}). Do not expose this on a network.")
