"""Generate the app icons (run once; output committed to the repo).

    python3 frontend/gen_icon.py

Produces icon-180.png (iOS apple-touch-icon) and icon-512.png (PWA manifest):
a papaya-orange rounded tile carrying the GridMaster mark — an empty grid spot,
a square seen from a slightly angled top-down view with its near (bottom) side
left open, like an unclaimed box on the F1 starting grid.
"""
from PIL import Image, ImageDraw
from pathlib import Path

OUT = Path(__file__).resolve().parent
ORANGE, DARK = (255, 135, 0), (27, 36, 37)


def make(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(size * 0.22)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=ORANGE)

    # Empty grid spot: a perspective square (near edge wider than the far edge)
    # drawn as three connected sides — left, far/back, right — with the near
    # (bottom) edge omitted so the box reads as open. Coordinates are fractions
    # of the canvas to scale cleanly to any size.
    pts = [(0.16, 0.82), (0.31, 0.25), (0.69, 0.25), (0.84, 0.82)]
    px = [(x * size, y * size) for x, y in pts]
    d.line(px, fill=DARK, width=max(3, int(size * 0.11)), joint="curve")
    # Round the outer corners so the stroke matches the SVG's rounded caps.
    rr = max(2, int(size * 0.055))
    for cx, cy in (px[0], px[-1]):
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=DARK)
    return img


for s in (180, 512):
    make(s).save(OUT / f"icon-{s}.png")
    print("wrote", OUT / f"icon-{s}.png")
