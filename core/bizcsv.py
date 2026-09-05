"""
Foundry — flexible CSV import for BUSINESSES.

One parser, two callers:

  * the New Business form's "Upload CSV" box, for a single row that pre-fills
    the fields so you can review before saving; and
  * the bulk importer, for a file of many rows that become many businesses.

It is deliberately forgiving in the same spirit as the block importer: columns
may appear in any order, headers are matched by NAME (with common synonyms), and
extra columns are ignored. Only `company` and `phone` are required — everything
else is optional and simply left blank when absent. Errors are per-row and plain
("Row 4: missing company"), never "no importable rows found".
"""

from __future__ import annotations

import csv
import io
from typing import Any

# header name -> the canonical field it fills. Everything is lower-cased and
# stripped of spaces/underscores/hyphens before lookup, so "Company Name",
# "company_name" and "companyname" all match the same key.
_ALIASES: dict[str, str] = {}


def _add(field: str, *names: str) -> None:
    for n in names:
        _ALIASES[_norm(n)] = field


def _norm(s: str) -> str:
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


_add("company", "company", "companyname", "business", "businessname", "name")
_add("brand", "brand", "dba", "tradingas", "tradename")
_add("category", "category", "categories", "type", "businesstype", "niche",
     "vertical", "industry", "maincategory", "primarycategory", "gmbcategory",
     "googlecategory", "mapscategory", "servicetype", "trade")
_add("phone", "phone", "telephone", "tel", "phonenumber", "contact", "mobile", "cell")
_add("email", "email", "emailaddress", "mail")
_add("street", "street", "address", "address1", "streetaddress", "addr", "addressline1")
_add("city", "city", "town", "locality")
_add("state", "state", "region", "province")
_add("zip", "zip", "zipcode", "postal", "postcode", "postalcode")
_add("years", "years", "yearsinbusiness", "established", "yrs", "yearsofexperience", "experience")
_add("hours", "hours", "businesshours", "openinghours", "timings", "workinghours")
_add("rating", "rating", "stars", "starrating", "googlerating", "score")
_add("reviews", "reviews", "reviewcount", "reviews", "numreviews", "totalreviews", "ratingcount")
_add("website", "website", "url", "site", "webaddress", "web", "homepage")
_add("warranty", "warranty", "warrantyyears", "guarantee", "warrantyyrs")
_add("free_estimates", "freeestimates", "freeestimate", "freequote", "freequotes", "freeinspection")
_add("licensed", "licensed", "license", "licence", "licenced")
_add("insured", "insured", "insurance")
_add("emergency_24_7", "emergency", "emergency247", "247", "24x7", "emergencyservice", "emergency24_7")
_add("financing", "financing", "finance", "paymentplans", "payment")
_add("family_owned", "familyowned", "family", "familyrun", "familybusiness")

_BOOL_FIELDS = {"free_estimates", "licensed", "insured", "emergency_24_7",
                "financing", "family_owned"}
_TRUEY = {"yes", "y", "true", "t", "1", "x", "on", "checked", "✓", "✔", "yeah"}
_FALSEY = {"no", "n", "false", "f", "0", "off", "", "-", "none", "na", "n/a"}


def _truthy(value: str) -> bool:
    return _norm_val(value) in _TRUEY


def _norm_val(value: str) -> str:
    return str(value or "").strip().lower()


def _rating(value: str) -> float | None:
    """A star rating like '4.8' -> 4.8, clamped to 0-5; blank/garbage -> None."""
    try:
        r = float(str(value or "").strip())
    except ValueError:
        return None
    return round(r, 1) if 0 < r <= 5 else None


def _digits(value: str) -> int | bool:
    """First run of digits in the cell, or False when there is none — so '15
    years', '15', 'fifteen' become 15, 15, False respectively."""
    run = ""
    for ch in str(value or ""):
        if ch.isdigit():
            run += ch
        elif run:
            break
    return int(run) if run else False


# Free-text business category (e.g. a scraped Google-Maps type like "Plumber",
# "Roofing contractor", "Hair salon") -> the engine's niche slug. Ordered most-
# specific first, matched as a substring of the lower-cased category. A category
# that matches nothing maps to "" — the business still imports, but it is
# UNCATEGORISED and will never appear on a trade directory (so a salon or a
# dentist never lands on the plumbing directory). Declared, never guessed.
_CATEGORY_NICHE: list[tuple[tuple[str, ...], str]] = [
    (("bathroom remodel", "bath remodel"), "bathroom-remodeling"),
    (("kitchen remodel",), "kitchen-remodeling"),
    (("water damage", "water restoration", "water mitigation"), "water-damage"),
    (("fire damage", "smoke damage", "fire restoration"), "fire-damage"),
    (("mold",), "mold-removal"),
    (("biohazard", "crime scene", "trauma clean"), "biohazard"),
    (("foundation",), "foundation-repair"),
    (("waterproof",), "waterproofing"),
    (("garage door",), "garage-door"),
    (("gutter",), "gutters"),
    (("appliance",), "appliance-repair"),
    (("dui", "dwi"), "dui-dwi-attorneys"),
    (("personal injury", "injury attorney", "injury lawyer"), "personal-injury-attorneys"),
    (("accident attorney", "accident lawyer", "car accident", "auto accident"), "auto-accident-attorneys"),
    (("plumb", "rooter", "drain", "sewer", "septic"), "plumbing"),
    (("roof",), "roofing"),
    (("hvac", "heating", "air condition", "furnace", "cooling", "ac repair"), "hvac"),
    (("electric",), "electrical"),
    (("pest", "exterminat", "termite"), "pest-control"),
    (("landscap", "lawn care", "lawn service"), "landscaping"),
    (("paint",), "painting"),
    (("siding",), "siding"),
    (("window",), "windows"),
    (("deck",), "deck"),
]

