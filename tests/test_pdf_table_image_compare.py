from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from pypdf import PdfWriter


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "benchmark" / "pdf" / "table_image_compare.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pdf_table_image_compare", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_pdf(path: Path, pages: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as handle:
        writer.write(handle)


def test_default_engines_are_image_capable_and_skip_text_only_tools() -> None:
    module = _load_module()

    engines = module.resolve_engines("default")

    assert {"markitdown", "docling", "pymupdf4llm", "pymupdf"}.issubset(engines)
    assert "camelot" not in engines
    assert "edgeparse" not in engines
    assert "pdfminer_six" not in engines
    assert "tabula_java" not in engines
    assert set(module.SKIPPED_ENGINES).isdisjoint(engines)
    assert "edgeparse" in module.SKIPPED_ENGINES
    assert all(module.ENGINE_SPECS[engine].image_note for engine in engines)


def test_engine_groups_expand_without_duplicates() -> None:
    module = _load_module()

    engines = module.resolve_engines("markitdown,default,markitdown,optional")

    assert engines.count("markitdown") == 1
    assert engines[0] == "markitdown"
    assert "nutrient_pdf_to_markdown" in engines
    assert "unstructured" in engines


def test_resolve_windows_path_has_manual_wsl_fallback(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)

    path, strategy = module.resolve_windows_path(r"C:\Users\wb620566\Downloads\P162302 PAD.pdf")

    assert strategy == "manual_wsl"
    assert path.as_posix() == "/mnt/c/Users/wb620566/Downloads/P162302 PAD.pdf"


def test_discover_dpf_deck_documents_uses_known_extract_assets(tmp_path: Path) -> None:
    module = _load_module()
    assets = tmp_path / "deck" / "assets"
    _write_pdf(assets / "ecuador_dpf2_page_058_only.pdf")
    _write_pdf(assets / "bhutan_dpc3_pages_051_052.pdf", pages=2)

    docs = module.discover_dpf_deck_documents([tmp_path / "missing", tmp_path / "deck"])

    assert [doc["id"] for doc in docs] == ["ecuador_dpf2_page_058", "bhutan_dpc3_pages_051_052"]
    assert all(doc["source"] == "dpf_markdown_failure_deck" for doc in docs)


def test_choose_imf_article_iv_prefers_first_priority_iso_then_pages(tmp_path: Path) -> None:
    module = _load_module()
    _write_pdf(tmp_path / "KEN-2025-1934-7685-002.2025.issue-001-en.pdf", pages=20)
    _write_pdf(tmp_path / "ETH-2023-1934-7685-002.2023.issue-001-en.pdf", pages=3)
    _write_pdf(tmp_path / "ETH-2024-1934-7685-002.2024.issue-001-en.pdf", pages=5)

    selected = module.choose_imf_article_iv([tmp_path])

    assert selected is not None
    assert Path(selected["path"]).name == "ETH-2024-1934-7685-002.2024.issue-001-en.pdf"
    assert selected["source"] == "imf_article_iv_downloads"


def test_resolve_default_corpus_collects_dpf_pad_and_imf(tmp_path: Path) -> None:
    module = _load_module()
    dpf_artifacts = tmp_path / "dpf_deck"
    _write_pdf(dpf_artifacts / "assets" / "ecuador_dpf2_page_058_only.pdf")
    _write_pdf(dpf_artifacts / "assets" / "bhutan_dpc3_pages_051_052.pdf", pages=2)
    dpf_discovery = tmp_path / "imagebank"
    _write_pdf(dpf_discovery / "Sample Development Policy Financing.pdf", pages=4)
    imf_root = tmp_path / "imf"
    _write_pdf(imf_root / "ETH-2024-1934-7685-002.2024.issue-001-en.pdf", pages=5)
    pad = tmp_path / "P162302 PAD.pdf"
    _write_pdf(pad, pages=6)

    payload = module.resolve_default_corpus(
        dpf_artifact_roots=[dpf_artifacts],
        dpf_discovery_roots=[dpf_discovery],
        imf_download_roots=[imf_root],
        windows_pad=str(pad),
        max_dpf_docs=5,
    )

    ids = {doc["id"] for doc in payload["documents"]}
    assert "ecuador_dpf2_page_058" in ids
    assert "bhutan_dpc3_pages_051_052" in ids
    assert "sample_development_policy_financing" in ids
    assert "p162302_pad" in ids
    assert any(doc_id.startswith("imf_article_iv_eth_2024") for doc_id in ids)
    assert payload["missing"] == []
    assert all("sha256" in doc for doc in payload["documents"])


def test_build_engine_command_and_summary_document_outputs(tmp_path: Path) -> None:
    module = _load_module()

    cmd = module.build_engine_command(
        engine="docling",
        input_pdf=tmp_path / "input.pdf",
        output_dir=tmp_path / "out",
    )
    nutrient_cmd = module.build_engine_command(
        engine="nutrient_pdf_to_markdown",
        input_pdf=tmp_path / "input.pdf",
        output_dir=tmp_path / "nutrient",
    )
    summary = module.build_summary(
        run_dir=tmp_path / "run",
        rows=[
            {
                "document_id": "doc",
                "engine": "docling",
                "status": "ok",
                "output_format": "json",
                "image_count": 2,
                "seconds_per_page": 1.25,
                "output_path": "/tmp/output.json",
                "image_manifest_path": "/tmp/image_manifest.json",
            }
        ],
        manifest={"pages_spec": "1", "engines": ["docling"]},
    )

    assert cmd[:4] == ["uv", "run", "--with", "docling"]
    assert str(module.ENGINE_RUNNER) in cmd
    assert "--with" not in nutrient_cmd
    assert "`camelot`" in summary
    assert "JSON, then HTML, then Markdown" in summary
