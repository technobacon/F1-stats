"""Generate the app icons (run once; output committed to the repo).

    python3 frontend/gen_icon.py

Produces icon-180.png (iOS apple-touch-icon) and icon-512.png (PWA manifest):
a stone-brown bevelled tile carrying the ScapeMaster mark — a rune stone (a
rounded standing stone) with a simple three-stroke "guess" chevron glyph in
Saradomin blue. Original art, no game assets.
"""
from PIL import Image, ImageDraw
from pathlib import Path

OUT = Path(__file__).resolve().parent
STONE = (58, 47, 28)       # tile background — dark stone brown
STONE_HI = (110, 92, 58)   # bevel highlight (top/left)
STONE_LO = (30, 23, 13)    # bevel shadow (bottom/right)
SLAB = (43, 34, 20)        # inner rune-stone slab
BLUE = (47, 107, 216)      # Saradomin-blue glyph


def make(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(size * 0.2)
    # Base tile with a chunky bevel: draw the shadow tile, then the highlight
    # tile offset up-left, then the flat face — a simple carved-stone look.
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=STONE_LO)
    d.rounded_rectangle([0, 0, size - 3, size - 3], radius=r, fill=STONE_HI)
    inset = max(2, int(size * 0.02))
    d.rounded_rectangle([inset, inset, size - 1 - inset, size - 1 - inset],
                        radius=r, fill=STONE)

    # Inner rune-stone slab (a rounded standing stone).
    m = int(size * 0.16)
    d.rounded_rectangle([m, int(size * 0.1), size - m, int(size * 0.9)],
                        radius=int(size * 0.14), fill=SLAB,
                        outline=STONE_LO, width=max(2, int(size * 0.02)))

    # The "guess" glyph: a three-stroke chevron in Saradomin blue.
    pts = [(0.34, 0.62), (0.50, 0.34), (0.66, 0.62)]
    px = [(x * size, y * size) for x, y in pts]
    d.line(px, fill=BLUE, width=max(3, int(size * 0.09)), joint="curve")
    rr = max(2, int(size * 0.045))
    for cx, cy in (px[0], px[-1]):
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=BLUE)
    return img


for s in (180, 512):
    make(s).save(OUT / f"icon-{s}.png")
    print("wrote", OUT / f"icon-{s}.png")
