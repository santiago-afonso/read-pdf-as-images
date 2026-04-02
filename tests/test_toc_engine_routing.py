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
Pages: 4
Page size: 612 x 792 pts
PDF version: 1.7
EOF
""",
    )


def _install_fake_uv_enforcing_pdf_to_markdown_routing(bin_dir: Path) -> None:
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

with_packages = []
while args[:1] == ["--with"]:
    with_packages.append(args[1])
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

if target == "read_pdf_text.py":
    if arg_value("--engine") == "pdf_to_markdown":
        if "pypdf" not in with_packages:
            sys.stderr.write("pdf_to_markdown mode must install pypdf\\n")
            sys.exit(97)
        meta_out = arg_value("--meta-json-out")
        if meta_out:
            Path(meta_out).write_text(
                json.dumps({"full_char_count": 200, "selected_pages": [1]}, ensure_ascii=False),
                encoding="utf-8",
            )
        if arg_value("--filter") == "toc":
            sys.stdout.write("<!-- PAGE 1 -->\\n## Table of Contents\\n")
        else:
            sys.stdout.write("# Full Text\\n")
        sys.exit(0)

    if arg_value("--engine") == "pymupdf4llm":
        sys.stdout.write("<!-- PAGE 1 -->\\n# Precise Text\\n")
        sys.exit(0)

    sys.stderr.write("unexpected read_pdf_text.py engine\\n")
    sys.exit(98)

if target == "truncate_text_output.py":
    os.execvp("python3", ["python3"] + py_args)

if target == "-":
    os.execvp("python3", ["python3"] + py_args)

sys.stderr.write(f"unsupported fake-uv python target: {target}\\n")
sys.exit(94)
""",
    )


def _install_fake_pdf_to_markdown_runtime(bin_dir: Path) -> Path:
    runtime = bin_dir / "nutrient-linux-amd64"
    _write_executable(
        runtime,
        """#!/usr/bin/env bash
set -euo pipefail
echo "unused fake runtime"
""",
    )
    return runtime


def _install_noop_uv(bin_dir: Path) -> None:
    _write_executable(
        bin_dir / "uv",
        """#!/usr/bin/env bash
set -euo pipefail
echo "noop uv should not be called" >&2
exit 99
""",
    )


def test_toc_routes_to_pdf_to_markdown_engine(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _install_fake_pdfinfo(fake_bin)
    _install_fake_uv_enforcing_pdf_to_markdown_routing(fake_bin)
    fake_runtime = _install_fake_pdf_to_markdown_runtime(fake_bin)

    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\\n%fake\\n")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["PDF_TO_MARKDOWN_BINARY"] = str(fake_runtime)
    result = subprocess.run(
        [str(READ_PDF), str(fake_pdf), "--toc", "--max-output-tokens", "0"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert 'mode="toc"' in result.stdout
    assert 'engine="pdf_to_markdown"' in result.stdout
    assert "## Table of Contents" in result.stdout


def test_default_fast_text_routes_to_pdf_to_markdown_engine(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _install_fake_pdfinfo(fake_bin)
    _install_fake_uv_enforcing_pdf_to_markdown_routing(fake_bin)
    fake_runtime = _install_fake_pdf_to_markdown_runtime(fake_bin)

    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\\n%fake\\n")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["PDF_TO_MARKDOWN_BINARY"] = str(fake_runtime)
    result = subprocess.run(
        [str(READ_PDF), str(fake_pdf), "--max-output-tokens", "0"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert 'engine="pdf_to_markdown"' in result.stdout
    assert 'source="pdf_to_markdown"' in result.stdout
    assert "explicit PAGE markers are not guaranteed" in result.stdout


def test_default_fast_text_fails_fast_when_pdf_to_markdown_binary_is_missing(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _install_fake_pdfinfo(fake_bin)
    _install_noop_uv(fake_bin)

    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\\n%fake\\n")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["PDF_TO_MARKDOWN_BINARY"] = str(tmp_path / "missing-nutrient")
    result = subprocess.run(
        [str(READ_PDF), str(fake_pdf), "--max-output-tokens", "0"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "PDF_TO_MARKDOWN_BINARY does not exist" in result.stderr
    assert "pdf-to-markdown --help" in result.stderr
