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

# Products and services that ship INSIDE another product's checkout. They are
# not separate allowlist entries, and deliberately not separately UPDATABLE:
# they are built from one git tree by one compose file, so an "Update Flashback"
# button would rebuild the whole stack while claiming to touch one service — the
# kind of control that is worse than no control.
#
# They ARE listed, with their own section on the updates screen, because an
# operator otherwise cannot see that Flashback and Baseline are installed at
# all, what they do, which of them is the one that is down, or that updating
# SLOP is what updates them.
#
# A module key IS its compose service name. That is what makes the lifecycle
# actions below exact — `docker compose restart baseline` touches one service —
# and it is checked against the real compose file by
# tests/test_updater_modules.py, so a renamed service cannot leave a section
# here pointing at nothing.
MODULES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "slop": (
        ("gateway", "Gateway",
         "Caddy: the platform's single front door, TLS, and every app's route"),
        ("idp", "Sign-in & accounts",
         "The platform's identity provider — sessions, SSO and this console"),
        ("flashback", "Sysible Flashback",
         "Backup and restore: config history for every host, and the platform's own shape"),
        ("baseline", "Sysible Baseline",
         "Desired-state configuration: declare what each host must be, applied with Salt"),
        ("visualizer", "Sysible Visualizer",
         "Activity and logs from every app, separated by app"),
        ("updater", "Updater",
         "The sidecar holding the host's Docker socket — what carries out this page"),
    ),
}


def modules(key: str) -> list[dict]:
    """The named modules inside this product's checkout, if any.

    Reported even when the product is not installed on this host, so the screen
    can say "Flashback would come with SLOP" rather than silently omitting it.
    """
    return [{"key": k, "label": lbl, "detail": d} for k, lbl, d in MODULES.get(key, ())]


def module_keys(key: str) -> list[str]:
    return [k for k, _lbl, _d in MODULES.get(key, ())]


def is_module(key: str, module: str) -> bool:
    """Whether `module` is a declared module of `key` — the only way a service
    name ever reaches an argv. Nothing here accepts a service from a caller."""
    return module in module_keys(key)


_COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


def keys() -> list[str]:
    return list(ALLOWLIST)


def label(key: str) -> str:
    return ALLOWLIST[key][0]


def checkout_dir(key: str) -> Path | None:
    """The product's checkout, or None when it isn't on this host.

    An explicit SYSIBLE_<APP>_DIR wins (that is what sysiblectl and install.sh
    honour), otherwise the conventional location under SYSIBLE_SRC_DIR.
    """
    if key not in ALLOWLIST:
        raise KeyError(key)
    _, dirname, env = ALLOWLIST[key]
    override = (os.environ.get(env) or "").strip()
    for cand in ([Path(override)] if override else []) + [SRC_DIR / dirname]:
        if (cand / ".git").is_dir():
            return cand
    # Last resort: a case-insensitive scan of SRC_DIR. GitHub serves each repo
    # under its canonical capitalisation, so `git clone .../Sysible-Controller`
    # lands in a CAPITALISED directory that the conventional path above misses —
    # and the product then reads as "not installed on this host" on a box where
    # it is plainly installed. Exact matches are tried first, above, so this only
    # ever resolves a case variant. (sysiblectl hit the same thing and answers
    # it with `find -iname`.)
    try:
        for child in sorted(SRC_DIR.iterdir()):
            if child.name.lower() == dirname.lower() and (child / ".git").is_dir():
                return child
    except OSError:
        pass
    return None


def compose_dir(root: Path) -> Path | None:
    """Where `docker compose` must run for this checkout — the directory holding
    its compose file (the checkout root, or deploy/)."""
    for d in (root, root / "deploy"):
        for name in _COMPOSE_NAMES:
            if (d / name).is_file():
                return d
    return None
