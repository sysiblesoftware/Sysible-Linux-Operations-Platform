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


def blocked_reason(directory) -> str | None:
    """Both checks, in the order the failures actually happen."""
    return unavailable_reason() or missing_file_reason(directory)


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
