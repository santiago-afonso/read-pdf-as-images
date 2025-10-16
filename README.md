# read-pdf-as-images

Render selected PDF pages to images and emit a JSONL manifest to stderr.

Why: Codex/CLI agents need deterministic file paths to attach images. Base64 over stdout is fragile due to output truncation. This tool wraps Poppler utilities to rasterize pages and print a tiny per‑page manifest you can parse immediately.

## Features
- Pages → images with `pdftoppm` (Poppler)
- Defaults: PNG @ 220 DPI
- Output layout: `tmp/pdf_renders/<pdf-basename>/page-<NNN>.png`
- JSONL per page to stderr: `{"page":7,"path":"…/page-007.png","dpi":220,"format":"png"}`
- Clean stdout for piping; `--help` and `--version`

## Install
Dependencies: Poppler (`pdftoppm`, `pdfinfo`). On Ubuntu/Debian: `sudo apt-get install -y poppler-utils`.

Install the CLI into `~/.local/bin` (override with `PREFIX`):

```
make install
# ensure ~/.local/bin is on your PATH
```

Uninstall:
```
make uninstall
```

## Usage
```
read-pdf-as-images <pdf> [--pages "1,3,7-12"] [--dpi 220] [--format png|jpeg] [--outdir DIR]
read-pdf-as-images --help | -h
read-pdf-as-images --version | -V
```

Defaults:
- `--format png`
- `--dpi 220`
- `--outdir tmp/pdf_renders/<pdf-basename>/`

Behavior:
- Filenames: `page-<NNN>.<ext>` (zero‑padded; width = max(3, digits(total_pages))).
- JSONL is written to stderr, one line per page; stdout stays empty.

Example:
```
read-pdf-as-images "Input/Classifiers/FY25 Theme Taxonomy. Mar 2025.pdf" --pages "1-3,6-8"
```

Example JSONL (stderr):
```
{"page":1,"path":"tmp/pdf_renders/FY25 Theme Taxonomy. Mar 2025/page-001.png","dpi":220,"format":"png"}
{"page":2,"path":"tmp/pdf_renders/FY25 Theme Taxonomy. Mar 2025/page-002.png","dpi":220,"format":"png"}
```

## Exit Codes
- `0`: success
- `2`: usage error (prints `--help`)
- `3+`: runtime errors (dependencies, missing outputs, etc.)

## Notes
- This tool generates images only; it does not perform OCR or text extraction.
- Page range parsing accepts lists and ranges, e.g., `1,3,7-12`.

