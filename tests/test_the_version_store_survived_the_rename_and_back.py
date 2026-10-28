"""Flashback was renamed Baseline, then renamed back. The store never moved.

That is the point of this file. Across a rename and its reversal — two passes of
mechanical find-and-replace over three repositories — the `flashback-data`
volume and the `flashback.db` inside it never changed name once, because they
were marked as contracts rather than brand the first time round. Everything else
churned twice. These tests are why that stayed true.

The service DID churn, and that is the live hazard: a stack brought up during
the two days the rename was live is running a `sysible-baseline` container built
from this service. It is an orphan now, it still holds the agent port on the
docker bridge, and `sysible-flashback` cannot start while it exists.

`baseline` is also no longer a free name. It belongs to Sysible Baseline, the
Salt-driven config manager, so the env-var migration that put Flashback's
settings under SYSIBLE_BASELINE_* runs in reverse and exactly ONCE — after that,
a SYSIBLE_BASELINE_AGENT_TOKEN in the file is the config manager's, and moving
it would break that service instead. The one-shot marker is the only thing
telling those two cases apart, because nothing about the key itself can.
"""
import os
import subprocess
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COMPOSE = ROOT / "docker-compose.yml"
INSTALL = ROOT / "install.sh"


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load(COMPOSE.read_text())


# ---- what must never move -------------------------------------------------

def test_the_version_store_volume_kept_its_name(compose):
    """`flashback-data` holds every version of every config file on every host
    and is the only thing in the stack that cannot be rebuilt from anything
    else. A renamed volume is an empty volume."""
    assert "flashback-data" in (compose.get("volumes") or {}), (
        "flashback-data is gone from the compose file. Every existing install's "
        "config history is now in a volume nothing mounts.")
    svc = compose["services"]["flashback"]
    mounts = [m for m in svc["volumes"] if ":/data" in m]
    assert mounts == ["flashback-data:/data"], \
        f"Flashback's /data is no longer the original volume: {mounts}"


def test_the_database_file_kept_its_name():
    src = (ROOT / "flashback" / "backend" / "store.py").read_text()
    assert 'os.path.join(_DATA_DIR, "flashback.db")' in src, \
        "the version store file was renamed; existing history is in a file the " \
        "service no longer opens"


# ---- the two products are separate ---------------------------------------

def _build_dir(svc):
    """Compose takes `build:` either as a bare path or as a mapping with a
    context — this repo moved to the mapping to pass SYSIBLE_REV in. Which of
    the two it is says nothing about which directory is built, so read the
    directory and let the shape be the shape."""
    build = svc["build"]
    return build["context"] if isinstance(build, dict) else build


def test_the_backup_service_is_flashback(compose):
    svc = compose["services"]["flashback"]
    assert _build_dir(svc) == "./flashback"
    assert svc["container_name"] == "sysible-flashback"
    assert svc["image"] == "sysible-flashback"


def test_the_config_manager_is_a_different_service(compose):
    """If Baseline is wired in, it must be its own service with its own
    container, image and volumes. One service answering for both products is
    the failure this whole split exists to avoid."""
    bl = compose["services"].get("baseline")
    if bl is None:
        pytest.skip("the Baseline config manager is not in the compose file yet")
    fb = compose["services"]["flashback"]
    assert bl.get("container_name") != fb.get("container_name")
    assert bl.get("image") != fb.get("image")
    assert _build_dir(bl) != _build_dir(fb)
    fb_vols = {v.split(":")[0] for v in (fb.get("volumes") or [])}
    bl_vols = {v.split(":")[0] for v in (bl.get("volumes") or [])}
    assert "flashback-data" not in bl_vols, \
        "the config manager mounts the backup tool's version store"
    assert not (fb_vols & bl_vols), f"the two products share volumes: {fb_vols & bl_vols}"


