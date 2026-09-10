"""Tests for Sysible Visualizer: SSO trust, identity FORWARDING (the security-
critical bit), per-app normalisation, upstream-failure isolation, and headers."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SECRET = "shhh-gateway-secret"


@pytest.fixture
def mod(monkeypatch):
    import backend.identity as identity
    import backend.sources as sources
    monkeypatch.setattr(identity, "_TRUST_GATEWAY", True)
    monkeypatch.setattr(identity, "_SSO_SECRET", SECRET)
    monkeypatch.setattr(sources, "_SSO_SECRET", SECRET)
    import backend.app as app_mod
    return app_mod, sources


@pytest.fixture
def client(mod):
    return TestClient(mod[0].app)


def hdr(user="alice", role="operator", secret=SECRET):
    return {"X-Sysible-Auth": secret, "X-Sysible-User": user, "X-Sysible-Role": role}


# ---- trust boundary --------------------------------------------------------
def test_requires_gateway_secret(client):
    assert client.get("/api/apps").status_code == 401
    assert client.get("/api/apps", headers=hdr(secret="wrong")).status_code == 401
    assert client.get("/api/apps", headers=hdr()).status_code == 200


def test_unknown_role_is_refused(client):
    assert client.get("/api/apps", headers=hdr(role="root")).status_code == 401


def test_auditor_may_view(client):
    # Visualizer is read-only oversight — an auditor is a legitimate viewer.
    assert client.get("/api/apps", headers=hdr(role="auditor")).status_code == 200


# ---- identity forwarding (must NOT be spoofable, must NOT escalate) --------
def test_forwards_the_callers_own_identity_not_client_headers(mod, monkeypatch):
    app_mod, sources = mod
    seen = {}

    def fake_get(url, identity, params=None, want_json=True):
        seen["headers"] = sources._headers(identity)
        return ({"activity": []} if want_json else ""), None

    monkeypatch.setattr(sources, "_get", fake_get)
    c = TestClient(app_mod.app)
    # The browser tries to smuggle a superuser identity in extra headers; the
    # gateway-validated identity (alice/auditor) is what must go upstream.
    r = c.get("/api/activity", params={"app": "controller"},
              headers={**hdr(user="alice", role="auditor"),
                       "X-Sysible-User-Override": "root"})
    assert r.status_code == 200
    assert seen["headers"]["X-Sysible-User"] == "alice"
    assert seen["headers"]["X-Sysible-Role"] == "auditor"
    assert seen["headers"]["X-Sysible-Auth"] == SECRET


# ---- normalisation ---------------------------------------------------------
def test_controller_events_are_normalised(mod):
    app_mod, sources = mod

    def fake_get(url, identity, params=None, want_json=True):
        if url.endswith("/api/activity"):
            return {"activity": [{"id": 7, "timestamp": 1700000000.0, "username": "bob",
                                  "host": "web1", "description": "restart nginx",
                                  "command": "systemctl restart nginx"}]}, None
        return None, "not permitted for role 'operator' (403)"

    import backend.sources as s
    object.__setattr__(s, "_get", fake_get)
    c = TestClient(app_mod.app)
    d = c.get("/api/activity", params={"app": "controller"}, headers=hdr()).json()
    assert d["app"] == "controller" and d["label"] == "Sysible Controller"
    e = d["events"][0]
    assert (e["actor"], e["action"], e["target"]) == ("bob", "restart nginx", "web1")
    assert e["ts"] == 1700000000.0
    # A superuser-only upstream refusing an operator is a NOTE, not a hard error.
    assert any("not permitted" in n for n in d["notes"])
    assert d["errors"] == []


def test_one_dead_app_does_not_break_the_console(mod):
    app_mod, sources = mod

    def fake_get(url, identity, params=None, want_json=True):
        return None, "unreachable (ConnectError)"

    import backend.sources as s
    object.__setattr__(s, "_get", fake_get)
    c = TestClient(app_mod.app)
    d = c.get("/api/activity", params={"app": "slep"}, headers=hdr()).json()
    assert d["events"] == []
    assert any("unreachable" in m for m in d["errors"])   # reported, not raised


def test_connect_missing_audit_reads_as_a_note(mod):
    app_mod, sources = mod

    def fake_get(url, identity, params=None, want_json=True):
        return None, "endpoint not found (app may predate this feature)"

    import backend.sources as s
    object.__setattr__(s, "_get", fake_get)
    c = TestClient(app_mod.app)
    d = c.get("/api/activity", params={"app": "connect"}, headers=hdr()).json()
    assert d["events"] == [] and d["errors"] == []
    assert any("not found" in n for n in d["notes"])


def test_apps_are_separated_by_app(client):
    keys = [a["key"] for a in client.get("/api/apps", headers=hdr()).json()["apps"]]
    assert keys == ["controller", "slep", "connect", "flashback"]


def test_unknown_app_404s(client):
    assert client.get("/api/activity", params={"app": "nope"}, headers=hdr()).status_code == 404


# ---- hardening -------------------------------------------------------------
def test_security_headers_and_no_store(client):
    r = client.get("/api/health")
    # SAMEORIGIN: SLOP is one origin and Administration hosts app settings
    # in-page. Every other site is still refused.
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "no-store"   # it's other people's audit data


def test_oversized_body_rejected(client, mod):
    huge = mod[0]._MAX_REQUEST_BYTES + 1
    r = client.post("/api/activity", content=b"x",
                    headers={**hdr(), "Content-Length": str(huge)})
    assert r.status_code == 413


def test_health_is_public_for_the_portal_dot(client):
    assert client.get("/api/health").json()["service"] == "visualizer"


# ---- how it refuses --------------------------------------------------------
# A browser that lands here unauthenticated used to get a bare
# {"detail":"Not signed in."} — which from the portal tile just looks like the app
# is broken. Navigations now get a page naming the wiring fault; fetch()/API
# callers keep the JSON contract, and neither may echo the shared secret.
BROWSER = {"Accept": "text/html,application/xhtml+xml"}


def _why(resp):
    import re
    m = re.search(r"<div class=why>(.*?)</div>", resp.text, re.S)
    return m.group(1) if m else ""


def test_api_refusal_is_still_json(client):
    r = client.get("/api/apps")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"detail": "Not signed in."}


def test_browser_refusal_names_the_fault(client):
    r = client.get("/", headers=BROWSER)
    assert r.status_code == 401 and r.headers["content-type"].startswith("text/html")
    assert "no gateway proof header" in _why(r)

    r = client.get("/", headers={**BROWSER, "X-Sysible-Auth": "wrong"})
    assert "SYSIBLE_SSO_SHARED_SECRET" in _why(r)

    r = client.get("/", headers={**BROWSER, "X-Sysible-Auth": SECRET})
    assert "asserted no user" in _why(r)

    r = client.get("/", headers={**BROWSER, "X-Sysible-Auth": SECRET,
                                 "X-Sysible-User": "bob", "X-Sysible-Role": "wizard"})
    assert "unusable role" in _why(r)


def test_refusal_never_echoes_the_secret(client):
    for extra in ({}, {"X-Sysible-Auth": "wrong"}, {"X-Sysible-Auth": SECRET}):
        assert SECRET not in client.get("/", headers={**BROWSER, **extra}).text


def test_signed_in_browser_gets_the_console(client):
    r = client.get("/", headers={**BROWSER, **hdr()})
    assert r.status_code == 200 and "Sysible <b>Visualizer</b>" in r.text


# ---- fleet topology --------------------------------------------------------
# The map is assembled from five Controller endpoints. What matters at this level
# is that the caller's OWN identity goes to every one of them (so an auditor gets
# an auditor's fleet), that the expensive posture sweep is opt-in, and that a dead
# endpoint degrades the map instead of erroring the whole view.
def _fleet_upstreams(seen=None):
    def fake_get(url, identity, params=None, want_json=True):
        if seen is not None:
            seen.setdefault("urls", []).append(url)
            seen["headers"] = __import__("backend.sources", fromlist=["x"])._headers(identity)
        if url.endswith("/api/hosts"):
            return {"hosts": [
                {"id": "h1", "label": "kvm-1", "environment": "Labs", "type_text": "Agent"},
                {"id": "h2", "label": "vm-a", "environment": "Dev", "type_text": "Agent"},
            ]}, None
        if url.endswith("/api/fleet-health"):
            return {"hosts": [{"id": "h1", "online": True, "verdict": "OK",
                               "hyp": "kvm", "vms": 1, "vm_names": ["vm-a"]},
                              {"id": "h2", "online": True, "verdict": "OK"}]}, None
        if url.endswith("/api/agents"):
            return {"agents": [{"host_id": "h1", "hostname": "kvm-1", "ip": "10.0.0.9"}]}, None
        if url.endswith("/api/suppressions"):
            return {"suppressions": []}, None
        if url.endswith("/api/fleet-posture"):
            return {"hosts": [{"id": "h2", "flags": {"eol_os": True}, "posture": {}}]}, None
        return None, "unexpected"
    return fake_get


def test_topology_merges_the_fleet_and_forwards_identity(mod, monkeypatch):
    app_mod, sources = mod
    seen = {}
    monkeypatch.setattr(sources, "_get", _fleet_upstreams(seen))
    c = TestClient(app_mod.app)
    d = c.get("/api/topology", headers=hdr(user="alice", role="auditor")).json()

    assert [n["label"] for n in d["nodes"]] == ["kvm-1", "vm-a"]
    assert d["parents"] == {"vm-a": "kvm-1"}          # guest nests across environments
    assert d["counts"]["online"] == 2
    # Every upstream is queried as the real caller, never as the service itself.
    assert seen["headers"]["X-Sysible-User"] == "alice"
    assert seen["headers"]["X-Sysible-Role"] == "auditor"
    # …and the expensive sweep is NOT one of them by default.
    assert not any(u.endswith("/api/fleet-posture") for u in seen["urls"])
    assert d["posture"] is False


def test_topology_posture_is_opt_in(mod, monkeypatch):
    app_mod, sources = mod
    seen = {}
    monkeypatch.setattr(sources, "_get", _fleet_upstreams(seen))
    c = TestClient(app_mod.app)
    d = c.get("/api/topology", params={"posture": 1}, headers=hdr()).json()
    assert any(u.endswith("/api/fleet-posture") for u in seen["urls"])
    assert d["posture"] is True
    # The posture flag is what raises the critical ring on vm-a.
    assert [n["hasCrit"] for n in d["nodes"]] == [False, True]


def test_topology_survives_a_dead_overlay(mod, monkeypatch):
    app_mod, sources = mod

    def fake_get(url, identity, params=None, want_json=True):
        if url.endswith("/api/hosts"):
            return {"hosts": [{"id": "h1", "label": "web-1", "environment": "Prod"}]}, None
        return None, "unreachable (ConnectError)"

    monkeypatch.setattr(sources, "_get", fake_get)
    c = TestClient(app_mod.app)
    d = c.get("/api/topology", headers=hdr()).json()
    # The map still has its node; the failures are reported as notes, not errors,
    # because losing colour is not losing the map.
    assert [n["label"] for n in d["nodes"]] == ["web-1"]
    assert d["errors"] == []
    assert len(d["notes"]) >= 3


def test_topology_reports_a_dead_inventory_as_an_error(mod, monkeypatch):
    app_mod, sources = mod
    monkeypatch.setattr(sources, "_get",
                        lambda *a, **k: (None, "unreachable (ConnectError)"))
    c = TestClient(app_mod.app)
    d = c.get("/api/topology", headers=hdr()).json()
    # No host inventory means no map at all — that one IS an error.
    assert d["nodes"] == []
    assert any("/api/hosts" in e for e in d["errors"])


def test_topology_requires_a_signed_in_caller(mod):
    app_mod, _ = mod
    assert TestClient(app_mod.app).get("/api/topology").status_code == 401


# ---- event source (person / API caller / an app's own automation) ----------
# The Controller classifies its own rows and reports a `source`; the other apps
# do not, so Visualizer infers one from the actor. Getting this wrong in either
# direction is bad: a person's action filed as "automation" hides real work, and
# a background sweep filed as "user" pins the fleet's own noise on a human.
def test_controller_source_is_taken_from_the_upstream(mod):
    app_mod, sources = mod

    def fake_get(url, identity, params=None, want_json=True):
        if url.endswith("/api/activity"):
            return {"activity": [
                {"id": 1, "timestamp": 1.0, "username": "bob", "host": "web1",
                 "description": "restarted nginx", "source": "user"},
                {"id": 2, "timestamp": 2.0, "username": "bob", "host": "web1",
                 "description": "ran a fleet health check", "source": "automation"},
                {"id": 3, "timestamp": 3.0, "username": "svc", "host": "web1",
                 "description": "ran a command", "source": "api"},
            ]}, None
        return None, "not permitted for role 'operator' (403)"

    import backend.sources as s
    object.__setattr__(s, "_get", fake_get)
    c = TestClient(app_mod.app)
    ev = c.get("/api/activity", params={"app": "controller"}, headers=hdr()).json()["events"]
    assert {e["id"]: e["source"] for e in ev} == {1: "user", 2: "automation", 3: "api"}


def test_an_upstream_source_we_do_not_know_falls_back_to_the_actor(mod):
    """A future Controller inventing a new source value must not leak it into the
    filter vocabulary — the chips would silently drop those rows."""
    app_mod, sources = mod

    def fake_get(url, identity, params=None, want_json=True):
        if url.endswith("/api/activity"):
            return {"activity": [
                {"id": 1, "timestamp": 1.0, "username": "bob", "description": "x",
                 "source": "something-new"},
                {"id": 2, "timestamp": 2.0, "username": "controller", "description": "y",
                 "source": None},
            ]}, None
        return None, "nope"

    import backend.sources as s
    object.__setattr__(s, "_get", fake_get)
    c = TestClient(app_mod.app)
    ev = c.get("/api/activity", params={"app": "controller"}, headers=hdr()).json()["events"]
    got = {e["id"]: e["source"] for e in ev}
    assert got == {1: "user", 2: "automation"}
    assert all(e["source"] in sources.SOURCES for e in ev)


def test_apps_without_a_source_are_classified_by_actor(mod):
    """Connect/SLEP/Flashback record no source. A named human is a person; a
    service identity (or an unattributed row) is the app's own automation."""
    app_mod, sources = mod

    def fake_get(url, identity, params=None, want_json=True):
        return {"entries": [
            {"id": 1, "ts": 1.0, "actor": "alice", "action": "opened a terminal"},
            {"id": 2, "ts": 2.0, "actor": "system", "action": "pruned old snapshots"},
            {"id": 3, "ts": 3.0, "actor": "", "action": "startup"},
        ]}, None

    import backend.sources as s
    object.__setattr__(s, "_get", fake_get)
    c = TestClient(app_mod.app)
    ev = c.get("/api/activity", params={"app": "connect"}, headers=hdr()).json()["events"]
    assert {e["id"]: e["source"] for e in ev} == {1: "user", 2: "automation", 3: "automation"}


