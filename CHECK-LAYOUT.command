#!/bin/bash
# Foundry — the layout sweep. Loads every built page at six widths in a headless
# browser and fails on horizontal overflow.
#
# Installs Playwright + Chromium on first run (a few hundred MB, once).
# Everything else in Foundry runs without it.

cd "$(dirname "$0")" || exit 1
set -u
VENV_PY=".venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
  printf "\n\033[31mRun BUILD-AND-VIEW.command first — it creates the environment.\033[0m\n\n"
  read -r -p "Press return to close."; exit 1
fi

if ! "$VENV_PY" -c "import playwright" >/dev/null 2>&1; then
  printf "\n\033[1mFirst run — installing Playwright and Chromium\033[0m\n"
  "$VENV_PY" -m pip install --quiet playwright || { read -r -p "Install failed. Press return."; exit 1; }
  "$VENV_PY" -m playwright install chromium || { read -r -p "Browser download failed. Press return."; exit 1; }
fi

"$VENV_PY" foundry.py sweep
printf "\n"
read -r -p "Press return to close."
