"""Run starship, fzf and lazygit in a private tmux server and measure what they draw.

    python3 scripts/audit_terminal.py             # with the themes
    python3 scripts/audit_terminal.py --defaults  # with each tool's own colors, for comparison
    python3 scripts/audit_terminal.py --shots     # also save each scene to build/terminal-audit/ (.ans, .html, .png)

Each scene runs a tool in a detached tmux pane, drives it with keys, and captures the
pane with its colors (capture-pane -e). Every character is measured as Ghostty draws
it in each variant (build_ghostty.Terminal): text at 4.5:1, faint text and
line-drawing characters (borders, scrollbars) at 3:1. Nothing touches your own
configs: each tool gets a throwaway HOME, git config and lazygit config folder, and
the scenes' repository is made up.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import build_fzf
import build_ghostty
import build_lazygit
import build_starship
import rhino

OUT = rhino.ROOT / "build" / "terminal-audit"
SOCKET = "rhino-cli-audit"
SGR = re.compile(r"\x1b\[([0-9;:]*)m")
LINE_CHARS = range(0x2500, 0x25A0)  # box drawing and block elements: borders, scrollbars, pointers
# Pictures are set in Source Code Pro, with Nerd Font symbols for the icons prompts and
# editors draw (a local copy if there is one, otherwise from the CDN).
NERD_FONT = ("https://cdn.jsdelivr.net/gh/ryanoasis/nerd-fonts@v3.2.1/patched-fonts/NerdFontsSymbolsOnly/"
             "SymbolsNerdFontMono-Regular.ttf")
FONT = '"Source Code Pro","Symbols Nerd Font Mono","DejaVu Sans Mono",monospace'


# --- reading a capture ---------------------------------------------------------------

@dataclass(frozen=True)
class Style:
    fg: int | str | None = None
    bg: int | str | None = None
    bold: bool = False
    faint: bool = False
    reverse: bool = False
    italic: bool = False
    underline: bool = False

    def ink(self) -> dict:
        return {"fg": self.fg, "bg": self.bg, "bold": self.bold, "faint": self.faint, "reverse": self.reverse}


def sgr(params: str, st: dict) -> None:
    """Apply one SGR sequence's parameters to a style dict."""
    ps = params.replace(":", ";").split(";") if params else ["0"]
    i = 0
    while i < len(ps):
        p = ps[i]
        if p in ("", "0"):
            st.clear()
        elif p in ("38", "48") and i + 1 < len(ps):
            key = "fg" if p == "38" else "bg"
            if ps[i + 1] == "2":
                st[key] = "#{:02X}{:02X}{:02X}".format(*(int(x) for x in ps[i + 2:i + 5]))
                i += 4
            elif ps[i + 1] == "5":
                st[key] = int(ps[i + 2])
                i += 2
        elif p.isdigit():
            n = int(p)
            flags = {1: ("bold", True), 2: ("faint", True), 3: ("italic", True), 4: ("underline", True),
                     7: ("reverse", True), 23: ("italic", False), 24: ("underline", False), 27: ("reverse", False)}
            if n in flags:
                st[flags[n][0]] = flags[n][1]
            elif n == 22:
                st["bold"] = st["faint"] = False
            elif 30 <= n <= 37 or 90 <= n <= 97:
                st["fg"] = n - 30 if n < 90 else n - 82
            elif 40 <= n <= 47 or 100 <= n <= 107:
                st["bg"] = n - 40 if n < 100 else n - 92
            elif n == 39:
                st.pop("fg", None)
            elif n == 49:
                st.pop("bg", None)
        i += 1


def parse(text: str) -> list[list[tuple[str, Style]]]:
    """A capture as rows of (character, style). A style carries over from the end of one line
    to the next: tmux emits only changes, even across lines."""
    rows = []
    st: dict = {}
    for raw in text.split("\n"):
        cells, pos = [], 0
        for m in SGR.finditer(raw):
            style = Style(**st)
            cells += [(c, style) for c in raw[pos:m.start()]]
            sgr(m.group(1), st)
            pos = m.end()
        style = Style(**st)
        cells += [(c, style) for c in raw[pos:]]
        rows.append(cells)
    while rows and not "".join(c for c, _ in rows[-1]).strip():
        rows.pop()
    return rows


# --- measuring -----------------------------------------------------------------------

