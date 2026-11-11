"""tools/migrate-to-integrated: the order it does things in.

Moving a host from three app checkouts to one is a handful of docker commands,
and docs/INTEGRATED.md lists them. The reason there is a script as well is that
two of those commands are the ones nobody can write down correctly:

  * what your volumes are CALLED. Compose namespaces a volume by project, and
    the project name comes from whichever directory each stack was first brought
    up from -- so on a real host the Controller's is sysible-controller_… and
    SLEP's and Connect's are both deploy_…, which no document would have
    guessed. The script asks the running containers.

  * WHEN the data moves. It has to be in the new volume before the app starts,
    not after. Started first, each app initialises its own volume: a fresh
    database and a fresh TLS certificate. Agents PIN the Controller's
    certificate, so a copy made afterwards is racing an init that has already
    broken the fleet.

These tests drive the script against a stand-in docker and check the ORDER and
the REFUSALS, which is all of it that can be wrong without anyone noticing until
their data is gone.

Run: pytest tests/
"""
import os
import re
import shutil
import subprocess
import textwrap

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "tools", "migrate-to-integrated")

# The script stops containers and writes to /var/backups; it refuses to run as
# anyone but root, and that refusal is itself one of the tests below.
_IS_ROOT = os.geteuid() == 0

_FAKE_DOCKER = r"""#!/usr/bin/env bash
S="$FAKE_DOCKER_STATE"
printf '%s\n' "docker $*" >> "$S/calls.log"
case "$1" in
  info) exit 0 ;;
  compose)
    case "$*" in
      *version*) exit 0 ;;
      *"ps --format"*)
        for s in gateway idp flashback baseline visualizer updater controller slep connect; do
          echo "$s running"; done; exit 0 ;;
      *) exit 0 ;;
    esac ;;
  inspect)
    name="${@: -1}"
    grep -qx "$name" "$S/containers" 2>/dev/null || exit 1
    if [[ "$*" == *".Mounts"* ]]; then
      case "$name" in
        sysible-controller) echo "sysible-controller_sysible-data" ;;
        sysible-slep)       echo "deploy_slep-data" ;;
        sysible-connect)    echo "deploy_connect-data" ;;
      esac
    fi
    exit 0 ;;
  image) grep -qx "${*: -1}" "$S/images" 2>/dev/null && exit 0 || exit 1 ;;
  volume)
    case "$2" in
      inspect) grep -qx "$3" "$S/volumes" 2>/dev/null && exit 0 || exit 1 ;;
      create)  echo "$3" >> "$S/volumes"; echo "$3"; exit 0 ;;
      ls)      cat "$S/platform_volumes" 2>/dev/null; exit 0 ;;
      rm)      echo "REFUSED: the migration must never delete a volume" >&2; exit 97 ;;
    esac ;;
  run)
    [ -f "$S/fail_tar" ]  && [[ "$*" == *"tar czf"*   ]] && { echo "disk full" >&2; exit 1; }
    [ -f "$S/fail_copy" ] && [[ "$*" == *"cp -a"*     ]] && { echo "copy failed" >&2; exit 1; }
    [[ "$*" == *"ls -A /v | wc -l"* ]] && { cat "$S/newvol_filecount" 2>/dev/null || echo 0; }
    exit 0 ;;
  stop|rm|pull) exit 0 ;;
esac
exit 0
"""


