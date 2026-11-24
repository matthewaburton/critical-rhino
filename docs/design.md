# Critical Rhino: design notes

How the scheme is built and why each choice was made. The [README](../README.md)
covers installing it.

- [Contrast](#contrast)
- [The palette](#the-palette)
- [Syntax](#syntax)
- [Readability](#readability)
- [Warm grays](#warm-grays)
- [Cyan](#cyan)
- [The light variant](#the-light-variant)
- [Neovim](#neovim)
- [Ghostty](#ghostty)
- [VS Code and Cursor](#vs-code-and-cursor)
- [Starship, fzf, lazygit and tmux](#starship-fzf-lazygit-and-tmux)
- [Adding a tool](#adding-a-tool)
- [Screenshots](#screenshots)
- [Logo](#logo)

## Contrast

Two minimums, from WCAG, apply everywhere:

- **4.5:1 for text**: code, interface text (file names, labels, links, line
  numbers, messages), text on colored fills such as the status bar, and the
  terminal's normal colors.
- **3:1 for what should recede but still be seen**: ghost text and shell
  suggestions, faint (SGR 2) terminal text, git gutter marks, focus rings, the
  cursor, and the terminal's bright colors in the light variant.

Decoration is exempt: borders, indent guides and split dividers. So are the
terminal's black and bright white, since in each variant one is the text color and
the other nearly the background.

A color is measured where it's drawn, on every surface it can land on, and a
translucent color is blended onto each surface first. Every theme in the repo has a
check that does this for its own color keys, and three audits run the real apps and
measure what they draw. A theme that fails its check isn't written.

## The palette

[`palette.toml`](../palette.toml) is the single source of truth. It has four parts:

- **The base palette**: 16 named colors, typed in once.
- **Derived colors**, each a rule applied to a base color (below).
- **Roles**: what each color is for. `[syntax]` and `[styles]` for code, `[ui]` for
  the editor around it, `[terminal]` for the 16 ANSI colors.
- **`[light]`**, which overrides only what changes in the light variant.

| Base color | Hex | Base color | Hex |
|---|---|---|---|
| Black | `#202020` | White (the cream) | `#F7EEDC` |
| Red | `#AE3C38` | Red (Muted) | `#AC5A58` |
| Orange | `#E86F41` | Yellow | `#FDD172` |
| Green | `#76A041` | Blue | `#59759A` |
| Purple | `#9C618A` | Loden | `#3D3A39` |
| Gray (Dark) | `#4D4D4D` | Gray (Light) | `#808080` |
| Gray Accent 1 | `#3C3F41` | Gray Accent 2 | `#2D2D2D` |
| Blue Accent 1 | `#2F65CA` | Blue Accent 2 | `#0D293E` |

### Derived colors are rules

Only the base palette and one cyan are typed in. Every other color is a rule applied
to a base color, stored next to the hex it produces:

```toml
red        = { rule = "text", from = "red", hue_shift = -6, hex = "#E66E72" }
gray_dark  = { rule = "tint", from = "gray_dark", by = "loden", hex = "#504C4B" }
bright_red = { rule = "bright", from = "readable.red", hex = "#FFA1A5" }
```

- **text** keeps the color's chroma and moves its lightness away from the background
  only until it reaches 4.5:1 on every surface code sits on. Colors that already
  pass come out unchanged. Every syntax role uses one of these `readable.*` colors,
  including the cream text, green and yellow. With `exact = true`, a color that
  already passes moves toward the background instead, so it lands on the minimum;
  the light bright colors use it to sit an even step lighter than the normals.
- **tint** keeps the lightness and takes the hue and tint strength of another color.
- **bright** is for the dark variant's bright terminal colors: +`#333333` per
  channel. It's close to perceptually even (+0.155 to +0.168 lightness), except for
  yellow, which runs into white.
- **fill** is for colors that carry text, like the status bar: it keeps the hue and
  chroma and moves lightness away from the text color only until that text reaches
  4.5:1 on the fill.
- **wash** is for light selections: an opaque color in the hue of `from`, as light
  as `from` would be at `alpha` over the background. A translucent blue over cream
  cancels to gray; the wash keeps that lightness and puts the blue back.

`make derive` rewrites the hex values after a rule changes, and `make palette:check`
fails if any are stale. Keeping the hex in the file means it stays readable and
diffs show real color changes. Rounding picks the nearest sRGB value in OKLab, which
keeps low-chroma grays on Loden's hue.

## Syntax

| Role | Color | Dark | Light |
|---|---|---|---|
| Keywords, including `true`, `false`, `null` and `None` | orange | `#E37523` | `#9F4200` |
| Strings | red | `#E66E72` | `#A7354A` |
| Comments and doc comments | green | `#76A041` | `#446902` |
| Numbers | purple | `#BD80AA` | `#844B73` |
| Types, built-ins, `self` and `cls`, string escapes | blue | `#7895BC` | `#456084` |
| Decorators, annotations and doc-comment tags | yellow | `#FDD172` | `#795901` |
| Everything else: names, functions, parameters, properties, operators | text | `#F7EEDC` | `#202020` |

Named constants (enum members, static and final fields) are text in italics, so
purple means a number. Regular expressions are text too.

Font styles are used sparingly. Comments stay upright, since the green already sets
them apart. Italics mark documentation and constants (doc comments, constants,
Markdown italics). Bold marks emphasis (TODO, Markdown headings and bold). Invalid
code is struck through and links are underlined.

## Readability

- **4.5:1 on every surface code sits on**: the background, the current line, the
  selection, inserted and removed diff lines, changed lines and the changed text in
  side-by-side diffs, the widget surface (hovers, peek views and the suggest widget
  show code on it) and highlighted references to the symbol under the cursor.
  `make palette:check` tests all nine. The diff-change surfaces are washes as light
  as the widget surface, so they move no text color: a blue wash for changed lines
  and a yellow one for the changed text, so it can't pass for a selection.
- **Interface text too.** `rhino.UI_TEXT_ROLES` lists the UI roles drawn as text
  (file names colored by git status, muted labels and placeholders, links, match
  highlights, error / warning / info, line numbers) and the surfaces each sits on:
  the background, the side bar, raised inputs, the current line or the soft
  selection. Git, error, info and hint colors use the readable tones; muted text is
  the text color at 59% (dark) or 70% (light), enough for placeholders in inputs and
  for inlay hints inside a selection; dimmed line numbers are yellow at 57% (dark)
  and ochre at 90% (light).
- **Dim text and marks at 3:1.** `rhino.UI_DIM_ROLES` covers what is meant to recede
  but still has to be seen: the dim gray for ghost text and backdrops (the terminal's
  bright black), and the git gutter marks, which are meaningful graphics. Gutter
  marks use the readable tones (the base red is 2.7:1); staged changes use the same
  tones at exactly 3:1, so they recede next to unstaged ones.
- **Search matches are fills.** All matches are black on the palette's yellow, the
  current one black on orange, the same in both variants, so a match never looks
  like a selection or a diff line.
- **Text on colored fills.** Every fill that carries text has a `*_foreground` role
  (`rhino.UI_FILLS`), and each pair must reach 4.5:1. The status bar blue, the
  no-folder purple and the list filter box's Red (Muted) are `fill` versions,
  darkened just enough for cream text (`#59759A` becomes `#536E93`); the orange
  debugging and green remote bars take black text instead. Matched letters in the
  selected list row are cream like the rest of the row: the link blue would be under
  2:1 on Blue Accent 1, and bold sets them apart.
- **Readable tones.** Red, purple, blue and Blue Accent 1 are too dark to read as
  text on `#202020`. Each has a `readable.*` version with the same OKLCH chroma,
  lightened only until it reaches 4.5:1 on all of those surfaces. The base colors
  are unchanged; they still make the tints, fills and surfaces.
- **Strings and keywords are nudged apart.** Once red is light enough to read, it
  matches orange in lightness and saturation (only 0.042 apart in OKLab). Red moves
  toward rose (`#E66E72`, hue 26° to 20°) and keyword orange toward amber
  (`#E37523`, 41° to 52°), which doubles the gap to 0.086. The check fails if any two
  distinct syntax colors are closer than 0.08.
- **The current line is Loden at 40%** (`#2C2A2A`). Full Loden would drop the
  readable colors to about 3.1:1 on the line being edited.
- **The selection is Blue Accent 2** (`#0D293E`), a navy that keeps text at 4.7:1
  and shows the selection through its hue. A translucent blue would drop text to
  3.1:1.
- **Diff lines are faint tints** (green 8%, red 18%) so text keeps 4.5:1 on them.
- **The terminal is readable too.** Most programs print in the normal ANSI colors
  (git deletions and errors in red, `ls` folders in blue), so the six colored
  normals reach 4.5:1, using the editor's readable tones (the base red, blue and
  purple are 2.72:1, 3.44:1 and 3.48:1). Bright colors are built from the normals
  with the bright rule so they stay lighter, which makes bright red a pastel
  `#FFA1A5`. Colored normals must reach 4.5:1 and colored brights 3:1, on the
  terminal background and inside a selection, since selected text keeps its colors.
  That's why the dark terminal selection is the editor's navy: cream at 25% would
  drop colored text to about 2.1:1. White is secondary text, at 4.5:1; bright black
  is the dim gray shells use for suggestions and timestamps, at 3:1.
- **The cursor stands out at 3:1** against everything it's drawn over: the terminal,
  its selection, and every surface an editor draws under it (current line,
  selection, diff lines, popups). The character under a block cursor is text, so
  4.5:1. Blue Accent 1 is 2.98:1 on the dark background, so dark uses the text blue
  (`#7895BC`) with black under it. Light uses Blue Accent 1 (`#2F65CA`, 3.8:1 or
  more everywhere) with cream under it.

The lists in `rhino.py` are written by hand and cover only the pairings they name.
That's why every theme also checks its own pairings, from the tool's own color keys
(see [Adding a tool](#adding-a-tool)).

## Warm grays

The base palette's grays mix temperatures: neutral (`#2D2D2D`, `#4D4D4D`,
`#808080`), cool (`#3C3F41`) and warm (Loden, and the cream text). `[colors.warm]`
holds warm versions of the neutral grays: the same lightness, tinted with Loden's
hue at Loden's tint strength (chroma divided by lightness). UI surfaces, borders,
muted text and the terminal grays use them, and raised surfaces use Loden itself.
The editor background is Black.

## Cyan

The base palette has no cyan, and terminals need one. The derived cyan, `#13978A`,
comes from the palette itself:

1. **Hue:** scan the cyan band between green (129°) and blue (256°) and take the
   hue most distinct from both (184°, about 0.12 from each).
2. **Chroma:** read off the palette's own pattern. Its warm accents are more
   saturated than its cool ones, so chroma is interpolated around the hue circle
   between green and blue.
3. **Lightness:** just enough for 4.5:1 on the background, in the same band as
   green and blue.

Like the other accents, cyan has a text tone for each variant: `#2AA497` in dark,
lightened slightly so it also reads on the navy selection and widgets, and
`#016A60` in light. Bright cyan follows the bright rule: `#5DD7CA`.

## The light variant

The `[light]` section overrides only what changes; syntax roles and font styles are
shared, so both variants mean the same thing. The check requires `[light]` to define
every UI and terminal role, and `rhino.view(data, "light")` gives the merged palette.

- **Background and text:** the cream (`#F7EEDC`) and Black (`#202020`).
- **Readable tones:** the same text rule, which darkens against a light background:
  crimson strings `#A7354A`, rust keywords `#9F4200`, ochre decorators `#795901`,
  olive comments `#446902`, slate types `#456084`, plum numbers `#844B73`.
- **Different nudges.** Darkening squeezes red, orange and yellow together. Dark's
  nudges would push orange into the darkened yellow (0.069 apart), so light uses red
  −12° and orange +6°, the smallest nudges that keep every pair 0.08 apart. The warm
  trio can't get further apart than 0.084 within the 12° limit.
- **Surfaces** are Loden tints over the cream (side bar 7%, raised 13%, current line
  8%), a soft gray-beige that matches the dark variant's Loden. Line numbers are the
  readable ochre at 90% (the least that reaches 4.5:1), so the current line's number
  is black to stand out.
- **Selections are blue washes.** Blue Accent 1 at 16% over the cream comes out a
  flat gray (`#D7D8D9`). The wash rule keeps that lightness in Blue Accent 1's hue:
  `#CBD9F0` for selections (editor and terminal) and `#D7DFEC`, the lightness of
  12%, for inactive ones. Because the lightness is unchanged, no text color moves.
- **Terminal:** normal colors are the readable set (about 5.6:1). Bright colors are
  the base colors at exactly 3:1 (`exact = true`), so each sits the same step
  lighter than its normal, as in dark. White (`#625D5B`, 5.6:1) and bright black
  (`#7E7977`, 3.7:1) follow the same gray rule as dark.

## Neovim

- **Generated, then committed.** `make nvim` writes `lua/critical-rhino/{dark,light}.lua`
  from the palette: 435 highlight groups, the 16 terminal colors for `:terminal`,
  and a lualine theme (lualine's `auto` theme picks it up from the scheme's name).
  The files are committed, so installing needs no Python.
- **Every group says where it's drawn.** In `build_nvim.py` each group that sets a
  text color is tagged: on every code surface, in floats and menus (and their
  selected row), as a gutter mark, on its own background, or decoration.
  `make nvim:check` measures each against those backgrounds (4.5:1 for text, 3:1 for
  dim text and marks) and fails if the generated files are stale.
- **The audit covers what plugins add.** Plugins define about 1,250 more groups,
  mostly as links to standard ones, some with colors of their own. `make nvim:audit`
  starts the Neovim config on your machine headless with this checkout's theme loaded
  (from its files, since lazy.nvim finds a plugin's modules in the folder your config
  names), dumps every group as it finally resolves, and measures each where it's
  drawn. The theme sets the
  groups it found wanting: bufferline drawing inactive tabs in the Comment color
  (green, so 4.2:1 in light); gitsigns dimming staged marks to 1.6:1; mason's
  hard-coded colors; plugins reusing the line-number color as text in floats; the
  picker's selected row using the current-line color, which is darker than the
  picker itself.
- **Mapping choices.** Syntax follows the palette roles: tree-sitter captures and
  semantic tokens map onto them. For plugins, the classic `Constant` group is the
  number purple, since plugins use it for keys and counts; `Title` is orange bold
  (float titles, the dashboard banner); `NonText` is the dim gray; secondary text
  that plugins draw in `Comment` (descriptions, sources, counts) is muted instead of
  green. Mode colors in lualine reuse the status fills: normal blue, insert green,
  visual purple, replace Red (Muted), command orange, terminal cyan.
- **Search matches inside a selection.** Neovim draws a selection over a match by
  keeping the match's text color and taking the selection's background, so dark's
  black-on-yellow match would turn black on navy, 1.09:1. In dark, matches are drawn
  in reverse video instead, with the fill as the color: outside a selection they look
  the same, and inside one the fill stays and the text takes the selection's navy.
  The build picks the form from the numbers and measures the match on the selection
  either way. Light's black text reads on its pale selection, so light keeps plain
  matches.
- **bufferline** paints file icons on its own shade of the background, which a color
  scheme can't reach. `require("critical-rhino").bufferline` hands it the theme's tab
  colors, and returns nothing when another scheme is active.
- **Not the theme's.** In the light variant, snacks.nvim dims the editor behind
  pickers with 60% black. That's snacks' own `backdrop` option, set each time a
  window opens, so a color scheme can't change it.

## Ghostty

- **What it sets.** The background, foreground and 16 ANSI colors from the terminal
  roles; the selection, keeping each character's own color
  (`selection-foreground = cell-foreground`, as in Neovim's visual mode); the cursor
  and the text under it; the search highlights, the same as in the editors; the
  split divider; and the two options that change how readable text is,
  `bold-color` and `faint-opacity`.
- **Every pair is measured as Ghostty draws it.** `make ghostty:check` reads back the
  generated files and measures the foreground and ANSI colors on the background and
  the selection; the cursor against the background, the selection and every surface
  an editor draws under it, and the character under it; text on both search
  highlights; and faint text and text in unfocused splits (Ghostty dims those to
  70%), at 3:1. It fails if a file is stale, and has Ghostty itself validate both.
- **Bold.** `bold-color = bright` draws bold text in the bright colors. Dark sets it,
  since its brights are 7.8:1 and up. Light doesn't: its brights are 3:1 accents, so
  bold prompts, `ls` folder names and git branch names would drop to 3.7:1, and bold
  bright white would match the background. The build decides from the numbers: a
  variant gets it only if every bright reads at its normal color's minimum.
- **Faint.** Programs dim text with SGR 2 (starship's `dimmed` styles, many tools'
  secondary text). Ghostty draws it at `faint-opacity`, and how that blends depends
  on the platform: its text shader mixes in linear light on Linux
  (`linear-corrected` only corrects glyph edges) and on sRGB values on macOS
  (`native`). Linear mixing pulls dark text toward a light background much harder:
  at Ghostty's default 50%, faint colored text in light is 1.7:1. The theme sets the
  lowest opacity, in steps of 0.05, that keeps every text color at 3:1 under both:
  0.65 in dark (decided by sRGB) and 0.85 in light (decided by linear). Faint still
  reads as faint: in light the faint text color is 4.7:1 against 14.1:1.
- **`make term:test`**, run in Ghostty outside tmux (which answers color queries
  itself), asks Ghostty for its background, foreground, cursor and 16 palette colors,
  says which variant it's showing and names any color that doesn't match the theme.
  Then it prints each color normal, bold, faint, italic and underlined, as
  backgrounds, the 256-color cube, and what to check by hand: the selection, search,
  the cursor and splits.
- **Not the theme's.** Programs that choose their own colors keep them: 256-color and
  true-color codes, and text on an ANSI color used as a background. The starship,
  lazygit and tmux themes fix the cases in those tools. `palette-generate` stays off,
  as Ghostty ships it, because programs that use 256 colors expect the xterm values.

## VS Code and Cursor

- **What it sets.** 977 workbench colors, nearly every one VS Code and Cursor
  register, so none of VS Code's own palette leaks through; TextMate scopes for every
  syntax role; semantic tokens where their meaning differs from the scope VS Code
  would pick (enum members and readonly properties are constants, standard-library
  names are built-ins, `self` and `cls` too); and the terminal's 16 colors. Brackets
  are plain text, as everywhere in the scheme, so bracket-pair colorization draws
  them all in the text color.
- **Every pair is measured where VS Code draws it.** Each of 373 text colors and
  marks names the backgrounds it lands on, as stacks:
  `list.hoverBackground>sideBar.background` is the hover color laid over the side
  bar. Translucent keys are composited down the stack as the workbench draws them.
  Every syntax color is measured on every surface code sits on: the editor, the
  current line, both selections, the peek editor, notebooks, output and code blocks,
  and each decoration VS Code draws under text (find, word and selection highlights,
  diff, merge, coverage, debugging), over the editor, the current line and the
  selection. `make vscode:check` measures about 7,750 backgrounds per variant and
  fails if a file is stale.
- **Decorations stack, so they're drawn in surfaces.** VS Code draws its highlights
  translucent and on top of the current line and the selection. Readable colors sit
  close to 4.5:1 on the lightest code surface, so a light tint stacked on the current
  line (green at 8%, say) would drop code to 4.1:1. Every decoration is drawn instead
  in a color that is itself a code surface: the word-highlight gray, the selection's
  navy, the yellow wash, or, for diffs, merges and coverage, the most saturated
  color of each hue that keeps every syntax color at 4.5:1, also mixed into the
  background, current line and selection at any strength. (Browsers mix translucent
  colors on sRGB values, which makes a mix of two hues darker than either: harmless
  in dark, but in light, yellow half over the blue selection would dip to 4.43:1.)
  In dark these are deep greens, reds and blues no lighter than the side bar; in
  light, clear limes, pinks and sky blues.
- **Diffs keep the selection visible.** VS Code draws diff highlights over the
  selection, so a solid tint would hide it. Diff lines are drawn at half strength,
  so the selection shows through at about half its usual strength. In dark that's
  faint, since the selection is navy on near-black and stands out by hue.
- **Find matches** are black on yellow, the current one black on orange, as in
  Neovim and Ghostty. In the terminal, where VS Code can't recolor the text, matches
  are outlined in yellow and orange over the yellow wash, which every terminal color
  reads on.
- **Hover darkens.** A list row or tab under the pointer goes darker in dark and
  lighter in light (`hover` in the palette), since file names in git colors sit at
  4.5:1 on the side bar and a lighter hover would drop them below it.
- **The panel shares the side bar's surface**, with the terminal on the editor's
  background inside it.
- **Dim text is translucent.** Ghost text (inline suggestions), disabled controls and
  unused code are the text color at the least opacity that keeps 3:1 on everything
  they're drawn over: 41% in dark and 52% in light. Cursor also draws every editor's
  placeholder in the ghost-text color, inputs included, so that's measured too.
- **The terminal.** VS Code draws bold text in the bright colors
  (`terminal.integrated.drawBoldTextInBrightColors`, on by default), which in light
  would put bold text at the brights' 3:1. VS Code's other default,
  `terminal.integrated.minimumContrastRatio` at 4.5, lifts any terminal color below
  4.5:1 back up as it draws, so with the defaults nothing in the terminal reads
  below 4.5:1. That also lifts bright black, the dim gray shells use for
  suggestions, so it looks less dim than in Ghostty. Setting the minimum to 1 shows
  the theme's own colors, but then turn `drawBoldTextInBrightColors` off for the
  light theme too: there bright white is the background color, so bold white text
  would vanish. The terminal draws on a canvas, so the audit can't measure its text;
  `make vscode:check` measures every terminal color on the background, both
  selections and the find highlights.
- **`make vscode:audit` measures what the editors really draw.** It starts VS Code
  and Cursor headless with the theme, in throwaway profiles, opens a sample
  repository with a change of every kind and steps through ten scenes: the explorer,
  a diff with a selection in it, find, a merge conflict, suggestions, a hover, the
  command palette, the terminal, Markdown and settings. In each it measures every
  visible run of text and every icon against the pixels behind it, and saves
  screenshots to `build/vscode-audit/`. This catches colors the workbench sets in
  its own stylesheets: VS Code draws a problem's source in a hover ("ts(2322)") at
  60% of the hover's text color, which in light needs pure black there instead of
  the palette's.
- **Not the theme's.** Cursor draws some of its own text at fixed fractions of the
  editor's text color: 74%, 62% and 36% for secondary, tertiary and quaternary text,
  and key hints at 50%. In dark these read; in light the 62% tier is 3.96:1 and the
  key hints 2.9:1, and no theme color can lift them. Its "Cursor Tab" status item is
  dimmed while the feature is off. The audit lists these but doesn't fail on them.
- **The store listing comes from the package.** The Marketplace and Open VSX build the
  extension's page from the `.vsix`: its icon (the logo's dark tile, see
  [Logo](#logo)), a banner in the dark background, and `extras/vscode/README.md`,
  which shows the README's VS Code screenshots from the release's tag, so each
  version's listing shows that version.

## Starship, fzf, lazygit and tmux

These tools run in the terminal and print mostly in its 16 colors, which the
terminal switches with the desktop. So each theme changes only what doesn't follow
along or doesn't read, and every color it sets reads in dark and light alike. Their
checks measure every pair the way Ghostty draws it: bold in the bright colors where
the theme sets `bold-color = bright`, faint text at the theme's faint opacity under
both blends, reversed text with its colors swapped.

- **Starship** draws its prompt in the 16 colors, except for 13 modules with fixed
  colors (the package version in xterm 208 is 2.1:1 on the light background; C and
  C++ in 149; Gleam, Quarto and Typst in hex) and three in bright colors, which in
  light are 3:1 accents. The theme moves each to the nearest of the normal colors,
  bold as before, so in dark nothing changes: bold already draws the bright color
  there. `make starship:check` reads every style in the installed starship's
  defaults (`starship print-config --default`) with the theme's on top, so a module a
  later starship adds with a fixed color fails the check. Its `dimmed` styles are
  drawn faint, which Ghostty keeps at 3:1.
- **fzf** can't tell a dark terminal from a light one, and its default colors are
  fixed 256-color ones meant for dark (in light its counter, header and matches are
  about 2:1). The theme uses only the 16 colors. The current line is the editors'
  blue band, drawn in reverse video so it reads in both, though only as wide as the
  text; matched characters are blue, and bold on the band, as in the editors.
- **lazygit** asks the terminal for its background color to choose dark or light
  defaults. Inside tmux the answer can be stale: tmux 3.4 answers from a copy it
  takes when the client attaches, so after the desktop switches, lazygit guesses
  wrong until you reattach. So the theme doesn't depend on the guess. The selected
  line is the editors' focused row, cream on the selection blue, with every span's
  color replaced by cream (lazygit's default band is the ANSI blue, which puts its
  own colored text at 1.3 to 2.7:1 in dark); in unfocused panels it's the same blue
  with its color taken out. Copied commits are a cyan pill, which reads in both.
  Authors and the graph are all one gray, since lazygit's own author colors are
  24-bit colors chosen for whichever background it guessed. The focused panel's
  frame is blue, as the editors' focus is, and other frames are gray. The build fails
  if the selection colors ever differ between variants.
- **tmux** draws its status bar, borders, messages and copy-mode highlights in colors
  its options set, and its defaults put black text on green, yellow, cyan or
  magenta: fine in dark, but about 2.5:1 in light, where black is the text color.
  The theme sets each of those options to one of the 16 colors or the terminal's
  own. Highlights are reverse video: a palette color as the block, with text in the
  terminal's background color, so each reads at the color's own contrast in both
  variants. Pane titles are drawn in the border colors, so borders are held to 4.5:1
  too, and they turn red while input goes to every pane. `make tmux:check` also has
  tmux load the file in a private server and confirms every option took.
- **`make terminal:audit` measures what starship, fzf and lazygit really draw.** It
  runs each in a private tmux server with a throwaway HOME and a made-up repository:
  starship prompts in a dirty repository with a failed command, fzf filtering a file
  list with two files marked, lazygit's files, commits (one copied), branches, a menu
  and a search, in each variant, and a shell session: `ls`, `git` and `grep` under
  starship prompts, then every text color. It captures the pane with its colors and
  measures every character as Ghostty draws it, at 4.5:1 (3:1 for faint text, line
  drawing, and bright colors a program asks for by number). `--defaults` runs the
  same scenes with each tool's own colors, for comparison, and `--shots` saves each
  scene to `build/terminal-audit/`.
- **Not the theme's.** lazygit hard-codes two things no theme key reaches: search
  matches in ANSI black on yellow and on cyan (in light, where those are text
  colors, 2.5:1; the current match on cyan is 4.3:1 in dark), and the selected
  commit's graph line in bright white, which in light is the background color, so
  that line vanishes. The audit lists them but doesn't fail on them.

## Adding a tool

Each theme has a builder in `scripts/build_<tool>.py` that reads the palette through
`scripts/rhino.py` (`rhino.view(data, "dark")` or `rhino.view(data, "light")`), maps
the roles onto the tool's format, and writes into `extras/<tool>/`. A new one
should:

- **Check its own pairings**, derived from the tool's own color keys: every
  foreground key and each background it's drawn on (in VS Code, for example,
  `list.highlightForeground` on the list background and `list.focusHighlightForeground`
  on `list.activeSelectionBackground`). Each pair is held to the same minimums, and
  any role a pairing needs is added to the palette.
- **Refuse to write** a theme that fails, and fail `--check` when the committed file is
  stale.
- **Get two make targets**, `<tool>` and `<tool>:check`, with the check added to
  `make check`, and a line in `.gitattributes` marking its output as generated.

Tools drawn in the terminal can reuse `build_ghostty.Terminal`, which draws a cell
the way Ghostty does, and `build_ghostty.tool_main`, which the starship, fzf, lazygit
and tmux builders share.

## Screenshots

`make screenshots` retakes the README's pictures, which the site shows too, from the
current themes, so they can't drift from the palette. The terminal and lazygit pictures are the terminal
audit's shell and commits scenes, and the VS Code pictures are its audit's explorer
scene; each is copied only if its audit passes. The Neovim pictures come from the
Neovim config on your machine, with this checkout's theme, editing the same sample
file the VS Code pictures show. Terminal captures are drawn as Ghostty would draw
them, in Source Code Pro with Nerd Font symbols for icons, each glyph in a box as
wide as its cells so the columns line up.

## Logo

A CR monogram over a bar of the palette's orange. The letters are outlines of
Archivo (SIL Open Font License) at width 125 and weight 850, so the logo needs no
font to render. The ink is each variant's text color. Icon tiles put the letters at
80% of the width so they still read at 16 px. `make logo` draws it into
`docs/images/`.

The VS Code extension's icon, `extras/vscode/icon.png`, is the dark tile at 256 px:
cream on near-black on its own ground, so it holds on the stores' light and dark
pages, and they refuse SVG icons. `make vscode` rasterizes the same outlines in plain
arithmetic, 16 samples down each pixel with exact coverage across it, so every
machine draws the same pixels; `make vscode:check` compares them.
