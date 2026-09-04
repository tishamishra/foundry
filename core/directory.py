"""
Foundry — the multi-business DIRECTORY site type.

Every other site this engine builds is a marketing site for ONE business. A
directory is the opposite: one site that LISTS many businesses — a browsable
directory of local providers, grouped by city, each linking to their own site.

WHERE THE LISTINGS COME FROM. Nothing is invented. Every provider is a real
business record the user has already added to the engine (data/businesses/),
enriched from the site(s) built for it: the site's domain becomes the provider's
website, and the site's niche and selected services become the provider's
category and service tags. A directory therefore never contains a business the
user did not create — no fabricated companies, no fake phone numbers.

HOW IT IS SELECTED. A directory is just a site record with `type: directory`:

    site_id: al-plumbing-directory
    type: directory
    domain: alplumbingdirectory.com
    title: Alabama Plumbing Directory
    tagline: Find a trusted local plumber near you
    niche: plumbing          # optional — list only businesses with a plumbing site
    theme: slate
    style: classic

build_site() detects the type and routes here; the output is an ordinary
dist/<domain>/ tree that previews and deploys exactly like any other site.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .graph import _read_yaml, slugify

# US state name -> USPS abbreviation, so "Marietta, Georgia" displays as
# "Marietta, GA". A value that is already a 2-letter code is left alone.
_STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}


def _abbr(state: str) -> str:
    s = (state or "").strip()
    if len(s) == 2:
        return s.upper()
    return _STATE_ABBR.get(s.lower(), s)


def _phone_link(phone: str) -> str:
    digits = re.sub(r"[^\d+]", "", phone or "")
    if digits.startswith("+"):
        return digits
    d = re.sub(r"\D", "", digits)
    if len(d) == 10:
        return "+1" + d
    return "+" + d if d else ""


def _load_site_index(root: Path) -> dict[str, list[dict]]:
    """business slug -> the marketing sites built for it (domain, niche, services)."""
    idx: dict[str, list[dict]] = {}
    folder = root / "data" / "sites"
    if not folder.is_dir():
        return idx
    for p in sorted(folder.glob("*.yaml")):
        site = _read_yaml(p)
        if site.get("type") == "directory":
            continue
        biz = site.get("business")
        if not biz:
            continue
        idx.setdefault(biz, []).append(site)
    return idx


def _niche_services(root: Path, niche_key: str) -> dict[str, str]:
    """slug -> display name for a niche's services (to label a provider's tags)."""
    out: dict[str, str] = {}
    n = _read_yaml(root / "data" / "niches" / f"{niche_key}.yaml")
    for s in n.get("services") or []:
        if s.get("slug"):
            out[s["slug"]] = s.get("name") or s["slug"]
    return out, (n.get("label") or niche_key.replace("-", " ").title())


def load_providers(root: Path, niche_filter: str | None = None,
                   include_slugs: set | None = None) -> list[dict]:
    """Build the provider list from the engine's OWN business + site records.

    include_slugs, when given, is an explicit pick of business slugs to list —
    exactly those businesses appear, regardless of niche. When it is None the
    listing is every business (optionally filtered to one trade by niche_filter).
    """
    site_index = _load_site_index(root)
    folder = root / "data" / "businesses"
    providers: list[dict] = []
    if not folder.is_dir():
        return providers

    for p in sorted(folder.glob("*.yaml")):
        biz = _read_yaml(p)
        company = biz.get("company") or biz.get("brand")
        if not company:
            continue
        slug = biz.get("slug") or p.stem
        # An explicit pick wins: include exactly the chosen businesses.
        if include_slugs is not None and slug not in include_slugs:
            continue
        sites = site_index.get(slug, [])

        # The provider's category comes from the site(s) built for it. With no
        # explicit pick, filter to the directory's niche when one is set.
        niches = [s.get("niche") for s in sites if s.get("niche")]
        if niche_filter and include_slugs is None:
            if niche_filter not in niches:
                continue
            niche_key = niche_filter
        elif niche_filter:
            niche_key = niche_filter
        else:
            niche_key = niches[0] if niches else None

        svc_names: list[str] = []
        niche_label = ""
        if niche_key:
            svc_map, niche_label = _niche_services(root, niche_key)
            # services the business's site actually selected, else the niche's own
            picked: list[str] = []
            for s in sites:
                if s.get("niche") == niche_key:
                    picked = s.get("services") or picked
            slugs = picked or list(svc_map.keys())
            svc_names = [svc_map[x] for x in slugs if x in svc_map][:8]

        addr = biz.get("address") or {}
        facts = biz.get("facts") or {}
        city = (addr.get("city") or "").strip()
        state_abbr = _abbr(addr.get("state") or "")
        website = ("https://" + sites[0]["domain"]) if sites and sites[0].get("domain") else ""
        rating = facts.get("rating")
        parts = [addr.get("street"), city, state_abbr, addr.get("zip")]
        address_full = ", ".join([x for x in parts if x])

        providers.append({
            "slug": slugify(company),
            "name": company,
            "phone": biz.get("phone") or "",
            "phone_link": _phone_link(biz.get("phone") or ""),
            "email": biz.get("email") or "",
            "city": city,
            "state_abbr": state_abbr,
            "place": (f"{city}, {state_abbr}" if city and state_abbr else (city or state_abbr)),
            "address_full": address_full,
            "hours": facts.get("hours") or "",
            "years": facts.get("years_in_business"),
            "rating": float(rating) if isinstance(rating, (int, float)) else None,
            "website": website,
            "niche": niche_key or "",
            "niche_label": niche_label,
            "services": svc_names,
            "featured": bool(sites) and any(s.get("directory_featured") for s in sites),
        })

    # de-dupe slugs (two businesses with the same company name)
    seen: dict[str, int] = {}
    for prov in providers:
        base = prov["slug"]
        if base in seen:
            seen[base] += 1
            prov["slug"] = f"{base}-{seen[base]}"
        else:
            seen[base] = 1
    return providers


def _cities(providers: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for prov in providers:
        if not prov["place"]:
            continue
        key = prov["place"]
        g = groups.setdefault(key, {
            "place": key, "city": prov["city"], "state_abbr": prov["state_abbr"],
            "slug": slugify(key), "providers": []})
        g["providers"].append(prov)
    for g in groups.values():
        g["providers"].sort(key=lambda x: (-(x["rating"] or 0), x["name"]))
        g["count"] = len(g["providers"])
    return sorted(groups.values(), key=lambda g: (-g["count"], g["place"]))


def build_directory(root: Path, site: dict, out_root: Path | None = None):
    """Render a directory site (home + per-city + per-provider) to dist/<domain>/."""
    from .render import _env, clear_output, BuildResult, Page

    domain = site["domain"]
    out_dir = (out_root or root / "dist") / domain
    clear_output(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Ship the shared stylesheet the pages reference (same sheet every site uses).
    import shutil
    css_src = root / "assets" / "css" / "site.css"
    if css_src.is_file():
        (out_dir / "assets" / "css").mkdir(parents=True, exist_ok=True)
        shutil.copy2(css_src, out_dir / "assets" / "css" / "site.css")

    env = _env(root)
    tpl = env.get_template("directory.html.j2")

    themes = _read_yaml(root / "data" / "themes.yaml")
    theme = themes.get(site.get("theme") or "slate", next(iter(themes.values())))
    styles = _read_yaml(root / "data" / "styles.yaml")
    style = dict(styles.get(site.get("style") or "classic", next(iter(styles.values()))))
    style["key"] = site.get("style") or "classic"
    globals_ = _read_yaml(root / "data" / "global.yaml")

    include = site.get("businesses") or None
    providers = load_providers(root, site.get("niche") or None,
                               include_slugs=set(include) if include else None)
    cities = _cities(providers)

    # Optional cap: at most N businesses per city (highest-rated kept first).
    try:
        per_city = int(site.get("per_city_limit") or 0)
    except (TypeError, ValueError):
        per_city = 0
    if per_city > 0:
        for c in cities:
            c["providers"] = c["providers"][:per_city]
            c["count"] = len(c["providers"])
        kept = {pr["slug"] for c in cities for pr in c["providers"]}
        providers = [pr for pr in providers if not pr["place"] or pr["slug"] in kept]
    niche_label = site.get("niche_label") or (providers[0]["niche_label"] if providers else "") or "Local"
    dir_title = site.get("title") or (niche_label + " Directory")

    result = BuildResult(site_id=site.get("site_id") or domain, domain=domain, out_dir=out_dir)
    seen: set[str] = set()

    def emit(url: str, html: str, kind: str, title: str, desc: str) -> None:
        op = ("index.html" if url == "/" else url.strip("/") + "/index.html")
        if op in seen:
            return
        seen.add(op)
        target = out_dir / op
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
        result.pages.append(Page(url=url, kind=kind, title=title, description=desc, html=""))

    base = {
        "site": site, "domain": domain, "theme": theme, "style": style,
        "globals": globals_, "dir_title": dir_title, "niche_label": niche_label,
        "tagline": site.get("tagline") or (f"Find a trusted {niche_label.lower()} near you"),
        "providers": providers, "cities": cities, "total": len(providers),
        "phone_link": None,
    }

    # ---- home ----
    home_title = f"{dir_title} — {len(providers)} {niche_label} businesses"
    home_desc = f"Browse {len(providers)} {niche_label.lower()} businesses across {len(cities)} cities. Compare, view details and contact them directly."
    emit("/", tpl.render(page="home", url="/", title=home_title, description=home_desc, **base),
         "home", home_title, home_desc)

    # ---- one page per city ----
    for c in cities:
        u = f"/city/{c['slug']}"
        t = f"{niche_label} in {c['place']} — {c['count']} listed | {dir_title}"
        d = f"{c['count']} {niche_label.lower()} businesses in {c['place']}. Compare ratings, services and contact details, and reach each one directly."
        emit(u, tpl.render(page="city", url=u, city=c, title=t, description=d, **base),
             "location", t, d)

    # ---- one page per provider ----
    for prov in providers:
        u = f"/provider/{prov['slug']}"
        place = prov["place"] or niche_label
        t = f"{prov['name']} — {niche_label} in {place} | {dir_title}"
        d = f"{prov['name']}, {niche_label.lower()} in {place}. Contact details, services and hours." + (f" Call {prov['phone']}." if prov['phone'] else "")
        emit(u, tpl.render(page="provider", url=u, prov=prov, title=t, description=d, **base),
             "service", t, d)

    return result
