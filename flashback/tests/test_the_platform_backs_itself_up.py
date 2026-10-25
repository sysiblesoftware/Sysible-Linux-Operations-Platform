"""Flashback is the SLOP platform's backup tool, including the platform itself.

Per-host configuration history never covered the thing doing the managing: lose
the SLOP host and every host's /etc was recorded while the stack that read it
was not. This covers the platform's SHAPE — the compose file, the gateway
config, the portal, and an inventory of the named volumes.

The hard part is not collecting it, it is NOT collecting the rest. This store is
readable by every operator with console access, so a credential in it is a
credential granted fleet-wide. And the claim has to stay honest: volume contents
are not here, so an operator who believes this is disaster recovery will not
take the backup that actually is one.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from backend import platform                                   # noqa: E402


# ---- secrets never land in the store --------------------------------------

@pytest.mark.parametrize("line", [
    "SYSIBLE_SSO_SHARED_SECRET=9f8e7d6c5b4a",
    "SYSIBLE_FLASHBACK_AGENT_TOKEN=" + "a" * 64,
    "SYSIBLE_UPDATER_SECRET=hunter2",
    "      SYSIBLE_BASELINE_AGENT_TOKEN: literal-token-in-compose",
    "api_key: abcdef123456",
])
def test_a_real_secret_value_is_removed(line):
    out = platform.redact(line)
    assert "redacted by Flashback" in out, f"a secret survived: {line}"
    for bad in ("9f8e7d6c5b4a", "a" * 64, "hunter2", "literal-token-in-compose",
                "abcdef123456"):
        assert bad not in out


def test_the_key_survives_so_a_diff_still_means_something():
    """"The SSO secret is set" and "it is missing" are different platform
    states, and the second is a likely cause of an outage. A redaction that
    removes the whole line cannot tell them apart."""
    out = platform.redact("SYSIBLE_SSO_SHARED_SECRET=abc123")
    assert "SYSIBLE_SSO_SHARED_SECRET" in out


@pytest.mark.parametrize("line", [
    "      SYSIBLE_BASELINE_AGENT_TOKEN: ${SYSIBLE_BASELINE_AGENT_TOKEN:-}",
    '      SYSIBLE_FLASHBACK_URL: "${SYSIBLE_FLASHBACK_URL:-}"',
    "SYSIBLE_UPDATER_SECRET=$SOME_VAR",
])
def test_compose_wiring_is_not_mistaken_for_a_secret(line):
    """${FOO:-} is the WIRING, not a credential. The first version of this
    redacted the whole compose environment block, turning the single most useful
    artefact here into a list of keys."""
    assert platform.redact(line) == line


def test_a_comment_is_left_alone():
    assert platform.redact("# TOKEN=example") == "# TOKEN=example"


@pytest.mark.parametrize("path", [".env", "slop/.env", "server.key", "tls.pem",
                                  "idp_secret.txt", "agent_token"])
def test_secret_files_are_never_collected(path):
    assert platform.is_secret_path(path) is True


def test_the_env_file_is_not_in_the_tracked_set():
    """The .env holds the SSO secret, the updater secret and BOTH service
    tokens. It is the single file that must never be here."""
    assert not any(".env" in t for t in platform.TRACKED)


# ---- what it does collect --------------------------------------------------

def test_it_collects_the_platforms_shape(tmp_path):
    (tmp_path / "gateway").mkdir()
    (tmp_path / "portal").mkdir()
    (tmp_path / "docker-compose.yml").write_text("services:\n  gateway: {}\n")
    (tmp_path / "gateway" / "Caddyfile").write_text("example.com {\n}\n")
    files = platform.collect(str(tmp_path), {"flashback-data": 10})
    paths = [f["path"] for f in files]
    assert "slop/docker-compose.yml" in paths and "slop/gateway/Caddyfile" in paths
    assert all(p.startswith("slop/") for p in paths), \
        "platform files would collide with a host's own /etc paths"


def test_a_missing_file_is_recorded_as_missing_not_skipped(tmp_path):
    """A stack without its Caddyfile is exactly what someone restoring needs to
    see. Silently omitting it makes the snapshot agree with a broken host."""
    files = {f["path"]: f["content"] for f in platform.collect(str(tmp_path), None)}
    assert "not present" in files["slop/gateway/Caddyfile"]


def test_the_volume_inventory_says_contents_are_not_stored(tmp_path):
    """The honesty requirement. An operator who thinks this is disaster
    recovery will not take the backup that actually is one."""
    files = {f["path"]: f["content"] for f in platform.collect(
        str(tmp_path), {"flashback-data": 4096, "slop-idp-data": 8192})}
    inv = files["slop/volumes.txt"]
    assert "flashback-data: 4096 bytes" in inv and "slop-idp-data: 8192 bytes" in inv
    assert "NOT STORED" in inv, "the inventory does not say the contents are absent"


def test_flashback_does_not_need_the_docker_socket():
    """The volume inventory is passed IN. Holding the docker socket is a
    root-equivalent grant, and this service has no other reason to want one."""
    src = (open(os.path.join(HERE, "backend", "platform.py")).read())
    assert "docker" not in src.lower() or "docker socket" in src.lower()
    assert "subprocess" not in src, "the collector shells out"


def test_the_platform_id_cannot_collide_with_a_host():
    """Stored under a reserved id. A host called 'platform' must not overwrite
    the platform's own history."""
    assert ":" in platform.PLATFORM_ID
