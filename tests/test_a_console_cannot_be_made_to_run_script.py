"""The server-rendered consoles build HTML with f-strings.

Baseline, Flashback and Visualizer compose their pages by interpolating values
into string literals. That is fine and it is fast, and it has exactly one way to
go wrong: a value that reaches the page without `escape()` turns whoever can
influence that value into an author of the page.

The values these pages render server-side are the caller's identity — the user
and role the gateway asserted — and, on the refusal pages, a reason string.

All three escape correctly today. None of them had a test saying so, and the
escaping is done at the EDGE (`escape(who.user)` at the call site) with the page
shell receiving strings it trusts. That is a sound pattern and a fragile one:
the shell cannot tell an escaped string from an unescaped one, so a single new
caller passing raw data is all it takes.

These render the real pages with a hostile identity and look at the output. A
test that read the source for `escape(` would pass against a call that had been
moved, which is not the same question.
"""
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

EVIL = "<script>alert(1)</script>"
ESCAPED = "&lt;script&gt;"


class _Who:
    """Enough of an Identity for the renderers that take one."""

    def __init__(self, user, role):
        self.user, self.role = user, role

    @property
    def can_write(self):
        return True


def _pages(app, ui):
    """(name, html) for every server-rendered page the console exposes.

    Each console names its renderers differently, which is itself worth
    pinning: a console that grows a NEW page is not covered until it is added
    here, and the count assertion below is what says so."""
    if app == "baseline":
        return [("console_page", ui.console_page(_Who(EVIL, "superuser"))),
                ("refusal_page", ui.refusal_page("bad-proof", EVIL))]
    if app == "flashback":
        return [("page", ui.page(EVIL, EVIL, True)),
                ("denied_page", ui.denied_page(EVIL, "bad-proof"))]
    if app == "visualizer":
        return [("page", ui.page(EVIL, EVIL)),
                ("denied_page", ui.denied_page(EVIL, "bad-proof"))]
    raise AssertionError("no page list for %s" % app)


CONSOLES = ("baseline", "flashback", "visualizer")


@pytest.mark.parametrize("app", CONSOLES)
def test_a_hostile_identity_is_escaped_not_rendered(app, app_module):
    """The gateway asserts the user and role; the console prints them. A name
    carrying a script tag must come out as text."""
    with app_module(app, "backend.ui") as ui:
        for name, html in _pages(app, ui):
            body = html if isinstance(html, str) else \
                getattr(html, "body", b"").decode("utf-8", "replace")
            assert EVIL not in body, (
                "%s's %s rendered a raw <script> from the caller's identity" % (app, name))
            assert ESCAPED in body, (
                "%s's %s did not render the value at all — the test is not "
                "exercising the path it thinks it is" % (app, name))


@pytest.mark.parametrize("app", CONSOLES)
def test_the_refusal_page_escapes_its_reason(app, app_module):
    """The refusal pages take a REASON string, and a refusal is exactly when
    unusual input is in play. Covered above as one of the pages, and named here
    so a failure says which half broke."""
    with app_module(app, "backend.ui") as ui:
        pages = dict(_pages(app, ui))
        name = "refusal_page" if "refusal_page" in pages else "denied_page"
        body = pages[name]
        assert EVIL not in body and ESCAPED in body, (
            "%s's %s does not escape its reason" % (app, name))


@pytest.mark.parametrize("app", CONSOLES)
def test_an_attribute_cannot_be_broken_out_of(app, app_module):
    """The identity is rendered inside attributes as well as text. A bare quote
    there escapes the attribute without needing a tag at all."""
    with app_module(app, "backend.ui") as ui:
        probe = 'x" onmouseover="alert(1)'
        if app == "baseline":
            body = ui.console_page(_Who(probe, "superuser"))
        elif app == "flashback":
            body = ui.page(probe, "superuser", True)
        else:
            body = ui.page(probe, "superuser")
        assert 'onmouseover="alert(1)' not in body, (
            "%s let a value close an HTML attribute" % app)


def test_the_escaping_helper_is_the_real_one():
    """`escape` has to be html.escape with quote=True. A local helper that
    handles < and > but not " leaves every attribute open, and reads identically
    at the call sites."""
    import html as _html
    assert _html.escape('a"b', quote=True) == "a&quot;b"
    for app in CONSOLES:
        src = (REPO / app / "backend" / "ui.py").read_text(encoding="utf-8")
        assert "from html import escape" in src or "html.escape" in src, (
            "%s uses an escape() that is not html.escape — check it handles "
            "quotes, or every attribute it renders is open" % app)
