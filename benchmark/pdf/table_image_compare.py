#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = REPO_ROOT / "benchmark" / "pdf"
ENGINE_RUNNER = BENCHMARK_DIR / "table_image_engine_runner.py"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "tmp" / "benchmark" / "pdf-table-images"
DEFAULT_WINDOWS_PAD = r"C:\Users\wb620566\Downloads\P162302 PAD.pdf"
DEFAULT_DPF_ARTIFACT_ROOTS = [
    Path.home() / "DRM_Policy_Platform" / "artifacts" / "dpf_markdown_failure_deck",
    Path("/home/santi/DRM_Policy_Platform/artifacts/dpf_markdown_failure_deck"),
]
DEFAULT_DPF_DISCOVERY_ROOTS = [
    Path.home() / "DRM_Policy_Platform" / "Output" / "lakehouse_artifacts" / "databricks_imagebank",
    Path.home() / "DRM_Policy_Platform" / "Output" / "lakehouse_artifacts" / "pilot-rerun-20260513T185353Z" / "databricks_imagebank",
    Path("/home/santi/DRM_Policy_Platform/Output/lakehouse_artifacts/databricks_imagebank"),
]
DEFAULT_IMF_DOWNLOAD_ROOTS = [
    Path.home() / "imf-article-ivs" / "_downloads",
    Path("/home/santi/imf-article-ivs/_downloads"),
]


@dataclass(frozen=True)
class EngineSpec:
    name: str
    package_args: tuple[str, ...]
    preferred_format: str
    category: str
    description: str
    image_note: str
    default: bool = True


ENGINE_SPECS: dict[str, EngineSpec] = {
    "markitdown": EngineSpec(
        "markitdown",
        ("markitdown[pdf]", "pymupdf"),
        "markdown",
        "core",
        "MarkItDown PDF-to-Markdown plus PyMuPDF embedded-image sidecar because this workflow was locally judged viable.",
        "Markdown is native; image assets are extracted from the same PDF as a sidecar.",
    ),
    "pymupdf4llm": EngineSpec(
        "pymupdf4llm",
        ("pymupdf4llm",),
        "json",
        "core",
        "PyMuPDF4LLM structured JSON with write_images enabled, falling back to Markdown if JSON export changes.",
        "Uses pymupdf4llm image writing.",
    ),
    "docling": EngineSpec(
        "docling",
        ("docling",),
        "json",
        "core",
        "Docling JSON export with referenced images and OCR disabled.",
        "Uses Docling referenced image export.",
    ),
    "opendataloader": EngineSpec(
        "opendataloader",
        ("opendataloader-pdf",),
        "json",
        "core",
        "OpenDataLoader JSON with external PNG image output.",
        "Uses OpenDataLoader external image output.",
    ),
    "pdf_oxide": EngineSpec(
        "pdf_oxide",
        ("pdf_oxide",),
        "markdown",
        "core",
        "PDF Oxide Markdown with external image output from its Python API.",
        "Uses PDF Oxide image_output_dir.",
    ),
    "pymupdf": EngineSpec(
        "pymupdf",
        ("pymupdf",),
        "json",
        "component",
        "Low-level PyMuPDF JSON with detected tables, embedded images, and page renders.",
        "Uses PyMuPDF embedded-image extraction plus page renders.",
    ),
    "kreuzberg": EngineSpec(
        "kreuzberg",
        ("kreuzberg",),
        "json",
        "core",
        "Kreuzberg JSON payload containing HTML content plus extracted images via its Python API.",
        "Uses Kreuzberg ImageExtractionConfig through the Python API.",
    ),
    "nutrient_pdf_to_markdown": EngineSpec(
        "nutrient_pdf_to_markdown",
        (),
        "markdown",
        "optional",
        "Nutrient/PSPDFKit pdf-to-markdown binary with --enable-image-export.",
        "Requires a separately installed local Nutrient binary.",
        default=False,
    ),
    "unstructured": EngineSpec(
        "unstructured",
        ("unstructured[local-inference]",),
        "json",
        "heavy",
        "Unstructured hi_res partition with table HTML plus image/table block extraction.",
        "Heavy optional path; may require model/runtime assets.",
        default=False,
    ),
}


