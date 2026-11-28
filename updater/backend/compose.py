"""Which compose this container speaks, and whether there is anything to run.

Every executed `docker compose` in the whole Sysible suite lives in this package
— jobs.py, detached.py and wiring.py — and until now not one of them checked
that compose existed. updater/Dockerfile said "the CLI and the compose plugin
come from the base rather than being installed by hand", which is an unverified
property of a third-party image behind a floating tag. When it is not true, the
operator gets Docker's own words, which name nothing we ship and suggest nothing
to do:

    docker: 'compose' is not a docker command.

sysiblectl detects compose and falls back to the standalone binary, and
install.sh installs one if it is missing. The GUI was the only surface with no
detection at all, so Administration was the one place this could surface raw.

The other half is the same class of thing. Compose run in a directory with no
compose file says:

    no configuration file provided: not found

...which does not say WHICH directory it looked in — and the directory is a bind
mount from the host, so "wrong path" is the single most likely cause and the one
the message hides. Both are now checked up front, with the path in the text.

Nothing here is derived from a request: compose_argv() returns one of two fixed
literals, decided by what is installed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

# The names compose looks for, in its own order of precedence.
COMPOSE_FILENAMES = ("compose.yaml", "compose.yml",
                     "docker-compose.yaml", "docker-compose.yml")

_PLUGIN = ["docker", "compose"]
_STANDALONE = ["docker-compose"]

_cached: list[str] | None = None


def _detect() -> list[str]:
    # `docker compose version` rather than looking for the plugin file: the
    # plugin can be installed to any of several directories, and a `docker` that
    # is really a shim onto something else (podman-docker) loads none of them.
    # Asking is the only answer that is true for the docker on this PATH.
    try:
        if subprocess.run(_PLUGIN + ["version"], capture_output=True,
                          timeout=15).returncode == 0:
            return list(_PLUGIN)
    except Exception:
        pass
    if shutil.which(_STANDALONE[0]):
        return list(_STANDALONE)
    return []


def compose_argv() -> list[str]:
    """The argv prefix that runs compose here, or [] if there is none.

    Cached: this cannot change under a running container, and the detection
    costs a subprocess on a path that is hit per page load.
    """
    global _cached
    if _cached is None:
        _cached = _detect()
    return list(_cached)


def available() -> bool:
    return bool(compose_argv())


def unavailable_reason() -> str | None:
    """Why compose cannot run here, naming a remedy — or None if it can."""
    if available():
        return None
    return ("No compose implementation in this container: neither `docker "
            "compose` (the v2 plugin) nor `docker-compose`. The updater image is "
            "built to carry it, so this container is running an older image — "
            "rebuild it from the SLOP checkout on the host:\n"
            "  docker compose up -d --build updater\n"
            "If the host itself has no compose, install it first:\n"
            "  sudo apt-get install -y docker-compose-plugin")


def missing_file_reason(directory) -> str | None:
    """Why compose has nothing to run in `directory`, naming it — or None.

    Compose's own "no configuration file provided: not found" omits the path it
    searched, which is the one fact needed to debug it: these directories reach
    this container as bind mounts, so a path that is right on the host and wrong
    in here produces that message with nothing to go on.
    """
    p = Path(directory)
    if not p.is_dir():
        return (f"{p} is not a directory inside the updater container. It is a "
                "bind mount from the host — check the `volumes:` entry for the "
                "updater service in docker-compose.yml, and that the checkout "
                "is where it says.")
    if any((p / n).is_file() for n in COMPOSE_FILENAMES):
        return None
    listing = ", ".join(sorted(c.name for c in list(p.iterdir())[:12])) or "(empty)"
    return (f"No compose file in {p} — looked for "
            f"{', '.join(COMPOSE_FILENAMES)}. It contains: {listing}. This "
            "directory is a bind mount from the host, so it is usually pointed "
            "at the wrong place rather than missing a file.")


# Every service in SLOP's compose file pins `container_name:` -- sysible-slep,
# sysible-connect, sysible-controller and the rest. A pinned name is GLOBAL to the
# Docker daemon, not scoped to a compose project. The compose file has no `name:`,
# so the project name comes from the DIRECTORY it is run from.
#
# Put those together and starting the stack from a different directory does not
# adopt the running one -- it FORKS it. Compose finds no containers of its own,
# creates fresh volumes, and then collides on the names:
#
#     Volume "sysible-linux-operations-platform_connect-data"  Created
#     Container sysible-connect  Creating
#     Error response from daemon: Conflict. The container name "/sysible-connect"
#     is already in use by container "12c88950b2d6...".
#
# "Creating" rather than "Recreate", next to a brand-new data volume, is the tell:
# this project has never owned those containers. The same split is why
# Administration reported "No container" for three apps that were running, and why
# a SLEP container could be inspected with an empty /data/projects while Terraform
# was quoting real line numbers -- two SLEPs, the work in the other one's volume.
#
# Docker's message names neither the project nor the data, and the obvious reaction
# to it -- remove the container that is "in the way" -- is the one action that
# strands the volume holding the operator's projects. So say it first.
_COMPOSE_PROJECT_LABEL = "com.docker.compose.project"


def pinned_container_names(directory) -> list[str]:
    """The container_name: values pinned by the compose file in `directory`.

    Read textually rather than through `docker compose config`, because this runs
    on the failure path, where the daemon may be the unhappy thing."""
    for fn in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        p = Path(directory) / fn
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []
            return re.findall(r"^\s*container_name:\s*([A-Za-z0-9_.-]+)\s*$", text, re.M)
    return []


def project_name_for(directory) -> str:
    """What compose will call this project: the directory, normalised as compose
    normalises it. Only used to compare against what the daemon reports."""
    base = os.path.basename(os.path.abspath(str(directory))).lower()
    return re.sub(r"[^a-z0-9_-]", "", base)


def forked_stack_message(project: str, owners: dict) -> str | None:
    """The sentence to show, given who owns each pinned name. Pure, so it can be
    tested without a daemon.

    `owners` maps a container name to the compose project holding it, or "" for a
    container carrying no compose label at all. A name this project already owns
    is just a restart and is not reported."""
    foreign = {n: (o or "") for n, o in owners.items() if (o or "") != project}
    if not foreign:
        return None
    others = sorted({o for o in foreign.values() if o})
    names = ", ".join(sorted(foreign))
    plural = len(foreign) > 1
    whose = ("project '" + "', '".join(others) + "'") if others else "no compose project"
    return (
        f"This stack would start as project '{project}', but "
        f"{'containers' if plural else 'a container'} named {names} "
        f"{'are' if plural else 'is'} already held by {whose}. The compose file "
        "pins these names, so the two cannot coexist and the start fails with a "
        "name conflict.\n\n"
        "DO NOT delete those containers to clear the way. Each project has its own "
        "data volumes, so the apps would come back EMPTY and the work would still "
        "be in the other project's volumes.\n\n"
        "This nearly always means the stack was first brought up from a different "
        "directory, because the project name comes from the directory name. Start "
        "it from the original directory, or move the volumes across deliberately "
        "first."
    )


def container_owners(names: list[str]) -> dict:
    """Which compose project holds each of `names`, for those that exist."""
    if not names:
        return {}
    fmt = '{{.Name}}\t{{index .Config.Labels "%s"}}' % _COMPOSE_PROJECT_LABEL
    try:
        p = subprocess.run(["docker", "inspect", "--format", fmt] + list(names),
                           capture_output=True, text=True, timeout=15)
    except Exception:  # noqa: BLE001 - a preflight must never be what fails
        return {}
    owners = {}
    for line in (p.stdout or "").splitlines():
        if "\t" not in line:
            continue
        name, owner = line.split("\t", 1)
        name = name.strip().lstrip("/")
        if name:
            owners[name] = owner.strip()
    return owners


def forked_stack_reason(directory) -> str | None:
    """Whether another compose project already holds this stack's container names."""
    names = pinned_container_names(directory)
    if not names:
        return None
    return forked_stack_message(project_name_for(directory), container_owners(names))


