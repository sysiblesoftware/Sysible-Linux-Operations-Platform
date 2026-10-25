"""Sysible Baseline — desired-state configuration for the fleet (FastAPI service).

WHERE THIS SITS. Baseline declares what a host's configuration must be; Salt
makes it so; the event rules decide what happens afterwards. It does not talk to
hosts, and that is deliberate — the Controller already holds the only
authenticated channel to every enrolled agent, and giving a second service its
own fleet credentials doubles the blast radius of a compromise for no gain.

So the flow mirrors Sysible Flashback's, in the other direction:

    Controller  --GET  /api/agent/desired?host_id=--> Baseline   (what should it be?)
    Controller  --runs salt-call on the host via its own agent channel-->
    Controller  --POST /api/agent/result----------->  Baseline   (here is what happened)

Baseline parses that result, decides which rules fired, and records both. The
Controller carries out the actions, because the Controller is the thing with a
channel to the host.

Both agent routes are guarded by one bearer token and FAIL CLOSED without it:
they describe and record a fleet's configuration, which is not public.
"""

from __future__ import annotations

import os

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from . import controller, eda, env, identity, salt, store, ui

app = FastAPI(title="Sysible Baseline", docs_url=None, redoc_url=None, openapi_url=None)

_MAX_REQUEST_BYTES = int(env.getenv("MAX_REQUEST_BYTES", str(16 * 1024 * 1024)))


