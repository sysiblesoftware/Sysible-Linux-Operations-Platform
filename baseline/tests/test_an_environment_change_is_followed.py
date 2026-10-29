"""A host that moves environment in the Controller is followed here.

Asked for: "make sure that if I change some hosts to a new env in controller
it's recognized across the platform."

The good news first, because it determines what is actually at risk. Baseline's
`assignments` table is the ONLY place in SLOP that stores an environment NAME —
as a target of the form `env:<name>`. Everywhere else the environment is read
live from the Controller: the console re-reads /api/targets on every poll, and
the agent path is handed the host's environment by the Controller at apply time
(see api_desired in app.py). Nothing caches it. So moving a host from one
environment to another changes what it must satisfy with nothing to invalidate
and nothing to re-assign.

What is NOT free is the other direction: an assignment left pointing at an
environment the Controller no longer reports, because it was renamed or because
its last host moved out. That assignment stays in the table, covers nothing, and
looks exactly like one that covers everything — the state is still declared and
still assigned, it simply reaches no host. Nobody notices until those hosts turn
out never to have been configured.

So: the move is tested, and the orphan is tested, because only one of them was
ever going to be a problem.

Run: pytest baseline/tests/test_an_environment_change_is_followed.py
"""
import importlib
import json
import os
import sys
import threading
import time
import urllib.request
import warnings

import pytest

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

SECRET = "s" * 32
AGENT = "t" * 64
PORT = 8889
OP = {"x-sysible-user": "alice", "x-sysible-role": "operator",
      "x-sysible-auth": SECRET, "content-type": "application/json"}

# Mutable, so a test can move a host the way the Controller would.
FLEET = {"hosts": [
    {"id": "web-01", "label": "web-01", "environment": "staging",
     "kind": "agent", "address": "10.0.0.11", "online": True},
    {"id": "web-02", "label": "web-02", "environment": "staging",
     "kind": "agent", "address": "10.0.0.12", "online": True},
]}


def _envs():
    return sorted({h["environment"] for h in FLEET["hosts"] if h["environment"]})


