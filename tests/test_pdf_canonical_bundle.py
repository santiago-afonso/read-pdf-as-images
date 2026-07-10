from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "benchmark" / "pdf" / "canonical_bundle.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pdf_canonical_bundle", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_page_ranges_accepts_lists_ranges_and_all() -> None:
    module = _load_module()

    assert module.parse_page_ranges("1, 3-5, 5") == [1, 3, 4, 5]
    assert module.parse_page_ranges("all") is None
    assert module.parse_page_ranges(None) is None
    assert module.compact_page_ranges([1, 2, 3, 7, 9, 10]) == "1-3,7,9-10"
    assert module.page_selection_label({"selected_pages": [1, 2, 3], "source_page_count": 3}) == "all (3 pages)"


def test_image_token_formula_matches_read_pdf_high_detail_examples() -> None:
    module = _load_module()

    assert module.image_tokens_gpt5_high_detail(1700, 2200) == 630
    assert module.image_tokens_gpt5_high_detail(1870, 2420) == 630
    assert module.gemini3_image_tokens_for_dpi(150) == ("MEDIA_RESOLUTION_LOW", 280)
    assert module.gemini3_image_tokens_for_dpi(220) == ("MEDIA_RESOLUTION_MEDIUM", 560)
    assert module.gemini3_image_tokens_for_dpi(240) == ("MEDIA_RESOLUTION_HIGH", 1120)


def test_content_asset_classifier_filters_stationery_and_full_pages(tmp_path: Path) -> None:
    module = _load_module()
    from PIL import Image

    logo = tmp_path / "logo.png"
    Image.new("RGB", (50, 48), "white").save(logo)
    assert module.classify_content_asset(logo)["keep"] is False

    full_page = tmp_path / "page.png"
    Image.effect_noise((1700, 2200), 100).convert("RGB").save(full_page)
    assert module.classify_content_asset(full_page)["reason"] == "full_page_like_image_not_default_asset"

    chart = tmp_path / "chart.png"
    Image.effect_noise((800, 480), 100).convert("RGB").save(chart)
    assert module.classify_content_asset(chart)["keep"] is True


def test_build_podman_command_mounts_run_dir_and_uses_container_python(tmp_path: Path) -> None:
    module = _load_module()
    args = argparse.Namespace(
        podman="podman",
        conversion_network="none",
        pids_limit=128,
        memory="2g",
        cpus="1",
        image="localhost/test:canonical",
    )

    cmd = module.build_podman_command(args, run_dir=tmp_path, pages=None, render_dpi=160)

    assert "--network" in cmd
    assert cmd[cmd.index("--network") + 1] == "none"
    assert f"{tmp_path}:/work:rw" in cmd
    assert "/opt/venv/bin/python" in cmd
    assert "/work/runner.py" in cmd
    assert "--render-dpi" in cmd
    assert cmd[cmd.index("--render-dpi") + 1] == "160"


def test_rows_for_comparison_shapes_requested_columns() -> None:
    module = _load_module()

    rows = module.rows_for_comparison(
        [
            {
                "doc_type": "DPF",
                "doc_id": "sample",
                "selected_pages": [1],
                "status": "ok",
                "run_dir": "/tmp/run",
                "token_summary": {
                    "canonical_bundle_gpt5_total": 100,
                    "canonical_bundle_gemini3_total": 90,
                    "canonical_cleanup_gpt5_total": 80,
                    "canonical_cleanup_gemini3_total": 70,
                    "pdf_images_gpt5_total": 1190,
                    "pdf_images_gemini3_total": 560,
                    "content_asset_image_count": 2,
                    "audit_bundle_image_count": 3,
                    "audit_page_render_count": 1,
                    "stored_audit_bundle_gpt5_total": 120,
                    "stored_audit_bundle_gemini3_total": 110,
                    "payload_text_tokens": 50,
                    "cleanup_text_tokens": 30,
                },
            }
        ]
    )

    assert rows == [
        {
            "document_type": "DPF",
            "document_id": "sample",
            "pages": "1",
            "page_count": 1,
            "canonical_bundle_openai_gpt5_tokens": 100,
            "canonical_bundle_gemini3_tokens": 90,
            "canonical_bundle_plus_cleanup_openai_gpt5_tokens": 80,
            "canonical_bundle_plus_cleanup_gemini3_tokens": 70,
            "stored_audit_bundle_openai_gpt5_tokens": 120,
            "stored_audit_bundle_gemini3_tokens": 110,
            "pdf_as_images_openai_gpt5_tokens": 1190,
            "pdf_as_images_gemini3_tokens": 560,
            "content_asset_image_count": 2,
            "audit_bundle_image_count": 3,
            "audit_page_render_count": 1,
            "payload_text_tokens": 50,
            "cleanup_text_tokens": 30,
            "status": "ok",
            "run_dir": "/tmp/run",
        }
    ]


def test_evaluation_report_includes_openai_and_gemini_token_columns(tmp_path: Path) -> None:
    module = _load_module()

    report = module.build_evaluation_report(
        tmp_path,
        [
            {
                "doc_type": "DPF",
                "doc_id": "sample",
                "selected_pages": [1],
                "status": "ok",
                "run_dir": "/tmp/run",
                "token_summary": {
                    "canonical_bundle_gpt5_total": 100,
                    "canonical_bundle_gemini3_total": 90,
                    "canonical_cleanup_gpt5_total": 80,
                    "canonical_cleanup_gemini3_total": 70,
                    "pdf_images_gpt5_total": 630,
                    "pdf_images_gemini3_total": 560,
                    "content_asset_image_count": 2,
                    "audit_bundle_image_count": 3,
                    "audit_page_render_count": 1,
                    "stored_audit_bundle_gpt5_total": 120,
                    "stored_audit_bundle_gemini3_total": 110,
                    "payload_text_tokens": 50,
                    "cleanup_text_tokens": 30,
                },
            }
        ],
    )

    assert "Canonical LLM payload OpenAI" in report
    assert "Canonical LLM payload Gemini" in report
    assert "Canonical + cleanup OpenAI" in report
    assert "Canonical + cleanup Gemini" in report
    assert "| DPF | `1` | 100 | 90 | 80 | 70 | 630 | 560 | 0.16x | 0.16x |" in report
    assert "Stored bundle with page renders OpenAI" in report
