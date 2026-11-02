"""The Updates page has to repair the Controller's wiring, because sysiblectl
cannot do it for the people who use the page.

REPORTED. A screenshot of Sysible Baseline with sixteen hosts all failing:
"SYSIBLE_BASELINE_URL and SYSIBLE_BASELINE_AGENT_TOKEN unset". Those two
settings live in the CONTROLLER's compose .env, they are written by install.sh
and by nothing else, and an update is a `git pull` plus a rebuild — so every
platform that was updated rather than reinstalled after Baseline shipped has a
Controller that cannot reach it at all.

`sysiblectl slop update` repairs it now. This console does NOT run sysiblectl —
jobs.py runs git and docker compose itself — so the fix reached only the
operators who were already at a shell on the host, and the path the console
offers stayed the one that never heals. That is the gap these tests close.

The three paths that have to do it, because they are three different pieces of
code: `_update_one` (any product but SLOP), `_queue_worker`'s SELF_AFFECTING
branch (Update everything), and `start`'s (the Update button next to SLOP).
The last one is the one an operator presses, and it is the one with no job to
log into — so it is also the one most likely to be left out.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import apps, jobs, wiring  # noqa: E402

TOKEN = "b" * 64
FBTOKEN = "f" * 64


# ---------------------------------------------------------------------------
# the decision, with no files and no docker anywhere near it
# ---------------------------------------------------------------------------
def test_an_unwired_controller_gets_both_services():
    slop = {"SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN,
            "SYSIBLE_FLASHBACK_AGENT_TOKEN": FBTOKEN}
    changes, notes = wiring.plan(slop, {})
    assert changes == {
        "SYSIBLE_BASELINE_URL": "http://host.docker.internal:8771",
        "SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN,
        "SYSIBLE_FLASHBACK_URL": "http://host.docker.internal:8770",
        "SYSIBLE_FLASHBACK_AGENT_TOKEN": FBTOKEN,
    }, changes
    assert notes, "a repair that says nothing is a repair nobody can audit"


def test_a_wired_controller_is_left_completely_alone():
    """This runs on EVERY update. Rewriting a correct file would recreate the
    Controller every time anyone pressed Update."""
    slop = {"SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN,
            "SYSIBLE_FLASHBACK_AGENT_TOKEN": FBTOKEN}
    ctl = {"SYSIBLE_BASELINE_URL": "http://host.docker.internal:8771",
           "SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN,
           "SYSIBLE_FLASHBACK_URL": "http://host.docker.internal:8770",
           "SYSIBLE_FLASHBACK_AGENT_TOKEN": FBTOKEN}
    changes, _notes = wiring.plan(slop, ctl)
    assert changes == {}, changes


def test_the_two_services_do_not_get_each_others_ports():
    """8770 is Flashback, 8771 is Baseline. Crossing them is the bug that put
    config snapshots on the config manager's endpoint."""
    changes, _ = wiring.plan({"SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN,
                              "SYSIBLE_FLASHBACK_AGENT_TOKEN": FBTOKEN}, {})
    assert changes["SYSIBLE_BASELINE_URL"].endswith(":8771")
    assert changes["SYSIBLE_FLASHBACK_URL"].endswith(":8770")
    assert changes["SYSIBLE_BASELINE_AGENT_TOKEN"] != changes["SYSIBLE_FLASHBACK_AGENT_TOKEN"]


def test_a_baseline_url_pointing_at_flashback_is_corrected():
    """The installer bug: it wrote FLASHBACK's url and token under BASELINE's
    names, so the keys are PRESENT and point at the wrong service. Seeding alone
    would leave that in place for ever."""
    slop = {"SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN,
            "SYSIBLE_FLASHBACK_AGENT_TOKEN": FBTOKEN}
    ctl = {"SYSIBLE_BASELINE_URL": "http://host.docker.internal:8770",   # Flashback's
           "SYSIBLE_BASELINE_AGENT_TOKEN": FBTOKEN,                      # Flashback's
           "SYSIBLE_FLASHBACK_URL": "http://host.docker.internal:8770",
           "SYSIBLE_FLASHBACK_AGENT_TOKEN": FBTOKEN}
    changes, notes = wiring.plan(slop, ctl)
    assert changes["SYSIBLE_BASELINE_URL"] == "http://host.docker.internal:8771"
    assert changes["SYSIBLE_BASELINE_AGENT_TOKEN"] == TOKEN
    assert "SYSIBLE_FLASHBACK_URL" not in changes, changes
    assert any("WRONG address" in n for n in notes), notes


