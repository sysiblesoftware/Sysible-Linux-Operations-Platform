"""A service card has to have room for the words on it.

Reported as "this is so clunky", with a screenshot of the services grid:
titles clipped mid-word to "C(" and "Er Platform", descriptions set one word
per line down a 69px column, cards 250px tall to hold two lines of text.

The cause was one CSS track. `.mod` was `minmax(0,1fr) auto`, with the four
recovery buttons in the `auto` column -- and `auto` means max-content, which
for four buttons is ~250px. The cards are ~330px wide inside, so the buttons
took 250 of it and `minmax(0,1fr)` let the text shrink into the 69 that were
left. There WAS a stacking rule, at `max-width:640px`, but that is the
VIEWPORT's width and the cards are 280-350px at every viewport above it, so
the broken layout was the one every desktop got.

Four buttons do not fit beside a title in a 330px card at any window size.

Measured in a real browser against the SHIPPING stylesheet, because what is
asserted is how wide the words are, and no reading of the CSS would have
caught this one -- it looked right.

Run: pytest tests/
"""
import json
import os
import pathlib
import re
import tempfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
IDP = (REPO / "idp" / "app.py").read_text(encoding="utf-8")
CHROMIUM = os.environ.get("CHROMIUM_BIN", "/opt/pw-browsers/chromium")

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                   # pragma: no cover
    sync_playwright = None

pytestmark = [
    pytest.mark.skipif(sync_playwright is None, reason="playwright is not installed"),
    pytest.mark.skipif(not pathlib.Path(CHROMIUM).is_file(), reason="no chromium"),
]

# The real services, with the real copy. A short label would hide the defect.
MODS = [
    ("Controller", "The console, its API, and the agent endpoint every managed host reports to"),
    ("Engineering Platform", "Ansible and Terraform: playbooks, inventories, and every run's output"),
    ("Connect", "The browser terminal and file workspace, and its SSH sessions to the fleet"),
    ("Gateway", "Caddy: the platform's single front door, TLS, and every app's route"),
    ("Sign-in &amp; accounts", "The identity provider every app trusts"),
    ("Sysible Flashback", "Backup and restore of every config file on every host"),
    ("Sysible Baseline", "Desired-state configuration, applied with Salt"),
    ("Sysible Visualizer", "Activity and logs from every app in one place"),
    ("Updater", "Pulls each checkout and rebuilds its containers"),
]


def _css() -> str:
    m = re.search(r'^_CSS = """(.*?)"""', IDP, re.S | re.M)
    assert m, "the stylesheet is no longer a module-level _CSS string"
    return m.group(1)


def _card(name, detail):
    acts = "".join(
        f'<button class="btn {"danger" if a == "stop" else "ghost"} sm">'
        f'{a.capitalize()}</button>'
        for a in ("restart", "stop", "start", "recreate"))
    return (f'<div class=mod><div class=mod-name>{name}</div>'
            f'<div class=mod-detail>{detail}</div>'
            f'<div class=mod-state><span class=ok>Running</span> Up 19 hours'
            f' (healthy)</div><div class=mod-act>{acts}</div></div>')


def _measure(width):
    """The grid, managing (every button on screen), at this viewport width."""
    html = ("<!doctype html><html data-theme=dark><head><meta charset=utf-8>"
            f"<style>{_css()}</style></head><body><div class=wrap>"
            "<div class='prod-mods managing' id=g>"
            + "".join(_card(n, d) for n, d in MODS) + "</div></div></body></html>")
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "i.html").write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM)
        pg = b.new_page(viewport={"width": width, "height": 1000})
        pg.goto(f"file://{d}/i.html", wait_until="load")
        pg.wait_for_timeout(150)
        out = pg.evaluate("""() => [...document.querySelectorAll('.mod')].map(m => {
          const n = m.querySelector('.mod-name');
          const de = m.querySelector('.mod-detail');
          const cs = getComputedStyle(m);
          const inner = m.clientWidth
            - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
          return {name: n.textContent, inner: Math.round(inner),
                  nameW: Math.round(n.clientWidth),
                  nameScroll: Math.round(n.scrollWidth),
                  detailW: Math.round(de.clientWidth),
                  detailH: Math.round(de.offsetHeight),
                  lineH: Math.round(parseFloat(getComputedStyle(de).lineHeight)),
                  h: Math.round(m.offsetHeight)};
        })""")
        b.close()
    return out


@pytest.fixture(scope="module")
def wide():
    return _measure(1440)


@pytest.fixture(scope="module")
def narrow():
    return _measure(900)


@pytest.mark.parametrize("at", ["wide", "narrow"])
def test_no_title_is_clipped(at, request):
    for c in request.getfixturevalue(at):
        assert c["nameScroll"] <= c["nameW"] + 1, (
            f"{c['name']!r} is cut off at {at}: needs {c['nameScroll']}px, "
            f"has {c['nameW']}px")


@pytest.mark.parametrize("at", ["wide", "narrow"])
def test_the_words_get_most_of_the_card(at, request):
    """The property, not a pixel count: whatever sits beside the text must not
    be allowed to take the card. 69px of 330 was 21%."""
    for c in request.getfixturevalue(at):
        share = c["detailW"] / c["inner"]
        assert share >= 0.7, (
            f"{c['name']!r} gets {share:.0%} of its card for text at {at} "
            f"({c['detailW']}px of {c['inner']}px)")


@pytest.mark.parametrize("at", ["wide", "narrow"])
def test_a_one_line_description_does_not_take_six(at, request):
    """One word per line is what 69px does to a sentence. Three lines of
    description is a card; nine is the bug."""
    for c in request.getfixturevalue(at):
        lines = round(c["detailH"] / max(c["lineH"], 1))
        assert lines <= 4, (
            f"{c['name']!r} wraps its description to {lines} lines at {at}")


def test_the_cards_are_not_wildly_uneven(wide):
    """They were 193 to 263px for the same two lines, because each one's
    wrapping depended on how much the buttons happened to leave it."""
    hs = [c["h"] for c in wide]
    assert max(hs) - min(hs) <= 40, f"card heights range {min(hs)}-{max(hs)}px"


def test_the_buttons_are_not_in_a_track_that_can_eat_the_row():
    """The specific cause, so it cannot come back as a 'tidy' two-column
    layout. `auto` is max-content: it takes what the buttons want and leaves
    the text whatever is over."""
    css = _css()
    m = re.search(r"\.mod\{[^}]*grid-template-columns:([^;}]*)", css)
    assert m, ".mod is no longer a grid; re-check what this test is pinning"
    cols = m.group(1).strip()
    assert "auto" not in cols, (
        f".mod has an auto track again ({cols}) -- four buttons measure ~250px "
        f"and the cards are ~330px wide")
