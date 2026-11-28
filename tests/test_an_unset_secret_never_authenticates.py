"""No app may believe a forged identity when its shared secret is unset.

Every SLOP app decides whether to trust `X-Sysible-User` by comparing the
request's `X-Sysible-Auth` against a secret from the environment. That pattern
has one classic way to fail, and it is silent:

    hmac.compare_digest("", "")  ->  True

So a deployment where the secret never reached the environment — a missed
`.env`, a container recreated without it, a mistyped variable name — does not
fail noisily. It authenticates *everybody*, as whatever user and role they care
to type, with no credential at all. On this platform that reaches a terminal on
the fleet (Connect), the host's Docker socket (updater), and a restore that runs
as root (Baseline/Flashback).

All seven apps get this right today, and each does it differently: an
`if not secret: return False`, a `gateway_configured()` that ANDs the secret in,
a 503 raised before the compare. Seven defences, seven files, no shared
assertion — so one refactor removes one of them and nothing says so.

These are behavioural: each one calls the REAL function in the REAL module with
the environment cleared and forged headers. Asserting that a guard *line* exists
would pass against a guard that no longer runs, which is a failure this codebase
has hit before.
"""
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

# THE DEPLOYMENT THIS DESCRIBES: SSO turned ON, secret MISSING.
#
# That distinction is the whole test, and getting it wrong made an earlier
# version of this file useless: with the trust flags cleared too, every app
# returns None on the flag alone and never reaches the comparison, so SIX of the
# eight guards could be deleted with the suite still green. A test that cannot
# fail is worse than no test, because it is reported as cover.
#
# Each app has its own flag, with its own name, defaulting off:
SECRET_VARS = ("SYSIBLE_SSO_SHARED_SECRET", "SYSIBLE_UPDATER_SECRET",
               "SYSIBLE_BASELINE_GATEWAY_SECRET", "SYSIBLE_AGENT_TOKEN")
# Baseline and Flashback read their settings through a per-service PREFIX
# (SYSIBLE_BASELINE_ / SYSIBLE_FLASHBACK_) deliberately, so that two products
# wanting the name TRUST_GATEWAY_AUTH cannot consume each other's settings. The
# unprefixed name does nothing, which is how they slipped past an earlier
# version of this file.
TRUST_VARS = ("SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH",
              "SYSIBLE_FLASHBACK_TRUST_GATEWAY_AUTH",
              "SYSIBLE_VISUALIZER_TRUST_GATEWAY_AUTH",     # visualizer
              "SYSIBLE_CONNECT_TRUST_GATEWAY_AUTH",        # connect
              "SLEP_TRUST_GATEWAY_AUTH",                   # slep (backend + bff)
              "SYSIBLE_WEBGUI_TRUST_SSO")                  # controller bff

# What an attacker sends: a complete, well-formed, wholly unauthenticated
# identity. The empty auth header is the one that makes compare_digest true.
FORGED = {"x-sysible-user": "attacker",
          "x-sysible-role": "superuser",
          "x-sysible-auth": ""}


class _Headers(dict):
    def get(self, k, default=None):
        return dict.get(self, k.lower(), default)


class _Req:
    """The smallest thing these functions accept: headers and cookies."""

    def __init__(self, headers):
        self.headers = _Headers(headers)
        self.cookies = {}


@pytest.fixture
def cleanenv(monkeypatch):
    """SSO on, secret missing — the misconfiguration this file is about."""
    for v in SECRET_VARS:
        monkeypatch.delenv(v, raising=False)
    for v in TRUST_VARS:
        monkeypatch.setenv(v, "1")
    return monkeypatch


# Every app ships its OWN top-level `backend` package, so a plain import returns
# whichever reached sys.modules first — the collision tests/conftest.py exists to
# describe. `app_module` is its loader: it isolates sys.path and sys.modules and
# restores both afterwards. It is already a context manager.
SIDECARS = [("baseline", "baseline"),
            ("flashback", "flashback"),
            ("visualizer", "visualizer")]


@pytest.mark.parametrize("name,app", SIDECARS)
def test_a_sidecar_does_not_accept_a_forged_identity(name, app, cleanenv, app_module):
    """These resolve a caller in `identity.current(request)`. With no secret the
    acceptable answers are None (refused) or the LOCAL fallback — and the
    fallback must never be the attacker's claimed name or role."""
    with app_module(app, "backend.identity") as ident:
        who = ident.current(_Req(dict(FORGED)))
        if who is None:
            return
        assert getattr(who, "user", None) != "attacker", (
            "%s accepted a forged X-Sysible-User with no shared secret" % name)
        assert getattr(who, "role", "") != "superuser", (
            "%s accepted a forged X-Sysible-Role with no shared secret" % name)


