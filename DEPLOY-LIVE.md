# Hosting Foundry live on your own server (no more data loss)

The reason your imported blocks kept disappearing was never the storage — it was
the delivery. Foundry saves everything as flat files under `data/`, and every
fresh `foundry.zip` I sent was a clean copy of that folder **without your
content**. Running it live fixes this permanently: your `data/` folder lives on
a **persistent volume**, so new code never touches it.

You do **not** need a database. A persistent volume with the existing YAML/JSON
files does exactly what you want, with zero code change.

---

## What is stored, and where

Everything mutable lives under `data/`:

- `data/library/user/<niche>.yaml` — the blocks **you import** (this is what was being lost)
- `data/sites/*.yaml`, `data/businesses/*.yaml` — your site and business records
- `data/coverage/` — your imported coverage feed
- `data/shipped.json` — the shipped-fingerprint record
- `data/library/_base.yaml`, `<niche>.yaml` — the base + niche libraries

The Docker setup mounts all of `data/` (and `dist/`) as a named volume. That
volume survives every redeploy.

---

## Deploy on Hostinger + Dokploy

1. **Put this project in a Git repo** (GitHub/GitLab). Dokploy pulls from Git.

2. **In Dokploy → Create → Compose** (or Application → Docker Compose). Point it
   at this repo. It will use the included `docker-compose.yml`.

3. **Set environment variables** in Dokploy:
   - `FOUNDRY_PASSWORD` — your panel login password (change it from `change-me`).
   - `FOUNDRY_SECRET` — any long random string (keeps logins alive across restarts).

4. **The volume is already declared** in `docker-compose.yml` as `foundry_data`
   mounted at `/app/data`. Dokploy will create and persist it. **Do not delete
   this volume** — it is your content.

5. **Domain / subdomain:** in Dokploy, add a domain (e.g. `foundry.yourdomain.com`),
   point it at the service's port **8080**. Dokploy handles HTTPS.

6. **Deploy.** First run seeds the volume from the shipped library and coverage.
   Open your subdomain, log in, and import content — it writes to the volume.

That's it. It runs 24/7 on your subdomain.

---

## How updates work now (the important part)

When I send a new version:

1. Push the new code to your Git repo (replace the files — **except you never
   touch the running volume**).
2. In Dokploy, hit **Redeploy**.

The container is rebuilt with new code; the `foundry_data` volume is **left
alone**. Everything you imported is still there. No more data loss.

The seed step only runs when the volume is **empty** (first deploy). On every
later deploy it prints `existing data volume found — leaving your content
untouched` and skips seeding.

---

## Run it locally with Docker first (optional sanity check)

```bash
docker compose up --build
# open http://localhost:8080  (password = FOUNDRY_PASSWORD, default change-me)
```

Import a few blocks, then:

```bash
docker compose down          # stops the container
docker compose up --build    # rebuilds — your blocks are still there
```

---

## Notes / limits

- **Preview** (the per-site local servers) is a desktop convenience and is not
  exposed through the single web port. On the server, publish sites with the
  **Deploy** tab (GitHub Pages / Cloudflare / your server) instead.
- **`foundry sweep`** (the headless-browser layout check) needs Playwright and a
  browser; it is not installed in this lean image. The build, QA, SEO, import,
  and intelligence features all work without it.
- Keep `data/secrets.yaml` (deploy credentials) out of Git — it is already in
  `.dockerignore` and `.gitignore`.
- The base library only re-seeds into an **empty** volume. If I ship an updated
  `_base.yaml` later and you want it, import it through the panel or tell me and
  I'll give you a one-line reseed step — this is the deliberate trade for never
  overwriting your content.
