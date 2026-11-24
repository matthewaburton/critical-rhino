"""Build the VS Code and Cursor theme extension from palette.toml.

    python3 scripts/build_vscode.py            # write extras/vscode/: package.json, README.md, icon.png, themes/*.json
    python3 scripts/build_vscode.py --check    # fail (exit 1) if they're stale or a pairing is unreadable
    python3 scripts/build_vscode.py --report   # print every measured pair, worst first
    python3 scripts/build_vscode.py --package  # also write build/critical-rhino-<version>.vsix
    python3 scripts/build_vscode.py --install  # package it and install it into VS Code and Cursor

Cursor is a fork of VS Code and reads the same extension. Its icon is the logo's
dark icon as a PNG (logo.icon_png); the stores refuse SVG icons.

Every key that colors text (or a mark that must stand out, at 3:1) says where it's
drawn, as stacks of background keys: "list.hoverBackground>sideBar.background" is
the hover color laid over the side bar. Translucent keys are composited down the
stack the way the workbench draws them, so the check measures the colors on
screen. Code text (every syntax color) is measured on every surface code sits on:
the editor, the current line, selections, and each decoration drawn under text.

VS Code draws its editor decorations (find matches, word highlights, diff lines,
merge blocks) translucent and on top of the current line and the selection, so
they stack. Readable colors sit close to 4.5:1 on the lightest code surface, so a
light tint over the current line (green at 8%, say) drops code below 4.5:1. Here
every decoration is drawn in a color that is itself a code surface (the side-bar
surface, the selection, the diff washes) at some alpha: a mix of two surfaces
that each keep code at 4.5:1 keeps it there too, however they stack.

`make vscode:audit` complements this: it runs the real editors and measures the
text they draw, which also covers colors the workbench sets in its own CSS.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from html import escape
from pathlib import Path

import logo
import rhino
from rhino import Color

EXT_DIR = rhino.ROOT / "extras" / "vscode"
THEME_DIR = EXT_DIR / "themes"
PUBLISHER = "matthewaburton"
NAME = "critical-rhino"
DESCRIPTION = "A warm dark and light color theme: cream on near-black, readable at 4.5:1 everywhere."
ICON = "icon.png"
ICON_APPEARANCE = "dark"   # cream on near-black, on its own ground, so it holds on light and dark store pages
ENGINE = "^1.90.0"
TEXT, DIM = rhino.TEXT_MIN, rhino.DIM_MIN
CLEAR = "#00000000"

# Shorthand for where things are drawn.
EDITOR, SIDE, PANEL = "editor.background", "sideBar.background", "panel.background"
WIDGET, QUICK, NOTIFY = "editorWidget.background", "quickInput.background", "notifications.background"
GUTTER, LINE, SELECTION = "editorGutter.background", "editor.lineHighlightBackground", "editor.selectionBackground"
TERM = "terminal.background"


def rows(*bases: str) -> list[str]:
    """A list row on each base: at rest, under the pointer, and selected in an unfocused list."""
    return [s for b in bases for s in (b, f"list.hoverBackground>{b}", f"list.inactiveSelectionBackground>{b}")]


LISTS = rows(SIDE, PANEL, QUICK, EDITOR)


def alpha(hex_: str, a: float) -> str:
    """#RRGGBB at alpha `a`, as #RRGGBBAA."""
    return hex_[:7] if a >= 1 else f"{hex_[:7]}{round(a * 255):02X}"


def parse(value: str) -> Color:
    v = value.lstrip("#")
    return Color(f"#{v[:6].upper()}", int(v[6:8], 16) / 255 if len(v) == 8 else 1.0)


@dataclass
class Pair:
    fg: str                 # a theme key, or "#hex" for colors that aren't keys (syntax)
    on: tuple[str, ...]     # background stacks, top first
    min: float
    label: str


class Theme:
    """The workbench colors of one variant, plus where each text color is drawn."""

    def __init__(self, v: rhino.Variant):
        self.v = v
        self.colors: dict[str, str] = {}
        self.pairs: list[Pair] = []
        self.code: list[str] = []        # every background stack code text sits on (set by editor())

    def set(self, key: str, value: str) -> None:
        if key in self.colors:
            raise ValueError(f"{key} set twice")
        self.colors[key] = value

    def text(self, key: str, value: str, on, minimum: float = TEXT) -> None:
        self.set(key, value)
        self.pairs.append(Pair(key, tuple(on), minimum, key))

    def mark(self, key: str, value: str, on) -> None:
        self.text(key, value, on, DIM)

    def fill(self, bg_key: str, bg: str, fg_key: str, fg: str) -> None:
        """A colored fill and the text on it."""
        self.set(bg_key, bg)
        self.text(fg_key, fg, [bg_key])

    # --- measuring ---------------------------------------------------------------------

    def value(self, part: str) -> Color:
        if part.startswith("#"):
            return parse(part)
        if part not in self.colors:
            raise KeyError(f"{part} is drawn on but the theme doesn't set it")
        return parse(self.colors[part])

    def flatten(self, stack: str) -> str:
        """The opaque color of a background stack ("top>…>bottom")."""
        parts = stack.split(">")
        bottom = self.value(parts[-1])
        if bottom.alpha < 1:
            raise ValueError(f"{stack}: the bottom of a stack must be opaque ({parts[-1]} is {self.colors[parts[-1]]})")
        out = bottom.hex
        for part in reversed(parts[:-1]):
            out = rhino.composite(self.value(part), out)
        return out

    def measure(self, pair: Pair) -> list[tuple[float, str, str, str]]:
        """(ratio, text color as drawn, background, stack) for every stack the pair lands on."""
        fg = self.value(pair.fg)
        out = []
        for stack in pair.on:
            bg = self.flatten(stack)
            shown = rhino.composite(fg, bg)
            out.append((rhino.contrast(shown, bg), shown, bg, stack))
        return out


def least_alpha(color: str, surfaces: list[str], minimum: float) -> str:
    """`color` at the least opacity (in steps of 0.01) that keeps `minimum` contrast on every
    surface: translucent text that stays dim but readable wherever it lands."""
    for a in range(20, 101):
        if all(rhino.contrast(rhino.composite(Color(color, a / 100), s), s) >= minimum + 0.005 for s in surfaces):
            return alpha(color, a / 100)
    return color


def vivid(v: rhino.Variant, src: str) -> str:
    """The most saturated color in `src`'s hue that keeps every syntax color at 4.5:1 on it: as
    light as code allows in dark (as dark in light), with as much chroma as sRGB holds there.

    Browsers blend translucent colors on sRGB values, so a mix of two hues comes out darker
    than either. In dark that only adds contrast; in light, yellow half over the blue
    selection would dip below 4.5:1. So the color must also stay safe mixed into the
    background, the current line and the selection, and then decorations drawn in it stay
    readable however VS Code stacks them."""
    code = {c.hex for c in v.syntax.values()}
    dark = rhino.luminance(v.bg) < 0.18
    _, _, hue = rhino.oklch(src)
    bases = [v.bg, v.u("current_line"), v.u("editor_selection")]

    def safe(h: str) -> bool:
        mixes = [h] + [rhino.composite(Color(h, a), b) for b in bases for a in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)]
        return all(rhino.contrast(c, m) >= TEXT for c in code for m in mixes)

    best, best_distance = v.bg, 0.0
    for c in range(0, 161, 2):
        pick = None
        for i in (range(0, 1001, 5) if dark else range(1000, -1, -5)):
            h = rhino.oklch_to_hex(i / 1000, c / 1000, hue)
            if h is None:
                continue
            if not safe(h):
                break
            pick = h
        if pick and rhino.distance(pick, v.bg) > best_distance:
            best, best_distance = pick, rhino.distance(pick, v.bg)
    return best


# --- the workbench ---------------------------------------------------------------------

