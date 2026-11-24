"""Build the Neovim colorscheme from palette.toml.

    python3 scripts/build_nvim.py           # write the generated Lua files
    python3 scripts/build_nvim.py --check   # fail (exit 1) if they're stale or a pairing is unreadable

Writes lua/critical-rhino/{dark,light}.lua (highlight groups, terminal colors and a
lualine theme for each variant). colors/*.lua, lua/critical-rhino/init.lua and the
lualine theme files are small hand-written loaders.

Every group that sets a text color also says where that text is drawn, so the check
can measure it against those backgrounds:

    code     on every surface code sits on (rhino.code_surfaces)
    editor   on the background and the current line
    gutter   on the background only (line numbers; the current line's number is separate)
    float    on popup menus and floating windows, and their selected row
    sign     a mark in the gutter, beside code: 3:1 on the background and current line
    own      a group with its own background is measured on that background only
    None     decoration (borders, indent guides): not text, not checked
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import rhino

LUA_DIR = rhino.ROOT / "lua" / "critical-rhino"
STYLE_KEYS = ("bold", "italic", "underline", "undercurl", "strikethrough", "reverse")
DIM = rhino.DIM_MIN


# One appearance of the palette, resolved (shared with the other builds).
Variant = rhino.Variant
load_variant = rhino.load_variant


def hl(fg=None, bg=None, sp=None, *styles, on="code", min=rhino.TEXT_MIN, link=None, blend=None) -> dict:
    """One highlight group. `on` and `min` are for the check and aren't written out."""
    if link:
        return {"link": link}
    spec = {k: v for k, v in (("fg", fg), ("bg", bg), ("sp", sp), ("blend", blend)) if v is not None}
    for style in styles:
        if style not in STYLE_KEYS:
            raise ValueError(f"unknown style {style!r}")
        spec[style] = True
    spec["_on"] = "own" if bg and fg else on
    spec["_min"] = min
    return spec


def link(target: str) -> dict:
    return {"link": target}


