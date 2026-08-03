#!/usr/bin/env python3
"""Freeze page-level visual completeness and composite-media integrity.

This Spec 03 source-boundary stage consumes the original PDF independently of
the MinerU/Popo block inventory.  It renders every page in the declared scope,
binds a closed human page review and a byte-bound standalone-media review, and
replaces inseparable/contaminated media with reviewed source-PDF composite
regions.  It never reconstructs, erases, inpaints, or selects a render/template
construct.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz


VERSION = "spec03-visual-region-integrity/1.0.4"
LEDGER_HASH_SCOPE = "canonical JSON hash of ordered source_block records"
UNRESOLVED = {"open", "stale", "invalidated"}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def relative(base: Path, target: Path) -> str:
    return os.path.relpath(target, base).replace("\\", "/")


def read_ledger(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) < 2:
        raise ValueError("canonical ledger is empty")
    header, records = rows[0], rows[1:]
    if header.get("record_type") != "ledger_header" or any(row.get("record_type") != "source_block" for row in records):
        raise ValueError("canonical ledger must contain one header followed by source_block records")
    if header.get("current_ledger_hash_scope") != LEDGER_HASH_SCOPE:
        raise ValueError("visual-region stage requires the standard source-block-only hash scope")
    if header.get("current_ledger_hash") != canonical_hash(records):
        raise ValueError("canonical ledger payload hash mismatch")
    if header.get("ledger_checkpoint") != "source_reconciled" or header.get("spec_status") != "passed":
        raise ValueError("visual-region stage requires a passed source_reconciled parent")
    identifiers = [row.get("block_id") for row in records]
    if None in identifiers or len(identifiers) != len(set(identifiers)):
        raise ValueError("source block ids are missing or duplicated")
    return header, records


def validate_index(path: Path, expected_hash: str) -> dict[str, Any]:
    if sha256_file(path) != expected_hash:
        raise ValueError("parent decision-index hash differs from the ledger binding")
    index = read_json(path)
    decisions = index.get("decisions", [])
    identifiers = [row.get("decision_id") for row in decisions]
    if index.get("spec_status") != "passed" or None in identifiers or len(identifiers) != len(set(identifiers)):
        raise ValueError("parent decision index is not a closed unique inventory")
    if any(row.get("status") in UNRESOLVED for row in decisions):
        raise ValueError("parent decision index contains unresolved decisions")
    return index


def page_png(doc: fitz.Document, physical_page: int, dpi: int = 96) -> tuple[bytes, int, int]:
    page = doc[physical_page - 1]
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return pix.tobytes("png"), pix.width, pix.height


def region_png(doc: fitz.Document, physical_page: int, bbox: list[float], scale: float = 2.0) -> bytes:
    if len(bbox) != 4:
        raise ValueError("composite region bbox must have four coordinates")
    x0, y0, x1, y1 = [float(value) for value in bbox]
    if min(x0, y0) < 0 or max(x1, y1) > 1 or x1 <= x0 or y1 <= y0:
        raise ValueError("composite region bbox must be non-empty normalized 0..1")
    page = doc[physical_page - 1]
    rect = fitz.Rect(x0 * page.rect.width, y0 * page.rect.height, x1 * page.rect.width, y1 * page.rect.height)
    return page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False).tobytes("png")


def stable_region_png(source_pdf: Path, physical_page: int, bbox: list[float], scale: float = 2.0) -> bytes:
    """Render a crop in an isolated PDF instance so bytes do not depend on prior page-cache activity."""
    with fitz.open(source_pdf) as isolated:
        return region_png(isolated, physical_page, bbox, scale)


def candidate_fingerprints(normalized: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for atom in sorted(normalized.get("atoms", []), key=lambda item: item.get("media_id", "")):
        candidates = []
        for candidate in sorted(atom.get("candidates", []), key=lambda item: item.get("candidate_id", "")):
            candidates.append({
                "candidate_id": candidate.get("candidate_id"),
                "representation_type": candidate.get("representation_type"),
                "declared_artifact_sha256": candidate.get("sha256") or candidate.get("artifact_sha256") or candidate.get("payload_sha256"),
                "root_id": candidate.get("root_id"),
                "path": candidate.get("path"),
                "source_page": candidate.get("source_page"),
                "bbox": candidate.get("bbox"),
                "crop_recipe": candidate.get("crop_recipe"),
            })
        rows.append({
            "media_id": atom.get("media_id"),
            "inclusion_status": atom.get("inclusion_status"),
            "source_block_ids": sorted(atom.get("source_block_ids", [])),
            "requested_candidate_id": atom.get("requested_candidate_id"),
            "candidates": candidates,
        })
    return rows


def expected_pages(scope_mode: str, header: dict[str, Any], records: list[dict[str, Any]], doc: fitz.Document) -> list[int]:
    if scope_mode == "formal_full_source":
        if int(header.get("material_identity", {}).get("page_count", 0)) != len(doc):
            raise ValueError("formal page count differs between ledger and source PDF")
        return list(range(1, len(doc) + 1))
    if scope_mode in {"bounded_media_regression", "migration_hash_regression"}:
        pages = sorted({int(row["pdf_physical_page"]) for row in records})
        if not pages:
            raise ValueError("bounded visual scope is empty")
        return pages
    raise ValueError(f"unsupported visual-region scope mode: {scope_mode}")


def validate_bundle(
    bundle: dict[str, Any], parent_ledger: Path, normalized_path: Path, source_pdf: Path,
    header: dict[str, Any], records: list[dict[str, Any]], doc: fitz.Document,
) -> dict[str, Any]:
    if bundle.get("schema_version") != "visual-region-review-bundle/1.0":
        raise ValueError("unsupported visual-region review bundle")
    if bundle.get("source_pdf_sha256") != sha256_file(source_pdf):
        raise ValueError("review bundle source PDF hash mismatch")
    if bundle.get("parent_ledger_sha256") != sha256_file(parent_ledger):
        raise ValueError("review bundle parent-ledger hash mismatch")
    if bundle.get("normalized_candidates_sha256") != sha256_file(normalized_path):
        raise ValueError("review bundle normalized-candidate hash mismatch")
    normalized = read_json(normalized_path)
    parent_binding = normalized.get("parent_canonical_ledger", {})
    if parent_binding.get("sha256") != sha256_file(parent_ledger) or parent_binding.get("payload_hash") != header.get("current_ledger_hash"):
        raise ValueError("normalized candidates are not bound to the exact parent ledger")
    pages = expected_pages(bundle.get("scope_mode"), header, records, doc)
    page_review = bundle.get("page_review", {})
    reviewed = sorted(int(page) for page in page_review.get("reviewed_pages", []))
    if page_review.get("status") != "closed" or not page_review.get("decision_id") or reviewed != pages:
        raise ValueError("page-level visual review is not exactly closed over the declared scope")
    if page_review.get("reviewed_pages_hash") != canonical_hash(reviewed):
        raise ValueError("reviewed-page inventory hash mismatch")
    fingerprints = candidate_fingerprints(normalized)
    media_review = bundle.get("media_review", {})
    if media_review.get("status") != "closed" or not media_review.get("decision_id"):
        raise ValueError("standalone-media review is not closed")
    if media_review.get("default_disposition") != "standalone_suitable":
        raise ValueError("unknown default media disposition")
    if media_review.get("candidate_fingerprints_hash") != canonical_hash(fingerprints):
        raise ValueError("standalone-media review is stale for the candidate package")
    return {"pages": pages, "fingerprints": fingerprints, "normalized": normalized}


def deterministic_visual_block_id(source_sha: str, page: int, bbox: list[float], role: str) -> str:
    return "src-visual-" + canonical_hash({"source_pdf_sha256": source_sha, "physical_page": page, "bbox": bbox, "role": role})[:24]


def produce(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite immutable visual-region run: {output}")
    output.mkdir(parents=True)
    parent_ledger = args.parent_ledger.resolve()
    parent_index_path = args.parent_decision_index.resolve()
    normalized_path = args.normalized_candidates.resolve()
    source_pdf = args.source_pdf.resolve()
    review_path = args.review_bundle.resolve()
    header, records = read_ledger(parent_ledger)
    parent_index = validate_index(parent_index_path, header.get("canonical_decision_index_hash"))
    if header.get("material_identity", {}).get("source_pdf_sha256") != sha256_file(source_pdf):
        raise ValueError("source PDF differs from parent ledger")
    if not review_path.is_file():
        raise FileNotFoundError("visual-region review bundle is missing")
    bundle = read_json(review_path)
    doc = fitz.open(source_pdf)
    checked = validate_bundle(bundle, parent_ledger, normalized_path, source_pdf, header, records, doc)
    normalized_in = checked["normalized"]
    source_sha = sha256_file(source_pdf)

    page_rows = []
    page_dir = output / "evidence/page_rasters"
    for page_number in checked["pages"]:
        data, width, height = page_png(doc, page_number)
        path = page_dir / f"page-{page_number:03d}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        page_rows.append({
            "physical_page": page_number, "source_pdf_sha256": source_sha, "dpi": 96,
            "width_px": width, "height_px": height,
            "raster_path": relative(output, path), "raster_sha256": sha256_bytes(data),
            "review_status": "closed", "decision_id": bundle["page_review"]["decision_id"],
        })
    page_inventory = {
        "schema_version": "page-visual-region-inventory/1.0", "generated_at": now(),
        "source_pdf": {"path": str(source_pdf), "sha256": source_sha, "page_count": len(doc)},
        "scope_mode": bundle["scope_mode"], "reviewed_pages": len(page_rows), "open_pages": 0,
        "pages": page_rows,
    }
    page_inventory["payload_hash"] = canonical_hash({key: value for key, value in page_inventory.items() if key not in {"generated_at", "payload_hash"}})
    page_inventory_path = output / "visual/page_visual_region_inventory.json"
    write_json(page_inventory_path, page_inventory)

    records_out = copy.deepcopy(records)
    record_by_id = {row["block_id"]: row for row in records_out}
    atoms = copy.deepcopy(normalized_in.get("atoms", []))
    atom_by_id = {atom["media_id"]: atom for atom in atoms}
    if len(atom_by_id) != len(atoms):
        raise ValueError("normalized media ids are duplicated")
    exceptions = bundle["media_review"].get("exceptions", [])
    exception_by_media: dict[str, dict[str, Any]] = {}
    for item in exceptions:
        media_id = item.get("media_id")
        if not media_id or media_id in exception_by_media or item.get("disposition") != "composite_required":
            raise ValueError("media-review exceptions must be unique composite_required items")
        if not item.get("composite_region_id") or not item.get("contamination_indicators"):
            raise ValueError(f"composite media exception lacks region/indicators: {media_id}")
        exception_by_media[media_id] = item

    new_decision_events: list[dict[str, Any]] = []
    declared_decisions = [bundle["page_review"]["decision_id"], bundle["media_review"]["decision_id"]]
    composite_rows = []
    crop_dir = output / "evidence/composite_regions"
    removed_media: set[str] = set()
    composite_atoms: list[dict[str, Any]] = []
    region_ids: set[str] = set()
    for region in bundle.get("composite_regions", []):
        region_id = region.get("region_id")
        if not region_id or region_id in region_ids:
            raise ValueError("composite region ids are missing or duplicated")
        region_ids.add(region_id)
        page = int(region.get("source_page", 0))
        if page not in checked["pages"]:
            raise ValueError(f"composite region is outside reviewed page scope: {region_id}")
        bbox = [round(float(value), 8) for value in region.get("bbox", [])]
        role = region.get("visual_role", "source_composite_visual")
        member_ids = list(region.get("member_block_ids", []))
        if not member_ids or len(member_ids) != len(set(member_ids)):
            raise ValueError(f"composite region members are empty or duplicated: {region_id}")
        for block_id in member_ids:
            row = record_by_id.get(block_id)
            if not row or int(row.get("pdf_physical_page", 0)) != page or row.get("scope_status") != "included":
                raise ValueError(f"composite member is not an included same-page source block: {region_id}/{block_id}")
        if region.get("add_missing_visual_region"):
            new_id = deterministic_visual_block_id(source_sha, page, bbox, role)
            if new_id in record_by_id:
                raise ValueError(f"deterministic missing-visual block already exists: {new_id}")
            crop_bytes = stable_region_png(source_pdf, page, bbox)
            existing_members = [record_by_id[block_id] for block_id in member_ids]
            local_orders = [row.get("page_local_order") for row in existing_members if isinstance(row.get("page_local_order"), int)]
            final_orders = [row.get("candidate_final_order") for row in existing_members if isinstance(row.get("candidate_final_order"), int)]
            new_record = {
                "record_type": "source_block", "block_id": new_id, "source_system": "source_pdf_visual_review",
                "pdf_physical_page": page, "upstream_page_idx": page - 1, "bbox": bbox,
                "upstream_block_ref": {"provider": "source-pdf-page-visual-review", "source_pdf_sha256": source_sha,
                    "physical_page": page, "region_id": region_id},
                "bbox_basis": "PDF CropBox normalized 0..1, origin top-left", "source_type": "visual_region",
                "source_label": "visual_region", "raw_content": None, "raw_content_sha256": canonical_hash(None),
                "asset_ref": None, "scope_status": "included", "scope_reason": "Meaningful source visual region confirmed by page-level PDF review.",
                "terminal_state": "source_reconciled", "review_required": False,
                "page_local_order": min(local_orders) if local_orders else None,
                "candidate_final_order": min(final_orders) if final_orders else None,
                "order_evidence": [{"rule_id": "VR-COMPOSITE-SHARED-ANCHOR", "region_id": region_id,
                    "reason": "The missing visual region shares the composite anchor and logical emission with its reviewed member fragments."}],
                "human_decision_refs": [region["decision_id"]],
                "source_representation": {"authority": "pdf_visual_region", "physical_page": page, "bbox": bbox,
                    "bbox_coordinate_space": "pdf_cropbox_normalized_0_1_top_left", "source_pdf_sha256": source_sha,
                    "source_crop_sha256": sha256_bytes(crop_bytes)},
                "visual_region_integrity": {"region_id": region_id, "visual_role": role,
                    "source_crop_sha256": sha256_bytes(crop_bytes),
                    "discovery": "page_level_visual_review_independent_of_upstream_blocks"},
            }
            records_out.append(new_record)
            record_by_id[new_id] = new_record
            member_ids.append(new_id)
        touched_atoms = []
        member_set = set(member_ids)
        for atom in atoms:
            source_ids = set(atom.get("source_block_ids", []))
            if source_ids & member_set:
                if not source_ids <= member_set:
                    raise ValueError(f"composite would split an existing media atom: {region_id}/{atom['media_id']}")
                touched_atoms.append(atom["media_id"])
        expected_replaced = sorted(region.get("replaces_media_ids", []))
        if sorted(touched_atoms) != expected_replaced:
            raise ValueError(f"composite replaced-media inventory differs from live fragments: {region_id}")
        for media_id in touched_atoms:
            exception = exception_by_media.get(media_id)
            if not exception or exception.get("composite_region_id") != region_id:
                raise ValueError(f"contaminated/replaced media lacks exact composite exception: {media_id}")
        removed_media.update(touched_atoms)
        crop_bytes = stable_region_png(source_pdf, page, bbox)
        crop_hash = sha256_bytes(crop_bytes)
        if region.get("observed_artifact_sha256") != crop_hash:
            raise ValueError(f"composite review is not bound to the exact source crop: {region_id}; observed={crop_hash}")
        crop_path = crop_dir / f"{region_id}.png"
        crop_path.parent.mkdir(parents=True, exist_ok=True)
        crop_path.write_bytes(crop_bytes)
        media_id = "media-composite-" + canonical_hash({"source_pdf_sha256": source_sha, "page": page, "bbox": bbox, "members": sorted(member_ids)})[:24]
        candidate_id = "source-composite-region"
        decision_id = region.get("decision_id")
        if not decision_id:
            raise ValueError(f"composite region lacks decision id: {region_id}")
        declared_decisions.append(decision_id)
        candidate = {
            "candidate_id": candidate_id, "representation_type": "source_region_image", "source_page": page,
            "bbox": bbox, "bbox_coordinate_space": "pdf_cropbox_normalized_0_1_top_left",
            "crop_recipe": "direct_pdf_clip", "render_scale": 2.0, "artifact_sha256": crop_hash,
            "blank_nonwhite_threshold": 0.0, "edge_dark_threshold": 1.0,
            "human_review": {"status": "closed", "decision_id": decision_id, "observed_artifact_sha256": crop_hash,
                "review_scope": "composite_relationship_and_standalone_suitability"},
            "decision_refs": [decision_id],
            "upstream_refs": [{"provider": "source-pdf-page-visual-review", "source_pdf_sha256": source_sha,
                "physical_page": page, "region_id": region_id}],
        }
        composite_atom = {
            "media_id": media_id, "source_page": page, "bbox": bbox,
            "bbox_coordinate_space": "pdf_cropbox_normalized_0_1_top_left", "media_kind": "visual_region",
            "inclusion_status": "included", "source_block_ids": member_ids,
            "requested_candidate_id": candidate_id, "candidates": [candidate],
            "composite_integrity": {"schema_version": "composite-media-integrity/1.0", "region_id": region_id,
                "spatial_relation": region.get("spatial_relation"), "visual_role": role,
                "replaces_media_ids": sorted(touched_atoms), "member_block_ids_hash": canonical_hash(sorted(member_ids)),
                "contamination_indicators": sorted({flag for media_id_ in touched_atoms for flag in exception_by_media[media_id_]["contamination_indicators"]}),
                "review_decision_id": decision_id, "source_crop_sha256": crop_hash,
                "prohibitions": ["inpaint", "erase_neighbor_content", "invent_missing_pixels", "vector_reconstruction"]},
        }
        composite_atoms.append(composite_atom)
        for block_id in member_ids:
            row = record_by_id[block_id]
            row.setdefault("visual_region_relationships", []).append({
                "region_id": region_id, "media_id": media_id, "relationship": "composite_member",
                "decision_id": decision_id, "source_crop_sha256": crop_hash,
            })
            row["human_decision_refs"] = sorted(set(row.get("human_decision_refs", [])) | {decision_id})
        composite_rows.append({
            "region_id": region_id, "source_page": page, "bbox": bbox, "media_id": media_id,
            "member_block_ids": member_ids, "replaces_media_ids": sorted(touched_atoms),
            "source_crop_path": relative(output, crop_path), "source_crop_sha256": crop_hash,
            "status": "represented", "decision_id": decision_id,
        })

    if set(exception_by_media) != removed_media:
        raise ValueError("media-review composite exceptions do not exactly match replaced media atoms")
    atoms_out = [atom for atom in atoms if atom["media_id"] not in removed_media] + composite_atoms
    fragment_owner: dict[str, str] = {}
    for atom in atoms_out:
        for block_id in atom.get("source_block_ids", []):
            if block_id in fragment_owner:
                raise ValueError(f"visual output candidate package duplicates a source fragment: {block_id}")
            fragment_owner[block_id] = atom["media_id"]

    declared_decisions.append(args.stage_decision_id)
    if len(declared_decisions) != len(set(declared_decisions)):
        raise ValueError("visual-region review and stage decision ids must be unique")
    parent_ids = {row.get("decision_id") for row in parent_index.get("decisions", [])}
    if parent_ids & set(declared_decisions):
        raise ValueError("visual-region decision id already exists in the parent index")
    evidence = [
        {"role": "source_pdf", "path": str(source_pdf), "sha256": source_sha},
        {"role": "parent_ledger", "path": str(parent_ledger), "sha256": sha256_file(parent_ledger)},
        {"role": "normalized_candidates", "path": str(normalized_path), "sha256": sha256_file(normalized_path)},
        {"role": "review_bundle", "path": str(review_path), "sha256": sha256_file(review_path)},
        {"role": "page_visual_inventory", "path": "visual/page_visual_region_inventory.json", "sha256": sha256_file(page_inventory_path)},
    ]
    event_specs = [
        (bundle["page_review"]["decision_id"], "CV-R01/PAGE-VISUAL-REGION-COMPLETE", "Every source page in the declared scope was reviewed against the original PDF raster."),
        (bundle["media_review"]["decision_id"], "CV-R07/STANDALONE-MEDIA-INTEGRITY", "Every current media candidate received a byte-bound standalone-suitability disposition."),
    ]
    for region in bundle.get("composite_regions", []):
        event_specs.append((region["decision_id"], "CV-R07/COMPOSITE-SOURCE-REGION", f"Freeze source-backed composite region {region['region_id']} without image reconstruction."))
    event_specs.append((args.stage_decision_id, "CV-H04/VISUAL-REGION-INTEGRITY-COMMIT", "Commit the reviewed page-visual and composite-media correction as an immutable source_reconciled child."))
    for decision_id, rule_id, scope in event_specs:
        new_decision_events.append({
            "decision_id": decision_id, "status": "closed", "rule_id": rule_id, "decided_at": now(),
            "decision_type": "visual_region_integrity", "scope": scope, "evidence": evidence,
            "supersedes": [], "invalidated_by": None,
            "prohibitions": ["formula_reconstruction", "table_reconstruction", "inpainting", "template_or_render_choice"],
        })
    event_path = output / "decisions/visual_region_integrity_decisions.jsonl"
    write_jsonl(event_path, new_decision_events)
    decisions = copy.deepcopy(parent_index["decisions"])
    for event in new_decision_events:
        decisions.append({
            "decision_id": event["decision_id"], "event_file": "decisions/visual_region_integrity_decisions.jsonl",
            "rule_id": event["rule_id"], "status": "closed", "supersedes": [], "invalidated_by": None,
        })
    statuses = Counter(row.get("status") for row in decisions)
    decision_index = {
        "schema_version": "canonical-decision-index/1.1", "decision_index_id": parent_index["decision_index_id"],
        "snapshot_id": args.decision_snapshot_id, "version": int(parent_index["version"]) + 1, "generated_at": now(),
        "parent_index_ref": relative(output, parent_index_path), "parent_index_hash": sha256_file(parent_index_path),
        "acyclic_commit_rule": "source_evidence_then_decision_index_D_then_visual_corrected_ledger_L",
        "spec_status": "passed", "evidence_committed_before_index": evidence,
        "decision_event_files": [{"path": "decisions/visual_region_integrity_decisions.jsonl", "sha256": sha256_file(event_path),
            "decision_ids": [event["decision_id"] for event in new_decision_events]}],
        "decisions": decisions,
        "summary": {"closed": statuses["closed"], "superseded": statuses["superseded"], "open": 0, "stale": 0, "invalidated": 0},
    }
    decision_path = output / "decisions/canonical_decision_index.json"
    write_json(decision_path, decision_index)
    decision_sha = sha256_file(decision_path)

    header_out = copy.deepcopy(header)
    header_out["summary"] = {
        **copy.deepcopy(header.get("summary", {})),
        "source_records": len(records_out),
        "source_evidence_records": len(records_out),
        "included_atoms": sum(row.get("scope_status") == "included" for row in records_out),
        "excluded_source_records": sum(row.get("scope_status") == "excluded" for row in records_out),
    }
    header_out.update({
        "generated_at": now(), "updated_at": now(), "ledger_snapshot_id": args.ledger_snapshot_id,
        "ledger_version": args.ledger_version, "parent_ledger_ref": relative(output, parent_ledger),
        "parent_ledger_file_sha256": sha256_file(parent_ledger), "parent_ledger_hash": header["current_ledger_hash"],
        "canonical_decision_index_ref": "decisions/canonical_decision_index.json", "canonical_decision_index_hash": decision_sha,
        "current_ledger_hash": canonical_hash(records_out), "current_ledger_hash_scope": LEDGER_HASH_SCOPE,
        "visual_region_integrity": {"schema_version": "visual-region-integrity/1.0", "status": "passed",
            "reviewed_pages": len(page_rows), "composite_regions": len(composite_rows),
            "new_visual_source_atoms": len(records_out) - len(records), "producer": VERSION},
    })
    ledger_path = output / "ledgers/canonical_block_ledger.jsonl"
    write_jsonl(ledger_path, [header_out, *records_out])
    ledger_sha = sha256_file(ledger_path)

    normalized_out = copy.deepcopy(normalized_in)
    normalized_out["schema_version"] = "normalized-media-candidates/1.2"
    normalized_out["parent_canonical_ledger"] = {
        "path": str(ledger_path), "sha256": ledger_sha, "ledger_snapshot_id": header_out["ledger_snapshot_id"],
        "payload_hash": header_out["current_ledger_hash"],
    }
    normalized_out["atoms"] = sorted(atoms_out, key=lambda item: item["media_id"])
    normalized_out["visual_region_integrity"] = {
        "stage_run": str(output), "review_bundle_sha256": sha256_file(review_path),
        "page_inventory_sha256": sha256_file(page_inventory_path), "composite_regions": len(composite_rows),
        "replaced_media_atoms": len(removed_media), "producer": VERSION,
    }
    normalized_out["summary"] = {
        "atoms": len(atoms_out), "included": sum(atom.get("inclusion_status") == "included" for atom in atoms_out),
        "excluded": sum(atom.get("inclusion_status") == "excluded" for atom in atoms_out),
        "composite_atoms": len(composite_atoms), "replaced_media_atoms": len(removed_media),
    }
    normalized_out_path = output / "contracts/normalized_media_candidates.json"
    write_json(normalized_out_path, normalized_out)

    media_rows = []
    for atom in normalized_in.get("atoms", []):
        media_id = atom["media_id"]
        if media_id in removed_media:
            item = exception_by_media[media_id]
            media_rows.append({"media_id": media_id, "status": "replaced_by_source_composite", "composite_region_id": item["composite_region_id"],
                "contamination_indicators": item["contamination_indicators"], "decision_id": bundle["media_review"]["decision_id"]})
        else:
            media_rows.append({"media_id": media_id, "status": "standalone_suitable", "decision_id": bundle["media_review"]["decision_id"]})
    gates = [
        {"gate_id": "VR-H01-source-pdf-and-page-raster-live", "status": "passed", "evidence": {"pages": len(page_rows), "source_pdf_sha256": source_sha}},
        {"gate_id": "VR-H02-page-review-exact-scope", "status": "passed", "evidence": {"scope_mode": bundle["scope_mode"], "reviewed_pages": len(page_rows), "open_pages": 0}},
        {"gate_id": "VR-H03-meaningful-region-disposition-closed", "status": "passed", "evidence": {"composite_regions": len(composite_rows), "open_regions": 0}},
        {"gate_id": "VR-H04-media-standalone-integrity-closed", "status": "passed", "evidence": {"reviewed_media": len(media_rows), "replaced_contaminated": len(removed_media)}},
        {"gate_id": "VR-H05-composite-fragment-partition", "status": "passed", "evidence": {"composite_atoms": len(composite_atoms), "duplicate_fragments": 0}},
        {"gate_id": "VR-H06-source-backed-no-reconstruction", "status": "passed", "evidence": {"source_region_crops": len(composite_rows), "reconstructed_assets": 0}},
        {"gate_id": "VR-H07-decision-closure-and-D-to-L", "status": "passed", "evidence": {"new_decisions": len(new_decision_events), "open_decisions": 0}},
    ]
    report = {
        "schema_version": "visual-region-integrity-report/1.0", "report_id": args.report_id,
        "generated_at": now(), "producer": VERSION, "status": "passed", "spec_status": "passed",
        "ledger_checkpoint": "source_reconciled", "scope_mode": bundle["scope_mode"],
        "source_pdf": {"path": str(source_pdf), "sha256": source_sha, "page_count": len(doc)},
        "review_bundle": {"path": str(review_path), "sha256": sha256_file(review_path)},
        "input_parent_ledger": {"path": str(parent_ledger), "sha256": sha256_file(parent_ledger), "payload_hash": header["current_ledger_hash"]},
        "output_parent_ledger": {"path": str(ledger_path), "sha256": ledger_sha, "snapshot_id": header_out["ledger_snapshot_id"], "payload_hash": header_out["current_ledger_hash"]},
        "output_decision_index": {"path": str(decision_path), "sha256": decision_sha},
        "input_normalized_candidates": {"path": str(normalized_path), "sha256": sha256_file(normalized_path)},
        "output_normalized_candidates": {"path": str(normalized_out_path), "sha256": sha256_file(normalized_out_path)},
        "page_visual_inventory": {"path": str(page_inventory_path), "sha256": sha256_file(page_inventory_path), "reviewed_pages": len(page_rows), "open_pages": 0},
        "media_crop_integrity": {"candidate_fingerprints_hash": canonical_hash(checked["fingerprints"]), "items": media_rows, "open_items": 0},
        "composite_regions": composite_rows, "gates": gates,
        "summary": {"gates": len(gates), "passed": len(gates), "failed": 0, "reviewed_pages": len(page_rows),
            "input_media_atoms": len(atoms), "output_media_atoms": len(atoms_out), "composite_regions": len(composite_rows),
            "new_visual_source_atoms": len(records_out) - len(records), "open_reviews": 0},
        "scope_limit": "Page visual-region completeness and composite-media integrity only; no formula/table reconstruction, teaching-box choice, template mutation, or upstream cleaning rewrite.",
    }
    report_path = output / "reports/visual_region_integrity_report.json"
    write_json(report_path, report)
    manifest = {
        "schema_version": "visual-region-integrity-stage-manifest/1.0", "run_id": args.run_id,
        "generated_at": now(), "stage": "spec03_visual_region_integrity", "status": "passed",
        "decision_index_D": {"path": "decisions/canonical_decision_index.json", "sha256": decision_sha},
        "ledger_L": {"path": "ledgers/canonical_block_ledger.jsonl", "sha256": ledger_sha, "payload_hash": header_out["current_ledger_hash"]},
        "normalized_candidates": {"path": "contracts/normalized_media_candidates.json", "sha256": sha256_file(normalized_out_path)},
        "report": {"path": "reports/visual_region_integrity_report.json", "sha256": sha256_file(report_path)},
        "page_inventory": {"path": "visual/page_visual_region_inventory.json", "sha256": sha256_file(page_inventory_path)},
        "commit_order": ["source_pdf_and_review_evidence_E", "decision_index_D", "source_corrected_ledger_and_candidates_L", "stage_manifest_M"],
        "immutable_after_publication": True,
    }
    manifest_path = output / "manifests/visual_region_integrity_stage_manifest.json"
    write_json(manifest_path, manifest)
    doc.close()
    validation = validate_run(output)
    write_json(output / "reports/visual_region_integrity_validation.json", validation)
    return report


def validate_run(run_dir: Path) -> dict[str, Any]:
    run = run_dir.resolve()
    manifest_path = run / "manifests/visual_region_integrity_stage_manifest.json"
    report_path = run / "reports/visual_region_integrity_report.json"
    if not manifest_path.is_file() or not report_path.is_file():
        raise FileNotFoundError("visual-region run lacks manifest or report")
    manifest = read_json(manifest_path)
    report = read_json(report_path)
    if manifest.get("status") != "passed" or report.get("status") != "passed":
        raise ValueError("visual-region run is not passed")
    for key in ("decision_index_D", "ledger_L", "normalized_candidates", "report", "page_inventory"):
        item = manifest[key]
        path = run / item["path"]
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            raise ValueError(f"visual-region artifact hash mismatch: {key}")
    source_pdf = Path(report["source_pdf"]["path"])
    if not source_pdf.is_file() or sha256_file(source_pdf) != report["source_pdf"]["sha256"]:
        raise ValueError("visual-region source PDF is missing or drifted")
    ledger_path = Path(report["output_parent_ledger"]["path"])
    header, records = read_ledger(ledger_path)
    if sha256_file(ledger_path) != report["output_parent_ledger"]["sha256"] or header["current_ledger_hash"] != report["output_parent_ledger"]["payload_hash"]:
        raise ValueError("visual-region output ledger identity mismatch")
    normalized_path = Path(report["output_normalized_candidates"]["path"])
    normalized = read_json(normalized_path)
    binding = normalized.get("parent_canonical_ledger", {})
    if binding.get("sha256") != sha256_file(ledger_path) or binding.get("payload_hash") != header["current_ledger_hash"]:
        raise ValueError("visual-region normalized candidates are not bound to output ledger")
    index_path = Path(report["output_decision_index"]["path"])
    validate_index(index_path, header["canonical_decision_index_hash"])
    inventory = read_json(Path(report["page_visual_inventory"]["path"]))
    doc = fitz.open(source_pdf)
    for row in inventory.get("pages", []):
        data, width, height = page_png(doc, int(row["physical_page"]), int(row["dpi"]))
        if sha256_bytes(data) != row["raster_sha256"] or width != row["width_px"] or height != row["height_px"]:
            raise ValueError(f"live page raster differs: {row['physical_page']}")
        stored = run / row["raster_path"]
        if not stored.is_file() or sha256_file(stored) != row["raster_sha256"]:
            raise ValueError(f"stored page raster differs: {row['physical_page']}")
    for region in report.get("composite_regions", []):
        data = stable_region_png(source_pdf, int(region["source_page"]), region["bbox"])
        if sha256_bytes(data) != region["source_crop_sha256"]:
            raise ValueError(f"live composite source crop differs: {region['region_id']}")
        crop = run / region["source_crop_path"]
        if not crop.is_file() or sha256_file(crop) != region["source_crop_sha256"]:
            raise ValueError(f"stored composite crop differs: {region['region_id']}")
    doc.close()
    if any(row.get("status") != "passed" for row in report.get("gates", [])) or report.get("summary", {}).get("open_reviews") != 0:
        raise ValueError("visual-region gates or reviews are not closed")
    return {
        "schema_version": "visual-region-integrity-validation/1.0", "status": "passed", "validator": VERSION,
        "run_dir": str(run), "checks": 9, "passed": 9, "failed": 0,
        "reviewed_pages": len(inventory.get("pages", [])), "composite_regions": len(report.get("composite_regions", [])),
        "source_records": len(records), "media_atoms": len(normalized.get("atoms", [])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    produce_parser = sub.add_parser("produce")
    produce_parser.add_argument("--parent-ledger", type=Path, required=True)
    produce_parser.add_argument("--parent-decision-index", type=Path, required=True)
    produce_parser.add_argument("--normalized-candidates", type=Path, required=True)
    produce_parser.add_argument("--source-pdf", type=Path, required=True)
    produce_parser.add_argument("--review-bundle", type=Path, required=True)
    produce_parser.add_argument("--ledger-snapshot-id", required=True)
    produce_parser.add_argument("--ledger-version", type=int, required=True)
    produce_parser.add_argument("--decision-snapshot-id", required=True)
    produce_parser.add_argument("--stage-decision-id", required=True)
    produce_parser.add_argument("--run-id", required=True)
    produce_parser.add_argument("--report-id", required=True)
    produce_parser.add_argument("--output-dir", type=Path, required=True)
    validate_parser = sub.add_parser("validate-run")
    validate_parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = produce(args) if args.command == "produce" else validate_run(args.run_dir)
        print(json.dumps(result.get("summary", result), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "producer": VERSION, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
