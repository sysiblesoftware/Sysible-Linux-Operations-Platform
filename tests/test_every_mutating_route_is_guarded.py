"""Every route that CHANGES something must establish who is asking.

SLOP is 579 HTTP routes across eight backends, 329 of which mutate state. No
amount of reading finds the one somebody adds next month without the
dependency — so this walks the AST of every module and resolves each route's
guard mechanically.

HOW THIS WAS CALIBRATED, because that is the whole difficulty. The first
version reported 117 unguarded routes. Every one was a false alarm, and each
round of fixing the detector taught it a guard idiom that genuinely exists in
this codebase:

    117  ->  it only looked at signature defaults, and missed
             @app.delete(..., dependencies=[Depends(require_api_key)])
             and agent-credential checks called in the body (verify_agent)
     85  ->  the Controller's BFF uses require_login_changed / _as_admin,
             neither of which was in a list I had written from memory
     38  ->  every app has its OWN body-level idiom: baseline _require,
             updater _authorized, flashback _require_identity, idp _access_guard
     13  ->  _authorize_exec, _require_writer, _check_terminal_owner,
             _reject_auditor, portal_auth.validate_session
      0

A census that cries wolf is worse than none: the real hole hides in the noise.
So the vocabulary below is HARVESTED FROM THE SOURCE (every name this codebase
passes to Depends(), plus the body helpers beside them), not invented — and the
handful of genuinely public routes are listed one by one with a reason, because
"it looked fine" is not one.
"""
import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
COMPONENTS = ("idp", "updater", "baseline", "visualizer", "flashback",
              "apps/controller", "apps/slep", "apps/connect")
MUTATING = {"post", "put", "patch", "delete", "websocket"}

# Names that establish WHO is asking. Every one appears in the source; adding a
# name here is a claim that it authenticates, and should be made deliberately.
GUARDS = {
    # browser identity, asserted by the gateway
    "current_user", "require_operator", "require_superuser", "require_admin",
    "require_role", "require_login", "require_login_changed",
    "require_superuser_session", "require_activity_viewer", "acting_admin_name",
    "_session_or_401", "_require_super", "_require_platform_admin", "_current",
    "current", "identity", "_identity", "_as_admin", "_as_superuser",
    "_require_login", "_access_guard", "validate_session",
    # api / agent / machine credentials
    "require_api_key", "require_api_key_or_gateway", "verify_agent",
    "_verify_agent", "require_agent", "verify_agent_secret", "_require_agent",
    "require_enroll_token", "require_updater_secret", "_require_secret",
    "_gateway_secret_ok", "_is_loopback",
    # each app's own body-level idiom
    "_require", "_require_read", "_authorized", "_require_identity",
    "_require_writer", "_authorize_exec", "_reject_auditor",
    "_check_terminal_owner", "require_remote_file_access",
    # a websocket bypasses the Depends() machinery, so Connect's terminal
    # applies gateway trust itself: origin allowlist, then identity, then role
    "gateway_identity", "_ws_origin_ok", "session_user",
    # per-object authorization, which implies identity was resolved first
    "_guard_project", "_require_org", "_guard_object_org", "_visible_org_ids",
}

# Public BY DESIGN. Each needs a reason that survives being read aloud.
PUBLIC = {
    "/healthz": "liveness probe",
    "/health": "liveness probe",
    "/api/health": "liveness probe",
    "/login": "the login page itself",
    "/api/login": "the login endpoint itself; it IS the authenticator",
    "/logout": "ends a session — refusing one without a session achieves nothing",
    "/api/logout": "ends a session — refusing one without a session achieves nothing",
    "/auth/verify": "the gateway asks this; it IS the authenticator",
    "/auth/me": "answers 401 when not signed in",
    "/setup": "first-run bootstrap; the backstop is that it 409s once an admin exists",
    "/api/admin/setup": "first-run bootstrap; 409 once an admin exists, and refused "
                        "outright when SLOP owns identity",
    "/agents/enroll": "an agent enrolling presents an enrollment token in the body, "
                      "which is the credential — there is nothing earlier to check",
}


def _names(node):
    return ({n.id for n in ast.walk(node) if isinstance(n, ast.Name)} |
            {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)})


