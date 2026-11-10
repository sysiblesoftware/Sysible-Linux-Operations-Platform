"""Flashback and Baseline have their own sections — and those sections must name
services that exist.

Reported: "Baseline and flashback should have their own sections on the updates
screens too." They now do, and the thing that makes those sections safe is that
a module's KEY IS its compose service name. That is what lets the updater turn
a section's Restart button into `docker compose restart baseline` without any
part of the argv coming from the request, and what lets one `compose ps` feed
every section's state.

Which means the same name is written down in three places that cannot import
each other:

  * docker-compose.yml          — the services that actually exist
  * updater/backend/apps.py     — MODULES, which the actions are derived from
  * idp/app.py                  — _SKELETON_MODULES, the server-rendered page

A rename in one of them is silent: the section keeps rendering, its state
collapses to "No container", and its buttons start refusing. These tests make
that rename loud instead.

Run: pytest tests/    (from the repo root)
"""
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _compose_services() -> set[str]:
    """Top-level service names, read without a YAML dependency: the services:
    block's two-space keys, stopping at the next top-level key."""
    text = open(os.path.join(REPO, "docker-compose.yml"), encoding="utf-8").read()
    body = text.split("\nservices:", 1)[1]
    body = re.split(r"\n(?=[a-zA-Z])", body)[0]
    return set(re.findall(r"^  ([a-z0-9][a-z0-9_-]*):", body, re.M))


# Every product has a MODULES entry now, not just SLOP, which is what gives all
# four rows on Software & services the same shape. Only SLOP's services are
# declared by THIS repository's compose file, though; the other three are
# declared in their own repositories, which this test cannot read.
#
# So the pair is split. The strict check below covers SLOP. For the other three,
# the compose file is read wherever it can be found on this machine — a sibling
# checkout, the installed tree under SYSIBLE_SRC_DIR, or a vendored apps/<key>/ —
# and when none of those exist the expected name is still pinned here, so a
# rename in apps.py alone cannot pass unnoticed. The other half of the pair lives
# in each app's own repository, as a test that fails there when its compose
# service is renamed.
_OTHER_PRODUCTS = {
    "controller": ("controller", ("sysible-controller", "Sysible-Controller")),
    "slep": ("slep", ("sysible-linux-engineering-platform",)),
    "connect": ("connect", ("sysible-connect",)),
}

_COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml",
                  "compose.yml", "compose.yaml")


def _find_compose(key: str) -> str | None:
    """That product's compose file on this machine, or None."""
    import os
    roots = []
    # Vendored into this repo (see the apps/ layout), then siblings of it, then
    # the installed tree.
    roots.append(os.path.join(REPO, "apps", key))
    parent = os.path.dirname(REPO)
    for dirname in _OTHER_PRODUCTS[key][1]:
        roots.append(os.path.join(parent, dirname))
        roots.append(os.path.join(
            os.environ.get("SYSIBLE_SRC_DIR", "/opt/sysible-src"), dirname))
    for root in roots:
        for sub in ("", "deploy", os.path.join("deploy", "docker")):
            for name in _COMPOSE_NAMES:
                cand = os.path.join(root, sub, name)
                if os.path.isfile(cand):
                    return cand
    return None


def _services_in(path: str) -> set[str]:
    text = open(path, encoding="utf-8").read()
    body = text.split("\nservices:", 1)[1]
    body = re.split(r"\n(?=[a-zA-Z])", body)[0]
    return set(re.findall(r"^  ([a-z0-9][a-z0-9_-]*):", body, re.M))


def test_every_slop_module_key_is_a_real_compose_service(app_module):
    services = _compose_services()
    assert "baseline" in services and "flashback" in services, \
        f"the compose file this test reads has no baseline/flashback: {sorted(services)}"
    with app_module("updater", "backend.apps") as apps:
        for key, label, _detail in apps.MODULES["slop"]:
            assert key in services, (
                f"{label} is listed inside slop as service {key!r}, which is "
                f"not in docker-compose.yml ({sorted(services)}). Its card "
                f"would render with no state and every button would refuse.")


