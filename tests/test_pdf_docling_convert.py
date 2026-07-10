from __future__ import annotations

import importlib.util
import io
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "benchmark" / "pdf" / "docling_convert.py"


def load_module():
    spec = importlib.util.spec_from_file_location("pdf_docling_convert", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_expand_profiles_approved_smoke_uses_standard_plus_granite_docling() -> None:
    module = load_module()
    profiles = module.expand_profiles("approved-smoke")
    assert profiles == [
        "standard-tableformer",
        "vlm-granite-docling",
    ]
    lanes = {module.profile_conceptual_lane(profile) for profile in profiles}
    assert lanes == {
        "standard-table-structure",
        "vlm-granite-docling",
    }


def test_expand_profiles_all_keeps_large_granite_variant_explicit() -> None:
    module = load_module()
    profiles = module.expand_profiles("all")
    assert profiles == [
        "standard-tableformer",
        "vlm-granite-docling",
        "vlm-granite-vision-small",
        "vlm-granite-vision-large",
    ]


def test_parse_page_selection_requires_one_contiguous_docling_range() -> None:
    module = load_module()
    selection = module.parse_page_selection("33-35")
    assert selection.start == 33
    assert selection.end == 35
    assert selection.docling_range == (33, 35)
    assert selection.page_count == 3

    try:
        module.parse_page_selection("1,3")
    except ValueError as exc:
        assert "contiguous" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("non-contiguous pages should fail")


def test_smoke_p174008_defaults_are_hard_case_paths_and_matrix_pages(tmp_path) -> None:
    module = load_module()
    parser = module.build_parser()
    args = parser.parse_args(["smoke-p174008", "--output-dir", str(tmp_path)])

    assert args.input is None
    assert args.reference_html is None
    assert args.pages is None

    # Check constants without running conversion.
    assert module.P174008_SOURCE_PDF.name == "005_dpl_dpo_dpf_main_P174008_RUNV3CTX_P174008_001.pdf"
    assert "opendataloader_native_clean_novlm" in str(module.P174008_REFERENCE_HTML)
    assert module.P174008_DEFAULT_PAGES == "33-35"


def test_build_parser_requires_convert_output_dir() -> None:
    module = load_module()
    # main() handles the parser.error path; the parser itself keeps output_dir optional
    # so smoke-p174008 can default it.
    parser = module.build_parser()
    args = parser.parse_args(["convert", "--input", "/tmp/example.pdf", "--output-dir", "/tmp/out"])
    assert args.input == "/tmp/example.pdf"
    assert args.output_dir == "/tmp/out"
    assert args.image_mode == "referenced"
    assert args.device == "cuda"
    assert args.no_ocr is False
    assert args.ocr_engine == "easyocr"
    assert args.profiles == "approved-smoke"
    assert args.no_picture_classification is True
    assert args.no_picture_description is True
    assert args.enrich_code is False
    assert args.enrich_formula is False


def test_parser_can_explicitly_enable_optional_enrichments() -> None:
    module = load_module()
    parser = module.build_parser()
    args = parser.parse_args(
        [
            "convert",
            "--input",
            "/tmp/example.pdf",
            "--output-dir",
            "/tmp/out",
            "--picture-classification",
            "--picture-description",
            "--enrich-code",
            "--enrich-formula",
        ]
    )

    assert args.no_picture_classification is False
    assert args.no_picture_description is False
    assert args.enrich_code is True
    assert args.enrich_formula is True


def test_batch_sample94_defaults_to_standard_lane_and_dry_run_manifest(tmp_path) -> None:
    module = load_module()
    source_dir = tmp_path / "source_pdfs"
    source_dir.mkdir()
    for name in ["b.pdf", "a.pdf"]:
        (source_dir / name).write_bytes(b"%PDF-1.4\n")
    output_dir = tmp_path / "batch"

    parser = module.build_parser()
    args = parser.parse_args(
        [
            "batch-sample94",
            "--source-dir",
            str(source_dir),
            "--output-dir",
            str(output_dir),
            "--dry-run",
            "--limit",
            "1",
            "--max-runtime-seconds",
            "5",
        ]
    )

    assert args.profiles == "standard"
    assert args.max_runtime_seconds == 5
    assert args.func(args) == 0
    manifest = module.read_json_if_exists(output_dir / "manifest.json")
    assert manifest is not None
    assert manifest["status"] == "dry_run"
    assert manifest["discovered_pdf_count"] == 2
    assert manifest["selected_pdf_count"] == 1
    assert manifest["max_runtime_seconds"] == 5
    assert manifest["document_timeout_seconds"] == 1800.0
    assert manifest["no_ocr"] is True
    assert manifest["inputs"][0]["document_id"] == "a"
    assert (output_dir / "batch_summary.csv").is_file()
    assert (output_dir / "batch_results.jsonl").is_file()


def test_generic_batch_accepts_args_and_input_list_dry_run(tmp_path) -> None:
    module = load_module()
    pdf_a = tmp_path / "a" / "report.pdf"
    pdf_b = tmp_path / "b" / "report.pdf"
    pdf_a.parent.mkdir()
    pdf_b.parent.mkdir()
    pdf_a.write_bytes(b"%PDF-1.4\n")
    pdf_b.write_bytes(b"%PDF-1.4\n")
    input_list = tmp_path / "inputs.txt"
    input_list.write_text(f"\n# comment\n{pdf_b}\n", encoding="utf-8")
    output_dir = tmp_path / "batch"

    parser = module.build_parser()
    args = parser.parse_args(
        [
            "batch",
            str(pdf_a),
            "--input-list",
            str(input_list),
            "--output-dir",
            str(output_dir),
            "--device",
            "cpu",
            "--jobs",
            "2",
            "--dry-run",
        ]
    )

    assert args.func(args) == 0
    manifest = module.read_json_if_exists(output_dir / "manifest.json")
    assert manifest is not None
    assert manifest["batch_kind"] == "generic"
    assert manifest["profiles"] == ["standard-tableformer"]
    assert manifest["jobs"] == 2
    assert manifest["no_ocr"] is True
    assert [item["input_pdf"] for item in manifest["inputs"]] == [str(pdf_a.resolve()), str(pdf_b.resolve())]
    assert manifest["inputs"][0]["document_id"].startswith("report-")
    assert manifest["inputs"][1]["document_id"].startswith("report-")
    assert manifest["inputs"][0]["document_id"] != manifest["inputs"][1]["document_id"]


def test_generic_batch_reads_piped_stdin(monkeypatch, tmp_path) -> None:
    module = load_module()
    pdf = tmp_path / "stdin.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(str(pdf) + "\n"))

    parser = module.build_parser()
    args = parser.parse_args(
        [
            "batch",
            "--output-dir",
            str(tmp_path / "batch"),
            "--device",
            "cpu",
            "--dry-run",
        ]
    )

    assert args.func(args) == 0
    manifest = module.read_json_if_exists(tmp_path / "batch" / "manifest.json")
    assert manifest is not None
    assert manifest["inputs"][0]["input_pdf"] == str(pdf.resolve())
    assert manifest["jobs"] == 2
    assert manifest["worker_threads"] == 1
    assert manifest["no_ocr"] is True
    assert manifest["profiles"] == ["standard-tableformer"]
    assert args.device == "cpu"
    assert args.no_ocr is True


def test_generic_batch_refuses_parallel_non_cpu_without_stress_ack(tmp_path) -> None:
    module = load_module()
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    parser = module.build_parser()
    args = parser.parse_args(
        [
            "batch",
            str(pdf),
            "--output-dir",
            str(tmp_path / "batch"),
            "--device",
            "cuda",
            "--jobs",
            "2",
        ]
    )

    try:
        args.func(args)
    except module.DoclingConvertError as exc:
        assert "CPU-only" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("parallel CUDA batch should require explicit stress acknowledgement")


def test_generic_batch_runs_document_subprocesses_up_to_jobs(tmp_path, monkeypatch) -> None:
    module = load_module()
    pdfs = []
    for index in range(3):
        pdf = tmp_path / f"{index}.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        pdfs.append(pdf)
    output_dir = tmp_path / "batch"
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_run_document_convert_subprocess(args, *, input_pdf, document_dir):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        module.write_json(
            document_dir / "manifest.json",
            {
                "status": "ok",
                "input_pdf": str(input_pdf),
                "results": [
                    {
                        "status": "ok",
                        "profile": "standard-tableformer",
                        "artifact_summary": {"structure": {"page_count_in_document": 1}},
                    }
                ],
            },
        )
        log_path = document_dir / "logs" / "convert-subprocess.log"
        module.write_text(log_path, "ok\n")
        with lock:
            active -= 1
        return 0, log_path, None

    monkeypatch.setattr(module, "run_document_convert_subprocess", fake_run_document_convert_subprocess)

    parser = module.build_parser()
    args = parser.parse_args(
        [
            "batch",
            *[str(pdf) for pdf in pdfs],
            "--output-dir",
            str(output_dir),
            "--device",
            "cpu",
            "--jobs",
            "2",
        ]
    )

    assert args.func(args) == 0
    assert max_active == 2
    manifest = module.read_json_if_exists(output_dir / "manifest.json")
    assert manifest is not None
    assert manifest["status"] == "ok"
    assert len(manifest["results"]) == 3


def test_batch_convert_subprocess_command_preserves_convert_flags(tmp_path) -> None:
    module = load_module()
    parser = module.build_parser()
    source_dir = tmp_path / "source_pdfs"
    source_dir.mkdir()
    input_pdf = source_dir / "a.pdf"
    input_pdf.write_bytes(b"%PDF-1.4\n")
    document_dir = tmp_path / "batch" / "a"
    args = parser.parse_args(
        [
            "batch-sample94",
            "--source-dir",
            str(source_dir),
            "--output-dir",
            str(tmp_path / "batch"),
            "--device",
            "cpu",
            "--pages",
            "3-4",
            "--profiles",
            "standard",
            "--no-picture-description",
            "--ocr-lang",
            "en",
            "--max-runtime-seconds",
            "5",
        ]
    )

    command = module.build_convert_subprocess_command(args, input_pdf=input_pdf, document_dir=document_dir)

    assert command[:3] == [sys.executable, str(module.Path(module.__file__).resolve()), "convert"]
    assert "--source-dir" not in command
    assert "--max-runtime-seconds" not in command
    assert command[command.index("--input") + 1] == str(input_pdf)
    assert command[command.index("--output-dir") + 1] == str(document_dir)
    assert command[command.index("--device") + 1] == "cpu"
    assert command[command.index("--pages") + 1] == "3-4"
    assert "--no-ocr" in command
    assert "--no-picture-classification" in command
    assert "--no-picture-description" in command
    assert command[command.index("--ocr-lang") + 1] == "en"
    assert command[command.index("--llm-hard-limit-mb") + 1] == "200.0"
    assert command[command.index("--llm-transport-headroom-mb") + 1] == "20.0"
    assert command[command.index("--llm-prompt-schema-headroom-mb") + 1] == "24.0"


def test_batch_convert_subprocess_command_preserves_enabled_optional_enrichments(tmp_path) -> None:
    module = load_module()
    parser = module.build_parser()
    source_dir = tmp_path / "source_pdfs"
    source_dir.mkdir()
    input_pdf = source_dir / "a.pdf"
    input_pdf.write_bytes(b"%PDF-1.4\n")
    document_dir = tmp_path / "batch" / "a"
    args = parser.parse_args(
        [
            "batch-sample94",
            "--source-dir",
            str(source_dir),
            "--output-dir",
            str(tmp_path / "batch"),
            "--device",
            "cpu",
            "--picture-classification",
            "--picture-description",
            "--enrich-code",
            "--enrich-formula",
        ]
    )

    command = module.build_convert_subprocess_command(args, input_pdf=input_pdf, document_dir=document_dir)

    assert "--picture-classification" in command
    assert "--picture-description" in command
    assert "--no-picture-classification" not in command
    assert "--no-picture-description" not in command
    assert "--enrich-code" in command
    assert "--enrich-formula" in command


def test_large_local_cuda_batch_requires_explicit_stress_ack(tmp_path) -> None:
    module = load_module()
    source_dir = tmp_path / "source_pdfs"
    source_dir.mkdir()
    for index in range(4):
        (source_dir / f"{index:03d}.pdf").write_bytes(b"%PDF-1.4\n")
    output_dir = tmp_path / "batch"
    parser = module.build_parser()
    args = parser.parse_args(
        [
            "batch-sample94",
            "--source-dir",
            str(source_dir),
            "--output-dir",
            str(output_dir),
            "--device",
            "cuda",
        ]
    )

    try:
        args.func(args)
    except module.DoclingConvertError as exc:
        assert "--allow-local-gpu-stress" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("large local CUDA batch should require an explicit stress acknowledgement")


def test_run_document_convert_subprocess_timeout_marks_timeout(tmp_path, monkeypatch) -> None:
    module = load_module()
    parser = module.build_parser()
    source_dir = tmp_path / "source_pdfs"
    source_dir.mkdir()
    input_pdf = source_dir / "a.pdf"
    input_pdf.write_bytes(b"%PDF-1.4\n")
    args = parser.parse_args(
        [
            "batch-sample94",
            "--source-dir",
            str(source_dir),
            "--output-dir",
            str(tmp_path / "batch"),
            "--document-timeout-seconds",
            "12",
        ]
    )

    def fake_run(*_args, **kwargs):
        raise module.subprocess.TimeoutExpired(cmd=kwargs.get("args", []), timeout=kwargs.get("timeout"))

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    returncode, log_path, error = module.run_document_convert_subprocess(
        args,
        input_pdf=input_pdf,
        document_dir=tmp_path / "batch" / "a",
    )

    assert returncode == 124
    assert log_path.is_file()
    assert error == "SubprocessTimeout: document conversion exceeded 12.0 seconds"
    assert "SubprocessTimeout" in log_path.read_text(encoding="utf-8")


def test_normalize_subprocess_manifest_marks_stale_running_manifest_failed(tmp_path) -> None:
    module = load_module()
    manifest_path = tmp_path / "manifest.json"
    input_pdf = tmp_path / "a.pdf"
    input_pdf.write_bytes(b"%PDF-1.4\n")
    log_path = tmp_path / "logs" / "convert-subprocess.log"
    module.write_json(manifest_path, {"status": "running", "input_pdf": str(input_pdf)})

    manifest = module.normalize_subprocess_manifest(
        manifest_path=manifest_path,
        input_pdf=input_pdf,
        returncode=-9,
        log_path=log_path,
    )

    assert manifest["status"] == "failed"
    assert manifest["error_type"] == "SubprocessInterrupted"
    assert "returncode=-9" in manifest["error"]
    assert manifest["subprocess_log"] == str(log_path)


def test_table_complexity_keeps_simple_table_as_structured_html() -> None:
    module = load_module()
    result = module.analyze_table_complexity(
        [
            ["Year", "GDP growth"],
            ["2024", "3.1"],
            ["2025", "3.4"],
        ]
    )
    assert result["label"] == "simple"
    assert result["bitmap_required"] is False
    assert result["trust_structured_extraction"] is True
    record = {"complexity": result}
    assert module.table_should_render_as_bitmap(record, policy="complex") is False


def test_table_complexity_flags_policy_matrix_failures_for_bitmap_rendering() -> None:
    module = load_module()
    result = module.analyze_table_complexity(
        [
            [
                "Prior actions and Triggers",
                "Prior actions and Triggers",
                "Results",
                "Results",
                "Results",
            ],
            [
                "Prior Actions under DPF 1",
                "Triggers for DPF 2",
                "Indicator Name",
                "Baseline",
                "Target",
            ],
            [
                "Prior Action #3. To improve debt management, the MEF adopted an arrêté on external borrowing.",
                "Trigger #3. The MEF adopted a risk assessment framework.",
                (
                    "Results Indicator #3.1: Non-concessional loans approved after consultations. "
                    "Results Indicator #3.2: Percentage of beneficiaries of on-lent loans."
                ),
                "2018: 80 percent 2018: 0 percent",
                "2021: 100 percent 2021: 80 percent",
            ],
        ]
    )
    assert result["bitmap_required"] is True
    assert result["structure_confidence"] == "low"
    assert "duplicate_headers" in result["flags"]
    assert "multiple_year_value_pairs_in_one_cell" in result["flags"]
    assert "multiple_indicators_in_one_cell" in result["flags"]
    record = {"complexity": result}
    assert module.table_should_render_as_bitmap(record, policy="complex") is True
    assert module.table_should_render_as_bitmap(record, policy="none") is False
    assert module.table_should_render_as_bitmap(record, policy="all") is True


def test_picture_bitmap_filter_skips_tiny_page_furniture_and_keeps_semantic_caption(tmp_path) -> None:
    module = load_module()

    tiny_png = tmp_path / "tiny.png"
    tiny_png.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + (50).to_bytes(4, "big")
        + (50).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    tiny = module.classify_picture_for_llm_bitmap(tiny_png, caption="")
    assert tiny["bitmap_required"] is False
    assert tiny["llm_render"] == "skip"
    assert "icon_sized" in tiny["flags"]

    banner_png = tmp_path / "banner.png"
    banner_png.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + (252).to_bytes(4, "big")
        + (50).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    banner = module.classify_picture_for_llm_bitmap(banner_png, caption="")
    assert banner["bitmap_required"] is False
    assert "banner_or_page_furniture_shaped" in banner["flags"]

    medium_png = tmp_path / "medium.png"
    medium_png.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + (222).to_bytes(4, "big")
        + (163).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    medium = module.classify_picture_for_llm_bitmap(medium_png, caption="")
    assert medium["bitmap_required"] is True
    assert "medium_semantic_image" in medium["flags"]

    large_png = tmp_path / "large.png"
    large_png.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + (800).to_bytes(4, "big")
        + (500).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    large = module.classify_picture_for_llm_bitmap(large_png, caption="Figure 2. Results chain diagram")
    assert large["bitmap_required"] is True
    assert large["llm_render"] == "bitmap"
    assert "semantic_caption" in large["flags"]


def test_page_width_crop_box_uses_full_rendered_width_and_bbox_vertical_band() -> None:
    module = load_module()
    crop = module.page_width_crop_box(
        page_size={"width": 792.0, "height": 612.0},
        bbox={"l": 43.6, "t": 488.9, "r": 751.9, "b": 84.1, "coord_origin": "BOTTOMLEFT"},
        image_size=(2420, 1870),
        padding_pt=18.0,
    )
    assert crop[0] == 0
    assert crop[2] == 2420
    assert 300 <= crop[1] <= 360
    assert 1600 <= crop[3] <= 1700


def test_preferred_table_bitmap_uses_llm_image_over_raw_docling_crop() -> None:
    module = load_module()
    record = {
        "image": {"path": "/tmp/raw-docling-crop.png", "source": "docling_table_crop"},
        "llm_image": {"path": "/tmp/page-width-crop.png", "source": "page_width_provenance_crop"},
    }
    assert module.preferred_table_bitmap_info(record)["path"] == "/tmp/page-width-crop.png"


def test_preferred_picture_bitmap_uses_llm_image_over_raw_docling_crop() -> None:
    module = load_module()
    record = {
        "image": {"path": "/tmp/raw-picture-crop.png", "source": "docling_picture_crop"},
        "llm_image": {"path": "/tmp/page-width-picture.png", "source": "page_width_provenance_crop"},
    }
    assert module.preferred_picture_bitmap_info(record)["path"] == "/tmp/page-width-picture.png"


def test_lean_html_fragment_strips_nonsemantic_formatting_and_metadata() -> None:
    module = load_module()
    lean = module.lean_html_fragment(
        (
            '<table class="x" style="color:red" data-meta="drop">'
            '<caption><div>Table 1. Caption</div></caption>'
            '<tbody><tr><td colspan="2" data-score="9">A</td></tr></tbody>'
            "</table>"
        )
    )
    assert lean == '<table><caption>Table 1. Caption</caption><tr><td colspan="2">A</td></tr></table>'
    assert "class=" not in lean
    assert "style=" not in lean
    assert "data-" not in lean
    assert "<tbody" not in lean
    assert "<div" not in lean


def test_lean_html_text_and_image_are_markdown_like() -> None:
    module = load_module()

    assert module.lean_html_text_block("  A < B & C  ") == "A &lt; B &amp; C"
    assert module.lean_image_tag("../tables/a.png", 'A "quoted" table') == (
        '<img src="../tables/a.png" alt="A &quot;quoted&quot; table">'
    )
    document = module.lean_html_document(
        [
            module.lean_html_text_block("Paragraph one"),
            module.lean_image_tag("../images/figure.png", "Figure 1"),
            "<table><tr><td>A</td></tr></table>",
        ]
    )
    assert "<p>" not in document
    assert "<img src=" in document


def test_bitmap_exclusion_zones_skip_text_inside_rendered_picture() -> None:
    module = load_module()
    zones = module.bitmap_exclusion_zones(
        table_records=[],
        picture_records=[
            {
                "index": 1,
                "provenance": [
                    {
                        "page_no": 6,
                        "bbox": {"l": 100, "r": 500, "t": 450, "b": 100, "coord_origin": "BOTTOMLEFT"},
                    }
                ],
                "render_decision": {"bitmap_required": True},
                "llm_image": {"path": "/tmp/picture.page-width.png"},
            }
        ],
        table_bitmap_policy="complex",
    )
    inside = SimpleNamespace(
        prov=[
            {
                "page_no": 6,
                "bbox": {"l": 120, "r": 480, "t": 420, "b": 130, "coord_origin": "BOTTOMLEFT"},
            }
        ]
    )
    outside = SimpleNamespace(
        prov=[
            {
                "page_no": 6,
                "bbox": {"l": 110, "r": 490, "t": 90, "b": 60, "coord_origin": "BOTTOMLEFT"},
            }
        ]
    )

    assert module.item_inside_bitmap_zone(inside, zones) is True
    assert module.item_inside_bitmap_zone(outside, zones) is False


def test_table_complexity_flags_possible_page_continuation() -> None:
    module = load_module()
    result = module.analyze_table_complexity(
        [
            ["loans, and concessional loans greater than FCFA 67 billion", "guarantees whose risk score is reflected"],
            ["Prior Action #4", "Results Indicator #4"],
        ],
        threshold=2,
    )
    assert result["bitmap_required"] is True
    assert "possible_page_continuation" in result["flags"]


def test_table_complexity_flags_spanning_cells_for_bitmap_rendering() -> None:
    module = load_module()
    result = module.analyze_table_complexity(
        [["Header A", "Header B"], ["A", "B"]],
        html_text='<table><tr><th colspan="2">Merged heading</th></tr><tr><td>A</td><td>B</td></tr></table>',
    )

    assert result["bitmap_required"] is True
    assert result["trust_structured_extraction"] is False
    assert "html_spanning_cells" in result["flags"]


def test_captioned_table_fallback_triggers_only_on_actual_caption() -> None:
    module = load_module()

    assert module.looks_like_table_caption("Table 2.4. Model Assumptions") is True
    assert module.looks_like_table_caption("Text Table 1. Fiscal adjustment scenario") is True
    assert module.looks_like_table_caption("Annex IV. Public Debt Sustainability Analysis") is True
    assert module.looks_like_table_caption("See Table 12.1 for the source data") is False


def test_caption_fallback_bbox_extends_from_caption_to_page_bottom() -> None:
    module = load_module()

    fallback = module.caption_to_page_bottom_bbox(
        caption_bbox={"l": 120, "r": 400, "t": 520, "b": 500, "coord_origin": "BOTTOMLEFT"},
        page_size={"width": 612, "height": 792},
    )

    assert fallback["l"] == 0.0
    assert fallback["r"] == 612.0
    assert fallback["t"] == 520
    assert fallback["b"] == 0.0
    assert fallback["coord_origin"] == "BOTTOMLEFT"


def test_page_looks_like_table_continuation_uses_policy_matrix_signals() -> None:
    module = load_module()
    records = [
        {
            "page_no": 21,
            "text": "Prior Action #2: To improve tax administration",
            "provenance": [
                {"page_no": 21, "bbox": {"l": 40, "r": 240, "t": 700, "b": 620, "coord_origin": "BOTTOMLEFT"}}
            ],
        },
        {
            "page_no": 21,
            "text": "Reform program and results are on track",
            "provenance": [
                {"page_no": 21, "bbox": {"l": 300, "r": 560, "t": 710, "b": 610, "coord_origin": "BOTTOMLEFT"}}
            ],
        },
        {
            "page_no": 21,
            "text": "Results baseline update target",
            "provenance": [
                {"page_no": 21, "bbox": {"l": 40, "r": 240, "t": 540, "b": 500, "coord_origin": "BOTTOMLEFT"}}
            ],
        },
    ]

    assert module.page_looks_like_table_continuation(records, page_size={"width": 612, "height": 792}) is True
    assert module.page_looks_like_table_continuation(
        [
            {
                "page_no": 22,
                "text": "5. RATIONALE FOR PROPOSED SUPPLEMENTAL FINANCING",
                "provenance": [
                    {
                        "page_no": 22,
                        "bbox": {"l": 40, "r": 560, "t": 700, "b": 660, "coord_origin": "BOTTOMLEFT"},
                    }
                ],
            }
        ],
        page_size={"width": 612, "height": 792},
    ) is False


def test_captioned_table_fallback_adds_continuation_pages_until_table_item(tmp_path, monkeypatch) -> None:
    module = load_module()

    def rec(page_no: int, text: str, *, left: int, right: int, top: int, bottom: int) -> dict:
        return {
            "page_no": page_no,
            "text": text,
            "provenance": [
                {
                    "page_no": page_no,
                    "bbox": {"l": left, "r": right, "t": top, "b": bottom, "coord_origin": "BOTTOMLEFT"},
                }
            ],
        }

    document = SimpleNamespace(
        pages={
            page_no: SimpleNamespace(size=SimpleNamespace(width=612, height=792))
            for page_no in range(20, 24)
        }
    )
    artifacts = {
        "tables": [
            {
                "provenance": [
                    {"page_no": 23, "bbox": {"l": 40, "r": 560, "t": 720, "b": 80, "coord_origin": "BOTTOMLEFT"}}
                ]
            }
        ]
    }
    text_records = [
        rec(20, "Table 6: DPO Series Reform Program Update", left=40, right=400, top=520, bottom=500),
        rec(21, "Prior Action #2: To improve tax administration", left=40, right=240, top=700, bottom=620),
        rec(21, "Reform program and results are on track", left=300, right=560, top=710, bottom=610),
        rec(21, "Results baseline update target", left=40, right=240, top=540, bottom=500),
        rec(22, "Indicative Trigger #3: To improve debt management", left=40, right=240, top=700, bottom=620),
        rec(22, "Reform program could be affected by implementation delays", left=300, right=560, top=710, bottom=610),
        rec(22, "Results baseline update target", left=40, right=240, top=540, bottom=500),
        rec(23, "This page has a real Docling table item and should stop continuation", left=40, right=560, top=700, bottom=620),
    ]

    def fake_build_page_width_bbox_bitmap(**kwargs):
        return {
            "path": str(kwargs["output_path"]),
            "source": kwargs["source"],
            "page_no": kwargs["page_no"],
            "bbox": kwargs["bbox"],
        }

    monkeypatch.setattr(module, "build_page_width_bbox_bitmap", fake_build_page_width_bbox_bitmap)

    module.add_captioned_table_fallbacks(
        artifacts=artifacts,
        text_records=text_records,
        input_pdf=tmp_path / "fake.pdf",
        document=document,
        tables_dir=tmp_path / "tables",
    )

    fallback_records = artifacts["fallback_table_bitmaps"]
    assert [record["page_no"] for record in fallback_records] == [20, 21, 22]
    assert fallback_records[0].get("continuation_of_page") is None
    assert fallback_records[1]["continuation_of_page"] == 20
    assert fallback_records[2]["continuation_of_page"] == 20
    assert "captioned_table_continuation_without_table_item" in fallback_records[1]["render_decision"]["flags"]
    assert "cont-0021" in fallback_records[1]["llm_image"]["path"]


def test_captioned_table_fallback_does_not_swallow_next_table_caption(tmp_path, monkeypatch) -> None:
    module = load_module()
    document = SimpleNamespace(
        pages={page_no: SimpleNamespace(size=SimpleNamespace(width=612, height=792)) for page_no in [20, 21]}
    )
    artifacts: dict = {"tables": []}
    text_records = [
        {
            "page_no": 20,
            "text": "Table 6: DPO Series Reform Program Update",
            "provenance": [
                {"page_no": 20, "bbox": {"l": 40, "r": 400, "t": 520, "b": 500, "coord_origin": "BOTTOMLEFT"}}
            ],
        },
        {
            "page_no": 21,
            "text": "Table 7: New and separate table",
            "provenance": [
                {"page_no": 21, "bbox": {"l": 40, "r": 400, "t": 700, "b": 680, "coord_origin": "BOTTOMLEFT"}}
            ],
        },
    ]

    def fake_build_page_width_bbox_bitmap(**kwargs):
        return {"path": str(kwargs["output_path"]), "source": kwargs["source"], "page_no": kwargs["page_no"]}

    monkeypatch.setattr(module, "build_page_width_bbox_bitmap", fake_build_page_width_bbox_bitmap)

    module.add_captioned_table_fallbacks(
        artifacts=artifacts,
        text_records=text_records,
        input_pdf=tmp_path / "fake.pdf",
        document=document,
        tables_dir=tmp_path / "tables",
    )

    fallback_records = artifacts["fallback_table_bitmaps"]
    assert [record["page_no"] for record in fallback_records] == [20, 21]
    assert all("continuation_of_page" not in record for record in fallback_records)


def test_union_provenance_bbox_combines_fragmented_picture_cluster() -> None:
    module = load_module()

    bbox = module.union_provenance_bbox(
        [
            {"bbox": {"l": 100, "r": 200, "t": 500, "b": 400, "coord_origin": "BOTTOMLEFT"}},
            {"bbox": {"l": 250, "r": 350, "t": 520, "b": 390, "coord_origin": "BOTTOMLEFT"}},
        ]
    )

    assert bbox == {"l": 100.0, "r": 350.0, "b": 390.0, "t": 520.0, "coord_origin": "BOTTOMLEFT"}


def test_render_pdf_page_png_uses_pypdfium2_when_pdftoppm_is_missing(tmp_path, monkeypatch) -> None:
    module = load_module()
    input_pdf = tmp_path / "input.pdf"
    input_pdf.write_bytes(b"%PDF-1.4\n")
    output_png = tmp_path / "page.png"
    one_pixel_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x01\x01"
        b"\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    class FakePilImage:
        def save(self, path, _format):
            Path(path).write_bytes(one_pixel_png)

    class FakeBitmap:
        def to_pil(self):
            return FakePilImage()

    class FakePage:
        def __init__(self):
            self.render_scale = None
            self.closed = False

        def render(self, *, scale):
            self.render_scale = scale
            return FakeBitmap()

        def close(self):
            self.closed = True

    class FakePdfDocument:
        def __init__(self, path):
            self.path = path
            self.page = FakePage()
            self.closed = False

        def __len__(self):
            return 1

        def __getitem__(self, index):
            assert index == 0
            return self.page

        def close(self):
            self.closed = True

    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "pypdfium2", SimpleNamespace(PdfDocument=FakePdfDocument))

    info = module.render_pdf_page_png(input_pdf, page_no=1, output_path=output_png, dpi=144)

    assert output_png.exists()
    assert info["render_engine"] == "pypdfium2"
    assert info["render_dpi"] == 144
    assert info["render_scale"] == 2.0


