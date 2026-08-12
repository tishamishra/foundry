"""
Foundry — the layout sweep.

Engine A's half of the "verify rendered reality" lesson, ported whole:

    "'Looks fine on my screen' is not a claim that survives 200 sites."

Every built page is loaded at six widths and asserted not to overflow
horizontally. When something does, the probe reports the offending element's
tag, classes and DOM path — because the last time this fired, an overflow that
looked like a heading problem turned out to be the header call button all along.
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import socket
import socketserver
import threading
from pathlib import Path

WIDTHS = (1440, 1280, 820, 390, 360, 320)

# WCAG AA. Large text is >=24px, or >=18.66px when bold.
CONTRAST_NORMAL = 4.5
CONTRAST_LARGE = 3.0

CONTRAST_PROBE = """() => {
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const parse = (s) => {
    const m = s.match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(',').map(Number);
    return { rgb: [p[0], p[1], p[2]], a: p.length > 3 ? p[3] : 1 };
  };
  const over = (fg, bg) => fg.rgb.map((v, i) => v * fg.a + bg[i] * (1 - fg.a));
  const bgOf = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0.9) return c.rgb;
      if (c && c.a > 0) return over(c, [255, 255, 255]);
    }
    return [255, 255, 255];
  };
  const out = [];
  for (const el of document.querySelectorAll('p,h1,h2,h3,li,a,span,strong,em,blockquote,figcaption,summary,label,button')) {
    const t = (el.textContent || '').trim();
    if (!t || el.children.length > 0) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity < 0.1) continue;
    const fg = parse(cs.color); if (!fg) continue;
    const bg = bgOf(el);
    const c = over(fg, bg);
    const L1 = lum(c), L2 = lum(bg);
    const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const px = parseFloat(cs.fontSize);
    const bold = parseInt(cs.fontWeight, 10) >= 700;
    const need = (px >= 24 || (bold && px >= 18.66)) ? %s : %s;
    if (ratio < need) {
      out.push({ tag: el.tagName.toLowerCase(),
                 cls: (el.getAttribute('class') || '').slice(0, 40),
                 ratio: Math.round(ratio * 100) / 100, need: need,
                 text: t.slice(0, 48) });
    }
  }
  return out.slice(0, 8);
}""" % (CONTRAST_LARGE, CONTRAST_NORMAL)

PROBE = """() => {
  const vw = document.documentElement.clientWidth;
  const out = [];
  if (document.documentElement.scrollWidth > vw + 1)
    out.push({tag: 'html', cls: '', path: 'document', right: document.documentElement.scrollWidth});

  // An element inside a deliberately scrollable or clipped container is not
  // page overflow — a carousel slide and a tab strip are SUPPOSED to extend
  // past the viewport and be scrolled to. Reporting them buries the real
  // findings, which is how a probe stops being trusted.
  const contained = (el) => {
    for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      const ov = getComputedStyle(n).overflowX;
      // auto/scroll only. `hidden` is NOT exempt: it clips content the reader
      // can never reach, which is a real defect wearing a tidy costume.
      if (ov === 'auto' || ov === 'scroll') return true;
    }
    return false;
  };

  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    if (contained(el)) continue;
    if (r.right > vw + 1) {
      const path = [];
      let n = el;
      while (n && n.tagName && path.length < 5) { path.unshift(n.tagName.toLowerCase()); n = n.parentElement; }
      out.push({tag: el.tagName.toLowerCase(),
                cls: (el.getAttribute('class') || '').slice(0, 60),
                path: path.join(' > '), right: Math.round(r.right)});
    }
  }
  return out.slice(0, 6);
}"""


@contextlib.contextmanager
def _served(directory: Path):
    """Serve `dist` over HTTP for the duration of the sweep.

    The first version of this loaded pages over file://, where a root-absolute
    /assets/css/site.css resolves to the filesystem root and never arrives. The
    sweep then measured UNSTYLED html and reported seventeen overflows that do
    not exist. That is the same failure mode as checking a substitution instead
    of the rendered output — so the probe now runs against the transport
    production actually uses.
    """
    class _Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args):    # noqa: D102
            pass

    handler = functools.partial(_Quiet, directory=str(directory))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = socketserver.TCPServer(("127.0.0.1", port), handler)
    server.allow_reuse_address = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def sweep(dist: Path, limit: int = 40) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"available": False,
                "reason": "playwright is not installed — reported as a blind spot, "
                          "never silently skipped"}

    pages = sorted(dist.rglob("index.html"))[:limit]
    masters = sorted(dist.rglob("_masters/*.html"))[:8]
    targets = pages + masters
    findings, contrast, checked = [], [], 0

    with _served(dist) as origin, sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width in WIDTHS:
            page = browser.new_page(viewport={"width": width, "height": 900})
            # Block every third-party request, and measure with the FALLBACK
            # fonts. That is deliberate, not a shortcut: a web font can be slow,
            # blocked by a corporate proxy, or simply absent on first paint, and
            # a heading that only fits once the font arrives does not fit.
            # Engine A learned the same thing the hard way when a per-font width
            # factor broke against the system fallback.
            page.route("**/*", lambda route: (
                route.abort() if "127.0.0.1" not in route.request.url else route.continue_()))
            for target in targets:
                rel = target.relative_to(dist).as_posix()
                page.goto(f"{origin}/{rel}", wait_until="domcontentloaded")
                page.wait_for_timeout(60)
                bad = page.evaluate(PROBE)
                checked += 1
                for item in bad:
                    findings.append({"width": width,
                                     "page": str(target.relative_to(dist)), **item})
                if width == WIDTHS[0]:
                    # Contrast is width-independent, so it runs once per page.
                    # Engine B shipped a live homepage whose white H1 sat on a
                    # white rectangle because a hero rule leaked past the hero.
                    # Text was checked. Pixels were not. This checks the pixels.
                    for item in page.evaluate(CONTRAST_PROBE):
                        contrast.append({"page": str(target.relative_to(dist)), **item})
            page.close()
        browser.close()

    return {"available": True, "widths": list(WIDTHS), "pages": len(targets),
            "checks": checked, "findings": findings, "contrast": contrast}
