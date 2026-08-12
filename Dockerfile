# Foundry — the site factory, as a long-running web app.
#
# The whole point of this image is that YOUR DATA OUTLIVES CODE UPDATES. The
# repo is baked in, but the mutable data/ folder is seeded to a persistent
# volume on first run and never overwritten again. Ship a new image, redeploy,
# and everything you imported is still there.
FROM python:3.11-slim

WORKDIR /app

# system deps for the three deploy targets:
#   git    -> GitHub Pages target
#   rsync  -> your-own-server target (over SSH)
#   nodejs -> Cloudflare target (npx wrangler)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git rsync openssh-client nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bake a pristine copy of the shipped data. The entrypoint seeds the volume
# from here only when the volume is empty (first run), so your imports survive.
RUN cp -a data /seed-data && chmod +x docker-entrypoint.sh

ENV PORT=8080 FOUNDRY_PASSWORD=change-me
EXPOSE 8080
ENTRYPOINT ["./docker-entrypoint.sh"]
