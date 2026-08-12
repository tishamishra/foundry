# Foundry

A lead-gen site factory for local pay-per-call businesses.

It **renders from a content graph** instead of cloning a codebase, **composes copy from a block
library** instead of rewriting whole documents per site, **measures uniqueness** instead of
assuming it, and ships **static artifacts with an edge fallback** instead of one server process
per site.

Engine A's composition economics. Engine B's verification rigour. Neither engine's ceiling.

---

## One library, twenty-four niches

`data/library/_base.yaml` is **trade-agnostic**: every block is written with `{niche_lower}`,
`{service_lower}` and `{city}` rather than naming a trade, so it is true of any competent
contractor in any vertical the feed carries. 312 blocks across 18 kinds.

Loading order is `_base` -> `<niche>` -> `user`. A niche with no file of its own still builds; a
niche file adds specifics on top; `foundry fill` output lands in `user` and is never overwritten
by an upgrade.

**The trade-off, stated plainly:** two sites in *different* niches that draw the same base block
share that sentence. The similarity gate compares within a niche, so it will not catch that.
Niche files and `foundry fill` are how you move a niche off the shared base.

## Coverage: the buyer's feed

`foundry feed <file.csv>` imports the wide multi-niche feed
(`zip,city,state,vertical,net_payout,last_updated`) in one pass, splitting it into
`data/coverage/<niche>/<STATE>.csv`.

On the 201,000-row feed this was built against:

```
193,308 rows kept · 29,870 recovered from ZIP · 200,567 counties added · 7,453 duplicates dropped
24 verticals · 46-51 states each · 35,380 unique ZIPs
```

Three things the importer does that matter more than the row count.

**It recovers the 15% the feed loses.** 30,102 rows arrived with an empty city *and* an empty
state — but a valid ZIP, vertical and payout. Dropping them would have silently discarded a
seventh of the footprint. They are rebuilt from the ZIP. A ZIP that cannot be matched is
**reported by number, never guessed** — 40 of them, all Puerto Rico ranges.

**It supplies the county the feed does not have.** Engine B recorded this exact gap: its own
location table had `withCounty: 0`, so every build fell back to a fabricated `<state>-statewide`
group. County pages are a real page type and an invented county is a lie on a live site. Foundry
ships `data/geo/zip-county.csv` — 42,342 US ZIPs, 1.5 MB, part of the package. No network, no
extra dependency, no runtime download.

**It carries `net_payout` all the way through.** Neither parent engine had this. It is the
difference between "these ZIPs are payable" and "these ZIPs are worth more", and it now decides
**which cities get pre-rendered**: when only some pages become files, the highest-value ones are
the static ones. `foundry feed` prints the payout-weighted table so you can see which niche to
build first — on this feed, water damage is worth 5.2M against roofing's 1.1M.

### What is loaded and what is still missing

All 24 verticals have coverage and a **niche definition** (services, schema type, SEO patterns).
Only roofing has a **block library**, so only roofing can build. Every other niche refuses, by
design, with the reason:

> *Coverage says WHERE you may publish; the library is WHAT the pages say. Borrowing another
> niche's library would put roofing sentences on a plumbing site, so the build refuses rather
> than substituting.*

`python3 foundry.py fill <niche> <kind> 40` is how that gap closes.

### Scale, measured

One national roofing site — 46 states, 11,356 cities, 928 counties:

```
graph load       0.2 s
build            71 s
static pages     3,741   (the 400 highest-payout cities, plus fixed/service/county pages)
edge pages       76,692  (from 42 master templates)
TOTAL            80,433
on disk          54.9 MB
homepage         18 KB
```

Turning `prerender_top_n` down moves pages from disk to the edge without changing the page count.
Build time tracks the *pre-rendered* count, not the footprint.

## One niche per site — and what "services" means

A site covers **one niche**. That was already true and is worth stating plainly, because the
words look interchangeable and are not:

```
NICHE     the trade the site is about        water-damage       one per site
SERVICE   a job WITHIN that trade            Emergency Water Extraction
                                             Structural Drying
                                             Burst Pipe Cleanup ...
```

So the directory's "services by area" matrix is *water-damage jobs × cities*, all inside one
niche. It is never two different trades on one domain.

**Single-service is available as an option.** Tick exactly one service on the site form and the
build drops `/services/<x>/<city>` entirely — with one service those pages and `/areas/<city>`
say the same thing about the same place, and two pages competing for one query is worse than one
page winning it. The city page *becomes* the service page, the directory matrix collapses to a
place index, and every internal link follows. Leaving all services ticked (the default) is the
normal full-niche site.

