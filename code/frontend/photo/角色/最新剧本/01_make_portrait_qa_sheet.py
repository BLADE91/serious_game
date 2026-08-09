from __future__ import annotations

from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent

DEFAULT_NAMES = ["陈默", "谭老六", "顾克明", "宁德海", "杨波"]


def font(size: int, bold: bool = False):
    for path in [
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ]:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def checker(w: int, h: int, cell: int = 20) -> Image.Image:
    img = Image.new("RGB", (w, h), (238, 232, 220))
    draw = ImageDraw.Draw(img)
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(211, 205, 193))
    return img


def fit(im: Image.Image, box: tuple[int, int], pad: int = 26) -> Image.Image:
    im = im.convert("RGBA")
    alpha = im.getchannel("A")
    bbox = alpha.getbbox() or (0, 0, im.width, im.height)
    crop = im.crop(bbox)
    bw, bh = box
    scale = min((bw - pad * 2) / crop.width, (bh - pad * 2) / crop.height)
    crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
    canvas = Image.new("RGBA", box, (0, 0, 0, 0))
    canvas.alpha_composite(crop, ((bw - crop.width) // 2, bh - pad - crop.height))
    return canvas


def main() -> None:
    names = sys.argv[1:] or DEFAULT_NAMES
    card_w, card_h = 360, 760
    margin = 34
    title_h = 76
    out = Image.new("RGB", (margin * 2 + len(names) * card_w, margin * 2 + title_h + card_h), (246, 239, 224))
    draw = ImageDraw.Draw(out)
    draw.text((margin, margin), "问题立绘边缘 QA｜棋盘格显示透明区域", font=font(34, True), fill=(45, 43, 36))
    for i, name in enumerate(names):
        x = margin + i * card_w
        y = margin + title_h
        bg = checker(card_w - 18, card_h - 62)
        out.paste(bg, (x + 9, y + 10))
        im = Image.open(ROOT / f"{name}.png").convert("RGBA")
        fitted = fit(im, (card_w - 18, card_h - 62))
        out.paste(fitted, (x + 9, y + 10), fitted)
        draw.rectangle((x + 9, y + 10, x + card_w - 9, y + card_h - 52), outline=(166, 139, 91), width=2)
        tw = draw.textbbox((0, 0), name, font=font(26, True))[2]
        draw.text((x + (card_w - tw) // 2, y + card_h - 38), name, font=font(26, True), fill=(38, 84, 66))
    out.save(ROOT / "问题立绘_QA_修复前.jpg", quality=94)


if __name__ == "__main__":
    main()
