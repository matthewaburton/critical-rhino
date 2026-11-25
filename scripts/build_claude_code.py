"""Build the Claude Code themes from palette.toml.

    python3 scripts/build_claude_code.py           # write extras/claude-code/themes/critical-rhino-{dark,light}.json
    python3 scripts/build_claude_code.py --check   # fail (exit 1) if they're stale or a pair is unreadable
    python3 scripts/build_claude_code.py --report  # print every measured pair

A Claude Code theme is a JSON file in ~/.claude/themes: a name, a built-in theme to
start from (`base`) and colors for its tokens (`overrides`). Claude Code draws on the
terminal's own background, so every pair here is measured the way Ghostty draws it
(build_ghostty.Terminal).

Code isn't themable: Claude Code highlights code in fixed schemes that its base picks,
and decides itself which color each kind of code takes. These themes start from the
ANSI bases (dark-ansi, light-ansi), whose schemes use the terminal's own colors,
Critical Rhino's in Ghostty, held to their minimums there. They don't follow the
editors' roles. Code blocks are in the normals: strings red and comments green, as in
the editors, but keywords and inline code blue (bright blue in dark), function names
yellow, types and built-ins cyan, decorators bright black, the rest the text color.
Diffs are highlighted in the brights (3:1): strings bright green, keywords and types
bright cyan, names bright yellow, numbers bright blue, with the code text in white
(dark) or black (light) and the markers in bright green and red. Removed lines aren't
highlighted: their code is drawn faint (3:1), with the line number and marker in bright
red. Other line numbers are faint too.

Every token is set to an exact color except the diff backgrounds: under the ANSI bases
Claude Code draws those from the 256-color palette, so the themes name entries from it
rather than let it round. Code text on them has to reach 4.5:1, the brights 3:1 and a
removed line's faint code 3:1. In dark that leaves the darkest navy for added lines (no
green is dark enough) and, for removed lines, a near-black a step darker than the
background (no red is dark enough under faint code); light takes the palest cyan, to
match, and pink. Changed words are cut out in the terminal's extreme, black in dark and
white in light, since no stronger tint keeps the code readable. Added and removed
lines, and words and their lines, stay 0.08 apart in OKLab, the palette's rule for
telling syntax colors apart.

Where each token lands was measured in Claude Code 2.1.332 (a probe theme with a
unique color per token): the prompt rules (promptBorder), dividers (subtle), dim text
(inactive, also drawn faint, bold and italic), the spinner (claude fading to
claudeShimmer), permission prompts, mode labels (autoAccept, planMode; auto mode in
warning), the bash border, your messages (text on userMessageBackground) and bash
commands (text and bashBorder on bashMessageBackgroundColor). Tokens not seen drawn
are text on the terminal background, as nearly everything in Claude Code is;
inverseText is text on a colored fill, so it's checked on every accent. Shimmers fade
toward the text color, so both ends of every fade read.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import build_ghostty
import rhino
from build_ghostty import Pair, Terminal

THEME_DIR = rhino.ROOT / "extras" / "claude-code" / "themes"
BASES = {"dark": "dark-ansi", "light": "light-ansi"}
T, M, B = rhino.TEXT_MIN, rhino.DIM_MIN, rhino.BRIGHT_MIN
DISTINCT = 0.08  # OKLab, as check.py holds syntax colors apart

# Claude Code 2.1.332's tokens, in its order. An override for any other name is dropped
# without a word, so the check holds the themes to this list.
TOKENS = (
    "autoAccept", "autoAcceptShimmer", "skill", "bashBorder", "claude", "claudeShimmer",
    "claudeBlue_FOR_SYSTEM_SPINNER", "claudeBlueShimmer_FOR_SYSTEM_SPINNER", "permission", "permissionShimmer",
    "planMode", "ide", "promptBorder", "promptBorderShimmer", "text", "inverseText", "inactive", "inactiveShimmer",
    "subtle", "suggestion", "remember", "background", "success", "error", "warning", "merged", "warningShimmer",
    "diffAdded", "diffRemoved", "diffAddedDimmed", "diffRemovedDimmed", "diffAddedWord", "diffRemovedWord",
    "red_FOR_SUBAGENTS_ONLY", "blue_FOR_SUBAGENTS_ONLY", "green_FOR_SUBAGENTS_ONLY", "yellow_FOR_SUBAGENTS_ONLY",
    "purple_FOR_SUBAGENTS_ONLY", "orange_FOR_SUBAGENTS_ONLY", "pink_FOR_SUBAGENTS_ONLY", "cyan_FOR_SUBAGENTS_ONLY",
    "professionalBlue", "chromeYellow", "clawd_body", "clawd_background", "userMessageBackground",
    "userMessageBackgroundHover", "composerSidebarBackground", "selectionBg", "bashMessageBackgroundColor",
    "memoryBackgroundColor", "rate_limit_fill", "rate_limit_empty", "fastMode", "fastModeShimmer", "effortUltra",
    "briefLabelYou", "briefLabelClaude", "rainbow_red", "rainbow_orange", "rainbow_yellow", "rainbow_green",
    "rainbow_blue", "rainbow_indigo", "rainbow_violet", "rainbow_red_shimmer", "rainbow_orange_shimmer",
    "rainbow_yellow_shimmer", "rainbow_green_shimmer", "rainbow_blue_shimmer", "rainbow_indigo_shimmer",
    "rainbow_violet_shimmer",
)

# The surfaces Claude Code draws text on, besides the terminal's background.
FILLS = {
    "userMessageBackground": "surface",        # your messages
    "userMessageBackgroundHover": "raised",    # a message or list row under the pointer
    "composerSidebarBackground": "surface",    # fullscreen
    "selectionBg": "selection",                # fullscreen: selected text keeps its colors
    "bashMessageBackgroundColor": "surface",   # bash commands (! mode)
    "memoryBackgroundColor": "surface",
    "clawd_background": "background",          # the logo sits on the terminal's background
}
TEXT_FILLS = tuple(f for f in FILLS if f != "clawd_background")

ON_BG = ("terminal",)
# token: (source, minimum, drawn on). A source is a palette name, or one of
# foreground/background/muted/dim/pink (see color()). Shimmers fade toward the text color.
INK: dict[str, tuple[str, float, tuple[str, ...]]] = {
    "autoAccept": ("readable.purple", T, ON_BG),
    "skill": ("readable.purple", T, ON_BG),
    "bashBorder": ("readable.purple", T, ON_BG + ("bashMessageBackgroundColor",)),
    "claude": ("readable.orange", T, ON_BG),
    "claudeBlue_FOR_SYSTEM_SPINNER": ("readable.blue", T, ON_BG),
    "permission": ("readable.blue_accent_1", T, ON_BG),
    "planMode": ("readable.cyan", T, ON_BG),
    "ide": ("readable.blue", T, ON_BG),
    "promptBorder": ("dim", M, ON_BG),
    "text": ("foreground", T, ON_BG + TEXT_FILLS),
    "inverseText": ("background", T, ()),      # on every accent; see pairs()
    "inactive": ("muted", T, ON_BG + ("userMessageBackground",)),
    "subtle": ("dim", M, ON_BG),
    "suggestion": ("readable.blue_accent_1", T, ON_BG),
    "remember": ("readable.blue", T, ON_BG),
    "background": ("readable.cyan", T, ON_BG),   # an accent, despite the name
    "success": ("readable.green", T, ON_BG),
    "error": ("readable.red", T, ON_BG),
    "warning": ("readable.yellow", T, ON_BG),
    "merged": ("readable.purple", T, ON_BG),
    "red_FOR_SUBAGENTS_ONLY": ("readable.red", T, ON_BG),
    "blue_FOR_SUBAGENTS_ONLY": ("readable.blue", T, ON_BG),
    "green_FOR_SUBAGENTS_ONLY": ("readable.green", T, ON_BG),
    "yellow_FOR_SUBAGENTS_ONLY": ("readable.yellow", T, ON_BG),
    "purple_FOR_SUBAGENTS_ONLY": ("readable.purple", T, ON_BG),
    "orange_FOR_SUBAGENTS_ONLY": ("readable.orange", T, ON_BG),
    "pink_FOR_SUBAGENTS_ONLY": ("pink", T, ON_BG),
    "cyan_FOR_SUBAGENTS_ONLY": ("readable.cyan", T, ON_BG),
    "professionalBlue": ("readable.blue", T, ON_BG),
    "chromeYellow": ("readable.yellow", T, ON_BG),
    "clawd_body": ("readable.orange", M, ON_BG + ("clawd_background",)),
    "rate_limit_fill": ("readable.blue_accent_1", M, ON_BG),
    "rate_limit_empty": ("dim", M, ON_BG),
    "fastMode": ("readable.orange", T, ON_BG),
    "effortUltra": ("readable.purple", T, ON_BG),
    "briefLabelYou": ("readable.blue", T, ON_BG),
    "briefLabelClaude": ("readable.orange", T, ON_BG),
    "rainbow_red": ("readable.red", T, ON_BG),
    "rainbow_orange": ("readable.orange", T, ON_BG),
    "rainbow_yellow": ("readable.yellow", T, ON_BG),
    "rainbow_green": ("readable.green", T, ON_BG),
    "rainbow_blue": ("readable.cyan", T, ON_BG),
    "rainbow_indigo": ("readable.blue", T, ON_BG),
    "rainbow_violet": ("readable.purple", T, ON_BG),
}
SHIMMERS = {
    "autoAcceptShimmer": "autoAccept", "claudeShimmer": "claude",
    "claudeBlueShimmer_FOR_SYSTEM_SPINNER": "claudeBlue_FOR_SYSTEM_SPINNER", "permissionShimmer": "permission",
    "promptBorderShimmer": "promptBorder", "inactiveShimmer": "inactive", "warningShimmer": "warning",
    "fastModeShimmer": "fastMode",
    **{f"rainbow_{c}_shimmer": f"rainbow_{c}" for c in ("red", "orange", "yellow", "green", "blue", "indigo", "violet")},
}
for _shimmer, _of in SHIMMERS.items():
    INK[_shimmer] = ("foreground", INK[_of][1], INK[_of][2])

# Diff backgrounds: entries of the 256-color palette (see the module docstring).
# (line, changed words). The dimmed lines keep the line's color.
DIFFS = {
    "dark": {"added": (17, 16), "removed": (232, 16)},     # navy, near-black; words on black
    "light": {"added": (159, 231), "removed": (224, 231)},  # pale cyan, pink; words on white
}
# What Claude Code 2.1.332 draws under the ANSI bases, as terminal palette indices
# (measured). On a diff line: code text, scopes in the brights, and the markers.
DIFF_TEXT = {"dark": 7, "light": 0}
SCOPES = (("keywords", 13), ("types", 14), ("numbers", 12), ("strings", 10), ("names", 11), ("comments", 8))
MARKERS = {"added": 10, "removed": 9}
# In code blocks, on the terminal's background (None is the text color). Inline code
# is the keywords' blue.
CODE_BLOCKS = {
    name: (("text", None, T), ("keywords and inline code", keyword, T), ("function names", 3, T), ("types", 6, T),
           ("strings", 1, T), ("comments", 2, T), ("decorators", 8, M))
    for name, keyword in (("dark", 12), ("light", 4))
}


def color(v: rhino.Variant, source: str) -> str:
    """A source as an opaque color in one variant."""
    bg = v.t("background")
    if source == "foreground":
        return v.u("foreground", bg)
    if source == "background":
        return bg
    if source == "muted":      # secondary text: the terminal's white
        return v.t("white")
    if source == "dim":        # dim text and marks: the terminal's bright black, 3:1
        return v.t("bright_black")
    if source == "pink":       # bright magenta where it reads as text, else purple
        pink = v.t("bright_magenta")
        return pink if rhino.contrast(pink, bg) >= T else v.colors["readable.purple"]
    if source == "surface":
        return v.u("surface", bg)
    if source == "raised":
        return v.u("surface_raised", bg)
    if source == "selection":
        return rhino.composite(v.term["selection"], bg)
    return v.colors[source]


def diff_entries(name: str) -> dict[str, int]:
    out = {}
    for kind, (line, word) in DIFFS[name].items():
        k = kind.capitalize()
        out |= {f"diff{k}": line, f"diff{k}Dimmed": line, f"diff{k}Word": word}
    return out


def overrides(v: rhino.Variant) -> dict[str, str]:
    """Every token's value in one variant, in Claude Code's order."""
    diffs = diff_entries(v.name)
    out = {}
    for token in TOKENS:
        if token in diffs:
            out[token] = f"ansi256({diffs[token]})"
        elif token in FILLS:
            out[token] = color(v, FILLS[token])
        else:
            out[token] = color(v, INK[token][0])
    return out


def theme_file(v: rhino.Variant) -> str:
    theme = {"name": f"Critical Rhino {v.name.title()}", "base": BASES[v.name], "overrides": overrides(v)}
    return json.dumps(theme, indent=2) + "\n"


def pairs(v: rhino.Variant) -> tuple[list[Pair], list[Pair]]:
    """(pairs the theme decides, pairs Claude Code fixes) in one variant."""
    values = overrides(v)
    found: list[Pair] = []
    for token, (_, minimum, on) in INK.items():
        if token == "inverseText":
            continue
        for surface in on:
            under = None if surface == "terminal" else values[surface]
            where = "the background" if surface == "terminal" else surface
            found.append(Pair(f"{token} on {where}", {"fg": values[token], "bg": under}, minimum))
    # Dim text is also drawn faint (SGR 2), at Ghostty's faint opacity: 3:1.
    found.append(Pair("inactive, faint, on the background", {"fg": values["inactive"], "faint": True}, M))
    accents = sorted({t for t, (src, _, _) in INK.items() if src.startswith("readable.") or src == "pink"})
    for fill in accents:
        found.append(Pair(f"inverseText on {fill}", {"fg": values["inverseText"], "bg": values[fill]}, T))
    for kind, (line, word) in DIFFS[v.name].items():
        for part, bg in (("line", line), ("words", word)):
            label = f"{kind} {part} (ansi256 {bg})"
            found.append(Pair(f"code text on the {label}", {"fg": DIFF_TEXT[v.name], "bg": bg}, T))
            # With highlighting off (Ctrl+T in /theme), a diff's code is in the text color.
            found.append(Pair(f"text on the {label}", {"fg": values["text"], "bg": bg}, T))
            if kind == "removed":   # a removed line's code is drawn faint
                found.append(Pair(f"code text, faint, on the {label}",
                                  {"fg": DIFF_TEXT[v.name], "bg": bg, "faint": True}, M))
            found.append(Pair(f"{kind} marker on the {label}", {"fg": MARKERS[kind], "bg": bg}, M))
            for scope, index in SCOPES:
                found.append(Pair(f"{scope} on the {label}", {"fg": index, "bg": bg}, B))
    own = [Pair(f"code block {scope}", {"fg": index}, minimum) for scope, index, minimum in CODE_BLOCKS[v.name]]
    # Around the changes, on the terminal's background: faint line numbers, highlighted code.
    own.append(Pair("diff line numbers, faint", {"fg": DIFF_TEXT[v.name], "faint": True}, M))
    own.append(Pair("diff context text", {"fg": DIFF_TEXT[v.name]}, T))
    own += [Pair(f"diff context {scope}", {"fg": index}, B) for scope, index in SCOPES]
    # With highlighting off, a code block is plain text under its language, drawn faint.
    own.append(Pair("code block language, faint, highlighting off", {"faint": True}, M))
    return found, own


def distinct(v: rhino.Variant) -> tuple[list[str], list[str]]:
    """Added and removed lines, and words and their lines, must look different."""
    xt = build_ghostty.xterm
    d = DIFFS[v.name]
    checks = [("added and removed lines", d["added"][0], d["removed"][0])]
    checks += [(f"{kind} words and their line", line, word) for kind, (line, word) in d.items()]
    errors, report = [], []
    for label, a, b in checks:
        gap = rhino.distance(xt(a), xt(b))
        below = gap < DISTINCT
        if below:
            errors.append(f"{v.name}: {label} are {gap:.3f} apart in OKLab, under {DISTINCT}")
        report.append(f"  {label:58s} {gap:5.3f} apart{f'  <- under {DISTINCT}' if below else ''}")
    return errors, report


TOKEN_VALUE = re.compile(r"^(#[0-9A-F]{6}|ansi256\((\d{1,3})\))$")


def validate(v: rhino.Variant) -> list[str]:
    """What Claude Code would drop without a word: unknown tokens, values it can't read."""
    errors = []
    values = overrides(v)
    missing = set(TOKENS) - set(INK) - set(FILLS) - set(diff_entries(v.name))
    errors += [f"{v.name}: no color for {t}" for t in sorted(missing)]
    for token, value in values.items():
        m = TOKEN_VALUE.match(value)
        if not m or (m.group(2) and not 16 <= int(m.group(2)) <= 255):
            errors.append(f"{v.name}: {token} = {value!r} isn't a color Claude Code reads")
    return errors


def outputs(data: dict) -> tuple[dict[Path, str], list[str], list[str]]:
    files, errors, report = {}, [], []
    for name in rhino.appearances(data):
        v = rhino.load_variant(data, name)
        files[THEME_DIR / f"critical-rhino-{name}.json"] = theme_file(v)
        term = Terminal(v)
        found, own = pairs(v)
        e, r = build_ghostty.measure(term, found)
        de, dr = distinct(v)
        errors += validate(v) + e + de
        report += [f"===== {name} ({BASES[name]}) =====", *r, *dr,
                   "  [Claude Code's own, not failed: no setting reaches these]", *build_ghostty.measure(term, own)[1],
                   ""]
    return files, errors, report


if __name__ == "__main__":
    sys.exit(build_ghostty.tool_main("claude-code", outputs))
