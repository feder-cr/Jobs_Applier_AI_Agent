#!/bin/sh
# AIHawk in one command: installs uv if it is missing, brings AIHawk up to date, and starts it.
command -v uvx >/dev/null 2>&1 || { curl -LsSf https://astral.sh/uv/install.sh | sh && . "$HOME/.local/bin/env"; }
# This script arrives through a pipe, and the screen needs the terminal. Python 3.14: on 3.15, the newest, some of
# AIHawk's libraries have no ready build yet, and the install stopped to compile them.
exec uvx --python 3.14 --refresh-package aihawk --from git+https://github.com/feder-cr/Jobs_Applier_AI_Agent aihawk "$@" </dev/tty