The site form now has a **service picker**, filtered to the selected niche. It did not before —
narrowing a site meant editing YAML by hand.

## CSS engine — built-in by default, Tailwind optional

```bash
python3 foundry.py css tailwind    # compile (installs Tailwind locally on first run)
python3 foundry.py css check       # which engine each site is set to
```

```yaml
css_engine: tailwind    # per site; default is `builtin`
```

`tailwind/src.css` produces a **drop-in replacement**: Tailwind's utilities, plus an `@theme`
block mapping the design tokens onto the *same custom properties* the themes set at runtime — so
`bg-brand` and `background: var(--brand)` can never disagree — plus the existing hand-authored
stylesheet imported whole into the components layer. Nothing is replaced; utilities are added.

Measured: built-in **35.8 KB**, Tailwind build **37.9 KB**. Both engines pass the same sweep —
288 checks, zero overflow, zero contrast failures — and the two shipped side by side in the same
`dist/`.

**Why the built-in sheet stays the default:** Tailwind needs Node, and it needs a rebuild after
every template edit. That second one is exactly what bit Engine A — its templates and its
compiled bundle could drift, and a section using a class the compiler had not seen rendered
**silently unstyled**, with no error and no warning. `foundry css check` reports drift instead of
letting a live page reveal it, but the risk is real and it is why the no-build path is the one
that ships.

## Skeletons and style packs — two axes, not one

They used to be one thing, and that was the limit. "Bold" meant both condensed uppercase type
*and* an emergency bar at the top, so you could not have the loud typography on a calm,
content-led page. Split apart they multiply instead of repeating:

```
skeleton   what the page IS      6 architectures   data/skeletons.yaml
style      how the page LOOKS   17 packs           data/styles.yaml
theme      colour                10 palettes       data/themes.yaml
seed       the copy              searched, measured
                                 = 1,020 combinations
```

### The six skeletons

| Skeleton | Shell | For |
|---|---|---|
| **standard** | single | the balanced marketing page; good default |
| **conversion** | single | CTA-dense, short sections — paid traffic and urgent trades |
| **longform** | reading | prose-led, fewer and deeper sections, narrower measure |
| **directory** | directory | index-first: search, a service × place matrix, an A–Z of covered areas |
| **local-first** | single | geography above the marketing copy |
| **showcase** | single | leans on the image pool |

A skeleton owns the section order for every page type and names a layout shell the stylesheet
knows about. Adding one is a block of YAML.

### The directory skeleton

Genuinely a different page model, not a re-skin. The home page is a **client-side filter over the
places the site actually covers**, a **service × place matrix where every cell is a real page**,
and an **A–Z index grouped by county**. City pages get every service as a tile; service pages get
every city as a chip.

That matters for more than looks. A 26,000-page site whose long tail is linked only from a
sitemap is 25,000 orphans — the SEO agent's `orphan-pages` rule exists precisely because that is
the default failure. The matrix and the index are what turn those pages into a crawlable graph.

The filter needs no backend and no search index: every place is already in the HTML, so the input
just hides rows.

### The seventeen style packs

Visual only — hero shape, card treatment, headline family, accent device, rhythm, radius, fonts.
Eight hero variants, eight card treatments, eight headline families, seven accent devices.

`classic` · `bold` · `editorial` · `civic` · `compact` · `premium` · `ledger` · `almanac` ·
`atlas` · `quiet` · `signal` · `trade` · `harbor` · `grid` · `dispatch` · `meridian` · `foreman`

Ranging from **ledger** (monospace headings, hairline rules, `//` accents) through **almanac**
(slab serif, boxed heads, hard shadows) and **trade** (Oswald condensed in a framed hero) to
**quiet** (Lora, soft shadows, airy) and **foreman** (Anton, tight, highlighter). One stylesheet,
36 KB, no build step.

A site is now three independent choices, not one:

```
theme              colour + surface        10 palettes
style pack         SHAPE and section order  6 packs
composition_seed   the copy                 searched, measured
```

A style pack (`data/styles.yaml`) sets the hero variant, the card treatment, the headline family,
the accent device, the vertical rhythm, the corner radius, the web fonts — **and the section order
for every page type**. That last one matters most: two sites on different packs do not merely
repaint, they re-order. Structure is something a search engine compares too, so varying it is not
only cosmetic.

