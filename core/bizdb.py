"""
Foundry — the BUSINESS database (SQLite).

Businesses used to live as one YAML file each under data/businesses/. That is
fine for a few hundred, but the panel and the directory build read *every* file
on *every* page/build, so at thousands — let alone the 50,000+ this engine is
meant to hold — it crawls.

This module keeps the businesses in a single SQLite file (data/businesses.db)
with indexes, so counts, search, pagination ("load the next 500") and per-niche
directory queries are instant and never touch the filesystem per row.

DESIGN
  * YAML stays the canonical per-record store (single-business marketing sites
    still read data/businesses/<slug>.yaml via core.graph). The DB is the fast
    QUERY layer, kept in sync on every write and rebuildable from YAML at any
    time. Nothing is lost if the DB file is deleted — it rebuilds.
  * The stored `niche` is the EFFECTIVE trade: the business's own niche, else
    inferred from its scraped category, else from its company name. That is
    exactly what a directory filters on, precomputed once at write time.
  * WAL mode + short-lived connections: safe with gunicorn's threaded worker.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

_WRITE_LOCK = threading.Lock()

# Columns kept as real, indexed fields; the complete fact block is also stored
# as JSON so nothing about a record is lost in the DB copy.
_COLS = ["slug", "company", "brand", "phone", "email", "street", "city", "state",
         "zip", "place", "niche", "category", "website", "rating", "review_count",
         "years", "hours", "facts_json"]


def db_path(root: Path) -> Path:
    return root / "data" / "businesses.db"


def _connect(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(root), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(root: Path) -> None:
    db_path(root).parent.mkdir(parents=True, exist_ok=True)
    with _connect(root) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS businesses(
            slug TEXT PRIMARY KEY, company TEXT, brand TEXT, phone TEXT, email TEXT,
            street TEXT, city TEXT, state TEXT, zip TEXT, place TEXT,
            niche TEXT, category TEXT, website TEXT,
            rating REAL, review_count INTEGER, years INTEGER, hours TEXT,
            facts_json TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_biz_niche ON businesses(niche)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_biz_city ON businesses(city)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_biz_company ON businesses(company)")


def _effective_niche(rec: dict) -> str:
    """Own niche first, then inferred from category, then company name."""
    own = (rec.get("niche") or "").strip()
    if own:
        return own
    try:
        from .bizcsv import niche_from_category
    except Exception:
        return ""
    return niche_from_category(rec.get("category") or "") or \
        niche_from_category(rec.get("company") or rec.get("brand") or "")


def _num(v, kind):
    return v if isinstance(v, kind) else None


def row_from_record(rec: dict) -> dict:
    addr = rec.get("address") or {}
    facts = rec.get("facts") or {}
    city = (addr.get("city") or "").strip()
    state = (addr.get("state") or "").strip()
    return {
        "slug": rec.get("slug") or "",
        "company": rec.get("company") or rec.get("brand") or rec.get("slug") or "",
        "brand": rec.get("brand") or "",
        "phone": rec.get("phone") or "",
        "email": rec.get("email") or "",
        "street": addr.get("street") or "",
        "city": city, "state": state, "zip": addr.get("zip") or "",
        "place": ", ".join([x for x in (city, state) if x]),
        "niche": _effective_niche(rec),
        "category": rec.get("category") or "",
        "website": rec.get("website") or "",
        "rating": _num(facts.get("rating"), (int, float)),
        "review_count": _num(facts.get("review_count"), int),
        "years": _num(facts.get("years_in_business"), int),
        "hours": facts.get("hours") or "",
        "facts_json": json.dumps(facts, ensure_ascii=False),
    }


def upsert(root: Path, rec: dict) -> None:
    init_db(root)
    row = row_from_record(rec)
    if not row["slug"]:
        return
    cols = ",".join(row.keys())
    ph = ",".join("?" * len(row))
    upd = ",".join(f"{k}=excluded.{k}" for k in row if k != "slug")
    with _WRITE_LOCK, _connect(root) as c:
        c.execute(f"INSERT INTO businesses({cols}) VALUES({ph}) "
                  f"ON CONFLICT(slug) DO UPDATE SET {upd}", list(row.values()))


def upsert_many(root: Path, recs) -> int:
    init_db(root)
    rows = [row_from_record(r) for r in recs]
    rows = [r for r in rows if r["slug"]]
    if not rows:
        return 0
    cols = ",".join(_COLS)
    ph = ",".join("?" * len(_COLS))
    upd = ",".join(f"{k}=excluded.{k}" for k in _COLS if k != "slug")
    with _WRITE_LOCK, _connect(root) as c:
        c.executemany(f"INSERT INTO businesses({cols}) VALUES({ph}) "
                      f"ON CONFLICT(slug) DO UPDATE SET {upd}",
                      [[r[k] for k in _COLS] for r in rows])
    return len(rows)


def delete(root: Path, slug: str) -> None:
    if not db_path(root).exists():
        return
    with _WRITE_LOCK, _connect(root) as c:
        c.execute("DELETE FROM businesses WHERE slug=?", (slug,))


def count(root: Path, niche: str | None = None) -> int:
    if not db_path(root).exists():
        return 0
    with _connect(root) as c:
        if niche:
            return c.execute("SELECT COUNT(*) FROM businesses WHERE niche=?", (niche,)).fetchone()[0]
        return c.execute("SELECT COUNT(*) FROM businesses").fetchone()[0]


def _where(q: str, niche: str | None):
    clauses, args = [], []
    if niche:
        clauses.append("niche=?")
        args.append(niche)
    for tok in (q or "").lower().split():
        clauses.append("(lower(company) LIKE ? OR lower(place) LIKE ?)")
        args += [f"%{tok}%", f"%{tok}%"]
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


def search(root: Path, q: str = "", niche: str | None = None,
           limit: int = 30, offset: int = 0) -> tuple[int, list[dict]]:
    """A page of matches (slug/company/place/niche) plus the total match count,
    for the panel's type-ahead pickers and 'load next 500'."""
    if not db_path(root).exists():
        return 0, []
    wc, args = _where(q, niche)
    with _connect(root) as c:
        total = c.execute(f"SELECT COUNT(*) FROM businesses{wc}", args).fetchone()[0]
        rows = c.execute(
            f"SELECT slug,company,place,niche FROM businesses{wc} "
            f"ORDER BY company LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
    return total, [dict(r) for r in rows]


def get(root: Path, slug: str) -> dict | None:
    if not db_path(root).exists():
        return None
    with _connect(root) as c:
        r = c.execute("SELECT * FROM businesses WHERE slug=?", (slug,)).fetchone()
    return dict(r) if r else None


def providers_for(root: Path, niche: str | None = None,
                  slugs=None, limit: int | None = None) -> list[dict]:
    """Full rows for the directory build — filtered by niche and/or an explicit
    slug set, highest-rated first. This replaces scanning every YAML file."""
    if not db_path(root).exists():
        return []
    clauses, args = [], []
    if niche:
        clauses.append("niche=?")
        args.append(niche)
    if slugs is not None:
        slugs = list(slugs)
        if not slugs:
            return []
        clauses.append(f"slug IN ({','.join('?' * len(slugs))})")
        args += slugs
    wc = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    lim = f" LIMIT {int(limit)}" if limit else ""
    with _connect(root) as c:
        rows = c.execute(
            f"SELECT * FROM businesses{wc} "
            f"ORDER BY (rating IS NULL), rating DESC, company{lim}", args).fetchall()
    return [dict(r) for r in rows]


def is_empty(root: Path) -> bool:
    return count(root) == 0


def rebuild_from_yaml(root: Path) -> int:
    """(Re)build the whole DB from the YAML files — the one-time migration, also
    usable any time to resync. Idempotent."""
    import yaml
    init_db(root)
    folder = root / "data" / "businesses"
    recs = []
    if folder.is_dir():
        for p in folder.glob("*.yaml"):
            try:
                rec = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            rec.setdefault("slug", p.stem)
            recs.append(rec)
    with _WRITE_LOCK, _connect(root) as c:
        c.execute("DELETE FROM businesses")
    upsert_many(root, recs)
    return count(root)


def ensure_ready(root: Path) -> None:
    """Make sure the DB exists and is populated. On the first call after a deploy
    the table is empty but YAML files exist — migrate them once."""
    init_db(root)
    try:
        if is_empty(root) and (root / "data" / "businesses").is_dir() and \
           any((root / "data" / "businesses").glob("*.yaml")):
            rebuild_from_yaml(root)
    except Exception:
        pass
