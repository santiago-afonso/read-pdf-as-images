---
id: rp-c1da
status: closed
deps: []
links: []
created: 2026-06-07T13:07:00Z
type: task
priority: 1
assignee: Santiago Afonso
---
# Add structured PDF table/image benchmark

Intent/problem statement:
Build a second PDF benchmark focused on LLM ingestion of complex tables and bitmap visual content. The existing benchmark covers regular text/markdown quality; this follow-on should benchmark each capable engine's best structured conversion for faithful tables plus exported images. JSON is preferred over HTML, HTML over Markdown, but fidelity matters more than format. Images are a hard requirement: engines incapable of extracting or exporting bitmap visual content should be dropped from this benchmark.

Scope:
- Add a new table/image-focused benchmark, implemented as a separate/copy-style harness if that keeps complexity lower.
- Select engines that can produce structured table output (JSON, HTML, or best Markdown) and extract/export images. Include markitdown based on colleague validation, plus Docling and other capable engines identified from current runner/docs.
- Prefer output formats in this order: JSON > HTML > Markdown.
- Preserve existing text benchmark behavior unless narrowly necessary for shared utilities.
- Add or resolve benchmark corpus PDFs: the DPF set including Ecuador/Bhutan examples referenced by the Marp slide deck, add Windows path C:\Users\wb620566\Downloads\P162302 PAD.pdf via wslpath, and add at least one IMF Article IV from ~/imf-article-ivs/_downloads for a large country likely to include LIC DSA-style analysis.
- Run the table/image benchmark against the selected corpus.
- Visually assess resulting conversion artifacts with multimodal inspection for image extraction, comprehensible layout, and table structure correctness.

Non-goals:
- Do not optimize for backward compatibility with old benchmark artifacts.
- Do not preserve engines in this benchmark if they cannot export images.
- Do not run broad/full test suites unless specifically needed.

Risks/tradeoffs:
- Several engine CLIs are moving targets and may need best-effort command overrides or output normalization.
- Image export semantics differ: embedded image extraction, page screenshots, and figure/table crops are not identical. The benchmark should record what each engine actually produced.
- Full corpus conversion may be slow, especially Docling/Unstructured-style lanes.
- Some source PDFs may be absent on this machine; missing corpus sources should be surfaced clearly with alternatives where possible.

Indicative files likely to change:
- benchmark/pdf/*table* or benchmark/pdf/*structured* benchmark files
- benchmark/pdf/mass_compare_container/ or a new container runner copy
- scripts/* benchmark launcher
- README.md / benchmark docs
- tests for engine selection, output format preference, corpus resolution, and result summaries
- .tickets/<this ticket>


## Acceptance Criteria

Acceptance criteria:
1. Repo contains a runnable second benchmark for table/image-focused conversion that is distinct from the existing regular text benchmark.
2. Benchmark engine matrix includes only engines with image-export capability, including markitdown, docling, and the other viable local engines; skipped engines are documented with reasons.
3. Each engine uses its best available format by preference JSON > HTML > Markdown, with image assets copied/referenced in its output directory.
4. Corpus resolution includes Ecuador/Bhutan/other DPF examples from the existing artifacts/deck context, the PAD PDF resolved from C:\Users\wb620566\Downloads\P162302 PAD.pdf using wslpath if present, and at least one large-country IMF Article IV PDF selected from ~/imf-article-ivs/_downloads.
5. Running the benchmark writes per-engine outputs, image assets/manifests, timing/result metadata, and a summary under tmp/benchmark/pdf-table-images/<run-id>/ or equivalent.
6. Focused tests or smoke checks verify engine selection/format preference, required image capability filtering, corpus path resolution, and summary/result generation.
7. A real benchmark run is executed on the selected corpus or, if the full run is blocked by missing local files/tools, the blocker is proven with command output and the harness is smoke-tested on available PDFs.
8. Resulting artifacts are visually assessed by Codex/multimodal inspection for: images actually extracted, layout comprehensibility for weaker LLMs, and table structure correctness.



## Notes

**2026-06-07T13:20:45Z**

Implemented host-side table/image benchmark runner/orchestrator, wrapper script, docs, Makefile/README wiring, and focused tests. Verified py_compile, Ruff, list-engines, and default corpus resolution. Corpus now resolves DPF deck extracts, P162302 PAD via Windows path, and an IMF Article IV source; benchmark smoke/full conversion still pending.

**2026-06-07T13:30:28Z**

Completed table/image benchmark implementation and verification. Engine answer: default supported set is markitdown (Markdown plus PyMuPDF sidecar), pymupdf4llm, docling, opendataloader, pdf_oxide, pymupdf, and kreuzberg; optional set is nutrient_pdf_to_markdown and unstructured. Strict HTML/JSON plus image-capable lanes are docling, opendataloader, pymupdf4llm (JSON API with Markdown fallback), pymupdf, kreuzberg (HTML content serialized to JSON), and optional unstructured. EdgeParse was tested and skipped because the installed CLI supports HTML/JSON but exposes no image-export option. Ran dpf-extracts-default-v2 on Ecuador/Bhutan extracts: 14/14 ok. Ran corpus-default-page1-v1 on 12 resolved docs including DPF extracts/full DPFs, P162302 PAD via /mnt/c path, and Ethiopia 2025 IMF Article IV: 84/84 ok. Visual assessment: PyMuPDF page renders are faithful bitmap fallbacks for table/cover pages; extracted-image lanes mostly capture logos/cover imagery; Docling/OpenDataLoader preserve explicit table cell/row metadata; PyMuPDF detects DPF tables; PyMuPDF4LLM is readable Markdown; MarkItDown/PDF Oxide/Kreuzberg are weaker on complex matrix table structure.
