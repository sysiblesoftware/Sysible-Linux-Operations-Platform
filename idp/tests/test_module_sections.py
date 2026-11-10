"""Administration · Software & services — the module sections.

Reported: "Baseline and flashback should have their own sections on the updates
screens too." Before this, the only trace of either was one line reading
"6 services: all running" under the Operations Platform, so the screen could not
tell you that Flashback was installed, what it does, or which one of the six was
the one that was down.

Each module now has its own section. The three things that make a section honest
rather than decorative are what these tests drive:

  * its STATE is the real `compose ps` state of its own service, so a module
    that is down is visibly the one that is down;
  * its CONTROLS come from the updater's own refusal table, so a button that
    would be refused is never drawn — and the gap is explained in place of it;
  * there is NO per-module update, because the stack is one git checkout built
    by one compose file.

Driven in a real browser against a stubbed updater, because what is being
asserted is what an operator sees and what their click actually posts.

Run: pytest idp/tests/
"""
import importlib
import os
import re
import sys
import threading
import time
import warnings

import pytest

from _browser import chromium_path

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

ADMIN_PW = "adminpassword123"
PORT = 8791

# A fleet where the platform is up but ONE module inside it is not, and another
# is up while failing its own health check. That is the case the sections exist
# for: the aggregate line says "6 services", and only a section can say which.
MODULES = [
    {"key": "gateway", "label": "Gateway", "detail": "Caddy: the front door",
     "services": [{"service": "gateway", "state": "running", "status": "Up 2 days",
                   "health": "healthy"}],
     "actions": ["restart", "start", "recreate"]},
    {"key": "idp", "label": "Sign-in & accounts", "detail": "Sessions and SSO",
     "services": [{"service": "idp", "state": "running", "status": "Up 2 days",
                   "health": ""}],
     "actions": ["restart", "start", "recreate"]},
    {"key": "flashback", "label": "Sysible Flashback", "detail": "Backup and restore",
     "services": [{"service": "flashback", "state": "running", "status": "Up 3 hours",
                   "health": "unhealthy"}],
     "actions": ["restart", "stop", "start", "recreate"]},
    {"key": "baseline", "label": "Sysible Baseline", "detail": "Desired-state config",
     "services": [{"service": "baseline", "state": "exited",
                   "status": "Exited (1) 4 minutes ago", "health": ""}],
     "actions": ["restart", "stop", "start", "recreate"]},
    {"key": "visualizer", "label": "Sysible Visualizer", "detail": "Activity and logs",
     "services": [], "actions": ["restart", "stop", "start", "recreate"]},
    {"key": "updater", "label": "Updater", "detail": "Holds the Docker socket",
     "services": [{"service": "updater", "state": "running", "status": "Up 2 days",
                   "health": ""}],
     "actions": [],
     # The reason, in the words of the table that refuses it. The console used to
     # write this sentence itself from a guess at which rule had fired; it is
     # carried on the payload now, so there is one copy of it.
     "refusals": {a: ("the updater is the service carrying out the request — "
                      "acting on it from here would kill the call mid-run. Use "
                      "'sysiblectl slop restart' on the host.")
                  for a in ("restart", "stop", "start", "recreate")}},
]
_NO_STOP = {"stop": ("stopping it would leave no way to start it again from the "
                     "console. Use Restart, or 'sysiblectl slop stop' on the host.")}
for _m in MODULES:
    if _m["key"] in ("gateway", "idp"):
        _m["refusals"] = dict(_NO_STOP)
    elif "refusals" not in _m:
        _m["refusals"] = {}

# A single-service product's card, with the refusal that keeps it informational:
# `docker compose restart controller` in a one-service project is the same
# command the row's own Manage panel already runs, and two sets of buttons doing
# the same thing is how a console teaches an operator not to trust it.
def _solo(key, label, detail):
    return [{"key": key, "label": label, "detail": detail,
             "services": [{"service": key, "state": "running",
                           "status": "Up 2 days", "health": ""}],
             "actions": [],
             "refusals": {a: (f"{key} is a single service, so this is the same "
                              "command as the product's own Restart/Stop/Start/"
                              "Recreate — use those on the row.")
                          for a in ("restart", "stop", "start", "recreate")}}]


