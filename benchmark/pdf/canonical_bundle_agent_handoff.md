# Canonical PDF Bundle Agent Handoff

Use this document to adopt `read-pdf` as a PDF context-preparation layer from another repo.

The current integration surface is a CLI, not a Python library. It is usable from another project today by calling `scripts/pdf-canonical-bundle` with an explicit artifact root and then reading `result.json`.

## Purpose

Prepare PDF context for LLM queries where layout, complex tables, figures, charts, and extracted text all matter.

The pipeline creates a local canonical bundle with:

- structured page text and page metadata,
- normalized table artifacts in JSON, Markdown, and HTML when detected,
- PyMuPDF4LLM Markdown as a readable text fallback,
- OpenDataLoader JSON when available,
- extracted native PDF images,
- full-page renders for audit, fallback, and crop generation,
- token-accounting fields for intended LLM payloads, stored audit bundles, and PDF-as-images baselines.

## Core Rule

Do not pass the whole stored bundle to the LLM.

The stored bundle and the LLM payload are different:

| Concept | Use |
| --- | --- |
| Intended LLM payload | Structured text/table payload plus selected content assets. |
| Stored/audit bundle | Full artifact set, including page renders, sidecars, manifests, and rejected image candidates. |
| Full PDF as images | Baseline multimodal ingestion path, one image per page. |

Full-page renders live in the stored bundle so agents can inspect or crop them later. They are not default prompt attachments.

## Prerequisites

On the machine that hosts this repo:

- `uv`
- `podman`
- the `read-pdf` checkout, for example `/home/safonso/read-pdf`
- the mass-comparator base image, then the canonical bundle image

Build once:

```bash
cd /home/safonso/read-pdf
scripts/pdf-mass-compare --build-image-only
scripts/pdf-canonical-bundle build-image
```

The canonical image derives from `localhost/read-pdf-mass-compare:alpine`. If `build-image` fails because that base image is missing, build the mass-comparator image first.

## Run From Another Repo

From the adopting project:

```bash
READ_PDF_REPO=/home/safonso/read-pdf
PDF=/absolute/path/to/input.pdf
DOC_ID=my_document_id
RUN_ID=my_document_id_full
ARTIFACT_ROOT="$PWD/.artifacts/pdf-context"

"$READ_PDF_REPO/scripts/pdf-canonical-bundle" run \
  --pdf "$PDF" \
  --doc-id "$DOC_ID" \
  --pages all \
  --run-id "$RUN_ID" \
  --artifact-root "$ARTIFACT_ROOT" \
  --cleanup never \
  --timeout-seconds 1800
```

The command prints the run directory on stdout, for example:

```text
/path/to/adopting-repo/.artifacts/pdf-context/my_document_id_full/my_document_id
```

Use page ranges when the downstream task already knows the relevant pages:

```bash
"$READ_PDF_REPO/scripts/pdf-canonical-bundle" run \
  --pdf "$PDF" \
  --doc-id "$DOC_ID" \
  --pages "1,7-9,31" \
  --artifact-root "$ARTIFACT_ROOT" \
  --cleanup never
```

## Output Contract

Given:

```text
RUN_DIR=.artifacts/pdf-context/<run-id>/<doc-id>
```

Read:

```text
RUN_DIR/result.json
```

Important files:

| Path | Meaning |
| --- | --- |
| `result.json` | Main host-side manifest. Use this first. |
| `bundle/canonical_payload.md` | Readable structured text/table packet. Paths are references, not attachment instructions. |
| `bundle/manifest.json` | In-container bundle manifest. |
| `bundle/pages/page_*.json` | Page text, dimensions, text blocks, table refs, image refs. |
| `bundle/tables/*/pymupdf.json` | Detected table rows and geometry. |
| `bundle/tables/*/table.html` | Normalized HTML table. |
| `bundle/tables/*/table.md` | Normalized Markdown table. |
| `bundle/text/pymupdf4llm.md` | Readable Markdown fallback. |
| `bundle/engines/opendataloader.json` | OpenDataLoader JSON when available. |
| `bundle/engines/docling.json` | Docling JSON when available. Currently absent in the lightweight Alpine run if Docling imports `torch`. |
| `bundle/images/embedded/*` | Native PDF embedded images. Some are real content assets; some are stationery. |
| `bundle/page-renders/page_*.png` | Full-page renders. Audit/fallback only by default. |

## Token Fields

Use `result.json["token_summary"]`.

Main fields:

