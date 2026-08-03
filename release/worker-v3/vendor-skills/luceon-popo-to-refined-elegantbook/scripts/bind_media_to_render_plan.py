#!/usr/bin/env python3
"""Mechanically bind a passed native media plan into a frozen Spec 04 plan."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

VERSION = "spec04-media-binding-assembler/1.1.0"
HASH_EXCLUDED = {"deterministic_payload_hash", "frozen_at", "spec_status", "open_reviews"}


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
    if header.get("current_ledger_hash") != canonical_hash(records):
        raise ValueError(f"canonical ledger payload hash mismatch: {path}")
    return header, records


def relative(base: Path, target: Path) -> str:
    return os.path.relpath(target, base).replace("\\", "/")


def load_media_core():
    path = Path(__file__).with_name("media_source_representation.py")
    spec = importlib.util.spec_from_file_location("media_source_representation", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load media core: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_promotion_gate():
    path = Path(__file__).with_name("stage_promotion_gate.py")
    spec = importlib.util.spec_from_file_location("stage_promotion_gate", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load promotion gate: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite immutable run directory: {output}")
    output.mkdir(parents=True)

    source_ledger_path = args.source_media_ledger.resolve()
    media_evidence_path = args.media_evidence_ledger.resolve()
    media_plan_path = args.media_representation_plan.resolve()
    verified_ledger_path = args.verified_semantic_ledger.resolve()
    verified_plan_path = args.verified_render_plan.resolve()
    verified_mapping_path = args.verified_semantic_mapping.resolve()
    capability_path = args.capability_manifest.resolve()
    parent_index_path = args.parent_decision_index.resolve()

    promotion_gate = load_promotion_gate()
    promotion_selection = promotion_gate.verify_registry_selection(
        args.promotion_registry.resolve(), args.promotion_lineage_key,
        args.source_promotion_manifest.resolve(), "spec03_media_contract"
    )
    promotion = promotion_selection["promotion"]
    promoted = promotion["promoted_artifacts"]
    expected_bindings = {
        "ledger_L": source_ledger_path,
        "media_evidence_ledger": media_evidence_path,
        "media_representation_plan": media_plan_path,
        "decision_index_D": parent_index_path,
    }
    for name, expected_path in expected_bindings.items():
        item = promoted.get(name)
        if not item or Path(item["path"]).resolve() != expected_path or item["sha256"] != sha256_file(expected_path):
            raise ValueError(f"Spec 04 input is not the exact promoted Spec 03 artifact: {name}")

    source_header, source_records = read_ledger(source_ledger_path)
    verified_header, verified_records = read_ledger(verified_ledger_path)
    verified_plan = read_json(verified_plan_path)
    verified_mapping = read_json(verified_mapping_path)
    media_evidence = read_json(media_evidence_path)
    media_plan = read_json(media_plan_path)
    capability_sha = sha256_file(capability_path)
    parent_index = read_json(parent_index_path)
    if source_header.get("ledger_checkpoint") != "source_reconciled" or source_header.get("spec_status") != "passed":
        raise ValueError("Spec 04 requires a passed source_reconciled parent ledger")
    if source_header.get("canonical_decision_index_hash") != sha256_file(parent_index_path):
        raise ValueError("source media ledger is not bound to the supplied parent decision index")
    if set(record["block_id"] for record in source_records) != set(record["block_id"] for record in verified_records):
        raise ValueError("verified semantic baseline does not preserve the source atom inventory")
    if verified_header.get("render_plan_sha256") != sha256_file(verified_plan_path):
        raise ValueError("verified semantic baseline is not bound to its render plan")
    if verified_plan.get("capability_manifest_sha256") != capability_sha:
        raise ValueError("capability manifest differs from the verified semantic baseline")
    media_core = load_media_core()
    media_validation = media_core.validate_contracts(media_evidence_path, media_plan_path)
    if media_validation.get("status") != "passed" or media_plan.get("spec_status") != "passed" or media_plan.get("open_reviews") != 0:
        raise ValueError("media representation contract is not closed and passed")
    if media_evidence.get("canonical_ledger", {}).get("sha256") != sha256_file(source_ledger_path):
        raise ValueError("media evidence contract is not generated from the supplied canonical ledger")

    evidence = [
        {"role": "source_media_canonical_ledger", "path": str(source_ledger_path), "sha256": sha256_file(source_ledger_path)},
        {"role": "media_evidence_ledger", "path": str(media_evidence_path), "sha256": sha256_file(media_evidence_path)},
        {"role": "media_representation_plan", "path": str(media_plan_path), "sha256": sha256_file(media_plan_path)},
        {"role": "verified_semantic_baseline_ledger", "path": str(verified_ledger_path), "sha256": sha256_file(verified_ledger_path)},
        {"role": "verified_semantic_baseline_plan", "path": str(verified_plan_path), "sha256": sha256_file(verified_plan_path)},
        {"role": "template_capability_manifest", "path": str(capability_path), "sha256": capability_sha},
    ]
    event = {
        "decision_id": args.decision_id,
        "status": "closed",
        "rule_id": "SM-H07/MEDIA-BINDING-MECHANICAL-CONSUMPTION",
        "decided_at": now(),
        "scope": "Mechanically retain the reviewed semantic plan and bind every closed native media representation exactly once.",
        "evidence": evidence,
        "semantic_choice": "forbidden",
        "supersedes": [],
        "invalidated_by": None,
    }
    event_path = output / "decisions/media_binding_decisions.jsonl"
    write_jsonl(event_path, [event])
    decisions = list(parent_index.get("decisions", []))
    if args.decision_id in {item.get("decision_id") for item in decisions}:
        raise ValueError(f"decision id already exists: {args.decision_id}")
    decisions.append({"decision_id": args.decision_id, "event_file": "decisions/media_binding_decisions.jsonl", "rule_id": event["rule_id"], "status": "closed", "supersedes": [], "invalidated_by": None})
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
        "decision_event_files": [{"path": "decisions/media_binding_decisions.jsonl", "sha256": sha256_file(event_path), "decision_ids": [args.decision_id]}],
        "decisions": decisions,
        "summary": dict(Counter(item.get("status", "unknown") for item in decisions)),
    }
    decision_path = output / "decisions/canonical_decision_index.json"
    write_json(decision_path, decision_index)  # D before plan/L.
    decision_sha = sha256_file(decision_path)

    atoms = {item["media_id"]: item for item in media_evidence["atoms"]}
    representations = {item["media_id"]: item for item in media_plan["representations"] if item.get("status") == "closed"}
    node_to_rep: dict[str, dict[str, Any]] = {}
    for media_id, representation in representations.items():
        atom = atoms[media_id]
        node_id = atom.get("imported_verified_render_node_id")
        if not node_id or node_id in node_to_rep:
            raise ValueError(f"missing or duplicate imported render node identity: {media_id}")
        node_to_rep[node_id] = representation

    new_plan = copy.deepcopy(verified_plan)
    media_plan_sha = sha256_file(media_plan_path)
    changed_payload_nodes = 0
    bound_counts: Counter[str] = Counter()
    for node in new_plan["nodes"]:
        representation = node_to_rep.get(node["render_node_id"])
        if not representation:
            if node.get("media_binding"):
                raise ValueError(f"verified baseline unexpectedly already has media_binding: {node['render_node_id']}")
            continue
        expected_construct = {
            "source_asset_image": "source_asset_image",
            "source_region_image": "source_region_image",
            "structured_formula": "display_math",
        }.get(representation["representation_type"])
        if node["target_construct"] != expected_construct:
            raise ValueError(f"media contract would change a verified construct: {node['render_node_id']}")
        node["media_binding"] = {
            "media_id": representation["media_id"],
            "representation_id": representation["representation_id"],
            "representation_type": representation["representation_type"],
            "selected_candidate_id": representation["selected_candidate_id"],
            "artifact_sha256": representation["artifact_sha256"],
            "media_representation_plan_sha256": media_plan_sha,
        }
        if representation["representation_type"] == "source_asset_image":
            existing = node["payload"].get("asset_sha256")
            if existing is not None and existing != representation["artifact_sha256"]:
                raise ValueError(f"verified asset hash conflicts with native media plan: {node['render_node_id']}")
            if existing is None:
                node["payload"]["asset_sha256"] = representation["artifact_sha256"]
                changed_payload_nodes += 1
        node["payload_hash"] = canonical_hash(node["payload"])
        bound_counts[representation["representation_type"]] += 1
    if set(node_to_rep) != {node["render_node_id"] for node in new_plan["nodes"] if node.get("media_binding")}:
        raise ValueError("not every closed media representation was bound exactly once")

    new_plan.update({
        "schema_version": "render-plan/1.5",
        "source_ledger_snapshot_id": source_header["ledger_snapshot_id"],
        "source_ledger_payload_hash": source_header["current_ledger_hash"],
        "parent_render_plan_sha256": sha256_file(verified_plan_path),
        "decision_index_sha256": decision_sha,
        "media_evidence_ledger_sha256": sha256_file(media_evidence_path),
        "media_representation_plan_sha256": media_plan_sha,
        "frozen_at": now(),
        "spec_status": "passed",
        "open_reviews": 0,
    })
    new_plan["deterministic_payload_hash"] = canonical_hash({key: value for key, value in new_plan.items() if key not in HASH_EXCLUDED})
    plan_path = output / "render/render_plan.json"
    write_json(plan_path, new_plan)
    plan_sha = sha256_file(plan_path)

    source_by_id = {record["block_id"]: record for record in source_records}
    verified_by_id = {record["block_id"]: record for record in verified_records}
    node_by_block = {block_id: node for node in new_plan["nodes"] for block_id in node["source_block_ids"]}
    records_out: list[dict[str, Any]] = []
    for source_record in source_records:
        block_id = source_record["block_id"]
        record = copy.deepcopy(verified_by_id[block_id])
        if source_record.get("media_contract"):
            record["media_contract"] = source_record["media_contract"]
            record["media_contract_status"] = source_record["media_contract_status"]
        node = node_by_block.get(block_id)
        if node:
            record["render_binding"] = {
                "render_node_id": node["render_node_id"],
                "target_construct": node["target_construct"],
                "construct_parameters": node["construct_parameters"],
                "payload_hash": node["payload_hash"],
                "capability_manifest_sha256": capability_sha,
            }
            if node.get("media_binding"):
                record["media_binding"] = node["media_binding"]
        record["human_decision_refs"] = sorted(set(record.get("human_decision_refs", [])) | {args.decision_id})
        records_out.append(record)

    header_out = copy.deepcopy(verified_header)
    header_out.update({
        "generated_at": now(),
        "updated_at": now(),
        "ledger_snapshot_id": args.ledger_snapshot_id,
        "ledger_version": args.ledger_version,
        "ledger_checkpoint": "semantic_frozen",
        "spec_status": "passed",
        "parent_ledger_ref": relative(output, source_ledger_path),
        "parent_ledger_file_sha256": sha256_file(source_ledger_path),
        "parent_ledger_hash": source_header["current_ledger_hash"],
        "canonical_decision_index_ref": "decisions/canonical_decision_index.json",
        "canonical_decision_index_hash": decision_sha,
        "render_plan_ref": "render/render_plan.json",
        "render_plan_sha256": plan_sha,
        "template_capability_manifest_ref": relative(output, capability_path),
        "template_capability_manifest_sha256": capability_sha,
        "current_ledger_hash": canonical_hash(records_out),
        "current_ledger_hash_scope": "canonical JSON hash of ordered source_block records with frozen semantic and native media bindings",
        "media_binding_summary": {"bound_nodes": len(node_to_rep), "representation_types": dict(sorted(bound_counts.items())), "media_representation_plan_sha256": media_plan_sha},
    })
    ledger_path = output / "ledgers/canonical_block_ledger.jsonl"
    write_jsonl(ledger_path, [header_out, *records_out])
    ledger_sha = sha256_file(ledger_path)

    mapping = copy.deepcopy(verified_mapping)
    mapping.update({
        "schema_version": "semantic-mapping-ledger/1.5",
        "generated_at": now(),
        "source_ledger_snapshot_id": source_header["ledger_snapshot_id"],
        "decision_index_sha256": decision_sha,
        "media_representation_plan_sha256": media_plan_sha,
        "parent_mapping_ref": relative(output, verified_mapping_path),
        "parent_mapping_sha256": sha256_file(verified_mapping_path),
    })
    new_nodes = {node["render_node_id"]: node for node in new_plan["nodes"]}
    for assignment in mapping.get("assignments", []):
        assignment["payload_hash"] = new_nodes[assignment["render_node_id"]]["payload_hash"]
        if new_nodes[assignment["render_node_id"]].get("media_binding"):
            assignment["media_binding"] = new_nodes[assignment["render_node_id"]]["media_binding"]
    mapping_path = output / "ledgers/semantic_mapping_ledger.json"
    write_json(mapping_path, mapping)

    write_json(output / "ledgers/ledger_manifest.json", {
        "schema_version": "ledger-manifest/2.1", "generated_at": now(), "ledger_id": header_out["ledger_id"],
        "ledger_version": header_out["ledger_version"], "snapshot_id": header_out["ledger_snapshot_id"],
        "artifact_path": "ledgers/canonical_block_ledger.jsonl", "artifact_sha256": ledger_sha,
        "payload_hash": header_out["current_ledger_hash"], "parent_artifact_ref": header_out["parent_ledger_ref"],
        "parent_artifact_sha256": header_out["parent_ledger_file_sha256"], "decision_index_ref": "decisions/canonical_decision_index.json",
        "decision_index_hash": decision_sha, "spec_status": "passed", "ledger_checkpoint": "semantic_frozen", "immutable_after_publication": True,
    })

    binding_report = media_core.validate_render_binding(media_evidence_path, media_plan_path, plan_path)
    write_json(output / "reports/media_render_binding_validation.json", binding_report)
    if binding_report.get("status") != "passed":
        raise ValueError("mechanical media-to-render binding validation failed")
    drift_report = {
        "schema_version": "spec04-media-binding-drift-report/1.0", "generated_at": now(), "status": "passed",
        "verified_baseline_nodes": len(verified_plan["nodes"]), "new_nodes": len(new_plan["nodes"]),
        "node_order_unchanged": [node["render_node_id"] for node in verified_plan["nodes"]] == [node["render_node_id"] for node in new_plan["nodes"]],
        "semantic_construct_changes": 0, "construct_parameter_changes": 0, "media_bound_nodes": len(node_to_rep),
        "payload_changes_limited_to_asset_hash_binding": changed_payload_nodes,
        "representation_types": dict(sorted(bound_counts.items())),
        "scope_limit": "No semantic, construct, layout, formula, table, or upstream-cleaning choice was made.",
    }
    write_json(output / "reports/media_binding_drift_report.json", drift_report)
    manifest = {
        "schema_version": "semantic-stage-manifest/1.5", "generated_at": now(), "stage": "semantic_media_bound", "status": "passed", "ledger_checkpoint": "semantic_frozen",
        "parent_run_ref": relative(output, source_ledger_path.parent.parent),
        "decision_index_D": {"path": "decisions/canonical_decision_index.json", "sha256": decision_sha},
        "ledger_L": {"path": "ledgers/canonical_block_ledger.jsonl", "sha256": ledger_sha, "payload_hash": header_out["current_ledger_hash"]},
        "render_plan": {"path": "render/render_plan.json", "sha256": plan_sha, "deterministic_payload_hash": new_plan["deterministic_payload_hash"]},
        "semantic_mapping": {"path": "ledgers/semantic_mapping_ledger.json", "sha256": sha256_file(mapping_path)},
        "media_contract": {"evidence_sha256": sha256_file(media_evidence_path), "plan_sha256": media_plan_sha, "binding_validation_sha256": sha256_file(output / "reports/media_render_binding_validation.json")},
        "source_promotion_manifest": {"path": str(args.source_promotion_manifest.resolve()), "sha256": sha256_file(args.source_promotion_manifest.resolve()), "promotion_id": promotion["promotion_id"]},
        "promotion_registry": {"path": str(args.promotion_registry.resolve()), "sha256": sha256_file(args.promotion_registry.resolve()), "lineage_key": args.promotion_lineage_key},
        "capability_manifest": {"path": relative(output, capability_path), "sha256": capability_sha},
        "commit_order": ["decision_index_D", "render_plan_and_ledger_L", "stage_manifest_M"],
        "scope_limits": "Mechanical media binding only; semantic and construct choices are inherited unchanged from the verified baseline.",
    }
    write_json(output / "manifests/semantic_stage_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-media-ledger", type=Path, required=True)
    parser.add_argument("--media-evidence-ledger", type=Path, required=True)
    parser.add_argument("--media-representation-plan", type=Path, required=True)
    parser.add_argument("--verified-semantic-ledger", type=Path, required=True)
    parser.add_argument("--verified-render-plan", type=Path, required=True)
    parser.add_argument("--verified-semantic-mapping", type=Path, required=True)
    parser.add_argument("--capability-manifest", type=Path, required=True)
    parser.add_argument("--parent-decision-index", type=Path, required=True)
    parser.add_argument("--source-promotion-manifest", type=Path, required=True)
    parser.add_argument("--promotion-registry", type=Path, required=True)
    parser.add_argument("--promotion-lineage-key", required=True)
    parser.add_argument("--ledger-snapshot-id", required=True)
    parser.add_argument("--ledger-version", type=int, required=True)
    parser.add_argument("--decision-snapshot-id", required=True)
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "generator": VERSION, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
