"""Sysible Flashback — the standalone SLOP module (FastAPI service).

A config "time machine": host agents POST snapshots of their tracked config files;
Flashback stores every CHANGED version content-deduplicated, and lets an operator
browse each host's files, diff any two versions, download one, or restore one
(queued for the host's agent to write back). Fronted by the SLOP gateway at
/flashback with the shared SSO identity; agents authenticate with a bearer token.

Endpoints
  Browser (SSO identity via the gateway; auditor read-only):
    GET  /                                  the console (server-rendered UI)
    GET  /api/health                        liveness for the portal dot
    GET  /api/whoami                        the caller's identity/role
    GET  /api/hosts                         hosts + file/version counts
    GET  /api/hosts/{h}/files               tracked files for a host
    GET  /api/hosts/{h}/versions?path=      version timeline of a file
    GET  /api/hosts/{h}/download?path=&sha= raw content of one version
    GET  /api/hosts/{h}/diff?path=&a=&b=    unified diff between two versions
    POST /api/hosts/{h}/restore             queue a restore (operator+ only)
    GET  /api/hosts/{h}/restores            recent restore activity
  Agent (bearer token):
    POST /api/agent/snapshot                ingest a capture snapshot
    GET  /api/agent/restores?host_id=       pending restores to apply
    GET  /api/agent/restores/{id}/payload?host_id=  the bytes to write
    POST /api/agent/restores/{id}/ack?host_id=      mark applied/failed
"""
from __future__ import annotations

import base64
import os

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from . import controller, identity, store, ui

app = FastAPI(title="Sysible Flashback", docs_url=None, redoc_url=None, openapi_url=None)


# Bound request bodies: snapshots carry file content, so an unbounded POST could
# exhaust memory. 64 MiB default (a full /etc snapshot is well under this),
# overridable. Enforced against ACTUAL streamed bytes, not just Content-Length (a
# Content-Length-only check is bypassable with Transfer-Encoding: chunked).
_MAX_REQUEST_BYTES = int(os.environ.get("SYSIBLE_FLASHBACK_MAX_REQUEST_BYTES", str(64 * 1024 * 1024)))


class _BodyLimitASGI:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for k, v in scope.get("headers") or []:
            if k == b"content-length" and v.isdigit() and int(v) > self.max_bytes:
                return await self._too_large(scope, send)
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return
            body += message.get("body", b"")
            more_body = message.get("more_body", False)
            if len(body) > self.max_bytes:
                return await self._too_large(scope, send)
        sent = False

        async def replay_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)

    async def _too_large(self, scope, send):
        async def _noop_receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        await JSONResponse({"detail": "Request body too large."}, status_code=413)(scope, _noop_receive, send)


app.add_middleware(_BodyLimitASGI, max_bytes=_MAX_REQUEST_BYTES)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    # Defense-in-depth headers. The lone frame-ancestors CSP blocks clickjacking of
    # the restore controls without restricting the console's own fetch()/resources;
    # the gateway also stamps these, but this covers a standalone/direct deploy too.
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https":
        resp.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
    return resp


@app.on_event("startup")
def _startup() -> None:
    store.init_db()
    print(identity.startup_notice(), flush=True)


# --------------------------------------------------------------------------- #
# Identity helpers
# --------------------------------------------------------------------------- #
def _wants_html(request: Request) -> bool:
    """True for a browser navigation, as opposed to the console's own fetch() or an
    agent. Chooses only the ERROR REPRESENTATION — never authorization."""
    return "text/html" in (request.headers.get("accept") or "")


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    """Refuse in the representation the caller can actually read.

    A browser that follows the portal tile and gets a bare
    `{"detail":"Not signed in."}` has no way to tell a direct hit apart from a
    gateway that isn't stamping identity — it just looks like "Flashback does
    nothing". On 401/403 a navigation gets the diagnostic page instead, naming
    which wiring fault occurred (identity.deny_reason leaks nothing secret).
    API and agent callers keep the JSON contract unchanged.
    """
    if exc.status_code in (401, 403) and _wants_html(request):
        code, why = identity.deny_reason(request)
        if not why:                       # denied for a role reason, not a wiring one
            why = str(exc.detail)
        return HTMLResponse(ui.denied_page(why, code, exc.status_code),
                            status_code=exc.status_code, headers=exc.headers or {})
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                        headers=exc.headers or {})


