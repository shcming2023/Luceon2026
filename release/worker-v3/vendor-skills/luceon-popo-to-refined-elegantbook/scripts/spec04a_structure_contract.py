#!/usr/bin/env python3
"""Freeze and validate the Spec 04-A source-outline structure contract.

This slice owns only source TOC reconciliation, body hierarchy, title-candidate
disposition, and the abstract final TOC projection.  It never chooses teaching
boxes, template constructs, formula/table representations, or LaTeX syntax.
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


VERSION = "spec04a-structure-contract/1.0.0"
CONTRACT_SCHEMA = "spec04a-structure-contract/1.0"
STAGE_SCHEMA = "spec04a-structure-stage-manifest/1.0"
FORBIDDEN_KEYS = {
    "target_construct", "construct_parameters", "render_plan", "render_node_id",
    "latex", "tcolorbox", "formula_reconstruction", "table_reconstruction",
}


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
    path.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in values), encoding="utf-8")


def relative(base: Path, target: Path) -> str:
    return os.path.relpath(target, base).replace("\\", "/")


def read_ledger(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with path.open(encoding="utf-8") as stream:
        header = json.loads(next(stream))
        records = [json.loads(line) for line in stream if line.strip()]
    if header.get("record_type") != "ledger_header":
        raise ValueError("canonical ledger lacks ledger_header")
    if header.get("current_ledger_hash") != canonical_hash(records):
        raise ValueError("canonical ledger payload hash is invalid")
    if any(record.get("record_type") != "source_block" for record in records):
        raise ValueError("Spec 04-A requires a source-block-only canonical payload")
    return header, records


def load_execution_core():
    path = Path(__file__).with_name("execution_capability.py")
    spec = importlib.util.spec_from_file_location("execution_capability_spec04a", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load execution capability core: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_promotion_core():
    path = Path(__file__).with_name("stage_promotion_gate.py")
    spec = importlib.util.spec_from_file_location("stage_promotion_gate_spec04a", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load stage promotion gate: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_no_downstream_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_KEYS & set(value)
        if forbidden:
            raise ValueError(f"Spec 04-A review bundle contains downstream keys at {path}: {sorted(forbidden)}")
        for key, item in value.items():
            assert_no_downstream_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_downstream_keys(item, f"{path}[{index}]")


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


def order_key(record: dict[str, Any]) -> tuple[int, int, str]:
    order = record.get("candidate_final_order")
    if not isinstance(order, int):
        raise ValueError(f"included structural anchor lacks candidate_final_order: {record.get('block_id')}")
    return int(record.get("pdf_physical_page", 0)), order, record["block_id"]


def title_candidate_inventory(records: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = []
    for record in records:
        if record.get("scope_status") != "included":
            continue
        if record.get("source_type") != "title" and record.get("source_label") != "title":
            continue
        candidates.append({
            "block_id": record["block_id"],
            "pdf_physical_page": record.get("pdf_physical_page"),
            "candidate_final_order": record.get("candidate_final_order"),
            "raw_content_sha256": record.get("raw_content_sha256") or canonical_hash(record.get("raw_content")),
        })
    candidates.sort(key=lambda item: (item.get("pdf_physical_page") or 0, item.get("candidate_final_order") or 0, item["block_id"]))
    return {
        "schema_version": "structure-title-candidate-inventory/1.0",
        "selection_rule": "all included source blocks labelled or typed as title",
        "candidates": candidates,
        "candidate_count": len(candidates),
        "payload_hash": canonical_hash(candidates),
    }


def verify_evidence_files(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    verified = []
    for item in bundle.get("source_outline_evidence", []):
        evidence_id = item.get("evidence_id")
        path = Path(item.get("path", "")).resolve()
        if not evidence_id or not path.is_file() or sha256_file(path) != item.get("sha256"):
            raise ValueError(f"source outline evidence is missing or drifted: {evidence_id}")
        page = item.get("pdf_physical_page")
        if not isinstance(page, int) or page < 1:
            raise ValueError(f"source outline evidence lacks a physical page: {evidence_id}")
        verified.append({"evidence_id": evidence_id, "path": str(path), "sha256": item["sha256"], "pdf_physical_page": page})
    if not verified:
        raise ValueError("source outline evidence is empty")
    return verified


def validate_bundle(
    *, ledger_header: dict[str, Any], records: list[dict[str, Any]], bundle: dict[str, Any],
    source_pdf: Path, parent_promotion: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    if bundle.get("schema_version") != "spec04a-outline-review-bundle/1.0":
        raise ValueError("unsupported Spec 04-A outline review bundle")
    assert_no_downstream_keys(bundle)
    if bundle.get("review", {}).get("status") != "closed" or bundle.get("review", {}).get("open_items") != 0:
        raise ValueError("outline review is not closed")
    if ledger_header.get("ledger_checkpoint") != "source_reconciled" or ledger_header.get("spec_status") != "passed":
        raise ValueError("Spec 04-A requires a passed source_reconciled parent ledger")
    if ledger_header.get("run_mode") == "regression_fixture" or ledger_header.get("capability_status") == "media_contract_test_only" or ledger_header.get("summary", {}).get("media_only_scope"):
        raise ValueError("STRUCTURE_INPUT_SCOPE_INSUFFICIENT: media-only or bounded regression input cannot establish a book outline")
    if ledger_header.get("material_identity", {}).get("source_pdf_sha256") != sha256_file(source_pdf):
        raise ValueError("source PDF differs from the parent canonical ledger")
    binding = bundle.get("parent_binding", {})
    if binding.get("ledger_snapshot_id") != ledger_header.get("ledger_snapshot_id") or binding.get("ledger_payload_hash") != ledger_header.get("current_ledger_hash"):
        raise ValueError("outline review bundle is not bound to the exact parent ledger")
    if binding.get("source_pdf_sha256") != sha256_file(source_pdf):
        raise ValueError("outline review bundle source PDF hash mismatch")
    if binding.get("promotion_id") != parent_promotion.get("promotion_id") or binding.get("promotion_manifest_sha256") != parent_promotion.get("manifest_sha256"):
        raise ValueError("outline review bundle is not bound to the active parent promotion")
    evidence = verify_evidence_files(bundle)
    evidence_ids = {item["evidence_id"] for item in evidence}
    by_id = {record["block_id"]: record for record in records}
    inventory = title_candidate_inventory(records)
    disposition = bundle.get("title_candidate_disposition", {})
    if disposition.get("candidate_inventory_payload_hash") != inventory["payload_hash"]:
        raise ValueError("title candidate inventory changed after review")
    if disposition.get("all_unassigned") != "local_heading" or disposition.get("review_status") != "closed":
        raise ValueError("unassigned title candidates lack a closed local-heading disposition")

    nodes = copy.deepcopy(bundle.get("nodes", []))
    if not nodes:
        raise ValueError("body hierarchy has no nodes")
    node_ids = [node.get("node_id") for node in nodes]
    if None in node_ids or len(node_ids) != len(set(node_ids)):
        raise ValueError("body hierarchy has missing or duplicate node ids")
    node_by_id = {node["node_id"]: node for node in nodes}
    anchor_owner: dict[str, str] = {}
    selected_title_ids: set[str] = set()
    for node in nodes:
        if node.get("review_status") != "closed" or not node.get("title") or not node.get("role"):
            raise ValueError(f"structure node is incomplete or open: {node.get('node_id')}")
        if not isinstance(node.get("level"), int) or node["level"] < 0:
            raise ValueError(f"structure node level is invalid: {node['node_id']}")
        anchor_id = node.get("anchor_block_id")
        if anchor_id not in by_id or by_id[anchor_id].get("scope_status") != "included":
            raise ValueError(f"structure node anchor is not an included source block: {node['node_id']}")
        if anchor_id in anchor_owner:
            raise ValueError(f"multiple structure nodes share one body anchor: {anchor_id}")
        anchor_owner[anchor_id] = node["node_id"]
        evidence_blocks = node.get("heading_evidence_block_ids", [])
        if not evidence_blocks or any(block_id not in by_id for block_id in evidence_blocks):
            raise ValueError(f"structure node heading evidence is missing: {node['node_id']}")
        if not node.get("source_outline_evidence_ids") or any(item not in evidence_ids for item in node["source_outline_evidence_ids"]):
            raise ValueError(f"structure node lacks exact source-outline evidence: {node['node_id']}")
        for block_id in evidence_blocks:
            record = by_id[block_id]
            if record.get("scope_status") == "included" and (record.get("source_type") == "title" or record.get("source_label") == "title"):
                selected_title_ids.add(block_id)
        node["source_order_start"] = order_key(by_id[anchor_id])[1]
        node["pdf_physical_page_start"] = by_id[anchor_id].get("pdf_physical_page")
        node["title_sha256"] = canonical_hash(node["title"])

    nodes.sort(key=lambda node: (node["pdf_physical_page_start"], node["source_order_start"], node["node_id"]))
    stack: list[dict[str, Any]] = []
    for node in nodes:
        level = node["level"]
        while len(stack) > level:
            stack.pop()
        if level > len(stack):
            raise ValueError(f"hierarchy level jump before node: {node['node_id']}")
        expected_parent = stack[-1]["node_id"] if level else None
        if node.get("parent_node_id") != expected_parent:
            raise ValueError(f"hierarchy parent mismatch for {node['node_id']}: expected {expected_parent}")
        if len(stack) == level:
            stack.append(node)
        else:
            stack[level] = node
    for index, node in enumerate(nodes):
        end = None
        for later in nodes[index + 1:]:
            if later["level"] <= node["level"]:
                end = later["source_order_start"] - 1
                break
        node["source_order_end"] = end

    source_entries = copy.deepcopy(bundle.get("source_toc_entries", []))
    entry_ids = [entry.get("entry_id") for entry in source_entries]
    if None in entry_ids or len(entry_ids) != len(set(entry_ids)):
        raise ValueError("source TOC entries have missing or duplicate ids")
    source_entries.sort(key=lambda item: item.get("source_order", 0))
    included_targets: list[str] = []
    for entry in source_entries:
        status = entry.get("scope_status")
        target = entry.get("target_node_id")
        if not entry.get("title") or not entry.get("source_outline_evidence_ids") or any(item not in evidence_ids for item in entry["source_outline_evidence_ids"]):
            raise ValueError(f"source TOC entry lacks title or evidence: {entry.get('entry_id')}")
        if status == "included":
            if target not in node_by_id:
                raise ValueError(f"included source TOC entry lacks a body node: {entry['entry_id']}")
            included_targets.append(target)
            if entry.get("match_status") not in {"exact", "approved_normalization", "source_supported_structural_title"}:
                raise ValueError(f"included source TOC entry has invalid match status: {entry['entry_id']}")
        elif status == "excluded":
            if target is not None or not entry.get("scope_reason"):
                raise ValueError(f"excluded source TOC entry is not closed: {entry['entry_id']}")
        else:
            raise ValueError(f"source TOC entry has invalid scope status: {entry.get('entry_id')}")
    if len(included_targets) != len(set(included_targets)):
        raise ValueError("multiple source TOC entries map to the same body node")

    final_entries = []
    for node in nodes:
        final = node.get("final_toc", {})
        if not isinstance(final.get("include"), bool):
            raise ValueError(f"node lacks explicit final TOC disposition: {node['node_id']}")
        if final["include"]:
            if final.get("level") != node["level"] or not final.get("title"):
                raise ValueError(f"final TOC entry differs from the frozen hierarchy: {node['node_id']}")
            if final.get("title") != node["title"] and not final.get("approved_title_normalization"):
                raise ValueError(f"final TOC title changes source title without approval: {node['node_id']}")
            final_entries.append({
                "toc_entry_id": f"toc::{node['node_id']}", "node_id": node["node_id"],
                "title": final["title"], "level": final["level"],
                "source_order": node["source_order_start"], "source_toc_entry_ids": node.get("source_toc_entry_ids", []),
            })
    if not final_entries:
        raise ValueError("final TOC projection is empty")
    if any(block_id not in {item["block_id"] for item in inventory["candidates"]} for block_id in selected_title_ids):
        raise ValueError("selected structural title is outside the title candidate inventory")
    local_title_ids = sorted({item["block_id"] for item in inventory["candidates"]} - selected_title_ids)
    if set(node_ids) != {entry["node_id"] for entry in final_entries} | {node["node_id"] for node in nodes if not node["final_toc"]["include"]}:
        raise ValueError("final TOC projection does not explicitly dispose every structure node")

    outline = {
        "schema_version": CONTRACT_SCHEMA,
        "contract_id": bundle["review_id"],
        "generated_at": now(),
        "slice_status": "passed",
        "full_spec04_status": "not_evaluated",
        "parent": {
            "ledger_snapshot_id": ledger_header["ledger_snapshot_id"],
            "ledger_payload_hash": ledger_header["current_ledger_hash"],
            "source_pdf_sha256": sha256_file(source_pdf),
            "promotion_id": parent_promotion["promotion_id"],
            "promotion_manifest_sha256": parent_promotion["manifest_sha256"],
        },
        "source_outline_evidence": evidence,
        "source_toc_entries": source_entries,
        "body_hierarchy": nodes,
        "title_candidate_disposition": {
            "inventory_payload_hash": inventory["payload_hash"],
            "candidate_count": inventory["candidate_count"],
            "structural_title_blocks": sorted(selected_title_ids),
            "local_heading_blocks": local_title_ids,
            "unresolved": 0,
        },
        "final_toc_entries": final_entries,
        "prohibitions": ["teaching_box_choice", "template_construct_choice", "formula_reconstruction", "table_reconstruction", "latex_generation", "upstream_cleaning_rewrite"],
        "summary": {
            "source_toc_entries": len(source_entries),
            "included_source_toc_entries": sum(item["scope_status"] == "included" for item in source_entries),
            "structure_nodes": len(nodes),
            "final_toc_entries": len(final_entries),
            "title_candidates": inventory["candidate_count"],
            "local_headings_excluded_from_toc": len(local_title_ids),
            "open_reviews": 0,
        },
    }
    final_toc = {
        "schema_version": "final-toc-plan/1.0", "generated_at": outline["generated_at"],
        "status": "passed", "source_outline_contract_payload_hash": canonical_hash(outline),
        "entries": final_entries, "open_reviews": 0,
        "scope": "abstract TOC visibility and level only; no template construct or LaTeX command selected",
    }
    queue = {
        "schema_version": "structure-review-queue/1.0", "generated_at": outline["generated_at"],
        "status": "closed", "open_items": 0, "items": [],
    }
    return outline, final_toc, queue, inventory


def capability_resources(skill_root: Path, review_bundle: Path) -> list[tuple[str, Path]]:
    return [
        ("machine_schema", skill_root / "schemas/spec04a-outline-review-bundle.schema.json"),
        ("machine_schema", skill_root / "schemas/spec04a-structure-contract.schema.json"),
        ("machine_schema", skill_root / "schemas/spec04a-structure-stage-manifest.schema.json"),
        ("machine_schema", skill_root / "schemas/execution-capability-manifest.schema.json"),
        ("book_configuration", review_bundle),
    ]


def capability_invocation(args: argparse.Namespace) -> list[str]:
    return [
        "spec04a_structure_contract.py", "produce",
        "--parent-ledger", str(args.parent_ledger.resolve()),
        "--parent-decision-index", str(args.parent_decision_index.resolve()),
        "--source-pdf", str(args.source_pdf.resolve()),
        "--promotion-registry", str(args.promotion_registry.resolve()),
        "--parent-promotion", str(args.parent_promotion.resolve()),
        "--parent-lineage-key", args.parent_lineage_key,
        "--review-bundle", str(args.review_bundle.resolve()),
        "--ledger-snapshot-id", args.ledger_snapshot_id,
        "--ledger-version", str(args.ledger_version),
        "--decision-snapshot-id", args.decision_snapshot_id,
        "--stage-decision-id", args.stage_decision_id,
        "--run-id", args.run_id,
        "--output-dir", str(args.output_dir.resolve()),
    ]


def verify_parent_selection(args: argparse.Namespace, parent_ledger: Path) -> dict[str, Any]:
    core = load_promotion_core()
    selection = core.verify_registry_selection(
        args.promotion_registry.resolve(), args.parent_lineage_key,
        args.parent_promotion.resolve(), "spec03_media_contract",
        capability_verification="frozen",
    )
    promotion = selection["promotion"]
    artifact = promotion.get("promoted_artifacts", {}).get("ledger_L", {})
    if Path(artifact.get("path", "")).resolve() != parent_ledger.resolve() or artifact.get("sha256") != sha256_file(parent_ledger):
        raise ValueError("active parent promotion does not promote the supplied canonical ledger")
    return {
        "promotion_id": promotion["promotion_id"],
        "promotion_class": promotion["promotion_class"],
        "producer_execution_provenance": promotion.get("producer_execution_provenance"),
        "manifest_path": str(args.parent_promotion.resolve()),
        "manifest_sha256": sha256_file(args.parent_promotion.resolve()),
        "registry_path": str(args.promotion_registry.resolve()),
        "registry_sha256": sha256_file(args.promotion_registry.resolve()),
        "lineage_key": args.parent_lineage_key,
        "capability_verification": "frozen_ancestor_snapshot",
    }


def validation_report(outline: dict[str, Any], final_toc: dict[str, Any], queue: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    structural_titles = set(outline["title_candidate_disposition"]["structural_title_blocks"])
    local_titles = set(outline["title_candidate_disposition"]["local_heading_blocks"])
    checks = [
        ("S4A-H01-formal-source-input", outline["parent"]["ledger_snapshot_id"] is not None),
        ("S4A-H02-source-outline-evidence-bound", bool(outline["source_outline_evidence"])),
        ("S4A-H03-source-toc-reconciled", all(item["scope_status"] in {"included", "excluded"} for item in outline["source_toc_entries"])),
        ("S4A-H04-title-candidate-inventory-complete", outline["title_candidate_disposition"]["candidate_count"] == inventory["candidate_count"]),
        ("S4A-H05-hierarchy-valid", bool(outline["body_hierarchy"])),
        ("S4A-H06-source-order-preserved", [item["source_order"] for item in final_toc["entries"]] == sorted(item["source_order"] for item in final_toc["entries"])),
        ("S4A-H07-final-toc-exact", len(final_toc["entries"]) == outline["summary"]["final_toc_entries"]),
        ("S4A-H08-local-headings-do-not-pollute-toc", not (structural_titles & local_titles) and len(structural_titles | local_titles) == inventory["candidate_count"]),
        ("S4A-H09-no-open-review", queue["open_items"] == 0),
        ("S4A-H10-planning-boundary-preserved", set(outline["prohibitions"]) >= {"teaching_box_choice", "latex_generation"}),
    ]
    items = [{"check_id": check_id, "status": "passed" if result else "failed"} for check_id, result in checks]
    return {
        "schema_version": "spec04a-structure-validation/1.0", "generated_at": now(),
        "status": "passed" if all(result for _, result in checks) else "failed",
        "checks": items,
        "summary": {"checks": len(items), "passed": sum(item["status"] == "passed" for item in items), "failed": sum(item["status"] == "failed" for item in items)},
    }


def produce(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite run directory: {output}")
    output.mkdir(parents=True)
    parent_ledger_path = args.parent_ledger.resolve()
    parent_index_path = args.parent_decision_index.resolve()
    source_pdf = args.source_pdf.resolve()
    review_bundle_path = args.review_bundle.resolve()
    header, records = read_ledger(parent_ledger_path)
    parent_index = read_json(parent_index_path)
    closed_decision_index(parent_index)
    if header.get("canonical_decision_index_hash") != sha256_file(parent_index_path):
        raise ValueError("parent ledger is not bound to the supplied decision index")
    parent_promotion = verify_parent_selection(args, parent_ledger_path)
    bundle = read_json(review_bundle_path)
    outline, final_toc, queue, inventory = validate_bundle(
        ledger_header=header, records=records, bundle=bundle, source_pdf=source_pdf,
        parent_promotion=parent_promotion,
    )
    write_json(output / "precommit/structure_title_candidate_inventory.json", inventory)

    skill_root = Path(__file__).parents[1].resolve()
    execution_core = load_execution_core()
    capability_path = output / "precommit/execution_capability_manifest.json"
    capability = execution_core.build_manifest(
        manifest_id=f"{args.run_id}-producer-capability", skill_root=skill_root,
        entrypoints=[
            ("stage_producer", Path(__file__).resolve()),
            ("execution_capability_core", Path(__file__).with_name("execution_capability.py").resolve()),
            ("promotion_selection_core", Path(__file__).with_name("stage_promotion_gate.py").resolve()),
        ],
        resources=capability_resources(skill_root, review_bundle_path),
        invocation=capability_invocation(args), producer=VERSION,
    )
    write_json(capability_path, capability)
    execution_core.validate_manifest(capability_path)

    precommit_evidence = [
        {"role": "parent_canonical_ledger", "path": str(parent_ledger_path), "sha256": sha256_file(parent_ledger_path)},
        {"role": "parent_decision_index", "path": str(parent_index_path), "sha256": sha256_file(parent_index_path)},
        {"role": "source_pdf", "path": str(source_pdf), "sha256": sha256_file(source_pdf)},
        {"role": "active_parent_promotion", "path": parent_promotion["manifest_path"], "sha256": parent_promotion["manifest_sha256"]},
        {"role": "promotion_registry", "path": parent_promotion["registry_path"], "sha256": parent_promotion["registry_sha256"]},
        {"role": "outline_review_bundle", "path": str(review_bundle_path), "sha256": sha256_file(review_bundle_path)},
        {"role": "title_candidate_inventory", "path": "precommit/structure_title_candidate_inventory.json", "sha256": sha256_file(output / "precommit/structure_title_candidate_inventory.json")},
        {"role": "execution_capability", "path": "precommit/execution_capability_manifest.json", "sha256": sha256_file(capability_path)},
    ]
    event = {
        "decision_id": args.stage_decision_id, "status": "closed",
        "rule_id": "SM-H01/SM-H02/SPEC04A-STRUCTURE-COMMIT", "decided_at": now(),
        "decision_type": "reviewed_structure_commit",
        "scope": "Freeze source TOC reconciliation, body hierarchy, title-candidate dispositions, and abstract final TOC only.",
        "evidence": precommit_evidence,
        "review_refs": bundle.get("review", {}).get("decision_refs", []),
        "prohibitions": outline["prohibitions"], "supersedes": [], "invalidated_by": None,
    }
    event_path = output / "decisions/structure_decisions.jsonl"
    write_jsonl(event_path, [event])
    decisions = copy.deepcopy(parent_index.get("decisions", []))
    if args.stage_decision_id in {item.get("decision_id") for item in decisions}:
        raise ValueError(f"stage decision id already exists: {args.stage_decision_id}")
    decisions.append({
        "decision_id": args.stage_decision_id, "event_file": "decisions/structure_decisions.jsonl",
        "rule_id": event["rule_id"], "status": "closed", "supersedes": [], "invalidated_by": None,
    })
    statuses = Counter(item.get("status") for item in decisions)
    index = {
        "schema_version": "canonical-decision-index/1.1", "decision_index_id": parent_index["decision_index_id"],
        "snapshot_id": args.decision_snapshot_id, "version": int(parent_index["version"]) + 1,
        "generated_at": now(), "parent_index_ref": relative(output, parent_index_path),
        "parent_index_hash": sha256_file(parent_index_path),
        "acyclic_commit_rule": "evidence_or_parent_then_decision_index_D_then_child_artifact_L",
        "spec_status": "passed", "evidence_committed_before_index": precommit_evidence,
        "decision_event_files": [{"path": "decisions/structure_decisions.jsonl", "sha256": sha256_file(event_path), "decision_ids": [args.stage_decision_id]}],
        "decisions": decisions,
        "summary": {"closed": statuses["closed"], "superseded": statuses["superseded"], "open": 0, "stale": 0, "invalidated": 0},
    }
    decision_path = output / "decisions/canonical_decision_index.json"
    write_json(decision_path, index)
    decision_sha = sha256_file(decision_path)

    outline["canonical_decision_index_sha256"] = decision_sha
    outline_path = output / "structure/source_outline_ledger.json"
    write_json(outline_path, outline)
    final_toc["source_outline_contract_sha256"] = sha256_file(outline_path)
    final_toc_path = output / "structure/final_toc_plan.json"
    write_json(final_toc_path, final_toc)
    queue_path = output / "structure/structure_review_queue.json"
    write_json(queue_path, queue)
    report = validation_report(outline, final_toc, queue, inventory)
    report_path = output / "reports/spec04a_structure_validation.json"
    write_json(report_path, report)
    if report["status"] != "passed":
        raise ValueError("internal Spec 04-A validation failed")

    selected_map: dict[str, list[dict[str, Any]]] = {}
    for node in outline["body_hierarchy"]:
        selected_map.setdefault(node["anchor_block_id"], []).append({
            "node_id": node["node_id"], "role": node["role"], "level": node["level"],
            "title_sha256": node["title_sha256"], "final_toc_included": node["final_toc"]["include"],
            "decision_refs": [args.stage_decision_id],
        })
    candidate_ids = {item["block_id"] for item in inventory["candidates"]}
    structural_title_ids = set(outline["title_candidate_disposition"]["structural_title_blocks"])
    records_out = copy.deepcopy(records)
    for record in records_out:
        if record["block_id"] in selected_map:
            record["structure_memberships"] = selected_map[record["block_id"]]
        if record["block_id"] in candidate_ids:
            record["heading_disposition"] = "structure_node" if record["block_id"] in structural_title_ids else "local_heading"
            record["heading_disposition_decision_refs"] = [args.stage_decision_id]
    header_out = copy.deepcopy(header)
    header_out.update({
        "generated_at": now(), "updated_at": now(), "ledger_snapshot_id": args.ledger_snapshot_id,
        "ledger_version": args.ledger_version, "parent_ledger_ref": relative(output, parent_ledger_path),
        "parent_ledger_file_sha256": sha256_file(parent_ledger_path), "parent_ledger_hash": header["current_ledger_hash"],
        "canonical_decision_index_ref": "decisions/canonical_decision_index.json", "canonical_decision_index_hash": decision_sha,
        "current_ledger_hash": canonical_hash(records_out),
        "current_ledger_hash_scope": "canonical JSON hash of ordered source_block records with Spec 04-A structure overlay",
        "spec04a_structure": {
            "status": "passed", "full_spec04_status": "not_evaluated", "producer": VERSION,
            "source_outline_ledger_sha256": sha256_file(outline_path), "final_toc_plan_sha256": sha256_file(final_toc_path),
            "structure_nodes": outline["summary"]["structure_nodes"], "final_toc_entries": outline["summary"]["final_toc_entries"],
            "open_reviews": 0,
        },
    })
    ledger_path = output / "ledgers/canonical_block_ledger.jsonl"
    write_jsonl(ledger_path, [header_out, *records_out])
    write_json(output / "ledgers/ledger_manifest.json", {
        "schema_version": "ledger-manifest/2.2", "generated_at": now(), "ledger_id": header_out["ledger_id"],
        "ledger_version": header_out["ledger_version"], "snapshot_id": header_out["ledger_snapshot_id"],
        "artifact_path": "ledgers/canonical_block_ledger.jsonl", "artifact_sha256": sha256_file(ledger_path),
        "payload_hash": header_out["current_ledger_hash"], "parent_artifact_ref": header_out["parent_ledger_ref"],
        "parent_artifact_sha256": header_out["parent_ledger_file_sha256"],
        "decision_index_ref": "decisions/canonical_decision_index.json", "decision_index_hash": decision_sha,
        "spec04a_structure_status": "passed", "full_spec04_status": "not_evaluated", "immutable_after_publication": True,
    })

    producer_mode = "formal_native" if parent_promotion["promotion_class"] == "formal_native" else "migration_compatibility"
    stage = {
        "schema_version": STAGE_SCHEMA, "stage_kind": "spec04a_structure_contract", "run_id": args.run_id,
        "generated_at": now(), "status": "passed", "slice_status": "passed", "full_spec04_status": "not_evaluated",
        "producer": VERSION, "producer_mode": producer_mode,
        "commit_order": ["precommit_evidence_and_execution_capability_E", "decision_index_D", "structure_contract_and_ledger_L", "stage_manifest_M"],
        "parent_promotion": parent_promotion,
        "execution_capability_E": {"path": "precommit/execution_capability_manifest.json", "sha256": sha256_file(capability_path), "payload_hash": capability["payload_hash"]},
        "decision_index_D": {"path": "decisions/canonical_decision_index.json", "sha256": decision_sha},
        "ledger_L": {"path": "ledgers/canonical_block_ledger.jsonl", "sha256": sha256_file(ledger_path), "payload_hash": header_out["current_ledger_hash"]},
        "source_outline_ledger": {"path": "structure/source_outline_ledger.json", "sha256": sha256_file(outline_path)},
        "final_toc_plan": {"path": "structure/final_toc_plan.json", "sha256": sha256_file(final_toc_path)},
        "review_queue": {"path": "structure/structure_review_queue.json", "sha256": sha256_file(queue_path)},
        "validation": {"path": "reports/spec04a_structure_validation.json", "sha256": sha256_file(report_path)},
        "scope_prohibitions": outline["prohibitions"],
    }
    stage_path = output / "manifests/spec04a_structure_stage_manifest.json"
    write_json(stage_path, stage)
    run_files = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "run_manifest.json":
            run_files.append({"path": path.relative_to(output).as_posix(), "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
    run_manifest = {
        "schema_version": "immutable-run-manifest/1.1", "run_id": args.run_id, "generated_at": now(),
        "status": "passed", "stage_kind": "spec04a_structure_contract", "producer_mode": producer_mode,
        "immutable_after_publication": True, "files": run_files,
    }
    write_json(output / "manifests/run_manifest.json", run_manifest)
    return stage, 0


def validate_run(run_dir: Path) -> dict[str, Any]:
    run = run_dir.resolve()
    stage_path = run / "manifests/spec04a_structure_stage_manifest.json"
    stage = read_json(stage_path)
    if stage.get("schema_version") != STAGE_SCHEMA or stage.get("status") != "passed":
        raise ValueError("unsupported or non-passed Spec 04-A stage manifest")
    required = ["execution_capability_E", "decision_index_D", "ledger_L", "source_outline_ledger", "final_toc_plan", "review_queue", "validation"]
    artifacts = {}
    for name in required:
        item = stage.get(name, {})
        path = run / item.get("path", "")
        if not path.is_file() or sha256_file(path) != item.get("sha256"):
            raise ValueError(f"Spec 04-A stage artifact is missing or drifted: {name}")
        artifacts[name] = path
    execution = load_execution_core().validate_manifest(artifacts["execution_capability_E"])
    header, records = read_ledger(artifacts["ledger_L"])
    index = read_json(artifacts["decision_index_D"])
    closed_decision_index(index)
    if header.get("canonical_decision_index_hash") != sha256_file(artifacts["decision_index_D"]):
        raise ValueError("Spec 04-A ledger is not bound to decision index D")
    if stage["ledger_L"].get("payload_hash") != header.get("current_ledger_hash"):
        raise ValueError("Spec 04-A stage manifest ledger payload hash mismatch")
    index_values = scalar_strings(index)
    forbidden = [header.get("ledger_snapshot_id"), header.get("current_ledger_hash"), sha256_file(artifacts["ledger_L"])]
    if any(item and item in index_values for item in forbidden):
        raise ValueError("decision index D references its descendant ledger L")
    outline = read_json(artifacts["source_outline_ledger"])
    final_toc = read_json(artifacts["final_toc_plan"])
    queue = read_json(artifacts["review_queue"])
    report = read_json(artifacts["validation"])
    if outline.get("slice_status") != "passed" or final_toc.get("status") != "passed" or queue.get("open_items") != 0 or report.get("status") != "passed":
        raise ValueError("Spec 04-A live artifacts are not closed and passed")
    membership_nodes = {
        membership["node_id"]
        for record in records for membership in record.get("structure_memberships", [])
    }
    expected_nodes = {node["node_id"] for node in outline["body_hierarchy"]}
    if membership_nodes != expected_nodes:
        raise ValueError("canonical ledger structure overlay differs from source outline ledger")
    if {item["node_id"] for item in final_toc["entries"]} != {node["node_id"] for node in outline["body_hierarchy"] if node["final_toc"]["include"]}:
        raise ValueError("final TOC projection differs from the source outline ledger")
    run_manifest_path = run / "manifests/run_manifest.json"
    run_manifest = read_json(run_manifest_path)
    drift = [item["path"] for item in run_manifest.get("files", []) if not (run / item["path"]).is_file() or sha256_file(run / item["path"]) != item["sha256"]]
    if run_manifest.get("status") != "passed" or not run_manifest.get("immutable_after_publication") or drift:
        raise ValueError(f"Spec 04-A immutable run manifest is invalid or drifted: {drift[:8]}")
    return {
        "status": "passed", "run_id": stage["run_id"], "producer_mode": stage["producer_mode"],
        "structure_nodes": len(expected_nodes), "final_toc_entries": len(final_toc["entries"]),
        "title_candidates": outline["summary"]["title_candidates"], "local_headings": outline["summary"]["local_headings_excluded_from_toc"],
        "open_reviews": 0, "producer_execution_capability": execution,
    }


def add_produce_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--parent-ledger", type=Path, required=True)
    parser.add_argument("--parent-decision-index", type=Path, required=True)
    parser.add_argument("--source-pdf", type=Path, required=True)
    parser.add_argument("--promotion-registry", type=Path, required=True)
    parser.add_argument("--parent-promotion", type=Path, required=True)
    parser.add_argument("--parent-lineage-key", required=True)
    parser.add_argument("--review-bundle", type=Path, required=True)
    parser.add_argument("--ledger-snapshot-id", required=True)
    parser.add_argument("--ledger-version", type=int, required=True)
    parser.add_argument("--decision-snapshot-id", required=True)
    parser.add_argument("--stage-decision-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    inventory = sub.add_parser("inventory")
    inventory.add_argument("--ledger", type=Path, required=True)
    inventory.add_argument("--output", type=Path, required=True)
    produce_parser = sub.add_parser("produce")
    add_produce_arguments(produce_parser)
    validate = sub.add_parser("validate-run")
    validate.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "inventory":
            _, records = read_ledger(args.ledger.resolve())
            result = title_candidate_inventory(records)
            write_json(args.output.resolve(), result)
            code = 0
        elif args.command == "produce":
            result, code = produce(args)
        else:
            result = validate_run(args.run_dir)
            code = 0
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return code
    except Exception as exc:
        print(json.dumps({"status": "failed", "tool": VERSION, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
