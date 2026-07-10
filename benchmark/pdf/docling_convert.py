#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import contextlib
import csv
import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "tmp" / "docling-convert"
SAMPLE94_SOURCE_DIR = Path("/home/safonso/IDA21/2026m06-run01/sample94_strict_pdf/source_pdfs")
P174008_SOURCE_PDF = Path(
    "/home/safonso/IDA21/2026m06-run01/sample94_strict_pdf/source_pdfs/"
    "005_dpl_dpo_dpf_main_P174008_RUNV3CTX_P174008_001.pdf"
)
P174008_REFERENCE_HTML = Path(
    "/home/safonso/IDA21/2026m06-run01/sample94_strict_pdf/opendataloader_native_clean_novlm/"
    "005_dpl_dpo_dpf_main_P174008_RUNV3CTX_P174008_001/opendataloader/"
    "005_dpl_dpo_dpf_main_P174008_RUNV3CTX_P174008_001.html"
)
P174008_DEFAULT_PAGES = "33-35"

PRACTICAL_SMOKE_PROFILES = [
    "standard-tableformer",
    "vlm-granite-docling",
]
FULL_COMPARISON_PROFILES = [
    *PRACTICAL_SMOKE_PROFILES,
    "vlm-granite-vision-small",
    "vlm-granite-vision-large",
]
PROFILE_ALIASES = {
    "approved-smoke": PRACTICAL_SMOKE_PROFILES,
    "practical-smoke": PRACTICAL_SMOKE_PROFILES,
    "granite-docling-smoke": PRACTICAL_SMOKE_PROFILES,
    "two-lanes": PRACTICAL_SMOKE_PROFILES,
    "all": FULL_COMPARISON_PROFILES,
    "with-large": FULL_COMPARISON_PROFILES,
    "standard": ["standard-tableformer"],
    "tableformer": ["standard-tableformer"],
    "granite-docling": ["vlm-granite-docling"],
    "granite-vision": ["vlm-granite-vision-small", "vlm-granite-vision-large"],
}
VALID_PROFILES = {
    "standard-tableformer",
    "standard-granite-table",
    "vlm-granite-docling",
    "vlm-granite-vision-small",
    "vlm-granite-vision-large",
}


class DoclingConvertError(RuntimeError):
    pass


YEAR_VALUE_RE = re.compile(r"\b(?:19|20)\d{2}\s*:")
INDICATOR_RE = re.compile(r"\b(?:results?\s+)?indicator\s*#?\s*\d+(?:\.\d+)?", re.IGNORECASE)
POLICY_MATRIX_TERMS = [
    "policy and results matrix",
    "policy matrix",
    "results matrix",
    "prior action",
    "prior actions",
    "trigger",
    "triggers",
    "results indicator",
    "indicator",
    "baseline",
    "target",
    "pillar",
]
TABLE_CAPTION_RE = re.compile(
    r"^\s*(?:text\s+)?table\s+[A-Za-z]?\d+(?:[.\-]\d+)*(?:\s*[:.]|\s+)",
    re.IGNORECASE,
)
TABLE_LIKE_ANNEX_RE = re.compile(
    r"^\s*annex\s+[IVXLC]+\.\s+(?:public\s+)?debt\s+sustainability\s+analysis\b",
    re.IGNORECASE,
)
MAJOR_SECTION_HEADING_RE = re.compile(
    r"^\s*\d+(?:\.\d+)?\s+[A-Z][A-Z0-9 ,;:/()&'\u2019-]{8,}$"
)
CAPTION_FALLBACK_MAX_CONTINUATION_PAGES = 6
DEFAULT_LLM_HARD_LIMIT_MB = 200.0
DEFAULT_LLM_TRANSPORT_HEADROOM_MB = 20.0
DEFAULT_LLM_PROMPT_SCHEMA_HEADROOM_MB = 24.0
DEFAULT_LLM_BATCH_MODEL = "gpt-5.1-batch"
DEFAULT_LLM_BATCH_ENDPOINT = "/v1/chat/completions"
DEFAULT_LLM_DOCUMENT_PROMPT = (
    "Use the following converted document and its source-evidence images to complete the requested extraction."
)
PDF_POINTS_PER_INCH = 72.0
CM_PER_INCH = 2.54
PERCEPTUAL_FURNITURE_MAX_DISTANCE = 6
TABLE_CONTINUATION_TERMS = [
    "prior action",
    "indicative trigger",
    "results",
    "reform program",
    "program update",
    "pillar",
    "baseline",
    "target",
    "update:",
    "indicator",
]


@dataclass(frozen=True)
class PageSelection:
    spec: str | None
    start: int
    end: int

    @property
    def docling_range(self) -> tuple[int, int]:
        return (self.start, self.end)

    @property
    def page_count(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True)
class BatchInput:
    batch_index: int
    document_id: str
    input_pdf: Path


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_run_id(now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    return current.strftime("%Y%m%d-%H%M%S")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_text(path: Path, text: str) -> Path:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")
    return path


def write_json(path: Path, payload: Any) -> Path:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def parse_page_selection(spec: str | None) -> PageSelection | None:
    if spec is None or not spec.strip() or spec.strip().lower() in {"all", "*"}:
        return None
    cleaned = spec.strip().replace(" ", "").replace("\u2013", "-").replace("\u2014", "-")
    parts = [part for part in cleaned.split(",") if part]
    if not parts:
        return None
    pages: list[int] = []
    for part in parts:
        if "-" in part:
            raw_start, raw_end = part.split("-", 1)
            if not raw_start.isdigit() or not raw_end.isdigit():
                raise ValueError(f"Invalid page range token: {part}")
            start, end = int(raw_start), int(raw_end)
            if end < start:
                raise ValueError(f"Invalid page range `{part}`: end precedes start")
            pages.extend(range(start, end + 1))
        else:
            if not part.isdigit():
                raise ValueError(f"Invalid page token: {part}")
            pages.append(int(part))
    if any(page < 1 for page in pages):
        raise ValueError("Page numbers are 1-based and must be positive")
    unique = sorted(set(pages))
    expected = list(range(unique[0], unique[-1] + 1))
    if unique != expected:
        raise ValueError(
            "Docling page_range accepts one contiguous range. "
            f"Use a contiguous range instead of `{spec}`."
        )
    return PageSelection(spec=spec, start=unique[0], end=unique[-1])


def expand_profiles(raw: str | list[str] | None) -> list[str]:
    if raw is None:
        raw_values = ["approved-smoke"]
    elif isinstance(raw, str):
        raw_values = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        raw_values = []
        for item in raw:
            raw_values.extend(part.strip() for part in item.split(",") if part.strip())
    profiles: list[str] = []
    for value in raw_values:
        expanded = PROFILE_ALIASES.get(value, [value])
        for profile in expanded:
            if profile not in VALID_PROFILES:
                raise ValueError(
                    f"Unknown profile `{profile}`. Valid profiles: {', '.join(sorted(VALID_PROFILES))}; "
                    f"aliases: {', '.join(sorted(PROFILE_ALIASES))}."
                )
            if profile not in profiles:
                profiles.append(profile)
    return profiles


def image_ref_mode(value: str):
    from docling_core.types.doc import ImageRefMode

    mapping = {
        "placeholder": ImageRefMode.PLACEHOLDER,
        "embedded": ImageRefMode.EMBEDDED,
        "referenced": ImageRefMode.REFERENCED,
    }
    return mapping[value]


def profile_conceptual_lane(profile: str) -> str:
    if profile.startswith("standard-"):
        return "standard-table-structure"
    if profile == "vlm-granite-docling":
        return "vlm-granite-docling"
    if profile.startswith("vlm-granite-vision"):
        return "vlm-granite-vision"
    return profile


def collect_gpu_diagnostics() -> dict[str, Any]:
    diagnostics: dict[str, Any] = {"collected_at_utc": utc_now_iso()}
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        diagnostics["nvidia_smi_returncode"] = result.returncode
        diagnostics["nvidia_smi_stderr"] = result.stderr.strip()
        if result.returncode == 0:
            gpus = []
            for line in result.stdout.splitlines():
                parts = [part.strip() for part in line.split(",")]
                if len(parts) >= 5:
                    gpus.append(
                        {
                            "name": parts[0],
                            "memory_total_mib": int(parts[1]),
                            "memory_used_mib": int(parts[2]),
                            "memory_free_mib": int(parts[3]),
                            "driver_version": parts[4],
                        }
                    )
            diagnostics["gpus"] = gpus
    except FileNotFoundError:
        diagnostics["nvidia_smi_error"] = "nvidia-smi not found"

    try:
        import torch

        diagnostics["torch_version"] = getattr(torch, "__version__", None)
        diagnostics["torch_cuda_available"] = bool(torch.cuda.is_available())
        diagnostics["torch_cuda_version"] = getattr(torch.version, "cuda", None)
        if torch.cuda.is_available():
            diagnostics["torch_device_count"] = torch.cuda.device_count()
            devices = []
            for idx in range(torch.cuda.device_count()):
                with contextlib.suppress(Exception):
                    free_bytes, total_bytes = torch.cuda.mem_get_info(idx)
                    props = torch.cuda.get_device_properties(idx)
                    devices.append(
                        {
                            "index": idx,
                            "name": torch.cuda.get_device_name(idx),
                            "total_memory_bytes": int(props.total_memory),
                            "mem_get_info_free_bytes": int(free_bytes),
                            "mem_get_info_total_bytes": int(total_bytes),
                        }
                    )
            diagnostics["torch_devices"] = devices
    except Exception as exc:  # pragma: no cover - environment dependent
        diagnostics["torch_error"] = f"{type(exc).__name__}: {exc}"
    return diagnostics


def require_cuda_if_requested(device: str, *, allow_no_cuda: bool) -> None:
    if device != "cuda":
        return
    diagnostics = collect_gpu_diagnostics()
    if diagnostics.get("torch_cuda_available") is True:
        return
    if allow_no_cuda:
        return
    raise DoclingConvertError(
        "CUDA was requested but torch cannot see CUDA. "
        f"Diagnostics: {json.dumps(diagnostics, ensure_ascii=False)[:2000]}"
    )


def accelerator_options_for(device: str):
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions

    mapping = {
        "auto": AcceleratorDevice.AUTO,
        "cpu": AcceleratorDevice.CPU,
        "cuda": AcceleratorDevice.CUDA,
        "mps": AcceleratorDevice.MPS,
        "xpu": AcceleratorDevice.XPU,
    }
    return AcceleratorOptions(device=mapping[device])


def materialize_hf_repo(repo_id: str, *, revision: str | None, artifacts_root: Path) -> Path:
    """Download/copy a HF repo into a real-file Docling artifacts tree.

    The current Granite Vision 4.1 Transformers path can fail when Transformers
    resolves remote-code imports through the default Hugging Face symlink cache:
    it follows a symlink into `.../blobs/` and then looks for sibling Python
    files there.  Docling's VLM loader accepts an artifacts root containing a
    `<org>--<model>` directory, so keep one materialized copy for these models.
    """

    from huggingface_hub import snapshot_download

    model_dir = ensure_dir(artifacts_root / repo_id.replace("/", "--"))
    snapshot_download(repo_id=repo_id, revision=revision, local_dir=model_dir)
    return artifacts_root


def maybe_import_version(package: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(package)
    except Exception:
        return None


def build_standard_pipeline_options(profile: str, args: argparse.Namespace):
    from docling.datamodel.pipeline_options import (
        EasyOcrOptions,
        GraniteVisionTableStructureOptions,
        PdfPipelineOptions,
        RapidOcrOptions,
        TableFormerMode,
        TableStructureOptions,
        granite_picture_description,
    )
    from docling.datamodel.picture_classification_options import DocumentPictureClassifierOptions

    if profile == "standard-granite-table":
        table_options: Any = GraniteVisionTableStructureOptions()
    else:
        table_options = TableStructureOptions(mode=TableFormerMode.ACCURATE, do_cell_matching=True)

    kwargs: dict[str, Any] = {
        "accelerator_options": accelerator_options_for(args.device),
        "do_ocr": not args.no_ocr,
        "do_table_structure": True,
        "table_structure_options": table_options,
        "do_code_enrichment": bool(args.enrich_code),
        "do_formula_enrichment": bool(args.enrich_formula),
        "do_picture_classification": not args.no_picture_classification,
        "do_picture_description": not args.no_picture_description,
        "do_chart_extraction": False,
        "generate_picture_images": True,
        "generate_table_images": True,
        "generate_page_images": bool(args.generate_page_images),
        "images_scale": args.images_scale,
        "ocr_batch_size": args.ocr_batch_size,
        "layout_batch_size": args.layout_batch_size,
        "table_batch_size": args.table_batch_size,
    }
    if not args.no_picture_description:
        kwargs["picture_description_options"] = granite_picture_description
    if not args.no_picture_classification:
        classifier_options = DocumentPictureClassifierOptions.from_preset("document_figure_classifier_v2")
        if not args.compile_picture_classifier:
            classifier_options = classifier_options.model_copy(
                update={
                    "engine_options": classifier_options.engine_options.model_copy(
                        update={"compile_model": False}
                    )
                }
            )
        kwargs["picture_classification_options"] = classifier_options
    if args.ocr_engine == "easyocr":
        kwargs["ocr_options"] = EasyOcrOptions(
            lang=args.ocr_lang or ["fr", "de", "es", "en"],
            use_gpu=True if args.device == "cuda" else None,
        )
    if args.ocr_engine == "rapidocr-torch":
        # Docling's GPU documentation identifies RapidOCR with torch backend as the known GPU OCR path.
        kwargs["ocr_options"] = RapidOcrOptions(backend="torch", lang=args.ocr_lang or ["english"])
    return PdfPipelineOptions(**kwargs)


def build_vlm_pipeline_options(profile: str, args: argparse.Namespace):
    from docling.datamodel.picture_classification_options import DocumentPictureClassifierOptions
    from docling.datamodel.pipeline_options import (
        GRANITE_VISION_4_1_TRANSFORMERS,
        VlmConvertOptions,
        VlmPipelineOptions,
        granite_picture_description,
    )
    from docling.datamodel.pipeline_options_vlm_model import ResponseFormat

    if profile == "vlm-granite-docling":
        vlm_options = VlmConvertOptions.from_preset("granite_docling")
        with contextlib.suppress(Exception):
            vlm_options = vlm_options.model_copy(
                update={
                    "batch_size": args.vlm_batch_size,
                    "force_backend_text": bool(args.force_backend_text),
                }
            )
    elif profile == "vlm-granite-vision-small":
        vlm_options = VlmConvertOptions.from_preset("granite_vision")
        with contextlib.suppress(Exception):
            vlm_options = vlm_options.model_copy(update={"batch_size": args.vlm_batch_size})
    elif profile == "vlm-granite-vision-large":
        transformers_version = maybe_import_version("transformers")
        transformers_major = int(transformers_version.split(".", 1)[0]) if transformers_version else None
        if transformers_major is not None and transformers_major >= 5:
            raise DoclingConvertError(
                "vlm-granite-vision-large currently requires transformers<5 in this environment. "
                "Transformers 5.x fails in Granite Vision 4.1 remote-code generation "
                "(`create_causal_mask(... cache_position=...)`). Retry with: "
                "PDF_DOCLING_TRANSFORMERS_SPEC='transformers<5' scripts/pdf-docling-convert ..."
            )
        # Docling 2.111 exposes Granite Vision 4.1 as an InlineVlmOptions preset.
        # Turn on 8-bit quantization explicitly and keep the same document-conversion
        # prompt/format as the small Granite Vision preset.
        vlm_options = GRANITE_VISION_4_1_TRANSFORMERS.model_copy(
            update={
                "prompt": "Convert this page to markdown. Do not miss any text and only output the bare markdown!",
                "response_format": ResponseFormat.MARKDOWN,
                "max_new_tokens": args.max_new_tokens,
                "temperature": 0.0,
                "quantized": True,
                "load_in_8bit": True,
            }
        )
    else:  # pragma: no cover - guarded by caller
        raise ValueError(f"Unsupported VLM profile: {profile}")

    kwargs: dict[str, Any] = {
        "accelerator_options": accelerator_options_for(args.device),
        "vlm_options": vlm_options,
        "generate_page_images": True,
        "generate_picture_images": True,
        "images_scale": args.images_scale,
        "force_backend_text": bool(args.force_backend_text),
        "do_picture_classification": not args.no_picture_classification,
        "do_picture_description": not args.no_picture_description,
        "do_chart_extraction": False,
    }
    if profile == "vlm-granite-vision-large" and not args.no_materialize_large_model:
        artifacts_root = Path(args.hf_artifacts_root).expanduser().resolve()
        kwargs["artifacts_path"] = materialize_hf_repo(
            vlm_options.repo_id,
            revision=vlm_options.revision,
            artifacts_root=artifacts_root,
        )
        if not args.no_picture_description:
            materialize_hf_repo(
                granite_picture_description.repo_id,
                revision=None,
                artifacts_root=artifacts_root,
            )
        if not args.no_picture_classification:
            materialize_hf_repo(
                "docling-project/DocumentFigureClassifier-v2.5",
                revision="main",
                artifacts_root=artifacts_root,
            )
    if not args.no_picture_description:
        kwargs["picture_description_options"] = granite_picture_description
    if not args.no_picture_classification:
        classifier_options = DocumentPictureClassifierOptions.from_preset("document_figure_classifier_v2")
        if not args.compile_picture_classifier:
            classifier_options = classifier_options.model_copy(
                update={
                    "engine_options": classifier_options.engine_options.model_copy(
                        update={"compile_model": False}
                    )
                }
            )
        kwargs["picture_classification_options"] = classifier_options
    return VlmPipelineOptions(**kwargs)


def build_converter(profile: str, args: argparse.Namespace):
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption

    if profile.startswith("standard-"):
        pipeline_options = build_standard_pipeline_options(profile, args)
        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )

    from docling.pipeline.vlm_pipeline import VlmPipeline

    pipeline_options = build_vlm_pipeline_options(profile, args)
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_cls=VlmPipeline,
                pipeline_options=pipeline_options,
            )
        }
    )


def token_count(text: str) -> int | None:
    try:
        import tiktoken

        try:
            enc = tiktoken.get_encoding("o200k_base")
        except Exception:
            enc = tiktoken.encoding_for_model("gpt-4o")
        return len(enc.encode(text))
    except Exception:
        return None


