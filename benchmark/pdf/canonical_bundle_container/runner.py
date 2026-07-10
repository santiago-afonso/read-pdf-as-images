#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


class BundleError(RuntimeError):
    pass


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": len(value)}
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "model_dump"):
        return json_safe(value.model_dump())
    if hasattr(value, "__dict__"):
        return {k: json_safe(v) for k, v in vars(value).items() if not k.startswith("_")}
    return repr(value)


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
    return json_default(value)


def write_json(path: Path, payload: Any) -> Path:
    return write_text(path, json.dumps(payload, indent=2, ensure_ascii=False, default=json_default) + "\n")


def parse_pages(spec: str | None, page_count: int) -> list[int]:
    if spec is None or spec.strip().lower() in {"", "all", "*"}:
        return list(range(1, page_count + 1))
    pages: list[int] = []
    seen: set[int] = set()
    cleaned = spec.strip().replace("[", "").replace("]", "").replace(" ", "")
    for part in cleaned.split(","):
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            start, end = int(left), int(right)
        else:
            start = end = int(part)
        if start < 1 or end < start or end > page_count:
            raise ValueError(f"Invalid page range {part!r} for PDF with {page_count} pages")
        for page in range(start, end + 1):
            if page not in seen:
                seen.add(page)
                pages.append(page)
    return pages


