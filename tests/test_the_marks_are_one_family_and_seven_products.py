"""Seven tiles on one portal: same family, seven different glyphs.

Reported: "Baseline's icon looks too similar to visualizer." It was — both were
small upright rounded bars with a brand-green stroke across them, and the portal
renders these at 40px, where that is the whole picture. Two tiles side by side
that an operator has to read the LABEL to tell apart are two tiles that do not
work.

What can be checked mechanically is not "do these look different" — that needed
rendering them and looking, which is how the problem was found in the first
place. What can be checked is the two ways the set silently rots:

  * a mark that leaves the family (tile gradient, green ring) stops matching
    the other six;
  * an app's favicon and its portal tile drift apart, so the tab shows one
    glyph and the portal another. They are byte copies of each other, kept that
    way by hand.

Run: pytest tests/    (from the repo root)
"""
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
MARKS = sorted((REPO / "portal" / "marks").glob("*.svg"))
# Apps that serve their own copy of their portal tile as a favicon.
SELF_SERVING = ("baseline", "flashback", "visualizer")


def test_there_are_marks_to_check():
    assert len(MARKS) >= 7, f"only {len(MARKS)} marks found — this would pass vacuously"


@pytest.mark.parametrize("mark", MARKS, ids=lambda p: p.stem)
def test_every_mark_wears_the_family_tile(mark):
    svg = mark.read_text(encoding="utf-8")
    assert 'stop-color="#161d29"' in svg and 'stop-color="#0a0d14"' in svg, \
        f"{mark.name} does not use the family tile gradient"
    ring = re.search(r'<rect[^>]*rx="26\.5"[^>]*>', svg)
    assert ring, f"{mark.name} has no family ring rect"
    assert 'stroke="#6ddb73"' in ring.group(0), f"{mark.name}'s ring is not brand green"
    assert 'stroke-width="2"' in ring.group(0), f"{mark.name}'s ring is not 2px"


@pytest.mark.parametrize("mark", MARKS, ids=lambda p: p.stem)
def test_a_mark_is_drawn_on_the_same_canvas(mark):
    """128x128 on a 0 0 128 128 viewBox, every one of them.

    The portal asks for these at width=40 height=40. A mark authored on a
    different box is then scaled by a different factor than its neighbours, so
    its strokes land at a different weight on the same row of tiles — which
    reads as "that one looks wrong" with nothing in the file to point at.

    (This replaced a check that tried to prove no COORDINATE fell outside the
    box. It cannot be done with a regex: admin.svg's shield is a path with
    relative curve deltas, and `c0 18-12 28-30 34` is full of negative numbers
    that are not coordinates at all. It failed that mark, which is drawn
    correctly. A test that is wrong about a correct file is worse than no test,
    and what it was reaching for is caught by rendering the set and looking —
    which is how the Visualizer collision was found.)
    """
    svg = mark.read_text(encoding="utf-8")
    tag = svg[svg.index("<svg"):svg.index(">", svg.index("<svg"))]
    assert 'viewBox="0 0 128 128"' in tag, f"{mark.name} is not on the 128 canvas: {tag}"
    assert 'width="128"' in tag and 'height="128"' in tag, \
        f"{mark.name} does not declare 128x128: {tag}"


@pytest.mark.parametrize("app", SELF_SERVING)
def test_an_apps_favicon_is_its_portal_tile(app):
    """They are the same drawing maintained in two files. When they drift the
    browser tab and the portal show different glyphs for the same product —
    and nothing anywhere complains."""
    ui = (REPO / app / "backend" / "ui.py").read_text(encoding="utf-8")
    # Two spellings in the tree: one ends `</svg>\n"""`, the others `</svg>"""`.
    # Matching only the first made this test pass for one app and error for the
    # other two while all three were in fact fine.
    m = re.search(r'FAVICON_SVG = """\\\n(.*?)"""', ui, re.S)
    assert m, f"{app}/backend/ui.py has no FAVICON_SVG in the expected form"
    mark = (REPO / "portal" / "marks" / f"{app}.svg").read_text(encoding="utf-8")
    assert m.group(1).strip() == mark.strip(), (
        f"{app}'s favicon and portal/marks/{app}.svg have drifted — the tab and "
        f"the portal now show different glyphs for the same product")


def test_no_two_marks_are_the_same_drawing():
    """The cheap half of the real question. It cannot tell you that two glyphs
    LOOK alike — only rendering them does — but it does catch a mark copied
    from its neighbour and left unedited."""
    seen = {}
    for m in MARKS:
        body = m.read_text(encoding="utf-8")
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
        body = re.sub(r'\bid="[^"]*"|url\(#[^)]*\)|aria-label="[^"]*"', "", body)
        glyph = re.sub(r"\s+", " ", body[body.index("</defs>"):]).strip()
        assert glyph not in seen, f"{m.stem} and {seen[glyph]} are the same drawing"
        seen[glyph] = m.stem
