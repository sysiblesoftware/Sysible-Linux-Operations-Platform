"""An update that recreates the container running it.

SLOP updates itself: `docker compose up -d --build` in this checkout recreates
every service in the project, and the updater is one of them. The compose client
doing the work was a child of the updater process, inside that container.

Measured, with real Docker, on a two-service stand-in project whose `runner`
service ran `docker compose up -d --force-recreate` against its own project:

    exp-victim                 Exited (137)      <- SIGKILLed
    exp-runner                 Exited (137)      <- SIGKILLed
    c20a6df4086f_exp-victim    Created           <- never started
    5fda9aa91c5a_exp-runner    Created           <- never started

Nothing running. compose builds each replacement under a temporary
<oldid>_<name> and renames it over the original only after removing the old one,
so a kill anywhere in that sequence leaves the stack down and half-swapped, with
nothing still running that could finish it. Press Update on the Operations
Platform and this is what you can be left with: no gateway, no console, and the
one tool that would have told you is behind the gateway.

The same project, updated by a detached helper that is NOT part of it:

    exp-victim   Up   (new id)     helper exit 0
    exp-runner   Up   (new id)

These tests hold both halves: the wiring (a self-affecting job is handed off,
and nothing a caller sends reaches the helper's command line) and, where Docker
is available, the claim itself — that the helper finishes the job after the
container that started it has been destroyed.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import detached, jobs  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_job():
    jobs._job = None
    detached._invalidate()
    if jobs._lock.locked():                     # a previous test's leak
        jobs._lock.release()
    yield
    jobs._job = None
    detached._invalidate()


@pytest.fixture()
def spawned(monkeypatch):
    """Capture what would have been launched, without launching it."""
    calls = []

    def _fake_spawn(script, cwd, action="update"):
        calls.append({"script": script, "cwd": Path(cwd), "action": action})
        return True, "started"

    monkeypatch.setattr(detached, "spawn", _fake_spawn)
    monkeypatch.setattr(detached, "running", lambda: False)
    monkeypatch.setattr(detached, "status", lambda *a, **k: None)
    return calls


# ---- the hand-off ----------------------------------------------------------
def test_updating_slop_does_not_run_in_this_process(spawned, monkeypatch, tmp_path):
    """The whole fix. In-process, compose kills its own client part way through."""
    started_threads = []
    monkeypatch.setattr(jobs.threading, "Thread",
                        lambda *a, **k: pytest.fail("the self-update ran in-process"))
    ok, msg = jobs.start("slop", tmp_path, tmp_path, "alice")
    assert ok, msg
    assert len(spawned) == 1
    assert spawned[0]["script"] == detached.SCRIPT_UPDATE
    assert spawned[0]["cwd"] == tmp_path
    assert "restart" in msg.lower(), \
        "the operator is not told the page is about to drop"


def test_another_product_still_updates_in_process(spawned, tmp_path, monkeypatch):
    """Only SLOP owns this container. Handing every product off would cost a
    container per update for nothing."""
    ran = []
    monkeypatch.setattr(jobs, "_run", lambda argv, cwd: ran.append(argv) or 0)
    ok, _ = jobs.start("controller", tmp_path, tmp_path, "alice")
    assert ok
    for _ in range(100):
        if len(ran) >= 2:
            break
        time.sleep(0.02)
    assert not spawned, "a product that does not own this container was handed off"
    assert ran, "the in-process path stopped running"


@pytest.mark.parametrize("action", ["restart", "recreate", "start"])
def test_slop_lifecycle_actions_are_handed_off_too(spawned, tmp_path, action):
    """`compose restart` stops every service in the project, and the client asking
    for it is inside one of them — the same self-kill as an update."""
    ok, msg = jobs.start_action("slop", action, tmp_path, "alice")
    assert ok, msg
    assert spawned[0]["script"] == detached.SCRIPT_ACTIONS[action]
    assert spawned[0]["action"] == action, (
        "the helper does not record WHICH action it is running, so after the "
        "restart the console calls every one of them an update")


def test_stopping_slop_is_still_refused(tmp_path):
    """Detaching makes `stop` POSSIBLE, which is not the same as wise: it would
    take down the gateway, the console and this service with no way back."""
    assert jobs.action_refusal("slop", "stop")


def test_a_second_job_is_refused_while_the_helper_runs(monkeypatch, tmp_path):
    """The in-process lock cannot see the helper — it lives in another container,
    and restarting this one resets the lock while the work carries on. Two
    `compose up --build` runs on one project fight over container names."""
    monkeypatch.setattr(detached, "running", lambda: True)
    ok, msg = jobs.start("slop", tmp_path, tmp_path, "alice")
    assert not ok and "already running" in msg
    ok, msg = jobs.start_action("slop", "restart", tmp_path, "alice")
    assert not ok and "already running" in msg


def test_the_lock_is_not_left_held_after_a_hand_off(spawned, tmp_path):
    """It is released because the helper is the lock from then on. Leaving it held
    would refuse every later update until this container restarted."""
    assert jobs.start("slop", tmp_path, tmp_path, "alice")[0]
    assert not jobs._lock.locked()
    assert jobs.start_action("slop", "restart", tmp_path, "alice")[0]
    assert not jobs._lock.locked()


def test_a_helper_that_will_not_start_is_reported_not_swallowed(monkeypatch, tmp_path):
    monkeypatch.setattr(detached, "running", lambda: False)
    monkeypatch.setattr(detached, "spawn", lambda s, c, a="update": (False, "no such image"))
    ok, msg = jobs.start("slop", tmp_path, tmp_path, "alice")
    assert not ok and "no such image" in msg
    assert not jobs._lock.locked(), "a failed hand-off left updates locked out"


# ---- nothing from a caller reaches the helper's command line ----------------
def test_the_scripts_are_constants():
    """This service is safe to hand a Docker socket only because no part of what
    it runs comes from a request. A helper command built by interpolation would
    undo that in one line."""
    for script in [detached.SCRIPT_UPDATE, *detached.SCRIPT_ACTIONS.values()]:
        for forbidden in ("{", "}", "%s", "$(", "`"):
            assert forbidden not in script, f"{script!r} is not a literal"


def test_the_helper_is_launched_with_a_fixed_argv(monkeypatch, tmp_path):
    seen = []

    def _fake(*args, timeout=20):
        seen.append(list(args))
        class R:
            returncode, stdout, stderr = 0, "cid\n", ""
        return R()

    monkeypatch.setattr(detached, "_docker", _fake)
    monkeypatch.setattr(detached, "_own_image", lambda: "sysible-slop-updater")
    ok, _ = detached.spawn(detached.SCRIPT_UPDATE, tmp_path)
    assert ok
    assert seen[0][:2] == ["rm", "-f"], "a finished helper from last time is left behind"
    argv = seen[1]
    assert argv[0] == "run" and "-d" in argv
    assert argv[argv.index("--name") + 1] == detached.HELPER
    assert "/var/run/docker.sock:/var/run/docker.sock" in argv
    assert f"{tmp_path}:{tmp_path}" in argv, \
        "the checkout is not mounted at the path the host knows it by"
    assert argv[argv.index("-w") + 1] == str(tmp_path)
    assert argv[-4:] == ["sysible-slop-updater", "sh", "-c", detached.SCRIPT_UPDATE]


def test_the_helper_is_not_given_a_restart_policy(monkeypatch, tmp_path):
    """It is a one-shot. `unless-stopped` would re-run the entire update every
    time the daemon came back."""
    seen = []
    monkeypatch.setattr(detached, "_docker",
                        lambda *a, timeout=20: seen.append(list(a)) or type(
                            "R", (), {"returncode": 0, "stdout": "x", "stderr": ""})())
    monkeypatch.setattr(detached, "_own_image", lambda: "img")
    detached.spawn(detached.SCRIPT_UPDATE, tmp_path)
    argv = seen[1]
    assert argv[argv.index("--restart") + 1] == "no"


# ---- and the result survives the restart it caused --------------------------
def _inspect(running, code, started="2026-09-28T10:00:00.000000000Z",
             finished="2026-09-28T10:01:00.000000000Z", action="update"):
    return {"running": running, "exit_code": code, "action": action,
            "started_at": started, "finished_at": finished}


def test_a_finished_update_is_reported_after_we_are_restarted(monkeypatch):
    """There is no in-memory job left — this process is new. Without asking the
    helper, the console loses every self-update at the moment it succeeds."""
    monkeypatch.setattr(detached, "_inspect", lambda: _inspect(False, 0,
                        finished=time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime())))
    monkeypatch.setattr(detached, "logs", lambda tail=600: ["Container sysible-slop-gateway Started"])
    jobs._job = None
    j = jobs.current()
    assert j and j["state"] == "succeeded" and j["app"] == "slop"
    assert j["detached"] is True
    assert any("gateway" in line for line in j["log"]), "the helper's log was lost"


def test_a_failed_update_is_reported_as_failed(monkeypatch):
    monkeypatch.setattr(detached, "_inspect", lambda: _inspect(False, 1,
                        finished=time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime())))
    monkeypatch.setattr(detached, "logs", lambda tail=600: ["failed to build"])
    jobs._job = None
    j = jobs.current()
    assert j["state"] == "failed" and "exited 1" in j["message"]


def test_a_running_helper_reads_as_running(monkeypatch):
    monkeypatch.setattr(detached, "_inspect", lambda: _inspect(True, 0, finished="0001-01-01T00:00:00Z"))
    monkeypatch.setattr(detached, "logs", lambda tail=600: ["#1 building"])
    jobs._job = None
    j = jobs.current()
    assert j["state"] == "running" and j["finished"] is None
    assert j["self_update"] is True, "the console needs to expect the dropped connection"


def test_last_weeks_update_is_not_todays_news(monkeypatch):
    """A helper container sticks around so its log can be read. Reporting it
    forever would put an old update on the console of someone who just signed in."""
    old = time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z",
                        time.gmtime(time.time() - detached.RESULT_TTL - 60))
    monkeypatch.setattr(detached, "_inspect", lambda: _inspect(False, 0, finished=old))
    monkeypatch.setattr(detached, "logs", lambda tail=600: ["old"])
    jobs._job = None
    assert jobs.current() is None


def test_no_helper_at_all_is_simply_no_job(monkeypatch):
    monkeypatch.setattr(detached, "_inspect", lambda: None)
    jobs._job = None
    assert jobs.current() is None


def test_the_helper_is_not_inspected_on_every_poll(monkeypatch):
    """/api/status is polled by every open Administration page, and this costs two
    subprocesses against the Docker daemon."""
    n = {"i": 0}

    def _counted():
        n["i"] += 1
        return _inspect(True, 0, finished="0001-01-01T00:00:00Z")

    monkeypatch.setattr(detached, "_inspect", _counted)
    monkeypatch.setattr(detached, "logs", lambda tail=600: [])
    jobs._job = None
    for _ in range(20):
        jobs.current()
    assert n["i"] <= 1, f"{n['i']} docker inspects for 20 polls"


# ---- the claim itself, against a real daemon --------------------------------
def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True,
                              timeout=30).returncode == 0
    except Exception:
        return False


CLI_IMAGE = "docker:27-cli"


def _project(tmp_path):
    """A two-service stand-in for the SLOP stack: one bystander, and one service
    that can drive compose against its own project."""
    if subprocess.run(["docker", "image", "inspect", CLI_IMAGE],
                      capture_output=True).returncode != 0:
        if subprocess.run(["docker", "pull", "-q", CLI_IMAGE],
                          capture_output=True, timeout=300).returncode != 0:
            pytest.skip(f"cannot obtain {CLI_IMAGE}")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "docker-compose.yml").write_text(f"""services:
  victim:
    image: {CLI_IMAGE}
    container_name: selftest-victim
    command: sleep 600
  runner:
    image: {CLI_IMAGE}
    container_name: selftest-runner
    command: sleep 600
    working_dir: {proj}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - {proj}:{proj}
