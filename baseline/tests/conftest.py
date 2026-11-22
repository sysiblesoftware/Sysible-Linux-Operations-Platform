"""Make the browser tests actually run.

Every browser test in this suite reads SYSIBLE_TEST_CHROMIUM and skips when it
is unset. Nothing sets it -- not the Dockerfile, not CI, not the dev shell --
so all 125 of them skipped on every machine they had ever run on. They were
green because they never executed. The same shape hid nine real bugs in the
Controller's suite earlier.

So resolve the browser here, once, the way the Controller's tests do: look
where a browser is actually installed. SYSIBLE_TEST_CHROMIUM still wins when it
is set, so an operator can still point the suite at a specific build.
"""
import os
import shutil

_CANDIDATES = (
    "/opt/pw-browsers/chromium",        # the image's pre-installed build
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
)


def _find_chromium():
    for path in _CANDIDATES:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return shutil.which("chromium") or shutil.which("google-chrome")


_found = _find_chromium()
if _found:
    # setdefault, NOT assignment: an explicit override stays in charge.
    os.environ.setdefault("SYSIBLE_TEST_CHROMIUM", _found)
