"""Sysible Baseline — the things that must not go wrong in a config manager.

A configuration tool has a small number of failure modes that are genuinely
dangerous, and they are not the obvious ones. It failing loudly is fine. What is
not fine:

  * Reporting a host as "as declared" when it never actually evaluated anything.
    A render error, a missing salt-call, garbage on stdout — any of these parsed
    as an empty success means a drifted fleet shows up green.
  * Firing follow-up actions on a host that did not change. "Restart on config
    change" that fires every scheduled run is an outage on a timer.
  * Firing follow-up actions off a PREVIEW. A dry run changes nothing; a restart
    for a change that was never made is worse than no preview at all.
  * Losing the previous declaration. "What did this say yesterday" is the first
    question asked when a fleet drifts.
"""
import importlib
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

SECRET = "s" * 32
TOKEN = "t" * 64
OPERATOR = {"x-sysible-user": "alice", "x-sysible-role": "operator", "x-sysible-auth": SECRET}
AGENT = {"authorization": "Bearer " + TOKEN}


@pytest.fixture()
def cl(tmp_path):
    from starlette.testclient import TestClient
    os.environ.update(
        SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1",
        SYSIBLE_SSO_SHARED_SECRET=SECRET,
        SYSIBLE_BASELINE_AGENT_TOKEN=TOKEN,
        SYSIBLE_BASELINE_DATA=str(tmp_path),
    )
    os.environ.pop("SYSIBLE_BASELINE_DB", None)
    for mod in ("backend.env", "backend.identity", "backend.store", "backend.app"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    import backend.app as m
    m = importlib.reload(m)
    m.store.init_db()
    with TestClient(m.app, base_url="http://slop.lan") as c:
        c._mod = m
        yield c


def _ret(**states):
    """A salt-call --out=json return with the given state outcomes."""
    body = {}
    for sid, (result, changed) in states.items():
        body[f"pkg_|-{sid}_|-{sid}_|-installed"] = {
            "result": result, "changes": {"x": 1} if changed else {},
            "comment": "", "name": sid}
    return json.dumps({"local": body})


def _declare(cl, name="web", sls="nginx:\n  pkg.installed: []\n", host="h1"):
    assert cl.post(f"/api/states/{name}", headers=OPERATOR, json={"sls": sls}).status_code == 200
    assert cl.post("/api/assign", headers=OPERATOR,
                   json={"target": host, "state": name}).status_code == 200
    d = cl.get("/api/agent/desired", headers=AGENT, params={"host_id": host}).json()
    return d["apply"][0]


# ---- a host that could not be evaluated is never "clean" -------------------

@pytest.mark.parametrize("output,why", [
    ("", "salt-call printed nothing"),
    ("bash: salt-call: command not found", "salt is not installed on the host"),
    ('{"local": []}', "the state could not be rendered"),
    ('{"local": {}}', "salt returned no state results"),
    ("{not json", "truncated output"),
])
def test_an_unevaluated_host_is_an_error_not_a_pass(cl, output, why):
    """THE one that matters. Every one of these used to be indistinguishable
    from a host that was already perfect."""
    item = _declare(cl)
    r = cl.post("/api/agent/result", headers=AGENT,
                json={"run_id": item["run_id"], "output": output}).json()
    assert r["ok"] is False, f"{why}: reported as a successful run"
    assert r["error"], f"{why}: no reason recorded"
    assert r["actions"] == [], "rules fired off a run that never happened"


def test_a_failed_state_is_counted(cl):
    item = _declare(cl)
    r = cl.post("/api/agent/result", headers=AGENT, json={
        "run_id": item["run_id"], "output": _ret(good=(True, False), bad=(False, False))}).json()
    assert r["ok"] is False and r["failed"] == 1


# ---- follow-up actions ----------------------------------------------------

def test_a_steady_host_fires_nothing(cl):
    """"Restart nginx when its config changes" must not restart nginx on every
    scheduled run. That is an outage on a timer, and it is the single most
    likely way for this feature to do harm."""
    cl.post("/api/rules", headers=OPERATOR, json={
        "name": "restart", "match": "*", "trigger": "changed",
        "action": "command", "argument": "systemctl reload nginx"})
    item = _declare(cl)
    r = cl.post("/api/agent/result", headers=AGENT,
                json={"run_id": item["run_id"], "output": _ret(nginx=(True, False))}).json()
    assert r["changed"] == 0
    assert r["actions"] == [], "a rule fired on a host that did not change"


def test_a_changed_host_fires_once(cl):
    cl.post("/api/rules", headers=OPERATOR, json={
        "name": "restart", "match": "*", "trigger": "changed",
        "action": "command", "argument": "systemctl reload nginx"})
    item = _declare(cl)
    r = cl.post("/api/agent/result", headers=AGENT, json={
        "run_id": item["run_id"],
        "output": _ret(a=(True, True), b=(True, True), c=(True, True))}).json()
    assert [a["rule"] for a in r["actions"]] == ["restart"], \
        "one rule fired once per changed state instead of once per run"


def test_a_preview_never_fires_anything(cl):
    """A dry run changes nothing on the host. Restarting a service for a change
    that was never made is worse than having no preview at all."""
    cl.post("/api/rules", headers=OPERATOR, json={
        "name": "restart", "match": "*", "trigger": "changed", "action": "command",
        "argument": "systemctl reload nginx"})
    cl.post("/api/states/web", headers=OPERATOR, json={"sls": "a:\n  pkg.installed: []\n"})
    cl.post("/api/assign", headers=OPERATOR, json={"target": "h1", "state": "web"})
    d = cl.get("/api/agent/desired", headers=AGENT,
               params={"host_id": "h1", "preview": "true"}).json()["apply"][0]
    assert "test=True" in d["command"], "preview did not ask salt for a dry run"
    r = cl.post("/api/agent/result", headers=AGENT, json={
        "run_id": d["run_id"], "preview": True, "output": _ret(a=(True, True))}).json()
    assert r["actions"] == [], "a preview fired real actions"


def test_a_capture_rule_hands_off_to_flashback(cl):
    """The product seam: Baseline sets the configuration, Flashback records what
    the host then actually looks like. Two services, deliberately."""
    cl.post("/api/rules", headers=OPERATOR,
            json={"name": "snapshot", "match": "*", "trigger": "changed", "action": "capture"})
    item = _declare(cl)
    r = cl.post("/api/agent/result", headers=AGENT,
                json={"run_id": item["run_id"], "output": _ret(a=(True, True))}).json()
    assert [(a["rule"], a["action"]) for a in r["actions"]] == [("snapshot", "capture")]


def test_a_command_rule_with_no_command_is_refused(cl):
    """It would fire, do nothing, and look like protection."""
    r = cl.post("/api/rules", headers=OPERATOR, json={
        "name": "empty", "match": "*", "trigger": "changed", "action": "command"})
    assert r.status_code == 400


# ---- the declaration itself ------------------------------------------------

def test_saving_a_state_keeps_the_previous_one(cl):
    cl.post("/api/states/web", headers=OPERATOR, json={"sls": "first:\n  test.nop: []\n"})
    cl.post("/api/states/web", headers=OPERATOR, json={"sls": "second:\n  test.nop: []\n"})
    body = cl.get("/api/states/web", headers=OPERATOR).json()
    assert [v["version"] for v in body["versions"]] == [2, 1], \
        "an edit overwrote the previous declaration"
    assert "second" in body["state"]["sls"]


def test_an_empty_state_is_refused(cl):
    """Applying an empty state is not 'no change' — it is a declaration that
    nothing is declared, saved over something that was."""
    assert cl.post("/api/states/web", headers=OPERATOR,
                   json={"sls": "   \n"}).status_code == 400


def test_a_run_records_which_version_was_applied(cl):
    item = _declare(cl)
    cl.post("/api/agent/result", headers=AGENT,
            json={"run_id": item["run_id"], "output": _ret(a=(True, True))})
    run = cl.get("/api/runs", headers=OPERATOR).json()["runs"][0]
    assert run["state_version"] == 1 and run["state_name"] == "web"


# ---- access ---------------------------------------------------------------

def test_the_agent_api_fails_closed_without_a_token(cl, monkeypatch):
    m = cl._mod
    monkeypatch.setattr(m.identity, "_AGENT_TOKEN", "")
    r = cl.get("/api/agent/desired", headers=AGENT, params={"host_id": "h1"})
    assert r.status_code == 503 and "agent token" in r.json()["detail"]


def test_a_browser_cannot_read_the_fleets_configuration(cl):
    assert cl.get("/api/states").status_code == 401
    assert cl.get("/api/agent/desired", params={"host_id": "h1"}).status_code in (401, 503)


def test_the_console_says_which_way_sign_in_failed(cl):
    body = cl.get("/").text
    assert "/baseline/" in body and "login" in body, \
        "the refusal page does not say how to sign in"
