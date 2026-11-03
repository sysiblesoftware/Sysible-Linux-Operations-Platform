"""A host with no Salt says so on its own page, and can be fixed from it.

REPORTED: "Salt isn't installed on baseline."

Baseline evaluates every declared state with `salt-call --local` ON THE HOST.
Salt is therefore a hard prerequisite of every host it manages, and the product
offered nothing: a fleet without it failed every apply at once, and the console
reported "salt-call did not return JSON". The operator was told the wrong thing
about the wrong layer, and then given no way to put it right.

THE SPLIT. Baseline recognises the condition; the CONTROLLER installs. Baseline
posts a host id and nothing else — no command, no package name, no shell. The
Controller holds the only authenticated root channel to the fleet and builds
every command it runs, so a Baseline that could post shell there would be a
second root on every managed host. That boundary is asserted here, because it
is the kind of thing a later convenience ("just let Baseline send the command")
quietly erases.
"""
import importlib
import os
import sqlite3
import sys
import threading
import time
import warnings

import pytest

import console

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

# THE REAL ONE, captured before anything stubs it.
#
# The server fixture replaces controller.request_install_salt on the module
# object so the browser tests do not need a Controller. That assignment outlives
# the fixture body, so the two unit tests at the bottom — which are about what
# the REAL client puts on the wire — were quietly calling the stub and passing
# for the wrong reason. Same trap as the fleet fixtures: a module attribute is
# process-global.
from backend import controller as _controller  # noqa: E402
_REAL_INSTALL = _controller.request_install_salt

SECRET = "s" * 32
PORT = 8894
OP = {"x-sysible-user": "alice", "x-sysible-role": "operator", "x-sysible-auth": SECRET}

NO_SALT = ("Salt is not installed on this host, so there is nothing to apply "
           "the state with. Baseline runs `salt-call --local` on each host it "
           "manages. Install it from the host's page, or install it yourself "
           "and apply again.")

FLEET = [
    {"id": "bare-01", "label": "bare-01", "environment": "Dev", "kind": "agent",
     "address": "10.0.0.1", "online": True},      # no Salt
    {"id": "ok-01", "label": "ok-01", "environment": "Dev", "kind": "agent",
     "address": "10.0.0.2", "online": True},      # fine
    {"id": "broke-01", "label": "broke-01", "environment": "Dev", "kind": "agent",
     "address": "10.0.0.3", "online": True},      # a real state failure
]

ASKED = []


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
    tmp = tmp_path_factory.mktemp("nosalt")
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
    _real = {k: getattr(m.controller, k)
             for k in ("list_targets", "request_install_salt")}
    m.controller.list_targets = lambda _i: (FLEET, ["Dev"], None)

    def _install(identity, host_id):
        ASKED.append(host_id)
        return True, f"Salt installed: salt-call 3006 ({host_id})"

    m.controller.request_install_salt = _install

    m.store.save_state("ssh servers", "openssh-server:\n  pkg.installed: []\n",
                       "alice", source="yaml")
    m.store.assign("env:Dev", "ssh servers")

    db = sqlite3.connect(str(tmp / "baseline.db"))
    now = int(time.time())
    for host, ok, ch, fa, err in (("bare-01", 0, 0, 0, NO_SALT),
                                  ("ok-01", 1, 0, 0, None),
                                  ("broke-01", 0, 0, 2, "two states failed")):
        db.execute(
            "INSERT INTO runs(host_id,state_name,state_version,preview,started,"
            "finished,ok,changed,failed,error,states_json,actions_json) "
            "VALUES(?,?,1,0,?,?,?,?,?,?,'[]','[]')",
            (host, "ssh servers", now, now, ok, ch, fa, err))
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
    ASKED.clear()
    ctx = browser.new_context(viewport={"width": 1500, "height": 950},
                              extra_http_headers=OP)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(server + "/", wait_until="domcontentloaded")
    console.ready(page, "hosts")
    yield page
    assert not errs, errs
    ctx.close()


# ---------------------------------------------------------------------------
# it says so, where you are looking
# ---------------------------------------------------------------------------
def test_the_host_says_it_has_no_salt(pg):
    console.open_host(pg, "bare-01")
    txt = console.detail(pg)
    assert "no Salt" in txt, txt[:400]
    assert "salt-call" in txt, "it does not say what Salt is needed FOR"
    assert "nothing else about this host is wrong" in txt.lower(), txt[:400]