@pytest.mark.parametrize("name,app", SIDECARS)
def test_a_sidecar_is_not_in_gateway_mode_without_a_secret(name, app, cleanenv, app_module):
    """gateway_configured() gates the trusting branch in all three. If it ever
    returns true on an empty secret, that branch becomes reachable."""
    with app_module(app, "backend.identity") as ident:
        assert ident.gateway_configured() is False, (
            "%s considers itself gateway-protected with no secret set" % name)


def test_connect_does_not_mint_an_identity(cleanenv, app_module):
    """Connect's terminal websocket calls gateway_identity(headers) directly — it
    bypasses the Depends() machinery, so this IS its authentication."""
    with app_module("apps/connect", "backend.app") as mod:
        assert mod.gateway_identity(_Headers(dict(FORGED))) is None, (
            "Connect accepted a forged identity with no shared secret — this is "
            "the function its terminal websocket uses to decide who you are")


def test_slep_does_not_mint_an_identity(cleanenv, app_module):
    with app_module("apps/slep", "backend.app") as mod:
        assert mod._gateway_identity(_Req(dict(FORGED))) is None, (
            "SLEP accepted a forged identity with no shared secret")


def test_the_controller_does_not_match_an_empty_secret(cleanenv, app_module):
    """_gateway_secret_ok gates /auth/api-key — the one route that hands out the
    master API key to a caller who does not already hold it."""
    with app_module("apps/controller", "backend.app") as mod:
        assert mod._gateway_secret_ok(_Req(dict(FORGED))) is False, (
            "the Controller treated an empty secret as a match — this gates the "
            "route that issues the master API key")


def test_the_controller_bff_does_not_mint_an_identity(cleanenv, app_module):
    """The BFF has its own copy of the check, with its own flag."""
    with app_module("apps/controller", "webgui.server") as mod:
        assert mod._sso_identity(_Req(dict(FORGED))) is None, (
            "the Controller's BFF accepted a forged identity with no secret")


def test_the_updater_refuses_everything(cleanenv, app_module):
    """The updater holds the host's Docker socket. Unauthenticated access to it
    is a root shell on the machine."""
    from fastapi import HTTPException
    with app_module("updater", "backend.app") as mod:
        with pytest.raises(HTTPException) as e:
            mod._authorized(_Req(dict(FORGED)))
        assert e.value.status_code in (401, 503), e.value.status_code


# ------------------------------------------------------- the property, stated

_EXPLICIT_REFUSAL = re.compile(
    r"gateway_configured|if\s+not\s+proof|raise\s+HTTPException|return\s+(None|False)")


def test_every_gateway_secret_comparison_has_an_emptiness_guard():
    """Belt and braces for a comparison added somewhere the cases above do not
    reach — which is how a property like this actually erodes.

    THE PROPERTY, stated the way the code actually expresses it: the secret must
    be tested for truthiness somewhere OTHER than inside the compare. The four
    real forms in this codebase are

        if not secret: return False                      (Controller)
        if not (_TRUST_SSO and _SSO_SECRET): return None (Controller BFF)
        _TRUST_GATEWAY_AUTH and _SSO_SHARED_SECRET and compare_digest(...)
                                                         (SLEP BFF)
        if gateway_configured(): ...                     (the three sidecars)

    A regex tight enough to miss one of them is worse than no check, so rather
    than matching phrases this blanks out the compare_digest call itself and
    asks whether the secret's name still appears nearby. Both false alarms this
    check produced while being written — the Controller BFF and the SLEP BFF —
    were guards of a shape the earlier pattern could not see."""
    offenders = []
    for py in REPO.rglob("*.py"):
        if any(p in py.parts for p in ("node_modules", "tests", ".git", "venv")):
            continue
        try:
            lines = py.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:                                      # pragma: no cover
            continue
        for i, line in enumerate(lines):
            if "compare_digest" not in line or "x-sysible-auth" not in line.lower():
                continue
            # The window runs PAST the compare, because the call is wrapped
            # across lines in two places and the secret sits on the second one.
            window = "\n".join(lines[max(0, i - 14):i + 4])
            # Blank the comparison itself — across newlines — so a secret named
            # INSIDE it is never mistaken for the secret being tested.
            blanked = re.sub(r"compare_digest\(.*?\)\s*\)?", "COMPARE", window, flags=re.S)
            guarded = bool(_EXPLICIT_REFUSAL.search(blanked))
            # ...or any secret-shaped name tested in its own right nearby.
            guarded = guarded or bool(re.search(r"[A-Za-z_]*(SECRET|secret)[A-Za-z_]*", blanked))
            if not guarded:
                offenders.append("%s:%d" % (py.relative_to(REPO), i + 1))
    assert not offenders, (
        'these compare the gateway secret without testing it for emptiness '
        'anywhere nearby. An unset secret makes compare_digest("", "") true and '
        "authenticates everyone:\n  " + "\n  ".join(offenders))
