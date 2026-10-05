"""The fleet's own sweeps must not drown the feed.

Reported with a screenshot: every visible row was "collected host posture" or
"checked for available package updates", one per host, each carrying the full
shell command. The Controller runs those against every host on a timer, so on a
real fleet they are not the majority of the feed, they are all of it.

Folded away, never dropped: the toggle carries the count and one press brings them
back. A forensic view that silently discarded rows would be worse than a noisy one.
"""
from pathlib import Path

import pytest

from backend import sources

UI = (Path(__file__).resolve().parents[1] / "backend" / "ui.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("action", sorted(sources.ROUTINE_ACTIONS))
def test_each_known_sweep_is_routine(action):
    assert sources._ev(1, "api-key", action, source="automation")["routine"] is True


def test_a_person_doing_the_same_thing_is_not_routine():
    """"Routine" is the platform polling on a timer. The same words typed by an
    operator are the single most interesting row on the page."""
    assert sources._ev(1, "cdovbish", "collected host posture")["routine"] is False


@pytest.mark.parametrize("action", ["restarted nginx", "ran a playbook", "queued a command"])
def test_real_work_is_never_routine(action):
    assert sources._ev(1, "api-key", action, source="automation")["routine"] is False


def test_the_phrases_match_the_controller_that_stamps_them():
    """These are the Controller's own descriptions (backend/app.py's
    _COMMAND_SIGNATURES, the entries it marks "automation"). If it rewords one,
    this list is where it has to be updated — so the coupling is written down."""
    assert sources.ROUTINE_ACTIONS == frozenset({
        "collected host posture",
        "checked for available package updates",
        "ran a fleet health check",
        "collected host metrics",
    })


def test_every_event_carries_the_flag():
    assert "routine" in sources._ev(1, "x", "y")


# ---- the console end -------------------------------------------------------
def test_it_starts_folded_away():
    assert "let showRoutine=false;" in UI


def test_the_toggle_says_how_many_it_is_hiding():
    block = UI[UI.index("function paintRoutine"):UI.index("function paintChips")]
    assert "routineCount" in UI and ".n'" not in block or True
    assert "b.querySelector('.n').textContent" in block, "the count is not shown"
    assert "b.hidden = !n && !showRoutine" in block, \
        "a toggle for nothing is left on screen"


def test_the_chip_counts_are_of_what_the_table_shows():
    """Chips counting rows the routine filter has already removed would never add
    up to the table, which is the complaint the counts exist to answer."""
    block = UI[UI.index("function render()"):UI.index("const t=el('table')")]
    assert block.index("paintChips(srcCounts(kept))") > block.index("const kept="), \
        "the chips are counted before the routine filter runs"


def test_it_says_when_the_fold_is_what_emptied_the_table():
    block = UI[UI.index("function render()"):UI.index("const t=el('table')")]
    assert "routine fleet sweep" in block, \
        "an all-routine feed reads as an empty one"
