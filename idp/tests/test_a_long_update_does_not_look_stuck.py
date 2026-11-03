"""Updating SLOP takes minutes, and the console said seconds.

REPORTED, over a screenshot of the panel at 6m 44s: "The update doesn't
automatically refresh it's just stuck." The panel read:

    SLOP is restarting itself. This console is part of what is being updated,
    so it drops out for a few seconds and comes back on its own.
    Reconnecting… (attempt 97)

Nothing was stuck. Updating SLOP rebuilds every image in the project —
gateway, sign-in, Flashback, Baseline, Visualizer and the updater itself — and
this console is one of them, so it cannot be reached for the whole rebuild.
The poller was doing exactly what it should.

What was wrong was everything it said about it.

  * "a few seconds" is wrong by an order of magnitude. An operator told
    seconds who then watches six minutes pass concludes it has hung, which is
    precisely what happened.

  * "attempt 97" is a retry counter. It tells an operator nothing they can
    judge and reads like a failure report; the number they are actually
    weighing is how long it has been.

  * And the one real bug underneath: the helper keeps a finished job's result
    for a bounded window, and an update that outlasted the window ended with
    the panel vanishing and NO VERDICT AT ALL — on the one screen whose whole
    job is to say how it went.
"""
import importlib
import os
import re
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from test_an_update_is_watchable import (  # noqa: E402
    BASE, Script, _open, _panel, _playwright, live,          # noqa: F401
)


def _start_then_lose_contact(pg):
    """Press Update, WAIT FOR THE PANEL TO HAVE A JOB IN IT, then cut the line.

    The waiting note is drawn from the last job the page saw, so cutting the
    connection before the first successful poll leaves nothing to draw and the
    note never appears. A fixed 600ms sleep raced the poller's own 600ms first
    tick and these tests failed about a third of the time on it.
    """
    pg.click("#b-slop")
    pg.wait_for_function(
        "() => {const p=document.getElementById('jobpanel');"
        " return p && !p.hidden && (document.getElementById('jobtitle')||{})"
        ".textContent.length > 0;}", timeout=15000)
    pg.route("**/admin/updates/job*", lambda r: r.abort())
    pg.wait_for_function(
        "() => (document.getElementById('jobsay')||{}).textContent"
        ".indexOf('restarting itself') >= 0", timeout=15000)


# ---------------------------------------------------------------------------
# what it promises
# ---------------------------------------------------------------------------
def test_it_does_not_promise_a_few_seconds():
    """The sentence that made a working update look broken.

    Reads the FILE. The first version asked inspect.getsource for the reloaded
    module and got something that did not contain the page's JavaScript at all,
    so restoring the old wording sailed straight through it — caught by the
    re-break pass, which is the only reason this is not still a test that
    cannot fail.
    """
    src = open(os.path.join(HERE, "app.py"), encoding="utf-8").read()
    # COLLAPSED, because the sentence is built from adjacent JS string literals
    # split across lines — "...so it drops out for " + "a few seconds and..." —
    # so the plain substring never appeared in the file even when the wording
    # did. The re-break pass caught that; it is the second assertion in this
    # file that could not fail.
    flat = re.sub(r"['\"]\s*\+?\s*['\"]", "", src)
    assert "drops out for a few seconds" not in flat, \
        "the console still promises a few seconds for a full rebuild"
    assert "A full rebuild is usually a few minutes" in flat, \
        "nothing tells the operator the real duration"


def test_it_says_minutes_and_that_the_work_carries_on(live):
    from playwright.sync_api import sync_playwright
    m, script = live
    script.add(step="build")
    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            _start_then_lose_contact(pg)
            say = _panel(pg)["say"]
            assert "minutes" in say, say
            assert "carries on" in say or "carry on" in say, say
            assert "few seconds" not in say, say
        finally:
            b.close()


# ---------------------------------------------------------------------------
# what it counts
# ---------------------------------------------------------------------------
def test_it_reports_elapsed_time_not_an_attempt_number(live):
    from playwright.sync_api import sync_playwright
    m, script = live
    script.add(step="build")
    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            _start_then_lose_contact(pg)
            say = _panel(pg)["say"]
            assert "attempt" not in say.lower(), \
                f"the raw retry counter is still on screen: {say}"
            assert re.search(r"\d+s\b|\d+m \d+s", say), \
                f"nothing says how long it has been waiting: {say}"
        finally:
            b.close()


def test_the_elapsed_time_actually_advances(live):
    """A clock frozen at 0s is worse than no clock: it reads as a hang."""
    from playwright.sync_api import sync_playwright
    m, script = live
    script.add(step="build")
    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            _start_then_lose_contact(pg)
            first = re.search(r"(\d+)s", _panel(pg)["say"])
            assert first, _panel(pg)["say"]
            pg.wait_for_function(
                f"() => {{var t=(document.getElementById('jobsay')||{{}}).textContent;"
                f" var m=/([0-9]+)s/.exec(t); return m && +m[1] > {int(first.group(1))};}}",
                timeout=20000)
        finally:
            b.close()


def test_it_keeps_waiting_rather_than_giving_up(live):
    """The work is still running on the host whatever this page can see."""
    from playwright.sync_api import sync_playwright
    m, script = live
    script.add(step="build")
    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            _start_then_lose_contact(pg)
            pg.wait_for_timeout(4000)
            say = _panel(pg)["say"]
            assert "reload" not in say.lower(), say
            assert "Waiting" in say or "waiting" in say, say
        finally:
            b.close()


# ---------------------------------------------------------------------------
# the real bug: a long update losing its verdict
# ---------------------------------------------------------------------------
def test_an_update_that_outlasts_the_reporting_window_still_says_so(live):
    """The helper keeps a finished result for a bounded time so last week's
    update does not greet whoever opens the page. An update longer than that
    window came back to a blank panel and no word either way — the operator is
    left to work out from version strings whether their update worked."""
    from playwright.sync_api import sync_playwright
    m, script = live
    script.add(step="build")
    with sync_playwright() as pw:
        b, pg = _open(pw)
        try:
            # Out of contact for a while...
            _start_then_lose_contact(pg)
            pg.wait_for_timeout(1200)
            # ...and when it comes back, the helper has nothing left to report.
            pg.unroute("**/admin/updates/job*")
            pg.route("**/admin/updates/job*", lambda r: r.fulfill(
                status=200, content_type="application/json", body='{"job": null}'))
            # Wait for THIS toast, not for any toast — pressing Update already
            # put "Updating slop…" on screen, so "a toast exists" was true
            # before the thing under test had happened at all and the first
            # version of this assertion passed on the wrong one.
            pg.wait_for_function(
                "() => (document.getElementById('toasts')||{}).innerText"
                ".toLowerCase().indexOf('out of contact') >= 0", timeout=20000)
            text = pg.inner_text("#toasts")
            assert "finished" in text.lower(), text
            assert "installed now" in text.lower() or "versions" in text.lower(), text
        finally:
            b.close()
