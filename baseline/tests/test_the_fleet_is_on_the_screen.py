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

import console

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

    # RESTORED on teardown. `m.controller` is one module object for the whole
    # pytest process, so a plain assignment outlives this file and every later
    # one sees the stub — which is exactly what happened when a second console
    # test file landed alphabetically before test_state_builder.py and left it
    # looking at a fleet of thirteen hosts it had never heard of.
    _real_targets = m.controller.list_targets
    m.controller.list_targets = _targets

    m.store.save_state("All Servers", "motd:\n  file.managed:\n  - name: /etc/motd\n",
                       "alice", source="yaml")
    m.store.save_state("web-servers", "nginx:\n  pkg.installed: []\n",
                       "alice", source="yaml")
    # Declared and assigned to nothing, which is a real state to be in and the
    # one where every apply control must be absent.
    m.store.save_state("idle-state", "htop:\n  pkg.installed: []\n",
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
    m.controller.list_targets = _real_targets
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
    console.ready(page, "hosts")
    page.wait_for_timeout(300)
    yield page
    assert not errs, errs
    ctx.close()


def _row(page, host):
    """One host's row in the Hosts section."""
    console.goto(page, "hosts")
    return page.locator(f".grid .r[data-host='{host}']")


def _pick(page, host):
    """Open one host. It used to fill two columns beside the list; it opens the
    host's own page now, which is where everything about it already was."""
    console.open_host(page, host)


def _names(page, nav="hosts"):
    return console.names(page, nav)


def test_the_fleet_is_on_the_page_without_opening_anything(pg):
    """It used to exist on this screen only inside the Assign dialog."""
    console.goto(pg, "hosts")
    text = pg.text_content("#view")
    for h in FLEET:
        assert h["id"] in text, f"{h['id']} is not on the page: {text[:300]}"
    assert "7 hosts" in (pg.text_content(".vh .n") or "")


def test_hosts_are_grouped_by_environment(pg):
    console.goto(pg, "hosts")
    heads = pg.evaluate(
        "() => [...document.querySelectorAll('.grid .gh')]"
        ".map(t => t.firstChild.textContent.trim())")
    assert sorted(h.lower() for h in heads) == ["lab", "production", "staging"], heads


def test_a_host_shows_what_it_must_satisfy_including_what_it_inherits(pg):
    """Two sources — assigned to the host, and assigned to its environment.
    Before this the operator had to know that rule and apply it themselves
    against a different table. It is the middle column now: pick a host and
    what it must satisfy is beside it, not further down the page."""
    _pick(pg, "web-01")
    mid = console.detail(pg)
    assert "web-servers" in mid, mid
    assert "All Servers" in mid, mid
    assert "via production" in mid, f"an inherited state is not marked as inherited: {mid}"

    _pick(pg, "web-02")
    other = console.detail(pg)
    assert "All Servers" in other and "via production" in other, other
    assert "web-servers" not in other, \
        f"web-02 shows a state assigned to a different host: {other}"


def test_a_host_with_nothing_assigned_is_not_managed_rather_than_compliant(pg):
    """The state most of a fleet is in on day one. Calling it "as declared"
    would report an entirely unconfigured fleet as green."""
    _pick(pg, "stg-02")
    detail = console.detail(pg)
    assert "not managed" in detail.lower(), detail
    assert "as declared" not in detail.lower(), detail
    assert "simply not" in detail.lower(), \
        f"it does not say what being unmanaged MEANS: {detail[:300]}"


def test_an_unmanaged_host_still_offers_the_one_thing_it_can_do(pg):
    """There is nothing to apply, and a button that cannot do anything is a
    promise the screen cannot keep — but assigning is exactly what it needs."""
    _pick(pg, "stg-02")
    assert pg.locator("#detail [data-apply]").count() == 0
    assert pg.locator("#detail [data-assignhost]").count() == 1
    _pick(pg, "web-01")
    assert pg.locator("#detail [data-apply]").count() == 1


@pytest.mark.parametrize("host,want", [
    ("web-01", "drifted"),
    ("web-02", "as declared"),
    ("db-01", "failing"),
])
def test_each_host_says_what_the_last_run_made_of_it(pg, host, want):
    _pick(pg, host)
    detail = console.detail(pg)
    assert want in detail.lower(), f"{host} should read {want!r}: {detail[:300]}"


def test_the_list_leads_with_what_needs_attention(pg):
    """Alphabetical across a fleet buries the one failing host wherever its name
    happens to fall. db-01 failed; it is the first row of its environment."""
    order = _names(pg)
    prod = [h for h in order if h in ("web-01", "web-02", "db-01", "aaa-01")]
    assert prod[0] == "db-01", f"the failing host is not first: {prod}"
    assert prod[1] == "web-01", f"the drifted host is not second: {prod}"
    # aaa-01 is fine, and sorts first in the alphabet. It goes last.
    assert prod.index("aaa-01") > prod.index("web-01"), \
        f"the list is in alphabetical order, not attention order: {prod}"


def test_the_console_opens_on_what_needs_attention(pg):
    """Empty columns are not a starting point.

    It used to answer this by SELECTING the worst host, which told you about
    one host and nothing about the fleet. The console opens on the Overview
    now, which leads with every host that has something wrong with it, worst
    first — so the same question is answered for all of them at once."""
    console.goto(pg, "overview")
    first = pg.evaluate(
        "() => {const e = document.querySelector('.grid .r[data-host]');"
        " return e ? e.dataset.host : null;}")
    assert first == "db-01", f"it leads with {first!r} rather than the failing host"
    assert "failing" in (pg.text_content("#view") or ""), \
        "the Overview does not say what is wrong"


def test_a_host_is_one_line(pg):
    """The address was on a line of its own under the name, which is a second
    screenful per fifty hosts for a string that fits beside it."""
    console.goto(pg, "hosts")
    tall = pg.evaluate(
        "() => [...document.querySelectorAll('.grid .r')]"
        ".map(r => r.getBoundingClientRect().height).filter(h => h > 34)")
    assert not tall, f"{len(tall)} host rows are taller than one line: {tall}"


def test_the_fleet_does_not_scroll_the_page(pg):
    """The complaint behind the whole shape: the console was five full-width
    sections stacked, so reading it meant scrolling and holding the earlier
    ones in your head. The columns scroll; the page does not."""
    over = pg.evaluate(
        "() => document.documentElement.scrollHeight - window.innerHeight")
    assert over <= 1, f"the page scrolls by {over}px"


# ---- pivoting ---------------------------------------------------------------
def test_a_state_can_be_reached_from_the_host_that_must_satisfy_it(pg):
    _pick(pg, "web-01")
    pg.click("#detail .ln[data-pickstate='web-servers']")
    pg.wait_for_timeout(450)
    assert "web-servers" in (pg.text_content("#detail h2") or "")
    assert "nginx" in (pg.text_content("#detail pre.sls") or ""), \
        "the state's detail does not show what it declares"


def test_the_other_end_lists_states_and_their_hosts(pg):
    """The relationship runs both ways, and the console used to offer only one
    direction."""
    names = _names(pg, "states")
    assert "All Servers" in names and "web-servers" in names, names
    console.open_state(pg, "web-servers")
    assert "web-01" in console.detail(pg), \
        "the hosts a state lands on are not listed with it"


def test_a_host_can_be_reached_from_the_state_it_satisfies(pg):
    console.open_state(pg, "All Servers")
    pg.click("#detail .ln[data-pickhost='db-01']")
    pg.wait_for_timeout(450)
    detail = console.detail(pg) or ""
    assert "db-01" in detail, detail[:200]
    assert "All Servers" in (pg.text_content("#detail h2") or ""), \
        "following a host from a state should keep the state in view"


def test_a_rail_section_opens_that_section_not_where_you_last_were(pg):
    """THE CONTRACT CHANGED, deliberately.

    With a two-way By host / By state switch, each end kept its place: going to
    states and back put you on the host you had picked. With a rail, pressing
    "Hosts" means "show me the hosts" — landing in one host's page because that
    is where you were ten minutes ago is the console deciding where you are
    going. The way back out of a detail is the crumb at the top of it, which
    is right there and says where it goes.
    """
    _pick(pg, "web-02")
    assert "web-02" in console.detail(pg)
    console.goto(pg, "states")
    console.goto(pg, "hosts")
    assert pg.locator(".grid .r[data-host]").count() == len(FLEET), \
        "pressing Hosts did not show the hosts"
    assert pg.locator("#detail").count() == 0, \
        "pressing Hosts left a host's page open"


def test_the_crumb_goes_back_to_the_list(pg):
    _pick(pg, "web-02")
    pg.click(".crumb")
    pg.wait_for_timeout(350)
    assert pg.locator(".grid .r[data-host]").count() == len(FLEET), \
        "the way back out of a host does not lead to the hosts"


# ---- assigning --------------------------------------------------------------
def test_assigning_starts_from_the_host(pg):
    """The dialog opens with that host already chosen — it was reached from
    the host's own row, so making you find it again in the list is work the
    screen already knows the answer to."""
    _pick(pg, "db-01")
    pg.click("#detail [data-assignhost]")
    pg.wait_for_selector("#a-pick")
    pg.wait_for_timeout(700)
    assert pg.input_value("#a-pick") == "db-01", pg.input_value("#a-pick")
    pg.click("#dlg-assign button[value=cancel]")


def test_a_whole_environment_can_be_assigned_by_clicking_it(pg):
    """env:<name> was a piece of syntax you had to know before it was a button."""
    console.goto(pg, "hosts")
    pg.click(".grid [data-assignenv=production]")
    pg.wait_for_selector("#a-pick")
    pg.wait_for_timeout(700)
    assert pg.input_value("#a-pick") == "env:production", pg.input_value("#a-pick")
    pg.click("#dlg-assign button[value=cancel]")


def test_the_screen_does_not_tell_you_to_type_a_host_id(pg):
    """It said "assign it to a host id" long after the picker replaced typing —
    an instruction that outlived the thing it described."""
    assert "assign it to a host id" not in pg.content(), \
        "the screen still tells people to type a host id"


# ---- when the Controller is not there ---------------------------------------
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
        page.wait_for_timeout(1500)
        console.goto(page, "hosts")
        assert "could not reach the Controller" in (page.text_content("#view") or "")
        # ...and the states are still browsable, which is the half of this
        # console that never needed the Controller at all.
        console.goto(page, "states")
        assert "All Servers" in (page.text_content("#view") or "")
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
    Promise.all rejects with it and NOTHING renders: the states, assignments,
    rules and runs all come from Baseline's own store and have no reason to
    vanish because one request did.
    """
    FLEET_REACHABLE["raise"] = True
    ctx = browser.new_context(viewport={"width": 1280, "height": 1000},
                              extra_http_headers=OP)
    page = ctx.new_page()
    try:
        page.goto(server + "/", wait_until="domcontentloaded")
        page.wait_for_timeout(1800)
        console.goto(page, "states")
        assert "All Servers" in (page.text_content("#view") or ""), \
            "one failing request emptied the whole console"
    finally:
        FLEET_REACHABLE["raise"] = False
        ctx.close()


# ---- finding a host in a fleet that does not fit on a screen ---------------
def _filter(page, text):
    """Type into the open section's filter.

    It heads the section rather than being revealed once a fleet gets big: in a
    list that scrolls there is nowhere else to look for it.
    """
    page.fill("#filter", text)
    page.wait_for_timeout(300)


def _shown(page):
    """The rows the filter left, WITHOUT navigating — going to a section would
    rebuild the view and clear what was typed."""
    return page.eval_on_selector_all(
        ".grid .r .c:first-child", "els => els.map(e => e.textContent.trim())")


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
    _filter(pg, "web-servers")
    assert sorted(_shown(pg)) == ["stg-01", "web-01"], _shown(pg)


def test_a_filter_that_matches_nothing_says_so(pg):
    """An empty column is indistinguishable from a fleet that failed to load."""
    _filter(pg, "zzzz")
    assert "Nothing matches" in (pg.text_content("#view") or "")


def test_clearing_the_filter_brings_everything_back(pg):
    _filter(pg, "web")
    _filter(pg, "")
    assert len(_shown(pg)) == len(FLEET), _shown(pg)


def test_the_filter_searches_states_in_the_states_section_too(pg):
    console.goto(pg, "states")
    _filter(pg, "web")
    assert _shown(pg) == ["web-servers"], _shown(pg)


def test_a_state_is_judged_by_ITSELF_on_its_hosts(pg):
    """Not by how its hosts are doing overall.

    web-01 is drifted — because of "All Servers", which is the only state
    anything has ever been applied for. "web-servers" is also on web-01 and has
    never been applied anywhere, so it is "never applied". Rolling the HOST's
    status up to the state instead put a red dot beside a state that was
    nowhere near the problem, and the fix went in without a test until a
    deliberate re-break sailed through.
    """
    console.goto(pg, "states")
    said = pg.evaluate(
        """() => {const o = {};
             document.querySelectorAll('.grid .r[data-state]').forEach((e) => {
               const c = e.querySelector('.c:last-child');
               o[e.dataset.state] = c ? c.textContent.trim() : null;});
             return o;}""")
    assert said.get("web-servers") == "never applied", (
        f"web-servers has never been applied anywhere: {said}")
    assert said.get("All Servers") == "failing", (
        f"All Servers has a failing host: {said}")


def test_a_state_on_one_host_reports_THAT_host(pg):
    """A host's page lists the states it must satisfy, so what it says beside
    each one means "this state, ON THIS HOST". Showing the state's worst host
    anywhere puts a failure beside a state that is fine here."""
    _pick(pg, "web-01")
    said = pg.evaluate(
        """() => {const o = {};
             document.querySelectorAll('#detail .ln[data-pickstate]').forEach((e) => {
               const t = e.querySelector('.t');
               o[e.dataset.pickstate] = t ? t.textContent.trim() : null;});
             return o;}""")
    # db-01 is the failing host, and it is not this one.
    assert said.get("All Servers") == "drifted", \
        f"web-01's own run of All Servers changed things: {said}"
    assert said.get("web-servers") == "never applied", \
        f"web-servers has never run on web-01: {dots}"


# ---- applying ---------------------------------------------------------------
def test_a_state_can_be_applied_to_everything_it_is_assigned_to(pg):
    """Reported, over a state assigned to sixteen hosts whose only action was
    "Apply to arch-01": "It should be the state and then assign it to the hosts
    then apply and be able to do it to all."

    Sixteen hosts meant visiting sixteen hosts. Declare, assign, apply — the
    third step has to be able to mean the same set as the second.
    """
    posted = []
    pg.on("request", lambda r: posted.append(r.url)
          if r.method == "POST" and "/apply" in r.url else None)
    pg.on("dialog", lambda d: d.accept())
    console.open_state(pg, "All Servers")

    btn = pg.locator("#detail [data-applyall]")
    assert btn.count() == 1, "there is no way to apply a state to all of its hosts"
    label = btn.inner_text()
    assert "all" in label.lower() and "host" in label.lower(), label

    btn.click()
    pg.wait_for_timeout(2500)
    assert posted, "pressing it posted nothing"

    # ONE REQUEST PER HOST. This used to assert the opposite — that
    # env:production went as a single call — on the reasoning that "the
    # Controller expands an environment into every host in it, so fanning out
    # here as well would run each host twice."
    #
    # Both halves of that were wrong. Fanning out here REPLACES the env call
    # rather than adding to it, so nothing runs twice. And the single call it
    # was defending is the bug: _run_baseline_cycle expands the environment and
    # runs every host's salt-call SYNCHRONOUSLY inside that one request, at up
    # to 60s each, while this service gives up on it after 20s — so the console
    # reported a failure, or nothing, while the applies carried on unobserved.
    # Reported as "the apply to all 16 hosts doesn't do anything".
    assert not any("env%3Aproduction" in u or "env:production" in u for u in posted), (
        "still sending the environment as one request", posted)
    want = {h["id"] for h in FLEET if h["environment"] == "production"}
    got = {u.rsplit("/hosts/", 1)[1].split("/")[0] for u in posted}
    assert got == want, (got, want)


def test_the_count_on_the_button_is_the_number_of_hosts_it_reaches(pg):
    """A state assigned to one environment is assigned to one TARGET and
    reaches every host in it. Counting targets would have said "all 1 host"
    over a fleet of sixteen."""
    console.open_state(pg, "All Servers")
    label = pg.locator("#detail [data-applyall]").inner_text()
    hosts = len([h for h in FLEET if h["environment"] == "production"])
    assert str(hosts) in label, f"{label!r} does not name the {hosts} hosts it reaches"


def test_applying_to_everything_asks_first_and_says_what_it_will_do(pg):
    """It is a fleet-wide change, and it converges each host to its WHOLE
    desired state — not only the state whose page the button is on."""
    seen = []
    pg.once("dialog", lambda d: (seen.append(d.message), d.dismiss()))
    console.open_state(pg, "All Servers")
    pg.click("#detail [data-applyall]")
    pg.wait_for_timeout(600)
    assert seen, "a fleet-wide apply went ahead without asking"
    assert "host" in seen[0], seen
    assert "other than" in seen[0], \
        f"it does not say it applies more than this one state: {seen[0]!r}"


def test_a_state_assigned_to_nothing_offers_no_apply_at_all(pg):
    """A button that cannot do anything is a promise the screen cannot keep."""
    console.open_state(pg, "idle-state")
    assert pg.locator("#detail [data-applyall]").count() == 0
    assert pg.locator("#detail [data-apply]").count() == 0
    assert "does not apply it" in (console.detail(pg) or ""), \
        "it does not say why there is nothing to apply"
