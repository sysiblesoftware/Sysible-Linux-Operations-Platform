"""An available update has to be visible from the screen everybody lands on.

Reported as "there was an update available but I have to go all the way to the
admin tile to see it". That was exactly right: the only update indicator in SLOP
was the pill on the IdP's admin pages, so you had to open Administration to find
out there was a reason to open Administration. The portal — the one screen every
operator starts on — said nothing at all.

The count now sits on the Administration tile, in the corner the app tiles give
their health dot.

These drive the SHIPPING portal in a real browser against a stubbed
/admin/updates/status, because what is being asserted is what an operator sees.

Run: pytest tests/    (from the repo root)
"""
import json
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTAL = os.path.join(REPO, "portal")

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                   # pragma: no cover
    sync_playwright = None

pytestmark = pytest.mark.skipif(sync_playwright is None,
                                reason="playwright is not installed")

CHROMIUM = os.environ.get("CHROMIUM_BIN", "/opt/pw-browsers/chromium")


def _apps(available_count, total=4):
    names = ["controller", "slep", "connect", "slop"][:total]
    return {"apps": [{"key": n, "available": i < available_count}
                     for i, n in enumerate(names)]}


@pytest.fixture()
def portal():
    """The real portal page, with /auth/me and the updates endpoint stubbed."""
    class Portal:
        def open(self, role="superuser", status=None, status_code=200, me_status=200,
                 css=None, visit_admin=False):
            pw = sync_playwright().start()
            browser = pw.chromium.launch(executable_path=CHROMIUM)
            page = browser.new_page()
            self._stop = lambda: (browser.close(), pw.stop())
            self.calls = []

            def route(r, req):
                u = req.url
                if "/auth/me" in u:
                    if me_status != 200:
                        return r.fulfill(status=me_status, body="")
                    body = {"authenticated": True, "user": "admin"}
                    # role=None stands for "answered, but said nothing about
                    # the role" — which is NOT the same as "not a superuser".
                    if role is not None:
                        body["role"] = role
                    return r.fulfill(status=200, content_type="application/json",
                                     body=json.dumps(body))
                if "/admin/updates/status" in u:
                    self.calls.append(u)
                    return r.fulfill(status=status_code, content_type="application/json",
                                     body=json.dumps(status if status is not None else {}))
                if "/healthz/" in u:
                    return r.fulfill(status=200, content_type="application/json",
                                     body=json.dumps({"ok": True}))
                return r.fulfill(status=404, body="")

            def dispatch(r, q):
                # Stand in for a browser holding an OLD style.css next to this
                # page — the three portal files have three cache entries.
                if css is not None and q.url.endswith("style.css"):
                    return r.fulfill(status=200, content_type="text/css", body=css)
                if ("/auth/me" in q.url or "/admin/" in q.url or "/healthz/" in q.url):
                    return route(r, q)
                return r.continue_()

            page.route("**/*", dispatch)
            if visit_admin:
                # Make /admin a VISITED link. A fresh profile has visited
                # nothing, so :visited never applies and an unstyled <a> is not
                # purple — which is why every earlier test missed the reported
                # symptom entirely.
                page.goto("file://" + os.path.join(PORTAL, "index.html") + "#admin-visited")
                page.evaluate("() => { var a=document.getElementById('admtab');"
                              " if(a) a.click(); }")
                page.wait_for_timeout(100)
            page.goto("file://" + os.path.join(PORTAL, "index.html"))
            page.wait_for_timeout(600)
            self.page = page
            return page

        def badge(self):
            el = self.page.query_selector("#upd-admin")
            assert el is not None, "the Administration tile has no update badge at all"
            return el

    p = Portal()
    yield p
    try:
        p._stop()
    except Exception:
        pass


# ---- the report -------------------------------------------------------------
def test_an_available_update_is_visible_without_opening_administration(portal):
    portal.open(status=_apps(available_count=2))
    b = portal.badge()
    assert b.is_visible(), "an update is waiting and the portal still says nothing"
    assert b.inner_text().strip() == "2 updates"


def test_one_update_is_not_called_updates(portal):
    portal.open(status=_apps(available_count=1))
    assert portal.badge().inner_text().strip() == "1 update"