def test_native_merged_and_missing_cells_force_bitmap() -> None:
    module = load_module()
    element = SimpleNamespace(
        data=SimpleNamespace(
            num_rows=2,
            num_cols=3,
            table_cells=[
                SimpleNamespace(
                    row_span=1,
                    col_span=2,
                    start_row_offset_idx=0,
                    end_row_offset_idx=1,
                    start_col_offset_idx=0,
                    end_col_offset_idx=2,
                    text="Merged header",
                ),
                SimpleNamespace(
                    row_span=1,
                    col_span=1,
                    start_row_offset_idx=0,
                    end_row_offset_idx=1,
                    start_col_offset_idx=2,
                    end_col_offset_idx=3,
                    text="Target",
                ),
                SimpleNamespace(
                    row_span=1,
                    col_span=1,
                    start_row_offset_idx=1,
                    end_row_offset_idx=2,
                    start_col_offset_idx=0,
                    end_col_offset_idx=1,
                    text="A",
                ),
                # The second row intentionally has no cell covering column 1.
                SimpleNamespace(
                    row_span=1,
                    col_span=1,
                    start_row_offset_idx=1,
                    end_row_offset_idx=2,
                    start_col_offset_idx=2,
                    end_col_offset_idx=3,
                    text="B",
                ),
            ],
        )
    )

    native = module.table_structure_metrics(element)
    result = module.analyze_table_complexity(
        [["Merged header", "Target"], ["A", "B"]],
        native_structure=native,
    )

    assert native["merged_cell_count"] == 1
    assert native["grid_hole_count"] == 1
    assert result["bitmap_required"] is True
    assert "native_merged_cells" in result["flags"]
    assert "native_grid_holes" in result["flags"]


