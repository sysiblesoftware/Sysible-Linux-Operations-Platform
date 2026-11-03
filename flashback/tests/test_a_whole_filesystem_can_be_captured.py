"""Getting a machine's filesystem into the store without one enormous request.

THE BLOCKER THIS CLEARS. Config capture posts every file base64'd in ONE JSON
body: the agent truncates at 8 MiB, the service refuses past 12, and the path
list is three entries long (/etc, one grub file, the cron spool). A restore
point is the whole machine — gigabytes across a hundred thousand files — so
that transport cannot carry it and no amount of raising the cap would make it
sane: the body is built in memory on both ends.

THE PROTOCOL, and it falls straight out of the store already being
content-addressed:

  1. open  — name the point, get an id
  2. manifest, in chunks — path, kind, mode, uid, gid, size, sha256 per entry.
     The reply is only the hashes this store has NEVER SEEN.
  3. blobs — upload exactly those, and nothing else
  4. seal  — refuse unless every byte arrived

On a fleet of similar machines, or a second point on a host that has not
changed, step 3 is empty. That is the whole economy: the bytes that cross the
wire are the bytes that are new to the entire store, not new to this host.

These tests drive the REAL routes through TestClient, and the sizes are the
point — a fixture that only ever sends three files would pass against the old
single-POST design too.
"""
import base64
import hashlib
import importlib
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

SECRET = "s" * 32
TOKEN = "t" * 64
AGENT = {"Authorization": f"Bearer {TOKEN}"}
OP = {"x-sysible-user": "alice", "x-sysible-role": "operator", "x-sysible-auth": SECRET}


