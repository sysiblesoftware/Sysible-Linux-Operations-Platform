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

from . import identity, store, ui

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
    if not identity.agent_auth_ok(request):
        raise HTTPException(status_code=401, detail="Invalid or missing agent token.")


# --------------------------------------------------------------------------- #
# Console + liveness
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def console(request: Request):
    who = _require_identity(request)
    return HTMLResponse(ui.page(who.user, who.role, who.can_write))


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
def api_hosts(request: Request) -> list:
    _require_identity(request)
    return store.list_hosts()


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
