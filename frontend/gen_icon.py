"""Generate the app icons (run once; output committed to the repo).

    python3 frontend/gen_icon.py

Produces icon-180.png (iOS apple-touch-icon) and icon-512.png (PWA manifest):
a papaya-orange rounded tile with an "F1" wordmark and a checkered-flag bar.
"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

OUT = Path(__file__).resolve().parent
ORANGE, DARK, WHITE = (255, 135, 0), (27, 36, 37), (255, 255, 255)


def _font(size):
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(size * 0.22)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=ORANGE)

    # "F1" wordmark, centred a little high to leave room for the flag bar.
    font = _font(int(size * 0.46))
    text = "F1"
    box = d.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text(((size - tw) / 2 - box[0], size * 0.30 - box[1]), text, font=font, fill=DARK)

    # Checkered-flag bar near the bottom.
    cells, bar_h = 8, max(2, size // 16)
    cw = size / cells
    y0 = int(size * 0.72)
    for i in range(cells):
        for j in range(2):
            if (i + j) % 2 == 0:
                x0 = int(i * cw)
                d.rectangle([x0, y0 + j * bar_h, int(x0 + cw), y0 + (j + 1) * bar_h],
                            fill=DARK if (i + j) % 2 == 0 else WHITE)
    return img


for s in (180, 512):
    make(s).save(OUT / f"icon-{s}.png")
    print("wrote", OUT / f"icon-{s}.png")
