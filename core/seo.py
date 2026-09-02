"""
Foundry — the SEO agent.

Runs after the build, against the RENDERED OUTPUT, and gates "shipped". A site
that passes QA but fails this is built and previewable; it is not shipped.

It exists because programmatic SEO fails in a small number of specific,
boring ways, and every one of them is machine-checkable:

  DUPLICATE TITLES.  The classic. Ten thousand city pages, one title pattern,
  and a missing {state} token — Engine B shipped exactly that, and recorded
  why it mattered: **499 city names on that box exist in more than one state.**
  Without the state, those pages share a title and Google picks one.

  A SITEMAP NOBODY CAN READ.  The protocol caps a sitemap at 50,000 URLs and
  50 MB. A national footprint here is 80,433 pages. A single oversized sitemap
  is not "mostly fine" — it is rejected, and every URL past the cap is invisible.

  ORPHANS.  A page in the sitemap that nothing links to is a page a crawler
  reaches last, or never. At this page count that is most of the site unless
  the internal linking is deliberate.

  NOINDEX SHIPPED BY ACCIDENT.  Engine B checked this six times across twelve
  stages and still shipped defects, because it was checked in the wrong place.
  It is checked once here, on the rendered page.

  PAGE WEIGHT.  Named as a blind spot when a directory section quietly pushed a
  build from 55 MB to 222 MB while passing every other gate. It is a gate now.

Every rule carries a `why`, every severity is operator-editable in
`data/seo-rules.yaml`, and the report declares what it did NOT check.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Google truncates around here. These are guides, not gospel, which is why they
# are warnings rather than blockers.
TITLE_MIN, TITLE_MAX = 25, 65
DESC_MIN, DESC_MAX = 70, 165
SITEMAP_MAX_URLS = 50_000
SITEMAP_MAX_BYTES = 50 * 1024 * 1024
PAGE_WEIGHT_WARN = 120 * 1024
PAGE_WEIGHT_BLOCK = 300 * 1024
# Per page type, because a contact page is legitimately short and a city page
# is not. One global floor would either excuse thin city pages or nag forever
# about a page whose job is to show a phone number.
WORD_FLOOR = {"location": 300, "location_service": 350, "service": 400,
              "county": 200, "home": 500, "about": 250, "contact": 120,
              "services": 250, "areas": 120}

_TAG = re.compile(r"<[^>]+>")
_WORD = re.compile(r"[A-Za-z0-9']+")


DEFAULT_RULES: dict[str, dict[str, Any]] = {
    "title-missing": {"severity": "blocker", "why":
        "A page with no title is a page Google titles for you, usually badly."},
    "title-duplicate": {"severity": "blocker", "why":
        "Two DIFFERENT pages with the same title compete with each other. On city "
        "pages this is almost always a missing {state} token — 499 US city names "
        "exist in more than one state."},
    "url-repeated": {"severity": "blocker", "why":
        "The same URL was generated more than once, so one copy overwrote the other "
        "on disk and the sitemap advertises it twice. This is not a copy problem and "
        "no amount of rewriting will fix it — something upstream is producing the "
        "page twice, usually a service or a city named twice in the site record."},
    "title-length": {"severity": "warning", "why":
        "Outside roughly 25-65 characters a title is either truncated in results or too "
        "thin to say anything."},
    "description-missing": {"severity": "warning", "why":
        "Without one, the snippet is scraped from the page and rarely reads as an offer."},
    "description-duplicate": {"severity": "warning", "why":
        "Identical descriptions across a city set are a strong thin-content signal."},
    "description-length": {"severity": "warning", "why":
        "Outside roughly 70-165 characters it is truncated or wastes the slot."},
    "h1-missing": {"severity": "blocker", "why":
        "Every page needs exactly one H1 stating what the page is about."},
    "h1-multiple": {"severity": "warning", "why":
        "More than one H1 splits the topic signal and usually means a template mistake."},
    "heading-skip": {"severity": "warning", "why":
        "Jumping H1 to H3 breaks the outline for assistive technology and for parsers."},
    "canonical-missing": {"severity": "blocker", "why":
        "Without a canonical, parameterised and duplicate URLs all compete."},
    "canonical-mismatch": {"severity": "blocker", "why":
        "A canonical pointing somewhere other than this page de-indexes this page. "
        "It is the single most destructive one-line mistake in technical SEO."},
    "noindex": {"severity": "blocker", "why":
        "A noindex left in from staging removes the page from the index entirely."},
    "keyword-missing": {"severity": "warning", "why":
        "A city page whose city is not in the title, the H1 and the opening copy is not "
        "really a city page."},
    "thin-content": {"severity": "warning", "why":
        "Below the floor for its page type, a page competes badly and reads as machine-made."},
    "link-broken": {"severity": "blocker", "why":
        "An internal link to nothing wastes crawl budget and loses the reader."},
    "orphan-pages": {"severity": "warning", "why":
        "A page in the sitemap that nothing links to is reached last or never."},
    "sitemap-oversize": {"severity": "blocker", "why":
        "The protocol caps a sitemap at 50,000 URLs and 50 MB. Past that it is rejected, "
        "and every URL beyond the cap is invisible."},
    "sitemap-mismatch": {"severity": "warning", "why":
        "A sitemap listing URLs that do not exist, or omitting ones that do, teaches a "
        "crawler to trust it less."},
    "robots-missing": {"severity": "warning", "why":
        "robots.txt should exist and name the sitemap."},
    "robots-disallow-all": {"severity": "blocker", "why":
        "A blanket Disallow shipped from staging hides the whole site."},
    "schema-invalid": {"severity": "blocker", "why":
        "JSON-LD that does not parse is JSON-LD that does nothing."},
    "schema-incomplete": {"severity": "warning", "why":
        "A LocalBusiness without a telephone or an areaServed is not doing the job it is "
        "there to do."},
    "image-alt": {"severity": "warning", "why":
        "Missing alt text costs accessibility and image search, and both are free."},
    "image-dimensions": {"severity": "warning", "why":
        "Images without width and height cause layout shift, which is a ranking input."},
    "page-weight": {"severity": "warning", "why":
        "Heavy pages are slow pages. This became a rule after a directory section pushed "
        "a build from 55 MB to 222 MB while passing every other gate."},
}


def _advisory() -> bool:
    """Advisory QA is ON unless FOUNDRY_STRICT_QA is explicitly truthy. In
    advisory mode SEO blockers become warnings, so a build ships and is
    deployable while the findings still show as items to improve."""
    return (os.environ.get("FOUNDRY_STRICT_QA") or "").strip().lower() not in (
        "1", "true", "yes", "on")


def load_rules(root: Path) -> dict[str, dict[str, Any]]:
    rules = {k: dict(v) for k, v in DEFAULT_RULES.items()}
    path = root / "data" / "seo-rules.yaml"
    if path.is_file():
        for key, override in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).items():
            rules.setdefault(key, {}).update(override or {})
    if _advisory():
        for spec in rules.values():
            if spec.get("severity") == "blocker":
                spec["severity"] = "warning"
    return rules


@dataclass
class SeoFinding:
    rule: str
    severity: str
    message: str
    why: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class SeoReport:
    site_id: str
    findings: list[SeoFinding] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    not_verified: list[str] = field(default_factory=list)

    @property
    def blockers(self) -> list[SeoFinding]:
        return [f for f in self.findings if f.severity == "blocker"]

    @property
    def warnings(self) -> list[SeoFinding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def passed(self) -> bool:
        return not self.blockers

    @property
    def score(self) -> int:
        """0-100. Blunt on purpose — it is a summary, the findings are the detail."""
        return max(0, 100 - 12 * len(self.blockers) - 3 * len(self.warnings))


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------

def _one(pattern: str, html: str, group: int = 1) -> str:
    m = re.search(pattern, html, re.I | re.S)
    return (m.group(group) or "").strip() if m else ""


def _attr(tag_pattern: str, html: str) -> str:
    """Read an attribute value with the QUOTE CHARACTER matched by backreference.

    `["\']...["\']` looks right and is wrong: it terminates on the first quote of
    either kind, so a description containing "Prince George's County" measured
    55 characters instead of 148 and the agent reported a length problem that
    did not exist. A gate that misreads the page is worse than no gate.
    """
    m = re.search(tag_pattern, html, re.I | re.S)
    return (m.group("v") or "").strip() if m else ""


def _text(html: str) -> str:
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", _TAG.sub(" ", body)).strip()


def extract(html: str) -> dict[str, Any]:
    headings = [(int(m.group(1)), _TAG.sub("", m.group(2)).strip())
                for m in re.finditer(r"<h([1-6])[^>]*>(.*?)</h\1>", html, re.I | re.S)]
    images = re.findall(r"<img\b([^>]*)>", html, re.I)
    return {
        "title": _one(r"<title[^>]*>(.*?)</title>", html),
        "description": _attr(
            r'<meta\s+name=(?P<q1>["\'])description(?P=q1)\s+content=(?P<q>["\'])(?P<v>.*?)(?P=q)', html),
        "canonical": _attr(
            r'<link\s+rel=(?P<q1>["\'])canonical(?P=q1)\s+href=(?P<q>["\'])(?P<v>.*?)(?P=q)', html),
        "robots": _attr(
            r'<meta\s+name=(?P<q1>["\'])robots(?P=q1)\s+content=(?P<q>["\'])(?P<v>.*?)(?P=q)', html),
        "lang": _attr(r'<html[^>]*\blang=(?P<q>["\'])(?P<v>.*?)(?P=q)', html),
        "headings": headings,
        "images": images,
        "links": re.findall(r'<a\b[^>]*href=["\']([^"\'#]+)["\']', html, re.I),
        "jsonld": re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>',
                             html, re.I | re.S),
        "text": _text(html),
        "bytes": len(html.encode("utf-8")),
    }


# --------------------------------------------------------------------------
# the audit
# --------------------------------------------------------------------------

def audit(root: Path, graph, result, *, link_sample: int = 400) -> SeoReport:
    rules = load_rules(root)
    rep = SeoReport(site_id=result.site_id)

    def add(rule: str, message: str, evidence: list[str] | None = None) -> None:
        spec = rules.get(rule, {"severity": "warning", "why": ""})
        if spec.get("severity") == "off":
            rep.not_verified.append(f"{rule}: switched off in data/seo-rules.yaml")
            return
        rep.findings.append(SeoFinding(
            rule=rule, severity=spec.get("severity", "warning"), message=message,
            why=spec.get("why", ""), evidence=(evidence or [])[:6]))

    pages = [p for p in result.pages if p.kind != "machine"]
    titles: dict[str, list[str]] = defaultdict(list)
    descs: dict[str, list[str]] = defaultdict(list)
    linked: set[str] = set()
    known: set[str] = {p.url for p in result.pages}
    heavy: list[str] = []
    problems = Counter()
    ev: dict[str, list[str]] = defaultdict(list)

    for page in pages:
        d = extract(page.html)
        url = page.url

        # --- metadata ---
        if not d["title"]:
            problems["title-missing"] += 1; ev["title-missing"].append(url)
        else:
            titles[d["title"]].append(url)
            if not (TITLE_MIN <= len(d["title"]) <= TITLE_MAX):
                problems["title-length"] += 1
                ev["title-length"].append(f"{url} ({len(d['title'])} chars)")
        if not d["description"]:
            problems["description-missing"] += 1; ev["description-missing"].append(url)
        else:
            descs[d["description"]].append(url)
            if not (DESC_MIN <= len(d["description"]) <= DESC_MAX):
                problems["description-length"] += 1
                ev["description-length"].append(f"{url} ({len(d['description'])} chars)")

        # --- indexability ---
        if "noindex" in (d["robots"] or "").lower():
            problems["noindex"] += 1; ev["noindex"].append(url)
        expected = f"https://{graph.site['domain']}{'' if url == '/' else url}"
        if not d["canonical"]:
            problems["canonical-missing"] += 1; ev["canonical-missing"].append(url)
        elif d["canonical"].rstrip("/") != expected.rstrip("/"):
            problems["canonical-mismatch"] += 1
            ev["canonical-mismatch"].append(f"{url} -> {d['canonical']}")

        # --- headings ---
        h1s = [t for lvl, t in d["headings"] if lvl == 1]
        if not h1s:
            problems["h1-missing"] += 1; ev["h1-missing"].append(url)
        elif len(h1s) > 1:
            problems["h1-multiple"] += 1; ev["h1-multiple"].append(f"{url} ({len(h1s)})")
        levels = [lvl for lvl, _ in d["headings"]]
        for a, b in zip(levels, levels[1:]):
            if b > a + 1:
                problems["heading-skip"] += 1
                ev["heading-skip"].append(f"{url} (h{a} -> h{b})")
                break

        # --- keyword presence on the money pages ---
        if page.kind in ("location", "location_service"):
            city = next((l.city for l in graph.locations if l.slug in url), "")
            if city:
                head = " ".join([d["title"], h1s[0] if h1s else "", d["text"][:400]]).lower()
                if city.lower() not in head:
                    problems["keyword-missing"] += 1
                    ev["keyword-missing"].append(f"{url} (no '{city}' in title/H1/opening)")

        # --- content depth ---
        words = len(_WORD.findall(d["text"]))
        floor = WORD_FLOOR.get(page.kind, 200)
        if words < floor:
            problems["thin-content"] += 1
            ev["thin-content"].append(f"{url} ({words} words, floor {floor})")

        # --- images ---
        for attrs in d["images"]:
            if not re.search(r'\balt=["\'][^"\']+["\']', attrs) and 'alt=""' not in attrs:
                problems["image-alt"] += 1
                ev["image-alt"].append(f"{url}: {attrs.strip()[:60]}")
                break
        for attrs in d["images"]:
            if not (re.search(r'\bwidth=', attrs) and re.search(r'\bheight=', attrs)):
                problems["image-dimensions"] += 1
                ev["image-dimensions"].append(f"{url}: {attrs.strip()[:60]}")
                break

        # --- schema ---
        for blob in d["jsonld"]:
            try:
                data = json.loads(blob)
            except json.JSONDecodeError as exc:
                problems["schema-invalid"] += 1
                ev["schema-invalid"].append(f"{url}: {exc}")
                continue
            if isinstance(data, dict) and data.get("@type"):
                if not data.get("telephone"):
                    problems["schema-incomplete"] += 1
                    ev["schema-incomplete"].append(f"{url}: no telephone")
                elif not data.get("areaServed"):
                    problems["schema-incomplete"] += 1
                    ev["schema-incomplete"].append(f"{url}: no areaServed")

        # --- weight ---
        if d["bytes"] > PAGE_WEIGHT_BLOCK:
            heavy.append(f"{url} ({d['bytes'] // 1024} KB)")
        elif d["bytes"] > PAGE_WEIGHT_WARN:
            problems["page-weight"] += 1
            ev["page-weight"].append(f"{url} ({d['bytes'] // 1024} KB)")

        # --- links ---
        for href in d["links"]:
            if href.startswith(("http", "mailto:", "tel:")):
                continue
            linked.add("/" + href.strip("/") if href != "/" else "/")

    # --- duplicates across the whole site ---
    #
    # Two titles on two URLs is a copy problem. The SAME URL listed twice is not
    # a copy problem at all — it is the build having produced one page twice —
    # and reporting it as "probably a missing {state} token" sends you looking
    # in the templates for something that was never there. Separate the two, and
    # say which one this is. (The renderer now refuses this outright, so it
    # should be unreachable; the check stays because a rule that only exists
    # upstream is a rule that stops existing the day upstream is refactored.)
    def same_url(urls: list[str]) -> bool:
        return len(set(urls)) < len(urls)

    dup_titles = {t: u for t, u in titles.items() if len(u) > 1}
    repeated = {t: u for t, u in dup_titles.items() if same_url(u)}
    if repeated:
        add("url-repeated",
            f"{len(repeated)} URL(s) appear more than once in this build",
            [f"{u[0]} rendered {len(u)} times as {t[:44]!r}"
             for t, u in list(repeated.items())[:5]])
    genuine = {t: u for t, u in dup_titles.items() if not same_url(u)}
    if genuine:
        add("title-duplicate", f"{len(genuine)} title(s) used on more than one page",
            [f"{t[:52]!r} on {len(u)} pages: {', '.join(u[:3])}"
             for t, u in list(genuine.items())[:5]])
    dup_desc = {t: u for t, u in descs.items()
                if len(u) > 1 and not same_url(u)}
    if dup_desc:
        add("description-duplicate",
            f"{len(dup_desc)} description(s) used on more than one page",
            [f"{len(u)} pages: {', '.join(u[:3])}" for u in list(dup_desc.values())[:5]])

    # --- link resolution, sampled ---
    edge_index = {}
    masters = result.out_dir / "_masters" / "index.json"
    if masters.is_file():
        edge_index = json.loads(masters.read_text(encoding="utf-8"))
    edge_locs = set((edge_index.get("locations") or {}).keys())
    edge_svcs = set(edge_index.get("services") or [])

    def resolvable(url: str) -> bool:
        if url in known:
            return True
        parts = [p for p in url.strip("/").split("/") if p]
        if len(parts) == 2 and parts[0] == "areas" and parts[1] in edge_locs:
            return True
        if len(parts) == 3 and parts[0] == "services" and parts[1] in edge_svcs \
                and parts[2] in edge_locs:
            return True
        return (result.out_dir / url.strip("/") / "index.html").is_file() or \
               (result.out_dir / url.strip("/")).is_file()

    checked = sorted(linked)[:link_sample]
    broken = [u for u in checked if not resolvable(u)]
    if broken:
        add("link-broken", f"{len(broken)} internal link(s) of {len(checked)} sampled "
                           f"resolve to nothing", broken)

    orphans = [p.url for p in pages if p.url not in linked and p.url != "/"]
    if orphans:
        add("orphan-pages", f"{len(orphans)} pre-rendered page(s) are in the sitemap but "
                            f"nothing links to them", orphans)

    # --- sitemap and robots ---
    sm_files = sorted(result.out_dir.glob("sitemap*.xml"))
    total_urls = 0
    for sm in sm_files:
        raw = sm.read_text(encoding="utf-8")
        n = raw.count("<loc>")
        total_urls += n
        if "<sitemapindex" in raw:
            continue
        if n > SITEMAP_MAX_URLS or sm.stat().st_size > SITEMAP_MAX_BYTES:
            add("sitemap-oversize",
                f"{sm.name} holds {n:,} URLs / {sm.stat().st_size / 1e6:.1f} MB — "
                f"over the 50,000 / 50 MB protocol cap",
                [f"{n - SITEMAP_MAX_URLS:,} URLs past the cap are invisible"]
                if n > SITEMAP_MAX_URLS else [])
    if not sm_files:
        add("sitemap-mismatch", "no sitemap was written")

    robots = result.out_dir / "robots.txt"
    if not robots.is_file():
        add("robots-missing", "robots.txt is missing")
    else:
        body = robots.read_text(encoding="utf-8")
        if re.search(r"^\s*Disallow:\s*/\s*$", body, re.M):
            add("robots-disallow-all", "robots.txt disallows the entire site")
        if "sitemap" not in body.lower():
            add("robots-missing", "robots.txt does not name the sitemap")

    if heavy:
        spec = dict(rules.get("page-weight", {}))
        rep.findings.append(SeoFinding(
            rule="page-weight", severity="blocker",
            message=f"{len(heavy)} page(s) over {PAGE_WEIGHT_BLOCK // 1024} KB of HTML",
            why=spec.get("why", ""), evidence=heavy[:6]))

    for rule, count in problems.items():
        add(rule, f"{count} page(s)", ev[rule])

    rep.stats = {
        "pages_audited": len(pages),
        "edge_pages": result.edge_pages,
        "unique_titles": len(titles),
        "unique_descriptions": len(descs),
        "sitemap_urls": total_urls,
        "links_sampled": len(checked),
        "avg_page_kb": round(sum(len(p.html.encode()) for p in pages) / max(1, len(pages)) / 1024, 1),
    }
    rep.not_verified += [
        f"{result.edge_pages:,} edge-rendered page(s) were not fetched — their master "
        f"template was audited instead",
        "off-page signals: backlinks, entity consistency, Search Console status",
        "real-world Core Web Vitals (this measures HTML weight, not field data)",
        "whether the target keywords are ones anyone searches for",
    ]
    return rep
