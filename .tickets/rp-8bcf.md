---
id: rp-8bcf
status: in_progress
deps: [rp-8dff, rp-9c37, rp-835c, rp-6904]
links: [rp-4b33, rp-53b1]
created: 2026-06-18T11:13:15Z
type: task
priority: 2
assignee: Santiago Afonso
parent: rp-53b1
---
# Evaluate sample94 Docling bitmap/table outputs

Intent: sample converted documents and judge whether the HTML/Markdown+bitmap policy produces usable LLM inputs. Scope: evaluate P174008, at least one document that references a figure/diagram, and additional documents flagged by the complexity utility; inspect rendered Markdown/HTML and manifests; record failure modes and recommended defaults. Non-goals: formal benchmark or exhaustive human review of all 94 files.

## Acceptance Criteria

Evaluation note identifies sampled document IDs/paths, why each was sampled, whether figures/complex tables were preserved as referenced bitmaps, whether simple tables remained HTML tables, and what failed. P174008 is explicitly evaluated. At least one figure/diagram document is explicitly evaluated.


## Notes

**2026-06-18T11:38:38Z**

2026-06-18 preliminary evaluation completed before the full all-94 batch. P174008 complex-table smoke: source /home/safonso/IDA21/2026m06-run01/sample94_strict_pdf/source_pdfs/005_dpl_dpo_dpf_main_P174008_RUNV3CTX_P174008_001.pdf pages 33-35, output tmp/docling-convert/p174008-smoke-standard-cpu-wrapper. Result: standard-tableformer status ok; 3 tables detected; all 3 scored complex and rendered as referenced table bitmaps in the LLM Markdown; 3 tiny/page-furniture pictures were skipped. The table bitmap preserves merged/grouped policy-matrix structure, but structured extraction remains unsuitable as sole representation. Figure sample: source /home/safonso/IDA21/2026m06-run01/sample94_strict_pdf/source_pdfs/004_dpl_dpo_dpf_main_P174064_RUNV3CTX_P174064_001.pdf page 6, output tmp/docling-convert/figure-smoke-004-p6-cpu, rendered page tmp/pdf_renders/004_p174064_p6/page-006.png. Result: 2 pictures detected; tiny 50x51 page furniture skipped; chart captioned 'Figure 1. COVID-19 has spread rapidly in Somalia' kept as referenced bitmap ../images/picture-0002.png. Simple-table check used synthetic fixture tmp/docling-fixtures/simple_table.pdf, output tmp/docling-convert/simple-table-smoke: 1 simple table, simple_table_html_count=1, LLM Markdown contains inline HTML <table>. Runtime caveat: current sandbox cannot see CUDA (nvidia-smi rc 255; torch_cuda_available false), so these evaluation conversions used --device cpu and are provisional for performance/CUDA conclusions. Full all-94 run remains tracked in rp-9c37 before final evaluation closure.

**2026-06-18T13:00:13Z**

2026-06-18 evaluation observation: existing lean smoke artifacts confirm P174008 pages 33-35 renders 3/3 complex tables as page-width table bitmaps and simple-table fixture keeps one lean HTML table. Figure smoke for P174064 page 6 kept the semantic COVID-19 chart as a page-width bitmap and skipped the tiny icon, but also duplicated chart-internal OCR/caption/source text after the image; this triggered a code fix under rp-6904 to skip text items inside kept bitmap zones. Need rerun the figure smoke with patched code before closing final evaluation.

**2026-06-18T13:02:47Z**

2026-06-18 Opus feedback incorporated: do not close final evaluation against CPU/pre-fix smoke artifacts alone. Final evaluation must sample from post-rp-835c/post-rp-6904 CUDA batch artifacts under tmp/docling-convert/sample94-full-standard-cuda-lean; P174008 and at least one figure document remain required, with crop completeness/rightmost-column checks.

**2026-06-18T13:06:17Z**

2026-06-18 patched figure evaluation: reran P174064 page 6 figure smoke at tmp/docling-convert/figure-smoke-004-p6-lean-v4 using CPU/no-picture-description to avoid GPU batch contention. Result ok: 2 pictures detected, 1 semantic picture bitmap retained, tiny icon skipped, LLM HTML has only a plain img tag for Figure 1 and no class/style/data/Meta/classifier output; chart-internal duplicated caption/source/date text removed via covered_text_skipped_count=3. Visual check confirms the chart crop includes title, right labels, x-axis, and source. P174008 table crop visual check confirms rightmost Baseline/Target columns are included in page-width table bitmap; page-spanning vertical continuation remains represented by subsequent table/page-width bitmaps.