def workbench(v: rhino.Variant) -> Theme:
    t = Theme(v)
    U, R = v.u, v.ui                         # opaque over the editor background; the role as written
    C = v.colors

    def ui(role: str) -> str:                # a role as written, translucent if it is
        return R[role].hex_with_alpha

    bg, fg, surface, raised = v.bg, U("foreground"), U("surface"), U("surface_raised")
    border, focus, accent = U("border"), U("focus"), U("accent")
    sel, sel_fg, soft, edsel = U("selection"), U("selection_foreground"), U("selection_soft"), U("editor_selection")
    muted, dim, hover = ui("muted_foreground"), U("dim_foreground"), ui("hover")
    link, match = U("link"), U("match_highlight")
    error, warning, info, hint = U("error"), U("warning"), U("info"), U("hint")
    added, modified, deleted, conflict = U("git_added"), U("git_modified"), U("git_deleted"), U("git_conflict")
    navy = C["blue_accent_2"]
    shadow = alpha("#000000", 0.36) if v.name == "dark" else alpha(C["loden"], 0.16)
    on = v.on

    # Colors that decorations are drawn in, each a surface that keeps code at 4.5:1 (see the
    # docstring): the word-highlight gray, the yellow wash, and the strongest tint of each hue.
    word, wash_yellow = U("word_highlight"), U("diff_text_bg")
    green, red, blue, yellow = (vivid(v, C[n]) for n in ("green", "red", "blue", "yellow"))

    # --- base ---------------------------------------------------------------------------
    t.text("foreground", fg, LISTS + [WIDGET, NOTIFY, "titleBar.activeBackground", "input.background"])
    t.text("descriptionForeground", muted, LISTS + [WIDGET, NOTIFY, "input.background"])
    # disabled controls: the text color, translucent, at the least opacity that keeps 3:1 on every surface
    t.text("disabledForeground", least_alpha(fg, [bg, surface, raised], DIM),
           [SIDE, PANEL, EDITOR, WIDGET, "input.background"], DIM)
    t.text("errorForeground", error, [SIDE, PANEL, EDITOR, WIDGET, NOTIFY])
    t.text("strongForeground", fg, [SIDE, PANEL, EDITOR, WIDGET])
    t.mark("focusBorder", focus, [EDITOR, SIDE, PANEL, "input.background", "titleBar.activeBackground"])
    t.mark("icon.foreground", fg, [EDITOR, SIDE, PANEL, WIDGET, "titleBar.activeBackground"])
    t.set("selection.background", edsel)
    t.set("widget.border", border)
    t.set("widget.shadow", shadow)
    t.set("sash.hoverBorder", focus)
    t.set("scrollbar.shadow", shadow)
    t.set("scrollbar.background", CLEAR)
    for prefix in ("scrollbarSlider", "minimapSlider", "notebookScrollbarSlider"):
        t.set(f"{prefix}.background", alpha(fg, 0.20))
        t.set(f"{prefix}.hoverBackground", alpha(fg, 0.32))
        t.set(f"{prefix}.activeBackground", alpha(fg, 0.44))
    t.mark("progressBar.background", focus, [EDITOR, SIDE, PANEL, WIDGET])
    t.set("toolbar.hoverBackground", hover)
    t.set("toolbar.activeBackground", alpha(soft, 1))
    t.set("toolbar.hoverOutline", CLEAR)
    t.set("actionBar.toggledBackground", soft)

    # --- activity bar ----------------------------------------------------------------------
    t.set("activityBar.background", bg)
    t.mark("activityBar.foreground", focus, ["activityBar.background"])          # the active view's icon
    t.mark("activityBar.inactiveForeground", muted, ["activityBar.background"])
    t.set("activityBar.activeBorder", focus)
    t.set("activityBar.activeFocusBorder", focus)
    t.set("activityBar.activeBackground", CLEAR)
    t.set("activityBar.border", bg)
    t.set("activityBar.dropBorder", focus)
    t.fill("activityBarBadge.background", sel, "activityBarBadge.foreground", sel_fg)
    t.set("activityBarTop.background", surface)
    t.mark("activityBarTop.foreground", focus, ["activityBarTop.background"])
    t.mark("activityBarTop.inactiveForeground", muted, ["activityBarTop.background"])
    t.set("activityBarTop.activeBorder", focus)
    t.set("activityBarTop.activeBackground", CLEAR)
    t.set("activityBarTop.dropBorder", focus)
    t.fill("activityErrorBadge.background", error, "activityErrorBadge.foreground", on(error))
    t.fill("activityWarningBadge.background", warning, "activityWarningBadge.foreground", on(warning))
    t.fill("badge.background", U("badge"), "badge.foreground", U("badge_foreground"))
    t.fill("profileBadge.background", U("badge"), "profileBadge.foreground", U("badge_foreground"))
    t.fill("panelTitleBadge.background", sel, "panelTitleBadge.foreground", sel_fg)
    t.fill("extensionBadge.remoteBackground", U("status_remote"), "extensionBadge.remoteForeground",
           U("status_remote_foreground"))

    # --- side bar, panel, title bar ------------------------------------------------------------
    t.set(SIDE, surface)
    t.text("sideBar.foreground", fg, rows(SIDE))
    t.set("sideBar.border", surface)
    t.set("sideBar.dropBackground", alpha(sel, 0.25))
    t.set("sideBarTitle.background", surface)
    t.text("sideBarTitle.foreground", fg, ["sideBarTitle.background"])
    t.set("sideBarTitle.border", CLEAR)
    t.set("sideBarSectionHeader.background", bg)
    t.text("sideBarSectionHeader.foreground", fg, ["sideBarSectionHeader.background"])
    t.set("sideBarSectionHeader.border", CLEAR)
    t.set("sideBarStickyScroll.background", surface)
    t.set("sideBarStickyScroll.border", border)
    t.set("sideBarStickyScroll.shadow", shadow)
    t.set("sideBarActivityBarTop.border", CLEAR)

    t.set(PANEL, surface)                       # tool windows share the side bar's surface
    t.set("panel.border", CLEAR)
    t.set("panel.dropBorder", focus)
    t.text("panelTitle.activeForeground", fg, [PANEL])
    t.text("panelTitle.inactiveForeground", muted, [PANEL])
    t.set("panelTitle.activeBorder", focus)
    t.set("panelTitle.border", CLEAR)
    t.set("panelInput.border", border)
    t.set("panelSection.border", border)
    t.set("panelSection.dropBackground", alpha(sel, 0.25))
    t.set("panelSectionHeader.background", bg)
    t.text("panelSectionHeader.foreground", fg, ["panelSectionHeader.background"])
    t.set("panelSectionHeader.border", CLEAR)
    t.set("panelStickyScroll.background", surface)
    t.set("panelStickyScroll.border", border)
    t.set("panelStickyScroll.shadow", shadow)
    t.set("outputView.background", surface)
    t.set("outputViewStickyScroll.background", surface)

    t.set("titleBar.activeBackground", raised)
    t.text("titleBar.activeForeground", fg, ["titleBar.activeBackground"])
    t.set("titleBar.inactiveBackground", raised)
    t.text("titleBar.inactiveForeground", muted, ["titleBar.inactiveBackground"])
    t.set("titleBar.border", CLEAR)
    t.set("menubar.selectionBackground", hover)
    t.text("menubar.selectionForeground", fg, ["menubar.selectionBackground>titleBar.activeBackground"])
    t.set("menubar.selectionBorder", CLEAR)
    t.set("commandCenter.background", surface)
    t.text("commandCenter.foreground", fg, ["commandCenter.background", "commandCenter.activeBackground>commandCenter.background",
                                           "commandCenter.debuggingBackground>titleBar.activeBackground"])
    t.set("commandCenter.activeBackground", hover)
    t.text("commandCenter.activeForeground", fg, ["commandCenter.activeBackground>commandCenter.background"])
    t.text("commandCenter.inactiveForeground", muted, ["commandCenter.background"])
    t.set("commandCenter.border", border)
    t.set("commandCenter.activeBorder", border)
    t.set("commandCenter.inactiveBorder", alpha(border, 0.6))
    t.set("commandCenter.debuggingBackground", alpha(U("status_debugging"), 0.26))

    t.set("menu.background", surface)
    t.text("menu.foreground", fg, ["menu.background"])
    t.set("menu.selectionBackground", sel)
    t.text("menu.selectionForeground", sel_fg, ["menu.selectionBackground"])
    t.set("menu.selectionBorder", CLEAR)
    t.set("menu.separatorBackground", border)
    t.set("menu.border", border)

    # --- lists and trees ------------------------------------------------------------------
    t.set("list.hoverBackground", hover)        # darker in dark, lighter in light: names keep 4.5:1
    t.set("list.inactiveSelectionBackground", soft)
    t.set("list.activeSelectionBackground", sel)
    t.text("list.activeSelectionForeground", sel_fg, ["list.activeSelectionBackground"])
    t.mark("list.activeSelectionIconForeground", sel_fg, ["list.activeSelectionBackground"])
    t.text("list.focusHighlightForeground", U("match_highlight_selected"), ["list.activeSelectionBackground"])
    t.set("list.focusOutline", focus)
    t.set("list.inactiveFocusOutline", CLEAR)
    t.text("list.highlightForeground", match, LISTS)
    t.text("list.errorForeground", error, LISTS)
    t.text("list.warningForeground", warning, LISTS)
    t.text("list.invalidItemForeground", error, LISTS)
    t.text("list.deemphasizedForeground", muted, LISTS)
    t.set("list.filterMatchBackground", wash_yellow)
    t.set("list.filterMatchBorder", CLEAR)
    t.set("list.dropBackground", soft)
    t.set("list.dropBetweenBackground", focus)
    t.set("listFilterWidget.background", U("filter_widget"))
    t.set("listFilterWidget.outline", CLEAR)
    t.set("listFilterWidget.noMatchesOutline", error)
    t.set("listFilterWidget.shadow", shadow)
    t.set("tree.indentGuidesStroke", border)
    t.set("tree.inactiveIndentGuidesStroke", alpha(border, 0.5))
    t.set("tree.tableColumnsBorder", border)
    t.set("tree.tableOddRowsBackground", hover)
    t.set("keybindingTable.headerBackground", surface)
    t.set("keybindingTable.rowsBackground", hover)

    # git colors on file names: lists and tabs
    names = LISTS + ["tab.activeBackground", "tab.inactiveBackground", "tab.hoverBackground>tab.inactiveBackground"]
    for key, color in (("addedResourceForeground", added), ("untrackedResourceForeground", added),
                       ("renamedResourceForeground", added), ("modifiedResourceForeground", modified),
                       ("stageModifiedResourceForeground", modified), ("deletedResourceForeground", deleted),
                       ("stageDeletedResourceForeground", deleted), ("conflictingResourceForeground", conflict),
                       ("submoduleResourceForeground", info)):
        t.text(f"gitDecoration.{key}", color, names)
    t.text("gitDecoration.ignoredResourceForeground", dim, names, DIM)

    # --- inputs and controls --------------------------------------------------------------
    t.set("input.background", raised)
    t.text("input.foreground", fg, ["input.background"])
    t.set("input.border", border)
    t.text("input.placeholderForeground", muted, ["input.background"])
    t.set("inputOption.activeBackground", soft)
    t.text("inputOption.activeForeground", fg, ["inputOption.activeBackground>input.background"])
    t.set("inputOption.activeBorder", focus)
    t.set("inputOption.hoverBackground", hover)
    for kind, color in (("error", error), ("warning", warning), ("info", info)):
        t.set(f"inputValidation.{kind}Background", surface)
        t.text(f"inputValidation.{kind}Foreground", fg, [f"inputValidation.{kind}Background"])
        t.set(f"inputValidation.{kind}Border", color)
    t.set("dropdown.background", raised)
    t.text("dropdown.foreground", fg, ["dropdown.background", "dropdown.listBackground"])
    t.set("dropdown.border", border)
    t.set("dropdown.listBackground", surface)
    t.set("checkbox.background", raised)
    t.mark("checkbox.foreground", fg, ["checkbox.background"])
    t.set("checkbox.border", border)
    t.set("checkbox.selectBackground", surface)
    t.set("checkbox.selectBorder", focus)
    t.set("radio.activeBackground", soft)
    t.text("radio.activeForeground", fg, ["radio.activeBackground>input.background"])
    t.set("radio.activeBorder", focus)
    t.set("radio.inactiveBackground", CLEAR)
    t.text("radio.inactiveForeground", muted, [SIDE, EDITOR, WIDGET])
    t.set("radio.inactiveBorder", border)
    t.set("radio.inactiveHoverBackground", hover)
    t.set("keybindingLabel.background", raised)
    t.text("keybindingLabel.foreground", fg, ["keybindingLabel.background"])
    t.set("keybindingLabel.border", border)
    t.set("keybindingLabel.bottomBorder", border)

    # buttons: cream on the blue fills; hover is the brighter selection blue, which keeps cream at 4.5:1
    for prefix in ("button", "extensionButton", "agentsNewSessionButton"):
        t.set(f"{prefix}.background", accent)
        t.text(f"{prefix}.foreground", U("accent_foreground"), [f"{prefix}.background", f"{prefix}.hoverBackground"])
        t.set(f"{prefix}.hoverBackground", sel)
        t.set(f"{prefix}.border", CLEAR)
    t.set("button.separator", alpha(U("accent_foreground"), 0.4))
    t.set("extensionButton.separator", alpha(U("accent_foreground"), 0.4))
    t.set("extensionButton.prominentBackground", accent)
    t.text("extensionButton.prominentForeground", U("accent_foreground"),
           ["extensionButton.prominentBackground", "extensionButton.prominentHoverBackground"])
    t.set("extensionButton.prominentHoverBackground", sel)
    t.set("button.secondaryBackground", raised)
    t.text("button.secondaryForeground", fg, ["button.secondaryBackground", "button.secondaryHoverBackground"])
    t.set("button.secondaryHoverBackground", border)
    t.set("button.secondaryBorder", CLEAR)
    t.set("agentsUpdateButton.downloadedBackground", accent)
    t.set("agentsUpdateButton.downloadingBackground", soft)

    # --- quick input, notifications, banner --------------------------------------------------
    t.set(QUICK, surface)
    t.text("quickInput.foreground", fg, rows(QUICK))
    t.set("quickInputTitle.background", raised)
    for key in ("quickInputList.focusBackground", "quickInput.list.focusBackground"):
        t.set(key, sel)
    t.text("quickInputList.focusForeground", sel_fg, ["quickInputList.focusBackground"])
    t.mark("quickInputList.focusIconForeground", sel_fg, ["quickInputList.focusBackground"])
    t.text("quickInputList.focusHighlightForeground", U("match_highlight_selected"), ["quickInputList.focusBackground"])
    t.text("pickerGroup.foreground", focus, rows(QUICK))
    t.set("pickerGroup.border", border)

    t.set(NOTIFY, surface)
    t.text("notifications.foreground", fg, [NOTIFY])
    t.set("notifications.border", border)
    t.set("notificationCenterHeader.background", raised)
    t.text("notificationCenterHeader.foreground", fg, ["notificationCenterHeader.background"])
    t.set("notificationToast.border", border)
    t.set("notificationCenter.border", border)
    t.text("notificationLink.foreground", link, [NOTIFY])
    for kind, color in (("Error", error), ("Warning", warning), ("Info", info)):
        t.mark(f"notifications{kind}Icon.foreground", color, [NOTIFY])
    t.set("banner.background", soft)
    t.text("banner.foreground", fg, ["banner.background"])
    t.mark("banner.iconForeground", info, ["banner.background"])

    # --- status bar: cream on the blue fill. Hovered items turn the selection blue with cream
    # text, whatever the bar's color, so black text on the orange debugging bar never sits on
    # a darkened hover.
    t.fill("statusBar.background", accent, "statusBar.foreground", U("accent_foreground"))
    t.set("statusBar.border", CLEAR)
    t.fill("statusBar.debuggingBackground", U("status_debugging"), "statusBar.debuggingForeground",
           U("status_debugging_foreground"))
    t.set("statusBar.debuggingBorder", CLEAR)
    t.fill("statusBar.noFolderBackground", U("status_no_folder"), "statusBar.noFolderForeground",
           U("status_no_folder_foreground"))
    t.set("statusBar.noFolderBorder", CLEAR)
    t.mark("statusBar.focusBorder", U("accent_foreground"), ["statusBar.background"])
    t.mark("statusBarItem.focusBorder", U("accent_foreground"), ["statusBar.background"])
    t.fill("statusBarItem.hoverBackground", sel, "statusBarItem.hoverForeground", sel_fg)
    t.set("statusBarItem.activeBackground", sel)
    t.set("statusBarItem.compactHoverBackground", sel)
    t.fill("statusBarItem.prominentBackground", sel, "statusBarItem.prominentForeground", sel_fg)
    t.fill("statusBarItem.prominentHoverBackground", navy, "statusBarItem.prominentHoverForeground", sel_fg)
    t.fill("statusBarItem.remoteBackground", U("status_remote"), "statusBarItem.remoteForeground",
           U("status_remote_foreground"))
    for kind, fill in (("error", error), ("warning", warning), ("offline", U("badge"))):
        t.fill(f"statusBarItem.{kind}Background", fill, f"statusBarItem.{kind}Foreground", on(fill))
    for kind in ("remote", "error", "warning", "offline"):
        t.fill(f"statusBarItem.{kind}HoverBackground", sel, f"statusBarItem.{kind}HoverForeground", sel_fg)

    # --- tabs and breadcrumbs ----------------------------------------------------------------
    t.set("editorGroupHeader.tabsBackground", surface)
    t.set("editorGroupHeader.tabsBorder", CLEAR)
    t.set("editorGroupHeader.noTabsBackground", bg)
    t.set("editorGroupHeader.border", CLEAR)
    t.set("editorGroupHeader.connectedTabsBackground", surface)
    t.set("tab.activeBackground", bg)
    t.text("tab.activeForeground", fg, ["tab.activeBackground"])
    t.set("tab.activeBorder", CLEAR)
    t.set("tab.activeBorderTop", focus)
    t.set("tab.inactiveBackground", surface)
    t.text("tab.inactiveForeground", muted, ["tab.inactiveBackground", "tab.hoverBackground>tab.inactiveBackground"])
    t.set("tab.hoverBackground", hover)
    t.text("tab.hoverForeground", fg, ["tab.hoverBackground>tab.inactiveBackground"])
    t.set("tab.hoverBorder", CLEAR)
    t.set("tab.unfocusedActiveBackground", bg)
    t.text("tab.unfocusedActiveForeground", muted, ["tab.unfocusedActiveBackground"])
    t.set("tab.unfocusedActiveBorder", CLEAR)
    t.set("tab.unfocusedActiveBorderTop", border)
    t.set("tab.unfocusedInactiveBackground", surface)
    t.text("tab.unfocusedInactiveForeground", muted, ["tab.unfocusedInactiveBackground"])
    t.set("tab.unfocusedHoverBackground", hover)
    t.text("tab.unfocusedHoverForeground", fg, ["tab.unfocusedHoverBackground>tab.unfocusedInactiveBackground"])
    t.set("tab.unfocusedHoverBorder", CLEAR)
    t.set("tab.selectedBackground", soft)
    t.text("tab.selectedForeground", fg, ["tab.selectedBackground"])
    t.set("tab.selectedBorderTop", focus)
    t.set("tab.border", surface)
    t.set("tab.divider", border)
    t.set("tab.lastPinnedBorder", border)
    t.set("tab.dragAndDropBorder", focus)
    for key in ("activeModifiedBorder", "inactiveModifiedBorder", "unfocusedActiveModifiedBorder",
                "unfocusedInactiveModifiedBorder"):
        t.set(f"tab.{key}", modified)
    t.set("tab.worktreeBorder", border)               # Cursor
    t.set("breadcrumb.background", bg)
    t.text("breadcrumb.foreground", muted, ["breadcrumb.background"])
    t.text("breadcrumb.focusForeground", fg, ["breadcrumb.background"])
    t.text("breadcrumb.activeSelectionForeground", fg, ["breadcrumb.background"])
    t.set("breadcrumbPicker.background", surface)

    # the "modern" layout (floating panels), when it's turned on
    t.set("modernUI.shellBackground", bg)
    t.set("modernUI.inactiveShellBackground", bg)
    t.set("surface.background", surface)
    t.text("surface.foreground", fg, ["surface.background"])
    t.set("surface.border", border)
    t.set("modernPanel.border", border)
    t.set("modernSash.gripForeground", border)
    t.set("modernActivityBar.background", bg)
    t.set("modernActivityBar.border", border)
    t.set("modernActivityBar.inactiveBackground", CLEAR)
    for prefix in ("modernActivityBar", "modernActivityBarItem", "modernTab"):
        t.set(f"{prefix}.activeBackground", soft)
        t.text(f"{prefix}.activeForeground", fg, [f"{prefix}.activeBackground>{EDITOR}"])
        t.set(f"{prefix}.hoverBackground", hover)
        t.text(f"{prefix}.hoverForeground", fg, [f"{prefix}.hoverBackground>{EDITOR}", f"{prefix}.hoverBackground>{SIDE}"])
    t.set("modernEditorTab.activeBackground", bg)
    t.text("modernEditorTab.activeForeground", fg, ["modernEditorTab.activeBackground"])
    t.set("modernEditorTab.inactiveBackground", surface)
    t.set("modernEditorTab.hoverBackground", hover)
    t.text("modernEditorTab.hoverForeground", fg, ["modernEditorTab.hoverBackground>modernEditorTab.inactiveBackground"])
    t.set("modernEditorTab.activeHoverBackground", hover)
    for key in ("activeActionBackground", "activeHoverActionBackground", "hoverActionBackground", "selectedActionBackground"):
        t.set(f"modernEditorTab.{key}", hover)

    # --- editor groups -----------------------------------------------------------------------
    t.set("editorGroup.border", border)
    t.set("editorGroup.emptyBackground", bg)
    t.set("editorGroup.focusedEmptyBorder", focus)
    t.set("editorGroup.dropBackground", alpha(sel, 0.25))
    t.set("editorGroup.dropIntoPromptBackground", surface)
    t.text("editorGroup.dropIntoPromptForeground", fg, ["editorGroup.dropIntoPromptBackground"])
    t.set("editorGroup.dropIntoPromptBorder", border)
    t.set("editorPane.background", bg)
    t.set("sideBySideEditor.horizontalBorder", border)
    t.set("sideBySideEditor.verticalBorder", border)
    t.set("editorWatermark.foreground", border)       # Cursor

    editor(t, locals())
    widgets(t, locals())
    rest(t, locals())
    return t