def summarize_file(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists() and path.is_file():
        info["bytes"] = path.stat().st_size
        if path.suffix.lower() in {".md", ".html", ".json", ".txt", ".doctags", ".doclang"}:
            with contextlib.suppress(Exception):
                text = path.read_text(encoding="utf-8", errors="replace")
                info["chars"] = len(text)
                info["tokens_o200k_base"] = token_count(text)
    return info


def read_text_if_exists(path: Path | None) -> str:
    if path is None or not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def read_csv_rows(path: Path | None) -> list[list[str]]:
    if path is None or not path.exists() or not path.is_file():
        return []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        return [[cell or "" for cell in row] for row in csv.reader(handle)]


def normalize_cell_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def repeated_label_ratio(cells: list[str]) -> float:
    labels = [normalize_cell_text(cell) for cell in cells if normalize_cell_text(cell)]
    if not labels:
        return 0.0
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    repeated = sum(count for count in counts.values() if count >= 2)
    return repeated / len(labels)


def count_year_value_pairs(text: str) -> int:
    return len(YEAR_VALUE_RE.findall(text or ""))


def count_indicator_mentions(text: str) -> int:
    return len(INDICATOR_RE.findall(text or ""))


def table_structure_metrics(element: Any) -> dict[str, Any]:
    """Summarize Docling's native cell grid before DataFrame/HTML flattening.

    Exported HTML and CSV can conceal exactly the cases for which visual evidence
    is required: merged cells, deleted/absent grid slots, and overlapping cells.
    Inspect the native offsets first so those risks survive serialization.
    """

    data = _field_value(element, "data")
    declared_rows = int(_field_value(data, "num_rows", 0) or 0)
    declared_cols = int(_field_value(data, "num_cols", 0) or 0)
    raw_cells = _field_value(data, "table_cells", []) or []
    cells = [_plain_value(cell) for cell in raw_cells]
    coverage = [[0 for _ in range(declared_cols)] for _ in range(declared_rows)]
    merged_cell_count = 0
    invalid_offset_count = 0

    for cell in cells:
        try:
            row_start = int(_field_value(cell, "start_row_offset_idx"))
            row_end = int(_field_value(cell, "end_row_offset_idx"))
            col_start = int(_field_value(cell, "start_col_offset_idx"))
            col_end = int(_field_value(cell, "end_col_offset_idx"))
        except (TypeError, ValueError):
            invalid_offset_count += 1
            continue
        row_span = int(_field_value(cell, "row_span", 0) or max(row_end - row_start, 1))
        col_span = int(_field_value(cell, "col_span", 0) or max(col_end - col_start, 1))
        if row_span > 1 or col_span > 1:
            merged_cell_count += 1
        if (
            row_start < 0
            or col_start < 0
            or row_end <= row_start
            or col_end <= col_start
            or row_end > declared_rows
            or col_end > declared_cols
        ):
            invalid_offset_count += 1
            continue
        for row_idx in range(row_start, row_end):
            for col_idx in range(col_start, col_end):
                coverage[row_idx][col_idx] += 1

    grid_hole_count = sum(value == 0 for row in coverage for value in row)
    grid_overlap_count = sum(value > 1 for row in coverage for value in row)
    return {
        "declared_rows": declared_rows,
        "declared_cols": declared_cols,
        "cell_count": len(cells),
        "merged_cell_count": merged_cell_count,
        "grid_hole_count": grid_hole_count,
        "grid_overlap_count": grid_overlap_count,
        "invalid_offset_count": invalid_offset_count,
    }


def analyze_table_complexity(
    rows: list[list[str]],
    *,
    markdown_text: str = "",
    html_text: str = "",
    native_structure: dict[str, Any] | None = None,
    provenance_pages: set[int] | None = None,
    threshold: int = 4,
) -> dict[str, Any]:
    """Score whether table structure is risky enough to require bitmap evidence.

    This intentionally scores extraction risk, not aesthetic complexity.  A table
    should be rendered as an original bitmap when there is material risk that a
    Markdown/HTML table misassigns labels, row groups, years, units, or values.
    """

    normalized_rows = [[cell or "" for cell in row] for row in rows if row]
    row_widths = [len(row) for row in normalized_rows]
    n_rows = len(normalized_rows)
    n_cols = max((len(row) for row in normalized_rows), default=0)
    padded_rows = [row + [""] * (n_cols - len(row)) for row in normalized_rows]
    all_cells = [cell for row in padded_rows for cell in row]
    nonempty_cells = [cell for cell in all_cells if normalize_cell_text(cell)]
    all_text = "\n".join(nonempty_cells + [markdown_text, html_text])

    flags: list[str] = []
    score = 0

    def add(flag: str, points: int) -> None:
        nonlocal score
        if flag not in flags:
            flags.append(flag)
            score += points

    if n_cols >= 5:
        add("many_columns", 2)
    if n_rows >= 10:
        add("many_rows", 1)
    if row_widths and len(set(row_widths)) > 1:
        add("ragged_exported_rows", 4)
    first_nonempty_cell = next((cell.strip() for row in padded_rows for cell in row if cell.strip()), "")
    if first_nonempty_cell and (
        first_nonempty_cell[0].islower()
        or first_nonempty_cell.startswith((",", ";", ")", "and ", "or ", "(including"))
    ):
        add("possible_page_continuation", 2)

    header_cells = [cell for row in padded_rows[:2] for cell in row]
    if repeated_label_ratio(header_cells) >= 0.25:
        add("duplicate_headers", 2)
    if header_cells:
        blank_header_ratio = sum(1 for cell in header_cells if not normalize_cell_text(cell)) / len(header_cells)
        if blank_header_ratio >= 0.15:
            add("blank_headers", 1)

    if nonempty_cells:
        long_cell_ratio = sum(1 for cell in nonempty_cells if len(cell) > 250) / len(nonempty_cells)
        if long_cell_ratio >= 0.10:
            add("long_prose_cells", 2)
        empty_cell_ratio = sum(1 for cell in all_cells if not normalize_cell_text(cell)) / max(len(all_cells), 1)
        if empty_cell_ratio >= 0.25:
            add("many_empty_cells", 1)

    if any(count_year_value_pairs(cell) >= 2 for cell in nonempty_cells):
        add("multiple_year_value_pairs_in_one_cell", 4)
    if any(count_indicator_mentions(cell) >= 2 for cell in nonempty_cells):
        add("multiple_indicators_in_one_cell", 4)

    semantic_hits = sum(1 for term in POLICY_MATRIX_TERMS if term in all_text.lower())
    if semantic_hits >= 3:
        add("policy_matrix_semantics", 3)

    if re.search(r"\browspan\s*=", html_text, re.IGNORECASE) or re.search(r"\bcolspan\s*=", html_text, re.IGNORECASE):
        add("html_spanning_cells", 4)

    native_structure = native_structure or {}
    if int(native_structure.get("merged_cell_count") or 0) > 0:
        add("native_merged_cells", 4)
    if int(native_structure.get("grid_hole_count") or 0) > 0:
        add("native_grid_holes", 4)
    if int(native_structure.get("grid_overlap_count") or 0) > 0:
        add("native_grid_overlaps", 4)
    if int(native_structure.get("invalid_offset_count") or 0) > 0:
        add("native_invalid_cell_offsets", 4)
    if len(provenance_pages or set()) > 1:
        add("native_multipage_table", 4)

    if any(normalize_cell_text(cell).startswith(("pillar ", "component ", "axis ")) for cell in nonempty_cells):
        add("section_or_pillar_rows", 1)

    critical_flags = {
        "html_spanning_cells",
        "native_grid_holes",
        "native_grid_overlaps",
        "native_invalid_cell_offsets",
        "native_merged_cells",
        "native_multipage_table",
        "ragged_exported_rows",
        "multiple_year_value_pairs_in_one_cell",
        "multiple_indicators_in_one_cell",
    }
    has_critical_flag = bool(critical_flags.intersection(flags))
    bitmap_required = score >= threshold or has_critical_flag
    if has_critical_flag or score >= max(7, threshold + 3):
        label = "complex"
        structure_confidence = "low"
    elif bitmap_required:
        label = "possibly_complex"
        structure_confidence = "medium"
    else:
        label = "simple"
        structure_confidence = "high"

    return {
        "label": label,
        "score": score,
        "threshold": threshold,
        "bitmap_required": bitmap_required,
        "trust_structured_extraction": not bitmap_required,
        "structure_confidence": structure_confidence,
        "flags": flags,
        "metrics": {
            "n_rows": n_rows,
            "n_cols": n_cols,
            "nonempty_cell_count": len(nonempty_cells),
            "semantic_term_hits": semantic_hits,
            "native_structure": native_structure,
            "provenance_pages": sorted(provenance_pages or set()),
        },
    }


def add_complexity_flag(
    table_record: dict[str, Any],
    flag: str,
    *,
    points: int = 4,
) -> None:
    complexity = table_record.setdefault("complexity", {})
    flags = list(complexity.get("flags") or [])
    if flag not in flags:
        flags.append(flag)
        complexity["score"] = int(complexity.get("score") or 0) + points
    complexity["flags"] = flags
    complexity["bitmap_required"] = True
    complexity["trust_structured_extraction"] = False
    complexity["structure_confidence"] = "low"
    complexity["label"] = "complex"


def header_label_set(rows: list[list[str]]) -> set[str]:
    labels: set[str] = set()
    for row in rows[:2]:
        for cell in row:
            normalized = normalize_cell_text(cell)
            if normalized:
                labels.add(normalized)
    return labels


def mark_cross_page_table_sequences(table_records: list[dict[str, Any]]) -> None:
    """Conservatively bitmap adjacent tables that look like one continued table."""

    linked_indexes: list[tuple[int, int]] = []
    for current_index, (previous, current) in enumerate(
        zip(table_records, table_records[1:], strict=False),
        start=1,
    ):
        previous_pages = page_set_from_records([previous])
        current_pages = page_set_from_records([current])
        if len(previous_pages) != 1 or len(current_pages) != 1:
            continue
        previous_page = next(iter(previous_pages))
        current_page = next(iter(current_pages))
        if current_page != previous_page + 1:
            continue
        previous_rows = read_csv_rows(Path((previous.get("csv") or {}).get("path", "")))
        current_rows = read_csv_rows(Path((current.get("csv") or {}).get("path", "")))
        previous_cols = int((previous.get("complexity") or {}).get("metrics", {}).get("n_cols") or 0)
        current_cols = int((current.get("complexity") or {}).get("metrics", {}).get("n_cols") or 0)
        if previous_cols < 2 or previous_cols != current_cols:
            continue
        previous_headers = header_label_set(previous_rows)
        current_headers = header_label_set(current_rows)
        shared = previous_headers.intersection(current_headers)
        union = previous_headers.union(current_headers)
        header_similarity = len(shared) / max(len(union), 1)
        previous_flags = set((previous.get("complexity") or {}).get("flags") or [])
        current_flags = set((current.get("complexity") or {}).get("flags") or [])
        continuation_evidence = (
            header_similarity >= 0.35
            or "possible_page_continuation" in current_flags
            or (
                previous_cols >= 5
                and (
                    "blank_headers" in current_flags
                    or "cross_page_table_sequence" in previous_flags
                )
            )
        )
        if not continuation_evidence:
            continue
        add_complexity_flag(previous, "cross_page_table_sequence")
        add_complexity_flag(current, "cross_page_table_sequence")
        linked_indexes.append((current_index - 1, current_index))

    if not linked_indexes:
        return

    parent = list(range(len(table_records)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left, right in linked_indexes:
        union(left, right)

    groups: dict[int, list[int]] = {}
    for left, right in linked_indexes:
        for index in (left, right):
            groups.setdefault(find(index), []).append(index)
    for indexes in groups.values():
        ordered_indexes = sorted(set(indexes))
        pages = sorted(page_set_from_records([table_records[index] for index in ordered_indexes]))
        first_record = table_records[ordered_indexes[0]]
        first_table_index = int(first_record.get("index") or ordered_indexes[0] + 1)
        group_id = (
            f"table-continuation-p{pages[0]:04d}-p{pages[-1]:04d}-"
            f"t{first_table_index:04d}"
        )
        for sequence_index, record_index in enumerate(ordered_indexes, start=1):
            record = table_records[record_index]
            record["continuation_group_id"] = group_id
            record["continuation_sequence_index"] = sequence_index
            record["continuation_page_range"] = {"start": pages[0], "end": pages[-1]}


def table_should_render_as_bitmap(table_record: dict[str, Any], *, policy: str) -> bool:
    if policy == "all":
        return True
    if policy == "none":
        return False
    return bool((table_record.get("complexity") or {}).get("bitmap_required"))


def relative_posix_path(path: str | Path, *, start: Path) -> str:
    return Path(os.path.relpath(Path(path), start=start)).as_posix()


class LeanHtmlFragmentParser(HTMLParser):
    """Keep semantic document/table HTML and strip styling/diagnostic attrs."""

    allowed_tags = {
        "a",
        "b",
        "blockquote",
        "br",
        "caption",
        "code",
        "dd",
        "dl",
        "dt",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "i",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "strong",
        "sub",
        "sup",
        "table",
        "td",
        "th",
        "tr",
        "u",
        "ul",
    }
    void_tags = {"br", "img"}
    semantic_attrs = {
        "a": {"href"},
        "img": {"src", "alt"},
        "td": {"rowspan", "colspan"},
        "th": {"rowspan", "colspan"},
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.open_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_l = tag.lower()
        if tag_l not in self.allowed_tags:
            return
        kept_attrs = []
        allowed_attrs = self.semantic_attrs.get(tag_l, set())
        for name, value in attrs:
            name_l = name.lower()
            if name_l not in allowed_attrs or value is None:
                continue
            kept_attrs.append(f'{name_l}="{html.escape(value, quote=True)}"')
        attr_text = "" if not kept_attrs else " " + " ".join(kept_attrs)
        self.parts.append(f"<{tag_l}{attr_text}>")
        if tag_l not in self.void_tags:
            self.open_tags.append(tag_l)

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if tag_l not in self.allowed_tags or tag_l in self.void_tags:
            return
        if tag_l in self.open_tags:
            while self.open_tags:
                open_tag = self.open_tags.pop()
                self.parts.append(f"</{open_tag}>")
                if open_tag == tag_l:
                    break

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def close_fragment(self) -> str:
        while self.open_tags:
            self.parts.append(f"</{self.open_tags.pop()}>")
        return "".join(self.parts).strip()


def lean_html_fragment(fragment: str) -> str:
    parser = LeanHtmlFragmentParser()
    parser.feed(fragment or "")
    return parser.close_fragment()


def lean_html_text_block(text: str) -> str:
    """Escape text for the LLM HTML export without paragraph boilerplate.

    The LLM-oriented HTML is consumed as source, not as a browser facsimile.
    Keeping plain text as text nodes makes it much closer to Markdown while
    preserving semantic HTML only where it matters: tables, images, links, and
    inline emphasis/code that Docling already emitted in fragments.
    """

    return html.escape(text.strip(), quote=False)


def lean_image_tag(src: str, alt: str) -> str:
    return f'<img src="{html.escape(src, quote=True)}" alt="{html.escape(alt, quote=True)}">'


def lean_html_document(body_parts: list[str]) -> str:
    body = "\n\n".join(part for part in body_parts if part).strip()
    return f"<!doctype html>\n<html><body>\n{body}\n</body></html>\n"


def png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
        if len(header) >= 24 and header[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")
    except Exception:
        return None
    return None


def _plain_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        with contextlib.suppress(Exception):
            return value.model_dump()
    return value


def _field_value(value: Any, field: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(field, default)
    return getattr(value, field, default)


def provenance_to_dict(prov: Any) -> dict[str, Any] | None:
    page_no = _field_value(prov, "page_no")
    bbox = _plain_value(_field_value(prov, "bbox"))
    if page_no is None or not isinstance(bbox, dict):
        return None
    bbox_record = {
        "l": bbox.get("l"),
        "t": bbox.get("t"),
        "r": bbox.get("r"),
        "b": bbox.get("b"),
        "coord_origin": str(bbox.get("coord_origin") or "BOTTOMLEFT"),
    }
    return {"page_no": int(page_no), "bbox": bbox_record}


def item_provenance_records(element: Any) -> list[dict[str, Any]]:
    records = []
    for prov in getattr(element, "prov", []) or []:
        record = provenance_to_dict(prov)
        if record is not None:
            records.append(record)
    return records


def page_size_points(document: Any, page_no: int) -> dict[str, float] | None:
    pages = getattr(document, "pages", {}) or {}
    page = pages.get(page_no) or pages.get(str(page_no))
    if page is None:
        return None
    size = _field_value(page, "size")
    width = _field_value(size, "width")
    height = _field_value(size, "height")
    if width is None or height is None:
        return None
    return {"width": float(width), "height": float(height)}


def page_width_crop_box(
    *,
    page_size: dict[str, float],
    bbox: dict[str, Any],
    image_size: tuple[int, int],
    padding_pt: float = 18.0,
) -> tuple[int, int, int, int]:
    """Return a pixel crop that trusts bbox vertically but not horizontally.

    Docling's table bbox can omit right-side columns on complex policy matrices.
    For LLM-facing table bitmaps, use the source page's full rendered width and
    the table bbox's vertical band, expanded by padding.  This preserves the
    visible table columns while avoiding a full-page image when possible.
    """

    page_height = float(page_size["height"])
    image_width, image_height = image_size
    top_pt = float(bbox["t"])
    bottom_pt = float(bbox["b"])
    origin = str(bbox.get("coord_origin") or "BOTTOMLEFT").upper()

    if origin.endswith("TOPLEFT"):
        y_top_pt = max(0.0, min(top_pt, bottom_pt) - padding_pt)
        y_bottom_pt = min(page_height, max(top_pt, bottom_pt) + padding_pt)
        y_top_px = int(max(0, round(y_top_pt / page_height * image_height)))
        y_bottom_px = int(min(image_height, round(y_bottom_pt / page_height * image_height)))
    else:
        expanded_top_pt = min(page_height, max(top_pt, bottom_pt) + padding_pt)
        expanded_bottom_pt = max(0.0, min(top_pt, bottom_pt) - padding_pt)
        y_top_px = int(max(0, round((page_height - expanded_top_pt) / page_height * image_height)))
        y_bottom_px = int(min(image_height, round((page_height - expanded_bottom_pt) / page_height * image_height)))

    if y_bottom_px <= y_top_px:
        y_top_px, y_bottom_px = 0, image_height
    return (0, y_top_px, image_width, y_bottom_px)


def bbox_crop_box(
    *,
    page_size: dict[str, float],
    bbox: dict[str, Any],
    image_size: tuple[int, int],
    padding_pt: float = 8.0,
) -> tuple[int, int, int, int] | None:
    """Return a pixel crop for the exact Docling bbox, expanded by padding.

    This is intentionally different from `page_width_crop_box`: formulas are
    usually narrow, inline/centered objects, so a page-width crop wastes tokens
    and obscures what Docling actually detected.  Tables remain page-width by
    default because Docling can under-estimate their horizontal extent.
    """

    page_width = float(page_size["width"])
    page_height = float(page_size["height"])
    image_width, image_height = image_size
    edges = bbox_edges(bbox)
    if edges is None:
        return None
    left_pt, right_pt, low_pt, high_pt = edges
    origin = str(bbox.get("coord_origin") or "BOTTOMLEFT").upper()

    x_left_pt = max(0.0, left_pt - padding_pt)
    x_right_pt = min(page_width, right_pt + padding_pt)
    if origin.endswith("TOPLEFT"):
        y_top_pt = max(0.0, low_pt - padding_pt)
        y_bottom_pt = min(page_height, high_pt + padding_pt)
        y_top_px = int(max(0, round(y_top_pt / page_height * image_height)))
        y_bottom_px = int(min(image_height, round(y_bottom_pt / page_height * image_height)))
    else:
        expanded_top_pt = min(page_height, high_pt + padding_pt)
        expanded_bottom_pt = max(0.0, low_pt - padding_pt)
        y_top_px = int(max(0, round((page_height - expanded_top_pt) / page_height * image_height)))
        y_bottom_px = int(min(image_height, round((page_height - expanded_bottom_pt) / page_height * image_height)))

    x_left_px = int(max(0, round(x_left_pt / page_width * image_width)))
    x_right_px = int(min(image_width, round(x_right_pt / page_width * image_width)))
    if x_right_px <= x_left_px or y_bottom_px <= y_top_px:
        return None
    return (x_left_px, y_top_px, x_right_px, y_bottom_px)


def union_provenance_bbox(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    bboxes = [record.get("bbox") for record in records if record.get("bbox") is not None]
    if not bboxes:
        return None
    edges = [bbox_edges(bbox) for bbox in bboxes]
    edges = [edge for edge in edges if edge is not None]
    if not edges:
        return None
    left = min(edge[0] for edge in edges)
    right = max(edge[1] for edge in edges)
    low = min(edge[2] for edge in edges)
    high = max(edge[3] for edge in edges)
    origin = str(bboxes[0].get("coord_origin") or "BOTTOMLEFT")
    return {"l": left, "r": right, "b": low, "t": high, "coord_origin": origin}


def caption_to_page_bottom_bbox(*, caption_bbox: dict[str, Any], page_size: dict[str, float]) -> dict[str, Any]:
    edges = bbox_edges(caption_bbox)
    if edges is None:
        return caption_bbox
    left, right, _low, high = edges
    origin = str(caption_bbox.get("coord_origin") or "BOTTOMLEFT")
    return {
        "l": 0.0,
        "r": float(page_size["width"]),
        "t": high,
        "b": 0.0,
        "coord_origin": origin,
        "anchor_l": left,
        "anchor_r": right,
    }


def render_pdf_page_png(input_pdf: Path, *, page_no: int, output_path: Path, dpi: int) -> dict[str, Any]:
    ensure_dir(output_path.parent)
    if output_path.exists() and output_path.stat().st_size > 0:
        return summarize_file(output_path) | {"render_dpi": dpi, "reused": True}
    if shutil.which("pdftoppm") is not None:
        prefix = output_path.with_suffix("")
        result = subprocess.run(
            [
                "pdftoppm",
                "-f",
                str(page_no),
                "-l",
                str(page_no),
                "-r",
                str(dpi),
                "-png",
                "-singlefile",
                str(input_pdf),
                str(prefix),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 or not output_path.exists():
            raise DoclingConvertError(
                "pdftoppm failed while rendering page "
                f"{page_no} of {input_pdf}: {result.stderr.strip()[:1000]}"
            )
        return summarize_file(output_path) | {
            "render_dpi": dpi,
            "render_engine": "pdftoppm",
            "reused": False,
            "stderr": result.stderr.strip()[:1000],
        }

    # Databricks DBR 15.4 default CPU clusters do not expose Poppler
    # (`pdftoppm`/`pdfinfo`) in the notebook environment.  Docling already
    # brings pypdfium2 in the tested runtime, so use it as the no-OS-package
    # page renderer for page-width table/figure crops.
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(input_pdf))
        try:
            page_index = page_no - 1
            if page_index < 0 or page_index >= len(pdf):
                raise DoclingConvertError(f"Page {page_no} is outside PDF page range for {input_pdf}")
            page = pdf[page_index]
            try:
                scale = dpi / 72.0
                bitmap = page.render(scale=scale)
                pil_image = bitmap.to_pil()
                pil_image.save(output_path, "PNG")
            finally:
                with contextlib.suppress(Exception):
                    page.close()
        finally:
            with contextlib.suppress(Exception):
                pdf.close()
    except ImportError as exc:
        raise DoclingConvertError(
            "No PDF page renderer is available. Install Poppler (`pdftoppm`) or ensure "
            "`pypdfium2` is installed; Docling normally provides pypdfium2 in the "
            "Databricks CPU batch runtime."
        ) from exc
    except Exception as exc:
        raise DoclingConvertError(
            f"pypdfium2 failed while rendering page {page_no} of {input_pdf}: {exc}"
        ) from exc

    if not output_path.exists():
        raise DoclingConvertError(
            f"pypdfium2 did not create expected page image for page {page_no} of {input_pdf}: {output_path}"
        )
    return summarize_file(output_path) | {
        "render_dpi": dpi,
        "render_engine": "pypdfium2",
        "render_scale": dpi / 72.0,
        "reused": False,
    }


def build_page_width_bbox_bitmap(
    *,
    input_pdf: Path | None,
    document: Any,
    page_no: int,
    bbox: dict[str, Any],
    output_path: Path,
    page_render_dir: Path,
    source: str,
    dpi: int = 220,
    padding_pt: float = 18.0,
) -> dict[str, Any] | None:
    if input_pdf is None:
        return None
    page_size = page_size_points(document, page_no)
    if page_size is None:
        return None

    page_render_dir = ensure_dir(page_render_dir)
    page_png = page_render_dir / f"page-{page_no:04d}-{dpi}dpi.png"
    page_image_info = render_pdf_page_png(input_pdf, page_no=page_no, output_path=page_png, dpi=dpi)

    from PIL import Image

    with Image.open(page_png) as page_image:
        crop_box = page_width_crop_box(
            page_size=page_size,
            bbox=bbox,
            image_size=page_image.size,
            padding_pt=padding_pt,
        )
        ensure_dir(output_path.parent)
        page_image.crop(crop_box).save(output_path, "PNG")

    return summarize_file(output_path) | {
        "source": source,
        "page_no": page_no,
        "bbox": bbox,
        "page_size_points": page_size,
        "padding_pt": padding_pt,
        "crop_box_pixels": list(crop_box),
        "source_page_image": page_image_info,
    }


def build_exact_bbox_bitmap(
    *,
    input_pdf: Path | None,
    document: Any,
    page_no: int,
    bbox: dict[str, Any],
    output_path: Path,
    page_render_dir: Path,
    source: str,
    dpi: int = 220,
    padding_pt: float = 8.0,
) -> dict[str, Any] | None:
    if input_pdf is None:
        return None
    page_size = page_size_points(document, page_no)
    if page_size is None:
        return None

    page_render_dir = ensure_dir(page_render_dir)
    page_png = page_render_dir / f"page-{page_no:04d}-{dpi}dpi.png"
    page_image_info = render_pdf_page_png(input_pdf, page_no=page_no, output_path=page_png, dpi=dpi)

    from PIL import Image

    with Image.open(page_png) as page_image:
        crop_box = bbox_crop_box(
            page_size=page_size,
            bbox=bbox,
            image_size=page_image.size,
            padding_pt=padding_pt,
        )
        if crop_box is None:
            return None
        ensure_dir(output_path.parent)
        page_image.crop(crop_box).save(output_path, "PNG")

    return summarize_file(output_path) | {
        "source": source,
        "page_no": page_no,
        "bbox": bbox,
        "page_size_points": page_size,
        "padding_pt": padding_pt,
        "crop_box_pixels": list(crop_box),
        "source_page_image": page_image_info,
    }


def build_page_width_item_bitmap(
    *,
    input_pdf: Path | None,
    document: Any,
    element: Any,
    output_path: Path,
    page_render_dir: Path,
    source: str,
    dpi: int = 220,
    padding_pt: float = 18.0,
) -> dict[str, Any] | None:
    if input_pdf is None:
        return None
    provenance = item_provenance_records(element)
    if not provenance:
        return None
    first = provenance[0]
    page_no = int(first["page_no"])
    bbox = first["bbox"]
    return build_page_width_bbox_bitmap(
        input_pdf=input_pdf,
        document=document,
        page_no=page_no,
        bbox=bbox,
        output_path=output_path,
        page_render_dir=page_render_dir,
        source=source,
        dpi=dpi,
        padding_pt=padding_pt,
    )


def build_page_width_table_bitmap(
    *,
    input_pdf: Path | None,
    document: Any,
    element: Any,
    tables_dir: Path,
    table_index: int,
    dpi: int = 220,
    padding_pt: float = 18.0,
) -> dict[str, Any] | None:
    return build_page_width_item_bitmap(
        input_pdf=input_pdf,
        document=document,
        element=element,
        output_path=tables_dir / f"table-{table_index:04d}.page-width.png",
        page_render_dir=tables_dir / "page-renders",
        source="page_width_provenance_crop",
        dpi=dpi,
        padding_pt=padding_pt,
    )


def build_page_width_picture_bitmap(
    *,
    input_pdf: Path | None,
    document: Any,
    element: Any,
    images_dir: Path,
    picture_index: int,
    dpi: int = 220,
    padding_pt: float = 24.0,
) -> dict[str, Any] | None:
    return build_page_width_item_bitmap(
        input_pdf=input_pdf,
        document=document,
        element=element,
        output_path=images_dir / f"picture-{picture_index:04d}.page-width.png",
        page_render_dir=images_dir / "page-renders",
        source="page_width_provenance_crop",
        dpi=dpi,
        padding_pt=padding_pt,
    )


def build_formula_bitmap(
    *,
    input_pdf: Path | None,
    document: Any,
    element: Any,
    formulas_dir: Path,
    formula_index: int,
    dpi: int = 220,
    padding_pt: float = 8.0,
) -> dict[str, Any] | None:
    provenance = item_provenance_records(element)
    if not provenance:
        return None
    first = provenance[0]
    return build_exact_bbox_bitmap(
        input_pdf=input_pdf,
        document=document,
        page_no=int(first["page_no"]),
        bbox=first["bbox"],
        output_path=formulas_dir / f"formula-{formula_index:04d}.png",
        page_render_dir=formulas_dir / "page-renders",
        source="formula_bbox_crop",
        dpi=dpi,
        padding_pt=padding_pt,
    )


def preferred_table_bitmap_info(table_record: dict[str, Any]) -> dict[str, Any]:
    return (table_record.get("llm_image") or table_record.get("image") or {})  # type: ignore[return-value]


def preferred_picture_bitmap_info(picture_record: dict[str, Any]) -> dict[str, Any]:
    return (picture_record.get("llm_image") or picture_record.get("image") or {})  # type: ignore[return-value]


def picture_page_geometry(
    provenance: list[dict[str, Any]] | None,
    *,
    page_size: dict[str, float] | None,
) -> dict[str, Any]:
    if not provenance or page_size is None:
        return {}
    bbox = provenance[0].get("bbox")
    edges = bbox_edges(bbox or {})
    if edges is None:
        return {}
    left, right, low, high = edges
    page_width = float(page_size["width"])
    page_height = float(page_size["height"])
    width_ratio = max(right - left, 0.0) / max(page_width, 1.0)
    height_ratio = max(high - low, 0.0) / max(page_height, 1.0)
    origin = str((bbox or {}).get("coord_origin") or "BOTTOMLEFT").upper()
    if origin.endswith("TOPLEFT"):
        near_top = low <= page_height * 0.08
        near_bottom = high >= page_height * 0.92
    else:
        near_top = high >= page_height * 0.92
        near_bottom = low <= page_height * 0.08
    return {
        "width_points": max(right - left, 0.0),
        "height_points": max(high - low, 0.0),
        "width_inches": max(right - left, 0.0) / PDF_POINTS_PER_INCH,
        "height_inches": max(high - low, 0.0) / PDF_POINTS_PER_INCH,
        "width_cm": max(right - left, 0.0) / PDF_POINTS_PER_INCH * CM_PER_INCH,
        "height_cm": max(high - low, 0.0) / PDF_POINTS_PER_INCH * CM_PER_INCH,
        "area_square_inches": (
            max(right - left, 0.0) * max(high - low, 0.0) / PDF_POINTS_PER_INCH**2
        ),
        "width_ratio": width_ratio,
        "height_ratio": height_ratio,
        "area_ratio": width_ratio * height_ratio,
        "near_top_margin": near_top,
        "near_bottom_margin": near_bottom,
    }


def classify_picture_for_llm_bitmap(
    image_path: Path | None,
    *,
    caption: str = "",
    provenance: list[dict[str, Any]] | None = None,
    page_size: dict[str, float] | None = None,
) -> dict[str, Any]:
    flags: list[str] = []
    score = 0
    geometry = picture_page_geometry(provenance, page_size=page_size)
    physical_width = float(geometry.get("width_inches") or 0.0)
    physical_height = float(geometry.get("height_inches") or 0.0)
    physical_area = float(geometry.get("area_square_inches") or 0.0)
    physical_size_available = physical_width > 0 and physical_height > 0
    dimensions = png_dimensions(image_path) if image_path else None
    if dimensions:
        width, height = dimensions
        area = width * height
        aspect_ratio = max(width / max(height, 1), height / max(width, 1))
        if width < 140 or height < 90 or area < 25_000:
            flags.append("icon_sized")
        if area < 90_000 and aspect_ratio >= 3.5:
            flags.append("banner_or_page_furniture_shaped")
        if width >= 180 and height >= 120 and area >= 30_000:
            flags.append("medium_semantic_image")
            if not physical_size_available:
                score += 2
        if width >= 300 and height >= 120 and area >= 50_000:
            flags.append("large_semantic_image")
            if not physical_size_available:
                score += 2
        if width >= 500 and height >= 200:
            flags.append("large_figure_candidate")
            if not physical_size_available:
                score += 1
    caption_l = caption.lower()
    semantic_terms = ["figure", "fig.", "diagram", "chart", "graph", "map", "table", "matrix", "flow"]
    if any(term in caption_l for term in semantic_terms):
        flags.append("semantic_caption")
        score += 3
    decorative_terms = ["logo", "seal", "watermark", "banner", "letterhead"]
    if any(term in caption_l for term in decorative_terms):
        flags.append("decorative_caption")
        score -= 3
    if physical_width >= 1.5 and physical_height >= 0.65 and physical_area >= 1.25:
        flags.append("physically_substantive_region")
        score += 2
    if (
        float(geometry.get("area_ratio") or 0.0) >= 0.025
        and float(geometry.get("width_ratio") or 0.0) >= 0.20
        and float(geometry.get("height_ratio") or 0.0) >= 0.08
    ):
        flags.append("large_page_region")
        score += 2
    near_margin = geometry.get("near_top_margin") or geometry.get("near_bottom_margin")
    semantic_caption = "semantic_caption" in flags
    if (
        near_margin
        and (
            float(geometry.get("area_ratio") or 0.0) <= 0.015
            or physical_height <= 0.30
        )
        and not semantic_caption
    ):
        flags.append("small_margin_furniture")
        score -= 3
    hard_furniture = "decorative_caption" in flags or (
        "small_margin_furniture" in flags and not semantic_caption
    )
    if (
        "banner_or_page_furniture_shaped" in flags
        and not semantic_caption
        and "physically_substantive_region" not in flags
    ):
        hard_furniture = True
    bitmap_required = score >= 2 and not hard_furniture
    return {
        "llm_render": "bitmap" if bitmap_required else "skip",
        "bitmap_required": bitmap_required,
        "score": score,
        "flags": flags,
        "dimensions": None if dimensions is None else {"width": dimensions[0], "height": dimensions[1]},
        "page_geometry": geometry,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def difference_hash(path: Path) -> int:
    """Return a deterministic 64-bit dHash for conservative near-duplicate matching."""

    from PIL import Image

    with Image.open(path) as image:
        grayscale = image.convert("L").resize((9, 8))
        get_pixels = getattr(grayscale, "get_flattened_data", grayscale.getdata)
        pixels = list(get_pixels())
    value = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            value = (value << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
    return value


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def same_furniture_margin(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_geometry = (left.get("render_decision") or {}).get("page_geometry") or {}
    right_geometry = (right.get("render_decision") or {}).get("page_geometry") or {}
    same_top = left_geometry.get("near_top_margin") and right_geometry.get("near_top_margin")
    same_bottom = left_geometry.get("near_bottom_margin") and right_geometry.get("near_bottom_margin")
    if not (same_top or same_bottom):
        return False
    left_width = float(left_geometry.get("width_inches") or 0.0)
    right_width = float(right_geometry.get("width_inches") or 0.0)
    left_height = float(left_geometry.get("height_inches") or 0.0)
    right_height = float(right_geometry.get("height_inches") or 0.0)
    if min(left_width, right_width, left_height, right_height) <= 0:
        return True
    return (
        abs(left_width - right_width) / max(left_width, right_width) <= 0.20
        and abs(left_height - right_height) / max(left_height, right_height) <= 0.20
    )


def suppress_repeated_picture_furniture(picture_records: list[dict[str, Any]]) -> int:
    """Suppress exact and perceptually repeated margin furniture.

    Perceptual matching is deliberately narrower than exact matching: it needs
    three pages, compatible physical dimensions, and the same top/bottom margin.
    This avoids treating recurring in-body chart panels as furniture.
    """

    by_hash: dict[str, list[dict[str, Any]]] = {}
    hashable_records: list[dict[str, Any]] = []
    for record in picture_records:
        path_value = (record.get("image") or {}).get("path")
        if not path_value:
            continue
        path = Path(path_value)
        if not path.is_file():
            continue
        digest = sha256_file(path)
        record["image_sha256"] = digest
        with contextlib.suppress(Exception):
            perceptual_hash = difference_hash(path)
            record["image_dhash"] = f"{perceptual_hash:016x}"
            hashable_records.append(record)
        by_hash.setdefault(digest, []).append(record)

    suppressed = 0
    suppressed_record_ids: set[int] = set()

    def suppress_record(record: dict[str, Any], flag: str) -> None:
        nonlocal suppressed
        decision = record.get("render_decision") or {}
        flags = list(decision.get("flags") or [])
        if flag not in flags:
            flags.append(flag)
        decision["flags"] = flags
        decision["bitmap_required"] = False
        decision["llm_render"] = "skip"
        decision["score"] = min(int(decision.get("score") or 0), -1)
        record["render_decision"] = decision
        if id(record) not in suppressed_record_ids:
            suppressed_record_ids.add(id(record))
            suppressed += 1

    for records in by_hash.values():
        repeated_pages = page_set_from_records(records)
        if len(repeated_pages) < 2:
            continue
        for record in records:
            decision = record.get("render_decision") or {}
            flags = list(decision.get("flags") or [])
            geometry = decision.get("page_geometry") or {}
            semantic_caption = "semantic_caption" in flags
            margin_or_furniture_shape = (
                geometry.get("near_top_margin")
                or geometry.get("near_bottom_margin")
                or "banner_or_page_furniture_shaped" in flags
                or "icon_sized" in flags
            )
            if semantic_caption or not margin_or_furniture_shape:
                continue
            suppress_record(record, "repeated_page_furniture")

    parent = list(range(len(hashable_records)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left_index, left in enumerate(hashable_records):
        left_flags = set((left.get("render_decision") or {}).get("flags") or [])
        if "semantic_caption" in left_flags:
            continue
        left_hash = int(str(left["image_dhash"]), 16)
        left_pages = page_set_from_records([left])
        for right_index in range(left_index + 1, len(hashable_records)):
            right = hashable_records[right_index]
            right_flags = set((right.get("render_decision") or {}).get("flags") or [])
            if "semantic_caption" in right_flags:
                continue
            if left_pages.intersection(page_set_from_records([right])):
                continue
            if not same_furniture_margin(left, right):
                continue
            right_hash = int(str(right["image_dhash"]), 16)
            if hamming_distance(left_hash, right_hash) > PERCEPTUAL_FURNITURE_MAX_DISTANCE:
                continue
            left_root = find(left_index)
            right_root = find(right_index)
            if left_root != right_root:
                parent[right_root] = left_root

    perceptual_groups: dict[int, list[dict[str, Any]]] = {}
    for index, record in enumerate(hashable_records):
        perceptual_groups.setdefault(find(index), []).append(record)
    for records in perceptual_groups.values():
        if len(page_set_from_records(records)) < 3:
            continue
        for record in records:
            if id(record) in suppressed_record_ids:
                continue
            suppress_record(record, "perceptually_repeated_page_furniture")
    return suppressed


def suppress_photo_credit_decorations(
    picture_records: list[dict[str, Any]],
    text_records: list[dict[str, Any]],
) -> int:
    """Suppress uncaptioned full-page photographs next to explicit photo credits."""

    page_text: dict[int, str] = {}
    for record in text_records:
        page_no = record.get("page_no")
        if page_no is None:
            continue
        page_text[int(page_no)] = "\n".join(
            [page_text.get(int(page_no), ""), str(record.get("text") or "")]
        ).lower()
    credit_terms = [
        "shutterstock",
        "unsplash",
        "pexels",
        "photo credit",
        "cover photo",
        "photograph by",
        "©",
    ]
    suppressed = 0
    for record in picture_records:
        if str(record.get("caption") or "").strip():
            continue
        decision = record.get("render_decision") or {}
        geometry = decision.get("page_geometry") or {}
        if float(geometry.get("area_ratio") or 0.0) < 0.30:
            continue
        pages = page_set_from_records([record])
        if not pages or not any(
            term in page_text.get(page_no, "") for page_no in pages for term in credit_terms
        ):
            continue
        flags = list(decision.get("flags") or [])
        if "photo_credit_decoration" not in flags:
            flags.append("photo_credit_decoration")
        decision["flags"] = flags
        decision["bitmap_required"] = False
        decision["llm_render"] = "skip"
        decision["score"] = min(int(decision.get("score") or 0), -1)
        record["render_decision"] = decision
        suppressed += 1
    return suppressed


def looks_like_table_caption(text: str) -> bool:
    stripped = re.sub(r"\s+", " ", text or "").strip()
    return bool(TABLE_CAPTION_RE.search(stripped) or TABLE_LIKE_ANNEX_RE.search(stripped))


def looks_like_major_section_heading(text: str) -> bool:
    stripped = re.sub(r"\s+", " ", text or "").strip()
    return bool(MAJOR_SECTION_HEADING_RE.search(stripped))


def text_record_key(record: dict[str, Any]) -> str:
    return f"{record.get('page_no')}::{normalize_cell_text(str(record.get('text') or ''))}"


def page_set_from_records(records: list[dict[str, Any]]) -> set[int]:
    pages: set[int] = set()
    for record in records:
        for provenance in record.get("provenance") or []:
            page_no = provenance.get("page_no")
            if page_no is not None:
                pages.add(int(page_no))
    return pages


def text_records_by_page(text_records: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    records_by_page: dict[int, list[dict[str, Any]]] = {}
    for record in text_records:
        page_no = record.get("page_no")
        if page_no is not None:
            records_by_page.setdefault(int(page_no), []).append(record)
    return records_by_page


def first_text_record_for_page(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((record for record in records if str(record.get("text") or "").strip()), None)


def text_record_provenance_for_page(record: dict[str, Any], page_no: int) -> list[dict[str, Any]]:
    return [
        provenance
        for provenance in record.get("provenance") or []
        if int(provenance.get("page_no") or -1) == page_no and provenance.get("bbox") is not None
    ]


def page_has_two_column_table_layout(records: list[dict[str, Any]], *, page_size: dict[str, float]) -> bool:
    bboxes = [
        provenance["bbox"]
        for record in records
        for provenance in text_record_provenance_for_page(record, int(record.get("page_no") or -1))
    ]
    edges = [bbox_edges(bbox) for bbox in bboxes]
    edges = [edge for edge in edges if edge is not None]
    if len(edges) < 3:
        return False
    page_width = float(page_size["width"])
    centers = [(edge[0] + edge[1]) / 2 for edge in edges]
    has_left = any(center < page_width * 0.47 for center in centers)
    has_right = any(center > page_width * 0.53 for center in centers)
    union_width = max(edge[1] for edge in edges) - min(edge[0] for edge in edges)
    return has_left and has_right and union_width >= page_width * 0.60


def page_looks_like_table_continuation(records: list[dict[str, Any]], *, page_size: dict[str, float]) -> bool:
    if not records:
        return False
    first_record = first_text_record_for_page(records)
    first_text = str((first_record or {}).get("text") or "")
    if looks_like_table_caption(first_text) or looks_like_major_section_heading(first_text):
        return False

    all_text = "\n".join(str(record.get("text") or "") for record in records)
    all_text_l = all_text.lower()
    term_hits = sum(1 for term in TABLE_CONTINUATION_TERMS if term in all_text_l)
    first_stripped = first_text.strip()
    starts_mid_sentence = bool(first_stripped) and (
        first_stripped[0].islower()
        or first_stripped.startswith((",", ";", ")", "and ", "or ", "(including"))
    )
    two_column_layout = page_has_two_column_table_layout(records, page_size=page_size)

    return (
        term_hits >= 3
        or (term_hits >= 2 and two_column_layout)
        or (starts_mid_sentence and term_hits >= 1)
        or (starts_mid_sentence and two_column_layout and len(records) >= 4)
    )


def continuation_page_bbox_from_records(
    records: list[dict[str, Any]],
    *,
    page_no: int,
    page_size: dict[str, float],
) -> dict[str, Any] | None:
    provenance_records = [
        provenance
        for record in records
        for provenance in text_record_provenance_for_page(record, page_no)
    ]
    union_bbox = union_provenance_bbox(provenance_records)
    if union_bbox is None:
        return None
    union_bbox["l"] = 0.0
    union_bbox["r"] = float(page_size["width"])
    return union_bbox


def add_clustered_picture_fallbacks(
    *,
    artifacts: dict[str, Any],
    input_pdf: Path | None,
    document: Any,
    images_dir: Path,
) -> None:
    """Keep composite figures that Docling split into several icon-sized pictures.

    In several reports, one semantic figure is represented in the PDF as many
    small picture fragments (for example four donut charts or six chart panels).
    Per-picture filtering can lose the semantic figure or retain several panels
    as separate request assets. Group three or more non-furniture fragments into
    one page-width union crop and suppress the redundant individual references.
    """

    pictures = artifacts.get("pictures") or []
    candidates_by_page: dict[int, list[dict[str, Any]]] = {}
    for record in pictures:
        flags = set((record.get("render_decision") or {}).get("flags") or [])
        if flags.intersection(
            {
                "decorative_caption",
                "small_margin_furniture",
                "repeated_page_furniture",
                "photo_credit_decoration",
            }
        ):
            continue
        for provenance in record.get("provenance") or []:
            page_no = provenance.get("page_no")
            if page_no is not None:
                candidates_by_page.setdefault(int(page_no), []).append(record)

    promoted = 0
    for page_no, records in sorted(candidates_by_page.items()):
        if len(records) < 3:
            continue
        provenance_records = [
            provenance
            for record in records
            for provenance in record.get("provenance") or []
            if int(provenance.get("page_no") or -1) == page_no
        ]
        union_bbox = union_provenance_bbox(provenance_records)
        page_size = page_size_points(document, page_no)
        if union_bbox is None or page_size is None:
            continue
        edges = bbox_edges(union_bbox)
        if edges is None:
            continue
        width_pt = edges[1] - edges[0]
        height_pt = edges[3] - edges[2]
        page_area = float(page_size["width"]) * float(page_size["height"])
        cluster_area = max(width_pt, 0.0) * max(height_pt, 0.0)
        if width_pt < float(page_size["width"]) * 0.20 or height_pt < float(page_size["height"]) * 0.08:
            continue
        if cluster_area < page_area * 0.025:
            continue

        promoted += 1
        target = next((record for record in records if record.get("caption")), records[0])
        llm_image = build_page_width_bbox_bitmap(
            input_pdf=input_pdf,
            document=document,
            page_no=page_no,
            bbox=union_bbox,
            output_path=images_dir / f"picture-cluster-page-{page_no:04d}.png",
            page_render_dir=images_dir / "page-renders",
            source="page_width_picture_cluster_crop",
            dpi=220,
            padding_pt=24.0,
        )
        if llm_image is None:
            continue
        target["provenance"] = [{"page_no": page_no, "bbox": union_bbox}]
        target["llm_image"] = llm_image
        target["caption"] = target.get("caption") or f"Composite figure on page {page_no}"
        decision = target.setdefault("render_decision", {})
        decision["llm_render"] = "bitmap"
        decision["bitmap_required"] = True
        decision["bitmap_source"] = llm_image.get("source")
        decision["score"] = max(int(decision.get("score") or 0), 2)
        flags = list(decision.get("flags") or [])
        for flag in ["clustered_small_pictures", "composite_figure_fallback"]:
            if flag not in flags:
                flags.append(flag)
        decision["flags"] = flags
        for record in records:
            if record is target:
                continue
            other_decision = record.setdefault("render_decision", {})
            other_decision["llm_render"] = "skip"
            other_decision["bitmap_required"] = False
            other_flags = list(other_decision.get("flags") or [])
            if "included_in_composite_figure" not in other_flags:
                other_flags.append("included_in_composite_figure")
            other_decision["flags"] = other_flags

    artifacts.setdefault("fallbacks", {})["clustered_picture_pages"] = promoted


def add_captioned_table_fallbacks(
    *,
    artifacts: dict[str, Any],
    text_records: list[dict[str, Any]],
    input_pdf: Path | None,
    document: Any,
    tables_dir: Path,
) -> None:
    """Create page-width crops for captioned tables missed as TableItems.

    A captioned table can span multiple pages while Docling misses the leading
    caption page as a TableItem.  In that case, the caption crop alone preserves
    only page 1 of the table.  After adding the caption crop, scan immediately
    following pages for table-continuation signals and add page-width fallback
    crops until an explicit new table/section or a real Docling table item is
    encountered.
    """

    table_pages = page_set_from_records(artifacts.get("tables") or [])
    records_by_page = text_records_by_page(text_records)
    fallback_records: list[dict[str, Any]] = []
    seen_pages: set[int] = set()

    def append_fallback_record(
        *,
        page_no: int,
        bbox: dict[str, Any],
        anchor_key: str,
        anchor_text: str,
        output_path: Path,
        source: str,
        flags: list[str],
        display_text: str | None = None,
        continuation_of_page: int | None = None,
    ) -> bool:
        llm_image = build_page_width_bbox_bitmap(
            input_pdf=input_pdf,
            document=document,
            page_no=page_no,
            bbox=bbox,
            output_path=output_path,
            page_render_dir=tables_dir / "page-renders",
            source=source,
            dpi=220,
            padding_pt=12.0,
        )
        if llm_image is None:
            return False
        index = len(fallback_records) + 1
        fallback_record = {
            "index": index,
            "page_no": page_no,
            "anchor_key": anchor_key,
            "anchor_text": anchor_text,
            "display_text": display_text or anchor_text,
            "provenance": [{"page_no": page_no, "bbox": bbox}],
            "llm_image": llm_image,
            "render_decision": {
                "llm_render": "bitmap",
                "bitmap_required": True,
                "bitmap_source": llm_image.get("source"),
                "flags": flags,
            },
        }
        if continuation_of_page is not None:
            fallback_record["continuation_of_page"] = continuation_of_page
        fallback_records.append(fallback_record)
        seen_pages.add(page_no)
        return True

    for text_record in text_records:
        page_no = text_record.get("page_no")
        if page_no is None:
            continue
        page_no = int(page_no)
        if page_no in table_pages or page_no in seen_pages:
            continue
        text = str(text_record.get("text") or "")
        if not looks_like_table_caption(text):
            continue
        provenance = text_record.get("provenance") or []
        if not provenance or provenance[0].get("bbox") is None:
            continue
        page_size = page_size_points(document, page_no)
        if page_size is None:
            continue
        caption_bbox = provenance[0]["bbox"]
        fallback_bbox = caption_to_page_bottom_bbox(caption_bbox=caption_bbox, page_size=page_size)
        created = append_fallback_record(
            page_no=page_no,
            bbox=fallback_bbox,
            anchor_key=text_record_key(text_record),
            anchor_text=text,
            output_path=tables_dir / f"table-caption-fallback-page-{page_no:04d}.png",
            source="page_width_captioned_table_fallback_crop",
            flags=["captioned_table_without_table_item", "page_width_caption_fallback"],
        )
        if not created:
            continue

        for continuation_page_no in range(page_no + 1, page_no + CAPTION_FALLBACK_MAX_CONTINUATION_PAGES + 1):
            if continuation_page_no in table_pages:
                break
            if continuation_page_no in seen_pages:
                break
            continuation_records = records_by_page.get(continuation_page_no) or []
            if any(looks_like_table_caption(str(record.get("text") or "")) for record in continuation_records):
                break
            continuation_page_size = page_size_points(document, continuation_page_no)
            if continuation_page_size is None:
                break
            if not page_looks_like_table_continuation(
                continuation_records,
                page_size=continuation_page_size,
            ):
                break
            continuation_anchor = first_text_record_for_page(continuation_records)
            if continuation_anchor is None:
                break
            continuation_bbox = continuation_page_bbox_from_records(
                continuation_records,
                page_no=continuation_page_no,
                page_size=continuation_page_size,
            )
            if continuation_bbox is None:
                break
            continuation_label = f"Continuation of {text} (page {continuation_page_no})"
            append_fallback_record(
                page_no=continuation_page_no,
                bbox=continuation_bbox,
                anchor_key=text_record_key(continuation_anchor),
                anchor_text=str(continuation_anchor.get("text") or ""),
                display_text=continuation_label,
                output_path=tables_dir / f"table-caption-fallback-page-{page_no:04d}-cont-{continuation_page_no:04d}.png",
                source="page_width_captioned_table_continuation_fallback_crop",
                flags=[
                    "captioned_table_continuation_without_table_item",
                    "page_width_caption_continuation_fallback",
                ],
                continuation_of_page=page_no,
            )
    artifacts["fallback_table_bitmaps"] = fallback_records


def item_to_markdown(element: Any, document: Any) -> str:
    with contextlib.suppress(Exception):
        value = element.export_to_markdown(doc=document)
        if value:
            return str(value).strip()
    for attr in ["text", "orig", "caption"]:
        value = getattr(element, attr, None)
        if value:
            return str(value).strip()
    return ""


FORMULA_NOT_DECODED_HTML_RE = re.compile(
    r'<div\s+class="formula-not-decoded"\s*>\s*Formula not decoded\s*</div>',
    re.IGNORECASE,
)
FORMULA_NOT_DECODED_MD_RE = re.compile(r"<!--\s*formula-not-decoded\s*-->", re.IGNORECASE)


def formula_image_alt(record: dict[str, Any]) -> str:
    formula_index = int(record.get("index") or 0)
    return f"Formula {formula_index}"


def replace_raw_formula_placeholders(
    *,
    artifacts: dict[str, Any],
    formulas: list[dict[str, Any]],
) -> dict[str, Any]:
    """Replace raw Docling formula placeholders with bbox formula bitmaps.

    Docling may detect formulas and retain their provenance, but emit
    `Formula not decoded` in raw HTML/Markdown when formula enrichment is off
    or MathML parsing fails.  For this workflow the image is safer than a lossy
    or empty textual formula, so replace placeholders in-order with crops from
    the detected formula bounding boxes.
    """

    replacements: dict[str, Any] = {}
    formulas_with_images = [
        record
        for record in formulas
        if ((record.get("image") or {}).get("path"))
    ]
    if not formulas_with_images:
        return replacements

    html_file = ((artifacts.get("files") or {}).get("html") or {}).get("path")
    if html_file:
        html_path = Path(html_file)
        text = read_text_if_exists(html_path)
        if text:
            count = 0
            for record in formulas_with_images:
                image_path = Path((record.get("image") or {}).get("path"))
                image_rel = relative_posix_path(image_path, start=html_path.parent)
                alt = formula_image_alt(record)
                tag = (
                    '<img class="formula-bitmap" '
                    f'src="{html.escape(image_rel, quote=True)}" '
                    f'alt="{html.escape(alt, quote=True)}">'
                )
                text, n = FORMULA_NOT_DECODED_HTML_RE.subn(tag, text, count=1)
                count += n
            if count:
                write_text(html_path, text)
                artifacts["files"]["html"] = summarize_file(html_path)
                replacements["html_formula_placeholders_replaced"] = count

    markdown_file = ((artifacts.get("files") or {}).get("markdown") or {}).get("path")
    if markdown_file:
        markdown_path = Path(markdown_file)
        text = read_text_if_exists(markdown_path)
        if text:
            count = 0
            for record in formulas_with_images:
                image_path = Path((record.get("image") or {}).get("path"))
                image_rel = relative_posix_path(image_path, start=markdown_path.parent)
                alt = formula_image_alt(record).replace("]", "\\]")
                markdown = f"![{alt}]({image_rel})"
                text, n = FORMULA_NOT_DECODED_MD_RE.subn(markdown, text, count=1)
                count += n
            if count:
                write_text(markdown_path, text)
                artifacts["files"]["markdown"] = summarize_file(markdown_path)
                replacements["markdown_formula_placeholders_replaced"] = count

    return replacements


def bbox_edges(bbox: dict[str, Any]) -> tuple[float, float, float, float] | None:
    try:
        left = float(bbox["l"])
        right = float(bbox["r"])
        top = float(bbox["t"])
        bottom = float(bbox["b"])
    except (KeyError, TypeError, ValueError):
        return None
    return min(left, right), max(left, right), min(top, bottom), max(top, bottom)


def bbox_contains(outer_bbox: dict[str, Any], inner_bbox: dict[str, Any], *, padding_pt: float = 6.0) -> bool:
    outer = bbox_edges(outer_bbox)
    inner = bbox_edges(inner_bbox)
    if outer is None or inner is None:
        return False
    outer_left, outer_right, outer_low, outer_high = outer
    inner_left, inner_right, inner_low, inner_high = inner
    return (
        inner_left >= outer_left - padding_pt
        and inner_right <= outer_right + padding_pt
        and inner_low >= outer_low - padding_pt
        and inner_high <= outer_high + padding_pt
    )


def bitmap_exclusion_zones(
    *,
    table_records: list[dict[str, Any]],
    picture_records: list[dict[str, Any]],
    fallback_table_records: list[dict[str, Any]] | None = None,
    table_bitmap_policy: str,
) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    for record in table_records:
        if not table_should_render_as_bitmap(record, policy=table_bitmap_policy):
            continue
        if not preferred_table_bitmap_info(record).get("path"):
            continue
        for provenance in record.get("provenance") or []:
            if provenance.get("bbox") is not None:
                zones.append(
                    {
                        "kind": "table",
                        "index": record.get("index"),
                        "page_no": provenance.get("page_no"),
                        "bbox": provenance.get("bbox"),
                        "padding_pt": 6.0,
                    }
                )
    for record in fallback_table_records or []:
        if not (record.get("render_decision") or {}).get("bitmap_required"):
            continue
        if not (record.get("llm_image") or {}).get("path"):
            continue
        for provenance in record.get("provenance") or []:
            if provenance.get("bbox") is not None:
                zones.append(
                    {
                        "kind": "fallback_table",
                        "index": record.get("index"),
                        "page_no": provenance.get("page_no"),
                        "bbox": provenance.get("bbox"),
                        "padding_pt": float((record.get("llm_image") or {}).get("padding_pt") or 12.0),
                    }
                )
    for record in picture_records:
        if not (record.get("render_decision") or {}).get("bitmap_required"):
            continue
        if not preferred_picture_bitmap_info(record).get("path"):
            continue
        for provenance in record.get("provenance") or []:
            if provenance.get("bbox") is not None:
                zones.append(
                    {
                        "kind": "picture",
                        "index": record.get("index"),
                        "page_no": provenance.get("page_no"),
                        "bbox": provenance.get("bbox"),
                        "padding_pt": float((record.get("llm_image") or {}).get("padding_pt") or 24.0),
                    }
                )
    return zones


def item_inside_bitmap_zone(element: Any, zones: list[dict[str, Any]]) -> bool:
    if not zones:
        return False
    for provenance in item_provenance_records(element):
        page_no = provenance.get("page_no")
        bbox = provenance.get("bbox")
        if bbox is None:
            continue
        for zone in zones:
            if zone.get("page_no") == page_no and bbox_contains(
                zone.get("bbox") or {},
                bbox,
                padding_pt=float(zone.get("padding_pt") or 6.0),
            ):
                return True
    return False


def write_llm_oriented_exports(
    document: Any,
    profile_dir: Path,
    artifacts: dict[str, Any],
    *,
    stem: str,
    table_bitmap_policy: str,
    bitmap_mode: str = "full",
) -> dict[str, Any]:
    from docling_core.types.doc import FormulaItem, PictureItem, TableItem

    llm_dir = ensure_dir(profile_dir / "llm")
    md_path = llm_dir / f"{stem}.llm.md"
    html_path = llm_dir / f"{stem}.llm.html"
    table_records = artifacts.get("tables", [])
    picture_records = artifacts.get("pictures", [])
    formula_records = artifacts.get("formulas", [])
    if bitmap_mode not in {"full", "tables_only", "mechanical"}:
        raise ValueError(f"Unsupported LLM bitmap mode: {bitmap_mode}")
    effective_table_policy = "none" if bitmap_mode == "mechanical" else table_bitmap_policy
    include_picture_bitmaps = bitmap_mode == "full"
    include_formula_bitmaps = bitmap_mode == "full"
    fallback_table_records = (
        artifacts.get("fallback_table_bitmaps", []) if bitmap_mode != "mechanical" else []
    )
    exclusion_zones = bitmap_exclusion_zones(
        table_records=table_records,
        picture_records=picture_records if include_picture_bitmaps else [],
        fallback_table_records=fallback_table_records,
        table_bitmap_policy=effective_table_policy,
    )
    fallback_tables_by_anchor = {
        record.get("anchor_key"): record
        for record in fallback_table_records
        if record.get("anchor_key") and (record.get("llm_image") or {}).get("path")
    }
    used_fallback_table_keys: set[str] = set()
    table_index = 0
    picture_index = 0
    formula_index = 0
    md_parts: list[str] = []
    html_parts: list[str] = []
    referenced_asset_paths: list[str] = []
    blocks: list[dict[str, Any]] = []
    decisions = {
        "simple_table_html_count": 0,
        "table_bitmap_count": 0,
        "fallback_table_bitmap_count": 0,
        "picture_bitmap_count": 0,
        "formula_bitmap_count": 0,
        "covered_text_skipped_count": 0,
    }

    def append_block(
        markdown: str,
        html_text: str,
        *,
        kind: str,
        provenance: list[dict[str, Any]] | None = None,
        asset_paths: list[str] | None = None,
    ) -> None:
        markdown = markdown.strip()
        if not markdown and not html_text:
            return
        paths = list(dict.fromkeys(asset_paths or []))
        md_parts.append(markdown)
        html_parts.append(html_text)
        referenced_asset_paths.extend(paths)
        pages = sorted(
            {
                int(record["page_no"])
                for record in provenance or []
                if record.get("page_no") is not None
            }
        )
        blocks.append(
            {
                "kind": kind,
                "markdown": markdown,
                "html": html_text,
                "page_nos": pages,
                "asset_paths": paths,
                "starts_section": kind == "text" and looks_like_major_section_heading(markdown),
            }
        )

    for element, _level in document.iterate_items():
        if isinstance(element, TableItem):
            table_index += 1
            record = table_records[table_index - 1] if table_index - 1 < len(table_records) else {}
            render_bitmap = table_should_render_as_bitmap(record, policy=effective_table_policy)
            bitmap_info = preferred_table_bitmap_info(record)
            if render_bitmap:
                if not bitmap_info.get("path"):
                    raise DoclingConvertError(
                        f"Table {table_index} requires bitmap evidence but no bitmap artifact exists."
                    )
                decisions["table_bitmap_count"] += 1
                image_rel = relative_posix_path(bitmap_info["path"], start=llm_dir)
                alt = f"Table {table_index}"
                append_block(
                    f"![{alt}]({image_rel})",
                    lean_image_tag(image_rel, alt),
                    kind="table_bitmap",
                    provenance=record.get("provenance") or [],
                    asset_paths=[str(bitmap_info["path"])],
                )
            else:
                decisions["simple_table_html_count"] += 1
                table_html = read_text_if_exists(Path((record.get("html") or {}).get("path", "")))
                if not table_html:
                    with contextlib.suppress(Exception):
                        table_html = element.export_to_html(doc=document)
                table_html = lean_html_fragment(table_html)
                append_block(
                    table_html,
                    table_html,
                    kind="table_html",
                    provenance=record.get("provenance") or [],
                )
        elif isinstance(element, PictureItem):
            picture_index += 1
            record = picture_records[picture_index - 1] if picture_index - 1 < len(picture_records) else {}
            render_decision = record.get("render_decision") or {}
            if not include_picture_bitmaps or not render_decision.get("bitmap_required"):
                continue
            image_path = preferred_picture_bitmap_info(record).get("path")
            if not image_path:
                raise DoclingConvertError(
                    f"Picture {picture_index} requires bitmap evidence but no bitmap artifact exists."
                )
            decisions["picture_bitmap_count"] += 1
            image_rel = relative_posix_path(image_path, start=llm_dir)
            caption = record.get("caption") or f"Figure/Picture {picture_index}"
            append_block(
                f"![{caption}]({image_rel})",
                lean_image_tag(image_rel, str(caption)),
                kind="picture_bitmap",
                provenance=record.get("provenance") or [],
                asset_paths=[str(image_path)],
            )
        elif isinstance(element, FormulaItem):
            formula_index += 1
            record = formula_records[formula_index - 1] if formula_index - 1 < len(formula_records) else {}
            image_path = ((record.get("image") or {}).get("path"))
            if image_path and include_formula_bitmaps:
                decisions["formula_bitmap_count"] += 1
                image_rel = relative_posix_path(image_path, start=llm_dir)
                alt = formula_image_alt(record)
                append_block(
                    f"![{alt}]({image_rel})",
                    lean_image_tag(image_rel, alt),
                    kind="formula_bitmap",
                    provenance=record.get("provenance") or [],
                    asset_paths=[str(image_path)],
                )
            else:
                text = item_to_markdown(element, document)
                if text:
                    append_block(
                        text,
                        lean_html_text_block(text),
                        kind="formula_text",
                        provenance=record.get("provenance") or [],
                    )
        else:
            provenance = item_provenance_records(element)
            text = item_to_markdown(element, document)
            if text and provenance:
                page_no = provenance[0].get("page_no")
                key = text_record_key({"page_no": page_no, "text": text})
                fallback_record = fallback_tables_by_anchor.get(key)
                if fallback_record is not None and key not in used_fallback_table_keys:
                    used_fallback_table_keys.add(key)
                    image_path = (fallback_record.get("llm_image") or {}).get("path")
                    if image_path:
                        decisions["fallback_table_bitmap_count"] += 1
                        image_rel = relative_posix_path(image_path, start=llm_dir)
                        alt = str(
                            fallback_record.get("display_text")
                            or fallback_record.get("anchor_text")
                            or f"Fallback table page {page_no}"
                        )
                        append_block(
                            f"![{alt}]({image_rel})",
                            lean_image_tag(image_rel, alt),
                            kind="fallback_table_bitmap",
                            provenance=fallback_record.get("provenance") or provenance,
                            asset_paths=[str(image_path)],
                        )
            if item_inside_bitmap_zone(element, exclusion_zones):
                decisions["covered_text_skipped_count"] += 1
                continue
            if text:
                append_block(
                    text,
                    lean_html_text_block(text),
                    kind="text",
                    provenance=provenance,
                )

    write_text(md_path, "\n\n".join(part for part in md_parts if part).strip() + "\n")
    write_text(html_path, lean_html_document(html_parts))
    return {
        "files": {
            "llm_markdown": summarize_file(md_path),
            "llm_html": summarize_file(html_path),
        },
        "decisions": decisions,
        "table_bitmap_policy": effective_table_policy,
        "bitmap_mode": bitmap_mode,
        "referenced_asset_paths": list(dict.fromkeys(referenced_asset_paths)),
        "blocks": blocks,
    }


def estimate_llm_payload_budget(
    markdown_path: Path,
    referenced_asset_paths: list[str],
    *,
    hard_limit_mb: float = DEFAULT_LLM_HARD_LIMIT_MB,
    transport_headroom_mb: float = DEFAULT_LLM_TRANSPORT_HEADROOM_MB,
    prompt_schema_headroom_mb: float = DEFAULT_LLM_PROMPT_SCHEMA_HEADROOM_MB,
) -> dict[str, Any]:
    """Conservatively estimate a JSON/data-URL request against a hard byte cap."""

    unique_paths = list(dict.fromkeys(referenced_asset_paths))
    asset_bytes = 0
    serialized_asset_bytes = 0
    missing_assets: list[str] = []
    for path_value in unique_paths:
        path = Path(path_value)
        if not path.is_file():
            missing_assets.append(path_value)
            continue
        size = path.stat().st_size
        asset_bytes += size
        serialized_asset_bytes += 4 * ((size + 2) // 3) + 96

    markdown_bytes = markdown_path.stat().st_size if markdown_path.is_file() else 0
    converted_payload_bytes = markdown_bytes + serialized_asset_bytes
    prompt_schema_reserve_bytes = int(prompt_schema_headroom_mb * 1_000_000)
    transport_headroom_bytes = int(transport_headroom_mb * 1_000_000)
    hard_limit_bytes = int(hard_limit_mb * 1_000_000)
    estimated_total_bytes = (
        converted_payload_bytes + prompt_schema_reserve_bytes + transport_headroom_bytes
    )
    return {
        "hard_limit_bytes": hard_limit_bytes,
        "transport_headroom_bytes": transport_headroom_bytes,
        "prompt_schema_headroom_bytes": prompt_schema_reserve_bytes,
        "markdown_bytes": markdown_bytes,
        "asset_count": len(unique_paths),
        "asset_bytes": asset_bytes,
        "serialized_asset_bytes": serialized_asset_bytes,
        "converted_payload_bytes": converted_payload_bytes,
        "estimated_total_bytes": estimated_total_bytes,
        "fits": not missing_assets and estimated_total_bytes <= hard_limit_bytes,
        "missing_assets": missing_assets,
        "serialization_assumption": "base64_data_urls_plus_96_bytes_json_overhead_per_asset",
    }


def asset_data_url(path: Path, *, cache: dict[str, str] | None = None) -> str:
    cache_key = str(path)
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    value = f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
    if cache is not None:
        cache[cache_key] = value
    return value


def build_openai_batch_document_record(
    *,
    custom_id: str,
    markdown: str,
    asset_paths: list[str],
    model: str = DEFAULT_LLM_BATCH_MODEL,
    endpoint: str = DEFAULT_LLM_BATCH_ENDPOINT,
    prompt: str = DEFAULT_LLM_DOCUMENT_PROMPT,
    response_schema: dict[str, Any] | None = None,
    data_url_cache: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the same Chat Completions JSONL envelope used by the WBG batch lane."""

    if endpoint != "/v1/chat/completions":
        raise ValueError("Document bitmap packaging currently supports /v1/chat/completions only")
    content: list[dict[str, Any]] = [
        {"type": "text", "text": f"{prompt.strip()}\n\n{markdown.strip()}".strip()}
    ]
    for path_value in dict.fromkeys(asset_paths):
        path = Path(path_value)
        if not path.is_file():
            raise DoclingConvertError(f"Cannot serialize missing LLM asset: {path}")
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": asset_data_url(path, cache=data_url_cache),
                    "detail": "high",
                },
            }
        )
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
    }
    if response_schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "document_extraction",
                "strict": True,
                "schema": response_schema,
            },
        }
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": endpoint,
        "body": body,
    }


def serialize_jsonl_record(record: dict[str, Any]) -> bytes:
    return (
        json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=False) + "\n"
    ).encode("utf-8")


def blocks_to_page_units(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    units_by_page: dict[int, dict[str, Any]] = {}
    next_unpaged = 0
    for block in blocks:
        pages = [int(page) for page in block.get("page_nos") or []]
        if pages:
            page_no = min(pages)
        else:
            next_unpaged -= 1
            page_no = next_unpaged
        unit = units_by_page.setdefault(
            page_no,
            {
                "page_start": min(pages) if pages else None,
                "page_end": max(pages) if pages else None,
                "blocks": [],
                "starts_section": False,
            },
        )
        unit["blocks"].append(block)
        unit["starts_section"] = bool(unit["starts_section"] or block.get("starts_section"))
        if pages:
            unit["page_start"] = min(int(unit["page_start"]), min(pages))
            unit["page_end"] = max(int(unit["page_end"]), max(pages))
    return [units_by_page[key] for key in sorted(units_by_page, key=lambda page: (page < 0, page))]


def combine_llm_units(units: list[dict[str, Any]]) -> dict[str, Any]:
    blocks = [block for unit in units for block in unit.get("blocks") or []]
    pages = [
        int(page)
        for block in blocks
        for page in block.get("page_nos") or []
    ]
    return {
        "markdown": "\n\n".join(
            str(block.get("markdown") or "").strip()
            for block in blocks
            if str(block.get("markdown") or "").strip()
        ).strip()
        + "\n",
        "asset_paths": list(
            dict.fromkeys(
                str(path)
                for block in blocks
                for path in block.get("asset_paths") or []
            )
        ),
        "page_start": min(pages) if pages else None,
        "page_end": max(pages) if pages else None,
    }


def plan_openai_batch_shards(
    blocks: list[dict[str, Any]],
    *,
    stem: str,
    hard_limit_bytes: int,
    transport_headroom_bytes: int,
    prompt_schema_headroom_bytes: int,
    model: str = DEFAULT_LLM_BATCH_MODEL,
    endpoint: str = DEFAULT_LLM_BATCH_ENDPOINT,
    prompt: str = DEFAULT_LLM_DOCUMENT_PROMPT,
    response_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pack page-aligned request records, splitting before dropping bitmap evidence."""

    request_limit_bytes = (
        hard_limit_bytes - transport_headroom_bytes - prompt_schema_headroom_bytes
    )
    if request_limit_bytes <= 0:
        raise ValueError("Configured headroom leaves no bytes for the serialized request")
    units = blocks_to_page_units(blocks)
    if not units:
        units = [{"page_start": None, "page_end": None, "blocks": [], "starts_section": False}]
    data_url_cache: dict[str, str] = {}

    def render(candidate_units: list[dict[str, Any]], part_number: int) -> dict[str, Any]:
        combined = combine_llm_units(candidate_units)
        custom_id = f"{stem}-part-{part_number:04d}"
        record = build_openai_batch_document_record(
            custom_id=custom_id,
            markdown=combined["markdown"],
            asset_paths=combined["asset_paths"],
            model=model,
            endpoint=endpoint,
            prompt=prompt,
            response_schema=response_schema,
            data_url_cache=data_url_cache,
        )
        serialized = serialize_jsonl_record(record)
        return {
            **combined,
            "custom_id": custom_id,
            "record": record,
            "serialized": serialized,
            "serialized_bytes": len(serialized),
            "fits": len(serialized) <= request_limit_bytes,
        }

    whole = render(units, 1)
    if whole["fits"]:
        return {
            "shards": [whole],
            "sharded": False,
            "request_limit_bytes": request_limit_bytes,
        }

    shards: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for unit in units:
        candidate = [*pending, unit]
        candidate_result = render(candidate, len(shards) + 1)
        prefer_section_boundary = bool(
            pending
            and unit.get("starts_section")
            and candidate_result["serialized_bytes"] > request_limit_bytes * 0.80
        )
        if pending and (not candidate_result["fits"] or prefer_section_boundary):
            completed = render(pending, len(shards) + 1)
            if not completed["fits"]:
                return {
                    "shards": shards,
                    "sharded": True,
                    "request_limit_bytes": request_limit_bytes,
                    "oversized_unit": completed,
                }
            shards.append(completed)
            pending = [unit]
        else:
            pending = candidate
    if pending:
        completed = render(pending, len(shards) + 1)
        if not completed["fits"]:
            return {
                "shards": shards,
                "sharded": True,
                "request_limit_bytes": request_limit_bytes,
                "oversized_unit": completed,
            }
        shards.append(completed)
    return {
        "shards": shards,
        "sharded": True,
        "request_limit_bytes": request_limit_bytes,
    }


def write_openai_batch_shards(
    llm_dir: Path,
    *,
    stem: str,
    shard_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    requests_dir = ensure_dir(llm_dir / "requests")
    for old_path in requests_dir.glob(f"{stem}.part-*.jsonl"):
        old_path.unlink()
    for old_path in requests_dir.glob(f"{stem}.part-*.md"):
        old_path.unlink()
    summaries: list[dict[str, Any]] = []
    for part_number, shard in enumerate(shard_plan.get("shards") or [], start=1):
        jsonl_path = requests_dir / f"{stem}.part-{part_number:04d}.jsonl"
        markdown_path = requests_dir / f"{stem}.part-{part_number:04d}.md"
        jsonl_path.write_bytes(shard["serialized"])
        write_text(markdown_path, shard["markdown"])
        summaries.append(
            {
                "part_number": part_number,
                "custom_id": shard["custom_id"],
                "page_start": shard["page_start"],
                "page_end": shard["page_end"],
                "asset_count": len(shard["asset_paths"]),
                "asset_paths": shard["asset_paths"],
                "serialized_bytes": shard["serialized_bytes"],
                "jsonl": summarize_file(jsonl_path),
                "markdown": summarize_file(markdown_path),
            }
        )
    return summaries


def write_budgeted_llm_oriented_exports(
    document: Any,
    profile_dir: Path,
    artifacts: dict[str, Any],
    *,
    stem: str,
    table_bitmap_policy: str,
    hard_limit_mb: float,
    transport_headroom_mb: float,
    prompt_schema_headroom_mb: float,
    batch_model: str = DEFAULT_LLM_BATCH_MODEL,
    batch_endpoint: str = DEFAULT_LLM_BATCH_ENDPOINT,
    document_prompt: str = DEFAULT_LLM_DOCUMENT_PROMPT,
    response_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    hard_limit_bytes = int(hard_limit_mb * 1_000_000)
    transport_headroom_bytes = int(transport_headroom_mb * 1_000_000)
    prompt_schema_headroom_bytes = int(prompt_schema_headroom_mb * 1_000_000)
    for bitmap_mode in ("full", "tables_only", "mechanical"):
        result = write_llm_oriented_exports(
            document,
            profile_dir,
            artifacts,
            stem=stem,
            table_bitmap_policy=table_bitmap_policy,
            bitmap_mode=bitmap_mode,
        )
        markdown_path = Path(result["files"]["llm_markdown"]["path"])
        budget = estimate_llm_payload_budget(
            markdown_path,
            result["referenced_asset_paths"],
            hard_limit_mb=hard_limit_mb,
            transport_headroom_mb=transport_headroom_mb,
            prompt_schema_headroom_mb=prompt_schema_headroom_mb,
        )
        blocks = result.get("blocks") or [
            {
                "kind": "document",
                "markdown": markdown_path.read_text(encoding="utf-8"),
                "html": "",
                "page_nos": [],
                "asset_paths": result["referenced_asset_paths"],
                "starts_section": False,
            }
        ]
        shard_plan = plan_openai_batch_shards(
            blocks,
            stem=stem,
            hard_limit_bytes=hard_limit_bytes,
            transport_headroom_bytes=transport_headroom_bytes,
            prompt_schema_headroom_bytes=prompt_schema_headroom_bytes,
            model=batch_model,
            endpoint=batch_endpoint,
            prompt=document_prompt,
            response_schema=response_schema,
        )
        oversized_unit = shard_plan.get("oversized_unit")
        attempt = {
            "bitmap_mode": bitmap_mode,
            **budget,
            "exact_serialization": "openai_batch_chat_completions_jsonl",
            "request_limit_bytes": shard_plan["request_limit_bytes"],
            "sharded": bool(shard_plan["sharded"]),
            "shard_count": len(shard_plan.get("shards") or []),
            "exact_serialized_bytes": [
                shard["serialized_bytes"] for shard in shard_plan.get("shards") or []
            ],
            "oversized_unit_bytes": (
                int(oversized_unit["serialized_bytes"]) if oversized_unit is not None else None
            ),
        }
        attempts.append(attempt)
        if oversized_unit is None:
            shard_summaries = write_openai_batch_shards(
                profile_dir / "llm",
                stem=stem,
                shard_plan=shard_plan,
            )
            request_manifest_path = profile_dir / "llm" / "requests" / f"{stem}.manifest.json"
            request_manifest = {
                "format": "openai_batch_chat_completions_jsonl",
                "model": batch_model,
                "endpoint": batch_endpoint,
                "bitmap_mode": bitmap_mode,
                "hard_limit_bytes": hard_limit_bytes,
                "transport_headroom_bytes": transport_headroom_bytes,
                "prompt_schema_headroom_bytes": prompt_schema_headroom_bytes,
                "request_limit_bytes": shard_plan["request_limit_bytes"],
                "sharded": bool(shard_plan["sharded"]),
                "shards": shard_summaries,
            }
            write_json(request_manifest_path, request_manifest)
            result["files"]["llm_batch_request_manifest"] = summarize_file(
                request_manifest_path
            )
            result["request_shards"] = shard_summaries
            result["payload_budget"] = {
                **budget,
                "fits": True,
                "exact_serialization": "openai_batch_chat_completions_jsonl",
                "request_limit_bytes": shard_plan["request_limit_bytes"],
                "exact_serialized_bytes": [
                    shard["serialized_bytes"] for shard in shard_plan["shards"]
                ],
                "sharded": bool(shard_plan["sharded"]),
                "shard_count": len(shard_summaries),
                "selected_bitmap_mode": bitmap_mode,
                "fallback_applied": bitmap_mode != "full",
                "attempts": attempts,
            }
            return result
    raise DoclingConvertError(
        "LLM mechanical payload still exceeds the configured hard limit after "
        f"reserving transport and prompt/schema headroom: {attempts[-1]}"
    )


def compact_artifact_summary(artifacts: dict[str, Any]) -> dict[str, Any]:
    tables = artifacts.get("tables") or []
    pictures = artifacts.get("pictures") or []
    formulas = artifacts.get("formulas") or []
    complex_tables = [
        table
        for table in tables
        if (table.get("complexity") or {}).get("bitmap_required")
    ]
    return {
        "structure": artifacts.get("structure") or {},
        "table_count": len(tables),
        "picture_count": len(pictures),
        "formula_count": len(formulas),
        "complex_table_count": len(complex_tables),
        "table_bitmap_count": (artifacts.get("llm_rendering") or {}).get("table_bitmap_count", 0),
        "fallback_table_bitmap_count": (artifacts.get("llm_rendering") or {}).get("fallback_table_bitmap_count", 0),
        "simple_table_html_count": (artifacts.get("llm_rendering") or {}).get("simple_table_html_count", 0),
        "picture_bitmap_count": (artifacts.get("llm_rendering") or {}).get("picture_bitmap_count", 0),
        "formula_bitmap_count": (artifacts.get("llm_rendering") or {}).get("formula_bitmap_count", 0),
        "formula_placeholder_replacements": artifacts.get("formula_placeholder_replacements") or {},
        "llm_markdown": ((artifacts.get("files") or {}).get("llm_markdown") or {}).get("path"),
        "llm_html": ((artifacts.get("files") or {}).get("llm_html") or {}).get("path"),
        "llm_payload_budget": artifacts.get("llm_payload_budget") or {},
        "risk_routing": artifacts.get("risk_routing") or {},
    }


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    with contextlib.suppress(Exception):
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def discover_pdf_inputs(source_dir: Path) -> list[Path]:
    if not source_dir.is_dir():
        raise DoclingConvertError(f"PDF source directory not found: {source_dir}")
    return sorted(path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")


def select_batch_inputs(pdfs: list[Path], *, offset: int, limit: int | None) -> list[Path]:
    if offset < 0:
        raise ValueError("--offset must be non-negative")
    if limit is not None and limit < 1:
        raise ValueError("--limit must be positive when supplied")
    selected = pdfs[offset:]
    if limit is not None:
        selected = selected[:limit]
    return selected


def stable_document_id(path: Path, *, include_hash: bool = False) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._-") or "document"
    if include_hash:
        digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
        return f"{slug}-{digest}"
    return slug


def make_batch_input_records(
    pdfs: list[Path],
    *,
    start_index: int = 1,
    unique_document_ids: bool = False,
) -> list[BatchInput]:
    stems = [stable_document_id(path) for path in pdfs]
    duplicate_stems = {stem for stem in stems if stems.count(stem) > 1}
    records: list[BatchInput] = []
    for idx, path in enumerate(pdfs, start=start_index):
        base_id = stable_document_id(path)
        document_id = stable_document_id(
            path,
            include_hash=unique_document_ids and base_id in duplicate_stems,
        )
        records.append(BatchInput(batch_index=idx, document_id=document_id, input_pdf=path))
    return records


def iter_input_list_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def read_batch_input_list(path_spec: str) -> list[str]:
    if path_spec == "-":
        return iter_input_list_lines(sys.stdin.read())
    path = Path(path_spec).expanduser()
    if not path.is_file():
        raise DoclingConvertError(f"Input list file not found: {path}")
    return iter_input_list_lines(path.read_text(encoding="utf-8", errors="replace"))


def resolve_generic_batch_pdfs(args: argparse.Namespace) -> list[Path]:
    raw_inputs: list[str] = []
    if getattr(args, "input", None):
        raw_inputs.append(str(args.input))
    raw_inputs.extend(str(path) for path in getattr(args, "input_pdfs", []) or [])
    for list_path in getattr(args, "input_list", []) or []:
        raw_inputs.extend(read_batch_input_list(str(list_path)))
    if not raw_inputs and not sys.stdin.isatty():
        raw_inputs.extend(iter_input_list_lines(sys.stdin.read()))
    if not raw_inputs:
        raise DoclingConvertError(
            "No input PDFs supplied. Pass PDF paths after `batch`, use --input/--input-list, "
            "or pipe a newline-delimited list to stdin."
        )

    pdfs: list[Path] = []
    seen: set[Path] = set()
    for raw_input in raw_inputs:
        path = Path(raw_input).expanduser().resolve()
        if path in seen:
            continue
        if not path.is_file():
            raise DoclingConvertError(f"Input PDF not found: {path}")
        if path.suffix.lower() != ".pdf":
            raise DoclingConvertError(f"Input is not a PDF path: {path}")
        seen.add(path)
        pdfs.append(path)
    return pdfs


BATCH_SUMMARY_FIELDS = [
    "batch_index",
    "document_id",
    "input_pdf",
    "status",
    "returncode",
    "elapsed_seconds",
    "profile_count",
    "ok_profile_count",
    "failed_profile_count",
    "page_count",
    "table_count",
    "picture_count",
    "complex_table_count",
    "table_bitmap_count",
    "simple_table_html_count",
    "picture_bitmap_count",
    "output_dir",
    "run_manifest",
    "error",
]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def write_batch_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=BATCH_SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in BATCH_SUMMARY_FIELDS})
    return path


def summarize_document_run(
    *,
    batch_index: int,
    input_pdf: Path,
    output_dir: Path,
    run_manifest: dict[str, Any] | None,
    returncode: int | None,
    elapsed_seconds: float,
    document_id: str | None = None,
    skipped: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    profile_results = (run_manifest or {}).get("results") or []
    first_artifact_summary = next(
        (
            result.get("artifact_summary")
            for result in profile_results
            if result.get("artifact_summary")
        ),
        {},
    )
    structure = first_artifact_summary.get("structure") or {}
    status = "skipped" if skipped else (run_manifest or {}).get("status") or ("failed" if error else "unknown")
    row: dict[str, Any] = {
        "batch_index": batch_index,
        "document_id": document_id or input_pdf.stem,
        "input_pdf": str(input_pdf),
        "status": status,
        "returncode": "" if returncode is None else returncode,
        "elapsed_seconds": elapsed_seconds,
        "profile_count": len(profile_results),
        "ok_profile_count": sum(1 for result in profile_results if result.get("status") == "ok"),
        "failed_profile_count": sum(1 for result in profile_results if result.get("status") != "ok"),
        "page_count": structure.get("page_count_in_document", ""),
        "table_count": first_artifact_summary.get("table_count", ""),
        "picture_count": first_artifact_summary.get("picture_count", ""),
        "complex_table_count": first_artifact_summary.get("complex_table_count", ""),
        "table_bitmap_count": first_artifact_summary.get("table_bitmap_count", ""),
        "simple_table_html_count": first_artifact_summary.get("simple_table_html_count", ""),
        "picture_bitmap_count": first_artifact_summary.get("picture_bitmap_count", ""),
        "output_dir": str(output_dir),
        "run_manifest": str(output_dir / "manifest.json"),
        "error": error or (run_manifest or {}).get("error") or "",
        "profile_summaries": [
            {
                "profile": result.get("profile"),
                "status": result.get("status"),
                "elapsed_seconds": result.get("elapsed_seconds"),
                "artifact_summary": result.get("artifact_summary"),
                "error": result.get("error"),
            }
            for result in profile_results
        ],
    }
    return row


def write_batch_outputs(batch_dir: Path, manifest: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    manifest["results"] = rows
    manifest["ok_count"] = sum(1 for row in rows if row.get("status") == "ok")
    manifest["partial_count"] = sum(1 for row in rows if row.get("status") == "partial")
    manifest["failed_count"] = sum(1 for row in rows if row.get("status") == "failed")
    manifest["skipped_count"] = sum(1 for row in rows if row.get("status") == "skipped")
    manifest["summary_csv"] = str(batch_dir / "batch_summary.csv")
    manifest["summary_jsonl"] = str(batch_dir / "batch_results.jsonl")
    write_json(batch_dir / "manifest.json", manifest)
    write_batch_csv(batch_dir / "batch_summary.csv", rows)
    write_jsonl(batch_dir / "batch_results.jsonl", rows)


def add_flag_value(command: list[str], flag: str, value: Any | None) -> None:
    if value is not None:
        command.extend([flag, str(value)])


def build_convert_subprocess_command(args: argparse.Namespace, *, input_pdf: Path, document_dir: Path) -> list[str]:
    """Build a fresh-process convert command from batch args.

    GPU model memory is not reliably released when many Docling conversions run
    sequentially in one Python process. Batch mode therefore treats the document
    as the teardown boundary and invokes this module's `convert` command in a
    child process for every PDF.
    """

    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "convert",
        "--input",
        str(input_pdf),
        "--output-dir",
        str(document_dir),
        "--profiles",
        str(args.profiles),
        "--device",
        str(args.device),
        "--image-mode",
        str(args.image_mode),
        "--table-bitmap-policy",
        str(args.table_bitmap_policy),
        "--table-complexity-threshold",
        str(args.table_complexity_threshold),
        "--llm-hard-limit-mb",
        str(args.llm_hard_limit_mb),
        "--llm-transport-headroom-mb",
        str(args.llm_transport_headroom_mb),
        "--llm-prompt-schema-headroom-mb",
        str(args.llm_prompt_schema_headroom_mb),
        "--llm-batch-model",
        str(args.llm_batch_model),
        "--llm-batch-endpoint",
        str(args.llm_batch_endpoint),
        "--ocr-engine",
        str(args.ocr_engine),
        "--images-scale",
        str(args.images_scale),
        "--ocr-batch-size",
        str(args.ocr_batch_size),
        "--layout-batch-size",
        str(args.layout_batch_size),
        "--table-batch-size",
        str(args.table_batch_size),
        "--vlm-batch-size",
        str(args.vlm_batch_size),
        "--max-new-tokens",
        str(args.max_new_tokens),
        "--hf-artifacts-root",
        str(args.hf_artifacts_root),
    ]
    add_flag_value(command, "--pages", args.pages)
    add_flag_value(command, "--reference-html", args.reference_html)
    add_flag_value(command, "--llm-prompt-file", args.llm_prompt_file)
    add_flag_value(command, "--llm-schema-file", args.llm_schema_file)
    for language in args.ocr_lang or []:
        command.extend(["--ocr-lang", str(language)])
    command.append("--no-picture-classification" if args.no_picture_classification else "--picture-classification")
    command.append("--no-picture-description" if args.no_picture_description else "--picture-description")
    for flag, attr in [
        ("--allow-no-cuda", "allow_no_cuda"),
        ("--write-embedded", "write_embedded"),
        ("--no-ocr", "no_ocr"),
        ("--compile-picture-classifier", "compile_picture_classifier"),
        ("--enrich-code", "enrich_code"),
        ("--enrich-formula", "enrich_formula"),
        ("--generate-page-images", "generate_page_images"),
        ("--force-backend-text", "force_backend_text"),
        ("--no-materialize-large-model", "no_materialize_large_model"),
        ("--fail-fast", "fail_fast"),
    ]:
        if getattr(args, attr, False):
            command.append(flag)
    return command


def run_document_convert_subprocess(
    args: argparse.Namespace,
    *,
    input_pdf: Path,
    document_dir: Path,
) -> tuple[int, Path, str | None]:
    log_dir = ensure_dir(document_dir / "logs")
    log_path = log_dir / "convert-subprocess.log"
    command = build_convert_subprocess_command(args, input_pdf=input_pdf, document_dir=document_dir)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    if getattr(args, "device", None) == "cpu" and int(getattr(args, "jobs", 1) or 1) > 1:
        worker_threads = str(max(1, int(getattr(args, "worker_threads", 2) or 2)))
        for name in ["OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"]:
            env.setdefault(name, worker_threads)
        env.setdefault("TOKENIZERS_PARALLELISM", "false")
    timeout_seconds = getattr(args, "document_timeout_seconds", None)
    if timeout_seconds is not None and timeout_seconds <= 0:
        timeout_seconds = None
    with log_path.open("w", encoding="utf-8") as log_handle:
        log_handle.write("$ " + " ".join(shlex_quote(part) for part in command) + "\n\n")
        if timeout_seconds is not None:
            log_handle.write(f"# document_timeout_seconds={timeout_seconds}\n\n")
        log_handle.flush()
        try:
            completed = subprocess.run(
                command,
                cwd=str(REPO_ROOT),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout_seconds,
            )
            return int(completed.returncode), log_path, None
        except subprocess.TimeoutExpired:
            message = f"SubprocessTimeout: document conversion exceeded {timeout_seconds} seconds"
            log_handle.write("\n" + message + "\n")
            return 124, log_path, message


def shlex_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def normalize_subprocess_manifest(
    *,
    manifest_path: Path,
    input_pdf: Path,
    returncode: int,
    log_path: Path | None,
    error: str | None = None,
) -> dict[str, Any]:
    run_manifest = read_json_if_exists(manifest_path)
    if run_manifest is None:
        run_manifest = {
            "status": "failed",
            "input_pdf": str(input_pdf),
            "results": [],
            "error_type": "SubprocessTimeout" if error and error.startswith("SubprocessTimeout") else "SubprocessInterrupted",
            "error": error or f"conversion subprocess did not write a manifest; returncode={returncode}",
        }
        if log_path is not None:
            run_manifest["subprocess_log"] = str(log_path)
        write_json(manifest_path, run_manifest)
        return run_manifest

    if run_manifest.get("status") == "running":
        error_type = "SubprocessTimeout" if error and error.startswith("SubprocessTimeout") else "SubprocessInterrupted"
        run_manifest.update(
            {
                "status": "failed",
                "finished_at_utc": utc_now_iso(),
                "error_type": error_type,
                "error": error
                or f"conversion subprocess exited before completing manifest; returncode={returncode}",
            }
        )
        if log_path is not None:
            run_manifest["subprocess_log"] = str(log_path)
        write_json(manifest_path, run_manifest)
    return run_manifest


def ordered_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: int(row.get("batch_index") or 0))


def validate_batch_parallelism(args: argparse.Namespace, *, selected_count: int) -> None:
    jobs = int(getattr(args, "jobs", 1) or 1)
    if jobs < 1:
        raise ValueError("--jobs must be positive")
    if int(getattr(args, "worker_threads", 2) or 2) < 1:
        raise ValueError("--worker-threads must be positive")
    if not getattr(args, "dry_run", False) and jobs > 1 and args.device != "cpu" and not args.allow_local_gpu_stress:
        raise DoclingConvertError(
            "Document-level parallelism is CPU-only by default. "
            "Use --device cpu for --jobs > 1, or pass --allow-local-gpu-stress only after explicit approval."
        )
    if (
        not getattr(args, "dry_run", False)
        and args.device == "cuda"
        and selected_count > 3
        and not args.allow_local_gpu_stress
    ):
        raise DoclingConvertError(
            "Refusing to run a large local CUDA batch without --allow-local-gpu-stress. "
            "This workload can saturate laptop GPU memory for long periods and may destabilize "
            "the Windows host. Use --limit 3 for a smoke run, --device cpu for a slow safe run, "
            "or pass --allow-local-gpu-stress only after explicit user approval."
        )


def dry_run_rows(batch_dir: Path, selected_inputs: list[BatchInput]) -> list[dict[str, Any]]:
    return [
        {
            "batch_index": item.batch_index,
            "document_id": item.document_id,
            "input_pdf": str(item.input_pdf),
            "status": "dry_run",
            "output_dir": str(batch_dir / item.document_id),
            "run_manifest": str(batch_dir / item.document_id / "manifest.json"),
        }
        for item in selected_inputs
    ]


def run_one_batch_document(args: argparse.Namespace, *, batch_dir: Path, batch_input: BatchInput) -> dict[str, Any]:
    document_dir = ensure_dir(batch_dir / batch_input.document_id)
    manifest_path = document_dir / "manifest.json"
    existing_manifest = read_json_if_exists(manifest_path)
    if existing_manifest and (
        args.skip_existing or (args.resume and existing_manifest.get("status") == "ok")
    ):
        return summarize_document_run(
            batch_index=batch_input.batch_index,
            document_id=batch_input.document_id,
            input_pdf=batch_input.input_pdf,
            output_dir=document_dir,
            run_manifest=existing_manifest,
            returncode=None,
            elapsed_seconds=0.0,
            skipped=True,
        )

    start_time = time.perf_counter()
    returncode = 1
    error: str | None = None
    log_path: Path | None = None
    try:
        returncode, log_path, subprocess_error = run_document_convert_subprocess(
            args,
            input_pdf=batch_input.input_pdf,
            document_dir=document_dir,
        )
        if subprocess_error:
            error = subprocess_error
    except Exception as exc:  # pragma: no cover - depends on external runtime
        error = f"{type(exc).__name__}: {exc}"
        write_text(
            document_dir / "logs" / "batch-error.txt",
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )
    elapsed = time.perf_counter() - start_time
    run_manifest = normalize_subprocess_manifest(
        manifest_path=manifest_path,
        input_pdf=batch_input.input_pdf,
        returncode=returncode,
        log_path=log_path,
        error=error,
    )
    return summarize_document_run(
        batch_index=batch_input.batch_index,
        document_id=batch_input.document_id,
        input_pdf=batch_input.input_pdf,
        output_dir=document_dir,
        run_manifest=run_manifest,
        returncode=returncode,
        elapsed_seconds=elapsed,
        error=error,
    )


def run_batch_documents(
    args: argparse.Namespace,
    *,
    batch_dir: Path,
    batch_manifest: dict[str, Any],
    selected_inputs: list[BatchInput],
    batch_started_perf: float,
) -> int:
    rows: list[dict[str, Any]] = []
    if args.dry_run:
        rows = dry_run_rows(batch_dir, selected_inputs)
        batch_manifest["status"] = "dry_run"
        batch_manifest["finished_at_utc"] = utc_now_iso()
        write_batch_outputs(batch_dir, batch_manifest, rows)
        payload = {
            "manifest": str(batch_dir / "manifest.json"),
            "summary_csv": str(batch_dir / "batch_summary.csv"),
            "summary_jsonl": str(batch_dir / "batch_results.jsonl"),
            "status": "dry_run",
            "selected_pdf_count": len(selected_inputs),
        }
        for key in ["batch_kind", "discovered_pdf_count"]:
            if key in batch_manifest:
                payload[key] = batch_manifest[key]
        print(
            json.dumps(payload, indent=2)
        )
        return 0

    write_batch_outputs(batch_dir, batch_manifest, rows)
    failed = False
    stop_reason: str | None = None
    jobs = min(int(args.jobs), max(len(selected_inputs), 1))
    next_index = 0
    futures: dict[Future[dict[str, Any]], BatchInput] = {}

    def may_submit_more() -> bool:
        nonlocal stop_reason
        if next_index >= len(selected_inputs):
            return False
        if failed and args.fail_fast:
            stop_reason = stop_reason or "fail_fast"
            return False
        if (
            args.max_runtime_seconds is not None
            and time.perf_counter() - batch_started_perf >= args.max_runtime_seconds
        ):
            stop_reason = stop_reason or "max_runtime_seconds_reached_before_next_document"
            return False
        return True

    def submit_next(executor: ThreadPoolExecutor) -> None:
        nonlocal next_index
        batch_input = selected_inputs[next_index]
        next_index += 1
        futures[
            executor.submit(
                run_one_batch_document,
                args,
                batch_dir=batch_dir,
                batch_input=batch_input,
            )
        ] = batch_input

    with ThreadPoolExecutor(max_workers=jobs) as executor:
        while len(futures) < jobs and may_submit_more():
            submit_next(executor)

        while futures:
            done, _pending = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                batch_input = futures.pop(future)
                try:
                    row = future.result()
                except Exception as exc:  # pragma: no cover - defensive
                    row = {
                        "batch_index": batch_input.batch_index,
                        "document_id": batch_input.document_id,
                        "input_pdf": str(batch_input.input_pdf),
                        "status": "failed",
                        "returncode": "",
                        "elapsed_seconds": 0.0,
                        "output_dir": str(batch_dir / batch_input.document_id),
                        "run_manifest": str(batch_dir / batch_input.document_id / "manifest.json"),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                rows.append(row)
                failed = failed or row.get("status") == "failed" or row.get("returncode") not in {"", 0, "0"}
                batch_manifest["status"] = "partial" if failed else "running"
                batch_manifest["elapsed_seconds"] = time.perf_counter() - batch_started_perf
                write_batch_outputs(batch_dir, batch_manifest, ordered_rows(rows))

            while len(futures) < jobs and may_submit_more():
                submit_next(executor)

    batch_manifest["finished_at_utc"] = utc_now_iso()
    batch_manifest["elapsed_seconds"] = time.perf_counter() - batch_started_perf
    if stop_reason is not None:
        batch_manifest["stop_reason"] = stop_reason
    if len(rows) < len(selected_inputs):
        batch_manifest["status"] = "partial"
    elif any(row.get("status") == "failed" for row in rows):
        batch_manifest["status"] = "partial" if any(row.get("status") == "ok" for row in rows) else "failed"
    else:
        batch_manifest["status"] = "ok"
    write_batch_outputs(batch_dir, batch_manifest, ordered_rows(rows))
    print(
        json.dumps(
            {
                "manifest": str(batch_dir / "manifest.json"),
                "summary_csv": str(batch_dir / "batch_summary.csv"),
                "summary_jsonl": str(batch_dir / "batch_results.jsonl"),
                "status": batch_manifest["status"],
                "processed_count": len(rows),
                "selected_pdf_count": len(selected_inputs),
                "jobs": jobs,
            },
            indent=2,
        )
    )
    return 0 if batch_manifest["status"] == "ok" else 1


def save_core_artifacts(
    document: Any,
    profile_dir: Path,
    *,
    input_pdf: Path | None,
    stem: str,
    image_mode_name: str,
    write_embedded: bool,
    table_bitmap_policy: str,
    table_complexity_threshold: int,
    llm_hard_limit_mb: float,
    llm_transport_headroom_mb: float,
    llm_prompt_schema_headroom_mb: float,
    llm_batch_model: str,
    llm_batch_endpoint: str,
    llm_document_prompt: str,
    llm_response_schema: dict[str, Any] | None,
) -> dict[str, Any]:
    from docling_core.types.doc import FormulaItem, PictureItem, TableItem

    image_mode = image_ref_mode(image_mode_name)
    docling_dir = ensure_dir(profile_dir / "docling")
    markdown_dir = ensure_dir(profile_dir / "markdown")
    html_dir = ensure_dir(profile_dir / "html")
    tables_dir = ensure_dir(profile_dir / "tables")
    images_dir = ensure_dir(profile_dir / "images")
    formulas_dir = ensure_dir(profile_dir / "formulas")

    artifacts: dict[str, Any] = {
        "files": {},
        "tables": [],
        "pictures": [],
        "formulas": [],
        "fallback_table_bitmaps": [],
        "pages": [],
    }

    json_path = docling_dir / f"{stem}.docling.json"
    document.save_as_json(json_path, artifacts_dir=images_dir, image_mode=image_mode, indent=2)
    artifacts["files"]["docling_json"] = summarize_file(json_path)

    md_path = markdown_dir / f"{stem}.md"
    document.save_as_markdown(md_path, artifacts_dir=images_dir, image_mode=image_mode, page_break_placeholder="\n\n<!-- page-break -->\n\n")
    artifacts["files"]["markdown"] = summarize_file(md_path)

    html_path = html_dir / f"{stem}.html"
    document.save_as_html(html_path, artifacts_dir=images_dir, image_mode=image_mode)
    artifacts["files"]["html"] = summarize_file(html_path)

    if write_embedded:
        embedded_md = markdown_dir / f"{stem}.embedded.md"
        document.save_as_markdown(embedded_md, image_mode=image_ref_mode("embedded"), page_break_placeholder="\n\n<!-- page-break -->\n\n")
        artifacts["files"]["markdown_embedded"] = summarize_file(embedded_md)
        embedded_html = html_dir / f"{stem}.embedded.html"
        document.save_as_html(embedded_html, image_mode=image_ref_mode("embedded"))
        artifacts["files"]["html_embedded"] = summarize_file(embedded_html)

    doctags_path = docling_dir / f"{stem}.doctags"
    with contextlib.suppress(Exception):
        write_text(doctags_path, document.export_to_doctags())
        artifacts["files"]["doctags"] = summarize_file(doctags_path)

    doclang_path = docling_dir / f"{stem}.doclang"
    with contextlib.suppress(Exception):
        maybe_doclang = document.export_to_doctags()
        if maybe_doclang:
            write_text(doclang_path, str(maybe_doclang))
            artifacts["files"]["doclang_or_document_tokens"] = summarize_file(doclang_path)

    table_count = 0
    picture_count = 0
    formula_count = 0
    text_records: list[dict[str, Any]] = []
    table_elements: list[Any] = []
    for element, level in document.iterate_items():
        if isinstance(element, TableItem):
            table_elements.append(element)
            table_count += 1
            table_record: dict[str, Any] = {"index": table_count, "level": level}
            table_record["provenance"] = item_provenance_records(element)
            table_html_text = ""
            csv_rows: list[list[str]] = []
            table_html = tables_dir / f"table-{table_count:04d}.html"
            with contextlib.suppress(Exception):
                table_html_text = element.export_to_html(doc=document)
                write_text(table_html, table_html_text)
                table_record["html"] = summarize_file(table_html)
            table_csv = tables_dir / f"table-{table_count:04d}.csv"
            with contextlib.suppress(Exception):
                df = element.export_to_dataframe(doc=document)
                df.to_csv(table_csv, index=False, quoting=csv.QUOTE_MINIMAL)
                table_record["csv"] = summarize_file(table_csv)
                csv_rows = read_csv_rows(table_csv)
            table_md = tables_dir / f"table-{table_count:04d}.md"
            table_md_text = ""
            with contextlib.suppress(Exception):
                table_md_text = element.export_to_markdown(doc=document)
                write_text(table_md, table_md_text)
                table_record["markdown"] = summarize_file(table_md)
            table_png = tables_dir / f"table-{table_count:04d}.png"
            with contextlib.suppress(Exception):
                element.get_image(document).save(table_png, "PNG")
                table_record["image"] = summarize_file(table_png)
            if not csv_rows:
                csv_rows = read_csv_rows(table_csv)
            native_structure = table_structure_metrics(element)
            provenance_pages = page_set_from_records([table_record])
            table_record["complexity"] = analyze_table_complexity(
                csv_rows,
                markdown_text=table_md_text or read_text_if_exists(table_md),
                html_text=table_html_text or read_text_if_exists(table_html),
                native_structure=native_structure,
                provenance_pages=provenance_pages,
                threshold=table_complexity_threshold,
            )
            artifacts["tables"].append(table_record)
        elif isinstance(element, PictureItem):
            picture_count += 1
            picture_record: dict[str, Any] = {"index": picture_count, "level": level}
            picture_record["provenance"] = item_provenance_records(element)
            with contextlib.suppress(Exception):
                picture_record["caption"] = element.caption_text(document)
            picture_png = images_dir / f"picture-{picture_count:04d}.png"
            with contextlib.suppress(Exception):
                element.get_image(document).save(picture_png, "PNG")
                picture_record["image"] = summarize_file(picture_png)
            picture_record["render_decision"] = classify_picture_for_llm_bitmap(
                picture_png if picture_png.exists() else None,
                caption=str(picture_record.get("caption") or ""),
                provenance=picture_record["provenance"],
                page_size=(
                    page_size_points(
                        document,
                        int(picture_record["provenance"][0]["page_no"]),
                    )
                    if picture_record["provenance"]
                    else None
                ),
            )
            if picture_record["render_decision"].get("bitmap_required"):
                with contextlib.suppress(Exception):
                    llm_image = build_page_width_picture_bitmap(
                        input_pdf=input_pdf,
                        document=document,
                        element=element,
                        images_dir=images_dir,
                        picture_index=picture_count,
                    )
                    if llm_image is not None:
                        picture_record["llm_image"] = llm_image
                        picture_record["render_decision"]["bitmap_source"] = llm_image.get("source")
                if "bitmap_source" not in picture_record["render_decision"]:
                    picture_record["render_decision"]["bitmap_source"] = "docling_picture_crop"
            artifacts["pictures"].append(picture_record)
        elif isinstance(element, FormulaItem):
            formula_count += 1
            formula_record: dict[str, Any] = {"index": formula_count, "level": level}
            formula_record["provenance"] = item_provenance_records(element)
            for attr in ["orig", "text"]:
                value = getattr(element, attr, None)
                if value:
                    formula_record[attr] = str(value)
            with contextlib.suppress(Exception):
                image_info = build_formula_bitmap(
                    input_pdf=input_pdf,
                    document=document,
                    element=element,
                    formulas_dir=formulas_dir,
                    formula_index=formula_count,
                )
                if image_info is not None:
                    formula_record["image"] = image_info
            formula_record["render_decision"] = {
                "llm_render": "bitmap" if (formula_record.get("image") or {}).get("path") else "text_or_placeholder",
                "bitmap_source": (formula_record.get("image") or {}).get("source"),
            }
            artifacts["formulas"].append(formula_record)
        else:
            provenance = item_provenance_records(element)
            if provenance:
                text = item_to_markdown(element, document)
                if text:
                    text_records.append(
                        {
                            "page_no": provenance[0].get("page_no"),
                            "text": text,
                            "provenance": provenance,
                        }
                    )

    mark_cross_page_table_sequences(artifacts["tables"])
    for table_index, (table_record, element) in enumerate(
        zip(artifacts["tables"], table_elements, strict=True),
        start=1,
    ):
        table_record["render_decision"] = {
            "llm_render": (
                "bitmap"
                if table_should_render_as_bitmap(table_record, policy=table_bitmap_policy)
                else "html_table"
            ),
            "table_bitmap_policy": table_bitmap_policy,
        }
        if table_record["render_decision"]["llm_render"] != "bitmap":
            continue
        try:
            llm_image = build_page_width_table_bitmap(
                input_pdf=input_pdf,
                document=document,
                element=element,
                tables_dir=tables_dir,
                table_index=table_index,
            )
        except Exception as exc:
            raise DoclingConvertError(
                f"Failed to build source-page bitmap for risky table {table_index}: {exc}"
            ) from exc
        if llm_image is None:
            raise DoclingConvertError(
                f"Risky table {table_index} has no source-page bitmap; refusing silent HTML fallback."
            )
        table_record["llm_image"] = llm_image
        table_record["render_decision"]["bitmap_source"] = llm_image.get("source")

    repeated_furniture_suppressed = suppress_repeated_picture_furniture(artifacts["pictures"])
    artifacts.setdefault("fallbacks", {})[
        "repeated_picture_furniture_suppressed"
    ] = repeated_furniture_suppressed
    artifacts["fallbacks"]["photo_credit_decorations_suppressed"] = (
        suppress_photo_credit_decorations(artifacts["pictures"], text_records)
    )
    add_clustered_picture_fallbacks(
        artifacts=artifacts,
        input_pdf=input_pdf,
        document=document,
        images_dir=images_dir,
    )
    add_captioned_table_fallbacks(
        artifacts=artifacts,
        text_records=text_records,
        input_pdf=input_pdf,
        document=document,
        tables_dir=tables_dir,
    )

    with contextlib.suppress(Exception):
        for page_no, page in sorted(document.pages.items()):
            page_record: dict[str, Any] = {"page_no": page_no}
            page_image = getattr(page, "image", None)
            pil_image = getattr(page_image, "pil_image", None)
            if pil_image is not None:
                page_png = images_dir / f"page-{int(page_no):04d}.png"
                pil_image.save(page_png, "PNG")
                page_record["image"] = summarize_file(page_png)
            artifacts["pages"].append(page_record)

    structure = {
        "table_count": len(getattr(document, "tables", []) or []),
        "picture_count": len(getattr(document, "pictures", []) or []),
        "formula_count": len(artifacts["formulas"]),
        "page_count_in_document": len(getattr(document, "pages", {}) or {}),
        "exported_table_records": len(artifacts["tables"]),
        "exported_picture_records": len(artifacts["pictures"]),
        "exported_formula_records": len(artifacts["formulas"]),
    }
    artifacts["structure"] = structure
    complex_table_pages = sorted(
        page_set_from_records(
            [
                record
                for record in artifacts["tables"]
                if (record.get("complexity") or {}).get("bitmap_required")
            ]
        )
    )
    fallback_table_pages = sorted(
        page_set_from_records(artifacts.get("fallback_table_bitmaps") or [])
    )
    artifacts["risk_routing"] = {
        "complex_table_pages": complex_table_pages,
        "fallback_table_pages": fallback_table_pages,
        "table_continuation_groups": sorted(
            {
                str(record["continuation_group_id"])
                for record in artifacts["tables"]
                if record.get("continuation_group_id")
            }
        ),
        "granite_second_pass_candidate_pages": sorted(
            set(complex_table_pages).union(fallback_table_pages)
        ),
        "granite_second_pass_is_optional": True,
    }
    formula_replacements = replace_raw_formula_placeholders(
        artifacts=artifacts,
        formulas=artifacts["formulas"],
    )
    if formula_replacements:
        artifacts["formula_placeholder_replacements"] = formula_replacements
    llm_exports = write_budgeted_llm_oriented_exports(
        document,
        profile_dir,
        artifacts,
        stem=stem,
        table_bitmap_policy=table_bitmap_policy,
        hard_limit_mb=llm_hard_limit_mb,
        transport_headroom_mb=llm_transport_headroom_mb,
        prompt_schema_headroom_mb=llm_prompt_schema_headroom_mb,
        batch_model=llm_batch_model,
        batch_endpoint=llm_batch_endpoint,
        document_prompt=llm_document_prompt,
        response_schema=llm_response_schema,
    )
    artifacts["files"].update(llm_exports["files"])
    artifacts["llm_rendering"] = llm_exports["decisions"] | {
        "table_bitmap_policy": llm_exports["table_bitmap_policy"],
        "bitmap_mode": llm_exports["bitmap_mode"],
    }
    artifacts["llm_payload_budget"] = llm_exports["payload_budget"]
    write_json(profile_dir / "diagnostics" / "structure.json", {**structure, "artifacts": artifacts})
    return artifacts


def run_one_profile(profile: str, args: argparse.Namespace, *, input_pdf: Path, run_dir: Path, page_selection: PageSelection | None) -> dict[str, Any]:
    profile_dir = ensure_dir(run_dir / profile)
    log_dir = ensure_dir(profile_dir / "logs")
    stem = input_pdf.stem
    start_time = time.perf_counter()
    before_gpu = collect_gpu_diagnostics()
    manifest: dict[str, Any] = {
        "status": "running",
        "profile": profile,
        "conceptual_lane": profile_conceptual_lane(profile),
        "input_pdf": str(input_pdf),
        "page_selection": None if page_selection is None else page_selection.__dict__,
        "started_at_utc": utc_now_iso(),
        "device": args.device,
        "image_mode": args.image_mode,
        "table_bitmap_policy": args.table_bitmap_policy,
        "table_complexity_threshold": args.table_complexity_threshold,
        "code_enrichment_requested": bool(args.enrich_code),
        "formula_enrichment_requested": bool(args.enrich_formula),
        "chart_extraction_requested": False,
        "picture_classification_requested": not args.no_picture_classification,
        "picture_description_requested": not args.no_picture_description,
        "picture_description_model": None if args.no_picture_description else "ibm-granite/granite-vision-3.3-2b",
        "gpu_before": before_gpu,
        "versions": {
            package: maybe_import_version(package)
            for package in ["docling", "docling-core", "torch", "transformers", "bitsandbytes"]
        },
    }
    write_json(profile_dir / "manifest.json", manifest)
    try:
        converter = build_converter(profile, args)
        convert_kwargs: dict[str, Any] = {"raises_on_error": True}
        if page_selection is not None:
            convert_kwargs["page_range"] = page_selection.docling_range
        conversion = converter.convert(input_pdf, **convert_kwargs)
        document = conversion.document
        artifacts = save_core_artifacts(
            document,
            profile_dir,
            input_pdf=input_pdf,
            stem=stem,
            image_mode_name=args.image_mode,
            write_embedded=args.write_embedded,
            table_bitmap_policy=args.table_bitmap_policy,
            table_complexity_threshold=args.table_complexity_threshold,
            llm_hard_limit_mb=args.llm_hard_limit_mb,
            llm_transport_headroom_mb=args.llm_transport_headroom_mb,
            llm_prompt_schema_headroom_mb=args.llm_prompt_schema_headroom_mb,
            llm_batch_model=args.llm_batch_model,
            llm_batch_endpoint=args.llm_batch_endpoint,
            llm_document_prompt=(
                Path(args.llm_prompt_file).expanduser().read_text(encoding="utf-8")
                if args.llm_prompt_file
                else DEFAULT_LLM_DOCUMENT_PROMPT
            ),
            llm_response_schema=(
                json.loads(Path(args.llm_schema_file).expanduser().read_text(encoding="utf-8"))
                if args.llm_schema_file
                else None
            ),
        )
        elapsed = time.perf_counter() - start_time
        manifest.update(
            {
                "status": "ok",
                "finished_at_utc": utc_now_iso(),
                "elapsed_seconds": elapsed,
                "artifacts": artifacts,
                "gpu_after": collect_gpu_diagnostics(),
            }
        )
        # Docling conversion object shape changes across releases; keep best-effort metadata only.
        with contextlib.suppress(Exception):
            manifest["conversion_status"] = str(conversion.status)
        with contextlib.suppress(Exception):
            manifest["input_page_count"] = getattr(conversion.input, "page_count", None)
        return manifest
    except Exception as exc:
        elapsed = time.perf_counter() - start_time
        error_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        write_text(log_dir / "error.txt", error_text)
        manifest.update(
            {
                "status": "failed",
                "finished_at_utc": utc_now_iso(),
                "elapsed_seconds": elapsed,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback_path": str(log_dir / "error.txt"),
                "gpu_after": collect_gpu_diagnostics(),
            }
        )
        return manifest
    finally:
        write_json(profile_dir / "manifest.json", manifest)


def run_convert(args: argparse.Namespace) -> int:
    input_pdf = Path(args.input).expanduser().resolve()
    if not input_pdf.is_file():
        raise DoclingConvertError(f"Input PDF not found: {input_pdf}")
    profiles = expand_profiles(args.profiles)
    page_selection = parse_page_selection(args.pages)
    if args.llm_hard_limit_mb <= 0:
        raise ValueError("--llm-hard-limit-mb must be positive")
    if args.llm_transport_headroom_mb < 0 or args.llm_prompt_schema_headroom_mb < 0:
        raise ValueError("LLM payload headroom values cannot be negative")
    if args.llm_transport_headroom_mb + args.llm_prompt_schema_headroom_mb >= args.llm_hard_limit_mb:
        raise ValueError("LLM payload headroom must leave positive space below the hard limit")
    for label, path_value in [
        ("--llm-prompt-file", args.llm_prompt_file),
        ("--llm-schema-file", args.llm_schema_file),
    ]:
        if path_value and not Path(path_value).expanduser().is_file():
            raise ValueError(f"{label} does not exist: {path_value}")
    if args.llm_schema_file:
        schema = json.loads(Path(args.llm_schema_file).expanduser().read_text(encoding="utf-8"))
        if not isinstance(schema, dict):
            raise ValueError("--llm-schema-file must contain a JSON object")
    require_cuda_if_requested(args.device, allow_no_cuda=args.allow_no_cuda)

    output_dir = Path(args.output_dir).expanduser().resolve()
    run_dir = ensure_dir(output_dir)
    run_manifest: dict[str, Any] = {
        "status": "running",
        "started_at_utc": utc_now_iso(),
        "input_pdf": str(input_pdf),
        "profiles": profiles,
        "page_selection": None if page_selection is None else page_selection.__dict__,
        "reference_html": str(Path(args.reference_html).expanduser().resolve()) if args.reference_html else None,
        "reference_html_exists": bool(args.reference_html and Path(args.reference_html).expanduser().is_file()),
        "default_image_policy": args.image_mode,
        "notes": [
            "Images are referenced by default; embedded Markdown/HTML is optional.",
            "LLM-oriented exports render simple tables as HTML tables and complex tables/figures as referenced bitmaps.",
            "Chart extraction enrichment is intentionally disabled.",
            "Picture description and picture classification are disabled by default; image retention uses deterministic geometry/caption rules.",
            "Code/formula enrichment is disabled by default; enable it explicitly with --enrich-code or --enrich-formula.",
            "Granite-Docling-258M is the default VLM comparison lane; Granite Vision lanes are explicit opt-in profiles.",
            "LLM request shards use the OpenAI Batch Chat Completions JSONL envelope, are measured after exact data-URL serialization, and reserve transport plus prompt/schema headroom below 200 MB by default.",
        ],
        "gpu_initial": collect_gpu_diagnostics(),
    }
    write_json(run_dir / "manifest.json", run_manifest)

    results = []
    for profile in profiles:
        result = run_one_profile(profile, args, input_pdf=input_pdf, run_dir=run_dir, page_selection=page_selection)
        result_summary = {k: v for k, v in result.items() if k != "artifacts"}
        if result.get("artifacts"):
            result_summary["artifact_summary"] = compact_artifact_summary(result["artifacts"])
        results.append(result_summary)
        if result.get("status") != "ok" and args.fail_fast:
            break

    ok = [result for result in results if result.get("status") == "ok"]
    failed = [result for result in results if result.get("status") != "ok"]
    run_manifest.update(
        {
            "status": "ok" if not failed else "partial" if ok else "failed",
            "finished_at_utc": utc_now_iso(),
            "results": results,
            "ok_count": len(ok),
            "failed_count": len(failed),
            "gpu_final": collect_gpu_diagnostics(),
        }
    )
    write_json(run_dir / "manifest.json", run_manifest)
    write_summary_markdown(run_dir / "summary.md", run_manifest)
    print(json.dumps({"manifest": str(run_dir / "manifest.json"), "summary": str(run_dir / "summary.md"), "status": run_manifest["status"]}, indent=2))
    return 0 if not failed else 1


def run_batch_sample94(args: argparse.Namespace) -> int:
    batch_started_perf = time.perf_counter()
    source_dir = Path(args.source_dir).expanduser().resolve()
    all_pdfs = discover_pdf_inputs(source_dir)
    selected_pdfs = select_batch_inputs(all_pdfs, offset=args.offset, limit=args.limit)
    selected_inputs = make_batch_input_records(
        selected_pdfs,
        start_index=args.offset + 1,
        unique_document_ids=False,
    )
    profiles = expand_profiles(args.profiles)
    validate_batch_parallelism(args, selected_count=len(selected_inputs))
    batch_dir = ensure_dir(
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else DEFAULT_ARTIFACT_ROOT / f"sample94-batch-{build_run_id()}"
    )
    batch_manifest: dict[str, Any] = {
        "status": "dry_run" if args.dry_run else "running",
        "started_at_utc": utc_now_iso(),
        "source_dir": str(source_dir),
        "discovered_pdf_count": len(all_pdfs),
        "selected_pdf_count": len(selected_pdfs),
        "offset": args.offset,
        "limit": args.limit,
        "profiles": profiles,
        "pages": args.pages or "all",
        "no_ocr": bool(args.no_ocr),
        "dry_run": bool(args.dry_run),
        "resume": bool(args.resume),
        "skip_existing": bool(args.skip_existing),
        "jobs": int(args.jobs),
        "worker_threads": int(args.worker_threads),
        "max_runtime_seconds": args.max_runtime_seconds,
        "document_timeout_seconds": args.document_timeout_seconds,
        "allow_local_gpu_stress": bool(args.allow_local_gpu_stress),
        "inputs": [
            {
                "batch_index": item.batch_index,
                "document_id": item.document_id,
                "input_pdf": str(item.input_pdf),
            }
            for item in selected_inputs
        ],
        "notes": [
            "Batch mode defaults to the standard TableFormer lane to keep the 94-file run tractable.",
            "Use --profiles approved-smoke for the standard-plus-Granite-Docling smoke; use explicit Granite Vision profiles only for targeted comparison runs.",
            "Picture description/classification and code/formula enrichment are disabled by default to avoid unnecessary model loading.",
            "Per-document manifests retain profile-level table, figure, bitmap, and LLM-export details.",
            "Each document is converted in a fresh subprocess so GPU model memory is released between PDFs.",
            "CPU batches can use --jobs for document-level parallelism; local GPU/VLM batches are serial by default.",
            "Each document subprocess has a wall-clock timeout so one wedged PDF cannot block the batch indefinitely.",
            "Large local CUDA batches require --allow-local-gpu-stress because laptop GPU saturation can destabilize the host.",
        ],
    }
    return run_batch_documents(
        args,
        batch_dir=batch_dir,
        batch_manifest=batch_manifest,
        selected_inputs=selected_inputs,
        batch_started_perf=batch_started_perf,
    )


def run_batch_generic(args: argparse.Namespace) -> int:
    batch_started_perf = time.perf_counter()
    pdfs = resolve_generic_batch_pdfs(args)
    selected_inputs = make_batch_input_records(pdfs, unique_document_ids=True)
    profiles = expand_profiles(args.profiles)
    validate_batch_parallelism(args, selected_count=len(selected_inputs))
    batch_dir = ensure_dir(
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else DEFAULT_ARTIFACT_ROOT / f"batch-{build_run_id()}"
    )
    batch_manifest: dict[str, Any] = {
        "status": "dry_run" if args.dry_run else "running",
        "started_at_utc": utc_now_iso(),
        "batch_kind": "generic",
        "selected_pdf_count": len(selected_inputs),
        "profiles": profiles,
        "pages": args.pages or "all",
        "no_ocr": bool(args.no_ocr),
        "dry_run": bool(args.dry_run),
        "resume": bool(args.resume),
        "skip_existing": bool(args.skip_existing),
        "jobs": int(args.jobs),
        "worker_threads": int(args.worker_threads),
        "max_runtime_seconds": args.max_runtime_seconds,
        "document_timeout_seconds": args.document_timeout_seconds,
        "allow_local_gpu_stress": bool(args.allow_local_gpu_stress),
        "inputs": [
            {
                "batch_index": item.batch_index,
                "document_id": item.document_id,
                "input_pdf": str(item.input_pdf),
            }
            for item in selected_inputs
        ],
        "notes": [
            "Generic batch mode accepts PDF paths from arguments, --input-list files, or stdin.",
            "Each document is converted in a fresh subprocess and written to its own document_id directory.",
            "Use --jobs with --device cpu for simple document-level parallelism.",
            "Local GPU/VLM parallelism is refused by default because it can destabilize laptop CUDA/WSL hosts.",
        ],
    }
    return run_batch_documents(
        args,
        batch_dir=batch_dir,
        batch_manifest=batch_manifest,
        selected_inputs=selected_inputs,
        batch_started_perf=batch_started_perf,
    )


def write_summary_markdown(path: Path, manifest: dict[str, Any]) -> None:
    lines = [
        "# Docling conversion run",
        "",
        f"Status: `{manifest.get('status')}`",
        f"Input: `{manifest.get('input_pdf')}`",
        f"Pages: `{(manifest.get('page_selection') or {}).get('spec') or 'all'}`",
        f"Reference HTML: `{manifest.get('reference_html') or ''}`",
        "",
        "| Profile | Lane | Status | Seconds | Tables | Complex tables | Table bitmaps | Figure bitmaps | LLM Markdown | Error |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for result in manifest.get("results", []):
        artifact_summary = result.get("artifact_summary") or {}
        lines.append(
            "| {profile} | {lane} | {status} | {seconds:.2f} | {tables} | {complex_tables} | {table_bitmaps} | {picture_bitmaps} | {llm_markdown} | {error} |".format(
                profile=result.get("profile", ""),
                lane=result.get("conceptual_lane", ""),
                status=result.get("status", ""),
                seconds=float(result.get("elapsed_seconds") or 0),
                tables=artifact_summary.get("table_count", ""),
                complex_tables=artifact_summary.get("complex_table_count", ""),
                table_bitmaps=artifact_summary.get("table_bitmap_count", ""),
                picture_bitmaps=artifact_summary.get("picture_bitmap_count", ""),
                llm_markdown=artifact_summary.get("llm_markdown", ""),
                error=str(result.get("error") or "").replace("|", "\\|")[:200],
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation notes",
            "",
            "- This is a smoke/comparison artifact, not a formal benchmark.",
            "- Treat referenced images as the default LLM-efficient output; embedded outputs are optional and can be token-expensive.",
            "- LLM-oriented exports render simple tables as HTML tables and complex/risky tables plus figures as referenced bitmaps.",
            "- Picture description and picture classification are disabled by default; image retention uses deterministic geometry/caption rules.",
            "- Code/formula enrichment is disabled by default; enable it explicitly with --enrich-code or --enrich-formula.",
            "- Granite-Docling-258M is the default VLM comparison lane; Granite Vision lanes remain explicit opt-in profiles.",
            "- Chart-extraction enrichment is disabled by design for this run.",
        ]
    )
    write_text(path, "\n".join(lines) + "\n")


def run_smoke_p174008(args: argparse.Namespace) -> int:
    if args.input is None:
        args.input = str(P174008_SOURCE_PDF)
    if args.reference_html is None:
        args.reference_html = str(P174008_REFERENCE_HTML)
    if args.pages is None:
        args.pages = P174008_DEFAULT_PAGES
    if args.output_dir is None:
        args.output_dir = str(DEFAULT_ARTIFACT_ROOT / f"p174008-smoke-{build_run_id()}")
    return run_convert(args)


def run_diagnose(args: argparse.Namespace) -> int:
    diagnostics = collect_gpu_diagnostics()
    diagnostics["versions"] = {
        package: maybe_import_version(package)
        for package in ["docling", "docling-core", "torch", "transformers", "bitsandbytes"]
    }
    print(json.dumps(diagnostics, indent=2, ensure_ascii=False))
    if args.device == "cuda" and diagnostics.get("torch_cuda_available") is not True and not args.allow_no_cuda:
        return 1
    return 0


def add_common_convert_args(
    parser: argparse.ArgumentParser,
    *,
    include_input_required: bool,
    device_default: str = "cuda",
    no_ocr_default: bool = False,
) -> None:
    parser.add_argument("--input", required=include_input_required, help="Input PDF path.")
    parser.add_argument(
        "--output-dir",
        help="Output directory. Required for convert; smoke and batch commands choose a timestamped default when omitted.",
    )
    parser.add_argument("--reference-html", help="Optional reference HTML path for diagnostics/comparison context.")
    parser.add_argument("--pages", help="One contiguous 1-based page range, e.g. 33-35. Use all or omit for full document.")
    parser.add_argument(
        "--profiles",
        default="approved-smoke",
        help=(
            "Comma-separated profiles or alias. Default approved-smoke expands to the practical two-lane smoke: "
            "standard TableFormer plus Granite-Docling-258M. Use explicit Granite Vision profiles, all, or "
            "with-large only for targeted comparison runs."
        ),
    )
    parser.add_argument("--device", choices=["auto", "cpu", "cuda", "mps", "xpu"], default=device_default)
    parser.add_argument("--allow-no-cuda", action="store_true", help="Do not fail fast if --device cuda cannot see CUDA.")
    parser.add_argument("--image-mode", choices=["placeholder", "referenced", "embedded"], default="referenced")
    parser.add_argument("--write-embedded", action="store_true", help="Also write embedded-image Markdown and HTML variants.")
    parser.add_argument(
        "--table-bitmap-policy",
        choices=["complex", "all", "none"],
        default="complex",
        help=(
            "Policy for LLM-oriented exports: complex renders only complex/risky tables as original bitmaps, "
            "all renders every table as a bitmap, none renders every table as an HTML table."
        ),
    )
    parser.add_argument(
        "--table-complexity-threshold",
        type=int,
        default=4,
        help="Complexity/risk score threshold above which LLM-oriented exports use the original table bitmap.",
    )
    parser.add_argument(
        "--llm-hard-limit-mb",
        type=float,
        default=DEFAULT_LLM_HARD_LIMIT_MB,
        help=(
            "Hard serialized request/file ceiling in decimal MB. Default 200 MB; "
            "the converter reserves separate transport and prompt/schema headroom."
        ),
    )
    parser.add_argument(
        "--llm-transport-headroom-mb",
        type=float,
        default=DEFAULT_LLM_TRANSPORT_HEADROOM_MB,
        help="Reserved decimal MB for JSONL/HTTP/provider framing below the hard limit.",
    )
    parser.add_argument(
        "--llm-prompt-schema-headroom-mb",
        type=float,
        default=DEFAULT_LLM_PROMPT_SCHEMA_HEADROOM_MB,
        help="Reserved decimal MB for downstream prompt and response schema below the hard limit.",
    )
    parser.add_argument(
        "--llm-batch-model",
        default=DEFAULT_LLM_BATCH_MODEL,
        help="Model/deployment name written into request-ready OpenAI Batch JSONL shards.",
    )
    parser.add_argument(
        "--llm-batch-endpoint",
        choices=[DEFAULT_LLM_BATCH_ENDPOINT],
        default=DEFAULT_LLM_BATCH_ENDPOINT,
        help="OpenAI-compatible Batch endpoint written into each request shard.",
    )
    parser.add_argument(
        "--llm-prompt-file",
        help="Optional UTF-8 prompt included in exact request serialization; defaults to a short handoff instruction.",
    )
    parser.add_argument(
        "--llm-schema-file",
        help="Optional JSON Schema included as strict Chat Completions response_format in every request shard.",
    )
    ocr_group = parser.add_mutually_exclusive_group()
    ocr_group.add_argument(
        "--ocr",
        dest="no_ocr",
        action="store_false",
        help="Enable OCR. Single-document conversion defaults to OCR on; Databricks/CPU batch commands default to OCR off.",
    )
    ocr_group.add_argument(
        "--no-ocr",
        dest="no_ocr",
        action="store_true",
        help="Disable OCR. This is the default for CPU batch conversion over born-digital IMF/WBG PDFs.",
    )
    parser.set_defaults(no_ocr=no_ocr_default)
    parser.add_argument(
        "--ocr-engine",
        choices=["easyocr", "auto", "rapidocr-torch"],
        default="easyocr",
        help=(
            "OCR engine for standard profiles. Default easyocr avoids RapidOCR's ModelScope download path; "
            "rapidocr-torch is available for explicit GPU OCR probes."
        ),
    )
    parser.add_argument("--ocr-lang", action="append", help="OCR language(s), mainly for rapidocr-torch. Repeatable.")
    picture_classification = parser.add_mutually_exclusive_group()
    picture_classification.add_argument(
        "--picture-classification",
        dest="no_picture_classification",
        action="store_false",
        help="Enable Docling's picture classifier. Default is off; LLM-facing image retention uses deterministic rules.",
    )
    picture_classification.add_argument(
        "--no-picture-classification",
        dest="no_picture_classification",
        action="store_true",
        help="Disable Docling's picture classifier. This is the default.",
    )
    picture_description = parser.add_mutually_exclusive_group()
    picture_description.add_argument(
        "--picture-description",
        dest="no_picture_description",
        action="store_false",
        help="Enable picture-description VLM enrichment. Default is off.",
    )
    picture_description.add_argument(
        "--no-picture-description",
        dest="no_picture_description",
        action="store_true",
        help="Disable picture-description VLM enrichment. This is the default.",
    )
    parser.set_defaults(no_picture_classification=True, no_picture_description=True)
    parser.add_argument(
        "--enrich-code",
        action="store_true",
        help="Enable Docling code enrichment. Default is off for policy/economics PDF bitmap-first conversion.",
    )
    parser.add_argument(
        "--enrich-formula",
        action="store_true",
        help="Enable Docling formula enrichment. Default is off for policy/economics PDF bitmap-first conversion.",
    )
    parser.add_argument(
        "--compile-picture-classifier",
        action="store_true",
        help=(
            "Allow torch.compile for the picture classifier. Default is off because "
            "PyTorch Inductor/Triton triggered illegal-memory-access failures on the local Blackwell GPU."
        ),
    )
    parser.add_argument("--generate-page-images", action="store_true", help="Generate page images for standard profiles. VLM profiles generate page images by default.")
    parser.add_argument("--images-scale", type=float, default=2.0)
    parser.add_argument("--ocr-batch-size", type=int, default=4)
    parser.add_argument("--layout-batch-size", type=int, default=4)
    parser.add_argument("--table-batch-size", type=int, default=4)
    parser.add_argument("--vlm-batch-size", type=int, default=1)
    parser.add_argument("--max-new-tokens", type=int, default=4096)
    parser.add_argument("--force-backend-text", action="store_true", help="Use backend text where supported by Docling VLM options.")
    parser.add_argument(
        "--hf-artifacts-root",
        default=str(Path.home() / ".cache" / "docling-materialized-hf"),
        help=(
            "Materialized Hugging Face artifacts root for models that need real sibling files, "
            "currently used by the Granite Vision 4.1 Transformers lane."
        ),
    )
    parser.add_argument(
        "--no-materialize-large-model",
        action="store_true",
        help="Use the default Hugging Face symlink cache for Granite Vision 4.1 instead of a materialized artifacts tree.",
    )
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first profile failure.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Docling PDF conversion wrapper for Markdown/HTML/JSON/DocTags and hard-case smoke comparisons."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert = subparsers.add_parser("convert", help="Convert one PDF with one or more Docling profiles.")
    add_common_convert_args(convert, include_input_required=True)
    convert.set_defaults(func=run_convert)

    smoke = subparsers.add_parser("smoke-p174008", help="Run the approved hard-case P174008 smoke target.")
    add_common_convert_args(smoke, include_input_required=False)
    smoke.set_defaults(func=run_smoke_p174008)

    generic_batch = subparsers.add_parser(
        "batch",
        help="Run/resume Docling conversion over arbitrary PDF paths, list files, or stdin.",
    )
    add_common_convert_args(
        generic_batch,
        include_input_required=False,
        device_default="cpu",
        no_ocr_default=True,
    )
    generic_batch.add_argument(
        "input_pdfs",
        nargs="*",
        help="PDF paths to convert. Also accepts --input, --input-list, or newline-delimited stdin.",
    )
    generic_batch.add_argument(
        "--input-list",
        action="append",
        help="Newline-delimited PDF path list. Use '-' to read from stdin. Repeatable.",
    )
    generic_batch.add_argument("--dry-run", action="store_true", help="Only list selected PDFs and write the batch manifest.")
    generic_batch.add_argument("--resume", action="store_true", help="Skip documents whose existing per-document manifest has status ok.")
    generic_batch.add_argument("--skip-existing", action="store_true", help="Skip any document with an existing per-document manifest.")
    generic_batch.add_argument(
        "--jobs",
        type=int,
        default=2,
        help="Maximum number of document subprocesses to run concurrently. Use with --device cpu.",
    )
    generic_batch.add_argument(
        "--worker-threads",
        type=int,
        default=1,
        help="Per-worker CPU thread cap for OpenMP/BLAS when --jobs > 1 and --device cpu.",
    )
    generic_batch.add_argument(
        "--max-runtime-seconds",
        type=float,
        help=(
            "Stop submitting new documents after this wall-clock budget. "
            "Already-running documents are allowed to finish."
        ),
    )
    generic_batch.add_argument(
        "--document-timeout-seconds",
        type=float,
        default=1800.0,
        help=(
            "Kill and mark failed any single PDF conversion that exceeds this wall-clock budget. "
            "Default is 1800 seconds; set 0 or a negative value to disable."
        ),
    )
    generic_batch.add_argument(
        "--allow-local-gpu-stress",
        action="store_true",
        help=(
            "Explicitly allow non-CPU parallel or large CUDA batch execution. "
            "Use only after confirming the host can tolerate sustained GPU memory saturation."
        ),
    )
    generic_batch.set_defaults(func=run_batch_generic, profiles="standard")

    batch = subparsers.add_parser("batch-sample94", help="Run/resume Docling conversion over the sample94 source PDF set.")
    add_common_convert_args(
        batch,
        include_input_required=False,
        device_default="cpu",
        no_ocr_default=True,
    )
    batch.add_argument(
        "--source-dir",
        default=str(SAMPLE94_SOURCE_DIR),
        help="Directory containing the 94 source PDFs.",
    )
    batch.add_argument("--limit", type=int, help="Limit the number of selected PDFs.")
    batch.add_argument("--offset", type=int, default=0, help="Skip this many sorted PDFs before running.")
    batch.add_argument("--dry-run", action="store_true", help="Only list selected PDFs and write the batch manifest.")
    batch.add_argument("--resume", action="store_true", help="Skip documents whose existing per-document manifest has status ok.")
    batch.add_argument("--skip-existing", action="store_true", help="Skip any document with an existing per-document manifest.")
    batch.add_argument(
        "--jobs",
        type=int,
        default=2,
        help="Maximum number of document subprocesses to run concurrently. Use with --device cpu.",
    )
    batch.add_argument(
        "--worker-threads",
        type=int,
        default=1,
        help="Per-worker CPU thread cap for OpenMP/BLAS when --jobs > 1 and --device cpu.",
    )
    batch.add_argument(
        "--max-runtime-seconds",
        type=float,
        help=(
            "Stop submitting new documents after this wall-clock budget. "
            "Already-running documents are allowed to finish."
        ),
    )
    batch.add_argument(
        "--document-timeout-seconds",
        type=float,
        default=1800.0,
        help=(
            "Kill and mark failed any single PDF conversion that exceeds this wall-clock budget. "
            "Default is 1800 seconds; set 0 or a negative value to disable."
        ),
    )
    batch.add_argument(
        "--allow-local-gpu-stress",
        action="store_true",
        help=(
            "Explicitly allow a CUDA batch larger than 3 PDFs on the local laptop GPU. "
            "Use only after confirming the host can tolerate sustained GPU memory saturation."
        ),
    )
    batch.set_defaults(func=run_batch_sample94, profiles="standard")

    diagnose = subparsers.add_parser("diagnose", help="Print Docling/CUDA/runtime diagnostics.")
    diagnose.add_argument("--device", choices=["auto", "cpu", "cuda", "mps", "xpu"], default="cuda")
    diagnose.add_argument("--allow-no-cuda", action="store_true")
    diagnose.set_defaults(func=run_diagnose)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "command", None) == "convert" and not args.output_dir:
        parser.error("convert requires --output-dir")
    try:
        return int(args.func(args))
    except (DoclingConvertError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
