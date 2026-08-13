"""
Foundry — the block library and the composition engine.

This module is the reason the product exists.

Engine B rewrote whole documents with a model, once per site: 17 parallel calls,
409 seconds, $4.56 — and the next site paid it all again. Engine A composed
pre-written blocks, free per site, but a human had to write them and it never
knew whether two compositions were actually different.

Foundry keeps Engine A's composition and lets Engine B's writer fill the pool:

    cost = O(blocks) with an LLM   instead of   O(sites) with an LLM
                                   or           O(blocks) with a human

Three properties matter and all three are load-bearing:

  DETERMINISTIC   same seed + same data -> byte-identical output, always.
                  splitmix64 is used instead of `random` so the result does not
                  drift between Python versions.

  INDEPENDENT     every slot draws from its own stream, so two seeds differ in
                  EVERY slot rather than in one. This is what makes the copy
                  diverge instead of merely rotating.

  HONEST          a block declares the facts it asserts. If the business record
                  does not supply that fact, the block is removed from the pool
                  BEFORE selection. Fabrication is therefore structurally
                  impossible for library copy, not merely detected afterwards.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .tokens import ALIASES as _ALIASES
from .tokens import _TOKEN as _alias_token

MASK = (1 << 64) - 1
GOLDEN = 0x9E3779B97F4A7C15

# Every block kind the templates can ask for. A kind not listed here is a typo,
# and a typo should be a loud error rather than a silently empty section.
KINDS: dict[str, str] = {
    "taglines":             "text",
    "hero_intros":          "text",
    "hero_ctas":            "text",
    "trust_points":         "titled",
    "about_paras":          "paras",
    "why_us":               "titled",
    "process_steps":        "titled",
    "service_intros":       "paras",
    "service_heroes":       "text",
    "service_bullets":      "text",
    "faqs":                 "qa",
    "reviews":              "review",
    "location_intros":      "paras",
    "location_heroes":      "text",
    "location_service_intros": "paras",
    "cta_blocks":           "cta",
    "closing_paras":        "paras",
    "signs":                "titled",
    "compare_rows":         "compare",
    "cost_factors":         "titled",
}

# The kinds that describe ONE service and so may be tagged to a specific service
# with a `for:` field. A block without `for` is GENERIC — eligible on every page
# (any service, any city, the home page). A tagged block is eligible ONLY on its
# own service's pages. This is what stops a roof-replacement bullet from landing
# on the chimney-repair page, while a shallow pool still falls back to generic
# copy so a page is never empty. The other 14 kinds ignore `for` entirely.
SERVICE_SCOPED_KINDS: frozenset[str] = frozenset({
    "service_intros", "service_heroes", "service_bullets",
    "signs", "compare_rows", "cost_factors",
})

# Sentinel meaning "generic blocks only" — passed by the home/location composers
# so a service-tagged block can never leak onto a page that is not that service.
# A real service slug is kebab-case and can never equal this.
GENERIC = "\x00generic\x00"


def _tag(block: Any) -> str | None:
    """The service a block is bound to, or None if it is generic."""
    return block.get("for") if isinstance(block, dict) else None


def _emit(kind: str, block: Any) -> Any:
    """Normalise a stored block into what the template consumes: strip the `for`
    tag, and unwrap a `{text, for}` wrapper back to a bare string for text kinds."""
    if isinstance(block, dict):
        if KINDS.get(kind) == "text":
            return block.get("text", "")
        if "for" in block:
            return {k: v for k, v in block.items() if k != "for"}
    return block


def _emit_key(value: Any) -> str:
    """A stable string key for an emitted block — used to avoid the same sentence
    appearing twice when a tagged draw is topped up from the generic pool."""
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, dict):
        return "|".join(f"{k}={value[k]}" for k in sorted(value))
    if isinstance(value, list):
        return "|".join(_emit_key(v) for v in value)
    return str(value)


# --------------------------------------------------------------------------
# deterministic randomness (portable — not `random`)
# --------------------------------------------------------------------------

class Stream:
    """splitmix64. Exact, portable, and stable across Python versions."""

    __slots__ = ("_s",)

    def __init__(self, seed: int):
        self._s = (seed or GOLDEN) & MASK

    def next(self) -> int:
        self._s = (self._s + GOLDEN) & MASK
        z = self._s
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK
        return (z ^ (z >> 31)) & MASK

    def below(self, n: int) -> int:
        return self.next() % n if n > 0 else 0

    def shuffled(self, items: list) -> list:
        out = list(items)
        for i in range(len(out) - 1, 0, -1):
            j = self.below(i + 1)
            out[i], out[j] = out[j], out[i]
        return out


def _hash64(text: str) -> int:
    return int.from_bytes(hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest(), "big")


def fingerprint(block: Any) -> str:
    """Normalised hash of a block's meaningful text — used to drop duplicates."""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k in sorted(node):
                if k not in ("requires", "id", "source", "model", "created"):
                    walk(node[k])
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif node is not None:
            parts.append(re.sub(r"\s+", " ", str(node)).strip().lower())

    walk(block)
    return hashlib.blake2b("|".join(parts).encode("utf-8"), digest_size=16).hexdigest()