def editor(t: Theme, k: dict) -> None:
    """The editor: text, gutter, cursor, and every decoration drawn under code."""
    v, bg, fg, border, focus = k["v"], k["bg"], k["fg"], k["border"], k["focus"]
    muted, dim, hover, edsel, soft, warning = k["muted"], k["dim"], k["hover"], k["edsel"], k["soft"], k["warning"]
    word, green, red, blue, yellow = k["word"], k["green"], k["red"], k["blue"], k["yellow"]
    U = v.u

    t.set(EDITOR, bg)
    t.set("editor.foreground", v.s("text"))
    t.set(LINE, U("current_line"))           # opaque: drawn under the selection, so nothing is hidden
    t.set("editor.inactiveLineHighlightBackground", U("current_line"))
    t.set("editor.lineHighlightBorder", CLEAR)
    t.set(SELECTION, edsel)
    t.set("editor.inactiveSelectionBackground", alpha(edsel, 0.6))
    # Find: black text on yellow (other matches) and orange (the current one), as in Neovim and Ghostty
    t.fill("editor.findMatchBackground", U("search_current"), "editor.findMatchForeground", U("search_current_foreground"))
    t.set("editor.findMatchBorder", CLEAR)
    t.set("editor.findMatchHighlightBackground", alpha(U("search"), 0.9))
    t.text("editor.findMatchHighlightForeground", U("search_foreground"),
           [f"editor.findMatchHighlightBackground>{s}" for s in (EDITOR, f"{LINE}>{EDITOR}", f"{SELECTION}>{EDITOR}")])
    t.set("editor.findMatchHighlightBorder", CLEAR)
    t.set("editor.findRangeHighlightBackground", alpha(edsel, 0.4))
    t.set("editor.findRangeHighlightBorder", CLEAR)
    # Decorations under code, each in a code surface's color (see the module docstring). VS Code
    # draws them over the selection, so whole lines stay near half strength: a selection inside a
    # diff still shows. Text highlights stack on their line's tint.
    deco = {
        "editor.selectionHighlightBackground": alpha(edsel, 0.5),     # other copies of the selected text
        "editor.wordHighlightBackground": alpha(word, 0.9),           # other uses of the symbol (read)
        "editor.wordHighlightStrongBackground": alpha(blue, 0.6),     # ... (written)
        "editor.wordHighlightTextBackground": alpha(word, 0.6),       # textual matches
        "editor.hoverHighlightBackground": alpha(edsel, 0.5),
        "editor.rangeHighlightBackground": alpha(word, 0.7),
        "editor.symbolHighlightBackground": alpha(yellow, 0.6),
        "editor.linkedEditingBackground": alpha(edsel, 0.5),
        "editor.snippetTabstopHighlightBackground": alpha(edsel, 0.5),
        "editor.stackFrameHighlightBackground": alpha(yellow, 0.5),
        "editor.focusedStackFrameHighlightBackground": alpha(green, 0.5),
        "editor.foldBackground": alpha(soft, 0.3),
        "diffEditor.insertedLineBackground": alpha(green, 0.5),
        "diffEditor.removedLineBackground": alpha(red, 0.5),
        "diffEditor.insertedTextBackground": alpha(green, 0.55),
        "diffEditor.removedTextBackground": alpha(red, 0.55),
        "diffEditor.unchangedCodeBackground": alpha(word, 0.5),
        "merge.currentContentBackground": alpha(green, 0.4),
        "merge.currentHeaderBackground": alpha(green, 0.75),
        "merge.incomingContentBackground": alpha(blue, 0.4),
        "merge.incomingHeaderBackground": alpha(blue, 0.75),
        "merge.commonContentBackground": alpha(word, 0.6),
        "merge.commonHeaderBackground": alpha(word, 0.95),
        "mergeEditor.change.background": alpha(blue, 0.4),
        "mergeEditor.change.word.background": alpha(blue, 0.6),
        "mergeEditor.changeBase.background": alpha(red, 0.4),
        "mergeEditor.changeBase.word.background": alpha(red, 0.6),
        "mergeEditor.conflict.input1.background": alpha(green, 0.4),
        "mergeEditor.conflict.input2.background": alpha(blue, 0.4),
        "mergeEditor.conflictingLines.background": alpha(yellow, 0.4),
        "inlineEdit.originalBackground": alpha(red, 0.3),
        "inlineEdit.modifiedBackground": alpha(green, 0.3),
        "inlineEdit.originalChangedLineBackground": alpha(red, 0.5),
        "inlineEdit.originalChangedTextBackground": alpha(red, 0.55),
        "inlineEdit.modifiedChangedLineBackground": alpha(green, 0.5),
        "inlineEdit.modifiedChangedTextBackground": alpha(green, 0.55),
        "inlineChatDiff.inserted": alpha(green, 0.5),
        "inlineChatDiff.removed": alpha(red, 0.5),
        "testing.coveredBackground": alpha(green, 0.4),
        "testing.uncoveredBackground": alpha(red, 0.4),
        "testing.uncoveredBranchBackground": alpha(red, 0.7),
        "testing.message.error.lineBackground": alpha(red, 0.4),
        "testing.message.info.lineBackground": alpha(blue, 0.4),
        "editorCommentsWidget.rangeBackground": alpha(word, 0.6),
        "editorCommentsWidget.rangeActiveBackground": alpha(blue, 0.5),
        "peekViewEditor.matchHighlightBackground": CLEAR,
        "chat.findMatchBackground": alpha(yellow, 0.8),
        "chat.findMatchHighlightBackground": alpha(yellow, 0.5),
        "notebook.symbolHighlightBackground": alpha(word, 0.7),
        "searchEditor.findMatchBackground": alpha(yellow, 0.8),
        "editorUnicodeHighlight.background": CLEAR,
        "editorBracketMatch.background": CLEAR,
        "editorError.background": CLEAR,
        "editorWarning.background": CLEAR,
        "editorInfo.background": CLEAR,
        "editor.inlineValuesBackground": CLEAR,
        "editorInlayHint.background": CLEAR,
        "editorInlayHint.typeBackground": CLEAR,
        "editorInlayHint.parameterBackground": CLEAR,
        "editorGhostText.background": CLEAR,
    }
    for key, value in deco.items():
        t.set(key, value)
    for key in ("editor.selectionHighlightBorder", "editor.wordHighlightBorder", "editor.wordHighlightStrongBorder",
                "editor.wordHighlightTextBorder", "editor.rangeHighlightBorder", "editor.symbolHighlightBorder",
                "editor.snippetTabstopHighlightBorder", "diffEditor.insertedTextBorder", "diffEditor.removedTextBorder",
                "merge.border", "editorError.border", "editorWarning.border", "editorInfo.border", "editorHint.border",
                "editorGhostText.border", "editorUnnecessaryCode.border", "editorCommentsWidget.rangeBorder"):
        t.set(key, CLEAR)
    t.set("peekViewEditor.matchHighlightBorder", warning)
    t.set("searchEditor.findMatchBorder", warning)
    t.set("editor.snippetFinalTabstopHighlightBackground", CLEAR)
    t.set("editor.snippetFinalTabstopHighlightBorder", dim)
    t.set("editorUnicodeHighlight.border", warning)
    t.set("editorBracketMatch.border", dim)

    # Every surface code sits on: the editor's own, the other editors' (peek, sticky scroll,
    # notebooks, output, code blocks), and each decoration over the editor, the current line
    # and the selection. Diff and merge lines also stack their text highlights.
    bases = [EDITOR, f"{LINE}>{EDITOR}", f"{SELECTION}>{EDITOR}", f"editor.inactiveSelectionBackground>{EDITOR}",
             f"{SELECTION}>{LINE}>{EDITOR}"]
    code = list(bases)
    code += [f"{key}>{b}" for key in deco if deco[key] != CLEAR for b in bases]
    code += [f"{top}>{line}>{b}" for top, line in (("diffEditor.insertedTextBackground", "diffEditor.insertedLineBackground"),
                                                   ("diffEditor.removedTextBackground", "diffEditor.removedLineBackground"),
                                                   ("inlineEdit.modifiedChangedTextBackground", "inlineEdit.modifiedChangedLineBackground"),
                                                   ("inlineEdit.originalChangedTextBackground", "inlineEdit.originalChangedLineBackground"),
                                                   ("mergeEditor.change.word.background", "mergeEditor.change.background"),
                                                   ("mergeEditor.changeBase.word.background", "mergeEditor.changeBase.background"),
                                                   ("editor.wordHighlightBackground", "diffEditor.insertedLineBackground"),
                                                   ("editor.wordHighlightBackground", "diffEditor.removedLineBackground"),
                                                   ("editor.selectionHighlightBackground", "diffEditor.insertedLineBackground"))
             for b in bases[:3]]
    code += ["peekViewEditor.background", f"{LINE}>peekViewEditor.background", f"{SELECTION}>peekViewEditor.background",
             "editorStickyScroll.background", "editorStickyScrollHover.background>editorStickyScroll.background",
             "notebook.cellEditorBackground", "outputView.background", "textCodeBlock.background",
             "walkThrough.embeddedEditorBackground", WIDGET]
    t.code = code

    t.text("editorLineNumber.foreground", v.ui["line_numbers"].hex_with_alpha, [GUTTER])
    t.text("editorLineNumber.activeForeground", U("line_number_active"), [GUTTER, f"{LINE}>{GUTTER}"])
    t.text("editorActiveLineNumber.foreground", U("line_number_active"), [GUTTER, f"{LINE}>{GUTTER}"])
    t.text("editorLineNumber.dimmedForeground", dim, [GUTTER], DIM)
    t.set(GUTTER, bg)
    gutter = [GUTTER, f"{LINE}>{GUTTER}"]
    for kind, role in (("added", "gutter_added"), ("modified", "gutter_modified"), ("deleted", "gutter_deleted")):
        t.mark(f"editorGutter.{kind}Background", U(role), gutter)
        t.mark(f"editorGutter.{kind}SecondaryBackground", U(role.replace("gutter_", "gutter_staged_")), gutter)
        t.set(f"minimapGutter.{kind}Background", U(role))
    t.mark("editorGutter.foldingControlForeground", muted, gutter)
    t.mark("editorGutter.commentGlyphForeground", fg, gutter)
    t.mark("editorGutter.commentUnresolvedGlyphForeground", warning, gutter)
    t.mark("editorGutter.commentDraftGlyphForeground", muted, gutter)
    t.set("editorGutter.commentRangeForeground", border)
    t.set("editorGutter.itemBackground", k["raised"])
    t.mark("editorGutter.itemGlyphForeground", fg, ["editorGutter.itemBackground"])

    cursor = v.t("cursor")                  # the terminal's cursor: 3:1 on every code surface
    t.mark("editorCursor.foreground", cursor, code)
    t.set("editorCursor.background", v.on(cursor))         # the character under a block cursor
    t.pairs.append(Pair("editorCursor.background", ("editorCursor.foreground",), TEXT, "editorCursor.background"))
    for kind in ("primary", "secondary"):
        t.set(f"editorMultiCursor.{kind}.foreground", cursor)
        t.set(f"editorMultiCursor.{kind}.background", v.on(cursor))

    t.text("editorBracketMatch.foreground", U("bracket_match"), code)
    for n in range(1, 7):
        t.set(f"editorBracketHighlight.foreground{n}", v.s("text"))   # brackets are punctuation: plain text
        t.set(f"editorBracketPairGuide.background{n}", CLEAR)
        t.set(f"editorBracketPairGuide.activeBackground{n}", border)
    t.text("editorBracketHighlight.unexpectedBracket.foreground", k["error"], code)
    t.set("editorIndentGuide.background1", border)
    t.set("editorIndentGuide.activeBackground1", dim)
    t.set("editorWhitespace.foreground", border)
    t.set("editorWordWrapIndicator.foreground", border)
    t.set("editorRuler.foreground", U("ruler"))
    t.text("editorCodeLens.foreground", muted, bases)
    t.text("editorLink.activeForeground", k["link"], code)
    t.text("editor.foldPlaceholderForeground", muted, bases)
    t.text("editor.placeholder.foreground", muted, [EDITOR])
    t.text("editorInlayHint.foreground", muted, code)
    t.text("editorInlayHint.typeForeground", muted, code)
    t.text("editorInlayHint.parameterForeground", muted, code)
    t.text("editor.inlineValuesForeground", muted, code)
    t.text("git.blame.editorDecorationForeground", muted, bases)
    t.set("editor.compositionBorder", fg)
    for kind, role in (("Error", "error"), ("Warning", "warning"), ("Info", "info"), ("Hint", "hint")):
        t.mark(f"editor{kind}.foreground", U(role), code)        # squiggles
    t.mark("editorLightBulb.foreground", warning, gutter)
    t.mark("editorLightBulbAutoFix.foreground", k["info"], gutter)
    t.mark("editorLightBulbAi.foreground", focus, gutter)
    t.set("editorStickyScroll.background", bg)
    t.set("editorStickyScroll.border", border)
    t.set("editorStickyScroll.shadow", k["shadow"])
    t.set("editorStickyScrollHover.background", hover)
    t.set("editorStickyScrollGutter.background", bg)


