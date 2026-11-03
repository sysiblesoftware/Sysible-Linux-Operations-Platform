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

from . import env

# Keep at most this many CHANGED versions per (host, path); older ones are pruned.
MAX_VERSIONS_PER_FILE = int(env.getenv("KEEP", "50"))

_DATA_DIR = env.getenv("DATA", "/data")

# THE DATABASE FILE IS STILL CALLED flashback.db, ON PURPOSE.
#
# This file IS the product: every version of every config file on every host,
# and the one thing in the stack that cannot be rebuilt from anything else. It
# lives in the `flashback-data` volume, which keeps its name for the same
# reason. Renaming either would not migrate the history — it would hide it, and
# the console would come up pristine and empty on a fleet with years of
# captures, which reads exactly like "Flashback deleted everything".
#
# Nobody sees this name: it is a file inside a Docker volume. The rebrand is for
# the humans, and no human reads this path.
_DB_PATH = env.getenv("DB", os.path.join(_DATA_DIR, "flashback.db"))

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
            -- A RESTORE POINT: one machine, at one moment, as a set.
            --
            -- The config history above answers "what did this file used to
            -- say". A restore point answers "put this machine back" — so it is
            -- a manifest, not a stream of per-file versions, and it is only
            -- usable once every byte it names is here. `state` is that
            -- promise: 'open' while content is still arriving, 'sealed' when
            -- the set is complete. Restoring a half-uploaded machine is worse
            -- than not restoring: it boots into a mixture of two days that
            -- never existed.
            CREATE TABLE IF NOT EXISTS restore_points (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                host_id    TEXT NOT NULL,
                label      TEXT NOT NULL DEFAULT '',
                roots      TEXT NOT NULL DEFAULT '',
                note       TEXT NOT NULL DEFAULT '',
                state      TEXT NOT NULL DEFAULT 'open',
                created_at INTEGER NOT NULL,
                sealed_at  INTEGER
            );
            CREATE INDEX IF NOT EXISTS ix_rp_host
                ON restore_points(host_id, created_at);
            -- One row per path. `sha256` points into the SAME blob store the
            -- config history uses, which is the whole economy of this feature:
            -- a file is stored once however many points, versions or HOSTS
            -- contain it. Mode, uid and gid are not decoration — a restore
            -- that writes the right bytes into /etc/shadow with the wrong mode
            -- produces a host that boots and then refuses every login.
            CREATE TABLE IF NOT EXISTS restore_point_entries (
                point_id INTEGER NOT NULL,
                path     TEXT NOT NULL,
                kind     TEXT NOT NULL DEFAULT 'f',   -- f file | l symlink | d dir
                mode     INTEGER NOT NULL DEFAULT 0,
                uid      INTEGER NOT NULL DEFAULT 0,
                gid      INTEGER NOT NULL DEFAULT 0,
                size     INTEGER NOT NULL DEFAULT 0,
                sha256   TEXT NOT NULL DEFAULT '',
                target   TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (point_id, path)
            );
            CREATE INDEX IF NOT EXISTS ix_rpe_sha
                ON restore_point_entries(sha256);
            -- A WHOLE-MACHINE restore: one job, one host, one sealed point.
            --
            -- Separate from `restores` above, which queues ONE FILE. The two
            -- are different operations with different risks: putting back a
            -- config file is routine, putting back a filesystem is a reboot
            -- into a different machine. Sharing a table would have meant one
            -- poll, one ack and one status word covering both, and an agent
            -- that could not tell which it had been handed.
            CREATE TABLE IF NOT EXISTS restore_jobs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                host_id      TEXT NOT NULL,
                point_id     INTEGER NOT NULL,
                requested_by TEXT NOT NULL,
                requested_at INTEGER NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                reboot       INTEGER NOT NULL DEFAULT 1,
                note         TEXT NOT NULL DEFAULT '',
                finished_at  INTEGER
            );
            CREATE INDEX IF NOT EXISTS ix_rj_host
                ON restore_jobs(host_id, status, id);
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
    """Drop blobs nothing points at any more.

    BOTH REFERRERS. This asked only `versions`, which was right while versions
    were the only thing holding content. A restore point holds content too, and
    shares it — the same /etc/motd is one blob whether it arrived as a config
    version or inside a machine image. Collecting on the version alone deleted
    content a sealed restore point still named, silently, with the damage only
    discovered at the restore that could no longer be performed.
    """
    for sha in set(shas):
        if not sha:
            continue
        still = c.execute("SELECT 1 FROM versions WHERE sha256=? LIMIT 1",
                          (sha,)).fetchone()
        if not still:
            still = c.execute("SELECT 1 FROM restore_point_entries WHERE sha256=? "
                              "LIMIT 1", (sha,)).fetchone()
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

