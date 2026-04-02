---
id: rpai-e670
status: closed
deps: []
links: []
created: 2026-04-02T22:25:56Z
type: task
priority: 1
assignee: Santiago Afonso
tags: [pdf, read-pdf, runtime]
---
# Replace read-pdf fast runtime backend with pdf_to_markdown

Replace read-pdf's current fast runtime lane from markitdown[pdf] to pdf_to_markdown while keeping the existing pymupdf4llm precise lane unchanged. This is a runtime behavior change, not just a benchmark addition. The default text path and --toc should move to pdf_to_markdown with fail-fast local binary resolution and no hidden markitdown fallback.

Scope:
- Make pdf_to_markdown the default fast text engine in scripts/read-pdf.
- Preserve page-aware TOC extraction by slicing the PDF into one-page temporary PDFs and converting those pages with pdf_to_markdown.
- Keep --as-text-precise-layout-slow and --as-raw-text on pymupdf4llm.
- Update runtime helper logic, install/help text, prime-cache behavior, README, and focused tests.

Non-goals:
- Do not change images/search/page-candidate modes.
- Do not add any API or network-backed behavior.
- Do not keep a compatibility fallback in read-pdf runtime for markitdown.

## Acceptance Criteria

read-pdf default fast text mode uses pdf_to_markdown, --toc still returns page-numbered output via page slicing, runtime fails fast with a clear install hint when the Nutrient binary is missing, and focused docs/tests are updated accordingly.


## Notes

**2026-04-02T22:26:06Z**

Approved plan summary:
- Shift read-pdf's current fast runtime lane from markitdown[pdf] to pdf_to_markdown while keeping the existing pymupdf4llm precise lane unchanged.
- Default text mode uses whole-document pdf_to_markdown for speed.
- Page-aware features that need per-page semantics keep a separate page-sliced path.
- Missing Nutrient binaries fail fast with an explicit install hint; read-pdf must not auto-download at runtime.
- Add documented runtime override env var: PDF_TO_MARKDOWN_BINARY=/abs/path/to/nutrient-binary.
- For normal fast text mode, run pdf_to_markdown once on the full PDF and wrap that markdown in the existing pseudo-XML envelope. Update the <pdf-text> header comment so it truthfully states that explicit PAGE markers are not guaranteed in the fast path.
- Keep --toc page-aware by slicing the original PDF into one-page temporary PDFs, running pdf_to_markdown on each page, rebuilding the per-page text map, and preserving original page numbers in matched_pages and inserted PAGE markers.
- Update scripts/read-pdf help text, README, Makefile help, and read-pdf --prime-cache to stop priming markitdown and instead verify pdf_to_markdown runtime presence without auto-downloading.
- Do not change images mode, search mode, --toc-pages, --table-pages, or --chart-pages.
- Do not keep markitdown as a hidden runtime fallback.

**2026-04-02T22:26:06Z**

Implementation + verification:
- scripts/read-pdf now defaults TEXT_ENGINE to pdf_to_markdown, routes --toc through pdf_to_markdown, emits truthful fast-path metadata comments, and keeps precise/raw-text on pymupdf4llm.
- scripts/read_pdf_text.py now resolves a preinstalled Nutrient binary (PDF_TO_MARKDOWN_BINARY override or ~/.local/share/nutrient/cli/<platform-binary>), converts full documents with pdf_to_markdown for fast mode, and slices one-page temporary PDFs for page-aware TOC extraction. Internal markitdown support remains only for benchmark helper compatibility; read-pdf runtime no longer routes through it.
- README and Makefile were updated to document bootstrap/runtime expectations and prime-cache behavior.
- Focused tests were updated to cover default fast routing, TOC routing, fail-fast missing-binary behavior, and truncation paths.
- Verified with: bash -n scripts/read-pdf; uv run --with ruff ruff check scripts/read_pdf_text.py tests/test_toc_engine_routing.py tests/test_output_truncation.py; uv run --with pytest pytest tests/test_toc_engine_routing.py tests/test_output_truncation.py -q (5 passed); plus real runtime smoke on a benchmark excerpt for default fast mode and --toc.

**2026-04-02T22:29:45Z**

Closing note: read-pdf fast runtime now uses pdf_to_markdown by default, TOC extraction preserves page-numbered output via one-page slicing, docs/help/prime-cache were updated, and focused routing/truncation tests plus real CLI smoke checks passed. Residual caveat: runtime intentionally fails fast unless a local Nutrient binary has already been bootstrapped or PDF_TO_MARKDOWN_BINARY is set.
