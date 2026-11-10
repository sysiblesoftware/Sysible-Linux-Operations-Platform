"""Where the Chromium that drives these tests lives.

Every browser-driven test in this directory read SYSIBLE_TEST_CHROMIUM and, when
it was unset, let Playwright pick for itself. Playwright's own default is the
headless SHELL build, which is a separate download from the full browser — so on
a machine that has Chromium but not that shell, launch() failed with "Executable
doesn't exist" and every one of these tests reported SKIPPED. Green, and nothing
had been driven.

A skip for "no browser here" is right. A skip because the browser is at the path
Playwright did not guess is not, and it is invisible: the whole point of these
tests is that they click the thing an operator clicks.
"""
import os
import shutil

# Paths a Chromium is commonly installed to, in the order to try. The env var
# still wins, so a developer can point at their own build.
_CANDIDATES = (
    "/opt/pw-browsers/chromium",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
)


def chromium_path():
    """An explicit executable for p.chromium.launch(), or None to let Playwright
    choose. Returns None rather than raising: whether a missing browser is a skip
    or a failure is the test's call, not this helper's."""
    env = (os.environ.get("SYSIBLE_TEST_CHROMIUM") or "").strip()
    if env:
        return env
    for cand in _CANDIDATES:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    for name in ("chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None