SKIPPED_ENGINES: dict[str, str] = {
    "pdfminer_six": "Text-only; no native structured table plus image export path.",
    "pypdf": "Text extraction baseline; no faithful complex table reconstruction or image export lane here.",
    "pypdfium2": "Can render/extract images but is not a table-structure converter in this benchmark.",
    "poppler_pdftotext": "Text-only.",
    "camelot": "Table-only; does not export surrounding document images.",
    "tabula_java": "Table-only JSON; does not export surrounding document images.",
    "pdfplumber": "Useful low-level table inspection, but no reliable standalone image export lane for this harness.",
    "liteparse": "JSON path exists, but local screenshot/image export is not exposed reliably in the Python package used here.",
    "markit_ai": "Markdown baseline without a documented image export lane.",
    "edgeparse": "HTML/JSON conversion exists, but the installed CLI exposes no image-export option.",
}


class TableImageCompareError(RuntimeError):
    pass


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_run_id(now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    return current.strftime("%Y%m%d-%H%M%S")


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_page_ranges(spec: str | None) -> list[int] | None:
    if spec is None:
        return None
    cleaned = spec.strip()
    if not cleaned or cleaned.lower() in {"all", "*"}:
        return None
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1]
    cleaned = cleaned.replace("\u2013", "-").replace("\u2014", "-").replace(" ", "")
    pages: list[int] = []
    seen: set[int] = set()
    for raw_part in cleaned.split(","):
        if not raw_part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", raw_part)
        if not match:
            raise ValueError(f"Invalid page range token `{raw_part}` in `{spec}`")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < 1:
            raise ValueError("Page numbers are 1-based and must be positive")
        if end < start:
            raise ValueError(f"Page range `{raw_part}` ends before it starts")
        for page in range(start, end + 1):
            if page not in seen:
                pages.append(page)
                seen.add(page)
    return pages or None


def pdf_page_count(path: Path) -> int:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise TableImageCompareError("Missing dependency `pypdf`; run through `uv run`.") from exc
    with path.open("rb") as handle:
        return len(PdfReader(handle).pages)


def validate_pages(pages: list[int] | None, *, page_count: int) -> list[int] | None:
    if pages is None:
        return None
    out_of_range = [page for page in pages if page < 1 or page > page_count]
    if out_of_range:
        raise ValueError(
            f"Requested pages outside PDF page range 1-{page_count}: "
            + ", ".join(str(page) for page in out_of_range)
        )
    return pages


def materialize_input_pdf(source_pdf: Path, pages: list[int] | None, output_pdf: Path) -> None:
    ensure_directory(output_pdf.parent)
    if pages is None:
        shutil.copy2(source_pdf, output_pdf)
        return
    try:
        from pypdf import PdfReader, PdfWriter
    except ModuleNotFoundError as exc:
        raise TableImageCompareError("Missing dependency `pypdf`; run through `uv run`.") from exc
    with source_pdf.open("rb") as handle:
        reader = PdfReader(handle)
        writer = PdfWriter()
        for page in pages:
            writer.add_page(reader.pages[page - 1])
        with output_pdf.open("wb") as out_handle:
            writer.write(out_handle)


def _shell_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def quote_command(cmd: list[str]) -> str:
    return subprocess.list2cmdline(cmd) if os.name == "nt" else " ".join(_shell_quote(part) for part in cmd)


def sanitize_doc_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return cleaned[:80] or "pdf"


