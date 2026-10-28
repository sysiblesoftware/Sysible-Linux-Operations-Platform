"""A job reports the phase it is in, so the console can show a sequence.

An update is `git pull` then `docker compose up -d --build`, and the second one
is minutes long. "Running" for four minutes is not a progress display, so the
job announces each phase into its own log and the console reads it back.

A MARKER rather than a guess at the output, for two reasons that are easy to
lose sight of:

  * A self-affecting job runs in a detached helper container (detached.py). Its
    log is raw command output — there is no argv echo to key off, because the
    commands are not run by this process at all.
  * docker compose's own wording is not ours to depend on across versions.

So the same marker is written by both paths and read by one parser. The tests
that matter here are the ones that check the two agree: a marker the helper
emits that step_of does not understand is a phase the console silently never
shows, and nothing else would notice.

Run: pytest updater/tests/
"""
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from backend import detached, jobs


# ---- the parser -------------------------------------------------------------
def test_the_last_phase_announced_is_the_one_reported():
    log = ["Cloning", jobs.mark("pull"), "From github.com", jobs.mark("build"),
           "Building idp"]
    assert jobs.step_of(log) == "build"


def test_a_job_that_has_not_said_yet_reports_nothing():
    """Empty is a real answer — a job that has only just started, or whose log
    was trimmed past its markers. The console renders that as "starting", which
    is honest; inventing a phase it cannot see is not."""
    assert jobs.step_of([]) == ""
    assert jobs.step_of(["Building idp", "Container slop-idp Started"]) == ""
    assert jobs.step_of(None) == ""


def test_output_that_merely_looks_like_a_marker_is_not_one():
    """A build can print anything at all, including this."""
    assert jobs.step_of(["echo '>>> step: build'", "Step 3/9 : RUN pip install"]) == ""
    assert jobs.step_of([jobs.mark("sideways")]) == "", \
        "a phase this console has no column for was accepted"


def test_it_reads_a_marker_even_with_surrounding_whitespace():
    """The helper's log comes back through `docker logs`, which is not a channel
    that promises to leave lines exactly as written."""
    assert jobs.step_of(["  " + jobs.mark("pull") + "  \r"]) == "pull"


# ---- the two paths must agree ----------------------------------------------
def test_the_detached_helper_announces_phases_this_parser_understands():
    """The one that would rot silently. The helper is a shell string and the
    parser is Python; nothing but this test connects them, and a drifted marker
    costs exactly the case the phases exist for — a SLOP self-update, which is
    the only kind that runs detached."""
    emitted = re.findall(r"echo '([^']*)'", detached.SCRIPT_UPDATE)
    assert emitted, "the update helper announces no phases at all"
    assert jobs.step_of(emitted) == "build", emitted
    # Every marker it emits, not just the last.
    for line in emitted:
        assert jobs.step_of([line]), f"the helper emits {line!r}, which means nothing here"


@pytest.mark.parametrize("action", sorted(detached.SCRIPT_ACTIONS))
def test_every_detached_action_announces_itself(action):
    emitted = re.findall(r"echo '([^']*)'", detached.SCRIPT_ACTIONS[action])
    assert jobs.step_of(emitted) == "act", (action, emitted)


def test_the_helper_still_fails_the_whole_script_on_a_failed_pull():
    """`set -e` and the marker echoes have to coexist: an echo inserted between
    two commands must not become the thing whose exit status is reported."""
    assert detached.SCRIPT_UPDATE.startswith("set -e;"), detached.SCRIPT_UPDATE
    assert "exec docker compose up -d --build" in detached.SCRIPT_UPDATE
    # The exec is still last, so the helper's exit code is compose's.
    assert detached.SCRIPT_UPDATE.rstrip().endswith("up -d --build")


@pytest.mark.parametrize("action", sorted(detached.SCRIPT_ACTIONS))
def test_an_action_helper_still_execs_its_command_last(action):
    script = detached.SCRIPT_ACTIONS[action]
    assert "exec docker compose" in script, script
    assert script.index("echo") < script.index("exec"), \
        f"{action}: the marker is after the exec, so it never runs"


# ---- what the console is handed ---------------------------------------------
def test_the_running_job_carries_its_step(monkeypatch):
    """current() is what /api/job returns. A step computed but never attached is
    a phase the console cannot see."""
    monkeypatch.setattr(jobs, "_job", {
        "app": "controller", "state": "running", "started": 1.0, "finished": None,
        "message": "", "log": [jobs.mark("pull"), "From github.com"],
        "self_update": False})
    monkeypatch.setattr(jobs.detached, "status", lambda *a, **k: None)
    j = jobs.current()
    assert j["step"] == "pull", j


def test_a_detached_job_carries_its_step_too(monkeypatch):
    """The case the markers exist for: this job's log came from another
    container, so there is nothing else to derive a phase from."""
    monkeypatch.setattr(jobs, "_job", None)
    monkeypatch.setattr(jobs.detached, "status", lambda *a, **k: {
        "app": "slop", "state": "running", "detached": True, "self_update": True,
        "message": "", "started": 1.0, "finished": None,
        "log": [">>> step: pull", "Updating 538a889..359854c", ">>> step: build"]})
    j = jobs.current()
    assert j["step"] == "build", j


def test_the_markers_are_the_same_string_on_both_sides():
    """Two spellings of the same marker is the drift this whole file guards."""
    assert jobs.mark("pull") in detached.SCRIPT_UPDATE
    assert jobs.mark("build") in detached.SCRIPT_UPDATE
    for script in detached.SCRIPT_ACTIONS.values():
        assert jobs.mark("act") in script