# --------------------------------------------------------------------------- #
# Cross-host compare — "is this file the same everywhere?"
#
# The per-host view answers "how did this file change over time". The question an
# operator actually arrives with after an incident is the other one: this box
# behaves differently, is its config different from the rest? That needs the
# NEWEST stored version of one path on every host that has it, compared against a
# chosen flashback. It is the capability the EE panel has and this did not.
# --------------------------------------------------------------------------- #
def paths_across_hosts(limit: int = 500) -> list[dict]:
    """Every tracked path, with how many hosts have it — the picker for a
    comparison. Ordered by the paths present on the most hosts, since those are
    the ones worth comparing."""
    with _LOCK, _db() as c:
        rows = c.execute(
            "SELECT path, COUNT(DISTINCT host_id) AS hosts FROM versions "
            "GROUP BY path ORDER BY hosts DESC, path COLLATE NOCASE LIMIT ?",
            (max(1, min(int(limit), 2000)),)).fetchall()
        return [dict(r) for r in rows]


def newest_version(host_id: str, path: str) -> dict | None:
    with _LOCK, _db() as c:
        r = c.execute(
            "SELECT sha256, size, captured_at FROM versions "
            "WHERE host_id=? AND path=? ORDER BY captured_at DESC, id DESC LIMIT 1",
            (host_id, path)).fetchone()
        return dict(r) if r else None


def compare_path(path: str, flashback: str | None = None) -> dict:
    """Compare the newest stored version of `path` across every host that has it.

    Returns the flashback plus one row per other host saying whether it matches,
    differs, or has no copy at all. Comparison is by CONTENT HASH, so it is a
    cheap lookup rather than a diff of every pair — the diff is fetched only for
    the host an operator actually opens.
    """
    with _LOCK, _db() as c:
        rows = c.execute(
            """SELECT v.host_id, h.label, v.sha256, v.size, v.captured_at
                 FROM versions v LEFT JOIN hosts h ON h.host_id = v.host_id
                WHERE v.path = ?
                  AND v.id = (SELECT id FROM versions v2
                               WHERE v2.host_id = v.host_id AND v2.path = v.path
                               ORDER BY v2.captured_at DESC, v2.id DESC LIMIT 1)
                ORDER BY h.label COLLATE NOCASE""", (path,)).fetchall()
        newest = [dict(r) for r in rows]
        # Hosts Flashback knows about that have NO version of this path at all.
        known = c.execute("SELECT host_id, label FROM hosts").fetchall()
    have = {r["host_id"] for r in newest}
    if not newest:
        return {"path": path, "flashback": None, "hosts": [], "missing": [],
                "distinct": 0}
    # DEFAULT FLASHBACK = the MAJORITY content, not the alphabetically-first host.
    # Picking by name made the single odd host the reference whenever its label
    # sorted first, so the hosts that agreed were the ones reported as
    # "differing" — backwards, and the diff read inverted too. The majority is
    # what an operator means by "the rest of the fleet"; ties break by label so
    # the choice is still deterministic.
    if flashback:
        base = next((r for r in newest if r["host_id"] == flashback), None)
    else:
        base = None
    if base is None:
        counts: dict = {}
        for r in newest:
            counts[r["sha256"]] = counts.get(r["sha256"], 0) + 1
        top = max(counts.values())
        base = min((r for r in newest if counts[r["sha256"]] == top),
                   key=lambda r: ((r["label"] or r["host_id"]).lower()))
    out = []
    for r in newest:
        if r["host_id"] == base["host_id"]:
            continue
        out.append({**r, "same": r["sha256"] == base["sha256"]})
    missing = [{"host_id": k["host_id"], "label": k["label"]}
               for k in known if k["host_id"] not in have]
    return {
        "path": path,
        "flashback": base,
        "hosts": out,
        "missing": sorted(missing, key=lambda m: (m["label"] or "").lower()),
        # How many genuinely different contents exist across the fleet. 1 means
        # every host agrees; anything higher is the number worth investigating.
        "distinct": len({r["sha256"] for r in newest}),
    }


