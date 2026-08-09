from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent


def comps(alpha: Image.Image, threshold: int = 8) -> list[dict]:
    w, h = alpha.size
    pix = alpha.load()
    seen = bytearray(w * h)
    result = []
    for y in range(h):
        for x in range(w):
            idx = y * w + x
            if seen[idx] or pix[x, y] <= threshold:
                continue
            q = deque([(x, y)])
            seen[idx] = 1
            xs, ys = [], []
            count = 0
            while q:
                cx, cy = q.popleft()
                xs.append(cx)
                ys.append(cy)
                count += 1
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if nx < 0 or ny < 0 or nx >= w or ny >= h:
                        continue
                    nidx = ny * w + nx
                    if seen[nidx] or pix[nx, ny] <= threshold:
                        continue
                    seen[nidx] = 1
                    q.append((nx, ny))
            result.append({"count": count, "bbox": (min(xs), min(ys), max(xs) + 1, max(ys) + 1)})
    return sorted(result, key=lambda r: r["count"], reverse=True)


def main() -> None:
    suspicious = []
    for path in sorted(ROOT.glob("*.png")):
        im = Image.open(path).convert("RGBA")
        alpha = im.getchannel("A")
        cc = comps(alpha)
        small = [c for c in cc[1:] if c["count"] > 30]
        bbox = alpha.getbbox()
        touches = []
        if bbox:
            x0, y0, x1, y1 = bbox
            if x0 <= 1:
                touches.append("left")
            if y0 <= 1:
                touches.append("top")
            if x1 >= im.width - 1:
                touches.append("right")
            if y1 >= im.height - 1:
                touches.append("bottom")
        if small or touches:
            suspicious.append(
                {
                    "name": path.name,
                    "size": im.size,
                    "components": len(cc),
                    "small_components": small[:5],
                    "edge_touch": touches,
                    "bbox": bbox,
                }
            )
    for item in suspicious:
        print(item)
    print(f"suspicious={len(suspicious)}")


if __name__ == "__main__":
    main()
