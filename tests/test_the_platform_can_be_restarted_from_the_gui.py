"""Restarting the whole platform is a button, and it restarts the whole platform.

Reported: "there's no way to restart everything from the gui."

That was a true sentence about a button that existed. Restart lived inside the
row's "Manage" disclosure, shut by default, next to Stop and Recreate -- a
reasonable home for the rare and dangerous ones and the wrong one for the
action an operator comes to this page wanting. With the four product rows
folded into one, that disclosure was the only route to restarting anything.

There is a second half, and it is the one that would have made the button a
lie. The Controller, SLEP and Connect are services of SLOP's compose project,
gated behind the `integrated` profile. Without COMPOSE_PROFILES in SLOP's
.env, `docker compose restart` restarts the platform's own services and does
not so much as mention the other three -- the same silence that made three
pushed fixes invisible, because compose says nothing about services it is not
considering. The update path already repairs that profile before it builds;
the lifecycle path did not, so "Restart everything" would have restarted some
of it.

Run: pytest tests/
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
IDP = (REPO / "idp" / "app.py").read_text(encoding="utf-8")
JOBS = (REPO / "updater" / "backend" / "jobs.py").read_text(encoding="utf-8")


# ---- it is a button, in the open -------------------------------------------
def test_the_row_carries_a_visible_restart_button():
    assert "Restart everything" in IDP, \
        "the platform row has no restart button of its own"
    assert "data-restart=" in IDP, "nothing binds the restart button"


def test_the_restart_button_is_not_inside_the_fold():
    """The fold is `a-<key>`, painted by paintActions. The button is in the
    row's action area, `r-<key>`, beside Update now."""
    m = re.search(r"f\"<button class=btn id='b-\{key\}'.*?f\"</div>\"", IDP, re.S)
    assert m, "the skeleton row is no longer in the expected shape"
    assert "data-restart='{key}'" in m.group(0), \
        "the restart button is not in the row's action area"


def test_restart_is_offered_once_not_twice():
    """Having it on the row AND in the fold is the duplication this screen has
    already been trimmed of twice."""
    m = re.search(r"function paintActions\(a\)\{.*?\n  \}", IDP, re.S)
    assert m, "paintActions is gone or no longer in the expected shape"
    assert re.search(r"filter\(function\(x\)\{ return x !== 'restart'; \}\)", m.group(0)), \
        "the fold still lists restart alongside the row's own button"


def test_the_fold_no_longer_claims_to_be_where_restart_lives():
    assert "More actions" in IDP, "the fold is still labelled Manage"
    m = re.search(r"<p><b>Restart everything</b>(.*?)</p>", IDP, re.S)
    assert m, "the help text does not explain the restart button"
    for word in ("Controller", "SLEP", "Connect"):
        assert word in m.group(1), f"the help text does not say {word} restarts too"


def test_the_confirm_says_what_it_takes_down():
    """"Restart slop? Brief downtime" undersold a restart that takes the
    Controller, SLEP, Connect and this very page with it."""
    m = re.search(r"var whole = .*?: null;", IDP, re.S)
    assert m, "the confirm no longer distinguishes the platform-wide case"
    assert "everything" in m.group(0)
    assert "this page included" in m.group(0)


@pytest.mark.parametrize("site", ["a.key", "key"])
def test_the_restart_button_is_hidden_while_a_job_runs(site):
    """Every place that hides Update now and the fold toggle has to hide this
    too, or a restart can be fired into a running rebuild."""
    for m in re.finditer(r"hide\('b-'\+" + re.escape(site) + r"\);([^\n]*)", IDP):
        assert "hide('r-'+" + site in m.group(1), \
            f"a site hides b- and m- but leaves the restart button live: {m.group(0)}"