@pytest.mark.parametrize("suffix", ["AGENT_TOKEN", "KEEP", "DATA", "TRUST_GATEWAY_AUTH"])
def test_flashback_does_not_read_the_config_managers_prefix(suffix):
    """SYSIBLE_BASELINE_* belongs to Sysible Baseline now. A courtesy fallback
    would mean the backup service silently using the config manager's token,
    data dir or retention — a misconfiguration with no symptom until something
    needs restoring, which is the worst possible moment to find it."""
    # The flashback service block ONLY. Slicing to the next named service by
    # name is brittle — this read as far as `visualizer:` and swallowed the
    # whole `baseline:` service the moment one was added between them, so the
    # test failed on the config manager's own settings. Cut at the next
    # top-level service key instead.
    src = COMPOSE.read_text()
    lines = src.splitlines(True)
    start = next(n for n, l in enumerate(lines) if l.startswith("  flashback:"))
    end = next((n for n in range(start + 1, len(lines))
                if lines[n].startswith("  ") and not lines[n].startswith("   ")
                and lines[n].rstrip().endswith(":")), len(lines))
    block = "".join(lines[start:end])
    assert f"SYSIBLE_BASELINE_{suffix}" not in block, (
        f"the flashback service reads SYSIBLE_BASELINE_{suffix} — that is the "
        f"config manager's setting")


def test_the_orphan_container_from_the_rename_is_dealt_with():
    """A stack brought up during the rename runs sysible-baseline from this
    service. It holds the agent port, so the new container cannot start."""
    src = COMPOSE.read_text()
    block = src[max(0, src.index("  flashback:") - 1600):src.index("  flashback:")]
    assert "orphan" in block.lower(), "nothing explains the stranded container"
    assert "remove-orphans" in block
    assert "sysible-baseline" in block, "the note does not name the container that has to go"


@pytest.mark.skipif(subprocess.run(["sh", "-c", "command -v docker"],
                                   capture_output=True).returncode != 0,
                    reason="docker not available")
def test_docker_compose_actually_resolves_it():
    """Not a regex over the file — the real `docker compose config`, because the
    variable syntax either works in the operator's compose or it does not, and
    reading it cannot tell you which."""
    e = dict(os.environ, SYSIBLE_SSO_SHARED_SECRET="x", SYSIBLE_FLASHBACK_KEEP="97")
    out = subprocess.run(["docker", "compose", "config"], cwd=ROOT,
                         capture_output=True, text=True, env=e)
    if out.returncode != 0:                                  # pragma: no cover
        pytest.skip(f"docker compose config unavailable: {out.stderr[:200]}")
    cfg = yaml.safe_load(out.stdout)
    assert str(cfg["services"]["flashback"]["environment"]["SYSIBLE_FLASHBACK_KEEP"]) == "97"
    assert "flashback-data" in (cfg.get("volumes") or {})


# ---- the reverse migration, run for real ----------------------------------
#
# This EDITS A FILE THE OPERATOR OWNS, holding the SSO secret, the updater
# secret and the agent token. The same file's writer has already had one bug of
# exactly this shape (sed reading the VALUE as a replacement, so `&`, `|` and
# `\` corrupted paths — see test_install_env_file.py), so these extract the
# shipping function and run it rather than reading it.


def _fn(name):
    src = INSTALL.read_text()
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
    return src[start:i + 1] + "\n"


def _revert_block():
    src = INSTALL.read_text()
    i = src.index("_FB_REVERT_MARK=SYSIBLE_FLASHBACK_UNRENAMED")
    return src[i:src.index("\nfi\n", i) + 4]


@pytest.fixture(scope="module")
def harness():
    return ('#!/bin/sh\nset -eu\nENV_FILE="$1"\nsay(){ printf "  %s\\n" "$*"; }\n'
            + _fn("_upsert_kv") + _fn("_upsert_env") + _fn("_migrate_env_key")
            + _revert_block())


def _run(harness, env_path, tmp_path):
    return subprocess.run(["sh", "-s", str(env_path)], input=harness,
                          capture_output=True, text=True, cwd=tmp_path, timeout=60)


def _kv(p):
    return dict(l.split("=", 1) for l in p.read_text().splitlines() if "=" in l)