APPS = [
    {"key": "controller", "label": "Sysible Controller", "installed": True,
     "checked": True, "available": False, "current": "c0689c8", "can_update": False,
     "actions": ["restart", "stop", "start", "recreate"],
     "modules": _solo("controller", "Controller", "The console and its API"),
     "services": [{"service": "controller", "state": "running",
                   "status": "Up 2 days", "health": ""}]},
    {"key": "slep", "label": "Sysible Linux Engineering Platform", "installed": True,
     "checked": True, "available": False, "current": "e8aaa4e", "can_update": False,
     "actions": ["restart", "stop", "start", "recreate"],
     "modules": _solo("slep", "Engineering Platform", "Ansible and Terraform"),
     "services": [{"service": "slep", "state": "running",
                   "status": "Up 2 days", "health": ""}]},
    {"key": "connect", "label": "Sysible Connect", "installed": True,
     "checked": True, "available": False, "current": "d5bcb9b", "can_update": False,
     "actions": ["restart", "stop", "start", "recreate"],
     "modules": _solo("connect", "Connect", "Browser terminal workspace"),
     "services": [{"service": "connect", "state": "running",
                   "status": "Up 2 days", "health": ""}]},
    {"key": "slop", "label": "Sysible Linux Operations Platform", "installed": True,
     "checked": True, "available": False, "current": "538a889", "can_update": False,
     "actions": ["restart", "start", "recreate"], "modules": MODULES,
     # One service that has NO section — what a compose override on a real host
     # adds. The summary line is filtered so it does not repeat the sections; it
     # must not swallow this one, which is the only place it can be seen.
     "services": [m["services"][0] for m in MODULES if m["services"]]
                 + [{"service": "extra-sidecar", "state": "exited",
                     "status": "Exited (0) an hour ago", "health": ""}]},
]


@pytest.fixture()
def page(tmp_path):
    """The rendered page, as a superuser sees it — HTML only."""
    from starlette.testclient import TestClient
    os.environ.update(
        SLOP_DATA_DIR=str(tmp_path), SLOP_DB_PATH=str(tmp_path / "idp.db"),
        SLOP_ADMIN_USER="admin", SLOP_ADMIN_PASSWORD=ADMIN_PW,
        SLOP_ADMIN_FORCE_CHANGE="0", SLOP_ALLOW_INSECURE_COOKIE="1",
    )
    import app as m
    m = importlib.reload(m)
    with TestClient(m.app, base_url="http://slop.lan") as c:
        r = c.get("/login")
        tok = re.search(r"name=csrf value='([^']+)'", r.text).group(1)
        c.post("/login", data={"username": "admin", "password": ADMIN_PW, "csrf": tok},
               headers={"Origin": "http://slop.lan", "Referer": "http://slop.lan/login"})
        yield c.get("/admin/updates").text


# ---- the sections exist before the updater has said anything ---------------
def test_the_skeleton_already_names_flashback_and_baseline(page):
    """The skeleton is what the page renders with no updater reachable at all.
    Leaving the modules to the poll means an unreachable updater shows the
    platform with nothing inside it."""
    assert "id='mods-slop'" in page, "the SLOP row has no module section container"
    for name in ("Sysible Flashback", "Sysible Baseline", "Sysible Visualizer"):
        assert name in page, f"{name} is not on the page before the first poll"
    assert "id='mod-slop-baseline'" in page
    assert "id='mod-slop-flashback'" in page


def test_the_stale_hand_written_module_list_is_gone(page):
    """The SLOP row's label used to read "— gateway, sign-in, Flashback,
    Visualizer", a hand-maintained list that went stale the moment Baseline
    shipped. The sections replace it precisely so there is one table."""
    assert "gateway, sign-in, Flashback" not in page, \
        "the hard-coded module list is back in the product label"


