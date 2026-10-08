"""The updater had no cap on the request body while Flashback and the Visualizer
both did.

This service holds the host's Docker socket, so it is the one most worth making
expensive to poke at. Without a cap, an UNAUTHENTICATED POST makes the process
buffer whatever is sent before the trust gate — and therefore before the shared
secret is checked — has run at all.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SECRET = "updater-shared-secret"


@pytest.fixture
def app_mod(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSIBLE_SSO_SHARED_SECRET", SECRET)
    monkeypatch.setenv("SYSIBLE_SRC_DIR", str(tmp_path / "src"))
    import importlib
    from backend import apps as apps_mod
    importlib.reload(apps_mod)
    from backend import app as m
    return importlib.reload(m)


def test_a_declared_oversized_body_is_refused(app_mod):
    """Content-Length over the cap: refused without reading the body at all."""
    c = TestClient(app_mod.app)
    big = b"x" * (app_mod._MAX_REQUEST_BYTES + 1)
    r = c.post("/api/update/controller", content=big,
               headers={"Content-Type": "application/json"})
    assert r.status_code == 413, r.status_code


def test_it_is_refused_before_the_shared_secret_is_checked(app_mod):
    """413 rather than 401 is the whole point: an unauthenticated caller must not
    be able to make this process buffer a large body first and be rejected after.
    """
    c = TestClient(app_mod.app)
    big = b"x" * (app_mod._MAX_REQUEST_BYTES + 1)
    r = c.post("/api/update/controller", content=big,
               headers={"Content-Type": "application/json"})
    assert r.status_code == 413, (
        "an oversized unauthenticated body reached the trust gate")
    # ...and the same call, small, still gets as far as being told to authenticate.
    assert c.post("/api/update/controller", json={}).status_code == 401


def test_an_undeclared_oversized_body_is_refused_too(app_mod):
    """A chunked body never declares its length, so Content-Length alone is not
    a cap — the running total has to stop it."""
    cap = app_mod._MAX_REQUEST_BYTES

    def chunks():
        sent = 0
        while sent <= cap + 4096:
            yield b"x" * 8192
            sent += 8192

    c = TestClient(app_mod.app)
    r = c.post("/api/update/controller", content=chunks(),
               headers={"Content-Type": "application/json"})
    assert r.status_code == 413, r.status_code


def test_a_normal_request_is_untouched(app_mod):
    """The cap must not change the shape of anything real. Everything this
    service accepts is a short form or a small JSON object."""
    c = TestClient(app_mod.app)
    hdrs = {"X-Sysible-Auth": SECRET, "X-Sysible-User": "alice",
            "X-Sysible-Role": "superuser"}
    assert c.get("/api/status", headers=hdrs).status_code == 200
    r = c.post("/api/update/controller", json={"apps": ["controller"]}, headers=hdrs)
    assert r.status_code != 413, "a normal body was caught by the cap"


def test_the_cap_is_small(app_mod):
    """Sized for the payloads, not for comfort: a megabyte here would be a
    megabyte an unauthenticated caller can make the host hold."""
    assert app_mod._MAX_REQUEST_BYTES <= 256 * 1024, app_mod._MAX_REQUEST_BYTES
