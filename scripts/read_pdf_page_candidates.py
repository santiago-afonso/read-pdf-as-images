#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

from read_pdf_text import _select_toc_pages  # reuse existing TOC heuristics


def _collapse_ws(text: str) -> str:
    return " ".join(text.split())


_REFNUM = r"(?:[A-Z]?\d+(?:[.\-]\d+)*|[ivxlcdm]{1,10})"

# Try to match common caption/reference styles (best-effort).
_TABLE_RE = re.compile(
    rf"""
    \b
    (?:table|tabla|tableau|cuadro)
    \s*
    (?:no\.?|n[oº]\.?|num\.?|n\s*°)?
    \s*
    [:.\-]?
    \s*
    (?P<num>{_REFNUM})
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_CHART_RE = re.compile(
    rf"""
    \b
    (?:
      figure|fig\.?|figura|
      chart|graph|graphic|
      graphique|gr[aá]fico|grafico|
      diagram|diagrama|diagramme|
      sch[eé]ma|schema|
      illustration
    )
    \s*
    (?:no\.?|n[oº]\.?|num\.?|n\s*°)?
    \s*
    [:.\-]?
    \s*
    (?P<num>{_REFNUM})
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _extract_page_texts(pdf_path: Path) -> dict[int, str]:
    doc = fitz.open(pdf_path)
    by_page: dict[int, str] = {}
    for page_index in range(doc.page_count):
        page_no = page_index + 1
        by_page[page_no] = doc.load_page(page_index).get_text("text") or ""
    return by_page


def _table_pages(page_texts: dict[int, str]) -> list[int]:
    pages: list[int] = []
    for page_no, text in page_texts.items():
        collapsed = _collapse_ws(text)
        if not collapsed:
            continue
        if _TABLE_RE.search(collapsed):
            pages.append(page_no)
    return sorted(set(pages))


def _chart_pages(page_texts: dict[int, str]) -> list[int]:
    pages: list[int] = []
    for page_no, text in page_texts.items():
        collapsed = _collapse_ws(text)
        if not collapsed:
            continue
        if _CHART_RE.search(collapsed):
            pages.append(page_no)
    return sorted(set(pages))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Find candidate pages (TOC / tables / charts) using best-effort heuristics "
            "over extracted page text; may produce false positives/negatives."
        )
    )
    parser.add_argument("pdf", help="Path to the PDF.")
    parser.add_argument(
        "--kind",
        required=True,
        choices=["toc", "table", "chart"],
        help="Which page type to detect.",
    )
    parser.add_argument("--tool", default="read-pdf", help="Tool name (for metadata).")
    parser.add_argument("--tool-version", default="unknown", help="Tool version (for metadata).")
    parser.add_argument(
        "--pdf-pages",
        type=int,
        default=None,
        help="Optional expected page count (for diagnostics only).",
    )
    parser.add_argument(
        "--toc-max-pages",
        type=int,
        default=5,
        help="Max pages to return for TOC-like candidates (to mirror --toc). Default: 5.",
    )
    args = parser.parse_args(argv)

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    doc = fitz.open(pdf_path)
    if args.pdf_pages is not None and args.pdf_pages > 0 and doc.page_count != args.pdf_pages:
        print(
            f"WARNING: page count mismatch: expected={args.pdf_pages} actual={doc.page_count}",
            file=sys.stderr,
        )

    page_texts = _extract_page_texts(pdf_path)

    mode = f"{args.kind}-pages"
    note = (
        "best-effort heuristics; may produce false positives/negatives; "
        "prefer visual confirmation via 'read-pdf <pdf> --as-images --pages \"...\"'"
    )

    if args.kind == "toc":
        pages = _select_toc_pages(page_texts, args.toc_max_pages)
    elif args.kind == "table":
        pages = _table_pages(page_texts)
    else:
        pages = _chart_pages(page_texts)

    record = {
        "tool": args.tool,
        "tool_version": args.tool_version,
        "mode": mode,
        "best_effort": True,
        "note": note,
        "pdf_path": str(pdf_path),
        "pdf_page_count": doc.page_count,
        "pages": pages,
    }
    sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