def test_every_event_carries_a_known_source(mod):
    """Whatever an upstream returns, every row reaches the console with a source
    the filter understands — otherwise a chip would hide rows with no way back."""
    app_mod, sources = mod

    def fake_get(url, identity, params=None, want_json=True):
        if url.endswith("/api/runs"):
            return {"runs": [{"id": 1, "created": 1.0, "created_by": "carol",
                              "status": "ok", "kind": "playbook"}]}, None
        # Every app names its rows differently; answer with all the shapes at once
        # so one fake serves the Controller, SLEP, Connect and Flashback adapters.
        return {"activity": [{"id": 2, "timestamp": 2.0, "username": "erin",
                              "description": "restarted nginx"}],
                "audit": [{"id": 3, "timestamp": 3.0, "username": "frank",
                           "event": "login"}],
                "entries": [{"id": 4, "ts": 4.0, "actor": "dave",
                             "event": "login", "action": "login"}]}, None

    import backend.sources as s
    object.__setattr__(s, "_get", fake_get)
    c = TestClient(app_mod.app)
    for app in sources.app_keys():
        ev = c.get("/api/activity", params={"app": app}, headers=hdr()).json()["events"]
        assert ev, f"{app} produced no events"
        for e in ev:
            assert e["source"] in sources.SOURCES, (app, e)


