"""The rebrand renamed a docker service. It must not rename the volume under it.

Compose renames are not migrations. Rename a SERVICE and the old container is
left behind as an orphan — still holding the agent port on the docker bridge, so
the new container cannot bind it and dies on startup. Rename a VOLUME and compose
cheerfully creates a fresh empty one, the console comes up pristine on a fleet
with years of captures, and the old volume sits there unreferenced until someone
runs `docker volume prune`.

Only one of those two is recoverable. These pin both, and they read the shipping
compose file rather than a copy of it.
"""
import os
import re
import subprocess

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
COMPOSE = os.path.join(ROOT, "docker-compose.yml")
INSTALL = os.path.join(ROOT, "install.sh")


@pytest.fixture(scope="module")
def compose():
    with open(COMPOSE, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_the_version_store_volume_keeps_its_pre_rebrand_name(compose):
    """`flashback-data` holds every version of every config file on every host
    and is the only thing in the stack that cannot be rebuilt from anything
    else. A renamed volume is an empty volume."""
    assert "flashback-data" in (compose.get("volumes") or {}), (
        "flashback-data is gone from the compose file. Every existing install's "
        "config history is now in a volume nothing mounts.")
    svc = compose["services"]["baseline"]
    mounts = [m for m in svc["volumes"] if ":/data" in m]
    assert mounts == ["flashback-data:/data"], \
        f"Baseline's /data is no longer the pre-rebrand volume: {mounts}"


def test_the_service_is_renamed_so_docker_ps_says_baseline(compose):
    """The half of the rebrand an operator sees from a terminal."""
    assert "baseline" in compose["services"], "no `baseline` service"
    assert "flashback" not in compose["services"], \
        "both services present — two containers, one volume, one host port"
    svc = compose["services"]["baseline"]
    assert svc["build"] == "./baseline"
    assert svc["container_name"] == "sysible-baseline"
    assert svc["image"] == "sysible-baseline"


def test_the_service_rename_warns_about_the_orphan_it_leaves(compose):
    """The failure this causes is a container that will not start, on a stack
    that was working ten seconds ago. It costs one comment to name it."""
    src = open(COMPOSE, encoding="utf-8").read()
    block = src[src.index("  baseline:") - 1200:src.index("  baseline:")]
    assert "orphan" in block.lower() and "remove-orphans" in block, (
        "nothing near the renamed service explains that the old container is "
        "left behind holding the agent port")


@pytest.mark.parametrize("new,old", [
    ("SYSIBLE_BASELINE_AGENT_TOKEN", "SYSIBLE_FLASHBACK_AGENT_TOKEN"),
    ("SYSIBLE_BASELINE_KEEP", "SYSIBLE_FLASHBACK_KEEP"),
    ("SLOP_BASELINE_UPSTREAM", "SLOP_FLASHBACK_UPSTREAM"),
])
def test_compose_falls_back_to_the_pre_rebrand_variable(new, old):
    """An upgrade does not edit .env before compose reads it. Without the nested
    default, an operator's pinned value is silently replaced by the stock one —
    and for the agent token, "stock" is empty, which fails closed."""
    src = open(COMPOSE, encoding="utf-8").read()
    assert re.search(re.escape(new) + r":\s*\$\{" + re.escape(new)
                     + r":-\$\{" + re.escape(old) + r":-", src), \
        f"{new} does not fall back to {old}"


@pytest.mark.skipif(not os.environ.get("PATH") or
                    subprocess.run(["sh", "-c", "command -v docker"],
                                   capture_output=True).returncode != 0,
                    reason="docker not available")
@pytest.mark.parametrize("env,expect", [
    ({}, {"SLOP_BASELINE_UPSTREAM": "baseline:8080", "SYSIBLE_BASELINE_KEEP": "50"}),
    ({"SYSIBLE_FLASHBACK_KEEP": "99", "SLOP_FLASHBACK_UPSTREAM": "old:1"},
     {"SLOP_BASELINE_UPSTREAM": "old:1", "SYSIBLE_BASELINE_KEEP": "99"}),
    ({"SYSIBLE_FLASHBACK_KEEP": "99", "SYSIBLE_BASELINE_KEEP": "11"},
     {"SYSIBLE_BASELINE_KEEP": "11"}),
])
def test_docker_compose_itself_resolves_the_fallback(env, expect):
    """Not a regex over the file — the real `docker compose config`, because the
    nested-default syntax either works in the operator's compose or it does not,
    and reading it cannot tell you which."""
    e = dict(os.environ, SYSIBLE_SSO_SHARED_SECRET="x", **env)
    for k in ("SYSIBLE_FLASHBACK_KEEP", "SYSIBLE_BASELINE_KEEP",
              "SLOP_FLASHBACK_UPSTREAM", "SLOP_BASELINE_UPSTREAM"):
        if k not in env:
            e.pop(k, None)
    out = subprocess.run(["docker", "compose", "config"], cwd=ROOT,
                         capture_output=True, text=True, env=e)
    if out.returncode != 0:                                  # pragma: no cover
        pytest.skip(f"docker compose config unavailable: {out.stderr[:200]}")
    cfg = yaml.safe_load(out.stdout)
    resolved = cfg["services"]["baseline"]["environment"]
    resolved.update(cfg["services"]["visualizer"]["environment"])
    for k, v in expect.items():
        assert str(resolved.get(k)) == v, f"{k} resolved to {resolved.get(k)!r}, wanted {v!r}"


def test_the_installer_finds_an_existing_token_under_either_name():
    """If an upgrade cannot find the token already in .env it mints a FRESH one.
    The token is what every host's config backup authenticates with, so the
    console goes quiet across the whole fleet with both halves looking correct."""
    src = open(INSTALL, encoding="utf-8").read()
    reader = src[src.index("_fb_token_from_env() {"):src.index("FB_TOKEN=")]
    assert "SYSIBLE_BASELINE_AGENT_TOKEN" in reader
    assert "SYSIBLE_FLASHBACK_AGENT_TOKEN" in reader, \
        "an upgrade will not see the token an existing install already has"


# ---- the .env migration, run for real ------------------------------------
#
# This is the one piece of the rebrand that EDITS A FILE THE OPERATOR OWNS, and
# their .env holds the SSO secret, the updater secret and the agent token. The
# same file's writer has already had one bug of exactly this shape: _upsert_kv
# used `sed "s|^$KEY=.*|$KEY=$VALUE|"`, which read the VALUE as a sed
# replacement, so `&`, `|` and `\` silently corrupted paths — on the re-run
# branch only, so a first install looked perfect. See test_install_env_file.py.
#
# So these extract the shipping function and run it, rather than reading it.


def _fn(name):
    """One shell function, lifted out of install.sh by brace matching."""
    src = open(INSTALL, encoding="utf-8").read()
    start = src.index(name + "() {")
    depth, i = 0, start
    while True:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return src[start:i + 1]


@pytest.fixture(scope="module")
def migrate_sh():
    return ("say() { :; }\n" + _fn("_upsert_kv") + "\n"
            + 'ENV_FILE="$1"\n' + _fn("_upsert_env") + "\n"
            + _fn("_migrate_env_key") + "\n")


def _run(migrate_sh, env_path, body, tmp_path, arg=None):
    script = migrate_sh + body
    cmd = ["sh", "-s", str(env_path)] + ([arg] if arg is not None else [])
    return subprocess.run(cmd, input=script, capture_output=True, text=True,
                          cwd=tmp_path, timeout=60)


def test_a_pre_rebrand_env_file_is_renamed_keeping_every_value(migrate_sh, tmp_path):
    """The upgrade path: an .env written before the rebrand."""
    env = tmp_path / ".env"
    env.write_text("SYSIBLE_SSO_SHARED_SECRET=sso-keep-me\n"
                   "SYSIBLE_FLASHBACK_AGENT_TOKEN=" + "a" * 64 + "\n"
                   "SYSIBLE_FLASHBACK_KEEP=99\n"
                   "SLOP_FLASHBACK_UPSTREAM=legacy:1234\n"
                   "SYSIBLE_UPDATER_SECRET=upd-keep-me\n")
    r = _run(migrate_sh, env,
             '_migrate_env_key SYSIBLE_FLASHBACK_AGENT_TOKEN SYSIBLE_BASELINE_AGENT_TOKEN\n'
             '_migrate_env_key SYSIBLE_FLASHBACK_KEEP SYSIBLE_BASELINE_KEEP\n'
             '_migrate_env_key SLOP_FLASHBACK_UPSTREAM SLOP_BASELINE_UPSTREAM\n', tmp_path)
    assert r.returncode == 0, r.stderr
    got = dict(l.split("=", 1) for l in env.read_text().splitlines() if "=" in l)
    assert got["SYSIBLE_BASELINE_AGENT_TOKEN"] == "a" * 64
    assert got["SYSIBLE_BASELINE_KEEP"] == "99"
    assert got["SLOP_BASELINE_UPSTREAM"] == "legacy:1234"
    assert "SYSIBLE_FLASHBACK_AGENT_TOKEN" not in got, "the old key was left behind"
    # The secrets that have nothing to do with the rebrand must be untouched.
    assert got["SYSIBLE_SSO_SHARED_SECRET"] == "sso-keep-me"
    assert got["SYSIBLE_UPDATER_SECRET"] == "upd-keep-me"


def test_running_it_twice_changes_nothing(migrate_sh, tmp_path):
    """install.sh is re-run on every upgrade."""
    env = tmp_path / ".env"
    env.write_text("SYSIBLE_FLASHBACK_KEEP=99\n")
    body = "_migrate_env_key SYSIBLE_FLASHBACK_KEEP SYSIBLE_BASELINE_KEEP\n"
    assert _run(migrate_sh, env, body, tmp_path).returncode == 0
    once = env.read_text()
    assert _run(migrate_sh, env, body, tmp_path).returncode == 0
    assert env.read_text() == once, "not idempotent"


def test_a_value_the_operator_set_under_the_new_name_is_never_clobbered(migrate_sh, tmp_path):
    """Both spellings in one file is exactly what an upgrade leaves. The
    operator's current choice has to survive; overwriting it with a stale value
    from before the rename would be the rebrand undoing their work."""
    env = tmp_path / ".env"
    env.write_text("SYSIBLE_FLASHBACK_AGENT_TOKEN=OLD-must-not-win\n"
                   "SYSIBLE_BASELINE_AGENT_TOKEN=NEW-operator-set-this\n")
    r = _run(migrate_sh, env,
             '_migrate_env_key SYSIBLE_FLASHBACK_AGENT_TOKEN SYSIBLE_BASELINE_AGENT_TOKEN\n',
             tmp_path)
    assert r.returncode == 0, r.stderr
    got = dict(l.split("=", 1) for l in env.read_text().splitlines() if "=" in l)
    assert got["SYSIBLE_BASELINE_AGENT_TOKEN"] == "NEW-operator-set-this"


@pytest.mark.parametrize("value,why", [
    ("https://host:8770/?a=1&b=2", "& is sed's 'whole match'"),
    ("/opt/a|b", "| closes a sed s||| expression"),
    ("/opt/back\\slash", "a backslash escapes in sed and awk"),
    ("has spaces and $dollars", "word splitting and expansion"),
    ("has 'single' and \"double\" quotes", "quoting"),
    ("/opt/#hash", "a comment character"),
])
def test_an_awkward_value_survives_the_rename_byte_for_byte(migrate_sh, tmp_path, value, why):
    """Same hazard class that corrupted SYSIBLE_SLOP_DIR once already. The
    values passing through here include the agent token and a URL with a query
    string."""
    env = tmp_path / ".env"
    script = (migrate_sh +
              '_upsert_kv "$ENV_FILE" SYSIBLE_FLASHBACK_URL "$2"\n'
              '_migrate_env_key SYSIBLE_FLASHBACK_URL SYSIBLE_BASELINE_URL\n')
    r = subprocess.run(["sh", "-s", str(env), value], input=script,
                       capture_output=True, text=True, cwd=tmp_path, timeout=60)
    assert r.returncode == 0, r.stderr
    lines = env.read_text().splitlines()
    got = [l[len("SYSIBLE_BASELINE_URL="):] for l in lines
           if l.startswith("SYSIBLE_BASELINE_URL=")]
    assert got == [value], f"{why}: wrote {got!r}, expected [{value!r}]"


def test_a_missing_env_file_is_not_an_error(migrate_sh, tmp_path):
    """A first install has no .env yet, and the migration runs before it exists."""
    env = tmp_path / "absent.env"
    r = _run(migrate_sh, env,
             '_migrate_env_key SYSIBLE_FLASHBACK_KEEP SYSIBLE_BASELINE_KEEP\n', tmp_path)
    assert r.returncode == 0, r.stderr
    assert not env.exists(), "the migration created an .env out of nothing"


def test_the_installer_actually_calls_the_migration_for_every_setting():
    """The function existing is not the same as it running."""
    src = open(INSTALL, encoding="utf-8").read()
    assert "_migrate_env_key" in src
    called = src[src.index("_migrate_env_key \"SYSIBLE_FLASHBACK_$_k\""):]
    for suffix in ("AGENT_TOKEN", "AGENT_BIND", "AGENT_PORT", "KEEP", "URL"):
        assert suffix in src, f"{suffix} is never migrated"
    assert "_migrate_env_key SLOP_FLASHBACK_UPSTREAM SLOP_BASELINE_UPSTREAM" in src
