"""Load palette.toml and resolve color roles. Standard library only (Python 3.11+)."""

from __future__ import annotations

import copy
import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PALETTE_PATH = ROOT / "palette.toml"
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
ROLE_SECTIONS = ("syntax", "ui", "terminal")
STYLE_VALUES = ("bold", "italic", "underline", "strikethrough")
TEXT_MIN = 4.5
# Overlays that code is drawn on, besides the background itself. Hovers, peek views
# and the suggest widget show code on the widget surface.
SURFACE_ROLES = {"current line": "current_line", "selection": "editor_selection",
                 "inserted line": "diff_inserted_bg", "removed line": "diff_removed_bg",
                 "changed line": "diff_changed_bg", "changed text": "diff_text_bg",
                 "widget": "surface", "word highlight": "word_highlight"}
RULE_KEYS = {
    "text": {"from", "hue_shift", "min", "exact"},  # move lightness until readable on every code surface
    "tint": {"from", "by"},                 # keep lightness, take the hue and tint strength of `by`
    "bright": {"from"},                     # +#333333 per channel
    "fill": {"from", "text", "min"},        # move lightness until `text` is readable on it
    "wash": {"from", "alpha", "chroma"},    # the lightness of `from` at `alpha` over the background, in its hue
}

# Interface text: each UI role drawn as text, and the surfaces it's drawn on.
# These lists are written by hand, so the check covers only the pairings listed
# here. Build targets should check every foreground against the backgrounds it
# actually lands on in that tool, and add any missing pairs.
UI_SURFACES = {"background": "background", "side bar": "surface", "raised": "surface_raised",
               "current line": "current_line", "soft selection": "selection_soft", "selection": "editor_selection",
               "hover": "hover"}
UI_TEXT_ROLES = {
    "foreground": ("background", "side bar", "raised", "hover"),
    # inactive tabs, placeholders in inputs; in editors also inlay hints and concealed
    # text, which can sit inside a selection
    "muted_foreground": ("background", "side bar", "raised", "current line", "selection", "hover"),
    "git_added": ("background", "side bar", "hover"),            # file names in tabs and the explorer
    "git_modified": ("background", "side bar", "hover"),
    "git_deleted": ("background", "side bar", "hover"),
    "git_conflict": ("background", "side bar", "hover"),
    "link": ("background", "side bar"),
    "match_highlight": ("background", "side bar", "soft selection", "hover"),
    "error": ("background", "side bar", "hover"),
    "warning": ("background", "side bar", "hover"),
    "info": ("background", "side bar", "hover"),
    "hint": ("background", "side bar", "hover"),
    "line_numbers": ("background",),
    "line_number_active": ("current line",),
}
# Dim text (ghost text, backdrops) and marks that aren't text (git gutter signs):
# 3:1, the WCAG minimum for meaningful graphics.
DIM_MIN = 3.0
# Terminal text: programs print mostly in the six colored normals; brights are the
# lighter set. White is secondary text and bright black dim text (shell
# suggestions). Black and bright white are exempt: in each variant one is the text
# color and the other nearly the background.
TERMINAL_COLORS = ("red", "green", "yellow", "blue", "magenta", "cyan")
BRIGHT_MIN = 3.0
TERMINAL_MIN = {"foreground": TEXT_MIN, "white": TEXT_MIN, "bright_black": BRIGHT_MIN,
                **{c: TEXT_MIN for c in TERMINAL_COLORS},
                **{f"bright_{c}": BRIGHT_MIN for c in TERMINAL_COLORS}}
# The cursor is a mark, not text: 3:1 against everything it's drawn over (in an
# editor that includes the current line, selection and popups). The character
# under a block cursor is text: 4.5:1.
CURSOR_MIN = DIM_MIN
UI_DIM_ROLES = {
    "dim_foreground": ("background", "side bar", "current line"),
    "focus": ("background", "side bar", "raised"),   # focus rings and active marks, beside whatever they outline
    "gutter_added": ("background", "current line"),
    "gutter_modified": ("background", "current line"),
    "gutter_deleted": ("background", "current line"),
    "gutter_staged_added": ("background", "current line"),
    "gutter_staged_modified": ("background", "current line"),
    "gutter_staged_deleted": ("background", "current line"),
}
# Colored fills that carry text, and the roles drawn on them (the first is the fill's main text).
UI_FILLS = {"accent": ("accent_foreground",),
            "selection": ("selection_foreground", "match_highlight_selected"),
            "badge": ("badge_foreground",),
            "status_debugging": ("status_debugging_foreground",),
            "status_no_folder": ("status_no_folder_foreground",),
            "status_remote": ("status_remote_foreground",),
            "filter_widget": ("filter_widget_foreground",),
            "search": ("search_foreground",),
            "search_current": ("search_current_foreground",)}


