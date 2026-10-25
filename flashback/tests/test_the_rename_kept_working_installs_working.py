"""Flashback was renamed Baseline for two days, then renamed back — and the name
Baseline now belongs to a different product.

That is the whole subtlety here. A rename-and-revert would normally leave no
trace worth testing, but this one does, because Sysible Baseline is now the
Salt-driven CONFIG MANAGER, and it owns SYSIBLE_BASELINE_* for itself.
Flashback therefore cannot keep reading that prefix as a courtesy fallback:
SYSIBLE_BASELINE_AGENT_TOKEN and SYSIBLE_BASELINE_DATA are names both products
want, and the backup service quietly consuming the config manager's token fails
in a way nobody would think to look for.

So this is the one place in the codebase where a legacy name is deliberately
DROPPED rather than honoured, and these tests pin both halves of that: Flashback
reads only its own prefix, and an install stranded by the two-day rename is told
so out loud rather than silently falling back to defaults — where the default
agent token is empty, which fails closed and backs nothing up.

What has not moved, and must not: the version store file, the volume, and the
agent-facing header names. Those were never renamed even while the product was,
precisely so this kind of churn could never reach them.
"""
import importlib
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

# Every setting the service reads, with a value that is obviously not a default.
SETTINGS = [
    ("KEEP", "97"),
    ("DATA", "/tmp/flashback-settings-probe"),
    ("DB", "/tmp/flashback-settings-probe/x.db"),
    ("LOCAL_USER", "someone"),
    ("LOCAL_ROLE", "readonly"),
    ("TRUST_GATEWAY_AUTH", "1"),
    ("AGENT_TOKEN", "a" * 64),
    ("AGENT_BIND", "10.9.8.7"),
    ("MAX_REQUEST_BYTES", "1234"),
    ("CONTROLLER_TIMEOUT_S", "11"),
]


def _clean(monkeypatch, suffix):
    for p in ("SYSIBLE_FLASHBACK_", "SYSIBLE_FLASHBACK_"):
        monkeypatch.delenv(p + suffix, raising=False)


@pytest.mark.parametrize("suffix,value", SETTINGS)
def test_only_flashbacks_own_prefix_is_read(monkeypatch, suffix, value):
    """SYSIBLE_BASELINE_* belongs to the config manager now. Reading it here
    would mean the backup service picking up another product's settings."""
    from backend import env
    _clean(monkeypatch, suffix)
    monkeypatch.setenv("SYSIBLE_BASELINE_" + suffix, value)
    importlib.reload(env)
    assert env.getenv(suffix, "DEFAULT") == "DEFAULT", (
        f"Flashback read SYSIBLE_BASELINE_{suffix} — that prefix is the config "
        f"manager's, and sharing it means one service silently using the "
        f"other's configuration")


@pytest.mark.parametrize("suffix,value", SETTINGS)
def test_its_own_prefix_is_read(monkeypatch, suffix, value):
    from backend import env
    _clean(monkeypatch, suffix)
    monkeypatch.setenv("SYSIBLE_FLASHBACK_" + suffix, value)
    importlib.reload(env)
    assert env.getenv(suffix, "DEFAULT") == value


def test_an_explicitly_empty_value_is_not_a_missing_one(monkeypatch):
    """`SYSIBLE_FLASHBACK_AGENT_TOKEN=` means "no token" on purpose, and no
    token means the agent endpoints fail closed."""
    from backend import env
    _clean(monkeypatch, "AGENT_TOKEN")
    monkeypatch.setenv("SYSIBLE_FLASHBACK_AGENT_TOKEN", "")
    importlib.reload(env)
    assert env.getenv("AGENT_TOKEN", "x") == ""


def test_the_version_store_is_still_the_same_file_on_disk(monkeypatch, tmp_path):
    """THE one that cannot be undone. This file holds every version of every
    config file on every host and is the only thing in the stack that cannot be
    rebuilt from anything else. Point the service at a new filename and it comes
    up serenely empty on a fleet with years of captures — indistinguishable, to
    the operator looking at it, from Flashback having deleted the lot."""
    from backend import env, store
    monkeypatch.delenv("SYSIBLE_FLASHBACK_DB", raising=False)
    monkeypatch.delenv("SYSIBLE_FLASHBACK_DB", raising=False)
    monkeypatch.setenv("SYSIBLE_FLASHBACK_DATA", str(tmp_path))
    importlib.reload(env)
    importlib.reload(store)
    assert store._DB_PATH == str(tmp_path / "flashback.db"), (
        "the version store moved. Every existing install's history is now in a "
        "file the service no longer opens.")