def groups(v: Variant) -> dict[str, dict]:
    """Every highlight group the theme sets, for one variant."""
    U, S, T = v.u, v.s, v.t
    bg, fg = v.bg, U("foreground")
    surface, raised = U("surface"), U("surface_raised")
    sel_soft = U("selection_soft")
    border = U("border")
    muted, dim = U("muted_foreground"), U("dim_foreground")

    def syn(role: str, *extra: str, on: str = "code") -> dict:
        return hl(S(role), None, None, *v.styles.get(role, ()), *extra, on=on)

    def muted_on(over: str) -> str:
        return U("muted_foreground", over)

    def match(text: str, fill: str, *styles: str) -> dict:
        """A search match, text on a fill. Neovim draws a selection over a match by keeping
        the match's text color and taking the selection's background, so black text would
        vanish on dark's navy selection. Where it would, the match is drawn in reverse
        video instead: the fill as the color, so a selection keeps the fill and draws the
        text in its own color. Measured on every code surface, the selection included,
        which in reverse is the text on the fill."""
        selection = U("editor_selection")
        if rhino.contrast(text, selection) >= rhino.TEXT_MIN:
            return hl(text, fill, None, *styles) | {"_also": [selection]}
        return hl(fill, None, None, *styles, "reverse", on="code")

    g: dict[str, dict] = {}

    # --- editor ------------------------------------------------------------------
    g |= {
        "Normal": hl(fg, bg),
        "NormalNC": link("Normal"),
        "NormalFloat": hl(fg, surface),
        "FloatBorder": hl(border, surface, on=None) | {"_on": None},
        "FloatTitle": hl(S("keyword"), surface, None, "bold"),
        "FloatFooter": hl(muted_on(surface), surface),
        "Cursor": hl(v.on(T("cursor")), T("cursor")),
        "lCursor": link("Cursor"),
        "CursorIM": link("Cursor"),
        "TermCursor": link("Cursor"),
        "CursorLine": hl(bg=U("current_line")),
        "CursorColumn": hl(bg=U("current_line")),
        "ColorColumn": hl(bg=U("current_line")),
        "CursorLineNr": hl(U("line_number_active", U("current_line")), on="code"),
        "LineNr": hl(U("line_numbers"), on="gutter"),   # the current line's number is CursorLineNr
        "LineNrAbove": link("LineNr"),
        "LineNrBelow": link("LineNr"),
        "SignColumn": hl(on=None),
        "FoldColumn": hl(dim, on="code", min=DIM),
        "Folded": hl(muted_on(surface), surface),
        "Conceal": hl(muted, on="code"),
        "NonText": hl(dim, on="code", min=DIM),
        "SpecialKey": hl(dim, on="code", min=DIM),
        "EndOfBuffer": hl(border, on=None),
        "Whitespace": hl(border, on=None),
        "WinSeparator": hl(border, on=None),
        "VertSplit": link("WinSeparator"),
        "Directory": hl(S("type"), on="code"),
        "Title": hl(S("keyword"), None, None, "bold", on="code"),
        "MatchParen": hl(U("bracket_match"), None, None, "bold", "underline", on="code"),
        "Visual": hl(bg=U("editor_selection")),
        "VisualNOS": link("Visual"),
        "QuickFixLine": hl(bg=sel_soft),
        "Search": match(U("search_foreground"), U("search")),
        "CurSearch": match(U("search_current_foreground"), U("search_current"), "bold"),
        "IncSearch": link("CurSearch"),
        "Substitute": link("CurSearch"),
        "WildMenu": link("PmenuSel"),
        # messages and the command line
        "ModeMsg": hl(fg, None, None, "bold", on="code"),
        "MsgArea": hl(fg, on="code"),
        "MsgSeparator": hl(border, surface, on=None) | {"_on": None},
        "MoreMsg": hl(S("comment"), on="code"),
        "Question": hl(S("comment"), on="code"),
        "OkMsg": hl(S("comment"), on="code"),
        "ErrorMsg": hl(U("error"), on="code"),
        "WarningMsg": hl(U("warning"), on="code"),
        # popup menu (completion)
        "Pmenu": hl(fg, surface),
        "PmenuSel": hl(fg, sel_soft),
        "PmenuKind": hl(S("type"), surface),
        "PmenuKindSel": hl(S("type"), sel_soft),
        "PmenuExtra": hl(muted_on(surface), surface),
        "PmenuExtraSel": hl(muted_on(sel_soft), sel_soft),
        "PmenuMatch": hl(U("match_highlight"), surface, None, "bold"),
        "PmenuMatchSel": hl(U("match_highlight"), sel_soft, None, "bold"),
        "PmenuSbar": hl(bg=raised),
        "PmenuThumb": hl(bg=border),
        "PmenuBorder": link("FloatBorder"),
        # status line, tab line, window bar (lualine and bufferline draw their own)
        "StatusLine": hl(fg, surface),
        "StatusLineNC": hl(muted_on(surface), surface),
        "StatusLineTerm": link("StatusLine"),
        "StatusLineTermNC": link("StatusLineNC"),
        "TabLine": hl(muted_on(surface), surface),
        "TabLineSel": hl(fg, bg, None, "bold"),
        "TabLineFill": hl(bg=surface),
        "WinBar": hl(fg, bg, None, "bold"),
        "WinBarNC": hl(muted, bg),
        # spelling: an undercurl in the color of the matching diagnostic
        "SpellBad": hl(None, None, U("error"), "undercurl", on=None),
        "SpellCap": hl(None, None, U("warning"), "undercurl", on=None),
        "SpellLocal": hl(None, None, U("info"), "undercurl", on=None),
        "SpellRare": hl(None, None, U("hint"), "undercurl", on=None),
        # diffs
        "DiffAdd": hl(bg=U("diff_inserted_bg")),
        "DiffDelete": hl(S("diff_deleted"), U("diff_removed_bg")),
        "DiffChange": hl(bg=U("diff_changed_bg")),
        "DiffText": hl(bg=U("diff_text_bg")),
        "Added": hl(U("git_added"), on="code"),
        "Changed": hl(U("git_modified"), on="code"),
        "Removed": hl(U("git_deleted"), on="code"),
    }

    # --- diagnostics and LSP -------------------------------------------------------
    for kind, role in (("Error", "error"), ("Warn", "warning"), ("Info", "info"), ("Hint", "hint"),
                       ("Ok", "git_added")):
        color = U(role)
        g |= {
            f"Diagnostic{kind}": hl(color, on="code"),
            f"DiagnosticVirtualText{kind}": hl(color, on="code"),
            f"DiagnosticVirtualLines{kind}": hl(color, on="code"),
            f"DiagnosticUnderline{kind}": hl(None, None, color, "undercurl", on=None),
            f"DiagnosticFloating{kind}": hl(color, on="float"),
            f"DiagnosticSign{kind}": hl(color, on="sign", min=DIM),
        }
    g |= {
        "DiagnosticDeprecated": hl(None, None, U("error"), "strikethrough", on=None),
        "DiagnosticUnnecessary": hl(dim, on="code", min=DIM),
        "LspReferenceText": hl(bg=U("word_highlight")),
        "LspReferenceRead": hl(bg=U("word_highlight")),
        "LspReferenceWrite": hl(None, U("word_highlight"), None, "underline"),
        "LspReferenceTarget": link("LspReferenceText"),
        "LspInlayHint": hl(muted, on="code"),
        "LspCodeLens": hl(muted, on="code"),
        "LspCodeLensSeparator": hl(dim, on="code", min=DIM),
        "LspSignatureActiveParameter": hl(None, None, None, "bold", "underline", on=None),
        "SnippetTabstop": link("Visual"),
    }

    # --- syntax: the classic groups -------------------------------------------------
    g |= {
        "Comment": syn("comment"),
        "Constant": hl(S("number"), on="code"),   # plugins use this for keys and counts; named constants are @constant
        "String": syn("string"),
        "Character": syn("string"),
        "Number": syn("number"),
        "Float": syn("number"),
        "Boolean": syn("keyword"),
        "Identifier": syn("text"),
        "Function": syn("text"),
        "Statement": syn("keyword"),
        "Conditional": syn("keyword"),
        "Repeat": syn("keyword"),
        "Label": syn("decorator"),
        "Operator": syn("text"),
        "Keyword": syn("keyword"),
        "Exception": syn("keyword"),
        "PreProc": syn("keyword"),
        "Include": syn("keyword"),
        "Define": syn("keyword"),
        "Macro": syn("decorator"),
        "PreCondit": syn("keyword"),
        "Type": syn("type"),
        "StorageClass": syn("keyword"),
        "Structure": syn("type"),
        "Typedef": syn("type"),
        "Special": syn("string_escape"),
        "SpecialChar": syn("string_escape"),
        "Tag": syn("type"),
        "Delimiter": syn("text"),
        "SpecialComment": syn("doc_tag"),
        "Debug": syn("keyword"),
        "Underlined": hl(None, None, None, "underline", on=None),
        "Error": hl(U("error"), on="code"),
        "Todo": syn("todo"),
        "Bold": hl(None, None, None, "bold", on=None),
        "Italic": hl(None, None, None, "italic", on=None),
    }

    # --- syntax: tree-sitter captures --------------------------------------------------
    captures = {
        "text": ["@variable", "@variable.parameter", "@variable.member", "@property", "@function",
                 "@function.call", "@function.method", "@function.method.call", "@operator",
                 "@punctuation.delimiter", "@punctuation.bracket", "@tag", "@tag.builtin", "@tag.attribute",
                 "@tag.delimiter", "@constructor.lua", "@string.special.path"],
        "builtin": ["@variable.builtin", "@variable.parameter.builtin", "@function.builtin", "@type.builtin",
                    "@module.builtin"],
        "type": ["@type", "@type.definition", "@module", "@constructor"],
        "keyword": ["@keyword", "@keyword.coroutine", "@keyword.function", "@keyword.operator",
                    "@keyword.import", "@keyword.type", "@keyword.modifier", "@keyword.repeat",
                    "@keyword.return", "@keyword.debug", "@keyword.exception", "@keyword.conditional",
                    "@keyword.conditional.ternary", "@keyword.directive", "@keyword.directive.define",
                    "@boolean", "@constant.builtin", "@markup.list"],
        "constant": ["@constant", "@constant.macro", "@string.special.symbol"],
        "string": ["@string", "@character"],
        "string_escape": ["@string.escape", "@string.special", "@character.special", "@markup.math"],
        "regex": ["@string.regexp"],
        "number": ["@number", "@number.float"],
        "decorator": ["@attribute", "@attribute.builtin", "@label", "@function.macro"],
        "interpolation": ["@punctuation.special"],
        "comment": ["@comment"],
        "doc_comment": ["@comment.documentation", "@string.documentation"],
        "todo": ["@comment.todo", "@comment.error", "@comment.warning", "@comment.note"],
        "heading": ["@markup.heading"] + [f"@markup.heading.{n}" for n in range(1, 7)],
        "bold": ["@markup.strong"],
        "italic": ["@markup.italic"],
        "link_text": ["@markup.link", "@markup.link.label"],
        "link_url": ["@markup.link.url", "@string.special.url"],
        "quote": ["@markup.quote"],
        "inline_code": ["@markup.raw", "@markup.raw.block"],
        "diff_header": ["@diff.delta"],
        "diff_inserted": ["@diff.plus", "@markup.list.checked"],
        "diff_deleted": ["@diff.minus"],
        "invalid": ["@lsp.type.unresolvedReference"],
    }
    for role, names in captures.items():
        for name in names:
            g[name] = syn(role)
    g |= {
        "@markup.strikethrough": hl(None, None, None, "strikethrough", on=None),
        "@markup.underline": hl(None, None, None, "underline", on=None),
        "@markup.list.unchecked": syn("text"),
        "@none": hl(on=None),
        # Semantic tokens. Neovim links most @lsp.type.* groups to the captures above;
        # these are the ones whose meaning differs.
        "@lsp.type.comment": hl(on=None),   # empty, so tree-sitter's TODO highlighting shows through
        "@lsp.type.selfKeyword": syn("builtin"),
        "@lsp.type.selfParameter": syn("builtin"),
        "@lsp.type.builtinType": syn("builtin"),
        "@lsp.typemod.function.defaultLibrary": syn("builtin"),
        "@lsp.typemod.variable.defaultLibrary": syn("builtin"),
        "@lsp.type.escapeSequence": syn("string_escape"),
        "@lsp.type.formatSpecifier": syn("interpolation"),
        "@lsp.type.keyword": syn("keyword"),
    }

    # --- plugins ---------------------------------------------------------------------
    for color, ref in (("Azure", U("link")), ("Blue", S("type")), ("Cyan", U("hint")), ("Green", S("comment")),
                       ("Grey", muted), ("Orange", S("keyword")), ("Purple", S("number")), ("Red", S("string")),
                       ("Yellow", S("decorator"))):
        g[f"MiniIcons{color}"] = hl(ref, on="float" if color != "Grey" else "code")
        g[f"WhichKeyIcon{color}"] = link(f"MiniIcons{color}")
    g["MiniIconsGrey"] = hl(muted, on="code")

    g |= {
        # snacks: picker, dashboard, indent guides, notifier
        "SnacksPickerListCursorLine": hl(bg=sel_soft),
        "SnacksPickerPreviewCursorLine": hl(bg=sel_soft),
        "SnacksPickerMatch": hl(U("match_highlight"), None, None, "bold", on="float"),
        "SnacksPickerDir": hl(muted_on(surface), on="float"),
        "SnacksPickerTotals": hl(muted_on(surface), on="float"),
        "SnacksPickerKeymapRhs": hl(muted_on(surface), on="float"),
        "SnacksPickerDesc": hl(muted_on(surface), on="float"),
        "SnacksPickerPathHidden": hl(dim, on="float", min=DIM),
        "SnacksPickerPathIgnored": hl(dim, on="float", min=DIM),
        "SnacksPickerGitStatusIgnored": hl(dim, on="float", min=DIM),
        "SnacksPickerGitStatusUntracked": hl(muted_on(surface), on="float"),
        "SnacksPickerUnselected": hl(dim, on="float", min=DIM),
        "SnacksIndent": hl(border, on=None),
        "SnacksIndentBlank": hl(border, on=None),
        "SnacksDashboardDesc": hl(fg, on="code"),
        "SnacksDashboardDir": hl(muted, on="code"),
        "SnacksDashboardFooter": hl(muted, on="code"),
        # blink.cmp: colored kinds, dim ghost text
        "BlinkCmpMenu": link("Pmenu"),
        "BlinkCmpMenuBorder": link("FloatBorder"),
        "BlinkCmpMenuSelection": link("PmenuSel"),
        "BlinkCmpLabelMatch": hl(U("match_highlight"), None, None, "bold", on="float"),
        "BlinkCmpLabelDeprecated": hl(muted_on(surface), None, None, "strikethrough", on="float"),
        "BlinkCmpLabelDetail": hl(muted_on(surface), on="float"),
        "BlinkCmpLabelDescription": hl(muted_on(surface), on="float"),
        "BlinkCmpSource": hl(muted_on(surface), on="float"),
        "BlinkCmpGhostText": hl(dim, on="code", min=DIM),
        "BlinkCmpDoc": link("NormalFloat"),
        "BlinkCmpDocBorder": link("FloatBorder"),
        "BlinkCmpDocSeparator": hl(border, surface, on=None) | {"_on": None},
        "BlinkCmpSignatureHelpActiveParameter": link("LspSignatureActiveParameter"),
        "BlinkCmpScrollBarThumb": hl(bg=border),
        "BlinkCmpScrollBarGutter": hl(bg=surface),
        # which-key
        "WhichKey": hl(S("decorator"), on="float"),
        "WhichKeyGroup": hl(S("type"), on="float"),
        "WhichKeyDesc": hl(fg, on="float"),
        "WhichKeySeparator": hl(dim, on="float", min=DIM),
        "WhichKeyValue": hl(muted_on(surface), on="float"),
        # flash: matches like search, labels on the purple fill
        "FlashBackdrop": hl(dim, on="code", min=DIM),
        "FlashMatch": link("Search"),
        "FlashCurrent": link("IncSearch"),
        "FlashLabel": hl(U("status_no_folder_foreground"), U("status_no_folder"), None, "bold"),
        # gitsigns
        "GitSignsAdd": hl(U("gutter_added"), on="sign", min=DIM),
        "GitSignsChange": hl(U("gutter_modified"), on="sign", min=DIM),
        "GitSignsDelete": hl(U("gutter_deleted"), on="sign", min=DIM),
        "GitSignsChangedelete": link("GitSignsChange"),
        "GitSignsTopdelete": link("GitSignsDelete"),
        "GitSignsUntracked": link("GitSignsAdd"),
        "GitSignsCurrentLineBlame": hl(muted, on="code"),
        # staged hunks: gitsigns would dim the colors to about 1.6:1
        "GitSignsStagedAdd": hl(U("gutter_staged_added"), on="sign", min=DIM),
        "GitSignsStagedChange": hl(U("gutter_staged_modified"), on="sign", min=DIM),
        "GitSignsStagedDelete": hl(U("gutter_staged_deleted"), on="sign", min=DIM),
        "GitSignsStagedChangedelete": link("GitSignsStagedChange"),
        "GitSignsStagedTopdelete": link("GitSignsStagedDelete"),
        "GitSignsStagedUntracked": link("GitSignsStagedAdd"),
        "GitSignsStagedAddNr": link("GitSignsStagedAdd"),
        "GitSignsStagedChangeNr": link("GitSignsStagedChange"),
        "GitSignsStagedDeleteNr": link("GitSignsStagedDelete"),
        "GitSignsStagedChangedeleteNr": link("GitSignsStagedChange"),
        "GitSignsStagedTopdeleteNr": link("GitSignsStagedDelete"),
        "GitSignsStagedUntrackedNr": link("GitSignsStagedAdd"),
        # plugins that borrow the line-number color for text in floats or results
        "SnacksPickerCol": hl(muted_on(surface), on="float"),
        "SnacksPickerTree": hl(border, on=None),
        "LazyProgressTodo": hl(border, on=None),
        "GrugFarResultsLineNr": hl(muted, on="code"),
        "GrugFarResultsColumnNr": hl(muted, on="code"),
        # grug-far result markers (hard-coded colors by default)
        "GrugFarResultsAddIndicator": link("GitSignsAdd"),
        "GrugFarResultsChangeIndicator": link("GitSignsChange"),
        "GrugFarResultsRemoveIndicator": link("GitSignsDelete"),
        # mason (hard-coded colors by default)
        "MasonHeader": hl(U("accent_foreground"), U("accent"), None, "bold"),
        "MasonHeaderSecondary": hl(U("status_debugging_foreground"), U("status_debugging"), None, "bold"),
        "MasonHighlight": hl(U("link"), on="float"),
        "MasonHighlightBlock": hl(U("selection_foreground"), U("selection")),
        "MasonHighlightBlockBold": hl(U("selection_foreground"), U("selection"), None, "bold"),
        "MasonHighlightSecondary": hl(S("keyword"), on="float"),
        "MasonHighlightBlockSecondary": hl(U("status_debugging_foreground"), U("status_debugging")),
        "MasonHighlightBlockBoldSecondary": hl(U("status_debugging_foreground"), U("status_debugging"), None, "bold"),
        "MasonMuted": hl(muted_on(surface), on="float"),
        "MasonMutedBlock": hl(fg, raised),
        "MasonMutedBlockBold": hl(fg, raised, None, "bold"),
        # trouble: positions and tree guides
        "TroublePos": hl(muted_on(surface), on="float"),
        "TroubleIndent": hl(border, on=None),
        "TroubleIndentFoldOpen": hl(dim, on="float", min=DIM),
        # noice and lazy.nvim
        "NoiceLspProgressTitle": hl(muted_on(surface), on="float"),
        "LazyComment": hl(muted_on(surface), on="float"),
    }
    for kind, role in (("Class", "type"), ("Interface", "type"), ("Struct", "type"), ("Enum", "type"),
                       ("TypeParameter", "type"), ("Module", "type"), ("Keyword", "keyword"),
                       ("Constant", "number"), ("EnumMember", "number"), ("Value", "number"), ("Unit", "number"),
                       ("Snippet", "comment"), ("File", "type"), ("Folder", "type"), ("Operator", "text")):
        g[f"BlinkCmpKind{kind}"] = hl(S(role), on="float")
    g["BlinkCmpKindText"] = hl(muted_on(surface), on="float")
    g |= bufferline(v)
    return g