def test_a_stranded_install_gets_its_settings_back(harness, tmp_path):
    env = tmp_path / ".env"
    env.write_text("SYSIBLE_SSO_SHARED_SECRET=keep-me\n"
                   "SYSIBLE_BASELINE_AGENT_TOKEN=" + "a" * 64 + "\n"
                   "SYSIBLE_BASELINE_KEEP=99\n"
                   "SLOP_BASELINE_UPSTREAM=legacy:1234\n")
    assert _run(harness, env, tmp_path).returncode == 0
    got = _kv(env)
    assert got["SYSIBLE_FLASHBACK_AGENT_TOKEN"] == "a" * 64
    assert got["SYSIBLE_FLASHBACK_KEEP"] == "99"
    assert got["SLOP_FLASHBACK_UPSTREAM"] == "legacy:1234"
    assert got["SYSIBLE_SSO_SHARED_SECRET"] == "keep-me", "an unrelated secret was disturbed"


def test_it_runs_exactly_once_and_then_leaves_baseline_alone(harness, tmp_path):
    """THE one that matters. After the revert has run, a SYSIBLE_BASELINE_* key
    is the CONFIG MANAGER's — moving it would break that service. Nothing about
    the key distinguishes the two cases, so the marker has to."""
    env = tmp_path / ".env"
    env.write_text("SYSIBLE_BASELINE_AGENT_TOKEN=the-flashback-token\n")
    assert _run(harness, env, tmp_path).returncode == 0
    assert _kv(env)["SYSIBLE_FLASHBACK_AGENT_TOKEN"] == "the-flashback-token"

    # Now the config manager is configured, and the installer runs again.
    with env.open("a") as fh:
        fh.write("SYSIBLE_BASELINE_AGENT_TOKEN=CONFIG-MANAGER-TOKEN\n")
    assert _run(harness, env, tmp_path).returncode == 0
    got = _kv(env)
    assert got["SYSIBLE_BASELINE_AGENT_TOKEN"] == "CONFIG-MANAGER-TOKEN", \
        "the second run ate the config manager's token"
    assert got["SYSIBLE_FLASHBACK_AGENT_TOKEN"] == "the-flashback-token"


def test_the_marker_is_written(harness, tmp_path):
    env = tmp_path / ".env"
    env.write_text("SYSIBLE_BASELINE_KEEP=50\n")
    _run(harness, env, tmp_path)
    assert _kv(env).get("SYSIBLE_FLASHBACK_UNRENAMED") == "1", \
        "nothing records that the revert has run, so it will run again"


def test_a_first_install_is_not_stamped_as_migrated(harness, tmp_path):
    """A fresh install has no .env and nothing to migrate. Stamping it would be
    harmless today and a lie in the file."""
    env = tmp_path / "absent.env"
    assert _run(harness, env, tmp_path).returncode == 0
    assert not env.exists(), "the revert created an .env out of nothing"


@pytest.mark.parametrize("value,why", [
    ("https://host:8770/?a=1&b=2", "& is sed's 'whole match'"),
    ("/opt/a|b", "| closes a sed s||| expression"),
    ("/opt/back\\slash", "a backslash escapes in sed and awk"),
    ("has spaces and $dollars", "word splitting and expansion"),
])
def test_an_awkward_value_survives_the_revert(harness, tmp_path, value, why):
    env = tmp_path / ".env"
    script = harness + '_upsert_kv "$ENV_FILE" SYSIBLE_BASELINE_URL "$2"\n'
    # seed first, then migrate: the revert block runs before this line appends
    env.write_text("")
    subprocess.run(["sh", "-s", str(env), value], input=script,
                   capture_output=True, text=True, cwd=tmp_path, timeout=60)
    env.write_text(f"SYSIBLE_BASELINE_URL={value}\n")
    assert _run(harness, env, tmp_path).returncode == 0
    got = [l[len("SYSIBLE_FLASHBACK_URL="):] for l in env.read_text().splitlines()
           if l.startswith("SYSIBLE_FLASHBACK_URL=")]
    assert got == [value], f"{why}: wrote {got!r}"
