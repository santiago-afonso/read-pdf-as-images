---
id: rp-6eda
status: in_progress
deps: []
links: []
created: 2026-02-18T15:26:13Z
type: feature
priority: 1
assignee: Santiago Afonso
---
# Add tiktoken-based text output truncation across all modes

Implement shared truncation for every text output mode in read-pdf (regex search, TOC, text-fast, layout/full text and related text paths). Default cap ~16k tokens counted via tiktoken, overridable via CLI flag. Truncation notice must include estimated total tokens and configured limit.\n\nMinimal test plan:\n- unit tests for truncation helper (under/over/exact/override)\n- integration-style CLI tests for search, toc, text-fast, text-layout/full pathways\n- verify non-text modes (e.g., images/json payloads as applicable) remain unchanged unless explicitly text output

## Acceptance Criteria

All text-producing CLI modes are capped by default at ~16k tiktoken tokens with a clear truncation message that reports estimated total tokens and configured limit; users can override via a CLI flag; tests cover each mode and pass.


## Notes

**2026-02-18T15:30:17Z**

Implemented shared stdout truncation via scripts/truncate_text_output.py (default 16384 tokens, --max-output-tokens override, truncation notice with estimated total tokens). Routed all read-pdf modes through post-processing in main() and added tests/test_output_truncation.py covering text-fast, text-precise, raw, toc, toc-pages, table-pages, chart-pages, search, and disable-with-zero behavior. Verified with pytest + bash -n + direct tiktoken smoke.