# Exact niche slugs the engine knows — so a CSV that already carries the slug
# ("plumbing") or a close label ("Plumbing") is honoured directly.
_NICHE_SLUGS = {
    "appliance-repair", "auto-accident-attorneys", "bathroom-remodeling", "biohazard",
    "deck", "dui-dwi-attorneys", "electrical", "fire-damage", "foundation-repair",
    "garage-door", "gutters", "hvac", "kitchen-remodeling", "landscaping",
    "mold-removal", "painting", "personal-injury-attorneys", "pest-control",
    "plumbing", "roofing", "siding", "water-damage", "waterproofing", "windows",
}


def niche_from_category(text: str) -> str:
    """Map a free-text business category to an engine niche slug, or '' if none."""
    t = str(text or "").strip().lower()
    if not t:
        return ""
    slug = t.replace(" ", "-").replace("_", "-").replace("/", "-")
    if slug in _NICHE_SLUGS:
        return slug
    for needles, niche in _CATEGORY_NICHE:
        if any(n in t for n in needles):
            return niche
    return ""


def _row_to_payload(cells: dict[str, str]) -> dict[str, Any]:
    """Map one resolved {canonical_field: value} into a save_business payload."""
    g = lambda k: (cells.get(k) or "").strip()          # noqa: E731
    facts = {
        "years_in_business": _digits(cells.get("years", "")),
        "hours": g("hours") or None,
        "warranty_years": _digits(cells.get("warranty", "")),
    }
    rating = _rating(cells.get("rating", ""))
    if rating is not None:
        facts["rating"] = rating
    reviews = _digits(cells.get("reviews", ""))
    if reviews:
        facts["review_count"] = reviews
    for b in _BOOL_FIELDS:
        facts[b] = _truthy(cells.get(b, ""))
    category = g("category")
    return {
        "company": g("company"), "brand": g("brand"),
        "phone": g("phone"), "email": g("email"),
        "street": g("street"), "city": g("city"),
        "state": g("state"), "zip": g("zip"),
        "website": g("website"),
        "category": category,
        "niche": niche_from_category(category),
        "facts": facts,
    }


def parse(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse CSV text into (payloads, problems).

    Each payload is ready for save_business. `problems` is a human list of the
    rows that could not be used and why. A row with no company or no phone is
    reported and skipped; everything else is best-effort."""
    payloads: list[dict[str, Any]] = []
    problems: list[str] = []

    text = (text or "").lstrip("﻿")                 # strip a leading BOM
    if not text.strip():
        return [], ["The file was empty."]

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], ["No header row was found. The first line must name the columns."]

    # Map each real header to a canonical field once, up front.
    colmap: dict[str, str] = {}
    for raw in reader.fieldnames:
        field = _ALIASES.get(_norm(raw or ""))
        if field:
            colmap[raw] = field

    if "company" not in colmap.values() or "phone" not in colmap.values():
        problems.append("The header needs at least a company column and a phone "
                        "column. Recognised names include company/business/name "
                        "and phone/telephone/tel.")
        return [], problems

    for i, raw_row in enumerate(reader, start=2):        # row 1 was the header
        cells: dict[str, str] = {}
        for header, value in raw_row.items():
            field = colmap.get(header)
            if field and value is not None and str(value).strip():
                # keep the first non-empty value if a field maps twice
                cells.setdefault(field, str(value))
        payload = _row_to_payload(cells)
        if not payload["company"]:
            problems.append(f"Row {i}: no company name — skipped.")
            continue
        if not payload["phone"]:
            problems.append(f"Row {i}: '{payload['company']}' has no phone — skipped.")
            continue
        payloads.append(payload)

    if not payloads and not problems:
        problems.append("No data rows were found under the header.")
    return payloads, problems


TEMPLATE_HEADER = ("company,category,phone,email,street,city,state,zip,years,hours,"
                   "warranty,free_estimates,licensed,insured,emergency_24_7,"
                   "financing,family_owned")
TEMPLATE_EXAMPLE = ('Control Check Roofing,Roofing contractor,+1 833 000 0001,hello@example.com,'
                    '1 Test Way,Marietta,GA,30060,12,Mon-Fri 8:00-18:00,10,'
                    'yes,yes,yes,yes,no,yes')


def template_csv() -> str:
    return TEMPLATE_HEADER + "\n" + TEMPLATE_EXAMPLE + "\n"
