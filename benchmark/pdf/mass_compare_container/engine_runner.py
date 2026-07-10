#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


ENGINE_OUTPUTS: dict[str, tuple[str, str]] = {
    "markitdown": ("output.md", "markdown"),
    "pymupdf4llm": ("output.md", "markdown"),
    "docling": ("output.md", "markdown"),
    "kreuzberg": ("output.md", "markdown"),
    "opendataloader": ("output.md", "markdown"),
    "nutrient_pdf_to_markdown": ("output.md", "markdown"),
    "liteparse": ("output.json", "json"),
    "edgeparse": ("output.md", "markdown"),
    "pdf_oxide": ("output.md", "markdown"),
    "unstructured": ("output.md", "markdown"),
    "pymupdf": ("output.md", "markdown"),
    "pdfplumber": ("output.md", "markdown"),
    "pdfminer_six": ("output.txt", "text"),
    "pypdfium2": ("output.txt", "text"),
    "pypdf": ("output.txt", "text"),
    "poppler_pdftotext": ("output.txt", "text"),
    "camelot": ("output.md", "markdown"),
    "tabula_java": ("output.json", "json"),
    "markit_ai": ("output.md", "markdown"),
}


class EngineError(RuntimeError):
    pass


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_json(path: Path, payload: Any) -> Path:
    return write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def require_import(module_name: str, install_hint: str) -> Any:
    if importlib.util.find_spec(module_name) is None:
        raise EngineError(f"Missing Python dependency `{module_name}`. {install_hint}")
    return __import__(module_name)


