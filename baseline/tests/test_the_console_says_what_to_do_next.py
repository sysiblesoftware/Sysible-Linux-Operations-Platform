"""A console that teaches, on a fleet that is half set up.

Two complaints, one screen: "when I look at the UI I don't immediately know
what to do", and "it doesn't give very good visibility to the states or how to
build the states".

Day one was answered (test_day_one_shows_the_way_in). The moment ONE state
exists that panel disappears and the Overview goes back to listing problems —
so a fleet with a state declared and assigned to nothing, or sixteen hosts
with nothing on them, reads as "every managed host is as declared". Nothing is
drifting because nothing is being checked, and that is the most misleading
sentence this screen can show.

And the States list carried a name, a version and a verdict: three facts about
a declaration and none about what it declares. "Does web-stack install nginx?"
could only be answered by opening every state in turn.

Covered here:
  * the Overview names the earliest unfinished step and offers the way to it;
  * it says ONE, not a backlog;
  * the States list says what each state declares, hand-written ones included;
  * a new state opens on starting points, not an empty list;
  * picking one fills the builder with items the builder understands;
  * an EXISTING state is not offered them, because replacing its items is not
    a starting point.

Run: pytest baseline/tests/test_the_console_says_what_to_do_next.py
"""
import json
import os
import re
import sys
import threading
import time
import urllib.request
import warnings

import pytest

import console

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

SECRET = "s" * 32
PORT = 8893
OP = {"x-sysible-user": "alice", "x-sysible-role": "operator", "x-sysible-auth": SECRET}

FLEET = [{"id": f"web-{i:02d}", "label": f"web-{i:02d}", "environment": "Prod",
          "kind": "agent", "address": f"10.0.0.{i}", "online": True}
         for i in range(1, 5)]


def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _playwright(), reason="playwright not installed")


