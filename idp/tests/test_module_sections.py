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
     "actions": []},
]

APPS = [
    {"key": "controller", "label": "Sysible Controller", "installed": True,
     "checked": True, "available": False, "current": "c0689c8", "can_update": False,
     "actions": ["restart", "stop", "start", "recreate"], "modules": [],
     "services": [{"service": "controller", "state": "running"}]},
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


def test_a_product_with_no_modules_gets_an_empty_hidden_container(page):
    """Not every product has modules. Its container must exist (ensureRow and
    paintModules both address it by id) and must not draw an empty box."""
    assert re.search(r"class=prod-mods id='mods-connect' hidden", page), \
        "a module-less product renders an empty section box"


# ---- no per-module update, and the page says why ---------------------------
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
        chrome = os.environ.get("SYSIBLE_TEST_CHROMIUM")
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

                # The aggregate line above the sections must not say the same
                # thing again: "5 services: 1 not running — baseline (exited)"
                # directly above a Baseline section reading "Exited" is one
                # sentence twice, and the sections are the better one.
                agg = (pg.text_content("#s-slop") or "").strip()
                assert "baseline" not in agg.lower(), (
                    "the summary line repeats what the sections already say: "
                    + repr(agg))
                # A service with no section is the one thing this line is still
                # for. Filtering the covered ones must not drop it.
                assert "extra-sidecar" in agg, (
                    "a running service with no section vanished from the page "
                    "entirely: " + repr(agg))
                # ...but a product WITHOUT sections still gets its summary.
                assert "service" in (pg.text_content("#s-controller") or ""), \
                    "a module-less product lost its service summary"

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
                    why = pg.text_content("#mod-slop-updater")
                    assert "carries out the request" in why, \
                        f"a module with no controls does not say why: {why!r}"

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
