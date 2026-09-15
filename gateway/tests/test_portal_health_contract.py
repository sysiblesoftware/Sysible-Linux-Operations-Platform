"""Every dot on the portal must have somewhere to ask, and every route a dot.

The portal's live status dots depend on three separate files agreeing:

    portal/index.html   renders  <span class="dot" data-health="slep">
    portal/app.js       fetches  /healthz/slep
    gateway/Caddyfile   handles  /healthz/slep  ->  the app's own health endpoint

Nothing tied them together. A card added to the portal without a gateway route
gives a dot that answers 404 and sits on "not reachable" forever — an app that is
running reads as down, which is worse than no dot at all. A route with no dot is
dead config. And app.js carried its OWN hard-coded list of apps to poll, so it
could drift from the dots the page renders in either direction.

app.js now derives the list from the rendered dots, which removes one of the three
places; these pin the other two against each other.
"""
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CADDYFILE = os.path.join(ROOT, "gateway", "Caddyfile")
INDEX = os.path.join(ROOT, "portal", "index.html")
APPJS = os.path.join(ROOT, "portal", "app.js")


@pytest.fixture(scope="module")
def dots():
    html = open(INDEX, encoding="utf-8").read()
    return set(re.findall(r'data-health="([^"]+)"', html))


@pytest.fixture(scope="module")
def health_routes():
    caddy = open(CADDYFILE, encoding="utf-8").read()
    return set(re.findall(r"handle /healthz/([A-Za-z0-9_-]+)\s*\{", caddy))


def test_the_portal_renders_at_least_one_dot(dots):
    assert dots, "no health dots found — the selectors below would pass vacuously"


def test_every_dot_has_a_gateway_route(dots, health_routes):
    missing = sorted(dots - health_routes)
    assert not missing, (
        f"the portal shows a status dot for {missing} but the gateway has no "
        f"/healthz/<app> handler for it — the dot 404s and reads 'not reachable' "
        f"forever, so a running app looks down")


def test_every_gateway_route_has_a_dot(dots, health_routes):
    extra = sorted(health_routes - dots)
    assert not extra, (
        f"the gateway proxies /healthz/{extra} but no portal dot asks for it — "
        f"dead config, or a card that was dropped without its route")


def test_app_js_does_not_keep_its_own_list_of_apps(dots):
    """The drift this removes: a second list in JS that nothing compares to the
    first. The apps to poll must come from the dots the page rendered."""
    js = open(APPJS, encoding="utf-8").read()
    assert 'querySelectorAll(".dot[data-health]")' in js, \
        "app.js must read the apps to poll from the rendered dots"
    # No literal app-name array left behind to go stale.
    for app in dots:
        assert f'"{app}"' not in js, (
            f"app.js still names {app!r} literally; that is the list that drifted "
            f"from portal/index.html")


def test_polling_stops_when_the_portal_is_not_on_screen():
    """One proxied request per app every 15s, forever, in every tab left open —
    work the gateway and every app did for nobody."""
    js = open(APPJS, encoding="utf-8").read()
    assert "visibilitychange" in js and "clearInterval" in js, \
        "the health poll must pause while the tab is hidden"


def test_each_health_route_rewrites_to_a_real_health_path(health_routes):
    """A route that proxies /healthz/<app> straight through hits the app's ROOT,
    which behind SSO is the app itself — a 200 that means nothing."""
    caddy = open(CADDYFILE, encoding="utf-8").read()
    for app in health_routes:
        block = re.search(r"handle /healthz/" + re.escape(app) + r"\s*\{(.*?)\n\t\}",
                          caddy, re.S)
        assert block, f"could not read the /healthz/{app} block"
        body = block.group(1)
        assert re.search(r"rewrite \* /(api/health|healthz)\b", body), (
            f"/healthz/{app} must rewrite to the app's own health endpoint, "
            f"not proxy the bare path")
        assert "reverse_proxy" in body


def test_no_health_route_forwards_the_sso_session_cookie(health_routes):
    """These are PUBLIC (they answer before sign-in), so a leaky app on this path
    must never receive a live SSO token it could replay."""
    caddy = open(CADDYFILE, encoding="utf-8").read()
    for app in health_routes:
        block = re.search(r"handle /healthz/" + re.escape(app) + r"\s*\{(.*?)\n\t\}",
                          caddy, re.S)
        assert 'header_up Cookie "sysible_sso=' in block.group(1), \
            f"/healthz/{app} does not strip the IdP session cookie"
