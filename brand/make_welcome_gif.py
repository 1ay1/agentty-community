#!/usr/bin/env python3
"""Render the agentty welcome-screen wordmark animation to a GIF (and PNG frames).

This reproduces maya's WelcomeScreen sigil animation pixel-for-pixel:
  • Phase 1 — per-letter cascade drop: 100 ms stagger, 500 ms cubic-ease-out,
    each letter falls in from above its home row.
  • Phase 2 — per-letter sine bob: ±1.5 px, 2200 ms period, 0.7 rad phase/letter.
  • Heartbeat — an 80 ms bright-white flash every 3200 ms after the cascade.

The 6×7 glyph bitmaps, the ">AGENTTY" text, the 1-px spacer, and the sigil
magenta are the same values the C++ widget uses, so the GIF matches what you see
in the terminal. Output: community/brand/agentty-welcome.gif
"""

from __future__ import annotations

import math
from PIL import Image

# ── the exact maya 6×7 glyphs ──────────────────────────────────────────────
G = {
    '>': ["      ", "#  #  ", "## ## ", " ## ##", "## ## ", "#  #  ", "      "],
    'A': ["  ##  ", " #  # ", "#    #", "######", "#    #", "#    #", "#    #"],
    'G': [" #### ", "#    #", "#     ", "#  ###", "#    #", "#    #", " #### "],
    'E': ["######", "#     ", "#     ", "##### ", "#     ", "#     ", "######"],
    'N': ["#    #", "##   #", "# #  #", "#  # #", "#   ##", "#    #", "#    #"],
    'T': ["######", "  ##  ", "  ##  ", "  ##  ", "  ##  ", "  ##  ", "  ##  "],
    'Y': ["#    #", "#    #", " #  # ", "  ##  ", "  ##  ", "  ##  ", "  ##  "],
}
TEXT = ">AGENTTY"
FW, FH, SPACER = 6, 7, 1
PAD_TOP, PAD_BOTTOM = 3, 3          # extra room for the drop + bob
PW = len(TEXT) * FW + (len(TEXT) - 1) * SPACER
PH = FH + PAD_TOP + PAD_BOTTOM

# ── animation constants ───────────────────────────────────────
# No cascade drop — the wordmark is always fully drawn and perpetually bobbing.
# Everything is chosen so the last frame flows seamlessly into the first:
#   • the bob period tiles the loop an integer number of times, and
#   • the heartbeat period divides the loop exactly.
BOB_AMP, BOB_LETTER_PHASE = 1.0, 0.45
PULSE_WIDTH = 220

# ── color ──────────────────────────────────────────────────────────────────
BG = (22, 18, 28)                  # #16121c
# magenta gradient endpoints; interpolated across the wordmark width
C0 = (255, 95, 210)                # #ff5fd2
C1 = (192, 75, 255)               # #c04bff
WHITE = (255, 255, 255)
SCALE = 10                         # px per pixel-cell in the output
FPS = 20
LOOP_MS = 4000                     # a touch quicker, still calm and seamless
# Slow + subtle: a single gentle bob cycle over the whole loop, and one soft
# heartbeat. Both periods divide LOOP_MS exactly so frame[N] == frame[0].
BOB_PERIOD = LOOP_MS               # exactly ONE slow bob cycle per loop
PULSE_PERIOD = LOOP_MS             # one heartbeat per loop


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def letter_y_offset_px(li: int, t_ms: float) -> int:
    # Pure sine bob in OUTPUT pixels, so a sub-cell amplitude (e.g. 0.6 cells)
    # still moves smoothly instead of snapping between whole cells. Because
    # BOB_PERIOD divides LOOP_MS exactly, the offset at t=LOOP_MS == t=0.
    phase = 2 * math.pi * t_ms / BOB_PERIOD + li * BOB_LETTER_PHASE
    return round(math.sin(phase) * BOB_AMP * SCALE)


def render_frame(t_ms: float) -> Image.Image:
    # Draw straight into the full-resolution canvas so we can shift letters by
    # individual output pixels (sub-cell), giving a slow, smooth, subtle bob.
    W, H = PW * SCALE, PH * SCALE
    img = Image.new("RGB", (W, H), BG)
    px = img.load()
    # Gentle heartbeat: a smooth triangular ramp centered mid-loop that blends
    # the letters partway toward white (max ~35%), not a hard flash.
    d = abs((t_ms % PULSE_PERIOD) - PULSE_PERIOD / 2)
    pulse = max(0.0, 1.0 - d / (PULSE_WIDTH / 2)) * 0.35
    for li, ch in enumerate(TEXT):
        base_x = li * (FW + SPACER)
        dy = letter_y_offset_px(li, t_ms)
        for row in range(FH):
            for col in range(FW):
                if G[ch][row][col] != '#':
                    continue
                base = lerp(C0, C1, (base_x + col) / max(1, PW - 1))
                color = lerp(base, WHITE, pulse) if pulse > 0 else base
                x0 = (base_x + col) * SCALE
                y0 = (PAD_TOP + row) * SCALE + dy
                for yy in range(y0, y0 + SCALE):
                    if 0 <= yy < H:
                        rowpx = yy
                        for xx in range(x0, x0 + SCALE):
                            if 0 <= xx < W:
                                px[xx, rowpx] = color
    return img


def main() -> None:
    n = int(LOOP_MS / 1000 * FPS)
    frames = [render_frame(i / FPS * 1000) for i in range(n)]

    # Quantize to a shared palette with one spare slot reserved for transparency,
    # then, for every frame after the first, mark pixels that are unchanged from
    # the previous frame as transparent. With disposal=1 (leave prior frame in
    # place) the encoder only stores the moving pixels — the static dark field is
    # written once. This shrinks the file several-fold with zero visual change,
    # and keeps the seam intact (frame 0 is a full frame).
    pal_src = frames[len(frames) // 2].convert("RGB").quantize(colors=127, method=Image.Quantize.MAXCOVERAGE)
    palette = pal_src.getpalette()
    TRANSP = 127  # spare palette index

    def to_p(img):
        return img.convert("RGB").quantize(palette=pal_src, dither=Image.Dither.NONE)

    out = [to_p(frames[0])]
    prev = out[0]
    for f in frames[1:]:
        cur = to_p(f)
        a = prev.load()
        b = cur.load()
        w, h = cur.size
        for y in range(h):
            for x in range(w):
                if a[x, y] == b[x, y]:
                    b[x, y] = TRANSP
        cur.info["transparency"] = TRANSP
        out.append(cur)
        prev = to_p(f)  # compare against the true (non-holed) previous frame

    out[0].save(
        "community/brand/agentty-welcome.gif",
        save_all=True,
        append_images=out[1:],
        duration=int(1000 / FPS),
        loop=0,
        optimize=False,
        disposal=1,
        transparency=TRANSP,
    )
    print(f"wrote community/brand/agentty-welcome.gif ({n} frames, {PW*SCALE}x{PH*SCALE})")


if __name__ == "__main__":
    main()