def blocked_reason(directory) -> str | None:
    """Every check, in the order the failures actually happen."""
    return (unavailable_reason() or missing_file_reason(directory)
            or forked_stack_reason(directory))


def normalize(argv: list[str]) -> list[str]:
    """Swap the compose prefix of a literal argv for the one that works here.

    The argv tables in jobs.py are deliberately LITERAL — "the action is a KEY
    from this table, never a string from the caller, and the argv is a literal".
    Rewriting only a leading ["docker", "compose"] keeps that property: the
    subcommand and every flag still come from the literal, and the only thing
    that can change is which of two installed binaries runs it.

    With nothing installed the argv is returned untouched, so the failure is
    Docker's — the callers check blocked_reason() first and say something useful
    instead of getting there.
    """
    if list(argv[:2]) == _PLUGIN:
        got = compose_argv()
        if got:
            return got + list(argv[2:])
    return list(argv)


def shell_script(script: str) -> str:
    """The same swap for the detached helper's shell scripts.

    detached.py's scripts are module constants precisely so that nothing in them
    is derived from a request, and that still holds: the only substitution is one
    of two fixed binary names, chosen by what is installed in this image.
    """
    pre = " ".join(compose_argv())
    if not pre or pre == " ".join(_PLUGIN):
        return script
    return script.replace(" ".join(_PLUGIN), pre)
