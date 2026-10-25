"""A host here captures 800-1000 configs. Finding one must not mean scrolling.

Reported against the files column: "there should be a search bar to find the
configs". The column listed every captured path, in order, with no way to narrow
it — on the screenshot that was ~900 rows between the operator and /etc/X11/Xreset.
"""
from pathlib import Path

import pytest

UI = (Path(__file__).resolve().parents[1] / "backend" / "ui.py").read_text(encoding="utf-8")
FN = UI[UI.index("async function loadFiles"):UI.index("async function loadVersions")]
# Both columns share one control (colSearch). The behaviour that used to live in
# loadFiles — Escape, the count — lives there now.
BUILDER = UI[UI.index("function colSearch("):UI.index("function matchesFilter")]


def test_the_column_has_a_filter():
    assert "colSearch(" in FN, "no search control"
    assert "inp.type='search'" in BUILDER
    assert "setAttribute('aria-label'" in BUILDER, "the box is unlabelled for a screen reader"


def test_it_filters_the_list_it_already_has():
    """Re-fetching a thousand rows per keystroke would make typing feel broken, and
    hammer the backend for data the page is holding."""
    assert "let allFiles=[]" in UI
    assert "allFiles.filter" in FN
    assert FN.count("jget(") == 1, "the filter re-fetches"


def test_every_term_must_match_in_any_order():
    """"x11 session" should find /etc/X11/Xsession. Requiring the terms adjacent,
    or in order, is the difference between a filter you can guess at and one you
    have to learn."""
    assert "split(/\\s+/)" in FN
    assert "terms.every(" in FN, "a second term is ignored, or has to be adjacent"
    assert ".toLowerCase()" in FN, "the filter is case-sensitive"


def test_it_says_how_many_it_is_hiding():
    assert "hits.length+' of '+allFiles.length" in FN


def test_no_match_says_so_rather_than_going_blank():
    """An empty column is indistinguishable from a column that failed to load —
    the same confusion loadFiles() already had to fix once for the error path."""
    assert "No file on this host matches that." in FN


def test_escape_clears_the_box():
    assert "e.key==='Escape'" in BUILDER and "inp.value=''" in BUILDER


def test_the_box_stays_put_while_the_list_scrolls():
    """A filter that scrolls away with 900 rows is a filter you have to go back for."""
    css = UI[UI.index(".colsearch{"):UI.index(".colsearch input{")]
    assert "position:sticky" in css and "top:0" in css


def test_the_selected_file_stays_selected_through_a_filter():
    """Typing must not silently drop the highlight off the file being looked at —
    the versions and the content panes are still showing it."""
    assert "if(f.path===state.path)b.classList.add('sel')" in FN
