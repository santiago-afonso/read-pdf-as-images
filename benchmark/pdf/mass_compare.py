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
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any



REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = REPO_ROOT / "benchmark" / "pdf"
CONTAINER_DIR = BENCHMARK_DIR / "mass_compare_container"
CONTAINERFILE = CONTAINER_DIR / "Containerfile"
CONTAINER_RUNNER = CONTAINER_DIR / "engine_runner.py"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "tmp" / "benchmark" / "pdf-mass-compare"
DEFAULT_IMAGE = "localhost/read-pdf-mass-compare:alpine"


@dataclass(frozen=True)
class EngineSpec:
    name: str
    output_name: str
    output_format: str
    category: str
    description: str
    notes: str = ""


CORE_ENGINES = [
    "markitdown",
    "pymupdf4llm",
    "docling",
    "kreuzberg",
    "opendataloader",
    "nutrient_pdf_to_markdown",
    "liteparse",
    "edgeparse",
    "pdf_oxide",
    "unstructured",
]

COMPONENT_ENGINES = [
    "pymupdf",
    "pdfplumber",
    "pdfminer_six",
    "pypdfium2",
    "pypdf",
    "poppler_pdftotext",
    "camelot",
    "tabula_java",
]

LEGACY_EXISTING_ENGINES = [
    "markit_ai",
]

ENGINE_SPECS: dict[str, EngineSpec] = {
    "markitdown": EngineSpec(
        "markitdown",
        "output.md",
        "markdown",
        "core",
        "Microsoft MarkItDown PDF to Markdown baseline.",
    ),
    "pymupdf4llm": EngineSpec(
        "pymupdf4llm",
        "output.md",
        "markdown",
        "core",
        "PyMuPDF4LLM direct PDF to Markdown converter.",
    ),
    "docling": EngineSpec(
        "docling",
        "output.md",
        "markdown",
        "core",
        "Docling local PDF converter with --no-ocr; falls back to docling-parse on Alpine/musl.",
        "Full torch-backed Docling CLI is not required for the Alpine fallback used by this comparator.",
    ),
    "kreuzberg": EngineSpec(
        "kreuzberg",
        "output.md",
        "markdown",
        "core",
        "Kreuzberg extraction CLI with OCR disabled and CPU acceleration.",
    ),
    "opendataloader": EngineSpec(
        "opendataloader",
        "output.md",
        "markdown",
        "core",
        "OpenDataLoader PDF Markdown conversion, non-hybrid mode.",
    ),
    "nutrient_pdf_to_markdown": EngineSpec(
        "nutrient_pdf_to_markdown",
        "output.md",
        "markdown",
        "core",
        "Nutrient/PSPDFKit pdf-to-markdown binary.",
        "Proprietary/free-quota binary is not baked into the image; pass --pdf-to-markdown-binary or bake it yourself.",
    ),
    "liteparse": EngineSpec(
        "liteparse",
        "output.json",
        "json",
        "core",
        "LlamaIndex LiteParse spatial/layout parser.",
        "Default CLI templates are best-effort; use --engine-command liteparse=... if the package CLI changes.",
    ),
    "edgeparse": EngineSpec(
        "edgeparse",
        "output.md",
        "markdown",
        "core",
        "EdgeParse deterministic digital-PDF conversion.",
        "Default CLI templates are best-effort; use --engine-command edgeparse=... if needed.",
    ),
    "pdf_oxide": EngineSpec(
        "pdf_oxide",
        "output.md",
        "markdown",
        "core",
        "PDF Oxide Rust-core Markdown conversion.",
        "Default CLI templates are best-effort; use --engine-command pdf_oxide=... if needed.",
    ),
    "unstructured": EngineSpec(
        "unstructured",
        "output.md",
        "markdown",
        "core",
        "Unstructured non-OCR fast path, falling back to raw pdfminer text when PDF extras need offline assets.",
    ),
    "pymupdf": EngineSpec(
        "pymupdf",
        "output.md",
        "markdown",
        "component",
        "Low-level PyMuPDF text/Markdown extraction baseline.",
    ),
    "pdfplumber": EngineSpec(
        "pdfplumber",
        "output.md",
        "markdown",
        "component",
        "pdfplumber layout text plus detected tables as Markdown.",
    ),
    "pdfminer_six": EngineSpec(
        "pdfminer_six",
        "output.txt",
        "text",
        "component",
        "pdfminer.six high-level text extraction baseline.",
    ),
    "pypdfium2": EngineSpec(
        "pypdfium2",
        "output.txt",
        "text",
        "component",
        "PDFium text extraction via pypdfium2.",
    ),
    "pypdf": EngineSpec(
        "pypdf",
        "output.txt",
        "text",
        "component",
        "pypdf extract_text weak baseline.",
    ),
    "poppler_pdftotext": EngineSpec(
        "poppler_pdftotext",
        "output.txt",
        "text",
        "component",
        "Poppler pdftotext -layout baseline.",
    ),
    "camelot": EngineSpec(
        "camelot",
        "output.md",
        "markdown",
        "component",
        "Camelot table-only extraction rendered as Markdown tables.",
    ),
    "tabula_java": EngineSpec(
        "tabula_java",
        "output.json",
        "json",
        "component",
        "Tabula Java table-only extraction JSON.",
    ),
    "markit_ai": EngineSpec(
        "markit_ai",
        "output.md",
        "markdown",
        "legacy",
        "Existing benchmark Markit AI comparator (optional, installed by npm when image build succeeds).",
    ),
}

