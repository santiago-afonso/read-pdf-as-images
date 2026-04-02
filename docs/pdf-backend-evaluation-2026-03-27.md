# PDF Backend Evaluation

Date: 2026-03-27
Last updated: 2026-04-02

## Decision

Do not replace `markitdown` as the default `read-pdf` text backend today.

None of the evaluated candidates clearly beats `markitdown` on the combined requirement of:

- materially better layout and table extraction,
- comparable operational simplicity,
- fully local execution with no API calls,
- and enough speed to preserve the current "fast default" role.

The strongest candidate is `opendataloader-pdf`, but it changes the runtime model enough that it should be evaluated as an optional engine family first, not adopted immediately as the default replacement.

## Scope and constraints

This evaluation was done for `read-pdf` in this repository, not for general-purpose document parsing.

The target constraints were:

- no API calls ever,
- local execution only,
- WSL on a Ryzen HX 375,
- no GPU-specific complexity unless it is clearly justified,
- preference for speed,
- preference for better layout flow than `markitdown`,
- strong preference for better table handling than `markitdown`,
- formulas matter, but not enough to justify standing up a VLM server or adopting a significantly heavier system unless the overall result is clearly superior.

The acceptance threshold was strict:

> Replace `markitdown` only if a candidate is strictly better on speed x accuracy at a similar level of complexity.

That threshold matters. Several tools look better on raw extraction quality, but that is not enough by itself for this repository.

## Evaluation chronology

This document combines three closely related pieces of work:

1. The initial backend comparison on 2026-03-27.
   - repo/code inspection,
   - upstream benchmark review,
   - and local smoke tests for `markitdown`, `pymupdf4llm`, `docling`, and `kreuzberg`
2. The OpenDataLoader follow-up on 2026-03-27 after installing a local JRE.
   - local `opendataloader-pdf` standard-mode runs,
   - local hybrid-mode runs,
   - and a targeted OCR-forced check for the scanned sample
3. The documentation hardening pass on 2026-04-02.
   - no new backend comparison was added,
   - but this document was expanded so it can stand alone as the full evidence trail without relying on chat history

The conclusion did not change between the second and third passes. The third pass only documented the work more completely.

## What `read-pdf` needs from a backend

The current integration points are concentrated in:

- [scripts/read-pdf](/home/santi/read-pdf-as-images/scripts/read-pdf)
- [scripts/read_pdf_text.py](/home/santi/read-pdf-as-images/scripts/read_pdf_text.py)

In practice, a replacement backend needs to support:

- predictable page-level extraction,
- clean markdown or text output suitable for agent consumption,
- easy local installation through `uv` or similarly lightweight tooling,
- explicit offline behavior,
- and low enough complexity to fit the current CLI's "fast default" philosophy.

That last point is the hardest one.

## Artifact map

The evaluation produced a small local corpus plus several result directories. These paths are the durable evidence for the findings in this document.

### Corpus files

- `tmp/eval/corpus/attention-is-all-you-need.pdf`
  - born-digital scientific paper
  - 15 pages
- `tmp/eval/corpus/wesp-annex-2017.pdf`
  - full downloaded annex source used to derive the table-heavy sample
- `tmp/eval/corpus/wesp-annex-2017-table-sample-4p.pdf`
  - 4-page extracted table-heavy subset
- `tmp/eval/corpus/attention-is-all-you-need-scanned-3p.pdf`
  - 3-page synthetic scanned sample built from rasterized pages of the paper

### Baseline engine result directories

- `tmp/eval/results/markitdown`
- `tmp/eval/results/pymupdf4llm`

Each contains markdown output plus one `.time` file per tested PDF.

### OpenDataLoader result directories

- `tmp/eval/results/opendataloader-standard`
  - first standard-mode attempt using `--to-stdout`
  - timings are real
  - markdown files are zero-byte due to the CLI stdout issue described later
- `tmp/eval/results/opendataloader-hybrid-auto`
  - first hybrid-auto attempt using `--to-stdout`
  - timings are real
  - markdown files are zero-byte due to the same stdout issue
- `tmp/eval/results/opendataloader-standard-files`
  - canonical standard-mode file-backed outputs
- `tmp/eval/results/opendataloader-hybrid-auto-files`
  - canonical hybrid-auto file-backed outputs
