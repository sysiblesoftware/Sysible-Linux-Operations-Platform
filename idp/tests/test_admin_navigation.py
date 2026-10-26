"""Administration · the navigation, and what it replaced.

Reported: "the administration panel is confusing."

It was. Each of the four pages hand-wrote its own run of links to the OTHER
three — a sentence of undifferentiated blue text that left out the page you were
on, mixed the four SECTIONS of Administration in with two links that LEAVE it
(Your account, Portal →), repeated the "← Portal" button already sitting at the
top-left of the same page, and had drifted: Software & services had quietly lost
"Apps" altogether, so from that page the Apps section did not exist. No page
ever showed the whole set, and none of them said where you were.

One <nav>, generated from one table, with the current section marked.

Two more things this page was doing to its operator, both fixed here:

  * Configuration is eight tables and about three thousand pixels tall, with no
    way to reach any one of them but scrolling past the others.
  * One parameter description was written with real markup inside it, in a
    field the renderer escapes — so the characters `<span class=mono>` were
    rendered to the operator, as text, mid-sentence.

Run: pytest idp/tests/
"""
import importlib
import os
import re
import sys
import warnings

import pytest

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

ADMIN_PW = "adminpassword123"
SECTIONS = ("/admin", "/admin/apps", "/admin/settings", "/admin/updates")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from starlette.testclient import TestClient
    tmp = tmp_path_factory.mktemp("idp")
    os.environ.update(
        SLOP_DATA_DIR=str(tmp), SLOP_DB_PATH=str(tmp / "idp.db"),
        SLOP_ADMIN_USER="admin", SLOP_ADMIN_PASSWORD=ADMIN_PW,
        SLOP_ADMIN_FORCE_CHANGE="0", SLOP_ALLOW_INSECURE_COOKIE="1",
    )
    import app as m
    m = importlib.reload(m)
    m.updates.status = lambda *a, **k: ({"apps": [], "job": None}, None)
    m.updates.job = lambda *a, **k: ({"job": None}, None)
    m.updates.configured = lambda: True
    with TestClient(m.app, base_url="http://slop.lan") as c:
        r = c.get("/login")
        tok = re.search(r"name=csrf value='([^']+)'", r.text).group(1)
        c.post("/login", data={"username": "admin", "password": ADMIN_PW, "csrf": tok},
               headers={"Origin": "http://slop.lan", "Referer": "http://slop.lan/login"})
        yield c


def _nav(html):
    m = re.search(r"<nav class=anav.*?</nav>", html, re.S)
    assert m, "the page has no Administration nav"
    return m.group(0)


@pytest.mark.parametrize("path", SECTIONS)
def test_every_section_lists_every_section(client, path):
    """Including itself. The old runs omitted the current page, so the set you
    could see depended on where you were standing."""
    nav = _nav(client.get(path).text)
    for href in SECTIONS:
        assert f"href='{href}'" in nav, f"{path} does not list {href}"


@pytest.mark.parametrize("path", SECTIONS)
def test_the_page_you_are_on_is_marked(client, path):
    nav = _nav(client.get(path).text)
    current = re.findall(r"href='([^']+)'[^>]*aria-current=page", nav)
    assert current == [path], \
        f"{path} marks {current} as current — it must mark exactly itself"


def test_the_sections_are_in_the_same_order_everywhere(client):
    """Four hand-written runs meant four orders. Reading the same strip in a
    different sequence on every page is most of what made it unreadable."""
    orders = {p: re.findall(r"href='(/admin[^']*)'", _nav(client.get(p).text))
              for p in SECTIONS}
    first = orders[SECTIONS[0]]
    for path, got in orders.items():
        assert got == first, f"{path} orders the sections {got}, elsewhere {first}"


@pytest.mark.parametrize("path", SECTIONS)
def test_the_nav_does_not_duplicate_the_back_button(client, path):
    """Every admin page carries "← Portal" top-left. The old run ALSO ended in
    "Portal →" — the same destination, twice, pointing opposite ways."""
    html = client.get(path).text
    assert "class=back href='/'" in html, "the Portal back button is gone"
    assert "href='/'" not in _nav(html), \
        "the nav links back to the Portal as well as the button above it"


@pytest.mark.parametrize("path", SECTIONS)
def test_the_old_link_run_is_gone(client, path):
    """The thing being replaced: links joined by middots in a <p class=sub>."""
    html = client.get(path).text
    runs = re.findall(r"<p class=sub>(?:<a[^>]*>[^<]*</a>\s*·\s*){2,}", html)
    assert not runs, f"{path} still renders a run of middot-separated links: {runs[:1]}"