def test_every_product_gets_the_same_row_shape(page):
    """Reported, looking at this screen: "Looks like we made connect, controller
    and slep then added slop. It doesn't look unified."

    It did not, and the cause was one key in updater/backend/apps.py:MODULES.
    SLOP was the only product with services declared, so SLOP was the only row
    that could say what was inside it — its row expanded into a grid of described
    cards with its own heading and footnote while the other three were a single
    terse line. The page read as three products with a fourth bolted on.

    Every product declares its services now, so every row carries the same
    disclosure and differs from its neighbour by the NUMBER in it.
    """
    for key in ("controller", "slep", "connect", "slop"):
        assert f"id='mods-{key}'" in page, f"{key} has no services grid"
        assert f"data-disc='{key}'" in page, f"{key} has no services disclosure"
        assert f"id='sv-{key}'" in page, f"{key} has no count to put in it"
    counts = dict(re.findall(r"id='sv-([a-z]+)'>Services \((\d+)\)", page))
    assert counts == {"controller": "1", "slep": "1", "connect": "1", "slop": "6"}, \
        f"the rows do not all carry a service count: {counts}"


def test_every_services_grid_starts_OPEN(page):
    """Open, not collapsed — and this is the one decision on this screen worth
    spelling out, because collapsing is the obvious thing to do with four rows
    and nine cards.

    These cards exist because of "I don't see a flashback tile", asked twice,
    about a screen where the only trace of Flashback was the digit 6 in
    "6 services: all running". Shutting them by default would make the page
    shorter and put the answer back behind a click, which is most of the way
    back to the complaint. What makes the rows uniform is the COUNT sitting in
    the same control on every row; what makes them useful is the cards being
    open. An operator who wants a row quiet can shut it, and it stays shut
    across the poll that repaints the row.
    """
    for key in ("controller", "slep", "connect", "slop"):
        assert not re.search(rf"class=prod-mods id='mods-{key}' hidden", page), \
            f"{key}'s services grid is shut before the page has been used"
        assert re.search(rf"id='d-{key}'[^>]*aria-expanded=true", page), \
            f"{key}'s disclosure claims to be shut while its grid is showing"


def test_a_shut_row_is_remembered_as_shut_not_read_off_the_dom(page):
    """The row is rebuilt on every poll, so the handler cannot ask the DOM what
    state it was in — the node it would ask has been replaced. It has to hold
    the answer itself, and paintModules has to re-apply it after rebuilding the
    grid, or a collapse silently springs back open a few seconds later."""
    assert "discShut" in page, "nothing remembers which rows were shut"
    assert re.search(r"var open\s*=\s*!discShut\[key\]", page), \
        "open is not derived from the remembered state"
    # paintModules must end by re-applying it; otherwise the rebuild wins.
    m = re.search(r"function paintModules\(a\)\{.*?\n  \}", page, re.S)
    assert m, "paintModules is gone"
    assert "applyDisc(a.key)" in m.group(0), \
        "paintModules rebuilds the grid without re-applying the shut state"


def test_the_arrow_is_drawn_from_the_state_it_claims(page):
    """The mark turns in CSS, off aria-expanded, rather than being written by
    the click handler. Two writers for one fact is how a shut grid ends up under
    an arrow pointing down — and it is what lets the SERVER-rendered row show the
    right mark with no JS having run."""
    assert ".svc-tw[aria-expanded=true]::before" in page, \
        "the disclosure arrow is not derived from aria-expanded"
    # The glyph appears exactly once on the page: in that CSS declaration. A
    # second occurrence means something is also writing it by hand. (Matching on
    # `.textContent` instead would catch `td.textContent`, which is the status
    # cell and has nothing to do with this.)
    assert page.count("\u25b8") == 1, (
        f"the arrow glyph appears {page.count(chr(0x25b8))} times; it belongs "
        "only in the CSS rule that derives it from aria-expanded")


