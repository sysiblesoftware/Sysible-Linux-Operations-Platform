"""Sysible Flashback — backing up the SLOP platform itself.

Flashback's original job is per-host configuration history: what /etc looked
like on each managed machine, every version of it. This module covers the thing
that was missing from that picture — the platform doing the managing.

WHAT THIS COVERS, AND WHAT IT DELIBERATELY DOES NOT.

Covered: SLOP's own declarative configuration. The compose file that defines the
stack, the gateway's Caddyfile and portal, and an inventory of the named volumes
with their sizes. Lose the host and this is what you need to stand the platform
back up in the same shape.

NOT covered, and this is the important half:

  * SECRETS. The .env holds the SSO shared secret, the updater secret and both
    service tokens. They are excluded by pattern, and anything that looks like a
    secret is excluded even when the pattern misses, because a config-history
    store readable by every operator with console access is the wrong place for
    a credential that grants fleet-wide write. Restoring the platform means
    re-running install.sh, which mints fresh ones.
  * VOLUME CONTENTS. The IdP's user database, Caddy's issued certificates,
    Baseline's declared states and Flashback's own version store are DATA, not
    configuration, and several are far too large to live in a content-addressed
    text store. What is recorded is their existence and size, so a restore can
    tell whether a volume came back — the volumes themselves need a volume-level
    backup, which is what `sysible_ctl slop backup` is for.
  * FLASHBACK'S OWN STORE, for the obvious reason: a backup of a thing, inside
    that thing, is not a backup.

The honest summary, which the manual says too: this makes rebuilding the
platform's SHAPE a recorded, diffable, restorable thing. It is not a disaster
recovery plan on its own, and claiming otherwise would be worse than not having
it — an operator who believes they are covered does not take the other backup.
"""

from __future__ import annotations

import os
import re

# The reserved host id platform snapshots are stored under. It is not a hostname
# and cannot collide with one: agents are enrolled with ids the Controller
# generates, and none of them contains a colon.
PLATFORM_ID = "slop:platform"
PLATFORM_LABEL = "SLOP platform"

# Files that describe the platform's shape. Relative to the SLOP checkout.
TRACKED = (
    "docker-compose.yml",
    "gateway/Caddyfile",
    "portal/index.html",
    "portal/app.js",
    "portal/style.css",
)

# Anything matching these is never stored, wherever it was found.
_SECRET_NAME = re.compile(r"(^|/)\.env|\.key$|\.pem$|secret|token|password|credential",
                          re.IGNORECASE)
# A line that assigns something secret-shaped, for files we DO store.
_SECRET_LINE = re.compile(
    r"(SECRET|TOKEN|PASSWORD|PASSPHRASE|PRIVATE_KEY|API_KEY)\s*[:=]\s*\S", re.IGNORECASE)

_REDACTED = "<redacted by Flashback: a secret does not belong in config history>"

# Never read a file larger than this into the store. The tracked set is text;
# anything this size is not what we think it is.
MAX_FILE_BYTES = 2 * 1024 * 1024


def is_secret_path(path: str) -> bool:
    return bool(_SECRET_NAME.search(path or ""))


# A value that is ONLY a shell/compose variable reference — ${FOO}, ${FOO:-bar},
# $FOO — is not a secret, it is the WIRING, and blanking it defeats the point of
# storing the compose file at all. `SYSIBLE_BASELINE_AGENT_TOKEN: ${...:-}` says
# "this service takes its token from the environment and has no default", which
# is exactly the kind of thing someone restoring the platform needs to read.
_ONLY_A_REFERENCE = re.compile(r'^[\s"\']*\$\{?[A-Za-z_][A-Za-z0-9_]*'
                               r'(:-[^}]*)?\}?[\s"\']*$')


def redact(text: str) -> str:
    """Blank the VALUE of anything secret-shaped, keeping the key.

    Keeping the key matters: "SYSIBLE_SSO_SHARED_SECRET is set" and "it is
    missing" are different platform states, and a diff that cannot distinguish
    them hides the most likely cause of an outage.

    A value that is only a variable reference is left alone — see above. The
    first version of this redacted the whole compose file's environment block,
    which turned the most useful artefact here into a list of keys.
    """
    out = []
    for line in (text or "").splitlines():
        if _SECRET_LINE.search(line) and not line.lstrip().startswith("#"):
            # This used to compute key and value once and then immediately
            # overwrite both on the next two lines — dead work, and it passed
            # maxsplit positionally, which Python 3.13 deprecates.
            parts = re.split(r"[:=]", line, maxsplit=1)
            key = parts[0]
            value = parts[1] if len(parts) > 1 else ""
            if _ONLY_A_REFERENCE.match(value):
                out.append(line)            # wiring, not a secret
            else:
                out.append(f"{key}: {_REDACTED}")
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def collect(root: str, volumes: dict | None = None) -> list[dict]:
    """[{path, content}] for the platform's shape, secrets removed.

    `volumes` is {name: size_bytes} from whoever called us — this module does
    not shell out to docker. Keeping the collection pure means it can be tested
    without a daemon, and means Flashback never needs the docker socket, which
    is a root-equivalent grant it has no other reason to hold.
    """
    files = []
    for rel in TRACKED:
        full = os.path.join(root, rel)
        if is_secret_path(rel):
            continue                       # belt and braces; TRACKED has none
        try:
            if os.path.getsize(full) > MAX_FILE_BYTES:
                files.append({"path": f"slop/{rel}",
                              "content": f"<skipped: larger than {MAX_FILE_BYTES} bytes>\n"})
                continue
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                files.append({"path": f"slop/{rel}", "content": redact(fh.read())})
        except OSError:
            # A file that is absent is a FACT about the platform, not an error:
            # a stack missing its Caddyfile is exactly what someone restoring
            # needs to see.
            files.append({"path": f"slop/{rel}", "content": "<not present>\n"})

    if volumes is not None:
        lines = ["# Named volumes at the time of this snapshot.",
                 "# CONTENTS ARE NOT STORED — see backend/platform.py. These are",
                 "# here so a restore can tell whether a volume came back.",
                 ""]
        for name in sorted(volumes):
            lines.append(f"{name}: {volumes[name]} bytes")
        files.append({"path": "slop/volumes.txt", "content": "\n".join(lines) + "\n"})
    return files
