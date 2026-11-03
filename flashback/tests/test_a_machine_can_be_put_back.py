"""The round trip, through the real routes: capture a machine, break it, put
it back.

Everything else tests a piece. This drives the whole thing the way an agent
and an operator actually would — capture a filesystem, change it, queue a
restore, then play the agent's side and check that what it is handed
reconstructs exactly what was captured, down to the modes and the symlinks.

It is deliberately a REAL filesystem on disk at both ends: a capture that is
only ever fed hand-written dicts proves the store works and says nothing about
whether a machine round-trips.
"""
import base64
import hashlib
import importlib
import os
import stat
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
    monkeypatch.setenv("SYSIBLE_FLASHBACK_DATA", str(tmp_path / "db"))
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


def _machine(root):
    """A small but realistic filesystem: nested dirs, a setuid binary, a
    0640 secret, a symlink, and a file that will change."""
    (root / "etc").mkdir(parents=True, exist_ok=True)
    (root / "usr" / "bin").mkdir(parents=True, exist_ok=True)
    (root / "etc" / "hostname").write_text("web-01\n")
    (root / "etc" / "shadow").write_text("root:!:19000:::::\n")
    os.chmod(root / "etc" / "shadow", 0o640)
    (root / "usr" / "bin" / "sudo").write_bytes(b"\x7fELF fake sudo\n")
    os.chmod(root / "usr" / "bin" / "sudo", 0o4755)
    (root / "etc" / "app.conf").write_text("mode = good\n")
    link = root / "etc" / "resolv.conf"
    if not link.is_symlink():
        link.symlink_to("../run/resolv.conf")
    return root


