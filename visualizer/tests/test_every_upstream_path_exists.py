"""Every path this console calls is one the service on the other end serves.

The Visualizer reads five services directly on their own ports. Nothing checked
that the paths it builds exist there, and the failure mode is quiet: an unknown
path answers 404 or, worse, HTML, the panel shows a short note, and the
reasonable reading is "that app has nothing to show today".

THE SLEP HOP IS NOT WHAT IT LOOKS LIKE, and this test exists mostly to record
it. :8810 is SLEP's web CONSOLE, not its API. The console serves exactly three
routes:

    /api/health          — answered locally
    /api/{path:path}     — proxied to the backend on :9100 WITH THE /api PREFIX
                           STRIPPED, because the backend has no /api
    /{path:path}         — SPA fallback: serves index.html

So `/api/runs` here reaches the backend's `/runs` and is correct, and `/runs`
here does NOT reach the backend's `/runs` — it hits the SPA fallback and comes
back as a page of HTML. Reading the backend's routes alone says the opposite,
convincingly, and the "fix" that follows from it silently replaces a working
call with one that returns markup to a JSON parser.

Run: pytest visualizer/tests/test_every_upstream_path_exists.py
"""
import pathlib
import re
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
SLOP = HERE.parent
SIBLINGS = SLOP.parent
SLEP = SIBLINGS / "sysible-linux-engineering-platform"

ROUTE = re.compile(r'@(?:app|router)\.(?:get|post|put|delete|patch|api_route)\(\s*"([^"]+)"')
CALL_DIRECT = re.compile(r'_url\((_[A-Z]+),\s*"https?"\)\s*\+\s*f?"([^"]+)"')
CALL_BASE = re.compile(r'_get\(f"\{base\}([^"]+)"')
BASE_SET = re.compile(r'base = _url\((_[A-Z]+)')

# Which file declares the routes each upstream answers on the port we dial, and
# whether that port strips an /api prefix before handling.
UPSTREAMS = {
    "_CONTROLLER": (SIBLINGS / "Sysible-Controller" / "webgui" / "server.py", False),
    "_SLEP": (SLEP / "backend" / "app.py", True),
    "_FLASHBACK": (SLOP / "flashback" / "backend" / "app.py", False),
    "_BASELINE": (SLOP / "baseline" / "backend" / "app.py", False),
    "_CONNECT": (None, False),          # not part of this checkout
}


def _norm(p):
    return "/" + re.sub(r"\{[^}]*\}", "*", p).strip("/").split("?")[0]


def _declared(path):
    if path is None or not path.is_file():
        return set()
    return {_norm(m) for m in ROUTE.findall(path.read_text(encoding="utf-8", errors="ignore"))}


def _calls():
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
def test_the_path_reaches_a_real_route(service, path):
    source, strips_api = UPSTREAMS.get(service, (None, False))
    if source is None or not source.is_file():
        pytest.skip(f"{service} is not checked out beside this repo")
    want = _norm(path)
    if strips_api:
        # The console on this port forwards /api/<rest> to the backend as
        # /<rest>. A path that does NOT start with /api never reaches the
        # backend at all — it is answered by the SPA fallback with HTML.
        assert want.startswith("/api/"), (
            f"{service} is reached through a console that only proxies /api/*; "
            f"{want} would be answered by its SPA fallback with a page of HTML, "
            f"which this console would then try to parse as JSON")
        want = "/" + want[len("/api/"):]
    declared = _declared(source)
    assert declared, f"found no routes at all in {source}"
    ok = any(want == d or re.fullmatch(d.replace("*", "[^/]+"), want)
             or re.fullmatch(want.replace("*", "[^/]+"), d) for d in declared)
    assert ok, (
        f"{service} serves no route {want} ({source.name}). A 404 here shows up "
        f"as an empty panel, not an error. Nearest: "
        f"{sorted(d for d in declared if d.split('/')[1:2] == want.split('/')[1:2])[:6]}")


def test_the_slep_console_proxy_still_strips_the_api_prefix():
    """The assumption the SLEP rows above rest on. If that proxy is ever changed
    or removed, every SLEP call here starts returning the SPA's HTML and this is
    where it should be noticed."""
    if not (SLEP / "webgui" / "server.py").is_file():
        pytest.skip("SLEP is not checked out beside this repo")
    src = (SLEP / "webgui" / "server.py").read_text(encoding="utf-8")
    assert '@app.api_route("/api/{path:path}"' in src, \
        "SLEP's console no longer proxies /api/* to its backend"
    assert re.search(r'url\s*=\s*f"\{BACKEND\}/\{path\}"', src), \
        "SLEP's console no longer strips the /api prefix when it proxies"
