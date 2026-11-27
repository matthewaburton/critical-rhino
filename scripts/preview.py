"""Generate build/site/, the Critical Rhino site, from palette.toml.

    python3 scripts/preview.py
    python3 scripts/preview.py --compare main   # add a before/after section against a git ref (for review)

The site is one page, index.html, plus the README's screenshots and the icons, so
build/site/ is ready to serve as it is: the release workflow deploys it to GitHub
Pages. The page shows both variants. It follows the viewer's light or dark setting
and has its own Dark / Light switch; the page itself is drawn in the scheme's colors.

Code samples use a tiny markup: «role|text» colors `text` with a syntax role
(or a terminal slot in the terminal sample); «role+b|text» also makes it bold.
"""

from __future__ import annotations

import argparse
import html
import itertools
import re
import shutil
import struct
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import build_vscode
import logo
import rhino

SITE = rhino.ROOT / "build" / "site"
IMAGES = rhino.ROOT / "docs" / "images"
DESCRIPTION = ("A warm color scheme for editors and terminals, in dark and light, where every color the theme "
               "sets is measured on every surface it's drawn on.")
# Google Search Console checks for this tag on the home page to confirm who owns the
# site, and keeps checking: removing it drops the site's verification.
GOOGLE_SITE_VERIFICATION = "HXY2H8T_S8hxtPYDOTLM9YsMuG43a_4o2Q7j0cDauk8"
TOKEN_RE = re.compile(r"«([\w+]+)\|(.*?)»", re.DOTALL)
VARIANTS = ("dark", "light")
DISPLAY_NAMES = {"gray_dark": "Gray (Dark)", "gray_light": "Gray (Light)", "red_muted": "Red (Muted)",
                 "terminal.cyan": "Cyan", "foreground": "Text"}

# Palette cards, in display order. Colors not listed here land in the second group.
# Accents go around the hue circle, so neighbors in the hero strip blend instead of clashing.
ACCENTS = ("red", "orange", "yellow", "green", "terminal.cyan", "blue", "purple")
GROUNDS = ("black", "white", "loden", "gray_accent_2", "gray_dark", "gray_light", "gray_accent_1",
           "blue_accent_1", "blue_accent_2", "red_muted")

# Text colors in the readability grid, with the role each is best known for.
TEXT_ROWS = {"foreground": "code text", "orange": "keywords", "red": "strings", "yellow": "decorators",
             "green": "comments", "blue": "types", "purple": "numbers", "blue_accent_1": "links"}

SYNTAX_LABELS = {
    "text": "code text", "comment": "comments", "doc_comment": "doc comments", "doc_tag": "doc tags",
    "todo": "TODO markers", "keyword": "keywords", "string": "strings", "string_escape": "escapes",
    "regex": "regular expressions", "number": "numbers", "constant": "constants", "type": "types",
    "builtin": "built-ins", "decorator": "decorators", "interpolation": "interpolation", "invalid": "invalid code",
    "heading": "headings", "bold": "bold", "italic": "italics", "link_text": "link text", "link_url": "link URLs",
    "quote": "quotes", "inline_code": "inline code", "diff_header": "diff headers",
    "diff_inserted": "inserted lines", "diff_deleted": "deleted lines",
}
SYNTAX_COVERS = {
    "text": "identifiers, functions, parameters, properties, operators",
    "comment": "line and block comments",
    "doc_comment": "docstrings and /** */ blocks",
    "doc_tag": "@param, @return and other tags in doc comments",
    "todo": "TODO and FIXME inside comments",
    "keyword": "control flow, storage, modifiers, true / false / null",
    "string": "template literals, attribute values, JSON values",
    "string_escape": "\\n, \\t and other escapes in strings",
    "regex": "drawn as plain text, so patterns stay calm",
    "number": "integers, floats, hex and other numeric literals",
    "constant": "named constants, enum members, static fields",
    "type": "classes, interfaces, enums, type parameters",
    "builtin": "built-in functions and types, self and cls",
    "decorator": "decorators and annotations",
    "interpolation": "${ } and other embedded-code delimiters",
    "invalid": "invalid escapes, unresolved references",
    "heading": "Markdown # headings", "bold": "Markdown **strong** text", "italic": "Markdown *emphasis*",
    "link_text": "the [text] part of a Markdown link", "link_url": "the (target) part of a Markdown link",
    "quote": "Markdown > block quotes", "inline_code": "Markdown `code spans`",
    "diff_header": "file names and @@ hunk headers", "diff_inserted": "lines starting with + in a diff",
    "diff_deleted": "lines starting with - in a diff",
}
STYLE_WORDS = {"bold": "bold", "italic": "italic", "underline": "underlined", "strikethrough": "struck through"}
SPECIMENS = {
    "text": "herd.length", "comment": "# from drone photos", "doc_comment": '"""One sighting."""', "doc_tag": "@param",
    "todo": "TODO", "keyword": "return", "string": '"untagged"', "string_escape": "\\n", "regex": "/^(\\w+),/",
    "number": "3600", "constant": "MAX_WEIGHT_KG", "type": "Rhino", "builtin": "self", "decorator": "@dataclass",
    "interpolation": "${ }", "invalid": "\\q", "heading": "# Reserve log", "bold": "**adult**", "italic": "*north*",
    "link_text": "the sightings", "link_url": "(sightings.csv)", "quote": "> At dawn", "inline_code": "`2,140 kg`",
    "diff_header": "@@ -17,4 +17,5 @@", "diff_inserted": "+ top = max(…)", "diff_deleted": "- # TODO",
}
UI_LABELS = {
    "background": "editor background", "foreground": "UI text", "surface": "side bar and tabs",
    "surface_raised": "title bar and inputs", "border": "borders", "badge": "badges",
    "muted_foreground": "muted text", "line_numbers": "line numbers", "line_number_active": "active line number",
    "current_line": "current line", "ruler": "ruler", "bracket_match": "matching brackets", "accent": "status bar",
    "selection": "list selection", "selection_soft": "soft selection", "editor_selection": "text selection",
    "link": "links", "match_highlight": "match highlights", "filter_widget": "list filter box",
    "error": "errors", "warning": "warnings", "info": "info",
    "git_added": "added files", "git_modified": "modified files", "git_deleted": "deleted files",
    "git_conflict": "conflicts", "gutter_added": "added-line marks", "gutter_modified": "changed-line marks",
    "gutter_deleted": "deleted-line marks", "diff_inserted_bg": "inserted-line tint",
    "diff_removed_bg": "removed-line tint", "status_debugging": "debugging status bar",
    "status_no_folder": "no-folder status bar", "status_remote": "remote indicator",
    "accent_foreground": "status bar text", "selection_foreground": "selected item text",
    "badge_foreground": "badge text", "filter_widget_foreground": "filter box text",
    "status_debugging_foreground": "debugging status text", "status_no_folder_foreground": "no-folder status text",
    "status_remote_foreground": "remote indicator text", "match_highlight_selected": "matches in the selected row",
}
TERMINAL_LABELS = {"foreground": "text"}
SECTION_NAMES = {"syntax": "Code", "ui": "UI", "terminal": "Terminal"}
SURFACE_NAMES = {"background": "Background", "current line": "Current line", "selection": "Selection",
                 "inserted line": "Inserted", "removed line": "Removed", "widget": "Widgets"}
ANSI_SLOTS = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")

# --- samples -----------------------------------------------------------------

SAMPLES = {
    "Python": r'''
«keyword|from» dataclasses «keyword|import» dataclass

«comment|# Field notes: weights are estimated from drone photos.»
«constant|MAX_WEIGHT_KG» = «number|3600»

«decorator|@dataclass»(frozen=«keyword|True»)
«keyword|class» «type|Rhino»:
    «doc_comment|"""One sighting from the reserve's field log."""»
    name: «builtin|str»
    weight_kg: «builtin|float» = «number|0.0»
    tagged: «builtin|bool» = «keyword|False»

    «keyword|def» describe(«builtin|self») -> «builtin|str»:
        status = «string|"tagged"» «keyword|if» «builtin|self».tagged «keyword|else» «string|"untagged"»
        «keyword|return» «keyword|f»«string|"»«interpolation|{»«builtin|self».name«interpolation|}»«string|: »«interpolation|{»«builtin|self».weight_kg:,.0f«interpolation|}»«string| kg (»«interpolation|{»status«interpolation|}»«string|)»«string_escape|\n»«string|"»

«keyword|def» heaviest(herd: «builtin|list»[«type|Rhino»]) -> «type|Rhino» | «keyword|None»:
    «comment|# »«todo|TODO»«comment|: report ties instead of picking the first.»
    weighed = [r «keyword|for» r «keyword|in» herd «keyword|if» «number|0» < r.weight_kg <= «constant|MAX_WEIGHT_KG»]
    «keyword|return» «builtin|max»(weighed, key=«keyword|lambda» r: r.weight_kg, default=«keyword|None»)
''',
    "TypeScript": r'''
«keyword|import» { readFile } «keyword|from» «string|"node:fs/promises"»;

«comment|// Each line looks like "Kifaru,2140,true".»
«keyword|const» «constant|LINE» = «regex|/^(\w+),(\d+(?:\.\d+)?),(true|false)$/»;

«keyword|export» «keyword|interface» «type|Sighting» {
  name: «type|string»;
  weightKg: «type|number»;
  tagged: «type|boolean»;
}

«doc_comment|/**»
«doc_comment| * Read a sightings file into records.»
«doc_comment| * »«doc_tag|@param»«doc_comment| path CSV file with one sighting per line»
«doc_comment| */»
«keyword|export» «keyword|async» «keyword|function» loadSightings(path: «type|string»): «type|Promise»<«type|Sighting»[]> {
  «keyword|const» text = «keyword|await» readFile(path, «string|"utf8"»);
  «keyword|return» text
    .split(«string|"»«string_escape|\n»«string|"»)
    .map((line) «keyword|=>» «constant|LINE».exec(line.trim()))
    .filter((m): m «keyword|is» «type|RegExpExecArray» «keyword|=>» m !== «keyword|null»)
    .map(([, name, weight, tagged]) «keyword|=>» ({
      name,
      weightKg: «builtin|Number».parseFloat(weight),
      tagged: tagged === «string|"true"»,
    }));
}

«keyword|const» herd = «keyword|await» loadSightings(«string|"sightings.csv"»);
console.«builtin|log»(«string|`Loaded »«interpolation|${»herd.length«interpolation|}»«string| sightings`»);
''',
    "Go": r'''
«keyword|package» reserve

«keyword|import» (
	«string|"strconv"»
	«string|"strings"»
)

«keyword|const» «constant|MaxWeightKg» = «number|3600» «comment|// estimated from drone photos»

«comment|// Sighting is one line of the field log, such as "Kifaru,2140,true".»
«keyword|type» «type|Sighting» «keyword|struct» {
	Name     «builtin|string»
	WeightKg «builtin|float64»
	Tagged   «builtin|bool»
}

«comment|// Parse reads the field log and skips the lines it can't parse.»
«keyword|func» Parse(fieldLog «builtin|string») []«type|Sighting» {
	«keyword|var» herd []«type|Sighting»
	«keyword|for» _, line := «keyword|range» strings.Split(fieldLog, «string|"»«string_escape|\n»«string|"») {
		«comment|// »«todo|TODO»«comment|: report bad lines instead of skipping them.»
		name, rest, _ := strings.Cut(line, «string|","»)
		weight, tagged, _ := strings.Cut(rest, «string|","»)
		kg, err := strconv.ParseFloat(weight, «number|64»)
		«keyword|if» err != «keyword|nil» || kg <= «number|0» || kg > «constant|MaxWeightKg» {
			«keyword|continue»
		}
		herd = «builtin|append»(herd, «type|Sighting»{name, kg, tagged == «string|"true"»})
	}
	«keyword|return» herd
}
''',
    "Markdown": r'''
«heading|# Reserve log»

Two «bold|**adult**» rhinos at the «italic|*north*» waterhole this morning.

«quote|> Kifaru was tagged at dawn and is doing well.»

«heading|## Measurements»

- Estimated weight: «inline_code|`2,140 kg`»
- Full record in [«link_text|the sightings file»](«link_url|sightings.csv»)
''',
}