def test_cross_page_repeated_headers_force_both_table_bitmaps(tmp_path) -> None:
    module = load_module()
    records = []
    for table_index, page_no in enumerate([10, 11], start=1):
        csv_path = tmp_path / f"table-{page_no}.csv"
        csv_path.write_text("Indicator,Baseline,Target\nRevenue,1,2\n", encoding="utf-8")
        records.append(
            {
                "index": table_index,
                "csv": {"path": str(csv_path)},
                "provenance": [{"page_no": page_no, "bbox": {"l": 0, "r": 1, "t": 1, "b": 0}}],
                "complexity": {
                    "label": "simple",
                    "score": 0,
                    "bitmap_required": False,
                    "trust_structured_extraction": True,
                    "structure_confidence": "high",
                    "flags": [],
                    "metrics": {"n_cols": 3},
                },
            }
        )

    module.mark_cross_page_table_sequences(records)

    assert all(record["complexity"]["bitmap_required"] for record in records)
    assert all("cross_page_table_sequence" in record["complexity"]["flags"] for record in records)
    assert {record["continuation_group_id"] for record in records} == {
        "table-continuation-p0010-p0011-t0001"
    }
    assert [record["continuation_sequence_index"] for record in records] == [1, 2]


def test_picture_geometry_reports_real_pdf_size_and_uses_it_for_routing() -> None:
    module = load_module()
    provenance = [
        {
            "page_no": 1,
            "bbox": {"l": 72, "r": 216, "t": 144, "b": 72, "coord_origin": "BOTTOMLEFT"},
        }
    ]

    decision = module.classify_picture_for_llm_bitmap(
        None,
        provenance=provenance,
        page_size={"width": 612.0, "height": 792.0},
    )

    geometry = decision["page_geometry"]
    assert geometry["width_inches"] == 2.0
    assert geometry["height_inches"] == 1.0
    assert geometry["width_cm"] == 5.08
    assert geometry["height_cm"] == 2.54
    assert "physically_substantive_region" in decision["flags"]
    assert decision["bitmap_required"] is True