def bufferline(v: Variant) -> dict[str, dict]:
    """The buffer tabs. bufferline would otherwise invent its own darker backgrounds and
    draw inactive names in the Comment color, which here is green. It keeps a scheme's
    definitions because LazyVim leaves its `themable` option on.

    Inactive and visible tabs sit on the side-bar surface; the current one on the editor
    background, like VS Code's tabs. Names with diagnostics take the diagnostic's color."""
    U = v.u
    surface, bg, fg, border = U("surface"), v.bg, U("foreground"), U("border")
    muted = U("muted_foreground", surface)
    states = {"": (surface, muted, ()), "Visible": (surface, fg, ()), "Selected": (bg, fg, ("bold",))}
    g = {"BufferLineFill": hl(bg=surface),
         "BufferLineBackground": hl(muted, surface),
         "BufferLineTab": hl(muted, surface),
         "BufferLineTabClose": hl(muted, surface),
         "BufferLineTabSelected": hl(fg, bg, None, "bold"),
         "BufferLineTabSeparator": hl(border, surface, on=None) | {"_on": None},
         "BufferLineTabSeparatorSelected": hl(border, bg, on=None) | {"_on": None},
         "BufferLineIndicatorSelected": hl(U("link"), bg),
         "BufferLineIndicatorVisible": hl(surface, surface, on=None) | {"_on": None},
         "BufferLineOffsetSeparator": hl(border, surface, on=None) | {"_on": None},
         "BufferLineTruncMarker": hl(muted, surface),
         "BufferLineGroupLabel": hl(fg, U("surface_raised")),
         "BufferLineGroupSeparator": hl(border, surface, on=None) | {"_on": None}}
    for st, (sbg, sfg, sty) in states.items():
        g |= {f"BufferLineBuffer{st}": hl(sfg, sbg, None, *sty),
              f"BufferLineNumbers{st}": hl(sfg, sbg, None, *sty),
              f"BufferLineCloseButton{st}": hl(sfg, sbg),
              f"BufferLineDiagnostic{st}": hl(sfg, sbg),
              f"BufferLineDuplicate{st}": hl(U("muted_foreground", sbg), sbg),
              f"BufferLineModified{st}": hl(U("git_conflict"), sbg),
              f"BufferLinePick{st}": hl(U("error"), sbg, None, "bold"),
              f"BufferLineSeparator{st}": hl(border, sbg, on=None) | {"_on": None}}
        for kind, role in (("Error", "error"), ("Warning", "warning"), ("Info", "info"), ("Hint", "hint")):
            g |= {f"BufferLine{kind}{st}": hl(U(role), sbg, None, *sty),
                  f"BufferLine{kind}Diagnostic{st}": hl(U(role), sbg)}
    return g