# The diff tab: (kind, line) where kind is head, ctx, del or ins. Code lines keep their syntax colors.
DIFF = [
    ("head", "--- a/sightings.py"),
    ("head", "+++ b/sightings.py"),
    ("head", "@@ -17,4 +17,5 @@ class Rhino:"),
    ("del", "«keyword|def» heaviest(herd: «builtin|list»[«type|Rhino»]) -> «type|Rhino» | «keyword|None»:"),
    ("del", "    «comment|# »«todo|TODO»«comment|: report ties instead of picking the first.»"),
    ("ins", "«keyword|def» heaviest(herd: «builtin|list»[«type|Rhino»]) -> «builtin|list»[«type|Rhino»]:"),
    ("ins", '    «doc_comment|"""Every rhino tied for the top weight."""»'),
    ("ctx", "    weighed = [r «keyword|for» r «keyword|in» herd «keyword|if» «number|0» < r.weight_kg <= «constant|MAX_WEIGHT_KG»]"),
    ("del", "    «keyword|return» «builtin|max»(weighed, key=«keyword|lambda» r: r.weight_kg, default=«keyword|None»)"),
    ("ins", "    top = «builtin|max»((r.weight_kg «keyword|for» r «keyword|in» weighed), default=«keyword|None»)"),
    ("ins", "    «keyword|return» [r «keyword|for» r «keyword|in» weighed «keyword|if» r.weight_kg == top]"),
]

PROMPT = "«bright_green+b|ranger@reserve»:«bright_blue+b|~/reserve-log»$ "


def plain(src: str) -> str:
    return TOKEN_RE.sub(lambda m: m.group(2), src)


def terminal_sample() -> str:
    """A short shell session in the sample project; the grep output is real lines from the Python sample."""
    needle = "tagged"
    lines = SAMPLES["Python"].strip("\n").splitlines()
    hits = [(n, plain(line)) for n, line in enumerate(lines, 1) if needle in plain(line)][:2]
    grep = "\n".join(f"«magenta|sightings.py»«cyan|:»«green|{n}»«cyan|:»{line.replace(needle, f'«red+b|{needle}»')}"
                     for n, line in hits)
    return (
        f"{PROMPT}git log --oneline -3\n"
        "«yellow|a41c9e2 (»«bright_cyan+b|HEAD -> »«bright_green+b|main»«yellow|)» Report ties in heaviest()\n"
        "«yellow|7d03b18» Add tagging status\n"
        "«yellow|19f6a2e» Log weights in kg\n"
        f"{PROMPT}git status --short\n"
        " «red|M» sightings.py\n"
        "«green|A»  herd.csv\n"
        "«red|??» exports/\n"
        f"{PROMPT}grep -n {needle} sightings.py\n"
        f"{grep}\n"
        f"{PROMPT}pytest -q\n"
        "«green|..»«red|F»«green|.»                                                    «red|[100%]»\n"
        "«red+b|FAILED» tests/test_herd.py::test_ties - assert 1 == 2\n"
        "«red+b|1 failed», «green|3 passed», «yellow|1 warning» in 0.04s\n"
        f"{PROMPT}ls -F\n"
        "«blue+b|exports/»  herd.csv  README.md  sightings.py  tagging.py  «blue+b|tests/»\n"
        f"{PROMPT}git l"
    )


# The shell's suggestion from history, after the caret on the last line.
SUGGESTION = "«bright_black|og --oneline -3»"

# The README's screenshots (docs/images/<key>-<variant>.png, from make screenshots): key, tab, alt text.
SCREENSHOTS = (
    ("neovim", "Neovim", "Python in Neovim"),
    ("vscode", "VS Code", "Python in VS Code"),
    ("terminal", "Ghostty with starship", "A shell in Ghostty with a starship prompt"),
    ("lazygit", "lazygit", "lazygit"),
    ("claude-code", "Claude Code", "A conversation in Claude Code"),
)


# --- palette model -------------------------------------------------------------

@dataclass
class Variant:
    name: str
    colors: dict[str, str]
    roles: dict[str, dict[str, rhino.Color]]
    surfaces: dict[str, str]
    specs: dict[str, dict]

    @property
    def bg(self) -> str:
        return self.roles["ui"]["background"].hex


def load_variant(data: dict, name: str) -> Variant:
    seen = rhino.view(data, name)
    colors = rhino.palette_colors(seen)
    return Variant(name, colors, rhino.roles(seen), rhino.code_surfaces(seen, colors), rhino.derived_specs(seen))


def base_of(name: str, v: Variant, canonical: set[str]) -> str | None:
    """The palette color a derived color is built from (following `from`), or None for tints of a literal."""
    seen = set()
    while name not in canonical:
        if name in seen:
            return None
        seen.add(name)
        spec = v.specs.get(name)
        if spec is None:
            return name if name in v.colors else None  # a typed-in extra color, like terminal.cyan
        src = str(spec.get("from", ""))
        if src.startswith("#"):
            return None
        name = src
    return name


def text_rows(v: Variant) -> list[tuple[str, str, str, dict[str, float]]]:
    """(key, role label, hex, {surface: contrast}) for each text color in the readability grid."""
    rows = []
    for key, label in TEXT_ROWS.items():
        hex_ = v.colors.get(f"readable.{key}")
        if hex_:
            rows.append((key, label, hex_, {s: rhino.contrast(hex_, h) for s, h in v.surfaces.items()}))
    return rows


def ui_lowest(v: Variant) -> float:
    """Lowest contrast of interface text: UI text roles on their surfaces, and text on colored fills."""
    ui, bg = v.roles["ui"], v.bg
    under = {label: rhino.composite(ui[role], bg) for label, role in rhino.UI_SURFACES.items() if role in ui}
    ratios = [rhino.contrast(rhino.composite(ui[role], under[s]), under[s])
              for role, where in rhino.UI_TEXT_ROLES.items() if role in ui for s in where if s in under]
    for fill, text_roles in rhino.UI_FILLS.items():
        for text_role in text_roles:
            if fill in ui and text_role in ui:
                f = rhino.composite(ui[fill], bg)
                ratios.append(rhino.contrast(rhino.composite(ui[text_role], f), f))
    return min(ratios)


def family(ref: str) -> str:
    return display(ref.removeprefix("readable."))


def closest_pair(v: Variant) -> tuple[str, str, float]:
    distinct: dict[str, str] = {}
    for color in v.roles["syntax"].values():
        distinct.setdefault(color.hex, color.ref)
    (h1, r1), (h2, r2) = min(itertools.combinations(distinct.items(), 2),
                             key=lambda pair: rhino.distance(pair[0][0], pair[1][0]))
    return family(r1), family(r2), rhino.distance(h1, h2)


def muted(fg: str, bg: str, surfaces: tuple[str, ...]) -> str:
    """The faintest blend of fg over bg that still reaches 4.6:1 on bg and every surface."""
    for pct in range(56, 101, 2):
        c = rhino.composite(rhino.Color(fg, pct / 100), bg)
        if all(rhino.contrast(c, s) >= 4.6 for s in (bg, *surfaces)):
            return c
    return fg


def tokens(v: Variant) -> dict[str, str]:
    """CSS custom properties for one variant: page chrome, then every syntax, UI and terminal role."""
    ui = v.roles["ui"]
    bg, fg = v.bg, ui["foreground"].hex

    def flat(*names: str) -> str:
        color = next((ui[n] for n in names if n in ui), ui["background"])
        return rhino.composite(color, bg)

    surface, raised = flat("surface"), flat("surface_raised", "surface")
    out = {
        "bg": bg, "fg": fg, "surface": surface, "raised": raised, "line": flat("border"),
        "hair": rhino.Color(fg, 0.14).hex_with_alpha, "muted": muted(fg, bg, (surface, raised)),
        "accent": v.colors.get("readable.orange", fg), "link": v.colors.get("readable.blue_accent_1", fg),
    }
    for fill, (text_role, *_) in rhino.UI_FILLS.items():
        if fill in ui:
            under = rhino.composite(ui[fill], bg)
            out[f"on-{fill}"] = (ui[text_role].hex if text_role in ui
                                 else max((fg, bg), key=lambda h: rhino.contrast(h, under)))
    out |= {f"s-{r}": c.hex for r, c in v.roles["syntax"].items()}
    out |= {f"ui-{r}": c.hex_with_alpha for r, c in ui.items()}
    out |= {f"t-{r}": c.hex_with_alpha for r, c in v.roles["terminal"].items()}
    return out


def token_rule(selector: str, toks: dict[str, str], scheme: str) -> str:
    body = "\n".join(f"  --{k}: {val};" for k, val in toks.items())
    return f"{selector} {{\n{body}\n  color-scheme: {scheme};\n}}"


# --- helpers -----------------------------------------------------------------