def test_console_offers_a_source_filter_for_every_kind(client):
    """The chips are the whole point of the feature; if the markup loses one, the
    rows of that kind become unreachable in the UI."""
    html = client.get("/", headers={**hdr(), "Accept": "text/html"}).text
    for kind in ("all", "user", "api", "automation"):
        assert f"data-src={kind}" in html


# ---- browser-tab icon ------------------------------------------------------
# The tab came up BLANK next to the portal's and Connect's: nothing here declared
# an icon, so the browser fell back to /favicon.ico at the ORIGIN ROOT — which
# behind the gateway is the portal's path, not ours.
def test_the_console_declares_a_tab_icon(client):
    html = client.get("/", headers={**hdr(), "Accept": "text/html"}).text
    assert "rel=icon" in html and "favicon.svg" in html


def test_the_icon_href_is_relative_so_it_survives_the_gateway_prefix(client):
    """The gateway serves this app under /visualizer and STRIPS the prefix. An
    absolute href would resolve to the portal's root and 404; a relative one
    resolves to /visualizer/favicon.svg and reaches us."""
    html = client.get("/", headers={**hdr(), "Accept": "text/html"}).text
    assert "href='/favicon.svg'" not in html
    assert "href='favicon.svg'" in html


def test_the_refusal_page_carries_the_icon_too(client):
    """A caller with no identity gets the refusal page — the very page where a
    blank tab is most confusing, since that is what a wiring fault shows."""
    r = client.get("/", headers={"Accept": "text/html"})
    assert r.status_code == 401
    assert "favicon.svg" in r.text


