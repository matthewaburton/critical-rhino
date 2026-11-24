"""Print every terminal color, and check the terminal is drawing Critical Rhino's.

    python3 scripts/term_test.py                  # run it in the terminal you're testing
    python3 scripts/term_test.py --variant light  # if the terminal can't be asked
    python3 scripts/term_test.py --no-query       # don't ask the terminal anything

First it asks the terminal for its background, foreground, cursor and 16 palette
colors (OSC 11, 10, 12 and 4; Ghostty, VS Code's terminal, xterm and most others
answer), works out which variant it's showing, and compares every color with the
palette. A color that doesn't match usually means a line in the terminal's own
config is overriding the theme.

Then it prints each ANSI color normal, bold, faint, italic and underlined, with its
contrast on the background and the selection; each color as a background; the
256-color cube; and the things only a person can check (selection, search, the
cursor, splits).
"""

from __future__ import annotations

import argparse
import os
import re
import select
import sys
import termios
import textwrap
import tty

import build_ghostty
import rhino

SAMPLE = "Rhino"
REPLY = re.compile(rb"\x1b\](4;(\d+)|10|11|12);rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)(?:\x07|\x1b\\)")
DA1 = re.compile(rb"\x1b\[\?[0-9;]*c")  # every terminal answers this, so it marks the end of the replies
OSC_NAMES = {b"10": "foreground", b"11": "background", b"12": "cursor"}


def sgr(*codes: int | str) -> str:
    return f"\x1b[{';'.join(str(c) for c in codes)}m"


RESET = sgr(0)


def fg_code(i: int) -> int:
    return 30 + i if i < 8 else 90 + i - 8


def bg_code(i: int) -> int:
    return 40 + i if i < 8 else 100 + i - 8


# --- asking the terminal ---------------------------------------------------------------

def query_terminal(timeout: float = 0.5) -> dict:
    """Ask for the background, foreground, cursor and palette 0-15. Returns what came back."""
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return {}
    old = termios.tcgetattr(fd)
    buf = b""
    try:
        tty.setraw(fd)
        asks = [b"\x1b]11;?\x1b\\", b"\x1b]10;?\x1b\\", b"\x1b]12;?\x1b\\"]
        asks += [f"\x1b]4;{i};?\x1b\\".encode() for i in range(16)]
        os.write(fd, b"".join(asks) + b"\x1b[c")
        while not DA1.search(buf):
            ready, _, _ = select.select([fd], [], [], timeout)
            if not ready:
                break
            buf += os.read(fd, 4096)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        os.close(fd)
    return parse_replies(buf)


def parse_replies(buf: bytes) -> dict:
    """{"background": "#RRGGBB", ..., 0: "#RRGGBB", ...} from OSC color replies."""
    def channel(h: bytes) -> int:
        return round(int(h, 16) * 255 / (16 ** len(h) - 1))

    got: dict = {}
    for m in REPLY.finditer(buf):
        key = int(m.group(2)) if m.group(2) else OSC_NAMES[m.group(1)]
        got[key] = "#" + "".join(f"{channel(m.group(n)):02X}" for n in (3, 4, 5))
    return got


# --- what the theme says ---------------------------------------------------------------

def expected(v: rhino.Variant) -> dict:
    bg = v.t("background")
    return {"background": bg, "foreground": v.t("foreground"),
            "cursor": rhino.composite(v.term["cursor"], bg)} | dict(enumerate(rhino.terminal_colors(v)))


def detect(got: dict, variants: dict[str, rhino.Variant]) -> str | None:
    for name, v in variants.items():
        if got.get("background") == v.t("background"):
            return name
    return None


def report_query(got: dict, want: dict, name: str | None, asked: bool) -> list[str]:
    if not asked:
        return ["Not asking the terminal (--no-query); showing the palette only."]
    if not got:
        tmux = os.environ.get("TMUX")
        note = " Inside tmux the questions reach tmux, not the terminal: run it outside tmux." if tmux else ""
        return [f"The terminal didn't answer color queries, so nothing was compared.{note}"]
    if name is None:
        return [(f"The terminal's background is {got.get('background', 'unknown')}, which is neither variant's "
                 "background: the theme doesn't seem to be loaded. Showing the dark palette.")]
    keys = ["background", "foreground", "cursor", *range(16)]
    asked_keys = [k for k in keys if k in got]
    wrong = [k for k in asked_keys if got[k] != want[k]]
    matched = len(asked_keys) - len(wrong)
    ending = " exactly." if not wrong else ":"
    lines = [f"The terminal reports the {name} theme: {matched} of {len(asked_keys)} colors match{ending}"]
    for k in wrong:
        label = f"palette {k} ({rhino.ANSI_NAMES[k].replace('_', ' ')})" if isinstance(k, int) else k
        lines.append(f"  {label}: {got[k]}, the theme says {want[k]}")
    if wrong:
        lines.append("  Something overrides these: usually a color line in the terminal's own config.")
    missing = [str(k) for k in keys if k not in got]
    if missing and os.environ.get("TMUX") and not any(isinstance(k, int) for k in got):
        lines.append("  Only tmux answered: it reports the background and foreground but doesn't pass on "
                     "questions about the cursor or the 16 palette colors, so those weren't checked. "
                     "Run it outside tmux to check them.")
    elif missing:
        lines.append(f"  (No answer for: {', '.join(missing)}.)")
    return lines