def _post(base, path, body):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={**OP, "content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import importlib

    import uvicorn

    import backend as _pkg
    tmp = tmp_path_factory.mktemp("next")
    os.environ.update(
        SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1", SYSIBLE_SSO_SHARED_SECRET=SECRET,
        SYSIBLE_BASELINE_AGENT_TOKEN="t" * 64, SYSIBLE_BASELINE_DATA=str(tmp))
    os.environ.pop("SYSIBLE_BASELINE_DB", None)
    # Snapshot / purge / restore, for the reason test_day_one documents at
    # length: `backend.app` stays bound on the package and another file's
    # database would answer every assertion here.
    _names = ("backend", "backend.env", "backend.identity", "backend.store",
              "backend.app", "backend.controller", "backend.ui", "backend.build")
    _attrs = ("env", "identity", "store", "app", "controller", "ui", "build")
    _saved_mods = {n: sys.modules[n] for n in _names if n in sys.modules}
    _saved_attrs = {a: getattr(_pkg, a) for a in _attrs if hasattr(_pkg, a)}
    for n in _names:
        sys.modules.pop(n, None)
    for a in _saved_attrs:
        delattr(_pkg, a)

    m = importlib.import_module("backend.app")
    m.controller.list_targets = lambda who: (FLEET, ["Prod"], None)

    srv = uvicorn.Server(uvicorn.Config(m.app, host="127.0.0.1", port=PORT,
                                        log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "baseline did not come up"
    base = f"http://127.0.0.1:{PORT}"

    # The half-configured fleet: two states declared, NEITHER assigned.
    _post(base, "/api/states/web-stack", {"source": "builder", "items": [
        {"kind": "package", "names": ["nginx", "curl", "git"]},
        {"kind": "service", "name": "nginx", "enable": True},
        {"kind": "dir", "path": "/srv/www"}]})
    # One written by hand, because a summary built at save time would be right
    # for the form and absent for every state typed as YAML.
    _post(base, "/api/states/banner", {"source": "yaml", "sls":
        "motd:\n  file.managed:\n    - name: /etc/motd\n"
        "    - contents: hello\nissue:\n  file.managed:\n    - name: /etc/issue\n"
        "    - contents: hi\n"})
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
        ctx = b.new_context(viewport={"width": 1400, "height": 1000},
                            extra_http_headers=OP)
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(server + "/", wait_until="domcontentloaded")
        page.wait_for_selector("#view", timeout=10000)
        page.wait_for_timeout(500)
        yield page
        assert not errs, errs
        b.close()


def _flat(pg, sel):
    return re.sub(r"\s+", " ", pg.text_content(sel) or "").strip()


# ---------------------------------------------------------------------------
# the Overview says what to do
# ---------------------------------------------------------------------------
def test_a_half_configured_fleet_is_not_called_all_clear(pg):
    """Nothing is drifting because nothing is being checked. Saying "every
    managed host is as declared" and stopping is the misleading version."""
    console.goto(pg, "overview")
    assert pg.locator("#view .next").count() == 1, \
        "the Overview offers no next step on a fleet that is half set up"
    body = _flat(pg, "#view .next").lower()
    assert "assigned to nothing" in body, body
    assert "declaring a state does not apply it" in body, \
        "it names the problem without saying why it matters"


def test_it_names_one_step_not_a_backlog(pg):
    """A list of five suggestions on the first screen is a backlog, and a
    backlog is the thing a new operator scrolls past."""
    console.goto(pg, "overview")
    assert pg.locator("#view .next").count() == 1
    assert pg.locator("#view .next .nw").count() == 1


def test_the_step_offers_the_way_to_it(pg):
    """Naming a problem and leaving the operator to find the screen is half
    the help."""
    console.goto(pg, "overview")
    btn = pg.locator("#view .next button")
    assert btn.count() == 1, "the next step has no way to act on it"
    assert "assign" in btn.inner_text().lower(), btn.inner_text()


# ---------------------------------------------------------------------------
# the States list says what a state contains
# ---------------------------------------------------------------------------
def test_the_states_list_says_what_each_state_declares(pg):
    console.goto(pg, "states")
    pg.wait_for_selector(".grid .r", timeout=8000)
    head = _flat(pg, "#view .hd")
    assert "Declares" in head, head
    row = _flat(pg, ".grid .r[data-state='web-stack']")
    assert "3 packages" in row, row
    assert "1 service" in row, row
    assert "1 directory" in row, row


def test_a_hand_written_state_is_summarised_too(pg):
    """A summary kept at save time would be right for the builder and absent
    for every state typed as YAML — which is a first-class way in here."""
    console.goto(pg, "states")
    pg.wait_for_selector(".grid .r", timeout=8000)
    row = _flat(pg, ".grid .r[data-state='banner']")
    assert "2 files" in row, row


def test_a_bundled_package_block_counts_the_packages(pg):
    """One pkg.installed with three names declares three packages. Counting
    blocks made "12 packages" read as "1" on exactly the states that bundle
    the most."""
    console.goto(pg, "states")
    pg.wait_for_selector(".grid .r", timeout=8000)
    row = _flat(pg, ".grid .r[data-state='web-stack']")
    assert "1 package " not in row and not row.endswith("1 package"), row


# ---------------------------------------------------------------------------
# a new state has somewhere to start
# ---------------------------------------------------------------------------
def test_a_new_state_opens_on_starting_points(pg):
    console.goto(pg, "states")
    pg.click("#newstate")
    pg.wait_for_selector("#dlg-state[open]", timeout=5000)
    pg.wait_for_selector("#s-tpls .tpl", timeout=5000)
    assert not pg.locator("#s-start").is_hidden(), \
        "a new state still opens on an empty list"
    cards = pg.locator("#s-tpls .tpl")
    assert cards.count() >= 4, f"only {cards.count()} starting points"
    strip = _flat(pg, "#s-start").lower()
    assert "build your own" in strip, \
        "the templates read as the only way, not as a starting point"
    # Each card says what it is FOR and what it holds, or it is just a word.
    first = _flat(pg, "#s-tpls .tpl")
    assert len(first) > 20, first


def test_picking_one_fills_the_builder(pg):
    console.goto(pg, "states")
    pg.click("#newstate")
    pg.wait_for_selector("#s-tpls .tpl", timeout=5000)
    pg.locator("#s-tpls .tpl").first.click()
    pg.wait_for_selector("#s-items .item", timeout=5000)
    assert pg.locator("#s-items .item").count() >= 2, \
        "the template filled nothing into the builder"
    assert pg.input_value("#s-name").strip(), "it suggested no name"
    # ...and the strip goes, or a second click silently throws the first away.
    assert pg.locator("#s-start").is_hidden(), \
        "the starting points are still offered after one was used"


def test_a_template_does_not_overwrite_a_name_already_typed(pg):
    console.goto(pg, "states")
    pg.click("#newstate")
    pg.wait_for_selector("#s-tpls .tpl", timeout=5000)
    pg.fill("#s-name", "my-own-name")
    pg.locator("#s-tpls .tpl").first.click()
    pg.wait_for_selector("#s-items .item", timeout=5000)
    assert pg.input_value("#s-name") == "my-own-name", \
        "it replaced a name the operator had already chosen"


def test_an_existing_state_is_not_offered_a_starting_point(pg):
    """Replacing what a state already declares is not a starting point, it is
    throwing away what is there."""
    console.open_state(pg, "web-stack")
    pg.click("[data-edit='web-stack']")
    pg.wait_for_selector("#dlg-state[open]", timeout=5000)
    pg.wait_for_timeout(300)
    assert pg.locator("#s-start").is_hidden(), \
        "editing an existing state offers to replace its items wholesale"


def test_the_template_items_are_ones_the_builder_understands(pg):
    """Generated through the real emitters server-side, so a card cannot teach
    a shape the builder would not produce. Proven by saving one."""
    console.goto(pg, "states")
    pg.click("#newstate")
    pg.wait_for_selector("#s-tpls .tpl", timeout=5000)
    pg.locator("#s-tpls .tpl").first.click()
    pg.wait_for_selector("#s-items .item", timeout=5000)
    pg.fill("#s-name", "from-a-template")
    pg.click("#s-save")
    pg.wait_for_timeout(900)
    console.goto(pg, "states")
    pg.wait_for_selector(".grid .r", timeout=8000)
    row = pg.locator(".grid .r[data-state='from-a-template']")
    assert row.count() == 1, "the state a template produced would not save"
    assert _flat(pg, ".grid .r[data-state='from-a-template']").strip(), "it saved empty"


def test_an_absence_of_verdicts_is_not_a_good_verdict(pg):
    """"Every managed host is as declared" is vacuously true when nothing is
    managed — and it rendered directly under a panel saying nothing is being
    checked. The console arguing with itself, on its first screen."""
    console.goto(pg, "overview")
    body = _flat(pg, "#view")
    assert "Every managed host is as declared" not in body, body
    assert "nothing to check" in body, body


def test_no_column_heading_runs_into_the_next(pg):
    """The Declares column was added with a 4em Version track, and VERSION is a
    seven-letter uppercase heading with letter-spacing: it overflowed and the
    header read "VERSIONDECLARES"."""
    console.goto(pg, "states")
    pg.wait_for_selector(".grid .r", timeout=8000)
    boxes = pg.eval_on_selector_all(
        "#view .hd span",
        "els => els.map(e => { const r = e.getBoundingClientRect();"
        "  return {t: e.textContent.trim(), l: r.left, r: r.right, w: e.scrollWidth,"
        "          cw: Math.ceil(r.width)}; })")
    assert len(boxes) >= 5, boxes
    for b in boxes:
        assert b["w"] <= b["cw"] + 1, \
            f"the {b['t']!r} heading overflows its column ({b['w']}px in {b['cw']}px)"
    for a, b in zip(boxes, boxes[1:]):
        assert a["r"] <= b["l"] + 1, f"{a['t']!r} runs into {b['t']!r}"


def test_a_row_does_not_say_the_same_thing_twice(pg):
    """"no host — declaring is not assigning" truncated to "no host — declaring
    is ..." in a 10em column, next to a Worst column already reading "not
    assigned"."""
    console.goto(pg, "states")
    pg.wait_for_selector(".grid .r", timeout=8000)
    row = _flat(pg, ".grid .r[data-state='web-stack']")
    assert "declaring is not assigning" not in row, row
    cells = pg.eval_on_selector_all(
        ".grid .r[data-state='web-stack'] .c",
        "els => els.map(e => ({w: e.scrollWidth, cw: Math.ceil(e.getBoundingClientRect().width)}))")
    for c in cells:
        assert c["w"] <= c["cw"] + 1, f"a cell is truncated: {c}"