def test_the_icon_is_served_and_is_the_visualizer_mark(client):
    r = client.get("/favicon.svg")
    assert r.status_code == 200
    assert "image/svg+xml" in r.headers["content-type"]
    # The canonical mark from portal/marks/visualizer.svg, not some other app's.
    assert 'aria-label="Sysible Visualizer"' in r.text
    assert "#6ddb73" in r.text                     # the family's brand-green ring


def test_the_icon_needs_no_identity(client):
    """It is fetched for the REFUSAL page too, by a caller who has no identity
    yet. Gating it would leave exactly that tab blank. An icon is not a secret."""
    r = client.get("/favicon.svg")                 # no gateway headers at all
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# The log viewer's `ref` is the only caller-controlled part of any URL this
# service builds — and it lands in the PATH of a request that carries the
# platform shared secret. Unvalidated, "../../api/admin/users" collapsed to
# /api/admin/users/log before the request left the process, so ANY signed-in
# user (an auditor included) could use the log viewer to GET arbitrary SLEP
# endpoints through a privileged internal channel and read the raw body back.
# ---------------------------------------------------------------------------
def _captured_url(monkeypatch, ref):
    from backend import sources
    seen = {}

    def _fake_get(url, identity, params=None, want_json=True):
        seen["url"] = url
        return "log body", None
    monkeypatch.setattr(sources, "_get", _fake_get)

    class _Id:
        user, role = "auditor-al", "auditor"
    body, err = sources.fetch_log("slep", _Id(), ref)
    return seen.get("url"), body, err


