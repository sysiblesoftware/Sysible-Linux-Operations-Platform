"""Automatic updates: when they run, and what they say they did.

Asked for: "add an auto update option for slop and all components".

The scheduler is deliberately thin. It decides WHEN and then presses the button
an operator would — the same list, the same refusals, the same job. These tests
are mostly about the three ways a scheduler lies:

  * running when it should not (sixty updates from sixty ticks, a run in the
    middle of someone's afternoon);
  * NOT running when it should (a missed window that never catches up);
  * saying it ran when it refused, which is the one that gets believed.

Run: pytest updater/tests/
"""
import importlib
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_schedule():
    """A fresh backend.schedule. STATE_PATH is read at import, so the env var
    has to be in place first — which is why this reloads rather than importing
    once at module scope."""
    import backend.schedule as m
    return importlib.reload(m)


@pytest.fixture()
def sched(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSIBLE_UPDATER_STATE", str(tmp_path / "autoupdate.json"))
    yield _load_schedule()


# ---- the default is OFF ---------------------------------------------------
def test_it_is_off_until_someone_turns_it_on(sched):
    """An update of the platform recreates the gateway, this console and the
    updater itself. That is not something to switch on for someone."""
    s = sched.settings()
    assert s["enabled"] is False
    assert sched.next_run(s) is None
    assert sched.due(s, datetime(2026, 1, 4, 3, 0)) is False


def test_an_unreadable_state_file_reads_as_off(sched, tmp_path):
    """The failure mode of a corrupt file has to be 'does not update by
    itself', not 'updates on some hour nobody chose'."""
    (tmp_path / "autoupdate.json").write_text("{not json at all")
    assert sched.settings()["enabled"] is False


def test_a_hand_edited_file_is_brought_back_inside_its_own_rules(sched, tmp_path):
    """An hour of 99 would mean a schedule that never comes due while the screen
    says it is on — which is the shape of a setting that looks fine and does
    nothing."""
    (tmp_path / "autoupdate.json").write_text(
        '{"enabled": true, "hour": 99, "weekday": 41, "cadence": "fortnightly"}')
    s = sched.settings()
    assert 0 <= s["hour"] <= 23
    assert 0 <= s["weekday"] <= 6
    assert s["cadence"] in sched.CADENCES
    assert sched.next_run(s) is not None, "a cleaned-up schedule still has a next run"


# ---- when it is due -------------------------------------------------------
def test_daily_comes_due_in_its_hour_and_not_otherwise(sched):
    s = sched.save({"enabled": True, "cadence": "daily", "hour": 3})
    assert sched.due(s, datetime(2026, 1, 5, 3, 0)) is True
    assert sched.due(s, datetime(2026, 1, 5, 3, 59)) is True
    assert sched.due(s, datetime(2026, 1, 5, 2, 59)) is False
    assert sched.due(s, datetime(2026, 1, 5, 4, 0)) is False


def test_weekly_comes_due_on_its_day_only(sched):
    # weekday 6 is Sunday; 2026-01-04 is a Sunday.
    s = sched.save({"enabled": True, "cadence": "weekly", "hour": 3, "weekday": 6})
    assert sched.due(s, datetime(2026, 1, 4, 3, 10)) is True
    assert sched.due(s, datetime(2026, 1, 5, 3, 10)) is False, "Monday is not Sunday"


def test_a_tick_every_minute_does_not_start_sixty_updates(sched):
    """The thread looks at the clock once a minute. Without the record of the
    last run, every one of those looks would see the same due hour."""
    s = sched.save({"enabled": True, "cadence": "daily", "hour": 3})
    now = datetime(2026, 1, 5, 3, 0)
    assert sched.due(s, now) is True
    sched._record("nothing", "already up to date", at=now.timestamp())
    s = sched.settings()
    # Same hour, a minute later: already done.
    assert sched.due(s, now + timedelta(minutes=1)) is False
    # ...and due again tomorrow.
    assert sched.due(s, now + timedelta(days=1)) is True


def test_a_missed_window_is_picked_up_when_the_host_comes_back(sched):
    """A host switched off at 03:00 on Sunday has to update when it comes back,
    not wait a week. The window is the hour, so coming back inside it works."""
    s = sched.save({"enabled": True, "cadence": "weekly", "hour": 3, "weekday": 6})
    assert sched.due(s, datetime(2026, 1, 4, 3, 45)) is True


def test_next_run_is_a_real_future_time(sched):
    s = sched.save({"enabled": True, "cadence": "weekly", "hour": 3, "weekday": 6})
    now = datetime(2026, 1, 5, 12, 0)          # a Monday lunchtime
    nxt = sched.next_run(s, now)
    assert nxt > now
    assert nxt.weekday() == 6 and nxt.hour == 3
    assert (nxt - now) < timedelta(days=8)


def test_next_run_on_the_day_itself_does_not_skip_a_week(sched):
    """The off-by-one that would make a Sunday schedule say 'next Sunday' all
    Sunday morning."""
    s = sched.save({"enabled": True, "cadence": "weekly", "hour": 3, "weekday": 6})
    nxt = sched.next_run(s, datetime(2026, 1, 4, 1, 0))   # Sunday, 1am
    assert nxt == datetime(2026, 1, 4, 3, 0), nxt


# ---- what it says it did --------------------------------------------------
def test_a_refusal_is_recorded_as_a_refusal(sched):
    """The lie that matters. "Last run: Tuesday 03:00" over a checkout that has
    been refusing for three weeks is the kind of green that gets believed."""
    sched._record("refused", "nothing could be updated — blocked: slep (local changes)")
    last = sched.settings()["last"]
    assert last["outcome"] == "refused"
    assert "local changes" in last["detail"]


def test_nothing_to_do_is_not_spelled_the_same_as_updated(sched):
    """A screen that says "updated" every night about a platform that has not
    moved in a month teaches an operator to stop reading it."""
    sched._record("nothing", "everything was already up to date")
    assert sched.settings()["last"]["outcome"] == "nothing"


def test_a_caller_cannot_write_a_run_that_never_happened(sched):
    """`last` is the scheduler's own record. save() takes the five settings and
    nothing else."""
    sched.save({"enabled": True,
                "last": {"at": 0, "outcome": "updated", "detail": "a fiction"}})
    assert sched.settings()["last"] is None


def test_the_settings_survive_a_restart(sched, tmp_path):
    sched.save({"enabled": True, "cadence": "daily", "hour": 5})
    again = _load_schedule()          # as if the container came back
    s = again.settings()
    assert s["enabled"] is True and s["hour"] == 5


# ---- the run itself -------------------------------------------------------
def test_a_run_refuses_while_another_job_is_going(sched, monkeypatch):
    """Two updates of the same git tree at once is a fight over container
    names. The job lock would refuse it anyway; saying so is the point."""
    from backend import jobs
    monkeypatch.setattr(jobs, "current",
                        lambda: {"state": "running", "app": "slop"})
    outcome, detail = sched.run_now()
    assert outcome == "refused"
    assert "already running" in detail
    assert sched.settings()["last"]["outcome"] == "refused"


def test_a_run_with_nothing_available_records_nothing_not_updated(sched, monkeypatch):
    from backend import apps, git, jobs
    monkeypatch.setattr(jobs, "current", lambda: None)
    monkeypatch.setattr(apps, "keys", lambda: ["slop"])
    monkeypatch.setattr(apps, "is_vendored", lambda k: False)
    monkeypatch.setattr(apps, "checkout_dir", lambda k: __import__("pathlib").Path("/tmp"))
    monkeypatch.setattr(git, "status", lambda *a, **k: {"available": False})
    monkeypatch.setattr(apps, "compose_dir", lambda r: r)
    outcome, _ = sched.run_now()
    assert outcome == "nothing"


def test_a_dirty_checkout_blocks_and_is_named(sched, monkeypatch):
    """The checkout is reported and refused, never overwritten — the same rule
    the manual update follows."""
    import pathlib
    from backend import apps, git, jobs
    monkeypatch.setattr(jobs, "current", lambda: None)
    monkeypatch.setattr(apps, "keys", lambda: ["slep"])
    monkeypatch.setattr(apps, "is_vendored", lambda k: False)
    monkeypatch.setattr(apps, "checkout_dir", lambda k: pathlib.Path("/tmp"))
    monkeypatch.setattr(apps, "compose_dir", lambda r: r)
    monkeypatch.setattr(git, "status", lambda *a, **k: {"available": True})
    monkeypatch.setattr(git, "dirty", lambda r: True)
    outcome, detail = sched.run_now()
    assert outcome == "refused"
    assert "slep (local changes)" in detail


def test_the_platform_is_updated_last(sched, monkeypatch):
    """Updating it recreates the gateway, this console and the updater itself,
    so anything queued behind it is killed mid-pull."""
    import pathlib
    from backend import apps, git, jobs
    seen = {}
    monkeypatch.setattr(jobs, "current", lambda: None)
    monkeypatch.setattr(apps, "keys", lambda: ["slop", "slep", "connect"])
    monkeypatch.setattr(apps, "is_vendored", lambda k: False)
    monkeypatch.setattr(apps, "checkout_dir", lambda k: pathlib.Path("/tmp") / k)
    monkeypatch.setattr(apps, "compose_dir", lambda r: r)
    monkeypatch.setattr(git, "status", lambda *a, **k: {"available": True})
    monkeypatch.setattr(git, "dirty", lambda r: False)
    monkeypatch.setattr(jobs, "start_many",
                        lambda items, actor: (seen.update(order=[k for k, _r, _c in items])
                                              or (True, "updating 3 products")))
    outcome, _ = sched.run_now()
    assert outcome == "updated"
    assert seen["order"][-1] == "slop", seen["order"]


def test_a_vendored_app_is_not_queued_as_its_own_pull(sched, monkeypatch):
    """It is built from the platform's tree: it has no pull of its own, and
    listing it would queue the same git tree twice."""
    import pathlib
    from backend import apps, git, jobs
    seen = {}
    monkeypatch.setattr(jobs, "current", lambda: None)
    monkeypatch.setattr(apps, "keys", lambda: ["controller", "slop"])
    monkeypatch.setattr(apps, "is_vendored", lambda k: k == "controller")
    monkeypatch.setattr(apps, "checkout_dir", lambda k: pathlib.Path("/tmp") / k)
    monkeypatch.setattr(apps, "compose_dir", lambda r: r)
    monkeypatch.setattr(git, "status", lambda *a, **k: {"available": True})
    monkeypatch.setattr(git, "dirty", lambda r: False)
    monkeypatch.setattr(jobs, "start_many",
                        lambda items, actor: (seen.update(order=[k for k, _r, _c in items])
                                              or (True, "updating")))
    sched.run_now()
    assert seen["order"] == ["slop"], seen["order"]


def test_the_platform_can_be_left_out(sched, monkeypatch):
    """Some operators want their apps current and the front door changed only
    by hand — it is the one that logs everyone out."""
    import pathlib
    from backend import apps, git, jobs
    seen = {}
    sched.save({"include_platform": False})
    monkeypatch.setattr(jobs, "current", lambda: None)
    monkeypatch.setattr(apps, "keys", lambda: ["slep", "slop"])
    monkeypatch.setattr(apps, "is_vendored", lambda k: False)
    monkeypatch.setattr(apps, "checkout_dir", lambda k: pathlib.Path("/tmp") / k)
    monkeypatch.setattr(apps, "compose_dir", lambda r: r)
    monkeypatch.setattr(git, "status", lambda *a, **k: {"available": True})
    monkeypatch.setattr(git, "dirty", lambda r: False)
    monkeypatch.setattr(jobs, "start_many",
                        lambda items, actor: (seen.update(order=[k for k, _r, _c in items])
                                              or (True, "updating")))
    sched.run_now()
    assert seen["order"] == ["slep"], seen["order"]


def test_importing_the_module_does_not_start_updating_the_host(sched):
    """The thread is started from the service's startup hook, not at import, so
    reading this module — which these tests do — cannot set one going."""
    assert sched._thread is None, "importing backend.schedule started a thread"
