"""/flashback and /baseline are two different products. Nothing may bridge them.

For two days Flashback was renamed Sysible Baseline, and the gateway carried a
redirect sending /flashback/* to /baseline/*. The rename is undone and the name
Baseline now belongs to the Salt-driven CONFIG MANAGER — a different service,
with a different console, doing a different job.

That makes the obvious tidy-up — "there's a dangling /flashback, point it at
/baseline like it used to" — the most damaging change available here. It would
send every bookmark for the BACKUP tool to the CONFIG MANAGER: a working link,
a 200, a real page, and the wrong application. A 404 announces itself; this
would not.

So the absence of that redirect is a tested property, not an oversight, and
these tests exist to say so to whoever finds the gap next.
"""
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CADDYFILE = os.path.join(ROOT, "gateway", "Caddyfile")
INDEX = os.path.join(ROOT, "portal", "index.html")


@pytest.fixture(scope="module")
def caddy():
    return open(CADDYFILE, encoding="utf-8").read()


@pytest.fixture(scope="module")
def code(caddy):
    """Caddyfile with comments stripped — the reasoning below names the very
    routes these assertions search for, so a raw substring test matches the
    explanation and passes whatever the config does."""
    return "\n".join(l for l in caddy.splitlines() if not l.lstrip().startswith("#"))


def test_flashback_is_served_in_its_own_right(code):
    assert re.search(r"import appsite_http /flashback\b", code), \
        "Flashback has no route of its own"


def test_nothing_redirects_flashback_to_baseline(code):
    """The dangerous change. Backup bookmarks must not land on the config
    manager."""
    for m in re.finditer(r"handle(?:_path)? /flashback[^\{]*\{(.*?)\n\t\}", code, re.S):
        assert "/baseline" not in m.group(1), (
            "a /flashback route points at /baseline — that sends every bookmark "
            "for the backup tool to the config manager, which answers 200 with "
            "the wrong application")
    assert not re.search(r"redir[^\n]*/flashback[^\n]*/baseline", code)
    assert not re.search(r"redir[^\n]*/baseline[^\n]*/flashback", code)


def test_the_two_apps_do_not_share_an_upstream(code):
    """Same failure by another route: one service answering both prefixes."""
    fb = re.search(r"appsite_http /flashback http://\{\$([A-Z_]+):([^}]+)\}", code)
    assert fb, "Flashback's upstream is not readable"
    bl = re.search(r"appsite_http /baseline http://\{\$([A-Z_]+):([^}]+)\}", code)
    if not bl:
        pytest.skip("the Baseline config manager is not wired into the gateway yet")
    assert fb.group(1) != bl.group(1), "both apps read the same upstream variable"
    assert fb.group(2) != bl.group(2), f"both apps proxy to {fb.group(2)}"


def test_the_health_routes_are_separate_too(code):
    routes = set(re.findall(r"handle /healthz/([A-Za-z0-9_-]+)\s*\{", code))
    assert "flashback" in routes, "Flashback has no health route"
    if "baseline" in routes:
        fb = re.search(r"handle /healthz/flashback\s*\{(.*?)\n\t\}", code, re.S).group(1)
        bl = re.search(r"handle /healthz/baseline\s*\{(.*?)\n\t\}", code, re.S).group(1)
        assert fb != bl, "the two health routes proxy to the same place"


def test_the_portal_links_to_flashback_not_baseline_for_backup():
    html = open(INDEX, encoding="utf-8").read()
    assert 'data-sub="flashback"' in html, "the portal does not link to Flashback"
    i = html.index('data-sub="flashback"')
    card = html[i:i + 700]
    assert "Sysible Flashback" in card, "the Flashback tile is not named Flashback"
    assert "Baseline" not in card, "the Flashback tile mentions the config manager"


def test_the_reason_the_redirect_is_gone_is_written_down(caddy):
    """A deliberate absence that is not explained gets 'fixed' by the next
    person who notices it."""
    # Anchored on the ROUTE, not on the first "redirect" in the file — the
    # gateway's SSO handling says that word long before this does.
    i = caddy.index("import appsite_http /flashback")
    near = caddy[max(0, i - 400):i + 1400]
    assert "REDIRECT" in near.upper(), \
        "nothing near the Flashback route mentions the redirect that used to be here"
    assert "different product" in near or "config manager" in near.lower(), \
        "the note does not say WHY the redirect is gone, so the next person " \
        "will helpfully restore it"
