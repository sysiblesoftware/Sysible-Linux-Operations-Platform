"""A Controller that has not been updated past the rebrand still authenticates.

Controller and Baseline are SEPARATE deployments on separate upgrade schedules.
A Controller built before Flashback was split into Flashback and Baseline sends
its agent token in `x-sysible-flashback-agent`; a current one sends
`Authorization: Bearer`. Both have to work, or the desired-state channel stops
fleet-wide the moment one half is updated and the other is not — which is what
the comment on that fallback has always promised.

It did not do it. The loop read

    for name in ("x-sysible-baseline-agent", "x-sysible-baseline-agent"):

— the same header twice. It checked one name, then checked it again, and the
shim the comment describes was not there. Found by sweeping every collection
literal in the platform for a value listed twice, which is the third time that
exact shape has turned up here.

Flashback has no such shim and needs none: it has been called Flashback for
this header's whole life. Its copy of the loop listed ITS name twice, under the
same borrowed comment, and is now a single lookup that says so.

Run: pytest baseline/tests/test_the_rebrand_shim_in_agent_auth.py
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

TOKEN = "t" * 64


class _Req:
    def __init__(self, **headers):
        self.headers = {k.replace("_", "-").lower(): v for k, v in headers.items()}


@pytest.fixture
def auth(monkeypatch):
    from backend import identity
    monkeypatch.setattr(identity, "_AGENT_TOKEN", TOKEN)
    return identity.agent_auth_ok


def test_a_current_controller_authenticates(auth):
    assert auth(_Req(authorization=f"Bearer {TOKEN}")) is True


def test_the_current_header_name_authenticates(auth):
    assert auth(_Req(**{"x-sysible-baseline-agent": TOKEN})) is True


def test_a_pre_rebrand_controller_still_authenticates(auth):
    """The shim. Before this, the loop checked the Baseline name twice and a
    Controller sending the Flashback name was refused — so upgrading one half
    of the platform took desired-state configuration down across the fleet."""
    assert auth(_Req(**{"x-sysible-flashback-agent": TOKEN})) is True, (
        "a Controller from before the rebrand cannot authenticate, which is "
        "exactly what the fallback exists to prevent")


def test_a_wrong_token_is_refused_under_every_name(auth):
    for header in ("authorization", "x-sysible-baseline-agent",
                   "x-sysible-flashback-agent"):
        value = "Bearer wrong" if header == "authorization" else "wrong"
        assert auth(_Req(**{header: value})) is False, header


def test_no_header_at_all_is_refused(auth):
    assert auth(_Req()) is False


def test_it_fails_closed_when_no_token_is_configured(monkeypatch):
    """A missing token is a misconfiguration, never a grant: these endpoints
    take host_id from the caller."""
    from backend import identity
    monkeypatch.setattr(identity, "_AGENT_TOKEN", "")
    assert identity.agent_auth_ok(_Req(authorization=f"Bearer {TOKEN}")) is False
    assert identity.agent_auth_ok(_Req(**{"x-sysible-flashback-agent": TOKEN})) is False


def test_the_fallback_names_two_DIFFERENT_headers():
    """The bug itself, structurally. A loop over a pair that is the same value
    twice reads as a shim and is not one."""
    import inspect
    from backend import identity
    src = inspect.getsource(identity.agent_auth_ok)
    import re
    m = re.search(r"for name in \(([^)]*)\)", src)
    assert m, f"the fallback is no longer a loop over header names:\n{src}"
    names = re.findall(r'"([^"]+)"', m.group(1))
    assert len(names) == len(set(names)), f"the same header twice: {names}"
    assert "x-sysible-baseline-agent" in names and "x-sysible-flashback-agent" in names, names