@dataclass(frozen=True)
class Color:
    hex: str            # "#RRGGBB", uppercase
    alpha: float = 1.0
    ref: str = ""       # palette name it came from, e.g. "readable.red" ("" for literals)

    @property
    def hex_with_alpha(self) -> str:
        """#RRGGBB, or #RRGGBBAA when alpha < 1."""
        return self.hex if self.alpha >= 1 else f"{self.hex}{round(self.alpha * 255):02X}"


class PaletteError(ValueError):
    pass


def load(path: Path = PALETTE_PATH) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


APPEARANCES = ("dark", "light")


def appearances(data: dict) -> list[str]:
    """The appearances this palette defines: always "dark", plus "light" when there's a [light] section."""
    return [a for a in APPEARANCES if a == "dark" or a in data]


def view(data: dict, appearance: str = "dark") -> dict:
    """The palette as one appearance sees it. Dark is the top level; [light] overrides it."""
    if appearance == "dark":
        return {k: v for k, v in data.items() if k not in APPEARANCES}
    if appearance not in data:
        raise PaletteError(f"palette.toml has no [{appearance}] section")
    merged = copy.deepcopy({k: v for k, v in data.items() if k not in APPEARANCES})

    def merge(dst: dict, src: dict) -> None:
        for key, value in src.items():
            nested = isinstance(value, dict) and not ({"rule", "color"} & set(value))
            if nested and isinstance(dst.get(key), dict):
                merge(dst[key], value)
            else:
                dst[key] = copy.deepcopy(value)

    merge(merged, data[appearance])
    return merged


def _walk_colors(data: dict):
    """Yield (name, value) for every color in [colors]; `value` is a hex string or a rule table."""
    def walk(table: dict, prefix: str):
        for key, value in table.items():
            name = f"{prefix}{key}"
            if isinstance(value, dict) and "rule" not in value:
                yield from walk(value, f"{name}.")
            else:
                yield name, value
    yield from walk(data.get("colors", {}), "")


def palette_colors(data: dict) -> dict[str, str]:
    """Flatten [colors] into {"red": "#AE3C38", "readable.red": "#E46B6F", ...}.

    A derived color is a rule table; its stored `hex` is what this returns.
    """
    flat: dict[str, str] = {}
    for name, value in _walk_colors(data):
        if isinstance(value, dict):
            rule = value.get("rule")
            if rule not in RULE_KEYS:
                raise PaletteError(f"colors.{name}: unknown rule {rule!r} (expected one of {', '.join(RULE_KEYS)})")
            unknown = set(value) - RULE_KEYS[rule] - {"rule", "hex"}
            if unknown:
                raise PaletteError(f"colors.{name}: rule {rule!r} doesn't take {', '.join(sorted(unknown))}")
            value = value.get("hex")
        if isinstance(value, str) and HEX_RE.match(value):
            flat[name] = value.upper()
        else:
            raise PaletteError(f"colors.{name}: expected #RRGGBB, got {value!r}")
    return flat


def derived_specs(data: dict) -> dict[str, dict]:
    """Rule tables of the derived colors: {"readable.red": {"rule": "text", "from": "red", ...}}."""
    return {name: value for name, value in _walk_colors(data) if isinstance(value, dict)}