ENGINE_GROUPS: dict[str, list[str]] = {
    "core": CORE_ENGINES,
    "components": COMPONENT_ENGINES,
    "component": COMPONENT_ENGINES,
    "legacy": LEGACY_EXISTING_ENGINES,
    "existing": ["markitdown", "pymupdf4llm", "docling", "kreuzberg", "opendataloader", "markit_ai"],
    "all": CORE_ENGINES + COMPONENT_ENGINES,
    "all_with_legacy": CORE_ENGINES + COMPONENT_ENGINES + LEGACY_EXISTING_ENGINES,
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


class MassCompareError(RuntimeError):
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
    if not cleaned:
        return None

    pages: list[int] = []
    seen: set[int] = set()
    for raw_part in cleaned.split(","):
        part = raw_part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ValueError(
                f"Invalid page range token `{raw_part}` in `{spec}`. Use forms like 1,3-5,9."
            )
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
    if not pages:
        return None
    return pages


def pdf_page_count(path: Path) -> int:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise MassCompareError(
            "Missing Python dependency `pypdf`. Run with `uv run python benchmark/pdf/mass_compare.py ...` "
            "or install the project dependencies first."
        ) from exc

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
        raise MassCompareError(
            "Missing Python dependency `pypdf`. Run with `uv run python benchmark/pdf/mass_compare.py ...` "
            "or install the project dependencies first."
        ) from exc

    with source_pdf.open("rb") as handle:
        reader = PdfReader(handle)
        writer = PdfWriter()
        for page in pages:
            writer.add_page(reader.pages[page - 1])
        with output_pdf.open("wb") as out_handle:
            writer.write(out_handle)


def expand_engine_token(token: str) -> list[str]:
    if token in ENGINE_GROUPS:
        return ENGINE_GROUPS[token]
    if token in ENGINE_SPECS:
        return [token]
    valid = sorted(set(ENGINE_SPECS) | set(ENGINE_GROUPS))
    raise ValueError(f"Unknown engine or group `{token}`. Valid values: {', '.join(valid)}")


def resolve_engines(raw: str | None) -> list[str]:
    spec = raw or "all"
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


def sanitize_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base or os.environ)
    for key in LOCAL_ONLY_ENV_BLOCKLIST:
        env.pop(key, None)
    return env


def quote_command(cmd: list[str]) -> str:
    return subprocess.list2cmdline(cmd) if os.name == "nt" else " ".join(_shell_quote(part) for part in cmd)


