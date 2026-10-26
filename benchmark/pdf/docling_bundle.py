"""Source-bound structured-only Docling handoff. No visual evidence is supplied."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

CONTRACT = "read-pdf.docling-document.v4"
REPRESENTATION = "docling_structured_no_bitmaps"
TABLE_NOTICE = (
    "TABLE TRANSCRIPTION WARNING: No source bitmap is available in this representation. "
    "Docling transcription can misread values, signs, units, headers, merged cells, "
    "and row/column associations. Treat conclusions requiring precise table positioning "
    "as low confidence; do not claim visual verification."
)
LIMITATION = (
    "Structured-only Docling conversion of the full page range; no bitmaps supplied. "
    "Text and tables may contain OCR/transcription errors. Charts and other visual-only "
    "content are unavailable, not evidence of absence. Physical page coverage does not "
    "establish complete or faithful content extraction. The original PDF remains authoritative."
)


@dataclass(frozen=True)
class DocumentBundle:
    manifest_sha256: str
    source_sha256: str
    source_size_bytes: int
    page_count: int
    markdown: str
    source_units: tuple[dict, ...]
    source_unit_coverage: dict


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def structured_document(document) -> tuple[str, list[dict]]:
    """Keep reading order, all tables, and text provenance without image recovery."""
    from docling_core.types.doc import PictureItem, TableItem, TextItem

    blocks = [LIMITATION]
    inventory = []
    seen = set()
    for item, _ in document.iterate_items(traverse_pictures=True):
        ref = item.self_ref
        if ref in seen:
            continue
        seen.add(ref)
        pages = sorted({p.page_no for p in item.prov})
        if not set(pages) <= set(document.pages):
            raise ValueError("Docling item has invalid physical-page provenance")
        if isinstance(item, TableItem):
            value = TABLE_NOTICE + "\n" + item.export_to_html(doc=document)
            kind = "table"
        elif isinstance(item, PictureItem):
            value = "[Visual content unavailable: no bitmap supplied.]"
            kind = "unavailable_visual"
        elif isinstance(item, TextItem):
            value = item.text
            kind = "text"
        else:
            continue
        inventory.append({"item_ref": ref, "pages": pages, "kind": kind})
        label = ",".join(map(str, pages)) or "unanchored"
        blocks.append(f"[Physical PDF pages: {label}; source item: {ref}]\n{value}")
    if not any(row["kind"] in {"text", "table"} for row in inventory):
        raise ValueError("Docling conversion contains no structured text or tables")
    if {t.self_ref for t in document.tables} != {r["item_ref"] for r in inventory if r["kind"] == "table"}:
        raise ValueError("Docling table missing from document reading order")
    return "\n\n".join(blocks) + "\n", inventory


def _logical_source_chunks(document, order):
    """Use the library's complete logical units, preserving their provenance."""
    from docling_core.transforms.chunker.hierarchical_chunker import HierarchicalChunker

    units = []
    for chunk in HierarchicalChunker(merge_list_items=True, always_emit_headings=True).chunk(document):
        refs = [item.self_ref for item in chunk.meta.doc_items]
        units.append({"source_item_refs": refs,
                      "physical_pages": sorted({p.page_no for item in chunk.meta.doc_items for p in item.prov}),
                      "headings": chunk.meta.headings or [], "captions": chunk.meta.captions or [],
                      "text": chunk.text, "reading_order": min(order[ref] for ref in refs)})
    return units


def complete_source_units(document) -> tuple[list[dict], dict]:
    """E20's logical text/table units; never a claim of complete visual coverage."""
    items = [item for item, _ in document.iterate_items()]
    order = {item.self_ref: index for index, item in enumerate(items)}
    units = _logical_source_chunks(document, order)
    owned = {ref for unit in units for ref in unit["source_item_refs"]}
    for item in items:
        if item.self_ref not in owned and getattr(item, "text", "").strip():
            units.append({"source_item_refs": [item.self_ref],
                          "physical_pages": sorted({p.page_no for p in item.prov}),
                          "headings": [], "captions": [], "text": item.text,
                          "reading_order": order[item.self_ref]})
    units.sort(key=lambda unit: (min(unit["physical_pages"], default=0), unit["reading_order"]))
    for index, unit in enumerate(units, 1):
        unit["unit_ref"] = f"u{index:04d}"
    return units, _source_unit_coverage(document, items, units)