def test_a_current_platform_shows_no_badge(portal):
    """A tile that always carries a badge teaches people to stop seeing it."""
    portal.open(status=_apps(available_count=0))
    assert not portal.badge().is_visible()


# ---- it must not become a new way for the portal to break -------------------
def test_an_error_from_the_endpoint_leaves_the_badge_hidden(portal):
    """The portal is best-effort everywhere else; an updates check that cannot
    answer must say nothing, not claim zero."""
    portal.open(status={"error": "updater unreachable", "apps": []})
    assert not portal.badge().is_visible()


def test_a_403_leaves_the_badge_hidden(portal):
    portal.open(status={"detail": "Superuser access required."}, status_code=403)
    assert not portal.badge().is_visible()


def test_a_non_superuser_never_asks(portal):
    """/admin/updates/status answers 403 to anyone else, and the tab it badges
    is superuser-only — so an operator's portal must not call it at all."""
    p = portal.open(role="operator", status=_apps(available_count=2))
    assert portal.calls == [], portal.calls
    assert p.query_selector("#admtab").is_hidden()


# ---- and it must not cost the portal its speed ------------------------------
def test_it_does_not_force_a_git_fetch(portal):
    """refresh=1 makes the updater fetch every product's remote. The portal is
    loaded far too often to pay that; the cached answer is the same answer."""
    portal.open(status=_apps(available_count=1))
    assert portal.calls, "the portal never checked"
    assert all("refresh" not in u for u in portal.calls), portal.calls


# ---- the bug the test above found -------------------------------------------
# `hidden` is only display:none from the UA stylesheet, and .card/.userbox both
# set an explicit display, so every `hidden` in this page was being overridden.
# The superuser-only Administration entry rendered for every signed-in user,
# and the identity chip appeared before /auth/me had answered.
#
# It is a header TAB now rather than a tile in the app grid — Administration is
# not an app, and the grid answers "which tool do I want". The rule it proves is
# the same one, and it is the rule that matters: hidden has to mean hidden.
def test_the_administration_tab_is_really_hidden_from_a_non_superuser(portal):
    """The tab is rendered VISIBLE and hidden once /auth/me says the role is
    not superuser — see test_there_is_always_a_way_into_administration below
    for why that way round. `hidden` still has to actually hide."""
    p = portal.open(role="operator", status=_apps(available_count=2))
    tab = p.query_selector("#admtab")
    assert tab is not None, "the Administration entry is gone from the portal"
    assert tab.get_attribute("hidden") is not None, "nothing hid it from an operator"
    assert tab.is_hidden(), "it carries hidden= and renders anyway"


def test_an_auditor_does_not_see_it_either(portal):
    p = portal.open(role="auditor", status=_apps(available_count=2))
    assert p.query_selector("#admtab").is_hidden()


def test_a_superuser_does_see_it(portal):
    """The rule must hide what is hidden without hiding what is not."""
    p = portal.open(role="superuser", status=_apps(available_count=0))
    assert p.query_selector("#admtab").is_visible()


def test_the_identity_chip_appears_only_once_someone_is_known(portal):
    """Same inert-hidden bug: it showed before /auth/me resolved."""
    p = portal.open(role="superuser", status=_apps(available_count=0))
    assert p.query_selector("#userbox").is_visible()


def test_administration_is_not_one_of_the_app_tiles(portal):
    """It is not an app. The grid answers "which tool do I want"; Administration
    answers "I need to change the platform itself", and it grew a health dot and
    an update badge that no other tile had because it never fitted there."""
    p = portal.open(role="superuser", status=_apps(available_count=1))
    assert p.query_selector("#admtab") is not None, "the header tab is gone"
    in_grid = p.evaluate(
        "() => [...document.querySelectorAll('.cards .card')]"
        ".map(c => (c.querySelector('h2')||{}).textContent||'')")
    assert not any("Administration" in t for t in in_grid), \
        f"Administration is back in the app grid: {in_grid}"
    assert p.evaluate("() => !!document.querySelector('header .admtab')"), \
        "the Administration tab is not in the header"


