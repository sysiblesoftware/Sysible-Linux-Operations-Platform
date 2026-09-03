"""What the updater is allowed to touch — a FIXED allowlist, and how to find it.

This module is the reason the updater is safe to give a Docker socket to. The
socket is root-equivalent on the host, so the service holding it must not accept
a path, a repository, a branch or a command from its caller. It accepts one
thing: a key from ALLOWLIST below. Everything else — which directory, which
compose file, which argv — is derived here, from configuration set on the host.

Layout matches install.sh: each product is cloned into $SYSIBLE_SRC_DIR
(default /opt/sysible-src) under the basename of its repository URL, and its
compose file is either at the top of that checkout or in deploy/.
"""
from __future__ import annotations

import os
from pathlib import Path

SRC_DIR = Path(os.environ.get("SYSIBLE_SRC_DIR", "/opt/sysible-src"))

# key -> (label, checkout directory name, env var that overrides the directory)
ALLOWLIST: dict[str, tuple[str, str, str]] = {
    "controller": ("Sysible Controller", "sysible-controller", "SYSIBLE_CONTROLLER_DIR"),
    "slep": ("Sysible Linux Engineering Platform",
             "sysible-linux-engineering-platform", "SYSIBLE_SLEP_DIR"),
    "connect": ("Sysible Connect", "sysible-connect", "SYSIBLE_CONNECT_DIR"),
    # SLOP itself: the gateway, IdP, Flashback and Visualizer all live in this
    # one checkout and are rebuilt together, so it is a single entry.
    "slop": ("Sysible Linux Operations Platform", "sysible-linux-operations-platform",
             "SYSIBLE_SLOP_DIR"),
}

_COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


def keys() -> list[str]:
    return list(ALLOWLIST)


def label(key: str) -> str:
    return ALLOWLIST[key][0]


def checkout_dir(key: str) -> Path | None:
    """The product's checkout, or None when it isn't on this host.

    An explicit SYSIBLE_<APP>_DIR wins (that is what sysible_ctl and install.sh
    honour), otherwise the conventional location under SYSIBLE_SRC_DIR.
    """
    if key not in ALLOWLIST:
        raise KeyError(key)
    _, dirname, env = ALLOWLIST[key]
    override = (os.environ.get(env) or "").strip()
    for cand in ([Path(override)] if override else []) + [SRC_DIR / dirname]:
        if (cand / ".git").is_dir():
            return cand
    return None


def compose_dir(root: Path) -> Path | None:
    """Where `docker compose` must run for this checkout — the directory holding
    its compose file (the checkout root, or deploy/)."""
    for d in (root, root / "deploy"):
        for name in _COMPOSE_NAMES:
            if (d / name).is_file():
                return d
    return None
