"""Sysible Flashback — the gateway trust boundary and how it REFUSES.

Flashback has no login of its own: it trusts the identity the SLOP gateway stamps,
guarded by a shared secret. These tests pin both halves of that:

  * the boundary itself — a request without the secret is never authenticated,
    whatever X-Sysible-User it claims;
  * the refusal — a BROWSER gets a page naming which wiring fault occurred (a bare
    {"detail":"Not signed in."} made the portal tile look simply broken), while API
    and agent callers keep the JSON contract. The page must never echo the secret.

Run: pip install -r ../requirements.txt pytest ; pytest   (from the flashback/ dir)
"""
import importlib
import os
import re
import sys
import warnings

import pytest

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

SECRET = "test-shared-secret"
BROWSER = {"Accept": "text/html,application/xhtml+xml"}
GOOD = {"X-Sysible-Auth": SECRET, "X-Sysible-User": "admin", "X-Sysible-Role": "superuser"}


@pytest.fixture()
def cl(tmp_path):
    from starlette.testclient import TestClient

    os.environ.update(
        SYSIBLE_FLASHBACK_TRUST_GATEWAY_AUTH="1",
        SYSIBLE_SSO_SHARED_SECRET=SECRET,
        SYSIBLE_FLASHBACK_DATA=str(tmp_path),
    )
    for mod in ("backend.identity", "backend.store", "backend.app"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    import backend.app as m
    m = importlib.reload(m)
    with TestClient(m.app, base_url="http://slop.lan") as c:
        yield c


def _why(resp) -> str:
    m = re.search(r"<div class=why>(.*?)</div>", resp.text, re.S)
    return m.group(1) if m else ""


# ---- the trust boundary ----------------------------------------------------
def test_claimed_identity_without_the_secret_is_not_trusted(cl):
    # The whole boundary in one test: anyone can SEND X-Sysible-User; only the
    # gateway can send the matching secret.
    r = cl.get("/api/hosts", headers={"X-Sysible-User": "admin", "X-Sysible-Role": "superuser"})
    assert r.status_code == 401
    r = cl.get("/api/hosts", headers={**GOOD, "X-Sysible-Auth": SECRET + "x"})
    assert r.status_code == 401
    assert cl.get("/api/hosts", headers=GOOD).status_code == 200


def test_role_must_be_a_known_one(cl):
    r = cl.get("/api/hosts", headers={**GOOD, "X-Sysible-Role": "root"})
    assert r.status_code == 401


def test_auditor_is_read_only(cl):
    aud = {**GOOD, "X-Sysible-User": "aud", "X-Sysible-Role": "auditor"}
    assert cl.get("/api/hosts", headers=aud).status_code == 200
    r = cl.post("/api/hosts/h1/restore", json={"path": "/etc/hosts", "sha": "deadbeef"}, headers=aud)
    assert r.status_code == 403


# ---- how it refuses --------------------------------------------------------
def test_api_callers_still_get_json(cl):
    r = cl.get("/api/hosts")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"detail": "Not signed in."}


def test_browser_gets_a_page_naming_the_wiring_fault(cl):
    # No proof at all → reached directly, or the gateway isn't stamping this route.
    r = cl.get("/", headers=BROWSER)
    assert r.status_code == 401 and r.headers["content-type"].startswith("text/html")
    assert "no gateway proof header" in _why(r)

    # Proof present but wrong → the two ends disagree on the shared secret.
    r = cl.get("/", headers={**BROWSER, "X-Sysible-Auth": "wrong"})
    assert "SYSIBLE_SSO_SHARED_SECRET" in _why(r)

    # Proof good, no identity → forward_auth isn't copying the IdP's headers.
    r = cl.get("/", headers={**BROWSER, "X-Sysible-Auth": SECRET})
    assert "asserted no user" in _why(r)

    # Proof good, unusable role.
    r = cl.get("/", headers={**BROWSER, "X-Sysible-Auth": SECRET,
                             "X-Sysible-User": "bob", "X-Sysible-Role": "wizard"})
    assert "unusable role" in _why(r)


def test_the_refusal_never_echoes_the_secret(cl):
    for extra in ({}, {"X-Sysible-Auth": "wrong"}, {"X-Sysible-Auth": SECRET}):
        r = cl.get("/", headers={**BROWSER, **extra})
        assert SECRET not in r.text


def test_signed_in_browser_gets_the_console(cl):
    r = cl.get("/", headers={**BROWSER, **GOOD})
    assert r.status_code == 200 and "config time machine" in r.text


def test_health_stays_open_for_the_portal_dot(cl):
    # The portal polls this before/around sign-in, so it must not require identity.
    r = cl.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