def _require_identity(request: Request) -> identity.Identity:
    who = identity.current(request)
    if who is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return who


def _require_writer(request: Request) -> identity.Identity:
    who = _require_identity(request)
    if not who.can_write:
        raise HTTPException(status_code=403, detail="Read-only role — a restore needs operator access.")
    return who


def _require_agent(request: Request) -> None:
    if identity.agent_auth_ok(request):
        return
    if not identity.agent_configured():
        # Distinguish the two faults. Silently refusing an unconfigured install
        # looks identical to a wrong token, and this is exactly the wiring an
        # operator gets wrong when nothing ever appears in the console.
        raise HTTPException(
            status_code=503,
            detail="Flashback has no agent token configured, so it cannot accept "
                   "snapshots. Set SYSIBLE_FLASHBACK_AGENT_TOKEN on this service "
                   "and give the same value to the Controller.")
    raise HTTPException(status_code=401, detail="Invalid or missing agent token.")


# --------------------------------------------------------------------------- #
# Console + liveness
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def console(request: Request):
    who = _require_identity(request)
    return HTMLResponse(ui.page(who.user, who.role, who.can_write))


@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> Response:
    """The tab icon. Public and unauthenticated on purpose: the browser fetches it
    for the REFUSAL page too, which is served to callers who have no identity yet
    — and an icon is not a secret. The pages reference it RELATIVELY, so it
    resolves under whatever prefix the gateway serves this app at
    (/flashback/favicon.svg) and standalone at the root alike."""
    return Response(
        content=ui.FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "flashback"}


@app.get("/api/whoami")
def whoami(request: Request) -> dict:
    who = _require_identity(request)
    return {"user": who.user, "role": who.role, "can_write": who.can_write}


# --------------------------------------------------------------------------- #
# Browse / diff / download / restore  (SSO identity)
# --------------------------------------------------------------------------- #
@app.get("/api/hosts")
def api_hosts(request: Request) -> dict:
    """Hosts with stored history, PLUS the Controller's enrolled agent hosts that
    have not reported one yet. Showing only the former made a fleet that is wired
    correctly but has not captured yet look identical to one that is broken."""
    who = _require_identity(request)
    hosts = store.list_hosts()
    for h in hosts:
        h["backed_up"] = True
    known = {h["host_id"] for h in hosts}
    fleet, note = controller.list_hosts(who)
    for h in fleet:
        if h["host_id"] in known:
            continue
        hosts.append({"host_id": h["host_id"], "label": h["label"], "last_ts": None,
                      "files": 0, "versions": 0, "backed_up": False,
                      "environment": h.get("environment") or "",
                      "address": h.get("address") or ""})
    # Environment for hosts that DO have history too, so the grouping is complete.
    envs = {h["host_id"]: (h.get("environment") or "") for h in fleet}
    addrs = {h["host_id"]: (h.get("address") or "") for h in fleet}
    for h in hosts:
        h.setdefault("environment", "")
        h.setdefault("address", "")
        h["environment"] = h["environment"] or envs.get(h["host_id"], "")
        h["address"] = h["address"] or addrs.get(h["host_id"], "")
    hosts.sort(key=lambda h: (not h["backed_up"], (h.get("label") or "").lower()))
    return {"hosts": hosts, "note": note, "can_request": controller.configured()}


@app.get("/api/compare/paths")
def api_compare_paths(request: Request) -> dict:
    """Paths worth comparing, most-shared first."""
    _require_identity(request)
    return {"paths": store.paths_across_hosts()}