# ---- no per-module update, and the page says why ---------------------------
def test_the_screen_says_why_a_module_is_filed_under_a_product(page):
    """Asked, looking at it: "Why are some not in SLOP but others are?"

    A fair question the screen did not answer. Flashback, Baseline and the
    Visualizer are products in their own right — their own portal tiles, their
    own marks, their own manuals — so finding them filed under another product
    while Connect gets a row of its own looks arbitrary until you know the rule.

    The rule is that this screen groups by WHAT UPDATES TOGETHER: a row is one
    git checkout, and the sections are the services built from it. The footer
    gave only the consequence ("updating it updates all of them"), which is the
    answer to a question nobody was asking.
    """
    assert "checkout gets its own row above" in page, \
        "nothing on the screen says why the other products are not in this list"
    # The "INSIDE THE … CHECKOUT" heading is gone, deliberately. It existed when
    # SLOP was the only product with cards and they needed saying what they were
    # inside OF; the disclosure that opens them carries that on every row now,
    # and a heading repeating it is a second label for one thing.
    assert "Inside this checkout" not in page, \
        "the SLOP-only section heading is back, beside the disclosure that says it"
    assert "mods-h" not in page, \
        "the heading's class is still being emitted or styled"
    # ...and the long explanation states the principle, not just the list.
    assert "what updates together" in page, \
        "the help text does not give the rule the grouping follows"


def test_there_is_no_per_module_update_button(page):
    """One git tree, one compose file: that button would rebuild the whole stack
    while claiming to touch one service."""
    assert "runModuleAction" in page, "the module controls are gone entirely"
    assert "startUpdate(a.key, m" not in page
    assert not re.search(r"runModuleAction\([^)]*'update'", page), \
        "a module-scoped update is being posted"
    # The absence is explained rather than left to be guessed at.
    assert 'no separate update for a module' in page


def test_the_module_action_post_goes_to_the_module_endpoint(page):
    """A module control must not fall through to the PRODUCT endpoint — that
    would restart the whole platform from the Baseline section."""
    m = re.search(r"function runModuleAction\(.*?\n  \}", page, re.S)
    assert m, "runModuleAction is gone"
    body = m.group(0)
    assert "/admin/updates/module-action" in body
    assert "/admin/updates/action'" not in body, \
        "a module control posts to the product-wide action endpoint"
    for field in ("'app'", "'module'", "'action'", "'csrf'"):
        assert f"body.set({field}" in body, f"the module action posts no {field}"


