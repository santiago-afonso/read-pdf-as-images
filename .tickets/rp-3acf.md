---
id: rp-3acf
status: closed
deps: [rp-4b33]
links: []
created: 2026-07-10T12:39:46Z
type: task
priority: 1
assignee: Santiago Afonso
---
# Harden Docling physical sizing, payload sharding, furniture matching, and continuation metadata

Implement the follow-up improvements from the Docling bitmap-routing review. User outcome: reliable GPU-less bulk PDF conversion with fewer figure false negatives and furniture false positives, explicit multi-page table grouping, and Azure Batch payloads that remain below the 200 MB limit after real serialization. Technical scope: use PDF physical dimensions in picture classification; add a canonical Azure-compatible JSONL serialization measurement; split oversized LLM payloads by page/section while retaining risky-table bitmaps before mechanical fallback; add conservative perceptual-hash furniture suppression constrained by repeated margin geometry; assign continuation_group_id metadata to multi-page table sequences; document the existing Databricks CPU path; add focused tests. Non-goals: automatic Granite/VLM reconstruction, redesigning unrelated benchmark tools, or assuming a provider-specific prompt/schema beyond a small deterministic envelope.

## Acceptance Criteria

Picture records and routing expose/use physical inches/cm from PDF points; exact serialized JSONL bytes are measured against the configured cap; oversized documents are deterministically sharded on page boundaries with each shard independently under budget and risky table bitmaps retained, falling back to mechanical only if an indivisible shard cannot fit; perceptually similar repeated margin furniture is suppressed without suppressing semantic figures; multi-page tables receive stable continuation_group_id values; focused tests and ruff/compile/shell checks pass; README documents the GPU-less Databricks execution path and payload contract; intended repository changes are reviewed, committed in a bulk commit, and pushed.


## Notes

**2026-07-10T12:39:51Z**

Plan: (1) inspect current picture/table/export manifests and CPU batch path; (2) add physical-size metrics and conservative perceptual furniture clustering; (3) assign stable table continuation group IDs; (4) define deterministic JSONL request-envelope serialization and page-bounded shard manifests, retaining risky table bitmaps before mechanical fallback; (5) add tests/docs and targeted runtime checks; (6) review all pending repository changes, stage explicit intended paths, bulk commit, and push. Risks: provider payload schema may differ, so serializer must be explicit and overridable; page sharding can split semantic context, so prefer heading/section boundaries when available and record page ranges; perceptual matching must remain margin- and repetition-constrained.

**2026-07-10T12:49:09Z**

Implementation completed in benchmark/pdf/docling_convert.py and README.md: PDF-point physical dimensions are exposed in inches/cm and used as a routing signal; repeated furniture uses exact hash plus a 3-page/same-margin/compatible-size dHash gate; table continuation chains now receive stable group IDs and sequence positions; LLM blocks retain page provenance; request-ready /v1/chat/completions Batch JSONL is serialized with actual data URLs/prompt/schema and measured exactly; oversized documents are packed into separate page-aligned JSONL shards before tables-only/mechanical fallback; README documents the GPU-less Databricks CPU batch path. Focused tests currently pass 44 tests with Pillow; live Docling 2.111.0 CPU smoke generated a 612-byte exact JSONL request with supplied prompt/schema and status ok.

**2026-07-10T12:50:53Z**

Verification complete: focused Docling suite passes 45 tests; the broader pending PDF-tool batch passes 62 tests before the final physical-size regression addition (the added test then passed in the 45-test rerun); ruff passed across all pending Python implementations/tests; py_compile passed; bash -n passed for all four wrappers; git diff --check passed; shellcheck is unavailable. Live standard Docling 2.111.0 CPU smoke with a real prompt/schema completed status ok and wrote a 612-byte measured Chat Completions Batch JSONL shard. Residual contract note: each generated file is capped independently with 44 MB configured reserve; a later uploader combining many request lines must still shard upload files. Backout: revert the bulk commit; no persistent data migration is involved.

**2026-07-10T12:51:08Z**

Final combined targeted suite rerun after the physical-size regression test: 63 passed.
