"""Sysible Baseline — environment variables.

SYSIBLE_BASELINE_* belongs to this service alone. Sysible Flashback, the backup
tool in this same stack, briefly carried that prefix during a two-day rename and
deliberately no longer reads it — AGENT_TOKEN, DATA and TRUST_GATEWAY_AUTH are
names both products want, and one service silently consuming the other's
settings fails with no symptom until the day something breaks.
"""

from __future__ import annotations

import os

PREFIX = "SYSIBLE_BASELINE_"


def getenv(suffix: str, default: str = "") -> str:
    """Read one setting. An empty string counts as SET: an explicitly empty
    agent token means "no token" on purpose and must not inherit a default."""
    val = os.environ.get(PREFIX + suffix)
    return default if val is None else val
