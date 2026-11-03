"""Sysible Baseline — how a desired state actually reaches a host.

MASTERLESS, BY DESIGN. Baseline does not run a Salt master and the hosts do not
run a minion daemon. A state is shipped to the host with the task that applies
it, and `salt-call --local` evaluates it there.

That is a deliberate trade, and the alternatives are worse here:

  * A master/minion pair means a second always-on daemon on every host, a second
    port, a second set of keys to accept and rotate, and a second thing that can
    be offline while the console says a host is "managed".
  * salt-ssh from the Baseline container means Baseline needs SSH credentials for
    the whole fleet — a second copy of the thing the Controller already holds,
    in a container reachable from the gateway.

Masterless needs none of that. The Controller already has an authenticated,
outbound-only, NAT-friendly channel to every enrolled host, and it is already
the only component trusted to say which host is which. Baseline hands it a
state and a target; the host evaluates its own configuration locally.

The cost, stated plainly: no Salt mine, no peer publishing, no master-side
pillar, and `--local` means a host can only reason about itself. Baseline's job
is "this host must look like this", which is exactly what --local can answer.

WHAT GETS PARSED. `salt-call --local --out=json state.apply` returns a mapping
of state-id -> result. That mapping is the whole input to the event rules in
eda.py, so this module's job is to turn it into something with no Salt-shaped
surprises left in it: a stable list of per-state outcomes, with `changed` and
`failed` already decided.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex

# Salt's own state-id convention: "<sls>_|-<id>_|-<name>_|-<function>".
_ID_SEP = "_|-"

# ---------------------------------------------------------------------------
# A declared state is DATA, and must stay data all the way onto the host.
# ---------------------------------------------------------------------------
# The SLS travels in a heredoc with a QUOTED delimiter, which stops every kind
# of expansion inside the body — `$`, backticks, backslashes, globs — and that
# is why a heredoc is the right transport for YAML full of file contents.
#
# What a quoted delimiter does NOT stop is a body line that IS the delimiter.
# The shell ends the heredoc there and reads the rest of the body as commands.
# With a fixed delimiter that was remote code execution on every host a state
# is assigned to, with three aggravations:
#
#   * the injected lines run BEFORE salt-call, so test=True — the console's
#     "preview", the default path for a state nobody has applied yet — ran them
#     too. Previewing a state a colleague sent you was code execution;
#   * the Controller dispatches baseline-apply with want_root=True;
#   * Salt never sees them, so the run record, the event rules and the
#     Flashback capture all describe a clean preview.
#
# Two defences, because this one is worth two. The delimiter is derived from
# the body, so an author would need the hash of text that contains that hash in
# order to write the line that closes it. And the body is still checked against
# the delimiter, which is the assertion that keeps the derivation load-bearing:
# point _heredoc_delimiter back at a fixed string and the check is what refuses.
_DELIM_PREFIX = "SYSIBLE_BASELINE_SLS"


def _heredoc_delimiter(body: str) -> str:
    """The heredoc delimiter for this exact body. Deterministic — the command
    for a given state is stable, so it is reviewable and diffable — but not a
    value the body's author can predict."""
    return f"{_DELIM_PREFIX}_{hashlib.sha256(body.encode('utf-8', 'replace')).hexdigest()[:16]}"


def _reject_delimiter_line(body: str, delim: str) -> None:
    """Refuse a body that would close its own heredoc.

    Exact match, as the shell does it: `<<'X'` ends only on a line that is
    precisely X, so an indented or padded occurrence is ordinary data and is
    left alone. (`<<-` would strip leading tabs; this is not that form.)
    """
    for n, line in enumerate(body.split("\n"), 1):
        if line == delim:
            raise ValueError(
                f"refusing to apply this state: line {n} would close the heredoc "
                "that carries it to the host, and everything after it would run "
                "as a shell command")


def apply_command(sls_text: str, *, test: bool = False, state_name: str = "baseline") -> str:
    """A shell command that applies `sls_text` on the host and prints JSON.

    The state is written to a private temp root rather than /srv/salt: Baseline
    must not collide with, or quietly become, a Salt installation the operator
    manages themselves. The root is removed afterwards even when the run fails,
    because a state file left on disk is configuration nobody declared.

    `test=True` is Salt's dry run (test=True): report what WOULD change, change
    nothing. The console calls it "preview", and it is the default path for a
    state an operator has not applied before.
    """
    if not sls_text.strip():
        raise ValueError("refusing to apply an empty state: nothing to declare")
    name = "".join(c for c in state_name if c.isalnum() or c in "-_") or "baseline"
    # A heredoc keeps the YAML out of argv entirely — states are multi-line and
    # routinely contain quotes, backslashes and $ in file contents. See the
    # delimiter helpers above for why it is derived rather than fixed.
    #
    # rstrip() FIRST, then derive and check: the body that goes into the command
    # is the body that must be checked, and rstrip can turn a padded last line
    # into a bare one.
    body = sls_text.rstrip()
    delim = _heredoc_delimiter(body)
    _reject_delimiter_line(body, delim)
    return (
        "set -e; "
        f"_r=$(mktemp -d /tmp/sysible-baseline.XXXXXX); "
        f"trap 'rm -rf \"$_r\"' EXIT; "
        f"mkdir -p \"$_r/states\"; "
        f"cat > \"$_r/states/{name}.sls\" <<'{delim}'\n"
        f"{body}\n"
        f"{delim}\n"
        f"salt-call --local --retcode-passthrough --out=json "
        f"--file-root=\"$_r/states\" state.apply {shlex.quote(name)}"
        + (" test=True" if test else "")
    )


