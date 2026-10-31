"""A declared state is DATA. It must never become shell on the host.

apply_command() ships the SLS inside a quoted heredoc, which is the right
transport — states are multi-line and full of quotes, backslashes and `$`, and
a quoted delimiter stops all of that being expanded. What a quoted delimiter
does NOT stop is a body line that IS the delimiter: the shell ends the heredoc
there and reads everything after it as commands.

That mattered more than it looks:

  * The injected lines run BEFORE salt-call is reached, so `test=True` — the
    console's "preview", the default path for a state nobody has applied yet —
    executes them too. Previewing a state someone sent you was code execution.
  * The Controller dispatches baseline-apply with want_root=True, so they run
    as root on every host the state is assigned to.
  * Salt never sees them, so the run record, the event rules and the Flashback
    capture all describe a clean preview.

A `cmd.run` state is a legitimate way for an operator to run a command on a
host, so this is not about what an operator may do. It is about a dry run doing
anything at all, and about doing it outside everything that records it.
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from backend import salt  # noqa: E402

SHELLS = [s for s in ("/bin/dash", "/bin/bash") if os.path.exists(s)]


def _run(command, shell, cwd):
    """Run the generated command with salt-call stubbed out, and report what the
    shell actually did."""
    bindir = os.path.join(cwd, "bin")
    os.makedirs(bindir, exist_ok=True)
    stub = os.path.join(bindir, "salt-call")
    with open(stub, "w") as fh:
        fh.write('#!/bin/sh\necho "{\\"local\\": {}}"\n')
    os.chmod(stub, 0o755)
    env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"])
    return subprocess.run([shell, "-c", command], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("preview", [True, False])
def test_a_body_line_equal_to_the_delimiter_cannot_run_a_command(shell, preview, tmp_path):
    marker = tmp_path / "PWNED"
    # Exactly the shape that worked: close the heredoc, run a command, then
    # re-open one so the trailing delimiter the builder appends still lands
    # inside a heredoc and the command stays syntactically valid.
    attack = "\n".join([
        "pkg_x:",
        "  pkg.installed:",
        "    - name: curl",
        "SYSIBLE_BASELINE_SLS",
        f"touch {marker}",
        "cat > /dev/null <<'SYSIBLE_BASELINE_SLS'",
        "trailer: 1",
    ])
    try:
        cmd = salt.apply_command(attack, test=preview, state_name="demo")
    except ValueError:
        return  # refused before it could be dispatched — the whole point
    _run(cmd, shell, str(tmp_path))
    assert not marker.exists(), (
        f"the state body executed a command on the host (preview={preview})")


@pytest.mark.parametrize("shell", SHELLS)
def test_the_sls_reaches_the_host_byte_for_byte(shell, tmp_path):
    """The transport still has to work: quotes, backslashes, $ and whitespace in
    file contents are ordinary in a state and must survive unchanged."""
    sls = "\n".join([
        "/etc/motd:",
        "  file.managed:",
        "    - contents: |",
        "        \"quoted\" and 'single' and $HOME and `backticks`",
        "        a\\backslash and a trailing space ",
        "        $(id) ${PATH} *glob* #hash",
        "        SYSIBLE_BASELINE_SLS_but_not_exactly",
        "          deeper indent",
    ])
    cmd = salt.apply_command(sls, state_name="motd")
    # Keep the written file for inspection instead of letting the trap remove it.
    cmd = cmd.replace("trap 'rm -rf \"$_r\"' EXIT; ",
                      f"trap 'cp -a \"$_r/states\"/*.sls {tmp_path}/out.sls' EXIT; ")
    r = _run(cmd, shell, str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "out.sls").read_text() == sls + "\n"


def test_the_delimiter_is_not_a_value_an_author_can_know():
    """Second line of defence. The check above is an exact string comparison
    against the delimiter, and it is only correct if it models the shell's
    delimiter matching exactly. So the delimiter is also derived from the body:
    to write the line that ends the heredoc an author would have to know the
    hash of text that contains that hash.
    """
    a = salt.apply_command("a: 1\n")
    b = salt.apply_command("b: 2\n")
    da = salt._heredoc_delimiter("a: 1")
    assert da in a and da not in b, "the delimiter does not vary with the body"
    # Deterministic, so the command for a given state is stable and reviewable.
    assert salt.apply_command("a: 1\n") == a


def test_the_guard_refuses_a_body_that_closes_the_heredoc():
    """The guard itself, fed a collision by hand.

    apply_command cannot be made to hit it any more — that is what the derived
    delimiter buys — so the guard is exercised directly. It stays in the code
    because it is the assertion that makes the derivation load-bearing: point
    the delimiter back at a fixed string and this is what still refuses.
    """
    with pytest.raises(ValueError) as ei:
        salt._reject_delimiter_line("a: 1\nDELIM\nb: 2", "DELIM")
    msg = str(ei.value).lower()
    assert "heredoc" in msg or "delimiter" in msg

    # Exact match only, as the shell does it: indented or padded is still data.
    for near in ("a: 1\n DELIM\nb: 2", "a: 1\nDELIM \nb: 2", "a: 1\nDELIMX\nb: 2",
                 "a: 1\nxDELIM\nb: 2"):
        salt._reject_delimiter_line(near, "DELIM")