- `tmp/eval/results/opendataloader-hybrid-full-science`
  - file-backed hybrid-full run for the scientific paper
  - includes markdown, JSON, `.time`, and extracted images
- `tmp/eval/results/opendataloader-hybrid-full-force-ocr-scanned`
  - file-backed OCR-forced hybrid-full run for the scanned sample
  - includes markdown, `.time`, and one extracted image

### Which directories are canonical for content review

For qualitative output review, prefer:

- `tmp/eval/results/markitdown`
- `tmp/eval/results/pymupdf4llm`
- `tmp/eval/results/opendataloader-standard-files`
- `tmp/eval/results/opendataloader-hybrid-auto-files`
- `tmp/eval/results/opendataloader-hybrid-full-science`
- `tmp/eval/results/opendataloader-hybrid-full-force-ocr-scanned`

The `opendataloader-standard` and `opendataloader-hybrid-auto` directories remain useful only as evidence of the stdout-path bug.

## Candidate summary

### `markitdown`

Current default.

Strengths:

- operationally simple,
- already integrated,
- easy to run through `uv`,
- fast enough for routine use.

Weaknesses:

- poor table reconstruction,
- weak heading and layout preservation on many PDFs,
- output can fragment badly on some multi-column scientific papers.

For this repository, `markitdown` still wins on simplicity, not quality.

### `docling`

Best pure-local Python quality upgrade among the evaluated options.

Strengths:

- clearly better document flow on the local sample,
- much better markdown table reconstruction,
- page-aware API,
- local execution support is an explicit project goal,
- standard local pipeline does not require a remote service.

Weaknesses:

- much slower than `markitdown` in the local test,
- much heavier dependency footprint,
- CLI defaults are not ideal for `read-pdf`,
- formula extraction is a separate enrichment path, not a free part of the base win,
- ad-hoc Python usage triggered OCR model downloads unless configured carefully.

Important integration detail:

- `DoclingDocument.export_to_markdown(page_no=...)` exists.
- `DocumentConverter.convert(..., page_range=(start, end))` exists.
- placeholder image output is possible through the Python API.

That means Docling is technically usable here. It is just not a same-complexity drop-in default.

### `opendataloader-pdf`

Strongest candidate on paper.

Strengths:

- CPU-first design,
- local-only operation,
- standard mode is meant to be fast,
- hybrid mode is still local and adds better handling for complex pages,
- documented support for formulas, charts, OCR, and richer element typing in hybrid mode,
- benchmark story is much stronger than the other candidates for the specific metrics that matter here.

Weaknesses:

- Java 11+ is a hard dependency,
- repeated calls spawn a JVM,
- hybrid mode adds a backend server,
- hybrid mode is not the same complexity tier as the current `markitdown` lane,
- the CLI `--to-stdout` path produced empty markdown in local testing, so file-backed output is currently safer.

This is still the one candidate that most plausibly justifies more work, but only if the project is willing to accept Java as a first-class dependency and keep the hybrid lane opt-in.

### `kreuzberg`

Operationally attractive, but not convincing for this use case.

Strengths:

- very easy to install,
- very fast on the local sample,
- Rust core,
- CPU mode is available,
- page markers are supported,
- flexible CLI.

Weaknesses:

- local markdown quality on the sample did not justify replacing `markitdown`,
- tables were mostly flattened instead of reconstructed,
- formula-heavy sections showed symbol and encoding noise,
- quality improvements did not track with the most favorable benchmark interpretation.

Kreuzberg looks more appealing as a high-throughput extraction tool than as the next markdown-first backend for `read-pdf`.

### `pymupdf4llm`

Not a candidate from the original list, but it is already in the repository and matters to the decision.

Strengths:

- already supported,
- page-aware,
- locally fast in the sample,
- noticeably cleaner than `markitdown` on the sample,
- does reconstruct some tables.

Weaknesses:

- still weak on difficult formulas,
- still weaker than `docling` on tables and structure,
- current repo positioning treats it as "precise but slow", which may not hold uniformly.

This matters because adding a new backend is less compelling if the existing fallback already moves the quality frontier more than expected.

## Local sanity check

### Date and environment

- Date: 2026-03-27
- Environment: WSL 2, Ubuntu 24.04
- Current repository state: clean before evaluation
- Java availability: not installed in the environment at evaluation time

### Sample PDF

The local sanity check used:

