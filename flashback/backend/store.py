"""Sysible Flashback — the versioned, content-addressed config store.

A standalone store (stdlib sqlite3, no ORM) that keeps a time machine of every
managed host's config files:

  * hosts        — one row per host that has ever reported in.
  * blobs        — file CONTENT, keyed by its SHA-256, stored ONCE however many
                   files/versions/hosts share it (content-addressed dedup).
  * versions     — one row each time a tracked file's content CHANGED, pointing at
                   the blob. The last N (default 50) changed versions per file are
                   kept; older ones are pruned and their now-orphan blobs GC'd.
  * restores     — the restore queue: an operator asks for (host, path, version);
                   the host's agent polls, writes it back, and acks.
  * audit        — an append-only record of who did what.

Capture happens on the host agent (it decides what to snapshot); this store just
ingests snapshots, dedups, retains, and serves browse / diff / download / restore.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import sqlite3
import threading
import time

# Keep at most this many CHANGED versions per (host, path); older ones are pruned.
MAX_VERSIONS_PER_FILE = int(os.environ.get("SYSIBLE_FLASHBACK_KEEP", "50"))

_DATA_DIR = os.environ.get("SYSIBLE_FLASHBACK_DATA", "/data")
_DB_PATH = os.environ.get("SYSIBLE_FLASHBACK_DB", os.path.join(_DATA_DIR, "flashback.db"))

# Serialise writers in-process; SQLite itself serialises across processes.
_LOCK = threading.RLock()


def _db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _LOCK, _db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS hosts (
                host_id   TEXT PRIMARY KEY,
                label     TEXT NOT NULL DEFAULT '',
                last_ts   INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS blobs (
                sha256    TEXT PRIMARY KEY,
                size      INTEGER NOT NULL,
                content   BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS versions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                host_id     TEXT NOT NULL,
                path        TEXT NOT NULL,
                sha256      TEXT NOT NULL,
                size        INTEGER NOT NULL,
                captured_at INTEGER NOT NULL,
                FOREIGN KEY (sha256) REFERENCES blobs(sha256)
            );
            CREATE INDEX IF NOT EXISTS ix_versions_host_path
                ON versions(host_id, path, captured_at);
            CREATE TABLE IF NOT EXISTS restores (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                host_id      TEXT NOT NULL,
                path         TEXT NOT NULL,
                sha256       TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                requested_at INTEGER NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                applied_at   INTEGER
            );
            CREATE INDEX IF NOT EXISTS ix_restores_host_status
                ON restores(host_id, status);
            CREATE TABLE IF NOT EXISTS audit (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                ts     INTEGER NOT NULL,
                actor  TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            );
            """
        )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def log_audit(actor: str, action: str, detail: str = "") -> None:
    with _LOCK, _db() as c:
        c.execute("INSERT INTO audit(ts, actor, action, detail) VALUES(?,?,?,?)",
                  (int(time.time()), actor, action, detail))


def _store_blob(c: sqlite3.Connection, data: bytes) -> str:
    sha = _sha(data)
    # INSERT OR IGNORE: identical content is stored once (content-addressed dedup).
    c.execute("INSERT OR IGNORE INTO blobs(sha256, size, content) VALUES(?,?,?)",
              (sha, len(data), data))
    return sha


def _prune_versions(c: sqlite3.Connection, host_id: str, path: str) -> None:
    """Keep only the newest MAX_VERSIONS_PER_FILE versions of (host, path); delete
    the rest and GC any blob no version references any more."""
    rows = c.execute(
        "SELECT id, sha256 FROM versions WHERE host_id=? AND path=? "
        "ORDER BY captured_at DESC, id DESC", (host_id, path)).fetchall()
    stale = rows[MAX_VERSIONS_PER_FILE:]
    for r in stale:
        c.execute("DELETE FROM versions WHERE id=?", (r["id"],))
    _gc_blobs(c, [r["sha256"] for r in stale])


