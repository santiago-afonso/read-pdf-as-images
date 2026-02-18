from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
READ_PDF = REPO_ROOT / "scripts" / "read-pdf"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _install_fake_pdfinfo(bin_dir: Path) -> None:
    _write_executable(
        bin_dir / "pdfinfo",
        """#!/usr/bin/env bash
set -euo pipefail
cat <<'EOF'
Title: Fake
Author: Fake
Producer: FakeProducer
Creator: FakeCreator
Pages: 12
Page size: 612 x 792 pts
PDF version: 1.7
EOF
""",
    )


def _install_fake_uv(bin_dir: Path) -> None:
    _write_executable(
        bin_dir / "uv",
        """#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] in {"--offline", "--no-cache"}:
        i += 1
        continue
    if args[i] == "--cache-dir":
        i += 2
        continue
    break
args = args[i:]

if not args or args[0] != "run":
    sys.stderr.write("fake uv only supports `uv run ...`\\n")
    sys.exit(91)
args = args[1:]

while args[:1] == ["--with"]:
    args = args[2:]

if not args or args[0] != "python":
    sys.stderr.write("fake uv only supports python commands\\n")
    sys.exit(92)

py_args = args[1:]
if not py_args:
    sys.stderr.write("fake uv missing python target\\n")
    sys.exit(93)

target = Path(py_args[0]).name if py_args[0] != "-" else "-"

def arg_value(flag: str) -> str | None:
    if flag not in py_args:
        return None
    idx = py_args.index(flag)
    if idx + 1 >= len(py_args):
        return None
    return py_args[idx + 1]

def emit_large(prefix: str) -> str:
    return f"{prefix}\\n" + ("alpha beta gamma delta " * 8000) + "\\n"

if target == "read_pdf_text.py":
    filter_mode = arg_value("--filter") or "all"
    meta_out = arg_value("--meta-json-out")
    if filter_mode == "toc":
        body = emit_large("<!-- PAGE 1 -->\\n## Table of Contents")
        selected_pages = [1, 2]
    else:
        body = emit_large("<!-- PAGE 1 -->\\n# Full Text")
        selected_pages = []
    if meta_out:
        Path(meta_out).write_text(
            json.dumps({"full_char_count": len(body) * 2, "selected_pages": selected_pages}, ensure_ascii=False),
            encoding="utf-8",
        )
    sys.stdout.write(body)
    sys.exit(0)

if target == "read_pdf_search.py":
    line = json.dumps(
        {
            "tool": "read-pdf",
            "mode": "search",
            "page": 1,
            "match": "alpha",
            "context_before": "x " * 40,
            "context_after": "y " * 40,
        },
        ensure_ascii=False,
    )
    sys.stdout.write("\\n".join([line] * 1200) + "\\n")
    sys.exit(0)

if target == "read_pdf_page_candidates.py":
    kind = arg_value("--kind") or "toc"
    payload = {
        "tool": "read-pdf",
        "mode": f"{kind}-pages",
        "pdf_page_count": 12,
        "pages": list(range(1, 13)),
        "note": "candidate-pages " + ("z " * 12000),
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\\n")
    sys.exit(0)

if target == "truncate_text_output.py":
    os.execvp("python3", ["python3"] + py_args)

if target == "-":
    os.execvp("python3", ["python3"] + py_args)

sys.stderr.write(f"unsupported fake-uv python target: {target}\\n")
sys.exit(94)
""",
    )


def _run_read_pdf(tmp_path: Path, mode_args: list[str]) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _install_fake_pdfinfo(fake_bin)
    _install_fake_uv(fake_bin)

    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\\n%fake\\n")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    cmd = [str(READ_PDF), str(fake_pdf), *mode_args, "--max-output-tokens", "50"]
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, text=True, capture_output=True, check=False)


def test_all_text_modes_emit_truncation_notice(tmp_path: Path) -> None:
    cases = [
        [],
        ["--as-text-fast"],
        ["--as-text-precise-layout-slow"],
        ["--as-raw-text"],
        ["--toc"],
        ["--toc-pages"],
        ["--table-pages"],
        ["--chart-pages"],
        ["--search", "alpha"],
    ]
    for idx, mode_args in enumerate(cases):
        case_dir = tmp_path / f"case_{idx}"
        case_dir.mkdir()
        result = _run_read_pdf(case_dir, mode_args)
        assert result.returncode == 0, f"mode={mode_args} stderr={result.stderr}"
        assert "[read-pdf output truncated]" in result.stdout
        assert "Estimated total tokens for this response:" in result.stdout
        assert "Configured max output tokens: 50" in result.stdout


def test_disable_truncation_with_zero(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _install_fake_pdfinfo(fake_bin)
    _install_fake_uv(fake_bin)

    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\\n%fake\\n")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    result = subprocess.run(
        [str(READ_PDF), str(fake_pdf), "--as-raw-text", "--max-output-tokens", "0"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "[read-pdf output truncated]" not in result.stdout
    assert len(result.stdout) > 5000