# ---- and the whole thing, in a browser ------------------------------------
def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _playwright(), reason="playwright not installed")
def test_the_sections_say_which_module_is_down_and_offer_only_real_controls(tmp_path):
    import uvicorn
    from playwright.sync_api import sync_playwright

    os.environ.update(
        SLOP_DATA_DIR=str(tmp_path), SLOP_DB_PATH=str(tmp_path / "idp.db"),
        SLOP_ADMIN_USER="admin", SLOP_ADMIN_PASSWORD=ADMIN_PW,
        SLOP_ADMIN_FORCE_CHANGE="0", SLOP_ALLOW_INSECURE_COOKIE="1",
    )
    import app as m
    m = importlib.reload(m)
    m.updates.status = lambda *a, **k: ({"apps": APPS, "job": None}, None)
    m.updates.job = lambda *a, **k: ({"job": None}, None)
    m.updates.configured = lambda: True
    posted = []

    def fake_module_action(app, module, action, user, role):
        posted.append((app, module, action, user))
        return {"started": True, "message": f"{action}ing {module}…"}, None
    m.updates.module_action = fake_module_action

    cfg = uvicorn.Config(m.app, host="127.0.0.1", port=PORT, log_level="error")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "test server did not come up"

    # Two different kinds of "error", kept apart on purpose. A JS exception or a
    # console error is a defect in this page. A 404 for /brand/topo-dark.svg is
    # not: in production the gateway serves those, and this harness runs the IdP
    # on its own — so the exclusion is by URL prefix, not by giving up on the
    # assertion. Anything else that fails to load still fails the test.
    errors, missing = [], []
    # Paths the GATEWAY serves, which the IdP has no route for and 404s when it
    # runs on its own like this: the portal at "/" (where login redirects by
    # default), the brand assets, the favicon. Matched on the PATH — "/" as a
    # prefix would swallow every URL on the site.
    def served_by_gateway(url: str) -> bool:
        from urllib.parse import urlsplit
        path = urlsplit(url).path
        return path == "/" or path.startswith(("/brand/", "/favicon"))
    try:
        chrome = chromium_path()
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
            except Exception as e:
                pytest.skip(f"no chromium to drive ({e})")
            for width in (1600, 1100, 390):
                pg = b.new_page(viewport={"width": width, "height": 1000})
                pg.on("console", lambda msg: errors.append(msg.text)
                      if msg.type == "error"
                      and "Failed to load resource" not in msg.text else None)
                pg.on("pageerror", lambda e: errors.append(str(e)))
                pg.on("requestfailed", lambda r: missing.append(r.url))
                pg.on("response", lambda r: missing.append(r.url)
                      if r.status >= 400 else None)
                pg.goto(f"http://127.0.0.1:{PORT}/login", wait_until="domcontentloaded")
                pg.fill("input[name=username]", "admin")
                pg.fill("input[name=password]", ADMIN_PW)
                pg.click("form button[type=submit]")
                pg.wait_for_timeout(400)
                pg.goto(f"http://127.0.0.1:{PORT}/admin/updates",
                        wait_until="domcontentloaded")
                pg.wait_for_selector("#mod-slop-baseline", timeout=5000)
                pg.wait_for_timeout(600)

                # The page must still fit. Six sections inside a row is the most
                # content this card has ever carried.
                doc = pg.evaluate("() => document.documentElement.scrollWidth")
                assert doc <= width, (
                    f"at {width}px the page is {doc}px wide — the module sections "
                    f"broke the card")

                # Every section is READABLE without opening anything: that is the
                # whole request. ("I don't see a flashback tile" twice over.)
                vis = pg.evaluate(
                    "() => [...document.querySelectorAll('#mods-slop .mod')]"
                    ".map(e => [e.id, e.offsetParent !== null])")
                assert len(vis) == 6, f"{len(vis)} sections rendered, expected 6: {vis}"
                assert all(v for _i, v in vis), f"a section is not visible: {vis}"

                # The one that is down is the one marked down.
                down = pg.evaluate(
                    "() => [...document.querySelectorAll('#mods-slop .mod.down')]"
                    ".map(e => e.id)")
                assert sorted(down) == ["mod-slop-baseline", "mod-slop-flashback",
                                        "mod-slop-visualizer"], \
                    f"the wrong modules are flagged: {down}"

                txt = pg.text_content("#mod-slop-baseline")
                assert "Exited" in txt, f"Baseline's real state is not shown: {txt!r}"
                fb = pg.text_content("#mod-slop-flashback")
                assert "unhealthy" in fb.lower(), (
                    "a running-but-unhealthy module reads as fine: " + repr(fb))
                vz = pg.text_content("#mod-slop-visualizer")
                assert "No container" in vz, \
                    f"a module with no container at all reads as running: {vz!r}"

                # The disclosure's label must not say the same thing again:
                # "6 services: 1 not running — baseline (exited)" directly above
                # a Baseline card reading "Exited" is one sentence twice, and the
                # card is the better one. What the label DOES carry is the count
                # and the rollup, which is what makes all four rows uniform.
                lab = (pg.text_content("#sv-slop") or "").strip()
                assert "baseline" not in lab.lower(), (
                    "the disclosure label repeats what the cards already say: "
                    + repr(lab))
                # Three things are wrong in this fleet: baseline exited, the
                # visualizer has no container at all, and extra-sidecar exited.
                # A service that compose ps does not list never reaches the
                # not-running filter, so the rollup used to say "2" over three
                # visibly-bad cards, one of which says "No container" outright.
                assert "3 not running" in lab, (
                    "the rollup misses a declared service with no container at "
                    f"all \u2014 missing is not better than exited: {lab!r}")
                # A service with NO card is the one thing a line of prose is
                # still for: it is inside the count and nowhere else, so without
                # this it would be a number with nothing behind it. It sits
                # beside the control (#sn-), not in it.
                note = (pg.text_content("#sn-slop") or "").strip()
                assert "extra-sidecar" in note, (
                    "a running service with no card vanished from the page "
                    "entirely: " + repr(note))
                # The count has to AGREE with what is behind the control: six
                # cards plus the one uncovered service.
                assert "(7)" in lab, (
                    "the count does not include the service that has no card, "
                    f"so it disagrees with what the disclosure opens onto: {lab!r}")
                # Every row carries the same control, the single-service ones
                # included — that is the change. And its own description, which
                # is what .prod-meta holds now that the count has moved.
                for key, n in (("controller", 1), ("slep", 1), ("connect", 1)):
                    one = (pg.text_content(f"#sv-{key}") or "")
                    assert f"({n})" in one, \
                        f"{key} has no service count in its disclosure: {one!r}"
                    assert (pg.text_content(f"#s-{key}") or "").strip(), \
                        f"{key}'s row has no description"

                # Controls are behind the row's existing Manage toggle, not six
                # new ones, and not on screen by default.
                shown = pg.evaluate(
                    "() => [...document.querySelectorAll('#mods-slop .mod-act')]"
                    ".filter(e => e.offsetParent !== null).length")
                assert shown == 0, f"{shown} module control groups are showing unasked"

                if width == 1100:
                    pg.click("#m-slop")
                    pg.wait_for_timeout(200)
                    labels = pg.evaluate(
                        "() => [...document.querySelectorAll('#mods-slop .mod')].map("
                        "e => [e.id, [...e.querySelectorAll('.mod-act button')]"
                        ".filter(b => b.offsetParent !== null).map(b => b.textContent)])")
                    got = dict(labels)
                    assert got["mod-slop-baseline"] == ["Restart", "Stop", "Start",
                                                        "Recreate"], got
                    # The gateway and the IdP cannot be STOPPED from the console:
                    # it is served through one and by the other.
                    assert "Stop" not in got["mod-slop-gateway"], got
                    assert "Stop" not in got["mod-slop-idp"], got
                    # The updater is the service running the command.
                    assert got["mod-slop-updater"] == [], got
                    # The reason shown IS the reason the payload carried, not a
                    # sentence the page composed from a guess at which rule had
                    # fired. Asserted against the stub's own string rather than
                    # against prose: the page used to hard-code two explanations,
                    # and when a third rule appeared it stated the wrong one with
                    # complete confidence.
                    why = pg.text_content("#mod-slop-updater")
                    expected = next(m["refusals"]["restart"] for m in MODULES
                                    if m["key"] == "updater")
                    assert expected in why, (
                        "the refusal shown is not the one the updater gave:\n"
                        f"  shown:    {why!r}\n  expected: {expected!r}")
                    # And the single-service products say THEIR reason, which is
                    # a different one — the row's own buttons already run it.
                    solo = pg.text_content("#mod-controller-controller")
                    assert "same command as the product's own" in solo, (
                        "a one-service product does not explain why its card has "
                        f"no buttons: {solo!r}")

                    # A click posts for THAT module, not the platform.
                    pg.on("dialog", lambda d: d.accept())
                    pg.click("#mod-slop-baseline .mod-act button:nth-child(1)")
                    pg.wait_for_timeout(500)
                    assert posted == [("slop", "baseline", "restart", "admin")], \
                        f"the Baseline section posted {posted}"

                    # Hiding takes them away again.
                    pg.click("#m-slop")
                    pg.wait_for_timeout(200)
                    again = pg.evaluate(
                        "() => [...document.querySelectorAll('#mods-slop .mod-act')]"
                        ".filter(e => e.offsetParent !== null).length")
                    assert again == 0, "Hide left the module controls on screen"
                pg.close()
            b.close()
    finally:
        server.should_exit = True
        t.join(timeout=10)

    assert not errors, f"the page logged errors: {errors}"
    unexpected = [u for u in missing if not served_by_gateway(u)]
    assert not unexpected, f"the page asked for something the IdP does not serve: {unexpected}"


