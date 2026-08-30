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
# Double-submit CSRF token cookie. Deliberately HOST-ONLY (no parent-domain
# scope) so a sibling *.slop.lan app can't read it, and readable by our own form
# JS-free flow (the server echoes it into a hidden field); a state-changing POST
# must return the same value in that field.
CSRF_COOKIE = "sysible_csrf"

# Secure cookie by default (the gateway always terminates TLS). A deliberate
# plain-HTTP dev run opts out so the cookie rides http:// during local testing.
_ALLOW_INSECURE = os.environ.get("SLOP_ALLOW_INSECURE_COOKIE", "0") == "1"
SESSION_TTL = int(os.environ.get("SLOP_SESSION_TTL", str(12 * 3600)))  # 12h

# Brute-force throttle for POST /login. Caddy is the SOLE front end, so the
# trusted client address is the direct proxy peer (request.client.host), never a
# client-supplied X-Forwarded-For an attacker can rotate to dodge the limit. We
# throttle on that peer AND per target username, so neither source-address
# rotation nor username spraying can slip past the cap.
_LOGIN_MAX = int(os.environ.get("SLOP_LOGIN_MAX_ATTEMPTS", "8"))
_LOGIN_WINDOW = int(os.environ.get("SLOP_LOGIN_WINDOW_S", "300"))
# Bound the in-memory attempt map: a flood of distinct usernames/peers must not
# grow it without limit. Empty/expired buckets are purged each check; if still
# over this many keys, the stalest are evicted.
_LOGIN_ATTEMPTS_MAX_KEYS = int(os.environ.get("SLOP_LOGIN_MAX_KEYS", "4096"))

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


# A fixed dummy hash (current _SCRYPT params) verified against whenever the
# submitted username doesn't exist, so the login path always pays the same scrypt
# cost either way — closing the username-enumeration timing side channel.
_DUMMY_HASH = _hash_password(secrets.token_urlsafe(16))


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
# Failed-login timestamps, keyed by "ip:<peer>" and "user:<username>".
_login_attempts: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    # Caddy is the ONLY thing in front of us, so the trusted client address is the
    # direct peer — NOT a client-supplied X-Forwarded-For, which an attacker can
    # rotate on every request to land in a fresh bucket and defeat the throttle.
    # Never key the throttle on XFF here.
    return request.client.host if request.client else "unknown"


def _prune_attempts(now: float) -> None:
    """Purge empty/expired buckets and, if still over the cap, evict the stalest
    keys — so distinct source keys can't grow the map without bound."""
    for k in [k for k, v in _login_attempts.items()
              if not v or now - v[-1] >= _LOGIN_WINDOW]:
        _login_attempts.pop(k, None)
    excess = len(_login_attempts) - _LOGIN_ATTEMPTS_MAX_KEYS
    if excess > 0:
        for k in sorted(_login_attempts, key=lambda k: _login_attempts[k][-1])[:excess]:
            _login_attempts.pop(k, None)


def _bucket_wait(key: str, now: float) -> int:
    hits = [t for t in _login_attempts.get(key, []) if now - t < _LOGIN_WINDOW]
    if hits:
        _login_attempts[key] = hits
    else:
        _login_attempts.pop(key, None)
    if len(hits) >= _LOGIN_MAX:
        return int(_LOGIN_WINDOW - (now - hits[0]))
    return 0


def _throttled(ip: str, username: str) -> int:
    """Seconds the caller must wait before another attempt (0 if allowed).
    Throttles on the trusted proxy peer AND the target username, so a spray that
    rotates the source address still can't exceed the per-account limit."""
    now = time.time()
    _prune_attempts(now)
    return max(_bucket_wait("ip:" + ip, now), _bucket_wait("user:" + username, now))


def _record_fail(ip: str, username: str) -> None:
    now = time.time()
    for key in ("ip:" + ip, "user:" + username):
        _login_attempts.setdefault(key, []).append(now)


def _clear_fails(ip: str, username: str) -> None:
    _login_attempts.pop("ip:" + ip, None)
    _login_attempts.pop("user:" + username, None)


