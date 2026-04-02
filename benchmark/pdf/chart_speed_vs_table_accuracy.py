#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any


PREFERRED_ENGINE_ORDER = [
    "markitdown",
    "pymupdf4llm",
    "docling",
    "kreuzberg",
    "opendataloader",
    "opendataloader_hybrid_auto",
    "markit",
    "pdf_to_markdown",
]


def load_timing_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    normalized: list[dict[str, Any]] = []
    for row in rows:
        normalized.append(
            {
                **row,
                "seconds_per_page": float(row["seconds_per_page"]) if row.get("seconds_per_page") else None,
            }
        )
    return normalized


def load_grade_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def engine_sort_key(engine: str) -> tuple[int, str]:
    try:
        return (PREFERRED_ENGINE_ORDER.index(engine), engine)
    except ValueError:
        return (len(PREFERRED_ENGINE_ORDER), engine)


def collect_engine_metrics(
    *,
    timing_payloads: list[list[dict[str, Any]]],
    grade_payloads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    timing_values: dict[str, list[float]] = {}
    for rows in timing_payloads:
        for row in rows:
            if str(row.get("scope", "")) != "full":
                continue
            if str(row.get("status", "")) != "ok":
                continue
            seconds_per_page = row.get("seconds_per_page")
            if seconds_per_page in {None, ""}:
                continue
            value = float(seconds_per_page)
            if value <= 0:
                continue
            timing_values.setdefault(str(row["engine"]), []).append(value)

    table_values: dict[str, list[float]] = {}
    for payload in grade_payloads:
        if str(payload.get("status", "")).lower() != "completed":
            continue
        documents = payload.get("documents", [])
        if not isinstance(documents, list):
            continue
        for document in documents:
            excerpts = document.get("excerpts", [])
            if not isinstance(excerpts, list):
                continue
            for excerpt in excerpts:
                engines = excerpt.get("engines", {})
                if not isinstance(engines, dict):
                    continue
                for engine, engine_payload in engines.items():
                    if not isinstance(engine_payload, dict):
                        continue
                    scores = engine_payload.get("scores", {})
                    if not isinstance(scores, dict):
                        continue
                    table_score = scores.get("tables")
                    if table_score in {None, ""}:
                        continue
                    table_values.setdefault(str(engine), []).append(float(table_score))

    metrics: list[dict[str, Any]] = []
    for engine in sorted(set(timing_values) & set(table_values), key=engine_sort_key):
        mean_seconds_per_page = mean(timing_values[engine])
        mean_table_score = mean(table_values[engine])
        metrics.append(
            {
                "engine": engine,
                "mean_seconds_per_page": round(mean_seconds_per_page, 6),
                "pages_per_second": round(1.0 / mean_seconds_per_page, 4),
                "mean_table_score": round(mean_table_score, 2),
                "table_accuracy": round((mean_table_score / 5.0) * 100.0, 2),
                "table_samples": len(table_values[engine]),
                "timing_samples": len(timing_values[engine]),
            }
        )
    return metrics


def render_svg(metrics: list[dict[str, Any]], *, title: str) -> str:
    if not metrics:
        raise ValueError("No engines had both full-document timings and table scores")

    width = 960
    height = 640
    margin_left = 90
    margin_right = 40
    margin_top = 70
    margin_bottom = 80
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom

    x_values = [float(metric["pages_per_second"]) for metric in metrics]
    min_x = min(x_values)
    max_x = max(x_values)
    min_log = math.log10(min_x) - 0.08
    max_log = math.log10(max_x) + 0.08
    if min_log == max_log:
        min_log -= 0.5
        max_log += 0.5

    def x_to_px(value: float) -> float:
        ratio = (math.log10(value) - min_log) / (max_log - min_log)
        return margin_left + (ratio * plot_width)

    def y_to_px(value: float) -> float:
        ratio = value / 100.0
        return margin_top + ((1.0 - ratio) * plot_height)

    x_tick_values: list[float] = []
    for exponent in range(math.floor(min_log), math.ceil(max_log) + 1):
        tick = 10**exponent
        if min_x <= tick <= max_x:
            x_tick_values.append(float(tick))
    if not x_tick_values:
        x_tick_values = [min_x, max_x]

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fffdf8" />',
        f'<text x="{margin_left}" y="34" font-family="Helvetica, Arial, sans-serif" font-size="24" font-weight="700" fill="#111827">{escape_xml(title)}</text>',
        (
            f'<text x="{margin_left}" y="56" font-family="Helvetica, Arial, sans-serif" '
            'font-size="13" fill="#4b5563">x-axis: pages per second (log scale, higher is faster) · '
            'y-axis: table accuracy from spot-check grades (0-100)</text>'
        ),
        (
            f'<line x1="{margin_left}" y1="{margin_top + plot_height}" '
            f'x2="{margin_left + plot_width}" y2="{margin_top + plot_height}" stroke="#374151" stroke-width="1.5" />'
        ),
        (
            f'<line x1="{margin_left}" y1="{margin_top}" '
            f'x2="{margin_left}" y2="{margin_top + plot_height}" stroke="#374151" stroke-width="1.5" />'
        ),
    ]

    for tick in range(0, 101, 20):
        y = y_to_px(float(tick))
        lines.append(
            f'<line x1="{margin_left}" y1="{y:.2f}" x2="{margin_left + plot_width}" y2="{y:.2f}" stroke="#e5e7eb" stroke-width="1" />'
        )
        lines.append(
            f'<text x="{margin_left - 12}" y="{y + 4:.2f}" text-anchor="end" font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#4b5563">{tick}</text>'
        )

    for tick in x_tick_values:
        x = x_to_px(tick)
        label = f"{tick:g}"
        lines.append(
            f'<line x1="{x:.2f}" y1="{margin_top}" x2="{x:.2f}" y2="{margin_top + plot_height}" stroke="#f3f4f6" stroke-width="1" />'
        )
        lines.append(
            f'<text x="{x:.2f}" y="{margin_top + plot_height + 24}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#4b5563">{escape_xml(label)}</text>'
        )

    lines.append(
        f'<text x="{margin_left + plot_width / 2:.2f}" y="{height - 24}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#111827">Pages per second (log scale)</text>'
    )
    lines.append(
        f'<text transform="translate(24 {margin_top + plot_height / 2:.2f}) rotate(-90)" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#111827">Table accuracy (0-100)</text>'
    )

    for index, metric in enumerate(metrics):
        engine = str(metric["engine"])
        x = x_to_px(float(metric["pages_per_second"]))
        y = y_to_px(float(metric["table_accuracy"]))
        color = "#d97706" if engine == "pdf_to_markdown" else "#2563eb"
        label_dy = -10 if index % 2 == 0 else 18
        if x > margin_left + plot_width - 140:
            label_x = x - 10
            text_anchor = "end"
        else:
            label_x = x + 10
            text_anchor = "start"
        lines.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="6" fill="{color}" stroke="#111827" stroke-width="1" />'
        )
        lines.append(
            f'<text x="{label_x:.2f}" y="{y + label_dy:.2f}" text-anchor="{text_anchor}" font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#111827">{escape_xml(engine)}</text>'
        )

    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def write_metrics_csv(path: Path, metrics: list[dict[str, Any]]) -> None:
    fieldnames = [
        "engine",
        "mean_seconds_per_page",
        "pages_per_second",
        "mean_table_score",
        "table_accuracy",
        "table_samples",
        "timing_samples",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(metrics)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a speed-vs-table-accuracy chart from benchmark artifacts."
    )
    parser.add_argument(
        "--timings-csv",
        action="append",
        required=True,
        help="Path to a benchmark timings.csv file. Repeat for multiple runs.",
    )
    parser.add_argument(
        "--grades-json",
        action="append",
        required=True,
        help="Path to a completed benchmark grades JSON file. Repeat for multiple runs.",
    )
    parser.add_argument("--output-svg", required=True)
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Optional CSV path for the plotted dataset. Defaults to the SVG stem with .csv.",
    )
    parser.add_argument(
        "--title",
        default="PDF backend speed vs table accuracy",
        help="Chart title.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    timing_payloads = [load_timing_rows(Path(path).expanduser().resolve()) for path in args.timings_csv]
    grade_payloads = [load_grade_payload(Path(path).expanduser().resolve()) for path in args.grades_json]
    metrics = collect_engine_metrics(timing_payloads=timing_payloads, grade_payloads=grade_payloads)

    output_svg = Path(args.output_svg).expanduser().resolve()
    output_svg.parent.mkdir(parents=True, exist_ok=True)
    output_svg.write_text(render_svg(metrics, title=str(args.title)), encoding="utf-8")

    output_csv = (
        Path(args.output_csv).expanduser().resolve()
        if args.output_csv
        else output_svg.with_suffix(".csv")
    )
    write_metrics_csv(output_csv, metrics)

    print(output_svg)
    print(output_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
