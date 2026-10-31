"""The SLEP tab asks for as many runs as it intends to show, and says when it
could not have them.

_slep() asked /api/runs with no limit and then sliced the reply to `limit`,
which can be 500 (MAX_LIMIT). SLEP's /runs took no limit and capped at 100, so
on a busy SLEP the tab showed 100 runs, reported nothing in `notes`, and a
truncated feed was indistinguishable from a complete one. Every other source
here passes its limit; this one did not.

SLEP now accepts a bounded limit and echoes the one it used, so the two can
agree — and an older SLEP, which ignores the parameter, is reported rather than
trusted.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from backend import sources  # noqa: E402

IDENT = type("I", (), {"user": "alice", "role": "superuser"})()


def _runs(n, limit=None):
    body = {"runs": [{"id": i, "kind": "ansible", "status": "success",
                      "created": 1700000000 + i, "created_by": "alice",
                      "target": "all", "exit_code": 0} for i in range(n)]}
    if limit is not None:
        body["limit"] = limit
    return body


@pytest.fixture()
def spy(monkeypatch):
    """Capture what _slep asks for, and answer with whatever the test wants."""
    calls = []
    reply = {"body": _runs(0, 100), "audit": {"entries": []}}

    def _get(url, identity, params=None):
        calls.append((url, dict(params or {})))
        if url.endswith("/api/runs"):
            return reply["body"], None
        return reply["audit"], None

    monkeypatch.setattr(sources, "_get", _get)
    return calls, reply


def test_the_limit_is_actually_sent(spy):
    calls, _reply = spy
    sources._slep(IDENT, 500)
    runs = [p for u, p in calls if u.endswith("/api/runs")]
    assert runs, "no /api/runs call was made"
    assert runs[0].get("limit") == 500, runs[0]


def test_every_slep_call_carries_the_limit(spy):
    calls, _reply = spy
    sources._slep(IDENT, 250)
    assert calls, "nothing was fetched"
    for url, params in calls:
        assert params.get("limit") == 250, (url, params)


def test_a_full_page_is_reported_as_possibly_truncated(spy):
    _calls, reply = spy
    reply["body"] = _runs(100, 100)
    out = sources._slep(IDENT, 500)
    assert out["notes"], "a feed cut off at SLEP's maximum was reported as complete"
    assert "100" in " ".join(out["notes"])


def test_an_older_slep_that_ignores_the_limit_is_reported(spy):
    """No `limit` key in the reply: a SLEP from before it accepted one, which
    answers at most 100 whatever is asked."""
    _calls, reply = spy
    reply["body"] = _runs(100)          # no "limit" echoed
    out = sources._slep(IDENT, 500)
    assert any("100" in n for n in out["notes"]), out["notes"]


def test_a_short_page_is_not_reported(spy):
    _calls, reply = spy
    reply["body"] = _runs(7, 500)
    out = sources._slep(IDENT, 500)
    assert out["notes"] == [], out["notes"]
    assert len(out["events"]) == 7


def test_asking_for_less_than_slep_served_is_not_a_truncation(spy):
    _calls, reply = spy
    reply["body"] = _runs(50, 100)
    out = sources._slep(IDENT, 50)
    assert out["notes"] == [], out["notes"]
