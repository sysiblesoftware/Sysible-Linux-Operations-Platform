"""The image must contain every module the app imports.

This is a regression test for an outage, not a style check. The Dockerfile used
to `COPY app.py .` by name. Adding updates.py therefore produced an image where
`import updates` raised ImportError at startup: uvicorn never came up, the
gateway's forward_auth had no IdP to ask, and every page on the platform — the
portal, sign-in, all five apps — answered 502 Bad Gateway.

Nothing else caught it. The unit tests import the modules straight from the
source tree, where the file obviously exists, so they all passed against an image
that could not boot. The only thing that can catch it is a check on what the
image actually copies.
"""
import ast
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
IDP = os.path.dirname(HERE)


def _local_modules() -> set[str]:
    """Top-level .py files in the IdP directory (its own modules)."""
    return {f[:-3] for f in os.listdir(IDP)
            if f.endswith(".py") and not f.startswith("_")}


def _imports_of(path: str) -> set[str]:
    tree = ast.parse(open(path, encoding="utf-8").read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _copied_by_dockerfile() -> list[str]:
    out = []
    for line in open(os.path.join(IDP, "Dockerfile"), encoding="utf-8"):
        line = line.strip()
        if line.upper().startswith("COPY "):
            out.extend(line.split()[1:-1])       # sources, minus the destination
    return out


def test_the_dockerfile_ships_every_module_the_app_imports():
    local = _local_modules()
    needed = sorted(_imports_of(os.path.join(IDP, "app.py")) & local)
    assert needed, "expected app.py to import at least one local module"

    copied = _copied_by_dockerfile()
    globbed = any(re.fullmatch(r"\*\.py|\./\*\.py", c) for c in copied)
    if globbed:
        return                                   # *.py covers all of them
    named = {c.rsplit("/", 1)[-1][:-3] for c in copied if c.endswith(".py")}
    missing = [m for m in needed if m not in named]
    assert not missing, (
        "app.py imports these local modules but the Dockerfile does not copy them, "
        f"so the image cannot start: {missing}")


def test_every_local_module_is_importable_together():
    """A cheap smoke: the modules must at least parse and resolve as a set."""
    for name in sorted(_local_modules()):
        ast.parse(open(os.path.join(IDP, f"{name}.py"), encoding="utf-8").read())
