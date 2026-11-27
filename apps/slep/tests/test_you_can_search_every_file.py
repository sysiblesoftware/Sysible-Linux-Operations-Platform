"""Ctrl-F across a whole project, from the IDE.

Asked for twice: "I shoudl be able to search throughout here" against the IDE, then
"I should be able to search for words on every file on the project. like a ctrl f".
The file tree told you what existed; nothing told you what was IN it, so finding
where a variable was set meant opening each file and looking.
"""
import pytest

from backend import db


def _write(client, pid, path, content):
    r = client.put(f"/projects/{pid}/file", json={"path": path, "content": content})
    assert r.status_code == 200, r.text


@pytest.fixture()
def tree(client, project):
    pid = project["id"]
    _write(client, pid, "main.tf", 'variable "pool" {\n  default = "default"\n}\n')
    _write(client, pid, "vars/net.tf", 'variable "network" {\n  default = "br0"\n}\n')
    _write(client, pid, "site.yml", "- hosts: all\n  roles:\n    - common\n")
    _write(client, pid, "README.md", "This project builds a POOL of machines.\n")
    return pid


def test_a_word_is_found_across_the_whole_tree(client, tree):
    d = client.get(f"/projects/{tree}/search", params={"q": "default"}).json()
    hit = {(m["path"], m["line"]) for m in d["matches"]}
    assert ("main.tf", 1) in hit or ("main.tf", 2) in hit
    assert ("vars/net.tf", 2) in hit, "a file in a subdirectory was not searched"
    assert d["files_searched"] >= 4


def test_the_match_carries_where_to_jump_to(client, tree):
    d = client.get(f"/projects/{tree}/search", params={"q": "br0"}).json()
    m = next(m for m in d["matches"] if m["path"] == "vars/net.tf")
    assert m["line"] == 2
    assert m["text"].strip().startswith('default = "br0"')
    assert m["text"][m["col"]:m["col"] + m["len"]] == "br0", (
        "the column does not point at the match, so highlighting it would be wrong")


def test_case_is_ignored_by_default_and_respected_on_request(client, tree):
    loose = client.get(f"/projects/{tree}/search", params={"q": "pool"}).json()
    assert any(m["path"] == "README.md" for m in loose["matches"]), "POOL was not matched by pool"
    strict = client.get(f"/projects/{tree}/search", params={"q": "pool", "case": True}).json()
    assert not any(m["path"] == "README.md" for m in strict["matches"])


def test_a_plain_search_treats_punctuation_as_itself(client, tree):
    """Typing a dot into a search box means a dot. If every character matched
    everything, the result is not a search."""
    d = client.get(f"/projects/{tree}/search", params={"q": "..."}).json()
    assert d["matches"] == [], "a bare '...' matched as a regular expression"


def test_regex_is_available_when_asked_for(client, tree):
    d = client.get(f"/projects/{tree}/search",
                   params={"q": r'variable "(pool|network)"', "regex": True}).json()
    assert {m["path"] for m in d["matches"]} == {"main.tf", "vars/net.tf"}


def test_a_broken_regex_is_explained_not_a_500(client, tree):
    r = client.get(f"/projects/{tree}/search", params={"q": "variable (", "regex": True})
    assert r.status_code == 400
    assert "regular expression" in r.json()["detail"]


def test_an_empty_search_is_refused(client, tree):
    assert client.get(f"/projects/{tree}/search", params={"q": "   "}).status_code == 400


def test_vendored_trees_are_not_searched(client, tree):
    """.terraform alone is routinely hundreds of megabytes of provider binaries.
    Searching it buries the three real hits in ten thousand."""
    root = db.project_dir(tree)
    for d in (".git", ".terraform", "node_modules", "__pycache__"):
        (root / d).mkdir(parents=True, exist_ok=True)
        (root / d / "junk.tf").write_text('variable "pool" {}\n')
    d = client.get(f"/projects/{tree}/search", params={"q": "pool"}).json()
    for m in d["matches"]:
        assert not any(m["path"].startswith(x) for x in (".git", ".terraform", "node_modules", "__pycache__")), m


def test_a_binary_file_is_skipped_not_fatal(client, tree):
    (db.project_dir(tree) / "terraform.tfstate.bin").write_bytes(b"\x00\x01\x02\xff" * 400)
    d = client.get(f"/projects/{tree}/search", params={"q": "pool"}).json()
    assert d["files_skipped"] >= 1
    assert any(m["path"] == "main.tf" for m in d["matches"]), "the search died on a binary file"


