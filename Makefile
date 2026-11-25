# critical-rhino -- Run 'make' or 'make help' to see available commands
#
# palette.toml is the source of truth. The scripts in scripts/ derive, check and
# build from it, using only the standard library. The themes are generated into
# the repo (lua/ for Neovim, extras/ for everything else) and so is the logo
# (docs/images/); everything else, the site included, goes to build/.

.DEFAULT_GOAL := help

PYTHON ?= python3
BIN    := ./bin
RUFF   := $(abspath $(BIN))/ruff

# The oldest Python the scripts run on: tomllib arrived in 3.11.
PYTHON_MIN := 3.11
# Pinned ruff release for reproducible lint runs, installed with uv.
RUFF_VERSION ?= 0.17.4

# The git ref site:compare shows changes against.
REF ?= main

.PHONY: help \
        setup doctor versions clean \
        derive \
        build site site\:compare logo nvim ghostty vscode vscode\:package vscode\:install \
        starship fzf lazygit tmux claude-code screenshots \
        palette\:check logo\:check nvim\:check nvim\:audit ghostty\:check vscode\:check vscode\:audit \
        starship\:check fzf\:check lazygit\:check tmux\:check claude-code\:check terminal\:audit claude-code\:audit \
        term\:test lint check

#------------------------------------------------------------------------------
# Help
#------------------------------------------------------------------------------

help: ## Show available commands
	@awk '\
		/^#-+$$/ { divider = 1; next } \
		/^# [A-Za-z]/ && divider { section = substr($$0, 3); divider = 0; next } \
		{ divider = 0 } \
		/^[a-zA-Z_:\\-]+:.*## / { \
			match($$0, /^([a-zA-Z_-]|\\:)+:/); \
			target = substr($$0, 1, RLENGTH - 1); \
			gsub(/\\:/, ":", target); \
			match($$0, /## /); \
			desc = substr($$0, RSTART + 3); \
			targets[section] = targets[section] sprintf("  \033[36m%-22s\033[0m %s\n", target, desc); \
			order[section] = order[section] ? order[section] : ++count; \
		} \
		END { \
			for (i = 1; i <= count; i++) { \
				for (s in order) { \
					if (order[s] == i) { \
						if (i > 1) printf "\n"; \
						printf "\033[1m%s\033[0m\n", s; \
						printf "%s", targets[s]; \
					} \
				} \
			} \
		}' $(MAKEFILE_LIST)

#------------------------------------------------------------------------------
# Setup
#------------------------------------------------------------------------------

setup: ## Install pinned ruff into ./bin (needs uv)
	@command -v uv >/dev/null || (echo "Install uv first: https://docs.astral.sh/uv/" && exit 1)
	UV_TOOL_DIR=$(abspath $(BIN))/tools UV_TOOL_BIN_DIR=$(abspath $(BIN)) uv tool install --quiet --force ruff==$(RUFF_VERSION)

