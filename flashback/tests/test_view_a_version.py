"""You can read a stored version, not only download, restore or diff it.

Looking at the file is the obvious thing to want, and it was the one thing the
version pane did not offer.
"""
import os
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

UI = open(os.path.join(HERE, "backend", "ui.py"), encoding="utf-8").read()


@pytest.fixture()
def client(tmp_path):
    """Same shape as tests/test_console_actions.py's fixture.

    My first version set FLASHBACK_DATA_DIR, which this store does not read — it
    reads SYSIBLE_FLASHBACK_DATA (backend/store.py). It passed on its own and
    failed the moment another test file ran first, which is exactly what a wrong
    fixture looks like.
    """
    import importlib
    from starlette.testclient import TestClient

    os.environ["SYSIBLE_FLASHBACK_DATA"] = str(tmp_path)
    os.environ.pop("SYSIBLE_FLASHBACK_DB", None)
    # Pin the identity mode instead of inheriting it. test_flashback_auth.py leaves
    # gateway trust ON, and these modules are reloaded from the live environment —
    # so run after it, this client had no credentials and every call was a 401.
    os.environ["SYSIBLE_FLASHBACK_TRUST_GATEWAY_AUTH"] = "0"
    os.environ.pop("SYSIBLE_SSO_SHARED_SECRET", None)
    os.environ["SYSIBLE_FLASHBACK_LOCAL_ROLE"] = "superuser"
    for mod in ("backend.identity", "backend.store", "backend.app"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    from backend import store as st
    st.init_db()
    st.ingest_snapshot("web-1", "web-1", [
        {"path": "/etc/app.conf", "content": "key = value\nother = 2\n"},
        {"path": "/etc/blob.der", "content": bytes([0, 1, 2, 3] * 64)},
        {"path": "/etc/big.conf", "content": ("x" * 80 + "\n") * 9000},
    ])
    import backend.app as app_mod
    return TestClient(app_mod.app), st


def _sha(st, path):
    return st.list_versions("web-1", path)[0]["sha256"]


def test_a_text_version_comes_back_readable(client):
    c, st = client
    r = c.get("/api/hosts/web-1/view",
              params={"path": "/etc/app.conf", "sha": _sha(st, "/etc/app.conf")})
    assert r.status_code == 200
    d = r.json()
    assert d["binary"] is False and d["truncated"] is False
    assert d["text"] == "key = value\nother = 2\n"
    assert d["lines"] == 2 and d["size"] == 22


def test_a_binary_version_says_so_instead_of_returning_mojibake(client):
    c, st = client
    d = c.get("/api/hosts/web-1/view",
              params={"path": "/etc/blob.der", "sha": _sha(st, "/etc/blob.der")}).json()
    assert d["binary"] is True and d["text"] == ""


def test_an_enormous_file_is_truncated_and_says_so(client):
    """A browser asked to lay out megabytes of <pre> stops responding. The
    download is still there for the whole thing."""
    c, st = client
    d = c.get("/api/hosts/web-1/view",
              params={"path": "/etc/big.conf", "sha": _sha(st, "/etc/big.conf")}).json()
    assert d["truncated"] is True
    assert len(d["text"]) <= 512 * 1024
    assert d["size"] > 512 * 1024, "size must be the REAL size, not the truncated one"


def test_an_unknown_version_is_404_not_empty(client):
    c, _ = client
    assert c.get("/api/hosts/web-1/view",
                 params={"path": "/etc/app.conf", "sha": "0" * 64}).status_code == 404


def test_viewing_needs_an_identity(client, monkeypatch):
    c, st = client
    import backend.app as app_mod
    assert "_require_identity" in open(
        os.path.join(HERE, "backend", "app.py"), encoding="utf-8").read().split(
        'def api_view(')[1].split("def ")[0]


# ---- the console end -------------------------------------------------------
def test_the_pane_offers_it():
    bar = UI[UI.index("async function renderDetail"):UI.index("async function showVersion")]
    assert "'View this version'" in bar, "the version pane cannot open the file"
    assert "showVersion(v)" in bar


def test_the_content_is_never_treated_as_markup():
    """A stored config is an arbitrary file off a managed host. Putting it in the
    DOM as HTML would run whatever is in it on this origin."""
    fn = UI[UI.index("async function showVersion"):UI.index("function fmtSize")]
    assert "pre.textContent = r.text" in fn
    assert "innerHTML = r.text" not in fn and "innerHTML=r.text" not in fn


def test_a_binary_and_a_truncated_file_are_called_out_on_screen():
    fn = UI[UI.index("async function showVersion"):UI.index("function fmtSize")]
    assert "r.binary" in fn and "nothing to read" in fn
    assert "r.truncated" in fn and "download for the rest" in fn