def diff_across_hosts(path: str, host_a: str, host_b: str) -> str | None:
    """Unified diff of the newest version of one path on two different hosts.
    None when either host has no stored version of it — never invents content."""
    a = newest_version(host_a, path)
    b = newest_version(host_b, path)
    if not a or not b:
        return None
    with _LOCK, _db() as c:
        ba = _version_blob(c, host_a, path, a["sha256"])
        bb = _version_blob(c, host_b, path, b["sha256"])
    if ba is None or bb is None:
        return None
    return "".join(difflib.unified_diff(
        ba.decode("utf-8", "replace").splitlines(keepends=True),
        bb.decode("utf-8", "replace").splitlines(keepends=True),
        fromfile=f"{host_a}:{path}", tofile=f"{host_b}:{path}"))


# =============================================================================
# Restore points — a whole machine at a moment
# =============================================================================
# The config history answers "what did this file used to say". A restore point
# answers "put this machine back": every file, symlink and directory that
# mattered at one moment, with its mode and ownership, pointing at content in
# the blob store above.
#
# WHY FROM INSIDE THE OS. It has to work on bare metal as well as in any cloud,
# under any hypervisor — and bare metal has no hypervisor to snapshot. Reading
# the filesystem from inside is the only mechanism all three share.
#
# WHY A MANIFEST AND NOT AN IMAGE. Disk space matters more than restore speed,
# and the blob store is already content-addressed: a file is stored once
# however many points, versions or hosts contain it. Ten identical machines
# share one copy of /usr; a daily point on an unchanged host costs a manifest
# and no content at all. A disk image pays full size every time.


def open_restore_point(host_id: str, label: str, roots: str = "",
                       note: str = "", host_label: str = "") -> int:
    """Start one. It is 'open' — NOT restorable — until seal_restore_point.

    `label` names the POINT ("before the upgrade"); `host_label` names the HOST.
    They are two different strings and conflating them renamed the machine: a
    host Flashback had not seen before was registered under the point's label
    and then appeared in the hosts column, for ever, as "before the upgrade".
    """
    host_id = (host_id or "").strip()
    if not host_id:
        raise ValueError("host_id is required")
    now = int(time.time())
    with _LOCK, _db() as c:
        c.execute("INSERT OR IGNORE INTO hosts(host_id, label, last_ts) VALUES(?,?,?)",
                  (host_id, (host_label or host_id).strip(), now))
        cur = c.execute(
            "INSERT INTO restore_points(host_id,label,roots,note,state,created_at) "
            "VALUES(?,?,?,?,'open',?)",
            (host_id, (label or "").strip(), roots or "", note or "", now))
        return int(cur.lastrowid)