def widgets(t: Theme, k: dict) -> None:
    """Popups over the editor: find, suggest, hover, peek, and the diff and merge chrome."""
    v, bg, fg, surface, raised, border = k["v"], k["bg"], k["fg"], k["surface"], k["raised"], k["border"]
    focus, muted, soft, match = k["focus"], k["muted"], k["soft"], k["match"]
    sel, sel_fg, error, warning, info = k["sel"], k["sel_fg"], k["error"], k["warning"], k["info"]
    shadow, wash_yellow = k["shadow"], k["wash_yellow"]
    U = v.u

    t.set(WIDGET, surface)
    t.text("editorWidget.foreground", fg, [WIDGET])
    t.set("editorWidget.border", border)
    t.set("editorWidget.resizeBorder", focus)
    suggest = ["editorSuggestWidget.background", "editorSuggestWidget.selectedBackground>editorSuggestWidget.background"]
    t.set("editorSuggestWidget.background", surface)
    t.set("editorSuggestWidget.border", border)
    t.text("editorSuggestWidget.foreground", fg, suggest)
    t.set("editorSuggestWidget.selectedBackground", soft)
    t.text("editorSuggestWidget.selectedForeground", fg, suggest[1:])
    t.text("editorSuggestWidget.highlightForeground", match, suggest)
    t.text("editorSuggestWidget.focusHighlightForeground", match, suggest[1:])
    t.set("editorSuggestWidget.focusOutline", CLEAR)
    t.text("editorSuggestWidgetStatus.foreground", muted, suggest[:1])
    t.mark("editorSuggestWidget.selectedIconForeground", fg, suggest[1:])   # else cream, unreadable on the light row
    t.set("editorHoverWidget.background", surface)
    # VS Code draws a problem's source ("ts(2322)") in the hover's text color at 60% opacity. The
    # palette's black at 60% is 3.96:1 on the light widget surface; pure black reads, and at
    # full strength looks the same as the palette's black.
    hover_fg = "#000000" if v.name == "light" else fg
    hover_bgs = ["editorHoverWidget.background", "editorHoverWidget.statusBarBackground"]
    t.text("editorHoverWidget.foreground", hover_fg, hover_bgs)
    t.pairs.append(Pair(alpha(hover_fg, 0.6), tuple(hover_bgs), TEXT, "hover: a problem's source, at 60%"))
    t.set("editorHoverWidget.border", border)
    t.text("editorHoverWidget.highlightForeground", match, ["editorHoverWidget.background"])
    t.set("editorHoverWidget.statusBarBackground", rhino.composite(v.ui["hover"], surface))
    t.set("editorActionList.background", surface)
    t.text("editorActionList.foreground", fg, ["editorActionList.background"])
    t.set("editorActionList.focusBackground", sel)
    t.text("editorActionList.focusForeground", sel_fg, ["editorActionList.focusBackground"])

    t.set("editorMarkerNavigation.background", surface)
    for kind, color in (("Error", error), ("Warning", warning), ("Info", info)):
        t.set(f"editorMarkerNavigation{kind}.background", color)
        t.set(f"editorMarkerNavigation{kind}.headerBackground", alpha(color, 0.1))
    t.set("peekView.border", focus)
    for key in ("peekViewEditor.background", "peekViewEditorGutter.background", "peekViewEditorStickyScroll.background",
                "peekViewEditorStickyScrollGutter.background"):
        t.set(key, soft)
    results = ["peekViewResult.background", "peekViewResult.selectionBackground>peekViewResult.background",
               "peekViewResult.matchHighlightBackground>peekViewResult.background"]
    t.set("peekViewResult.background", surface)
    t.text("peekViewResult.fileForeground", fg, results)
    t.text("peekViewResult.lineForeground", fg, results)
    t.set("peekViewResult.matchHighlightBackground", wash_yellow)
    t.set("peekViewResult.selectionBackground", soft)
    t.text("peekViewResult.selectionForeground", fg, results[1:2])
    t.set("peekViewTitle.background", surface)
    t.text("peekViewTitleLabel.foreground", fg, ["peekViewTitle.background"])
    t.text("peekViewTitleDescription.foreground", muted, ["peekViewTitle.background"])
    t.set("debugExceptionWidget.background", surface)
    t.set("debugExceptionWidget.border", error)
    t.set("editorCommentsWidget.resolvedBorder", muted)
    t.set("editorCommentsWidget.unresolvedBorder", warning)
    t.set("editorCommentsWidget.replyInputBackground", raised)
    t.set("inlineChat.background", surface)
    t.text("inlineChat.foreground", fg, ["inlineChat.background"])
    t.set("inlineChat.border", border)
    t.set("inlineChat.shadow", shadow)
    t.set("inlineChatInput.background", raised)
    t.set("inlineChatInput.border", border)
    t.set("inlineChatInput.focusBorder", focus)
    t.text("inlineChatInput.placeholderForeground", muted, ["inlineChatInput.background"])

    # diff and merge chrome
    t.set("diffEditor.border", border)
    t.set("diffEditor.diagonalFill", alpha(border, 0.6))
    t.set("diffEditor.unchangedRegionShadow", shadow)
    t.set("diffEditor.move.border", alpha(muted[:7], 0.6))
    t.set("diffEditor.moveActive.border", focus)
    # Line numbers read at 4.5:1 on the background only, so the gutter isn't tinted
    t.set("diffEditorGutter.insertedLineBackground", CLEAR)
    t.set("diffEditorGutter.removedLineBackground", CLEAR)
    t.set("diffEditorOverview.insertedForeground", alpha(U("gutter_added"), 0.6))
    t.set("diffEditorOverview.removedForeground", alpha(U("gutter_deleted"), 0.6))
    t.set("multiDiffEditor.background", bg)
    t.set("multiDiffEditor.headerBackground", surface)
    t.set("multiDiffEditor.border", border)
    t.set("mergeEditor.conflict.handledFocused.border", focus)
    t.set("mergeEditor.conflict.handledUnfocused.border", border)
    t.set("mergeEditor.conflict.unhandledFocused.border", warning)
    t.set("mergeEditor.conflict.unhandledUnfocused.border", alpha(warning, 0.6))
    t.set("mergeEditor.conflict.handled.minimapOverViewRuler", alpha(U("gutter_added"), 0.6))
    t.set("mergeEditor.conflict.unhandled.minimapOverViewRuler", alpha(warning, 0.6))
    t.set("inlineEdit.originalBorder", CLEAR)
    t.set("inlineEdit.modifiedBorder", CLEAR)
    t.set("inlineEdit.tabWillAcceptOriginalBorder", U("gutter_deleted"))
    t.set("inlineEdit.tabWillAcceptModifiedBorder", U("gutter_added"))
    t.set("inlineEdit.gutterIndicator.background", bg)
    for kind, fill in (("primary", U("accent")), ("secondary", raised), ("successful", U("status_remote"))):
        t.fill(f"inlineEdit.gutterIndicator.{kind}Background", fill, f"inlineEdit.gutterIndicator.{kind}Foreground", v.on(fill)
               if kind == "successful" else (U("accent_foreground") if kind == "primary" else fg))
        t.set(f"inlineEdit.gutterIndicator.{kind}Border", CLEAR)


