"""Sysible Visualizer — the SLOP activity & log console.

One place to read what happened across the whole platform, SEPARATED BY APP:
Controller, SLEP, Connect and Flashback each get their own panel, fed by that
app's own activity/audit API. Visualizer stores nothing and mutates nothing — it
is a read-only aggregator that forwards the caller's own SSO identity upstream, so
each app applies its own role rules to the real human behind the request
(see sources.py for the trust argument).

Endpoints
    GET  /                      the console (server-rendered UI)
    GET  /api/health            liveness for the portal dot
    GET  /api/whoami            the caller's identity/role
    GET  /api/apps              the app list + which are reachable
    GET  /api/activity?app=&limit=   one app's normalised events
    GET  /api/log?app=&ref=     a raw log body (SLEP run log / Controller service log)
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from . import identity, sources, ui

app = FastAPI(title="Sysible Visualizer", docs_url=None, redoc_url=None, openapi_url=None)

# Visualizer takes no uploads; a small cap is plenty and closes the unbounded-body
# memory DoS. Enforced against ACTUAL streamed bytes, not just Content-Length (a
# Content-Length-only check is bypassable with Transfer-Encoding: chunked).
_MAX_REQUEST_BYTES = int(os.environ.get("SYSIBLE_VISUALIZER_MAX_REQUEST_BYTES", str(1 * 1024 * 1024)))


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
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    # Everything here is another user's audit data — never let it sit in a cache.
    resp.headers.setdefault("Cache-Control", "no-store")
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https":
        resp.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
    return resp


@app.on_event("startup")
def _startup() -> None:
    print(identity.startup_notice(), flush=True)


def _wants_html(request: Request) -> bool:
    """True for a browser navigation, as opposed to the console's own fetch().
    Chooses only the ERROR REPRESENTATION — never authorization."""
    return "text/html" in (request.headers.get("accept") or "")


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    """Refuse in the representation the caller can actually read: a navigation gets
    the diagnostic page naming the wiring fault, fetch()/API callers keep JSON."""
    if exc.status_code in (401, 403) and _wants_html(request):
        code, why = identity.deny_reason(request)
        if not why:
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


@app.get("/", response_class=HTMLResponse)
def console(request: Request):
    who = _require_identity(request)
    return HTMLResponse(ui.page(who.user, who.role))


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "visualizer"}


@app.get("/api/whoami")
def whoami(request: Request) -> dict:
    who = _require_identity(request)
    return {"user": who.user, "role": who.role}


@app.get("/api/apps")
def api_apps(request: Request) -> dict:
    _require_identity(request)
    return {"apps": [{"key": k, "label": sources.APPS[k][0]} for k in sources.app_keys()]}


@app.get("/api/activity")
def api_activity(request: Request, app: str = Query(...),
                 limit: int = Query(100, ge=1, le=sources.MAX_LIMIT)) -> dict:
    who = _require_identity(request)
    try:
        return sources.fetch(app, who, limit)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown app.")


@app.get("/api/log", response_class=PlainTextResponse)
def api_log(request: Request, app: str = Query(...), ref: str = Query("")):
    who = _require_identity(request)
    if app not in sources.APPS:
        raise HTTPException(status_code=404, detail="Unknown app.")
    text, err = sources.fetch_log(app, who, ref)
    if err:
        raise HTTPException(status_code=502, detail=err)
    return PlainTextResponse(text or "")
