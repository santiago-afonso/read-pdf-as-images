# CODEX TODO / PRD — Issue #1 — read-pdf-as-images

GitHub Issue: https://github.com/santiago-afonso/read-pdf-as-images/issues/1

## Context
Codex often needs to visually read PDF documents in the CLI harness. Base64 streaming to stdout is unreliable (stdout is truncated in the harness), and the image attach tool expects filesystem paths. A tiny wrapper around Poppler is sufficient to rasterize selected PDF pages into images and emit a machine‑parsable manifest that Codex can immediately consume.

## Objectives
- Provide a single CLI `read-pdf-as-images` that:
  - Renders specified page ranges from a PDF into images.
  - Defaults to PNG at 220 DPI.
  - Writes outputs under `tmp/pdf_renders/<filename-without-ext>/page-<NNN>.png`.
  - Always emits one JSONL line per generated page to stderr with: `{ "page": N, "path": "…", "dpi": 220, "format": "png" }`.
  - Provides `--help` and shows it when invoked with improper parameters.
  - Validates page ranges against the actual page count.
  - Keeps stdout clean for piping.

## Non-goals
- OCR / text extraction, annotation parsing, base64 streaming, or PDF structure analysis.

## Design / Behavior
- Dependencies: Poppler (`pdftoppm`, `pdfinfo`).
- Command shape: `read-pdf-as-images <pdf> [--pages "1,3,7-12"] [--dpi 220] [--format png|jpeg] [--outdir DIR] [--help]`.
- Default output dir: `tmp/pdf_renders/<pdf-basename-no-ext>/` (directory is created if missing).
- Filenames: `page-<NNN>.<ext>` with zero‑padding width = max(3, digits(total_pages)).
- Page selection: accepts comma/range list (e.g., `1,3,7-12`). If omitted, renders all pages.
- Efficiency: contiguous ranges are grouped into a single `pdftoppm` invocation per range.
- JSONL to stderr: one line per generated page, immediately usable by Codex to attach images.
- Exit codes: `0` success; `2` usage error (prints help); `>=3` runtime errors.

## Tasks (tracked and updated)
- [x] Initialize repo
- [x] Create GitHub Issue #1 (in read-pdf-as-images repo)
- [x] Author PRD/TODO file
- [x] Implement `scripts/read-pdf-as-images` per spec
- [x] Add `--help` output with examples
- [x] Validate against sample PDF (FY25 Theme Taxonomy)
- [x] Document minimal usage in README or within `--help`
- [x] Add Makefile install/uninstall targets (default to ~/.local/bin)
- [ ] Optional: add `--version`

## Acceptance Criteria
- Running `read-pdf-as-images "Input/Classifiers/FY25 Theme Taxonomy. Mar 2025.pdf" --pages "1-3,6-8"` produces images under `tmp/pdf_renders/FY25 Theme Taxonomy. Mar 2025/` with filenames like `page-001.png` and emits JSONL lines to stderr with the absolute/relative paths created.
- `--help` prints usage and examples; invalid invocation prints help and exits with non-zero status.

## Minimal prompt for usage
If you need to quickly tell Codex how to render and read PDF pages visually, use this:

> Render pages as images and attach them for visual inspection only (no OCR):
> Run: `scripts/read-pdf-as-images "<pdf_path>" --pages "<comma/ranges>"`.
> The command outputs JSONL to stderr with `path` fields under `tmp/pdf_renders/<pdf-name>/page-###.png`. Attach each `path` as an image and proceed to analyze visually.