def test_the_updates_count_rides_the_tab_it_leads_to(client):
    """It was a separate "⬆ Updates available 3" pill in the same line, linking
    to the same page: two controls for one fact."""
    html = client.get("/admin").text
    nav = _nav(html)
    tab = re.search(r"<a href='/admin/updates'[^>]*>(.*?)</a>", nav, re.S)
    assert tab, "the Software & services tab is gone"
    assert "id=updn" in tab.group(1), "the count is not on the tab"
    assert "class=upd-pill" not in html, "the old standalone pill is back"
    assert "getElementById('updn')" in html, "nothing fills the count in"


# ---- Configuration: eight tables, and a way to reach them -------------------
def test_configuration_has_a_jump_index_that_matches_its_sections(client):
    html = client.get("/admin/settings").text
    idx = re.search(r"<nav class=cfgindex.*?</nav>", html, re.S)
    assert idx, "Configuration has no section index"
    targets = re.findall(r"href='#cfg-([a-z]+)'", idx.group(0))
    ids = re.findall(r"<fieldset id='cfg-([a-z]+)'", html)
    assert len(ids) >= 8, f"only {len(ids)} sections found"
    assert targets == ids, (
        f"the index points at {targets} and the sections are {ids} — an entry "
        f"that scrolls nowhere is worse than no index")


def test_no_markup_is_rendered_to_the_operator_as_text(client):
    """Descriptions are escaped, which is RIGHT: several contain literal
    placeholders like <app> and <host-ip> that must show as angle brackets. One
    had been written with a real <span> in it, so the operator was shown the
    characters of the tag mid-sentence.

    The rule is precise rather than "no escaped angle brackets": a placeholder
    is not an HTML element, and real HTML element names are what markup leaking
    into a description looks like.
    """
    html = client.get("/admin/settings").text
    elements = ("span", "div", "code", "b", "i", "em", "strong", "a", "p", "br")
    leaked = [t for t in re.findall(r"&lt;/?([a-zA-Z][a-zA-Z0-9-]*)[^&]{0,60}&gt;", html)
              if t.lower() in elements]
    assert not leaked, f"markup is being shown as text: {sorted(set(leaked))}"
    # ...and the thing it was reaching for still renders as code.
    assert "<code>$SLOP_DATA_DIR/initial-password</code>" in html, \
        "the path lost its code formatting when the markup was removed"


def test_a_literal_placeholder_is_still_shown_as_one(client):
    """The other half of the rule above: these MUST stay escaped."""
    html = client.get("/admin/settings").text
    for placeholder in ("&lt;app&gt;", "&lt;host-ip&gt;"):
        assert placeholder in html, f"{placeholder} stopped rendering as text"


# ---- the app frame, when the app is not there -------------------------------
def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _playwright(), reason="playwright not installed")
def test_a_missing_app_gets_a_sentence_not_a_json_dump(tmp_path):
    """Administration → Apps embeds each app's own console. When the app is
    down — or behind a gateway route that answers 404 — the frame rendered
    whatever came back: a 700px black panel containing the two words
    {"detail":"Not Found"} and a "Pretty-print" checkbox. The browser's JSON
    viewer, shown to an operator as if it were a console.

    This harness runs the IdP with nothing upstream, so /controller/ 404s —
    which is exactly the failure. SLOP is one origin, so the frame's document
    can be read: a real console is hundreds of elements, a failure is a handful
    and a line of text.
    """
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
    m.updates.status = lambda *a, **k: ({"apps": [], "job": None}, None)
    m.updates.job = lambda *a, **k: ({"job": None}, None)
    m.updates.configured = lambda: True

    cfg = uvicorn.Config(m.app, host="127.0.0.1", port=8807, log_level="error")
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
            pg = b.new_page(viewport={"width": 1280, "height": 900})
            pg.goto("http://127.0.0.1:8807/login", wait_until="domcontentloaded")
            pg.fill("input[name=username]", "admin")
            pg.fill("input[name=password]", ADMIN_PW)
            pg.click("form button[type=submit]")
            pg.wait_for_timeout(400)
            pg.goto("http://127.0.0.1:8807/admin/apps", wait_until="domcontentloaded")
            pg.wait_for_selector("#framefail:not([hidden])", timeout=5000)

            assert pg.evaluate(
                "() => document.getElementById('appframe').offsetParent === null"), \
                "the failed frame is still on screen next to the message explaining it"
            txt = pg.text_content("#framefail")
            assert "did not load its administration" in txt, txt
            assert "Not Found" in txt, \
                f"the message does not say what the app actually returned: {txt!r}"
            assert "Open Sysible Controller directly" in txt
            # The hint used to sit AFTER the frame while calling it "the panel
            # below". Whatever it says now, it must be above what it describes.
            order = pg.evaluate(
                "() => {var h=[...document.querySelectorAll('p.sub')]"
                ".find(p => /embedded/.test(p.textContent));"
                "var f=document.getElementById('appframe');"
                "return h && f ? (h.compareDocumentPosition(f) & 4) === 4 : null;}")
            assert order is True, "the frame's explanation is below the frame again"
            pg.close()
            b.close()
    finally:
        server.should_exit = True
        t.join(timeout=10)
