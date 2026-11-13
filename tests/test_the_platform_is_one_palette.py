"""Five surfaces, one palette.

Part of the UI/UX audit. The portal, Administration, the Controller, SLEP and
Connect each carried their own copy of the Sysible colours, and the copies had
drifted into two families:

    --panel   #12161f  (Controller, SLEP)   vs  #131923  (Connect, portal, IdP)
    --text    #e9f0f7  (Controller, SLEP)   vs  #e6edf5  (Connect, portal, IdP)
    --muted   #9aa7bd  (Controller, SLEP)   vs  #93a1b5  (Connect, portal, IdP)
    --green   #43a047  (Controller, SLEP)   vs  #4caf5a  (Connect, portal)
    --accent  #5580ee  (Controller: blue)   vs  #43a047  vs  #46a04a (Connect)

None of that was decided. It is five copies of one idea aging apart, and what
it cost was the thing a platform is for: an operator moving from the portal
into a console saw the ground shift under them for no reason they could name.

The builds cannot share a file -- the Controller is its own repository, the
portal is static CSS and the IdP inlines its stylesheet into Python -- so each
surface keeps its copy and this test pins every copy to portal/brand/tokens.json.
Same shape as test_module_sections_name_real_services.py, for the same reason:
files that cannot import each other, one fact between them.

CONTRAST IS PART OF THE CONTRACT. Each value is recorded with what it measures,
and the ratios are recomputed here rather than trusted -- a palette whose notes
say "5.73:1" and whose colours say otherwise is worse than no notes. The rule
that came out of measuring: text on a coloured fill is DARK. White fails AA on
every fill this platform has (green 3.30:1, red 3.70:1, amber 2.14:1), and
three of the five surfaces were using it.

Run: pytest tests/
"""
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = json.loads((REPO / "portal" / "brand" / "tokens.json").read_text(encoding="utf-8"))
TOKENS = {k: v["value"] for k, v in SPEC["tokens"].items()}
ALIASES = SPEC["aliases"]
SURFACES = SPEC["surfaces"]
DARK_SELECTOR = SPEC["selectors"]


def _block(text: str, selector: str) -> str:
    """The body of the first rule with this selector."""
    i = text.find(selector)
    assert i >= 0, f"no {selector} block"
    return text[text.index("{", i) + 1: text.index("}", i)]


def _declared(surface: str) -> dict:
    text = (REPO / SURFACES[surface]).read_text(encoding="utf-8")
    body = _block(text, DARK_SELECTOR.get(surface, ":root"))
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body)}


def _name(surface: str, token: str) -> str:
    return ALIASES.get(surface, {}).get(token, token)


# ---- contrast: the notes have to be true -----------------------------------
def _lum(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    def chan(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(h[i:i + 2]) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_body_text_is_readable_on_the_ground():
    assert contrast(TOKENS["--text"], TOKENS["--bg"]) >= 7.0


def test_secondary_text_is_readable_on_the_ground():
    """--muted is most of the writing on these screens: every hint, every unit,
    every timestamp. 4.5:1 is the floor, and it was chosen from the two values
    in use by taking the readable one."""
    assert contrast(TOKENS["--muted"], TOKENS["--bg"]) >= 4.5


@pytest.mark.parametrize("fill", ["--accent", "--red", "--amber"])
def test_text_on_a_coloured_fill_clears_aa(fill):
    """The rule that white does not satisfy on any of them."""
    r = contrast(TOKENS["--on-accent"], TOKENS[fill])
    assert r >= 4.5, f"{TOKENS['--on-accent']} on {TOKENS[fill]} is {r:.2f}:1"


def test_white_on_a_fill_is_still_the_wrong_answer():
    """If a fill is ever lightened enough that white works, this test says so
    and the rule above can be revisited. Until then it documents WHY the ink is
    dark, so the next person does not 'fix' it back to white."""
    bad = [t for t in ("--accent", "--red", "--amber")
           if contrast("#ffffff", TOKENS[t]) >= 4.5]
    assert not bad, f"white now clears AA on {bad}; the dark-ink rule can be reconsidered"


@pytest.mark.parametrize("token", sorted(TOKENS))
def test_the_note_records_a_real_measurement(token):
    """Every ratio written in a note is recomputed, so the file cannot drift
    into describing colours it no longer holds."""
    note = SPEC["tokens"][token]["note"]
    for claimed, against in re.findall(r"([\d.]+):1 on (--[a-z-]+)", note):
        actual = contrast(TOKENS[token], TOKENS[against])
        assert abs(actual - float(claimed)) < 0.05, (
            f"{token} claims {claimed}:1 on {against}; it measures {actual:.2f}:1")


# ---- every surface carries the same values ---------------------------------
@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_the_surface_exists_and_declares_tokens(surface):
    d = _declared(surface)
    assert len(d) >= 6, f"{surface}: only {len(d)} tokens found — the parser has slipped"


@pytest.mark.parametrize("surface,token",
                         [(s, t) for s in sorted(SURFACES) for t in sorted(TOKENS)])
def test_every_surface_agrees_with_the_palette(surface, token):
    declared = _declared(surface)
    name = _name(surface, token)
    if name not in declared:
        pytest.skip(f"{surface} does not use {name}")
    got = declared[name].split("/*")[0].strip()
    assert got.lower() == TOKENS[token].lower(), (
        f"{SURFACES[surface]} sets {name} to {got}, the palette says "
        f"{TOKENS[token]}. Change portal/brand/tokens.json if the palette is "
        f"what should move.")


def test_the_accent_is_the_same_hue_everywhere():
    """The one an operator actually notices. The Controller rendered blue while
    the portal, Administration, SLEP and Connect rendered green."""
    seen = {}
    for s in SURFACES:
        d = _declared(s)
        if "--accent" in d:
            seen[s] = d["--accent"].split("/*")[0].strip().lower()
    assert len(set(seen.values())) == 1, f"the accent differs by surface: {seen}"


def test_no_surface_is_missing_from_the_palette_file():
    """A sixth console added without a line here is a sixth palette."""
    sheets = set()
    for pat in ("apps/*/webgui/frontend/src/styles.css", "portal/style.css"):
        sheets |= {str(p.relative_to(REPO)) for p in REPO.glob(pat)}
    missing = sheets - set(SURFACES.values())
    assert not missing, f"stylesheets nothing pins: {sorted(missing)}"
