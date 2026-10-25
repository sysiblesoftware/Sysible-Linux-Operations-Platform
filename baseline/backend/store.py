"""Sysible Baseline — the declared state, and what happened when it was applied.

Four things live here and nothing else:

  states       the desired configuration, as Salt SLS, with a version per edit
  assignments  which state a host (or an environment) must satisfy
  rules        the event rules applied after a run (see eda.py)
  runs         every application, its parsed outcome, and the actions it fired

STATES ARE VERSIONED AND NEVER OVERWRITTEN. A configuration manager whose
previous declaration is unrecoverable is one bad edit away from an outage with
nothing to compare against — and "what did this say yesterday" is the first
question asked when a fleet drifts. Old versions are small text; keeping them is
cheap and the alternative is not.

This is NOT a backup of the hosts. What a host's files actually look like, as
opposed to what Baseline declared they should, is Sysible Flashback's job — a
separate service, a separate store, deliberately not merged with this one. The
declaration and the observation have to be able to disagree, or neither is worth
reading.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from . import env

_DATA_DIR = env.getenv("DATA", "/data")
_DB_PATH = env.getenv("DB", os.path.join(_DATA_DIR, "baseline.db"))
_LOCK = threading.RLock()

# Keep at most this many versions of a state; older ones are pruned.
KEEP_VERSIONS = int(env.getenv("KEEP", "50"))


def _db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _LOCK, _db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS states (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            version INTEGER NOT NULL,
            sls TEXT NOT NULL,
            author TEXT NOT NULL DEFAULT '',
            created INTEGER NOT NULL,
            UNIQUE(name, version)
        );
        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY,
            target TEXT NOT NULL,            -- host id, or env:<name>
            state_name TEXT NOT NULL,
            created INTEGER NOT NULL,
            UNIQUE(target, state_name)
        );
        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            match TEXT NOT NULL DEFAULT '*',
            trigger TEXT NOT NULL DEFAULT 'changed',
            action TEXT NOT NULL DEFAULT 'notify',
            argument TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY,
            host_id TEXT NOT NULL,
            state_name TEXT NOT NULL,
            state_version INTEGER NOT NULL,
            preview INTEGER NOT NULL DEFAULT 0,
            started INTEGER NOT NULL,
            finished INTEGER,
            ok INTEGER,
            changed INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            states_json TEXT NOT NULL DEFAULT '[]',
            actions_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS runs_host ON runs(host_id, started DESC);
        """)


def save_state(name: str, sls: str, author: str = "") -> dict:
    """A new VERSION of a state. Never an overwrite — see the module docstring."""
    if not name.strip():
        raise ValueError("a state needs a name")
    if not sls.strip():
        raise ValueError("refusing to save an empty state")
    now = int(time.time())
    with _LOCK, _db() as c:
        row = c.execute("SELECT MAX(version) v FROM states WHERE name=?", (name,)).fetchone()
        ver = (row["v"] or 0) + 1
        c.execute("INSERT INTO states(name,version,sls,author,created) VALUES(?,?,?,?,?)",
                  (name, ver, sls, author, now))
        old = c.execute("SELECT version FROM states WHERE name=? ORDER BY version DESC "
                        "LIMIT -1 OFFSET ?", (name, KEEP_VERSIONS)).fetchall()
        for o in old:
            c.execute("DELETE FROM states WHERE name=? AND version=?", (name, o["version"]))
    return {"name": name, "version": ver}


def latest_state(name: str):
    with _LOCK, _db() as c:
        r = c.execute("SELECT * FROM states WHERE name=? ORDER BY version DESC LIMIT 1",
                      (name,)).fetchone()
    return dict(r) if r else None


def state_versions(name: str) -> list:
    with _LOCK, _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT version,author,created,length(sls) AS size FROM states "
            "WHERE name=? ORDER BY version DESC", (name,)).fetchall()]


def list_states() -> list:
    with _LOCK, _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT name, MAX(version) AS version, MAX(created) AS created "
            "FROM states GROUP BY name ORDER BY name").fetchall()]


