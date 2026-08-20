#!/usr/bin/env python3
"""
Foundry — the local admin panel.

A local Flask app that writes YAML and shells nothing out to a model unless you
ask it to. The panel is NOT the system: delete it and the factory still runs
from `foundry.py`. That dependency direction is deliberate — the CLI is the
product, the panel is a convenience over it.

  START-PANEL.command   ->  http://127.0.0.1:5050/   (password: admin)

Port 5000 is skipped: macOS AirPlay Receiver holds it and answers 403 to
everything, which looks exactly like a broken app.
"""

from __future__ import annotations

import os
import secrets
import sys
import time
import traceback
from functools import wraps
from pathlib import Path

from flask import (Flask, flash, redirect, render_template, request, session,
                   url_for)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.feed import VERTICALS, import_feed, niche_value  # noqa: E402
from core import images as imagelib  # noqa: E402
from core.deploy import (SECRET_KEYS, TARGETS, deploy, load_config,  # noqa: E402
                         load_secrets, save_config, save_secrets)
from core.jobs import RUNNER  # noqa: E402
from core.graph import (FoundryError, STATES, list_sites, load_coverage,  # noqa: E402
                        load_graph, parse_coverage_text, save_coverage)
from core.library import KINDS, add_many, load_library, source_counts  # noqa: E402
from core.prompts import (GLOBAL_COLUMNS, REQUIRED_SCHEMA, SHAPE_CSV,  # noqa: E402
                          api_prompt, build_prompt, coverage as content_coverage,
                          csv_template, csv_to_blocks, finalize_ai_blocks,
                          global_template, intel_prompt, parse_global_csv,
                          required_fields, service_prompt, shape_of,
                          smart_parse, tag_claims)
from core import aiwrite  # noqa: E402
from core.tokens import unknown_tokens  # noqa: E402
from core.preview import PreviewPool, free_port, load_asset  # noqa: E402
from core.render import build_site  # noqa: E402
from core.seo import audit as seo_audit  # noqa: E402
from core.spawn import (BLOCK_AT, WARN_AT, capacity, create_from_rows,  # noqa: E402
                        delete_business, delete_site, find_seed, parse_bulk,
                        save_business, save_site)
from core.verify import (check_site, diagnose, load_shipped, record_shipped,  # noqa: E402
                         signature)

import yaml  # noqa: E402

app = Flask(__name__, template_folder="webui/templates", static_folder="webui/static")
app.secret_key = os.environ.get("FOUNDRY_SECRET") or secrets.token_hex(32)
PASSWORD = os.environ.get("FOUNDRY_PASSWORD", "admin")
PREVIEW = PreviewPool(ROOT / "dist")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def guard(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.is_file() else {}


def niches() -> list[str]:
    folder = ROOT / "data" / "niches"
    return sorted(p.stem for p in folder.glob("*.yaml")) if folder.is_dir() else []


def themes() -> dict:
    return read_yaml(ROOT / "data" / "themes.yaml")


def styles() -> dict:
    return read_yaml(ROOT / "data" / "styles.yaml")


def skeletons() -> dict:
    return read_yaml(ROOT / "data" / "skeletons.yaml")


def businesses() -> list[dict]:
    folder = ROOT / "data" / "businesses"
    if not folder.is_dir():
        return []
    return sorted((read_yaml(p) for p in folder.glob("*.yaml")),
                  key=lambda b: b.get("company", ""))


def coverage_states(niche: str) -> list[str]:
    folder = ROOT / "data" / "coverage" / niche
    return sorted(p.stem for p in folder.glob("*.csv")) if folder.is_dir() else []


def site_rows() -> list[dict]:
    shipped = load_shipped(ROOT)
    rows = []
    for site_id in list_sites(ROOT):
        raw = read_yaml(ROOT / "data" / "sites" / f"{site_id}.yaml")
        row = {"site_id": site_id, "raw": raw, "error": None, "cities": 0,
               "counties": 0, "built": False, "company": raw.get("business", "")}
        built_dir = ROOT / "dist" / raw.get("domain", "-")
        row["built"] = (built_dir / "index.html").is_file()
        row["shipped"] = site_id in shipped
        try:
            g = load_graph(ROOT, site_id)
            row["cities"] = len(g.locations)
            row["counties"] = len(g.counties)
            row["company"] = g.business.get("company", "")
        except FoundryError as exc:
            row["error"] = str(exc)
        rows.append(row)
    return rows


def build_one(site_id: str) -> dict:
    started = time.time()
    try:
        graph = load_graph(ROOT, site_id)
        result = build_site(ROOT, site_id)
        report = check_site(ROOT, graph, result)
        seo = seo_audit(ROOT, graph, result)
        # "Shipped" needs BOTH gates. A site failing either is built and
        # previewable; it is not shipped and it is not deployable.
        ok = report.passed and seo.passed
        if ok:
            record_shipped(ROOT, site_id, graph.site["niche"],
                           signature(result.copy_text), signature(result.blocks_text))
        PREVIEW.drop(graph.site["domain"])
        # Counts, not pages. A finished 26,000-page build holds ~300 MB of HTML
        # in memory, and it is all already on disk.
        return {"site_id": site_id, "ok": ok, "report": report, "seo": seo,
                "stats": {"static": len(result.pages), "edge": result.edge_pages,
                          "assets": result.assets_copied,
                          "total": result.total_pages},
                "seconds": round(time.time() - started, 1),
                "domain": graph.site["domain"], "diag": None}
    except FoundryError as exc:
        return {"site_id": site_id, "ok": False, "report": None, "seo": None,
                "stats": None, "seconds": round(time.time() - started, 1),
                "domain": None, "error": str(exc), "diag": diagnose(str(exc), exc.ctx)}
    except Exception as exc:                                  # noqa: BLE001
        return {"site_id": site_id, "ok": False, "report": None, "seo": None,
                "stats": None, "seconds": round(time.time() - started, 1),
                "domain": None, "error": f"{type(exc).__name__}: {exc}",
                "diag": diagnose(str(exc), {}),
                "trace": traceback.format_exc()[-1200:]}


@app.context_processor
def inject():
    return {"NICHES": niches(), "THEMES": themes(), "STYLES": styles(), "SKELETONS": skeletons(), "STATES": STATES,
            "BLOCK_AT": BLOCK_AT, "WARN_AT": WARN_AT}


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("password", ""), PASSWORD):
            session["in"] = True
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Wrong password.", "bad")
    return render_template("login.html", default=PASSWORD == "admin")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# dashboard
