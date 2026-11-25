"""`sysiblectl first-run` is what a new host runs, and it must not lie.

Asked for as: "instead of having sudo ./install.sh as the first run in slop it
should be sysiblectl first-run. This should do a complete fresh install of
everything."

Two things made that more than a rename.

ONE. `install.sh`'s own comment says the one-checkout profile is what "a FRESH
host should" use, and docs/INTEGRATED.md documents it under "A fresh host" --
but plain `sudo ./install.sh` does not pass --integrated. So a new host that
followed the README got the older layout, three cloned repositories at three
commits, unless it knew to add a flag. first-run passes it.

TWO. That profile must NOT be forced onto a host already running the apps the
old way: their container names and ports are taken, and in install.sh's words
switching by surprise "is how a fleet's Controller database goes missing".
tools/migrate-to-integrated moves the data first. first-run is the only verb
here that refuses to run, and this pins that refusal.

The tests RUN the shim rather than reading it, under dash, because /bin/sh is
dash on Debian and the two shells do not agree about the thing that was broken.
"""
import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHIM = ROOT / "deploy" / "sysiblectl"
CLI = ROOT / "apps" / "controller" / "deploy" / "sysiblectl"

SH = shutil.which("dash") or shutil.which("sh")


@pytest.fixture()
def host(tmp_path):
    """A fake SLOP checkout with a stub install.sh, and stub docker on PATH."""
    repo = tmp_path / "slop"
    (repo / "deploy").mkdir(parents=True)
    (repo / "tools").mkdir()
    shutil.copy(SHIM, repo / "deploy" / "sysiblectl")
    os.chmod(repo / "deploy" / "sysiblectl", 0o755)
    inst = repo / "install.sh"
    inst.write_text('#!/bin/sh\necho "INSTALL.SH: $*"\n')
    os.chmod(inst, 0o755)
    bindir = tmp_path / "bin"
    bindir.mkdir()

    class Host:
        path = repo

        def containers(self, names):
            d = bindir / "docker"
            if names is None:
                d.unlink(missing_ok=True)
                return
            body = ['#!/bin/sh', 'case "$*" in', '  *"ps -a"*)']
            body += [f'    echo {n}' for n in names]
            body += ['    ;;', 'esac']
            d.write_text("\n".join(body) + "\n")
            os.chmod(d, 0o755)

        def integrated(self, yes):
            env = repo / ".env"
            if yes:
                env.write_text("SYSIBLE_INTEGRATED=1\n")
            else:
                env.unlink(missing_ok=True)

        def run(self, *args):
            real = str(pathlib.Path(shutil.which("grep")).parent)
            return subprocess.run(
                [SH, "./deploy/sysiblectl", *args], cwd=repo, capture_output=True,
                text=True, timeout=120,
                env={**os.environ, "PATH": f"{bindir}:{real}:/usr/bin:/bin"})

    h = Host()
    h.containers([])
    h.integrated(False)
    return h


def test_a_fresh_host_gets_the_profile_a_fresh_host_should_have(host):
    r = host.run("first-run")
    assert r.returncode == 0, r.stderr
    assert "INSTALL.SH: all --integrated" in r.stdout, r.stdout


def test_the_bare_command_does_the_same_thing(host):
    """`sudo ./deploy/sysiblectl` with no argument at all. This exited 2 in
    SILENCE and installed nothing: `shift` with no positional parameters is a
    special builtin error, and under `set -e` in dash a special builtin's
    failure exits the shell -- `|| true` does not rescue it. bash survives the
    identical line, which is why it was never seen while developing."""
    r = host.run()
    assert r.returncode == 0, f"rc={r.returncode} out={r.stdout!r} err={r.stderr!r}"
    assert "INSTALL.SH: all --integrated" in r.stdout, r.stdout


def test_the_shift_that_broke_it_is_not_written_that_way_anywhere(host):
    """The root cause, not just this one symptom."""
    assert "shift 2>/dev/null || true" not in SHIM.read_text(), (
        "an unguarded `shift` under set -e in dash exits the script")


def test_an_old_layout_host_is_refused_and_told_where_to_go(host):
    host.containers(["sysible-controller"])
    r = host.run("first-run")
    assert r.returncode != 0, r.stdout
    assert "INSTALL.SH" not in r.stdout, "it ran the installer over a live host"
    both = r.stdout + r.stderr
    assert "migrate-to-integrated" in both, both
    assert "sysible-controller" in both, both


@pytest.mark.parametrize("name", ["sysible-controller", "sysible-slep", "sysible-connect"])
def test_any_one_of_the_three_is_enough_to_refuse(host, name):
    """One app of the old layout still owns its name and port."""
    host.containers([name])
    r = host.run("first-run")
    assert r.returncode != 0, f"{name} did not stop it"


def test_a_host_that_is_already_integrated_may_re_run(host):
    """Re-running after editing .env or pulling is the normal case, not a
    collision -- there is nothing of the old layout to collide with."""
    host.containers(["sysible-controller"])
    host.integrated(True)
    r = host.run("first-run")
    assert r.returncode == 0, r.stderr
    assert "INSTALL.SH: all --integrated" in r.stdout


