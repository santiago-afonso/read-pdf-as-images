#!/usr/bin/env python
from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import fitz  # PyMuPDF


def xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def collect_page_chunks(pdf_path: Path):
    # Capture chunk-level details including words/images/tables.
    # extract_words=True to get a reliable word count; page_chunks=True for per-page data.
    buf = io.StringIO()
    with redirect_stdout(buf):
        import pymupdf4llm  # type: ignore[import-not-found]

        chunks = pymupdf4llm.to_markdown(
            str(pdf_path),
            page_chunks=True,
            extract_words=True,
        )
    pages = []
    for chunk in chunks:
        metadata = chunk.get("metadata") or {}
        page_no = metadata.get("page")
        if page_no is None:
            continue
        text = chunk.get("text") or ""
        words = chunk.get("words") or []
        images = chunk.get("images") or []
        tables = chunk.get("tables") or []
        pages.append(
            {
                "index": page_no,
                "has_text": bool(text.strip()) or bool(words),
                "word_count": len(words),
                "image_count": len(images),
                "table_count": len(tables),
            }
        )
    return pages


def collect_bookmarks_and_links(pdf_path: Path):
    doc = fitz.open(pdf_path)

    # Bookmarks (table of contents)
    bookmarks = []
    for level, title, page, *_rest in doc.get_toc(simple=False):
        bookmarks.append(
            {
                "title": title or "",
                "page": page,  # already 1-based
                "level": level,
            }
        )

    # Links (internal/external)
    links = []
    for page_index, page in enumerate(doc, start=1):
        for link in page.get_links():
            entry = {"from_page": page_index}
            uri = link.get("uri")
            dest_page = link.get("page")
            if uri:
                entry["type"] = "external"
                entry["uri"] = uri
            elif dest_page is not None:
                entry["type"] = "internal"
                entry["to_page"] = int(dest_page) + 1  # convert 0-based to 1-based
            else:
                continue
            links.append(entry)

    return bookmarks, links


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect page-level structure and document-level bookmarks/links as JSON."
    )
    parser.add_argument("pdf", help="Path to the PDF.")
    args = parser.parse_args(argv)

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    pages = collect_page_chunks(pdf_path)
    bookmarks, links = collect_bookmarks_and_links(pdf_path)

    json.dump(
        {
            "pages": pages,
            "bookmarks": bookmarks,
            "links": links,
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
