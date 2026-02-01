set nocompatible
set termguicolors
filetype plugin indent on

call plug#begin()
" Theme
Plug 'folke/tokyonight.nvim', { 'branch': 'main' }

" Core
Plug 'nvim-lua/plenary.nvim'
Plug 'nvim-telescope/telescope.nvim'
Plug 'nvim-treesitter/nvim-treesitter', {'do': ':TSUpdate'}

" File explorer
Plug 'nvim-tree/nvim-web-devicons'
Plug 'nvim-tree/nvim-tree.lua'

" Git
Plug 'tpope/vim-fugitive'
Plug 'lewis6991/gitsigns.nvim'
Plug 'junegunn/gv.vim'

" Status line
Plug 'nvim-lualine/lualine.nvim'

" Editing
Plug 'tpope/vim-surround'
Plug 'tpope/vim-commentary'
Plug 'windwp/nvim-autopairs'
Plug 'lukas-reineke/indent-blankline.nvim'

" LSP & Completion
Plug 'neovim/nvim-lspconfig'
Plug 'hrsh7th/nvim-cmp'
Plug 'hrsh7th/cmp-nvim-lsp'
Plug 'hrsh7th/cmp-buffer'
Plug 'hrsh7th/cmp-path'

" Diff & Merge
Plug 'chrisbra/vim-diff-enhanced'
Plug 'samoshkin/vim-mergetool'

" Tmux
Plug 'tmux-plugins/vim-tmux-focus-events'
Plug 'christoomey/vim-tmux-navigator'

" Syntax
Plug 'vim-pandoc/vim-pandoc-syntax'

call plug#end()

" ============================================================================
" Settings
" ============================================================================
set number                      " Line numbers
set relativenumber              " Relative line numbers
set cursorline                  " Highlight current line
set signcolumn=yes              " Always show sign column
set scrolloff=8                 " Keep 8 lines above/below cursor
set sidescrolloff=8             " Keep 8 columns left/right of cursor
set updatetime=250              " Faster updates
set timeoutlen=300              " Faster key sequences
set splitright                  " Split windows right
set splitbelow                  " Split windows below
set ignorecase                  " Case insensitive search
set smartcase                   " Unless uppercase used
set incsearch                   " Incremental search
set hlsearch                    " Highlight matches
set expandtab                   " Spaces instead of tabs
set tabstop=4                   " Tab width
set shiftwidth=4                " Indent width
set softtabstop=4               " Soft tab width
set smartindent                 " Smart indenting
set wrap                        " Wrap lines
set linebreak                   " Wrap at word boundaries
set hidden                      " Allow hidden buffers
set noswapfile                  " No swap files
set nobackup                    " No backup files
set undofile                    " Persistent undo
set undodir=~/.vim/undodir      " Undo directory
set mouse=a                     " Mouse support
set clipboard=unnamedplus       " System clipboard
set completeopt=menu,menuone,noselect

" ============================================================================
" Theme
" ============================================================================
let g:tokyonight_style = "night"
let g:tokyonight_terminal_colors = 1
let g:tokyonight_transparent = 0
let g:tokyonight_italic_keywords = 1
let g:tokyonight_italic_functions = 1
silent! colorscheme tokyonight-night

" ============================================================================
" Keymaps
" ============================================================================
let mapleader = " "

" Telescope
nnoremap <leader>ff <cmd>Telescope find_files<cr>
nnoremap <leader>fg <cmd>Telescope live_grep<cr>
nnoremap <leader>fb <cmd>Telescope buffers<cr>
nnoremap <leader>fh <cmd>Telescope help_tags<cr>

" NvimTree
nnoremap <leader>e <cmd>NvimTreeToggle<cr>

" Buffer navigation
nnoremap <S-l> <cmd>bnext<cr>
nnoremap <S-h> <cmd>bprevious<cr>

" Clear search highlight
nnoremap <Esc> <cmd>nohlsearch<cr>

" Better window navigation
nnoremap <C-h> <C-w>h
nnoremap <C-j> <C-w>j
nnoremap <C-k> <C-w>k
nnoremap <C-l> <C-w>l

" Move lines
vnoremap J :m '>+1<CR>gv=gv
vnoremap K :m '<-2<CR>gv=gv

" Keep cursor centered
nnoremap <C-d> <C-d>zz
nnoremap <C-u> <C-u>zz
nnoremap n nzzzv
nnoremap N Nzzzv

" Mergetool
let g:mergetool_layout = 'mr'
let g:mergetool_prefer_revision = 'local'

" ============================================================================
" Lua Config
" ============================================================================
lua << EOF
-- Safe require helper (doesn't error if plugin missing)
local function safe_require(module)
    local ok, result = pcall(require, module)
    if ok then return result end
    return nil
end

-- Lualine (status line)
local lualine = safe_require('lualine')
if lualine then
    lualine.setup {
        options = {
            theme = 'tokyonight',
            component_separators = { left = '', right = ''},
            section_separators = { left = '', right = ''},
        }
    }
end

-- Gitsigns
local gitsigns = safe_require('gitsigns')
if gitsigns then gitsigns.setup() end

-- Autopairs
local autopairs = safe_require('nvim-autopairs')
if autopairs then autopairs.setup() end

-- NvimTree
local nvimtree = safe_require('nvim-tree')
if nvimtree then
    nvimtree.setup {
        view = { width = 35 },
        renderer = { icons = { show = { file = true, folder = true, folder_arrow = true, git = true }}},
    }
end

-- Treesitter (disabled - using vim syntax instead)
-- To enable: uncomment and run :TSInstall python lua etc
-- local treesitter = safe_require('nvim-treesitter.configs')
-- if treesitter then
--     treesitter.setup {
--         ensure_installed = { "lua", "vim", "vimdoc", "python", "javascript", "typescript", "json", "yaml", "markdown", "bash" },
--         sync_install = false,
--         auto_install = false,
--         highlight = { enable = true },
--         indent = { enable = true },
--     }
-- end

-- Indent blankline
local ibl = safe_require('ibl')
if ibl then ibl.setup() end

-- Tokyo Night highlight tweaks
vim.api.nvim_create_autocmd("ColorScheme", {
    pattern = "tokyonight*",
    callback = function()
        vim.api.nvim_set_hl(0, "Boolean", { fg = "#ff9e64" })
        vim.api.nvim_set_hl(0, "pythonBoolean", { fg = "#ff9e64" })
        vim.api.nvim_set_hl(0, "Constant", { fg = "#ff9e64" })
    end
})
vim.api.nvim_set_hl(0, "Boolean", { fg = "#ff9e64" })
vim.api.nvim_set_hl(0, "pythonBoolean", { fg = "#ff9e64" })
vim.api.nvim_set_hl(0, "Constant", { fg = "#ff9e64" })
EOF