# Salt is a PREREQUISITE on every managed host — Baseline writes a state file
# there and runs `salt-call --local` on it — and a host without it answers with
# one line on stderr and exit 127. Which line depends on the shell: bash says
# "command not found", dash and busybox say "not found", and a dead symlink or
# an env-wrapped call says "No such file or directory". They all mean the same
# thing, and the operator used to be told about JSON.
#
# Anchored on salt-call so a STATE whose own comment contains the phrase — a
# cmd.run that failed, say — is still read as a state result.
_NO_SALT = re.compile(
    r"salt-call['\"]?\s*:\s*(?:command not found|not found|No such file or directory)",
    re.I)


def missing_salt(text: str) -> bool:
    """Does this return mean "Salt is not on this host"?

    Public because the console asks the same question about a host it has not
    tried yet: the answer is the difference between an Apply that is about to
    work and one that is about to fail on every host at once.
    """
    return bool(_NO_SALT.search(text or ""))


# One sentence, in one place, because it is said by the parser, by the pre-flight
# check and in the console's own words.
NO_SALT_REASON = (
    "Salt is not installed on this host, so there is nothing to apply the state "
    "with. Baseline runs `salt-call --local` on each host it manages. Install it "
    "from the host's page, or install it yourself and apply again.")


# INSTALLING SALT LIVES IN THE CONTROLLER, not here.
#
# Baseline asks the Controller to install Salt on a host by name; it does not
# hand over a command to run. The Controller holds the only authenticated
# channel to the fleet and acts as root through it, so it builds every command
# it executes and accepts none from a caller — the rule its updater states
# about its own allowlist. A Baseline that could post arbitrary shell there
# would be a second root on every host.
#
# The builder is client/_api_automation.cmd_install_salt(), where it also gets
# the Controller's single package-manager cascade instead of a copy of it. What
# stays here is the half Baseline owns: recognising that a host has no Salt,
# and saying so.

def parse_return(text: str) -> dict:
    """Turn `salt-call --out=json` output into a flat, decided result.

    Returns {"ok": bool, "states": [...], "changed": n, "failed": n, "error": str|None}
    where each state is {"id", "name", "function", "result", "changed", "comment"}.

    Salt prints warnings and progress on the same stream in some versions, so
    the JSON object is located rather than assumed to start at byte zero. A
    non-JSON return is an ERROR, not an empty success: "no states failed"
    because nothing was parsed is the most dangerous possible answer from a
    configuration tool.
    """
    out = {"ok": False, "states": [], "changed": 0, "failed": 0, "error": None}
    raw = (text or "").strip()
    if not raw:
        out["error"] = "salt-call produced no output"
        return out

    # BEFORE the JSON hunt. This is not a malformed return, it is a host that
    # could not run the command at all, and saying so is the difference between
    # "the tool is broken" and "install Salt here".
    if missing_salt(raw):
        out["error"] = NO_SALT_REASON
        return out

    start = raw.find("{")
    if start == -1:
        out["error"] = f"salt-call did not return JSON: {raw.splitlines()[0][:200]}"
        return out
    try:
        doc = json.loads(raw[start:])
    except json.JSONDecodeError as e:
        out["error"] = f"salt-call returned malformed JSON ({e.msg})"
        return out

    body = doc.get("local", doc) if isinstance(doc, dict) else doc

    # A state that cannot be rendered comes back as a LIST of error strings, not
    # a mapping. Reporting that as "0 states, nothing failed" is how a broken
    # state file looks identical to a clean host.
    if isinstance(body, list):
        out["error"] = "; ".join(str(x) for x in body)[:500] or "state rendering failed"
        return out
    if not isinstance(body, dict):
        out["error"] = "salt-call returned an unexpected shape"
        return out

    for key, rec in body.items():
        if not isinstance(rec, dict):
            continue
        parts = key.split(_ID_SEP)
        sid = parts[1] if len(parts) > 1 else key
        fn = f"{parts[0]}.{parts[3]}" if len(parts) > 3 else parts[0]
        result = rec.get("result")
        changed = bool(rec.get("changes"))
        # result is True / False / None. None is Salt's "would have changed"
        # under test=True — not a failure, and not a change that happened.
        failed = result is False
        out["states"].append({
            "id": sid,
            "name": rec.get("name") or sid,
            "function": fn,
            "result": result,
            "changed": changed,
            "comment": (rec.get("comment") or "").strip()[:500],
        })
        out["changed"] += 1 if changed else 0
        out["failed"] += 1 if failed else 0

    if not out["states"]:
        out["error"] = "salt-call returned no state results"
        return out
    out["ok"] = out["failed"] == 0
    return out
