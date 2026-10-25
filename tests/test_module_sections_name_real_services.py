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
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _compose_services() -> set[str]:
    """Top-level service names, read without a YAML dependency: the services:
    block's two-space keys, stopping at the next top-level key."""
    text = open(os.path.join(REPO, "docker-compose.yml"), encoding="utf-8").read()
    body = text.split("\nservices:", 1)[1]
    body = re.split(r"\n(?=[a-zA-Z])", body)[0]
    return set(re.findall(r"^  ([a-z0-9][a-z0-9_-]*):", body, re.M))


def _updater_modules():
    sys.path.insert(0, os.path.join(REPO, "updater"))
    from backend import apps
    return apps


def _idp_modules():
    sys.path.insert(0, os.path.join(REPO, "idp"))
    import app as idp
    return idp


def test_every_module_key_is_a_real_compose_service():
    apps = _updater_modules()
    services = _compose_services()
    assert "baseline" in services and "flashback" in services, \
        f"the compose file this test reads has no baseline/flashback: {sorted(services)}"
    for product, mods in apps.MODULES.items():
        for key, label, _detail in mods:
            assert key in services, (
                f"{label} is listed inside {product} as service {key!r}, which is not "
                f"in docker-compose.yml ({sorted(services)}). Its section would render "
                f"with no state and every button would refuse.")


def test_no_compose_service_is_left_without_a_section():
    """The sections ARE the inventory. A service that ships in the stack and has
    no section is a module an operator cannot see, which is the complaint this
    whole screen change came from."""
    apps = _updater_modules()
    listed = set(apps.module_keys("slop"))
    missing = _compose_services() - listed
    assert not missing, (
        f"these SLOP services have no section on the updates screen: {sorted(missing)}")


def test_the_idp_skeleton_and_the_updater_agree():
    """The IdP cannot import the updater's package, so it keeps its own copy for
    the server-rendered page. The updater's answer replaces it on the first poll
    — so the only failure mode is drift, and it looks like a section that
    silently changes name when the page finishes loading."""
    apps, idp = _updater_modules(), _idp_modules()
    for product, mods in idp._SKELETON_MODULES.items():
        theirs = {k: lbl for k, lbl, _d in apps.MODULES.get(product, ())}
        mine = dict(mods)
        assert list(mine) == list(theirs), (
            f"{product}: the skeleton lists {list(mine)} and the updater "
            f"{list(theirs)} — the sections would reorder or change on first poll")
        for k, lbl in mine.items():
            assert lbl == theirs[k], (
                f"{product}/{k}: skeleton says {lbl!r}, updater says {theirs[k]!r}")


def test_a_module_is_not_separately_updatable():
    """One git tree, one compose file. An "Update Flashback" button would rebuild
    the whole stack while claiming to touch one service, so there is deliberately
    no per-module update — in the allowlist, in the updater's routes, or in the
    IdP's client."""
    apps = _updater_modules()
    for product, mods in apps.MODULES.items():
        for key, _lbl, _d in mods:
            assert key not in apps.ALLOWLIST, (
                f"{key} is both a module of {product} and its own updatable "
                f"product — one of the two is wrong")
    updater_src = open(os.path.join(REPO, "updater/backend/app.py"), encoding="utf-8").read()
    assert "/api/update/{key}/{module}" not in updater_src
    idp_src = open(os.path.join(REPO, "idp/updates.py"), encoding="utf-8").read()
    assert "module" in idp_src, "the IdP has no module-scoped client call at all"
    assert "/api/update/" in idp_src
    assert not re.search(r"/api/update/\{?[a-z_]*\}?/\{?module", idp_src), \
        "the IdP builds a per-module UPDATE url"