def display(name: str) -> str:
    if name in DISPLAY_NAMES:
        return DISPLAY_NAMES[name]
    return DISPLAY_NAMES.get(name.split(".")[-1], name.split(".")[-1].replace("_", " ").title())


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def lch(hex_: str) -> str:
    L, C, H = rhino.oklch(hex_)
    return f"L {L:.2f} · C {C:.3f} · H {H:.0f}°"


def render(sample: str, prefix: str, known: set[str]) -> str:
    out, pos = [], 0
    for m in TOKEN_RE.finditer(sample):
        out.append(esc(sample[pos:m.start()]))
        classes = []
        for role in m.group(1).split("+"):
            if role == "b":
                classes.append("bold")
            elif role in known:
                classes.append(f"{prefix}-{role}")
            else:
                raise SystemExit(f"preview.py: sample uses unknown role {role!r}")
        out.append(f'<span class="{" ".join(classes)}">{esc(m.group(2))}</span>')
        pos = m.end()
    out.append(esc(sample[pos:]))
    return "".join(out).strip("\n")


def per_variant(dark: str, light: str) -> str:
    """Text that differs by variant: both are rendered and CSS shows the active one."""
    if dark == light:
        return dark
    return f'<span class="only-dark">{dark}</span><span class="only-light">{light}</span>'


def row(no: int | str, code: str, cls: str = "", marker: str = "") -> str:
    return (f'<div class="ln {cls}"><span class="mk {marker}"></span><span class="no">{no}</span>'
            f'<span class="code">{code}</span></div>')


def editor(rows: list[str], min_lines: int = 0, extra: str = "") -> str:
    style = f' style="min-height:calc({min_lines} * 1.65em + 22px)"' if min_lines else ""
    return f'<div class="ed"{style}><div class="rows">{"".join(rows)}</div>{extra}</div>'


def python_rows(known: set[str], current: int = 13) -> list[str]:
    rows = []
    for n, src in enumerate(SAMPLES["Python"].strip("\n").splitlines(), 1):
        if n == current:
            code = render(src.replace("describe", "\x00", 1), "s", known)
            code = code.replace("\x00", '<span class="sel">describe</span>')
            rows.append(row(n, code, "cur"))
        else:
            rows.append(row(n, render(src, "s", known)))
    return rows


def sample_rows(name: str, known: set[str]) -> list[str]:
    return [row(n, render(src, "s", known)) for n, src in enumerate(SAMPLES[name].strip("\n").splitlines(), 1)]


def diff_rows(known: set[str]) -> list[str]:
    rows, old_no, new_no = [], 17, 17
    for kind, src in DIFF:
        if kind == "head":
            rows.append(row("", f'<span class="s-diff_header">{esc(src)}</span>', "dh"))
        elif kind == "del":
            rows.append(row(old_no, f'<span class="s-diff_deleted">-</span>{render(src, "s", known)}', "del"))
            old_no += 1
        elif kind == "ins":
            rows.append(row(new_no, f'<span class="s-diff_inserted">+</span>{render(src, "s", known)}', "ins"))
            new_no += 1
        else:
            rows.append(row(new_no, f" {render(src, 's', known)}"))
            old_no += 1
            new_no += 1
    return rows


# --- sections ------------------------------------------------------------------

def workbench(known: set[str]) -> str:
    """An editor window: explorer with git colors, tabs, gutter marks, current line, completions, status bar."""
    py = SAMPLES["Python"].strip("\n").splitlines()
    marks = {10: "mk-mod", 11: "mk-add", 12: "mk-del"}
    rows = []
    for n in range(6, 21):
        if n == 15:
            code = ('        <span class="s-keyword">return</span> <span class="s-builtin">self</span>.'
                    '<span class="squiggle">ta</span><span class="caret"></span>')
            rows.append(row(n, code, "cur"))
        else:
            rows.append(row(n, render(py[n - 1], "s", known), marker=marks.get(n, "")))
    suggest = ('<div class="suggest">'
               '<div class="on"><span><b>ta</b>gged</span><small>bool</small></div>'
               '<div><span>__ge<b>t</b>st<b>a</b>te__</span><small>method</small></div>'
               '<div><span><b>t</b>o_<b>a</b>scii</span><small>method</small></div></div>')
    return f"""
    <div class="wb">
      <div class="wb-title"><span class="dots"><i></i><i></i><i></i></span>sightings.py — reserve-log</div>
      <div class="wb-body">
        <div class="wb-act"><i class="on"></i><i><b>4</b></i><i></i><i></i></div>
        <div class="wb-side">
          <div class="wb-head">Explorer</div>
          <div class="wb-folder">Reserve-log</div>
          <ul>
            <li><span>exports</span></li>
            <li><span>tests</span></li>
            <li class="in g-mod"><span>test_herd.py</span><span>M</span></li>
            <li class="g-add"><span>herd.csv</span><span>A</span></li>
            <li class="g-del"><span>notes.md</span><span>D</span></li>
            <li class="g-con"><span>tagging.py</span><span>!</span></li>
            <li class="sel"><span>sightings.py</span><span>M</span></li>
            <li><span>README.md</span></li>
          </ul>
        </div>
        <div class="wb-main">
          <div class="tabstrip"><span class="ftab on">sightings.py</span><span class="ftab g-con">tagging.py</span><span class="ftab">README.md</span></div>
          {editor(rows, extra=suggest)}
        </div>
      </div>
      <div class="wb-status">
        <div><span class="remote">SSH: reserve</span><span>main*</span><span>1 error, 0 warnings</span></div>
        <div><span>Ln 15, Col 23</span><span>Spaces: 4</span><span>UTF-8</span><span>Python</span></div>
      </div>
    </div>"""


def hero(data: dict, infos: dict[str, Variant], canonical: dict[str, str], known: set[str]) -> str:
    lowest = min(r for v in infos.values() for *_, ratios in text_rows(v) for r in ratios.values())
    strip = "".join(f'<i style="background:{canonical.get(n) or infos["dark"].colors[n]}" title="{esc(display(n))}"></i>'
                    for n in ACCENTS)
    n_colors = len(canonical) + sum(1 for n in infos["dark"].colors if "." in n and n not in infos["dark"].specs)
    shots = "".join(
        f'<div class="shot v-{name}" role="img" aria-label="Editor window in {data["meta"]["name"]} {name.title()}">'
        f'{workbench(known)}</div>' for name in VARIANTS)
    return f"""
<header class="hero">
  <p class="eyebrow">Color scheme for code and terminal</p>
  <h1>{esc(data["meta"]["name"])}</h1>
  <div class="hero-copy">
    <div class="hero-lede">
      <p class="lede">Warm, low-glare and easy to read. Orange keywords, red strings, green comments,
      purple numbers and blue types on near-black or cream, every one of them readable at 4.5:1 or better
      on the current line, selections and diffs as well as the background.</p>
      <div class="strip" aria-hidden="true">{strip}</div>
      <div class="actions">
        <a class="btn" href="#install">Install</a>
        <a class="btn quiet" href="{rhino.REPO_URL}">Source on GitHub</a>
        <p>For Neovim, VS Code and Cursor, Ghostty, starship, fzf, lazygit, tmux and Claude Code.</p>
      </div>
    </div>
    <dl class="specs">
      <div><dt>Variants</dt><dd>Dark · Light</dd></div>
      <div><dt>Palette</dt><dd>{n_colors} colors</dd></div>
      <div><dt>Syntax roles</dt><dd>{len(infos["dark"].roles["syntax"])}</dd></div>
      <div><dt>Lowest text contrast</dt><dd>{lowest:.2f}:1</dd></div>
    </dl>
  </div>
  <div class="shots">{shots}</div>
</header>"""


def editor_section(known: set[str]) -> str:
    files = [
        ("python", "sightings.py", python_rows(known)),
        ("typescript", "sightings.ts", sample_rows("TypeScript", known)),
        ("go", "sightings.go", sample_rows("Go", known)),
        ("markdown", "notes.md", sample_rows("Markdown", known)),
        ("diff", "sightings.py (diff)", diff_rows(known)),
    ]
    most = max(len(rows) for *_, rows in files)
    panels = [(key, label, editor(rows, most)) for key, label, rows in files]
    return f"""
<section class="section" id="editor" aria-labelledby="h-editor">
  <div class="intro">
    <p class="eyebrow">02 · Editor</p>
    <h2 id="h-editor">In the editor</h2>
    <p>Keywords are orange, strings red, comments green, types and built-ins blue, numbers purple and
    decorators yellow. Italics mark documentation and constants; bold marks emphasis. Switch files to see
    the same roles in TypeScript, Go, Markdown and a diff.</p>
  </div>
  {tabs_pane("ed", "Sample file", panels)}
</section>"""


def tabs_pane(prefix: str, label: str, panels: list[tuple[str, str, str]], cls: str = "") -> str:
    """A tab strip over panels, (key, tab, html); the page's script switches them."""
    tabs, bodies = [], []
    for i, (key, tab, body) in enumerate(panels):
        on = i == 0
        tabs.append(f'<button class="ftab" role="tab" id="tab-{prefix}-{key}" aria-controls="panel-{prefix}-{key}" '
                    f'aria-selected="{str(on).lower()}" tabindex="{0 if on else -1}">{esc(tab)}</button>')
        bodies.append(f'<div role="tabpanel" id="panel-{prefix}-{key}" aria-labelledby="tab-{prefix}-{key}"'
                      f'{"" if on else " hidden"}>{body}</div>')
    return (f'<div class="pane {cls}"><div class="tabstrip" role="tablist" aria-label="{esc(label)}">'
            f'{"".join(tabs)}</div>{"".join(bodies)}</div>')


def png_size(path: Path) -> tuple[int, int]:
    width, height = struct.unpack(">II", path.read_bytes()[16:24])
    return width, height


def screenshots_section() -> str:
    panels = []
    for key, tab, alt in SCREENSHOTS:
        imgs = []
        for v in VARIANTS:
            w, h = png_size(IMAGES / f"{key}-{v}.png")
            imgs.append(f'<img class="only-{v}" src="images/{key}-{v}.png" width="{w}" height="{h}" '
                        f'alt="{esc(alt)}, {v}" loading="lazy" decoding="async">')
        panels.append((key, tab, f'<div class="shotbox">{"".join(imgs)}</div>'))
    return f"""
<section class="section" id="screenshots" aria-labelledby="h-screenshots">
  <div class="intro">
    <p class="eyebrow">01 · Screenshots</p>
    <h2 id="h-screenshots">In the real apps</h2>
    <p>Neovim and VS Code editing the same file, a shell in Ghostty with a starship prompt, lazygit and
    Claude Code, each captured from the current themes. Switch the page between Dark and Light to see both variants.</p>
  </div>
  {tabs_pane("shot", "App", panels, "shots-pane")}
</section>"""


