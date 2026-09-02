"""Sysible Visualizer — the per-app activity/log adapters.

Each Sysible app keeps its own trail in its own shape. This module fetches them and
normalises every record to ONE event shape so the console can show them side by
side, still separated by app:

    {"id": int|None, "ts": float, "actor": str, "action": str,
     "target": str, "detail": str}

IDENTITY FORWARDING — the security-critical part. Visualizer never queries an app
"as itself". On every fetch it re-stamps the identity the GATEWAY asserted to it
(X-Sysible-User / X-Sysible-Role) plus the shared secret, so each upstream applies
its OWN role rules to the real human behind the request: a SLOP auditor sees
exactly what an auditor may see in the Controller, and SLEP still refuses its
superuser-only audit to a viewer. The identity is taken from the validated
Identity object — NEVER from anything the browser sent us — so this aggregator
cannot be used to escalate. A 401/403 from an upstream is reported as such, not
retried with a stronger identity.

TLS: the three fronted apps serve self-signed certs on the host (the same reason
the gateway uses tls_insecure_skip_verify on those hops), so verification is off
for them; flashback/visualizer are plain HTTP on the internal network.
"""
from __future__ import annotations

import os

import httpx

# Where each app listens. Defaults mirror the gateway's SLOP_*_UPSTREAM values in
# docker-compose.yml, so a stock stack needs no extra configuration.
_CONTROLLER = os.getenv("SLOP_CONTROLLER_UPSTREAM", "host.docker.internal:8800")
_SLEP = os.getenv("SLOP_SLEP_UPSTREAM", "host.docker.internal:8810")
_CONNECT = os.getenv("SLOP_CONNECT_UPSTREAM", "host.docker.internal:8700")
_FLASHBACK = os.getenv("SLOP_FLASHBACK_UPSTREAM", "flashback:8080")

_SSO_SECRET = os.getenv("SYSIBLE_SSO_SHARED_SECRET", "")
_TIMEOUT = float(os.getenv("SYSIBLE_VISUALIZER_TIMEOUT_S", "8"))
# Cap what one fetch can pull back, so a huge upstream trail can't balloon memory.
MAX_LIMIT = int(os.getenv("SYSIBLE_VISUALIZER_MAX_LIMIT", "500"))


def _url(upstream: str, default_scheme: str) -> str:
    """Build a base URL from an upstream env value. The value may already carry a
    scheme (e.g. "http://host:8810" when an app runs with TLS off); otherwise the
    app's normal scheme is applied. Mirrors how the gateway addresses each app."""
    u = (upstream or "").strip().rstrip("/")
    if u.startswith(("http://", "https://")):
        return u
    return f"{default_scheme}://{u}"


def _headers(identity) -> dict:
    """The caller's OWN identity, re-stamped for the upstream. Values come from the
    validated Identity, never from client-supplied headers."""
    h = {"Accept": "application/json"}
    if _SSO_SECRET:
        h["X-Sysible-Auth"] = _SSO_SECRET
        h["X-Sysible-User"] = identity.user
        h["X-Sysible-Role"] = identity.role
    return h


def _get(url: str, identity, params: dict | None = None, want_json: bool = True):
    """Fetch one upstream endpoint. Returns (data, error). `error` is a short
    operator-facing string; never raises, so one dead app can't blank the console."""
    try:
        with httpx.Client(timeout=_TIMEOUT, verify=False, follow_redirects=False) as c:
            r = c.get(url, headers=_headers(identity), params=params or {})
    except Exception as e:                                  # network/DNS/TLS/timeout
        return None, f"unreachable ({type(e).__name__})"
    if r.status_code in (401, 403):
        return None, f"not permitted for role '{identity.role}' ({r.status_code})"
    if r.status_code == 404:
        return None, "endpoint not found (app may predate this feature)"
    if r.status_code >= 400:
        return None, f"HTTP {r.status_code}"
    if not want_json:
        return r.text, None
    try:
        return r.json(), None
    except Exception:
        return None, "malformed JSON from upstream"


def _ev(ts, actor, action, target="", detail="", _id=None) -> dict:
    return {
        "id": _id,
        "ts": float(ts or 0),
        "actor": str(actor or ""),
        "action": str(action or ""),
        "target": str(target or ""),
        "detail": str(detail or ""),
    }


