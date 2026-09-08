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