# --------------------------------------------------------------------------

@app.route("/site/<site_id>/download.zip")
@guard
def download_site(site_id: str):
    """Download a built site's static files as a zip — the fallback to the live
    Deploy targets, for uploading the site to any host by hand."""
    import io
    import zipfile
    from flask import Response
    try:
        domain = load_graph(ROOT, site_id).site["domain"]
    except FoundryError as exc:
        flash(str(exc), "bad")
        return redirect(url_for("dashboard"))
    root = ROOT / "dist" / domain
    if not (root / "index.html").is_file():
        flash(f"{site_id} has not been built yet.", "warn")
        return redirect(url_for("dashboard"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in root.rglob("*"):
            if f.is_file():
                z.write(f, str(f.relative_to(root)))
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition":
                             f'attachment; filename="{domain}.zip"'})


@app.route("/backup.zip")
@guard
def backup():
    """Download every piece of content YOU created as one zip — imported blocks,
    sites, businesses, coverage, the shipped record. A restore point you can
    keep off the server, and the clean way to move data between local and live.

    Deliberately does NOT include code or the shared base library: this is your
    data, the thing that must never be lost."""
    import io
    import zipfile
    from flask import Response
    want = ["data/library/user", "data/sites", "data/businesses",
            "data/coverage", "data/images", "data/shipped.json"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in want:
            p = ROOT / rel
            if p.is_file():
                z.write(p, rel)
            elif p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file():
                        z.write(f, str(f.relative_to(ROOT)))
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition":
                             'attachment; filename="foundry-data-backup.zip"'})


@app.route("/")
@guard
def dashboard():
    rows = site_rows()
    # Only the selected niche's capacity is computed and rendered. Twenty-four
    # niches x eighteen meters made the dashboard unusable, and a page nobody
    # scrolls to the bottom of is a page whose warnings nobody reads.
    all_niches = niches()
    used = [r["raw"].get("niche") for r in rows if r["raw"].get("niche")]
    pick = request.args.get("cap") or (used[0] if used else (all_niches[0] if all_niches else ""))
    libs, caps = {}, {}
    if pick:
        libs[pick] = load_library(ROOT, pick)
        nd = read_yaml(ROOT / "data" / "niches" / f"{pick}.yaml")
        caps[pick] = capacity(libs[pick].counts, len(nd.get("services") or []), 4,
                              read_yaml(ROOT / "data" / "global.yaml").get("counts"))
    return render_template("dashboard.html", rows=rows, businesses=businesses(),
                           libs=libs, caps=caps, cap_pick=pick, all_niches=all_niches,
                           coverage={n: coverage_states(n) for n in niches()})


# --------------------------------------------------------------------------
# businesses
# --------------------------------------------------------------------------

@app.route("/business/new")
@app.route("/business/<slug>")
@guard
def business_form(slug: str | None = None):
    record = read_yaml(ROOT / "data" / "businesses" / f"{slug}.yaml") if slug else {}
    return render_template("business_form.html", b=record, slug=slug)


@app.route("/business/save", methods=["POST"])
@guard
def business_save():
    f = request.form
    facts = {
        "years_in_business": int(f["years"]) if f.get("years", "").strip().isdigit() else False,
        "hours": f.get("hours", "").strip() or None,
        "free_estimates": bool(f.get("free_estimates")),
        "licensed": bool(f.get("licensed")),
        "insured": bool(f.get("insured")),
        "emergency_24_7": bool(f.get("emergency_24_7")),
        "warranty_years": int(f["warranty"]) if f.get("warranty", "").strip().isdigit() else False,
        "financing": bool(f.get("financing")),
        "family_owned": bool(f.get("family_owned")),
    }
    try:
        slug = save_business(ROOT, {
            "slug": f.get("slug") or f.get("company"), "company": f.get("company"),
            "brand": f.get("brand"), "phone": f.get("phone"), "email": f.get("email"),
            "street": f.get("street"), "city": f.get("city"), "state": f.get("state"),
            "zip": f.get("zip"), "facts": facts,
        })
        flash(f"Saved {f.get('company')}.", "good")
        return redirect(url_for("site_form", business=slug)
                        if f.get("then") == "site" else url_for("dashboard"))
    except FoundryError as exc:
        flash(str(exc), "bad")
        return redirect(url_for("business_form"))


