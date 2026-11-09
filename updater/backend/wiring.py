"""Keeping the Controller's cross-app settings true on every update.

WHY THIS EXISTS, and it is worth being blunt about it.

SYSIBLE_BASELINE_URL / _AGENT_TOKEN and SYSIBLE_FLASHBACK_URL / _AGENT_TOKEN
live in the CONTROLLER's own compose .env. They are minted by SLOP and consumed
by the Controller, which is the only component that talks to either service's
agent API — and the only thing that has ever written them is install.sh.

An update is a `git pull` and a rebuild. It does not re-run the installer. So a
platform installed before Baseline was wired up keeps working perfectly in every
other respect and has nothing there, and the symptom is every host failing an
Apply at once with "SYSIBLE_BASELINE_URL and SYSIBLE_BASELINE_AGENT_TOKEN
unset" — which reads as a broken install rather than a missing step. Reported
exactly that way, with a screenshot of sixteen identical failures.

`sysiblectl` repairs this on `slop update` and `slop up`. THIS CONSOLE DOES NOT
RUN SYSIBLECTL: jobs.py runs git and docker compose itself. Without this module
the one path most operators actually use — the Updates page — is the one path
that never heals, and the fix in sysiblectl only reaches people who were already
at a shell on the host.

WHERE IT RUNS. In the updater process, BEFORE the work, never in the detached
self-update helper. The helper only gets SLOP's own checkout bind-mounted
(detached.spawn passes `-v {cwd}:{cwd}`), so it cannot see the Controller's .env
at all; this container gets SYSIBLE_SRC_DIR as well and can. Running first is
also correct on its own terms: the values come from SLOP's .env, which a git
pull cannot change (.env is not tracked), so the repair is right whether or not
the update that follows succeeds — and doing it first means the rebuild picks
the new values up instead of needing a second recreate.

SAFETY. Same rule as the rest of this service: nothing here takes a path or a
value from a caller. Both directories come from apps.py's allowlist, and the
keys are module constants.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import compose

from . import apps

# Default agent ports, matching SLOP's compose. Only used when SLOP's .env does
# not pin one.
_FLASHBACK_PORT = "8770"
_BASELINE_PORT = "8771"

# host.docker.internal, not loopback: the Controller runs in a container and
# both services publish on the HOST. Its compose maps that name to the gateway.
_HOST = "http://host.docker.internal:"


def read_env(path: Path) -> dict[str, str]:
    """A compose .env as a dict. Last occurrence wins; one layer of matching
    quotes is stripped.

    Last rather than first because that is what docker compose itself does with
    a duplicated key, and what sysiblectl's `sed … | tail -n1` does — a repair
    that disagreed with the thing consuming the file would be worse than none.

    Quotes for the same reason, and because of what this function is FOR: it is
    read on every update, and its answer decides whether anything is rewritten.
    A hand-quoted but perfectly correct value that read as different would
    rewrite the file and recreate the Controller every single time anyone
    pressed Update — a repair that never stops repairing.
    """
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if k.startswith("export "):
            k = k[len("export "):].strip()
        if k:
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            out[k] = v
    return out


def plan(slop: dict[str, str], ctl: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """What the Controller's .env must become. (changes, notes) — PURE.

    Separated from the file handling so the decisions can be tested without a
    checkout, a docker daemon or a container anywhere near them.
    """
    changes: dict[str, str] = {}
    notes: list[str] = []
    # Resolved the SAME way the Flashback branch below resolves its own url, so
    # the "this is actually Flashback's address" fingerprint still recognises it
    # on a platform that pins SYSIBLE_FLASHBACK_URL to something non-default.
    fb_url = (slop.get("SYSIBLE_FLASHBACK_URL")
              or _HOST + (slop.get("SYSIBLE_FLASHBACK_AGENT_PORT") or _FLASHBACK_PORT))

    for name, token_key, url_key, port_key, default_port, other_url in (
        ("Sysible Flashback", "SYSIBLE_FLASHBACK_AGENT_TOKEN", "SYSIBLE_FLASHBACK_URL",
         "SYSIBLE_FLASHBACK_AGENT_PORT", _FLASHBACK_PORT, ""),
        ("Sysible Baseline", "SYSIBLE_BASELINE_AGENT_TOKEN", "SYSIBLE_BASELINE_URL",
         "SYSIBLE_BASELINE_AGENT_PORT", _BASELINE_PORT, fb_url),
    ):
        tok = slop.get(token_key) or ""
        if not tok:
            # Nothing to carry across, and nothing this service can mint — the
            # token is shared with a service that already reads it from here.
            notes.append(f"! no {token_key} in SLOP's .env — {name} cannot work "
                         "from the Controller and every request to it will fail. "
                         "Re-run install.sh in the SLOP checkout to mint one.")
            continue

        want_url = slop.get(url_key) or (_HOST + (slop.get(port_key) or default_port))
        have_url = ctl.get(url_key) or ""
        # The URL is CORRECTED when it is absent or when it is the fingerprint of
        # the installer bug that wrote Flashback's address under Baseline's name;
        # a third value is a deliberate choice (a split host, a reverse proxy)
        # and is left alone.
        if not have_url:
            changes[url_key] = want_url
            notes.append(f"+ the Controller had no address for {name} — set to {want_url}")
        elif other_url and have_url == other_url and want_url != have_url:
            changes[url_key] = want_url
            notes.append(f"+ the Controller had the WRONG address for {name} "
                         f"({have_url}, which is the other service) — set to {want_url}")

        # The token is always corrected: SLOP's .env is authoritative for a value
        # the receiving service reads from that same file, and a stale token
        # fails the compare exactly like a missing one.
        if (ctl.get(token_key) or "") != tok:
            changes[token_key] = tok
            notes.append(f"+ the Controller's {name} agent token was "
                         + ("wrong" if ctl.get(token_key) else "missing")
                         + " — corrected")
    return changes, notes


def upsert(path: Path, changes: dict[str, str]) -> None:
    """Write keys into a compose .env, leaving everything else as it is.

    In place and line-wise rather than rewritten from a dict: an operator's
    comments and ordering are theirs, and a .env is a file people read.
    """
    if not changes:
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    def _key(line: str) -> str:
        bare = line.strip()
        if bare.startswith("#") or "=" not in bare:
            return ""
        k = bare.partition("=")[0].strip()
        return k[len("export "):].strip() if k.startswith("export ") else k

    # Which line holds each key's LAST occurrence. That one is rewritten and any
    # earlier duplicate is dropped, so the file ends up saying one thing — and
    # the thing it says is the one docker compose and read_env would have read.
    last = {}
    for i, line in enumerate(lines):
        k = _key(line)
        if k in changes:
            last[k] = i

    rendered = []
    for i, line in enumerate(lines):
        k = _key(line)
        if k in changes:
            if last[k] == i:
                rendered.append(f"{k}={changes[k]}")
            continue                    # an earlier duplicate of a rewritten key
        rendered.append(line)
    for k, v in changes.items():
        if k not in last:
            rendered.append(f"{k}={v}")
    path.write_text("\n".join(rendered).rstrip("\n") + "\n", encoding="utf-8")


def repair(slop_root: Path | None = None) -> tuple[dict[str, str], list[str]]:
    """Carry SLOP's cross-app settings into the Controller's .env.

    (changes, notes). An empty `changes` means there was nothing to do, which is
    the normal case — this runs on every update and must be silent when the
    wiring is already right.

    `slop_root` is optional because only a SLOP update has it in hand; a
    Controller update is just as good a moment to check, and resolves it the
    same way everything else here does.
    """
    if slop_root is None:
        slop_root = apps.checkout_dir("slop")
        if slop_root is None:
            return {}, []
    slop_dir = apps.compose_dir(slop_root) or slop_root
    slop_env = slop_dir / ".env"
    if not slop_env.is_file():
        return {}, []

    ctl_root = apps.checkout_dir("controller")
    if ctl_root is None:
        # A SLOP-only host. Not a problem and not worth a line in the log.
        return {}, []
    ctl_dir = apps.compose_dir(ctl_root)
    if ctl_dir is None:
        return {}, [f"! {ctl_root} has no compose file — cannot wire the Controller up."]
    ctl_env = ctl_dir / ".env"

    changes, notes = plan(read_env(slop_env), read_env(ctl_env))
    if changes:
        try:
            upsert(ctl_env, changes)
        except OSError as e:
            return {}, notes + [f"! could not write {ctl_env}: {e}"]
        notes.append(f"+ wrote {len(changes)} setting(s) into {ctl_env}")
    return changes, notes


def recreate_controller(timeout: float = 180) -> tuple[bool, str]:
    """Recreate the Controller so it reads the .env we just wrote.

    `up -d` with no --build: the image is unchanged, only its environment, and a
    rebuild would cost minutes for nothing. Compose recreates a container whose
    resolved environment has changed, which is exactly the condition here.
    """
    ctl_root = apps.checkout_dir("controller")
    if ctl_root is None:
        return False, "the Controller is not installed on this host"
    ctl_dir = apps.compose_dir(ctl_root)
    if ctl_dir is None:
        return False, f"{ctl_root} has no compose file"
    try:
        # This one already checks there is a compose FILE (compose_dir above);
        # what it never checked is that compose itself exists here.
        _blocked = compose.unavailable_reason()
        if _blocked:
            return False, _blocked
        p = subprocess.run(compose.normalize(["docker", "compose", "up", "-d"]),
                           cwd=str(ctl_dir),
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"{type(e).__name__} recreating the Controller"
    if p.returncode != 0:
        return False, (p.stderr or p.stdout or "").strip()[:300]
    return True, "the Controller was recreated with the new settings"
