# PDF Markdown Grading Rubric

This rubric is the canonical grading contract for the CPU-only PDF backend benchmark in this repository.

The benchmark separates:

- full-document conversions for speed measurement
- short excerpt-PDF conversions for quality grading

The grading model should compare each engine's markdown output against the rendered reference images for the same excerpt PDF. Score only what appears in the excerpt outputs. Do not infer quality from tool reputation, benchmark claims, or speed.

When a target declares `focus_dimensions`, score only those dimensions for that target.

- Return `null` for every non-focus dimension.
- Use evidence text `N/A - not scored for this target` for every non-focus dimension.
- Do not open or read the full original PDF or any full-document markdown output during grading.

## Scale

Use a 0-5 integer score for every scored dimension.

- `0`: unusable or missing
- `1`: major defects; only fragments are trustworthy
- `2`: partially usable but with frequent structural errors
- `3`: acceptable baseline; useful with visible limitations
- `4`: strong result with only minor defects
- `5`: excellent result; very close to the document's visible structure

`formulas` may be `N/A` when an excerpt has no meaningful formula content. In JSON outputs, represent `N/A` as `null`.

Every scored dimension must include one sentence of evidence.

## Dimensions And Weights

### `reading_order` (25%)

Judge whether the text follows the visible page flow.

- Reward correct column order, paragraph continuity, list order, caption placement, and avoidance of header/footer pollution.
- Penalize column interleaving, broken sentence order, duplicated blocks, and severe reading-order jumps.

### `headings` (20%)

Judge whether headings and section structure remain legible and useful.

- Reward clear heading boundaries, preserved hierarchy, and separation between section titles and body text.
- Penalize flattened structure, headings merged into paragraphs, or spurious heading promotion.

### `tables` (30%)

Judge whether tables are reconstructed in a way that an agent can actually use.

- Reward preserved row/column relationships, header association, multi-line cell coherence, and readable markdown or text structure.
- Penalize flattened cells, row scrambling, lost headers, or outputs that force the reader to reconstruct the table manually from noise.

### `formulas` (10%)

Judge whether mathematical content survives in a meaningful form when formulas are present.

- Reward correct symbol preservation, coherent inline math, readable display equations, or useful LaTeX-style extraction.
- Penalize dropped formulas, placeholder spam, mangled symbols, or text that makes the formula content unreliable.

### `usability` (15%)

Judge the overall usefulness of the excerpt output for an agent consuming the markdown.

- Reward outputs that are easy to scan, minimally noisy, and good enough for downstream reasoning without heavy cleanup.
- Penalize outputs that are technically present but hard to trust, cluttered, or likely to mislead an agent.

## Aggregation

Per excerpt:

- Compute the weighted average over the scored dimensions only.
- Exclude `formulas` from the denominator when it is `N/A`.
- Convert the weighted 0-5 result to a 0-100 quality score.

Per document and per engine:

- Average the excerpt quality scores for that document.

Overall per engine:

- Average the document-level quality scores.

## Required JSON Shape

Each grading result should be returned as JSON with this structure:

```json
{
  "status": "completed",
  "document_id": "gfsm_manual",
  "model": "gpt-5.4-mini",
  "excerpts": [
    {
      "excerpt_id": "front_excerpt",
      "engines": {
        "markitdown": {
          "scores": {
            "reading_order": 3,
            "headings": 3,
            "tables": 1,
            "formulas": null,
            "usability": 3
          },
          "evidence": {
            "reading_order": "One sentence.",
            "headings": "One sentence.",
            "tables": "One sentence.",
            "formulas": "N/A",
            "usability": "One sentence."
          }
        }
      }
    }
  ]
}
```