def _gc_blobs(c: sqlite3.Connection, shas) -> None:
    for sha in set(shas):
        still = c.execute("SELECT 1 FROM versions WHERE sha256=? LIMIT 1", (sha,)).fetchone()
        if not still:
            c.execute("DELETE FROM blobs WHERE sha256=?", (sha,))


def ingest_snapshot(host_id: str, label: str, files: list[dict]) -> dict:
    """Ingest one agent snapshot. `files` is a list of {path, content(bytes|str)}.
    A version row is created ONLY when a file's content differs from that file's
    most-recent stored version (change-only history). Returns a summary dict."""
    host_id = (host_id or "").strip()
    if not host_id:
        raise ValueError("host_id is required")
    now = int(time.time())
    changed = 0
    with _LOCK, _db() as c:
        c.execute(
            "INSERT INTO hosts(host_id, label, last_ts) VALUES(?,?,?) "
            "ON CONFLICT(host_id) DO UPDATE SET label=excluded.label, last_ts=excluded.last_ts",
            (host_id, (label or host_id).strip(), now))
        for f in files or []:
            path = str(f.get("path") or "").strip()
            if not path:
                continue
            data = f.get("content")
            if isinstance(data, str):
                data = data.encode("utf-8")
            if data is None:
                data = b""
            sha = _sha(data)
            last = c.execute(
                "SELECT sha256 FROM versions WHERE host_id=? AND path=? "
                "ORDER BY captured_at DESC, id DESC LIMIT 1", (host_id, path)).fetchone()
            if last and last["sha256"] == sha:
                continue  # unchanged since last capture — no new version
            _store_blob(c, data)
            c.execute(
                "INSERT INTO versions(host_id, path, sha256, size, captured_at) "
                "VALUES(?,?,?,?,?)", (host_id, path, sha, len(data), now))
            _prune_versions(c, host_id, path)
            changed += 1
    return {"host_id": host_id, "received": len(files or []), "changed": changed}


def list_hosts() -> list[dict]:
    with _LOCK, _db() as c:
        rows = c.execute(
            """SELECT h.host_id, h.label, h.last_ts,
                      COUNT(DISTINCT v.path) AS files,
                      COUNT(v.id)            AS versions
               FROM hosts h LEFT JOIN versions v ON v.host_id = h.host_id
               GROUP BY h.host_id ORDER BY h.label COLLATE NOCASE""").fetchall()
        return [dict(r) for r in rows]


def list_files(host_id: str) -> list[dict]:
    with _LOCK, _db() as c:
        rows = c.execute(
            """SELECT path, COUNT(*) AS versions, MAX(captured_at) AS last_ts,
                      MIN(captured_at) AS first_ts
               FROM versions WHERE host_id=? GROUP BY path ORDER BY path""",
            (host_id,)).fetchall()
        return [dict(r) for r in rows]


def list_versions(host_id: str, path: str) -> list[dict]:
    with _LOCK, _db() as c:
        rows = c.execute(
            "SELECT id, sha256, size, captured_at FROM versions "
            "WHERE host_id=? AND path=? ORDER BY captured_at DESC, id DESC",
            (host_id, path)).fetchall()
        return [dict(r) for r in rows]


def get_blob(sha256: str) -> bytes | None:
    with _LOCK, _db() as c:
        row = c.execute("SELECT content FROM blobs WHERE sha256=?", (sha256,)).fetchone()
        return bytes(row["content"]) if row else None


def _version_blob(c: sqlite3.Connection, host_id: str, path: str, sha256: str) -> bytes | None:
    """Fetch a blob only if it is actually a stored version of THIS host+path — so a
    caller can't read arbitrary content by guessing a sha from another host/file."""
    row = c.execute(
        "SELECT b.content FROM versions v JOIN blobs b ON b.sha256=v.sha256 "
        "WHERE v.host_id=? AND v.path=? AND v.sha256=? LIMIT 1",
        (host_id, path, sha256)).fetchone()
    return bytes(row["content"]) if row else None


def version_content(host_id: str, path: str, sha256: str) -> bytes | None:
    with _LOCK, _db() as c:
        return _version_blob(c, host_id, path, sha256)