# ---- when the updater cannot be reached ------------------------------------
# Reported: "updater not reachable".
#
# The updater is PART OF THE STACK IT UPDATES — updating SLOP recreates it — so
# it is guaranteed to be unreachable for a few seconds every single time. This
# page gave up permanently on that: one sticky toast, every product row left at
# "checking…", and no retry anywhere. The condition heals itself in seconds and
# the screen did not.
def test_the_page_retries_instead_of_giving_up(page):
    assert "function unreachable(" in page, "there is no failure path at all"
    assert "setTimeout" in page and "load()" in page, \
        "nothing re-asks after a failure — the page stays broken until a reload"
    assert "tries < 5" in page, "it retries forever or not at all"


def test_the_failure_explains_itself_rather_than_naming_an_exception(page):
    assert "carries out this page" in page, \
        "nothing says what the updater IS, so the message is just a stack trace"
    assert "updating SLOP restarts it" in page, \
        "it does not say that this is expected right after an update"
    for cmd in ("sysiblectl slop status", "sysiblectl slop logs"):
        assert cmd in page, f"the failure does not name {cmd}"


def test_a_row_does_not_keep_saying_checking_forever(page):
    """A spinner on a page that has finished. The same fault the unreported-
    product rows had, arriving by a different route."""
    m = re.search(r"function unreachable\(.*?\n  \}", page, re.S)
    assert m, "the failure path is gone"
    body = m.group(0)
    assert "Not known" in body, \
        "a failed poll leaves every row saying 'checking…' on a finished page"
    assert "staleModules" in body, \
        "the module sections keep whatever state they were last painted with"