def add_restore_point_entries(point_id: int, entries: list) -> dict:
    """Record part of a manifest. Returns {"missing": [sha256, …]}.

    THE MANIFEST COMES FIRST, CONTENT SECOND. The agent describes what it has —
    path, kind, mode, uid, gid, size, sha256 — and is told back only the
    hashes this store has never seen. On a fleet of similar machines, or a
    second point on an unchanged host, that list is empty and nothing is
    uploaded at all. It is what makes capturing a whole filesystem affordable
    over a link, and it falls straight out of the store already being
    content-addressed.

    Called repeatedly for one point: a real manifest is far too large for one
    request, so it arrives in chunks and this is additive.
    """
    rows, want = [], []
    for e in entries or []:
        path = str(e.get("path") or "").strip()
        if not path:
            continue
        kind = str(e.get("kind") or "f")[:1] or "f"
        sha = str(e.get("sha256") or "")
        # Only a regular file has content to fetch. A symlink IS its target and
        # a directory has none, so demanding a blob for either would leave
        # every point on a real machine permanently unsealable.
        if kind == "f" and sha:
            want.append(sha)
        rows.append((int(point_id), path, kind, int(e.get("mode") or 0),
                     int(e.get("uid") or 0), int(e.get("gid") or 0),
                     int(e.get("size") or 0), sha if kind == "f" else "",
                     str(e.get("target") or "") if kind == "l" else ""))
    with _LOCK, _db() as c:
        if not c.execute("SELECT 1 FROM restore_points WHERE id=? AND state='open'",
                         (int(point_id),)).fetchone():
            raise ValueError("that restore point is not open for writing")
        c.executemany(
            "INSERT OR REPLACE INTO restore_point_entries"
            "(point_id,path,kind,mode,uid,gid,size,sha256,target) "
            "VALUES(?,?,?,?,?,?,?,?,?)", rows)
        missing = _missing(c, want)
    return {"added": len(rows), "missing": missing}


def _missing(c: sqlite3.Connection, shas) -> list:
    out = []
    for sha in sorted(set(s for s in shas if s)):
        if not c.execute("SELECT 1 FROM blobs WHERE sha256=? LIMIT 1", (sha,)).fetchone():
            out.append(sha)
    return out


def missing_blobs(shas) -> list:
    """Which of these the store has never seen. Public so an agent can ask
    before it reads a single byte off the disk."""
    with _LOCK, _db() as c:
        return _missing(c, shas)


def put_blob(sha256: str, content: bytes) -> bool:
    """Store one blob. Idempotent, and VERIFIED.

    The digest is recomputed here rather than trusted. This store is
    content-addressed, so a blob filed under the wrong hash is not merely one
    bad file: every manifest that ever names that hash — on any host, in any
    point — would be handed the wrong bytes, and a restore would write them.
    """
    if isinstance(content, str):
        content = content.encode("utf-8", "replace")
    real = _sha(content)
    if sha256 and sha256 != real:
        raise ValueError(f"content does not match its digest "
                         f"(claimed {sha256[:12]}…, is {real[:12]}…)")
    with _LOCK, _db() as c:
        c.execute("INSERT OR IGNORE INTO blobs(sha256,size,content) VALUES(?,?,?)",
                  (real, len(content), content))
    return True


def get_blob(sha256: str):
    with _LOCK, _db() as c:
        row = c.execute("SELECT content FROM blobs WHERE sha256=?",
                        (sha256 or "",)).fetchone()
    return bytes(row["content"]) if row else None


def blob_count() -> int:
    with _LOCK, _db() as c:
        return int(c.execute("SELECT COUNT(*) n FROM blobs").fetchone()["n"])


