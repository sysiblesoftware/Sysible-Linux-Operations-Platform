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
_UNKNOWN_CB = {"configured": None, "reason": None}


def _fake_fleet(monkeypatch, hosts, note=None, cb=None):
    import backend.controller as ctrl
    monkeypatch.setattr(ctrl, "list_hosts",
                        lambda identity: (hosts, note, cb or _UNKNOWN_CB))


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


# ---- "Back up now" and environment grouping --------------------------------
# The console showed neither: no way to trigger a capture, and a flat list of
# opaque host ids with no indication of which environment a box was in. Both are
# things the EE D3lorean panel has.
def test_the_host_list_carries_the_environment(cl, monkeypatch):
    _fake_fleet(monkeypatch, [
        {"host_id": "h1", "label": "web1", "environment": "prod", "address": "10.0.0.1"},
        {"host_id": "h2", "label": "db1", "environment": "", "address": "10.0.0.2"},
    ])
    d = cl.get("/api/hosts", headers=GOOD).json()
    by = {h["host_id"]: h for h in d["hosts"]}
    assert by["h1"]["environment"] == "prod" and by["h1"]["address"] == "10.0.0.1"
    assert by["h2"]["environment"] == ""          # the console groups these as Unassigned
    assert d["can_request"] is True


def test_a_backup_request_reaches_the_controller(cl, monkeypatch):
    import backend.controller as ctrl
    seen = {}

    def fake(identity, host_id):
        seen["host"] = host_id
        seen["user"] = identity.user
        return True, "Requested — the host captures on its next check-in."
    monkeypatch.setattr(ctrl, "request_capture", fake)
    r = cl.post("/api/hosts/h1/backup-now", headers=GOOD)
    assert r.status_code == 200, r.text
    assert seen == {"host": "h1", "user": "admin"}
    # The wording must not imply the snapshot already happened: an agent is
    # outbound-only, so this is a request it picks up on its next poll.
    assert "next check-in" in r.json()["message"]


def test_a_backup_request_is_a_write_and_an_auditor_cannot_make_one(cl, monkeypatch):
    import backend.controller as ctrl
    monkeypatch.setattr(ctrl, "request_capture", lambda i, h: (True, "ok"))
    r = cl.post("/api/hosts/h1/backup-now",
                headers={**GOOD, "X-Sysible-Role": "auditor"})
    assert r.status_code == 403
    assert "operator or superuser" in r.json()["detail"]


def test_a_backup_request_needs_an_identity(cl):
    assert cl.post("/api/hosts/h1/backup-now").status_code == 401


def test_a_failed_backup_request_is_reported_not_swallowed(cl, monkeypatch):
    import backend.controller as ctrl
    monkeypatch.setattr(ctrl, "request_capture",
                        lambda i, h: (False, "could not reach the Controller (ConnectError)"))
    r = cl.post("/api/hosts/h1/backup-now", headers=GOOD)
    assert r.status_code == 502
    assert "ConnectError" in r.json()["detail"]


def test_a_backup_request_is_audited(cl, monkeypatch):
    """It causes a change on a host, so it belongs in the trail."""
    import backend.controller as ctrl
    monkeypatch.setattr(ctrl, "request_capture", lambda i, h: (True, "ok"))
    cl.post("/api/hosts/h1/backup-now", headers=GOOD)
    entries = cl.get("/api/audit", headers=GOOD).json()["entries"]
    assert any(e.get("action") == "request-backup" and e.get("actor") == "admin"
               for e in entries), entries


# ---- cross-host compare ----------------------------------------------------
# The per-host view answers "how did this file change over time". The question an
# operator actually arrives with after an incident is the other one: this box
# behaves differently — is its config different from the rest? That is the
# capability the EE panel has and this did not.
def _snap(cl, host, files, label=None):
    import backend.identity as ident
    ident._AGENT_TOKEN = "t"
    return cl.post("/api/agent/snapshot", headers={"Authorization": "Bearer t"},
                   json={"host_id": host, "label": label or host,
                         "files": [{"path": p, "content": c} for p, c in files]})


