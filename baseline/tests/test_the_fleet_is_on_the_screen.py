"""The host list, and what each host is supposed to be.

Reported, looking at a console with one state declared and nothing else:
"This doesn't feel intuitive where is the host list? Why isn't easy?"

It was a fair hiding. The console listed declarations, assignments, rules and
runs — four tables about configuration — and never the fleet. The hosts existed
on that screen only inside the Assign dialog, which you reach by pressing a
button on a state. So the question an operator actually arrives with ("what is
this host supposed to be, and is it?") could only be answered by reading two
tables and joining them in your head, and the answer to "which hosts are
there?" was: open a modal.

The fleet is the first thing on the page now, and each host carries the join:

  * the states it must satisfy, INCLUDING the ones it inherits from its
    environment, marked as inherited — that rule was something you had to know
    and apply yourself;
  * what the last run made of it, in words;
  * and the actions, so assigning starts from the host rather than from a
    declaration you then have to find a target for.

Run: pytest baseline/tests/
"""
import importlib
import os
import sqlite3
import sys
import threading
import time
import warnings

import pytest

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

SECRET = "s" * 32
PORT = 8883
OP = {"x-sysible-user": "alice", "x-sysible-role": "operator", "x-sysible-auth": SECRET}

FLEET = [
    {"id": "web-01", "label": "web-01", "environment": "production",
     "kind": "agent", "address": "10.0.0.11", "online": True},
    {"id": "web-02", "label": "web-02", "environment": "production",
     "kind": "agent", "address": "10.0.0.12", "online": True},
    {"id": "db-01", "label": "db-01", "environment": "production",
     "kind": "ssh", "address": "10.0.0.20", "online": None},
    {"id": "lab-01", "label": "lab-01", "environment": "lab",
     "kind": "agent", "address": "10.0.9.5", "online": False},
]
# web-01 drifted, web-02 was already right, db-01 failed, lab-01 never ran.
RUNS = (("web-01", 1, 1, 0, None), ("web-02", 1, 0, 0, None),
        ("db-01", 0, 0, 1, "could not finish"))


def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _playwright(), reason="playwright not installed")

