---
id: rpai-v934
status: closed
deps: []
links: [rpai-ftkp, rpai-d5lr]
created: 2026-03-27T22:05:16Z
type: task
priority: 1
assignee: Santiago Afonso
tags: [pdf, backend, evaluation]
---
# Evaluate offline PDF text backends to replace markitdown

Intent
Determine whether read-pdf should replace markitdown as the fast/default text backend, given the user constraints: no API calls ever, offline/local execution only, WSL on a Ryzen HX 375, strong preference for speed, better layout flow than markitdown, materially better table extraction, and no appetite for VLM servers, GPU-heavy paths, or high-complexity schemas unless clearly worthwhile.

Scope
- Inspect current read-pdf integration points for markitdown and the contract a replacement backend must satisfy.
- Evaluate docling, opendataloader-pdf, and kreuzberg against those requirements.
- Prefer primary sources (official repos/docs) and practical validation where feasible.
- Assess regular mode and hybrid-mode fit, especially the user's intended use of opendataloader in both.
- Compare expected speed, layout fidelity, table handling, formula handling, offline support, CPU/GPU assumptions, integration complexity, and maintenance risk.
- Produce a recommendation thresholded by the user's standard: replace markitdown only if a candidate strictly dominates on speed x accuracy at similar complexity; otherwise keep markitdown and rely on multimodal/image fallback where needed.

Non-goals
- Do not implement a backend swap in this ticket.
- Do not introduce API-backed services, hosted OCR/VLM systems, or local server orchestration unless only as explicitly rejected reference points.
- Do not run broad benchmark suites unrelated to the repo's likely workloads.

Plan
1. Read the current repo code and docs to understand markitdown's role, text-mode contract, offline/cache behavior, and any hybrid/image interactions.
2. Gather current upstream documentation for docling, opendataloader-pdf, and kreuzberg, focusing on PDF support, local-only operation, table/formula extraction, hardware requirements, and integration surface.
3. If feasible, run bounded local experiments on representative PDFs or minimal smoke tests to check install friction, runtime behavior, and output characteristics relevant to tables/layout.
4. Synthesize the findings into a recommendation with explicit go/no-go criteria for replacing markitdown, plus a suggested engine strategy if no candidate clearly dominates.

Acceptance criteria
- The evaluation states whether any candidate strictly dominates markitdown for this repo's fast/default text path under the stated constraints.
- The evaluation compares all three candidate backends on speed, text/layout quality, tables, formulas, offline/local support, hardware assumptions, and integration complexity.
- The recommendation clearly addresses whether opendataloader should be used in regular mode, hybrid mode, both, or neither.
- The output calls out residual uncertainty and the smallest next experiment that would reduce decision risk if needed.

Verification checklist
- Confirm current markitdown usage and replacement touchpoints from repository code.
- Confirm candidate capabilities from primary upstream sources.
- Where practical, confirm install/runtime behavior locally without API usage.
- Ensure the final recommendation matches the user's replace-only-if-strictly-dominated rule.

Risks and tradeoffs
- Upstream marketing claims may overstate table or formula quality relative to real-world PDFs.
- Some tools may require model downloads or OCR/layout pipelines that erode simplicity or speed even if accuracy improves.
- WSL CPU performance may differ materially from generic upstream benchmarks, and GPU acceleration on the Radeon 890M may not help enough to justify complexity.
- A backend that improves tables but regresses speed or install simplicity may not meet the repo's actual needs.

## Acceptance Criteria

Provide an evidence-based recommendation on whether to replace markitdown, with explicit comparison of docling, opendataloader-pdf, and kreuzberg under offline WSL CPU-first constraints.


## Notes

**2026-03-27T22:22:27Z**

Findings summary
- Current integration points are concentrated in scripts/read_pdf_text.py and scripts/read-pdf; a replacement backend must support predictable page-level extraction, offline local execution via uv-friendly packaging, and low-friction CLI or Python API usage.
- markitdown remains operationally simple, but local output quality on a representative multi-column paper was poor on first-page layout flow and table structure.
- Local smoke tests on 2026-03-27 with attention-is-all-you-need.pdf (15 pages): markitdown via read_pdf_text.py took 6.96s; pymupdf4llm took 5.35s and produced better first-page layout plus some markdown tables; docling 2.82.0 CLI with --no-ocr took 63.74s and produced much better flow/tables; kreuzberg 4.6.3 CLI took 0.94s but flattened tables and showed symbol/encoding issues in formula-heavy sections.
- Docling integration is feasible and page-aware via DoclingDocument.export_to_markdown(page_no=...) plus convert(page_range=...), but clean integration requires API-level configuration (e.g. image placeholders) and likely explicit OCR disabling for born-digital PDFs. Default CLI behavior embeds image data in markdown and default Python conversion triggered RapidOCR model downloads in ad-hoc tests.
- OpenDataLoader is the strongest external candidate on paper. Primary-source docs and the opendataloader-bench repo indicate fast local CPU mode plus a local-only hybrid mode for formulas/charts/tables. However it adds Java 11+ as a hard dependency, warns that each convert() call spawns a JVM, and hybrid mode adds a local backend server and model/runtime footprint. This makes it stronger as an optional engine pair than as a same-complexity default swap.
- Kreuzberg is attractive on install size and speed, and it already supports page markers, CPU mode, and markdown output. But its markdown extraction on the sample paper did not show enough table/formula fidelity to justify replacing markitdown for this repo's markdown-for-agents use case.
- Recommendation direction: do not replace markitdown with docling or kreuzberg as the default today. If willing to accept Java as a first-class dependency, evaluate opendataloader standard/hybrid as optional engines first. Also reconsider whether existing pymupdf4llm routing should be improved before adding a new backend, since it outperformed markitdown on the sample paper.