def test_a_huge_file_is_skipped(client, tree):
    (db.project_dir(tree) / "big.log").write_text("pool\n" * 600000)   # ~3 MB
    d = client.get(f"/projects/{tree}/search", params={"q": "pool"}).json()
    assert not any(m["path"] == "big.log" for m in d["matches"])
    assert d["files_skipped"] >= 1


def test_a_flood_of_matches_is_capped_and_says_so(client, tree):
    _write(client, tree, "many.txt", "pool\n" * 2000)
    d = client.get(f"/projects/{tree}/search", params={"q": "pool"}).json()
    assert d["truncated"] is True
    assert len(d["matches"]) <= 500, "the whole project streamed back for a one-word query"


def test_a_very_long_line_keeps_the_match_in_view(client, tree):
    _write(client, tree, "min.js", "x" * 3000 + "NEEDLE" + "y" * 3000 + "\n")
    d = client.get(f"/projects/{tree}/search", params={"q": "NEEDLE"}).json()
    m = next(m for m in d["matches"] if m["path"] == "min.js")
    assert "NEEDLE" in m["text"], "the match was cut off the end of a clipped line"
    assert len(m["text"]) <= 400
    assert m["clipped"] is True
    assert m["text"][m["col"]:m["col"] + m["len"]] == "NEEDLE"


def test_searching_needs_access_to_the_project(client, tree):
    import inspect
    from backend import app as backend_app
    src = inspect.getsource(backend_app)
    body = src[src.index("def search_project"):]
    body = body[:body.index("\n@app.")]
    assert '_guard_project(request, pid, "viewer")' in body, (
        "project search is not access-checked — it reads every file in the project")


def test_the_search_cannot_escape_the_project(client, tree):
    """It walks the project root rather than taking a path, so there is no traversal
    to exploit — pin that it stays that way."""
    import inspect
    from backend import app as backend_app
    src = inspect.getsource(backend_app)
    body = src[src.index("def search_project"):]
    body = body[:body.index("\n@app.")]
    assert "db.project_dir(pid).resolve()" in body
    assert "relative_to(root)" in body


# ------------------------------------------------------- reachable from the IDE

from pathlib import Path  # noqa: E402

IDE = (Path(__file__).resolve().parents[1] / "webgui" / "frontend" / "src" / "views" / "Ide.jsx").read_text(encoding="utf-8")
CSS = (Path(__file__).resolve().parents[1] / "webgui" / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")


def test_the_ide_actually_calls_the_search():
    assert "projects/${project.id}/search" in IDE, (
        "the endpoint exists but the IDE never calls it — nothing can reach it")


def test_there_is_a_search_box_in_the_explorer():
    assert "Search all files" in IDE
    assert "tree-search" in IDE and "tree-search" in CSS


def test_ctrl_shift_f_focuses_it():
    """The focus is normally inside Monaco, which handles its own keys, so the
    binding has to be on the window."""
    assert "e.shiftKey" in IDE and "'F'" in IDE
    assert 'window.addEventListener(\'keydown\'' in IDE


def test_a_result_opens_the_file_at_the_line():
    assert "openAt(m.path, m.line)" in IDE, "a result is not clickable"
    assert "revealLineInCenter" in IDE, "clicking a result does not jump to the line"


def test_opening_a_different_file_waits_for_its_model():
    """Revealing a line before the new content has rendered lands on the OLD file."""
    i = IDE.index("const openAt = async (p, line) =>")
    body = IDE[i:i + 400]
    assert "pendingLine.current = line" in body
    assert "if (p === path)" in body, (
        "an already-open file takes the pending path, which never re-renders, so the "
        "jump would silently never happen")


def test_the_search_is_not_fired_on_every_keystroke():
    """It walks the whole project server-side; per-keystroke means a full tree scan
    for every letter of a word."""
    i = IDE.index("const runSearch = async ()")
    assert "onKeyDown" in IDE and "'Enter'" in IDE
    # No debounce timer feeding runSearch from onChange. Bound to the handler's own
    # text: a fixed window ran past it into onKeyDown, where runSearch belongs.
    start = IDE.index("onChange={(e) => { setQ(")
    onchange = IDE[start:IDE.index("}}", start) + 2]
    assert "runSearch" not in onchange, onchange


def test_the_match_is_highlighted_where_the_server_said():
    assert "m.text.slice(m.col, m.col + m.len)" in IDE
    assert "<mark>" in IDE and ".hit-line mark" in CSS


def test_the_results_replace_the_tree_rather_than_hiding_it_forever():
    assert "{!hits && tree.map((f) => {" in IDE, (
        "the file tree still renders under the results, or never comes back")
    assert "setHits(null)" in IDE, "there is no way back to the tree"


def test_a_truncated_result_set_says_so():
    assert "narrow the search" in IDE