def test_physical_size_overrides_high_raster_resolution_for_tiny_margin_art(tmp_path) -> None:
    module = load_module()
    image = tmp_path / "high-resolution-logo.png"
    image.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + (1200).to_bytes(4, "big")
        + (600).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )

    decision = module.classify_picture_for_llm_bitmap(
        image,
        provenance=[
            {
                "page_no": 1,
                "bbox": {"l": 36, "r": 72, "t": 790, "b": 780, "coord_origin": "BOTTOMLEFT"},
            }
        ],
        page_size={"width": 612.0, "height": 792.0},
    )

    assert "large_semantic_image" in decision["flags"]
    assert decision["page_geometry"]["width_inches"] == 0.5
    assert decision["bitmap_required"] is False
    assert "small_margin_furniture" in decision["flags"]


def test_semantic_caption_overrides_icon_shape_but_margin_furniture_is_skipped(tmp_path) -> None:
    module = load_module()
    image = tmp_path / "small.png"
    image.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + (100).to_bytes(4, "big")
        + (60).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    page_size = {"width": 600.0, "height": 800.0}
    provenance = [
        {
            "page_no": 1,
            "bbox": {"l": 20, "r": 100, "t": 30, "b": 10, "coord_origin": "TOPLEFT"},
        }
    ]

    semantic = module.classify_picture_for_llm_bitmap(
        image,
        caption="Figure 1. Results flow",
        provenance=provenance,
        page_size=page_size,
    )
    furniture = module.classify_picture_for_llm_bitmap(
        image,
        caption="",
        provenance=provenance,
        page_size=page_size,
    )

    assert semantic["bitmap_required"] is True
    assert furniture["bitmap_required"] is False
    assert "small_margin_furniture" in furniture["flags"]


