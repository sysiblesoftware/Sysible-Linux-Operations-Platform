"""The console on a fleet where nothing has been set up yet.

Reported, over a screen of sixteen hosts all reading the same thing: "This
isn't a good ui."

It opened with three large numbers — 0 as declared, 0 drifted, 0 failing — a
scoreboard for a game that had not started, in the biggest type on the page and
in the place the eye lands first. Below that, every host carried "nothing" in
one column and "Not managed — no state assigned to it or its environment" in
the next: two columns and a sentence, repeated once per host, all saying the one
thing the three zeros had already said.

Day one needs the way in, not a tally of nothing.

Run: pytest baseline/tests/test_day_one_shows_the_way_in.py
"""
import json
import os
import sys
import threading
import time
import warnings

import pytest

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

SECRET = "s" * 32
PORT = 8886
OP = {"x-sysible-user": "alice", "x-sysible-role": "operator", "x-sysible-auth": SECRET}

# The reported fleet: enrolled, and entirely unmanaged.
FLEET = [{"id": f"host-{i:02d}", "label": f"host-{i:02d}", "environment": "Dev",
          "kind": "agent", "address": f"192.168.100.{i}", "online": True}
         for i in range(1, 17)]


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
    tmp = tmp_path_factory.mktemp("dayone")
    os.environ.update(
        SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1", SYSIBLE_SSO_SHARED_SECRET=SECRET,
        SYSIBLE_BASELINE_AGENT_TOKEN="t" * 64, SYSIBLE_BASELINE_DATA=str(tmp))
    os.environ.pop("SYSIBLE_BASELINE_DB", None)
    # A FRESH backend, and the old one put back afterwards.
    #
    # Dropping only "backend.app" from sys.modules is not enough: `app` stays
    # bound as an attribute of the already-imported `backend` package, so
    # `from backend import app` hands back the module another test file loaded,
    # with its database, its states and its assignments. This file passed alone
    # and failed whenever it ran after the fleet suite — which is the order
    # pytest collects them in.
    #
    # And purging without restoring breaks the files that run AFTER this one,
    # which is the same mistake pointed the other way. So: snapshot, purge,
    # and hand it all back on teardown.
    import importlib

    import backend as _pkg
    _names = ("backend", "backend.env", "backend.identity", "backend.store",
              "backend.app", "backend.controller", "backend.ui")
    _attrs = ("env", "identity", "store", "app", "controller", "ui")
    _saved_mods = {n: sys.modules[n] for n in _names if n in sys.modules}
    _saved_attrs = {a: getattr(_pkg, a) for a in _attrs if hasattr(_pkg, a)}
    for n in _names:
        sys.modules.pop(n, None)
    for a in _saved_attrs:
        delattr(_pkg, a)

    m = importlib.import_module("backend.app")
    m.controller.list_targets = lambda who: (FLEET, ["Dev"], None)


    srv = uvicorn.Server(uvicorn.Config(m.app, host="127.0.0.1", port=PORT,
                                        log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "baseline did not come up"

    # This file is about the console with NOTHING set up, so check that is what
    # it has. Over HTTP, because the schema is created when the app starts, not
    # when the module is imported — and through the same endpoints the console
    # reads, so a leak from another suite cannot hide behind a direct call.
    import urllib.request
    base = f"http://127.0.0.1:{PORT}"
    for path, key in (("/api/states", "states"), ("/api/assignments", "assignments")):
        req = urllib.request.Request(base + path, headers=OP)
        got = json.loads(urllib.request.urlopen(req, timeout=10).read())
        assert not got.get(key), (
            f"{path} is not empty: {got.get(key)}. Another suite's state has "
            f"leaked into this one, and every assertion here would be measuring "
            f"the wrong page.")

    try:
        yield base
    finally:
        srv.should_exit = True
        t.join(timeout=10)
        for n in _names:
            sys.modules.pop(n, None)
        sys.modules.update(_saved_mods)
        for a, v in _saved_attrs.items():
            setattr(_pkg, a, v)


@pytest.fixture
def pg(server):
    from playwright.sync_api import sync_playwright
    chrome = os.environ.get("SYSIBLE_TEST_CHROMIUM")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
        except Exception as e:
            pytest.skip(f"no chromium to drive ({e})")
        ctx = b.new_context(viewport={"width": 1280, "height": 1000},
                            extra_http_headers=OP)
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/", wait_until="domcontentloaded")
        page.wait_for_selector("#browselist .it", timeout=10000)
        page.wait_for_timeout(500)
        yield page
        assert not errs, errs
        b.close()


# ---- what the page opens with ----------------------------------------------
def test_it_does_not_open_with_a_scoreboard_of_zeros(pg):
    """Three large zeros are the same "nothing is set up" said again, in the
    largest type on the page."""
    assert pg.locator(".stat").count() == 0, \
        "the page still leads with a tally of a fleet nobody has configured"
    assert pg.locator("#col3 .start").count() == 1, \
        "there is no way in where the tally used to be"


def test_it_says_what_baseline_does_and_how_to_start(pg):
    import re
    panel = re.sub(r"\s+", " ", pg.text_content("#col3 .start")).strip()
    assert "Nothing is managed yet" in panel, panel
    assert "16 hosts are enrolled" in panel, \
        f"it does not connect the advice to this fleet: {panel}"
    for step in ("Declare a state", "Assign it", "Apply it"):
        assert step in panel, f"the way in is missing {step!r}: {panel}"


def test_the_way_in_is_a_button_that_works(pg):
    """An instruction that names a control is worse than the control."""
    pg.click("#startstate")
    pg.wait_for_selector("#dlg-state[open]", timeout=5000)
    pg.click("#dlg-state button[value=cancel]")


def test_a_reader_who_cannot_write_is_not_told_to_press_it(server):
    """An operator can declare a state; an auditor cannot, and the panel must
    not hand them a control that will be refused.

    Takes `server` and not `pg`: a second sync_playwright cannot be opened
    inside the one that fixture is already holding.
    """
    from playwright.sync_api import sync_playwright
    chrome = os.environ.get("SYSIBLE_TEST_CHROMIUM")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
        except Exception as e:
            pytest.skip(f"no chromium to drive ({e})")
        ctx = b.new_context(extra_http_headers={
            "x-sysible-user": "bob", "x-sysible-role": "auditor",
            "x-sysible-auth": SECRET})
        page = ctx.new_page()
        page.goto(server + "/", wait_until="domcontentloaded")
        page.wait_for_selector("#col3 .start", timeout=10000)
        assert page.locator("#startstate").count() == 0, \
            "an auditor is offered a button the server will refuse"
        assert "Ask an operator" in page.text_content("#col3 .start")
        b.close()


# ---- the fleet beside it ----------------------------------------------------
def test_the_fleet_does_not_repeat_it_once_per_host(pg):
    """Sixteen hosts came to 840px of screen whose entire content was "nothing
    is set up yet", said thirty-two times."""
    text = pg.text_content("#browselist")
    assert text.count("no state assigned to it or its environment") == 0, \
        "the per-row explanation is back"
    assert text.lower().count("not managed") <= 1, \
        f"'not managed' appears {text.lower().count('not managed')} times in the list"
    head = pg.text_content("#browselist .grp")
    assert "none managed" in head, head


def test_every_host_is_still_listed_and_still_assignable(pg):
    """Collapsing what they all say must not collapse the hosts themselves."""
    names = pg.evaluate(
        "() => [...document.querySelectorAll('#browselist .it .nm')]"
        ".map(e => e.textContent.trim())")
    assert sorted(names) == sorted(h["id"] for h in FLEET), names
    # And the action that covers all of them at once is the prominent one.
    assert pg.locator("#browselist [data-assignenv].primary").count() == 1


def test_the_fleet_fits_in_a_column_rather_than_a_page(pg):
    """The measurement that started this: 840px, 52.5px per host, to say
    nothing. A host is one line, and the column scrolls instead of the page."""
    per = pg.evaluate(
        """() => {const r = [...document.querySelectorAll('#browselist .it')];
             return r.length ? r.reduce((a, e) =>
               a + e.getBoundingClientRect().height, 0) / r.length : 999;}""")
    assert per < 34, f"{round(per, 1)}px per host — the rows are not one line"
    over = pg.evaluate(
        "() => document.documentElement.scrollHeight - window.innerHeight")
    assert over <= 1, f"the page scrolls by {over}px"


def test_the_filter_is_there_whatever_the_size_of_the_fleet(pg):
    """It heads the column. In a list that scrolls, a control that appears only
    past some size is a control nobody finds."""
    assert pg.locator("#browsefilter").is_visible() is True