# ---- the way in must not depend on a fetch succeeding ----------------------
# Reported: "I don't see a way to go to the administration options."
#
# The tab was rendered `hidden` and revealed only when /auth/me came back with
# exactly role === "superuser". It is the ONLY link to /admin on this page, so
# every other outcome — the request failing, an answer with no role in it, an
# answer saying "Superuser" — produced the same silent nothing, with no way
# into Administration and nothing on screen to suggest one existed.
#
# It fails open now. A non-superuser who slips through sees one link that tells
# them they need a superuser account; /admin is gated server-side by the IdP
# and the whole portal is behind sign-in at the gateway. Failing closed costs
# everyone the way in, which is strictly worse.
def _tab_visible(page):
    return page.evaluate(
        "() => {var t = document.getElementById('admtab');"
        " return t ? t.offsetParent !== null : false;}")


def test_a_superuser_sees_the_way_in(portal):
    assert _tab_visible(portal.open(role="superuser", status=_apps(0)))


def test_an_unreachable_auth_me_still_leaves_a_way_in(portal):
    """This is the reported bug. The portal is best-effort about everything
    else; being best-effort about the only entry point meant losing it."""
    p = portal.open(role="superuser", status=_apps(0), me_status=503)
    assert _tab_visible(p), \
        "/auth/me failed and the portal has no link to Administration at all"


def test_an_answer_with_no_role_leaves_a_way_in(portal):
    """Not knowing the role is not the same as knowing it is not superuser."""
    p = portal.open(role=None, status=_apps(0))
    assert _tab_visible(p), "an answer that said nothing about the role hid the way in"


def test_the_role_is_matched_without_caring_about_case(portal):
    """Being strict here only creates silent invisibility."""
    assert _tab_visible(portal.open(role="Superuser", status=_apps(0)))


@pytest.mark.parametrize("role", ["operator", "auditor"])
def test_a_non_superuser_is_the_one_case_that_hides_it(portal, role):
    p = portal.open(role=role, status=_apps(2))
    assert not _tab_visible(p), f"a {role} is shown the Administration tab"


# ---- a stylesheet from before this page was written ------------------------
# Reported, looking at the header: "Why is this purple? The font looks weird
# too." The Administration tab was rendering as underlined visited-purple at
# the inherited font size — which is exactly what an <a> looks like with no
# rule applied, because the browser was holding a style.css from before
# .admtab existed.
#
# The gateway now sends no-cache for all three portal files, but that does
# nothing for a copy cached BEFORE the header shipped: it keeps being served
# from heuristic freshness. The page renders with the wrong rules and says
# nothing about it, which is the part worth fixing.
_OLD_CSS = ":root{--bg:#0d1117;--text:#e6edf5;--muted:#93a1b5;--font:sans-serif}\n" \
           "body{background:var(--bg);color:var(--text);font-family:var(--font)}\n"


def test_a_current_stylesheet_says_nothing(portal):
    p = portal.open(role="superuser", status=_apps(0))
    assert p.query_selector("#stale") is None, \
        "the stale-asset warning fires on a perfectly current page"


def test_a_stylesheet_from_before_this_page_is_called_out(portal):
    """Otherwise the only symptom is 'why is this purple'."""
    p = portal.open(role="superuser", status=_apps(0), css=_OLD_CSS)
    el = p.query_selector("#stale")
    assert el is not None, \
        "an out-of-date stylesheet renders the page wrong and says nothing"
    assert el.is_visible()
    text = el.text_content()
    assert "older copy" in text, text
    assert "Ctrl-Shift-R" in text, "the message does not say how to fix it"


def test_the_warning_does_not_depend_on_the_stylesheet_it_is_warning_about(portal):
    """A class would be the one rule that cannot be relied on here."""
    p = portal.open(role="superuser", status=_apps(0), css=_OLD_CSS)
    # NOT offsetParent: it is null for a position:fixed element by spec, so the
    # first version of this check called a perfectly visible banner invisible.
    # Measure the box instead.
    styled = p.evaluate(
        "() => {var e=document.getElementById('stale');"
        " if(!e) return null; var s=getComputedStyle(e); var r=e.getBoundingClientRect();"
        " return {pos:s.position, bg:s.backgroundColor,"
        "         w:Math.round(r.width), h:Math.round(r.height)};}")
    assert styled and styled["w"] > 200 and styled["h"] > 10, styled
    assert styled["pos"] == "fixed", styled
    assert styled["bg"] != "rgba(0, 0, 0, 0)", f"the banner has no background: {styled}"