def seal_restore_point(point_id: int) -> dict:
    """Declare the set complete. REFUSES while any blob it names is absent.

    This is the promise the whole feature rests on. A point that can be
    restored while content is still arriving produces a machine holding some of
    yesterday's files and some of today's — a state that never existed on any
    host, booted deliberately, with the operator believing they rolled back.
    """
    with _LOCK, _db() as c:
        row = c.execute("SELECT * FROM restore_points WHERE id=?",
                        (int(point_id),)).fetchone()
        if not row:
            raise ValueError("no such restore point")
        shas = [r["sha256"] for r in c.execute(
            "SELECT DISTINCT sha256 FROM restore_point_entries "
            "WHERE point_id=? AND kind='f' AND sha256<>''", (int(point_id),))]
        missing = _missing(c, shas)
        if missing:
            raise ValueError(
                f"{len(missing)} file(s) of this restore point are missing their "
                f"content, so it cannot be sealed — upload them and seal again")
        n = c.execute("SELECT COUNT(*) n FROM restore_point_entries WHERE point_id=?",
                      (int(point_id),)).fetchone()["n"]
        if not n:
            raise ValueError("a restore point with no files is not a restore point")
        size = c.execute("SELECT COALESCE(SUM(size),0) s FROM restore_point_entries "
                         "WHERE point_id=?", (int(point_id),)).fetchone()["s"]
        c.execute("UPDATE restore_points SET state='sealed', sealed_at=? WHERE id=?",
                  (int(time.time()), int(point_id)))
    return {"id": int(point_id), "files": int(n), "bytes": int(size)}


def _rp_row(r) -> dict:
    d = dict(r)
    d["id"] = int(d["id"])
    return d


def list_restore_points(host_id: str = "", sealed_only: bool = False) -> list:
    """Newest first. `sealed_only` is what the console offers for restore —
    an open point is a capture in progress, not a machine you can go back to."""
    q = "SELECT * FROM restore_points WHERE 1=1"
    args = []
    if host_id:
        q += " AND host_id=?"
        args.append(host_id)
    if sealed_only:
        q += " AND state='sealed'"
    q += " ORDER BY created_at DESC, id DESC"
    with _LOCK, _db() as c:
        rows = [_rp_row(r) for r in c.execute(q, args)]
        for d in rows:
            st = c.execute("SELECT COUNT(*) n, COALESCE(SUM(size),0) s "
                           "FROM restore_point_entries WHERE point_id=?",
                           (d["id"],)).fetchone()
            d["files"], d["bytes"] = int(st["n"]), int(st["s"])
    return rows


def restore_point_roots(point_id: int) -> list:
    """What this point holds, per captured root: [{root, files, dirs, links,
    bytes}, …]. Computed in SQL over the whole manifest, because the console
    only ever fetches the first page of it and a summary built from that page
    would understate every root after the first.
    """
    p = restore_point(point_id)
    if not p:
        return []
    roots = [r for r in str(p.get("roots") or "").split(":") if r.strip()]
    with _LOCK, _db() as c:
        rows = list(c.execute(
            "SELECT path,kind,size FROM restore_point_entries WHERE point_id=?",
            (int(point_id),)))
    # Longest root first, so /usr/local wins over / for /usr/local/bin/x.
    order = sorted(roots, key=len, reverse=True)
    out = {r: {"root": r, "files": 0, "dirs": 0, "links": 0, "bytes": 0}
           for r in roots}
    other = {"root": "elsewhere", "files": 0, "dirs": 0, "links": 0, "bytes": 0}
    for r in rows:
        path = r["path"] or ""
        hit = None
        for root in order:
            if path == root or path.startswith(root.rstrip("/") + "/"):
                hit = out[root]
                break
        d = hit if hit is not None else other
        k = (r["kind"] or "f")[:1]
        if k == "d":
            d["dirs"] += 1
        elif k == "l":
            d["links"] += 1
        else:
            d["files"] += 1
            d["bytes"] += int(r["size"] or 0)
    res = [d for d in (out[r] for r in roots)
           if d["files"] or d["dirs"] or d["links"]]
    if other["files"] or other["dirs"] or other["links"]:
        res.append(other)
    return res