@dataclass
class Finding:
    scene: str
    variant: str
    text: str
    style: Style
    ratio: float
    minimum: float


def measure(scene: str, rows: list[list[tuple[str, Style]]], term: build_ghostty.Terminal,
            ignore: Callable[[str, Style], bool] = lambda text, style: False) -> tuple[int, list[Finding]]:
    """Measure every run of characters drawn in one style. Returns (runs measured, findings)."""
    findings, measured, cache = [], 0, {}
    for cells in rows:
        runs: list[tuple[Style, str]] = []
        for ch, style in cells:
            if runs and runs[-1][0] == style:
                runs[-1] = (style, runs[-1][1] + ch)
            else:
                runs.append((style, ch))
        for style, text in runs:
            glyphs = text.strip()
            if not glyphs or ignore(glyphs, style):
                continue
            lines_only = all(ord(c) in LINE_CHARS or c.isspace() for c in glyphs)
            minimum = rhino.DIM_MIN if style.faint or lines_only else rhino.TEXT_MIN
            if isinstance(style.fg, int) and 8 <= style.fg < 16 and not style.reverse:
                # a bright color asked for by number keeps the theme's minimum for it (3:1)
                minimum = min(minimum, rhino.TERMINAL_MIN.get(rhino.ANSI_NAMES[style.fg], minimum))
            if style not in cache:
                cache[style] = min(rhino.contrast(f, b) for f, b in term.draw(**style.ink()))
            measured += 1
            if cache[style] < minimum - 1e-9:
                findings.append(Finding(scene, term.name, glyphs, style, cache[style], minimum))
    return measured, findings


# --- driving tmux --------------------------------------------------------------------

class Tmux:
    """A private tmux server with one pane. With `env`, the server starts with only that
    environment, so nothing else from the caller's reaches the pane."""

    def __init__(self, work: Path, cols: int, rows: int, socket: str = SOCKET,
                 env: dict[str, str] | None = None, options: str = "") -> None:
        conf = work / "tmux.conf"
        conf.write_text("set -g status off\nset -g default-terminal tmux-256color\nset -sg escape-time 0\n" + options)
        self.base = ["tmux", "-L", socket, "-f", str(conf)]
        self.size = (cols, rows)
        self.env = env

    def run(self, *args: str) -> str:
        out = subprocess.run(self.base + list(args), capture_output=True, text=True, check=False, env=self.env)
        if out.returncode != 0:
            raise RuntimeError(f"tmux {args[0]} failed (exit {out.returncode}): {out.stderr.strip() or out.stdout.strip()}")
        return out.stdout

    def start(self, command: str, env: dict[str, str]) -> None:
        self.stop()
        pairs = [x for k, v in env.items() for x in ("-e", f"{k}={v}")]
        self.run("new-session", "-d", "-s", "a", "-x", str(self.size[0]), "-y", str(self.size[1]), *pairs,
                 "bash", "--norc", "--noprofile", "-c", command)

    def keys(self, *keys: str, wait: float = 0.4) -> None:
        self.run("send-keys", "-t", "a", *keys)
        time.sleep(wait)

    def capture(self) -> str:
        return self.run("capture-pane", "-t", "a", "-e", "-p", "-N")

    def stop(self) -> None:
        """Kill the server and wait until it's gone: kill-server returns while it's still
        exiting, and a session started then can reach the dying server and fail."""
        subprocess.run(self.base + ["kill-server"], capture_output=True, check=False, env=self.env)
        for _ in range(100):
            if subprocess.run(self.base + ["list-sessions"], capture_output=True, check=False, env=self.env).returncode != 0:
                return
            time.sleep(0.05)


# --- the made-up world the scenes run in ---------------------------------------------

def git(cwd: Path, env: dict[str, str], *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, check=True)


