"""Restore points: a whole machine at a moment, restorable and bootable.

ASKED FOR: "I want flashback to be able to work like a backup AMI similar to
AWS. Like if a VM isn't working properly I can select a backup from flashback
and reboot the VM into that state." And then, on how: "I need a solution that
will work on baremetal as well as cloud. No matter what the hypervisor is and I
want to balance the disk space with speed but disk space is more important."

WHY NOT A DISK IMAGE. Bare metal has no hypervisor to snapshot, and neither
libvirt snapshots nor exported disk images exist there. The only thing that
works on bare metal, in every cloud and under every hypervisor at once is to
capture from INSIDE the operating system — which is what this store already
does for config files, one at a time.

WHAT A RESTORE POINT IS. A named, atomic set: every file, symlink and
directory that mattered at one moment, with its mode, uid and gid, pointing at
content in the SAME content-addressed blob store the config history uses. That
is where "disk space is more important" is paid for: a file is stored once no
matter how many points, how many versions or how many HOSTS contain it. Ten
identical Ubuntu machines share one copy of every byte of /usr, and a daily
point on an unchanged host adds a manifest and nothing else.

THE TWO INVARIANTS THAT MATTER MOST, both tested here before anything else:

  * A point is RESTORABLE ONLY WHEN SEALED, and sealing refuses while any blob
    it names is missing. Restoring a half-uploaded machine image is worse than
    not restoring at all — it is a system with some of yesterday's files and
    some of today's, which boots into a state that never existed.

  * Blob garbage collection must know about restore points. _gc_blobs asked
    only `versions`, so pruning a config file's 51st version would have deleted
    content a restore point still needed — silently, with the corruption only
    discovered at the restore nobody could then perform.
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


def _entry(path, content=b"", kind="f", mode=0o644, uid=0, gid=0, target=""):
    import hashlib
    sha = hashlib.sha256(content).hexdigest() if kind == "f" else ""
    return {"path": path, "kind": kind, "mode": mode, "uid": uid, "gid": gid,
            "size": len(content), "sha256": sha, "target": target}


# ---------------------------------------------------------------------------
# a point is not restorable until every byte of it is here
# ---------------------------------------------------------------------------
def test_a_point_with_missing_content_cannot_be_sealed(store):
    pid = store.open_restore_point("web-01", "before upgrade", roots="/")
    store.add_restore_point_entries(pid, [_entry("/etc/hosts", b"127.0.0.1 x")])
    with pytest.raises(ValueError) as e:
        store.seal_restore_point(pid)
    assert "missing" in str(e.value).lower(), str(e.value)
    assert store.restore_point(pid)["state"] != "sealed"


def test_an_unsealed_point_is_not_offered_for_restore(store):
    pid = store.open_restore_point("web-01", "half done", roots="/")
    store.add_restore_point_entries(pid, [_entry("/etc/hosts", b"x")])
    usable = [p["id"] for p in store.list_restore_points("web-01", sealed_only=True)]
    assert pid not in usable, "a half-uploaded machine image is offered as restorable"


def test_a_point_seals_once_its_content_is_uploaded(store):
    body = b"127.0.0.1 localhost\n"
    pid = store.open_restore_point("web-01", "good", roots="/")
    r = store.add_restore_point_entries(pid, [_entry("/etc/hosts", body)])
    assert r["missing"], "the store claims to already have content it has never seen"
    for sha in r["missing"]:
        store.put_blob(sha, body)
    out = store.seal_restore_point(pid)
    assert out["files"] == 1
    assert store.restore_point(pid)["state"] == "sealed"
    assert pid in [p["id"] for p in store.list_restore_points("web-01", sealed_only=True)]


# ---------------------------------------------------------------------------
# ...and the collector cannot take its content away afterwards
# ---------------------------------------------------------------------------
def test_pruning_config_history_does_not_gut_a_restore_point(store):
    """THE DANGEROUS ONE. _gc_blobs asked only `versions`. A config file whose
    history rolled past the retention limit took its blob with it — including
    when a sealed restore point still pointed at exactly that content."""
    body = b"shared between a version and a point\n"
    store.ingest_snapshot("web-01", "web-01", [{"path": "/etc/motd", "content": body}])
    pid = store.open_restore_point("web-01", "keeps it", roots="/")
    r = store.add_restore_point_entries(pid, [_entry("/etc/motd", body)])
    for sha in r["missing"]:
        store.put_blob(sha, body)
    store.seal_restore_point(pid)

    # Roll that file's history well past the retention limit.
    for i in range(store.MAX_VERSIONS_PER_FILE + 5):
        store.ingest_snapshot("web-01", "web-01",
                              [{"path": "/etc/motd", "content": b"v%d\n" % i}])

    files = store.restore_point_entries(pid)
    assert len(files) == 1
    sha = files[0]["sha256"]
    assert store.get_blob(sha) == body, \
        "the restore point's content was collected out from under it"


def test_deleting_a_point_keeps_content_another_point_needs(store):
    body = b"two points, one file\n"
    ids = []
    for label in ("monday", "tuesday"):
        pid = store.open_restore_point("web-01", label, roots="/")
        r = store.add_restore_point_entries(pid, [_entry("/etc/hosts", body)])
        for sha in r["missing"]:
            store.put_blob(sha, body)
        store.seal_restore_point(pid)
        ids.append(pid)
    store.delete_restore_point(ids[0])
    kept = store.restore_point_entries(ids[1])
    assert store.get_blob(kept[0]["sha256"]) == body, \
        "deleting one point destroyed another's content"


def test_deleting_a_point_keeps_content_the_config_history_needs(store):
    body = b"also a tracked config file\n"
    store.ingest_snapshot("web-01", "web-01", [{"path": "/etc/motd", "content": body}])
    pid = store.open_restore_point("web-01", "doomed", roots="/")
    r = store.add_restore_point_entries(pid, [_entry("/etc/motd", body)])
    for sha in r["missing"]:
        store.put_blob(sha, body)
    store.seal_restore_point(pid)
    store.delete_restore_point(pid)
    vers = store.list_versions("web-01", "/etc/motd")
    assert vers, "the config history lost its version"
    assert store.get_blob(vers[0]["sha256"]) == body, \
        "deleting a restore point collected the config history's content"


def test_deleting_a_point_does_collect_content_nothing_else_wants(store):
    """The other half: a store that never collects anything is a disk that
    fills up, and disk space is the thing being optimised for."""
    body = b"only ever in this one point\n"
    pid = store.open_restore_point("web-01", "throwaway", roots="/")
    r = store.add_restore_point_entries(pid, [_entry("/opt/thing", body)])
    sha = r["missing"][0]
    store.put_blob(sha, body)
    store.seal_restore_point(pid)
    store.delete_restore_point(pid)
    assert store.get_blob(sha) is None, "nothing references it and it is still on disk"


# ---------------------------------------------------------------------------
# disk space: the whole point
# ---------------------------------------------------------------------------
def test_identical_content_across_hosts_is_stored_once(store):
    """Ten identical machines are not ten copies. This is what makes capturing
    a whole system affordable at all."""
    body = b"#!/bin/sh\necho the same on every box\n"
    shas = set()
    for n in range(10):
        pid = store.open_restore_point(f"web-{n:02d}", "first", roots="/")
        r = store.add_restore_point_entries(pid, [_entry("/usr/bin/thing", body)])
        for sha in r["missing"]:
            store.put_blob(sha, body)
            shas.add(sha)
        store.seal_restore_point(pid)
    assert store.blob_count() == 1, \
        f"{store.blob_count()} copies of one file across ten hosts"
    assert len(shas) == 1


def test_a_second_point_on_an_unchanged_host_uploads_nothing(store):
    """The daily case. If a host has not changed, the second point costs a
    manifest and no content at all."""
    files = [(_entry(f"/etc/f{i}", b"body %d" % i), b"body %d" % i) for i in range(20)]
    first = store.open_restore_point("web-01", "day one", roots="/")
    r = store.add_restore_point_entries(first, [e for e, _b in files])
    for e, body in files:
        if e["sha256"] in r["missing"]:
            store.put_blob(e["sha256"], body)
    store.seal_restore_point(first)

    second = store.open_restore_point("web-01", "day two", roots="/")
    r2 = store.add_restore_point_entries(second, [e for e, _b in files])
    assert r2["missing"] == [], f"it asked for content it already has: {r2['missing']}"
    store.seal_restore_point(second)
    assert store.blob_count() == 20


# ---------------------------------------------------------------------------
# a machine is not just file contents
# ---------------------------------------------------------------------------
def test_modes_owners_symlinks_and_directories_survive(store):
    """A restore that writes the right bytes with the wrong mode produces a
    host that boots and then refuses logins. /etc/shadow is 0640 root:shadow
    and /usr/bin/sudo is 4755; neither is recoverable from content alone."""
    body = b"secret\n"
    entries = [
        _entry("/etc/shadow", body, mode=0o640, uid=0, gid=42),
        _entry("/usr/bin/sudo", b"ELF", mode=0o4755),
        _entry("/etc/alternatives", kind="d", mode=0o755),
        _entry("/etc/resolv.conf", kind="l", target="../run/systemd/resolve/stub-resolv.conf"),
    ]
    pid = store.open_restore_point("web-01", "modes", roots="/")
    r = store.add_restore_point_entries(pid, entries)
    for sha in r["missing"]:
        store.put_blob(sha, body if sha == entries[0]["sha256"] else b"ELF")
    store.seal_restore_point(pid)

    got = {e["path"]: e for e in store.restore_point_entries(pid)}
    assert got["/etc/shadow"]["mode"] == 0o640
    assert got["/etc/shadow"]["gid"] == 42
    assert got["/usr/bin/sudo"]["mode"] == 0o4755, "the setuid bit was lost"
    assert got["/etc/alternatives"]["kind"] == "d"
    assert got["/etc/resolv.conf"]["kind"] == "l"
    assert got["/etc/resolv.conf"]["target"].endswith("stub-resolv.conf")


def test_a_symlink_needs_no_blob(store):
    """Its target is the content. Demanding a blob for it would make every
    point unsealable on any real machine."""
    pid = store.open_restore_point("web-01", "links", roots="/")
    r = store.add_restore_point_entries(
        pid, [_entry("/etc/resolv.conf", kind="l", target="/run/x")])
    assert r["missing"] == [], r["missing"]
    assert store.seal_restore_point(pid)["files"] == 1


# ---------------------------------------------------------------------------
# the content store is content-ADDRESSED, so the address must be true
# ---------------------------------------------------------------------------
# Both of these exist because a deliberate re-break SURVIVED the first eleven
# tests: removing the digest check, and demanding content for symlinks, each
# changed real behaviour and nothing went red.
def test_a_blob_filed_under_the_wrong_digest_is_refused(store):
    """Not merely one bad file. Every manifest that ever names that hash — on
    any host, in any point, forever — would be handed these bytes, and a
    restore would write them. The digest is recomputed here, never trusted."""
    import hashlib
    body = b"the real content\n"
    liar = hashlib.sha256(b"something else entirely\n").hexdigest()
    with pytest.raises(ValueError) as e:
        store.put_blob(liar, body)
    assert "digest" in str(e.value).lower(), str(e.value)
    assert store.get_blob(liar) is None, "it was stored under the claimed hash anyway"


def test_an_honest_digest_is_accepted_and_is_idempotent(store):
    import hashlib
    body = b"the real content\n"
    sha = hashlib.sha256(body).hexdigest()
    store.put_blob(sha, body)
    store.put_blob(sha, body)          # again: a retried upload is not an error
    assert store.get_blob(sha) == body
    assert store.blob_count() == 1


def test_a_symlink_carrying_a_hash_still_needs_no_content(store):
    """A real agent builds one entry shape for everything it walks, so a
    symlink arrives with the sha256 field populated — of its target, or of
    whatever the walker last put there. Taking that as "fetch me a blob" makes
    every point on every real machine permanently unsealable, because that
    content does not exist and never will.

    The first version of this test sent sha256='' and so proved nothing: the
    deliberate break passed it.
    """
    import hashlib
    stray = hashlib.sha256(b"/run/systemd/resolve/stub-resolv.conf").hexdigest()
    pid = store.open_restore_point("web-01", "links", roots="/")
    r = store.add_restore_point_entries(pid, [{
        "path": "/etc/resolv.conf", "kind": "l", "mode": 0o777, "uid": 0, "gid": 0,
        "size": 39, "sha256": stray,
        "target": "/run/systemd/resolve/stub-resolv.conf"}])
    assert r["missing"] == [], (
        f"a symlink is being asked to supply file content: {r['missing']}")
    assert store.seal_restore_point(pid)["files"] == 1
    got = store.restore_point_entries(pid)[0]
    assert got["kind"] == "l"
    assert got["target"] == "/run/systemd/resolve/stub-resolv.conf"
    assert got["sha256"] == "", "a stray hash was kept on a symlink"


def test_a_directory_carrying_a_hash_still_needs_no_content(store):
    import hashlib
    pid = store.open_restore_point("web-01", "dirs", roots="/")
    r = store.add_restore_point_entries(pid, [{
        "path": "/etc/alternatives", "kind": "d", "mode": 0o755,
        "sha256": hashlib.sha256(b"junk").hexdigest()}])
    assert r["missing"] == [], r["missing"]
    assert store.seal_restore_point(pid)["files"] == 1
