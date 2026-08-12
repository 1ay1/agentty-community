# agentty brand assets

Every logo here is derived **pixel-for-pixel from the in-app welcome-screen
wordmark** — maya's `WelcomeScreen`, the `>AGENTTY` 6×7 half-block bitmap font —
so the animated web logo, the Discord icon, the favicon, and the terminal splash
are all the same mark. Brand color is the flagship "Strategic" sigil magenta.

## Assets

| File | What it is | Use it for |
|------|-----------|------------|
| `agentty-welcome.gif` | **animated** wordmark: gentle bob + soft heartbeat | README header, social, Discord `#welcome` |
| `agentty-welcome.svg` | **animated** SVG (SMIL) of the same motion, self-contained | website hero, GitHub README `<img>` |
| `agentty-icon.gif` | **animated** chevron: glow halo + gradient + sweeping sheen + bob | README/web/socials (see note) |
| `make_icon_gif.py` | the icon-GIF renderer | tweak/re-generate the animated icon |
| `agentty-logo.svg` / `.png` | static `>AGENTTY` wordmark | README, banners |
| `agentty-icon.png` | 512×512 chevron `>` on a dark rounded tile | **Discord server icon**, app icon |
| `agentty-icon-128.png` | 128×128 of the same | favicon, avatars |
| `agentty-server-banner.png` / `.svg` | 960×540 branded banner (logo + tagline on a glow bg) | **Discord server background**, social header |
| `agentty-icon.svg` | vector source of the icon | re-export any size |
| `agentty-wordmark.txt` | ANSI full-block wordmark | Discord `#read-me-first` (in a code fence) |
| `make_welcome_gif.py` | the renderer | tweak/re-generate the GIF |

## The animation

The wordmark is **always fully drawn and perpetually in gentle motion** — a
slow, subtle, seamless loop with no intro. It reads as "alive" without being
distracting:

1. **Slow bob** — one gentle sine cycle over a 6 s loop; each letter sways ~10 px
   (≈ 0.6 cell) with a small per-letter phase offset, so the wordmark undulates
   softly left-to-right.
2. **Soft heartbeat** — a smooth ramp that blends the letters only ~30 % toward
   white once per loop, centered mid-loop (never a hard flash).

The bob and pulse periods both equal the loop length, so the last frame is
pixel-identical to the first (GIF seam = 0). `disposal=1` transparency-diffed
frames keep the file small.

## Colors

- Magenta gradient: `#ff5fd2` → `#c04bff` (interpolated across the wordmark width)
- Icon / dark tile background: `#16121c`

## Regenerate

```sh
python3 community/brand/make_welcome_gif.py        # GIF
# SVG/PNG variants are produced by the one-off scripts noted in git history;
# the committed .svg files are hand-checked and safe to edit directly.
```

## Discord setup

- **Server icon:** upload `agentty-icon.png` (Server Settings → Overview).
  Discord server icons are static — use the PNG here. `agentty-icon.gif`
  (animated) is for the README/web/socials, or a Nitro user avatar.
- **Server background:** upload `agentty-server-banner.png` (Server Settings →
  Overview → Server Background — requires Level 2 boost). 960×540, 16:9.
- **`#read-me-first`:** paste `agentty-wordmark.txt` inside a ``` code fence so
  Discord renders the blocks in monospace.
- **`#welcome` / hero:** drop `agentty-welcome.gif` — it loops on its own.

## Embedding the animated logo

GitHub README (renders the animated SVG or GIF):

```md
<p align="center"><img src="community/brand/agentty-welcome.svg" alt="agentty" width="472"></p>
```

> Note: some Markdown renderers strip SMIL from SVGs for security. If the SVG
> shows only the first frame, use `agentty-welcome.gif` instead — it animates
> everywhere.