- `tmp/eval/attention-is-all-you-need.pdf`
- arXiv PDF of "Attention Is All You Need"
- 15 pages

This is only a bounded smoke test, not a full benchmark.

It was chosen because it is a useful stress case for:

- multi-column narrative flow,
- formulas,
- figures,
- and borderless or lightly structured tables.

### Smoke-test command shapes

The initial local comparison used the repo's existing text-extraction wrapper for the current engines and direct CLIs for new candidates.

Representative command shapes:

```bash
uv run --with 'markitdown[pdf]' python scripts/read_pdf_text.py \
  --engine markitdown tmp/eval/attention-is-all-you-need.pdf

uv run --with pymupdf4llm python scripts/read_pdf_text.py \
  --engine pymupdf4llm tmp/eval/attention-is-all-you-need.pdf

uvx --from docling docling \
  tmp/eval/attention-is-all-you-need.pdf \
  --from pdf --to md --no-ocr

uv run --with kreuzberg python -m kreuzberg.cli \
  extract tmp/eval/attention-is-all-you-need.pdf
```

Those smoke tests established the broad shape of the problem:

- `docling` looked best on quality but much too slow for the default lane,
- `kreuzberg` looked best on raw throughput but did not earn a quality win,
- and `pymupdf4llm` outperformed expectations enough to matter to the recommendation.

### Single-run timings

These are single local runs and should not be treated as rigorous benchmarks.

They are still useful for checking whether a candidate is obviously out of family.

| Engine | Command path | Measured wall time |
|---|---|---:|
| `markitdown` | current `read_pdf_text.py` engine | `6.96s` |
| `pymupdf4llm` | current `read_pdf_text.py` engine | `5.35s` |
| `docling 2.82.0` | CLI, `--from pdf --to md --no-ocr` | `63.74s` |
| `kreuzberg 4.6.3` | CLI, markdown output | `0.94s` |

### Qualitative output notes

#### `markitdown`

Observed behavior on the sample:

- first-page layout was noticeably fragmented,
- some content appeared character-by-character near the top,
- no convincing markdown table reconstruction,
- formulas were present only as inline text fragments.

This matches the known repo complaint: acceptable speed, weak table handling.

#### `pymupdf4llm`

Observed behavior on the sample:

- better first-page structure than `markitdown`,
- some tables were emitted as actual markdown tables,
- formulas remained messy,
- overall output was more usable than expected for a built-in fallback.

This result is important because it suggests the current "slow precise fallback" may already deserve more attention before adding a new engine.

#### `docling`

Observed behavior on the sample:

- best overall narrative flow of the tested local engines,
- clear section hierarchy,
- good markdown tables,
- formulas were preserved in context,
- but formula decoding was not complete without enrichment and placeholders such as `<!-- formula-not-decoded -->` appeared.

Operational notes:

- the CLI output file was huge because images were embedded in markdown by default,
- the Python API can avoid that by using placeholder image mode,
- default ad-hoc Python conversion triggered RapidOCR model downloads in the test environment,
- which means clean `read-pdf` integration would need explicit OCR and image-export choices.

#### `kreuzberg`

Observed behavior on the sample:

- very fast output,
- decent broad narrative extraction,
- page markers were present,
- but table content was largely flattened,
- and formula-heavy sections showed symbol and encoding issues.

It also emitted a warning about page markers not being found even though page markers were present in the resulting markdown, which suggests some rough edges in the current CLI path.

## Benchmark review

### Primary benchmark source used here

The best benchmark source found for this decision was:

- `opendataloader-project/opendataloader-bench`
- latest checked main-branch commit date: 2026-03-27

That repo is useful because it is explicit about:

- corpus size,
- metrics,
- current comparison table,
- and the fact that it is scoring markdown-oriented outputs on reading order, table similarity, and heading hierarchy.

From its main README on 2026-03-27:

| Engine | Overall | Reading Order | Table | Heading | Speed (s/page) |
|---|---:|---:|---:|---:|---:|
| `opendataloader` | `0.84` | `0.91` | `0.49` | `0.74` | `0.05` |
| `opendataloader [hybrid]` | `0.90` | `0.94` | `0.93` | `0.81` | `0.46` |
| `docling` | `0.86` | `0.90` | `0.89` | `0.80` | `0.73` |
| `pymupdf4llm` | `0.57` | `0.89` | `0.40` | `0.41` | `0.09` |
| `markitdown` | `0.29` | `0.88` | `0.00` | `0.00` | `0.04` |