def make_world(work: Path) -> dict[str, str]:
    """A throwaway HOME with two projects, and the environment every scene runs in."""
    home = work / "home"
    (home / ".config").mkdir(parents=True)
    gitconfig = home / ".gitconfig"
    gitconfig.write_text("[user]\n\tname = Ada Lovelace\n\temail = ada@example.com\n"
                         "[init]\n\tdefaultBranch = main\n[advice]\n\tdetachedHead = false\n")
    env = {"HOME": str(home), "PATH": os.environ["PATH"], "COLORTERM": "truecolor", "LANG": "C.UTF-8",
           "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1", "XDG_CONFIG_HOME": str(home / ".config"),
           "XDG_STATE_HOME": str(home / ".state"), "XDG_CACHE_HOME": str(home / ".cache")}
    people = [("Ada Lovelace", "ada@example.com"), ("Grace Hopper", "grace@example.com"),
              ("Alan Turing", "alan@example.com"), ("Katherine Johnson", "kj@example.com")]

    remote = work / "remote.git"
    app = home / "projects" / "rhino-app"
    app.mkdir(parents=True)
    git(work, env, "init", "--bare", "-q", str(remote))
    git(app, env, "init", "-q")
    git(app, env, "remote", "add", "origin", str(remote))
    files = {
        "package.json": '{\n  "name": "rhino-app",\n  "version": "1.4.2"\n}\n',
        "README.md": "# Rhino app\n\nTracks rhino sightings.\n",
        "src/herd.py": "def count(herd):\n    return len(herd)\n",
        "src/horn.py": "HORN_CM = 50\n",
    }
    messages = ["Start the rhino app", "Count the herd", "Measure horns in centimetres", "Add sightings",
                "Fix the herd count for calves", "Explain the app in the README", "Group sightings by reserve",
                "Speed up the sightings map"]
    for i, msg in enumerate(messages):
        name, email = people[i % len(people)]
        for path, text in files.items():
            p = app / path
            p.parent.mkdir(exist_ok=True)
            p.write_text(text + f"# {i}\n" * (i % 3 == 0 and path.endswith(".py")))
        files["src/herd.py"] += f"# revision {i}\n"
        cenv = env | {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name,
                      "GIT_COMMITTER_EMAIL": email, "GIT_AUTHOR_DATE": f"2026-11-{10 + i}T10:00:00",
                      "GIT_COMMITTER_DATE": f"2026-11-{10 + i}T10:00:00"}
        git(app, cenv, "add", "-A")
        git(app, cenv, "commit", "-q", "-m", msg)
        if i == 2:
            git(app, env, "tag", "v1.0.0")
        if i == 4:
            git(app, env, "branch", "feature/sightings-map")
            git(app, env, "push", "-q", "origin", "main")
    git(app, env, "branch", "fix/calf-count")
    git(app, env, "tag", "v1.4.2")
    # one stash, then work in progress: staged, modified, untracked, deleted
    (app / "src" / "horn.py").write_text("HORN_CM = 55\n")
    git(app, env, "stash", "-q")
    (app / "src" / "herd.py").write_text("def count(herd):\n    return sum(1 for rhino in herd if rhino)\n")
    git(app, env, "add", "src/herd.py")
    (app / "README.md").write_text("# Rhino app\n\nTracks rhino sightings across reserves.\n")
    (app / "src" / "reserves.py").write_text("RESERVES = ['Etosha', 'Kruger']\n")
    (app / "src" / "horn.py").unlink()

    lib = home / "projects" / "horn-lib"
    lib.mkdir(parents=True)
    (lib / "pyproject.toml").write_text('[project]\nname = "horn-lib"\nversion = "0.3.0"\n')
    (lib / "horn.py").write_text("print('horn')\n")
    return env


# --- scenes --------------------------------------------------------------------------

@dataclass
class Scene:
    name: str
    tool: str
    cols: int
    rows: int
    # (tmux, env, variant) -> [(view name, capture)]: drives the tool and captures each view
    play: Callable[[Tmux, dict[str, str], str | None], list[tuple[str, str]]]
    per_variant: bool = False   # run once per variant, telling the tool which it's in
    ignore: Callable[[str, Style], bool] = field(default=lambda text, style: False)


def starship_prompts(tmux: Tmux, env: dict[str, str], variant: str | None) -> None:
    home = env["HOME"]
    steps = [
        (home, "--status=0", "cd projects/rhino-app"),
        (f"{home}/projects/rhino-app", "--status=0 --jobs=1", "npm test"),
        (f"{home}/projects/rhino-app", "--status=1 --cmd-duration=4200", "cd ../horn-lib"),
        (f"{home}/projects/horn-lib", "--status=0", ""),
    ]
    script = ["unset STARSHIP_SHELL"]
    for cwd, flags, typed in steps:
        script.append(f"cd {shlex.quote(cwd)} && starship prompt {flags} && printf '%s\\n' {shlex.quote(typed)}")
    script.append("sleep 60")
    tmux.start("; ".join(script), env)
    time.sleep(2.5)
    return [("starship-prompts", tmux.capture())]


