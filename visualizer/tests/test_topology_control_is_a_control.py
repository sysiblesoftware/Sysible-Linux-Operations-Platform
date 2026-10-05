"""Fleet Topology must look like something you press.

It is appended to the tab strip with margin-left:auto, so it lands at the far
right, away from the run of app tabs — and once the app tabs grew to their full
product names ("Sysible Linux Engineering Platform") it wraps onto a row of its
own. As a bare .tab it had no border, no background and no icon, so in both
positions it read as a stray word rather than a control.

Checked at the source, because this console has no build step and no JS test
runner: the CSS block and the element that gets the class.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import ui  # noqa: E402


def _topo_css() -> str:
    """Every declaration that applies to .tab.topo itself (not its children)."""
    out = []
    for m in re.finditer(r"\.tab\.topo(?P<rest>[^{,]*)\{(?P<body>[^}]*)\}", ui._CSS):
        if m.group("rest").strip() in ("", ":hover"):
            out.append(m.group("body"))
    return " ".join(out)


def test_it_is_drawn_as_a_button_not_bare_text():
    css = _topo_css()
    assert "border:" in css, "no border — it reads as text, not a control"
    assert "background:" in css, "no background — it reads as text, not a control"
    assert "border-radius:" in css


def test_it_carries_an_icon():
    assert ".tab.topo svg{" in ui._CSS, "no rule sizing an icon inside the control"
    assert "<svg" in ui._JS and "Fleet Topology" in ui._JS
    # The icon is built right where the control is, not somewhere unrelated.
    block = ui._JS[ui._JS.index("tab topo"):ui._JS.index("Fleet Topology")]
    assert "<svg" in block, "the control is created without its icon"


def test_the_icon_follows_the_selected_state():
    """A hard-coded stroke colour would vanish against the filled selected pill."""
    block = ui._JS[ui._JS.index("tab topo"):ui._JS.index("Fleet Topology")]
    assert 'stroke="currentColor"' in block
    assert re.search(r"\.tab\.topo\.sel\{[^}]*background:", ui._CSS), \
        "selected state is not filled, so the pill gives no pressed feedback"


def test_it_is_still_reachable_as_a_tab():
    """Styling it as a pill must not cost it the .tab behaviour select() drives."""
    assert "el('button','tab topo')" in ui._JS
    assert "t.dataset.key=TOPO" in ui._JS
