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
