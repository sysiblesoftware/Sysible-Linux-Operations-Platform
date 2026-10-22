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
        def open(self, role="superuser", status=None, status_code=200):
            pw = sync_playwright().start()
            browser = pw.chromium.launch(executable_path=CHROMIUM)
            page = browser.new_page()
            self._stop = lambda: (browser.close(), pw.stop())
            self.calls = []

            def route(r, req):
                u = req.url
                if "/auth/me" in u:
                    body = {"authenticated": True, "user": "admin", "role": role}
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

            page.route("**/*", lambda r, q: route(r, q)
                       if "/auth/me" in q.url or "/admin/" in q.url or "/healthz/" in q.url
                       else r.continue_())
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
    """/admin/updates/status answers 403 to anyone else, and the tile it badges
    is superuser-only — so an operator's portal must not call it at all."""
    p = portal.open(role="operator", status=_apps(available_count=2))
    assert portal.calls == [], portal.calls
    assert p.query_selector("#card-admin").is_hidden()


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
# The superuser-only Administration tile was rendering for every signed-in user,
# and the identity chip appeared before /auth/me had answered.
def test_the_administration_tile_is_really_hidden_from_a_non_superuser(portal):
    p = portal.open(role="operator", status=_apps(available_count=2))
    card = p.query_selector("#card-admin")
    assert card.get_attribute("hidden") is not None, "the markup stopped hiding it"
    assert card.is_hidden(), "it carries hidden= and renders anyway"


def test_an_auditor_does_not_see_it_either(portal):
    p = portal.open(role="auditor", status=_apps(available_count=2))
    assert p.query_selector("#card-admin").is_hidden()


def test_a_superuser_does_see_it(portal):
    """The rule must hide what is hidden without hiding what is not."""
    p = portal.open(role="superuser", status=_apps(available_count=0))
    assert p.query_selector("#card-admin").is_visible()


def test_the_identity_chip_appears_only_once_someone_is_known(portal):
    """Same inert-hidden bug: it showed before /auth/me resolved."""
    p = portal.open(role="superuser", status=_apps(available_count=0))
    assert p.query_selector("#userbox").is_visible()
