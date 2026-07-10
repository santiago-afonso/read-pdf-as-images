# PDF mass comparator

`mass_compare.py` runs a single PDF (or a page subset) through many local PDF parsers/converters for side-by-side benchmarking. By default it starts one Alpine-based Podman container per PDF and runs engines as parallel child processes inside that sandbox, with only the run directory mounted at `/work`. Use `--execution-mode per-engine` only when you explicitly want one Podman startup per engine.

## Build the Alpine image

Image building is the explicit network/supply-chain boundary. Conversion runs default to `--network none`.

```bash
scripts/pdf-mass-compare --build-image-only
# equivalent from an installed dev checkout: pdf-mass-compare --build-image-only
```

The image defaults Alpine APK repositories to `http://dl-cdn.alpinelinux.org/alpine` to avoid TLS verification failures on constrained/MITM hosts. When `~/machine-setup` contains the known corporate CA files, the CLI combines them and passes them to `podman build` as a secret (`corp_ca_bundle`) for pip, Cargo, npm, and curl. Override the APK mirror only if you need a different mirror:

```bash
scripts/pdf-mass-compare --build-image-only \
  --build-arg ALPINE_REPOSITORY_URL=https://dl-cdn.alpinelinux.org/alpine
```

The image build is best-effort on Alpine/musl: packages that cannot be installed are logged under `/opt/mass-compare/install-logs` inside the image, and their engines will produce failed artifacts instead of blocking other engines. Docling uses `docling-slim[format-pdf]` plus an Alpine-compatible `docling-parse` fallback if the full torch-backed CLI cannot import. Unstructured remains non-OCR and falls back to raw pdfminer text when PDF extras require offline OCR/model/NLTK assets.

## Run all core + component engines

```bash
scripts/pdf-mass-compare \
  --pdf /path/to/input.pdf \
  --pages '1-3,7' \
  --jobs 4
```

Default output root:

```text
tmp/benchmark/pdf-mass-compare/<run-id>/
  input.pdf                  # copied/subsetted PDF exposed to containers
  engine_runner.py           # exact runner used inside containers
  manifest.json
  timings.csv              # engine_seconds_inside_container + container/podman wall/overhead fields
  summary.md
  outputs/<engine>/
    output.md|output.txt|output.json
    engine-runner.stdout.txt
    engine-runner.stderr.txt
    result.json
```

## Engine groups

```bash
scripts/pdf-mass-compare --list-engines
```

Useful groups:

- `core`: MarkItDown, PyMuPDF4LLM, Docling, Kreuzberg, OpenDataLoader, Nutrient, LiteParse, EdgeParse, PDF Oxide, Unstructured.
- `components`: PyMuPDF, pdfplumber, pdfminer.six, pypdfium2, pypdf, Poppler `pdftotext`, Camelot, Tabula Java.
- `all`: `core` + `components` (default).
- `all_with_legacy`: `all` + the existing `markit_ai` comparator.

## Proprietary or experimental engines

Nutrient/PSPDFKit is not baked into the image. Mount a host binary explicitly; on glibc hosts the CLI also mounts the host glibc loader/libs read-only so the proprietary binary can run from inside Alpine:

```bash
scripts/pdf-mass-compare \
  --pdf input.pdf \
  --engines nutrient_pdf_to_markdown \
  --pdf-to-markdown-binary ~/.local/share/nutrient/cli/nutrient-linux-amd64
```

LiteParse, EdgeParse, and PDF Oxide CLIs are still moving targets. If the default templates fail, override an engine command inside the container:

```bash
scripts/pdf-mass-compare \
  --pdf input.pdf \
  --engines edgeparse \
  --engine-command 'edgeparse=edgeparse convert {input} --to markdown --output {output}'
```

Placeholders: `{input}`, `{output}`, `{output_dir}`.
