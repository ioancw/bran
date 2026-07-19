"""Self-contained HTML document export (the `save_document` tool's engine).

Turns an agent's markdown (with `\\(...\\)` / `$$...$$` math, exactly the
notation the agents already use for the chat UI) into ONE standalone .html file
the user opens in Chrome and prints to PDF. Everything is inlined — KaTeX CSS
with its woff2 fonts as data: URIs, katex.min.js, markdown-it.min.js — so the
file renders identically forever, offline, and survives being emailed or
archived. No CDN, no network.

The in-browser render step mirrors the app's `markdown.ts` pipeline (mask code,
pull math out before markdown-it runs, restore it with katex.renderToString) so
a document reads the same as the chat reply it came from.

Vendored assets live in web/assets/doc/ — regenerate with
`python scripts/vendor_doc_assets.py`.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from bran.config import SETTINGS

_ASSETS = Path(__file__).parent / "web" / "assets" / "doc"


class DocAssetsMissing(RuntimeError):
    """The vendored KaTeX/markdown-it runtime isn't present."""


@lru_cache(maxsize=1)
def _asset_bundle() -> tuple[str, str, str]:
    """(katex_css, katex_js, markdownit_js), read once and cached."""
    try:
        return (
            (_ASSETS / "katex.inlined.css").read_text(encoding="utf-8"),
            (_ASSETS / "katex.min.js").read_text(encoding="utf-8"),
            (_ASSETS / "markdown-it.min.js").read_text(encoding="utf-8"),
        )
    except OSError as e:
        raise DocAssetsMissing(
            "Document runtime not vendored — run `python scripts/vendor_doc_assets.py`."
        ) from e


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slugify(title: str, fallback: str = "document") -> str:
    """A safe, readable filename stem from a title (no extension)."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")
    return slug[:80] or fallback


# Editorial + print stylesheet. Kept literal (no f-string) so CSS braces need no
# escaping. `@page` + page-break rules make Chrome's "Save as PDF" produce a
# clean, well-margined document rather than a screenshot of a web page.
_DOC_CSS = """
:root { --ink: #1a1a1a; --muted: #666; --rule: #ddd; --accent: #7c3a2d; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  margin: 0; color: var(--ink); background: #fff;
  font-family: Georgia, "Times New Roman", serif;
  font-size: 16px; line-height: 1.6;
}
.page {
  max-width: 46rem; margin: 0 auto; padding: 3rem 2.5rem 4rem;
}
header.doc-head { margin-bottom: 2rem; border-bottom: 2px solid var(--accent); padding-bottom: 1rem; }
header.doc-head h1 { font-size: 2rem; line-height: 1.2; margin: 0 0 .3rem; }
header.doc-head .meta { color: var(--muted); font-size: .85rem; font-family: -apple-system, system-ui, sans-serif; }
h1, h2, h3, h4 { line-height: 1.25; margin: 1.8em 0 .6em; font-weight: 700; }
h2 { font-size: 1.5rem; border-bottom: 1px solid var(--rule); padding-bottom: .2rem; }
h3 { font-size: 1.2rem; }
p { margin: 0 0 1em; }
a { color: var(--accent); }
ul, ol { margin: 0 0 1em 1.4em; padding: 0; }
li { margin: .25em 0; }
blockquote { margin: 1em 0; padding: .3em 1.1em; border-left: 3px solid var(--accent); color: var(--muted); font-style: italic; }
hr { border: 0; border-top: 1px solid var(--rule); margin: 2em 0; }
code { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: .88em; background: #f4f2ef; padding: .1em .35em; border-radius: 3px; }
pre { background: #f7f5f2; border: 1px solid var(--rule); border-radius: 6px; padding: 1em; overflow-x: auto; margin: 0 0 1em; }
pre code { background: none; padding: 0; font-size: .82em; line-height: 1.5; }
table { border-collapse: collapse; width: 100%; margin: 0 0 1em; font-size: .92em; }
th, td { border: 1px solid var(--rule); padding: .45em .7em; text-align: left; }
th { background: #f4f2ef; font-family: -apple-system, system-ui, sans-serif; }
img { max-width: 100%; }
.katex-display { margin: 1.2em 0; overflow-x: auto; overflow-y: hidden; }

@page { margin: 18mm 16mm; }
@media print {
  .page { max-width: none; margin: 0; padding: 0; }
  h1, h2, h3, h4 { break-after: avoid; }
  pre, blockquote, table, .katex-display { break-inside: avoid; }
  a { color: var(--ink); text-decoration: none; }
}
"""

# The in-browser renderer — a compact port of markdown.ts. `¤` (U+00A4) matches
# the app's placeholder sentinel.
_RENDER_JS = r"""
(function () {
  var md = window.markdownit({ html: false, linkify: true, breaks: false });
  var text = document.getElementById('bran-src').value;

  var code = [];
  function maskCode(re) {
    text = text.replace(re, function (m) { code.push(m); return '¤¤C_' + (code.length - 1) + '¤¤'; });
  }
  maskCode(/^([`~]{3,}).*\n[\s\S]*?(?:^\1[`~]*[ \t]*$|(?![\s\S]))/gm);
  maskCode(/(`+)(?!`)[\s\S]*?\1/g);

  var math = [];
  function extract(re, display) {
    text = text.replace(re, function (_m, c) { math.push({ c: c, d: display }); return '¤¤M_' + (math.length - 1) + '¤¤'; });
  }
  extract(/\\\[([\s\S]+?)\\\]/g, true);
  extract(/\$\$([\s\S]+?)\$\$/g, true);
  extract(/\\\(([\s\S]+?)\\\)/g, false);

  text = text.replace(/¤¤C_(\d+)¤¤/g, function (_m, i) { return code[+i] || ''; });
  var html = md.render(text);
  html = html.replace(/¤¤M_(\d+)¤¤/g, function (_m, i) {
    var b = math[+i]; if (!b) return '';
    try { return katex.renderToString(b.c, { displayMode: b.d, throwOnError: false }); }
    catch (e) { return md.utils.escapeHtml(b.c); }
  });
  document.getElementById('bran-doc').innerHTML = html;
})();
"""


def render_document_html(title: str, markdown_source: str, *, subtitle: str = "") -> str:
    """Assemble a complete, self-contained HTML document string."""
    katex_css, katex_js, markdownit_js = _asset_bundle()
    meta = _html_escape(subtitle) if subtitle else "Generated by bran · print to PDF from your browser"
    return "".join([
        "<!doctype html>\n<html lang=\"en\">\n<head>\n",
        "<meta charset=\"utf-8\">\n",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n",
        f"<title>{_html_escape(title)}</title>\n",
        "<style>", katex_css, "</style>\n",
        "<style>", _DOC_CSS, "</style>\n",
        "</head>\n<body>\n",
        "<div class=\"page\">\n",
        "<header class=\"doc-head\">\n",
        f"<h1>{_html_escape(title)}</h1>\n",
        f"<div class=\"meta\">{meta}</div>\n",
        "</header>\n",
        "<div id=\"bran-doc\"></div>\n",
        "</div>\n",
        # Markdown source lives in a hidden <textarea>: HTML-escaping makes a
        # premature </textarea> impossible, and .value decodes the entities
        # back to the exact original text (lossless, unlike a <script> block
        # which would leak an escaping backslash into code samples).
        "<textarea id=\"bran-src\" hidden>", _html_escape(markdown_source), "</textarea>\n",
        "<script>", katex_js, "</script>\n",
        "<script>", markdownit_js, "</script>\n",
        "<script>", _RENDER_JS, "</script>\n",
        "</body>\n</html>\n",
    ])


def documents_dir() -> Path:
    """Fallback output folder for documents created outside any project
    (e.g. a loose chat). Under bran_home, so it's a sanctioned write root."""
    d = SETTINGS.bran_home / "documents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_document(
    title: str, markdown_source: str, *, project_id: str | None = None,
    filename: str | None = None, subtitle: str = "",
) -> dict[str, Any]:
    """Render and write a document, returning {name, path, size, dir}.

    Writes into the project's managed files folder when `project_id` is given
    (so it sits alongside uploads and shows in the Files list), else into
    bran_home/documents/.
    """
    from bran.project_files import _safe_name, files_dir

    html = render_document_html(title, markdown_source, subtitle=subtitle)

    stem = slugify(Path(filename).stem if filename else title)
    name = _safe_name(f"{stem}.html")
    dest_dir = files_dir(project_id, create=True) if project_id else documents_dir()
    dest = dest_dir / name
    dest.write_text(html, encoding="utf-8")
    return {
        "name": name,
        "path": str(dest),
        "size": dest.stat().st_size,
        "dir": str(dest_dir),
    }
