"""Retake the README's screenshots, docs/images/<picture>-{dark,light}.png, from this checkout's themes.

    python3 scripts/screenshots.py                  # all of them
    python3 scripts/screenshots.py neovim lazygit   # some: neovim, vscode, terminal, lazygit, claude-code

    neovim    The Neovim config on this machine (a lazy.nvim setup such as LazyVim, as
              make nvim:audit uses) with this checkout's theme, editing the sample app.py
              the VS Code pictures show, drawn as Ghostty would draw it.
    vscode    The explorer scene from make vscode:audit, in VS Code.
    terminal  The shell scene from make terminal:audit: starship prompts over ls, git,
              grep and every text color.
    lazygit   The commits view from make terminal:audit.
    claude-code  The conversation from make claude-code:audit, in accept-edits mode.

The audits measure what they capture, and the pictures are copied only if the audit
passes. Neovim's are measured by make nvim:check and nvim:audit instead: a picture
of the editor includes decoration, such as indent guides, held to no minimum.

Needs tmux and Chrome or Chromium; vscode needs VS Code, neovim needs Neovim with a
lazy.nvim config, and claude-code needs Claude Code.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import audit_claude_code
import audit_nvim
import audit_terminal
import audit_vscode
import build_ghostty
import rhino

OUT = rhino.ROOT / "docs" / "images"
HERE = Path(__file__).resolve().parent
PICTURES = ("neovim", "vscode", "terminal", "lazygit", "claude-code")
NVIM_SIZE = (130, 36)   # the sample file, the status line and the command line
# The README's bufferline snippet, run again: the config set bufferline up at startup,
# possibly with another checkout's theme.
BUFFERLINE = (":lua pcall(function() local p = require('lazy.core.config').plugins['bufferline.nvim']; "
              "local o = require('lazy.core.plugin').values(p, 'opts', false); "
              "o.highlights = require('critical-rhino').bufferline; require('bufferline').setup(o) end)")
NVIM_STEPS = [
    BUFFERLINE,
    ":echo ''",
    "22G",            # the TODO comment, on the current line
    "/distance_km",   # search: every match highlighted, one of them inside the selection below
    "27G", "V", "j",  # a selection over two lines of summary(), and a match in it
]


def run(script: str, *args: str) -> None:
    if subprocess.run([sys.executable, str(HERE / script), *args], check=False).returncode != 0:
        raise SystemExit(f"{script} failed, so its pictures weren't copied")


def copy(src: Path, name: str) -> None:
    if not src.exists():
        raise SystemExit(f"{src.relative_to(rhino.ROOT)} wasn't made")
    shutil.copyfile(src, OUT / name)
    print(f"wrote {(OUT / name).relative_to(rhino.ROOT)}")


def neovim(terms: dict[str, build_ghostty.Terminal]) -> None:
    if not shutil.which("nvim"):
        raise SystemExit("nvim isn't installed")
    with tempfile.TemporaryDirectory(prefix="rhino-screenshots-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        ws = audit_vscode.make_workspace(work)
        state = work / "state"
        (state / "nvim" / "lazy").mkdir(parents=True)
        # Tell lazy.nvim's update checker it just ran, so it doesn't fetch every plugin.
        (state / "nvim" / "lazy" / "state.json").write_text(json.dumps({"checker": {"last_check": int(time.time())}}))
        env = {"XDG_STATE_HOME": str(state), "XDG_CACHE_HOME": str(work / "cache"), "COLORTERM": "truecolor"}
        tmux = audit_terminal.Tmux(work, *NVIM_SIZE)
        try:
            for name, term in terms.items():
                args = [f"+set rtp^={rhino.ROOT}", f"+lua {audit_nvim.LOAD_THEME}", f"+colorscheme critical-rhino-{name}"]
                tmux.start(f"cd {shlex.quote(str(ws))} && nvim -i NONE {' '.join(map(shlex.quote, args))} app.py", env)
                time.sleep(6.0)
                for step in NVIM_STEPS:
                    tmux.keys("-l", step, wait=0.2)
                    if step.startswith((":", "/")):
                        tmux.keys("Enter", wait=0.6)
                time.sleep(1.0)
                rows = audit_terminal.parse(tmux.capture())
                page = work / f"neovim-{name}.html"
                page.write_text(audit_terminal.to_html(rows, term), encoding="utf-8")
                audit_terminal.screenshot(page, OUT / f"neovim-{name}.png", NVIM_SIZE[0], len(rows))
                print(f"wrote {(OUT / f'neovim-{name}.png').relative_to(rhino.ROOT)}")
                tmux.keys("Escape", wait=0.2)
                tmux.keys("-l", ":qa!", wait=0.1)
                tmux.keys("Enter", wait=1.0)   # quit, so Neovim isn't still writing its cache when it's removed
        finally:
            tmux.stop()


def main() -> int:
    wanted = sys.argv[1:] or list(PICTURES)
    unknown = [w for w in wanted if w not in PICTURES]
    if unknown:
        print(f"unknown picture {', '.join(unknown)}; choose from {', '.join(PICTURES)}")
        return 2
    if not (shutil.which("google-chrome") or shutil.which("chromium")) or not shutil.which("tmux"):
        print("needs tmux and Chrome or Chromium")
        return 1
    data = rhino.load()
    variants = rhino.appearances(data)
    terms = {n: build_ghostty.Terminal(rhino.load_variant(data, n)) for n in variants}
    OUT.mkdir(parents=True, exist_ok=True)
    shots = audit_terminal.OUT
    scenes = [s for w, s in (("terminal", "shell"), ("lazygit", "lazygit")) if w in wanted]
    if scenes:
        run("audit_terminal.py", "--shots", *(x for s in scenes for x in ("--scene", s)))
        for v in variants:
            if "terminal" in wanted:
                copy(shots / f"shell-{v}-theme.png", f"terminal-{v}.png")
            if "lazygit" in wanted:
                copy(shots / f"lazygit-commits-{v}-theme.png", f"lazygit-{v}.png")
    if "vscode" in wanted:
        run("audit_vscode.py", *(f"code:{v}" for v in variants))
        for v in variants:
            copy(audit_vscode.OUT / f"code-{v}-explorer.png", f"vscode-{v}.png")
    if "claude-code" in wanted:
        run("audit_claude_code.py", "--shots")
        for v in variants:
            copy(audit_claude_code.OUT / f"{audit_claude_code.PICTURE}-{v}.png", f"claude-code-{v}.png")
    if "neovim" in wanted:
        neovim(terms)
    return 0


if __name__ == "__main__":
    sys.exit(main())
