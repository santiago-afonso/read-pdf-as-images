#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import textwrap
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

import yaml
from pypdf import PdfReader, PdfWriter


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "benchmark" / "pdf" / "corpus.yaml"
DEFAULT_RUBRIC = REPO_ROOT / "benchmark" / "pdf" / "rubric.md"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "tmp" / "benchmark" / "pdf"
READ_PDF = REPO_ROOT / "scripts" / "read-pdf"
READ_PDF_TEXT = REPO_ROOT / "scripts" / "read_pdf_text.py"

ENGINE_ORDER = [
    "markitdown",
    "pymupdf4llm",
    "docling",
    "kreuzberg",
    "opendataloader",
    "opendataloader_hybrid_auto",
    "markit",
    "pdf_to_markdown",
]

RUBRIC_WEIGHTS = {
    "reading_order": 0.25,
    "headings": 0.20,
    "tables": 0.30,
    "formulas": 0.10,
    "usability": 0.15,
}

LOCAL_ONLY_ENV_BLOCKLIST = [
    "ANTHROPIC_API_KEY",
    "COHERE_API_KEY",
    "DEEPSEEK_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "TOGETHER_API_KEY",
    "XAI_API_KEY",
]

PDF_TO_MARKDOWN_INSTALL_HINT = (
    "Install via: curl -fsSL "
    "https://raw.githubusercontent.com/PSPDFKit/pdf-to-markdown/main/install.sh | sh"
)


