"""Make the browser tests actually run; see the block below."""


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
