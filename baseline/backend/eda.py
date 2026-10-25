"""Sysible Baseline — what happens AFTER a state is applied.

Applying configuration is half the job. The half that gets forgotten is the
follow-up: the service that has to be restarted because its config file changed,
the snapshot that should be taken now that the host is in a known state, the
alert for the one state in forty that failed.

Doing that follow-up inside the state itself is the usual answer and it is a
poor one — Salt's `watch`/`onchanges` requisites only reach things Salt owns, so
"tell the Controller", "capture this in Flashback" and "page someone" all end up
as shell-outs embedded in configuration, where they are invisible to review and
run with whatever privileges the state had.

So the follow-up lives here, as rules over the EVENTS a state run produced. A
rule is matched against each state's outcome, not against the run as a whole,
which is what makes "restart nginx only when nginx.conf actually changed"
expressible at all.

TRIGGER SEMANTICS, and the one that matters: a rule fires on CHANGE, not on
state. `changed` means Salt reported a diff for that state in THIS run — so a
host already in the desired state produces no events and fires nothing. Without
that, "restart on config change" restarts the service on every scheduled run
forever, which is an outage on a timer.
"""

from __future__ import annotations

import fnmatch

# What a rule may do. Each is a verb Baseline can carry out itself; nothing here
# shells out on the host except `command`, which says so in its name.
ACTIONS = ("notify", "command", "capture", "reapply")

# What a rule may match on.
TRIGGERS = ("changed", "failed", "ok", "any")


def _matches(rule: dict, st: dict) -> bool:
    pattern = (rule.get("match") or "*").strip() or "*"
    if not (fnmatch.fnmatch(st["id"], pattern) or fnmatch.fnmatch(st["function"], pattern)):
        return False
    trigger = (rule.get("trigger") or "changed").strip().lower()
    if trigger == "any":
        return True
    if trigger == "changed":
        return bool(st["changed"])
    if trigger == "failed":
        return st["result"] is False
    if trigger == "ok":
        return st["result"] is True and not st["changed"]
    return False


def evaluate(rules: list, parsed: dict) -> list:
    """Rules x state outcomes -> the actions to carry out, in rule order.

    Returns [{"rule", "action", "argument", "state", "why"}]. Pure: it decides
    WHAT should happen and nothing more, so the decision can be tested, shown
    in the console before anything runs, and audited after.
    """
    out = []
    if not parsed.get("states"):
        return out
    for rule in rules or []:
        if not rule.get("enabled", True):
            continue
        action = (rule.get("action") or "notify").strip().lower()
        if action not in ACTIONS:
            # An unknown action is NOT silently skipped: a rule an operator
            # believes is protecting them, doing nothing, is worse than no rule.
            out.append({"rule": rule.get("name") or "?", "action": "notify",
                        "argument": "", "state": "-",
                        "why": f"rule has an unknown action {action!r}; nothing was run"})
            continue
        for st in parsed["states"]:
            if not _matches(rule, st):
                continue
            out.append({
                "rule": rule.get("name") or "?",
                "action": action,
                "argument": (rule.get("argument") or "").strip(),
                "state": st["id"],
                "why": _why(rule, st),
            })
            if rule.get("once", True):
                break          # one firing per rule per run, not one per state
    return out


def _why(rule: dict, st: dict) -> str:
    trigger = (rule.get("trigger") or "changed").lower()
    if trigger == "failed":
        return f"{st['id']} failed: {st['comment'][:120]}"
    if trigger == "changed":
        return f"{st['id']} changed"
    if trigger == "ok":
        return f"{st['id']} was already as declared"
    return f"{st['id']} matched"


def describe(rule: dict) -> str:
    """One line for the console, in the operator's words rather than Salt's."""
    trig = {"changed": "changes", "failed": "fails", "ok": "is already correct",
            "any": "runs"}.get((rule.get("trigger") or "changed").lower(), "runs")
    act = {"notify": "record it", "command": f"run {rule.get('argument') or '…'}",
           "capture": "capture the host's config in Flashback",
           "reapply": "apply the state again"}.get(
               (rule.get("action") or "notify").lower(), "do nothing")
    return f"When {rule.get('match') or 'any state'} {trig} → {act}"
