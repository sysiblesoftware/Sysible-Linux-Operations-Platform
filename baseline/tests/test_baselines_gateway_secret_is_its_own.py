"""Baseline believes an asserted identity only on ITS OWN gateway secret.

docker-compose.yml publishes this container's :8080 on the docker bridge, so
that the Controller — a container in a different compose project, for which
`baseline:8080` does not resolve — can reach the agent API. That same port
serves the browser console. Anything else on the bridge can reach it too.

Baseline used to check the platform-wide SYSIBLE_SSO_SHARED_SECRET, which every
service on the stack carries: the IdP, the Visualizer, Flashback, the
Controller, SLEP. So a flaw in ANY of them was one hop from a forged Baseline
SUPERUSER — read the secret out of your own environment, POST to the bridge port
with X-Sysible-User and X-Sysible-Role, declare a state. A Baseline state runs
as root on every host it is assigned to, so that was root on the fleet with no
login anywhere.

SYSIBLE_BASELINE_GATEWAY_SECRET is held by three components instead of eight:
the gateway (which stamps it for /baseline only), Baseline, and the Visualizer
(which reads Baseline's run feed directly). Exactly the split, and the reasoning,
that SYSIBLE_UPDATER_SECRET already makes for the component holding the Docker
socket.

It falls back to the shared secret when unset, so an install that has not re-run
install.sh is unchanged — and says so on every start, because the fallback
restores the hop.
"""
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

SHARED = "s" * 32
OWN = "b" * 32


def _identity(**env):
    """Reload backend.identity under a given environment."""
    keep = {k: os.environ.get(k) for k in
            ("SYSIBLE_SSO_SHARED_SECRET", "SYSIBLE_BASELINE_GATEWAY_SECRET",
             "SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH")}
    try:
        for k in keep:
            os.environ.pop(k, None)
        os.environ.update({k: v for k, v in env.items() if v is not None})
        for mod in ("backend.env", "backend.identity"):
            if mod in sys.modules:
                importlib.reload(sys.modules[mod])
        from backend import identity
        return importlib.reload(identity)
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _Req:
    def __init__(self, **h):
        self.headers = {k.lower().replace("_", "-"): v for k, v in h.items()}


def _as(ident_mod, secret, user="attacker", role="superuser"):
    return ident_mod.current(_Req(x_sysible_auth=secret, x_sysible_user=user,
                                  x_sysible_role=role))


def test_its_own_secret_is_accepted():
    m = _identity(SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1",
                  SYSIBLE_SSO_SHARED_SECRET=SHARED,
                  SYSIBLE_BASELINE_GATEWAY_SECRET=OWN)
    who = _as(m, OWN, user="alice", role="operator")
    assert who is not None and who.user == "alice" and who.role == "operator"


def test_the_platform_wide_secret_is_not_enough():
    """The hop: any service on the stack holds SHARED."""
    m = _identity(SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1",
                  SYSIBLE_SSO_SHARED_SECRET=SHARED,
                  SYSIBLE_BASELINE_GATEWAY_SECRET=OWN)
    assert _as(m, SHARED) is None, (
        "a service holding only the platform-wide secret forged a superuser")


def test_it_falls_back_to_the_platform_secret_when_unset():
    """An install that has not re-run install.sh must keep working."""
    m = _identity(SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1",
                  SYSIBLE_SSO_SHARED_SECRET=SHARED)
    who = _as(m, SHARED, user="alice", role="operator")
    assert who is not None and who.user == "alice"


def test_the_fallback_is_announced(capfd):
    _identity(SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1",
              SYSIBLE_SSO_SHARED_SECRET=SHARED)
    out = capfd.readouterr().out
    assert "SYSIBLE_BASELINE_GATEWAY_SECRET" in out and "install.sh" in out, out


def test_no_warning_once_the_secrets_are_separated(capfd):
    _identity(SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1",
              SYSIBLE_SSO_SHARED_SECRET=SHARED,
              SYSIBLE_BASELINE_GATEWAY_SECRET=OWN)
    assert "falling back" not in capfd.readouterr().out


