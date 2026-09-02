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

  * A browser signs in here (POST /login). We set a HOST-ONLY session cookie
    (no Domain=). SLOP is ONE origin — the portal and all three apps share it,
    addressed by path — so that one cookie rides every /controller /slep /connect
    request, which is what makes it single sign-on rather than three logins.
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
import re
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

# SLOP has NO configured domain: it answers on whatever IP/name the client uses,
# on one origin addressed by path. The session cookie is therefore always HOST-ONLY
# (no Domain=), which is the only valid choice for a raw IP and needs no config. The
# CSRF checks below are same-origin (compare against the request's own host), so
# nothing here needs to know the address.
COOKIE = "sysible_sso"
# Double-submit CSRF token cookie. Deliberately HOST-ONLY (no Domain=), matching
# the session cookie, and readable by our own form
# JS-free flow (the server echoes it into a hidden field); a state-changing POST
# must return the same value in that field.
CSRF_COOKIE = "sysible_csrf"

# Secure cookie by default (the gateway always terminates TLS). A deliberate
# plain-HTTP dev run opts out so the cookie rides http:// during local testing.
_ALLOW_INSECURE = os.environ.get("SLOP_ALLOW_INSECURE_COOKIE", "0") == "1"
SESSION_TTL = int(os.environ.get("SLOP_SESSION_TTL", str(12 * 3600)))  # 12h

# Brute-force throttle for POST /login. Caddy is the SOLE front end and APPENDS
# the real client to X-Forwarded-For, so _client_ip() takes the rightmost hop as
# the trusted per-client key (see the note there for why the raw proxy peer would
# collapse into one platform-wide bucket). We throttle on that client IP AND per
# target username, so neither source-address rotation nor username spraying slips
# past the cap — and one client's failures can't lock everyone else out.
_LOGIN_MAX = int(os.environ.get("SLOP_LOGIN_MAX_ATTEMPTS", "8"))
_LOGIN_WINDOW = int(os.environ.get("SLOP_LOGIN_WINDOW_S", "300"))
# Bound the in-memory attempt map: a flood of distinct usernames/peers must not
# grow it without limit. Empty/expired buckets are purged each check; if still
# over this many keys, the stalest are evicted.
_LOGIN_ATTEMPTS_MAX_KEYS = int(os.environ.get("SLOP_LOGIN_MAX_KEYS", "4096"))

# The three canonical SLOP roles, most→least privileged. Each app maps these onto
# its own vocabulary (e.g. SLEP: auditor→viewer). Keep this list authoritative.
ROLES = ("superuser", "operator", "auditor")

# Accepted username shape at CREATION time (existing/bootstrap accounts are never
# re-validated). Keep it to a portable identifier set so the same name is valid as
# a primary key here and in every downstream app's user vocabulary.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

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
    _sweep_expired_sessions()  # opportunistic, self-throttled bulk cleanup on login
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


_last_session_sweep = 0.0
_SESSION_SWEEP_INTERVAL = 3600  # seconds between opportunistic sweeps


def _sweep_expired_sessions(force: bool = False) -> None:
    """Bulk-delete every expired session row. _resolve_session already drops a row
    lazily when its own token is presented, but a session that's simply abandoned
    (browser closed, device lost) is never presented again and would otherwise sit
    in the table forever. Sweep on startup and at most once an interval thereafter
    so the table can't grow without bound."""
    global _last_session_sweep
    now = time.time()
    if not force and now - _last_session_sweep < _SESSION_SWEEP_INTERVAL:
        return
    _last_session_sweep = now
    with _db() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (int(now),))


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
    # Caddy (the single front proxy) APPENDS the real client's address to
    # X-Forwarded-For, so the LAST hop is the trusted client IP. Keying the throttle on
    # the direct peer instead would be Caddy's OWN address for every user — a single
    # global bucket that a few failed logins could use to lock the whole platform out of
    # sign-in (the SSO front door for Controller/SLEP/Connect). Taking the last XFF hop
    # is spoofing-resistant: a client may PREPEND fake entries, but only Caddy appends
    # the rightmost one. Falls back to the direct peer when there's no proxy (standalone).
    xff = request.headers.get("x-forwarded-for", "")
    peer = request.client.host if request.client else "unknown"
    if xff:
        return xff.split(",")[-1].strip() or peer
    return peer


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