def restore_point_counts(host_id: str = "") -> dict:
    """{host_id: how many restore points it has}. One query for the whole fleet:
    the console needs it per row, and a point-per-host round trip over a fleet
    of three hundred is how a host list stops loading."""
    q = ("SELECT host_id, COUNT(*) n FROM restore_points "
         "WHERE state='sealed'")
    args = []
    if host_id:
        q += " AND host_id=?"
        args.append(host_id)
    q += " GROUP BY host_id"
    with _LOCK, _db() as c:
        return {r["host_id"]: int(r["n"]) for r in c.execute(q, args)}


def restore_point(point_id: int):
    with _LOCK, _db() as c:
        row = c.execute("SELECT * FROM restore_points WHERE id=?",
                        (int(point_id),)).fetchone()
    return _rp_row(row) if row else None


def restore_point_entries(point_id: int, limit: int = 0, offset: int = 0) -> list:
    """The manifest. Paged, because a whole filesystem does not fit in one
    response and the agent restores it in chunks anyway."""
    q = ("SELECT path,kind,mode,uid,gid,size,sha256,target "
         "FROM restore_point_entries WHERE point_id=? ORDER BY path")
    args = [int(point_id)]
    if limit:
        q += " LIMIT ? OFFSET ?"
        args += [int(limit), int(offset)]
    with _LOCK, _db() as c:
        return [dict(r) for r in c.execute(q, args)]


def delete_restore_point(point_id: int) -> bool:
    """Forget it, and collect whatever nothing else wants.

    REFUSED while a restore of it is queued or running: the agent would
    otherwise wake up holding a job that points at a manifest which no longer
    exists, halfway through rewriting a filesystem.

    The collection is the point of deleting — this store exists to not fill a
    disk — but it has to go through _gc_blobs, which asks BOTH the config
    history and the other restore points before dropping anything.
    """
    with _LOCK, _db() as c:
        busy = c.execute("SELECT id FROM restore_jobs WHERE point_id=? AND status "
                         "IN (?,?) LIMIT 1", (int(point_id), *_ACTIVE)).fetchone()
        if busy:
            raise ValueError(f"a restore of this point is in progress "
                             f"(job #{busy['id']}) — it cannot be deleted yet")
        shas = [r["sha256"] for r in c.execute(
            "SELECT DISTINCT sha256 FROM restore_point_entries "
            "WHERE point_id=? AND sha256<>''", (int(point_id),))]
        c.execute("DELETE FROM restore_point_entries WHERE point_id=?", (int(point_id),))
        c.execute("DELETE FROM restore_points WHERE id=?", (int(point_id),))
        _gc_blobs(c, shas)
    return True


# -----------------------------------------------------------------------------
# Restoring a whole machine
# -----------------------------------------------------------------------------
_ACTIVE = ("pending", "running")


def queue_point_restore(host_id: str, point_id: int, requested_by: str,
                        reboot: bool = True) -> dict:
    """Ask a host to become one of its own sealed restore points.

    THE HOST MUST OWN THE POINT. This is the worst thing the feature could do
    otherwise: web-02's /etc/hostname, its ssh host keys, its machine-id and
    its fstab written onto web-01 gives two machines one identity and a box
    that may not come back. Nothing in the agent protocol lets a host be told
    to become a different one, and it is refused here as well — the agent
    cannot be the only thing standing between a bad row and a dead host.

    IT MUST BE SEALED. Sealing is the promise that every byte arrived. An open
    point is a capture in progress, and restoring one writes a mixture of two
    days that never existed on any machine.

    ONE AT A TIME. Two restores writing different manifests across one
    filesystem concurrently produces a host that matches neither.

    `reboot` defaults ON because a restore IS a reboot: replacing a running
    system's binaries and libraries leaves it in a state nothing has been
    tested in. Declining it is a deliberate choice for the operator putting
    back a data directory, not a default anyone should fall into.
    """
    host_id = (host_id or "").strip()
    with _LOCK, _db() as c:
        p = c.execute("SELECT * FROM restore_points WHERE id=?",
                      (int(point_id),)).fetchone()
        if not p:
            raise ValueError("no such restore point")
        if p["host_id"] != host_id:
            raise ValueError(
                f"that restore point was captured from {p['host_id']}, not "
                f"{host_id} — a machine can only be put back to its own state")
        if p["state"] != "sealed":
            raise ValueError("that restore point is not sealed (its capture never "
                             "finished), so it is not a state any machine was in")
        busy = c.execute("SELECT id FROM restore_jobs WHERE host_id=? AND status IN "
                         "(?,?) LIMIT 1", (host_id, *_ACTIVE)).fetchone()
        if busy:
            raise ValueError(f"{host_id} is already restoring (job #{busy['id']}) — "
                             "wait for it to finish or fail")
        now = int(time.time())
        cur = c.execute(
            "INSERT INTO restore_jobs(host_id,point_id,requested_by,requested_at,"
            "status,reboot) VALUES(?,?,?,?,'pending',?)",
            (host_id, int(point_id), requested_by or "", now, 1 if reboot else 0))
        jid = int(cur.lastrowid)
    log_audit(requested_by or "", "restore_point_queued",
              f"{host_id} -> #{point_id} ({p['label'] or 'unlabelled'})")
    return {"id": jid, "host_id": host_id, "point_id": int(point_id),
            "reboot": bool(reboot), "status": "pending"}


