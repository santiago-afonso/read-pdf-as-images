---
id: rp-33be
status: closed
deps: []
links: []
created: 2026-02-02T21:05:58Z
type: feature
priority: 2
assignee: Santiago Afonso
---
# read-pdf: estimate gpt-5 vision tokens in --as-images

When rendering pages to images, emit a token estimate summary for GPT-5 family image inputs based on OpenAI docs.

Test plan:
- `make test PRIME_CACHE=0` and ensure the summary is emitted
- run `read-pdf test.pdf --as-images --pages 1` and validate JSONL + summary output

## Acceptance Criteria

- `read-pdf <pdf> --as-images` emits per-page JSONL manifest lines to stderr as before
- additionally prints total + per-page average token estimates to stdout for `gpt-5`, `gpt-5-mini`, `gpt-5-nano`
- does not remove or rename existing JSONL fields