| Field | Meaning |
| --- | --- |
| `canonical_bundle_gpt5_total` | Intended OpenAI LLM payload: structured text/table tokens plus selected content assets. |
| `canonical_bundle_gemini3_total` | Intended Gemini LLM payload: structured text/table tokens plus selected content assets. |
| `payload_text_tokens` | Structured text/table packet tokens. |
| `content_asset_image_count` | Number of selected content assets counted in intended payload. |
| `content_asset_selection.selected` | Image assets selected for possible attachment. |
| `content_asset_selection.rejected` | Image assets rejected by heuristics, usually stationery/logos/tiny/decorative/full-page-like images. |
| `stored_audit_bundle_gpt5_total` | What the payload would cost if every page render and audit image were counted. Do not use as default LLM payload. |
| `stored_audit_bundle_gemini3_total` | Gemini version of stored/audit bundle cost. |
| `pdf_images_gpt5_total` | Baseline cost for sending selected source PDF pages as images. |
| `pdf_images_gemini3_total` | Gemini version of PDF-as-images baseline. |

The intended payload excludes full-page renders by default.

## Payload Selection Rules

Default payload for an LLM query:

1. Include relevant sections from `bundle/canonical_payload.md`, or construct a smaller packet from `bundle/pages/page_*.json`.
2. Include table artifacts when tables matter:
   - `table.html` for structure,
   - `pymupdf.json` for rows and coordinates,
   - `table.md` for model-readable fallback.
3. Attach images only from `token_summary.content_asset_selection.selected` when semantically relevant to the query.
4. Attach a full-page render from `bundle/page-renders/` only when layout interpretation is unresolved or a targeted crop is needed.
5. Do not attach every file in `bundle/images/`, `bundle/images/pymupdf4llm/`, or `bundle/page-renders/`.
6. Do not attach tiny repeated icons, logos, or stationery by default.

## Minimal Context Packet Shape

An adopting agent should construct a prompt packet like:

```json
{
  "document_id": "my_document_id",
  "source_pdf": "/absolute/path/to/input.pdf",
  "pages": [
    {
      "page": 31,
      "text": "...",
      "tables": [
        {
          "html_path": ".../bundle/tables/page_0031_table_01/table.html",
          "json_path": ".../bundle/tables/page_0031_table_01/pymupdf.json",
          "bbox": [72.0, 144.0, 540.0, 620.0]
        }
      ],
      "content_assets": [
        "/.../bundle/images/embedded/page_0031_image_001_xref123.png"
      ],
      "audit_page_render": "/.../bundle/page-renders/page_0031.png"
    }
  ],
  "notes": [
    "audit_page_render is a fallback reference, not a default attachment"
  ]
}
```

Then attach only the `content_assets` that are relevant. Attach `audit_page_render` only after the model or retrieval layer identifies a layout ambiguity.

## Suggested Integration Pattern

For another repo:

1. Wrap the CLI in a local helper, for example `prepare_pdf_context`.
2. Cache by:
   - PDF absolute path or content hash,
   - selected pages,
   - `read-pdf` git revision,
   - canonical image tag.
3. Store run artifacts under the adopting repo, for example `.artifacts/pdf-context/`.
4. Parse `result.json`.
5. Index page text, table text, table HTML, page numbers, and coordinates.
6. At query time, retrieve relevant pages/tables and build a small packet.
7. Add selected content assets only if the query requires visual evidence.
8. Use full-page renders only as fallback or to generate crops.

## Copy-Paste Instructions For An Agent

Use this when handing the task to another coding agent:

```text
Use /home/safonso/read-pdf/scripts/pdf-canonical-bundle as the PDF context preparation CLI.

Run it with --artifact-root inside this repo's artifact directory and read RUN_DIR/result.json.

Treat result.json["token_summary"]["canonical_bundle_*"] as the intended LLM payload estimate.
Do not treat page-renders/*.png as default attachments.
Do not attach every extracted image.

Build query context from:
- bundle/canonical_payload.md or bundle/pages/page_*.json for text,
- bundle/tables/*/table.html and bundle/tables/*/pymupdf.json for tables,
- token_summary.content_asset_selection.selected for candidate visual assets.

Attach bundle/page-renders/page_*.png only for targeted fallback when table/chart/layout interpretation is unresolved.
```

## Current Limitations

- The current integration surface is CLI plus JSON files, not a packaged importable library.
- Full-page renders are generated for audit/fallback even though they are not default LLM payload.
- Content-asset selection uses transparent heuristics, not a perfect chart/figure classifier.
- Vector charts and complex visual tables may not appear as native embedded image assets; use page coordinates and targeted crops when needed.
- The lightweight Alpine container currently runs OpenDataLoader, PyMuPDF, PyMuPDF4LLM, and MarkItDown. Docling remains recommended from the host benchmark, but in this derived image its runtime path can fail if Docling imports `torch`.
- Scanned PDFs or OCR-heavy workflows are not the current sweet spot.

## Decision Summary

Use this layer when the project needs better context preparation than plain full-PDF multimodal ingestion:

- better table structure,
- better retrieval,
- page and table coordinates,
- inspectable artifacts,
- selective visual prompting.

Do not use it as a way to reduce whole-document token cost. For whole-document "read everything visually" tasks, full-PDF multimodal ingestion can still be cheaper. The canonical bundle wins when the downstream system retrieves and sends only the relevant structured packets and content assets.
