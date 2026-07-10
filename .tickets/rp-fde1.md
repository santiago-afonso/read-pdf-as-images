---
id: rp-fde1
status: closed
deps: []
links: []
created: 2026-06-18T12:08:19Z
type: task
priority: 1
assignee: Santiago Afonso
parent: rp-9c37
---
# Add time-bounded resume mode for sample94 batch

Intent: allow the full 94-file Docling batch to make durable progress in capped CPU/Codex sessions by stopping gracefully after a configured wall-clock budget, writing a partial manifest, and resuming later. Scope: add a batch-sample94 --max-runtime-seconds option checked between documents, record stop_reason and elapsed batch seconds in the batch manifest, and keep per-document rows intact. Non-goals: killing an in-progress Docling conversion mid-document, job scheduler integration, or changing the final CUDA preference.

## Acceptance Criteria

batch-sample94 exposes --max-runtime-seconds; dry-run/test coverage verifies parser/manifest field; a time-bounded run exits cleanly with selected_pdf_count=94 and status partial when not all docs have run, preserving completed rows and allowing --resume.


## Notes

**2026-06-18T12:25:00Z**

2026-06-18 implemented --max-runtime-seconds for batch-sample94, checked between documents, with elapsed_seconds and stop_reason in manifest. Verification passed: ruff check benchmark/pdf/docling_convert.py tests/test_pdf_docling_convert.py; uv run --offline --no-project --with pytest pytest -s -q tests/test_pdf_docling_convert.py (12 passed); py_compile passed. This is now subsumed by the broader full-run ticket rp-9c37.
