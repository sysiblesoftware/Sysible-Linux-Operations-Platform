"""What may talk to what, stated as the edges that must NOT exist.

SLOP is nine components in one repository, which makes `from baseline.backend
import store` a two-second convenience and a permanent coupling. The boundary
that is actually designed — and that every security property here rests on — is
that components talk to each other over HTTP, through the gateway, carrying an
identity the receiver verifies.

An import bypasses all of it: no gateway, no shared secret, no role check, no
audit entry, and the receiving app's own authorization never runs. A direct
sqlite open is the same thing with the schema coupled too.

Both are clean today. Neither was asserted anywhere, and neither would look
wrong in review — which is the whole reason to write it down.

The positive edges are deliberately NOT pinned here. Which component calls which
endpoint changes as features move; what must not change is the mechanism.
"""
import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

COMPONENTS = {
    "idp": REPO / "idp",
    "updater": REPO / "updater",
    "baseline": REPO / "baseline",
    "visualizer": REPO / "visualizer",
    "flashback": REPO / "flashback",
    "controller": REPO / "apps" / "controller",
    "slep": REPO / "apps" / "slep",
    "connect": REPO / "apps" / "connect",
}

# Top-level module names that belong to a component. Importing one of these from
# OUTSIDE its own directory is the edge this file forbids.
OWNED_NAMES = {"idp", "updater", "baseline", "visualizer", "flashback",
               "controller", "slep", "connect"}


def _py_files(root):
    for p in root.rglob("*.py"):
        if any(part in ("node_modules", ".git", "tests", "__pycache__", "venv", ".venv")
               for part in p.parts):
            continue
        if p.name.startswith("test_"):
            continue
        yield p


def _imports(path):
    """Top-level module name of every import in the file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:                                      # pragma: no cover
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                                   # relative: within the component
                continue
            if node.module:
                yield node.module.split(".")[0]


def test_the_components_are_all_here():
    """A walk that finds nothing passes everything below."""
    for name, root in COMPONENTS.items():
        assert root.is_dir(), "%s is missing from %s" % (name, root)
        assert any(_py_files(root)), "no python found in %s" % name


@pytest.mark.parametrize("name", sorted(COMPONENTS))
def test_no_component_imports_another(name):
    """The edge that bypasses everything: no gateway, no shared secret, no role
    check, no audit entry, and the other app's authorization never runs."""
    root = COMPONENTS[name]
    others = OWNED_NAMES - {name}
    offenders = []
    for py in _py_files(root):
        for mod in _imports(py):
            if mod in others:
                offenders.append("%s imports %r" % (py.relative_to(REPO), mod))
    assert not offenders, (
        "%s reaches into another component directly instead of calling it over "
        "HTTP:\n  " % name + "\n  ".join(offenders))


@pytest.mark.parametrize("name", sorted(COMPONENTS))
def test_no_component_opens_another_components_database(name):
    """Same coupling, with the schema attached. Each component owns its store;
    a reader that bypasses the API also bypasses every check the API makes."""
    root = COMPONENTS[name]
    others = OWNED_NAMES - {name}
    offenders = []
    for py in _py_files(root):
        text = py.read_text(encoding="utf-8", errors="replace")
        if "sqlite3.connect" not in text and "sqlite3.Connection" not in text:
            continue
        for line in text.splitlines():
            if ".sqlite" not in line and ".db" not in line:
                continue
            # `sqlite3.connect(...)` contains the component name "connect", which
            # made every Controller database open look like a cross-component
            # read. Blank the call before matching.
            probe = line.replace("sqlite3.connect", "").replace(".connect(", "(")
            for other in others:
                if other in probe:
                    offenders.append("%s: %s" % (py.relative_to(REPO), line.strip()[:90]))
    assert not offenders, (
        "%s opens another component's database:\n  " % name + "\n  ".join(offenders))


# ------------------------------------------------- the secrets that isolate

def test_the_two_components_that_need_their_own_secret_get_one():
    """Baseline's console port and the updater's Docker socket are both reachable
    on the docker bridge, and the platform-wide secret is held by every service
    on it — so a flaw in any one of them would be one hop from a forged Baseline
    superuser, or from the host's Docker socket.

    Both therefore have a dedicated secret. The isolation is only real if an
    install actually GENERATES them, which is what this checks: without that,
    every deployment runs on the documented fallback and the split exists only
    in the comments."""
    install = (REPO / "install.sh").read_text(encoding="utf-8")
    for var in ("SYSIBLE_UPDATER_SECRET", "SYSIBLE_BASELINE_GATEWAY_SECRET"):
        assert var in install, "install.sh never sets %s" % var
        assert "_upsert_env %s" % var in install, (
            "install.sh does not PERSIST %s into the .env, so it would be "
            "regenerated (or lost) on the next run" % var)


def test_the_fallback_announces_itself():
    """Falling back to the platform-wide secret is allowed — an install that has
    not re-run install.sh must keep working rather than lock its operators out —
    but it restores the hop, so it has to say so rather than degrade quietly."""
    for mod in (REPO / "baseline" / "backend" / "identity.py",
                REPO / "updater" / "backend" / "app.py"):
        text = mod.read_text(encoding="utf-8")
        assert "_SHARED_FALLBACK" in text, "%s has no fallback flag" % mod.name
        assert "PLATFORM-WIDE" in text, (
            "%s falls back to the shared secret without saying so at startup" % mod.name)