@app.route("/business/<slug>/delete", methods=["POST"])
@guard
def business_delete(slug: str):
    used = delete_business(ROOT, slug)
    flash(f"Still used by {', '.join(used)} — delete those sites first." if used
          else f"Deleted {slug}.", "bad" if used else "good")
    return redirect(url_for("dashboard"))


# --------------------------------------------------------------------------
# sites
# --------------------------------------------------------------------------

@app.route("/api/cities/<niche>/<state>")
@guard
def api_cities(niche: str, state: str):
    """Cities in one state, richest first.

    Loaded on demand rather than rendered into the form: a national footprint is
    11,356 cities and no form should carry that many checkboxes up front.
    """
    from flask import jsonify
    try:
        locs, _ = load_coverage(ROOT, niche, [state])
    except FoundryError as exc:
        return jsonify({"error": str(exc), "cities": []}), 200
    rows = sorted(locs, key=lambda l: (-l.payout, l.city))
    return jsonify({"state": state.upper(), "count": len(rows), "cities": [
        {"slug": l.slug, "city": l.city, "county": l.county,
         "zips": len(l.zips), "payout": round(l.payout, 2)} for l in rows]})


@app.route("/site/new")
@app.route("/site/<site_id>")
@guard
def site_form(site_id: str | None = None):
    record = read_yaml(ROOT / "data" / "sites" / f"{site_id}.yaml") if site_id else {}
    if request.args.get("business"):
        record.setdefault("business", request.args["business"])
    niche_services, niche_labels = {}, {}
    for n in niches():
        raw = read_yaml(ROOT / "data" / "niches" / f"{n}.yaml")
        niche_services[n] = raw.get("services") or []
        niche_labels[n] = raw.get("label") or n.replace("-", " ").title()
    return render_template("site_form.html", s=record, site_id=site_id,
                           businesses=businesses(), niche_services=niche_services,
                           niche_labels=niche_labels,
                           cover={n: coverage_states(n) for n in niches()})


@app.route("/site/save", methods=["POST"])
@guard
def site_save():
    f = request.form
    top = f.get("prerender_top_n", "").strip()

    # The services box draws a checkbox for EVERY niche's services and hides the
    # ones that do not belong to the selected niche. Three slugs are shared by
    # two niches each, so a browser that restores form state on a Back
    # navigation can post the hidden twin as well — and the site then owns the
    # same service twice. Filter to the chosen niche and de-duplicate here,
    # server side: the form is a suggestion, this is the record.
    chosen = read_yaml(ROOT / "data" / "niches" / f"{f.get('niche')}.yaml")
    valid = {s["slug"] for s in (chosen.get("services") or [])}
    services = list(dict.fromkeys(s for s in f.getlist("services") if s in valid))
    payload = {
        "site_id": f.get("site_id") or None,
        "business": f.get("business"), "niche": f.get("niche"),
        "domain": f.get("domain"), "theme": f.get("theme"), "style": f.get("style"), "skeleton": f.get("skeleton"),
        "composition_seed": f.get("composition_seed") or 1,
        "states": f.getlist("states"),
        "cities": f.getlist("cities"),
        "mode": f.get("mode"),
        "prerender_top_n": None if top in ("", "all") else int(top),
        "location_variants": f.get("location_variants") or 4,
        "services": services,
    }
    try:
        site_id = save_site(ROOT, payload)
        if f.get("autoseed"):
            graph = load_graph(ROOT, site_id)
            found = find_seed(ROOT, graph, exclude=site_id,
                              candidates=range(1, int(f.get("search", 250)) + 1))
            payload["site_id"] = site_id
            payload["composition_seed"] = found.seed
            save_site(ROOT, payload)
            flash(f"Seed {found.seed} chosen from {found.tried} candidates — "
                  f"{found.advice}", {"clear": "good", "warn": "warn",
                                      "crowded": "bad"}[found.verdict])
        else:
            flash(f"Saved {site_id}.", "good")
        return redirect(url_for("build", site=site_id) if f.get("then") == "build"
                        else url_for("dashboard"))
    except FoundryError as exc:
        flash(str(exc), "bad")
        return redirect(url_for("site_form"))


@app.route("/site/<site_id>/delete", methods=["POST"])
@guard
def site_delete(site_id: str):
    delete_site(ROOT, site_id)
    flash(f"Deleted {site_id}. Its business record is untouched.", "good")
    return redirect(url_for("dashboard"))


@app.route("/site/<site_id>/seed", methods=["POST"])
@guard
def site_seed(site_id: str):
    try:
        graph = load_graph(ROOT, site_id)
        found = find_seed(ROOT, graph, exclude=site_id,
                          candidates=range(1, int(request.form.get("search", 250)) + 1))
        raw = read_yaml(ROOT / "data" / "sites" / f"{site_id}.yaml")
        raw["composition_seed"] = found.seed
        (ROOT / "data" / "sites" / f"{site_id}.yaml").write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
        flash(f"{site_id}: seed {found.seed} of {found.tried} tried — {found.advice}",
              {"clear": "good", "warn": "warn", "crowded": "bad"}[found.verdict])
    except FoundryError as exc:
        flash(str(exc), "bad")
    return redirect(url_for("dashboard"))


# --------------------------------------------------------------------------
# spin-off — "another one like that, different branding"
# --------------------------------------------------------------------------

