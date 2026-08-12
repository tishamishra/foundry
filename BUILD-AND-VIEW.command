#!/bin/bash
# Foundry — double-click launcher for macOS.
#
# Sets up an isolated Python environment on first run, builds every site,
# then serves dist/ and opens your browser. Safe to run repeatedly.
#
# First time only: right-click this file -> Open -> Open.
# (macOS quarantines files that arrive in a zip. A normal double-click works
#  from then on.)

cd "$(dirname "$0")" || exit 1
set -u

say() { printf "\n\033[1m%s\033[0m\n" "$1"; }
die() { printf "\n\033[31m%s\033[0m\n\n" "$1"; read -r -p "Press return to close."; exit 1; }

# --- 1. find a usable python3 -------------------------------------------------
PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
      PY="$candidate"; break
    fi
  fi
done
[ -n "$PY" ] || die "No Python 3.9+ found.
Install it with either:
  xcode-select --install          (Apple's own toolchain)
  brew install python             (Homebrew)
then double-click this file again."

say "Using $($PY --version 2>&1)"

# --- 2. isolated environment, created once ------------------------------------
if [ ! -d ".venv" ]; then
  say "First run — creating an isolated environment in .venv"
  "$PY" -m venv .venv || die "Could not create the virtual environment."
fi
VENV_PY=".venv/bin/python"
[ -x "$VENV_PY" ] || die "The .venv looks broken. Delete the .venv folder and run this again."

say "Checking dependencies"
"$VENV_PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1
"$VENV_PY" -m pip install --quiet -r requirements.txt || die "Dependency install failed.
If you are offline, connect and try again — Foundry needs only Jinja2 and PyYAML."

# --- 3. build -----------------------------------------------------------------
say "Building every site"
"$VENV_PY" foundry.py build
BUILD_STATUS=$?
if [ $BUILD_STATUS -ne 0 ]; then
  printf "\n\033[33mOne or more sites were blocked by a QA gate (see above).\033[0m\n"
  printf "\033[2mThat is the system working. The sites that passed still built.\033[0m\n"
fi

# --- 4. serve -----------------------------------------------------------------
# Port 5000 is skipped deliberately: on macOS the AirPlay Receiver listens there
# and answers every request with 403, which looks exactly like a broken app.
PORT=8000
while [ "$PORT" -lt 8050 ]; do
  if [ "$PORT" -ne 5000 ] && ! nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1; then break; fi
  PORT=$((PORT + 1))
done

say "Opening the preview index at http://127.0.0.1:$PORT/"
printf "\033[2mEach site gets its own port, because each one is the root of its own\ndomain in production. The index page links to all of them.\033[0m\n"
( sleep 1; open "http://127.0.0.1:$PORT/" ) &
"$VENV_PY" foundry.py serve "$PORT"

printf "\nStopped.\n"
