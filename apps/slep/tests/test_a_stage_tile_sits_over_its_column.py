"""The run visualizer's stage tiles must line up with the columns they label.

Reported with a screenshot of a five-stage run: the breadcrumb strip at the top
(Terraform·destroy → Terraform·apply → Inventory → Ansible → Enroll) was packed to
the left at content width, while the stage columns underneath are flex:1 1 0 with
min-width:210px. By the third stage the tile was visibly left of its column, and
the Enroll tile sat over the Ansible column.

Measured rather than asserted: the real stylesheet is rendered in Chromium and the
centre of each tile compared with the centre of its column. Before the fix the
worst offset was 1299px; it must now be under a pixel, at every stage count and
every pane width -- including narrow ones, where both rows scroll and a fix that
only works unscrolled would drift apart.
"""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
CSS = HERE / "webgui" / "frontend" / "src" / "styles.css"
VIEWS = HERE / "webgui" / "frontend" / "src" / "views"


# ------------------------------------------------- structure (always checked)

def test_the_two_rows_scroll_as_one():
    """Separate scrollers drift apart the moment the pane is too narrow."""
    runs = (VIEWS / "Runs.jsx").read_text(encoding="utf-8")
    assert "stage-sync" in runs, "the tiles and the columns are not in a shared scroller"
    css = CSS.read_text(encoding="utf-8")
    assert ".stage-sync{overflow-x:auto}" in css
    assert ".stage-sync > .stage-row{overflow-x:visible}" in css, (
        "the column row still owns its own horizontal scroll, so it scrolls "
        "independently of the tiles above it")


def test_the_tiles_are_laid_out_on_the_columns_tracks():
    css = CSS.read_text(encoding="utf-8")
    assert ".pipe-flow.aligned" in css, "there is no aligned mode"
    # A column is a 210px track with 12px padding each side; a tile must be the
    # same track: 186 + 12 + 12. If either number moves, they stop lining up.
    assert "min-width:186px;margin:0 12px" in css, (
        "the tile track no longer matches the column track (210px = 186 + 12 + 12)")
    assert "min-width:210px" in css, "the column track changed"
    assert "padding:8px 0" in css, (
        "the aligned strip still has horizontal padding, which shifts every tile "
        "relative to its column")


def test_the_arrows_do_not_eat_track_width():
    """As flex siblings the → arrows take width from the row and push every tile
    off its column. In aligned mode they are drawn in the gutter instead."""
    viz = (VIEWS / "RunViz.jsx").read_text(encoding="utf-8")
    assert "!aligned && i < seq.length - 1" in viz, (
        "the arrow is still rendered as a flex sibling in aligned mode")
    assert ".pipe-flow.aligned > .pipe-stage + .pipe-stage::before" in CSS.read_text(encoding="utf-8")


def test_a_maximized_stage_is_not_aligned_to_anything():
    """Maximized, there is one column; the strip goes back to its normal look."""
    runs = (VIEWS / "Runs.jsx").read_text(encoding="utf-8")
    assert "aligned={!maxed}" in runs


# ------------------------------------------------ measured in a real browser

MEASURE_JS = r"""
const { chromium } = require('playwright');
const fs = require('fs');
const css = fs.readFileSync(process.argv[2], 'utf8');
const tile = (i) => `<button class="pipe-stage"><span class="pipe-ico">⬢</span>
  <span class="pipe-body"><b>Stage${i}</b><span class="faint mono">target-${i}</span></span>
  <span class="pipe-dot"></span></button>`;
const blk = (i) => `<div class="stage-block"><div class="stage-block-h"><b>s${i}</b></div>
  <div class="stage-block-body">b${i}</div></div>`;
const page = (n, w, aligned) => `<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0}
:root{--line:#333;--panel:#1a1a1a;--panel2:#222;--text:#eee;--faint:#888;--accent:#4af;--muted:#999}
${css}
.wrap{width:${w}px}</style></head><body><div class="wrap"><div class="stage-sync">
<div class="pipe-flow${aligned ? ' aligned' : ''}">${Array.from({length:n},(_,i)=>
  tile(i) + (!aligned && i < n-1 ? '<span class="pipe-arrow">→</span>' : '')).join('')}</div>
<div class="stage-row">${Array.from({length:n},(_,i)=>blk(i)).join('')}</div>
</div></div></body></html>`;
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1600, height: 900 } });
  const out = [];
  for (const n of [3,4,5,7]) for (const w of [1400,1000,760,520]) {
    await p.setContent(page(n, w, process.argv[3] === 'aligned'));
    const r = await p.evaluate(() => {
      const c = (el) => { const b = el.getBoundingClientRect(); return b.left + b.width/2; };
      return { t: [...document.querySelectorAll('.pipe-stage')].map(c),
               k: [...document.querySelectorAll('.stage-block')].map(c) };
    });
    out.push({ n, w, worst: Math.max(...r.t.map((x,i) => Math.abs(x - r.k[i]))) });
  }
  await b.close();
  console.log(JSON.stringify(out));
})();
"""


def _measure(mode, tmp_path):
    js = tmp_path / "m.js"
    js.write_text(MEASURE_JS)
    p = subprocess.run(["node", str(js), str(CSS), mode],
                       capture_output=True, text=True, timeout=180, cwd="/")
    if p.returncode != 0:
        pytest.skip("could not run the browser measurement: %s" % (p.stderr or "")[-300:])
    return json.loads(p.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_every_tile_sits_over_its_column_at_every_width(tmp_path):
    for row in _measure("aligned", tmp_path):
        assert row["worst"] <= 1.0, (
            "%d stages at %dpx: a tile is %.1fpx off its column"
            % (row["n"], row["w"], row["worst"]))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_measurement_would_have_caught_the_reported_bug(tmp_path):
    """Without the aligned layout the same harness must FAIL -- otherwise it is not
    measuring anything and would pass against the broken version too."""
    worst = max(r["worst"] for r in _measure("unaligned", tmp_path))
    assert worst > 50, (
        "the harness reports the OLD layout as aligned (worst %.1fpx), so it is not "
        "actually measuring the thing that was reported" % worst)
