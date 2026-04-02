---
id: rpai-ftkp
status: closed
deps: []
links: [rpai-v934, rpai-d5lr]
created: 2026-03-27T22:28:18Z
type: task
priority: 1
assignee: Santiago Afonso
tags: [pdf, backend, evaluation, java]
---
# Evaluate OpenDataLoader standard and hybrid with local JRE

Intent
Install a local Java runtime in the WSL environment and run the focused follow-up evaluation proposed in the previous backend review. The goal is to determine whether opendataloader-pdf standard mode and local-only hybrid mode are strong enough to justify becoming optional engines for read-pdf, and whether either comes close enough to revisit the default-backend decision.

Scope
- Install a minimal headless JRE suitable for opendataloader-pdf on Ubuntu 24.04 WSL.
- Select or download three representative PDFs: one scientific paper, one borderless-table-heavy report, and one scanned page set.
- Run opendataloader-pdf standard mode and hybrid mode on those PDFs.
- Compare results against the repo's current backends, at minimum markitdown and pymupdf4llm, and docling where useful.
- Measure one-file invocation latency and inspect output usefulness in the read-pdf-style page-aware markdown context.
- Capture whether hybrid mode is operationally tolerable in practice: install burden, server startup, local model downloads, memory/runtime behavior, and output wins.
- Update docs with the new findings and resulting recommendation.

Non-goals
- Do not modify read-pdf implementation yet.
- Do not introduce cloud or remote API calls.
- Do not optimize GPU acceleration.

Plan
1. Inspect package availability and install a headless JRE with the smallest reasonable system footprint.
2. Prepare the comparison corpus and any helper commands needed for standard and hybrid runs.
3. Install opendataloader-pdf and any local hybrid extras, then run standard mode on all three PDFs.
4. Start the local hybrid backend and run hybrid mode on the same PDFs, including formula-sensitive handling where appropriate.
5. Compare runtime, output quality, and integration complexity against existing engines.
6. Update docs under docs/ with a full addendum or follow-up evaluation note and summarize the recommendation.

Acceptance criteria
- Java is installed and verified locally.
- OpenDataLoader standard and hybrid runs are executed on three representative PDFs.
- Results include runtime observations and qualitative output assessment relevant to read-pdf.
- Docs are updated with a durable written conclusion.
- The final recommendation clearly states whether OpenDataLoader should be pursued as optional engines and whether the default-backend conclusion changes.

Verification checklist
- Confirm java -version after installation.
- Confirm opendataloader-pdf CLI is runnable.
- Confirm hybrid backend starts locally with no remote services.
- Save representative outputs for inspection.
- Compare against current repo backends on the same files.

Risks and tradeoffs
- Installing a system JRE mutates the development environment.
- Hybrid mode may require additional model downloads or server orchestration that change the complexity calculus.
- Some PDF samples may unfairly favor one engine category, so conclusions must stay scoped and explicit.

## Acceptance Criteria

Install a local JRE, run standard and hybrid OpenDataLoader evaluations on three representative PDFs, and document whether the backend should be pursued for read-pdf.


## Notes

**2026-03-27T22:45:28Z**

Installed a user-local Temurin JRE because sudo apt installation was not available in this WSL session.

Verification completed:
- java -version reports Temurin 17.0.18+8
- opendataloader-pdf 2.2.0 standard mode and hybrid mode were run locally on three PDFs
- corpus covered a born-digital scientific paper, a borderless-table-heavy report sample, and a scanned sample
- outputs were saved under tmp/eval/results/

Key findings:
- OpenDataLoader standard mode is genuinely fast and faster than markitdown and pymupdf4llm on the born-digital paper in this test set
- Standard mode improves layout flow over markitdown but still misses on harder tables, formulas, and scanned OCR
- Hybrid mode materially improves formulas and tables, but CPU runtime is much slower and requires a local server plus model downloads
- Default hybrid auto mode did not OCR the scanned sample; force-OCR hybrid full did, but took 71.55s for 3 pages and produced noisy text
- The CLI --to-stdout path produced empty markdown locally, while file-backed output worked

Conclusion:
Do not replace markitdown as the default backend. If optional engines are added, OpenDataLoader standard is the strongest candidate, with hybrid reserved for an explicit slow quality-first lane.

Docs updated:
- docs/pdf-backend-evaluation-2026-03-27.md