@app.route("/spinoff/<site_id>", methods=["GET", "POST"])
@guard
def spinoff(site_id: str):
    source = read_yaml(ROOT / "data" / "sites" / f"{site_id}.yaml")
    if not source:
        flash("No such site.", "bad")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        f = request.form
        try:
            slug = save_business(ROOT, {
                "company": f.get("company"), "phone": f.get("phone"),
                "email": f.get("email"), "city": f.get("city"), "state": f.get("state"),
            })
            payload = {
                "business": slug, "niche": source["niche"], "domain": f.get("domain"),
                "theme": f.get("theme") or source.get("theme"),
                "style": f.get("style") or source.get("style"),
                "skeleton": f.get("skeleton") or source.get("skeleton"),
                "composition_seed": 1,
                "states": (source.get("coverage") or {}).get("states") or [],
                "mode": (source.get("render") or {}).get("mode", "hybrid"),
                "prerender_top_n": (source.get("render") or {}).get("prerender_top_n"),
                "location_variants": (source.get("render") or {}).get("location_variants", 4),
            }
            new_id = save_site(ROOT, payload)
            graph = load_graph(ROOT, new_id)
            found = find_seed(ROOT, graph, exclude=new_id, candidates=range(1, 251))
            payload["site_id"] = new_id
            payload["composition_seed"] = found.seed
            save_site(ROOT, payload)
            flash(f"Created {new_id} with seed {found.seed} — {found.advice}",
                  {"clear": "good", "warn": "warn", "crowded": "bad"}[found.verdict])
            return redirect(url_for("build", site=new_id))
        except FoundryError as exc:
            flash(str(exc), "bad")

    return render_template("spinoff.html", src=source, site_id=site_id)


# --------------------------------------------------------------------------
# bulk
# --------------------------------------------------------------------------

@app.route("/bulk", methods=["GET", "POST"])
@guard
def bulk():
    rows, results, form = [], None, request.form
    if request.method == "POST":
        text = form.get("rows", "")
        if form.get("file_text"):
            text = form["file_text"]
        rows = parse_bulk(text)
        if form.get("action") == "create":
            top = form.get("prerender_top_n", "").strip()
            results = create_from_rows(
                ROOT, rows, niche=form.get("niche"), states=form.getlist("states"),
                theme=form.get("theme") or "slate", style=form.get("style") or "classic",
                skeleton=form.get("skeleton") or "standard", mode=form.get("mode") or "hybrid",
                prerender_top_n=None if top in ("", "all") else int(top),
                variants=int(form.get("location_variants") or 4),
                search_seeds=int(form.get("search") or 200))
    return render_template("bulk.html", rows=rows, results=results, form=form,
                           cover={n: coverage_states(n) for n in niches()})


# --------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------

@app.route("/coverage", methods=["GET", "POST"])
@guard
def coverage():
    report = None
    feed = None
    if request.method == "POST" and request.form.get("mode") == "feed":
        upload = request.files.get("feed_file")
        if not upload or not upload.filename:
            flash("Choose the feed CSV to upload.", "bad")
        else:
            tmp = ROOT / "data" / "_feed-upload.csv"
            upload.save(tmp)
            try:
                feed = import_feed(ROOT, tmp, niches=request.form.getlist("only") or None)
                flash(feed.summary(), "good")
            except Exception as exc:                       # noqa: BLE001
                flash(f"Could not read the feed: {exc}", "bad")
            finally:
                tmp.unlink(missing_ok=True)
    elif request.method == "POST":
        niche = request.form.get("niche", "").strip()
        text = request.form.get("text", "")
        upload = request.files.get("file")
        if upload and upload.filename:
            text = upload.read().decode("utf-8", "replace")
        if not niche or not text.strip():
            flash("Pick a niche and paste or upload the buyer's list.", "bad")
        else:
            parsed, rep = parse_coverage_text(text)
            written = save_coverage(ROOT, niche, parsed)
            report = {"rep": rep, "written": written, "niche": niche}
            flash(f"{rep.rows_kept} rows kept for {niche}. The niche was REPLACED, "
                  f"not merged — a buyer publishes a complete footprint.", "good")

    loaded = {}
    for n in niches():
        try:
            locs, rep = load_coverage(ROOT, n)
            loaded[n] = {"cities": len(locs), "states": sorted({l.state_abbr for l in locs}),
                         "counties": len({l.county for l in locs}), "rep": rep}
        except FoundryError:
            loaded[n] = None
    return render_template("coverage.html", loaded=loaded, report=report, feed=feed,
                           verticals=sorted(set(VERTICALS.values())))


# --------------------------------------------------------------------------
# library
# --------------------------------------------------------------------------