def _shell_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def run_subprocess(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def podman_image_exists(podman: str, image: str) -> bool:
    result = run_subprocess([podman, "image", "exists", image])
    return result.returncode == 0


def default_corp_ca_files() -> list[Path]:
    setup_dir = Path.home() / "machine-setup"
    names = [
        "codex-ca-bundle.pem",
        "wbg_root_ca_g2.cer",
        "wbg-cloud-root-ca.pem",
        "wbg-cloud-issuing-ca1.pem",
        "wbg-cloud-openai-bundle.pem",
    ]
    return [setup_dir / name for name in names if (setup_dir / name).is_file()]


def write_combined_ca_bundle(path: Path, sources: list[Path]) -> bool:
    if not sources:
        return False
    chunks: list[str] = []
    for source in sources:
        text = source.read_text(encoding="utf-8", errors="ignore").strip()
        if text:
            chunks.append(text)
    if not chunks:
        return False
    path.write_text("\n".join(chunks) + "\n", encoding="utf-8")
    return True


def build_image(podman: str, image: str, *, build_args: list[str] | None = None) -> None:
    if not CONTAINERFILE.is_file():
        raise FileNotFoundError(f"Containerfile not found: {CONTAINERFILE}")
    with tempfile.TemporaryDirectory(prefix="pdf-mass-compare-ca-") as temp_dir:
        cmd = [podman, "build", "-t", image, "-f", str(CONTAINERFILE)]
        ca_bundle = Path(temp_dir) / "corp-ca-bundle.pem"
        if write_combined_ca_bundle(ca_bundle, default_corp_ca_files()):
            cmd.extend(["--secret", f"id=corp_ca_bundle,src={ca_bundle}"])
        for build_arg in build_args or []:
            cmd.extend(["--build-arg", build_arg])
        cmd.append(str(CONTAINER_DIR))
        print(f"Building benchmark image: {quote_command(cmd)}", file=sys.stderr)
        result = subprocess.run(cmd, cwd=REPO_ROOT, text=True, check=False)
    if result.returncode != 0:
        raise MassCompareError(f"Image build failed with return code {result.returncode}: {quote_command(cmd)}")


def ensure_image_available(args: argparse.Namespace) -> None:
    if args.build_image or args.build_image_only:
        build_image(args.podman, args.image, build_args=args.build_arg)
        return
    if args.skip_image_check:
        return
    if not podman_image_exists(args.podman, args.image):
        raise MassCompareError(
            f"Podman image `{args.image}` does not exist. Build it once with:\n"
            f"  {Path(__file__).name} --build-image-only\n"
            "or run this command with --build-image. Image build may use network; conversions default to --network none."
        )


def parse_engine_command_overrides(values: list[str] | None) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError("--engine-command must be ENGINE=COMMAND_TEMPLATE")
        engine, template = value.split("=", 1)
        engine = engine.strip()
        if engine not in ENGINE_SPECS:
            raise ValueError(f"Unknown engine in --engine-command: {engine}")
        if "{input}" not in template or "{output}" not in template:
            raise ValueError("--engine-command templates must include {input} and {output} placeholders")
        overrides[engine] = template
    return overrides


def host_glibc_runtime_mounts() -> list[tuple[Path, str]]:
    """Read-only host glibc mounts for proprietary glibc binaries inside Alpine.

    The Nutrient pdf-to-markdown Linux binary currently needs glibc symbols that
    Alpine gcompat does not provide.  Mounting the host loader and library dir is
    still container-isolated and avoids baking proprietary binaries or host libs
    into the benchmark image.
    """
    loader = Path("/lib64/ld-linux-x86-64.so.2")
    library_dir = Path("/lib/x86_64-linux-gnu")
    if loader.is_file() and (library_dir / "libc.so.6").is_file():
        return [
            (loader, "/host-ld-linux-x86-64.so.2"),
            (library_dir, "/host-lib/x86_64-linux-gnu"),
        ]
    return []


def build_podman_base_command(
    *,
    args: argparse.Namespace,
    run_dir: Path,
    overrides: dict[str, str],
) -> list[str]:
    uid = os.getuid() if hasattr(os, "getuid") else 0
    gid = os.getgid() if hasattr(os, "getgid") else 0
    cmd = [
        args.podman,
        "run",
        "--rm",
        "--network",
        args.conversion_network,
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "--pids-limit",
        str(args.pids_limit),
        "--user",
        f"{uid}:{gid}",
        "--workdir",
        "/work",
        "--env",
        "HOME=/tmp/mass-compare-home",
        "--env",
        "XDG_CACHE_HOME=/tmp/mass-compare-cache",
        "--env",
        "CUDA_VISIBLE_DEVICES=",
        "--volume",
        f"{run_dir}:/work:rw",
    ]
    if args.memory:
        cmd.extend(["--memory", str(args.memory)])
    if args.cpus:
        cmd.extend(["--cpus", str(args.cpus)])
    if args.pdf_to_markdown_binary:
        binary_path = Path(args.pdf_to_markdown_binary).expanduser().resolve()
        # Mount to a root-level file path so Podman does not depend on creating
        # a missing parent directory for a file mount inside the image.
        cmd.extend(["--volume", f"{binary_path}:/pdf-to-markdown:ro"])
        cmd.extend(["--env", "PDF_TO_MARKDOWN_BINARY=/pdf-to-markdown"])
        glibc_mounts = host_glibc_runtime_mounts()
        for host_path, container_path in glibc_mounts:
            cmd.extend(["--volume", f"{host_path}:{container_path}:ro"])
        if glibc_mounts:
            cmd.extend(["--env", "NUTRIENT_GLIBC_LOADER=/host-ld-linux-x86-64.so.2"])
            cmd.extend(["--env", "NUTRIENT_GLIBC_LIBRARY_PATH=/host-lib/x86_64-linux-gnu"])
    for mount in args.extra_readonly_mount or []:
        host, container = parse_mount(mount)
        cmd.extend(["--volume", f"{host}:{container}:ro"])
    for override_engine, template in overrides.items():
        env_name = "MASS_COMPARE_CMD_" + override_engine.upper().replace("-", "_")
        cmd.extend(["--env", f"{env_name}={template}"])
    return cmd


def build_podman_command(
    *,
    args: argparse.Namespace,
    engine: str,
    run_dir: Path,
    engine_output_dir: Path,
    overrides: dict[str, str],
) -> list[str]:
    cmd = build_podman_base_command(args=args, run_dir=run_dir, overrides=overrides)
    cmd.extend(
        [
            args.image,
            "python3",
            "/work/engine_runner.py",
            "--engine",
            engine,
            "--input",
            "/work/input.pdf",
            "--output-dir",
            f"/work/outputs/{engine}",
        ]
    )
    return cmd


def build_batch_podman_command(
    *,
    args: argparse.Namespace,
    engines: list[str],
    run_dir: Path,
    overrides: dict[str, str],
) -> list[str]:
    cmd = build_podman_base_command(args=args, run_dir=run_dir, overrides=overrides)
    cmd.extend(
        [
            args.image,
            "python3",
            "/work/engine_runner.py",
            "--engines",
            ",".join(engines),
            "--input",
            "/work/input.pdf",
            "--output-root",
            "/work/outputs",
            "--jobs",
            str(args.jobs),
            "--timeout-seconds",
            str(args.timeout_seconds),
        ]
    )
    return cmd


def parse_mount(value: str) -> tuple[Path, str]:
    if ":" not in value:
        raise ValueError("Mounts must be HOST_PATH:CONTAINER_PATH")
    host_raw, container = value.split(":", 1)
    host = Path(host_raw).expanduser().resolve()
    if not host.exists():
        raise FileNotFoundError(f"Mount host path does not exist: {host}")
    if not container.startswith("/"):
        raise ValueError("Container mount target must be absolute")
    return host, container


def container_path_to_host(path: str, run_dir: Path) -> str:
    if path.startswith("/work/"):
        return str(run_dir / path.removeprefix("/work/"))
    if path == "/work":
        return str(run_dir)
    return path


def load_engine_result(result_path: Path, *, run_dir: Path) -> dict[str, Any] | None:
    if not result_path.is_file():
        return None
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and isinstance(data.get("output_path"), str):
        data["output_path"] = container_path_to_host(str(data["output_path"]), run_dir)
    return data if isinstance(data, dict) else None


def build_result_row(
    *,
    engine: str,
    run_dir: Path,
    selected_page_count: int,
    result_payload: dict[str, Any],
    output_dir: Path,
    stdout_path: Path,
    stderr_path: Path,
    result_json_path: Path,
    command: str,
    returncode: int | str | None,
    execution_mode: str,
    podman_wall_seconds: float | None,
    timed_out: bool = False,
) -> dict[str, Any]:
    spec = ENGINE_SPECS[engine]
    output_path = output_dir / spec.output_name
    if not output_path.is_file() and isinstance(result_payload.get("output_path"), str):
        candidate = Path(str(result_payload["output_path"]))
        if candidate.is_file():
            output_path = candidate

    engine_seconds_raw = result_payload.get("seconds_inside_container")
    if engine_seconds_raw in {None, ""}:
        engine_seconds_raw = result_payload.get("child_wall_seconds")
    if engine_seconds_raw in {None, ""}:
        engine_seconds_raw = podman_wall_seconds
    engine_seconds = float(engine_seconds_raw) if engine_seconds_raw not in {None, ""} else 0.0

    payload_status = str(result_payload.get("status", ""))
    success = bool(payload_status == "ok" and output_path.is_file() and output_path.stat().st_size > 0)
    if timed_out or payload_status == "timeout":
        status = "timeout"
    else:
        status = "ok" if success else "failed"
    container_wall = "" if podman_wall_seconds is None else round(float(podman_wall_seconds), 4)
    overhead = ""
    if podman_wall_seconds is not None and engine_seconds_raw not in {None, ""}:
        overhead = round(max(0.0, float(podman_wall_seconds) - engine_seconds), 4)

    return {
        "engine": engine,
        "category": spec.category,
        "status": status,
        "returncode": "" if returncode is None else returncode,
        "seconds": round(engine_seconds, 4),
        "seconds_per_page": round(engine_seconds / selected_page_count, 6) if selected_page_count else "",
        "engine_seconds_inside_container": round(engine_seconds, 4),
        "container_wall_seconds": container_wall,
        "podman_wall_seconds": container_wall,
        "podman_overhead_seconds": overhead,
        "execution_mode": execution_mode,
        "pages": selected_page_count,
        "output_format": spec.output_format,
        "output_path": str(output_path) if output_path.exists() else "",
        "output_bytes": output_path.stat().st_size if output_path.exists() else 0,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "result_json_path": str(result_json_path) if result_json_path.exists() else "",
        "command": command,
        "error": str(result_payload.get("error", "")),
    }


def run_one_engine(
    *,
    args: argparse.Namespace,
    engine: str,
    run_dir: Path,
    overrides: dict[str, str],
    selected_page_count: int,
) -> dict[str, Any]:
    engine_output_dir = ensure_directory(run_dir / "outputs" / engine)
    stdout_path = engine_output_dir / "container.stdout.txt"
    stderr_path = engine_output_dir / "container.stderr.txt"
    result_json_path = engine_output_dir / "result.json"
    cmd = build_podman_command(
        args=args,
        engine=engine,
        run_dir=run_dir,
        engine_output_dir=engine_output_dir,
        overrides=overrides,
    )
    start = time.perf_counter()
    timed_out = False
    returncode: int | None
    try:
        with stdout_path.open("w", encoding="utf-8") as out_handle, stderr_path.open(
            "w", encoding="utf-8"
        ) as err_handle:
            completed = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                text=True,
                stdout=out_handle,
                stderr=err_handle,
                check=False,
                timeout=args.timeout_seconds,
                env=sanitize_env(),
            )
        returncode = completed.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = None
        with stderr_path.open("a", encoding="utf-8") as err_handle:
            err_handle.write(f"\nTimed out after {args.timeout_seconds} seconds.\n")
    wall_seconds = time.perf_counter() - start

    result_payload = load_engine_result(result_json_path, run_dir=run_dir) or {}
    if timed_out and not result_payload:
        result_payload = {"engine": engine, "status": "timeout", "error": f"Timed out after {args.timeout_seconds} seconds"}
    return build_result_row(
        engine=engine,
        run_dir=run_dir,
        selected_page_count=selected_page_count,
        result_payload=result_payload,
        output_dir=engine_output_dir,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        result_json_path=result_json_path,
        command=quote_command(cmd),
        returncode=returncode,
        execution_mode="per-engine",
        podman_wall_seconds=wall_seconds,
        timed_out=timed_out,
    )


