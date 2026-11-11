#!/bin/sh
# AIHawk in one command: installs uv if it is missing, brings AIHawk up to date, and starts it.

# AIHawk's code as an archive, not a clone of its repository: a clone needs git, which many computers do not have, and
# brings the whole history, 180 MB against 7.
SOURCE="aihawk @ https://github.com/feder-cr/Jobs_Applier_AI_Agent/archive/refs/heads/main.tar.gz"
# Python 3.14: on 3.15, the newest, some of AIHawk's libraries have no ready build yet, and the install stopped to
# compile them.
PYTHON=3.14

# Where uv installs itself: on the path from here on, so a terminal opened before uv was installed finds it too, and
# its installer's own file for that need not be read.
PATH="$HOME/.local/bin:$PATH"
if ! command -v uvx >/dev/null 2>&1; then
    echo "AIHawk: getting ready. The first start downloads about 250 MB and takes a minute or two; the next ones take seconds."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    command -v uvx >/dev/null 2>&1 || { echo "AIHawk could not be installed: check your connection and run this again."; exit 1; }
fi

# The newest AIHawk is fetched first, and then started from this computer's copy. Without a connection the copy already
# here starts as it is: AIHawk must not need GitHub to open.
if uvx --python "$PYTHON" --refresh-package aihawk --from "$SOURCE" aihawk --version >/dev/null; then
    OFFLINE=""
else
    echo "AIHawk could not check for its newest version (no connection?). Starting the one on this computer."
    OFFLINE="--offline"
fi

# This script arrives through a pipe, and the screen needs the terminal.
if ! (: </dev/tty) 2>/dev/null; then
    echo "AIHawk needs a terminal to show its screen: run this in a terminal window."
    exit 1
fi
# shellcheck disable=SC2086  # OFFLINE is one flag or nothing.
exec uvx $OFFLINE --python "$PYTHON" --from "$SOURCE" aihawk "$@" </dev/tty
