"""The Critical Rhino logo: a CR monogram over an orange bar, in palette colors.

    python3 scripts/logo.py           # write docs/images/{logo,icon}-{dark,light}.svg
    python3 scripts/logo.py --check   # fail (exit 1) if they're stale

The letters are outlines of Archivo (SIL Open Font License) at width 125 and
weight 850, the wordmark's face, so the logo needs no font to render. The ink is
each variant's text color and the bar is the palette's orange.

icon_png() draws the icon as a PNG, for the VS Code extension: the stores refuse
SVG icons. It rasterizes the same outlines with plain arithmetic (16 samples down
each pixel, exact coverage across it), so every machine draws the same pixels.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
import zlib

import rhino

OUT_DIR = rhino.ROOT / "docs" / "images"

# Glyph outlines in font units (1000 per em, y up, baseline at 0, cap height 688).
C_PATH = ("M487.2 -12Q340.5 -12 243.4 28.5Q146.3 69 98.2 148.1Q50 227.2 50 344Q50 514.8 161.9 607.3"
          "Q273.7 699.8 488.5 699.8Q607.6 699.8 698.1 663.3Q788.5 626.8 839.5 561.4Q890.6 495.9 890.6 408.7"
          "H671.3Q671.3 447.8 648.1 477.1Q624.8 506.4 582.9 522.6Q541 538.8 484.8 538.8Q417.1 538.8 371.7 515.2"
          "Q326.4 491.7 304 450.6Q281.7 409.6 281.7 357V332Q281.7 280.7 304 239.2Q326.4 197.6 370.7 173.3"
          "Q415 149 481.1 149Q542.7 149 586.6 164.3Q630.6 179.6 654.1 208.1Q677.7 236.6 677.7 275.6H890.8"
          "Q890.8 191.1 842.2 126Q793.6 60.9 703.3 24.5Q612.9 -12 487.2 -12Z")
R_PATH = ("M79.3 0V687.8H663.4Q739.5 687.8 789.8 656.3Q840.2 624.9 865.5 573.7Q890.8 522.5 890.8 462.7"
          "Q890.8 395.6 860.1 342.1Q829.4 288.6 768.8 257L907 0H652.7L543.7 221.8H304.6V0Z"
          "M304.6 382.6H586.7Q619 382.6 639.5 402.4Q660 422.1 660 455.9Q660 478.7 650.5 494.6"
          "Q641 510.4 624.8 518.6Q608.6 526.8 586.7 526.8H304.6Z")
R_X = 905                     # where the R starts: the C's advance (941), tightened like the wordmark
INK_LEFT, INK_RIGHT = 50, R_X + 907
BAR_GAP, BAR_HEIGHT = 96, 112   # below the baseline; about a sixth of the cap height
TOP, BOTTOM = 700, -(BAR_GAP + BAR_HEIGHT)


def _shapes(ink: str, accent: str) -> str:
    width = INK_RIGHT - INK_LEFT
    return (f'<g transform="scale(1 -1)">'
            f'<path fill="{ink}" d="{C_PATH}"/>'
            f'<path fill="{ink}" transform="translate({R_X} 0)" d="{R_PATH}"/>'
            f'<rect fill="{accent}" x="{INK_LEFT}" y="{BOTTOM}" width="{width}" height="{BAR_HEIGHT}" rx="14"/>'
            f'</g>')


def mark(ink: str, accent: str, *, height: str | None = None, label: str = "Critical Rhino") -> str:
    """The monogram on its own, cropped to its ink with a little room."""
    pad = 24
    x, y = INK_LEFT - pad, -TOP - pad
    w, h = INK_RIGHT - INK_LEFT + 2 * pad, TOP - BOTTOM + 2 * pad
    size = f' height="{height}"' if height else ""
    a11y = f'role="img" aria-label="{label}"' if label else 'aria-hidden="true"'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x} {y} {w} {h}"{size} {a11y}>'
            f'{_shapes(ink, accent)}</svg>')


def _icon_box() -> tuple[float, float, int, int]:
    """(x, y, side, corner radius) of the icon's rounded square, in the logo's SVG coordinates."""
    width = INK_RIGHT - INK_LEFT
    side = round(width / 0.8)   # the letters fill 80% of the width, so they hold up at favicon sizes
    cx, cy = (INK_LEFT + INK_RIGHT) / 2, (TOP + BOTTOM) / 2
    return cx - side / 2, -cy - side / 2, side, round(side * 0.22)


def icon(ink: str, accent: str, ground: str, *, size: int | None = None) -> str:
    """The monogram centered on a rounded square, for app icons, marketplaces and favicons."""
    x, y, side, radius = _icon_box()
    dims = f' width="{size}" height="{size}"' if size else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x:g} {y:g} {side} {side}"{dims} '
            f'role="img" aria-label="Critical Rhino">'
            f'<rect x="{x:g}" y="{y:g}" width="{side}" height="{side}" rx="{radius}" fill="{ground}"/>'
            f'{_shapes(ink, accent)}</svg>')


# --- The icon as a PNG ----------------------------------------------------------

Point = tuple[float, float]
CURVE_STEPS = 16   # line segments per letter curve: none turns far, so each stays within 0.01 px
CORNER_STEPS = 64  # per corner of the square, a quarter turn: within 0.01 px of the arc at 256 px
SUBROWS = 16       # coverage samples down each pixel


def _outline(d: str, dx: float = 0) -> list[list[Point]]:
    """The subpaths of an outline (absolute M, L, H, V, Q and Z only) as polygons, in SVG
    coordinates: font units flipped to y down, as _shapes() draws them, and moved dx across."""
    tokens = re.findall(r"[A-Za-z]|-?\d*\.?\d+", d)
    polygons: list[list[Point]] = []
    polygon: list[Point] = []
    x = y = 0.0
    i, cmd = 0, ""

    def nums(n: int) -> list[float]:
        nonlocal i
        i += n
        return [float(t) for t in tokens[i - n:i]]

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
        if cmd == "Z":
            polygons.append(polygon)
            polygon = []
            cmd = ""
        elif cmd == "M":
            if polygon:
                polygons.append(polygon)
            x, y = nums(2)
            polygon = [(x, y)]
            cmd = "L"   # numbers after a moveto are linetos
        elif cmd == "L":
            x, y = nums(2)
            polygon.append((x, y))
        elif cmd == "H":
            (x,) = nums(1)
            polygon.append((x, y))
        elif cmd == "V":
            (y,) = nums(1)
            polygon.append((x, y))
        elif cmd == "Q":
            qx, qy, ex, ey = nums(4)
            for k in range(1, CURVE_STEPS + 1):
                t = k / CURVE_STEPS
                a, b, c = (1 - t) * (1 - t), 2 * (1 - t) * t, t * t
                polygon.append((a * x + b * qx + c * ex, a * y + b * qy + c * ey))
            x, y = ex, ey
        else:
            raise ValueError(f"unsupported path command {cmd!r}")
    if polygon:
        polygons.append(polygon)
    return [[(px + dx, -py) for px, py in p] for p in polygons]


def _rounded_rect(x: float, y: float, w: float, h: float, r: float) -> list[Point]:
    """A rounded rectangle as a polygon. Each corner is the usual cubic quarter-circle, which
    keeps to arithmetic (no sin or cos) so every machine computes the same points."""
    k = r * 0.5523   # control-point distance that best fits a quarter circle
    corners = [  # (start, control 1, control 2, end), clockwise from the top edge's right end
        ((x + w - r, y), (x + w - r + k, y), (x + w, y + r - k), (x + w, y + r)),
        ((x + w, y + h - r), (x + w, y + h - r + k), (x + w - r + k, y + h), (x + w - r, y + h)),
        ((x + r, y + h), (x + r - k, y + h), (x, y + h - r + k), (x, y + h - r)),
        ((x, y + r), (x, y + r - k), (x + r - k, y), (x + r, y)),
    ]
    points: list[Point] = []
    for p0, p1, p2, p3 in corners:
        for step in range(CORNER_STEPS + 1):
            t = step / CORNER_STEPS
            a, b, c, e = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
            points.append((a * p0[0] + b * p1[0] + c * p2[0] + e * p3[0],
                           a * p0[1] + b * p1[1] + c * p2[1] + e * p3[1]))
    return points


def _add_span(line: list[float], whole: list[float], xa: float, xb: float, share: float) -> None:
    """Add one sample row's span from xa to xb to a pixel row: the pixels at its ends by how
    much of them it crosses into line, the whole pixels between as a running difference."""
    size = len(line)
    xa, xb = max(0.0, min(size, xa)), max(0.0, min(size, xb))
    if xb <= xa:
        return
    ia, ib = int(xa), int(xb)
    if ia == ib:
        line[ia] += (xb - xa) * share
        return
    line[ia] += (ia + 1 - xa) * share
    whole[ia + 1] += share
    whole[ib] -= share
    if ib < size:
        line[ib] += (xb - ib) * share


def _coverage(polygons: list[list[Point]], size: int) -> list[float]:
    """How much of each pixel the shape covers (nonzero fill, as SVG draws it), row by row."""
    edges = []
    for poly in polygons:
        for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
            if y0 != y1:
                up = y0 > y1
                if up:
                    x0, y0, x1, y1 = x1, y1, x0, y0
                edges.append((y0, y1, x0, (x1 - x0) / (y1 - y0), -1 if up else 1))
    edges.sort()
    cover: list[float] = []
    share = 1 / SUBROWS
    active: list[tuple] = []
    nxt = 0
    for row in range(size):
        line, whole = [0.0] * size, [0.0] * (size + 1)
        for sub in range(SUBROWS):
            sy = row + (sub + 0.5) * share
            while nxt < len(edges) and edges[nxt][0] <= sy:
                active.append(edges[nxt])
                nxt += 1
            active = [e for e in active if e[1] > sy]
            winding, start = 0, 0.0
            for x, direction in sorted((e[2] + (sy - e[0]) * e[3], e[4]) for e in active):
                if winding == 0:
                    start = x
                winding += direction
                if winding == 0:
                    _add_span(line, whole, start, x, share)
        running = 0.0
        for col in range(size):
            running += whole[col]
            cover.append(min(1.0, line[col] + running))
    return cover


def _png(width: int, height: int, raw: bytes) -> bytes:
    """An 8-bit RGBA PNG from rows of pixels, each row led by filter byte 0."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def png_pixels(data: bytes) -> tuple[bytes, bytes]:
    """(header, rows) of a PNG _png() wrote, to compare pixels: zlib builds may compress the
    same rows to different bytes."""
    pos, header, body = 8, b"", b""
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"IHDR":
            header = data[pos + 8:pos + 8 + length]
        elif kind == b"IDAT":
            body += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    return header, zlib.decompress(body) if body else b""


