"""
Foundry — the local preview server.

TWO THINGS THIS FIXES, both of which made a perfectly good site look broken:

1. EVERY SITE IS ITS OWN DOCUMENT ROOT.
   A page asks for `/assets/css/site.css` — root-relative, because in production
   it lives at the root of its own domain. Serving the whole `dist/` folder and
   browsing to `/bennettroofers.com/` therefore looked for
   `dist/assets/css/site.css`, which does not exist. Result: 404 on the
   stylesheet and every image, so every page rendered as unstyled HTML.

   This is the SAME defect the layout sweep had, found and fixed there, and
   then left standing in the one place a human actually looks. Fixing a bug in
   the checker and not in the viewer is worse than not finding it.

   So: one server per site, each rooted at that site's own folder, exactly like
   production. Plus a landing page that links to them.

2. HYBRID SITES ARE FULLY BROWSABLE LOCALLY.
   In hybrid mode most city pages are rendered at the edge by worker.js rather
   than written to disk. Locally they would 404, and a preview where two thirds
   of the links are dead reads as a broken site.

   This server implements the Worker's exact logic — static first, then master
   substitution — so local preview matches what Cloudflare will serve.
"""

from __future__ import annotations

import http.server
import json
import socket
import socketserver
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

SENTINELS = ("city", "state", "state_abbr", "county", "zips_short", "nearby")


def free_port(start: int = 8000, tries: int = 200) -> int:
    for port in range(start, start + tries):
        if port == 5000:          # macOS AirPlay Receiver answers 403 to everything
            continue
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port found")


class SiteHandler(http.server.SimpleHTTPRequestHandler):
    """Serves one site. Falls back to edge rendering exactly like worker.js."""

    site_root: Path = Path(".")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.site_root), **kwargs)

    def log_message(self, *_args):        # keep the terminal readable
        pass

    # -- the edge fallback -------------------------------------------------

    def _index(self) -> dict:
        cache = getattr(type(self), "_edge_index", None)
        if cache is None:
            path = self.site_root / "_masters" / "index.json"
            cache = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            type(self)._edge_index = cache
        return cache

    def _edge_page(self, url_path: str) -> bytes | None:
        idx = self._index()
        locations = idx.get("locations") or {}
        parts = [p for p in unquote(url_path).strip("/").split("/") if p]

        master = slug = None
        if len(parts) == 2 and parts[0] == "areas" and parts[1] in locations:
            slug = parts[1]
            master = f"location-{locations[slug][6]}"
        elif (len(parts) == 3 and parts[0] == "services"
              and parts[1] in (idx.get("services") or []) and parts[2] in locations):
            slug = parts[2]
            master = f"location_service-{locations[slug][6]}-{parts[1]}"
        if not master:
            return None

        source = self.site_root / "_masters" / f"{master}.html"
        if not source.is_file():
            return None

        row = locations[slug]
        html = source.read_text(encoding="utf-8")
        for i, name in enumerate(SENTINELS):
            html = html.replace(f"%%{name}%%", row[i])
        html = html.replace("%%slug%%", slug)
        html = html.replace("%%city_slug%%", slug.rsplit("-", 1)[0])
        return html.encode("utf-8")

    def do_GET(self):                      # noqa: N802
        path = urlparse(self.path).path
        target = self.translate_path(self.path)
        target_path = Path(target)
        exists = target_path.is_file() or (target_path / "index.html").is_file()

        if not exists:
            body = self._edge_page(path)
            if body is not None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Foundry-Render", "edge")
                self.end_headers()
                self.wfile.write(body)
                return
        super().do_GET()


