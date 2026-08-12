#!/usr/bin/env python3
"""Render the agentty square icon (the ">" chevron) as a seamless animated GIF.

Same slow, subtle bob + soft heartbeat as the wordmark, but on the 512-tile
chevron mark. Note: Discord *server* icons can't be animated (static PNG only) —
use this for the README/web/socials, and `agentty-icon.png` for the server icon.

Output: community/brand/agentty-icon.gif
"""

from __future__ import annotations

import math
from PIL import Image

# The ">" chevron glyph (maya 6×7 font).
CHEV = ["      ", "#  #  ", "## ## ", " ## ##", "## ## ", "#  #  ", "      "]
FW, FH = 6, 7

# ── layout ─────────────────────────────────────────────────────────────────
S = 256                             # tile size (plenty for an icon; small file)
U = 34                              # px per pixel-cell — bigger chevron
TILE_RADIUS = int(S * 0.22)
BOB_BAND = 40                       # px of vertical room reserved for the bob

# tight bounds of lit cells, to center the chevron
LIT = [(r, c) for r in range(FH) for c in range(FW) if CHEV[r][c] == '#']
MINC = min(c for _, c in LIT); MAXC = max(c for _, c in LIT)
MINR = min(r for r, _ in LIT); MAXR = max(r for r, _ in LIT)
GW = (MAXC - MINC + 1) * U
GH = (MAXR - MINR + 1) * U
OX = (S - GW) / 2 - MINC * U
OY = (S - GH) / 2 - MINR * U

# ── color ──────────────────────────────────────────────────────────────────
TILE = (18, 15, 24)                 # slightly deeper tile for contrast
C0 = (255, 110, 220)                # brighter magenta top-left
C1 = (150, 90, 255)                # cooler violet bottom-right
WHITE = (255, 255, 255)
GLOW = (255, 120, 225)              # halo color behind the chevron

# ── animation (cooler + more visible) ──────────────────────────────────────
FPS = 15
LOOP_MS = 4000
BOB_PERIOD = LOOP_MS
BOB_AMP_PX = 22                     # strong, obvious sway
SHEEN_PERIOD = LOOP_MS              # one specular sweep per loop
SHEEN_WIDTH = 65                    # px width of the moving highlight band
BREATHE = 0.18                      # deeper shrink/grow (±18%)
BREATHE_PERIOD = LOOP_MS            # one slow, full breath per loop


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded_mask(size, radius):
    from PIL import ImageDraw
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius, fill=255)
    return m


# Build the chevron coverage mask at a given vertical shift `dy` and uniform
# `scale` about the tile center — lets us bob AND "breathe" the mark so the
# motion is obvious at a glance.
def _chevron_mask(dy: float, scale: float):
    import numpy as np
    m = np.zeros((S, S), dtype=bool)
    cx, cy = S / 2.0, S / 2.0
    us = U * scale
    for r, c in LIT:
        # cell center in the unscaled layout, then scaled about tile center
        ux = OX + (c + 0.5) * U
        uy = OY + (r + 0.5) * U
        sx = cx + (ux - cx) * scale
        sy = cy + (uy - cy) * scale + dy
        x0 = int(round(sx - us / 2)); x1 = int(round(sx + us / 2))
        y0 = int(round(sy - us / 2)); y1 = int(round(sy + us / 2))
        m[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = True
    return m


def render_frame(t_ms: float, _cache={}):
    import numpy as np
    if "grid" not in _cache:
        yy, xx = np.mgrid[0:S, 0:S]
        _cache["xx"] = xx.astype(np.float32)
        _cache["yy"] = yy.astype(np.float32)
        _cache["grid"] = True
    xxf, yyf = _cache["xx"], _cache["yy"]

    phase = 2 * math.pi * t_ms / BOB_PERIOD
    dy = math.sin(phase) * BOB_AMP_PX
    # Breathe on its own slow cycle (one full shrink+grow per loop), so it reads
    # as a long, deliberate breath rather than a quick bounce.
    bphase = 2 * math.pi * t_ms / BREATHE_PERIOD
    scale = 1.0 - BREATHE + BREATHE * (0.5 - 0.5 * math.cos(bphase)) * 2
    mask = _chevron_mask(dy, scale)

    img = np.empty((S, S, 3), dtype=np.float32)
    img[:] = TILE

    # 1) soft radial GLOW halo behind the chevron, bobbing with it.
    cx, cy = S / 2, S / 2 + dy
    dist = np.sqrt((xxf - cx) ** 2 + (yyf - cy) ** 2)
    halo = np.clip(1.0 - dist / (S * 0.42), 0.0, 1.0) ** 2.2
    for k in range(3):
        img[:, :, k] += (GLOW[k] - img[:, :, k]) * halo * 0.28

    # 2) gradient fill on the chevron (top-left bright -> bottom-right cool).
    grad = np.clip((xxf + yyf) / (2 * S), 0.0, 1.0)
    fill = np.empty((S, S, 3), dtype=np.float32)
    for k in range(3):
        fill[:, :, k] = C0[k] + (C1[k] - C0[k]) * grad

    # 3) specular SHEEN: a diagonal white band sweeping left->right each loop.
    sweep = (t_ms % SHEEN_PERIOD) / SHEEN_PERIOD           # 0..1
    band_x = -S * 0.4 + sweep * (S * 1.8)                  # travels off-screen ends
    diag = xxf + (yyf * 0.4)                               # diagonal orientation
    sheen = np.clip(1.0 - np.abs(diag - band_x) / SHEEN_WIDTH, 0.0, 1.0) ** 2
    for k in range(3):
        fill[:, :, k] += (255 - fill[:, :, k]) * sheen * 0.55

    m3 = mask[:, :, None]
    img = np.where(m3, fill, img)
    return Image.fromarray(np.clip(img, 0, 255).astype("uint8"), "RGB")


def main() -> None:
    import numpy as np

    n = int(LOOP_MS / 1000 * FPS)
    frames = [render_frame(i / FPS * 1000) for i in range(n)]

    pal_src = frames[n // 2].convert("RGB").quantize(colors=200, method=Image.Quantize.MAXCOVERAGE)
    TRANSP = 255

    def to_p(img):
        return img.convert("RGB").quantize(palette=pal_src, dither=Image.Dither.NONE)

    p_frames = [to_p(f) for f in frames]
    arrs = [np.asarray(p) for p in p_frames]
    out = [p_frames[0]]
    for i in range(1, n):
        cur = p_frames[i].copy()
        same = arrs[i] == arrs[i - 1]
        b = np.asarray(cur).copy()
        b[same] = TRANSP
        cur = Image.fromarray(b, mode="P")
        cur.putpalette(pal_src.getpalette())
        cur.info["transparency"] = TRANSP
        out.append(cur)

    out[0].save(
        "community/brand/agentty-icon.gif",
        save_all=True,
        append_images=out[1:],
        duration=int(1000 / FPS),
        loop=0,
        optimize=False,
        disposal=1,
        transparency=TRANSP,
    )
    print(f"wrote community/brand/agentty-icon.gif ({n} frames, {S}x{S})")


if __name__ == "__main__":
    main()
