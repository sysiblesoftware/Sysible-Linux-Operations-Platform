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
from html import unescape

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


def _tok(c):
    """Fetch the double-submit CSRF token the server hands out (a GET /login seeds
    the cookie); its value is what every state-changing POST must echo back."""
    c.get("/login", follow_redirects=False)
    return c.cookies.get("sysible_csrf")


def _login(c, user, pw):
    return c.post("/login", data={"username": user, "password": pw, "csrf": _tok(c)},
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
    tok = _tok(cl)
    assert cl.post("/logout", data={"csrf": tok}, headers=HDR,
                   follow_redirects=False).status_code == 302
    assert cl.get("/auth/verify").status_code == 401


def test_logout_requires_same_origin_but_not_a_token(cl):
    # SameSite=Lax means a cross-site POST carries no session cookie, so the
    # same-origin check is the real gate. A cross-site / origin-less POST must NOT
    # drop the session...
    _login(cl, "admin", ADMIN_PW)
    r = cl.post("/logout", follow_redirects=False)          # no origin, no csrf
    assert r.status_code == 302
    assert cl.get("/auth/verify").status_code == 204          # still signed in

    # ...and a WRONG token is rejected even from the right origin.
    r = cl.post("/logout", data={"csrf": "not-the-real-token"}, headers=HDR,
                follow_redirects=False)
    assert r.status_code == 302
    assert cl.get("/auth/verify").status_code == 204          # still signed in

    # ...but a same-origin click with NO token still signs out. The portal's Sign
    # out button is static HTML with no server-rendered token; requiring one wedged
    # sign-out entirely (regression).
    r = cl.post("/logout", headers=HDR, follow_redirects=False)
    assert r.status_code == 302
    assert cl.get("/auth/verify").status_code == 401


def test_admin_create_reset_and_forced_change(client):
    from starlette.testclient import TestClient
    cl, m = client
    _login(cl, "admin", ADMIN_PW)
    r = cl.post("/admin/users",
                data={"username": "opsy", "role": "operator", "password": "temppass12345",
                      "csrf": _tok(cl)}, headers=HDR)
    assert r.status_code == 200 and "Created" in r.text
    # a freshly created user must change their password at first login
    r = cl.post("/admin/users/opsy/reset", data={"csrf": _tok(cl)}, headers=HDR)
    temp = re.search(r"password for 'opsy': (\S+)", unescape(r.text)).group(1)
    with TestClient(m.app, base_url="http://slop.lan") as d:
        r = _login(d, "opsy", temp)
        assert r.status_code == 302 and r.headers["location"].startswith("/account")
        # must_change is enforced at the gateway probe: no app access until changed
        assert d.get("/auth/verify").status_code == 401
        # after changing it, the probe passes and reflects the role
        c = d.post("/account/password",
                   data={"current": temp, "new1": "opsynewpass123", "new2": "opsynewpass123",
                         "csrf": _tok(d)}, headers=HDR, follow_redirects=False)
        # a successful change clears must_change and sends the user into the platform
        assert c.status_code == 303 and c.headers["location"] == "/"
        v = d.get("/auth/verify")
        assert v.status_code == 204
        assert v.headers["X-Sysible-Role"] == "operator"
        # a non-superuser can't reach the admin console
        assert d.get("/admin").status_code == 403


def test_self_service_password_change(cl):
    _login(cl, "admin", ADMIN_PW)
    r = cl.post("/account/password",
                data={"current": ADMIN_PW, "new1": "brandnewpass99", "new2": "brandnewpass99",
                      "csrf": _tok(cl)}, headers=HDR, follow_redirects=False)
    # a successful change drops the form and redirects into the platform (portal)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cl.post("/logout", headers=HDR, follow_redirects=False)
    assert _login(cl, "admin", "brandnewpass99").status_code == 302


def test_password_change_rejects_short_and_mismatch(cl):
    _login(cl, "admin", ADMIN_PW)
    r = cl.post("/account/password",
                data={"current": ADMIN_PW, "new1": "short", "new2": "short", "csrf": _tok(cl)},
                headers=HDR)
    assert r.status_code == 400
    r = cl.post("/account/password",
                data={"current": ADMIN_PW, "new1": "longenough123", "new2": "different123",
                      "csrf": _tok(cl)}, headers=HDR)
    assert r.status_code == 400


def test_cross_site_post_blocked(cl):
    _login(cl, "admin", ADMIN_PW)
    r = cl.post("/account/password",
                data={"current": ADMIN_PW, "new1": "xxxxxxxxxx11", "new2": "xxxxxxxxxx11",
                      "csrf": _tok(cl)},
                headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_cross_origin_blocked(cl):
    # A POST whose Origin host differs from the host this request was addressed to
    # (base_url slop.lan) must be rejected — the same-origin guard is host-exact.
    _login(cl, "admin", ADMIN_PW)
    r = cl.post("/account/password",
                data={"current": ADMIN_PW, "new1": "xxxxxxxxxx22", "new2": "xxxxxxxxxx22",
                      "csrf": _tok(cl)},
                headers={"origin": "http://other-host.example"})
    assert r.status_code == 403


def test_csrf_token_required_on_state_change(cl):
    # Right origin + right current password, but a MISSING csrf token is rejected.
    _login(cl, "admin", ADMIN_PW)
    r = cl.post("/account/password",
                data={"current": ADMIN_PW, "new1": "nocsrfhere123", "new2": "nocsrfhere123"},
                headers=HDR)
    assert r.status_code == 403
    # a WRONG csrf token is rejected too
    r = cl.post("/account/password",
                data={"current": ADMIN_PW, "new1": "nocsrfhere123", "new2": "nocsrfhere123",
                      "csrf": "not-the-real-token"},
                headers=HDR)
    assert r.status_code == 403
    # and the password was never changed
    cl.post("/logout", headers=HDR, follow_redirects=False)
    assert _login(cl, "admin", ADMIN_PW).status_code == 302


def test_missing_origin_and_referer_fails_closed(cl):
    # No Origin AND no Referer on a state-changing POST → reject (no silent allow).
    _login(cl, "admin", ADMIN_PW)
    r = cl.post("/account/password",
                data={"current": ADMIN_PW, "new1": "noorigin1234", "new2": "noorigin1234",
                      "csrf": _tok(cl)})
    assert r.status_code == 403


def test_cannot_delete_or_demote_last_superuser(cl):
    _login(cl, "admin", ADMIN_PW)
    assert cl.post("/admin/users/admin/delete", data={"csrf": _tok(cl)}, headers=HDR).status_code == 400
    assert cl.post("/admin/users/admin/role", data={"role": "operator", "csrf": _tok(cl)},
                   headers=HDR).status_code == 400


def test_reset_kills_existing_sessions(client):
    from starlette.testclient import TestClient
    cl, m = client
    _login(cl, "admin", ADMIN_PW)
    cl.post("/admin/users", data={"username": "sam", "role": "auditor", "password": "sampass12345",
                                  "csrf": _tok(cl)}, headers=HDR)
    with TestClient(m.app, base_url="http://slop.lan") as d:
        # first login forces a change; do it so sam has a live, usable session
        _login(d, "sam", "sampass12345")
        d.post("/account/password",
               data={"current": "sampass12345", "new1": "sambrandnew99", "new2": "sambrandnew99",
                     "csrf": _tok(d)}, headers=HDR)
        assert d.get("/auth/verify").status_code == 204
        # admin resets sam's password -> sam's live session is dropped immediately
        cl.post("/admin/users/sam/reset", data={"csrf": _tok(cl)}, headers=HDR)
        assert d.get("/auth/verify").status_code == 401


# ---- regression tests for the pentest fixes --------------------------------
def test_verify_blocked_until_password_changed(client):
    """must_change is enforced at /auth/verify: 401 while pending, 204 only after
    the password is actually changed."""
    from starlette.testclient import TestClient
    cl, m = client
    _login(cl, "admin", ADMIN_PW)
    cl.post("/admin/users",
            data={"username": "newbie", "role": "operator", "password": "temppass12345",
                  "csrf": _tok(cl)}, headers=HDR)
    r = cl.post("/admin/users/newbie/reset", data={"csrf": _tok(cl)}, headers=HDR)
    temp = re.search(r"password for 'newbie': (\S+)", unescape(r.text)).group(1)
    with TestClient(m.app, base_url="http://slop.lan") as d:
        assert _login(d, "newbie", temp).status_code == 302
        # forced change pending -> the gateway probe refuses (no app access)
        assert d.get("/auth/verify").status_code == 401
        # change it -> probe now succeeds
        d.post("/account/password",
               data={"current": temp, "new1": "newbiepass777", "new2": "newbiepass777",
                     "csrf": _tok(d)}, headers=HDR)
        assert d.get("/auth/verify").status_code == 204


def test_password_change_revokes_other_sessions(client):
    """Changing a password invalidates OTHER sessions (a stolen cookie) while
    keeping the acting browser signed in."""
    from starlette.testclient import TestClient
    cl, m = client
    _login(cl, "admin", ADMIN_PW)
    with TestClient(m.app, base_url="http://slop.lan") as thief:
        _login(thief, "admin", ADMIN_PW)
        assert thief.get("/auth/verify").status_code == 204
        # the real user rotates their password in the first browser
        r = cl.post("/account/password",
                    data={"current": ADMIN_PW, "new1": "rotated-pw-123", "new2": "rotated-pw-123",
                          "csrf": _tok(cl)}, headers=HDR, follow_redirects=False)
        assert r.status_code == 303
        # acting browser stays signed in (fresh cookie minted on the response)...
        assert cl.get("/auth/verify").status_code == 204
        # ...but the other/stolen session is revoked immediately
        assert thief.get("/auth/verify").status_code == 401


def test_throttle_not_bypassable_by_spoofed_xff(client):
    """Rotating X-Forwarded-For on every attempt must NOT dodge the throttle —
    it keys on the real proxy peer + the target username, not on client XFF."""
    cl, m = client
    last = None
    for i in range(m._LOGIN_MAX + 2):
        last = cl.post(
            "/login",
            data={"username": "admin", "password": "wrong", "csrf": _tok(cl)},
            headers={"origin": "http://slop.lan", "x-forwarded-for": f"10.0.0.{i}"},
            follow_redirects=False,
        )
    assert last.status_code == 429


def test_login_runs_scrypt_even_for_unknown_user(client):
    """Timing side channel: the login path always runs a scrypt verify — against
    the fixed dummy hash when the user doesn't exist — so both branches cost the
    same and valid usernames can't be enumerated by latency."""
    cl, m = client
    seen = []
    orig = m._verify_password
    m._verify_password = lambda pw, stored: seen.append(stored) or orig(pw, stored)
    try:
        r = _login(cl, "definitely-not-a-real-user", "whateverpass12")
    finally:
        m._verify_password = orig
    assert r.status_code == 401
    # scrypt ran against the module-level dummy hash for the nonexistent user
    assert m._DUMMY_HASH in seen


def test_session_cookie_is_host_only(tmp_path):
    """The session cookie is ALWAYS host-only (no Domain=), for any address. SLOP has
    no configured domain — it answers on the server's IP — and a Domain-scoped cookie
    is both unnecessary (one origin) and invalid for a bare IP (a Domain=.192.168.8.249
    cookie is malformed and silently dropped, which is what broke sign-in by IP)."""
    from starlette.testclient import TestClient

    os.environ.update(
        SLOP_DATA_DIR=str(tmp_path), SLOP_DB_PATH=str(tmp_path / "idp.db"),
        SLOP_ADMIN_USER="admin", SLOP_ADMIN_PASSWORD=ADMIN_PW,
        SLOP_ADMIN_FORCE_CHANGE="0", SLOP_ALLOW_INSECURE_COOKIE="1",
    )
    import app as m
    m = importlib.reload(m)
    assert m._cookie_domain() is None
    # Reached by a raw IP: the Set-Cookie must carry NO Domain= attribute.
    with TestClient(m.app, base_url="http://192.168.8.249") as c:
        r = c.post("/login", data={"username": "admin", "password": ADMIN_PW, "csrf": _tok(c)},
                   headers={"origin": "http://192.168.8.249"}, follow_redirects=False)
        assert r.status_code == 302
        sso = next(v for k, v in r.headers.multi_items()
                   if k.lower() == "set-cookie" and v.startswith("sysible_sso="))
        assert "domain=" not in sso.lower()


def test_config_page_requires_superuser(cl):
    # Not signed in → bounced to login (302 to /login).
    r = cl.get("/admin/settings", follow_redirects=False)
    assert r.status_code in (302, 401, 403)


def test_config_page_renders_for_superuser_and_masks_secret(client, monkeypatch):
    cl, m = client
    monkeypatch.setenv("SYSIBLE_SSO_SHARED_SECRET", "topsecret-shared-value-xyz")
    _login(cl, "admin", ADMIN_PW)
    r = cl.get("/admin/settings")
    assert r.status_code == 200
    body = r.text
    # Documents the parameters…
    assert "Configuration" in body
    assert "SYSIBLE_SSO_SHARED_SECRET" in body and "SLOP_SESSION_TTL" in body
    # …but never prints the actual secret value.
    assert "topsecret-shared-value-xyz" not in body
    assert "configured" in body


def test_config_page_forbidden_for_non_superuser(client):
    from starlette.testclient import TestClient
    cl, m = client
    _login(cl, "admin", ADMIN_PW)
    cl.post("/admin/users", data={"username": "auditguy", "role": "auditor",
            "password": "auditorpass123", "csrf": _tok(cl)}, headers=HDR)
    with TestClient(m.app, base_url="http://slop.lan") as d:
        d.post("/login", data={"username": "auditguy", "password": "auditorpass123",
               "csrf": _tok(d)}, headers=HDR, follow_redirects=False)
        # auditor is signed in but not superuser → 403 on the config console
        assert d.get("/admin/settings").status_code == 403


# ---- regression tests for the security-audit fixes -------------------------
def test_safe_next_rejects_open_redirect_vectors():
    import app as m
    # Site-relative targets are preserved…
    assert m._safe_next("/controller/") == "/controller/"
    assert m._safe_next("/account?first=1") == "/account?first=1"
    # …every off-origin vector collapses to "/", including the backslash bypass
    # that urlsplit() alone would let through.
    for bad in ("//evil.com", "/\\evil.com", "/\\/evil.com", "\\\\evil.com",
                "http://evil.com", "https:evil.com", "javascript:alert(1)",
                "  //evil.com", None, ""):
        assert m._safe_next(bad) == "/", bad


def test_admin_add_rejects_bad_username(cl):
    _login(cl, "admin", ADMIN_PW)
    for bad in ("has space", "quote'd", "<script>", "a" * 65, "semi;colon"):
        r = cl.post("/admin/users",
                    data={"username": bad, "role": "operator", "password": "temppass12345",
                          "csrf": _tok(cl)}, headers=HDR)
        assert r.status_code == 400, bad
    # a clean name still works
    r = cl.post("/admin/users",
                data={"username": "ok.user-1", "role": "operator", "password": "temppass12345",
                      "csrf": _tok(cl)}, headers=HDR)
    assert r.status_code == 200 and "Created" in r.text


def test_security_headers_present(cl):
    r = cl.get("/login", follow_redirects=False)
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "frame-ancestors 'none'" in r.headers.get("Content-Security-Policy", "")
    assert r.headers.get("Cache-Control") == "no-store"


def test_second_superuser_can_be_demoted(cl):
    # The last-superuser guard must NOT block demotion when another superuser
    # remains (regression for the atomic-guarded UPDATE).
    _login(cl, "admin", ADMIN_PW)
    cl.post("/admin/users", data={"username": "super2", "role": "superuser",
            "password": "super2pass123", "csrf": _tok(cl)}, headers=HDR)
    r = cl.post("/admin/users/super2/role", data={"role": "operator", "csrf": _tok(cl)},
                headers=HDR)
    assert r.status_code == 200 and "role to operator" in r.text
    # now super2 is demoted, admin is the only superuser again → guard re-engages
    assert cl.post("/admin/users/admin/role", data={"role": "operator", "csrf": _tok(cl)},
                   headers=HDR).status_code == 400
