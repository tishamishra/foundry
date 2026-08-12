# Foundry — Product 3 · Technical Plan

**What it is:** a local pay-per-call lead-gen site factory that renders from a content graph
instead of cloning a codebase, composes copy from an LLM-fillable block library instead of
rewriting whole documents per site, measures uniqueness instead of assuming it, and ships static
artifacts with an edge fallback instead of one server process per site.

**Lineage:** Engine A's composition economics · Engine B's verification rigour · plus the
feedback loop neither has.

---

## 1. The four decisions this plan rests on

| Decision | Consequence |
|---|---|
| **Render from a content graph. Never clone.** | Deletes Engine B's entire in-place-mutation guarantee stack: source guard, ownership hashing, substitution proof, `proseOnly` swaps, swap-table explosion caps, `regenerates`, TS cast repairs, stale `.bak` leakage. ~3,000 lines of its hardest code becomes unnecessary. |
| **Compose at block granularity.** | Copy cost becomes O(blocks), not O(sites). Engine B pays $4.56 per site forever; Foundry pays it once per library and reuses it across every site. |
| **Static output with an edge fallback.** | No pm2 process, no port per site. Engine B's ~59-site ceiling (ports 3041–3099) disappears. `prerender_top_n` is a single dial from all-static to all-edge. |
| **Hand-authored CSS with custom properties.** | Engine A's precompiled-Tailwind constraint disappears with it — no `build.py css` step, no "a computed arbitrary value does not exist in the bundle" trap, no Node dependency at all. |

---

## 2. The content graph

Five kinds of YAML, each owning exactly one thing. Nothing is inferred from file contents.

```
NICHE      data/niches/<niche>.yaml       services, page graph, schema type, claim policy
LIBRARY    data/library/<niche>.yaml      the block pool — every kind, many options each
BUSINESS   data/businesses/<slug>.yaml    the entity + THE FACT BLOCK (the only facts that exist)
SITE       data/sites/<site_id>.yaml      the placement: business × niche × coverage × theme × seed
COVERAGE   data/coverage/<niche>/<ST>.csv the buyer's payable-ZIP footprint — a compliance constraint
GLOBAL     data/global.yaml               what every site shares
THEMES     data/themes.yaml               colour + type tokens
QA RULES   data/qa-rules.yaml             operator-editable severities, each with a plain-English `why`
```

**Business vs Site is Engine A's product/placement orthogonality, applied to lead-gen.** A
business can run on several domains; a domain can be re-pointed to a different composition
without touching the business record.

**The fact block is Engine B's anti-fabrication mechanism, promoted to a first-class field.**
`business.facts` is the closed world. Anything not in it does not exist — enforced at compose
time by the library's claim policy and again at QA time by a scanner over the rendered output.

---

## 3. The page graph and the fan-out

```
FIXED          /  ·  /about  ·  /contact  ·  /services  ·  /areas
SERVICE        /services/<service>                       × S services
COUNTY         /areas/<county>-county-<st>               × C counties
LOCATION       /areas/<city>-<st>                        × L locations      ← axis 1
MONEY PAGES    /services/<service>/<city>-<st>           × S × L            ← axis 1 × axis 2
MACHINE        /sitemap.xml  ·  /robots.txt
```

Every one of those is produced from **one master per page type**, carrying tokens:

```
{city} {city_slug} {state} {state_abbr} {county} {zip} {zips_short} {nearby}
{service} {service_slug} {service_lower}
{company} {phone} {phone_link} {domain} {email} {address} {years} {year}
```

**Build cost is O(masters), not O(pages)** — Engine B's key property, generalised from one axis
(geography) to two (geography × service), with the schema ready for a third (`{language}`).

---

## 4. The composition engine — the core innovation

Engine B rewrites whole documents per site. Engine A rotates pre-written blocks. Foundry does
Engine A's composition with Engine B's writer behind it.

```
site.composition_seed = 7

for every copy slot in every master:
    pool  = library[niche][slot.kind]
    index = stable_hash(slot_key)  +  seed × PRIME     (mod len(pool))
    block = pool[index]
```

- **Deterministic** — same seed, same site, byte-identical output. Engine A's property, kept.
- **Independent per slot** — each slot has its own stream, so two seeds differ in *every* slot,
  not in one.
- **Distinct multi-pick** — FAQs, testimonials and bullet lists use a seeded shuffle, so a site
  never draws the same block twice.
- **Combinatorial** — 12 slots × ~15 options each is ~10¹³ compositions from a library that costs
  about **one** of Engine B's per-site rewrites to produce.

