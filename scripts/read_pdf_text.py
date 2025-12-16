#!/usr/bin/env python
from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path


def build_markdown_with_page_markers(pdf_path: Path) -> str:
    """Return markdown for the PDF, segmented per page with explicit markers.

    We use pymupdf4llm.to_markdown(..., page_chunks=True) to get per-page chunks
    and then prepend a clear comment marker before each page's text:
      <!-- PAGE 1 -->
      <page 1 markdown>
      <!-- PAGE 2 -->
      <page 2 markdown>
      ...
    """
    # Some versions of pymupdf4llm print informational messages to stdout.
    # Capture and discard any such output so that this script's stdout only
    # contains the markdown we generate.
    buf = io.StringIO()
    with redirect_stdout(buf):
        # Import inside the redirected context so that any informational prints
        # from pymupdf4llm or its dependencies are captured and discarded.
        import pymupdf4llm  # type: ignore[import-not-found]

        chunks = pymupdf4llm.to_markdown(str(pdf_path), page_chunks=True, extract_words=True)
    parts: list[str] = []
    for chunk in chunks:
        metadata = chunk.get("metadata") or {}
        page_no = metadata.get("page")
        if page_no is None:
            # Should not normally happen, but be robust.
            continue
        text = chunk.get("text") or ""
        parts.append(f"<!-- PAGE {page_no} -->\n")
        parts.append(text.rstrip() + "\n")
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a PDF into markdown using pymupdf4llm, "
            "inserting explicit <!-- PAGE n --> markers."
        )
    )
    parser.add_argument("pdf", help="Path to the PDF file to convert.")
    args = parser.parse_args(argv)

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    markdown = build_markdown_with_page_markers(pdf_path)
    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
