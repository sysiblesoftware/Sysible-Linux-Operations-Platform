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
    # Sorts FIRST alphabetically and LAST by what needs attention. Without a
    # host like this the two orders coincide in production — db-01, web-01,
    # web-02 is both the alphabet and the ranking — so the ordering test passed
    # just as happily with the ranking removed.
    {"id": "aaa-01", "label": "aaa-01", "environment": "production",
     "kind": "agent", "address": "10.0.0.40", "online": True},
    # staging is the MIXED arrangement: stg-01 is assigned a state directly and
    # stg-02 has nothing, so the per-host columns have work to do. production is
    # managed through its environment and lab has nothing at all, so one page
    # carries all three shapes.
    {"id": "stg-01", "label": "stg-01", "environment": "staging",
     "kind": "agent", "address": "10.0.5.1", "online": True},
    {"id": "stg-02", "label": "stg-02", "environment": "staging",
     "kind": "agent", "address": "10.0.5.2", "online": True},
    {"id": "lab-01", "label": "lab-01", "environment": "lab",
     "kind": "agent", "address": "10.0.9.5", "online": False},
]
# web-01 drifted, web-02 was already right, db-01 failed, lab-01 never ran.
RUNS = (("web-01", 1, 1, 0, None), ("web-02", 1, 0, 0, None),
        ("aaa-01", 1, 0, 0, None),
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
        return FLEET, ["lab", "production", "staging"], None

    m.controller.list_targets = _targets

    m.store.save_state("All Servers", "motd:\n  file.managed:\n  - name: /etc/motd\n",
                       "alice", source="yaml")
    m.store.save_state("web-servers", "nginx:\n  pkg.installed: []\n",
                       "alice", source="yaml")
    m.store.assign("env:production", "All Servers")
    m.store.assign("web-01", "web-servers")
    m.store.assign("stg-01", "web-servers")

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
    assert "7 from the Controller" in (pg.text_content("#nhosts") or "")


def test_hosts_are_grouped_by_environment(pg):
    heads = pg.evaluate(
        "() => [...document.querySelectorAll('#hosts .envhead b')]"
        ".map(t => t.textContent.trim())")
    assert sorted(h.lower() for h in heads) == ["lab", "production", "staging"], heads


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
    row = _row(pg, "stg-02").text_content()
    assert "not managed" in row.lower(), row
    assert "as declared" not in row.lower(), row
    # The explanation is the same sentence on every row of a kind, so it is on
    # the control rather than printed down the page — but it still has to be
    # reachable, or "not managed" is a phrase with nowhere to look it up.
    title = _row(pg, "stg-02").locator(".hstate [title]").first.get_attribute("title")
    assert "no state is assigned" in (title or ""), title


def test_an_environment_with_nothing_managed_says_so_once(pg):
    """The reported screen: sixteen hosts, every one of them unmanaged, and so
    every row carrying "nothing" in one column and "Not managed \u2014 no state
    assigned to it or its environment" in the next. Thirty-two restatements of
    one fact, in 840px of screen.

    When no host in an environment is managed there is nothing to say per row,
    so the group says it and the rows go back to being a list of hosts.
    """
    head = pg.text_content("#hosts .envhead")
    envs = pg.evaluate(
        "() => [...document.querySelectorAll('#hosts .envhead')]"
        ".map(h => h.textContent.replace(/\\s+/g,' ').trim())")
    lab = [e for e in envs if e.lower().startswith("lab")]
    assert lab and "none managed yet" in lab[0], (lab, head)
    # And the row carries no per-host state column at all.
    assert _row(pg, "lab-01").locator(".hstate").count() == 0, \
        "the group already said it; the row says it again"
    assert _row(pg, "lab-01").locator("[data-assignhost]").count() == 1, \
        "an unmanaged host must still be assignable from its own row"


def test_a_mixed_environment_keeps_the_per_host_columns(pg):
    """The collapse is only for the case where every row would say the same
    thing. One managed host in the group and the columns have work to do again."""
    assert _row(pg, "stg-02").locator(".hstate").count() == 1
    assert _row(pg, "stg-01").locator(".hstate").count() == 1


def test_the_list_leads_with_what_needs_attention(pg):
    """Alphabetical across a fleet buries the one failing host wherever its name
    happens to fall. db-01 failed; it is the first row of its environment."""
    order = pg.evaluate(
        "() => [...document.querySelectorAll('#hosts .fleet .hname .mono')]"
        ".map(e => e.textContent.trim())")
    prod = [h for h in order if h in ("web-01", "web-02", "db-01", "aaa-01")]
    assert prod[0] == "db-01", f"the failing host is not first: {prod}"
    assert prod[1] == "web-01", f"the drifted host is not second: {prod}"
    # aaa-01 is fine, and sorts first in the alphabet. It goes last.
    assert prod[-1] in ("aaa-01", "web-02"), prod
    assert prod.index("aaa-01") > prod.index("web-01"), \
        f"the list is in alphabetical order, not attention order: {prod}"
    stg = [h for h in order if h.startswith("stg-")]
    assert stg == ["stg-01", "stg-02"], \
        f"an unmanaged host is not last in its environment: {stg}"


def test_a_host_is_one_line(pg):
    """The address was on a line of its own under the name, which is a second
    screenful per fifty hosts for a string that fits beside it."""
    tall = pg.evaluate(
        "() => [...document.querySelectorAll('#hosts .fleet tr')]"
        ".map(r => r.getBoundingClientRect().height).filter(h => h > 34)")
    assert not tall, f"{len(tall)} host rows are taller than one line: {tall}"


def test_an_unmanaged_host_is_not_offered_apply(pg):
    """There is nothing to apply, and a button that cannot do anything is a
    promise the screen cannot keep."""
    assert _row(pg, "lab-01").locator("[data-apply]").count() == 0
    assert _row(pg, "web-01").locator("[data-apply]").count() == 1


@pytest.mark.parametrize("host,want", [
    ("web-01", "drifted"),
    ("web-02", "as declared"),
    ("db-01", "failing"),
])
def test_each_host_says_what_the_last_run_made_of_it(pg, host, want):
    row = _row(pg, host).text_content()
    assert want in row.lower(), f"{host} should read {want!r}: {row}"


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


# ---- finding a host in a fleet that does not fit on a screen ---------------
def test_a_small_fleet_is_not_given_a_search_box(pg):
    """Six hosts fit. A filter over a list you can already see is a control to
    read past, not a tool."""
    assert pg.evaluate(
        "() => document.querySelector('.hfilter').hidden") is True


def _filter(page, text):
    """Type into the filter.

    Set-and-dispatch rather than page.fill, because the control is hidden for a
    fleet this size (see the test above) and fill waits for visibility. What is
    under test here is the filtering, not the threshold.
    """
    page.evaluate(
        """(t) => { const b = document.getElementById('hostfilter');
                    b.value = t; b.dispatchEvent(new Event('input')); }""", text)


def _shown(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('#hosts .fleet .hname .mono')]"
        ".map(e => e.textContent.trim())")