def lualine(v: Variant) -> dict:
    """lualine theme: section a is the mode on a fill; b and c are the raised and side-bar surfaces."""
    U = v.u
    b = {"fg": U("foreground"), "bg": U("surface_raised")}
    c = {"fg": U("foreground"), "bg": U("surface")}
    modes = {"normal": ("accent", "accent_foreground"), "insert": ("status_remote", "status_remote_foreground"),
             "visual": ("status_no_folder", "status_no_folder_foreground"),
             "replace": ("filter_widget", "filter_widget_foreground"),
             "command": ("status_debugging", "status_debugging_foreground")}
    out = {m: {"a": {"fg": U(text), "bg": U(fill), "gui": "bold"}, "b": b, "c": c} for m, (fill, text) in modes.items()}
    term = U("hint")
    out["terminal"] = {"a": {"fg": v.on(term), "bg": term, "gui": "bold"}, "b": b, "c": c}
    nc = {"fg": U("muted_foreground", U("surface")), "bg": U("surface")}
    out["inactive"] = {"a": nc, "b": nc, "c": nc}
    return out


ANSI = rhino.ANSI
terminal_colors = rhino.terminal_colors


# --- checking ------------------------------------------------------------------------

def contexts(v: Variant) -> dict[str, list[str]]:
    return {
        "code": list(v.surfaces.values()),
        "editor": [v.bg, v.u("current_line")],
        "gutter": [v.bg],
        "float": [v.u("surface"), v.u("selection_soft")],
        "sign": [v.bg, v.u("current_line")],
    }


