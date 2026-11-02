"""A class the stylesheet does not define renders as nothing, and says nothing.

FOUND BY ACCIDENT, which is the problem. The console was rebuilt from three
columns into a rail and one view, and `.wrap` — the grid those columns lived
in — was deleted with them. `refusal_page()`, the 401 page, was still wrapping
itself in `<div class=wrap>`. It had no test, so nothing failed: it would have
shipped as an unstyled block of text on the one page an operator sees when they
cannot get in.

Nothing here checks that a page looks right. It checks the cheaper thing that
catches the whole class of mistake: every class NAME the server writes into a
page is one the stylesheet actually defines. A rename or a deletion then fails
here rather than in front of somebody.

Server-rendered classes only. The views are built in the browser from STATE and
their classes come and go with the data; those are covered by the tests that
drive a real browser.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from backend import ui  # noqa: E402


class _Who:
    user = "alice"
    role = "operator"


def _markup(html: str) -> str:
    """The page's own markup, with <script> and <style> taken out.

    The console ships its JavaScript inline, and that JavaScript is full of
    template literals that write class attributes — `class="h ${r.state}"` and
    the like. Scanning those gives you JS fragments, not class names, so the
    thing being checked here is only what the SERVER writes.
    """
    html = re.sub(r"<script\b.*?</script>", " ", html, flags=re.S | re.I)
    return re.sub(r"<style\b.*?</style>", " ", html, flags=re.S | re.I)


def _classes(html: str) -> set:
    """Every class name in `class=x`, `class='x y'` or `class="x y"`."""
    out = set()
    for m in re.finditer(r"""class=(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", _markup(html)):
        for name in (m.group(1) or m.group(2) or m.group(3) or "").split():
            # Skip anything the server interpolates rather than writes.
            if "{" in name or "$" in name:
                continue
            out.add(name)
    return out


def _defined() -> set:
    """Every class the stylesheet defines, including inside a media query."""
    return set(re.findall(r"\.([A-Za-z][\w-]*)", ui._CSS))


def test_the_refusal_page_uses_classes_that_exist():
    """The page an operator sees when they cannot get in. It has no console on
    it, so it borrowed the console's layout — and kept borrowing it after that
    layout was deleted."""
    missing = _classes(ui.refusal_page("no_identity", "Not signed in.")) - _defined()
    assert not missing, f"the 401 page uses undefined classes: {sorted(missing)}"


def test_the_console_page_uses_classes_that_exist():
    missing = _classes(ui.console_page(_Who())) - _defined()
    assert not missing, f"the console uses undefined classes: {sorted(missing)}"


def test_the_stylesheet_is_actually_being_read():
    """A guard that passes because it found nothing to check is not a guard.
    Both pages must really put classes on the page, and the CSS must really
    define some."""
    assert len(_defined()) > 40, len(_defined())
    assert len(_classes(ui.refusal_page("x", "y"))) >= 3
    assert len(_classes(ui.console_page(_Who()))) >= 10
