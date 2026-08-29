#!/usr/bin/env python3
"""Sysible Operations Platform — the SLOP Identity Provider (CE).

SLOP is the single front door for the three Sysible apps (Controller, SLEP,
Connect). This service is the ONE place a user signs in: it owns the user
store, issues the shared single-sign-on session, and is where every account
and password (for all three apps) is managed — because behind the gateway the
apps no longer keep their own logins; they trust the identity SLOP asserts.

How the pieces fit (see ../docs/SSO.md for the full contract):

  browser ──► Caddy gateway ──► app (Controller/SLEP/Connect)
                 │  forward_auth
                 ▼
             this IdP  /auth/verify   ← "is this browser signed in?"

  * A browser signs in here (POST /login). We set a session cookie scoped to
    the PARENT domain (Domain=.slop.lan), so the same cookie rides requests to
    every *.slop.lan subdomain — that shared cookie is what makes it single
    sign-on across the three apps rather than three separate logins.
  * On each proxied request Caddy calls GET /auth/verify with the browser's
    cookies. We answer 200 + headers `X-Sysible-User` / `X-Sysible-Role` when
    the session is valid (Caddy copies those onto the upstream request and adds
    the shared-secret header so the app can trust them), or 401 so Caddy bounces
    the browser to /login.
  * Password resets for ALL THREE apps happen here (self-service /account, or a
    superuser resetting anyone from /admin), because this is the sole credential.

CE scope: a small, dependency-light service (FastAPI + stdlib sqlite3 + stdlib
scrypt). EE hardening (MFA, external IdP/OIDC federation, per-app fine-grained
RBAC, signed assertions, mTLS to the apps) is deliberately left for the EE build.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from html import escape
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

# ---------------------------------------------------------------------------
# Configuration (env-driven; every value has a working single-host default).
# ---------------------------------------------------------------------------
DATA_DIR = os.environ.get("SLOP_DATA_DIR", "/data")
DB_PATH = os.environ.get("SLOP_DB_PATH", os.path.join(DATA_DIR, "slop-idp.db"))

# The apex the portal answers on (e.g. "slop.lan"). The session cookie is scoped
# to ".<apex>" so it is sent to the apex AND every app subdomain — the mechanism
# that makes one login cover all three apps. localhost has no dot-parent, so the
# cookie domain is omitted there (host-only cookie), which the browser accepts.
SLOP_DOMAIN = os.environ.get("SLOP_DOMAIN", "slop.lan")
_COOKIE_DOMAIN_ENV = os.environ.get("SLOP_COOKIE_DOMAIN")  # explicit override wins
COOKIE = "sysible_sso"

# Secure cookie by default (the gateway always terminates TLS). A deliberate
# plain-HTTP dev run opts out so the cookie rides http:// during local testing.
_ALLOW_INSECURE = os.environ.get("SLOP_ALLOW_INSECURE_COOKIE", "0") == "1"
SESSION_TTL = int(os.environ.get("SLOP_SESSION_TTL", str(12 * 3600)))  # 12h

# Brute-force throttle for POST /login (per client IP).
_LOGIN_MAX = int(os.environ.get("SLOP_LOGIN_MAX_ATTEMPTS", "8"))
_LOGIN_WINDOW = int(os.environ.get("SLOP_LOGIN_WINDOW_S", "300"))

# The three canonical SLOP roles, most→least privileged. Each app maps these onto
# its own vocabulary (e.g. SLEP: auditor→viewer). Keep this list authoritative.
ROLES = ("superuser", "operator", "auditor")

# scrypt work factors (OWASP-ish interactive defaults). Stored alongside each hash
# so a later bump doesn't lock out existing users. scrypt's buffer is ~128*r*N
# bytes; OpenSSL caps that at 32 MiB unless we raise maxmem, so set a generous
# ceiling that comfortably fits these params (and any stored older ones).
_SCRYPT = dict(n=2 ** 15, r=8, p=1)
_SCRYPT_MAXMEM = 256 * 1024 * 1024


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def _db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db() -> None:
    with _db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS users (
                   username     TEXT PRIMARY KEY,
                   pw_hash      TEXT NOT NULL,
                   role         TEXT NOT NULL,
                   must_change  INTEGER NOT NULL DEFAULT 0,
                   created_at   INTEGER NOT NULL,
                   updated_at   INTEGER NOT NULL
               )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                   token_hash  TEXT PRIMARY KEY,
                   username    TEXT NOT NULL,
                   role        TEXT NOT NULL,
                   created_at  INTEGER NOT NULL,
                   expires_at  INTEGER NOT NULL
               )"""
        )


