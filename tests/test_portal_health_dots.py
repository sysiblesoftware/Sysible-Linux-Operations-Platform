"""The portal's status dots have to reach a verdict.

Reported as "SLOP isn't coming up", with a screenshot of the portal in which all
six tiles carried a GREY dot — the colour the page ships with, meaning "still
checking". Not red, not amber: nothing. The status board said nothing at all, in
precisely the situation it exists for.

A module that is DOWN refuses the connection and its dot goes red immediately.
A module that is WEDGED accepts the connection and never answers, and neither the
gateway (no default response timeout in Caddy) nor the page (a bare fetch) had a
deadline — so the probe never settled and the dot never painted, for as long as
the tab stayed open.

This drives the SHIPPING portal in a real browser against an upstream that
accepts and then says nothing, and asserts the dots stop being grey. The gateway
carries its own fix (gateway/tests/test_caddyfile.py); this one is about the page
not depending on the gateway's version to tell the truth.

Run: pytest tests/    (from the repo root)
"""
import os
import re
import shutil
import socket
import subprocess
import threading
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTAL = os.path.join(REPO, "portal")
CADDY = shutil.which("caddy") or os.environ.get("CADDY_BIN")


def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture()
def wedged_portal(tmp_path):
    """The real portal, served by a real caddy, whose /healthz/* upstream accepts
    every connection and answers none of them."""
    sink = socket.socket()
    sink.bind(("127.0.0.1", 0))
    sink.listen(32)
    held = []

    def _hold():
        while True:
            try:
                c, _ = sink.accept()
            except OSError:
                return
            held.append(c)
    threading.Thread(target=_hold, daemon=True).start()

    port = _free_port()
    cfg = tmp_path / "Caddyfile"
    cfg.write_text(
        "{\n\tadmin off\n\tauto_https off\n}\n\n"
        f":{port} {{\n"
        f"\thandle /healthz/* {{\n\t\treverse_proxy 127.0.0.1:{sink.getsockname()[1]}\n\t}}\n"
        f"\thandle {{\n\t\troot * {PORTAL}\n\t\tfile_server\n\t}}\n"
        "}\n")
    proc = subprocess.Popen([CADDY, "run", "--config", str(cfg), "--adapter", "caddyfile"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        sink.close()


# ---- the rule, readable without a browser ----------------------------------
def test_the_probe_has_a_deadline():
    src = open(os.path.join(PORTAL, "app.js"), encoding="utf-8").read()
    poll = src[src.index("function poll(app)"):src.index("function healthApps()")]
    assert "AbortController" in poll and "setTimeout" in poll, \
        "the health probe can hang forever again, and the dot stays grey with it"
    assert re.search(r"PROBE_MS\s*=\s*\d+", src), "no deadline is defined"


def test_a_browser_without_abortcontroller_still_settles():
    """The fallback matters more than it looks: without it the guard silently does
    nothing on the browser that needed it most."""
    src = open(os.path.join(PORTAL, "app.js"), encoding="utf-8").read()
    poll = src[src.index("function poll(app)"):src.index("function healthApps()")]
    assert "if (!ctl)" in poll, "no fallback when the fetch cannot be aborted"


# ---- ...and the same thing, driven -----------------------------------------
@pytest.mark.skipif(not CADDY, reason="caddy binary not available")
@pytest.mark.skipif(not _playwright(), reason="playwright not installed")
def test_a_wedged_module_turns_the_dot_red_rather_than_leaving_it_grey(wedged_portal):
    from playwright.sync_api import sync_playwright

    chrome = os.environ.get("SYSIBLE_TEST_CHROMIUM")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
        except Exception as e:
            pytest.skip(f"no chromium to drive ({e})")
        pg = b.new_page(viewport={"width": 1200, "height": 900})
        pg.goto(wedged_portal, wait_until="domcontentloaded")
        pg.wait_for_selector(".dot[data-health]")

        read = ("() => [...document.querySelectorAll('.dot[data-health]')]"
                ".map(d => d.className.replace('dot','').trim() || 'GREY')")
        deadline = time.time() + 20
        states = pg.evaluate(read)
        while time.time() < deadline and any(s == "GREY" for s in states):
            pg.wait_for_timeout(500)
            states = pg.evaluate(read)
        b.close()

    assert states, "the portal rendered no status dots at all"
    assert not any(s == "GREY" for s in states), (
        f"dots still unpainted after 20s: {states} — this is the reported "
        f"screenshot, where every tile sat grey while the platform was in trouble")
    assert all(s == "down" for s in states), \
        f"a module that never answers is 'not reachable': {states}"
