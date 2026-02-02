---
id: rp-23ab
status: closed
deps: []
links: []
created: 2026-02-02T21:28:16Z
type: feature
priority: 2
assignee: Santiago Afonso
---
# read-pdf: add Gemini 3 image token estimates

Add Gemini 3 family multimodal token estimates for image inputs when running `--as-images`, based on Google Gemini token counting docs.

Test plan:
- `make test PRIME_CACHE=0` and ensure stdout includes gemini summary lines
- run `read-pdf tmp/test.pdf --as-images --pages 1` and verify stderr JSONL unchanged

## Acceptance Criteria

- stdout token summary includes Gemini 3 family models in addition to GPT-5 family
- per-page JSONL manifest on stderr remains unchanged (same fields, one line per page)
