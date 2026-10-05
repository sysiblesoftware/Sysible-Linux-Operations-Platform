"""No page may render an HTML entity as literal text.

Reported from the browser's tab: "Software &amp; services · SL…". The title
literal carried the entity AND _page() escapes the title, so "&amp;" became
"&amp;amp;" and the browser drew the markup instead of the ampersand.

This cannot be caught by reading either line: the literal is at the call site and
the escape is inside _page(). It is obvious in the output, so that is what this
checks — every page, not just the one that was reported.
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

# An entity that has been escaped a second time: "&amp;" -> "&amp;amp;".
DOUBLE = re.compile(r"&amp;(amp|lt|gt|quot|apos|nbsp|rsquo|lsquo|ldquo|rdquo|#\d+|#x[0-9a-fA-F]+);")
TITLE = re.compile(r"<title>(.*?)</title>", re.S)

PAGES = ["/account", "/admin", "/admin/settings", "/admin/apps", "/admin/updates"]


@pytest.fixture()
def client(tmp_path):
    from starlette.testclient import TestClient
    os.environ.update(
        SLOP_DATA_DIR=str(tmp_path), SLOP_DB_PATH=str(tmp_path / "idp.db"),
        SLOP_ADMIN_USER="admin", SLOP_ADMIN_PASSWORD=ADMIN_PW,
        SLOP_ADMIN_FORCE_CHANGE="0", SLOP_ALLOW_INSECURE_COOKIE="1",
    )
    import app as m
    m = importlib.reload(m)
    with TestClient(m.app, base_url="http://slop.lan") as c:
        r = c.get("/login")
        tok = re.search(r"name=csrf value='([^']+)'", r.text).group(1)
        c.post("/login", data={"username": "admin", "password": ADMIN_PW, "csrf": tok},
               headers={"Origin": "http://slop.lan", "Referer": "http://slop.lan/login"})
        yield c


@pytest.mark.parametrize("path", PAGES)
def test_no_page_renders_an_entity_as_text(client, path):
    html = client.get(path).text
    found = sorted(set(DOUBLE.findall(html)))
    assert not found, f"{path} renders {['&amp;' + f + ';' for f in found]} as visible text"


@pytest.mark.parametrize("path", PAGES)
def test_no_tab_title_renders_an_entity_as_text(client, path):
    """The title is the one place a double escape is read as a word, because the
    browser chrome shows it with no markup around it."""
    m = TITLE.search(client.get(path).text)
    assert m, f"{path} has no <title>"
    assert not DOUBLE.search(m.group(1)), f"{path} tab title is {m.group(1)!r}"


def test_the_title_helper_escapes_so_callers_must_not(client):
    """Why the rule exists: _page() escapes what it is given, so a caller passing
    markup gets it drawn. Asserted on the helper, so the next caller inherits it."""
    import app as m
    html = m._page("A & B <tag>", "<p>x</p>")
    assert "<title>A &amp; B &lt;tag&gt;</title>" in html
