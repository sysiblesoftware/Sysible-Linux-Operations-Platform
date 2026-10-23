"""Sysible Baseline was called Sysible Flashback. Nothing on a running fleet knows that.

This file is the whole safety net under the rebrand. A product rename is a cheap
piece of work with one expensive failure mode: the names that are not brand at
all — environment variables, header names, the file the database lives in — look
exactly like the ones that are, and renaming those turns a cosmetic change into
a fleet-wide outage that reports itself as "Baseline isn't backing anything up".

We have the receipt for getting this wrong. Baseline was D3lorean before it was
Flashback, THAT rename changed the product name and left the variables alone, and
the Controller still reads SYSIBLE_D3LOREAN_PATHS today. The cost was not an
outage — it was that nobody could ever safely tidy it up afterwards, because by
then no one knew which installs depended on which spelling.

So every compatibility promise is pinned here, each one named after what breaks
if it is dropped.
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
    ("DATA", "/tmp/baseline-settings-probe"),
    ("DB", "/tmp/baseline-settings-probe/x.db"),
    ("LOCAL_USER", "someone"),
    ("LOCAL_ROLE", "readonly"),
    ("TRUST_GATEWAY_AUTH", "1"),
    ("AGENT_TOKEN", "a" * 64),
    ("AGENT_BIND", "10.9.8.7"),
    ("MAX_REQUEST_BYTES", "1234"),
    ("CONTROLLER_TIMEOUT_S", "11"),
]


def _clean(monkeypatch, suffix):
    for p in ("SYSIBLE_BASELINE_", "SYSIBLE_FLASHBACK_"):
        monkeypatch.delenv(p + suffix, raising=False)


@pytest.mark.parametrize("suffix,value", SETTINGS)
def test_the_pre_rebrand_variable_is_still_honoured(monkeypatch, suffix, value):
    """An upgrade does not rewrite the operator's .env before the service starts.
    If the old name stopped being read, every setting on every existing install
    would silently revert to its default the moment Baseline was deployed — the
    agent token to empty, which fails closed, which stops config backup fleet-wide."""
    from backend import env
    _clean(monkeypatch, suffix)
    monkeypatch.setenv("SYSIBLE_FLASHBACK_" + suffix, value)
    importlib.reload(env)
    assert env.getenv(suffix, "DEFAULT") == value


@pytest.mark.parametrize("suffix,value", SETTINGS)
def test_the_current_variable_wins_when_both_are_set(monkeypatch, suffix, value):
    """Exactly the state an upgrade leaves behind: the installer writes the new
    name and an old line is still lying in the file. The operator's current
    spelling has to win, or their deliberate change is overridden by history."""
    from backend import env
    _clean(monkeypatch, suffix)
    monkeypatch.setenv("SYSIBLE_FLASHBACK_" + suffix, "stale-value")
    monkeypatch.setenv("SYSIBLE_BASELINE_" + suffix, value)
    importlib.reload(env)
    assert env.getenv(suffix, "DEFAULT") == value


def test_an_explicitly_empty_new_value_does_not_inherit_the_old_one(monkeypatch):
    """`SYSIBLE_BASELINE_AGENT_TOKEN=` means "no token", and no token means the
    agent endpoints fail closed. Treating empty as unset would quietly re-arm
    them with a token the operator thought they had removed."""
    from backend import env
    _clean(monkeypatch, "AGENT_TOKEN")
    monkeypatch.setenv("SYSIBLE_FLASHBACK_AGENT_TOKEN", "old-token")
    monkeypatch.setenv("SYSIBLE_BASELINE_AGENT_TOKEN", "")
    importlib.reload(env)
    assert env.getenv("AGENT_TOKEN", "x") == ""


def test_the_version_store_is_still_the_same_file_on_disk(monkeypatch, tmp_path):
    """THE one that cannot be undone. This file holds every version of every
    config file on every host and is the only thing in the stack that cannot be
    rebuilt from anything else. Point the service at a new filename and it comes
    up serenely empty on a fleet with years of captures — indistinguishable, to
    the operator looking at it, from Baseline having deleted the lot."""
    from backend import env, store
    monkeypatch.delenv("SYSIBLE_BASELINE_DB", raising=False)
    monkeypatch.delenv("SYSIBLE_FLASHBACK_DB", raising=False)
    monkeypatch.setenv("SYSIBLE_BASELINE_DATA", str(tmp_path))
    importlib.reload(env)
    importlib.reload(store)
    assert store._DB_PATH == str(tmp_path / "flashback.db"), (
        "the version store moved. Every existing install's history is now in a "
        "file the service no longer opens.")


def test_the_agent_token_is_accepted_under_the_pre_rebrand_header(monkeypatch):
    """Controller and Baseline are separate deployments on separate upgrade
    schedules. A Controller that has not been updated yet still sends
    x-sysible-flashback-agent, and if that stopped authenticating, config backup
    would stop for the whole fleet at the moment one half was updated."""
    from backend import env, identity
    monkeypatch.setenv("SYSIBLE_BASELINE_AGENT_TOKEN", "t" * 64)
    importlib.reload(env)
    importlib.reload(identity)

    class _R:
        def __init__(self, h):
            self.headers = h
    assert identity.agent_auth_ok(_R({"x-sysible-flashback-agent": "t" * 64})) is True
    assert identity.agent_auth_ok(_R({"x-sysible-baseline-agent": "t" * 64})) is True
    assert identity.agent_auth_ok(_R({"authorization": "Bearer " + "t" * 64})) is True
    assert identity.agent_auth_ok(_R({"x-sysible-baseline-agent": "wrong"})) is False


def test_a_restore_carries_its_path_and_digest_under_both_header_names(monkeypatch, tmp_path):
    """The reader here is a host AGENT, the slowest-moving half of the fleet:
    they update on their own heartbeat and some are powered off for weeks. An
    agent that cannot find the path and digest either refuses the restore or
    writes the wrong file — on a machine nobody is watching."""
    monkeypatch.setenv("SYSIBLE_BASELINE_DATA", str(tmp_path))
    monkeypatch.setenv("SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH", "1")
    monkeypatch.setenv("SYSIBLE_SSO_SHARED_SECRET", "s" * 32)
    monkeypatch.setenv("SYSIBLE_BASELINE_AGENT_TOKEN", "t" * 64)
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
    for name in ("X-Baseline-Path", "X-Flashback-Path"):
        assert r.headers.get(name) == "/etc/hosts", f"{name} missing"
    for name in ("X-Baseline-Sha256", "X-Flashback-Sha256"):
        assert (r.headers.get(name) or "").strip(), f"{name} missing"
    assert r.headers["X-Baseline-Sha256"] == r.headers["X-Flashback-Sha256"]


def test_the_legacy_prefix_is_reported_so_it_can_eventually_be_retired(monkeypatch):
    """The reason the D3lorean variables are still there is that nothing ever
    said which installs used them. This is how this rename avoids the same fate:
    the service can tell an operator exactly what is still on the old spelling."""
    from backend import env
    monkeypatch.delenv("SYSIBLE_BASELINE_KEEP", raising=False)
    monkeypatch.setenv("SYSIBLE_FLASHBACK_KEEP", "50")
    importlib.reload(env)
    assert ("SYSIBLE_FLASHBACK_KEEP", "SYSIBLE_BASELINE_KEEP") in env.legacy_names_in_use()
    monkeypatch.setenv("SYSIBLE_BASELINE_KEEP", "50")
    assert ("SYSIBLE_FLASHBACK_KEEP", "SYSIBLE_BASELINE_KEEP") not in env.legacy_names_in_use(), \
        "a setting already migrated should not still be nagged about"
