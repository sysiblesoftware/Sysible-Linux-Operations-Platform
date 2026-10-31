"""A SLOP service with gateway trust OFF answers as a READ-ONLY identity.

Baseline, Flashback and the Visualizer each fall back to a single local identity
when no gateway trust is configured. That mode is unauthenticated by definition
— it exists so the service is usable on a laptop — and all three defaulted it to
`superuser`. So a service started without trust answered every caller as a full
administrator: a laptop run, a `.env` that lost its trust flag, a container
brought up by hand, or the install that deploy/sysiblectl exists partly to
recover from.

Read-only costs a developer one environment variable; it costs an accident
nothing. Production is unaffected — docker-compose.yml sets
TRUST_GATEWAY_AUTH=1 for all three, so the value is never consulted there, which
is also why no test noticed.

Checked at source level because the three services each ship their own `backend`
package and cannot share one pytest process; one of them is then exercised for
real.
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

APPS = {
    "baseline": (ROOT / "baseline" / "backend" / "identity.py", "SYSIBLE_BASELINE_LOCAL_ROLE"),
    "flashback": (ROOT / "flashback" / "backend" / "identity.py", "SYSIBLE_FLASHBACK_LOCAL_ROLE"),
    "visualizer": (ROOT / "visualizer" / "backend" / "identity.py", "SYSIBLE_VISUALIZER_LOCAL_ROLE"),
}


@pytest.mark.parametrize("app", sorted(APPS))
def test_the_default_local_role_is_read_only(app):
    src, _var = APPS[app]
    m = re.search(r'_LOCAL_ROLE\s*=\s*[\w.]+\(\s*"[A-Z_]*LOCAL_ROLE"\s*,\s*"(\w+)"\s*\)',
                  src.read_text())
    assert m, f"could not find the _LOCAL_ROLE default in {src}"
    assert m.group(1) == "auditor", f"{app} defaults its unauthenticated identity to {m.group(1)!r}"


@pytest.mark.parametrize("app", sorted(APPS))
def test_the_startup_notice_says_how_to_raise_it(app):
    src, var = APPS[app]
    text = src.read_text()
    i = text.index("def startup_notice")
    notice = text[i:i + 700]
    assert "UNAUTHENTICATED" in notice
    assert var in notice, f"{app}'s notice should name {var} so a dev knows the lever"


@pytest.mark.parametrize("app", sorted(APPS))
def test_production_never_consults_it(app):
    """The reason this is safe to change: compose turns trust on everywhere."""
    compose = (ROOT / "docker-compose.yml").read_text()
    flag = {"baseline": "SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH",
            "flashback": "SYSIBLE_FLASHBACK_TRUST_GATEWAY_AUTH",
            "visualizer": "SYSIBLE_VISUALIZER_TRUST_GATEWAY_AUTH"}[app]
    assert f'{flag}: "1"' in compose, flag


def test_one_of_them_for_real(monkeypatch):
    """Source checks can agree with each other and still be wrong about what the
    code does, so drive Baseline's module."""
    # identity.py does `from . import env`, so it has to be imported AS part of a
    # package. Each app ships its own `backend` package, so the name is loaded
    # under a private alias and removed again — a bare `import backend.identity`
    # would collide with whichever app got there first.
    for k in ("SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH", "SYSIBLE_BASELINE_LOCAL_ROLE",
              "SYSIBLE_BASELINE_GATEWAY_SECRET", "SYSIBLE_SSO_SHARED_SECRET"):
        monkeypatch.delenv(k, raising=False)
    pkg = "slop_baseline_pkg_localrole"
    pkg_spec = importlib.util.spec_from_file_location(
        pkg, APPS["baseline"][0].parent / "__init__.py",
        submodule_search_locations=[str(APPS["baseline"][0].parent)])
    pkg_mod = importlib.util.module_from_spec(pkg_spec)
    sys.modules[pkg] = pkg_mod
    if pkg_spec.loader is not None:
        pkg_spec.loader.exec_module(pkg_mod)
    monkeypatch.setitem(sys.modules, pkg, pkg_mod)
    spec = importlib.util.spec_from_file_location(pkg + ".identity", APPS["baseline"][0])
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, mod)
    spec.loader.exec_module(mod)

    class _Req:
        headers = {}

    who = mod.current(_Req())
    assert who is not None, "the local fallback identity should still exist"
    assert who.role == "auditor", who.role
    assert who.can_write is False, "an unauthenticated caller must not be able to write"
