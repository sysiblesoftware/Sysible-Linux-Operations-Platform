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

# The services inside each product's checkout: compose service name, label, and
# a line saying what the thing is for.
#
# EVERY product has an entry, the three that run exactly one service included.
# That is the point of the table, not an accident of it. SLOP used to be the only
# key here, so SLOP was the only row on Software & services that could say what
# was inside it: its row expanded into a grid of cards with its own heading and
# footnote while the other three were a single terse line. Reported, looking at
# it: "Looks like we made connect, controller and slep then added slop. It
# doesn't look unified." The page did read that way, and the cause was here — one
# key in this dict — not in the stylesheet. A product now differs from its
# neighbour by the NUMBER in its disclosure, never by the shape of its row.
#
# These are deliberately not separately UPDATABLE: a product is built from one git
# tree by one compose file, so an "Update Flashback" button would rebuild the
# whole stack while claiming to touch one service — the kind of control that is
# worse than no control. Restart/stop/start/recreate ARE per-service, for a
# product that has more than one; for a product that is one service, the
# product's own Manage panel already runs exactly that command, so the module
# card is informational and jobs.module_action_refusal says so.
#
# A module key IS its compose service name. That is what makes the lifecycle
# actions exact — `docker compose restart baseline` touches one service — and it
# is checked against the real compose file: for SLOP by
# tests/test_module_sections_name_real_services.py in this repo, and for the
# other three by a test in each app's own repository, which is the only place
# that app's compose file can be read. A renamed service cannot leave a section
# here pointing at nothing without one of those failing.
MODULES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "controller": (
        ("controller", "Controller",
         "The console, its API, and the agent endpoint every managed host reports to"),
    ),
    "slep": (
        ("slep", "Engineering Platform",
         "Ansible and Terraform: playbooks, inventories, and every run's output"),
    ),
    "connect": (
        ("connect", "Connect",
         "The browser terminal and file workspace, and its SSH sessions to the fleet"),
    ),
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


# Where a vendored app's source sits inside the SLOP checkout. Three products
# used to be three git checkouts on this host; their source is in apps/ now and
# they are built by SLOP's one compose file.
VENDOR_SUBDIR = "apps"


def slop_dir() -> Path | None:
    """SLOP's own checkout, which is where apps/ lives."""
    return checkout_dir("slop")


def vendored(key: str) -> Path | None:
    """This product's source inside the SLOP checkout, if it is in there.

    Independent of whether the integrated profile is RUNNING: this answers "is
    this product part of the platform's tree", which is what decides whether it
    can have an update of its own.
    """
    if key == "slop":
        return None
    root = slop_dir()
    if root is None:
        return None
    cand = root / VENDOR_SUBDIR / key
    return cand if cand.is_dir() else None


def integrated() -> bool:
    """Whether this host runs the one-checkout layout.

    Set by `install.sh --integrated`, which writes it to the compose .env so it
    survives every later recreate. Deliberately NOT inferred from apps/ being
    present: that directory arrives with a git pull, and a pull must not change
    what this screen says is running. A host that has not migrated would have
    three rows start claiming to be "built with the platform" while three
    standalone stacks went on serving every request.
    """
    return (os.environ.get("SYSIBLE_INTEGRATED") or "").strip().lower() in (
        "1", "true", "yes", "on")


def is_vendored(key: str) -> bool:
    """Whether this product is built from the platform's tree rather than its own.

    Three things, all of them required:

      * the host opted in (above), so a pull cannot flip it;
      * apps/<key> is in the platform's checkout, so there is something to build;
      * there is NO separate checkout of it.

    That last one is what makes a migration survivable. On a host part-way
    through -- apps/controller present, /opt/sysible-src/sysible-controller
    still there and still running -- the separate checkout is the thing actually
    serving requests, so it keeps its own row and its own update button until
    someone removes it. A product does not quietly lose its update because a
    directory appeared somewhere else.
    """
    return (integrated()
            and vendored(key) is not None
            and checkout_dir(key) is None)


# The three that are built from SLOP's own tree on an integrated host.
VENDORED_KEYS = ("controller", "slep", "connect")


def keys() -> list[str]:
    """Every product the updater MANAGES. Machinery, not display.

    Update-all walks this, each entry has its own checkout to pull and its own
    compose project to build, and that stays true whatever the screen shows.
    row_keys() below is what the screen shows.
    """
    return list(ALLOWLIST)


def row_keys() -> list[str]:
    """The rows on Software & services. There is ONE.

    Asked for about ten times, in these words: "I want everything under slop."

    It was conditional on the integrated layout, which was the wrong call
    twice over. It made the thing that was asked for depend on a flag the
    operator never set and had no reason to know about -- this host was
    installed with the server installer, so the flag is off and the screen
    went on showing four products. And the distinction it was protecting is
    not one an operator has: whether the Controller's source sits in its own
    checkout or inside SLOP's is a packaging detail, and the screen was
    turning it into four headings, four version lines and four verdicts.

    One row. The apps are services under it, because that is how they are
    reached, started, stopped and looked at. What they keep is everything that
    is really theirs: their own version, their own update, their own lifecycle
    buttons -- on their own service card rather than on a product row of their
    own. Nothing can be done here that could be done before.
    """
    return ["slop"]


def modules(key: str) -> list[dict]:
    """The named modules inside this product's checkout, if any.

    Reported even when the product is not installed on this host, so the screen
    can say "Flashback would come with SLOP" rather than silently omitting it.
    """
    return [{"key": k, "label": lbl, "detail": d} for k, lbl, d in _entries(key)]


def _entries(key: str) -> tuple[tuple[str, str, str], ...]:
    """This product's service entries.

    The platform's row carries the three apps' services as well as its own,
    because the platform's row is the only row. The apps come FIRST: it then
    reads as the three consoles an operator opens, then the parts that serve
    them, rather than burying the Controller below the updater sidecar.
    """
    if key == "slop":
        vendored_svcs = tuple(e for k in VENDORED_KEYS for e in MODULES.get(k, ()))
        return vendored_svcs + MODULES.get("slop", ())
    return MODULES.get(key, ())


def module_keys(key: str) -> list[str]:
    return [k for k, _lbl, _d in _entries(key)]


def is_module(key: str, module: str) -> bool:
    """Whether `module` is a declared module of `key` — the only way a service
    name ever reaches an argv. Nothing here accepts a service from a caller."""
    return module in module_keys(key)


_COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


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


def compose_dir_for(key: str) -> Path | None:
    """Where `docker compose` must run to see THIS product's services.

    Its own checkout normally. For a vendored product it is SLOP's, because its
    services are built by SLOP's compose file and belong to SLOP's project — and
    a `compose ps` anywhere else would report it as having no containers at all,
    on a host where it is running perfectly well.
    """
    if is_vendored(key):
        root = slop_dir()
        return compose_dir(root) if root else None
    root = checkout_dir(key)
    return compose_dir(root) if root else None


def compose_dir(root: Path) -> Path | None:
    """Where `docker compose` must run for this checkout — the directory holding
    its compose file (the checkout root, or deploy/)."""
    for d in (root, root / "deploy"):
        for name in _COMPOSE_NAMES:
            if (d / name).is_file():
                return d
    return None


def owner_of(service: str) -> str:
    """Which product a compose SERVICE belongs to.

    The platform's row lists nine services drawn from four products. A card
    showing a version or offering an update has to know whose version and
    whose update, and this is the one place that mapping lives.
    """
    for key, entries in MODULES.items():
        if any(k == service for k, _lbl, _d in entries):
            return key
    return "slop"
