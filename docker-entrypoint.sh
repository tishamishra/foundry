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

# Design DEFINITIONS (themes, style packs, page skeletons) are code, not your
# content — there is no UI that edits them, and new fonts/looks ship with the
# image. So refresh just these three from the baked seed on every boot. Your
# businesses, sites, imported library, coverage and images are never touched.
# This is what makes a design update actually reach the live sites on redeploy.
for f in themes.yaml styles.yaml skeletons.yaml; do
  if [ -f "/seed-data/$f" ]; then
    cp -f "/seed-data/$f" "/app/data/$f"
    echo "[foundry] refreshed design config: $f"
  fi
done

# One worker, many threads: the build runner and preview pool keep state in
# memory, so multiple workers would not see each other's jobs. Threads give
# concurrency without splitting that state.
exec gunicorn --workers 1 --threads 8 --timeout 1800 \
     --bind "0.0.0.0:${PORT:-8080}" panel:app