def unique_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[Path] = set()
    used_ids: set[str] = set()
    unique: list[dict[str, Any]] = []
    for doc in documents:
        path = Path(str(doc["path"])).expanduser().resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        base_id = sanitize_doc_id(str(doc.get("id") or path.stem))
        doc_id = base_id
        suffix = 2
        while doc_id in used_ids:
            doc_id = f"{base_id}_{suffix}"
            suffix += 1
        used_ids.add(doc_id)
        normalized = dict(doc)
        normalized["id"] = doc_id
        normalized["path"] = str(path)
        unique.append(normalized)
    return unique


def resolve_windows_path(value: str) -> tuple[Path, str]:
    if re.match(r"^[A-Za-z]:\\", value):
        if shutil.which("wslpath"):
            result = subprocess.run(["wslpath", value], text=True, capture_output=True, check=False)
            if result.returncode == 0 and result.stdout.strip():
                return Path(result.stdout.strip()).expanduser().resolve(), "wslpath"
        drive = value[0].lower()
        rest = value[2:].replace("\\", "/").lstrip("/")
        return Path(f"/mnt/{drive}/{rest}").resolve(), "manual_wsl"
    return Path(value).expanduser().resolve(), "posix"


def find_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.is_dir() or path.is_file():
            return path.resolve()
    return None


def discover_dpf_deck_documents(artifact_roots: list[Path]) -> list[dict[str, Any]]:
    root = find_existing(artifact_roots)
    if root is None:
        return []
    assets = root / "assets"
    docs: list[dict[str, Any]] = []
    candidates = [
        ("ecuador_dpf2_page_058", "Ecuador DPF2 page 58 extract", assets / "ecuador_dpf2_page_058_only.pdf"),
        ("bhutan_dpc3_pages_051_052", "Bhutan DPC3 pages 51-52 extract", assets / "bhutan_dpc3_pages_051_052.pdf"),
    ]
    for doc_id, label, path in candidates:
        if path.is_file():
            docs.append({"id": doc_id, "label": label, "path": str(path), "source": "dpf_markdown_failure_deck"})
    return docs


def discover_dpf_full_documents(discovery_roots: list[Path], *, max_docs: int) -> list[dict[str, Any]]:
    patterns = ("development policy", "dpf", "dpc", "dpo", "dpl")
    docs: list[dict[str, Any]] = []
    for root in discovery_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.pdf")):
            lowered = path.name.lower()
            if not any(pattern in lowered for pattern in patterns):
                continue
            docs.append(
                {
                    "id": sanitize_doc_id(path.stem),
                    "label": path.stem,
                    "path": str(path.resolve()),
                    "source": "dpf_discovery",
                }
            )
            if len(docs) >= max_docs:
                return unique_documents(docs)
    return unique_documents(docs)


def choose_imf_article_iv(download_roots: list[Path]) -> dict[str, Any] | None:
    priority_iso3 = ["ETH", "BGD", "KEN", "GHA", "ZMB", "MOZ", "PAK", "NGA", "TZA", "RWA"]
    pdfs: list[Path] = []
    for root in download_roots:
        if root.is_dir():
            pdfs.extend(sorted(root.rglob("*-en.pdf")))
    if not pdfs:
        return None
    by_iso = {iso3: [path for path in pdfs if path.name.startswith(f"{iso3}-")] for iso3 in priority_iso3}
    candidates: list[Path] = []
    for iso3 in priority_iso3:
        candidates.extend(by_iso[iso3])
        if candidates:
            break
    if not candidates:
        candidates = pdfs

    def score(path: Path) -> tuple[int, int, str]:
        try:
            pages = pdf_page_count(path)
        except Exception:
            pages = 0
        year_match = re.search(r"\.(20\d{2})\.", path.name)
        year = int(year_match.group(1)) if year_match else 0
        return pages, year, path.name

    selected = max(candidates, key=score)
    return {
        "id": sanitize_doc_id(f"imf_article_iv_{selected.stem}"),
        "label": f"IMF Article IV selected: {selected.name}",
        "path": str(selected.resolve()),
        "source": "imf_article_iv_downloads",
        "selection_rule": "first priority ISO3 with largest page count, then newest filename year",
    }


