# read-pdf

Unified CLI to read PDFs either as text (default: `markitdown[pdf]`, optional: `pymupdf4llm`) or as images (via Poppler), optimized for use by Codex/CLI coding agents.

Why: Codex/CLI agents need deterministic file paths to attach images *and* a robust way to ingest PDF text while being aware of conversion caveats. This tool wraps Poppler utilities for image rendering and provides two text conversion engines (fast default + precise fallback), emitting machine-friendly metadata to help agents reason about the document.

## Features
- Text mode (default / `--as-text-fast`, alias: `--as-text`):
  - Uses `markitdown[pdf]` (via `uv run --with markitdown[pdf]`) to convert the PDF into markdown.
  - Emits `<pdf-metadata>` with file identity, conversion/tool info, layout-type guesses, and page count.
  - Emits `<pdf-text>` wrapping the markdown output, with:
    - a header comment summarizing original page count,
    - explicit `<!-- PAGE n -->` markers inserted before each page chunk, and
    - optional `<page-structure>` / `<doc-structure>` blocks when requested.
- Precise (slow) text mode (`--as-text-precise-layout-slow`):
  - Uses `pymupdf4llm.to_markdown(...)` (via `uv run --with pymupdf4llm`) to convert the PDF into markdown.
  - Can be significantly slower than markitdown on some PDFs; use when layout-aware extraction is worth the cost.
- TOC/index extraction (`--toc`, best-effort):
  - Emits the same pseudo-XML wrapper as text mode (`<pdf-metadata>` + `<pdf-text>`), but with `<pdf-text>` containing only pages that look like a Table of Contents / Índice / Index / List of Figures / etc.
  - Uses regex + simple structural heuristics (e.g., headings plus lines ending with page numbers / dot leaders). This can miss real TOC pages.
  - Caps output to 5 pages.
  - If no matches are found, prints plain guidance text (no pseudo-XML) so agents can fall back to full text or image rendering.
- Raw text mode (`--as-raw-text`):
  - Emits only raw markdown output (no pseudo-XML), using the same `pymupdf4llm`-based converter (still includes `<!-- PAGE n -->` markers).
- Output truncation across text modes:
  - All stdout text modes are capped by default at roughly 16k tokens, estimated with `tiktoken` (`o200k_base`).
  - Applies to full text, TOC mode, raw text, regex search JSONL, and page-candidate JSON modes.
  - Override with `--max-output-tokens N` (set `0` to disable truncation).
  - When truncation occurs, output includes a notice with estimated total tokens and the configured cap.
- Images mode (`--as-images`):
  - Pages → images with `pdftoppm` (Poppler).
  - Defaults: PNG @ 220 DPI.
  - Output layout: `tmp/pdf_renders/<pdf-basename>/page-<NNN>.png`.
  - JSONL per page to stderr, now including tool and conversion metadata:
    `{"page":7,"path":"…/page-007.png","dpi":220,"format":"png","mode":"images","pdf_path":"…","pdf_basename":"…","tool":"read-pdf","tool_version":"0.1.3","engine":"pdftoppm"}`.
- Optional structural metadata flags (text mode):
  - `--page-structure` to include per-page summaries (has_text, word_count, image_count, table_count).
  - `--doc-structure` to include bookmarks and link lists (internal/external destinations).
- Clean separation of channels:
  - Text modes: pseudo-XML goes to stdout; stderr reserved for diagnostics.
  - Image mode: JSONL goes to stderr; stdout prints a token-estimate summary (`gpt-5` + Gemini 3) after rendering.

## Install
Dependencies:
- Poppler (`pdftoppm`, `pdfinfo`) for image rendering and PDF metadata.
- `uv` for Python execution and dependency management.
- `markitdown[pdf]` for fast PDF → markdown/text conversion (fetched on demand via `uv run --with markitdown[pdf]`, no persistent virtualenv required).
- `pymupdf4llm` (plus PyMuPDF) for the precise (slow) text mode and optional structural scans (fetched on demand via `uv run --with pymupdf4llm`).

On Ubuntu/Debian, for Poppler:
`sudo apt-get install -y poppler-utils`.

Install the CLI into `~/.local/bin` (override with `PREFIX`):

```
make install
# ensure ~/.local/bin is on your PATH
```

By default, `make install` also primes the `uv` cache (downloads `markitdown[pdf]`, `pymupdf`, `pymupdf4llm`) so later runs in restricted/offline environments don’t need network. To skip priming:

