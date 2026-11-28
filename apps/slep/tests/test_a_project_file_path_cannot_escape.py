"""The IDE reads and writes files by a path the browser chooses.

SLEP's IDE takes `?path=` straight from the client on six routes — read, write,
create, rename (twice) and delete. The project directory is the boundary, and
the only thing holding it is `_safe_path`. Nothing tested that, which for a
feature whose entire input is an attacker-chosen path is the wrong amount.

Reading the function and agreeing with it is not the same as running it, so
these are real payloads against the real function. They include the two that
most often get through a hand-rolled check:

  * a SYMLINK planted inside the project that points out of it. A guard that
    only inspects the string never sees this; `_safe_path` survives it because
    it resolves before comparing, which is a property worth pinning rather
    than leaving to chance.
  * an ABSOLUTE path. `root / "/etc/passwd"` is "/etc/passwd" in pathlib — the
    join silently discards the root — so the lstrip("/") is doing real work and
    is not the tidying it looks like.
"""
import os
import pathlib

import pytest
from fastapi import HTTPException

from backend import app as m


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project directory, with db.project_dir pinned to it."""
    root = tmp_path / "projects" / "7"
    root.mkdir(parents=True)
    (root / "main.tf").write_text("# real file\n")
    monkeypatch.setattr(m.db, "project_dir", lambda pid: root)
    return root


def _inside(root, target):
    return target == root or root in target.parents


# Strings that must either be refused outright or neutralised to somewhere
# inside the project. Both outcomes are safe; escaping is not.
PAYLOADS = [
    "../../../../etc/passwd",
    "subdir/../../../../etc/shadow",
    "./../../root/.ssh/id_rsa",
    "..",
    "../",
    "/etc/passwd",                       # absolute: the join would discard root
    "//etc/passwd",
    "..%2f..%2fetc%2fpasswd",            # percent-encoded: must NOT be decoded here
    "....//....//etc/passwd",
    "\\..\\..\\windows\\system32",       # backslashes are ordinary characters on POSIX
    "a/b/c/../../../../../../etc/passwd",
]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_no_payload_reaches_outside_the_project(project, payload):
    try:
        target = m._safe_path(7, payload)
    except HTTPException as e:
        assert e.status_code == 400
        return                                   # refused: safe
    assert _inside(project, target), (
        "%r resolved to %s, which is outside the project" % (payload, target))


def test_a_symlink_out_of_the_project_is_refused(project):
    """The one a string check cannot catch. _safe_path resolves first, so the
    link's TARGET is what gets compared — keep it that way."""
    link = project / "escape"
    link.symlink_to("/etc")
    with pytest.raises(HTTPException):
        m._safe_path(7, "escape/passwd")


def test_a_symlink_inside_the_project_still_works(project):
    """The guard must not be so blunt that ordinary links break: a link that
    stays inside is legitimate and some generated trees contain them."""
    (project / "sub").mkdir()
    (project / "sub" / "f.txt").write_text("x")
    (project / "alias").symlink_to(project / "sub")
    target = m._safe_path(7, "alias/f.txt")
    assert _inside(project, target)


def test_an_ordinary_path_still_resolves(project):
    """A guard that refuses everything passes every test above and ships a
    broken IDE."""
    assert m._safe_path(7, "main.tf") == project / "main.tf"
    assert m._safe_path(7, "a/b/c.txt") == project / "a" / "b" / "c.txt"
    assert m._safe_path(7, "") == project


def test_the_lstrip_is_load_bearing(project):
    """pathlib's join DISCARDS the left side when the right is absolute:

        Path("/srv/project") / "/etc/passwd"  ->  PosixPath("/etc/passwd")

    so without the lstrip an absolute path would land outside and the
    containment check would then reject a request the IDE should have served
    harmlessly. Pinned because it reads like tidying."""
    assert pathlib.Path("/srv/project") / "/etc/passwd" == pathlib.Path("/etc/passwd")
    assert _inside(project, m._safe_path(7, "/etc/passwd"))


def test_every_file_route_goes_through_the_guard():
    """Six routes take a client path. One that builds its own would be the hole,
    and it would look perfectly ordinary in review."""
    import inspect
    import re
    src = inspect.getsource(m)
    # each place a project path arrives from the request and becomes a Path
    for fn in ("def read_file", "def write_file", "def create_path",
               "def rename_path", "def delete_path"):
        if fn not in src:
            continue
        body = src[src.index(fn):]
        body = body[:body.index("\n@app.") if "\n@app." in body else 2500]
        assert "_safe_path(" in body, "%s builds a project path without _safe_path" % fn
    # ...and nothing joins a VARIABLE onto project_dir() by hand. A literal
    # ("main.tf", "variables.tf") cannot escape; a name might, so each one has
    # to be accounted for.
    #
    # KNOWN AND SAFE, each for a stated reason:
    SAFE_JOINS = {
        "fname": "infra.scaffold() returns it from the fixed _SCAFFOLDS table, "
                 "keyed by a stage that is validated to 'configure'/'maintain'. "
                 "The project NAME reaches render() and becomes file CONTENT, "
                 "never the filename.",
    }
    joins = re.findall(r"project_dir\([^)]*\)\s*/\s*([A-Za-z_][A-Za-z0-9_]*)", src)
    unknown = sorted(set(joins) - set(SAFE_JOINS))
    assert not unknown, (
        "a variable is joined onto project_dir() without going through "
        "_safe_path: %r. If it cannot carry a client-chosen path, add it to "
        "SAFE_JOINS with the reason; otherwise route it through _safe_path."
        % unknown)
    for name, why in SAFE_JOINS.items():
        assert len(why) > 20, "%s is allowlisted with no real reason" % name
