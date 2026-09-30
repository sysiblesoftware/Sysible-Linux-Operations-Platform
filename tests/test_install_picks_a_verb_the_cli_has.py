"""install.sh must not ask the CLI for a verb the CLI does not have.

This shipped and broke a real install:

    == updating sysible-controller ==
    Already up to date.
    Installed sysiblectl -> /usr/local/bin/sysiblectl
    ERROR: 'rebuild' is not a command. Commands: up build update status ...
      WARNING: controller did not come up — continuing.
    ...
    Finished WITH PROBLEMS — these did not come up: controller slep connect slop-gateway

The CLI was renamed and its verbs were cut down in the Controller repository;
this installer was updated in the same breath to say `rebuild`. But they are two
repositories that reach a host at different times — the platform installer pulls
whatever the Controller's main branch has right now — so for as long as one is
ahead of the other, every single product fails at the first command.

The fix is not to pick the right verb, it is to stop guessing: ask the CLI, using
its own parser and a product name that cannot exist. Both answers are refusals
raised while parsing the arguments, so nothing is built, started or touched
either way:

    new CLI:  'rebuild' is a command, '__probe__' is not a product
    old CLI:  'rebuild' is not a command at all

These tests run the real probe out of install.sh against both shapes of CLI.
"""
import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "install.sh"

# What each CLI does with `rebuild __probe__`. Lifted from the real refusals.
NEW_CLI = """#!/bin/sh
echo "ERROR: '__probe__' is not a product. Name the one you mean: sysiblectl <product> rebuild __probe__" >&2
echo "  products: controller slep connect slop, or all" >&2
exit 1
"""
OLD_CLI = """#!/bin/sh
echo "ERROR: unknown or ambiguous: 'rebuild __probe__'" >&2
echo "ERROR: Write it as: sysiblectl <product|all> <command>   (either order)" >&2
echo "ERROR:   commands: up build update status logs restart start stop backup destroy" >&2
exit 1
"""
# A CLI that is not installed at all — the probe must still answer something.
NO_CLI = None


@pytest.fixture(scope="module")
def probe_src():
    """The real probe, lifted out of install.sh."""
    src = INSTALL.read_text(encoding="utf-8")
    m = re.search(r"^CTL_BUILD_VERB=up\n(?:.*\n)*?^fi\n", src, re.M)
    assert m, "install.sh no longer probes for the build verb"
    return m.group(0)


def _verb(probe_src, tmp_path, cli_body):
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    if cli_body is not None:
        cli = bindir / "sysiblectl"
        cli.write_text(cli_body)
        cli.chmod(0o755)
    script = probe_src + '\nprintf %s "$CTL_BUILD_VERB"\n'
    r = subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                       env={"PATH": f"{bindir}:/usr/bin:/bin"}, timeout=60)
    return r.stdout.strip()


# ---- the reported failure --------------------------------------------------
def test_an_older_cli_gets_the_verb_it_has(probe_src, tmp_path):
    """The exact break: install.sh asked for `rebuild` and every product failed."""
    assert _verb(probe_src, tmp_path / "old", OLD_CLI) == "up"


def test_a_current_cli_gets_the_verb_that_replaced_it(probe_src, tmp_path):
    assert _verb(probe_src, tmp_path / "new", NEW_CLI) == "rebuild"


def test_no_cli_at_all_still_answers(probe_src, tmp_path):
    """The probe must not abort the installer when the CLI is missing — the link
    step above it already fails loudly for that, with a better message."""
    assert _verb(probe_src, tmp_path / "none", NO_CLI) == "up"


# ---- and it must stay side-effect free -------------------------------------
def test_the_probe_names_a_product_that_cannot_exist(probe_src):
    """This is what makes it safe to run. With a REAL product name, a CLI that
    knows `rebuild` would go ahead and rebuild it — the installer would have
    built something just to find out which word to use."""
    assert "__probe__" in probe_src
    for real in ("controller", "slep", "connect", "slop", "all"):
        assert not re.search(rf"rebuild\s+{real}\b", probe_src), \
            f"the probe would actually rebuild {real}"


def test_the_probe_reads_the_refusal_and_not_the_exit_code(probe_src):
    """Both answers exit non-zero — the exit code says only that something was
    refused, not which thing."""
    assert "is not a product" in probe_src, \
        "the probe no longer distinguishes the two refusals by their text"


def test_both_bring_up_calls_use_the_probed_verb():
    """One of the two hard-coded would be the same outage, half the time."""
    # Real command lines only. A comment mentioning the old spelling is prose,
    # not a call, and matching one reports a bug that is not there.
    lines = [ln for ln in INSTALL.read_text(encoding="utf-8").splitlines()
             if not ln.lstrip().startswith("#")]
    calls = [ln.strip() for ln in lines
             if re.search(r'sysiblectl (?:"\$p"|slop) \S', ln)]
    assert calls, "the bring-up calls are gone"
    for ln in calls:
        assert "CTL_BUILD_VERB" in ln, f"a bring-up call hard-codes its verb: {ln}"


def test_install_sh_is_valid_posix_sh():
    r = subprocess.run(["sh", "-n", str(INSTALL)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