def check_groups(v: Variant, gs: dict[str, dict]) -> list[str]:
    errors = []
    ctx = contexts(v)
    for name, spec in gs.items():
        if "link" in spec:
            if spec["link"] not in gs and not spec["link"][0].isupper():
                errors.append(f"{name} links to {spec['link']}, which the theme doesn't define")
            continue
        fg, on = spec.get("fg"), spec.get("_on")
        if not fg or on is None:
            continue
        under = ([spec["bg"]] if on == "own" else ctx[on]) + spec.get("_also", [])
        worst = min(under, key=lambda s: rhino.contrast(fg, s))
        r = rhino.contrast(fg, worst)
        if r < spec["_min"] - 1e-9:
            errors.append(f"{v.name}: {name} {fg} is {r:.2f}:1 on {worst} ({on}), below {spec['_min']}")
    return errors


def check_lualine(v: Variant, theme: dict) -> list[str]:
    errors = []
    for mode, sections in theme.items():
        for sec, c in sections.items():
            r = rhino.contrast(c["fg"], c["bg"])
            if r < rhino.TEXT_MIN:
                errors.append(f"{v.name}: lualine {mode}.{sec} {c['fg']} on {c['bg']} is {r:.2f}:1")
    return errors


# --- writing -------------------------------------------------------------------------