```
make install PRIME_CACHE=0
```

The install target also places the helper Python scripts (`read_pdf_text.py`, `read_pdf_structure.py`) next to the CLI binary so `read-pdf` works from any directory. Re-run `make install` after updating the repo to refresh those helpers.

Development convenience (no stale installs):
```
make install-dev
```
This creates symlinks into `PREFIX/bin` (default: `~/.local/bin`), so changes in `scripts/` are reflected immediately. If `read-pdf --help` doesn’t match what you see in the repo, check `command -v read-pdf` and re-run `make install` (or use `make install-dev`).

Uninstall:
```
make uninstall
```

## Usage
```
read-pdf <pdf> [--as-text-fast] [--page-structure] [--doc-structure]
read-pdf <pdf> --as-text-precise-layout-slow [--page-structure] [--doc-structure]
read-pdf <pdf> --toc [--page-structure] [--doc-structure]
read-pdf <pdf> --as-raw-text
read-pdf <pdf> [mode flags...] [--max-output-tokens 16384]
read-pdf <pdf> --as-images [--pages "1,3,7-12"] [--dpi 220] [--format png|jpeg] [--outdir DIR]
read-pdf --prime-cache
read-pdf --help | -h
read-pdf --version | -V
```

Defaults:
- `--format png`
- `--dpi 220`
- `--outdir tmp/pdf_renders/<pdf-basename-no-ext>/`
- `--max-output-tokens 16384` (stdout text cap; `0` disables)

Behavior (images mode):
- Filenames: `page-<NNN>.<ext>` (zero‑padded; width = max(3, digits(total_pages))).
- JSONL is written to stderr, one line per page (stable manifest for tooling).
- Stdout prints a small token-estimate summary for `gpt-5` + Gemini 3 image inputs (total + per-page average).

Example (images):
```
read-pdf "Input/Classifiers/FY25 Theme Taxonomy. Mar 2025.pdf" --as-images --pages "1-3,6-8"
```

Example JSONL (stderr):
```
{"page":1,"path":"tmp/pdf_renders/FY25 Theme Taxonomy. Mar 2025/page-001.png","dpi":220,"format":"png","mode":"images","pdf_path":"Input/Classifiers/FY25 Theme Taxonomy. Mar 2025.pdf","pdf_basename":"FY25 Theme Taxonomy. Mar 2025.pdf","tool":"read-pdf","tool_version":"0.1.3","engine":"pdftoppm"}
{"page":2,"path":"tmp/pdf_renders/FY25 Theme Taxonomy. Mar 2025/page-002.png","dpi":220,"format":"png","mode":"images","pdf_path":"Input/Classifiers/FY25 Theme Taxonomy. Mar 2025.pdf","pdf_basename":"FY25 Theme Taxonomy. Mar 2025.pdf","tool":"read-pdf","tool_version":"0.1.3","engine":"pdftoppm"}
```

Example (text with metadata):
```
read-pdf "Input/Classifiers/FY25 Theme Taxonomy. Mar 2025.pdf"
```
This prints:
- `<pdf-metadata>...</pdf-metadata>` with file identity, conversion/tool info, and layout-type guesses.
- `<pdf-text>...</pdf-text>` wrapping the markdown output, including a header comment about original page count and `<!-- PAGE n -->` markers.
- Optional `<page-structure>` and `<doc-structure>` blocks when `--page-structure` / `--doc-structure` are supplied, including per-page stats plus bookmarks/links.

## Exit Codes
- `0`: success
- `2`: usage error (prints `--help`)
- `3+`: runtime errors (dependencies, missing outputs, conversions, etc.)

## Notes
- `read-pdf-as-images` remains as a thin, deprecated wrapper that delegates to `read-pdf --as-images` for backward compatibility.
- Image mode generates images only; it does not perform OCR or text extraction.
- Page range parsing accepts lists and ranges, e.g., `1,3,7-12`.
- Default text mode relies on `markitdown[pdf]`; `read-pdf` uses `uv run --with markitdown[pdf]` under the hood, so you only need `uv` installed and network access the first time to fetch the package (subsequent runs will use the cached environment).
- Precise (slow) text mode relies on `pymupdf4llm`; use `--as-text-precise-layout-slow` when you need layout-aware extraction and can tolerate higher runtime.
- To force offline behavior (no network attempts), set `READ_PDF_UV_OFFLINE=1`.