def resolve_default_corpus(
    *,
    dpf_artifact_roots: list[Path],
    dpf_discovery_roots: list[Path],
    imf_download_roots: list[Path],
    windows_pad: str,
    max_dpf_docs: int,
) -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    documents.extend(discover_dpf_deck_documents(dpf_artifact_roots))
    documents.extend(discover_dpf_full_documents(dpf_discovery_roots, max_docs=max_dpf_docs))

    pad_path, pad_strategy = resolve_windows_path(windows_pad)
    if pad_path.is_file():
        documents.append(
            {
                "id": "p162302_pad",
                "label": "P162302 Program Appraisal Document PAD",
                "path": str(pad_path),
                "source": "windows_path",
                "path_resolution": pad_strategy,
            }
        )
    else:
        missing.append({"label": "P162302 PAD", "path": str(pad_path), "source": windows_pad})

    imf_doc = choose_imf_article_iv(imf_download_roots)
    if imf_doc:
        documents.append(imf_doc)
    else:
        missing.append({"label": "IMF Article IV", "path": ",".join(str(p) for p in imf_download_roots), "source": "imf_article_iv_downloads"})

    resolved_docs = unique_documents(documents)
    for doc in resolved_docs:
        path = Path(str(doc["path"]))
        try:
            doc["page_count"] = pdf_page_count(path)
            doc["sha256"] = sha256_file(path)
        except Exception as exc:
            doc["metadata_error"] = str(exc)
    return {
        "version": 1,
        "generated_at": utc_now_iso(),
        "documents": resolved_docs,
        "missing": missing,
        "skipped_engines": SKIPPED_ENGINES,
    }


def expand_engine_token(token: str) -> list[str]:
    if token == "default":
        return [name for name, spec in ENGINE_SPECS.items() if spec.default]
    if token == "all":
        return list(ENGINE_SPECS)
    if token == "heavy":
        return [name for name, spec in ENGINE_SPECS.items() if spec.category == "heavy"]
    if token == "optional":
        return [name for name, spec in ENGINE_SPECS.items() if not spec.default]
    if token in ENGINE_SPECS:
        return [token]
    valid = sorted(set(ENGINE_SPECS) | {"default", "all", "heavy", "optional"})
    raise ValueError(f"Unknown engine or group `{token}`. Valid values: {', '.join(valid)}")


def resolve_engines(raw: str | None) -> list[str]:
    spec = raw or "default"
    selected: list[str] = []
    seen: set[str] = set()
    for token in [part.strip() for part in spec.split(",") if part.strip()]:
        for engine in expand_engine_token(token):
            if engine not in seen:
                selected.append(engine)
                seen.add(engine)
    if not selected:
        raise ValueError("At least one engine must be selected")
    return selected


def build_engine_command(*, engine: str, input_pdf: Path, output_dir: Path) -> list[str]:
    spec = ENGINE_SPECS[engine]
    cmd = ["uv", "run"]
    for package in spec.package_args:
        cmd.extend(["--with", package])
    cmd.extend(
        [
            "python",
            str(ENGINE_RUNNER),
            "--engine",
            engine,
            "--input",
            str(input_pdf),
            "--output-dir",
            str(output_dir),
        ]
    )
    return cmd