def _routes_in(path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:                                    # pragma: no cover
        return
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            call = dec if isinstance(dec, ast.Call) else None
            fn = call.func if call else dec
            if not isinstance(fn, ast.Attribute):
                continue
            method = fn.attr.lower()
            if method not in MUTATING and method not in ("get", "head", "options"):
                continue
            if not (isinstance(fn.value, ast.Name) and fn.value.id in ("app", "router")):
                continue
            route = ""
            if call and call.args and isinstance(call.args[0], ast.Constant):
                route = str(call.args[0].value)
            found = set()
            if call:                                       # dependencies=[Depends(...)]
                for kw in call.keywords:
                    found |= _names(kw.value) & GUARDS
            found |= _names(node.args) & GUARDS            # signature defaults
            for n in ast.walk(node):                       # body calls
                if isinstance(n, ast.Call):
                    f = n.func
                    nm = (f.id if isinstance(f, ast.Name)
                          else f.attr if isinstance(f, ast.Attribute) else "")
                    if nm in GUARDS:
                        found.add(nm)
            yield {"file": str(path.relative_to(REPO)), "line": node.lineno,
                   "method": method.upper(), "route": route, "func": node.name,
                   "mutating": method in MUTATING, "guards": sorted(found)}


def _census():
    out = []
    for comp in COMPONENTS:
        base = REPO / comp
        if not base.is_dir():
            continue
        for py in base.rglob("*.py"):
            if "node_modules" in py.parts or "tests" in py.parts:
                continue
            if py.name.startswith("test_"):
                continue
            for r in _routes_in(py):
                r["component"] = comp
                out.append(r)
    return out


@pytest.fixture(scope="module")
def census():
    return _census()


def test_the_census_actually_found_the_platform(census):
    """A walker that silently matches nothing passes every assertion below. The
    counts are floors, not exact: routes get added."""
    assert len(census) > 500, "only found %d routes — the walker is broken" % len(census)
    assert sum(1 for r in census if r["mutating"]) > 300
    comps = {r["component"] for r in census}
    for expected in ("idp", "apps/controller", "apps/slep", "apps/connect",
                     "baseline", "flashback", "updater", "visualizer"):
        assert expected in comps, "no routes found in %s" % expected


def test_no_mutating_route_is_unguarded(census):
    """THE assertion. Everything else here exists to make this one trustworthy."""
    bad = [r for r in census
           if r["mutating"] and not r["guards"] and r["route"] not in PUBLIC]
    assert not bad, (
        "these routes change state without establishing who is asking:\n  "
        + "\n  ".join("%-7s %-44s %s:%d" % (r["method"], r["route"], r["file"], r["line"])
                      for r in sorted(bad, key=lambda x: (x["component"], x["line"])))
        + "\n\nIf one of these IS guarded, the guard's name belongs in GUARDS — add "
          "it deliberately, because naming it here asserts that it authenticates. "
          "If it is public on purpose, add it to PUBLIC with the reason.")


def test_every_public_route_has_a_stated_reason():
    """An allowlist whose entries nobody has to justify grows until it is the
    whole application."""
    for route, why in PUBLIC.items():
        assert why and len(why) > 10, "%s is allowlisted with no real reason" % route


def test_the_public_allowlist_has_no_dead_entries(census):
    """An entry for a route that no longer exists is a hole held open for
    nothing — and the next route to take that path inherits it silently."""
    live = {r["route"] for r in census}
    dead = [r for r in PUBLIC if r not in live]
    assert not dead, "allowlisted but no such route any more: %s" % dead


def test_nothing_public_is_a_delete(census):
    """A public GET is a disclosure question. A public DELETE is not survivable,
    whatever the reason given."""
    for r in census:
        if r["route"] in PUBLIC and r["method"] == "DELETE":
            pytest.fail("%s %s is on the public allowlist" % (r["method"], r["route"]))


@pytest.mark.parametrize("component", COMPONENTS)
def test_each_component_is_clean_on_its_own(component, census):
    """Named per component so a failure says which app regressed, rather than
    one wall of text for the platform."""
    bad = [r for r in census if r["component"] == component
           and r["mutating"] and not r["guards"] and r["route"] not in PUBLIC]
    assert not bad, "%s: %s" % (component, [r["route"] for r in bad])
