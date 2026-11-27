"""SLEP has to say which build it is, and WHICH INSTANCE.

Two questions, both unanswerable from any SLEP screen before this, and both of
which cost an evening:

  * "I updated and my fix isn't here." The fix had shipped four days earlier and
    was on main. Nothing on the page could tell a stale image from a current one,
    so the next hour went into re-reading code that was already correct.

  * "Where is my project's main.tf?" A Terraform apply failed quoting real line
    numbers out of main.tf while the container being inspected had an EMPTY
    /data/projects. The apply was being run by a different SLEP, and no screen on
    either of them said so.

A build stamp answers only the first. So /version reports the instance as well —
the data directory actually in use and how many projects are in it — and the chip
puts both a glance answer and a hover answer in the rail.
"""
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
SRC = HERE / "webgui" / "frontend" / "src"


# ------------------------------------------------------------------ the answer

def test_version_says_which_build(client):
    d = client.get("/version").json()
    assert "rev" in d, "nothing reports the revision baked in at image build time"
    assert "commit_short" in d and "branch" in d


def test_version_says_which_instance(client, project):
    """The half a build stamp does not answer, and the half that was actually
    needed: two SLEPs, one with the projects and one without."""
    d = client.get("/version").json()
    assert d["data_dir"], "does not say which data directory it is using"
    assert isinstance(d["projects"], int), "does not say how many projects are in it"
    assert d["projects"] >= 1, "a project exists but /version reports %r" % (d["projects"],)
    assert d["host"] and d["pid"], "does not identify the process"


def test_version_says_when_the_code_on_disk_moved():
    """Pulled in the running directory and never restarted: the code executing is
    not the code on disk."""
    from backend import build_info
    assert "restart_needed" in build_info.info()


def test_version_needs_a_session(client):
    """Not secret — "am I on the right console" is not a privileged question — but
    not anonymous either."""
    import inspect
    from backend import app as backend_app
    src = inspect.getsource(backend_app)
    body = src[src.index("def version_route"):]
    body = body[:body.index("\n@app.")]
    sig = src[src.index("def version_route"):src.index("def version_route") + 200]
    assert "Depends(current_user)" in sig, "/version is open to anyone"


def test_an_image_with_no_revision_says_unknown_rather_than_inventing_one(monkeypatch):
    import importlib
    monkeypatch.setenv("SYSIBLE_REV", "")
    from backend import build_info
    importlib.reload(build_info)
    assert build_info.REV == ""
    # label() falls back to the checkout, and to "version unknown" with neither
    monkeypatch.setattr(build_info, "_head_commit", lambda: None)
    monkeypatch.setattr(build_info, "_git", lambda *a: None)
    assert build_info.label() == "version unknown"
    monkeypatch.setenv("SYSIBLE_REV", "deadbee")
    importlib.reload(build_info)
    assert build_info.label() == "build deadbee"
    monkeypatch.delenv("SYSIBLE_REV", raising=False)
    importlib.reload(build_info)


def test_the_identity_never_breaks_the_app(monkeypatch):
    """A console that will not load because it cannot count projects is worse than
    one that cannot say how many it has."""
    from backend import build_info, db
    monkeypatch.setattr(db, "list_projects", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    d = build_info.info()
    assert d["projects"] is None
    assert d["rev"] is not None or d["commit"] is not None or True   # still returns


# ------------------------------------------------------------- shown on screen

def test_the_rail_shows_it():
    app = (SRC / "App.jsx").read_text(encoding="utf-8")
    # RENDERED, not merely defined. A component that exists and is never placed in
    # the tree is the same as no component, and this project has shipped that twice.
    assert "<BuildChip " in app, "BuildChip is defined but never rendered"
    assert "api('version')" in app, "nothing fetches it"
    assert ".build-chip" in (SRC / "styles.css").read_text(encoding="utf-8")


def test_the_chip_shows_the_instance_on_hover():
    """The glance answer is the build; the facts that settle an argument between
    two instances are the data directory and the project count."""
    app = (SRC / "App.jsx").read_text(encoding="utf-8")
    chip = app[app.index("function BuildChip"):]
    chip = chip[:chip.index("\nfunction ")] if "\nfunction " in chip else chip[:2000]
    for fact in ("data_dir", "projects", "host", "restart_needed"):
        assert fact in chip, "the chip never shows %s" % fact


def test_the_chip_marks_a_stale_process():
    """The class, specifically — "stale" also appears in the CSS and in prose, so a
    bare substring check passed against the marker being removed."""
    app = (SRC / "App.jsx").read_text(encoding="utf-8")
    assert "' stale'" in app, "the chip no longer marks a process running stale code"
    assert "never restarted" in app
    assert ".build-chip.stale" in (SRC / "styles.css").read_text(encoding="utf-8"), (
        "the stale class is set but has no styling, so it looks identical")


def test_the_chip_survives_the_endpoint_being_unreachable():
    app = (SRC / "App.jsx").read_text(encoding="utf-8")
    assert "unreachable" in app, "a failed fetch would leave the rail blank with no explanation"


# ------------------------------------------------------- baked into the image

def test_the_image_bakes_the_revision_in():
    df = (HERE / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^ARG SYSIBLE_REV", df, re.M)
    assert re.search(r"^ENV SYSIBLE_REV=\$SYSIBLE_REV", df, re.M)
    lines = [l for l in df.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert lines[-1].startswith("ENV SYSIBLE_REV"), (
        "the revision is not the last layer, so changing it reinstalls every dependency")