def test_compare_says_when_every_host_agrees(cl, monkeypatch):
    for h in ("h1", "h2", "h3"):
        _snap(cl, h, [("/etc/sshd_config", "Port 22\n")])
    d = cl.get("/api/compare", params={"path": "/etc/sshd_config"}, headers=GOOD).json()
    assert d["distinct"] == 1                      # one content across the fleet
    assert all(h["same"] for h in d["hosts"])
    assert d["missing"] == []


def test_compare_finds_the_odd_one_out(cl, monkeypatch):
    _snap(cl, "h1", [("/etc/sshd_config", "Port 22\n")])
    _snap(cl, "h2", [("/etc/sshd_config", "Port 22\n")])
    _snap(cl, "h3", [("/etc/sshd_config", "Port 2222\nPermitRootLogin yes\n")])
    d = cl.get("/api/compare", params={"path": "/etc/sshd_config", "baseline": "h1"},
               headers=GOOD).json()
    assert d["baseline"]["host_id"] == "h1"
    by = {h["host_id"]: h for h in d["hosts"]}
    assert by["h2"]["same"] is True
    assert by["h3"]["same"] is False
    assert d["distinct"] == 2


def test_compare_lists_hosts_that_have_no_copy_at_all(cl, monkeypatch):
    """"Absent" and "identical" are completely different findings, and a compare
    that silently omitted the hosts without the file would read as agreement."""
    _snap(cl, "h1", [("/etc/nginx.conf", "a\n")])
    _snap(cl, "h2", [("/etc/other", "b\n")])       # h2 exists but has no nginx.conf
    d = cl.get("/api/compare", params={"path": "/etc/nginx.conf"}, headers=GOOD).json()
    assert [m["host_id"] for m in d["missing"]] == ["h2"]


def test_compare_uses_the_NEWEST_version_on_each_host(cl, monkeypatch):
    """Comparing stale versions would report a difference that no longer exists."""
    _snap(cl, "h1", [("/etc/f", "old\n")])
    _snap(cl, "h2", [("/etc/f", "new\n")])
    _snap(cl, "h1", [("/etc/f", "new\n")])         # h1 catches up
    d = cl.get("/api/compare", params={"path": "/etc/f"}, headers=GOOD).json()
    assert d["distinct"] == 1, d
    assert all(h["same"] for h in d["hosts"])


def test_the_compare_diff_is_for_the_pair_asked_for(cl, monkeypatch):
    _snap(cl, "h1", [("/etc/f", "Port 22\n")])
    _snap(cl, "h2", [("/etc/f", "Port 2222\n")])
    r = cl.get("/api/compare/diff", params={"path": "/etc/f", "a": "h1", "b": "h2"},
               headers=GOOD)
    assert r.status_code == 200
    assert "-Port 22" in r.text and "+Port 2222" in r.text
    assert "h1:/etc/f" in r.text and "h2:/etc/f" in r.text


def test_a_diff_against_a_host_with_no_such_file_is_a_404_not_invented(cl, monkeypatch):
    _snap(cl, "h1", [("/etc/f", "a\n")])
    _snap(cl, "h2", [("/etc/other", "b\n")])
    r = cl.get("/api/compare/diff", params={"path": "/etc/f", "a": "h1", "b": "h2"},
               headers=GOOD)
    assert r.status_code == 404


def test_the_path_picker_puts_the_most_shared_paths_first(cl, monkeypatch):
    for h in ("h1", "h2", "h3"):
        _snap(cl, h, [("/etc/everywhere", "x\n")])
    _snap(cl, "h1", [("/etc/only-here", "y\n")])
    d = cl.get("/api/compare/paths", headers=GOOD).json()["paths"]
    assert d[0]["path"] == "/etc/everywhere" and d[0]["hosts"] == 3
    assert any(p["path"] == "/etc/only-here" and p["hosts"] == 1 for p in d)


def test_compare_needs_an_identity(cl):
    assert cl.get("/api/compare", params={"path": "/etc/f"}).status_code == 401
    assert cl.get("/api/compare/paths").status_code == 401