def _cookie_domain() -> None:
    # Always host-only (no Domain=). SLOP is one origin addressed by path, reached by
    # the server's IP, so a host-only cookie is both correct and the only valid choice
    # for an IP (a Domain=.<ip> cookie is malformed and silently dropped). Nothing to
    # configure.
    return None


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
    the IdP's OWN origin. Fail CLOSED when a browser sends neither header (no
    silent allow)."""
    # Same-origin: the Origin/Referer host must match the host THIS request was
    # addressed to. No fixed domain — SLOP answers on whatever IP/name the client
    # used, and the gateway preserves that as the Host / X-Forwarded-Host header.
    self_host = (request.headers.get("x-forwarded-host")
                 or request.headers.get("host") or "").split(",")[0].split(":")[0].strip().lower()
    for h in ("origin", "referer"):
        v = request.headers.get(h)
        if not v:
            continue
        host = (urlsplit(v).hostname or "").lower()
        return bool(self_host) and host == self_host
    return False  # neither Origin nor Referer present → reject


def _safe_next(raw: str | None) -> str:
    """Validate a ?next= redirect target to stop open-redirects. SLOP is one origin
    addressed by path, so every legitimate target is a site-relative path (e.g.
    /controller/…); anything with a scheme/host is refused and falls back to /."""
    if not raw:
        return "/"
    # Must be a single-slash site-relative path. Reject, in addition to any
    # scheme/host: a leading "//" (scheme-relative -> another origin) and ANY
    # backslash. urlsplit() does NOT fold "\" to "/", so "/\evil.com" parses with
    # an empty netloc and would slip through the scheme/host test — yet a browser
    # treats "\" as "/", so Location: /\evil.com navigates to //evil.com. The
    # shipped Starlette happens to percent-encode "\" in the Location and defang it,
    # but that's an implementation detail one dependency bump could remove, so we
    # refuse backslashes here rather than lean on it.
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return "/"
    parts = urlsplit(raw)
    if not parts.scheme and not parts.netloc:
        return raw  # site-relative
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
td.sub{color:var(--muted);font-size:12.5px}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;font-size:12.5px}
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
.back{display:inline-flex;align-items:center;gap:5px;margin-bottom:10px;padding:5px 11px;
  border:1px solid var(--line);border-radius:8px;font-size:13px;color:var(--muted)}
.back:hover{color:var(--accent2);border-color:var(--accent2)}
.foot{margin-top:1.4em;color:var(--faint);font-size:12px;text-align:center;letter-spacing:.04em}
fieldset{border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:1.2em 0 0}
legend{color:var(--muted);font-size:12.5px;padding:0 6px}
.theme-btn{position:fixed;top:16px;right:16px;background:transparent;border:1px solid var(--line);
color:var(--muted);width:34px;height:34px;border-radius:8px;cursor:pointer;font-size:15px;line-height:1}
.theme-btn:hover{color:var(--text);border-color:var(--accent)}
"""