| Pack | Hero | Cards | Headline | Accent | Feel |
|---|---|---|---|---|---|
| Classic | split | outline | serif (Fraunces) | none | measured, trustworthy |
| Bold | banner | raised | condensed caps (Archivo) | highlight | loud, urgent |
| Editorial | stacked | flat | display serif (Playfair) | underline | magazine |
| Civic | split | left-edge | grotesk (IBM Plex) | bar | municipal, plain |
| Compact | banner | flat | grotesk (Manrope) | none | utilitarian |
| Showcase | overlay | raised | condensed (Bricolage) | highlight | premium |

Ten themes × six packs = **60 distinct looks over one hand-authored stylesheet**. Adding a pack is
a block of YAML and a block of CSS — no template, no build step.

### The sections

Fourteen new ones, all data-driven and all optional per pack: animated **stats** (derived from the
coverage list — never invented), a scrolling **county strip**, **credential badges** that render
only the facts the business record supplies, an **alternating showcase**, **tabbed services**, a
**process timeline**, a **"signs worth a call"** grid, a **repair-vs-replace comparison**, a
**what-changes-the-price** grid, a **coverage directory**, an **emergency alert bar**, an inline
**quote band with a form**, a **testimonial carousel**, and a **mobile sticky call bar**.

### The interactive layer

`assets/js/site.js` — vanilla, no dependencies, ~5 KB, deferred: mobile nav, scroll-linked header
and reading progress, IntersectionObserver reveals, counting stats, tabs, a snap carousel, smooth
in-page scrolling, and a sticky call bar that appears once the hero CTA scrolls away and hides
while you scroll up.

Everything degrades. With JavaScript off the page is complete, every tab panel is reachable, the
carousel is a scrollable row, and the phone number is still in the header. On a lead-capture page
a script failure costs a call, so nothing important is allowed to depend on one.

## The SEO agent

`foundry seo [site]` — and it runs automatically on every build. **"Shipped" now requires both
gates:** a site failing QA *or* SEO is built and previewable, but is not recorded as shipped.

It audits the rendered output, not the templates, and checks the things programmatic SEO
actually fails at: duplicate titles and descriptions across the whole site, title and description
length, exactly one H1 and no skipped heading levels, canonical present and self-referencing,
noindex left in from staging, the target city present in the title/H1/opening copy of a city
page, word-count floors per page type, internal links that resolve (including edge-rendered
routes), orphan pages, sitemap size against the 50,000-URL protocol cap, robots.txt, JSON-LD
that parses and carries a telephone and an areaServed, image alt text and intrinsic dimensions,
and HTML page weight.

Severities live in `data/seo-rules.yaml`, each with a `why`, and the report ends with what it did
**not** check — off-page signals, field Core Web Vitals, and whether the keywords are ones anyone
searches for.

### What it found on its first run

Four real defects, on a site that had passed every existing gate:

- the quote-band background image had no `width`/`height` — layout shift on 91 pages
- `/about` and `/contact` titles were 21 and 23 characters, wasting the slot
- `/contact` fell one word under a floor that was wrong for a contact page
- at national scale, **`/areas` was 567 KB** — 928 counties × 18 city links on one page

And two defects in itself, which is the more useful half:

- **the extractor misread its own pages.** `["\']...["\']` looks right and is wrong: it terminates
  on the first quote of either kind, so a description containing "Prince George's County"
  measured 55 characters instead of 148 and the agent reported a length problem that did not
  exist. Now matched with a backreference. *A gate that misreads the page is worse than no gate.*
- **`str.title()` capitalises after an apostrophe**, so the county was "Prince George'S County" on
  every page that named it.

### The operating envelope

These sites are built for **one to three states and up to roughly 4,000 cities**, not for a
national footprint. Measured on the real feed — pest control across PA + CA + TX, 3,732 cities,
285 counties, 6 services, 10 city-page variants:

| | hybrid (500 pre-rendered) | full static |
|---|---:|---:|
| build | **24 s** | 161 s |
| pages on disk | 3,798 | 26,422 |
| pages total | 26,422 | 26,422 |
| disk | **50.6 MB** | 336 MB |
| sitemap | 1 file, 26,420 URLs | same |
| SEO | 100/100 | 100/100 |

Two things follow from those numbers.

**Full static is genuinely viable at this scale.** 161 seconds and 336 MB for a complete
three-state site with no Worker, no edge logic, and nothing to deploy but a folder. Hybrid is
seven times faster and a sixth of the disk, but "everything is a real file" is a legitimate
choice here in a way it is not nationally.

