from __future__ import annotations

from pathlib import Path
import shutil

from PIL import Image

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "源图" / "角色组图_批次4_村民.png"
BACKUP = ROOT / "修复备份_重裁前_谭老六杨波"

# 手工复核过的源图裁切框：保留完整人物，避开相邻人物。
# 坐标基于实际 1717×916 源图。
CROPS = {
    "谭老六": (972, 38, 1372, 882),
    "杨波": (1332, 38, 1717, 888),
}

KEY = (255, 0, 255)


def remove_magenta(im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    pix = rgba.load()
    w, h = rgba.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = pix[x, y]
            # 源图背景是高饱和洋红；给边缘抗锯齿留出柔性阈值。
            if r > 210 and b > 210 and g < 90:
                pix[x, y] = (0, 0, 0, 0)
            elif r > 175 and b > 175 and g < 125:
                # Source-sheet antialiasing can connect neighboring portraits through
                # magenta fringe; remove it fully so connected-component cleanup works.
                pix[x, y] = (0, 0, 0, 0)
    return rgba


def trim_and_margin(im: Image.Image, margin: int = 44) -> Image.Image:
    alpha = im.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return im
    cropped = im.crop(bbox)
    canvas = Image.new("RGBA", (cropped.width + margin * 2, cropped.height + margin * 2), (0, 0, 0, 0))
    canvas.alpha_composite(cropped, (margin, margin))
    return canvas


def remove_known_edge_slivers(name: str, im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    pix = rgba.load()
    w, h = rgba.size
    if name == "谭老六":
        return rgba
    elif name == "杨波":
        return rgba
    return rgba


def remove_postmargin_slivers(name: str, im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    pix = rgba.load()
    w, h = rgba.size
    if name == "杨波":
        # After transparent margin is added, the real figure starts well inside the canvas.
        # These two isolated source-sheet fragments sit outside that area.
        for y in range(220, min(h, 545)):
            for x in range(0, min(74, w)):
                pix[x, y] = (0, 0, 0, 0)
            for x in range(max(0, w - 28), w):
                pix[x, y] = (0, 0, 0, 0)
    elif name == "谭老六":
        for y in range(220, min(h, 520)):
            for x in range(0, min(54, w)):
                pix[x, y] = (0, 0, 0, 0)
            for x in range(max(0, w - 54), w):
                pix[x, y] = (0, 0, 0, 0)
    return rgba


def keep_largest_component(im: Image.Image) -> Image.Image:
    """Remove isolated fragments from neighboring people after cropping."""
    from collections import deque

    rgba = im.convert("RGBA")
    alpha = rgba.getchannel("A")
    w, h = rgba.size
    pix = alpha.load()
    seen = bytearray(w * h)
    comps: list[list[tuple[int, int]]] = []
    for y in range(h):
        for x in range(w):
            idx = y * w + x
            if seen[idx] or pix[x, y] <= 8:
                continue
            q = deque([(x, y)])
            seen[idx] = 1
            pts: list[tuple[int, int]] = []
            while q:
                cx, cy = q.popleft()
                pts.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if nx < 0 or ny < 0 or nx >= w or ny >= h:
                        continue
                    nidx = ny * w + nx
                    if seen[nidx] or pix[nx, ny] <= 8:
                        continue
                    seen[nidx] = 1
                    q.append((nx, ny))
            comps.append(pts)
    if not comps:
        return rgba
    keep = max(comps, key=len)
    mask = Image.new("L", (w, h), 0)
    mask_pix = mask.load()
    alpha_pix = alpha.load()
    for x, y in keep:
        mask_pix[x, y] = alpha_pix[x, y]
    rgba.putalpha(mask)
    return rgba


def main() -> None:
    BACKUP.mkdir(exist_ok=True)
    source = Image.open(SOURCE).convert("RGBA")
    for name, box in CROPS.items():
        dst = ROOT / f"{name}.png"
        backup = BACKUP / f"{name}.png"
        if dst.exists() and not backup.exists():
            shutil.copy2(dst, backup)
        cut = source.crop(box)
        clean = trim_and_margin(remove_known_edge_slivers(name, keep_largest_component(remove_magenta(cut))), margin=44)
        clean = remove_postmargin_slivers(name, clean)
        clean.save(dst)
        print(f"{name}: {box} -> {clean.size}")


if __name__ == "__main__":
    main()