def test_repeated_margin_picture_hash_is_suppressed(tmp_path) -> None:
    module = load_module()
    image_bytes = b"same-logo"
    records = []
    for page_no in [1, 2]:
        path = tmp_path / f"logo-{page_no}.png"
        path.write_bytes(image_bytes)
        records.append(
            {
                "image": {"path": str(path)},
                "provenance": [{"page_no": page_no, "bbox": {"l": 0, "r": 10, "t": 10, "b": 0}}],
                "render_decision": {
                    "bitmap_required": True,
                    "llm_render": "bitmap",
                    "score": 2,
                    "flags": ["icon_sized"],
                    "page_geometry": {"near_top_margin": True},
                },
            }
        )

    suppressed = module.suppress_repeated_picture_furniture(records)

    assert suppressed == 2
    assert all(record["render_decision"]["bitmap_required"] is False for record in records)
    assert all("repeated_page_furniture" in record["render_decision"]["flags"] for record in records)


def test_perceptually_repeated_margin_furniture_needs_three_pages(tmp_path) -> None:
    module = load_module()
    image_module = __import__("PIL.Image", fromlist=["Image"])
    records = []
    for page_no, changed_pixel in [(1, None), (2, (1, 1)), (3, (2, 1))]:
        image = image_module.new("RGB", (64, 24), "white")
        for x in range(8, 56):
            for y in range(8, 16):
                image.putpixel((x, y), (20, 20, 20))
        if changed_pixel is not None:
            image.putpixel(changed_pixel, (230, 230, 230))
        path = tmp_path / f"header-{page_no}.png"
        image.save(path, "PNG", compress_level=page_no)
        records.append(
            {
                "image": {"path": str(path)},
                "provenance": [{"page_no": page_no, "bbox": {"l": 36, "r": 180, "t": 780, "b": 752}}],
                "render_decision": {
                    "bitmap_required": True,
                    "llm_render": "bitmap",
                    "score": 2,
                    "flags": [],
                    "page_geometry": {
                        "near_top_margin": True,
                        "near_bottom_margin": False,
                        "width_inches": 2.0,
                        "height_inches": 0.39,
                    },
                },
            }
        )

    suppressed = module.suppress_repeated_picture_furniture(records)

    assert suppressed == 3
    assert all(
        "perceptually_repeated_page_furniture" in record["render_decision"]["flags"]
        for record in records
    )