@pytest.fixture()
def host(tmp_path):
    """A stand-in platform checkout, and a docker that records what it is told."""
    bindir, state, root = tmp_path / "bin", tmp_path / "state", tmp_path / "slop-host"
    for d in (bindir, state, root / "tools"):
        d.mkdir(parents=True)
    (bindir / "docker").write_text(_FAKE_DOCKER)
    (bindir / "docker").chmod(0o755)
    shutil.copy2(SCRIPT, root / "tools" / "migrate-to-integrated")
    for name in ("docker-compose.yml", "docker-compose.integrated.yml"):
        shutil.copy2(os.path.join(REPO, name), root / name)
    for a in ("controller", "slep", "connect"):
        (root / "apps" / a).mkdir(parents=True)
    (root / ".env").write_text("SYSIBLE_SSO_SHARED_SECRET=abc123\n")
    (state / "containers").write_text("sysible-controller\nsysible-slep\nsysible-connect\n")
    (state / "images").write_text("alpine:latest\n")
    (state / "volumes").write_text(
        "sysible-controller_sysible-data\ndeploy_slep-data\ndeploy_connect-data\n")
    (state / "platform_volumes").write_text("slop-host_slop-idp-data\n")
    (state / "calls.log").write_text("")

    class H:
        def __init__(self):
            self.root, self.state, self.bin = root, state, bindir

        def run(self, *args):
            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"
            env["FAKE_DOCKER_STATE"] = str(state)
            return subprocess.run(
                ["bash", str(root / "tools" / "migrate-to-integrated"), *args],
                capture_output=True, text=True, env=env)

        @property
        def calls(self):
            return (state / "calls.log").read_text().splitlines()

        def index_of(self, pattern):
            for i, line in enumerate(self.calls):
                if re.search(pattern, line):
                    return i
            return -1

    return H()


# ---- what it refuses to do ------------------------------------------------
@pytest.mark.skipif(_IS_ROOT, reason="this check only fires for a non-root caller")
def test_it_refuses_to_run_without_root(host):
    r = host.run("--dry-run")
    assert r.returncode != 0 and "sudo" in (r.stdout + r.stderr)


@pytest.mark.skipif(not _IS_ROOT, reason="needs to get past the root check")
@pytest.mark.parametrize("break_it,expected", [
    (lambda h: (h.root / "docker-compose.integrated.yml").unlink(), "predates"),
    (lambda h: shutil.rmtree(h.root / "apps" / "slep"), "apps/slep is missing"),
    (lambda h: (h.root / ".env").write_text("# nothing\n"), "SYSIBLE_SSO_SHARED_SECRET"),
])
def test_it_stops_before_touching_a_host_it_cannot_migrate(host, break_it, expected):
    break_it(host)
    r = host.run("--dry-run")
    assert r.returncode != 0, r.stdout
    assert expected in r.stdout + r.stderr
    assert not [c for c in host.calls if re.match(r"docker (stop|rm) ", c)], \
        "it stopped something on a host it then refused to migrate"


@pytest.mark.skipif(not _IS_ROOT, reason="needs to get past the root check")
def test_a_host_with_nothing_to_migrate_is_told_to_just_install(host):
    (host.state / "containers").write_text("")
    r = host.run("--dry-run")
    assert r.returncode == 0
    assert "nothing to migrate" in r.stdout
    assert "install.sh --integrated" in r.stdout


@pytest.mark.skipif(not _IS_ROOT, reason="needs to get past the root check")
def test_a_destination_volume_with_data_in_it_stops_everything_first(host):
    """A previous attempt that got as far as copying. The answer is "go and look
    at it", and nobody should have to do that with their fleet offline -- so
    this is checked BEFORE anything is stopped, not at the copy."""
    (host.state / "volumes").write_text(
        (host.state / "volumes").read_text() + "slop-host_controller-data\n")
    (host.state / "newvol_filecount").write_text("42\n")
    r = host.run("--yes")
    assert r.returncode != 0, r.stdout
    assert "NOTHING has been stopped" in r.stdout + r.stderr
    assert not [c for c in host.calls if re.match(r"docker (stop|rm) ", c)]
    assert not [c for c in host.calls if "tar czf" in c], \
        "it took a backup on a run it was always going to refuse"


@pytest.mark.skipif(not _IS_ROOT, reason="needs to get past the root check")
def test_a_failed_backup_stops_before_anything_comes_down(host):
    (host.state / "fail_tar").write_text("")
    r = host.run("--yes")
    assert r.returncode != 0
    assert "nothing has been changed yet" in r.stdout + r.stderr
    assert not [c for c in host.calls if re.match(r"docker (stop|rm) ", c)], \
        "the apps were stopped although the backup had already failed"


# ---- the order it does things in ------------------------------------------
@pytest.mark.skipif(not _IS_ROOT, reason="needs to get past the root check")
def test_it_backs_up_before_it_stops_anything(host):
    assert host.run("--yes").returncode == 0, "the happy path failed"
    first_backup = host.index_of(r"tar czf")
    first_stop = host.index_of(r"^docker stop ")
    assert first_backup >= 0 and first_stop >= 0
    assert first_backup < first_stop, (
        "it stopped an app before its data had been backed up")


