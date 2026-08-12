#!/bin/bash
# Foundry — the admin panel.
#
# First time only: right-click this file -> Open -> Open.
#
# Password is "admin" unless you set FOUNDRY_PASSWORD.

cd "$(dirname "$0")" || exit 1
set -u
say() { printf "\n\033[1m%s\033[0m\n" "$1"; }
die() { printf "\n\033[31m%s\033[0m\n\n" "$1"; read -r -p "Press return to close."; exit 1; }

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && \
     "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || die "No Python 3.9+ found.
Install it with  xcode-select --install  or  brew install python  and try again."

if [ ! -d ".venv" ]; then
  say "First run — creating an isolated environment in .venv"
  "$PY" -m venv .venv || die "Could not create the virtual environment."
fi
VENV_PY=".venv/bin/python"
say "Checking dependencies"
"$VENV_PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1
"$VENV_PY" -m pip install --quiet -r requirements.txt || die "Dependency install failed."

# Port 5000 is skipped: macOS AirPlay Receiver holds it and answers 403 to
# everything, which looks exactly like a broken app.
PORT=5050
while [ "$PORT" -lt 5090 ]; do
  if [ "$PORT" -ne 5000 ] && ! nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1; then break; fi
  PORT=$((PORT + 1))
done

say "Opening the panel at http://127.0.0.1:$PORT/"
( sleep 2; open "http://127.0.0.1:$PORT/" ) &
PORT="$PORT" "$VENV_PY" panel.py
printf "\nStopped.\n"
