#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


class EngineError(RuntimeError):
    pass


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": len(value)}
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def write_json(path: Path, payload: Any) -> Path:
    return write_text(path, json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n")


def json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": len(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return json_safe(value.model_dump())
    if hasattr(value, "__dict__"):
        return {k: json_safe(v) for k, v in vars(value).items() if not k.startswith("_")}
    return repr(value)


def image_extension_from_bytes(data: bytes, default: str = "png") -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    return default


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


def scan_image_files(root: Path) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    files = sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS and p.stat().st_size > 0
    )
    return [
        {
            "path": str(path),
            "relative_path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "format": path.suffix.lower().lstrip("."),
        }
        for path in files
    ]


def export_pymupdf_embedded_images(input_pdf: Path, image_dir: Path, *, prefix: str = "image") -> list[dict[str, Any]]:
    import fitz  # type: ignore[import-not-found]

    image_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(input_pdf))
    records: list[dict[str, Any]] = []
    seen_xrefs: set[int] = set()
    for page_index in range(len(doc)):
        page = doc[page_index]
        for image_index, img in enumerate(page.get_images(full=True), start=1):
            xref = int(img[0])
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            try:
                extracted = doc.extract_image(xref)
            except Exception:
                continue
            data = extracted.get("image")
            if not data:
                continue
            ext = str(extracted.get("ext") or "png").lower().lstrip(".")
            if ext == "jpe":
                ext = "jpg"
            out = image_dir / f"{prefix}_p{page_index + 1:04d}_{image_index:03d}_xref{xref}.{ext}"
            out.write_bytes(data)
            records.append(
                {
                    "path": str(out),
                    "relative_path": out.relative_to(image_dir.parent).as_posix(),
                    "page": page_index + 1,
                    "xref": xref,
                    "bytes": out.stat().st_size,
                    "format": ext,
                    "kind": "embedded_pdf_image",
                }
            )
    doc.close()
    return records


def export_pymupdf_page_renders(input_pdf: Path, image_dir: Path, *, dpi: int = 160) -> list[dict[str, Any]]:
    import fitz  # type: ignore[import-not-found]

    image_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(input_pdf))
    records: list[dict[str, Any]] = []
    for page_index in range(len(doc)):
        page = doc[page_index]
        out = image_dir / f"page_{page_index + 1:04d}.png"
        pixmap = page.get_pixmap(dpi=dpi)
        pixmap.save(str(out))
        records.append(
            {
                "path": str(out),
                "relative_path": out.relative_to(image_dir.parent).as_posix(),
                "page": page_index + 1,
                "bytes": out.stat().st_size,
                "format": "png",
                "kind": "page_render",
                "dpi": dpi,
            }
        )
    doc.close()
    return records


