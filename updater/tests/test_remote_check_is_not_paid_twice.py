"""Browsing Administration must not cost a round-trip to every git remote.

GET /api/status asks each product's remote for its tip. Administration's header
pill polls that endpoint on EVERY page it renders — accounts, apps, configuration,
updates — so opening any of them fired one `git ls-remote` per installed product,
and a slow or unreachable remote stalled the page for REMOTE_TIMEOUT per product
before it could draw.

The tip is now memoised for a few minutes. That is only safe while a fresh answer
stays reachable, so these pin both halves: the cache, and the explicit re-check
that bypasses it.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import git  # noqa: E402


def _repo(path: Path, remote: Path) -> Path:
    """A real checkout with a real upstream, so the caching wraps real git calls."""
    path.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    def run(*a, cwd):
        subprocess.run(a, cwd=str(cwd), check=True, capture_output=True, env=env)
    remote.mkdir(parents=True, exist_ok=True)
    run("git", "init", "-q", "-b", "main", cwd=remote)
    (remote / "f").write_text("1")
    run("git", "add", "f", cwd=remote)
    run("git", "commit", "-qm", "one", cwd=remote)
    run("git", "clone", "-q", str(remote), str(path), cwd=path.parent)
    return path


@pytest.fixture(autouse=True)
def _clear_cache():
    git._remote_cache.clear()
    yield
    git._remote_cache.clear()


@pytest.fixture()
def counted(monkeypatch):
    """Count the ls-remote calls that actually reach git."""
    calls = []
    real = git._git

    def spy(root, *args, **kw):
        if args and args[0] == "ls-remote":
            calls.append(str(root))
        return real(root, *args, **kw)

    monkeypatch.setattr(git, "_git", spy)
    return calls


def test_repeated_status_checks_ask_the_remote_once(tmp_path, counted):
    root = _repo(tmp_path / "work", tmp_path / "origin")
    for _ in range(5):
        assert git.status(root)["checked"] is True
    assert len(counted) == 1, f"asked the remote {len(counted)} times for 5 status calls"


def test_the_cached_answer_is_the_same_answer(tmp_path, counted):
    root = _repo(tmp_path / "work", tmp_path / "origin")
    first = git.status(root)
    assert git.status(root) == first


def test_an_explicit_recheck_bypasses_the_cache(tmp_path, counted):
    """A cached answer must never be the only answer available, or the console
    could show 'up to date' for minutes after a release with no way to ask again."""
    root = _repo(tmp_path / "work", tmp_path / "origin")
    git.status(root)
    git.status(root, fresh=True)
    assert len(counted) == 2


def test_a_release_landing_upstream_is_seen_on_an_explicit_recheck(tmp_path, counted):
    origin = tmp_path / "origin"
    root = _repo(tmp_path / "work", origin)
    assert git.status(root)["available"] is False
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    (origin / "f").write_text("2")
    subprocess.run(["git", "commit", "-aqm", "two"], cwd=str(origin), check=True,
                   capture_output=True, env=env)
    assert git.status(root)["available"] is False, "the cached answer should still stand"
    assert git.status(root, fresh=True)["available"] is True


def test_the_cache_expires(tmp_path, counted, monkeypatch):
    root = _repo(tmp_path / "work", tmp_path / "origin")
    monkeypatch.setattr(git, "REMOTE_TTL", 0.05)
    git.status(root)
    time.sleep(0.1)
    git.status(root)
    assert len(counted) == 2


def test_a_pull_forgets_what_was_remembered(tmp_path, counted):
    """After a pull the memoised tip describes the world before it."""
    root = _repo(tmp_path / "work", tmp_path / "origin")
    git.status(root)
    git.forget_remote(root)
    git.status(root)
    assert len(counted) == 2


def test_a_failure_is_remembered_only_briefly(tmp_path, counted, monkeypatch):
    """An unreachable remote must not stall every admin page for the full timeout —
    but it must also not keep reading as broken once the network is back."""
    root = _repo(tmp_path / "work", tmp_path / "origin")
    subprocess.run(["git", "remote", "set-url", "origin", str(tmp_path / "gone")],
                   cwd=str(root), check=True, capture_output=True)
    first = git.status(root)
    assert first["checked"] is False and "ls-remote" in first["reason"]
    git.status(root)
    assert len(counted) == 1, "a failure should be cached too, or the page stalls each time"
    monkeypatch.setattr(git, "REMOTE_FAIL_TTL", 0.05)
    time.sleep(0.1)
    git.status(root)
    assert len(counted) == 2, "a failure must expire sooner than a success"


def test_the_failure_ttl_is_shorter_than_the_success_ttl():
    assert 0 < git.REMOTE_FAIL_TTL < git.REMOTE_TTL


def test_two_checkouts_do_not_share_an_answer(tmp_path, counted):
    a = _repo(tmp_path / "a", tmp_path / "origin-a")
    b = _repo(tmp_path / "b", tmp_path / "origin-b")
    git.status(a)
    git.status(b)
    assert len(counted) == 2
