"""Loading one app's modules must not decide what they mean for the rest of the run.

Every SLOP app ships its own top-level `backend` package, and the IdP its own
top-level `app`. So the first test to `sys.path.insert` its app directory and
import binds that name in `sys.modules` for the whole pytest process, and a
later suite asking for *its* `backend` silently gets the first one's. Nothing
warns. The import succeeds and returns the wrong package, so the failure lands
in a test that never mentioned the app that broke it — `updater/tests` failing
with "cannot import name 'apps' from 'backend' (visualizer/backend/__init__.py)".

`app_module` is the only way these suites should reach into an app: it puts
`sys.path` and every affected `sys.modules` entry back the way it found them, so
each import is fresh and none of them leaks.
"""
import contextlib
import importlib
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _sweep(root):
    """Everything bound under a top-level module name, so a fresh import of it
    re-executes its submodules too."""
    return {k: v for k, v in sys.modules.items()
            if k == root or k.startswith(root + ".")}


@contextlib.contextmanager
def load(app, dotted):
    """Import `dotted` out of <repo>/<app>, then undo every trace of having done so.

    >>> with load("visualizer", "backend.sources") as sources: ...
    """
    root = dotted.split(".")[0]
    saved_path, saved_mods = list(sys.path), _sweep(root)
    for name in saved_mods:
        del sys.modules[name]
    sys.path.insert(0, str(REPO / app))
    try:
        yield importlib.import_module(dotted)
    finally:
        for name in _sweep(root):
            del sys.modules[name]
        sys.modules.update(saved_mods)
        sys.path[:] = saved_path


@pytest.fixture
def app_module():
    return load