def rest(t: Theme, k: dict) -> None:
    """Terminal, debugging, testing, SCM, chat, notebooks, settings and the smaller views."""
    v, bg, fg, surface, raised, border = k["v"], k["bg"], k["fg"], k["surface"], k["raised"], k["border"]
    focus, muted, dim, soft, hover, link = k["focus"], k["muted"], k["dim"], k["soft"], k["hover"], k["link"]
    sel, sel_fg, error, warning, info, hint = k["sel"], k["sel_fg"], k["error"], k["warning"], k["info"], k["hint"]
    added, modified, deleted = k["added"], k["modified"], k["deleted"]
    wash_yellow = k["wash_yellow"]
    U, S, C = v.u, v.s, v.colors

    # --- terminal: the Ghostty palette. Find matches tint the background with the yellow
    # wash (every terminal color keeps its minimum on it) and are outlined, since VS Code
    # can't recolor the text in a terminal match.
    t.set(TERM, v.t("background"))
    terminal_bases = [TERM, f"terminal.selectionBackground>{TERM}", f"terminal.inactiveSelectionBackground>{TERM}",
                      f"terminal.findMatchBackground>{TERM}", f"terminal.findMatchHighlightBackground>{TERM}"]
    t.text("terminal.foreground", v.t("foreground"), terminal_bases)
    for i, name in enumerate(rhino.ANSI_NAMES):
        key = "terminal.ansi" + "".join(w.capitalize() for w in name.split("_"))
        minimum = rhino.TERMINAL_MIN.get(name)
        if minimum is None:
            t.set(key, v.t(name))             # black and bright white: exempt (see rhino.TERMINAL_MIN)
        else:
            t.text(key, v.t(name), terminal_bases, minimum)
    t.set("terminal.selectionBackground", v.t("selection"))
    t.set("terminal.inactiveSelectionBackground", alpha(v.t("selection"), 0.6))
    t.set("terminal.findMatchBackground", alpha(wash_yellow, 0.95))
    t.set("terminal.findMatchBorder", U("search_current"))
    t.set("terminal.findMatchHighlightBackground", alpha(wash_yellow, 0.6))
    t.set("terminal.findMatchHighlightBorder", U("search"))
    t.set("terminal.hoverHighlightBackground", alpha(v.t("selection"), 0.5))
    t.mark("terminalCursor.foreground", v.t("cursor"), terminal_bases)
    t.set("terminalCursor.background", v.on(v.t("cursor")))
    t.pairs.append(Pair("terminalCursor.background", ("terminalCursor.foreground",), TEXT, "terminalCursor.background"))
    t.set("terminal.border", border)
    t.set("terminal.dropBackground", alpha(sel, 0.25))
    t.set("terminal.tab.activeBorder", focus)
    t.text("terminal.initialHintForeground", muted, [TERM])
    t.mark("terminalCommandDecoration.defaultBackground", muted, [TERM])
    t.mark("terminalCommandDecoration.successBackground", added, [TERM])
    t.mark("terminalCommandDecoration.errorBackground", error, [TERM])
    t.set("terminalCommandGuide.foreground", border)
    t.set("terminalOverviewRuler.border", CLEAR)
    t.set("terminalOverviewRuler.cursorForeground", alpha(v.t("cursor"), 0.6))
    t.set("terminalOverviewRuler.findMatchForeground", alpha(U("search"), 0.6))
    t.set("terminalStickyScroll.background", bg)
    t.set("terminalStickyScroll.border", border)
    t.set("terminalStickyScrollHover.background", hover)

    # --- symbol icons (outline, breadcrumbs, suggestions): the syntax colors of what they stand for
    icon_bases = LISTS + ["editorSuggestWidget.background",
                          "editorSuggestWidget.selectedBackground>editorSuggestWidget.background", "breadcrumb.background"]
    kinds = {
        "type": ("class", "interface", "struct", "enumerator", "typeParameter", "module", "namespace", "package",
                 "file", "folder"),
        "keyword": ("keyword", "boolean", "null"),
        "number": ("number", "constant", "enumeratorMember", "unit"),
        "string": ("string",),
        "comment": ("snippet",),
        "decorator": ("event",),
        "text": ("array", "color", "constructor", "field", "function", "key", "method", "object", "operator",
                 "property", "reference", "variable"),
    }
    for role, names in kinds.items():
        for name in names:
            t.mark(f"symbolIcon.{name}Foreground", S(role), icon_bases)
    t.mark("symbolIcon.textForeground", muted, icon_bases)
    terminal_kinds = {
        "type": ("file", "folder", "symbolicLinkFile", "symbolicLinkFolder"),
        "keyword": ("flag", "option"),
        "string": ("optionValue", "argument"),
        "decorator": ("alias", "tag"),
        "number": ("commit", "stash"),
        "comment": ("branch", "pullRequest", "pullRequestDone"),
        "text": ("method", "remote"),
    }
    for role, names in terminal_kinds.items():
        for name in names:
            t.mark(f"terminalSymbolIcon.{name}Foreground", S(role), icon_bases)
    t.mark("terminalSymbolIcon.symbolText", S("text"), icon_bases)       # this one has no "Foreground"
    t.mark("terminalSymbolIcon.inlineSuggestionForeground", dim, icon_bases)

    # --- debugging --------------------------------------------------------------------------
    t.set("debugToolBar.background", surface)
    t.set("debugToolBar.border", border)
    debug_icons = {"breakpoint": error, "breakpointDisabled": dim, "breakpointUnverified": muted,
                   "breakpointCurrentStackframe": warning, "breakpointStackframe": added, "start": added,
                   "pause": info, "stop": error, "disconnect": error, "restart": added, "stepOver": info,
                   "stepInto": info, "stepOut": info, "stepBack": info, "continue": info}
    for name, color in debug_icons.items():
        t.mark(f"debugIcon.{name}Foreground", color, ["debugToolBar.background", GUTTER, f"{LINE}>{GUTTER}"])
    for name, color in (("info", fg), ("warning", warning), ("error", error), ("source", muted)):
        t.text(f"debugConsole.{name}Foreground", color, rows(PANEL))
    t.mark("debugConsoleInputIcon.foreground", focus, [PANEL])
    tokens = {"name": S("text"), "value": S("text"), "string": S("string"), "number": S("number"),
              "boolean": S("keyword"), "type": S("type"), "error": error}
    for name, color in tokens.items():
        t.text(f"debugTokenExpression.{name}", color, rows(SIDE) + [WIDGET])
    t.fill("debugView.exceptionLabelBackground", error, "debugView.exceptionLabelForeground", v.on(error))
    t.fill("debugView.stateLabelBackground", U("badge"), "debugView.stateLabelForeground", U("badge_foreground"))
    t.set("debugView.valueChangedHighlight", wash_yellow)

    # --- testing --------------------------------------------------------------------------------
    for name, color in (("Failed", error), ("Errored", error), ("Passed", added), ("Queued", warning),
                        ("Unset", muted), ("Skipped", muted)):
        t.mark(f"testing.icon{name}", color, rows(SIDE) + [GUTTER])
        t.set(f"testing.icon{name}.retired", alpha(color[:7], 0.6))
    t.mark("testing.runAction", added, [GUTTER, f"{LINE}>{GUTTER}"])
    t.set("testing.peekBorder", error)
    t.set("testing.peekHeaderBackground", alpha(error, 0.1))
    t.set("testing.messagePeekBorder", info)
    t.set("testing.messagePeekHeaderBackground", alpha(info, 0.1))
    t.fill("testing.message.error.badgeBackground", error, "testing.message.error.badgeForeground", v.on(error))
    t.set("testing.message.error.badgeBorder", CLEAR)
    t.text("testing.message.info.decorationForeground", muted, t.code)
    t.set("testing.coveredBorder", CLEAR)
    t.set("testing.uncoveredBorder", CLEAR)
    t.set("testing.coveredGutterBackground", U("gutter_added"))
    t.set("testing.uncoveredGutterBackground", U("gutter_deleted"))
    t.set("testing.coveredMinimapBackground", alpha(U("gutter_added"), 0.6))
    t.set("testing.uncoveredMinimapBackground", alpha(U("gutter_deleted"), 0.6))
    t.fill("testing.coverCountBadgeBackground", U("badge"), "testing.coverCountBadgeForeground", U("badge_foreground"))

    # --- source control graph ---------------------------------------------------------------
    lanes = [C["readable.blue_accent_1"], C["readable.purple"], C["readable.yellow"], C["readable.green"],
             C["readable.orange"]]
    for i, color in enumerate(lanes, 1):
        t.mark(f"scmGraph.foreground{i}", color, rows(SIDE))
    t.mark("scmGraph.historyItemRefColor", focus, rows(SIDE))
    t.mark("scmGraph.historyItemRemoteRefColor", C["readable.purple"], rows(SIDE))
    t.mark("scmGraph.historyItemBaseRefColor", C["readable.orange"], rows(SIDE))
    # Ref labels are filled with a ref color; text in the background color reads on every one,
    # since each readable color reads on the background.
    for ref in ("scmGraph.historyItemRefColor", "scmGraph.historyItemRemoteRefColor", "scmGraph.historyItemBaseRefColor"):
        t.pairs.append(Pair("scmGraph.historyItemHoverLabelForeground", (ref,), TEXT, f"label on {ref}"))
    t.set("scmGraph.historyItemHoverLabelForeground", bg)
    t.fill("scmGraph.historyItemHoverDefaultLabelBackground", U("badge"), "scmGraph.historyItemHoverDefaultLabelForeground",
           U("badge_foreground"))
    t.text("scmGraph.historyItemHoverAdditionsForeground", added, [WIDGET])
    t.text("scmGraph.historyItemHoverDeletionsForeground", deleted, [WIDGET])

    # --- chat and agents ------------------------------------------------------------------
    t.set("chat.requestBackground", CLEAR)
    t.set("chat.requestBubbleBackground", bg)
    t.set("chat.requestBubbleHoverBackground", rhino.composite(v.ui["hover"], bg))
    t.set("chat.requestBorder", border)
    t.set("chat.requestCodeBorder", border)
    for key in ("chat.requestBubbleBackground", "chat.requestBubbleHoverBackground"):
        for name, color in (("text", fg), ("link", link), ("code", S("inline_code")), ("muted", muted)):
            t.pairs.append(Pair(f"#{color.lstrip('#')}", (key,), TEXT, f"chat {name} on {key}"))
    t.fill("chat.slashCommandBackground", soft, "chat.slashCommandForeground", link)
    t.fill("chat.avatarBackground", raised, "chat.avatarForeground", fg)
    t.text("chat.editedFileForeground", modified, rows(SIDE))
    t.text("chat.linesAddedForeground", added, rows(SIDE))
    t.text("chat.linesRemovedForeground", deleted, rows(SIDE))
    t.text("chat.mcpCompatibilityWarningForeground", warning, rows(SIDE))
    t.set("chat.checkpointSeparator", border)
    t.set("chat.statusBackground", surface)
    t.set("chat.thinkingShimmer", alpha(fg, 0.5))
    for key, color in (("workingProgressStableIconForeground", focus), ("workingProgressInsidersIconForeground", focus),
                       ("inputWorkingBorderColor1", focus), ("inputWorkingBorderColor2", C["readable.purple"]),
                       ("inputWorkingBorderColor3", U("accent")), ("fusionBadgeGradientStart", C["readable.purple"]),
                       ("fusionBadgeGradientEnd", focus), ("voiceGlowBaseColor", alpha(focus, 0.4)),
                       ("voiceListeningGlow", alpha(focus, 0.5)), ("voiceSpeakingGlow", alpha(C["readable.purple"], 0.5)),
                       ("dictationActiveMicGlow", alpha(error, 0.5)),
                       ("sessionStateIndicator.inProgressBorder", focus),
                       ("sessionStateIndicator.needsInputBorder", warning),
                       ("sessionStateIndicator.unvisitedBorder", muted)):
        t.set(f"chat.{key}", color)
    t.set("interactive.activeCodeBorder", focus)
    t.set("interactive.inactiveCodeBorder", border)
    t.set("agents.background", bg)
    t.set("agentsDetail.background", surface)
    t.set("agentsPanel.background", surface)
    t.text("agentsPanel.foreground", fg, ["agentsPanel.background"])
    t.set("agentsPanel.border", border)
    t.set("agentsActivityBar.background", bg)
    t.set("agentsActivityBar.border", border)
    t.set("agentsCard.border", border)
    t.set("agentsBottomPanel.border", border)
    t.set("agentsGradient.tintColor", focus)
    t.set("agentsChatInput.background", raised)
    t.text("agentsChatInput.foreground", fg, ["agentsChatInput.background"])
    t.text("agentsChatInput.placeholderForeground", muted, ["agentsChatInput.background"])
    t.set("agentsChatInput.border", border)
    t.set("agentsChatInput.focusBorder", focus)
    for prefix in ("agentsBadge", "agentsUnreadBadge"):
        t.fill(f"{prefix}.background", sel, f"{prefix}.foreground", sel_fg)
    t.fill("agentsVoice.speakingBackground", sel, "agentsVoice.speakingForeground", sel_fg)
    t.set("agentsWindowGraphic.orange", C["orange"])
    t.set("agentStatusIndicator.background", U("accent"))
    t.text("agentSessionNeedsInput.foreground", warning, rows(SIDE))
    t.mark("agentSessionReadIndicator.foreground", muted, rows(SIDE))
    t.set("agentSessionSelectedBadge.border", focus)
    t.set("agentSessionSelectedUnfocusedBadge.border", border)
    t.set("agentFeedbackEditorWidget.background", surface)
    t.set("agentFeedbackEditorWidget.border", border)
    t.set("agentFeedbackInputWidget.border", border)
    t.set("activeSessionView.background", surface)
    t.text("activeSessionView.foreground", fg, ["activeSessionView.background"])
    t.set("inactiveSessionView.background", surface)
    t.text("inactiveSessionView.foreground", muted, ["inactiveSessionView.background"])

    # --- notebooks ------------------------------------------------------------------------
    t.set("notebook.editorBackground", bg)
    t.set("notebook.cellEditorBackground", surface)
    t.set("notebook.cellBorderColor", border)
    t.set("notebook.focusedCellBorder", focus)
    t.set("notebook.focusedEditorBorder", focus)
    t.set("notebook.inactiveFocusedCellBorder", border)
    t.set("notebook.inactiveSelectedCellBorder", border)
    t.set("notebook.selectedCellBackground", soft)
    t.set("notebook.selectedCellBorder", border)
    t.set("notebook.focusedCellBackground", CLEAR)
    t.set("notebook.cellHoverBackground", hover)
    t.set("notebook.outputContainerBackgroundColor", surface)
    t.set("notebook.outputContainerBorderColor", border)
    t.set("notebook.cellInsertionIndicator", focus)
    t.set("notebook.cellStatusBarItemHoverBackground", hover)
    t.set("notebook.cellToolbarSeparator", border)
    t.mark("notebookStatusSuccessIcon.foreground", added, [EDITOR, "notebook.cellEditorBackground"])
    t.mark("notebookStatusErrorIcon.foreground", error, [EDITOR, "notebook.cellEditorBackground"])
    t.mark("notebookStatusRunningIcon.foreground", info, [EDITOR, "notebook.cellEditorBackground"])
    t.set("notebookEditorOverviewRuler.runningCellForeground", added)

    # --- settings, welcome, extensions, problems ------------------------------------------
    t.text("settings.headerForeground", fg, [EDITOR])
    t.text("settings.settingsHeaderHoverForeground", fg, [EDITOR])
    t.set("settings.modifiedItemIndicator", focus)
    for name, bg_role in (("dropdown", raised), ("checkbox", raised), ("textInput", raised), ("numberInput", raised)):
        t.set(f"settings.{name}Background", bg_role)
        t.text(f"settings.{name}Foreground", fg, [f"settings.{name}Background"])
        t.set(f"settings.{name}Border", border)
    t.set("settings.dropdownListBorder", border)
    t.set("settings.focusedRowBackground", hover)
    t.set("settings.focusedRowBorder", focus)
    t.set("settings.rowHoverBackground", hover)
    t.set("settings.headerBorder", border)
    t.set("settings.sashBorder", border)
    t.set("welcomePage.background", bg)
    t.set("welcomePage.tileBackground", surface)
    t.set("welcomePage.tileHoverBackground", rhino.composite(v.ui["hover"], surface))
    t.set("welcomePage.tileBorder", border)
    t.set("welcomePage.progress.background", raised)
    t.set("welcomePage.progress.foreground", focus)
    t.set("walkThrough.embeddedEditorBackground", surface)
    t.text("walkthrough.stepTitle.foreground", fg, [EDITOR, "welcomePage.tileBackground"])
    t.text("textLink.foreground", link, [EDITOR, SIDE, WIDGET, NOTIFY, "editorHoverWidget.statusBarBackground"])
    t.text("textLink.activeForeground", link, [EDITOR, SIDE, WIDGET, NOTIFY])
    t.set("textPreformat.background", hover)
    t.text("textPreformat.foreground", S("inline_code"), [f"textPreformat.background>{b}" for b in (EDITOR, SIDE, WIDGET)])
    t.set("textPreformat.border", CLEAR)
    t.set("textBlockQuote.background", surface)
    t.set("textBlockQuote.border", border)
    t.set("textCodeBlock.background", bg)
    t.set("textSeparator.foreground", border)
    for kind, color in (("note", info), ("tip", added), ("important", C["readable.purple"]), ("warning", warning),
                        ("caution", error)):                       # Markdown alerts: > [!NOTE] and friends
        t.text(f"markdownAlert.{kind}.foreground", color, [EDITOR, SIDE, WIDGET])
    for name, color in (("star", warning), ("verified", link), ("preRelease", C["readable.orange"]),
                        ("sponsor", C["readable.purple"]), ("private", muted)):
        t.mark(f"extensionIcon.{name}Foreground", color, LISTS)
    t.mark("mcpIcon.starForeground", warning, LISTS)
    t.mark("ports.iconRunningProcessForeground", added, LISTS)
    for kind, color in (("Error", error), ("Warning", warning), ("Info", info)):
        t.mark(f"problems{kind}Icon.foreground", color, LISTS)
    t.text("search.resultsInfoForeground", muted, [SIDE])
    t.set("searchEditor.textInputBorder", border)
    t.mark("commentsView.resolvedIcon", muted, LISTS)
    t.mark("commentsView.unresolvedIcon", warning, LISTS)
    for name, color in (("red", error), ("blue", info), ("yellow", warning), ("orange", C["readable.orange"]),
                        ("green", added), ("purple", C["readable.purple"])):
        t.mark(f"charts.{name}", color, [EDITOR, SIDE, PANEL])
    t.text("charts.foreground", fg, [EDITOR, SIDE, PANEL])
    t.set("charts.lines", border)
    t.mark("chart.axis", muted, [EDITOR, SIDE, PANEL])
    t.set("chart.guide", border)
    t.mark("chart.line", focus, [EDITOR, SIDE, PANEL])
    for n, color in enumerate((C["readable.red"], C["readable.orange"], C["readable.yellow"], C["readable.green"],
                               C["readable.blue"], C["readable.purple"]), 1):
        t.set(f"confetti.color{n}", color)
    t.set("browser.border", border)
    t.set("profiles.sashBorder", border)
    t.set("simpleFindWidget.sashBorder", border)

    # --- overview ruler and minimap: marks beside the scrollbar --------------------------------
    ruler = {"border": CLEAR, "background": CLEAR, "errorForeground": error, "warningForeground": warning,
             "infoForeground": info, "addedForeground": alpha(U("gutter_added"), 0.6),
             "modifiedForeground": alpha(U("gutter_modified"), 0.6), "deletedForeground": alpha(U("gutter_deleted"), 0.6),
             "findMatchForeground": alpha(U("search"), 0.7), "rangeHighlightForeground": alpha(fg, 0.3),
             "selectionHighlightForeground": alpha(focus, 0.5), "wordHighlightForeground": alpha(fg, 0.3),
             "wordHighlightStrongForeground": alpha(focus, 0.5), "wordHighlightTextForeground": alpha(fg, 0.2),
             "bracketMatchForeground": alpha(fg, 0.5), "currentContentForeground": alpha(U("gutter_added"), 0.6),
             "incomingContentForeground": alpha(U("gutter_modified"), 0.6), "commonContentForeground": alpha(fg, 0.3),
             "commentForeground": alpha(fg, 0.4), "commentUnresolvedForeground": alpha(warning, 0.6),
             "commentDraftForeground": alpha(fg, 0.3), "inlineChatInserted": alpha(U("gutter_added"), 0.6),
             "inlineChatRemoved": alpha(U("gutter_deleted"), 0.6)}
    for key, value in ruler.items():
        t.set(f"editorOverviewRuler.{key}", value)
    t.set("editorMinimap.inlineChatInserted", alpha(U("gutter_added"), 0.6))
    minimap = {"background": bg, "findMatchHighlight": U("search"), "selectionHighlight": alpha(focus, 0.5),
               "selectionOccurrenceHighlight": alpha(fg, 0.3), "errorHighlight": error, "warningHighlight": warning,
               "infoHighlight": info, "chatEditHighlight": alpha(focus, 0.5)}
    for key, value in minimap.items():
        t.set(f"minimap.{key}", value)
    _ = hint