Interpretation:

- `opendataloader` standard mode is the only candidate that looks close to `markitdown` speed while materially improving quality.
- `opendataloader [hybrid]` has the strongest reported overall extraction quality.
- `docling` looks very strong on quality, but at a much slower speed tier.
- `markitdown` remains fast but is dramatically weak on tables and headings in that benchmark.

### Important inconsistency

The `opendataloader-pdf` package README contains benchmark numbers too, but they do not fully match the main benchmark repo.

Example:

- `opendataloader-bench` main README showed `opendataloader` overall `0.84`
- `opendataloader-pdf` README showed `opendataloader` overall `0.72`

That inconsistency is a reason to trust the dedicated benchmark repo more than the package README.

### Kreuzberg benchmark source

The user-provided benchmark release:

- `kreuzberg-dev/kreuzberg/releases/tag/benchmark-run-23359982805`
- published 2026-03-21

As of 2026-03-27, it was still the latest `benchmark-run-*` release I found.

That benchmark release is useful for:

- broad comparative throughput and performance rankings,
- recent release metadata,
- and showing where Kreuzberg sits among many extractors and bindings.

However, it is less directly persuasive for this repository's decision because:

- its quality metrics are less obviously tied to markdown fidelity than the OpenDataLoader benchmark,
- and the local markdown output from Kreuzberg did not support a strong replacement case.

### Practical takeaway from benchmarks

Benchmarks and local testing point in the same direction:

- `docling` is quality-first, not default-fast.
- `kreuzberg` is speed-first, but did not show enough markdown structure quality locally.
- `opendataloader-pdf` is the only candidate that plausibly competes on both axes, but at a higher operational complexity tier.

## Why the answer is still "do not replace the default"

Because the decision rule was not "pick the best extractor."

The decision rule was "replace `markitdown` only if something is clearly better on speed x accuracy at similar complexity."

On that rule:

- `docling` fails the complexity and speed parts.
- `kreuzberg` fails the accuracy-for-this-use-case part.
- `opendataloader-pdf` probably fails the "similar complexity" part, even if it may eventually win on total utility.

That last point is why OpenDataLoader remains interesting without yet being the right default recommendation.

## Follow-up JRE evaluation

### What was installed

The OpenDataLoader follow-up was completed later on 2026-03-27.

Because `sudo` was not available without a password in this WSL session, the JRE was installed as a user-local runtime instead of through `apt`.

- JRE path: `~/.local/opt/temurin-17-jre`
- `java -version`: `Temurin 17.0.18+8`
- `opendataloader-pdf` version used for the follow-up: `2.2.0`

This is good enough for local evaluation, but it still counts as a real runtime dependency that `markitdown` does not require.

### Version notes

Versions explicitly used or observed during this work:

- `opendataloader-pdf`: `2.2.0`
- `kreuzberg`: `4.6.3`
- `markitdown`: `0.1.5` when re-probed on 2026-04-02
- `pymupdf4llm`: `0.2.7` when re-probed on 2026-04-02
- `docling`: the original local smoke test cited here was run with `2.82.0`

One important nuance:

- a post-hoc version probe on 2026-04-02 resolved `docling` to `2.84.0` in the same environment
- the timing cited earlier in this document is still the `2.82.0` timing from the original smoke test, because that was the version actually used when the run happened

### Corpus

The focused follow-up used three PDFs:

1. `tmp/eval/corpus/attention-is-all-you-need.pdf`
   - born-digital scientific paper
   - 15 pages
   - used to evaluate reading order, formulas, figures, and one internal table
2. `tmp/eval/corpus/wesp-annex-2017-table-sample-4p.pdf`
   - 4 extracted pages from the WESP 2017 annex
   - borderless and list-like classification tables
   - used to evaluate table reconstruction
3. `tmp/eval/corpus/attention-is-all-you-need-scanned-3p.pdf`
   - synthetic scanned version of the first 3 pages of the paper
   - used to test OCR behavior in local-only modes

Representative outputs were saved under `tmp/eval/results/`.

### Corpus preparation details

The table-heavy sample was not selected manually page by page from memory.

The workflow was:

1. Download the full WESP annex PDF into `tmp/eval/corpus/wesp-annex-2017.pdf`.
2. Use `read-pdf --table-pages` to identify promising table-heavy pages.
3. Extract pages 4, 6, 7, and 10 into `tmp/eval/corpus/wesp-annex-2017-table-sample-4p.pdf`.

The scanned sample was intentionally synthetic so the content could be compared against the born-digital original:

1. Rasterize pages 1-3 of `attention-is-all-you-need.pdf` with `pdftoppm` at 150 DPI.
2. Reassemble those page images into `attention-is-all-you-need-scanned-3p.pdf` with `img2pdf`.

This means the scanned sample is useful for OCR behavior testing, but it is not a full real-world scan benchmark.

### Important CLI finding

`opendataloader-pdf --format markdown --to-stdout` produced empty files in local testing for both standard and hybrid modes, while file-backed output through `--output-dir` worked.

That is not fatal for integration, but it does matter:

- shell composability is weaker than expected,
- benchmarking must use file output to assess content,
- and any future `read-pdf` integration should assume file output unless this is fixed upstream.

The two zero-byte-output directories were intentionally retained:

- `tmp/eval/results/opendataloader-standard`
- `tmp/eval/results/opendataloader-hybrid-auto`

They are part of the findings, not accidental junk.

### Follow-up command shapes

Representative commands used for the OpenDataLoader follow-up:

Baseline comparisons against current engines:

```bash
/usr/bin/time -o tmp/eval/results/markitdown/attention-is-all-you-need.time -f '%e' \
  uv run --with 'markitdown[pdf]' python scripts/read_pdf_text.py \
  --engine markitdown tmp/eval/corpus/attention-is-all-you-need.pdf \
  > tmp/eval/results/markitdown/attention-is-all-you-need.md

/usr/bin/time -o tmp/eval/results/pymupdf4llm/attention-is-all-you-need.time -f '%e' \
  uv run --with pymupdf4llm python scripts/read_pdf_text.py \
  --engine pymupdf4llm tmp/eval/corpus/attention-is-all-you-need.pdf \
  > tmp/eval/results/pymupdf4llm/attention-is-all-you-need.md
```

Initial OpenDataLoader stdout-path runs:

```bash
/usr/bin/time -o tmp/eval/results/opendataloader-standard/attention-is-all-you-need.time -f '%e' \
  uvx --from opendataloader-pdf opendataloader-pdf \
  tmp/eval/corpus/attention-is-all-you-need.pdf \
  --format markdown --quiet --to-stdout \
  --markdown-page-separator '<!-- PAGE %page-number% -->' \
  > tmp/eval/results/opendataloader-standard/attention-is-all-you-need.md
```

Canonical file-backed standard-mode runs:

```bash
/usr/bin/time -o tmp/eval/results/opendataloader-standard-files/attention-is-all-you-need.time -f '%e' \
  uvx --from opendataloader-pdf opendataloader-pdf \
  tmp/eval/corpus/attention-is-all-you-need.pdf \
  --output-dir tmp/eval/results/opendataloader-standard-files \
  --format markdown --quiet \
  --markdown-page-separator '<!-- PAGE %page-number% -->'
```

Hybrid server and hybrid-auto runs:

```bash
uvx --from 'opendataloader-pdf[hybrid]' opendataloader-pdf-hybrid \
  --port 5002 --enrich-formula

/usr/bin/time -o tmp/eval/results/opendataloader-hybrid-auto-files/attention-is-all-you-need.time -f '%e' \
  uvx --from opendataloader-pdf opendataloader-pdf \
  tmp/eval/corpus/attention-is-all-you-need.pdf \
  --output-dir tmp/eval/results/opendataloader-hybrid-auto-files \
  --format markdown --quiet \
  --markdown-page-separator '<!-- PAGE %page-number% -->' \
  --hybrid docling-fast --hybrid-mode auto \
  --hybrid-url 'http://127.0.0.1:5002'
```

Hybrid-full science run:

```bash
/usr/bin/time -o tmp/eval/results/opendataloader-hybrid-full-science/attention-is-all-you-need.time -f '%e' \
  uvx --from opendataloader-pdf opendataloader-pdf \
  tmp/eval/corpus/attention-is-all-you-need.pdf \
  --output-dir tmp/eval/results/opendataloader-hybrid-full-science \
  --format markdown,json --quiet \
  --markdown-page-separator '<!-- PAGE %page-number% -->' \
  --hybrid docling-fast --hybrid-mode full \
  --hybrid-url 'http://127.0.0.1:5002'
```

