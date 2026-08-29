"""Tests for the SLOP IdP — the identity provider behind the gateway.

Run: pip install -r ../requirements.txt pytest ; pytest  (from the idp/ dir)

Each test gets a fresh SQLite file and a fresh import of the app module, so the
first-run bootstrap and the module-level config are exercised cleanly.
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

ADMIN_PW = "adminpassword123"


@pytest.fixture()
def client(tmp_path):
    from starlette.testclient import TestClient

    os.environ.update(
        SLOP_DATA_DIR=str(tmp_path),
        SLOP_DB_PATH=str(tmp_path / "idp.db"),
        SLOP_ADMIN_USER="admin",
        SLOP_ADMIN_PASSWORD=ADMIN_PW,
        SLOP_ADMIN_FORCE_CHANGE="0",
        SLOP_ALLOW_INSECURE_COOKIE="1",
        SLOP_DOMAIN="slop.lan",
    )
    import app as m
    m = importlib.reload(m)
    with TestClient(m.app, base_url="http://slop.lan") as c:
        yield c, m


@pytest.fixture()
def cl(client):
    # Convenience: most tests only need the HTTP client.
    return client[0]


HDR = {"origin": "http://slop.lan"}


def _login(c, user, pw):
    return c.post("/login", data={"username": user, "password": pw},
                  headers=HDR, follow_redirects=False)


def test_verify_unauthenticated_is_401(cl):
    assert cl.get("/auth/verify").status_code == 401


def test_bad_password_rejected(cl):
    assert _login(cl, "admin", "nope").status_code == 401


def test_login_then_verify_emits_identity_headers(cl):
    assert _login(cl, "admin", ADMIN_PW).status_code == 302
    r = cl.get("/auth/verify")
    assert r.status_code == 204
    assert r.headers["X-Sysible-User"] == "admin"
    assert r.headers["X-Sysible-Role"] == "superuser"
    assert r.headers["Cache-Control"] == "no-store"


def test_auth_me(cl):
    _login(cl, "admin", ADMIN_PW)
    j = cl.get("/auth/me").json()
    assert j == {"authenticated": True, "user": "admin", "role": "superuser", "must_change": False}


def test_logout_invalidates_session(cl):
    _login(cl, "admin", ADMIN_PW)
    assert cl.post("/logout", headers=HDR, follow_redirects=False).status_code == 302
    assert cl.get("/auth/verify").status_code == 401


def test_admin_create_reset_and_forced_change(client):
    from starlette.testclient import TestClient
    cl, m = client
    _login(cl, "admin", ADMIN_PW)
    r = cl.post("/admin/users", data={"username": "opsy", "role": "operator", "password": "temppass12345"},
                headers=HDR)
    assert r.status_code == 200 and "Created" in r.text
    # a freshly created user must change their password at first login
    r = cl.post("/admin/users/opsy/reset", headers=HDR)
    from html import unescape
    temp = re.search(r"password for 'opsy': (\S+)", unescape(r.text)).group(1)
    with TestClient(m.app, base_url="http://slop.lan") as d:
        r = _login(d, "opsy", temp)
        assert r.status_code == 302 and r.headers["location"].startswith("/account")
        # role is reflected in the SSO probe
        assert d.get("/auth/verify").headers["X-Sysible-Role"] == "operator"
        # a non-superuser can't reach the admin console
        assert d.get("/admin").status_code == 403


def test_self_service_password_change(cl):
    _login(cl, "admin", ADMIN_PW)
    r = cl.post("/account/password",
                data={"current": ADMIN_PW, "new1": "brandnewpass99", "new2": "brandnewpass99"},
                headers=HDR)
    assert "Password changed" in r.text
    cl.post("/logout", headers=HDR, follow_redirects=False)
    assert _login(cl, "admin", "brandnewpass99").status_code == 302


def test_password_change_rejects_short_and_mismatch(cl):
    _login(cl, "admin", ADMIN_PW)
    r = cl.post("/account/password", data={"current": ADMIN_PW, "new1": "short", "new2": "short"}, headers=HDR)
    assert r.status_code == 400
    r = cl.post("/account/password", data={"current": ADMIN_PW, "new1": "longenough123", "new2": "different123"},
                headers=HDR)
    assert r.status_code == 400


def test_cross_site_post_blocked(cl):
    _login(cl, "admin", ADMIN_PW)
    r = cl.post("/account/password",
                data={"current": ADMIN_PW, "new1": "xxxxxxxxxx11", "new2": "xxxxxxxxxx11"},
                headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_cannot_delete_or_demote_last_superuser(cl):
    _login(cl, "admin", ADMIN_PW)
    assert cl.post("/admin/users/admin/delete", headers=HDR).status_code == 400
    assert cl.post("/admin/users/admin/role", data={"role": "operator"}, headers=HDR).status_code == 400


def test_reset_kills_existing_sessions(client):
    from starlette.testclient import TestClient
    cl, m = client
    _login(cl, "admin", ADMIN_PW)
    cl.post("/admin/users", data={"username": "sam", "role": "auditor", "password": "sampass12345"}, headers=HDR)
    with TestClient(m.app, base_url="http://slop.lan") as d:
        # first login forces a change; do it so sam has a live, usable session
        d.post("/login", data={"username": "sam", "password": "sampass12345"}, headers=HDR, follow_redirects=False)
        d.post("/account/password",
               data={"current": "sampass12345", "new1": "sambrandnew99", "new2": "sambrandnew99"}, headers=HDR)
        assert d.get("/auth/verify").status_code == 204
        # admin resets sam's password -> sam's live session is dropped immediately
        cl.post("/admin/users/sam/reset", headers=HDR)
        assert d.get("/auth/verify").status_code == 401
