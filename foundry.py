#!/usr/bin/env python3
"""
Foundry — a lead-gen site factory.

  foundry build [site_id ...]     render, verify, and record the fingerprint
  foundry check [site_id ...]     render to a scratch dir and verify only
  foundry compare                 similarity matrix across every site
  foundry list                    sites, niches, library depth
  foundry serve [port]            browse dist/
  foundry coverage <niche> <file> import a buyer's payable-ZIP footprint
  foundry feed <file> [niche...]  import the wide multi-niche coverage feed
  foundry images <niche> [cmd]    add | fetch | list the image pool
  foundry seo [site_id ...]       technical SEO audit of the built output
  foundry css tailwind | check    compile the optional Tailwind stylesheet
  foundry fill <niche> <kind> [n] extend the block library with the tool-free LLM
  foundry sweep [site]            headless overflow sweep at six widths
  foundry deploy <site> <target>  github | cloudflare | server  (--dry-run first)

Nothing here is a wrapper around a model. Nine tenths of this tool is
deterministic; the model touches the library and nothing else.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.deploy import TARGETS, deploy as run_deploy  # noqa: E402
from core.graph import FoundryError, list_sites, load_graph, parse_coverage_text, save_coverage  # noqa: E402
from core.library import load_library  # noqa: E402
from core.render import build_site  # noqa: E402
from core.seo import audit as seo_audit  # noqa: E402
import yaml  # noqa: E402

from core.verify import (check_site, diagnose, load_shipped, record_shipped,  # noqa: E402
                         signature, similarity)

G, Y, R, B, DIM, OFF = "\033[32m", "\033[33m", "\033[31m", "\033[1m", "\033[2m", "\033[0m"
TICK, CROSS, WARN, DOT = f"{G}✓{OFF}", f"{R}✗{OFF}", f"{Y}!{OFF}", f"{DIM}·{OFF}"


def _report(rep, res) -> None:
    print(f"    {DIM}pages{OFF} {len(res.pages)} static + {res.edge_pages} edge"
          f"   {DIM}assets{OFF} {res.assets_copied}"
          f"   {DIM}words/page{OFF} {rep.stats.get('avg_words_per_page', 0)}")
    sim = rep.stats.get("similarity") or {}
    if sim.get("nearest"):
        score = sim["score"]
        tone = R if score >= 0.25 else (Y if score >= 0.15 else G)
        print(f"    {DIM}nearest{OFF} {sim['nearest']} at {tone}{score:.1%}{OFF}"
              f"   {DIM}(composed copy alone: {sim.get('copy_only', 0):.1%}){OFF}")
    else:
        print(f"    {DIM}nearest{OFF} — first site of this niche, nothing to compare against")

    for f in rep.findings:
        mark = CROSS if f.severity == "blocker" else WARN
        print(f"    {mark} {B}{f.rule}{OFF}  {f.message}")
        if f.why:
            print(f"        {DIM}why: {f.why.strip().splitlines()[0]}{OFF}")
        for ev in f.evidence[:4]:
            print(f"        {DIM}- {ev}{OFF}")
    if not rep.findings:
        print(f"    {TICK} no findings")
    for blind in rep.not_verified:
        print(f"    {DOT} not verified: {blind}")


def cmd_build(args: list[str], verify_only: bool = False) -> int:
    ids = args or list_sites(ROOT)
    if not ids:
        print("no sites in data/sites/")
        return 1

    out_root = Path(tempfile.mkdtemp(prefix="foundry-check-")) if verify_only else None
    failed = 0

    for site_id in ids:
        print(f"\n{B}{site_id}{OFF}")
        started = time.time()
        try:
            graph = load_graph(ROOT, site_id)
            lib = load_library(ROOT, graph.site["niche"], graph.facts)
            filtered = sum(lib.filtered.values())
            print(f"    {DIM}library{OFF} {sum(lib.counts.values())} blocks across "
                  f"{len(lib.counts)} kinds"
                  + (f"   {Y}{filtered} removed{OFF} {DIM}(facts not supplied by the "
                     f"business record){OFF}" if filtered else ""))
            print(f"    {DIM}coverage{OFF} {len(graph.locations)} cities, "
                  f"{len(graph.counties)} counties"
                  + (f"   {Y}{len(graph.coverage_report.skipped)} skipped{OFF}"
                     if graph.coverage_report.skipped else "")
                  + (f"   {Y}{graph.coverage_report.city_merges} spelling(s) merged{OFF}"
                     if graph.coverage_report.city_merges else ""))
            for note in graph.coverage_report.merged_names[:3]:
                print(f"      {DIM}{note}{OFF}")

            res = build_site(ROOT, site_id, out_root)
            rep = check_site(ROOT, graph, res)
            _report(rep, res)

            seo = seo_audit(ROOT, graph, res)
            tone = G if seo.score >= 90 else (Y if seo.score >= 70 else R)
            print(f"    {DIM}SEO{OFF} score {tone}{seo.score}/100{OFF}   "
                  f"{DIM}{seo.stats['unique_titles']} unique titles across "
                  f"{seo.stats['pages_audited']} audited pages · "
                  f"{seo.stats['sitemap_urls']:,} sitemap URLs · "
                  f"{seo.stats['avg_page_kb']} KB avg{OFF}")
            for f in seo.findings:
                mark = CROSS if f.severity == "blocker" else WARN
                print(f"    {mark} {B}seo:{f.rule}{OFF}  {f.message}")
                if f.why:
                    print(f"        {DIM}why: {f.why.strip().splitlines()[0]}{OFF}")
                for e in f.evidence[:3]:
                    print(f"        {DIM}- {e}{OFF}")
            if not seo.findings:
                print(f"    {TICK} no SEO findings")

            # "Shipped" needs BOTH gates. A site can build and preview while
            # failing either; it cannot be recorded as shipped.
            if rep.passed and seo.passed:
                if not verify_only:
                    record_shipped(ROOT, site_id, graph.site["niche"],
                                   signature(res.copy_text), signature(res.blocks_text))
                print(f"    {TICK} {B}pass{OFF}  {DIM}{time.time() - started:.1f}s"
                      f"  -> {res.out_dir}{OFF}")
            else:
                failed += 1
                n = len(rep.blockers) + len(seo.blockers)
                print(f"    {CROSS} {B}BLOCKED{OFF} by {n} finding(s) "
                      f"({len(rep.blockers)} QA, {len(seo.blockers)} SEO) "
                      f"{DIM}— not recorded as shipped{OFF}")
        except FoundryError as exc:
            failed += 1
            d = diagnose(str(exc), exc.ctx)
            print(f"    {CROSS} {exc}")
            print(f"      {B}why{OFF}   {d['title']}")
            print(f"      {DIM}cause {d['cause']}{OFF}")
            print(f"      {B}fix{OFF}   {d['fix']}")
            if d["auto_handled"]:
                print(f"      {Y}note  this class is already guarded — a guard missed a "
                      f"case. Investigate, do not re-fix.{OFF}")
        except Exception as exc:                       # noqa: BLE001
            failed += 1
            d = diagnose(str(exc), {})
            print(f"    {CROSS} {type(exc).__name__}: {exc}")
            print(f"      {B}why{OFF}   {d['title']}\n      {DIM}cause {d['cause']}{OFF}"
                  f"\n      {B}fix{OFF}   {d['fix']}")

    if out_root:
        shutil.rmtree(out_root, ignore_errors=True)
    print(f"\n{len(ids) - failed}/{len(ids)} passed")
    return 1 if failed else 0


def cmd_compare(_: list[str]) -> int:
    shipped = load_shipped(ROOT)
    if len(shipped) < 2:
        print("need at least two recorded sites — run `foundry build` first")
        return 1
    ids = sorted(shipped)
    width = max(len(i) for i in ids) + 2
    print(f"\n{B}Measured copy similarity{OFF} {DIM}(MinHash over 5-word shingles){OFF}\n")
    print(" " * width + "".join(f"{i[:10]:>12}" for i in ids))
    for a in ids:
        row = f"{a:<{width}}"
        for b in ids:
            if a == b:
                row += f"{DIM}{'—':>12}{OFF}"
                continue
            s = similarity(shipped[a]["sig"], shipped[b]["sig"])
            tone = R if s >= 0.25 else (Y if s >= 0.15 else G)
            row += f"{tone}{s:>11.1%}{OFF} "
        print(row)
    print(f"\n{DIM}block >= 25%   warn >= 15%   "
          f"(Engine B measured 60-91% between untreated clones, 3-6% after a real "
          f"rewrite){OFF}")
    return 0


def cmd_list(_: list[str]) -> int:
    print(f"\n{B}Sites{OFF}")
    for site_id in list_sites(ROOT):
        try:
            g = load_graph(ROOT, site_id)
            print(f"  {site_id:<26} {g.site['domain']:<26} seed={g.site.get('composition_seed', 0):<4}"
                  f" {len(g.locations)} cities  theme={g.site.get('theme')}")
        except FoundryError as exc:
            print(f"  {site_id:<26} {R}{exc}{OFF}")

    print(f"\n{B}Libraries{OFF}")
    for path in sorted((ROOT / "data" / "library").glob("*.yaml")):
        lib = load_library(ROOT, path.stem)
        total = sum(lib.counts.values())
        print(f"  {path.stem:<14} {total} blocks")
        for kind, n in sorted(lib.counts.items()):
            bar = "█" * min(28, n)
            print(f"    {kind:<26}{n:>4}  {DIM}{bar}{OFF}")
    return 0


def cmd_serve(args: list[str]) -> int:
    from core.preview import serve
    dist = ROOT / "dist"
    if not dist.is_dir():
        print("nothing built yet — run `foundry build`")
        return 1
    try:
        serve(dist, int(args[0]) if args else 8000)
    except RuntimeError as exc:
        print(exc)
        return 1
    return 0


def cmd_coverage(args: list[str]) -> int:
    if len(args) < 2:
        print("usage: foundry coverage <niche> <file>")
        return 1
    niche, path = args[0], Path(args[1])
    rows, rep = parse_coverage_text(path.read_text(encoding="utf-8"))
    written = save_coverage(ROOT, niche, rows)
    print(f"{TICK} {rep.rows_kept} rows kept from {rep.rows_in}")
    print(f"  {DIM}{rep.duplicate_zips} duplicate ZIP(s) dropped{OFF}")
    for note in rep.skipped:
        print(f"  {WARN} skipped: {note}")
    print(f"  {DIM}written: {', '.join(f'{k}={v}' for k, v in written.items())}{OFF}")
    print(f"  {DIM}note: the niche was REPLACED, not merged — a buyer publishes a "
          f"complete footprint{OFF}")
    return 0


def cmd_fill(args: list[str]) -> int:
    from core.llm import fill_kind
    if len(args) < 2:
        print("usage: foundry fill <niche> <kind> [count] [--dry-run]")
        return 1
    niche, kind = args[0], args[1]
    count = int(args[2]) if len(args) > 2 and args[2].isdigit() else 10
    dry = "--dry-run" in args
    res = fill_kind(ROOT, niche, kind, count, dry_run=dry)
    print(f"{'dry run' if dry else 'fill'}: {niche}/{kind} requested {res.requested}, "
          f"accepted {len(res.accepted)}{' (after one retry)' if res.retried else ''}")
    for p in res.problems:
        print(f"  {WARN} {p}")
    if res.written:
        print(f"  {TICK} {res.written['added']} added, "
              f"{res.written['skipped_duplicate']} duplicate(s) skipped, "
              f"{res.written['total']} now in the user library")
    return 0 if res.ok or dry else 1


def cmd_sweep(args: list[str]) -> int:
    from core.sweep import sweep
    dist = ROOT / "dist" / args[0] if args else ROOT / "dist"
    if not dist.is_dir():
        print("nothing built yet — run `foundry build`")
        return 1
    # Each site is its own document root: pages reference /assets/... from the
    # site root, so serving the parent dist/ would 404 every stylesheet and the
    # probe would measure unstyled HTML.
    roots = [dist] if (dist / "index.html").is_file() and (dist / "assets").is_dir() \
        else sorted(d for d in dist.iterdir() if d.is_dir() and (d / "assets").is_dir())
    if not roots:
        print("no built site roots under dist/")
        return 1

    total_findings = 0
    for root in roots:
        res = sweep(root)
        if not res["available"]:
            print(f"{DOT} layout sweep not run: {res['reason']}")
            return 0
        print(f"\n{B}Layout sweep{OFF} {root.name} {DIM}{res['pages']} page(s) x "
              f"{len(res['widths'])} widths = {res['checks']} checks{OFF}")
        bad_contrast = res.get("contrast") or []
        if not res["findings"]:
            print(f"    {TICK} no horizontal overflow at "
                  f"{', '.join(str(w) for w in res['widths'])}px")
        if bad_contrast:
            seen = set()
            print(f"    {CROSS} {len(bad_contrast)} WCAG contrast failure(s)")
            for c in bad_contrast:
                key = (c["tag"], c["cls"])
                if key in seen:
                    continue
                seen.add(key)
                print(f"        {DIM}{c['tag']}.{c['cls'] or '-'}  {c['ratio']}:1 "
                      f"(needs {c['need']}:1)  {c['text']!r}{OFF}")
            total_findings += len(bad_contrast)
        else:
            print(f"    {TICK} no WCAG contrast failures")
        if not res["findings"]:
            continue
        for f in res["findings"][:12]:
            print(f"    {CROSS} {f['width']}px  {f['page']}")
            print(f"        {DIM}{f['path']}  class={f['cls']!r}  right={f['right']}px{OFF}")
        total_findings += len(res["findings"])
    if total_findings:
        print(f"\n{total_findings} overflow(s)")
        return 1
    return 0


def cmd_feed(args: list[str]) -> int:
    from core.feed import import_feed, VERTICALS
    if not args:
        print("usage: foundry feed <file.csv> [niche ...]")
        return 1
    path = Path(args[0])
    if not path.is_file():
        print(f"no such file: {path}")
        return 1
    rep = import_feed(ROOT, path, niches=args[1:] or None)

    print(f"\n{B}{rep.summary()}{OFF}")
    if rep.recovered:
        print(f"    {DIM}{rep.recovered:,} rows arrived with no city and no state and were "
              f"rebuilt from their ZIP — they would otherwise have been dropped{OFF}")
    if rep.unmatched_zip:
        print(f"    {WARN} {len(rep.unmatched_zip)} ZIP(s) not in the crosswalk, reported "
              f"rather than guessed: {', '.join(rep.unmatched_zip[:8])}")
    for vertical, n in list(rep.unknown_vertical.items())[:8]:
        print(f"    {WARN} unknown vertical {vertical!r} ({n:,} rows) — add it to "
              f"VERTICALS in core/feed.py")

    print(f"\n{B}{'niche':<26}{'cities':>8}{'counties':>10}{'states':>8}"
          f"{'median $':>10}{'total $':>12}{OFF}")
    for niche, info in sorted(rep.per_niche.items(),
                              key=lambda kv: -kv[1]["payout_total"]):
        gap = f"  {Y}{info['no_county']} no county{OFF}" if info["no_county"] else ""
        print(f"{niche:<26}{info['cities']:>8,}{info['counties']:>10,}"
              f"{len(info['states']):>8}{info['payout_median']:>10,.0f}"
              f"{info['payout_total']:>12,.0f}{gap}")

    missing = [n for n in rep.per_niche if not (ROOT / "data" / "niches" / f"{n}.yaml").is_file()]
    if missing:
        print(f"\n{Y}Coverage is loaded but these niches have no definition yet:{OFF} "
              f"{', '.join(sorted(missing))}")
    nolib = [n for n in rep.per_niche
             if not (ROOT / "data" / "library" / f"{n}.yaml").is_file()]
    if nolib:
        print(f"{Y}And these have no block library, so a build would refuse:{OFF} "
              f"{', '.join(sorted(nolib))}")
        print(f"{DIM}That refusal is correct — borrowing another niche's copy would put "
              f"roofing sentences on a plumbing site.{OFF}")
    return 0


def cmd_images(args: list[str]) -> int:
    from core import images as im
    if not args:
        print("usage: foundry images <niche> add <file> [slot] | fetch | list")
        return 1
    niche, sub = args[0], (args[1] if len(args) > 1 else "list")

    if sub == "add":
        if len(args) < 3:
            print("usage: foundry images <niche> add <file.csv> [slot]")
            return 1
        text = Path(args[2]).read_text(encoding="utf-8")
        rows, problems = im.parse_paste(text, args[3] if len(args) > 3 else "hero")
        stats = im.add_rows(ROOT, niche, rows)
        print(f"{TICK} {stats['added']} added, {stats['duplicate']} duplicate(s) skipped, "
              f"{stats['total']} in the manifest")
        for pr in problems[:12]:
            print(f"  {WARN} {pr}")
        return 0

    if sub == "fetch":
        limit = int(args[2]) if len(args) > 2 and args[2].isdigit() else None
        rep = im.fetch(ROOT, niche, limit=limit)
        print(f"{TICK} {rep.summary()}   {rep.bytes_saved / 1e6:.1f} MB stored")
        for url, why in rep.failed[:12]:
            print(f"  {CROSS} {url[:60]}\n      {DIM}{why}{OFF}")
        return 1 if rep.failed else 0

    counts = im.pool_counts(ROOT, niche)
    rows = im.read_manifest(ROOT, niche)
    ok = sum(1 for r in rows if r.status == "ok")
    bad = [r for r in rows if r.status == "failed"]
    print(f"\n{B}{niche}{OFF}  {len(rows)} in manifest · {ok} fetched · {len(bad)} failed")
    for slot, n in counts.items():
        bar = "█" * min(30, n)
        print(f"  {slot:<12}{n:>4}  {DIM}{bar}{OFF}")
    for r in bad[:8]:
        print(f"  {CROSS} {r.url[:56]}  {DIM}{r.note}{OFF}")
    return 0


def cmd_seo(args: list[str]) -> int:
    ids = args or list_sites(ROOT)
    worst = 0
    for site_id in ids:
        try:
            graph = load_graph(ROOT, site_id)
            res = build_site(ROOT, site_id)
            rep = seo_audit(ROOT, graph, res)
        except FoundryError as exc:
            print(f"\n{B}{site_id}{OFF}\n    {CROSS} {exc}")
            worst += 1
            continue
        tone = G if rep.score >= 90 else (Y if rep.score >= 70 else R)
        print(f"\n{B}{site_id}{OFF}   {tone}{rep.score}/100{OFF}")
        for k, v in rep.stats.items():
            print(f"    {DIM}{k:<22}{v}{OFF}")
        for f in rep.findings:
            mark = CROSS if f.severity == "blocker" else WARN
            print(f"    {mark} {B}{f.rule}{OFF}  {f.message}")
            print(f"        {DIM}why: {f.why}{OFF}")
            for e in f.evidence:
                print(f"        {DIM}- {e}{OFF}")
        if not rep.findings:
            print(f"    {TICK} clean")
        for n in rep.not_verified:
            print(f"    {DOT} not verified: {n}")
        worst += len(rep.blockers)
    return 1 if worst else 0


def cmd_css(args: list[str]) -> int:
    """Compile the optional Tailwind build, or report which engine each site uses."""
    import shutil as _sh
    import subprocess as _sp

    sub = args[0] if args else "check"
    out = ROOT / "assets" / "css" / "site.tailwind.css"

    if sub == "check":
        print(f"\n{B}CSS engines{OFF}")
        built = out.is_file()
        print(f"  built-in   {ROOT / 'assets' / 'css' / 'site.css'} "
              f"({(ROOT / 'assets' / 'css' / 'site.css').stat().st_size / 1024:.1f} KB)")
        print(f"  tailwind   " + (f"{out} ({out.stat().st_size / 1024:.1f} KB)"
                                  if built else f"{Y}not compiled{OFF}"))
        for site_id in list_sites(ROOT):
            raw = yaml.safe_load((ROOT / "data" / "sites" / f"{site_id}.yaml")
                                 .read_text(encoding="utf-8")) or {}
            eng = (raw.get("css_engine") or "builtin").lower()
            flag = f"  {CROSS} tailwind selected but not compiled" \
                if eng == "tailwind" and not built else ""
            print(f"    {site_id:<26} {eng}{flag}")
        return 0

    if sub != "tailwind":
        print("usage: foundry css tailwind | check")
        return 1

    if not _sh.which("npx"):
        print(f"{CROSS} npx not found. Tailwind is the OPTIONAL engine and needs Node;\n"
              f"  the built-in stylesheet is the default and needs nothing.\n"
              f"  Install Node, or leave css_engine unset.")
        return 1

    src = ROOT / "tailwind" / "src.css"
    if not (ROOT / "node_modules" / "tailwindcss").is_dir():
        print(f"{DIM}first run — installing tailwind locally (once){OFF}")
        inst = _sp.run(["npm", "install", "--no-audit", "--no-fund", "--silent"],
                       capture_output=True, text=True, cwd=str(ROOT), timeout=900)
        if inst.returncode != 0:
            print(f"{CROSS} npm install failed:\n{inst.stderr.strip()[-600:]}")
            return 1
    print(f"{DIM}compiling {src.name} -> {out.name}{OFF}")
    proc = _sp.run(["./node_modules/.bin/tailwindcss", "-i", str(src), "-o", str(out),
                    "--minify"], capture_output=True, text=True, cwd=str(ROOT), timeout=600)
    if proc.returncode != 0 or not out.is_file():
        print(f"{CROSS} tailwind build failed:\n{proc.stderr.strip()[-800:]}")
        return 1
    base = (ROOT / "assets" / "css" / "site.css").stat().st_size
    print(f"{TICK} {out.stat().st_size / 1024:.1f} KB "
          f"{DIM}(built-in sheet is {base / 1024:.1f} KB){OFF}")
    print(f"  {DIM}set `css_engine: tailwind` on a site to ship it{OFF}")
    return 0


def cmd_deploy(args: list[str]) -> int:
    """The CLI half of the panel's Deploy tab. Same preflight, same refusals —
    the panel imports this module, it does not reimplement it."""
    dry = "--dry-run" in args or "-n" in args
    rest = [a for a in args if not a.startswith("-")]
    if len(rest) < 2:
        print(f"usage: foundry deploy <site_id> <{' | '.join(TARGETS)}> [--dry-run]")
        return 1
    site_id, target = rest[0], rest[1]
    res = run_deploy(ROOT, site_id, target, dry_run=dry)

    if res.error and not res.steps:
        print(f"{CROSS} {res.error}")
        d = diagnose(res.error, res.ctx)
        if d["id"] != "unclassified":
            print(f"  {DIM}{d['title']}{OFF}\n  cause: {d['cause']}\n  fix:   {d['fix']}")
        return 1

    for note in res.notes:
        print(f"  {DIM}note: {note}{OFF}")
    for step in res.steps:
        mark = TICK if step.ok else CROSS
        print(f"{mark} {step.name:<9} {DIM}{step.cmd}{OFF}")
        if step.skipped:
            print(f"    {DIM}skipped — {step.skipped}{OFF}")
        elif step.output and (not step.ok or dry):
            for line in step.output.splitlines()[-12:]:
                print(f"    {DIM}{line}{OFF}")
    if res.ok:
        print(f"{TICK} {'would deploy' if dry else 'deployed'} {site_id} to {target}"
              f"{' — ' + res.url if res.url and not dry else ''}")
        return 0
    print(f"{CROSS} {res.error}")
    return 1


COMMANDS = {
    "deploy": cmd_deploy,
    "css": cmd_css,
    "seo": cmd_seo,
    "images": cmd_images,
    "feed": cmd_feed,
    "sweep": cmd_sweep,
    "build": cmd_build,
    "check": lambda a: cmd_build(a, verify_only=True),
    "compare": cmd_compare,
    "list": cmd_list,
    "serve": cmd_serve,
    "coverage": cmd_coverage,
    "fill": cmd_fill,
}


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd not in COMMANDS:
        print(f"unknown command {cmd!r}\n{__doc__}")
        return 1
    return COMMANDS[cmd](rest)


if __name__ == "__main__":
    raise SystemExit(main())
