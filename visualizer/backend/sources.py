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

import re as _re
from urllib.parse import quote as _quote

import concurrent.futures as _futures

import httpx

from . import fleet

# Where each app listens. Defaults mirror the gateway's SLOP_*_UPSTREAM values in
# docker-compose.yml, so a stock stack needs no extra configuration.
_CONTROLLER = os.getenv("SLOP_CONTROLLER_UPSTREAM", "host.docker.internal:8800")
_SLEP = os.getenv("SLOP_SLEP_UPSTREAM", "host.docker.internal:8810")
_CONNECT = os.getenv("SLOP_CONNECT_UPSTREAM", "host.docker.internal:8700")
# This read SLOP_FLASHBACK_UPSTREAM, and then fell back to
# SLOP_FLASHBACK_UPSTREAM. The comment above it said "new name first, pre-rebrand
# name honoured" and both names were the same one — left over from a rebrand that
# was reverted, so the second read could only ever return what the first had
# already returned. Dead, but the kind of dead that reads as a working
# compatibility shim; see tests/test_no_value_falls_back_to_itself.py.
_FLASHBACK = os.getenv("SLOP_FLASHBACK_UPSTREAM", "flashback:8080")

_BASELINE = os.getenv("SLOP_BASELINE_UPSTREAM", "baseline:8080")

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


# Baseline checks its OWN gateway secret rather than the platform-wide one: its
# container publishes the console port on the docker bridge so the Controller can
# reach the agent API, and a Baseline state runs as root on the fleet, so a secret
# every service holds was one hop from a forged superuser there (see
# baseline/backend/identity.py). The Visualizer is one of the three holders by
# design, because it reads Baseline's feeds directly. Falls back to the shared
# secret, matching Baseline's own fallback, so an install that has not re-run
# install.sh is unchanged.
_BASELINE_SECRET = os.getenv("SYSIBLE_BASELINE_GATEWAY_SECRET", "") or _SSO_SECRET


def _headers(identity, secret: str | None = None) -> dict:
    """The caller's OWN identity, re-stamped for the upstream. Values come from the
    validated Identity, never from client-supplied headers. `secret` overrides the
    platform-wide proof for an app that checks its own."""
    h = {"Accept": "application/json"}
    proof = secret if secret is not None else _SSO_SECRET
    if proof:
        h["X-Sysible-Auth"] = proof
        h["X-Sysible-User"] = identity.user
        h["X-Sysible-Role"] = identity.role
    return h


def _get(url: str, identity, params: dict | None = None, want_json: bool = True,
         secret: str | None = None):
    """Fetch one upstream endpoint. Returns (data, error). `error` is a short
    operator-facing string; never raises, so one dead app can't blank the console."""
    try:
        with httpx.Client(timeout=_TIMEOUT, verify=False, follow_redirects=False) as c:
            r = c.get(url, headers=_headers(identity, secret), params=params or {})
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


# Every event carries WHO KIND OF ACTOR produced it, so the console can separate a
# person's work from the controller's own background sweeps and from key-only API
# callers. The Controller classifies its own rows and reports a `source`; the other
# apps don't, so we infer it from the actor name. Vocabulary is shared with the
# Controller's activity feed: "user" | "api" | "automation".
SOURCES = ("user", "api", "automation")

# Actor names that are a SERVICE, not a person. These are the identities the apps
# stamp on their own background work (health sweeps, agent check-ins, schedulers).
#
# Matched against actor names in rows that are ALREADY STORED, which is why old
# spellings stay: an actor this set does not recognise is taken to be a PERSON,
# so dropping one reclassifies every event the fleet ever recorded under it from
# "automation" to a named human.
#
# This read `"flashback", "flashback"` — the same name twice, under a comment
# saying one of them was the old spelling kept for history. It was left behind
# by a rebrand that was then reverted, so the set protected nothing and the
# comment described a compatibility shim that was not there. The name the rebrand
# actually used was "baseline", and it belongs here on its own merits as well:
# Sysible Baseline is a service that acts on hosts, and the Controller stamps its
# rule-driven work with that name.
_SERVICE_ACTORS = {"", "-", "controller", "agent", "system", "systemd",
                   "scheduler", "cron", "sysible", "slep",
                   "flashback", "baseline"}