# ---- browser-tab icon ------------------------------------------------------
# The tab came up BLANK next to the portal's and Connect's: nothing here declared
# an icon, so the browser fell back to /favicon.ico at the ORIGIN ROOT — which
# behind the gateway is the portal's path, not ours.
def test_the_console_declares_a_tab_icon(cl):
    html = cl.get("/", headers={**GOOD, **BROWSER}).text
    assert "rel=icon" in html and "favicon.svg" in html


def test_the_icon_href_is_relative_so_it_survives_the_gateway_prefix(cl):
    """The gateway serves this app under /flashback and STRIPS the prefix. An
    absolute href would resolve to the portal's root and 404; a relative one
    resolves to /flashback/favicon.svg and reaches us."""
    html = cl.get("/", headers={**GOOD, **BROWSER}).text
    assert "href='/favicon.svg'" not in html
    assert "href='favicon.svg'" in html


def test_the_refusal_page_carries_the_icon_too(cl):
    """A caller with no identity gets the refusal page — the very page where a
    blank tab is most confusing, since that is what a wiring fault shows."""
    r = cl.get("/", headers=BROWSER)
    assert r.status_code == 401
    assert "favicon.svg" in r.text


def test_the_icon_is_served_and_is_the_flashback_mark(cl):
    r = cl.get("/favicon.svg")
    assert r.status_code == 200
    assert "image/svg+xml" in r.headers["content-type"]
    # The canonical mark from portal/marks/flashback.svg, not some other app's.
    assert 'aria-label="Sysible Flashback"' in r.text
    assert "#6ddb73" in r.text                     # the family's brand-green ring


def test_the_icon_needs_no_identity(cl):
    """It is fetched for the REFUSAL page too, by a caller who has no identity
    yet. Gating it would leave exactly that tab blank. An icon is not a secret."""
    r = cl.get("/favicon.svg")                     # no GOOD headers at all
    assert r.status_code == 200


# ---- the agent API fails closed -------------------------------------------
# agent_auth_ok() used to return True when no token was configured, on the
# reasoning that an unconfigured deployment is a dev one. But these endpoints take
# host_id from the CALLER: "open" meant anyone who could reach the service could
# read any host's stored configuration and queue a restore that overwrites a file
# on it. A missing token is a misconfiguration, never a grant.
def test_agent_endpoints_are_closed_when_no_token_is_configured(cl, monkeypatch):
    import backend.identity as ident
    monkeypatch.setattr(ident, "_AGENT_TOKEN", "")
    r = cl.post("/api/agent/snapshot", json={"host_id": "h", "files": []})
    assert r.status_code == 503
    # ...and it says WHICH fault, so a blank token is diagnosable rather than
    # looking identical to a wrong one.
    assert "SYSIBLE_FLASHBACK_AGENT_TOKEN" in r.json()["detail"]
    assert cl.get("/api/agent/restores", params={"host_id": "h"}).status_code == 503


def test_a_wrong_token_is_refused_when_one_is_configured(cl, monkeypatch):
    import backend.identity as ident
    monkeypatch.setattr(ident, "_AGENT_TOKEN", "the-real-token")
    r = cl.post("/api/agent/snapshot", headers={"Authorization": "Bearer wrong"},
                json={"host_id": "h", "files": []})
    assert r.status_code == 401


def test_the_right_token_is_accepted(cl, monkeypatch):
    """The other half: fail-closed must not mean fail-always."""
    import backend.identity as ident
    monkeypatch.setattr(ident, "_AGENT_TOKEN", "the-real-token")
    r = cl.post("/api/agent/snapshot", headers={"Authorization": "Bearer the-real-token"},
                json={"host_id": "h", "files": [{"path": "/etc/hosts", "content": "x"}]})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == 1


# ---- enrolled hosts appear before they have backed anything up -------------
# Listing only hosts WITH history made a correctly-wired fleet that had not
# captured yet look identical to a broken one — both rendered "No host has
# reported a config backup yet", which covered four different faults at once.
def _fake_fleet(monkeypatch, hosts, note=None):
    import backend.controller as ctrl
    monkeypatch.setattr(ctrl, "list_hosts", lambda identity: (hosts, note))


def test_enrolled_hosts_show_with_no_history_yet(cl, monkeypatch):
    _fake_fleet(monkeypatch, [{"host_id": "h1", "label": "web1"}])
    d = cl.get("/api/hosts", headers=GOOD).json()
    assert [h["host_id"] for h in d["hosts"]] == ["h1"]
    h = d["hosts"][0]
    assert h["backed_up"] is False and h["files"] == 0 and h["last_ts"] is None
    assert h["label"] == "web1"


