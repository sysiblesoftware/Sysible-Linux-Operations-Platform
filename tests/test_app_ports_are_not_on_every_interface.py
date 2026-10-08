"""Behind SLOP, an app's console must not also be listening on the network.

SLOP is meant to be the front door, and each app ALSO published its console on
0.0.0.0 — so the gateway's HSTS/CSP/frame headers and the platform's central
login throttle could be skipped by going straight to :8800 / :8810 / :8700.
(Not an authentication bypass: the apps fail closed without the shared secret and
SLEP refuses a local login under SSO. Surface area, not a way in.)

connect's own compose has said "the SLOP installer wires this up" since before
the installer did. Now it does.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")

APPS = {
    "controller": "SYSIBLE_CONTROLLER_CONSOLE_BIND",
    "slep": "SYSIBLE_SLEP_BIND",
    "connect": "SYSIBLE_CONNECT_BIND",
}


@pytest.mark.parametrize("app,var", sorted(APPS.items()))
def test_the_installer_binds_each_console(app, var):
    assert var in INSTALL, f"{app}'s console bind is never set by the installer"
    block = INSTALL[INSTALL.index("case \"$p\" in"):]
    assert f"{app})" in block.split("esac")[0], f"{app} is not in the bind table"


def test_it_binds_to_the_bridge_the_gateway_actually_uses():
    """Loopback would be wrong: Caddy reaches the apps through
    host.docker.internal, which resolves to the bridge gateway."""
    assert "_bridge_gateway()" in INSTALL
    assert '_upsert_kv "$_aenv" "$_bind_var" "$FB_BIND"' in INSTALL


def test_the_controller_api_port_is_left_alone():
    """9000 is the agent + CLI API. Managed hosts across the network dial it,
    so binding it would strand the whole fleet. Only the CONSOLE is restricted."""
    assert "SYSIBLE_CONTROLLER_CONSOLE_BIND" in INSTALL
    assert "SYSIBLE_CONTROLLER_API_BIND" not in INSTALL, \
        "the agent API port was restricted too"
    assert "9000" not in INSTALL.split('case "$p" in')[1].split("esac")[0], \
        "the bind table touches the agent API port"


def test_the_bridge_address_is_asked_for_not_assumed():
    """A host with a customised default bridge subnet has a different gateway,
    and guessing fails silently."""
    assert "docker network inspect bridge" in INSTALL
    assert '172.17.0.1' in INSTALL, "no fallback if docker cannot be asked"
