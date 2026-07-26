#!/usr/bin/env python3
"""Produce the Spec 04-B semantic-span and teaching-column grouping contract.

This stage consumes an active Spec 04-A promotion.  It partitions every
included source atom exactly once into a conservative semantic span and groups
only explicitly reviewed, source-supported teaching columns.  It does not
choose ElegantBook constructs, boxes, render nodes, formula/table rebuilds, or
LaTeX syntax.
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


VERSION = "spec04b-semantic-span-contract/1.0.0"
CONTRACT_SCHEMA = "spec04b-semantic-span-contract/1.0"
STAGE_SCHEMA = "spec04b-semantic-stage-manifest/1.0"
ALLOWED_BODY_TYPES = {"text", "aside_text", "list"}
FRAGILE_TYPES = {
    "equation", "formula", "image", "chart", "table", "image_caption",
    "image_footnote", "table_caption", "page_footnote",
}
FORBIDDEN_KEYS = {
    "target_construct", "construct_parameters", "render_plan", "render_node_id",
    "latex", "tcolorbox", "box_style", "template_capability_manifest",
    "formula_reconstruction", "table_reconstruction",
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
    if any(item.get("record_type") != "source_block" for item in records):
        raise ValueError("Spec 04-B requires a source-block-only canonical payload")
    return header, records


def load_module(filename: str, module_name: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load required core: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_no_downstream_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        found = FORBIDDEN_KEYS & set(value)
        if found:
            raise ValueError(f"Spec 04-B contains downstream keys at {path}: {sorted(found)}")
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
    identifiers = [item.get("decision_id") for item in index.get("decisions", [])]
    if None in identifiers or len(identifiers) != len(set(identifiers)):
        raise ValueError("parent decision index has missing or duplicate ids")
    unresolved = [
        item.get("decision_id") for item in index.get("decisions", [])
        if item.get("status") in {"open", "stale", "invalidated"}
    ]
    if unresolved:
        raise ValueError(f"parent decision index has unresolved decisions: {unresolved[:8]}")


def source_order(record: dict[str, Any]) -> tuple[int, int, str]:
    page = record.get("pdf_physical_page")
    order = record.get("candidate_final_order")
    if not isinstance(page, int) or not isinstance(order, int):
        raise ValueError(f"included source atom lacks exact page/order: {record.get('block_id')}")
    return page, order, record["block_id"]


def verify_parent_selection(args: argparse.Namespace, parent_ledger: Path) -> dict[str, Any]:
    core = load_module("stage_promotion_gate.py", "stage_promotion_gate_spec04b")
    selected = core.verify_registry_selection(
        args.promotion_registry.resolve(), args.parent_lineage_key,
        args.parent_promotion.resolve(), "spec04a_structure_contract",
        capability_verification="frozen",
    )
    promotion = selected["promotion"]
    artifact = promotion.get("promoted_artifacts", {}).get("ledger_L", {})
    if Path(artifact.get("path", "")).resolve() != parent_ledger or artifact.get("sha256") != sha256_file(parent_ledger):
        raise ValueError("active Spec 04-A promotion does not promote the supplied canonical ledger")
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
        "source_outline_ledger": promotion["promoted_artifacts"]["source_outline_ledger"],
        "final_toc_plan": promotion["promoted_artifacts"]["final_toc_plan"],
        "decision_index_D": promotion["promoted_artifacts"]["decision_index_D"],
    }


def verify_evidence(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    verified = []
    ids: set[str] = set()
    for item in bundle.get("source_evidence", []):
        evidence_id = item.get("evidence_id")
        path = Path(item.get("path", "")).resolve()
        if not evidence_id or evidence_id in ids:
            raise ValueError(f"source evidence has missing or duplicate id: {evidence_id}")
        if not path.is_file() or sha256_file(path) != item.get("sha256"):
            raise ValueError(f"source evidence is missing or drifted: {evidence_id}")
        if not isinstance(item.get("pdf_physical_page"), int) or item["pdf_physical_page"] < 1:
            raise ValueError(f"source evidence lacks a physical page: {evidence_id}")
        ids.add(evidence_id)
        verified.append({**item, "path": str(path)})
    if not verified:
        raise ValueError("semantic review bundle has no source-page evidence")
    return verified


def validate_bundle(
    *, header: dict[str, Any], records: list[dict[str, Any]], bundle: dict[str, Any],
    source_pdf: Path, parent: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if bundle.get("schema_version") != "spec04b-semantic-review-bundle/1.0":
        raise ValueError("unsupported Spec 04-B semantic review bundle")
    assert_no_downstream_keys(bundle)
    if bundle.get("review", {}).get("status") != "closed" or bundle.get("review", {}).get("open_items") != 0:
        raise ValueError("Spec 04-B semantic review is not closed")
    if header.get("spec04a_structure", {}).get("status") != "passed":
        raise ValueError("Spec 04-B requires a passed Spec 04-A ledger")
    if header.get("spec04a_structure", {}).get("full_spec04_status") != "not_evaluated":
        raise ValueError("Spec 04-A parent incorrectly claims full Spec 04")
    if header.get("material_identity", {}).get("source_pdf_sha256") != sha256_file(source_pdf):
        raise ValueError("source PDF differs from the Spec 04-A ledger")

    binding = bundle.get("parent_binding", {})
    expected = {
        "ledger_snapshot_id": header.get("ledger_snapshot_id"),
        "ledger_payload_hash": header.get("current_ledger_hash"),
        "source_pdf_sha256": sha256_file(source_pdf),
        "promotion_id": parent["promotion_id"],
        "promotion_manifest_sha256": parent["manifest_sha256"],
        "source_outline_ledger_sha256": parent["source_outline_ledger"]["sha256"],
        "final_toc_plan_sha256": parent["final_toc_plan"]["sha256"],
    }
    drift = sorted(key for key, value in expected.items() if binding.get(key) != value)
    if drift:
        raise ValueError(f"semantic review bundle parent binding drifted: {drift}")

    evidence = verify_evidence(bundle)
    evidence_by_id = {item["evidence_id"]: item for item in evidence}
    by_id = {record["block_id"]: record for record in records}
    included = {block_id: record for block_id, record in by_id.items() if record.get("scope_status") == "included"}
    if not included:
        raise ValueError("Spec 04-B parent ledger has no included source atoms")
    for record in included.values():
        source_order(record)

    group_ids: set[str] = set()
    grouped_blocks: set[str] = set()
    group_by_block: dict[str, dict[str, Any]] = {}
    groups_out = []
    for group in copy.deepcopy(bundle.get("teaching_groups", [])):
        group_id = group.get("group_id")
        marker_id = group.get("marker_block_id")
        body_ids = group.get("body_block_ids", [])
        if not group_id or group_id in group_ids:
            raise ValueError(f"teaching group has missing or duplicate id: {group_id}")
        if not body_ids:
            raise ValueError(f"EMPTY_TEACHING_GROUP: {group_id}")
        if marker_id in body_ids or len(body_ids) != len(set(body_ids)):
            raise ValueError(f"teaching group has duplicate marker/body membership: {group_id}")
        member_ids = [marker_id, *body_ids]
        if any(block_id not in included for block_id in member_ids):
            raise ValueError(f"teaching group contains a non-included source atom: {group_id}")
        overlap = grouped_blocks & set(member_ids)
        if overlap:
            raise ValueError(f"teaching group membership overlaps another group: {sorted(overlap)}")
        marker = included[marker_id]
        if marker.get("structure_memberships") or marker.get("heading_disposition") == "structure_node":
            raise ValueError(f"teaching group marker conflicts with a Spec 04-A structure node: {marker_id}")
        if marker.get("source_type") not in {"title", "text", "aside_text"}:
            raise ValueError(f"teaching group marker has an unsafe source type: {marker_id}")
        if marker.get("heading_disposition") not in {None, "local_heading"}:
            raise ValueError(f"teaching group marker has an incompatible Spec 04-A disposition: {marker_id}")
        allowed_types = set(group.get("relation_rule", {}).get("allowed_body_source_types", []))
        if not allowed_types or not allowed_types <= ALLOWED_BODY_TYPES:
            raise ValueError(f"teaching group declares unsafe body source types: {group_id}")
        if any(included[block_id].get("source_type") not in allowed_types for block_id in body_ids):
            raise ValueError(f"teaching group includes media, formula, table, or undeclared body type: {group_id}")
        pages = {included[block_id].get("pdf_physical_page") for block_id in member_ids}
        if len(pages) != 1 or group.get("relation_rule", {}).get("same_physical_page") is not True:
            raise ValueError(f"teaching group crosses physical pages: {group_id}")
        basis = group.get("relation_rule", {}).get("basis")
        if basis not in {"same_tree_path_and_spatial_proximity", "reviewed_visual_bbox_subset"}:
            raise ValueError(f"teaching group lacks a supported relation basis: {group_id}")
        if basis == "same_tree_path_and_spatial_proximity":
            paths = {tuple(included[block_id].get("tree_context", {}).get("node_path", [])) for block_id in member_ids}
            if len(paths) != 1 or group.get("relation_rule", {}).get("same_tree_path") is not True:
                raise ValueError(f"same-tree teaching group does not share an exact Popo tree path: {group_id}")
        refs = group.get("source_evidence_ids", [])
        if not refs or any(ref not in evidence_by_id for ref in refs):
            raise ValueError(f"teaching group lacks exact source-page evidence: {group_id}")
        page = next(iter(pages))
        if not any(evidence_by_id[ref]["pdf_physical_page"] == page for ref in refs):
            raise ValueError(f"teaching group evidence page differs from group page: {group_id}")
        if group.get("review_status") != "closed" or not group.get("semantic_role"):
            raise ValueError(f"teaching group review is incomplete: {group_id}")
        ordered_ids = sorted(member_ids, key=lambda block_id: source_order(included[block_id]))
        group["source_block_ids"] = ordered_ids
        group["pdf_physical_page"] = page
        group["source_order_start"] = min(included[item]["candidate_final_order"] for item in member_ids)
        group["source_order_end"] = max(included[item]["candidate_final_order"] for item in member_ids)
        group["source_content_hashes"] = {item: included[item].get("raw_content_sha256") for item in ordered_ids}
        groups_out.append(group)
        group_ids.add(group_id)
        grouped_blocks.update(member_ids)
        for block_id in member_ids:
            group_by_block[block_id] = group

    standalone_by_block: dict[str, dict[str, Any]] = {}
    standalone_out = []
    for item in copy.deepcopy(bundle.get("standalone_labels", [])):
        block_id = item.get("block_id")
        if block_id in standalone_by_block or block_id in grouped_blocks:
            raise ValueError(f"standalone teaching label overlaps another disposition: {block_id}")
        if block_id not in included:
            raise ValueError(f"standalone teaching label is not an included source atom: {block_id}")
        standalone_record = included[block_id]
        if standalone_record.get("structure_memberships") or standalone_record.get("heading_disposition") == "structure_node":
            raise ValueError(f"standalone teaching label conflicts with a Spec 04-A structure node: {block_id}")
        if standalone_record.get("source_type") not in {"title", "text", "aside_text"} or standalone_record.get("heading_disposition") not in {None, "local_heading"}:
            raise ValueError(f"standalone teaching label has an unsafe source type or disposition: {block_id}")
        refs = item.get("source_evidence_ids", [])
        page = included[block_id]["pdf_physical_page"]
        if not refs or any(ref not in evidence_by_id for ref in refs) or not any(evidence_by_id[ref]["pdf_physical_page"] == page for ref in refs):
            raise ValueError(f"standalone teaching label lacks exact source-page evidence: {block_id}")
        if item.get("review_status") != "closed" or not item.get("semantic_role"):
            raise ValueError(f"standalone teaching label review is incomplete: {block_id}")
        item["pdf_physical_page"] = page
        item["source_content_sha256"] = included[block_id].get("raw_content_sha256")
        standalone_by_block[block_id] = item
        standalone_out.append(item)

    spans = []
    assigned: set[str] = set()
    decision_refs = bundle.get("review", {}).get("decision_refs", [])
    for record in sorted(included.values(), key=source_order):
        block_id = record["block_id"]
        if block_id in assigned:
            continue
        if block_id in group_by_block:
            group = group_by_block[block_id]
            member_ids = group["source_block_ids"]
            disposition = "teaching_column"
            span_id = f"span::teaching::{group['group_id']}"
            role = group["semantic_role"]
            extra = {"teaching_group_id": group["group_id"], "semantic_role": role}
        else:
            member_ids = [block_id]
            if record.get("structure_memberships"):
                disposition = "book_structure"
            elif block_id in standalone_by_block:
                disposition = "standalone_semantic_label"
            elif record.get("heading_disposition") == "local_heading":
                disposition = "local_heading"
            elif record.get("source_type") in FRAGILE_TYPES or record.get("asset_ref"):
                disposition = "fragile_or_media"
            else:
                disposition = "plain_body"
            span_id = f"span::atom::{block_id}"
            extra = {}
            if block_id in standalone_by_block:
                extra["semantic_role"] = standalone_by_block[block_id]["semantic_role"]
        if assigned & set(member_ids):
            raise ValueError(f"semantic span overlap detected at {block_id}")
        members = [included[item] for item in member_ids]
        assigned.update(member_ids)
        spans.append({
            "span_id": span_id,
            "semantic_disposition": disposition,
            "source_block_ids": member_ids,
            "source_order_start": min(item["candidate_final_order"] for item in members),
            "source_order_end": max(item["candidate_final_order"] for item in members),
            "pdf_physical_pages": sorted({item["pdf_physical_page"] for item in members}),
            "review_status": "closed",
            "decision_refs": decision_refs,
            **extra,
        })
    if assigned != set(included):
        raise ValueError(f"semantic span partition is incomplete: missing={sorted(set(included) - assigned)[:8]}")

    contract = {
        "schema_version": CONTRACT_SCHEMA,
        "contract_id": bundle["review_id"],
        "generated_at": now(),
        "slice_status": "passed",
        "full_spec04_status": "not_evaluated",
        "parent": expected,
        "spans": spans,
        "prohibitions": [
            "elegantbook_box_choice", "template_construct_choice", "render_plan_generation",
            "formula_reconstruction", "table_reconstruction", "latex_generation", "upstream_cleaning_rewrite",
        ],
        "summary": {
            "included_source_atoms": len(included),
            "semantic_spans": len(spans),
            "teaching_groups": len(groups_out),
            "grouped_source_atoms": len(grouped_blocks),
            "standalone_semantic_labels": len(standalone_out),
            "open_reviews": 0,
            "dispositions": dict(sorted(Counter(item["semantic_disposition"] for item in spans).items())),
        },
    }
    groups = {
        "schema_version": "teaching-column-group-ledger/1.0",
        "generated_at": contract["generated_at"],
        "status": "passed",
        "full_spec04_status": "not_evaluated",
        "parent_contract_payload_hash": canonical_hash(contract),
        "groups": sorted(groups_out, key=lambda item: (item["pdf_physical_page"], item["source_order_start"], item["group_id"])),
        "standalone_labels": sorted(standalone_out, key=lambda item: source_order(included[item["block_id"]])),
        "open_reviews": 0,
        "scope": "source-supported semantic grouping only; no ElegantBook construct or box selected",
    }
    queue = {
        "schema_version": "semantic-span-review-queue/1.0",
        "generated_at": contract["generated_at"],
        "status": "closed", "open_items": 0, "items": [],
    }
    assert_no_downstream_keys(contract)
    assert_no_downstream_keys(groups)
    return contract, groups, queue


def validation_report(contract: dict[str, Any], groups: dict[str, Any], queue: dict[str, Any]) -> dict[str, Any]:
    source_ids = [block_id for span in contract["spans"] for block_id in span["source_block_ids"]]
    grouped_ids = [block_id for group in groups["groups"] for block_id in group["source_block_ids"]]
    checks = [
        ("S4B-H01-active-spec04a-parent-consumed", bool(contract["parent"]["promotion_id"])),
        ("S4B-H02-every-included-atom-disposed-once", len(source_ids) == len(set(source_ids)) == contract["summary"]["included_source_atoms"]),
        ("S4B-H03-teaching-groups-nonempty", all(group["body_block_ids"] for group in groups["groups"])),
        ("S4B-H04-group-membership-nonoverlap", len(grouped_ids) == len(set(grouped_ids))),
        ("S4B-H05-fragile-types-not-grouped", all(set(group["relation_rule"]["allowed_body_source_types"]) <= ALLOWED_BODY_TYPES for group in groups["groups"])),
        ("S4B-H06-source-page-evidence-bound", all(group["source_evidence_ids"] for group in groups["groups"])),
        ("S4B-H07-safe-ungrouped-degradation", all(span["semantic_disposition"] in {"book_structure", "teaching_column", "standalone_semantic_label", "local_heading", "fragile_or_media", "plain_body"} for span in contract["spans"])),
        ("S4B-H08-no-open-review", queue["open_items"] == groups["open_reviews"] == contract["summary"]["open_reviews"] == 0),
        ("S4B-H09-no-render-or-box-decision", set(contract["prohibitions"]) >= {"elegantbook_box_choice", "render_plan_generation", "latex_generation"}),
        ("S4B-H10-full-spec04-not-claimed", contract["full_spec04_status"] == groups["full_spec04_status"] == "not_evaluated"),
    ]
    items = [{"check_id": key, "status": "passed" if result else "failed"} for key, result in checks]
    return {
        "schema_version": "spec04b-semantic-span-validation/1.0", "generated_at": now(),
        "status": "passed" if all(result for _, result in checks) else "failed",
        "checks": items,
        "summary": {"checks": len(items), "passed": sum(item["status"] == "passed" for item in items), "failed": sum(item["status"] == "failed" for item in items)},
    }


def capability_resources(skill_root: Path, review_bundle: Path) -> list[tuple[str, Path]]:
    return [
        ("machine_schema", skill_root / "schemas/spec04b-semantic-review-bundle.schema.json"),
        ("machine_schema", skill_root / "schemas/spec04b-semantic-span-contract.schema.json"),
        ("machine_schema", skill_root / "schemas/spec04b-semantic-stage-manifest.schema.json"),
        ("machine_schema", skill_root / "schemas/execution-capability-manifest.schema.json"),
        ("book_configuration", review_bundle),
    ]


def capability_invocation(args: argparse.Namespace) -> list[str]:
    result = ["spec04b_semantic_span_contract.py", "produce"]
    for name in (
        "parent_ledger", "parent_decision_index", "source_pdf", "promotion_registry",
        "parent_promotion", "review_bundle", "output_dir",
    ):
        result.extend([f"--{name.replace('_', '-')}", str(getattr(args, name).resolve())])
    for name in ("parent_lineage_key", "ledger_snapshot_id", "ledger_version", "decision_snapshot_id", "stage_decision_id", "run_id"):
        result.extend([f"--{name.replace('_', '-')}", str(getattr(args, name))])
    return result


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
    parent = verify_parent_selection(args, parent_ledger_path)
    if Path(parent["decision_index_D"]["path"]).resolve() != parent_index_path or parent["decision_index_D"]["sha256"] != sha256_file(parent_index_path):
        raise ValueError("active Spec 04-A promotion does not promote the supplied decision index")
    bundle = read_json(review_bundle_path)
    contract, groups, queue = validate_bundle(
        header=header, records=records, bundle=bundle, source_pdf=source_pdf, parent=parent,
    )

    skill_root = Path(__file__).parents[1].resolve()
    execution_core = load_module("execution_capability.py", "execution_capability_spec04b")
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

    precommit = [
        {"role": "parent_canonical_ledger", "path": str(parent_ledger_path), "sha256": sha256_file(parent_ledger_path)},
        {"role": "parent_decision_index", "path": str(parent_index_path), "sha256": sha256_file(parent_index_path)},
        {"role": "source_pdf", "path": str(source_pdf), "sha256": sha256_file(source_pdf)},
        {"role": "active_spec04a_promotion", "path": parent["manifest_path"], "sha256": parent["manifest_sha256"]},
        {"role": "promotion_registry", "path": parent["registry_path"], "sha256": parent["registry_sha256"]},
        {"role": "source_outline_ledger", "path": parent["source_outline_ledger"]["path"], "sha256": parent["source_outline_ledger"]["sha256"]},
        {"role": "final_toc_plan", "path": parent["final_toc_plan"]["path"], "sha256": parent["final_toc_plan"]["sha256"]},
        {"role": "semantic_review_bundle", "path": str(review_bundle_path), "sha256": sha256_file(review_bundle_path)},
        {"role": "execution_capability", "path": "precommit/execution_capability_manifest.json", "sha256": sha256_file(capability_path)},
    ]
    event = {
        "decision_id": args.stage_decision_id, "status": "closed", "decided_at": now(),
        "rule_id": "SM-H03/SM-R02/SM-R03/SPEC04B-SEMANTIC-SPAN-COMMIT",
        "decision_type": "reviewed_semantic_span_and_group_commit",
        "scope": "Freeze exact source-supported semantic spans and teaching-column membership only.",
        "evidence": precommit, "review_refs": bundle.get("review", {}).get("decision_refs", []),
        "prohibitions": contract["prohibitions"], "supersedes": [], "invalidated_by": None,
    }
    event_path = output / "decisions/semantic_span_decisions.jsonl"
    write_jsonl(event_path, [event])
    decisions = copy.deepcopy(parent_index.get("decisions", []))
    if args.stage_decision_id in {item.get("decision_id") for item in decisions}:
        raise ValueError(f"stage decision id already exists: {args.stage_decision_id}")
    decisions.append({
        "decision_id": args.stage_decision_id, "event_file": "decisions/semantic_span_decisions.jsonl",
        "rule_id": event["rule_id"], "status": "closed", "supersedes": [], "invalidated_by": None,
    })
    statuses = Counter(item.get("status") for item in decisions)
    index = {
        "schema_version": "canonical-decision-index/1.1", "decision_index_id": parent_index["decision_index_id"],
        "snapshot_id": args.decision_snapshot_id, "version": int(parent_index["version"]) + 1,
        "generated_at": now(), "parent_index_ref": relative(output, parent_index_path),
        "parent_index_hash": sha256_file(parent_index_path),
        "acyclic_commit_rule": "evidence_or_parent_then_decision_index_D_then_child_artifact_L",
        "spec_status": "passed", "evidence_committed_before_index": precommit,
        "decision_event_files": [{"path": "decisions/semantic_span_decisions.jsonl", "sha256": sha256_file(event_path), "decision_ids": [args.stage_decision_id]}],
        "decisions": decisions,
        "summary": {"closed": statuses["closed"], "superseded": statuses["superseded"], "open": 0, "stale": 0, "invalidated": 0},
    }
    decision_path = output / "decisions/canonical_decision_index.json"
    write_json(decision_path, index)
    decision_sha = sha256_file(decision_path)

    contract["canonical_decision_index_sha256"] = decision_sha
    contract_path = output / "semantic/semantic_span_ledger.json"
    write_json(contract_path, contract)
    groups["semantic_span_contract_sha256"] = sha256_file(contract_path)
    groups_path = output / "semantic/teaching_column_group_ledger.json"
    write_json(groups_path, groups)
    queue_path = output / "semantic/semantic_review_queue.json"
    write_json(queue_path, queue)
    report = validation_report(contract, groups, queue)
    report_path = output / "reports/spec04b_semantic_span_validation.json"
    write_json(report_path, report)
    if report["status"] != "passed":
        raise ValueError("internal Spec 04-B validation failed")

    span_by_block = {
        block_id: span for span in contract["spans"] for block_id in span["source_block_ids"]
    }
    records_out = copy.deepcopy(records)
    for record in records_out:
        span = span_by_block.get(record["block_id"])
        if span:
            record["semantic_span_id"] = span["span_id"]
            record["semantic_disposition"] = span["semantic_disposition"]
            record["semantic_disposition_decision_refs"] = [args.stage_decision_id]
            if span.get("teaching_group_id"):
                record["teaching_group_id"] = span["teaching_group_id"]
                record["teaching_group_role"] = span["semantic_role"]
    header_out = copy.deepcopy(header)
    header_out.update({
        "generated_at": now(), "updated_at": now(), "ledger_snapshot_id": args.ledger_snapshot_id,
        "ledger_version": args.ledger_version, "parent_ledger_ref": relative(output, parent_ledger_path),
        "parent_ledger_file_sha256": sha256_file(parent_ledger_path), "parent_ledger_hash": header["current_ledger_hash"],
        "canonical_decision_index_ref": "decisions/canonical_decision_index.json", "canonical_decision_index_hash": decision_sha,
        "current_ledger_hash": canonical_hash(records_out),
        "current_ledger_hash_scope": "canonical JSON hash of ordered source_block records with Spec 04-B semantic-span overlay",
        "spec04b_semantic_spans": {
            "status": "passed", "full_spec04_status": "not_evaluated", "producer": VERSION,
            "semantic_span_ledger_sha256": sha256_file(contract_path),
            "teaching_column_group_ledger_sha256": sha256_file(groups_path),
            "included_source_atoms": contract["summary"]["included_source_atoms"],
            "semantic_spans": contract["summary"]["semantic_spans"],
            "teaching_groups": contract["summary"]["teaching_groups"], "open_reviews": 0,
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
        "spec04b_semantic_span_status": "passed", "full_spec04_status": "not_evaluated", "immutable_after_publication": True,
    })

    producer_mode = "formal_native" if parent["promotion_class"] == "formal_native" else "migration_compatibility"
    stage = {
        "schema_version": STAGE_SCHEMA, "stage_kind": "spec04b_semantic_span_contract", "run_id": args.run_id,
        "generated_at": now(), "status": "passed", "slice_status": "passed", "full_spec04_status": "not_evaluated",
        "producer": VERSION, "producer_mode": producer_mode,
        "commit_order": ["precommit_evidence_and_execution_capability_E", "decision_index_D", "semantic_contract_and_ledger_L", "stage_manifest_M"],
        "parent_promotion": parent,
        "execution_capability_E": {"path": "precommit/execution_capability_manifest.json", "sha256": sha256_file(capability_path), "payload_hash": capability["payload_hash"]},
        "decision_index_D": {"path": "decisions/canonical_decision_index.json", "sha256": decision_sha},
        "ledger_L": {"path": "ledgers/canonical_block_ledger.jsonl", "sha256": sha256_file(ledger_path), "payload_hash": header_out["current_ledger_hash"]},
        "semantic_span_ledger": {"path": "semantic/semantic_span_ledger.json", "sha256": sha256_file(contract_path)},
        "teaching_column_group_ledger": {"path": "semantic/teaching_column_group_ledger.json", "sha256": sha256_file(groups_path)},
        "review_queue": {"path": "semantic/semantic_review_queue.json", "sha256": sha256_file(queue_path)},
        "validation": {"path": "reports/spec04b_semantic_span_validation.json", "sha256": sha256_file(report_path)},
        "scope_prohibitions": contract["prohibitions"],
    }
    stage_path = output / "manifests/spec04b_semantic_stage_manifest.json"
    write_json(stage_path, stage)
    run_files = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "run_manifest.json":
            run_files.append({"path": path.relative_to(output).as_posix(), "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
    write_json(output / "manifests/run_manifest.json", {
        "schema_version": "immutable-run-manifest/1.1", "run_id": args.run_id, "generated_at": now(),
        "status": "passed", "stage_kind": "spec04b_semantic_span_contract", "producer_mode": producer_mode,
        "immutable_after_publication": True, "files": run_files,
    })
    return stage, 0


def validate_run(run_dir: Path) -> dict[str, Any]:
    run = run_dir.resolve()
    stage_path = run / "manifests/spec04b_semantic_stage_manifest.json"
    stage = read_json(stage_path)
    if stage.get("schema_version") != STAGE_SCHEMA or stage.get("stage_kind") != "spec04b_semantic_span_contract" or stage.get("status") != "passed":
        raise ValueError("unsupported or non-passed Spec 04-B stage manifest")
    names = ["execution_capability_E", "decision_index_D", "ledger_L", "semantic_span_ledger", "teaching_column_group_ledger", "review_queue", "validation"]
    artifacts = {}
    for name in names:
        item = stage.get(name, {})
        path = run / item.get("path", "")
        if not path.is_file() or sha256_file(path) != item.get("sha256"):
            raise ValueError(f"Spec 04-B stage artifact is missing or drifted: {name}")
        artifacts[name] = path
    execution = load_module("execution_capability.py", "execution_capability_spec04b_validate").validate_manifest(artifacts["execution_capability_E"])
    header, records = read_ledger(artifacts["ledger_L"])
    index = read_json(artifacts["decision_index_D"])
    closed_decision_index(index)
    if header.get("canonical_decision_index_hash") != sha256_file(artifacts["decision_index_D"]):
        raise ValueError("Spec 04-B ledger is not bound to decision index D")
    if stage["ledger_L"].get("payload_hash") != header.get("current_ledger_hash"):
        raise ValueError("Spec 04-B stage ledger payload hash mismatch")
    index_values = scalar_strings(index)
    forbidden = [header.get("ledger_snapshot_id"), header.get("current_ledger_hash"), sha256_file(artifacts["ledger_L"])]
    if any(item and item in index_values for item in forbidden):
        raise ValueError("Spec 04-B decision index D references descendant ledger L")
    contract = read_json(artifacts["semantic_span_ledger"])
    groups = read_json(artifacts["teaching_column_group_ledger"])
    queue = read_json(artifacts["review_queue"])
    report = read_json(artifacts["validation"])
    assert_no_downstream_keys(contract)
    assert_no_downstream_keys(groups)
    if contract.get("slice_status") != "passed" or contract.get("full_spec04_status") != "not_evaluated" or groups.get("status") != "passed" or queue.get("open_items") != 0 or report.get("status") != "passed":
        raise ValueError("Spec 04-B live artifacts are not closed and accurately scoped")
    included = {item["block_id"] for item in records if item.get("scope_status") == "included"}
    assigned = [block_id for span in contract.get("spans", []) for block_id in span.get("source_block_ids", [])]
    if set(assigned) != included or len(assigned) != len(set(assigned)):
        raise ValueError("Spec 04-B live semantic span partition is incomplete or overlapping")
    overlay = {item["block_id"]: item.get("semantic_span_id") for item in records if item.get("scope_status") == "included"}
    expected = {block_id: span["span_id"] for span in contract["spans"] for block_id in span["source_block_ids"]}
    if overlay != expected:
        raise ValueError("Spec 04-B canonical ledger overlay differs from semantic span ledger")
    run_manifest_path = run / "manifests/run_manifest.json"
    manifest = read_json(run_manifest_path)
    drift = [item["path"] for item in manifest.get("files", []) if not (run / item["path"]).is_file() or sha256_file(run / item["path"]) != item["sha256"]]
    if manifest.get("status") != "passed" or not manifest.get("immutable_after_publication") or drift:
        raise ValueError(f"Spec 04-B immutable run manifest is invalid or drifted: {drift[:8]}")
    return {
        "status": "passed", "run_id": stage["run_id"], "producer_mode": stage["producer_mode"],
        "included_source_atoms": len(included), "semantic_spans": len(contract["spans"]),
        "teaching_groups": len(groups["groups"]), "standalone_semantic_labels": len(groups["standalone_labels"]),
        "open_reviews": 0, "full_spec04_status": "not_evaluated", "producer_execution_capability": execution,
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
    produce_parser = sub.add_parser("produce")
    add_produce_arguments(produce_parser)
    validate_parser = sub.add_parser("validate-run")
    validate_parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "produce":
            result, code = produce(args)
        else:
            result, code = validate_run(args.run_dir), 0
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return code
    except Exception as exc:
        print(json.dumps({"status": "failed", "tool": VERSION, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