# --------------------------------------------------------------------------- #
# Per-app adapters. Each returns {"events": [...], "errors": [...], "notes": [...]}
# --------------------------------------------------------------------------- #
def _controller(identity, limit: int) -> dict:
    base = _url(_CONTROLLER, "https")
    events, errors, notes = [], [], []

    # The operator activity feed (who ran what, on which host).
    data, err = _get(f"{base}/api/activity", identity, {"limit": limit})
    if err:
        errors.append(f"activity: {err}")
    else:
        for e in (data or {}).get("activity", []):
            events.append(_ev(e.get("timestamp"), e.get("username"),
                              e.get("description"), e.get("host"),
                              e.get("command"), e.get("id")))

    # The admin audit trail (logins, role changes…) — superuser-only upstream.
    data, err = _get(f"{base}/api/audit-log", identity, {"limit": limit})
    if err:
        notes.append(f"admin audit-log: {err}")
    else:
        for e in (data or {}).get("audit", []):
            events.append(_ev(e.get("timestamp"), e.get("username"),
                              e.get("event"), "", e.get("detail")))
    return {"events": events, "errors": errors, "notes": notes}


def _slep(identity, limit: int) -> dict:
    base = _url(_SLEP, "https")
    events, errors, notes = [], [], []

    # Pipeline runs are SLEP's real activity: readable by any signed-in role.
    data, err = _get(f"{base}/api/runs", identity)
    if err:
        errors.append(f"runs: {err}")
    else:
        for r in (data or {}).get("runs", [])[:limit]:
            status = r.get("status") or "?"
            events.append(_ev(
                r.get("finished") or r.get("started") or r.get("created"),
                r.get("created_by"), f"run {status}", f"{r.get('kind')} #{r.get('id')}",
                f"target={r.get('target') or '-'} exit={r.get('exit_code')}", r.get("id")))

    # The admin audit chain — superuser-only upstream, so a viewer just gets a note.
    data, err = _get(f"{base}/api/audit", identity, {"limit": limit})
    if err:
        notes.append(f"audit: {err}")
    else:
        for e in (data or {}).get("entries", []):
            events.append(_ev(e.get("ts"), e.get("username"), e.get("event"),
                              "", e.get("detail"), e.get("id")))
    return {"events": events, "errors": errors, "notes": notes}


def _connect(identity, limit: int) -> dict:
    data, err = _get(_url(_CONNECT, "https") + "/api/audit", identity, {"limit": limit})
    if err:
        # Connect grew its audit trail later than the others; say so plainly rather
        # than showing an empty panel that reads as "nothing ever happened".
        return {"events": [], "errors": [],
                "notes": [f"audit: {err}"]}
    events = [_ev(e.get("ts"), e.get("actor"), e.get("action"),
                  e.get("target"), e.get("detail"), e.get("id"))
              for e in (data or {}).get("entries", [])]
    return {"events": events, "errors": [], "notes": []}


def _flashback(identity, limit: int) -> dict:
    data, err = _get(_url(_FLASHBACK, "http") + "/api/audit", identity, {"limit": limit})
    if err:
        return {"events": [], "errors": [f"audit: {err}"], "notes": []}
    events = [_ev(e.get("ts"), e.get("actor"), e.get("action"),
                  "", e.get("detail"), e.get("id"))
              for e in (data or {}).get("entries", [])]
    return {"events": events, "errors": [], "notes": []}


# key -> (label, fetcher). Order is the tab order in the console.
APPS: dict[str, tuple] = {
    "controller": ("Sysible Controller", _controller),
    "slep": ("Sysible Linux Engineering Platform", _slep),
    "connect": ("Sysible Connect", _connect),
    "flashback": ("Sysible Flashback", _flashback),
}


def app_keys() -> list[str]:
    return list(APPS.keys())


def fetch(app: str, identity, limit: int = 100) -> dict:
    """Activity for ONE app, newest first. Never raises."""
    if app not in APPS:
        raise KeyError(app)
    label, fn = APPS[app]
    limit = max(1, min(int(limit), MAX_LIMIT))
    out = fn(identity, limit)
    events = sorted(out["events"], key=lambda e: e["ts"], reverse=True)[:limit]
    return {"app": app, "label": label, "events": events,
            "errors": out["errors"], "notes": out["notes"]}


def fetch_log(app: str, identity, ref: str) -> tuple[str | None, str | None]:
    """A raw log body for an app, when it has one. SLEP exposes per-run logs; the
    Controller exposes its own service log (superuser-only upstream)."""
    if app == "slep":
        return _get(_url(_SLEP, "https") + f"/api/runs/{ref}/log", identity, want_json=False)
    if app == "controller":
        return _get(_url(_CONTROLLER, "https") + "/api/controller-log", identity,
                    {"lines": 500}, want_json=False)
    return None, "this app exposes no log endpoint"