FILES = ["src/herd.py", "src/horn.py", "src/reserves.py", "src/sightings/map.py", "src/sightings/__init__.py",
         "tests/test_herd.py", "tests/test_horn.py", "docs/rhino-facts.md", "docs/reserves/etosha.md",
         "docs/reserves/kruger.md", "README.md", "package.json", "scripts/import_sightings.py",
         "scripts/rhino_report.py", "assets/rhino.svg", "assets/horn.png", ".github/workflows/ci.yml"]


def fzf_picker(tmux: Tmux, env: dict[str, str], variant: str | None) -> None:
    items = shlex.quote("\n".join(FILES))
    opts = ("--multi --border --border-label=' files ' --header='TAB marks a file' "
            "--preview='printf \"%s\\n\" {} \"is a rhino file\"' --preview-window=right,40% --preview-label=' preview '")
    tmux.start(f"printf '%s\\n' {items} | fzf {opts}; sleep 60", env)
    time.sleep(1.0)
    # Tab marks the line and moves down, toward the prompt: mark the first two matches, end on the third
    for key in ("r", "h", "Tab", "Up", "Tab", "Up"):
        tmux.keys(key, wait=0.25)
    tmux.keys("Up", wait=0.8)
    return [("fzf-picker", tmux.capture())]


def lazygit_views(tmux: Tmux, env: dict[str, str], variant: str | None) -> list[tuple[str, str]]:
    """Files, commits (one copied), branches, a menu over the commits, and a search."""
    app = shlex.quote(f"{env['HOME']}/projects/rhino-app")
    overlay = Path(env["HOME"]) / f"lazygit-{variant}.yml"
    overlay.write_text(f"disableStartupPopups: true\nupdate:\n  method: never\ngit:\n  autoFetch: false\n"
                       f"gui:\n  showRandomTip: false\n  colorScheme: {variant}\n")
    files = ",".join(x for x in (env.get("LAZYGIT_THEME"), str(overlay)) if x)
    tmux.start(f"cd {app} && LG_CONFIG_FILE={shlex.quote(files)} lazygit; sleep 60", env)
    time.sleep(3.0)
    shots = [("lazygit-files", tmux.capture())]
    tmux.keys("4", wait=0.8)
    for key in ("Down", "Down", "C", "Down"):     # copy the third commit, then select the fourth
        tmux.keys(key, wait=0.4)
    shots.append(("lazygit-commits", tmux.capture()))
    tmux.keys("?", wait=0.8)                       # the keybindings menu, over the commits
    shots.append(("lazygit-menu", tmux.capture()))
    tmux.keys("Escape", wait=0.5)
    tmux.keys("3", wait=0.8)
    shots.append(("lazygit-branches", tmux.capture()))
    tmux.keys("4", wait=0.5)
    tmux.keys("/", wait=0.4)
    tmux.keys("-l", "herd", wait=0.4)
    tmux.keys("Enter", wait=0.8)
    shots.append(("lazygit-search", tmux.capture()))
    return shots


COLORS_SCRIPT = r"""#!/usr/bin/env bash
# Every text color: normal, bold and faint, then the bright colors.
names=(black red green yellow blue magenta cyan white)
row() {
  printf '%-7s' "$1"
  for i in 1 2 3 4 5 6 7; do printf '\e[%s3%dm%-8s\e[0m' "$2" "$i" "${names[$i]}"; done
  printf '\e[%s39mtext\e[0m\n' "$2"
}
row normal ""
row bold "1;"
row faint "2;"
printf '%-7s' bright
for i in 0 1 2 3 4 5 6; do printf '\e[9%dm%-8s\e[0m' "$i" "${names[$i]}"; done
printf '\n'
"""