**The sitemap never shards at this size.** 26,420 URLs is well under the 45,000 chunk, so the
index logic sits unused — it exists as a backstop for the day a build runs wider, not as
something this workload hits.

A build outside the envelope — more than three states, or more than 4,000 cities — now raises a
`coverage-envelope` warning. Not a wall: past that point the build slows sharply, the sitemap
approaches its cap, and one site starts competing with the next one built in the same states.
That should be a decision, not an accident.

### Sitemap sharding, as a backstop

The protocol caps a sitemap at 50,000 URLs and 50 MB. A national footprint here is 80,433 pages,
and an oversized sitemap is not "mostly fine" — it is rejected and every URL past the cap is
invisible. The build now writes a sitemap index plus shards automatically:

```
sitemap.xml      2 locs   INDEX
sitemap-1.xml   45,000 locs   3.9 MB
sitemap-2.xml   35,431 locs   3.1 MB
```

National site, after the fixes: **97/100**, `/areas` down from 567 KB to 71 KB, 12.0 KB average page.

## Deploying

Three targets, one rule: **a site that is not shipped does not deploy.** "Shipped" already means
something exact — it passed the QA gate *and* the SEO gate in the same build. Preview never asks
that; deploy always does. The gates exist to stop a failure that only becomes expensive after
publication, and pages a search engine has already crawled cannot be un-crawled by fixing the
generator afterwards.

| Target | What it does | When it is the right one |
|---|---|---|
| `github` | Keeps a working clone in `.deploy/<site>/github`, mirrors `dist/` into it, commits and pushes. Writes `CNAME` and `.nojekyll` for Pages. | Static builds. The second deploy of a large site pushes the diff, not every file again. |
| `cloudflare` | `wrangler`. Deploys the Worker and its assets together when the build emitted one, otherwise Pages. | **The only target that can serve an edge-rendered site.** |
| `server` | `rsync -az --delete` over SSH, batch mode. | Your own box. |

```
foundry deploy <site_id> github|cloudflare|server [--dry-run]
```

Panel: **Deploy**. Credentials are global and go to `data/secrets.yaml` at 0600, which is added
to `.gitignore` on first save; an environment variable of the same name in capitals wins over the
file. Per-site settings live in `data/deploy.yaml`. Neither the panel nor the CLI ever prints a
credential — every command line and every line of captured output passes through a redactor built
from the current secrets, and ANSI colour is stripped so wrangler's output is readable in a
browser.

**Dry run is the default in the panel, and it should be.** It runs the entire preflight and prints
the exact commands, `shlex`-quoted so you could paste them into a shell, without executing any of
them. It is the only way to read the `rsync --delete` line *before* it runs rather than after.

Preflight refuses rather than warns, and each refusal has a cause and a fix:

- not built, or not shipped
- **the site was edited after the last build** — deploying would publish the previous version and
  report success, which is the worst kind of failure
- **edge render mode on a static host** — GitHub Pages and a plain web server have nothing to run,
  so every city page outside the prerendered set would 404 *after* the sitemap advertised it
- **`rsync --delete` into `/`, `/etc`, `/var/www` or any other system directory** — pointed at the
  wrong path that is not a deploy, it is a wipe
- missing tool (`git`, `rsync`, Node), missing credential, unconfigured target

Failures from the tools themselves are diagnosed too, because each was going to cost an hour the
first time: GitHub refusing a password, a non-fast-forward push, `Permission denied (publickey)`,
a Cloudflare token without the right permission, and no network at all.

One thing worth knowing: a build and a deploy never run at the same time. A build rewrites `dist/`
from scratch and a deploy reads it, so they take turns.

## The admin panel

`START-PANEL.command` opens a local Flask panel at `http://127.0.0.1:5050/` (password `admin`,
override with `FOUNDRY_PASSWORD`). It is a convenience over the CLI, not the system: delete
`panel.py` and the factory still runs.

| Screen | What it is for |
|---|---|
| Deploy | Credentials, per-site targets, dry run, and a live deploy log |
| **Dashboard** | every site, its coverage, seed, render mode and status; build, preview, edit, re-seed, spin-off, delete |
| **New site** | business × niche × coverage × theme × composition, with automatic seed search |
| **Spin-off** | "another one like that, new branding" — inherits niche, coverage and render mode, starts with an **empty fact block**, and searches a seed that does not repeat the original |
| **Bulk create** | paste many businesses, one per line; each gets its own record, site and searched seed |
| **Coverage** | import a buyer's payable-ZIP list; replaces the niche wholesale, never merges |
| **Library** | browse blocks by kind, add batches, and see the capacity ceiling |