""")

    def dc(*args, **kw):
        return subprocess.run(["docker", "compose", "-p", "selftest", *args],
                              cwd=str(proj), capture_output=True, text=True,
                              timeout=300, **kw)

    def names():
        p = subprocess.run(["docker", "ps", "--filter",
                            "label=com.docker.compose.project=selftest",
                            "--format", "{{.Names}} {{.ID}}"],
                           capture_output=True, text=True, timeout=60)
        return dict(l.split() for l in p.stdout.split("\n") if l.strip())

    return proj, dc, names


@pytest.mark.skipif(not _docker_ready(), reason="no usable docker daemon")
def test_running_it_in_process_really_does_wreck_the_stack(tmp_path):
    """The measurement in the docstring, kept runnable — otherwise the fix above
    is a cost with nothing on the other side of it. Same project, recreated by its
    OWN service: every container is killed, and the replacements are created but
    never started."""
    proj, dc, names = _project(tmp_path)
    try:
        assert dc("up", "-d").returncode == 0
        before = names()
        assert set(before) == {"selftest-victim", "selftest-runner"}

        subprocess.run(["docker", "exec", "selftest-runner", "sh", "-c",
                        "docker compose -p selftest up -d --force-recreate"],
                       capture_output=True, text=True, timeout=180)
        time.sleep(3)
        after = names()
        assert "selftest-runner" not in after and "selftest-victim" not in after, (
            f"expected the in-process run to take the stack down: {after}")

        all_p = subprocess.run(["docker", "ps", "-a", "--filter",
                                "label=com.docker.compose.project=selftest",
                                "--format", "{{.Names}}\t{{.Status}}"],
                               capture_output=True, text=True, timeout=60).stdout
        assert "Exited (137)" in all_p, f"nothing was killed, so the premise is wrong:\n{all_p}"
        # The replacements exist and were never started. compose builds each one
        # under a temporary <oldid>_<name> and renames it over the original only
        # once the old one is gone — so being killed here leaves BOTH, and the
        # stack down, with no one left running to finish the swap.
        assert "_selftest-victim" in all_p or "_selftest-runner" in all_p, (
            f"expected half-made replacements left behind:\n{all_p}")
        assert "Created" in all_p, f"the replacements were started after all:\n{all_p}"
    finally:
        dc("down", "--remove-orphans")
        subprocess.run(["docker", "rm", "-f", "selftest-helper"], capture_output=True)


@pytest.mark.skipif(not _docker_ready(), reason="no usable docker daemon")
def test_a_detached_helper_finishes_after_its_parent_is_destroyed(tmp_path):
    """The claim, end to end: a stack whose own service asks for it to be
    recreated ends up recreated and RUNNING, rather than killed half way."""
    proj, dc, names = _project(tmp_path)
    try:
        assert dc("up", "-d").returncode == 0
        before = names()
        assert set(before) == {"selftest-victim", "selftest-runner"}, before

        # The real thing, with the real script shape: a helper started by
        # `docker run` from inside the project, then the project is recreated.
        subprocess.run(["docker", "rm", "-f", "selftest-helper"], capture_output=True)
        spawn = subprocess.run(
            ["docker", "exec", "selftest-runner", "docker", "run", "-d",
             "--name", "selftest-helper", "--restart", "no",
             "-v", "/var/run/docker.sock:/var/run/docker.sock",
             "-v", f"{proj}:{proj}", "-w", str(proj), CLI_IMAGE,
             "sh", "-c", "exec docker compose -p selftest up -d --force-recreate"],
            capture_output=True, text=True, timeout=120)
        assert spawn.returncode == 0, spawn.stderr

        deadline = time.time() + 180
        state = None
        while time.time() < deadline:
            p = subprocess.run(["docker", "inspect", "-f",
                                "{{.State.Running}} {{.State.ExitCode}}", "selftest-helper"],
                               capture_output=True, text=True, timeout=30)
            state = p.stdout.strip()
            if state.startswith("false"):
                break
            time.sleep(1)

        log = subprocess.run(["docker", "logs", "selftest-helper"],
                             capture_output=True, text=True, timeout=60)
        assert state == "false 0", (
            f"the helper did not finish cleanly: {state!r}\n{log.stdout}{log.stderr}")

        after = names()
        assert set(after) == {"selftest-victim", "selftest-runner"}, (
            f"the stack is not running after its own recreation: {after}\n"
            f"{log.stdout}{log.stderr}")
        assert after["selftest-runner"] != before["selftest-runner"], \
            "the container that asked for the recreation was never actually recreated"
        assert after["selftest-victim"] != before["selftest-victim"]
    finally:
        subprocess.run(["docker", "rm", "-f", "selftest-helper"], capture_output=True)
        dc("down", "--remove-orphans")


def test_a_restart_does_not_come_back_calling_itself_an_update(monkeypatch):
    """The console titles the panel from the job. After the hand-off there is no
    in-memory job left to remember which button was pressed, so the helper carries
    it."""
    monkeypatch.setattr(detached, "_inspect", lambda: _inspect(
        False, 0, action="restart",
        finished=time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime())))
    monkeypatch.setattr(detached, "logs", lambda tail=600: [])
    jobs._job = None
    j = jobs.current()
    assert j["action"] == "restart"
    assert "restart" in j["message"]


def test_an_update_carries_no_action_so_the_panel_says_update(monkeypatch):
    monkeypatch.setattr(detached, "_inspect", lambda: _inspect(
        False, 0, finished=time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime())))
    monkeypatch.setattr(detached, "logs", lambda tail=600: [])
    jobs._job = None
    assert jobs.current()["action"] is None


def test_a_helper_with_no_action_label_is_not_mistaken_for_no_helper(monkeypatch):
    """docker renders a missing label as an EMPTY trailing field. Stripping the
    whole line eats the tab with it, the field count comes up short, and the
    result of the update being reported on is thrown away."""
    class R:
        returncode = 0
        stdout = "false\t0\t2026-09-28T10:00:00Z\t2026-09-28T10:00:05Z\t"
        stderr = ""
    monkeypatch.setattr(detached, "_docker", lambda *a, timeout=20: R())
    info = detached._inspect()
    assert info is not None, "a helper with no action label read as no helper at all"
    assert info["action"] == "update"


def test_a_pull_the_helper_did_is_not_remembered_as_not_having_happened(monkeypatch):
    """The `git pull` happened in another container, so this process's memo of the
    remote tip still describes the world before it."""
    from backend import apps, git as gitmod
    forgot = []
    monkeypatch.setattr(gitmod, "forget_remote", lambda root: forgot.append(root))
    monkeypatch.setattr(apps, "checkout_dir", lambda key: Path("/opt/slop"))
    monkeypatch.setattr(detached, "_inspect", lambda: _inspect(
        False, 0, finished=time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime())))
    monkeypatch.setattr(detached, "logs", lambda tail=600: [])
    jobs._forgotten.clear()
    jobs._job = None
    jobs.current()
    assert forgot == [Path("/opt/slop")], "the console can still report the OLD tip"
    detached._invalidate()
    jobs.current()
    assert len(forgot) == 1, "the memo is dropped on every poll, not once"