# The portal's mark, verbatim (gradient chip + SLOP's nested-arches portal glyph:
# a bold green outer arch framing a blue inner arch = the single front door).
_MARK = (
    '<svg class="mark" width="34" height="34" viewBox="0 0 128 128" aria-hidden="true">'
    '<defs><linearGradient id="t" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#161d29"/><stop offset="1" stop-color="#0a0d13"/></linearGradient></defs>'
    '<rect x="6" y="6" width="116" height="116" rx="28" fill="url(#t)"/>'
    '<rect x="8.5" y="8.5" width="111" height="111" rx="25.5" fill="none" stroke="#43a047" stroke-width="4"/>'
    '<path d="M38 98 L38 64 A26 26 0 0 1 90 64 L90 98" fill="none" stroke="#43a047" '
    'stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>'
    '<path d="M54 98 L54 68 A10 10 0 0 1 74 68 L74 98" fill="none" stroke="#5580ee" '
    'stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/></svg>'
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


# Content-Security-Policy for the IdP's own pages. The single-origin SLOP model
# means an XSS in ANY app runs at the same origin as this admin console, so a real
# CSP here is the containment that separate origins would otherwise give: no
# framing (anti-clickjacking of the destructive admin forms), no base-tag or form
# hijack, no plugins, nothing loaded off-origin. The pages carry a small inline
# theme <script> and inline <style>, so script/style keep 'unsafe-inline' (server-
# rendered, no user-injected markup reaches them). The gateway adds framing/sniff
# headers for the apps it fronts; this covers the IdP whether reached through the
# gateway or directly.
_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("Content-Security-Policy", _CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    # same-origin, NOT no-referrer: Chromium derives a navigation's Origin header
    # from the referrer policy, so no-referrer turns every HTML form POST here
    # (login, /account/password, the admin forms, sign out) into `Origin: null`
    # with no Referer, which _origin_ok correctly rejects — breaking sign-in and
    # sign-out outright. same-origin still sends nothing off-site.
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    # The IdP serves only per-user, security-relevant responses (login forms with
    # CSRF tokens, the admin user list, one-time temp passwords). None of it should
    # ever land in a shared/back-button cache; auth_verify already sets this, hence
    # setdefault.
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.on_event("startup")
def _startup() -> None:
    _init_db()
    _bootstrap_admin()
    _sweep_expired_sessions(force=True)


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
        # Report the LIVE role from the user row (like /auth/verify), not the value
        # frozen into the session at login. Role changes drop sessions today, so the
        # two agree — but reading the live row keeps them consistent if that ever
        # changes, and never over-reports a stale privileged role.
        "role": (u["role"] if u else sess["role"]),
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
def logout_post(request: Request, csrf: str = Form("")):
    # SAME-ORIGIN is the gate here, not the token. With SameSite=Lax the session
    # cookie is not sent on a cross-site POST at all, so a forged logout can't even
    # name a session to kill — the origin check alone closes forced-logout CSRF.
    # The double-submit token is still verified WHEN SUPPLIED (the account page and
    # the portal both send it), but a missing token must not wedge sign-out:
    # requiring it silently broke the portal's "Sign out" button, which is static
    # HTML served by Caddy with no server-rendered token to embed. Fail-closed on
    # the origin, fail-open on an absent token — logout must always work for a
    # legitimate same-origin click.
    if not _origin_ok(request):
        return RedirectResponse("/", status_code=302)
    if csrf and not _csrf_ok(request, csrf):
        return RedirectResponse("/", status_code=302)
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
        "<a class=back href='/'>&larr; Portal</a>"
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
        "<a class=back href='/'>&larr; Portal</a>"
        f"<div class=top><h1>Administration · Accounts</h1><span class=pill>{escape(sess['username'])} · superuser</span></div>"
        f"<p class=sub><a href='/admin/settings'>Configuration</a> · <a href='/account'>Your account</a> · "
        "<a href='/'>Portal →</a> · one credential signs a user into all three apps.</p>"
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
    if not _USERNAME_RE.match(username):
        return _csrf_html(_admin_page(
            sess, "Username must be 1–64 chars: letters, digits, dot, dash or underscore.",
            "err", csrf=tok), tok, 400)
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
    u = _get_user(username)
    if not u:
        return _csrf_html(_admin_page(sess, "No such user.", "err", csrf=tok), tok, 404)
    temp = secrets.token_urlsafe(12)
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
    # Update only the role in place — never touch the stored password hash. The
    # "don't demote the last superuser" guard lives INSIDE the UPDATE (the count
    # sub-select is evaluated under the same write lock as the write), so two
    # superusers demoting each other at once can't both slip past a separate
    # count-then-update and leave zero superusers (TOCTOU). rowcount==0 means the
    # guard blocked the demotion.
    with _db() as c:
        cur = c.execute(
            """UPDATE users SET role=?, updated_at=? WHERE username=? AND (
                   ?='superuser' OR role<>'superuser'
                   OR (SELECT COUNT(*) FROM users WHERE role='superuser')>1)""",
            (role, int(time.time()), username, role),
        )
        changed = cur.rowcount
    if not changed:
        return _csrf_html(_admin_page(sess, "Can't demote the only superuser.", "err", csrf=tok), tok, 400)
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
    # As with role demotion, the last-superuser guard is evaluated INSIDE the
    # DELETE (count sub-select under the same write lock) so two concurrent
    # superuser deletes can't both pass a separate count check and empty the admin
    # set (TOCTOU). rowcount==0 means the guard held.
    with _db() as c:
        cur = c.execute(
            """DELETE FROM users WHERE username=? AND (
                   role<>'superuser'
                   OR (SELECT COUNT(*) FROM users WHERE role='superuser')>1)""",
            (username,),
        )
        deleted = cur.rowcount
    if not deleted:
        return _csrf_html(_admin_page(sess, "Can't delete the only superuser.", "err", csrf=tok), tok, 400)
    _drop_user_sessions(username)
    return _csrf_html(_admin_page(sess, f"Deleted '{username}'.", "ok", csrf=tok), tok)


# ---------------------------------------------------------------------------
# Configuration console — a read-only reference of every SLOP parameter, with
# the running values (secrets never shown) and where each is set. SLOP is
# configured through environment variables applied on restart, so this is a
# status + reference view, not an editor; the account actions on /admin are the
# runtime-editable surface.
# ---------------------------------------------------------------------------
def _cfg_table(items) -> str:
    """items: list of (env_name, value_html, desc). value_html is pre-escaped/marked."""
    trs = "".join(
        f"<tr><td><code>{escape(name)}</code></td><td class=mono>{value}</td>"
        f"<td class=sub style='margin:0'>{escape(desc)}</td></tr>"
        for name, value, desc in items
    )
    return ("<table><tr><th>Parameter</th><th>Value</th><th>What it does</th></tr>"
            f"{trs}</table>")


def _secret_status(v: str) -> str:
    # Never render a secret; show only whether it is configured.
    return "<span class=pill>configured</span>" if v else "<span class=pill>not set</span>"


def _config_page(sess: sqlite3.Row) -> str:
    _admin_pw_set = bool(os.environ.get("SLOP_ADMIN_PASSWORD", "").strip())

    identity = _cfg_table([
        ("SLOP_ADMIN_USER", escape(os.environ.get("SLOP_ADMIN_USER", "admin") or "admin"),
         "Username of the first-run bootstrap superuser."),
        ("SLOP_ADMIN_PASSWORD",
         "<span class=pill>set</span>" if _admin_pw_set else "<span class=pill>auto-generated</span>",
         "Bootstrap admin password. Empty = a random one is generated and printed once to the idp logs."),
        ("SLOP_ADMIN_FORCE_CHANGE",
         "on" if os.environ.get("SLOP_ADMIN_FORCE_CHANGE", "1") == "1" else "off",
         "Force the bootstrap admin to change the password at first login."),
        ("SLOP_MIN_PASSWORD_LEN", str(_MIN_PW),
         "Minimum length for a new or changed password."),
        ("SLOP_SESSION_TTL", f"{SESSION_TTL}s (≈{SESSION_TTL // 3600}h)",
         "How long a sign-in lasts before re-login is required."),
    ])

    logins = _cfg_table([
        ("SLOP_LOGIN_MAX_ATTEMPTS", str(_LOGIN_MAX),
         "Failed sign-ins allowed per source within the window before throttling."),
        ("SLOP_LOGIN_WINDOW_S", f"{_LOGIN_WINDOW}s",
         "Rolling window the failed-login count is measured over."),
        ("SLOP_LOGIN_MAX_KEYS", str(_LOGIN_ATTEMPTS_MAX_KEYS),
         "Cap on tracked source keys for the throttle (memory bound against a spray)."),
        ("SLOP_ALLOW_INSECURE_COOKIE", "on (HTTP allowed)" if _ALLOW_INSECURE else "off (HTTPS only)",
         "Allow the session cookie over plain HTTP. Keep off in production."),
    ])

    sso = _cfg_table([
        ("SYSIBLE_SSO_SHARED_SECRET", _secret_status(os.environ.get("SYSIBLE_SSO_SHARED_SECRET", "")),
         "The gateway stamps this on proxied requests so each app can prove a request came "
         "through the gateway before trusting the asserted identity. Must be IDENTICAL here "
         "and in every app's SYSIBLE_SSO_SHARED_SECRET."),
    ])

    store = _cfg_table([
        ("SLOP_DATA_DIR", escape(DATA_DIR), "Directory holding the IdP data volume."),
        ("SLOP_DB_PATH", escape(DB_PATH), "Path to the SQLite user + session store."),
        ("PORT", escape(os.environ.get("PORT", "8080")), "Port the IdP listens on inside its container."),
    ])

    # Gateway-side values live on the Caddy container, not this IdP process, so show the
    # documented defaults (from .env.example) rather than a value this process can't read.
    upstreams = _cfg_table([
        ("SLOP_CONTROLLER_UPSTREAM", "host.docker.internal:8800", "Where the Controller listens (host:port)."),
        ("SLOP_SLEP_UPSTREAM", "host.docker.internal:8810", "Where the Engineering Platform (SLEP) listens."),
        ("SLOP_CONNECT_UPSTREAM", "host.docker.internal:8700", "Where Connect listens."),
        ("SLOP_IDP_UPSTREAM", "idp:8080", "Where the gateway finds this IdP."),
    ])

    # One identity, three apps: how the SLOP-asserted role maps into each app's own
    # role model (SLOP is the identity authority; each app trusts + maps it).
    role_map = (
        "<table><tr><th>SLOP role</th><th>Controller</th><th>Engineering Platform</th><th>Connect</th></tr>"
        "<tr><td>superuser</td><td>superuser</td><td>superuser</td><td>full user</td></tr>"
        "<tr><td>operator</td><td>sysadmin</td><td>operator</td><td>full user</td></tr>"
        "<tr><td>auditor</td><td>auditor</td><td>viewer</td><td>&mdash;</td></tr>"
        "</table>"
    )

    # The Controller owns the deep RBAC + security parameters (they're managed live
    # there, with its own audit trail). Surface them here as one-click deep links —
    # SSO carries the operator's identity, so each opens the exact settings tab with
    # no second login.
    def _clink(tab, label, desc):
        href = f"/controller/?view=settings&amp;tab={tab}"
        return (f"<tr><td><a href='{href}' target=_blank rel=noopener>{escape(label)} &rarr;</a></td>"
                f"<td class=sub style='margin:0'>{escape(desc)}</td></tr>")

    controller_rbac = (
        "<table><tr><th>Controller setting</th><th>What it manages</th></tr>"
        + _clink("admins", "Administrators (RBAC)",
                 "Controller admin accounts, roles (superuser / sysadmin / auditor), and per-account 'sudo on Connect'.")
        + _clink("policy", "Password Policy",
                 "Minimum length and required character classes for Controller passwords.")
        + _clink("enrollacl", "Enrollment Access",
                 "Which source networks may enroll agents, the enrollment pause kill-switch, and rate limits.")
        + _clink("tls", "TLS / Certificates",
                 "The Controller's HTTPS certificate and the hostnames it's valid for.")
        + _clink("controller", "Controller address",
                 "The routable address / hostnames agents and SLEP connect to (host:9000).")
        + _clink("audit", "Audit log",
                 "The record of privileged Controller actions.")
        + "</table>"
    )

    # App-side flags each app reads from its OWN .env; listed here as the SSO reference.
    apps = _cfg_table([
        ("SYSIBLE_WEBGUI_TRUST_SSO", "1 in SLOP", "Controller: trust the gateway-asserted identity."),
        ("SLEP_TRUST_GATEWAY_AUTH", "1 in SLOP", "SLEP: trust the gateway-asserted identity."),
        ("SYSIBLE_CONNECT_TRUST_GATEWAY_AUTH", "1 in SLOP", "Connect: trust the gateway-asserted identity."),
        ("SYSIBLE_CONNECT_CONTROLLER_URL", "https://&lt;host-ip&gt;:9000",
         "Connect auto-attaches to the local Controller at this URL over SSO (no manual login)."),
    ])

    body = (
        "<a class=back href='/'>&larr; Portal</a>"
        f"<div class=top><h1>Administration · Configuration</h1>"
        f"<span class=pill>{escape(sess['username'])} · superuser</span></div>"
        "<p class=sub><a href='/admin'>Accounts</a> · <a href='/account'>Your account</a> · "
        "<a href='/'>Portal &rarr;</a></p>"
        "<p class=sub>SLOP is configured through environment variables in <code>.env</code> "
        "(the gateway host, and each app), applied when the stack is restarted "
        "(<code>sysible_ctl &lt;app&gt; up</code>). This page shows the running values and "
        "documents every parameter &mdash; stored passwords and the shared secret are never "
        "displayed (only whether each is set; a one-time temp password from a reset is shown "
        "once, on the reset screen). "
        "Manage users and password resets under <a href='/admin'>Accounts</a>.</p>"
        f"<fieldset><legend>Identity &amp; passwords</legend>{identity}</fieldset>"
        f"<fieldset><legend>Login throttle &amp; sessions</legend>{logins}</fieldset>"
        f"<fieldset><legend>Single sign-on (trust boundary)</legend>{sso}</fieldset>"
        f"<fieldset><legend>Role mapping (one identity, every app)</legend>"
        "<p class=sub style='margin:.2rem 0 .6rem'>SLOP is the identity authority; each "
        "app trusts the gateway-asserted role and maps it to its own.</p>"
        f"{role_map}</fieldset>"
        f"<fieldset><legend>Controller RBAC &amp; security parameters</legend>"
        "<p class=sub style='margin:.2rem 0 .6rem'>These are managed live in the Controller "
        "(with its own audit trail). Each link opens the exact settings tab &mdash; signed in "
        "by SSO, no second login.</p>"
        f"{controller_rbac}</fieldset>"
        f"<fieldset><legend>App upstreams (set on the gateway)</legend>"
        "<p class=sub style='margin:.2rem 0 .6rem'>Defaults shown &mdash; override in the "
        "gateway's <code>.env</code> if an app runs elsewhere.</p>"
        f"{upstreams}</fieldset>"
        f"<fieldset><legend>Per-app SSO (set in each app's .env)</legend>{apps}</fieldset>"
        f"<fieldset><legend>Data store</legend>{store}</fieldset>"
    )
    return _page("Configuration · SLOP", body, wide=True)


@app.get("/admin/settings", response_class=HTMLResponse)
def admin_settings(request: Request):
    sess, err = _require_super(request)
    if err:
        return err
    return HTMLResponse(_config_page(sess))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