def diff_versions(host_id: str, path: str, sha_a: str, sha_b: str) -> str | None:
    """Unified diff between two stored versions of a file. Returns None if either
    version isn't a real version of this host+path (never leaks other content)."""
    with _LOCK, _db() as c:
        a = _version_blob(c, host_id, path, sha_a)
        b = _version_blob(c, host_id, path, sha_b)
    if a is None or b is None:
        return None
    a_lines = a.decode("utf-8", "replace").splitlines(keepends=True)
    b_lines = b.decode("utf-8", "replace").splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        a_lines, b_lines, fromfile=f"{path}@{sha_a[:12]}", tofile=f"{path}@{sha_b[:12]}"))


def queue_restore(host_id: str, path: str, sha256: str, requested_by: str) -> dict:
    """Queue a restore of a specific stored version. The host's agent polls the queue,
    writes the version back (backing up the current file first), and acks. Refuses a
    version that isn't a real stored version of this host+path."""
    with _LOCK, _db() as c:
        exists = c.execute(
            "SELECT 1 FROM versions WHERE host_id=? AND path=? AND sha256=? LIMIT 1",
            (host_id, path, sha256)).fetchone()
        if not exists:
            raise ValueError("no such version for this host and path")
        now = int(time.time())
        cur = c.execute(
            "INSERT INTO restores(host_id, path, sha256, requested_by, requested_at, status) "
            "VALUES(?,?,?,?,?, 'pending')", (host_id, path, sha256, requested_by, now))
        rid = cur.lastrowid
    log_audit(requested_by, "restore_queued", f"{host_id}:{path}@{sha256[:12]} (#{rid})")
    return {"id": rid, "status": "pending"}


def pending_restores(host_id: str) -> list[dict]:
    with _LOCK, _db() as c:
        rows = c.execute(
            "SELECT id, path, sha256, requested_by, requested_at FROM restores "
            "WHERE host_id=? AND status='pending' ORDER BY id", (host_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            b = _version_blob(c, host_id, d["path"], d["sha256"])
            d["content_present"] = b is not None
            d["size"] = len(b) if b is not None else 0
            out.append(d)
        return out


def restore_payload(host_id: str, restore_id: int) -> tuple[dict, bytes] | None:
    """The (metadata, content) for a queued restore the agent is about to apply."""
    with _LOCK, _db() as c:
        r = c.execute(
            "SELECT * FROM restores WHERE id=? AND host_id=? AND status='pending'",
            (restore_id, host_id)).fetchone()
        if not r:
            return None
        b = _version_blob(c, host_id, r["path"], r["sha256"])
        if b is None:
            return None
        return dict(r), b


def ack_restore(host_id: str, restore_id: int, ok: bool = True) -> bool:
    with _LOCK, _db() as c:
        cur = c.execute(
            "UPDATE restores SET status=?, applied_at=? "
            "WHERE id=? AND host_id=? AND status='pending'",
            ("applied" if ok else "failed", int(time.time()), restore_id, host_id))
        changed = cur.rowcount
    if changed:
        log_audit(f"agent:{host_id}", "restore_" + ("applied" if ok else "failed"),
                  f"#{restore_id}")
    return bool(changed)


def list_audit(limit: int = 100, since_id: int = 0) -> list[dict]:
    """Read the audit trail newest-first. Same {id, ts, actor, action, detail}
    contract the other Sysible apps expose, so one aggregator client shape works
    across all of them."""
    limit = max(1, min(int(limit), 500))
    with _LOCK, _db() as c:
        rows = c.execute(
            "SELECT id, ts, actor, action, detail FROM audit WHERE id > ? "
            "ORDER BY id DESC LIMIT ?", (int(since_id), limit)).fetchall()
        return [dict(r) for r in rows]


def recent_restores(host_id: str, limit: int = 50) -> list[dict]:
    with _LOCK, _db() as c:
        rows = c.execute(
            "SELECT id, path, sha256, requested_by, requested_at, status, applied_at "
            "FROM restores WHERE host_id=? ORDER BY id DESC LIMIT ?",
            (host_id, limit)).fetchall()
        return [dict(r) for r in rows]