def test_large_uncaptioned_photo_next_to_credit_is_suppressed() -> None:
    module = load_module()
    pictures = [
        {
            "caption": "",
            "provenance": [{"page_no": 125, "bbox": {"l": 0, "r": 600, "t": 790, "b": 260}}],
            "render_decision": {
                "bitmap_required": True,
                "llm_render": "bitmap",
                "score": 3,
                "flags": ["large_semantic_image", "large_figure_candidate"],
                "page_geometry": {"area_ratio": 0.67},
            },
        }
    ]
    text_records = [
        {
            "page_no": 125,
            "text": "© 2020 Handerson Chulet/Shutterstock Appendix",
        }
    ]

    suppressed = module.suppress_photo_credit_decorations(pictures, text_records)

    assert suppressed == 1
    assert pictures[0]["render_decision"]["bitmap_required"] is False
    assert "photo_credit_decoration" in pictures[0]["render_decision"]["flags"]


def test_payload_budget_uses_base64_expansion_and_reserved_headroom(tmp_path) -> None:
    module = load_module()
    markdown = tmp_path / "payload.md"
    markdown.write_bytes(b"x" * 100)
    asset = tmp_path / "table.png"
    asset.write_bytes(b"a" * 300)

    budget = module.estimate_llm_payload_budget(
        markdown,
        [str(asset)],
        hard_limit_mb=3,
        transport_headroom_mb=1,
        prompt_schema_headroom_mb=1,
    )

    assert budget["asset_bytes"] == 300
    assert budget["serialized_asset_bytes"] == 496
    assert budget["estimated_total_bytes"] == 2_000_596
    assert budget["fits"] is True