def resolve(value, colors: dict[str, str], where: str) -> Color:
    alpha = 1.0
    if isinstance(value, dict):
        unknown = set(value) - {"color", "alpha"}
        if unknown or "color" not in value:
            raise PaletteError(f"{where}: inline table needs `color` (and optional `alpha`), got {value!r}")
        alpha = float(value.get("alpha", 1.0))
        if not 0 <= alpha <= 1:
            raise PaletteError(f"{where}: alpha must be between 0 and 1, got {alpha}")
        value = value["color"]
    if not isinstance(value, str):
        raise PaletteError(f"{where}: expected a palette name or hex color, got {value!r}")
    if HEX_RE.match(value):
        return Color(value.upper(), alpha)
    if value in colors:
        return Color(colors[value], alpha, value)
    raise PaletteError(f"{where}: unknown palette name {value!r}")


def roles(data: dict) -> dict[str, dict[str, Color]]:
    """Resolve every role: {"syntax": {"string": Color(...), ...}, "ui": {...}, "terminal": {...}}."""
    colors = palette_colors(data)
    return {
        section: {name: resolve(v, colors, f"{section}.{name}") for name, v in data.get(section, {}).items()}
        for section in ROLE_SECTIONS
    }


def code_surfaces(data: dict, colors: dict[str, str] | None = None) -> dict[str, str]:
    """Opaque color of every surface code is drawn on: {"background": ..., "current line": ..., ...}."""
    colors = palette_colors(data) if colors is None else colors
    specs = derived_specs(data)
    ui = data.get("ui", {})
    bg = resolve(ui.get("background"), colors, "ui.background").hex
    out = {"background": bg}
    for label, role in SURFACE_ROLES.items():
        if role not in ui:
            continue
        color = resolve(ui[role], colors, f"ui.{role}")
        if specs.get(color.ref, {}).get("rule") == "text":
            raise PaletteError(f"ui.{role}: a code surface can't use {color.ref}, which is measured against it")
        out[label] = composite(color, bg)
    return out


def compute(name: str, spec: dict, colors: dict[str, str], surfaces: dict[str, str]) -> str:
    """Run a derived color's rule against the current colors."""
    def ref(value) -> str:
        if isinstance(value, str) and HEX_RE.match(value):
            return value.upper()
        if value in colors:
            return colors[value]
        raise PaletteError(f"colors.{name}: unknown color {value!r}")

    src = ref(spec.get("from"))
    rule = spec["rule"]
    if rule == "bright":
        return "#" + "".join(f"{min(255, int(src[i:i + 2], 16) + 0x33):02X}" for i in (1, 3, 5))
    if rule == "tint":
        L, _, _ = oklch(src)
        Lb, Cb, Hb = oklch(ref(spec["by"]))
        out = oklch_to_hex(L, Cb / Lb * L if Lb else 0.0, Hb)
        if out is None:
            raise PaletteError(f"colors.{name}: tint falls outside sRGB")
        return out
    if rule == "fill":
        return _fill_tone(name, src, ref(spec["text"]), float(spec.get("min", TEXT_MIN)))
    if rule == "wash":
        return _wash(name, src, surfaces["background"], float(spec["alpha"]), float(spec["chroma"]))
    return _text_tone(name, src, list(surfaces.values()), float(spec.get("hue_shift", 0)),
                      float(spec.get("min", TEXT_MIN)), bool(spec.get("exact", False)))


def _wash(name: str, src: str, background: str, alpha: float, chroma: float) -> str:
    """An opaque color in `src`'s hue at `chroma`, as light as `src` at `alpha` over the
    background: never darker on a light background, never lighter on a dark one, so text
    keeps the contrast it would have on the translucent mix. Mixing a blue into cream
    cancels to gray; this keeps the lightness and the hue."""
    target = luminance(composite(Color(src, alpha), background))
    _, _, H = oklch(src)
    dark = luminance(background) < 0.18
    best = None
    for i in (range(1001) if dark else range(1000, -1, -1)):
        out = oklch_to_hex(i / 1000, chroma, H)
        if out is None:          # outside sRGB at this lightness; very dark yellows can't hold the chroma
            continue
        if luminance(out) <= target if dark else luminance(out) >= target:
            best = out
        elif best:
            return best
    raise PaletteError(f"colors.{name}: no color at chroma {chroma} matches that lightness")


