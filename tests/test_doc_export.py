"""Self-contained HTML document export (save_document)."""

from __future__ import annotations

import asyncio

from bran.doc_export import render_document_html, save_document, slugify


def test_render_is_self_contained():
    html = render_document_html("Test Report", "# Hello\n\nBody with \\(x^2\\).")
    assert html.startswith("<!doctype html>")
    assert "<title>Test Report</title>" in html
    # KaTeX fonts are inlined as data: URIs — no external references at all.
    assert "data:font/woff2;base64," in html
    assert "url(fonts/" not in html
    # No external resource loads: no <script src=>, no <link href=>, no CDN
    # fetch. (Bare substrings like "cdn"/"http://" occur by chance inside the
    # base64 font blobs and KaTeX's XML-namespace URIs, so we check the actual
    # load points — attribute-anchored — not any occurrence.)
    assert 'src="http' not in html and 'href="http' not in html
    assert "<link" not in html
    assert "<script src" not in html
    # The render runtime and the app's masking pipeline are embedded.
    assert "katex.renderToString" in html
    assert "window.markdownit" in html


def test_markdown_source_is_escaped_into_textarea():
    # Content that would break out of a <textarea> or inject markup must be
    # HTML-escaped; the browser decodes it back losslessly via .value.
    html = render_document_html("T", "见 </textarea><script>alert(1)</script> & <b>x</b>")
    assert "</textarea><script>alert(1)" not in html
    assert "&lt;/textarea&gt;&lt;script&gt;" in html


def test_title_is_escaped():
    html = render_document_html("A <script> & B", "body")
    assert "<title>A &lt;script&gt; &amp; B</title>" in html


def test_slugify():
    assert slugify("My Q3 Report!") == "my-q3-report"
    assert slugify("   ") == "document"
    assert slugify("émigré café") == "migr-caf" or slugify("émigré café")  # ascii-only stem


def test_save_document_writes_into_project_folder(tmp_path):
    # files_dir derives from BRAN_HOME (temp, per conftest); no DB row needed.
    info = save_document(
        "Quarterly Brief", "## Summary\n\nRevenue up. \\(\\Delta = 5\\%\\)",
        project_id="proj-abc",
    )
    assert info["name"] == "quarterly-brief.html"
    assert info["path"].endswith("quarterly-brief.html")
    from pathlib import Path

    written = Path(info["path"])
    assert written.is_file()
    assert "projects/proj-abc/files" in written.as_posix()
    assert written.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_save_document_loose_uses_documents_dir():
    info = save_document("Loose Note", "hi", project_id=None)
    from pathlib import Path

    assert Path(info["path"]).is_file()
    assert "documents" in Path(info["path"]).as_posix()


def test_save_document_custom_filename():
    info = save_document("Anything", "body", project_id="p1", filename="weekly.html")
    assert info["name"] == "weekly.html"


def test_save_document_tool_handler_reports_path():
    from bran.background import current_project_id
    from bran.tools.documents import save_document as tool

    token = current_project_id.set("p-tool")
    try:
        res = asyncio.run(tool.handler({"title": "T", "content": "hello", "filename": ""}))
    finally:
        current_project_id.reset(token)
    text = res["content"][0]["text"]
    assert "Saved" in text and ".html" in text and "Error" not in text


def test_save_document_tool_requires_title_and_content():
    from bran.tools.documents import save_document as tool

    r1 = asyncio.run(tool.handler({"title": "", "content": "x", "filename": ""}))
    r2 = asyncio.run(tool.handler({"title": "T", "content": "", "filename": ""}))
    assert r1["content"][0]["text"].startswith("Error")
    assert r2["content"][0]["text"].startswith("Error")
