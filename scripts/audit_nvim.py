"""Audit the Neovim theme against the plugins installed on this machine.

    python3 scripts/audit_nvim.py

`build_nvim.py --check` measures every group the theme sets. Plugins define hundreds
more, mostly as links to standard groups, and some compute or hard-code their own
colors. This starts the Neovim config on this machine (a lazy.nvim setup such as
LazyVim) headless with the theme loaded, loads the plugins below, dumps every
highlight group as it finally resolves, and measures each one where it's drawn.

State and cache go to a temporary folder, so sessions, history and lazy.nvim's update
checks are left alone. Exits 1 if any group falls below its minimum.

Where a group is drawn comes from its name; what it needs comes from its color: the
dim gray needs 3:1, the border color is decoration, icons and gutter signs need 3:1,
everything else 4.5:1. A group drawn in its own background color is deliberately
hidden and skipped.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import build_nvim
import rhino

# Your config may name another checkout of the theme, and lazy.nvim's module loader finds
# a plugin's modules in the folder the config names, whatever the runtime path says. So
# load this checkout's modules from their files, and every require then gets them.
LOAD_THEME = ("local d = [[" + str(rhino.ROOT / "lua" / "critical-rhino") + "]] "
              "for _, m in ipairs({'dark', 'light'}) do package.loaded['critical-rhino.' .. m] = dofile(d .. '/' .. m .. '.lua') end "
              "package.loaded['critical-rhino'] = dofile(d .. '/init.lua')")

# Plugins that draw their own interface. claudecode.nvim is left out on purpose:
# loading it starts its IDE server.
PLUGINS = os.environ.get("PLUGINS", "blink.cmp bufferline.nvim flash.nvim gitsigns.nvim grug-far.nvim "
                         "lualine.nvim mason.nvim mini.icons noice.nvim render-markdown.nvim "
                         "todo-comments.nvim trouble.nvim which-key.nvim nvim-treesitter")
SNACKS = ("picker dashboard notifier indent input scroll words terminal statuscolumn zen scope win explorer "
          "image toggle bigfile quickfile")
# Plugins that only define their highlights when first used.
SETUPS = ["require('blink.cmp.highlights').setup()", "require('which-key.colors').setup()",
          "require('noice.config.highlights').setup()", "require('lazy.view.colors').setup()",
          "require('mason.ui.colors')", "require('flash.highlight').setup()"]

DUMP_LUA = """
local function hex(n) return n and string.format("#%06X", n) or nil end
local out = {}
for name, def in pairs(vim.api.nvim_get_hl(0, {})) do
  local r = vim.api.nvim_get_hl(0, { name = name, link = false })
  out[name] = { link = def.link, fg = hex(r.fg), bg = hex(r.bg), reverse = r.reverse }