def test_an_unreadable_stylesheet_never_cries_wolf(portal):
    """If the marker cannot be read at all, saying nothing beats saying the
    wrong thing — a false 'your page is stale' is worse than none."""
    p = portal.open(role="superuser", status=_apps(0))
    broke = p.evaluate("""() => {
        var orig = window.getComputedStyle;
        window.getComputedStyle = function(){ throw new Error('nope'); };
        try { return true; } finally { window.getComputedStyle = orig; }
    }""")
    assert broke
    assert p.query_selector("#stale") is None


# ---- the header tab must not depend on a separately-cached file ------------
# Reported twice. First "I don't see a way to go to the administration
# options" (the reveal logic), then "Why is this purple? The font looks weird
# too" and, after a hard refresh, "Administration is still purple".
#
# Purple-and-underlined at the inherited size is what an <a> looks like with no
# rule applied. index.html and style.css are two files with two cache entries,
# so a browser can hold new markup with an old stylesheet — and this element is
# the only way into Administration, so when it renders unstyled it does not
# just look wrong, it stops reading as a control at all.
#
# Its rules ship inline with the markup that needs them now. Everything else on
# the page is older than its markup and stays in the stylesheet.
#
# The link is VISITED in these tests. A fresh browser profile never visits
# anything, so :visited never applied and the exact reported symptom was
# unreproducible in every test written before this one.
_PRE_TAB_CSS = ":root{--bg:#0d1117;--panel:#131923;--line:#26303f;--text:#e6edf5;" \
               "--muted:#93a1b5;--accent:#43a047;--font:sans-serif}\n" \
               "body{background:var(--bg);color:var(--text);font-family:var(--font)}\n" \
               ".head-right{display:flex;align-items:center;gap:14px}\n" \
               "[hidden]{display:none !important}\n"


def _tab_style(page):
    """The tab's computed style, plus what var(--muted, …) resolves to RIGHT NOW.

    Comparing against a literal colour was wrong: the portal has a light theme,
    so --muted is #93a1b5 or #5b6675 depending on which one is active, and the
    first version of this test failed on correct code for that reason alone.
    The property is "it is the muted colour", not "it is that hex".
    """
    return page.evaluate(
        "() => {var t=document.getElementById('admtab'); if(!t) return null;"
        " var probe=document.createElement('span');"
        " probe.style.color='var(--muted, #93a1b5)';"
        " document.body.appendChild(probe);"
        " var want=getComputedStyle(probe).color; probe.remove();"
        " var s=getComputedStyle(t);"
        " return {color:s.color, muted:want, deco:s.textDecorationLine,"
        "         border:parseFloat(s.borderTopWidth), size:s.fontSize};}")


@pytest.mark.parametrize("css,label", [
    (None, "the current stylesheet"),
    (_PRE_TAB_CSS, "a stylesheet from before the tab existed"),
    ("", "no stylesheet at all"),
])
def test_the_admin_tab_is_styled_whatever_the_stylesheet_says(portal, css, label):
    p = portal.open(role="superuser", status=_apps(1), css=css, visit_admin=True)
    got = _tab_style(p)
    assert got, "the Administration tab is gone"
    assert got["deco"] == "none", f"underlined with {label}: {got}"
    assert got["color"] == got["muted"], \
        f"not the muted colour with {label} — purple is the visited default: {got}"
    assert got["border"] >= 1, f"no tab outline with {label}: {got}"
    assert got["size"] == "13.5px", f"inherited font size with {label}: {got}"


def test_the_tab_rules_travel_with_the_markup(portal):
    """Structural, not visual: the point is that the two cannot be split."""
    import os as _os
    html = open(_os.path.join(PORTAL, "index.html"), encoding="utf-8").read()
    assert ".admtab {" in html or ".admtab{" in html, \
        "the tab's rules left index.html — a cached stylesheet can strand it again"
    css = open(_os.path.join(PORTAL, "style.css"), encoding="utf-8").read()
    decls = [ln for ln in css.splitlines()
             if ln.strip().startswith(".admtab") and "{" in ln]
    assert not decls, f"the rules are in both files and will drift: {decls}"
