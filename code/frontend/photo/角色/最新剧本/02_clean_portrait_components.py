from __future__ import annotations

from collections import deque
from pathlib import Path
import shutil

from PIL import Image

ROOT = Path(__file__).resolve().parent
BACKUP = ROOT / "修复备份_原始问题立绘"
TARGETS = ["陈默", "谭老六", "顾克明", "宁德海", "杨波"]


def alpha_components(alpha: Image.Image, threshold: int = 8) -> list[dict]:
    w, h = alpha.size
    pix = alpha.load()
    seen = bytearray(w * h)
    comps: list[dict] = []
    for y in range(h):
        for x in range(w):
            idx = y * w + x
            if seen[idx] or pix[x, y] <= threshold:
                continue
            q = deque([(x, y)])
            seen[idx] = 1
            xs: list[int] = []
            ys: list[int] = []
            points: list[tuple[int, int]] = []
            count = 0
            while q:
                cx, cy = q.popleft()
                xs.append(cx)
                ys.append(cy)
                points.append((cx, cy))
                count += 1
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if nx < 0 or ny < 0 or nx >= w or ny >= h:
                        continue
                    nidx = ny * w + nx
                    if seen[nidx] or pix[nx, ny] <= threshold:
                        continue
                    seen[nidx] = 1
                    q.append((nx, ny))
            comps.append(
                {
                    "count": count,
                    "bbox": (min(xs), min(ys), max(xs) + 1, max(ys) + 1),
                    "points": points,
                    "touch_edge": min(xs) == 0 or min(ys) == 0 or max(xs) == w - 1 or max(ys) == h - 1,
                }
            )
    return sorted(comps, key=lambda c: c["count"], reverse=True)


def clean_image(path: Path) -> dict:
    im = Image.open(path).convert("RGBA")
    alpha = im.getchannel("A")
    comps = alpha_components(alpha)
    if not comps:
        return {"file": path.name, "components": 0, "kept": 0, "removed": 0}

    mask = Image.new("L", im.size, 0)
    mask_pix = mask.load()
    alpha_pix = alpha.load()
    main = comps[0]
    for x, y in main["points"]:
        mask_pix[x, y] = alpha_pix[x, y]

    # Keep tiny antialiasing specks immediately adjacent to the main bbox, but remove remote slivers.
    cleaned = im.copy()
    cleaned.putalpha(mask)

    # Manual art-direction cleanup for source sheets where a neighboring portrait touches the subject.
    # These masks remove only side-edge fragments, not central character pixels.
    px = cleaned.load()
    name = path.stem
    if name == "谭老六":
        # Right-side beige sleeve / neighboring body fragment.
        for y in range(120, min(cleaned.height, 410)):
            for x in range(max(0, cleaned.width - 74), cleaned.width):
                r, g, b, a = px[x, y]
                if a and (r > 150 and g > 110 and b > 70 or x > cleaned.width - 34):
                    px[x, y] = (0, 0, 0, 0)
    elif name == "宁德海":
        for y in range(0, cleaned.height):
            for x in range(max(0, cleaned.width - 48), cleaned.width):
                r, g, b, a = px[x, y]
                if a and x > cleaned.width - 38:
                    px[x, y] = (0, 0, 0, 0)
    elif name == "杨波":
        for y in range(120, min(cleaned.height, 420)):
            for x in range(0, min(54, cleaned.width)):
                r, g, b, a = px[x, y]
                if a and x < 42:
                    px[x, y] = (0, 0, 0, 0)

    bbox = cleaned.getchannel("A").getbbox()
    if bbox:
        pad = 30 if name in {"谭老六", "杨波"} else 22
        cx0 = max(0, bbox[0] - pad)
        cy0 = max(0, bbox[1] - pad)
        cx1 = min(im.width, bbox[2] + pad)
        cy1 = min(im.height, bbox[3] + pad)
        cleaned = cleaned.crop((cx0, cy0, cx1, cy1))
    cleaned.save(path)
    return {
        "file": path.name,
        "components": len(comps),
        "kept": comps[0]["count"],
        "removed": sum(c["count"] for c in comps[1:]),
        "main_bbox": main["bbox"],
        "new_size": cleaned.size,
    }


def main() -> None:
    BACKUP.mkdir(exist_ok=True)
    results = []
    for name in TARGETS:
        src = ROOT / f"{name}.png"
        backup = BACKUP / f"{name}.png"
        if src.exists() and not backup.exists():
            shutil.copy2(src, backup)
        elif backup.exists():
            shutil.copy2(backup, src)
        results.append(clean_image(src))
    for row in results:
        print(row)


if __name__ == "__main__":
    main()
