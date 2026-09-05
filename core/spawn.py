"""
Foundry — spawning new sites.

This is what turns the engine into a factory: the answer to "I want another one
like that, with different branding" and "I want forty of them."

THE SEED SEARCH IS THE POINT.

A site's copy is decided by one integer, `composition_seed`. Picking it by hand
is guesswork — Engine A picked variants by hand and could only check afterwards
that two sites carried different variant *ids*, which proves nothing about the
text. Foundry instead searches the seed space and MEASURES:

    for each candidate seed:
        compose the blocks   (microseconds — no rendering)
        Jaccard the 5-word shingles against every existing site in the niche
    keep the seed with the lowest worst-case overlap

Exact Jaccard rather than MinHash here: the sets are small enough that it is
both faster and more accurate than an estimate, and this number decides whether
a site is worth publishing.

When the best seed the search can find is still above the block threshold, that
is not a failure to report as an error — it is the library telling you it is too
shallow. The result says so, and names the kinds that are colliding, which is
exactly the input `foundry fill` consumes.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from .graph import FoundryError, Graph, list_sites, load_graph, slugify
from .library import _hash64
from .render import composition_text

_WORD = re.compile(r"[a-z0-9']+")

BLOCK_AT = 0.25
WARN_AT = 0.15

# The seed space the composer draws from. Any positive integer is a valid
# composition seed; we keep the default well inside a large range so two
# different sites almost never collide by chance.
SEED_SPACE = 1_000_000


def seed_from_identity(key: str) -> int:
    """A stable, well-spread default seed derived from a site's own identity.

    Why this exists: content selection is decided ENTIRELY by composition_seed
    (`Stream(h ^ ((seed+1)*GOLDEN) ...)`). Two sites that share a seed draw
    byte-identical blocks from the pool — only the business facts differ. The
    old default was a hard-coded `1` for every site, and `find_seed` returned
    `1` whenever a site had no siblings yet (the first site of a niche, or a
    site rebuilt right after its only sibling was deleted). The result: every
    such site got seed 1 and therefore identical copy.

    Deriving the default from the site's own id/domain gives each site a
    distinct seed the moment it is created — no sibling search required — so a
    fresh build, or a delete-then-rebuild, varies its content on its own.
    """
    return (_hash64(key or "foundry") % SEED_SPACE) + 1


def _shingles(text: str, n: int = 5) -> set[str]:
    words = _WORD.findall(text.lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class SeedResult:
    seed: int
    score: float
    nearest: str | None
    tried: int
    verdict: str            # "clear" | "warn" | "crowded"
    advice: str = ""
    ranked: list[tuple[int, float]] = field(default_factory=list)


def find_seed(root: Path, graph: Graph, *, exclude: str | None = None,
              candidates: Iterable[int] = range(1, 400),
              variants: int = 4, time_budget: float | None = 8.0) -> SeedResult:
    """Pick the composition seed whose copy overlaps least with its siblings.

    `time_budget` (seconds) caps the search so an interactive "Save and build"
    cannot hang the request: once the budget is spent, the best seed found so far
    is returned. Composing text for hundreds of candidates against many siblings
    is the slow part; the budget bounds it. Pass None for an exhaustive search
    (batch spawning, where wall-clock is not user-facing)."""
    niche = graph.site["niche"]
    deadline = (time.monotonic() + time_budget) if time_budget else None

    siblings: list[tuple[str, set[str]]] = []
    for site_id in list_sites(root):
        if site_id == exclude or site_id == graph.site["site_id"]:
            continue
        try:
            other = load_graph(root, site_id)
        except FoundryError:
            continue
        if other.site["niche"] != niche:
            continue
        seed = int(other.site.get("composition_seed", 0))
        v = int((other.site.get("render") or {}).get("location_variants", 4))
        siblings.append((site_id, _shingles(composition_text(root, other, seed, v))))

    # No siblings to differ from — the first site of a niche, or a site rebuilt
    # right after its only sibling was deleted. EVERY seed scores a perfect 0
    # here, so "search for the least-overlapping seed" is meaningless. The old
    # code broke on the first candidate and returned seed 1 for all of them,
    # which is exactly why fresh/rebuilt sites came out byte-identical. Instead
    # derive the seed from the site's own identity: distinct per site, stable on
    # rebuild, and already varied without anything to compare against.
    if not siblings:
        seed = seed_from_identity(graph.site["site_id"])
        return SeedResult(seed=seed, score=0.0, nearest=None, tried=1,
                          verdict="clear",
                          advice="first site of this niche — seeded from its own "
                                 "identity so its copy is already distinct.",
                          ranked=[(seed, 0.0)])

    ranked: list[tuple[int, float]] = []
    best = SeedResult(seed=1, score=1.0, nearest=None, tried=0, verdict="clear")

    for seed in candidates:
        shs = _shingles(composition_text(root, graph, seed, variants))
        worst, who = 0.0, None
        for site_id, other in siblings:
            score = _jaccard(shs, other)
            if score > worst:
                worst, who = score, site_id
        ranked.append((seed, worst))
        best.tried += 1
        if worst < best.score:
            best = SeedResult(seed=seed, score=worst, nearest=who,
                              tried=best.tried, verdict="clear")
        if worst == 0.0:
            break
        if deadline is not None and time.monotonic() >= deadline:
            # Budget spent — keep the best seed found so far rather than stalling
            # the request. The remaining candidates rarely beat it.
            break

    best.ranked = sorted(ranked, key=lambda r: r[1])[:8]
    if best.score >= BLOCK_AT:
        best.verdict = "crowded"
        best.advice = (
            f"the best of {best.tried} seeds still overlaps {best.score:.0%} with "
            f"{best.nearest}. The seed space is not the problem — the library is too "
            f"shallow for this many sites. Run `foundry fill {graph.site['niche']} "
            f"<kind>` to deepen the pools, then search again.")
    elif best.score >= WARN_AT:
        best.verdict = "warn"
        best.advice = (
            f"{best.score:.0%} against {best.nearest}. Publishable, but the pools are "
            f"getting thin — deepen the library before the next few sites.")
    elif not best.advice:
        best.advice = f"{best.score:.0%} against {best.nearest or 'nothing yet'} — clear."
    return best


# --------------------------------------------------------------------------
# writing records
# --------------------------------------------------------------------------

FACT_KEYS = ["years_in_business", "hours", "free_estimates", "licensed", "insured",
             "emergency_24_7", "warranty_years", "financing", "family_owned",
             "bbb_rating", "rating", "review_count", "awards", "certifications",
             "volume_claims"]


def blank_facts() -> dict:
    """Everything absent by default.

    A fact must be supplied deliberately. Any claim the record does not support
    is removed from the block pool before selection, so the safe default is the
    one where the business asserts nothing.
    """
    return {k: (None if k in ("hours",) else False) for k in FACT_KEYS}


def save_business(root: Path, data: dict) -> str:
    slug = slugify(data.get("slug") or data.get("company", ""))
    if not slug:
        raise FoundryError("a business needs a company name",
                           ctx={"incomplete_row": True, "field": "company"})
    for field_name in ("company", "phone"):
        if not str(data.get(field_name) or "").strip():
            raise FoundryError(
                f"missing {field_name} — an incomplete record is refused at the door, "
                f"never filled with a guess",
                ctx={"incomplete_row": True, "field": field_name})

    facts = blank_facts()
    facts.update({k: v for k, v in (data.get("facts") or {}).items() if k in FACT_KEYS})

    record = {
        "slug": slug,
        "company": data["company"].strip(),
        "brand": (data.get("brand") or data["company"]).strip(),
        "phone": data["phone"].strip(),
        "email": (data.get("email") or "").strip(),
        "address": {
            "street": (data.get("street") or "").strip(),
            "city": (data.get("city") or "").strip(),
            "state": (data.get("state") or "").strip(),
            "zip": (data.get("zip") or "").strip(),
        },
        "facts": facts,
    }
    # The business's own trade — the niche slug it belongs to (from its scraped
    # category on import). This is what a directory filters on, so a plumbing
    # directory lists only plumbing businesses. Kept only when known; the raw
    # category is retained for reference.
    niche = (data.get("niche") or "").strip()
    if niche:
        record["niche"] = niche
    category = (data.get("category") or "").strip()
    if category:
        record["category"] = category
    website = (data.get("website") or "").strip()
    if website:
        record["website"] = website
    folder = root / "data" / "businesses"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{slug}.yaml").write_text(
        yaml.safe_dump(record, sort_keys=False, allow_unicode=True), encoding="utf-8")
    # Keep the fast query DB in sync (YAML stays canonical; the DB is the index).
    try:
        from . import bizdb
        bizdb.upsert(root, {"slug": slug, **record})
    except Exception:
        pass
    return slug


def save_site(root: Path, data: dict) -> str:
    # NOT lstrip(): str.lstrip strips any leading character in the SET it is
    # given, not the prefix. "summitlineroofing.com".lstrip("https://") removes
    # the leading "s" and yields "ummitlineroofing.com" — which is exactly what
    # the first bulk run produced, silently, into a real site record.
    domain = (data.get("domain") or "").strip().lower()
    for prefix in ("https://", "http://", "www."):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    domain = domain.strip("/")
    if not domain:
        raise FoundryError("a site needs a domain",
                           ctx={"incomplete_row": True, "field": "domain"})
    site_id = data.get("site_id") or slugify(domain)

    record = {
        "site_id": site_id,
        "business": data["business"],
        "niche": data["niche"],
        "domain": domain,
        "theme": data.get("theme") or "slate",
        "style": data.get("style") or "classic",
        "skeleton": data.get("skeleton") or "standard",
        "composition_seed": int(data.get("composition_seed") or seed_from_identity(site_id)),
        "coverage": {"states": data.get("states") or [],
                     "cities": data.get("cities") or []},
        "render": {
            "mode": data.get("mode") or "hybrid",
            "prerender_top_n": data.get("prerender_top_n"),
            "location_variants": int(data.get("location_variants") or 4),
        },
    }
    if data.get("services"):
        record["services"] = data["services"]

    folder = root / "data" / "sites"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{site_id}.yaml").write_text(
        yaml.safe_dump(record, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return site_id


def save_directory(root: Path, data: dict) -> str:
    """Write a multi-business DIRECTORY site record (type: directory).

    Unlike save_site this has no single business/niche/skeleton — a directory
    lists many businesses. An optional `niche` filters the listing to providers
    that have a site in that trade; omit it to list every business.
    """
    domain = (data.get("domain") or "").strip().lower()
    for prefix in ("https://", "http://", "www."):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    domain = domain.strip("/")
    if not domain:
        raise FoundryError("a directory needs a domain",
                           ctx={"incomplete_row": True, "field": "domain"})
    site_id = data.get("site_id") or slugify(domain)

    record = {
        "site_id": site_id,
        "type": "directory",
        "domain": domain,
        "title": (data.get("title") or "").strip(),
        "tagline": (data.get("tagline") or "").strip(),
        "theme": data.get("theme") or "slate",
        "style": data.get("style") or "classic",
    }
    niche = (data.get("niche") or "").strip()
    if niche:
        record["niche"] = niche
    # Optional explicit pick of businesses (slugs); empty = list all matching.
    picked = [b for b in (data.get("businesses") or []) if b]
    if picked:
        record["businesses"] = picked
    # Optional cap on how many businesses to list per city.
    try:
        limit = int(data.get("per_city_limit") or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit > 0:
        record["per_city_limit"] = limit
    # Optional cap on the TOTAL businesses the directory lists (highest-rated
    # kept first; sponsored picks are always kept). Blank = list all matching.
    try:
        max_total = int(data.get("max_total") or 0)
    except (TypeError, ValueError):
        max_total = 0
    if max_total > 0:
        record["max_total"] = max_total
    # Up to 3 sponsored ("Best <trade>") businesses, shown first on every
    # provider page's related list.
    sponsored = [b for b in (data.get("sponsored") or []) if b][:3]
    if sponsored:
        record["sponsored"] = sponsored
    label = (data.get("sponsored_label") or "").strip()
    if label:
        record["sponsored_label"] = label
    # Where the public "Add your business" form posts (the panel's
    # /submit-business endpoint). Optional; a sensible default is applied at build.
    submit_url = (data.get("submit_url") or "").strip()
    if submit_url:
        record["submit_url"] = submit_url

    folder = root / "data" / "sites"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{site_id}.yaml").write_text(
        yaml.safe_dump(record, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return site_id


import shutil

# Prefix that core.render uses for build output it renames aside; a leftover of
# these is always disposable. Kept in sync by value, not import, to avoid a cycle.
_TRASH_PREFIX = ".foundry-trash-"


def _dir_bytes(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def delete_site(root: Path, site_id: str) -> None:
    """Remove a site RECORD and everything derived from it: its built output in
    dist/<domain> and any deploy worktree in .deploy/<site_id>. Deleting only the
    record (the old behaviour) left the whole build orphaned on disk forever."""
    path = root / "data" / "sites" / f"{site_id}.yaml"
    domain = ""
    if path.is_file():
        try:
            rec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            domain = (rec.get("domain") or "").strip()
        except Exception:
            domain = ""
        path.unlink()
    if domain:
        shutil.rmtree(root / "dist" / domain, ignore_errors=True)
    shutil.rmtree(root / ".deploy" / site_id, ignore_errors=True)


def list_orphan_builds(root: Path) -> list[dict]:
    """Built folders on disk with no live site behind them: dist/<domain> for a
    domain no current site uses, .deploy/<site_id> for a deleted site, and any
    leftover build-trash folders. Each entry carries its size so the panel can
    show what will be freed."""
    sites_dir = root / "data" / "sites"
    domains: set[str] = set()
    site_ids: set[str] = set()
    if sites_dir.is_dir():
        for p in sites_dir.glob("*.yaml"):
            site_ids.add(p.stem)
            try:
                rec = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:
                rec = {}
            if rec.get("domain"):
                domains.add(str(rec["domain"]).strip())

    orphans: list[dict] = []
    dist = root / "dist"
    if dist.is_dir():
        for d in dist.iterdir():
            if not d.is_dir():
                continue
            if d.name.startswith(_TRASH_PREFIX):
                orphans.append({"kind": "trash", "name": d.name, "path": str(d), "bytes": _dir_bytes(d)})
            elif d.name not in domains:
                orphans.append({"kind": "build", "name": d.name, "path": str(d), "bytes": _dir_bytes(d)})
    deploy = root / ".deploy"
    if deploy.is_dir():
        for d in deploy.iterdir():
            if d.is_dir() and d.name not in site_ids:
                orphans.append({"kind": "deploy", "name": d.name, "path": str(d), "bytes": _dir_bytes(d)})
    orphans.sort(key=lambda o: -o["bytes"])
    return orphans


def prune_orphan_builds(root: Path) -> tuple[list[dict], int]:
    """Delete every orphan list_orphan_builds finds. Returns (removed, bytes_freed)."""
    removed, freed = [], 0
    for o in list_orphan_builds(root):
        shutil.rmtree(o["path"], ignore_errors=True)
        if not Path(o["path"]).exists():
            removed.append(o)
            freed += o["bytes"]
    return removed, freed


def delete_business(root: Path, slug: str) -> list[str]:
    """Refuses while any site still points at it."""
    used = []
    for site_id in list_sites(root):
        raw = yaml.safe_load((root / "data" / "sites" / f"{site_id}.yaml")
                             .read_text(encoding="utf-8")) or {}
        if raw.get("business") == slug:
            used.append(site_id)
    if used:
        return used
    path = root / "data" / "businesses" / f"{slug}.yaml"
    if path.is_file():
        path.unlink()
    try:
        from . import bizdb
        bizdb.delete(root, slug)
    except Exception:
        pass
    return []


# --------------------------------------------------------------------------
# bulk
# --------------------------------------------------------------------------

@dataclass
class BulkRow:
    company: str = ""
    domain: str = ""
    phone: str = ""
    email: str = ""
    city: str = ""
    state: str = ""
    problem: str = ""

    @property
    def ok(self) -> bool:
        return not self.problem


def parse_bulk(text: str) -> list[BulkRow]:
    """One business per line:  Company, domain.com, phone, email, city, ST

    An incomplete line is REJECTED with a reason and never completed by
    guessing. Design defaults (theme, render mode) are filled in elsewhere —
    those are presentation choices. Facts about a real business are not.
    """
    rows: list[BulkRow] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if parts and parts[0].lower() in ("company", "name", "business"):
            continue                                    # a header line
        row = BulkRow()
        row.company = parts[0] if parts else ""
        row.domain = parts[1] if len(parts) > 1 else ""
        row.phone = parts[2] if len(parts) > 2 else ""
        row.email = parts[3] if len(parts) > 3 else ""
        row.city = parts[4] if len(parts) > 4 else ""
        row.state = parts[5] if len(parts) > 5 else ""
        missing = [n for n, v in (("company", row.company), ("domain", row.domain),
                                  ("phone", row.phone)) if not v]
        if missing:
            row.problem = "missing " + ", ".join(missing)
        elif "." not in row.domain:
            row.problem = f"{row.domain!r} does not look like a domain"
        rows.append(row)
    return rows


def create_from_rows(root: Path, rows: list[BulkRow], *, niche: str, states: list[str],
                     theme: str, mode: str, prerender_top_n: Any, style: str = "classic",
                     skeleton: str = "standard",
                     variants: int = 4, search_seeds: int = 250) -> list[dict]:
    """Create a business + a site per row, each with a searched seed.

    Seeds are searched one row at a time and the row is written before the next
    is searched, so row 2 is measured against row 1 rather than against an empty
    world. Creating forty sites that all differ from the originals but not from
    each other is the exact failure this ordering prevents.
    """
    out: list[dict] = []
    for row in rows:
        if not row.ok:
            out.append({"row": row, "status": "skipped", "detail": row.problem})
            continue
        try:
            slug = save_business(root, {
                "company": row.company, "phone": row.phone, "email": row.email,
                "city": row.city, "state": row.state,
            })
            site_id = save_site(root, {
                "business": slug, "niche": niche, "domain": row.domain,
                "theme": theme, "style": style, "skeleton": skeleton,
                "composition_seed": 1, "states": states,
                "mode": mode, "prerender_top_n": prerender_top_n,
                "location_variants": variants,
            })
            graph = load_graph(root, site_id)
            seed = find_seed(root, graph, exclude=site_id, variants=variants,
                             candidates=range(1, search_seeds + 1), time_budget=None)
            save_site(root, {
                "site_id": site_id, "business": slug, "niche": niche,
                "domain": row.domain, "theme": theme, "style": style, "skeleton": skeleton,
                "composition_seed": seed.seed, "states": states,
                "mode": mode, "prerender_top_n": prerender_top_n,
                "location_variants": variants,
            })
            out.append({"row": row, "status": "created", "site_id": site_id,
                        "seed": seed.seed, "score": seed.score,
                        "verdict": seed.verdict, "detail": seed.advice})
        except FoundryError as exc:
            out.append({"row": row, "status": "failed", "detail": str(exc)})
    return out


# --------------------------------------------------------------------------
# capacity — how many distinct sites the library can actually support
# --------------------------------------------------------------------------

def demand(services: int, variants: int, counts: dict | None = None) -> dict[str, int]:
    """How many blocks of each kind ONE site consumes.

    Mirrors core.render.compose_*. Worth stating explicitly, because the number
    that surprises people is `faqs`: it is drawn once for the site, again per
    service, and again per city-page variant. A pool of 18 against 34 draws is
    exhausted by a single site, and two sites then share almost all of it no
    matter which seed they use. The seed space cannot fix a shallow pool, and
    saying so before the build is more useful than blocking after it.
    """
    c = counts or {}
    return {
        "taglines": 1, "hero_intros": 1, "hero_ctas": 1, "about_paras": 1,
        "cta_blocks": 1, "closing_paras": 1,
        "trust_points": c.get("trust", 4), "why_us": c.get("why_us", 6),
        "process_steps": c.get("process", 4),
        "reviews": c.get("reviews", 3) + c.get("location_reviews", 2) * variants,
        "faqs": (c.get("faqs", 6) + c.get("service_faqs", 3) * services
                 + c.get("location_faqs", 3) * variants),
        "service_intros": services,
        "service_heroes": services,
        "service_bullets": c.get("service_bullets", 4) * services,
        "location_intros": variants,
        "location_heroes": variants,
        "location_service_intros": variants,
        "signs": c.get("signs", 6),
        "compare_rows": c.get("compare_rows", 5),
        "cost_factors": c.get("cost_factors", 5),
    }


def capacity(counts: dict[str, int], services: int, variants: int,
             page_counts: dict | None = None) -> dict[str, dict]:
    """pool vs demand per kind, and a rough ceiling on distinct sites.

    The ceiling is deliberately conservative: two sites drawing k of n blocks
    overlap heavily once k approaches n, so `pool / draws` is the number of
    genuinely non-overlapping draws available, not a promise.
    """
    need = demand(services, variants, page_counts)
    out: dict[str, dict] = {}
    for kind, draws in sorted(need.items()):
        pool = counts.get(kind, 0)
        out[kind] = {
            "pool": pool, "draws": draws,
            "sites": (pool // draws) if draws else 0,
            "short": pool < draws,
        }
    return out
