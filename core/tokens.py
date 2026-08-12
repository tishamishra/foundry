"""
Foundry — dynamic tokens, in one place.

A {token} in a content block is filled in at render time from the business, the
city, and the service. There are two failure modes and this module addresses
both:

  * a WRONG SPELLING of a real token — ChatGPT writes {business} or
    {phone_number} or {location} instead of the canonical name. The alias table
    rewrites these to the real token so the block still renders. Being forgiving
    here is worth more than being strict, because the alternative is a literal
    "{business}" on the page and a blocked build.

  * a TOKEN THAT MAPS TO NOTHING — {rating}, {discount}, {years_established}.
    These cannot be invented, so the importer flags them at paste time rather
    than letting them surface as a build error later.

CANONICAL is the set the renderer actually fills. Keep it in sync with
render.base_context / location_context / service_context — this module is the
name authority the rest of the code checks against.
"""

from __future__ import annotations

import re

# The tokens the renderer resolves. (Mirrors render.base_context and the
# location/service context builders.)
CANONICAL = {
    "company", "brand", "phone", "phone_link", "email", "domain",
    "street", "hq_city", "hq_state", "hq_zip", "address",
    "years", "hours", "year",
    "niche", "niche_lower",
    "city", "city_slug", "state", "state_abbr", "county", "zips_short",
    "nearby", "slug",
    "service", "service_slug", "service_lower",
}

# Common ways people and models spell the same thing → the canonical token.
# The value must be a CANONICAL name.
ALIASES = {
    # the business
    "business": "company", "business_name": "company", "businessname": "company",
    "company_name": "company", "companyname": "company", "brand_name": "brand",
    "brandname": "brand", "org": "company", "organization": "company",
    # contact
    "phone_number": "phone", "phonenumber": "phone", "phone_no": "phone",
    "telephone": "phone", "tel": "phone", "contact": "phone",
    "contact_number": "phone", "call": "phone",
    "email_address": "email", "mail": "email",
    "website": "domain", "url": "domain", "site": "domain", "web": "domain",
    # place
    "location": "city", "town": "city", "area": "city", "place": "city",
    "city_name": "city", "cityname": "city", "locality": "city",
    "state_full": "state", "province": "state", "region": "state",
    "state_name": "state", "statename": "state",
    "state_code": "state_abbr", "st": "state_abbr", "state_abbrev": "state_abbr",
    "county_name": "county", "countyname": "county",
    "nearby_areas": "nearby", "nearby_cities": "nearby", "surrounding": "nearby",
    "nearby_towns": "nearby", "neighboring": "nearby",
    "zip": "zips_short", "zips": "zips_short", "zipcode": "zips_short",
    "zip_code": "zips_short", "zip_codes": "zips_short", "zipcodes": "zips_short",
    "postal": "zips_short", "postcode": "zips_short", "postal_code": "zips_short",
    # the work
    "service_name": "service", "servicename": "service", "service_type": "service",
    "servicetype": "service", "job": "service",
    "niche_name": "niche", "industry": "niche", "trade": "niche",
    "category": "niche", "vertical": "niche",
    # time
    "current_year": "year", "yr": "year", "this_year": "year",
}

_TOKEN = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def _walk(value, fn):
    if isinstance(value, dict):
        return {k: _walk(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, fn) for v in value]
    if isinstance(value, str):
        return fn(value)
    return value


def normalise(value):
    """Rewrite every alias token to its canonical form, anywhere in a string,
    dict, or list. A canonical or unknown token is left untouched."""
    def rewrite(text: str) -> str:
        return _TOKEN.sub(
            lambda m: "{" + ALIASES.get(m.group(1), m.group(1)) + "}", text)
    return _walk(value, rewrite)


def tokens_in(value) -> set[str]:
    found: set[str] = set()

    def scan(text: str) -> str:
        found.update(_TOKEN.findall(text))
        return text
    _walk(value, scan)
    return found


def unknown_tokens(value) -> list[str]:
    """Tokens that are neither canonical nor a known alias — these resolve to
    nothing and would render literally. Reported at import time."""
    return sorted(t for t in tokens_in(value)
                  if t not in CANONICAL and t not in ALIASES)
