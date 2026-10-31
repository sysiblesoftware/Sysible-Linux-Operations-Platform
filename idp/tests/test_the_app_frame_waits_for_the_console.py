"""Administration → Apps must not call a loading console a failure.

The embedded consoles are single-page apps. Their first paint is a placeholder
— SLEP renders `<div class="center muted">Loading…</div>`, the Controller a bare
spinner — and the real tree appears only after /me resolves. The iframe's `load`
event fires when the HTML and the bundle have loaded, which is BEFORE that.

_APPFRAME_JS judged the page once, on that event, by element count and text
length ("if(n>12||t.length>600) return null"). Two elements and eight
characters is not a console by that measure, so a perfectly healthy SLEP was
reported as

    Sysible Linux Engineering Platform did not load its administration.
    It returned: Loading…

the frame was hidden, and nothing ever re-checked — the SPA does not reload, so
it could not self-correct. The Controller's spinner has no text at all and read
as "It returned an empty page."

The measure is fine; judging once, against an asynchronous render, is the bug.
So the check now watches the document and only gives up after a deadline.

Driven in a real browser, because the thing under test is an event race.
"""
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "idp"))

APP_PY = (ROOT / "idp" / "app.py").read_text()


def _appframe_js() -> str:
    m = re.search(r'_APPFRAME_JS = r"""(.*?)"""', APP_PY, re.S)
    assert m, "could not find _APPFRAME_JS in idp/app.py"
    return m.group(1)


HOST = """<!doctype html><meta charset=utf-8>
<iframe id=appframe src="{src}"></iframe>
<div id=framefail hidden><span id=framewhy></span></div>
<script>{js}</script>
"""

# An SPA: a placeholder at first paint, the real console a moment later.
SPA = """<!doctype html><meta charset=utf-8><body><div class="center muted">Loading…</div>
<script>
setTimeout(function(){
  var h='';
  for (var i=0;i<40;i++) h += '<div><span>row '+i+'</span></div>';
  document.body.innerHTML = h;
}, 400);
</script>
"""

# A genuine failure: a short error body that never changes.
BROKEN = """<!doctype html><meta charset=utf-8><body>502 Bad Gateway"""

EMPTY = """<!doctype html><meta charset=utf-8><body>"""


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"playwright unavailable ({e})")
    chrome = os.environ.get("SYSIBLE_TEST_CHROMIUM")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}),
                                  args=["--no-sandbox"])
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no chromium to drive ({e})")
        try:
            yield b
        finally:
            b.close()


def _verdict(browser, inner_html, settle_ms=2500):
    """(frame_hidden, why) after letting the page settle."""
    page = browser.new_page()
    try:
        page.route("**/inner", lambda r: r.fulfill(status=200, content_type="text/html",
                                                   body=inner_html))
        page.route("**/host", lambda r: r.fulfill(
            status=200, content_type="text/html",
            body=HOST.format(src="/inner", js=_appframe_js())))
        page.goto("http://sysible.test/host", wait_until="load")
        page.wait_for_timeout(settle_ms)
        hidden = page.eval_on_selector("#appframe", "e => e.hidden")
        why = page.eval_on_selector("#framewhy", "e => e.textContent")
        box = page.eval_on_selector("#framefail", "e => e.hidden")
        return hidden, (why or "").strip(), box
    finally:
        page.close()


def test_a_console_that_renders_late_is_not_reported_as_failed(browser):
    hidden, why, box_hidden = _verdict(browser, SPA)
    assert hidden is False, f"a healthy console was hidden: {why!r}"
    assert box_hidden is True, f"the failure box was shown: {why!r}"


# Past the deadline the check gives up on. A genuinely dead app costs the
# operator those few seconds; the alternative was slandering a live one.
_PAST_DEADLINE_MS = 6000


def test_a_real_failure_is_still_reported(browser):
    hidden, why, box_hidden = _verdict(browser, BROKEN, settle_ms=_PAST_DEADLINE_MS)
    assert hidden is True, "a 502 body was presented as a console"
    assert box_hidden is False
    assert "502" in why, why


def test_an_empty_page_is_still_reported(browser):
    hidden, why, box_hidden = _verdict(browser, EMPTY, settle_ms=_PAST_DEADLINE_MS)
    assert hidden is True
    assert "empty" in why.lower(), why


def test_the_frame_stays_visible_while_it_waits(browser):
    """What the operator sees in the meantime: the console loading, not a hidden
    frame and not a failure box that will be taken back."""
    hidden, why, box_hidden = _verdict(browser, BROKEN, settle_ms=800)
    assert hidden is False, "the frame was hidden before the deadline"
    assert box_hidden is True, f"a failure was announced early: {why!r}"


def test_a_console_that_takes_most_of_the_window_still_wins(browser):
    """Nothing special about 400ms — the watcher has to keep looking right up to
    the deadline, not just for the first moment after load."""
    slow = SPA.replace("}, 400);", "}, 3000);")
    hidden, why, box_hidden = _verdict(browser, slow, settle_ms=5000)
    assert hidden is False, f"a slow but healthy console was hidden: {why!r}"
    assert box_hidden is True


def test_the_check_does_not_judge_only_on_load():
    """Source-level backstop for the shape, so the intent survives a rewrite
    even where no browser is available to run the cases above."""
    js = _appframe_js()
    assert "MutationObserver" in js or "setInterval" in js or "setTimeout" in js, (
        "the check must re-examine the document, not judge once on 'load'")