# ---- and it restarts everything --------------------------------------------
def test_a_lifecycle_action_repairs_the_profile_first():
    m = re.search(r"def start_action\(.*?\n    return True,", JOBS, re.S)
    assert m, "start_action is gone or no longer in the expected shape"
    body = m.group(0)
    assert "wiring.repair_profile()" in body, (
        "start_action does not put COMPOSE_PROFILES back, so a restart skips "
        "the Controller, SLEP and Connect without saying so")


def test_the_repair_happens_before_the_verb_runs():
    m = re.search(r"def start_action\(.*?\n    return True,", JOBS, re.S)
    body = m.group(0)
    assert body.index("wiring.repair_profile()") < body.index("detached.spawn"), \
        "the profile is repaired after the action has already been handed off"


def test_only_the_actions_that_start_containers_repair_it():
    """`stop` has nothing to gain from it, and writing .env on the way to
    stopping the platform is a change nobody asked for."""
    m = re.search(r"if key in SELF_AFFECTING and action in \(([^)]*)\)", JOBS)
    assert m, "the repair is not scoped to the actions that bring things up"
    named = {x.strip().strip('"\'') for x in m.group(1).split(",") if x.strip()}
    assert named == {"restart", "start", "recreate"}, named


def test_restart_is_an_action_the_platform_row_accepts():
    """The button is painted from a.actions, which comes from ACTIONS minus
    whatever action_refusal() turns down. Restart must survive that."""
    import sys
    sys.path.insert(0, str(REPO / "updater"))
    from backend import jobs
    assert "restart" in jobs.ACTIONS
    assert jobs.action_refusal("slop", "restart") is None, \
        jobs.action_refusal("slop", "restart")
    # ...and stop must still be refused: it would take the gateway and this
    # console down with no way back but a shell on the host.
    assert jobs.action_refusal("slop", "stop") is not None


# ---- the migration brings the stack up the way an update does --------------
#
# After migrating, Flashback and Baseline were dark and the footer said
# "version unknown". `sysiblectl <p> update` has always passed --remove-orphans
# and SYSIBLE_SLOP_REV on its `up`; this script, which is the other way the
# whole stack gets brought up, passed neither.

MIGRATE = (REPO / "tools" / "migrate-to-integrated").read_text(encoding="utf-8")


def _bring_up_line():
    m = re.search(r"^run_sh \"cd '\$HERE'.*?up -d --build.*$", MIGRATE, re.M)
    assert m, "the migration's bring-up is gone or no longer in the expected shape"
    return m.group(0)


def test_the_migration_removes_orphans_on_the_way_up():
    """A container carrying this project's labels under a service name the file
    no longer defines keeps running, unmanaged, still holding its published
    ports. Flashback's is the one that bites: the service was briefly renamed,
    and a container left from that window holds the agent port, so the real one
    cannot bind it and dies on startup."""
    assert "--remove-orphans" in _bring_up_line(), _bring_up_line()


def test_the_migration_stamps_the_revision_it_built():
    """Every image takes the commit it was built from as a build arg and every
    console prints it. Without it they all say "version unknown", which is how
    you end up unable to tell whether the fix you just installed is running."""
    line = _bring_up_line()
    assert "SYSIBLE_SLOP_REV=" in line, line
    assert "MIG_REV" in MIGRATE and "rev-parse" in MIGRATE


def test_the_bring_up_is_one_string_because_run_sh_takes_one():
    """run_sh is `bash -c "$1"`. An `env VAR=x` prefix passed as separate
    arguments runs `env` on its own -- and prints just that under --dry-run,
    which is the half of this script meant to show exactly what the real run
    does."""
    assert not re.search(r"^run_sh env ", MIGRATE, re.M), \
        "the bring-up passes arguments run_sh will drop"


def test_the_dry_run_still_shows_the_whole_command():
    """--dry-run is the same code path with the last step removed. A command
    the dry run cannot print is a command nobody can review."""
    m = re.search(r"^run_sh\(\) \{.*?^\}", MIGRATE, re.S | re.M)
    assert m and '"$1"' in m.group(0), m.group(0) if m else "run_sh is gone"