def test_exact_openai_batch_serialization_and_page_sharding_retain_images(tmp_path) -> None:
    module = load_module()
    asset_a = tmp_path / "page-1.png"
    asset_b = tmp_path / "page-2.png"
    asset_a.write_bytes(b"a" * 600)
    asset_b.write_bytes(b"b" * 600)
    blocks = [
        {
            "kind": "table_bitmap",
            "markdown": "![Table 1](page-1.png)",
            "html": "",
            "page_nos": [1],
            "asset_paths": [str(asset_a)],
            "starts_section": False,
        },
        {
            "kind": "table_bitmap",
            "markdown": "![Table 2](page-2.png)",
            "html": "",
            "page_nos": [2],
            "asset_paths": [str(asset_b)],
            "starts_section": False,
        },
    ]
    unlimited = module.plan_openai_batch_shards(
        blocks,
        stem="report",
        hard_limit_bytes=100_000,
        transport_headroom_bytes=0,
        prompt_schema_headroom_bytes=0,
        response_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
    )
    combined_size = unlimited["shards"][0]["serialized_bytes"]
    single_sizes = []
    for block in blocks:
        single = module.plan_openai_batch_shards(
            [block],
            stem="report",
            hard_limit_bytes=100_000,
            transport_headroom_bytes=0,
            prompt_schema_headroom_bytes=0,
        )
        single_sizes.append(single["shards"][0]["serialized_bytes"])
    limit = max(single_sizes) + 32
    assert limit < combined_size

    plan = module.plan_openai_batch_shards(
        blocks,
        stem="report",
        hard_limit_bytes=limit,
        transport_headroom_bytes=0,
        prompt_schema_headroom_bytes=0,
    )
    summaries = module.write_openai_batch_shards(tmp_path, stem="report", shard_plan=plan)

    assert plan["sharded"] is True
    assert len(plan["shards"]) == 2
    assert all(shard["serialized_bytes"] <= limit for shard in plan["shards"])
    assert [summary["asset_count"] for summary in summaries] == [1, 1]
    for summary in summaries:
        raw = Path(summary["jsonl"]["path"]).read_bytes()
        assert len(raw) == summary["serialized_bytes"]
        assert b'"url":"/v1/chat/completions"' in raw
        assert b"data:image/png;base64," in raw


