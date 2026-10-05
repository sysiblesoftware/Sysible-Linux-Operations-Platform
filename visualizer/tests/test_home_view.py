"""Visualizer opens on a chooser, not a tab strip with something bolted to the end.

Fleet Topology was a fifth .tab in the app row, pushed right with margin-left:auto.
It was reported as looking like an afterthought twice: once as bare text, and again
after I gave it a border and an icon — because the problem was never how the pill
was painted. "Activity for one more app" was the only shape on offer, and topology
is not that. It supersedes tests/test_topology_control_is_a_control.py, which
guarded the pill.

The three views are peers. Activity owns the app tabs; topology and logs have no
apps, so they do not appear in that row at all.
"""
import re
from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parents[1] / "backend" / "ui.py").read_text(encoding="utf-8")


def test_the_pill_is_gone_not_restyled():
    assert ".tab.topo" not in SRC, "Fleet Topology is a tab again"
    assert "margin-left:auto" not in SRC.split("_JS")[0].split(".tabs{")[-1][:400], \
        "something is still being shoved to the end of the tab row"


def test_the_home_view_offers_every_view():
    block = SRC[SRC.index("function showHome"):SRC.index("async function boot")]
    for name in ("Activity", "Fleet Topology", "Logs"):
        assert f"'{name}'" in block, f"{name} is not offered on the home view"
    assert SRC.count("class=home-grid") == 1


def test_the_app_tabs_belong_to_activity():
    """They are Activity's tabs. Showing them beside a topology map says the map is
    one app's view of the world, which is the confusion this replaced."""
    sel = SRC[SRC.index("function select(key)"):SRC.index("async function load()")]
    assert "$('#tabs').hidden=isTopo" in sel, "the app tabs stay up during topology"
    home = SRC[SRC.index("function showHome"):SRC.index("async function boot")]
    assert "$('#tabs').hidden=true" in home, "the app tabs are up on the home view"


def test_there_is_a_way_back():
    assert "id=home-btn" in SRC and "All views" in SRC
    assert "$('#home-btn').onclick=showHome" in SRC


def test_every_view_is_hidden_on_the_home_view():
    """A pane left behind would render underneath the chooser."""
    home = SRC[SRC.index("function showHome"):SRC.index("async function boot")]
    for pane in ("#bar-activity", "#bar-topo", "#body", "#log", "#topo"):
        assert f"$('{pane}').hidden=true" in home, f"{pane} is still showing on the home view"
    assert "topoAuto(false)" in home, "topology keeps polling from the home view"