def _fill_tone(name: str, src: str, text: str, target: float) -> str:
    """Keep hue and chroma, then move lightness away from `text` until `text` reaches
    `target` contrast on the fill. A fill that already passes is returned unchanged."""
    if contrast(src, text) >= target:
        return src.upper()
    L, C, H = oklch(src)
    step = -0.001 if luminance(text) > 0.18 else 0.001   # light text: darken the fill
    while 0 <= L <= 1:
        c = C
        out = oklch_to_hex(L, c, H)
        while out is None and c > 0:
            c = max(0.0, c - 0.001)
            out = oklch_to_hex(L, c, H)
        if out and contrast(out, text) >= target:
            return out
        L += step
    raise PaletteError(f"colors.{name}: no lightness gives {text} {target}:1 on this fill")


def _text_tone(name: str, src: str, surfaces: list[str], hue_shift: float, target: float,
               exact: bool = False) -> str:
    """Keep chroma, shift hue, then move lightness away from the background until the
    color reaches `target` contrast on every surface. A color that already passes
    (with no hue shift) is returned unchanged, unless `exact`: then it moves toward
    the background instead, as far as it can while still passing, so it lands on
    `target`."""
    def passes(h: str) -> bool:
        return min(contrast(h, s) for s in surfaces) >= target

    if hue_shift == 0 and passes(src) and not exact:
        return src.upper()
    L, C, H = oklch(src)
    H = (H + hue_shift) % 360

    def tone(lightness: float) -> str | None:
        c = C
        out = oklch_to_hex(lightness, c, H)
        while out is None and c > 0:
            c = max(0.0, c - 0.001)
            out = oklch_to_hex(lightness, c, H)
        return out

    step = 0.001 if luminance(surfaces[0]) < 0.18 else -0.001   # dark background: lighten
    first = tone(L)
    if exact and first and passes(first):
        best = first
        while 0 <= L - step <= 1:
            L -= step
            out = tone(L)
            if not out or not passes(out):
                break
            best = out
        return best
    while 0 <= L <= 1:
        out = tone(L)
        if out and passes(out):
            return out
        L += step
    raise PaletteError(f"colors.{name}: no lightness reaches {target}:1 on every code surface")


# --- one appearance, resolved, for the build targets ---------------------------------

@dataclass
class Variant:
    name: str
    colors: dict[str, str]
    syntax: dict[str, Color]
    ui: dict[str, Color]
    term: dict[str, Color]
    styles: dict[str, tuple[str, ...]]
    surfaces: dict[str, str]

    @property
    def bg(self) -> str:
        return self.ui["background"].hex

    def u(self, role: str, over: str | None = None) -> str:
        """A UI role as an opaque color, flattened over `over` (default: the editor background)."""
        return composite(self.ui[role], over or self.bg)

    def s(self, role: str) -> str:
        return self.syntax[role].hex

    def t(self, role: str) -> str:
        return self.term[role].hex

    def on(self, fill: str) -> str:
        """Black or cream, whichever reads better on `fill`."""
        return text_on(fill, self.colors)


def load_variant(data: dict, name: str) -> Variant:
    """One appearance ("dark" or "light") with every role resolved."""
    seen = view(data, name)
    colors = palette_colors(seen)
    resolved = roles(seen)
    return Variant(name, colors, resolved["syntax"], resolved["ui"], resolved["terminal"], styles(seen),
                   code_surfaces(seen, colors))


# Terminal palette order: index 0-7 are these, 8-15 their bright_ versions.
ANSI = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")
ANSI_NAMES = ANSI + tuple(f"bright_{n}" for n in ANSI)


def terminal_colors(v: Variant) -> list[str]:
    return [v.t(n) for n in ANSI_NAMES]


def stale(data: dict, appearance: str = "dark") -> list[tuple[str, str, str]]:
    """Derived colors defined by one appearance whose stored hex differs from what their
    rule gives in that appearance: [(name, stored, computed)]."""
    seen = view(data, appearance)
    colors = palette_colors(seen)
    surfaces = code_surfaces(seen, colors)
    own = data if appearance == "dark" else data.get(appearance, {})
    out = []
    for name, spec in derived_specs(own).items():
        computed = compute(name, spec, colors, surfaces)
        if computed != colors[name]:
            out.append((name, colors[name], computed))
    return out


