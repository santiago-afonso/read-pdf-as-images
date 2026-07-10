# Canonical Bundle Token Blowup Findings

This note explains why the current canonical PDF "frankenstack" payload can cost more tokens than feeding the full PDF as multimodal page images, even after excluding full-page renders from the default payload.

It is based on the current full-document run:

```text
tmp/benchmark/pdf-canonical-bundles/eval-canonical-full-v1/
```

Source artifacts:

```text
tmp/benchmark/pdf-canonical-bundles/eval-canonical-full-v1/evaluation-report.md
tmp/benchmark/pdf-canonical-bundles/eval-canonical-full-v1/token-comparison.csv
tmp/benchmark/pdf-canonical-bundles/eval-canonical-full-v1/evaluation-manifest.json
```

## Short Answer

The current intended canonical payload is not image-heavy. It is text-heavy.

Full-PDF multimodal ingestion is cheap in token terms because each page image is billed with a compact image-token formula. In these measurements, a full page costs roughly:

| Model family | Approximate full-page image cost |
| --- | ---: |
| OpenAI `gpt-5(detail=high)` accounting | 630 tokens/page |
| Gemini 3 Flash media-resolution accounting | 560 tokens/page |

The canonical payload, by contrast, explicitly spells out extracted page text, table text, table structure, page references, and selected content assets. A dense report page can easily serialize to 800-1,200 text tokens before any image asset is attached.

So the blowup is mostly:

```text
explicit full-document text/table serialization
  >
compact full-page image-token accounting
```

It is not primarily caused by attaching every page as an image. The corrected current accounting excludes full-page renders from the intended LLM payload.

## What Was Counted

The current token comparison separates three concepts:

| Concept | Counted in main `canonical_bundle_*` totals? | Notes |
| --- | --- | --- |
| Extracted page text and table Markdown in `canonical_payload.md` | Yes | This is the dominant cost. |
| Selected native content assets | Yes | Only images passing content-asset heuristics are counted. |
| Full-page renders in `bundle/page-renders/*.png` | No | Stored for audit/fallback/crop generation. |
| Tiny logos, stationery, repeated icons | No by default | Rejected by content-asset heuristics when detected. |
| Stored audit bundle with page renders | Reported separately | See `stored_audit_bundle_*` fields. |
| Full PDF as images | Reported separately | See `pdf_images_*` fields. |

The current payload is still a full-document text payload. It is not yet a retrieval-minimized query packet.

## Measured Full-Document Cases

| Document type | Source pages | Structured text/table tokens | Content asset images | OpenAI content asset tokens | Gemini content asset tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| DPF | 92 | 109,642 | 7 | 6,090 | 3,920 |
| IPF/PAD | 72 | 59,600 | 2 | 1,820 | 1,120 |
| Article IV | 170 | 172,043 | 31 | 36,890 | 17,360 |

The image assets are not the main driver. The structured text/table serialization is.

## Current Intended Payload vs PDF-As-Images

| Document type | Pages | Canonical LLM payload OpenAI | PDF as images OpenAI | Ratio | Canonical LLM payload Gemini | PDF as images Gemini | Ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DPF | 92 | 115,732 | 57,960 | 2.00x | 113,562 | 51,520 | 2.20x |
| IPF/PAD | 72 | 61,420 | 45,360 | 1.35x | 60,720 | 40,320 | 1.51x |
| Article IV | 170 | 208,933 | 107,100 | 1.95x | 189,403 | 95,200 | 1.99x |

Per-page view:

| Document type | Structured text/table tokens per page | Canonical payload OpenAI tokens per page | PDF-as-images OpenAI tokens per page |
| --- | ---: | ---: | ---: |
| DPF | 1,191.8 | 1,258.0 | 630.0 |
| IPF/PAD | 827.8 | 853.1 | 630.0 |
| Article IV | 1,012.0 | 1,229.0 | 630.0 |

The DPF and Article IV cases are roughly twice as expensive as PDF-as-images because their extracted text/table representation is around 1,000+ tokens per page.

## Stored/Audit Bundle Cost

The stored bundle keeps full-page renders for audit, crop generation, and fallback. That is not the intended default LLM payload. If those renders are counted anyway, the overhead is much worse:

| Document type | Stored/audit bundle OpenAI | PDF as images OpenAI | Stored/audit ratio | Full page renders counted |
| --- | ---: | ---: | ---: | ---: |
| DPF | 202,112 | 57,960 | 3.49x | 92 |
| IPF/PAD | 108,950 | 45,360 | 2.40x | 72 |
| Article IV | 347,883 | 107,100 | 3.25x | 170 |

This is the redundant variant: text/table structure plus every page image. It should not be used as the default prompt payload.

## Why The Blowup Happens

### 1. Page images are token-cheap

The PDF-as-images baseline compresses a whole page into a small number of image tiles. In the current accounting, each source page is roughly 630 OpenAI tokens or 560 Gemini tokens.

That cost is independent of whether the page contains:

- 50 words,
- 700 words,
- a dense table,
- repeated headers and footers,
- many numeric cells.

The visual model may still struggle to interpret the layout, but the token bill stays compact.

### 2. Extracted text pays for every visible token

The canonical text packet pays for:

- every paragraph word,
- repeated headers and footers,
- table cell contents,
- footnotes,
- numeric values,
- row and column labels,
- page headings,
- Markdown delimiters and section labels,
- image and table path references.

For dense institutional PDFs, that can exceed the compact page-image token budget.

### 3. Tables expand when serialized

A compact visual table becomes verbose when represented as Markdown, HTML, or JSON.

Even a visually small table may include:

- repeated column headings,
- row labels,
- empty-cell placeholders,
- pipe characters or HTML tags,
- footnote markers,
- numeric values in every cell.

This is especially relevant for DPF policy matrices and IMF Article IV tables.

### 4. Full-document canonical payload is the wrong unit for most queries

The current comparison uses the whole document to make the overhead visible. That is useful as a stress test, but it is not the intended operating mode.

The canonical bundle is supposed to be:

```text
build once -> index -> retrieve relevant pages/tables/assets -> send small packet
```

If the system sends the entire canonical text packet for every query, it loses most of the benefit.

### 5. Structured context buys fidelity, not raw token savings

The frankenstack makes the model's job easier by exposing:

- table rows and cells,
- page numbers,
- table bounding boxes,
- normalized HTML/Markdown,
- extracted content assets,
- audit page renders for fallback.

That structure costs tokens when sent. The payoff is reliability and targeted retrieval, not lower whole-document token cost.

## Case Notes

### DPF

DPF full document:

```text
dpf_ecuador_dpf2_full
92 pages
```

Measured:

| Component | OpenAI tokens |
| --- | ---: |
| Structured text/table payload | 109,642 |
| Selected content assets | 6,090 |
| Intended canonical payload | 115,732 |
| PDF as images | 57,960 |
| Stored/audit bundle with page renders | 202,112 |

Why it blows up:

- DPF policy and results matrices serialize into a lot of text/table tokens.
- The document has dense policy language and repeated institutional structure.
- The intended payload is 2.00x the PDF-as-images baseline even with only 7 selected content assets.
- If every page render is counted, the stored/audit bundle jumps to 3.49x.

### IPF/PAD

IPF/PAD full document:

```text
ipf_p161969_pad_full
72 pages
```

Measured:

| Component | OpenAI tokens |
| --- | ---: |
| Structured text/table payload | 59,600 |
| Selected content assets | 1,820 |
| Intended canonical payload | 61,420 |
| PDF as images | 45,360 |
| Stored/audit bundle with page renders | 108,950 |

Why it is less bad:

- The PAD payload is less dense per page than the DPF and Article IV examples.
- Text/table tokens average about 828 per page.
- It is still 1.35x the PDF-as-images baseline because text serialization exceeds the 630-token page-image budget on many pages.

### Article IV

Article IV full document:

```text
article_iv_ethiopia_2025_full
170 pages
```

Measured:

| Component | OpenAI tokens |
| --- | ---: |
| Structured text/table payload | 172,043 |
| Selected content assets | 36,890 |
| Intended canonical payload | 208,933 |
| PDF as images | 107,100 |
| Stored/audit bundle with page renders | 347,883 |

Why it blows up:

- Article IV reports are dense with prose, macro tables, debt tables, annexes, and repeated report boilerplate.
- The structured text/table payload averages about 1,012 tokens per page.
- The measured content assets are more numerous than in the DPF/IPF cases: 31 selected images.
- The intended canonical payload is 1.95x the PDF-as-images baseline; the stored/audit bundle would be 3.25x.

## What This Does Not Mean

This does not mean the frankenstack is useless.

It means the frankenstack should not be used as:

```text
serialize the whole PDF -> send all text/tables/assets to the model
```

That is often more expensive than:

```text
send the whole PDF as page images
```

The frankenstack is useful when it is used as:

```text
prepare and index -> retrieve small relevant packet -> send selected structure/assets
```

## Practical Recommendation

Use the current frankenstack as a context preparation and retrieval layer, not as a full-document prompt payload.

Default query-time payload:

```text
selected page text
+ selected table HTML/Markdown/JSON
+ page/table coordinates
+ selected content assets if semantically relevant
+ full-page render only if layout ambiguity remains
```

Do not attach:

```text
all page renders
all extracted images
all sidecar JSON
all table files from the entire document
```

## Next Optimization Targets

To make the payload materially cheaper, optimize the query packet rather than the stored bundle:

1. Strip repeated headers, footers, copyright notices, and page boilerplate from canonical text.
2. Chunk and retrieve by page, section, table, and figure rather than sending full documents.
3. Emit compact table summaries for retrieval, with raw HTML/JSON available only for selected tables.
4. Keep asset paths in text and attach images only when the query needs visual evidence.
5. Generate targeted crops from page renders only after retrieval identifies a layout-risk table/chart.
6. Consider a cheap cleanup/compression pass for selected packets, not whole documents.

## Bottom Line

The current frankenstack is token-inefficient for whole-document ingestion because explicit text/table serialization is larger than compact page-image accounting. The fix is not to remove the frankenstack; the fix is to use it at the right level of granularity.

For whole-document "read everything visually" tasks, full-PDF multimodal ingestion can be cheaper.

For precise answers over tables, policies, charts, and page-grounded evidence, use the frankenstack to retrieve and send only the relevant structured context.