### The seed search is the point

A site's copy is decided by one integer. Rather than pick it and hope, Foundry composes every
candidate seed — microseconds each, no rendering — and Jaccards the 5-word shingles against every
existing site in the niche, keeping the seed with the lowest worst-case overlap. 250 candidates
take under a second.

In bulk, seeds are searched **one row at a time and written before the next is searched**, so row
two is measured against row one rather than against an empty world. Creating forty sites that all
differ from the originals but not from each other is the exact failure that ordering prevents.

### Library capacity — the honest ceiling

The Library page shows, per block kind, the pool size against how many blocks **one site draws**,
and therefore roughly how many distinct sites the library supports. The shipped roofing library is
222 blocks and supports about **one**: `faqs` holds 42 while a single site draws 36.

That number is the whole answer to "how do I make unlimited sites". The seed space cannot fix a
shallow pool — if one site consumes most of a pool, every site will, whatever seed it uses. Deepen
the pools (`foundry fill roofing faqs 40`) and the ceiling rises with them.

## Run it on a Mac — no Terminal needed

Unzip the folder somewhere you can find again (Documents is fine, not Downloads), then:

1. **Right-click `BUILD-AND-VIEW.command` → Open → Open.** The right-click is only needed the
   first time; macOS quarantines anything that arrives in a zip. A normal double-click works
   from then on.
2. It finds Python, creates an isolated `.venv` on first run, installs Jinja2 and PyYAML, builds
   every site, then serves them and opens your browser.
3. `START-PANEL.command` opens the admin panel described above.
4. `CHECK-LAYOUT.command` runs the six-width overflow sweep. It installs Playwright and Chromium
   on first use — a few hundred MB, once. Nothing else in Foundry needs it.