def install_section(known: set[str]) -> str:
    ext_id = build_vscode.EXTENSION_ID
    clone = ("Terminal, once", f"git clone {rhino.REPO_URL}.git ~/critical-rhino")
    home = "~/critical-rhino/extras"
    tools = [
        ("neovim", "Neovim",
         ("A standard colorscheme plugin for Neovim 0.10 or later. <code>critical-rhino</code> follows "
         "<code>'background'</code>; <code>critical-rhino-dark</code> and <code>critical-rhino-light</code> pick "
         "one. lualine picks up the matching theme by itself, and the README covers LazyVim and bufferline."),
         [("lazy.nvim plugin spec",
           ('{\n  «string|"matthewaburton/critical-rhino"»,\n  lazy = «keyword|false»,\n  priority = «number|1000»,\n'
           '  config = «keyword|function»()\n    vim.cmd.colorscheme(«string|"critical-rhino"»)\n  «keyword|end»,\n},'))]),
        ("vscode", "VS Code and Cursor",
         ("Search the Extensions view for <b>Critical Rhino</b>, or install it from a terminal. VS Code gets it "
         f'from the <a href="{esc(build_vscode.MARKETPLACE_URL)}">Visual Studio Marketplace</a>, and Cursor and '
         f'VSCodium from <a href="{esc(build_vscode.OPEN_VSX_URL)}">Open VSX</a>. Then pick '
         "<b>Critical Rhino Dark</b> or <b>Critical Rhino Light</b> with <b>Preferences: Color Theme</b>, or "
         "follow the desktop with the settings below."),
         [("Terminal", f"code --install-extension {ext_id}\ncursor --install-extension {ext_id}"),
          ("settings.json",
           ('«string|"window.autoDetectColorScheme"»: «keyword|true»,\n'
           '«string|"workbench.preferredDarkColorTheme"»: «string|"Critical Rhino Dark"»,\n'
           '«string|"workbench.preferredLightColorTheme"»: «string|"Critical Rhino Light"»'))]),
        ("ghostty", "Ghostty",
         ("Point <code>theme</code> at both files by absolute path and Ghostty 1.3 or later follows the desktop. "
         "Colors in your own config override the theme's, so remove any <code>background</code>, "
         "<code>foreground</code>, <code>palette</code> and <code>selection-*</code> lines."),
         [clone, ("~/.config/ghostty/config.ghostty",
                  ("theme = light:/home/you/critical-rhino/extras/ghostty/critical-rhino-light,"
                  "dark:/home/you/critical-rhino/extras/ghostty/critical-rhino-dark"))]),
        ("starship", "starship",
         ("Starship reads a single config file. If you have your own, copy the theme's tables into it instead: "
         "they only replace the default styles that use fixed colors."),
         [clone, ("~/.bashrc", f"«keyword|export» STARSHIP_CONFIG={home}/starship/critical-rhino.toml")]),
        ("fzf", "fzf",
         ("It appends its colors to <code>FZF_DEFAULT_OPTS</code>, so your other options stay. Load it before "
         "fzf's key bindings."),
         [clone, ("~/.bashrc", f"«keyword|.» {home}/fzf/critical-rhino.sh")]),
        ("lazygit", "lazygit",
         "Point lazygit at the theme, or list your own config first to keep it.",
         [clone, ("~/.bashrc",
                  (f"«keyword|export» LG_CONFIG_FILE={home}/lazygit/critical-rhino.yml\n"
                  "«comment|# or, to keep your own config, list it first:»\n"
                  '«keyword|export» LG_CONFIG_FILE=«string|"$HOME/.config/lazygit/config.yml,'
                  '$HOME/critical-rhino/extras/lazygit/critical-rhino.yml"»'))]),
        ("tmux", "tmux",
         ("It sets only colors: the status bar, borders, messages, the copy-mode selection and search matches. "
         "Layout and status-line contents stay yours."),
         [clone, ("~/.tmux.conf", f"«keyword|source-file» {home}/tmux/critical-rhino.conf")]),
        ("claude-code", "Claude Code",
         ("Link the themes into Claude Code's themes folder, then pick <b>Critical Rhino Dark</b> or "
         "<b>Critical Rhino Light</b> in <code>/theme</code>, whichever matches the terminal: plain text and code "
         "are drawn in the terminal's own colors. A custom theme doesn't follow the desktop, so switch with "
         "<code>/theme</code> when it does. Inside tmux, let Claude Code use full color."),
         [clone, ("Terminal", ("mkdir -p ~/.claude/themes\n"
                               f"ln -s {home}/claude-code/themes/critical-rhino-*.json ~/.claude/themes/")),
          ("~/.bashrc, inside tmux", "«keyword|export» CLAUDE_CODE_TMUX_TRUECOLOR=1")]),
    ]
    panels = []
    for key, tab, note, snippets in tools:
        snips = "".join(f'<figure class="snip"><figcaption><span>{esc(caption)}</span>'
                        f'<button type="button" class="copy">Copy</button></figcaption>'
                        f'<pre><code>{render(code, "s", known)}</code></pre></figure>'
                        for caption, code in snippets)
        panels.append((key, tab, (f'<div class="inst"><p class="inst-note">{note}</p>'
                                 f'<div class="snips">{snips}</div></div>')))
    return f"""
<section class="section" id="install" aria-labelledby="h-install">
  <div class="intro">
    <p class="eyebrow">07 · Install</p>
    <h2 id="h-install">Install</h2>
    <p>Neovim installs like any colorscheme plugin, and VS Code and Cursor from their extension stores. The other themes
    are plain files: clone the repository somewhere it can stay and point each tool at it. The
    <a href="{rhino.REPO_URL}#install">README</a> has more on each tool.</p>
  </div>
  {tabs_pane("inst", "Tool", panels)}
</section>"""


def palette_section(infos: dict[str, Variant], canonical: dict[str, str]) -> str:
    dark = infos["dark"]
    names = set(canonical)
    extras = [n for n in dark.colors if "." in n and n not in dark.specs]  # typed-in extras, like terminal.cyan
    hexes = {**canonical, **{n: dark.colors[n] for n in extras}}

    uses: dict[str, dict[str, dict[str, set[str]]]] = {}
    tones: dict[str, dict[str, str]] = {}
    warm: dict[str, str] = {}
    for vname, v in infos.items():
        for section in rhino.ROLE_SECTIONS:
            for role, color in v.roles[section].items():
                base = base_of(color.ref, v, names) if color.ref else None
                if base:
                    label = {"syntax": SYNTAX_LABELS, "ui": UI_LABELS, "terminal": TERMINAL_LABELS}[section].get(
                        role, role.replace("_", " "))
                    uses.setdefault(base, {}).setdefault(section, {}).setdefault(label, set()).add(vname)
        for name, hex_ in v.colors.items():
            base = base_of(name, v, names)
            if name.startswith("readable.") and base:
                tones.setdefault(base, {})[vname] = hex_
            elif name.startswith("warm.") and base:
                warm[base] = hex_

    def tiles(base: str, fallback: bool) -> str:
        out = []
        for vname, v in infos.items():
            hex_ = tones.get(base, {}).get(vname)
            if hex_ is None and fallback and rhino.contrast(hexes[base], v.bg) >= rhino.TEXT_MIN:
                hex_ = hexes[base]
            if hex_:
                r = rhino.contrast(hex_, v.bg)
                out.append(f'<div class="tone" style="background:{v.bg};color:{hex_}" '
                           f'title="Text on {vname}: {hex_}, {r:.2f}:1"><small>on {vname}</small>'
                           f'<b>Aa</b><span>{hex_}</span></div>')
        return f'<div class="tones">{"".join(out)}</div>' if out else ""

    def usage(base: str) -> str:
        parts = []
        for section, title in SECTION_NAMES.items():
            items = uses.get(base, {}).get(section)
            if not items:
                continue
            groups: dict[str, list[str]] = {"both": [], **{name: [] for name in infos}}
            for label, vs in items.items():
                groups["both" if vs == set(infos) else next(iter(vs))].append(esc(label))
            lines = [", ".join(groups["both"])] if groups["both"] else []
            lines += [f'<span class="only">{name}</span> {", ".join(groups[name])}' for name in infos if groups[name]]
            parts.append(f"<div><dt>{title}</dt><dd>{''.join(f'<span>{x}</span>' for x in lines)}</dd></div>")
        return f'<dl class="use">{"".join(parts)}</dl>' if parts else '<p class="spare">Not assigned to a role yet.</p>'

    accents = [n for n in ACCENTS if n in hexes]
    grounds = [n for n in GROUNDS if n in hexes] + [n for n in hexes if n not in ACCENTS and n not in GROUNDS]
    acc_rows = "".join(f"""
    <article class="acc">
      <div class="acc-sw" style="background:{hexes[n]}"></div>
      <div class="acc-id"><h4>{esc(display(n))}</h4><code class="hex">{hexes[n]}</code><code class="lch">{lch(hexes[n])}</code></div>
      {tiles(n, True)}
      {usage(n)}
    </article>""" for n in accents)
    cards = []
    for n in grounds:
        if n in warm:
            swatch = (f'<div class="gc-sw"><i style="background:{hexes[n]}" title="{hexes[n]}"></i>'
                      f'<i style="background:{warm[n]}" title="warm {warm[n]}"></i></div>')
            hex_line = f'<code class="hex">{hexes[n]}</code><code class="hex">warm {warm[n]}</code>'
        else:
            swatch = f'<div class="gc-sw"><i style="background:{hexes[n]}"></i></div>'
            hex_line = f'<code class="hex">{hexes[n]}</code>'
        cards.append(f"""
    <article class="gc">{swatch}
      <div class="gc-body"><h4>{esc(display(n))}</h4><div class="gc-hex">{hex_line}</div>{tiles(n, False)}{usage(n)}</div>
    </article>""")
    return f"""
<section class="section" id="palette" aria-labelledby="h-palette">
  <div class="intro">
    <p class="eyebrow">03 · Palette</p>
    <h2 id="h-palette">The palette</h2>
    <p>Six warm accents and a terminal cyan, set on near-black and cream. Each accent has a text tone for
    each variant: the same hue and chroma, with lightness moved just far enough to read at 4.5:1. The grays
    are warmed toward Loden so the interface sits comfortably with the cream.</p>
  </div>
  <h3 class="group">Accents</h3>
  <div class="accents">{acc_rows}
  </div>
  <h3 class="group">Grounds and highlights</h3>
  <div class="grounds">{"".join(cards)}
  </div>
</section>"""