def test_unrelated_containers_are_not_mistaken_for_the_old_layout(host):
    host.containers(["nginx", "postgres", "sysible-slop-gateway"])
    r = host.run("first-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "INSTALL.SH: all --integrated" in r.stdout


def test_no_docker_at_all_does_not_stop_a_fresh_install(host):
    """Docker is what install.sh installs. Needing it to decide whether to run
    would mean the one command a bare host runs cannot run on a bare host."""
    host.containers(None)
    r = host.run("first-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "INSTALL.SH: all --integrated" in r.stdout


@pytest.mark.parametrize("verb,expect", [
    ("install", "INSTALL.SH: all"),
    ("apps", "INSTALL.SH: apps"),
    ("gateway", "INSTALL.SH: gateway"),
])
def test_the_existing_verbs_are_untouched(host, verb, expect):
    r = host.run(verb)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == expect, r.stdout
    if verb == "install":
        assert "--integrated" not in r.stdout, (
            "install must stay what it was; first-run is the one that changes the profile")


# --- the CLI that is actually on PATH ---------------------------------------

def test_the_installed_cli_understands_it_too():
    """The command is written `sysiblectl first-run`, with no path. On a Sysible
    host that is the preinstalled CLI, not this shim, so the verb has to exist
    there as well or the documented command is simply wrong."""
    src = CLI.read_text()
    assert "first-run|firstrun|first_run)" in src, "no first-run in the CLI's dispatch"
    assert "cmd_first_run()" in src, "no cmd_first_run"


def test_the_installed_cli_also_refuses_an_old_layout_host():
    """The guard has to live in both entry points, or the safe one is whichever
    the operator happened to type."""
    body = CLI.read_text()
    fn = body[body.index("cmd_first_run()"):]
    fn = fn[:fn.index("\n}\n") + 3]
    assert "SYSIBLE_INTEGRATED=1" in fn, fn
    assert "migrate-to-integrated" in fn, fn
    assert "--integrated" in fn, "the CLI must pass the fresh-host profile too"


def test_the_help_points_at_first_run_not_install():
    """`install` only symlinks the CLI. Advertising it as the first command is
    how an operator ends up with a symlink and no platform."""
    src = CLI.read_text()
    i = src.index("First time on a box")
    line = src[i:i + 200]
    assert "first-run" in line, line
    assert "./deploy/sysiblectl install" not in line, line


# --- the documentation ------------------------------------------------------

@pytest.mark.parametrize("doc", ["README.md", "docs/INTEGRATED.md"])
def test_the_docs_lead_with_first_run(doc):
    text = (ROOT / doc).read_text()
    assert "first-run" in text, f"{doc} never mentions it"
    # The fresh-host instruction must not still be the bare installer, which
    # silently gives the older layout.
    for bad in ("sudo ./install.sh --integrated\n```", "sudo ./install.sh                 #"):
        assert bad not in text, f"{doc} still tells a fresh host to run: {bad!r}"


# --- the hand-off to the installed CLI --------------------------------------

def _code_only(path):
    """Comments stripped: the shim's comment NAMES the broken option in order to
    explain why it is not used."""
    return "\n".join(l for l in path.read_text().splitlines()
                     if not l.lstrip().startswith("#"))


def test_the_shim_can_find_the_installed_cli_at_all():
    """It used `command -v -a`, which exists in NEITHER dash nor bash -- it is a
    zsh/ksh spelling. Under this file's own #!/bin/sh it failed outright, and the
    `|| true` after it made the result silently empty. So REAL was always empty
    and every management verb reported "sysiblectl is not installed yet" on a
    host where it plainly was."""
    assert "command -v -a" not in _code_only(SHIM), \
        "command -v -a fails in dash AND bash; the lookup is always empty"


def test_a_management_verb_reaches_the_real_cli(host, tmp_path):
    real = tmp_path / "realbin"
    real.mkdir()
    (real / "sysiblectl").write_text('#!/bin/sh\necho "REAL-CLI: $*"\n')
    os.chmod(real / "sysiblectl", 0o755)
    grep_dir = str(pathlib.Path(shutil.which("grep")).parent)
    r = subprocess.run(
        [SH, "./deploy/sysiblectl", "status"], cwd=host.path, capture_output=True,
        text=True, timeout=60,
        env={**os.environ, "PATH": f"{real}:{grep_dir}:/usr/bin:/bin"})
    assert r.returncode == 0, f"{r.stdout!r} {r.stderr!r}"
    assert "REAL-CLI: status" in r.stdout, r.stdout


def test_two_shims_on_path_do_not_exec_each_other_forever():
    """Each shim skips ITSELF, which is not enough once the OS ships one too."""
    assert "SYSIBLE_CTL_HANDOFF" in SHIM.read_text(), "no one-hop guard"
