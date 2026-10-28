"""Watching an update happen, including the part where the console disappears.

Reported: "I want a better visualization of the updates through Administration.
Just saying reload the page after a few moments isn't acceptable."

It was not. THE UPDATER IS PART OF THE STACK IT UPDATES: `compose up -d --build`
on the SLOP project recreates the container serving this page, so the connection
drops every single time, by design. The page treated that as the end of the
story — it stopped polling and put up a sticky

    SLOP is rebuilding and will restart — reload this page in a moment.

which is the one moment an operator most wants to be told what is happening, and
the moment the screen gave up and handed the job back to them.

There was an answer to wait for the whole time. A self-affecting job runs in a
detached helper container that compose never touches, so the work, its log and
its exit code all survive the restart. Nothing was polling for them.

These tests drive the real page in a real browser and take the service away
mid-job, which is the only way to check any of this: every one of them passes
against a page that renders perfectly and gives up the moment it is tested.

Run: pytest idp/tests/test_an_update_is_watchable.py
"""
import importlib
import json
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
PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"

APPS = [
    {"key": "controller", "label": "Sysible Controller", "installed": True,
     "checked": True, "available": True, "current": "c0689c8", "latest": "aa11bb2",
     "can_update": True, "actions": ["restart"],
     "services": [{"service": "controller", "state": "running"}]},
    {"key": "slop", "label": "Sysible Linux Operations Platform", "installed": True,
     "checked": True, "available": True, "current": "538a889", "latest": "359854c",
     "can_update": True, "actions": ["restart", "start", "recreate"],
     "services": [{"service": "idp", "state": "running"}]},
]


def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


class Script:
    """The job endpoint, driven from the test rather than from a real updater."""

    def __init__(self):
        self.frames = []
        self.at = 0
        self.asked = 0

    def add(self, **job):
        base = {"app": "slop", "state": "running", "started": time.time() - 30,
                "finished": None, "message": "", "log": [], "self_update": True,
                "step": "pull"}
        base.update(job)
        self.frames.append(base)
        return self

    def __call__(self, *a, **k):
        self.asked += 1
        i = min(self.at, len(self.frames) - 1)
        self.at += 1
        return {"job": self.frames[i]}, None


@pytest.fixture()
def live(tmp_path, monkeypatch):
    """The real IdP, served, with the updater stubbed. Yields (module, script)."""
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
    script = Script()
    # monkeypatch, NOT assignment. `m.updates` is the one shared module object
    # for the whole pytest process, so a plain `m.updates.apply = ...` outlives
    # this test and every later one sees the stub — which is exactly how this
    # file first broke two unrelated tests in idp/tests/test_idp.py.
    monkeypatch.setattr(m.updates, "configured", lambda: True)
    monkeypatch.setattr(m.updates, "status",
                        lambda *a, **k: ({"apps": APPS, "job": None}, None))
    monkeypatch.setattr(m.updates, "job", script)
    monkeypatch.setattr(m.updates, "apply",
                        lambda key, *a, **k: ({"message": f"Updating {key}…"}, None))

    cfg = uvicorn.Config(m.app, host="127.0.0.1", port=PORT, log_level="error")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(150):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "test server did not come up"
    try:
        yield m, script
    finally:
        server.should_exit = True
        time.sleep(0.4)


def _open(pw, script_unused=None):
    chrome = os.environ.get("SYSIBLE_TEST_CHROMIUM")
    try:
        b = pw.chromium.launch(**({"executable_path": chrome} if chrome else {}))
    except Exception as e:
        pytest.skip(f"no chromium to drive ({e})")
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    pg.on("dialog", lambda d: d.accept())
    pg.goto(f"{BASE}/login", wait_until="domcontentloaded")
    pg.fill("input[name=username]", "admin")
    pg.fill("input[name=password]", ADMIN_PW)
    pg.click("form button[type=submit]")
    pg.wait_for_url(re.compile(r".*/(admin|)$"), timeout=10000)
    pg.goto(f"{BASE}/admin/updates", wait_until="domcontentloaded")
    pg.wait_for_selector("#b-slop", state="attached", timeout=10000)
    return b, pg