def test_a_host_with_history_is_not_duplicated_by_the_import(cl, monkeypatch):
    import backend.identity as ident
    monkeypatch.setattr(ident, "_AGENT_TOKEN", "t")
    cl.post("/api/agent/snapshot", headers={"Authorization": "Bearer t"},
            json={"host_id": "h1", "files": [{"path": "/etc/hosts", "content": "x"}]})
    _fake_fleet(monkeypatch, [{"host_id": "h1", "label": "web1"}, {"host_id": "h2", "label": "web2"}])
    d = cl.get("/api/hosts", headers=GOOD).json()
    by_id = {h["host_id"]: h for h in d["hosts"]}
    assert len(d["hosts"]) == 2, d["hosts"]
    assert by_id["h1"]["backed_up"] is True and by_id["h1"]["files"] == 1
    assert by_id["h2"]["backed_up"] is False
    # Hosts WITH history sort first — the ones you can actually act on.
    assert d["hosts"][0]["host_id"] == "h1"


def test_an_unreachable_controller_costs_a_note_not_the_page(cl, monkeypatch):
    import backend.identity as ident
    monkeypatch.setattr(ident, "_AGENT_TOKEN", "t")
    cl.post("/api/agent/snapshot", headers={"Authorization": "Bearer t"},
            json={"host_id": "h1", "files": [{"path": "/etc/hosts", "content": "x"}]})
    _fake_fleet(monkeypatch, [], note="could not reach the Controller for its host list")
    d = cl.get("/api/hosts", headers=GOOD).json()
    assert [h["host_id"] for h in d["hosts"]] == ["h1"]     # stored history survives
    assert "could not reach the Controller" in d["note"]


def test_the_host_list_still_needs_an_identity(cl):
    assert cl.get("/api/hosts").status_code == 401


# ---- an enrichment must never take the service down ------------------------
# The host-import module imported httpx at module scope before httpx was in
# requirements.txt. backend.app imports that module, so the app failed to import,
# uvicorn never started, and EVERY Flashback page became a 502 at the gateway —
# a feature that only adds rows to a list took the whole service out.
def test_the_app_still_serves_when_httpx_is_missing(monkeypatch, tmp_path):
    """Simulate the dependency being absent and re-import the app from scratch."""
    import builtins
    import importlib
    import sys as _sys

    real_import = builtins.__import__

    def no_httpx(name, *a, **kw):
        if name == "httpx" or name.startswith("httpx."):
            raise ImportError("simulated: httpx not installed")
        return real_import(name, *a, **kw)

    os.environ.update(SYSIBLE_FLASHBACK_TRUST_GATEWAY_AUTH="1",
                      SYSIBLE_SSO_SHARED_SECRET=SECRET,
                      SYSIBLE_FLASHBACK_DATA=str(tmp_path))
    for m in [m for m in list(_sys.modules) if m.startswith("backend")]:
        del _sys.modules[m]
    monkeypatch.setattr(builtins, "__import__", no_httpx)
    try:
        app_mod = importlib.import_module("backend.app")     # must NOT raise
        ctrl = importlib.import_module("backend.controller")
        assert ctrl.httpx is None
        assert ctrl.configured() is False
        assert "httpx" in (ctrl.unavailable_reason() or "")
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)

    from starlette.testclient import TestClient
    with TestClient(app_mod.app, base_url="http://slop.lan") as c:
        # The console still loads, and the page says why the fleet is missing.
        d = c.get("/api/hosts", headers=GOOD).json()
        assert d["hosts"] == []
        assert "httpx" in d["note"]
        assert c.get("/api/health").status_code == 200
    for m in [m for m in list(_sys.modules) if m.startswith("backend")]:
        del _sys.modules[m]


def test_httpx_is_declared_in_requirements():
    """The other half: it must actually be installed, or the host import is off
    on every deployment and nobody sees the fleet."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "requirements.txt")) as fh:
        assert "httpx" in fh.read(), "backend/controller.py imports httpx"


def test_every_third_party_import_is_declared_in_requirements():
    """The general form of the 502 above: any module backend/ imports at top
    level must be stdlib, local, or in requirements.txt — otherwise the image
    builds fine and the service dies at startup."""
    import ast
    import pathlib
    import sys as _sys

    here = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    reqs = (here / "requirements.txt").read_text().lower()
    local = {p.stem for p in (here / "backend").glob("*.py")}
    stdlib = set(getattr(_sys, "stdlib_module_names", ()))

    undeclared = {}
    for src in (here / "backend").glob("*.py"):
        tree = ast.parse(src.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for n in names:
                if n in stdlib or n in local or n == "backend":
                    continue
                # uvicorn/fastapi bring these; a name in requirements is fine too.
                if n.lower() in reqs or n.lower() in ("starlette", "pydantic"):
                    continue
                undeclared.setdefault(n, set()).add(src.name)
    assert not undeclared, f"imported but not in requirements.txt: {undeclared}"
