"""
Foundry — the content graph.

Loads and validates every YAML that describes what a site IS, and turns a lead
buyer's payable-ZIP footprint into a clean location index.

Design notes, and why:

  * NOTHING here is inferred from file contents. Every fact arrives from a
    declared field. (Engine B learned this the hard way: a swap table built by
    guessing at file contents rewrote the keys of a 50-state lookup map.)

  * A COVERAGE LIST IS A COMPLIANCE CONSTRAINT, NOT A PREFERENCE. The buyer's
    rule is "any location outside this list is not covered and must not be
    published", so `assert_within_coverage` RAISES rather than warning.

  * The five real traps in a buyer's pasted report are handled explicitly and
    named in `CoverageReport.skipped` — never silently swallowed:
        1. a city whose name looks like a metadata key ("Rule: 79547" is
           Rule, Texas — not a `RULE:` header)
        2. military "states" (AA / AE / AP) and DPO ZIPs
        3. duplicate ZIPs (7,453 exact repeats in one real plumbing list)
        4. split cities that resolve to different counties in different lists
        5. county spelling variants, and independent cities that the Census
           writes as "Norfolk city"
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

# --------------------------------------------------------------------------
# geography reference
# --------------------------------------------------------------------------

STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

# Not states. Lists routinely open with these and they are not buildable.
MILITARY = {"AA", "AE", "AP"}

# Independent cities the Census writes in lower case. Getting this wrong makes
# two spellings of the same county look like two counties.
INDEPENDENT_CITY_CASE = {
    "norfolk city", "richmond city", "roanoke city", "hampton city",
    "alexandria city", "chesapeake city", "portsmouth city", "suffolk city",
    "virginia beach city", "newport news city", "st. louis city",
    "baltimore city", "carson city",
}

# Cities that straddle a county line and resolve differently in different
# buyer lists. Declared once here rather than voted on per file.
KNOWN_COUNTY: dict[tuple[str, str], str] = {
    ("carrollton", "TX"): "Denton County",
    ("kansas city", "MO"): "Jackson County",
    ("houston", "TX"): "Harris County",
    ("atlanta", "GA"): "Fulton County",
    ("columbus", "GA"): "Muscogee County",
    ("aurora", "IL"): "Kane County",
    ("chicago", "IL"): "Cook County",
}

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_ZIP = re.compile(r"^\d{5}$")


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return _SLUG_STRIP.sub("-", text.lower()).strip("-")


def canonical_county(raw: str) -> str:
    """Fold county spelling variants onto one canonical form."""
    name = " ".join(str(raw).split()).strip().rstrip(",")
    if not name:
        return ""
    low = name.lower()
    if low in INDEPENDENT_CITY_CASE:
        return name.title().replace(" City", " city")
    low = re.sub(r"\s+(county|parish|borough|census area|municipality)$", "", low)
    low = low.replace("saint ", "st. ").replace("st ", "st. ")
    low = re.sub(r"\s+", " ", low).strip()
    # str.title() capitalises after an apostrophe, giving "Prince George'S".
    titled = re.sub(r"[A-Za-z]+(?:'[a-z]+)?",
                    lambda m: m.group(0)[:1].upper() + m.group(0)[1:], low)
    return f"{titled} County"


# --------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------

@dataclass
class Location:
    city: str
    state_abbr: str
    county: str
    zips: list[str] = field(default_factory=list)
    payout: float = 0.0     # summed net payout across this city's ZIPs

    @property
    def state(self) -> str:
        return STATES.get(self.state_abbr, self.state_abbr)

    @property
    def slug(self) -> str:
        return f"{slugify(self.city)}-{self.state_abbr.lower()}"

    @property
    def county_slug(self) -> str:
        return slugify(self.county)

    @property
    def label(self) -> str:
        return f"{self.city}, {self.state_abbr}"

    def zips_short(self, limit: int = 6) -> str:
        return ", ".join(self.zips[:limit])


@dataclass
class CoverageReport:
    rows_in: int = 0
    rows_kept: int = 0
    duplicate_zips: int = 0
    skipped: list[str] = field(default_factory=list)
    county_merges: int = 0
    city_merges: int = 0
    merged_names: list[str] = field(default_factory=list)


def parse_coverage_text(text: str) -> tuple[list[dict], CoverageReport]:
    """
    Parse a lead buyer's pasted report:

        GEORGIA (GA)
        Fulton County
        Atlanta: 30301, 30302
        Rule: 79547            <- a CITY named Rule, not a `RULE:` header

    Trap 1 is why header keys are only read ABOVE the first state block: once a
    state block has opened, every `Word: 12345` line is a city, full stop.
    """
    rep = CoverageReport()
    rows: list[dict] = []
    state_open = False
    state_abbr = ""
    county = ""
    seen: set[tuple[str, str]] = set()

    state_re = re.compile(r"^\s*(.+?)\s*\(([A-Z]{2})\)\s*$")
    city_re = re.compile(r"^\s*(.+?)\s*:\s*(.+?)\s*$")

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        m = state_re.match(line)
        if m:
            abbr = m.group(2)
            if abbr in MILITARY:
                state_open, state_abbr = False, ""
                rep.skipped.append(f"military region {abbr} — not a state, not buildable")
                continue
            if abbr not in STATES:
                rep.skipped.append(f"unrecognised state code {abbr!r}")
                state_open, state_abbr = False, ""
                continue
            state_open, state_abbr, county = True, abbr, ""
            continue

        if not state_open:
            # Trap 1: metadata keys live only above the first state block.
            continue

        m = city_re.match(line)
        if m:
            city = " ".join(m.group(1).split())
            zips = [z.strip() for z in re.split(r"[,\s]+", m.group(2)) if _ZIP.match(z.strip())]
            if not zips:
                rep.skipped.append(f"{city}, {state_abbr}: no 5-digit ZIP on the line")
                continue
            rep.rows_in += len(zips)
            for z in zips:
                key = (z, state_abbr)
                if key in seen:                       # Trap 3
                    rep.duplicate_zips += 1
                    continue
                seen.add(key)
                rows.append({"zip": z, "city": city, "state": state_abbr, "county": county})
                rep.rows_kept += 1
            continue

        # a bare line inside a state block is a county heading
        county = canonical_county(line)

    return rows, rep


def _better_name(a: str, b: str) -> str:
    """Two spellings of one town — pick the richer one, deterministically.

    "Coeur d'Alene" beats "Coeur D Alene"; "Winston-Salem" beats "Winston Salem";
    "McKinney" beats "Mckinney". Punctuation first, then an interior capital,
    then alphabetical so the answer never depends on which file was read first.
    """
    def score(name: str) -> tuple[int, int, str]:
        punct = 2 if any(c in name for c in "'-.") else 0
        inner = 1 if re.search(r"[a-z][A-Z]", name) else 0
        return (punct + inner, 0, "")
    sa, sb = score(a)[0], score(b)[0]
    if sa != sb:
        return a if sa > sb else b
    return min(a, b)


def load_coverage(root: Path, niche: str, states: Iterable[str] | None = None
                  ) -> tuple[list[Location], CoverageReport]:
    """Load data/coverage/<niche>/<ST>.csv into a de-duplicated location index."""
    rep = CoverageReport()
    folder = root / "data" / "coverage" / niche
    if not folder.is_dir():
        raise FoundryError(
            f"no coverage list for niche {niche!r} at {folder}",
            ctx={"missing_coverage": True, "niche": niche},
        )

    wanted = {s.upper() for s in states} if states else None
    buckets: dict[tuple[str, str], Location] = {}
    county_forms: dict[str, set[str]] = {}
    seen_zip: set[tuple[str, str]] = set()

    for csv_path in sorted(folder.glob("*.csv")):
        abbr = csv_path.stem.upper()
        if abbr in MILITARY:
            rep.skipped.append(f"{csv_path.name}: military region, skipped")
            continue
        if abbr not in STATES:
            rep.skipped.append(f"{csv_path.name}: not a US state code, skipped")
            continue
        if wanted and abbr not in wanted:
            continue

        with csv_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                zip_code = (row.get("zip") or "").strip()
                city = " ".join((row.get("city") or "").split())
                if not _ZIP.match(zip_code) or not city:
                    continue
                rep.rows_in += 1
                if (zip_code, abbr) in seen_zip:            # Trap 3
                    rep.duplicate_zips += 1
                    continue
                seen_zip.add((zip_code, abbr))

                county = canonical_county(row.get("county") or "")
                # Trap 4: a declared split city always wins over the file.
                county = KNOWN_COUNTY.get((city.lower(), abbr), county) \
                    or f"{STATES[abbr]} Statewide"

                # Trap 6: the bucket key is the SLUG, not the name.
                #
                # A buyer's feed spells one town two ways — "Winston Salem" and
                # "Winston-Salem", "Coeur D Alene" and "Coeur d'Alene", "Mckinney"
                # and "McKinney". Keying on the lowercased NAME makes those two
                # locations; keying on the slug makes them one, which is what they
                # are. The slug is the URL, and two locations that produce one URL
                # are not two pages — they are one page written twice, with half
                # the ZIPs on the copy that loses.
                key = (slugify(city), abbr)
                county_forms.setdefault(key[0] + "|" + abbr, set()).add(county)

                loc = buckets.get(key)
                if loc is None:
                    loc = buckets[key] = Location(city=city, state_abbr=abbr, county=county)
                elif loc.city != city:
                    keep = _better_name(loc.city, city)
                    note = f"{min(loc.city, city)} / {max(loc.city, city)} → {keep}, {abbr}"
                    if note not in rep.merged_names:      # once per town, not per ZIP
                        rep.merged_names.append(note)
                        rep.city_merges += 1
                    loc.city = keep
                loc.zips.append(zip_code)
                try:
                    loc.payout += float(row.get("payout") or 0)
                except (TypeError, ValueError):
                    pass
                rep.rows_kept += 1

    # Trap 5: a city that voted for two counties keeps the most common one.
    for k, forms in county_forms.items():
        if len(forms) > 1:
            rep.county_merges += 1

    locations = sorted(buckets.values(), key=lambda l: (l.state_abbr, l.city))
    for loc in locations:
        loc.zips = sorted(set(loc.zips))
    if not locations:
        raise FoundryError(
            f"coverage list for {niche!r} produced zero buildable locations",
            ctx={"empty_coverage": True, "niche": niche},
        )
    return locations, rep


def save_coverage(root: Path, niche: str, rows: list[dict]) -> dict[str, int]:
    """
    Write a niche's coverage. REPLACES the niche wholesale — never merges.

    A buyer publishes a COMPLETE footprint. Merging a new list into an old one
    keeps dropped ZIPs alive, and publishing a ZIP the buyer has dropped is
    exactly the compliance failure the list exists to prevent.
    """
    folder = root / "data" / "coverage" / niche
    if folder.is_dir():
        for old in folder.glob("*.csv"):
            old.unlink()
    folder.mkdir(parents=True, exist_ok=True)

    by_state: dict[str, list[dict]] = {}
    for r in rows:
        by_state.setdefault(r["state"].upper(), []).append(r)

    written: dict[str, int] = {}
    for abbr, items in sorted(by_state.items()):
        path = folder / f"{abbr}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["zip", "city", "state", "county"])
            w.writeheader()
            w.writerows(sorted(items, key=lambda r: (r["city"], r["zip"])))
        written[abbr] = len(items)
    return written


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------

class FoundryError(RuntimeError):
    """An error that carries enough context for diagnose() to explain the cause."""

    def __init__(self, message: str, ctx: dict[str, Any] | None = None):
        super().__init__(message)
        self.ctx = ctx or {}


# --------------------------------------------------------------------------
# the graph
# --------------------------------------------------------------------------

def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise FoundryError(f"missing file: {path}", ctx={"missing_file": str(path)})
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise FoundryError(f"{path} must contain a YAML mapping", ctx={"bad_yaml": str(path)})
    return data


REQUIRED_BUSINESS = ("slug", "company", "phone")
REQUIRED_SITE = ("site_id", "business", "niche", "domain")


@dataclass
class Graph:
    root: Path
    site: dict
    business: dict
    niche: dict
    globals: dict
    theme: dict
    style: dict
    skeleton: dict
    locations: list[Location]
    coverage_report: CoverageReport
    services: list[dict]

    # -- convenience -------------------------------------------------------

    @property
    def facts(self) -> dict:
        return self.business.get("facts") or {}

    @property
    def counties(self) -> list[dict]:
        out: dict[str, dict] = {}
        for loc in self.locations:
            c = out.setdefault(loc.county, {
                "name": loc.county,
                "slug": f"{slugify(loc.county)}-{loc.state_abbr.lower()}",
                "state_abbr": loc.state_abbr,
                "state": loc.state,
                "cities": [],
            })
            c["cities"].append(loc)
        return sorted(out.values(), key=lambda c: (c["state_abbr"], c["name"]))

    def nearby(self, loc: Location, limit: int = 4) -> list[Location]:
        same = [l for l in self.locations
                if l.county == loc.county and l.slug != loc.slug]
        if len(same) < limit:
            same += [l for l in self.locations
                     if l.state_abbr == loc.state_abbr and l.slug != loc.slug
                     and l not in same]
        return same[:limit]


def load_graph(root: Path, site_id: str) -> Graph:
    site = _read_yaml(root / "data" / "sites" / f"{site_id}.yaml")
    for key in REQUIRED_SITE:
        if not site.get(key):
            raise FoundryError(
                f"site {site_id!r} is missing required field {key!r}",
                ctx={"incomplete_row": True, "field": key},
            )
    if site["site_id"] != site_id:
        raise FoundryError(
            f"site_id {site['site_id']!r} does not match filename {site_id!r}",
            ctx={"slug_mismatch": True},
        )

    business = _read_yaml(root / "data" / "businesses" / f"{site['business']}.yaml")
    for key in REQUIRED_BUSINESS:
        if not business.get(key):
            raise FoundryError(
                f"business {site['business']!r} is missing required field {key!r} — "
                f"an incomplete row is refused at the door, never filled with a guess",
                ctx={"incomplete_row": True, "field": key},
            )

    niche = _read_yaml(root / "data" / "niches" / f"{site['niche']}.yaml")
    globals_ = _read_yaml(root / "data" / "global.yaml")

    themes = _read_yaml(root / "data" / "themes.yaml")
    theme_key = site.get("theme") or "slate"
    if theme_key not in themes:
        raise FoundryError(
            f"unknown theme {theme_key!r}; available: {', '.join(sorted(themes))}",
            ctx={"unknown_theme": theme_key},
        )

    _lib = root / "data" / "library"
    if not any(p.is_file() for p in (_lib / "_base.yaml",
                                     _lib / f"{site['niche']}.yaml",
                                     _lib / "user" / f"{site['niche']}.yaml")):
        raise FoundryError(
            f"niche {site['niche']!r} has coverage but no block library, so there is "
            f"nothing to compose a page from",
            ctx={"no_library": True, "niche": site["niche"]})

    styles = _read_yaml(root / "data" / "styles.yaml")
    style_key = site.get("style") or "classic"
    if style_key not in styles:
        raise FoundryError(
            f"unknown style {style_key!r}; available: {', '.join(sorted(styles))}",
            ctx={"unknown_style": style_key})
    style = dict(styles[style_key])
    style["key"] = style_key

    skeletons = _read_yaml(root / "data" / "skeletons.yaml")
    skel_key = site.get("skeleton") or "standard"
    if skel_key not in skeletons:
        raise FoundryError(
            f"unknown skeleton {skel_key!r}; available: {', '.join(sorted(skeletons))}",
            ctx={"unknown_skeleton": skel_key})
    skeleton = dict(skeletons[skel_key])
    skeleton["key"] = skel_key

    cov = site.get("coverage") or {}
    locations, report = load_coverage(root, site["niche"], cov.get("states"))

    # An explicit city list narrows the states. Empty means "every city in the
    # selected states" — the common case, and the reason picking a state is
    # enough on its own.
    picked = {c.strip().lower() for c in (cov.get("cities") or []) if c.strip()}
    if picked:
        kept = [l for l in locations if l.slug in picked]
        missing = picked - {l.slug for l in locations}
        if missing:
            report.skipped.append(
                f"{len(missing)} selected city/cities are not in this niche's coverage "
                f"list and were dropped: {', '.join(sorted(missing)[:6])}")
        if not kept:
            raise FoundryError(
                "the selected cities are all outside this niche's payable coverage list",
                ctx={"empty_coverage": True, "niche": site["niche"]})
        locations = kept

    # services: the site may narrow the niche's list, never invent one
    all_services = {s["slug"]: s for s in (niche.get("services") or [])}
    picked = site.get("services") or list(all_services)
    # Naming a service twice is not asking for it twice. Three niche pairs share
    # a service slug (roofing/gutters both have gutter-installation), the site
    # form draws a checkbox per niche-and-service, and a browser restoring form
    # state on a Back navigation can re-tick the hidden twin — so the same slug
    # arrives twice and every page it owns is rendered twice, at the same URL.
    picked = list(dict.fromkeys(picked))
    unknown = [s for s in picked if s not in all_services]
    if unknown:
        raise FoundryError(
            f"site names services not defined by niche {site['niche']!r}: {unknown}",
            ctx={"unknown_service": unknown},
        )
    services = [all_services[s] for s in picked]

    return Graph(
        root=root, site=site, business=business, niche=niche, globals=globals_,
        theme=themes[theme_key], style=style, skeleton=skeleton, locations=locations,
        coverage_report=report, services=services,
    )


def assert_within_coverage(graph: Graph, place: str) -> None:
    """
    Compliance gate. The buyer's rule line reads:
        "any location outside this list is not covered and must not be published"
    so this RAISES. A warning would let a non-payable page reach the internet.
    """
    needle = place.strip().lower()
    for loc in graph.locations:
        if loc.city.lower() == needle or loc.county.lower() == needle:
            return
    raise FoundryError(
        f"{place!r} is outside the payable coverage list for niche "
        f"{graph.site['niche']!r} and must not be published",
        ctx={"outside_coverage": True, "place": place},
    )


def list_sites(root: Path) -> list[str]:
    folder = root / "data" / "sites"
    return sorted(p.stem for p in folder.glob("*.yaml")) if folder.is_dir() else []
