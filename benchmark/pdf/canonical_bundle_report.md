# Canonical PDF Bundle Findings

This report captures the recommended ingestion strategy for PDFs with complex tables and bitmap visual content. It builds on the table/image benchmark runs over DPF extracts and a bounded DPF/PAD/Article IV corpus.

## Summary Recommendation

Do not use a single converter as the source of truth. The strongest practical approach is a page-centered canonical bundle that combines:

1. **Docling JSON** as the preferred structured representation when available.
2. **OpenDataLoader JSON** as a table cross-check and fallback when available.
3. **PyMuPDF page renders** as visual ground truth for layout, tables, charts, and figures.
4. **PyMuPDF table detection and geometry** as a lightweight coordinate layer.
5. **PyMuPDF4LLM Markdown** as the readable linear fallback for text-heavy downstream prompting.
6. **Native extracted images** as useful assets, but not as a replacement for page renders.

The practical default stack is:

```text
Docling + OpenDataLoader + PyMuPDF + PyMuPDF4LLM
```

If runtime needs to be smaller:

```text
Docling + PyMuPDF + PyMuPDF4LLM
```

## Key Findings

The important distinction is between the **stored bundle** and the **LLM payload**:

- The stored bundle may include every full-page render, page JSON, extracted images, engine outputs, table HTML/JSON/Markdown, and manifests.
- The intended LLM payload should not include every full-page render.
- The intended LLM payload is structured text/table representation plus selected content assets: figures, charts, graphs, diagrams, and targeted page/table crops only when needed.
- Full-page renders are fallback/audit artifacts. They are useful for verifying layout and generating targeted crops, but passing all of them alongside structured text is redundant with full-PDF multimodal ingestion.
- Logos, stationery, repeated icons, and decorative bitmaps should be filtered out by default.

The corrected full-document comparison shows:

| Document type | Full pages | Intended canonical LLM payload OpenAI | Intended canonical LLM payload Gemini | PDF as images OpenAI | PDF as images Gemini |
| --- | ---: | ---: | ---: | ---: | ---: |
| DPF | 92 | 115732 | 113562 | 57960 | 51520 |
| IPF/PAD | 72 | 61420 | 60720 | 45360 | 40320 |
| Article IV | 170 | 208933 | 189403 | 107100 | 95200 |

The intended payload is still larger than sending the whole PDF as page images for these full-document runs, mostly because structured text/table serialization is large. But the gap is much smaller than the page-render-heavy stored bundle:

| Document type | Intended payload ratio vs PDF images, OpenAI | Stored/audit bundle ratio vs PDF images, OpenAI |
| --- | ---: | ---: |
| DPF | 2.00x | 3.49x |
| IPF/PAD | 1.35x | 2.40x |
| Article IV | 1.95x | 3.25x |

This means the frankenstack is not a lower-token whole-document ingestion strategy. Its value is better structure, retrieval, inspectability, and selective prompting. The practical win is to build the bundle once, index it, then send only the relevant page/table/figure packets to the model.

First-best operational pattern:

1. Generate the canonical bundle and keep all audit artifacts locally.
2. Feed structured text/HTML/JSON/Markdown table payloads by default.
3. Attach extracted content assets when the query needs charts, diagrams, figures, or graphs.
4. Attach full-page renders or table crops only when layout interpretation is unresolved.
5. Use a cheap LLM cleanup pass only for targeted packets, not whole documents, unless a lossy summary is explicitly desired.

Second-best when full multimodal ingestion is blocked:

- Use text-side canonical payloads: PyMuPDF4LLM Markdown, Docling/OpenDataLoader JSON or normalized HTML, PyMuPDF table coordinates, and selected table summaries.
- If no images can be sent, retain coordinates and explicitly flag layout-risk pages so the downstream answer can qualify uncertainty.
- For DPF policy matrices and dense Article IV tables, prefer structured HTML/JSON plus coordinates over plain Markdown alone.

## Why A Hybrid Bundle Wins

Structured converters and visual renderers solve different failure modes.

Docling and OpenDataLoader were strongest for preserving explicit table structure. On the DPF policy/results matrix, they exposed row/cell/table metadata that is much more useful than plain text for reconstructing the intent of the matrix.

PyMuPDF is essential even though it is not the best semantic converter. It gives reliable page renders, page dimensions, embedded image extraction, detected table bounding boxes, and text blocks. For LLM ingestion, that makes it the grounding layer. When table structure is questionable, the page render lets a multimodal model recover the human-visible layout.

PyMuPDF4LLM produced the best readable Markdown among the Markdown-style lanes. It is not the canonical table structure source, but it is useful for downstream prompts because LLMs digest its Markdown well.

MarkItDown remains useful as a cheap baseline and was retained because it was locally validated by a colleague, but it should not be the canonical source for complex tables. PDF Oxide and Kreuzberg are useful comparison lanes but weaker on complex DPF matrices.

## Recommended Bundle Layout

Each page should be represented independently:

```text
page-N/
  page.json                 # page dimensions, text blocks, detected tables, image references
  page.png                  # full-page render, always present for selected pages
  text.md                   # readable text or Markdown
  tables/
    table-1.docling.json
    table-1.opendataloader.json
    table-1.pymupdf.json
    table-1.html
    table-1.md
  images/
    extracted-*
```

The implemented local bundle command writes a compact version of this structure:

```text
bundle/
  canonical_payload.md
  manifest.json
  image_manifest.json
  pages/page_0001.json
  page-renders/page_0001.png
  images/embedded/*
  images/pymupdf4llm/*
  tables/page_0001_table_01/
    pymupdf.json
    table.html
    table.md
  text/
    pymupdf4llm.md
    markitdown.md
    docling.md
  engines/
    docling.json
    opendataloader.json
```

## What To Feed LLMs

For ordinary prose pages:

```text
- Clean page text or Markdown.
- Page metadata and page number.
- Extracted figures/images if present.
- Page render only if the page has table/chart/layout risk.
```

For complex table pages:

```text
- Page heading and nearby context.
- Docling/OpenDataLoader structured table JSON if available.
- Normalized HTML and Markdown table representations.
- PyMuPDF bounding boxes and page coordinates.
- Full page render or table crop.
```

For charts, figures, and diagrams:

```text
- Extracted image if available.
- Full-page render or figure crop from PyMuPDF.
- Nearby caption and surrounding text.
- Page number and coordinates.
```

For weaker text-only or small-context LLMs, prefer:

```text
Clean text + simplified HTML/Markdown tables + selected page/table images
```

Raw deeply nested JSON should be kept as an artifact but not necessarily pasted directly into small models.

## Engine Roles

| Role | Engine |
| --- | --- |
| Primary structured table extraction | Docling |
| Table cross-check / fallback | OpenDataLoader |
| Visual ground truth | PyMuPDF page renders |
| Geometry and table bounding boxes | PyMuPDF |
| Readable text fallback | PyMuPDF4LLM |
| Cheap Markdown baseline | MarkItDown |
| Optional heavy table/image block extraction | Unstructured |
| Optional Markdown + image binary lane | Nutrient pdf-to-markdown |

## Observed Quality Pattern

On DPF policy matrix pages:

- Docling and OpenDataLoader preserve explicit table metadata.
- PyMuPDF detects tables and gives faithful full-page renders.
- PyMuPDF4LLM produces readable Markdown tables, but it can duplicate headers or flatten row groups.
- MarkItDown often preserves recognizable text but table columns can collapse or interleave.
- PDF Oxide is fast, but complex table structure is weaker.
- Kreuzberg can provide HTML-ish text and images, but it did not expose separate table objects for the tested DPF matrices.

## Token Tradeoff Expectations

Full-PDF-as-images can be attractive for very small page selections because the token accounting is simple and bounded by page count. But it sacrifices:

- searchable text,
- table cells/rows,
- coordinates,
- normalized Markdown/HTML tables,
- engine disagreement diagnostics.

The canonical bundle can cost more tokens if it is naively sent with both page renders and full text/JSON artifacts. The intended production use is selective:

- Always keep the full bundle on disk.
- Feed structured text/table packets to the LLM by default.
- Attach extracted content assets such as figures, charts, graphs, and diagrams when they are semantically relevant.
- Attach full-page renders only as targeted fallback for pages with unresolved table/chart/layout ambiguity.
- Do not attach stationery/logos/decorative bitmaps by default.
- Prefer cleaned HTML/Markdown summaries for low-cost passes.

## Bounded Page-Slice Evaluation Run

The first local canonical bundle evaluation was a bounded page-slice smoke run over one DPF extract, one IPF/PAD page pair, and one Article IV page pair. This was useful for quality inspection, but it was not a full-document overhead comparison:

```bash
scripts/pdf-canonical-bundle evaluate \
  --run-id eval-canonical-v1 \
  --cleanup auto \
  --cleanup-model wbg-gemini-3-flash-preview \
  --cleanup-max-tokens 4096 \
  --cleanup-timeout 240 \
  --timeout-seconds 1200
```

Artifacts:

```text
tmp/benchmark/pdf-canonical-bundles/eval-canonical-v1/
  evaluation-report.md
  evaluation-manifest.json
  token-comparison.csv
  dpf_ecuador_policy_matrix/
  ipf_p161969_pad/
  article_iv_ethiopia_2025/
```

The token counts below use the corrected intended-payload accounting: structured text/table payload plus content assets only. Full page renders remain stored on disk but are not counted as default LLM payload.

| Document type | Selected pages | Canonical LLM payload OpenAI | Canonical LLM payload Gemini | Canonical + Gemini cleanup OpenAI | Canonical + Gemini cleanup Gemini | PDF as images OpenAI | PDF as images Gemini |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DPF | `1` | 1749 | 1749 | 820 | 820 | 630 | 560 |
| IPF/PAD | `1,10` | 811 | 811 | 514 | 514 | 1260 | 1120 |
| Article IV | `1,45` | 1608 | 1608 | 1918 | 1918 | 1260 | 1120 |

Quality notes from the run:

- **DPF:** the page render preserved the full policy/results matrix layout, and PyMuPDF table detection produced JSON/HTML/Markdown table artifacts. The Gemini 3 Flash cleanup pass reduced the payload substantially while keeping the table easier to read, but it should be treated as a normalized derivative rather than the canonical record.
- **IPF/PAD:** the bundle preserved the cover plus selected project-detail page with renders and text. Cleanup modestly reduced tokens and made the packet easier to scan.
- **Article IV:** the selected analytical page contained a dense monetary survey table. The cleanup pass reconstructed a more readable Markdown table and increased token count, which is the right tradeoff when table readability matters more than minimum tokens.
- **Docling in the derived Alpine container:** the local containerized evaluation kept the Docling artifact lane but Docling failed at runtime because its installed path wanted `torch`. The earlier host-side benchmark validated Docling as a strong HTML/JSON table engine, so it remains part of the recommended first-best stack. The current lightweight container run still exercised OpenDataLoader, PyMuPDF, PyMuPDF4LLM, and MarkItDown.

## Full-Document Overhead Run

The corrected full-document overhead evaluation was run with:

```bash
scripts/pdf-canonical-bundle evaluate \
  --full-documents \
  --run-id eval-canonical-full-v1 \
  --cleanup never \
  --timeout-seconds 3600
```

Artifacts:

```text
tmp/benchmark/pdf-canonical-bundles/eval-canonical-full-v1/
  evaluation-report.md
  evaluation-manifest.json
  token-comparison.csv
  dpf_ecuador_dpf2_full/
  ipf_p161969_pad_full/
  article_iv_ethiopia_2025_full/
```

This is the apples-to-apples overhead comparison for "send the full PDF as multimodal pages" versus "send the intended canonical LLM payload." The intended payload excludes full-page renders and likely stationery/decorative images. It intentionally does not run the Gemini cleanup pass, because a single full-document cleanup output is a lossy compression/summarization artifact rather than a faithful replacement for the bundle.

| Document type | Full pages | Canonical LLM payload OpenAI | Canonical LLM payload Gemini | PDF as images OpenAI | PDF as images Gemini | OpenAI payload/image ratio | Gemini payload/image ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DPF | 92 | 115732 | 113562 | 57960 | 51520 | 2.00x | 2.20x |
| IPF/PAD | 72 | 61420 | 60720 | 45360 | 40320 | 1.35x | 1.51x |
| Article IV | 170 | 208933 | 189403 | 107100 | 95200 | 1.95x | 1.99x |

Intended payload composition:

| Document type | Structured text/table tokens | Content asset images | Content asset tokens OpenAI | Content asset tokens Gemini |
| --- | ---: | ---: | ---: | ---: |
| DPF | 109642 | 7 | 6090 | 3920 |
| IPF/PAD | 59600 | 2 | 1820 | 1120 |
| Article IV | 172043 | 31 | 36890 | 17360 |

Stored/audit bundle overhead if every full-page render is included:

| Document type | Full pages | Stored/audit bundle OpenAI | Stored/audit bundle Gemini | Full page renders counted | Audit image count |
| --- | ---: | ---: | ---: | ---: | ---: |
| DPF | 92 | 202112 | 170682 | 92 | 109 |
| IPF/PAD | 72 | 108950 | 102720 | 72 | 77 |
| Article IV | 170 | 347883 | 288523 | 170 | 208 |

The conclusion is now more precise: the intended frankenstack payload is not "full PDF images plus structure." It is structured text/table representation plus selected content assets. Even then, full-document structured payloads can still exceed PDF-as-images tokens because the text/table serialization is large. The reason to build the frankenstack is not lower whole-document token cost; it is better retrieval, table structure, inspectability, and selective packet construction.

## First-Best Recommendation

Use the canonical bundle with selective multimodal prompting:

1. Run PyMuPDF for every selected page to create renders, geometry, embedded images, and table bboxes.
2. Run Docling for structured document/table JSON.
3. Run OpenDataLoader as a table cross-check when available.
4. Run PyMuPDF4LLM for readable Markdown.
5. Build page/table/figure packets and attach page renders for layout-risk pages.

## Second-Best If Full Multimodal Ingestion Is Blocked

Use the text-side canonical bundle:

```text
Docling/OpenDataLoader table JSON or normalized HTML
+ PyMuPDF4LLM Markdown
+ PyMuPDF table coordinates
+ selected table/page images only when a downstream model can accept them
```

If images are completely blocked, retain page coordinates and include a short note that the table has visual layout risk. For DPF policy matrices, this is important because merged headers and row groups are often semantically meaningful.

## Local Commands

Build the derived container:

```bash
scripts/pdf-canonical-bundle build-image
```

Run one PDF:

```bash
scripts/pdf-canonical-bundle run \
  --pdf /path/to/input.pdf \
  --pages "1,3-4" \
  --doc-id sample
```

Run the DPF/IPF/Article IV evaluation:

```bash
scripts/pdf-canonical-bundle evaluate
```

Artifacts are written under `tmp/benchmark/pdf-canonical-bundles/<run-id>/`.