@app.route("/prompts", methods=["GET", "POST"])
@guard
def prompts():
    niche = request.values.get("niche") or (niches()[0] if niches() else "")
    kind = request.values.get("kind") or "faqs"
    n = max(1, min(50, int(request.values.get("n") or 20)))
    niche_def = read_yaml(ROOT / "data" / "niches" / f"{niche}.yaml") if niche else {}
    niche_label = niche_def.get("label") or niche.replace("-", " ").title()

    # The niche's own service list drives the per-service prompt: pick one and the
    # prompt is written for that service, its rows pre-tagged so the copy lands on
    # that service's pages only.
    svc_list = [s for s in (niche_def.get("services") or []) if s.get("slug")]
    service_slug = request.values.get("service") or (svc_list[0]["slug"] if svc_list else "")
    svc_label = next((s["name"] for s in svc_list if s["slug"] == service_slug),
                     service_slug.replace("-", " ").title())
    svc_prompt = (service_prompt(niche, niche_label, svc_label, service_slug, n)
                  if service_slug else "")

    if request.method == "POST":
        try:
            raw = request.form.get("content", "").strip()
            up = request.files.get("file")
            if up and up.filename:
                raw = up.read().decode("utf-8", "replace")
            # One tolerant parser: YAML list, CSV, or one-block-per-line all work,
            # so it does not matter which the operator pastes.
            blocks = smart_parse(kind, raw)
            if not blocks:
                raise ValueError(
                    f"nothing recognised for {kind}. A {kind} block needs "
                    f"{', '.join(required_fields(kind)) or 'text'}. Paste ChatGPT's YAML "
                    f"list, a CSV with the columns {', '.join(SHAPE_CSV[shape_of(kind)])}, "
                    "or — for this block type — one block per line.")
            stats = add_many(ROOT, niche, kind, blocks)
            flash(f"{stats['added']} added to the {niche} pool, "
                  f"{stats['skipped_duplicate']} duplicate(s) skipped, "
                  f"{stats['total']} now in your library for {kind}.", "good")
            bad = unknown_tokens(blocks)
            if bad:
                flash("Heads up — these tokens are not recognised and will show "
                      "literally on the page (fix them before building): "
                      + ", ".join("{" + t + "}" for t in bad), "warn")
        except Exception as exc:                              # noqa: BLE001
            flash(f"Could not save: {exc}", "bad")

    lib = load_library(ROOT, niche) if niche else None
    return render_template(
        "prompts.html", niche=niche, kind=kind, n=n, kinds=sorted(KINDS),
        niche_label=niche_label,
        prompt=build_prompt(kind, niche_label, n),
        csv_cols=SHAPE_CSV[shape_of(kind)],
        pool_count=(lib.counts.get(kind, 0) if lib else 0),
        services=svc_list, service=service_slug, svc_label=svc_label,
        service_prompt=svc_prompt)


@app.route("/prompts/intel")
@guard
def prompts_intel():
    niche = request.values.get("niche") or (niches()[0] if niches() else "")
    target = max(2, min(200, int(request.values.get("target") or 25)))
    niche_def = read_yaml(ROOT / "data" / "niches" / f"{niche}.yaml") if niche else {}
    niche_label = niche_def.get("label") or niche.replace("-", " ").title()
    services = len(niche_def.get("services") or []) or 6

    lib = load_library(ROOT, niche) if niche else None
    cov, weakest, split = [], [], {}
    if lib:
        cap = capacity(lib.counts, services, 4,
                       read_yaml(ROOT / "data" / "global.yaml").get("counts"))
        cov = content_coverage(cap, target)
        weakest = [r for r in cov if r["strength"] in ("empty", "thin")][:6]
        split = source_counts(ROOT, niche)     # base / niche / yours per kind
        for r in cov:
            r["src"] = split.get(r["kind"], {"base": 0, "niche": 0, "user": 0})
    # Per-section completion: how full the pool is relative to what `target` sites
    # need. Drives the coloured progress bars.
    for r in cov:
        r["pct"] = min(100, round(100 * r["pool"] / max(1, r["target_pool"])))
    tally = {s: 0 for s in ("empty", "thin", "ok", "strong")}
    for r in cov:
        tally[r["strength"]] = tally.get(r["strength"], 0) + 1
    niche_pct = round(sum(r["pct"] for r in cov) / len(cov)) if cov else 0

    # All-niches overview — the birds-eye "which niche is still empty". Counting the
    # operator's OWN blocks (niche + user files, skipping the shared base) is cheap
    # and is what actually differs between niches; a full capacity pass over 24
    # niches would re-merge the base each time and take seconds.
    overview = []
    libdir = ROOT / "data" / "library"
    for x in niches():
        own = 0
        for p in (libdir / f"{x}.yaml", libdir / "user" / f"{x}.yaml"):
            if p.is_file():
                data = read_yaml(p) or {}
                own += sum(len(v) for v in data.values() if isinstance(v, list))
        overview.append({"niche": x, "own": own})
    max_own = max((o["own"] for o in overview), default=0) or 1
    for o in overview:
        o["pct"] = round(100 * o["own"] / max_own)
    overview.sort(key=lambda o: (-o["own"], o["niche"]))

    # a ready small prompt for each weak section (fills the master-CSV format);
    # service-scoped kinds get the niche's own services so the prompt tags rows.
    svc_list = [s for s in (niche_def.get("services") or []) if s.get("slug")]
    prompts = {r["kind"]: intel_prompt(niche, niche_label, r["kind"],
                                       max(10, min(40, r["gap"] or 20)),
                                       services=svc_list)
               for r in cov}
    return render_template("prompts_intel.html", niche=niche, niche_label=niche_label,
                           target=target, coverage=cov, weakest=weakest,
                           prompts=prompts, kinds=sorted(KINDS),
                           ai_ready=aiwrite.have_key(), niche_pct=niche_pct,
                           tally=tally, overview=overview,
                           sites_now=(min((r["sites"] for r in cov), default=0)))