# --- syntax ---------------------------------------------------------------------------------

# TextMate scopes for each syntax role. VS Code applies the rule whose selector matches the
# innermost scope, so narrow scopes here override broad ones (keyword.operator over keyword).
SCOPES: dict[str, list[str]] = {
    "comment": ["comment", "punctuation.definition.comment"],
    "doc_comment": ["comment.block.documentation", "comment.block.javadoc", "comment.line.documentation",
                    "comment.line.triple-slash", "comment.line.double-slash.documentation", "comment.block.doc",
                    "string.quoted.docstring", "string.quoted.docstring punctuation.definition.string"],
    "doc_tag": ["storage.type.class.jsdoc", "punctuation.definition.block.tag.jsdoc", "keyword.other.phpdoc",
                "storage.type.class.doxygen", "keyword.other.documentation", "comment.block.documentation keyword",
                "comment.block.documentation storage.type", "keyword.other.doctag"],
    "todo": ["keyword.codetag.notation", "comment keyword.codetag", "keyword.other.todo"],
    "keyword": ["keyword", "keyword.control", "keyword.other", "storage", "storage.type", "storage.modifier",
                "constant.language", "keyword.operator.new", "keyword.operator.expression", "keyword.operator.delete",
                "keyword.operator.instanceof", "keyword.operator.typeof", "keyword.operator.void",
                "keyword.operator.logical.python", "keyword.operator.word", "keyword.operator.sizeof",
                "keyword.operator.alignof", "keyword.operator.wordlike", "meta.preprocessor", "keyword.control.directive",
                "punctuation.definition.directive", "constant.character.entity", "punctuation.definition.entity",
                "punctuation.definition.list.begin.markdown", "markup.list punctuation.definition.list"],
    "text": ["keyword.operator", "storage.type.function.arrow", "support.type.property-name", "meta.object-literal.key",
             "entity.name.tag", "entity.other.attribute-name", "variable.parameter", "string.regexp keyword.operator"],
    "string": ["string", "punctuation.definition.string", "support.constant.property-value", "support.constant.font-name",
               "support.constant.color", "constant.other.color", "storage.type.string.python",
               "meta.preprocessor.include string"],
    "string_escape": ["constant.character.escape", "constant.character.unicode", "constant.character.string.escape",
                      "string.regexp constant.character.escape"],
    "interpolation": ["punctuation.definition.template-expression", "punctuation.section.embedded",
                      "punctuation.definition.interpolation", "punctuation.section.interpolation",
                      "constant.character.format.placeholder", "constant.other.placeholder",
                      "punctuation.definition.placeholder"],
    "regex": ["string.regexp", "string.regexp keyword", "string.regexp keyword.control", "string.regexp constant.other",
              "string.regexp punctuation", "string.regexp punctuation.definition.string",
              "keyword.control.anchor.regexp", "keyword.other.back-reference.regexp"],
    "number": ["constant.numeric", "keyword.other.unit"],
    "constant": ["variable.other.constant", "variable.other.enummember", "constant.other.caps", "constant.other.enum",
                 "entity.name.constant", "constant.other.symbol"],
    "type": ["entity.name.type", "entity.name.class", "entity.other.inherited-class", "support.class",
             "entity.name.namespace", "entity.name.scope-resolution", "entity.name.package", "entity.name.type.module",
             "storage.type.cs", "storage.type.generic.cs", "storage.type.java", "storage.type.generic.java",
             "storage.type.object.array.java", "storage.type.groovy"],
    "builtin": ["support.function", "support.type", "support.variable", "support.constant", "variable.language",
                "support.type.primitive", "storage.type.primitive", "storage.type.built-in",
                "variable.parameter.function.language.special.self", "variable.parameter.function.language.special.cls",
                "variable.language.special.self", "storage.type.numeric.go", "storage.type.byte.go",
                "storage.type.boolean.go", "storage.type.string.go", "storage.type.uintptr.go", "storage.type.error.go",
                "storage.type.rune.go"],
    "decorator": ["entity.name.function.decorator", "punctuation.definition.decorator", "punctuation.decorator",
                  "meta.decorator entity.name.function", "meta.decorator variable.other", "storage.type.annotation",
                  "punctuation.definition.annotation", "entity.name.label", "entity.name.function.macro",
                  "entity.name.function.preprocessor", "meta.attribute.rust", "punctuation.definition.attribute.rust"],
    "invalid": ["invalid", "invalid.illegal", "invalid.deprecated"],
    "heading": ["markup.heading", "entity.name.section", "punctuation.definition.heading", "markup.heading entity.name"],
    "bold": ["markup.bold", "punctuation.definition.bold"],
    "italic": ["markup.italic", "punctuation.definition.italic"],
    "link_text": ["string.other.link.title", "string.other.link.description"],
    "link_url": ["markup.underline.link", "constant.other.reference.link"],
    "quote": ["markup.quote", "punctuation.definition.quote.begin.markdown"],
    "inline_code": ["markup.inline.raw", "markup.raw.block", "fenced_code.block.language",
                    "markup.fenced_code.block.markdown punctuation.definition.markdown"],
    "diff_header": ["meta.diff.header", "meta.diff.range", "meta.diff.index", "punctuation.definition.range.diff",
                    "markup.changed"],
    "diff_inserted": ["markup.inserted", "punctuation.definition.inserted"],
    "diff_deleted": ["markup.deleted", "punctuation.definition.deleted"],
}
EXTRA_STYLES = {"markup.strikethrough": "strikethrough", "markup.underline": "underline"}