def _hash_password(password: str) -> str:
    """scrypt with a per-password random salt; self-describing so params can change."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, maxmem=_SCRYPT_MAXMEM, **_SCRYPT)
    return "scrypt${n}${r}${p}${salt}${hash}".format(
        salt=salt.hex(), hash=dk.hex(), **_SCRYPT
    )


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), maxmem=_SCRYPT_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def _get_user(username: str) -> sqlite3.Row | None:
    with _db() as c:
        return c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


def _count_role(role: str) -> int:
    with _db() as c:
        return c.execute("SELECT COUNT(*) n FROM users WHERE role=?", (role,)).fetchone()["n"]


def _upsert_user(username: str, password: str, role: str, must_change: bool) -> None:
    now = int(time.time())
    with _db() as c:
        c.execute(
            """INSERT INTO users(username, pw_hash, role, must_change, created_at, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(username) DO UPDATE SET
                   pw_hash=excluded.pw_hash, role=excluded.role,
                   must_change=excluded.must_change, updated_at=excluded.updated_at""",
            (username, _hash_password(password), role, int(must_change), now, now),
        )


def _new_session(username: str, role: str) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with _db() as c:
        c.execute(
            "INSERT INTO sessions(token_hash, username, role, created_at, expires_at) VALUES(?,?,?,?,?)",
            (_sha(token), username, role, now, now + SESSION_TTL),
        )
    return token


def _resolve_session(token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    with _db() as c:
        row = c.execute(
            "SELECT * FROM sessions WHERE token_hash=?", (_sha(token),)
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < int(time.time()):
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_sha(token),))
            return None
        return row


def _drop_session(token: str | None) -> None:
    if not token:
        return
    with _db() as c:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (_sha(token),))


def _drop_user_sessions(username: str) -> None:
    """Kill every live session for a user — used on password reset / role change /
    delete, so a credential change takes effect immediately everywhere."""
    with _db() as c:
        c.execute("DELETE FROM sessions WHERE username=?", (username,))


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# First-run bootstrap: guarantee exactly one way in on a fresh install.
# ---------------------------------------------------------------------------
def _bootstrap_admin() -> None:
    with _db() as c:
        n = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    if n:
        return
    user = os.environ.get("SLOP_ADMIN_USER", "admin").strip() or "admin"
    pw = os.environ.get("SLOP_ADMIN_PASSWORD", "").strip()
    generated = False
    if not pw:
        pw = secrets.token_urlsafe(12)
        generated = True
    # Force a change on first login when we generated the password (or when asked).
    must_change = generated or os.environ.get("SLOP_ADMIN_FORCE_CHANGE", "1") == "1"
    _upsert_user(user, pw, "superuser", must_change)
    banner = "=" * 70
    print(banner)
    print(" SLOP IdP: created the initial superuser account.")
    print(f"   username: {user}")
    if generated:
        print(f"   password: {pw}    <-- shown ONCE; change it at /account on first login")
    else:
        print("   password: (from SLOP_ADMIN_PASSWORD)")
    print(banner, flush=True)


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------
_login_attempts: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    # Caddy is the only thing in front of us; honor its X-Forwarded-For so the
    # throttle keys on the real client, not the proxy.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _throttled(ip: str) -> int:
    now = time.time()
    hits = [t for t in _login_attempts.get(ip, []) if now - t < _LOGIN_WINDOW]
    _login_attempts[ip] = hits
    if len(hits) >= _LOGIN_MAX:
        return int(_LOGIN_WINDOW - (now - hits[0]))
    return 0


def _record_fail(ip: str) -> None:
    _login_attempts.setdefault(ip, []).append(time.time())


def _clear_fails(ip: str) -> None:
    _login_attempts.pop(ip, None)


def _cookie_domain() -> str | None:
    if _COOKIE_DOMAIN_ENV is not None:
        return _COOKIE_DOMAIN_ENV or None
    # A bare hostname with no dot (localhost, an IP) can't carry a domain-scoped
    # cookie — fall back to a host-only cookie the browser will still accept.
    if "." not in SLOP_DOMAIN:
        return None
    return "." + SLOP_DOMAIN


def _set_session_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        COOKIE, token,
        max_age=SESSION_TTL,
        httponly=True,
        secure=not _ALLOW_INSECURE,
        samesite="lax",
        domain=_cookie_domain(),
        path="/",
    )


def _clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(COOKIE, domain=_cookie_domain(), path="/")


def _origin_ok(request: Request) -> bool:
    """Same-origin backstop for state-changing POSTs (SameSite=Lax is the primary
    control). If the browser sent an Origin/Referer, its host must be us or a
    sibling *.slop.lan; absent both, allow (non-browser tooling / curl)."""
    for h in ("origin", "referer"):
        v = request.headers.get(h)
        if not v:
            continue
        host = (urlsplit(v).hostname or "").lower()
        if host == SLOP_DOMAIN or host.endswith("." + SLOP_DOMAIN) or host in ("localhost", "127.0.0.1"):
            return True
        return False
    return True


def _safe_next(raw: str | None) -> str:
    """Validate a ?next= redirect target to stop open-redirects: allow only a
    site-relative path, or an absolute URL whose host is the apex / a *.slop.lan
    subdomain. Anything else falls back to the portal."""
    if not raw:
        return "/"
    parts = urlsplit(raw)
    if not parts.scheme and not parts.netloc and raw.startswith("/"):
        return raw  # site-relative
    host = (parts.hostname or "").lower()
    if host == SLOP_DOMAIN or host.endswith("." + SLOP_DOMAIN):
        return raw
    return "/"


def _current(request: Request) -> sqlite3.Row | None:
    return _resolve_session(request.cookies.get(COOKIE))


# ---------------------------------------------------------------------------
# HTML (inlined — three small server-rendered pages, styled to match the portal)
# ---------------------------------------------------------------------------
_CSS = """
:root{--bg:#0a0d13;--panel:#121826;--line:#223;--fg:#e8edf5;--mut:#93a0b4;
--brand:#43a047;--accent:#5580ee;--err:#e5484d;--ok:#43a047}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:radial-gradient(1200px 600px at 50% -10%,#161d29,var(--bg));
color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.card{width:min(94vw,420px);background:var(--panel);border:1px solid var(--line);
border-radius:16px;padding:28px 26px;box-shadow:0 20px 60px rgba(0,0,0,.45)}
.wide{width:min(94vw,760px)}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.brand b{color:var(--brand)}
h1{font-size:19px;margin:.2em 0 .1em}
p.sub{color:var(--mut);margin:.1em 0 1.2em;font-size:13.5px}
label{display:block;font-size:12.5px;color:var(--mut);margin:.9em 0 .3em}
input[type=text],input[type=password],select{width:100%;padding:10px 12px;border-radius:10px;
border:1px solid var(--line);background:#0d1320;color:var(--fg);font-size:14px}
button{margin-top:1.3em;width:100%;padding:11px;border:0;border-radius:10px;cursor:pointer;
background:var(--brand);color:#04120a;font-weight:600;font-size:14.5px}
button.sec{background:#1b2436;color:var(--fg);border:1px solid var(--line);font-weight:500}
button.danger{background:transparent;color:var(--err);border:1px solid #40232a;width:auto;
margin:0;padding:6px 10px;font-size:12.5px}
button.mini{width:auto;margin:0;padding:6px 10px;font-size:12.5px}
a{color:var(--accent);text-decoration:none}
.msg{padding:9px 12px;border-radius:9px;font-size:13px;margin:.4em 0}
.msg.err{background:#2a161a;color:#ff9ba0;border:1px solid #40232a}
.msg.ok{background:#14241a;color:#8fe0a6;border:1px solid #22432f}
.row{display:flex;gap:10px}.row>*{flex:1}
table{width:100%;border-collapse:collapse;margin-top:.6em;font-size:13.5px}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-weight:500;font-size:12px}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:.4em}
.pill{font-size:11.5px;color:var(--mut)}
.foot{margin-top:1.4em;color:var(--mut);font-size:12px;text-align:center}
fieldset{border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:1.2em 0 0}
legend{color:var(--mut);font-size:12.5px;padding:0 6px}
"""

_MARK = (
    '<svg width="30" height="30" viewBox="0 0 128 128" aria-hidden="true">'
    '<rect x="6" y="6" width="116" height="116" rx="28" fill="#121826"/>'
    '<rect x="8.5" y="8.5" width="111" height="111" rx="25.5" fill="none" stroke="#43a047" stroke-width="4"/>'
    '<path d="M40 44 L64 64 L40 84" fill="none" stroke="#43a047" stroke-width="9" '
    'stroke-linecap="round" stroke-linejoin="round"/>'
    '<rect x="72" y="74" width="20" height="10" rx="2" fill="#5580ee"/></svg>'
)


def _page(title: str, body: str, wide: bool = False) -> str:
    return (
        f"<!doctype html><html lang=en data-theme=dark><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body>"
        f"<div class='card{' wide' if wide else ''}'>"
        f"<div class=brand>{_MARK}<div>Sysible <b>Operations Platform</b></div></div>"
        f"{body}"
        f"<div class=foot>Sysible Linux Operations Platform · Community Edition</div>"
        f"</div></body></html>"
    )


def _msg(text: str, kind: str = "err") -> str:
    return f"<div class='msg {kind}'>{escape(text)}</div>" if text else ""


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="SLOP IdP", docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event("startup")
def _startup() -> None:
    _init_db()
    _bootstrap_admin()


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


# ---- the forward_auth probe the gateway calls on every proxied request -----
@app.get("/auth/verify")
def auth_verify(request: Request) -> Response:
    """Answer the gateway's one question: is this browser signed in?

    200 + X-Sysible-User / X-Sysible-Role when the session cookie is valid (Caddy
    copies those headers onto the upstream request and adds the shared secret so
    the app can trust them); 401 otherwise, so Caddy redirects to /login.
    """
    sess = _current(request)
    if not sess:
        return Response(status_code=401, headers={"Cache-Control": "no-store"})
    return Response(
        status_code=204,
        headers={
            "X-Sysible-User": sess["username"],
            "X-Sysible-Role": sess["role"],
            "Cache-Control": "no-store",
        },
    )


@app.get("/auth/me")
def auth_me(request: Request):
    sess = _current(request)
    if not sess:
        return JSONResponse({"authenticated": False}, status_code=401)
    u = _get_user(sess["username"])
    return {
        "authenticated": True,
        "user": sess["username"],
        "role": sess["role"],
        "must_change": bool(u["must_change"]) if u else False,
    }


# ---- login / logout --------------------------------------------------------
def _login_form(next_url: str, msg: str = "") -> str:
    body = (
        "<h1>Sign in</h1><p class=sub>One sign-in for Controller, Engineering "
        "Platform, and Connect.</p>"
        f"{_msg(msg)}"
        f"<form method=post action='/login?{urlencode({'next': next_url})}'>"
        "<label>Username</label>"
        "<input name=username autocomplete=username autofocus required>"
        "<label>Password</label>"
        "<input type=password name=password autocomplete=current-password required>"
        "<button type=submit>Sign in</button></form>"
    )
    return _page("Sign in · SLOP", body)


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request, next: str = "/"):
    nxt = _safe_next(next)
    if _current(request):  # already signed in → straight through
        return RedirectResponse(nxt, status_code=302)
    return HTMLResponse(_login_form(nxt))


@app.post("/login")
def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = "/",
):
    nxt = _safe_next(next)
    if not _origin_ok(request):
        return HTMLResponse(_login_form(nxt, "Request blocked (bad origin)."), status_code=403)
    ip = _client_ip(request)
    wait = _throttled(ip)
    if wait:
        return HTMLResponse(
            _login_form(nxt, f"Too many attempts. Try again in {max(wait, 1)}s."),
            status_code=429,
        )
    user = _get_user(username.strip())
    if not user or not _verify_password(password, user["pw_hash"]):
        _record_fail(ip)
        return HTMLResponse(_login_form(nxt, "Invalid username or password."), status_code=401)
    _clear_fails(ip)
    token = _new_session(user["username"], user["role"])
    # A forced password change (fresh account / admin reset) routes to /account first.
    dest = "/account?first=1" if user["must_change"] else nxt
    resp = RedirectResponse(dest, status_code=302)
    _set_session_cookie(resp, token)
    return resp


@app.post("/logout")
def logout_post(request: Request):
    _drop_session(request.cookies.get(COOKIE))
    resp = RedirectResponse("/login", status_code=302)
    _clear_session_cookie(resp)
    return resp


# ---- self-service account (change my own password) -------------------------
def _account_page(sess: sqlite3.Row, first: bool, msg: str = "", kind: str = "err") -> str:
    must = first or (_get_user(sess["username"]) or {"must_change": 0})["must_change"]
    intro = (
        "<div class='msg err'>Set a new password to continue.</div>"
        if must else ""
    )
    admin_link = "<a href='/admin'>Manage accounts →</a> · " if sess["role"] == "superuser" else ""
    body = (
        f"<div class=top><h1>Your account</h1><span class=pill>{escape(sess['username'])} "
        f"· {escape(sess['role'])}</span></div>"
        f"<p class=sub>{admin_link}<a href='/'>Open portal →</a></p>"
        f"{intro}{_msg(msg, kind)}"
        "<form method=post action='/account/password'>"
        "<label>Current password</label>"
        "<input type=password name=current autocomplete=current-password required>"
        "<label>New password</label>"
        "<input type=password name=new1 autocomplete=new-password required>"
        "<label>Confirm new password</label>"
        "<input type=password name=new2 autocomplete=new-password required>"
        "<button type=submit>Change password</button></form>"
        "<form method=post action='/logout' style='margin-top:.6em'>"
        "<button class=sec type=submit>Sign out</button></form>"
    )
    return _page("Account · SLOP", body)


@app.get("/account", response_class=HTMLResponse)
def account_get(request: Request, first: int = 0):
    sess = _current(request)
    if not sess:
        return RedirectResponse("/login?" + urlencode({"next": "/account"}), status_code=302)
    return HTMLResponse(_account_page(sess, bool(first)))


_MIN_PW = int(os.environ.get("SLOP_MIN_PASSWORD_LEN", "10"))


@app.post("/account/password")
def account_password(
    request: Request,
    current: str = Form(...),
    new1: str = Form(...),
    new2: str = Form(...),
):
    sess = _current(request)
    if not sess:
        return RedirectResponse("/login", status_code=302)
    if not _origin_ok(request):
        return HTMLResponse(_account_page(sess, False, "Request blocked (bad origin)."), status_code=403)
    user = _get_user(sess["username"])
    if not user or not _verify_password(current, user["pw_hash"]):
        return HTMLResponse(_account_page(sess, False, "Current password is incorrect."), status_code=401)
    if new1 != new2:
        return HTMLResponse(_account_page(sess, False, "The new passwords don't match."), status_code=400)
    if len(new1) < _MIN_PW:
        return HTMLResponse(
            _account_page(sess, False, f"Use at least {_MIN_PW} characters."), status_code=400
        )
    if _verify_password(new1, user["pw_hash"]):
        return HTMLResponse(
            _account_page(sess, False, "Choose a password you haven't used here."), status_code=400
        )
    _upsert_user(user["username"], new1, user["role"], must_change=False)
    return HTMLResponse(_account_page(sess, False, "Password changed.", kind="ok"))


# ---- superuser: manage accounts + reset anyone's password ------------------
def _admin_page(sess: sqlite3.Row, msg: str = "", kind: str = "ok") -> str:
    with _db() as c:
        rows = c.execute("SELECT username, role, must_change FROM users ORDER BY username").fetchall()
    trs = ""
    for r in rows:
        opts = "".join(
            f"<option value='{ro}'{' selected' if ro == r['role'] else ''}>{ro}</option>"
            for ro in ROLES
        )
        is_self = r["username"] == sess["username"]
        flag = " · must change" if r["must_change"] else ""
        trs += (
            f"<tr><td>{escape(r['username'])}<span class=pill>{flag}</span></td>"
            f"<td><form method=post action='/admin/users/{escape(r['username'])}/role' class=row "
            f"style='align-items:center;margin:0'>"
            f"<select name=role>{opts}</select>"
            f"<button class=mini type=submit>Set</button></form></td>"
            f"<td><form method=post action='/admin/users/{escape(r['username'])}/reset' style='margin:0'>"
            f"<button class='mini sec' type=submit>Reset password</button></form></td>"
            f"<td style='text-align:right'>"
            + ("" if is_self else
               f"<form method=post action='/admin/users/{escape(r['username'])}/delete' style='margin:0'>"
               f"<button class=danger type=submit>Delete</button></form>")
            + "</td></tr>"
        )
    role_opts = "".join(f"<option value='{ro}'>{ro}</option>" for ro in ROLES)
    body = (
        f"<div class=top><h1>Accounts</h1><span class=pill>{escape(sess['username'])} · superuser</span></div>"
        f"<p class=sub><a href='/account'>Your account</a> · <a href='/'>Portal →</a> · "
        "one credential signs a user into all three apps.</p>"
        f"{_msg(msg, kind)}"
        "<table><tr><th>User</th><th>Role</th><th>Password</th><th></th></tr>"
        f"{trs}</table>"
        "<fieldset><legend>Add a user</legend>"
        "<form method=post action='/admin/users'>"
        "<div class=row><div><label>Username</label>"
        "<input name=username required></div>"
        f"<div><label>Role</label><select name=role>{role_opts}</select></div></div>"
        "<label>Temporary password (they'll be asked to change it)</label>"
        "<input type=password name=password required>"
        "<button type=submit>Create user</button></form></fieldset>"
    )
    return _page("Accounts · SLOP", body, wide=True)


def _require_super(request: Request):
    sess = _current(request)
    if not sess:
        return None, RedirectResponse("/login?" + urlencode({"next": "/admin"}), status_code=302)
    if sess["role"] != "superuser":
        return None, HTMLResponse(_page("Forbidden", "<h1>Forbidden</h1>"
                                        "<p class=sub>Superuser access required. "
                                        "<a href='/account'>Your account →</a></p>"), status_code=403)
    return sess, None


@app.get("/admin", response_class=HTMLResponse)
def admin_get(request: Request):
    sess, err = _require_super(request)
    if err:
        return err
    return HTMLResponse(_admin_page(sess))


@app.post("/admin/users")
def admin_add(request: Request, username: str = Form(...), role: str = Form(...), password: str = Form(...)):
    sess, err = _require_super(request)
    if err:
        return err
    if not _origin_ok(request):
        return HTMLResponse(_admin_page(sess, "Request blocked (bad origin).", "err"), status_code=403)
    username = username.strip()
    if not username or role not in ROLES:
        return HTMLResponse(_admin_page(sess, "Username and a valid role are required.", "err"), status_code=400)
    if len(password) < _MIN_PW:
        return HTMLResponse(_admin_page(sess, f"Temporary password needs {_MIN_PW}+ characters.", "err"), status_code=400)
    if _get_user(username):
        return HTMLResponse(_admin_page(sess, f"User '{username}' already exists.", "err"), status_code=409)
    _upsert_user(username, password, role, must_change=True)
    return HTMLResponse(_admin_page(sess, f"Created '{username}'.", "ok"))


@app.post("/admin/users/{username}/reset")
def admin_reset(request: Request, username: str):
    sess, err = _require_super(request)
    if err:
        return err
    if not _origin_ok(request):
        return HTMLResponse(_admin_page(sess, "Request blocked (bad origin).", "err"), status_code=403)
    if not _get_user(username):
        return HTMLResponse(_admin_page(sess, "No such user.", "err"), status_code=404)
    temp = secrets.token_urlsafe(12)
    u = _get_user(username)
    _upsert_user(username, temp, u["role"], must_change=True)
    _drop_user_sessions(username)  # force re-login with the new password
    return HTMLResponse(
        _admin_page(sess, f"Temporary password for '{username}': {temp}  (they must change it at next login)", "ok")
    )


@app.post("/admin/users/{username}/role")
def admin_role(request: Request, username: str, role: str = Form(...)):
    sess, err = _require_super(request)
    if err:
        return err
    if not _origin_ok(request):
        return HTMLResponse(_admin_page(sess, "Request blocked (bad origin).", "err"), status_code=403)
    u = _get_user(username)
    if not u or role not in ROLES:
        return HTMLResponse(_admin_page(sess, "No such user, or invalid role.", "err"), status_code=400)
    # Don't let the last superuser demote themselves out of admin access.
    if u["role"] == "superuser" and role != "superuser" and _count_role("superuser") <= 1:
        return HTMLResponse(_admin_page(sess, "Can't demote the only superuser.", "err"), status_code=400)
    # Update only the role in place — never touch the stored password hash.
    with _db() as c:
        c.execute("UPDATE users SET role=?, updated_at=? WHERE username=?",
                  (role, int(time.time()), username))
    _drop_user_sessions(username)  # role change takes effect immediately
    return HTMLResponse(_admin_page(sess, f"Set {username}'s role to {role}.", "ok"))


@app.post("/admin/users/{username}/delete")
def admin_delete(request: Request, username: str):
    sess, err = _require_super(request)
    if err:
        return err
    if not _origin_ok(request):
        return HTMLResponse(_admin_page(sess, "Request blocked (bad origin).", "err"), status_code=403)
    if username == sess["username"]:
        return HTMLResponse(_admin_page(sess, "You can't delete your own account.", "err"), status_code=400)
    u = _get_user(username)
    if not u:
        return HTMLResponse(_admin_page(sess, "No such user.", "err"), status_code=404)
    if u["role"] == "superuser" and _count_role("superuser") <= 1:
        return HTMLResponse(_admin_page(sess, "Can't delete the only superuser.", "err"), status_code=400)
    with _db() as c:
        c.execute("DELETE FROM users WHERE username=?", (username,))
    _drop_user_sessions(username)
    return HTMLResponse(_admin_page(sess, f"Deleted '{username}'.", "ok"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
