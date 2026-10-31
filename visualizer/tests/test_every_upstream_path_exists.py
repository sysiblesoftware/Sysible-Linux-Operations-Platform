"""Every path this console calls is a route the service on the other end has.

The Visualizer reads five services directly on their own ports. Nothing checks
that the paths it builds are paths those services serve, and the failure is
quiet: an unknown path answers 404, the panel shows "runs: HTTP 404" or just a
note, and the reasonable reading is "that app has nothing to show".

SLEP's whole panel had never worked. Its API has NO /api prefix — the routes
are /runs, /audit and /runs/{id}/log on its own port — and this file asked for
/api/runs and /api/audit. The gateway strips /slep for a browser; a service
talking to the upstream directly gets no such help, and nobody noticed because
an empty activity panel looks like a quiet day.

This test reads the ROUTES OUT OF THE OTHER REPOS. It is skipped where a repo
is not checked out beside this one, which means it is not a guarantee on every
machine — but on a machine with the platform checked out it is the only thing
that would have caught this.

Run: pytest visualizer/tests/test_every_upstream_path_exists.py
"""
import os
import pathlib
import re
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
SLOP = HERE.parent
SIBLINGS = SLOP.parent

from backend import sources                                  # noqa: E402

# Where each upstream's routes are declared.
SERVICES = {
    "_CONTROLLER": [SIBLINGS / "Sysible-Controller" / "webgui" / "server.py"],
    "_SLEP": list((SIBLINGS / "sysible-linux-engineering-platform" / "backend")
                  .glob("*.py")) if (SIBLINGS / "sysible-linux-engineering-platform"
                                     / "backend").is_dir() else [],
    "_FLASHBACK": [SLOP / "flashback" / "backend" / "app.py"],
    "_BASELINE": [SLOP / "baseline" / "backend" / "app.py"],
    # Connect is not part of this checkout.
    "_CONNECT": [],
}

ROUTE = re.compile(r'@(?:app|router)\.(?:get|post|put|delete|patch)\(\s*"([^"]+)"')
CALL_DIRECT = re.compile(r'_url\((_[A-Z]+),\s*"https?"\)\s*\+\s*f?"([^"]+)"')
CALL_BASE = re.compile(r'_get\(f"\{base\}([^"]+)"')
BASE_SET = re.compile(r'base = _url\((_[A-Z]+)')


def _norm(p):
    p = re.sub(r"\{[^}]*\}", "*", p)
    return "/" + p.strip("/").split("?")[0]


def _declared(service):
    out = set()
    for f in SERVICES.get(service, []):
        if not f.is_file():
            continue
        for m in ROUTE.findall(f.read_text(encoding="utf-8", errors="ignore")):
            out.add(_norm(m))
    return out


def _calls():
    """(service, path) for every upstream request this module makes."""
    text = (HERE / "backend" / "sources.py").read_text(encoding="utf-8")
    found, cur = [], None
    for line in text.splitlines():
        b = BASE_SET.search(line)
        if b:
            cur = b.group(1)
            continue
        d = CALL_DIRECT.search(line)
        if d:
            found.append((d.group(1), d.group(2)))
            continue
        c = CALL_BASE.search(line)
        if c and cur:
            found.append((cur, c.group(1)))
    return found


def test_this_console_actually_calls_several_upstreams():
    """A guard on the guard: if the extraction stops matching, every test below
    passes by finding nothing."""
    calls = _calls()
    assert len(calls) >= 7, f"only found {len(calls)} upstream calls: {calls}"
    assert len({s for s, _ in calls}) >= 4, calls


@pytest.mark.parametrize("service,path", _calls())
def test_the_path_is_a_route_that_service_serves(service, path):
    if not SERVICES.get(service):
        pytest.skip(f"{service} is not checked out beside this repo")
    declared = _declared(service)
    assert declared, f"found no routes at all for {service}"
    want = _norm(path)
    # A path with an interpolated segment normalises to *; match loosely.
    ok = any(want == d or re.fullmatch(d.replace("*", "[^/]+"), want)
             or re.fullmatch(want.replace("*", "[^/]+"), d) for d in declared)
    assert ok, (
        f"{service} has no route {want} — this console calls it anyway, and a "
        f"404 here shows up as an empty panel rather than an error. "
        f"Nearest: {sorted(d for d in declared if d.split('/')[1:2] == want.split('/')[1:2])[:6]}")


def test_slep_is_addressed_without_an_api_prefix():
    """The specific one. SLEP serves /runs and /audit at the root of its port;
    /api/runs matched nothing and the panel read as an idle SLEP."""
    for service, path in _calls():
        if service == "_SLEP":
            assert not path.startswith("/api/"), (
                f"SLEP has no /api prefix, but this calls {path}")
