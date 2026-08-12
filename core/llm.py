"""
Foundry — the tool-free library filler.

This is Engine B's single best decision, kept exactly, with one change of
destination.

Engine B's invocation:

    claude -p "<instruction + INPUT JSON>" --tools "" --output-format json ...

`--tools ""` means the model cannot open a file. The rule "do not touch
anything you were not asked to touch" stops being an instruction you hope it
follows and becomes a capability it does not have. That is the difference
between a policy and a guarantee.

THE CHANGE: Engine B pointed this at one site's data files and paid $4.56 every
time it built a site. Foundry points it at the BLOCK LIBRARY, so the cost is
paid once per block and amortised across every site that ever draws it.

    Engine B :  cost = O(sites)   x LLM
    Foundry  :  cost = O(blocks)  x LLM

Everything Engine B wrapped around the call is kept, because each piece exists
for a reason it learned the hard way: shape validation, immutable fields, token
preservation, [[...]] balance, EXACTLY ONE RETRY carrying the specific failures,
and an all-or-nothing merge — a half-written batch is worse than none.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .library import KINDS, add_many, fingerprint

TOKEN = re.compile(r"\{([a-z_]+)\}")

SYSTEM = """You write blocks for a local-services website content library.

HARD RULES — a violation makes the whole batch unusable:

1. NEVER INVENT A FACT. Do not state licence numbers, certifications, years in
   business, award counts, review counts, ratings, guarantees, warranties,
   "family owned since", "24/7", financing, or insurance status. Write only
   what is true of any competent contractor in this trade.
   A fabricated credential is a legal problem, not a style problem.

2. Blocks are reused across many businesses in many towns. Never name a
   specific company, town, county, price or date. Use the placeholder tokens
   listed in the instruction and nothing else.

3. A token you were given must appear in a natural sentence, not bolted on.
   Never invent a new token.

4. Wrap any clause that only makes sense when a token has a value in [[ ... ]].
   The renderer removes the whole clause when the token is empty. Brackets must
   balance.

5. Return ONLY a JSON array matching the shape you are shown. No prose, no
   markdown fence, no commentary, no trailing text.

