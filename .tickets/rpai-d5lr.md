---
id: rpai-d5lr
status: closed
deps: []
links: [rpai-v934, rpai-ftkp]
created: 2026-04-02T15:18:46Z
type: task
priority: 1
assignee: Santiago Afonso
tags: [pdf, backend, evaluation, docs]
---
# Expand PDF backend evaluation docs with full findings

Expand docs/pdf-backend-evaluation-2026-03-27.md into a full source-of-truth record of the offline backend evaluation. Capture exact corpus, environment, installed versions, commands used, artifact directories, stdout/file-output quirks, per-engine timing results, qualitative findings by backend and PDF type, and the evidence behind the final recommendation. Avoid duplicating conclusions across multiple docs unless necessary; prefer strengthening the existing doc.

## Acceptance Criteria

The evaluation doc includes the full findings from the earlier backend review and OpenDataLoader JRE follow-up, including methodology, artifact locations, command shapes, observed quirks, and a complete timing/quality summary that supports the recommendation.


## Notes

**2026-04-02T15:22:42Z**

Expanded docs/pdf-backend-evaluation-2026-03-27.md into a fuller source-of-truth record.

Added documentation for:
- evaluation chronology and scope boundaries
- canonical corpus files and result directories
- distinction between canonical OpenDataLoader file-backed outputs and the retained zero-byte stdout-path bug artifacts
- corpus preparation details for the WESP table sample and the synthetic scanned sample
- representative command shapes used for smoke tests and OpenDataLoader follow-up runs
- version notes and the docling version nuance between the original run and the later probe
- detailed findings by corpus (scientific paper, table-heavy sample, scanned sample)
- reproduction/interpretation notes for future readers

The underlying recommendation did not change; this was a documentation-completeness pass.