class PreviewPool:
    """Lazily starts one server per site and remembers the port.

    The admin panel links OUT to these rather than proxying, because a site
    served under /preview/<id>/ would ask for /assets/... and 404 exactly as
    `dist/` did. One document root per site is the only arrangement that
    matches production, so it is the only one worth previewing.
    """

    def __init__(self, dist: Path, start: int = 8400):
        self.dist = dist
        self._next = start
        self._ports: dict[str, int] = {}
        self._servers: dict[str, socketserver.BaseServer] = {}

    def url_for(self, name: str) -> str | None:
        site = self.dist / name
        if not (site / "index.html").is_file():
            return None
        if name in self._ports:
            return f"http://127.0.0.1:{self._ports[name]}/"
        port = free_port(self._next)
        self._next = port + 1
        handler = type(f"Handler_{name}", (SiteHandler,),
                       {"site_root": site, "_edge_index": None})
        server = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._ports[name] = port
        self._servers[name] = server
        return f"http://127.0.0.1:{port}/"

    def drop(self, name: str) -> None:
        """Forget a site after a rebuild, so the next preview re-reads it."""
        server = self._servers.pop(name, None)
        self._ports.pop(name, None)
        if server:
            server.shutdown()
            server.server_close()

    def drop_all(self) -> None:
        for name in list(self._servers):
            self.drop(name)


def _landing(sites: list[tuple[str, int]]) -> str:
    rows = "".join(
        f'<a class="site" href="http://127.0.0.1:{port}/">'
        f'<span class="name">{name}</span>'
        f'<span class="port">127.0.0.1:{port}</span></a>'
        for name, port in sites)
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>Foundry — local preview</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{{font:16px/1.6 system-ui,-apple-system,sans-serif;margin:0;background:#f6f8fb;color:#0f172a}}
 .wrap{{width:min(46rem,100% - 2.5rem);margin:4rem auto}}
 h1{{font-size:1.6rem;margin:0 0 .3rem}}
 p.sub{{color:#5b6472;margin:0 0 2rem}}
 .site{{display:flex;justify-content:space-between;align-items:center;gap:1rem;
   background:#fff;border:1px solid #dfe3ea;border-radius:12px;padding:1rem 1.2rem;
   margin-bottom:.7rem;text-decoration:none;color:inherit}}
 .site:hover{{border-color:#2563eb}}
 .name{{font-weight:650}}
 .port{{color:#5b6472;font-size:.85rem;font-family:ui-monospace,monospace}}
 .note{{margin-top:2rem;color:#5b6472;font-size:.9rem}}
</style>
<div class="wrap">
<h1>Foundry — local preview</h1>
<p class="sub">Each site runs on its own port, because each one is its own domain root in production.</p>
{rows}
<p class="note">City pages that were not pre-rendered are produced on the fly by the
same logic <code>worker.js</code> uses at the edge, so what you see here is what
Cloudflare will serve. Press Control-C in the terminal to stop.</p>
</div></html>"""


def serve(dist: Path, base_port: int | None = None) -> None:
    site_dirs = sorted(d for d in dist.iterdir()
                       if d.is_dir() and (d / "index.html").is_file())
    if not site_dirs:
        raise RuntimeError("nothing built yet — run `foundry build` first")

    base = free_port(base_port or 8000)
    servers: list[socketserver.BaseServer] = []
    listed: list[tuple[str, int]] = []
    next_port = base + 1

    for site in site_dirs:
        port = free_port(next_port)
        next_port = port + 1
        handler = type(f"Handler_{site.name}", (SiteHandler,),
                       {"site_root": site, "_edge_index": None})
        server = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        listed.append((site.name, port))

    page = _landing(listed).encode("utf-8")

    class Landing(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_GET(self):                  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

    index_server = socketserver.ThreadingTCPServer(("127.0.0.1", base), Landing)
    index_server.daemon_threads = True

    print(f"\n  index      http://127.0.0.1:{base}/")
    for name, port in listed:
        print(f"  {name:<26} http://127.0.0.1:{port}/")
    print("\n  Control-C to stop.\n")

    try:
        index_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        index_server.server_close()
        for server in servers:
            server.shutdown()
            server.server_close()

    return base