def shell_session(tmux: Tmux, env: dict[str, str], variant: str | None) -> list[tuple[str, str]]:
    """An interactive bash with the starship prompt: ls, git and grep in the repository,
    then every text color."""
    home = Path(env["HOME"])
    app = home / "projects" / "rhino-app"
    (app / "build.sh").write_text("#!/bin/sh\necho build\n")
    (app / "build.sh").chmod(0o755)
    (app / "release.tar.gz").write_bytes(b"")
    (app / "readme-link").symlink_to("README.md")
    bin_dir = home / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "colors").write_text(COLORS_SCRIPT)
    (bin_dir / "colors").chmod(0o755)
    tmux.start(f"cd {shlex.quote(str(app))} && PATH={shlex.quote(str(bin_dir))}:$PATH GIT_PAGER=cat "
               "HISTFILE=/dev/null exec bash --norc --noprofile -i", env)
    time.sleep(1.0)
    setup = ['eval "$(starship init bash)"', "clear"]
    typed = ["ls --color=auto -F", "git log --oneline --decorate --branches --tags --remotes -6", "git status -sb",
             "git diff README.md | tail -n 4", "grep -n --color=always herd src/*.py", "colors"]
    for line in setup + typed:
        tmux.keys("-l", line, wait=0.1)
        tmux.keys("Enter", wait=0.8)
    return [("shell", tmux.capture())]


def lazygit_own(text: str, style: Style) -> bool:
    """What lazygit hard-codes that no theme key reaches (build_lazygit.OWN)."""
    return (style.fg == 0 and style.bg in (3, 6)) or (style.fg == 15 and style.bold)


SCENES = [
    Scene("starship-prompts", "starship", 100, 14, starship_prompts),
    Scene("fzf-picker", "fzf", 110, 22, fzf_picker),
    Scene("lazygit", "lazygit", 120, 30, lazygit_views, per_variant=True, ignore=lazygit_own),
    Scene("shell", "starship", 100, 48, shell_session),
]


def theme_env(env: dict[str, str], work: Path, defaults: bool) -> dict[str, str]:
    """Point each tool at the theme, or at nothing for its own colors."""
    env = dict(env)
    empty = work / "empty.toml"
    empty.write_text("")
    env["STARSHIP_CONFIG"] = str(empty if defaults else build_starship.THEME_FILE)
    if not defaults:
        script = f'. {shlex.quote(str(build_fzf.THEME_FILE))}; printf %s "$FZF_DEFAULT_OPTS"'
        opts = subprocess.run(["bash", "--norc", "-c", script], capture_output=True, text=True, check=True).stdout
        env["FZF_DEFAULT_OPTS"] = opts
        env["LAZYGIT_THEME"] = str(build_lazygit.THEME_FILE)
    return env


# --- pictures ------------------------------------------------------------------------

def cell(ch: str) -> str:
    """One character as HTML. Glyphs outside ASCII can come from fallback fonts with other
    widths, so each gets a box as wide as the cells it takes in the terminal (two for wide
    characters such as emoji), and the columns stay lined up. Icons are drawn in the icon
    font first: a text font can claim their code points without drawing them."""
    if ch.isascii():
        return html.escape(ch)
    width = "w" if unicodedata.east_asian_width(ch) in ("W", "F") else "c"
    icon = " i" if unicodedata.category(ch) == "Co" else ""   # private use: Nerd Font icons
    return f'<b class="{width}{icon}">{html.escape(ch)}</b>'


def to_html(rows: list[list[tuple[str, Style]]], term: build_ghostty.Terminal) -> str:
    """The capture drawn as Ghostty would (faint text blended in linear light, as on Linux)."""
    lines = []
    for cells in rows:
        spans, last, buf = [], None, ""
        for ch, style in cells:
            fg, bg = term.draw(**style.ink())[-1]
            css = f"color:{fg};background:{bg}" + (";font-weight:700" if style.bold else "") + \
                  (";font-style:italic" if style.italic else "") + (";text-decoration:underline" if style.underline else "")
            if css != last and buf:
                spans.append(f'<span style="{last}">{buf}</span>')
                buf = ""
            last, buf = css, buf + cell(ch)
        if buf:
            spans.append(f'<span style="{last}">{buf}</span>')
        lines.append("".join(spans))
    return (f'<!doctype html><meta charset="utf-8"><style>body{{margin:0;background:{term.background}}}'
            f'@font-face{{font-family:"Symbols Nerd Font Mono";'
            f'src:local("Symbols Nerd Font Mono"),url("{NERD_FONT}") format("truetype")}}'
            f'pre{{margin:0;padding:14px 16px;font:15px/19px {FONT};'
            f'color:{term.foreground};background:{term.background};white-space:pre}}'
            f'b{{display:inline-block;font-weight:inherit;text-align:center;overflow:visible}}'
            f'.c{{width:1ch}}.w{{width:2ch}}.i{{font-family:"Symbols Nerd Font Mono",{FONT}}}</style>'
            f'<pre>{chr(10).join(lines)}</pre>')