def test_filtering_narrows_the_fleet_without_asking_the_server(pg):
    """It is a view of data the page already has. A request per keystroke would
    be a request per keystroke to the Controller, which is the thing this whole
    console is otherwise careful about."""
    asked = []
    pg.on("request", lambda r: asked.append(r.url) if "/api/targets" in r.url else None)
    _filter(pg, "web-0")
    assert sorted(_shown(pg)) == ["web-01", "web-02"], _shown(pg)
    assert not asked, f"filtering went to the server: {asked}"
    # The bare word "web" legitimately pulls in a third: stg-01 must satisfy
    # web-servers. Searching the states as well as the names is the point —
    # "which hosts have web-servers on them" is a fleet question.
    _filter(pg, "web")
    assert sorted(_shown(pg)) == ["stg-01", "web-01", "web-02"], _shown(pg)


def test_a_host_can_be_found_by_its_address(pg):
    """An operator holding an IP and not a name is the normal way round."""
    _filter(pg, "10.0.9.5")
    assert _shown(pg) == ["lab-01"], _shown(pg)


def test_a_host_can_be_found_by_the_state_it_must_satisfy(pg):
    """"Which hosts have web-servers on them?" is a fleet question, and the
    answer was only readable by joining two tables by eye."""
    _filter(pg, "web-servers")
    assert sorted(_shown(pg)) == ["stg-01", "web-01"], _shown(pg)


def test_a_filter_that_matches_nothing_says_so(pg):
    """An empty card is indistinguishable from a fleet that failed to load."""
    _filter(pg, "zzzz")
    assert "No host matches" in pg.text_content("#hosts")


def test_clearing_the_filter_brings_everything_back(pg):
    _filter(pg, "web")
    _filter(pg, "")
    assert len(_shown(pg)) == len(FLEET), _shown(pg)


# ---- the first thing on the page -------------------------------------------
def test_apply_is_only_the_loud_button_where_it_would_change_something(pg):
    """A column of identical green "Apply now" buttons says nothing about which
    row to press, and the loudest thing on the screen ends up being the hosts
    that need nothing."""
    def primary(host):
        return _row(pg, host).locator("[data-apply].primary").count()
    assert primary("db-01") == 1, "the failing host does not offer the action"
    assert primary("web-01") == 1, "the drifted host does not offer the action"
    assert primary("web-02") == 0, "a host that is already as declared shouts Apply"
    # Still available, just not shouting.
    assert _row(pg, "web-02").locator("[data-apply]").count() == 1
