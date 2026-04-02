from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from pypdf import PdfWriter


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "benchmark" / "pdf" / "benchmark.py"
CHART_MODULE_PATH = REPO_ROOT / "benchmark" / "pdf" / "chart_speed_vs_table_accuracy.py"


def _load_module_from(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_module():
    return _load_module_from(MODULE_PATH, "pdf_benchmark")


def _load_chart_module():
    return _load_module_from(CHART_MODULE_PATH, "pdf_chart")


def _write_pdf(path: Path, pages: int = 1) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as handle:
        writer.write(handle)


def test_select_article_iv_candidate_prefers_english_then_page_count_then_year() -> None:
    module = _load_module()
    candidates = [
        module.CandidateRecord(
            path=Path("/tmp/article-iv-2026-fr.pdf"),
            page_count=310,
            year=2026,
            english_preferred=False,
        ),
        module.CandidateRecord(
            path=Path("/tmp/article-iv-2024-en.pdf"),
            page_count=280,
            year=2024,
            english_preferred=True,
        ),
        module.CandidateRecord(
            path=Path("/tmp/article-iv-2025-en.pdf"),
            page_count=280,
            year=2025,
            english_preferred=True,
        ),
        module.CandidateRecord(
            path=Path("/tmp/article-iv-2025-z-en.pdf"),
            page_count=280,
            year=2025,
            english_preferred=True,
        ),
    ]

    selected = module.select_article_iv_candidate(candidates, english_only=True)

    assert selected.path.name == "article-iv-2025-en.pdf"


def test_select_excerpt_plans_dedupes_used_pages_and_caps_table_pages() -> None:
    module = _load_module()

    plans = module.select_excerpt_plans(
        20,
        table_pages=[2, 4, 16, 16, 1, 18],
        front_pages=3,
        max_table_pages=2,
        late_fraction=0.8,
        max_excerpts=5,
    )

    assert [plan["excerpt_id"] for plan in plans] == [
        "front_excerpt",
        "table_p0004",
        "table_p0016",
        "midpoint_p0010",
    ]
    assert plans[0]["pages"] == [1, 2, 3]


def test_compute_excerpt_quality_excludes_na_dimensions() -> None:
    module = _load_module()

    quality = module.compute_excerpt_quality(
        {
            "reading_order": 4,
            "headings": 3,
            "tables": 2,
            "formulas": None,
            "usability": 5,
        },
        module.RUBRIC_WEIGHTS,
    )

    expected = ((4 * 0.25) + (3 * 0.20) + (2 * 0.30) + (5 * 0.15)) / (0.25 + 0.20 + 0.30 + 0.15)
    expected = (expected / 5.0) * 100.0
    assert quality == expected


def test_aggregate_grades_rolls_excerpt_scores_up_to_document_and_overall() -> None:
    module = _load_module()

    grades = {
        "status": "completed",
        "documents": [
            {
                "document_id": "doc_a",
                "excerpts": [
                    {
                        "excerpt_id": "front_excerpt",
                        "engines": {
                            "markitdown": {
                                "scores": {
                                    "reading_order": 3,
                                    "headings": 3,
                                    "tables": 2,
                                    "formulas": None,
                                    "usability": 4,
                                }
                            }
                        },
                    }
                ],
            },
            {
                "document_id": "doc_b",
                "excerpts": [
                    {
                        "excerpt_id": "front_excerpt",
                        "engines": {
                            "markitdown": {
                                "scores": {
                                    "reading_order": 4,
                                    "headings": 4,
                                    "tables": 3,
                                    "formulas": None,
                                    "usability": 4,
                                }
                            }
                        },
                    }
                ],
            },
        ],
    }

    summary = module.aggregate_grades(grades, weights=module.RUBRIC_WEIGHTS)

    assert summary["status"] == "completed"
    assert set(summary["document_scores"]) == {"doc_a", "doc_b"}
    assert summary["overall_scores"]["markitdown"] == round(
        (
            summary["document_scores"]["doc_a"]["markitdown"]
            + summary["document_scores"]["doc_b"]["markitdown"]
        )
        / 2,
        2,
    )


def test_load_quality_targets_validates_and_normalizes(tmp_path: Path) -> None:
    module = _load_module()
    source_pdf = tmp_path / "source.pdf"
    _write_pdf(source_pdf, pages=3)
    targets_path = tmp_path / "targets.json"
    targets_path.write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "target_id": "sample_target",
                        "document_id": "doc_1",
                        "document_label": "Document 1",
                        "source_pdf": str(source_pdf),
                        "pages": [2, 2, 3],
                        "label": "Sample target",
                        "target_type": "table_layout",
                        "reason": "Hard table sample",
                        "search_anchor": "Table 1",
                        "focus_dimensions": ["tables", "usability"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    targets = module.load_quality_targets(targets_path)

    assert targets == [
        {
            "target_id": "sample_target",
            "document_id": "doc_1",
            "document_label": "Document 1",
            "source_pdf": str(source_pdf.resolve()),
            "pages": [2, 3],
            "label": "Sample target",
            "target_type": "table_layout",
            "reason": "Hard table sample",
            "search_anchor": "Table 1",
            "focus_dimensions": ["tables", "usability"],
        }
    ]


def test_load_quality_targets_rejects_out_of_range_pages(tmp_path: Path) -> None:
    module = _load_module()
    source_pdf = tmp_path / "source.pdf"
    _write_pdf(source_pdf, pages=2)
    targets_path = tmp_path / "targets.json"
    targets_path.write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "target_id": "bad_target",
                        "document_id": "doc_1",
                        "document_label": "Document 1",
                        "source_pdf": str(source_pdf),
                        "pages": [3],
                        "label": "Bad target",
                        "target_type": "table_layout",
                        "reason": "Invalid page",
                        "search_anchor": "",
                        "focus_dimensions": ["tables"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    try:
        module.load_quality_targets(targets_path)
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("Expected load_quality_targets to reject out-of-range pages")


def test_normalize_timing_rows_casts_numeric_fields() -> None:
    module = _load_module()

    rows = module.normalize_timing_rows(
        [
            {
                "scope": "full",
                "document_id": "doc_1",
                "engine": "markitdown",
                "returncode": "0",
                "pages": "12",
                "seconds": "1.2345",
                "seconds_per_page": "0.102875",
            }
        ]
    )

    assert rows[0]["returncode"] == 0
    assert rows[0]["pages"] == 12
    assert rows[0]["seconds"] == 1.2345
    assert rows[0]["seconds_per_page"] == 0.102875


def test_build_grading_bundle_uses_only_target_local_artifacts(tmp_path: Path) -> None:
    module = _load_module()
    rubric_path = tmp_path / "rubric.md"
    rubric_path.write_text("# Rubric\n", encoding="utf-8")
    image_path = tmp_path / "reference.png"
    image_path.write_bytes(b"png")
    markdown_path = tmp_path / "output.md"
    markdown_path.write_text("table output\n", encoding="utf-8")

    module.build_grading_bundle(
        run_dir=tmp_path,
        excerpt_records=[
            {
                "document_id": "doc_1",
                "document_label": "Document 1",
                "target_id": "sample_target",
                "excerpt_id": "sample_target",
                "label": "Sample target",
                "target_type": "table_layout",
                "reason": "Hard table sample",
                "search_anchor": "Table 1",
                "focus_dimensions": ["tables", "usability"],
                "pages": [2, 3],
                "source_pdf": "/tmp/full-source.pdf",
                "excerpt_pdf": "/tmp/excerpt.pdf",
                "reference_images": [
                    {
                        "page": 2,
                        "path": str(image_path),
                        "dpi": 220,
                        "format": "png",
                        "mode": "images",
                        "tool": "read-pdf",
                        "tool_version": "0.1.3",
                        "engine": "pdftoppm",
                        "pdf_path": "/tmp/full-source.pdf",
                        "pdf_basename": "full-source.pdf",
                    }
                ],
                "engine_outputs": {"markitdown": str(markdown_path)},
            }
        ],
        engines=["markitdown"],
        rubric_path=rubric_path,
        grade_model="gpt-5.4-mini",
    )

    grading_input = json.loads((tmp_path / "grading" / "doc_1" / "grading-input.json").read_text(encoding="utf-8"))
    excerpt = grading_input["excerpts"][0]

    assert "source_pdf" not in excerpt
    assert "excerpt_pdf" not in excerpt
    assert excerpt["reference_images"] == [
        {
            "page": 2,
            "path": str(image_path),
            "dpi": 220,
            "format": "png",
            "mode": "images",
            "tool": "read-pdf",
            "tool_version": "0.1.3",
            "engine": "pdftoppm",
        }
    ]
    assert excerpt["engine_outputs"] == {"markitdown": str(markdown_path)}


def test_build_conversion_command_pdf_to_markdown_uses_override_binary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_module()
    binary_path = tmp_path / "nutrient-linux-amd64"
    binary_path.write_text("", encoding="utf-8")
    binary_path.chmod(0o755)
    input_pdf = tmp_path / "input.pdf"
    output_dir = tmp_path / "out"

    monkeypatch.setenv("PDF_TO_MARKDOWN_BINARY", str(binary_path))

    command, output_mode = module.build_conversion_command(
        "pdf_to_markdown",
        input_pdf=input_pdf,
        output_dir=output_dir,
        expected_pages=1,
        hybrid_url="http://127.0.0.1:5002",
    )

    assert command == [str(binary_path), str(input_pdf), str(output_dir / "output.md")]
    assert output_mode == "fixed_output_file"
    assert module.version_command_for_engine("pdf_to_markdown") == [str(binary_path), "--version"]


def test_collect_engine_metrics_combines_multiple_runs() -> None:
    module = _load_chart_module()
    timing_payloads = [
        [
            {
                "scope": "full",
                "document_id": "doc_1",
                "engine": "markitdown",
                "status": "ok",
                "seconds_per_page": 0.1,
            },
            {
                "scope": "full",
                "document_id": "doc_1",
                "engine": "pdf_to_markdown",
                "status": "ok",
                "seconds_per_page": 0.01,
            },
        ],
        [
            {
                "scope": "full",
                "document_id": "doc_2",
                "engine": "markitdown",
                "status": "ok",
                "seconds_per_page": 0.2,
            }
        ],
    ]
    grade_payloads = [
        {
            "status": "completed",
            "documents": [
                {
                    "document_id": "doc_1",
                    "excerpts": [
                        {
                            "excerpt_id": "t1",
                            "engines": {
                                "markitdown": {"scores": {"tables": 2}},
                                "pdf_to_markdown": {"scores": {"tables": 4}},
                            },
                        }
                    ],
                }
            ],
        },
        {
            "status": "completed",
            "documents": [
                {
                    "document_id": "doc_2",
                    "excerpts": [
                        {
                            "excerpt_id": "t2",
                            "engines": {
                                "markitdown": {"scores": {"tables": 4}},
                            },
                        }
                    ],
                }
            ],
        },
    ]

    metrics = module.collect_engine_metrics(
        timing_payloads=timing_payloads,
        grade_payloads=grade_payloads,
    )

    assert metrics == [
        {
            "engine": "markitdown",
            "mean_seconds_per_page": 0.15,
            "pages_per_second": round(1.0 / 0.15, 4),
            "mean_table_score": 3.0,
            "table_accuracy": 60.0,
            "table_samples": 2,
            "timing_samples": 2,
        },
        {
            "engine": "pdf_to_markdown",
            "mean_seconds_per_page": 0.01,
            "pages_per_second": 100.0,
            "mean_table_score": 4.0,
            "table_accuracy": 80.0,
            "table_samples": 1,
            "timing_samples": 1,
        },
    ]
