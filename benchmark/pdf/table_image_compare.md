# PDF Table/Image Comparator

`table_image_compare.py` is a second benchmark harness for PDFs where LLM ingestion depends on complex table structure and exported bitmap content. It is intentionally separate from the regular text/markdown benchmark because the engine filter is stricter: engines stay in this comparison only when they can produce useful table-oriented output and export image assets, or when they are explicitly retained with a documented sidecar strategy.

Format preference is:

1. JSON
2. HTML or HTML-in-JSON
3. Markdown

Fidelity matters more than the label on the file. Each run records the format actually produced, output bytes, image count, image bytes, and per-engine stdout/stderr.

## Included Engines

Default engines:

- `markitdown`: Markdown conversion plus a PyMuPDF embedded-image sidecar. MarkItDown is retained because it was locally validated by a colleague, even though image export is not native in the converter path.
- `pymupdf4llm`: JSON with `write_images` when available, falling back to Markdown with images if the installed API has changed.
- `docling`: Docling JSON with referenced image export.
- `opendataloader`: OpenDataLoader JSON with external PNG image output.
- `pdf_oxide`: PDF Oxide Markdown with external image output.
- `pymupdf`: Low-level PyMuPDF JSON, detected tables, embedded images, and page renders.
- `kreuzberg`: JSON payload containing HTML content plus extracted image files through Kreuzberg's Python API.

Optional engines:

- `nutrient_pdf_to_markdown`: Markdown plus `--enable-image-export`, using a separately installed Nutrient/PSPDFKit binary.
- `unstructured`: hi-res JSON partition output with table HTML plus extracted image/table blocks. This is deliberately optional because it can require heavier local model/runtime assets.

Skipped engines are documented in the generated `summary.md`; examples include text-only extractors (`pdfminer.six`, `pypdf`, Poppler `pdftotext`), table-only tools without document image export (`camelot`, `tabula-java`), EdgeParse's installed CLI because it has HTML/JSON output but no image export option, and low-level libraries that do not provide a reliable standalone table-plus-image conversion lane for this harness.

## Corpus Resolution

The default corpus resolver looks for:

- DPF extracts from `artifacts/dpf_markdown_failure_deck/assets/`:
  - Ecuador DPF2 page 58
  - Bhutan DPC3 pages 51-52
- Additional DPF/DPC/DPO/Development Policy PDFs under local DRM Policy Platform image-bank artifacts.
- `C:\Users\wb620566\Downloads\P162302 PAD.pdf`, resolved through `wslpath` when available.
- One IMF Article IV PDF from `~/imf-article-ivs/_downloads`, preferring large LIC/IDA-relevant countries such as Ethiopia, Bangladesh, Kenya, Ghana, Zambia, Mozambique, Pakistan, Nigeria, Tanzania, and Rwanda.

Missing corpus sources are reported in `corpus-resolved.json`; present sources receive page counts and SHA-256 hashes.

## Commands

List available engines and the skipped-engine rationale:

```bash
scripts/pdf-table-image-compare list-engines
```

Resolve the default corpus without running conversions:

```bash
scripts/pdf-table-image-compare resolve-corpus \
  --output tmp/benchmark/pdf-table-images/corpus-resolved.json
```

Run the default engine set on the default corpus:

```bash
scripts/pdf-table-image-compare run
```

Run a small smoke test on explicit PDFs:

```bash
scripts/pdf-table-image-compare run \
  --pdf ecuador=/home/safonso/DRM_Policy_Platform/artifacts/dpf_markdown_failure_deck/assets/ecuador_dpf2_page_058_only.pdf \
  --pdf bhutan=/home/safonso/DRM_Policy_Platform/artifacts/dpf_markdown_failure_deck/assets/bhutan_dpc3_pages_051_052.pdf \
  --engines markitdown,pymupdf4llm,pymupdf \
  --run-id smoke-table-images
```

Run a subset of pages for every PDF:

```bash
scripts/pdf-table-image-compare run \
  --corpus-json tmp/benchmark/pdf-table-images/corpus-resolved.json \
  --engines default \
  --pages "1-3,7"
```

## Artifact Layout

Runs are written to `tmp/benchmark/pdf-table-images/<run-id>/` by default:

- `corpus-resolved.json`: source corpus payload used for the run.
- `inputs/`: copied or page-subsetted PDFs passed to engines.
- `conversions/<document-id>/<engine>/`: primary output, image assets, stdout/stderr, and per-engine `result.json`.
- `timings.csv`: flat conversion result table.
- `manifest.json`: full run metadata, engine specs, source documents, and rows.
- `summary.md`: human-readable result table plus skipped-engine rationale.

Each engine directory includes `image_manifest.json` when the runner reaches image scanning. Image counts are mechanical counts of bitmap files under that engine directory; visual review is still required to decide whether those images are useful page renders, figures, crops, tables, or noisy artifacts.
