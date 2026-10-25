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

import json
import shlex

# Salt's own state-id convention: "<sls>_|-<id>_|-<name>_|-<function>".
_ID_SEP = "_|-"


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
    # routinely contain quotes, backslashes and $ in file contents.
    return (
        "set -e; "
        f"_r=$(mktemp -d /tmp/sysible-baseline.XXXXXX); "
        f"trap 'rm -rf \"$_r\"' EXIT; "
        f"mkdir -p \"$_r/states\"; "
        f"cat > \"$_r/states/{name}.sls\" <<'SYSIBLE_BASELINE_SLS'\n"
        f"{sls_text.rstrip()}\n"
        "SYSIBLE_BASELINE_SLS\n"
        f"salt-call --local --retcode-passthrough --out=json "
        f"--file-root=\"$_r/states\" state.apply {shlex.quote(name)}"
        + (" test=True" if test else "")
    )


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
