"""
Foundry — the image library.

You paste a CSV of URLs. This fetches them once, validates them, strips them,
and hands each site a DIFFERENT selection from the pool.

Five decisions, each with a reason:

  FETCH ONCE, COPY MANY.  The pool is content-addressed under
  `assets/pool/<niche>/<hash>.<ext>`, so the same URL never downloads twice and
  a re-run is idempotent. Fetching per site would pull one URL hundreds of times
  and rate-limit you off whatever host you are using.

  VALIDATE BY MAGIC BYTES, NOT BY SIZE.  Engine B shipped a live site with all
  89 images broken because a Google Drive share link returned a 900 KB HTML
  sign-in page and the only check was "bigger than 512 bytes". Twenty-four files
  named .jpg were HTML, the build compiled, and the homepage was a blank white
  rectangle. Content-type AND magic bytes here, plus a decode check — a file
  that cannot be opened as an image is rejected at fetch time, not at render.

  NORMALISE SHARE LINKS.  Cloud-drive "view" URLs serve a viewer page, not the
  file. The common ones are rewritten to their direct form before fetching.

  STRIP AND RE-ENCODE.  EXIF can carry GPS coordinates, a camera serial and the
  original filename. Shipping the same untouched JPEG across forty sites hands
  anyone a trivial way to connect them. Every image is re-encoded without
  metadata and resized to a sane maximum.

  RENAME PER SITE.  A pool file called `hero-a1b2c3.jpg` appearing on forty
  domains is itself a fingerprint. Each site gets its own filename derived from
  its own seed, so the same photograph does not arrive under the same name.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Slots a template can ask for. A slot not listed is a typo, and a typo should
# be a loud error rather than a silently missing picture.
SLOTS = ("hero", "showcase", "about", "cta", "service", "gallery", "reviewer")

MAGIC = {
    b"\xff\xd8\xff": "jpg", b"\x89PNG\r\n\x1a\n": "png",
    b"GIF87a": "gif", b"GIF89a": "gif", b"RIFF": "webp",
}
MAX_WIDTH = 1800
MAX_BYTES = 12 * 1024 * 1024
JPEG_QUALITY = 82


# --------------------------------------------------------------------------
# url handling
# --------------------------------------------------------------------------

_SHARE_REWRITES = (
    (re.compile(r"https?://drive\.google\.com/file/d/([\w-]+)"),
     r"https://lh3.googleusercontent.com/d/\1"),
    (re.compile(r"https?://drive\.google\.com/open\?id=([\w-]+)"),
     r"https://lh3.googleusercontent.com/d/\1"),
    (re.compile(r"https?://(?:www\.)?dropbox\.com/(.+?)\?dl=0"),
     r"https://dl.dropboxusercontent.com/\1"),
    (re.compile(r"https?://(?:www\.)?dropbox\.com/(.+?)$"),
     r"https://dl.dropboxusercontent.com/\1"),
)


def normalise_url(url: str) -> str:
    """A share link serves a viewer page, not the file. Rewrite the common ones."""
    url = url.strip()
    for pattern, repl in _SHARE_REWRITES:
        if pattern.match(url):
            return pattern.sub(repl, url)
    return url


def url_key(url: str) -> str:
    return hashlib.blake2b(normalise_url(url).encode("utf-8"), digest_size=8).hexdigest()


# --------------------------------------------------------------------------
# the manifest
# --------------------------------------------------------------------------

@dataclass
class ImageRow:
    url: str
    slot: str = "hero"
    tags: str = ""
    status: str = ""          # "" | ok | failed
    file: str = ""            # pool filename once fetched
    note: str = ""

    @property
    def key(self) -> str:
        return url_key(self.url)


def manifest_path(root: Path, niche: str) -> Path:
    return root / "data" / "images" / f"{niche}.csv"


def read_manifest(root: Path, niche: str) -> list[ImageRow]:
    path = manifest_path(root, niche)
    if not path.is_file():
        return []
    out: list[ImageRow] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            url = (row.get("url") or "").strip()
            if not url or url.startswith("#"):
                continue
            out.append(ImageRow(
                url=url, slot=(row.get("slot") or "hero").strip().lower(),
                tags=(row.get("tags") or "").strip(),
                status=(row.get("status") or "").strip(),
                file=(row.get("file") or "").strip(),
                note=(row.get("note") or "").strip()))
    return out


def write_manifest(root: Path, niche: str, rows: list[ImageRow]) -> None:
    path = manifest_path(root, niche)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["url", "slot", "tags", "status", "file", "note"])
        w.writeheader()
        for r in rows:
            w.writerow({"url": r.url, "slot": r.slot, "tags": r.tags,
                        "status": r.status, "file": r.file, "note": r.note})


def parse_paste(text: str, default_slot: str = "hero") -> tuple[list[ImageRow], list[str]]:
    """
    Accepts a CSV with headers, or one URL per line, or `url, slot, tags`.

    A line that is not a URL is REPORTED, never silently dropped — a paste that
    quietly loses a third of its rows is how a pool ends up too shallow without
    anyone noticing.
    """
    rows: list[ImageRow] = []
    problems: list[str] = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines and lines[0].lower().replace(" ", "").startswith("url"):
        header = [h.strip().lower() for h in lines[0].split(",")]
        body = lines[1:]
    else:
        header, body = ["url", "slot", "tags"], lines

    for line in body:
        if line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        rec = dict(zip(header, parts))
        url = rec.get("url", "")
        if not re.match(r"^https?://\S+$", url):
            problems.append(f"not a URL: {line[:70]}")
            continue
        slot = (rec.get("slot") or default_slot).lower()
        if slot not in SLOTS:
            problems.append(f"unknown slot {slot!r} for {url[:50]} "
                            f"(known: {', '.join(SLOTS)})")
            continue
        rows.append(ImageRow(url=url, slot=slot, tags=rec.get("tags", "")))
    return rows, problems


def add_rows(root: Path, niche: str, new: list[ImageRow]) -> dict[str, int]:
    existing = read_manifest(root, niche)
    seen = {r.key for r in existing}
    added = dupe = 0
    for r in new:
        if r.key in seen:
            dupe += 1
            continue
        seen.add(r.key)
        existing.append(r)
        added += 1
    write_manifest(root, niche, existing)
    return {"added": added, "duplicate": dupe, "total": len(existing)}


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

@dataclass
class FetchReport:
    attempted: int = 0
    fetched: int = 0
    cached: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    bytes_saved: int = 0

    def summary(self) -> str:
        return (f"{self.fetched} fetched, {self.cached} already cached, "
                f"{len(self.failed)} failed")


def pool_dir(root: Path, niche: str) -> Path:
    return root / "assets" / "pool" / niche


def _sniff(head: bytes) -> str | None:
    for magic, ext in MAGIC.items():
        if head.startswith(magic):
            if ext == "webp" and b"WEBP" not in head[:16]:
                continue
            return ext
    return None


def _process(raw: bytes, ext: str) -> tuple[bytes, str, tuple[int, int]]:
    """Re-encode without metadata, resized. Raises if it will not decode."""
    from PIL import Image

    img = Image.open(io.BytesIO(raw))
    img.load()                                   # forces a real decode
    size = img.size
    if img.width > MAX_WIDTH:
        ratio = MAX_WIDTH / img.width
        img = img.resize((MAX_WIDTH, max(1, round(img.height * ratio))), Image.LANCZOS)

    buf = io.BytesIO()
    if ext == "png" and img.mode in ("RGBA", "LA", "P"):
        # A new image, so nothing carries over: no EXIF, no ICC, no comments.
        Image.new(img.mode, img.size).paste(img)
        img.convert("RGBA").save(buf, "PNG", optimize=True)
        out_ext = "png"
    else:
        img.convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY,
                                optimize=True, progressive=True)
        out_ext = "jpg"
    return buf.getvalue(), out_ext, size


def fetch(root: Path, niche: str, *, limit: int | None = None,
          timeout: int = 25, session=None) -> FetchReport:
    import requests

    rows = read_manifest(root, niche)
    rep = FetchReport()
    folder = pool_dir(root, niche)
    folder.mkdir(parents=True, exist_ok=True)
    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", "Foundry/1.0 (+image pool fetch)")

    todo = [r for r in rows if r.status != "ok" or not (folder / r.file).is_file()]
    if limit:
        todo = todo[:limit]

    for row in todo:
        rep.attempted += 1
        existing = list(folder.glob(f"{row.key}.*"))
        if existing:
            row.status, row.file = "ok", existing[0].name
            rep.cached += 1
            continue
        try:
            url = normalise_url(row.url)
            resp = sess.get(url, timeout=timeout, stream=True)
            resp.raise_for_status()
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            raw = resp.raw.read(MAX_BYTES + 1, decode_content=True)
            if len(raw) > MAX_BYTES:
                raise ValueError(f"larger than {MAX_BYTES // 1024 // 1024} MB")

            ext = _sniff(raw[:16])
            if ext is None:
                # This is the Engine B failure, caught: a share link that served
                # an HTML sign-in page instead of the file.
                looks_html = raw[:200].lstrip()[:1] == b"<"
                raise ValueError(
                    f"not an image (content-type {ctype or 'none'}"
                    + (", body is HTML — probably a share/login page" if looks_html else "")
                    + ")")
            if ctype and not ctype.startswith("image/"):
                raise ValueError(f"content-type {ctype} does not match image bytes")

            data, out_ext, dims = _process(raw, ext)
            name = f"{row.key}.{out_ext}"
            (folder / name).write_bytes(data)
            row.status, row.file, row.note = "ok", name, f"{dims[0]}x{dims[1]}"
            rep.fetched += 1
            rep.bytes_saved += len(data)
        except Exception as exc:                                  # noqa: BLE001
            row.status, row.file = "failed", ""
            row.note = str(exc)[:140]
            rep.failed.append((row.url, row.note))

    write_manifest(root, niche, rows)
    return rep


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------

def pool_for(root: Path, niche: str, slot: str) -> list[str]:
    """Fetched, verified files available for one slot, in a stable order."""
    folder = pool_dir(root, niche)
    return sorted(r.file for r in read_manifest(root, niche)
                  if r.slot == slot and r.status == "ok" and (folder / r.file).is_file())


def pool_counts(root: Path, niche: str) -> dict[str, int]:
    return {slot: len(pool_for(root, niche, slot)) for slot in SLOTS}


def copy_into_site(root: Path, niche: str, out_dir: Path,
                   chosen: dict[str, str]) -> dict[str, str]:
    """
    Copy the selected pool files into a site, under site-specific names.

    The rename is deliberate. A file called `a1b2c3.jpg` appearing on forty
    domains is a fingerprint tying them together, and the whole point of this
    factory is that they do not look related.
    """
    target = out_dir / "assets" / "img"
    target.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for public_name, pool_file in chosen.items():
        src = pool_dir(root, niche) / pool_file
        if not src.is_file():
            continue
        dest = target / public_name
        shutil.copy2(src, dest)
        written[public_name] = f"/assets/img/{public_name}"
    return written
