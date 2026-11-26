"""The copy of the CLI that SHIPS must be able to install without a network.

apps/controller/deploy/sysiblectl is not a convenience copy. The ISO ships this
whole checkout, install-sysible links THIS file onto the host's PATH, and an
installed machine runs it. The standalone Sysible-Controller repository is a
publish target (apps/VENDORED.json), so work that lands only there does not
reach a single user.

That is exactly what happened. The airgapped-install support -- ship the built
images, load them at boot, and start from them instead of rebuilding -- was
written in the standalone repo. This copy had none of it: no
_images_all_present, no _registry_ready, and an unconditional `up -d --build`.
So every service's Dockerfile went to PyPI, npm, Debian and Alpine, and the
install failed with the finished images sitting in the local store, unused. Four
ISOs were built before anyone checked which copy actually runs.

Two further things these pin, both of which shipped broken:

  * `$ME` was referenced in the no-rebuild branch and assigned nowhere. Under
    `set -euo pipefail` that is not an empty string, it is an immediate exit --
    inside the branch that runs ONLY when every image is already present, which
    is precisely the airgapped host the feature was for. `bash -n` passes it.
  * install.sh cloned the Controller unconditionally, including for `gateway`,
    which is the mode sysiblectl hands off to. The clone is how the CLI reaches
    the host, so it ran everywhere -- and it cannot succeed on an isolated
    network, where the vendored copy is sitting right there.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "apps/controller/deploy/sysiblectl"
INSTALL = ROOT / "install.sh"


def _code(p):
    """The file with comment lines and trailing comments removed."""
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(re.sub(r"(?<!\\)\s#.*$", "", line))
    return "\n".join(out)


def test_the_vendored_cli_exists():
    assert CLI.exists(), (
        "apps/controller/deploy/sysiblectl is missing — install-sysible links this "
        "file onto an installed host's PATH, so without it nothing can be managed"
    )


@pytest.mark.parametrize("helper", ["_images_all_present", "_registry_ready"])
def test_the_vendored_cli_knows_whether_it_needs_a_registry(helper):
    code = _code(CLI)
    # Defined AND called, matched on a word boundary. A plain `helper in code`
    # passed when the name was renamed to `_images_all_presentX`, because the
    # old name is a substring of the new one -- the test survived the exact
    # mutation it exists to catch.
    defined = re.search(rf"^{re.escape(helper)}\(\)\s*\{{", code, re.M)
    called = re.search(rf"(?<![\w.]){re.escape(helper)}\b\s+\S", code)
    assert defined and called, (
        f"{helper} is missing from the CLI that ships. Without it the first "
        f"bring-up rebuilds every service from source, which needs PyPI, npm, "
        f"Debian and Alpine — so an airgapped install fails at the last step "
        f"with the shipped images already in the local store. The standalone "
        f"repo having it is not enough: this is the copy that runs."
    )


def test_the_first_bring_up_does_not_force_a_rebuild():
    """p_rebuild must decide; p_update must always rebuild."""
    code = _code(CLI)
    m = re.search(r"\np_rebuild\(\)\s*\{(.*?)\n\}\n", code, re.S)
    assert m, "p_rebuild not found in the vendored CLI"
    body = m.group(1)
    assert "up -d --build" not in body, (
        "p_rebuild passes --build unconditionally, so a host that already holds "
        "every image rebuilds anyway and an airgapped install cannot work"
    )
    assert re.search(r"(?<![\w.])_images_all_present\b", body), (
        "p_rebuild does not check whether the images are already here"
    )
    # The update path is the opposite case and must keep rebuilding.
    m2 = re.search(r"\np_update\(\)\s*\{(.*?)\n\}\n", code, re.S)
    assert m2, "p_update not found"
    assert "--build" in m2.group(1), (
        "p_update stopped rebuilding. An update is new source; without --build it "
        "would restart the old image and report success"
    )


def test_install_sh_does_not_clone_what_this_checkout_carries():
    code = _code(INSTALL)
    assert "dir_for" in code, (
        "install.sh has no resolver for the apps this checkout vendors, so it "
        "clones them from GitHub — which cannot work on an isolated network"
    )
    m = re.search(r"clone_one\s+\"\$CTL_REPO\"[^\n]*", code)
    assert m, "the Controller clone call is gone — check this test still applies"
    # It must be reachable only when there is no vendored copy to use.
    assert "controller" in m.group(0), (
        "clone_one is called for the Controller without naming the product, so "
        "it cannot tell that apps/controller is already on disk"
    )


# ---- the set -u landmine, which bash -n cannot see ------------------------
PROVIDED = set("""
0 1 2 3 4 5 6 7 8 9 @ * # ? - $ ! _ IFS PATH HOME PWD OLDPWD SHELL USER LOGNAME
TERM LANG LC_ALL HOSTNAME UID EUID PPID RANDOM SECONDS LINENO BASH BASH_SOURCE
BASH_VERSION FUNCNAME PIPESTATUS REPLY OPTARG OPTIND SHLVL TMPDIR EDITOR VISUAL
PAGER COLUMNS LINES SUDO_USER SUDO_UID SUDO_GID DEBIAN_FRONTEND NEWT_COLORS
BASH_REMATCH GLOBIGNORE HOSTTYPE OSTYPE MACHTYPE DIRSTACK CDPATH
""".split())
ASSIGN = re.compile(r"""(?mx)
    (?:^|[;&|(){}\s])
    (?:local\s+|declare\s+(?:-\w+\s+)?|export\s+|readonly\s+|typeset\s+)?
    ([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]]*\])?\+?=
""")
LOOPVAR = re.compile(r"(?m)\bfor\s+([A-Za-z_][A-Za-z0-9_]*)\b")
READVAR = re.compile(r"(?m)\bread\s+(?:-\w+\s+)*((?:[A-Za-z_][A-Za-z0-9_]*\s*)+)")
MAPFILE = re.compile(r"(?m)\bmapfile\s+(?:-\w+\s+)*([A-Za-z_][A-Za-z0-9_]*)")
GETOPTS = re.compile(r"(?m)\bgetopts\s+\S+\s+([A-Za-z_][A-Za-z0-9_]*)")
REF_PLAIN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
REF_GUARDED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?[-=+?]|##|%%|#|%|//|/|\^|,)")


def _unbound(path):
    src = path.read_text(encoding="utf-8", errors="replace")
    if not re.search(r"^set\s+-\w*u", src, re.M):
        return []
    assigned = set(PROVIDED)
    for rx in (ASSIGN, LOOPVAR, MAPFILE, GETOPTS):
        assigned |= {m.group(1) for m in rx.finditer(src)}
    for m in READVAR.finditer(src):
        assigned |= set(m.group(1).split())
    assigned |= {m.group(1) for m in REF_GUARDED.finditer(src)}
    out = []
    for n, line in enumerate(src.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for m in REF_PLAIN.finditer(re.sub(r"(?<!\\)#.*$", "", line)):
            name = m.group(1) or m.group(2)
            if name not in assigned:
                out.append((n, name, line.strip()[:100]))
    return out


@pytest.mark.parametrize("script", [CLI, INSTALL], ids=lambda p: p.name)
def test_no_variable_is_referenced_without_ever_being_assigned(script):
    bad = _unbound(script)
    assert not bad, "under `set -u` each of these exits the script:\n" + "\n".join(
        f"  {script.name}:{n}: ${name}\n      {line}" for n, name, line in bad
    )
