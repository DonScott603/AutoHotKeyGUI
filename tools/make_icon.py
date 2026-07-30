"""Generate app.ico for the AutoHotkey Text Expansion Manager.

Draws a blue rounded tile with a white "expand" chevron and two text lines,
matching the app's accent colour. Rendered at high resolution and downsampled
for smooth (anti-aliased) edges, then written as a multi-size .ico.

Run:  python tools/make_icon.py
"""

from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw

RGBA = tuple[int, int, int, int]

ACCENT: RGBA = (37, 99, 235, 255)   # #2563eb, the app's accent blue
WHITE: RGBA = (255, 255, 255, 255)

SCALE = 4          # supersample factor for anti-aliasing
BASE = 256
S = BASE * SCALE   # working canvas size
ICO_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]

OUT_DIR = Path(__file__).resolve().parent.parent


def _round_line(
    draw: ImageDraw.ImageDraw,
    points: Sequence[tuple[int, int]],
    width: int,
    fill: RGBA,
) -> None:
    draw.line(points, fill=fill, width=width, joint="curve")
    r = width // 2
    for x, y in points:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def render() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded tile background.
    margin = int(0.06 * S)
    draw.rounded_rectangle(
        (margin, margin, S - margin, S - margin),
        radius=int(0.22 * S),
        fill=ACCENT,
    )

    # White "expand" chevron on the left.
    lw = int(0.085 * S)
    _round_line(
        draw,
        [(int(0.33 * S), int(0.32 * S)),
         (int(0.47 * S), int(0.50 * S)),
         (int(0.33 * S), int(0.68 * S))],
        width=lw,
        fill=WHITE,
    )

    # Two rounded "text" lines on the right, decreasing width.
    bar_h = int(0.085 * S)
    x0 = int(0.54 * S)
    for y_frac, x1_frac in ((0.40, 0.78), (0.56, 0.70)):
        y = int(y_frac * S)
        draw.rounded_rectangle(
            (x0, y, int(x1_frac * S), y + bar_h),
            radius=bar_h // 2,
            fill=WHITE,
        )

    return img.resize((BASE, BASE), Image.Resampling.LANCZOS)


def main() -> None:
    icon = render()
    ico_path = OUT_DIR / "app.ico"
    icon.save(ico_path, format="ICO", sizes=ICO_SIZES)
    print(f"Wrote {ico_path}")


if __name__ == "__main__":
    main()