@pytest.mark.skipif(not _IS_ROOT, reason="needs to get past the root check")
def test_the_data_is_in_place_before_the_apps_start(host):
    """The one that matters. Started first, each app initialises its own volume
    -- a fresh database, and a fresh TLS certificate that agents have not
    pinned."""
    assert host.run("--yes").returncode == 0
    last_copy = max(i for i, c in enumerate(host.calls) if "cp -a" in c)
    up = host.index_of(r"compose .*up -d")
    assert up >= 0, "it never brought the platform up"
    assert last_copy < up, "an app was started before its data had been moved into place"


@pytest.mark.skipif(not _IS_ROOT, reason="needs to get past the root check")
def test_it_never_deletes_a_volume(host):
    """The stand-in docker fails the command outright if it is asked to. This is
    what makes the whole thing reversible, and it is the one line of the script
    that must never be written."""
    assert host.run("--yes").returncode == 0
    assert not [c for c in host.calls if c.startswith("docker volume rm")]
    # An INVOCATION, not the phrase: the script tells the operator how to
    # remove a volume by hand when it refuses, which is the opposite of doing
    # it. A command would be at the start of a line, optionally behind the
    # run/run_sh wrappers everything mutating goes through.
    src = open(SCRIPT, encoding="utf-8").read()
    invocations = [ln for ln in src.splitlines()
                   if re.match(r"\s*(run |run_sh ['\"]?)?docker volume rm\b", ln)]
    assert not invocations, f"the script runs a volume rm: {invocations}"


@pytest.mark.skipif(not _IS_ROOT, reason="needs to get past the root check")
def test_the_host_is_only_marked_integrated_once_the_data_has_moved(host):
    """SYSIBLE_INTEGRATED is what sysiblectl and the updater read. Setting it on
    a run that then fails would leave a host claiming a layout it is not in."""
    (host.state / "fail_copy").write_text("")
    r = host.run("--yes")
    assert r.returncode != 0
    assert "SYSIBLE_INTEGRATED=1" not in (host.root / ".env").read_text()
    assert "--rollback" in r.stdout + r.stderr, \
        "it did not say how to undo a half-done migration"


@pytest.mark.skipif(not _IS_ROOT, reason="needs to get past the root check")
def test_the_volume_names_follow_compose_s_own_rule(host):
    """<project>_<name>, where the project is the lowercased checkout directory
    with anything outside [a-z0-9_-] removed -- the same rule sysiblectl's
    _compose_project implements, because it is compose's."""
    assert host.run("--yes").returncode == 0
    created = [c.split()[-1] for c in host.calls if c.startswith("docker volume create")]
    # "slop-host", not "slophost": a hyphen is legal in a compose project name
    # and is kept. Getting that wrong is how a migration creates a second,
    # empty set of volumes beside the ones it just filled.
    assert created == ["slop-host_controller-data", "slop-host_slep-data",
                       "slop-host_connect-data"], created


@pytest.mark.skipif(not _IS_ROOT, reason="needs to get past the root check")
def test_rollback_puts_the_marker_back_and_deletes_nothing(host):
    assert host.run("--yes").returncode == 0
    assert "SYSIBLE_INTEGRATED=1" in (host.root / ".env").read_text()
    (host.state / "calls.log").write_text("")
    r = host.run("--rollback")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SYSIBLE_INTEGRATED=0" in (host.root / ".env").read_text()
    assert not [c for c in host.calls if c.startswith("docker volume rm")]


@pytest.mark.skipif(not _IS_ROOT, reason="needs to get past the root check")
def test_dry_run_changes_nothing_at_all(host):
    before = (host.root / ".env").read_text()
    r = host.run("--dry-run")
    assert r.returncode == 0
    assert (host.root / ".env").read_text() == before
    mutating = [c for c in host.calls
                if re.match(r"docker (stop|rm|volume create) ", c)
                or "tar czf" in c or "cp -a" in c]
    assert not mutating, f"--dry-run issued mutating commands: {mutating}"
