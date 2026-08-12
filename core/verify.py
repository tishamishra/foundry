"""
Foundry — verification.

Three layers, all of them run against RENDERED OUTPUT rather than against
intermediate state. Both parent engines arrived at that rule the hard way and
each implemented only half of it:

  Engine B  shipped a live site with all 89 images broken while passing every
            gate — "text was checked exhaustively, pixels were never checked at
            all". It has the semantic half.

  Engine A  "'looks fine on my screen' is not a claim that survives 200 sites"
            — Playwright at six widths against hostile fixtures. It has the
            layout half.

Foundry runs both, and adds the layer neither had: uniqueness as a MEASURED,
enforced budget rather than an identifier comparison. Engine A's `check` only
proved that two sites carried different variant *ids*; two variants could be
90% identical and it printed green. Engine B actually measured the thing —
60-91% between untreated clones, 3-6% for a genuine rewrite — so those numbers
are where the thresholds start.

Every report carries `not_verified`. A green result that hides its blind spots
is worse than a red one.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PERMS = 128
_MASK32 = 0xFFFFFFFF
_WORD = re.compile(r"[a-z0-9']+")


# --------------------------------------------------------------------------
# uniqueness
# --------------------------------------------------------------------------

def shingles(text: str, n: int = 5) -> set[int]:
    words = _WORD.findall(text.lower())
    if len(words) < n:
        return set()
    return {
        int.from_bytes(
            hashlib.blake2b(" ".join(words[i:i + n]).encode(), digest_size=4).digest(),
            "big")
        for i in range(len(words) - n + 1)
    }


def signature(text: str, perms: int = PERMS) -> list[int]:
    """MinHash signature. Cheap to store, cheap to compare, close enough to
    Jaccard that a 25% threshold means what it says."""
    shs = shingles(text)
    if not shs:
        return [0] * perms
    sig = []
    for i in range(perms):
        a = (i * 2 + 1) & _MASK32
        b = (i * 0x9E3779B1) & _MASK32
        sig.append(min(((a * s + b) & _MASK32) for s in shs))
    return sig


def similarity(sig_a: list[int], sig_b: list[int]) -> float:
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    return sum(1 for x, y in zip(sig_a, sig_b) if x == y) / len(sig_a)


def load_shipped(root: Path) -> dict[str, Any]:
    path = root / "data" / "shipped.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def record_shipped(root: Path, site_id: str, niche: str, sig: list[int],
                   csig: list[int] | None = None) -> None:
    path = root / "data" / "shipped.json"
    data = load_shipped(root)
    data[site_id] = {"niche": niche, "sig": sig, "csig": csig or []}
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def nearest_shipped(root: Path, site_id: str, niche: str, sig: list[int],
                    key: str = "sig") -> tuple[str | None, float]:
    worst, score = None, 0.0
    for other, rec in load_shipped(root).items():
        if other == site_id or rec.get("niche") != niche:
            continue
        s = similarity(sig, rec.get(key) or [])
        if s > score:
            worst, score = other, s
    return worst, score


# --------------------------------------------------------------------------
# claims
# --------------------------------------------------------------------------

# A claim pattern maps to the fact that would make it true. If the business
# record does not supply that fact, the claim is a fabrication.
#
# "A fabricated credential is a legal problem, not a style problem." — Engine B
CLAIM_PATTERNS: list[tuple[str, str, str]] = [
    (r"\blicen[cs]ed\b",                            "licensed",         "licensing claim"),
    (r"\b(?:fully\s+)?insured\b",                   "insured",          "insurance claim"),
    (r"\b(?:24/7|24-7|round[- ]the[- ]clock|around the clock)\b",
                                                     "emergency_24_7",  "24/7 availability"),
    (r"\b\d{1,3}\+?\s*years?\s+(?:of\s+)?(?:experience|in business)\b",
                                                     "years_in_business", "tenure claim"),
    (r"\bfamily[- ]owned\b",                        "family_owned",     "ownership claim"),
    (r"\bBBB\b|\bA\+\s*rat(?:ed|ing)\b",            "bbb_rating",       "BBB claim"),
    (r"\b[0-5](?:\.\d)?\s*[- ]?star\b",             "rating",           "star-rating claim"),
    (r"\bfree estimates?\b",                        "free_estimates",   "free-estimate offer"),
    (r"\bwarrant(?:y|ies)\b",                       "warranty_years",   "warranty claim"),
    (r"\bfinancing\b",                              "financing",        "financing offer"),
    (r"\bcertifi(?:ed|cation)\b",                   "certifications",   "certification claim"),
    (r"\baward[- ]winning\b|\bawards?\b",           "awards",           "award claim"),
    (r"\b(?:\d{2,3},?\d{0,3})\+?\s*(?:reviews|customers|jobs)\b",
                                                     "volume_claims",   "volume claim"),
]

SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9_\-]{20,}",           "API key"),
    (r"AKIA[0-9A-Z]{16}",                 "AWS access key"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"(?i)\b(?:api[_-]?key|secret|password)\s*[:=]\s*['\"][^'\"]{12,}", "inline credential"),
]


def _fact_supplied(facts: dict, name: str) -> bool:
    v = facts.get(name)
    if v is None or v is False:
        return False
    if isinstance(v, str) and not v.strip():
        return False
    if isinstance(v, (list, dict)) and not v:
        return False
    return True


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------

@dataclass
class Finding:
    rule: str
    severity: str
    message: str
    why: str
    evidence: list[str] = field(default_factory=list)
    owner: str = ""


@dataclass
class Report:
    site_id: str
    findings: list[Finding] = field(default_factory=list)
    not_verified: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def blockers(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "blocker"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def passed(self) -> bool:
        return not self.blockers


DEFAULT_RULES: dict[str, dict[str, str]] = {
    "unresolved-tokens": {"severity": "blocker", "owner": "render", "why":
        "A visible {token} on a rendered page tells a visitor the site is generated. "
        "Engine B shipped a hero reading 'Dumpster Rentals for{CITY}, {STATE}'."},
    "images-exist": {"severity": "blocker", "owner": "assets", "why":
        "An image reference with no file behind it renders as a broken box, and on a "
        "hero it can leave white text on a white rectangle."},
    "asset-invalid": {"severity": "blocker", "owner": "assets", "why":
        "A file named .jpg that is actually an HTML sign-in page passes every size "
        "check and breaks every page it appears on."},
    "foreign-identity": {"severity": "blocker", "owner": "graph", "why":
        "Another business's name, phone, domain or street address on this site reaches "
        "the footer, the contact page and the LocalBusiness schema Google reads."},
    "out-of-area": {"severity": "blocker", "owner": "graph", "why":
        "The buyer's rule is that any location outside the payable list is not covered "
        "and must not be published. This is compliance, not preference."},
    "fabricated-claims": {"severity": "blocker", "owner": "library", "why":
        "A licence, rating, warranty or tenure the business did not supply is a legal "
        "problem, not a style problem."},
    "secrets": {"severity": "blocker", "owner": "render", "why":
        "A credential in the output ships to every visitor."},
    "duplicate-content": {"severity": "blocker", "owner": "compose", "why":
        "Two sites Google collapses into one result are worth less than one site. "
        "Measured, not assumed — an identifier comparison proves nothing."},
    "coverage-envelope": {"severity": "warning", "owner": "graph", "why":
        "Sites here are built for one to three states and up to roughly 4,000 cities. "
        "Past that the build slows sharply, the sitemap approaches its 50,000-URL cap, "
        "and one site starts competing with the next one you build in the same states. "
        "It is a warning, not a wall — but it should be a decision, not an accident."},
    "thin-pages": {"severity": "warning", "owner": "library", "why":
        "A page below the word floor competes badly and reads as machine-made."},
    "layout-overflow": {"severity": "off", "owner": "templates", "why":
        "Horizontal overflow at narrow widths. Requires a headless browser; reported "
        "as a blind spot when one is unavailable rather than silently skipped."},
}


def load_rules(root: Path) -> dict[str, dict[str, str]]:
    path = root / "data" / "qa-rules.yaml"
    rules = {k: dict(v) for k, v in DEFAULT_RULES.items()}
    if path.is_file():
        for key, override in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).items():
            rules.setdefault(key, {}).update(override or {})
    return rules


def check_site(root: Path, graph, result, *, similarity_block: float = 0.25,
               similarity_warn: float = 0.15) -> Report:
    rules = load_rules(root)
    rep = Report(site_id=result.site_id)

    def add(rule: str, message: str, evidence: list[str] | None = None) -> None:
        spec = rules.get(rule, {"severity": "warning", "why": "", "owner": ""})
        if spec.get("severity") == "off":
            rep.not_verified.append(f"{rule}: switched off in data/qa-rules.yaml")
            return
        rep.findings.append(Finding(
            rule=rule, severity=spec.get("severity", "warning"), message=message,
            why=spec.get("why", ""), evidence=(evidence or [])[:8],
            owner=spec.get("owner", "")))

    text_pages = [p for p in result.pages if p.kind != "machine"]

    # Tags become a BOUNDARY MARKER, not a space.
    #
    # Trap: a table cell ending "...stays usable throughout" sits next to a cell
    # beginning "Usually, with minor accommodation". Strip the tags to spaces and
    # those two independent fragments become the sentence "throughout Usually",
    # which the geography scanner reads as the preposition "throughout" followed
    # by a capitalised place called "Usually" — and blocks the build over a town
    # that does not exist. Every rule here that reads a window of words across
    # text needs to know where one fragment ends and the next begins, and the
    # only place that knowledge exists is the markup.
    def _flatten(html: str) -> str:
        return re.sub(r"<[^>]+>", " ¶ ", html)

    plain = " ".join(_flatten(p.html) for p in text_pages)

    # 1 — structural ------------------------------------------------------
    leaked: list[str] = []
    for page in text_pages:
        body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page.html, flags=re.S | re.I)
        for tok in set(re.findall(r"\{[a-z_]{2,}\}", re.sub(r"<[^>]+>", " ", body))):
            leaked.append(f"{page.url} -> {tok}")
    if leaked:
        add("unresolved-tokens", f"{len(leaked)} visible token(s) on rendered pages", leaked)

    if result.placeholders:
        add("images-exist",
            f"{len(result.placeholders)} referenced asset(s) had no file and were "
            f"replaced with a labelled placeholder", result.placeholders)
    for warn in result.warnings:
        add("asset-invalid", warn)

    # 2 — semantic --------------------------------------------------------
    foreign = graph.site.get("known_foreign_identity") or []
    hits = [f for f in foreign if f and f.lower() in plain.lower()]
    if hits:
        add("foreign-identity", "another business's identity appears in the output", hits)

    covered = {l.city.lower() for l in graph.locations}
    covered |= {c["name"].lower() for c in graph.counties}
    covered |= {l.state.lower() for l in graph.locations}
    declared = graph.site.get("extra_allowed_places") or []
    covered |= {d.lower() for d in declared}
    hq = (graph.business.get("address") or {}).get("city")
    if hq:
        covered.add(hq.lower())
    # Up to four capitalised words, and a candidate counts as covered if it is a
    # prefix of a covered name or a covered name is a prefix of it. Without that,
    # "Port Saint Lucie" got clipped to "Port Saint" and reported as foreign
    # geography on a site that covers it — a blocker firing on its own coverage
    # list is worse than no rule at all, because it teaches you to ignore it.
    # Whitespace is flattened first. Without it a match ran across a line break
    # and captured "Gay \n After" — Gay IS a covered Georgia city, and the rule
    # reported it as foreign geography because the next sentence began with a
    # capital.
    flat = re.sub(r"\s+", " ", plain)
    candidates = []
    for fragment in flat.split("¶"):
        candidates += re.findall(
            r"\b(?:serving|in|near|around|across|throughout)\s+"
            r"([A-Z][a-zA-Z.'-]+(?:\s[A-Z][a-zA-Z.'-]+){0,3})", fragment)

    def _words(name: str) -> list[str]:
        """Split into words with punctuation stripped, stopping at a sentence end.

        "Lee County. Pick" captured across a full stop because the character
        class allowed a period inside a word. Trimming at the first
        period-terminated word turns it back into "Lee County", which is on the
        coverage list.
        """
        out: list[str] = []
        for raw in name.split():
            word = raw.strip(".,;:!?()").lower()
            if word:
                out.append(word)
            if raw.rstrip(")").endswith((".", ",", ";", ":")) and len(word) > 2:
                break
        return out

    # The candidate words have their punctuation stripped, so the covered set
    # must be compared on the same terms — otherwise "St. Charles County", which
    # is how the Census writes it and how canonical_county stores it, never
    # matches the "st charles county" the scanner produces.
    covered_norm = {re.sub(r"[.\u2019\']", "", c) for c in covered}

    def is_covered(name: str) -> bool:
        """Any PREFIX of the candidate being covered is enough.

        The regex is greedy by necessity — it cannot know where a place name
        ends and the next sentence begins. So "Gay After" passes because "Gay"
        is covered, and "Springfield Illinois" passes on a Springfield site.
        A gate that reports your own coverage list teaches you to ignore it.
        """
        words = _words(name)
        for n in range(len(words), 0, -1):
            prefix = re.sub(r"[.\u2019\']", "", " ".join(words[:n]))
            if prefix in covered_norm:
                return True
            if len(prefix) > 3 and any(c.startswith(prefix) for c in covered_norm):
                return True
        return False

    stray = sorted({m for m in candidates if len(m) > 3 and not is_covered(m)})[:10]
    if stray:
        add("out-of-area", "place names in the copy that are not in the coverage list", stray)

    facts = graph.facts
    bad_claims = []
    for pattern, fact, label in CLAIM_PATTERNS:
        if _fact_supplied(facts, fact):
            continue
        found = re.findall(pattern, plain, flags=re.I)
        if found:
            bad_claims.append(f"{label}: {found[0]!r} (business.facts.{fact} not supplied)")
    if bad_claims:
        add("fabricated-claims", "claims the business record does not support", bad_claims)

    secrets = [label for pat, label in SECRET_PATTERNS if re.search(pat, plain)]
    if secrets:
        add("secrets", "credential-shaped strings in the output", secrets)

    # 3 — uniqueness ------------------------------------------------------
    sig = signature(result.copy_text)
    other, score = nearest_shipped(root, result.site_id, graph.site["niche"], sig)
    csig = signature(result.blocks_text)
    _, cscore = nearest_shipped(root, result.site_id, graph.site["niche"], csig, key="csig")
    rep.stats["similarity"] = {"nearest": other, "score": round(score, 4),
                               "copy_only": round(cscore, 4)}
    if other and score >= similarity_block:
        add("duplicate-content",
            f"copy is {score:.0%} similar to {other} — above the {similarity_block:.0%} "
            f"block threshold", [f"colliding slot kinds: "
                                 f"{', '.join(sorted(result.composition))}"])
    elif other and score >= similarity_warn:
        rep.findings.append(Finding(
            rule="duplicate-content", severity="warning",
            message=f"copy is {score:.0%} similar to {other}",
            why=rules["duplicate-content"]["why"], owner="compose"))

    states = {l.state_abbr for l in graph.locations}
    if len(states) > 3 or len(graph.locations) > 4000:
        add("coverage-envelope",
            f"{len(graph.locations):,} cities across {len(states)} state(s) — outside the "
            f"1-3 state, ~4,000 city envelope these sites are built for",
            [f"states: {', '.join(sorted(states))}"] if len(states) <= 12 else
            [f"{len(states)} states"])

    words = len(_WORD.findall(plain)) / max(1, len(text_pages))
    rep.stats["avg_words_per_page"] = round(words)
    if words < 250:
        add("thin-pages", f"average {words:.0f} words per page is below the 250 floor")

    # blind spots, always declared
    rep.not_verified.extend([
        "rendered layout at narrow widths (needs a headless browser — run `foundry sweep`)",
        "third-party image licensing",
        f"{result.edge_pages} edge-rendered page(s) were not fetched — their master "
        f"template was checked instead",
    ])
    rep.stats.update({
        "pages_prerendered": len(result.pages),
        "pages_edge": result.edge_pages,
        "assets": result.assets_copied,
    })
    return rep


# --------------------------------------------------------------------------
# diagnose
# --------------------------------------------------------------------------

RULES: list[dict[str, Any]] = [
    {"id": "empty-coverage", "when": lambda m, c: c.get("empty_coverage"),
     "title": "The coverage list produced no buildable locations",
     "cause": "The niche folder exists but every row was skipped — usually military "
              "regions, malformed ZIPs, or a state filter that matches nothing.",
     "fix": "Check data/coverage/<niche>/ and the site's coverage.states filter.",
     "auto": False},
    {"id": "missing-coverage", "when": lambda m, c: c.get("missing_coverage"),
     "title": "No coverage list for this niche",
     "cause": "A lead-gen site cannot be published without the buyer's payable-ZIP "
              "footprint; without it the county grouping would be invented.",
     "fix": "foundry coverage import <niche> <file>",
     "auto": False},
    {"id": "incomplete-row", "when": lambda m, c: c.get("incomplete_row"),
     "title": "A required fact about the business is missing",
     "cause": "An incomplete record is refused at the door rather than accepted and "
              "filled with a default. Design defaults are fine; facts are not.",
     "fix": "Supply the named field in data/businesses/<slug>.yaml.",
     "auto": False},
    {"id": "outside-coverage", "when": lambda m, c: c.get("outside_coverage"),
     "title": "A place outside the payable list would have been published",
     "cause": "The buyer's rule line makes this a compliance failure, not a warning.",
     "fix": "Remove the place, or add it to the coverage list if it really is payable.",
     "auto": True},
    {"id": "no-library", "when": lambda m, c: c.get("no_library"),
     "title": "The niche has coverage loaded but no block library",
     "cause": "Coverage says WHERE you may publish; the library is WHAT the pages say. "
              "Borrowing another niche's library would put roofing sentences on a "
              "plumbing site, so the build refuses rather than substituting.",
     "fix": "Create data/library/<niche>.yaml, or generate one: "
            "`python3 foundry.py fill <niche> <kind> 40` for each block kind.",
     "auto": False},
    {"id": "empty-pool", "when": lambda m, c: "block pool" in m,
     "title": "A block pool is empty after fact filtering",
     "cause": "Every block of that kind asserted a fact the business record does not "
              "supply, so all of them were removed before selection.",
     "fix": "Add fact-free blocks for that kind, or supply the fact in business.facts.",
     "auto": True},
    {"id": "subpath-assets", "when": lambda m, c: c.get("subpath_assets"),
     "title": "The site was served from a subfolder, so every asset 404'd",
     "cause": "Pages ask for /assets/... because in production each site is the root "
              "of its own domain. Serving the parent dist/ and browsing to "
              "/<domain>/ looks for dist/assets/... which does not exist, so the "
              "stylesheet and every image 404 and the page renders unstyled.",
     "fix": "Serve each site as its own document root — `foundry serve` does this "
            "now, one port per site. Never serve dist/ directly.",
     "auto": True},
    {"id": "unknown-skeleton", "when": lambda m, c: c.get("unknown_skeleton"),
     "title": "The site names a page skeleton that does not exist",
     "cause": "Skeletons are declared in data/skeletons.yaml. A skeleton decides which "
              "sections exist and in what order, so an unknown one cannot be defaulted "
              "without silently changing what the pages contain.",
     "fix": "Use one of the listed keys, or add the skeleton to data/skeletons.yaml.",
     "auto": False},
    {"id": "unknown-style", "when": lambda m, c: c.get("unknown_style"),
     "title": "The site names a style pack that does not exist",
     "cause": "Style packs are declared in data/styles.yaml and nowhere else. A style "
              "sets the section order and the layout variants, so an unknown one cannot "
              "be silently defaulted.",
     "fix": "Use one of the listed keys, or add the pack to data/styles.yaml.",
     "auto": False},
    {"id": "unknown-theme", "when": lambda m, c: c.get("unknown_theme"),
     "title": "The site names a theme that does not exist",
     "cause": "Themes are declared in data/themes.yaml and nowhere else.",
     "fix": "Use one of the listed keys, or add the theme.", "auto": False},
    # ---- deploy: what the tools actually say when they fail --------------
    # These read command output, not our own ctx. Every one is a message a real
    # deploy produced; the point is that the panel explains it once rather than
    # you searching for it each time.
    {"id": "git-auth", "when": lambda m, c: "authentication failed" in m.lower()
        or "support for password authentication was removed" in m.lower()
        or "could not read username" in m.lower(),
     "title": "GitHub refused the credentials",
     "cause": "GitHub stopped accepting account passwords over https in 2021. A "
              "push needs a personal access token with repo scope, or an ssh "
              "remote. Terminal prompts are disabled here on purpose — nothing is "
              "watching a prompt inside a background job.",
     "fix": "Deploy → Credentials → GitHub personal access token, or change the "
            "repo URL to git@github.com:you/repo.git and use your ssh key.",
     "auto": False},
    {"id": "git-rejected", "when": lambda m, c: "rejected" in m.lower()
        and ("non-fast-forward" in m.lower() or "fetch first" in m.lower()),
     "title": "The remote branch has commits this deploy does not",
     "cause": "Something else pushed to that branch. Foundry will not silently "
              "overwrite it, because the thing it would overwrite might be the "
              "only copy.",
     "fix": "If the remote is only ever written by Foundry, tick Force push. "
            "Otherwise reconcile the branch by hand first.", "auto": False},
    {"id": "ssh-denied", "when": lambda m, c: "permission denied (publickey" in m.lower()
        or "host key verification failed" in m.lower(),
     "title": "The server refused the SSH key",
     "cause": "rsync runs ssh in batch mode, so it fails immediately instead of "
              "falling back to a password prompt nobody would answer.",
     "fix": "Add the public key to ~/.ssh/authorized_keys on the server, or point "
            "the SSH key field at the right private key.", "auto": False},
    {"id": "cf-auth", "when": lambda m, c: "authentication error" in m.lower()
        or "code: 10000" in m.lower() or "invalid api token" in m.lower(),
     "title": "Cloudflare refused the API token",
     "cause": "The token is wrong, expired, or lacks the permission this deploy "
              "needs — a Worker deploy needs Workers Scripts: Edit, Pages needs "
              "Cloudflare Pages: Edit.",
     "fix": "Reissue the token with the right permission and save it again.",
     "auto": False},
    {"id": "network", "when": lambda m, c: "could not resolve host" in m.lower()
        or "connection refused" in m.lower() or "network is unreachable" in m.lower()
        or "fetch request failed" in m.lower(),
     "title": "The deploy could not reach the network",
     "cause": "Nothing was uploaded. This is the safe failure: the site on disk is "
              "untouched and the live site is whatever it was before.",
     "fix": "Check the connection and run it again. Deploy is idempotent — "
            "repeating it costs a diff, not a rebuild.", "auto": False},
    {"id": "duplicate-url", "when": lambda m, c: c.get("duplicate_url"),
     "title": "The build produced the same URL more than once",
     "cause": "Two pages wanted the same path, so one would have overwritten the "
              "other on disk while the sitemap advertised it twice. The usual "
              "source is a service listed twice in the site record — three niche "
              "pairs share a service slug, and a browser restoring form state on "
              "a Back navigation can post the hidden twin. A city spelled two ways "
              "in the buyer's feed used to do it too.",
     "fix": "Open the site record and remove the repeated entry under services. "
            "Re-saving the site through the panel also cleans it, because the "
            "panel now filters services to the chosen niche and de-duplicates.",
     "auto": True},
    {"id": "not-built", "when": lambda m, c: c.get("not_built"),
     "title": "There is nothing in dist/ for this site",
     "cause": "dist/ is disposable by design — nothing is cached and nothing is "
              "patched in place — so a site that has not been built since the last "
              "clean has no output to send anywhere.",
     "fix": "Build it, then deploy.", "auto": False},
    {"id": "not-shipped", "when": lambda m, c: c.get("not_shipped"),
     "title": "The site built, but it is not deployable",
     "cause": "Deploy requires BOTH gates — QA and SEO — to have passed in the same "
              "build. The gates exist to stop a failure that only becomes expensive "
              "after publication: pages a search engine has already crawled cannot "
              "be un-crawled by fixing the generator afterwards.",
     "fix": "Open the build report, clear the findings, rebuild. Preview works "
            "throughout; only deploy is withheld.",
     "auto": False},
    {"id": "stale-build", "when": lambda m, c: c.get("stale_build"),
     "title": "The site was edited after the last build",
     "cause": "dist/ holds the previous version. Deploying it would publish the old "
              "site and silently discard the edit — the worst kind of failure, "
              "because everything reports success.",
     "fix": "Build this site again, then deploy.", "auto": False},
    {"id": "edge-on-static-host", "when": lambda m, c: c.get("edge_on_static_host"),
     "title": "This render mode cannot be served by that host",
     "cause": "The build put the long tail behind a Worker, so only the prerendered "
              "pages are real files. A static host has nothing to run, so every "
              "other city page would 404 — after the sitemap has already "
              "advertised it.",
     "fix": "Deploy to Cloudflare, or set render.prerender_top_n to null and "
            "rebuild so every page is a file.", "auto": False},
    {"id": "dangerous-path", "when": lambda m, c: c.get("dangerous_path"),
     "title": "The rsync destination is a system directory",
     "cause": "The server target syncs with --delete, which removes everything at "
              "the destination that is not part of this build. Pointed at / or "
              "/etc that is not a deploy, it is a wipe.",
     "fix": "Use a directory owned by the site, e.g. /var/www/<domain>.",
     "auto": True},
    {"id": "missing-tool", "when": lambda m, c: c.get("missing_tool"),
     "title": "A deploy target needs a program that is not installed",
     "cause": "git, rsync and Node are the deploy targets' only dependencies, and "
              "Foundry does not bundle them.",
     "fix": "Install the named tool, or use a target whose tool you already have.",
     "auto": False},
    {"id": "missing-secret", "when": lambda m, c: c.get("missing_secret"),
     "title": "A credential this target needs is not saved",
     "cause": "Credentials are global, kept in data/secrets.yaml at 0600 and never "
              "rendered back into the page — the form shows only whether a key is "
              "set. Nothing else in the deploy path can supply them.",
     "fix": "Deploy → Credentials, or export the matching environment variable.",
     "auto": False},
    {"id": "target-unconfigured", "when": lambda m, c: c.get("target_unconfigured"),
     "title": "That deploy target has no settings for this site",
     "cause": "Deploy settings are per site, because two sites almost never share a "
              "repo or a server path.",
     "fix": "Open the site's Deploy tab and fill the target in. Dry run first — it "
            "prints the exact commands without running them.", "auto": False},
    {"id": "stale-output", "when": lambda m, c: c.get("stale_output")
        or "directory not empty" in m.lower() or "errno 66" in m.lower()
        or "errno 39" in m.lower(),
     "title": "The previous build's output could not be deleted",
     "cause": "Something wrote into dist/ while the old copy was being removed — on a "
              "Mac that is normally Finder recreating .DS_Store in a folder you have "
              "open, or iCloud Drive rematerialising a file it had evicted. The "
              "directory skeleton hits it first because /areas/ is thousands of "
              "folders, so the delete takes long enough to be interrupted.",
     "fix": "Close any Finder window showing dist/, and keep the foundry folder "
            "outside iCloud Drive (Desktop and Documents are synced by default). "
            "The build now renames the old output aside instead of walking it, so "
            "this should not recur; if it does, delete dist/ by hand once.",
     "auto": True},
    {"id": "strict-undefined", "when": lambda m, c: "undefined" in m.lower(),
     "title": "A template referenced something the renderer never passed",
     "cause": "Jinja runs with StrictUndefined on purpose: a typo is a loud build "
              "error rather than a silently blank section.",
     "fix": "Use .get() for genuinely optional keys, or pass the value from render.py.",
     "auto": False},
]


def diagnose(message: str, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Explain the CAUSE, not the place the build stopped.

    Engine B built this because every failure it produced named where it halted
    and needed a human to trace back — "the template does not declare its
    identity" when the real cause was an empty workspace.

    `auto_handled: True` means the pipeline already guards against this class.
    Seeing one means the guard missed a case: investigate, do not re-fix.
    """
    ctx = ctx or {}
    for rule in RULES:
        try:
            if rule["when"](message, ctx):
                return {"id": rule["id"], "title": rule["title"], "cause": rule["cause"],
                        "fix": rule["fix"], "auto_handled": rule["auto"]}
        except Exception:
            continue
    return {"id": "unclassified", "title": "Unclassified failure", "cause": message,
            "fix": "Add a rule to RULES in core/verify.py so the software keeps this "
                   "knowledge instead of the conversation.", "auto_handled": False}
