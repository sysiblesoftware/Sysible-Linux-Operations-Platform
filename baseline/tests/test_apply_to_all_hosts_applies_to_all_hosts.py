"""Apply has to reach every host, and has to show what it did.

Reported: "the apply to all 16 hosts in baseline doesn't do anything", and
then "I can't tell what has been applied, what the state is etc. It's not a
great UI for visibility."

THE BUG. applyEverywhere looped the ASSIGNMENT TARGETS. A state assigned to an
environment has ONE target — `env:production` — so sixteen hosts became one
request, and the Controller's _run_baseline_cycle expands that and runs all
sixteen hosts' salt-calls SYNCHRONOUSLY inside it, at up to 60s each by its own
task budget. Baseline gave up on that call after 20 seconds and reported "the
Controller is unreachable", in a bar at the top of a page whose content is in
three independently scrolling columns, while the applies carried on running
unobserved. From the outside: nothing happened.

One request per host now. Each is one host's work and fits in a timeout, each
reports separately, a host assigned the state both directly AND through its
environment is applied once rather than twice, and "16 hosts" counts what was
actually asked for.

THE VISIBILITY. An apply was a toast that hid itself after four seconds, and
then a page that looked exactly as it had. There is a panel now, above the
columns because it is about the fleet rather than the selection, listing every
host and what came back, and it stays until it is dismissed.
"""
import importlib
import os
import sqlite3
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

# Twelve in production, one in staging. ssh-servers is assigned to the
# environment AND directly to web-01 — the shape that double-applies if the
# targets are not de-duplicated into hosts.
FLEET = [{"id": f"web-{i:02d}", "label": f"web-{i:02d}", "environment": "production",
          "kind": "agent", "address": f"10.0.0.{i}", "online": True}
         for i in range(1, 13)]
FLEET.append({"id": "stg-01", "label": "stg-01", "environment": "staging",
              "kind": "agent", "address": "10.0.5.1", "online": True})


def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _playwright(), reason="playwright not installed")

ASKED = []
FAIL_ON = set()
# One slot. Set it to make every failing host fail with the SAME sentence —
# which is what a fleet-wide cause does, and the case the panel got wrong.
FAIL_MSG = []


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import uvicorn
    tmp = tmp_path_factory.mktemp("applyall")
    os.environ.update(
        SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1", SYSIBLE_SSO_SHARED_SECRET=SECRET,
        SYSIBLE_BASELINE_GATEWAY_SECRET=SECRET,
        SYSIBLE_BASELINE_AGENT_TOKEN="t" * 64, SYSIBLE_BASELINE_DATA=str(tmp))
    os.environ.pop("SYSIBLE_BASELINE_DB", None)
    for mod in ("backend.env", "backend.identity", "backend.store", "backend.app"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    import backend.app as m
    m = importlib.reload(m)
    m.store.init_db()
    # SAVED and restored below. m.controller is one module object for the whole
    # pytest process, so a plain assignment outlives this file — and this one
    # sorts early, so every later console test saw this fleet instead of its
    # own. The repo has hit that before; it is why the fixtures elsewhere say
    # "monkeypatch, NOT assignment".
    _real = {k: getattr(m.controller, k) for k in ("list_targets", "request_apply")}
    m.controller.list_targets = lambda _i: (FLEET, ["production", "staging"], None)

    def _apply(identity, target, preview=False):
        ASKED.append((target, bool(preview)))
        if target in FAIL_ON:
            return False, FAIL_MSG[0] if FAIL_MSG else f"{target} refused the task."
        return True, "Applied on 1 host(s)."

    m.controller.request_apply = _apply

    m.store.save_state("ssh-servers", "openssh-server:\n  pkg.installed: []\n",
                       "alice", source="yaml")
    m.store.assign("env:production", "ssh-servers")
    m.store.assign("web-01", "ssh-servers")       # also directly
    m.store.assign("stg-01", "ssh-servers")

    cfg = uvicorn.Config(m.app, host="127.0.0.1", port=PORT, log_level="error")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "baseline did not come up"
    yield f"http://127.0.0.1:{PORT}", tmp
    for k, v in _real.items():
        setattr(m.controller, k, v)
    srv.should_exit = True
    t.join(timeout=10)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    chrome = os.environ.get("SYSIBLE_TEST_CHROMIUM")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}),
                                  args=["--no-sandbox"])
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no chromium to drive ({e})")
        yield b
        b.close()


