"""Validate palette.toml and report contrast, for every appearance it defines.

    python3 scripts/check.py

Fails (exit 1) if a reference or rule is broken, a derived color's stored hex is
stale, a syntax role doesn't use a
readable.* color or falls below 4.5:1 on any surface code sits on (background,
current line, selection, diff lines, widgets), interface text (file names,
muted text, links, line numbers, text on the status bar and other fills) falls
below 4.5:1 on a surface listed for it in rhino.py, terminal text falls below its
minimum on the terminal background or selection, the cursor stands out less than
3:1 against anything it's drawn over or no text color reads on it, two different syntax colors
are too close to tell apart, or the [light] section leaves a role undefined.

The surface lists are written by hand: the check covers the pairings they name,
not every place a tool might draw a color. Build targets should add the pairs
their tool really uses.
"""

from __future__ import annotations

import itertools
import sys

import rhino

TEXT_MIN = rhino.TEXT_MIN
HUE_TOLERANCE = 12.0  # degrees; readable colors may be nudged apart in hue, but no further
MIN_DISTANCE = 0.08   # OKLab; closer than this, two syntax colors read as the same at a glance
LIGHT_SECTIONS = {"colors", "ui", "terminal"}
TERMINAL_MIN = rhino.TERMINAL_MIN  # see rhino.py: normals 4.5:1, brights and bright black 3:1


def completeness(data: dict) -> list[str]:
    """[light] must define every UI and terminal role, and every readable color dark has."""
    light = data.get("light")
    if light is None:
        return []
    errors = []
    if extra := set(light) - LIGHT_SECTIONS:
        errors.append(f"[light] can only override {', '.join(sorted(LIGHT_SECTIONS))}, not {', '.join(sorted(extra))}")
    for section in ("ui", "terminal"):
        dark_keys, light_keys = set(data.get(section, {})), set(light.get(section, {}))
        if missing := dark_keys - light_keys:
            errors.append(f"[light.{section}] is missing {', '.join(sorted(missing))}")
        if extra := light_keys - dark_keys:
            errors.append(f"[light.{section}] defines roles dark doesn't have: {', '.join(sorted(extra))}")
    dark_readable = set(data.get("colors", {}).get("readable", {}))
    if missing := dark_readable - set(light.get("colors", {}).get("readable", {})):
        errors.append(f"[light.colors.readable] is missing {', '.join(sorted(missing))}")
    return errors