@app.route("/prompts/intel/generate", methods=["POST"])
@guard
def prompts_intel_generate():
    """Run one section's Intel prompt through the OpenAI API and store the result
    straight into that niche's pool — the button that removes the copy-paste."""
    niche = request.form.get("niche", "")
    kind = request.form.get("kind", "")
    n = max(1, min(100, int(request.form.get("n") or 20)))
    back = url_for("prompts_intel", niche=niche)

    if kind not in KINDS or niche not in set(niches()):
        flash("Unknown niche or block type.", "bad")
        return redirect(back)
    if not aiwrite.have_key():
        flash("No OpenAI key set. Add FOUNDRY_OPENAI_KEY to the environment and redeploy.", "bad")
        return redirect(back)

    niche_def = read_yaml(ROOT / "data" / "niches" / f"{niche}.yaml")
    niche_label = niche_def.get("label") or niche.replace("-", " ").title()
    svc_list = [s for s in (niche_def.get("services") or []) if s.get("slug")]

    # A model can't reliably return 100 blocks in one reply — it truncates. So we
    # ask in batches of BATCH and accumulate distinct blocks until we reach n
    # (or a batch comes back empty / errors). YAML keeps prose comma-safe.
    from core.library import fingerprint
    BATCH = 20
    collected: list = []
    seen: set = set()
    batches = 0
    max_batches = (n + BATCH - 1) // BATCH + 2      # a little slack for duplicates
    error = None
    while len(collected) < n and batches < max_batches:
        batches += 1
        want = min(BATCH, n - len(collected))
        prompt = api_prompt(niche, niche_label, kind, max(want, 8), services=svc_list)
        try:
            raw = aiwrite.strip_fences(aiwrite.generate(prompt, max_tokens=8000))
        except aiwrite.AIError as exc:
            error = str(exc)
            break                                    # keep whatever we already have
        blocks = (smart_parse(kind, raw)
                  or parse_global_csv(raw, {niche})["grouped"].get((niche, kind), [])
                  or csv_to_blocks(kind, raw))
        blocks = finalize_ai_blocks(kind, blocks)
        fresh = 0
        for b in blocks:
            fp = fingerprint(b)
            if fp in seen:
                continue
            seen.add(fp)
            collected.append(b)
            fresh += 1
            if len(collected) >= n:
                break
        if fresh == 0:                               # model repeating itself — stop
            break

    if not collected:
        flash(error or (f"The model's reply had no importable {kind} blocks. Try "
                        "again — occasionally the model returns an off-format answer."), "bad")
        return redirect(back)

    stats = add_many(ROOT, niche, kind, collected[:n])
    note = f" (stopped early: {error})" if error else ""
    flash(f"Generated {len(collected)} {kind} block(s) in {batches} batch(es): "
          f"{stats['added']} added, {stats['skipped_duplicate']} duplicate(s) skipped — "
          f"now {stats['total']} in the {niche} pool.{note}", "good")
    return redirect(back)


@app.route("/prompts/global.csv")
@guard
def prompts_global_template():
    from flask import Response
    return Response(global_template(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             'attachment; filename="foundry-content-master.csv"'})


@app.route("/prompts/global", methods=["GET", "POST"])
@guard
def prompts_global():
    result = None
    if request.method == "POST":
        raw = ""
        up = request.files.get("file")
        if up and up.filename:
            raw = up.read().decode("utf-8", "replace")
        else:
            raw = request.form.get("content", "")
        parsed = parse_global_csv(raw, set(niches()))
        rep = parsed["report"]
        if rep.get("error"):
            flash(rep["error"], "bad")
        else:
            # One growing file, re-imported freely: add_many fingerprints, so
            # rows seen before are skipped and only fresh ones land.
            rows = []
            bad_tokens: set[str] = set()
            for (niche, kind), blocks in sorted(parsed["grouped"].items()):
                stats = add_many(ROOT, niche, kind, blocks)
                bad_tokens.update(unknown_tokens(blocks))
                rows.append({"niche": niche, "kind": kind,
                             "added": stats["added"],
                             "skipped": stats["skipped_duplicate"],
                             "total": stats["total"]})
            # Per-niche roll-up, so a 24-niche import reads at a glance rather
            # than as a 200-row table.
            by_niche: dict[str, dict] = {}
            for r in rows:
                b = by_niche.setdefault(r["niche"], {"added": 0, "skipped": 0, "kinds": 0})
                b["added"] += r["added"]
                b["skipped"] += r["skipped"]
                b["kinds"] += 1 if r["added"] else 0
            summary = sorted(({"niche": n, **v} for n, v in by_niche.items()),
                             key=lambda x: (-x["added"], x["niche"]))
            problems = rep.get("problems") or []
            result = {"rows": rows, "report": rep, "summary": summary,
                      "unknown_tokens": sorted(bad_tokens), "problems": problems,
                      "added": sum(r["added"] for r in rows),
                      "skipped": sum(r["skipped"] for r in rows)}
            if rows:
                flash(f"{result['added']} block(s) added, {result['skipped']} duplicate(s) "
                      f"skipped across {len(rows)} niche/kind group(s).", "good")
                if problems:
                    flash(f"{len(problems)} note(s) about your file — see the details below.", "warn")
            elif problems:
                # Say exactly which rows failed and why, not a blank "nothing imported".
                shown = "  ".join(problems[:5])
                more = f"  (+{len(problems) - 5} more)" if len(problems) > 5 else ""
                flash(f"Nothing imported — {len(problems)} row(s) had problems: {shown}{more}", "bad")
            else:
                flash("No rows found. Make sure the file has a header row plus at least one data row.", "warn")
    return render_template("prompts_global.html", columns=GLOBAL_COLUMNS,
                           kinds=sorted(KINDS), niche_list=niches(), result=result,
                           required_schema=REQUIRED_SCHEMA)


