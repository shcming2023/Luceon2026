#!/usr/bin/env python3
"""Freeze native media contracts into a new immutable Spec 03 ledger snapshot.

This is a migration/commit boundary, not a semantic classifier.  It imports
only representation choices that already exist in a reviewed render plan and
binds them to immutable source, asset, crop-review, and decision evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

VERSION = "canonical-media-contract-freezer/1.0.0"
MEDIA_TARGETS = {"source_asset_image", "source_region_image", "display_math"}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


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


def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" for value in values), encoding="utf-8")


def read_ledger(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with path.open(encoding="utf-8") as stream:
        header = json.loads(next(stream))
        records = [json.loads(line) for line in stream if line.strip()]
    if header.get("record_type") != "ledger_header":
        raise ValueError(f"not a canonical block ledger: {path}")
    if header.get("current_ledger_hash") != canonical_hash(records):
        raise ValueError(f"canonical ledger payload hash mismatch: {path}")
    return header, records


def relative(base: Path, target: Path) -> str:
    return os.path.relpath(target, base).replace("\\", "/")


def parse_roots(values: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"asset root must be NAME=PATH: {value}")
        name, raw = value.split("=", 1)
        path = Path(raw).resolve()
        if not name or name in roots or not path.is_dir():
            raise ValueError(f"invalid or duplicate asset root: {value}")
        roots[name] = path
    return roots


def load_media_core():
    path = Path(__file__).with_name("media_source_representation.py")
    spec = importlib.util.spec_from_file_location("media_source_representation", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load media core: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def media_kind(node: dict[str, Any], records: list[dict[str, Any]]) -> str:
    if node["target_construct"] == "display_math":
        return "formula"
    labels = {(record.get("source_type"), record.get("source_label")) for record in records}
    if any(source_type == "table" for source_type, _ in labels):
        return "table"
    if any(source_type == "chart" or label == "chart" for source_type, label in labels):
        return "chart"
    if node["target_construct"] == "source_region_image" and not any(source_type == "equation" for source_type, _ in labels):
        return "visual_region"
    if any(source_type == "equation" for source_type, _ in labels):
        return "formula"
    return "image"


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite immutable run directory: {output}")
    output.mkdir(parents=True)

    source_ledger = args.source_ledger.resolve()
    semantic_ledger = args.verified_semantic_ledger.resolve()
    render_plan_path = args.verified_render_plan.resolve()
    crop_audit_path = args.crop_audit.resolve()
    final_audit_path = args.final_media_audit.resolve()
    parent_index_path = args.parent_decision_index.resolve()
    source_pdf = args.source_pdf.resolve()
    roots = parse_roots(args.asset_root)
    if args.source_asset_root_id not in roots or args.source_page_root_id not in roots:
        raise ValueError("declared source asset/page root ids must exist in --asset-root")

    source_header, source_records = read_ledger(source_ledger)
    semantic_header, semantic_records = read_ledger(semantic_ledger)
    source_by_id = {record["block_id"]: record for record in source_records}
    semantic_by_id = {record["block_id"]: record for record in semantic_records}
    if set(source_by_id) != set(semantic_by_id):
        raise ValueError("verified semantic ledger does not preserve the source ledger atom inventory")
    if source_header.get("ledger_checkpoint") != "source_reconciled" or source_header.get("spec_status") != "passed":
        raise ValueError("parent Spec 03 source ledger is not passed at source_reconciled")
    if sha256_file(source_pdf) != source_header.get("material_identity", {}).get("source_pdf_sha256"):
        raise ValueError("source PDF differs from the parent canonical ledger")

    render_plan = read_json(render_plan_path)
    crop_audit = read_json(crop_audit_path)
    final_audit = read_json(final_audit_path)
    parent_index = read_json(parent_index_path)
    if render_plan.get("spec_status") != "passed" or crop_audit.get("status") != "closed" or final_audit.get("spec_status") != "passed":
        raise ValueError("verified plan/crop/final media evidence is not closed and passed")
    if semantic_header.get("render_plan_sha256") != sha256_file(render_plan_path):
        raise ValueError("verified semantic ledger is not bound to the supplied render plan")
    if semantic_header.get("canonical_decision_index_hash") != sha256_file(parent_index_path):
        raise ValueError("verified semantic ledger is not bound to the supplied parent decision index")

    evidence = [
        {"role": "source_reconciled_parent_ledger", "path": str(source_ledger), "sha256": sha256_file(source_ledger)},
        {"role": "verified_semantic_ledger", "path": str(semantic_ledger), "sha256": sha256_file(semantic_ledger)},
        {"role": "verified_render_plan", "path": str(render_plan_path), "sha256": sha256_file(render_plan_path)},
        {"role": "closed_source_region_crop_audit", "path": str(crop_audit_path), "sha256": sha256_file(crop_audit_path)},
        {"role": "passed_final_media_audit", "path": str(final_audit_path), "sha256": sha256_file(final_audit_path)},
    ]
    event = {
        "decision_id": args.decision_id,
        "status": "closed",
        "rule_id": "CV-H04/MEDIA-NATIVE-CONTRACT-MIGRATION",
        "decided_at": now(),
        "scope": "Import already verified media representation choices into the native canonical ledger contract without changing semantic or representation choices.",
        "evidence": evidence,
        "prohibitions": ["formula_reconstruction", "table_reconstruction", "upstream_cleaning_rewrite", "semantic_reclassification"],
        "supersedes": [],
        "invalidated_by": None,
    }
    event_path = output / "decisions/media_contract_decisions.jsonl"
    write_jsonl(event_path, [event])

    decisions = list(parent_index.get("decisions", []))
    if args.decision_id in {item.get("decision_id") for item in decisions}:
        raise ValueError(f"decision id already exists: {args.decision_id}")
    decisions.append({
        "decision_id": args.decision_id,
        "event_file": "decisions/media_contract_decisions.jsonl",
        "rule_id": event["rule_id"],
        "status": "closed",
        "supersedes": [],
        "invalidated_by": None,
    })
    decision_index = {
        "schema_version": "canonical-decision-index/1.0",
        "decision_index_id": parent_index.get("decision_index_id"),
        "snapshot_id": args.decision_snapshot_id,
        "version": int(parent_index.get("version", 0)) + 1,
        "generated_at": now(),
        "parent_index_ref": relative(output, parent_index_path),
        "parent_index_hash": sha256_file(parent_index_path),
        "acyclic_commit_rule": "evidence_or_parent_then_decision_index_D_then_child_artifact_L",
        "spec_status": "passed",
        "evidence_committed_before_index": evidence,
        "decision_event_files": [{"path": "decisions/media_contract_decisions.jsonl", "sha256": sha256_file(event_path), "decision_ids": [args.decision_id]}],
        "decisions": decisions,
        "summary": dict(Counter(item.get("status", "unknown") for item in decisions)),
    }
    decision_path = output / "decisions/canonical_decision_index.json"
    write_json(decision_path, decision_index)  # D is frozen before L.
    decision_sha = sha256_file(decision_path)

    crop_items = {item["render_node_id"]: item for item in crop_audit.get("items", [])}
    media_nodes = [node for node in render_plan.get("nodes", []) if node.get("target_construct") in MEDIA_TARGETS]
    if len(media_nodes) != len({node["render_node_id"] for node in media_nodes}):
        raise ValueError("verified render plan contains duplicate media node ids")
    updated_by_id = {record["block_id"]: dict(record) for record in source_records}
    type_counts: Counter[str] = Counter()

    for node in media_nodes:
        block_ids = list(node.get("source_block_ids", []))
        if not block_ids or any(block_id not in updated_by_id for block_id in block_ids):
            raise ValueError(f"media node lacks canonical source identity: {node.get('render_node_id')}")
        semantic_sources = [semantic_by_id[block_id] for block_id in block_ids]
        media_id = "media::" + canonical_hash({"source_block_ids": sorted(block_ids)})[:24]
        representation = {
            "source_asset_image": "source_asset_image",
            "source_region_image": "source_region_image",
            "display_math": "structured_formula",
        }[node["target_construct"]]
        payload = node.get("payload", {})
        candidate_id = "candidate::" + canonical_hash({"media_id": media_id, "representation_type": representation, "payload": payload})[:24]
        common = {
            "candidate_id": candidate_id,
            "representation_type": representation,
            "decision_refs": [args.decision_id],
            "upstream_refs": [
                {"path": str(render_plan_path), "sha256": sha256_file(render_plan_path), "fragment": node["render_node_id"]},
                {"path": str(semantic_ledger), "sha256": sha256_file(semantic_ledger)},
            ],
        }
        if representation == "source_asset_image":
            asset_ref = payload.get("asset_ref")
            if not asset_ref:
                raise ValueError(f"source asset node lacks asset_ref: {node['render_node_id']}")
            asset_path = roots[args.source_asset_root_id] / asset_ref
            if not asset_path.is_file():
                raise FileNotFoundError(f"verified source asset is unavailable: {asset_path}")
            candidate = {**common, "root_id": args.source_asset_root_id, "path": asset_ref, "sha256": sha256_file(asset_path)}
        elif representation == "source_region_image":
            audit_item = crop_items.get(node["render_node_id"])
            if not audit_item:
                raise ValueError(f"source-region node lacks closed crop audit: {node['render_node_id']}")
            reviewed_crop = crop_audit_path.parent / audit_item["crop_ref"]
            if not reviewed_crop.is_file() or sha256_file(reviewed_crop) != audit_item["crop_sha256"]:
                raise ValueError(f"reviewed crop bytes drifted: {node['render_node_id']}")
            page_number = int(payload["pdf_physical_page"])
            page_ref = f"page-{page_number:03d}.jpg"
            page_path = roots[args.source_page_root_id] / page_ref
            if not page_path.is_file():
                raise FileNotFoundError(f"source page raster unavailable: {page_path}")
            candidate = {
                **common,
                "source_page": page_number,
                "bbox": payload["bbox"],
                "bbox_coordinate_space": payload["bbox_coordinate_space"],
                "crop_recipe": "source_page_raster_cropbox_to_mediabox",
                "raster_coordinate_space": payload["raster_coordinate_space"],
                "crop_padding_fraction_of_cropbox": payload["crop_padding_fraction_of_cropbox"],
                "source_raster_root_id": args.source_page_root_id,
                "source_raster_path": page_ref,
                "source_raster_sha256": sha256_file(page_path),
                "artifact_sha256": audit_item["crop_sha256"],
                "human_review": {
                    "status": "closed",
                    "decision_id": args.decision_id,
                    "observed_artifact_sha256": audit_item["crop_sha256"],
                    "evidence_ref": {"path": str(crop_audit_path), "sha256": sha256_file(crop_audit_path), "fragment": node["render_node_id"]},
                },
            }
        else:
            candidate = {
                **common,
                "payload": payload,
                "payload_sha256": canonical_hash(payload),
                "verification_status": "verified",
                "verification_refs": [
                    {"path": str(render_plan_path), "sha256": sha256_file(render_plan_path), "fragment": node["render_node_id"]},
                    {"path": str(final_audit_path), "sha256": sha256_file(final_audit_path)},
                ],
            }
        atom = {
            "media_contract_schema_version": "canonical-media-atom/1.0",
            "media_id": media_id,
            "source_block_ids": block_ids,
            "source_page": min(int(record["pdf_physical_page"]) for record in semantic_sources),
            "media_kind": media_kind(node, semantic_sources),
            "inclusion_status": "included",
            "requested_candidate_id": candidate_id,
            "candidates": [candidate],
            "imported_verified_render_node_id": node["render_node_id"],
        }
        type_counts[representation] += 1
        for block_id in block_ids:
            updated = updated_by_id[block_id]
            verified = semantic_by_id[block_id]
            for field in ("bbox", "bbox_basis", "bbox_reference", "source_representation"):
                if field in verified:
                    updated[field] = verified[field]
            updated["media_contract"] = atom
            updated["media_contract_status"] = "frozen"
            updated["human_decision_refs"] = sorted(set(updated.get("human_decision_refs", [])) | {args.decision_id})

    records_out = [updated_by_id[record["block_id"]] for record in source_records]
    header_out = dict(source_header)
    header_out.update({
        "generated_at": now(),
        "updated_at": now(),
        "ledger_snapshot_id": args.ledger_snapshot_id,
        "ledger_version": args.ledger_version,
        "ledger_checkpoint": "source_reconciled",
        "spec_status": "passed",
        "parent_ledger_ref": relative(output, source_ledger),
        "parent_ledger_file_sha256": sha256_file(source_ledger),
        "parent_ledger_hash": source_header["current_ledger_hash"],
        "canonical_decision_index_ref": "decisions/canonical_decision_index.json",
        "canonical_decision_index_hash": decision_sha,
        "current_ledger_hash": canonical_hash(records_out),
        "current_ledger_hash_scope": "canonical JSON hash of ordered source_block evidence records including native media_contract fields",
        "media_contract": {
            "schema_version": "canonical-media-atom/1.0",
            "status": "frozen",
            "media_atoms": len(media_nodes),
            "representation_types": dict(sorted(type_counts.items())),
            "migration_evidence_only": True,
        },
    })
    ledger_path = output / "ledgers/canonical_block_ledger.jsonl"
    write_jsonl(ledger_path, [header_out, *records_out])  # L is frozen after D.
    ledger_sha = sha256_file(ledger_path)
    write_json(output / "ledgers/ledger_manifest.json", {
        "schema_version": "ledger-manifest/2.1",
        "generated_at": now(),
        "ledger_id": header_out["ledger_id"],
        "ledger_version": header_out["ledger_version"],
        "snapshot_id": header_out["ledger_snapshot_id"],
        "artifact_path": "ledgers/canonical_block_ledger.jsonl",
        "artifact_sha256": ledger_sha,
        "payload_hash": header_out["current_ledger_hash"],
        "parent_artifact_ref": header_out["parent_ledger_ref"],
        "parent_artifact_sha256": header_out["parent_ledger_file_sha256"],
        "decision_index_ref": "decisions/canonical_decision_index.json",
        "decision_index_hash": decision_sha,
        "spec_status": "passed",
        "ledger_checkpoint": "source_reconciled",
        "immutable_after_publication": True,
    })

    media_dir = output / "media"
    core = load_media_core()
    normalized_path = core.normalized_from_canonical(ledger_path, decision_path, source_pdf, media_dir)
    media_ledger, media_plan, media_queue = core.build_contracts(normalized_path, source_pdf, roots, media_dir)
    validation = core.validate_contracts(media_dir / "media_evidence_ledger.json", media_dir / "media_representation_plan.json")
    write_json(output / "reports/media_contract_validation.json", validation)
    if media_plan.get("spec_status") != "passed" or media_queue.get("open_items") != 0 or validation.get("status") != "passed":
        raise ValueError("native media contract did not pass closed-contract validation")

    manifest = {
        "schema_version": "spec03-media-contract-stage-manifest/1.0",
        "generated_at": now(),
        "stage": "source_reconciled_media_contract_frozen",
        "status": "passed",
        "ledger_checkpoint": "source_reconciled",
        "decision_index_D": {"path": "decisions/canonical_decision_index.json", "sha256": decision_sha},
        "ledger_L": {"path": "ledgers/canonical_block_ledger.jsonl", "sha256": ledger_sha, "payload_hash": header_out["current_ledger_hash"]},
        "media_evidence_ledger": {"path": "media/media_evidence_ledger.json", "sha256": sha256_file(media_dir / "media_evidence_ledger.json"), "payload_hash": media_ledger["payload_hash"]},
        "media_representation_plan": {"path": "media/media_representation_plan.json", "sha256": sha256_file(media_dir / "media_representation_plan.json"), "payload_hash": media_plan["payload_hash"]},
        "validation": {"path": "reports/media_contract_validation.json", "sha256": sha256_file(output / "reports/media_contract_validation.json")},
        "commit_order": ["decision_index_D", "ledger_L", "stage_manifest_M"],
        "scope_limits": "Native media representation contract only; formula reconstruction, table reconstruction, and upstream cleaning changes are excluded.",
    }
    write_json(output / "manifests/spec03_media_contract_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-ledger", type=Path, required=True)
    parser.add_argument("--verified-semantic-ledger", type=Path, required=True)
    parser.add_argument("--verified-render-plan", type=Path, required=True)
    parser.add_argument("--crop-audit", type=Path, required=True)
    parser.add_argument("--final-media-audit", type=Path, required=True)
    parser.add_argument("--parent-decision-index", type=Path, required=True)
    parser.add_argument("--source-pdf", type=Path, required=True)
    parser.add_argument("--asset-root", action="append", default=[])
    parser.add_argument("--source-asset-root-id", required=True)
    parser.add_argument("--source-page-root-id", required=True)
    parser.add_argument("--ledger-snapshot-id", required=True)
    parser.add_argument("--ledger-version", type=int, required=True)
    parser.add_argument("--decision-snapshot-id", required=True)
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = freeze(args)
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "generator": VERSION, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
