"""Automatic updates, from the console.

Asked for: "add an auto update option for slop and all components".

The scheduler itself is tested in updater/tests/test_automatic_updates.py. What
these tests are about is the half an operator actually touches, and the two ways
a settings panel lies:

  * it shows a state the server does not have (a checkbox left reading "on"
    after the save was refused, so the page claims a schedule that does not
    exist);
  * it says a run happened without saying whether anything came of it. "Last
    run: Tuesday 03:00" over a checkout that has been refusing for three weeks
    is the kind of green that gets believed.

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
PORT = 8794


def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def _server(tmp_path, au_state, refuse=False):
    """The IdP with a stubbed updater whose schedule lives in `au_state`."""
    import uvicorn

    os.environ.update(
        SLOP_DATA_DIR=str(tmp_path), SLOP_DB_PATH=str(tmp_path / "idp.db"),
        SLOP_ADMIN_USER="admin", SLOP_ADMIN_PASSWORD=ADMIN_PW,
        SLOP_ADMIN_FORCE_CHANGE="0", SLOP_ALLOW_INSECURE_COOKIE="1",
    )
    import app as m
    m = importlib.reload(m)
    m.updates.status = lambda *a, **k: ({"apps": [], "job": None}, None)
    m.updates.job = lambda *a, **k: ({"job": None}, None)
    m.updates.configured = lambda: True

    def _get(user, role):
        nxt = "Sunday 15 November at 03:00" if au_state["enabled"] else None
        return ({"autoupdate": dict(au_state), "next_run_text": nxt}, None)

    def _set(changes, user, role):
        if refuse:
            # The updater refused it -- a dirty state file, an older updater, a
            # value it will not take. The page must not keep the click.
            return None, "the updater refused that schedule"
        au_state.update(changes)
        return _get(user, role)

    # Patched on the `updates` MODULE, which importlib.reload(app) does NOT
    # reset -- so without putting them back, these stubs answer for every test
    # that runs after this file. That leak made another test pass in the suite
    # and fail on its own, which is the worst way round.
    _orig = (m.updates.autoupdate, m.updates.set_autoupdate,
             m.updates.status, m.updates.job, m.updates.configured)
    m.updates.autoupdate = _get
    m.updates.set_autoupdate = _set

    cfg = uvicorn.Config(m.app, host="127.0.0.1", port=PORT, log_level="error")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "test server did not come up"
    server._sysible_restore = (m.updates, _orig)
    return server


def _shutdown(server):
    """Stop the server AND put the stubs back on the updates module."""
    server.should_exit = True
    mod, orig = getattr(server, "_sysible_restore", (None, None))
    if mod is not None:
        (mod.autoupdate, mod.set_autoupdate, mod.status, mod.job,
         mod.configured) = orig


def _sign_in(pg):
    pg.goto(f"http://127.0.0.1:{PORT}/login", wait_until="domcontentloaded")
    pg.fill("input[name=username]", "admin")
    pg.fill("input[name=password]", ADMIN_PW)
    pg.click("form button[type=submit]")
    pg.wait_for_timeout(400)
    pg.goto(f"http://127.0.0.1:{PORT}/admin/updates", wait_until="domcontentloaded")
    pg.wait_for_selector("#au-enabled", timeout=8000)
    pg.wait_for_timeout(500)


# ---- the markup is there without any JS having run ------------------------
def test_the_control_is_server_rendered(tmp_path):
    """Not built by the poll. A panel that only exists once a fetch has
    succeeded is a panel that is missing on the host where the updater is the
    thing that is broken."""
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
        page = c.get("/admin/updates").text
    for ident in ("au-enabled", "au-cadence", "au-hour", "au-weekday",
                  "au-platform", "au-note"):
        assert f"id={ident}" in page, f"{ident} is not in the server-rendered page"
    # Hidden until the settings arrive: an unchecked box is a CLAIM that
    # automatic updates are off, and this page has not asked yet.
    assert re.search(r"class=autoup id=autoup hidden", page), \
        "the panel claims a schedule before it has asked the updater for one"


def test_the_post_route_is_superuser_and_csrf_guarded(tmp_path):
    from starlette.testclient import TestClient
    os.environ.update(
        SLOP_DATA_DIR=str(tmp_path), SLOP_DB_PATH=str(tmp_path / "idp.db"),
        SLOP_ADMIN_USER="admin", SLOP_ADMIN_PASSWORD=ADMIN_PW,
        SLOP_ADMIN_FORCE_CHANGE="0", SLOP_ALLOW_INSECURE_COOKIE="1",
    )
    import app as m
    m = importlib.reload(m)
    with TestClient(m.app, base_url="http://slop.lan") as c:
        # Signed out entirely.
        assert c.post("/admin/updates/autoupdate",
                      data={"enabled": "1"}).status_code in (401, 403, 302, 303, 307)
        r = c.get("/login")
        tok = re.search(r"name=csrf value='([^']+)'", r.text).group(1)
        c.post("/login", data={"username": "admin", "password": ADMIN_PW, "csrf": tok},
               headers={"Origin": "http://slop.lan", "Referer": "http://slop.lan/login"})
        # Signed in, no CSRF token: still refused. A schedule that updates the
        # host is as much a mutation as a restart is.
        bad = c.post("/admin/updates/autoupdate", data={"enabled": "1", "csrf": "no"},
                     headers={"Origin": "http://slop.lan"})
        assert bad.status_code == 403, bad.text


# ---- and in a browser -----------------------------------------------------
@pytest.mark.skipif(not _playwright(), reason="playwright not installed")
def test_turning_it_on_saves_and_says_when_the_next_run_is(tmp_path):
    from playwright.sync_api import sync_playwright
    state = {"enabled": False, "cadence": "weekly", "hour": 3, "weekday": 6,
             "include_platform": True, "last": None}
    server = _server(tmp_path, state)
    try:
        with sync_playwright() as p:
            chrome = chromium_path()
            try:
                b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
            except Exception as e:                                  # noqa: BLE001
                pytest.skip(f"no chromium to drive ({e})")
            pg = b.new_page(viewport={"width": 1180, "height": 1000})
            _sign_in(pg)

            # Off: the schedule controls are not on screen at all.
            assert pg.is_hidden("#au-when"), \
                "the schedule is shown while automatic updates are off"
            assert pg.is_checked("#au-enabled") is False

            pg.check("#au-enabled")
            pg.wait_for_timeout(600)
            assert state["enabled"] is True, "the click did not reach the updater"
            assert pg.is_visible("#au-when"), "turning it on did not reveal the schedule"
            note = pg.text_content("#au-note") or ""
            assert "Next run" in note, f"it does not say when the next run is: {note!r}"

            # Weekly shows a day; daily must not -- "every day on Sunday" is not
            # a schedule.
            assert pg.is_visible("#au-day-wrap")
            pg.select_option("#au-cadence", "daily")
            pg.wait_for_timeout(600)
            assert state["cadence"] == "daily"
            assert pg.is_hidden("#au-day-wrap"), \
                "a daily schedule still offers a day of the week"

            pg.select_option("#au-hour", "5")
            pg.wait_for_timeout(600)
            assert state["hour"] == 5, state

            # The platform can be left out: it is the one that signs everyone
            # out when it updates.
            pg.uncheck("#au-platform")
            pg.wait_for_timeout(600)
            assert state["include_platform"] is False, state
            b.close()
    finally:
        _shutdown(server)


@pytest.mark.skipif(not _playwright(), reason="playwright not installed")
def test_a_refused_run_is_shown_as_refused(tmp_path):
    """The lie that matters. A scheduler that reports "last run" without saying
    whether anything came of it is the one that gets believed about a checkout
    which has been refusing for three weeks."""
    from playwright.sync_api import sync_playwright
    state = {"enabled": True, "cadence": "weekly", "hour": 3, "weekday": 6,
             "include_platform": True,
             "last": {"at": 1763000000, "outcome": "refused",
                      "detail": "blocked: slep (local changes)",
                      "products": []}}
    server = _server(tmp_path, state)
    try:
        with sync_playwright() as p:
            chrome = chromium_path()
            try:
                b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
            except Exception as e:                                  # noqa: BLE001
                pytest.skip(f"no chromium to drive ({e})")
            pg = b.new_page(viewport={"width": 1180, "height": 1000})
            _sign_in(pg)
            note = pg.text_content("#au-note") or ""
            assert "did not run" in note, (
                f"a refused run does not read as a refusal: {note!r}")
            assert "local changes" in note, (
                f"the reason it refused is not shown: {note!r}")
            cls = pg.get_attribute("#au-note", "class") or ""
            assert "warn" in cls, (
                "a refused run is not marked as needing attention; it reads "
                "exactly like a successful one")
            b.close()
    finally:
        _shutdown(server)


@pytest.mark.skipif(not _playwright(), reason="playwright not installed")
def test_a_refused_save_does_not_leave_the_page_claiming_a_schedule(tmp_path):
    """The other lie. The checkbox has to go back to what the SERVER has."""
    from playwright.sync_api import sync_playwright
    state = {"enabled": False, "cadence": "weekly", "hour": 3, "weekday": 6,
             "include_platform": True, "last": None}
    server = _server(tmp_path, state, refuse=True)
    try:
        with sync_playwright() as p:
            chrome = chromium_path()
            try:
                b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
            except Exception as e:                                  # noqa: BLE001
                pytest.skip(f"no chromium to drive ({e})")
            pg = b.new_page(viewport={"width": 1180, "height": 1000})
            _sign_in(pg)
            pg.check("#au-enabled")
            pg.wait_for_timeout(900)
            assert state["enabled"] is False, "the stub should have refused"
            assert pg.is_checked("#au-enabled") is False, (
                "the page still shows automatic updates as ON after the updater "
                "refused to turn them on")
            b.close()
    finally:
        _shutdown(server)
