---
id: rp-cafe
status: closed
deps: []
links: []
created: 2026-02-03T14:31:30Z
type: task
priority: 2
assignee: Santiago Afonso
---
# Fix read-pdf jpeg output extension

Bug: read-pdf --as-images --format jpeg invokes pdftoppm -jpeg, which writes .jpg files, but the script expected .jpeg and errored. Fix by normalizing output extension to .jpg while preserving --format jpeg in manifest.

## Acceptance Criteria

Running read-pdf <pdf> --as-images --format jpeg successfully produces page-XXX.jpg and emits a manifest without errors.

