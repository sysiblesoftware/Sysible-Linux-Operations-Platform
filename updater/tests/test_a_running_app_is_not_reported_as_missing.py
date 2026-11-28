"""Three running apps were reported as "No container".

REPORTED, with a screenshot of Administration - Software & services, while the
operator was working in SLEP at that moment:

    Controller            No container
      docker compose ps does not list the controller service
    Engineering Platform  No container
    Connect               No container
    Gateway  Running - Up About a minute
    ...

The sentence was literally true and completely wrong. The Controller, SLEP and
Connect are declared `profiles: ["integrated"]` in SLOP's compose file, and
compose does not CONSIDER a service whose profile is inactive -- not for `up`,
and not for `ps`. The status read ran a bare `docker compose ps`, so those three
were absent from the listing whatever their containers were doing.

Demonstrated against a real compose (v5.7.1), two services, one gated:

    docker compose config --services                       -> always
    COMPOSE_PROFILES=integrated docker compose config -s    -> always, gated

`ps` has no --profile flag, so the profile has to arrive through the
environment. That is the fix, and the reason it is the environment and not an
argv flag is worth keeping written down.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE.parent))

from updater.backend import jobs, wiring  # noqa: E402


def _captured(monkeypatch):
    """Run services() against a stubbed subprocess and return the kwargs it used."""
    seen = {}

    class R:
        returncode = 0
        stdout = "[]"
        stderr = ""

    def fake_run(argv, **kw):
        seen["argv"] = argv
        seen["kw"] = kw
        return R()

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    jobs.services(Path("/srv/slop"))
    return seen


def test_the_status_read_asks_about_the_gated_services(monkeypatch):
    """Without this the three profile-gated apps are missing from the listing and
    the page calls a running container 'No container'."""
    seen = _captured(monkeypatch)
    env = seen["kw"].get("env") or {}
    profiles = [p.strip() for p in (env.get("COMPOSE_PROFILES") or "").split(",") if p.strip()]
    assert wiring.INTEGRATED_PROFILE in profiles, (
        "compose is asked for status without the integrated profile, so the "
        "Controller, SLEP and Connect cannot appear in the answer")


def test_it_does_not_discard_an_operators_own_profiles(monkeypatch):
    """A status read must not quietly narrow what the host runs."""
    monkeypatch.setenv("COMPOSE_PROFILES", "gpu,lab")
    seen = _captured(monkeypatch)
    profiles = [p.strip() for p in (seen["kw"]["env"]["COMPOSE_PROFILES"]).split(",") if p.strip()]
    assert "gpu" in profiles and "lab" in profiles, profiles
    assert wiring.INTEGRATED_PROFILE in profiles, profiles


def test_the_profile_is_not_duplicated_when_already_set(monkeypatch):
    monkeypatch.setenv("COMPOSE_PROFILES", wiring.INTEGRATED_PROFILE)
    seen = _captured(monkeypatch)
    profiles = [p.strip() for p in (seen["kw"]["env"]["COMPOSE_PROFILES"]).split(",") if p.strip()]
    assert profiles.count(wiring.INTEGRATED_PROFILE) == 1, profiles


def test_it_still_reads_the_right_directory(monkeypatch):
    """Passing an env must not have displaced cwd or the timeout."""
    seen = _captured(monkeypatch)
    assert seen["kw"].get("cwd") == "/srv/slop"
    assert seen["kw"].get("timeout")
    assert "ps" in seen["argv"] and "--all" in seen["argv"]


def test_the_environment_is_inherited_not_replaced(monkeypatch):
    """compose needs PATH and DOCKER_HOST. An env built from scratch would find
    no docker at all, turning a reporting bug into a blank page."""
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1000/docker.sock")
    seen = _captured(monkeypatch)
    env = seen["kw"]["env"]
    assert env.get("DOCKER_HOST") == "unix:///run/user/1000/docker.sock"
    assert env.get("PATH") == os.environ.get("PATH")


def _compose():
    return subprocess.run(["docker", "compose", "version"],
                          capture_output=True).returncode == 0


@pytest.mark.skipif(not _compose(), reason="needs the docker compose plugin")
def test_compose_really_does_hide_a_gated_service(tmp_path):
    """The premise, checked against the real thing rather than remembered.

    Needs no docker DAEMON: the profile filter is applied while the model is
    being built, which is why `config` shows it and why the bug reached `ps`."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  always:\n    image: busybox\n    command: sleep 1\n"
        "  gated:\n    profiles: [\"%s\"]\n    image: busybox\n    command: sleep 1\n"
        % wiring.INTEGRATED_PROFILE)

    def names(env=None):
        e = dict(os.environ)
        e.pop("COMPOSE_PROFILES", None)
        e.update(env or {})
        p = subprocess.run(["docker", "compose", "config", "--services"],
                           cwd=tmp_path, capture_output=True, text=True, env=e, timeout=60)
        return sorted(x.strip() for x in p.stdout.split() if x.strip())

    assert names() == ["always"], "compose no longer hides an inactive profile"
    assert names({"COMPOSE_PROFILES": wiring.INTEGRATED_PROFILE}) == ["always", "gated"]
