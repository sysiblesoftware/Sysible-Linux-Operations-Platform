"""Flashback accepts its own agent header, and claims no shim it does not have.

Its fallback was written as a loop over a two-element tuple listing
`x-sysible-flashback-agent` twice, under a comment — borrowed from Baseline's
copy — about honouring "both spellings" so a half-upgraded Controller keeps
working. Flashback was never renamed, so there is no second spelling and no
shim to honour. The loop checked one header and then checked it again, and the
comment described a guarantee that did not exist and was not needed.

Baseline IS the one that was renamed, and its copy now honours both names for
real.

Run: pytest flashback/tests/test_agent_auth_has_one_header_name.py
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


def test_a_bearer_token_authenticates(auth):
    assert auth(_Req(authorization=f"Bearer {TOKEN}")) is True


def test_its_own_fallback_header_authenticates(auth):
    assert auth(_Req(**{"x-sysible-flashback-agent": TOKEN})) is True


def test_a_wrong_token_is_refused(auth):
    assert auth(_Req(**{"x-sysible-flashback-agent": "wrong"})) is False
    assert auth(_Req(authorization="Bearer wrong")) is False


def test_it_fails_closed_with_no_token_configured(monkeypatch):
    from backend import identity
    monkeypatch.setattr(identity, "_AGENT_TOKEN", "")
    assert identity.agent_auth_ok(_Req(authorization=f"Bearer {TOKEN}")) is False


def test_it_does_not_pretend_to_accept_a_second_spelling():
    """Either accept another name or do not claim to. A loop over one name
    listed twice did neither."""
    import inspect
    import re
    from backend import identity
    src = inspect.getsource(identity.agent_auth_ok)
    m = re.search(r"for name in \(([^)]*)\)", src)
    if m:
        names = re.findall(r'"([^"]+)"', m.group(1))
        assert len(names) == len(set(names)), f"the same header listed twice: {names}"
