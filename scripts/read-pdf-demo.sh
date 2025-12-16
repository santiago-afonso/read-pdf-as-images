#!/usr/bin/env bash
set -euo pipefail

# Quick demo runner for read-pdf showing all modes.
# Usage: scripts/read-pdf-demo.sh <pdf>

if (( $# == 0 )); then
  echo "Usage: $0 <pdf>" >&2
  exit 1
fi

PDF=$1
if [ ! -f "$PDF" ]; then
  echo "ERROR: PDF not found: $PDF" >&2
  exit 1
fi

OUTDIR=${OUTDIR:-tmp/read-pdf-demo}
mkdir -p "$OUTDIR"

echo "[1/3] Text mode with metadata + page/doc structure -> $OUTDIR/text_with_metadata.xml"
scripts/read-pdf "$PDF" --page-structure --doc-structure > "$OUTDIR/text_with_metadata.xml"

echo "[2/3] Raw markdown only (with PAGE markers) -> $OUTDIR/raw_text.md"
scripts/read-pdf "$PDF" --as-raw-text > "$OUTDIR/raw_text.md"

echo "[3/3] Images mode (pages 1-2) JSONL manifest -> $OUTDIR/images_manifest.jsonl"
scripts/read-pdf "$PDF" --as-images --pages "1-2" 2> "$OUTDIR/images_manifest.jsonl"

echo "Done. Outputs saved under $OUTDIR"