# ---- fleet-wide backup -----------------------------------------------------
def test_backing_up_all_hosts_asks_each_one(cl, monkeypatch):
    import backend.controller as ctrl
    asked = []
    monkeypatch.setattr(ctrl, "list_hosts", lambda i: (
        [{"host_id": "h1", "label": "a"}, {"host_id": "h2", "label": "b"}],
        None, _UNKNOWN_CB))
    monkeypatch.setattr(ctrl, "request_capture",
                        lambda i, h: (asked.append(h) or (True, "ok")))
    r = cl.post("/api/backup-now", headers=GOOD, json={"host_ids": "all"})
    assert r.status_code == 200, r.text
    assert sorted(asked) == ["h1", "h2"]
    assert r.json()["requested"] == 2


def test_one_unreachable_host_does_not_cost_the_batch(cl, monkeypatch):
    import backend.controller as ctrl
    monkeypatch.setattr(ctrl, "request_capture",
                        lambda i, h: (False, "unreachable") if h == "bad" else (True, "ok"))
    r = cl.post("/api/backup-now", headers=GOOD, json={"host_ids": ["good", "bad"]})
    assert r.status_code == 200
    d = r.json()
    assert d["requested"] == 1
    assert [f["host_id"] for f in d["failed"]] == ["bad"]
    assert "1 could not be asked" in d["message"]


def test_a_fleet_backup_is_a_write(cl, monkeypatch):
    r = cl.post("/api/backup-now", headers={**GOOD, "X-Sysible-Role": "auditor"},
                json={"host_ids": ["h1"]})
    assert r.status_code == 403


def test_the_default_baseline_is_the_majority_not_the_first_by_name(cl, monkeypatch):
    """Found in a browser: picking the alphabetically-first host made the single
    ODD host the reference whenever its label sorted first, so the two hosts that
    AGREED were reported as "differing" and the diff read inverted. The majority
    is what an operator means by "the rest of the fleet"."""
    _snap(cl, "h1", [("/etc/f", "Port 22\n")], label="web1")
    _snap(cl, "h2", [("/etc/f", "Port 22\n")], label="web2")
    _snap(cl, "h3", [("/etc/f", "Port 2222\n")], label="db1")   # sorts FIRST by label
    d = cl.get("/api/compare", params={"path": "/etc/f"}, headers=GOOD).json()
    assert d["baseline"]["host_id"] in ("h1", "h2"), d["baseline"]
    by = {h["host_id"]: h["same"] for h in d["hosts"]}
    assert by["h3"] is False
    assert all(v for k, v in by.items() if k != "h3")


def test_the_majority_baseline_is_deterministic_on_a_tie(cl, monkeypatch):
    """Two contents, one host each: the choice must still be stable, or the page
    would flip between reference hosts on every reload."""
    _snap(cl, "h1", [("/etc/f", "a\n")], label="web1")
    _snap(cl, "h2", [("/etc/f", "b\n")], label="db1")
    first = cl.get("/api/compare", params={"path": "/etc/f"}, headers=GOOD).json()
    again = cl.get("/api/compare", params={"path": "/etc/f"}, headers=GOOD).json()
    assert first["baseline"]["host_id"] == again["baseline"]["host_id"] == "h2"   # 'db1' < 'web1'


def test_an_explicit_baseline_still_wins(cl, monkeypatch):
    """"Differs from prod" and "differs from this one box" are different
    questions, so the operator can override the majority."""
    _snap(cl, "h1", [("/etc/f", "Port 22\n")], label="web1")
    _snap(cl, "h2", [("/etc/f", "Port 22\n")], label="web2")
    _snap(cl, "h3", [("/etc/f", "Port 2222\n")], label="db1")
    d = cl.get("/api/compare", params={"path": "/etc/f", "baseline": "h3"},
               headers=GOOD).json()
    assert d["baseline"]["host_id"] == "h3"
    assert not any(h["same"] for h in d["hosts"])


def test_a_host_enrolled_but_never_captured_is_listed_as_having_no_copy(cl, monkeypatch):
    """Also found in a browser. The store only knows hosts that HAVE history, so
    a host enrolled and never captured — exactly the one worth noticing — was
    silently absent from the comparison instead of flagged."""
    _snap(cl, "h1", [("/etc/f", "a\n")], label="web1")
    _fake_fleet(monkeypatch, [{"host_id": "h1", "label": "web1"},
                              {"host_id": "h9", "label": "new1"}])
    d = cl.get("/api/compare", params={"path": "/etc/f"}, headers=GOOD).json()
    assert [m["host_id"] for m in d["missing"]] == ["h9"]