def _panel(pg):
    return pg.evaluate("""() => {
      const p = document.getElementById('jobpanel');
      if (!p || p.hidden) return null;
      const steps = [...document.querySelectorAll('#jobsteps .job-step')]
        .map(s => ({label: s.textContent, cls: s.className}));
      return {
        title: (document.getElementById('jobtitle')||{}).textContent || '',
        count: (document.getElementById('jobcount')||{}).textContent || '',
        time:  (document.getElementById('jobtime')||{}).textContent || '',
        say:   (document.getElementById('jobsay')||{}).textContent || '',
        done:  [...document.querySelectorAll('#jobdone .d')].map(d => d.textContent),
        steps: steps,
        logHidden: (document.getElementById('joblog')||{}).hidden,
        logBtn: (document.getElementById('joblogtoggle')||{}).textContent || '',
      };
    }""")


# ---- the reported complaint -------------------------------------------------
def test_it_never_tells_the_operator_to_reload_the_page(live):
    """The whole complaint, in one line.

    Anchored on the CALL, not the words. The comment above the fix quotes the
    old message to explain what was wrong with it, so a plain search for the
    sentence matches the explanation and passes on a page that still says it —
    the fifth assertion in this repo to be satisfied by its own prose.
    """
    src = live[0]._UPDATES_JS
    # A regex over the CALL, in either quoting. Anchoring on the exact literal
    # "pushToast('SLOP is rebuilding" let a re-break using double quotes sail
    # straight past this test while restoring the behaviour it exists to stop.
    offending = re.search(r"""pushToast\(\s*['"][^'"]*reload this page""", src)
    assert not offending, \
        f"the page still hands the job back to the operator: {offending.group(0)!r}"
    # And nothing else on the page asks for one either.
    for bad in ("reload this page", "refresh this page", "reload the page"):
        in_literals = re.findall(r"""['"][^'"\n]*""" + bad, src)
        assert not in_literals, f"{bad!r} is still shown to the operator: {in_literals}"


def test_the_console_waits_out_the_restart_instead_of_giving_up(live):
    """The scenario, driven: pull, build, THE SERVICE GOES AWAY, it comes back.

    The old page stopped polling on the first failed fetch. The check that
    matters is not what it renders during the outage but that it is still
    ASKING — so the number of requests is counted across it.
    """
    from playwright.sync_api import sync_playwright
    script = live[1]
    (script
        .add(step="pull", log=[">>> step: pull", "From github.com:sysible/slop"])
        .add(step="build", log=[">>> step: pull", ">>> step: build", "Building idp"])
        .add(step="build", state="failed", finished=time.time(),
             message="docker compose up --build failed — see the log.",
             log=[">>> step: pull", ">>> step: build", "Building idp", "ERROR"]))

    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            pg.click("#b-slop")
            pg.wait_for_selector("#jobpanel", state="visible", timeout=10000)

            # Phase one: it is being watched, and says which phase.
            pg.wait_for_function(
                "() => (document.getElementById('jobsay')||{}).textContent"
                ".indexOf('Building') >= 0", timeout=10000)
            running = _panel(pg)
            assert "Sysible Linux Operations Platform" in running["title"], running
            labels = [s["label"] for s in running["steps"]]
            assert labels == ["Pull", "Build & start", "Done"], labels
            assert "done" in running["steps"][0]["cls"], running["steps"]
            assert "now" in running["steps"][1]["cls"], running["steps"]

            # THE SERVICE GOES AWAY. Not a stubbed error — the request fails,
            # exactly as it does when compose recreates this container.
            pg.route("**/admin/updates/job*", lambda r: r.abort())
            before = script.asked

            pg.wait_for_function(
                "() => (document.getElementById('jobsay')||{}).textContent"
                ".indexOf('restarting itself') >= 0", timeout=10000)
            out = _panel(pg)
            assert "Reconnecting" in out["say"], out["say"]
            assert "reload" not in out["say"].lower(), out["say"]
            # It keeps the phase it last saw rather than blanking.
            assert "Build & start" in [s["label"] for s in out["steps"]]

            first = int(re.search(r"attempt (\d+)", out["say"]).group(1))
            pg.wait_for_function(
                f"() => {{var t=(document.getElementById('jobsay')||{{}}).textContent;"
                f" var m=/attempt ([0-9]+)/.exec(t); return m && +m[1] > {first + 1};}}",
                timeout=20000)
            assert script.asked == before, \
                "the aborted requests should not have reached the server"

            # The service comes back, and the console picks the answer up by
            # itself — nobody reloaded anything.
            pg.unroute("**/admin/updates/job*")
            pg.wait_for_function(
                "() => (document.getElementById('jobtitle')||{})"
                ".textContent.indexOf('failed') >= 0", timeout=20000)
            end = _panel(pg)
            assert "docker compose up --build failed" in end["say"], end["say"]
            assert "bad" in end["steps"][1]["cls"], end["steps"]
        finally:
            b.close()


