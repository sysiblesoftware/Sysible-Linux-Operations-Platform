"""Sysible Linux Operations Platform — the updater sidecar.

SLOP Administration can rebuild and restart each product from the browser. Doing
that needs the host's Docker socket, which is root-equivalent on the host — so it
does NOT go to the IdP. The IdP terminates browser sessions and is the service an
attacker reaches first; giving it the socket would mean a flaw there is a root
shell on the host. Instead the socket lives here, in a service whose entire API
surface is:

    GET  /api/status          what is installed, and what is behind
    POST /api/update/{app}    update ONE product from a fixed allowlist
    GET  /api/job             how the running update is going

There is no endpoint that takes a path, a repository, a branch, an image or a
command. `app` is a key checked against apps.ALLOWLIST; everything else is
derived from configuration set on the host. Nothing runs through a shell.

The service is expose-only on the compose network (no published port) and every
request must carry the platform shared secret, so only the IdP — itself behind
the gateway's sign-in — can reach it.

It is still a privileged component. Treat a change here like a change to sudoers.
"""
from __future__ import annotations

import hmac
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import apps, git, jobs

app = FastAPI(title="Sysible updater", docs_url=None, redoc_url=None, openapi_url=None)

_SECRET = os.environ.get("SYSIBLE_SSO_SHARED_SECRET", "")
if not _SECRET:
    print("[sysible-updater] SYSIBLE_SSO_SHARED_SECRET is empty — every request will be "
          "refused (fail closed).", flush=True)


def _authorized(request: Request) -> str:
    """The calling operator, or 401/503. The shared secret proves the call came
    from inside the platform; X-Sysible-User is recorded as the actor.

    Fails closed with no secret configured: an updater that accepted unauthenticated
    calls would be a remote root shell on the host.
    """
    if not _SECRET:
        raise HTTPException(status_code=503,
                            detail="Updater is not configured (no shared secret).")
    if not hmac.compare_digest(request.headers.get("x-sysible-auth", ""), _SECRET):
        raise HTTPException(status_code=401, detail="Not authorized.")
    # The IdP only calls this for a superuser; the role is carried so the refusal
    # is enforced on BOTH sides rather than trusting the caller's own check.
    if (request.headers.get("x-sysible-role") or "").strip().lower() != "superuser":
        raise HTTPException(status_code=403, detail="Updating requires a superuser.")
    return (request.headers.get("x-sysible-user") or "").strip() or "unknown"


@app.middleware("http")
async def _headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "updater"}


@app.get("/api/status")
def status(request: Request) -> dict:
    _authorized(request)
    out = []
    for key in apps.keys():
        row = {"key": key, "label": apps.label(key)}
        root = apps.checkout_dir(key)
        if root is None:
            # Not every SLOP host runs every product; say so plainly rather than
            # showing it as broken.
            row.update({"installed": False, "checked": False,
                        "reason": "not installed on this host"})
            out.append(row)
            continue
        row["installed"] = True
        row.update(git.status(root))
        row["dirty"] = git.dirty(root)
        row["can_update"] = bool(row.get("available")) and not row["dirty"] \
            and apps.compose_dir(root) is not None
        if row["dirty"]:
            row["reason"] = "the checkout has local changes — resolve them on the host first"
        elif apps.compose_dir(root) is None:
            row["reason"] = "no compose file found in the checkout"
        out.append(row)
    return {"apps": out, "job": jobs.current()}


@app.post("/api/update/{key}")
def update(key: str, request: Request):
    actor = _authorized(request)
    if key not in apps.ALLOWLIST:
        raise HTTPException(status_code=404, detail="Unknown product.")
    root = apps.checkout_dir(key)
    if root is None:
        raise HTTPException(status_code=409, detail="That product is not installed here.")
    if git.dirty(root):
        raise HTTPException(status_code=409,
                            detail="That checkout has local changes — a pull would "
                                   "overwrite them. Resolve it on the host first.")
    compose = apps.compose_dir(root)
    if compose is None:
        raise HTTPException(status_code=409, detail="No compose file in that checkout.")
    started, message = jobs.start(key, root, compose, actor)
    if not started:
        raise HTTPException(status_code=409, detail=message)
    print(f"[sysible-updater] {actor} started an update of {key} ({root})", flush=True)
    return {"started": True, "message": message}


@app.get("/api/job")
def job(request: Request, tail: int = 200):
    _authorized(request)
    j = jobs.current()
    if not j:
        return JSONResponse({"job": None})
    tail = max(1, min(int(tail), jobs.MAX_LOG_LINES))
    j["log"] = j["log"][-tail:]
    return {"job": j}