@app.get("/api/compare")
def api_compare(request: Request, path: str = Query(...),
                baseline: str = Query(default="")) -> dict:
    """Is this file the same across the fleet? Compared by content hash against
    a baseline host, so it is a lookup rather than a diff of every pair."""
    who = _require_identity(request)
    out = store.compare_path(path, baseline or None)
    # "No copy stored" must cover the whole FLEET, not just hosts that already
    # have some history. A host enrolled but never captured is exactly the one an
    # operator needs to see here, and the store cannot know about it.
    have = {out["baseline"]["host_id"]} if out.get("baseline") else set()
    have |= {h["host_id"] for h in out.get("hosts") or []}
    fleet, _note = controller.list_hosts(who)
    known = {m["host_id"] for m in out.get("missing") or []}
    for h in fleet:
        if h["host_id"] not in have and h["host_id"] not in known:
            out.setdefault("missing", []).append(
                {"host_id": h["host_id"], "label": h.get("label") or h["host_id"]})
    out["missing"] = sorted(out.get("missing") or [],
                            key=lambda m: (m.get("label") or "").lower())
    return out


@app.get("/api/compare/diff", response_class=PlainTextResponse)
def api_compare_diff(request: Request, path: str = Query(...),
                     a: str = Query(...), b: str = Query(...)):
    """The diff for one pair, fetched only when an operator opens it."""
    _require_identity(request)
    d = store.diff_across_hosts(path, a, b)
    if d is None:
        raise HTTPException(status_code=404,
                            detail="One of those hosts has no stored version of that path.")
    return d or "(identical)"


@app.post("/api/backup-now")
def api_backup_now_many(request: Request, body: dict = Body(default=None)) -> dict:
    """Back up several hosts, or the whole tracked fleet.

    Best-effort per host: one unreachable host must not cost the rest, so each
    result is reported and nothing aborts the batch."""
    who = _require_identity(request)
    if not who.can_write:
        raise HTTPException(status_code=403,
                            detail="Requesting a backup needs operator or superuser.")
    ids = (body or {}).get("host_ids")
    if ids == "all" or not ids:
        fleet, _note = controller.list_hosts(who)
        ids = [h["host_id"] for h in fleet] or [h["host_id"] for h in store.list_hosts()]
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="host_ids must be a list, or \"all\".")
    ids = [str(i) for i in ids if str(i).strip()][:500]
    if not ids:
        raise HTTPException(status_code=409, detail="No hosts to back up.")
    ok, failed = [], []
    for hid in ids:
        good, message = controller.request_capture(who, hid)
        (ok if good else failed).append({"host_id": hid, "message": message})
    store.log_audit(who.user, "request-backup",
                    f"{len(ok)} host(s) requested, {len(failed)} failed")
    return {"requested": len(ok), "failed": failed,
            "message": (f"Requested on {len(ok)} host(s) — each captures on its next "
                        "check-in (within a minute).")
                       + (f" {len(failed)} could not be asked." if failed else "")}


@app.post("/api/hosts/{host_id}/backup-now")
def api_backup_now(host_id: str, request: Request) -> dict:
    """"Back up now" for one host. Write action, so operator or superuser only —
    the same gate as queueing a restore."""
    who = _require_identity(request)
    if not who.can_write:
        raise HTTPException(status_code=403,
                            detail="Requesting a backup needs operator or superuser.")
    ok, message = controller.request_capture(who, host_id)
    if not ok:
        raise HTTPException(status_code=502, detail=message)
    store.log_audit(who.user, "request-backup", host_id)
    return {"requested": True, "message": message}


@app.get("/api/hosts/{host_id}/files")
def api_files(request: Request, host_id: str) -> list:
    _require_identity(request)
    return store.list_files(host_id)


@app.get("/api/hosts/{host_id}/versions")
def api_versions(request: Request, host_id: str, path: str = Query(...)) -> list:
    _require_identity(request)
    return store.list_versions(host_id, path)


