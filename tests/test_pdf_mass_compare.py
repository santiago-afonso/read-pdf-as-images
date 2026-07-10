from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "benchmark" / "pdf" / "mass_compare.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pdf_mass_compare", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_pdf(path: Path, pages: int) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as handle:
        writer.write(handle)


def test_parse_page_ranges_accepts_commas_ranges_and_brackets() -> None:
    module = _load_module()

    assert module.parse_page_ranges("[1, 3-5, 5, 7]") == [1, 3, 4, 5, 7]
    assert module.parse_page_ranges("all") is None
    assert module.parse_page_ranges(None) is None


def test_parse_page_ranges_rejects_invalid_or_reversed_tokens() -> None:
    module = _load_module()

    try:
        module.parse_page_ranges("3-1")
    except ValueError as exc:
        assert "ends before" in str(exc)
    else:
        raise AssertionError("Expected reversed range to fail")

    try:
        module.parse_page_ranges("1,bad")
    except ValueError as exc:
        assert "Invalid page range" in str(exc)
    else:
        raise AssertionError("Expected invalid token to fail")


def test_materialize_input_pdf_extracts_requested_pages(tmp_path: Path) -> None:
    module = _load_module()
    source = tmp_path / "source.pdf"
    subset = tmp_path / "subset.pdf"
    _write_pdf(source, pages=4)

    module.materialize_input_pdf(source, [2, 4], subset)

    with subset.open("rb") as handle:
        assert len(PdfReader(handle).pages) == 2


def test_resolve_engines_expands_groups_without_duplicates() -> None:
    module = _load_module()

    engines = module.resolve_engines("core,pymupdf,core")

    assert engines[:3] == ["markitdown", "pymupdf4llm", "docling"]
    assert engines.count("markitdown") == 1
    assert "pymupdf" in engines
    assert "markit_ai" not in module.resolve_engines("all")
    assert "markit_ai" in module.resolve_engines("all_with_legacy")


def test_build_podman_command_mounts_only_run_dir_and_explicit_assets(tmp_path: Path) -> None:
    module = _load_module()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    nutrient = tmp_path / "nutrient-linux-amd64"
    nutrient.write_text("fake", encoding="utf-8")
    extra = tmp_path / "asset"
    extra.write_text("asset", encoding="utf-8")
    args = argparse.Namespace(
        podman="podman",
        conversion_network="none",
        pids_limit=128,
        memory="2g",
        cpus="1",
        pdf_to_markdown_binary=str(nutrient),
        extra_readonly_mount=[f"{extra}:/opt/asset"],
        image="localhost/test:alpine",
    )

    cmd = module.build_podman_command(
        args=args,
        engine="pypdf",
        run_dir=run_dir,
        engine_output_dir=run_dir / "outputs" / "pypdf",
        overrides={"pypdf": "cp {input} {output}"},
    )

    assert "--network" in cmd
    assert cmd[cmd.index("--network") + 1] == "none"
    assert f"{run_dir}:/work:rw" in cmd
    assert f"{nutrient.resolve()}:/pdf-to-markdown:ro" in cmd
    assert f"{extra.resolve()}:/opt/asset:ro" in cmd
    assert "OPENAI_API_KEY" not in " ".join(cmd)
    assert cmd[-8:] == [
        "python3",
        "/work/engine_runner.py",
        "--engine",
        "pypdf",
        "--input",
        "/work/input.pdf",
        "--output-dir",
        "/work/outputs/pypdf",
    ]