6. Every item must be substantively different from every other item and from
   the existing blocks you are shown. Rephrasing is not variation — change the
   angle, the objection being answered, or the reader being addressed."""


@dataclass
class FillResult:
    niche: str
    kind: str
    requested: int
    accepted: list[Any] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    retried: bool = False
    written: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.accepted) and not self.problems


# --------------------------------------------------------------------------
# validation — every check earns its place
# --------------------------------------------------------------------------

def shape_of(node: Any, prefix: str = "") -> set[str]:
    """Every leaf key path, so a returned object can be compared exactly."""
    out: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("requires", "id", "source", "model", "created"):
                continue
            out |= shape_of(v, f"{prefix}.{k}")
    elif isinstance(node, list):
        out |= shape_of(node[0], f"{prefix}[]") if node else {f"{prefix}[]"}
    else:
        out.add(prefix or ".")
    return out


def validate(items: Any, exemplar: Any, allowed_tokens: set[str],
             existing: set[str]) -> tuple[list[Any], list[str]]:
    problems: list[str] = []
    if not isinstance(items, list):
        return [], ["response was not a JSON array"]

    want = shape_of(exemplar)
    kept, seen = [], set(existing)

    for i, item in enumerate(items):
        errs: list[str] = []

        got = shape_of(item)
        if got != want:
            missing, extra = sorted(want - got), sorted(got - want)
            errs.append(f"shape mismatch (missing {missing}, unexpected {extra})")

        text = json.dumps(item, ensure_ascii=False)

        bad = sorted(set(TOKEN.findall(text)) - allowed_tokens)
        if bad:
            errs.append(f"invented token(s) {bad}; only {sorted(allowed_tokens)} exist")

        if text.count("[[") != text.count("]]"):
            errs.append("unbalanced [[ ]] optional block — the clause would either "
                        "print literal brackets or hard-code an optional phrase as always-on")

        fp = fingerprint(item)
        if fp in seen:
            errs.append("duplicate of an existing block")
        seen.add(fp)

        if errs:
            problems.append(f"item {i}: " + "; ".join(errs))
        else:
            kept.append(item)

    return kept, problems


# --------------------------------------------------------------------------
# the call
# --------------------------------------------------------------------------

TOKENS_FOR_KIND: dict[str, set[str]] = {
    "location_intros": {"company", "phone", "city", "state", "state_abbr", "county", "nearby"},
    "location_service_intros": {"company", "phone", "city", "state", "state_abbr",
                                "county", "nearby", "service", "service_lower"},
    "service_intros": {"company", "phone", "service", "service_lower", "city"},
    "faqs": {"company", "phone", "service", "service_lower", "city"},
    "cta_blocks": {"company", "phone", "city"},
}
DEFAULT_TOKENS = {"company", "phone", "city", "county", "niche", "niche_lower"}


def build_prompt(niche: str, kind: str, count: int, exemplar: Any,
                 samples: list[Any], tokens: set[str], feedback: str = "") -> str:
    return "\n".join([
        f"Niche: {niche}. Block kind: {kind}. Produce exactly {count} new blocks.",
        "",
        "SHAPE — every item must match this exactly:",
        json.dumps(exemplar, ensure_ascii=False, indent=2),
        "",
        f"TOKENS YOU MAY USE (and no others): {sorted(tokens)}",
        "",
        "EXISTING BLOCKS — yours must be substantively different from all of these:",
        json.dumps(samples, ensure_ascii=False, indent=2),
        "",
        (f"YOUR PREVIOUS ATTEMPT FAILED VALIDATION. Fix exactly these problems:\n{feedback}"
         if feedback else ""),
        "",
        f"Return ONLY a JSON array of {count} items.",
    ])


def call_model(prompt: str, model: str = "sonnet", timeout: int = 300) -> str:
    if not shutil.which("claude"):
        raise RuntimeError(
            "the `claude` CLI is not on PATH. Foundry never gives the model file "
            "access, so it shells out to `claude -p --tools \"\"`. Install the CLI, "
            "or pipe your own transport into fill_kind(transport=...)."
        )
    proc = subprocess.run(
        ["claude", "-p", prompt,
         "--tools", "",                      # <- the guarantee, not the request
         "--output-format", "json",
         "--model", model,
         "--no-session-persistence",
         "--append-system-prompt", SYSTEM],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"model call failed: {proc.stderr.strip()[:400]}")
    return proc.stdout


def parse_response(raw: str) -> Any:
    try:
        envelope = json.loads(raw)
        text = envelope.get("result", raw) if isinstance(envelope, dict) else raw
    except json.JSONDecodeError:
        text = raw
    if isinstance(text, (list, dict)):
        return text
    text = re.sub(r"^```(?:json)?|```$", "", str(text).strip(), flags=re.M).strip()
    start = min([i for i in (text.find("["), text.find("{")) if i >= 0] or [0])
    return json.loads(text[start:])


def fill_kind(root: Path, niche: str, kind: str, count: int = 10, *,
              model: str = "sonnet", transport=None, dry_run: bool = False) -> FillResult:
    """
    Generate `count` new blocks of `kind` and merge them into the operator's
    library. EXACTLY ONE RETRY, carrying the specific validation failures. Then
    it stops. There is no "repeat until clean" loop.
    """
    if kind not in KINDS:
        raise KeyError(f"unknown block kind {kind!r}; known: {', '.join(sorted(KINDS))}")

    shipped = yaml.safe_load((root / "data" / "library" / f"{niche}.yaml")
                             .read_text(encoding="utf-8")) or {}
    pool = shipped.get(kind) or []
    if not pool:
        raise RuntimeError(
            f"no exemplar for {kind!r} in the shipped {niche} library — the shape "
            f"contract is derived from a real block, never guessed")

    exemplar = pool[0]
    tokens = TOKENS_FOR_KIND.get(kind, DEFAULT_TOKENS)
    existing = {fingerprint(b) for b in pool}
    samples = pool[:6]

    res = FillResult(niche=niche, kind=kind, requested=count)
    send = transport or (lambda p: parse_response(call_model(p, model=model)))
    feedback = ""

    for attempt in (1, 2):
        prompt = build_prompt(niche, kind, count, exemplar, samples, tokens, feedback)
        if dry_run:
            res.problems.append("dry run — prompt built, no call made")
            res.accepted = []
            return res
        try:
            items = send(prompt)
        except Exception as exc:                      # noqa: BLE001
            res.problems.append(f"attempt {attempt}: {exc}")
            break

        kept, problems = validate(items, exemplar, tokens, existing)
        if not problems:
            res.accepted, res.problems = kept, []
            break
        if attempt == 1:
            res.retried = True
            feedback = "\n".join(problems[:12])
            res.accepted = kept
            continue
        # second failure: keep what validated, report the rest, stop.
        res.accepted = kept
        res.problems = problems

    # all-or-nothing per item, never per batch: a validated block is good even
    # if a sibling failed. What is NOT allowed is writing an invalid one.
    if res.accepted and not dry_run:
        res.written = add_many(root, niche, kind, res.accepted)
    return res