def _walk(root):
    """What an agent's walker produces: one entry per path, relative to root."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in list(dirnames) + list(filenames):
            full = os.path.join(dirpath, name)
            rel = "/" + os.path.relpath(full, root)
            st = os.lstat(full)
            if stat.S_ISLNK(st.st_mode):
                out.append({"path": rel, "kind": "l", "mode": 0o777,
                            "uid": st.st_uid, "gid": st.st_gid, "size": st.st_size,
                            "target": os.readlink(full), "sha256": ""})
            elif stat.S_ISDIR(st.st_mode):
                out.append({"path": rel, "kind": "d",
                            "mode": stat.S_IMODE(st.st_mode),
                            "uid": st.st_uid, "gid": st.st_gid})
            else:
                body = open(full, "rb").read()
                out.append({"path": rel, "kind": "f",
                            "mode": stat.S_IMODE(st.st_mode),
                            "uid": st.st_uid, "gid": st.st_gid, "size": len(body),
                            "sha256": hashlib.sha256(body).hexdigest()})
    return {e["path"]: e for e in out}


def _capture(cl, host, label, root):
    entries = _walk(root)
    pid = cl.post("/api/agent/restore-point/open",
                  json={"host_id": host, "label": label, "roots": "/"},
                  headers=AGENT).json()["point_id"]
    missing = []
    vals = list(entries.values())
    for i in range(0, len(vals), 50):
        missing += cl.post("/api/agent/restore-point/manifest",
                           json={"point_id": pid, "entries": vals[i:i + 50]},
                           headers=AGENT).json()["missing"]
    by_sha = {}
    for path, e in entries.items():
        if e["kind"] == "f":
            by_sha[e["sha256"]] = open(os.path.join(root, path.lstrip("/")), "rb").read()
    if missing:
        cl.post("/api/agent/restore-point/blobs", json={"point_id": pid, "blobs": [
            {"sha256": s, "content_b64": base64.b64encode(by_sha[s]).decode()}
            for s in missing]}, headers=AGENT)
    r = cl.post("/api/agent/restore-point/seal", json={"point_id": pid}, headers=AGENT)
    assert r.status_code == 200, r.text
    return pid


def _agent_restores(cl, host, into):
    """Play the agent's side of a pending restore onto `into`."""
    job = cl.get("/api/agent/restore-point/pending", params={"host_id": host},
                 headers=AGENT).json()["job"]
    assert job, "no job was waiting"
    cl.post("/api/agent/restore-point/start", json={"job_id": job["id"]}, headers=AGENT)
    ents, off = [], 0
    while True:
        page = cl.get("/api/agent/restore-point/manifest",
                      params={"point_id": job["point_id"], "limit": 500, "offset": off},
                      headers=AGENT).json()["entries"]
        if not page:
            break
        ents += page
        off += len(page)
    # Directories first so the files have somewhere to land.
    for e in sorted(ents, key=lambda x: (x["kind"] != "d", x["path"])):
        dest = os.path.join(into, e["path"].lstrip("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if e["kind"] == "d":
            os.makedirs(dest, exist_ok=True)
            os.chmod(dest, e["mode"])
        elif e["kind"] == "l":
            if os.path.islink(dest) or os.path.exists(dest):
                os.remove(dest)
            os.symlink(e["target"], dest)
        else:
            r = cl.get("/api/agent/restore-point/blob",
                       params={"sha256": e["sha256"]}, headers=AGENT)
            assert r.status_code == 200, (e["path"], r.status_code)
            body = r.content
            # The agent verifies before writing. The hash is the contract.
            assert hashlib.sha256(body).hexdigest() == e["sha256"], e["path"]
            with open(dest, "wb") as fh:
                fh.write(body)
            os.chmod(dest, e["mode"])
    cl.post("/api/agent/restore-point/finish",
            json={"job_id": job["id"], "ok": True, "note": f"{len(ents)} entries"},
            headers=AGENT)
    return job, ents


# ---------------------------------------------------------------------------
def test_a_captured_machine_rebuilds_byte_for_byte(cl, tmp_path):
    src = _machine(tmp_path / "src")
    pid = _capture(cl, "web-01", "before the bad deploy", src)

    r = cl.post(f"/api/restore-points/{pid}/restore", json={}, headers=OP)
    assert r.status_code == 200, r.text
    assert "reboot" in r.json()["detail"]

    dest = tmp_path / "dest"
    dest.mkdir()
    _job, ents = _agent_restores(cl, "web-01", str(dest))

    before, after = _walk(src), _walk(dest)
    assert set(before) == set(after), (
        f"missing: {sorted(set(before) - set(after))[:5]} "
        f"extra: {sorted(set(after) - set(before))[:5]}")
    for path, e in before.items():
        got = after[path]
        assert got["kind"] == e["kind"], path
        assert got["mode"] == e["mode"], f"{path}: mode {oct(got['mode'])} != {oct(e['mode'])}"
        if e["kind"] == "f":
            assert got["sha256"] == e["sha256"], f"{path}: content differs"
        if e["kind"] == "l":
            assert got["target"] == e["target"], path


def test_the_setuid_bit_and_the_secret_mode_survive(cl, tmp_path):
    """A restore that writes the right bytes with the wrong mode gives a host
    that boots and then refuses every login."""
    src = _machine(tmp_path / "src")
    pid = _capture(cl, "web-01", "modes", src)
    cl.post(f"/api/restore-points/{pid}/restore", json={}, headers=OP)
    dest = tmp_path / "dest"
    dest.mkdir()
    _agent_restores(cl, "web-01", str(dest))
    assert stat.S_IMODE(os.lstat(dest / "usr" / "bin" / "sudo").st_mode) == 0o4755
    assert stat.S_IMODE(os.lstat(dest / "etc" / "shadow").st_mode) == 0o640


def test_a_broken_machine_goes_back_to_how_it_was(cl, tmp_path):
    """The actual story: capture, break it, restore, and the breakage is gone
    while everything else is untouched."""
    src = _machine(tmp_path / "src")
    pid = _capture(cl, "web-01", "known good", src)

    (src / "etc" / "app.conf").write_text("mode = catastrophically bad\n")
    (src / "etc" / "extra-junk").write_text("left by the bad deploy\n")

    cl.post(f"/api/restore-points/{pid}/restore", json={}, headers=OP)
    dest = tmp_path / "dest"
    dest.mkdir()
    _agent_restores(cl, "web-01", str(dest))

    assert (dest / "etc" / "app.conf").read_text() == "mode = good\n"
    assert not (dest / "etc" / "extra-junk").exists(), \
        "a file created after the capture is in the restored manifest"
    assert (dest / "etc" / "hostname").read_text() == "web-01\n"


def test_the_job_reports_back_and_the_host_is_free_again(cl, tmp_path):
    src = _machine(tmp_path / "src")
    pid = _capture(cl, "web-01", "one", src)
    cl.post(f"/api/restore-points/{pid}/restore", json={}, headers=OP)
    dest = tmp_path / "dest"
    dest.mkdir()
    job, ents = _agent_restores(cl, "web-01", str(dest))

    jobs = cl.get("/api/restore-jobs", params={"host_id": "web-01"},
                  headers=OP).json()["jobs"]
    assert jobs[0]["status"] == "done", jobs[0]
    assert str(len(ents)) in jobs[0]["note"]
    assert cl.get("/api/agent/restore-point/pending", params={"host_id": "web-01"},
                  headers=AGENT).json()["job"] is None
    # ...and another restore can now be asked for.
    assert cl.post(f"/api/restore-points/{pid}/restore", json={},
                   headers=OP).status_code == 200


def test_a_restore_cannot_be_aimed_at_another_host(cl, tmp_path):
    """The route takes the host from the POINT, so no request can name one.
    Belt and braces: the store refuses it too."""
    src = _machine(tmp_path / "src")
    pid = _capture(cl, "web-02", "web-02 state", src)
    r = cl.post(f"/api/restore-points/{pid}/restore",
                json={"host_id": "web-01"}, headers=OP)
    assert r.status_code == 200, r.text
    assert cl.get("/api/agent/restore-point/pending", params={"host_id": "web-01"},
                  headers=AGENT).json()["job"] is None, \
        "web-01 was handed web-02's filesystem"
    assert cl.get("/api/agent/restore-point/pending", params={"host_id": "web-02"},
                  headers=AGENT).json()["job"]["point_id"] == pid


def test_an_auditor_cannot_start_a_restore(cl, tmp_path):
    src = _machine(tmp_path / "src")
    pid = _capture(cl, "web-01", "one", src)
    aud = {"x-sysible-user": "bob", "x-sysible-role": "auditor",
           "x-sysible-auth": SECRET}
    assert cl.post(f"/api/restore-points/{pid}/restore", json={},
                   headers=aud).status_code == 403
