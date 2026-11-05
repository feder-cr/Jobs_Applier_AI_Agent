#!/bin/sh
# AIHawk in one command: installs uv if it is missing, brings AIHawk up to date, and starts it.
command -v uvx >/dev/null 2>&1 || { curl -LsSf https://astral.sh/uv/install.sh | sh && . "$HOME/.local/bin/env"; }
# This script arrives through a pipe, and the screen needs the terminal.
exec uvx --refresh-package aihawk --from git+https://github.com/feder-cr/Jobs_Applier_AI_Agent aihawk "$@" </dev/tty
