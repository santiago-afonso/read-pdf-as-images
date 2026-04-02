---
id: rpai-bhqq
status: closed
deps: []
links: []
created: 2026-04-02T15:41:17Z
type: task
priority: 1
assignee: Santiago Afonso
tags: [pdf, benchmark, harness, docs]
---
# Implement reusable PDF backend benchmark harness

Implement the approved reusable CPU-only PDF benchmark harness for read-pdf. Add a benchmark manifest, pinned rubric file, corpus preparation flow for GFSM + auto-resolved Article IV, conversion runners for the full engine matrix, excerpt-based grading inputs for gpt-5.4-mini subagents, and result aggregation artifacts under tmp/benchmark/pdf/<run_id>/.

## Intent

This work turns the earlier one-off backend evaluation into a repeatable, local benchmark workflow that can be rerun as engines evolve. The user wants a small CPU-only benchmark corpus, stable grading criteria recorded in-repo, batch conversions across the candidate engine matrix, and result artifacts that make it easy to compare speed and markdown quality without relying on chat history.

## Scope

- Add a repo-tracked benchmark manifest at `benchmark/pdf/corpus.yaml`.
- Add a repo-tracked grading rubric at `benchmark/pdf/rubric.md`.
- Add a runnable benchmark harness at `benchmark/pdf/benchmark.py`.
- Support a 2-document pilot corpus:
  - fixed GFSM manual path
  - one auto-resolved Article IV report selected from `/home/santi/imf-article-ivs`
- Support the CPU-only engine matrix:
  - `markitdown`
  - `pymupdf4llm`
  - `docling`
  - `kreuzberg`
  - `opendataloader` standard
  - `opendataloader` hybrid auto
  - `markit`
- Generate deterministic excerpt PDFs plus reference renders for grading.
- Produce run artifacts under `tmp/benchmark/pdf/<run_id>/`.

## Non-goals

- Do not change `read-pdf` runtime behavior or replace the default backend in this task.
- Do not add GPU-specific benchmark paths.
- Do not include OCR-forced or scanned-document stress tests in the default pilot.
- Do not rely on any remote API backend.
- Do not run a broad/full repository test suite.

## Approved Plan

The benchmark harness will live in this repo, not in `/home/santi/imf-article-ivs`.

The pilot corpus will use 2 documents:
- the fixed local GFSM manual at `/home/santi/machine-setup/.agents/skills/imf-gfs-manual/references/gfsm2014.pdf`
- one automatically selected Article IV PDF resolved from `/home/santi/imf-article-ivs`

The benchmark will cover:
- `markitdown`
- `pymupdf4llm`
- `docling`
- `kreuzberg`
- `opendataloader` standard
- `opendataloader` hybrid auto
- `markit`

The benchmark will be CPU-only and split into:
- full-document conversions for timing
- short excerpt-PDF conversions for grading

Grading will be done by `gpt-5.4-mini` subagents, and the rubric will be recorded in a file.

Repo files to add:
- `benchmark/pdf/benchmark.py`
- `benchmark/pdf/corpus.yaml`
- `benchmark/pdf/rubric.md`

Corpus manifest details:
- GFSM entry: fixed absolute path
- Article IV entry: auto-resolve from `/home/santi/imf-article-ivs` with:
  - `from_date: 2024-01-01`
  - `candidate_count: 20`
  - `countries_only: true`
  - `article_iv_only: true`
  - `english_only: true`
  - `selection_rule: largest_page_count`
  - tie-breaker: newest year in filename, then lexicographic path

`prepare-corpus` will:
- run in `/home/santi/imf-article-ivs`
- execute `uv run imf-elibrary crawl --from 2024-01-01 --page-size 50 --max-pages 6`
- execute `uv run imf-elibrary map-countries`
- execute `uv run imf-elibrary download --from 2024-01-01 --countries-only --article-iv-only --skip-unknown-iso3 --max-items 20`
- scan `_downloads` recursively for PDFs
- prefer files ending in `-en.pdf`
- pick the largest by page count
- write a resolved corpus manifest to `tmp/benchmark/pdf/<run_id>/corpus-resolved.json`

`run` will prewarm tools before timing so install/cache fill does not contaminate benchmark speed.

The selected conversion commands are:
- `markitdown`: `uv run --with 'markitdown[pdf]' python scripts/read_pdf_text.py --engine markitdown`
- `pymupdf4llm`: `uv run --with pymupdf4llm python scripts/read_pdf_text.py --engine pymupdf4llm`
- `docling`: `uvx --from docling docling <pdf> --from pdf --to md --image-export-mode placeholder --no-ocr --output <dir>`
- `kreuzberg`: `uvx --from kreuzberg kreuzberg extract <pdf> --output-format markdown --page-markers true --ocr false --acceleration cpu`
- `opendataloader` standard: `uvx --from opendataloader-pdf opendataloader-pdf <pdf> --output-dir <dir> --format markdown --quiet --markdown-page-separator '<!-- PAGE %page-number% -->'`
- `opendataloader` hybrid auto: same as standard plus `--hybrid docling-fast --hybrid-mode auto --hybrid-url http://127.0.0.1:5002`
- `markit`: `npx -y markit-ai@0.5.0 convert <pdf> -q -o <out.md>`

