"""Tests for the updater sidecar — the component that holds the Docker socket.

The socket is root-equivalent on the host, so the thing that matters most here is
not that updates work, it is that NOTHING a caller sends can widen what runs. The
API takes one key, checked against a fixed allowlist; every path and argv is
derived from host configuration. These tests attack that boundary directly:
traversal, absolute paths, unknown keys, missing and forged secrets, a non-
superuser role, and concurrent updates.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SECRET = "updater-shared-secret"


def _hdrs(user="alice", role="superuser", secret=SECRET):
    return {"X-Sysible-Auth": secret, "X-Sysible-User": user, "X-Sysible-Role": role}


def _make_repo(path: Path, remote: Path | None = None) -> Path:
    """A real git checkout, so the git helpers are exercised for real rather than
    against a mock that can agree with a wrong implementation."""
    path.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    def g(*a, cwd=path):
        return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True,
                              text=True, env=env, check=False)
    g("init", "-q", "-b", "main")
    (path / "docker-compose.yml").write_text("services: {}\n")
    g("add", "-A"); g("commit", "-qm", "init")
    if remote is not None:
        g("remote", "add", "origin", str(remote))
        g("fetch", "-q", "origin")
        g("branch", "--set-upstream-to=origin/main", "main")
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An updater wired to a throwaway source tree with one product installed."""
    monkeypatch.setenv("SYSIBLE_SSO_SHARED_SECRET", SECRET)
    monkeypatch.setenv("SYSIBLE_SRC_DIR", str(tmp_path / "src"))
    import importlib
    from backend import apps as apps_mod
    importlib.reload(apps_mod)
    from backend import git as git_mod, jobs as jobs_mod
    importlib.reload(git_mod)
    importlib.reload(jobs_mod)
    from backend import app as app_mod
    app_mod = importlib.reload(app_mod)

    upstream = _make_repo(tmp_path / "upstream")
    _make_repo(tmp_path / "src" / "sysible-controller", remote=upstream)
    return app_mod, apps_mod, tmp_path


@pytest.fixture
def client(env):
    return TestClient(env[0].app)


# ---- the trust gate --------------------------------------------------------
def test_every_endpoint_needs_the_shared_secret(client):
    for call in (lambda: client.get("/api/status"),
                 lambda: client.get("/api/job"),
                 lambda: client.post("/api/update/controller")):
        assert call().status_code == 401


def test_a_wrong_secret_is_not_authorization(client):
    assert client.get("/api/status", headers=_hdrs(secret="nope")).status_code == 401


def test_updating_requires_a_superuser(client):
    for role in ("operator", "auditor", "", "root"):
        r = client.post("/api/update/controller", headers=_hdrs(role=role))
        assert r.status_code == 403, role


def test_it_fails_closed_with_no_secret_configured(tmp_path, monkeypatch):
    """An updater that answered unauthenticated calls would be a remote root
    shell on the host, so no secret must mean no service — not open."""
    monkeypatch.setenv("SYSIBLE_SSO_SHARED_SECRET", "")
    monkeypatch.setenv("SYSIBLE_SRC_DIR", str(tmp_path / "src"))
    import importlib
    from backend import apps as apps_mod, app as app_mod
    importlib.reload(apps_mod)
    app_mod = importlib.reload(app_mod)
    c = TestClient(app_mod.app)
    assert c.get("/api/status", headers=_hdrs(secret="")).status_code == 503
    assert c.post("/api/update/controller", headers=_hdrs()).status_code == 503


def test_health_is_open_for_the_container_probe(client):
    assert client.get("/api/health").json()["status"] == "ok"


# ---- the allowlist is the whole security model -----------------------------
@pytest.mark.parametrize("key", [
    "../../etc", "..%2f..%2fetc", "/etc/passwd", "controller;rm -rf /",
    "controller%00", "CONTROLLER", "nope", ".", "..", "",
])
def test_no_key_outside_the_allowlist_is_ever_accepted(client, key):
    # Several of these never even reach the handler — the router rejects the path
    # first, which is stronger still. What matters is that none of them starts a
    # job, so assert on refusal rather than on which layer did the refusing.
    r = client.post(f"/api/update/{key}", headers=_hdrs())
    assert r.status_code >= 400, f"{key!r} -> {r.status_code}"
    from backend import jobs as jobs_mod
    assert jobs_mod.current() is None, f"{key!r} started a job"


def test_an_unknown_but_well_formed_key_is_named_as_such(client):
    r = client.post("/api/update/nope", headers=_hdrs())
    assert r.status_code == 404 and r.json()["detail"] == "Unknown product."


def test_a_traversal_key_cannot_reach_a_real_checkout(env):
    _, apps_mod, tmp_path = env
    for key in ("../../etc", "/etc", "..", "sysible-controller"):
        with pytest.raises(KeyError):
            apps_mod.checkout_dir(key)


