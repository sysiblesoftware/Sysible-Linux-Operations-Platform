"""Salt is a prerequisite on every managed host, and nothing said so.

REPORTED: "Salt isn't installed on baseline."

WHAT AN OPERATOR SAW. Baseline builds a shell command that runs `salt-call
--local` ON THE HOST and parses its JSON. A host without Salt answers exit 127
and one line on stderr — "salt-call: command not found" — which the Controller
concatenates onto stdout and hands back here. parse_return looked for a `{`,
did not find one, and reported:

    salt-call did not return JSON: bash: line 5: salt-call: command not found

The cause is in that string, buried behind a complaint about JSON. An operator
reads "did not return JSON" and concludes the tool is broken; the actual answer
is "this host does not have Salt on it", and the actual next step is to install
it. Neither was said, and there was no way to do it from the console.

THE SHAPES THIS TAKES. Which line comes back depends on the host's shell and
whether sudo is in the way, and the fleet is deliberately mixed — bash on most,
dash on Debian/Ubuntu derivatives, busybox on anything small. All of them mean
the same thing and all of them used to produce the same useless sentence.
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import pytest  # noqa: E402

from backend import salt  # noqa: E402

# Real first lines, from the shells a mixed fleet actually runs.
MISSING = [
    "bash: line 5: salt-call: command not found\n",          # bash
    "/bin/sh: 5: salt-call: not found\n",                    # dash — Debian, Ubuntu
    "sh: salt-call: not found\n",                            # busybox
    "sudo: salt-call: command not found\n",                  # sudo resolving it
    "bash: salt-call: No such file or directory\n",          # execve of a dead symlink
    "env: 'salt-call': No such file or directory\n",         # env-wrapped
]


@pytest.mark.parametrize("line", MISSING)
def test_a_missing_salt_is_named_as_a_missing_salt(line):
    out = salt.parse_return(line)
    assert not out["ok"]
    err = out["error"] or ""
    assert "not installed" in err.lower(), (
        f"the console still blames JSON for a host with no Salt: {err}")
    assert "salt" in err.lower()
    assert "JSON" not in err, f"the operator is told about JSON again: {err}"


@pytest.mark.parametrize("line", MISSING)
def test_it_says_what_to_do_about_it(line):
    """A diagnosis with no next step is half an answer, and this one has a
    next step that the console itself can take."""
    err = (salt.parse_return(line)["error"] or "").lower()
    assert "install" in err, err


def test_a_missing_salt_is_reported_as_a_failure_not_an_empty_success():
    """The most dangerous possible answer from a configuration tool is "nothing
    failed" because nothing ran."""
    out = salt.parse_return(MISSING[0])
    assert out["ok"] is False
    assert out["states"] == []


def test_it_is_not_confused_with_a_state_that_mentions_the_phrase():
    """A state whose comment happens to contain "command not found" — a
    cmd.run that failed, say — is a state RESULT and must stay one."""
    doc = ('{"local": {"cmd_|-check_|-check_|-run": {"name": "check", '
           '"result": false, "changes": {}, '
           '"comment": "Command \\"frobnicate\\" run: command not found"}}}')
    out = salt.parse_return(doc)
    assert out["error"] is None, out["error"]
    assert out["failed"] == 1
    assert len(out["states"]) == 1


def test_some_other_non_json_return_is_still_reported_as_itself():
    """Only the missing-binary case gets the new sentence; anything else must
    keep saying what it actually said, or this becomes a catch-all that hides
    the next unfamiliar failure."""
    out = salt.parse_return("Traceback (most recent call last):\n  File ...\n")
    err = out["error"] or ""
    assert "not installed" not in err.lower(), err
    assert "Traceback" in err, err


def test_an_empty_return_is_not_claimed_to_be_a_missing_salt():
    """Nothing at all came back. That is its own failure and we do not know
    which one it was."""
    err = (salt.parse_return("")["error"] or "").lower()
    assert "not installed" not in err, err