# --------------------------------------------------------------------------
# the library
# --------------------------------------------------------------------------

@dataclass
class Library:
    niche: str
    blocks: dict[str, list[Any]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    filtered: dict[str, int] = field(default_factory=dict)

    def pool(self, kind: str) -> list[Any]:
        if kind not in KINDS:
            raise KeyError(
                f"unknown block kind {kind!r}; known kinds: {', '.join(sorted(KINDS))}"
            )
        return self.blocks.get(kind, [])


def _merge(into: dict[str, list], other: dict[str, Any]) -> None:
    for kind, items in (other or {}).items():
        if not isinstance(items, list):
            continue
        into.setdefault(kind, []).extend(items)


def _fact_is_true(facts: dict, name: str) -> bool:
    value = facts.get(name)
    if value is None or value is False:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, dict)) and not value:
        return False
    return True


_CACHE: dict[tuple, tuple[float, Library]] = {}


def _stamp(*paths: Path) -> float:
    return sum(p.stat().st_mtime for p in paths if p.is_file())


def load_library(root: Path, niche: str, facts: dict | None = None) -> Library:
    """
    Merge the shipped library with the operator's own additions, then remove
    every block whose asserted facts the business does not supply.

    The shipped file is NEVER written to (Engine A's rule) so upgrading it
    cannot destroy the operator's work.
    """
    facts = facts or {}
    base = root / "data" / "library" / "_base.yaml"
    shipped = root / "data" / "library" / f"{niche}.yaml"
    user = root / "data" / "library" / "user" / f"{niche}.yaml"

    # Keyed on the facts too, because fact filtering changes the pool. Invalidated
    # on the mtime of either file, so editing YAML in a text editor is picked up
    # without restarting anything. Seed search calls this a few hundred times in
    # a row; re-parsing the YAML each time was 90% of its runtime.
    key = (str(root), niche, tuple(sorted((k, str(v)) for k, v in facts.items())))
    stamp = _stamp(base, shipped, user)
    hit = _CACHE.get(key)
    if hit and hit[0] == stamp:
        return hit[1]

    merged: dict[str, list] = {}
    # _base.yaml is trade-agnostic: every sentence in it is written with
    # {niche_lower} / {service_lower} / {city} tokens so it is true of any trade
    # the factory covers. It is what lets 24 niches build from one body of work
    # instead of 24 hand-written libraries.
    #
    # The trade-off is stated plainly: two sites in DIFFERENT niches drawing the
    # same base block share that sentence. The similarity gate compares within a
    # niche, so it will not catch that. Niche files and `foundry fill` are how
    # you move a niche off the shared base.
    if base.is_file():
        _merge(merged, yaml.safe_load(base.read_text(encoding="utf-8")) or {})

    if shipped.is_file():
        _merge(merged, yaml.safe_load(shipped.read_text(encoding="utf-8")) or {})

    if user.is_file():
        _merge(merged, yaml.safe_load(user.read_text(encoding="utf-8")) or {})

    lib = Library(niche=niche)
    for kind, items in merged.items():
        if kind not in KINDS:
            continue
        keep, seen, dropped = [], set(), 0
        for item in items:
            fp = fingerprint(item)
            if fp in seen:
                continue
            seen.add(fp)
            needs = (item.get("requires") or []) if isinstance(item, dict) else []
            if any(not _fact_is_true(facts, n) for n in needs):
                dropped += 1          # the business cannot support this claim
                continue
            keep.append(item)
        lib.blocks[kind] = keep
        lib.counts[kind] = len(keep)
        if dropped:
            lib.filtered[kind] = dropped
    _CACHE[key] = (stamp, lib)
    return lib