def icon_png(ink: str, accent: str, ground: str, size: int = 256) -> bytes:
    """icon() as a size x size PNG, transparent outside the rounded square."""
    x0, y0, side, radius = _icon_box()
    scale = size / side

    def to_pixels(polygons: list[list[Point]]) -> list[list[Point]]:
        return [[((px - x0) * scale, (py - y0) * scale) for px, py in p] for p in polygons]

    layers = [
        (ground, to_pixels([_rounded_rect(x0, y0, side, side, radius)])),
        (ink, to_pixels(_outline(C_PATH) + _outline(R_PATH, R_X))),
        (accent, to_pixels([_rounded_rect(INK_LEFT, -BOTTOM - BAR_HEIGHT, INK_RIGHT - INK_LEFT, BAR_HEIGHT, 14)])),
    ]
    rgba = [[0.0, 0.0, 0.0, 0.0] for _ in range(size * size)]
    for color, polygons in layers:
        rgb = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
        for px, a in zip(rgba, _coverage(polygons, size)):
            if a > 0:   # source over: the layer's color at its coverage over what's there
                below = px[3] * (1 - a)
                out = a + below
                px[:3] = [(c * a + d * below) / out for c, d in zip(rgb, px[:3])]
                px[3] = out
    raw = bytearray()
    for row in range(size):
        raw.append(0)
        for r, g, b, a in rgba[row * size:(row + 1) * size]:
            alpha = round(a * 255)
            raw += bytes((round(r), round(g), round(b), alpha) if alpha else (0, 0, 0, 0))
    return _png(size, size, bytes(raw))


