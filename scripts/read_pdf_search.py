#!/usr/bin/env python
from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF


def _collapse_ws(text: str) -> str:
    return " ".join(text.split())


def _word_starts(text: str) -> tuple[list[str], list[int]]:
    words: list[str] = []
    starts: list[int] = []
    for m in re.finditer(r"\S+", text):
        starts.append(m.start())
        words.append(m.group(0))
    return words, starts


def _context_by_words(
    words: list[str],
    word_start_positions: list[int],
    *,
    match_start: int,
    match_end: int,
    context_words: int,
) -> tuple[str, str]:
    if not words:
        return "", ""

    start_idx = bisect.bisect_right(word_start_positions, match_start) - 1
    if start_idx < 0:
        start_idx = 0

    end_idx = bisect.bisect_left(word_start_positions, match_end)
    if end_idx < start_idx:
        end_idx = start_idx
    if end_idx > len(words):
        end_idx = len(words)

    before_start = max(0, start_idx - context_words)
    after_end = min(len(words), end_idx + context_words)

    before = " ".join(words[before_start:start_idx]).strip()
    after = " ".join(words[end_idx:after_end]).strip()
    return before, after


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Search a PDF for a regex and emit JSONL matches with word-context windows "
            "(best-effort: uses PyMuPDF text extraction; layout and hyphenation may affect results)."
        )
    )
    parser.add_argument("pdf", help="Path to the PDF.")
    parser.add_argument("--regex", required=True, help="Python regex pattern to search for.")
    parser.add_argument(
        "--context-words",
        type=int,
        default=50,
        help="Words of context before and after each match. Default: 50.",
    )
    parser.add_argument("--tool", default="read-pdf", help="Tool name (for metadata).")
    parser.add_argument("--tool-version", default="unknown", help="Tool version (for metadata).")
    parser.add_argument(
        "--pdf-pages",
        type=int,
        default=None,
        help="Optional expected page count (for diagnostics only).",
    )
    args = parser.parse_args(argv)

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    try:
        pattern = re.compile(args.regex)
    except re.error as e:
        print(f"ERROR: invalid regex: {e}", file=sys.stderr)
        return 2

    doc = fitz.open(pdf_path)
    if args.pdf_pages is not None and args.pdf_pages > 0 and doc.page_count != args.pdf_pages:
        print(
            f"WARNING: page count mismatch: expected={args.pdf_pages} actual={doc.page_count}",
            file=sys.stderr,
        )

    for page_index in range(doc.page_count):
        page_no = page_index + 1
        text = doc.load_page(page_index).get_text("text") or ""
        collapsed = _collapse_ws(text)
        if not collapsed:
            continue

        words, starts = _word_starts(collapsed)
        for match in pattern.finditer(collapsed):
            before, after = _context_by_words(
                words,
                starts,
                match_start=match.start(),
                match_end=match.end(),
                context_words=args.context_words,
            )
            record = {
                "tool": args.tool,
                "tool_version": args.tool_version,
                "mode": "search",
                "pdf_path": str(pdf_path),
                "page": page_no,
                "match": match.group(0),
                "match_start_char": match.start(),
                "match_end_char": match.end(),
                "context_words": args.context_words,
                "context_before": before,
                "context_after": after,
            }
            sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

