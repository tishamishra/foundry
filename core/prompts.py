"""
Foundry — content prompts and CSV import.

One idea: the library's schema is already known to the software, so the software
should hand you a ChatGPT prompt that produces exactly that schema, and accept
the result back with no reformatting. You should never have to remember what
shape a `faqs` block is or which tokens are allowed — the prompt states it, and
the importer enforces it.

Three things live here:

  build_prompt(kind, niche, n)   a ready ChatGPT prompt for one block kind,
                                 embedding the exact YAML shape, the token
                                 rules, the banned claims, and an example.

  csv_to_blocks(kind, text)      parse a CSV (the format the prompt documents)
                                 into blocks the library accepts as-is.

  csv_template(kind)             the header row + one example, so "import
                                 compatible" is a file you can download, fill,
                                 and paste back.

The banned-claim list is imported from verify.py rather than restated, so the
prompt can never drift from what the QA gate actually rejects.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import yaml

from .library import KINDS, SERVICE_SCOPED_KINDS
from .verify import CLAIM_PATTERNS

# The tokens a block may contain. Anything depending on a location token must be
# wrapped in [[ ... ]] so it disappears cleanly when the token is empty.
TOKENS = [
    "{company}", "{brand}", "{phone}", "{city}", "{state}", "{state_abbr}",
    "{county}", "{nearby}", "{zips_short}", "{niche}", "{niche_lower}",
    "{service}", "{service_lower}", "{year}",
]
OPTIONAL_TOKENS = ["{city}", "{county}", "{nearby}", "{zips_short}", "{state}"]

# The banned claims, phrased for a human — derived from the QA gate's patterns
# so the two can never disagree.
BANNED = [
    "licensed / licence / license", "insured / fully insured",
    "24/7, round-the-clock, around the clock", "\"N years of experience\" or tenure",
    "family-owned", "BBB or A+ rated", "star ratings (5-star, 4.9-star)",
    "free estimate / free quote", "warranty / guarantee (as a claim)",
    "financing", "certified / certification", "award / award-winning",
    "volume claims (500+ jobs, 1000+ customers)",
]

# What each kind is, and how long each block should run. Word ranges are per
# block (per paragraph for the paras shape).
META: dict[str, dict[str, Any]] = {
    "taglines":        {"about": "a short headline tag under the H1", "words": "4-9"},
    "hero_intros":     {"about": "the homepage hero lede paragraph", "words": "28-50"},
    "hero_ctas":       {"about": "the call-to-action button label", "words": "3-7"},
    "trust_points":    {"about": "a hero tick-list point (short label + one sentence)", "words": "title 3-6, text 12-22"},
    "about_paras":     {"about": "an 'about us' block of 2-3 paragraphs", "words": "45-90 per paragraph"},
    "why_us":          {"about": "a 'why homeowners call us' point", "words": "title 3-6, text 18-30"},
    "process_steps":   {"about": "a step in 'how the job runs'", "words": "title 3-6, text 14-26"},
    "service_intros":  {"about": "the body intro for a service page (2 paragraphs)", "words": "45-90 per paragraph"},
    "service_heroes":  {"about": "the HERO lede on a service page — names {service}", "words": "22-40"},
    "service_bullets": {"about": "one deliverable a service includes", "words": "12-28"},
    "faqs":            {"about": "a question and its answer", "words": "answer 40-90"},
    "reviews":         {"about": "a customer review (first name + last initial, and the text)", "words": "45-85"},
    "location_intros": {"about": "the body intro for a city page (3-4 paragraphs)", "words": "45-90 per paragraph"},
    "location_heroes": {"about": "the HERO lede on a city page — uses {city}", "words": "22-32"},
    "location_service_intros": {"about": "the body intro for a service-in-city page (3 paragraphs)", "words": "40-80 per paragraph"},
    "cta_blocks":      {"about": "the quote-band call to action (button, heading, text)", "words": "text 40-70"},
    "closing_paras":   {"about": "the closing paragraph at the end of a page", "words": "70-120"},
    "signs":           {"about": "a 'sign worth a phone call' (title + explanation)", "words": "title 4-8, text 25-45"},
    "compare_rows":    {"about": "one row of a repair-vs-replace comparison", "words": "6-16 per cell"},
    "cost_factors":    {"about": "a factor that changes the price (title + text)", "words": "title 4-8, text 22-40"},
}

# Section-specific direction. `about` says WHAT a block is; GUIDE says HOW to
# write a good one for THAT section — its angle, what to include, what to avoid —
# so no two kinds get the same generic prompt. This is what makes each section's
# copy read as written for that spot on the page rather than interchangeable.
GUIDE: dict[str, str] = {
    "taglines": "A punchy promise that sits under the H1. Lead with the outcome or the "
        "trade-plus-place, not a full sentence — e.g. 'Roofing done right, {city}'. No period.",
    "hero_intros": "The first thing a visitor reads. Open on the problem or the promise, name "
        "the trade, and steer toward a call. Confident and benefit-led — not a company history.",
    "hero_ctas": "A button label that says what happens next: an action plus its value, e.g. "
        "'Get a free estimate' or 'Book an inspection'. No punctuation.",
    "trust_points": "One reason to trust the business — a short label and one backing sentence. "
        "This is where credentials belong: licensed & insured, 24/7 response, workmanship "
        "warranty, years in business. Each point a different proof.",
    "about_paras": "The story that builds confidence: who they are, how long they've worked, how "
        "they operate, what they stand for. Concrete and specific — never generic filler.",
    "why_us": "One differentiator that answers 'why call THEM' — a specific edge such as same-day "
        "response, upfront pricing, in-house crews, or guaranteed clean-up. Every point a "
        "distinct angle; never reword the same idea twice.",
    "process_steps": "One step in how the job runs, in sequence from first call to finished work. "
        "Set expectations and lower anxiety. Title = the step; text = what actually happens in it.",
    "service_intros": "The opening of a service page: what the service is, when a customer needs "
        "it, and how this business approaches it. Keyword-relevant and reassuring; it sets up the "
        "detail below, so don't cram everything in.",
    "service_heroes": "The banner lede on a service page. Name {service}, state the promise for "
        "THAT service in a sentence or two, and nudge toward a call. Specific to the service — a "
        "line that would fit any service is wrong here.",
    "service_bullets": "One concrete thing the customer actually gets — a real inclusion or "
        "deliverable ('full tear-off down to the deck', 'site magnet-swept for nails'), not a "
        "vague benefit. Tangible and specific to this exact service.",
    "faqs": "A real question a customer would type or ask on the phone, with a straight, helpful "
        "answer that quietly reassures and ends pointing toward a call. Spread them across cost, "
        "timing, process, warranty and emergencies.",
    "reviews": "A believable customer story in their own voice: a specific problem, what the "
        "business did, and the result. First name + last initial. Vary the situations — never a "
        "generic 'great service, highly recommend'.",
    "location_intros": "The body of a city page: tie the trade to THIS place — local weather, "
        "housing stock, neighbourhoods, why local homeowners need it — so the page earns the city "
        "rather than repeating the home page.",
    "location_heroes": "The banner lede on a city page. Use {city}, make the business feel local "
        "and already-here, in one or two confident sentences.",
    "location_service_intros": "The opening of a service-in-city page: this service, in this "
        "place. Blend the service promise with local relevance so it clones neither parent page.",
    "cta_blocks": "The quote-band push near the foot of the page: a heading, a persuasive line or "
        "two, and a button. Build urgency and lower friction — 'free', 'no obligation', 'fast "
        "response'.",
    "closing_paras": "A warm final paragraph that recaps the promise and asks for the call — the "
        "last nudge before the visitor leaves. Confident, not desperate.",
    "signs": "One warning sign that means 'call now' — a short title plus what it indicates and "
        "why it matters. Educational and specific to the service; build urgency honestly, never "
        "scare-monger.",
    "compare_rows": "One row of the decision a customer weighs (e.g. repair vs replacement): a "
        "factor, and the short honest value on each side. Help them self-diagnose which "
        "conversation they're in.",
    "cost_factors": "One thing that moves the price — a title and a plain explanation. Set honest "
        "expectations without publishing a number, and position the business as straight-talking.",
}


def guide_for(kind: str) -> str:
    return GUIDE.get(kind, META.get(kind, {}).get("about", kind))

# The YAML shape for each block SHAPE, shown to ChatGPT and to the user.
SHAPE_YAML = {
    "text":    '- "The block text, one string, with {tokens} allowed."',
    "titled":  '- title: "Short label"\n  text: "One or two sentences."',
    "paras":   '- paras:\n    - "First paragraph."\n    - "Second paragraph."',
    "qa":      '- q: "The question?"\n  a: "The answer, one or more sentences."',
    "review":  '- name: "Firstname L."\n  text: "The review, a specific story."',
    "cta":     '- button: "Call {phone}"\n  heading: "Short heading"\n  text: "One or two sentences."',
    "compare": '- factor: "What it addresses"\n  repair: "Short repair-side value"\n  replace: "Short replacement-side value"',
}

# CSV columns for each SHAPE. `paras` uses ONE column with paragraphs separated
# by a double pipe, because a CSV cell cannot hold a list.
SHAPE_CSV = {
    "text":    ["text"],
    "titled":  ["title", "text"],
    "paras":   ["paras"],            # paragraphs separated by ||
    "qa":      ["question", "answer"],
    "review":  ["name", "text"],
    "cta":     ["button", "heading", "text"],
    "compare": ["factor", "repair", "replace"],
}

PARA_SEP = "||"


def shape_of(kind: str) -> str:
    if kind not in KINDS:
        raise KeyError(f"unknown block kind {kind!r}")
    return KINDS[kind]


# --------------------------------------------------------------------------
# what each block type actually REQUIRES
# --------------------------------------------------------------------------
#
# The importer matches columns by header NAME (never position), so a CSV may
# carry any subset of columns in any order. Whether a row is valid depends on
# the BLOCK TYPE, not the CSV width: a `service_heroes` row needs only `text`,
# a `faqs` row needs `question` and `answer`. This table is the single source of
# truth for that — it drives both the validation errors and the required-field
# hint shown in the UI. Each entry is a list of field-GROUPS; a group is a tuple
# of interchangeable names ("one of these must be filled").
_SHAPE_REQUIRED: dict[str, list[tuple[str, ...]]] = {
    "text":    [("text",)],
    "titled":  [("title", "text")],          # a title or a body — at least one
    "paras":   [("paras",)],
    "qa":      [("question",), ("answer",)],
    "review":  [("text",)],                   # name is optional
    "cta":     [("text",)],                   # button/heading optional
    "compare": [("factor",), ("repair",), ("replace",)],
}


def required_fields(kind: str) -> list[str]:
    """Human labels for the fields a kind needs (niche + kind always implied)."""
    return [" or ".join(group) for group in _SHAPE_REQUIRED.get(shape_of(kind), [])]


# The per-kind schema the importer validates against — exactly the shape asked
# for: required = niche, kind, plus that block type's own required fields.
REQUIRED_SCHEMA: dict[str, list[str]] = {
    k: ["niche", "kind"] + required_fields(k) for k in KINDS
}


# Common header synonyms, so a natural CSV imports without column-name pedantry.
# The canonical field is tried first, then these — the first non-empty wins. This
# is what lets ChatGPT put a service_intros body in a `text` column (with `||`)
# instead of `paras` and still have it land correctly.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "text":     ("text", "body", "content", "paras", "paragraph", "paragraphs"),
    "paras":    ("paras", "text", "body", "content", "paragraph", "paragraphs"),
    "title":    ("title", "label", "heading", "headline"),
    "question": ("question", "q", "faq", "prompt"),
    "answer":   ("answer", "a", "response", "reply"),
    "name":     ("name", "author", "reviewer", "customer"),
    "button":   ("button", "button_text", "cta", "cta_text"),
    "heading":  ("heading", "headline", "title"),
    "factor":   ("factor", "feature", "aspect", "criteria"),
    "repair":   ("repair", "option_a", "left", "a"),
    "replace":  ("replace", "replacement", "option_b", "right", "b"),
}


def _resolve(row: dict, field: str) -> str:
    """Fetch a field's value by canonical name or a known synonym — first
    non-empty wins. Keys in `row` are already lower-cased and trimmed."""
    for alias in _FIELD_ALIASES.get(field, (field,)):
        v = (row.get(alias) or "").strip()
        if v:
            return v
    return ""


def _missing_required(kind: str, getter) -> list[str]:
    """Which required field-groups a row leaves unfilled. Empty list = valid.
    `getter(name)` returns the row's value for a header name (or "")."""
    missing = []
    for group in _SHAPE_REQUIRED.get(shape_of(kind), []):
        if not any((getter(name) or "").strip() for name in group):
            missing.append(" or ".join(group))
    return missing


