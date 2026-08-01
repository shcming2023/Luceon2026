#!/usr/bin/env python3
"""Freeze a complete Spec 04 render plan from promoted 04-A/04-C and Spec 03 evidence.

The producer is deliberately mechanical: it inherits structure, semantic-group
constructs, media representations, and canonical source order.  It may serialize
payloads and output anchors, but it must not reclassify semantics, reselect a box,
reconstruct a formula/table, or emit LaTeX.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


VERSION = "spec04d-render-plan-contract/1.7.1"
STAGE_SCHEMA = "spec04d-render-plan-stage-manifest/1.0"
COMPACT_TASK_SCHEMA = "luceon.worker-v3-spec04d-compact-task/v1"
COMPACT_REVIEW_SCHEMA = "luceon.worker-v3-spec04d-compact-review/v1"
MEDIA_TYPES = {"chart", "equation", "image", "table"}
MAX_DELIVERY_ZIP_BYTES = 50_000_000
MAX_FILE_ENTITIES_EXCLUSIVE = 2_000
MAX_BODY_PART_BYTES_EXCLUSIVE = 900_000
MAX_RASTER_IMAGE_BYTES_EXCLUSIVE = 1_000_000
VOLUME_METADATA_FIELDS = {"title", "subtitle", "author", "institute", "date", "extrainfo"}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


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


def relative(base: Path, target: Path) -> str:
    return os.path.relpath(target, base).replace("\\", "/")


def load_module(filename: str, name: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_ledger(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with path.open(encoding="utf-8") as stream:
        header = json.loads(next(stream))
        records = [json.loads(line) for line in stream if line.strip()]
    if header.get("record_type") != "ledger_header" or header.get("current_ledger_hash") != canonical_hash(records):
        raise ValueError("canonical ledger identity is invalid")
    return header, records


def closed_index(index: dict[str, Any]) -> None:
    unresolved = [item.get("decision_id") for item in index.get("decisions", []) if item.get("status") in {"open", "stale", "invalidated"}]
    ids = [item.get("decision_id") for item in index.get("decisions", [])]
    if index.get("spec_status") != "passed" or unresolved or None in ids or len(ids) != len(set(ids)):
        raise ValueError(f"decision index is not uniquely closed: {unresolved[:8]}")


def record_order(record: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        int(record.get("candidate_final_order", 10**9)),
        int(record.get("pdf_physical_page", 10**9)),
        int(record.get("page_local_order", 10**9)),
        record["block_id"],
    )


def preflight_data(records: list[dict[str, Any]], media_plan: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    included = {item["block_id"]: item for item in records if item.get("scope_status") == "included"}
    closed_cover: dict[str, str] = {}
    duplicate: list[str] = []
    included_excluded: list[str] = []
    unsupported: list[str] = []
    allowed_reps = set(policy["media_constructs"])
    explicitly_unsupported = set(policy.get("unsupported_representation_types", []))
    for rep in media_plan.get("representations", []):
        ids = [value for value in rep.get("source_block_ids", []) if value in included]
        if rep.get("status") == "excluded" and ids:
            included_excluded.extend(ids)
        if rep.get("status") != "closed":
            continue
        if rep.get("representation_type") not in allowed_reps:
            unsupported.extend(ids if rep.get("representation_type") in explicitly_unsupported else ids)
        for block_id in ids:
            if block_id in closed_cover:
                duplicate.append(block_id)
            closed_cover[block_id] = rep.get("representation_id")
    required = set(policy["fragile_types_requiring_media_representation"])
    unsafe = [
        {"block_id": item["block_id"], "source_type": item.get("source_type"), "pdf_physical_page": item.get("pdf_physical_page"), "candidate_final_order": item.get("candidate_final_order"), "raw_content_sha256": item.get("raw_content_sha256")}
        for item in included.values()
        if item.get("source_type") in required and item["block_id"] not in closed_cover
    ]
    issues = []
    for code, values in (
        ("UNSAFE_FRAGILE_ATOM_LACKS_CLOSED_SPEC03_REPRESENTATION", unsafe),
        ("MEDIA_FRAGMENT_ASSIGNED_TO_MULTIPLE_REPRESENTATIONS", sorted(set(duplicate))),
        ("INCLUDED_ATOM_HAS_EXCLUDED_MEDIA_REPRESENTATION", sorted(set(included_excluded))),
        ("UNSUPPORTED_CLOSED_MEDIA_REPRESENTATION", sorted(set(unsupported))),
    ):
        if values:
            issues.append({"issue_code": code, "count": len(values), "items": values})
    return {
        "schema_version": "spec04d-preflight-report/1.0",
        "generated_at": now(),
        "status": "passed" if not issues else "failed",
        "included_source_atoms": len(included),
        "closed_media_fragments": len(closed_cover),
        "unsafe_unrepresented_fragile_atoms": len(unsafe),
        "issues": issues,
    }


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema_version") != "spec04d-render-policy/1.1":
        raise ValueError("unsupported Spec 04-D render policy")
    review = policy.get("review", {})
    if review.get("status") != "closed" or not review.get("decision_refs"):
        raise ValueError("Spec 04-D render policy review is not closed")
    if set(policy.get("fragile_types_requiring_media_representation", [])) != MEDIA_TYPES:
        raise ValueError("render policy must route all image/table/chart/equation atoms through Spec 03")
    toc = policy.get("toc_representation", {})
    if toc.get("ownership_layer") not in {"core", "profile", "book_config"} or toc.get("overflow_strategy") != "localized_depth_override":
        raise ValueError("render policy lacks a reviewed, owned TOC overflow strategy")
    level_map = toc.get("semantic_level_to_entry_type", {})
    if not level_map or len(set(level_map.values())) != len(level_map):
        raise ValueError("render policy TOC semantic-level mapping is missing or ambiguous")
    if not toc.get("decision_refs"):
        raise ValueError("TOC representation policy lacks a closed decision reference")
    structure_source_role_overrides(policy)
    pedagogical = policy.get("pedagogical_layout")
    if pedagogical is not None:
        if (
            pedagogical.get("schema_version") != "outline-pedagogical-layout-plan/1.0"
            or pedagogical.get("review", {}).get("status") != "closed"
            or pedagogical.get("review", {}).get("open_items") != 0
            or not pedagogical.get("review", {}).get("decision_refs")
        ):
            raise ValueError("pedagogical layout contract is unsupported or not closed")
        if not pedagogical.get("source_ref") or not re.fullmatch(r"[0-9a-f]{64}", str(pedagogical.get("source_sha256", ""))):
            raise ValueError("pedagogical layout source binding is absent")
        invariants = pedagogical.get("invariants", {})
        required = {
            "source_atoms_rewritten": False,
            "heading_labels_mechanically_decomposed": True,
            "question_source_labels_preserved": True,
            "response_groups_contain_text_question_atoms_only": True,
            "layout_choice_frozen_before_spec05": True,
            "toc_pollution_from_local_headings": False,
        }
        if any(invariants.get(key) is not value for key, value in required.items()):
            raise ValueError("pedagogical layout safety invariants are incomplete")
    volume = policy.get("volume_partition")
    if volume is not None:
        if volume.get("mode") not in {"single_volume", "two_volume"}:
            raise ValueError("volume_partition mode must be single_volume or two_volume")
        if volume.get("mode") == "two_volume":
            if not volume.get("decision_refs") or not volume.get("trigger_evidence"):
                raise ValueError("two-volume partition lacks closed decision refs or trigger evidence")
            if not isinstance(volume.get("non_media_file_entity_allowance"), int) or volume["non_media_file_entity_allowance"] < 1:
                raise ValueError("two-volume partition lacks a positive non-media entity allowance")
            if not isinstance(volume.get("non_media_zip_bytes_allowance"), int) or volume["non_media_zip_bytes_allowance"] < 1:
                raise ValueError("two-volume partition lacks a positive non-media byte allowance")
            definitions = volume.get("volumes")
            if not isinstance(definitions, list) or len(definitions) != 2:
                raise ValueError("two-volume partition must define exactly two volumes")
            for ordinal, item in enumerate(definitions, 1):
                if item.get("volume_id") != f"volume-{ordinal:02d}" or item.get("ordinal") != ordinal:
                    raise ValueError("two-volume definitions require ordered stable volume ids")
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", str(item.get("filename_suffix", ""))):
                    raise ValueError("unsafe volume filename suffix")
                overrides = item.get("metadata_overrides")
                if not isinstance(overrides, dict) or not overrides or not set(overrides) <= VOLUME_METADATA_FIELDS:
                    raise ValueError("volume metadata overrides are absent or outside the template metadata allowlist")
                if any(not isinstance(value, str) or not value.strip() or any(char in value for char in "\\{}") for value in overrides.values()):
                    raise ValueError("volume metadata overrides must be non-empty plain Unicode text")
                capacity = item.get("delivery_capacity_preflight")
                required_capacity = {
                    "estimated_generated_body_bytes_upper_bound",
                    "estimated_editable_text_bytes_upper_bound",
                    "largest_atomic_tex_line_bytes_upper_bound",
                    "evidence_refs",
                }
                if not isinstance(capacity, dict) or not required_capacity <= set(capacity):
                    raise ValueError("two-volume definition lacks delivery-capacity preflight evidence")
                if not capacity.get("evidence_refs"):
                    raise ValueError("two-volume delivery-capacity preflight lacks evidence refs")


def _stable_construct_binding_hash(contract: dict[str, Any]) -> str:
    return canonical_hash(
        {
            "schema_version": contract.get("schema_version"),
            "slice_status": contract.get("slice_status"),
            "bindings": contract.get("bindings"),
            "summary": contract.get("summary"),
            "template_capability_payload_hash": contract.get(
                "template_capability_payload_hash"
            ),
            "prohibitions": contract.get("prohibitions"),
        }
    )


def _deterministic_render_policy(
    *,
    outline: dict[str, Any],
    template: dict[str, Any],
) -> dict[str, Any]:
    hierarchy = outline.get("body_hierarchy")
    sectioning = template.get("constructs", {}).get("sectioning")
    standard = template.get("constructs", {}).get("standard_serialization")
    toc = template.get("toc_capability")
    if (
        not isinstance(hierarchy, list)
        or not hierarchy
        or not isinstance(sectioning, list)
        or not isinstance(standard, dict)
        or not isinstance(toc, dict)
    ):
        raise ValueError("Spec 04-D compact task lacks closed structure/template capability")
    levels = sorted({item.get("level") for item in hierarchy})
    if any(not isinstance(level, int) or level < 0 for level in levels):
        raise ValueError("Spec 04-D structure levels are unsupported")
    entry_depths = toc.get("entry_type_depths")
    if not isinstance(entry_depths, dict):
        raise ValueError("template lacks deterministic TOC entry depths")
    entry_by_depth: dict[int, str] = {}
    for entry_type, depth in entry_depths.items():
        if not isinstance(entry_type, str) or not isinstance(depth, int):
            raise ValueError("template TOC entry depth inventory is invalid")
        if depth in entry_by_depth:
            raise ValueError("template exposes ambiguous TOC entry types for one depth")
        entry_by_depth[depth] = entry_type
    level_constructs: dict[str, str] = {}
    toc_mapping: dict[str, str] = {}
    for level in levels:
        entry_type = entry_by_depth.get(level)
        construct = f"{entry_type}*" if entry_type else None
        if not entry_type or construct not in sectioning:
            raise ValueError(
                f"template cannot preserve semantic structure level {level}"
            )
        level_constructs[str(level)] = construct
        toc_mapping[str(level)] = entry_type
    ranked_starred = sorted(
        (
            (entry_depths.get(item[:-1], -1), item)
            for item in sectioning
            if isinstance(item, str) and item.endswith("*")
        ),
        reverse=True,
    )
    local_heading = ranked_starred[0][1] if ranked_starred else "paragraph"
    if "paragraph" not in standard:
        raise ValueError("template lacks the deterministic paragraph construct")
    media_constructs: dict[str, str] = {}
    for representation, construct in (
        ("source_asset_image", "source_asset_image"),
        ("source_region_image", "source_region_image"),
        ("structured_formula", "display_math"),
    ):
        if construct in standard:
            media_constructs[representation] = construct
    if not {"source_asset_image", "source_region_image"} <= set(media_constructs):
        raise ValueError("template lacks required source-image serialization")
    return {
        "schema_version": "spec04d-render-policy/1.1",
        "ownership_layer": "profile",
        "structure_level_constructs": level_constructs,
        "toc_representation": {
            "ownership_layer": "core",
            "semantic_level_to_entry_type": toc_mapping,
            "overflow_strategy": "localized_depth_override",
        },
        "local_heading_construct": local_heading,
        "plain_body_construct": "paragraph",
        "safe_textual_fragile_types": [
            "image_caption",
            "image_footnote",
            "page_footnote",
            "table_caption",
        ],
        "fragile_types_requiring_media_representation": sorted(MEDIA_TYPES),
        "media_constructs": media_constructs,
        "source_image_layout": {
            "minimum_width_fraction": 0.25,
            "maximum_width_fraction": 0.9,
            "max_height_fraction": 0.72,
            "alignment": "center",
        },
        "unsupported_representation_types": [
            "structured_chart",
            "structured_table",
            "structured_vector",
        ],
        "structure_media_collision_rule": (
            "virtual_source_supported_structure_node_and_single_media_atom_output"
        ),
        "prohibitions": [
            "semantic_reclassification",
            "construct_reselection",
            "content_rewriting",
            "formula_reconstruction",
            "table_reconstruction",
            "latex_generation",
        ],
    }


def _compact_review_result(
    task_id: str,
    review_tasks: list[dict[str, Any]],
    *,
    use_last_option: bool,
) -> dict[str, Any]:
    return {
        "schema_version": COMPACT_REVIEW_SCHEMA,
        "task_id": task_id,
        "review_status": "closed",
        "decisions": [
            {
                "task_id": item["task_id"],
                "selected_option_id": (
                    item["options"][-1 if use_last_option else 0]["option_id"]
                ),
            }
            for item in review_tasks
        ],
        "open_reviews": [],
    }


def render_policy_review_task(
    *,
    records: list[dict[str, Any]],
    ledger_payload_hash: str,
    outline: dict[str, Any],
    final_toc: dict[str, Any],
    construct_binding: dict[str, Any],
    template: dict[str, Any],
    media_plan: dict[str, Any],
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", ledger_payload_hash):
        raise ValueError("Spec 04-D ledger payload hash is invalid")
    if (
        outline.get("slice_status") != "passed"
        or outline.get("summary", {}).get("open_reviews") != 0
        or final_toc.get("status") != "passed"
        or final_toc.get("open_reviews") != 0
        or construct_binding.get("slice_status") != "passed"
        or construct_binding.get("summary", {}).get("open_reviews") != 0
        or media_plan.get("spec_status") != "passed"
        or media_plan.get("open_reviews") != 0
    ):
        raise ValueError("Spec 04-D compact inputs are not closed and passed")
    hierarchy = outline.get("body_hierarchy")
    toc_entries = final_toc.get("entries")
    if not isinstance(hierarchy, list) or not isinstance(toc_entries, list):
        raise ValueError("Spec 04-D compact inputs lack structure evidence")
    expected_toc = [
        {
            "level": item.get("level"),
            "node_id": item.get("node_id"),
            "source_order": item.get("source_order_start"),
            "source_toc_entry_ids": item.get("source_toc_entry_ids", []),
            "title": item.get("final_toc", {}).get("title"),
            "toc_entry_id": f"toc::{item.get('node_id')}",
        }
        for item in hierarchy
        if item.get("final_toc", {}).get("include") is True
    ]
    if expected_toc != toc_entries:
        raise ValueError("Spec 04-D final TOC differs from the frozen hierarchy")
    template_payload_hash = template.get("capability_payload_hash")
    if not isinstance(template_payload_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}", template_payload_hash
    ):
        raise ValueError("Spec 04-D template capability payload hash is invalid")
    if (
        construct_binding.get("template_capability_payload_hash")
        != template_payload_hash
    ):
        raise ValueError("Spec 04-D construct binding/template capability drift")
    included = {
        item["block_id"]: item
        for item in records
        if item.get("scope_status") == "included"
    }
    review_tasks: list[dict[str, Any]] = []
    for structure in hierarchy:
        node_id = structure.get("node_id")
        heading_ids = structure.get("heading_evidence_block_ids") or [
            structure.get("anchor_block_id")
        ]
        for block_id in heading_ids:
            record = included.get(block_id)
            if record is None or record.get("source_type") in MEDIA_TYPES:
                continue
            if (
                record.get("source_type") == "title"
                or record.get("source_label") == "title"
            ):
                continue
            candidate_index = len(review_tasks)
            raw_content = str(record.get("raw_content") or "")
            review_tasks.append(
                {
                    "candidate_index": candidate_index,
                    "task_id": f"structure-source-role:{candidate_index:04d}",
                    "structure_node_id": node_id,
                    "block_id": block_id,
                    "outline_title": structure.get("final_toc", {}).get("title"),
                    "source_type": record.get("source_type"),
                    "source_label": record.get("source_label"),
                    "source_excerpt": raw_content[:2_000],
                    "source_excerpt_truncated": len(raw_content) > 2_000,
                    "raw_content_sha256": record.get("raw_content_sha256"),
                    "options": [
                        {
                            "option_id": "option-0000",
                            "role": "title_fragment",
                            "reason": (
                                "The source atom is visible title text supporting "
                                "the frozen outline title."
                            ),
                        },
                        {
                            "option_id": "option-0001",
                            "role": "post_heading_body",
                            "reason": (
                                "The source atom is body content following the "
                                "frozen outline heading."
                            ),
                        },
                    ],
                }
            )
    if len(review_tasks) > 256:
        raise ValueError("Spec 04-D compact review exceeds 256 bounded decisions")
    baseline = _deterministic_render_policy(outline=outline, template=template)
    bindings = {
        "ledger_payload_hash": ledger_payload_hash,
        "outline_hierarchy_payload_hash": canonical_hash(hierarchy),
        "final_toc_payload_hash": canonical_hash(toc_entries),
        "construct_binding_payload_hash": _stable_construct_binding_hash(
            construct_binding
        ),
        "template_capability_payload_hash": template_payload_hash,
        "media_representation_payload_hash": canonical_hash(
            media_plan.get("representations", [])
        ),
    }
    identity = {
        "bindings": bindings,
        "deterministic_policy": baseline,
        "review_tasks": review_tasks,
    }
    task_id = "spec04d-compact-" + canonical_hash(identity)[:24]
    minimum = len(
        canonical_bytes(
            _compact_review_result(
                task_id, review_tasks, use_last_option=False
            )
        )
    )
    maximum = len(
        canonical_bytes(
            _compact_review_result(
                task_id, review_tasks, use_last_option=True
            )
        )
    )
    return {
        "schema_version": COMPACT_TASK_SCHEMA,
        "task_id": task_id,
        "bindings": bindings,
        "deterministic_policy": baseline,
        "candidate_count": len(review_tasks),
        "review_tasks": review_tasks,
        "capacity": {
            "minimum_response_bytes": min(minimum, maximum),
            "maximum_response_bytes": max(minimum, maximum),
        },
        "instructions": [
            "Return exactly one selected_option_id for every task_id in supplied order.",
            "Select only an option_id declared on the same review task.",
            "Do not emit render policy, LaTeX, template edits, or source rewrites.",
        ],
    }


def project_policy_review(
    task: dict[str, Any],
    compact_review: dict[str, Any],
) -> dict[str, Any]:
    expected_task_fields = {
        "schema_version",
        "task_id",
        "bindings",
        "deterministic_policy",
        "candidate_count",
        "review_tasks",
        "capacity",
        "instructions",
    }
    if (
        set(task) != expected_task_fields
        or task.get("schema_version") != COMPACT_TASK_SCHEMA
    ):
        raise ValueError("Spec 04-D compact task is unsupported or drifted")
    if (
        set(compact_review)
        != {
            "schema_version",
            "task_id",
            "review_status",
            "decisions",
            "open_reviews",
        }
        or compact_review.get("schema_version") != COMPACT_REVIEW_SCHEMA
        or compact_review.get("task_id") != task.get("task_id")
        or compact_review.get("review_status") != "closed"
        or compact_review.get("open_reviews") != []
    ):
        raise ValueError("Spec 04-D compact review is open, unsupported, or drifted")
    review_tasks = task.get("review_tasks")
    decisions = compact_review.get("decisions")
    if (
        not isinstance(review_tasks, list)
        or not isinstance(decisions, list)
        or len(review_tasks) != task.get("candidate_count")
        or len(decisions) != len(review_tasks)
    ):
        raise ValueError("Spec 04-D decisions must be complete, ordered, and total")
    overrides: list[dict[str, Any]] = []
    for candidate_index, (review_task, decision) in enumerate(
        zip(review_tasks, decisions)
    ):
        if (
            not isinstance(review_task, dict)
            or review_task.get("candidate_index") != candidate_index
            or not isinstance(decision, dict)
            or set(decision) != {"task_id", "selected_option_id"}
            or decision.get("task_id") != review_task.get("task_id")
        ):
            raise ValueError("Spec 04-D decisions must be complete, ordered, and total")
        selected = [
            option
            for option in review_task.get("options", [])
            if isinstance(option, dict)
            and option.get("option_id") == decision.get("selected_option_id")
        ]
        if len(selected) != 1:
            raise ValueError("Spec 04-D decisions must be complete, ordered, and in-set")
        option = selected[0]
        overrides.append(
            {
                "structure_node_id": review_task["structure_node_id"],
                "block_id": review_task["block_id"],
                "role": option["role"],
                "decision_refs": [
                    f"{review_task['task_id']}::{option['option_id']}"
                ],
                "reason": option["reason"],
            }
        )
    compact_hash = canonical_hash(compact_review)
    policy = copy.deepcopy(task.get("deterministic_policy"))
    if not isinstance(policy, dict):
        raise ValueError("Spec 04-D compact task lacks deterministic policy")
    decision_ref = f"compact-review::{compact_hash}"
    policy.update(
        {
            "policy_id": str(task["task_id"]),
            "review": {
                "status": "closed",
                "decision_refs": [decision_ref],
                "basis": (
                    "Release kernel projected a total bounded review over the "
                    "frozen source-role candidates."
                ),
            },
            "structure_source_role_overrides": overrides,
        }
    )
    policy["toc_representation"]["decision_refs"] = [decision_ref]
    validate_policy(policy)
    return policy


def validate_pedagogical_layout_binding(
    policy: dict[str, Any],
    parent_ledger: Path,
    header: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    embedded = policy.get("pedagogical_layout")
    if embedded is None:
        return None
    source_path = Path(embedded["source_ref"]).resolve()
    if not source_path.is_file() or sha256_file(source_path) != embedded["source_sha256"]:
        raise ValueError("pedagogical layout source plan is absent or drifted")
    source = read_json(source_path)
    source_payload = {key: value for key, value in source.items() if key not in {"generated_at", "deterministic_payload_hash"}}
    if (
        source.get("schema_version") != "outline-pedagogical-layout-plan/1.0"
        or source.get("status") != "passed"
        or source.get("deterministic_payload_hash") != canonical_hash(source_payload)
        or source.get("deterministic_payload_hash") != embedded.get("deterministic_payload_hash")
    ):
        raise ValueError("pedagogical layout source plan identity is invalid")
    binding = source.get("source_ledger_binding", {})
    if (
        binding.get("sha256") != sha256_file(parent_ledger)
        or binding.get("ledger_snapshot_id") != header.get("ledger_snapshot_id")
        or binding.get("ledger_payload_hash") != header.get("current_ledger_hash")
    ):
        raise ValueError("pedagogical layout plan is not bound to the exact Spec 04-D parent ledger")
    for key in ("heading_presentations", "numbering_map", "response_groups", "review", "invariants"):
        if embedded.get(key) != source.get(key):
            raise ValueError(f"embedded pedagogical layout differs from source plan: {key}")

    included = {item["block_id"]: item for item in records if item.get("scope_status") == "included"}
    heading_ids = [item.get("block_id") for item in embedded.get("heading_presentations", [])]
    if None in heading_ids or len(heading_ids) != len(set(heading_ids)) or any(value not in included for value in heading_ids):
        raise ValueError("pedagogical heading plan contains duplicate or unknown source atoms")
    response_ids: list[str] = []
    group_ids: list[str] = []
    for group in embedded.get("response_groups", []):
        group_ids.append(group.get("group_id"))
        ids = group.get("source_block_ids", [])
        if group.get("columns") not in {1, 2} or not ids or ids != [item.get("block_id") for item in group.get("items", [])]:
            raise ValueError(f"invalid pedagogical response group: {group.get('group_id')}")
        if group.get("columns") == 2 and len(ids) < 2:
            raise ValueError(f"two-column pedagogical response group requires at least two independently ordered atoms: {group.get('group_id')}")
        answer = group.get("answer_space", {})
        if answer.get("mode") not in {"inline_rule", "vertical_space"} or not 0 < float(answer.get("rule_width_fraction", 0)) <= 0.9 or not 1 <= int(answer.get("vertical_space_baselines", 0)) <= 12:
            raise ValueError(f"unsafe pedagogical answer-space contract: {group.get('group_id')}")
        for item in group["items"]:
            record = included.get(item["block_id"])
            if (
                record is None
                or record.get("source_type") != "text"
                or item.get("source_text") != record.get("raw_content")
                or item.get("source_text_sha256") != record.get("raw_content_sha256")
            ):
                raise ValueError(f"pedagogical response item is not byte-bound source text: {item.get('block_id')}")
        response_ids.extend(ids)
    if None in group_ids or len(group_ids) != len(set(group_ids)) or len(response_ids) != len(set(response_ids)):
        raise ValueError("pedagogical response groups or source atoms are duplicated")
    return embedded


def _volume_payload_hash(value: dict[str, Any]) -> str:
    return canonical_hash({key: item for key, item in value.items() if key not in {"generated_at", "deterministic_payload_hash"}})


def validate_volume_partition_plan(partition: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, Any]:
    if partition.get("schema_version") not in {"volume-partition-plan/1.0", "volume-partition-plan/1.1", "volume-partition-plan/1.2"} or partition.get("status") != "passed":
        raise ValueError("unsupported or non-passed volume partition plan")
    if partition.get("deterministic_payload_hash") != _volume_payload_hash(partition):
        raise ValueError("volume partition deterministic payload hash mismatch")
    volumes = partition.get("volumes")
    if not isinstance(volumes, list) or len(volumes) not in {1, 2}:
        raise ValueError("delivery set cardinality must be one or two")
    if partition.get("mode") != ("single_volume" if len(volumes) == 1 else "two_volume"):
        raise ValueError("volume partition mode/cardinality mismatch")
    expected_nodes = [item["render_node_id"] for item in nodes]
    expected_sources = [block_id for item in nodes for block_id in item.get("source_block_ids", [])]
    actual_nodes: list[str] = []
    actual_sources: list[str] = []
    previous_end = 0
    anchor_volume: dict[str, str] = {}
    node_volume: dict[str, str] = {}
    schema_version = partition.get("schema_version")
    if schema_version not in {"volume-partition-plan/1.1", "volume-partition-plan/1.2"}:
        raise ValueError("unsupported volume partition plan schema")
    for ordinal, volume in enumerate(volumes, 1):
        if volume.get("ordinal") != ordinal or volume.get("volume_id") != f"volume-{ordinal:02d}":
            raise ValueError("volume ids/ordinals are unstable")
        start, end = volume.get("render_order_start"), volume.get("render_order_end")
        if start != previous_end + 1 or not isinstance(end, int) or end < start:
            raise ValueError("volume render ranges are not contiguous")
        selected = nodes[start - 1:end]
        selected_ids = [item["render_node_id"] for item in selected]
        selected_sources = [block_id for item in selected for block_id in item.get("source_block_ids", [])]
        if selected_ids != volume.get("render_node_ids") or selected_sources != volume.get("source_block_ids"):
            raise ValueError("volume membership differs from its frozen render range")
        if volume.get("first_render_node_id") != selected_ids[0] or volume.get("last_render_node_id") != selected_ids[-1]:
            raise ValueError("volume boundary node identity mismatch")
        for item in selected:
            node_volume[item["render_node_id"]] = volume["volume_id"]
            anchor_volume[item["output_anchor_id"]] = volume["volume_id"]
        actual_nodes.extend(selected_ids)
        actual_sources.extend(selected_sources)
        previous_end = end
        if schema_version == "volume-partition-plan/1.2":
            units = volume.get("body_units")
            if not isinstance(units, list) or not units:
                raise ValueError("volume lacks frozen semantic body units")
            unit_nodes: list[str] = []
            unit_sources: list[str] = []
            expected_start = start
            for unit_ordinal, unit in enumerate(units, 1):
                if unit.get("ordinal") != unit_ordinal or unit.get("unit_id") != f"unit-{unit_ordinal:04d}":
                    raise ValueError("body unit ids/ordinals are unstable")
                unit_start, unit_end = unit.get("render_order_start"), unit.get("render_order_end")
                if unit_start != expected_start or not isinstance(unit_end, int) or unit_end < unit_start or unit_end > end:
                    raise ValueError("body unit ranges are not contiguous within the volume")
                unit_selected = nodes[unit_start - 1:unit_end]
                ids = [item["render_node_id"] for item in unit_selected]
                sources = [block_id for item in unit_selected for block_id in item.get("source_block_ids", [])]
                if ids != unit.get("render_node_ids") or sources != unit.get("source_block_ids"):
                    raise ValueError("body unit membership differs from its frozen render range")
                if unit.get("boundary_kind") == "source_top_level_structure":
                    first = unit_selected[0]
                    if first.get("node_kind") != "book_structure" or first.get("parent_output_anchor_id") is not None:
                        raise ValueError("body unit does not start at a source-supported top-level structure")
                    if unit.get("source_anchor_id") != first.get("output_anchor_id"):
                        raise ValueError("body unit source anchor identity drift")
                elif unit.get("boundary_kind") != "leading_body":
                    raise ValueError("unsupported body unit boundary kind")
                unit_nodes.extend(ids)
                unit_sources.extend(sources)
                expected_start = unit_end + 1
            if expected_start != end + 1 or unit_nodes != selected_ids or unit_sources != selected_sources:
                raise ValueError("body units do not exactly cover the volume")
            budget = volume.get("budget_estimate", {})
            if budget.get("capacity_preflight_status") == "measured":
                if budget.get("estimated_max_body_part_bytes_after_sharding", MAX_BODY_PART_BYTES_EXCLUSIVE) >= MAX_BODY_PART_BYTES_EXCLUSIVE:
                    raise ValueError("volume estimated body part does not satisfy the 900K limit")
                if budget.get("largest_atomic_tex_line_bytes_upper_bound", MAX_BODY_PART_BYTES_EXCLUSIVE) >= MAX_BODY_PART_BYTES_EXCLUSIVE:
                    raise ValueError("volume contains an atomic TeX line that cannot be safely sharded")
            if budget.get("largest_raster_image_bytes", MAX_RASTER_IMAGE_BYTES_EXCLUSIVE) >= MAX_RASTER_IMAGE_BYTES_EXCLUSIVE:
                raise ValueError("volume contains a raster image at or above the 1 MB delivery limit")
    if actual_nodes != expected_nodes or len(actual_nodes) != len(set(actual_nodes)):
        raise ValueError("cross-volume render-node coverage is not exact")
    if actual_sources != expected_sources or len(actual_sources) != len(set(actual_sources)):
        raise ValueError("cross-volume source-atom coverage is not exact")
    cross_parent = [
        item["render_node_id"] for item in nodes
        if item.get("parent_output_anchor_id")
        and anchor_volume.get(item["parent_output_anchor_id"]) != node_volume[item["render_node_id"]]
    ]
    if cross_parent:
        raise ValueError(f"volume cut crosses parent-anchor dependencies: {cross_parent[:8]}")
    if len(volumes) == 2:
        first_second = nodes[volumes[1]["render_order_start"] - 1]
        boundary = partition.get("boundary", {})
        if boundary.get("before_render_node_id") != first_second["render_node_id"]:
            raise ValueError("two-volume boundary does not bind the first node of volume two")
        if first_second.get("node_kind") != "book_structure" or first_second.get("parent_output_anchor_id") is not None:
            raise ValueError("two-volume cut is not a top-level source-supported structure boundary")
        for volume in volumes:
            budget = volume.get("budget_estimate", {})
            if budget.get("estimated_file_entities", MAX_FILE_ENTITIES_EXCLUSIVE) >= MAX_FILE_ENTITIES_EXCLUSIVE:
                raise ValueError("volume estimated file entities do not satisfy the unchanged limit")
            if budget.get("estimated_zip_bytes_upper_bound", MAX_DELIVERY_ZIP_BYTES) >= MAX_DELIVERY_ZIP_BYTES:
                raise ValueError("volume estimated byte upper bound does not satisfy the unchanged limit")
            if schema_version == "volume-partition-plan/1.1":
                if budget.get("capacity_preflight_status") != "measured":
                    raise ValueError("two-volume plan lacks measured text-capacity preflight")
                if budget.get("estimated_max_tex_file_bytes_after_sharding", 2_000_000) >= 2_000_000:
                    raise ValueError("volume estimated TeX shard does not satisfy the editable-file limit")
                if budget.get("estimated_editable_text_bytes_upper_bound", 7_000_000) >= 7_000_000:
                    raise ValueError("volume estimated editable text does not satisfy the project limit")
                if budget.get("largest_atomic_tex_line_bytes_upper_bound", 2_000_000) >= 2_000_000:
                    raise ValueError("volume contains an estimated atomic TeX line that cannot be safely sharded")
    return {
        "mode": partition["mode"], "volumes": len(volumes), "render_nodes": len(actual_nodes),
        "source_atoms": len(actual_sources), "cross_volume_parent_dependencies": len(cross_parent),
    }


def build_volume_partition_plan(nodes: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(policy.get("volume_partition") or {"mode": "single_volume"})
    mode = config["mode"]
    if mode == "single_volume":
        definitions = [{
            "volume_id": "volume-01", "ordinal": 1, "label": None, "filename_suffix": "",
            "metadata_overrides": {},
        }]
        ranges = [(1, len(nodes))]
        boundary = None
        trigger = {"reason_code": "single_volume_default", "evidence": [], "decision_refs": []}
    else:
        before_id = config.get("boundary", {}).get("before_render_node_id")
        indexes = [index for index, item in enumerate(nodes) if item["render_node_id"] == before_id]
        if len(indexes) != 1 or indexes[0] == 0:
            raise ValueError("two-volume boundary node is absent or cannot begin volume two")
        cut = indexes[0]
        definitions = config["volumes"]
        ranges = [(1, cut), (cut + 1, len(nodes))]
        boundary = {
            **copy.deepcopy(config["boundary"]),
            "after_render_node_id": nodes[cut - 1]["render_node_id"],
            "before_render_node_id": nodes[cut]["render_node_id"],
            "render_order_after": cut,
            "render_order_before": cut + 1,
        }
        trigger = {
            "reason_code": config.get("trigger_reason_code", "single_volume_delivery_limit_exceeded"),
            "evidence": copy.deepcopy(config["trigger_evidence"]),
            "decision_refs": sorted(set(config["decision_refs"])),
        }
    def body_units_for_range(start: int, end: int) -> list[dict[str, Any]]:
        selected = nodes[start - 1:end]
        local_starts = [
            index for index, item in enumerate(selected)
            if item.get("node_kind") == "book_structure" and item.get("parent_output_anchor_id") is None
        ]
        starts = ([0] if not local_starts or local_starts[0] != 0 else []) + local_starts
        starts = sorted(set(starts))
        result: list[dict[str, Any]] = []
        for ordinal, local_start in enumerate(starts, 1):
            local_end = starts[ordinal] - 1 if ordinal < len(starts) else len(selected) - 1
            unit_nodes = selected[local_start:local_end + 1]
            first = unit_nodes[0]
            boundary_kind = (
                "source_top_level_structure"
                if first.get("node_kind") == "book_structure" and first.get("parent_output_anchor_id") is None
                else "leading_body"
            )
            result.append({
                "unit_id": f"unit-{ordinal:04d}",
                "ordinal": ordinal,
                "boundary_kind": boundary_kind,
                "source_anchor_id": first.get("output_anchor_id") if boundary_kind == "source_top_level_structure" else None,
                "render_order_start": start + local_start,
                "render_order_end": start + local_end,
                "render_node_ids": [item["render_node_id"] for item in unit_nodes],
                "source_block_ids": [block_id for item in unit_nodes for block_id in item.get("source_block_ids", [])],
            })
        return result

    volumes = []
    for definition, (start, end) in zip(definitions, ranges):
        selected = nodes[start - 1:end]
        source_ids = [block_id for item in selected for block_id in item.get("source_block_ids", [])]
        media_assets: dict[str, tuple[int, str]] = {}
        for item in selected:
            binding = item.get("media_binding") or item.get("payload", {}).get("media_binding")
            payload = item.get("payload", {})
            asset_size = payload.get("asset_size_bytes")
            asset_ref = payload.get("asset_ref")
            if binding and isinstance(asset_size, int) and asset_size >= 0 and isinstance(asset_ref, str):
                media_assets.setdefault(
                    binding["artifact_sha256"],
                    (asset_size, Path(asset_ref).suffix.lower()),
                )
        media_bytes = sum(size for size, _ in media_assets.values())
        largest_raster_image_bytes = max(
            (
                size for size, suffix in media_assets.values()
                if suffix in {".jpg", ".jpeg", ".png"}
            ),
            default=0,
        )
        allowance_entities = int(config.get("non_media_file_entity_allowance", 3))
        allowance_bytes = int(config.get("non_media_zip_bytes_allowance", 1_000_000))
        capacity = definition.get("delivery_capacity_preflight") or config.get("delivery_capacity_preflight")
        capacity_status = "measured" if isinstance(capacity, dict) else "deferred_to_spec05_exact"
        if capacity_status == "measured":
            body_bytes = int(capacity["estimated_generated_body_bytes_upper_bound"])
            editable_bytes = int(capacity.get("estimated_editable_text_bytes_upper_bound", body_bytes))
            atomic_line_bytes = int(capacity["largest_atomic_tex_line_bytes_upper_bound"])
            evidence_refs = copy.deepcopy(capacity["evidence_refs"])
            if min(body_bytes, editable_bytes, atomic_line_bytes) < 0 or not evidence_refs:
                raise ValueError("invalid delivery-capacity preflight values")
            unit_count = len(body_units_for_range(start, end))
            if body_bytes < MAX_BODY_PART_BYTES_EXCLUSIVE:
                shard_count = max(1, unit_count)
                transport_entities = 1 + shard_count
                max_tex_bytes = body_bytes
            else:
                shard_count = max(unit_count, (body_bytes + MAX_BODY_PART_BYTES_EXCLUSIVE - 2) // (MAX_BODY_PART_BYTES_EXCLUSIVE - 1))
                transport_entities = 1 + shard_count
                max_tex_bytes = MAX_BODY_PART_BYTES_EXCLUSIVE - 1
        else:
            body_bytes = editable_bytes = atomic_line_bytes = max_tex_bytes = None
            evidence_refs = []
            shard_count = transport_entities = None
        volumes.append({
            **copy.deepcopy(definition),
            "render_order_start": start, "render_order_end": end,
            "first_render_node_id": selected[0]["render_node_id"], "last_render_node_id": selected[-1]["render_node_id"],
            "render_node_ids": [item["render_node_id"] for item in selected], "source_block_ids": source_ids,
            "body_units": body_units_for_range(start, end),
            "budget_estimate": {
                "unique_media_assets": len(media_assets), "source_media_bytes": media_bytes,
                "non_media_file_entity_allowance": allowance_entities,
                "non_media_zip_bytes_allowance": allowance_bytes,
                "estimated_body_transport_file_entities": transport_entities,
                "estimated_tex_shard_count": shard_count,
                "estimated_generated_body_bytes_upper_bound": body_bytes,
                "estimated_max_body_part_bytes_after_sharding": max_tex_bytes,
                "estimated_editable_text_bytes_upper_bound": editable_bytes,
                "largest_atomic_tex_line_bytes_upper_bound": atomic_line_bytes,
                "largest_raster_image_bytes": largest_raster_image_bytes,
                "capacity_preflight_status": capacity_status,
                "capacity_evidence_refs": evidence_refs,
                    "estimated_file_entities": len(media_assets) + allowance_entities + (transport_entities or 0),
                "estimated_zip_bytes_upper_bound": media_bytes + allowance_bytes + (editable_bytes or 0),
                "file_entity_limit_exclusive": MAX_FILE_ENTITIES_EXCLUSIVE,
                "zip_byte_limit_exclusive": MAX_DELIVERY_ZIP_BYTES,
                "body_part_byte_limit_exclusive": MAX_BODY_PART_BYTES_EXCLUSIVE,
                "raster_image_byte_limit_exclusive": MAX_RASTER_IMAGE_BYTES_EXCLUSIVE,
            },
        })
    result = {
        "schema_version": "volume-partition-plan/1.2", "generated_at": now(), "status": "passed", "mode": mode,
        "selection_authority": "spec04d_frozen_semantic_boundary", "max_volumes": 2,
        "single_volume_preferred": True, "trigger": trigger, "boundary": boundary, "volumes": volumes,
        "body_transport_contract": {
            "root_entry": "main.tex",
            "loader": "body/generated-body.tex",
            "leaf_pattern": "body/units/unit-NNNN/part-NNNN.tex",
            "body_part_byte_limit_exclusive": MAX_BODY_PART_BYTES_EXCLUSIVE,
            "semantic_unit_authority": "source_supported_top_level_structure",
        },
        "cross_volume_contract": {
            "render_nodes_exactly_once": True, "source_atoms_exactly_once": True,
            "source_order_contiguous": True, "cross_volume_parent_dependencies": 0,
            "template_framework_duplication_only": True,
        },
    }
    result["deterministic_payload_hash"] = _volume_payload_hash(result)
    validate_volume_partition_plan(result, nodes)
    return result


def structure_source_role_overrides(policy: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for item in policy.get("structure_source_role_overrides", []):
        key = (item.get("structure_node_id"), item.get("block_id"))
        if None in key or key in result:
            raise ValueError("structure source-role overrides have missing or duplicate applicability")
        if item.get("role") not in {"title_fragment", "post_heading_body"}:
            raise ValueError(f"unsupported structure source role: {item.get('role')}")
        if not item.get("decision_refs") or not item.get("reason"):
            raise ValueError(f"structure source-role override lacks a closed decision or reason: {key}")
        result[key] = item
    return result


def classify_structure_source_roles(
    outline: dict[str, Any], included: dict[str, dict[str, Any]], media_source_ids: set[str], policy: dict[str, Any],
) -> dict[str, dict[str, list[str]]]:
    overrides = structure_source_role_overrides(policy)
    used_overrides: set[tuple[str, str]] = set()
    result: dict[str, dict[str, list[str]]] = {}
    for structure in outline.get("body_hierarchy", []):
        node_id = structure["node_id"]
        heading_evidence_ids = structure.get("heading_evidence_block_ids") or [structure["anchor_block_id"]]
        title_ids: list[str] = []
        post_body_ids: list[str] = []
        media_ids: list[str] = []
        for block_id in heading_evidence_ids:
            if block_id not in included:
                continue
            if block_id in media_source_ids:
                media_ids.append(block_id)
                continue
            record = included[block_id]
            key = (node_id, block_id)
            override = overrides.get(key)
            if override:
                used_overrides.add(key)
                role = override["role"]
            elif record.get("source_type") == "title" or record.get("source_label") == "title":
                role = "title_fragment"
            else:
                raise ValueError(
                    "AMBIGUOUS_STRUCTURE_SOURCE_ROLE: non-title heading evidence requires an explicit "
                    f"title_fragment/post_heading_body decision: {node_id} {block_id}"
                )
            (title_ids if role == "title_fragment" else post_body_ids).append(block_id)
        anchor_id = structure["anchor_block_id"]
        if anchor_id in included and anchor_id not in media_source_ids and anchor_id not in heading_evidence_ids:
            post_body_ids.append(anchor_id)
        elif anchor_id in media_source_ids and anchor_id not in media_ids:
            media_ids.append(anchor_id)
        result[node_id] = {
            "title_fragment_block_ids": list(dict.fromkeys(title_ids)),
            "post_heading_body_block_ids": list(dict.fromkeys(post_body_ids)),
            "media_evidence_block_ids": sorted(set(media_ids)),
        }
    unused = sorted(set(overrides) - used_overrides)
    if unused:
        raise ValueError(f"structure source-role overrides do not match active included heading evidence: {unused[:8]}")
    return result


def validate_structure_source_integrity(
    nodes: list[dict[str, Any]], outline: dict[str, Any], records: list[dict[str, Any]], policy: dict[str, Any],
) -> dict[str, Any]:
    included = {item["block_id"]: item for item in records if item.get("scope_status") == "included"}
    media_ids = {value for node in nodes if node.get("node_kind") == "media" for value in node.get("source_block_ids", [])}
    roles = classify_structure_source_roles(outline, included, media_ids, policy)
    covered_by: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        for block_id in node.get("source_block_ids", []):
            covered_by.setdefault(block_id, []).append(node)
    structure_nodes = {
        node.get("payload", {}).get("structure_node_id"): node
        for node in nodes if node.get("node_kind") == "book_structure"
    }
    post_body_count = 0
    title_fragment_count = 0
    for structure in outline.get("body_hierarchy", []):
        node_id = structure["node_id"]
        node = structure_nodes.get(node_id)
        if not node:
            raise ValueError(f"structure render node is absent: {node_id}")
        expected = roles[node_id]
        expected_titles = expected["title_fragment_block_ids"]
        expected_body = expected["post_heading_body_block_ids"]
        actual_titles = [item.get("block_id") for item in node.get("payload", {}).get("title_source_fragments", [])]
        if node.get("source_block_ids") != expected_titles or actual_titles != expected_titles:
            raise ValueError(f"structure title fragments differ from the reviewed source roles: {node_id}")
        if node.get("payload", {}).get("separate_body_block_ids", []) != expected_body:
            raise ValueError(f"structure post-heading body references differ from the reviewed source roles: {node_id}")
        if node.get("payload", {}).get("title") != structure.get("final_toc", {}).get("title"):
            raise ValueError(f"structure render title differs from the frozen final TOC: {node_id}")
        for block_id in expected_titles:
            if covered_by.get(block_id) != [node]:
                raise ValueError(f"structure title fragment is not covered exactly once by its structure node: {block_id}")
        for block_id in expected_body:
            matches = covered_by.get(block_id, [])
            if len(matches) != 1:
                raise ValueError(f"post-heading body atom is not covered exactly once: {block_id}")
            body_node = matches[0]
            record = included[block_id]
            payload = body_node.get("payload", {})
            structure_body_valid = (
                body_node.get("node_kind") == "structure_body"
                and body_node.get("target_construct") == policy["plain_body_construct"]
                and body_node.get("source_block_ids") == [block_id]
                and payload.get("structure_node_id") == node_id
                and payload.get("raw_content") == record.get("raw_content")
                and payload.get("raw_content_sha256") == record.get("raw_content_sha256")
            )
            response_items = {
                item.get("block_id"): item
                for item in payload.get("items", [])
                if isinstance(item, dict)
            }
            response_item = response_items.get(block_id)
            response_body_valid = (
                body_node.get("node_kind") == "response_list"
                and body_node.get("target_construct") == "response_list"
                and response_item is not None
                and response_item.get("source_text") == record.get("raw_content")
                and response_item.get("source_text_sha256") == record.get("raw_content_sha256")
            )
            if not (structure_body_valid or response_body_valid):
                raise ValueError(f"post-heading body atom was changed or misclassified as a title: {block_id}")
        title_fragment_count += len(expected_titles)
        post_body_count += len(expected_body)
    return {
        "structure_nodes": len(outline.get("body_hierarchy", [])),
        "title_fragment_atoms": title_fragment_count,
        "post_heading_body_atoms": post_body_count,
        "reviewed_role_overrides": len(structure_source_role_overrides(policy)),
    }


def toc_capability_binding(capability: dict[str, Any]) -> dict[str, Any]:
    toc = capability.get("toc_capability")
    if not isinstance(toc, dict):
        raise ValueError("template capability manifest lacks TOC capability evidence")
    return {
        "template_capability_payload_hash": capability.get("capability_payload_hash"),
        "effective_tocdepth": toc.get("effective_tocdepth"),
        "effective_tocdepth_status": toc.get("effective_tocdepth_status"),
        "entry_type_depths": copy.deepcopy(toc.get("entry_type_depths", {})),
        "native_visible_entry_types": list(toc.get("native_visible_entry_types", [])),
        "serialization_strategies": copy.deepcopy(toc.get("serialization_strategies", {})),
    }


def structure_toc_parameters(
    structure: dict[str, Any], construct: str, capability: dict[str, Any], policy: dict[str, Any],
) -> dict[str, Any]:
    final_toc = structure.get("final_toc")
    if not isinstance(final_toc, dict) or not isinstance(final_toc.get("include"), bool):
        raise ValueError(f"structure node lacks an explicit Spec 04-A final TOC disposition: {structure.get('node_id')}")
    level = final_toc.get("level")
    if not isinstance(level, int) or level != structure.get("level"):
        raise ValueError(f"source hierarchy and final TOC level disagree: {structure.get('node_id')}")
    params: dict[str, Any] = {"numbered": False, "toc": final_toc["include"], "level": level}
    if not final_toc["include"]:
        params["toc_visibility_strategy"] = "none"
        return params

    policy_toc = policy["toc_representation"]
    entry_type = policy_toc["semantic_level_to_entry_type"].get(str(level))
    if not entry_type:
        raise ValueError(f"render policy does not map semantic TOC level {level}")
    if construct != f"{entry_type}*":
        raise ValueError(f"body heading construct would change source TOC hierarchy at {structure.get('node_id')}: {construct} vs {entry_type}*")
    toc = capability.get("toc_capability", {})
    if toc.get("entry_type_depths", {}).get(entry_type) != level:
        raise ValueError(f"template TOC entry depth mapping disagrees with semantic level {level}: {entry_type}")
    effective = toc.get("effective_tocdepth")
    params["toc_entry_level"] = entry_type
    if isinstance(effective, int) and level <= effective:
        if entry_type not in toc.get("native_visible_entry_types", []):
            raise ValueError(f"template capability omits natively visible TOC entry type: {entry_type}")
        params["toc_visibility_strategy"] = "native"
        return params

    strategy = policy_toc["overflow_strategy"]
    capability_strategy = toc.get("serialization_strategies", {}).get(strategy, {})
    if (
        strategy != "localized_depth_override"
        or capability_strategy.get("supported") is not True
        or capability_strategy.get("preserves_entry_type") is not True
        or capability_strategy.get("preserves_pdf_outline_level") is not True
        or capability_strategy.get("adds_template_api") is not False
        or capability_strategy.get("modifies_template_preamble_or_class") is not False
    ):
        raise ValueError(f"MAPPING_TOC_LEVEL_UNRENDERABLE: level {level} exceeds template TOC depth without a legal preserving strategy")
    params.update({"toc_visibility_strategy": strategy, "toc_depth_override": level})
    return params


def validate_toc_renderability(nodes: list[dict[str, Any]], binding: dict[str, Any]) -> dict[str, Any]:
    effective = binding.get("effective_tocdepth")
    depths = binding.get("entry_type_depths", {})
    strategies = binding.get("serialization_strategies", {})
    toc_nodes = [item for item in nodes if item.get("node_kind") == "book_structure" and item.get("construct_parameters", {}).get("toc")]
    localized = 0
    for node in toc_nodes:
        params = node["construct_parameters"]
        level = params.get("level")
        entry_type = params.get("toc_entry_level")
        strategy = params.get("toc_visibility_strategy")
        if not isinstance(level, int) or depths.get(entry_type) != level or node.get("target_construct") != f"{entry_type}*":
            raise ValueError(f"MAPPING_TOC_LEVEL_UNRENDERABLE: semantic/body/TOC level mismatch at {node.get('render_node_id')}")
        if strategy == "native":
            if not isinstance(effective, int) or level > effective or entry_type not in binding.get("native_visible_entry_types", []):
                raise ValueError(f"MAPPING_TOC_LEVEL_UNRENDERABLE: native TOC entry exceeds effective depth at {node.get('render_node_id')}")
        elif strategy == "localized_depth_override":
            capability_strategy = strategies.get(strategy, {})
            if (
                (isinstance(effective, int) and level <= effective)
                or params.get("toc_depth_override") != level
                or capability_strategy.get("supported") is not True
                or capability_strategy.get("preserves_entry_type") is not True
                or capability_strategy.get("adds_template_api") is not False
            ):
                raise ValueError(f"MAPPING_TOC_LEVEL_UNRENDERABLE: invalid localized TOC strategy at {node.get('render_node_id')}")
            localized += 1
        else:
            raise ValueError(f"MAPPING_TOC_LEVEL_UNRENDERABLE: unknown TOC strategy at {node.get('render_node_id')}")
    return {"toc_nodes": len(toc_nodes), "native_toc_nodes": len(toc_nodes) - localized, "localized_depth_override_nodes": localized}


def verify_selection(registry: Path, lineage: str, promotion_path: Path, stage_kind: str) -> dict[str, Any]:
    gate = load_module("stage_promotion_gate.py", f"promotion_{stage_kind}")
    selected = gate.verify_registry_selection(registry, lineage, promotion_path, stage_kind, capability_verification="frozen")
    promotion = selected["promotion"]
    return {
        "promotion_id": promotion["promotion_id"],
        "promotion_class": promotion["promotion_class"],
        "lineage_key": lineage,
        "manifest_path": str(promotion_path),
        "manifest_sha256": sha256_file(promotion_path),
        "registry_path": str(registry),
        "registry_sha256": sha256_file(registry),
        "promoted_artifacts": promotion.get("promoted_artifacts", {}),
        "capability_verification": "frozen_ancestor_snapshot",
    }


def require_promoted(parent: dict[str, Any], role: str, supplied: Path) -> None:
    item = parent["promoted_artifacts"].get(role, {})
    if Path(item.get("path", "")).resolve() != supplied.resolve() or item.get("sha256") != sha256_file(supplied):
        raise ValueError(f"active promotion does not promote supplied {role}")


def validate_media_parent_binding(plan: dict[str, Any], media_promotion: dict[str, Any]) -> dict[str, int]:
    promoted = media_promotion.get("promoted_artifacts", {})
    evidence = promoted.get("media_evidence_ledger", {})
    representation = promoted.get("media_representation_plan", {})
    evidence_path = Path(evidence.get("path", ""))
    representation_path = Path(representation.get("path", ""))
    if (
        not evidence_path.is_file()
        or evidence.get("sha256") != sha256_file(evidence_path)
        or plan.get("media_evidence_ledger_sha256") != evidence.get("sha256")
    ):
        raise ValueError("render plan is not bound to the exact active media evidence ledger")
    if (
        not representation_path.is_file()
        or representation.get("sha256") != sha256_file(representation_path)
        or plan.get("media_representation_plan_sha256") != representation.get("sha256")
    ):
        raise ValueError("render plan is not bound to the exact active media representation plan")

    media_plan = read_json(representation_path)
    closed = {
        item["representation_id"]: item
        for item in media_plan.get("representations", [])
        if item.get("status") == "closed"
    }
    media_nodes = [node for node in plan.get("nodes", []) if node.get("node_kind") == "media"]
    for node in media_nodes:
        binding = node.get("payload", {}).get("media_binding", {})
        representation_id = binding.get("representation_id")
        parent = closed.get(representation_id)
        if parent is None:
            raise ValueError(f"render node does not bind a closed active media representation: {representation_id}")
        if binding.get("media_representation_plan_sha256") != representation.get("sha256"):
            raise ValueError(f"render node media plan hash drift: {representation_id}")
        if (
            binding.get("artifact_sha256") != parent.get("artifact_sha256")
            or node.get("payload", {}).get("artifact_sha256") != parent.get("artifact_sha256")
            or set(node.get("source_block_ids", [])) != set(parent.get("source_block_ids", []))
        ):
            raise ValueError(f"render node media representation drift: {representation_id}")
    return {"media_nodes": len(media_nodes), "closed_representations": len(closed)}


def selected_candidate(
    atom: dict[str, Any],
    representation: dict[str, Any],
    artifact_root: Path,
) -> dict[str, Any]:
    matches = [item for item in atom.get("candidates", []) if item.get("candidate_id") == representation.get("selected_candidate_id")]
    if len(matches) != 1:
        raise ValueError(f"selected media candidate is absent or ambiguous: {representation.get('representation_id')}")
    candidate = matches[0]
    artifact_hash = candidate.get("artifact_sha256") or candidate.get("sha256")
    if artifact_hash != representation.get("artifact_sha256"):
        raise ValueError(f"selected media candidate hash differs from representation: {representation.get('representation_id')}")
    path_value = candidate.get("resolved_path") or candidate.get("crop_path")
    if representation.get("representation_type") in {"source_asset_image", "source_region_image"}:
        path = Path(str(path_value))
        if not path.is_absolute():
            path = artifact_root / path
        if not path.is_file() or sha256_file(path) != artifact_hash:
            raise ValueError(f"selected media artifact is absent or drifted: {path}")
        candidate = dict(candidate)
        if candidate.get("resolved_path"):
            candidate["resolved_path"] = str(path.resolve())
        else:
            candidate["crop_path"] = str(path.resolve())
    return candidate


def node_id(kind: str, source_ids: list[str], extra: str = "") -> str:
    return "render::" + canonical_hash({"kind": kind, "source_block_ids": source_ids, "extra": extra})[:24]


def validate_pedagogical_render_nodes(nodes: list[dict[str, Any]], contract: dict[str, Any] | None) -> dict[str, Any]:
    if contract is None:
        return {"enabled": False, "response_groups": 0, "heading_presentations": 0}
    response_nodes = {item["payload"].get("group_id"): item for item in nodes if item.get("node_kind") == "response_list"}
    expected_groups = {item["group_id"]: item for item in contract.get("response_groups", [])}
    if set(response_nodes) != set(expected_groups):
        raise ValueError("rendered response-list groups differ from the frozen pedagogical layout contract")
    for group_id, group in expected_groups.items():
        node = response_nodes[group_id]
        if group.get("columns") == 2 and len(group.get("items", [])) < 2:
            raise ValueError(f"two-column pedagogical response group requires at least two independently ordered atoms: {group_id}")
        if (
            node.get("target_construct") != "response_list"
            or node.get("source_block_ids") != group.get("source_block_ids")
            or node.get("construct_parameters", {}).get("columns") != group.get("columns")
            or node.get("construct_parameters", {}).get("answer_space") != group.get("answer_space")
            or node.get("payload", {}).get("items") != group.get("items")
        ):
            raise ValueError(f"response-list render node differs from its frozen group: {group_id}")
    headings = {item["block_id"]: item for item in contract.get("heading_presentations", [])}
    rendered_headings = {
        item["source_block_ids"][0]: item
        for item in nodes
        if item.get("node_kind") == "local_heading" and len(item.get("source_block_ids", [])) == 1 and item["source_block_ids"][0] in headings
    }
    for block_id, node in rendered_headings.items():
        expected = headings[block_id]
        if node.get("target_construct") != expected.get("target_construct"):
            raise ValueError(f"local heading construct differs from frozen presentation: {block_id}")
        if node["target_construct"] == "paragraph":
            actual_title = node.get("payload", {}).get("raw_content")
            expected_title = expected.get("source_title")
        else:
            actual_title = node.get("payload", {}).get("title")
            expected_title = expected.get("display_title")
        if actual_title != expected_title:
            raise ValueError(f"local heading display differs from frozen presentation: {block_id}")
    return {
        "enabled": True,
        "response_groups": len(response_nodes),
        "response_items": sum(len(item["source_block_ids"]) for item in response_nodes.values()),
        "heading_presentations": len(headings),
        "rendered_local_heading_presentations": len(rendered_headings),
    }


def build_render_nodes(
    records: list[dict[str, Any]], outline: dict[str, Any], bindings: dict[str, Any], media_evidence: dict[str, Any],
    media_plan: dict[str, Any], capability: dict[str, Any], policy: dict[str, Any], decision_refs: list[str],
) -> list[dict[str, Any]]:
    included = {item["block_id"]: item for item in records if item.get("scope_status") == "included"}
    assigned: dict[str, str] = {}
    draft: list[dict[str, Any]] = []
    capability_hash = capability["capability_payload_hash"]
    sectioning = set(capability["constructs"]["sectioning"])
    standard = set(capability["constructs"].get("standard_serialization", {}))
    media_source_ids = {
        value
        for rep in media_plan.get("representations", [])
        if rep.get("status") == "closed"
        for value in rep.get("source_block_ids", [])
        if value in included
    }
    structure_roles = classify_structure_source_roles(outline, included, media_source_ids, policy)
    pedagogical = policy.get("pedagogical_layout") or {}
    heading_presentations = {item["block_id"]: item for item in pedagogical.get("heading_presentations", [])}
    response_source_ids = {
        block_id
        for group in pedagogical.get("response_groups", [])
        for block_id in group.get("source_block_ids", [])
    }

    def add(kind: str, source_ids: list[str], construct: str, params: dict[str, Any], payload: dict[str, Any], evidence: list[str], extra: str = "", order_bounds: tuple[int, int] | None = None, physical_pages: list[int] | None = None, node_decision_refs: list[str] | None = None) -> None:
        if (not source_ids and kind != "book_structure") or len(source_ids) != len(set(source_ids)):
            raise ValueError(f"invalid empty or duplicate source ids in {kind}")
        unknown = sorted(set(source_ids) - set(included))
        overlap = sorted(block_id for block_id in source_ids if block_id in assigned)
        if unknown or overlap:
            raise ValueError(f"render coverage collision for {kind}: unknown={unknown[:6]} overlap={overlap[:6]}")
        rid = node_id(kind, source_ids, extra)
        for block_id in source_ids:
            assigned[block_id] = rid
        recs = [included[value] for value in source_ids]
        payload_hash = canonical_hash(payload)
        start = min((record_order(item)[0] for item in recs), default=order_bounds[0] if order_bounds else 10**9)
        end = max((record_order(item)[0] for item in recs), default=order_bounds[1] if order_bounds else start)
        draft.append({
            "render_node_id": rid, "node_kind": kind, "source_block_ids": source_ids,
            "target_construct": construct, "construct_parameters": params, "payload": payload,
            "payload_hash": payload_hash, "capability_manifest_sha256": capability_hash,
            "review_status": "closed", "decision_refs": sorted(set([*decision_refs, *(node_decision_refs or [])])), "source_evidence_ids": evidence,
            "virtual_source_supported": not source_ids,
            "source_order_start": start, "source_order_end": end,
            "pdf_physical_pages": sorted({item.get("pdf_physical_page") for item in recs}) if recs else sorted(set(physical_pages or [])),
        })

    structure_nodes = outline.get("body_hierarchy", [])
    structure_anchors: dict[str, str] = {}
    structure_by_render: dict[str, dict[str, Any]] = {}
    for structure in sorted(structure_nodes, key=lambda item: (item["source_order_start"], item["level"], item["node_id"])):
        # Source-outline evidence may legitimately include excluded running headers
        # used to confirm a chapter label.  Such evidence remains cited, but only
        # included atoms may enter the exact render partition.
        heading_evidence_ids = structure.get("heading_evidence_block_ids") or [structure["anchor_block_id"]]
        role_contract = structure_roles[structure["node_id"]]
        included_heading_ids = role_contract["title_fragment_block_ids"]
        post_heading_ids = role_contract["post_heading_body_block_ids"]
        source_ids = list(included_heading_ids)
        if structure["anchor_block_id"] not in included:
            raise ValueError(f"structure anchor is not an included source atom: {structure['node_id']}")
        construct = policy["structure_level_constructs"].get(str(structure["level"]))
        if construct not in sectioning:
            raise ValueError(f"template lacks structure construct {construct} for level {structure['level']}")
        rid = node_id("book_structure", source_ids, structure["node_id"])
        structure_by_render[rid] = structure
        structure_anchors[structure["node_id"]] = "anchor::" + rid.split("::", 1)[1]
        payload = {
            "title": structure["final_toc"]["title"],
            "title_source_fragments": [{"block_id": value, "raw_content": included[value].get("raw_content", ""), "raw_content_sha256": included[value].get("raw_content_sha256")} for value in included_heading_ids],
            "separate_body_block_ids": post_heading_ids,
            "source_evidence_block_ids": heading_evidence_ids,
            "media_evidence_block_ids": sorted(value for value in set([*heading_evidence_ids, structure["anchor_block_id"]]) if value in media_source_ids),
            "structure_node_id": structure["node_id"], "semantic_role": structure["role"],
        }
        params = structure_toc_parameters(structure, construct, capability, policy)
        add("book_structure", source_ids, construct, params, payload, structure.get("source_outline_evidence_ids", []), structure["node_id"], (structure["source_order_start"], structure["source_order_start"]), [structure["pdf_physical_page_start"]])
        body_construct = policy["plain_body_construct"]
        if post_heading_ids and body_construct not in standard:
            raise ValueError(f"template lacks structure-body construct: {body_construct}")
        for block_id in post_heading_ids:
            # A non-title structure anchor is still source body.  When the
            # frozen pedagogical contract assigns that atom to a response
            # group, the response_list owns its one visible serialization;
            # emitting an additional structure_body would duplicate content.
            if block_id in response_source_ids:
                continue
            record = included[block_id]
            add(
                "structure_body", [block_id], body_construct,
                {"source_type": record.get("source_type"), "structure_node_id": structure["node_id"]},
                {
                    "raw_content": record.get("raw_content", ""),
                    "raw_content_sha256": record.get("raw_content_sha256"),
                    "source_type": record.get("source_type"),
                    "structure_node_id": structure["node_id"],
                },
                structure.get("source_outline_evidence_ids", []), f"{structure['node_id']}::{block_id}",
            )

    for binding in sorted(bindings.get("bindings", []), key=lambda item: min(record_order(included[value]) for value in item["source_block_ids"])):
        source_ids = sorted(binding["source_block_ids"], key=lambda value: record_order(included[value]))
        construct = binding["target_construct"]
        if construct == "tcolorbox":
            marker = binding["construct_parameters"]["title_source_block_id"]
            body_ids = [value for value in source_ids if value != marker]
            payload = {
                "semantic_role": binding["semantic_role"],
                "title": included[marker].get("raw_content", ""),
                "title_source_block_id": marker,
                "body": [{"block_id": value, "raw_content": included[value].get("raw_content", ""), "raw_content_sha256": included[value].get("raw_content_sha256")} for value in body_ids],
            }
        else:
            payload = {"semantic_role": binding["semantic_role"], "title": included[source_ids[0]].get("raw_content", ""), "title_source_block_id": source_ids[0]}
        add(binding["object_kind"], source_ids, construct, copy.deepcopy(binding["construct_parameters"]), payload, binding.get("source_evidence_ids", []), binding["binding_id"])

    atoms_by_media = {item["media_id"]: item for item in media_evidence.get("atoms", [])}
    for rep in sorted((item for item in media_plan.get("representations", []) if item.get("status") == "closed"), key=lambda item: min((record_order(included[value]) for value in item.get("source_block_ids", []) if value in included), default=(10**9, 0, 0, ""))):
        source_ids = [value for value in rep.get("source_block_ids", []) if value in included]
        if not source_ids:
            continue
        representation_type = rep["representation_type"]
        construct = policy["media_constructs"].get(representation_type)
        if not construct or construct not in standard:
            raise ValueError(f"closed media representation has no template serialization capability: {representation_type}")
        atom = atoms_by_media.get(rep["media_id"])
        if not atom:
            raise ValueError(f"media evidence atom is absent: {rep['media_id']}")
        candidate = selected_candidate(
            atom,
            rep,
            Path(
                media_evidence.get("_artifact_root")
                or Path(media_plan["_path"]).resolve().parents[1]
            ),
        )
        binding = {
            "media_id": rep["media_id"], "representation_id": rep["representation_id"],
            "representation_type": representation_type, "selected_candidate_id": rep["selected_candidate_id"],
            "artifact_sha256": rep["artifact_sha256"], "media_representation_plan_sha256": sha256_file(Path(media_plan["_path"])),
        }
        params: dict[str, Any] = {}
        if representation_type in {"source_asset_image", "source_region_image"}:
            source_path = candidate.get("resolved_path") or candidate.get("crop_path")
            source_file = Path(str(source_path))
            bboxes = [included[value].get("bbox") for value in source_ids if included[value].get("bbox")]
            width = max((bbox[2] - bbox[0] for bbox in bboxes), default=policy["source_image_layout"]["maximum_width_fraction"])
            layout = policy["source_image_layout"]
            params = {
                "width_fraction": round(max(layout["minimum_width_fraction"], min(layout["maximum_width_fraction"], width)), 4),
                "max_height_fraction": layout["max_height_fraction"], "alignment": layout["alignment"],
            }
            payload = {
                "asset_ref": source_file.name,
                "asset_size_bytes": source_file.stat().st_size,
                "artifact_sha256": rep["artifact_sha256"],
                "media_binding": binding,
            }
        else:
            math = candidate.get("payload", {}).get("math") or candidate.get("math")
            if not isinstance(math, str) or not math.strip():
                raise ValueError(f"structured formula lacks frozen source math: {rep['representation_id']}")
            payload = {"source_math": math, "artifact_sha256": rep["artifact_sha256"], "media_binding": binding}
        add("media", source_ids, construct, params, payload, rep.get("decision_refs", []), rep["representation_id"])

    response_construct = "response_list"
    for group in sorted(
        pedagogical.get("response_groups", []),
        key=lambda item: min(record_order(included[value]) for value in item["source_block_ids"]),
    ):
        source_ids = list(group["source_block_ids"])
        if response_construct not in standard:
            raise ValueError("template lacks the frozen response-list serialization capability")
        items = []
        for item in group["items"]:
            record = included[item["block_id"]]
            if item["source_text"] != record.get("raw_content") or item["source_text_sha256"] != record.get("raw_content_sha256"):
                raise ValueError(f"response-list source binding drift: {item['block_id']}")
            items.append(copy.deepcopy(item))
        add(
            "response_list", source_ids, response_construct,
            {
                "columns": group["columns"],
                "answer_space": copy.deepcopy(group["answer_space"]),
                "exercise_heading_block_id": group.get("exercise_heading_block_id"),
            },
            {"group_id": group["group_id"], "topic_id": group["topic_id"], "items": items},
            [], group["group_id"], node_decision_refs=group.get("decision_refs", []),
        )

    safe_fragile = set(policy["safe_textual_fragile_types"])
    for record in sorted(included.values(), key=record_order):
        block_id = record["block_id"]
        if block_id in assigned:
            continue
        disposition = record.get("semantic_disposition")
        source_type = record.get("source_type")
        if source_type in set(policy["fragile_types_requiring_media_representation"]):
            raise ValueError(f"unsafe fragile source atom lacks closed Spec 03 media representation: {block_id}")
        if disposition == "local_heading":
            presentation = heading_presentations.get(block_id)
            construct = presentation["target_construct"] if presentation else policy["local_heading_construct"]
            if construct in sectioning:
                params = {"numbered": False, "toc": False}
                payload = {
                    "title": presentation["display_title"] if presentation else record.get("raw_content", ""),
                    "source_title": record.get("raw_content", ""),
                    "source_title_sha256": record.get("raw_content_sha256"),
                    "semantic_role": presentation.get("semantic_role") if presentation else "local_heading",
                }
            elif construct == policy["plain_body_construct"] and construct in standard:
                params = {"source_type": source_type, "semantic_role": presentation.get("semantic_role") if presentation else "local_heading"}
                payload = {
                    "raw_content": record.get("raw_content", ""),
                    "raw_content_sha256": record.get("raw_content_sha256"),
                    "source_type": source_type,
                }
            else:
                raise ValueError(f"template lacks frozen local-heading construct: {construct}")
            add(
                "local_heading", [block_id], construct, params, payload,
                record.get("semantic_disposition_decision_refs", []),
                node_decision_refs=presentation.get("decision_refs", []) if presentation else [],
            )
        elif disposition == "plain_body" or (disposition == "fragile_or_media" and source_type in safe_fragile):
            construct = policy["plain_body_construct"]
            if construct not in standard:
                raise ValueError(f"template lacks plain-body construct: {construct}")
            add("source_annotation" if disposition == "fragile_or_media" else "plain_body", [block_id], construct, {"source_type": source_type}, {"raw_content": record.get("raw_content", ""), "raw_content_sha256": record.get("raw_content_sha256"), "source_type": source_type}, record.get("semantic_disposition_decision_refs", []))
        else:
            raise ValueError(f"unconsumed semantic disposition cannot be guessed in Spec 04-D: {block_id} {disposition}/{source_type}")

    if set(assigned) != set(included):
        raise ValueError("render coverage is not an exact partition")

    def containing_structure(node: dict[str, Any]) -> dict[str, Any] | None:
        candidates = [
            item for item in structure_nodes
            if item["source_order_start"] <= node["source_order_start"] <= (item["source_order_end"] if item.get("source_order_end") is not None else 10**9)
        ]
        return max(candidates, key=lambda item: (item["level"], item["source_order_start"]), default=None)

    priority = {"book_structure": 0, "structure_body": 1, "local_heading": 1, "standalone_label": 1, "teaching_group": 2, "response_list": 3, "plain_body": 3, "source_annotation": 4, "media": 5}
    draft.sort(key=lambda item: (item["source_order_start"], priority.get(item["node_kind"], 3), item["source_order_end"], item["render_node_id"]))
    for order, item in enumerate(draft, 1):
        item["render_order"] = order
        item["output_anchor_id"] = "anchor::" + item["render_node_id"].split("::", 1)[1]
        if item["render_node_id"] in structure_by_render:
            parent_id = structure_by_render[item["render_node_id"]].get("parent_node_id")
            item["parent_output_anchor_id"] = structure_anchors.get(parent_id)
        else:
            parent = containing_structure(item)
            item["parent_output_anchor_id"] = structure_anchors.get(parent["node_id"]) if parent else None
    validate_toc_renderability(draft, toc_capability_binding(capability))
    validate_structure_source_integrity(draft, outline, records, policy)
    validate_pedagogical_render_nodes(draft, pedagogical or None)
    return draft


def capability_resources(skill_root: Path, policy: Path) -> list[tuple[str, Path]]:
    names = [
        "render-plan.schema.json", "media-binding.schema.json", "spec04d-render-policy.schema.json",
        "spec04d-render-plan-stage-manifest.schema.json", "spec04d-semantic-mapping-ledger.schema.json",
        "spec04d-preflight-report.schema.json", "volume-partition-plan.schema.json", "execution-capability-manifest.schema.json",
    ]
    return [("machine_schema", skill_root / "schemas" / name) for name in names] + [("render_policy", policy)]


def prepare_policy_review_task(args: argparse.Namespace) -> dict[str, Any]:
    parent = args.parent.resolve()
    structure = args.structure.resolve()
    media = args.media.resolve()
    header, records = read_ledger(
        parent / "ledgers/canonical_block_ledger.jsonl"
    )
    task = render_policy_review_task(
        records=records,
        ledger_payload_hash=header["current_ledger_hash"],
        outline=read_json(structure / "structure/source_outline_ledger.json"),
        final_toc=read_json(structure / "structure/final_toc_plan.json"),
        construct_binding=read_json(
            parent / "semantic/construct_binding_ledger.json"
        ),
        template=read_json(
            parent / "template/template_capability_manifest.json"
        ),
        media_plan=read_json(
            media / "media/media_representation_plan.json"
        ),
    )
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite review task: {output}")
    write_json(output, task)
    return {
        "status": "prepared",
        "task_sha256": sha256_file(output),
        "task_canonical_sha256": canonical_hash(task),
        "candidates": task["candidate_count"],
        "minimum_response_bytes": task["capacity"]["minimum_response_bytes"],
        "maximum_response_bytes": task["capacity"]["maximum_response_bytes"],
    }


def project_policy_review_command(args: argparse.Namespace) -> dict[str, Any]:
    task = read_json(args.task.resolve())
    compact_review = read_json(args.compact_review.resolve())
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite projected review: {output}")
    policy = project_policy_review(task, compact_review)
    write_json(output, policy)
    return {
        "status": "projected",
        "output_sha256": sha256_file(output),
        "output_canonical_sha256": canonical_hash(policy),
        "structure_source_role_overrides": len(
            policy["structure_source_role_overrides"]
        ),
    }


def capability_invocation(args: argparse.Namespace) -> list[str]:
    result = ["spec04d_render_plan_contract.py", "produce"]
    path_names = (
        "parent_ledger", "parent_decision_index", "construct_binding_ledger", "template_capability_manifest",
        "source_outline_ledger", "final_toc_plan", "media_evidence_ledger", "media_representation_plan",
        "source_pdf", "promotion_registry", "parent_04c_promotion", "structure_promotion", "media_promotion",
        "render_policy", "output_dir",
    )
    for name in path_names:
        result += [f"--{name.replace('_', '-')}", str(getattr(args, name).resolve())]
    for name in ("parent_04c_lineage", "structure_lineage", "media_lineage", "ledger_snapshot_id", "ledger_version", "decision_snapshot_id", "stage_decision_id", "run_id"):
        result += [f"--{name.replace('_', '-')}", str(getattr(args, name))]
    return result


def produce(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite run directory: {output}")
    parent_ledger = args.parent_ledger.resolve()
    parent_index_path = args.parent_decision_index.resolve()
    header, records = read_ledger(parent_ledger)
    parent_index = read_json(parent_index_path)
    closed_index(parent_index)
    if header.get("canonical_decision_index_hash") != sha256_file(parent_index_path):
        raise ValueError("parent ledger is not bound to supplied decision index")
    if header.get("material_identity", {}).get("source_pdf_sha256") != sha256_file(args.source_pdf.resolve()):
        raise ValueError("source PDF differs from parent canonical ledger")
    policy = read_json(args.render_policy.resolve())
    validate_policy(policy)
    pedagogical_contract = validate_pedagogical_layout_binding(policy, parent_ledger, header, records)
    media_plan = read_json(args.media_representation_plan.resolve())
    media_plan["_path"] = str(args.media_representation_plan.resolve())
    preflight = preflight_data(records, media_plan, policy)
    if preflight["status"] != "passed":
        raise ValueError(f"Spec 04-D preflight failed: {preflight['issues']}")

    registry = args.promotion_registry.resolve()
    parent04c = verify_selection(registry, args.parent_04c_lineage, args.parent_04c_promotion.resolve(), "spec04c_construct_binding_contract")
    structure = verify_selection(registry, args.structure_lineage, args.structure_promotion.resolve(), "spec04a_structure_contract")
    media = verify_selection(registry, args.media_lineage, args.media_promotion.resolve(), "spec03_media_contract")
    require_promoted(parent04c, "ledger_L", parent_ledger)
    require_promoted(parent04c, "decision_index_D", parent_index_path)
    require_promoted(parent04c, "construct_binding_ledger", args.construct_binding_ledger.resolve())
    require_promoted(parent04c, "template_capability_manifest", args.template_capability_manifest.resolve())
    require_promoted(structure, "source_outline_ledger", args.source_outline_ledger.resolve())
    require_promoted(structure, "final_toc_plan", args.final_toc_plan.resolve())
    require_promoted(media, "media_evidence_ledger", args.media_evidence_ledger.resolve())
    require_promoted(media, "media_representation_plan", args.media_representation_plan.resolve())
    if len({parent04c["promotion_class"], structure["promotion_class"], media["promotion_class"]}) != 1:
        raise ValueError("Spec 04-D parent promotion classes disagree")

    outline = read_json(args.source_outline_ledger.resolve())
    final_toc = read_json(args.final_toc_plan.resolve())
    bindings = read_json(args.construct_binding_ledger.resolve())
    capability = read_json(args.template_capability_manifest.resolve())
    media_evidence = read_json(args.media_evidence_ledger.resolve())
    media_evidence["_artifact_root"] = str(
        args.media_evidence_ledger.resolve().parents[1]
    )
    if outline.get("slice_status") != "passed" or bindings.get("slice_status") != "passed" or media_plan.get("spec_status") != "passed":
        raise ValueError("one or more parent contracts are not passed")
    if final_toc.get("open_reviews") != 0 or bindings.get("summary", {}).get("open_reviews") != 0 or media_plan.get("open_reviews") != 0:
        raise ValueError("one or more parent contracts contain open reviews")

    output.mkdir(parents=True)
    write_json(output / "reports/spec04d_preflight.json", preflight)
    skill_root = Path(__file__).parents[1].resolve()
    execution = load_module("execution_capability.py", "execution_capability_spec04d")
    capability_path = output / "precommit/execution_capability_manifest.json"
    producer_capability = execution.build_manifest(
        manifest_id=f"{args.run_id}-producer-capability", skill_root=skill_root,
        entrypoints=[
            ("stage_producer", Path(__file__).resolve()),
            ("execution_capability_core", Path(__file__).with_name("execution_capability.py").resolve()),
            ("promotion_selection_core", Path(__file__).with_name("stage_promotion_gate.py").resolve()),
        ],
        resources=capability_resources(skill_root, args.render_policy.resolve()), invocation=capability_invocation(args), producer=VERSION,
    )
    write_json(capability_path, producer_capability)
    execution.validate_manifest(capability_path)

    precommit = [
        {"role": "parent_spec04c_promotion", "path": parent04c["manifest_path"], "sha256": parent04c["manifest_sha256"]},
        {"role": "active_spec04a_promotion", "path": structure["manifest_path"], "sha256": structure["manifest_sha256"]},
        {"role": "active_spec03_promotion", "path": media["manifest_path"], "sha256": media["manifest_sha256"]},
        {"role": "promotion_registry", "path": str(registry), "sha256": sha256_file(registry)},
        {"role": "parent_canonical_ledger", "path": str(parent_ledger), "sha256": sha256_file(parent_ledger)},
        {"role": "parent_decision_index", "path": str(parent_index_path), "sha256": sha256_file(parent_index_path)},
        {"role": "construct_binding_ledger", "path": str(args.construct_binding_ledger.resolve()), "sha256": sha256_file(args.construct_binding_ledger.resolve())},
        {"role": "template_capability_manifest", "path": str(args.template_capability_manifest.resolve()), "sha256": sha256_file(args.template_capability_manifest.resolve())},
        {"role": "source_outline_ledger", "path": str(args.source_outline_ledger.resolve()), "sha256": sha256_file(args.source_outline_ledger.resolve())},
        {"role": "final_toc_plan", "path": str(args.final_toc_plan.resolve()), "sha256": sha256_file(args.final_toc_plan.resolve())},
        {"role": "media_evidence_ledger", "path": str(args.media_evidence_ledger.resolve()), "sha256": sha256_file(args.media_evidence_ledger.resolve())},
        {"role": "media_representation_plan", "path": str(args.media_representation_plan.resolve()), "sha256": sha256_file(args.media_representation_plan.resolve())},
        {"role": "render_policy", "path": str(args.render_policy.resolve()), "sha256": sha256_file(args.render_policy.resolve())},
        {"role": "source_pdf", "path": str(args.source_pdf.resolve()), "sha256": sha256_file(args.source_pdf.resolve())},
        {"role": "execution_capability", "path": "precommit/execution_capability_manifest.json", "sha256": sha256_file(capability_path)},
    ]
    event = {
        "decision_id": args.stage_decision_id, "status": "closed", "decided_at": now(),
        "rule_id": "SM-H01..SM-H18/SPEC04D-RENDER-PLAN-COMMIT",
        "decision_type": "mechanical_complete_render_plan_freeze",
        "scope": "Freeze exact source coverage, order, payload, output anchors, inherited constructs, and Spec 03 media bindings; emit no LaTeX.",
        "evidence": precommit, "review_refs": policy["review"]["decision_refs"], "prohibitions": policy["prohibitions"],
        "supersedes": [], "invalidated_by": None,
    }
    event_path = output / "decisions/render_plan_decisions.jsonl"
    write_jsonl(event_path, [event])
    decisions = copy.deepcopy(parent_index["decisions"])
    if args.stage_decision_id in {item["decision_id"] for item in decisions}:
        raise ValueError("stage decision id already exists")
    decisions.append({"decision_id": args.stage_decision_id, "event_file": "decisions/render_plan_decisions.jsonl", "rule_id": event["rule_id"], "status": "closed", "supersedes": [], "invalidated_by": None})
    counts = Counter(item["status"] for item in decisions)
    index = {
        "schema_version": "canonical-decision-index/1.1", "decision_index_id": parent_index["decision_index_id"],
        "snapshot_id": args.decision_snapshot_id, "version": int(parent_index["version"]) + 1, "generated_at": now(),
        "parent_index_ref": relative(output, parent_index_path), "parent_index_hash": sha256_file(parent_index_path),
        "acyclic_commit_rule": "evidence_or_parent_then_decision_index_D_then_child_artifact_L",
        "spec_status": "passed", "evidence_committed_before_index": precommit,
        "decision_event_files": [{"path": "decisions/render_plan_decisions.jsonl", "sha256": sha256_file(event_path), "decision_ids": [args.stage_decision_id]}],
        "decisions": decisions, "summary": {"closed": counts["closed"], "superseded": counts["superseded"], "open": 0, "stale": 0, "invalidated": 0},
    }
    index_path = output / "decisions/canonical_decision_index.json"
    write_json(index_path, index)
    decision_sha = sha256_file(index_path)

    nodes = build_render_nodes(records, outline, bindings, media_evidence, media_plan, capability, policy, [args.stage_decision_id, *policy["review"]["decision_refs"]])
    volume_partition = build_volume_partition_plan(nodes, policy)
    volume_partition_path = output / "render/volume_partition_plan.json"
    write_json(volume_partition_path, volume_partition)
    plan = {
        "schema_version": "render-plan/2.0", "generated_at": now(),
        "source_ledger_snapshot_id": header["ledger_snapshot_id"], "source_ledger_payload_hash": header["current_ledger_hash"],
        "source_ledger_sha256": sha256_file(parent_ledger),
        "profile": {"id": policy["policy_id"], "ownership_layer": policy["ownership_layer"]},
        "book_config_sha256": sha256_file(args.render_policy.resolve()), "planning_only": True, "latex_generated": False,
        "capability_manifest_sha256": capability["capability_payload_hash"], "capability_manifest_file_sha256": sha256_file(args.template_capability_manifest.resolve()),
        "toc_capability_binding": toc_capability_binding(capability),
        "structure_source_role_contract": {
            "render_policy_sha256": sha256_file(args.render_policy.resolve()),
            "role_overrides": copy.deepcopy(policy.get("structure_source_role_overrides", [])),
        },
        "pedagogical_layout_contract": copy.deepcopy(pedagogical_contract),
        "decision_index_sha256": decision_sha,
        "media_evidence_ledger_sha256": sha256_file(args.media_evidence_ledger.resolve()),
        "media_representation_plan_sha256": sha256_file(args.media_representation_plan.resolve()),
        "parent_promotions": {"spec04c": {k: v for k, v in parent04c.items() if k != "promoted_artifacts"}, "spec04a": {k: v for k, v in structure.items() if k != "promoted_artifacts"}, "spec03": {k: v for k, v in media.items() if k != "promoted_artifacts"}},
        "nodes": nodes, "volume_partition_plan": volume_partition,
        "volume_partition_plan_sha256": sha256_file(volume_partition_path),
        "spec_status": "passed", "open_reviews": 0,
        "scope_prohibitions": policy["prohibitions"],
    }
    plan["deterministic_payload_hash"] = canonical_hash({key: value for key, value in plan.items() if key not in {"generated_at", "deterministic_payload_hash"}})
    plan_path = output / "render/render_plan.json"
    write_json(plan_path, plan)
    mapping = {
        "schema_version": "semantic-mapping-ledger/2.0", "generated_at": now(), "status": "passed", "full_spec04_status": "passed",
        "render_plan_sha256": sha256_file(plan_path), "render_plan_payload_hash": plan["deterministic_payload_hash"],
        "assignments": [{"render_node_id": item["render_node_id"], "source_block_ids": item["source_block_ids"], "semantic_role": item["node_kind"], "target_construct": item["target_construct"], "construct_parameters": item["construct_parameters"], "payload_hash": item["payload_hash"], "output_anchor_id": item["output_anchor_id"], "parent_output_anchor_id": item["parent_output_anchor_id"], "decision_refs": item["decision_refs"]} for item in nodes],
        "summary": {"included_source_atoms": sum(item.get("scope_status") == "included" for item in records), "render_nodes": len(nodes), "open_reviews": 0, "constructs": dict(sorted(Counter(item["target_construct"] for item in nodes).items()))},
    }
    mapping_path = output / "semantic/semantic_mapping_ledger.json"
    write_json(mapping_path, mapping)
    queue_path = output / "semantic/semantic_review_queue.json"
    write_json(queue_path, {"schema_version": "semantic-review-queue/2.0", "generated_at": now(), "status": "closed", "open_items": 0, "items": []})

    by_block = {block_id: item for item in nodes for block_id in item["source_block_ids"]}
    structure_integrity = validate_structure_source_integrity(nodes, outline, records, policy)
    records_out = copy.deepcopy(records)
    for record in records_out:
        if record.get("scope_status") == "included":
            item = by_block[record["block_id"]]
            record["render_binding"] = {"render_node_id": item["render_node_id"], "target_construct": item["target_construct"], "render_order": item["render_order"], "payload_hash": item["payload_hash"], "output_anchor_id": item["output_anchor_id"], "decision_refs": item["decision_refs"]}
    header_out = copy.deepcopy(header)
    header_out.update({
        "generated_at": now(), "updated_at": now(), "ledger_snapshot_id": args.ledger_snapshot_id, "ledger_version": args.ledger_version,
        "parent_ledger_ref": relative(output, parent_ledger), "parent_ledger_file_sha256": sha256_file(parent_ledger), "parent_ledger_hash": header["current_ledger_hash"],
        "canonical_decision_index_ref": "decisions/canonical_decision_index.json", "canonical_decision_index_hash": decision_sha,
        "ledger_checkpoint": "semantic_frozen", "spec_status": "passed", "current_ledger_hash": canonical_hash(records_out),
        "current_ledger_hash_scope": "canonical JSON hash of ordered source_block records with frozen Spec 04-D render bindings",
        "spec04d_render_plan": {"status": "passed", "full_spec04_status": "passed", "producer": VERSION, "render_plan_sha256": sha256_file(plan_path), "render_plan_payload_hash": plan["deterministic_payload_hash"], "semantic_mapping_ledger_sha256": sha256_file(mapping_path), "included_source_atoms": len(by_block), "open_reviews": 0},
    })
    ledger_path = output / "ledgers/canonical_block_ledger.jsonl"
    write_jsonl(ledger_path, [header_out, *records_out])

    checks = [
        ("S4D-H01-exact-source-atom-partition", len(by_block) == sum(item.get("scope_status") == "included" for item in records)),
        ("S4D-H02-unique-render-orders", [item["render_order"] for item in nodes] == list(range(1, len(nodes) + 1))),
        ("S4D-H03-inherited-construct-bindings", all(any(set(binding["source_block_ids"]) == set(item["source_block_ids"]) and binding["target_construct"] == item["target_construct"] for item in nodes) for binding in bindings["bindings"])),
        ("S4D-H04-media-fragments-grouped-once", preflight["status"] == "passed"),
        ("S4D-H05-output-anchors-complete", all(item.get("output_anchor_id") for item in nodes)),
        ("S4D-H06-payloads-hashed", all(item["payload_hash"] == canonical_hash(item["payload"]) for item in nodes)),
        ("S4D-H07-no-open-review", plan["open_reviews"] == 0),
        ("S4D-H08-no-latex-or-reconstruction", plan["latex_generated"] is False and {"latex_generation", "formula_reconstruction", "table_reconstruction"} <= set(plan["scope_prohibitions"])),
        ("S4D-H09-full-spec04-closed", mapping["full_spec04_status"] == "passed"),
        ("S4D-H10-source-order-monotonic", all(nodes[i]["source_order_start"] <= nodes[i+1]["source_order_start"] for i in range(len(nodes)-1))),
        ("S4D-H11-structure-anchor-text-integrity", structure_integrity["structure_nodes"] == len(outline.get("body_hierarchy", []))),
        ("S4D-H12-volume-partition-closed", validate_volume_partition_plan(volume_partition, nodes)["volumes"] in {1, 2}),
        ("S4D-H13-pedagogical-layout-mechanically-consumed", validate_pedagogical_render_nodes(nodes, pedagogical_contract)["response_groups"] == len((pedagogical_contract or {}).get("response_groups", []))),
    ]
    report = {"schema_version": "spec04d-render-plan-validation/1.0", "generated_at": now(), "status": "passed" if all(value for _, value in checks) else "failed", "checks": [{"check_id": key, "status": "passed" if value else "failed"} for key, value in checks], "summary": {"checks": len(checks), "passed": sum(value for _, value in checks), "failed": sum(not value for _, value in checks)}}
    report_path = output / "reports/spec04d_render_plan_validation.json"
    write_json(report_path, report)
    if report["status"] != "passed":
        raise ValueError("internal Spec 04-D validation failed")

    write_json(output / "ledgers/ledger_manifest.json", {"schema_version": "ledger-manifest/2.4", "generated_at": now(), "ledger_id": header_out["ledger_id"], "ledger_version": header_out["ledger_version"], "snapshot_id": header_out["ledger_snapshot_id"], "artifact_path": "ledgers/canonical_block_ledger.jsonl", "artifact_sha256": sha256_file(ledger_path), "payload_hash": header_out["current_ledger_hash"], "parent_artifact_ref": header_out["parent_ledger_ref"], "parent_artifact_sha256": header_out["parent_ledger_file_sha256"], "decision_index_ref": "decisions/canonical_decision_index.json", "decision_index_hash": decision_sha, "ledger_checkpoint": "semantic_frozen", "spec_status": "passed", "full_spec04_status": "passed", "immutable_after_publication": True})
    stage = {
        "schema_version": STAGE_SCHEMA, "stage_kind": "spec04d_render_plan_contract", "run_id": args.run_id, "generated_at": now(),
        "status": "passed", "slice_status": "passed", "full_spec04_status": "passed", "producer": VERSION, "producer_mode": parent04c["promotion_class"],
        "commit_order": ["precommit_evidence_and_execution_capability_E", "decision_index_D", "render_plan_mapping_and_ledger_L", "stage_manifest_M"],
        "parent_promotions": {"spec04c": {k: v for k, v in parent04c.items() if k != "promoted_artifacts"}, "spec04a": {k: v for k, v in structure.items() if k != "promoted_artifacts"}, "spec03": {k: v for k, v in media.items() if k != "promoted_artifacts"}},
        "execution_capability_E": {"path": "precommit/execution_capability_manifest.json", "sha256": sha256_file(capability_path), "payload_hash": producer_capability["payload_hash"]},
        "decision_index_D": {"path": "decisions/canonical_decision_index.json", "sha256": decision_sha},
        "ledger_L": {"path": "ledgers/canonical_block_ledger.jsonl", "sha256": sha256_file(ledger_path), "payload_hash": header_out["current_ledger_hash"]},
        "render_plan": {"path": "render/render_plan.json", "sha256": sha256_file(plan_path), "payload_hash": plan["deterministic_payload_hash"]},
        "volume_partition_plan": {"path": "render/volume_partition_plan.json", "sha256": sha256_file(volume_partition_path), "payload_hash": volume_partition["deterministic_payload_hash"]},
        "semantic_mapping_ledger": {"path": "semantic/semantic_mapping_ledger.json", "sha256": sha256_file(mapping_path)},
        "review_queue": {"path": "semantic/semantic_review_queue.json", "sha256": sha256_file(queue_path)},
        "validation": {"path": "reports/spec04d_render_plan_validation.json", "sha256": sha256_file(report_path)},
        "preflight": {"path": "reports/spec04d_preflight.json", "sha256": sha256_file(output / "reports/spec04d_preflight.json")},
        "scope_prohibitions": policy["prohibitions"],
    }
    stage_path = output / "manifests/spec04d_render_plan_stage_manifest.json"
    write_json(stage_path, stage)
    run_files = [{"path": path.relative_to(output).as_posix(), "sha256": sha256_file(path), "size_bytes": path.stat().st_size} for path in sorted(output.rglob("*")) if path.is_file() and path.name != "run_manifest.json"]
    write_json(output / "manifests/run_manifest.json", {"schema_version": "immutable-run-manifest/1.1", "run_id": args.run_id, "generated_at": now(), "status": "passed", "stage_kind": "spec04d_render_plan_contract", "producer_mode": stage["producer_mode"], "immutable_after_publication": True, "files": run_files})
    return stage, 0


def validate_run(run_dir: Path) -> dict[str, Any]:
    run = run_dir.resolve()
    stage = read_json(run / "manifests/spec04d_render_plan_stage_manifest.json")
    if stage.get("schema_version") != STAGE_SCHEMA or stage.get("status") != "passed" or stage.get("full_spec04_status") != "passed":
        raise ValueError("unsupported or non-passed Spec 04-D stage")
    roles = ["execution_capability_E", "decision_index_D", "ledger_L", "render_plan", "volume_partition_plan", "semantic_mapping_ledger", "review_queue", "validation", "preflight"]
    paths = {}
    for role in roles:
        path = run / stage[role]["path"]
        if not path.is_file() or sha256_file(path) != stage[role]["sha256"]:
            raise ValueError(f"Spec 04-D artifact is absent or drifted: {role}")
        paths[role] = path
    load_module("execution_capability.py", "execution_capability_spec04d_validate").validate_manifest(paths["execution_capability_E"])
    producer_capability = read_json(paths["execution_capability_E"])
    header, records = read_ledger(paths["ledger_L"])
    index = read_json(paths["decision_index_D"])
    closed_index(index)
    plan = read_json(paths["render_plan"])
    volume_partition = read_json(paths["volume_partition_plan"])
    mapping = read_json(paths["semantic_mapping_ledger"])
    if header.get("canonical_decision_index_hash") != sha256_file(paths["decision_index_D"]) or header.get("spec04d_render_plan", {}).get("render_plan_sha256") != sha256_file(paths["render_plan"]):
        raise ValueError("Spec 04-D ledger is not bound to decision index/render plan")
    if plan.get("deterministic_payload_hash") != canonical_hash({key: value for key, value in plan.items() if key not in {"generated_at", "deterministic_payload_hash"}}):
        raise ValueError("render plan deterministic payload hash mismatch")
    included = {item["block_id"] for item in records if item.get("scope_status") == "included"}
    covered = [block_id for node in plan.get("nodes", []) for block_id in node.get("source_block_ids", [])]
    if set(covered) != included or len(covered) != len(set(covered)):
        raise ValueError("render plan is not an exact partition of included source atoms")
    if any(node.get("payload_hash") != canonical_hash(node.get("payload")) for node in plan["nodes"]):
        raise ValueError("render node payload hash mismatch")
    if plan.get("volume_partition_plan") != volume_partition or plan.get("volume_partition_plan_sha256") != sha256_file(paths["volume_partition_plan"]):
        raise ValueError("render plan does not bind the exact volume partition plan")
    volume_summary = validate_volume_partition_plan(volume_partition, plan["nodes"])
    toc_summary = validate_toc_renderability(plan["nodes"], plan.get("toc_capability_binding", {}))
    policy_resources = [item for item in producer_capability.get("resources", []) if item.get("role") == "render_policy"]
    if len(policy_resources) != 1:
        raise ValueError("Spec 04-D producer capability does not bind exactly one render policy")
    policy = read_json(Path(policy_resources[0]["path"]))
    if plan.get("pedagogical_layout_contract") != policy.get("pedagogical_layout"):
        raise ValueError("render plan pedagogical layout differs from the bound render policy")
    pedagogical_summary = validate_pedagogical_render_nodes(plan["nodes"], plan.get("pedagogical_layout_contract"))
    media_promotion = read_json(Path(stage["parent_promotions"]["spec03"]["manifest_path"]))
    media_binding = validate_media_parent_binding(plan, media_promotion)
    structure_promotion = read_json(Path(stage["parent_promotions"]["spec04a"]["manifest_path"]))
    outline = read_json(Path(structure_promotion["promoted_artifacts"]["source_outline_ledger"]["path"]))
    structure_integrity = validate_structure_source_integrity(plan["nodes"], outline, records, policy)
    if read_json(paths["preflight"]).get("status") != "passed" or read_json(paths["review_queue"]).get("open_items") != 0 or mapping.get("full_spec04_status") != "passed":
        raise ValueError("Spec 04-D closure artifacts are not passed")
    manifest = read_json(run / "manifests/run_manifest.json")
    drift = [item["path"] for item in manifest.get("files", []) if not (run / item["path"]).is_file() or sha256_file(run / item["path"]) != item["sha256"]]
    if drift or not manifest.get("immutable_after_publication"):
        raise ValueError(f"immutable run drift: {drift[:8]}")
    return {"status": "passed", "run_id": stage["run_id"], "producer_mode": stage["producer_mode"], "full_spec04_status": "passed", "included_source_atoms": len(included), "render_nodes": len(plan["nodes"]), "media_parent_binding": media_binding, "volume_partition": volume_summary, "toc_renderability": toc_summary, "structure_source_integrity": structure_integrity, "pedagogical_layout": pedagogical_summary, "open_reviews": 0}


def preflight_command(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    _, records = read_ledger(args.parent_ledger.resolve())
    policy = read_json(args.render_policy.resolve())
    validate_policy(policy)
    result = preflight_data(records, read_json(args.media_representation_plan.resolve()), policy)
    write_json(args.report.resolve(), result)
    return result, 0 if result["status"] == "passed" else 4


def evaluate_promotion(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    run = args.run_dir.resolve()
    output = args.output.resolve()
    stage_path = run / "manifests/spec04d_render_plan_stage_manifest.json"
    stage = read_json(stage_path)
    checks: list[dict[str, Any]] = []
    artifacts: dict[str, dict[str, Any]] = {}

    def check(check_id: str, function) -> None:
        try:
            detail = function()
            checks.append({"check_id": check_id, "status": "passed", "detail": detail})
        except Exception as exc:
            checks.append({"check_id": check_id, "status": "failed", "error": str(exc)})

    execution = load_module("execution_capability.py", "execution_capability_spec04d_gate")
    skill_root = Path(__file__).parents[1].resolve()
    evaluator_path = (args.evaluator_capability_output.resolve() if args.evaluator_capability_output else output.with_suffix(".evaluator-capability.json"))
    evaluator = execution.build_manifest(
        manifest_id=f"{args.promotion_id}-evaluator-capability", skill_root=skill_root,
        entrypoints=[
            ("promotion_evaluator", Path(__file__).with_name("stage_promotion_gate.py").resolve()),
            ("render_plan_contract_core", Path(__file__).resolve()),
            ("execution_capability_core", Path(__file__).with_name("execution_capability.py").resolve()),
        ],
        resources=capability_resources(skill_root, Path(json.loads((run / "precommit/execution_capability_manifest.json").read_text())["resources"][-1]["path"])),
        invocation=["stage_promotion_gate.py", "evaluate-spec04d-render-plan", "--run-dir", str(run), "--promotion-id", args.promotion_id, "--lineage-key", args.lineage_key, "--evaluator-capability-output", str(evaluator_path), "--output", str(output)],
        producer="stage-promotion-gate/spec04d-1.1.0",
    )
    write_json(evaluator_path, evaluator)

    def stage_shape() -> dict[str, Any]:
        if stage.get("schema_version") != STAGE_SCHEMA or stage.get("stage_kind") != "spec04d_render_plan_contract":
            raise ValueError("unsupported Spec 04-D stage manifest")
        if stage.get("status") != "passed" or stage.get("full_spec04_status") != "passed" or stage.get("producer_mode") not in {"formal_native", "migration_compatibility"}:
            raise ValueError("Spec 04-D stage status/class is invalid")
        return {"producer_mode": stage["producer_mode"], "full_spec04_status": stage["full_spec04_status"]}

    def hashes() -> dict[str, Any]:
        for role in ("execution_capability_E", "decision_index_D", "ledger_L", "render_plan", "volume_partition_plan", "semantic_mapping_ledger", "review_queue", "validation", "preflight"):
            path = run / stage[role]["path"]
            if not path.is_file() or sha256_file(path) != stage[role]["sha256"]:
                raise ValueError(f"missing or drifted stage artifact: {role}")
            artifacts["producer_execution_capability" if role == "execution_capability_E" else role] = {"path": str(path), "sha256": sha256_file(path)}
        manifest = run / "manifests/run_manifest.json"
        artifacts["run_manifest"] = {"path": str(manifest), "sha256": sha256_file(manifest)}
        return {"artifacts": len(artifacts)}

    def decision_inheritance() -> dict[str, Any]:
        child = read_json(Path(artifacts["decision_index_D"]["path"]))
        closed_index(child)
        parent_path = (run / child["parent_index_ref"]).resolve()
        if not parent_path.is_file() or sha256_file(parent_path) != child["parent_index_hash"]:
            raise ValueError("parent decision index is absent or drifted")
        parent = read_json(parent_path)
        p = {item["decision_id"]: item for item in parent["decisions"]}
        c = {item["decision_id"]: item for item in child["decisions"]}
        added = set(c) - set(p)
        if len(added) != 1 or any(c[key] != value for key, value in p.items()) or child["version"] != parent["version"] + 1:
            raise ValueError("decision inheritance is not cumulative and single-step")
        return {"inherited": len(p), "added": sorted(added)}

    def ledger_identity() -> dict[str, Any]:
        header, records = read_ledger(Path(artifacts["ledger_L"]["path"]))
        if header.get("ledger_checkpoint") != "semantic_frozen" or header.get("spec_status") != "passed" or header.get("canonical_decision_index_hash") != artifacts["decision_index_D"]["sha256"]:
            raise ValueError("semantic-frozen ledger identity/status is invalid")
        if header.get("spec04d_render_plan", {}).get("render_plan_sha256") != artifacts["render_plan"]["sha256"]:
            raise ValueError("ledger does not bind exact render plan")
        return {"records": len(records), "payload_hash": header["current_ledger_hash"]}

    def parents_active() -> dict[str, Any]:
        expected = {"spec04c": "spec04c_construct_binding_contract", "spec04a": "spec04a_structure_contract", "spec03": "spec03_media_contract"}
        for key, stage_kind in expected.items():
            parent = stage["parent_promotions"][key]
            selected = verify_selection(Path(parent["registry_path"]), parent["lineage_key"], Path(parent["manifest_path"]), stage_kind)
            if selected["promotion_id"] != parent["promotion_id"] or selected["promotion_class"] != stage["producer_mode"]:
                raise ValueError(f"inactive or class-drifted parent: {key}")
            artifacts[f"parent_{key}_promotion"] = {"path": parent["manifest_path"], "sha256": parent["manifest_sha256"]}
        return {"active_parents": 3}

    def exact_cross_stage_contract() -> dict[str, Any]:
        plan = read_json(Path(artifacts["render_plan"]["path"]))
        parent04c = read_json(Path(stage["parent_promotions"]["spec04c"]["manifest_path"]))
        binding_path = Path(parent04c["promoted_artifacts"]["construct_binding_ledger"]["path"])
        bindings = read_json(binding_path)["bindings"]
        for binding in bindings:
            matches = [node for node in plan["nodes"] if set(node["source_block_ids"]) == set(binding["source_block_ids"]) and node["target_construct"] == binding["target_construct"] and node["construct_parameters"] == binding["construct_parameters"]]
            if len(matches) != 1:
                raise ValueError(f"construct binding was not inherited exactly once: {binding['binding_id']}")
        parent03 = read_json(Path(stage["parent_promotions"]["spec03"]["manifest_path"]))
        media_binding = validate_media_parent_binding(plan, parent03)
        media_plan = read_json(Path(parent03["promoted_artifacts"]["media_representation_plan"]["path"]))
        media_nodes = [node for node in plan["nodes"] if node["node_kind"] == "media"]
        by_rep = {node["payload"]["media_binding"]["representation_id"]: node for node in media_nodes}
        closed = [rep for rep in media_plan["representations"] if rep["status"] == "closed"]
        included_closed = [rep for rep in closed if rep["representation_id"] in by_rep]
        if any(set(by_rep[rep["representation_id"]]["source_block_ids"]) != set(rep["source_block_ids"]) for rep in included_closed):
            raise ValueError("one-to-many media fragments were not preserved exactly")
        if any(node["payload"]["media_binding"]["artifact_sha256"] != node["payload"]["artifact_sha256"] for node in media_nodes):
            raise ValueError("media binding artifact hash drift")
        return {"construct_bindings": len(bindings), "media_nodes": media_binding["media_nodes"], "multi_fragment_media_nodes": sum(len(node["source_block_ids"]) > 1 for node in media_nodes)}

    def live_validation() -> dict[str, Any]:
        return validate_run(run)

    def toc_renderability_gate() -> dict[str, Any]:
        plan = read_json(Path(artifacts["render_plan"]["path"]))
        parent04c = read_json(Path(stage["parent_promotions"]["spec04c"]["manifest_path"]))
        capability_path = Path(parent04c["promoted_artifacts"]["template_capability_manifest"]["path"])
        capability = read_json(capability_path)
        expected = toc_capability_binding(capability)
        if plan.get("toc_capability_binding") != expected:
            raise ValueError("render plan TOC capability binding differs from the active Spec 04-C template evidence")
        return validate_toc_renderability(plan["nodes"], expected)

    def structure_source_integrity_gate() -> dict[str, Any]:
        plan = read_json(Path(artifacts["render_plan"]["path"]))
        _, records = read_ledger(Path(artifacts["ledger_L"]["path"]))
        producer_manifest = read_json(Path(artifacts["producer_execution_capability"]["path"]))
        policy_resources = [item for item in producer_manifest.get("resources", []) if item.get("role") == "render_policy"]
        if len(policy_resources) != 1:
            raise ValueError("producer capability does not bind exactly one render policy")
        policy_path = Path(policy_resources[0]["path"])
        policy = read_json(policy_path)
        structure_promotion = read_json(Path(stage["parent_promotions"]["spec04a"]["manifest_path"]))
        outline = read_json(Path(structure_promotion["promoted_artifacts"]["source_outline_ledger"]["path"]))
        if plan.get("structure_source_role_contract", {}).get("render_policy_sha256") != sha256_file(policy_path):
            raise ValueError("render plan does not bind the exact structure source-role policy")
        return validate_structure_source_integrity(plan["nodes"], outline, records, policy)

    def volume_partition_gate() -> dict[str, Any]:
        plan = read_json(Path(artifacts["render_plan"]["path"]))
        partition = read_json(Path(artifacts["volume_partition_plan"]["path"]))
        if plan.get("volume_partition_plan") != partition:
            raise ValueError("promoted render plan/volume partition payload mismatch")
        if plan.get("volume_partition_plan_sha256") != artifacts["volume_partition_plan"]["sha256"]:
            raise ValueError("promoted render plan/volume partition hash mismatch")
        return validate_volume_partition_plan(partition, plan["nodes"])

    def pedagogical_layout_gate() -> dict[str, Any]:
        plan = read_json(Path(artifacts["render_plan"]["path"]))
        producer_manifest = read_json(Path(artifacts["producer_execution_capability"]["path"]))
        policy_resources = [item for item in producer_manifest.get("resources", []) if item.get("role") == "render_policy"]
        if len(policy_resources) != 1:
            raise ValueError("producer capability does not bind exactly one render policy")
        policy = read_json(Path(policy_resources[0]["path"]))
        contract = plan.get("pedagogical_layout_contract")
        if contract != policy.get("pedagogical_layout"):
            raise ValueError("promoted render plan pedagogical contract differs from the bound render policy")
        if contract is not None:
            source_path = Path(contract["source_ref"])
            if not source_path.is_file() or sha256_file(source_path) != contract["source_sha256"]:
                raise ValueError("promoted pedagogical layout source artifact is absent or drifted")
            source = read_json(source_path)
            for key in ("heading_presentations", "numbering_map", "response_groups", "review", "invariants"):
                if source.get(key) != contract.get(key):
                    raise ValueError(f"promoted pedagogical layout source differs: {key}")
        return validate_pedagogical_render_nodes(plan["nodes"], contract)

    def acyclic() -> dict[str, Any]:
        index = read_json(Path(artifacts["decision_index_D"]["path"]))
        values = json.dumps(index, ensure_ascii=False)
        plan = read_json(Path(artifacts["render_plan"]["path"]))
        forbidden = [artifacts["ledger_L"]["sha256"], artifacts["render_plan"]["sha256"], plan["deterministic_payload_hash"]]
        if any(value in values for value in forbidden):
            raise ValueError("decision index references descendant render/ledger identity")
        return {"commit_order": stage["commit_order"]}

    def producer_capability() -> dict[str, Any]:
        return execution.validate_manifest(Path(artifacts["producer_execution_capability"]["path"]))

    def evaluator_capability() -> dict[str, Any]:
        result = execution.validate_manifest(evaluator_path)
        artifacts["evaluator_execution_capability"] = {"path": str(evaluator_path), "sha256": sha256_file(evaluator_path)}
        return result

    check("S4D-PG-H01-stage-shape-and-full-spec04-status", stage_shape)
    check("S4D-PG-H02-stage-artifact-hashes", hashes)
    check("S4D-PG-H03-decision-closure-and-inheritance", decision_inheritance)
    check("S4D-PG-H04-semantic-frozen-ledger-identity", ledger_identity)
    check("S4D-PG-H05-active-spec03-spec04a-spec04c-parents", parents_active)
    check("S4D-PG-H06-exact-construct-and-media-inheritance", exact_cross_stage_contract)
    check("S4D-PG-H07-live-complete-render-plan-validation", live_validation)
    check("S4D-PG-H08-E-to-D-to-L-to-M-acyclicity", acyclic)
    check("S4D-PG-H09-live-producer-execution-capability", producer_capability)
    check("S4D-PG-H10-live-evaluator-execution-capability", evaluator_capability)
    check("S4D-PG-H11-template-toc-renderability", toc_renderability_gate)
    check("S4D-PG-H12-structure-anchor-text-integrity", structure_source_integrity_gate)
    check("S4D-PG-H13-volume-partition-contract", volume_partition_gate)
    check("S4D-PG-H14-pedagogical-layout-contract", pedagogical_layout_gate)
    disposition = "promoted" if all(item["status"] == "passed" for item in checks) else "rejected"
    manifest = {
        "schema_version": "stage-promotion-manifest/1.1", "promotion_id": args.promotion_id, "lineage_key": args.lineage_key,
        "evaluated_at": now(), "evaluator": "stage-promotion-gate/spec04d-1.1.0", "stage_kind": "spec04d_render_plan_contract",
        "run_dir": str(run), "stage_manifest": {"path": str(stage_path), "sha256": sha256_file(stage_path)},
        "disposition": disposition, "promotion_class": stage.get("producer_mode", "undetermined"),
        "producer_execution_provenance": "live_verified" if next(item for item in checks if item["check_id"] == "S4D-PG-H09-live-producer-execution-capability")["status"] == "passed" else "unverified",
        "evaluator_capability": {"path": str(evaluator_path), "sha256": sha256_file(evaluator_path), "payload_hash": evaluator["payload_hash"]},
        "checks": checks, "summary": {"checks": len(checks), "passed": sum(item["status"] == "passed" for item in checks), "failed": sum(item["status"] == "failed" for item in checks)},
        "promoted_artifacts": artifacts if disposition == "promoted" else {},
        "consumer_rule": "Spec 05 must consume this exact active render plan and volume partition mechanically; semantic roles, constructs, payload sources, media bindings, output anchors, volume cardinality, and cut point are frozen.",
        "scope_limit": "Full Spec 04 only; no LaTeX generation, compile, layout repair, or Spec 06 final-page acceptance is evaluated.",
    }
    write_json(output, manifest)
    return manifest, 0 if disposition == "promoted" else 4


def add_produce_args(parser: argparse.ArgumentParser) -> None:
    for name in ("parent-ledger", "parent-decision-index", "construct-binding-ledger", "template-capability-manifest", "source-outline-ledger", "final-toc-plan", "media-evidence-ledger", "media-representation-plan", "source-pdf", "promotion-registry", "parent-04c-promotion", "structure-promotion", "media-promotion", "render-policy", "output-dir"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("parent-04c-lineage", "structure-lineage", "media-lineage", "ledger-snapshot-id", "decision-snapshot-id", "stage-decision-id", "run-id"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--ledger-version", type=int, required=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    add_produce_args(sub.add_parser("produce"))
    validate = sub.add_parser("validate-run")
    validate.add_argument("--run-dir", type=Path, required=True)
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--parent-ledger", type=Path, required=True)
    preflight.add_argument("--media-representation-plan", type=Path, required=True)
    preflight.add_argument("--render-policy", type=Path, required=True)
    preflight.add_argument("--report", type=Path, required=True)
    prepare = sub.add_parser("prepare-policy-review-task")
    prepare.add_argument("--parent", type=Path, required=True)
    prepare.add_argument("--structure", type=Path, required=True)
    prepare.add_argument("--media", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    project = sub.add_parser("project-policy-review")
    project.add_argument("--task", type=Path, required=True)
    project.add_argument("--compact-review", type=Path, required=True)
    project.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "produce":
            result, code = produce(args)
        elif args.command == "validate-run":
            result, code = validate_run(args.run_dir), 0
        elif args.command == "preflight":
            result, code = preflight_command(args)
        elif args.command == "prepare-policy-review-task":
            result, code = prepare_policy_review_task(args), 0
        else:
            result, code = project_policy_review_command(args), 0
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return code
    except Exception as exc:
        print(json.dumps({"status": "failed", "tool": VERSION, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
