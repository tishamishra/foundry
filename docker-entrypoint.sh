#!/usr/bin/env bash
set -e
# Seed the data volume ONCE. If /app/data is empty (a fresh volume just mounted),
# fill it from the image's baked copy. If it already has content — your imports,
# your sites, your coverage — leave it completely alone.
if [ -z "$(ls -A /app/data 2>/dev/null)" ]; then
  echo "[foundry] empty data volume — seeding shipped library and coverage once"
  cp -a /seed-data/. /app/data/
else
  echo "[foundry] existing data volume found — leaving your content untouched"
fi

# One worker, many threads: the build runner and preview pool keep state in
# memory, so multiple workers would not see each other's jobs. Threads give
# concurrency without splitting that state.
exec gunicorn --workers 1 --threads 8 --timeout 1800 \
     --bind "0.0.0.0:${PORT:-8080}" panel:app