end
local f = assert(io.open(vim.g.hl_dump_path, "w"))
f:write(vim.json.encode(out))
f:close()
"""

DECOR = re.compile(r"(Border|Separator|Indent(?!Scope)|Whitespace|EndOfBuffer|VertSplit|Scrollbar|ScrollBar|"
                   r"Thumb|Shadow|Backdrop|^Cursor$|^lCursor|^CursorIM|^TermCursor|RedrawDebug|"
                   r"^NvimInternalError|Spinner|^SnacksImage|Dash$|^SignColumn|FoldColumn|^MiniCursorword|"
                   r"Cul$|Sbar|Gutter|^Noice.*Icon|IndentFoldOpen)")
RULES = [  # (name pattern, where it's drawn, minimum)
    (r"^LineNr(Above|Below)?$", "gutter", rhino.TEXT_MIN),
    (r"^lualine", "own", rhino.TEXT_MIN),
    ((r"Sign|^GitSigns(Staged)?(Add|Change|Delete|Changedelete|Topdelete|Untracked)(Nr)?$|"
      r"^GrugFarResults\w+Indicator$"), "sign", rhino.DIM_MIN),
    (r"Icon|^MiniIcons|Kind", "float+code", rhino.DIM_MIN),
    ((r"^(Pmenu|Blink|WhichKey|Noice|Lazy|Mason|Trouble|Float|NormalFloat|SnacksPicker|SnacksNotifier|"
      r"SnacksInput|SnacksWin|SnacksFooter|SnacksTitle|SnacksNormal|Wild|DiagnosticFloating|health|"
      r"MiniCompletion|Snacks(Zen|Scratch))"), "float", rhino.TEXT_MIN),
    (r".", "code", rhino.TEXT_MIN),   # syntax, tree-sitter, diagnostics, LSP, markdown, dashboard...
]


def dump(variant: str, tmp: Path) -> dict:
    (tmp / "state/nvim/lazy").mkdir(parents=True, exist_ok=True)
    # Tell lazy.nvim's update checker it just ran, so it doesn't fetch every plugin.
    (tmp / "state/nvim/lazy/state.json").write_text(json.dumps({"checker": {"last_check": int(time.time())}}))
    (tmp / "dump.lua").write_text(DUMP_LUA)
    out = tmp / f"{variant}.json"
    setups = " ".join(f"pcall(function() {s} end)" for s in SETUPS)
    env = dict(os.environ, XDG_STATE_HOME=str(tmp / "state"), XDG_CACHE_HOME=str(tmp / "cache"))
    cmd = ["nvim", "--headless", "-i", "NONE",
           f"+set rtp^={rhino.ROOT}",   # after startup: lazy.nvim resets the runtime path
           f"+lua {LOAD_THEME}",
           f"+colorscheme critical-rhino-{variant}",
           f"+Lazy! load {PLUGINS}",
           f"+lua for m in ('{SNACKS}'):gmatch('%S+') do pcall(function() return Snacks[m] end) end",
           "+lua pcall(require, 'snacks.picker.config.highlights')",
           f"+lua {setups}",
           f"+lua vim.g.hl_dump_path = [[{out}]]", f"+luafile {tmp / 'dump.lua'}", "+qa!"]
    subprocess.run(cmd, cwd=tmp, env=env, check=True, timeout=120, capture_output=True)
    return {k: (v if isinstance(v, dict) else {}) for k, v in json.loads(out.read_text()).items()}


def audit(variant: str, groups: dict) -> list[str]:
    v = build_nvim.load_variant(rhino.load(), variant)
    ctx = build_nvim.contexts(v)
    dim, border = v.u("dim_foreground"), v.u("border")
    fails, measured = [], 0
    for name, g in sorted(groups.items()):
        fg, bg = g.get("fg"), g.get("bg")
        # A reversed group swaps its colors, which leaves the contrast the same, so it's measured as is.
        if not fg or DECOR.search(name) or fg in (border, bg):
            continue
        where, minimum = next((w, m) for pat, w, m in RULES if re.search(pat, name))
        if fg == dim:
            minimum = min(minimum, rhino.DIM_MIN)
        if bg:
            where, under = "own", [bg]
        elif where == "own":   # a lualine component without a background: sections c and x
            under = [v.u("surface")]
        else:
            under = [s for part in where.split("+") for s in ctx[part]]
        worst = min(under, key=lambda s: rhino.contrast(fg, s))
        ratio = rhino.contrast(fg, worst)
        measured += 1
        if ratio < minimum - 1e-9:
            fails.append(f"{variant}: {name} {fg} is {ratio:.2f}:1 on {worst} ({where}), below {minimum}")
    print(f"{variant}: {len(groups)} groups, {measured} measured, {len(fails)} below their minimum")
    return fails


def main() -> int:
    fails = []
    with tempfile.TemporaryDirectory(prefix="rhino-audit-") as tmp:
        for variant in rhino.appearances(rhino.load()):
            fails += audit(variant, dump(variant, Path(tmp)))
    for f in fails:
        print(f"- {f}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
