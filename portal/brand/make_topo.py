#!/usr/bin/env python3
"""Generate the platform's shared background: the topographic contours.

ONE FIELD, EVERYWHERE. The same contours appear on sysible.com's hero, on the
Sysible Workstation wallpapers, and — via the SVGs this writes — behind every
SLOP surface. They are not three similar drawings: the height field is
reproduced exactly here, from the site's own linear congruential generator with
the same seed, the same fourteen gaussian bumps and the same regional slope, so
the platform and the product it manages are visibly the same thing.

Output is SVG rather than a raster because these sit behind live UI at every
window size: a vector scales without resampling, stays a few tens of KB, and
costs one request that caches forever.

    python3 portal/brand/make_topo.py

Writes topo-dark.svg and topo-light.svg next to this file. Committed, so a
normal build needs neither numpy nor this script.
"""
from __future__ import annotations

import os

import numpy as np

# ---- the site's field (index.html, drawTopo) -------------------------------
SEED = 7
BUMPS = 14
LEVELS = 24
ACCENTS = (8, 15)          # the two levels picked out in green

# The rendered viewBox. 16:10 covers the usual desktop shapes under
# background-size: cover without stretching anything recognisable.
W, H = 1600, 1000
# Sampling grid. The site uses 132 across; a little finer keeps the curves
# smooth at 2560px wide without turning the file into a megabyte.
GW, GH = 190, 120


def _bumps():
    rs = SEED

    def rnd():
        nonlocal rs
        rs = (1103515245 * rs + 12345) & 0x7FFFFFFF
        return rs / 0x7FFFFFFF

    return [(rnd() * 1.1 - 0.05, rnd() * 1.1 - 0.05, rnd() * 2 - 1, 0.08 + rnd() * 0.22)
            for _ in range(BUMPS)]


def _field(gw: int, gh: int) -> np.ndarray:
    gx = np.linspace(0.0, 1.0, gw, dtype=np.float32)[None, :]
    gy = np.linspace(0.0, 1.0, gh, dtype=np.float32)[:, None]
    f = gx * 0.6 + gy * 0.3
    for bx, by, amp, sig in _bumps():
        d2 = ((gx - bx) ** 2 + (gy - by) ** 2) / (2.0 * sig * sig)
        f = f + amp * np.exp(-d2)
    return f.astype(np.float32)


def _segments(F: np.ndarray, lvl: float, sx: float, sy: float) -> np.ndarray:
    """Marching squares for one level -> (N,4) of x0,y0,x1,y1.

    Edge order is the site's (top, right, bottom, left), so the same cells
    connect the same way and a four-crossing cell emits two segments exactly as
    the canvas version does."""
    tl, tr = F[:-1, :-1], F[:-1, 1:]
    bl, br = F[1:, :-1], F[1:, 1:]
    m = np.stack([(tl > lvl) != (tr > lvl), (tr > lvl) != (br > lvl),
                  (br > lvl) != (bl > lvl), (bl > lvl) != (tl > lvl)], axis=-1)
    n = m.sum(axis=-1)
    if not n.any():
        return np.empty((0, 4), dtype=np.float32)
    j, i = np.meshgrid(np.arange(F.shape[0] - 1, dtype=np.float32),
                       np.arange(F.shape[1] - 1, dtype=np.float32), indexing="ij")
    with np.errstate(divide="ignore", invalid="ignore"):
        px = np.stack([(i + (lvl - tl) / (tr - tl)) * sx, (i + 1) * sx,
                       (i + 1 - (lvl - br) / (bl - br)) * sx, i * sx], axis=-1)
        py = np.stack([j * sy, (j + (lvl - tr) / (br - tr)) * sy,
                       (j + 1) * sy, (j + 1 - (lvl - bl) / (tl - bl)) * sy], axis=-1)
    px = np.nan_to_num(px, nan=0.0, posinf=0.0, neginf=0.0)
    py = np.nan_to_num(py, nan=0.0, posinf=0.0, neginf=0.0)
    order = np.argsort(~m, axis=-1, kind="stable")

    def pick(k):
        idx = order[..., k]
        return (np.take_along_axis(px, idx[..., None], axis=-1)[..., 0],
                np.take_along_axis(py, idx[..., None], axis=-1)[..., 0])

    x0, y0 = pick(0)
    x1, y1 = pick(1)
    x2, y2 = pick(2)
    x3, y3 = pick(3)
    two = n >= 2
    out = [np.stack([x0[two], y0[two], x1[two], y1[two]], axis=-1)]
    four = n == 4
    if four.any():
        out.append(np.stack([x2[four], y2[four], x3[four], y3[four]], axis=-1))
    return np.concatenate(out, axis=0).astype(np.float32)


def _path_d(segs: np.ndarray) -> str:
    """One `d` for a whole level. Integer coordinates: at this viewBox a
    half-pixel is far below what a background at 12% opacity can show, and it
    roughly halves the file."""
    parts = []
    for x0, y0, x1, y1 in segs:
        parts.append(f"M{x0:.0f} {y0:.0f}L{x1:.0f} {y1:.0f}")
    return "".join(parts)


def render(dark: bool) -> str:
    F = _field(GW, GH)
    lo, hi = float(F.min()), float(F.max())
    sx, sy = W / (GW - 1), H / (GH - 1)

    # Deliberately fainter than the website hero. There it is the whole picture;
    # here it sits behind dense operational UI, and anything stronger competes
    # with the content instead of grounding it.
    if dark:
        line, index_a, plain_a = "#5a82f0", 0.22, 0.10
        green, green_a = "#6ddb73", 0.26
    else:
        line, index_a, plain_a = "#3560d4", 0.20, 0.09
        green, green_a = "#2f9e44", 0.24

    body = []
    for k in range(LEVELS):
        lvl = lo + (hi - lo) * (k + 0.5) / LEVELS
        segs = _segments(F, lvl, sx, sy)
        if not len(segs):
            continue
        if k in ACCENTS:
            col, alpha, wid = green, green_a, 1.6
        elif k % 5 == 0:                      # the site's index contours
            col, alpha, wid = line, index_a, 1.5
        else:
            col, alpha, wid = line, plain_a, 1.0
        body.append(f'<path d="{_path_d(segs)}" stroke="{col}" '
                    f'stroke-opacity="{alpha}" stroke-width="{wid}" fill="none"/>')

    return ('<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid slice">'
            + "".join(body) + "</svg>")


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    for dark, name in ((True, "topo-dark.svg"), (False, "topo-light.svg")):
        path = os.path.join(here, name)
        svg = render(dark)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
        print(f"{name}: {len(svg) / 1024:.0f} KB")
