---
id: rp-bed6
status: closed
deps: []
links: []
created: 2026-02-02T21:32:00Z
type: chore
priority: 2
assignee: Santiago Afonso
---
# read-pdf: slim --as-images token summary

Remove `gpt-5-mini`/`gpt-5-nano` from the `--as-images` token summary and derive Gemini 3 `media_resolution` from `--dpi` to reduce stdout verbosity.

Test plan:
- `make test PRIME_CACHE=0`
- run `read-pdf tmp/test.pdf --as-images --pages 1 --dpi 180` and confirm stdout is a single concise line

## Acceptance Criteria

- stdout summary includes only `gpt-5` (detail=high) + one Gemini 3 line
- Gemini 3 line uses a single `media_resolution` chosen from `--dpi`
- stderr JSONL per-page manifest unchanged