Force-OCR hybrid run for the scanned sample:

```bash
uvx --from 'opendataloader-pdf[hybrid]' opendataloader-pdf-hybrid \
  --port 5003 --force-ocr --ocr-lang en --enrich-formula

/usr/bin/time -o tmp/eval/results/opendataloader-hybrid-full-force-ocr-scanned/attention-is-all-you-need-scanned-3p.time -f '%e' \
  uvx --from opendataloader-pdf opendataloader-pdf \
  tmp/eval/corpus/attention-is-all-you-need-scanned-3p.pdf \
  --output-dir tmp/eval/results/opendataloader-hybrid-full-force-ocr-scanned \
  --format markdown --quiet \
  --markdown-page-separator '<!-- PAGE %page-number% -->' \
  --hybrid docling-fast --hybrid-mode full \
  --hybrid-url 'http://127.0.0.1:5003'
```

### Timings

Measured wall times for the focused follow-up:

| PDF | `markitdown` | `pymupdf4llm` | `opendataloader` standard | `opendataloader` hybrid auto | `opendataloader` hybrid full |
|---|---:|---:|---:|---:|---:|
| scientific paper (15p) | `5.58s` | `4.97s` | `2.74s` | `60.53s` | `65.44s` |
| table sample (4p) | `1.12s` | `1.44s` | `0.85s` | `13.31s` | n/a |
| scanned sample (3p) | `0.91s` | `0.55s` | `1.02s` | `1.47s` | `71.55s` with `--force-ocr` |

Interpretation:

- OpenDataLoader standard mode is genuinely fast.
- On born-digital PDFs it was faster than both current engines in this repo.
- Hybrid mode is much slower on CPU, and that is true even before counting server startup and model-download overhead.

The timing numbers above are the canonical file-backed runs for OpenDataLoader, not the earlier stdout-path attempts.

The earlier stdout-path runs produced similar timings for standard mode and even worse timings for one hybrid-auto science run (`99.04s`), but because those runs were entangled with the zero-byte output path, the file-backed runs are the ones that should be cited when comparing actual content.

## Detailed findings by corpus

### Born-digital scientific paper

Primary file:

- `tmp/eval/corpus/attention-is-all-you-need.pdf`

Relevant outputs:

- `tmp/eval/results/markitdown/attention-is-all-you-need.md`
- `tmp/eval/results/pymupdf4llm/attention-is-all-you-need.md`
- `tmp/eval/results/opendataloader-standard-files/attention-is-all-you-need.md`
- `tmp/eval/results/opendataloader-hybrid-auto-files/attention-is-all-you-need.md`
- `tmp/eval/results/opendataloader-hybrid-full-science/attention-is-all-you-need.md`
- `tmp/eval/results/opendataloader-hybrid-full-science/attention-is-all-you-need.json`

Findings:

- `markitdown`
  - worst first-page reading flow of the engines compared locally
  - character-by-character fragmentation near the arXiv header
  - formulas stayed inline and brittle
- `pymupdf4llm`
  - meaningfully better first-page structure than `markitdown`
  - cleaner heading rendering
  - still weak on formulas
- `docling`
  - best local smoke-test narrative flow
  - strongest tables of the non-hybrid engines tested on the paper
  - too slow for the default role
- `kreuzberg`
  - extremely fast
  - did not preserve enough structure to earn the swap
  - formula-heavy regions looked noisy
- `opendataloader` standard
  - faster than both current repo engines
  - clearly better than `markitdown` on the first page
  - preserved figures as image references
  - formula rendering degraded into broken inline math text
  - `Table 1` was not reconstructed as a proper markdown table in the standard lane
- `opendataloader` hybrid
  - restored formulas as LaTeX blocks in markdown
  - reconstructed `Table 1` as a real markdown table
  - produced explicit `formula` nodes in JSON
  - strengthened heading hierarchy relative to standard mode
  - paid for that quality with a very large latency increase

### Borderless-table sample

Primary file:

- `tmp/eval/corpus/wesp-annex-2017-table-sample-4p.pdf`

Relevant outputs:

- `tmp/eval/results/markitdown/wesp-annex-2017-table-sample-4p.md`
- `tmp/eval/results/pymupdf4llm/wesp-annex-2017-table-sample-4p.md`
- `tmp/eval/results/opendataloader-standard-files/wesp-annex-2017-table-sample-4p.md`
- `tmp/eval/results/opendataloader-hybrid-auto-files/wesp-annex-2017-table-sample-4p.md`

Tables specifically inspected:

- `Table C`
- `Table D`
- `Table H`
- `Table I`

Findings:

- `markitdown`
  - partially reconstructed `Table D`
  - left the more complex tables mostly flattened or badly interleaved
- `pymupdf4llm`
  - reconstructed `Table D` reasonably well
  - did not rescue the more difficult list-like tables enough to change the overall decision
- `opendataloader` standard
  - strongest fast-lane result on this sample
  - recovered `Table D` as a markdown table
  - still flattened or merged much of `Table C`, `Table H`, and `Table I`
- `opendataloader` hybrid auto
  - best result on this sample
  - reconstructed `Table C`, `Table D`, `Table H`, and `Table I` as markdown tables
  - still showed merged headers and imperfect cell grouping
  - quality gain was real, but it came at a much slower runtime tier

### Scanned sample

Primary file:

- `tmp/eval/corpus/attention-is-all-you-need-scanned-3p.pdf`

Relevant outputs:

- `tmp/eval/results/markitdown/attention-is-all-you-need-scanned-3p.md`
- `tmp/eval/results/pymupdf4llm/attention-is-all-you-need-scanned-3p.md`
- `tmp/eval/results/opendataloader-standard-files/attention-is-all-you-need-scanned-3p.md`
- `tmp/eval/results/opendataloader-hybrid-auto-files/attention-is-all-you-need-scanned-3p.md`
- `tmp/eval/results/opendataloader-hybrid-full-force-ocr-scanned/attention-is-all-you-need-scanned-3p.md`

Findings:

- `markitdown`
  - effectively no usable text
- `pymupdf4llm`
  - page markers only
  - no meaningful OCR recovery
- `opendataloader` standard
  - no OCR text
  - emitted page-image references only
- `opendataloader` hybrid auto
  - same practical result as standard for this sample
  - still emitted image references only
- `opendataloader` hybrid full with `--force-ocr`
  - recovered real text
  - text was visibly noisy, with punctuation and token corruption
  - runtime was `71.55s` for 3 pages on CPU

This is enough to say that OpenDataLoader can support local scanned OCR, but only after moving into a deliberately slow and operationally heavier lane.

### Standard mode quality

On the scientific paper:

- clearly better first-page flow than `markitdown`,
- faster than both current engines,
- figures were preserved as image references,
- but formulas were degraded into broken inline text,
- and headings were weaker than the hybrid output.

On the table sample:

- meaningfully better than `markitdown`,
- good enough to recover `Table D` as an actual markdown table,
- but `Table C`, `Table H`, and `Table I` were still largely flattened or merged into running text.

On the scanned sample:

- no OCR text was produced,
- output was just page-image references,
- so standard mode does not improve the current multimodal fallback story for scanned PDFs.

Bottom line:

OpenDataLoader standard mode is the first candidate tested here that looks fast enough to be a serious optional CPU lane.

It does not strictly dominate `markitdown` though, because:

- it brings Java complexity,
- it still misses badly on harder tables,
- and it does not solve formulas or scanned OCR in its standard lane.

### Hybrid mode quality

Hybrid mode materially improved content quality on born-digital PDFs.

On the scientific paper:

- formulas were emitted as LaTeX blocks in markdown,
- the JSON output contained explicit `formula` elements,
- `Table 1` was reconstructed as a real markdown table,
- and section structure was stronger than standard mode.

On the table sample:

- `Table C`, `Table D`, `Table H`, and `Table I` were reconstructed into real markdown tables,
- the results were noticeably better than both `markitdown` and `pymupdf4llm`,
- but there were still merged headers and some imperfect cell grouping.

On the scanned sample:

- default hybrid auto mode still emitted only page-image references,
- so local scanned OCR is not part of the default hybrid operating point.

A targeted OCR-forced test was also run:

- server mode: `opendataloader-pdf-hybrid --force-ocr --ocr-lang en --enrich-formula`
- client mode: hybrid `full`
- runtime: `71.55s` for 3 scanned pages
- result: useful OCR text was produced, but it was visibly noisy and still required a dedicated OCR-forced server mode