If step 1 says no Python was found, run `xcode-select --install` (Apple's own toolchain) or
`brew install python`, then double-click again.

Port 5000 is skipped deliberately: on macOS the AirPlay Receiver holds it and answers every
request with `403`, which looks exactly like a broken app.

## Run it from a terminal

```bash
pip install -r requirements.txt          # Jinja2 + PyYAML. That is the whole dependency list.

python3 foundry.py build                 # render + verify every site
python3 foundry.py build bennettroofers-com
python3 foundry.py check                 # verify without touching dist/
python3 foundry.py compare               # measured similarity matrix
python3 foundry.py sweep                 # headless overflow sweep at six widths
python3 foundry.py list                  # sites, coverage, library depth
python3 foundry.py serve 8000            # browse dist/
python3 foundry.py coverage roofing buyer-list.txt
python3 foundry.py fill roofing faqs 20  # extend the library with the tool-free LLM
```

Node is **not** required. There is no CSS build step: the stylesheet is hand-authored with CSS
custom properties, so Engine A's "a computed Tailwind arbitrary value does not exist in the
bundle" trap and its mandatory `build css` step both disappear.

---

## What it produced on the demo data

Three sites from one 150-block roofing library and one 59-row Georgia coverage list:

```
bennettroofers-com      103 static + 140 edge pages     0.8s    PASS
harborlineroofing-com   243 static +   0 edge pages     1.8s    PASS
controlcheck-test       103 static + 140 edge pages     —       BLOCKED
```

`controlcheck-test` is built with the **same `composition_seed`** as `bennettroofers-com` on
purpose. It is blocked at **77.3% measured similarity** (100% on the composed copy alone), which
is the duplicate-content gate proving it actually fires rather than merely existing.

`harborlineroofing-com` differs only by seed and sits at **17.2%** — a warning, not a block, and
honestly so: two sites covering the *identical* 32-city Georgia footprint really do share their
location lists, their county names and their service names. The report separates that structural
overlap from the copy overlap so you can tell which one you are looking at.

Verification, all of it on rendered output:

```
zero unresolved {tokens} across every page of every site
816 overflow checks (48 + 48 + 40 pages × six widths) — zero overflow
edge worker smoke test: 29 masters → 140 pages, zero sentinels left in output
```

---

## The idea in one table

|  | Engine A | Engine B | **Foundry** |
|---|---|---|---|
| Core act | render from data | clone and mutate | **render from data** |
| Copy source | humans write blocks | LLM rewrites per site | **LLM writes blocks, machine composes** |
| Copy cost | O(blocks) × human | **O(sites) × $4.56** | **O(blocks) × LLM, amortised** |
| Uniqueness | variant-ID check | measured per site | **measured + self-diagnosing** |
| Output | static | Next.js + pm2 per site | **static + edge fallback** |
| Scale ceiling | none | **~59 live sites** | none |
| Fabrication guard | none | scanned after the fact | **structurally impossible** |

---

## How it works

### 1. The content graph

```
data/niches/<niche>.yaml        services, page graph, schema type, SEO patterns
data/library/<niche>.yaml       the shipped block pool          (never written to)
data/library/user/<niche>.yaml  your additions + LLM output     (merged at read time)
data/businesses/<slug>.yaml     the entity + THE FACT BLOCK
data/sites/<site_id>.yaml       the placement: business × niche × coverage × theme × seed
data/coverage/<niche>/<ST>.csv  the buyer's payable-ZIP footprint
data/global.yaml                section order per page type, shared footer
data/themes.yaml                colour + type tokens
data/qa-rules.yaml              operator-editable severities, each with a `why`
```

**Business ≠ Site.** A business can run on several domains; a domain can be re-composed without
touching the business record. That orthogonality is what makes everything else possible.

### 2. The fact block is a closed world

```yaml
facts:
  years_in_business: 15
  free_estimates: true
  licensed: false          # <- not claimed, because not supplied
  insured: false
  emergency_24_7: false
  warranty_years: null
```

A library block declares what it asserts:

```yaml
- title: "Fully insured crews"
  text: "..."
  requires: [insured]
```

The composer **removes that block from the pool before selection** whenever the fact is missing
or false. Fabrication is therefore structurally impossible for library copy — not merely
detected afterwards. The QA claim scanner then re-checks the rendered output as a second net.

> *A fabricated credential is a legal problem, not a style problem.*

### 3. Composition, not rewriting

```
site.composition_seed = 7

for every copy slot:
    pool  = library[niche][slot.kind]
    index = stable_hash(slot_key) + seed × PRIME   (mod len(pool))
```

Deterministic (splitmix64, not `random`, so it does not drift between Python versions),
independent per slot, and distinct on multi-picks. Twelve slots at fifteen options each is
~10¹³ compositions from a library that costs about **one** of Engine B's per-site rewrites to
produce.

### 4. Fan-out over two axes

```
FIXED       /  /about  /contact  /services  /areas
SERVICE     /services/<service>                    × S
COUNTY      /areas/<county>-county-<st>            × C
LOCATION    /areas/<city>-<st>                     × L        ← axis 1
MONEY       /services/<service>/<city>-<st>        × S × L    ← axis 1 × axis 2
```

Every one comes from a master carrying `{city} {state} {county} {zips_short} {nearby} {service}
{company} {phone} …`. **Build cost is O(masters), not O(pages)** — Engine B's key property,
generalised from one axis to two, with `location_variants` masters instead of one so a site has
N distinct city-page skeletons rather than a single repeated one.

`[[ ... ]]` marks a clause that disappears when its tokens are empty, so a homepage never ships
`Roofing for{CITY}, {STATE}`.

### 5. One build, two render modes

```yaml
render:
  mode: hybrid
  prerender_top_n: 250     # null = every page a file; 0 = everything edge
  location_variants: 4
```

The Worker tries the static asset first and only falls back to rendering, so the two modes are a
**dial, not a fork**. One wildcard DNS record, no origin process, no port per site.

### 6. Verification

| Layer | What it does |
|---|---|
| **Structural** | visible `{token}` on a rendered page → blocker; referenced assets exist; assets validated by **magic bytes**, never by size |
| **Semantic** | foreign identity, out-of-coverage geography, unsupported claims, secrets |
| **Uniqueness** | MinHash over 5-word shingles vs every shipped site in the niche |
| **Layout** | `foundry sweep` — six widths, reports the offending element's DOM path |

Every report ends with `not verified:` — the blind spots it did **not** cover. A green result
that hides its blind spots is worse than a red one.

Failures explain themselves. `diagnose()` returns cause, fix, and `auto_handled` — and when
`auto_handled` is true, a guard missed a case, so the instruction is *investigate*, not *re-fix*.

### 7. The LLM has no tools

```
claude -p "<instruction + INPUT JSON>" --tools "" --output-format json …
```

The model cannot open a file. "Do not touch anything else" is a capability it lacks rather than
an instruction it might ignore. Shape validation, immutable fields, token preservation,
`[[ ]]` balance, **exactly one retry** carrying the specific failures — all kept from Engine B.

What changed is the destination: output goes into the **block library**, so the cost amortises
across every site that ever draws it instead of being paid again per site.

---

## Previewing locally

`foundry serve` (and `BUILD-AND-VIEW.command`) starts **one server per site**, each rooted at
that site's own folder, plus an index page linking to them. That is not a nicety — it is the only
correct way to serve them. See scar 3.

City pages that were not pre-rendered are produced on the fly by the same logic `worker.js` uses
at the edge, so a hybrid site is fully browsable locally and what you see is what Cloudflare will
serve. Responses carry `X-Foundry-Render: edge` when that path was taken.

## Ten scars already recorded

Kept here because a lesson learned once should be encoded once.

**The sweep measured unstyled HTML.** Its first version loaded pages over `file://`, where a
root-absolute `/assets/css/site.css` resolves to the filesystem root and never arrives. It
confidently reported seventeen overflows that do not exist. The probe now runs over HTTP against
each site root — the transport production actually uses. *Same failure class as checking a
substitution instead of the rendered output.*

**Serving `dist/` directly made every page look broken.** Pages ask for
`/assets/css/site.css` — root-relative, because in production each site is the root of its own
domain. Serving the parent folder and browsing to `/bennettroofers.com/` looked for
`dist/assets/css/site.css`, which does not exist, so the stylesheet and every image 404'd and
every page rendered as raw unstyled HTML. **This is the same defect the layout sweep had — found
there, fixed there, and left standing in the one place a human actually looks.** Fixing a bug in
the checker and not in the viewer is worse than not finding it. Now recorded as the
`subpath-assets` diagnose rule.

**Ten thousand titles ran two characters long.** `{niche} in {county}, {state_abbr} | {company}`
is fine until the county and the company are both long — "Pest Control in San Bernardino County,
CA | Statewide Pest Solutions" is 68 characters, and a three-state build tripped it on 10,145
pages at once. Titles are now fitted by dropping the **brand suffix** first, then shortening it,
never by truncating the subject: the city is what earns the click, so the company name is what
goes.

**A blocker fired on its own coverage list.** The out-of-area rule matched at most two
capitalised words, so `Port Saint Lucie` was clipped to `Port Saint`, not found in the covered
set, and reported as foreign geography - on a site that covers it. A gate that cries wolf about
your own data is worse than no gate, because it teaches you to ignore the ones that matter.

**A directory section that did not scale.** `coverage_map` listed every county with ten cities
each — fine at 6 counties, and about 9,000 links per page at 928. A 3,741-page national build
came to **222 MB**. It now shows the page's own state first and links onward: **54.9 MB**, same
page count. Worth recording because it passed every gate — no overflow, no contrast failure, no
broken link — and was still wrong. Nothing in the QA set measures page weight; that is a real
blind spot, now named.

**White text on a white background — the exact defect Engine B shipped.** The banner-hero pack
sets `.lede` to white, and the rule was written `body[data-hero=banner] .lede` rather than
`body[data-hero=banner] .hero .lede`. It therefore applied to *every* `.lede` on the page,
including the one inside a white tab panel. The build passed, the copy was correct, the layout
sweep was clean, and a paragraph was invisible. **Text was checked. Pixels were not.** The sweep
now runs a WCAG contrast probe over every text node, computing the effective colour against the
effective background — 344 failures on the first run, zero now.

**The sweep called its own carousel a bug.** Slides inside a horizontal scroller legitimately
extend past the viewport. Reporting them buried the real findings, which is how a probe stops
being trusted. It now skips elements inside `overflow-x: auto|scroll` — but deliberately *not*
`hidden`, because that clips content the reader can never reach, which is a real defect wearing a
tidy costume.

**`lstrip` ate a domain.** The first bulk run turned `summitlineroofing.com` into
`ummitlineroofing.com` and wrote it into a real site record without complaint.
`str.lstrip("https://")` strips any leading character **in that set**, not the prefix — and the
set contains `s`. Fixed with an explicit `startswith` loop. Worth keeping because it is silent,
it looks like a typo, and it would have reached a live DNS record.

**The first build was blocked by its own asset gate.** `hero.jpg` did not exist, the renderer
emitted a labelled placeholder, and `images-exist` refused to ship it. That is the intended
behaviour: a missing asset must be loud, never a broken icon and never a plausible-looking wrong
photo from another niche.

---

**A delete stopped a build.** `shutil.rmtree` reads a directory, deletes what it saw, then
`rmdir`s it — and on macOS something can put a file back in between: Finder recreating
`.DS_Store` in a folder you have open, iCloud Drive rematerialising an evicted file. The rmdir
then fails with `[Errno 66] Directory not empty`. It surfaced on the directory skeleton first
because `/areas/` is thousands of folders, so the walk is long enough to be interrupted. The old
output is now **renamed aside in one atomic syscall** and deleted best-effort afterwards; a build
should never fail because of a delete. Recorded as the `stale-output` rule.

**The button worked; it just looked exactly like a button that does not.** `/build` did all the
work synchronously, so eight sites meant Flask answered 62 seconds later and the browser sat on a
blank pending request the whole time — and every other click in that tab looked dead too, because
the tab was still waiting on the first navigation. Builds now run on a worker thread and the page
redirects immediately to a status view with a progress bar and per-site chips. *A page that takes
a minute to arrive is indistinguishable from a broken one.*

**One page was built twice, and the SEO agent blamed the wrong thing.** A site listed
`gutter-installation` twice under `services`, so every page that service owns was generated
twice at the same URL — thirty-six of them. On disk the second write silently won; in the sitemap
the URL appeared twice; and the only visible symptom was the SEO agent reporting thirty-six
duplicate titles with the explanation *"almost always a missing {state} token"*, which is true
for two different pages and completely wrong here. **The evidence line even printed the same URL
twice — `/services/gutter-installation, /services/gutter-installation` — and the message still
said to go looking in the templates.** Fixed at four levels, because the defect could enter at
any of them: the panel filters posted services to the chosen niche and de-duplicates server side
(three slugs are shared by two niches each, and a browser restoring form state on a Back
navigation re-ticks the hidden twin); the graph de-duplicates whatever is in the record; the
renderer **refuses to write** when two pages want one path; and the SEO agent now separates
"same title, two URLs" from "same URL, twice" and says which it found. *A diagnosis that is
confidently wrong costs more than no diagnosis.*

**One town, two spellings, two pages.** The buyer's feed contains `Winston Salem` and
`Winston-Salem`, `Coeur D Alene` and `Coeur d'Alene`, `Mckinney` and `McKinney`. Coverage
bucketed on the lowercased name, so those were two locations — with the same slug, and therefore
the same URL, and therefore the same silent overwrite, with half the ZIPs on whichever copy lost.
Coverage now buckets on the **slug**, which is the URL, which is the identity that matters; the
richer spelling wins (punctuation, then an interior capital, then alphabetically, so it never
depends on which file was read first) and both spellings' ZIPs merge. Four towns across the
193,308-row feed were affected.

**The pages were thin, and the library was only a third of the reason.** A city page came out at
844 words. Three things caused it and only one was obvious. The location page — the highest-volume
page type on every site — was the *only* page type with no signs, no process, no cost and no
comparison section, all of which already existed, were already composed, and were already on the
service page. The per-page draw counts were set for a shallow library: three FAQs on a city page,
two reviews. And the blocks themselves ran about half the length they should — FAQ answers at a
median of 35 words. Fixed in that order: the sections were given to the location page, the counts
were raised, and the library was rewritten longer and deeper (`_base` 232 → 1,302 blocks, roofing
222 → 891). The city page is now 2,604 words.

**Adding content made the uniqueness gate fire, which was the tool working.** Drawing more blocks
per page from the same shallow pool pushed two roofing sites from 20.3% composed-copy similarity to
26.6% — over the 25% wall — and the build stopped. That is the correct behaviour and it is the
number that tells you the pool is too small for the number of sites you want from it. After the
library rewrite the same pair measures **1.6–10.2%**. The rule to take from this: *page depth and
site count draw on the same budget, and the only way to buy more of both is more blocks.*

## Next

1. `foundry fill` at volume — the demo library is deliberately shallow, and the composed-copy
   number (26.6% between two seeds) is the tool telling you so. Deeper pools drop it directly.
2. Novelty-tiered approval: human on a new template or block batch, auto-ship on a new placement.
3. Promotion with Engine B's ordering — irreversible steps last, tested rollback — but for static
   artifacts, so there is no process and no port to allocate.
4. **Close the loop.** Stamp pages with their block IDs at render, ingest postbacks, rank blocks
   by conversion, retire losers, ask the LLM for more like the winners. Both parent engines are
   open-loop; this is the only part that makes the next thousand pages better than the last.
