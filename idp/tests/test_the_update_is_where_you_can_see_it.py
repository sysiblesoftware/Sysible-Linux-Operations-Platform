"""An update in progress has to be visible, and the page has to use the window.

Reported from a real update: "When updating this shouldn't be at the bottom of
the page. I didn't even see it down there." The progress panel was rendered
after the product table — so below four product rows and, for SLOP, six module
cards — which on any normal window put the thing the operator had just started
off the bottom of the page. They pressed Update now and nothing they could see
changed.

And, from the same screenshot: "why is this so narrow, there's a lot more page
it could take up." Every Administration tab used the login form's card, capped
at 760px — under half of a 1600px window. The module cards stacked one per row
down the middle, the status text wrapped to three lines beside a column of
nothing, and the Apps tab squeezed a whole embedded console into 760px.

So: the panel is above the table and scrolls itself into view the first time it
appears, and the Administration tabs get a width of their own.
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
PORT = 8793
BASE = f"http://127.0.0.1:{PORT}"

# Four products, the last of them carrying modules — the shape that made the
# page long enough for the panel to fall off the bottom.
APPS = [
    {"key": "controller", "label": "Sysible Controller", "installed": True,
     "checked": True, "available": True, "current": "c0689c8", "latest": "aa11bb2",
     "can_update": True, "actions": ["restart"],
     "services": [{"service": "controller", "state": "running"}]},
    {"key": "slep", "label": "Sysible Linux Engineering Platform", "installed": True,
     "checked": True, "available": True, "current": "1111111", "latest": "2222222",
     "can_update": True, "actions": ["restart"],
     "services": [{"service": "slep", "state": "running"}]},
    {"key": "connect", "label": "Sysible Connect", "installed": True,
     "checked": True, "available": False, "current": "d5bcb9b", "latest": "d5bcb9b",
     "can_update": False, "actions": ["restart"],
     "services": [{"service": "connect", "state": "running"}]},
    {"key": "slop", "label": "Sysible Linux Operations Platform", "installed": True,
     "checked": True, "available": True, "current": "538a889", "latest": "359854c",
     "can_update": True, "actions": ["restart", "start", "recreate"],
     "services": [{"service": "idp", "state": "running"}],
     "modules": [
         {"key": "gateway", "label": "Gateway", "state": "running",
          "detail": "Caddy: the platform's single front door", "uptime": "Up 2 days"},
         {"key": "idp", "label": "Sign-in & accounts", "state": "running",
          "detail": "The platform's identity provider", "uptime": "Up 46 hours"},
         {"key": "flashback", "label": "Sysible Flashback", "state": "running",
          "detail": "Backup and restore", "uptime": "Up 46 hours"},
         {"key": "baseline", "label": "Sysible Baseline", "state": "running",
          "detail": "Desired-state configuration", "uptime": "Up 46 hours"},
         {"key": "visualizer", "label": "Sysible Visualizer", "state": "running",
          "detail": "Activity and logs", "uptime": "Up 46 hours"},
         {"key": "updater", "label": "Updater", "state": "running",
          "detail": "The sidecar holding the host's Docker socket",
          "uptime": "Up 46 hours"},
     ]},
]

RUNNING_JOB = {"app": "slep", "state": "running", "started": time.time() - 43,
               "finished": None, "message": "", "log": ["line"] * 147,
               "self_update": False, "step": "pull",
               "queue": ["controller", "slep", "slop"], "index": 1}


def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.fixture()
def live(tmp_path, monkeypatch):
    if not _playwright():
        pytest.skip("playwright not installed")
    import uvicorn

    os.environ.update(
        SLOP_DATA_DIR=str(tmp_path), SLOP_DB_PATH=str(tmp_path / "idp.db"),
        SLOP_ADMIN_USER="admin", SLOP_ADMIN_PASSWORD=ADMIN_PW,
        SLOP_ADMIN_FORCE_CHANGE="0", SLOP_ALLOW_INSECURE_COOKIE="1",
    )
    import app as m
    m = importlib.reload(m)
    state = {"job": None}
    # monkeypatch, not assignment: m.updates is one shared module object for the
    # whole pytest process, and a plain attribute set outlives this test.
    monkeypatch.setattr(m.updates, "configured", lambda: True)
    monkeypatch.setattr(m.updates, "status",
                        lambda *a, **k: ({"apps": APPS, "job": state["job"]}, None))
    monkeypatch.setattr(m.updates, "job", lambda *a, **k: ({"job": state["job"]}, None))

    def _apply(key, *a, **k):
        state["job"] = dict(RUNNING_JOB, app=key)
        return {"message": f"Updating {key}…"}, None

    monkeypatch.setattr(m.updates, "apply", _apply)

    cfg = uvicorn.Config(m.app, host="127.0.0.1", port=PORT, log_level="error")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(150):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "test server did not come up"
    try:
        yield m, state
    finally:
        server.should_exit = True
        time.sleep(0.4)


def _open(pw, width=1600, height=900):
    chrome = os.environ.get("SYSIBLE_TEST_CHROMIUM")
    try:
        b = pw.chromium.launch(**({"executable_path": chrome} if chrome else {}),
                               args=["--no-sandbox"])
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no chromium to drive ({e})")
    pg = b.new_page(viewport={"width": width, "height": height})
    pg.on("dialog", lambda d: d.accept())
    pg.goto(f"{BASE}/login", wait_until="domcontentloaded")
    pg.fill("input[name=username]", "admin")
    pg.fill("input[name=password]", ADMIN_PW)
    pg.click("form button[type=submit]")
    pg.wait_for_url(re.compile(r".*/(admin|)$"), timeout=10000)
    return b, pg


def _goto_updates(pg):
    pg.goto(f"{BASE}/admin/updates", wait_until="domcontentloaded")
    pg.wait_for_selector("#b-slop", state="attached", timeout=10000)
    pg.wait_for_timeout(500)


# ---------------------------------------------------------------------------
# where the progress is
# ---------------------------------------------------------------------------
def test_the_progress_panel_is_above_the_product_table(live):
    """Document order, which is what decides whether it is on screen."""
    from playwright.sync_api import sync_playwright
    _m, state = live
    state["job"] = dict(RUNNING_JOB)
    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            _goto_updates(pg)
            pg.wait_for_selector("#jobpanel", state="visible", timeout=10000)
            order = pg.evaluate(
                "() => document.getElementById('jobpanel')"
                ".compareDocumentPosition(document.getElementById('updtable'))")
            # 4 = DOCUMENT_POSITION_FOLLOWING: the table comes after the panel.
            assert order & 4, "the progress panel is still below the product table"
        finally:
            b.close()


def test_a_running_update_is_on_screen_without_scrolling(live):
    """The actual complaint. Not 'is it in the DOM' — is it in the viewport when
    the page opens."""
    from playwright.sync_api import sync_playwright
    _m, state = live
    state["job"] = dict(RUNNING_JOB)
    with sync_playwright() as pw:
        b, pg = _open(pw, height=820)
        try:
            _goto_updates(pg)
            pg.wait_for_selector("#jobpanel", state="visible", timeout=10000)
            top = pg.evaluate(
                "() => document.getElementById('jobpanel').getBoundingClientRect().top")
            assert top < 820, (
                f"the panel starts {top:.0f}px down an 820px window — still below "
                "the fold")
        finally:
            b.close()


def test_starting_an_update_from_the_last_row_brings_it_into_view(live):
    """The button that starts a job can be several rows down, so being above the
    table is not enough on its own."""
    from playwright.sync_api import sync_playwright
    _m, _state = live
    with sync_playwright() as pw:
        b, pg = _open(pw, height=720)
        try:
            _goto_updates(pg)
            assert pg.evaluate("() => document.getElementById('jobpanel').hidden") is True, \
                "a quiet page should have no progress panel on it"
            pg.eval_on_selector("#b-slop", "e => e.scrollIntoView()")
            pg.click("#b-slop")
            pg.wait_for_selector("#jobpanel", state="visible", timeout=10000)
            pg.wait_for_timeout(900)          # the smooth scroll
            box = pg.evaluate("""() => {
              const r = document.getElementById('jobpanel').getBoundingClientRect();
              return {top: r.top, bottom: r.bottom, h: innerHeight};
            }""")
            assert box["bottom"] > 0 and box["top"] < box["h"], (
                f"after starting an update the panel is off-screen: {box}")
        finally:
            b.close()


def test_a_quiet_page_has_nothing_extra_on_it(live):
    from playwright.sync_api import sync_playwright
    _m, _state = live
    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            _goto_updates(pg)
            assert pg.evaluate("() => document.getElementById('jobpanel').hidden") is True
        finally:
            b.close()


# ---------------------------------------------------------------------------
# how much of the window it uses
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/admin/updates", "/admin",
                                  "/admin/settings", "/admin/apps"])
def test_every_administration_tab_uses_the_same_width(live, path):
    """One tab strip is one control: a card that resized as you moved along it
    would read as the page breaking."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            pg.goto(BASE + path, wait_until="domcontentloaded")
            pg.wait_for_selector(".card", timeout=10000)
            w = pg.evaluate("() => document.querySelector('.card').getBoundingClientRect().width")
            assert w > 1000, f"{path} is only {w:.0f}px wide in a 1600px window"
            assert w <= 1240 + 1, f"{path} is {w:.0f}px — wider than the cap"
        finally:
            b.close()