@pytest.mark.parametrize("product", sorted(_OTHER_PRODUCTS))
def test_another_products_module_key_is_its_compose_service(app_module, product):
    expected, _dirs = _OTHER_PRODUCTS[product]
    with app_module("updater", "backend.apps") as apps:
        keys = [k for k, _l, _d in apps.MODULES.get(product, ())]
    assert keys == [expected], (
        f"{product} declares services {keys} on Software & services; this test "
        f"knows it as {[expected]}. If the product really gained or renamed a "
        f"service, change it HERE as well — and in that product's own repo, "
        f"where the compose file is.")
    path = _find_compose(product)
    if path is None:
        pytest.skip(f"no {product} checkout on this machine to read; the name is "
                    f"pinned above and verified in that product's own repository")
    found = _services_in(path)
    assert found == {expected}, (
        f"{path} declares {sorted(found)}, but Software & services gives "
        f"{product} one card keyed {expected!r}. A card whose key is not a "
        f"compose service renders with no state and refuses every button.")


def test_no_slop_compose_service_is_left_without_a_section(app_module):
    """The sections ARE the inventory. A service that ships in the stack and has
    no section is a module an operator cannot see, which is the complaint this
    whole screen change came from."""
    with app_module("updater", "backend.apps") as apps:
        listed = set(apps.module_keys("slop"))
    missing = _compose_services() - listed
    assert not missing, (
        f"these SLOP services have no section on the updates screen: {sorted(missing)}")


def test_the_idp_skeleton_and_the_updater_agree(app_module):
    """The IdP cannot import the updater's package, so it keeps its own copy for
    the server-rendered page. The updater's answer replaces it on the first poll
    — so the only failure mode is drift, and it looks like a section that
    silently changes name when the page finishes loading."""
    with app_module("updater", "backend.apps") as apps, \
            app_module("idp", "app") as idp:
        for product, mods in idp._SKELETON_MODULES.items():
            theirs = {k: lbl for k, lbl, _d in apps.MODULES.get(product, ())}
            mine = dict(mods)
            assert list(mine) == list(theirs), (
                f"{product}: the skeleton lists {list(mine)} and the updater "
                f"{list(theirs)} — the sections would reorder or change on first poll")
            for k, lbl in mine.items():
                assert lbl == theirs[k], (
                    f"{product}/{k}: skeleton says {lbl!r}, updater says {theirs[k]!r}")


def test_a_module_is_not_separately_updatable(app_module):
    """One git tree, one compose file. An "Update Flashback" button would rebuild
    the whole stack while claiming to touch one service, so there is deliberately
    no per-module update — in the allowlist, in the updater's routes, or in the
    IdP's client."""
    with app_module("updater", "backend.apps") as apps:
        for product, mods in apps.MODULES.items():
            for key, _lbl, _d in mods:
                # A product filed inside ANOTHER product. That is the real
                # contradiction: it would get an updatable row of its own AND a
                # card inside a row that rebuilds it, so two controls would claim
                # the same git tree.
                #
                # A product sharing a name with its OWN single service is not
                # that, and is the normal case: the Controller's compose service
                # is called `controller` because the project has one. The check
                # used to be a flat "no module key is in the allowlist", which
                # was equivalent while SLOP was the only product with services
                # declared — and started failing the moment every product
                # declared its own, for a name collision that means nothing.
                if key == product:
                    continue
                assert key not in apps.ALLOWLIST, (
                    f"{key} is a card inside {product} AND its own updatable "
                    f"product — updating {product} would rebuild it behind the "
                    f"back of its own row")
    updater_src = open(os.path.join(REPO, "updater/backend/app.py"), encoding="utf-8").read()
    assert "/api/update/{key}/{module}" not in updater_src
    idp_src = open(os.path.join(REPO, "idp/updates.py"), encoding="utf-8").read()
    assert "module" in idp_src, "the IdP has no module-scoped client call at all"
    assert "/api/update/" in idp_src
    assert not re.search(r"/api/update/\{?[a-z_]*\}?/\{?module", idp_src), \
        "the IdP builds a per-module UPDATE url"