@pytest.mark.skipif(not _playwright(), reason="playwright not installed")
def test_an_unreachable_updater_recovers_on_its_own(tmp_path):
    """Driven, because the point is the page healing itself without a reload."""
    import threading
    import time

    import uvicorn
    from playwright.sync_api import sync_playwright

    os.environ.update(
        SLOP_DATA_DIR=str(tmp_path), SLOP_DB_PATH=str(tmp_path / "idp.db"),
        SLOP_ADMIN_USER="admin", SLOP_ADMIN_PASSWORD=ADMIN_PW,
        SLOP_ADMIN_FORCE_CHANGE="0", SLOP_ALLOW_INSECURE_COOKIE="1",
    )
    import app as m
    m = importlib.reload(m)
    down = {"yes": True}

    def status(*a, **k):
        if down["yes"]:
            return None, "updater unreachable (ConnectError)"
        return {"apps": APPS, "job": None}, None

    m.updates.status = status
    m.updates.job = lambda *a, **k: ({"job": None}, None)
    m.updates.configured = lambda: True

    cfg = uvicorn.Config(m.app, host="127.0.0.1", port=8808, log_level="error")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started
    try:
        chrome = chromium_path()
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
            except Exception as e:
                pytest.skip(f"no chromium to drive ({e})")
            pg = b.new_page(viewport={"width": 1280, "height": 900})
            pg.goto("http://127.0.0.1:8808/login", wait_until="domcontentloaded")
            pg.fill("input[name=username]", "admin")
            pg.fill("input[name=password]", ADMIN_PW)
            pg.click("form button[type=submit]")
            pg.wait_for_timeout(400)
            pg.goto("http://127.0.0.1:8808/admin/updates", wait_until="domcontentloaded")

            pg.wait_for_selector("#updfail:not([hidden])", timeout=5000)
            txt = pg.text_content("#updfail")
            assert "ConnectError" in txt, txt
            assert "carries out this page" in txt, txt
            assert "Trying again in" in txt, f"it does not say it will retry: {txt}"
            # No row may still claim to be checking.
            assert "checking" not in (pg.text_content("#updtable") or "").lower(), \
                "rows are still spinning on a page that has finished"

            # The updater comes back, as it does seconds after every SLOP update.
            down["yes"] = False
            # state="hidden", not a [hidden] selector: wait_for_selector waits
            # for VISIBLE by default, so `#updfail[hidden]` waits for a hidden
            # element to become visible and times out however well the page
            # recovers. The first version of this test did exactly that and
            # failed against working code — the log read "26 x locator resolved
            # to hidden".
            pg.wait_for_selector("#updfail", state="hidden", timeout=15000)
            assert "Up to date" in (pg.text_content("#updtable") or ""), \
                "the page did not repaint once the updater answered again"
            pg.close()
            b.close()
    finally:
        server.should_exit = True
        t.join(timeout=10)