**2026-06-18T13:11:11Z**

2026-06-18 post-fix CUDA P174008 evaluation: full-batch artifact /home/safonso/read-pdf/tmp/docling-convert/sample94-full-standard-cuda-lean/005_dpl_dpo_dpf_main_P174008_RUNV3CTX_P174008_001/standard-tableformer completed ok in the CUDA batch. Document summary: 47 pages, 14 tables, 11 complex table bitmaps, 3 simple HTML tables, 54 picture records, 3 picture bitmaps. LLM Markdown/HTML sizes ~119K/123K; Markdown contains 14 image refs total (11 tables + 3 figures) and 3 inline HTML tables. Lean HTML grep found no class/style/data/Meta/classification output. Visual check of Annex 1 policy matrix page-width table bitmaps table-0012/0013/0014 confirms rightmost Baseline/Target columns are included; vertical page-spanning continuation is represented across successive bitmaps. Residual limitation: this is visual preservation, not reliable structured extraction of merged policy-matrix cells.

**2026-06-18T14:43:10Z**

2026-06-18 user-requested extension: evaluate CPU-safe Docling bitmap-first extraction on one representative PDF each for DPF/DPL/DPO, PER/PFR, PforR, IPF, and IMF Article IV chosen from repositories/workspaces under /home/safonso rather than relying only on sample94. For each, convert with the lightweight standard-tableformer lane unless a page-limited Granite-Docling lane is needed, visually compare source pages against retained table/figure bitmaps, and inspect whether LLM HTML can be made leaner. Do not run local CUDA.

**2026-06-18T15:25:02Z**

2026-06-18 cross-document CPU-safe visual smoke completed on one representative PDF each from local repos: DPF /home/safonso/wbg-dnr-fetcher/out_dpf/BEN/DPF-BEN-2024-10-03-P181457.pdf; PER /home/safonso/wbg-dnr-fetcher/out_per_pfr/BTN/full/PER-BTN-2024-04-05.pdf; PforR /home/safonso/databricks/artifacts/world_bank_docs_sample_20260609/projects/pforr/P174484_Pakistan_Crisis-Resilient_Social_Protection_CRISP/pad/32901147_Pakistan_-_Crisis-Resilient_Social_Protection_CRISP_Program-for-Results_Project.pdf; IPF /home/safonso/databricks/artifacts/world_bank_docs_sample_20260609/projects/ipf/P175768_Benin_Vocational_Education_and_Entrepreneurship_for_Jobs_Project/pad/33653494_Benin_-_Vocational_Education_and_Entrepreneurship_for_Jobs_Project.pdf; IMF Article IV /home/safonso/imf-article-ivs/_downloads/BLZ-2025-1934-7685-002.2025.issue-262-en.pdf. Full-document standard TableFormer CPU/no-OCR conversions completed under tmp/docling-convert/crossdoc-20260618-standard. Counts: DPF 92p/42 tables/26 table bitmaps/16 simple HTML tables/14 picture bitmaps; PER 140p/48 tables/7 table bitmaps/41 simple tables/93 picture bitmaps; PforR 54p/38 tables/21 table bitmaps/17 simple tables/0 picture bitmaps; IPF 118p/61 tables/27 table bitmaps/34 simple tables/8 picture bitmaps; IMF 115p/37 tables/25 table bitmaps/12 simple tables/28 picture bitmaps. Visual audit found DPF and PforR sampled hard tables were included, but initial misses on PER p121 chart cluster, IPF p100 captioned table, and IMF p32-34 chart panels, p50 spanning DSA table, p72 medium chart, p105 captioned table. Code was patched to promote clustered small picture fragments, add captioned-table fallback crops, treat rowspan/colspan as critical table-complexity evidence, and retain medium semantic images; focused reruns under tmp/docling-convert/crossdoc-20260618-fallback-rerun visually confirm those misses are fixed. HTML was further leaned by outputting bare text blocks and bare img tags, and stripping nonsemantic tbody/thead/tfoot/div wrappers while retaining semantic table/tr/th/td/caption and row/col spans; smoke rerun tmp/docling-convert/html-lean-smoke-imf-p105 produced a 0-<p>, 0-class/style/data HTML image-only fallback. Residual limitation: not a formal exhaustive benchmark; a table with no Docling TableItem and no recognizable caption can still be missed, and full five-document artifacts predate the late fixes except for focused page reruns.
