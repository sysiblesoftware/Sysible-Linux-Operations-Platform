"""The four columns are the operator's to size.

The fixed grid picked one answer for everyone: 250px of files column, which wrapped
"/etc/alternatives/builtins.7.gz" onto two lines, beside a content pane that held
the rest of the screen and stayed empty until a version was picked. Which column
needs the room depends entirely on what is being looked at.
"""
import re
from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parents[1] / "backend" / "ui.py").read_text(encoding="utf-8")


def test_the_columns_are_variables_not_fixed_pixels():
    m = re.search(r"\.wrap\{[^}]*grid-template-columns:([^;]+);", SRC)
    assert m, ".wrap no longer defines its columns"
    cols = m.group(1)
    for v in ("--c1", "--c2", "--c3"):
        assert v in cols, f"{v} is not a column variable, so it cannot be dragged"


def test_there_is_a_gutter_between_every_pair_of_columns():
    assert SRC.count("class=gutter") == 3, "a column pair has no splitter"
    # Each one has to say WHICH column it sizes, or they all drag the same one.
    assert sorted(re.findall(r"data-resize=(\d)", SRC)) == ["1", "2", "3"]


def test_a_gutter_is_reachable_without_a_mouse():
    """A splitter that only answers to a pointer is one half the operators here
    cannot use at all."""
    for bit in ("role=separator", "aria-orientation=vertical", "tabindex=0"):
        assert bit in SRC, f"the gutters are missing {bit}"
    assert "aria-label='Resize" in SRC
    js = SRC[SRC.index("function colsInit"):]
    assert "'ArrowLeft'" in js and "'ArrowRight'" in js, "arrow keys do not resize"


def test_a_width_is_clamped_and_remembered():
    js = SRC[SRC.index("const COLS ="):]
    assert "COL_MIN" in js and "COL_MAX" in js, "a column can be dragged to nothing"
    assert "localStorage" in js, "the size is forgotten on every reload"
    assert "Math.max(COL_MIN, Math.min(COL_MAX" in js


def test_storage_failures_do_not_break_the_drag():
    """A private window, or blocked site data, throws on localStorage. A splitter
    that dies there is worse than one that forgets."""
    js = SRC[SRC.index("function colsLoad"):SRC.index("function colWidth")]
    assert js.count("catch") >= 2, "a throwing localStorage is unhandled"


def test_the_phone_layout_drops_the_splitters():
    """One column stacked vertically has nothing to split."""
    m = re.search(r"@media\(max-width:900px\)\{([^@]*)\}", SRC)
    assert m and ".gutter{display:none}" in m.group(1).replace(" ", "")