def _cookie_domain() -> str | None:
    # SLOP is ONE origin, addressed by path (/controller /slep /connect), so the
    # session cookie is HOST-ONLY by default — the single origin already covers
    # every app path. Host-only is also the only VALID choice when SLOP_DOMAIN is a
    # bare IP: a Domain=.192.168.8.249 cookie is malformed and silently dropped by
    # the browser (that drop is exactly what left an IP deployment unable to sign
    # in). docker-compose passes ${SLOP_COOKIE_DOMAIN:-} (present-but-empty when
    # unset), which correctly means host-only here. Set SLOP_COOKIE_DOMAIN to a
    # value ONLY for a custom multi-host/subdomain layout that needs a parent scope.
    return _COOKIE_DOMAIN_ENV or None


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


def _csrf_token(request: Request) -> str:
    """The browser's double-submit CSRF token, minting a fresh one if it has none
    yet. Deterministic given an existing cookie, so building a form and setting
    the cookie in the same handler use the SAME value."""
    return request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)


def _set_csrf_cookie(resp: Response, token: str) -> None:
    # Host-only (no Domain) so siblings can't read it; not httponly since it's a
    # double-submit token the form must echo, never a credential.
    resp.set_cookie(
        CSRF_COOKIE, token,
        max_age=SESSION_TTL,
        httponly=False,
        secure=not _ALLOW_INSECURE,
        samesite="lax",
        path="/",
    )


def _csrf_html(html: str, token: str, status_code: int = 200) -> HTMLResponse:
    """Render HTML that already embeds `token` and (re)issue the matching cookie."""
    resp = HTMLResponse(html, status_code=status_code)
    _set_csrf_cookie(resp, token)
    return resp


def _csrf_ok(request: Request, submitted: str | None) -> bool:
    """Double-submit check: the form's token must match the cookie (constant-time)."""
    have = request.cookies.get(CSRF_COOKIE)
    if not have or not submitted:
        return False
    return hmac.compare_digest(have, submitted)


def _origin_ok(request: Request) -> bool:
    """Same-origin guard for state-changing POSTs. The Origin/Referer host must be
    the IdP's OWN origin — never a sibling *.slop.lan app, which shares the
    parent-domain session cookie and must not be able to drive state changes here.
    Fail CLOSED when a browser sends neither header (no silent allow)."""
    for h in ("origin", "referer"):
        v = request.headers.get(h)
        if not v:
            continue
        host = (urlsplit(v).hostname or "").lower()
        return host == SLOP_DOMAIN or host in ("localhost", "127.0.0.1")
    return False  # neither Origin nor Referer present → reject


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
# Palette, fonts and background glows are lifted verbatim from portal/style.css so
# the sign-in flow reads as one product with the portal and the app consoles: same
# engineering-dark ground, same brand green, same light-theme toggle. The legacy
# --fg/--mut/--brand names are kept as aliases so the page bodies need no edits.
_CSS = """
:root{--bg:#0d1117;--panel:#131923;--panel2:#1a212d;--line:#26303f;
--text:#e6edf5;--muted:#93a1b5;--faint:#6f7d92;
--accent:#43a047;--accent2:#5580ee;--ok:#4caf5a;--err:#e5534b;--amber:#e0a83b;
--shadow:0 1px 2px rgba(0,0,0,.35),0 10px 30px rgba(0,0,0,.30);
--font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
--field:#0d1320;
--fg:var(--text);--mut:var(--muted);--brand:var(--accent)}
:root[data-theme="light"]{--bg:#eef1f6;--panel:#ffffff;--panel2:#f3f5f9;--line:#dbe1ea;
--text:#1b2431;--muted:#5b6675;--faint:#8794a4;
--accent:#2f8a37;--accent2:#2f6fe0;--ok:#2f9e4a;--err:#d23a30;--amber:#c98a12;
--shadow:0 1px 2px rgba(20,30,50,.08),0 10px 30px rgba(20,30,50,.10);
--field:#ffffff}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;
background:var(--bg);color:var(--text);font:15px/1.5 var(--font);-webkit-font-smoothing:antialiased}
.bg{position:fixed;inset:0;z-index:-1;pointer-events:none;
background:radial-gradient(70% 55% at 15% -10%,rgba(67,160,71,.12),transparent 60%),
radial-gradient(70% 60% at 100% 110%,rgba(85,128,238,.09),transparent 55%)}
.card{width:min(94vw,420px);background:var(--panel);border:1px solid var(--line);
border-radius:16px;padding:28px 26px;box-shadow:var(--shadow)}
.wide{width:min(94vw,760px)}
.brand{display:flex;align-items:center;gap:12px;margin-bottom:10px}
.brand .brand-text{font-size:18px;letter-spacing:.2px}
.brand b{color:var(--accent);font-weight:700}
h1{font-size:19px;margin:.2em 0 .1em}
p.sub{color:var(--muted);margin:.1em 0 1.2em;font-size:13.5px;line-height:1.55}
label{display:block;font-size:12.5px;color:var(--muted);margin:.9em 0 .3em}
input:not([type]),input[type=text],input[type=password],select{width:100%;padding:10px 12px;border-radius:10px;
border:1px solid var(--line);background:var(--field);color:var(--text);font-size:14px;font-family:var(--font)}
input:focus,select:focus{outline:none;border-color:var(--accent)}
input:-webkit-autofill,input:-webkit-autofill:focus{-webkit-text-fill-color:var(--text);
-webkit-box-shadow:0 0 0 1000px var(--field) inset;caret-color:var(--text)}
button{margin-top:1.3em;width:100%;padding:11px;border:0;border-radius:10px;cursor:pointer;
background:var(--accent);color:#04120a;font-weight:600;font-size:14.5px;font-family:var(--font)}
button:hover{filter:brightness(1.06)}
button.sec{background:var(--panel2);color:var(--text);border:1px solid var(--line);font-weight:500}
button.danger{background:transparent;color:var(--err);width:auto;margin:0;padding:6px 10px;font-size:12.5px;
border:1px solid color-mix(in srgb,var(--err) 45%,var(--line))}
button.mini{width:auto;margin:0;padding:6px 10px;font-size:12.5px}
a{color:var(--accent2);text-decoration:none}
a:hover{text-decoration:underline}
.msg{padding:9px 12px;border-radius:9px;font-size:13px;margin:.4em 0;border:1px solid transparent}
.msg.err{background:color-mix(in srgb,var(--err) 14%,var(--panel));color:var(--err);
border-color:color-mix(in srgb,var(--err) 40%,var(--line))}
.msg.ok{background:color-mix(in srgb,var(--ok) 14%,var(--panel));color:var(--ok);
border-color:color-mix(in srgb,var(--ok) 38%,var(--line))}
.row{display:flex;gap:10px}.row>*{flex:1}
table{width:100%;border-collapse:collapse;margin-top:.6em;font-size:13.5px}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:500;font-size:12px}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:.4em}
.pill{font-size:11.5px;color:var(--muted)}
.foot{margin-top:1.4em;color:var(--faint);font-size:12px;text-align:center;letter-spacing:.04em}
fieldset{border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:1.2em 0 0}
legend{color:var(--muted);font-size:12.5px;padding:0 6px}
.theme-btn{position:fixed;top:16px;right:16px;background:transparent;border:1px solid var(--line);
color:var(--muted);width:34px;height:34px;border-radius:8px;cursor:pointer;font-size:15px;line-height:1}
.theme-btn:hover{color:var(--text);border-color:var(--accent)}
"""