def run_batch_engines(
    *,
    args: argparse.Namespace,
    engines: list[str],
    run_dir: Path,
    overrides: dict[str, str],
    selected_page_count: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    outputs_root = ensure_directory(run_dir / "outputs")
    stdout_path = run_dir / "container.batch.stdout.txt"
    stderr_path = run_dir / "container.batch.stderr.txt"
    batch_result_path = outputs_root / "_batch_result.json"
    cmd = build_batch_podman_command(args=args, engines=engines, run_dir=run_dir, overrides=overrides)
    start = time.perf_counter()
    timed_out = False
    returncode: int | None
    batch_timeout = max(args.timeout_seconds + 30, args.timeout_seconds * max(1, len(engines)))
    try:
        with stdout_path.open("w", encoding="utf-8") as out_handle, stderr_path.open(
            "w", encoding="utf-8"
        ) as err_handle:
            completed = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                text=True,
                stdout=out_handle,
                stderr=err_handle,
                check=False,
                timeout=batch_timeout,
                env=sanitize_env(),
            )
        returncode = completed.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = None
        with stderr_path.open("a", encoding="utf-8") as err_handle:
            err_handle.write(f"\nBatch timed out after {batch_timeout} seconds.\n")
    wall_seconds = time.perf_counter() - start

    batch_payload = load_json_if_exists(batch_result_path) or {
        "status": "timeout" if timed_out else "failed",
        "error": "Batch container did not write _batch_result.json",
    }
    rows: list[dict[str, Any]] = []
    command_text = quote_command(cmd)
    for engine in engines:
        engine_output_dir = ensure_directory(outputs_root / engine)
        result_json_path = engine_output_dir / "result.json"
        result_payload = load_engine_result(result_json_path, run_dir=run_dir) or {}
        if not result_payload:
            result_payload = {
                "engine": engine,
                "status": "timeout" if timed_out else "failed",
                "error": "No per-engine result.json was written",
            }
        child_stdout = Path(
            container_path_to_host(
                str(result_payload.get("child_stdout_path", engine_output_dir / "engine-runner.stdout.txt")),
                run_dir,
            )
        )
        child_stderr = Path(
            container_path_to_host(
                str(result_payload.get("child_stderr_path", engine_output_dir / "engine-runner.stderr.txt")),
                run_dir,
            )
        )
        rows.append(
            build_result_row(
                engine=engine,
                run_dir=run_dir,
                selected_page_count=selected_page_count,
                result_payload=result_payload,
                output_dir=engine_output_dir,
                stdout_path=child_stdout,
                stderr_path=child_stderr,
                result_json_path=result_json_path,
                command=command_text,
                returncode=result_payload.get("child_returncode", returncode),
                execution_mode="batch",
                podman_wall_seconds=wall_seconds,
                timed_out=timed_out,
            )
        )
    return rows, batch_payload


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