def test_the_agent_token_is_accepted_under_the_pre_rebrand_header(monkeypatch):
    """Controller and Flashback are separate deployments on separate upgrade
    schedules. A Controller that has not been updated yet still sends
    x-sysible-flashback-agent, and if that stopped authenticating, config backup
    would stop for the whole fleet at the moment one half was updated."""
    from backend import env, identity
    monkeypatch.setenv("SYSIBLE_FLASHBACK_AGENT_TOKEN", "t" * 64)
    importlib.reload(env)
    importlib.reload(identity)

    class _R:
        def __init__(self, h):
            self.headers = h
    assert identity.agent_auth_ok(_R({"x-sysible-flashback-agent": "t" * 64})) is True
    assert identity.agent_auth_ok(_R({"x-sysible-flashback-agent": "t" * 64})) is True
    assert identity.agent_auth_ok(_R({"authorization": "Bearer " + "t" * 64})) is True
    assert identity.agent_auth_ok(_R({"x-sysible-flashback-agent": "wrong"})) is False


def test_a_restore_carries_its_path_and_digest_under_both_header_names(monkeypatch, tmp_path):
    """The reader here is a host AGENT, the slowest-moving half of the fleet:
    they update on their own heartbeat and some are powered off for weeks. An
    agent that cannot find the path and digest either refuses the restore or
    writes the wrong file — on a machine nobody is watching."""
    monkeypatch.setenv("SYSIBLE_FLASHBACK_DATA", str(tmp_path))
    monkeypatch.setenv("SYSIBLE_FLASHBACK_TRUST_GATEWAY_AUTH", "1")
    monkeypatch.setenv("SYSIBLE_SSO_SHARED_SECRET", "s" * 32)
    monkeypatch.setenv("SYSIBLE_FLASHBACK_AGENT_TOKEN", "t" * 64)
    monkeypatch.delenv("SLOP_CONTROLLER_UPSTREAM", raising=False)
    from backend import env
    importlib.reload(env)
    for mod in ("backend.identity", "backend.store", "backend.app"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    import backend.app as m
    m = importlib.reload(m)
    from starlette.testclient import TestClient

    m.store.init_db()
    m.store.ingest_snapshot("h1", "h1", [{"path": "/etc/hosts", "content": "127.0.0.1 x\n"}])
    versions = m.store.list_versions("h1", "/etc/hosts")
    sha = versions[0]["sha256"]
    queued = m.store.queue_restore("h1", "/etc/hosts", sha, "tester")
    rid = queued["id"] if isinstance(queued, dict) else queued

    with TestClient(m.app, base_url="http://slop.lan") as cl:
        r = cl.get(f"/api/agent/restores/{rid}/payload",
                   headers={"authorization": "Bearer " + "t" * 64},
                   params={"host_id": "h1"})
    assert r.status_code == 200, r.text
    for name in ("X-Flashback-Path", "X-Flashback-Path"):
        assert r.headers.get(name) == "/etc/hosts", f"{name} missing"
    for name in ("X-Flashback-Sha256", "X-Flashback-Sha256"):
        assert (r.headers.get(name) or "").strip(), f"{name} missing"
    assert r.headers["X-Flashback-Sha256"] == r.headers["X-Flashback-Sha256"]


def test_an_install_stranded_by_the_rename_is_told_so(monkeypatch):
    """The two-day window. install.sh migrates these back, but a stack that has
    not been re-run still has them — and silence here means Flashback comes up
    on defaults, with an empty agent token, backing nothing up while every
    status light stays green."""
    from backend import env
    monkeypatch.delenv("SYSIBLE_FLASHBACK_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("SYSIBLE_BASELINE_AGENT_TOKEN", "t" * 64)
    importlib.reload(env)
    assert ("SYSIBLE_BASELINE_AGENT_TOKEN", "SYSIBLE_FLASHBACK_AGENT_TOKEN") \
        in env.orphaned_names()
    msg = env.warn_about_orphans()
    assert "SYSIBLE_BASELINE_AGENT_TOKEN" in msg
    assert "fails closed" in msg, "the warning does not say what it COSTS"


def test_a_migrated_install_is_not_nagged(monkeypatch):
    from backend import env
    monkeypatch.setenv("SYSIBLE_BASELINE_AGENT_TOKEN", "t" * 64)
    monkeypatch.setenv("SYSIBLE_FLASHBACK_AGENT_TOKEN", "t" * 64)
    importlib.reload(env)
    assert env.orphaned_names() == []
    assert env.warn_about_orphans() == ""


def test_the_config_managers_own_settings_are_not_mistaken_for_strays(monkeypatch):
    """SYSIBLE_BASELINE_* names that Flashback never owned belong to the config
    manager and must not appear in this warning at all."""
    from backend import env
    monkeypatch.setenv("SYSIBLE_BASELINE_SALT_ROOT", "/srv/salt")
    monkeypatch.setenv("SYSIBLE_BASELINE_EDA_ENABLED", "1")
    monkeypatch.setenv("SYSIBLE_FLASHBACK_AGENT_TOKEN", "t" * 64)
    importlib.reload(env)
    assert env.orphaned_names() == [], \
        "the config manager's own settings are being reported as Flashback strays"