The hybrid server should run locally with:
- `uvx --from 'opendataloader-pdf[hybrid]' opendataloader-pdf-hybrid --port 5002 --enrich-formula`

The excerpt selector will:
- always include pages `1-3` as `front_excerpt` if present
- add up to 2 single-page excerpts from `read-pdf --table-pages`
- add 1 single-page excerpt at `round(total_pages / 2)`
- add 1 single-page excerpt at `round(total_pages * 0.8)`
- dedupe page numbers
- cap at 5 excerpts per document
- build each excerpt as its own PDF so every engine is judged on the same slice

The rubric file should define 0-5 integer scores for:
- reading order and layout flow
- heading and section structure
- table extraction
- formula handling
- overall agent usability

Default rubric weights:
- reading order `25%`
- headings `20%`
- tables `30%`
- formulas `10%`
- usability `15%`
- formulas may be `N/A` and excluded from the denominator
- each score requires a one-sentence evidence note

Run output should include:
- `conversions/`
- `excerpts/`
- `timings.csv`
- `versions.json`
- `grades.json`
- `summary.md`

The derived speed/quality metric should be:
- `speed_index = fastest_seconds_per_page_for_doc / engine_seconds_per_page_for_doc`
- `blended_score = quality_score_0_to_100 * speed_index`
- raw quality and raw speed should be shown first

Per-engine failures should not abort the full run.

## Acceptance Criteria

The repo contains a runnable benchmark harness plus rubric and manifest. It can resolve a 2-document pilot corpus, run the specified engine matrix in CPU-only mode, generate deterministic excerpt slices for grading, and write timings, grades, versions, and a markdown summary under tmp/benchmark/pdf/<run_id>/.

## Verification Plan

- Validate manifest loading and resolved corpus selection logic with focused unit tests.
- Validate deterministic excerpt selection and scoring aggregation with focused unit tests.
- Run targeted harness commands that exercise:
  - help/argument parsing
  - dry resolution of the manifest
  - summary/grade data generation on synthetic fixtures where practical
- Run `uv run pytest` only for the benchmark-focused test file plus any directly impacted existing tests if needed.

## Risks and tradeoffs

- The benchmark harness depends on several external CLIs whose output shapes can change upstream.
- OpenDataLoader hybrid requires a local sidecar server and Java runtime, which is materially heavier than the other lanes.
- True `gpt-5.4-mini` grading is an assistant capability, not a normal local Python dependency, so the repo-side harness must preserve deterministic grading inputs and grade-import hooks rather than assuming local autonomous access to subagents.
- `markit`, `docling`, and `kreuzberg` may evolve quickly upstream, so version capture needs to be part of the artifacts.

## Files expected to change

- `.tickets/rpai-bhqq.md`
- `pyproject.toml`
- `uv.lock`
- `README.md`
- `benchmark/pdf/corpus.yaml`
- `benchmark/pdf/rubric.md`
- `benchmark/pdf/benchmark.py`
- `tests/test_pdf_benchmark.py`

## Notes

**2026-04-02T15:57:26Z**

Implemented benchmark/pdf/benchmark.py, benchmark/pdf/corpus.yaml, benchmark/pdf/rubric.md, README benchmark docs, and focused tests. Verified with uv lock, ruff, pytest tests/test_pdf_benchmark.py, and CLI help. Real prepare-corpus smoke on 2026-04-02 hit an upstream imf-elibrary crawl timeout in Playwright while waiting for IMF Staff Country Reports; see tmp/benchmark/pdf/smoke-prepare/logs/prepare-corpus/article-iv-step-01.stderr.txt.

**2026-04-02T16:26:56Z**

Scope updated on 2026-04-02: revise benchmark quality grading to use explicit source-page spot checks only, never full original PDFs or full output markdown. Replace mislabeled Nigeria path PDF with the real Nigeria 2024 Article IV report for the custom corpus rerun.

**2026-04-02T16:52:13Z**

2026-04-02 custom run setup: using a manual 6-document corpus at tmp/benchmark/pdf/imf-spotcheck-20260402/corpus-resolved.json with GFSM + IEO 2004 + Colombia SRDSF 2023 + Argentina EFF 2022 + Argentina EPE 2021 + Nigeria 2024 Article IV. The originally supplied imf_fad1_24_r7114... PDF was inspected after wslpath mapping and excluded from the benchmark because it is a 6-page CV, not a Nigeria Article IV report. Quality grading is driven by explicit spot checks in tmp/benchmark/pdf/imf-spotcheck-20260402/quality-targets-input.json and is bounded to target-local source images plus excerpt markdown only.