# --------------------------------------------------------------------------
# the prompt
# --------------------------------------------------------------------------

def build_prompt(kind: str, niche_label: str, n: int = 20) -> str:
    shape = shape_of(kind)
    meta = META.get(kind, {"about": kind, "words": ""})
    yaml_shape = SHAPE_YAML[shape]
    csv_cols = SHAPE_CSV[shape]

    tokens = ", ".join(TOKENS)
    optional = ", ".join(OPTIONAL_TOKENS)
    words = f" Aim for {meta['words']} words." if meta.get("words") else ""

    return f"""You are writing SEO-optimized website content blocks for a {niche_label} business.
Produce {n} blocks of ONE type: {kind} — {meta['about']}.{words}

HOW TO WRITE THIS SECTION: {guide_for(kind)}

Write to rank and to convert: natural, keyword-relevant, benefit-led copy. Use the
language a customer would search for, and mention the services and selling points
that fit — 24/7 emergency response, licensed and insured work, warranties, free
estimates, financing, experience, and so on — wherever they suit the business.

OUTPUT FORMAT — return ONLY a YAML list, nothing before or after it, {n} items:

{_indent(yaml_shape)}

FORMAT RULES (these keep the import working — they are technical, not editorial):
1. Tokens you may use (they are filled in at build time, so leave them as-is,
   do not replace them with real values):
   {tokens}
2. Any clause that depends on one of these OPTIONAL tokens ({optional}) MUST be
   wrapped in [[ ... ]] so it disappears cleanly when the token is empty. Example:
   "We cover {{city}}[[, including {{nearby}}]]." Without this you get a stray
   comma on pages where the token has no value.
3. Every block must be DISTINCT from the others — a different angle or keyword
   focus, not the same sentence reworded. Exact duplicates are dropped on import.

Return the YAML list only.

--- OR, if you prefer a spreadsheet, output CSV with this header instead ---
{",".join(csv_cols)}
{_csv_hint(shape)}"""