def check_appearance(data: dict, appearance: str) -> list[str]:
    seen = rhino.view(data, appearance)
    try:
        colors = rhino.palette_colors(seen)
        roles = rhino.roles(seen)
        styles = rhino.styles(seen)
        surfaces = rhino.code_surfaces(seen, colors)
        stale = rhino.stale(data, appearance)
    except rhino.PaletteError as e:
        return [f"error: {e}"]
    errors = [f"colors.{n} is {old} but its rule gives {new}; run scripts/derive.py" for n, old, new in stale]

    for name, spec in rhino.derived_specs(seen).items():
        if spec["rule"] == "text" and abs(float(spec.get("hue_shift", 0))) > HUE_TOLERANCE:
            errors.append(f"colors.{name} shifts hue by {spec['hue_shift']} deg, more than {HUE_TOLERANCE}")
    for role, color in roles["syntax"].items():
        if not color.ref.startswith("readable."):
            errors.append(f"syntax.{role} uses {color.ref or color.hex}; syntax roles must use a readable.* color")
    for name, hex_ in colors.items():
        if name.startswith("readable."):
            for surface, hex_bg in surfaces.items():
                if rhino.contrast(hex_, hex_bg) < TEXT_MIN:
                    errors.append(f"colors.{name} is {rhino.contrast(hex_, hex_bg):.2f}:1 on the {surface} "
                                  f"({hex_bg}), below {TEXT_MIN}")

    print("Contrast on " + ", ".join(f"{s} {h}" for s, h in surfaces.items()) + "\n")
    term = roles["terminal"]
    term_bg = term["background"].hex if "background" in term else surfaces["background"]
    term_surfaces = {"terminal background": term_bg}
    if "selection" in term:  # selected terminal text keeps its colors, so they must read on the selection too
        term_surfaces["terminal selection"] = rhino.composite(term["selection"], term_bg)
    for section in ("syntax", "terminal"):
        checked = surfaces if section == "syntax" else term_surfaces
        print(f"[{section}]")
        for name, color in roles[section].items():
            if section == "terminal" and name in ("background", "selection", "cursor"):
                continue
            minimum = TEXT_MIN if section == "syntax" else TERMINAL_MIN.get(name)
            ratios = {s: rhino.contrast(rhino.composite(color, h), h) for s, h in checked.items()}
            low = [s for s, r in ratios.items() if minimum and r < minimum]
            errors += [f"{section}.{name} is {ratios[s]:.2f}:1 on the {s}, below {minimum}" for s in low]
            style = f"  ({', '.join(styles[name])})" if section == "syntax" and name in styles else ""
            cols = " ".join(f"{r:5.2f}:1" for r in ratios.values())
            print(f"  {name:16s} {color.hex_with_alpha:10s} {cols}  {color.ref}{style}"
                  f"{f'  <- below {minimum}' if low else ''}")
        print()

    # The cursor: a mark against everything it's drawn over (the terminal, and in an
    # editor every code surface), with the character under a block cursor readable on it.
    if "cursor" in term:
        cursor = rhino.composite(term["cursor"], term_bg)
        over = term_surfaces | {s: h for s, h in surfaces.items() if h not in term_surfaces.values()}
        ratios = {s: rhino.contrast(cursor, h) for s, h in over.items()}
        low = [s for s, r in ratios.items() if r < rhino.CURSOR_MIN]
        errors += [f"terminal.cursor is {ratios[s]:.2f}:1 against the {s}, below {rhino.CURSOR_MIN}" for s in low]
        text = rhino.text_on(cursor, colors)
        r_text = rhino.contrast(text, cursor)
        if r_text < TEXT_MIN:
            errors.append(f"no text color reads on terminal.cursor ({cursor}): best is {text} at {r_text:.2f}:1")
        print(f"[terminal cursor] at least {rhino.CURSOR_MIN}:1 against what it's drawn over; "
              f"text under a block cursor at {TEXT_MIN}:1")
        print(f"  cursor {cursor}  text {text} {r_text:.2f}:1")
        print("  " + "  ".join(f"{s} {r:.2f}:1" for s, r in ratios.items())
              + (f"  <- below {rhino.CURSOR_MIN}" if low else "") + "\n")

    # Interface text, on each surface it's drawn on (translucent text is blended onto that surface).
    ui = roles["ui"]
    bg = surfaces["background"]
    ui_surfaces = {label: rhino.composite(ui[role], bg) for label, role in rhino.UI_SURFACES.items() if role in ui}
    print("[ui text] on " + ", ".join(f"{s} {h}" for s, h in ui_surfaces.items()))
    for role, where in rhino.UI_TEXT_ROLES.items():
        if role not in ui:
            errors.append(f"ui.{role} is missing")
            continue
        color = ui[role]
        ratios = {s: rhino.contrast(rhino.composite(color, ui_surfaces[s]), ui_surfaces[s])
                  for s in where if s in ui_surfaces}
        low = [s for s, r in ratios.items() if r < TEXT_MIN]
        errors += [f"ui.{role} is {ratios[s]:.2f}:1 on the {s}, below {TEXT_MIN}" for s in low]
        cols = "  ".join(f"{s} {r:5.2f}:1" for s, r in ratios.items())
        print(f"  {role:20s} {color.hex_with_alpha:10s} {cols}{f'  <- below {TEXT_MIN}' if low else ''}")
    print(f"\n[ui dim] dim text and marks, at least {rhino.DIM_MIN}:1")
    for role, where in rhino.UI_DIM_ROLES.items():
        if role not in ui:
            errors.append(f"ui.{role} is missing")
            continue
        color = ui[role]
        ratios = {s: rhino.contrast(rhino.composite(color, ui_surfaces[s]), ui_surfaces[s])
                  for s in where if s in ui_surfaces}
        low = [s for s, r in ratios.items() if r < rhino.DIM_MIN]
        errors += [f"ui.{role} is {ratios[s]:.2f}:1 on the {s}, below {rhino.DIM_MIN}" for s in low]
        cols = "  ".join(f"{s} {r:5.2f}:1" for s, r in ratios.items())
        print(f"  {role:20s} {color.hex_with_alpha:10s} {cols}{f'  <- below {rhino.DIM_MIN}' if low else ''}")
    print("\n[ui fills] text on colored fills")
    for fill, text_roles in rhino.UI_FILLS.items():
        if fill not in ui:
            continue
        under = rhino.composite(ui[fill], bg)
        for text_role in text_roles:
            if text_role not in ui:
                errors.append(f"ui.{fill} carries text, so ui.{text_role} must say what color that text is")
                continue
            r = rhino.contrast(rhino.composite(ui[text_role], under), under)
            if r < TEXT_MIN:
                errors.append(f"ui.{text_role} is {r:.2f}:1 on ui.{fill} ({under}), below {TEXT_MIN}")
            print(f"  {text_role:28s} {ui[text_role].hex:8s} on {fill:17s} {under}  {r:5.2f}:1"
                  f"{f'  <- below {TEXT_MIN}' if r < TEXT_MIN else ''}")
    print()

    by_hex: dict[str, list[str]] = {}
    for name, color in roles["syntax"].items():
        by_hex.setdefault(color.hex, []).append(name)
    pairs = sorted((rhino.distance(a, b), a, b) for a, b in itertools.combinations(by_hex, 2))
    if pairs:
        d, a, b = pairs[0]
        print(f"Closest syntax colors: {by_hex[a][0]} {a} and {by_hex[b][0]} {b}, {d:.3f} apart "
              f"(minimum {MIN_DISTANCE})\n")
    errors += [f"syntax colors {a} ({', '.join(by_hex[a])}) and {b} ({', '.join(by_hex[b])}) are only "
               f"{d:.3f} apart, below {MIN_DISTANCE}" for d, a, b in pairs if d < MIN_DISTANCE]
    return errors


def main() -> int:
    data = rhino.load()
    errors = completeness(data)
    names = rhino.appearances(data)
    for appearance in names:
        print(f"===== {appearance} =====\n")
        errors += [f"{appearance}: {e}" for e in check_appearance(data, appearance)]

    if errors:
        print("Problems:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"OK ({' and '.join(names)}): rules and references hold, code, interface and terminal text meet "
          "their minimum contrast on every surface listed for them in rhino.py, and syntax colors stay "
          "distinguishable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
