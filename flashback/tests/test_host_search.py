"""The hosts column filters too, the same way the files column does.

It always had a filter — and it did not read as one. It appeared only past eight
hosts, so a smaller fleet had no way to narrow the list and nobody with eight
hosts knew the control existed; and it was a bare input in the flow of the column,
so on a fleet long enough to need it, it had already scrolled out of sight under
the list it was filtering.

Both columns now use one control, so they cannot drift apart again.
"""
import re
from pathlib import Path

import pytest

UI = (Path(__file__).resolve().parents[1] / "backend" / "ui.py").read_text(encoding="utf-8")
RENDER = UI[UI.index("function renderHosts()"):UI.index("function hostRow(")]
BUILDER = UI[UI.index("function colSearch("):UI.index("function matchesFilter")]


def test_both_columns_use_the_same_control():
    """Two hand-rolled search boxes is how one of them ends up without Escape, or
    without a count, or not sticky — which is exactly what had happened."""
    assert "colSearch(" in RENDER, "the hosts column rolls its own"
    files = UI[UI.index("async function loadFiles"):UI.index("async function loadVersions")]
    assert "colSearch(" in files
    assert ".hostfilter{" not in UI, "the old bare-input styling is still here"


def test_it_appears_before_the_list_is_unscannable():
    m = re.search(r"const FILTER_FROM=(\d+);", UI)
    assert m, "the threshold is gone"
    assert int(m.group(1)) <= 5, \
        "a filter nobody sees until they have nine hosts is a filter nobody finds"


def test_it_stays_put_while_the_list_scrolls():
    css = UI[UI.index(".colsearch{"):UI.index(".colsearch input{")]
    assert "position:sticky" in css and "top:0" in css


def test_it_says_how_many_it_is_hiding():
    assert "' of ' + hosts.length" in RENDER


def test_it_matches_the_environment_too():
    """"lab" has to find the lab boxes. Matching only the hostname would make the
    grouping decorative."""
    fn = UI[UI.index("function matchesFilter"):UI.index("async function loadHosts")]
    for field in ("h.label", "h.host_id", "h.address", "h.environment"):
        assert field in fn, f"{field} is not searched"


def test_a_filtered_view_opens_the_folds():
    """A match inside a collapsed environment must not be silently withheld."""
    assert "const open=q ? true :" in RENDER


def test_no_match_says_so():
    assert "No host or environment matches" in RENDER


def test_escape_clears_it():
    assert "e.key==='Escape'" in BUILDER


def test_typing_does_not_lose_the_cursor():
    """The hosts column repaints wholesale on every keystroke, so focus and caret
    have to be put back or the box drops a character and jumps to the start."""
    assert "again.focus()" in RENDER and "setSelectionRange" in RENDER
