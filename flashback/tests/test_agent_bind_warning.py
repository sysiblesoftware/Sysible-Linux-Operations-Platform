"""The agent endpoint's safety is a configuration property, so say so out loud.

The agent API takes host_id from the caller and authenticates with one token
shared by whoever posts snapshots. That is safe as shipped only because of where
it listens — the installer binds it to the docker bridge, and only the Controller
holds the token. Published on every interface, any token holder can write any
host's config history. Demonstrated, not theorised.

So an override that removes the protection has to announce itself.
"""
import importlib
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def _warn(monkeypatch, bind, capsys):
    if bind is None:
        monkeypatch.delenv("SYSIBLE_FLASHBACK_AGENT_BIND", raising=False)
    else:
        monkeypatch.setenv("SYSIBLE_FLASHBACK_AGENT_BIND", bind)
    from backend import identity
    importlib.reload(identity)
    identity._warn_if_agent_port_is_wide_open()
    return capsys.readouterr().out


@pytest.mark.parametrize("bind", ["0.0.0.0", "::", "*"])
def test_a_wildcard_bind_is_announced(monkeypatch, capsys, bind):
    out = _warn(monkeypatch, bind, capsys)
    assert "EVERY interface" in out, f"{bind} published silently"
    assert "host_id" in out and "fleet-wide token" in out, \
        "the warning does not say WHY it matters"


@pytest.mark.parametrize("bind", [None, "172.17.0.1", "127.0.0.1", "10.1.2.3"])
def test_a_safe_bind_says_nothing(monkeypatch, capsys, bind):
    """A warning that fires on the correct configuration is a warning people
    learn to ignore."""
    assert _warn(monkeypatch, bind, capsys) == ""


def test_the_token_still_fails_closed_when_unset(monkeypatch):
    monkeypatch.delenv("SYSIBLE_FLASHBACK_AGENT_TOKEN", raising=False)
    from backend import identity
    importlib.reload(identity)

    class _R:
        headers = {"authorization": "Bearer anything"}
    assert identity.agent_auth_ok(_R()) is False