**2026-04-02T19:03:03Z**

2026-04-02 benchmark finalized at tmp/benchmark/pdf/imf-spotcheck-20260402. Full-document timing completed for 6 documents x 7 engines. Spot-check grading completed with gpt-5.4-mini on 9 explicit targets after sanitizing grading-input metadata to remove original PDF paths from image entries. Final quality ranking: opendataloader_hybrid_auto 83.33, docling 76.07, pymupdf4llm 68.61, opendataloader 58.17, markitdown 43.93, kreuzberg 37.02, markit 3.33. Blended score is speed-biased and therefore favors kreuzberg 37.02 and opendataloader 25.16 while heavily penalizing docling/hybrid. Markit failed to produce usable markdown outputs across the corpus in this run.

**2026-04-02T21:10:03Z**

2026-04-02 follow-up scope: add PSPDFKit/Nutrient pdf-to-markdown as a benchmarked engine, run it on the same corpus/spot-check setup, and produce a speed-vs-table-accuracy chart. Upstream wrapper caveat discovered from official repo: it downloads a signed platform binary from Nutrient CDN and the free-use license says the software may collect/transmit usage data, so this backend is not the same privacy/network profile as the pure-local Python engines.

**2026-04-02T21:21:32Z**

2026-04-02 added opt-in benchmark support for pdf_to_markdown (Nutrient/PSPDFKit wrapper, resolved to the already-downloaded local binary at ~/.local/share/nutrient/cli/nutrient-linux-amd64 to avoid wrapper update checks during timing), added benchmark/pdf/chart_speed_vs_table_accuracy.py plus focused tests, and ran incremental benchmark imf-spotcheck-pdf-to-markdown-20260402 against the existing 6-document IMF corpus and explicit spot-check targets. Full-document mean speed for pdf_to_markdown was 0.027997 s/page (35.7185 pages/s). Manual bounded table-only grading on the 7 table targets produced mean table score 3.29/5 = 65.71, tied with opendataloader on this corpus. Chart artifacts: tmp/benchmark/pdf/imf-spotcheck-pdf-to-markdown-20260402/speed-vs-table-accuracy.svg and .csv. Caveat remains: upstream wrapper downloads a signed proprietary binary from Nutrient CDN and is not the same pure-local install/update profile as the Python engines.

**2026-04-02T22:18:43Z**

2026-04-02 approved implementation plan: replace markitdown with pdf_to_markdown in read-pdf runtime. Default fast text mode becomes whole-document pdf_to_markdown with fail-fast install hint and optional PDF_TO_MARKDOWN_BINARY override; no hidden markitdown fallback. Keep pymupdf4llm precise mode unchanged. Preserve --toc by page-slicing the PDF and converting one page at a time with pdf_to_markdown so matched_pages and inserted PAGE markers still refer to original page numbers. Update help/install/prime-cache docs and Makefile to stop priming markitdown and instead check for the Nutrient binary without auto-downloading at runtime.

**2026-04-02T22:25:33Z**

2026-04-02 implemented the read-pdf runtime swap from markitdown to pdf_to_markdown. Changed scripts/read-pdf so the default fast text lane and --toc route through pdf_to_markdown with fail-fast runtime resolution (PDF_TO_MARKDOWN_BINARY override or pre-extracted ~/.local/share/nutrient/cli binary only; no wrapper fallback / no silent download), while --as-text-precise-layout-slow and --as-raw-text stay on pymupdf4llm. Changed scripts/read_pdf_text.py to support whole-document pdf_to_markdown extraction for fast mode and page-sliced one-page temporary PDFs for page-aware TOC extraction, while retaining internal markitdown support only for benchmark helper compatibility. Updated README and Makefile help/prime-cache docs accordingly, and added focused tests covering default fast routing, TOC routing, fail-fast missing-binary behavior, and truncation paths. Verified with: bash -n scripts/read-pdf; uv run --with ruff ruff check scripts/read_pdf_text.py tests/test_toc_engine_routing.py tests/test_output_truncation.py; uv run --with pytest pytest tests/test_toc_engine_routing.py tests/test_output_truncation.py -q (5 passed); plus real runtime smoke on a benchmark excerpt for default fast mode and --toc.

**2026-04-02T22:29:45Z**

Closing note: benchmark harness, rubric, corpus manifest, grading bundles, result aggregation, pdf_to_markdown benchmark support, and the speed-vs-table-accuracy chart workflow all landed. Verified during the implementation chain with uv lock, focused ruff/pytest runs, benchmark CLI help, full IMF corpus benchmark runs, and chart generation. Residual caveats: the IMF auto-crawl path hit an upstream Playwright timeout in one smoke run, and pdf_to_markdown remains an opt-in benchmark backend with a proprietary downloaded binary profile.
