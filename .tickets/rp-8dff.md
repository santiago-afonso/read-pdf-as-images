---
id: rp-8dff
status: closed
deps: [rp-65b0]
links: []
created: 2026-06-18T11:13:15Z
type: task
priority: 2
assignee: Santiago Afonso
parent: rp-53b1
---
# Add sample94 Docling batch runner and summary manifest

Intent: run the wrapper over the 94-file source PDF set without manual one-off commands. Scope: add or extend a batch CLI that discovers PDFs under /home/safonso/IDA21/2026m06-run01/sample94_strict_pdf/source_pdfs, runs the practical Docling profile(s), records per-file status/timing/table/image/complexity counts, supports resume/skip-existing, and writes a CSV/JSONL summary. Non-goals: expensive Granite 4.1 default runs or Databricks execution.

## Acceptance Criteria

A dry-run lists the 94 target PDFs. A small --limit run succeeds and writes a batch manifest. The command shape for the full 94-file run is documented in the ticket and summary output includes status, elapsed time, page/table/picture counts, complex_table_count, bitmap_required_count, and output paths.


## Notes

**2026-06-18T11:34:35Z**

2026-06-18 batch runner implemented as scripts/pdf-docling-convert batch-sample94. It discovers PDFs under /home/safonso/IDA21/2026m06-run01/sample94_strict_pdf/source_pdfs, supports --dry-run, --limit, --offset, --resume, --skip-existing, writes manifest.json, batch_summary.csv, and batch_results.jsonl with status/timing/page/table/picture/complexity/bitmap/LLM paths. Verified dry-run all: tmp/docling-convert/sample94-dry-run-all discovered and selected 94 PDFs. Verified small limit run on CPU because CUDA is not visible in this sandbox: PDF_DOCLING_UV_OFFLINE=1 scripts/pdf-docling-convert batch-sample94 --limit 1 --pages 1-2 --device cpu --output-dir tmp/docling-convert/sample94-limit1-pages1-2-cpu --fail-fast, status ok.
