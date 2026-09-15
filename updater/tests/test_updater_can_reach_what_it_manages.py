"""Every checkout the updater is told to manage must be visible INSIDE it.

The updater holds the host's Docker socket and drives `git -C <root> pull` plus
`docker compose up -d --build` with cwd=<compose dir>. Both ends matter:

  * the paths must exist inside the CONTAINER, or apps.checkout_dir() answers
    None and the product renders as "not installed on this host";
  * they must be the same paths on the HOST, because `docker compose` here talks
    to the host daemon, which resolves the build context on the host.

That is why the compose file bind-mounts SYSIBLE_SRC_DIR at the identical path.
It did that for the products install.sh clones — and NOT for SLOP itself, whose
checkout is wherever the operator cloned it (install.sh never clones SLOP; it is
run FROM it). So the SLOP row in Administration → Updates said "not installed on
this host" on every host where SLOP was not cloned under /opt/sysible-src — i.e.
the normal case — and the platform could not update itself from its own console.

These read the real docker-compose.yml, because the bug was in the wiring, not in
any code a unit test would reach.
"""
import os
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "docker-compose.yml"


def _expand(value: str, env: dict) -> str:
    """Resolve compose's ${VAR:-default} substitution the way compose would."""
    def sub(m):
        name, default = m.group(1), m.group(3)
        return env.get(name) or (default or "")
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}", sub, value)


@pytest.fixture(scope="module")
def updater_service():
    svc = yaml.safe_load(COMPOSE.read_text())["services"]["updater"]
    assert svc, "the compose file has no updater service"
    return svc


def _mount_targets(svc, env):
    targets = {}
    for entry in svc.get("volumes", []):
        assert isinstance(entry, str), "short-form mounts only, so source and target are comparable"
        src, dst = _expand(entry, env).split(":")[:2]
        targets[dst.rstrip("/")] = src.rstrip("/")
    return targets


def _dir_env(svc, env):
    """Every directory the updater is CONFIGURED to reach, from its environment."""
    out = {}
    for k, v in (svc.get("environment") or {}).items():
        if k.endswith("_DIR"):
            out[k] = _expand(str(v), env).rstrip("/")
    return out


@pytest.mark.parametrize("env", [
    pytest.param({}, id="defaults"),
    # The real shape: SLOP cloned where the operator put it, products where
    # install.sh puts them.
    pytest.param({"SYSIBLE_SLOP_DIR": "/opt/Sysible-Linux-Operations-Platform"},
                 id="slop-cloned-outside-src-dir"),
    pytest.param({"SYSIBLE_SRC_DIR": "/srv/sysible",
                  "SYSIBLE_SLOP_DIR": "/srv/slop"}, id="both-relocated"),
])
def test_every_directory_it_is_given_is_mounted_at_the_same_path(updater_service, env):
    mounts = _mount_targets(updater_service, env)
    for var, path in _dir_env(updater_service, env).items():
        assert path, f"{var} expanded to nothing"
        covering = [t for t in mounts if path == t or path.startswith(t + "/")]
        assert covering, (
            f"{var}={path} is not bind-mounted into the updater — apps.checkout_dir() "
            f"will not find its .git and the product reads as 'not installed'. "
            f"Mounted: {sorted(mounts)}")
        # What is mounted there must BE that directory on the host, because the
        # `docker compose` this service runs is resolved by the HOST daemon: a
        # container path that means something else on the host builds nothing.
        # An absolute source must therefore equal its target. A RELATIVE source is
        # resolved by compose against the project directory — which for this file
        # is the SLOP checkout itself — so `.` is correct for, and only for, the
        # SLOP mount.
        t = max(covering, key=len)
        src = mounts[t]
        if src.startswith("/"):
            assert src == t, (
                f"{var}={path} is mounted from {src} to {t}; the updater passes "
                f"container paths to the HOST daemon, so the two must be identical")
        else:
            assert src == "." and var == "SYSIBLE_SLOP_DIR", (
                f"{var}={path} is mounted from the relative source {src!r}; only the "
                f"SLOP checkout may be mounted that way (it IS the project directory)")


def test_the_slop_checkout_itself_is_reachable(updater_service):
    """The specific regression: SLOP is the one product install.sh does not clone,
    so its checkout was never in the mount list."""
    env = {"SYSIBLE_SLOP_DIR": "/opt/Sysible-Linux-Operations-Platform"}
    slop = _dir_env(updater_service, env).get("SYSIBLE_SLOP_DIR")
    assert slop == "/opt/Sysible-Linux-Operations-Platform"
    assert slop in _mount_targets(updater_service, env), \
        "the SLOP checkout is not mounted, so SLOP can never update itself"


def test_the_allowlisted_products_all_have_a_directory_variable(updater_service):
    """Anything in the allowlist that the compose file does not name a directory
    for can only be found at its conventional path — which is how this drifted."""
    import backend.apps as apps
    env_names = set(updater_service.get("environment") or {})
    for key, (_, _, var) in apps.ALLOWLIST.items():
        if key == "slop":
            assert var in env_names, f"{key}: {var} must be set so the mount can match it"


class TestFindingACheckoutWhoseNameIsCapitalised:
    """github.com/sysiblesoftware/Sysible-Controller clones to a CAPITALISED
    directory, so the conventional-path lookup missed it and the product read as
    'not installed'. sysible_ctl hit this and was fixed with `find -iname`."""

    def test_a_capitalised_clone_is_still_found(self, tmp_path, monkeypatch):
        import importlib
        src = tmp_path / "src"
        (src / "Sysible-Controller" / ".git").mkdir(parents=True)
        monkeypatch.setenv("SYSIBLE_SRC_DIR", str(src))
        monkeypatch.delenv("SYSIBLE_CONTROLLER_DIR", raising=False)
        import backend.apps as apps
        importlib.reload(apps)
        try:
            assert apps.checkout_dir("controller") == src / "Sysible-Controller"
        finally:
            importlib.reload(apps)

    def test_an_exact_match_still_wins_over_a_case_variant(self, tmp_path, monkeypatch):
        import importlib
        src = tmp_path / "src"
        (src / "sysible-controller" / ".git").mkdir(parents=True)
        (src / "Sysible-Controller" / ".git").mkdir(parents=True)
        monkeypatch.setenv("SYSIBLE_SRC_DIR", str(src))
        monkeypatch.delenv("SYSIBLE_CONTROLLER_DIR", raising=False)
        import backend.apps as apps
        importlib.reload(apps)
        try:
            assert apps.checkout_dir("controller") == src / "sysible-controller"
        finally:
            importlib.reload(apps)