def source_counts(root: Path, niche: str) -> dict[str, dict[str, int]]:
    """Per-kind counts split by WHERE the blocks live: the shared _base file,
    the niche-specific file, and the operator's own imports.

    This exists because "80 in the dashboard but 30 in the base file" reads like
    a bug when it is not — the 80 is base + niche + yours, the 30 is the base
    file alone. Showing the split removes the confusion at the source.
    """
    def load(p: Path) -> dict:
        return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.is_file() else {}
    base = load(root / "data" / "library" / "_base.yaml")
    niche_f = load(root / "data" / "library" / f"{niche}.yaml")
    user = load(root / "data" / "library" / "user" / f"{niche}.yaml")
    out: dict[str, dict[str, int]] = {}
    for kind in KINDS:
        out[kind] = {
            "base": len(base.get(kind) or []),
            "niche": len(niche_f.get(kind) or []),
            "user": len(user.get(kind) or []),
        }
        out[kind]["raw_total"] = sum(out[kind].values())
    return out


def add_many(root: Path, niche: str, kind: str, items: list[Any]) -> dict[str, int]:
    """
    Append a batch to the operator's own library file.

    ONE write for the whole batch. Engine A measured the naive per-item version
    at 105 seconds for 1,000 blocks; batching brought it to 0.15 s.
    """
    if kind not in KINDS:
        raise KeyError(f"unknown block kind {kind!r}")
    folder = root / "data" / "library" / "user"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{niche}.yaml"

    current = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    current = current or {}
    existing = current.setdefault(kind, [])
    seen = {fingerprint(b) for b in existing}

    added = skipped = 0
    for item in items:
        fp = fingerprint(item)
        if fp in seen:
            skipped += 1
            continue
        seen.add(fp)
        existing.append(item)
        added += 1

    path.write_text(yaml.safe_dump(current, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _CACHE.clear()
    return {"added": added, "skipped_duplicate": skipped, "total": len(existing)}


# --------------------------------------------------------------------------
# composition
# --------------------------------------------------------------------------

class EmptyPool(RuntimeError):
    def __init__(self, kind: str, filtered: int):
        self.kind, self.filtered = kind, filtered
        extra = (f" ({filtered} block(s) were removed because the business record "
                 f"does not supply the facts they assert)") if filtered else ""
        super().__init__(f"block pool {kind!r} is empty{extra}")


@dataclass
class Composer:
    """
    Picks blocks deterministically from (composition_seed, slot key).

    `used` records every (kind, index) drawn, which is what lets the uniqueness
    check report WHICH slot kinds are colliding between two sites — and that
    report is exactly the input the LLM filler consumes.
    """

    library: Library
    seed: int
    used: dict[str, list[int]] = field(default_factory=dict)

    def _index(self, kind: str, slot: str, size: int, offset: int = 0) -> int:
        h = _hash64(f"{kind}::{slot}")
        return Stream(h ^ ((self.seed + 1) * GOLDEN) ^ (offset * 0x27220A95)).below(size)

    def _split(self, kind: str, service: str | None) -> tuple[list[Any], list[Any]]:
        """Return (primary, secondary) pools for a draw.

          service is None      -> (whole pool, [])          — unchanged legacy path
          service is GENERIC    -> (generic only, [])         — home/location pages
          service == <slug>     -> (tagged-for-slug, generic) — service pages, tagged
                                    first and generic as top-up; if nothing is tagged
                                    for the slug the generic pool becomes primary.

        When a kind's pool carries no `for` tags at all, every branch collapses to
        the whole pool, so composition stays byte-identical until tags are added.
        """
        pool = self.library.pool(kind)
        if service is None:
            return pool, []
        generic = [b for b in pool if _tag(b) is None]
        if service == GENERIC:
            return generic, []
        tagged = [b for b in pool if _tag(b) == service]
        if tagged:
            return tagged, generic
        return generic, []

    def one(self, kind: str, slot: str, service: str | None = None) -> Any:
        primary, secondary = self._split(kind, service)
        src = primary or secondary
        if not src:
            raise EmptyPool(kind, self.library.filtered.get(kind, 0))
        idx = self._index(kind, slot, len(src))
        self.used.setdefault(kind, []).append(idx)
        return _emit(kind, src[idx])

    def many(self, kind: str, slot: str, count: int,
             service: str | None = None) -> list[Any]:
        """Distinct picks — a site never draws the same block twice in one slot.

        For a service draw, tagged blocks are exhausted first and the generic pool
        tops up any shortfall, de-duplicated by emitted text so a sentence that
        exists both tagged and generic can never appear twice on one page."""
        primary, secondary = self._split(kind, service)
        if not primary and not secondary:
            raise EmptyPool(kind, self.library.filtered.get(kind, 0))

        order = Stream(_hash64(f"{kind}::{slot}") ^ ((self.seed + 1) * GOLDEN)) \
            .shuffled(list(range(len(primary))))
        picked = order[:max(0, min(count, len(primary)))]
        self.used.setdefault(kind, []).extend(picked)
        out = [_emit(kind, primary[i]) for i in picked]

        if len(out) < count and secondary:
            seen = {_emit_key(o) for o in out}
            order2 = Stream(_hash64(f"{kind}::{slot}\x00g") ^ ((self.seed + 1) * GOLDEN)) \
                .shuffled(list(range(len(secondary))))
            for i in order2:
                if len(out) >= count:
                    break
                emitted = _emit(kind, secondary[i])
                key = _emit_key(emitted)
                if key in seen:
                    continue
                seen.add(key)
                out.append(emitted)
                self.used.setdefault(kind, []).append(i)
        return out

    def coverage_note(self) -> dict[str, Any]:
        """How much of each pool this composition actually consumed."""
        out = {}
        for kind, idxs in sorted(self.used.items()):
            size = len(self.library.pool(kind))
            out[kind] = {"pool": size, "drawn": len(set(idxs)),
                         "headroom": max(0, size - len(set(idxs)))}
        return out


# --------------------------------------------------------------------------
# N-axis interpolation
# --------------------------------------------------------------------------

_TOKEN = re.compile(r"\{([a-z_]+)\}")
# A clause the template drops when its token resolves to nothing.
_OPTIONAL = re.compile(r"\[\[(.*?)\]\]", re.S)


def interpolate(value: Any, ctx: dict[str, Any]) -> Any:
    """
    Walk any structure and substitute {tokens} from the render context.

    Engine A proved this is what makes a library possible at all: without it a
    pre-written FAQ is welded to one product, one city and one service.

    `[[ ... ]]` marks an optional clause. If every token inside it is empty the
    whole clause disappears; otherwise the brackets are removed and the clause
    stays. Engine B shipped a live hero reading "Dumpster Rentals for{CITY},
    {STATE}" because it had no equivalent.
    """
    if isinstance(value, dict):
        return {k: interpolate(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v, ctx) for v in value]
    if not isinstance(value, str):
        return value

    # Rewrite alias tokens ({business} → {company}, {location} → {city}, …) to
    # their canonical form first, so a block that uses a natural variant still
    # resolves instead of printing the raw token.
    value = _alias_token.sub(
        lambda m: "{" + _ALIASES.get(m.group(1), m.group(1)) + "}", value)

    def optional(m: re.Match) -> str:
        inner = m.group(1)
        names = _TOKEN.findall(inner)
        if names and all(not str(ctx.get(n, "")).strip() for n in names):
            return ""
        return inner

    text = _OPTIONAL.sub(optional, value)
    text = _TOKEN.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), text)
    # tidy the seams an emptied clause leaves behind
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return text.strip()


def unresolved_tokens(text: str) -> list[str]:
    """Any {token} still present after interpolation is a visible defect."""
    return sorted(set(_TOKEN.findall(text)))
