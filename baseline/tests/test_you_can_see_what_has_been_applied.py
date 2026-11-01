"""A host that ran an OLD version of a state is not "as declared".

Reported: "I can't tell what has been applied, what the state is etc. It's not
a great UI for visibility." Three things were missing, and two of them were
wrong rather than absent:

  * hostStateStatus ignored the version. A host that satisfied v1 of a state
    that is now v3 showed "as declared", in green — running configuration two
    edits old, reported as compliant. That is the most misleading thing a
    desired-state console can say, and the only version on the screen was the
    state's current one, so there was nothing to notice it by.
  * the state's host list used hostStatus(), the host's OVERALL status across
    every state it has. So a host showed "failing" beside a state that was fine
    on it, because some other state had failed there. stateStatus()'s own
    comment warns about exactly this roll-up and this list was still doing it.
  * nothing said WHEN. "failing" is a different sentence depending on whether it
    broke four minutes ago or two days ago, and neither was on the page.
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
PORT = 8885
OP = {"x-sysible-user": "alice", "x-sysible-role": "operator", "x-sysible-auth": SECRET}

FLEET = [
    # ran the current version, clean
    {"id": "cur-01", "label": "cur-01", "environment": "prod", "kind": "agent",
     "address": "10.0.0.1", "online": True},
    # ran v1 of a state that is now v2 — stale, and was shown green
    {"id": "old-01", "label": "old-01", "environment": "prod", "kind": "agent",
     "address": "10.0.0.2", "online": True},
    # the current version, but it had to change something
    {"id": "drift-01", "label": "drift-01", "environment": "prod", "kind": "agent",
     "address": "10.0.0.3", "online": True},
    # fine for THIS state, broken on another one
    {"id": "other-01", "label": "other-01", "environment": "prod", "kind": "agent",
     "address": "10.0.0.4", "online": True},
    # nothing has ever run
    {"id": "new-01", "label": "new-01", "environment": "prod", "kind": "agent",
     "address": "10.0.0.5", "online": True},
]


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
    tmp = tmp_path_factory.mktemp("visibility")
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
    m.controller.list_targets = lambda _i: (FLEET, ["prod"], None)
    m.controller.request_apply = lambda *a, **k: (True, "Applied on 1 host(s).")

    # ssh-servers is edited once, so it is on v2.
    m.store.save_state("ssh-servers", "openssh-server:\n  pkg.installed: []\n",
                       "alice", source="yaml")
    m.store.save_state("ssh-servers", "openssh-server:\n  pkg.latest: []\n",
                       "alice", source="yaml")
    m.store.save_state("motd", "/etc/motd:\n  file.managed: []\n", "alice", source="yaml")
    m.store.assign("env:prod", "ssh-servers")
    m.store.assign("other-01", "motd")

    db = sqlite3.connect(str(tmp / "baseline.db"))
    now = int(time.time())
    rows = [
        ("cur-01", "ssh-servers", 2, 1, 0, 0, None, now - 240),
        ("old-01", "ssh-servers", 1, 1, 0, 0, None, now - 3 * 86400),
        ("drift-01", "ssh-servers", 2, 1, 1, 0, None, now - 600),
        ("other-01", "ssh-servers", 2, 1, 0, 0, None, now - 300),
        ("other-01", "motd", 1, 0, 0, 1, "motd blew up", now - 120),
    ]
    for host, st, ver, ok, ch, fa, err, t in rows:
        db.execute(
            "INSERT INTO runs(host_id,state_name,state_version,preview,started,finished,"
            "ok,changed,failed,error,states_json,actions_json) "
            "VALUES(?,?,?,0,?,?,?,?,?,?,'[]','[]')",
            (host, st, ver, t, t, ok, ch, fa, err))
    db.commit()
    db.close()

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
    ctx = browser.new_context(viewport={"width": 1400, "height": 1000},
                              extra_http_headers=OP)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(server + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#browselist .it", timeout=8000)
    page.wait_for_timeout(400)
    yield page
    assert not errs, errs
    ctx.close()


def _status(pg, host, state):
    """What the page itself computes for this state on this host."""
    return pg.evaluate(
        "([h, s]) => hostStateStatus(STATE.hosts.find(x => x.id === h), s)",
        [host, state])


def _open_state(pg, name, host="new-01"):
    """Open a state, with `host` selected.

    The detail pane's host list is headed "Also on" and leaves OUT whichever
    host is selected — so the test has to know which one that is, or the host
    it is asking about is the one that is missing."""
    pg.click("#by-host")
    pg.wait_for_timeout(200)
    pg.locator(f"#browselist .it[data-host='{host}']").click()
    pg.wait_for_timeout(250)
    pg.click("#by-state")
    pg.wait_for_timeout(250)
    pg.locator(f"#browselist .it[data-state='{name}']").click()
    pg.wait_for_timeout(350)


# ---------------------------------------------------------------------------
# the version
# ---------------------------------------------------------------------------
def test_a_host_on_an_older_version_is_not_reported_as_compliant(pg):
    st = _status(pg, "old-01", "ssh-servers")
    assert st["label"] == "out of date", st
    assert st["cls"] == "amber", st
    assert "v1" in st["note"] and "v2" in st["note"], st["note"]


def test_a_host_on_the_current_version_still_reads_as_declared(pg):
    assert _status(pg, "cur-01", "ssh-servers")["label"] == "as declared"


def test_a_failure_outranks_being_out_of_date(pg):
    """Knowing it is broken matters more than knowing it is old."""
    assert _status(pg, "other-01", "motd")["label"] == "failing"


def test_drift_on_the_current_version_is_still_drift(pg):
    assert _status(pg, "drift-01", "ssh-servers")["label"] == "drifted"


def test_nothing_run_is_never_applied(pg):
    assert _status(pg, "new-01", "ssh-servers")["label"] == "never applied"


# ---------------------------------------------------------------------------
# this state, on that host — not the host in general
# ---------------------------------------------------------------------------
def test_the_host_list_reports_this_state_not_the_host(pg):
    """other-01 is fine on ssh-servers and failing on motd. On the ssh-servers
    page it must say so."""
    _open_state(pg, "ssh-servers")
    rows = pg.evaluate("""() => [...document.querySelectorAll('.sec .ln[data-pickhost]')]
      .map(e => ({host: (e.querySelector('.k')||{}).textContent.trim(),
                  said: (e.querySelector('.t')||{}).textContent.trim()}))""")
    by = {r["host"]: r["said"] for r in rows}
    assert by.get("other-01") == "as declared", by
    assert by.get("old-01") == "out of date", by
    assert by.get("cur-01") == "as declared", by


def test_a_host_that_never_ran_is_given_no_date(pg):
    """Inventing one would be worse than the blank."""
    _open_state(pg, "ssh-servers", host="cur-01")
    said = pg.evaluate("""() => [...document.querySelectorAll('.sec .ln[data-pickhost]')]
      .map(e => ({host: (e.querySelector('.k')||{}).textContent.trim(),
                  when: ((e.querySelector('.when')||{}).textContent || '').trim()}))""")
    by = {r["host"]: r["when"] for r in said}
    assert "new-01" in by, by
    assert by["new-01"] == "", by


def test_the_dot_beside_a_host_is_about_this_state(pg):
    """The middle column had the same roll-up: other-01 is failing on motd and
    fine on ssh-servers, and its dot on the ssh-servers page was red."""
    _open_state(pg, "ssh-servers", host="cur-01")
    dots = pg.evaluate("""() => [...document.querySelectorAll('#col2list .it')]
      .map(e => ({host: ((e.querySelector('.nm')||{}).textContent || '').trim(),
                  dot: ((e.querySelector('.dot')||{}).className || '')}))""")
    by = {d["host"]: d["dot"] for d in dots}
    assert by, "no hosts in the middle column"
    assert "err" not in by.get("other-01", ""), by


def test_the_roll_up_says_how_many_are_in_each_condition(pg):
    """"on 5 hosts · failing" says there is a problem and nothing about its
    size: one bad host out of five reads the same as five out of five."""
    _open_state(pg, "ssh-servers")
    tally = pg.text_content(".tally")
    assert "1 out of date" in tally, tally
    assert "1 drifted" in tally, tally
    assert "1 never applied" in tally, tally
    assert "2 as declared" in tally, tally


# ---------------------------------------------------------------------------
# when
# ---------------------------------------------------------------------------
def test_every_host_says_how_long_ago(pg):
    _open_state(pg, "ssh-servers")
    said = pg.evaluate("""() => [...document.querySelectorAll('.sec .ln[data-pickhost]')]
      .map(e => ({host: (e.querySelector('.k')||{}).textContent.trim(),
                  when: ((e.querySelector('.when')||{}).textContent || '').trim()}))""")
    by = {r["host"]: r["when"] for r in said}
    assert by.get("old-01") == "3d ago", by
    assert by.get("cur-01") in ("3m ago", "4m ago", "5m ago"), by


def test_a_hosts_own_page_says_when_each_state_last_ran(pg):
    pg.click("#by-host")
    pg.wait_for_timeout(250)
    pg.locator("#browselist .it[data-host='old-01']").click()
    pg.wait_for_timeout(350)
    txt = pg.text_content(".col:last-child")
    assert "out of date" in txt, txt[:400]
    assert "3d ago" in txt, txt[:400]
