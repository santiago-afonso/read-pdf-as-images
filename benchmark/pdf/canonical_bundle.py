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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = REPO_ROOT / "benchmark" / "pdf"
CONTAINER_DIR = BENCHMARK_DIR / "canonical_bundle_container"
CONTAINERFILE = CONTAINER_DIR / "Containerfile"
CONTAINER_RUNNER = CONTAINER_DIR / "runner.py"
DEFAULT_IMAGE = "localhost/read-pdf-canonical-bundle:alpine"
DEFAULT_BASE_IMAGE = "localhost/read-pdf-mass-compare:alpine"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "tmp" / "benchmark" / "pdf-canonical-bundles"
DEFAULT_LLM_MODEL = "wbg-gemini-3-flash-preview"
DEFAULT_RENDER_DPI = 160
DEFAULT_SOURCE_DPI = 220
CONTENT_ASSET_MIN_BYTES = 12_000
CONTENT_ASSET_MIN_AREA = 80_000
CONTENT_ASSET_MIN_SIDE = 120


@dataclass(frozen=True)
class EvalDoc:
    doc_type: str
    doc_id: str
    path: Path
    pages: str | None
    description: str


class CanonicalBundleError(RuntimeError):
    pass


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_run_id(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def quote_command(cmd: list[str]) -> str:
    return subprocess.list2cmdline(cmd) if os.name == "nt" else " ".join(sh_quote(part) for part in cmd)


def sh_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def parse_page_ranges(spec: str | None) -> list[int] | None:
    if spec is None or spec.strip().lower() in {"", "all", "*"}:
        return None
    pages: list[int] = []
    seen: set[int] = set()
    cleaned = spec.strip().replace("[", "").replace("]", "").replace(" ", "")
    for part in cleaned.split(","):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ValueError(f"Invalid page token: {part}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start:
            raise ValueError(f"Invalid page range: {part}")
        for page in range(start, end + 1):
            if page not in seen:
                seen.add(page)
                pages.append(page)
    return pages or None


def compact_page_ranges(pages: list[int]) -> str:
    if not pages:
        return ""
    ranges: list[str] = []
    start = pages[0]
    prev = pages[0]
    for page in pages[1:]:
        if page == prev + 1:
            prev = page
            continue
        ranges.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = page
    ranges.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(ranges)


def page_selection_label(result: dict[str, Any]) -> str:
    selected_pages = [int(page) for page in result["selected_pages"]]
    source_page_count = int(result.get("source_page_count") or 0)
    if source_page_count and selected_pages == list(range(1, source_page_count + 1)):
        return f"all ({source_page_count} pages)"
    return compact_page_ranges(selected_pages)


def parse_pdf_page_count(path: Path) -> int:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise CanonicalBundleError("Missing pypdf; run through `uv run`.") from exc
    with path.open("rb") as handle:
        return len(PdfReader(handle).pages)


def materialize_input_pdf(source_pdf: Path, pages: list[int] | None, output_pdf: Path) -> None:
    ensure_dir(output_pdf.parent)
    if pages is None:
        shutil.copy2(source_pdf, output_pdf)
        return
    from pypdf import PdfReader, PdfWriter
    with source_pdf.open("rb") as handle:
        reader = PdfReader(handle)
        writer = PdfWriter()
        for page in pages:
            writer.add_page(reader.pages[page - 1])
        with output_pdf.open("wb") as out:
            writer.write(out)


def image_tokens_gpt5_high_detail(width: int, height: int) -> int:
    import math

    max_side = max(width, height)
    scale_1 = 2048.0 / max_side if max_side > 2048 else 1.0
    width_1 = width * scale_1
    height_1 = height * scale_1
    min_side = min(width_1, height_1)
    scale_2 = 768.0 / min_side
    width_2 = width_1 * scale_2
    height_2 = height_1 * scale_2
    tiles = math.ceil(width_2 / 512.0) * math.ceil(height_2 / 512.0)
    return 70 + 140 * tiles


def gemini3_image_tokens_for_dpi(dpi: int) -> tuple[str, int]:
    if dpi <= 150:
        return "MEDIA_RESOLUTION_LOW", 280
    if dpi < 240:
        return "MEDIA_RESOLUTION_MEDIUM", 560
    return "MEDIA_RESOLUTION_HIGH", 1120


def image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
    except ModuleNotFoundError as exc:
        raise CanonicalBundleError("Missing Pillow; run with `uv run --with pillow`.") from exc
    with Image.open(path) as image:
        return image.size


def page_dimensions_px(pdf_path: Path, page: int, dpi: int) -> tuple[int, int]:
    import fitz  # type: ignore[import-not-found]

    doc = fitz.open(str(pdf_path))
    try:
        rect = doc[page - 1].rect
        return round((rect.width / 72.0) * dpi), round((rect.height / 72.0) * dpi)
    finally:
        doc.close()


def estimate_text_tokens(text: str) -> tuple[int, str]:
    try:
        import tiktoken  # type: ignore[import-not-found]
        enc = tiktoken.get_encoding("o200k_base")
        return len(enc.encode(text)), "tiktoken:o200k_base"
    except Exception:
        return max(1, round(len(text) / 4)), "chars_div_4"


def estimate_file_text_tokens(path: Path) -> tuple[int, str]:
    if not path.is_file():
        return 0, "missing"
    return estimate_text_tokens(path.read_text(encoding="utf-8", errors="replace"))


def estimate_image_tokens_for_paths(paths: list[Path], *, dpi: int) -> dict[str, Any]:
    gpt_total = 0
    gemini_resolution, gemini_per_image = gemini3_image_tokens_for_dpi(dpi)
    records: list[dict[str, Any]] = []
    for path in paths:
        width, height = image_size(path)
        gpt_tokens = image_tokens_gpt5_high_detail(width, height)
        gpt_total += gpt_tokens
        records.append({"path": str(path), "width": width, "height": height, "gpt5_tokens": gpt_tokens})
    return {
        "image_count": len(paths),
        "gpt5_image_tokens": gpt_total,
        "gemini3_resolution": gemini_resolution,
        "gemini3_image_tokens": len(paths) * gemini_per_image,
        "gemini3_tokens_per_image": gemini_per_image,
        "images": records,
    }


def is_likely_full_page_image(width: int, height: int) -> bool:
    aspect = min(width, height) / max(width, height)
    return min(width, height) >= 1200 and 0.65 <= aspect <= 0.82


def classify_content_asset(path: Path) -> dict[str, Any]:
    bytes_ = path.stat().st_size
    width, height = image_size(path)
    area = width * height
    if bytes_ < CONTENT_ASSET_MIN_BYTES:
        keep = False
        reason = "small_file_likely_logo_or_stationery"
    elif min(width, height) < CONTENT_ASSET_MIN_SIDE:
        keep = False
        reason = "thin_or_small_likely_stationery"
    elif area < CONTENT_ASSET_MIN_AREA:
        keep = False
        reason = "small_area_likely_decorative"
    elif is_likely_full_page_image(width, height):
        keep = False
        reason = "full_page_like_image_not_default_asset"
    else:
        keep = True
        reason = "content_asset_candidate"
    return {
        "path": str(path),
        "bytes": bytes_,
        "width": width,
        "height": height,
        "keep": keep,
        "reason": reason,
    }


def select_content_asset_images(manifest: dict[str, Any]) -> dict[str, Any]:
    selected: list[Path] = []
    selected_records: list[dict[str, Any]] = []
    rejected_records: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for image in manifest.get("pymupdf", {}).get("images", []):
        path_value = image.get("path")
        if not path_value:
            continue
        path = Path(path_value)
        if not path.is_file():
            continue
        record = {"source": "pymupdf_embedded_image", **classify_content_asset(path)}
        if not record["keep"]:
            rejected_records.append(record)
            continue
        digest = sha256_file(path)
        record["sha256"] = digest
        if digest in seen_hashes:
            record["keep"] = False
            record["reason"] = "duplicate_content_asset"
            rejected_records.append(record)
            continue
        seen_hashes.add(digest)
        selected.append(path)
        selected_records.append(record)
    return {"paths": selected, "selected": selected_records, "rejected": rejected_records}


def estimate_pdf_as_images_tokens(pdf_path: Path, pages: list[int], *, dpi: int) -> dict[str, Any]:
    gpt_total = 0
    records: list[dict[str, Any]] = []
    for page in pages:
        width, height = page_dimensions_px(pdf_path, page, dpi)
        gpt_tokens = image_tokens_gpt5_high_detail(width, height)
        gpt_total += gpt_tokens
        records.append({"page": page, "width": width, "height": height, "gpt5_tokens": gpt_tokens})
    gemini_resolution, gemini_per_image = gemini3_image_tokens_for_dpi(dpi)
    return {
        "page_count": len(pages),
        "gpt5_image_tokens": gpt_total,
        "gemini3_resolution": gemini_resolution,
        "gemini3_image_tokens": len(pages) * gemini_per_image,
        "gemini3_tokens_per_image": gemini_per_image,
        "pages": records,
    }


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
    chunks = [source.read_text(encoding="utf-8", errors="ignore").strip() for source in sources]
    chunks = [chunk for chunk in chunks if chunk]
    if not chunks:
        return False
    path.write_text("\n".join(chunks) + "\n", encoding="utf-8")
    return True


def build_image(args: argparse.Namespace) -> None:
    with tempfile.TemporaryDirectory(prefix="pdf-canonical-ca-") as temp_dir:
        cmd = [args.podman, "build", "-t", args.image, "-f", str(CONTAINERFILE)]
        ca_bundle = Path(temp_dir) / "corp-ca-bundle.pem"
        if write_combined_ca_bundle(ca_bundle, default_corp_ca_files()):
            cmd.extend(["--secret", f"id=corp_ca_bundle,src={ca_bundle}"])
        cmd.append(str(CONTAINER_DIR))
        print(f"Building canonical bundle image: {quote_command(cmd)}", file=sys.stderr)
        result = subprocess.run(cmd, cwd=REPO_ROOT, text=True, check=False)
    if result.returncode != 0:
        raise CanonicalBundleError(f"Image build failed: {quote_command(cmd)}")


def podman_image_exists(podman: str, image: str) -> bool:
    return subprocess.run([podman, "image", "exists", image], check=False).returncode == 0


def ensure_image(args: argparse.Namespace) -> None:
    if args.build_image:
        build_image(args)
        return
    if not podman_image_exists(args.podman, args.image):
        raise CanonicalBundleError(
            f"Podman image `{args.image}` does not exist. Build it with:\n"
            f"  scripts/pdf-canonical-bundle build-image\n"
            f"The image derives from `{DEFAULT_BASE_IMAGE}`; build the mass comparator image first if needed."
        )


def container_path_to_host(value: str, run_dir: Path) -> str:
    if value == "/work":
        return str(run_dir)
    if value.startswith("/work/"):
        return str(run_dir / value.removeprefix("/work/"))
    return value


def translate_container_paths(value: Any, run_dir: Path) -> Any:
    if isinstance(value, str):
        return container_path_to_host(value, run_dir)
    if isinstance(value, list):
        return [translate_container_paths(item, run_dir) for item in value]
    if isinstance(value, dict):
        return {key: translate_container_paths(item, run_dir) for key, item in value.items()}
    return value


def build_podman_command(args: argparse.Namespace, *, run_dir: Path, pages: str | None, render_dpi: int) -> list[str]:
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
        "HOME=/tmp/canonical-bundle-home",
        "--env",
        "XDG_CACHE_HOME=/tmp/canonical-bundle-cache",
        "--env",
        "CUDA_VISIBLE_DEVICES=",
        "--env",
        "PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "--volume",
        f"{run_dir}:/work:rw",
    ]
    if args.memory:
        cmd.extend(["--memory", str(args.memory)])
    if args.cpus:
        cmd.extend(["--cpus", str(args.cpus)])
    cmd.extend(
        [
            args.image,
            "/opt/venv/bin/python",
            "/work/runner.py",
            "--input",
            "/work/input.pdf",
            "--output-dir",
            "/work/bundle",
            "--render-dpi",
            str(render_dpi),
        ]
    )
    if pages:
        cmd.extend(["--pages", pages])
    return cmd


def run_container_bundle(
    args: argparse.Namespace,
    *,
    source_pdf: Path,
    doc_id: str,
    pages_spec: str | None,
    artifact_root: Path,
    run_id: str,
) -> dict[str, Any]:
    ensure_image(args)
    page_count = parse_pdf_page_count(source_pdf)
    selected_pages = parse_page_ranges(pages_spec) or list(range(1, page_count + 1))
    for page in selected_pages:
        if page < 1 or page > page_count:
            raise ValueError(f"Page {page} outside PDF page range 1-{page_count}")

    run_dir = ensure_dir(artifact_root / run_id / doc_id)
    input_pdf = run_dir / "input.pdf"
    materialize_input_pdf(source_pdf, selected_pages, input_pdf)
    shutil.copy2(CONTAINER_RUNNER, run_dir / "runner.py")
    pre_manifest = {
        "version": 1,
        "generated_at": utc_now_iso(),
        "doc_id": doc_id,
        "source_pdf": str(source_pdf),
        "source_sha256": sha256_file(source_pdf),
        "source_page_count": page_count,
        "selected_pages": selected_pages,
        "input_pdf": str(input_pdf),
        "input_sha256": sha256_file(input_pdf),
        "image": args.image,
        "render_dpi": args.render_dpi,
    }
    write_json(run_dir / "host-manifest.json", pre_manifest)

    cmd = build_podman_command(args, run_dir=run_dir, pages=None, render_dpi=args.render_dpi)
    stdout_path = run_dir / "container.stdout.txt"
    stderr_path = run_dir / "container.stderr.txt"
    started = time.perf_counter()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        completed = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            text=True,
            stdout=stdout,
            stderr=stderr,
            timeout=args.timeout_seconds,
            check=False,
        )
    wall_seconds = time.perf_counter() - started
    bundle_dir = run_dir / "bundle"
    bundle_manifest_path = bundle_dir / "manifest.json"
    try:
        bundle_manifest = translate_container_paths(json.loads(bundle_manifest_path.read_text(encoding="utf-8")), run_dir)
    except Exception:
        bundle_manifest = {}
    result = {
        **pre_manifest,
        "run_dir": str(run_dir),
        "bundle_dir": str(bundle_dir),
        "bundle_manifest_path": str(bundle_manifest_path) if bundle_manifest_path.is_file() else "",
        "container_stdout_path": str(stdout_path),
        "container_stderr_path": str(stderr_path),
        "container_returncode": completed.returncode,
        "container_seconds": round(wall_seconds, 4),
        "container_command": quote_command(cmd),
        "status": "ok" if completed.returncode == 0 and bundle_manifest_path.is_file() else "failed",
        "bundle_manifest": bundle_manifest,
    }
    write_json(run_dir / "result.json", result)
    return result


