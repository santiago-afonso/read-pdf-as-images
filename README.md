# read-pdf

Unified CLI to read PDFs either as text (default: `pdf_to_markdown`, optional: `pymupdf4llm`) or as images (via Poppler), optimized for use by Codex/CLI coding agents.

Why: Codex/CLI agents need deterministic file paths to attach images *and* a robust way to ingest PDF text while being aware of conversion caveats. This tool wraps Poppler utilities for image rendering and provides two text conversion engines (fast default + precise fallback), emitting machine-friendly metadata to help agents reason about the document.

## Features
- Text mode (default / `--as-text-fast`, alias: `--as-text`):
  - Uses a local Nutrient `pdf_to_markdown` binary (invoked through `read_pdf_text.py`) to convert the PDF into markdown.
  - Emits `<pdf-metadata>` with file identity, conversion/tool info, layout-type guesses, and page count.
  - Emits `<pdf-text>` wrapping the markdown output, with:
    - a header comment summarizing original page count, and
    - optional `<page-structure>` / `<doc-structure>` blocks when requested.
  - This fast lane is whole-document markdown; explicit `<!-- PAGE n -->` markers are not guaranteed.
- Precise (slow) text mode (`--as-text-precise-layout-slow`):
  - Uses `pymupdf4llm.to_markdown(...)` (via `uv run --with pymupdf4llm`) to convert the PDF into markdown.
  - Can be significantly slower than the fast default engine on some PDFs; use when layout-aware extraction is worth the cost.
- TOC/index extraction (`--toc`, best-effort):
  - Emits the same pseudo-XML wrapper as text mode (`<pdf-metadata>` + `<pdf-text>`), but with `<pdf-text>` containing only pages that look like a Table of Contents / Índice / Index / List of Figures / etc.
  - Uses regex + simple structural heuristics (e.g., headings plus lines ending with page numbers / dot leaders). This can miss real TOC pages.
  - Preserves explicit `<!-- PAGE n -->` markers by converting one page at a time through `pdf_to_markdown` before applying the heuristics.
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
- A local Nutrient `pdf_to_markdown` binary for fast PDF → markdown/text conversion.
- `pymupdf4llm` (plus PyMuPDF) for the precise (slow) text mode and optional structural scans (fetched on demand via `uv run --with pymupdf4llm`).

Bootstrap the fast default engine once:

```bash
curl -fsSL https://raw.githubusercontent.com/PSPDFKit/pdf-to-markdown/main/install.sh | sh
pdf-to-markdown --help
```

`read-pdf` does not auto-download the Nutrient binary at runtime. If you keep the binary somewhere else, set:

```bash
export PDF_TO_MARKDOWN_BINARY=/abs/path/to/nutrient-binary
```

On Ubuntu/Debian, for Poppler:
`sudo apt-get install -y poppler-utils`.

Install the CLI into `~/.local/bin` (override with `PREFIX`):

```
make install
# ensure ~/.local/bin is on your PATH
```

By default, `make install` also primes the local runtime checks and `uv` cache (`pypdf`, `pymupdf`, `pymupdf4llm`) so later runs in restricted/offline environments don’t need network. To skip priming:

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
- `<pdf-text>...</pdf-text>` wrapping the markdown output, including a header comment about original page count. In the default fast lane, explicit `<!-- PAGE n -->` markers are not guaranteed.
- Optional `<page-structure>` and `<doc-structure>` blocks when `--page-structure` / `--doc-structure` are supplied, including per-page stats plus bookmarks/links.

## Exit Codes
- `0`: success
- `2`: usage error (prints `--help`)
- `3+`: runtime errors (dependencies, missing outputs, conversions, etc.)

## Notes
- `read-pdf-as-images` remains as a thin, deprecated wrapper that delegates to `read-pdf --as-images` for backward compatibility.
- Image mode generates images only; it does not perform OCR or text extraction.
- Page range parsing accepts lists and ranges, e.g., `1,3,7-12`.
- Default text mode relies on a local Nutrient `pdf_to_markdown` binary. If `read-pdf` cannot find it, it fails fast with the exact bootstrap hint instead of auto-downloading anything.
- Precise (slow) text mode relies on `pymupdf4llm`; use `--as-text-precise-layout-slow` when you need layout-aware extraction and can tolerate higher runtime.
- To force offline behavior (no network attempts), set `READ_PDF_UV_OFFLINE=1`.

## Benchmarking Backends

The repo now includes a reusable CPU-only benchmark harness under `benchmark/pdf/` for comparing local PDF-to-markdown backends on a small pilot corpus.