doctor: ## Check Python is new enough, uv and ruff are at their pins, and the tools the targets build for
	@fail=0; \
	ok()  { printf '  \033[32mok\033[0m    %s\n' "$$1"; }; \
	bad() { printf '  \033[31mFAIL\033[0m  %s\n        -> %s\n' "$$1" "$$2"; fail=1; }; \
	v=$$($(PYTHON) -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null); \
	if [ -n "$$v" ] && $(PYTHON) -c 'import sys; sys.exit(sys.version_info < tuple(map(int, "$(PYTHON_MIN)".split("."))))'; then ok "Python $$v"; \
	else bad "Python $${v:-missing} (want $(PYTHON_MIN) or later)" "install a newer Python, or run make with PYTHON=/path/to/python3.11+"; fi; \
	v=$$(uv --version 2>/dev/null | awk '{print $$2}'); \
	if [ -n "$$v" ]; then ok "uv $$v"; \
	else bad "uv missing" "install uv: https://docs.astral.sh/uv/"; fi; \
	v=$$($(RUFF) --version 2>/dev/null | awk '{print $$2}'); \
	if [ "$$v" = "$(RUFF_VERSION)" ]; then ok "ruff $$v"; \
	else bad "ruff $${v:-missing} (want $(RUFF_VERSION))" "make setup"; fi; \
	v=$$(nvim --version 2>/dev/null | awk 'NR == 1 {print $$2}'); \
	if [ -n "$$v" ]; then ok "nvim $$v"; \
	else bad "nvim missing" "install Neovim 0.10 or later (only make nvim:audit needs it)"; fi; \
	v=$$(ghostty +version 2>/dev/null | awk '$$2 == "version:" {print $$3}'); \
	if [ -n "$$v" ] && [ "$$(printf '%s\n' 1.3.0 "$$v" | sort -V | head -1)" = 1.3.0 ]; then ok "Ghostty $$v"; \
	else bad "Ghostty $${v:-missing} (want 1.3 or later)" "install Ghostty 1.3+: the themes need it, and ghostty:check has it validate them"; fi; \
	for cli in code cursor; do \
		l=/usr/share/$$cli/bin/$$cli; test -x $$l || l=$$cli; \
		v=$$($$l --version 2>/dev/null | head -1); \
		if [ -n "$$v" ]; then ok "$$cli $$v"; \
		else printf '  \033[33m--\033[0m    %s\n' "$$cli not installed (only vscode:install and vscode:audit use it)"; fi; \
	done; \
	for t in starship fzf lazygit tmux; do \
		if command -v $$t >/dev/null; then ok "$$t"; \
		else printf '  \033[33m--\033[0m    %s\n' "$$t not installed (terminal:audit skips what's missing; starship:check reads starship's defaults; tmux:check has tmux load its theme)"; fi; \
	done; \
	if command -v claude >/dev/null; then ok "claude"; \
	else printf '  \033[33m--\033[0m    %s\n' "claude not installed (only claude-code:audit uses it)"; fi; \
	exit $$fail

versions: ## Show Python, uv, ruff, Neovim, Ghostty, VS Code, Cursor, starship, fzf, lazygit, tmux and Claude Code versions
	@$(PYTHON) --version
	@uv --version 2>/dev/null || echo "uv: not installed"
	@if test -x $(RUFF); then $(RUFF) --version; else echo "ruff: not installed (run make setup)"; fi
	@nvim --version 2>/dev/null | head -1 || echo "nvim: not installed"
	@ghostty +version 2>/dev/null | awk '$$2 == "version:" {print "Ghostty " $$3}' | grep . || echo "ghostty: not installed"
	@for cli in code cursor; do l=/usr/share/$$cli/bin/$$cli; test -x $$l || l=$$cli; \
		v=$$($$l --version 2>/dev/null | head -1); echo "$$cli: $${v:-not installed}"; done
	@starship --version 2>/dev/null | head -1 || echo "starship: not installed"
	@fzf --version 2>/dev/null | sed 's/^/fzf /' || echo "fzf: not installed"
	@lazygit --version 2>/dev/null | sed 's/.*, version=\([^,]*\),.*/lazygit \1/' || echo "lazygit: not installed"
	@tmux -V 2>/dev/null || echo "tmux: not installed"
	@claude --version 2>/dev/null | head -1 || echo "claude: not installed"

clean: ## Remove ./bin and build/
	rm -rf $(BIN) build

#------------------------------------------------------------------------------
# Palette
#------------------------------------------------------------------------------

derive: ## Refresh derived hex values in palette.toml after changing a rule
	$(PYTHON) scripts/derive.py

#------------------------------------------------------------------------------
# Build
#------------------------------------------------------------------------------

build: site logo nvim ghostty vscode starship fzf lazygit tmux claude-code ## Build the site, the logo and every theme

site: ## Generate the site, build/site/ (the release workflow deploys it to GitHub Pages)
	$(PYTHON) scripts/preview.py

site\:compare: ## The site plus a before/after section against REF (default main); for review, not publishing
	$(PYTHON) scripts/preview.py --compare $(REF)

logo: ## Draw the logo, docs/images/{logo,icon}-{dark,light}.svg
	$(PYTHON) scripts/logo.py

screenshots: ## Retake the README's screenshots, docs/images/*.png (slow: runs the terminal, VS Code and Claude Code audits)
	$(PYTHON) scripts/screenshots.py