def bundle_payload_image_paths(bundle_dir: Path, manifest: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for page in manifest.get("pymupdf", {}).get("pages", []):
        render = page.get("page_render_path")
        if render and Path(render).is_file():
            paths.append(Path(render))
    for image in manifest.get("pymupdf", {}).get("images", []):
        path = image.get("path")
        if path and Path(path).is_file():
            paths.append(Path(path))
    # De-duplicate while preserving order.
    out: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


def compute_token_summary(
    result: dict[str, Any],
    *,
    source_dpi: int,
    bundle_dpi: int,
    cleanup_path: Path | None = None,
) -> dict[str, Any]:
    bundle_dir = Path(result["bundle_dir"])
    manifest = result.get("bundle_manifest") or {}
    payload_path = Path(str(manifest.get("canonical_payload_path") or bundle_dir / "canonical_payload.md"))
    payload_text_tokens, text_method = estimate_file_text_tokens(payload_path)
    audit_image_paths = bundle_payload_image_paths(bundle_dir, manifest)
    audit_images = estimate_image_tokens_for_paths(audit_image_paths, dpi=bundle_dpi)
    content_asset_selection = select_content_asset_images(manifest)
    content_asset_images = estimate_image_tokens_for_paths(content_asset_selection["paths"], dpi=bundle_dpi)
    source_images = estimate_pdf_as_images_tokens(
        Path(result["source_pdf"]),
        [int(page) for page in result["selected_pages"]],
        dpi=source_dpi,
    )
    cleanup_tokens = 0
    cleanup_method = "not_run"
    if cleanup_path and cleanup_path.is_file():
        cleanup_tokens, cleanup_method = estimate_file_text_tokens(cleanup_path)
    return {
        "payload_path": str(payload_path),
        "payload_text_tokens": payload_text_tokens,
        "payload_text_token_method": text_method,
        "content_asset_image_count": content_asset_images["image_count"],
        "content_asset_gpt5_image_tokens": content_asset_images["gpt5_image_tokens"],
        "content_asset_gemini3_image_tokens": content_asset_images["gemini3_image_tokens"],
        "content_asset_selection": {
            "selected": content_asset_selection["selected"],
            "rejected": content_asset_selection["rejected"],
            "heuristics": {
                "min_bytes": CONTENT_ASSET_MIN_BYTES,
                "min_area": CONTENT_ASSET_MIN_AREA,
                "min_side": CONTENT_ASSET_MIN_SIDE,
                "excludes_full_page_like_images": True,
            },
        },
        "audit_bundle_image_count": audit_images["image_count"],
        "audit_bundle_gpt5_image_tokens": audit_images["gpt5_image_tokens"],
        "audit_bundle_gemini3_image_tokens": audit_images["gemini3_image_tokens"],
        "audit_page_render_count": len(manifest.get("pymupdf", {}).get("pages", [])),
        "canonical_bundle_gpt5_total": payload_text_tokens + content_asset_images["gpt5_image_tokens"],
        "canonical_bundle_gemini3_total": payload_text_tokens + content_asset_images["gemini3_image_tokens"],
        "stored_audit_bundle_gpt5_total": payload_text_tokens + audit_images["gpt5_image_tokens"],
        "stored_audit_bundle_gemini3_total": payload_text_tokens + audit_images["gemini3_image_tokens"],
        "cleanup_path": str(cleanup_path) if cleanup_path else "",
        "cleanup_text_tokens": cleanup_tokens,
        "cleanup_text_token_method": cleanup_method,
        "canonical_cleanup_gpt5_total": cleanup_tokens + content_asset_images["gpt5_image_tokens"] if cleanup_tokens else "",
        "canonical_cleanup_gemini3_total": cleanup_tokens + content_asset_images["gemini3_image_tokens"] if cleanup_tokens else "",
        "pdf_images_gpt5_total": source_images["gpt5_image_tokens"],
        "pdf_images_gemini3_total": source_images["gemini3_image_tokens"],
        "source_image_pages": source_images["page_count"],
        "source_image_dpi": source_dpi,
        "bundle_image_dpi": bundle_dpi,
        "content_asset_image_details": content_asset_images,
        "audit_bundle_image_details": audit_images,
        "bundle_image_count": content_asset_images["image_count"],
        "bundle_gpt5_image_tokens": content_asset_images["gpt5_image_tokens"],
        "bundle_gemini3_image_tokens": content_asset_images["gemini3_image_tokens"],
        "bundle_image_details": content_asset_images,
        "source_image_details": source_images,
    }


def find_llm_binary() -> str | None:
    candidates = [
        Path("/home/safonso/.local/share/uv/tools/llm/bin/llm"),
        Path("/home/safonso/.local/share/pipx/venvs/llm/bin/llm"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    resolved = shutil.which("llm")
    return resolved


def run_cleanup(args: argparse.Namespace, result: dict[str, Any]) -> Path | None:
    if args.cleanup == "never":
        return None
    llm = find_llm_binary()
    if not llm:
        return None
    bundle_dir = Path(result["bundle_dir"])
    manifest = result.get("bundle_manifest") or {}
    payload_path = Path(str(manifest.get("canonical_payload_path") or bundle_dir / "canonical_payload.md"))
    if not payload_path.is_file():
        return None
    prompt = (
        "Clean up this PDF canonical extraction packet for downstream LLM ingestion. "
        "Preserve all numeric values, table headers, page numbers, and image path references. "
        "Do not invent content. Convert awkward raw extraction text into concise sections and "
        "readable Markdown/HTML table summaries where possible.\n\n"
        + payload_path.read_text(encoding="utf-8", errors="replace")
    )
    output_path = bundle_dir / "canonical_payload.cleaned.gemini3flash.md"
    cmd = [
        llm,
        "prompt",
        "-m",
        args.cleanup_model,
        "--no-stream",
        "-o",
        "thinking_level",
        "low",
        "-o",
        "max_tokens",
        str(args.cleanup_max_tokens),
    ]
    if args.cleanup_auth_context:
        cmd.extend(["-o", "auth_context", args.cleanup_auth_context])
    completed = subprocess.run(cmd, input=prompt, text=True, capture_output=True, check=False, timeout=args.cleanup_timeout)
    (bundle_dir / "cleanup.stderr.txt").write_text(completed.stderr or "", encoding="utf-8")
    if completed.returncode != 0 or not completed.stdout.strip():
        if args.cleanup == "always":
            raise CanonicalBundleError(f"Cleanup failed: {(completed.stderr or '')[-2000:]}")
        return None
    output_path.write_text(completed.stdout, encoding="utf-8")
    return output_path


def discover_default_eval_docs(*, full_documents: bool = False) -> list[EvalDoc]:
    page_slice_docs = [
        EvalDoc(
            "DPF",
            "dpf_ecuador_policy_matrix",
            Path("/home/safonso/DRM_Policy_Platform/artifacts/dpf_markdown_failure_deck/assets/ecuador_dpf2_page_058_only.pdf"),
            "1",
            "Ecuador DPF2 policy/results matrix extract from the failure-mode deck.",
        ),
        EvalDoc(
            "IPF",
            "ipf_p161969_pad",
            Path("/home/safonso/DRM_Policy_Platform/tmp/ipf/P161969 PAD.pdf"),
            "1,10",
            "Local IPF/PAD sample; page 1 plus a later project-detail page.",
        ),
        EvalDoc(
            "Article IV",
            "article_iv_ethiopia_2025",
            Path("/home/safonso/imf-article-ivs/_downloads/ETH-2025-1934-7685-002.2025.issue-188-en.pdf"),
            "1,45",
            "Ethiopia 2025 Article IV / ECF report; cover plus an analytical page.",
        ),
    ]
    full_docs = [
        EvalDoc(
            "DPF",
            "dpf_ecuador_dpf2_full",
            Path(
                "/home/safonso/DRM_Policy_Platform/Output/lakehouse_artifacts/databricks_imagebank/33935830/"
                "33935830-Ecuador - Second Green and Resilient Recovery Development Policy Financing.pdf"
            ),
            None,
            "Full Ecuador Second Green and Resilient Recovery DPF / EGARR DPF-2 document.",
        ),
        EvalDoc(
            "IPF",
            "ipf_p161969_pad_full",
            Path("/home/safonso/DRM_Policy_Platform/tmp/ipf/P161969 PAD.pdf"),
            None,
            "Full local IPF/PAD sample.",
        ),
        EvalDoc(
            "Article IV",
            "article_iv_ethiopia_2025_full",
            Path("/home/safonso/imf-article-ivs/_downloads/ETH-2025-1934-7685-002.2025.issue-188-en.pdf"),
            None,
            "Full Ethiopia 2025 Article IV / ECF report.",
        ),
    ]
    docs = full_docs if full_documents else page_slice_docs
    return [doc for doc in docs if doc.path.is_file()]


def rows_for_comparison(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        tokens = result["token_summary"]
        rows.append(
            {
                "document_type": result["doc_type"],
                "document_id": result["doc_id"],
                "pages": page_selection_label(result),
                "page_count": len(result["selected_pages"]),
                "canonical_bundle_openai_gpt5_tokens": tokens["canonical_bundle_gpt5_total"],
                "canonical_bundle_gemini3_tokens": tokens["canonical_bundle_gemini3_total"],
                "canonical_bundle_plus_cleanup_openai_gpt5_tokens": tokens["canonical_cleanup_gpt5_total"],
                "canonical_bundle_plus_cleanup_gemini3_tokens": tokens["canonical_cleanup_gemini3_total"],
                "stored_audit_bundle_openai_gpt5_tokens": tokens["stored_audit_bundle_gpt5_total"],
                "stored_audit_bundle_gemini3_tokens": tokens["stored_audit_bundle_gemini3_total"],
                "pdf_as_images_openai_gpt5_tokens": tokens["pdf_images_gpt5_total"],
                "pdf_as_images_gemini3_tokens": tokens["pdf_images_gemini3_total"],
                "content_asset_image_count": tokens["content_asset_image_count"],
                "audit_bundle_image_count": tokens["audit_bundle_image_count"],
                "audit_page_render_count": tokens["audit_page_render_count"],
                "payload_text_tokens": tokens["payload_text_tokens"],
                "cleanup_text_tokens": tokens["cleanup_text_tokens"],
                "status": result["status"],
                "run_dir": result["run_dir"],
            }
        )
    return rows


def comparison_fieldnames() -> list[str]:
    return [
        "document_type",
        "document_id",
        "pages",
        "page_count",
        "canonical_bundle_openai_gpt5_tokens",
        "canonical_bundle_gemini3_tokens",
        "canonical_bundle_plus_cleanup_openai_gpt5_tokens",
        "canonical_bundle_plus_cleanup_gemini3_tokens",
        "stored_audit_bundle_openai_gpt5_tokens",
        "stored_audit_bundle_gemini3_tokens",
        "pdf_as_images_openai_gpt5_tokens",
        "pdf_as_images_gemini3_tokens",
        "content_asset_image_count",
        "audit_bundle_image_count",
        "audit_page_render_count",
        "payload_text_tokens",
        "cleanup_text_tokens",
        "status",
        "run_dir",
    ]


def build_evaluation_report(eval_dir: Path, results: list[dict[str, Any]]) -> str:
    rows = rows_for_comparison(results)
    is_full_document = all(
        result.get("source_page_count")
        and [int(page) for page in result["selected_pages"]]
        == list(range(1, int(result["source_page_count"]) + 1))
        for result in results
    )
    scope = "full-document overhead" if is_full_document else "bounded page-slice"
    lines = [
        "# Canonical PDF Bundle Evaluation",
        "",
        f"Generated: `{utc_now_iso()}`",
        f"Evaluation directory: `{eval_dir}`",
        f"Evaluation scope: `{scope}`",
        "",
        "## Token Comparison",
        "",
        "| Document type | Pages | Canonical LLM payload OpenAI | Canonical LLM payload Gemini | Canonical + cleanup OpenAI | Canonical + cleanup Gemini | PDF as images OpenAI | PDF as images Gemini | OpenAI payload/image ratio | Gemini payload/image ratio |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        cleanup_openai = row["canonical_bundle_plus_cleanup_openai_gpt5_tokens"] or "not run"
        cleanup_gemini = row["canonical_bundle_plus_cleanup_gemini3_tokens"] or "not run"
        openai_ratio = row["canonical_bundle_openai_gpt5_tokens"] / row["pdf_as_images_openai_gpt5_tokens"]
        gemini_ratio = row["canonical_bundle_gemini3_tokens"] / row["pdf_as_images_gemini3_tokens"]
        lines.append(
            f"| {row['document_type']} | `{row['pages']}` | "
            f"{row['canonical_bundle_openai_gpt5_tokens']} | "
            f"{row['canonical_bundle_gemini3_tokens']} | "
            f"{cleanup_openai} | "
            f"{cleanup_gemini} | "
            f"{row['pdf_as_images_openai_gpt5_tokens']} | "
            f"{row['pdf_as_images_gemini3_tokens']} | "
            f"{openai_ratio:.2f}x | "
            f"{gemini_ratio:.2f}x |"
        )
    lines.extend(
        [
            "",
            "## Intended Payload Composition",
            "",
            "| Document type | Pages | Structured text/table tokens | Content asset images | Content asset tokens OpenAI | Content asset tokens Gemini |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in rows:
        openai_image_tokens = row["canonical_bundle_openai_gpt5_tokens"] - row["payload_text_tokens"]
        gemini_image_tokens = row["canonical_bundle_gemini3_tokens"] - row["payload_text_tokens"]
        lines.append(
            f"| {row['document_type']} | `{row['pages']}` | "
            f"{row['payload_text_tokens']} | "
            f"{row['content_asset_image_count']} | "
            f"{openai_image_tokens} | "
            f"{gemini_image_tokens} |"
        )
    lines.extend(
        [
            "",
            "## Stored Bundle / Audit Overhead",
            "",
            "| Document type | Pages | Stored bundle with page renders OpenAI | Stored bundle with page renders Gemini | Full page renders counted | Audit image count |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['document_type']} | `{row['pages']}` | "
            f"{row['stored_audit_bundle_openai_gpt5_tokens']} | "
            f"{row['stored_audit_bundle_gemini3_tokens']} | "
            f"{row['audit_page_render_count']} | "
            f"{row['audit_bundle_image_count']} |"
        )
    lines.extend(
        [
            "",
            "The table above reports OpenAI `gpt-5(detail=high)`-style image tokens and Gemini 3 Flash image tokens, plus text tokens estimated with `tiktoken` when available. "
            "Both image-token estimates use the same algorithms as `read-pdf`; Gemini uses the matching DPI-to-media-resolution heuristic.",
            "",
            "The canonical LLM payload excludes full-page renders by default. It counts structured page text/table payload plus native embedded images that pass simple content-asset heuristics: minimum byte size, dimensions, area, and not full-page-like. Page renders stay in the stored bundle as optional visual ground truth for targeted fallback, audit, or table/chart pages selected by a retrieval step.",
            "",
            "## Quality Notes",
            "",
            "- The canonical bundle always includes PyMuPDF page renders, so layout ground truth is preserved even when table JSON is imperfect.",
            "- Docling and OpenDataLoader outputs are kept as raw engine artifacts when available; PyMuPDF tables are normalized to JSON, Markdown, and HTML.",
            "- The bundle is page-centered: every page record links text, render image, extracted images, and table artifacts.",
            "- Full-PDF-as-images is simpler and can be lower-token for full-document ingestion, but it gives up searchable text and structured table objects.",
            "- For full-document overhead, the stored/audit bundle with every page render is intentionally reported separately because sending it wholesale is redundant with full-PDF-as-images.",
            "- The cleanup pass is a summarizing/normalizing pass, not a lossless artifact. It can reduce tokens for text-heavy packets, but can increase tokens when it reconstructs a complex table more explicitly.",
            "",
            "## Recommendation",
            "",
            "First best: use the canonical bundle for table/image-heavy ingestion, attaching page renders only for pages with tables, charts, figures, or layout risk. "
            "Use Docling/OpenDataLoader/PyMuPDF table artifacts as structure and PyMuPDF renders as visual truth.",
            "",
            "Second best if full multimodal ingestion is blocked: send the text/HTML/Markdown portion of the canonical bundle plus table crops or selected page renders for high-risk pages. "
            "This preserves most table semantics while keeping image payloads bounded.",
        ]
    )
    return "\n".join(lines) + "\n"


def command_build_image(args: argparse.Namespace) -> int:
    build_image(args)
    print(args.image)
    return 0


def command_run(args: argparse.Namespace) -> int:
    source_pdf = Path(args.pdf).expanduser().resolve()
    if not source_pdf.is_file():
        raise FileNotFoundError(f"PDF not found: {source_pdf}")
    artifact_root = Path(args.artifact_root).expanduser().resolve()
    run_id = args.run_id or build_run_id()
    result = run_container_bundle(
        args,
        source_pdf=source_pdf,
        doc_id=args.doc_id or sanitize_id(source_pdf.stem),
        pages_spec=args.pages,
        artifact_root=artifact_root,
        run_id=run_id,
    )
    cleanup_path = run_cleanup(args, result)
    result["token_summary"] = compute_token_summary(
        result,
        source_dpi=args.source_image_dpi,
        bundle_dpi=args.render_dpi,
        cleanup_path=cleanup_path,
    )
    write_json(Path(result["run_dir"]) / "result.json", result)
    print(result["run_dir"])
    return 0


def command_evaluate(args: argparse.Namespace) -> int:
    artifact_root = Path(args.artifact_root).expanduser().resolve()
    run_id = args.run_id or build_run_id()
    eval_dir = ensure_dir(artifact_root / run_id)
    docs = discover_default_eval_docs(full_documents=args.full_documents)
    if not docs:
        raise CanonicalBundleError("No default evaluation PDFs found")
    results: list[dict[str, Any]] = []
    for doc in docs:
        print(f"[{doc.doc_type}] {doc.doc_id} pages={doc.pages or 'all'}", file=sys.stderr)
        result = run_container_bundle(
            args,
            source_pdf=doc.path,
            doc_id=doc.doc_id,
            pages_spec=doc.pages,
            artifact_root=artifact_root,
            run_id=run_id,
        )
        result["doc_type"] = doc.doc_type
        result["description"] = doc.description
        cleanup_path = run_cleanup(args, result)
        result["token_summary"] = compute_token_summary(
            result,
            source_dpi=args.source_image_dpi,
            bundle_dpi=args.render_dpi,
            cleanup_path=cleanup_path,
        )
        write_json(Path(result["run_dir"]) / "result.json", result)
        results.append(result)
    rows = rows_for_comparison(results)
    write_csv(
        eval_dir / "token-comparison.csv",
        rows,
        comparison_fieldnames(),
    )
    write_json(
        eval_dir / "evaluation-manifest.json",
        {
            "version": 1,
            "generated_at": utc_now_iso(),
            "full_documents": bool(args.full_documents),
            "results": results,
        },
    )
    (eval_dir / "evaluation-report.md").write_text(build_evaluation_report(eval_dir, results), encoding="utf-8")
    print(eval_dir)
    return 0


def sanitize_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()[:80] or "pdf"


def add_container_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--podman", default=os.environ.get("PODMAN", "podman"))
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--build-image", action="store_true", help="Build the canonical container image before running.")
    parser.add_argument("--conversion-network", default="none", choices=["none", "slirp4netns", "host", "bridge"])
    parser.add_argument("--memory", default=None)
    parser.add_argument("--cpus", default=None)
    parser.add_argument("--pids-limit", type=int, default=512)
    parser.add_argument("--timeout-seconds", type=int, default=1800)


def add_common_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--render-dpi", type=int, default=DEFAULT_RENDER_DPI)
    parser.add_argument("--source-image-dpi", type=int, default=DEFAULT_SOURCE_DPI)
    parser.add_argument("--cleanup", choices=["auto", "always", "never"], default="auto")
    parser.add_argument("--cleanup-model", default=DEFAULT_LLM_MODEL)
    parser.add_argument("--cleanup-auth-context", default="")
    parser.add_argument("--cleanup-max-tokens", type=int, default=4096)
    parser.add_argument("--cleanup-timeout", type=int, default=240)
    add_container_args(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build page-centered canonical PDF bundles for LLM ingestion.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser_ = subparsers.add_parser("build-image", help="Build the derived canonical bundle container image.")
    add_container_args(build_parser_)
    build_parser_.set_defaults(func=command_build_image)

    run_parser = subparsers.add_parser("run", help="Run the canonical bundle pipeline for one PDF.")
    run_parser.add_argument("--pdf", required=True)
    run_parser.add_argument("--doc-id", default=None)
    run_parser.add_argument("--pages", default=None)
    add_common_run_args(run_parser)
    run_parser.set_defaults(func=command_run)

    eval_parser = subparsers.add_parser("evaluate", help="Run DPF/IPF/Article IV evaluation.")
    add_common_run_args(eval_parser)
    eval_parser.add_argument(
        "--full-documents",
        action="store_true",
        help="Evaluate full source PDFs instead of the default bounded page-slice smoke pages.",
    )
    eval_parser.set_defaults(func=command_evaluate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (CanonicalBundleError, FileNotFoundError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
