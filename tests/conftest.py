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


# ---- the browser these suites drive ----------------------------------------
# Every browser test here reads SYSIBLE_TEST_CHROMIUM and skips when it is
# unset. Nothing set it -- not the Dockerfile, not CI, not the dev shell -- so
# those tests skipped on every machine they had ever run on. They were green
# because they never executed, and test_portal_health_dots is the test that
# would have caught the portal showing red dots for healthy services.
#
# Resolved here the way idp/tests/_browser.py does it: look where a browser is
# actually installed, and let an explicit SYSIBLE_TEST_CHROMIUM still win so an
# operator can point the suite at a specific build. Each suite carries its own
# copy because flashback and baseline are vendored into other repositories and
# have to stand alone there.
import os
import shutil

_CHROMIUM_CANDIDATES = (
    "/opt/pw-browsers/chromium",        # the image's pre-installed build
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
)


def _resolve_chromium():
    for cand in _CHROMIUM_CANDIDATES:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    for name in ("chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


_chromium = _resolve_chromium()
if _chromium:
    # setdefault, NOT assignment: an explicit override stays in charge.
    os.environ.setdefault("SYSIBLE_TEST_CHROMIUM", _chromium)