# The portal's mark, verbatim (gradient chip + green ">_" prompt + blue cursor).
_MARK = (
    '<svg class="mark" width="34" height="34" viewBox="0 0 128 128" aria-hidden="true">'
    '<defs><linearGradient id="t" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#161d29"/><stop offset="1" stop-color="#0a0d13"/></linearGradient></defs>'
    '<rect x="6" y="6" width="116" height="116" rx="28" fill="url(#t)"/>'
    '<rect x="8.5" y="8.5" width="111" height="111" rx="25.5" fill="none" stroke="#43a047" stroke-width="4"/>'
    '<path d="M40 44 L64 64 L40 84" fill="none" stroke="#43a047" stroke-width="9" '
    'stroke-linecap="round" stroke-linejoin="round"/>'
    '<rect x="72" y="74" width="20" height="10" rx="2" fill="#5580ee"/></svg>'
)


def _page(title: str, body: str, wide: bool = False) -> str:
    # The pre-paint script picks up the theme the operator chose on the portal
    # (shared 'slop-theme' key), falling back to the OS preference, so the login
    # never flashes the wrong theme. The trailing script wires the corner toggle.
    return (
        f"<!doctype html><html lang=en data-theme=dark><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title>"
        f"<script>try{{var t=localStorage.getItem('slop-theme');"
        f"if(t!=='light'&&t!=='dark')t=matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';"
        f"document.documentElement.setAttribute('data-theme',t)}}catch(e){{}}</script>"
        f"<style>{_CSS}</style></head><body>"
        f"<div class=bg></div>"
        f"<button class=theme-btn id=theme title='Toggle light / dark' aria-label='Toggle theme'>&#9728;</button>"
        f"<div class='card{' wide' if wide else ''}'>"
        f"<div class=brand>{_MARK}<div class=brand-text>Sysible <b>Operations Platform</b></div></div>"
        f"{body}"
        f"<div class=foot>Sysible Linux Operations Platform · Community Edition</div>"
        f"</div>"
        f"<script>(function(){{var b=document.getElementById('theme'),r=document.documentElement;"
        f"function s(){{b.textContent=r.getAttribute('data-theme')==='light'?'\\u263e':'\\u2600'}}s();"
        f"b.addEventListener('click',function(){{var n=r.getAttribute('data-theme')==='light'?'dark':'light';"
        f"r.setAttribute('data-theme',n);try{{localStorage.setItem('slop-theme',n)}}catch(e){{}}s()}})}})();</script>"
        f"</body></html>"
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
    # Re-read the user on EVERY verify (the session row predates any later admin
    # reset/role change). If the account is gone, or a forced password change is
    # still pending, refuse: Caddy bounces to /login, which routes to /account, so
    # no app subdomain is reachable until the password is actually changed.
    user = _get_user(sess["username"])
    if not user or user["must_change"]:
        return Response(status_code=401, headers={"Cache-Control": "no-store"})
    return Response(
        status_code=204,
        headers={
            "X-Sysible-User": user["username"],
            "X-Sysible-Role": user["role"],
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
def _hidden_csrf(token: str) -> str:
    return f"<input type=hidden name=csrf value='{escape(token)}'>"


def _login_form(next_url: str, msg: str = "", csrf: str = "") -> str:
    body = (
        "<h1>Sign in</h1><p class=sub>One sign-in for Controller, Engineering "
        "Platform, and Connect.</p>"
        f"{_msg(msg)}"
        f"<form method=post action='/login?{urlencode({'next': next_url})}'>"
        f"{_hidden_csrf(csrf)}"
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
    tok = _csrf_token(request)
    sess = _current(request)
    if sess:  # already signed in
        # A pending forced change must land on /account, never on an app: the app's
        # /auth/verify keeps 401'ing while must_change is set, so redirecting to it
        # would bounce the browser app -> /login -> app forever.
        u = _get_user(sess["username"])
        dest = "/account?first=1" if (u and u["must_change"]) else nxt
        resp: Response = RedirectResponse(dest, status_code=302)
    else:
        resp = HTMLResponse(_login_form(nxt, csrf=tok))
    _set_csrf_cookie(resp, tok)  # seed the double-submit token either way
    return resp


@app.post("/login")
def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf: str = Form(""),
    next: str = "/",
):
    nxt = _safe_next(next)
    tok = _csrf_token(request)
    if not _origin_ok(request):
        return _csrf_html(_login_form(nxt, "Request blocked (bad origin).", tok), tok, 403)
    if not _csrf_ok(request, csrf):
        return _csrf_html(_login_form(nxt, "Request blocked (bad or missing token).", tok), tok, 403)
    uname = username.strip()
    ip = _client_ip(request)
    wait = _throttled(ip, uname)
    if wait:
        return _csrf_html(
            _login_form(nxt, f"Too many attempts. Try again in {max(wait, 1)}s.", tok), tok, 429
        )
    user = _get_user(uname)
    # Always run a scrypt verification — against a fixed dummy hash when the user
    # doesn't exist — so both branches cost the same and the response latency can't
    # be used to enumerate valid usernames (timing side channel).
    pw_hash = user["pw_hash"] if user else _DUMMY_HASH
    ok = _verify_password(password, pw_hash)
    if not user or not ok:
        _record_fail(ip, uname)
        return _csrf_html(_login_form(nxt, "Invalid username or password.", tok), tok, 401)
    _clear_fails(ip, uname)
    token = _new_session(user["username"], user["role"])
    # A forced password change (fresh account / admin reset) routes to /account first.
    dest = "/account?first=1" if user["must_change"] else nxt
    resp = RedirectResponse(dest, status_code=302)
    _set_session_cookie(resp, token)
    _set_csrf_cookie(resp, tok)
    return resp


@app.post("/logout")
def logout_post(request: Request):
    _drop_session(request.cookies.get(COOKIE))
    resp = RedirectResponse("/login", status_code=302)
    _clear_session_cookie(resp)
    return resp


# ---- self-service account (change my own password) -------------------------
def _account_page(sess: sqlite3.Row, first: bool, msg: str = "", kind: str = "err",
                  csrf: str = "") -> str:
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
        f"{_hidden_csrf(csrf)}"
        "<label>Current password</label>"
        "<input type=password name=current autocomplete=current-password required>"
        "<label>New password</label>"
        "<input type=password name=new1 autocomplete=new-password required>"
        "<label>Confirm new password</label>"
        "<input type=password name=new2 autocomplete=new-password required>"
        "<button type=submit>Change password</button></form>"
        "<form method=post action='/logout' style='margin-top:.6em'>"
        f"{_hidden_csrf(csrf)}"
        "<button class=sec type=submit>Sign out</button></form>"
    )
    return _page("Account · SLOP", body)


@app.get("/account", response_class=HTMLResponse)
def account_get(request: Request, first: int = 0):
    sess = _current(request)
    if not sess:
        return RedirectResponse("/login?" + urlencode({"next": "/account"}), status_code=302)
    tok = _csrf_token(request)
    return _csrf_html(_account_page(sess, bool(first), csrf=tok), tok)


_MIN_PW = int(os.environ.get("SLOP_MIN_PASSWORD_LEN", "10"))


@app.post("/account/password")
def account_password(
    request: Request,
    current: str = Form(...),
    new1: str = Form(...),
    new2: str = Form(...),
    csrf: str = Form(""),
):
    sess = _current(request)
    if not sess:
        return RedirectResponse("/login", status_code=302)
    tok = _csrf_token(request)
    if not _origin_ok(request):
        return _csrf_html(_account_page(sess, False, "Request blocked (bad origin).", csrf=tok), tok, 403)
    if not _csrf_ok(request, csrf):
        return _csrf_html(_account_page(sess, False, "Request blocked (bad or missing token).", csrf=tok), tok, 403)
    user = _get_user(sess["username"])
    if not user or not _verify_password(current, user["pw_hash"]):
        return _csrf_html(_account_page(sess, False, "Current password is incorrect.", csrf=tok), tok, 401)
    if new1 != new2:
        return _csrf_html(_account_page(sess, False, "The new passwords don't match.", csrf=tok), tok, 400)
    if len(new1) < _MIN_PW:
        return _csrf_html(_account_page(sess, False, f"Use at least {_MIN_PW} characters.", csrf=tok), tok, 400)
    if _verify_password(new1, user["pw_hash"]):
        return _csrf_html(_account_page(sess, False, "Choose a password you haven't used here.", csrf=tok), tok, 400)
    _upsert_user(user["username"], new1, user["role"], must_change=False)
    # A password change must revoke a stolen/older cookie: drop EVERY session for
    # this user (including this browser's), then mint a fresh one and set it on the
    # response so the acting browser stays signed in while all others are killed.
    _drop_user_sessions(user["username"])
    new_token = _new_session(user["username"], user["role"])
    # must_change is now cleared, so send them INTO the platform (the portal
    # launcher) rather than leaving them staring at the change-password form — this
    # is the "you're in" moment right after the forced first-login change. 303 so
    # the browser re-issues it as a GET.
    resp: Response = RedirectResponse("/", status_code=303)
    _set_session_cookie(resp, new_token)
    return resp


# ---- superuser: manage accounts + reset anyone's password ------------------
def _admin_page(sess: sqlite3.Row, msg: str = "", kind: str = "ok", csrf: str = "") -> str:
    with _db() as c:
        rows = c.execute("SELECT username, role, must_change FROM users ORDER BY username").fetchall()
    hidden = _hidden_csrf(csrf)
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
            f"{hidden}<select name=role>{opts}</select>"
            f"<button class=mini type=submit>Set</button></form></td>"
            f"<td><form method=post action='/admin/users/{escape(r['username'])}/reset' style='margin:0'>"
            f"{hidden}<button class='mini sec' type=submit>Reset password</button></form></td>"
            f"<td style='text-align:right'>"
            + ("" if is_self else
               f"<form method=post action='/admin/users/{escape(r['username'])}/delete' style='margin:0'>"
               f"{hidden}<button class=danger type=submit>Delete</button></form>")
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
        f"{hidden}"
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


def _admin_guard(request: Request, sess: sqlite3.Row, csrf: str, tok: str):
    """Shared origin + CSRF check for admin mutations. Returns an error response
    to send, or None when the request may proceed."""
    if not _origin_ok(request):
        return _csrf_html(_admin_page(sess, "Request blocked (bad origin).", "err", csrf=tok), tok, 403)
    if not _csrf_ok(request, csrf):
        return _csrf_html(_admin_page(sess, "Request blocked (bad or missing token).", "err", csrf=tok), tok, 403)
    return None


@app.get("/admin", response_class=HTMLResponse)
def admin_get(request: Request):
    sess, err = _require_super(request)
    if err:
        return err
    tok = _csrf_token(request)
    return _csrf_html(_admin_page(sess, csrf=tok), tok)


@app.post("/admin/users")
def admin_add(request: Request, username: str = Form(...), role: str = Form(...),
              password: str = Form(...), csrf: str = Form("")):
    sess, err = _require_super(request)
    if err:
        return err
    tok = _csrf_token(request)
    blocked = _admin_guard(request, sess, csrf, tok)
    if blocked:
        return blocked
    username = username.strip()
    if not username or role not in ROLES:
        return _csrf_html(_admin_page(sess, "Username and a valid role are required.", "err", csrf=tok), tok, 400)
    if len(password) < _MIN_PW:
        return _csrf_html(_admin_page(sess, f"Temporary password needs {_MIN_PW}+ characters.", "err", csrf=tok), tok, 400)
    if _get_user(username):
        return _csrf_html(_admin_page(sess, f"User '{username}' already exists.", "err", csrf=tok), tok, 409)
    _upsert_user(username, password, role, must_change=True)
    return _csrf_html(_admin_page(sess, f"Created '{username}'.", "ok", csrf=tok), tok)


@app.post("/admin/users/{username}/reset")
def admin_reset(request: Request, username: str, csrf: str = Form("")):
    sess, err = _require_super(request)
    if err:
        return err
    tok = _csrf_token(request)
    blocked = _admin_guard(request, sess, csrf, tok)
    if blocked:
        return blocked
    if not _get_user(username):
        return _csrf_html(_admin_page(sess, "No such user.", "err", csrf=tok), tok, 404)
    temp = secrets.token_urlsafe(12)
    u = _get_user(username)
    _upsert_user(username, temp, u["role"], must_change=True)
    _drop_user_sessions(username)  # force re-login with the new password
    return _csrf_html(
        _admin_page(sess, f"Temporary password for '{username}': {temp}  (they must change it at next login)", "ok", csrf=tok), tok
    )


@app.post("/admin/users/{username}/role")
def admin_role(request: Request, username: str, role: str = Form(...), csrf: str = Form("")):
    sess, err = _require_super(request)
    if err:
        return err
    tok = _csrf_token(request)
    blocked = _admin_guard(request, sess, csrf, tok)
    if blocked:
        return blocked
    u = _get_user(username)
    if not u or role not in ROLES:
        return _csrf_html(_admin_page(sess, "No such user, or invalid role.", "err", csrf=tok), tok, 400)
    # Don't let the last superuser demote themselves out of admin access.
    if u["role"] == "superuser" and role != "superuser" and _count_role("superuser") <= 1:
        return _csrf_html(_admin_page(sess, "Can't demote the only superuser.", "err", csrf=tok), tok, 400)
    # Update only the role in place — never touch the stored password hash.
    with _db() as c:
        c.execute("UPDATE users SET role=?, updated_at=? WHERE username=?",
                  (role, int(time.time()), username))
    _drop_user_sessions(username)  # role change takes effect immediately
    return _csrf_html(_admin_page(sess, f"Set {username}'s role to {role}.", "ok", csrf=tok), tok)


@app.post("/admin/users/{username}/delete")
def admin_delete(request: Request, username: str, csrf: str = Form("")):
    sess, err = _require_super(request)
    if err:
        return err
    tok = _csrf_token(request)
    blocked = _admin_guard(request, sess, csrf, tok)
    if blocked:
        return blocked
    if username == sess["username"]:
        return _csrf_html(_admin_page(sess, "You can't delete your own account.", "err", csrf=tok), tok, 400)
    u = _get_user(username)
    if not u:
        return _csrf_html(_admin_page(sess, "No such user.", "err", csrf=tok), tok, 404)
    if u["role"] == "superuser" and _count_role("superuser") <= 1:
        return _csrf_html(_admin_page(sess, "Can't delete the only superuser.", "err", csrf=tok), tok, 400)
    with _db() as c:
        c.execute("DELETE FROM users WHERE username=?", (username,))
    _drop_user_sessions(username)
    return _csrf_html(_admin_page(sess, f"Deleted '{username}'.", "ok", csrf=tok), tok)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