def screenshot(page: Path, png: Path, cols: int, rows: int) -> None:
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        return
    size = f"{round(cols * 9.05 + 34)},{rows * 19 + 30}"
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                    "--virtual-time-budget=5000", f"--window-size={size}", f"--screenshot={png}", page.as_uri()], capture_output=True, check=False)


# --- main ----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Measure what starship, fzf and lazygit draw, in a private tmux.")
    parser.add_argument("--defaults", action="store_true", help="use each tool's own colors instead of the themes")
    parser.add_argument("--shots", action="store_true", help="save each scene to build/terminal-audit/")
    parser.add_argument("--scene", action="append", help="run only scenes whose name starts with this")
    parser.add_argument("--verbose", action="store_true", help="list every failing run, not just the first few")
    args = parser.parse_args()
    if not shutil.which("tmux"):
        print("tmux isn't installed; the audit runs each tool in a private tmux server.")
        return 1
    data = rhino.load()
    terms = {n: build_ghostty.Terminal(rhino.load_variant(data, n)) for n in rhino.appearances(data)}
    label = "defaults" if args.defaults else "theme"
    if args.shots:
        OUT.mkdir(parents=True, exist_ok=True)
    failures, total = [], 0
    with tempfile.TemporaryDirectory(prefix="rhino-cli-audit-") as tmp:
        work = Path(tmp)
        env = theme_env(make_world(work), work, args.defaults)
        for scene in SCENES:
            if args.scene and not any(scene.name.startswith(s) for s in args.scene):
                continue
            if not shutil.which(scene.tool):
                print(f"-- {scene.name}: {scene.tool} isn't installed; skipped")
                continue
            tmux = Tmux(work, scene.cols, scene.rows)
            try:
                for variant in (terms if scene.per_variant else [None]):
                    for view, capture in scene.play(tmux, env, variant):
                        rows = parse(capture)
                        for name, term in terms.items():
                            if variant and variant != name:
                                continue
                            n, found = measure(view, rows, term, scene.ignore)
                            total += n
                            failures += found
                            if args.shots:
                                stem = f"{view}-{name}-{label}"
                                (OUT / f"{stem}.ans").write_text(capture, encoding="utf-8")
                                (OUT / f"{stem}.html").write_text(to_html(rows, term), encoding="utf-8")
                                screenshot(OUT / f"{stem}.html", OUT / f"{stem}.png", scene.cols, len(rows))
                            print(f"   {view} [{name}]: {n} runs, {len(found)} below their minimum")
            finally:
                tmux.stop()
    return summarize(failures, total, label, args.verbose)


def summarize(failures: list[Finding], total: int, label: str, verbose: bool) -> int:
    """Print the findings, worst first, grouped by view and style. Returns the exit code."""
    seen = {}
    for f in failures:
        key = (f.scene, f.variant, f.style)
        seen.setdefault(key, []).append(f)
    for (scene, variant, style), group in sorted(seen.items(), key=lambda kv: kv[1][0].ratio):
        f = group[0]
        texts = ", ".join(repr(g.text[:30]) for g in group[: (None if verbose else 3)])
        more = f" (+{len(group) - 3} more)" if len(group) > 3 and not verbose else ""
        print(f"- {scene} [{variant}] {f.ratio:.2f}:1 < {f.minimum:g}: {texts}{more}  {style_name(style)}")
    if failures:
        print(f"{len(failures)} of {total} runs are below their minimum ({label}).")
        return 1
    print(f"OK: all {total} runs of text read ({label}).")
    return 0


def style_name(s: Style) -> str:
    parts = [f"fg={s.fg}" if s.fg is not None else "", f"bg={s.bg}" if s.bg is not None else "",
             "bold" * s.bold, "faint" * s.faint, "reverse" * s.reverse]
    return " ".join(p for p in parts if p) or "default"


if __name__ == "__main__":
    sys.exit(main())
