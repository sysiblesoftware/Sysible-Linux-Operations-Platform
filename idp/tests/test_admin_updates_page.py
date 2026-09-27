"""Administration · Software & services — the page an operator acts on.

Reported: "not a good UI, make it more intuitive and less busy." Measured before
touching it, at three widths: the document was 2173px wide inside a 760px card,
with control buttons running up to 993px past the card's right edge and a
horizontal scrollbar at every viewport including 1600px.

The cause was one line of CSS that was never written. The page asks for
.btn / .ghost / .sm; this stylesheet defined none of them, so every control fell
through to the base `button{}` rule — the login form's full-width green submit.
Four of those per product is sixteen identical full-width green bars, which is
both the overflow and the "busy": nothing on the page said which button mattered,
and the loudest thing on it was "Check now".

What the page is for is narrow: is anything out of date, and is anything down.
So each product now shows its state as one phrase, at most ONE action inline (the
update, when there is one), and the recovery controls — rare, interrupting, and
including Stop — behind that row's own toggle.

Two of these tests exist because I broke exactly what they check while doing it:
the toggle carried `data-app`, which the page binds startUpdate() to, so clicking
"Manage" posted an update; and `.prod-controls{display:flex}` beat the `hidden`
attribute, so every panel rendered permanently open. Neither is visible in the
source — both are obvious the moment the page is driven.
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

# What the updater reports on a fleet with one product behind, two with dirty
# checkouts, and everything running — the reported state.
APPS = [
    {"key": "controller", "label": "Sysible Controller", "installed": True,
     "checked": True, "available": False, "current": "c0689c8", "can_update": False,
     "actions": ["restart", "stop", "start", "recreate"],
     "services": [{"service": "controller", "state": "running"}]},
    {"key": "slep", "label": "Sysible Linux Engineering Platform", "installed": True,
     "checked": True, "available": False, "current": "c0f7ec4", "can_update": False,
     "reason": "the checkout has local changes — resolve them on the host first",
     "actions": ["restart", "stop", "start", "recreate"],
     "services": [{"service": "slep", "state": "running"}]},
    {"key": "slop", "label": "Sysible Linux Operations Platform", "installed": True,
     "checked": True, "available": True, "current": "538a889", "latest": "359854c",
     "can_update": True, "actions": ["restart", "start", "recreate"],
     "services": [{"service": "idp", "state": "running"}]},
]


@pytest.fixture()
def page(tmp_path):
    """The rendered page, as a superuser sees it."""
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


# ---- the cause: a button vocabulary that was never defined -----------------
def test_the_button_classes_the_page_uses_are_actually_defined(page):
    """Without these every control inherits the login form's full-width green
    submit — which is how four controls per product became 993px of overflow."""
    for cls in (".btn{", ".btn.ghost", ".btn.sm"):
        assert cls in page, f"{cls} is used by this page and defined nowhere"


def test_a_control_button_is_not_full_width(page):
    m = re.search(r"\.btn\{([^}]*)\}", page)
    assert m, ".btn is gone"
    assert "width:auto" in m.group(1), \
        "a control button inherits width:100% from the form submit again"


def test_stop_is_marked_as_the_dangerous_one(page):
    assert ".btn.danger" in page, "Stop looks like every other control again"


# ---- less busy: one action inline, the rest behind the row's own toggle -----
def test_the_recovery_controls_start_hidden(page):
    assert re.search(r"class=prod-controls id='a-[a-z]+' hidden", page), \
        "the per-row controls panel is not hidden to begin with"


def test_hidden_actually_hides_the_panel(page):
    """An author `display` beats the hidden attribute's UA display:none, so
    without this rule every Manage panel renders permanently open — which is the
    row of four buttons the toggle exists to put away."""
    assert ".prod-controls[hidden]{display:none}" in page, \
        "display:flex will override the hidden attribute again"


def test_the_manage_toggle_cannot_trigger_an_update(page):
    """The page binds startUpdate() to buttons carrying data-app. A toggle with
    that attribute posts an update when clicked — on the platform row, behind the
    'you will be signed out' confirm."""
    toggles = re.findall(r"<button[^>]*id='m-[a-z]+'[^>]*>", page)
    assert toggles, "the Manage toggles are gone"
    for t in toggles:
        assert "data-app" not in t, f"a Manage toggle carries data-app: {t}"
        assert "data-manage" in t, f"a Manage toggle has no key to toggle: {t}"


def test_the_update_binding_names_the_update_buttons(page):
    """`button[data-app]` is a description of "any button that knows its product",
    so any new control given that attribute silently becomes an update trigger."""
    assert 'querySelectorAll(\'button[data-app]\')' not in page, \
        "the update handler is bound to every button that carries data-app again"
    assert 'button.btn[data-app][id^=' in page


def test_a_product_the_updater_does_not_manage_is_resolved(page):
    """The skeleton hardcodes four products so the list survives an unreachable
    updater. When the updater answers and omits one, that row used to keep its
    initial 'checking...' forever — a spinner on a page that has finished."""
    assert "resolveUnreported(d.apps" in page, \
        "an unreported product row is left saying 'checking...' again"
    assert "function resolveUnreported(" in page
    assert "Not managed here" in page


# ---- the prose is available without being in the way -----------------------
def test_the_long_explanation_is_behind_a_disclosure(page):
    assert "<details class=help>" in page, \
        "the two paragraphs are back in front of the controls"
    assert "What do these controls do?" in page
    # ...but the detail itself must still be there to be read.
    for kept in ("Recreate", "sysible_ctl", "cannot be STOPPED"):
        assert kept in page, f"the explanation lost {kept!r}"


def test_check_now_is_not_the_loudest_thing_on_the_page(page):
    """It re-asks the git remotes. It was a full-width green bar above a page
    whose actual action is 'Update now'."""
    m = re.search(r"<button[^>]*id=checknow[^>]*>", page)
    assert m, "Check now is gone"
    assert "ghost" in m.group(0) and "sm" in m.group(0), \
        f"Check now outranks the update button again: {m.group(0)}"


# ---- and the whole thing, in a browser -------------------------------------
def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _playwright(), reason="playwright not installed")
def test_the_page_fits_its_card_and_the_toggle_is_inert(tmp_path):
    """No amount of reading HTML proves a layout. This loads the page at three
    widths and measures it, then clicks every Manage toggle and asserts nothing
    was posted."""
    import uvicorn
    from playwright.sync_api import sync_playwright

    os.environ.update(
        SLOP_DATA_DIR=str(tmp_path), SLOP_DB_PATH=str(tmp_path / "idp.db"),
        SLOP_ADMIN_USER="admin", SLOP_ADMIN_PASSWORD=ADMIN_PW,
        SLOP_ADMIN_FORCE_CHANGE="0", SLOP_ALLOW_INSECURE_COOKIE="1",
    )
    import app as m
    m = importlib.reload(m)
    # Stand in for the updater, so the page renders a full fleet.
    m.updates.status = lambda *a, **k: ({"apps": APPS, "job": None}, None)
    m.updates.job = lambda *a, **k: ({"job": None}, None)
    m.updates.configured = lambda: True

    cfg = uvicorn.Config(m.app, host="127.0.0.1", port=8788, log_level="error")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "test server did not come up"

    try:
        chrome = os.environ.get("SYSIBLE_TEST_CHROMIUM")
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
            except Exception as e:
                pytest.skip(f"no chromium to drive ({e})")
            for width in (1600, 1100, 390):
                pg = b.new_page(viewport={"width": width, "height": 900})
                pg.goto("http://127.0.0.1:8788/login", wait_until="domcontentloaded")
                pg.fill("input[name=username]", "admin")
                pg.fill("input[name=password]", ADMIN_PW)
                pg.click("form button[type=submit]")
                pg.wait_for_timeout(400)
                pg.goto("http://127.0.0.1:8788/admin/updates", wait_until="domcontentloaded")
                pg.wait_for_selector(".prod-name", timeout=5000)
                pg.wait_for_timeout(500)

                doc = pg.evaluate("() => document.documentElement.scrollWidth")
                assert doc <= width, (
                    f"at {width}px the page is {doc}px wide — it does not fit its own "
                    f"card, so every viewport gets a horizontal scrollbar")

                open_now = pg.evaluate(
                    "() => [...document.querySelectorAll('.prod-controls')]"
                    ".map(e => e.offsetParent !== null)")
                assert open_now and not any(open_now), \
                    "the recovery controls are showing before anyone asked for them"

                # The skeleton also lists Sysible Connect, which this updater
                # does not manage. That row must say so, not sit at
                # "checking\u2026" on a page that has finished loading.
                unreported = pg.text_content("#u-connect")
                assert "checking" not in unreported.lower(), (
                    "a product the updater never reported is still showing "
                    "'checking\u2026' after the page finished loading")
                assert "Not managed here" in unreported
                assert pg.evaluate(
                    "() => document.getElementById('m-connect').hidden") is True, \
                    "a product with no actions still offers Manage"

                if width == 1100:
                    posts = []
                    pg.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
                    pg.on("dialog", lambda d: d.dismiss())
                    for key in ("controller", "slep", "slop"):
                        pg.click(f"#m-{key}")
                        pg.wait_for_timeout(150)
                    assert not posts, f"clicking Manage posted: {posts}"
                    shown = pg.evaluate(
                        "() => ['controller','slep','slop'].map("
                        "k => document.getElementById('a-'+k).offsetParent !== null)")
                    assert all(shown), f"Manage did not reveal the controls: {shown}"
                    assert pg.evaluate(
                        "() => document.getElementById('a-connect')"
                        ".offsetParent !== null") is False, \
                        "an untouched row's controls opened too"
                    # exactly one product is behind, so exactly one Update button
                    vis = pg.evaluate(
                        "() => [...document.querySelectorAll('button[id^=b-]')]"
                        ".filter(b => !b.hidden).length")
                    assert vis == 1, f"{vis} Update buttons visible; exactly one product is behind"
                pg.close()
            b.close()
    finally:
        server.should_exit = True
        t.join(timeout=10)