def roles_section(infos: dict[str, Variant], styles: dict[str, tuple[str, ...]]) -> str:
    dark, light = infos["dark"].roles["syntax"], infos["light"].roles["syntax"]
    groups: dict[str, list[str]] = {}
    for role, color in dark.items():
        groups.setdefault(color.ref or color.hex, []).append(role)
    order = ["readable.foreground", "readable.orange", "readable.red", "readable.green", "readable.yellow",
             "readable.blue", "readable.purple"]
    keys = [k for k in order if k in groups] + [k for k in groups if k not in order]
    cards = []
    for key in keys:
        roles_ = groups[key]
        first = roles_[0]
        def style_note(role: str) -> str:
            """The role's font style in words, set in that style: "Italic · ", "Bold italic · "."""
            flags = styles.get(role, ())
            if not flags:
                return ""
            words = " ".join(STYLE_WORDS.get(f, f) for f in flags)
            classes = " ".join(f"st-{f}" for f in flags)
            return f'<span class="style {classes}">{esc(words[0].upper() + words[1:])}</span> · '

        items = "".join(
            f'<li><span class="spec s-{r}">{esc(SPECIMENS.get(r, r))}</span>'
            f'<span class="rl">{esc(SYNTAX_LABELS.get(r, r))}'
            f'<small>{style_note(r)}{esc(SYNTAX_COVERS.get(r, ""))}</small></span></li>' for r in roles_)
        cards.append(f"""
    <article class="fam">
      <div class="fam-head"><i style="background:var(--s-{first})"></i><h4>{esc(family(key))}</h4>
        <code>{per_variant(dark[first].hex, light[first].hex)}</code></div>
      <ul>{items}</ul>
    </article>""")
    return f"""
<section class="section" id="roles" aria-labelledby="h-roles">
  <div class="intro">
    <p class="eyebrow">04 · Roles</p>
    <h2 id="h-roles">Syntax roles</h2>
    <p>{len(dark)} roles in {len(keys)} colors, the same in both variants, so a keyword means the same thing
    on cream as on near-black. Anything without a role is plain text.</p>
  </div>
  <div class="fams">{"".join(cards)}
  </div>
</section>"""


def readability_section(infos: dict[str, Variant]) -> str:
    panels = []
    for vname, v in infos.items():
        rows = text_rows(v)
        # The table shows the main surfaces; the rest (diff-mode changes, highlighted references)
        # are checked too and count toward "Lowest", but nine columns don't fit.
        shown = [s for s in v.surfaces if s in SURFACE_NAMES]
        head = "".join(f'<th scope="col"><i style="background:{v.surfaces[s]}"></i>{SURFACE_NAMES[s]}</th>'
                       for s in shown)
        body = "".join(
            f'<tr><th scope="row"><b>{esc(display(key))}</b><small>{esc(label)}</small></th>'
            + "".join(f'<td style="background:{v.surfaces[s]};color:{hex_}">{ratios[s]:.2f}</td>'
                      for s in shown)
            + "</tr>" for key, label, hex_, ratios in rows)
        lowest = min(r for *_, ratios in rows for r in ratios.values())
        a, b, d = closest_pair(v)
        panels.append(f"""
    <div class="proof v-{vname}">
      <h3>{vname.title()}</h3>
      <div class="table-wrap"><table class="proof-t">
        <thead><tr><th scope="col"><span class="sr">Text color</span></th>{head}</tr></thead>
        <tbody>{body}</tbody>
      </table></div>
      <p class="proof-foot">Lowest <b>{lowest:.2f}:1</b> · interface text at least <b>{ui_lowest(v):.2f}:1</b>
      · closest syntax colors {esc(a)} and {esc(b)}, <b>{d:.3f}</b> apart in OKLab</p>
    </div>""")
    return f"""
<section class="section" id="readability" aria-labelledby="h-readability">
  <div class="intro">
    <p class="eyebrow">05 · Readability</p>
    <h2 id="h-readability">Readable on every surface</h2>
    <p>Code isn't only drawn on the background. Every text color is measured against the current line,
    a selection, inserted and removed diff lines, the hovers and widgets that show code, changed lines
    in side-by-side diffs and highlighted references, and reaches 4.5:1 on all of them. Interface text meets the same bar: file names, muted labels, line numbers and
    the status bar. Distinct syntax colors stay at least 0.08 apart in OKLab, so a string never passes
    for a keyword.</p>
  </div>
  <div class="proofs">{"".join(panels)}
  </div>
</section>"""


def terminal_section(infos: dict[str, Variant]) -> str:
    dark, light = infos["dark"], infos["light"]
    slots = set(dark.roles["terminal"])
    cells = []

    def info(v: Variant, slot: str) -> str:
        c = v.roles["terminal"][slot].hex
        return f'{c} · {rhino.contrast(c, v.roles["terminal"]["background"].hex):.2f}:1'

    for bright in (False, True):
        for base in ANSI_SLOTS:
            slot = f"bright_{base}" if bright else base
            if slot not in slots:
                continue
            cells.append(f'<div><i style="background:var(--t-{slot})"></i><span class="slot">'
                         f'{esc(slot.replace("_", " "))}</span>'
                         f'<code>{per_variant(info(dark, slot), info(light, slot))}</code></div>')
    known = slots
    return f"""
<section class="section" id="terminal" aria-labelledby="h-terminal">
  <div class="intro">
    <p class="eyebrow">06 · Terminal</p>
    <h2 id="h-terminal">In the terminal</h2>
    <p>Most programs print in the normal ANSI colors, so the six colored normals read at 4.5:1, on the
    background and inside a selection. The brights are lighter in both variants and stay at 3:1 or better.
    White is secondary text at 4.5:1, and bright black is the dim gray shells use for suggestions, at 3:1.</p>
  </div>
  <div class="term">
    <div class="term-bar"><span class="dots"><i></i><i></i><i></i></span>ranger@reserve: ~/reserve-log</div>
    <pre>{render(terminal_sample(), "a", known)}<span class="caret"></span>{render(SUGGESTION, "a", known)}</pre>
  </div>
  <div class="ansi">{"".join(cells)}</div>
</section>"""


def plain_name(c: rhino.Color, tones: bool = True) -> str:
    """A palette reference in words: "Red", "Green text tone", "Warm Gray (Dark)", "Blue fill", "Loden 40%"."""
    group, _, key = c.ref.rpartition(".")
    if not c.ref:
        name = c.hex
    elif group == "readable":
        name = display(key) + (" text tone" if tones and key != "foreground" else "")
    elif group == "warm":
        name = f"Warm {display(key)}"
    elif group == "fill":
        name = f"{display(key)} fill"
    elif group == "wash":
        name = f"{display(key)} wash"
    elif group == "terminal":
        name = f"Terminal {display(key)}" if key in ("black", "white") else key.replace("_", " ").title()
    else:
        name = display(key)
    return name + (f" {round(c.alpha * 100)}%" if c.alpha < 1 else "")


def reference_section(infos: dict[str, Variant], styles: dict[str, tuple[str, ...]]) -> str:
    dark, light = infos["dark"], infos["light"]

    def cell(c: rhino.Color | None, bg: str, tones: bool) -> str:
        if c is None:
            return "<td>—</td>"
        shown = rhino.composite(c, bg)
        return (f'<td><span class="chip"><i style="background:{shown}"></i><code>{shown}</code></span>'
                f'<span class="refname">{esc(plain_name(c, tones))}</span></td>')

    def table(section: str, labels: dict[str, str], extra_head: str = "", extra=None) -> str:
        rows = []
        tones = section != "syntax"  # every code color is a text tone, so syntax rows just name the color
        for role in dark.roles[section]:
            d, li = dark.roles[section].get(role), light.roles[section].get(role)
            more = extra(role) if extra else ""
            rows.append(f'<tr><th scope="row"><code>{esc(role)}</code><small>{esc(labels.get(role, ""))}</small></th>'
                        f'{cell(d, dark.bg, tones)}{cell(li, light.bg, tones)}{more}</tr>')
        return (f'<div class="table-wrap"><table class="ref-t"><thead><tr><th scope="col">Role</th>'
                f'<th scope="col">Dark</th><th scope="col">Light</th>{extra_head}</tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>')

    syntax = table("syntax", SYNTAX_LABELS, '<th scope="col">Style</th>',
                   lambda r: f'<td>{esc(", ".join(styles.get(r, ())) or "plain")}</td>')
    return f"""
<section class="section" id="reference" aria-labelledby="h-reference">
  <div class="intro">
    <p class="eyebrow">08 · Reference</p>
    <h2 id="h-reference">Every role</h2>
    <p>The values theme files are built from. Code colors are each accent's text tone for that variant.
    Translucent colors are shown blended onto their background, with the color and opacity underneath.</p>
  </div>
  <div class="refs">
  <details><summary>Syntax · {len(dark.roles["syntax"])} roles</summary>{syntax}</details>
  <details><summary>Interface · {len(dark.roles["ui"])} roles</summary>{table("ui", UI_LABELS)}</details>
  <details><summary>Terminal · {len(dark.roles["terminal"])} colors</summary>{table("terminal", TERMINAL_LABELS)}</details>
  </div>
</section>"""


# --- comparison against a git ref (for review, not for the published page) -----

def load_palette_at(ref: str) -> dict:
    out = subprocess.run(["git", "show", f"{ref}:palette.toml"], cwd=rhino.ROOT,
                         capture_output=True, text=True, check=True)
    return tomllib.loads(out.stdout)


def short_sha(ref: str) -> str:
    # An unknown ref is fine: the caller shows the ref as typed.
    out = subprocess.run(["git", "rev-parse", "--short", ref], cwd=rhino.ROOT, capture_output=True, text=True,
                         check=False)
    return out.stdout.strip() or ref