# Semantic tokens whose meaning differs from the TextMate scope VS Code would map them to.
SEMANTIC = {
    "enumMember": "constant", "property.readonly": "constant", "variable.readonly": "text",
    "*.defaultLibrary": "builtin", "selfParameter": "builtin", "clsParameter": "builtin", "selfKeyword": "builtin",
    "builtinType": "builtin", "builtinConstant": "keyword", "escapeSequence": "string_escape",
    "formatSpecifier": "interpolation", "unresolvedReference": "invalid", "decorator": "decorator", "macro": "decorator",
    "label": "decorator", "typeParameter": "type", "namespace": "type",
}


def font_style(styles: tuple[str, ...]) -> str:
    return " ".join(s for s in ("italic", "bold", "underline", "strikethrough") if s in styles)


def token_colors(v: rhino.Variant) -> list[dict]:
    out = []
    for role, scopes in SCOPES.items():
        settings = {"foreground": v.s(role)}
        if style := font_style(v.styles.get(role, ())):
            settings["fontStyle"] = style
        out.append({"name": role, "scope": scopes, "settings": settings})
    out += [{"name": scope, "scope": scope, "settings": {"fontStyle": style}} for scope, style in EXTRA_STYLES.items()]
    return out


def semantic_colors(v: rhino.Variant) -> dict:
    out = {}
    for token, role in SEMANTIC.items():
        styles = v.styles.get(role, ())
        rule = {"foreground": v.s(role)}
        for s in ("italic", "bold", "underline", "strikethrough"):
            if s in styles:
                rule[s] = True
        if token == "variable.readonly":
            rule["italic"] = False      # const locals aren't named constants
        out[token] = rule
    return out


# --- checking -------------------------------------------------------------------------------

def unnecessary_opacity(t: Theme) -> float:
    """VS Code dims unused code with CSS opacity (blended on sRGB values). The lowest opacity,
    in steps of 0.01, that keeps every syntax color at 3:1 on every code surface."""
    surfaces = [t.flatten(s) for s in t.code]
    colors = {c.hex for c in t.v.syntax.values()}
    for i in range(30, 101):
        op = i / 100
        if all(rhino.contrast(rhino.composite(Color(c, op), s), s) >= DIM - 1e-9 for c in colors for s in surfaces):
            return op
    return 1.0


def add_code_pairs(t: Theme) -> None:
    """Every syntax color on every code surface, and dimmed unused code at 3:1."""
    seen = set()
    for role, color in t.v.syntax.items():
        if color.hex in seen:
            continue
        seen.add(color.hex)
        t.pairs.append(Pair(color.hex, tuple(t.code), TEXT, f"code: {role}"))


def check(t: Theme) -> tuple[list[str], list[tuple]]:
    errors, measured = [], []
    for pair in t.pairs:
        try:
            results = t.measure(pair)
        except (KeyError, ValueError) as e:
            errors.append(f"{t.v.name}: {pair.label}: {e}")
            continue
        worst = min(results)
        measured.append((worst[0], pair, worst))
        if worst[0] < pair.min - 1e-9:
            errors.append(f"{t.v.name}: {pair.label} {worst[1]} is {worst[0]:.2f}:1 on {worst[2]} ({worst[3]}), "
                          f"below {pair.min}")
    op = float(int(t.colors["editorUnnecessaryCode.opacity"][7:9], 16)) / 255
    for c in {c.hex for c in t.v.syntax.values()}:
        for stack in t.code:
            s = t.flatten(stack)
            r = rhino.contrast(rhino.composite(Color(c, op), s), s)
            if r < DIM - 0.02:            # the stored opacity is rounded to a byte
                errors.append(f"{t.v.name}: unused code {c} at opacity {op:.2f} is {r:.2f}:1 on {s} ({stack})")
    return errors, measured


# --- writing --------------------------------------------------------------------------------

def theme_json(v: rhino.Variant) -> tuple[dict, Theme]:
    t = workbench(v)
    # Ghost text (inline suggestions) is dim, at 3:1. Cursor also draws every editor's placeholder
    # in it, inputs included, so it's the text color at the least opacity that holds 3:1 on code
    # surfaces and on inputs.
    ghost_on = [t.flatten(s) for s in t.code] + [t.flatten("input.background")]
    t.text("editorGhostText.foreground", least_alpha(v.u("foreground"), ghost_on, DIM),
           t.code + ["input.background"], DIM)
    t.set("editorUnnecessaryCode.opacity", alpha("#000000", unnecessary_opacity(t)))
    add_code_pairs(t)
    label = f"Critical Rhino {v.name.capitalize()}"
    doc = {
        "$schema": "vscode://schemas/color-theme",
        "name": label,
        "type": v.name,
        "semanticHighlighting": True,
        "colors": dict(sorted(t.colors.items())),
        "tokenColors": token_colors(v),
        "semanticTokenColors": semantic_colors(v),
    }
    return doc, t