# --------------------------------------------------------------------------- #
# Why a host has no backup
#
# "Back up now" reported success and then nothing happened, with no way to find
# out why. Two very different situations rendered as the same sentence: an agent
# whose build predates config backup (which will NEVER capture, however long you
# wait) and one that simply hasn't reached its next check-in. The Controller
# knows the difference — it records when each agent last asked for config-backup
# work — so the console must carry it through rather than flatten it.
# --------------------------------------------------------------------------- #
def test_a_host_whose_agent_never_asks_is_marked_incapable(cl, monkeypatch):
    import backend.controller as ctrl
    monkeypatch.setattr(ctrl, "list_hosts", lambda who: ([
        {"host_id": "old", "label": "old-box", "environment": "dev", "address": "10.0.0.1",
         "capture_capable": False, "online": True},
        {"host_id": "new", "label": "new-box", "environment": "dev", "address": "10.0.0.2",
         "capture_capable": True, "online": True},
    ], None, _UNKNOWN_CB))
    monkeypatch.setattr(ctrl, "configured", lambda: True)
    hosts = {h["host_id"]: h for h in cl.get("/api/hosts", headers=GOOD).json()["hosts"]}
    assert hosts["old"]["capture_capable"] is False
    assert hosts["new"]["capture_capable"] is True


def test_stored_history_proves_capability_whatever_the_controller_says(cl, monkeypatch):
    """A Controller that restarted has not seen a poll since, so it reports the
    host as never having asked. A host with stored snapshots has demonstrably
    captured, and must not be told its agent can't do this."""
    import backend.controller as ctrl
    import backend.store as store
    store.ingest_snapshot("h1", "web1", [{"path": "/etc/hosts", "content_b64": "eA=="}])
    monkeypatch.setattr(ctrl, "list_hosts", lambda who: ([
        {"host_id": "h1", "label": "web1", "environment": "", "address": "",
         "capture_capable": False, "online": True},
    ], None, _UNKNOWN_CB))
    monkeypatch.setattr(ctrl, "configured", lambda: True)
    h1 = {h["host_id"]: h for h in cl.get("/api/hosts", headers=GOOD).json()["hosts"]}["h1"]
    assert h1["backed_up"] is True
    assert h1["capture_capable"] is True, "a host with history was called incapable"


# ---------------------------------------------------------------------------
# When the CONTROLLER is the fault, do not blame the agents.
#
# The per-host signal ("this agent has never asked for config-backup work") is
# only meaningful if the Controller can relay snapshots at all. On a Controller
# with no Flashback wiring NO agent ever asks, so every row rendered "agent
# doesn't do config backup — update this host's agent" — and the operator went
# and updated agents that were already current, which changed nothing.
# ---------------------------------------------------------------------------
_CB_OFF = {"configured": False, "reason": "this controller has no Flashback wiring"}
_CB_ON = {"configured": True, "reason": None}


def test_an_unwired_controller_is_reported_once_not_per_host(cl, monkeypatch):
    import backend.controller as ctrl
    monkeypatch.setattr(ctrl, "configured", lambda: True)
    _fake_fleet(monkeypatch, [
        {"host_id": "h1", "label": "a", "capture_capable": False, "online": True},
        {"host_id": "h2", "label": "b", "capture_capable": False, "online": True},
    ], cb=_CB_OFF)
    d = cl.get("/api/hosts", headers=GOOD).json()
    assert d["config_backup_configured"] is False
    assert "Flashback wiring" in (d["config_backup_reason"] or "")
    # None = unknown. Not False, which the console renders as "update this agent".
    assert [h["capture_capable"] for h in d["hosts"]] == [None, None]


def test_back_up_now_is_withdrawn_when_it_could_not_possibly_work(cl, monkeypatch):
    """Offering the button on a Controller that cannot relay a snapshot is the
    exact "Back up now does nothing" complaint, one layer up."""
    import backend.controller as ctrl
    monkeypatch.setattr(ctrl, "configured", lambda: True)
    _fake_fleet(monkeypatch, [{"host_id": "h1", "label": "a", "capture_capable": False}],
                cb=_CB_OFF)
    assert cl.get("/api/hosts", headers=GOOD).json()["can_request"] is False


