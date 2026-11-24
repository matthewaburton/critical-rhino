-- lualine theme for Critical Rhino, following 'background'. lualine's "auto" theme
-- picks this up from g:colors_name.
return require("critical-rhino." .. (vim.o.background == "light" and "light" or "dark")).lualine
