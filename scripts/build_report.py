"""Build the submission copies of report.html and slides.html.

The course requires a SELF-CONTAINED HTML file. The working copies in report/
reference screenshots from report/images/, which is convenient to edit but
breaks the moment the file is uploaded on its own. This script inlines every
local asset as a base64 data URI and writes the result to report/dist/.

    python -m scripts.build_report

Output:
    report/dist/report.html   single file, no external assets
    report/dist/slides.html   single file, reveal.js vendored inline

Submit the files in report/dist/. The copies in report/ stay editable.
"""
from __future__ import annotations

import base64
import mimetypes
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "report"
DIST = SRC / "dist"

# reveal.js is loaded from a CDN in the working copy. A self-contained deck
# must not depend on the network at grading time, so those are fetched once
# and inlined here.
CDN_TIMEOUT = 30

REPO_URL = "https://github.com/chanpaca/usedcardossier"


def inline_images(html: str, base: Path) -> tuple[str, int]:
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        src = match.group(2)
        if src.startswith(("data:", "http://", "https://")):
            return match.group(0)
        path = (base / src).resolve()
        if not path.is_file():
            print(f"  ! missing asset: {src}", file=sys.stderr)
            return match.group(0)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        count += 1
        return f'{match.group(1)}data:{mime};base64,{payload}"'

    html = re.sub(r'(<img[^>]*\ssrc=")([^"]+)"', repl, html)
    return html, count


def fetch(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "UsedCarDossier/1.0"})
        with urllib.request.urlopen(req, timeout=CDN_TIMEOUT) as r:
            return r.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"  ! could not fetch {url}: {exc}", file=sys.stderr)
        return None


def inline_cdn(html: str) -> tuple[str, int]:
    """Replace CDN <link> and <script> tags with their contents."""
    count = 0

    # The reveal.js theme carries `@import url(./fonts/.../source-sans-pro.css)`,
    # which 404s once the CSS is inlined and the relative path no longer
    # resolves. Our own rules set the typeface anyway, so a relative @import is
    # dropped rather than shipped as a guaranteed failed request.
    relative_import = re.compile(
        r"""@import\s+url\(\s*['"]?(?!https?:|data:)[^)]*\)\s*;""", re.I
    )

    def css_repl(match: re.Match[str]) -> str:
        nonlocal count
        body = fetch(match.group(1))
        if body is None:
            return match.group(0)
        body = relative_import.sub("", body)
        count += 1
        return f"<style>\n{body}\n</style>"

    def js_repl(match: re.Match[str]) -> str:
        nonlocal count
        body = fetch(match.group(1))
        if body is None:
            return match.group(0)
        count += 1
        # Guard against a stray </script> inside the library source.
        return "<script>\n" + body.replace("</script>", "<\\/script>") + "\n</script>"

    html = re.sub(
        r'<link\s+rel="stylesheet"\s+href="(https://[^"]+)"\s*/?>', css_repl, html
    )
    html = re.sub(r'<script\s+src="(https://[^"]+)"\s*>\s*</script>', js_repl, html)
    return html, count


def build(name: str, do_cdn: bool) -> None:
    src = SRC / name
    html = src.read_text(encoding="utf-8")

    html, n_img = inline_images(html, SRC)
    n_cdn = 0
    if do_cdn:
        html, n_cdn = inline_cdn(html)

    # The working copy links sideways to its sibling; in dist they sit
    # together, so the relative link still resolves.
    html = html.replace('href="../frontend/index.html"',
                        'href="%s"' % REPO_URL)

    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / name
    out.write_text(html, encoding="utf-8")
    size = out.stat().st_size / 1_000_000
    print(f"  {name:14s} {n_img} images + {n_cdn} CDN assets inlined "
          f"-> {size:.1f} MB")


def build_slides_pdf() -> None:
    """Render the deck to PDF -- Canvas asks for the slides as a PDF.

    reveal.js ships a print stylesheet that activates on `?print-pdf` and lays
    the deck out one slide per page. The page size must match the deck's
    configured 1280x760 or slides get clipped at the edges.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ! slides.pdf skipped (pip install playwright)", file=sys.stderr)
        return
    out = DIST / "slides.pdf"
    url = (SRC / "slides.html").as_uri() + "?print-pdf"
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 760})
        pg.goto(url, wait_until="networkidle")
        pg.wait_for_timeout(2500)
        pg.pdf(path=str(out), width="1280px", height="760px",
               print_background=True,
               margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        b.close()
    print(f"  {'slides.pdf':14s} rendered -> {out.stat().st_size / 1_000_000:.1f} MB")


def main() -> int:
    print(f"building self-contained copies into {DIST}")
    build("report.html", do_cdn=False)
    build("slides.html", do_cdn=True)
    build_slides_pdf()

    leftovers = []
    for name in ("report.html", "slides.html"):
        text = (DIST / name).read_text(encoding="utf-8")
        # Ignore: absolute URLs, the sibling link between the two dist files,
        # and ${...} fragments from template literals inside vendored JS.
        siblings = {"report.html", "slides.html"}
        for match in re.finditer(r'(?:src|href)="(?!data:|#|mailto:)([^"]+)"', text):
            url = match.group(1)
            if url.startswith(("http://", "https://")) or url in siblings:
                continue
            if "${" in url:
                continue
            leftovers.append(f"{name}: {url}")
    if leftovers:
        print("\n! still referencing local files:")
        for item in leftovers:
            print("   ", item)
        return 1
    print("\nboth files are self-contained (no local asset references remain)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
