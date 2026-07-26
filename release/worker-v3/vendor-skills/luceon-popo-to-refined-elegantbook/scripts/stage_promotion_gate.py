#!/usr/bin/env python3
"""Independently promote or reject immutable stage runs.

Stage self-reported status is evidence, never promotion authority.  Promotion
manifests and registries live outside immutable runs and bind exact bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

VERSION = "stage-promotion-gate/1.7.0"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite immutable promotion artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def scalar_strings(value: Any) -> set[str]:
    if isinstance(value, dict):
        result: set[str] = set()
        for item in value.values():
            result.update(scalar_strings(item))
        return result
    if isinstance(value, list):
        result = set()
        for item in value:
            result.update(scalar_strings(item))
        return result
    return {value} if isinstance(value, str) else set()


def load_media_core():
    path = Path(__file__).with_name("media_source_representation.py")
    spec = importlib.util.spec_from_file_location("media_source_representation", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load media core: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_source_integrity_core():
    path = Path(__file__).with_name("build_source_lineage_integrity.py")
    spec = importlib.util.spec_from_file_location("build_source_lineage_integrity", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load source-integrity core: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_visual_integrity_core():
    path = Path(__file__).with_name("visual_region_integrity.py")
    spec = importlib.util.spec_from_file_location("visual_region_integrity_gate", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load visual-region integrity core: {path}")
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


def load_spec04a_structure_core():
    path = Path(__file__).with_name("spec04a_structure_contract.py")
    spec = importlib.util.spec_from_file_location("spec04a_structure_contract_gate", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Spec 04-A structure core: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_spec04b_semantic_core():
    path = Path(__file__).with_name("spec04b_semantic_span_contract.py")
    spec = importlib.util.spec_from_file_location("spec04b_semantic_span_contract_gate", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Spec 04-B semantic core: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_spec04c_construct_core():
    path = Path(__file__).with_name("spec04c_construct_binding_contract.py")
    spec = importlib.util.spec_from_file_location("spec04c_construct_binding_contract_gate", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Spec 04-C construct core: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_spec04d_render_core():
    path = Path(__file__).with_name("spec04d_render_plan_contract.py")
    spec = importlib.util.spec_from_file_location("spec04d_render_plan_contract_gate", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Spec 04-D render-plan core: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_spec05_native_core():
    path = Path(__file__).with_name("spec05_native_execution_gate.py")
    spec = importlib.util.spec_from_file_location("spec05_native_execution_gate", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Spec 05 native execution gate: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluator_resources(skill_root: Path) -> list[tuple[str, Path]]:
    names = [
        "execution-capability-manifest.schema.json",
        "spec03-media-contract-stage-manifest.schema.json",
        "stage-promotion-manifest.schema.json",
        "source-lineage-integrity-report.schema.json",
        "media-evidence-ledger.schema.json",
        "media-representation-plan.schema.json",
        "visual-region-review-bundle.schema.json",
        "visual-region-integrity-report.schema.json",
    ]
    return [("machine_schema", skill_root / "schemas" / name) for name in names]


def evaluator_invocation(args: argparse.Namespace, capability_output: Path) -> list[str]:
    values = [
        "stage_promotion_gate.py", "evaluate-spec03-media",
        "--run-dir", str(args.run_dir.resolve()),
        "--promotion-id", args.promotion_id,
        "--lineage-key", args.lineage_key,
    ]
    if getattr(args, "source_integrity_report", None):
        values.extend(["--source-integrity-report", str(args.source_integrity_report.resolve())])
    values.extend([
        "--evaluator-capability-output", str(capability_output),
        "--output", str(args.output.resolve()),
    ])
    return values


def spec04a_evaluator_resources(skill_root: Path) -> list[tuple[str, Path]]:
    names = [
        "execution-capability-manifest.schema.json",
        "spec04a-outline-review-bundle.schema.json",
        "spec04a-structure-contract.schema.json",
        "spec04a-structure-stage-manifest.schema.json",
        "stage-promotion-manifest.schema.json",
        "promotion-registry.schema.json",
    ]
    return [("machine_schema", skill_root / "schemas" / name) for name in names]


def spec04a_evaluator_invocation(args: argparse.Namespace, capability_output: Path) -> list[str]:
    return [
        "stage_promotion_gate.py", "evaluate-spec04a-structure",
        "--run-dir", str(args.run_dir.resolve()),
        "--promotion-id", args.promotion_id,
        "--lineage-key", args.lineage_key,
        "--evaluator-capability-output", str(capability_output),
        "--output", str(args.output.resolve()),
    ]


def spec04b_evaluator_resources(skill_root: Path) -> list[tuple[str, Path]]:
    names = [
        "execution-capability-manifest.schema.json",
        "spec04b-semantic-review-bundle.schema.json",
        "spec04b-semantic-span-contract.schema.json",
        "spec04b-semantic-stage-manifest.schema.json",
        "stage-promotion-manifest.schema.json",
        "promotion-registry.schema.json",
    ]
    return [("machine_schema", skill_root / "schemas" / name) for name in names]


def spec04b_evaluator_invocation(args: argparse.Namespace, capability_output: Path) -> list[str]:
    return [
        "stage_promotion_gate.py", "evaluate-spec04b-semantic-spans",
        "--run-dir", str(args.run_dir.resolve()),
        "--promotion-id", args.promotion_id,
        "--lineage-key", args.lineage_key,
        "--evaluator-capability-output", str(capability_output),
        "--output", str(args.output.resolve()),
    ]


def spec04c_evaluator_resources(skill_root: Path) -> list[tuple[str, Path]]:
    names = [
        "execution-capability-manifest.schema.json",
        "spec04c-construct-review-bundle.schema.json",
        "template-capability-manifest.schema.json",
        "spec04c-construct-binding-contract.schema.json",
        "spec04c-construct-stage-manifest.schema.json",
        "stage-promotion-manifest.schema.json",
        "promotion-registry.schema.json",
    ]
    return [("machine_schema", skill_root / "schemas" / name) for name in names]


def spec04c_evaluator_invocation(args: argparse.Namespace, capability_output: Path) -> list[str]:
    return [
        "stage_promotion_gate.py", "evaluate-spec04c-construct-bindings",
        "--run-dir", str(args.run_dir.resolve()),
        "--promotion-id", args.promotion_id,
        "--lineage-key", args.lineage_key,
        "--evaluator-capability-output", str(capability_output),
        "--output", str(args.output.resolve()),
    ]


class Gate:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(self, check_id: str, fn: Callable[[], Any]) -> None:
        try:
            evidence = fn()
            self.checks.append({"check_id": check_id, "status": "passed", "evidence": evidence})
        except Exception as exc:
            self.checks.append({"check_id": check_id, "status": "failed", "detail": str(exc)})

    @property
    def passed(self) -> bool:
        return all(item["status"] == "passed" for item in self.checks)


def resolve_artifact(run: Path, item: dict[str, Any]) -> Path:
    path = run / item["path"]
    if not path.is_file() or sha256_file(path) != item.get("sha256"):
        raise ValueError(f"stage artifact is missing or hash-drifted: {item.get('path')}")
    return path


def evaluate_spec03_media(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    run = args.run_dir.resolve()
    output = args.output.resolve()
    execution_core = load_execution_core()
    skill_root = Path(__file__).parents[1].resolve()
    capability_output_arg = getattr(args, "evaluator_capability_output", None)
    evaluator_capability_path = capability_output_arg.resolve() if capability_output_arg else output.with_suffix(".evaluator-capability.json")
    evaluator_capability = execution_core.build_manifest(
        manifest_id=f"{args.promotion_id}-evaluator-capability",
        skill_root=skill_root,
        entrypoints=[
            ("promotion_evaluator", Path(__file__).resolve()),
            ("execution_capability_core", Path(__file__).with_name("execution_capability.py").resolve()),
            ("media_representation_core", Path(__file__).with_name("media_source_representation.py").resolve()),
            ("source_lineage_integrity_core", Path(__file__).with_name("build_source_lineage_integrity.py").resolve()),
            ("visual_region_integrity_core", Path(__file__).with_name("visual_region_integrity.py").resolve()),
        ],
        resources=evaluator_resources(skill_root),
        invocation=evaluator_invocation(args, evaluator_capability_path),
        producer=VERSION,
    )
    write_json(evaluator_capability_path, evaluator_capability)
    stage_path = run / "manifests/spec03_media_contract_manifest.json"
    if not stage_path.is_file():
        raise FileNotFoundError(f"Spec 03 media stage manifest missing: {stage_path}")
    stage = read_json(stage_path)
    gate = Gate()
    artifacts: dict[str, dict[str, Any]] = {}
    context: dict[str, Any] = {}

    def stage_shape() -> dict[str, Any]:
        if not str(stage.get("schema_version", "")).startswith("spec03-media-contract-stage-manifest/"):
            raise ValueError("unsupported stage manifest schema")
        if stage.get("status") != "passed" or stage.get("ledger_checkpoint") != "source_reconciled":
            raise ValueError("stage did not self-report a passed source_reconciled checkpoint")
        required = {"decision_index_D", "ledger_L", "media_evidence_ledger", "media_representation_plan", "validation"}
        if stage.get("producer_mode") == "formal_native":
            required.add("execution_capability_E")
            required.add("visual_region_integrity")
        missing = sorted(required - stage.keys())
        if missing:
            raise ValueError(f"stage manifest lacks required artifacts: {missing}")
        return {"schema_version": stage["schema_version"], "self_reported_status": stage["status"]}

    def artifact_hashes() -> dict[str, Any]:
        for name in ("decision_index_D", "ledger_L", "media_evidence_ledger", "media_representation_plan", "validation"):
            path = resolve_artifact(run, stage[name])
            artifacts[name] = {"path": str(path), "sha256": sha256_file(path)}
        if stage.get("execution_capability_E"):
            path = resolve_artifact(run, stage["execution_capability_E"])
            artifacts["producer_execution_capability"] = {"path": str(path), "sha256": sha256_file(path)}
        if stage.get("visual_region_integrity"):
            path = resolve_artifact(run, stage["visual_region_integrity"])
            artifacts["visual_region_integrity"] = {"path": str(path), "sha256": sha256_file(path)}
        return {"artifacts": len(artifacts)}

    def decision_closure() -> dict[str, Any]:
        path = Path(artifacts["decision_index_D"]["path"])
        index = read_json(path)
        context["decision_index"] = index
        if index.get("spec_status") != "passed":
            raise ValueError("decision index lacks spec_status=passed")
        items = index.get("decisions", [])
        identifiers = [item.get("decision_id") for item in items]
        if None in identifiers or len(identifiers) != len(set(identifiers)):
            raise ValueError("decision ids are missing or duplicated")
        unresolved = [item["decision_id"] for item in items if item.get("status") in {"open", "stale", "invalidated"}]
        if unresolved:
            raise ValueError(f"decision index contains unresolved decisions: {unresolved[:8]}")
        return {"decisions": len(items), "unresolved": 0}

    def ledger_identity() -> dict[str, Any]:
        path = Path(artifacts["ledger_L"]["path"])
        with path.open(encoding="utf-8") as stream:
            header = json.loads(next(stream))
            records = [json.loads(line) for line in stream if line.strip()]
        context["ledger_header"] = header
        context["ledger_records"] = records
        if header.get("spec_status") != "passed" or header.get("ledger_checkpoint") != "source_reconciled":
            raise ValueError("canonical ledger is not passed at source_reconciled")
        if header.get("current_ledger_hash") != canonical_hash(records):
            raise ValueError("canonical ledger payload hash mismatch")
        if header.get("canonical_decision_index_hash") != artifacts["decision_index_D"]["sha256"]:
            raise ValueError("canonical ledger is not bound to the stage decision index")
        if stage["ledger_L"].get("payload_hash") != header["current_ledger_hash"]:
            raise ValueError("stage manifest ledger payload hash mismatch")
        return {"snapshot_id": header.get("ledger_snapshot_id"), "records": len(records), "payload_hash": header["current_ledger_hash"]}

    def acyclic_commit() -> dict[str, Any]:
        index_values = scalar_strings(context["decision_index"])
        forbidden = [context["ledger_header"].get("ledger_snapshot_id"), context["ledger_header"].get("current_ledger_hash"), artifacts["ledger_L"]["sha256"]]
        found = [item for item in forbidden if item and item in index_values]
        if found:
            raise ValueError(f"decision index references child ledger identities: {found}")
        if "decision_index_D_then_child_artifact_L" not in context["decision_index"].get("acyclic_commit_rule", ""):
            raise ValueError("D-to-L acyclic rule is absent")
        return {"forbidden_child_references": 0}

    def media_contract_live() -> dict[str, Any]:
        core = load_media_core()
        report = core.validate_contracts(Path(artifacts["media_evidence_ledger"]["path"]), Path(artifacts["media_representation_plan"]["path"]))
        stored = read_json(Path(artifacts["validation"]["path"]))
        plan = read_json(Path(artifacts["media_representation_plan"]["path"]))
        if report.get("status") != "passed" or stored.get("status") != "passed" or plan.get("spec_status") != "passed" or plan.get("open_reviews") != 0:
            raise ValueError("live or stored media contract validation is not passed")
        return {"representations": plan.get("summary", {}).get("representations"), "open_reviews": 0, "live_checks": report["summary"]}

    def native_mode_integrity() -> dict[str, Any]:
        header = context["ledger_header"]
        summary = header.get("media_contract", {})
        mode = stage.get("producer_mode") or ("migration_compatibility" if summary.get("migration_evidence_only") else "unknown")
        if mode not in {"formal_native", "migration_compatibility"}:
            raise ValueError(f"stage does not declare a supported producer mode: {mode}")
        context["promotion_class"] = "formal_native" if mode == "formal_native" else "migration_compatibility"
        if context["promotion_class"] == "formal_native":
            if stage.get("render_plan_dependency") is not False or summary.get("render_plan_dependency") is not False or summary.get("migration_evidence_only") is not False:
                raise ValueError("formal-native stage does not prove downstream render independence")
            run_manifest_path = run / "manifests/run_manifest.json"
            if not run_manifest_path.is_file():
                raise ValueError("formal-native run lacks immutable run manifest")
            run_manifest = read_json(run_manifest_path)
            drifted = [item["path"] for item in run_manifest.get("files", []) if not (run / item["path"]).is_file() or sha256_file(run / item["path"]) != item["sha256"]]
            if run_manifest.get("status") != "passed" or not run_manifest.get("immutable_after_publication") or drifted:
                raise ValueError(f"formal-native run manifest is invalid or drifted: {drifted[:8]}")
            artifacts["run_manifest"] = {"path": str(run_manifest_path), "sha256": sha256_file(run_manifest_path)}
        return {"promotion_class": context["promotion_class"], "render_plan_dependency": stage.get("render_plan_dependency")}

    def source_integrity_report() -> dict[str, Any]:
        if context.get("promotion_class") != "formal_native":
            return {"required": False, "promotion_class": context.get("promotion_class")}
        supplied = getattr(args, "source_integrity_report", None)
        if not supplied:
            raise ValueError("formal-native promotion requires --source-integrity-report")
        path = Path(supplied).resolve()
        core = load_source_integrity_core()
        report = core.validate_report(path)
        header = context["ledger_header"]
        parent_ledger = report["source_parent_ledger"]
        parent_decision = report["source_parent_decision_index"]
        if header.get("parent_ledger_file_sha256") != parent_ledger["sha256"] or header.get("parent_ledger_hash") != parent_ledger["payload_hash"]:
            raise ValueError("child canonical ledger does not descend from the integrity report's source ledger")
        if context["decision_index"].get("parent_index_hash") != parent_decision["sha256"]:
            raise ValueError("child decision index does not descend from the integrity report's source decision index")
        artifacts["source_integrity_report"] = {"path": str(path), "sha256": sha256_file(path)}
        context["source_integrity"] = report
        return {"required": True, "report_id": report["report_id"], "scope_mode": report["scope_mode"], "live_gates": report["summary"]}

    def cumulative_child_decisions() -> dict[str, Any]:
        if context.get("promotion_class") != "formal_native":
            return {"required": False}
        report = context["source_integrity"]
        parent = read_json(Path(report["source_parent_decision_index"]["path"]))
        child = context["decision_index"]
        parent_by_id = {item["decision_id"]: item for item in parent.get("decisions", [])}
        child_by_id = {item["decision_id"]: item for item in child.get("decisions", [])}
        missing = sorted(set(parent_by_id) - set(child_by_id))
        changed = sorted(decision_id for decision_id, item in parent_by_id.items() if child_by_id.get(decision_id) != item)
        added = sorted(set(child_by_id) - set(parent_by_id))
        if missing or changed or len(added) != 1:
            raise ValueError(f"child decision inheritance failed: missing={missing[:8]} changed={changed[:8]} added={added[:8]}")
        if int(child.get("version", 0)) != int(parent.get("version", 0)) + 1:
            raise ValueError("child decision version is not parent version + 1")
        if any(not item.get("event_file") or not item.get("rule_id") for item in child.get("decisions", [])):
            raise ValueError("child decision inventory lacks rule_id or event_file")
        return {"parent_decisions": len(parent_by_id), "inherited_unchanged": len(parent_by_id), "stage_decisions_added": added}

    def child_fragment_partition() -> dict[str, Any]:
        if context.get("promotion_class") != "formal_native":
            return {"required": False}
        report = context["source_integrity"]
        normalized = read_json(Path(report["normalized_media_candidates"]["path"]))
        expected = {atom["media_id"]: atom for atom in normalized.get("atoms", [])}
        contracts: dict[str, dict[str, Any]] = {}
        seen_on: dict[str, set[str]] = {}
        fragment_owner: dict[str, str] = {}
        for record in context["ledger_records"]:
            block_id = record["block_id"]
            for contract in record.get("media_contracts", []):
                media_id = contract.get("media_id")
                if media_id not in expected:
                    raise ValueError(f"child ledger contains an unexpected media contract: {media_id}")
                if block_id not in contract.get("source_block_ids", []):
                    raise ValueError(f"media contract is attached outside its declared fragment set: {media_id} -> {block_id}")
                if block_id in fragment_owner and fragment_owner[block_id] != media_id:
                    raise ValueError(f"source fragment belongs to multiple media atoms: {block_id}")
                fragment_owner[block_id] = media_id
                if media_id in contracts and contracts[media_id] != contract:
                    raise ValueError(f"repeated fragment copies of a media contract differ: {media_id}")
                contracts[media_id] = contract
                seen_on.setdefault(media_id, set()).add(block_id)
        if set(contracts) != set(expected):
            raise ValueError("child media contract inventory differs from normalized candidates")
        multi = 0
        for media_id, atom in expected.items():
            expected_refs = set(atom.get("source_block_ids", []))
            if set(contracts[media_id].get("source_block_ids", [])) != expected_refs or seen_on.get(media_id, set()) != expected_refs:
                raise ValueError(f"child media fragment copies are incomplete or over-assigned: {media_id}")
            if len(expected_refs) > 1:
                multi += 1
        expected_fragment_count = report["media_fragment_binding"]["fragment_assignments"]
        if len(fragment_owner) != expected_fragment_count or multi != report["media_fragment_binding"]["multi_fragment_atoms"]:
            raise ValueError("child fragment partition differs from the source-integrity report")
        return {"media_atoms": len(contracts), "fragment_assignments": len(fragment_owner), "multi_fragment_atoms": multi, "duplicate_fragment_assignments": 0}

    def standard_identity_and_review_binding() -> dict[str, Any]:
        if context.get("promotion_class") != "formal_native":
            return {"required": False}
        header = context["ledger_header"]
        report = context["source_integrity"]
        if header.get("record_type") != "ledger_header":
            raise ValueError("child ledger lacks the standard ledger header record type")
        if header.get("current_ledger_hash_scope") != "canonical JSON hash of ordered source_block records including native media_contracts":
            raise ValueError("child ledger uses a non-standard native-media payload hash scope")
        review = report.get("review_queue_precision", {})
        if any(review.get(key) != 0 for key in ("signal_only_events", "unresolved_events", "risk_pages_missing_review", "risk_events_missing_evidence")):
            raise ValueError("source-order review queue is not precisely and completely closed")
        return {"ledger_hash_scope": header["current_ledger_hash_scope"], "source_scope_mode": report["scope_mode"], "reviewed_pages": review["reviewed_pages"], "risk_events": review["risk_events"]}

    def visual_region_integrity() -> dict[str, Any]:
        if context.get("promotion_class") != "formal_native":
            return {"required": False}
        item = artifacts.get("visual_region_integrity")
        if not item:
            raise ValueError("formal-native Spec 03 promotion lacks visual-region integrity evidence")
        report = read_json(Path(item["path"]))
        if report.get("status") != "passed" or report.get("summary", {}).get("open_reviews") != 0:
            raise ValueError("visual-region integrity report is not passed and closed")
        source_report = context.get("source_integrity", {})
        bound = source_report.get("visual_region_integrity", {})
        if bound.get("sha256") != item["sha256"]:
            raise ValueError("source-lineage report and media stage do not bind the same visual-region report")
        live = load_visual_integrity_core().validate_run(Path(item["path"]).parents[1])
        if report.get("output_parent_ledger", {}).get("sha256") != context["ledger_header"].get("parent_ledger_file_sha256"):
            raise ValueError("visual-region corrected ledger is not the media stage parent")
        return {"required": True, "scope_mode": report["scope_mode"], "reviewed_pages": live["reviewed_pages"],
            "composite_regions": live["composite_regions"], "open_reviews": 0}

    def producer_execution_capability() -> dict[str, Any]:
        if context.get("promotion_class") != "formal_native":
            context["producer_execution_provenance"] = "historical_unbound"
            return {"required": False, "status": "historical_unbound", "promotion_class": context.get("promotion_class")}
        item = artifacts.get("producer_execution_capability")
        if not item:
            raise ValueError("formal-native stage lacks execution_capability_E")
        path = Path(item["path"])
        live = execution_core.validate_manifest(path)
        stored = read_json(path)
        if stage["execution_capability_E"].get("payload_hash") != stored.get("payload_hash"):
            raise ValueError("stage manifest execution capability payload hash mismatch")
        committed = [entry for entry in context["decision_index"].get("evidence_committed_before_index", []) if entry.get("role") == "execution_capability"]
        if len(committed) != 1 or committed[0].get("sha256") != item["sha256"]:
            raise ValueError("decision index D does not bind the exact execution capability evidence E")
        expected_order = ["precommit_evidence_and_execution_capability_E", "decision_index_D", "canonical_ledger_and_media_views_L", "stage_manifest_M"]
        if stage.get("commit_order") != expected_order:
            raise ValueError("stage execution capability does not follow E-to-D-to-L-to-M commit order")
        context["producer_execution_provenance"] = "live_verified"
        return {"required": True, "status": "live_verified", **live}

    def evaluator_execution_capability() -> dict[str, Any]:
        live = execution_core.validate_manifest(evaluator_capability_path)
        artifacts["evaluator_execution_capability"] = {"path": str(evaluator_capability_path), "sha256": sha256_file(evaluator_capability_path)}
        return live

    gate.check("PG-H01-stage-shape", stage_shape)
    gate.check("PG-H02-stage-artifact-hashes", artifact_hashes)
    gate.check("PG-H03-decision-closure", decision_closure)
    gate.check("PG-H04-ledger-identity-and-binding", ledger_identity)
    gate.check("PG-H05-D-to-L-acyclicity", acyclic_commit)
    gate.check("PG-H06-live-media-contract-validation", media_contract_live)
    gate.check("PG-H07-native-mode-integrity", native_mode_integrity)
    gate.check("PG-H08-live-source-lineage-integrity", source_integrity_report)
    gate.check("PG-H09-cumulative-child-decision-inheritance", cumulative_child_decisions)
    gate.check("PG-H10-child-media-fragment-partition", child_fragment_partition)
    gate.check("PG-H11-standard-ledger-identity-and-review-binding", standard_identity_and_review_binding)
    gate.check("PG-H12-live-producer-execution-capability", producer_execution_capability)
    gate.check("PG-H13-live-evaluator-execution-capability", evaluator_execution_capability)
    gate.check("PG-H14-page-visual-and-composite-integrity", visual_region_integrity)
    disposition = "promoted" if gate.passed else "rejected"
    manifest = {
        "schema_version": "stage-promotion-manifest/1.1", "promotion_id": args.promotion_id,
        "lineage_key": args.lineage_key, "evaluated_at": now(), "evaluator": VERSION,
        "stage_kind": "spec03_media_contract", "run_dir": str(run),
        "stage_manifest": {"path": str(stage_path), "sha256": sha256_file(stage_path)},
        "disposition": disposition, "promotion_class": context.get("promotion_class", "undetermined"),
        "producer_execution_provenance": context.get("producer_execution_provenance", "unverified"),
        "evaluator_capability": {"path": str(evaluator_capability_path), "sha256": sha256_file(evaluator_capability_path), "payload_hash": evaluator_capability["payload_hash"]},
        "checks": gate.checks,
        "summary": {"checks": len(gate.checks), "passed": sum(item["status"] == "passed" for item in gate.checks), "failed": sum(item["status"] == "failed" for item in gate.checks)},
        "promoted_artifacts": artifacts if disposition == "promoted" else {},
        "consumer_rule": "Downstream formal consumers must verify this manifest byte hash, disposition=promoted, lineage_key, stage kind, and exact promoted artifact hashes.",
    }
    write_json(output, manifest)
    return manifest, 0 if disposition == "promoted" else 4


def evaluate_spec04a_structure(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    run = args.run_dir.resolve()
    output = args.output.resolve()
    execution_core = load_execution_core()
    structure_core = load_spec04a_structure_core()
    skill_root = Path(__file__).parents[1].resolve()
    capability_output_arg = getattr(args, "evaluator_capability_output", None)
    evaluator_capability_path = capability_output_arg.resolve() if capability_output_arg else output.with_suffix(".evaluator-capability.json")
    evaluator_capability = execution_core.build_manifest(
        manifest_id=f"{args.promotion_id}-evaluator-capability",
        skill_root=skill_root,
        entrypoints=[
            ("promotion_evaluator", Path(__file__).resolve()),
            ("execution_capability_core", Path(__file__).with_name("execution_capability.py").resolve()),
            ("structure_contract_core", Path(__file__).with_name("spec04a_structure_contract.py").resolve()),
        ],
        resources=spec04a_evaluator_resources(skill_root),
        invocation=spec04a_evaluator_invocation(args, evaluator_capability_path),
        producer=VERSION,
    )
    write_json(evaluator_capability_path, evaluator_capability)
    stage_path = run / "manifests/spec04a_structure_stage_manifest.json"
    if not stage_path.is_file():
        raise FileNotFoundError(f"Spec 04-A stage manifest missing: {stage_path}")
    stage = read_json(stage_path)
    gate = Gate()
    artifacts: dict[str, dict[str, Any]] = {}
    context: dict[str, Any] = {}

    def stage_shape() -> dict[str, Any]:
        if stage.get("schema_version") != "spec04a-structure-stage-manifest/1.0" or stage.get("stage_kind") != "spec04a_structure_contract":
            raise ValueError("unsupported Spec 04-A stage manifest")
        if stage.get("status") != "passed" or stage.get("slice_status") != "passed" or stage.get("full_spec04_status") != "not_evaluated":
            raise ValueError("stage does not accurately report a passed 04-A slice and unevaluated full Spec 04")
        required = {
            "execution_capability_E", "decision_index_D", "ledger_L", "source_outline_ledger",
            "final_toc_plan", "review_queue", "validation", "parent_promotion",
        }
        missing = sorted(required - stage.keys())
        if missing:
            raise ValueError(f"Spec 04-A stage manifest lacks required artifacts: {missing}")
        if stage.get("producer_mode") not in {"formal_native", "migration_compatibility"}:
            raise ValueError("Spec 04-A stage producer mode is invalid")
        context["promotion_class"] = stage["producer_mode"]
        return {"schema_version": stage["schema_version"], "producer_mode": stage["producer_mode"], "full_spec04_status": stage["full_spec04_status"]}

    def artifact_hashes() -> dict[str, Any]:
        names = ("execution_capability_E", "decision_index_D", "ledger_L", "source_outline_ledger", "final_toc_plan", "review_queue", "validation")
        for name in names:
            path = resolve_artifact(run, stage[name])
            role = "producer_execution_capability" if name == "execution_capability_E" else name
            artifacts[role] = {"path": str(path), "sha256": sha256_file(path)}
        run_manifest = run / "manifests/run_manifest.json"
        if not run_manifest.is_file():
            raise ValueError("Spec 04-A run manifest is missing")
        artifacts["run_manifest"] = {"path": str(run_manifest), "sha256": sha256_file(run_manifest)}
        return {"artifacts": len(artifacts)}

    def decision_closure_and_inheritance() -> dict[str, Any]:
        child_path = Path(artifacts["decision_index_D"]["path"])
        child = read_json(child_path)
        context["decision_index"] = child
        if child.get("spec_status") != "passed":
            raise ValueError("Spec 04-A decision index is not passed")
        unresolved = [item.get("decision_id") for item in child.get("decisions", []) if item.get("status") in {"open", "stale", "invalidated"}]
        if unresolved:
            raise ValueError(f"Spec 04-A decision index contains unresolved decisions: {unresolved[:8]}")
        parent_path = (run / child.get("parent_index_ref", "")).resolve()
        if not parent_path.is_file() or sha256_file(parent_path) != child.get("parent_index_hash"):
            raise ValueError("Spec 04-A parent decision index is missing or drifted")
        parent = read_json(parent_path)
        parent_by_id = {item["decision_id"]: item for item in parent.get("decisions", [])}
        child_by_id = {item["decision_id"]: item for item in child.get("decisions", [])}
        missing = sorted(set(parent_by_id) - set(child_by_id))
        changed = sorted(item for item in parent_by_id if child_by_id.get(item) != parent_by_id[item])
        added = sorted(set(child_by_id) - set(parent_by_id))
        if missing or changed or len(added) != 1 or int(child.get("version", 0)) != int(parent.get("version", 0)) + 1:
            raise ValueError(f"Spec 04-A decision inheritance failed: missing={missing[:8]} changed={changed[:8]} added={added[:8]}")
        return {"parent_decisions": len(parent_by_id), "inherited_unchanged": len(parent_by_id), "stage_decisions_added": added}

    def ledger_identity_and_binding() -> dict[str, Any]:
        path = Path(artifacts["ledger_L"]["path"])
        with path.open(encoding="utf-8") as stream:
            header = json.loads(next(stream))
            records = [json.loads(line) for line in stream if line.strip()]
        context["ledger_header"] = header
        if header.get("current_ledger_hash") != canonical_hash(records):
            raise ValueError("Spec 04-A ledger payload hash mismatch")
        if header.get("canonical_decision_index_hash") != artifacts["decision_index_D"]["sha256"]:
            raise ValueError("Spec 04-A ledger is not bound to decision index D")
        if stage["ledger_L"].get("payload_hash") != header.get("current_ledger_hash"):
            raise ValueError("Spec 04-A stage manifest ledger payload hash mismatch")
        if header.get("spec04a_structure", {}).get("status") != "passed" or header.get("spec04a_structure", {}).get("full_spec04_status") != "not_evaluated":
            raise ValueError("Spec 04-A ledger overlay status is inaccurate")
        return {"snapshot_id": header.get("ledger_snapshot_id"), "records": len(records), "payload_hash": header["current_ledger_hash"]}

    def acyclic_commit() -> dict[str, Any]:
        index_values = scalar_strings(context["decision_index"])
        forbidden = [context["ledger_header"].get("ledger_snapshot_id"), context["ledger_header"].get("current_ledger_hash"), artifacts["ledger_L"]["sha256"]]
        found = [item for item in forbidden if item and item in index_values]
        if found:
            raise ValueError(f"Spec 04-A decision index references child ledger identities: {found}")
        expected = ["precommit_evidence_and_execution_capability_E", "decision_index_D", "structure_contract_and_ledger_L", "stage_manifest_M"]
        if stage.get("commit_order") != expected:
            raise ValueError("Spec 04-A stage does not follow E-to-D-to-L-to-M commit order")
        return {"forbidden_child_references": 0, "commit_order": expected}

    def live_structure_validation() -> dict[str, Any]:
        result = structure_core.validate_run(run)
        context["live_structure"] = result
        return result

    def active_parent_promotion() -> dict[str, Any]:
        parent = stage["parent_promotion"]
        selection = verify_registry_selection(
            Path(parent["registry_path"]), parent["lineage_key"], Path(parent["manifest_path"]),
            "spec03_media_contract", capability_verification="frozen"
        )
        if parent.get("capability_verification") != "frozen_ancestor_snapshot":
            raise ValueError("Spec 04-A parent promotion lacks an explicit frozen-ancestor capability policy")
        if parent.get("promotion_id") != selection["promotion"].get("promotion_id") or parent.get("manifest_sha256") != sha256_file(Path(parent["manifest_path"])):
            raise ValueError("Spec 04-A parent promotion binding differs from the active registry selection")
        if stage["producer_mode"] != selection["promotion"].get("promotion_class"):
            raise ValueError("Spec 04-A promotion class does not inherit the parent promotion class")
        artifacts["parent_promotion"] = {"path": parent["manifest_path"], "sha256": parent["manifest_sha256"]}
        artifacts["parent_promotion_registry"] = {"path": parent["registry_path"], "sha256": parent["registry_sha256"]}
        return {"promotion_id": parent["promotion_id"], "promotion_class": stage["producer_mode"], "lineage_key": parent["lineage_key"]}

    def exact_structure_coverage() -> dict[str, Any]:
        outline = read_json(Path(artifacts["source_outline_ledger"]["path"]))
        final_toc = read_json(Path(artifacts["final_toc_plan"]["path"]))
        if outline.get("summary", {}).get("open_reviews") != 0 or outline.get("title_candidate_disposition", {}).get("unresolved") != 0:
            raise ValueError("Spec 04-A outline has unresolved structure or title candidates")
        final_nodes = {item["node_id"] for item in final_toc.get("entries", [])}
        expected = {node["node_id"] for node in outline.get("body_hierarchy", []) if node.get("final_toc", {}).get("include")}
        if final_nodes != expected:
            raise ValueError("final TOC projection is not an exact view of the reviewed hierarchy")
        local_blocks = set(outline["title_candidate_disposition"]["local_heading_blocks"])
        structural_blocks = set(outline["title_candidate_disposition"]["structural_title_blocks"])
        if local_blocks & structural_blocks or len(local_blocks | structural_blocks) != outline["title_candidate_disposition"]["candidate_count"]:
            raise ValueError("title candidate partition is overlapping or incomplete")
        return {"structure_nodes": outline["summary"]["structure_nodes"], "final_toc_entries": len(final_nodes), "title_candidates": outline["title_candidate_disposition"]["candidate_count"], "local_headings": len(local_blocks), "unresolved": 0}

    def producer_execution_capability() -> dict[str, Any]:
        path = Path(artifacts["producer_execution_capability"]["path"])
        live = execution_core.validate_manifest(path)
        stored = read_json(path)
        if stage["execution_capability_E"].get("payload_hash") != stored.get("payload_hash"):
            raise ValueError("Spec 04-A producer capability payload hash mismatch")
        committed = [item for item in context["decision_index"].get("evidence_committed_before_index", []) if item.get("role") == "execution_capability"]
        if len(committed) != 1 or committed[0].get("sha256") != artifacts["producer_execution_capability"]["sha256"]:
            raise ValueError("Spec 04-A decision index D does not bind execution capability E")
        context["producer_execution_provenance"] = "live_verified"
        return {"status": "live_verified", **live}

    def evaluator_execution_capability() -> dict[str, Any]:
        live = execution_core.validate_manifest(evaluator_capability_path)
        artifacts["evaluator_execution_capability"] = {"path": str(evaluator_capability_path), "sha256": sha256_file(evaluator_capability_path)}
        return live

    gate.check("S4A-PG-H01-stage-shape", stage_shape)
    gate.check("S4A-PG-H02-stage-artifact-hashes", artifact_hashes)
    gate.check("S4A-PG-H03-decision-closure-and-inheritance", decision_closure_and_inheritance)
    gate.check("S4A-PG-H04-ledger-identity-and-binding", ledger_identity_and_binding)
    gate.check("S4A-PG-H05-E-to-D-to-L-to-M-acyclicity", acyclic_commit)
    gate.check("S4A-PG-H06-live-structure-validation", live_structure_validation)
    gate.check("S4A-PG-H07-active-parent-promotion", active_parent_promotion)
    gate.check("S4A-PG-H08-exact-structure-and-title-partition", exact_structure_coverage)
    gate.check("S4A-PG-H09-live-producer-execution-capability", producer_execution_capability)
    gate.check("S4A-PG-H10-live-evaluator-execution-capability", evaluator_execution_capability)
    disposition = "promoted" if gate.passed else "rejected"
    manifest = {
        "schema_version": "stage-promotion-manifest/1.1", "promotion_id": args.promotion_id,
        "lineage_key": args.lineage_key, "evaluated_at": now(), "evaluator": VERSION,
        "stage_kind": "spec04a_structure_contract", "run_dir": str(run),
        "stage_manifest": {"path": str(stage_path), "sha256": sha256_file(stage_path)},
        "disposition": disposition, "promotion_class": context.get("promotion_class", "undetermined"),
        "producer_execution_provenance": context.get("producer_execution_provenance", "unverified"),
        "evaluator_capability": {"path": str(evaluator_capability_path), "sha256": sha256_file(evaluator_capability_path), "payload_hash": evaluator_capability["payload_hash"]},
        "checks": gate.checks,
        "summary": {"checks": len(gate.checks), "passed": sum(item["status"] == "passed" for item in gate.checks), "failed": sum(item["status"] == "failed" for item in gate.checks)},
        "promoted_artifacts": artifacts if disposition == "promoted" else {},
        "consumer_rule": "Full Spec 04 must verify this active promotion and consume the exact hierarchy and abstract final TOC without reclassifying title candidates.",
        "scope_limit": "Spec 04-A only; teaching roles, ElegantBook constructs, render_plan, LaTeX, compile, and final page review are not evaluated.",
    }
    write_json(output, manifest)
    return manifest, 0 if disposition == "promoted" else 4


def evaluate_spec04b_semantic_spans(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    run = args.run_dir.resolve()
    output = args.output.resolve()
    execution_core = load_execution_core()
    semantic_core = load_spec04b_semantic_core()
    skill_root = Path(__file__).parents[1].resolve()
    capability_arg = getattr(args, "evaluator_capability_output", None)
    evaluator_path = capability_arg.resolve() if capability_arg else output.with_suffix(".evaluator-capability.json")
    evaluator = execution_core.build_manifest(
        manifest_id=f"{args.promotion_id}-evaluator-capability",
        skill_root=skill_root,
        entrypoints=[
            ("promotion_evaluator", Path(__file__).resolve()),
            ("execution_capability_core", Path(__file__).with_name("execution_capability.py").resolve()),
            ("semantic_span_contract_core", Path(__file__).with_name("spec04b_semantic_span_contract.py").resolve()),
        ],
        resources=spec04b_evaluator_resources(skill_root),
        invocation=spec04b_evaluator_invocation(args, evaluator_path),
        producer=VERSION,
    )
    write_json(evaluator_path, evaluator)
    stage_path = run / "manifests/spec04b_semantic_stage_manifest.json"
    if not stage_path.is_file():
        raise FileNotFoundError(f"Spec 04-B stage manifest missing: {stage_path}")
    stage = read_json(stage_path)
    gate = Gate()
    artifacts: dict[str, dict[str, Any]] = {}
    context: dict[str, Any] = {}

    def stage_shape() -> dict[str, Any]:
        if stage.get("schema_version") != "spec04b-semantic-stage-manifest/1.0" or stage.get("stage_kind") != "spec04b_semantic_span_contract":
            raise ValueError("unsupported Spec 04-B stage manifest")
        if stage.get("status") != "passed" or stage.get("slice_status") != "passed" or stage.get("full_spec04_status") != "not_evaluated":
            raise ValueError("stage does not accurately report a passed 04-B slice and unevaluated full Spec 04")
        required = {
            "execution_capability_E", "decision_index_D", "ledger_L", "semantic_span_ledger",
            "teaching_column_group_ledger", "review_queue", "validation", "parent_promotion",
        }
        missing = sorted(required - stage.keys())
        if missing:
            raise ValueError(f"Spec 04-B stage manifest lacks required artifacts: {missing}")
        if stage.get("producer_mode") not in {"formal_native", "migration_compatibility"}:
            raise ValueError("Spec 04-B stage producer mode is invalid")
        context["promotion_class"] = stage["producer_mode"]
        return {"schema_version": stage["schema_version"], "producer_mode": stage["producer_mode"], "full_spec04_status": stage["full_spec04_status"]}

    def artifact_hashes() -> dict[str, Any]:
        names = ("execution_capability_E", "decision_index_D", "ledger_L", "semantic_span_ledger", "teaching_column_group_ledger", "review_queue", "validation")
        for name in names:
            path = resolve_artifact(run, stage[name])
            role = "producer_execution_capability" if name == "execution_capability_E" else name
            artifacts[role] = {"path": str(path), "sha256": sha256_file(path)}
        run_manifest = run / "manifests/run_manifest.json"
        if not run_manifest.is_file():
            raise ValueError("Spec 04-B run manifest is missing")
        artifacts["run_manifest"] = {"path": str(run_manifest), "sha256": sha256_file(run_manifest)}
        return {"artifacts": len(artifacts)}

    def decision_inheritance() -> dict[str, Any]:
        child_path = Path(artifacts["decision_index_D"]["path"])
        child = read_json(child_path)
        context["decision_index"] = child
        if child.get("spec_status") != "passed":
            raise ValueError("Spec 04-B decision index is not passed")
        unresolved = [item.get("decision_id") for item in child.get("decisions", []) if item.get("status") in {"open", "stale", "invalidated"}]
        if unresolved:
            raise ValueError(f"Spec 04-B decision index contains unresolved decisions: {unresolved[:8]}")
        parent_path = (run / child.get("parent_index_ref", "")).resolve()
        if not parent_path.is_file() or sha256_file(parent_path) != child.get("parent_index_hash"):
            raise ValueError("Spec 04-B parent decision index is missing or drifted")
        parent = read_json(parent_path)
        parent_by_id = {item["decision_id"]: item for item in parent.get("decisions", [])}
        child_by_id = {item["decision_id"]: item for item in child.get("decisions", [])}
        missing = sorted(set(parent_by_id) - set(child_by_id))
        changed = sorted(item for item in parent_by_id if child_by_id.get(item) != parent_by_id[item])
        added = sorted(set(child_by_id) - set(parent_by_id))
        if missing or changed or len(added) != 1 or int(child.get("version", 0)) != int(parent.get("version", 0)) + 1:
            raise ValueError(f"Spec 04-B decision inheritance failed: missing={missing[:8]} changed={changed[:8]} added={added[:8]}")
        return {"parent_decisions": len(parent_by_id), "inherited_unchanged": len(parent_by_id), "stage_decisions_added": added}

    def ledger_identity() -> dict[str, Any]:
        path = Path(artifacts["ledger_L"]["path"])
        with path.open(encoding="utf-8") as stream:
            header = json.loads(next(stream))
            records = [json.loads(line) for line in stream if line.strip()]
        context["ledger_header"] = header
        if header.get("current_ledger_hash") != canonical_hash(records):
            raise ValueError("Spec 04-B ledger payload hash mismatch")
        if header.get("canonical_decision_index_hash") != artifacts["decision_index_D"]["sha256"]:
            raise ValueError("Spec 04-B ledger is not bound to decision index D")
        if stage["ledger_L"].get("payload_hash") != header.get("current_ledger_hash"):
            raise ValueError("Spec 04-B stage manifest ledger payload hash mismatch")
        status = header.get("spec04b_semantic_spans", {})
        if status.get("status") != "passed" or status.get("full_spec04_status") != "not_evaluated":
            raise ValueError("Spec 04-B ledger overlay status is inaccurate")
        return {"snapshot_id": header.get("ledger_snapshot_id"), "records": len(records), "payload_hash": header["current_ledger_hash"]}

    def acyclic_commit() -> dict[str, Any]:
        values = scalar_strings(context["decision_index"])
        forbidden = [context["ledger_header"].get("ledger_snapshot_id"), context["ledger_header"].get("current_ledger_hash"), artifacts["ledger_L"]["sha256"]]
        found = [item for item in forbidden if item and item in values]
        if found:
            raise ValueError(f"Spec 04-B decision index references child ledger identities: {found}")
        expected = ["precommit_evidence_and_execution_capability_E", "decision_index_D", "semantic_contract_and_ledger_L", "stage_manifest_M"]
        if stage.get("commit_order") != expected:
            raise ValueError("Spec 04-B stage does not follow E-to-D-to-L-to-M commit order")
        return {"forbidden_child_references": 0, "commit_order": expected}

    def live_validation() -> dict[str, Any]:
        result = semantic_core.validate_run(run)
        context["live_semantic"] = result
        return result

    def active_spec04a_parent() -> dict[str, Any]:
        parent = stage["parent_promotion"]
        selection = verify_registry_selection(
            Path(parent["registry_path"]), parent["lineage_key"], Path(parent["manifest_path"]),
            "spec04a_structure_contract", capability_verification="frozen",
        )
        if parent.get("capability_verification") != "frozen_ancestor_snapshot":
            raise ValueError("Spec 04-B parent lacks an explicit frozen-ancestor capability policy")
        if parent.get("promotion_id") != selection["promotion"].get("promotion_id") or parent.get("manifest_sha256") != sha256_file(Path(parent["manifest_path"])):
            raise ValueError("Spec 04-B parent binding differs from the active Spec 04-A registry selection")
        if stage["producer_mode"] != selection["promotion"].get("promotion_class"):
            raise ValueError("Spec 04-B promotion class does not inherit Spec 04-A class")
        promoted = selection["promotion"].get("promoted_artifacts", {})
        for role in ("decision_index_D", "source_outline_ledger", "final_toc_plan"):
            bound = parent.get(role, {})
            if bound.get("path") != promoted.get(role, {}).get("path") or bound.get("sha256") != promoted.get(role, {}).get("sha256"):
                raise ValueError(f"Spec 04-B does not bind exact promoted Spec 04-A artifact: {role}")
        parent_ledger = (run / context["ledger_header"].get("parent_ledger_ref", "")).resolve()
        promoted_ledger = promoted.get("ledger_L", {})
        if parent_ledger != Path(promoted_ledger.get("path", "")).resolve() or context["ledger_header"].get("parent_ledger_file_sha256") != promoted_ledger.get("sha256"):
            raise ValueError("Spec 04-B child ledger is not descended from the exact promoted Spec 04-A ledger")
        artifacts["parent_promotion"] = {"path": parent["manifest_path"], "sha256": parent["manifest_sha256"]}
        artifacts["parent_promotion_registry"] = {"path": parent["registry_path"], "sha256": parent["registry_sha256"]}
        return {"promotion_id": parent["promotion_id"], "promotion_class": stage["producer_mode"], "lineage_key": parent["lineage_key"]}

    def exact_semantic_partition() -> dict[str, Any]:
        contract = read_json(Path(artifacts["semantic_span_ledger"]["path"]))
        groups = read_json(Path(artifacts["teaching_column_group_ledger"]["path"]))
        queue = read_json(Path(artifacts["review_queue"]["path"]))
        if contract.get("full_spec04_status") != "not_evaluated" or groups.get("full_spec04_status") != "not_evaluated":
            raise ValueError("Spec 04-B artifacts overclaim full Spec 04")
        if queue.get("open_items") != 0 or groups.get("open_reviews") != 0:
            raise ValueError("Spec 04-B contains unresolved semantic reviews")
        ids = [block_id for span in contract.get("spans", []) for block_id in span.get("source_block_ids", [])]
        if len(ids) != len(set(ids)) or len(ids) != contract.get("summary", {}).get("included_source_atoms"):
            raise ValueError("Spec 04-B semantic span partition overlaps or omits source atoms")
        group_ids = [block_id for group in groups.get("groups", []) for block_id in group.get("source_block_ids", [])]
        if len(group_ids) != len(set(group_ids)) or any(not group.get("body_block_ids") for group in groups.get("groups", [])):
            raise ValueError("Spec 04-B teaching groups overlap or contain empty bodies")
        semantic_core.assert_no_downstream_keys(contract)
        semantic_core.assert_no_downstream_keys(groups)
        return {"included_source_atoms": len(ids), "semantic_spans": len(contract.get("spans", [])), "teaching_groups": len(groups.get("groups", [])), "standalone_labels": len(groups.get("standalone_labels", [])), "open_reviews": 0}

    def producer_capability() -> dict[str, Any]:
        path = Path(artifacts["producer_execution_capability"]["path"])
        live = execution_core.validate_manifest(path)
        stored = read_json(path)
        if stage["execution_capability_E"].get("payload_hash") != stored.get("payload_hash"):
            raise ValueError("Spec 04-B producer capability payload hash mismatch")
        committed = [item for item in context["decision_index"].get("evidence_committed_before_index", []) if item.get("role") == "execution_capability"]
        if len(committed) != 1 or committed[0].get("sha256") != artifacts["producer_execution_capability"]["sha256"]:
            raise ValueError("Spec 04-B decision index D does not bind execution capability E")
        context["producer_execution_provenance"] = "live_verified"
        return {"status": "live_verified", **live}

    def evaluator_capability() -> dict[str, Any]:
        live = execution_core.validate_manifest(evaluator_path)
        artifacts["evaluator_execution_capability"] = {"path": str(evaluator_path), "sha256": sha256_file(evaluator_path)}
        return live

    gate.check("S4B-PG-H01-stage-shape", stage_shape)
    gate.check("S4B-PG-H02-stage-artifact-hashes", artifact_hashes)
    gate.check("S4B-PG-H03-decision-closure-and-inheritance", decision_inheritance)
    gate.check("S4B-PG-H04-ledger-identity-and-binding", ledger_identity)
    gate.check("S4B-PG-H05-E-to-D-to-L-to-M-acyclicity", acyclic_commit)
    gate.check("S4B-PG-H06-live-semantic-validation", live_validation)
    gate.check("S4B-PG-H07-active-spec04a-parent-and-exact-artifacts", active_spec04a_parent)
    gate.check("S4B-PG-H08-exact-span-partition-and-safe-groups", exact_semantic_partition)
    gate.check("S4B-PG-H09-live-producer-execution-capability", producer_capability)
    gate.check("S4B-PG-H10-live-evaluator-execution-capability", evaluator_capability)
    disposition = "promoted" if gate.passed else "rejected"
    manifest = {
        "schema_version": "stage-promotion-manifest/1.1", "promotion_id": args.promotion_id,
        "lineage_key": args.lineage_key, "evaluated_at": now(), "evaluator": VERSION,
        "stage_kind": "spec04b_semantic_span_contract", "run_dir": str(run),
        "stage_manifest": {"path": str(stage_path), "sha256": sha256_file(stage_path)},
        "disposition": disposition, "promotion_class": context.get("promotion_class", "undetermined"),
        "producer_execution_provenance": context.get("producer_execution_provenance", "unverified"),
        "evaluator_capability": {"path": str(evaluator_path), "sha256": sha256_file(evaluator_path), "payload_hash": evaluator["payload_hash"]},
        "checks": gate.checks,
        "summary": {"checks": len(gate.checks), "passed": sum(item["status"] == "passed" for item in gate.checks), "failed": sum(item["status"] == "failed" for item in gate.checks)},
        "promoted_artifacts": artifacts if disposition == "promoted" else {},
        "consumer_rule": "The next Spec 04 slice must verify this active promotion and consume the exact semantic spans and teaching-column membership without choosing them again.",
        "scope_limit": "Spec 04-B only; ElegantBook boxes/constructs, render_plan, formula/table reconstruction, LaTeX, compile, and final page review are not evaluated.",
    }
    write_json(output, manifest)
    return manifest, 0 if disposition == "promoted" else 4


def evaluate_spec04c_construct_bindings(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    run = args.run_dir.resolve()
    output = args.output.resolve()
    execution_core = load_execution_core()
    construct_core = load_spec04c_construct_core()
    skill_root = Path(__file__).parents[1].resolve()
    capability_arg = getattr(args, "evaluator_capability_output", None)
    evaluator_path = capability_arg.resolve() if capability_arg else output.with_suffix(".evaluator-capability.json")
    evaluator = execution_core.build_manifest(
        manifest_id=f"{args.promotion_id}-evaluator-capability", skill_root=skill_root,
        entrypoints=[
            ("promotion_evaluator", Path(__file__).resolve()),
            ("execution_capability_core", Path(__file__).with_name("execution_capability.py").resolve()),
            ("construct_binding_contract_core", Path(__file__).with_name("spec04c_construct_binding_contract.py").resolve()),
        ],
        resources=spec04c_evaluator_resources(skill_root),
        invocation=spec04c_evaluator_invocation(args, evaluator_path), producer=VERSION,
    )
    write_json(evaluator_path, evaluator)
    stage_path = run / "manifests/spec04c_construct_stage_manifest.json"
    if not stage_path.is_file():
        raise FileNotFoundError(f"Spec 04-C stage manifest missing: {stage_path}")
    stage = read_json(stage_path)
    gate = Gate()
    artifacts: dict[str, dict[str, Any]] = {}
    context: dict[str, Any] = {}

    def stage_shape() -> dict[str, Any]:
        if stage.get("schema_version") != "spec04c-construct-stage-manifest/1.0" or stage.get("stage_kind") != "spec04c_construct_binding_contract":
            raise ValueError("unsupported Spec 04-C stage manifest")
        if stage.get("status") != "passed" or stage.get("slice_status") != "passed" or stage.get("full_spec04_status") != "not_evaluated":
            raise ValueError("stage does not accurately report a passed 04-C slice and unevaluated full Spec 04")
        required = {
            "execution_capability_E", "decision_index_D", "ledger_L", "template_capability_manifest",
            "construct_binding_ledger", "review_queue", "validation", "parent_promotion",
        }
        missing = sorted(required - stage.keys())
        if missing:
            raise ValueError(f"Spec 04-C stage manifest lacks required artifacts: {missing}")
        if stage.get("producer_mode") not in {"formal_native", "migration_compatibility"}:
            raise ValueError("Spec 04-C stage producer mode is invalid")
        context["promotion_class"] = stage["producer_mode"]
        return {"schema_version": stage["schema_version"], "producer_mode": stage["producer_mode"], "full_spec04_status": stage["full_spec04_status"]}

    def artifact_hashes() -> dict[str, Any]:
        names = ("execution_capability_E", "decision_index_D", "ledger_L", "template_capability_manifest", "construct_binding_ledger", "review_queue", "validation")
        for name in names:
            path = resolve_artifact(run, stage[name])
            role = "producer_execution_capability" if name == "execution_capability_E" else name
            artifacts[role] = {"path": str(path), "sha256": sha256_file(path)}
        run_manifest = run / "manifests/run_manifest.json"
        if not run_manifest.is_file():
            raise ValueError("Spec 04-C run manifest is missing")
        artifacts["run_manifest"] = {"path": str(run_manifest), "sha256": sha256_file(run_manifest)}
        return {"artifacts": len(artifacts)}

    def decision_inheritance() -> dict[str, Any]:
        child_path = Path(artifacts["decision_index_D"]["path"])
        child = read_json(child_path)
        context["decision_index"] = child
        if child.get("spec_status") != "passed":
            raise ValueError("Spec 04-C decision index is not passed")
        unresolved = [item.get("decision_id") for item in child.get("decisions", []) if item.get("status") in {"open", "stale", "invalidated"}]
        if unresolved:
            raise ValueError(f"Spec 04-C decision index contains unresolved decisions: {unresolved[:8]}")
        parent_path = (run / child.get("parent_index_ref", "")).resolve()
        if not parent_path.is_file() or sha256_file(parent_path) != child.get("parent_index_hash"):
            raise ValueError("Spec 04-C parent decision index is missing or drifted")
        parent = read_json(parent_path)
        parent_by_id = {item["decision_id"]: item for item in parent.get("decisions", [])}
        child_by_id = {item["decision_id"]: item for item in child.get("decisions", [])}
        missing = sorted(set(parent_by_id) - set(child_by_id))
        changed = sorted(item for item in parent_by_id if child_by_id.get(item) != parent_by_id[item])
        added = sorted(set(child_by_id) - set(parent_by_id))
        if missing or changed or len(added) != 1 or int(child.get("version", 0)) != int(parent.get("version", 0)) + 1:
            raise ValueError(f"Spec 04-C decision inheritance failed: missing={missing[:8]} changed={changed[:8]} added={added[:8]}")
        return {"parent_decisions": len(parent_by_id), "inherited_unchanged": len(parent_by_id), "stage_decisions_added": added}

    def ledger_identity() -> dict[str, Any]:
        path = Path(artifacts["ledger_L"]["path"])
        with path.open(encoding="utf-8") as stream:
            header = json.loads(next(stream))
            records = [json.loads(line) for line in stream if line.strip()]
        context["ledger_header"] = header
        if header.get("current_ledger_hash") != canonical_hash(records):
            raise ValueError("Spec 04-C ledger payload hash mismatch")
        if header.get("canonical_decision_index_hash") != artifacts["decision_index_D"]["sha256"] or stage["ledger_L"].get("payload_hash") != header.get("current_ledger_hash"):
            raise ValueError("Spec 04-C ledger is not bound to decision index D")
        status = header.get("spec04c_construct_bindings", {})
        if status.get("status") != "passed" or status.get("full_spec04_status") != "not_evaluated":
            raise ValueError("Spec 04-C ledger overlay status is inaccurate")
        return {"snapshot_id": header.get("ledger_snapshot_id"), "records": len(records), "payload_hash": header["current_ledger_hash"]}

    def acyclic_commit() -> dict[str, Any]:
        values = scalar_strings(context["decision_index"])
        forbidden = [context["ledger_header"].get("ledger_snapshot_id"), context["ledger_header"].get("current_ledger_hash"), artifacts["ledger_L"]["sha256"]]
        found = [item for item in forbidden if item and item in values]
        if found:
            raise ValueError(f"Spec 04-C decision index references child ledger identities: {found}")
        expected = ["precommit_evidence_template_and_execution_capability_E", "decision_index_D", "construct_contract_and_ledger_L", "stage_manifest_M"]
        if stage.get("commit_order") != expected:
            raise ValueError("Spec 04-C stage does not follow E-to-D-to-L-to-M commit order")
        return {"forbidden_child_references": 0, "commit_order": expected}

    def live_validation() -> dict[str, Any]:
        result = construct_core.validate_run(run)
        context["live_construct"] = result
        return result

    def active_spec04b_parent() -> dict[str, Any]:
        parent = stage["parent_promotion"]
        selection = verify_registry_selection(
            Path(parent["registry_path"]), parent["lineage_key"], Path(parent["manifest_path"]),
            "spec04b_semantic_span_contract", capability_verification="frozen",
        )
        if parent.get("capability_verification") != "frozen_ancestor_snapshot":
            raise ValueError("Spec 04-C parent lacks frozen-ancestor capability policy")
        if parent.get("promotion_id") != selection["promotion"].get("promotion_id") or parent.get("manifest_sha256") != sha256_file(Path(parent["manifest_path"])):
            raise ValueError("Spec 04-C parent differs from active Spec 04-B selection")
        if stage["producer_mode"] != selection["promotion"].get("promotion_class"):
            raise ValueError("Spec 04-C promotion class does not inherit Spec 04-B class")
        promoted = selection["promotion"].get("promoted_artifacts", {})
        for role in ("decision_index_D", "semantic_span_ledger", "teaching_column_group_ledger"):
            bound = parent.get(role, {})
            if bound.get("path") != promoted.get(role, {}).get("path") or bound.get("sha256") != promoted.get(role, {}).get("sha256"):
                raise ValueError(f"Spec 04-C does not bind exact Spec 04-B artifact: {role}")
        parent_ledger = (run / context["ledger_header"].get("parent_ledger_ref", "")).resolve()
        promoted_ledger = promoted.get("ledger_L", {})
        if parent_ledger != Path(promoted_ledger.get("path", "")).resolve() or context["ledger_header"].get("parent_ledger_file_sha256") != promoted_ledger.get("sha256"):
            raise ValueError("Spec 04-C child ledger is not descended from exact promoted Spec 04-B ledger")
        artifacts["parent_promotion"] = {"path": parent["manifest_path"], "sha256": parent["manifest_sha256"]}
        artifacts["parent_promotion_registry"] = {"path": parent["registry_path"], "sha256": parent["registry_sha256"]}
        return {"promotion_id": parent["promotion_id"], "promotion_class": stage["producer_mode"], "lineage_key": parent["lineage_key"]}

    def exact_construct_bindings() -> dict[str, Any]:
        template = read_json(Path(artifacts["template_capability_manifest"]["path"]))
        contract = read_json(Path(artifacts["construct_binding_ledger"]["path"]))
        queue = read_json(Path(artifacts["review_queue"]["path"]))
        if template.get("schema_version") != "template-capability-manifest/2.0" or contract.get("full_spec04_status") != "not_evaluated":
            raise ValueError("Spec 04-C template/contract schema or scope is invalid")
        if contract.get("template_capability_manifest_sha256") != artifacts["template_capability_manifest"]["sha256"]:
            raise ValueError("construct contract is not bound to exact template manifest")
        if queue.get("open_items") != 0 or contract.get("summary", {}).get("open_reviews") != 0:
            raise ValueError("Spec 04-C contains unresolved reviews")
        bindings = contract.get("bindings", [])
        keys = [(item.get("object_kind"), item.get("object_id")) for item in bindings]
        if len(keys) != len(set(keys)) or len(keys) != contract.get("summary", {}).get("semantic_objects"):
            raise ValueError("Spec 04-C semantic object binding is incomplete or duplicated")
        styles = template.get("constructs", {}).get("tcolorbox_styles", {})
        if any(item.get("construct_parameters", {}).get("style") not in styles for item in bindings if item.get("target_construct") == "tcolorbox"):
            raise ValueError("Spec 04-C requests a nonexistent template style")
        if any(item.get("object_kind") != "teaching_group" for item in bindings if item.get("target_construct") == "tcolorbox"):
            raise ValueError("Spec 04-C creates an empty standalone box")
        construct_core.assert_no_forbidden_keys(contract)
        return {"semantic_objects": len(bindings), "boxed_bindings": sum(item.get("target_construct") == "tcolorbox" for item in bindings), "open_reviews": 0}

    def toc_capability_contract() -> dict[str, Any]:
        template = read_json(Path(artifacts["template_capability_manifest"]["path"]))
        toc = template.get("toc_capability", {})
        strategies = toc.get("serialization_strategies", {})
        localized = strategies.get("localized_depth_override", {})
        if toc.get("effective_tocdepth_status") not in {"explicitly_declared", "unknown_fail_closed"}:
            raise ValueError("Spec 04-C TOC depth evidence is missing or permissive")
        if set(toc.get("entry_type_depths", {})) != {"chapter", "section", "subsection", "subsubsection"}:
            raise ValueError("Spec 04-C TOC entry-depth mapping is incomplete")
        if (
            localized.get("supported") is not True
            or localized.get("preserves_entry_type") is not True
            or localized.get("preserves_pdf_outline_level") is not True
            or localized.get("adds_template_api") is not False
            or localized.get("modifies_template_preamble_or_class") is not False
        ):
            raise ValueError("Spec 04-C lacks a template-legal hierarchy-preserving TOC overflow strategy")
        return {
            "effective_tocdepth": toc.get("effective_tocdepth"),
            "native_visible_entry_types": toc.get("native_visible_entry_types", []),
            "overflow_strategy": "localized_depth_override",
        }

    def producer_capability() -> dict[str, Any]:
        path = Path(artifacts["producer_execution_capability"]["path"])
        live = execution_core.validate_manifest(path)
        stored = read_json(path)
        if stage["execution_capability_E"].get("payload_hash") != stored.get("payload_hash"):
            raise ValueError("Spec 04-C producer capability payload hash mismatch")
        committed = [item for item in context["decision_index"].get("evidence_committed_before_index", []) if item.get("role") == "execution_capability"]
        if len(committed) != 1 or committed[0].get("sha256") != artifacts["producer_execution_capability"]["sha256"]:
            raise ValueError("Spec 04-C decision index D does not bind execution capability E")
        context["producer_execution_provenance"] = "live_verified"
        return {"status": "live_verified", **live}

    def evaluator_capability() -> dict[str, Any]:
        live = execution_core.validate_manifest(evaluator_path)
        artifacts["evaluator_execution_capability"] = {"path": str(evaluator_path), "sha256": sha256_file(evaluator_path)}
        return live

    gate.check("S4C-PG-H01-stage-shape", stage_shape)
    gate.check("S4C-PG-H02-stage-artifact-hashes", artifact_hashes)
    gate.check("S4C-PG-H03-decision-closure-and-inheritance", decision_inheritance)
    gate.check("S4C-PG-H04-ledger-identity-and-binding", ledger_identity)
    gate.check("S4C-PG-H05-E-to-D-to-L-to-M-acyclicity", acyclic_commit)
    gate.check("S4C-PG-H06-live-construct-validation", live_validation)
    gate.check("S4C-PG-H07-active-spec04b-parent-and-exact-artifacts", active_spec04b_parent)
    gate.check("S4C-PG-H08-exact-construct-bindings-and-template-existence", exact_construct_bindings)
    gate.check("S4C-PG-H09-live-producer-execution-capability", producer_capability)
    gate.check("S4C-PG-H10-live-evaluator-execution-capability", evaluator_capability)
    gate.check("S4C-PG-H11-explicit-toc-capability-contract", toc_capability_contract)
    disposition = "promoted" if gate.passed else "rejected"
    manifest = {
        "schema_version": "stage-promotion-manifest/1.1", "promotion_id": args.promotion_id,
        "lineage_key": args.lineage_key, "evaluated_at": now(), "evaluator": VERSION,
        "stage_kind": "spec04c_construct_binding_contract", "run_dir": str(run),
        "stage_manifest": {"path": str(stage_path), "sha256": sha256_file(stage_path)},
        "disposition": disposition, "promotion_class": context.get("promotion_class", "undetermined"),
        "producer_execution_provenance": context.get("producer_execution_provenance", "unverified"),
        "evaluator_capability": {"path": str(evaluator_path), "sha256": sha256_file(evaluator_path), "payload_hash": evaluator["payload_hash"]},
        "checks": gate.checks,
        "summary": {"checks": len(gate.checks), "passed": sum(item["status"] == "passed" for item in gate.checks), "failed": sum(item["status"] == "failed" for item in gate.checks)},
        "promoted_artifacts": artifacts if disposition == "promoted" else {},
        "consumer_rule": "The next Spec 04 slice must consume exact construct bindings and template capability bytes without reselecting constructs.",
        "scope_limit": "Spec 04-C only; render_plan/payload, formula/table reconstruction, LaTeX, compile, and final page review are not evaluated.",
    }
    write_json(output, manifest)
    return manifest, 0 if disposition == "promoted" else 4


def compose_registry(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    output = args.output.resolve()
    parent = args.parent_registry.resolve() if args.parent_registry else None
    entries: list[dict[str, Any]] = []
    version = 1
    parent_ref = None
    parent_hash = None
    if parent:
        parent_doc = read_json(parent)
        if parent_doc.get("schema_version") != "promotion-registry/1.0":
            raise ValueError("unsupported parent promotion registry")
        entries = list(parent_doc.get("entries", []))
        version = int(parent_doc.get("version", 0)) + 1
        parent_ref = str(parent)
        parent_hash = sha256_file(parent)
    known = {entry["promotion_id"] for entry in entries}
    for value in args.promotion_manifest:
        path = Path(value).resolve()
        manifest = read_json(path)
        if manifest.get("schema_version") not in {"stage-promotion-manifest/1.0", "stage-promotion-manifest/1.1"}:
            raise ValueError(f"unsupported promotion manifest: {path}")
        if manifest.get("disposition") == "promoted":
            verify_promotion_manifest(path, manifest.get("stage_kind"))
        elif manifest.get("disposition") != "rejected" or not any(item.get("status") == "failed" for item in manifest.get("checks", [])):
            raise ValueError(f"promotion manifest is neither a verified promotion nor an evidence-backed rejection: {path}")
        if manifest["promotion_id"] in known:
            raise ValueError(f"duplicate promotion id: {manifest['promotion_id']}")
        known.add(manifest["promotion_id"])
        entries.append({
            "promotion_id": manifest["promotion_id"], "lineage_key": manifest["lineage_key"],
            "disposition": manifest["disposition"], "promotion_class": manifest["promotion_class"],
            "manifest_path": str(path), "manifest_sha256": sha256_file(path),
            "run_dir": manifest["run_dir"], "stage_manifest_sha256": manifest["stage_manifest"]["sha256"],
        })
    active: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry["disposition"] == "promoted":
            active[entry["lineage_key"]] = {"promotion_id": entry["promotion_id"], "manifest_path": entry["manifest_path"], "manifest_sha256": entry["manifest_sha256"], "promotion_class": entry["promotion_class"]}
    registry = {
        "schema_version": "promotion-registry/1.0", "registry_id": args.registry_id, "snapshot_id": args.snapshot_id,
        "version": version, "generated_at": now(), "parent_registry_ref": parent_ref, "parent_registry_sha256": parent_hash,
        "entries": entries, "active_promotions": dict(sorted(active.items())),
        "selection_rule": "For each lineage_key, the last appended promoted entry is active; rejected entries never become active.",
        "payload_hash": "",
    }
    registry["payload_hash"] = canonical_hash({key: value for key, value in registry.items() if key not in {"generated_at", "payload_hash"}})
    write_json(output, registry)
    return registry, 0


def verify_promotion_manifest(
    path: Path, expected_stage: str, expected_class: str | None = None,
    capability_verification: str = "live",
) -> dict[str, Any]:
    path = path.resolve()
    manifest = read_json(path)
    if manifest.get("schema_version") not in {"stage-promotion-manifest/1.0", "stage-promotion-manifest/1.1"} or manifest.get("disposition") != "promoted":
        raise ValueError("promotion manifest is not a promoted v1 artifact")
    if manifest.get("stage_kind") != expected_stage:
        raise ValueError("promotion manifest stage kind mismatch")
    if expected_class and manifest.get("promotion_class") != expected_class:
        raise ValueError("promotion manifest class mismatch")
    if capability_verification not in {"live", "frozen"}:
        raise ValueError(f"unsupported capability verification policy: {capability_verification}")
    if manifest.get("schema_version") == "stage-promotion-manifest/1.1":
        core = load_execution_core()
        evaluator = manifest.get("evaluator_capability", {})
        evaluator_path = Path(evaluator.get("path", ""))
        if not evaluator_path.is_file() or sha256_file(evaluator_path) != evaluator.get("sha256"):
            raise ValueError("promotion evaluator capability is missing or drifted")
        if capability_verification == "live":
            core.validate_manifest(evaluator_path)
        if manifest.get("promotion_class") == "formal_native":
            producer = manifest.get("promoted_artifacts", {}).get("producer_execution_capability", {})
            producer_path = Path(producer.get("path", ""))
            if manifest.get("producer_execution_provenance") != "live_verified" or not producer_path.is_file() or sha256_file(producer_path) != producer.get("sha256"):
                raise ValueError("formal-native producer execution capability is missing or drifted")
            if capability_verification == "live":
                core.validate_manifest(producer_path)
    for item in manifest.get("promoted_artifacts", {}).values():
        artifact = Path(item["path"])
        if not artifact.is_file() or sha256_file(artifact) != item["sha256"]:
            raise ValueError(f"promoted artifact drifted: {artifact}")
    stage = Path(manifest["stage_manifest"]["path"])
    if not stage.is_file() or sha256_file(stage) != manifest["stage_manifest"]["sha256"]:
        raise ValueError("promoted stage manifest drifted")
    return manifest


def verify_registry_selection(
    registry_path: Path, lineage_key: str, promotion_manifest_path: Path,
    expected_stage: str, expected_class: str | None = None,
    capability_verification: str = "live",
) -> dict[str, Any]:
    registry_path = registry_path.resolve()
    promotion_manifest_path = promotion_manifest_path.resolve()
    registry = read_json(registry_path)
    if registry.get("schema_version") != "promotion-registry/1.0":
        raise ValueError("unsupported promotion registry")
    computed = canonical_hash({key: value for key, value in registry.items() if key not in {"generated_at", "payload_hash"}})
    if registry.get("payload_hash") != computed:
        raise ValueError("promotion registry payload hash mismatch")
    if registry.get("parent_registry_ref"):
        parent = Path(registry["parent_registry_ref"])
        if not parent.is_file() or sha256_file(parent) != registry.get("parent_registry_sha256"):
            raise ValueError("promotion registry parent drifted")
    active = registry.get("active_promotions", {}).get(lineage_key)
    if not active:
        raise ValueError(f"promotion registry has no active entry for lineage: {lineage_key}")
    if Path(active["manifest_path"]).resolve() != promotion_manifest_path or sha256_file(promotion_manifest_path) != active["manifest_sha256"]:
        raise ValueError("supplied promotion manifest is not the registry's active selection")
    manifest = verify_promotion_manifest(
        promotion_manifest_path, expected_stage, expected_class,
        capability_verification=capability_verification,
    )
    if manifest.get("lineage_key") != lineage_key or manifest.get("promotion_id") != active.get("promotion_id"):
        raise ValueError("active registry entry and promotion manifest identity disagree")
    return {"registry": registry, "active": active, "promotion": manifest}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    evaluate = sub.add_parser("evaluate-spec03-media")
    evaluate.add_argument("--run-dir", type=Path, required=True)
    evaluate.add_argument("--promotion-id", required=True)
    evaluate.add_argument("--lineage-key", required=True)
    evaluate.add_argument("--source-integrity-report", type=Path)
    evaluate.add_argument("--evaluator-capability-output", type=Path)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate_structure = sub.add_parser("evaluate-spec04a-structure")
    evaluate_structure.add_argument("--run-dir", type=Path, required=True)
    evaluate_structure.add_argument("--promotion-id", required=True)
    evaluate_structure.add_argument("--lineage-key", required=True)
    evaluate_structure.add_argument("--evaluator-capability-output", type=Path)
    evaluate_structure.add_argument("--output", type=Path, required=True)
    evaluate_semantic = sub.add_parser("evaluate-spec04b-semantic-spans")
    evaluate_semantic.add_argument("--run-dir", type=Path, required=True)
    evaluate_semantic.add_argument("--promotion-id", required=True)
    evaluate_semantic.add_argument("--lineage-key", required=True)
    evaluate_semantic.add_argument("--evaluator-capability-output", type=Path)
    evaluate_semantic.add_argument("--output", type=Path, required=True)
    evaluate_construct = sub.add_parser("evaluate-spec04c-construct-bindings")
    evaluate_construct.add_argument("--run-dir", type=Path, required=True)
    evaluate_construct.add_argument("--promotion-id", required=True)
    evaluate_construct.add_argument("--lineage-key", required=True)
    evaluate_construct.add_argument("--evaluator-capability-output", type=Path)
    evaluate_construct.add_argument("--output", type=Path, required=True)
    evaluate_render = sub.add_parser("evaluate-spec04d-render-plan")
    evaluate_render.add_argument("--run-dir", type=Path, required=True)
    evaluate_render.add_argument("--promotion-id", required=True)
    evaluate_render.add_argument("--lineage-key", required=True)
    evaluate_render.add_argument("--evaluator-capability-output", type=Path)
    evaluate_render.add_argument("--output", type=Path, required=True)
    evaluate_spec05 = sub.add_parser("evaluate-spec05-build")
    evaluate_spec05.add_argument("--run-dir", type=Path, required=True)
    evaluate_spec05.add_argument("--promotion-id", required=True)
    evaluate_spec05.add_argument("--lineage-key", required=True)
    evaluate_spec05.add_argument("--evaluator-capability-output", type=Path)
    evaluate_spec05.add_argument("--output", type=Path, required=True)
    preflight_spec05 = sub.add_parser("preflight-spec05-parent")
    preflight_spec05.add_argument("--promotion-registry", type=Path, required=True)
    preflight_spec05.add_argument("--parent-promotion", type=Path, required=True)
    preflight_spec05.add_argument("--parent-lineage-key", required=True)
    preflight_spec05.add_argument("--output", type=Path, required=True)
    registry = sub.add_parser("compose-registry")
    registry.add_argument("--promotion-manifest", action="append", required=True)
    registry.add_argument("--parent-registry", type=Path)
    registry.add_argument("--registry-id", required=True)
    registry.add_argument("--snapshot-id", required=True)
    registry.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "evaluate-spec03-media":
            result, code = evaluate_spec03_media(args)
        elif args.command == "evaluate-spec04a-structure":
            result, code = evaluate_spec04a_structure(args)
        elif args.command == "evaluate-spec04b-semantic-spans":
            result, code = evaluate_spec04b_semantic_spans(args)
        elif args.command == "evaluate-spec04c-construct-bindings":
            result, code = evaluate_spec04c_construct_bindings(args)
        elif args.command == "evaluate-spec04d-render-plan":
            result, code = load_spec04d_render_core().evaluate_promotion(args)
        elif args.command == "evaluate-spec05-build":
            result, code = load_spec05_native_core().evaluate_promotion(args)
        elif args.command == "preflight-spec05-parent":
            result, code = load_spec05_native_core().preflight_parent(args)
        else:
            result, code = compose_registry(args)
        print(json.dumps({"status": result.get("disposition", "created"), "summary": result.get("summary"), "output": str(args.output.resolve())}, ensure_ascii=False, indent=2, sort_keys=True))
        return code
    except Exception as exc:
        print(json.dumps({"status": "failed", "gate": VERSION, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
