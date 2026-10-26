"""Generate PyDrop's SVG, PNG and multi-resolution ICO using only the standard library.

Run this after changing the geometric brand design. No image library or external
font is needed, so the source assets can be reproduced on any build machine.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKGROUND = (16, 24, 39)
MINT = (102, 224, 187)
INK = (12, 26, 40)
WHITE = (237, 243, 252)
SVG = '''<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <title>PyDrop</title>
  <rect width="256" height="256" rx="52" fill="#101827"/>
  <path d="M128 38 C111 65 63 112 63 153 C63 193 91 220 128 220 C165 220 193 193 193 153 C193 112 145 65 128 38Z" fill="#66e0bb"/>
  <path d="M96 142 L127 165 L160 137 M127 165 L127 193" fill="none" stroke="#0c1a28" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
  <g fill="#0c1a28"><circle cx="96" cy="142" r="12"/><circle cx="160" cy="137" r="12"/><circle cx="127" cy="193" r="10"/></g>
  <circle cx="127" cy="165" r="12" fill="#edf3fc"/>
</svg>
'''


def _cubic(a, b, c, d, steps=60):
    return [tuple((1-t)**3*a[k] + 3*(1-t)**2*t*b[k] + 3*(1-t)*t*t*c[k] + t**3*d[k]
                  for k in range(2)) for t in (i/steps for i in range(steps+1))]


DROP = (_cubic((128, 38), (111, 65), (63, 112), (63, 153)) +
        _cubic((63, 153), (63, 193), (91, 220), (128, 220)) +
        _cubic((128, 220), (165, 220), (193, 193), (193, 153)) +
        _cubic((193, 153), (193, 112), (145, 65), (128, 38)))


@lru_cache(maxsize=4096)
def _drop_span(y):
    """Cache the two drop edges for each supersampled raster scanline."""
    crossings = []
    last_x, last_y = DROP[-1]
    for next_x, next_y in DROP:
        if (next_y > y) != (last_y > y):
            crossings.append((last_x-next_x)*(y-next_y)/(last_y-next_y)+next_x)
        last_x, last_y = next_x, next_y
    return (min(crossings), max(crossings)) if crossings else (1, 0)


def _distance(x, y, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    along = max(0, min(1, ((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy)))
    return math.hypot(x-a[0]-along*dx, y-a[1]-along*dy)


def _sample(x, y):
    # Signed-distance test for a rounded rectangle centered in the viewbox.
    qx, qy = abs(x-128)-76, abs(y-128)-76
    if math.hypot(max(qx, 0), max(qy, 0))+min(max(qx, qy), 0) > 52:
        return (0, 0, 0, 0)
    left, right = _drop_span(y)
    color = MINT if left <= x < right else BACKGROUND
    if any(_distance(x, y, a, b) <= 5 for a, b in
           [((96, 142), (127, 165)), ((127, 165), (160, 137)), ((127, 165), (127, 193))]):
        color = INK
    if any(math.hypot(x-cx, y-cy) <= radius for cx, cy, radius in
           [(96, 142, 12), (160, 137, 12), (127, 193, 10)]):
        color = INK
    if math.hypot(x-127, y-165) <= 12:
        color = WHITE
    return (*color, 255)


def png(size):
    supersample = 3
    raw = bytearray()
    for row in range(size):
        raw.append(0)  # PNG filter None
        for column in range(size):
            samples = [_sample((column+(sx+.5)/supersample)*256/size,
                               (row+(sy+.5)/supersample)*256/size)
                       for sy in range(supersample) for sx in range(supersample)]
            # Average premultiplied colors to avoid dark fringes at transparency.
            alpha = sum(sample[3] for sample in samples)
            channels = [round(sum(sample[k]*sample[3] for sample in samples)/alpha)
                        if alpha else 0 for k in range(3)]
            raw.extend((*channels, round(alpha/len(samples))))

    def chunk(kind, data):
        return struct.pack(">I", len(data))+kind+data+struct.pack(">I", zlib.crc32(kind+data)&0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def generate(output):
    output.mkdir(parents=True, exist_ok=True)
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = [png(size) for size in sizes]
    (output/"pydrop.svg").write_text(SVG, encoding="utf-8", newline="\n")
    (output/"pydrop.png").write_bytes(images[-1])
    offset = 6+16*len(sizes)
    entries = []
    for size, data in zip(sizes, images):
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    (output/"pydrop.ico").write_bytes(struct.pack("<HHH", 0, 1, len(sizes))+b"".join(entries)+b"".join(images))
    return [output/name for name in ("pydrop.svg", "pydrop.png", "pydrop.ico")]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT/"assets")
    args = parser.parse_args()
    for path in generate(args.output):
        print(path)