@dataclass(frozen=True)
class CandidateRecord:
    path: Path
    page_count: int
    year: int
    english_preferred: bool


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_run_id(now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    return current.strftime("%Y%m%d-%H%M%S")


def relative_display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def expected_pdf_to_markdown_binary_name() -> str | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux" and machine in {"x86_64", "amd64"}:
        return "nutrient-linux-amd64"
    if system == "linux" and machine in {"aarch64", "arm64"}:
        return "nutrient-linux-arm64"
    if system == "darwin" and machine == "arm64":
        return "nutrient-darwin-arm64"
    return None


def resolve_pdf_to_markdown_executable() -> str:
    override = os.environ.get("PDF_TO_MARKDOWN_BINARY")
    if override:
        override_path = Path(override).expanduser().resolve()
        if not override_path.is_file():
            raise FileNotFoundError(
                f"PDF_TO_MARKDOWN_BINARY does not exist: {override_path}. {PDF_TO_MARKDOWN_INSTALL_HINT}"
            )
        return str(override_path)

    binary_name = expected_pdf_to_markdown_binary_name()
    if binary_name:
        binary_path = Path.home() / ".local" / "share" / "nutrient" / "cli" / binary_name
        if binary_path.is_file():
            return str(binary_path)

    wrapper = shutil.which("pdf-to-markdown")
    if wrapper:
        return wrapper

    raise FileNotFoundError(
        "Unable to find pdf-to-markdown. "
        "Set PDF_TO_MARKDOWN_BINARY to the extracted Nutrient binary or install the wrapper. "
        f"{PDF_TO_MARKDOWN_INSTALL_HINT}"
    )


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    ensure_directory(path.parent)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    ensure_directory(path.parent)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_manifest(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Manifest must be a mapping: {path}")
    return data


def resolve_artifact_root(manifest: dict[str, Any], override: str | None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    manifest_root = (
        manifest.get("defaults", {}).get("artifact_root")
        if isinstance(manifest.get("defaults"), dict)
        else None
    )
    if manifest_root:
        return Path(str(manifest_root)).expanduser().resolve()
    return DEFAULT_ARTIFACT_ROOT


def extract_year_from_name(path: Path) -> int:
    years = [int(match) for match in re.findall(r"(?:19|20)\d{2}", path.name)]
    return max(years) if years else 0


def get_pdf_page_count(pdf_path: Path) -> int:
    with pdf_path.open("rb") as handle:
        reader = PdfReader(handle)
        return len(reader.pages)


def sanitize_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base or os.environ)
    env["CUDA_VISIBLE_DEVICES"] = ""
    env.setdefault("NO_COLOR", "1")
    for key in LOCAL_ONLY_ENV_BLOCKLIST:
        env.pop(key, None)
    return env


def quote_command(cmd: list[str]) -> str:
    return shlex.join(cmd)


def run_capture(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        cwd=cwd or REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {quote_command(cmd)}\n{result.stderr.strip()}"
        )
    return result


def run_to_files(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdout_path: Path,
    stderr_path: Path,
) -> subprocess.CompletedProcess[str]:
    ensure_directory(stdout_path.parent)
    ensure_directory(stderr_path.parent)
    with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_handle:
        return subprocess.run(
            cmd,
            cwd=cwd or REPO_ROOT,
            env=env,
            text=True,
            stdout=stdout_handle,
            stderr=stderr_handle,
            check=False,
        )


def read_nonempty_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line.rstrip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    ensure_directory(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def select_article_iv_candidate(
    candidates: list[CandidateRecord], *, english_only: bool = True
) -> CandidateRecord:
    if not candidates:
        raise ValueError("No candidate PDFs were found")

    pool = candidates
    if english_only:
        english_candidates = [candidate for candidate in candidates if candidate.english_preferred]
        if english_candidates:
            pool = english_candidates

    return sorted(
        pool,
        key=lambda candidate: (
            -candidate.page_count,
            -candidate.year,
            str(candidate.path.resolve()),
        ),
    )[0]


def resolve_fixed_document(spec: dict[str, Any]) -> dict[str, Any]:
    pdf_path = Path(str(spec["path"])).expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"Fixed benchmark PDF not found: {pdf_path}")
    return {
        "document_id": str(spec["id"]),
        "label": str(spec["label"]),
        "path": str(pdf_path),
        "page_count": get_pdf_page_count(pdf_path),
        "source": "fixed",
        "selection": {"manifest_path": str(pdf_path)},
    }


def collect_article_iv_candidates(download_root: Path) -> list[CandidateRecord]:
    candidates: list[CandidateRecord] = []
    for pdf_path in sorted(download_root.rglob("*.pdf")):
        try:
            page_count = get_pdf_page_count(pdf_path)
        except Exception:
            continue
        candidates.append(
            CandidateRecord(
                path=pdf_path.resolve(),
                page_count=page_count,
                year=extract_year_from_name(pdf_path),
                english_preferred=pdf_path.name.endswith("-en.pdf"),
            )
        )
    return candidates


def resolve_article_iv_document(spec: dict[str, Any], *, logs_dir: Path) -> dict[str, Any]:
    repo_path = Path(str(spec["repo_path"])).expanduser().resolve()
    if not repo_path.is_dir():
        raise FileNotFoundError(f"Article IV source repo not found: {repo_path}")

    commands = [
        [
            "uv",
            "run",
            "imf-elibrary",
            "crawl",
            "--from",
            str(spec["from_date"]),
            "--page-size",
            str(spec.get("crawl_page_size", 50)),
            "--max-pages",
            str(spec.get("crawl_max_pages", 6)),
        ],
        ["uv", "run", "imf-elibrary", "map-countries"],
        [
            "uv",
            "run",
            "imf-elibrary",
            "download",
            "--from",
            str(spec["from_date"]),
            "--countries-only",
            "--article-iv-only",
            "--skip-unknown-iso3",
            "--max-items",
            str(spec["candidate_count"]),
        ],
    ]
    if not bool(spec.get("countries_only", True)):
        commands[2].remove("--countries-only")
    if not bool(spec.get("article_iv_only", True)):
        commands[2].remove("--article-iv-only")
    if not bool(spec.get("skip_unknown_iso3", True)):
        commands[2].remove("--skip-unknown-iso3")

    command_logs: list[dict[str, Any]] = []
    env = sanitize_env()
    for index, cmd in enumerate(commands, start=1):
        stdout_path = logs_dir / f"article-iv-step-{index:02d}.stdout.txt"
        stderr_path = logs_dir / f"article-iv-step-{index:02d}.stderr.txt"
        result = run_to_files(
            cmd,
            cwd=repo_path,
            env=env,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        command_logs.append(
            {
                "command": cmd,
                "cwd": str(repo_path),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "returncode": result.returncode,
            }
        )
        if result.returncode != 0:
            stderr_lines = read_nonempty_lines(stderr_path)
            stderr_tail = "\n".join(stderr_lines[-8:]) if stderr_lines else "No stderr captured."
            raise RuntimeError(
                f"Failed while preparing Article IV corpus: {quote_command(cmd)} "
                f"(see {relative_display(stderr_path)})\n{stderr_tail}"
            )

    download_root = repo_path / "_downloads"
    candidates = collect_article_iv_candidates(download_root)
    selected = select_article_iv_candidate(
        candidates, english_only=bool(spec.get("english_only", True))
    )
    return {
        "document_id": str(spec["id"]),
        "label": str(spec["label"]),
        "path": str(selected.path),
        "page_count": selected.page_count,
        "source": "imf_article_iv_auto",
        "selection": {
            "repo_path": str(repo_path),
            "download_root": str(download_root),
            "candidate_count_found": len(candidates),
            "selected_year": selected.year,
            "selected_english_preferred": selected.english_preferred,
            "command_logs": command_logs,
            "top_candidates": [
                {
                    "path": str(candidate.path),
                    "page_count": candidate.page_count,
                    "year": candidate.year,
                    "english_preferred": candidate.english_preferred,
                }
                for candidate in sorted(
                    candidates,
                    key=lambda candidate: (
                        -int(candidate.english_preferred),
                        -candidate.page_count,
                        -candidate.year,
                        str(candidate.path.resolve()),
                    ),
                )[:10]
            ],
        },
    }


def resolve_corpus(
    manifest: dict[str, Any],
    *,
    run_dir: Path,
) -> dict[str, Any]:
    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("Manifest must define a non-empty documents list")

    resolved_documents: list[dict[str, Any]] = []
    corpus_logs_dir = ensure_directory(run_dir / "logs" / "prepare-corpus")
    for spec in documents:
        if not isinstance(spec, dict):
            raise ValueError("Each manifest document must be a mapping")
        source = str(spec.get("source", "fixed"))
        if source == "fixed":
            resolved_documents.append(resolve_fixed_document(spec))
            continue
        if source == "imf_article_iv_auto":
            resolved_documents.append(resolve_article_iv_document(spec, logs_dir=corpus_logs_dir))
            continue
        raise ValueError(f"Unsupported manifest source: {source}")

    return {
        "run_id": run_dir.name,
        "resolved_at": utc_now_iso(),
        "manifest_path": str(DEFAULT_MANIFEST),
        "rubric_path": str(DEFAULT_RUBRIC),
        "documents": resolved_documents,
    }


def clamp_page(page_number: int, page_count: int) -> int:
    if page_count <= 0:
        return 1
    return max(1, min(page_count, page_number))


def normalize_page_list(pages: list[Any], *, page_count: int | None = None) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for raw_page in pages:
        page = int(raw_page)
        if page_count is not None:
            page = clamp_page(page, page_count)
        if page in seen:
            continue
        normalized.append(page)
        seen.add(page)
    if not normalized:
        raise ValueError("At least one page must be provided")
    return normalized


def select_excerpt_plans(
    page_count: int,
    *,
    table_pages: list[int] | None = None,
    front_pages: int = 3,
    max_table_pages: int = 2,
    late_fraction: float = 0.8,
    max_excerpts: int = 5,
) -> list[dict[str, Any]]:
    if page_count <= 0:
        raise ValueError("page_count must be positive")

    plans: list[dict[str, Any]] = []
    used_pages: set[int] = set()

    front = list(range(1, min(front_pages, page_count) + 1))
    plans.append(
        {
            "excerpt_id": "front_excerpt",
            "label": "Front excerpt",
            "pages": front,
            "reason": "pages 1-3 when available",
        }
    )
    used_pages.update(front)

    for page in table_pages or []:
        normalized_page = clamp_page(page, page_count)
        if normalized_page in used_pages:
            continue
        plans.append(
            {
                "excerpt_id": f"table_p{normalized_page:04d}",
                "label": f"Table candidate page {normalized_page}",
                "pages": [normalized_page],
                "reason": "table page candidate from read-pdf --table-pages",
            }
        )
        used_pages.add(normalized_page)
        if len([plan for plan in plans if plan["excerpt_id"].startswith("table_")]) >= max_table_pages:
            break

    for excerpt_id, raw_page, reason in [
        ("midpoint", round(page_count / 2), "rounded midpoint page"),
        ("late", round(page_count * late_fraction), "late-document page"),
    ]:
        normalized_page = clamp_page(int(raw_page), page_count)
        if normalized_page in used_pages or len(plans) >= max_excerpts:
            continue
        plans.append(
            {
                "excerpt_id": f"{excerpt_id}_p{normalized_page:04d}",
                "label": f"{excerpt_id.title()} page {normalized_page}",
                "pages": [normalized_page],
                "reason": reason,
            }
        )
        used_pages.add(normalized_page)

    return plans[:max_excerpts]


def extract_pdf_excerpt(source_pdf: Path, pages: list[int], output_pdf: Path) -> None:
    ensure_directory(output_pdf.parent)
    with source_pdf.open("rb") as source_handle:
        reader = PdfReader(source_handle)
        writer = PdfWriter()
        for page_number in pages:
            writer.add_page(reader.pages[page_number - 1])
        with output_pdf.open("wb") as output_handle:
            writer.write(output_handle)


def detect_table_pages(pdf_path: Path) -> list[int]:
    result = run_capture(
        [str(READ_PDF), str(pdf_path), "--table-pages", "--max-output-tokens", "0"],
        check=False,
        env=sanitize_env(),
    )
    if result.returncode != 0:
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return []
    return [int(page) for page in pages if isinstance(page, int) or str(page).isdigit()]


def parse_image_manifests(stderr_text: str) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for line in stderr_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            manifests.append(json.loads(stripped))
        except json.JSONDecodeError:
            continue
    return manifests


def render_pdf_page_images(pdf_path: Path, *, pages: list[int], out_dir: Path) -> list[dict[str, Any]]:
    ensure_directory(out_dir)
    page_spec = ",".join(str(page) for page in pages)
    result = run_capture(
        [
            str(READ_PDF),
            str(pdf_path),
            "--as-images",
            "--pages",
            page_spec,
            "--outdir",
            str(out_dir),
            "--max-output-tokens",
            "0",
        ],
        check=True,
        env=sanitize_env(),
    )
    return parse_image_manifests(result.stderr)


def render_excerpt_images(excerpt_pdf: Path, *, out_dir: Path) -> list[dict[str, Any]]:
    ensure_directory(out_dir)
    result = run_capture(
        [
            str(READ_PDF),
            str(excerpt_pdf),
            "--as-images",
            "--outdir",
            str(out_dir),
            "--max-output-tokens",
            "0",
        ],
        check=True,
        env=sanitize_env(),
    )
    return parse_image_manifests(result.stderr)


def load_quality_targets(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        targets = raw.get("targets")
    else:
        targets = raw
    if not isinstance(targets, list) or not targets:
        raise ValueError(f"Quality targets file must define a non-empty targets list: {path}")

    normalized_targets: list[dict[str, Any]] = []
    valid_dimensions = set(RUBRIC_WEIGHTS)
    for index, target in enumerate(targets, start=1):
        if not isinstance(target, dict):
            raise ValueError(f"Quality target #{index} must be an object")
        source_pdf = Path(str(target["source_pdf"])).expanduser().resolve()
        if not source_pdf.is_file():
            raise FileNotFoundError(f"Quality target source PDF not found: {source_pdf}")
        page_count = get_pdf_page_count(source_pdf)
        pages = target.get("pages")
        if not isinstance(pages, list) or not pages:
            raise ValueError(f"Quality target #{index} must include a non-empty pages list")
        invalid_pages = [int(page) for page in pages if int(page) < 1 or int(page) > page_count]
        if invalid_pages:
            raise ValueError(
                f"Quality target #{index} references pages outside {source_pdf.name} "
                f"(1-{page_count}): {', '.join(str(page) for page in invalid_pages)}"
            )
        focus_dimensions = target.get("focus_dimensions")
        if not isinstance(focus_dimensions, list) or not focus_dimensions:
            raise ValueError(f"Quality target #{index} must include focus_dimensions")
        invalid_dimensions = [dimension for dimension in focus_dimensions if dimension not in valid_dimensions]
        if invalid_dimensions:
            raise ValueError(
                f"Quality target #{index} has unsupported focus dimensions: {', '.join(invalid_dimensions)}"
            )

        normalized_targets.append(
            {
                "target_id": str(target["target_id"]),
                "document_id": str(target["document_id"]),
                "document_label": str(target["document_label"]),
                "source_pdf": str(source_pdf),
                "pages": normalize_page_list(pages, page_count=page_count),
                "label": str(target["label"]),
                "target_type": str(target["target_type"]),
                "reason": str(target["reason"]),
                "search_anchor": str(target.get("search_anchor", "")),
                "focus_dimensions": [str(dimension) for dimension in focus_dimensions],
            }
        )
    return normalized_targets


def warm_command_for_engine(engine: str) -> list[str]:
    if engine == "markitdown":
        return [
            "uv",
            "run",
            "--with",
            "markitdown[pdf]",
            "python",
            "-c",
            "import importlib.metadata as m; print(m.version('markitdown'))",
        ]
    if engine == "pymupdf4llm":
        return [
            "uv",
            "run",
            "--with",
            "pymupdf4llm",
            "python",
            "-c",
            "import importlib.metadata as m; print(m.version('pymupdf4llm'))",
        ]
    if engine == "docling":
        return ["uvx", "--from", "docling", "docling", "--help"]
    if engine == "kreuzberg":
        return ["uvx", "--from", "kreuzberg", "kreuzberg", "--help"]
    if engine == "opendataloader":
        return ["uvx", "--from", "opendataloader-pdf", "opendataloader-pdf", "--help"]
    if engine == "opendataloader_hybrid_auto":
        return [
            "uvx",
            "--from",
            "opendataloader-pdf[hybrid]",
            "opendataloader-pdf-hybrid",
            "--help",
        ]
    if engine == "markit":
        return ["npx", "-y", "markit-ai@0.5.0", "--help"]
    if engine == "pdf_to_markdown":
        return [resolve_pdf_to_markdown_executable(), "--help"]
    raise ValueError(f"Unsupported engine: {engine}")


def version_command_for_engine(engine: str) -> list[str]:
    if engine == "markitdown":
        return [
            "uv",
            "run",
            "--with",
            "markitdown[pdf]",
            "python",
            "-c",
            "import importlib.metadata as m; print(m.version('markitdown'))",
        ]
    if engine == "pymupdf4llm":
        return [
            "uv",
            "run",
            "--with",
            "pymupdf4llm",
            "python",
            "-c",
            "import importlib.metadata as m; print(m.version('pymupdf4llm'))",
        ]
    if engine == "docling":
        return [
            "uv",
            "run",
            "--with",
            "docling",
            "python",
            "-c",
            "import importlib.metadata as m; print(m.version('docling'))",
        ]
    if engine == "kreuzberg":
        return [
            "uv",
            "run",
            "--with",
            "kreuzberg",
            "python",
            "-c",
            "import importlib.metadata as m; print(m.version('kreuzberg'))",
        ]
    if engine == "opendataloader":
        return [
            "uv",
            "run",
            "--with",
            "opendataloader-pdf",
            "python",
            "-c",
            "import importlib.metadata as m; print(m.version('opendataloader-pdf'))",
        ]
    if engine == "opendataloader_hybrid_auto":
        return [
            "uv",
            "run",
            "--with",
            "opendataloader-pdf[hybrid]",
            "python",
            "-c",
            "import importlib.metadata as m; print(m.version('opendataloader-pdf'))",
        ]
    if engine == "markit":
        return ["npx", "-y", "markit-ai@0.5.0", "--version"]
    if engine == "pdf_to_markdown":
        return [resolve_pdf_to_markdown_executable(), "--version"]
    raise ValueError(f"Unsupported engine: {engine}")


def find_single_markdown_file(output_dir: Path) -> Path | None:
    markdown_files = sorted(output_dir.rglob("*.md"))
    return markdown_files[0] if markdown_files else None


def convert_with_stdout(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    markdown_path: Path,
    stdout_log_path: Path,
    stderr_log_path: Path,
) -> subprocess.CompletedProcess[str]:
    ensure_directory(markdown_path.parent)
    ensure_directory(stdout_log_path.parent)
    ensure_directory(stderr_log_path.parent)
    with markdown_path.open("w", encoding="utf-8") as markdown_handle, stderr_log_path.open(
        "w", encoding="utf-8"
    ) as stderr_handle:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            text=True,
            stdout=markdown_handle,
            stderr=stderr_handle,
            check=False,
        )
    stdout_log_path.write_text("", encoding="utf-8")
    return result


def build_conversion_command(
    engine: str,
    *,
    input_pdf: Path,
    output_dir: Path,
    expected_pages: int,
    hybrid_url: str,
) -> tuple[list[str], str]:
    if engine == "markitdown":
        return (
            [
                "uv",
                "run",
                "--with",
                "markitdown[pdf]",
                "python",
                str(READ_PDF_TEXT),
                "--engine",
                "markitdown",
                "--expected-pages",
                str(expected_pages),
                str(input_pdf),
            ],
            "stdout_markdown",
        )
    if engine == "pymupdf4llm":
        return (
            [
                "uv",
                "run",
                "--with",
                "pymupdf4llm",
                "python",
                str(READ_PDF_TEXT),
                "--engine",
                "pymupdf4llm",
                "--expected-pages",
                str(expected_pages),
                str(input_pdf),
            ],
            "stdout_markdown",
        )
    if engine == "docling":
        return (
            [
                "uvx",
                "--from",
                "docling",
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
                str(output_dir),
            ],
            "output_dir_markdown",
        )
    if engine == "kreuzberg":
        return (
            [
                "uvx",
                "--from",
                "kreuzberg",
                "kreuzberg",
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
            ],
            "stdout_markdown",
        )
    if engine == "opendataloader":
        return (
            [
                "uvx",
                "--from",
                "opendataloader-pdf",
                "opendataloader-pdf",
                "--output-dir",
                str(output_dir),
                "--format",
                "markdown",
                "--quiet",
                "--markdown-page-separator",
                "<!-- PAGE %page-number% -->",
                str(input_pdf),
            ],
            "output_dir_markdown",
        )
    if engine == "opendataloader_hybrid_auto":
        return (
            [
                "uvx",
                "--from",
                "opendataloader-pdf",
                "opendataloader-pdf",
                "--output-dir",
                str(output_dir),
                "--format",
                "markdown",
                "--quiet",
                "--markdown-page-separator",
                "<!-- PAGE %page-number% -->",
                "--hybrid",
                "docling-fast",
                "--hybrid-mode",
                "auto",
                "--hybrid-url",
                hybrid_url,
                str(input_pdf),
            ],
            "output_dir_markdown",
        )
    if engine == "markit":
        return (
            [
                "npx",
                "-y",
                "markit-ai@0.5.0",
                "convert",
                str(input_pdf),
                "-q",
                "-o",
                str(output_dir / "output.md"),
            ],
            "fixed_output_file",
        )
    if engine == "pdf_to_markdown":
        return (
            [
                resolve_pdf_to_markdown_executable(),
                str(input_pdf),
                str(output_dir / "output.md"),
            ],
            "fixed_output_file",
        )
    raise ValueError(f"Unsupported engine: {engine}")


def run_conversion(
    engine: str,
    *,
    input_pdf: Path,
    output_dir: Path,
    expected_pages: int,
    hybrid_url: str,
    scope: str,
    document_id: str,
    excerpt_id: str | None,
) -> dict[str, Any]:
    ensure_directory(output_dir)
    stdout_log_path = output_dir / "stdout.txt"
    stderr_log_path = output_dir / "stderr.txt"
    markdown_path = output_dir / "output.md"

    cmd, output_mode = build_conversion_command(
        engine,
        input_pdf=input_pdf,
        output_dir=output_dir,
        expected_pages=expected_pages,
        hybrid_url=hybrid_url,
    )
    env = sanitize_env()
    start = time.perf_counter()
    if output_mode == "stdout_markdown":
        result = convert_with_stdout(
            cmd,
            cwd=REPO_ROOT,
            env=env,
            markdown_path=markdown_path,
            stdout_log_path=stdout_log_path,
            stderr_log_path=stderr_log_path,
        )
    else:
        result = run_to_files(
            cmd,
            cwd=REPO_ROOT,
            env=env,
            stdout_path=stdout_log_path,
            stderr_path=stderr_log_path,
        )
    elapsed = time.perf_counter() - start

    if output_mode == "output_dir_markdown":
        found_markdown = find_single_markdown_file(output_dir)
        if found_markdown is not None and found_markdown != markdown_path:
            markdown_path = found_markdown
    if output_mode == "fixed_output_file":
        markdown_path = output_dir / "output.md"

    success = result.returncode == 0 and markdown_path.is_file()
    if success and markdown_path.stat().st_size == 0:
        success = False

    return {
        "scope": scope,
        "document_id": document_id,
        "excerpt_id": excerpt_id or "",
        "engine": engine,
        "command": quote_command(cmd),
        "returncode": result.returncode,
        "status": "ok" if success else "failed",
        "input_pdf": str(input_pdf),
        "output_dir": str(output_dir),
        "output_markdown": str(markdown_path) if markdown_path.exists() else "",
        "stdout_path": str(stdout_log_path),
        "stderr_path": str(stderr_log_path),
        "pages": expected_pages,
        "seconds": round(elapsed, 4),
        "seconds_per_page": round(elapsed / expected_pages, 6),
    }


def wait_for_port(host: str, port: int, *, timeout_seconds: float = 120.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for {host}:{port}")


def start_hybrid_server(
    *,
    host: str,
    port: int,
    log_dir: Path,
) -> tuple[subprocess.Popen[str], dict[str, Any]]:
    ensure_directory(log_dir)
    stdout_path = log_dir / "hybrid-server.stdout.txt"
    stderr_path = log_dir / "hybrid-server.stderr.txt"
    cmd = [
        "uvx",
        "--from",
        "opendataloader-pdf[hybrid]",
        "opendataloader-pdf-hybrid",
        "--host",
        host,
        "--port",
        str(port),
        "--enrich-formula",
    ]
    env = sanitize_env()
    stdout_handle = stdout_path.open("w", encoding="utf-8")
    stderr_handle = stderr_path.open("w", encoding="utf-8")
    start = time.perf_counter()
    process = subprocess.Popen(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=stdout_handle,
        stderr=stderr_handle,
    )
    try:
        wait_for_port(host, port)
    except Exception:
        process.terminate()
        stdout_handle.close()
        stderr_handle.close()
        raise

    elapsed = time.perf_counter() - start
    return process, {
        "command": quote_command(cmd),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "startup_seconds": round(elapsed, 4),
        "url": f"http://{host}:{port}",
        "stdout_handle": stdout_handle,
        "stderr_handle": stderr_handle,
    }


def stop_hybrid_server(process: subprocess.Popen[str] | None, metadata: dict[str, Any] | None) -> None:
    if process is None or metadata is None:
        return
    try:
        process.terminate()
        process.wait(timeout=15)
    except Exception:
        process.kill()
    finally:
        stdout_handle = metadata.get("stdout_handle")
        stderr_handle = metadata.get("stderr_handle")
        if stdout_handle:
            stdout_handle.close()
        if stderr_handle:
            stderr_handle.close()


def warm_and_capture_versions(engines: list[str]) -> dict[str, Any]:
    details: dict[str, Any] = {"generated_at": utc_now_iso(), "engines": {}}
    env = sanitize_env()
    for engine in engines:
        warm_result = run_capture(warm_command_for_engine(engine), env=env, check=False)
        version_result = run_capture(version_command_for_engine(engine), env=env, check=False)
        details["engines"][engine] = {
            "warm_command": warm_command_for_engine(engine),
            "warm_returncode": warm_result.returncode,
            "warm_stdout": warm_result.stdout.strip(),
            "warm_stderr": warm_result.stderr.strip(),
            "version_command": version_command_for_engine(engine),
            "version_returncode": version_result.returncode,
            "version_stdout": version_result.stdout.strip(),
            "version_stderr": version_result.stderr.strip(),
        }
    return details


def summarize_quality_documents(excerpt_records: list[dict[str, Any]]) -> list[dict[str, str]]:
    ordered_documents: dict[str, dict[str, str]] = {}
    for record in excerpt_records:
        document_id = str(record["document_id"])
        if document_id in ordered_documents:
            continue
        ordered_documents[document_id] = {
            "document_id": document_id,
            "label": str(record["document_label"]),
        }
    return list(ordered_documents.values())


def build_grading_bundle(
    *,
    run_dir: Path,
    excerpt_records: list[dict[str, Any]],
    engines: list[str],
    rubric_path: Path,
    grade_model: str,
) -> dict[str, Any]:
    bundle_documents: list[dict[str, Any]] = []
    for document in summarize_quality_documents(excerpt_records):
        document_id = document["document_id"]
        doc_excerpts = [record for record in excerpt_records if record["document_id"] == document_id]
        excerpt_entries: list[dict[str, Any]] = []
        for record in doc_excerpts:
            reference_images = []
            for image in record["reference_images"]:
                if not isinstance(image, dict):
                    continue
                reference_images.append(
                    {
                        key: value
                        for key, value in image.items()
                        if key in {"page", "path", "dpi", "format", "mode", "tool", "tool_version", "engine"}
                    }
                )
            excerpt_entries.append(
                {
                    "target_id": record["target_id"],
                    "excerpt_id": record["excerpt_id"],
                    "label": record["label"],
                    "target_type": record["target_type"],
                    "reason": record["reason"],
                    "search_anchor": record["search_anchor"],
                    "focus_dimensions": record["focus_dimensions"],
                    "pages": record["pages"],
                    "reference_images": reference_images,
                    "engine_outputs": {
                        engine: record["engine_outputs"].get(engine, "") for engine in engines
                    },
                }
            )

        grading_dir = ensure_directory(run_dir / "grading" / document_id)
        grading_input = {
            "document_id": document_id,
            "label": document["label"],
            "rubric_path": str(rubric_path.resolve()),
            "grade_model": grade_model,
            "engines": engines,
            "excerpts": excerpt_entries,
        }
        grading_input_path = grading_dir / "grading-input.json"
        write_json(grading_input_path, grading_input)

        prompt_path = grading_dir / "subagent-prompt.md"
        prompt = textwrap.dedent(
            f"""
            Grade the markdown extraction quality for document `{document_id}` using the rubric at:
            `{rubric_path.resolve()}`

            Inputs are listed in:
            `{grading_input_path.resolve()}`

            Instructions:
            - Compare each engine's excerpt markdown output against the rendered source-page images for each target.
            - Do not read any full original PDF or full-document markdown output. Only use the target-local files listed in the grading input JSON.
            - Use the exact rubric dimensions and 0-5 integer scale from the rubric.
            - Score only the dimensions listed in each target's `focus_dimensions`.
            - For every dimension not listed in `focus_dimensions`, return `null` and use evidence `N/A - not scored for this target`.
            - Use `null` for `formulas` when a target does not contain meaningful formula content.
            - Provide one sentence of evidence for every scored dimension.
            - Do not infer quality from speed or tool reputation; score only the excerpt outputs.

            Return JSON with this shape:
            {{
              "status": "completed",
              "document_id": "{document_id}",
              "model": "{grade_model}",
              "excerpts": [
                {{
                  "excerpt_id": "gfsm_table_4_1",
                  "engines": {{
                    "markitdown": {{
                      "scores": {{
                        "reading_order": 0,
                        "headings": null,
                        "tables": 0,
                        "formulas": null,
                        "usability": 0
                      }},
                      "evidence": {{
                        "reading_order": "...",
                        "headings": "N/A - not scored for this target",
                        "tables": "...",
                        "formulas": "N/A",
                        "usability": "..."
                      }}
                    }}
                  }}
                }}
              ]
            }}
            """
        ).strip()
        write_text(prompt_path, prompt + "\n")

        bundle_documents.append(
            {
                "document_id": document_id,
                "label": document["label"],
                "grading_input_path": str(grading_input_path),
                "prompt_path": str(prompt_path),
            }
        )

    payload = {
        "status": "pending_subagent_grading",
        "generated_at": utc_now_iso(),
        "model": grade_model,
        "rubric_path": str(rubric_path.resolve()),
        "documents": bundle_documents,
    }
    write_json(run_dir / "grading" / "bundle.json", payload)
    return payload


def build_pending_grades_template(
    *,
    excerpt_records: list[dict[str, Any]],
    engines: list[str],
    grade_model: str,
) -> dict[str, Any]:
    documents_payload: list[dict[str, Any]] = []
    for document in summarize_quality_documents(excerpt_records):
        doc_excerpts = [record for record in excerpt_records if record["document_id"] == document["document_id"]]
        excerpt_payload = []
        for record in doc_excerpts:
            focus_dimensions = set(record.get("focus_dimensions", RUBRIC_WEIGHTS.keys()))
            excerpt_payload.append(
                {
                    "target_id": record["target_id"],
                    "excerpt_id": record["excerpt_id"],
                    "label": record["label"],
                    "focus_dimensions": record["focus_dimensions"],
                    "engines": {
                        engine: {
                            "scores": {
                                dimension: (None if dimension in focus_dimensions else None)
                                for dimension in RUBRIC_WEIGHTS
                            },
                            "evidence": {
                                dimension: (
                                    ""
                                    if dimension in focus_dimensions
                                    else "N/A - not scored for this target"
                                )
                                for dimension in RUBRIC_WEIGHTS
                            },
                        }
                        for engine in engines
                    },
                }
            )
        documents_payload.append(
            {
                "document_id": document["document_id"],
                "label": document["label"],
                "model": grade_model,
                "excerpts": excerpt_payload,
            }
        )
    return {
        "status": "pending_subagent_grading",
        "generated_at": utc_now_iso(),
        "model": grade_model,
        "documents": documents_payload,
    }


def compute_excerpt_quality(scores: dict[str, int | None], weights: dict[str, float]) -> float | None:
    weighted_total = 0.0
    weight_total = 0.0
    for dimension, weight in weights.items():
        score = scores.get(dimension)
        if score is None:
            continue
        weighted_total += float(score) * weight
        weight_total += weight
    if weight_total == 0:
        return None
    return (weighted_total / weight_total) / 5.0 * 100.0


def aggregate_grades(
    grades_payload: dict[str, Any],
    *,
    weights: dict[str, float],
) -> dict[str, Any]:
    documents = grades_payload.get("documents", [])
    if not isinstance(documents, list):
        return {"status": "pending", "document_scores": {}, "overall_scores": {}}

    document_scores: dict[str, dict[str, float]] = {}
    overall_scores: dict[str, list[float]] = {}

    for document in documents:
        document_id = str(document.get("document_id", ""))
        excerpts = document.get("excerpts", [])
        engine_scores: dict[str, list[float]] = {}
        if not isinstance(excerpts, list):
            continue
        for excerpt in excerpts:
            engines = excerpt.get("engines", {})
            if not isinstance(engines, dict):
                continue
            for engine, payload in engines.items():
                if not isinstance(payload, dict):
                    continue
                scores = payload.get("scores", {})
                if not isinstance(scores, dict):
                    continue
                excerpt_quality = compute_excerpt_quality(scores, weights)
                if excerpt_quality is None:
                    continue
                engine_scores.setdefault(engine, []).append(excerpt_quality)

        document_scores[document_id] = {}
        for engine, values in engine_scores.items():
            document_scores[document_id][engine] = round(mean(values), 2)
            overall_scores.setdefault(engine, []).append(document_scores[document_id][engine])

    return {
        "status": "completed"
        if str(grades_payload.get("status", "")).lower() == "completed"
        else "pending",
        "document_scores": document_scores,
        "overall_scores": {engine: round(mean(values), 2) for engine, values in overall_scores.items()},
    }


def build_summary(
    *,
    run_dir: Path,
    resolved_documents: list[dict[str, Any]],
    timing_rows: list[dict[str, Any]],
    grades_payload: dict[str, Any],
) -> str:
    full_rows = [row for row in timing_rows if row["scope"] == "full"]
    grades_summary = aggregate_grades(grades_payload, weights=RUBRIC_WEIGHTS)
    timing_by_doc_engine = {
        (row["document_id"], row["engine"]): row for row in full_rows if row["status"] == "ok"
    }

    doc_fastest: dict[str, float] = {}
    for document in resolved_documents:
        document_id = document["document_id"]
        doc_values = [
            float(row["seconds_per_page"])
            for row in full_rows
            if row["document_id"] == document_id and row["status"] == "ok"
        ]
        if doc_values:
            doc_fastest[document_id] = min(doc_values)

    engine_speed_indices: dict[str, list[float]] = {}
    for document in resolved_documents:
        document_id = document["document_id"]
        fastest = doc_fastest.get(document_id)
        if not fastest:
            continue
        for engine in ENGINE_ORDER:
            row = timing_by_doc_engine.get((document_id, engine))
            if row is None:
                continue
            engine_speed_indices.setdefault(engine, []).append(
                round(fastest / float(row["seconds_per_page"]), 4)
            )

    overall_speed_index = {
        engine: round(mean(values), 4) for engine, values in engine_speed_indices.items()
    }
    blended_scores = {}
    for engine, quality in grades_summary["overall_scores"].items():
        speed_index = overall_speed_index.get(engine)
        if speed_index is None:
            continue
        blended_scores[engine] = round(quality * speed_index, 2)

    lines = [
        "# PDF Backend Benchmark",
        "",
        f"Run directory: `{run_dir.resolve()}`",
        f"Generated: `{utc_now_iso()}`",
        "",
        "## Corpus",
        "",
    ]
    for document in resolved_documents:
        lines.append(
            f"- `{document['document_id']}`: {document['label']} "
            f"({document['page_count']} pages, `{document['path']}`)"
        )

    lines.extend(
        [
            "",
            "## Full-document speed",
            "",
            "| Engine | Document | Status | Seconds | Seconds/page | Output |",
            "| --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in full_rows:
        output = row["output_markdown"] or row["stderr_path"]
        lines.append(
            f"| `{row['engine']}` | `{row['document_id']}` | `{row['status']}` | "
            f"{row['seconds']:.4f} | {row['seconds_per_page']:.6f} | `{output}` |"
        )

    if grades_summary["status"] != "completed":
        lines.extend(
            [
                "",
                "## Quality grading",
                "",
                "Subagent grading is still pending. Use the prompt/input files under:",
                f"`{(run_dir / 'grading').resolve()}`",
                "",
                "A placeholder `grades.json` was written so the expected schema is pinned in the run artifacts.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "## Spot-check quality scores",
                "",
                "| Engine | Overall quality |",
                "| --- | ---: |",
            ]
        )
        for engine in ENGINE_ORDER:
            if engine in grades_summary["overall_scores"]:
                lines.append(f"| `{engine}` | {grades_summary['overall_scores'][engine]:.2f} |")
        lines.extend(
            [
                "",
                "## Blended score",
                "",
                "Derived from spot-check quality x speed index.",
                "",
                "| Engine | Quality | Speed index | Blended |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for engine in ENGINE_ORDER:
            if engine not in blended_scores:
                continue
            lines.append(
                f"| `{engine}` | {grades_summary['overall_scores'][engine]:.2f} | "
                f"{overall_speed_index.get(engine, 0.0):.4f} | {blended_scores[engine]:.2f} |"
            )

    return "\n".join(lines) + "\n"


def select_engines(manifest: dict[str, Any], requested: str | None) -> list[str]:
    if requested:
        engines = [engine.strip() for engine in requested.split(",") if engine.strip()]
    else:
        defaults = manifest.get("defaults", {})
        configured = defaults.get("engines", ENGINE_ORDER) if isinstance(defaults, dict) else ENGINE_ORDER
        engines = [str(engine) for engine in configured]
    invalid = [engine for engine in engines if engine not in ENGINE_ORDER]
    if invalid:
        raise ValueError(f"Unsupported engines requested: {', '.join(invalid)}")
    return engines


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def normalize_timing_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        normalized = dict(row)
        for key in ("returncode", "pages"):
            if key in normalized and normalized[key] != "":
                normalized[key] = int(normalized[key])
        for key in ("seconds", "seconds_per_page"):
            if key in normalized and normalized[key] != "":
                normalized[key] = float(normalized[key])
        normalized_rows.append(normalized)
    return normalized_rows


def build_quality_target_excerpt_records(
    *,
    quality_targets: list[dict[str, Any]],
    run_dir: Path,
    engines: list[str],
    hybrid_url: str,
    timing_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    excerpt_records: list[dict[str, Any]] = []
    for target in quality_targets:
        document_id = str(target["document_id"])
        target_id = str(target["target_id"])
        source_pdf = Path(str(target["source_pdf"])).expanduser().resolve()
        pages = normalize_page_list(list(target["pages"]))

        excerpt_dir = ensure_directory(run_dir / "excerpts" / document_id / target_id)
        excerpt_pdf = excerpt_dir / "excerpt.pdf"
        extract_pdf_excerpt(source_pdf, pages, excerpt_pdf)
        reference_images = render_pdf_page_images(
            source_pdf, pages=pages, out_dir=excerpt_dir / "reference-images"
        )

        engine_outputs: dict[str, str] = {}
        for engine in engines:
            result = run_conversion(
                engine,
                input_pdf=excerpt_pdf,
                output_dir=excerpt_dir / engine,
                expected_pages=len(pages),
                hybrid_url=hybrid_url,
                scope="excerpt",
                document_id=document_id,
                excerpt_id=target_id,
            )
            timing_rows.append(result)
            if result["status"] == "ok":
                engine_outputs[engine] = result["output_markdown"]

        record = {
            "document_id": document_id,
            "document_label": str(target["document_label"]),
            "target_id": target_id,
            "excerpt_id": target_id,
            "label": str(target["label"]),
            "target_type": str(target["target_type"]),
            "reason": str(target["reason"]),
            "search_anchor": str(target.get("search_anchor", "")),
            "focus_dimensions": list(target["focus_dimensions"]),
            "pages": pages,
            "source_pdf": str(source_pdf),
            "excerpt_pdf": str(excerpt_pdf),
            "reference_images": reference_images,
            "engine_outputs": engine_outputs,
        }
        excerpt_records.append(record)
        write_json(excerpt_dir / "excerpt-manifest.json", record)
    return excerpt_records


def prepare_corpus_command(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = load_manifest(manifest_path)
    artifact_root = resolve_artifact_root(manifest, args.artifact_root)
    run_id = args.run_id or build_run_id()
    run_dir = ensure_directory(artifact_root / run_id)
    resolved = resolve_corpus(manifest, run_dir=run_dir)
    resolved["manifest_path"] = str(manifest_path)
    write_json(run_dir / "corpus-resolved.json", resolved)
    print(run_dir / "corpus-resolved.json")
    return 0


def run_benchmark_command(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = load_manifest(manifest_path)
    artifact_root = resolve_artifact_root(manifest, args.artifact_root)
    run_id = args.run_id or build_run_id()
    run_dir = ensure_directory(artifact_root / run_id)
    engines = select_engines(manifest, args.engines)
    grade_model = str(manifest.get("defaults", {}).get("grade_model", "gpt-5.4-mini"))
    rubric_path = Path(str(manifest.get("defaults", {}).get("rubric_path", DEFAULT_RUBRIC))).expanduser()

    if args.resolved_corpus:
        resolved_path = Path(args.resolved_corpus).expanduser().resolve()
        resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        write_json(run_dir / "corpus-resolved.json", resolved)
    else:
        resolved = resolve_corpus(manifest, run_dir=run_dir)
        resolved["manifest_path"] = str(manifest_path)
        write_json(run_dir / "corpus-resolved.json", resolved)

    versions = warm_and_capture_versions(engines)
    write_json(run_dir / "versions.json", versions)

    defaults = manifest.get("defaults", {}) if isinstance(manifest.get("defaults"), dict) else {}
    hybrid_host = str(defaults.get("hybrid_host", "127.0.0.1"))
    hybrid_port = int(defaults.get("hybrid_port", 5002))
    hybrid_url = f"http://{hybrid_host}:{hybrid_port}"
    hybrid_process: subprocess.Popen[str] | None = None
    hybrid_metadata: dict[str, Any] | None = None
    if "opendataloader_hybrid_auto" in engines:
        hybrid_process, hybrid_metadata = start_hybrid_server(
            host=hybrid_host,
            port=hybrid_port,
            log_dir=run_dir / "logs" / "hybrid-server",
        )
        versions["hybrid_server"] = {
            key: value
            for key, value in hybrid_metadata.items()
            if key not in {"stdout_handle", "stderr_handle"}
        }
        write_json(run_dir / "versions.json", versions)

    timing_rows: list[dict[str, Any]] = []
    quality_targets: list[dict[str, Any]] = []
    if args.quality_targets_file:
        quality_targets = load_quality_targets(Path(args.quality_targets_file).expanduser().resolve())
        write_json(
            run_dir / "quality-targets.json",
            {
                "generated_at": utc_now_iso(),
                "targets": quality_targets,
            },
        )

    excerpt_records: list[dict[str, Any]] = []
    try:
        for document in resolved["documents"]:
            document_id = str(document["document_id"])
            pdf_path = Path(str(document["path"])).expanduser().resolve()
            page_count = int(document["page_count"])

            for engine in engines:
                result = run_conversion(
                    engine,
                    input_pdf=pdf_path,
                    output_dir=run_dir / "conversions" / document_id / engine,
                    expected_pages=page_count,
                    hybrid_url=hybrid_url,
                    scope="full",
                    document_id=document_id,
                    excerpt_id=None,
                )
                timing_rows.append(result)

        if quality_targets:
            excerpt_records = build_quality_target_excerpt_records(
                quality_targets=quality_targets,
                run_dir=run_dir,
                engines=engines,
                hybrid_url=hybrid_url,
                timing_rows=timing_rows,
            )
            write_json(
                run_dir / "quality-targets.json",
                {
                    "generated_at": utc_now_iso(),
                    "targets": excerpt_records,
                },
            )
        else:
            for document in resolved["documents"]:
                document_id = str(document["document_id"])
                pdf_path = Path(str(document["path"])).expanduser().resolve()
                page_count = int(document["page_count"])
                table_pages = detect_table_pages(pdf_path)
                excerpt_plans = select_excerpt_plans(page_count, table_pages=table_pages)
                for plan in excerpt_plans:
                    excerpt_dir = ensure_directory(
                        run_dir / "excerpts" / document_id / str(plan["excerpt_id"])
                    )
                    excerpt_pdf = excerpt_dir / "excerpt.pdf"
                    extract_pdf_excerpt(pdf_path, plan["pages"], excerpt_pdf)
                    reference_images = render_excerpt_images(
                        excerpt_pdf, out_dir=excerpt_dir / "reference-images"
                    )

                    engine_outputs: dict[str, str] = {}
                    for engine in engines:
                        result = run_conversion(
                            engine,
                            input_pdf=excerpt_pdf,
                            output_dir=excerpt_dir / engine,
                            expected_pages=len(plan["pages"]),
                            hybrid_url=hybrid_url,
                            scope="excerpt",
                            document_id=document_id,
                            excerpt_id=str(plan["excerpt_id"]),
                        )
                        timing_rows.append(result)
                        if result["status"] == "ok":
                            engine_outputs[engine] = result["output_markdown"]

                    record = {
                        "document_id": document_id,
                        "document_label": str(document["label"]),
                        "target_id": str(plan["excerpt_id"]),
                        "excerpt_id": str(plan["excerpt_id"]),
                        "label": str(plan["label"]),
                        "target_type": "heuristic_excerpt",
                        "reason": str(plan["reason"]),
                        "search_anchor": "",
                        "focus_dimensions": list(RUBRIC_WEIGHTS),
                        "source_pdf": str(pdf_path),
                        "pages": plan["pages"],
                        "excerpt_pdf": str(excerpt_pdf),
                        "reference_images": reference_images,
                        "engine_outputs": engine_outputs,
                    }
                    excerpt_records.append(record)
                    write_json(excerpt_dir / "excerpt-manifest.json", record)
    finally:
        stop_hybrid_server(hybrid_process, hybrid_metadata)

    timing_fieldnames = [
        "scope",
        "document_id",
        "excerpt_id",
        "engine",
        "status",
        "returncode",
        "pages",
        "seconds",
        "seconds_per_page",
        "input_pdf",
        "output_markdown",
        "output_dir",
        "stdout_path",
        "stderr_path",
        "command",
    ]
    write_csv(run_dir / "timings.csv", timing_rows, timing_fieldnames)

    build_grading_bundle(
        run_dir=run_dir,
        excerpt_records=excerpt_records,
        engines=engines,
        rubric_path=rubric_path,
        grade_model=grade_model,
    )

    if args.grades_file:
        grades_payload = json.loads(
            Path(args.grades_file).expanduser().resolve().read_text(encoding="utf-8")
        )
    else:
        grades_payload = build_pending_grades_template(
            excerpt_records=excerpt_records,
            engines=engines,
            grade_model=grade_model,
        )
    write_json(run_dir / "grades.json", grades_payload)

    summary = build_summary(
        run_dir=run_dir,
        resolved_documents=resolved["documents"],
        timing_rows=timing_rows,
        grades_payload=grades_payload,
    )
    write_text(run_dir / "summary.md", summary)
    print(run_dir)
    return 0


def finalize_benchmark_command(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    resolved = load_json(run_dir / "corpus-resolved.json")
    timings_rows = normalize_timing_rows(
        list(csv.DictReader((run_dir / "timings.csv").open(encoding="utf-8")))
    )
    grades_payload = load_json(Path(args.grades_file).expanduser().resolve())

    write_json(run_dir / "grades.json", grades_payload)
    summary = build_summary(
        run_dir=run_dir,
        resolved_documents=resolved["documents"],
        timing_rows=timings_rows,
        grades_payload=grades_payload,
    )
    write_text(run_dir / "summary.md", summary)
    print(run_dir)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CPU-only PDF backend benchmark harness for read-pdf."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser(
        "prepare-corpus",
        help="Resolve the pilot corpus and write corpus-resolved.json",
    )
    prepare_parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    prepare_parser.add_argument("--run-id", default=None)
    prepare_parser.add_argument("--artifact-root", default=None)
    prepare_parser.set_defaults(func=prepare_corpus_command)

    run_parser = subparsers.add_parser(
        "run",
        help="Run the benchmark and write artifacts under tmp/benchmark/pdf/<run_id>/",
    )
    run_parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    run_parser.add_argument("--run-id", default=None)
    run_parser.add_argument("--artifact-root", default=None)
    run_parser.add_argument(
        "--resolved-corpus",
        default=None,
        help="Optional existing corpus-resolved.json to reuse.",
    )
    run_parser.add_argument(
        "--engines",
        default=None,
        help="Comma-separated engine override. Defaults to manifest defaults.engines.",
    )
    run_parser.add_argument(
        "--grades-file",
        default=None,
        help="Optional completed subagent grades JSON to import into the run output.",
    )
    run_parser.add_argument(
        "--quality-targets-file",
        default=None,
        help="Optional JSON file defining explicit spot-check targets for quality grading.",
    )
    run_parser.set_defaults(func=run_benchmark_command)

    finalize_parser = subparsers.add_parser(
        "finalize",
        help="Import completed grades for an existing run and regenerate summary.md",
    )
    finalize_parser.add_argument("--run-dir", required=True)
    finalize_parser.add_argument("--grades-file", required=True)
    finalize_parser.set_defaults(func=finalize_benchmark_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