def _indent(block: str, pad: str = "  ") -> str:
    return "\n".join(pad + line for line in block.splitlines())


def _csv_hint(shape: str) -> str:
    if shape == "paras":
        return f"(put the paragraphs in one cell, separated by {PARA_SEP})"
    return "(one block per row)"


# --------------------------------------------------------------------------
# CSV import
# --------------------------------------------------------------------------

def csv_template(kind: str) -> str:
    """Header + one worked example row, so the format is a file, not a paragraph."""
    shape = shape_of(kind)
    cols = SHAPE_CSV[shape]
    examples = {
        "text":    ["{company} — {niche_lower} done properly"],
        "titled":  ["We look before we quote", "Nobody prices the job from the kerb; the number comes after the assessment."],
        "paras":   [f"{{company}} covers {{city}}.{PARA_SEP}A second paragraph about the local area.{PARA_SEP}A third."],
        "qa":      ["How quickly can someone come out?", "Call {phone} and we will give you an honest position in the queue."],
        "review":  ["Marcus D.", "Came out, looked properly, and talked me out of the bigger job. Rare."],
        "cta":     ["Call {phone}", "Tell us what you are seeing", "A two-minute call usually narrows it down."],
        "compare": ["What it addresses", "One failed detail", "A system at the end of its life"],
    }[shape]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerow(examples)
    return buf.getvalue()