def dataframe_like_table(table: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    bbox = getattr(table, "bbox", None)
    if bbox is not None:
        payload["bbox"] = list(bbox)
    try:
        payload["rows"] = table.extract()
    except Exception as exc:
        payload["extract_error"] = str(exc)
    try:
        payload["markdown"] = table.to_markdown()
    except Exception:
        pass
    return payload


def convert_markitdown(input_pdf: Path, output_dir: Path) -> tuple[Path, str, dict[str, Any]]:
    from markitdown import MarkItDown  # type: ignore[import-not-found]

    output_path = output_dir / "output.md"
    converter = MarkItDown()
    with contextlib.redirect_stdout(io.StringIO()):
        if hasattr(converter, "convert_local"):
            result = converter.convert_local(input_pdf)
        else:
            result = converter.convert(str(input_pdf))
    text = getattr(result, "markdown", None) or getattr(result, "text_content", None) or str(result)
    write_text(output_path, text)
    image_records = export_pymupdf_embedded_images(input_pdf, output_dir / "images", prefix="markitdown_sidecar")
    return output_path, "markdown", {
        "image_strategy": "pymupdf_embedded_image_sidecar_for_markitdown",
        "native_image_export": False,
        "sidecar_image_count": len(image_records),
    }


def convert_pymupdf4llm(input_pdf: Path, output_dir: Path) -> tuple[Path, str, dict[str, Any]]:
    import pymupdf4llm  # type: ignore[import-not-found]

    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "output.json"
    try:
        text = pymupdf4llm.to_json(
            str(input_pdf),
            write_images=True,
            image_path=str(image_dir),
            embed_images=False,
            image_size_limit=0,
        )
        write_text(output_path, str(text))
        return output_path, "json", {"image_strategy": "pymupdf4llm_to_json_write_images"}
    except Exception as exc:
        markdown_path = output_dir / "output.md"
        text = pymupdf4llm.to_markdown(
            str(input_pdf),
            write_images=True,
            image_path=str(image_dir),
            embed_images=False,
            image_size_limit=0,
        )
        write_text(markdown_path, str(text))
        return markdown_path, "markdown", {
            "image_strategy": "pymupdf4llm_to_markdown_write_images",
            "json_fallback_reason": str(exc)[-1000:],
        }


def convert_pymupdf(input_pdf: Path, output_dir: Path) -> tuple[Path, str, dict[str, Any]]:
    import fitz  # type: ignore[import-not-found]

    embedded = export_pymupdf_embedded_images(input_pdf, output_dir / "images", prefix="pymupdf")
    renders = export_pymupdf_page_renders(input_pdf, output_dir / "page-renders")
    doc = fitz.open(str(input_pdf))
    pages: list[dict[str, Any]] = []
    for page_index in range(len(doc)):
        page = doc[page_index]
        page_payload: dict[str, Any] = {
            "page": page_index + 1,
            "text": page.get_text("dict"),
            "tables": [],
        }
        try:
            tables = page.find_tables()
            page_payload["tables"] = [dataframe_like_table(table) for table in tables]
        except Exception as exc:
            page_payload["table_error"] = str(exc)
        pages.append(page_payload)
    doc.close()
    output_path = output_dir / "output.json"
    write_json(
        output_path,
        {
            "engine": "pymupdf",
            "pages": pages,
            "image_assets": embedded + renders,
        },
    )
    return output_path, "json", {
        "image_strategy": "pymupdf_embedded_images_plus_page_renders",
        "embedded_image_count": len(embedded),
        "page_render_count": len(renders),
    }


def convert_docling(input_pdf: Path, output_dir: Path) -> tuple[Path, str, dict[str, Any]]:
    raw_dir = output_dir / "docling-output"
    templates = [
        [
            "docling",
            str(input_pdf),
            "--from",
            "pdf",
            "--to",
            "json",
            "--image-export-mode",
            "referenced",
            "--no-ocr",
            "--output",
            str(raw_dir),
        ],
        [
            "docling",
            str(input_pdf),
            "--from",
            "pdf",
            "--format",
            "json",
            "--image-export-mode",
            "referenced",
            "--no-ocr",
            "--output",
            str(raw_dir),
        ],
    ]
    attempts: list[dict[str, Any]] = []
    for cmd in templates:
        result = run_command(cmd, stderr_path=output_dir / "docling.stderr.txt", check=False)
        attempts.append({"command": cmd, "returncode": result.returncode})
        if result.returncode == 0 and first_file(raw_dir, ".json"):
            output_path = copy_first_file(raw_dir, ".json", output_dir / "output.json")
            write_json(output_dir / "command-attempts.json", attempts)
            return output_path, "json", {"image_strategy": "docling_referenced_images"}
    write_json(output_dir / "command-attempts.json", attempts)
    raise EngineError("Docling did not produce JSON output with referenced image export")


def convert_opendataloader(input_pdf: Path, output_dir: Path) -> tuple[Path, str, dict[str, Any]]:
    raw_dir = output_dir / "opendataloader-output"
    image_dir = raw_dir / "images"
    cmd = [
        "opendataloader-pdf",
        "--output-dir",
        str(raw_dir),
        "--format",
        "json",
        "--image-output",
        "external",
        "--image-format",
        "png",
        "--image-dir",
        str(image_dir),
        "--quiet",
        str(input_pdf),
    ]
    run_command(cmd, stderr_path=output_dir / "opendataloader.stderr.txt")
    output_path = copy_first_file(raw_dir, ".json", output_dir / "output.json")
    return output_path, "json", {"image_strategy": "opendataloader_external_images"}


def convert_kreuzberg(input_pdf: Path, output_dir: Path) -> tuple[Path, str, dict[str, Any]]:
    from kreuzberg import (  # type: ignore[import-not-found]
        ExtractionConfig,
        ImageExtractionConfig,
        OutputFormat,
        ResultFormat,
        extract_file_sync,
    )

    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    result = extract_file_sync(
        input_pdf,
        config=ExtractionConfig(
            output_format=OutputFormat.HTML,
            result_format=ResultFormat.UNIFIED,
            images=ImageExtractionConfig(extract_images=True),
        ),
    )
    content_path = write_text(output_dir / "content.html", str(getattr(result, "content", "") or ""))
    image_records: list[dict[str, Any]] = []
    for index, raw_image in enumerate(getattr(result, "images", []) or [], start=1):
        image = json_safe(raw_image)
        data = raw_image.get("data") if isinstance(raw_image, dict) else getattr(raw_image, "data", None)
        if isinstance(data, bytes) and data:
            ext = image_extension_from_bytes(data)
            out = image_dir / f"kreuzberg_image_{index:04d}.{ext}"
            out.write_bytes(data)
            image["data"] = {"__bytes__": len(data), "exported_path": str(out)}
            image["relative_path"] = out.relative_to(output_dir).as_posix()
            image["bytes"] = out.stat().st_size
            image["format"] = ext
            image_records.append(image)
    output_path = output_dir / "output.json"
    write_json(
        output_path,
        {
            "engine": "kreuzberg",
            "content_format": "html",
            "content_path": str(content_path),
            "content": getattr(result, "content", ""),
            "metadata": json_safe(getattr(result, "metadata", None)),
            "tables": json_safe(getattr(result, "tables", None)),
            "images": image_records,
            "mime_type": getattr(result, "mime_type", None),
            "output_format": json_safe(getattr(result, "output_format", None)),
            "result_format": json_safe(getattr(result, "result_format", None)),
            "processing_warnings": json_safe(getattr(result, "processing_warnings", None)),
            "quality_score": json_safe(getattr(result, "quality_score", None)),
        },
    )
    return output_path, "json", {
        "image_strategy": "kreuzberg_python_api_extract_images",
        "content_html_path": str(content_path),
        "exported_image_count": len(image_records),
    }


def convert_kreuzberg_cli(input_pdf: Path, output_dir: Path) -> tuple[Path, str, dict[str, Any]]:
    output_path = output_dir / "output.json"
    cmd = [
        "kreuzberg",
        "extract",
        str(input_pdf),
        "--content-format",
        "html",
        "--format",
        "json",
        "--extract-images",
        "true",
        "--pdf-extract-images",
        "true",
        "--ocr",
        "false",
        "--acceleration",
        "cpu",
    ]
    result = run_command(cmd, cwd=output_dir, stderr_path=output_dir / "kreuzberg.stderr.txt", check=False)
    if result.returncode != 0:
        cmd[cmd.index("--content-format")] = "--output-format"
        result = run_command(cmd, cwd=output_dir, stderr_path=output_dir / "kreuzberg.stderr.txt", check=False)
    if result.returncode != 0:
        raise EngineError("Kreuzberg failed to produce structured HTML-in-JSON output")
    write_text(output_path, result.stdout or "")
    return output_path, "json", {"image_strategy": "kreuzberg_extract_images"}


def convert_edgeparse(input_pdf: Path, output_dir: Path) -> tuple[Path, str, dict[str, Any]]:
    raw_dir = output_dir / "edgeparse-output"
    attempts: list[dict[str, Any]] = []
    templates = [
        [
            "edgeparse",
            str(input_pdf),
            "-f",
            "json",
            "--image-output",
            "external",
            "--image-format",
            "png",
            "-o",
            str(raw_dir),
        ],
        [
            "edgeparse",
            str(input_pdf),
            "--format",
            "json",
            "--image-output",
            "external",
            "--image-format",
            "png",
            "--output-dir",
            str(raw_dir),
        ],
    ]
    for cmd in templates:
        result = run_command(cmd, stderr_path=output_dir / "edgeparse.stderr.txt", check=False)
        attempts.append({"command": cmd, "returncode": result.returncode})
        if result.returncode == 0:
            if first_file(raw_dir, ".json"):
                output_path = copy_first_file(raw_dir, ".json", output_dir / "output.json")
                write_json(output_dir / "command-attempts.json", attempts)
                return output_path, "json", {"image_strategy": "edgeparse_external_images"}
            if result.stdout.strip():
                output_path = write_text(output_dir / "output.json", result.stdout)
                write_json(output_dir / "command-attempts.json", attempts)
                return output_path, "json", {"image_strategy": "edgeparse_stdout_json_external_images"}
    write_json(output_dir / "command-attempts.json", attempts)
    raise EngineError("EdgeParse did not produce JSON output with external image export")


def convert_pdf_oxide(input_pdf: Path, output_dir: Path) -> tuple[Path, str, dict[str, Any]]:
    from pdf_oxide import PdfDocument  # type: ignore[import-not-found]

    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    doc = PdfDocument(str(input_pdf))
    output_path = output_dir / "output.md"
    markdown = doc.to_markdown_all(
        preserve_layout=True,
        detect_headings=True,
        include_images=True,
        embed_images=False,
        image_output_dir=str(image_dir),
    )
    write_text(output_path, str(markdown))
    return output_path, "markdown", {"image_strategy": "pdf_oxide_markdown_external_images"}


def resolve_nutrient_binary() -> str:
    override = os.environ.get("PDF_TO_MARKDOWN_BINARY")
    if override and Path(override).is_file():
        return override
    for name in ["pdf-to-markdown", "nutrient-linux-amd64", "nutrient-linux-arm64"]:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    for candidate in (Path.home() / ".local" / "share" / "nutrient" / "cli").glob("nutrient-linux-*"):
        if candidate.is_file():
            return str(candidate)
    raise EngineError(
        "Missing Nutrient/PSPDFKit pdf-to-markdown binary. Set PDF_TO_MARKDOWN_BINARY "
        "or install the binary before benchmarking this engine."
    )


def convert_nutrient(input_pdf: Path, output_dir: Path) -> tuple[Path, str, dict[str, Any]]:
    executable = resolve_nutrient_binary()
    output_path = output_dir / "output.md"
    templates = [
        [executable, "--enable-image-export", str(input_pdf), str(output_path)],
        [executable, str(input_pdf), str(output_path), "--enable-image-export"],
    ]
    attempts: list[dict[str, Any]] = []
    for cmd in templates:
        result = run_command(cmd, stderr_path=output_dir / "nutrient.stderr.txt", check=False)
        attempts.append({"command": cmd, "returncode": result.returncode})
        if result.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 0:
            write_json(output_dir / "command-attempts.json", attempts)
            return output_path, "markdown", {"image_strategy": "nutrient_enable_image_export"}
    write_json(output_dir / "command-attempts.json", attempts)
    raise EngineError("Nutrient pdf-to-markdown did not produce Markdown with image export")


def convert_unstructured(input_pdf: Path, output_dir: Path) -> tuple[Path, str, dict[str, Any]]:
    from unstructured.partition.pdf import partition_pdf  # type: ignore[import-not-found]

    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    elements = partition_pdf(
        filename=str(input_pdf),
        strategy="hi_res",
        infer_table_structure=True,
        extract_image_block_types=["Image", "Table"],
        extract_image_block_to_payload=False,
        extract_image_block_output_dir=str(image_dir),
    )
    payload: list[dict[str, Any]] = []
    for element in elements:
        if hasattr(element, "to_dict"):
            payload.append(element.to_dict())
        else:
            payload.append({"category": getattr(element, "category", ""), "text": str(element)})
    output_path = output_dir / "output.json"
    write_json(output_path, payload)
    return output_path, "json", {"image_strategy": "unstructured_hi_res_image_and_table_blocks"}


CONVERTERS = {
    "markitdown": convert_markitdown,
    "pymupdf4llm": convert_pymupdf4llm,
    "docling": convert_docling,
    "kreuzberg": convert_kreuzberg,
    "opendataloader": convert_opendataloader,
    "edgeparse": convert_edgeparse,
    "pdf_oxide": convert_pdf_oxide,
    "pymupdf": convert_pymupdf,
    "nutrient_pdf_to_markdown": convert_nutrient,
    "unstructured": convert_unstructured,
}


def run_engine(engine: str, input_pdf: Path, output_dir: Path) -> dict[str, Any]:
    if engine not in CONVERTERS:
        raise EngineError(f"Unsupported engine: {engine}")
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    output_path, output_format, details = CONVERTERS[engine](input_pdf, output_dir)
    seconds = time.perf_counter() - started
    image_files = scan_image_files(output_dir)
    image_manifest_path = write_json(output_dir / "image_manifest.json", image_files)
    return {
        "engine": engine,
        "status": "ok",
        "seconds_inside_process": round(seconds, 4),
        "primary_output_format": output_format,
        "primary_output_path": str(output_path),
        "primary_output_bytes": output_path.stat().st_size if output_path.is_file() else 0,
        "image_manifest_path": str(image_manifest_path),
        "image_count": len(image_files),
        "image_bytes": sum(int(item.get("bytes", 0)) for item in image_files),
        "details": details,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one table/image PDF benchmark engine.")
    parser.add_argument("--engine", required=True, choices=sorted(CONVERTERS))
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"
    try:
        result = run_engine(str(args.engine), Path(args.input), output_dir)
        write_json(result_path, result)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        payload = {
            "engine": str(args.engine),
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        write_json(result_path, payload)
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