def compare_section(ref: str, data: dict, known: set[str]) -> tuple[str, list[str]]:
    old_data = load_palette_at(ref)
    sha = short_sha(ref)
    css, panes, rows = [], [], []
    for vname in VARIANTS:
        if vname not in rhino.appearances(old_data) or vname not in rhino.appearances(data):
            continue
        old, new = load_variant(old_data, vname), load_variant(data, vname)
        for cls, v, title in ((f"cmp-old-{vname}", old, f"{ref} ({sha})"), (f"cmp-new-{vname}", new, "This version")):
            css.append(token_rule(f".{cls}", tokens(v), vname))
            panes.append(f'<div class="pane {cls}"><div class="tabstrip"><span class="ftab on">{esc(title)} · '
                         f'{vname}</span></div>{editor(python_rows(known))}</div>')
        for section in rhino.ROLE_SECTIONS:
            o_s, n_s = old.roles[section], new.roles[section]
            for name in list(n_s) + [k for k in o_s if k not in n_s]:
                o, n = o_s.get(name), n_s.get(name)
                if (o.hex_with_alpha if o else None) == (n.hex_with_alpha if n else None):
                    continue
                chip = lambda c, bg: (f'<span class="chip"><i style="background:{rhino.composite(c, bg)}"></i>'
                                      f'<code>{c.hex_with_alpha}</code></span>') if c else "—"
                rows.append(f"<tr><th scope=row><code>{vname} {section}.{esc(name)}</code></th>"
                            f"<td>{chip(o, old.bg)}</td><td>{chip(n, new.bg)}</td></tr>")
    html_ = f"""
<section class="section" aria-labelledby="h-changes">
  <div class="intro"><p class="eyebrow">Review</p><h2 id="h-changes">Changes since {esc(ref)}</h2>
  <p>Left: <code>palette.toml</code> at {esc(ref)} ({esc(sha)}). Right: this version. {len(rows)} role values differ.</p></div>
  <div class="compare">{"".join(panes)}</div>
  <div class="table-wrap"><table class="ref-t"><thead><tr><th>Role</th><th>Before</th><th>After</th></tr></thead>
  <tbody>{"".join(rows)}</tbody></table></div>
</section>"""
    return html_, css


# --- page --------------------------------------------------------------------