def test_the_real_shape_of_a_pre_baseline_install_is_fully_repaired():
    """What the installer actually left behind before its wiring was fixed: it
    wrote FLASHBACK's url and token under BASELINE's names, and wrote nothing at
    all under Flashback's own. All four keys are wrong or missing at once."""
    slop = {"SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN,
            "SYSIBLE_FLASHBACK_AGENT_TOKEN": FBTOKEN}
    ctl = {"SYSIBLE_BASELINE_URL": "http://host.docker.internal:8770",
           "SYSIBLE_BASELINE_AGENT_TOKEN": FBTOKEN}
    changes, _ = wiring.plan(slop, ctl)
    assert changes == {
        "SYSIBLE_BASELINE_URL": "http://host.docker.internal:8771",
        "SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN,
        "SYSIBLE_FLASHBACK_URL": "http://host.docker.internal:8770",
        "SYSIBLE_FLASHBACK_AGENT_TOKEN": FBTOKEN,
    }, changes


def test_the_fingerprint_still_works_with_a_pinned_flashback_url():
    """A platform that reaches Flashback at a non-default address still has to
    have Baseline's copy of THAT address recognised as the wrong service."""
    slop = {"SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN,
            "SYSIBLE_FLASHBACK_AGENT_TOKEN": FBTOKEN,
            "SYSIBLE_FLASHBACK_URL": "http://10.9.9.9:8770"}
    ctl = {"SYSIBLE_BASELINE_URL": "http://10.9.9.9:8770",       # Flashback's
           "SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN,
           "SYSIBLE_FLASHBACK_URL": "http://10.9.9.9:8770",
           "SYSIBLE_FLASHBACK_AGENT_TOKEN": FBTOKEN}
    changes, _ = wiring.plan(slop, ctl)
    assert changes == {"SYSIBLE_BASELINE_URL": "http://host.docker.internal:8771"}, changes