@app.get("/api/hosts/{host_id}/download")
def api_download(request: Request, host_id: str, path: str = Query(...), sha: str = Query(...)):
    _require_identity(request)
    data = store.version_content(host_id, path, sha)
    if data is None:
        raise HTTPException(status_code=404, detail="No such version.")
    name = path.rstrip("/").split("/")[-1] or "file"
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}.{sha[:8]}"'},
    )


@app.get("/api/hosts/{host_id}/diff")
def api_diff(request: Request, host_id: str, path: str = Query(...),
             a: str = Query(...), b: str = Query(...)) -> dict:
    _require_identity(request)
    diff = store.diff_versions(host_id, path, a, b)
    if diff is None:
        raise HTTPException(status_code=404, detail="One or both versions not found for this file.")
    return {"host_id": host_id, "path": path, "a": a, "b": b, "diff": diff}


@app.post("/api/hosts/{host_id}/restore")
def api_restore(request: Request, host_id: str, body: dict = Body(...)) -> dict:
    who = _require_writer(request)
    path = str((body or {}).get("path") or "").strip()
    sha = str((body or {}).get("sha") or "").strip()
    if not path or not sha:
        raise HTTPException(status_code=400, detail="path and sha are required.")
    try:
        return store.queue_restore(host_id, path, sha, who.user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/audit")
def api_audit(request: Request, limit: int = Query(100, ge=1, le=500),
              since_id: int = Query(0, ge=0)) -> dict:
    """The audit trail (who restored/queued what). Readable by any signed-in
    identity incl. auditor — it is oversight data. Consumed by Sysible Visualizer."""
    _require_identity(request)
    return {"entries": store.list_audit(limit, since_id)}


@app.get("/api/hosts/{host_id}/restores")
def api_restore_activity(request: Request, host_id: str) -> list:
    _require_identity(request)
    return store.recent_restores(host_id)


# --------------------------------------------------------------------------- #
# Agent side  (bearer token)
# --------------------------------------------------------------------------- #
@app.post("/api/agent/snapshot")
def agent_snapshot(request: Request, body: dict = Body(...)) -> dict:
    _require_agent(request)
    host_id = str((body or {}).get("host_id") or "").strip()
    label = str((body or {}).get("label") or "")
    raw_files = (body or {}).get("files") or []
    files = []
    for f in raw_files:
        path = str(f.get("path") or "").strip()
        if not path:
            continue
        if "content_b64" in f:
            try:
                content = base64.b64decode(f["content_b64"])
            except Exception:
                raise HTTPException(status_code=400, detail=f"bad base64 for {path}")
        else:
            content = f.get("content", "")
        files.append({"path": path, "content": content})
    try:
        return store.ingest_snapshot(host_id, label, files)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/agent/restores")
def agent_restores(request: Request, host_id: str = Query(...)) -> list:
    _require_agent(request)
    return store.pending_restores(host_id)


@app.get("/api/agent/restores/{restore_id}/payload")
def agent_restore_payload(request: Request, restore_id: int, host_id: str = Query(...)):
    _require_agent(request)
    payload = store.restore_payload(host_id, restore_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="No such pending restore.")
    meta, content = payload
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "X-Flashback-Path": meta["path"],
            "X-Flashback-Sha256": meta["sha256"],
            "Content-Disposition": 'attachment; filename="restore.bin"',
        },
    )


@app.post("/api/agent/restores/{restore_id}/ack")
def agent_restore_ack(request: Request, restore_id: int, host_id: str = Query(...),
                      body: dict = Body(default=None)) -> dict:
    _require_agent(request)
    ok = True
    if isinstance(body, dict) and "ok" in body:
        ok = bool(body["ok"])
    if not store.ack_restore(host_id, restore_id, ok):
        raise HTTPException(status_code=404, detail="No such pending restore.")
    return {"id": restore_id, "status": "applied" if ok else "failed"}


# A tiny plain-text health alias some probes prefer.
@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"
