"""Hardening from the security review. One test per finding.

Each of these is cheap to regress by accident, and two of them are only defence
in depth today — which is exactly the kind of guard that gets removed because
"nothing breaks".
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


@pytest.fixture()
def mod(tmp_path):
    """The module, reloaded against a fresh data dir. NOTE: _bootstrap_admin runs
    in the startup event, so it has NOT run yet here — use `booted` for anything
    that needs the first-run account."""
    os.environ.update(
        SLOP_DATA_DIR=str(tmp_path), SLOP_DB_PATH=str(tmp_path / "idp.db"),
        SLOP_ADMIN_USER="admin", SLOP_ADMIN_FORCE_CHANGE="0",
        SLOP_ALLOW_INSECURE_COOKIE="1",
    )
    os.environ.pop("SLOP_ADMIN_PASSWORD", None)
    import app as m
    return importlib.reload(m)


# ---- open redirect: control characters --------------------------------------
@pytest.mark.parametrize("raw", [
    "/\t//evil.example.com",      # TAB  — browsers strip it, leaving ///evil
    "/\r//evil.example.com",
    "/\n//evil.example.com",
    "/\x0b//evil.example.com",
    "/\x0c//evil.example.com",
    "/\x00//evil.example.com",
])
def test_a_control_character_cannot_smuggle_an_off_site_redirect(mod, raw):
    """A browser removes TAB/CR/LF from a URL before parsing it, so
    "/<TAB>//evil" becomes "///evil" and leaves the origin — confirmed in
    Chromium. Starlette percent-encodes these on the way out today, which is a
    dependency's behaviour and not a guarantee; the backslash above is refused
    here for exactly that reason rather than left to it."""
    assert mod._safe_next(raw) == "/"


@pytest.mark.parametrize("raw,want", [
    ("/controller/", "/controller/"),
    ("/admin", "/admin"),
    ("/ok?a=b#c", "/ok?a=b#c"),
])
def test_legitimate_targets_still_work(mod, raw, want):
    assert mod._safe_next(raw) == want


@pytest.fixture()
def booted(mod, capsys):
    """Startup actually run, so the first-run account exists. Returns
    (module, whatever the service printed)."""
    from starlette.testclient import TestClient
    with TestClient(mod.app, base_url="http://slop.lan"):
        pass
    return mod, capsys.readouterr().out


# ---- the bootstrap password must not live in the container log ---------------
def test_the_generated_password_is_not_printed(booted, tmp_path):
    """"Shown once" was never once: a container log is replayed by
    `docker compose logs` to anyone who can read it, for as long as the service
    lives."""
    _mod, out = booted
    path = tmp_path / "initial-password"
    assert path.is_file(), "the password file was not written"
    pw = path.read_text().strip()
    assert pw, "the password file is empty"
    assert pw not in out, "the generated password was printed to the log"
    assert "initial-password" in out, "the log does not say where to find it"


def test_the_password_file_is_not_world_readable(booted, tmp_path):
    path = tmp_path / "initial-password"
    assert path.is_file()
    assert oct(path.stat().st_mode)[-3:] == "600", oct(path.stat().st_mode)


def test_signing_in_removes_the_password_file(booted, tmp_path):
    """It has done its job; leaving it is leaving a credential on disk."""
    from starlette.testclient import TestClient
    mod, _out = booted
    path = tmp_path / "initial-password"
    pw = path.read_text().strip()
    with TestClient(mod.app, base_url="http://slop.lan") as c:
        r = c.get("/login")
        tok = re.search(r"name=csrf value='([^']+)'", r.text).group(1)
        c.post("/login", data={"username": "admin", "password": pw, "csrf": tok},
               headers={"Origin": "http://slop.lan", "Referer": "http://slop.lan/login"})
    assert not path.exists(), "the bootstrap password file survived a sign-in"


# ---- request body cap --------------------------------------------------------
def test_an_oversized_body_is_refused_before_a_handler_runs(mod):
    """Unauthenticated: without the cap this buffers whatever is sent before any
    authentication runs."""
    from starlette.testclient import TestClient
    with TestClient(mod.app, base_url="http://slop.lan") as c:
        big = "x" * (mod._MAX_REQUEST_BYTES + 1024)
        r = c.post("/login", data={"username": big, "password": "x", "csrf": "x"},
                   headers={"Origin": "http://slop.lan"})
        assert r.status_code == 413, r.status_code


def test_a_normal_sign_in_is_not_affected(booted, tmp_path):
    from starlette.testclient import TestClient
    mod, _out = booted
    pw = (tmp_path / "initial-password").read_text().strip()
    with TestClient(mod.app, base_url="http://slop.lan") as c:
        r = c.get("/login")
        tok = re.search(r"name=csrf value='([^']+)'", r.text).group(1)
        r = c.post("/login", data={"username": "admin", "password": pw, "csrf": tok},
                   headers={"Origin": "http://slop.lan", "Referer": "http://slop.lan/login"},
                   follow_redirects=False)
        assert r.status_code == 302, r.status_code
