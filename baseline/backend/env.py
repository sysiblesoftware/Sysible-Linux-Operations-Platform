"""Sysible Baseline — environment variables, and the names they used to have.

This module exists because of what the LAST rename left behind. Baseline was
called Sysible Flashback, and before that Sysible D3lorean, and the Controller
still reads `SYSIBLE_D3LOREAN_PATHS` to this day because that rename changed the
product name and left the variables where they were. Three years on, an operator
reading the manual for `SYSIBLE_BASELINE_*` would find a running stack configured
entirely in the vocabulary of a product that no longer exists.

So the rebrand is done once, properly: `SYSIBLE_BASELINE_*` is the only name the
docs mention, and `SYSIBLE_FLASHBACK_*` keeps working forever, because a rename
that requires every operator to edit their .env before the service will start is
not a rebrand — it is an outage with a new logo.

Precedence is new-name-first so an operator who has set the new name gets it even
if a stale old one is still lying in the file (exactly the state an upgrade leaves
you in). `legacy_names_in_use()` reports what is still on the old spelling so
`sysible_ctl` can nudge without failing anything.
"""

from __future__ import annotations

import os

# Canonical prefix, and the prefixes this product answered to before.
PREFIX = "SYSIBLE_BASELINE_"
LEGACY_PREFIXES = ("SYSIBLE_FLASHBACK_",)


def names(suffix: str) -> list[str]:
    """Every spelling of one setting, most-preferred first."""
    return [PREFIX + suffix] + [p + suffix for p in LEGACY_PREFIXES]


def getenv(suffix: str, default: str = "") -> str:
    """Read one setting under its current name, falling back to the old ones.

    An empty string counts as SET: `SYSIBLE_BASELINE_AGENT_TOKEN=` means "no
    token" on purpose and must not silently inherit a stale Flashback token from
    the same file.
    """
    for name in names(suffix):
        val = os.environ.get(name)
        if val is not None:
            return val
    return default


def legacy_names_in_use() -> list[tuple[str, str]]:
    """(old_name, new_name) for every setting still configured the old way and
    NOT yet set under the new one. Advisory only — nothing here fails."""
    out = []
    for name, val in sorted(os.environ.items()):
        for p in LEGACY_PREFIXES:
            if name.startswith(p) and val is not None:
                new = PREFIX + name[len(p):]
                if os.environ.get(new) is None:
                    out.append((name, new))
    return out