def markdown_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    normalized = [["" if cell is None else str(cell).replace("\n", " ").strip() for cell in row] for row in rows]
    width = max(len(row) for row in normalized)
    normalized = [row + [""] * (width - len(row)) for row in normalized]

    def fmt(row: list[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"

    return "\n".join(
        [fmt(normalized[0]), "| " + " | ".join("---" for _ in normalized[0]) + " |"]
        + [fmt(row) for row in normalized[1:]]
    ) + "\n"


def html_table(rows: list[list[Any]]) -> str:
    out = ["<table>"]
    for row_index, row in enumerate(rows):
        tag = "th" if row_index == 0 else "td"
        out.append("  <tr>" + "".join(f"<{tag}>{html.escape('' if cell is None else str(cell))}</{tag}>" for cell in row) + "</tr>")
    out.append("</table>")
    return "\n".join(out) + "\n"


def scan_images(root: Path) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and path.stat().st_size > 0:
            records.append(
                {
                    "path": str(path),
                    "relative_path": path.relative_to(root).as_posix(),
                    "bytes": path.stat().st_size,
                    "format": path.suffix.lower().lstrip("."),
                }
            )
    return records


def page_text_blocks(page: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    raw = page.get_text("dict")
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        lines: list[str] = []
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", []))
            if text.strip():
                lines.append(text)
        if lines:
            blocks.append({"bbox": block.get("bbox"), "text": "\n".join(lines)})
    return blocks


def extract_pymupdf(input_pdf: Path, output_dir: Path, pages: list[int], *, dpi: int) -> dict[str, Any]:
    import fitz  # type: ignore[import-not-found]

    doc = fitz.open(str(input_pdf))
    pages_dir = output_dir / "pages"
    render_dir = output_dir / "page-renders"
    image_dir = output_dir / "images" / "embedded"
    tables_dir = output_dir / "tables"
    pages_dir.mkdir(parents=True, exist_ok=True)
    render_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)

    seen_xrefs: set[int] = set()
    page_records: list[dict[str, Any]] = []
    image_records: list[dict[str, Any]] = []
    table_records: list[dict[str, Any]] = []
    for page_number in pages:
        page = doc[page_number - 1]
        page_id = f"page_{page_number:04d}"
        render_path = render_dir / f"{page_id}.png"
        page.get_pixmap(dpi=dpi).save(str(render_path))

        embedded_for_page: list[dict[str, Any]] = []
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
            out = image_dir / f"{page_id}_image_{image_index:03d}_xref{xref}.{ext}"
            out.write_bytes(data)
            record = {
                "kind": "embedded_pdf_image",
                "page": page_number,
                "xref": xref,
                "path": str(out),
                "relative_path": out.relative_to(output_dir).as_posix(),
                "bytes": out.stat().st_size,
                "format": ext,
            }
            embedded_for_page.append(record)
            image_records.append(record)

        tables_for_page: list[dict[str, Any]] = []
        try:
            tables = list(page.find_tables())
        except Exception as exc:
            tables = []
            table_error = str(exc)
        else:
            table_error = ""
        for table_index, table in enumerate(tables, start=1):
            table_id = f"{page_id}_table_{table_index:02d}"
            table_dir = tables_dir / table_id
            table_dir.mkdir(parents=True, exist_ok=True)
            try:
                rows = table.extract()
            except Exception:
                rows = []
            payload = {
                "table_id": table_id,
                "engine": "pymupdf",
                "page": page_number,
                "bbox": list(getattr(table, "bbox", []) or []),
                "rows": rows,
            }
            json_path = write_json(table_dir / "pymupdf.json", payload)
            md_path = write_text(table_dir / "table.md", markdown_table(rows))
            html_path = write_text(table_dir / "table.html", html_table(rows))
            record = {
                "table_id": table_id,
                "page": page_number,
                "bbox": payload["bbox"],
                "rows": len(rows),
                "columns": max((len(row) for row in rows), default=0),
                "json_path": str(json_path),
                "markdown_path": str(md_path),
                "html_path": str(html_path),
            }
            tables_for_page.append(record)
            table_records.append(record)

        page_payload = {
            "page": page_number,
            "width": page.rect.width,
            "height": page.rect.height,
            "rotation": page.rotation,
            "text": page.get_text("text"),
            "text_blocks": page_text_blocks(page),
            "page_render_path": str(render_path),
            "page_render_relative_path": render_path.relative_to(output_dir).as_posix(),
            "embedded_images": embedded_for_page,
            "tables": tables_for_page,
            "table_error": table_error,
        }
        write_json(pages_dir / f"{page_id}.json", page_payload)
        page_records.append(page_payload)
    doc.close()
    return {
        "pages": page_records,
        "images": image_records,
        "tables": table_records,
        "page_render_count": len(page_records),
    }


def run_pymupdf4llm(input_pdf: Path, output_dir: Path, pages: list[int]) -> dict[str, Any]:
    import pymupdf4llm  # type: ignore[import-not-found]

    image_dir = output_dir / "images" / "pymupdf4llm"
    image_dir.mkdir(parents=True, exist_ok=True)
    kwargs = {
        "pages": [page - 1 for page in pages],
        "write_images": True,
        "image_path": str(image_dir),
        "embed_images": False,
        "image_size_limit": 0,
        "page_separators": True,
    }
    markdown = pymupdf4llm.to_markdown(str(input_pdf), **kwargs)
    output_path = write_text(output_dir / "text" / "pymupdf4llm.md", str(markdown))
    return {"status": "ok", "markdown_path": str(output_path), "images": scan_images(image_dir)}


def run_markitdown(input_pdf: Path, output_dir: Path) -> dict[str, Any]:
    from markitdown import MarkItDown  # type: ignore[import-not-found]

    converter = MarkItDown()
    result = converter.convert(str(input_pdf))
    text = getattr(result, "markdown", None) or getattr(result, "text_content", None) or str(result)
    output_path = write_text(output_dir / "text" / "markitdown.md", text)
    return {"status": "ok", "markdown_path": str(output_path)}


def run_docling(input_pdf: Path, output_dir: Path) -> dict[str, Any]:
    raw_dir = output_dir / "engines" / "docling"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cli = shutil.which("docling") or "/opt/venv/bin/docling"
    if Path(cli).is_file():
        attempts = [
            [cli, str(input_pdf), "--from", "pdf", "--to", "json", "--image-export-mode", "referenced", "--no-ocr", "--output", str(raw_dir)],
            [cli, str(input_pdf), "--from", "pdf", "--format", "json", "--image-export-mode", "referenced", "--no-ocr", "--output", str(raw_dir)],
        ]
        for cmd in attempts:
            result = subprocess.run(cmd, text=True, capture_output=True, check=False)
            (raw_dir / "last-cli.stderr.txt").write_text(result.stderr or "", encoding="utf-8")
            if result.returncode == 0:
                json_files = sorted(raw_dir.rglob("*.json"))
                if json_files:
                    output_path = output_dir / "engines" / "docling.json"
                    shutil.copyfile(json_files[0], output_path)
                    return {"status": "ok", "json_path": str(output_path), "images": scan_images(raw_dir)}

    from docling.document_converter import DocumentConverter  # type: ignore[import-not-found]

    converter = DocumentConverter()
    result = converter.convert(input_pdf)
    document = result.document
    payload = document.export_to_dict()
    json_path = write_json(output_dir / "engines" / "docling.json", payload)
    try:
        markdown = document.export_to_markdown()
    except Exception:
        markdown = ""
    markdown_path = write_text(output_dir / "text" / "docling.md", markdown)
    return {"status": "ok", "json_path": str(json_path), "markdown_path": str(markdown_path), "images": scan_images(raw_dir)}


def run_opendataloader(input_pdf: Path, output_dir: Path) -> dict[str, Any]:
    raw_dir = output_dir / "engines" / "opendataloader"
    image_dir = raw_dir / "images"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cli = shutil.which("opendataloader-pdf") or "/opt/venv/bin/opendataloader-pdf"
    if not Path(cli).is_file():
        raise BundleError("opendataloader-pdf executable is not available in the container")
    cmd = [
        cli,
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
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    (raw_dir / "opendataloader.stderr.txt").write_text(result.stderr or "", encoding="utf-8")
    if result.returncode != 0:
        raise BundleError(f"OpenDataLoader failed: {(result.stderr or '')[-2000:]}")
    json_files = sorted(raw_dir.rglob("*.json"))
    if not json_files:
        raise BundleError("OpenDataLoader produced no JSON")
    output_path = output_dir / "engines" / "opendataloader.json"
    shutil.copyfile(json_files[0], output_path)
    return {"status": "ok", "json_path": str(output_path), "images": scan_images(raw_dir)}


def build_payload(output_dir: Path, manifest: dict[str, Any]) -> Path:
    lines = [
        "# Canonical PDF LLM Bundle",
        "",
        f"Source: `{manifest['input_pdf']}`",
        f"Pages: `{', '.join(str(p) for p in manifest['pages'])}`",
        "",
        "## Engine Roles",
        "",
        "- Docling/OpenDataLoader JSON are preferred for structured table interpretation when available.",
        "- PyMuPDF page renders are the visual ground truth and should be attached for table/chart/layout pages.",
        "- PyMuPDF table detection supplies page geometry and normalized table Markdown/HTML.",
        "- PyMuPDF4LLM Markdown is the readable linear fallback.",
        "",
    ]
    for page in manifest["pymupdf"]["pages"]:
        lines.extend(
            [
                f"## Page {page['page']}",
                "",
                f"Page image: `{page['page_render_relative_path']}`",
                "",
                "### Text",
                "",
                page.get("text", "").strip(),
                "",
            ]
        )
        if page.get("tables"):
            lines.extend(["### Tables", ""])
            for table in page["tables"]:
                lines.append(f"#### {table['table_id']}")
                lines.append("")
                md_path = Path(table["markdown_path"])
                try:
                    lines.append(md_path.read_text(encoding="utf-8").strip())
                except Exception:
                    lines.append("")
                lines.append("")
                lines.append(f"HTML: `{Path(table['html_path']).relative_to(output_dir).as_posix()}`")
                lines.append("")
        if page.get("embedded_images"):
            lines.extend(["### Extracted Images", ""])
            for image in page["embedded_images"]:
                lines.append(f"- `{image['relative_path']}` ({image['bytes']} bytes)")
            lines.append("")
    engine_results = manifest.get("engine_results", {})
    for engine in ["docling", "opendataloader", "pymupdf4llm", "markitdown"]:
        result = engine_results.get(engine) or {}
        if result.get("status") == "ok":
            lines.append(f"## {engine} Output")
            lines.append("")
            for key in ["json_path", "markdown_path"]:
                if result.get(key):
                    path = Path(str(result[key]))
                    try:
                        rel = path.relative_to(output_dir).as_posix()
                    except ValueError:
                        rel = str(path)
                    lines.append(f"- `{key}`: `{rel}`")
            lines.append("")
    return write_text(output_dir / "canonical_payload.md", "\n".join(lines).strip() + "\n")


def safe_engine_call(name: str, func: Any, *args: Any) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = func(*args)
        result["seconds"] = round(time.perf_counter() - started, 4)
        return result
    except Exception as exc:
        return {
            "status": "failed",
            "seconds": round(time.perf_counter() - started, 4),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "engine": name,
        }


def run_bundle(input_pdf: Path, output_dir: Path, *, pages_spec: str | None, render_dpi: int) -> dict[str, Any]:
    import fitz  # type: ignore[import-not-found]

    output_dir.mkdir(parents=True, exist_ok=True)
    page_count = fitz.open(str(input_pdf)).page_count
    pages = parse_pages(pages_spec, page_count)
    pymupdf_payload = extract_pymupdf(input_pdf, output_dir, pages, dpi=render_dpi)
    engine_results = {
        "pymupdf4llm": safe_engine_call("pymupdf4llm", run_pymupdf4llm, input_pdf, output_dir, pages),
        "docling": safe_engine_call("docling", run_docling, input_pdf, output_dir),
        "opendataloader": safe_engine_call("opendataloader", run_opendataloader, input_pdf, output_dir),
        "markitdown": safe_engine_call("markitdown", run_markitdown, input_pdf, output_dir),
    }
    image_manifest = scan_images(output_dir)
    manifest = {
        "version": 1,
        "input_pdf": str(input_pdf),
        "source_page_count": page_count,
        "pages": pages,
        "render_dpi": render_dpi,
        "pymupdf": pymupdf_payload,
        "engine_results": engine_results,
        "image_manifest": image_manifest,
        "quality_flags": {
            "has_page_renders": pymupdf_payload["page_render_count"] == len(pages),
            "has_detected_tables": bool(pymupdf_payload["tables"]),
            "has_docling_json": engine_results["docling"].get("status") == "ok" and bool(engine_results["docling"].get("json_path")),
            "has_opendataloader_json": engine_results["opendataloader"].get("status") == "ok" and bool(engine_results["opendataloader"].get("json_path")),
            "requires_visual_context": bool(pymupdf_payload["tables"]) or len(image_manifest) > len(pages),
        },
    }
    payload_path = build_payload(output_dir, manifest)
    manifest["canonical_payload_path"] = str(payload_path)
    write_json(output_dir / "manifest.json", manifest)
    write_json(output_dir / "image_manifest.json", image_manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a canonical PDF LLM bundle inside a container.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--pages", default=None)
    parser.add_argument("--render-dpi", type=int, default=160)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = run_bundle(Path(args.input), Path(args.output_dir), pages_spec=args.pages, render_dpi=args.render_dpi)
        print(json.dumps({"status": "ok", "manifest_path": str(Path(args.output_dir) / "manifest.json"), "quality_flags": manifest["quality_flags"]}))
        return 0
    except Exception as exc:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        write_json(
            output_dir / "manifest.json",
            {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
