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
for f in themes.yaml styles.yaml skeletons.yaml global.yaml; do
  if [ -f "/seed-data/$f" ]; then
    cp -f "/seed-data/$f" "/app/data/$f"
    echo "[foundry] refreshed design config: $f"
  fi
done

# The shared base library (_base.yaml) is shipped copy, not your data — your
# imports live in data/library/<niche>.yaml and data/library/user/. Refresh it
# from the image so base-copy updates (e.g. the new section-heading pools) reach
# the live sites on redeploy. Your niche and user pools are left untouched.
if [ -f "/seed-data/library/_base.yaml" ]; then
  cp -f "/seed-data/library/_base.yaml" "/app/data/library/_base.yaml"
  echo "[foundry] refreshed base library: _base.yaml"
fi

# Niche DEFINITIONS (the service list, schema type and SEO title/description
# templates per trade) are shipped config, not your content — there is no UI
# that edits them. Refresh them so service-list changes (e.g. plumbing's
# expanded services) actually reach the panel and new builds on redeploy. The
# services a given site SELECTS live in that site's own record and are never
# touched here.
if [ -d "/seed-data/niches" ]; then
  cp -f /seed-data/niches/*.yaml /app/data/niches/ 2>/dev/null || true
  echo "[foundry] refreshed niche definitions"
fi

# One worker, many threads: the build runner and preview pool keep state in
# memory, so multiple workers would not see each other's jobs. Threads give
# concurrency without splitting that state.
exec gunicorn --workers 1 --threads 8 --timeout 1800 \
     --bind "0.0.0.0:${PORT:-8080}" panel:app