def _source_for(actor, given=None) -> str:
    """Normalise an event's source. An explicit upstream value wins when it is one
    we know; otherwise classify by actor — a named human is "user", a service
    identity (or no actor at all) is "automation"."""
    g = str(given or "").strip().lower()
    if g in SOURCES:
        return g
    return "automation" if str(actor or "").strip().lower() in _SERVICE_ACTORS else "user"


# The fleet's own sweeps. The Controller runs these against EVERY host on a timer,
# so on a twenty-host fleet they are not merely the majority of the feed — they are
# effectively all of it. The reported symptom was an activity view in which every
# visible row was "collected host posture" or "checked for available package
# updates", each carrying the full shell command. That is the platform working, not
# anybody doing anything.
#
# Hidden by DEFAULT but never dropped: the toggle beside the source chips says how
# many there are and brings them straight back. A forensic view that silently
# discarded rows would be worse than a noisy one.
#
# These are the phrases the Controller stamps in backend/app.py's
# _COMMAND_SIGNATURES — exactly the entries it marks "automation", which are the
# ones it runs on a timer. A new sweep there needs a line here.
ROUTINE_ACTIONS = frozenset({
    "collected host posture",
    "checked for available package updates",
    "ran a fleet health check",
    "collected host metrics",
})


def _is_routine(action, source) -> bool:
    return source == "automation" and str(action or "").strip().lower() in ROUTINE_ACTIONS


