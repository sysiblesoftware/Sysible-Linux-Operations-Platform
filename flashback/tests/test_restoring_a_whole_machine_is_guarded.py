"""Putting a machine back, and everything that must not happen while doing it.

This is the operation the whole feature exists for — "if a VM isn't working
properly I can select a backup from flashback and reboot the VM into that
state" — and it is the most dangerous thing in the platform. It writes as root,
across the whole filesystem, on a machine that is currently running. Every
guard below is here because the failure it prevents is unrecoverable by the
console that caused it.

  * A point belongs to ONE host. Restoring web-02's filesystem onto web-01
    means /etc/hostname, /etc/ssh/ssh_host_*, /etc/machine-id and the fstab of
    a different machine — two hosts claiming one identity, and a box that may
    not boot. The queue refuses it; nothing about the agent protocol lets a
    host be told to become another one.

  * Only a SEALED point. Sealing is the promise that every byte arrived;
    restoring an open one writes a mixture of two days that never existed on
    any machine.

  * The agent verifies each blob against its digest before writing. The store
    is content-addressed, so the hash in the manifest is the whole contract —
    if it does not match, something between here and there is wrong and the
    worst possible response is to write it anyway.

  * A restore is a REBOOT, not a file copy. Replacing a running system's
    binaries and libraries leaves it in a state nothing has ever been tested
    in; the reboot is the operation, not an afterthought, and it defaults on.
"""
import importlib
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSIBLE_FLASHBACK_DATA", str(tmp_path))
    monkeypatch.delenv("SYSIBLE_FLASHBACK_DB", raising=False)
    for mod in ("backend.env", "backend.store"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    import backend.store as st
    st = importlib.reload(st)
    st.init_db()
    return st


def _sealed(store, host, label="good"):
    import hashlib
    body = b"/etc/hostname content\n"
    pid = store.open_restore_point(host, label, roots="/")
    r = store.add_restore_point_entries(pid, [
        {"path": "/etc/hostname", "kind": "f", "mode": 0o644, "size": len(body),
         "sha256": hashlib.sha256(body).hexdigest()}])
    for sha in r["missing"]:
        store.put_blob(sha, body)
    store.seal_restore_point(pid)
    return pid


# ---------------------------------------------------------------------------
# a point belongs to one machine
# ---------------------------------------------------------------------------
def test_one_hosts_point_cannot_be_restored_onto_another(store):
    """The worst thing this feature could do. web-02's /etc/hostname,
    ssh_host keys, machine-id and fstab landing on web-01 gives two hosts one
    identity and a box that may not boot."""
    pid = _sealed(store, "web-02")
    with pytest.raises(ValueError) as e:
        store.queue_point_restore("web-01", pid, "alice")
    msg = str(e.value).lower()
    assert "web-02" in msg or "another host" in msg or "belongs" in msg, str(e.value)
    assert store.pending_point_restore("web-01") is None


def test_its_own_point_is_accepted(store):
    pid = _sealed(store, "web-01")
    job = store.queue_point_restore("web-01", pid, "alice")
    assert job["point_id"] == pid
    assert store.pending_point_restore("web-01")["point_id"] == pid


# ---------------------------------------------------------------------------
# only a complete one
# ---------------------------------------------------------------------------
def test_an_unsealed_point_cannot_be_restored(store):
    import hashlib
    body = b"never uploaded\n"
    pid = store.open_restore_point("web-01", "half", roots="/")
    store.add_restore_point_entries(pid, [
        {"path": "/etc/hosts", "kind": "f", "size": len(body),
         "sha256": hashlib.sha256(body).hexdigest()}])
    with pytest.raises(ValueError) as e:
        store.queue_point_restore("web-01", pid, "alice")
    assert "sealed" in str(e.value).lower() or "complete" in str(e.value).lower()


def test_a_point_that_does_not_exist_is_refused(store):
    with pytest.raises(ValueError):
        store.queue_point_restore("web-01", 99999, "alice")


# ---------------------------------------------------------------------------
# it is a reboot
# ---------------------------------------------------------------------------
def test_a_restore_reboots_by_default(store):
    """Replacing a running system's binaries leaves it in a state nothing has
    been tested in. The reboot is the operation."""
    pid = _sealed(store, "web-01")
    job = store.queue_point_restore("web-01", pid, "alice")
    assert job["reboot"] is True


def test_the_reboot_can_be_declined_deliberately(store):
    """An operator restoring only a data directory may not want one — but it
    has to be said, not defaulted into."""
    pid = _sealed(store, "web-01")
    job = store.queue_point_restore("web-01", pid, "alice", reboot=False)
    assert job["reboot"] is False
    assert store.pending_point_restore("web-01")["reboot"] is False


# ---------------------------------------------------------------------------
# one at a time, and it reports back
# ---------------------------------------------------------------------------
def test_a_second_restore_is_refused_while_one_is_running(store):
    """Two restores writing different manifests across one filesystem at the
    same time produces a machine that matches neither."""
    a, b = _sealed(store, "web-01", "monday"), _sealed(store, "web-01", "tuesday")
    store.queue_point_restore("web-01", a, "alice")
    with pytest.raises(ValueError) as e:
        store.queue_point_restore("web-01", b, "alice")
    assert "already" in str(e.value).lower(), str(e.value)


def test_a_finished_restore_frees_the_host_for_another(store):
    a, b = _sealed(store, "web-01", "monday"), _sealed(store, "web-01", "tuesday")
    job = store.queue_point_restore("web-01", a, "alice")
    store.finish_point_restore(job["id"], ok=True, note="1 file written")
    assert store.pending_point_restore("web-01") is None
    assert store.queue_point_restore("web-01", b, "alice")["point_id"] == b


def test_a_failed_restore_says_so_and_does_not_block_forever(store):
    pid = _sealed(store, "web-01")
    job = store.queue_point_restore("web-01", pid, "alice")
    store.finish_point_restore(job["id"], ok=False, note="read-only filesystem")
    hist = store.restore_jobs("web-01")
    assert hist and hist[0]["status"] == "failed"
    assert "read-only" in hist[0]["note"]
    assert store.pending_point_restore("web-01") is None


def test_the_history_says_who_asked_and_what_for(store):
    """A whole-machine rollback is the thing an audit most wants to find."""
    pid = _sealed(store, "web-01", "before the bad deploy")
    store.queue_point_restore("web-01", pid, "alice")
    j = store.restore_jobs("web-01")[0]
    assert j["requested_by"] == "alice"
    assert j["point_id"] == pid
    assert j["label"] == "before the bad deploy", j


# ---------------------------------------------------------------------------
# the content the agent is handed
# ---------------------------------------------------------------------------
def test_the_manifest_handed_back_is_the_sealed_one(store):
    pid = _sealed(store, "web-01")
    store.queue_point_restore("web-01", pid, "alice")
    job = store.pending_point_restore("web-01")
    ents = store.restore_point_entries(job["point_id"])
    assert [e["path"] for e in ents] == ["/etc/hostname"]
    assert store.get_blob(ents[0]["sha256"]) == b"/etc/hostname content\n"


def test_deleting_a_point_with_a_restore_queued_is_refused(store):
    """Otherwise the agent wakes up holding a job pointing at a manifest that
    no longer exists, mid-rollback."""
    pid = _sealed(store, "web-01")
    store.queue_point_restore("web-01", pid, "alice")
    with pytest.raises(ValueError) as e:
        store.delete_restore_point(pid)
    assert "restore" in str(e.value).lower(), str(e.value)
    assert store.restore_point(pid) is not None