CSS = r"""
:root {
  --sans: "Archivo", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
img { max-width: 100%; height: auto; }
html { scroll-behavior: smooth; scroll-padding-top: 72px; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.6 var(--sans); transition: background-color .3s, color .3s; }
code, pre { font-family: var(--mono); }
a { color: var(--link); }
:focus-visible { outline: 2px solid var(--link); outline-offset: 2px; }
.v-dark, .v-light { color: var(--fg); }
.only-dark, .only-light { display: inline; }
:root[data-variant="dark"] .only-light, :root:not([data-variant="dark"]) .only-dark { display: none; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

/* top bar */
body::before { content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px); z-index: 10; background: var(--bg); }
.top { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 9; background: var(--bg); border-bottom: 1px solid var(--hair); transition: background-color .3s; }
.top-in {
  max-width: 1200px; margin: 0 auto; display: flex; align-items: center; gap: 28px; padding-block: 10px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
}
.mark { display: flex; align-items: center; gap: 10px; color: var(--fg); text-decoration: none; font: 800 1rem/1 var(--sans); font-stretch: 125%; letter-spacing: -0.01em; white-space: nowrap; }
.mark svg { flex: none; height: 18px; width: auto; }
.nav { display: flex; gap: 22px; margin-left: auto; }
.nav a { color: var(--muted); text-decoration: none; font-size: .88rem; font-weight: 500; }
.nav a:hover { color: var(--fg); }
.switch { display: inline-flex; gap: 2px; padding: 3px; border: 1px solid var(--line); border-radius: 999px; }
.switch button {
  display: inline-flex; align-items: center; gap: 7px; padding: 5px 13px 5px 9px; border: 0; border-radius: 999px;
  background: none; color: var(--muted); font: 500 .82rem var(--sans); cursor: pointer;
}
.switch button:hover { color: var(--fg); }
.switch button[aria-pressed="true"] { background: var(--raised); color: var(--fg); }
.switch i { width: 12px; height: 12px; border-radius: 50%; box-shadow: inset 0 0 0 1px var(--line); }

/* layout */
.wrap {
  max-width: 1200px; margin: 0 auto; display: grid; gap: 120px; padding-block: 64px 96px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
}
.wrap > * { min-width: 0; }
h1, h2, h3, h4 { margin: 0; text-wrap: balance; }
p { margin: 0; text-wrap: pretty; }
.eyebrow { font: 500 .75rem/1.2 var(--mono); letter-spacing: .12em; text-transform: uppercase; color: var(--accent); }
.section { display: grid; gap: 28px; }
.section > * { min-width: 0; }
/* Section intros: heading on the left, text on the right, so lines stay a readable length. */
.intro { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); gap: 12px 56px; }
.intro > .eyebrow { grid-column: 1; grid-row: 1; }
.intro > h2 { grid-column: 1; grid-row: 2; }
.intro > p:not(.eyebrow) { grid-column: 2; grid-row: 1 / span 2; align-self: end; max-width: 64ch; color: var(--muted); font-size: 1.05rem; }
h2 { font: 750 clamp(1.9rem, 4vw, 2.75rem)/1.05 var(--sans); font-stretch: 118%; letter-spacing: -0.02em; }
h3.group { font: 500 .75rem var(--mono); letter-spacing: .12em; text-transform: uppercase; color: var(--muted); margin-bottom: -12px; }
.table-wrap { overflow-x: auto; }

/* hero */
.hero { display: grid; gap: 26px; }
h1 { font: 850 clamp(3.4rem, 11.5vw, 8rem)/.9 var(--sans); font-stretch: 125%; letter-spacing: -0.035em; }
.hero-copy { display: grid; grid-template-columns: minmax(0, 7fr) minmax(0, 5fr); gap: 32px 64px; align-items: end; }
.hero-lede { display: grid; gap: 26px; }
.lede { font-size: clamp(1.08rem, 1.8vw, 1.32rem); line-height: 1.55; max-width: 56ch; }
.strip { display: flex; height: 10px; border-radius: 2px; overflow: hidden; }
.strip i { flex: 1; }
.specs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 22px 32px; margin: 0; padding-left: 32px; border-left: 1px solid var(--line); }
.specs div { display: grid; gap: 4px; }
.specs dt { font: 500 .68rem var(--mono); letter-spacing: .1em; text-transform: uppercase; color: var(--muted); }
.specs dd { margin: 0; font: 700 clamp(1.2rem, 2.2vw, 1.6rem)/1.15 var(--sans); font-stretch: 112%; font-variant-numeric: tabular-nums; }
.shots { display: grid; margin-top: 18px; }
.shot {
  grid-area: 1 / 1; margin: 0 0 56px 72px; z-index: 1; border-radius: 10px; overflow: hidden;
  border: 1px solid var(--line); box-shadow: 0 28px 60px -24px rgb(0 0 0 / .5);
  transition: margin .5s cubic-bezier(.2, .7, .2, 1);
}
:root[data-variant="dark"] .shot.v-dark, :root:not([data-variant="dark"]) .shot.v-light { margin: 56px 72px 0 0; z-index: 2; }

/* editor window */
.wb { min-width: 940px; background: var(--ui-background); color: var(--ui-foreground); font: 13px/1.5 var(--sans); }
.wb-title, .term-bar {
  position: relative; display: flex; align-items: center; justify-content: center; height: 34px;
  background: var(--ui-surface_raised); color: var(--ui-muted_foreground); font: 12px var(--sans);
}
.dots { position: absolute; left: 14px; display: flex; gap: 7px; }
.dots i { width: 11px; height: 11px; border-radius: 50%; background: var(--ui-border); }
.wb-body { display: grid; grid-template-columns: 48px 214px minmax(0, 1fr); }
.wb-act { display: grid; align-content: start; justify-items: center; gap: 22px; padding-top: 14px; }
.wb-act i { position: relative; width: 20px; height: 20px; border: 2px solid var(--ui-muted_foreground); border-radius: 5px; }
.wb-act i.on { border-color: var(--ui-foreground); }
.wb-act b {
  position: absolute; right: -9px; bottom: -8px; min-width: 16px; padding: 0 4px; border-radius: 8px;
  background: var(--ui-selection); color: var(--on-selection); font: 600 9px/16px var(--sans); text-align: center;
}
.wb-side { background: var(--ui-surface); padding-block: 10px; }
.wb-head { padding: 0 14px 8px; font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--ui-muted_foreground); }
.wb-folder { padding: 2px 14px; font-size: 11px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
.wb-side ul { list-style: none; margin: 0; padding: 0; }
.wb-side li { display: flex; justify-content: space-between; gap: 8px; padding: 2px 14px 2px 22px; }
.wb-side li.in { padding-left: 36px; }
.wb-side li.sel { background: var(--ui-selection); color: var(--on-selection); }
.g-mod { color: var(--ui-git_modified); }
.g-add { color: var(--ui-git_added); }
.g-del { color: var(--ui-git_deleted); }
.g-del span:first-child { text-decoration: line-through; }
.g-con { color: var(--ui-git_conflict); }
.wb-main { display: grid; grid-template-rows: auto 1fr; min-width: 0; }
.tabstrip { display: flex; overflow-x: auto; background: var(--ui-surface); }
.ftab {
  padding: 8px 16px; border: 0; background: none; color: var(--ui-muted_foreground); font: 13px var(--sans);
  white-space: nowrap; box-shadow: inset 0 -1px 0 var(--ui-border);
}
.ftab.g-con { color: var(--ui-git_conflict); }
.ftab.on, .ftab[aria-selected="true"] { background: var(--ui-background); color: var(--ui-foreground); box-shadow: inset 0 2px 0 var(--ui-accent); }
button.ftab { cursor: pointer; }
button.ftab:hover { color: var(--ui-foreground); }
.wb-status { display: flex; justify-content: space-between; gap: 16px; height: 24px; align-items: center; background: var(--ui-accent); color: var(--on-accent); font-size: 12px; }
.wb-status div { display: flex; gap: 16px; align-items: center; height: 100%; }
.wb-status div:last-child { padding-right: 12px; }
.wb-status .remote { display: flex; align-items: center; height: 100%; padding: 0 10px; background: var(--ui-status_remote); color: var(--on-status_remote); }

/* code */
.ed { position: relative; overflow-x: auto; padding-block: 11px; background: var(--ui-background); color: var(--s-text); font: 13px/1.65 var(--mono); tab-size: 4; }
.rows { display: grid; width: max-content; min-width: 100%; }
.ln { display: grid; grid-template-columns: 4px 3.4em 1fr; white-space: pre; padding-right: 20px; }
.ln .no { padding-right: 1.3em; text-align: right; color: var(--ui-line_numbers); user-select: none; }
.ln.cur { background: var(--ui-current_line); }
.ln.cur .no { color: var(--ui-line_number_active, var(--ui-foreground)); }
.ln.ins { background: var(--ui-diff_inserted_bg); }
.ln.del { background: var(--ui-diff_removed_bg); }
.sel { background: var(--ui-editor_selection); }
.mk-add { background: var(--ui-gutter_added); }
.mk-mod { background: var(--ui-gutter_modified); }
.mk-del { background: linear-gradient(var(--ui-gutter_deleted), var(--ui-gutter_deleted)) top / 100% 3px no-repeat; }
.squiggle { text-decoration: underline wavy var(--ui-error); text-decoration-skip-ink: none; text-underline-offset: 3px; }
.caret { display: inline-block; width: 2px; height: 1.25em; margin-left: 1px; vertical-align: text-bottom; background: var(--ui-foreground); }
.wb .ed { padding-bottom: 30px; }
.wb .ed::after { content: ""; position: absolute; inset-block: 0; left: calc(4px + 3.4em + 80ch); width: 1px; background: var(--ui-ruler); }
.suggest {
  position: absolute; z-index: 1; top: calc(13px + 10 * 1.65em); left: calc(4px + 3.4em + 20ch); width: 250px;
  background: var(--surface); border: 1px solid var(--line); box-shadow: 0 8px 20px rgb(0 0 0 / .25); font: 13px var(--mono);
}
.suggest div { display: flex; justify-content: space-between; gap: 12px; padding: 0 10px; color: var(--ui-foreground); font-size: 12.5px; line-height: 1.75; }
.suggest div.on { background: var(--ui-selection_soft); }
.suggest b { color: var(--ui-match_highlight); font-weight: 700; }
.suggest small { color: var(--muted); font: 11px var(--sans); align-self: center; }
.bold { font-weight: 700; }
.pane, .term { border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }

/* palette */
.accents { display: grid; border-top: 1px solid var(--line); }
.acc { display: grid; grid-template-columns: 92px minmax(10rem, 13rem) 15rem minmax(0, 1fr); gap: 28px; align-items: center; padding-block: 20px; border-bottom: 1px solid var(--line); }
.acc-sw { width: 92px; height: 92px; border-radius: 10px; box-shadow: inset 0 0 0 1px var(--hair); }
.acc-id, .gc-hex { display: grid; gap: 2px; }
h4 { font: 700 1.12rem/1.25 var(--sans); font-stretch: 110%; }
.hex { font-size: .85rem; }
.lch { font-size: .72rem; color: var(--muted); }
.tones { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.tone { display: grid; gap: 1px; padding: 9px 12px 10px; border-radius: 8px; box-shadow: inset 0 0 0 1px var(--hair); }
.tone small { font: 500 .62rem var(--mono); letter-spacing: .08em; text-transform: uppercase; }
.tone b { font: 700 1.5rem/1.15 var(--sans); }
.tone span { font: .74rem var(--mono); }
.use { display: grid; gap: 5px; margin: 0; font-size: .88rem; line-height: 1.45; }
.use div { display: grid; grid-template-columns: 5rem minmax(0, 1fr); gap: 10px; }
.use dt { font: 500 .66rem/2 var(--mono); letter-spacing: .1em; text-transform: uppercase; color: var(--muted); }
.use dd { margin: 0; }
.use dd > span { display: block; }
.only { font: 500 .62rem var(--mono); letter-spacing: .06em; text-transform: uppercase; color: var(--muted); border: 1px solid var(--line); border-radius: 3px; padding: 0 4px; vertical-align: 1px; white-space: nowrap; }
.spare { color: var(--muted); font-size: .88rem; font-style: italic; }
.grounds { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 210px), 1fr)); gap: 16px; }
.gc { display: grid; grid-template-rows: auto 1fr; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
.gc-sw { display: flex; height: 84px; }
.gc-sw i { flex: 1; box-shadow: inset 0 -1px 0 var(--hair); }
.gc-body { display: grid; gap: 10px; align-content: start; padding: 14px 16px 16px; }
.gc .use div { grid-template-columns: 1fr; gap: 0; }
.gc .use dt { line-height: 1.6; }
.gc .tone b { font-size: 1.2rem; }

/* roles */
.fams { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 350px), 1fr)); gap: 16px; align-items: start; }
.fam { border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: var(--ui-background); }
.fam-head { display: flex; align-items: center; gap: 10px; padding: 12px 16px; background: var(--surface); border-bottom: 1px solid var(--line); }
.fam-head i { width: 14px; height: 14px; border-radius: 4px; }
.fam-head code { margin-left: auto; font-size: .78rem; color: var(--muted); }
.fam ul { list-style: none; margin: 0; padding: 6px 0; }
.fam li { display: grid; grid-template-columns: minmax(0, 10.5rem) minmax(0, 1fr); gap: 16px; align-items: baseline; padding: 8px 16px; }
.fam li + li { border-top: 1px solid var(--hair); }
.spec { font: 14px/1.4 var(--mono); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rl { font-size: .9rem; font-weight: 600; }
.rl small { display: block; color: var(--muted); font-size: .78rem; font-weight: 400; line-height: 1.4; }
.rl .style { color: var(--fg); }
.st-bold { font-weight: 700; }
.st-italic { font-style: italic; }
.st-underline { text-decoration: underline; text-underline-offset: 2px; }
.st-strikethrough { text-decoration: line-through; }
.s-text { color: var(--s-text); }

/* readability */
.proofs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.proof { display: grid; gap: 14px; align-content: start; padding: 20px 18px; border-radius: 12px; background: var(--bg); border: 1px solid var(--line); }
.proof h3 { font: 700 1.1rem var(--sans); font-stretch: 110%; }
.proof-t { width: 100%; border-collapse: separate; border-spacing: 2px; font-variant-numeric: tabular-nums; }
.proof-t thead th { padding: 0 2px 6px; font: 500 .58rem/1.3 var(--mono); letter-spacing: .06em; text-transform: uppercase; color: var(--muted); text-align: left; vertical-align: bottom; white-space: nowrap; }
.proof-t thead i { display: block; width: 100%; height: 6px; margin-bottom: 6px; border-radius: 2px; box-shadow: inset 0 0 0 1px var(--hair); }
.proof-t tbody th { padding-right: 6px; text-align: left; font-weight: 600; font-size: .82rem; line-height: 1.25; white-space: nowrap; }
.proof-t tbody th small { display: block; color: var(--muted); font-weight: 400; font-size: .74rem; }
.proof-t td { padding: 8px; border-radius: 4px; font: 500 .8rem var(--mono); white-space: nowrap; }
.proof-foot { color: var(--muted); font-size: .84rem; }
.proof-foot b { color: var(--fg); font-weight: 600; }

/* terminal */
.term { background: var(--t-background); color: var(--t-foreground); }
.term pre { margin: 0; padding: 16px 20px 22px; overflow-x: auto; font: 13px/1.65 var(--mono); }
.term .caret { background: var(--t-cursor); width: .6em; vertical-align: text-bottom; }
.ansi { display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: 10px; }
.ansi div { display: grid; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.ansi i { height: 46px; box-shadow: inset 0 -1px 0 var(--hair); }
.ansi .slot { padding: 8px 10px 0; font-size: .82rem; font-weight: 600; }
.ansi code { padding: 0 10px 9px; font-size: .7rem; color: var(--muted); }

/* reference */
details { border-top: 1px solid var(--line); }
details:last-child { border-bottom: 1px solid var(--line); }
summary { padding: 14px 2px; cursor: pointer; font-weight: 600; }
.ref-t { width: 100%; border-collapse: collapse; margin-bottom: 18px; font-size: .86rem; }
.ref-t th, .ref-t td { padding: 8px 12px 8px 0; border-bottom: 1px solid var(--hair); text-align: left; vertical-align: top; }
.ref-t thead th { font: 500 .66rem var(--mono); letter-spacing: .1em; text-transform: uppercase; color: var(--muted); }
.ref-t tbody th { font-weight: 400; }
.ref-t tbody th small, .refname { display: block; color: var(--muted); font-size: .76rem; }
.refname { font-family: var(--mono); padding-left: 24px; }
.chip { display: inline-flex; align-items: center; gap: 8px; white-space: nowrap; }
.chip i { width: 16px; height: 16px; border-radius: 4px; box-shadow: inset 0 0 0 1px var(--hair); flex: none; }
.compare { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }

footer { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px 24px; padding-top: 24px; border-top: 1px solid var(--line); color: var(--muted); font-size: .88rem; }
footer .mark { color: var(--fg); }
footer a:not(.mark) { color: var(--fg); text-underline-offset: 2px; }

/* hero actions and the top bar's source link */
.actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.actions p { flex-basis: 100%; color: var(--muted); font-size: .92rem; }
.btn {
  display: inline-flex; align-items: center; padding: 10px 20px; border-radius: 8px;
  background: var(--ui-accent); color: var(--on-accent); font: 650 .95rem/1.2 var(--sans); text-decoration: none;
}
.btn.quiet { background: none; color: var(--fg); box-shadow: inset 0 0 0 1px var(--line); }
.btn:hover { filter: brightness(1.08); }
.btn.quiet:hover { filter: none; background: var(--surface); }
.src { color: var(--muted); font-size: .88rem; font-weight: 500; text-decoration: none; white-space: nowrap; }
.src:hover { color: var(--fg); }

/* screenshots */
.shotbox { display: grid; justify-items: center; background: var(--ui-background); }
.shotbox img { display: block; }

/* install */
.inst { display: grid; grid-template-columns: minmax(0, 4fr) minmax(0, 8fr); gap: 24px 40px; padding: 24px; background: var(--ui-background); color: var(--ui-foreground); }
.inst-note { font-size: .95rem; line-height: 1.6; max-width: 46ch; }
.inst-note code { font-size: .88em; white-space: nowrap; }
.inst-note a { color: var(--ui-link, var(--link)); }
.snips { display: grid; gap: 16px; align-content: start; min-width: 0; }
.snip { margin: 0; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; min-width: 0; }
.snip figcaption {
  display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 5px 6px 5px 14px;
  background: var(--ui-surface); color: var(--ui-muted_foreground); font: 12px var(--mono);
}
.copy { padding: 3px 10px; border: 0; border-radius: 5px; background: none; color: var(--ui-muted_foreground); font: 500 12px var(--sans); cursor: pointer; }
.copy:hover { background: var(--ui-hover, var(--raised)); color: var(--ui-foreground); }
.snip pre { margin: 0; padding: 12px 14px; overflow-x: auto; background: var(--ui-background); color: var(--s-text); font: 13px/1.65 var(--mono); }

@media (max-width: 1100px) {
  .acc { grid-template-columns: 80px minmax(0, 1fr) 15rem; }
  .acc-sw { width: 80px; height: 80px; }
  .acc .use { grid-column: 2 / -1; }
  .proofs { grid-template-columns: 1fr; }
}
@media (max-width: 1000px) {
  .nav a:not(.keep) { display: none; }
}
@media (max-width: 900px) {
  .intro, .hero-copy { grid-template-columns: minmax(0, 1fr); }
  .intro > .eyebrow, .intro > h2, .intro > p:not(.eyebrow) { grid-column: 1; grid-row: auto; }
  .specs { padding-left: 0; border-left: 0; }
  .shot { margin: 0 !important; }
  .shot.v-dark, .shot.v-light { display: none; }
  :root[data-variant="dark"] .shot.v-dark, :root:not([data-variant="dark"]) .shot.v-light { display: block; }
  .wb { min-width: 0; }
  .wb-body { grid-template-columns: minmax(0, 1fr); }
  .wb-act, .wb-side { display: none; }
  .wb-status div:last-child span:nth-child(n+2) { display: none; }
  .ansi { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .compare { grid-template-columns: 1fr; }
  .inst { grid-template-columns: minmax(0, 1fr); padding: 18px 16px; }
}
@media (max-width: 720px) {
  .nav { display: none; }
  .top-in { gap: 16px; }
  .switch { margin-left: auto; }
  .wrap { gap: 88px; padding-block: 40px 72px; }
  .acc { grid-template-columns: 64px minmax(0, 1fr); gap: 16px 18px; }
  .acc-sw { width: 64px; height: 64px; }
  .acc .tones, .acc .use { grid-column: 1 / -1; }
  .fam li { grid-template-columns: minmax(0, 1fr); gap: 2px; }
}
@media (max-width: 480px) {
  .ansi { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .use div { grid-template-columns: 1fr; gap: 0; }
  .src { display: none; }
}
@media (max-width: 420px) {
  .top .mark-name { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  body, .top, .shot { transition: none; }
}
"""

