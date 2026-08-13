"""
Foundry — the renderer.

Deterministic: same content graph in, byte-identical output out. There is no
cache, `dist/` is disposable, and nothing is ever patched in place. That single
property is what removes the need for Engine B's entire in-place-mutation
guarantee stack (source guard, ownership hashing, substitution proof) — you
cannot scribble outside your scope if you never edit, only emit.

Two render modes from ONE build, because the Worker tries the static asset
first and only falls back to rendering:

    prerender_top_n = 0     only fixed pages are files; the long tail is edge
    prerender_top_n = 250   fixed + services + counties + 250 busiest cities
    prerender_top_n = null  every page is a real file (host anywhere, no Worker)

So "static or edge" is a dial, not a fork, and Engine B's ~59-live-sites ceiling
(one pm2 process and one port each) simply does not exist here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .graph import FoundryError, Graph, Location, load_graph, slugify
from .images import (SLOTS, copy_into_site, pool_counts, pool_for)
from .library import (GENERIC, Composer, interpolate, load_library,
                      unresolved_tokens)

# Sentinels the edge Worker substitutes at request time.
EDGE = {
    "city": "%%city%%", "city_slug": "%%city_slug%%", "state": "%%state%%",
    "state_abbr": "%%state_abbr%%", "county": "%%county%%",
    "zips_short": "%%zips_short%%", "nearby": "%%nearby%%", "slug": "%%slug%%",
}

IMAGE_MAGIC = {
    b"\xff\xd8\xff": "jpeg", b"\x89PNG\r\n\x1a\n": "png",
    b"GIF87a": "gif", b"GIF89a": "gif", b"<svg": "svg", b"<?xm": "svg",
}


@dataclass
class Page:
    url: str
    kind: str
    title: str
    description: str
    html: str = ""
    prerendered: bool = True

    @property
    def out_path(self) -> str:
        if self.url == "/":
            return "index.html"
        if self.url.endswith(".xml") or self.url.endswith(".txt"):
            return self.url.lstrip("/")
        return self.url.strip("/") + "/index.html"


@dataclass
class BuildResult:
    site_id: str
    domain: str
    out_dir: Path
    pages: list[Page] = field(default_factory=list)
    edge_pages: int = 0
    assets_copied: int = 0
    placeholders: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    composition: dict[str, Any] = field(default_factory=dict)
    copy_text: str = ""
    blocks_text: str = ""
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def total_pages(self) -> int:
        return len(self.pages) + self.edge_pages


# --------------------------------------------------------------------------
# context
# --------------------------------------------------------------------------

def _phone_digits(phone: str) -> str:
    return re.sub(r"[^0-9+]", "", str(phone or ""))


TITLE_LIMIT = 62


def fit_title(title: str, brand: str = "") -> str:
    """Keep a title inside the SERP width by dropping the least useful part.

    The pattern `{niche} in {county}, {state_abbr} | {company}` is fine until
    the county and the company are both long — "Pest Control in San Bernardino
    County, CA | Statewide Pest Solutions" is 68 characters, and 10,145 pages
    of a three-state build tripped it at once.

    The subject of the page is what earns the click, so the BRAND SUFFIX is what
    goes: first shortened to the brand, then dropped entirely. Truncating the
    other way would cut the city out of a city page.
    """
    if len(title) <= TITLE_LIMIT or " | " not in title:
        return title
    head, _, _tail = title.rpartition(" | ")
    if brand and len(f"{head} | {brand}") <= TITLE_LIMIT:
        return f"{head} | {brand}"
    return head if len(head) <= TITLE_LIMIT else head[:TITLE_LIMIT - 1].rstrip(" ,-|") + "…"


def base_context(g: Graph) -> dict[str, Any]:
    b, f = g.business, g.business.get("facts") or {}
    addr = b.get("address") or {}
    return {
        "company": b["company"],
        "brand": b.get("brand") or b["company"],
        "phone": b["phone"],
        "phone_link": _phone_digits(b["phone"]),
        "email": b.get("email", ""),
        "domain": g.site["domain"],
        "street": addr.get("street", ""),
        "hq_city": addr.get("city", ""),
        "hq_state": addr.get("state", ""),
        "hq_zip": addr.get("zip", ""),
        "address": ", ".join(x for x in [addr.get("street"), addr.get("city"),
                                         addr.get("state"), addr.get("zip")] if x),
        "years": str(f.get("years_in_business") or ""),
        "hours": f.get("hours", ""),
        "year": str(date.today().year),
        "niche": g.niche.get("label", g.site["niche"]),
        "niche_lower": g.niche.get("label", g.site["niche"]).lower(),
        # location + service tokens default to empty so [[ ... ]] clauses drop
        "city": "", "city_slug": "", "state": "", "state_abbr": "",
        "county": "", "zips_short": "", "nearby": "", "slug": "",
        "service": "", "service_slug": "", "service_lower": "",
    }


def location_context(base: dict, loc: Location, g: Graph) -> dict:
    ctx = dict(base)
    near = g.nearby(loc)
    ctx.update({
        "city": loc.city, "city_slug": slugify(loc.city), "state": loc.state,
        "state_abbr": loc.state_abbr, "county": loc.county, "slug": loc.slug,
        "zips_short": loc.zips_short(),
        "nearby": ", ".join(l.city for l in near),
    })
    return ctx


def service_context(base: dict, service: dict) -> dict:
    ctx = dict(base)
    ctx.update({
        "service": service["name"],
        "service_slug": service["slug"],
        "service_lower": service["name"].lower(),
    })
    return ctx


# --------------------------------------------------------------------------
# composed content
# --------------------------------------------------------------------------

def compose_site_content(g: Graph, comp: Composer) -> dict[str, Any]:
    """Everything drawn once per site and reused across its pages."""
    counts = g.globals.get("counts") or {}
    return {
        "tagline":  comp.one("taglines", "site:tagline"),
        "hero_intro": comp.one("hero_intros", "site:hero"),
        "hero_cta": comp.one("hero_ctas", "site:cta"),
        "trust":    comp.many("trust_points", "site:trust", counts.get("trust", 4)),
        "about":    comp.one("about_paras", "site:about"),
        "why_us":   comp.many("why_us", "site:why", counts.get("why_us", 8)),
        "process":  comp.many("process_steps", "site:process", counts.get("process", 6)),
        "faqs":     comp.many("faqs", "site:faq", counts.get("faqs", 10)),
        "reviews":  comp.many("reviews", "site:reviews", counts.get("reviews", 5)),
        "cta":      comp.one("cta_blocks", "site:cta_block"),
        "closing":  comp.one("closing_paras", "site:closing"),
        # GENERIC: the home page is not one service, so it draws only untagged
        # signs/compare/costs — a service-tagged block must never surface here.
        "signs":    comp.many("signs", "site:signs", counts.get("signs", 6), service=GENERIC),
        "compare":  comp.many("compare_rows", "site:compare", counts.get("compare_rows", 5), service=GENERIC),
        "costs":    comp.many("cost_factors", "site:costs", counts.get("cost_factors", 5), service=GENERIC),
    }


def compose_service_content(comp: Composer, slug: str, counts: dict | None = None) -> dict[str, Any]:
    counts = counts or {}
    return {
        # The hero on a service page used to be the site-level hero_intro — the
        # same lede on all 16 service pages, only the H1 changing. This is the
        # service's OWN hero, keyed by slug, so roof-inspection and
        # gutter-installation open differently. Distinct pool from the body
        # intro, so the two are never the same paragraph on one page.
        # service=slug: every service-scoped draw prefers blocks tagged `for: slug`
        # and falls back to generic copy, so roof-replacement pages carry
        # roof-replacement content and never another service's.
        "hero":    comp.one("service_heroes", f"service:{slug}:hero", service=slug),
        "intro":   comp.one("service_intros", f"service:{slug}", service=slug),
        "bullets": comp.many("service_bullets", f"service:{slug}:bullets",
                             counts.get("service_bullets", 6), service=slug),
        "faqs":    comp.many("faqs", f"service:{slug}:faq", counts.get("service_faqs", 7)),
        # Shared sections, now service-aware. On a service page (and a
        # service-in-city page) the template prefers these over the site-level /
        # per-city ones, so the signs, comparison and cost drivers speak to THIS
        # service rather than the trade in general.
        "signs":   comp.many("signs", f"service:{slug}:signs",
                             counts.get("service_signs", 6), service=slug),
        "costs":   comp.many("cost_factors", f"service:{slug}:costs",
                             counts.get("service_costs", 6), service=slug),
        "compare": comp.many("compare_rows", f"service:{slug}:compare",
                             counts.get("service_compare", 5), service=slug),
    }


def compose_location_content(comp: Composer, bucket: int,
                             counts: dict | None = None) -> dict[str, Any]:
    """
    Keyed by BUCKET, not by city.

    This is Engine B's master-template insight — one master fans out over every
    location, so build cost is independent of location count — with one
    improvement: `location_variants` masters instead of one, picked by a stable
    hash of the city slug. A site therefore has N distinct city-page skeletons
    rather than one, at N times the cost of one rather than L times.
    """
    counts = counts or {}
    return {
        # The city page's own hero, keyed by bucket, interpolating {city}/{county}.
        # One block becomes 500 distinct rendered heroes because the city name
        # differs; `location_variants` buckets multiply that again. This is why a
        # 500-city site does not need 500 hero blocks — pool × interpolation ×
        # variants is the effective count.
        "hero":          comp.one("location_heroes", f"loc:{bucket}:hero"),
        "intro":         comp.one("location_intros", f"loc:{bucket}"),
        "service_intro": comp.one("location_service_intros", f"locsvc:{bucket}"),
        "faqs":          comp.many("faqs", f"loc:{bucket}:faq", counts.get("location_faqs", 8)),
        "reviews":       comp.many("reviews", f"loc:{bucket}:reviews",
                                   counts.get("location_reviews", 4)),
        # Drawn PER BUCKET, not once for the site. The city page is the page that
        # has to rank, and if every city carried an identical signs/costs/compare
        # block the whole city set would read as one page repeated — which is
        # exactly the thin-content signal these sections were added to remove.
        # GENERIC: a city page covers the whole trade in that place, not one
        # service, so it draws only untagged signs/costs/compare — service-tagged
        # blocks stay on their own service pages.
        "signs":         comp.many("signs", f"loc:{bucket}:signs",
                                   counts.get("location_signs", 6), service=GENERIC),
        "costs":         comp.many("cost_factors", f"loc:{bucket}:costs",
                                   counts.get("location_costs", 6), service=GENERIC),
        "compare":       comp.many("compare_rows", f"loc:{bucket}:compare",
                                   counts.get("location_compare", 5), service=GENERIC),
        "process":       comp.many("process_steps", f"loc:{bucket}:process",
                                   counts.get("location_process", 5)),
    }


def compose_all(root: Path, g: Graph, seed: int, variants: int):
    """Draw every block for a site. Shared by the renderer and by seed search.

    Seed search needs to know what a candidate seed would produce, and it needs
    to know it for a few hundred candidates. Rendering to find out would cost a
    second each; composing costs microseconds, and the composed blocks are the
    only thing a seed actually changes.
    """
    lib = load_library(root, g.site["niche"], g.facts)
    counts = g.globals.get("counts") or {}
    comp = Composer(lib, seed)
    site_content = compose_site_content(g, comp)
    svc_content = {s["slug"]: compose_service_content(comp, s["slug"], counts)
                   for s in g.services}
    loc_content = {b: compose_location_content(comp, b, counts) for b in range(variants)}
    return comp, site_content, svc_content, loc_content


def composition_text(root: Path, g: Graph, seed: int, variants: int = 4) -> str:
    _, site_content, svc_content, loc_content = compose_all(root, g, seed, variants)
    return json.dumps([site_content, svc_content, loc_content],
                      sort_keys=True, default=str)


def choose_images(root: Path, g: Graph, seed: int) -> tuple[dict, dict[str, str]]:
    """Pick this site's pictures from the niche pool, deterministically.

    Images are the fourth uniqueness axis, and the most visible one: copy can
    diverge perfectly and two sites still read as the same site if they share a
    hero. Selection uses the same seeded stream as the copy, so a re-seed moves
    the pictures too.

    Public filenames are site-specific on purpose — see images.copy_into_site.
    """
    from .library import Stream, _hash64

    niche = g.site["niche"]
    token = hashlib.blake2b(g.site["site_id"].encode(), digest_size=3).hexdigest()
    chosen: dict[str, str] = {}          # public name -> pool file
    used: dict[str, Any] = {}            # what the templates read

    def pick(slot: str, key: str, public: str):
        pool = pool_for(root, niche, slot)
        if not pool:
            return None
        idx = Stream(_hash64(f"img::{slot}::{key}") ^ ((seed + 1) * 0x9E3779B97F4A7C15)) \
            .below(len(pool))
        pool_file = pool[idx]
        name = f"{public}-{token}{Path(pool_file).suffix}"
        chosen[name] = pool_file
        return f"/assets/img/{name}"

    used["hero"] = pick("hero", "site", "hero")
    used["about"] = pick("about", "site", "about")
    used["cta"] = pick("cta", "site", "cta")
    # Services take a DISTINCT set, not independent picks. Picking per service
    # meant three of six rows could land on the same photograph — which reads as
    # a broken page long before anyone thinks about uniqueness.
    show_pool = pool_for(root, niche, "showcase")
    used["showcase"] = {}
    if show_pool:
        order = Stream(_hash64("img::showcase") ^ ((seed + 1) * 0x9E3779B97F4A7C15)) \
            .shuffled(list(range(len(show_pool))))
        for n, sv in enumerate(g.services):
            pf = show_pool[order[n % len(order)]]
            name = f"svc-{sv['slug']}-{token}{Path(pf).suffix}"
            chosen[name] = pf
            used["showcase"][sv["slug"]] = f"/assets/img/{name}"
    else:
        used["showcase"] = {sv["slug"]: None for sv in g.services}
    gallery_pool = pool_for(root, niche, "gallery")
    if gallery_pool:
        order = Stream(_hash64("img::gallery") ^ ((seed + 1) * 0x9E3779B97F4A7C15)) \
            .shuffled(list(range(len(gallery_pool))))[:6]
        gal = []
        for n, i in enumerate(order):
            pf = gallery_pool[i]
            name = f"gal{n + 1}-{token}{Path(pf).suffix}"
            chosen[name] = pf
            gal.append(f"/assets/img/{name}")
        used["gallery"] = gal
    else:
        used["gallery"] = []
    return used, chosen


def bucket_of(slug: str, variants: int) -> int:
    if variants <= 1:
        return 0
    h = int.from_bytes(hashlib.blake2b(slug.encode(), digest_size=4).digest(), "big")
    return h % variants


# --------------------------------------------------------------------------
# assets
# --------------------------------------------------------------------------

def _validate_image(path: Path) -> str | None:
    """Magic bytes, not file size.

    Engine B shipped a live site with all 89 images broken because a Google
    Drive share link returned a 900 KB HTML sign-in page and the only check was
    "bigger than 512 bytes". Twenty-four files named .jpg were HTML, the build
    compiled, the copy was clean, and the homepage was a blank white rectangle.
    """
    try:
        head = path.open("rb").read(16)
    except OSError:
        return None
    for magic, kind in IMAGE_MAGIC.items():
        if head.startswith(magic):
            return kind
    if b"<svg" in head or b"<?xml" in head:
        return "svg"
    return None


def placeholder_svg(label: str) -> str:
    """A labelled grey box. A missing image is a readable diagnostic, never a
    broken icon and never a plausible-looking wrong photo."""
    safe = re.sub(r"[<>&]", "", label)[:40]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 500" role="img">'
        '<rect width="800" height="500" fill="#e2e5ea"/>'
        '<rect x="8" y="8" width="784" height="484" fill="none" stroke="#b6bcc6" '
        'stroke-width="3" stroke-dasharray="14 10"/>'
        f'<text x="400" y="245" text-anchor="middle" font-family="system-ui,sans-serif" '
        f'font-size="26" fill="#6a7280">missing asset</text>'
        f'<text x="400" y="285" text-anchor="middle" font-family="ui-monospace,monospace" '
        f'font-size="22" fill="#39404a">{safe}</text></svg>'
    )


def resolve_asset(root: Path, site_id: str, niche: str, name: str) -> Path | None:
    """Three-tier vault: site -> niche -> shared. Specific overrides general, so
    one photo uploaded once serves every site that does not override it."""
    for folder in (root / "assets" / "sites" / site_id,
                   root / "assets" / "niches" / niche,
                   root / "assets" / "shared"):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


# --------------------------------------------------------------------------
# the build
# --------------------------------------------------------------------------

def _env(root: Path) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(root / "templates")),
        undefined=StrictUndefined,      # a typo is a loud error, not a blank section
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["slug"] = slugify
    return env


TRASH_PREFIX = ".foundry-trash-"


def clear_output(out_dir: Path) -> None:
    """Empty a previous build without walking it.

    `shutil.rmtree` reads a directory, deletes what it saw, then rmdir's it. On
    macOS something else can put a file back between those two steps — Finder
    writing .DS_Store into a folder you have open, iCloud Drive rematerialising
    an evicted file, Spotlight, a sync client — and rmdir fails with
    ENOTEMPTY ([Errno 66] Directory not empty). The directory skeleton is where
    this shows up first because /areas/ is thousands of folders deep, so the
    walk is long enough for someone else to get a write in.

    A build should never fail because of a delete. So the old output is
    RENAMED aside — one atomic syscall, nothing to race — and the build carries
    on into a fresh directory immediately. Deleting the renamed copy is then
    best-effort: if it fails it is retried at the start of the next build, and
    if it fails forever it is a few megabytes in dist/, not a blocked site.

    Rename can still fail if dist/ sits on a different volume from its parent
    or is read-only, so rmtree remains the fallback — with retries, because the
    interfering write is usually a one-off.
    """
    parent = out_dir.parent
    parent.mkdir(parents=True, exist_ok=True)

    # Sweep anything a previous build could not delete. ignore_errors, always:
    # a leftover we cannot remove must not stop the build that found it.
    for stale in parent.glob(f"{TRASH_PREFIX}*"):
        shutil.rmtree(stale, ignore_errors=True)

    if not out_dir.exists():
        return

    trash = parent / f"{TRASH_PREFIX}{out_dir.name}-{os.getpid()}-{int(time.time())}"
    try:
        out_dir.rename(trash)
    except OSError:
        pass                      # cross-device, permissions — fall through
    else:
        shutil.rmtree(trash, ignore_errors=True)
        return

    last: OSError | None = None
    for attempt in range(3):
        try:
            shutil.rmtree(out_dir)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.25 * (attempt + 1))

    # Everything failed. Do not let stale pages from the last build survive
    # into this one — a half-cleared dist/ is worse than a refusal.
    raise FoundryError(
        f"Could not clear the previous build at {out_dir}: {last}",
        {"stale_output": True, "out_dir": str(out_dir)},
    )


def build_site(root: Path, site_id: str, out_root: Path | None = None) -> BuildResult:
    g = load_graph(root, site_id)
    site, niche_key = g.site, g.site["niche"]
    out_dir = (out_root or root / "dist") / site["domain"]
    clear_output(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    env = _env(root)
    tpl = env.get_template("page.html.j2")

    render_cfg = site.get("render") or {}
    top_n = render_cfg.get("prerender_top_n", None)
    variants = int(render_cfg.get("location_variants", 4))
    lib = load_library(root, niche_key, g.facts)

    single_service = len(g.services) == 1
    base = base_context(g)
    images, image_files = choose_images(root, g, int(site.get("composition_seed", 0)))
    comp, site_content, svc_content, loc_content = compose_all(
        root, g, int(site.get("composition_seed", 0)), variants)

    # Section order comes from the STYLE PACK first, falling back to global.
    # That is what makes structure a uniqueness axis rather than just a look:
    # two sites on different packs do not merely repaint, they re-order.
    # Section order belongs to the SKELETON. Style packs are visual only, which
    # is what makes the two multiply instead of repeat: sixteen looks x six
    # architectures, rather than sixteen fused pairs.
    sections = dict(g.globals.get("page_sections") or {})
    sections.update(g.skeleton.get("page_sections") or {})
    seo = g.niche.get("seo") or {}
    result = BuildResult(site_id=site_id, domain=site["domain"], out_dir=out_dir)
    copy_parts: list[str] = []

    def render(kind: str, url: str, ctx: dict, extra: dict, edge: bool = False) -> Page:
        title = fit_title(interpolate(seo.get(f"{kind}_title", "{company}"), ctx),
                          g.business.get("brand") or "")
        desc = interpolate(seo.get(f"{kind}_description", ""), ctx)
        payload = {
            "kind": kind, "url": url, "t": ctx, "site": site, "biz": g.business,
            "facts": g.facts, "theme": g.theme, "style": g.style,
            "skeleton": g.skeleton, "globals": g.globals,
            "niche": g.niche, "services": g.services, "content": site_content,
            "sections": sections.get(kind) or sections.get("default") or [],
            "title": title, "description": desc, "edge": edge,
            "counties": g.counties, "locations": g.locations, "imgs": images,
            "single_service": len(g.services) == 1,
            **extra,
        }
        html = interpolate(tpl.render(**payload), ctx)
        page = Page(url=url, kind=kind, title=title, description=desc, html=html)
        if not edge:
            body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
            copy_parts.append(re.sub(r"<[^>]+>", " ", body))
        return page

    # ---- fixed pages ----
    for kind, url in (("home", "/"), ("about", "/about"), ("contact", "/contact"),
                      ("services", "/services"), ("areas", "/areas")):
        result.pages.append(render(kind, url, base, {}))

    # ---- one page per service ----
    for svc in g.services:
        ctx = service_context(base, svc)
        result.pages.append(
            render("service", f"/services/{svc['slug']}", ctx,
                   {"service": svc, "svc": svc_content[svc["slug"]]}))

    # ---- one page per county ----
    for county in g.counties:
        ctx = dict(base)
        ctx.update({"county": county["name"], "state": county["state"],
                    "state_abbr": county["state_abbr"]})
        result.pages.append(
            render("county", f"/areas/{county['slug']}", ctx, {"county": county}))

    # ---- locations: prerender a prefix, edge-render the tail ----
    # Rank by PAYOUT first, then by ZIP count. When only some cities are
    # pre-rendered, the ones worth the most should be the static ones — they
    # are the pages a crawler and a buyer are most likely to hit.
    ranked = sorted(g.locations, key=lambda l: (-l.payout, -len(l.zips), l.city))
    limit = len(ranked) if top_n is None else max(0, int(top_n))
    prerender = {l.slug for l in ranked[:limit]}

    # A site narrowed to ONE service does not get service x city pages: with a
    # single service, /services/<it>/<city> and /areas/<city> say the same thing
    # about the same place, and two pages competing for one query is worse than
    # one page winning it. The city page IS the service page.
    money_services = [] if single_service else g.services

    for loc in g.locations:
        b = bucket_of(loc.slug, variants)
        lc = loc_content[b]
        if loc.slug in prerender:
            ctx = location_context(base, loc, g)
            result.pages.append(
                render("location", f"/areas/{loc.slug}", ctx, {"loc": loc, "lc": lc}))
            for svc in money_services:
                sctx = service_context(ctx, svc)
                result.pages.append(
                    render("location_service", f"/services/{svc['slug']}/{loc.slug}",
                           sctx, {"loc": loc, "lc": lc, "service": svc,
                                  "svc": svc_content[svc["slug"]]}))
        else:
            result.edge_pages += 1 + len(money_services)

    # ---- edge masters ----
    edge_needed = result.edge_pages > 0
    if edge_needed:
        _emit_edge(root, g, out_dir, base, loc_content, svc_content, sections,
                   seo, render, variants, ranked, prerender)

    # ---- machine files ----
    result.pages.extend(_sitemaps(g, result, ranked))
    result.pages.append(_robots(g))

    # ---- one page, one URL ----
    #
    # This is the last thing checked before anything touches disk, and it is a
    # refusal rather than a warning, because the failure it catches is silent by
    # construction: two pages with the same path means the second write wins,
    # the first page's content is gone, the sitemap lists the URL twice, and the
    # only symptom downstream is the SEO agent reporting a duplicate title and
    # blaming a missing {state} token. It cost an hour to trace once. It should
    # cost a build error from now on.
    seen: dict[str, int] = {}
    for page in result.pages:
        seen[page.out_path] = seen.get(page.out_path, 0) + 1
    clashes = sorted(p for p, n in seen.items() if n > 1)
    if clashes:
        raise FoundryError(
            f"{len(clashes)} URL(s) were generated more than once: "
            f"{', '.join(clashes[:5])}"
            f"{f' and {len(clashes) - 5} more' if len(clashes) > 5 else ''}",
            {"duplicate_url": True, "paths": clashes[:20]})

    # ---- write ----
    for page in result.pages:
        target = out_dir / page.out_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page.html, encoding="utf-8")

    copied_imgs = copy_into_site(root, g.site["niche"], out_dir, image_files)
    result.assets_copied, result.placeholders, warns = _emit_assets(root, g, out_dir)
    result.assets_copied += len(copied_imgs)
    missing_imgs = [n for n in image_files if n not in copied_imgs]
    if missing_imgs:
        warns.append(f"{len(missing_imgs)} pool image(s) selected but missing from "
                     f"assets/pool/{g.site['niche']}/ — run `foundry images fetch "
                     f"{g.site['niche']}`")
    result.warnings.extend(warns)
    result.composition = comp.coverage_note()
    result.copy_text = " ".join(copy_parts)
    # Copy-only signal: the composed blocks alone, with no navigation, no
    # service names and no city lists. Full-page similarity is what Google
    # sees and is the number that blocks; this one tells the operator whether
    # a high score came from the COPY (fixable: add blocks) or from shared
    # STRUCTURE (two sites on an identical coverage list genuinely do share
    # their location lists).
    result.blocks_text = json.dumps(
        [site_content, svc_content, loc_content], sort_keys=True, default=str)
    return result


SITEMAP_CHUNK = 45_000     # under the protocol's 50,000 cap, with headroom


def _sitemaps(g: Graph, result: BuildResult, ranked: list[Location]) -> list[Page]:
    """One sitemap, or an index plus shards once the cap is in sight.

    The protocol caps a sitemap at 50,000 URLs and 50 MB. A national footprint
    here is over 80,000 pages, and an oversized sitemap is not "mostly fine" —
    it is rejected outright and every URL past the cap is invisible. Splitting
    is not an optimisation; it is the difference between the long tail existing
    and not.
    """
    base = f"https://{g.site['domain']}"
    urls = [p.url for p in result.pages if not p.url.endswith((".xml", ".txt"))]
    have = set(urls)
    single = len(g.services) == 1
    for loc in ranked:                       # edge pages exist as URLs too
        for u in [f"/areas/{loc.slug}"] + ([] if single else
                                           [f"/services/{s['slug']}/{loc.slug}"
                                            for s in g.services]):
            if u not in have:
                urls.append(u)
                have.add(u)

    def urlset(chunk: list[str]) -> str:
        body = "\n".join(
            f"  <url><loc>{base}{'' if u == '/' else u}</loc></url>" for u in chunk)
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                f"{body}\n</urlset>\n")

    if len(urls) <= SITEMAP_CHUNK:
        return [Page(url="/sitemap.xml", kind="machine", title="", description="",
                     html=urlset(urls))]

    pages: list[Page] = []
    shards = [urls[i:i + SITEMAP_CHUNK] for i in range(0, len(urls), SITEMAP_CHUNK)]
    for n, chunk in enumerate(shards, 1):
        pages.append(Page(url=f"/sitemap-{n}.xml", kind="machine", title="",
                          description="", html=urlset(chunk)))
    index = "\n".join(f"  <sitemap><loc>{base}/sitemap-{n}.xml</loc></sitemap>"
                       for n in range(1, len(shards) + 1))
    pages.insert(0, Page(
        url="/sitemap.xml", kind="machine", title="", description="",
        html='<?xml version="1.0" encoding="UTF-8"?>\n'
             '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
             f"{index}\n</sitemapindex>\n"))
    return pages


def _robots(g: Graph) -> Page:
    # /_masters/ holds the edge templates, full of %%city%% sentinels. The Worker
    # 404s them, but a static host would serve them happily — and an indexed
    # master is the single most obvious "this site is generated" artefact there
    # is. Disallowed here as well, so the belt does not depend on the braces.
    txt = (f"User-agent: *\nAllow: /\nDisallow: /_masters/\n\n"
           f"Sitemap: https://{g.site['domain']}/sitemap.xml\n")
    return Page(url="/robots.txt", kind="machine", title="", description="", html=txt)


def _emit_edge(root, g, out_dir, base, loc_content, svc_content, sections, seo,
               render, variants, ranked, prerender) -> None:
    """
    Emit the Worker plus one master per (variant × page type) plus a compact
    location index. Build cost stays O(masters); page count is unbounded.
    """
    masters_dir = out_dir / "_masters"
    masters_dir.mkdir(parents=True, exist_ok=True)

    edge_ctx = dict(base)
    edge_ctx.update(EDGE)
    fake = Location(city=EDGE["city"], state_abbr="XX", county=EDGE["county"],
                    zips=[EDGE["zips_short"]])

    single_service = len(g.services) == 1
    for b in range(variants):
        lc = loc_content[b]
        page = render("location", f"/areas/{EDGE['slug']}", edge_ctx,
                      {"loc": fake, "lc": lc}, edge=True)
        (masters_dir / f"location-{b}.html").write_text(page.html, encoding="utf-8")
        for svc in ([] if single_service else g.services):
            sctx = service_context(edge_ctx, svc)
            sp = render("location_service",
                        f"/services/{svc['slug']}/{EDGE['slug']}", sctx,
                        {"loc": fake, "lc": lc, "service": svc,
                         "svc": svc_content[svc["slug"]]}, edge=True)
            (masters_dir / f"location_service-{b}-{svc['slug']}.html").write_text(
                sp.html, encoding="utf-8")

    index = {
        "variants": variants,
        "services": [] if single_service else [s["slug"] for s in g.services],
        "locations": {
            l.slug: [l.city, l.state, l.state_abbr, l.county, l.zips_short(),
                     ", ".join(n.city for n in g.nearby(l)), bucket_of(l.slug, variants)]
            for l in ranked if l.slug not in prerender
        },
    }
    (masters_dir / "index.json").write_text(json.dumps(index, separators=(",", ":")),
                                            encoding="utf-8")

    worker = _env(root).get_template("worker.js.j2").render(
        domain=g.site["domain"], root_folder="_root")
    (out_dir / "worker.js").write_text(worker, encoding="utf-8")
    (out_dir / "wrangler.toml").write_text(
        f'name = "{slugify(g.site["domain"])}"\n'
        f'main = "worker.js"\n'
        f'compatibility_date = "2026-01-01"\n\n'
        f'[assets]\ndirectory = "."\nbinding = "ASSETS"\n', encoding="utf-8")


def _emit_assets(root: Path, g: Graph, out_dir: Path) -> tuple[int, list[str], list[str]]:
    """Copy only the assets the rendered pages actually reference.

    Engine A's rule, and it matters at scale: a shared pool of reviewer photos
    copied into every site folder is thousands of redundant files.
    """
    referenced: set[str] = set()
    for html in out_dir.rglob("*.html"):
        referenced.update(re.findall(r'(?:src|href)="/assets/([^"?#]+)"',
                                     html.read_text(encoding="utf-8")))

    target = out_dir / "assets"
    target.mkdir(parents=True, exist_ok=True)
    copied, placeholders, warnings = 0, [], []

    # Which stylesheet ships is a per-site choice. The built-in sheet needs no
    # Node and is the default; `css_engine: tailwind` ships the compiled
    # Tailwind build instead. Both are the same filename on the site, so
    # nothing downstream has to care which one it is.
    engine = (g.site.get("css_engine") or "builtin").lower()
    css_src = root / "assets" / "css" / (
        "site.tailwind.css" if engine == "tailwind" else "site.css")
    if engine == "tailwind" and not css_src.is_file():
        warnings.append(
            "css_engine is 'tailwind' but assets/css/site.tailwind.css does not exist — "
            "shipped the built-in sheet instead. Run `python3 foundry.py css tailwind`.")
        css_src = root / "assets" / "css" / "site.css"
    if css_src.is_file():
        (target / "css").mkdir(exist_ok=True)
        shutil.copy2(css_src, target / "css" / "site.css")
        copied += 1
    js_src = root / "assets" / "js" / "site.js"
    if js_src.is_file():
        (target / "js").mkdir(exist_ok=True)
        shutil.copy2(js_src, target / "js" / "site.js")
        copied += 1

    for name in sorted(referenced):
        if name.startswith(("css/", "js/", "img/")):
            continue
        src = resolve_asset(root, g.site["site_id"], g.site["niche"], name)
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src is None:
            dest.with_suffix(".svg").write_text(placeholder_svg(name), encoding="utf-8")
            placeholders.append(name)
            continue
        kind = _validate_image(src)
        if kind is None:
            dest.with_suffix(".svg").write_text(placeholder_svg(f"{name} (not an image)"),
                                                encoding="utf-8")
            warnings.append(
                f"{name}: failed magic-byte validation — the file on disk is not an "
                f"image, whatever its extension says")
            continue
        shutil.copy2(src, dest)
        copied += 1

    return copied, placeholders, warnings


def build_all(root: Path, out_root: Path | None = None) -> list[BuildResult]:
    from .graph import list_sites
    return [build_site(root, sid, out_root) for sid in list_sites(root)]