Files:
- `benchmark/pdf/corpus.yaml` pins the pilot corpus and engine matrix defaults.
- `benchmark/pdf/rubric.md` pins the grading rubric used by `gpt-5.4-mini` subagents.
- `benchmark/pdf/benchmark.py` prepares the corpus, runs conversions, writes timings/artifacts, and emits grading bundles plus a placeholder `grades.json` schema when subagent grading has not been imported yet.
- `benchmark/pdf/mass_compare.py` and `scripts/pdf-mass-compare` run one PDF or page subset through all configured engines in isolated Alpine Podman containers.
- `benchmark/pdf/mass_compare.md` documents the mass-comparator image build, engine groups, and artifact layout.
- `benchmark/pdf/table_image_compare.py` and `scripts/pdf-table-image-compare` run the table/image-focused comparator for engines that can emit structured table output plus image artifacts.
- `benchmark/pdf/table_image_compare.md` documents the table/image engine matrix, corpus resolver, and artifact layout.
- `benchmark/pdf/canonical_bundle.py` and `scripts/pdf-canonical-bundle` build page-centered canonical bundles for LLM ingestion inside a Podman container.
- `benchmark/pdf/canonical_bundle_report.md` documents the recommended hybrid ingestion strategy and quality/token tradeoffs.
- `benchmark/pdf/canonical_bundle_agent_handoff.md` is the concise adoption guide for agents using the canonical bundle CLI from another repo.
- `benchmark/pdf/canonical_bundle_token_blowup.md` explains why full-document canonical payloads can exceed full-PDF multimodal token counts, with measured DPF/IPF/Article IV cases.
- `benchmark/pdf/chart_speed_vs_table_accuracy.py` builds a scatter chart from benchmark `timings.csv` and completed grade payloads.

Examples:

```bash
uv run benchmark/pdf/benchmark.py prepare-corpus
uv run benchmark/pdf/benchmark.py run
uv run benchmark/pdf/benchmark.py run --resolved-corpus path/to/corpus-resolved.json --quality-targets-file path/to/quality-targets.json
uv run benchmark/pdf/chart_speed_vs_table_accuracy.py --timings-csv path/to/timings.csv --grades-json path/to/grades.json --output-svg path/to/chart.svg
scripts/pdf-mass-compare --build-image-only
scripts/pdf-mass-compare --pdf path/to/input.pdf --pages '1-3,7' --jobs 4
scripts/pdf-table-image-compare list-engines
scripts/pdf-table-image-compare resolve-corpus --output tmp/benchmark/pdf-table-images/corpus-resolved.json
scripts/pdf-table-image-compare run --corpus-json tmp/benchmark/pdf-table-images/corpus-resolved.json --engines default
scripts/pdf-canonical-bundle build-image
scripts/pdf-canonical-bundle evaluate
scripts/pdf-canonical-bundle evaluate --full-documents --cleanup never
```

Run artifacts are written to `tmp/benchmark/pdf/<run_id>/` and include:
- `corpus-resolved.json`
- `conversions/`
- `excerpts/`
- `versions.json`
- `timings.csv`
- `grading/`
- `grades.json`
- `summary.md`

If `--quality-targets-file` is provided, quality grading is constrained to explicit spot-check targets:
- full-document conversions are still used for speed measurement
- the grading bundles include only target-local source-page images plus the small excerpt markdown outputs for each engine
- the subagent is instructed not to read any full original PDF or full-document markdown output

The benchmark harness also supports `pdf_to_markdown`. Its upstream wrapper downloads a signed proprietary Nutrient binary from the vendor CDN, so it does not have the same pure-local install/update profile as the Python-first engines.

The mass comparator writes artifacts to `tmp/benchmark/pdf-mass-compare/<run_id>/` by default. It copies/subsets the input PDF before running containers, mounts only that run directory at `/work`, defaults conversion containers to `--network none`, and uses one-container batch mode by default so `timings.csv` records engine runtime separately from Podman wall time. Builds auto-pass known `~/machine-setup` corporate CA files as a Podman secret when present; Nutrient binaries can be mounted with read-only host glibc loader/libs for Alpine execution.

The table/image comparator writes artifacts to `tmp/benchmark/pdf-table-images/<run_id>/` by default. It prefers JSON, then HTML, then Markdown, and keeps only engines with an image-export lane: MarkItDown with a PyMuPDF sidecar, PyMuPDF4LLM, Docling, OpenDataLoader, PDF Oxide, PyMuPDF, Kreuzberg, plus optional Nutrient and Unstructured lanes when their heavier local runtimes are installed. Text-only, table-only, and HTML/JSON-without-images engines are listed as skipped in each run summary with the reason they were excluded.

The canonical bundle tool writes artifacts to `tmp/benchmark/pdf-canonical-bundles/<run_id>/` by default. It runs the converter stack inside Podman and returns a host-readable bundle with page renders, page JSON, PyMuPDF table JSON/HTML/Markdown, extracted images, PyMuPDF4LLM text, optional Docling/OpenDataLoader artifacts, token summaries, and an evaluation report comparing canonical bundles against PDF-as-images ingestion. Token summaries distinguish the intended LLM payload (structured text/tables plus selected content assets) from the stored audit bundle that keeps full-page renders for targeted fallback.

## Docling LLM conversion wrapper