@pytest.fixture()
def cl(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    monkeypatch.setenv("SYSIBLE_FLASHBACK_TRUST_GATEWAY_AUTH", "1")
    monkeypatch.setenv("SYSIBLE_SSO_SHARED_SECRET", SECRET)
    monkeypatch.setenv("SYSIBLE_FLASHBACK_AGENT_TOKEN", TOKEN)
    monkeypatch.setenv("SYSIBLE_FLASHBACK_DATA", str(tmp_path))
    monkeypatch.delenv("SYSIBLE_FLASHBACK_DB", raising=False)
    monkeypatch.delenv("SLOP_CONTROLLER_UPSTREAM", raising=False)
    for mod in ("backend.env", "backend.identity", "backend.store", "backend.app"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    import backend.app as m
    m = importlib.reload(m)
    m.store.init_db()
    with TestClient(m.app, base_url="http://slop.lan") as c:
        yield c


def _manifest(n, body_for):
    """n regular files, plus a symlink and a directory for realism."""
    out = []
    for i in range(n):
        b = body_for(i)
        out.append({"path": f"/usr/share/f{i:05d}", "kind": "f", "mode": 0o644,
                    "uid": 0, "gid": 0, "size": len(b),
                    "sha256": hashlib.sha256(b).hexdigest()})
    out.append({"path": "/etc/alternatives", "kind": "d", "mode": 0o755})
    out.append({"path": "/etc/resolv.conf", "kind": "l", "mode": 0o777,
                "target": "/run/systemd/resolve/stub-resolv.conf"})
    return out


def _capture(cl, host, label, n=250, body_for=None, chunk=64):
    """Drive the whole protocol, as an agent would. Returns (point_id, uploaded)."""
    body_for = body_for or (lambda i: b"content number %d\n" % i)
    pid = cl.post("/api/agent/restore-point/open",
                  json={"host_id": host, "label": label, "roots": "/"},
                  headers=AGENT).json()["point_id"]
    entries = _manifest(n, body_for)
    missing = []
    for i in range(0, len(entries), chunk):
        r = cl.post("/api/agent/restore-point/manifest",
                    json={"point_id": pid, "entries": entries[i:i + chunk]},
                    headers=AGENT)
        assert r.status_code == 200, r.text
        missing += r.json()["missing"]
    by_sha = {e["sha256"]: body_for(int(e["path"][-5:]))
              for e in entries if e["kind"] == "f"}
    uploaded = 0
    for i in range(0, len(missing), 32):
        batch = [{"sha256": s, "content_b64": base64.b64encode(by_sha[s]).decode()}
                 for s in missing[i:i + 32]]
        r = cl.post("/api/agent/restore-point/blobs",
                    json={"point_id": pid, "blobs": batch}, headers=AGENT)
        assert r.status_code == 200, r.text
        uploaded += r.json()["stored"]
    r = cl.post("/api/agent/restore-point/seal", json={"point_id": pid}, headers=AGENT)
    assert r.status_code == 200, r.text
    return pid, uploaded


# ---------------------------------------------------------------------------
# a machine goes in
# ---------------------------------------------------------------------------
def test_a_large_manifest_arrives_in_chunks_and_seals(cl):
    pid, uploaded = _capture(cl, "web-01", "before the upgrade", n=250)
    assert uploaded == 250
    p = cl.get(f"/api/restore-points/{pid}", headers=OP).json()
    assert p["state"] == "sealed"
    assert p["files"] == 252                      # 250 + the dir + the symlink
    assert p["label"] == "before the upgrade"


def test_no_single_request_has_to_be_enormous(cl):
    """The old transport built the whole machine in one body on both ends.
    Here every request is bounded by the chunk size the agent chooses."""
    pid, _ = _capture(cl, "web-01", "chunked", n=300, chunk=25)
    assert cl.get(f"/api/restore-points/{pid}", headers=OP).json()["state"] == "sealed"


# ---------------------------------------------------------------------------
# ...and the second one is nearly free
# ---------------------------------------------------------------------------
def test_a_second_point_on_an_unchanged_host_uploads_nothing(cl):
    _capture(cl, "web-01", "monday", n=200)
    _pid, uploaded = _capture(cl, "web-01", "tuesday", n=200)
    assert uploaded == 0, f"it re-uploaded {uploaded} unchanged files"


def test_a_second_host_running_the_same_image_uploads_nothing(cl):
    """Ten identical VMs are not ten copies — this is what makes a whole-system
    capture affordable at all."""
    _capture(cl, "web-01", "first", n=200)
    _pid, uploaded = _capture(cl, "web-02", "first", n=200)
    assert uploaded == 0, f"the second identical host uploaded {uploaded} files"


def test_only_what_actually_changed_is_uploaded(cl):
    def day_one(i):
        return b"content number %d\n" % i

    def day_two(i):
        return b"CHANGED %d\n" % i if i < 7 else day_one(i)

    _capture(cl, "web-01", "monday", n=120, body_for=day_one)
    _pid, uploaded = _capture(cl, "web-01", "tuesday", n=120, body_for=day_two)
    assert uploaded == 7, f"expected the 7 changed files, uploaded {uploaded}"


# ---------------------------------------------------------------------------
# the promise
# ---------------------------------------------------------------------------
def test_a_point_cannot_be_sealed_with_content_outstanding(cl):
    pid = cl.post("/api/agent/restore-point/open",
                  json={"host_id": "web-01", "label": "half", "roots": "/"},
                  headers=AGENT).json()["point_id"]
    body = b"never uploaded\n"
    cl.post("/api/agent/restore-point/manifest", json={"point_id": pid, "entries": [
        {"path": "/etc/hosts", "kind": "f", "mode": 0o644, "size": len(body),
         "sha256": hashlib.sha256(body).hexdigest()}]}, headers=AGENT)
    r = cl.post("/api/agent/restore-point/seal", json={"point_id": pid}, headers=AGENT)
    assert r.status_code == 400, r.text
    assert "missing" in r.text.lower()
    assert cl.get(f"/api/restore-points/{pid}", headers=OP).json()["state"] == "open"


def test_an_unsealed_point_is_not_listed_as_restorable(cl):
    pid = cl.post("/api/agent/restore-point/open",
                  json={"host_id": "web-01", "label": "half", "roots": "/"},
                  headers=AGENT).json()["point_id"]
    listed = cl.get("/api/restore-points", params={"host_id": "web-01",
                                                   "restorable": "true"},
                    headers=OP).json()["points"]
    assert pid not in [p["id"] for p in listed]


def test_a_blob_that_does_not_match_its_digest_is_refused(cl):
    pid = cl.post("/api/agent/restore-point/open",
                  json={"host_id": "web-01", "label": "liar", "roots": "/"},
                  headers=AGENT).json()["point_id"]
    r = cl.post("/api/agent/restore-point/blobs", json={"point_id": pid, "blobs": [
        {"sha256": hashlib.sha256(b"one thing").hexdigest(),
         "content_b64": base64.b64encode(b"quite another").decode()}]},
        headers=AGENT)
    assert r.status_code == 400, r.text
    assert "digest" in r.text.lower()


# ---------------------------------------------------------------------------
# who may do what
# ---------------------------------------------------------------------------
def test_the_agent_routes_need_the_agent_token(cl):
    for path, body in (("open", {"host_id": "x", "label": "y"}),
                       ("manifest", {"point_id": 1, "entries": []}),
                       ("blobs", {"point_id": 1, "blobs": []}),
                       ("seal", {"point_id": 1})):
        r = cl.post(f"/api/agent/restore-point/{path}", json=body)
        assert r.status_code in (401, 403), f"{path} accepted an unauthenticated agent"


def test_an_auditor_can_see_points_but_not_make_one(cl):
    pid, _ = _capture(cl, "web-01", "visible", n=5)
    aud = {"x-sysible-user": "bob", "x-sysible-role": "auditor",
           "x-sysible-auth": SECRET}
    assert cl.get("/api/restore-points", params={"host_id": "web-01"},
                  headers=aud).status_code == 200
    r = cl.delete(f"/api/restore-points/{pid}", headers=aud)
    assert r.status_code == 403, r.text