def test_an_address_the_operator_chose_is_not_overwritten():
    """A split host, or a reverse proxy. Not our business."""
    slop = {"SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN}
    ctl = {"SYSIBLE_BASELINE_URL": "https://baseline.internal.example:443",
           "SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN}
    changes, _ = wiring.plan(slop, ctl)
    assert changes == {}, changes


def test_a_stale_token_is_corrected_even_at_a_custom_address():
    """The token is not a preference — a stale one fails the compare exactly
    like a missing one, and SLOP's .env is where the other end reads it from."""
    slop = {"SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN}
    ctl = {"SYSIBLE_BASELINE_URL": "https://baseline.internal.example:443",
           "SYSIBLE_BASELINE_AGENT_TOKEN": "c" * 64}
    changes, _ = wiring.plan(slop, ctl)
    assert changes == {"SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN}, changes


def test_a_pinned_port_in_slops_env_is_honoured():
    changes, _ = wiring.plan({"SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN,
                              "SYSIBLE_BASELINE_AGENT_PORT": "9771"}, {})
    assert changes["SYSIBLE_BASELINE_URL"] == "http://host.docker.internal:9771"


def test_a_url_pinned_in_slops_env_wins_over_the_port():
    changes, _ = wiring.plan({"SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN,
                              "SYSIBLE_BASELINE_AGENT_PORT": "9771",
                              "SYSIBLE_BASELINE_URL": "http://10.1.2.3:8771"}, {})
    assert changes["SYSIBLE_BASELINE_URL"] == "http://10.1.2.3:8771"


def test_no_token_in_slops_env_says_so_and_invents_nothing():
    """The token is shared with a service that reads it from the same file, so
    there is nothing to make up — and silence here is an Apply that fails with
    no explanation anywhere."""
    changes, notes = wiring.plan({}, {})
    assert changes == {}, changes
    assert any("SYSIBLE_BASELINE_AGENT_TOKEN" in n and "install.sh" in n for n in notes), notes
    assert any("SYSIBLE_FLASHBACK_AGENT_TOKEN" in n for n in notes), notes


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------
def test_the_rest_of_the_env_file_survives(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# the operator's own notes\n"
                   "SYSIBLE_ADMIN_EMAIL=ops@example.com\n"
                   "\n"
                   "SYSIBLE_BASELINE_URL=http://host.docker.internal:8770\n"
                   "SYSIBLE_TLS_CERT=/etc/ssl/own.pem\n", encoding="utf-8")
    wiring.upsert(env, {"SYSIBLE_BASELINE_URL": "http://host.docker.internal:8771",
                        "SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN})
    out = env.read_text(encoding="utf-8")
    assert "# the operator's own notes" in out
    assert "SYSIBLE_ADMIN_EMAIL=ops@example.com" in out
    assert "SYSIBLE_TLS_CERT=/etc/ssl/own.pem" in out
    back = wiring.read_env(env)
    assert back["SYSIBLE_BASELINE_URL"] == "http://host.docker.internal:8771"
    assert back["SYSIBLE_BASELINE_AGENT_TOKEN"] == TOKEN
    # Rewritten in place, not appended on top of the old line.
    assert out.count("SYSIBLE_BASELINE_URL=") == 1, out


def test_a_duplicated_key_ends_up_saying_one_thing(tmp_path):
    """Compose takes the LAST assignment. A repair that wrote the first, or
    appended a third, would leave the file disagreeing with the thing reading
    it — which is how this whole class of bug is invisible."""
    env = tmp_path / ".env"
    env.write_text("SYSIBLE_BASELINE_AGENT_TOKEN=old-one\n"
                   "SYSIBLE_OTHER=keep\n"
                   "SYSIBLE_BASELINE_AGENT_TOKEN=old-two\n", encoding="utf-8")
    assert wiring.read_env(env)["SYSIBLE_BASELINE_AGENT_TOKEN"] == "old-two"
    wiring.upsert(env, {"SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN})
    out = env.read_text(encoding="utf-8")
    assert out.count("SYSIBLE_BASELINE_AGENT_TOKEN=") == 1, out
    assert wiring.read_env(env)["SYSIBLE_BASELINE_AGENT_TOKEN"] == TOKEN
    assert "SYSIBLE_OTHER=keep" in out


def test_a_hand_quoted_value_that_is_already_right_changes_nothing(tmp_path):
    """This runs on EVERY update, and whether anything is rewritten is decided
    by how the file is read. A correct value in quotes reading as different
    would recreate the Controller every time anyone pressed Update."""
    env = tmp_path / ".env"
    env.write_text(f'SYSIBLE_BASELINE_URL="http://host.docker.internal:8771"\n'
                   f"SYSIBLE_BASELINE_AGENT_TOKEN='{TOKEN}'\n", encoding="utf-8")
    assert wiring.read_env(env)["SYSIBLE_BASELINE_URL"] == "http://host.docker.internal:8771"
    changes, _ = wiring.plan({"SYSIBLE_BASELINE_AGENT_TOKEN": TOKEN},
                             wiring.read_env(env))
    assert changes == {}, changes


def test_a_commented_out_key_is_not_mistaken_for_one(tmp_path):
    env = tmp_path / ".env"
    env.write_text("#SYSIBLE_BASELINE_URL=http://disabled/\n", encoding="utf-8")
    assert wiring.read_env(env) == {}
    wiring.upsert(env, {"SYSIBLE_BASELINE_URL": "http://host.docker.internal:8771"})
    out = env.read_text(encoding="utf-8")
    assert "#SYSIBLE_BASELINE_URL=http://disabled/" in out, out
    assert wiring.read_env(env)["SYSIBLE_BASELINE_URL"].endswith(":8771")


# ---------------------------------------------------------------------------
# end to end, on a fake pair of checkouts
# ---------------------------------------------------------------------------
@pytest.fixture
def host(tmp_path, monkeypatch):
    """A SLOP checkout and a Controller checkout, laid out as install.sh does."""
    src = tmp_path / "src"
    slop = src / "sysible-linux-operations-platform"
    ctl = src / "sysible-controller"
    for d in (slop, ctl):
        (d / ".git").mkdir(parents=True)
        (d / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (slop / ".env").write_text(
        f"SYSIBLE_BASELINE_AGENT_TOKEN={TOKEN}\n"
        f"SYSIBLE_FLASHBACK_AGENT_TOKEN={FBTOKEN}\n", encoding="utf-8")
    (ctl / ".env").write_text("SYSIBLE_ADMIN_EMAIL=ops@example.com\n", encoding="utf-8")
    monkeypatch.setattr(apps, "SRC_DIR", src)
    for var in ("SYSIBLE_SLOP_DIR", "SYSIBLE_CONTROLLER_DIR", "SYSIBLE_SRC_DIR"):
        monkeypatch.delenv(var, raising=False)
    return {"src": src, "slop": slop, "ctl": ctl, "env": ctl / ".env"}


def test_repair_writes_the_controllers_env(host):
    changes, notes = wiring.repair(host["slop"])
    assert set(changes) == {"SYSIBLE_BASELINE_URL", "SYSIBLE_BASELINE_AGENT_TOKEN",
                            "SYSIBLE_FLASHBACK_URL", "SYSIBLE_FLASHBACK_AGENT_TOKEN"}
    back = wiring.read_env(host["env"])
    assert back["SYSIBLE_BASELINE_AGENT_TOKEN"] == TOKEN
    assert back["SYSIBLE_ADMIN_EMAIL"] == "ops@example.com"
    assert any(str(host["env"]) in n for n in notes), notes
    # Idempotent: the second update must change nothing.
    again, _ = wiring.repair(host["slop"])
    assert again == {}, again


def test_repair_finds_slop_on_its_own(host):
    """`_update_one` has SLOP's root in hand; the controller path does not."""
    changes, _ = wiring.repair(None)
    assert "SYSIBLE_BASELINE_AGENT_TOKEN" in changes, changes


def test_a_host_without_the_controller_is_not_an_error(host, monkeypatch):
    """SLOP on its own is a supported install, and must not log a failure on
    every single update."""
    import shutil
    shutil.rmtree(host["ctl"])
    changes, notes = wiring.repair(host["slop"])
    assert changes == {} and notes == [], (changes, notes)


# ---------------------------------------------------------------------------
# …and that the job runner actually calls it. THREE paths.
# ---------------------------------------------------------------------------
@pytest.fixture
def spy(monkeypatch):
    """Record the repair instead of doing it, and keep docker out of this."""
    calls = []
    monkeypatch.setattr(jobs.wiring, "repair",
                        lambda root=None: (calls.append(("repair", root)),
                                           ({"SYSIBLE_BASELINE_URL": "x"}, ["+ did it"]))[1])
    monkeypatch.setattr(jobs.wiring, "recreate_controller",
                        lambda *a, **k: (calls.append(("recreate", None)), (True, "recreated"))[1])
    return calls


def test_update_one_repairs_before_it_pulls(monkeypatch, spy, tmp_path):
    steps = []
    monkeypatch.setattr(jobs, "_run", lambda argv, cwd: (steps.append(argv[0]), 0)[1])
    monkeypatch.setattr(jobs.git, "forget_remote", lambda root: None)
    monkeypatch.setattr(jobs, "_rev", lambda root: "abc1234")
    ok, _msg = jobs._update_one("controller", tmp_path, tmp_path)
    assert ok
    assert [c[0] for c in spy] == ["repair"], spy
    # BEFORE the pull — the values cannot change in it, and the rebuild that
    # follows is what picks them up.
    assert steps and steps[0] == "git", steps


def test_a_controller_update_does_not_recreate_it_twice(monkeypatch, spy, tmp_path):
    """The update is about to recreate the Controller anyway. Two recreates for
    one change is two outages."""
    monkeypatch.setattr(jobs, "_run", lambda argv, cwd: 0)
    monkeypatch.setattr(jobs.git, "forget_remote", lambda root: None)
    monkeypatch.setattr(jobs, "_rev", lambda root: "abc1234")
    jobs._update_one("controller", tmp_path, tmp_path)
    assert [c[0] for c in spy] == ["repair"], spy


def test_a_slop_update_does_recreate_the_controller(monkeypatch, spy, tmp_path):
    """A different compose project: nothing in SLOP's update would touch it."""
    monkeypatch.setattr(jobs, "_run", lambda argv, cwd: 0)
    monkeypatch.setattr(jobs.git, "forget_remote", lambda root: None)
    monkeypatch.setattr(jobs, "_rev", lambda root: "abc1234")
    jobs._update_one("slop", tmp_path, tmp_path)
    assert [c[0] for c in spy] == ["repair", "recreate"], spy


def test_slep_is_not_touched(monkeypatch, spy, tmp_path):
    monkeypatch.setattr(jobs, "_run", lambda argv, cwd: 0)
    monkeypatch.setattr(jobs.git, "forget_remote", lambda root: None)
    monkeypatch.setattr(jobs, "_rev", lambda root: "abc1234")
    jobs._update_one("slep", tmp_path, tmp_path)
    assert spy == [], spy


def test_the_slop_update_button_repairs_it_before_handing_off(monkeypatch, spy, tmp_path):
    """THE PATH AN OPERATOR PRESSES. `start` hands SLOP straight to the detached
    helper — which only has SLOP's own checkout mounted and cannot see the
    Controller's .env — so if the repair does not happen here it never happens
    at all for anybody using the console."""
    monkeypatch.setattr(jobs.detached, "running", lambda: False)
    monkeypatch.setattr(jobs.detached, "spawn",
                        lambda script, cwd, action="update": (spy.append(("spawn", cwd)),
                                                              (True, "off"))[1])
    monkeypatch.setattr(jobs.apps, "checkout_dir", lambda key: tmp_path)
    started, message = jobs.start("slop", tmp_path, tmp_path, "alice")
    assert started, message
    # BEFORE the spawn, in that order. The helper runs with only SLOP's own
    # checkout bind-mounted (detached.spawn passes `-v {cwd}:{cwd}` and no
    # SYSIBLE_SRC_DIR mount), so once we have handed off, the Controller's .env
    # is out of reach of everything still running.
    assert [c[0] for c in spy] == ["repair", "recreate", "spawn"], spy
    # …and the operator is told, because there is no job log on this path.
    assert "wiring" in message.lower(), message
    assert "SYSIBLE_BASELINE_URL" in message, message


def test_update_everything_repairs_it_too(monkeypatch, spy, tmp_path):
    monkeypatch.setattr(jobs.detached, "running", lambda: False)
    monkeypatch.setattr(jobs.detached, "spawn",
                        lambda script, cwd, action="update": (True, "off"))
    monkeypatch.setattr(jobs, "_run", lambda argv, cwd: 0)
    monkeypatch.setattr(jobs.git, "forget_remote", lambda root: None)
    monkeypatch.setattr(jobs, "_rev", lambda root: "abc1234")
    started, _m = jobs.start_many([("slop", tmp_path, tmp_path)], "alice")
    assert started
    for _ in range(200):
        if spy:
            break
        import time
        time.sleep(0.02)
    assert [c[0] for c in spy] == ["repair", "recreate"], spy


def test_a_failed_repair_does_not_stop_the_update(monkeypatch, tmp_path):
    """This is a repair of something that was ALREADY broken. Refusing to update
    because of it would turn a degraded platform into a stuck one."""
    def boom(root=None):
        raise OSError("read-only file system")
    monkeypatch.setattr(jobs.wiring, "repair", boom)
    monkeypatch.setattr(jobs, "_run", lambda argv, cwd: 0)
    monkeypatch.setattr(jobs.git, "forget_remote", lambda root: None)
    monkeypatch.setattr(jobs, "_rev", lambda root: "abc1234")
    ok, _msg = jobs._update_one("slop", tmp_path, tmp_path)
    assert ok, "the update was abandoned over a repair that is not its job"