def pending_point_restore(host_id: str):
    """The job this host should be carrying out, or None. One or nothing."""
    with _LOCK, _db() as c:
        r = c.execute(
            "SELECT j.*, p.label, p.roots, p.created_at AS point_created "
            "FROM restore_jobs j JOIN restore_points p ON p.id=j.point_id "
            "WHERE j.host_id=? AND j.status IN (?,?) ORDER BY j.id LIMIT 1",
            ((host_id or "").strip(), *_ACTIVE)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["reboot"] = bool(d["reboot"])
        d["files"] = int(c.execute(
            "SELECT COUNT(*) n FROM restore_point_entries WHERE point_id=?",
            (d["point_id"],)).fetchone()["n"])
    return d


def start_point_restore(job_id: int) -> bool:
    with _LOCK, _db() as c:
        cur = c.execute("UPDATE restore_jobs SET status='running' "
                        "WHERE id=? AND status='pending'", (int(job_id),))
        return bool(cur.rowcount)


def finish_point_restore(job_id: int, ok: bool = True, note: str = "") -> bool:
    """Record the outcome and free the host. A job that never reports is why
    `pending` and `running` are both 'active': a host that died mid-restore
    stays blocked until somebody looks, rather than quietly accepting another."""
    with _LOCK, _db() as c:
        row = c.execute("SELECT host_id FROM restore_jobs WHERE id=?",
                        (int(job_id),)).fetchone()
        cur = c.execute(
            "UPDATE restore_jobs SET status=?, finished_at=?, note=? "
            "WHERE id=? AND status IN (?,?)",
            ("done" if ok else "failed", int(time.time()), (note or "")[:1000],
             int(job_id), *_ACTIVE))
        changed = cur.rowcount
    if changed and row:
        log_audit(f"agent:{row['host_id']}", "restore_point_" + ("done" if ok else "failed"),
                  f"#{job_id} {note[:200]}")
    return bool(changed)


def restore_jobs(host_id: str = "", limit: int = 50) -> list:
    """Newest first, with the point's label — "before the bad deploy" is what
    an audit is looking for, not an id."""
    q = ("SELECT j.*, p.label FROM restore_jobs j "
         "LEFT JOIN restore_points p ON p.id=j.point_id WHERE 1=1")
    args = []
    if host_id:
        q += " AND j.host_id=?"
        args.append(host_id)
    q += " ORDER BY j.id DESC LIMIT ?"
    args.append(int(limit))
    with _LOCK, _db() as c:
        out = []
        for r in c.execute(q, args):
            d = dict(r)
            d["reboot"] = bool(d["reboot"])
            d["label"] = d["label"] or ""
            out.append(d)
    return out
