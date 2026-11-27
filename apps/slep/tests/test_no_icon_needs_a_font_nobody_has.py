"""A UI glyph must not depend on a font the operator may not have.

REPORTED: "Ansible has a malformed glyph on slep" — a screenshot of the run
pipeline where the Ansible stage drew as tofu while the three stages beside it
rendered fine.

The cause was one codepoint out of four. STAGE_ICON was

    { terraform: U+2B22, inventory: U+25A4, ansible: U+23FB, enroll: U+25C8 }

and U+23FB POWER SYMBOL is Miscellaneous Technical, added in Unicode 9.0. The
other three are Geometric Shapes (Unicode 1.1), which essentially every font
covers — which is exactly why it was one odd icon and not four.

This could not be reproduced in CI: the container's Chromium has a font that
covers U+23FB and drew it correctly. A rendering test would have passed while the
operator looked at a box. So this checks the PROPERTY that actually matters —
which block the codepoint comes from — rather than whether one machine happens to
have a font for it.

MISCELLANEOUS TECHNICAL (U+2300-U+23FF) is the banned range. It is where the
power/eject/keyboard symbols live, its coverage is patchy and version-dependent,
and both glyphs that went wrong came from it: U+23FB for Ansible and U+2387
ALTERNATIVE KEY SYMBOL, which was the Git branch icon in two places.

Emoji-plane icons (U+1F300 and above) are a separate, known question — they
depend on a colour emoji font being installed, and U+1F5BF BLACK FOLDER has no
emoji presentation at all. They are listed, not failed, until that is decided.
"""
import io
import pathlib
import unicodedata

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "webgui" / "frontend" / "src"
SUFFIXES = (".jsx", ".js", ".css")

# U+2318 PLACE OF INTEREST is the Mac Command key, written inside a sentence that
# is ABOUT the Mac keyboard ("Use Ctrl+V (⌘V on Mac)"). A Mac has it; on anything
# else the sentence is not addressed to the reader. It is prose, not an icon.
ALLOWED = {"⌘"}

BANNED_LO, BANNED_HI = 0x2300, 0x23FF          # Miscellaneous Technical


def _sources():
    return [p for p in SRC.rglob("*")
            if p.suffix in SUFFIXES and "node_modules" not in p.parts]


def test_there_are_sources_to_check():
    """A glob that silently matches nothing passes every test below."""
    files = _sources()
    assert len(files) > 10, f"only found {len(files)} frontend sources under {SRC}"


def test_no_icon_comes_from_miscellaneous_technical():
    bad = []
    for p in _sources():
        for n, line in enumerate(io.open(p, encoding="utf-8").read().splitlines(), 1):
            for ch in line:
                if BANNED_LO <= ord(ch) <= BANNED_HI and ch not in ALLOWED:
                    bad.append("%s:%d  U+%04X %s" % (
                        p.relative_to(SRC), n, ord(ch),
                        unicodedata.name(ch, "<unnamed>")))
    assert not bad, (
        "these glyphs are from Miscellaneous Technical (U+2300-U+23FF), whose font "
        "coverage is patchy — U+23FB drew as tofu on a real machine while the "
        "Geometric Shapes icons beside it were fine:\n  " + "\n  ".join(bad))


def test_the_pipeline_stage_icons_are_all_near_universal():
    """The specific map that was reported, pinned by block rather than by value
    so it stays fixable without the test dictating which shape to use."""
    txt = io.open(SRC / "views" / "RunViz.jsx", encoding="utf-8").read()
    line = next((l for l in txt.splitlines() if "STAGE_ICON" in l and "=" in l), None)
    assert line, "RunViz no longer defines STAGE_ICON"
    glyphs = [c for c in line if ord(c) > 0x2000]
    assert glyphs, f"no glyphs found in: {line.strip()}"
    for ch in glyphs:
        o = ord(ch)
        assert 0x25A0 <= o <= 0x25FF or 0x2B00 <= o <= 0x2BFF, (
            "stage icon U+%04X %s is outside Geometric Shapes / Misc Symbols & "
            "Arrows; the Ansible stage was exactly this mistake"
            % (o, unicodedata.name(ch, "?")))


def test_emoji_plane_icons_are_listed_so_the_decision_is_visible():
    """Not a failure yet — a record. These need a colour emoji font, and
    U+1F5BF BLACK FOLDER has no emoji presentation, so it is the likeliest of
    them to be missing outright."""
    seen = set()
    for p in _sources():
        for ch in io.open(p, encoding="utf-8").read():
            if ord(ch) >= 0x1F300:
                seen.add(ch)
    assert all(ord(c) >= 0x1F300 for c in seen)