Uniqueness is then **measured, not assumed** (§6). When the composer cannot find a combination
under the similarity threshold, it does not fail — it records exactly which slot kinds are
colliding, which is the input the LLM filler consumes.

---

## 5. Render modes — one output, one dial

The answer to "static or edge" is **both, from a single build**, because the Worker tries the
static asset first and only falls back to rendering:

```
prerender_top_n = ∞   →  every page is a real file        (host anywhere, no Worker needed)
prerender_top_n = 250 →  fixed + services + counties + 250 busiest cities are files,
                         the long tail renders at the edge from the master
prerender_top_n = 0   →  only fixed pages are files, everything else edge-rendered
```

Worker logic: `try static asset → 404 → match the path against the location index → substitute
the master → return with cache headers → unknown path → redirect to /`.

One wildcard DNS record per network (Engine A's trick), no origin process, and disk stays flat
regardless of location count.

---

## 6. Verification — three layers, all on rendered output

Engine B's lesson (*"text was checked exhaustively, pixels were never checked at all"*) and
Engine A's lesson (*"'looks fine on my screen' is not a claim that survives 200 sites"*) are the
same lesson. Foundry runs both halves.

| Layer | Checks |
|---|---|
| **Structural** | unresolved `{token}` visible on a rendered page → **blocker**; every internal link resolves; every referenced image exists; assets validated by **magic bytes**, not by size |
| **Semantic** | foreign identity leakage (another business's name/phone/domain/address); geography outside the coverage list; fabricated claims not present in the fact block; secrets |
| **Uniqueness** | MinHash signature of the composed copy vs every previously shipped site from the same niche — Engine B measured 60–91% for untreated clones and 3–6% for a real rewrite, so the thresholds start there |

Every report carries `not_verified` — the blind spots it did **not** cover. *A green result that
hides its blind spots is worse than a red one.*

`diagnose(message, ctx)` returns `{id, title, cause, fix, auto_handled}` so a failure explains
**why**, not where it stopped — and the knowledge lives in the software, not in a conversation.

---

## 7. The LLM — a library worker with no tools

Exactly Engine B's invocation, because it is the best decision in either project:

```
claude -p "<instruction + INPUT JSON>" --tools "" --output-format json ...
```

The model cannot open a file. "Do not touch anything else" stops being an instruction and
becomes a capability it does not have.

Kept verbatim from Engine B: shape validation (every leaf key path), immutable fields, token
preservation, `[[…]]` balance, **exactly one retry** carrying the specific failures, all-or-
nothing merge. Changed: the output goes into the **versioned block library**, tagged
`(niche, kind, run, model, timestamp)` — not into one site's data directory.

---

## 8. File structure

```
foundry/
├── foundry.py              CLI — build · check · serve · stats · new-site · fill
├── core/
│   ├── graph.py            content graph, coverage parsing, geography, location index
│   ├── library.py          block library, deterministic composition, N-axis interpolation
│   ├── render.py           renderer (static + edge), asset vault, Worker emit
│   ├── verify.py           similarity, QA rules, claim scanner, diagnose
│   └── llm.py              tool-free JSON transformer → library
├── templates/              base + partials + worker.js.j2
├── data/                   niches · library · businesses · sites · coverage · global · themes · qa-rules
├── assets/                 css/site.css · shared/
└── dist/                   output — disposable, regenerated from data
```

---

## 9. Build order

| Phase | Contents | Status |
|---|---|---|
| 1 | Content graph + coverage + geography | **this session** |
| 2 | Block library + composition + interpolation | **this session** |
| 3 | Renderer, both modes, asset vault, Worker | **this session** |
| 4 | Similarity + QA + diagnose | **this session** |
| 5 | Templates, CSS, roofing niche pack, demo sites | **this session** |
| 6 | CLI + tool-free LLM contract | **this session** |
| 7 | LLM library filling at volume | next |
| 8 | Playwright overflow sweep | next |
| 9 | Novelty-tiered approval + promotion | next |
| 10 | Closed loop: stamp → postback → rank → regenerate | next |

---

## 10. What "done" means for this session

Two sites built from the same niche and the same template, and a **measured** demonstration that
their copy diverges — the exact property Engine B had to spend $4.56 and 409 seconds per site to
achieve, reproduced deterministically at zero marginal cost.

Plus: zero unresolved tokens on any rendered page, a QA report that names its own blind spots,
and both render modes emitting a working output tree.
