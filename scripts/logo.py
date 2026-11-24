"""The Critical Rhino logo: a CR monogram over an orange bar, in palette colors.

    python3 scripts/logo.py           # write docs/images/{logo,icon}-{dark,light}.svg
    python3 scripts/logo.py --check   # fail (exit 1) if they're stale

The letters are outlines of Archivo (SIL Open Font License) at width 125 and
weight 850, the wordmark's face, so the logo needs no font to render. The ink is
each variant's text color and the bar is the palette's orange.
"""

from __future__ import annotations

import argparse
import sys

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


def icon(ink: str, accent: str, ground: str, *, size: int | None = None) -> str:
    """The monogram centered on a rounded square, for app icons, marketplaces and favicons."""
    width = INK_RIGHT - INK_LEFT
    side = round(width / 0.8)   # the letters fill 80% of the width, so they hold up at favicon sizes
    cx, cy = (INK_LEFT + INK_RIGHT) / 2, (TOP + BOTTOM) / 2
    x, y = cx - side / 2, -cy - side / 2
    dims = f' width="{size}" height="{size}"' if size else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x:g} {y:g} {side} {side}"{dims} '
            f'role="img" aria-label="Critical Rhino">'
            f'<rect x="{x:g}" y="{y:g}" width="{side}" height="{side}" rx="{round(side * 0.22)}" fill="{ground}"/>'
            f'{_shapes(ink, accent)}</svg>')


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
