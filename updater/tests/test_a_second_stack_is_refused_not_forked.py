"""Starting from a different directory forked the platform in two.

REPORTED, from a pull that failed:

    Volume "sysible-linux-operations-platform_connect-data"  Created
    Volume "sysible-linux-operations-platform_slep-data"     Created
    Container sysible-connect     Creating
    Container sysible-controller  Creating
    Container sysible-slep        Creating
    Error response from daemon: Conflict. The container name "/sysible-connect"
    is already in use by container "12c88950b2d634db46d8c28d443a9b31...".

Two details in that log say what happened. Compose printed "Creating", not
"Recreate" -- so this project has never owned those containers. And it created
brand-new data volumes -- so this project has never had any data either.

The cause is two decisions meeting. Every service pins `container_name:`, and a
pinned name is global to the daemon rather than scoped to a project; and the
compose file had no `name:`, so the project name came from the DIRECTORY. Bring
the stack up from somewhere else and compose does not adopt the running stack,
it forks it: new volumes, same names, hard conflict.

The same split explains two earlier reports that looked unrelated --
Administration showing "No container" for three apps that were running, and a
SLEP container inspected with an empty /data/projects while Terraform was
quoting real line numbers out of a project directory. Two stacks; the work in
the other one's volumes.

Docker's own message names neither the project nor the data, and the obvious
response to it -- remove the container that is in the way -- is the single
action that strands the operator's projects.
"""
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from updater.backend import compose as c  # noqa: E402

PROJECT = "sysible-linux-operations-platform"


# ------------------------------------------------- the file pins both things

def test_the_compose_file_pins_the_project_name():
    """Without this the project name is the directory, and a renamed or
    re-cloned checkout silently becomes a second platform."""
    text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert any(l.strip() == f"name: {PROJECT}" for l in text.splitlines()), (
        "docker-compose.yml does not pin `name:` — the project name would come "
        "from the directory again")


def test_the_services_still_pin_their_container_names():
    """The pinned names are why the fork is a hard failure rather than two quiet
    copies. If they ever stop being pinned this whole guard is moot — and the
    preflight below would have nothing to look for."""
    names = c.pinned_container_names(REPO)
    for expected in ("sysible-slep", "sysible-connect", "sysible-controller"):
        assert expected in names, names


# ---------------------------------------------------------- the preflight

def test_containers_this_project_owns_are_not_a_problem():
    """A name we already hold is just a restart."""
    assert c.forked_stack_message(PROJECT, {
        "sysible-slep": PROJECT, "sysible-connect": PROJECT}) is None


def test_the_reported_fork_is_named():
    msg = c.forked_stack_message(PROJECT, {
        "sysible-connect": "slop", "sysible-slep": "slop", "sysible-controller": "slop"})
    assert msg
    assert "sysible-connect" in msg and "sysible-slep" in msg
    assert "slop" in msg, "the message does not say which project holds them"
    assert PROJECT in msg, "the message does not say what this project is called"


def test_it_warns_against_the_thing_the_operator_will_otherwise_do():
    """Docker says "You have to remove (or rename) that container". Following
    that advice here brings the apps back EMPTY, because the data is in the other
    project's volumes."""
    msg = c.forked_stack_message(PROJECT, {"sysible-slep": "slop"})
    assert "DO NOT delete" in msg, msg
    assert "EMPTY" in msg, "the message does not say what deleting would cost"
    assert "volume" in msg.lower()


def test_it_says_where_the_fork_came_from():
    """An operator who does not know the project name comes from the directory
    cannot act on any of this."""
    msg = c.forked_stack_message(PROJECT, {"sysible-slep": "slop"})
    assert "directory" in msg.lower(), msg


def test_a_container_with_no_compose_label_is_still_caught():
    """Something hand-started with `docker run --name sysible-slep` holds the
    name just as hard, and has no project to name."""
    msg = c.forked_stack_message(PROJECT, {"sysible-slep": ""})
    assert msg and "no compose project" in msg


def test_only_the_foreign_names_are_listed():
    """Half ours, half theirs: naming all of them sends the operator looking at
    containers that are fine."""
    msg = c.forked_stack_message(PROJECT, {
        "sysible-slep": PROJECT, "sysible-connect": "slop"})
    assert "sysible-connect" in msg
    assert "sysible-slep" not in msg, msg


def test_nothing_running_is_not_a_fork():
    assert c.forked_stack_message(PROJECT, {}) is None


def test_the_check_is_actually_wired_into_the_preflight():
    """A guard nothing calls is a guard that does not exist."""
    import inspect
    src = inspect.getsource(c.blocked_reason)
    assert "forked_stack_reason" in src, src


def test_the_preflight_never_becomes_the_failure(monkeypatch):
    """It runs when things are already wrong. A daemon that will not answer must
    make it quiet, not make it throw."""
    def boom(*a, **k):
        raise OSError("no docker here")
    monkeypatch.setattr(c.subprocess, "run", boom)
    assert c.container_owners(["sysible-slep"]) == {}
    assert c.forked_stack_reason(REPO) is None


def test_a_directory_with_no_compose_file_is_quiet(tmp_path):
    assert c.pinned_container_names(tmp_path) == []
    assert c.forked_stack_reason(tmp_path) is None


# ------------------------------------------- the premise, against real compose

def _compose():
    return subprocess.run(["docker", "compose", "version"],
                          capture_output=True).returncode == 0


@pytest.mark.skipif(not _compose(), reason="needs the docker compose plugin")
def test_the_name_key_really_does_pin_the_project(tmp_path):
    """Checked against the real thing, and it needs no daemon: the project name
    is settled while the model is built."""
    d = tmp_path / "weird-dir-name"
    d.mkdir()
    body = ("services:\n  demo:\n    container_name: sysible-demo\n"
            "    image: busybox\n    command: sleep 1\n")

    def name_of(text):
        (d / "docker-compose.yml").write_text(text)
        p = subprocess.run(["docker", "compose", "config", "--format", "json"],
                           cwd=d, capture_output=True, text=True, timeout=60)
        import json
        return json.loads(p.stdout)["name"]

    assert name_of(body) == "weird-dir-name", (
        "compose no longer derives the project from the directory — the hazard "
        "this guards against may have changed shape")
    assert name_of(f"name: {PROJECT}\n" + body) == PROJECT