def _source_unit_coverage(document, items, units):
    """Verify owned text and explicitly inventory unavailable visual surfaces.

    Picture descendants are chart/visual transcriptions, not independent body
    paragraphs. Excluding them must not disappear from coverage evidence.
    """
    counts = Counter(ref for unit in units for ref in unit["source_item_refs"])
    # A detected table region with no extracted cells (an image-only table) has no text to own;
    # like pictures it is inventoried as unavailable, never silently dropped.
    structureless = [item for item in items if str(item.label) == "table" and not item.data.table_cells]
    eligible = {item.self_ref for item in items if getattr(item, "text", "").strip()
                or (str(item.label) == "table" and item.data.table_cells)}
    if eligible - counts.keys() or any(count != 1 for count in counts.values()):
        raise ValueError("Source-item ownership missing or duplicated")
    represented_pages = {page for unit in units for page in unit["physical_pages"]}
    top_level_refs = {item.self_ref for item in items}
    return {
        "eligible_source_item_refs": sorted(eligible),
        "pages_without_structured_content": sorted(set(document.pages) - represented_pages),
        "unanchored_unit_refs": [unit["unit_ref"] for unit in units if not unit["physical_pages"]],
        "visual_items_not_supplied": [{"source_item_ref": item.self_ref,
            "physical_pages": sorted({p.page_no for p in item.prov})}
            for item in items if str(item.label) == "picture"],
        "visual_descendant_refs_not_supplied": [item.self_ref for item, _ in document.iterate_items(traverse_pictures=True)
                                                if item.self_ref not in top_level_refs],
        "structureless_tables_not_supplied": [{"source_item_ref": item.self_ref,
            "physical_pages": sorted({p.page_no for p in item.prov})} for item in structureless],
        "source_bitmaps_available": False,
        "limitation": LIMITATION,
        "table_notice": TABLE_NOTICE,
    }


def write_document_bundle(*, source: Path, source_sha256: str, document, destination: Path) -> Path:
    """Shared fresh/saved export, independent of bitmap postprocessing."""
    import pymupdf
    from docling_core.types.doc import DocumentOrigin

    raw = source.read_bytes()
    if digest(raw) != source_sha256:
        raise ValueError("Original PDF differs from expected SHA-256")
    with pymupdf.open(stream=raw, filetype="pdf") as pdf:
        count = len(pdf)
    if sorted(document.pages) != list(range(1, count + 1)):
        raise ValueError("Docling conversion does not cover every physical source page")
    origin = DocumentOrigin(mimetype="application/pdf", binary_hash=source_sha256, filename=source.name)
    if document.origin is None or document.origin.binary_hash != origin.binary_hash:
        raise ValueError("Docling origin does not match the original PDF")
    markdown, inventory = structured_document(document)
    units, unit_coverage = complete_source_units(document)
    eligible_refs = set(unit_coverage["eligible_source_item_refs"])
    for item in inventory:
        item["source_inventory_eligible"] = item["item_ref"] in eligible_refs
    unit_content = (json.dumps({"units": units, "coverage": unit_coverage}, ensure_ascii=False, sort_keys=True) + "\n").encode()
    content = markdown.encode()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    manifest = {
        "contract": CONTRACT,
        "representation": REPRESENTATION,
        "source_bitmaps_available": False,
        "source": {"sha256": source_sha256, "size_bytes": len(raw), "page_count": count},
        "processed_pages": list(range(1, count + 1)),
        "origin_binding": "docling_sha256_low_64_bits",
        "limitation": LIMITATION,
        "table_notice": TABLE_NOTICE,
        "items": inventory,
        "markdown": {"name": "document.md", "sha256": digest(content), "size_bytes": len(content)},
        "source_units": {"name": "source-units.json", "sha256": digest(unit_content), "size_bytes": len(unit_content)},
    }
    path = destination / "manifest.json"
    (destination / "document.md").write_bytes(content)
    (destination / "source-units.json").write_bytes(unit_content)
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    for target in (path, destination / "document.md", destination / "source-units.json"):
        target.chmod(0o600)
    return path


