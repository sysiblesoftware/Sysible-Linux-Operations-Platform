"""The shell: a left rail and one view, and no screen that says a thing twice.

REPORTED, with a screenshot: "This whole layout doesn't make much sense and
isn't as intuitive as SLEP or the other apps."

It was three resizable columns — browse, "what the selection is made of",
detail — and the middle one was the mistake. It listed the selected host's
states and its recent runs; the detail pane immediately to its right listed the
same two things again, in more detail. It was not a drill-down either: picking
something in the middle column did not narrow the pane, it REPLACED the pane's
subject. So one fact was on the screen twice, the detail had a third of the
width it needed, and the bottom two thirds of the screen were empty.

SLEP and the Sysible Controller are both a left rail of named sections and one
full-width view — SLEP's App.jsx says so about its own rail: "Left-rail
navigation — same shape as the Sysible Controller console". Baseline was the
only console in the family not built that way, which is what "not as intuitive
as the other apps" was describing.

These tests hold the new contract: five sections, one at a time, each the whole
width; a detail opens inside its section with a way back; and nothing appears
in two places at once.
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
PORT = 8893
OP = {"x-sysible-user": "alice", "x-sysible-role": "operator", "x-sysible-auth": SECRET}

FLEET = [{"id": f"web-{i:02d}", "label": f"web-{i:02d}", "environment": "production",
          "kind": "agent", "address": f"10.0.0.{i}", "online": True}
         for i in range(1, 7)]
FLEET.append({"id": "db-01", "label": "db-01", "environment": "staging",
              "kind": "ssh", "address": "10.0.5.1", "online": True})

SECTIONS = ("overview", "hosts", "states", "runs", "rules")


def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _playwright(), reason="playwright not installed")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import uvicorn
    tmp = tmp_path_factory.mktemp("shell")
    os.environ.update(
        SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1", SYSIBLE_SSO_SHARED_SECRET=SECRET,
        SYSIBLE_BASELINE_GATEWAY_SECRET=SECRET,
        SYSIBLE_BASELINE_AGENT_TOKEN="t" * 64, SYSIBLE_BASELINE_DATA=str(tmp))
    os.environ.pop("SYSIBLE_BASELINE_DB", None)
    for mod in ("backend.env", "backend.identity", "backend.store", "backend.app"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    import backend.app as m
    m = importlib.reload(m)
    m.store.init_db()
    _real = {k: getattr(m.controller, k) for k in ("list_targets",)}
    m.controller.list_targets = lambda _i: (FLEET, ["production", "staging"], None)

    m.store.save_state("web-stack", "nginx:\n  pkg.installed: []\n", "alice", source="yaml")
    m.store.save_state("hardening", "sshd:\n  service.running: []\n", "alice", source="yaml")
    m.store.assign("env:production", "web-stack")
    m.store.assign("db-01", "hardening")

    db = sqlite3.connect(str(tmp / "baseline.db"))
    now = int(time.time())
    for host, ok, ch, fa, err in (("web-01", 1, 2, 0, None), ("web-02", 1, 0, 0, None),
                                  ("web-03", 0, 0, 1, None)):
        db.execute(
            "INSERT INTO runs(host_id,state_name,state_version,preview,started,finished,"
            "ok,changed,failed,error,states_json,actions_json) "
            "VALUES(?,?,1,0,?,?,?,?,?,?,'[]','[]')",
            (host, "web-stack", now, now, ok, ch, fa, err))
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
    for k, v in _real.items():
        setattr(m.controller, k, v)
    srv.should_exit = True
    t.join(timeout=10)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    chrome = os.environ.get("SYSIBLE_TEST_CHROMIUM")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}),
                                  args=["--no-sandbox"])
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no chromium to drive ({e})")
        yield b
        b.close()


@pytest.fixture
def pg(browser, server):
    ctx = browser.new_context(viewport={"width": 1600, "height": 950},
                              extra_http_headers=OP)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(server + "/", wait_until="domcontentloaded")
    page.wait_for_selector(".rail-item", timeout=8000)
    page.wait_for_timeout(500)
    yield page
    assert not errs, errs
    ctx.close()


# ---------------------------------------------------------------------------
# the shape
# ---------------------------------------------------------------------------
def test_the_rail_offers_every_section(pg):
    got = pg.eval_on_selector_all(".rail-item", "els => els.map(e => e.dataset.nav)")
    assert got == list(SECTIONS), got


@pytest.mark.parametrize("nav", SECTIONS)
def test_each_section_opens_and_names_itself(pg, nav):
    console.goto(pg, nav)
    assert pg.locator(f".rail-item[data-nav={nav}].on").count() == 1, \
        f"the rail does not mark {nav} as the open section"
    heading = (pg.text_content(".vh h1") or "").strip().lower()
    assert heading.startswith(nav.rstrip("s")[:4]), (nav, heading)


@pytest.mark.parametrize("nav", SECTIONS)
def test_only_one_section_is_on_the_screen(pg, nav):
    """The whole complaint, as an assertion. Three columns meant three
    subjects at once, two of them the same subject."""
    console.goto(pg, nav)
    assert pg.locator(".vh h1").count() == 1, \
        f"{nav} is showing more than one section at a time"


def test_the_view_takes_the_width_the_rail_does_not(pg):
    """The detail used to be the third of three columns. It is the page now."""
    console.goto(pg, "hosts")
    box = pg.evaluate("""() => {
      const r = document.querySelector('.rail').getBoundingClientRect();
      const v = document.querySelector('.view').getBoundingClientRect();
      return {rail: r.width, view: v.width, win: innerWidth};
    }""")
    assert box["rail"] < 260, box
    assert box["view"] > box["win"] * 0.75, \
        f"the view is not getting the width: {box}"


def test_nothing_scrolls_the_page_itself(pg):
    """Each section scrolls inside the view. A page that scrolls as well puts
    the rail off the top of the screen."""
    for nav in SECTIONS:
        console.goto(pg, nav)
        over = pg.evaluate(
            "() => document.documentElement.scrollHeight - window.innerHeight")
        assert over <= 1, f"{nav} scrolls the page by {over}px"


# ---------------------------------------------------------------------------
# the thing that was wrong: one fact, two places
# ---------------------------------------------------------------------------
def test_no_section_of_a_hosts_page_appears_twice(pg):
    """THE COMPLAINT, as an assertion.

    The middle column was headed "Must satisfy <host>" and listed that host's
    states and its recent runs. The detail pane beside it had sections headed
    "Must satisfy" and "Recent runs" listing the same two things. Two renderings
    of one fact, side by side, with the narrower one ellipsising.

    Counting the NAME would be wrong — "web-stack" appears under Must satisfy
    (this host has to satisfy it) and under Recent runs (the last run of it
    changed two things), which are two different facts that happen to share a
    word. It is the SECTIONS that must not repeat.
    """
    console.open_host(pg, "web-01")
    heads = pg.eval_on_selector_all(
        "#view h4", "els => els.map(e => e.textContent.trim().toLowerCase())")
    assert heads, "the host's page has no sections at all"
    assert len(heads) == len(set(heads)), f"a section is on the screen twice: {heads}"
    for want in ("state", "must satisfy", "recent runs"):
        assert heads.count(want) == 1, f"{want!r} appears {heads.count(want)} times: {heads}"


# ---------------------------------------------------------------------------
# moving around
# ---------------------------------------------------------------------------
def test_a_detail_opens_inside_its_own_section(pg):
    console.open_host(pg, "db-01")
    assert pg.locator(".rail-item[data-nav=hosts].on").count() == 1, \
        "opening a host left the rail pointing somewhere else"
    assert "db-01" in console.detail(pg)


def test_the_way_back_is_on_the_screen_and_says_where_it_goes(pg):
    console.open_host(pg, "db-01")
    crumb = pg.locator(".crumb")
    assert crumb.count() == 1, "there is no way back out of a host"
    assert "Hosts" in crumb.inner_text(), crumb.inner_text()
    crumb.click()
    pg.wait_for_timeout(350)
    assert pg.locator(".grid .r[data-host]").count() == len(FLEET)


def test_an_overview_chip_leads_into_the_hosts_it_counted(pg):
    """A number you cannot act on is decoration."""
    console.goto(pg, "overview")
    chip = pg.locator(".chip[data-verdict='failing']")
    assert chip.count() == 1, "the overview does not count the failing hosts"
    chip.click()
    pg.wait_for_timeout(400)
    assert pg.locator(".rail-item[data-nav=hosts].on").count() == 1
    # Read WITHOUT navigating: console.names() would go to Hosts again and
    # rebuild the view, which clears the filter the chip just set.
    names = pg.eval_on_selector_all(".grid .r .c:first-child",
                                    "els => els.map(e => e.textContent.trim())")
    assert names == ["web-03"], f"the chip did not narrow the list to its own hosts: {names}"


def test_the_rail_carries_the_worst_thing_in_each_section(pg):
    """"7" beside Hosts and "7" in red are different messages, and the second
    is the one worth walking past."""
    cls = pg.evaluate(
        "() => document.getElementById('n-hosts').className")
    assert "err" in cls, f"a failing fleet is not marked in the rail: {cls}"
    assert (pg.text_content("#n-hosts") or "").strip() == str(len(FLEET))


# ---------------------------------------------------------------------------
# the filter
# ---------------------------------------------------------------------------
def test_typing_in_the_filter_survives_the_background_poll(pg):
    """The view is rebuilt from STATE every twenty seconds, filter box and all.
    A poll landing mid-word used to take the cursor out of it and the next
    keystroke went nowhere."""
    console.goto(pg, "hosts")
    pg.click("#filter")
    pg.type("#filter", "web-0", delay=20)
    pg.evaluate("() => renderAll()")        # exactly what the poll does
    pg.wait_for_timeout(200)
    assert pg.evaluate("() => document.activeElement.id") == "filter", \
        "a refresh took the cursor out of the filter"
    assert pg.input_value("#filter") == "web-0"
    pg.type("#filter", "1", delay=20)
    assert pg.input_value("#filter") == "web-01", pg.input_value("#filter")