@app.route("/prompts/template/<kind>.csv")
@guard
def prompts_template(kind: str):
    from flask import Response
    if kind not in KINDS:
        return redirect(url_for("prompts"))
    return Response(csv_template(kind), mimetype="text/csv",
                    headers={"Content-Disposition":
                             f'attachment; filename="{kind}-template.csv"'})


@app.route("/library", methods=["GET", "POST"])
@guard
def library():
    niche = request.values.get("niche") or (niches()[0] if niches() else "")
    kind = request.values.get("kind") or "faqs"

    if request.method == "POST" and request.form.get("action") == "add":
        raw = request.form.get("blocks", "").strip()
        try:
            parsed = yaml.safe_load(raw)
            if not isinstance(parsed, list):
                raise ValueError("expected a YAML list of blocks")
            stats = add_many(ROOT, niche, kind, parsed)
            flash(f"{stats['added']} added, {stats['skipped_duplicate']} duplicate(s) "
                  f"skipped, {stats['total']} now in the user library.", "good")
        except Exception as exc:                              # noqa: BLE001
            flash(f"Could not parse: {exc}", "bad")

    lib = load_library(ROOT, niche) if niche else None
    niche_def = read_yaml(ROOT / "data" / "niches" / f"{niche}.yaml") if niche else {}
    caps = capacity(lib.counts, len(niche_def.get("services") or []), 4,
                    read_yaml(ROOT / "data" / "global.yaml").get("counts")) if lib else {}
    shipped = read_yaml(ROOT / "data" / "library" / f"{niche}.yaml") if niche else {}
    example = (shipped.get(kind) or [None])[0]
    return render_template("library.html", niche=niche, kind=kind, lib=lib, caps=caps,
                           kinds=sorted(KINDS), blocks=(lib.pool(kind) if lib else []),
                           src=source_counts(ROOT, niche) if niche else {},
                           example=yaml.safe_dump([example], sort_keys=False,
                                                  allow_unicode=True) if example else "")


# --------------------------------------------------------------------------
# build + preview
# --------------------------------------------------------------------------

@app.route("/images", methods=["GET", "POST"])
@guard
def images():
    niche = request.values.get("niche") or (niches()[0] if niches() else "")
    fetched = None
    problems: list[str] = []

    if request.method == "POST" and niche:
        action = request.form.get("action")
        if action == "add":
            text = request.form.get("rows", "")
            upload = request.files.get("file")
            if upload and upload.filename:
                text = upload.read().decode("utf-8", "replace")
            rows, problems = imagelib.parse_paste(text, request.form.get("slot") or "hero")
            stats = imagelib.add_rows(ROOT, niche, rows)
            flash(f"{stats['added']} added, {stats['duplicate']} duplicate(s) skipped, "
                  f"{stats['total']} in the manifest.", "good")
        elif action == "fetch":
            try:
                fetched = imagelib.fetch(ROOT, niche)
                flash(f"{fetched.summary()} · {fetched.bytes_saved / 1e6:.1f} MB stored",
                      "bad" if fetched.failed else "good")
            except Exception as exc:                          # noqa: BLE001
                flash(f"Fetch failed: {exc}", "bad")

    rows = imagelib.read_manifest(ROOT, niche) if niche else []
    return render_template("images.html", niche=niche, rows=rows,
                           counts=imagelib.pool_counts(ROOT, niche) if niche else {},
                           slots=imagelib.SLOTS, fetched=fetched, problems=problems,
                           failed=[r for r in rows if r.status == "failed"])


@app.route("/build", methods=["GET", "POST"])
@guard
def build():
    """Start a build and redirect. NEVER render the results inline.

    Rendering inline is what made this look broken: eight sites took 62 seconds,
    Flask answered only at the end, and the browser showed a pending tab the
    whole time. A page that takes a minute to arrive is indistinguishable from a
    button that does nothing.
    """
    targets = request.values.getlist("site") or [r["site_id"] for r in site_rows()]
    job = RUNNER.start(targets, build_one)
    if set(job.targets) != set(targets) and job.running:
        flash("A build is already running — showing that one. Builds write to the "
              "same dist/ and shipped record, so they run one at a time.", "warn")
    return redirect(url_for("build_status", job_id=job.id))


@app.route("/build/<job_id>")
@guard
def build_status(job_id: str):
    job = RUNNER.get(job_id)
    if not job:
        flash("That build is no longer in memory. Builds are not persisted — the "
              "sites themselves are, in dist/.", "warn")
        return redirect(url_for("dashboard"))
    return render_template("build.html", job=job, results=job.done)


# --------------------------------------------------------------------------
# deploy
# --------------------------------------------------------------------------