`scripts/pdf-docling-convert` is the local Docling wrapper for policy/economics PDFs where the target is a compact LLM payload rather than a layout-faithful HTML page. It writes raw Docling exports plus `llm/*.llm.md` and `llm/*.llm.html`, keeping simple tables as lean semantic HTML and rendering complex tables/figures as referenced PNG bitmaps.

The bitmap decision is deliberately conservative for tables. Native Docling
merged cells, uncovered/overlapping grid slots, invalid cell offsets, ragged
exports, and adjacent-page continuation signals force source-page bitmap
evidence even when the flattened HTML/CSV looks simple. Repeated header/footer
graphics are suppressed as page furniture; semantic captions can override the
small-image filter. Picture decisions use the PDF bounding box's physical size
in points/inches/cm as the primary scale signal, with page-relative geometry and
raster pixels as complementary evidence. Re-encoded recurring furniture is
matched by a conservative perceptual hash only when it repeats on at least three
pages in the same top or bottom margin with compatible physical dimensions.
Multi-page table fragments receive a stable `continuation_group_id` in the
diagnostics manifest.

Every LLM export records `llm_payload_budget` in the profile manifest and writes
request-ready OpenAI Batch Chat Completions JSONL under `llm/requests/`. The
default 200 MB hard ceiling reserves 20 MB for transport and 24 MB for additional
prompt/schema growth. The JSONL byte count is measured after the Markdown,
actual prompt/schema, and image data URLs have been serialized. If one request
does not fit, the wrapper first splits it at page boundaries, preferring major
section boundaries near the limit, while retaining risky-table bitmaps. Only an
indivisible oversized page triggers the tables-only and then mechanical
text/HTML fallback. Each part is a separate uploadable JSONL file with a manifest
recording its exact byte size and page range. Override the limits only when the
downstream API contract is known:

```bash
--llm-hard-limit-mb 200 \
--llm-transport-headroom-mb 20 \
--llm-prompt-schema-headroom-mb 24
```

Use the real extraction prompt and JSON Schema when producing final request
files so the measured JSONL matches the downstream request rather than only the
default handoff instruction:

```bash
--llm-batch-model gpt-5.1-batch \
--llm-prompt-file path/to/prompt.txt \
--llm-schema-file path/to/response.schema.json
```

Single PDF:

```bash
scripts/pdf-docling-convert convert \
  --input path/to/report.pdf \
  --output-dir tmp/docling-convert/report \
  --profiles standard \
  --device cpu \
  --allow-no-cuda
```

Generic document-level CPU batch with bounded parallelism:

```bash
scripts/pdf-docling-convert batch \
  path/to/a.pdf path/to/b.pdf \
  --output-dir tmp/docling-convert/batch \
  --profiles standard \
  --device cpu \
  --jobs 2 \
  --resume
```

The same batch command accepts newline-delimited lists:

```bash
find /some/corpus -name '*.pdf' |
  scripts/pdf-docling-convert batch \
    --input-list - \
    --output-dir tmp/docling-convert/corpus \
    --profiles standard \
    --device cpu \
    --jobs 2
```

`--jobs` is intended for CPU document-level parallelism. Local GPU/VLM parallelism is refused by default because sustained concurrent CUDA model loads can destabilize laptop WSL/Windows hosts; use serial GPU runs unless you explicitly accept the risk with `--allow-local-gpu-stress`.

### Databricks CPU execution

The production batch lane does not require a GPU or a VLM. Use the existing
`batch` command with `--profiles standard --device cpu`; it uses accurate
TableFormer plus deterministic table/picture routing. The converter already has
a `pypdfium2` source-page renderer fallback for Databricks runtimes without
Poppler, accepts Unity Catalog Volume paths as ordinary paths, bounds
document-level parallelism with `--jobs`, caps each worker's OpenMP/BLAS threads,
isolates each PDF in a subprocess, supports `--resume`, and writes per-document
manifests suitable for a Databricks work queue or Job task.

Representative driver command for a repo-backed Databricks Job or notebook
shell task:

```bash
scripts/pdf-docling-convert batch \
  --input-list /Volumes/<catalog>/<schema>/<volume>/manifests/pdfs.txt \
  --output-dir /Volumes/<catalog>/<schema>/<volume>/docling-output \
  --profiles standard \
  --device cpu \
  --no-ocr \
  --jobs 2 \
  --worker-threads 1 \
  --resume \
  --llm-prompt-file /Volumes/<catalog>/<schema>/<volume>/contracts/prompt.txt \
  --llm-schema-file /Volumes/<catalog>/<schema>/<volume>/contracts/response.schema.json
```

For thousands of PDFs, partition the input manifest across Databricks tasks or
queue workers and keep `--jobs 2` as a per-worker bound rather than launching all
documents from one driver. Model/cache reuse on a persistent job cluster avoids
repeated downloads. Because this lane is standard Docling and deterministic
routing, Serverless Jobs CPU or a conventional CPU job cluster is sufficient;
there is no reason to provision GPU compute unless a separate, explicitly
experimental VLM lane is reintroduced.
