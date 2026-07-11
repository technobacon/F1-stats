"""Generate the app icons (run once; output committed to the repo).

    python3 frontend/gen_icon.py

Produces icon-180.png (iOS apple-touch-icon) and icon-512.png (PWA manifest):
the GridMaster mark — the **Chequered G**, a pixel-grid monogram of rounded
squares (starting grid + chequered flag + the game's own share-grid language)
leaning forward row-by-row like pixel italics, on the app's dark surface. The
lit square at the G's crossbar is "your spot on the grid, claimed", in the
default (McLaren) papaya. Geometry mirrors favicon.svg / the header mark.
"""
from PIL import Image, ImageDraw
from pathlib import Path

OUT = Path(__file__).resolve().parent
DARK, LIGHT, ORANGE = (10, 13, 18), (238, 242, 247), (255, 128, 0)

# The Chequered G, as (x, y) cell origins in a 100×100 design space (cell 16,
# pitch 18, each row offset +2.5 per step up — the pixel-italic lean). The
# last entry is the claimed (accent) cell at the G's crossbar.
BASE_CELLS = [
    (29, 6), (47, 6), (65, 6),
    (8.5, 24),
    (6, 42), (60, 42), (78, 42),
    (3.5, 60), (75.5, 60),
    (19, 78), (37, 78), (55, 78), (73, 78),
]
CLAIM_CELL = (42, 42)
CELL, RADIUS = 16, 4
# Inset the 100-space artwork inside the tile (matches favicon.svg's 0.78 pad).
PAD_SCALE = 0.78


def make(size: int) -> Image.Image:
    # Draw at 4× and downsample for clean edges at small sizes.
    ss = size * 4
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, ss - 1, ss - 1], radius=int(ss * 0.22), fill=DARK)

    k = ss / 100 * PAD_SCALE                    # design-space -> pixels
    off = ss * (1 - PAD_SCALE) / 2              # centre the padded artwork

    def cell(x: float, y: float, colour: tuple) -> None:
        x0, y0 = off + x * k, off + y * k
        d.rounded_rectangle([x0, y0, x0 + CELL * k, y0 + CELL * k],
                            radius=RADIUS * k, fill=colour)

    for x, y in BASE_CELLS:
        cell(x, y, LIGHT)
    cell(*CLAIM_CELL, ORANGE)
    return img.resize((size, size), Image.LANCZOS)


for s in (180, 512):
    make(s).save(OUT / f"icon-{s}.png")
    print("wrote", OUT / f"icon-{s}.png")