def load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    ensure_directory(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    ensure_directory(path.parent)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_corpus(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        docs = payload.get("documents", [])
    else:
        docs = payload
    if not isinstance(docs, list):
        raise ValueError("Corpus JSON must contain a documents list")
    return unique_documents([doc for doc in docs if isinstance(doc, dict) and doc.get("path")])


def documents_from_pdf_args(values: list[str]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for raw in values:
        if "=" in raw:
            doc_id, raw_path = raw.split("=", 1)
            path = Path(raw_path).expanduser().resolve()
            label = doc_id
        else:
            path = Path(raw).expanduser().resolve()
            doc_id = path.stem
            label = path.stem
        if not path.is_file():
            raise FileNotFoundError(f"PDF not found: {path}")
        docs.append({"id": sanitize_doc_id(doc_id), "label": label, "path": str(path), "source": "cli"})
    return unique_documents(docs)


def run_one_conversion(
    *,
    engine: str,
    doc: dict[str, Any],
    input_pdf: Path,
    output_dir: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / "stdout.txt"
    stderr_path = output_dir / "stderr.txt"
    result_json_path = output_dir / "result.json"
    cmd = build_engine_command(engine=engine, input_pdf=input_pdf, output_dir=output_dir)
    started = time.perf_counter()
    try:
        with stdout_path.open("w", encoding="utf-8") as out_handle, stderr_path.open("w", encoding="utf-8") as err_handle:
            completed = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                text=True,
                stdout=out_handle,
                stderr=err_handle,
                timeout=timeout_seconds,
                check=False,
            )
        timed_out = False
        returncode: int | str = completed.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = ""
        with stderr_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\nTimed out after {timeout_seconds} seconds.\n")
    wall_seconds = time.perf_counter() - started
    payload = load_json_if_exists(result_json_path) or {}
    if timed_out:
        payload = {
            "engine": engine,
            "status": "timeout",
            "error": f"Timed out after {timeout_seconds} seconds",
        }
    elif not payload:
        payload = {
            "engine": engine,
            "status": "failed",
            "error": "Engine runner did not write result.json",
        }

    primary_output = str(payload.get("primary_output_path", ""))
    image_count = int(payload.get("image_count") or 0)
    status = "ok" if payload.get("status") == "ok" and primary_output and Path(primary_output).is_file() else str(payload.get("status", "failed"))
    spec = ENGINE_SPECS[engine]
    page_count = int(doc.get("selected_page_count") or doc.get("page_count") or 0)
    seconds = float(payload.get("seconds_inside_process") or wall_seconds)
    return {
        "document_id": doc["id"],
        "document_label": doc.get("label", doc["id"]),
        "engine": engine,
        "category": spec.category,
        "status": status,
        "returncode": returncode,
        "seconds": round(seconds, 4),
        "seconds_per_page": round(seconds / page_count, 6) if page_count else "",
        "pages": page_count,
        "preferred_format": spec.preferred_format,
        "output_format": payload.get("primary_output_format", ""),
        "output_path": primary_output,
        "output_bytes": payload.get("primary_output_bytes", 0),
        "image_count": image_count,
        "image_bytes": payload.get("image_bytes", 0),
        "image_manifest_path": payload.get("image_manifest_path", ""),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "result_json_path": str(result_json_path) if result_json_path.is_file() else "",
        "command": quote_command(cmd),
        "error": str(payload.get("error", "")),
    }


def build_summary(*, run_dir: Path, rows: list[dict[str, Any]], manifest: dict[str, Any]) -> str:
    lines = [
        "# PDF Table/Image Comparator Run",
        "",
        f"Run directory: `{run_dir}`",
        f"Generated: `{utc_now_iso()}`",
        f"Pages: `{manifest['pages_spec'] or 'all'}`",
        f"Engines: `{', '.join(manifest['engines'])}`",
        "",
        "## Results",
        "",
        "| Document | Engine | Status | Format | Images | Seconds/page | Output | Image manifest |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| `{row['document_id']}` | `{row['engine']}` | `{row['status']}` | "
            f"`{row.get('output_format', '')}` | {row.get('image_count', 0)} | "
            f"{row.get('seconds_per_page', '')} | `{row.get('output_path', '')}` | "
            f"`{row.get('image_manifest_path', '')}` |"
        )
    lines.extend(
        [
            "",
            "## Skipped Engines",
            "",
        ]
    )
    for engine, reason in sorted(SKIPPED_ENGINES.items()):
        lines.append(f"- `{engine}`: {reason}")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This benchmark prefers JSON, then HTML, then Markdown, but records the actual primary artifact each engine produced.",
            "- Image counts are from each engine output directory. For MarkItDown, image files are a PyMuPDF sidecar because the user explicitly asked to keep it in this comparison after colleague validation.",
            "- Visual quality assessment is intentionally separate from these mechanical run artifacts.",
        ]
    )
    return "\n".join(lines) + "\n"


def command_list_engines(_args: argparse.Namespace) -> int:
    print("Available engines/groups:")
    print("  default: " + ", ".join(resolve_engines("default")))
    print("  all: " + ", ".join(resolve_engines("all")))
    print("  optional: " + ", ".join(resolve_engines("optional")))
    print("\nEngines:")
    for name, spec in ENGINE_SPECS.items():
        default = "default" if spec.default else "optional"
        print(f"  {name:26} {spec.category:10} {spec.preferred_format:9} {default:9} {spec.description}")
    print("\nSkipped from this benchmark:")
    for name, reason in sorted(SKIPPED_ENGINES.items()):
        print(f"  {name:26} {reason}")
    return 0


def command_resolve_corpus(args: argparse.Namespace) -> int:
    payload = resolve_default_corpus(
        dpf_artifact_roots=[Path(p).expanduser() for p in args.dpf_artifact_root],
        dpf_discovery_roots=[Path(p).expanduser() for p in args.dpf_discovery_root],
        imf_download_roots=[Path(p).expanduser() for p in args.imf_download_root],
        windows_pad=args.windows_pad,
        max_dpf_docs=args.max_dpf_docs,
    )
    if args.output:
        output = Path(args.output).expanduser().resolve()
        write_json(output, payload)
        print(output)
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def command_run(args: argparse.Namespace) -> int:
    artifact_root = Path(args.artifact_root).expanduser().resolve()
    run_id = args.run_id or build_run_id()
    run_dir = ensure_directory(artifact_root / run_id)
    if args.corpus_json:
        documents = load_corpus(Path(args.corpus_json).expanduser().resolve())
        corpus_payload = {"version": 1, "documents": documents, "source": str(args.corpus_json)}
    elif args.pdf:
        documents = documents_from_pdf_args(args.pdf)
        corpus_payload = {"version": 1, "documents": documents, "source": "cli"}
    else:
        corpus_payload = resolve_default_corpus(
            dpf_artifact_roots=[Path(p).expanduser() for p in args.dpf_artifact_root],
            dpf_discovery_roots=[Path(p).expanduser() for p in args.dpf_discovery_root],
            imf_download_roots=[Path(p).expanduser() for p in args.imf_download_root],
            windows_pad=args.windows_pad,
            max_dpf_docs=args.max_dpf_docs,
        )
        documents = corpus_payload["documents"]
    if not documents:
        raise TableImageCompareError("No PDFs resolved for the table/image benchmark corpus")

    engines = resolve_engines(args.engines)
    pages = parse_page_ranges(args.pages)
    inputs_dir = ensure_directory(run_dir / "inputs")
    conversions_dir = ensure_directory(run_dir / "conversions")
    rows: list[dict[str, Any]] = []
    materialized_docs: list[dict[str, Any]] = []
    for doc in documents:
        source_pdf = Path(str(doc["path"])).expanduser().resolve()
        if not source_pdf.is_file():
            raise FileNotFoundError(f"PDF not found: {source_pdf}")
        original_page_count = pdf_page_count(source_pdf)
        selected_pages = validate_pages(pages, page_count=original_page_count)
        selected_page_count = len(selected_pages) if selected_pages is not None else original_page_count
        input_pdf = inputs_dir / f"{doc['id']}.pdf"
        materialize_input_pdf(source_pdf, selected_pages, input_pdf)
        materialized = dict(doc)
        materialized.update(
            {
                "path": str(source_pdf),
                "source_page_count": original_page_count,
                "selected_pages": selected_pages or list(range(1, original_page_count + 1)),
                "selected_page_count": selected_page_count,
                "input_pdf": str(input_pdf),
                "input_sha256": sha256_file(input_pdf),
            }
        )
        materialized_docs.append(materialized)
        for engine in engines:
            print(f"[{doc['id']}] {engine}", file=sys.stderr)
            row = run_one_conversion(
                engine=engine,
                doc=materialized,
                input_pdf=input_pdf,
                output_dir=conversions_dir / str(doc["id"]) / engine,
                timeout_seconds=args.timeout_seconds,
            )
            rows.append(row)
            print(
                f"[{row['status']}] {doc['id']} {engine}: "
                f"{row.get('output_path') or row.get('error')} images={row.get('image_count', 0)}",
                file=sys.stderr,
            )

    fieldnames = [
        "document_id",
        "document_label",
        "engine",
        "category",
        "status",
        "returncode",
        "seconds",
        "seconds_per_page",
        "pages",
        "preferred_format",
        "output_format",
        "output_path",
        "output_bytes",
        "image_count",
        "image_bytes",
        "image_manifest_path",
        "stdout_path",
        "stderr_path",
        "result_json_path",
        "command",
        "error",
    ]
    manifest = {
        "version": 1,
        "generated_at": utc_now_iso(),
        "run_id": run_id,
        "run_dir": str(run_dir),
        "pages_spec": args.pages,
        "engines": engines,
        "engine_specs": {engine: ENGINE_SPECS[engine].__dict__ for engine in engines},
        "skipped_engines": SKIPPED_ENGINES,
        "documents": materialized_docs,
        "corpus": corpus_payload,
        "results": rows,
    }
    write_json(run_dir / "corpus-resolved.json", corpus_payload)
    write_json(run_dir / "manifest.json", manifest)
    write_csv(run_dir / "timings.csv", rows, fieldnames)
    (run_dir / "summary.md").write_text(build_summary(run_dir=run_dir, rows=rows, manifest=manifest), encoding="utf-8")
    print(run_dir)
    return 0


def add_corpus_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dpf-artifact-root",
        action="append",
        default=[str(path) for path in DEFAULT_DPF_ARTIFACT_ROOTS],
        help="dpf_markdown_failure_deck root. May be repeated.",
    )
    parser.add_argument(
        "--dpf-discovery-root",
        action="append",
        default=[str(path) for path in DEFAULT_DPF_DISCOVERY_ROOTS],
        help="Root to search for full DPF/DPC/DPO PDFs. May be repeated.",
    )
    parser.add_argument(
        "--imf-download-root",
        action="append",
        default=[str(path) for path in DEFAULT_IMF_DOWNLOAD_ROOTS],
        help="Root containing IMF Article IV PDF downloads. May be repeated.",
    )
    parser.add_argument("--windows-pad", default=DEFAULT_WINDOWS_PAD)
    parser.add_argument("--max-dpf-docs", type=int, default=20)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark PDF table fidelity plus image export for LLM ingestion.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-engines", help="List table/image benchmark engines.")
    list_parser.set_defaults(func=command_list_engines)

    corpus_parser = subparsers.add_parser("resolve-corpus", help="Resolve the default DPF + PAD + Article IV corpus.")
    add_corpus_args(corpus_parser)
    corpus_parser.add_argument("--output", default=None)
    corpus_parser.set_defaults(func=command_resolve_corpus)

    run_parser = subparsers.add_parser("run", help="Run table/image conversions.")
    add_corpus_args(run_parser)
    run_parser.add_argument("--pdf", action="append", default=[], metavar="[ID=]PATH", help="PDF to benchmark. May be repeated.")
    run_parser.add_argument("--corpus-json", default=None, help="Resolved corpus JSON from resolve-corpus.")
    run_parser.add_argument("--engines", default="default", help="Comma-separated engines/groups. Default group: default.")
    run_parser.add_argument("--pages", default=None, help="Optional 1-based pages/ranges applied to every PDF.")
    run_parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    run_parser.add_argument("--run-id", default=None)
    run_parser.add_argument("--timeout-seconds", type=int, default=1800)
    run_parser.set_defaults(func=command_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (TableImageCompareError, FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
