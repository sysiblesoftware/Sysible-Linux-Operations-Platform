"""Sysible Flashback — environment variables, and the names they used to have.

Flashback was briefly renamed Sysible Baseline. That rename is undone: the name
Baseline now belongs to a DIFFERENT product, the Salt-driven config manager, and
two products cannot share a variable prefix.

So SYSIBLE_FLASHBACK_* is canonical again and SYSIBLE_BASELINE_* is NOT read
here — deliberately, and this is the one place in this codebase where a legacy
name is dropped rather than kept. Keeping it would mean Flashback quietly
consuming the config manager's settings: SYSIBLE_BASELINE_AGENT_TOKEN and
SYSIBLE_BASELINE_DATA are names BOTH products want, and the wrong service
reading the wrong token fails in a way nobody would think to look for.

An install that was migrated during the two days the rename was live has
SYSIBLE_BASELINE_* keys that mean Flashback. install.sh migrates those back,
once. `orphaned_names()` finds any that are left so the service can say so at
startup instead of silently falling back to defaults — which, for the agent
token, means failing closed and backing nothing up.
"""

from __future__ import annotations

import os

PREFIX = "SYSIBLE_FLASHBACK_"

# Settings Flashback owns. Used to spot leftovers from the Baseline rename, and
# NOT as a fallback — see the module docstring.
OWNED = (
    "AGENT_BIND", "AGENT_PORT", "AGENT_TOKEN", "CONTROLLER_TIMEOUT_S", "DATA",
    "DB", "KEEP", "LOCAL_ROLE", "LOCAL_USER", "MAX_REQUEST_BYTES",
    "TRUST_GATEWAY_AUTH", "URL",
)
_STRANDED_PREFIX = "SYSIBLE_BASELINE_"


def names(suffix: str) -> list[str]:
    """Every spelling of one setting. One, now."""
    return [PREFIX + suffix]


def getenv(suffix: str, default: str = "") -> str:
    """Read one setting. An empty string counts as SET: an explicitly empty
    agent token means "no token" on purpose and must not inherit a default."""
    val = os.environ.get(PREFIX + suffix)
    return default if val is None else val


def orphaned_names() -> list[tuple[str, str]]:
    """(stranded_name, correct_name) for settings still under the short-lived
    SYSIBLE_BASELINE_* spelling and NOT set under the current one.

    Advisory: nothing here fails, because a name in this list may equally belong
    to the config manager, which legitimately owns that prefix now.
    """
    out = []
    for suffix in OWNED:
        stranded = _STRANDED_PREFIX + suffix
        if os.environ.get(stranded) is not None and os.environ.get(PREFIX + suffix) is None:
            out.append((stranded, PREFIX + suffix))
    return sorted(out)


def warn_about_orphans() -> str:
    """One line for the startup log, or "" when there is nothing to say."""
    orphans = orphaned_names()
    if not orphans:
        return ""
    pairs = ", ".join(f"{a} -> {b}" for a, b in orphans)
    return ("[flashback] These settings are under the short-lived SYSIBLE_BASELINE_* "
            "spelling and are NOT being read: " + pairs + ". Flashback reads "
            "SYSIBLE_FLASHBACK_* only, because SYSIBLE_BASELINE_* now belongs to "
            "the Sysible Baseline config manager. Re-run install.sh to migrate "
            "them, or rename them by hand. Until then Flashback is using its "
            "defaults — and the default agent token is EMPTY, which fails closed "
            "and backs nothing up.")
