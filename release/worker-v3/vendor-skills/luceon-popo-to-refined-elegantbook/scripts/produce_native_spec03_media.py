#!/usr/bin/env python3
"""Produce a native Spec 03 media ledger without consuming any render plan.

Commit order is evidence/preflight E -> decision index D -> canonical ledger L
and formal media views -> stage manifest M.  The producer never imports
semantic, ElegantBook, or render-node choices.
"""
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

VERSION = "native-spec03-media-producer/1.3.0"
FORBIDDEN_UPSTREAM_KEYS = {"render_node_id", "target_construct", "media_binding", "imported_verified_render_node_id"}


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
    if header.get("record_type") != "ledger_header" or header.get("current_ledger_hash") != canonical_hash(records):
        raise ValueError("parent canonical ledger identity or payload hash is invalid")
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


def load_execution_core():
    path = Path(__file__).with_name("execution_capability.py")
    spec = importlib.util.spec_from_file_location("execution_capability", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load execution capability core: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_visual_core():
    path = Path(__file__).with_name("visual_region_integrity.py")
    spec = importlib.util.spec_from_file_location("visual_region_integrity", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load visual-region integrity core: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def capability_resources(skill_root: Path) -> list[tuple[str, Path]]:
    names = [
        "execution-capability-manifest.schema.json",
        "spec03-media-contract-stage-manifest.schema.json",
        "canonical-block-ledger.schema.json",
        "canonical-decision-index.schema.json",
        "canonical-media-atom.schema.json",
        "media-evidence-ledger.schema.json",
        "media-representation-plan.schema.json",
        "visual-region-review-bundle.schema.json",
        "visual-region-integrity-report.schema.json",
    ]
    return [("machine_schema", skill_root / "schemas" / name) for name in names]


def capability_invocation(args: argparse.Namespace) -> list[str]:
    values = [
        "produce_native_spec03_media.py",
        "--parent-ledger", str(args.parent_ledger.resolve()),
        "--parent-decision-index", str(args.parent_decision_index.resolve()),
        "--normalized-candidates", str(args.normalized_candidates.resolve()),
        "--source-pdf", str(args.source_pdf.resolve()),
    ]
    visual_arg = getattr(args, "visual_integrity_report", None)
    if visual_arg:
        values.extend(["--visual-integrity-report", str(visual_arg.resolve())])
    for root in args.asset_root:
        values.extend(["--asset-root", root])
    values.extend([
        "--ledger-snapshot-id", args.ledger_snapshot_id,
        "--ledger-version", str(args.ledger_version),
        "--decision-snapshot-id", args.decision_snapshot_id,
        "--stage-decision-id", args.stage_decision_id,
        "--run-id", args.run_id,
        "--output-dir", str(args.output_dir.resolve()),
    ])
    return values


def assert_no_render_dependency(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_UPSTREAM_KEYS & set(value)
        if forbidden:
            raise ValueError(f"formal Spec 03 candidate package contains downstream render keys at {path}: {sorted(forbidden)}")
        for key, item in value.items():
            assert_no_render_dependency(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_render_dependency(item, f"{path}[{index}]")
    elif isinstance(value, str) and Path(value).name == "render_plan.json":
        raise ValueError(f"formal Spec 03 candidate package references a historical render plan at {path}")


def closed_decision_index(index: dict[str, Any]) -> None:
    if index.get("spec_status") != "passed":
        raise ValueError("parent decision index is not passed")
    decisions = index.get("decisions", [])
    identifiers = [item.get("decision_id") for item in decisions]
    if None in identifiers or len(identifiers) != len(set(identifiers)):
        raise ValueError("parent decision index has missing or duplicate ids")
    unresolved = [item["decision_id"] for item in decisions if item.get("status") in {"open", "stale", "invalidated"}]
    if unresolved:
        raise ValueError(f"parent decision index has unresolved decisions: {unresolved[:8]}")
    if any(not item.get("event_file") or not item.get("rule_id") for item in decisions):
        raise ValueError("parent decision inventory lacks rule_id or event_file")


def representation_core(rep: dict[str, Any]) -> dict[str, Any]:
    keys = ("representation_id", "media_id", "status", "selected_candidate_id", "representation_type", "artifact_sha256", "rule_id", "reason", "decision_refs")
    return {key: rep.get(key) for key in keys}


def produce(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite run directory: {output}")
    output.mkdir(parents=True)

    parent_ledger_path = args.parent_ledger.resolve()
    parent_index_path = args.parent_decision_index.resolve()
    normalized_path = args.normalized_candidates.resolve()
    source_pdf = args.source_pdf.resolve()
    roots = parse_roots(args.asset_root)
    parent_header, parent_records = read_ledger(parent_ledger_path)
    parent_index = read_json(parent_index_path)
    normalized = read_json(normalized_path)
    closed_decision_index(parent_index)
    assert_no_render_dependency(normalized)
    if parent_header.get("ledger_checkpoint") != "source_reconciled" or parent_header.get("spec_status") != "passed":
        raise ValueError("native producer requires a passed source_reconciled parent ledger")
    if parent_header.get("current_ledger_hash_scope") != "canonical JSON hash of ordered source_block records":
        raise ValueError("native producer requires the standard source_block-only parent ledger hash scope")
    if parent_header.get("canonical_decision_index_hash") != sha256_file(parent_index_path):
        raise ValueError("parent ledger is not bound to the supplied decision index")
    if any(record.get("media_contract") or record.get("media_contracts") for record in parent_records):
        raise ValueError("fresh native producer refuses a parent ledger that already owns media contracts")
    source_sha = sha256_file(source_pdf)
    if parent_header.get("material_identity", {}).get("source_pdf_sha256") != source_sha:
        raise ValueError("source PDF differs from the parent canonical ledger")
    if normalized.get("schema_version") not in {"normalized-media-candidates/1.0", "normalized-media-candidates/1.1", "normalized-media-candidates/1.2"}:
        raise ValueError("unsupported normalized media candidate schema")
    if normalized.get("source_pdf", {}).get("sha256") != source_sha:
        raise ValueError("normalized media candidates differ from the parent source PDF")
    normalized_parent = normalized.get("parent_canonical_ledger", {})
    if normalized_parent.get("sha256") != sha256_file(parent_ledger_path) or normalized_parent.get("payload_hash") != parent_header.get("current_ledger_hash") or normalized_parent.get("ledger_snapshot_id") != parent_header.get("ledger_snapshot_id"):
        raise ValueError("normalized media candidates are not bound to the exact parent canonical ledger")
    visual_arg = getattr(args, "visual_integrity_report", None)
    visual_report_path = visual_arg.resolve() if visual_arg else None
    visual_validation = None
    if normalized.get("schema_version") == "normalized-media-candidates/1.2" and visual_report_path is None:
        raise ValueError("visual-integrity-enriched candidates require the exact visual integrity report")
    if visual_report_path is not None:
        visual_report = read_json(visual_report_path)
        if visual_report.get("status") != "passed":
            raise ValueError("visual integrity report is not passed")
        if visual_report.get("output_parent_ledger", {}).get("sha256") != sha256_file(parent_ledger_path):
            raise ValueError("visual integrity report does not bind the supplied parent ledger")
        if visual_report.get("output_decision_index", {}).get("sha256") != sha256_file(parent_index_path):
            raise ValueError("visual integrity report does not bind the supplied parent decision index")
        if visual_report.get("output_normalized_candidates", {}).get("sha256") != sha256_file(normalized_path):
            raise ValueError("visual integrity report does not bind the supplied normalized candidates")
        visual_core = load_visual_core()
        visual_validation = visual_core.validate_run(visual_report_path.parents[1])
    parent_by_id = {record["block_id"]: record for record in parent_records}
    atom_ids: set[str] = set()
    fragment_owner: dict[str, str] = {}
    for atom in normalized.get("atoms", []):
        media_id = atom.get("media_id")
        if not media_id or media_id in atom_ids:
            raise ValueError(f"missing or duplicate media id: {media_id}")
        atom_ids.add(media_id)
        block_ids = atom.get("source_block_ids", [])
        if not block_ids or any(block_id not in parent_by_id for block_id in block_ids):
            raise ValueError(f"media atom does not resolve to parent source blocks: {media_id}")
        if atom.get("inclusion_status") == "included" and any(parent_by_id[block_id].get("scope_status") != "included" for block_id in block_ids):
            raise ValueError(f"included media atom points to a non-included source block: {media_id}")
        for block_id in block_ids:
            if block_id in fragment_owner:
                raise ValueError(f"source block is assigned to multiple media atoms: {block_id}")
            fragment_owner[block_id] = media_id

    core = load_media_core()
    preflight_dir = output / "precommit"
    preflight_dir.mkdir()
    preflight_ledger, preflight_plan, preflight_queue = core.build_contracts(normalized_path, source_pdf, roots, preflight_dir)
    preflight_validation = core.validate_contracts(preflight_dir / "media_evidence_ledger.json", preflight_dir / "media_representation_plan.json")
    write_json(output / "reports/precommit_media_validation.json", preflight_validation)
    if preflight_plan.get("spec_status") != "passed" or preflight_queue.get("open_items") != 0 or preflight_validation.get("status") != "passed":
        attempt = {
            "schema_version": "spec03-media-production-attempt/1.0", "generated_at": now(),
            "status": "needs_review" if preflight_plan.get("spec_status") == "needs_review" else "failed",
            "producer": VERSION, "render_plan_dependency": False,
            "normalized_candidates": {"path": str(normalized_path), "sha256": sha256_file(normalized_path)},
            "open_reviews": preflight_queue.get("open_items"),
            "scope_limit": "No D, L, formal stage manifest, or promotion-eligible run was created.",
        }
        write_json(output / "manifests/attempt_manifest.json", attempt)
        return attempt, 3 if attempt["status"] == "needs_review" else 2

    skill_root = Path(__file__).parents[1].resolve()
    execution_core = load_execution_core()
    capability_path = output / "precommit/execution_capability_manifest.json"
    capability_manifest = execution_core.build_manifest(
        manifest_id=f"{args.run_id}-producer-capability",
        skill_root=skill_root,
        entrypoints=[
            ("stage_producer", Path(__file__).resolve()),
            ("execution_capability_core", Path(__file__).with_name("execution_capability.py").resolve()),
            ("media_representation_core", Path(__file__).with_name("media_source_representation.py").resolve()),
            *([("visual_region_integrity_core", Path(__file__).with_name("visual_region_integrity.py").resolve())] if visual_report_path else []),
        ],
        resources=capability_resources(skill_root),
        invocation=capability_invocation(args),
        producer=VERSION,
    )
    write_json(capability_path, capability_manifest)
    execution_core.validate_manifest(capability_path)
    capability_sha = sha256_file(capability_path)

    preflight_evidence = [
        {"role": "parent_canonical_ledger", "path": str(parent_ledger_path), "sha256": sha256_file(parent_ledger_path)},
        {"role": "parent_decision_index", "path": str(parent_index_path), "sha256": sha256_file(parent_index_path)},
        {"role": "normalized_media_candidates", "path": str(normalized_path), "sha256": sha256_file(normalized_path)},
        {"role": "source_pdf", "path": str(source_pdf), "sha256": source_sha},
        {"role": "precommit_media_evidence", "path": "precommit/media_evidence_ledger.json", "sha256": sha256_file(preflight_dir / "media_evidence_ledger.json")},
        {"role": "precommit_media_plan", "path": "precommit/media_representation_plan.json", "sha256": sha256_file(preflight_dir / "media_representation_plan.json")},
        {"role": "precommit_media_validation", "path": "reports/precommit_media_validation.json", "sha256": sha256_file(output / "reports/precommit_media_validation.json")},
        {"role": "execution_capability", "path": "precommit/execution_capability_manifest.json", "sha256": capability_sha},
        *([{"role": "visual_region_integrity_report", "path": str(visual_report_path), "sha256": sha256_file(visual_report_path)}] if visual_report_path else []),
    ]
    event = {
        "decision_id": args.stage_decision_id, "status": "closed", "rule_id": "CV-H04/NATIVE-MEDIA-CONTRACT-COMMIT",
        "decided_at": now(), "decision_type": "deterministic_stage_commit",
        "scope": "Commit the independently preflighted media candidates and representations into a native Spec 03 canonical ledger without downstream render evidence.",
        "evidence": preflight_evidence,
        "prohibitions": ["render_plan_input", "semantic_construct_choice", "formula_reconstruction", "table_reconstruction", "upstream_cleaning_rewrite"],
        "supersedes": [], "invalidated_by": None,
    }
    event_path = output / "decisions/media_production_decisions.jsonl"
    write_jsonl(event_path, [event])
    decisions = copy.deepcopy(parent_index["decisions"])
    if args.stage_decision_id in {item.get("decision_id") for item in decisions}:
        raise ValueError(f"stage decision id already exists: {args.stage_decision_id}")
    decisions.append({
        "decision_id": args.stage_decision_id, "event_file": "decisions/media_production_decisions.jsonl",
        "rule_id": event["rule_id"], "status": "closed", "supersedes": [], "invalidated_by": None,
    })
    statuses = Counter(item.get("status", "unknown") for item in decisions)
    decision_index = {
        "schema_version": "canonical-decision-index/1.1", "decision_index_id": parent_index["decision_index_id"],
        "snapshot_id": args.decision_snapshot_id, "version": int(parent_index["version"]) + 1, "generated_at": now(),
        "parent_index_ref": relative(output, parent_index_path), "parent_index_hash": sha256_file(parent_index_path),
        "acyclic_commit_rule": "evidence_or_parent_then_decision_index_D_then_child_artifact_L",
        "spec_status": "passed", "evidence_committed_before_index": preflight_evidence,
        "decision_event_files": [{"path": "decisions/media_production_decisions.jsonl", "sha256": sha256_file(event_path), "decision_ids": [args.stage_decision_id]}],
        "decisions": decisions,
        "summary": {"closed": statuses["closed"], "superseded": statuses["superseded"], "open": 0, "stale": 0, "invalidated": 0},
    }
    decision_path = output / "decisions/canonical_decision_index.json"
    write_json(decision_path, decision_index)  # D
    decision_sha = sha256_file(decision_path)

    reps = {item["media_id"]: item for item in preflight_plan["representations"]}
    records_out = copy.deepcopy(parent_records)
    out_by_id = {record["block_id"]: record for record in records_out}
    representation_counts: Counter[str] = Counter()
    for atom in normalized["atoms"]:
        rep = reps[atom["media_id"]]
        contract = copy.deepcopy(atom)
        contract["media_contract_schema_version"] = "canonical-media-atom/1.1"
        contract["frozen_representation"] = representation_core(rep)
        contract["contract_decision_refs"] = sorted(set(rep.get("decision_refs", [])) | {args.stage_decision_id})
        contract["precommit_evidence"] = {
            "media_evidence_ledger_sha256": sha256_file(preflight_dir / "media_evidence_ledger.json"),
            "media_representation_plan_sha256": sha256_file(preflight_dir / "media_representation_plan.json"),
            "media_validation_sha256": sha256_file(output / "reports/precommit_media_validation.json"),
        }
        representation_counts[rep.get("representation_type") or "none"] += 1
        for block_id in atom["source_block_ids"]:
            record = out_by_id[block_id]
            record.setdefault("media_contracts", []).append(contract)
            record["media_contract_status"] = "frozen"
            record["human_decision_refs"] = sorted(set(record.get("human_decision_refs", [])) | set(contract["contract_decision_refs"]))

    header_out = copy.deepcopy(parent_header)
    header_out.update({
        "generated_at": now(), "updated_at": now(), "ledger_snapshot_id": args.ledger_snapshot_id,
        "ledger_version": args.ledger_version, "ledger_checkpoint": "source_reconciled", "spec_status": "passed",
        "parent_ledger_ref": relative(output, parent_ledger_path), "parent_ledger_file_sha256": sha256_file(parent_ledger_path),
        "parent_ledger_hash": parent_header["current_ledger_hash"],
        "canonical_decision_index_ref": "decisions/canonical_decision_index.json", "canonical_decision_index_hash": decision_sha,
        "current_ledger_hash": canonical_hash(records_out),
        "current_ledger_hash_scope": "canonical JSON hash of ordered source_block records including native media_contracts",
        "media_contract": {
            "schema_version": "canonical-media-atom/1.1", "status": "frozen", "media_atoms": len(normalized["atoms"]),
            "representation_types": dict(sorted(representation_counts.items())), "migration_evidence_only": False,
            "producer": VERSION, "render_plan_dependency": False,
        },
    })
    ledger_path = output / "ledgers/canonical_block_ledger.jsonl"
    write_jsonl(ledger_path, [header_out, *records_out])  # L
    ledger_sha = sha256_file(ledger_path)
    write_json(output / "ledgers/ledger_manifest.json", {
        "schema_version": "ledger-manifest/2.1", "generated_at": now(), "ledger_id": header_out["ledger_id"],
        "ledger_version": header_out["ledger_version"], "snapshot_id": header_out["ledger_snapshot_id"],
        "artifact_path": "ledgers/canonical_block_ledger.jsonl", "artifact_sha256": ledger_sha,
        "payload_hash": header_out["current_ledger_hash"], "parent_artifact_ref": header_out["parent_ledger_ref"],
        "parent_artifact_sha256": header_out["parent_ledger_file_sha256"], "decision_index_ref": "decisions/canonical_decision_index.json",
        "decision_index_hash": decision_sha, "spec_status": "passed", "ledger_checkpoint": "source_reconciled", "immutable_after_publication": True,
    })

    formal_dir = output / "media"
    normalized_projection = core.normalized_from_canonical(ledger_path, decision_path, source_pdf, formal_dir)
    formal_ledger, formal_plan, formal_queue = core.build_contracts(normalized_projection, source_pdf, roots, formal_dir)
    formal_validation = core.validate_contracts(formal_dir / "media_evidence_ledger.json", formal_dir / "media_representation_plan.json")
    write_json(output / "reports/media_contract_validation.json", formal_validation)
    if formal_plan.get("spec_status") != "passed" or formal_queue.get("open_items") != 0 or formal_validation.get("status") != "passed":
        raise ValueError("formal native media views failed after canonical commit")
    formal_reps = {item["media_id"]: representation_core(item) for item in formal_plan["representations"]}
    preflight_reps = {item["media_id"]: representation_core(item) for item in preflight_plan["representations"]}
    if formal_reps != preflight_reps:
        raise ValueError("representation changed between preflight evidence and canonical-derived formal view")

    manifest = {
        "schema_version": "spec03-media-contract-stage-manifest/1.3", "generated_at": now(),
        "stage": "source_reconciled_media_contract_frozen", "status": "passed", "spec_status": "passed",
        "ledger_checkpoint": "source_reconciled", "producer_mode": "formal_native", "render_plan_dependency": False,
        "execution_capability_E": {"path": "precommit/execution_capability_manifest.json", "sha256": capability_sha, "payload_hash": capability_manifest["payload_hash"]},
        "decision_index_D": {"path": "decisions/canonical_decision_index.json", "sha256": decision_sha},
        "ledger_L": {"path": "ledgers/canonical_block_ledger.jsonl", "sha256": ledger_sha, "payload_hash": header_out["current_ledger_hash"]},
        "precommit_evidence": {
            "media_evidence_ledger": {"path": "precommit/media_evidence_ledger.json", "sha256": sha256_file(preflight_dir / "media_evidence_ledger.json")},
            "media_representation_plan": {"path": "precommit/media_representation_plan.json", "sha256": sha256_file(preflight_dir / "media_representation_plan.json")},
            "validation": {"path": "reports/precommit_media_validation.json", "sha256": sha256_file(output / "reports/precommit_media_validation.json")},
        },
        "media_evidence_ledger": {"path": "media/media_evidence_ledger.json", "sha256": sha256_file(formal_dir / "media_evidence_ledger.json"), "payload_hash": formal_ledger["payload_hash"]},
        "media_representation_plan": {"path": "media/media_representation_plan.json", "sha256": sha256_file(formal_dir / "media_representation_plan.json"), "payload_hash": formal_plan["payload_hash"]},
        "validation": {"path": "reports/media_contract_validation.json", "sha256": sha256_file(output / "reports/media_contract_validation.json")},
        **({"visual_region_integrity": {"path": str(visual_report_path), "sha256": sha256_file(visual_report_path), "live_validation": visual_validation}} if visual_report_path else {}),
        "commit_order": ["precommit_evidence_and_execution_capability_E", "decision_index_D", "canonical_ledger_and_media_views_L", "stage_manifest_M"],
        "promotion_status": "not_evaluated",
        "scope_limits": "Native Spec 03 media production only; no semantic/render-plan input, formula reconstruction, table reconstruction, or upstream cleaning rewrite.",
    }
    manifest_path = output / "manifests/spec03_media_contract_manifest.json"
    write_json(manifest_path, manifest)  # M
    files = [{"path": path.relative_to(output).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in sorted(output.rglob("*")) if path.is_file() and path.name != "run_manifest.json"]
    write_json(output / "manifests/run_manifest.json", {
        "schema_version": "run-manifest/2.0", "generated_at": now(), "run_id": args.run_id,
        "stage": "source_reconciled_media_contract_frozen", "status": "passed", "file_count_excluding_self": len(files),
        "files": files, "immutable_after_publication": True,
    })
    return manifest, 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-ledger", type=Path, required=True)
    parser.add_argument("--parent-decision-index", type=Path, required=True)
    parser.add_argument("--normalized-candidates", type=Path, required=True)
    parser.add_argument("--source-pdf", type=Path, required=True)
    parser.add_argument("--visual-integrity-report", type=Path)
    parser.add_argument("--asset-root", action="append", default=[])
    parser.add_argument("--ledger-snapshot-id", required=True)
    parser.add_argument("--ledger-version", type=int, required=True)
    parser.add_argument("--decision-snapshot-id", required=True)
    parser.add_argument("--stage-decision-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result, exit_code = produce(args)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    except Exception as exc:
        print(json.dumps({"status": "failed", "producer": VERSION, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