def test_a_wired_controller_still_names_the_stale_agent(cl, monkeypatch):
    """The per-host verdict is suppressed only when it would be meaningless."""
    import backend.controller as ctrl
    monkeypatch.setattr(ctrl, "configured", lambda: True)
    _fake_fleet(monkeypatch, [
        {"host_id": "old", "label": "a", "capture_capable": False, "online": True},
        {"host_id": "new", "label": "b", "capture_capable": True, "online": True},
    ], cb=_CB_ON)
    hosts = {h["host_id"]: h for h in cl.get("/api/hosts", headers=GOOD).json()["hosts"]}
    assert hosts["old"]["capture_capable"] is False
    assert hosts["new"]["capture_capable"] is True
    assert cl.get("/api/hosts", headers=GOOD).json()["can_request"] is True


def test_an_older_controller_that_says_nothing_is_unknown_not_broken(cl, monkeypatch):
    """A Controller predating the flag must not be reported as unwired — that
    would replace one wrong diagnosis with another."""
    import backend.controller as ctrl
    monkeypatch.setattr(ctrl, "configured", lambda: True)
    _fake_fleet(monkeypatch, [{"host_id": "h1", "label": "a", "capture_capable": True}])
    d = cl.get("/api/hosts", headers=GOOD).json()
    assert d["config_backup_configured"] is None
    assert d["can_request"] is True
    assert d["hosts"][0]["capture_capable"] is True


def test_history_still_wins_over_an_unwired_controller(cl, monkeypatch):
    """A host with stored snapshots demonstrably captured at some point; the
    banner explains the present, but its rows must not be downgraded."""
    import backend.controller as ctrl
    import backend.store as store
    store.ingest_snapshot("h1", "web1", [{"path": "/etc/hosts", "content_b64": "eA=="}])
    monkeypatch.setattr(ctrl, "configured", lambda: True)
    _fake_fleet(monkeypatch, [{"host_id": "h1", "label": "web1", "capture_capable": False}],
                cb=_CB_OFF)
    h1 = {h["host_id"]: h for h in cl.get("/api/hosts", headers=GOOD).json()["hosts"]}["h1"]
    assert h1["backed_up"] is True and h1["capture_capable"] is True


# ---------------------------------------------------------------------------
# "Back up now" builds a Controller URL from a caller-supplied host id, and that
# request carries the platform shared secret. /api/backup-now takes the ids from
# a JSON LIST, so nothing constrains them the way a path parameter would — an
# unvalidated "../../whatever" collapses before the request leaves the process,
# and the operator gets to POST to arbitrary Controller endpoints as the gateway.
# ---------------------------------------------------------------------------
def _capture_url(monkeypatch):
    import backend.controller as ctrl
    seen = []

    class _Resp:
        status_code = 200

    class _C:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, headers=None):
            seen.append(url)
            return _Resp()
    monkeypatch.setattr(ctrl, "configured", lambda: True)
    monkeypatch.setattr(ctrl.httpx, "Client", lambda **kw: _C())
    return seen


def test_a_real_host_id_is_still_asked(cl, monkeypatch):
    seen = _capture_url(monkeypatch)
    r = cl.post("/api/hosts/web-1.example_01/backup-now", headers=GOOD)
    assert r.status_code == 200, r.text
    assert seen and seen[0].endswith("/api/host/web-1.example_01/backup-now")


@pytest.mark.parametrize("hid", [
    "../../api/controller-restart",
    "x/../../admin",
    "a?force=1",
    "a#frag",
    "has space",
    "/etc/passwd",
    "x" * 129,
])
def test_a_forged_host_id_never_becomes_a_controller_request(cl, monkeypatch, hid):
    """Through the LIST endpoint, which is the one with no path-parameter to hide
    behind."""
    seen = _capture_url(monkeypatch)
    r = cl.post("/api/backup-now", headers=GOOD, json={"host_ids": [hid]})
    assert r.status_code == 200, r.text
    assert seen == [], f"{hid!r} was sent to the Controller as {seen}"
    assert r.json()["requested"] == 0
    assert r.json()["failed"][0]["message"] == "not a valid host id"
