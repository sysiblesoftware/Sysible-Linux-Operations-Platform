"""An element with `hidden` is not on the screen.

Reported, of the Assign dialog: "Why are there 2 target fields?"

Because there were. The dialog has a picker for the fleet and, behind the
"Type a target instead…" option, a text field for a target the Controller has
not enrolled. The text field carries the `hidden` attribute and is revealed by
syncAssignPick().

`hidden` is only `display:none` from the USER AGENT stylesheet, so any explicit
display in the page's own CSS beats it — and this sheet has
`label.f{display:block}`. The fallback field was therefore on screen
permanently, beside the picker it is the fallback for: two controls, one label,
both reading Target.

This is the fourth element in this codebase to be hidden-but-shown by that exact
rule, which is why the fix is one global `[hidden]{display:none !important}`
rather than a fifth special case — and why this test sweeps EVERY hidden element
rather than the one that was reported.

Run: pytest baseline/tests/test_hidden_means_hidden.py
"""
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
PORT = 8888
OP = {"x-sysible-user": "alice", "x-sysible-role": "operator", "x-sysible-auth": SECRET}

FLEET = [{"id": "web-01", "label": "web-01", "environment": "Dev", "kind": "agent",
          "address": "10.0.0.11", "online": True}]


def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _playwright(), reason="playwright not installed")

# Every element the browser is still showing despite the attribute, with enough
# detail to say WHY — the computed display is the thing that beat it.
PROBE = """() => {
  const bad = [];
  document.querySelectorAll('[hidden]').forEach((e) => {
    const r = e.getBoundingClientRect();
    const cs = getComputedStyle(e);
    if (cs.display !== 'none' && (r.width > 0 || r.height > 0)) {
      bad.push((e.id || e.className || e.tagName) + ' {display:' + cs.display + '} '
               + Math.round(r.width) + 'x' + Math.round(r.height));
    }
  });
  return bad;
}"""


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import uvicorn
    tmp = tmp_path_factory.mktemp("hidden")
    os.environ.update(
        SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1", SYSIBLE_SSO_SHARED_SECRET=SECRET,
        SYSIBLE_BASELINE_AGENT_TOKEN="t" * 64, SYSIBLE_BASELINE_DATA=str(tmp))
    os.environ.pop("SYSIBLE_BASELINE_DB", None)
    import importlib
    import backend as _pkg
    names = ("backend", "backend.env", "backend.identity", "backend.store",
             "backend.app", "backend.controller", "backend.ui")
    attrs = ("env", "identity", "store", "app", "controller", "ui")
    saved_mods = {n: sys.modules[n] for n in names if n in sys.modules}
    saved_attrs = {a: getattr(_pkg, a) for a in attrs if hasattr(_pkg, a)}
    for n in names:
        sys.modules.pop(n, None)
    for a in saved_attrs:
        delattr(_pkg, a)

    m = importlib.import_module("backend.app")
    m.controller.list_targets = lambda who: (FLEET, ["Dev"], None)
    # The schema is created on app startup, not on import, so seeding before
    # the server exists hits an empty database.
    m.store.init_db()
    m.store.save_state("web-stack", "nginx:\n  pkg.installed: []\n", "alice",
                       source="yaml")

    srv = uvicorn.Server(uvicorn.Config(m.app, host="127.0.0.1", port=PORT,
                                        log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "baseline did not come up"
    try:
        yield f"http://127.0.0.1:{PORT}"
    finally:
        srv.should_exit = True
        t.join(timeout=10)
        for n in names:
            sys.modules.pop(n, None)
        sys.modules.update(saved_mods)
        for a, v in saved_attrs.items():
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
        ctx = b.new_context(viewport={"width": 1280, "height": 900},
                            extra_http_headers=OP)
        page = ctx.new_page()
        page.goto(server + "/", wait_until="domcontentloaded")
        page.wait_for_selector("#hosts .fleet", timeout=10000)
        page.wait_for_timeout(500)
        yield page
        b.close()


# ---- the reported symptom ---------------------------------------------------
def test_the_assign_dialog_offers_one_target_control_not_two(pg):
    pg.click("[data-assignhost]")
    pg.wait_for_selector("#dlg-assign[open]", timeout=5000)
    pg.wait_for_timeout(600)
    shown = pg.evaluate(
        """() => [...document.querySelectorAll('#dlg-assign label.f')]
             .filter(l => getComputedStyle(l).display !== 'none')
             .map(l => (l.querySelector('span')||{}).textContent)""")
    assert shown.count("Target") == 1, \
        f"the dialog shows {shown.count('Target')} Target fields: {shown}"


def test_the_typed_target_appears_only_when_it_is_chosen(pg):
    """It is not dead weight — a Controller that is down, or a host it has not
    enrolled, both still need assigning. It is a fallback, and a fallback that
    is always on screen is just a second field."""
    pg.click("[data-assignhost]")
    pg.wait_for_selector("#dlg-assign[open]", timeout=5000)
    pg.wait_for_timeout(600)
    assert pg.locator("#a-otherwrap").is_visible() is False

    pg.select_option("#a-pick", label="Type a target instead…")
    pg.wait_for_timeout(250)
    assert pg.locator("#a-otherwrap").is_visible() is True, \
        "choosing to type a target does not reveal the field to type it in"

    pg.select_option("#a-pick", value="web-01")
    pg.wait_for_timeout(250)
    assert pg.locator("#a-otherwrap").is_visible() is False, \
        "the fallback stays on screen after picking from the list"


# ---- and nothing else is hidden-but-shown -----------------------------------
def test_nothing_on_the_page_is_hidden_but_visible(pg):
    bad = pg.evaluate(PROBE)
    assert not bad, f"elements carrying `hidden` that the browser still shows: {bad}"


@pytest.mark.parametrize("dlg", ["dlg-state", "dlg-rule", "dlg-assign"])
def test_nothing_in_a_dialog_is_hidden_but_visible(pg, dlg):
    """Most of this console's conditional markup lives in its dialogs, which is
    also where nobody looks until a user reports two of something."""
    pg.evaluate(f"() => document.getElementById('{dlg}').showModal()")
    pg.wait_for_timeout(400)
    bad = pg.evaluate(PROBE)
    assert not bad, f"{dlg}: elements carrying `hidden` that are shown anyway: {bad}"


def test_the_guard_is_in_the_stylesheet_not_on_each_element(pg):
    """One rule, so the next element someone hides does not have to rediscover
    this. Four already have."""
    assert pg.evaluate(
        """() => { const p = document.createElement('p');
                   p.hidden = true; p.style.display = 'block';
                   document.body.appendChild(p);
                   const d = getComputedStyle(p).display; p.remove(); return d; }""") \
        == "none", "an explicit display still beats the hidden attribute"