def require_executable(name: str, install_hint: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise EngineError(f"Missing executable `{name}`. {install_hint}")
    return resolved


def run_command(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    stdout_handle = None
    stderr_handle = None
    try:
        stdout_target: int | Any
        stderr_target: int | Any
        if stdout_path:
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_handle = stdout_path.open("w", encoding="utf-8")
            stdout_target = stdout_handle
        else:
            stdout_target = subprocess.PIPE
        if stderr_path:
            stderr_path.parent.mkdir(parents=True, exist_ok=True)
            stderr_handle = stderr_path.open("w", encoding="utf-8")
            stderr_target = stderr_handle
        else:
            stderr_target = subprocess.PIPE

        result = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            stdout=stdout_target,
            stderr=stderr_target,
            check=False,
        )
    finally:
        if stdout_handle:
            stdout_handle.close()
        if stderr_handle:
            stderr_handle.close()

    if check and result.returncode != 0:
        stderr = ""
        if stderr_path and stderr_path.is_file():
            stderr = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        elif result.stderr:
            stderr = result.stderr[-4000:]
        raise EngineError(f"Command failed ({result.returncode}): {' '.join(cmd)}\n{stderr}")
    return result


def run_command_stdout_to_file(cmd: list[str], output_path: Path, *, cwd: Path | None = None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path = output_path.parent / "engine-command.stderr.txt"
    with output_path.open("w", encoding="utf-8") as out_handle, stderr_path.open(
        "w", encoding="utf-8"
    ) as err_handle:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            stdout=out_handle,
            stderr=err_handle,
            check=False,
        )
    if result.returncode != 0:
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        raise EngineError(f"Command failed ({result.returncode}): {' '.join(cmd)}\n{stderr}")


def first_file(root: Path, suffix: str) -> Path | None:
    if not root.is_dir():
        return None
    matches = sorted(p for p in root.rglob(f"*{suffix}") if p.is_file())
    return matches[0] if matches else None


def copy_first_file(root: Path, suffix: str, output_path: Path) -> Path:
    found = first_file(root, suffix)
    if found is None:
        raise EngineError(f"No {suffix} file found under {root}")
    if found.resolve() != output_path.resolve():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(found, output_path)
    return output_path


def markdown_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    normalized = [["" if cell is None else str(cell).replace("\n", " ").strip() for cell in row] for row in rows]
    width = max(len(row) for row in normalized)
    normalized = [row + [""] * (width - len(row)) for row in normalized]
    header = normalized[0]
    body = normalized[1:]

    def fmt(row: list[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"

    lines = [fmt(header), "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend(fmt(row) for row in body)
    return "\n".join(lines) + "\n"


def collapse_spaces(text: str) -> str:
    return " ".join(text.replace("\x02", "-").split())


def render_unstructured_elements(elements: list[Any]) -> str:
    parts: list[str] = []
    last_page: int | None = None
    for element in elements:
        metadata = getattr(element, "metadata", None)
        page_number = getattr(metadata, "page_number", None) if metadata is not None else None
        if isinstance(page_number, int) and page_number != last_page:
            parts.append(f"\n<!-- PAGE {page_number} -->\n")
            last_page = page_number
        category = getattr(element, "category", "")
        text = str(element).strip()
        if not text:
            continue
        if category == "Title":
            parts.append(f"\n# {text}\n")
        elif category == "ListItem":
            parts.append(f"- {text}\n")
        else:
            parts.append(text + "\n\n")
    return "".join(parts).lstrip()


def text_cells_to_markdown(cells: list[Any]) -> str:
    rows: list[tuple[float, float, str]] = []
    for cell in cells:
        text = collapse_spaces(str(getattr(cell, "text", "") or ""))
        if not text:
            continue
        rect = getattr(cell, "rect", None)
        if rect is None:
            rows.append((0.0, float(len(rows)), text))
            continue
        xs = [float(getattr(rect, name, 0.0)) for name in ("r_x0", "r_x1", "r_x2", "r_x3")]
        ys = [float(getattr(rect, name, 0.0)) for name in ("r_y0", "r_y1", "r_y2", "r_y3")]
        rows.append((max(ys), min(xs), text))
    if not rows:
        return ""

    grouped: list[list[tuple[float, float, str]]] = []
    for y, x, text in sorted(rows, key=lambda item: (-item[0], item[1])):
        if grouped and abs(grouped[-1][0][0] - y) <= 3.0:
            grouped[-1].append((y, x, text))
        else:
            grouped.append([(y, x, text)])

    lines: list[str] = []
    for group in grouped:
        line = collapse_spaces(" ".join(text for _, _, text in sorted(group, key=lambda item: item[1])))
        if line:
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def env_override_template(engine: str) -> str | None:
    key = "MASS_COMPARE_CMD_" + engine.upper().replace("-", "_")
    return os.environ.get(key)


def run_template_override(engine: str, input_pdf: Path, output_dir: Path, output_path: Path) -> bool:
    template = env_override_template(engine)
    if not template:
        return False
    rendered = template.format(
        input=str(input_pdf),
        output=str(output_path),
        output_dir=str(output_dir),
    )
    result = subprocess.run(["/bin/sh", "-lc", rendered], text=True, capture_output=True, check=False)
    (output_dir / "engine-command.stderr.txt").write_text(result.stderr or "", encoding="utf-8")
    if result.returncode != 0:
        raise EngineError(f"Override command failed ({result.returncode}): {rendered}\n{(result.stderr or '')[-4000:]}")
    if output_path.is_file() and output_path.stat().st_size > 0:
        if result.stdout:
            (output_dir / "engine-command.stdout.txt").write_text(result.stdout, encoding="utf-8")
        return True
    if result.stdout:
        write_text(output_path, result.stdout)
        return True
    raise EngineError(f"Override command produced no stdout and did not write {output_path}: {rendered}")


def convert_markitdown(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("markitdown", input_pdf, output_dir, output_path):
        return output_path
    require_import("markitdown", "The benchmark image should install `markitdown[pdf]`.")
    from markitdown import MarkItDown  # type: ignore[import-not-found]

    converter = MarkItDown()
    with contextlib.redirect_stdout(io.StringIO()):
        if hasattr(converter, "convert_local"):
            result = converter.convert_local(input_pdf)
        else:
            result = converter.convert(str(input_pdf))
    text = getattr(result, "markdown", None) or getattr(result, "text_content", None) or str(result)
    return write_text(output_path, text)


def convert_pymupdf4llm(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("pymupdf4llm", input_pdf, output_dir, output_path):
        return output_path
    require_import("pymupdf4llm", "The benchmark image should install `pymupdf4llm`.")
    import pymupdf4llm  # type: ignore[import-not-found]

    with contextlib.redirect_stdout(io.StringIO()):
        text = pymupdf4llm.to_markdown(str(input_pdf))
    return write_text(output_path, text)


def convert_docling_parse_fallback(input_pdf: Path, output_dir: Path, output_path: Path, reason: str = "") -> Path:
    """Use Docling's native PDF parser when the full torch-backed CLI is unavailable on musl.

    Alpine can build and run `docling-parse`, but full Docling's standard PDF
    pipeline imports torch/docling-ibm-models even with `--no-ocr`.  This fallback
    still exercises Docling's parser stack and keeps the benchmark CPU-only and
    network-free.
    """
    require_import("docling_parse", "The benchmark image should install `docling-slim[format-pdf]`.")
    from docling_parse.pdf_parser import DoclingPdfParser  # type: ignore[import-not-found]

    parser = DoclingPdfParser()
    document = parser.load(str(input_pdf), lazy=False)
    parts: list[str] = []
    for page_no, page in document.iterate_pages():
        parts.append(f"<!-- PAGE {page_no} -->\n")
        cells = list(getattr(page, "textline_cells", None) or getattr(page, "char_cells", None) or [])
        parts.append(text_cells_to_markdown(cells).rstrip() + "\n\n")
    if reason:
        write_text(output_dir / "docling-fallback.txt", reason[-4000:] + "\n")
    return write_text(output_path, "".join(parts).strip() + "\n")


def convert_docling(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("docling", input_pdf, output_dir, output_path):
        return output_path
    raw_dir = output_dir / "docling-output"
    cmd = [
        "docling",
        str(input_pdf),
        "--from",
        "pdf",
        "--to",
        "md",
        "--image-export-mode",
        "placeholder",
        "--no-ocr",
        "--output",
        str(raw_dir),
    ]
    executable = shutil.which("docling")
    if executable and importlib.util.find_spec("torch") is not None:
        try:
            run_command(cmd, stderr_path=output_dir / "docling.stderr.txt")
            return copy_first_file(raw_dir, ".md", output_path)
        except Exception as exc:
            return convert_docling_parse_fallback(input_pdf, output_dir, output_path, reason=str(exc))
    reason = (
        "Full Docling CLI torch dependencies are not installed in this Alpine image; "
        "used docling-parse fallback."
        if executable
        else "Docling CLI executable not found; used docling-parse fallback."
    )
    return convert_docling_parse_fallback(input_pdf, output_dir, output_path, reason=reason)


def convert_kreuzberg(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("kreuzberg", input_pdf, output_dir, output_path):
        return output_path
    executable = shutil.which("kreuzberg")
    if executable:
        cmd = [
            executable,
            "extract",
            str(input_pdf),
            "--output-format",
            "markdown",
            "--page-markers",
            "true",
            "--ocr",
            "false",
            "--acceleration",
            "cpu",
            "--format",
            "text",
        ]
        run_command_stdout_to_file(cmd, output_path)
        return output_path

    require_import("kreuzberg", "The benchmark image should install `kreuzberg`.")
    import kreuzberg  # type: ignore[import-not-found]

    async def extract() -> Any:
        return await kreuzberg.extract_file(str(input_pdf), force_ocr=False)

    result = asyncio.run(extract())
    text = getattr(result, "content", None) or str(result)
    return write_text(output_path, str(text))


def convert_opendataloader(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("opendataloader", input_pdf, output_dir, output_path):
        return output_path
    require_executable(
        "opendataloader-pdf", "The benchmark image should install the `opendataloader-pdf` CLI."
    )
    raw_dir = output_dir / "opendataloader-output"
    cmd = [
        "opendataloader-pdf",
        "--output-dir",
        str(raw_dir),
        "--format",
        "markdown",
        "--quiet",
        "--markdown-page-separator",
        "<!-- PAGE %page-number% -->",
        str(input_pdf),
    ]
    run_command(cmd, stderr_path=output_dir / "opendataloader.stderr.txt")
    return copy_first_file(raw_dir, ".md", output_path)


def resolve_nutrient_binary() -> str:
    override = os.environ.get("PDF_TO_MARKDOWN_BINARY")
    if override and Path(override).is_file():
        return override
    for name in ["pdf-to-markdown", "nutrient-linux-amd64", "nutrient-linux-arm64"]:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    for candidate in Path("/opt").glob("**/nutrient-linux-*"):
        if candidate.is_file():
            return str(candidate)
    raise EngineError(
        "Missing Nutrient/PSPDFKit pdf-to-markdown binary. Pass "
        "`--pdf-to-markdown-binary /path/to/nutrient-linux-*` to the outer CLI, "
        "or bake the binary into the image and set PDF_TO_MARKDOWN_BINARY."
    )


def nutrient_cmd(executable: str, *args: str) -> list[str]:
    loader = os.environ.get("NUTRIENT_GLIBC_LOADER")
    library_path = os.environ.get("NUTRIENT_GLIBC_LIBRARY_PATH")
    if loader and library_path and Path(loader).is_file():
        return [loader, "--library-path", library_path, executable, *args]
    return [executable, *args]


def convert_nutrient(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("nutrient_pdf_to_markdown", input_pdf, output_dir, output_path):
        return output_path
    executable = resolve_nutrient_binary()
    first = subprocess.run(
        nutrient_cmd(executable, str(input_pdf), str(output_path)),
        text=True,
        capture_output=True,
        check=False,
    )
    if first.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 0:
        (output_dir / "nutrient.stderr.txt").write_text(first.stderr, encoding="utf-8")
        return output_path

    # Some versions print Markdown to stdout instead of accepting an output path.
    with output_path.open("w", encoding="utf-8") as handle:
        second = subprocess.run(
            nutrient_cmd(executable, str(input_pdf)),
            text=True,
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
        )
    (output_dir / "nutrient.stderr.txt").write_text(
        (first.stderr or "") + "\n--- stdout-mode retry ---\n" + (second.stderr or ""),
        encoding="utf-8",
    )
    if second.returncode != 0:
        raise EngineError(
            f"Nutrient pdf-to-markdown failed. First returncode={first.returncode}; "
            f"retry returncode={second.returncode}. See nutrient.stderr.txt."
        )
    return output_path


def run_first_successful_template(
    engine: str,
    input_pdf: Path,
    output_dir: Path,
    output_path: Path,
    templates: list[str],
) -> Path:
    if run_template_override(engine, input_pdf, output_dir, output_path):
        return output_path

    attempts: list[dict[str, Any]] = []
    for template in templates:
        rendered = template.format(input=str(input_pdf), output=str(output_path), output_dir=str(output_dir))
        result = subprocess.run(["/bin/sh", "-lc", rendered], text=True, capture_output=True, check=False)
        attempts.append(
            {
                "command": rendered,
                "returncode": result.returncode,
                "stdout_tail": result.stdout[-1000:],
                "stderr_tail": result.stderr[-1000:],
            }
        )
        if result.returncode == 0:
            if output_path.is_file() and output_path.stat().st_size > 0:
                write_json(output_dir / "template-attempts.json", attempts)
                return output_path
            if result.stdout.strip():
                write_text(output_path, result.stdout)
                write_json(output_dir / "template-attempts.json", attempts)
                return output_path
    write_json(output_dir / "template-attempts.json", attempts)
    raise EngineError(
        f"No default command template worked for `{engine}`. "
        f"Provide an override with --engine-command {engine}='command using {{input}} and {{output}}'."
    )


def convert_liteparse(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("liteparse", input_pdf, output_dir, output_path):
        return output_path
    require_import("liteparse", "The image should install `liteparse`.")
    from liteparse import LiteParse  # type: ignore[import-not-found]

    parser = LiteParse(ocr_enabled=False, output_format="json", quiet=True)
    result = parser.parse(input_pdf)
    if is_dataclass(result):
        payload = asdict(result)
    else:
        payload = getattr(result, "__dict__", {"text": str(result)})
    return write_json(output_path, payload)


def convert_edgeparse(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("edgeparse", input_pdf, output_dir, output_path):
        return output_path
    if importlib.util.find_spec("edgeparse") is not None:
        import edgeparse  # type: ignore[import-not-found]

        if hasattr(edgeparse, "convert"):
            text = edgeparse.convert(str(input_pdf), format="markdown")
            return write_text(output_path, str(text))
    return run_first_successful_template(
        "edgeparse",
        input_pdf,
        output_dir,
        output_path,
        [
            "edgeparse {input} --format markdown --output-dir {output_dir} && cp {output_dir}/*.md {output}",
            "edgeparse {input} -f markdown -o {output_dir} && cp {output_dir}/*.md {output}",
        ],
    )


def convert_pdf_oxide(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("pdf_oxide", input_pdf, output_dir, output_path):
        return output_path
    require_import("pdf_oxide", "The image should install `pdf_oxide`.")
    from pdf_oxide import PdfDocument  # type: ignore[import-not-found]

    document = PdfDocument(str(input_pdf))
    text = document.to_markdown_all(
        preserve_layout=True,
        detect_headings=True,
        include_images=False,
    )
    return write_text(output_path, text)


def convert_unstructured(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("unstructured", input_pdf, output_dir, output_path):
        return output_path
    if importlib.util.find_spec("unstructured") is None:
        raise EngineError("Missing Python dependency `unstructured`. The image should install `unstructured`.")

    try:
        from unstructured.partition.pdf import partition_pdf  # type: ignore[import-not-found]

        elements = partition_pdf(
            filename=str(input_pdf),
            strategy="fast",
            infer_table_structure=False,
            include_page_breaks=True,
        )
        return write_text(output_path, render_unstructured_elements(list(elements)))
    except Exception as exc:
        # `unstructured.partition.pdf` imports hi-res/OCR inference modules at import
        # time in recent releases, even when strategy="fast".  Keep the benchmark
        # non-OCR by extracting page text with pdfminer and partitioning that text
        # with Unstructured's text partitioner.
        write_text(output_dir / "unstructured-pdf-fallback.txt", str(exc)[-4000:] + "\n")

    from pdfminer.high_level import extract_text  # type: ignore[import-not-found]
    from pypdf import PdfReader  # type: ignore[import-not-found]

    reader = PdfReader(str(input_pdf))
    parts: list[str] = []
    for page_index in range(len(reader.pages)):
        page_text = extract_text(str(input_pdf), page_numbers=[page_index]) or ""
        parts.append(f"<!-- PAGE {page_index + 1} -->\n")
        # Avoid NLTK/model downloads in the conversion container.  The fallback is
        # deliberately raw pdfminer text after importing the Unstructured package.
        parts.append(page_text.strip() + "\n\n")
    return write_text(output_path, "".join(parts).lstrip())


def convert_pymupdf(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("pymupdf", input_pdf, output_dir, output_path):
        return output_path
    require_import("fitz", "The image should install `pymupdf`.")
    import fitz  # type: ignore[import-not-found]

    doc = fitz.open(str(input_pdf))
    parts: list[str] = []
    for idx, page in enumerate(doc, start=1):
        parts.append(f"<!-- PAGE {idx} -->\n")
        try:
            text = page.get_text("markdown")
        except Exception:
            text = page.get_text("text")
        parts.append((text or "").rstrip() + "\n\n")
    return write_text(output_path, "".join(parts))


def convert_pdfplumber(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("pdfplumber", input_pdf, output_dir, output_path):
        return output_path
    require_import("pdfplumber", "The image should install `pdfplumber`.")
    import pdfplumber  # type: ignore[import-not-found]

    parts: list[str] = []
    with pdfplumber.open(str(input_pdf)) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            parts.append(f"<!-- PAGE {idx} -->\n")
            text = page.extract_text(layout=True) or ""
            if text.strip():
                parts.append(text.rstrip() + "\n\n")
            tables = page.extract_tables() or []
            for table_index, table in enumerate(tables, start=1):
                parts.append(f"\n<!-- TABLE {idx}.{table_index} -->\n")
                parts.append(markdown_table(table))
                parts.append("\n")
    return write_text(output_path, "".join(parts))


def convert_pdfminer_six(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("pdfminer_six", input_pdf, output_dir, output_path):
        return output_path
    require_import("pdfminer", "The image should install `pdfminer.six`.")
    from pdfminer.high_level import extract_text  # type: ignore[import-not-found]

    return write_text(output_path, extract_text(str(input_pdf)) or "")


def convert_pypdfium2(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("pypdfium2", input_pdf, output_dir, output_path):
        return output_path
    require_import("pypdfium2", "The image should install `pypdfium2`.")
    import pypdfium2 as pdfium  # type: ignore[import-not-found]

    pdf = pdfium.PdfDocument(str(input_pdf))
    parts: list[str] = []
    for idx in range(len(pdf)):
        page = pdf[idx]
        textpage = page.get_textpage()
        parts.append(f"<!-- PAGE {idx + 1} -->\n")
        parts.append((textpage.get_text_range() or "").rstrip() + "\n\n")
        textpage.close()
        page.close()
    pdf.close()
    return write_text(output_path, "".join(parts))


def convert_pypdf(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("pypdf", input_pdf, output_dir, output_path):
        return output_path
    require_import("pypdf", "The image should install `pypdf`.")
    from pypdf import PdfReader  # type: ignore[import-not-found]

    reader = PdfReader(str(input_pdf))
    parts: list[str] = []
    for idx, page in enumerate(reader.pages, start=1):
        parts.append(f"<!-- PAGE {idx} -->\n")
        parts.append((page.extract_text() or "").rstrip() + "\n\n")
    return write_text(output_path, "".join(parts))


def convert_poppler_pdftotext(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("poppler_pdftotext", input_pdf, output_dir, output_path):
        return output_path
    require_executable("pdftotext", "The image should install Alpine package `poppler-utils`.")
    cmd = ["pdftotext", "-layout", "-enc", "UTF-8", str(input_pdf), str(output_path)]
    run_command(cmd, stderr_path=output_dir / "pdftotext.stderr.txt")
    return output_path


def convert_camelot(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("camelot", input_pdf, output_dir, output_path):
        return output_path
    require_import("camelot", "The image should install `camelot-py[base]`.")
    import camelot  # type: ignore[import-not-found]

    parts: list[str] = ["# Camelot table extraction\n\n"]
    errors: list[str] = []
    for flavor in ["lattice", "stream"]:
        try:
            tables = camelot.read_pdf(str(input_pdf), pages="all", flavor=flavor)
        except Exception as exc:
            errors.append(f"{flavor}: {exc}")
            continue
        parts.append(f"## flavor={flavor}\n\n")
        if len(tables) == 0:
            parts.append("No tables detected.\n\n")
            continue
        for index, table in enumerate(tables, start=1):
            parts.append(f"### Table {index}\n\n")
            try:
                parts.append(table.df.to_markdown(index=False) + "\n\n")
            except Exception:
                parts.append(table.df.to_csv(index=False) + "\n\n")
    if errors:
        write_text(output_dir / "camelot-errors.txt", "\n".join(errors) + "\n")
    return write_text(output_path, "".join(parts))


def convert_tabula_java(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("tabula_java", input_pdf, output_dir, output_path):
        return output_path
    jar = Path(os.environ.get("TABULA_JAR", "/opt/tabula/tabula.jar"))
    if jar.is_file():
        cmd = ["java", "-jar", str(jar), "-p", "all", "-f", "JSON", str(input_pdf)]
    else:
        executable = require_executable(
            "tabula",
            "Install tabula-java or mount a jar and set TABULA_JAR=/path/to/tabula.jar.",
        )
        cmd = [executable, "-p", "all", "-f", "JSON", str(input_pdf)]
    run_command_stdout_to_file(cmd, output_path)
    return output_path


def convert_markit_ai(input_pdf: Path, output_dir: Path, output_path: Path) -> Path:
    if run_template_override("markit_ai", input_pdf, output_dir, output_path):
        return output_path
    executable = shutil.which("markit-ai") or shutil.which("markit")
    if executable:
        cmd = [executable, "convert", str(input_pdf), "-q", "-o", str(output_path)]
    else:
        cmd = ["npx", "--offline", "markit-ai@0.5.0", "convert", str(input_pdf), "-q", "-o", str(output_path)]
    run_command(cmd, stderr_path=output_dir / "markit-ai.stderr.txt")
    return output_path


CONVERTERS = {
    "markitdown": convert_markitdown,
    "pymupdf4llm": convert_pymupdf4llm,
    "docling": convert_docling,
    "kreuzberg": convert_kreuzberg,
    "opendataloader": convert_opendataloader,
    "nutrient_pdf_to_markdown": convert_nutrient,
    "liteparse": convert_liteparse,
    "edgeparse": convert_edgeparse,
    "pdf_oxide": convert_pdf_oxide,
    "unstructured": convert_unstructured,
    "pymupdf": convert_pymupdf,
    "pdfplumber": convert_pdfplumber,
    "pdfminer_six": convert_pdfminer_six,
    "pypdfium2": convert_pypdfium2,
    "pypdf": convert_pypdf,
    "poppler_pdftotext": convert_poppler_pdftotext,
    "camelot": convert_camelot,
    "tabula_java": convert_tabula_java,
    "markit_ai": convert_markit_ai,
}


def run_engine(engine: str, input_pdf: Path, output_dir: Path) -> dict[str, Any]:
    if engine not in CONVERTERS:
        raise EngineError(f"Unsupported engine: {engine}")
    output_name, output_format = ENGINE_OUTPUTS[engine]
    output_path = output_dir / output_name
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    converter = CONVERTERS[engine]
    produced = converter(input_pdf, output_dir, output_path)
    seconds = time.perf_counter() - started
    return {
        "engine": engine,
        "status": "ok",
        "seconds_inside_container": round(seconds, 4),
        "output_format": output_format,
        "output_path": str(produced),
        "output_bytes": produced.stat().st_size if produced.is_file() else 0,
    }


def run_one_engine_command(engine: str, input_pdf: Path, output_root: Path, timeout_seconds: int) -> dict[str, Any]:
    output_dir = output_root / engine
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / "engine-runner.stdout.txt"
    stderr_path = output_dir / "engine-runner.stderr.txt"
    result_path = output_dir / "result.json"
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--engine",
        engine,
        "--input",
        str(input_pdf),
        "--output-dir",
        str(output_dir),
    ]
    started = time.perf_counter()
    try:
        with stdout_path.open("w", encoding="utf-8") as out_handle, stderr_path.open(
            "w", encoding="utf-8"
        ) as err_handle:
            completed = subprocess.run(
                cmd,
                text=True,
                stdout=out_handle,
                stderr=err_handle,
                check=False,
                timeout=timeout_seconds,
            )
        returncode: int | str = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        returncode = ""
        timed_out = True
        with stderr_path.open("a", encoding="utf-8") as err_handle:
            err_handle.write(f"\nTimed out after {timeout_seconds} seconds.\n")
    wall_seconds = time.perf_counter() - started

    payload: dict[str, Any]
    if result_path.is_file():
        try:
            loaded = json.loads(result_path.read_text(encoding="utf-8"))
            payload = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            payload = {}
    else:
        payload = {}

    if timed_out:
        payload = {
            "engine": engine,
            "status": "timeout",
            "error_type": "TimeoutExpired",
            "error": f"Timed out after {timeout_seconds} seconds",
        }
        write_json(result_path, payload)
    elif not payload:
        payload = {
            "engine": engine,
            "status": "failed",
            "error_type": "MissingResult",
            "error": "Child engine process did not write result.json",
        }
        write_json(result_path, payload)

    payload["child_returncode"] = returncode
    payload["child_wall_seconds"] = round(wall_seconds, 4)
    payload["child_stdout_path"] = str(stdout_path)
    payload["child_stderr_path"] = str(stderr_path)
    write_json(result_path, payload)
    return payload


def run_batch(engines: list[str], input_pdf: Path, output_root: Path, *, jobs: int, timeout_seconds: int) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    max_workers = min(max(1, jobs), len(engines))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(run_one_engine_command, engine, input_pdf, output_root, timeout_seconds): engine
            for engine in engines
        }
        for future in as_completed(futures):
            engine = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                output_dir = output_root / engine
                output_dir.mkdir(parents=True, exist_ok=True)
                payload = {
                    "engine": engine,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
                write_json(output_dir / "result.json", payload)
                results.append(payload)
    elapsed = time.perf_counter() - started
    payload = {
        "status": "completed",
        "seconds_inside_container_batch": round(elapsed, 4),
        "jobs": max_workers,
        "engines": engines,
        "results": sorted(results, key=lambda item: engines.index(str(item.get("engine", ""))) if str(item.get("engine", "")) in engines else len(engines)),
    }
    write_json(output_root / "_batch_result.json", payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run PDF comparator engine(s) inside the benchmark container.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--engine", choices=sorted(CONVERTERS), help="Run one engine.")
    mode.add_argument("--engines", help="Comma-separated engines to run in one container.")
    parser.add_argument("--input", required=True, help="Container-local input PDF path.")
    parser.add_argument("--output-dir", help="Container-local output directory for --engine.")
    parser.add_argument("--output-root", help="Container-local output root for --engines.")
    parser.add_argument("--jobs", type=int, default=1, help="Parallel child engine processes for --engines.")
    parser.add_argument("--timeout-seconds", type=int, default=1800, help="Per-child timeout for --engines.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.engine:
        if not args.output_dir:
            parser.error("--output-dir is required with --engine")
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        result_path = output_dir / "result.json"
        started = time.perf_counter()
        try:
            result = run_engine(str(args.engine), Path(args.input), output_dir)
            write_json(result_path, result)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        except Exception as exc:
            payload = {
                "engine": str(args.engine),
                "status": "failed",
                "seconds_inside_container": round(time.perf_counter() - started, 4),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            write_json(result_path, payload)
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
            return 1

    if not args.output_root:
        parser.error("--output-root is required with --engines")
    engines = [engine.strip() for engine in str(args.engines).split(",") if engine.strip()]
    invalid = [engine for engine in engines if engine not in CONVERTERS]
    if invalid:
        parser.error(f"Unsupported engine(s): {', '.join(invalid)}")
    payload = run_batch(
        engines,
        Path(args.input),
        Path(args.output_root),
        jobs=int(args.jobs),
        timeout_seconds=int(args.timeout_seconds),
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