@pytest.fixture
def pg(browser, server):
    ASKED.clear()
    FAIL_ON.clear()
    FAIL_MSG.clear()
    ctx = browser.new_context(viewport={"width": 1400, "height": 1000},
                              extra_http_headers=OP)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    page.goto(server[0] + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#browselist .it", timeout=8000)
    page.click("#by-state")
    page.wait_for_timeout(300)
    page.locator("#browselist .it").first.click()
    page.wait_for_timeout(400)
    yield page
    assert not errs, errs
    ctx.close()


def _panel(pg):
    return pg.evaluate("""() => {
      const p = document.getElementById('applypanel');
      if(!p || p.hidden) return null;
      return {
        title: (p.querySelector('.ah b')||{}).textContent || '',
        head:  (p.querySelector('.ah .faint')||{}).textContent || '',
        rows: [...p.querySelectorAll('.applyhosts .h')].map(h => {
          const nm = h.querySelector('.nm');
          return {
            name: nm ? nm.textContent : '',
            result: (h.querySelector('.r')||{}).textContent || '',
            cls: h.className,
            // Is the name itself cut off? scrollWidth is what the text needs,
            // clientWidth is what the box gives it.
            clipped: nm ? nm.scrollWidth > nm.clientWidth + 1 : false,
            shown: nm ? Math.round(nm.clientWidth) : 0,
          };
        }),
        // One entry per distinct reason: the message, and the hosts it hit.
        whys: [...p.querySelectorAll('.applywhy .w')].map(w => ({
          msg: (w.querySelector('.msg')||{}).textContent || '',
          on: (w.querySelector('.on')||{}).textContent || '',
        })),
      };
    }""")


def _apply_all(pg):
    pg.locator("button[data-applyall]").first.click()
    pg.wait_for_function(
        "() => {const p=document.getElementById('applypanel');"
        " return p && !p.hidden && !/running|waiting/.test(p.innerText);}",
        timeout=20000)
    pg.wait_for_timeout(300)


# ---------------------------------------------------------------------------
# the bug
# ---------------------------------------------------------------------------
def test_it_asks_for_every_host_not_for_the_assignment(pg):
    assert pg.locator("button[data-applyall]").first.inner_text() == "Apply to all 13 hosts"
    _apply_all(pg)
    targets = [t for t, _p in ASKED]
    assert "env:production" not in targets, (
        "still sending one request for the environment — the Controller runs all "
        "of them inside it and this service times out at 20s")
    assert sorted(targets) == sorted(h["id"] for h in FLEET), targets


def test_a_host_assigned_twice_is_applied_once(pg):
    """web-01 has the state directly AND through production."""
    _apply_all(pg)
    targets = [t for t, _p in ASKED]
    assert targets.count("web-01") == 1, targets


def test_the_count_on_the_button_is_what_gets_asked(pg):
    label = pg.locator("button[data-applyall]").first.inner_text()
    n = int("".join(c for c in label if c.isdigit()))
    _apply_all(pg)
    assert len(ASKED) == n, f"button said {n}, asked for {len(ASKED)}"


def test_preview_does_not_apply(pg):
    pg.locator("button[data-previewall]").first.click()
    pg.wait_for_function(
        "() => {const p=document.getElementById('applypanel');"
        " return p && !p.hidden && !/running|waiting/.test(p.innerText);}",
        timeout=20000)
    assert ASKED and all(preview for _t, preview in ASKED), ASKED


# ---------------------------------------------------------------------------
# what you can see while it happens
# ---------------------------------------------------------------------------
def test_the_panel_names_every_host_and_what_came_back(pg):
    _apply_all(pg)
    p = _panel(pg)
    assert p, "no apply panel at all"
    assert "ssh-servers" in p["title"], p["title"]
    assert len(p["rows"]) == len(FLEET), p["rows"]
    assert {r["name"] for r in p["rows"]} == {h["id"] for h in FLEET}
    assert all(r["result"] == "done" for r in p["rows"]), p["rows"]


def test_one_host_failing_is_reported_against_that_host(pg):
    FAIL_ON.add("web-05")
    _apply_all(pg)
    p = _panel(pg)
    bad = [r for r in p["rows"] if r["name"] == "web-05"]
    assert bad and bad[0]["result"] == "failed", p["rows"]
    # The reason is below the list now rather than inside web-05's row, and it
    # has to still say which host it was about.
    assert len(p["whys"]) == 1, p["whys"]
    assert "refused the task" in p["whys"][0]["msg"], p["whys"]
    assert p["whys"][0]["on"].strip() == "web-05", p["whys"]
    # …and the other twelve still went.
    assert len(ASKED) == len(FLEET), ASKED
    assert sum(1 for r in p["rows"] if r["result"] == "done") == len(FLEET) - 1


# ---------------------------------------------------------------------------
# what the operator saw: sixteen copies of one sentence, and no hostnames
# ---------------------------------------------------------------------------
# Reported with a screenshot: every host failing with "SYSIBLE_BASELINE_URL and
# SYSIBLE_BASELINE_AGENT_TOKEN unset", sixteen times over, and the hostnames
# squeezed down to two characters and an ellipsis.
UNSET = ("This controller is not wired to Sysible Baseline (SYSIBLE_BASELINE_URL "
         "and SYSIBLE_BASELINE_AGENT_TOKEN unset), so nothing can be applied. "
         "These are written into the controller's .env by the SLOP installer, "
         "and updating the platform does not re-run it.")


def test_one_reason_shared_by_the_whole_fleet_is_stated_once(pg):
    FAIL_MSG.append(UNSET)
    FAIL_ON.update(h["id"] for h in FLEET)
    _apply_all(pg)
    p = _panel(pg)
    assert len(p["rows"]) == len(FLEET), p["rows"]
    assert all(r["result"] == "failed" for r in p["rows"]), p["rows"]
    # ONE entry, not thirteen.
    assert len(p["whys"]) == 1, [w["msg"][:40] for w in p["whys"]]
    assert "SYSIBLE_BASELINE_URL" in p["whys"][0]["msg"]
    # …and it says it was all of them rather than listing every name.
    assert p["whys"][0]["on"].strip() == f"all {len(FLEET)} hosts", p["whys"][0]
    # The sentence appears once in the panel's text, not once per host.
    txt = pg.inner_text("#applypanel")
    assert txt.count("SYSIBLE_BASELINE_URL") == 1, txt.count("SYSIBLE_BASELINE_URL")


def test_the_hostnames_stay_readable_when_every_host_fails(pg):
    """THE REGRESSION. The reason used to be a third flex item in the host's own
    row; a sentence that long took the width and the name — which ellipsises —
    shrank below its own content. Thirteen rows of "we…" is a panel that names
    nothing."""
    FAIL_MSG.append(UNSET)
    FAIL_ON.update(h["id"] for h in FLEET)
    _apply_all(pg)
    p = _panel(pg)
    clipped = [r for r in p["rows"] if r["clipped"]]
    assert not clipped, clipped
    # And every name is really in the panel's text, not an ellipsis of one.
    txt = pg.inner_text("#applypanel")
    missing = [h["id"] for h in FLEET if h["id"] not in txt]
    assert not missing, missing


def test_two_different_reasons_stay_two_entries(pg):
    """Grouping must not merge distinct causes — two things wrong is two things
    to fix, and the hosts have to be attributed to the right one."""
    FAIL_ON.update({"web-02", "web-03"})
    _apply_all(pg)
    p = _panel(pg)
    assert len(p["whys"]) == 2, p["whys"]
    hosts = {w["on"].strip() for w in p["whys"]}
    assert hosts == {"web-02", "web-03"}, p["whys"]


def test_the_head_counts_the_failures(pg):
    FAIL_ON.update({"web-02", "web-03"})
    _apply_all(pg)
    assert "2 failed" in _panel(pg)["head"], _panel(pg)["head"]


def test_the_panel_stays_until_it_is_dismissed(pg):
    _apply_all(pg)
    pg.wait_for_timeout(5000)        # longer than the old toast's four seconds
    assert _panel(pg), "the record of the apply vanished on its own"
    pg.click("#applypanel .x")
    pg.wait_for_timeout(200)
    assert _panel(pg) is None


def test_applying_one_host_uses_the_same_panel(pg):
    """One host and sixteen should not be two different experiences."""
    pg.locator("button[data-apply]").first.click()
    pg.wait_for_function(
        "() => {const p=document.getElementById('applypanel');"
        " return p && !p.hidden && !/running|waiting/.test(p.innerText);}",
        timeout=20000)
    p = _panel(pg)
    assert p and len(p["rows"]) == 1, p
    assert len(ASKED) == 1, ASKED


def test_the_columns_are_not_pushed_off_the_screen(pg):
    """The grid used to claim calc(100vh - 51px), so anything above it — the
    message bar, and now this panel — pushed the bottom of every column out of
    the window."""
    before = pg.evaluate("() => document.querySelector('.wrap').getBoundingClientRect().bottom")
    _apply_all(pg)
    after = pg.evaluate("""() => {
      const r = document.querySelector('.wrap').getBoundingClientRect();
      return {bottom: r.bottom, h: innerHeight};
    }""")
    assert after["bottom"] <= after["h"] + 1, (before, after)