# --------------------------------------------------------------------------
# intelligence — where is a niche thin, and the small prompt to fix it
# --------------------------------------------------------------------------

def strength(sites: int, target: int) -> str:
    """A one-word read on a kind's depth, relative to how many distinct sites
    you want from this niche."""
    if sites <= 0:
        return "empty"
    if sites < max(2, target // 4):
        return "thin"
    if sites < target:
        return "ok"
    return "strong"


def coverage(cap: dict, target: int) -> list[dict]:
    """Turn capacity() output into a ranked, human-readable coverage list —
    weakest first, so the dashboard reads as a to-do list.

    `cap` is {kind: {pool, draws, sites, short}} from spawn.capacity.
    """
    rows = []
    for kind, c in cap.items():
        draws, pool, sites = c["draws"], c["pool"], c["sites"]
        want = draws * target                       # pool needed for `target` sites
        rows.append({
            "kind": kind,
            "pool": pool,
            "per_site": draws,
            "sites": sites,
            "target_pool": want,
            "gap": max(0, want - pool),
            "strength": strength(sites, target),
        })
    order = {"empty": 0, "thin": 1, "ok": 2, "strong": 3}
    rows.sort(key=lambda r: (order[r["strength"]], r["sites"], r["kind"]))
    return rows


_CSV_EXAMPLE = {
    "text":    {"text": "One SEO-optimized {niche_lower} line with {company} and {city}."},
    "titled":  {"title": "Short benefit label", "text": "One or two keyword-relevant sentences."},
    "paras":   {"paras": "First paragraph.||Second paragraph."},
    "qa":      {"question": "A question a customer would search?", "answer": "A helpful, keyword-relevant answer. Call {phone}."},
    "review":  {"name": "Marcus D.", "text": "A specific, positive customer story."},
    "cta":     {"button": "Call {phone}", "heading": "Short heading", "text": "One or two persuasive sentences."},
    "compare": {"factor": "What it addresses", "repair": "Left-side value", "replace": "Right-side value"},
}


def intel_prompt(niche_slug: str, niche_label: str, kind: str, n: int,
                 words: str | None = None,
                 services: list[dict] | None = None) -> str:
    """The small, focused prompt the dashboard hands you for one weak section.
    It outputs rows in the MASTER-CSV format, so the result drops straight into
    the bulk importer.

    For a service-scoped kind, the prompt is told to fill the `service` column
    with the niche's own service slugs and to spread the blocks across them, so
    filling a weak section also curates it per service rather than niche-wide."""
    shape = shape_of(kind)
    meta = META.get(kind, {"about": kind, "words": ""})
    words = words or meta.get("words", "")
    scoped = kind in SERVICE_SCOPED_KINDS
    svc = [s for s in (services or []) if s.get("slug")] if scoped else []

    buf = io.StringIO()
    wtr = csv.DictWriter(buf, fieldnames=GLOBAL_COLUMNS, extrasaction="ignore")
    wtr.writeheader()
    if svc:
        for s in svc[:2]:
            wtr.writerow({"niche": niche_slug, "kind": kind,
                          "service": s["slug"], **_CSV_EXAMPLE[shape]})
    else:
        wtr.writerow({"niche": niche_slug, "kind": kind, **_CSV_EXAMPLE[shape]})
    example_csv = buf.getvalue().strip()
    wc = f", about {words} words each" if words else ""

    if svc:
        slugs = ", ".join(s["slug"] for s in svc)
        service_rule = (
            f"This is a SERVICE-SPECIFIC block type: fill the 'service' column on every "
            f"row with the service the block is about, using one of these exact slugs — "
            f"{slugs}. Spread the {n} blocks across the services and make each block fit "
            f"ONLY its own service (a block that would suit any service should leave "
            f"'service' blank instead). ")
    elif scoped:
        service_rule = ("Fill the 'service' column only when a block is written for one "
                        "specific service; otherwise leave it blank. ")
    else:
        service_rule = "Leave the 'service' column blank — this block type is not service-specific. "

    return (
        f"Write {n} SEO-optimized {kind} blocks for a {niche_label} business{wc}. "
        f"Each block is {meta['about']}.\n"
        f"How to write this section: {guide_for(kind)}\n"
        f"Output ONLY CSV in this exact format — the same header, one block per row, "
        f"filling only the columns this block type uses:\n\n"
        f"{example_csv}\n\n"
        f"Rules: keep niche as \"{niche_slug}\" and kind as \"{kind}\" on every row. "
        f"{service_rule}"
        f"Use tokens {{company}}, {{city}}, {{phone}} where natural, and "
        f"wrap any {{city}}/{{county}}/{{nearby}} clause in [[ ... ]]. Every row distinct. "
        f"Return the CSV only.")


# --------------------------------------------------------------------------
# one file for every niche
# --------------------------------------------------------------------------
#
# A single master CSV that can hold blocks for ALL niches and ALL kinds. Each
# ROW is one block, tagged with its niche and its kind; the field columns are the
# union of every shape, so a row fills only the ones its kind needs. You keep one
# growing file, and re-importing it is safe — duplicates are skipped, so only the
# rows you added since last time actually land.

GLOBAL_COLUMNS = [
    "niche", "kind", "service",                         # routing (+ optional service tag)
    "text", "title", "question", "answer", "name",     # per-shape fields …
    "button", "heading", "factor", "repair", "replace", "paras",
]


def _tag_block(block: Any, service: str) -> Any:
    """Bind a block to one service. A text-kind block is a bare string, so it is
    wrapped as {text, for}; every other shape is already a dict and just gains a
    `for` key. Non-service kinds ignore the service column entirely."""
    if isinstance(block, str):
        return {"text": block, "for": service}
    if isinstance(block, dict):
        return {**block, "for": service}
    return block


# The service-scoped kinds in the order a service page reads top to bottom, so a
# generated prompt fills a page's sections in a sensible sequence.
SERVICE_PROMPT_KINDS = [
    "service_heroes", "service_intros", "service_bullets",
    "signs", "cost_factors", "compare_rows",
]


def service_prompt(niche_slug: str, niche_label: str, service_label: str,
                   service_slug: str, n: int = 8,
                   kinds: list[str] | None = None) -> str:
    """A ready prompt that produces master-CSV rows for ONE service, already
    tagged. Every row comes back with niche + service pre-filled, so pasting the
    result into the global importer binds the copy to exactly that service — and
    nowhere else. This is what makes a service page read as that service instead
    of the trade in general."""
    kinds = kinds or SERVICE_PROMPT_KINDS
    rows = []
    for k in kinds:
        rows.append({"niche": niche_slug, "kind": k, "service": service_slug,
                     **_CSV_EXAMPLE[shape_of(k)]})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=GLOBAL_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    example = buf.getvalue().strip()

    kind_lines = "\n".join(
        f"  - {k}"
        f"{(' (~' + META[k]['words'] + ' words)') if META.get(k, {}).get('words') else ''}"
        f": {guide_for(k)}"
        for k in kinds)

    return (
        f"You are writing SEO-optimized website content for the \"{service_label}\" "
        f"service offered by a {niche_label} business.\n"
        f"CRITICAL: every block must be SPECIFIC to {service_label} — describe what "
        f"{service_label} actually involves, its own signs, costs and trade-offs. Do "
        f"NOT write about the trade in general, and do NOT write anything that would "
        f"fit a different service.\n\n"
        f"Write {n} DISTINCT blocks for EACH of these block types:\n{kind_lines}\n\n"
        f"OUTPUT: return ONLY CSV with this exact header. On EVERY row keep "
        f"niche=\"{niche_slug}\" and service=\"{service_slug}\", set kind to the block "
        f"type, and fill only the columns that block type uses:\n\n"
        f"{example}\n\n"
        f"RULES: benefit-led, keyword-relevant copy in the language a customer would "
        f"search. You may reference 24/7 response, licensed and insured work, free "
        f"estimates and the like where they fit. Use tokens {{company}}, {{city}}, "
        f"{{phone}} where natural, and wrap any {{city}}/{{county}}/{{nearby}} clause in "
        f"[[ ... ]] so it disappears when empty. Every row must be distinct. Return the "
        f"CSV only.")


# which global columns each shape reads (the field names match SHAPE_CSV)
_SHAPE_FIELDS = {
    "text":    ["text"],
    "titled":  ["title", "text"],
    "paras":   ["paras"],
    "qa":      ["question", "answer"],
    "review":  ["name", "text"],
    "cta":     ["button", "heading", "text"],
    "compare": ["factor", "repair", "replace"],
}


def global_template() -> str:
    """The master file: the header plus a worked example row for several kinds
    across two niches, so ChatGPT can see the pattern before filling it."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=GLOBAL_COLUMNS, extrasaction="ignore")
    w.writeheader()
    rows = [
        {"niche": "roofing", "kind": "hero_intros",
         "text": "Protect your home with dependable roofing from {company}[[ across {city}, {state}]]. Call {phone}."},
        {"niche": "roofing", "kind": "taglines", "text": "Roofing done right, {city}"},
        {"niche": "roofing", "kind": "faqs",
         "question": "Do I need a full roof replacement?",
         "answer": "Not always — many roofs fail at a detail that can be repaired. Call {phone} for an assessment."},
        {"niche": "roofing", "kind": "reviews",
         "name": "Marcus D.", "text": "Came out, found the real problem, and fixed it the same day."},
        {"niche": "roofing", "kind": "why_us",
         "title": "24/7 emergency response", "text": "Storm damage does not wait, and neither do we."},
        {"niche": "roofing", "kind": "compare_rows",
         "factor": "What it addresses", "repair": "One failed detail", "replace": "A roof at end of life"},
        {"niche": "roofing", "kind": "service_bullets", "service": "roof-replacement",
         "text": "Full tear-off down to the deck, every old layer removed and hauled away"},
        {"niche": "roofing", "kind": "service_bullets", "service": "gutter-installation",
         "text": "Seamless gutters formed on site to the exact run length"},
        {"niche": "roofing", "kind": "about_paras",
         "paras": "First paragraph about the company.||Second paragraph about the work."},
        {"niche": "water-damage", "kind": "hero_intros",
         "text": "Fast water damage restoration from {company}[[ in {city}, {state}]]. Available 24/7 — call {phone}."},
        {"niche": "water-damage", "kind": "faqs",
         "question": "How quickly can you arrive?",
         "answer": "We prioritise active flooding and aim to be on site fast. Call {phone}."},
    ]
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def parse_global_csv(text: str, valid_niches: set[str]) -> dict[str, Any]:
    """Turn a master CSV into {(niche, kind): [blocks]} plus a report of what was
    skipped and why.

    Columns are matched by HEADER NAME, never by position: any order, any subset,
    extra unknown columns ignored, headers trimmed and case-insensitive, UTF-8 and
    a leading BOM tolerated. A row is judged by its BLOCK TYPE's required fields —
    not the CSV's width — and every rejection is reported with its row number and
    the exact field that was missing, so a valid short CSV never fails silently."""
    text = (text or "").strip("﻿ \n\r\t")
    result: dict[tuple[str, str], list[Any]] = {}
    report = {"rows": 0, "blocks": 0, "unknown_niche": set(), "unknown_kind": set(),
              "empty_rows": 0, "problems": []}
    if not text:
        return {"grouped": result, "report": report}

    reader = csv.DictReader(io.StringIO(text))
    have = {(c or "").strip().lower() for c in (reader.fieldnames or [])}
    if "niche" not in have or "kind" not in have:
        missing = [c for c in ("niche", "kind") if c not in have]
        report["error"] = (
            f"the header is missing the {' and '.join(chr(34)+m+chr(34) for m in missing)} "
            f"column{'s' if len(missing) > 1 else ''}. Every row needs a niche and a kind. "
            f"Found: {', '.join(sorted(have)) or '(no header row)'}.")
        return {"grouped": result, "report": report}

    # Header is line 1, so the first data row is line 2.
    for rownum, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        if not any(row.values()):
            continue
        report["rows"] += 1
        niche, kind = row.get("niche", ""), row.get("kind", "")
        if niche not in valid_niches:
            report["unknown_niche"].add(niche or "(blank)")
            report["problems"].append(f'Row {rownum}: unknown niche "{niche or "(blank)"}".')
            continue
        if kind not in KINDS:
            report["unknown_kind"].add(kind or "(blank)")
            report["problems"].append(f'Row {rownum}: unknown kind "{kind or "(blank)"}".')
            continue
        getter = lambda name, r=row: _resolve(r, name)
        missing = _missing_required(kind, getter)
        if missing:
            fields = ", ".join(chr(34) + m + chr(34) for m in missing)
            report["problems"].append(
                f'Row {rownum} ({kind}): missing required field {fields}.')
            report["empty_rows"] += 1
            continue
        block = _assemble(KINDS[kind], getter)
        if block is None:                        # defensive — should not happen now
            report["problems"].append(f'Row {rownum} ({kind}): could not read the row.')
            report["empty_rows"] += 1
            continue
        service = row.get("service", "")
        if service and kind in SERVICE_SCOPED_KINDS:
            block = _tag_block(block, service)
            report.setdefault("tagged", 0)
            report["tagged"] += 1
        result.setdefault((niche, kind), []).append(block)
        report["blocks"] += 1

    report["unknown_niche"] = sorted(report["unknown_niche"])
    report["unknown_kind"] = sorted(report["unknown_kind"])
    return {"grouped": result, "report": report}


def _unquote(s: str) -> str:
    """Strip one layer of surrounding quotes and a trailing comma, and undouble
    the CSV quote-escaping (a doubled quote inside a quoted cell means one quote)."""
    s = s.strip().rstrip(",").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].replace(s[0] * 2, s[0])
    return s.strip()


def smart_parse(kind: str, text: str) -> list[Any]:
    """Accept whatever the operator pastes and turn it into blocks.

    The point is that the box you paste into should not matter. Three inputs are
    common and all three work:

      * a YAML list (what the prompt asks ChatGPT for)
      * a CSV, with or without a header row
      * one block per line — quoted or not — which is how people paste a batch
        of hero or tagline lines

    It tries them in that order and returns whatever first yields blocks. A wrong
    guess yields nothing, so it falls through rather than producing garbage.
    """
    shape = shape_of(kind)
    text = (text or "").strip("﻿ \n\r\t")
    if not text:
        return []

    # 1) YAML list — but only if it really is a list. A bare quoted line is a
    #    valid YAML *string*, not a list, so it correctly falls through to (3).
    try:
        y = yaml.safe_load(text)
    except Exception:                                          # noqa: BLE001
        y = None
    if isinstance(y, list) and y:
        return _coerce(kind, y)

    # 2) Multi-column shapes are always CSV (a header or positional columns).
    if shape not in ("text", "paras"):
        return csv_to_blocks(kind, text)

    # 3) Single-value shapes: one block per line. Splitting on commas here would
    #    truncate a sentence that contains a comma, so each whole line is the
    #    block. A lone header line ("text" / "paras") is dropped.
    cols = SHAPE_CSV[shape]
    out: list[Any] = []
    for raw in text.splitlines():
        line = _unquote(raw)
        if not line or line.lower() in cols:
            continue
        if shape == "text":
            out.append(line)
        else:
            paras = [p.strip() for p in line.split(PARA_SEP) if p.strip()]
            if paras:
                out.append({"paras": paras})
    return out


def _coerce(kind: str, items: list) -> list[Any]:
    """A YAML list may hold plain strings for a text kind, or dicts. Keep dicts
    as-is; wrap bare strings for the shape so both paste styles work."""
    shape = shape_of(kind)
    out = []
    for it in items:
        if isinstance(it, dict):
            out.append(it)
        elif isinstance(it, str) and shape == "text":
            out.append(it)
        elif isinstance(it, str) and shape == "paras":
            out.append({"paras": [it]})
        elif isinstance(it, list) and shape == "paras":
            out.append({"paras": [str(p) for p in it]})
    return out


def csv_to_blocks(kind: str, text: str) -> list[Any]:
    """Parse pasted/uploaded CSV into blocks the library accepts as-is.

    Tolerant of a header row in any column order, extra whitespace, and a
    trailing blank line. A row missing a required column is skipped rather than
    silently producing a malformed block."""
    shape = shape_of(kind)
    cols = SHAPE_CSV[shape]
    text = text.strip("﻿ \n\r\t")
    if not text:
        return []

    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []

    # A header row is one whose cells name known fields (canonical OR a synonym)
    # for this shape — so columns match by NAME, in any order, and `text` is
    # accepted where `paras` is expected. Otherwise the cells are positional.
    head = [c.strip().lower() for c in rows[0]]
    known = {"niche", "kind", "service"}
    for c in cols:
        known |= set(_FIELD_ALIASES.get(c, (c,)))
    has_header = any(h in known for h in head)

    out: list[Any] = []
    if has_header:
        body = rows[1:]
        for row in body:
            if not any((c or "").strip() for c in row):
                continue
            rowmap = {head[i]: row[i] for i in range(min(len(head), len(row)))}
            block = _assemble(shape, lambda name, m=rowmap: _resolve(m, name))
            if block is not None:
                out.append(block)
    else:
        for row in rows:
            if not any((c or "").strip() for c in row):
                continue
            pos = {cols[i]: row[i] for i in range(min(len(cols), len(row)))}
            block = _assemble(shape, lambda name, m=pos: (m.get(name, "") or "").strip())
            if block is not None:
                out.append(block)
    return out


def _assemble(shape: str, cell) -> Any:
    if shape == "text":
        v = cell("text")
        return v or None
    if shape == "titled":
        t, x = cell("title"), cell("text")
        return {"title": t, "text": x} if (t or x) else None
    if shape == "paras":
        raw = cell("paras")
        paras = [p.strip() for p in raw.split(PARA_SEP) if p.strip()]
        return {"paras": paras} if paras else None
    if shape == "qa":
        q, a = cell("question"), cell("answer")
        return {"q": q, "a": a} if (q and a) else None
    if shape == "review":
        n, x = cell("name"), cell("text")
        return {"name": n, "text": x} if x else None
    if shape == "cta":
        return {"button": cell("button"), "heading": cell("heading"), "text": cell("text")} \
            if cell("text") else None
    if shape == "compare":
        f, r, p = cell("factor"), cell("repair"), cell("replace")
        return {"factor": f, "repair": r, "replace": p} if (f and r and p) else None
    return None