def test_it_still_fits_a_small_window(live):
    """The width is a cap, not a floor. The original report on this page was a
    horizontal scrollbar at every viewport, so this stays measured."""
    from playwright.sync_api import sync_playwright
    _m, state = live
    state["job"] = dict(RUNNING_JOB)
    with sync_playwright() as pw:
        b = None
        try:
            for width in (1600, 1100, 820, 390):
                b2, pg = _open(pw, width=width)
                b = b2
                _goto_updates(pg)
                doc = pg.evaluate("() => document.documentElement.scrollWidth")
                assert doc <= width + 1, (
                    f"at {width}px the page is {doc}px wide — a horizontal "
                    "scrollbar at every viewport again")
                b.close()
                b = None
        finally:
            if b:
                b.close()


def test_the_modules_flow_across_the_width_instead_of_stacking(live):
    """Six modules one per row is a column of short cards down the middle of a
    wide page — which is what 'a lot more page it could take up' was looking at."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            _goto_updates(pg)
            pg.wait_for_selector("#mods-slop .mod", timeout=10000)
            tops = pg.evaluate(
                "() => [...document.querySelectorAll('#mods-slop .mod')]"
                ".map(e => Math.round(e.getBoundingClientRect().top))")
            assert len(tops) >= 4, tops
            assert len(set(tops)) < len(tops), (
                "every module is on its own row at 1600px — they are still stacked")
        finally:
            b.close()


def test_the_modules_stack_again_when_there_is_no_room(live):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b, pg = _open(pw, width=390)
        try:
            _goto_updates(pg)
            pg.wait_for_selector("#mods-slop .mod", timeout=10000)
            tops = pg.evaluate(
                "() => [...document.querySelectorAll('#mods-slop .mod')]"
                ".map(e => Math.round(e.getBoundingClientRect().top))")
            assert len(set(tops)) == len(tops), (
                f"modules are sharing rows on a 390px screen: {tops}")
        finally:
            b.close()


def test_a_text_field_does_not_stretch_to_the_card(live):
    """The width is for tables and grids. An input at 1240px is not easier to
    use than one at 560."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            pg.goto(f"{BASE}/admin", wait_until="domcontentloaded")
            pg.wait_for_selector("input[name=username]", timeout=10000)
            w = pg.eval_on_selector("input[name=username]",
                                    "e => e.getBoundingClientRect().width")
            assert w <= 561, f"a text field is {w:.0f}px wide"
        finally:
            b.close()