def styles(data: dict) -> dict[str, tuple[str, ...]]:
    """Font styles per syntax role: {"constant": ("italic",), ...}. Unlisted roles are plain."""
    syntax = data.get("syntax", {})
    out: dict[str, tuple[str, ...]] = {}
    for role, value in data.get("styles", {}).items():
        if role not in syntax:
            raise PaletteError(f"styles.{role}: not a syntax role")
        if not isinstance(value, list) or not all(v in STYLE_VALUES for v in value):
            raise PaletteError(f"styles.{role}: expected a list drawn from {', '.join(STYLE_VALUES)}, got {value!r}")
        out[role] = tuple(value)
    return out


# --- color math ------------------------------------------------------------

def _rgb(hex_: str) -> tuple[float, float, float]:
    h = hex_.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def composite(color: Color, background: str) -> str:
    """Flatten a translucent color over an opaque background."""
    if color.alpha >= 1:
        return color.hex
    fg, bg = _rgb(color.hex), _rgb(background)
    mixed = (color.alpha * f + (1 - color.alpha) * b for f, b in zip(fg, bg))
    return "#" + "".join(f"{round(c * 255):02X}" for c in mixed)


def composite_linear(color: Color, background: str) -> str:
    """Flatten a translucent color over an opaque background, blending in linear light
    (as GPU renderers with gamma-correct blending do) instead of on sRGB values."""
    if color.alpha >= 1:
        return color.hex
    fg, bg = _rgb(color.hex), _rgb(background)
    mixed = (_from_linear(color.alpha * _to_linear(f) + (1 - color.alpha) * _to_linear(b)) for f, b in zip(fg, bg))
    return "#" + "".join(f"{round(min(max(c, 0.0), 1.0) * 255):02X}" for c in mixed)


def luminance(hex_: str) -> float:
    r, g, b = (_to_linear(c) for c in _rgb(hex_))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def text_on(fill: str, colors: dict[str, str]) -> str:
    """The palette's black or cream, whichever reads better on `fill`."""
    a, b = colors["black"], colors["white"]
    return a if contrast(a, fill) >= contrast(b, fill) else b


def contrast(a: str, b: str) -> float:
    """WCAG 2 contrast ratio between two opaque colors."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def oklch(hex_: str) -> tuple[float, float, float]:
    """(lightness 0-1, chroma, hue degrees)."""
    r, g, b = (_to_linear(c) for c in _rgb(hex_))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = (math.copysign(abs(x) ** (1 / 3), x) for x in (l, m, s))
    L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
    A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    B = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    return L, math.hypot(A, B), math.degrees(math.atan2(B, A)) % 360


def _lab(L: float, C: float, H: float) -> tuple[float, float, float]:
    return L, C * math.cos(math.radians(H)), C * math.sin(math.radians(H))


def distance(a: str, b: str) -> float:
    """Perceptual distance between two colors (Euclidean distance in OKLab)."""
    return math.dist(_lab(*oklch(a)), _lab(*oklch(b)))


def _from_linear(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def oklch_to_hex(L: float, C: float, H: float) -> str | None:
    """The sRGB hex closest (in OKLab) to an OKLCH color, or None if it lies outside sRGB."""
    _, A, B = _lab(L, C, H)
    l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3
    m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3
    s = (L - 0.0894841775 * A - 1.2914855480 * B) ** 3
    lin = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
           -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
    if not all(-1e-4 <= x <= 1 + 1e-4 for x in lin):
        return None
    rgb = [_from_linear(min(1.0, max(0.0, x))) * 255 for x in lin]
    # Rounding each channel separately can shift the hue of near-grays; take the
    # floor/ceil combination that lands closest to the target instead.
    target = (L, A, B)
    best = None
    for r in {math.floor(rgb[0]), math.ceil(rgb[0])}:
        for g in {math.floor(rgb[1]), math.ceil(rgb[1])}:
            for b in {math.floor(rgb[2]), math.ceil(rgb[2])}:
                h = "#" + "".join(f"{min(255, max(0, v)):02X}" for v in (r, g, b))
                d = math.dist(target, _lab(*oklch(h)))
                if best is None or d < best[0]:
                    best = (d, h)
    return best[1]