def package_json(variants: list[str], banner: str) -> dict:
    """The manifest. banner is the color behind the store listing's header: the dark background."""
    return {
        "name": NAME,
        "displayName": "Critical Rhino",
        "description": DESCRIPTION,
        "version": rhino.VERSION,
        "publisher": PUBLISHER,
        "license": "MIT",
        "icon": ICON,
        "galleryBanner": {"color": banner, "theme": "dark"},
        "homepage": rhino.SITE_URL,
        "repository": {"type": "git", "url": f"{rhino.REPO_URL}.git"},
        "bugs": {"url": f"{rhino.REPO_URL}/issues"},
        "engines": {"vscode": ENGINE},
        "categories": ["Themes"],
        "keywords": ["theme", "color-theme", "dark", "light"],
        "contributes": {
            "themes": [{"label": f"Critical Rhino {n.capitalize()}", "uiTheme": "vs-dark" if n == "dark" else "vs",
                        "path": f"./themes/critical-rhino-{n}-color-theme.json"} for n in variants],
        },
    }


def readme() -> str:
    """The store listing's page. Its pictures are the README's, from this version's tag: the
    stores need https image links, and each version's listing shows that version."""
    images = f"{rhino.REPO_URL.replace('github.com', 'raw.githubusercontent.com')}/v{rhino.VERSION}/docs/images"
    return f"""# Critical Rhino

A warm color theme in dark and light: cream text on near-black, or near-black on
cream, with orange keywords, red strings, green comments, purple numbers and blue
types. Every text color the theme sets is measured on every surface the editor draws
it on, at 4.5:1 for text and 3:1 for dim text and marks.

![Python in VS Code, dark]({images}/vscode-dark.png)

![Python in VS Code, light]({images}/vscode-light.png)

## Use it

Pick **Critical Rhino Dark** or **Critical Rhino Light** with **Preferences: Color
Theme**, or follow the desktop:

```jsonc
// settings.json
"window.autoDetectColorScheme": true,
"workbench.preferredDarkColorTheme": "Critical Rhino Dark",
"workbench.preferredLightColorTheme": "Critical Rhino Light"
```

VS Code lifts any terminal color below 4.5:1 as it draws
(`terminal.integrated.minimumContrastRatio`). If you turn that off to see the theme's
own terminal colors, also turn off `terminal.integrated.drawBoldTextInBrightColors`
for the light theme, where bright white is the background.

## The rest of the scheme

The same palette themes Neovim, Ghostty, starship, fzf, lazygit and tmux. The
[site]({rhino.SITE_URL}) shows every color and the role it plays, and the
[repository]({rhino.REPO_URL}) has the other themes, the design notes and the
[release notes]({rhino.REPO_URL}/releases).

Found a color that doesn't read? [Open an issue]({rhino.REPO_URL}/issues).
"""


def outputs(data: dict) -> tuple[dict[Path, str | bytes], list[str], dict[str, Theme]]:
    files, errors, themes = {}, [], {}
    names = rhino.appearances(data)
    for name in names:
        v = rhino.load_variant(data, name)
        doc, t = theme_json(v)
        e, _ = check(t)
        errors += e
        themes[name] = t
        files[THEME_DIR / f"critical-rhino-{name}-color-theme.json"] = json.dumps(doc, indent=2) + "\n"
    ink, accent, ground = logo.palette_colors(data, ICON_APPEARANCE)
    files[EXT_DIR / ICON] = logo.icon_png(ink, accent, ground)
    files[EXT_DIR / "package.json"] = json.dumps(package_json(names, ground), indent=2) + "\n"
    files[EXT_DIR / "README.md"] = readme()
    return files, errors, themes


def current(path: Path, content: str | bytes) -> bool:
    """Whether path already holds content. The icon is compared by its pixels."""
    if not path.exists():
        return False
    if isinstance(content, bytes):
        return logo.png_pixels(path.read_bytes()) == logo.png_pixels(content)
    return path.read_text(encoding="utf-8") == content


# What a .vsix's assets are, by the path vsce gives each file in the package.
ASSETS = {
    "extension/LICENSE.txt": "Microsoft.VisualStudio.Services.Content.License",
    "extension/README.md": "Microsoft.VisualStudio.Services.Content.Details",
}


def vsix_manifest(meta: dict, packaged: list[str]) -> str:
    """extension.vsixmanifest for a package.json, as vsce writes it. Editors read package.json
    itself; the stores build the listing from this: its links, banner, license, icon and README
    (packaged lists the package's files, which decide the assets)."""
    def prop(key: str, value: str) -> str:
        return f'      <Property Id="{key}" Value="{escape(value)}" />\n'

    # Without code to run, an extension also works in VS Code for the Web (vscode.dev), and the
    # Marketplace offers it there.
    web = "main" not in meta
    tags = meta.get("keywords", []) + (["__web_extension"] if web else [])
    props = [("Microsoft.VisualStudio.Code.Engine", meta["engines"]["vscode"]),
             ("Microsoft.VisualStudio.Code.ExtensionDependencies", ""),
             ("Microsoft.VisualStudio.Code.ExtensionPack", ""),
             ("Microsoft.VisualStudio.Code.ExtensionKind", "ui,workspace,web" if web else "workspace"),
             ("Microsoft.VisualStudio.Code.LocalizedLanguages", ""),
             ("Microsoft.VisualStudio.Code.EnabledApiProposals", "")]
    if repo := meta.get("repository", {}).get("url"):
        host = "GitHub" if repo.startswith("https://github.com/") else "Repository"
        props += [(f"Microsoft.VisualStudio.Services.Links.{k}", repo) for k in ("Source", "Getstarted", host)]
    if bugs := meta.get("bugs", {}).get("url"):
        props.append(("Microsoft.VisualStudio.Services.Links.Support", bugs))
    if homepage := meta.get("homepage"):
        props.append(("Microsoft.VisualStudio.Services.Links.Learn", homepage))
    if banner := meta.get("galleryBanner"):
        props += [("Microsoft.VisualStudio.Services.Branding.Color", banner["color"]),
                  ("Microsoft.VisualStudio.Services.Branding.Theme", banner["theme"])]
    props += [("Microsoft.VisualStudio.Services.GitHubFlavoredMarkdown", "true"),
              ("Microsoft.VisualStudio.Services.Content.Pricing", "Free")]
    assets = {"extension/package.json": "Microsoft.VisualStudio.Code.Manifest"}
    assets |= {path: kind for path, kind in ASSETS.items() if path in packaged}
    extra = ""
    if "extension/LICENSE.txt" in packaged:
        extra += "    <License>extension/LICENSE.txt</License>\n"
    if (icon := f"extension/{meta.get('icon')}") in packaged:
        assets[icon] = "Microsoft.VisualStudio.Services.Icons.Default"
        extra += f"    <Icon>{escape(icon)}</Icon>\n"
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" '
            'xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">\n'
            '  <Metadata>\n'
            f'    <Identity Language="en-US" Id="{escape(meta["name"])}" Version="{escape(meta["version"])}" '
            f'Publisher="{escape(meta["publisher"])}" />\n'
            f'    <DisplayName>{escape(meta.get("displayName", meta["name"]))}</DisplayName>\n'
            f'    <Description xml:space="preserve">{escape(meta.get("description", ""))}</Description>\n'
            f'    <Tags>{escape(",".join(tags))}</Tags>\n'
            f'    <Categories>{escape(",".join(meta.get("categories", [])))}</Categories>\n'
            '    <GalleryFlags>Public</GalleryFlags>\n'
            '    <Properties>\n' + "".join(prop(k, v) for k, v in props) + '    </Properties>\n'
            + extra +
            '  </Metadata>\n'
            '  <Installation>\n    <InstallationTarget Id="Microsoft.VisualStudio.Code" />\n  </Installation>\n'
            '  <Dependencies />\n'
            '  <Assets>\n'
            + "".join(f'    <Asset Type="{kind}" Path="{escape(path)}" Addressable="true" />\n'
                      for path, kind in assets.items()) +
            '  </Assets>\n'
            '</PackageManifest>\n')


CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension=".json" ContentType="application/json" />
  <Default Extension=".vsixmanifest" ContentType="text/xml" />
  <Default Extension=".md" ContentType="text/markdown" />
  <Default Extension=".txt" ContentType="text/plain" />
  <Default Extension=".png" ContentType="image/png" />
</Types>
"""


EDITORS = {"code": "VS Code", "cursor": "Cursor"}


def locate(cli: str) -> tuple[Path, Path] | None:
    """(launcher, binary) of an installed editor: the app the desktop opens (/usr/share/<cli>,
    /opt/<cli>) before whatever `cli` is first on PATH, which can be an older copy."""
    roots = [Path("/usr/share") / cli, Path("/opt") / cli]
    if found := shutil.which(cli):
        roots.append(Path(found).resolve().parent.parent)
    for root in roots:
        if (root / "bin" / cli).exists() and (root / cli).exists():
            return root / "bin" / cli, root / cli
    return None


def install(vsix: Path) -> int:
    """Install the .vsix into every editor found. Each keeps its own extensions."""
    found = 0
    for cli, name in EDITORS.items():
        if not (where := locate(cli)):
            print(f"{name}: not installed, skipped")
            continue
        found += 1
        r = subprocess.run([str(where[0]), "--install-extension", str(vsix), "--force"], capture_output=True, text=True,
                           check=False)
        print(f"{name}: " + ("installed" if r.returncode == 0 else f"failed: {(r.stderr or r.stdout).strip()}"))
    if found:
        print('Then pick "Critical Rhino Dark" or "Critical Rhino Light" with Preferences: Color Theme.')
    return 0 if found else 1


def vsix_path() -> Path:
    return rhino.ROOT / "build" / f"{NAME}-{rhino.VERSION}.vsix"


def package(files: dict[Path, str | bytes]) -> Path:
    """A .vsix (a zip with a manifest) that `code --install-extension` and `cursor --install-extension` take."""
    out = vsix_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    contents = {f"extension/{path.relative_to(EXT_DIR).as_posix()}": content for path, content in files.items()}
    contents["extension/LICENSE.txt"] = (rhino.ROOT / "LICENSE").read_bytes()
    manifest = vsix_manifest(json.loads(files[EXT_DIR / "package.json"]), list(contents))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("extension.vsixmanifest", manifest)
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        for name, content in contents.items():
            z.writestr(name, content)
    return out


def report(themes: dict[str, Theme]) -> None:
    for name, t in themes.items():
        _, measured = check(t)
        print(f"== {name}: {len(measured)} text colors and marks, measured on "
              f"{sum(len(p.on) for _, p, _ in measured)} backgrounds")
        for ratio, pair, (_, shown, bg, stack) in sorted(measured, key=lambda m: m[0] - m[1].min)[:40]:
            print(f"  {ratio:5.2f}:1 (min {pair.min})  {pair.label:52} {shown} on {bg}  [{stack}]")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the VS Code and Cursor themes from palette.toml.")
    parser.add_argument("--check", action="store_true", help="fail if generated files are stale or unreadable")
    parser.add_argument("--report", action="store_true", help="print the closest pairs for each variant")
    parser.add_argument("--package", action="store_true", help="also write the .vsix to build/")
    parser.add_argument("--install", action="store_true", help="package it and install it into VS Code and Cursor")
    args = parser.parse_args()
    files, errors, themes = outputs(rhino.load())
    if args.report:
        report(themes)
    stale = [p for p, content in files.items() if not current(p, content)]
    if args.check:
        errors += [f"{p.relative_to(rhino.ROOT)} is stale; run make vscode" for p in stale]
        for e in errors:
            print(f"- {e}")
        if errors:
            return 1
        counts = ", ".join(f"{n} {len(t.colors)} colors and {len(t.pairs)} pairs" for n, t in themes.items())
        print(f"OK: VS Code theme is current, and every text color reads on the backgrounds it's drawn on ({counts}).")
        return 0
    for e in errors:
        print(f"- {e}", file=sys.stderr)
    if errors:
        return 1
    for p in stale:
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content := files[p], bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
        print(f"wrote {p.relative_to(rhino.ROOT)}")
    if not stale:
        print("VS Code theme is up to date.")
    if args.package or args.install:
        vsix = package(files)
        print(f"wrote {vsix.relative_to(rhino.ROOT)}")
        if args.install:
            return install(vsix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
