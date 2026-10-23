"""/flashback/ was the product's URL for its whole life. Bookmarks do not migrate.

Baseline is served at /baseline/ now. The old prefix stays as a redirect, and
these run it against a REAL caddy because the two things that can go wrong here
are both invisible in the config file:

  * handle_path STRIPS the matched prefix before the handlers run, so
    {http.request.uri.path} inside the block is already "/api/versions" — build
    the target as "/baseline/" + that and you get "/baseline//api/versions".
  * {http.request.uri.path} drops the QUERY STRING, which is how the console
    passes ?host= and ?path=. A redirect that loses it lands on a page that
    renders, with the wrong data, and nothing anywhere reports an error.

Both were in the first draft of the Caddyfile block these test. Reading it did
not catch either one; running it caught both immediately.
"""
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CADDYFILE = os.path.join(ROOT, "gateway", "Caddyfile")

caddy_bin = shutil.which("caddy") or os.environ.get("CADDY_BIN")
needs_caddy = pytest.mark.skipif(not caddy_bin, reason="caddy binary not available")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def gw(tmp_path_factory):
    """The shipping legacy-prefix blocks, lifted verbatim from the Caddyfile and
    run with a stub where /baseline/ would be, so the assertions are about the
    config we actually ship."""
    if not caddy_bin:
        pytest.skip("caddy binary not available")
    src = open(CADDYFILE, encoding="utf-8").read()
    start = src.index("# /flashback/* — the pre-rebrand path")
    end = src.index("# Visualizer (activity & logs", start)
    body = src[start:end]

    port = _free_port()
    cfg = tmp_path_factory.mktemp("gw") / "Caddyfile"
    cfg.write_text(
        "{\n\tadmin off\n\tauto_https off\n}\n\n"
        f":{port} {{\n{body}\n"
        "\thandle /baseline/* {\n\t\trespond \"LANDED {http.request.uri}\" 200\n\t}\n}\n")
    proc = subprocess.Popen([caddy_bin, "run", "--config", str(cfg),
                             "--adapter", "caddyfile"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    else:                                                    # pragma: no cover
        proc.terminate()
        pytest.fail("caddy did not come up")
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    proc.wait(timeout=10)


def _redirect_to(gw, path):
    class _NoFollow(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None
    op = urllib.request.build_opener(_NoFollow)
    try:
        r = op.open(gw + path, timeout=10)
        return r.status, r.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location")


@pytest.mark.parametrize("old,new", [
    ("/flashback/", "/baseline/"),
    ("/flashback", "/baseline/"),
    ("/flashback/api/hosts", "/baseline/api/hosts"),
    ("/flashback/favicon.svg", "/baseline/favicon.svg"),
])
@needs_caddy
def test_a_pre_rebrand_url_redirects_to_the_same_place_under_the_new_name(gw, old, new):
    code, loc = _redirect_to(gw, old)
    assert code in (301, 302, 307, 308), f"{old} answered {code}, not a redirect"
    assert loc and loc.endswith(new), f"{old} -> {loc}, wanted {new}"


@needs_caddy
def test_the_redirect_keeps_the_query_string(gw):
    """?host= and ?path= are how the console addresses a specific file's history.
    Dropped, the redirect lands on a page that renders perfectly and shows
    something else entirely — the failure mode with no error message."""
    code, loc = _redirect_to(gw, "/flashback/api/versions?host=web-01&path=/etc/hosts")
    assert code in (301, 302, 307, 308)
    assert loc.endswith("/baseline/api/versions?host=web-01&path=/etc/hosts"), loc


@needs_caddy
def test_the_redirect_does_not_double_the_slash(gw):
    """handle_path has already stripped the prefix, so concatenating onto
    "/baseline/" yields "/baseline//api/..." — which routes, and then 404s
    inside the app."""
    _, loc = _redirect_to(gw, "/flashback/api/hosts")
    assert "//api" not in loc, f"doubled slash: {loc}"
    assert "/baseline//" not in loc, f"doubled slash: {loc}"


@needs_caddy
def test_following_the_redirect_reaches_the_app_with_the_whole_request_intact(gw):
    """End to end: the thing a bookmarked URL actually does."""
    with urllib.request.urlopen(
            gw + "/flashback/api/versions?host=web-01&limit=5", timeout=10) as r:
        body = r.read().decode()
    assert body == "LANDED /baseline/api/versions?host=web-01&limit=5", body


def test_the_portal_no_longer_offers_the_old_prefix_itself():
    """The redirect is for URLs already out in the world. Anything we still SHIP
    pointing at /flashback/ is just an extra hop we inflict on every click."""
    html = open(os.path.join(ROOT, "portal", "index.html"), encoding="utf-8").read()
    assert 'data-sub="flashback"' not in html
    assert 'data-sub="baseline"' in html, "the portal must link to the new prefix"


def test_the_legacy_prefix_is_a_redirect_and_not_a_second_proxy():
    """Two proxy blocks for one app means two sets of prefix-stripping, SSO
    stamping and cookie scrubbing that have to stay in lockstep forever — and
    the one nobody exercises is the one that rots into a hole."""
    src = open(CADDYFILE, encoding="utf-8").read()
    for m in re.finditer(r"handle(?:_path)? /flashback[^\{]*\{(.*?)\n\t\}", src, re.S):
        body = m.group(1)
        assert "redir" in body, "a /flashback block that does not redirect"
        assert "reverse_proxy" not in body, \
            "/flashback is proxied as well as redirected — one app, two routes"