# --- printing --------------------------------------------------------------------------

COLUMNS = (("normal", 8), ("bold", 8), ("faint", 8), ("italic", 8), ("underline", 10))  # fits 80 columns


def palette_table(want: dict) -> list[str]:
    """Each of the 16 colors and the foreground: a sample in each style, a swatch and the hex."""
    lines = [f"  {'#':>2}  {'color':15s}" + "".join(f"{c:{w}s}" for c, w in COLUMNS) + f"{'swatch':8s}hex"]
    rows = [(i, rhino.ANSI_NAMES[i]) for i in range(16)] + [(None, "foreground")]
    for i, name in rows:
        code = fg_code(i) if i is not None else 39
        styles = [(code,), (1, code), (2, code), (3, code), (4, code)]
        cells = "".join(f"{sgr(*s)}{SAMPLE}{RESET}{' ' * (w - len(SAMPLE))}" for s, (_, w) in zip(styles, COLUMNS, strict=True))
        swatch = f"{sgr(bg_code(i))}{' ' * 6}{RESET}  " if i is not None else " " * 8
        hex_ = want[i] if i is not None else want["foreground"]
        lines.append(f"  {'' if i is None else i:>2}  {name.replace('_', ' '):15s}{cells}{swatch}{hex_}")
    return lines


def background_rows() -> list[str]:
    def row(label: str, text_code: int, offset: int) -> str:
        cells = "".join(f"{sgr(bg_code(i + offset), text_code)}{rhino.ANSI[i]:^8}{RESET}" for i in range(8))
        return f"  {label:13s}{cells}"

    return [row("default text", 39, 0), row("black text", 30, 0), row("white text", 37, 0),
            row("bright, def.", 39, 8), row("bright, black", 30, 8)]


def cube_rows() -> list[str]:
    lines = []
    for g in range(6):
        cells = "".join(f"{sgr(48, 5, 16 + r * 36 + g * 6 + b)}  " for r in range(6) for b in range(6))
        lines.append(f"  {cells}{RESET}")
    lines.append("  " + "".join(f"{sgr(48, 5, n)}   " for n in range(232, 256)) + RESET)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Print every terminal color and check them against the palette.")
    parser.add_argument("--variant", choices=("dark", "light"), help="skip detection and use this variant")
    parser.add_argument("--no-query", action="store_true", help="don't ask the terminal for its colors")
    args = parser.parse_args()

    data = rhino.load()
    variants = {n: rhino.load_variant(data, n) for n in rhino.appearances(data)}
    asked = not args.no_query and sys.stdout.isatty()
    got = query_terminal() if asked else {}
    name = args.variant or detect(got, variants)
    v = variants[name or "dark"]
    want = expected(v)
    faint = build_ghostty.faint_opacity(v)
    sel = rhino.composite(v.term["selection"], v.t("background"))
    if build_ghostty.bold_is_bright(v):
        bold = "Bold uses the bright colors here, so the bold column matches rows 8-15."
    else:
        bold = "Bold keeps its own color here: the brights are too light for text."

    out = [f"{sgr(1)}Critical Rhino terminal test{RESET} ({name or 'dark, assumed'})", ""]
    out += wrap(report_query(got, want, name, asked)) + [""]
    out += [f"{sgr(1)}The 16 colors{RESET}", *palette_table(want), ""]
    minimums = ("  The normals and foreground are text: at least 4.5:1 on the background and the selection. "
                "Brights and bright black are 3:1 accents; black and bright white are exempt.")
    out += wrap([minimums, f"  {bold}", f"  Faint is drawn at {faint:.0%} opacity, which keeps every color at 3:1."])
    out += [""]
    out += [f"{sgr(1)}Each color as a background{RESET}",
            "  (tmux, lazygit and ls choose pairs like these; not every pair can read)", *background_rows(), ""]
    out += [f"{sgr(1)}256 colors{RESET} (fixed xterm values: palette-generate is off)", *cube_rows(), ""]
    out += [f"{sgr(1)}Check by hand in Ghostty{RESET}",
            f"  Selection  drag across the table: colors keep their hue on {sel}",
            f"  Search     Ctrl+Shift+F, type {SAMPLE}: matches {v.u('search')}, current {v.u('search_current')}",
            f"  Cursor     in nvim, the normal-mode block is {want['cursor']} with {v.on(want['cursor'])} text",
            "  Splits     Ctrl+Shift+O: the other split dims to 70% but stays readable",
            ""]
    print("\n".join(out))
    return 0


def wrap(lines: list[str], width: int = 78) -> list[str]:
    """Wrap prose lines to fit an 80-column terminal, keeping each line's indent."""
    out = []
    for line in lines:
        indent = " " * (len(line) - len(line.lstrip()))
        out += textwrap.wrap(line, width, initial_indent="", subsequent_indent=indent + "  ") or [""]
    return out


if __name__ == "__main__":
    sys.exit(main())