def test_checkout_dir_only_returns_a_git_checkout(env):
    _, apps_mod, tmp_path = env
    assert apps_mod.checkout_dir("controller") == tmp_path / "src" / "sysible-controller"
    # slep is not cloned here — a missing product is None, never a guess.
    assert apps_mod.checkout_dir("slep") is None


# ---- status ----------------------------------------------------------------
def test_status_reports_installed_and_missing_products(client):
    d = client.get("/api/status", headers=_hdrs()).json()
    by = {a["key"]: a for a in d["apps"]}
    assert set(by) == {"controller", "slep", "connect", "slop"}
    assert by["controller"]["installed"] is True
    assert by["controller"]["checked"] is True
    assert by["controller"]["available"] is False        # freshly cloned == current
    assert by["slep"]["installed"] is False
    assert "not installed" in by["slep"]["reason"]


def test_an_update_is_detected_when_the_remote_moves_ahead(env, client, tmp_path):
    up = tmp_path / "upstream"
    (up / "new.txt").write_text("x")
    e = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    subprocess.run(["git", "-C", str(up), "add", "-A"], env=e, capture_output=True)
    subprocess.run(["git", "-C", str(up), "commit", "-qm", "next"], env=e, capture_output=True)

    row = next(a for a in client.get("/api/status", headers=_hdrs()).json()["apps"]
               if a["key"] == "controller")
    assert row["checked"] is True and row["available"] is True
    assert row["current"] != row["latest"]
    assert row["can_update"] is True


def test_a_dirty_checkout_is_reported_and_refused(env, client, tmp_path):
    """A pull would fail or clobber the operator's edit, so say so instead of
    starting a job that destroys work."""
    (tmp_path / "src" / "sysible-controller" / "docker-compose.yml").write_text("services: {x: 1}\n")
    row = next(a for a in client.get("/api/status", headers=_hdrs()).json()["apps"]
               if a["key"] == "controller")
    assert row["dirty"] is True and row["can_update"] is False
    assert "local changes" in row["reason"]

    r = client.post("/api/update/controller", headers=_hdrs())
    assert r.status_code == 409 and "local changes" in r.json()["detail"]


def test_a_product_that_is_not_installed_cannot_be_updated(client):
    r = client.post("/api/update/slep", headers=_hdrs())
    assert r.status_code == 409 and "not installed" in r.json()["detail"]


def test_couldnt_check_never_looks_like_up_to_date(env, client, tmp_path):
    """A checkout with no upstream must report a reason, not a green verdict."""
    _make_repo(tmp_path / "src" / "sysible-connect")          # no remote configured
    row = next(a for a in client.get("/api/status", headers=_hdrs()).json()["apps"]
               if a["key"] == "connect")
    assert row["installed"] is True
    assert row["checked"] is False
    assert "upstream" in row["reason"]
    assert row.get("available") is None or row.get("available") is False
    assert row["can_update"] is False


# ---- running one at a time -------------------------------------------------
def test_only_one_update_runs_at_a_time(env, client, monkeypatch):
    """Two concurrent `compose up --build` runs fight over the daemon, the build
    cache and the container names."""
    import threading
    from backend import jobs as jobs_mod
    release = threading.Event()

    def slow(argv, cwd):
        release.wait(timeout=10)
        return 0

    monkeypatch.setattr(jobs_mod, "_run", slow)
    # Make the remote ahead so the update is allowed to start.
    assert client.post("/api/update/controller", headers=_hdrs()).status_code == 200
    r = client.post("/api/update/controller", headers=_hdrs())
    assert r.status_code == 409 and "already running" in r.json()["detail"]
    release.set()


def test_the_job_log_is_tailed_and_bounded(env, client, monkeypatch):
    from backend import jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "MAX_LOG_LINES", 10)

    def noisy(argv, cwd):
        for i in range(50):
            jobs_mod._log(f"line {i}")
        return 0

    monkeypatch.setattr(jobs_mod, "_run", noisy)
    assert client.post("/api/update/controller", headers=_hdrs()).status_code == 200
    import time
    for _ in range(50):
        j = client.get("/api/job", headers=_hdrs()).json()["job"]
        if j and j["state"] != "running":
            break
        time.sleep(0.05)
    assert j["state"] == "succeeded"
    assert len(j["log"]) <= 10, "the log must stay bounded — it lives in memory"


def test_a_failed_step_is_reported_as_failed(env, client, monkeypatch):
    from backend import jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "_run", lambda argv, cwd: 1)
    assert client.post("/api/update/controller", headers=_hdrs()).status_code == 200
    import time
    for _ in range(50):
        j = client.get("/api/job", headers=_hdrs()).json()["job"]
        if j and j["state"] != "running":
            break
        time.sleep(0.05)
    assert j["state"] == "failed"
    assert "git pull failed" in j["message"]


def test_the_actor_is_recorded_on_the_job(env, client, monkeypatch):
    from backend import jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "_run", lambda argv, cwd: 0)
    client.post("/api/update/controller", headers=_hdrs(user="carol"))
    assert client.get("/api/job", headers=_hdrs()).json()["job"]["actor"] == "carol"
