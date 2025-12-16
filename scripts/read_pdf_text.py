#!/usr/bin/env python
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import unicodedata
from contextlib import redirect_stdout
from pathlib import Path


def _strip_accents(text: str) -> str:
    # NFKD splits accented glyphs into base char + combining marks; we then
    # drop combining marks so matching is accent-insensitive (e.g., Índice == Indice).
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _norm(text: str) -> str:
    return _strip_accents(text).casefold()


def _collapse_ws(text: str) -> str:
    return " ".join(text.split())


_PAGE_LIST_RE = r"(?:\d{1,4}|[ivxlcdm]{1,8})(?:\s*(?:,|;|-|–|—)\s*(?:\d{1,4}|[ivxlcdm]{1,8}))*"
_NAV_ENTRY_RE = re.compile(
    rf"""
    ^\s*
    (?P<label>.+?)
    (?:\.{{2,}}|·{{2,}}|-{2,}|_{2,}|\s{{2,}}|\t)?\s*
    (?P<pagelist>{_PAGE_LIST_RE})
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Headings + section labels to look for in English + Spanish.
# Note: These are used as *hints*; the extraction is best-effort and we still
# gate on structure-like patterns to reduce false positives.
_HEADING_HINTS = [
    # Core TOC / index
    "table of contents",
    "table of content",
    "contents",
    "toc",
    "index",
    "indices",
    "indice",
    "indice general",
    "indice de contenidos",
    "tabla de contenidos",
    "tabla de contenido",
    "contenido",
    "contenidos",
    "sumario",
    # Lists
    "list of figures",
    "list of tables",
    "list of illustrations",
    "lista de figuras",
    "lista de tablas",
    "lista de ilustraciones",
    "indice de figuras",
    "indice de tablas",
    "indice de ilustraciones",
    # Other front-matter-ish sections that are often list-like
    "glossary",
    "glosario",
    "abbreviations",
    "abreviaturas",
    "acronyms",
    "siglas",
    "appendix",
    "appendices",
    "apendice",
    "apendices",
    "anexo",
    "anexos",
]

# Subset we treat as "strong" indicators when they appear as standalone headings.
_STRONG_HEADING_HINTS = [
    "table of contents",
    "contents",
    "tabla de contenidos",
    "tabla de contenido",
    "contenido",
    "contenidos",
    "sumario",
    "index",
    "indice",
    "list of figures",
    "list of tables",
    "lista de figuras",
    "lista de tablas",
    "glossary",
    "glosario",
    "abbreviations",
    "abreviaturas",
]


def _looks_like_heading_line(line: str) -> bool:
    # Extremely lightweight "heading" detector for markdown-ish output.
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return True
    # Uppercase headings are common in PDFs → markdown conversions.
    letters = [c for c in stripped if c.isalpha()]
    if len(letters) >= 6:
        upper = sum(1 for c in letters if c.isupper())
        if upper / len(letters) >= 0.85:
            return True
    return False


def _heading_hint_hits(page_text: str) -> tuple[set[str], set[str]]:
    """Return (any_hits, strong_heading_hits)."""
    normalized_full = _collapse_ws(_norm(page_text))
    any_hits: set[str] = set()
    for hint in _HEADING_HINTS:
        if re.search(rf"\b{re.escape(hint)}\b", normalized_full):
            any_hits.add(hint)

    strong_heading_hits: set[str] = set()
    lines = page_text.splitlines()
    # Scan a bit beyond the very top to allow for headers above "Tabla de contenidos", etc.
    for line in lines[:40]:
        if not line.strip():
            continue
        normalized_line = _collapse_ws(_norm(line))
        # Allow headings with mild decoration/punctuation.
        normalized_line = normalized_line.strip(":-–—•·*# \t")
        if not normalized_line:
            continue
        for hint in _STRONG_HEADING_HINTS:
            if re.fullmatch(rf"{re.escape(hint)}", normalized_line) or normalized_line.startswith(
                f"{hint} "
            ):
                # Treat as strong only when the line itself looks like a heading.
                if _looks_like_heading_line(line) or len(normalized_line.split()) <= 6:
                    strong_heading_hits.add(hint)
    return any_hits, strong_heading_hits


def _looks_like_nav_entry_line(line: str, *, max_page: int | None = None) -> bool:
    original = line
    line = line.strip()
    if not line:
        return False

    # Strip common list bullets while keeping numbered headings like "1.2 ...".
    line = re.sub(r"^\s*[-*•]\s+", "", line)
    if len(line) < 6 or len(line) > 160:
        return False

    normalized = _norm(line)
    m = _NAV_ENTRY_RE.match(normalized)
    if not m:
        return False

    label = (m.group("label") or "").strip(" .\t-—–·•_")
    if len(re.findall(r"[a-z]", label)) < 3:
        return False

    # Avoid common false positives like sentences ending in a year.
    pagelist = m.group("pagelist") or ""
    # If it's a single 4-digit token that looks like a year, ignore it.
    tokens = [t.strip() for t in re.split(r"[,;]", pagelist) if t.strip()]
    if len(tokens) == 1 and re.fullmatch(r"\d{4}", tokens[0]):
        year = int(tokens[0])
        if 1500 <= year <= 2200:
            return False

    # Page numbers should generally be plausible relative to the document size.
    # This helps avoid false positives from tables/figures with large numeric values.
    if max_page is not None and max_page > 0:
        numeric_tokens: list[int] = []
        for token in re.split(r"[,;\-–—]", pagelist):
            token = token.strip()
            if not token or not re.fullmatch(r"\d{1,4}", token):
                continue
            numeric_tokens.append(int(token))
        if numeric_tokens:
            if min(numeric_tokens) < 1:
                return False
            if max(numeric_tokens) > max_page:
                return False

    # Avoid treating page footers like "Page 3" as TOC entries.
    if re.fullmatch(r"(?:page|pagina|p)\s+\d{1,4}", _norm(original).strip()):
        return False

    return True


def _looks_like_term_definition_line(line: str) -> bool:
    """Heuristic for Glossary/Abbreviations: TERM — definition."""
    stripped = line.strip()
    if not stripped:
        return False
    # Skip headings and super-short lines.
    if len(stripped) < 8 or len(stripped) > 220:
        return False
    if _looks_like_heading_line(stripped):
        return False

    # Typical separators seen in extracted text.
    if "—" in stripped:
        parts = stripped.split("—", 1)
    elif "–" in stripped:
        parts = stripped.split("–", 1)
    elif " - " in stripped:
        parts = stripped.split(" - ", 1)
    elif ":" in stripped:
        parts = stripped.split(":", 1)
    else:
        return False

    left, right = (parts[0].strip(), parts[1].strip())
    if len(left) < 2 or len(right) < 3:
        return False

    # Require some alpha on both sides.
    if len(re.findall(r"[A-Za-z]", left)) < 2:
        return False
    if len(re.findall(r"[A-Za-z]", right)) < 3:
        return False

    return True


def _is_toc_like_page(page_text: str, *, prev_selected: bool, max_page: int) -> bool:
    lines = [ln for ln in page_text.splitlines() if ln.strip()]
    if not lines:
        return False

    any_hits, strong_heading_hits = _heading_hint_hits(page_text)

    nav_entry_lines = 0
    dot_leader_lines = 0
    term_def_lines = 0
    considered = 0

    for ln in lines:
        stripped = ln.strip()
        # Ignore huge paragraph-like lines to reduce noise.
        if len(stripped) > 260:
            continue
        considered += 1

        if "..." in stripped or "··" in stripped:
            dot_leader_lines += 1

        if _looks_like_nav_entry_line(stripped, max_page=max_page):
            nav_entry_lines += 1

        if _looks_like_term_definition_line(stripped):
            term_def_lines += 1

    if considered == 0:
        return False

    nav_ratio = nav_entry_lines / considered
    term_def_ratio = term_def_lines / considered

    has_any_hint = bool(any_hits)
    has_strong_heading = bool(strong_heading_hits)

    # Core TOC/index/list pages:
    if has_strong_heading and (nav_entry_lines >= 2 or nav_ratio >= 0.12 or dot_leader_lines >= 1):
        return True

    if has_any_hint and nav_entry_lines >= 6 and nav_ratio >= 0.2:
        return True

    # Continuations of TOC pages often don't repeat the heading.
    if prev_selected and nav_entry_lines >= 4 and nav_ratio >= 0.15:
        return True
    if prev_selected and term_def_lines >= 6 and term_def_ratio >= 0.2:
        return True

    # "Index" pages: often have many short lines with page number lists, not dot leaders.
    if has_any_hint and nav_entry_lines >= 10 and nav_ratio >= 0.25:
        return True

    # Glossary/Abbreviations pages can be list-like without page numbers.
    if has_strong_heading and term_def_lines >= 6 and term_def_ratio >= 0.2:
        return True

    # Fallback: no keyword match, but very TOC-like structure.
    if nav_entry_lines >= 12 and nav_ratio >= 0.35 and dot_leader_lines >= 2:
        return True

    return False


def _select_toc_pages(page_text_by_number: dict[int, str], toc_max_pages: int) -> list[int]:
    selected: list[int] = []
    prev_selected = False
    max_page = max(page_text_by_number.keys(), default=0)

    for page_no in sorted(page_text_by_number.keys()):
        if len(selected) >= toc_max_pages:
            break
        text = page_text_by_number[page_no]
        is_candidate = _is_toc_like_page(text, prev_selected=prev_selected, max_page=max_page)
        if is_candidate:
            selected.append(page_no)
            prev_selected = True
        else:
            prev_selected = False

    return selected


def build_markdown_with_page_markers(
    pdf_path: Path,
    *,
    engine: str = "markitdown",
    filter_mode: str = "all",
    toc_max_pages: int = 5,
    expected_pages: int | None = None,
) -> tuple[str, dict[str, object]]:
    """Return markdown for the PDF, segmented per page with explicit markers.

    We extract per-page markdown/text, then prepend a clear comment marker before
    each page's text:
      <!-- PAGE 1 -->
      <page 1 markdown>
      <!-- PAGE 2 -->
      <page 2 markdown>
      ...
    """
    page_text_by_number: dict[int, str] = _extract_pages(
        pdf_path, engine=engine, expected_pages=expected_pages
    )

    selected_pages: list[int] | None = None
    if filter_mode == "toc":
        selected_pages = _select_toc_pages(page_text_by_number, toc_max_pages)

    parts: list[str] = []
    for page_no in sorted(page_text_by_number.keys()):
        if selected_pages is not None and page_no not in selected_pages:
            continue
        text = page_text_by_number[page_no]
        parts.append(f"<!-- PAGE {page_no} -->\n")
        parts.append(text.rstrip() + "\n")
    markdown = "".join(parts)

    meta: dict[str, object] = {
        "engine": engine,
        "filter": filter_mode,
        "toc_max_pages": toc_max_pages,
        "full_char_count": sum(len(t) for t in page_text_by_number.values()),
        "selected_pages": selected_pages if selected_pages is not None else sorted(page_text_by_number.keys()),
        "selected_char_count": len(markdown),
    }
    return markdown, meta


def _extract_pages(
    pdf_path: Path,
    *,
    engine: str,
    expected_pages: int | None,
) -> dict[int, str]:
    if engine == "pymupdf4llm":
        page_text_by_number = _extract_pages_pymupdf4llm(pdf_path)
    elif engine == "markitdown":
        page_text_by_number = _extract_pages_markitdown(pdf_path)
    else:
        raise ValueError(f"Unknown engine: {engine}")

    # Ensure predictable keys for downstream TOC heuristics (max_page checks)
    # and for wrapper metadata that uses pdfinfo page counts.
    if expected_pages is not None and expected_pages > 0:
        normalized: dict[int, str] = {}
        for i in range(1, expected_pages + 1):
            normalized[i] = page_text_by_number.get(i, "")
        return normalized

    return dict(sorted(page_text_by_number.items()))


def _extract_pages_pymupdf4llm(pdf_path: Path) -> dict[int, str]:
    # Some versions of pymupdf4llm print informational messages to stdout.
    # Capture and discard any such output so that this script's stdout only
    # contains the markdown we generate.
    buf = io.StringIO()
    with redirect_stdout(buf):
        import pymupdf4llm  # type: ignore[import-not-found]

        chunks = pymupdf4llm.to_markdown(str(pdf_path), page_chunks=True, extract_words=True)

    page_text_by_number: dict[int, str] = {}
    for chunk in chunks:
        metadata = chunk.get("metadata") or {}
        page_no = metadata.get("page")
        if page_no is None:
            continue
        text = chunk.get("text") or ""
        page_text_by_number[int(page_no)] = text
    return page_text_by_number


def _extract_pages_markitdown(pdf_path: Path) -> dict[int, str]:
    # Like pymupdf4llm, some converters can print diagnostics to stdout.
    # Prevent that from polluting our own stdout (we only emit the final markdown).
    buf = io.StringIO()
    with redirect_stdout(buf):
        from markitdown import MarkItDown  # type: ignore[import-not-found]

        result = MarkItDown().convert_local(pdf_path)
        # DocumentConverterResult exposes both .markdown and .text_content; for PDFs
        # these are typically identical, but prefer .markdown when available.
        text = getattr(result, "markdown", None) or result.text_content

    # markitdown's pdf path emits form-feed (\\f) separators between pages.
    # Keep empty pages (split preserves them) so page indices stay aligned.
    pages = text.split("\f")
    page_text_by_number: dict[int, str] = {}
    for idx, page_text in enumerate(pages, start=1):
        # Normalize to match the pymupdf4llm path (a trailing newline is added later).
        page_text_by_number[idx] = page_text.rstrip() + "\n" if page_text.strip() else ""
    return page_text_by_number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a PDF into markdown, inserting explicit <!-- PAGE n --> markers."
        )
    )
    parser.add_argument(
        "--engine",
        choices=["markitdown", "pymupdf4llm"],
        default="markitdown",
        help=(
            "Extraction engine. "
            "'markitdown' is faster but less layout-aware; "
            "'pymupdf4llm' is more layout-aware but can be slow on some PDFs."
        ),
    )
    parser.add_argument(
        "--filter",
        choices=["all", "toc"],
        default="all",
        help=(
            "Optional extraction filter. "
            "'toc' is best-effort and returns only pages that look like TOC/Index/List sections."
        ),
    )
    parser.add_argument(
        "--toc-max-pages",
        type=int,
        default=5,
        help="Maximum number of pages to output when --filter toc is used. Default: 5.",
    )
    parser.add_argument(
        "--meta-json-out",
        help="Optional path to write extraction metadata JSON (for wrapper/automation).",
    )
    parser.add_argument(
        "--expected-pages",
        type=int,
        default=None,
        help="Optional expected page count (e.g., from pdfinfo) to align page indices. Default: unset.",
    )
    parser.add_argument("pdf", help="Path to the PDF file to convert.")
    args = parser.parse_args(argv)

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    markdown, meta = build_markdown_with_page_markers(
        pdf_path,
        engine=args.engine,
        filter_mode=args.filter,
        toc_max_pages=args.toc_max_pages,
        expected_pages=args.expected_pages,
    )
    if args.meta_json_out:
        Path(args.meta_json_out).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    if args.filter == "toc" and not meta.get("selected_pages"):
        # No matches: keep stdout empty and let the wrapper decide what to do.
        return 4
    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
