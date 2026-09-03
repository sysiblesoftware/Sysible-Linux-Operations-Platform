"""Tests for the SLOP gateway config — specifically, that it still DENIES.

The gateway is the only thing standing between an anonymous browser and every
Sysible app, and it enforces that in Caddyfile syntax rather than code. That makes
one class of mistake uniquely dangerous: a directive that is silently reinterpreted
still adapts, still starts, logs no warning, and simply stops blocking anything.

That is not hypothetical — it shipped. Inside a `handle_response` block, the first
argument of `redir` (and of most directives) is an OPTIONAL MATCHER, so

    handle_response @bad {
        redir /login?next={uri} 302        # <- WRONG
    }

parses as matcher=`/login?next={uri}`, destination=`"302"`. The route then matches
no request, the 401 branch does nothing, and the request FALLS THROUGH the
forward_auth as though the user were signed in: the portal was served to anyone,
and every app was proxied with the gateway's shared secret attached but no
identity — so each app either showed its own login again or refused with
"Not signed in.". The fix is the explicit `*` matcher.

These tests pin the shape of the deny path so it cannot regress into a no-op.
The lint runs anywhere; the adapt-level assertions run only when a `caddy` binary
is available (CI installs one), and are skipped otherwise rather than passing
vacuously.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CADDYFILE = os.path.join(os.path.dirname(HERE), "Caddyfile")


@pytest.fixture(scope="module")
def text():
    with open(CADDYFILE, "r", encoding="utf-8") as fh:
        return fh.read()


def _strip_comments(line: str) -> str:
    return re.sub(r"(^|\s)#.*$", "", line).strip()


# ---- source-level lint (always runs) ---------------------------------------
def test_every_directive_inside_handle_response_carries_an_explicit_matcher(text):
    """A leading `/path` argument is read as a MATCHER, not as the destination.

    Requiring an explicit `*` (or a named `@matcher`) makes the intent
    unambiguous and makes this failure mode impossible to reintroduce by
    accident.
    """
    depth = None
    offenders = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = _strip_comments(raw)
        if not line:
            continue
        if depth is not None:
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                depth = None
                continue
            parts = line.split()
            if parts[0] in ("redir", "respond", "rewrite", "reverse_proxy", "file_server", "root"):
                if parts[1:2] and not (parts[1] == "*" or parts[1].startswith("@")):
                    offenders.append(f"line {n}: {line}")
        elif line.startswith("handle_response"):
            depth = line.count("{") - line.count("}")
    assert not offenders, (
        "directive(s) inside handle_response with no explicit matcher — Caddy will "
        "read the first argument as a path matcher and silently do nothing:\n  "
        + "\n  ".join(offenders)
    )


def test_the_deny_path_exists_for_every_authenticated_area(text):
    # Three forward_auth gates: the app snippet, the plain-HTTP app snippet, and
    # the portal catch-all. Each must bounce a 401/403 to the sign-in page.
    directives = [_strip_comments(l) for l in text.splitlines()]
    assert sum(1 for l in directives if l.startswith("forward_auth ")) == 3
    assert sum(1 for l in directives if l == "redir * /login?next={uri} 302") == 3
    assert "@sso_bad status 401 403" in text
    assert "@portal_bad status 401 403" in text


def test_the_portal_is_not_public(text):
    """The static portal must sit BEHIND forward_auth. Serving it anonymously is
    what made a browser that had never signed in look 'already logged in'."""
    i = text.index("root * /srv/portal")
    block = text[:i]
    j = block.rindex("handle {")
    assert "forward_auth" in text[j:i], "the portal's handle block has no forward_auth"


def test_client_supplied_identity_headers_are_stripped_before_auth(text):
    # A browser must never be able to assert its own identity to an app: each
    # gated route drops inbound X-Sysible-* BEFORE forward_auth re-adds them.
    for h in ("X-Sysible-User", "X-Sysible-Role", "X-Sysible-Auth"):
        assert text.count(f"request_header -{h}") == 3


# ---- adapt-level assertions (need the caddy binary) ------------------------
caddy_bin = shutil.which("caddy") or os.environ.get("CADDY_BIN")
needs_caddy = pytest.mark.skipif(not caddy_bin, reason="caddy binary not available")


@pytest.fixture(scope="module")
def adapted():
    env = {**os.environ, "SLOP_IDP_UPSTREAM": "idp:8080",
           "SYSIBLE_SSO_SHARED_SECRET": "test-secret"}
    out = subprocess.run([caddy_bin, "adapt", "--config", CADDYFILE,
                          "--adapter", "caddyfile"],
                         capture_output=True, env=env, check=True)
    return json.loads(out.stdout)


def _forward_auth_proxies(obj, found=None):
    found = [] if found is None else found
    if isinstance(obj, dict):
        if obj.get("handler") == "reverse_proxy" and \
                obj.get("rewrite", {}).get("uri") == "/auth/verify":
            found.append(obj)
        for v in obj.values():
            _forward_auth_proxies(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _forward_auth_proxies(v, found)
    return found


@needs_caddy
def test_config_adapts(adapted):
    assert "apps" in adapted


@needs_caddy
def test_every_401_branch_actually_redirects(adapted):
    """The bug that shipped adapted cleanly to `Location: "302"` behind a path
    matcher that never fires. Assert the real shape: no matcher, and a Location
    pointing at the sign-in page."""
    proxies = _forward_auth_proxies(adapted)
    # 3 gates, but the two app snippets are imported once per fronted app.
    assert len(proxies) >= 6, f"expected every gated route to forward_auth, got {len(proxies)}"
    for p in proxies:
        branches = [h for h in p.get("handle_response", [])
                    if 401 in (h.get("match", {}).get("status_code") or [])]
        assert branches, "a forward_auth has no 401 branch at all"
        for b in branches:
            for route in b["routes"]:
                assert not route.get("match"), (
                    "the 401 branch is behind a matcher, so it will not fire: "
                    f"{route.get('match')}")
                for h in route["handle"]:
                    assert h.get("handler") == "static_response"
                    assert h.get("status_code") == 302
                    loc = h.get("headers", {}).get("Location", [])
                    assert loc and loc[0].startswith("/login?next="), \
                        f"401 branch does not redirect to sign-in: {loc}"


@needs_caddy
def test_the_shared_secret_is_only_stamped_on_gated_routes(adapted):
    """X-Sysible-Auth is the apps' whole trust boundary. It must never be stamped
    on a route that has not just passed forward_auth."""
    stamped = []

    def walk(o, seen_auth):
        if isinstance(o, dict):
            if o.get("handler") == "headers":
                st = (o.get("request") or {}).get("set") or {}
                if "X-Sysible-Auth" in st:
                    stamped.append(True)
            for v in o.values():
                walk(v, seen_auth)
        elif isinstance(o, list):
            for v in o:
                walk(v, seen_auth)

    walk(adapted, False)
    # One per fronted app (3 https + 2 plain-http); the portal never stamps it.
    assert len(stamped) == 5, f"unexpected number of X-Sysible-Auth stamps: {len(stamped)}"


# ---- framing: exactly 'self', not looser and not tighter --------------------
# Two failure modes, opposite directions, both real:
#   * back to 'none'  -> SLOP Administration can no longer host each app's own
#     settings UI, and the consolidation silently becomes a blank panel;
#   * anything wider  -> a foreign site can frame the destructive admin forms,
#     which is the clickjacking this header exists to stop.
# Verified in a real Chromium: same-origin embeds render, and a page on another
# origin is refused with 'because an ancestor violates ... frame-ancestors self'.
def test_framing_is_same_origin_exactly(text):
    directives = [_strip_comments(l) for l in text.splitlines()]
    csp = [l for l in directives if l.startswith("Content-Security-Policy")]
    assert csp, "the site-wide CSP header is gone"
    assert any("frame-ancestors 'self'" in l for l in csp), csp
    assert not any("frame-ancestors 'none'" in l for l in csp), \
        "'none' breaks Administration hosting each app's settings UI"
    assert not any("frame-ancestors *" in l or "frame-ancestors 'unsafe" in l for l in csp), \
        "a wildcard here reopens clickjacking of the admin forms"
    xfo = [l for l in directives if l.startswith("X-Frame-Options")]
    assert xfo and all("SAMEORIGIN" in l for l in xfo), xfo
    assert not any("DENY" in l for l in xfo)


@needs_caddy
def test_the_adapted_config_serves_same_origin_framing(adapted):
    found = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("handler") == "headers":
                st = ((o.get("response") or {}).get("set") or {})
                for k, v in st.items():
                    if k.lower() in ("content-security-policy", "x-frame-options"):
                        found.append((k.lower(), " ".join(v)))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(adapted)
    csp = [v for k, v in found if k == "content-security-policy"]
    xfo = [v for k, v in found if k == "x-frame-options"]
    assert csp and all("frame-ancestors 'self'" in c for c in csp), csp
    assert xfo and all(x == "SAMEORIGIN" for x in xfo), xfo


# ---- websockets must survive forward_auth ---------------------------------
# The second silent failure this file has shipped. forward_auth copies the
# CLIENT's headers onto its auth subrequest, so a websocket upgrade sent
# `Connection: Upgrade` and `Upgrade: websocket` to the IdP's plain /auth/verify
# route. uvicorn answers 403 to an upgrade on a non-websocket route, @sso_bad
# matches 401/403, and the browser's handshake received a 302 to /login instead
# of a 101. Every terminal in Sysible Connect failed AT THE GATEWAY and never
# reached the app, and nothing named it: Connect never saw the request, and a
# failed handshake gives the browser close code 1006 with no reason — a blank
# terminal with a blinking cursor.
def test_every_forward_auth_strips_the_upgrade_headers(text):
    """The auth subrequest must be a plain GET. Only the PROXIED request keeps
    its upgrade headers — that is what makes the websocket work."""
    blocks, depth, cur = [], None, []
    for raw in text.splitlines():
        line = _strip_comments(raw)
        if depth is not None:
            cur.append(line)
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                blocks.append(cur)
                depth, cur = None, []
        elif line.startswith("forward_auth "):
            cur = [line]
            depth = line.count("{") - line.count("}")
            if depth <= 0:
                blocks.append(cur)
                depth, cur = None, []
    assert len(blocks) == 3, f"expected 3 forward_auth blocks, found {len(blocks)}"
    for b in blocks:
        body = "\n".join(b)
        assert "header_up -Connection" in body, (
            "a forward_auth that forwards Connection: Upgrade to the IdP turns every "
            f"websocket into a 302 to /login:\n{body}")
        assert "header_up -Upgrade" in body, body


@needs_caddy
def test_a_websocket_really_upgrades_through_the_shipping_appsite_snippet(tmp_path):
    """End to end against a real caddy: run the SHIPPING (appsite) snippet in
    front of a stub IdP and a stub websocket app, and assert the handshake
    reaches 101 — and that the auth subrequest arrived WITHOUT the upgrade
    headers. Lint alone cannot catch this; the broken config adapted cleanly."""
    import base64
    import hashlib
    import http.server
    import re
    import socket
    import threading
    import time

    seen_auth_headers = []

    class IdP(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                     # noqa: N802
            seen_auth_headers.append({k.lower(): v for k, v in self.headers.items()})
            self.send_response(200)
            self.send_header("X-Sysible-User", "alice")
            self.send_header("X-Sysible-Role", "operator")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):                            # quiet
            pass

    idp = http.server.ThreadingHTTPServer(("127.0.0.1", 0), IdP)
    threading.Thread(target=idp.serve_forever, daemon=True).start()

    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    app_sock = socket.socket()
    app_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    app_sock.bind(("127.0.0.1", 0))
    app_sock.listen(4)

    def app_server():
        while True:
            try:
                c, _ = app_sock.accept()
            except OSError:
                return
            req = c.recv(65536).decode("utf-8", "replace")
            m = re.search(r"Sec-WebSocket-Key:\s*(\S+)", req, re.I)
            if m:
                acc = base64.b64encode(
                    hashlib.sha1((m.group(1) + GUID).encode()).digest()).decode()
                c.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Accept: {acc}\r\n\r\n"
                           ).encode())
            else:
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            time.sleep(0.2)
            c.close()

    threading.Thread(target=app_server, daemon=True).start()

    # Build a runnable config around the SHIPPING snippet (plain-HTTP stubs, so
    # the https-upstream transport block is dropped; the forward_auth under test
    # is used verbatim).
    src = open(CADDYFILE, encoding="utf-8").read()
    snippet = src[src.index("(appsite)"):src.index("# ---- same as (appsite)")]
    snippet = snippet.replace("{$SLOP_IDP_UPSTREAM:idp:8080}",
                              f"127.0.0.1:{idp.server_address[1]}")
    snippet = snippet.replace("{$SYSIBLE_SSO_SHARED_SECRET}", "test-secret")
    snippet = re.sub(r"transport http \{.*?\n\t*\}\n", "", snippet, flags=re.S)

    gw = socket.socket()
    gw.bind(("127.0.0.1", 0))
    port = gw.getsockname()[1]
    gw.close()
    cfg = tmp_path / "Caddyfile"
    cfg.write_text("{\n\tadmin off\n\tauto_https off\n}\n\n" + snippet +
                   f"\n:{port} {{\n\timport appsite /connect "
                   f"127.0.0.1:{app_sock.getsockname()[1]}\n}}\n")

    proc = subprocess.Popen([caddy_bin, "run", "--config", str(cfg),
                             "--adapter", "caddyfile"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):                       # wait for the listener
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

        s = socket.create_connection(("127.0.0.1", port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET /connect/api/terminal/ws HTTP/1.1\r\n"
                   f"Host: 127.0.0.1:{port}\r\n"
                   "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
                   ).encode())
        status = s.recv(4096).decode("utf-8", "replace").split("\r\n")[0]
        s.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        idp.shutdown()
        app_sock.close()

    assert "101" in status, (
        f"the websocket handshake did not upgrade: {status!r}. A 302 here is the "
        "gateway bouncing the upgrade to /login, which is what a blank terminal in "
        "Sysible Connect actually was.")
    assert seen_auth_headers, "forward_auth never reached the IdP"
    auth = seen_auth_headers[-1]
    assert "upgrade" not in auth, f"the auth subrequest still carries Upgrade: {auth}"
    assert "upgrade" not in (auth.get("connection", "").lower()), \
        f"the auth subrequest still carries Connection: Upgrade: {auth}"