def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import uvicorn
    tmp = tmp_path_factory.mktemp("envmove")
    os.environ.update(
        SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1", SYSIBLE_SSO_SHARED_SECRET=SECRET,
        SYSIBLE_BASELINE_AGENT_TOKEN=AGENT, SYSIBLE_BASELINE_DATA=str(tmp))
    os.environ.pop("SYSIBLE_BASELINE_DB", None)
    import backend as pkg
    names = ("backend", "backend.env", "backend.identity", "backend.store",
             "backend.app", "backend.controller", "backend.ui")
    attrs = ("env", "identity", "store", "app", "controller", "ui")
    saved_mods = {n: sys.modules[n] for n in names if n in sys.modules}
    saved_attrs = {a: getattr(pkg, a) for a in attrs if hasattr(pkg, a)}
    for n in names:
        sys.modules.pop(n, None)
    for a in saved_attrs:
        delattr(pkg, a)

    m = importlib.import_module("backend.app")
    m.controller.list_targets = lambda who: (FLEET["hosts"], _envs(), None)
    m.store.init_db()
    m.store.save_state("hardening", "openssh:\n  pkg.installed: []\n", "alice",
                       source="yaml")
    m.store.save_state("webserver", "nginx:\n  pkg.installed: []\n", "alice",
                       source="yaml")
    m.store.assign("env:staging", "hardening")

    srv = uvicorn.Server(uvicorn.Config(m.app, host="127.0.0.1", port=PORT,
                                        log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "baseline did not come up"
    try:
        yield f"http://127.0.0.1:{PORT}", m
    finally:
        srv.should_exit = True
        t.join(timeout=10)
        for n in names:
            sys.modules.pop(n, None)
        sys.modules.update(saved_mods)
        for a, v in saved_attrs.items():
            setattr(pkg, a, v)


@pytest.fixture(autouse=True)
def fleet_back():
    """Every test starts from the same fleet, whatever the last one moved."""
    before = json.loads(json.dumps(FLEET["hosts"]))
    yield
    FLEET["hosts"] = before


def _get(base, path, headers=None):
    req = urllib.request.Request(base + path, headers=headers or OP)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def _desired(base, host_id, environment):
    """What the agent channel is told this host must satisfy.

    The Controller passes the environment it holds for the host, which is the
    whole reason a move needs nothing invalidated here.
    """
    q = f"/api/agent/desired?host_id={host_id}&environment={environment}"
    req = urllib.request.Request(
        base + q, headers={"Authorization": f"Bearer {AGENT}"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


# ---- the move ---------------------------------------------------------------
def test_a_host_in_the_environment_must_satisfy_what_it_is_assigned(server):
    base, _ = server
    got = _desired(base, "web-01", "staging")
    assert [e["state"] for e in got["apply"]] == ["hardening"], got


def test_moving_a_host_to_a_new_environment_changes_what_it_must_satisfy(server):
    """The ask, directly. Nothing is re-assigned and nothing is invalidated —
    the environment arrives with the request."""
    base, _ = server
    assert [e["state"] for e in _desired(base, "web-01", "staging")["apply"]] \
        == ["hardening"]
    # The Controller moves it.
    FLEET["hosts"][0]["environment"] = "production"
    after = _desired(base, "web-01", "production")
    assert [e["state"] for e in after["apply"]] == [], (
        "the host still inherits its OLD environment's states after moving")


def test_moving_a_host_INTO_an_assigned_environment_starts_covering_it(server):
    base, m = server
    m.store.assign("env:production", "webserver")
    try:
        assert [e["state"] for e in _desired(base, "web-02", "staging")["apply"]] \
            == ["hardening"]
        FLEET["hosts"][1]["environment"] = "production"
        got = [e["state"] for e in _desired(base, "web-02", "production")["apply"]]
        assert got == ["webserver"], got
    finally:
        m.store.unassign("env:production", "webserver")


def test_the_console_reads_the_environment_live(server):
    """The fleet the console draws comes from the Controller on every poll, so
    there is no cache here to go stale."""
    base, _ = server
    assert {h["id"]: h["environment"] for h in _get(base, "/api/targets")["hosts"]} \
        == {"web-01": "staging", "web-02": "staging"}
    FLEET["hosts"][0]["environment"] = "production"
    fresh = _get(base, "/api/targets")
    assert fresh["hosts"][0]["environment"] == "production"
    assert "production" in fresh["environments"], fresh["environments"]


def test_no_part_of_slop_stores_an_environment_except_the_assignment(server):
    """The claim the rest of this file rests on. If an environment name starts
    being persisted somewhere else, moving a host stops being free and this
    test is where that gets noticed."""
    import pathlib
    root = pathlib.Path(HERE).parent
    offenders = []
    for py in root.glob("*/backend/*.py"):
        if py.name in ("ui.py",):
            continue
        text = py.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if "environment" not in low:
                continue
            if any(k in low for k in ("insert into", "update ", "create table")):
                offenders.append(f"{py.relative_to(root)}:{i}: {line.strip()}")
    assert not offenders, (
        "an environment name is being persisted outside the assignment target:\n"
        + "\n".join(offenders))


# ---- the orphan -------------------------------------------------------------
@pytest.mark.skipif(not _playwright(), reason="playwright not installed")
def test_an_assignment_to_a_vanished_environment_is_named_on_the_screen(server):
    """Rename an environment, or move its last host out, and the assignment
    stays — pointing at nothing. A state assigned to nothing looks exactly like
    a state assigned to something."""
    from playwright.sync_api import sync_playwright
    base, _ = server
    # Both hosts leave staging, so `env:staging` now matches no environment the
    # Controller reports.
    for h in FLEET["hosts"]:
        h["environment"] = "production"

    chrome = os.environ.get("SYSIBLE_TEST_CHROMIUM")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
        except Exception as e:
            pytest.skip(f"no chromium to drive ({e})")
        ctx = b.new_context(viewport={"width": 1440, "height": 900},
                            extra_http_headers={k: v for k, v in OP.items()
                                                if k != "content-type"})
        page = ctx.new_page()
        page.goto(base + "/", wait_until="domcontentloaded")
        page.wait_for_selector("#browselist .it", timeout=10000)
        page.click("#by-state")
        page.wait_for_timeout(400)
        page.click("[data-state='hardening']")
        page.wait_for_timeout(600)

        text = page.text_content("#col3")
        assert "covers nothing" in text, \
            f"an assignment that reaches no host is not called out: {text[:400]}"
        assert "staging" in text, "the environment that vanished is not named"
        assert "does not report this" in text, text[:400]
        b.close()
