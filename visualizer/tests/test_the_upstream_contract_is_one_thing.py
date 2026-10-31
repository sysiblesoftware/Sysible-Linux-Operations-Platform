"""SLOP_*_UPSTREAM means HOST:PORT to every reader of it.

The platform has two: gateway/Caddyfile, which writes the scheme in front of
the value at each import site, and this module. _url() used to accept a scheme
INSIDE the value and document it as the way to address an app running with TLS
off — so the same variable meant two different things.

Setting SLOP_SLEP_UPSTREAM=http://host:8810 produced
https://http://host:8810 in the Caddyfile, and Caddy refuses to adapt that:

    parsing upstream 'https://http://host.docker.internal:8810': for now, URLs
    for proxy upstreams only support scheme, host, and port components

The gateway does not start, and the error does not mention the scheme. So the
contract is the one the gateway can actually keep; a scheme in the value is
stripped here and reported at startup, rather than honoured by the one
component that could.
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

from backend import sources  # noqa: E402


@pytest.mark.parametrize("value,scheme,want", [
    ("host:8810", "https", "https://host:8810"),
    ("http://host:8810", "https", "https://host:8810"),
    ("https://host:8810", "http", "http://host:8810"),
    ("HTTP://host:8810", "https", "https://host:8810"),
    ("host:8810/", "https", "https://host:8810"),
    ("  host:8810  ", "http", "http://host:8810"),
])
def test_the_scheme_comes_from_the_caller_not_the_value(value, scheme, want):
    assert sources._url(value, scheme) == want


def test_a_scheme_in_the_value_is_reported(capfd, monkeypatch):
    monkeypatch.setenv("SLOP_SLEP_UPSTREAM", "http://host.docker.internal:8810")
    importlib.reload(sources)
    out = capfd.readouterr().out
    assert "SLOP_SLEP_UPSTREAM" in out and "HOST:PORT" in out, out


def test_nothing_is_reported_for_a_correct_value(capfd, monkeypatch):
    monkeypatch.setenv("SLOP_SLEP_UPSTREAM", "host.docker.internal:8810")
    importlib.reload(sources)
    assert "carries a scheme" not in capfd.readouterr().out


def test_the_gateway_really_does_refuse_a_scheme():
    """Pins WHY the contract is this way round, against the real caddy."""
    caddy = os.environ.get("CADDY_BIN") or "/tmp/caddybin/caddy"
    if not os.path.exists(caddy):
        pytest.skip("no caddy binary")
    env = dict(os.environ, SYSIBLE_SSO_SHARED_SECRET="s1", SLOP_DOMAIN="slop.test",
               SYSIBLE_BASELINE_GATEWAY_SECRET="b1",
               SLOP_SLEP_UPSTREAM="http://host.docker.internal:8810")
    r = subprocess.run([caddy, "validate", "--config", "Caddyfile",
                        "--adapter", "caddyfile"],
                       cwd=str(ROOT / "gateway"), env=env, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode != 0
    assert "https://http://" in (r.stdout + r.stderr)


def test_the_example_env_states_the_contract():
    text = (ROOT / ".env.example").read_text()
    i = text.index("SLOP_CONTROLLER_UPSTREAM=")
    assert "HOST:PORT" in text[max(0, i - 600):i], \
        ".env.example should say these carry no scheme"
