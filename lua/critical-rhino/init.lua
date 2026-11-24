-- Critical Rhino for Neovim. The highlight tables in dark.lua and light.lua are
-- generated from palette.toml by scripts/build_nvim.py; this file only applies them.
local M = {}

---@param variant? "dark"|"light" Defaults to the current 'background'.
function M.load(variant)
  -- Clearing first also unsets g:colors_name, so setting 'background' below
  -- doesn't make Neovim reload whichever scheme was active before.
  vim.cmd("highlight clear")
  if vim.fn.exists("syntax_on") == 1 then
    vim.cmd("syntax reset")
  end
  if variant then
    vim.o.background = variant
  else
    variant = vim.o.background == "light" and "light" or "dark"
  end

  local theme = require("critical-rhino." .. variant)
  for name, spec in pairs(theme.groups) do
    vim.api.nvim_set_hl(0, name, spec)
  end
  for i, color in ipairs(theme.terminal) do
    vim.g["terminal_color_" .. (i - 1)] = color
  end
end

--- Highlights for bufferline's `highlights` option. bufferline builds the background
--- behind file icons from its own shades of Normal, which a color scheme can't reach;
--- passing these makes the icons sit on the theme's tab colors. Returns an empty table
--- when another color scheme is active, so it's safe to leave configured.
function M.bufferline()
  if not (vim.g.colors_name or ""):find("^critical%-rhino") then
    return {}
  end
  local theme = require("critical-rhino." .. (vim.o.background == "light" and "light" or "dark"))
  local out = {}
  for name, spec in pairs(theme.groups) do
    local key = name:match("^BufferLine(%u.*)$")
    if key and not spec.link then
      out[key:gsub("(%l)(%u)", "%1_%2"):lower()] = spec
    end
  end
  return out
end

return M