def test_budgeted_export_falls_back_to_mechanical_when_bitmaps_do_not_fit(tmp_path, monkeypatch) -> None:
    module = load_module()
    large_asset = tmp_path / "large.png"
    large_asset.write_bytes(b"x" * 1_000)

    def fake_write(_document, profile_dir, _artifacts, *, stem, table_bitmap_policy, bitmap_mode):
        md_path = profile_dir / "llm" / f"{stem}.llm.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("ok\n", encoding="utf-8")
        return {
            "files": {
                "llm_markdown": module.summarize_file(md_path),
                "llm_html": module.summarize_file(md_path),
            },
            "decisions": {},
            "table_bitmap_policy": "none" if bitmap_mode == "mechanical" else table_bitmap_policy,
            "bitmap_mode": bitmap_mode,
            "referenced_asset_paths": [] if bitmap_mode == "mechanical" else [str(large_asset)],
        }

    monkeypatch.setattr(module, "write_llm_oriented_exports", fake_write)

    result = module.write_budgeted_llm_oriented_exports(
        object(),
        tmp_path,
        {},
        stem="doc",
        table_bitmap_policy="complex",
        hard_limit_mb=0.0005,
        transport_headroom_mb=0,
        prompt_schema_headroom_mb=0,
    )

    assert result["bitmap_mode"] == "mechanical"
    assert result["payload_budget"]["fallback_applied"] is True
    assert [attempt["bitmap_mode"] for attempt in result["payload_budget"]["attempts"]] == [
        "full",
        "tables_only",
        "mechanical",
    ]
