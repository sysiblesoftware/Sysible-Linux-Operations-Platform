"""The two failures an operator actually hit, and what they were told.

    docker: 'compose' is not a docker command.
    no configuration file provided: not found

Both came out of this package, and both are Docker's words rather than ours:
the first names nothing Sysible ships and suggests nothing to do, the second
does not say WHICH directory it searched — and that directory arrives as a bind
mount from the host, so "pointed at the wrong place" is the likeliest cause and
the one the message hides.

Neither was a Docker bug. Every executed `docker compose` in the whole suite is
in this package, and not one of them checked that compose existed. updater's
Dockerfile asserted that the base image supplied the plugin ("the CLI and the
compose plugin come from the base rather than being installed by hand") — an
unverified property of a third-party image behind a floating tag. Meanwhile
sysiblectl detected compose and fell back to the standalone binary, and
install.sh installed one when it was missing, so the GUI was the single surface
with no detection at all.
"""
import subprocess
import sys

import pytest

sys.path.insert(0, __import__("os").path.dirname(
    __import__("os").path.dirname(__import__("os").path.abspath(__file__))))

from backend import compose as compose_mod  # noqa: E402
from backend import jobs  # noqa: E402


@pytest.fixture()
def no_compose(monkeypatch):
    """A container with the docker CLI and no compose of any kind."""
    monkeypatch.setattr(compose_mod, "_cached", None)
    monkeypatch.setattr(compose_mod.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1))
    monkeypatch.setattr(compose_mod.shutil, "which", lambda _n: None)
    yield
    monkeypatch.setattr(compose_mod, "_cached", None)


@pytest.fixture()
def standalone_only(monkeypatch):
    """No v2 plugin, but the standalone docker-compose binary is present —
    exactly what the Podman fallback and an older Debian host give you."""
    monkeypatch.setattr(compose_mod, "_cached", None)
    monkeypatch.setattr(compose_mod.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1))
    monkeypatch.setattr(compose_mod.shutil, "which",
                        lambda n: "/usr/bin/docker-compose" if n == "docker-compose" else None)
    yield
    monkeypatch.setattr(compose_mod, "_cached", None)


class TestComposeMissingAltogether:
    def test_it_is_detected_rather_than_assumed(self, no_compose):
        assert compose_mod.compose_argv() == []
        assert not compose_mod.available()

    def test_the_operator_is_told_what_to_do(self, no_compose):
        why = compose_mod.unavailable_reason()
        assert why
        assert "compose" in why.lower()
        # The remedy, not just the diagnosis.
        assert "--build updater" in why or "docker-compose-plugin" in why

    def test_an_update_is_refused_with_that_reason(self, no_compose, tmp_path):
        ok, msg = jobs.start("controller", tmp_path, tmp_path, "alice")
        assert not ok
        assert "compose" in msg.lower()
        assert "is not a docker command" not in msg, \
            "Docker's raw message is being passed through to the operator"

    @pytest.mark.parametrize("action", ["restart", "start", "recreate"])
    def test_a_lifecycle_action_is_refused_with_that_reason(self, no_compose, tmp_path, action):
        ok, msg = jobs.start_action("controller", action, tmp_path, "alice")
        assert not ok
        assert "compose" in msg.lower()


class TestTheStandaloneBinaryIsUsedWhenThatIsAllThereIs:
    def test_it_falls_back_instead_of_failing(self, standalone_only):
        assert compose_mod.compose_argv() == ["docker-compose"]
        assert compose_mod.unavailable_reason() is None

    def test_a_literal_argv_is_rewritten_at_its_prefix_only(self, standalone_only):
        """The argv tables in jobs.py are deliberately literal — "the action is a
        KEY from this table, never a string from the caller, and the argv is a
        literal". Only the two-word prefix may change; every flag must survive."""
        got = compose_mod.normalize(["docker", "compose", "up", "-d", "--build"])
        assert got == ["docker-compose", "up", "-d", "--build"]

    def test_a_non_compose_argv_is_untouched(self, standalone_only):
        for argv in (["docker", "ps", "-a"], ["git", "pull", "--ff-only"], ["env", "X=1"]):
            assert compose_mod.normalize(argv) == argv

    def test_the_detached_helper_script_is_rewritten_too(self, standalone_only):
        """The self-update runs in a throwaway container from this same image, so
        it has the same compose — and its failure lands in a detached container's
        log the operator has to go and find."""
        from backend import detached
        out = compose_mod.shell_script(detached.SCRIPT_UPDATE)
        assert "exec docker-compose up -d --build" in out
        assert "docker compose" not in out
        # git, and the step markers the console parses, must be untouched.
        assert "git -c safe.directory='*' pull --ff-only" in out
        assert ">>> step: pull" in out and ">>> step: build" in out


class TestWithThePluginNothingChanges:
    def test_the_argv_is_left_exactly_as_written(self, monkeypatch):
        monkeypatch.setattr(compose_mod, "_cached", ["docker", "compose"])
        argv = ["docker", "compose", "up", "-d", "--build"]
        assert compose_mod.normalize(argv) == argv

    def test_the_detached_script_is_left_byte_identical(self, monkeypatch):
        from backend import detached
        monkeypatch.setattr(compose_mod, "_cached", ["docker", "compose"])
        assert compose_mod.shell_script(detached.SCRIPT_UPDATE) == detached.SCRIPT_UPDATE


class TestNoComposeFileInTheDirectory:
    """Compose's own message for this omits the path, which is the one fact
    needed to debug it."""

    def test_the_directory_is_named(self, tmp_path):
        empty = tmp_path / "not-a-checkout"
        empty.mkdir()
        why = compose_mod.missing_file_reason(empty)
        assert why and str(empty) in why

    def test_it_says_what_it_looked_for_and_what_was_there(self, tmp_path):
        d = tmp_path / "wrong"
        d.mkdir()
        (d / "README.md").write_text("x")
        why = compose_mod.missing_file_reason(d)
        assert "docker-compose.yml" in why
        assert "README.md" in why, "it does not show what the directory does contain"

    def test_it_points_at_the_bind_mount_as_the_likely_cause(self, tmp_path):
        d = tmp_path / "wrong2"
        d.mkdir()
        assert "bind mount" in compose_mod.missing_file_reason(d)

    def test_a_path_that_is_not_a_directory_is_distinguished(self, tmp_path):
        why = compose_mod.missing_file_reason(tmp_path / "does-not-exist")
        assert why and "not a directory" in why

    @pytest.mark.parametrize("name", list(compose_mod.COMPOSE_FILENAMES))
    def test_every_name_compose_accepts_is_accepted_here(self, tmp_path, name):
        """Checking only docker-compose.yml would refuse a checkout that compose
        itself would have driven happily."""
        d = tmp_path / name.replace(".", "_")
        d.mkdir()
        (d / name).write_text("services: {}\n")
        assert compose_mod.missing_file_reason(d) is None

    def test_an_update_is_refused_before_it_starts(self, tmp_path):
        d = tmp_path / "bare"
        d.mkdir()
        ok, msg = jobs.start("controller", d, d, "alice")
        assert not ok
        assert str(d) in msg
        assert "no configuration file provided" not in msg, \
            "compose's pathless message is being passed through to the operator"
