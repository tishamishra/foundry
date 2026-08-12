"""
Foundry — importing a lead buyer's coverage feed.

The feed that arrives is one wide CSV covering every niche at once:

    zip,city,state,vertical,net_payout,last_updated

Three things about it shape this module.

1. IT HAS NO COUNTY COLUMN.
   Engine B recorded exactly this: its own location table had `withCounty: 0`
   across all 9,346 cities, so any build without a county source fell back to a
   fabricated `<state>-statewide` group. County pages are a real page type and
   an invented county is a lie on a live site.
   Foundry ships a static ZIP -> city/state/county crosswalk in
   `data/geo/zip-county.csv` (42,342 US ZIPs). No network, no extra dependency,
   no runtime download — the file is part of the package.

2. 15% OF THE ROWS ARRIVE WITH NO CITY AND NO STATE.
   30,102 of 201,000 in the file that prompted this module. They are not junk:
   they carry a valid ZIP, vertical and payout, and the ZIP determines the rest.
   Dropping them would have silently discarded a seventh of the footprint, so
   they are RECOVERED from the crosswalk and counted separately in the report.
   A row that cannot be recovered is reported, never guessed.

3. IT CARRIES net_payout.
   Neither parent engine had this. It is the difference between "these ZIPs are
   payable" and "these ZIPs are worth more", and it is the right input for
   deciding what to build first and which city pages to pre-render.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .graph import MILITARY, STATES, canonical_county, slugify

# The feed's vertical labels, mapped to niche slugs. Declared, never inferred —
# a vertical this table does not know is REPORTED, not silently slugified into
# a niche nothing can build.
VERTICALS: dict[str, str] = {
    "roofing": "roofing",
    "plumbing": "plumbing",
    "hvac": "hvac",
    "pest control": "pest-control",
    "water damage": "water-damage",
    "fire damage removal": "fire-damage",
    "mold removal": "mold-removal",
    "biohazard": "biohazard",
    "windows": "windows",
    "siding": "siding",
    "electrical": "electrical",
    "bathroom remodeling": "bathroom-remodeling",
    "foundation repair": "foundation-repair",
    "garage door": "garage-door",
    "waterproofing": "waterproofing",
    "gutters": "gutters",
    "painting": "painting",
    "kitchen": "kitchen-remodeling",
    "deck": "deck",
    "landscaping": "landscaping",
    "appliance": "appliance-repair",
    "auto accident attorneys": "auto-accident-attorneys",
    "personal injury attorneys": "personal-injury-attorneys",
    "dui / dwi attorneys": "dui-dwi-attorneys",
}

_ZIP = re.compile(r"^\d{5}$")


@dataclass
class FeedReport:
    rows_in: int = 0
    kept: int = 0
    recovered: int = 0          # city/state rebuilt from the ZIP
    county_added: int = 0       # county the feed never had
    duplicates: int = 0
    unmatched_zip: list[str] = field(default_factory=list)
    unknown_vertical: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    per_niche: dict[str, dict] = field(default_factory=dict)

    def summary(self) -> str:
        return (f"{self.kept:,} rows kept of {self.rows_in:,} · "
                f"{self.recovered:,} recovered from ZIP · "
                f"{self.county_added:,} counties added · "
                f"{self.duplicates:,} duplicates dropped")


def load_crosswalk(root: Path) -> dict[str, tuple[str, str, str]]:
    """zip -> (city, state, county). Shipped as data, not fetched."""
    path = root / "data" / "geo" / "zip-county.csv"
    if not path.is_file():
        return {}
    out: dict[str, tuple[str, str, str]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            z = (row.get("zip") or "").strip()
            if _ZIP.match(z):
                out[z] = ((row.get("city") or "").strip(),
                          (row.get("state") or "").strip().upper(),
                          (row.get("county") or "").strip())
    return out


def read_feed(path: Path, crosswalk: dict[str, tuple[str, str, str]],
              *, niches: Iterable[str] | None = None
              ) -> tuple[dict[str, list[dict]], FeedReport]:
    """Parse the wide feed into per-niche row lists, enriched and de-duplicated."""
    rep = FeedReport()
    wanted = set(niches) if niches else None
    buckets: dict[str, list[dict]] = {}
    seen: set[tuple[str, str]] = set()
    unknown: dict[str, int] = {}
    unmatched: set[str] = set()

    with path.open(newline="", encoding="utf-8-sig") as fh:
        for raw in csv.DictReader(fh):
            rep.rows_in += 1
            vertical = (raw.get("vertical") or "").strip()
            niche = VERTICALS.get(vertical.lower())
            if not niche:
                unknown[vertical or "(blank)"] = unknown.get(vertical or "(blank)", 0) + 1
                continue
            if wanted and niche not in wanted:
                continue

            zip_code = (raw.get("zip") or "").strip()
            if not _ZIP.match(zip_code):
                rep.skipped.append(f"{zip_code!r} is not a 5-digit ZIP")
                continue

            city = " ".join((raw.get("city") or "").split())
            state = (raw.get("state") or "").strip().upper()
            hit = crosswalk.get(zip_code)

            if not city or len(state) != 2:
                # The 15% case. Recover rather than discard.
                if not hit:
                    unmatched.add(zip_code)
                    continue
                city, state = city or hit[0], state if len(state) == 2 else hit[1]
                rep.recovered += 1

            if state in MILITARY:
                rep.skipped.append(f"{state} is a military region, not a buildable state")
                continue
            if state not in STATES:
                rep.skipped.append(f"unrecognised state code {state!r} (ZIP {zip_code})")
                continue

            county = canonical_county(hit[2]) if hit and hit[2] else ""
            if county:
                rep.county_added += 1

            key = (niche, zip_code)
            if key in seen:
                rep.duplicates += 1
                continue
            seen.add(key)

            try:
                payout = round(float(raw.get("net_payout") or 0), 2)
            except ValueError:
                payout = 0.0

            buckets.setdefault(niche, []).append({
                "zip": zip_code, "city": city, "state": state,
                "county": county or f"{STATES[state]} Statewide", "payout": payout,
            })
            rep.kept += 1

    rep.unknown_vertical = dict(sorted(unknown.items(), key=lambda kv: -kv[1]))
    rep.unmatched_zip = sorted(unmatched)[:40]
    for niche, rows in buckets.items():
        pays = sorted(r["payout"] for r in rows)
        rep.per_niche[niche] = {
            "rows": len(rows),
            "states": sorted({r["state"] for r in rows}),
            "cities": len({(r["city"].lower(), r["state"]) for r in rows}),
            "counties": len({(r["county"], r["state"]) for r in rows}),
            "no_county": sum(1 for r in rows if r["county"].endswith("Statewide")),
            "payout_median": pays[len(pays) // 2] if pays else 0,
            "payout_max": pays[-1] if pays else 0,
            "payout_total": round(sum(pays), 2),
        }
    return buckets, rep


def write_niche(root: Path, niche: str, rows: list[dict]) -> dict[str, int]:
    """
    Replace one niche's coverage wholesale.

    Never merges. A buyer publishes a complete footprint, and merging a new list
    into an old one keeps dropped ZIPs alive — publishing a ZIP the buyer has
    dropped is precisely the compliance failure the list exists to prevent.
    """
    folder = root / "data" / "coverage" / niche
    if folder.is_dir():
        for old in folder.glob("*.csv"):
            old.unlink()
    folder.mkdir(parents=True, exist_ok=True)

    by_state: dict[str, list[dict]] = {}
    for r in rows:
        by_state.setdefault(r["state"], []).append(r)

    written: dict[str, int] = {}
    for state, items in sorted(by_state.items()):
        with (folder / f"{state}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["zip", "city", "state", "county", "payout"])
            w.writeheader()
            w.writerows(sorted(items, key=lambda r: (r["city"], r["zip"])))
        written[state] = len(items)
    return written


def import_feed(root: Path, path: Path, *, niches: Iterable[str] | None = None
                ) -> FeedReport:
    crosswalk = load_crosswalk(root)
    buckets, rep = read_feed(path, crosswalk, niches=niches)
    for niche, rows in buckets.items():
        rep.per_niche[niche]["written"] = write_niche(root, niche, rows)
    return rep


def niche_value(root: Path, niche: str) -> dict:
    """Payout-weighted view of one niche's footprint, for deciding what to build."""
    folder = root / "data" / "coverage" / niche
    if not folder.is_dir():
        return {}
    per_state: dict[str, dict] = {}
    for csv_path in folder.glob("*.csv"):
        with csv_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                st = row["state"]
                try:
                    pay = float(row.get("payout") or 0)
                except ValueError:
                    pay = 0.0
                rec = per_state.setdefault(st, {"zips": 0, "total": 0.0, "cities": set()})
                rec["zips"] += 1
                rec["total"] += pay
                rec["cities"].add(row["city"].lower())
    return {st: {"zips": v["zips"], "cities": len(v["cities"]),
                 "total": round(v["total"], 2),
                 "avg": round(v["total"] / v["zips"], 2) if v["zips"] else 0}
            for st, v in sorted(per_state.items(), key=lambda kv: -kv[1]["total"])}