That means OpenDataLoader can do local scanned OCR, but only in a much heavier operating mode than the one that makes it attractive as a fast default.

### Operational complexity observed in practice

The follow-up also clarified what "hybrid complexity" actually means on this machine:

- a persistent local server must be started,
- first use triggered local model downloads,
- the server reported CPU-only execution,
- the first hybrid server startup took about `18s` before the converter was ready,
- and even after the models were present, the OCR-forced server still represented a separate service tier.

This is still local-only and respects the no-API constraint.

It is not comparable in simplicity to `markitdown`.

Additional practical notes:

- the force-OCR hybrid server initialized faster than the earlier hybrid server because some assets had already been downloaded, but it was still a separate server mode to manage
- the hybrid logs explicitly reported CPU execution and no GPU detection
- the hybrid path also emitted PyTorch warnings about `pin_memory` being enabled without an accelerator
- this did not block the runs, but it is another reminder that the hybrid path is fundamentally a local ML service tier, not just a slightly smarter CLI flag

## Reproduction and interpretation notes

If someone revisits this work later, the following interpretation rules should be used:

1. Treat `opendataloader-standard-files` and `opendataloader-hybrid-auto-files` as the canonical content outputs.
2. Treat `opendataloader-standard` and `opendataloader-hybrid-auto` as evidence of the stdout bug only.
3. Treat `opendataloader-hybrid-full-science` as the canonical proof that hybrid mode can surface structured formulas.
4. Treat `opendataloader-hybrid-full-force-ocr-scanned` as the canonical proof that local scanned OCR is possible, but only in a much heavier configuration.
5. Do not compare the synthetic scanned sample to a production OCR benchmark corpus; it was intentionally a controlled local probe, not a full OCR evaluation.

## Recommended engine strategy

### Recommended now

Keep `markitdown` as the default fast backend.

If backend expansion is wanted, the most promising order is now:

1. Reassess `pymupdf4llm` routing and positioning.
2. Add `opendataloader-pdf` standard mode as an optional born-digital CPU lane.
3. Add `opendataloader-pdf` hybrid mode only as an explicit quality-first lane for table-heavy or formula-heavy PDFs.
4. Consider `docling` only as a slow quality-first alternative if Python-only integration becomes more important than latency.

### Not recommended now

- Do not replace `markitdown` with `opendataloader-pdf` as the default.
- Do not replace `markitdown` with `docling` as the default.
- Do not replace `markitdown` with `kreuzberg` as the default.
- Do not add GPU-specific paths just to support this backend decision.

No evidence gathered here justifies introducing GPU-specific complexity on this machine.

## Final recommendation

The repo should not replace `markitdown` as the default backend yet.

If further work is justified, it should focus on:

- `opendataloader-pdf` standard as the most credible optional engine,
- `opendataloader-pdf` hybrid only as an explicit slow quality lane,
- and a closer look at whether `pymupdf4llm` already deserves better routing than it currently gets.

The JRE follow-up changes one part of the earlier conclusion:

- OpenDataLoader standard mode is now clearly worth pursuing if optional engines are added.

It does not change the default-backend conclusion:

- standard mode is fast enough, but not complete enough, to strictly dominate `markitdown`,
- hybrid mode is good enough, but far too heavy, to become the default lane,
- and forced local OCR works only in an even slower server configuration.

`docling` is still the best local quality upgrade, but too slow and heavy for the current default role.

`kreuzberg` is the most operationally lightweight of the new candidates, but it did not show enough markdown quality on the local sample to justify the swap.

## Sources

- OpenDataLoader benchmark repo: <https://github.com/opendataloader-project/opendataloader-bench>
- OpenDataLoader package repo: <https://github.com/opendataloader-project/opendataloader-pdf>
- OpenDataLoader docs: <https://opendataloader.org/docs>
- Docling docs: <https://docling-project.github.io/docling/>
- Docling CLI reference: <https://docling-project.github.io/docling/reference/cli/>
- Docling model catalog: <https://docling-project.github.io/docling/usage/model_catalog/>
- Kreuzberg docs: <https://docs.kreuzberg.dev/>
- Kreuzberg benchmark release: <https://github.com/kreuzberg-dev/kreuzberg/releases/tag/benchmark-run-23359982805>
- MarkItDown repo: <https://github.com/microsoft/markitdown>