def lua_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, list):
        return "{ " + ", ".join(lua_value(x) for x in value) + " }"
    if isinstance(value, dict):
        if not value:
            return "{}"
        return "{ " + ", ".join(f"{lua_key(k)} = {lua_value(x)}" for k, x in value.items()) + " }"
    raise TypeError(value)


def lua_key(key: str) -> str:
    return key if key.isidentifier() else f'["{key}"]'


def render(v: Variant, gs: dict[str, dict]) -> str:
    lines = [f"-- Critical Rhino, {v.name}. Generated by scripts/build_nvim.py from palette.toml;",
             "-- don't edit by hand. Run `make nvim` after changing the palette.",
             "return {", "  groups = {"]
    for name in sorted(gs, key=lambda n: (n.startswith("@"), n.lower())):
        spec = {k: x for k, x in gs[name].items() if not k.startswith("_")}
        lines.append(f"    {lua_key(name)} = {lua_value(spec)},")
    lines += ["  },", f"  terminal = {lua_value(terminal_colors(v))},", "  lualine = {"]
    for mode, sections in lualine(v).items():
        lines.append(f"    {mode} = {lua_value(sections)},")
    lines += ["  },", "}", ""]
    return "\n".join(lines)


def outputs(data: dict) -> tuple[dict[Path, str], list[str]]:
    files, errors = {}, []
    for name in rhino.appearances(data):
        v = load_variant(data, name)
        gs = groups(v)
        errors += check_groups(v, gs) + check_lualine(v, lualine(v))
        files[LUA_DIR / f"{name}.lua"] = render(v, gs)
    return files, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Neovim colorscheme from palette.toml.")
    parser.add_argument("--check", action="store_true", help="fail if generated files are stale or unreadable")
    args = parser.parse_args()
    files, errors = outputs(rhino.load())
    stale = [p for p, text in files.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
    if args.check:
        errors += [f"{p.relative_to(rhino.ROOT)} is stale; run make nvim" for p in stale]
        for e in errors:
            print(f"- {e}")
        if errors:
            return 1
        print(f"OK: Neovim theme is current, and every text color reads on the backgrounds it's drawn on "
              f"({sum(1 for _ in files)} variants).")
        return 0
    for e in errors:
        print(f"- {e}", file=sys.stderr)
    if errors:
        return 1
    for p in stale:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(files[p], encoding="utf-8")
        print(f"wrote {p.relative_to(rhino.ROOT)}")
    if not stale:
        print("Neovim theme is up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
