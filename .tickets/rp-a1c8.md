---
id: rp-a1c8
status: closed
deps: []
links: []
created: 2026-06-07T21:24:02Z
type: task
priority: 2
assignee: Santiago Afonso
---
# Add canonical bundle adoption handoff

Intent/problem statement: add tight documentation that another repo/agent can use to adopt the canonical PDF bundle pipeline as an LLM context preparation layer. The doc should distinguish stored/audit artifacts from intended LLM payloads and give a practical command/output contract.\n\nScope:\n- Add a concise handoff/adoption markdown under benchmark/pdf.\n- Include prerequisites, commands, output paths, payload-selection rules, token-accounting fields, integration pattern, and caveats.\n- Update README to reference the handoff doc.\n\nNon-goals:\n- Do not package this as an installable library in this change.\n- Do not rerun conversion benchmarks.\n\nAcceptance criteria:\n1. A compact handoff document exists for another agent/repo.\n2. The document explains how to invoke the CLI from another repo and parse outputs.\n3. The document clearly says not to attach full page renders by default.\n4. README links the handoff document.\n5. Markdown/diff sanity checks pass.


## Notes

**2026-06-07T21:25:41Z**

Added benchmark/pdf/canonical_bundle_agent_handoff.md as a concise adoption guide for agents in other repos. It covers prerequisites, cross-repo invocation, output/result.json contract, token fields, payload selection rules, minimal context packet shape, copy-paste agent instructions, limitations, and decision summary. README now references the handoff doc. Verified with git diff --check on the new doc and README.