def test_the_phase_and_the_clock_are_on_screen(live):
    """"Running" for four minutes is not a progress display."""
    from playwright.sync_api import sync_playwright
    script = live[1]
    script.add(step="pull", started=time.time() - 95,
               log=[">>> step: pull", "Updating 538a889..359854c"])

    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            pg.click("#b-slop")
            pg.wait_for_selector("#jobpanel", state="visible", timeout=10000)
            pg.wait_for_function(
                "() => (document.getElementById('jobtime')||{}).textContent.length > 0",
                timeout=10000)
            p = _panel(pg)
            assert re.match(r"^\d+m \d\ds$", p["time"]), \
                f"the elapsed time is not a duration: {p['time']!r}"
            assert "Fetching the new code" in p["say"], p["say"]
            assert "now" in p["steps"][0]["cls"], p["steps"]
        finally:
            b.close()


def test_a_wrong_clock_shows_no_timer_rather_than_a_wrong_one(live):
    """The container's clock is not the browser's. A job that started "in the
    future" is skew, and a confidently wrong counter is worse than none."""
    from playwright.sync_api import sync_playwright
    script = live[1]
    script.add(step="build", started=time.time() + 7200)

    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            pg.click("#b-slop")
            pg.wait_for_selector("#jobpanel", state="visible", timeout=10000)
            pg.wait_for_function(
                "() => (document.getElementById('jobsay')||{}).textContent"
                ".indexOf('Building') >= 0", timeout=10000)
            assert _panel(pg)["time"] == "", "it invented an elapsed time from skew"
        finally:
            b.close()


def test_a_batch_says_which_products_are_done(live):
    """"3 of 4" does not say WHICH one failed, and one failure does not stop the
    rest (see _queue_worker) — so the list is the point."""
    from playwright.sync_api import sync_playwright
    script = live[1]
    script.add(app="slop", step="build", queue=["controller", "slop"], index=1,
               self_update=True,
               done=[{"key": "controller", "ok": True,
                      "message": "controller updated and restarted."}])

    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            pg.click("#b-slop")
            pg.wait_for_selector("#jobdone .d", timeout=10000)
            p = _panel(pg)
            assert p["count"] == "2 of 2", p["count"]
            joined = " | ".join(p["done"])
            assert "Sysible Controller" in joined, joined
            assert "updated and restarted" in joined, joined
            assert "in progress" in joined, joined
        finally:
            b.close()


def test_the_log_is_available_but_not_the_display(live):
    """It used to BE the progress display: a bare <pre> of compose output. It is
    a reference for when something looks wrong, so it starts closed."""
    from playwright.sync_api import sync_playwright
    script = live[1]
    script.add(step="build", log=[">>> step: pull", ">>> step: build",
                                  "Building idp", "Building gateway"])

    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            pg.click("#b-slop")
            pg.wait_for_selector("#joblogtoggle", state="visible", timeout=10000)
            p = _panel(pg)
            assert p["logHidden"] is True, "the log is open before anyone asked"
            assert "Show log" in p["logBtn"], p["logBtn"]
            assert "2 lines" in p["logBtn"], \
                f"the phase markers are being counted as output: {p['logBtn']!r}"
            pg.click("#joblogtoggle")
            pg.wait_for_selector("#joblog", state="visible", timeout=5000)
            text = pg.text_content("#joblog")
            assert "Building idp" in text
            assert ">>> step:" not in text, \
                "the page's own phase markers are being shown as command output"
        finally:
            b.close()


def test_a_lost_session_goes_to_the_login_page(live):
    """Restarting SLOP restarts the IdP too. If the session did not survive, the
    answer is the login page — not polling forever against a 403."""
    from playwright.sync_api import sync_playwright
    script = live[1]
    script.add(step="build")

    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            pg.click("#b-slop")
            pg.wait_for_selector("#jobpanel", state="visible", timeout=10000)
            # A marker that only a document reload can clear. Asserting on the
            # URL would prove nothing: the session in this test really is valid,
            # so the reload lands straight back on this same page.
            pg.evaluate("() => { window.__beforeReload = true; }")
            pg.route("**/admin/updates/job*",
                     lambda r: r.fulfill(status=403, content_type="application/json",
                                         body=json.dumps({"error": "nope"})))
            pg.wait_for_function("() => window.__beforeReload === undefined",
                                 timeout=20000)
        finally:
            b.close()
