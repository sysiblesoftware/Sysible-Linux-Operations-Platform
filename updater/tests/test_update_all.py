"""Update all: one job, in order, SLOP last, and one failure does not strand the rest."""
import threading
import time

import pytest

from backend import jobs


@pytest.fixture(autouse=True)
def _reset():
    jobs._job = None
    if jobs._lock.locked():
        jobs._lock.release()
    yield
    if jobs._lock.locked():
        jobs._lock.release()


def _wait(timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        j = jobs.current() or {}
        if j.get("state") in ("succeeded", "failed"):
            return j
        time.sleep(0.02)
    raise AssertionError("the job never finished")


def _fake(monkeypatch, outcomes):
    """Replace the real work with a recorder. outcomes: key -> ok."""
    seen = []

    def one(key, root, compose):
        seen.append(key)
        return outcomes.get(key, True), f"{key} done"
    monkeypatch.setattr(jobs, "_update_one", one)
    return seen


def test_it_runs_every_product_in_order_as_one_job(monkeypatch, tmp_path):
    seen = _fake(monkeypatch, {})
    items = [(k, tmp_path, tmp_path) for k in ("slep", "connect", "controller")]
    started, _ = jobs.start_many(items, "alice")
    assert started
    j = _wait()
    assert seen == ["slep", "connect", "controller"], "out of order, or run in parallel"
    assert j["state"] == "succeeded"
    assert j["message"] == "All 3 updated and restarted."
    assert [d["key"] for d in j["done"]] == seen


def test_one_failure_does_not_strand_the_rest(monkeypatch, tmp_path):
    """A host where one checkout is broken must still get the others updated —
    stopping at the first problem is how "update everything" becomes "update
    nothing"."""
    seen = _fake(monkeypatch, {"slep": False})
    items = [(k, tmp_path, tmp_path) for k in ("slep", "connect", "controller")]
    jobs.start_many(items, "alice")
    j = _wait()
    assert seen == ["slep", "connect", "controller"], "it stopped at the failure"
    assert j["state"] == "failed"
    assert "2 of 3 updated" in j["message"] and "slep failed" in j["message"]
    assert [d["ok"] for d in j["done"]] == [False, True, True]


def test_progress_is_reported_while_it_runs(monkeypatch, tmp_path):
    """On a four-product run, which one is in flight is the only thing worth
    knowing — the console renders `index` of `queue`."""
    gate = threading.Event()

    def one(key, root, compose):
        if key == "connect":
            gate.wait(2)
        return True, "ok"
    monkeypatch.setattr(jobs, "_update_one", one)
    items = [(k, tmp_path, tmp_path) for k in ("slep", "connect", "controller")]
    jobs.start_many(items, "alice")
    for _ in range(200):
        j = jobs.current() or {}
        if j.get("app") == "connect":
            break
        time.sleep(0.01)
    j = jobs.current()
    assert j["queue"] == ["slep", "connect", "controller"]
    assert j["index"] == 1, "the console cannot say which product is in flight"
    gate.set()
    _wait()


def test_a_second_batch_is_refused_while_one_runs(monkeypatch, tmp_path):
    gate = threading.Event()
    monkeypatch.setattr(jobs, "_update_one", lambda *a: (gate.wait(2), "ok")[1:] and (True, "ok"))
    items = [(k, tmp_path, tmp_path) for k in ("slep", "connect")]
    assert jobs.start_many(items, "alice")[0] is True
    started, message = jobs.start_many(items, "bob")
    assert started is False and "already running" in message
    gate.set()
    _wait()


def test_nothing_to_do_is_refused_not_started(tmp_path):
    started, message = jobs.start_many([], "alice")
    assert started is False and message == "Nothing to update."


def test_slop_hands_off_and_nothing_is_queued_behind_it(monkeypatch, tmp_path):
    """SLOP recreates this container, so anything after it would be killed
    mid-pull and no outcome could ever be reported."""
    seen = _fake(monkeypatch, {})
    spawned = []
    monkeypatch.setattr(jobs.detached, "spawn",
                        lambda script, compose: (spawned.append(script), (True, "handed off"))[1])
    items = [("slep", tmp_path, tmp_path), ("slop", tmp_path, tmp_path)]
    jobs.start_many(items, "alice")
    for _ in range(200):
        if spawned:
            break
        time.sleep(0.01)
    assert seen == ["slep"], "a product ran after slop, or slop ran in-process"
    assert spawned == [jobs.detached.SCRIPT_UPDATE]
