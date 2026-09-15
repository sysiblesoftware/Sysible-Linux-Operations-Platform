"""The fleet map's readings are independent — so it must not wait for them in turn.

topology() pulls hosts, health, agents, suppressions and (optionally) posture from
the Controller. Nothing there feeds anything else; they are merged afterwards. Read
one at a time, the map cost the SUM of five round-trips, and its worst case was
five times the per-request timeout — a Controller that was merely slow made the
page look hung, and the slower it got the worse the multiplier.

These pin the properties that matter: the readings overlap, the map still degrades
one reading at a time, and which failures are fatal did not change.
"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import sources  # noqa: E402


class _Id:
    user, role = "alice", "superuser"


ALL = ["/api/hosts", "/api/fleet-health", "/api/agents", "/api/suppressions"]


@pytest.fixture()
def slow_controller(monkeypatch):
    """Every reading takes the same visible time, and records when it ran."""
    lock = threading.Lock()
    spans = {}
    delay = {"s": 0.20}
    fail = set()

    def fake_get(url, identity, params=None, want_json=True):
        path = url.split("8800", 1)[-1] if "8800" in url else url
        for p in ALL + ["/api/fleet-posture"]:
            if url.endswith(p):
                path = p
        start = time.monotonic()
        time.sleep(delay["s"])
        with lock:
            spans[path] = (start, time.monotonic())
        if path in fail:
            return None, "unreachable (ConnectError)"
        key = {"/api/hosts": "hosts", "/api/fleet-health": "hosts",
               "/api/agents": "agents", "/api/suppressions": "suppressions",
               "/api/fleet-posture": "hosts"}[path]
        return {key: []}, None

    monkeypatch.setattr(sources, "_get", fake_get)
    return {"spans": spans, "delay": delay, "fail": fail}


def test_the_readings_overlap_instead_of_queueing(slow_controller):
    d = slow_controller["delay"]["s"]
    t0 = time.monotonic()
    sources.topology(_Id())
    elapsed = time.monotonic() - t0
    spans = slow_controller["spans"]
    assert set(spans) == set(ALL), spans
    # Sequentially this is 4*d; together it is ~d. Allow generous slack for a
    # loaded machine while still failing loudly if they queue.
    assert elapsed < d * 2.5, f"took {elapsed:.2f}s for {len(ALL)} x {d:.2f}s readings"


def test_every_reading_is_actually_in_flight_at_once(slow_controller):
    sources.topology(_Id())
    spans = slow_controller["spans"]
    latest_start = max(s for s, _ in spans.values())
    earliest_end = min(e for _, e in spans.values())
    assert latest_start < earliest_end, \
        f"no moment where all {len(spans)} readings were running: {spans}"


def test_posture_is_only_fetched_when_asked_for(slow_controller):
    sources.topology(_Id())
    assert "/api/fleet-posture" not in slow_controller["spans"]
    slow_controller["spans"].clear()
    sources.topology(_Id(), with_posture=True)
    assert "/api/fleet-posture" in slow_controller["spans"]


def test_the_host_list_failing_is_an_error(slow_controller):
    slow_controller["fail"].add("/api/hosts")
    out = sources.topology(_Id())
    assert any("/api/hosts" in e for e in out["errors"]), out
    assert not out["notes"]


def test_an_overlay_failing_is_only_a_note(slow_controller):
    """A half-drawn map beats an empty one."""
    slow_controller["fail"].update({"/api/fleet-health", "/api/agents"})
    out = sources.topology(_Id())
    assert not out["errors"], out
    assert len(out["notes"]) == 2, out


def test_failures_are_reported_in_a_stable_order(slow_controller):
    """Concurrency must not make the reported order depend on which finished
    first — the same broken fleet has to read the same way twice."""
    slow_controller["fail"].update({"/api/agents", "/api/fleet-health",
                                    "/api/suppressions"})
    first = sources.topology(_Id())["notes"]
    second = sources.topology(_Id())["notes"]
    assert first == second
    assert [n.split(":")[0] for n in first] == \
        ["/api/fleet-health", "/api/agents", "/api/suppressions"]


def test_one_slow_reading_does_not_multiply_the_wait(slow_controller, monkeypatch):
    """The worst case used to be five times the timeout, not one."""
    calls = []

    def fake_get(url, identity, params=None, want_json=True):
        calls.append(url)
        time.sleep(0.25 if url.endswith("/api/suppressions") else 0.01)
        return {"hosts": [], "agents": [], "suppressions": []}, None

    monkeypatch.setattr(sources, "_get", fake_get)
    t0 = time.monotonic()
    sources.topology(_Id(), with_posture=True)
    elapsed = time.monotonic() - t0
    assert len(calls) == 5
    assert elapsed < 0.25 * 2, f"the slow reading was waited for on its own: {elapsed:.2f}s"
