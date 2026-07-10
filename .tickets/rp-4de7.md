---
id: rp-4de7
status: closed
deps: []
links: []
created: 2026-06-07T21:42:15Z
type: task
priority: 2
assignee: Santiago Afonso
---
# Document canonical token blowup

Intent/problem statement: create a standalone markdown document explaining why the current canonical PDF frankenstack can use more tokens than full-PDF multimodal ingestion, with the concrete DPF/IPF/Article IV measurements.\n\nScope:\n- Add a focused document under benchmark/pdf.\n- Include what was counted and not counted in the current intended payload.\n- Include measured token tables and per-page/ratio interpretation.\n- Explain the specific causes of token blowup and the operational implication.\n\nNon-goals:\n- Do not rerun conversion benchmarks.\n- Do not change the pipeline implementation.\n\nAcceptance criteria:\n1. New markdown document exists.\n2. It includes DPF, IPF/PAD, and Article IV measurements from eval-canonical-full-v1.\n3. It explains the blowup causes in practical terms.\n4. Markdown diff sanity check passes.


## Notes

**2026-06-07T21:44:16Z**

Added benchmark/pdf/canonical_bundle_token_blowup.md summarizing why full-document canonical payloads can exceed full-PDF multimodal token counts. The doc includes the corrected current-frankenstack accounting, DPF/IPF/Article IV measurements, per-page token comparisons, stored/audit bundle contrast, blowup causes, case notes, and practical recommendations. README now references the doc. Verified with git diff --check on the new doc and README.