def load_document_bundle(path: Path, *, expected_manifest_sha256: str) -> DocumentBundle:
    """Authenticate content; reject old bitmap bundles rather than degrading them."""
    raw = path.read_bytes()
    if digest(raw) != expected_manifest_sha256:
        raise ValueError("Docling bundle manifest hash differs")
    manifest = json.loads(raw)
    if (manifest.get("contract") != CONTRACT or manifest.get("representation") != REPRESENTATION
            or manifest.get("source_bitmaps_available") is not False or "bitmaps" in manifest):
        raise ValueError("Unsupported Docling bundle; re-export saved conversion with current CLI")
    source = manifest["source"]
    count = source["page_count"]
    if type(count) is not int or count < 1 or manifest["processed_pages"] != list(range(1, count + 1)):
        raise ValueError("Docling bundle does not cover every physical page")
    record = manifest["markdown"]
    target = (path.parent / "document.md").resolve()
    if record["name"] != "document.md" or target.parent != path.parent.resolve():
        raise ValueError("Invalid bundle asset path")
    content = target.read_bytes()
    if len(content) != record["size_bytes"] or digest(content) != record["sha256"]:
        raise ValueError("Docling bundle asset integrity failure")
    markdown = content.decode()
    if not markdown.startswith(LIMITATION) or markdown.count(TABLE_NOTICE) != sum(
        row["kind"] == "table" for row in manifest["items"]
    ):
        raise ValueError("Missing structured-only limitation or table notices")
    units, coverage = _load_source_units(path, manifest)
    return DocumentBundle(expected_manifest_sha256, source["sha256"], source["size_bytes"], count, markdown, units, coverage)


def _load_source_units(path, manifest):
    descriptor = manifest["source_units"]
    if descriptor["name"] != "source-units.json":
        raise ValueError("Invalid source-unit asset path")
    raw = (path.parent / "source-units.json").read_bytes()
    if len(raw) != descriptor["size_bytes"] or digest(raw) != descriptor["sha256"]:
        raise ValueError("Docling source-unit integrity failure")
    payload = json.loads(raw)
    units, coverage = payload["units"], payload["coverage"]
    _validate_unit_ownership(units, coverage, manifest)
    _validate_unit_bindings(units, coverage, manifest)
    return tuple(units), coverage


def _validate_unit_ownership(units, coverage, manifest):
    """Unit bytes cannot silently lose or repeat the manifest's source items."""
    items = {item["item_ref"]: item for item in manifest["items"]}
    counts = Counter(ref for unit in units for ref in unit["source_item_refs"])
    expected = {ref for ref, item in items.items() if item["source_inventory_eligible"]}
    if (set(coverage["eligible_source_item_refs"]) != expected
            or expected - counts.keys() or not counts.keys() <= items.keys()
            or any(count != 1 for count in counts.values())):
        raise ValueError("Docling source-unit ownership missing, duplicated or foreign")


def _validate_unit_bindings(units, coverage, manifest):
    """A unit's page bindings must equal those of its exact owned source refs."""
    items = {item["item_ref"]: item for item in manifest["items"]}
    if [unit["unit_ref"] for unit in units] != [f"u{i:04d}" for i in range(1, len(units) + 1)]:
        raise ValueError("Docling source-unit order is not a complete sequence")
    if any(not isinstance(unit["text"], str)
           or set(unit["physical_pages"]) != {page for ref in unit["source_item_refs"] for page in items[ref]["pages"]}
           or not set(unit["physical_pages"]) <= set(manifest["processed_pages"])
           for unit in units):
        raise ValueError("Invalid source-unit text or physical-page binding")
    if coverage["source_bitmaps_available"] is not False or coverage["limitation"] != LIMITATION or coverage["table_notice"] != TABLE_NOTICE:
        raise ValueError("Source units lost their source representation limitations")
