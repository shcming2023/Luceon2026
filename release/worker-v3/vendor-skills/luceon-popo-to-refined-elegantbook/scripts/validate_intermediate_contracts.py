#!/usr/bin/env python3
"""Fail-closed validation for the four frozen ElegantBook intermediate contracts.

This validator is deliberately independent of any book title, sample id, page
number, publisher label, or known hash.  It validates identities, closure,
coverage, cross-contract bindings, and the frozen template surface.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

VERSION = "round1-contract-validator/1.1.0"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TERMINAL_LOGICAL = {"source_reconciled"}
METADATA_PATTERN = r"\\{name}\{{[^{{}}]*\}}"


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


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def package_inventory(text: str) -> list[dict[str, Any]]:
    return [
        {"package": match.group(2), "options": match.group(1) or "", "ordinal": index + 1}
        for index, match in enumerate(re.finditer(r"\\usepackage(?:\[([^]]*)\])?\{([^}]*)\}", text))
    ]


def api_inventory(text: str) -> dict[str, list[str]]:
    return {
        "newcommands": sorted(set(re.findall(r"\\newcommand\s*\{\\([A-Za-z@]+)", text))),
        "newtcolorboxes": sorted(set(re.findall(r"\\newtcolorbox\s*\{([^}]+)", text))),
        "newifs": sorted(set(re.findall(r"\\newif\\if([A-Za-z@]+)", text))),
        "newenvirons": sorted(set(re.findall(r"\\NewEnviron\s*\{([^}]+)", text))),
        "colors": sorted(set(re.findall(r"\\definecolor\s*\{([^}]+)", text))),
        "tcolorbox_styles": sorted(set(re.findall(r"^\s*([A-Za-z@]+)\s*/\.style\s*=", text, re.M))),
    }


def documentclass_inventory(text: str) -> dict[str, Any] | None:
    match = re.search(r"\\documentclass(?:\[([^]]*)\])?\{([^}]+)\}", text)
    if not match:
        return None
    return {
        "name": match.group(2),
        "options": [part.strip() for part in (match.group(1) or "").split(",") if part.strip()],
    }


def capability_constructs(capability: dict[str, Any]) -> dict[str, Any]:
    """Return one construct view for both capability-manifest/1.x and /2.0."""
    if capability.get("schema_version") == "template-capability-manifest/2.0":
        constructs = capability.get("constructs", {})
        return {
            "sectioning": constructs.get("sectioning", []),
            "native_environments": constructs.get("custom_environments", []),
            "generic_environments": constructs.get("generic_environments", []),
            "tcolorbox_styles": sorted(constructs.get("tcolorbox_styles", {})),
        }
    return {
        "sectioning": capability.get("sectioning", []),
        "native_environments": capability.get("native_environments", []),
        "generic_environments": capability.get("generic_environments", []),
        "tcolorbox_styles": capability.get("tcolorbox_styles", []),
    }


def mask_main(text: str, contract: dict[str, Any]) -> str:
    masked = text
    for name in contract.get("metadata_allowlist", {}):
        masked, count = re.subn(METADATA_PATTERN.format(name=re.escape(name)), rf"\\{name}{{<META:{name}>}}", masked, count=1)
        if name in contract.get("selected_metadata", {}) and count != 1:
            raise ValueError(f"allowlisted selected metadata macro not found exactly once: {name}")
    main = contract["main_template"]
    marker = main["body_marker"]
    end_token = main["body_end_token"]
    start = masked.index(marker) + len(marker)
    end = masked.index(end_token, start)
    return masked[:start] + "\n<BODY>\n" + masked[end:]


class Results:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def add(self, check_id: str, passed: bool, detail: str, **evidence: Any) -> None:
        item = {"check_id": check_id, "status": "passed" if passed else "failed", "detail": detail}
        if evidence:
            item["evidence"] = evidence
        self.checks.append(item)

    def guard(self, check_id: str, detail: str, fn) -> None:
        try:
            evidence = fn()
            self.add(check_id, True, detail, **(evidence or {}))
        except Exception as exc:  # validation must report all independent failures
            self.add(check_id, False, f"{detail}: {exc}")

    @property
    def passed(self) -> bool:
        return all(item["status"] == "passed" for item in self.checks)


def validate(
    ledger_path: Path,
    decision_path: Path,
    plan_path: Path,
    contract_path: Path,
    template_dir: Path,
    capability_path: Path,
) -> dict[str, Any]:
    results = Results()
    rows = load_jsonl(ledger_path)
    decision = load_json(decision_path)
    plan = load_json(plan_path)
    contract = load_json(contract_path)
    capability = load_json(capability_path)
    header = rows[0] if rows else {}
    records = rows[1:]

    results.guard("IC-H01-ledger-shape", "canonical ledger header and record types are valid", lambda: _ledger_shape(header, records))
    results.guard("IC-H02-ledger-identity", "canonical ledger payload hash and block identities are valid", lambda: _ledger_identity(header, records))
    results.guard("IC-H03-ledger-closure", "canonical ledger has no open source review", lambda: _ledger_closure(header, records))
    results.guard("IC-H04-decision-closure", "canonical decision index is unique, closed, and passed", lambda: _decision_closure(decision))
    results.guard("IC-H05-decision-acyclic", "decision index does not reference its child ledger or render plan", lambda: _decision_acyclic(decision, header, plan))
    results.guard("IC-H06-render-freeze", "render plan is planning-only, deterministic, closed, and ordered", lambda: _render_freeze(plan, capability))
    results.guard("IC-H07-render-coverage", "render plan consumes every included logical source atom exactly once", lambda: _render_coverage(records, plan))
    results.guard("IC-H08-cross-bindings", "ledger, decision index, plan, and capability hashes agree", lambda: _cross_bindings(header, decision_path, plan, capability_path))
    results.guard("IC-H09-template-contract", "template contract is frozen and selected metadata is plain text", lambda: _template_contract_shape(contract))
    results.guard("IC-H10-template-bytes", "template files, masked scaffold, class, packages, and API match the contract", lambda: _template_bytes(template_dir, contract))
    results.guard("IC-H11-template-capabilities", "planned constructs exist in the frozen capability manifest", lambda: _constructs_exist(plan, capability))

    return {
        "schema_version": "intermediate-contract-validation/1.0",
        "validator": VERSION,
        "status": "passed" if results.passed else "failed",
        "inputs": {
            "canonical_ledger": {"path": str(ledger_path), "sha256": sha256_file(ledger_path)},
            "decision_index": {"path": str(decision_path), "sha256": sha256_file(decision_path)},
            "render_plan": {"path": str(plan_path), "sha256": sha256_file(plan_path)},
            "template_contract": {"path": str(contract_path), "sha256": sha256_file(contract_path)},
            "template_dir": str(template_dir),
            "capability_manifest": {"path": str(capability_path), "sha256": sha256_file(capability_path)},
        },
        "summary": {
            "checks": len(results.checks),
            "passed": sum(item["status"] == "passed" for item in results.checks),
            "failed": sum(item["status"] == "failed" for item in results.checks),
        },
        "checks": results.checks,
    }


def _ledger_shape(header: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    required = {"record_type", "schema_version", "ledger_id", "ledger_snapshot_id", "ledger_version", "ledger_checkpoint", "current_ledger_hash", "spec_status", "summary"}
    missing = sorted(required - header.keys())
    if missing or header.get("record_type") != "ledger_header" or not str(header.get("schema_version", "")).startswith("canonical-block-ledger/"):
        raise ValueError(f"invalid header; missing={missing}")
    if not records or any(record.get("record_type") != "source_block" for record in records):
        raise ValueError("ledger must contain only source_block records after the header")
    for record in records:
        if not isinstance(record.get("pdf_physical_page"), int) or record["pdf_physical_page"] < 1:
            raise ValueError(f"invalid page on {record.get('block_id')}")
        bbox = record.get("bbox")
        if bbox is not None and (not isinstance(bbox, list) or len(bbox) != 4 or not all(isinstance(v, (int, float)) for v in bbox)):
            raise ValueError(f"invalid bbox on {record.get('block_id')}")
    return {"source_records": len(records), "ledger_checkpoint": header["ledger_checkpoint"]}


def _raw_hash(value: Any) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else canonical_bytes(value)
    return hashlib.sha256(data).hexdigest()


def _ledger_identity(header: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    identifiers = [record.get("block_id") for record in records]
    duplicates = [key for key, count in Counter(identifiers).items() if count > 1]
    if None in identifiers or duplicates:
        raise ValueError(f"missing or duplicate block ids: {duplicates[:5]}")
    bad_content = [record["block_id"] for record in records if record.get("raw_content_sha256") != _raw_hash(record.get("raw_content"))]
    if bad_content:
        raise ValueError(f"raw content hash mismatch: {bad_content[:5]}")
    computed = canonical_hash(records)
    if header.get("current_ledger_hash") != computed:
        raise ValueError(f"payload hash mismatch: expected {header.get('current_ledger_hash')}, computed {computed}")
    return {"payload_hash": computed, "unique_block_ids": len(identifiers)}


def _ledger_closure(header: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    open_records = [record["block_id"] for record in records if record.get("review_required") or record.get("scope_status") == "needs_review"]
    summary = header.get("summary", {})
    declared = {
        key: summary[key] for key in ("open_source_review_blocks", "open_reviews")
        if key in summary
    }
    if not declared:
        raise ValueError("ledger summary declares neither open_source_review_blocks nor open_reviews")
    if any(value != 0 for value in declared.values()):
        raise ValueError(f"ledger summary reports open reviews: {declared}")
    if header.get("spec_status") != "passed" or open_records:
        raise ValueError(f"ledger is not closed; open records={open_records[:5]}")
    return {"open_source_review_blocks": 0, "accepted_summary_fields": sorted(declared)}


def _decision_closure(decision: dict[str, Any]) -> dict[str, Any]:
    if not str(decision.get("schema_version", "")).startswith("canonical-decision-index/"):
        raise ValueError("unsupported decision index schema")
    items = decision.get("decisions")
    if not isinstance(items, list):
        raise ValueError("decisions must be an array")
    identifiers = [item.get("decision_id") for item in items]
    if None in identifiers or len(set(identifiers)) != len(identifiers):
        raise ValueError("decision ids are missing or duplicated")
    statuses = Counter(item.get("status") for item in items)
    if decision.get("spec_status") != "passed" or statuses["open"] or statuses["invalidated"]:
        raise ValueError(f"decision index is not closed: {dict(statuses)}")
    summary = decision.get("summary", {})
    if summary.get("open", 0) != 0 or summary.get("invalidated", 0) != 0:
        raise ValueError("decision summary reports open or invalidated decisions")
    return {"decisions": len(items), "statuses": dict(statuses)}


def _decision_acyclic(decision: dict[str, Any], header: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    serialized = canonical_bytes(decision).decode("utf-8")
    forbidden = [header.get("current_ledger_hash"), header.get("ledger_snapshot_id"), plan.get("deterministic_payload_hash")]
    found = [value for value in forbidden if value and value in serialized]
    if found:
        raise ValueError(f"D references child L/plan identities: {found}")
    if "decision_index_D_then_child_artifact_L" not in decision.get("acyclic_commit_rule", ""):
        raise ValueError("D to L acyclic commit rule is absent")
    return {"forbidden_child_references": 0}


def _render_freeze(plan: dict[str, Any], capability: dict[str, Any]) -> dict[str, Any]:
    if not str(plan.get("schema_version", "")).startswith("render-plan/"):
        raise ValueError("unsupported render plan schema")
    if plan.get("spec_status") != "passed" or plan.get("open_reviews") != 0 or plan.get("planning_only") is not True or plan.get("latex_generated") is not False:
        raise ValueError("render plan is not a closed planning-only snapshot")
    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("render plan has no nodes")
    node_ids = [node.get("render_node_id") for node in nodes]
    if None in node_ids or len(set(node_ids)) != len(node_ids):
        raise ValueError("render node ids are missing or duplicated")
    orders = [node.get("render_order") for node in nodes]
    if orders != list(range(1, len(nodes) + 1)):
        raise ValueError("render_order must be contiguous and match array order")
    for node in nodes:
        if node.get("review_status") != "closed":
            raise ValueError(f"open render node: {node.get('render_node_id')}")
        if node.get("payload_hash") != canonical_hash(node.get("payload")):
            raise ValueError(f"payload hash mismatch: {node.get('render_node_id')}")
        if node.get("capability_manifest_sha256") != plan.get("capability_manifest_sha256"):
            raise ValueError(f"capability binding mismatch: {node.get('render_node_id')}")
    excluded = {"generated_at", "deterministic_payload_hash"} if plan.get("schema_version") == "render-plan/2.0" else {"deterministic_payload_hash", "frozen_at", "spec_status", "open_reviews"}
    payload = {key: value for key, value in plan.items() if key not in excluded}
    computed = canonical_hash(payload)
    if computed != plan.get("deterministic_payload_hash"):
        raise ValueError(f"deterministic payload hash mismatch: computed {computed}")
    return {"nodes": len(nodes), "deterministic_payload_hash": computed}


def _render_coverage(records: list[dict[str, Any]], plan: dict[str, Any]) -> dict[str, Any]:
    expected = {
        record["block_id"] for record in records
        if record.get("scope_status") == "included"
        and (plan.get("schema_version") == "render-plan/2.0" or record.get("terminal_state") in TERMINAL_LOGICAL)
    }
    actual = [block_id for node in plan["nodes"] for block_id in node["source_block_ids"]]
    counts = Counter(actual)
    duplicated = sorted(block_id for block_id, count in counts.items() if count != 1)
    missing = sorted(expected - counts.keys())
    extra = sorted(counts.keys() - expected)
    if duplicated or missing or extra:
        raise ValueError(f"coverage mismatch: missing={missing[:5]}, extra={extra[:5]}, duplicated={duplicated[:5]}")
    return {"logical_source_atoms": len(expected), "planned_source_references": len(actual)}


def _cross_bindings(header: dict[str, Any], decision_path: Path, plan: dict[str, Any], capability_path: Path) -> dict[str, Any]:
    decision_hash = sha256_file(decision_path)
    capability_hash = sha256_file(capability_path)
    capability = load_json(capability_path)
    if header.get("canonical_decision_index_hash") != decision_hash or plan.get("decision_index_sha256") != decision_hash:
        raise ValueError("decision index byte hash does not bind ledger and render plan")
    if plan.get("schema_version") == "render-plan/2.0":
        if plan.get("capability_manifest_file_sha256") != capability_hash or plan.get("capability_manifest_sha256") != capability.get("capability_payload_hash"):
            raise ValueError("capability manifest file/payload hashes do not bind the render plan")
    elif header.get("template_capability_manifest_sha256") != capability_hash or plan.get("capability_manifest_sha256") != capability_hash:
        raise ValueError("capability manifest byte hash does not bind ledger and render plan")
    return {"decision_index_sha256": decision_hash, "capability_manifest_file_sha256": capability_hash, "capability_manifest_payload_hash": capability.get("capability_payload_hash")}


def _template_contract_shape(contract: dict[str, Any]) -> dict[str, Any]:
    if not str(contract.get("schema_version", "")).startswith("template-contract/") or contract.get("status") != "frozen":
        raise ValueError("template contract is not frozen")
    selected = contract.get("selected_metadata")
    allowlist = contract.get("metadata_allowlist")
    if not isinstance(selected, dict) or not isinstance(allowlist, dict) or not set(selected).issubset(allowlist):
        raise ValueError("selected metadata is outside the allowlist")
    for name, value in selected.items():
        if not isinstance(value, str) or any(char in value for char in "\\{}"):
            raise ValueError(f"metadata is not plain Unicode text: {name}")
    presentation_summary = None
    if contract.get("schema_version") == "template-contract/2.0":
        presentation = contract.get("selected_presentation", {})
        assets = presentation.get("assets") if isinstance(presentation, dict) else None
        binding = contract.get("presentation_config")
        if presentation.get("schema_version") != "spec05-presentation-config/1.0" or presentation.get("status") != "approved":
            raise ValueError("template-contract/2.0 presentation config is not approved")
        if not isinstance(binding, dict) or not binding.get("ref") or not binding.get("sha256"):
            raise ValueError("template-contract/2.0 lacks a presentation config binding")
        if not isinstance(assets, dict) or set(assets) != {"cover", "logo"}:
            raise ValueError("template-contract/2.0 must bind exactly cover and logo")
        for name, item in assets.items():
            if item.get("mode") not in {"template_default", "source_region_asset", "approved_static_asset"}:
                raise ValueError(f"unsupported presentation mode: {name}")
            if item.get("decision", {}).get("status") != "closed" or item.get("compatibility", {}).get("status") != "approved":
                raise ValueError(f"presentation decision is unresolved: {name}")
            value = item.get("macro_value")
            path = Path(value or "")
            if not value or path.is_absolute() or ".." in path.parts or any(char in value for char in "\\{}"):
                raise ValueError(f"unsafe presentation macro value: {name}")
        presentation_summary = {name: assets[name]["mode"] for name in sorted(assets)}
    insertion = contract.get("body_insertion", {})
    if insertion.get("file") != "main.tex" or not insertion.get("after_exact_marker") or not insertion.get("before_exact_token"):
        raise ValueError("unsupported or incomplete body insertion contract")
    return {"selected_metadata": sorted(selected), "immutable_files": len(contract.get("immutable_files", [])), "selected_presentation": presentation_summary}


def _template_bytes(template_dir: Path, contract: dict[str, Any]) -> dict[str, Any]:
    if not template_dir.is_dir():
        raise ValueError(f"template directory does not exist: {template_dir}")
    for item in contract.get("immutable_files", []):
        path = template_dir / item["path"]
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            raise ValueError(f"immutable template member drift: {item['path']}")
    main = template_dir / contract.get("body_insertion", {}).get("file", "main.tex")
    if not main.is_file() or sha256_file(main) != contract["main_template"]["sha256"]:
        raise ValueError("main template byte hash mismatch")
    text = main.read_text(encoding="utf-8")
    if hashlib.sha256(mask_main(text, contract).encode("utf-8")).hexdigest() != contract["main_template"]["masked_main_sha256"]:
        raise ValueError("masked main scaffold hash mismatch")
    if package_inventory(text) != contract.get("package_inventory"):
        raise ValueError("package inventory drift")
    if api_inventory(text) != contract.get("custom_api_inventory"):
        raise ValueError("custom API inventory drift")
    if documentclass_inventory(text) != contract.get("documentclass"):
        raise ValueError("documentclass inventory drift")
    return {"main_sha256": sha256_file(main), "masked_main_sha256": contract["main_template"]["masked_main_sha256"]}


def _constructs_exist(plan: dict[str, Any], capability: dict[str, Any]) -> dict[str, Any]:
    view = capability_constructs(capability)
    standard = {"paragraph", "display_math", "source_asset_image", "source_region_image", "caption_text"}
    if capability.get("schema_version") == "template-capability-manifest/2.0":
        standard |= set(capability.get("constructs", {}).get("standard_serialization", {}))
    allowed = standard | set(view["sectioning"]) | set(view["native_environments"]) | set(view["generic_environments"])
    unsupported: list[str] = []
    for node in plan["nodes"]:
        construct = node["target_construct"]
        if construct not in allowed:
            unsupported.append(f"{node['render_node_id']}:{construct}")
        if construct == "tcolorbox" and node.get("construct_parameters", {}).get("style") not in view["tcolorbox_styles"]:
            unsupported.append(f"{node['render_node_id']}:tcolorbox-style")
    if unsupported:
        raise ValueError(f"constructs absent from capability manifest: {unsupported[:10]}")
    return {"constructs": sorted({node["target_construct"] for node in plan["nodes"]})}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate frozen ledger, decision index, render plan, and template contract")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--decision-index", type=Path, required=True)
    parser.add_argument("--render-plan", type=Path, required=True)
    parser.add_argument("--template-contract", type=Path, required=True)
    parser.add_argument("--template-dir", type=Path, required=True)
    parser.add_argument("--capability-manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = validate(*(path.resolve() for path in [args.ledger, args.decision_index, args.render_plan, args.template_contract, args.template_dir, args.capability_manifest]))
    except Exception as exc:
        report = {"schema_version": "intermediate-contract-validation/1.0", "validator": VERSION, "status": "failed", "fatal_error": str(exc)}
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
