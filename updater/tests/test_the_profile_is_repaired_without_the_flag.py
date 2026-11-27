"""An update has to write the integrated profile on a host that has no flag.

REPORTED, after two updates: "Fix the compose profile issue in a slop update.
I have updated twice today and it's not registering as fixed."

The Administration page showed `Services (9) - 3 not running` with the Controller,
SLEP and Connect each saying "No container - docker compose ps does not list the
controller service". That is COMPOSE_PROFILES missing: those three are declared
`profiles: ["integrated"]`, and a compose command with no profile does not merely
decline to start them, it does not mention them at all.

repair_profile exists for precisely this and jobs.py calls it before an update
(jobs.py:263) and before restart/start/recreate (:578). It ran both times and did
nothing, because it was gated on integrated_env() -- the SYSIBLE_INTEGRATED key
in SLOP's .env, which `install.sh --integrated` writes and the SERVER INSTALLER
does not. apps.row_keys() already documents that exact host: "this host was
installed with the server installer, so the flag is off".

So the repair was gated on the one key that is missing on the hosts that need it.

The fix reads the LAYOUT as well as the flag, using the signal is_vendored()
already relies on: apps/<key> present AND no separate checkout of it. A pull can
create apps/; it cannot delete /opt/sysible-src/sysible-controller. These tests
pin both halves -- that it now fires without the flag, and that it still cannot
be fooled by a pull into converting a split host.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import apps, wiring  # noqa: E402


# ---- the decision, with no files anywhere near it -------------------------

def test_the_flag_alone_still_works():
    """Unchanged behaviour: a host that HAS the flag is repaired as before."""
    assert wiring.plan_profile({"SYSIBLE_INTEGRATED": "1"}) == {
        "COMPOSE_PROFILES": "integrated"}


def test_no_flag_and_no_layout_changes_nothing():
    """A genuine split host must be left alone."""
    assert wiring.plan_profile({}) == {}
    assert wiring.plan_profile({}, False) == {}


def test_the_layout_alone_is_enough():
    """The reported host: integrated in fact, with no flag to say so."""
    assert wiring.plan_profile({}, True) == {"COMPOSE_PROFILES": "integrated"}


def test_an_operators_own_profile_is_kept():
    assert wiring.plan_profile({"COMPOSE_PROFILES": "mine"}, True) == {
        "COMPOSE_PROFILES": "mine,integrated"}


def test_it_is_not_rewritten_when_already_right():
    assert wiring.plan_profile({"COMPOSE_PROFILES": "integrated"}, True) == {}
    assert wiring.plan_profile({"COMPOSE_PROFILES": "a,integrated,b"}, True) == {}


# ---- the layout signal, against a fake tree -------------------------------

@pytest.fixture()
def tree(tmp_path, monkeypatch):
    """A SLOP checkout with apps/, and a switchable standalone checkout."""
    slop = tmp_path / "sysible-linux-operations-platform"
    for k in apps.VENDORED_KEYS:
        (slop / "apps" / k).mkdir(parents=True)
    (slop / "docker-compose.yml").write_text("services: {}\n")

    state = {"standalone": set()}

    def fake_vendored(key):
        if key == "slop":
            return None
        c = slop / "apps" / key
        return c if c.is_dir() else None

    def fake_checkout_dir(key):
        if key == "slop":
            return slop
        return (tmp_path / key) if key in state["standalone"] else None

    monkeypatch.setattr(apps, "vendored", fake_vendored)
    monkeypatch.setattr(apps, "checkout_dir", fake_checkout_dir)
    return state


def test_a_vendored_host_with_no_standalone_checkouts_reads_integrated(tree):
    assert apps.layout_is_integrated() is True


@pytest.mark.parametrize("standalone", list(apps.VENDORED_KEYS))
def test_one_standalone_checkout_is_enough_to_say_split(tree, standalone):
    """A pull can create apps/. It cannot delete somebody's standalone checkout,
    and that checkout is the thing actually serving requests on a host part-way
    through a migration. ONE of them is enough to refuse."""
    tree["standalone"].add(standalone)
    assert apps.layout_is_integrated() is False, (
        f"a host still running a standalone {standalone} was converted to the "
        f"integrated profile, which would start a second copy beside it")


def test_a_missing_vendored_app_is_not_integrated(tree, monkeypatch):
    """apps/ without one of the three is not the one-checkout layout."""
    monkeypatch.setattr(apps, "vendored",
                        lambda k: None if k == "connect" else object())
    assert apps.layout_is_integrated() is False


# ---- repair_profile ITSELF, which is what jobs.py calls --------------------
#
# The tests above drive plan_profile directly. That is not enough, and the gap
# was real: reverting repair_profile to the flag-only gate -- the exact bug being
# fixed -- left all ten of them passing, because none of them went through the
# function that carries the `or layout_is_integrated()`. A test that cannot fail
# when the fix is removed is not testing the fix.

@pytest.fixture()
def host(tmp_path, monkeypatch):
    """A SLOP checkout with a real .env, and a switchable standalone checkout."""
    slop = tmp_path / "sysible-linux-operations-platform"
    for k in apps.VENDORED_KEYS:
        (slop / "apps" / k).mkdir(parents=True)
    (slop / "docker-compose.yml").write_text("services: {}\n")
    env = slop / ".env"
    env.write_text("SYSIBLE_SSO_SHARED_SECRET=" + "s" * 32 + "\n")

    state = {"standalone": set(), "slop": slop, "env": env}
    monkeypatch.setattr(apps, "vendored",
                        lambda k: None if k == "slop" else
                        ((slop / "apps" / k) if (slop / "apps" / k).is_dir() else None))
    monkeypatch.setattr(apps, "checkout_dir",
                        lambda k: slop if k == "slop" else
                        ((tmp_path / k) if k in state["standalone"] else None))
    return state


def test_an_update_writes_the_profile_on_a_host_with_no_flag(host):
    """The reported failure, end to end: .env has no SYSIBLE_INTEGRATED, the
    operator updates, and nothing changes. Twice."""
    changes, notes = wiring.repair_profile(host["slop"])
    assert changes == {"COMPOSE_PROFILES": "integrated"}, (
        "repair_profile did nothing on an integrated host whose .env has no "
        "SYSIBLE_INTEGRATED -- which is every host installed with the server "
        "installer, and is exactly the host that reported this")
    assert "COMPOSE_PROFILES=integrated" in host["env"].read_text()
    assert notes and "COMPOSE_PROFILES" in notes[0]


def test_running_it_twice_changes_nothing_the_second_time(host):
    wiring.repair_profile(host["slop"])
    changes, notes = wiring.repair_profile(host["slop"])
    assert changes == {} and notes == []
    assert host["env"].read_text().count("COMPOSE_PROFILES") == 1


def test_a_split_host_is_left_alone_by_an_update(host):
    """One standalone checkout and the update must not convert the host."""
    host["standalone"].add("controller")
    changes, _ = wiring.repair_profile(host["slop"])
    assert changes == {}, (
        "an update converted a host that is still running a standalone "
        "Controller; the integrated profile would stand a second copy beside it")
    assert "COMPOSE_PROFILES" not in host["env"].read_text()