def test_no_secret_at_all_still_fails_closed():
    m = _identity(SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1")
    assert m.gateway_configured() is False
    # Trust on with nothing to verify against must not honour headers; the
    # module's documented posture is the local-dev fallback identity, never the
    # asserted one.
    who = _as(m, "", user="attacker", role="superuser")
    assert who is None or who.user != "attacker"


def test_the_agent_token_is_untouched():
    """The agent API is authenticated by its own bearer token and has nothing to
    do with this. Changing the console's secret must not change that."""
    m = _identity(SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1",
                  SYSIBLE_SSO_SHARED_SECRET=SHARED,
                  SYSIBLE_BASELINE_GATEWAY_SECRET=OWN,
                  SYSIBLE_BASELINE_AGENT_TOKEN="t" * 64)
    assert m.agent_auth_ok(_Req(authorization="Bearer " + "t" * 64)) is True
    assert m.agent_auth_ok(_Req(authorization="Bearer " + OWN)) is False


# ---------------------------------------------------------------------------
# the wiring
# ---------------------------------------------------------------------------
def test_the_gateway_stamps_baselines_secret_and_only_baselines():
    """The whole point is that /baseline gets a different value from every other
    app. Checked against the REAL adapted Caddy config, not the Caddyfile text."""
    caddy = os.environ.get("CADDY_BIN") or "/tmp/caddybin/caddy"
    if not os.path.exists(caddy):
        pytest.skip("no caddy binary to adapt the config with")
    import json
    env = dict(os.environ, SYSIBLE_SSO_SHARED_SECRET="shared-xyz",
               SYSIBLE_BASELINE_GATEWAY_SECRET="baseline-xyz",
               SLOP_DOMAIN="slop.test")
    r = subprocess.run([caddy, "adapt", "--config", "Caddyfile", "--adapter", "caddyfile"],
                       cwd=str(ROOT / "gateway"), env=env, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    stamped = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("handler") == "headers":
                v = ((o.get("request") or {}).get("set") or {}).get("X-Sysible-Auth")
                if v:
                    stamped.extend(v)
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(json.loads(r.stdout))
    assert stamped.count("baseline-xyz") == 1, stamped
    assert stamped.count("shared-xyz") >= 4, stamped


def test_compose_defaults_the_secret_to_the_shared_one():
    """So an install that has not re-run install.sh is unchanged, and so the
    three holders are exactly the gateway, Baseline and the Visualizer."""
    text = (ROOT / "docker-compose.yml").read_text()
    assert text.count("SYSIBLE_BASELINE_GATEWAY_SECRET: "
                      "${SYSIBLE_BASELINE_GATEWAY_SECRET:-"
                      "${SYSIBLE_SSO_SHARED_SECRET:-}}") == 3, \
        "expected exactly the gateway, baseline and visualizer services to carry it"


def test_the_installer_generates_and_persists_it():
    sh = (ROOT / "install.sh").read_text()
    assert "_upsert_env SYSIBLE_BASELINE_GATEWAY_SECRET" in sh
    assert 'export SYSIBLE_BASELINE_GATEWAY_SECRET=' in sh
    # 64 hex chars, validated — the same floor the other two secrets get.
    i = sh.index("BL_GW_SECRET=")
    block = sh[i:i + 1400]
    assert "openssl rand -hex 32" in block and "-eq 64" in block, block


def test_the_visualizer_sends_baselines_secret():
    src = (ROOT / "visualizer" / "backend" / "sources.py").read_text()
    assert "_BASELINE_SECRET" in src
    call = src[src.index('_url(_BASELINE, "http")'):]
    call = call[:call.index("\n    if err")]
    assert "secret=_BASELINE_SECRET" in call, call
    # …and nobody else's call does.
    for other in ('_url(_FLASHBACK, "http")', '_url(_CONNECT, "https")'):
        seg = src[src.index(other):]
        seg = seg[:seg.index("\n    if err")]
        assert "_BASELINE_SECRET" not in seg, seg
