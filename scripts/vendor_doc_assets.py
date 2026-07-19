"""Vendor the KaTeX + markdown-it runtime for the self-contained document
exporter (`mcp__bran_docs__save_document`).

`save_document` writes standalone HTML files the user opens in Chrome and prints
to PDF. "Standalone" means no CDN and no network: the KaTeX stylesheet has its
woff2 fonts inlined as data: URIs, and the JS is embedded verbatim. This script
regenerates the four vendored files from the frontend's node_modules so they can
be committed and inlined at generation time.

Run after `npm install` in frontend/ (or when bumping KaTeX/markdown-it):

    python scripts/vendor_doc_assets.py

Output: src/bran/web/assets/doc/{katex.inlined.css, katex.min.js,
auto-render.min.js, markdown-it.min.js}
"""

from __future__ import annotations

import base64
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE_MODULES = ROOT / "frontend" / "node_modules"
KATEX = NODE_MODULES / "katex" / "dist"
MARKDOWN_IT = NODE_MODULES / "markdown-it" / "dist"
OUT = ROOT / "src" / "bran" / "web" / "assets" / "doc"


def inline_katex_css() -> str:
    """Return katex.min.css with every woff2 font swapped for a data: URI and
    the woff/ttf fallbacks stripped (modern Chrome reads woff2)."""
    css = (KATEX / "katex.min.css").read_text(encoding="utf-8")
    fonts_dir = KATEX / "fonts"

    def to_data_uri(woff2: Path) -> str:
        b64 = base64.b64encode(woff2.read_bytes()).decode("ascii")
        return f"url(data:font/woff2;base64,{b64}) format(\"woff2\")"

    # Replace the woff2 reference for each font file with its data URI.
    for woff2 in fonts_dir.glob("*.woff2"):
        css = css.replace(
            f'url(fonts/{woff2.name}) format("woff2")', to_data_uri(woff2)
        )
    # Drop the now-redundant woff/ttf fallback sources so the file isn't bloated
    # by references we didn't inline.
    css = re.sub(r',url\(fonts/[^)]+\) format\("(?:woff|truetype)"\)', "", css)
    return css


def main() -> None:
    if not KATEX.exists() or not MARKDOWN_IT.exists():
        raise SystemExit(
            f"node_modules not found under {NODE_MODULES}. "
            "Run `npm install` in frontend/ first."
        )
    OUT.mkdir(parents=True, exist_ok=True)

    # We mirror the app's markdown.ts pipeline (mask math → markdown-it →
    # katex.renderToString) rather than KaTeX auto-render, so only these three
    # runtime files are needed.
    (OUT / "katex.inlined.css").write_text(inline_katex_css(), encoding="utf-8")
    shutil.copyfile(KATEX / "katex.min.js", OUT / "katex.min.js")
    shutil.copyfile(MARKDOWN_IT / "markdown-it.min.js", OUT / "markdown-it.min.js")

    total = sum(p.stat().st_size for p in OUT.iterdir())
    print(f"Vendored doc assets → {OUT} ({total:,} bytes total)")
    for p in sorted(OUT.iterdir()):
        print(f"  {p.name}  {p.stat().st_size:,} B")


if __name__ == "__main__":
    main()