def test_a_plain_run_reference_still_works(monkeypatch):
    url, body, err = _captured_url(monkeypatch, "run-42")
    assert err is None and body == "log body"
    assert url.endswith("/api/runs/run-42/log")


@pytest.mark.parametrize("ref", [
    "../../api/admin/users",          # collapses to /api/admin/users/log
    "x/../../secret",
    "a?admin=1",                      # truncates the path, injects a query
    "a#frag",
    "..%2f..%2fadmin",
    "a b",
    "/etc/passwd",
    "",
    "x" * 129,
])
def test_a_forged_reference_never_reaches_an_upstream(monkeypatch, ref):
    url, body, err = _captured_url(monkeypatch, ref)
    assert url is None, f"{ref!r} was sent upstream as {url}"
    assert body is None and err == "not a valid run reference"


def test_the_controller_log_takes_no_reference_at_all(monkeypatch):
    """Its URL is fixed, so nothing the caller sends can steer it."""
    from backend import sources
    seen = {}
    monkeypatch.setattr(sources, "_get",
                        lambda url, i, params=None, want_json=True: (seen.update(url=url), ("x", None))[1])

    class _Id:
        user, role = "al", "auditor"
    sources.fetch_log("controller", _Id(), "../../anything")
    assert seen["url"].endswith("/api/controller-log")
