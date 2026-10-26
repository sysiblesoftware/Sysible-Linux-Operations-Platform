"""The state builder, driven in a browser.

What the API tests cannot say: whether an operator can actually declare
something without writing YAML. These click through it — add items, read the
generated SLS, save, reopen — because every bug this screen has had so far was
invisible in the source and obvious the moment it was driven.

Run: pytest baseline/tests/
"""
import importlib
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
PORT = 8813
OP = {"x-sysible-user": "alice", "x-sysible-role": "operator", "x-sysible-auth": SECRET}
AUD = {"x-sysible-user": "zoe", "x-sysible-role": "auditor", "x-sysible-auth": SECRET}


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
    tmp = tmp_path_factory.mktemp("bl")
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


def _page(browser, server, headers=None):
    ctx = browser.new_context(viewport={"width": 1280, "height": 950},
                              extra_http_headers=headers or OP)
    pg = ctx.new_page()
    errs = []
    pg.on("console", lambda m: errs.append(m.text)
          if m.type == "error" and "Failed to load resource" not in m.text else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(server + "/", wait_until="domcontentloaded")
    pg.wait_for_timeout(700)
    return pg, ctx, errs


def _build(pg, name, kinds):
    pg.click("#newstate")
    pg.wait_for_selector("#s-kind")
    pg.fill("#s-name", name)
    for kind in kinds:
        pg.select_option("#s-kind", kind)
        pg.click("#s-add")
        pg.wait_for_timeout(120)


def test_a_state_can_be_declared_without_writing_any_yaml(browser, server):
    """The whole request: "I don't want baseline to be yaml based with the new
    states. It should be more intuitive." """
    pg, ctx, errs = _page(browser, server)
    try:
        _build(pg, "web-servers", ["package", "service"])
        pg.fill("#it-0-names", "nginx\ncurl")
        pg.fill("#it-1-name", "nginx")
        assert pg.is_checked("#it-1-enable"), \
            "'Also start at boot' does not default to on — a service that is " \
            "running now and off at boot is not a baseline"
        pg.click("#s-save")
        pg.wait_for_timeout(700)
        assert "Saved web-servers v1" in (pg.text_content("#msg") or "")
        assert not errs, errs
    finally:
        ctx.close()


def test_the_yaml_view_shows_what_the_list_builds(browser, server):
    pg, ctx, errs = _page(browser, server)
    try:
        _build(pg, "yaml-view", ["package"])
        pg.fill("#it-0-names", "htop")
        pg.click("[data-mode=yaml]")
        pg.wait_for_timeout(700)
        sls = pg.input_value("#s-sls")
        assert "pkg.installed" in sls and "htop" in sls, sls
        assert pg.evaluate("() => document.getElementById('s-sls').readOnly") is True, \
            "the preview is editable, so what is read is not what gets saved"
        assert not errs, errs
    finally:
        ctx.close()


def test_the_yaml_view_is_big_enough_to_read_a_whole_state(browser, server):
    """Reported: "the YAML window needs to be bigger as it's hard to see what
    you're doing." It was sharing the 230px textarea rule with the small
    single-field inputs, so a state of eight items — about fifty lines of
    generated SLS — was read eight lines at a time.

    In YAML mode the dialog has almost nothing else in it (a name field and one
    line of explanation), so the box gets the height going spare.
    """
    pg, ctx, errs = _page(browser, server)
    try:
        _build(pg, "roomy", ["package", "service", "file", "user",
                             "group", "sysctl", "cron", "command"])
        pg.fill("#it-0-names", "nginx")
        pg.fill("#it-1-name", "nginx")
        pg.fill("#it-2-path", "/etc/motd")
        pg.fill("#it-2-contents", "hello")
        pg.fill("#it-3-name", "deploy")
        pg.fill("#it-4-name", "devs")
        pg.fill("#it-5-name", "vm.swappiness")
        pg.fill("#it-5-value", "10")
        pg.fill("#it-6-command", "/usr/local/bin/x")
        pg.fill("#it-7-command", "/usr/local/bin/y")
        pg.click("[data-mode=yaml]")
        pg.wait_for_timeout(700)
        box = pg.evaluate(
            "() => {var t=document.getElementById('s-sls');"
            " var s=getComputedStyle(t); var r=t.getBoundingClientRect();"
            " return {h:Math.round(r.height), w:Math.round(r.width),"
            "         line:parseFloat(s.lineHeight), vh:innerHeight,"
            "         scroll:t.scrollHeight};}")
        lines = box["h"] / box["line"]
        assert lines >= 24, (
            f"the YAML box shows {lines:.0f} lines — a generated state is "
            f"longer than that: {box}")
        # It must not have grown past the window it lives in.
        assert box["h"] < box["vh"], f"the YAML box is taller than the viewport: {box}"
        assert pg.evaluate(
            "() => {var b=document.getElementById('s-save').getBoundingClientRect();"
            " return b.bottom <= innerHeight && b.top >= 0;}"), \
            "the taller YAML box pushed Save off screen"
        assert not errs, errs
    finally:
        ctx.close()


def test_switching_modes_does_not_lose_what_was_typed(browser, server):
    """Reading the YAML is something you do WHILE building — so the trip back
    must keep the form, and the trip out must pick up what changed since.

    The second half is the one that matters and the one this test originally
    missed: its first version only checked the inputs still held their values
    after a round trip, which they do even when nothing reads the form at all,
    because nothing re-renders them. The preview was then generated from a
    stale list and the test stayed green.
    """
    pg, ctx, errs = _page(browser, server)
    try:
        _build(pg, "round-trip", ["file"])
        pg.fill("#it-0-path", "/etc/motd")
        pg.fill("#it-0-contents", "managed by baseline")
        pg.click("[data-mode=yaml]")
        pg.wait_for_timeout(600)
        pg.click("[data-mode=build]")
        pg.wait_for_timeout(300)
        assert pg.input_value("#it-0-path") == "/etc/motd"
        assert pg.input_value("#it-0-contents") == "managed by baseline"

        # Change it, go back out, and the preview must have moved with it.
        pg.fill("#it-0-path", "/etc/issue")
        pg.click("[data-mode=yaml]")
        pg.wait_for_timeout(600)
        sls = pg.input_value("#s-sls")
        assert "/etc/issue" in sls, f"the preview was built from a stale list: {sls}"
        assert "/etc/motd" not in sls, f"the preview kept the old value: {sls}"
        assert not errs, errs
    finally:
        ctx.close()


def test_an_item_can_be_removed_and_reordered(browser, server):
    """Salt applies in file order, so the order of this list is the order the
    host is changed in."""
    pg, ctx, errs = _page(browser, server)
    try:
        _build(pg, "ordering", ["package", "service", "group"])
        pg.fill("#it-0-names", "nginx")
        pg.fill("#it-1-name", "nginx")
        pg.fill("#it-2-name", "web")
        pg.click('[data-drop="2"]')
        pg.wait_for_timeout(200)
        assert pg.evaluate("() => document.querySelectorAll('#s-items .item').length") == 2
        pg.click('[data-move="1:-1"]')
        pg.wait_for_timeout(200)
        labels = pg.evaluate(
            "() => [...document.querySelectorAll('#s-items .item-h b')].map(b=>b.textContent)")
        assert labels == ["Service running", "Packages installed"], labels
        assert pg.input_value("#it-0-name") == "nginx", \
            "the values did not move with the item"
        assert not errs, errs
    finally:
        ctx.close()


def test_a_built_state_reopens_in_the_builder(browser, server):
    pg, ctx, errs = _page(browser, server)
    try:
        _build(pg, "reopen-me", ["sysctl"])
        pg.fill("#it-0-name", "vm.swappiness")
        pg.fill("#it-0-value", "10")
        pg.click("#s-save")
        pg.wait_for_timeout(800)
        pg.click('[data-edit="reopen-me"]')
        pg.wait_for_selector("#s-items .item")
        assert pg.is_visible("#s-build"), "it reopened in YAML"
        assert pg.input_value("#it-0-name") == "vm.swappiness"
        assert pg.input_value("#it-0-value") == "10"
        assert not errs, errs
    finally:
        ctx.close()


def test_taking_over_the_yaml_is_one_way_and_says_so(browser, server):
    """Parsing SLS back into items would be a guess, and what a guess silently
    drops is the part the operator cannot see. So the switch is one-way — and
    the confirm has to say that before it happens, not after."""
    pg, ctx, errs = _page(browser, server)
    try:
        asked = {}
        pg.on("dialog", lambda d: (asked.update(text=d.message), d.accept()))
        _build(pg, "taken-over", ["group"])
        pg.fill("#it-0-name", "devs")
        pg.click("[data-mode=yaml]")
        pg.wait_for_timeout(600)
        pg.click("#s-takeover")
        pg.wait_for_timeout(200)
        assert "builder cannot be used on it again" in asked.get("text", ""), asked
        assert pg.evaluate("() => document.getElementById('s-sls').readOnly") is False
        pg.fill("#s-sls", "hand_written:\n  test.nop: []\n")
        pg.click("#s-save")
        pg.wait_for_timeout(800)
        assert "Saved taken-over v1" in (pg.text_content("#msg") or "")

        # Reopening it gives YAML, not an empty builder.
        pg.click('[data-edit="taken-over"]')
        pg.wait_for_timeout(500)
        assert pg.is_visible("#s-yaml"), "a hand-written state reopened in the builder"
        assert "hand_written" in pg.input_value("#s-sls")
        assert pg.evaluate("() => document.getElementById('s-takeover').hidden") is True
        assert not errs, errs
    finally:
        ctx.close()


def test_a_long_state_keeps_its_save_button_on_screen(browser, server):
    """The list gets longer exactly as the reason to save it gets stronger."""
    pg, ctx, errs = _page(browser, server)
    try:
        _build(pg, "long-one", ["package", "service", "file", "user",
                                "group", "sysctl", "cron", "command"])
        pg.wait_for_timeout(300)
        fits = pg.evaluate(
            "() => {var b=document.getElementById('s-save').getBoundingClientRect();"
            "return b.bottom <= innerHeight && b.top >= 0;}")
        assert fits, "with eight items the dialog grew past the viewport, Save included"
        assert not errs, errs
    finally:
        ctx.close()


def test_an_auditor_gets_a_console_that_can_actually_read(browser, server):
    """It is served to anyone signed in and renders read-only for a role that
    cannot write — but every read endpoint demanded an operator, so each panel
    in turn said "This needs an operator account"."""
    pg, ctx, errs = _page(browser, server, headers=AUD)
    try:
        pg.wait_for_timeout(900)
        body = pg.text_content("body") or ""
        assert "needs an operator account" not in body, \
            "the read-only console still cannot read"
        assert pg.evaluate("() => !!document.getElementById('newstate')") is False, \
            "an auditor is offered a New state button"
        assert not errs, errs
    finally:
        ctx.close()