@app.middleware("http")
async def _cap_body(request: Request, call_next):
    """A state is text and a salt return is a JSON document; neither is large.
    An uncapped body on an authenticated endpoint is a memory exhaustion away
    from taking the service down."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > _MAX_REQUEST_BYTES:
        return JSONResponse({"detail": "Request body too large."}, status_code=413)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return resp


@app.on_event("startup")
def _startup() -> None:
    store.init_db()
    print(identity.startup_notice(), flush=True)


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
def _require(request: Request, role: str = "operator"):
    who = identity.current(request)
    if who is None:
        raise HTTPException(status_code=401, detail=identity.deny_reason(request)[1])
    if role == "operator" and who.role not in ("operator", "superuser"):
        raise HTTPException(status_code=403, detail="This needs an operator account.")
    if role == "superuser" and who.role != "superuser":
        raise HTTPException(status_code=403, detail="This needs a superuser account.")
    return who


def _require_agent(request: Request) -> None:
    if not identity.agent_auth_ok(request):
        raise HTTPException(
            status_code=503 if not identity.agent_configured() else 401,
            detail=("Baseline has no agent token configured, so it cannot describe or "
                    "record configuration. Set SYSIBLE_BASELINE_AGENT_TOKEN on this "
                    "service and on the Controller.")
            if not identity.agent_configured() else "Bad agent token.")


# --------------------------------------------------------------------------- #
# Console (humans)
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def console(request: Request):
    who = identity.current(request)
    if who is None:
        code, why = identity.deny_reason(request)
        return HTMLResponse(ui.refusal_page(code, why), status_code=401)
    return HTMLResponse(ui.console_page(who))


@app.get("/favicon.svg")
def favicon():
    return PlainTextResponse(ui.FAVICON_SVG, media_type="image/svg+xml",
                             headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "baseline"}


@app.get("/api/states")
def api_states(request: Request) -> dict:
    _require(request, "operator")
    return {"states": store.list_states()}


@app.get("/api/states/{name}")
def api_state(name: str, request: Request, version: int = Query(default=0)) -> dict:
    _require(request, "operator")
    rec = store.latest_state(name) if not version else None
    if version:
        for v in store.state_versions(name):
            if v["version"] == version:
                rec = store.latest_state(name)
                break
    if rec is None:
        raise HTTPException(status_code=404, detail="No such state.")
    return {"state": rec, "versions": store.state_versions(name)}


@app.post("/api/states/{name}")
def api_save_state(name: str, request: Request, body: dict = Body(...)) -> dict:
    who = _require(request, "operator")
    sls = (body or {}).get("sls") or ""
    try:
        # Reject here rather than at apply time: a state that cannot build a
        # command is a state that would fail on every host it is assigned to.
        salt.apply_command(sls, state_name=name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return store.save_state(name, sls, author=who.user)


@app.post("/api/assign")
def api_assign(request: Request, body: dict = Body(...)) -> dict:
    _require(request, "operator")
    target = (body.get("target") or "").strip()
    state_name = (body.get("state") or "").strip()
    if not target or not state_name:
        raise HTTPException(status_code=400, detail="target and state are both required.")
    if store.latest_state(state_name) is None:
        raise HTTPException(status_code=404, detail="No such state.")
    store.assign(target, state_name)
    return {"target": target, "states": store.states_for(target)}


@app.post("/api/unassign")
def api_unassign(request: Request, body: dict = Body(...)) -> dict:
    _require(request, "operator")
    store.unassign((body.get("target") or "").strip(), (body.get("state") or "").strip())
    return {"ok": True}


@app.get("/api/assignments")
def api_assignments(request: Request) -> dict:
    _require(request, "operator")
    return {"assignments": store.list_assignments()}


@app.post("/api/hosts/{target}/apply")
def api_apply(target: str, request: Request, body: dict = Body(default=None)) -> dict:
    """Ask the Controller to apply this target's desired state.

    Operator+, and the Controller re-checks that against the SAME human — this
    service proves who is asking, it does not vouch for them.
    """
    who = _require(request, "operator")
    preview = bool((body or {}).get("preview"))
    ok, message = controller.request_apply(who, target, preview=preview)
    if not ok:
        raise HTTPException(status_code=502, detail=message)
    return {"target": target, "preview": preview, "detail": message}


@app.get("/api/rules")
def api_rules(request: Request) -> dict:
    _require(request, "operator")
    rules = store.list_rules()
    for r in rules:
        r["describes"] = eda.describe(r)
    return {"rules": rules, "actions": list(eda.ACTIONS), "triggers": list(eda.TRIGGERS)}


@app.post("/api/rules")
def api_save_rule(request: Request, body: dict = Body(...)) -> dict:
    _require(request, "operator")
    name = (body.get("name") or "").strip()
    action = (body.get("action") or "notify").strip().lower()
    trigger = (body.get("trigger") or "changed").strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="a rule needs a name.")
    if action not in eda.ACTIONS:
        raise HTTPException(status_code=400,
                            detail=f"Unknown action. One of: {', '.join(eda.ACTIONS)}.")
    if trigger not in eda.TRIGGERS:
        raise HTTPException(status_code=400,
                            detail=f"Unknown trigger. One of: {', '.join(eda.TRIGGERS)}.")
    if action == "command" and not (body.get("argument") or "").strip():
        raise HTTPException(status_code=400,
                            detail="A 'command' rule with no command would fire and do nothing.")
    store.save_rule(name, (body.get("match") or "*").strip(), trigger, action,
                    (body.get("argument") or "").strip(), bool(body.get("enabled", True)))
    return {"ok": True}


@app.delete("/api/rules/{name}")
def api_delete_rule(name: str, request: Request) -> dict:
    _require(request, "operator")
    store.delete_rule(name)
    return {"ok": True}


@app.get("/api/runs")
def api_runs(request: Request, host_id: str = Query(default=""),
             limit: int = Query(default=50, le=500)) -> dict:
    _require(request, "operator")
    return {"runs": store.recent_runs(limit=limit, host_id=host_id),
            "drift": store.drift_summary()}


# --------------------------------------------------------------------------- #
# Agent-facing (the Controller)
# --------------------------------------------------------------------------- #
@app.get("/api/agent/desired")
def agent_desired(request: Request, host_id: str = Query(...),
                  environment: str = Query(default=""),
                  preview: bool = Query(default=False)) -> dict:
    """What this host must look like, and the command that makes it so.

    Returns one entry per assigned state. The Controller runs each command on
    the host through its own agent channel and posts the output back.
    """
    _require_agent(request)
    names = store.states_for(host_id)
    if environment:
        names += [n for n in store.states_for(f"env:{environment}") if n not in names]
    out = []
    for name in names:
        rec = store.latest_state(name)
        if rec is None:
            continue
        run_id = store.start_run(host_id, name, rec["version"], preview)
        out.append({
            "run_id": run_id,
            "state": name,
            "version": rec["version"],
            "preview": bool(preview),
            "command": salt.apply_command(rec["sls"], test=preview, state_name=name),
        })
    return {"host_id": host_id, "apply": out}


@app.post("/api/agent/result")
def agent_result(request: Request, body: dict = Body(...)) -> dict:
    """The salt-call output for one run. Parsed, stored, and run through the
    event rules — whose actions are returned for the Controller to carry out."""
    _require_agent(request)
    run_id = body.get("run_id")
    if not isinstance(run_id, int):
        raise HTTPException(status_code=400, detail="run_id is required.")
    parsed = salt.parse_return(body.get("output") or "")
    # Rules see only what actually happened. A preview changes nothing, so
    # firing "restart nginx" off a dry run would be a real restart for a change
    # that was never made.
    actions = [] if body.get("preview") else eda.evaluate(store.list_rules(), parsed)
    store.finish_run(run_id, parsed, actions)
    return {"run_id": run_id, "ok": parsed["ok"], "changed": parsed["changed"],
            "failed": parsed["failed"], "error": parsed["error"], "actions": actions}
