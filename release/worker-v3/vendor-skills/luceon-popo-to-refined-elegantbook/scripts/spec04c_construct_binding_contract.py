#!/usr/bin/env python3
"""Produce the Spec 04-C template-construct binding contract.

This stage consumes the active Spec 04-B promotion and a closed review bundle.
It extracts the actual template capability manifest and binds only the already
confirmed teaching groups and standalone semantic labels to existing template
constructs.  It does not create render nodes, payloads, LaTeX, or reconstruct
formulae/tables.
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
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


VERSION = "spec04c-construct-binding-contract/1.4.1"
CONTRACT_SCHEMA = "spec04c-construct-binding-contract/1.0"
STAGE_SCHEMA = "spec04c-construct-stage-manifest/1.0"
MANIFEST_SCHEMA = "template-capability-manifest/2.0"
COMPACT_TASK_SCHEMA = "luceon.worker-v3-spec04c-compact-task/v1"
COMPACT_REVIEW_SCHEMA = "luceon.worker-v3-spec04c-compact-review/v1"
FORBIDDEN_KEYS = {
    "render_plan", "render_node_id", "payload", "payload_hash", "latex",
    "formula_reconstruction", "table_reconstruction", "output_anchor",
}
TOC_ENTRY_DEPTHS = {
    "chapter": 0,
    "section": 1,
    "subsection": 2,
    "subsubsection": 3,
    "paragraph": 4,
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


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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


def load_module(filename: str, module_name: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load required core: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_ledger(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with path.open(encoding="utf-8") as stream:
        header = json.loads(next(stream))
        records = [json.loads(line) for line in stream if line.strip()]
    if header.get("record_type") != "ledger_header" or header.get("current_ledger_hash") != canonical_hash(records):
        raise ValueError("canonical ledger identity is invalid")
    if any(item.get("record_type") != "source_block" for item in records):
        raise ValueError("Spec 04-C requires a source-block-only canonical payload")
    return header, records


def closed_decision_index(index: dict[str, Any]) -> None:
    if index.get("spec_status") != "passed":
        raise ValueError("parent decision index is not passed")
    ids = [item.get("decision_id") for item in index.get("decisions", [])]
    if None in ids or len(ids) != len(set(ids)):
        raise ValueError("parent decision index has missing or duplicate ids")
    unresolved = [item.get("decision_id") for item in index.get("decisions", []) if item.get("status") in {"open", "stale", "invalidated"}]
    if unresolved:
        raise ValueError(f"parent decision index has unresolved decisions: {unresolved[:8]}")


def assert_no_forbidden_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        found = FORBIDDEN_KEYS & set(value)
        if found:
            raise ValueError(f"Spec 04-C contains downstream keys at {path}: {sorted(found)}")
        for key, item in value.items():
            assert_no_forbidden_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_forbidden_keys(item, f"{path}[{index}]")


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


def _member_name(intake: dict[str, Any], key: str) -> str:
    value = intake.get(key)
    if not value:
        value = intake.get("main_member" if key == "entry" else "class_member")
    if key == "class" and not value:
        value = intake.get("elegantbook_class")
    if not value:
        raise ValueError(f"template intake lacks {key} member")
    return Path(str(value)).name


def _expected_zip_sha(intake: dict[str, Any]) -> str:
    value = (
        intake.get("archive_sha256")
        or intake.get("zip_sha256")
        or intake.get("template_zip_sha256")
    )
    if not value:
        raise ValueError("template intake lacks archive sha256")
    return value


def _balanced_style_body(text: str, start: int) -> str:
    depth = 1
    pos = start
    while pos < len(text) and depth:
        char = text[pos]
        if char == "{" and (pos == 0 or text[pos - 1] != "\\"):
            depth += 1
        elif char == "}" and (pos == 0 or text[pos - 1] != "\\"):
            depth -= 1
        pos += 1
    if depth:
        raise ValueError("unbalanced tcolorbox style definition")
    return text[start:pos - 1]


def extract_toc_capability(entry_text: str, class_text: str, entry_member: str, class_member: str) -> dict[str, Any]:
    """Extract the effective TOC depth and legal body-side serialization paths.

    Class code executes before the entry preamble.  Only direct, line-level
    ``setcounter{tocdepth}`` declarations that execute before the unique
    ``tableofcontents`` call are treated as effective evidence.  Unknown depth
    never becomes a permissive guess: a plan must then use an explicit reviewed
    localized override strategy.
    """
    toc_calls = list(re.finditer(r"\\tableofcontents\b", entry_text))
    if len(toc_calls) != 1:
        raise ValueError(f"template must expose exactly one deterministic tableofcontents call; found {len(toc_calls)}")
    entry_before_toc = entry_text[:toc_calls[0].start()]
    declarations: list[dict[str, Any]] = []
    direct = re.compile(r"(?m)^\s*\\setcounter\{tocdepth\}\{(-?\d+)\}\s*(?:%.*)?$")
    for member, phase, text in (
        (class_member, "class_load", class_text),
        (entry_member, "entry_before_tableofcontents", entry_before_toc),
    ):
        for match in direct.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            declaration = match.group(0).strip()
            declarations.append({
                "member": member,
                "execution_phase": phase,
                "line": line,
                "declaration": declaration,
                "declaration_sha256": sha256_bytes(declaration.encode("utf-8")),
                "depth": int(match.group(1)),
            })
    effective_depth = declarations[-1]["depth"] if declarations else None
    native = [name for name, depth in TOC_ENTRY_DEPTHS.items() if effective_depth is not None and depth <= effective_depth]
    return {
        "entry_type_depths": TOC_ENTRY_DEPTHS,
        "effective_tocdepth": effective_depth,
        "effective_tocdepth_status": "explicitly_declared" if declarations else "unknown_fail_closed",
        "effective_declaration": declarations[-1] if declarations else None,
        "pre_toc_declarations": declarations,
        "tableofcontents": {
            "member": entry_member,
            "line": entry_text.count("\n", 0, toc_calls[0].start()) + 1,
        },
        "native_visible_entry_types": native,
        "serialization_strategies": {
            "native": {
                "supported": effective_depth is not None,
                "preserves_entry_type": True,
                "requires_depth_at_most_effective_tocdepth": True,
            },
            "localized_depth_override": {
                "supported": True,
                "preserves_entry_type": True,
                "preserves_pdf_outline_level": True,
                "scope": "single_toc_entry_group",
                "uses_only_standard_latex_commands": ["addtocontents", "begingroup", "setcounter", "endgroup", "addcontentsline"],
                "adds_template_api": False,
                "modifies_template_preamble_or_class": False,
            },
        },
    }


def extract_template_capabilities(template_intake: Path, template_zip: Path) -> dict[str, Any]:
    intake = read_json(template_intake)
    if sha256_file(template_zip) != _expected_zip_sha(intake):
        raise ValueError("template archive differs from template_intake")
    entry_name = _member_name(intake, "entry")
    class_name = _member_name(intake, "class")
    with zipfile.ZipFile(template_zip) as archive:
        names = archive.namelist()
        unsafe = [name for name in names if Path(name).is_absolute() or ".." in Path(name).parts]
        if unsafe:
            raise ValueError(f"template archive contains unsafe members: {unsafe[:4]}")
        by_base: dict[str, list[str]] = {}
        for name in names:
            by_base.setdefault(Path(name).name, []).append(name)
        if len(by_base.get(entry_name, [])) != 1 or len(by_base.get(class_name, [])) != 1:
            raise ValueError("template entry/class member is absent or ambiguous")
        entry_member = by_base[entry_name][0]
        class_member = by_base[class_name][0]
        entry_bytes = archive.read(entry_member)
        class_bytes = archive.read(class_member)
    entry_text = entry_bytes.decode("utf-8")
    class_text = class_bytes.decode("utf-8")
    combined = entry_text + "\n" + class_text

    body_end_token = r"\end{document}"
    if entry_text.count(body_end_token) != 1:
        raise ValueError("template entry must contain exactly one document end token")
    body_prefix = entry_text.split(body_end_token, 1)[0]
    body_marker = next(
        (line.strip() for line in reversed(body_prefix.splitlines()) if line.strip()),
        None,
    )
    if not body_marker or entry_text.count(body_marker) != 1:
        raise ValueError("template entry has no unique final body insertion marker")

    documentclass = re.search(r"\\documentclass\[([^]]*)\]\{([^}]+)\}", entry_text)
    if not documentclass:
        raise ValueError("template entry lacks a deterministic documentclass declaration")
    styles: dict[str, dict[str, Any]] = {}
    for match in re.finditer(r"(?m)([A-Za-z][A-Za-z0-9_-]*)\s*/\.style\s*=\s*\{", entry_text):
        body = _balanced_style_body(entry_text, match.end())
        name = match.group(1)
        default_title = None
        title_match = re.search(r"(?:^|,)\s*title\s*=\s*([^,}]+)", body)
        if title_match:
            default_title = title_match.group(1).strip()
        styles[name] = {
            "definition_sha256": sha256_bytes(body.encode("utf-8")),
            "default_title": default_title,
            "title_override_supported": True,
            "default_breakable": bool(re.search(r"(?:^|,)\s*breakable\s*(?:,|$)", body)),
        }
    if not styles:
        raise ValueError("template exposes no tcolorbox styles")

    sectioning = []
    for name in ("chapter", "section", "subsection", "subsubsection"):
        if re.search(rf"\\(?:titleformat|titlespacing)\{{\\{name}\}}", combined) or re.search(rf"\\{name}(?:\*|\{{)", combined):
            sectioning.extend([name, f"{name}*"])
    sectioning.extend(["paragraph", "paragraph*"])
    custom_envs = sorted(set(re.findall(r"\\newtcolorbox\{([^}]+)\}", entry_text)) | set(re.findall(r"\\(?:newenvironment|NewEnviron)\{([^}]+)\}", entry_text)))
    custom_commands = sorted(set(re.findall(r"\\(?:newcommand|renewcommand)\{\\([A-Za-z@]+)\}", entry_text)))
    hidden_envs = sorted(set(re.findall(r"\\excludecomment\{([^}]+)\}", entry_text)))
    breakable_supported = "breakable" in combined and "tcolorbox" in combined
    declared_packages = sorted({
        package.strip()
        for package_group in re.findall(r"\\(?:usepackage|RequirePackage)(?:\[[^]]*\])?\{([^}]+)\}", combined)
        for package in package_group.split(",")
        if package.strip()
    })
    standard_serialization = {
        "paragraph": {"evidence": "TeX document body primitive", "requirements": ["documentclass"]},
    }
    if "amsmath" in declared_packages or re.search(r"\\(?:begin\{equation\*?\}|\[)", combined):
        standard_serialization["display_math"] = {
            "evidence": "declared math capability",
            "requirements": ["amsmath_or_class_math_support"],
        }
    if "graphicx" in declared_packages:
        for construct in ("source_asset_image", "source_region_image"):
            standard_serialization[construct] = {
                "evidence": "graphicx package declaration",
                "requirements": ["graphicx"],
            }
    if "multicol" in declared_packages:
        standard_serialization["response_list"] = {
            "evidence": "multicol package declaration plus TeX body primitives",
            "requirements": ["multicol", "paragraph", "rule", "vspace"],
            "contract": "frozen source-bound items with one_or_two_columns_and_explicit_answer_space",
        }
    toc_capability = extract_toc_capability(entry_text, class_text, entry_member, class_member)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "generator": VERSION,
        "generated_at": now(),
        "template_intake": {"path": str(template_intake.resolve()), "sha256": sha256_file(template_intake)},
        "template_archive": {"path": str(template_zip.resolve()), "sha256": sha256_file(template_zip)},
        "entry": {"member": entry_member, "sha256": sha256_bytes(entry_bytes)},
        "class": {"member": class_member, "sha256": sha256_bytes(class_bytes)},
        "documentclass": {
            "name": documentclass.group(2).strip(),
            "options": [item.strip() for item in documentclass.group(1).split(",") if item.strip()],
            "immutable_pending_spec05": True,
        },
        "constructs": {
            "sectioning": sectioning,
            "generic_environments": ["tcolorbox"],
            "standard_serialization": standard_serialization,
            "tcolorbox_styles": styles,
            "custom_environments": custom_envs,
            "custom_commands": custom_commands,
        },
        "supported_parameters": {
            "tcolorbox": {"style": "required_existing_style", "title": True, "breakable": breakable_supported},
            "sectioning_starred": {
                "title": True, "numbered": False, "toc_default": False,
                "toc_entry_level": sorted(TOC_ENTRY_DEPTHS),
                "toc_visibility_strategy": sorted(toc_capability["serialization_strategies"]),
            },
            "source_image": {"source_path": True, "artifact_sha256": True, "width_fraction": True, "max_height_fraction": True},
            "display_math": {"source_math": True},
            "response_list": {
                "source_items": True,
                "columns": [1, 2],
                "answer_space_modes": ["inline_rule", "vertical_space"],
            },
        },
        "declared_packages": declared_packages,
        "visibility_constraints": {
            "hidden_by_default_environments": hidden_envs,
            "source_visible_content_forbidden_in_hidden_environment": True,
        },
        "toc_capability": toc_capability,
        "body_insertion": {
            "after_exact_marker": body_marker,
            "before_exact_token": body_end_token,
        },
        "body_insertion_candidate": "between the source body marker and end{document}; pending Spec 05 freeze",
        "spec05_freeze_status": "pending",
    }
    capability_payload = {
        key: value
        for key, value in manifest.items()
        if key not in {"generated_at", "capability_payload_hash"}
    }
    for runtime_bound_key in ("template_intake", "template_archive"):
        value = capability_payload.get(runtime_bound_key)
        if isinstance(value, dict):
            capability_payload[runtime_bound_key] = {
                key: item
                for key, item in value.items()
                if key != "path"
            }
    manifest["capability_payload_hash"] = canonical_hash(capability_payload)
    return manifest


def semantic_inventory(groups: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for group in groups.get("groups", []):
        items.append({
            "object_kind": "teaching_group", "object_id": group["group_id"],
            "semantic_role": group["semantic_role"], "source_block_ids": group["source_block_ids"],
        })
    for item in groups.get("standalone_labels", []):
        items.append({
            "object_kind": "standalone_label", "object_id": item["block_id"],
            "semantic_role": item["semantic_role"], "source_block_ids": [item["block_id"]],
        })
    return sorted(items, key=lambda item: (item["object_kind"], item["object_id"]))


def _compact_review_result(
    task_id: str,
    review_tasks: list[dict[str, Any]],
    *,
    use_last_option: bool,
) -> dict[str, Any]:
    decisions = []
    for row in review_tasks:
        options = row["options"]
        selected = options[-1] if use_last_option else options[0]
        decisions.append(
            {
                "task_id": row["task_id"],
                "selected_option_id": selected["option_id"],
            }
        )
    return {
        "schema_version": COMPACT_REVIEW_SCHEMA,
        "task_id": task_id,
        "review_status": "closed",
        "decisions": decisions,
        "open_reviews": [],
    }


def construct_review_task(
    *,
    groups: dict[str, Any],
    template: dict[str, Any],
    predecessor_sha256: str,
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", predecessor_sha256):
        raise ValueError("predecessor SHA-256 is invalid")
    template_payload_hash = template.get("capability_payload_hash")
    if not isinstance(template_payload_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}",
        template_payload_hash,
    ):
        raise ValueError("template capability payload hash is invalid")
    inventory = semantic_inventory(groups)
    inventory_hash = canonical_hash(inventory)
    counts = Counter(
        (item["object_kind"], item["semantic_role"])
        for item in inventory
    )
    styles = sorted(template.get("constructs", {}).get("tcolorbox_styles", {}))
    starred = sorted(
        item
        for item in template.get("constructs", {}).get("sectioning", [])
        if isinstance(item, str) and item.endswith("*")
    )
    breakable = (
        template.get("supported_parameters", {})
        .get("tcolorbox", {})
        .get("breakable")
        is True
    )
    review_tasks: list[dict[str, Any]] = []
    for candidate_index, ((object_kind, semantic_role), object_count) in enumerate(
        sorted(counts.items())
    ):
        options: list[dict[str, Any]] = []
        if object_kind == "teaching_group":
            for style in styles:
                options.append(
                    {
                        "option_id": f"option-{len(options):04d}",
                        "target_construct": "tcolorbox",
                        "construct_parameters": {
                            "style": style,
                            "breakable": breakable,
                        },
                        "layer": "book_config",
                        "selection_reason": (
                            "Selected from the frozen template tcolorbox style inventory."
                        ),
                        "why_box_or_not": (
                            "A non-empty source-supported teaching group may use an "
                            "existing template box."
                        ),
                    }
                )
        elif object_kind == "standalone_label":
            for construct in starred:
                options.append(
                    {
                        "option_id": f"option-{len(options):04d}",
                        "target_construct": construct,
                        "construct_parameters": {},
                        "layer": "core",
                        "selection_reason": (
                            "Selected from the frozen unnumbered sectioning inventory."
                        ),
                        "why_box_or_not": (
                            "A standalone label has no confirmed body, so an empty box "
                            "is forbidden."
                        ),
                    }
                )
        else:
            raise ValueError(f"unsupported semantic object kind: {object_kind}")
        if not options:
            raise ValueError(
                f"template exposes no legal construct for {object_kind}:{semantic_role}"
            )
        review_tasks.append(
            {
                "candidate_index": candidate_index,
                "task_id": f"construct:{candidate_index:04d}",
                "object_kind": object_kind,
                "semantic_role": semantic_role,
                "object_count": object_count,
                "options": options,
            }
        )
    identity = {
        "predecessor_sha256": predecessor_sha256,
        "semantic_object_inventory_hash": inventory_hash,
        "template_capability_payload_hash": template_payload_hash,
        "review_tasks": review_tasks,
    }
    task_id = "spec04c-compact-" + canonical_hash(identity)[:24]
    minimum = len(
        canonical_bytes(
            _compact_review_result(
                task_id,
                review_tasks,
                use_last_option=False,
            )
        )
    )
    maximum = len(
        canonical_bytes(
            _compact_review_result(
                task_id,
                review_tasks,
                use_last_option=True,
            )
        )
    )
    return {
        "schema_version": COMPACT_TASK_SCHEMA,
        "task_id": task_id,
        "predecessor_sha256": predecessor_sha256,
        "semantic_object_inventory_hash": inventory_hash,
        "semantic_object_count": len(inventory),
        "template_capability_payload_hash": template_payload_hash,
        "candidate_count": len(review_tasks),
        "review_tasks": review_tasks,
        "capacity": {
            "minimum_response_bytes": min(minimum, maximum),
            "maximum_response_bytes": max(minimum, maximum),
        },
        "instructions": [
            "Return exactly one selected_option_id for every task_id in supplied order.",
            "Select only an option_id declared on the same review task.",
            "Do not emit LaTeX, new constructs, template edits, or source rewrites.",
        ],
    }


def project_construct_review(
    task: dict[str, Any],
    compact_review: dict[str, Any],
) -> dict[str, Any]:
    expected_task_fields = {
        "schema_version",
        "task_id",
        "predecessor_sha256",
        "semantic_object_inventory_hash",
        "semantic_object_count",
        "template_capability_payload_hash",
        "candidate_count",
        "review_tasks",
        "capacity",
        "instructions",
    }
    if set(task) != expected_task_fields or task.get("schema_version") != COMPACT_TASK_SCHEMA:
        raise ValueError("Spec 04-C compact task is unsupported or drifted")
    expected_review_fields = {
        "schema_version",
        "task_id",
        "review_status",
        "decisions",
        "open_reviews",
    }
    if (
        set(compact_review) != expected_review_fields
        or compact_review.get("schema_version") != COMPACT_REVIEW_SCHEMA
        or compact_review.get("task_id") != task.get("task_id")
        or compact_review.get("review_status") != "closed"
        or compact_review.get("open_reviews") != []
    ):
        raise ValueError("Spec 04-C compact review is open, unsupported, or drifted")
    review_tasks = task.get("review_tasks")
    decisions = compact_review.get("decisions")
    if (
        not isinstance(review_tasks, list)
        or not isinstance(decisions, list)
        or len(review_tasks) != task.get("candidate_count")
        or len(decisions) != len(review_tasks)
    ):
        raise ValueError("Spec 04-C decisions must be complete, ordered, and total")
    rules: list[dict[str, Any]] = []
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
            raise ValueError("Spec 04-C decisions must be complete, ordered, and total")
        options = review_task.get("options")
        if not isinstance(options, list):
            raise ValueError("Spec 04-C compact task has invalid options")
        selected = [
            option
            for option in options
            if isinstance(option, dict)
            and option.get("option_id") == decision.get("selected_option_id")
        ]
        if len(selected) != 1:
            raise ValueError("Spec 04-C decisions must be complete, ordered, and in-set")
        option = selected[0]
        rules.append(
            {
                "rule_id": (
                    f"S4C-COMPACT-{candidate_index:04d}-"
                    f"{option['option_id']}"
                ),
                "rule_version": "1.0",
                "object_kind": review_task["object_kind"],
                "semantic_role": review_task["semantic_role"],
                "target_construct": option["target_construct"],
                "construct_parameters": copy.deepcopy(
                    option["construct_parameters"]
                ),
                "layer": option["layer"],
                "selection_reason": option["selection_reason"],
                "why_box_or_not": option["why_box_or_not"],
            }
        )
    compact_hash = canonical_hash(compact_review)
    return {
        "schema_version": "spec04c-construct-review-bundle/1.0",
        "review_id": str(task["task_id"]),
        "parent_binding": {},
        "semantic_object_inventory_hash": task[
            "semantic_object_inventory_hash"
        ],
        "construct_rules": rules,
        "review": {
            "status": "closed",
            "open_items": 0,
            "decision_refs": [f"compact-review::{compact_hash}"],
            "compact_review_sha256": compact_hash,
        },
    }


def verify_parent_selection(args: argparse.Namespace, parent_ledger: Path) -> dict[str, Any]:
    core = load_module("stage_promotion_gate.py", "stage_promotion_gate_spec04c")
    selected = core.verify_registry_selection(
        args.promotion_registry.resolve(), args.parent_lineage_key,
        args.parent_promotion.resolve(), "spec04b_semantic_span_contract",
        capability_verification="frozen",
    )
    promotion = selected["promotion"]
    promoted = promotion.get("promoted_artifacts", {})
    supplied = {
        "ledger_L": args.parent_ledger.resolve(),
        "decision_index_D": args.parent_decision_index.resolve(),
        "semantic_span_ledger": args.parent_semantic_span_ledger.resolve(),
        "teaching_column_group_ledger": args.parent_teaching_group_ledger.resolve(),
    }
    for role, path in supplied.items():
        item = promoted.get(role, {})
        if Path(item.get("path", "")).resolve() != path or item.get("sha256") != sha256_file(path):
            raise ValueError(f"active Spec 04-B promotion does not promote supplied {role}")
    return {
        "promotion_id": promotion["promotion_id"], "promotion_class": promotion["promotion_class"],
        "producer_execution_provenance": promotion.get("producer_execution_provenance"),
        "manifest_path": str(args.parent_promotion.resolve()), "manifest_sha256": sha256_file(args.parent_promotion.resolve()),
        "registry_path": str(args.promotion_registry.resolve()), "registry_sha256": sha256_file(args.promotion_registry.resolve()),
        "lineage_key": args.parent_lineage_key, "capability_verification": "frozen_ancestor_snapshot",
        **{role: promoted[role] for role in supplied},
    }


def build_bindings(
    bundle: dict[str, Any], groups: dict[str, Any], template: dict[str, Any], expected_parent: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if bundle.get("schema_version") != "spec04c-construct-review-bundle/1.0":
        raise ValueError("unsupported Spec 04-C review bundle")
    assert_no_forbidden_keys(bundle)
    review = bundle.get("review", {})
    if review.get("status") != "closed" or review.get("open_items") != 0 or not review.get("decision_refs"):
        raise ValueError("Spec 04-C construct review is not closed")
    binding = bundle.get("parent_binding", {})
    drift = sorted(key for key, value in expected_parent.items() if binding.get(key) != value)
    if drift:
        raise ValueError(f"Spec 04-C review bundle parent binding drifted: {drift}")
    inventory = semantic_inventory(groups)
    if bundle.get("semantic_object_inventory_hash") != canonical_hash(inventory):
        raise ValueError("Spec 04-C review bundle semantic inventory drifted")
    rules = bundle.get("construct_rules", [])
    rule_keys = [(item.get("object_kind"), item.get("semantic_role")) for item in rules]
    if None in {value for pair in rule_keys for value in pair} or len(rule_keys) != len(set(rule_keys)):
        raise ValueError("construct rules have missing or duplicate applicability")
    expected_keys = {(item["object_kind"], item["semantic_role"]) for item in inventory}
    if set(rule_keys) != expected_keys:
        raise ValueError(f"construct rule coverage differs from semantic inventory: missing={sorted(expected_keys-set(rule_keys))} extra={sorted(set(rule_keys)-expected_keys)}")
    rules_by_key = {key: value for key, value in zip(rule_keys, rules)}
    styles = template["constructs"]["tcolorbox_styles"]
    sectioning = set(template["constructs"]["sectioning"])
    groups_by_id = {item["group_id"]: item for item in groups.get("groups", [])}
    standalone_by_id = {item["block_id"]: item for item in groups.get("standalone_labels", [])}
    bindings = []
    for item in inventory:
        key = (item["object_kind"], item["semantic_role"])
        rule = rules_by_key[key]
        construct = rule.get("target_construct")
        params = copy.deepcopy(rule.get("construct_parameters", {}))
        if rule.get("layer") not in {"core", "profile", "book_config"} or not rule.get("selection_reason") or not rule.get("rule_id"):
            raise ValueError(f"construct rule lacks ownership or rationale: {key}")
        if construct == "tcolorbox":
            if item["object_kind"] != "teaching_group":
                raise ValueError(f"EMPTY_BOX_FORBIDDEN: standalone label cannot use tcolorbox: {item['object_id']}")
            group = groups_by_id[item["object_id"]]
            if not group.get("body_block_ids"):
                raise ValueError(f"EMPTY_BOX_FORBIDDEN: {item['object_id']}")
            if params.get("style") not in styles:
                raise ValueError(f"template does not expose requested tcolorbox style: {params.get('style')}")
            if params.get("breakable") is True and not template["supported_parameters"]["tcolorbox"]["breakable"]:
                raise ValueError("template does not expose breakable tcolorbox support")
            params["title_source_block_id"] = group["marker_block_id"]
            source_evidence_ids = group.get("source_evidence_ids", [])
        elif construct in sectioning and construct.endswith("*"):
            if item["object_kind"] != "standalone_label":
                raise ValueError("starred local-heading fallback is limited to standalone labels in Spec 04-C")
            params = {"title_source_block_id": item["object_id"], "numbered": False, "toc": False}
            source_evidence_ids = standalone_by_id[item["object_id"]].get("source_evidence_ids", [])
        else:
            raise ValueError(f"template does not expose requested construct: {construct}")
        if not source_evidence_ids:
            raise ValueError(f"construct binding lacks inherited source-page evidence: {item['object_id']}")
        bindings.append({
            "binding_id": f"construct::{item['object_kind']}::{item['object_id']}",
            **item,
            "target_construct": construct,
            "construct_parameters": params,
            "template_capability_payload_hash": template["capability_payload_hash"],
            "rule_id": rule["rule_id"], "rule_version": rule.get("rule_version", "1.0"),
            "layer": rule["layer"], "selection_reason": rule["selection_reason"],
            "why_box_or_not": rule.get("why_box_or_not"),
            "source_evidence_ids": source_evidence_ids,
            "review_status": "closed", "decision_refs": review["decision_refs"],
        })
    contract = {
        "schema_version": CONTRACT_SCHEMA, "contract_id": bundle["review_id"], "generated_at": now(),
        "slice_status": "passed", "full_spec04_status": "not_evaluated",
        "parent": expected_parent, "template_capability_payload_hash": template["capability_payload_hash"],
        "bindings": bindings,
        "prohibitions": ["render_plan_generation", "render_payload_generation", "latex_generation", "formula_reconstruction", "table_reconstruction", "upstream_cleaning_rewrite"],
        "summary": {
            "semantic_objects": len(inventory), "construct_bindings": len(bindings),
            "teaching_group_bindings": sum(item["object_kind"] == "teaching_group" for item in bindings),
            "standalone_heading_bindings": sum(item["object_kind"] == "standalone_label" for item in bindings),
            "boxed_bindings": sum(item["target_construct"] == "tcolorbox" for item in bindings),
            "open_reviews": 0,
            "constructs": dict(sorted(Counter(item["target_construct"] for item in bindings).items())),
            "styles": dict(sorted(Counter(item["construct_parameters"].get("style") for item in bindings if item["target_construct"] == "tcolorbox").items())),
        },
    }
    queue = {"schema_version": "construct-binding-review-queue/1.0", "generated_at": contract["generated_at"], "status": "closed", "open_items": 0, "items": []}
    assert_no_forbidden_keys(contract)
    return contract, queue


def validation_report(contract: dict[str, Any], template: dict[str, Any], queue: dict[str, Any]) -> dict[str, Any]:
    bindings = contract["bindings"]
    styles = template["constructs"]["tcolorbox_styles"]
    checks = [
        ("S4C-H01-active-spec04b-parent-consumed", bool(contract["parent"]["promotion_id"])),
        ("S4C-H02-semantic-object-bound-once", len(bindings) == len({(item["object_kind"], item["object_id"]) for item in bindings}) == contract["summary"]["semantic_objects"]),
        ("S4C-H03-template-capability-extracted", bool(styles) and bool(template["capability_payload_hash"])),
        ("S4C-H04-construct-exists", all(item["target_construct"] == "tcolorbox" or item["target_construct"] in template["constructs"]["sectioning"] for item in bindings)),
        ("S4C-H05-box-style-exists", all(item["construct_parameters"].get("style") in styles for item in bindings if item["target_construct"] == "tcolorbox")),
        ("S4C-H06-no-empty-box", all(item["object_kind"] == "teaching_group" for item in bindings if item["target_construct"] == "tcolorbox")),
        ("S4C-H07-template-manifest-bound", all(item["template_capability_payload_hash"] == template["capability_payload_hash"] for item in bindings)),
        ("S4C-H08-no-open-review", queue["open_items"] == contract["summary"]["open_reviews"] == 0),
        ("S4C-H09-no-render-payload-or-latex", set(contract["prohibitions"]) >= {"render_plan_generation", "render_payload_generation", "latex_generation"}),
        ("S4C-H10-full-spec04-not-claimed", contract["full_spec04_status"] == "not_evaluated"),
        ("S4C-H11-toc-capability-explicit", (
            template.get("toc_capability", {}).get("effective_tocdepth_status") in {"explicitly_declared", "unknown_fail_closed"}
            and template.get("toc_capability", {}).get("serialization_strategies", {}).get("localized_depth_override", {}).get("supported") is True
            and template.get("toc_capability", {}).get("serialization_strategies", {}).get("localized_depth_override", {}).get("adds_template_api") is False
        )),
    ]
    items = [{"check_id": key, "status": "passed" if result else "failed"} for key, result in checks]
    return {
        "schema_version": "spec04c-construct-binding-validation/1.0", "generated_at": now(),
        "status": "passed" if all(result for _, result in checks) else "failed", "checks": items,
        "summary": {"checks": len(items), "passed": sum(item["status"] == "passed" for item in items), "failed": sum(item["status"] == "failed" for item in items)},
    }


def capability_resources(skill_root: Path, review_bundle: Path) -> list[tuple[str, Path]]:
    names = [
        "spec04c-construct-review-bundle.schema.json", "template-capability-manifest.schema.json",
        "spec04c-construct-binding-contract.schema.json", "spec04c-construct-stage-manifest.schema.json",
        "execution-capability-manifest.schema.json",
    ]
    return [("machine_schema", skill_root / "schemas" / name) for name in names] + [("book_configuration", review_bundle)]


def capability_invocation(args: argparse.Namespace) -> list[str]:
    result = ["spec04c_construct_binding_contract.py", "produce"]
    for name in (
        "parent_ledger", "parent_decision_index", "parent_semantic_span_ledger", "parent_teaching_group_ledger",
        "source_pdf", "template_intake", "template_zip", "promotion_registry", "parent_promotion", "review_bundle", "output_dir",
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
    parent_ledger = args.parent_ledger.resolve()
    parent_index_path = args.parent_decision_index.resolve()
    span_path = args.parent_semantic_span_ledger.resolve()
    groups_path = args.parent_teaching_group_ledger.resolve()
    source_pdf = args.source_pdf.resolve()
    review_path = args.review_bundle.resolve()
    header, records = read_ledger(parent_ledger)
    parent_index = read_json(parent_index_path)
    closed_decision_index(parent_index)
    if header.get("canonical_decision_index_hash") != sha256_file(parent_index_path):
        raise ValueError("parent ledger is not bound to supplied decision index")
    if header.get("material_identity", {}).get("source_pdf_sha256") != sha256_file(source_pdf):
        raise ValueError("source PDF differs from Spec 04-B ledger")
    parent = verify_parent_selection(args, parent_ledger)
    groups = read_json(groups_path)
    spans = read_json(span_path)
    if groups.get("semantic_span_contract_sha256") != sha256_file(span_path) or groups.get("open_reviews") != 0 or spans.get("slice_status") != "passed":
        raise ValueError("Spec 04-B semantic artifacts are not exact and closed")
    template = extract_template_capabilities(args.template_intake.resolve(), args.template_zip.resolve())
    template_path = output / "template/template_capability_manifest.json"
    write_json(template_path, template)
    expected_parent = {
        "ledger_snapshot_id": header.get("ledger_snapshot_id"), "ledger_payload_hash": header.get("current_ledger_hash"),
        "source_pdf_sha256": sha256_file(source_pdf), "promotion_id": parent["promotion_id"],
        "promotion_manifest_sha256": parent["manifest_sha256"], "semantic_span_ledger_sha256": sha256_file(span_path),
        "teaching_column_group_ledger_sha256": sha256_file(groups_path), "template_intake_sha256": sha256_file(args.template_intake.resolve()),
        "template_zip_sha256": sha256_file(args.template_zip.resolve()),
    }
    bundle = read_json(review_path)
    contract, queue = build_bindings(bundle, groups, template, expected_parent)

    skill_root = Path(__file__).parents[1].resolve()
    execution = load_module("execution_capability.py", "execution_capability_spec04c")
    capability_path = output / "precommit/execution_capability_manifest.json"
    capability = execution.build_manifest(
        manifest_id=f"{args.run_id}-producer-capability", skill_root=skill_root,
        entrypoints=[
            ("stage_producer", Path(__file__).resolve()),
            ("execution_capability_core", Path(__file__).with_name("execution_capability.py").resolve()),
            ("promotion_selection_core", Path(__file__).with_name("stage_promotion_gate.py").resolve()),
        ],
        resources=capability_resources(skill_root, review_path), invocation=capability_invocation(args), producer=VERSION,
    )
    write_json(capability_path, capability)
    execution.validate_manifest(capability_path)
    precommit = [
        {"role": "parent_canonical_ledger", "path": str(parent_ledger), "sha256": sha256_file(parent_ledger)},
        {"role": "parent_decision_index", "path": str(parent_index_path), "sha256": sha256_file(parent_index_path)},
        {"role": "parent_semantic_span_ledger", "path": str(span_path), "sha256": sha256_file(span_path)},
        {"role": "parent_teaching_group_ledger", "path": str(groups_path), "sha256": sha256_file(groups_path)},
        {"role": "source_pdf", "path": str(source_pdf), "sha256": sha256_file(source_pdf)},
        {"role": "active_spec04b_promotion", "path": parent["manifest_path"], "sha256": parent["manifest_sha256"]},
        {"role": "promotion_registry", "path": parent["registry_path"], "sha256": parent["registry_sha256"]},
        {"role": "template_intake", "path": str(args.template_intake.resolve()), "sha256": sha256_file(args.template_intake.resolve())},
        {"role": "template_archive", "path": str(args.template_zip.resolve()), "sha256": sha256_file(args.template_zip.resolve())},
        {"role": "template_capability_manifest", "path": "template/template_capability_manifest.json", "sha256": sha256_file(template_path)},
        {"role": "construct_review_bundle", "path": str(review_path), "sha256": sha256_file(review_path)},
        {"role": "execution_capability", "path": "precommit/execution_capability_manifest.json", "sha256": sha256_file(capability_path)},
    ]
    event = {
        "decision_id": args.stage_decision_id, "status": "closed", "decided_at": now(),
        "rule_id": "SM-H05/SM-H06/SM-H13/SM-H14/SPEC04C-CONSTRUCT-BINDING-COMMIT",
        "decision_type": "reviewed_template_construct_binding_commit",
        "scope": "Bind exact Spec 04-B teaching semantic objects to existing template constructs only.",
        "evidence": precommit, "review_refs": bundle["review"]["decision_refs"],
        "prohibitions": contract["prohibitions"], "supersedes": [], "invalidated_by": None,
    }
    event_path = output / "decisions/construct_binding_decisions.jsonl"
    write_jsonl(event_path, [event])
    decisions = copy.deepcopy(parent_index.get("decisions", []))
    if args.stage_decision_id in {item.get("decision_id") for item in decisions}:
        raise ValueError(f"stage decision id already exists: {args.stage_decision_id}")
    decisions.append({
        "decision_id": args.stage_decision_id, "event_file": "decisions/construct_binding_decisions.jsonl",
        "rule_id": event["rule_id"], "status": "closed", "supersedes": [], "invalidated_by": None,
    })
    statuses = Counter(item.get("status") for item in decisions)
    index = {
        "schema_version": "canonical-decision-index/1.1", "decision_index_id": parent_index["decision_index_id"],
        "snapshot_id": args.decision_snapshot_id, "version": int(parent_index["version"]) + 1, "generated_at": now(),
        "parent_index_ref": relative(output, parent_index_path), "parent_index_hash": sha256_file(parent_index_path),
        "acyclic_commit_rule": "evidence_or_parent_then_decision_index_D_then_child_artifact_L",
        "spec_status": "passed", "evidence_committed_before_index": precommit,
        "decision_event_files": [{"path": "decisions/construct_binding_decisions.jsonl", "sha256": sha256_file(event_path), "decision_ids": [args.stage_decision_id]}],
        "decisions": decisions,
        "summary": {"closed": statuses["closed"], "superseded": statuses["superseded"], "open": 0, "stale": 0, "invalidated": 0},
    }
    decision_path = output / "decisions/canonical_decision_index.json"
    write_json(decision_path, index)
    decision_sha = sha256_file(decision_path)

    contract["canonical_decision_index_sha256"] = decision_sha
    contract["template_capability_manifest_sha256"] = sha256_file(template_path)
    contract_path = output / "semantic/construct_binding_ledger.json"
    write_json(contract_path, contract)
    queue_path = output / "semantic/construct_review_queue.json"
    write_json(queue_path, queue)
    report = validation_report(contract, template, queue)
    report_path = output / "reports/spec04c_construct_binding_validation.json"
    write_json(report_path, report)
    if report["status"] != "passed":
        raise ValueError("internal Spec 04-C validation failed")

    binding_by_block = {block_id: item["binding_id"] for item in contract["bindings"] for block_id in item["source_block_ids"]}
    records_out = copy.deepcopy(records)
    for record in records_out:
        if record["block_id"] in binding_by_block:
            record["spec04c_construct_binding_id"] = binding_by_block[record["block_id"]]
            record["spec04c_construct_decision_refs"] = [args.stage_decision_id]
    header_out = copy.deepcopy(header)
    header_out.update({
        "generated_at": now(), "updated_at": now(), "ledger_snapshot_id": args.ledger_snapshot_id,
        "ledger_version": args.ledger_version, "parent_ledger_ref": relative(output, parent_ledger),
        "parent_ledger_file_sha256": sha256_file(parent_ledger), "parent_ledger_hash": header["current_ledger_hash"],
        "canonical_decision_index_ref": "decisions/canonical_decision_index.json", "canonical_decision_index_hash": decision_sha,
        "current_ledger_hash": canonical_hash(records_out),
        "current_ledger_hash_scope": "canonical JSON hash of ordered source_block records with Spec 04-C construct-binding overlay",
        "spec04c_construct_bindings": {
            "status": "passed", "full_spec04_status": "not_evaluated", "producer": VERSION,
            "construct_binding_ledger_sha256": sha256_file(contract_path), "template_capability_manifest_sha256": sha256_file(template_path),
            "semantic_objects": contract["summary"]["semantic_objects"], "construct_bindings": contract["summary"]["construct_bindings"], "open_reviews": 0,
        },
    })
    ledger_path = output / "ledgers/canonical_block_ledger.jsonl"
    write_jsonl(ledger_path, [header_out, *records_out])
    write_json(output / "ledgers/ledger_manifest.json", {
        "schema_version": "ledger-manifest/2.3", "generated_at": now(), "ledger_id": header_out["ledger_id"],
        "ledger_version": header_out["ledger_version"], "snapshot_id": header_out["ledger_snapshot_id"],
        "artifact_path": "ledgers/canonical_block_ledger.jsonl", "artifact_sha256": sha256_file(ledger_path),
        "payload_hash": header_out["current_ledger_hash"], "parent_artifact_ref": header_out["parent_ledger_ref"],
        "parent_artifact_sha256": header_out["parent_ledger_file_sha256"], "decision_index_ref": "decisions/canonical_decision_index.json",
        "decision_index_hash": decision_sha, "spec04c_construct_binding_status": "passed", "full_spec04_status": "not_evaluated", "immutable_after_publication": True,
    })

    producer_mode = parent["promotion_class"]
    stage = {
        "schema_version": STAGE_SCHEMA, "stage_kind": "spec04c_construct_binding_contract", "run_id": args.run_id,
        "generated_at": now(), "status": "passed", "slice_status": "passed", "full_spec04_status": "not_evaluated",
        "producer": VERSION, "producer_mode": producer_mode,
        "commit_order": ["precommit_evidence_template_and_execution_capability_E", "decision_index_D", "construct_contract_and_ledger_L", "stage_manifest_M"],
        "parent_promotion": parent,
        "execution_capability_E": {"path": "precommit/execution_capability_manifest.json", "sha256": sha256_file(capability_path), "payload_hash": capability["payload_hash"]},
        "decision_index_D": {"path": "decisions/canonical_decision_index.json", "sha256": decision_sha},
        "ledger_L": {"path": "ledgers/canonical_block_ledger.jsonl", "sha256": sha256_file(ledger_path), "payload_hash": header_out["current_ledger_hash"]},
        "template_capability_manifest": {"path": "template/template_capability_manifest.json", "sha256": sha256_file(template_path), "payload_hash": template["capability_payload_hash"]},
        "construct_binding_ledger": {"path": "semantic/construct_binding_ledger.json", "sha256": sha256_file(contract_path)},
        "review_queue": {"path": "semantic/construct_review_queue.json", "sha256": sha256_file(queue_path)},
        "validation": {"path": "reports/spec04c_construct_binding_validation.json", "sha256": sha256_file(report_path)},
        "scope_prohibitions": contract["prohibitions"],
    }
    stage_path = output / "manifests/spec04c_construct_stage_manifest.json"
    write_json(stage_path, stage)
    run_files = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "run_manifest.json":
            run_files.append({"path": path.relative_to(output).as_posix(), "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
    write_json(output / "manifests/run_manifest.json", {
        "schema_version": "immutable-run-manifest/1.1", "run_id": args.run_id, "generated_at": now(),
        "status": "passed", "stage_kind": "spec04c_construct_binding_contract", "producer_mode": producer_mode,
        "immutable_after_publication": True, "files": run_files,
    })
    return stage, 0


def validate_run(run_dir: Path) -> dict[str, Any]:
    run = run_dir.resolve()
    stage = read_json(run / "manifests/spec04c_construct_stage_manifest.json")
    if stage.get("schema_version") != STAGE_SCHEMA or stage.get("stage_kind") != "spec04c_construct_binding_contract" or stage.get("status") != "passed":
        raise ValueError("unsupported or non-passed Spec 04-C stage manifest")
    names = ["execution_capability_E", "decision_index_D", "ledger_L", "template_capability_manifest", "construct_binding_ledger", "review_queue", "validation"]
    artifacts = {}
    for name in names:
        item = stage.get(name, {})
        path = run / item.get("path", "")
        if not path.is_file() or sha256_file(path) != item.get("sha256"):
            raise ValueError(f"Spec 04-C stage artifact is missing or drifted: {name}")
        artifacts[name] = path
    execution = load_module("execution_capability.py", "execution_capability_spec04c_validate").validate_manifest(artifacts["execution_capability_E"])
    header, records = read_ledger(artifacts["ledger_L"])
    index = read_json(artifacts["decision_index_D"])
    closed_decision_index(index)
    if header.get("canonical_decision_index_hash") != sha256_file(artifacts["decision_index_D"]) or stage["ledger_L"].get("payload_hash") != header.get("current_ledger_hash"):
        raise ValueError("Spec 04-C ledger is not bound to decision index D")
    forbidden = [header.get("ledger_snapshot_id"), header.get("current_ledger_hash"), sha256_file(artifacts["ledger_L"])]
    if any(item and item in scalar_strings(index) for item in forbidden):
        raise ValueError("Spec 04-C decision index D references descendant ledger L")
    template = read_json(artifacts["template_capability_manifest"])
    contract = read_json(artifacts["construct_binding_ledger"])
    queue = read_json(artifacts["review_queue"])
    report = read_json(artifacts["validation"])
    assert_no_forbidden_keys(contract)
    if template.get("schema_version") != MANIFEST_SCHEMA or contract.get("slice_status") != "passed" or contract.get("full_spec04_status") != "not_evaluated" or queue.get("open_items") != 0 or report.get("status") != "passed":
        raise ValueError("Spec 04-C live artifacts are not closed and accurately scoped")
    if contract.get("template_capability_manifest_sha256") != sha256_file(artifacts["template_capability_manifest"]):
        raise ValueError("construct contract is not bound to exact template capability manifest")
    expected_overlay = {block_id: item["binding_id"] for item in contract["bindings"] for block_id in item["source_block_ids"]}
    actual_overlay = {item["block_id"]: item["spec04c_construct_binding_id"] for item in records if item.get("spec04c_construct_binding_id")}
    if expected_overlay != actual_overlay:
        raise ValueError("Spec 04-C canonical overlay differs from construct binding ledger")
    manifest = read_json(run / "manifests/run_manifest.json")
    drift = [item["path"] for item in manifest.get("files", []) if not (run / item["path"]).is_file() or sha256_file(run / item["path"]) != item["sha256"]]
    if manifest.get("status") != "passed" or not manifest.get("immutable_after_publication") or drift:
        raise ValueError(f"Spec 04-C immutable run manifest is invalid or drifted: {drift[:8]}")
    return {
        "status": "passed", "run_id": stage["run_id"], "producer_mode": stage["producer_mode"],
        "semantic_objects": contract["summary"]["semantic_objects"], "construct_bindings": contract["summary"]["construct_bindings"],
        "boxed_bindings": contract["summary"]["boxed_bindings"], "open_reviews": 0,
        "full_spec04_status": "not_evaluated", "producer_execution_capability": execution,
    }


def prepare_review_task(args: argparse.Namespace) -> dict[str, Any]:
    parent = args.parent.resolve()
    groups_path = parent / "semantic/teaching_column_group_ledger.json"
    spans_path = parent / "semantic/semantic_span_ledger.json"
    groups = read_json(groups_path)
    spans = read_json(spans_path)
    if (
        groups.get("semantic_span_contract_sha256") != sha256_file(spans_path)
        or groups.get("open_reviews") != 0
        or groups.get("status") != "passed"
        or spans.get("slice_status") != "passed"
    ):
        raise ValueError("Spec 04-B semantic artifacts are not exact and closed")
    template = extract_template_capabilities(
        args.template_intake.resolve(),
        args.template_zip.resolve(),
    )
    task = construct_review_task(
        groups=groups,
        template=template,
        predecessor_sha256=args.predecessor_sha256,
    )
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite review task: {output}")
    write_json(output, task)
    return {
        "status": "prepared",
        "task_sha256": sha256_file(output),
        "task_canonical_sha256": canonical_hash(task),
        "semantic_objects": task["semantic_object_count"],
        "candidates": task["candidate_count"],
        "minimum_response_bytes": task["capacity"]["minimum_response_bytes"],
        "maximum_response_bytes": task["capacity"]["maximum_response_bytes"],
    }


def project_review(args: argparse.Namespace) -> dict[str, Any]:
    task = read_json(args.task.resolve())
    compact_review = read_json(args.compact_review.resolve())
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite projected review: {output}")
    bundle = project_construct_review(task, compact_review)
    write_json(output, bundle)
    return {
        "status": "projected",
        "output_sha256": sha256_file(output),
        "output_canonical_sha256": canonical_hash(bundle),
        "construct_rules": len(bundle["construct_rules"]),
    }


def add_produce_arguments(parser: argparse.ArgumentParser) -> None:
    for name in (
        "parent-ledger", "parent-decision-index", "parent-semantic-span-ledger", "parent-teaching-group-ledger",
        "source-pdf", "template-intake", "template-zip", "promotion-registry", "parent-promotion", "review-bundle", "output-dir",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--parent-lineage-key", required=True)
    parser.add_argument("--ledger-snapshot-id", required=True)
    parser.add_argument("--ledger-version", type=int, required=True)
    parser.add_argument("--decision-snapshot-id", required=True)
    parser.add_argument("--stage-decision-id", required=True)
    parser.add_argument("--run-id", required=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    add_produce_arguments(sub.add_parser("produce"))
    validate_parser = sub.add_parser("validate-run")
    validate_parser.add_argument("--run-dir", type=Path, required=True)
    prepare_parser = sub.add_parser("prepare-review-task")
    prepare_parser.add_argument("--parent", type=Path, required=True)
    prepare_parser.add_argument("--template-intake", type=Path, required=True)
    prepare_parser.add_argument("--template-zip", type=Path, required=True)
    prepare_parser.add_argument("--predecessor-sha256", required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    project_parser = sub.add_parser("project-review")
    project_parser.add_argument("--task", type=Path, required=True)
    project_parser.add_argument("--compact-review", type=Path, required=True)
    project_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "produce":
            result, code = produce(args)
        elif args.command == "validate-run":
            result, code = validate_run(args.run_dir), 0
        elif args.command == "prepare-review-task":
            result, code = prepare_review_task(args), 0
        else:
            result, code = project_review(args), 0
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return code
    except Exception as exc:
        print(json.dumps({"status": "failed", "tool": VERSION, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