# Runs before the page renders, so the right variant shows from the first frame.
EARLY_JS = """(function () {
  var r = document.documentElement, t = r.getAttribute('data-theme');
  r.setAttribute('data-variant', t === 'dark' || t === 'light' ? t
    : (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
})();"""

JS = """
(function () {
  const root = document.documentElement;
  const mq = matchMedia('(prefers-color-scheme: dark)');
  const buttons = [...document.querySelectorAll('.switch button')];
  let chosen = null;
  function apply() {
    const theme = root.getAttribute('data-theme');
    const v = chosen || (theme === 'dark' || theme === 'light' ? theme : (mq.matches ? 'dark' : 'light'));
    if (root.getAttribute('data-variant') !== v) root.setAttribute('data-variant', v);
    for (const b of buttons) b.setAttribute('aria-pressed', String(b.dataset.set === v));
  }
  for (const b of buttons) b.addEventListener('click', () => { chosen = b.dataset.set; apply(); });
  mq.addEventListener('change', apply);
  new MutationObserver(apply).observe(root, { attributes: true, attributeFilter: ['data-theme'] });
  apply();

  for (const list of document.querySelectorAll('[role="tablist"]')) {
    const tabs = [...list.querySelectorAll('[role="tab"]')];
    const select = (tab) => {
      for (const t of tabs) {
        const on = t === tab;
        t.setAttribute('aria-selected', String(on));
        t.tabIndex = on ? 0 : -1;
        document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
      }
    };
    for (const t of tabs) {
      t.addEventListener('click', () => select(t));
      t.addEventListener('keydown', (e) => {
        const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (!step) return;
        const next = tabs[(tabs.indexOf(t) + step + tabs.length) % tabs.length];
        select(next);
        next.focus();
      });
    }
  }

  for (const b of document.querySelectorAll('.copy')) {
    const pre = b.closest('.snip').querySelector('pre');
    let timer;
    b.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(pre.textContent);
        b.textContent = 'Copied';
      } catch {
        const range = document.createRange();
        range.selectNodeContents(pre);
        getSelection().removeAllRanges();
        getSelection().addRange(range);
        b.textContent = 'Selected';
      }
      clearTimeout(timer);
      timer = setTimeout(() => { b.textContent = 'Copy'; }, 1600);
    });
  }
})();
"""


def style_css(styles: dict[str, tuple[str, ...]], syntax: set[str], terminal: set[str]) -> list[str]:
    css = [f".s-{r} {{ color: var(--s-{r}); }}" for r in sorted(syntax)]
    for role, flags in styles.items():
        decls = []
        if "bold" in flags:
            decls.append("font-weight: 700")
        if "italic" in flags:
            decls.append("font-style: italic")
        lines = [v for f, v in (("underline", "underline"), ("strikethrough", "line-through")) if f in flags]
        if lines:
            decls.append(f"text-decoration: {' '.join(lines)}")
        css.append(f".s-{role} {{ {'; '.join(decls)}; }}")
    css += [f".a-{s} {{ color: var(--t-{s}); }}" for s in sorted(terminal)]
    return css


def build(compare_ref: str | None = None) -> str:
    data = rhino.load()
    if "light" not in data:
        raise SystemExit("preview.py: the showcase needs a [light] section")
    infos = {name: load_variant(data, name) for name in VARIANTS}
    dark, light = infos["dark"], infos["light"]
    canonical = {k: v for k, v in dark.colors.items() if "." not in k}
    styles = rhino.styles(data)
    known = set(dark.roles["syntax"])

    token_css = "\n".join([
        token_rule(":root, .v-light", tokens(light), "light"),
        "@media (prefers-color-scheme: dark) {\n"
        + token_rule(':root:not([data-theme="light"]):not([data-variant="light"])', tokens(dark), "dark") + "\n}",
        token_rule(':root[data-theme="dark"]:not([data-variant="light"]), :root[data-variant="dark"], .v-dark',
                   tokens(dark), "dark"),
        f'.switch [data-set="dark"] i {{ background: {dark.bg}; }}',
        f'.switch [data-set="light"] i {{ background: {light.bg}; }}',
    ])
    compare_html, compare_css = compare_section(compare_ref, data, known) if compare_ref else ("", [])
    role_css = style_css(styles, known, set(dark.roles["terminal"])) + compare_css

    name = esc(data["meta"]["name"])
    # The monogram takes the page's text color, so it follows the variant; the bar is the palette's orange.
    brand = logo.mark("currentColor", dark.colors["orange"], height="18", label="")
    top = f"""
<header class="top">
  <div class="top-in">
    <a class="mark" href="#top">{brand}<span class="mark-name">{name}</span></a>
    <nav class="nav" aria-label="Sections">
      <a href="#screenshots">Screenshots</a><a href="#editor">Editor</a><a href="#palette">Palette</a>
      <a href="#readability">Readability</a><a href="#terminal">Terminal</a><a class="keep" href="#install">Install</a>
    </nav>
    <div class="switch" role="group" aria-label="Variant">
      <button type="button" data-set="dark" aria-pressed="false"><i></i>Dark</button>
      <button type="button" data-set="light" aria-pressed="false"><i></i>Light</button>
    </div>
    <a class="src" href="{rhino.REPO_URL}">GitHub</a>
  </div>
</header>"""
    footer = f"""
<footer>
  <a class="mark" href="#top">{brand}{name}</a>
  <p><a href="{rhino.REPO_URL}/releases/tag/v{rhino.VERSION}">Version {rhino.VERSION}</a> ·
  <a href="{rhino.REPO_URL}">Source on GitHub</a> · <a href="{rhino.REPO_URL}/blob/main/docs/design.md">Design notes</a> ·
  <a href="{rhino.REPO_URL}/blob/main/LICENSE">MIT license</a></p>
  <p>Every color on this page is generated from
  <a href="{rhino.REPO_URL}/blob/main/palette.toml"><code>palette.toml</code></a>.</p>
</footer>"""

    fonts = ("https://fonts.googleapis.com/css2?family=Archivo:ital,wdth,wght@0,62..125,100..900;1,62..125,100..900"
             "&family=JetBrains+Mono:ital,wght@0,100..800;1,100..800&display=swap")
    social = f"{rhino.SITE_URL}images/{SCREENSHOTS[0][0]}-dark.png"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{name}: a warm color scheme for editors and terminals</title>
<meta name="description" content="{esc(DESCRIPTION)}">
<link rel="canonical" href="{rhino.SITE_URL}">
<meta name="google-site-verification" content="{GOOGLE_SITE_VERIFICATION}">
<link rel="icon" href="icon-light.svg" type="image/svg+xml" media="(prefers-color-scheme: light)">
<link rel="icon" href="icon-dark.svg" type="image/svg+xml" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="{light.bg}" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="{dark.bg}" media="(prefers-color-scheme: dark)">
<meta property="og:type" content="website">
<meta property="og:title" content="{name}">
<meta property="og:description" content="{esc(DESCRIPTION)}">
<meta property="og:url" content="{rhino.SITE_URL}">
<meta property="og:image" content="{social}">
<meta property="og:image:alt" content="{esc(SCREENSHOTS[0][2])}, dark">
<meta name="twitter:card" content="summary_large_image">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{fonts}">
<style>
{token_css}
{CSS}
{chr(10).join(role_css)}
</style>
<script>{EARLY_JS}</script>
</head>
<body>
<div id="top"></div>
{top}
<main class="wrap">
{hero(data, infos, canonical, known)}
{compare_html}
{screenshots_section()}
{editor_section(known)}
{palette_section(infos, canonical)}
{roles_section(infos, styles)}
{readability_section(infos)}
{terminal_section(infos)}
{install_section(known)}
{reference_section(infos, styles)}
{footer}
</main>
<script>{JS}</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the site, build/site/, from palette.toml.")
    parser.add_argument("--compare", metavar="REF",
                        help="add a before/after section against palette.toml at this git ref (for review)")
    args = parser.parse_args()
    page = build(args.compare)
    # build/site/ holds only what this script writes, so it starts empty each time.
    shutil.rmtree(SITE, ignore_errors=True)
    (SITE / "images").mkdir(parents=True)
    (SITE / "index.html").write_text(page, encoding="utf-8")
    for key, *_ in SCREENSHOTS:
        for v in VARIANTS:
            shutil.copy2(IMAGES / f"{key}-{v}.png", SITE / "images")
    for v in VARIANTS:
        shutil.copy2(IMAGES / f"icon-{v}.svg", SITE)
    print(f"wrote {SITE.relative_to(rhino.ROOT)}/index.html and its images")
    return 0


if __name__ == "__main__":
    sys.exit(main())
