"""An update that skips three of the apps must not report success.

Reported, after several rounds of fixes that were all pushed and none of which
appeared: "I don't see any difference." The Software & services page said
"Up to date at bd1d0d5" -- the commit that had just been pushed -- while the
Controller in the next tab was plainly the old build.

Both were telling the truth about different things.

On an integrated host the Controller, SLEP and Connect are services of SLOP's
compose file, declared `profiles: ["integrated"]` so they stay out of a split
host's stack. A compose command with no profile does not merely decline to
START such a service: it does not BUILD it and does not RECREATE it, and it
reports nothing about having left it out. The updater runs

    docker compose up -d --build

from a literal argv, on purpose -- nothing in it may come from a request. With
no profile that pulled the new code, rebuilt the platform's six unprofiled
services, left three apps running the image they already had, and returned
success. The page then read the checkout's commit and called the platform up
to date, which it was.

`tools/migrate-to-integrated` passes --profile on its own command lines, so
the stack came UP correctly; what nothing did was leave anything behind that
made LATER commands carry it. Compose reads COMPOSE_PROFILES from the project
.env, so that is where it goes -- once, where every command picks it up.

Run: pytest tests/
"""
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "updater"))
from backend import wiring                                    # noqa: E402

COMPOSE = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
INSTALL = (REPO / "install.sh").read_text(encoding="utf-8")
MIGRATE = (REPO / "tools" / "migrate-to-integrated").read_text(encoding="utf-8")


def _profiled() -> set:
    return {name for name, svc in COMPOSE["services"].items()
            if "integrated" in (svc.get("profiles") or [])}


def test_this_test_is_looking_at_something():
    """If the apps stop being profile-gated, everything below is vacuous and
    this says so rather than passing."""
    assert _profiled() == {"controller", "slep", "connect"}, (
        f"the integrated profile now gates {sorted(_profiled())}; this test "
        f"was written for the three vendored apps")


# ---- what has to be written down -------------------------------------------
def test_a_fresh_integrated_install_writes_the_profile():
    assert re.search(r"_upsert_kv \"\$ENV_FILE\" COMPOSE_PROFILES integrated", INSTALL), (
        "install.sh sets SYSIBLE_INTEGRATED but not COMPOSE_PROFILES, so every "
        "later compose command skips the three apps")


def test_the_migration_writes_the_profile_too():
    assert "COMPOSE_PROFILES=integrated" in MIGRATE, (
        "migrate-to-integrated passes --profile on its own lines but leaves "
        "nothing behind for the updater")


# ---- and repaired on hosts that predate it ---------------------------------
def test_an_integrated_host_without_the_profile_gets_it():
    assert wiring.plan_profile({"SYSIBLE_INTEGRATED": "1"}) == \
        {"COMPOSE_PROFILES": "integrated"}


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", " 1 "])
def test_every_spelling_of_yes_counts(value):
    assert wiring.plan_profile({"SYSIBLE_INTEGRATED": value})


def test_a_split_host_is_left_alone():
    """There the three ARE separate compose projects. Turning the profile on
    would start a second copy of each inside SLOP's project."""
    assert wiring.plan_profile({}) == {}
    assert wiring.plan_profile({"SYSIBLE_INTEGRATED": "0"}) == {}


def test_a_host_that_already_has_it_is_not_rewritten():
    assert wiring.plan_profile(
        {"SYSIBLE_INTEGRATED": "1", "COMPOSE_PROFILES": "integrated"}) == {}


def test_another_profile_the_operator_set_is_kept():
    """Only ever adds. Replacing the value would switch off whatever they had
    turned on, on a machine of theirs, during an update."""
    got = wiring.plan_profile(
        {"SYSIBLE_INTEGRATED": "1", "COMPOSE_PROFILES": "debug"})
    assert got == {"COMPOSE_PROFILES": "debug,integrated"}


def test_it_is_written_to_the_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SYSIBLE_INTEGRATED=1\nSYSIBLE_SLOP_DIR=/opt/sysible\n")
    monkeypatch.setattr(wiring.apps, "compose_dir", lambda root: tmp_path)
    changes, notes = wiring.repair_profile(tmp_path)
    assert changes == {"COMPOSE_PROFILES": "integrated"}
    assert "COMPOSE_PROFILES=integrated" in env.read_text()
    assert any("silently skipped" in n or "skipped" in n for n in notes), notes


def test_repairing_twice_changes_nothing_the_second_time(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SYSIBLE_INTEGRATED=1\n")
    monkeypatch.setattr(wiring.apps, "compose_dir", lambda root: tmp_path)
    wiring.repair_profile(tmp_path)
    before = env.read_text()
    assert wiring.repair_profile(tmp_path) == ({}, [])
    assert env.read_text() == before


# ---- and it happens BEFORE the build ---------------------------------------
def test_the_repair_runs_before_the_rebuild():
    """After the build it would be a note about next time."""
    jobs = (REPO / "updater" / "backend" / "jobs.py").read_text(encoding="utf-8")
    repair = jobs.index("wiring.repair_profile")
    build = jobs.index('"docker", "compose", "up", "-d", "--build"')
    assert repair < build, "the profile is repaired after the build it was needed for"
