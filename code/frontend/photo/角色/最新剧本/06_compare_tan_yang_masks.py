from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "源图" / "角色组图_批次4_村民.png"
CROPS = {
    "谭老六": (972, 38, 1372, 882),
    "杨波": (1332, 38, 1717, 888),
}


def font(size: int, bold: bool = False):
    for p in [Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf")]:
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def remove_magenta(im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    pix = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = pix[x, y]
            if r > 175 and b > 175 and g < 125:
                pix[x, y] = (0, 0, 0, 0)
    return rgba


def trim_margin(im: Image.Image, margin: int = 44) -> Image.Image:
    bbox = im.getchannel("A").getbbox()
    if not bbox:
        return im
    crop = im.crop(bbox)
    out = Image.new("RGBA", (crop.width + margin * 2, crop.height + margin * 2), (0, 0, 0, 0))
    out.alpha_composite(crop, (margin, margin))
    return out


def erase_variant(name: str, im: Image.Image, variant: str) -> Image.Image:
    rgba = im.copy()
    pix = rgba.load()
    w, h = rgba.size
    if name == "谭老六":
        if variant == "mild":
            for y in range(260, min(h, 470)):
                for x in range(max(0, w - 10), w):
                    pix[x, y] = (0, 0, 0, 0)
        elif variant == "current":
            for y in range(250, min(h, 500)):
                for x in range(max(0, w - 26), w):
                    pix[x, y] = (0, 0, 0, 0)
    if name == "杨波":
        if variant == "mild":
            for y in range(230, min(h, 510)):
                for x in range(0, min(38, w)):
                    pix[x, y] = (0, 0, 0, 0)
                for x in range(max(0, w - 18), w):
                    pix[x, y] = (0, 0, 0, 0)
        elif variant == "current":
            for y in range(210, min(h, 540)):
                for x in range(0, min(76, w)):
                    pix[x, y] = (0, 0, 0, 0)
                for x in range(max(0, w - 42), w):
                    pix[x, y] = (0, 0, 0, 0)
    return rgba


def checker(w: int, h: int, cell: int = 20) -> Image.Image:
    bg = Image.new("RGB", (w, h), (238, 232, 220))
    draw = ImageDraw.Draw(bg)
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(211, 205, 193))
    return bg


def fit(im: Image.Image, box=(300, 680)) -> Image.Image:
    bbox = im.getchannel("A").getbbox()
    crop = im.crop(bbox) if bbox else im
    scale = min((box[0] - 18) / crop.width, (box[1] - 18) / crop.height)
    crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
    out = Image.new("RGBA", box, (0, 0, 0, 0))
    out.alpha_composite(crop, ((box[0] - crop.width) // 2, box[1] - 9 - crop.height))
    return out


def main() -> None:
    source = Image.open(SOURCE).convert("RGBA")
    variants = ["none", "mild", "current"]
    card_w, card_h = 320, 760
    out = Image.new("RGB", (40 + card_w * len(variants), 90 + card_h * 2), (246, 239, 224))
    draw = ImageDraw.Draw(out)
    draw.text((24, 22), "谭老六/杨波边缘修复方案对比：none=不擦边，mild=极窄擦边，current=当前版", font=font(24, True), fill=(40, 40, 34))
    for row, name in enumerate(["谭老六", "杨波"]):
        base = trim_margin(remove_magenta(source.crop(CROPS[name])), 44)
        for col, variant in enumerate(variants):
            x = 24 + col * card_w
            y = 78 + row * card_h
            bg = checker(card_w - 20, card_h - 66)
            out.paste(bg, (x + 10, y + 10))
            img = fit(erase_variant(name, base, variant), (card_w - 20, card_h - 66))
            out.paste(img, (x + 10, y + 10), img)
            draw.rectangle((x + 10, y + 10, x + card_w - 10, y + card_h - 56), outline=(166, 139, 91), width=2)
            label = f"{name}｜{variant}"
            tw = draw.textbbox((0, 0), label, font=font(22, True))[2]
            draw.text((x + (card_w - tw) // 2, y + card_h - 42), label, font=font(22, True), fill=(38, 84, 66))
    out.save(ROOT / "谭老六_杨波_边缘方案对比.jpg", quality=94)


if __name__ == "__main__":
    main()
