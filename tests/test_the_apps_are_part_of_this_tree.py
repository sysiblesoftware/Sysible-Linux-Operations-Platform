"""The Controller, SLEP and Connect are directories in this repository.

They used to be three separate git checkouts on the host: install.sh cloned
three repositories into /opt/sysible-src and brought up three more compose
projects beside the platform's. That meant three of everything — three pulls to
reach one consistent state, three .env files each holding a copy of the same
shared secret, three compose projects to start in the right order, and a window
after any one of them updated in which the platform ran two halves of a contract
that had changed.

Asked for directly: "i want all the apps integrated into slop and we'll just
send updates to the stand alone repos when they are complete." So: this tree is
the source of truth, the standalone repositories are publish targets, and
tools/publish-app is how a finished change gets to one.

These tests hold the parts of that which are easy to half-do.

Run: pytest tests/
"""
import json
import os
import subprocess
import sys

import pytest
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO, "apps", "VENDORED.json")


@pytest.fixture(scope="module")
def manifest():
    with open(MANIFEST, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def compose():
    with open(os.path.join(REPO, "docker-compose.yml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_every_manifest_app_is_actually_here(manifest):
    for key, app in manifest["apps"].items():
        d = os.path.join(REPO, "apps", key)
        assert os.path.isdir(d), f"{app['label']} is in the manifest but apps/{key} is not here"
        # A directory with a README and nothing else would pass the check above.
        assert len(os.listdir(d)) > 3, f"apps/{key} looks empty: {os.listdir(d)}"


def test_a_vendored_app_brought_no_git_directory_with_it(manifest):
    """A nested .git makes the outer repository treat the directory as a
    submodule-shaped hole: git adds a gitlink, nothing inside is tracked, and a
    fresh clone of the platform arrives with three empty directories."""
    for key in manifest["apps"]:
        assert not os.path.exists(os.path.join(REPO, "apps", key, ".git")), \
            f"apps/{key}/.git came along; git will track a gitlink and no files"


def test_the_vendored_files_are_tracked(manifest):
    """The other half of the check above, from git's side: the files have to be
    IN the repository, not merely on this disk."""
    out = subprocess.run(["git", "ls-files", "apps/"], cwd=REPO,
                         capture_output=True, text=True).stdout.split()
    for key in manifest["apps"]:
        n = len([f for f in out if f.startswith(f"apps/{key}/")])
        assert n > 10, f"git tracks only {n} files under apps/{key}"


@pytest.mark.parametrize("key,prefix", [
    ("controller", ""),
    ("slep", "/slep/"),
    ("connect", "/connect/"),
])
def test_each_app_bakes_its_own_url_prefix(compose, key, prefix):
    """The single most expensive bug this layout removes, so it gets a test.

    SYSIBLE_BASE_PATH is a BUILD argument — baked into the console's JavaScript
    bundle — and all three apps read it from that ONE name out of whichever .env
    sat beside their compose file. One name, three different correct answers. A
    build that picked up the wrong one, or none, produced a console that served
    its page and then asked for its assets at a path the gateway does not route:
    a blank screen, with every container reporting healthy. Reported twice, and
    the last fix wrote the value into three separate .env files — a fix that
    survives exactly until something rebuilds without them.

    Here they are literals, beside the services they belong to. Nothing reads a
    variable, so nothing can read the wrong one.
    """
    args = compose["services"][key]["build"]["args"]
    got = args.get("SYSIBLE_BASE_PATH")
    assert got == prefix, (
        f"{key} builds with SYSIBLE_BASE_PATH={got!r}; it must be {prefix!r}. "
        f"Wrong here means a blank console behind the gateway and a healthy "
        f"container.")
    assert "${" not in str(got), (
        f"{key}'s prefix is a variable again ({got!r}). It is a build arg with "
        f"a different right answer per app; a shared variable is how this broke "
        f"twice.")


def test_the_controller_still_publishes_the_port_the_fleet_dials(compose):
    """Agents across the network dial the Controller's API on 9000. The consoles
    are deliberately NOT published any more — the gateway reaches them on this
    project's network — and 9000 getting swept up in that would strand every
    managed host."""
    published = " ".join(str(p) for p in compose["services"]["controller"]["ports"])
    assert "9000:9000" in published, (
        f"the Controller's agent API is not published: {published}. Managed "
        f"hosts dial it; without it they go quiet and the console looks fine.")
    assert "8800" not in published, (
        "the Controller's console is published again. Behind the gateway that "
        "is a second door at which the platform's security headers and login "
        "throttle do not apply.")


@pytest.mark.parametrize("key", ["slep", "connect"])
def test_the_app_consoles_are_not_published(compose, key):
    svc = compose["services"][key]
    assert "ports" not in svc, (
        f"{key} publishes {svc.get('ports')}. The gateway reaches it by service "
        f"name; a published console is a way around the gateway's headers and "
        f"the platform's login throttle.")


def test_connect_keeps_what_the_host_shell_needs(compose):
    """A superuser's "local shell" is the HOST's shell: terminals.py enters PID
    1's namespaces with nsenter. Drop any one of these and it silently becomes a
    shell inside a minimal container with no sudo and an empty /opt — which is
    exactly what was reported."""
    svc = compose["services"]["connect"]
    assert svc.get("pid") == "host", "connect lost pid: host; nsenter has no host PID 1 to enter"
    caps = set(svc.get("cap_add") or [])
    assert {"SYS_ADMIN", "SYS_PTRACE"} <= caps, f"connect lost the setns capabilities: {caps}"
    assert "apparmor:unconfined" in (svc.get("security_opt") or []), \
        "connect lost the apparmor exception; the default profile blocks setns"


def test_the_integrated_overlay_moves_every_app_upstream(compose):
    """The profile and the gateway's upstreams are two halves of one switch, and
    forgetting the second one does not FAIL: the gateway goes on dialling
    host.docker.internal:8800, finds nothing now that the app is a service on
    this network, and every app reports down on a platform that is running. They
    are in the same file so the -f that brings up the services is the -f that
    moves the gateway."""
    with open(os.path.join(REPO, "docker-compose.integrated.yml"), encoding="utf-8") as fh:
        overlay = yaml.safe_load(fh)
    env = overlay["services"]["gateway"]["environment"]
    for key, var, port in (("controller", "SLOP_CONTROLLER_UPSTREAM", 8800),
                           ("slep", "SLOP_SLEP_UPSTREAM", 8810),
                           ("connect", "SLOP_CONNECT_UPSTREAM", 8700)):
        assert env.get(var) == f"{key}:{port}", (
            f"{var} is {env.get(var)!r}; with the integrated profile up it must "
            f"be {key}:{port} or the gateway dials a port that no longer exists")
        assert key in compose["services"], f"{var} points at a service that is not here"


def test_the_apps_only_come_up_when_asked(compose):
    """A plain `docker compose up -d` must be byte-for-byte what it was. A host
    already running the three apps the old way has their container names and
    their ports taken, and two stacks competing for `sysible-controller` is not
    a thing to discover during an update."""
    for key in ("controller", "slep", "connect"):
        assert "integrated" in (compose["services"][key].get("profiles") or []), \
            f"{key} is no longer behind the integrated profile; a plain `up` would start it"
    for key in ("idp", "gateway", "flashback", "baseline", "visualizer", "updater"):
        assert not compose["services"][key].get("profiles"), \
            f"{key} grew a profile; a plain `up` would stop starting it"


# ---- the updater's view of a vendored product ----------------------------
def _apps_module(mp, slop_dir, src_dir, integrated=True):
    """backend.apps, imported with the host layout these arguments describe.

    Everything it changes goes through monkeypatch, so pytest puts it back.
    Setting os.environ directly here cost two unrelated failures in the full run
    and none when this file ran alone: backend.apps reads the environment at
    CALL time, so a SYSIBLE_SRC_DIR left pointing at a tmp_path that no longer
    exists made a later test's products read as "not installed". The env also
    reaches the SUBPROCESSES the sysiblectl tests drive, where a leaked
    SYSIBLE_INTEGRATED beats the .env file they exist to check -- so the leak
    would have turned those green for the wrong reason too.

    `integrated` is likewise explicit rather than ambient, for the same reason.
    """
    import importlib
    mp.syspath_prepend(os.path.join(REPO, "updater"))
    mp.setenv("SYSIBLE_SLOP_DIR", str(slop_dir))
    mp.setenv("SYSIBLE_SRC_DIR", str(src_dir))
    mp.setenv("SYSIBLE_INTEGRATED", "1" if integrated else "0")
    for name in list(sys.modules):
        if name.startswith("backend"):
            mp.delitem(sys.modules, name, raising=False)
    mod = importlib.import_module("backend.apps")
    return importlib.reload(mod)


def test_an_integrated_host_keeps_every_products_row(monkeypatch, tmp_path):
    """A vendored product is not separately updatable and does NOT vanish.

    Making the row disappear would be the easy implementation and the wrong
    one: an operator who knows this platform has a Controller would be left to
    work out that the row went away because the source moved to another
    repository. It keeps its name, its description and its services, and says
    what updates it.
    """
    apps = _apps_module(monkeypatch, REPO, tmp_path / "empty-src")
    assert apps.keys() == ["controller", "slep", "connect", "slop"], \
        f"a product lost its row: {apps.keys()}"
    assert all(apps.is_vendored(k) for k in ("controller", "slep", "connect"))
    assert not apps.is_vendored("slop")
    # Its services are SLOP's to run, so compose has to be asked in SLOP's
    # directory — anywhere else reports no containers on a host where it runs.
    assert apps.compose_dir_for("controller") == apps.compose_dir_for("slop")
    # ...and the cards stay on their own product rather than being swept into
    # the platform's, which would leave three rows with nothing in them.
    assert apps.module_keys("controller") == ["controller"]
    assert "controller" not in apps.module_keys("slop")


def test_a_half_migrated_host_leaves_the_old_checkout_in_charge(monkeypatch, tmp_path):
    """The case that decides whether a migration is survivable.

    A host part-way through has apps/controller in its SLOP checkout AND the
    old /opt/sysible-src/sysible-controller still there and still running. The
    thing that is actually serving requests is the old one, so it keeps its own
    row and its own update button until someone removes it. A product must not
    lose its update because a directory appeared in another repository.
    """
    src = tmp_path / "src"
    (src / "sysible-controller" / ".git").mkdir(parents=True)
    apps = _apps_module(monkeypatch, REPO, src)
    assert not apps.is_vendored("controller"), \
        "the standalone checkout is running and was treated as vendored anyway"
    assert apps.is_vendored("slep") and apps.is_vendored("connect")
    assert apps.compose_dir_for("controller") != apps.compose_dir_for("slop")


# ---- the switch itself ----------------------------------------------------
def _sh(script: str, cwd=None, env=None) -> subprocess.CompletedProcess:
    """Run a shell snippet with a CLEAN marker.

    SYSIBLE_INTEGRATED is removed unless the caller sets it. These tests are
    about what sysiblectl reads out of a host's .env, and an env var inherited
    from this process beats that file -- so without this, a marker left behind
    by another test decides the result.
    """
    e = dict(os.environ)
    e.pop("SYSIBLE_INTEGRATED", None)
    e.update(env or {})
    return subprocess.run(["bash", "-c", script], cwd=cwd, text=True,
                          capture_output=True, env=e)


def test_install_sh_and_sysiblectl_parse():
    """Both are shipped as-is to a host; a syntax error is found there."""
    for path in (os.path.join(REPO, "install.sh"),
                 os.path.join(REPO, "apps", "controller", "deploy", "sysiblectl")):
        r = _sh(f"bash -n {path!r}")
        assert r.returncode == 0, f"{path} does not parse:\n{r.stderr}"
    # install.sh is /bin/sh, so it has to parse as that too.
    r = _sh(f"sh -n {os.path.join(REPO, 'install.sh')!r}")
    assert r.returncode == 0, f"install.sh is not POSIX sh:\n{r.stderr}"


def test_the_integrated_flag_is_refused_without_the_apps(tmp_path):
    """--integrated on a checkout that has no apps/ would clone nothing and
    build nothing, and the first thing the operator would see is a platform
    with three missing services."""
    fake = tmp_path / "slop"
    fake.mkdir()
    (fake / "install.sh").write_text(
        open(os.path.join(REPO, "install.sh"), encoding="utf-8").read())
    r = _sh(f"sh {str(fake / 'install.sh')!r} --integrated 2>&1 | head -3")
    assert "apps/" in r.stdout, (
        f"--integrated did not complain about the missing apps/: {r.stdout!r}")


@pytest.mark.parametrize("env_body,want", [
    ("", False),
    ("SYSIBLE_INTEGRATED=0\n", False),
    ("SYSIBLE_INTEGRATED=1\n", True),
    ("SYSIBLE_INTEGRATED=true\n", True),
    ("SYSIBLE_INTEGRATED=YES\n", True),
    # _upsert_kv appends when the key is absent, so a file can carry the key
    # twice; the LAST one is what compose itself would use.
    ("SYSIBLE_INTEGRATED=0\nSYSIBLE_INTEGRATED=1\n", True),
])
def test_sysiblectl_only_adds_the_integrated_args_when_the_host_opted_in(
        tmp_path, env_body, want):
    """The half of the switch that is easy to get wrong in the other direction.

    apps/ and docker-compose.integrated.yml both arrive with a GIT PULL. If
    sysiblectl keyed off their presence, the first `sysiblectl slop update`
    after a pull would quietly start three more containers on a host whose three
    apps are still three separate stacks -- and they would collide on container
    name and port with the ones actually serving requests.
    """
    helper = subprocess.run(
        ["sed", "-n", "/^_integrated_args() {/,/^}/p",
         os.path.join(REPO, "apps", "controller", "deploy", "sysiblectl")],
        capture_output=True, text=True).stdout
    assert "_integrated_args" in helper, "the helper is gone from sysiblectl"
    d = tmp_path / "slop"
    d.mkdir()
    (d / "docker-compose.yml").write_text("services: {}\n")
    (d / "docker-compose.integrated.yml").write_text("services: {}\n")
    if env_body:
        (d / ".env").write_text(env_body)
    (tmp_path / "h.sh").write_text(helper)
    r = _sh(f'set -u; . {str(tmp_path / "h.sh")!r}; '
            f'_integrated_args {str(d / "docker-compose.yml")!r}')
    got = r.stdout.split()
    assert r.returncode == 0, r.stderr
    if want:
        assert "--profile" in got and "integrated" in got, (
            f"opted in ({env_body!r}) and got no profile: {got}")
        assert any(x.endswith("docker-compose.integrated.yml") for x in got), (
            f"the profile was added without the overlay that moves the gateway "
            f"to it -- every app would report down: {got}")
    else:
        assert got == [], (
            f"not opted in ({env_body!r}) and sysiblectl would start the "
            f"integrated services anyway: {got}")


def test_sysiblectl_adds_nothing_where_there_is_no_overlay(tmp_path):
    """An app's own compose file is not SLOP's. Adding --profile integrated to
    `sysiblectl connect restart` would be asking a one-service project for a
    profile it has never heard of."""
    helper = subprocess.run(
        ["sed", "-n", "/^_integrated_args() {/,/^}/p",
         os.path.join(REPO, "apps", "controller", "deploy", "sysiblectl")],
        capture_output=True, text=True).stdout
    d = tmp_path / "connect" / "deploy"
    d.mkdir(parents=True)
    (d / "docker-compose.yml").write_text("services: {}\n")
    (d / ".env").write_text("SYSIBLE_INTEGRATED=1\n")   # even then
    (tmp_path / "h.sh").write_text(helper)
    r = _sh(f'set -u; . {str(tmp_path / "h.sh")!r}; '
            f'_integrated_args {str(d / "docker-compose.yml")!r}')
    assert r.stdout.split() == [], (
        f"a product with no integrated overlay got profile args: {r.stdout!r}")


def test_the_updater_only_sees_a_vendored_app_when_the_host_opted_in(monkeypatch, tmp_path):
    """Same rule on the reporting side. apps/ arriving with a pull must not make
    Administration start saying three products are "built with the platform"
    while three standalone stacks go on serving every request."""
    apps = _apps_module(monkeypatch, REPO, tmp_path / "empty", integrated=False)
    assert not any(apps.is_vendored(k) for k in apps.ALLOWLIST), \
        "a pull alone flipped the updater's view of the host"
    apps = _apps_module(monkeypatch, REPO, tmp_path / "empty", integrated=True)
    assert all(apps.is_vendored(k) for k in ("controller", "slep", "connect")), \
        "the host opted in and the updater still does not see the apps"
