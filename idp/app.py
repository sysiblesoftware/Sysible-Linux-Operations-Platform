#!/usr/bin/env python3
"""Sysible Linux Operations Platform — the SLOP Identity Provider (CE).

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

import updates

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


_INITIAL_PW_FILE = "initial-password"


def _initial_password_path() -> str:
    return os.path.join(os.environ.get("SLOP_DATA_DIR", "/data"), _INITIAL_PW_FILE)


def _write_initial_password(pw: str) -> str | None:
    """Drop the generated bootstrap password where only root in this container can
    read it. Returns the path, or None if it could not be written."""
    path = _initial_password_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(pw + "\n")
        os.chmod(path, 0o600)
        return path
    except OSError:
        return None


def _clear_initial_password() -> None:
    """Once the bootstrap account has signed in, the file has done its job."""
    try:
        os.unlink(_initial_password_path())
    except OSError:
        pass


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
        # NOT printed. "Shown once" was never once: a container log is kept, and
        # `docker compose logs` replays it to anyone who can read it, for as long
        # as the service lives. Forced change mitigates that only if somebody
        # actually signs in — until then it is a live credential sitting in a log.
        # Write it 0600 instead and print the PATH, so retrieving it is a
        # deliberate act that leaves the secret out of the log entirely.
        where = _write_initial_password(pw)
        if where:
            print("   password: written to " + where + " (mode 0600)")
            print("   read it with:  docker compose exec idp cat " + where)
            print("   it is deleted the first time this account signs in.")
        else:
            # A read-only data dir is the one case where the log is all there is.
            print(f"   password: {pw}    <-- could not write the password file")
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
    # Control characters, for the same reason the backslash is refused above and
    # with the same caveat. Browsers STRIP tab/CR/LF out of a URL before parsing
    # it, so "/<TAB>//evil.example.com" becomes "///evil.example.com" and
    # navigates off-origin — verified in Chromium, which left the origin entirely
    # when a raw tab reached the Location header. Starlette happens to
    # percent-encode them on the way out, exactly as it defangs the backslash, so
    # this is not reachable today; that is a dependency's behaviour and not a
    # guarantee, which is precisely why the backslash is not left to it either.
    if any(c in raw for c in "\t\r\n\x0b\x0c\x00"):
        return "/"
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
--fg:var(--text);--mut:var(--muted);--brand:var(--accent);--topo:url(/brand/topo-dark.svg);}
:root[data-theme="light"]{--bg:#eef1f6;--panel:#ffffff;--panel2:#f3f5f9;--line:#dbe1ea;
--text:#1b2431;--muted:#5b6675;--faint:#8794a4;
--accent:#2f8a37;--accent2:#2f6fe0;--ok:#2f9e4a;--err:#d23a30;--amber:#c98a12;
--shadow:0 1px 2px rgba(20,30,50,.08),0 10px 30px rgba(20,30,50,.10);
--field:#ffffff;--topo:url(/brand/topo-light.svg);}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;
background:var(--bg);color:var(--text);font:15px/1.5 var(--font);-webkit-font-smoothing:antialiased}
/* The platform ground — the same topographic field as the portal, Flashback, the
Visualizer, sysible.com's hero and the Workstation wallpapers. One field,
generated by portal/brand/make_topo.py and served by the gateway at /brand/. */
.bg{position:fixed;inset:0;z-index:-1;pointer-events:none;
background:var(--topo) center / cover no-repeat,
radial-gradient(70% 55% at 15% -10%,rgba(67,160,71,.12),transparent 60%),
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
span.sub{color:var(--muted);font-size:12.5px}
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
/* Toast stack — same shape and behaviour as the Controller console's, so the
   two feel like one product. Fixed to the corner, auto-dismissed by JS. */
.toast-stack{position:fixed;right:16px;bottom:16px;display:flex;flex-direction:column;
gap:8px;z-index:60;width:min(380px,90vw)}
.toast{display:flex;gap:10px;align-items:flex-start;padding:.65rem .8rem;border-radius:10px;
border:1px solid var(--line);background:var(--panel);box-shadow:0 8px 24px rgba(0,0,0,.35);
font-size:13px;animation:toast-in .18s ease-out}
@keyframes toast-in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.toast.success{border-color:#4ec07a}.toast.error{border-color:#e5534b}
.toast.warn{border-color:#e0a83a}
.toast-title{font-weight:600;margin-bottom:2px;white-space:nowrap}
.toast-msg{color:var(--muted);line-height:1.45;word-break:break-word}
.toast-x{margin-left:auto;background:none;border:none;color:var(--muted);cursor:pointer;
font-size:16px;line-height:1;padding:0 2px}
.toast-x:hover{color:var(--text)}
/* "Updates available" pill — shown only when something is actually behind. */
.upd-pill{display:none;align-items:center;gap:6px;border:1px solid #e0a83a;color:#e0a83a;
background:none;border-radius:20px;padding:.15rem .6rem;font-size:12px;cursor:pointer;
font-family:inherit;text-decoration:none}
.upd-pill.on{display:inline-flex}
.upd-pill b{background:#e0a83a;color:#0d1117;border-radius:9px;padding:0 6px;font-size:11px}
/* Buttons that are NOT a form's submit.
   The base `button{}` rule above is the login form's full-width green submit, and
   every button on every admin page inherited it — including the Software &
   services controls, which ask for .btn/.ghost/.sm and found none of them defined
   here. Four per product rendered as four full-width green bars, so the page was
   2173px wide inside a 760px card: buttons ran up to 993px past its right edge
   and every viewport had a horizontal scrollbar. This is the vocabulary those
   pages were already written against. */
.btn{width:auto;margin:0;padding:7px 13px;border-radius:9px;font-size:13px;
font-weight:600;background:var(--accent);color:#04120a;border:1px solid transparent}
.btn.ghost{background:var(--panel2);color:var(--text);border-color:var(--line);font-weight:500}
.btn.ghost:hover{border-color:var(--accent);filter:none}
.btn.sm{padding:5px 10px;font-size:12px}
.btn.danger{background:transparent;color:var(--err);
border-color:color-mix(in srgb,var(--err) 45%,var(--line))}
.btn.danger:hover{border-color:var(--err);filter:none}
.btn:disabled{opacity:.45;cursor:not-allowed;filter:none}

/* A section heading inside a card. The accounts page used a <fieldset>+<legend>
   for "Add a user" and nothing at all for the list above it, so the two halves of
   the page were labelled in two different ways and only one of them was labelled.
   One heading style for both. */
h2.sect{font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;
letter-spacing:.06em;margin:1.6em 0 .2em}
h2.sect:first-of-type{margin-top:1.1em}
/* Accounts — one line per account, same shape as the product list below.
   It was a four-column table, and the columns were the problem: with one account
   the Password header sat 300px from the button it labelled, because column 4
   (Delete) is empty for your own row and the widths come from the content. Worse,
   the role form was a .row, so `.row>*{flex:1}` made the Set button exactly as
   wide and as loud as the select it confirms — the accent green, the strongest
   colour on the page, spent on the least consequential control.
   Here the account is the row, the role sits with its confirm, and the actions
   that change nothing by themselves are quiet. The only accent button on the page
   is the one that creates an account. */
/* ONE grid for the whole list, with each row as display:contents, so the three
   columns are tracks of a single grid and line up down the page. A grid per row
   does not: the tracks are then sized per row, so with seven accounts every
   select started at a different x depending on how long the name above it was,
   which is the jitter that made the old table feel untidy in the first place. */
.accts{display:grid;grid-template-columns:minmax(0,1fr) auto auto;
margin-top:.9rem;border:1px solid var(--line);border-radius:12px}
.acct{display:contents}
/* NOT align-self:center. Each cell draws the row's separator, so a cell only as
   tall as its own content puts its border at its own top edge — with a wrapped
   name beside a single-line select, one row's rule came out as three disconnected
   segments at three different heights. Stretched, every cell is the row's height,
   the three borders meet, and the cells centre their own content instead. */
/* The spacing BETWEEN cells is padding inside them, never a margin or a grid
   column-gap. Each cell draws its own slice of the row's separator, and a gap
   between cells is a gap with no border in it: the rule came out as three
   segments with two notches punched through it. */
.accts>.acct>*{padding:.7rem .45rem;border-top:1px solid var(--line)}
.accts>.acct>*:first-child{padding-left:.95rem}
.accts>.acct>*:last-child{padding-right:.95rem}
.accts>.acct:first-child>*{border-top:0}
.acct-name{font-weight:600;min-width:0;line-height:1.35;font-size:14px;
display:flex;align-items:center;gap:.45rem;flex-wrap:wrap}
/* Left-aligned inside their track, not right: your own row has no Delete, and
   pushing its cluster to the right edge moved its Reset password 63px out of
   line with every other row's. Aligning left keeps the Reset buttons a column
   and leaves the gap where the missing Delete actually is. */
.acct-role,.acct-act{display:flex;gap:.4rem;align-items:center;justify-content:flex-start}
.acct select{width:auto;min-width:140px;padding:6px 10px;font-size:13px}
/* A badge, not the bare muted text .pill gives: "must change" is a state of the
   account and reads as one. */
.tag{display:inline-block;border:1px solid var(--line);border-radius:20px;
padding:.05rem .5rem;font-size:11px;color:var(--muted);background:var(--panel2)}
/* No .tag.warn for "must change password". Every account created here starts
   that way, so amber on all of them paints a list of perfectly healthy accounts
   as a wall of warnings and leaves no colour for something actually wrong. */
/* The form's one real action. Full width is the LOGIN form's shape and it was
   inherited here, so "Create user" was a 672px green bar under three fields. */
.formact{display:flex;justify-content:flex-end;align-items:center;gap:.7rem;margin-top:1.1em}
.formact .hint{color:var(--muted);font-size:12px;margin-right:auto}
@media (max-width:640px){
  /* One column: the row's three cells stack, and the padding/border rules above
     would then draw a line between a name and its own controls. */
  .accts{grid-template-columns:minmax(0,1fr)}
  .accts>.acct>*{padding:.15rem .95rem;border-top:0}
  .accts>.acct>*:first-child{padding-top:.7rem;border-top:1px solid var(--line)}
  .accts>.acct>*:last-child{padding-bottom:.7rem}
  .accts>.acct:first-child>*:first-child{border-top:0}
  .acct-role,.acct-act{justify-content:flex-start;margin-left:0;flex-wrap:wrap}
  .formact{flex-wrap:wrap}
  .formact .hint{margin-right:0;flex:1 0 100%}
}
/* Software & services — one line per product.
   It was a table whose every row carried four equally loud buttons, so nothing
   on the page said which of them mattered. A product needs at most ONE action
   right now (update it, when there is one); restart/stop/start/recreate are
   recovery, wanted rarely and dangerous to hit by accident, so they sit behind
   the row's own toggle instead of competing with the thing you came for. */
.prods{margin-top:.9rem;border:1px solid var(--line);border-radius:12px}
.prod{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr) auto;
gap:.35rem .9rem;align-items:center;padding:.8rem .95rem;border-top:1px solid var(--line)}
.prod:first-child{border-top:0}
.prod-name{font-weight:600;min-width:0;line-height:1.35}
.prod-meta{grid-column:1;color:var(--muted);font-size:12px;min-width:0;line-height:1.4}
.prod-state{min-width:0;font-size:13px;line-height:1.4}
.prod-act{display:flex;gap:.4rem;align-items:center;justify-self:end;flex-wrap:wrap}
.prod-controls{grid-column:1/-1;display:flex;gap:.4rem;flex-wrap:wrap;align-items:center;
margin-top:.45rem;padding-top:.6rem;border-top:1px dashed var(--line)}
/* An author `display` beats the hidden attribute's UA `display:none`, so without
   this every Manage panel renders permanently open — which is the busy row of
   four buttons the toggle exists to put away. */
.prod-controls[hidden]{display:none}
.prod-controls .hint{color:var(--muted);font-size:12px;margin-left:.2rem}
/* The row you came to act on. A tint, not another button. */
.prod.due{background:color-mix(in srgb,#e0a83a 8%,transparent)}
@media(max-width:640px){
  .prod{grid-template-columns:1fr}
  .prod-act{justify-self:start;margin-top:.3rem}
}
/* Status, as one readable phrase plus its detail underneath. */
.st b{font-weight:600}
.st.ok b{color:#4ec07a}
.st.due b{color:#e0a83a}
.st.unk b{color:var(--muted)}
.st-note{display:block;color:var(--muted);font-size:12px;margin-top:2px;line-height:1.4}
.st-note.warn{color:#e0a83a}
/* The explanation, available without being in the way. */
details.help{margin:.5rem 0 .2rem;font-size:12.5px;color:var(--muted)}
details.help>summary{cursor:pointer;color:var(--accent2);list-style:none}
details.help>summary::-webkit-details-marker{display:none}
details.help>summary:hover{text-decoration:underline}
details.help p{margin:.5rem 0 0;line-height:1.55}
.toolbar{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin:.9rem 0 .2rem}
.toolbar .sub{margin:0}
.upd-row td{vertical-align:middle}
/* Hosted app administration. The frame is the app's REAL settings UI on the same
   origin — sized generously because it contains a full console, not a widget. */
.prodtabs{display:flex;gap:.4rem;flex-wrap:wrap;margin:.6rem 0 .5rem}
.chip.prod{font-size:13px;padding:.3rem .8rem;font-weight:600}
.subtabs{display:flex;gap:.35rem;flex-wrap:wrap;margin:.2rem 0 .7rem}
.chip{display:inline-block;padding:.2rem .65rem;border:1px solid var(--line);
border-radius:20px;font-size:12.5px;color:var(--muted);text-decoration:none}
.chip:hover{color:var(--text);border-color:var(--accent)}
.chip.on{color:var(--text);border-color:var(--accent);background:var(--panel2)}
.appframe{width:100%;height:min(72vh,900px);border:1px solid var(--line);
border-radius:12px;background:var(--panel);display:block}
.upd-log{margin:.6rem 0 0;padding:.6rem .8rem;background:var(--field,#0d1320);
border:1px solid var(--line);border-radius:8px;max-height:40vh;overflow:auto;
font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;
line-height:1.5;white-space:pre-wrap;word-break:break-word}
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


# Transient notifications, mirroring the Controller console's: a small stack in
# the corner, auto-dismissed, dismissable by hand. Defined once here so every
# admin page can call pushToast(msg, {title, kind}) without repeating it.
# RAW string: this is JavaScript, and its escapes are JS escapes. In a normal
# Python string "\n" becomes a real newline, which lands INSIDE a JS string
# literal and makes the whole file a syntax error that only shows up in the
# browser console.
_TOAST_JS = r"""
window.pushToast=function(msg,opts){
  opts=opts||{};
  var stack=document.getElementById('toasts'); if(!stack)return;
  var el=document.createElement('div');
  el.className='toast '+(opts.kind||'info');
  var body=document.createElement('div');
  if(opts.title){var t=document.createElement('div');t.className='toast-title';
    t.textContent=opts.title;body.appendChild(t);}
  var m=document.createElement('div');m.className='toast-msg';m.textContent=msg;
  body.appendChild(m); el.appendChild(body);
  var x=document.createElement('button');x.className='toast-x';x.type='button';
  x.setAttribute('aria-label','Dismiss');x.textContent='\u00d7';
  x.onclick=function(){el.remove();};
  el.appendChild(x); stack.appendChild(el);
  // Sticky on request: an update that failed should stay put until it is read.
  if(opts.ttl!==0)setTimeout(function(){el.remove();},opts.ttl||9000);
};
"""


# The "Updates available" pill. Hidden by default and revealed by the poll
# below only when a product is genuinely behind, so a current platform shows
# nothing — a permanently-lit badge is noise, not information.
_PILL = "<a class=upd-pill id=updpill href='/admin/updates' title='A product has an update available'>&#11014; Updates available <b>0</b></a> "
# "Set" confirms a role change, so with nothing changed it has nothing to do.
# Disabling it says that, and keeps the row quiet until there is something to
# apply. Rendered ENABLED and disabled from here, so a browser with no JavaScript
# gets a working button rather than a dead one.
_SETROLE_JS = (
    "<script>(function(){"
    "document.querySelectorAll('form.acct-role').forEach(function(f){"
    "var sel=f.querySelector('select'),btn=f.querySelector('.js-setrole');"
    "if(!sel||!btn)return;"
    "function sync(){btn.disabled=(sel.value===btn.dataset.was);}"
    "sel.addEventListener('change',sync);sync();});"
    # Deleting an account was one click with nothing between it and the deletion.
    # window.confirm is what this codebase already uses for the destructive things
    # (updating SLOP, restoring a config in Flashback), so it is what this uses.
    "document.querySelectorAll('form.js-del').forEach(function(f){"
    "f.addEventListener('submit',function(e){"
    "if(!window.confirm('Delete the account \"'+f.dataset.user+'\"? They lose access to "
    "every app immediately. This cannot be undone.'))e.preventDefault();});});"
    "})();</script>"
)

_PILL_POLL = "<script>fetch('/admin/updates/status',{cache:'no-store'}).then(function(r){return r.ok?r.json():null;}).then(function(d){if(!d||!d.apps)return;var n=d.apps.filter(function(a){return a.available;}).length;var p=document.getElementById('updpill');if(p&&n>0){p.classList.add('on');p.querySelector('b').textContent=n;}}).catch(function(){});</script>"


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
        f"<div class=brand>{_MARK}<div class=brand-text>Sysible <b>Linux Operations Platform</b></div></div>"
        f"{body}"
        f"<div class=foot>Sysible Linux Operations Platform · Community Edition</div>"
        f"</div>"
        # Toast stack: one per page, outside the card so it can sit in the corner.
        # Pages that have nothing to say simply never call pushToast.
        f"<div class=toast-stack id=toasts></div>"
        f"<script>{_TOAST_JS}</script>"
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

# ---------------------------------------------------------------------------
# Request body cap. Same shape as Flashback's and the Visualizer's, which both
# had one while this service did not: without it an unauthenticated POST can make
# the process buffer an arbitrarily large body before any handler — and before any
# authentication — runs. Small here on purpose: everything this service accepts is
# a short form or a small JSON object.
# ---------------------------------------------------------------------------
_MAX_REQUEST_BYTES = int(os.environ.get("SLOP_MAX_REQUEST_BYTES", str(1 * 1024 * 1024)))


class _BodyLimitASGI:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for k, v in scope.get("headers") or []:
            if k == b"content-length" and v.isdigit() and int(v) > self.max_bytes:
                return await self._too_large(scope, send)
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return
            body += message.get("body", b"")
            more_body = message.get("more_body", False)
            # A chunked body never declares its length, so the running total is
            # the only thing that can stop it.
            if len(body) > self.max_bytes:
                return await self._too_large(scope, send)
        sent = False

        async def replay_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)

    async def _too_large(self, scope, send):
        async def _noop_receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        await JSONResponse({"detail": "Request body too large."}, status_code=413)(
            scope, _noop_receive, send)


app.add_middleware(_BodyLimitASGI, max_bytes=_MAX_REQUEST_BYTES)


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
    "base-uri 'none'; form-action 'self'; frame-ancestors 'self'; object-src 'none'"
)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("Content-Security-Policy", _CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
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
        # Name everything this one sign-in actually covers. It listed the three
        # fronted apps and predated Flashback and the Visualizer, so the page that
        # exists to explain "one login for all of it" quietly undersold itself.
        "<h1>Sign in</h1><p class=sub>One sign-in for Controller, Engineering "
        "Platform, Connect, Flashback and the Visualizer.</p>"
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
    _clear_initial_password()
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
    accts = ""
    for r in rows:
        opts = "".join(
            f"<option value='{ro}'{' selected' if ro == r['role'] else ''}>{ro}</option>"
            for ro in ROLES
        )
        user = escape(r["username"])
        is_self = r["username"] == sess["username"]
        # Beside the name, not under it: a badge is a property of the account, and
        # on its own line it pushed the row's controls off the name's baseline for
        # the sake of two words. "this is you" is also the only explanation this
        # row has for its missing Delete, which is a rule rather than an accident.
        tags = ""
        if is_self:
            tags += "<span class=tag>this is you</span>"
        if r["must_change"]:
            tags += "<span class='tag warn'>must change password</span>"
        accts += (
            f"<div class=acct>"
            f"<div class=acct-name>{user}{tags}</div>"
            + f"<form class=acct-role method=post action='/admin/users/{user}/role'>"
            f"{hidden}<select name=role aria-label='Role for {user}'>{opts}</select>"
            # Enabled in the markup and disabled by the script below, so the page
            # still works with no JavaScript at all.
            f"<button class='btn ghost sm js-setrole' type=submit "
            f"data-was='{escape(r['role'])}'>Set</button></form>"
            f"<div class=acct-act>"
            f"<form method=post action='/admin/users/{user}/reset'>"
            f"{hidden}<button class='btn ghost sm' type=submit>Reset password</button></form>"
            + ("" if is_self else
               f"<form method=post action='/admin/users/{user}/delete' class=js-del "
               f"data-user='{user}'>"
               # Ghost, not danger-red. Red on every row of a seven-account list is
               # a wall of alarm over the ordinary act of removing a user, and it
               # leaves nothing louder for the confirm. The confirm is where the
               # weight belongs — and there was none at all before, so one misclick
               # removed an account outright.
               f"{hidden}<button class='btn ghost sm' type=submit>Delete</button></form>")
            + "</div></div>"
        )
    role_opts = "".join(f"<option value='{ro}'>{ro}</option>" for ro in ROLES)
    n = len(rows)
    body = (
        "<a class=back href='/'>&larr; Portal</a>"
        f"<div class=top><h1>Administration · Accounts</h1><span class=pill>{escape(sess['username'])} · superuser</span></div>"
        f"<p class=sub>{_PILL}<a href='/admin/settings'>Configuration</a> · <a href='/admin/apps'>Apps</a> · <a href='/admin/updates'>Software &amp; services</a> · <a href='/account'>Your account</a> · "
        "<a href='/'>Portal →</a><br>One credential signs a user into all three apps, "
        "and the role set here is the role every app enforces.</p>"
        f"{_msg(msg, kind)}"
        f"<h2 class=sect>{n} account{'' if n == 1 else 's'}</h2>"
        f"<div class=accts>{accts}</div>"
        "<h2 class=sect>Add a user</h2>"
        "<form method=post action='/admin/users' autocomplete=off>"
        f"{hidden}"
        "<div class=row><div><label for=nu>Username</label>"
        # autocomplete: without it the browser treats this as a sign-in form and
        # fills it with the SIGNED-IN admin's own saved credentials — one stray
        # submit away from a second superuser holding your password. new-password
        # is the token that actually stops it; off on the username is advisory.
        "<input id=nu name=username required autocomplete=off spellcheck=false></div>"
        f"<div><label for=nr>Role</label><select id=nr name=role>{role_opts}</select></div></div>"
        "<label for=np>Temporary password</label>"
        "<input id=np type=password name=password required autocomplete=new-password>"
        "<div class=formact><span class=hint>They are asked to change it at first sign-in.</span>"
        "<button class=btn type=submit>Create user</button></div></form>"
    )
    return _page("Accounts · SLOP", body + _PILL_POLL + _SETROLE_JS, wide=True)


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
         # It is no longer printed anywhere: this page was still sending the
         # operator to the logs for a password that has not been in them since
         # it moved to a 0600 file.
         "Bootstrap admin password. Empty = a random one is generated into "
         "<span class=mono>$SLOP_DATA_DIR/initial-password</span> (mode 0600), never the log. "
         "It is deleted the first time that account signs in."),
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
        # Points at the HOSTED panel, not the app in a new tab.
        href = f"/admin/apps?app=controller&amp;tab={tab}"
        return (f"<tr><td><a href='{href}'>{escape(label)} &rarr;</a></td>"
                f"<td class=sub style='margin:0'>{escape(desc)}</td></tr>")

    # Superseded by /admin/apps, which HOSTS these panels instead of linking out.
    # Kept as a compact index so the Configuration page still documents what each
    # one manages.
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
        f"<p class=sub>{_PILL}<a href='/admin'>Accounts</a> · <a href='/admin/apps'>Apps</a> · <a href='/admin/updates'>Software &amp; services</a> · <a href='/account'>Your account</a> · "
        "<a href='/'>Portal &rarr;</a></p>"
        "<p class=sub>SLOP is configured through environment variables in <code>.env</code> "
        "(the gateway host, and each app), applied when the stack is restarted "
        "(<code>sysiblectl &lt;app&gt; up</code>). This page shows the running values and "
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
    return _page("Configuration · SLOP", body + _PILL_POLL, wide=True)


# ---------------------------------------------------------------------------
# Administration → Apps: each app's administration, HOSTED HERE
# ---------------------------------------------------------------------------
# Administration used to LINK OUT to each app's settings in a new tab, which is
# not consolidation — it is a bookmark. These pages host the real thing: SLOP is
# one origin, so the app's own settings UI is embedded in-page (frame-ancestors
# 'self'). You stay in Administration, and there is no second copy of eight
# panels to drift out of step with the app that owns them.
#
# Accounts are the exception: they are NOT hosted, they are GONE from the apps.
# SLOP is the identity authority, so "Administrators" and "My Account" would be a
# second account store — which is the very thing single sign-on removes.
_APP_ADMIN = {
    "controller": ("Sysible Controller", "/controller/?view=settings", [
        ("controller", "Controller", "Software updates, address and port, restart."),
        ("policy", "Password policy", "Length and character classes."),
        ("enrollacl", "Enrollment access", "Which networks may enroll agents; the kill-switch."),
        ("tls", "TLS / certificates", "The console's certificate."),
        ("license", "License", "Edition and license key."),
        ("audit", "Audit log", "Privileged Controller actions."),
    ]),
    "slep": ("Sysible Linux Engineering Platform", "/slep/", []),
    "connect": ("Sysible Connect", "/connect/", []),
}


def _apps_page(sess: sqlite3.Row, app_key: str, tab: str) -> str:
    if app_key not in _APP_ADMIN:
        app_key = "controller"
    label, base, tabs = _APP_ADMIN[app_key]
    if tabs and tab not in {t for t, _, _ in tabs}:
        tab = tabs[0][0]

    # The IdP styles .chip, not .btn — a .btn here rendered as a bare blue link
    # and the tab strip read as unfinished.
    prod = "".join(
        f"<a class='chip prod{' on' if k == app_key else ''}' "
        f"href='/admin/apps?app={k}'>{escape(_APP_ADMIN[k][0])}</a>"
        for k in _APP_ADMIN)

    if tabs:
        sub = "<div class=subtabs>" + "".join(
            f"<a class='chip{' on' if t == tab else ''}' "
            f"href='/admin/apps?app={escape(app_key)}&amp;tab={escape(t)}' "
            f"title='{escape(desc)}'>{escape(lbl)}</a>"
            for t, lbl, desc in tabs) + "</div>"
        src = f"{base}&tab={tab}"
    else:
        sub = ""
        src = base

    body = (
        "<a class=back href='/'>&larr; Portal</a>"
        f"<div class=top><h1>Administration · Apps</h1>"
        f"<span class=pill>{escape(sess['username'])} · superuser</span></div>"
        f"<p class=sub>{_PILL}<a href='/admin'>Accounts</a> · "
        "<a href='/admin/settings'>Configuration</a> · "
        "<a href='/admin/updates'>Software &amp; services</a> · "
        "<a href='/account'>Your account</a> · <a href='/'>Portal &rarr;</a></p>"
        "<p class=sub>Each app&rsquo;s own administration, hosted here &mdash; you are signed "
        "in once and stay in Administration. Accounts and password resets are NOT here per "
        "app: SLOP owns identity, so they live under "
        "<a href='/admin'>Accounts</a> for every app at once.</p>"
        f"<div class=prodtabs>{prod}</div>"
        f"{sub}"
        f"<iframe class=appframe id=appframe src='{escape(src)}' "
        f"title='{escape(label)} administration'></iframe>"
        f"<p class=sub>If the panel below is blank, open "
        f"<a href='{escape(src)}' target=_blank rel=noopener>{escape(label)} &rarr;</a> "
        f"directly &mdash; the app may still be starting.</p>"
    )
    return _page("Apps · SLOP", body, wide=True)


@app.get("/admin/apps", response_class=HTMLResponse)
def admin_apps(request: Request, app: str = "controller", tab: str = ""):
    sess, err = _require_super(request)
    if err:
        return err
    return HTMLResponse(_apps_page(sess, app, tab))


# ---------------------------------------------------------------------------
# Administration → Software updates
# ---------------------------------------------------------------------------
# The IdP holds no Docker socket. It asks the updater sidecar what is behind and,
# on a superuser's click, to update ONE allowlisted product. Everything here is
# superuser-gated, and the apply route carries the same origin + CSRF guard as
# every other admin mutation.
_UPDATES_JS = r"""
(function(){
  var rows={}, polling=null;
  function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;
    if(x!=null)e.textContent=x;return e;}
  // One line an operator can read at a glance: what state this product is in,
  // with the version detail and any blocker underneath rather than crammed in
  // beside it.
  function fmtRow(a){
    var td=document.getElementById('u-'+a.key); if(!td)return;
    var row=document.getElementById('p-'+a.key);
    td.innerHTML='';
    if(row)row.classList.remove('due');
    if(!a.installed){
      td.appendChild(st('unk','Not installed', 'on this host'));
      hide('b-'+a.key); hide('m-'+a.key);
      paintServices(a); paintActions(a);
      return;
    }
    if(a.checked===false){
      td.appendChild(st('unk',"Couldn't check", a.reason||''));
    } else if(a.available){
      td.appendChild(st('due','Update available',
        (a.current&&a.latest)?(a.current+' \u2192 '+a.latest):''));
      if(row)row.classList.add('due');
    } else {
      td.appendChild(st('ok','Up to date', a.current?('at '+a.current):''));
    }
    // A reason alongside "up to date" is a BLOCKER (a dirty checkout), not a
    // footnote — it is the thing standing between this row and being updatable.
    if(a.reason && a.checked!==false){
      var n=el('div','st-note warn', a.reason);
      td.appendChild(n);
    }
    var btn=document.getElementById('b-'+a.key);
    if(btn){ btn.hidden=!a.can_update; btn.disabled=!a.can_update; }
    paintServices(a);
    paintActions(a);
  }
  function st(kind, label, detail){
    var w=el('div','st '+kind);
    var b=el('b',null,label); w.appendChild(b);
    if(detail)w.appendChild(el('span','st-note',detail));
    return w;
  }
  function hide(id){ var e=document.getElementById(id); if(e)e.hidden=true; }
  // What is actually RUNNING. "Up to date" said nothing about whether the thing
  // was up, which is how a stack could sit dead behind a green row.
  function paintServices(a){
    var box=document.getElementById('s-'+a.key); if(!box)return;
    box.innerHTML='';
    var svcs=a.services||[];
    if(!svcs.length){ return; }
    var bad=svcs.filter(function(s){return s.state!=='running';});
    var txt=svcs.length+' service'+(svcs.length===1?'':'s')+': ';
    var s1=el('span',null,txt);
    box.appendChild(s1);
    var lbl=el('span',null, bad.length ? (bad.length+' not running') : 'all running');
    lbl.style.color = bad.length ? '#e0a83a' : '#4ec07a';
    box.appendChild(lbl);
    if(bad.length){
      box.appendChild(el('span','sub',' \u2014 '+bad.map(function(s){
        return s.service+' ('+(s.state||'?')+')';}).join(', ')));
    }
  }
  // Buttons come from the API's own allowlist, so a refused action can never be
  // offered — the rule and the button cannot disagree.
  // Buttons come from the API's own allowlist, so a refused action can never be
  // offered — the rule and the button cannot disagree. They live behind the row's
  // Manage toggle: four of them per product, always on screen, gave every row the
  // same weight as the one thing the page is for, and put Stop one stray click
  // away. Rare and interrupting is exactly what a disclosure is for.
  function paintActions(a){
    var box=document.getElementById('a-'+a.key);
    var tog=document.getElementById('m-'+a.key);
    if(!box)return;
    box.innerHTML='';
    var acts=(a.installed && (a.actions||[]).length) ? a.actions : [];
    if(tog){
      tog.hidden = !acts.length;
      if(!acts.length){ box.hidden=true; tog.setAttribute('aria-expanded','false'); }
    }
    if(!acts.length) return;
    acts.forEach(function(act){
      var cls = (act==='stop') ? 'btn danger sm' : 'btn ghost sm';
      var b=el('button', cls, act.charAt(0).toUpperCase()+act.slice(1));
      b.onclick=function(){ runAction(a.key, act, b); };
      box.appendChild(b);
    });
    box.appendChild(el('span','hint', a.key==='slop'
      ? 'Stopping the platform from here would take this page down with it.'
      : 'Brief downtime while the containers come back.'));
  }
  // Bound once, on the list, so a repaint cannot lose the handler or stack a
  // second one on top of it.
  function bindManage(){
    var list=document.getElementById('updtable'); if(!list||list._bound)return;
    list._bound=true;
    list.addEventListener('click',function(ev){
      var t=ev.target.closest('button[data-manage]'); if(!t)return;
      var key=t.getAttribute('data-manage');
      var box=document.getElementById('a-'+key); if(!box)return;
      var open=box.hidden;
      box.hidden=!open;
      t.setAttribute('aria-expanded', open?'true':'false');
      t.textContent = open ? 'Hide' : 'Manage';
    });
  }
  function runAction(key, act, b){
    // Stop and restart interrupt service; make that explicit before doing it.
    var warn = (act==='stop')
      ? 'Stop '+key+"? Its services will be DOWN until you start them again."
      : (act==='restart'||act==='recreate')
        ? (act==='restart'?'Restart ':'Recreate ')+key+'? Brief downtime while it comes back.'
        : null;
    if(warn && !window.confirm(warn)) return;
    b.disabled=true;
    var body=new URLSearchParams();
    body.set('csrf', document.getElementById('csrf').value);
    body.set('app', key); body.set('action', act);
    fetch('/admin/updates/action',{method:'POST',body:body,
      headers:{'Content-Type':'application/x-www-form-urlencoded'}})
      .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
      .then(function(res){
        if(!res.ok){ pushToast(res.d.detail||'Refused',
          {title:'Software & services',kind:'warn',ttl:0}); b.disabled=false; return; }
        pushToast(res.d.message||(act+' '+key),{title:'Software & services'});
        load();
      })
      .catch(function(e){ pushToast(String(e.message||e),
        {title:'Software & services',kind:'error',ttl:0}); b.disabled=false; });
  }
  // A product the API knows about but the server-rendered table does not: build
  // its row rather than dropping it. The skeleton exists so an unreachable updater
  // still lists the known products; it must not become a filter that hides one
  // that was added to the updater's allowlist afterwards.
  function ensureRow(a){
    if(document.getElementById('u-'+a.key)) return;
    var list=document.getElementById('updtable'); if(!list) return;
    var row=el('div','prod'); row.id='p-'+a.key;
    var name=el('div','prod-name', a.label||a.key);
    var state=el('div','prod-state'); state.id='u-'+a.key;
    var act=el('div','prod-act');
    var btn=el('button','btn','Update now'); btn.id='b-'+a.key;
    btn.setAttribute('data-app',a.key); btn.hidden=true; btn.disabled=true;
    btn.addEventListener('click',function(){ startUpdate(a.key, btn); });
    var tog=el('button','btn ghost sm','Manage'); tog.id='m-'+a.key;
    tog.setAttribute('data-manage',a.key); tog.setAttribute('aria-expanded','false');
    tog.hidden=true;
    act.appendChild(btn); act.appendChild(tog);
    var meta=el('div','prod-meta'); meta.id='s-'+a.key;
    var ctrls=el('div','prod-controls'); ctrls.id='a-'+a.key; ctrls.hidden=true;
    row.appendChild(name); row.appendChild(state); row.appendChild(act);
    row.appendChild(meta); row.appendChild(ctrls);
    list.appendChild(row);
  }
  function paint(d){
    bindManage();
    (d.apps||[]).forEach(ensureRow);
    (d.apps||[]).forEach(fmtRow);
    resolveUnreported(d.apps||[]);
    var n=(d.apps||[]).filter(function(a){return a.available;}).length;
    var pill=document.getElementById('updpill');
    if(pill){pill.classList.toggle('on',n>0);
      var b=pill.querySelector('b'); if(b)b.textContent=n;}
    // "Update all" offers exactly what the rows say is updatable — a blocked
    // checkout is not silently swept in, and the count on the button is the
    // number of rows that would actually move.
    var able=(d.apps||[]).filter(function(a){return a.can_update;});
    var all=document.getElementById('updateall');
    if(all){
      all.hidden = able.length < 2;
      all.textContent = 'Update all (' + able.length + ')';
      all.dataset.slop = able.some(function(a){return a.key==='slop';}) ? '1' : '';
    }
    if(d.job)showJob(d.job);
  }
  // The skeleton names every product this platform knows about, so the list is
  // still useful when the updater cannot be reached. But when the updater DOES
  // answer and simply does not manage one of them, that row kept its initial
  // "checking\u2026" forever \u2014 a row that looks like it is still working, on a
  // page that has finished. Say what is actually true instead.
  function resolveUnreported(apps){
    var known={};
    apps.forEach(function(a){ known[a.key]=1; });
    var rows=document.querySelectorAll('#updtable .prod');
    Array.prototype.forEach.call(rows, function(row){
      var key=(row.id||'').replace(/^p-/,'');
      if(!key||known[key]) return;
      var td=document.getElementById('u-'+key); if(!td) return;
      td.innerHTML='';
      td.appendChild(st('unk','Not managed here',
        'the updater does not track this product'));
      hide('b-'+key); hide('m-'+key);
      var box=document.getElementById('a-'+key);
      if(box){ box.innerHTML=''; box.hidden=true; }
    });
  }
  function showJob(j){
    var box=document.getElementById('joblog');
    if(!box||!j)return;
    box.hidden=false;
    // In a batch, "Update of connect — running" alone hides how far along it is,
    // which on a four-product run is the only thing worth knowing.
    var where = (j.queue && j.queue.length>1)
      ? (' (' + ((j.index||0)+1) + ' of ' + j.queue.length + ')') : '';
    document.getElementById('jobtitle').textContent =
      (j.action ? (j.action.charAt(0).toUpperCase()+j.action.slice(1)+' of ')
                : 'Update of ')+j.app+where+' \u2014 '+j.state;
    box.textContent=(j.log||[]).join('\n');
    box.scrollTop=box.scrollHeight;
  }
  function load(fresh){
    return fetch('/admin/updates/status'+(fresh?'?refresh=1':''),{cache:'no-store'})
      .then(function(r){return r.ok?r.json():Promise.reject(new Error('HTTP '+r.status));})
      .then(function(d){
        if(d.error){pushToast(d.error,{title:'Software & services',kind:'warn',ttl:0});return;}
        paint(d);
      })
      .catch(function(e){pushToast(String(e.message||e),{title:'Software & services',kind:'error'});});
  }
  function poll(selfUpdate){
    if(polling)return;
    polling=setInterval(function(){
      fetch('/admin/updates/job',{cache:'no-store'})
        .then(function(r){return r.json();})
        .then(function(d){
          var j=d.job; if(!j)return;
          showJob(j);
          if(j.state!=='running'){
            clearInterval(polling); polling=null;
            pushToast(j.message||('Update '+j.state),
                      {title:'Software & services',
                       kind:j.state==='succeeded'?'success':'error',
                       ttl:j.state==='succeeded'?9000:0});
            load();
            // The containers were just recreated with new code, so this page and
            // every asset it is holding are from the build that was replaced.
            // Reloading is the difference between "it updated" and "it updated and
            // I am looking at it". Long enough for the toast to be read first.
            if(j.state==='succeeded'){
              setTimeout(function(){location.reload();},2500);
            }
          }
        })
        .catch(function(){
          // SLOP updating ITSELF recreates this container mid-job, so losing the
          // connection here is the expected outcome, not a failure.
          if(selfUpdate){
            clearInterval(polling); polling=null;
            pushToast('SLOP is rebuilding and will restart \u2014 reload this page in a moment.',
                      {title:'Software & services',kind:'warn',ttl:0});
          }
        });
    },2000);
  }
  function startUpdate(key, b){
        if(key==='slop' && !confirm('Updating SLOP rebuilds the gateway and this console. '
            +'You will be signed out briefly. Continue?'))return;
        b.disabled=true;
        var fd=new FormData();
        fd.append('csrf',document.getElementById('csrf').value);
        fd.append('app',key);
        fetch('/admin/updates/apply',{method:'POST',body:fd})
          .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
          .then(function(res){
            if(!res.ok){pushToast(res.d.detail||'Could not start the update',
                                  {title:'Software & services',kind:'error',ttl:0});
              b.disabled=false; return;}
            pushToast(res.d.message||('Updating '+key),{title:'Software & services'});
            poll(key==='slop');
          })
          .catch(function(e){pushToast(String(e.message||e),
                {title:'Software & services',kind:'error',ttl:0}); b.disabled=false;});
  }
  function startUpdateAll(b){
    // SLOP is updated LAST by the server, so the sign-out comes at the end — but
    // it still happens, and it is the one consequence worth confirming.
    if(b.dataset.slop && !confirm('This updates every product, SLOP last. '
        +'Updating SLOP rebuilds the gateway and this console, so you will be '
        +'signed out briefly at the end. Continue?'))return;
    b.disabled=true;
    var fd=new FormData();
    fd.append('csrf',document.getElementById('csrf').value);
    fetch('/admin/updates/apply-all',{method:'POST',body:fd})
      .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
      .then(function(res){
        if(!res.ok){pushToast(res.d.detail||'Could not start the updates',
                              {title:'Software & services',kind:'error',ttl:0});
          b.disabled=false; return;}
        pushToast(res.d.message||'Updating everything',{title:'Software & services'});
        if((res.d.skipped||[]).length){
          pushToast('Skipped (blocked): '+res.d.skipped.join(', '),
                    {title:'Software & services',kind:'warn',ttl:0});
        }
        // Tell the poller SLOP is in the batch, so losing the connection at the
        // end reads as the expected restart rather than a failure.
        poll((res.d.products||[]).indexOf('slop')>=0);
      })
      .catch(function(e){pushToast(String(e.message||e),
            {title:'Software & services',kind:'error',ttl:0}); b.disabled=false;});
  }
  document.addEventListener('DOMContentLoaded',function(){
    load();
    var ua=document.getElementById('updateall');
    if(ua)ua.addEventListener('click',function(){ startUpdateAll(ua); });
    // Only the Update buttons. This used to be button[data-app] — "any button
    // that knows which product it belongs to" — so any NEW control given that
    // attribute silently became an update trigger.
    document.querySelectorAll('button.btn[data-app][id^=\"b-\"]').forEach(function(b){
      b.addEventListener('click',function(){ startUpdate(b.getAttribute('data-app'), b); });
    });
    // The remote tip is memoised by the updater (browsing Administration would
    // otherwise cost a round-trip to GitHub per product per page). This is the
    // way to say "no, ask now" — so a cached answer is never the only answer.
    var chk=document.getElementById('checknow');
    if(chk) chk.addEventListener('click',function(){
      chk.disabled=true;
      var was=chk.textContent; chk.textContent='Checking\u2026';
      var done=function(){ chk.disabled=false; chk.textContent=was; };
      var p=load(true);
      if(p&&p.then){p.then(done,done);}else{done();}
    });
  });
})();
"""


def _updates_page(sess: sqlite3.Row, tok: str) -> str:
    rows = []
    for key, lbl in (("controller", "Sysible Controller"),
                     ("slep", "Sysible Linux Engineering Platform"),
                     ("connect", "Sysible Connect"),
                     ("slop", "Sysible Linux Operations Platform"
                              " — gateway, sign-in, Flashback, Visualizer")):
        rows.append(
            f"<div class=prod id='p-{key}'>"
            f"<div class=prod-name>{escape(lbl)}</div>"
            f"<div class=prod-state id='u-{key}'><span class=sub>checking&hellip;</span></div>"
            f"<div class=prod-act>"
            f"<button class=btn id='b-{key}' data-app='{key}' hidden disabled>Update now</button>"
            # data-MANAGE, never data-app: the page binds startUpdate() to every
            # button[data-app], so a toggle carrying it would post an update —
            # on the platform row, behind the "you will be signed out" confirm.
            f"<button class='btn ghost sm' id='m-{key}' data-manage='{key}' "
            f"aria-expanded=false hidden>Manage</button>"
            f"</div>"
            f"<div class=prod-meta id='s-{key}'></div>"
            f"<div class=prod-controls id='a-{key}' hidden></div>"
            f"</div>")
    table = f"<div class=prods id=updtable>{''.join(rows)}</div>"

    # Two dense paragraphs used to sit between the operator and the page. They are
    # worth having — they are the difference between "Recreate" being a guess and
    # a decision — but not worth reading every visit, so they fold away.
    note = (
        "<p class=sub>Each product is a git checkout on this host, kept up to date "
        "from here or with <code>sysiblectl &lt;product&gt; update</code>.</p>"
        "<details class=help><summary>What do these controls do?</summary>"
        "<p><b>Update now</b> pulls the checkout and rebuilds its containers &mdash; "
        "the same thing <code>sysiblectl &lt;product&gt; update</code> does. A "
        "checkout with local changes is reported and refused rather than "
        "overwritten.</p>"
        "<p><b>Manage</b> restarts, stops, starts or recreates a product's "
        "containers, so a wedged service does not need a shell on the host. "
        "<b>Recreate</b> applies a compose or environment change using the images "
        "already built, so it is much quicker than a full update.</p>"
        "<p>The Operations Platform itself cannot be STOPPED from here: that would "
        "take down the gateway, this page and the updater together, leaving no way "
        "back except the host. Restart it instead.</p>"
        "</details>")
    if not updates.configured():
        note += ("<p class=sub style='color:#e0a83a'>The updater service is not deployed, so "
                 "updates can only be applied on the host with "
                 "<code>sysiblectl &lt;product&gt; update</code>.</p>")

    body = (
        "<a class=back href='/'>&larr; Portal</a>"
        f"<div class=top><h1>Administration · Software &amp; services</h1>"
        f"<span class=pill>{escape(sess['username'])} · superuser</span></div>"
        f"<p class=sub>{_PILL}<a href='/admin'>Accounts</a> · <a href='/admin/settings'>Configuration</a> · "
        "<a href='/account'>Your account</a> · <a href='/'>Portal &rarr;</a></p>"
        f"{note}"
        f"<input type=hidden id=csrf value='{escape(tok)}'>"
        # The remote tip is memoised by the updater — the header pill polls this
        # page's data endpoint from EVERY Administration page, and each product
        # costs a round-trip to its git remote. This button is how an operator says
        # "ask the remotes now" instead of taking the remembered answer.
        "<div class=toolbar><button class='btn ghost sm' id=checknow>Check now</button>"
        # Hidden until the status poll finds more than one updatable product —
        # with one, the row's own button is the clearer thing to press.
        "<button class='btn sm' id=updateall hidden>Update all</button>"
        "<span class=sub>Availability is remembered for a few minutes; this re-asks "
        "every product&rsquo;s git remote.</span></div>"
        f"{table}"
        "<div id=joblog-wrap>"
        "<div id=jobtitle class=sub style='margin-top:1rem'></div>"
        "<pre class=upd-log id=joblog hidden></pre>"
        "</div>"
        f"<script>{_UPDATES_JS}</script>"
    )
    return _page("Software & services · SLOP", body, wide=True)


@app.get("/admin/updates", response_class=HTMLResponse)
def admin_updates(request: Request):
    sess, err = _require_super(request)
    if err:
        return err
    tok = _csrf_token(request)
    return _csrf_html(_updates_page(sess, tok), tok)


@app.get("/admin/updates/status")
def admin_updates_status(request: Request, refresh: bool = False):
    sess, err = _require_super(request)
    if err:
        return JSONResponse({"error": "Superuser access required."}, status_code=403)
    data, error = updates.status(sess["username"], "superuser", refresh=refresh)
    if error:
        return JSONResponse({"error": error, "apps": []})
    return JSONResponse(data)


@app.get("/admin/updates/job")
def admin_updates_job(request: Request):
    sess, err = _require_super(request)
    if err:
        return JSONResponse({"error": "Superuser access required."}, status_code=403)
    data, error = updates.job(sess["username"], "superuser")
    if error:
        return JSONResponse({"error": error, "job": None})
    return JSONResponse(data)


@app.post("/admin/updates/apply")
def admin_updates_apply(request: Request, csrf: str = Form(""), app: str = Form("")):
    sess, err = _require_super(request)
    if err:
        return JSONResponse({"detail": "Superuser access required."}, status_code=403)
    tok = _csrf_token(request)
    guard = _admin_guard(request, sess, csrf, tok)
    if guard is not None:
        return JSONResponse({"detail": "Request blocked (bad origin or token)."},
                            status_code=403)
    data, error = updates.apply(app, sess["username"], "superuser")
    if error:
        return JSONResponse({"detail": error}, status_code=409)
    return JSONResponse(data)


@app.post("/admin/updates/apply-all")
def admin_updates_apply_all(request: Request, csrf: str = Form("")):
    """Update everything that can be updated, in one sequential job."""
    sess, err = _require_super(request)
    if err:
        return JSONResponse({"detail": "Superuser access required."}, status_code=403)
    tok = _csrf_token(request)
    guard = _admin_guard(request, sess, csrf, tok)
    if guard is not None:
        return JSONResponse({"detail": "Request blocked (bad origin or token)."},
                            status_code=403)
    data, error = updates.apply_all(sess["username"], "superuser")
    if error:
        return JSONResponse({"detail": error}, status_code=409)
    return JSONResponse(data)


@app.post("/admin/updates/action")
def admin_updates_action(request: Request, csrf: str = Form(""), app: str = Form(""),
                         action: str = Form("")):
    """Restart / stop / start / recreate a product from the console, so a wedged
    service does not require a shell on the host. Superuser-gated and carrying
    the same origin + CSRF guard as every other admin mutation."""
    sess, err = _require_super(request)
    if err:
        return JSONResponse({"detail": "Superuser access required."}, status_code=403)
    tok = _csrf_token(request)
    guard = _admin_guard(request, sess, csrf, tok)
    if guard is not None:
        return JSONResponse({"detail": "Request blocked (bad origin or token)."},
                            status_code=403)
    data, error = updates.action(app, action, sess["username"], "superuser")
    if error:
        return JSONResponse({"detail": error}, status_code=409)
    return JSONResponse(data)


@app.get("/admin/settings", response_class=HTMLResponse)
def admin_settings(request: Request):
    sess, err = _require_super(request)
    if err:
        return err
    return HTMLResponse(_config_page(sess))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