def build_summary(*, run_dir: Path, rows: list[dict[str, Any]], manifest: dict[str, Any]) -> str:
    lines = [
        "# PDF Mass Comparator Run",
        "",
        f"Run directory: `{run_dir}`",
        f"Generated: `{utc_now_iso()}`",
        f"Source PDF: `{manifest['source_pdf']}`",
        f"Input PDF exposed to containers: `{manifest['input_pdf']}`",
        f"Pages: `{manifest['pages_spec'] or 'all'}` ({manifest['selected_page_count']} page(s))",
        f"Image: `{manifest['image']}`",
        f"Conversion network: `{manifest['conversion_network']}`",
        f"Execution mode: `{manifest.get('execution_mode', 'batch')}`",
        "",
        "## Results",
        "",
        "| Engine | Category | Status | Seconds | Seconds/page | Output | Error/log |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        output = row.get("output_path") or ""
        error = row.get("error") or row.get("stderr_path") or ""
        lines.append(
            f"| `{row['engine']}` | {row['category']} | `{row['status']}` | "
            f"{row['seconds']} | {row['seconds_per_page']} | `{output}` | `{error}` |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- In default `batch` mode, one Podman container is started for the PDF and engines run as child processes inside it; `seconds` and `seconds_per_page` use `engine_seconds_inside_container`, not Podman startup time.",
            "- `container_wall_seconds`/`podman_wall_seconds` record the outer container wall time; `podman_overhead_seconds` is `podman_wall_seconds - engine_seconds_inside_container`.",
            "- Use `--execution-mode per-engine` if you want one Podman container per engine for stronger engine-to-engine isolation.",
            "- Conversion defaults to `--network none`; image building is the explicit network/supply-chain boundary.",
            "- Some Alpine/musl or experimental CLI packages may fail to install or run. Failures are kept as benchmark artifacts rather than aborting the run.",
        ]
    )
    return "\n".join(lines) + "\n"