def test_it_offers_to_install_it(pg):
    console.open_host(pg, "bare-01")
    assert pg.locator("[data-installsalt='bare-01']").count() == 1, \
        "a host that cannot run Salt is not offered Salt"


def test_a_host_that_is_merely_broken_is_not_offered_salt(pg):
    """broke-01 ran Salt perfectly well and two of its states failed. Offering
    to install Salt there sends the operator at the wrong problem."""
    console.open_host(pg, "broke-01")
    assert pg.locator("[data-installsalt]").count() == 0, console.detail(pg)[:300]
    assert "no Salt" not in console.detail(pg)


def test_a_healthy_host_is_not_offered_salt(pg):
    console.open_host(pg, "ok-01")
    assert pg.locator("[data-installsalt]").count() == 0


# ---------------------------------------------------------------------------
# ...and the button works
# ---------------------------------------------------------------------------
def test_pressing_it_asks_the_controller_for_that_host(pg):
    console.open_host(pg, "bare-01")
    pg.click("[data-installsalt='bare-01']")
    pg.wait_for_timeout(900)
    assert ASKED == ["bare-01"], ASKED
    assert "installed" in (pg.text_content("#msg") or "").lower(), \
        pg.text_content("#msg")


def test_it_says_it_is_working_rather_than_looking_dead(pg):
    """On a host with a cold package cache this is minutes. A button that does
    nothing visible for four minutes gets pressed again."""
    console.open_host(pg, "bare-01")
    btn = pg.locator("[data-installsalt='bare-01']")
    assert "Install Salt" in btn.inner_text()
    pg.click("[data-installsalt='bare-01']")
    pg.wait_for_timeout(120)
    assert "Installing" in (pg.text_content("#msg") or "") \
        or "Installing" in (pg.content() or ""), pg.text_content("#msg")


def test_an_auditor_is_not_offered_a_button_that_will_be_refused(browser, server):
    ctx = browser.new_context(extra_http_headers={
        "x-sysible-user": "bob", "x-sysible-role": "auditor",
        "x-sysible-auth": SECRET})
    page = ctx.new_page()
    try:
        page.goto(server + "/", wait_until="domcontentloaded")
        console.ready(page, "hosts")
        console.open_host(page, "bare-01")
        assert "no Salt" in console.detail(page), "an auditor is not told why"
        assert page.locator("[data-installsalt]").count() == 0, \
            "an auditor is offered a button the server will refuse"
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# the boundary
# ---------------------------------------------------------------------------
def test_baseline_sends_a_host_id_and_never_a_command():
    """THE RULE. The Controller runs as root on every host through the only
    authenticated channel in the platform, and builds every command it runs. A
    Baseline that could post shell there would be a second root on the fleet.

    Asserted on the real client, with a stubbed transport, so the request that
    actually goes out is the one examined.
    """
    c = _controller
    sent = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "detail": "Salt installed."}

    class _Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            sent.update(url=url, json=json, headers=headers)
            return _Resp()

    real_httpx, real_secret = c.httpx, c._SSO_SECRET
    try:
        c.httpx = type("x", (), {"Client": _Client})
        c._SSO_SECRET = c._SSO_SECRET or SECRET
        who = type("w", (), {"user": "alice", "role": "operator"})()
        ok, msg = _REAL_INSTALL(who, "bare-01")
    finally:
        c.httpx, c._SSO_SECRET = real_httpx, real_secret
    assert ok, msg
    body = sent["json"] or {}
    blob = repr(body) + sent["url"]
    for word in ("salt-call", "apt-get", "dnf", "install -y", "command", "PKGMGR"):
        assert word not in blob, f"Baseline put {word!r} in the request: {blob}"
    assert "bare-01" in sent["url"]


def test_an_environment_is_refused_rather_than_timing_out():
    """"Install Salt on all sixteen" is a reasonable thing to want and a
    different thing to build — it needs per-host reporting, not one timeout
    covering sixteen package managers."""
    ok, msg = _REAL_INSTALL(
        type("w", (), {"user": "a", "role": "operator"})(), "env:Dev")
    assert not ok
    assert "one host at a time" in msg.lower(), msg
