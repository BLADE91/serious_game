from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
MIN_MARGIN = 32


def safe_margin(path: Path) -> bool:
    im = Image.open(path).convert("RGBA")
    bbox = im.getchannel("A").getbbox()
    if not bbox:
        return False
    x0, y0, x1, y1 = bbox
    left = max(0, MIN_MARGIN - x0)
    top = max(0, MIN_MARGIN - y0)
    right = max(0, MIN_MARGIN - (im.width - x1))
    bottom = max(0, MIN_MARGIN - (im.height - y1))
    if not any((left, top, right, bottom)):
        return False
    canvas = Image.new("RGBA", (im.width + left + right, im.height + top + bottom), (0, 0, 0, 0))
    canvas.alpha_composite(im, (left, top))
    canvas.save(path)
    print(f"{path.name}: +L{left} +T{top} +R{right} +B{bottom} -> {canvas.size}")
    return True


def main() -> None:
    changed = 0
    for path in sorted(ROOT.glob("*.png")):
        if safe_margin(path):
            changed += 1
    print(f"changed={changed}")


if __name__ == "__main__":
    main()