def assign(target: str, state_name: str) -> None:
    with _LOCK, _db() as c:
        c.execute("INSERT OR IGNORE INTO assignments(target,state_name,created) VALUES(?,?,?)",
                  (target, state_name, int(time.time())))


def unassign(target: str, state_name: str) -> None:
    with _LOCK, _db() as c:
        c.execute("DELETE FROM assignments WHERE target=? AND state_name=?", (target, state_name))


def states_for(target: str) -> list:
    with _LOCK, _db() as c:
        return [r["state_name"] for r in c.execute(
            "SELECT state_name FROM assignments WHERE target=? ORDER BY state_name",
            (target,)).fetchall()]


def list_assignments() -> list:
    """Every (target, state) pair. The console needs the whole picture: an
    assignment is the only thing that makes a declared state actually apply to
    anything, and a state declared but never assigned is the most common way to
    believe a fleet is managed when nothing is."""
    with _LOCK, _db() as c:
        return [{"target": r["target"], "state": r["state_name"], "created": r["created"]}
                for r in c.execute(
                    "SELECT target,state_name,created FROM assignments "
                    "ORDER BY target, state_name").fetchall()]


def save_rule(name: str, match: str, trigger: str, action: str,
              argument: str = "", enabled: bool = True) -> None:
    with _LOCK, _db() as c:
        c.execute("INSERT INTO rules(name,match,trigger,action,argument,enabled,created) "
                  "VALUES(?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                  "match=excluded.match, trigger=excluded.trigger, action=excluded.action, "
                  "argument=excluded.argument, enabled=excluded.enabled",
                  (name, match, trigger, action, argument, int(bool(enabled)), int(time.time())))


def list_rules() -> list:
    with _LOCK, _db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM rules ORDER BY name").fetchall()]


def delete_rule(name: str) -> None:
    with _LOCK, _db() as c:
        c.execute("DELETE FROM rules WHERE name=?", (name,))


def start_run(host_id: str, state_name: str, version: int, preview: bool) -> int:
    with _LOCK, _db() as c:
        cur = c.execute("INSERT INTO runs(host_id,state_name,state_version,preview,started) "
                        "VALUES(?,?,?,?,?)",
                        (host_id, state_name, version, int(bool(preview)), int(time.time())))
        return cur.lastrowid


def finish_run(run_id: int, parsed: dict, actions: list) -> None:
    with _LOCK, _db() as c:
        c.execute("UPDATE runs SET finished=?, ok=?, changed=?, failed=?, error=?, "
                  "states_json=?, actions_json=? WHERE id=?",
                  (int(time.time()), int(bool(parsed.get("ok"))), parsed.get("changed", 0),
                   parsed.get("failed", 0), parsed.get("error"),
                   json.dumps(parsed.get("states", [])), json.dumps(actions), run_id))


def recent_runs(limit: int = 50, host_id: str = "") -> list:
    q = "SELECT * FROM runs"
    args: list = []
    if host_id:
        q += " WHERE host_id=?"
        args.append(host_id)
    q += " ORDER BY started DESC LIMIT ?"
    args.append(int(limit))
    with _LOCK, _db() as c:
        rows = [dict(r) for r in c.execute(q, args).fetchall()]
    for r in rows:
        r["states"] = json.loads(r.pop("states_json") or "[]")
        r["actions"] = json.loads(r.pop("actions_json") or "[]")
    return rows


def drift_summary() -> dict:
    """Hosts whose last run changed something, failed, or errored.

    "Drift" here means Baseline had to DO something to bring the host back —
    a host that was already as declared produces no changes and does not appear.
    """
    out = {"drifted": [], "failed": [], "clean": 0}
    with _LOCK, _db() as c:
        rows = c.execute(
            "SELECT host_id, ok, changed, failed, error FROM runs r WHERE finished IS NOT NULL "
            "AND preview=0 AND started = (SELECT MAX(started) FROM runs WHERE host_id=r.host_id "
            "AND preview=0 AND finished IS NOT NULL) GROUP BY host_id").fetchall()
    for r in rows:
        if r["failed"] or r["error"]:
            out["failed"].append(r["host_id"])
        elif r["changed"]:
            out["drifted"].append(r["host_id"])
        else:
            out["clean"] += 1
    return out
