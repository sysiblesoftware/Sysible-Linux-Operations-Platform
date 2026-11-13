"""Administration must cover every product the portal offers.

Reported, for the second time: "the administration panel does not have
everything together. Controller, slep and connect should all be under SLOP. I
told you to do this before and you didn't do it."

The page WAS built. What it was not was complete. The portal showed six
products; Administration -> Apps carried three. Baseline, Flashback and the
Visualizer had no administration there at all, on a page whose own subtitle
calls itself "each app's own administration, hosted here". Nothing failed,
nothing logged, and the only way to notice was to count the tiles on one page
and the chips on another.

That is what this test does, so it is noticed by a test run rather than by an
operator. Three things have to agree, in three files that cannot import each
other:

    portal/index.html     the tiles -- what a user is told exists
    idp/app.py            _APP_ADMIN -- what Administration can administer
    docker-compose.yml    the services -- what is actually running

Also pinned here: the url each product's SECTIONS live at. Administration used
to append "&tab=<section>" to every app's base url. That is how the Controller
spells it; SLEP spells it "?view=". Every SLEP section chip would have opened
the same page, with nothing to say so -- a tab strip that looks like it works.

Run: pytest tests/
"""
import ast
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
PORTAL = (REPO / "portal" / "index.html").read_text(encoding="utf-8")
IDP = (REPO / "idp" / "app.py").read_text(encoding="utf-8")


def _portal_products() -> set:
    """The products the portal offers, from its own tiles."""
    return set(re.findall(r'data-sub="([a-z0-9-]+)"', PORTAL))


def _app_admin() -> dict:
    """_APP_ADMIN, read out of the source rather than imported.

    Importing the IdP drags in its database, its session store and its
    settings; this test is about a literal.
    """
    tree = ast.parse(IDP)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "_APP_ADMIN" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("_APP_ADMIN is no longer a literal in idp/app.py")


def test_this_test_is_looking_at_something():
    """Both sides non-empty: a regex that stopped matching would make every
    assertion below vacuously true."""
    assert len(_portal_products()) >= 5, _portal_products()
    assert len(_app_admin()) >= 5, sorted(_app_admin())


def test_every_product_on_the_portal_can_be_administered_from_here():
    missing = _portal_products() - set(_app_admin())
    assert not missing, (
        f"the portal offers {sorted(missing)} and Administration cannot "
        f"administer them. A user is shown a product and then has to leave "
        f"Administration to configure it -- which is the whole complaint this "
        f"page exists to answer. Add them to _APP_ADMIN in idp/app.py.")


def test_administration_does_not_offer_a_product_that_is_not_there():
    """The other direction. A chip for something the portal does not ship is a
    link to a 404 with a product's name on it."""
    extra = set(_app_admin()) - _portal_products()
    assert not extra, f"Administration offers {sorted(extra)}, which the portal does not"


@pytest.mark.parametrize("key", sorted(_app_admin()))
def test_each_product_is_administered_at_a_path_the_gateway_serves(key):
    label, url, _tabs = _app_admin()[key]
    assert label.startswith("Sysible "), f"{key}: {label!r} is not a product name"
    assert url.startswith(f"/{key}/"), (
        f"{key} is administered at {url!r}, which is not that product's path")


@pytest.mark.parametrize("key", sorted(_app_admin()))
def test_a_product_with_sections_says_where_its_sections_live(key):
    """{tab} is the app's OWN query parameter, wherever it puts it. Without it
    every section chip opens the same page."""
    _label, url, tabs = _app_admin()[key]
    if tabs:
        assert "{tab}" in url, (
            f"{key} has {len(tabs)} sections but its url {url!r} has nowhere to "
            f"put one, so every section chip opens the same page")
        rendered = url.format(tab=tabs[0][0])
        assert "{" not in rendered, f"{key}: {rendered!r} still has a placeholder"
    else:
        assert "{tab}" not in url, (
            f"{key} has no sections but its url {url!r} expects one")


@pytest.mark.parametrize("key", sorted(_app_admin()))
def test_sections_are_distinct_and_named(key):
    _label, _url, tabs = _app_admin()[key]
    keys = [t for t, _l, _d in tabs]
    assert len(keys) == len(set(keys)), f"{key} repeats a section: {keys}"
    for t, lbl, desc in tabs:
        assert t and lbl and desc, f"{key}: section {t!r} is missing a label or a description"


def test_a_sections_url_is_one_the_app_can_actually_read():
    """The deep link has to match what the app's own code looks for.

    SLEP's console read nothing from the URL at all until its rail became
    ?view= links -- which is why it had no sections here. If that is reverted,
    these chips go back to opening the same page six times.
    """
    slep = REPO / "apps" / "slep" / "webgui" / "frontend" / "src" / "App.jsx"
    src = slep.read_text(encoding="utf-8")
    assert "viewFromSearch" in src and "'view'" in src, (
        "SLEP no longer reads ?view= from the URL, so Administration's SLEP "
        "sections cannot reach anything")
    nav_keys = set(re.findall(r"\{ key: '([a-z]+)'", src))
    _l, _u, tabs = _app_admin()["slep"]
    for t, lbl, _d in tabs:
        assert t in nav_keys, (
            f"Administration offers SLEP's '{lbl}' as ?view={t}, which is not "
            f"one of SLEP's views: {sorted(nav_keys)}")


def test_every_administered_product_is_a_service_that_exists():
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
    services = set(compose.get("services", {}))
    for key in _app_admin():
        assert key in services, (
            f"Administration offers {key}, which docker-compose.yml does not run")
