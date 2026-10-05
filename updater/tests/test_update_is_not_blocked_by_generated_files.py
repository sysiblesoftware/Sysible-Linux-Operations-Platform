"""An update must not be blocked by a file the platform itself wrote, and must not
be offered when there is nothing to pull.

Both of these were reported from a real host: SLEP and Connect sat on "Update
available … the checkout has local changes", while `git pull` on the host said
"Already up to date". Two separate bugs with one symptom.
"""
import subprocess
from pathlib import Path

import pytest

from backend import git


def _run(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


@pytest.fixture
def pair(tmp_path):
    """(upstream, clone) — a real pair, because this is all git's own behaviour."""
    up = tmp_path / "up"
    up.mkdir()
    _run(up, "init", "-q", "-b", "main")
    (up / "f").write_text("a\n")
    _run(up, "add", "-A")
    _run(up, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "one")
    down = tmp_path / "down"
    subprocess.run(["git", "clone", "-q", str(up), str(down)], check=True)
    return up, down


def _advance(up):
    (up / "f").write_text((up / "f").read_text() + "b\n")
    _run(up, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "two")


# --------------------------------------------------------------------- dirty()
def test_a_generated_env_does_not_count_as_local_changes(pair):
    """SLOP's installer writes deploy/.env into every app's compose dir. An app
    that did not gitignore it could never be updated again from the day it was
    installed — and the .gitignore that fixes it could only arrive through the
    update it was blocking."""
    _, down = pair
    (down / "deploy").mkdir()
    (down / "deploy" / ".env").write_text("SYSIBLE_SSO_SHARED_SECRET=s3cret\n")
    assert git.dirty(down) is False


def test_an_edited_tracked_file_still_counts(pair):
    """The guard still has to fire for the thing it is actually for."""
    _, down = pair
    (down / "f").write_text("edited\n")
    assert git.dirty(down) is True


def test_a_pull_keeps_an_untracked_file_and_still_fast_forwards(pair):
    """Why ignoring untracked files is safe, asserted rather than assumed."""
    up, down = pair
    (down / "deploy").mkdir()
    (down / "deploy" / ".env").write_text("SECRET=x\n")
    _advance(up)
    r = _run(down, "pull", "--ff-only")
    assert r.returncode == 0, r.stderr
    assert (down / "deploy" / ".env").read_text() == "SECRET=x\n"
    assert (down / "f").read_text() == "a\nb\n"


# ------------------------------------------------------------------- available
def _sha(root, rev="HEAD"):
    return _run(root, "rev-parse", rev).stdout.strip()


def test_behind_is_true_when_the_remote_has_moved(pair):
    up, down = pair
    _advance(up)
    _run(down, "fetch", "-q")
    assert git._behind(down, _sha(up), _sha(down)) is True


def test_a_checkout_that_is_AHEAD_is_not_an_update(pair):
    """This is the reported contradiction: `git pull` says "Already up to date"
    while the console says "update available", because the old test was `latest !=
    cur` — true for any difference, including being ahead."""
    up, down = pair
    (down / "g").write_text("local work\n")
    _run(down, "add", "-A")
    _run(down, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "ahead")
    assert git._behind(down, _sha(up), _sha(down)) is False


def test_identical_is_not_an_update(pair):
    _, down = pair
    assert git._behind(down, _sha(down), _sha(down)) is False


def test_an_unfetched_commit_counts_as_behind(pair):
    """ls-remote sees the tip without fetching it, so the object is not here. Not
    having it is the definition of behind."""
    up, down = pair
    _advance(up)
    assert git._behind(down, _sha(up), _sha(down)) is True


def test_a_diverged_checkout_is_still_offered(pair):
    """Local commits AND remote commits. A pull will fail, but the console must not
    pretend there is nothing upstream."""
    up, down = pair
    _advance(up)
    _run(down, "fetch", "-q")
    (down / "g").write_text("mine\n")
    _run(down, "add", "-A")
    _run(down, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "mine")
    assert git._behind(down, _sha(up), _sha(down)) is True