nvim: ## Generate the Neovim theme, lua/critical-rhino/{dark,light}.lua
	$(PYTHON) scripts/build_nvim.py

ghostty: ## Generate the Ghostty themes, extras/ghostty/critical-rhino-{dark,light}
	$(PYTHON) scripts/build_ghostty.py

vscode: ## Generate the VS Code and Cursor theme extension, extras/vscode/
	$(PYTHON) scripts/build_vscode.py

vscode\:package: ## Package the extension as build/critical-rhino-<version>.vsix
	$(PYTHON) scripts/build_vscode.py --package

vscode\:install: ## Package the extension and install it into VS Code and Cursor
	$(PYTHON) scripts/build_vscode.py --install

starship: ## Generate the starship theme, extras/starship/critical-rhino.toml
	$(PYTHON) scripts/build_starship.py

fzf: ## Generate the fzf theme, extras/fzf/critical-rhino.sh
	$(PYTHON) scripts/build_fzf.py

lazygit: ## Generate the lazygit theme, extras/lazygit/critical-rhino.yml
	$(PYTHON) scripts/build_lazygit.py

tmux: ## Generate the tmux theme, extras/tmux/critical-rhino.conf
	$(PYTHON) scripts/build_tmux.py

claude-code: ## Generate the Claude Code themes, extras/claude-code/themes/critical-rhino-{dark,light}.json
	$(PYTHON) scripts/build_claude_code.py

#------------------------------------------------------------------------------
# Check
#------------------------------------------------------------------------------

palette\:check: ## Check rules, references, contrast and syntax separation in palette.toml
	$(PYTHON) scripts/check.py

logo\:check: ## Check the committed logo is current
	$(PYTHON) scripts/logo.py --check

nvim\:check: ## Check the Neovim theme is current and every text color reads where it's drawn
	$(PYTHON) scripts/build_nvim.py --check

nvim\:audit: ## Check the theme against this machine's Neovim plugins (needs nvim and a lazy.nvim config)
	$(PYTHON) scripts/audit_nvim.py

ghostty\:check: ## Check the Ghostty themes are current, every pair reads, and Ghostty accepts them
	$(PYTHON) scripts/build_ghostty.py --check

vscode\:check: ## Check the VS Code theme is current and every text color reads where it's drawn
	$(PYTHON) scripts/build_vscode.py --check

vscode\:audit: ## Measure the text VS Code and Cursor really draw, headless, in every scene (slow)
	$(PYTHON) scripts/audit_vscode.py

starship\:check: ## Check the starship theme is current and every style starship draws reads
	$(PYTHON) scripts/build_starship.py --check

fzf\:check: ## Check the fzf theme is current and every pair reads
	$(PYTHON) scripts/build_fzf.py --check

lazygit\:check: ## Check the lazygit theme is current and every pair reads
	$(PYTHON) scripts/build_lazygit.py --check

tmux\:check: ## Check the tmux theme is current, every pair reads, and tmux accepts it
	$(PYTHON) scripts/build_tmux.py --check

claude-code\:check: ## Check the Claude Code themes are current, every pair reads, and diffs stay distinct
	$(PYTHON) scripts/build_claude_code.py --check

terminal\:audit: ## Measure what starship, fzf and lazygit really draw, in a private tmux (needs tmux)
	$(PYTHON) scripts/audit_terminal.py

claude-code\:audit: ## Measure what Claude Code really draws with the themes, replaying a made-up conversation (needs tmux and Claude Code)
	$(PYTHON) scripts/audit_claude_code.py

term\:test: ## Print every terminal color and check the terminal is drawing the theme's (run it in that terminal)
	@$(PYTHON) scripts/term_test.py

lint: ## Lint scripts/ with pinned ruff
	@test -x $(RUFF) || (echo "Run 'make setup' to install ruff" && exit 1)
	$(RUFF) check scripts/

check: palette\:check logo\:check nvim\:check ghostty\:check vscode\:check starship\:check fzf\:check lazygit\:check tmux\:check claude-code\:check lint ## Run every check (needs make setup)