def _ev(ts, actor, action, target="", detail="", _id=None, source=None) -> dict:
    src = _source_for(actor, source)
    return {
        "id": _id,
        "ts": float(ts or 0),
        "actor": str(actor or ""),
        "action": str(action or ""),
        "target": str(target or ""),
        "detail": str(detail or ""),
        "source": src,
        # Fleet housekeeping, as opposed to a scheduled job somebody created.
        "routine": _is_routine(action, src),
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
            # The Controller classifies its own rows (person / API key / its own
            # background sweeps), so take its word rather than guessing.
            events.append(_ev(e.get("timestamp"), e.get("username"),
                              e.get("description"), e.get("host"),
                              e.get("command"), e.get("id"),
                              source=e.get("source")))

    # The admin audit trail (logins, role changes…) — superuser-only upstream.
    data, err = _get(f"{base}/api/audit-log", identity, {"limit": limit})
    if err:
        notes.append(f"admin audit-log: {err}")
    else:
        for e in (data or {}).get("audit", []):
            events.append(_ev(e.get("timestamp"), e.get("username"),
                              e.get("event"), "", e.get("detail")))
    return {"events": events, "errors": errors, "notes": notes}


# THE /api PREFIX HERE IS LOAD-BEARING, and it is not what reading SLEP's
# backend suggests. :8810 is SLEP's web CONSOLE, not its API. That console
# serves /api/health itself, proxies /api/<rest> to the backend on :9100 as
# /<rest> (the backend has no /api), and answers everything else with its SPA's
# index.html.
#
# So /api/runs below reaches the backend's /runs and is correct, while /runs
# would NOT reach the backend's /runs — it would come back as a page of HTML
# for this module to parse as JSON. Grepping the backend for its routes says
# the opposite, convincingly. See test_every_upstream_path_exists.py, which
# models the hop rather than the destination.
def _slep(identity, limit: int) -> dict:
    base = _url(_SLEP, "https")
    events, errors, notes = [], [], []

    # Pipeline runs are SLEP's real activity: readable by any signed-in role.
    #
    # PASS THE LIMIT. This asked for no limit and then sliced the reply to
    # `limit`, which can be 500 — and SLEP's /runs took no limit and capped at
    # 100, so a busy SLEP's feed arrived truncated with nothing in `notes` to
    # say so, and looked complete. SLEP now accepts and echoes a bounded limit;
    # an older SLEP ignores the query parameter and still answers 100, which is
    # what `notes` below reports.
    data, err = _get(f"{base}/api/runs", identity, {"limit": limit})
    if err:
        errors.append(f"runs: {err}")
    else:
        rows = (data or {}).get("runs", [])
        served = (data or {}).get("limit")
        if served is None and len(rows) >= 100 and limit > 100:
            # No `limit` in the reply at all: a SLEP from before it accepted one,
            # which caps at 100. Say so rather than presenting a truncated feed
            # as the whole story.
            notes.append("this SLEP returns at most 100 runs per request "
                         "(upgrade it to raise the limit)")
        elif served is not None and len(rows) >= served and limit > served:
            notes.append(f"SLEP served its maximum of {served} runs; "
                         "the feed may be truncated")
        for r in rows[:limit]:
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
        # Connect gained its audit trail later than the others, so an older Connect
        # has no /api/audit. Say so plainly rather than showing an empty panel that
        # reads as "nothing ever happened".
        return {"events": [], "errors": [], "notes": [f"audit: {err}"]}
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


def _baseline(identity, limit: int) -> dict:
    """Sysible Baseline's runs, as activity.

    Baseline was missing from this console entirely — which made the portal
    tile ("activity and logs from every app ... Flashback and Baseline") and
    the Visualizer's own description untrue, and left the one app that CHANGES
    hosts as the one app whose work could not be read here.

    A run is the event: which host, which state, and what it did. "Already as
    declared" is deliberately kept rather than filtered — a fleet that needed
    no changes is the thing an operator most wants to be able to see.
    """
    # Baseline's own gateway secret, not the platform-wide one — see
    # _BASELINE_SECRET above.
    data, err = _get(_url(_BASELINE, "http") + "/api/runs", identity, {"limit": limit},
                     secret=_BASELINE_SECRET)
    if err:
        return {"events": [], "errors": [f"runs: {err}"], "notes": []}
    events = []
    for r in (data or {}).get("runs", []):
        state = f"{r.get('state_name') or '?'} v{r.get('state_version') or '?'}"
        if r.get("error"):
            what, detail = "baseline.failed", str(r["error"])[:300]
        elif r.get("failed"):
            what, detail = "baseline.failed", f"{r['failed']} state(s) failed"
        elif r.get("changed"):
            what, detail = "baseline.changed", f"{r['changed']} change(s)"
        else:
            what, detail = "baseline.ok", "already as declared"
        if r.get("preview"):
            what, detail = "baseline.preview", detail + " (preview, nothing applied)"
        events.append(_ev(r.get("started"), "baseline", what,
                          r.get("host_id") or "", f"{state}: {detail}", r.get("id")))
    return {"events": events, "errors": [], "notes": []}


# key -> (label, fetcher). Order is the tab order in the console.
APPS: dict[str, tuple] = {
    "controller": ("Sysible Controller", _controller),
    "slep": ("Sysible Linux Engineering Platform", _slep),
    "connect": ("Sysible Connect", _connect),
    "flashback": ("Sysible Flashback", _flashback),
    "baseline": ("Sysible Baseline", _baseline),
}

# Which apps actually expose a raw log. The console's "Log…" button was shown
# for ALL of them, so on the three that have none it failed every time with
# "this app exposes no log endpoint" — a control that could not work, offered
# unconditionally. Served to the console so the button and the refusal cannot
# disagree.
LOG_APPS = ("slep", "controller")


def has_log(app: str) -> bool:
    return app in LOG_APPS


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


# A SLEP run reference, as it appears in a run row. Deliberately narrow: this
# value is the ONLY caller-controlled part of any URL this module builds, and it
# lands in the PATH of a request that carries the platform shared secret.
# Unvalidated, "../../api/admin/users" collapsed to /api/admin/users/log before
# the request left the process — turning the log viewer into a way for ANY
# signed-in user, auditor included, to GET arbitrary SLEP endpoints through a
# privileged internal channel and read the raw body. "?" and "#" were just as
# effective, truncating the path and injecting a query.
_REF_RE = _re.compile(r"[A-Za-z0-9._-]{1,128}\Z")


def fetch_log(app: str, identity, ref: str) -> tuple[str | None, str | None]:
    """A raw log body for an app, when it has one. SLEP exposes per-run logs; the
    Controller exposes its own service log (superuser-only upstream)."""
    if app == "slep":
        ref = (ref or "").strip()
        if not _REF_RE.match(ref):
            return None, "not a valid run reference"
        return _get(_url(_SLEP, "https") + f"/api/runs/{_quote(ref, safe='')}/log",
                    identity, want_json=False)
    if app == "controller":
        return _get(_url(_CONTROLLER, "https") + "/api/controller-log", identity,
                    {"lines": 500}, want_json=False)
    return None, "this app exposes no log endpoint"


# --------------------------------------------------------------------------- #
# Fleet topology — the Controller's Network Topology view, served from here.
# --------------------------------------------------------------------------- #
# The map correlates FIVE Controller readings. Four are cheap; fleet-posture is a
# whole-fleet sweep that can take many seconds on a cold cache, so it is opt-in
# per request: the console loads the map without it and fires a second request to
# fill in the critical rings and gateway labels. Blocking the map on the slow one
# was exactly what made the original view feel broken on a large fleet.
def topology(identity, with_posture: bool = False) -> dict:
    """The merged fleet map for ONE caller, fetched with that caller's identity.

    Same trust rule as every other source: the upstream applies its own RBAC to
    the real human behind the request, so an auditor sees an auditor's fleet. A
    reading we can't get degrades the map (and is reported) rather than blanking
    it — a half-drawn map beats an empty one when one endpoint is slow.

    The readings are INDEPENDENT — nothing here feeds anything else, they are
    merged afterwards — so they are fetched together. Sequentially the map cost
    the sum of five round-trips, and its worst case was five times the per-request
    timeout: a Controller that was merely slow made the page look hung. Together,
    the cost is the slowest single reading.
    """
    base = _url(_CONTROLLER, "https")
    errors, notes = [], []

    reads = [("/api/hosts", "hosts", True),
             # Health/agents/suppressions are overlays: without them the map still
             # has its shape, just fewer colours, so their failure is a note.
             ("/api/fleet-health", "hosts", False),
             ("/api/agents", "agents", False),
             ("/api/suppressions", "suppressions", False)]
    if with_posture:
        reads.append(("/api/fleet-posture", "hosts", False))

    def read(spec):
        path, key, _fatal = spec
        data, err = _get(f"{base}{path}", identity)
        return (data or {}).get(key) or [], err

    with _futures.ThreadPoolExecutor(max_workers=len(reads)) as pool:
        results = list(pool.map(read, reads))

    got = {}
    # Report in the order the reads are declared, not the order they finished, so
    # the operator sees a stable list rather than one that reshuffles per load.
    # strict=True: pool.map guarantees one result per read, and if that ever
    # stops being true a silent truncation would drop an upstream's rows with
    # no error anywhere.
    for (path, _key, fatal), (rows, err) in zip(reads, results, strict=True):
        if err:
            (errors if fatal else notes).append(f"{path}: {err}")
        got[path] = rows

    nodes = fleet.build(got["/api/hosts"], got["/api/fleet-health"],
                        got["/api/agents"], got["/api/suppressions"],
                        got.get("/api/fleet-posture", []))
    return {
        "nodes": nodes,
        "parents": fleet.parents(nodes),
        "counts": fleet.counts(nodes),
        "posture": bool(with_posture),
        "errors": errors,
        "notes": notes,
    }