def deploy_one(site_id: str, target: str, dry_run: bool) -> dict:
    try:
        res = deploy(ROOT, site_id, target, dry_run=dry_run)
    except Exception as exc:                                  # noqa: BLE001
        return {"site_id": site_id, "ok": False, "target": target,
                "error": f"{type(exc).__name__}: {exc}", "diag": diagnose(str(exc), {}),
                "trace": traceback.format_exc()[-1200:], "res": None, "seconds": 0}
    diag = None
    if res.error and not res.ok:
        # Preflight put its ctx on the result; a failed command has none, so
        # diagnose reads the step output and may fall through to unclassified.
        step = res.failed_step
        diag = diagnose(step.output if step else res.error, res.ctx)
    return {"site_id": site_id, "ok": res.ok, "target": target,
            "error": res.error, "diag": diag, "res": res, "seconds": res.seconds}



@app.route("/deploy", methods=["GET"])
@guard
def deploy_home():
    cfg = load_config(ROOT)
    secrets = load_secrets(ROOT)
    return render_template("deploy.html", rows=site_rows(), cfg=cfg,
                           targets=TARGETS,
                           secret_keys=SECRET_KEYS,
                           secrets_set={k: bool(secrets.get(k)) for k in SECRET_KEYS},
                           recent=[j for j in RUNNER.recent() if j.kind == "deploy"][:5])


@app.route("/deploy/settings", methods=["POST"])
@guard
def deploy_settings():
    site_id, target = request.form["site_id"], request.form["target"]
    if target not in TARGETS:
        flash("Unknown target.", "bad")
        return redirect(url_for("deploy_home"))
    cfg = load_config(ROOT)
    fields = {k[2:]: v.strip() for k, v in request.form.items()
              if k.startswith("f_") and v.strip()}
    # An absent checkbox means OFF, not "unchanged" — a browser posts nothing for
    # an unticked box, so reading only what arrived would make "GitHub Pages" a
    # setting you can turn on and never off again.
    if target == "github":
        fields.update(pages="pages" in request.form, force="force" in request.form)

    required = {"github": "repo", "cloudflare": None, "server": "host"}[target]
    cfg.setdefault(site_id, {})
    if required and not fields.get(required):
        cfg[site_id].pop(target, None)          # emptying the key field clears it
        flash(f"Cleared the {target} settings for {site_id}.", "warn")
    else:
        cfg[site_id][target] = fields
        flash(f"{target} settings saved for {site_id}. Dry run before the first real "
              f"deploy — it prints the exact commands without running them.", "good")
    if not cfg[site_id]:
        cfg.pop(site_id)
    save_config(ROOT, cfg)
    return redirect(url_for("deploy_home", site=site_id))


@app.route("/deploy/credentials", methods=["POST"])
@guard
def deploy_credentials():
    save_secrets(ROOT, {k: request.form.get(k, "") for k in SECRET_KEYS})
    flash("Credentials written to data/secrets.yaml at 0600 and added to "
          ".gitignore. They are never rendered back into this page.", "good")
    return redirect(url_for("deploy_home"))


@app.route("/deploy/run", methods=["POST"])
@guard
def deploy_run():
    target = request.form.get("target", "")
    dry = bool(request.form.get("dry"))
    sites = request.form.getlist("site")
    if not sites:
        flash("Pick at least one site.", "warn")
        return redirect(url_for("deploy_home"))
    job = RUNNER.start(sites, lambda s: deploy_one(s, target, dry), kind="deploy")
    if job.kind != "deploy" or set(job.targets) != set(sites):
        flash("Something is already running — showing that instead. Builds rewrite "
              "dist/ and deploys read it, so they take turns.", "warn")
    return redirect(url_for("job_status", job_id=job.id))


@app.route("/job/<job_id>")
@guard
def job_status(job_id: str):
    job = RUNNER.get(job_id)
    if not job:
        flash("That job is no longer in memory. Jobs are not persisted — the "
              "sites are, in dist/.", "warn")
        return redirect(url_for("dashboard"))
    template = "deploy_status.html" if job.kind == "deploy" else "build.html"
    return render_template(template, job=job, results=job.done)


@app.route("/preview/<site_id>/", defaults={"subpath": ""})
@app.route("/preview/<site_id>/<path:subpath>")
@guard
def preview(site_id: str, subpath: str):
    """Serve a built site THROUGH the panel, on the panel's own origin.

    The old preview redirected to a per-site http://127.0.0.1:<port>/ server.
    That only works when the panel and the browser are the same machine. Hosted
    on a server, 127.0.0.1 is the visitor's laptop, so it "refused to connect".
    Here the panel serves the site's files itself and re-roots every absolute
    link/asset under /preview/<id>/, so the whole site is browsable on-domain.
    """
    from flask import Response, abort
    try:
        domain = load_graph(ROOT, site_id).site["domain"]
    except FoundryError as exc:
        flash(str(exc), "bad")
        return redirect(url_for("dashboard"))
    site_root = ROOT / "dist" / domain
    if not (site_root / "index.html").is_file():
        flash(f"{site_id} has not been built yet.", "warn")
        return redirect(url_for("dashboard"))
    body, ctype = load_asset(site_root, subpath, f"/preview/{site_id}")
    if body is None:
        abort(404)
    resp = Response(body, content_type=ctype)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def main() -> int:
    port = free_port(int(os.environ.get("PORT", 5050)))
    print(f"\n  Foundry panel   http://127.0.0.1:{port}/")
    print(f"  password        {PASSWORD}"
          f"{'   (default — set FOUNDRY_PASSWORD to change it)' if PASSWORD == 'admin' else ''}")
    print("\n  Control-C to stop.\n")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