def palette_colors(data: dict, appearance: str) -> tuple[str, str, str]:
    """(ink, accent, ground) for one variant: its text color, the palette's orange, its background."""
    seen = rhino.view(data, appearance)
    ui = rhino.roles(seen)["ui"]
    return ui["foreground"].hex, rhino.palette_colors(seen)["orange"], ui["background"].hex


def outputs(data: dict) -> dict:
    files = {}
    for appearance in rhino.appearances(data):
        ink, accent, ground = palette_colors(data, appearance)
        files[OUT_DIR / f"logo-{appearance}.svg"] = mark(ink, accent) + "\n"
        files[OUT_DIR / f"icon-{appearance}.svg"] = icon(ink, accent, ground) + "\n"
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Draw the logo from palette.toml.")
    parser.add_argument("--check", action="store_true", help="fail if the committed logo files are stale")
    args = parser.parse_args()
    files = outputs(rhino.load())
    stale = [p for p, text in files.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
    if args.check:
        for p in stale:
            print(f"- {p.relative_to(rhino.ROOT)} is stale; run make logo")
        if stale:
            return 1
        print(f"OK: the logo is current ({len(files)} files).")
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in stale:
        path.write_text(files[path], encoding="utf-8")
        print(f"wrote {path.relative_to(rhino.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