def list_engines() -> None:
    print("Available engines/groups:")
    for group, engines in ENGINE_GROUPS.items():
        print(f"  {group}: {', '.join(engines)}")
    print("\nEngines:")
    for name in CORE_ENGINES + COMPONENT_ENGINES + LEGACY_EXISTING_ENGINES:
        spec = ENGINE_SPECS[name]
        note = f" ({spec.notes})" if spec.notes else ""
        print(f"  {name:26} {spec.category:10} {spec.output_format:9} {spec.description}{note}")


def run_command(args: argparse.Namespace) -> int:
    if args.list_engines:
        list_engines()
        return 0

    ensure_image_available(args)
    if args.build_image_only:
        print(args.image)
        return 0

    if not args.pdf:
        raise MassCompareError("--pdf is required unless --build-image-only or --list-engines is used")
    source_pdf = Path(args.pdf).expanduser().resolve()
    if not source_pdf.is_file():
        raise FileNotFoundError(f"PDF not found: {source_pdf}")
    if args.pdf_to_markdown_binary and not Path(args.pdf_to_markdown_binary).expanduser().resolve().is_file():
        raise FileNotFoundError(f"--pdf-to-markdown-binary not found: {args.pdf_to_markdown_binary}")

    original_page_count = pdf_page_count(source_pdf)
    requested_pages = validate_pages(parse_page_ranges(args.pages), page_count=original_page_count)
    selected_page_count = len(requested_pages) if requested_pages is not None else original_page_count
    engines = resolve_engines(args.engines)
    overrides = parse_engine_command_overrides(args.engine_command)

    artifact_root = Path(args.artifact_root).expanduser().resolve()
    run_id = args.run_id or build_run_id()
    run_dir = ensure_directory(artifact_root / run_id)
    input_pdf = run_dir / "input.pdf"
    materialize_input_pdf(source_pdf, requested_pages, input_pdf)
    shutil.copy2(CONTAINER_RUNNER, run_dir / "engine_runner.py")

    manifest: dict[str, Any] = {
        "version": 1,
        "generated_at": utc_now_iso(),
        "run_id": run_id,
        "run_dir": str(run_dir),
        "source_pdf": str(source_pdf),
        "source_sha256": sha256_file(source_pdf),
        "source_page_count": original_page_count,
        "pages_spec": args.pages,
        "selected_pages": requested_pages or list(range(1, original_page_count + 1)),
        "selected_page_count": selected_page_count,
        "input_pdf": str(input_pdf),
        "input_sha256": sha256_file(input_pdf),
        "image": args.image,
        "containerfile": str(CONTAINERFILE),
        "conversion_network": args.conversion_network,
        "jobs": args.jobs,
        "execution_mode": args.execution_mode,
        "engines": engines,
        "engine_specs": {engine: ENGINE_SPECS[engine].__dict__ for engine in engines},
        "engine_command_overrides": sorted(overrides),
    }
    write_json(run_dir / "manifest.json", manifest)

    print(f"Run directory: {run_dir}", file=sys.stderr)
    print(f"Engines ({len(engines)}): {', '.join(engines)}", file=sys.stderr)

    rows: list[dict[str, Any]] = []
    if args.execution_mode == "batch":
        rows, batch_payload = run_batch_engines(
            args=args,
            engines=engines,
            run_dir=run_dir,
            overrides=overrides,
            selected_page_count=selected_page_count,
        )
        manifest["batch_result"] = batch_payload
        for row in rows:
            print(f"[{row['status']}] {row['engine']} -> {row.get('output_path') or row.get('error')}", file=sys.stderr)
    else:
        max_workers = min(max(1, int(args.jobs)), len(engines))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    run_one_engine,
                    args=args,
                    engine=engine,
                    run_dir=run_dir,
                    overrides=overrides,
                    selected_page_count=selected_page_count,
                ): engine
                for engine in engines
            }
            for future in as_completed(futures):
                engine = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    spec = ENGINE_SPECS[engine]
                    row = {
                        "engine": engine,
                        "category": spec.category,
                        "status": "failed",
                        "returncode": "",
                        "seconds": "",
                        "seconds_per_page": "",
                        "engine_seconds_inside_container": "",
                        "container_wall_seconds": "",
                        "podman_wall_seconds": "",
                        "podman_overhead_seconds": "",
                        "execution_mode": "per-engine",
                        "pages": selected_page_count,
                        "output_format": spec.output_format,
                        "output_path": "",
                        "output_bytes": 0,
                        "stdout_path": "",
                        "stderr_path": "",
                        "result_json_path": "",
                        "command": "",
                        "error": str(exc),
                    }
                rows.append(row)
                print(f"[{row['status']}] {engine} -> {row.get('output_path') or row.get('error')}", file=sys.stderr)

    order = {engine: index for index, engine in enumerate(engines)}
    rows.sort(key=lambda row: order.get(str(row["engine"]), len(order)))
    fieldnames = [
        "engine",
        "category",
        "status",
        "returncode",
        "seconds",
        "seconds_per_page",
        "engine_seconds_inside_container",
        "container_wall_seconds",
        "podman_wall_seconds",
        "podman_overhead_seconds",
        "execution_mode",
        "pages",
        "output_format",
        "output_path",
        "output_bytes",
        "stdout_path",
        "stderr_path",
        "result_json_path",
        "command",
        "error",
    ]
    write_csv(run_dir / "timings.csv", rows, fieldnames)
    manifest["results"] = rows
    write_json(run_dir / "manifest.json", manifest)
    (run_dir / "summary.md").write_text(build_summary(run_dir=run_dir, rows=rows, manifest=manifest), encoding="utf-8")

    print(run_dir)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run many local PDF parsers/converters against one PDF inside isolated Alpine Podman containers. "
            "Outputs one preferred artifact per engine plus timings and logs."
        )
    )
    parser.add_argument("--pdf", help="Input PDF. The container sees only a copied/subsetted PDF in the run dir.")
    parser.add_argument(
        "--pages",
        default=None,
        help="Optional 1-based pages/ranges, e.g. '1,3-5,9' or '[1,3-5]'. Defaults to all pages.",
    )
    parser.add_argument(
        "--engines",
        default="all",
        help="Comma-separated engines/groups. Groups: all, core, components, legacy, all_with_legacy, existing.",
    )
    parser.add_argument("--list-engines", action="store_true", help="List supported engines/groups and exit.")
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1), help="Parallel engines. In batch mode these run inside one container.")
    parser.add_argument("--timeout-seconds", type=int, default=1800, help="Per-engine timeout. Default: 1800.")
    parser.add_argument(
        "--execution-mode",
        choices=["batch", "per-engine"],
        default="batch",
        help="batch starts one Podman container and runs engines inside it; per-engine starts one container per engine. Default: batch.",
    )

    parser.add_argument("--podman", default=os.environ.get("PODMAN", "podman"))
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--build-image",
        action="store_true",
        help="Build the Alpine benchmark image before running. This is the explicit network/supply-chain boundary.",
    )
    parser.add_argument(
        "--build-image-only",
        action="store_true",
        help="Build the Alpine benchmark image and exit without running conversions.",
    )
    parser.add_argument(
        "--build-arg",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra Podman build arg. The Containerfile already defaults ALPINE_REPOSITORY_URL to the HTTP Alpine mirror; override only if needed.",
    )
    parser.add_argument("--skip-image-check", action="store_true", help="Skip `podman image exists` preflight.")
    parser.add_argument(
        "--conversion-network",
        default="none",
        choices=["none", "slirp4netns", "host", "bridge"],
        help="Podman network mode for conversion containers. Default: none.",
    )
    parser.add_argument("--memory", default=None, help="Optional Podman memory limit, e.g. 8g.")
    parser.add_argument("--cpus", default=None, help="Optional Podman CPU limit.")
    parser.add_argument("--pids-limit", type=int, default=512)
    parser.add_argument(
        "--pdf-to-markdown-binary",
        default=None,
        help="Host Nutrient/PSPDFKit binary to mount read-only for nutrient_pdf_to_markdown.",
    )
    parser.add_argument(
        "--extra-readonly-mount",
        action="append",
        default=[],
        metavar="HOST:CONTAINER",
        help="Extra read-only mount for engine assets. Keep this minimal; do not mount secrets.",
    )
    parser.add_argument(
        "--engine-command",
        action="append",
        default=[],
        metavar="ENGINE=TEMPLATE",
        help="Override an engine command inside the container. Template must include {input} and {output}; may also use {output_dir}.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run_command(args)
    except (MassCompareError, FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