FLEET_REACHABLE = {"ok": True, "raise": False}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import uvicorn
    tmp = tmp_path_factory.mktemp("fleet")
    os.environ.update(
        SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1", SYSIBLE_SSO_SHARED_SECRET=SECRET,
        SYSIBLE_BASELINE_AGENT_TOKEN="t" * 64, SYSIBLE_BASELINE_DATA=str(tmp))
    os.environ.pop("SYSIBLE_BASELINE_DB", None)
    for mod in ("backend.env", "backend.identity", "backend.store", "backend.app"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    import backend.app as m
    m = importlib.reload(m)
    m.store.init_db()

    def _targets(_identity):
        if FLEET_REACHABLE["raise"]:
            # Not the Controller being down — list_targets handles that itself
            # and answers with a note. This is /api/targets ITSELF failing.
            raise RuntimeError("boom")
        if not FLEET_REACHABLE["ok"]:
            return [], [], "could not reach the Controller (ConnectError)"
        return FLEET, ["lab", "production"], None

    m.controller.list_targets = _targets

    m.store.save_state("All Servers", "motd:\n  file.managed:\n  - name: /etc/motd\n",
                       "alice", source="yaml")
    m.store.save_state("web-servers", "nginx:\n  pkg.installed: []\n",
                       "alice", source="yaml")
    m.store.assign("env:production", "All Servers")
    m.store.assign("web-01", "web-servers")

    db = sqlite3.connect(str(tmp / "baseline.db"))
    now = int(time.time())
    for host, ok, changed, failed, err in RUNS:
        db.execute(
            "INSERT INTO runs(host_id,state_name,state_version,preview,started,finished,"
            "ok,changed,failed,error,states_json,actions_json) "
            "VALUES(?,?,?,0,?,?,?,?,?,?,'[]','[]')",
            (host, "All Servers", 1, now, now, ok, changed, failed, err))
    db.commit()
    db.close()

    cfg = uvicorn.Config(m.app, host="127.0.0.1", port=PORT, log_level="error")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "baseline did not come up"
    yield f"http://127.0.0.1:{PORT}"
    srv.should_exit = True
    t.join(timeout=10)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    chrome = os.environ.get("SYSIBLE_TEST_CHROMIUM")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
        except Exception as e:
            pytest.skip(f"no chromium to drive ({e})")
        yield b
        b.close()


@pytest.fixture
def pg(browser, server):
    FLEET_REACHABLE["ok"] = True
    FLEET_REACHABLE["raise"] = False
    ctx = browser.new_context(viewport={"width": 1280, "height": 1100},
                              extra_http_headers=OP)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("console", lambda m: errs.append(m.text)
            if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.goto(server + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#hosts table", timeout=5000)
    page.wait_for_timeout(400)
    yield page
    assert not errs, errs
    ctx.close()


def _row(page, host):
    return page.locator("#hosts tr", has=page.locator(f"text='{host}'")).first


def test_the_fleet_is_on_the_page_without_opening_anything(pg):
    """It used to exist on this screen only inside the Assign dialog."""
    text = pg.text_content("#hosts")
    for host in ("web-01", "web-02", "db-01", "lab-01"):
        assert host in text, f"{host} is not on the page: {text[:300]}"
    assert "4 from the Controller" in (pg.text_content("#nhosts") or "")


def test_hosts_are_grouped_by_environment(pg):
    heads = pg.evaluate(
        "() => [...document.querySelectorAll('#hosts th')]"
        ".map(t => t.textContent.trim()).filter(t => /^(lab|production)/i.test(t))")
    assert len(heads) == 2, heads


def test_a_host_shows_what_it_must_satisfy_including_what_it_inherits(pg):
    """Two sources — assigned to the host, and assigned to its environment.
    Before this the operator had to know that rule and apply it themselves
    against a different table."""
    row = _row(pg, "web-01").text_content()
    assert "web-servers" in row, row
    assert "All Servers" in row, row
    assert "via production" in row, \
        f"an inherited state is not marked as inherited: {row}"

    other = _row(pg, "web-02").text_content()
    assert "All Servers" in other and "via production" in other, other
    assert "web-servers" not in other, \
        f"web-02 shows a state assigned to a different host: {other}"


def test_a_host_with_nothing_assigned_is_not_managed_rather_than_compliant(pg):
    """The state most of a fleet is in on day one. Calling it "as declared"
    would report an entirely unconfigured fleet as green."""
    row = _row(pg, "lab-01").text_content()
    assert "Not managed" in row, row
    assert "no state assigned" in row, row
    assert "As declared" not in row, row


def test_an_unmanaged_host_is_not_offered_apply(pg):
    """There is nothing to apply, and a button that cannot do anything is a
    promise the screen cannot keep."""
    assert _row(pg, "lab-01").locator("[data-apply]").count() == 0
    assert _row(pg, "web-01").locator("[data-apply]").count() == 1


@pytest.mark.parametrize("host,want", [
    ("web-01", "Drifted"),
    ("web-02", "As declared"),
    ("db-01", "Failing"),
])
def test_each_host_says_what_the_last_run_made_of_it(pg, host, want):
    row = _row(pg, host).text_content()
    assert want in row, f"{host} should read {want!r}: {row}"


def test_assigning_starts_from_the_host(pg):
    """The dialog opens with that host already chosen — it was reached from
    the host's own row, so making you find it again in the list is work the
    screen already knows the answer to."""
    _row(pg, "db-01").locator("[data-assignhost]").click()
    pg.wait_for_selector("#a-pick")
    pg.wait_for_timeout(700)
    assert pg.input_value("#a-pick") == "db-01", pg.input_value("#a-pick")
    pg.click("#dlg-assign button[value=cancel]")


def test_a_whole_environment_can_be_assigned_by_clicking_it(pg):
    """env:<name> was a piece of syntax you had to know before it was a button."""
    pg.click("[data-assignenv=production]")
    pg.wait_for_selector("#a-pick")
    pg.wait_for_timeout(700)
    assert pg.input_value("#a-pick") == "env:production", pg.input_value("#a-pick")
    pg.click("#dlg-assign button[value=cancel]")


def test_the_empty_assignments_text_does_not_tell_you_to_type_an_id(pg):
    """It said "assign it to a host id" long after the picker replaced typing —
    an instruction that outlived the thing it described."""
    import re
    html = pg.content()
    body = re.search(r"<div class=card id=assign>(.*?)</div>", html, re.S)
    # With assignments present this is a table; the copy still must not be in
    # the page source anywhere.
    assert "assign it to a host id" not in html, \
        "the screen still tells people to type a host id"


def test_an_unreachable_controller_costs_the_host_list_not_the_console(browser, server):
    """Everything else on this page comes from Baseline's own store and has no
    reason to disappear because the Controller is down."""
    FLEET_REACHABLE["ok"] = False
    ctx = browser.new_context(viewport={"width": 1280, "height": 1000},
                              extra_http_headers=OP)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    try:
        page.goto(server + "/", wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        hosts = page.text_content("#hosts") or ""
        assert "could not reach the Controller" in hosts, hosts
        # ...and the rest of the console still works.
        assert "All Servers" in (page.text_content("#states") or "")
        assert "env:production" in (page.text_content("#assign") or "")
        assert not errs, errs
    finally:
        FLEET_REACHABLE["ok"] = True
        ctx.close()


def test_the_console_survives_the_host_endpoint_failing_outright(browser, server):
    """A different fault from the one above, and the one the fetch guards.

    An unreachable Controller is handled on the SERVER: /api/targets answers
    200 with an empty list and a note, so the console renders fine either way —
    which means the test above passes whether or not the browser guards the
    call, and it took re-breaking the guard to notice. This makes the endpoint
    itself fail, so the request rejects. Without the guard the whole
    Promise.all rejects with it and NOTHING on the page renders: the states,
    assignments, rules and runs all come from Baseline's own store and have no
    reason to vanish because one request did.
    """
    FLEET_REACHABLE["raise"] = True
    ctx = browser.new_context(viewport={"width": 1280, "height": 1000},
                              extra_http_headers=OP)
    page = ctx.new_page()
    try:
        page.goto(server + "/", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        assert "All Servers" in (page.text_content("#states") or ""), \
            "one failing request emptied the whole console"
        assert "env:production" in (page.text_content("#assign") or "")
        assert (page.text_content("#hosts") or "").strip(), \
            "the host panel says nothing at all about why it is empty"
    finally:
        FLEET_REACHABLE["raise"] = False
        ctx.close()
