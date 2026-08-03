#!/usr/bin/env python3
"""Build and live-verify the source-order/cross-stage lineage hard-gate report.

The report is external to immutable Spec 03 runs.  It binds the exact passed
source-reconciled parent ledger, its cumulative decision index, normalized
media candidates, and the closed source-order review evidence consumed by a
formal-native promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


VERSION = "source-lineage-integrity-builder/1.2.0"
LEDGER_HASH_SCOPE = "canonical JSON hash of ordered source_block records"
RISK_SCHEMAS = {"source-order-audit/2.0", "source-order-audit/2.1"}
ALLOWED_TRIGGERS = {"source_tree_omission", "reading_order_reanchor", "manual_ambiguity", "reviewed_page_flow_strategy"}
ALLOWED_ACTIONS = {"reinsert_missing_source", "reanchor_reading_order", "queue_manual_review"}
PAGE_FLOW_TRIGGER_CODES = {"SINGLE_COLUMN_SPATIAL_SWEEP", "EXPLICIT_SOURCE_ORDER"}
UNRESOLVED = {"open", "stale", "invalidated"}
LABEL_ALIASES = {
    "image": {"image"},
    "formula": {"formula", "equation", "equation_interline"},
    "table": {"table"},
    "chart": {"chart"},
    "diagram": {"diagram"},
    "visual_region": {"visual_region"},
    "other": {"other"},
    "unknown": {"unknown"},
}


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_visual_core():
    path = Path(__file__).with_name("visual_region_integrity.py")
    spec = importlib.util.spec_from_file_location("visual_region_integrity", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load visual-region integrity core: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_ledger(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) < 2:
        raise ValueError("canonical ledger must contain one header and at least one source record")
    header, records = rows[0], rows[1:]
    if header.get("record_type") != "ledger_header":
        raise ValueError("canonical ledger does not begin with record_type=ledger_header")
    if any(record.get("record_type") != "source_block" for record in records):
        raise ValueError("canonical ledger payload contains a non-source_block record")
    if header.get("current_ledger_hash_scope") != LEDGER_HASH_SCOPE:
        raise ValueError("source-reconciled ledger uses a non-standard payload hash scope")
    if header.get("current_ledger_hash") != canonical_hash(records):
        raise ValueError("canonical ledger payload hash mismatch")
    if header.get("ledger_checkpoint") != "source_reconciled" or header.get("spec_status") != "passed":
        raise ValueError("parent ledger is not passed at source_reconciled")
    block_ids = [record.get("block_id") for record in records]
    if None in block_ids or len(block_ids) != len(set(block_ids)):
        raise ValueError("canonical source block ids are missing or duplicated")
    return header, records


def resolve_parent_index(index_path: Path, parent_ref: str) -> Path:
    raw = Path(parent_ref)
    candidates = [raw] if raw.is_absolute() else [index_path.parent / raw, index_path.parent.parent / raw]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError(f"cannot resolve parent decision index: {parent_ref}")


def validate_decision_inheritance(index_path: Path) -> dict[str, Any]:
    index = read_json(index_path)
    if index.get("spec_status") != "passed":
        raise ValueError("source decision index is not passed")
    decisions = index.get("decisions", [])
    identifiers = [item.get("decision_id") for item in decisions]
    if not identifiers or None in identifiers or len(identifiers) != len(set(identifiers)):
        raise ValueError("source decision ids are empty, missing, or duplicated")
    if any(not item.get("rule_id") or not item.get("event_file") for item in decisions):
        raise ValueError("source decision inventory lacks rule_id or event_file")
    unresolved = [item["decision_id"] for item in decisions if item.get("status") in UNRESOLVED]
    if unresolved:
        raise ValueError(f"source decision index contains unresolved decisions: {unresolved[:8]}")
    version = int(index.get("version", 0))
    inherited = 0
    mode = "root_snapshot"
    parent_path: Path | None = None
    if version >= 2:
        parent_ref = index.get("parent_index_ref")
        parent_hash = index.get("parent_index_hash")
        if not parent_ref or not parent_hash:
            raise ValueError("decision index version >=2 lacks a parent reference/hash")
        parent_path = resolve_parent_index(index_path, parent_ref)
        if sha256_file(parent_path) != parent_hash:
            raise ValueError("parent decision index hash mismatch")
        parent = read_json(parent_path)
        parent_by_id = {item.get("decision_id"): item for item in parent.get("decisions", [])}
        child_by_id = {item["decision_id"]: item for item in decisions}
        missing = sorted(set(parent_by_id) - set(child_by_id))
        changed = sorted(decision_id for decision_id, item in parent_by_id.items() if child_by_id.get(decision_id) != item)
        if missing or changed:
            raise ValueError(f"cumulative decision inheritance failed: missing={missing[:8]} changed={changed[:8]}")
        inherited = len(parent_by_id)
        mode = "cumulative_child_snapshot"
    return {
        "mode": mode,
        "current_decisions": len(decisions),
        "inherited_decisions": inherited,
        "missing_parent_decisions": 0,
        "changed_parent_decisions": 0,
        "unresolved_decisions": 0,
        "parent_index_path": str(parent_path) if parent_path else None,
        "parent_index_sha256": sha256_file(parent_path) if parent_path else None,
    }


def expected_media_blocks(records: list[dict[str, Any]], atoms: list[dict[str, Any]], rule: str) -> set[str]:
    if rule == "all_source_blocks":
        return {record["block_id"] for record in records}
    if rule != "source_label_compatible":
        raise ValueError(f"unsupported media inventory rule: {rule}")
    labels: set[str] = set()
    for atom in atoms:
        labels.update(LABEL_ALIASES.get(atom.get("media_kind"), {str(atom.get("media_kind"))}))
    return {record["block_id"] for record in records if record.get("source_label") in labels}


def validate_fragment_binding(
    normalized_path: Path, ledger_path: Path, header: dict[str, Any], records: list[dict[str, Any]], rule: str,
) -> dict[str, Any]:
    normalized = read_json(normalized_path)
    binding = normalized.get("parent_canonical_ledger", {})
    if binding.get("sha256") != sha256_file(ledger_path) or binding.get("payload_hash") != header.get("current_ledger_hash"):
        raise ValueError("normalized candidates are not bound to the exact parent canonical ledger")
    if binding.get("ledger_snapshot_id") != header.get("ledger_snapshot_id"):
        raise ValueError("normalized candidate parent ledger snapshot id mismatch")
    atoms = normalized.get("atoms", [])
    media_ids = [atom.get("media_id") for atom in atoms]
    if not atoms or None in media_ids or len(media_ids) != len(set(media_ids)):
        raise ValueError("normalized media atom ids are empty, missing, or duplicated")
    record_by_id = {record["block_id"]: record for record in records}
    assigned: dict[str, str] = {}
    invalid: list[str] = []
    duplicates: list[str] = []
    multi = 0
    for atom in atoms:
        block_ids = atom.get("source_block_ids", [])
        if not block_ids or len(block_ids) != len(set(block_ids)):
            raise ValueError(f"media atom has empty or duplicate source fragments: {atom.get('media_id')}")
        if len(block_ids) > 1:
            multi += 1
        for block_id in block_ids:
            if block_id not in record_by_id:
                invalid.append(block_id)
                continue
            if block_id in assigned:
                duplicates.append(block_id)
            assigned[block_id] = atom["media_id"]
            record = record_by_id[block_id]
            if int(record.get("pdf_physical_page")) != int(atom.get("source_page")):
                raise ValueError(f"media fragment crosses physical pages: {atom['media_id']} -> {block_id}")
            if record.get("scope_status") != atom.get("inclusion_status"):
                raise ValueError(f"media fragment scope differs from its atom: {atom['media_id']} -> {block_id}")
    expected = expected_media_blocks(records, atoms, rule)
    used = set(assigned)
    missing = sorted(expected - used)
    declared_composite_members = {
        block_id
        for atom in atoms if atom.get("composite_integrity", {}).get("schema_version") == "composite-media-integrity/1.0"
        for block_id in atom.get("source_block_ids", [])
    }
    extra = sorted(used - expected - declared_composite_members)
    if invalid or duplicates or missing or extra:
        raise ValueError(
            "media fragment binding is not an exact partition: "
            f"invalid={invalid[:8]} duplicate={duplicates[:8]} missing={missing[:8]} extra={extra[:8]}"
        )
    return {
        "schema_version": "source-ledger-media-fragment-binding/1.0",
        "inventory_rule": rule,
        "media_atoms": len(atoms),
        "canonical_media_blocks": len(expected),
        "unbound_canonical_media_blocks": 0,
        "duplicate_fragment_assignments": 0,
        "invalid_source_block_refs": 0,
        "multi_fragment_atoms": multi,
        "fragment_assignments": len(used),
        "fragment_block_ids_hash": canonical_hash(sorted(used)),
        "declared_composite_member_blocks": len(declared_composite_members),
    }


def validate_review_precision(
    audit_path: Path, review_path: Path, header: dict[str, Any], records: list[dict[str, Any]], scope_mode: str,
) -> dict[str, Any]:
    audit = read_json(audit_path)
    review = read_json(review_path)
    audit_schema = audit.get("schema_version")
    if audit_schema not in RISK_SCHEMAS:
        raise ValueError(f"source order audit must use one of {sorted(RISK_SCHEMAS)}")
    events = audit.get("risk_events", [])
    event_ids = [event.get("event_id") for event in events]
    if None in event_ids or len(event_ids) != len(set(event_ids)):
        raise ValueError("source-order risk event ids are missing or duplicated")
    event_pages: set[int] = set()
    missing_evidence: list[str] = []
    invalid_source_refs: list[str] = []
    source_ids = {record["block_id"] for record in records}
    for event in events:
        if event.get("signal_only") is not False or event.get("requires_human_review") is not True:
            raise ValueError(f"risk event is only a broad layout signal: {event.get('event_id')}")
        trigger_kind = event.get("trigger_kind")
        if trigger_kind not in ALLOWED_TRIGGERS or event.get("action_kind") not in ALLOWED_ACTIONS:
            raise ValueError(f"risk event has an unsupported trigger/action: {event.get('event_id')}")
        if not event.get("affected_source_refs") or not event.get("evidence_refs"):
            missing_evidence.append(str(event.get("event_id")))
        invalid_source_refs.extend(ref for ref in event.get("affected_source_refs", []) if ref not in source_ids)
        for ref in event.get("evidence_refs", []):
            raw = Path(ref)
            candidates = [raw] if raw.is_absolute() else [audit_path.parent / raw, audit_path.parent.parent / raw]
            if not any(candidate.is_file() for candidate in candidates):
                missing_evidence.append(str(event.get("event_id")))
        if trigger_kind == "reviewed_page_flow_strategy":
            if audit_schema != "source-order-audit/2.1" or event.get("trigger_code") not in PAGE_FLOW_TRIGGER_CODES:
                raise ValueError(f"reviewed page-flow event lacks a supported 2.1 trigger code: {event.get('event_id')}")
            before = event.get("before_order") or []
            after = event.get("after_order") or []
            if not before or len(before) != len(set(before)) or set(before) != set(after):
                raise ValueError(f"reviewed page-flow event is not an exact source partition: {event.get('event_id')}")
            changed = {block_id for block_id in before if before.index(block_id) != after.index(block_id)}
            if not changed or set(event.get("affected_source_refs") or []) != changed:
                raise ValueError(f"reviewed page-flow event does not declare the exact changed block set: {event.get('event_id')}")
            if event.get("review_status") != "closed":
                raise ValueError(f"reviewed page-flow event is not closed: {event.get('event_id')}")
        elif trigger_kind != "manual_ambiguity" and event.get("after_position") is None:
            raise ValueError(f"corrective source-order event lacks an after position: {event.get('event_id')}")
        event_pages.add(int(event["physical_page"]))
    declared_pages = {int(page) for page in audit.get("risk_pages", [])}
    if declared_pages != event_pages:
        raise ValueError("risk_pages is not the exact page projection of actionable risk_events")
    page_rows = audit.get("pages", [])
    page_numbers = [int(row["physical_page"]) for row in page_rows]
    if len(page_numbers) != len(set(page_numbers)):
        raise ValueError("source-order audit page rows are duplicated")
    expected_events_by_page = {
        page: {event["event_id"] for event in events if int(event["physical_page"]) == page}
        for page in page_numbers
    }
    for row in page_rows:
        page = int(row["physical_page"])
        if set(row.get("risk_event_ids", [])) != expected_events_by_page[page]:
            raise ValueError(f"page risk-event projection differs from the event inventory: {page}")
        for event in events:
            if int(event["physical_page"]) == page and event.get("trigger_kind") == "reviewed_page_flow_strategy":
                if event.get("after_order") != row.get("ordered_block_ids"):
                    raise ValueError(f"reviewed page-flow event differs from the audited page order: {event.get('event_id')}")
    reviewed_pages = {int(page) for page in review.get("reviewed_pages", [])}
    if scope_mode == "formal_full_source":
        page_count = int(header.get("material_identity", {}).get("page_count", 0))
        required_pages = set(range(1, page_count + 1))
    elif scope_mode == "bounded_media_regression":
        required_pages = {int(record["pdf_physical_page"]) for record in records}
    else:
        raise ValueError(f"unsupported source-integrity scope mode: {scope_mode}")
    if not required_pages or reviewed_pages != required_pages:
        raise ValueError("source review closure is not an exact review of the declared scope")
    if set(page_numbers) != required_pages or not event_pages.issubset(required_pages):
        raise ValueError("source-order audit pages are not an exact partition of the declared review scope")
    if review.get("status") != "closed":
        raise ValueError("source review closure is open")
    if {int(page) for page in review.get("risk_pages", [])} != event_pages:
        raise ValueError("review closure risk pages differ from actionable audit risk pages")
    if set(review.get("closed_risk_event_ids", [])) != set(event_ids):
        raise ValueError("review closure does not close every current risk event exactly")
    missing_review = sorted(event_pages - reviewed_pages)
    if missing_review or missing_evidence or invalid_source_refs:
        raise ValueError(f"risk review evidence incomplete: pages={missing_review} events={sorted(set(missing_evidence))} invalid_source_refs={sorted(set(invalid_source_refs))}")
    return {
        "source_order_audit_schema": audit_schema,
        "reviewed_pages": len(reviewed_pages),
        "risk_pages": len(event_pages),
        "risk_events": len(events),
        "signal_only_events": 0,
        "unresolved_events": 0,
        "risk_pages_missing_review": 0,
        "risk_events_missing_evidence": 0,
        "closed_risk_event_ids_hash": canonical_hash(sorted(event_ids)),
    }


def evaluate(
    *, parent_ledger: Path, parent_decision_index: Path, normalized_candidates: Path,
    source_order_audit: Path, source_review_closure: Path, scope_mode: str,
    media_inventory_rule: str, report_id: str, visual_integrity_report: Path | None = None,
) -> dict[str, Any]:
    paths = [parent_ledger, parent_decision_index, normalized_candidates, source_order_audit, source_review_closure]
    if any(not path.is_file() for path in paths):
        missing = [str(path) for path in paths if not path.is_file()]
        raise FileNotFoundError(f"source integrity input missing: {missing}")
    header, records = read_ledger(parent_ledger)
    if header.get("canonical_decision_index_hash") != sha256_file(parent_decision_index):
        raise ValueError("parent canonical ledger is not bound to the supplied source decision index")
    decision = validate_decision_inheritance(parent_decision_index)
    fragments = validate_fragment_binding(normalized_candidates, parent_ledger, header, records, media_inventory_rule)
    review = validate_review_precision(source_order_audit, source_review_closure, header, records, scope_mode)
    normalized = read_json(normalized_candidates)
    visual: dict[str, Any] | None = None
    if normalized.get("schema_version") == "normalized-media-candidates/1.2" and visual_integrity_report is None:
        raise ValueError("visual-integrity-enriched candidates require a live visual integrity report")
    if visual_integrity_report is not None:
        stored_visual = read_json(visual_integrity_report)
        if stored_visual.get("status") != "passed":
            raise ValueError("visual integrity report is not passed")
        if stored_visual.get("output_parent_ledger", {}).get("sha256") != sha256_file(parent_ledger):
            raise ValueError("visual integrity report does not bind the source parent ledger")
        if stored_visual.get("output_normalized_candidates", {}).get("sha256") != sha256_file(normalized_candidates):
            raise ValueError("visual integrity report does not bind normalized candidates")
        live = load_visual_core().validate_run(visual_integrity_report.parents[1])
        visual = {"path": str(visual_integrity_report), "sha256": sha256_file(visual_integrity_report), "live_validation": live}
    identity = {
        "record_type": "ledger_header", "hash_scope": LEDGER_HASH_SCOPE, "record_count": len(records),
        "unique_block_ids": True, "payload_hash_valid": True,
    }
    gates = [
        {"gate_id": "SL-H01-standard-ledger-identity", "status": "passed", "evidence": identity},
        {"gate_id": "SL-H02-cumulative-decision-inheritance", "status": "passed", "evidence": decision},
        {"gate_id": "SL-H03-exact-media-fragment-partition", "status": "passed", "evidence": fragments},
        {"gate_id": "SL-H04-actionable-review-queue-closure", "status": "passed", "evidence": review},
        *([{"gate_id": "SL-H05-page-visual-and-composite-integrity", "status": "passed", "evidence": visual}] if visual else []),
    ]
    return {
        "schema_version": "source-lineage-integrity-report/1.1" if visual else "source-lineage-integrity-report/1.0", "report_id": report_id, "status": "passed",
        "scope_mode": scope_mode, "media_inventory_rule": media_inventory_rule, "producer": VERSION,
        "source_parent_ledger": {"path": str(parent_ledger), "sha256": sha256_file(parent_ledger), "snapshot_id": header["ledger_snapshot_id"], "payload_hash": header["current_ledger_hash"]},
        "source_parent_decision_index": {"path": str(parent_decision_index), "sha256": sha256_file(parent_decision_index)},
        "normalized_media_candidates": {"path": str(normalized_candidates), "sha256": sha256_file(normalized_candidates)},
        "source_order_audit": {"path": str(source_order_audit), "sha256": sha256_file(source_order_audit)},
        "source_review_closure": {"path": str(source_review_closure), "sha256": sha256_file(source_review_closure)},
        "ledger_identity": identity, "decision_inheritance": decision,
        "media_fragment_binding": fragments, "review_queue_precision": review, "gates": gates,
        **({"visual_region_integrity": visual} if visual else {}),
        "summary": {"gates": len(gates), "passed": len(gates), "failed": 0, "scope_mode": scope_mode},
        "scope_limit": "Source ledger identity, cumulative decisions, media fragment grouping, and source-order review precision only; no formula/table reconstruction or upstream cleaning.",
    }


def validate_report(path: Path) -> dict[str, Any]:
    stored = read_json(path)
    if stored.get("schema_version") not in {"source-lineage-integrity-report/1.0", "source-lineage-integrity-report/1.1"} or stored.get("status") != "passed":
        raise ValueError("source lineage integrity report is not a passed supported report")
    recomputed = evaluate(
        parent_ledger=Path(stored["source_parent_ledger"]["path"]),
        parent_decision_index=Path(stored["source_parent_decision_index"]["path"]),
        normalized_candidates=Path(stored["normalized_media_candidates"]["path"]),
        source_order_audit=Path(stored["source_order_audit"]["path"]),
        source_review_closure=Path(stored["source_review_closure"]["path"]),
        scope_mode=stored["scope_mode"], media_inventory_rule=stored["media_inventory_rule"], report_id=stored["report_id"],
        visual_integrity_report=Path(stored["visual_region_integrity"]["path"]) if stored.get("visual_region_integrity") else None,
    )
    if stored != recomputed:
        raise ValueError("stored source lineage integrity report differs from live recomputation")
    return recomputed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-ledger", type=Path, required=True)
    parser.add_argument("--parent-decision-index", type=Path, required=True)
    parser.add_argument("--normalized-candidates", type=Path, required=True)
    parser.add_argument("--source-order-audit", type=Path, required=True)
    parser.add_argument("--source-review-closure", type=Path, required=True)
    parser.add_argument("--scope-mode", choices=["formal_full_source", "bounded_media_regression"], required=True)
    parser.add_argument("--media-inventory-rule", choices=["source_label_compatible", "all_source_blocks"], default="source_label_compatible")
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--visual-integrity-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        if output.exists():
            raise FileExistsError(f"refusing to overwrite immutable integrity report: {output}")
        report = evaluate(
            parent_ledger=args.parent_ledger.resolve(), parent_decision_index=args.parent_decision_index.resolve(),
            normalized_candidates=args.normalized_candidates.resolve(), source_order_audit=args.source_order_audit.resolve(),
            source_review_closure=args.source_review_closure.resolve(), scope_mode=args.scope_mode,
            media_inventory_rule=args.media_inventory_rule, report_id=args.report_id,
            visual_integrity_report=args.visual_integrity_report.resolve() if args.visual_integrity_report else None,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        validate_report(output)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "producer": VERSION, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
