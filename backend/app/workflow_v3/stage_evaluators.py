from __future__ import annotations

import hashlib
import io
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

try:
    from .overleaf_compiler import (
        ADAPTER_PROTOCOL,
        compile_overleaf_delivery,
        load_release_target_environment,
        validate_target_environment,
    )
    from .page_review_contract import (
        PageReviewContractError,
        validate_page_review_contract,
    )
    from .stage_entrypoint import (
        StageEntrypointError,
        run_release_python_kernel,
        sha256_file,
    )
    from .stage_evaluation_entrypoint import (
        EvaluationInput,
        StageEvaluation,
        StageEvaluationRequest,
    )
except ImportError:  # Release-local scripts import this module directly.
    from overleaf_compiler import (  # type: ignore[no-redef]
        ADAPTER_PROTOCOL,
        compile_overleaf_delivery,
        load_release_target_environment,
        validate_target_environment,
    )
    from page_review_contract import (  # type: ignore[no-redef]
        PageReviewContractError,
        validate_page_review_contract,
    )
    from stage_entrypoint import (  # type: ignore[no-redef]
        StageEntrypointError,
        run_release_python_kernel,
        sha256_file,
    )
    from stage_evaluation_entrypoint import (  # type: ignore[no-redef]
        EvaluationInput,
        StageEvaluation,
        StageEvaluationRequest,
    )


STAGE_GATES: Mapping[str, tuple[str, ...]] = {
    "intake_snapshot": (
        "source_pdf_identity_verified",
        "popo_manifest_identity_verified",
        "skill_release_identity_verified",
        "template_identity_verified",
    ),
    "source_scope_and_order": (
        "every_source_page_accounted_for",
        "body_scope_closed",
        "reading_order_closed",
        "open_source_ambiguities_zero",
    ),
    "canonical_block_ledger": (
        "canonical_ids_unique",
        "source_lineage_complete",
        "content_conservation_passed",
        "media_relations_closed",
    ),
    "outline_reconstruction": (
        "outline_source_evidenced",
        "outline_hierarchy_valid",
        "outline_body_coverage_complete",
        "outline_accuracy_at_least_99_percent",
        "open_outline_decisions_zero",
    ),
    "semantic_annotation": (
        "every_canonical_block_assigned_once",
        "semantic_relations_valid",
        "source_text_not_rewritten",
        "open_semantic_decisions_zero",
    ),
    "template_construct_binding": (
        "constructs_allowlisted",
        "template_hash_matches_release",
        "template_local_api_unchanged",
        "all_bindings_source_traceable",
    ),
    "frozen_render_plan": (
        "render_plan_schema_valid",
        "render_plan_fully_bound",
        "render_plan_has_no_open_decisions",
        "volume_partition_valid",
    ),
    "deterministic_elegantbook": (
        "formal_native_renderer_used",
        "protected_template_unchanged",
        "delivery_limits_passed",
        "xelatex_recompile_passed",
    ),
    "readonly_latex_audit": (
        "audit_is_readonly",
        "compile_errors_zero",
        "missing_glyphs_zero",
        "obvious_overflow_zero",
    ),
    "independent_full_page_review": (
        "review_pdf_hash_bound",
        "every_page_reviewed",
        "source_fidelity_reviewed",
        "blocking_findings_zero",
    ),
    "delivery_recompile": (
        "downloaded_zip_hash_verified",
        "independent_xelatex_recompile_passed",
        "compiled_pdf_hash_recorded",
        "delivery_manifest_complete",
    ),
    "ready_for_user_acceptance": (
        "all_prior_promotions_verified",
        "page_db_minio_lineage_consistent",
        "open_blockers_zero",
        "human_acceptance_not_self_attested",
    ),
}

PDF_RASTER_PROFILE: Mapping[str, Any] = {
    "renderer": "pymupdf",
    "scale": 2,
    "colorspace": "rgb",
    "alpha": False,
    "annots": True,
}
PDF_REVIEW_IMAGE_PROFILE: Mapping[str, Any] = {
    "renderer": "pymupdf+pillow",
    "scale": 1.25,
    "colorspace": "rgb",
    "alpha": False,
    "format": "jpeg",
    "quality": 74,
    "optimize": True,
    "progressive": False,
    "max_bytes": 1_500_000,
}

_NATIVE_VALIDATORS = {
    "outline_reconstruction": (
        "skills/luceon-popo-to-refined-elegantbook/scripts/"
        "spec04a_structure_contract.py"
    ),
    "semantic_annotation": (
        "skills/luceon-popo-to-refined-elegantbook/scripts/"
        "spec04b_semantic_span_contract.py"
    ),
    "template_construct_binding": (
        "skills/luceon-popo-to-refined-elegantbook/scripts/"
        "spec04c_construct_binding_contract.py"
    ),
    "frozen_render_plan": (
        "skills/luceon-popo-to-refined-elegantbook/scripts/"
        "spec04d_render_plan_contract.py"
    ),
}
_MAX_ZIP_BYTES = 50_000_000
_MAX_ZIP_FILES = 1_999
_MAX_IMAGE_BYTES = 999_999
_MAX_TEX_BYTES = 899_999
_MAX_ZIP_MEMBER_BYTES = 100_000_000
_MAX_ZIP_UNCOMPRESSED_BYTES = 2_000_000_000
_MAX_ZIP_COMPRESSION_RATIO = 1_000
_MIN_EXTRACTION_HEADROOM_BYTES = 512_000_000
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BODY_DEFINITION_RE = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand|"
    r"NewDocumentCommand|RenewDocumentCommand|ProvideDocumentCommand|"
    r"def|gdef|xdef|newenvironment|renewenvironment|"
    r"NewDocumentEnvironment|RenewDocumentEnvironment|"
    r"AtBeginDocument)\b"
)
_OVERFULL_RE = re.compile(
    r"Overfull \\[hv]box\b.*?\(([0-9]+(?:\.[0-9]+)?)pt too (?:wide|high)\)"
)


@dataclass(frozen=True)
class CompileEvidence:
    zip_sha256: str
    pdf_path: Path
    pdf_sha256: str
    page_count: int
    log: str
    xelatex_version: str
    latexmk_version: str


def evaluate_stage(
    request: StageEvaluationRequest,
    candidate: EvaluationInput,
    release_root: Path,
) -> StageEvaluation:
    expected = STAGE_GATES.get(request.stage_key)
    if expected is None or tuple(request.required_gates) != expected:
        raise StageEntrypointError(
            "stage_gate_contract_mismatch",
            "requested gates do not match the immutable stage contract",
            exit_code=3,
        )
    if request.stage_key in {
        "intake_snapshot",
        "source_scope_and_order",
        "canonical_block_ledger",
    }:
        return _evaluate_atomic_stage(request, candidate, release_root)
    if request.stage_key in _NATIVE_VALIDATORS:
        return _evaluate_native_spec04(request, candidate, release_root)
    if request.stage_key == "deterministic_elegantbook":
        return _evaluate_spec05(request, candidate, release_root)
    if request.stage_key == "readonly_latex_audit":
        return _evaluate_latex_audit(request, candidate)
    if request.stage_key == "independent_full_page_review":
        return _evaluate_full_page_review_contract(
            request,
            candidate,
            release_root,
        )
    if request.stage_key == "delivery_recompile":
        return _evaluate_delivery_recompile(request, candidate, release_root)
    if request.stage_key == "ready_for_user_acceptance":
        return _evaluate_readiness(request, candidate)
    raise StageEntrypointError(
        "stage_evaluator_unknown",
        f"no evaluator is registered for {request.stage_key!r}",
        exit_code=3,
    )


def _evaluate_atomic_stage(
    request: StageEvaluationRequest,
    candidate: EvaluationInput,
    release_root: Path,
) -> StageEvaluation:
    root = candidate.bundle_root
    common = _atomic_candidate_contract(root, request.stage_key, release_root)
    if request.stage_key == "intake_snapshot":
        gates = _evaluate_atomic_intake(root, release_root)
    elif request.stage_key == "source_scope_and_order":
        gates = _evaluate_atomic_scope_order(root)
    else:
        gates = _evaluate_atomic_ledger(root)
    gates["skill_release_identity_verified"] = (
        gates.get("skill_release_identity_verified", True) and common
    )
    return StageEvaluation(
        gate_results={gate: bool(gates.get(gate, False)) for gate in request.required_gates},
    )


def _atomic_candidate_contract(
    root: Path,
    stage_key: str,
    release_root: Path,
) -> bool:
    stage_path = _required(
        root,
        f"manifests/{stage_key}_candidate_stage_manifest.json",
    )
    stage = _read_json(stage_path, f"{stage_key} candidate stage manifest")
    run = _read_json(
        _required(root, "manifests/run_manifest.json"),
        f"{stage_key} run manifest",
    )
    if (
        stage.get("schema_version")
        != "luceon.worker-v3-atomic-stage-manifest/v1"
        or stage.get("stage") != stage_key
        or stage.get("candidate_status") != "complete"
        or stage.get("spec_status") != "not_evaluated"
        or stage.get("promotion_status") != "not_evaluated"
        or run.get("schema_version")
        != "luceon.worker-v3-atomic-run-manifest/v1"
        or run.get("stage") != stage_key
        or run.get("candidate_status") != "complete"
        or run.get("spec_status") != "not_evaluated"
        or run.get("promotion_status") != "not_evaluated"
    ):
        return False
    run_stage = run.get("stage_manifest")
    if (
        not isinstance(run_stage, dict)
        or run_stage.get("path")
        != f"manifests/{stage_key}_candidate_stage_manifest.json"
        or run_stage.get("sha256") != sha256_file(stage_path)
    ):
        return False
    artifacts = stage.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        return False
    for binding in artifacts.values():
        if not isinstance(binding, dict):
            return False
        try:
            bound = _required(root, binding.get("path"))
        except StageEntrypointError:
            return False
        if binding.get("sha256") != sha256_file(bound):
            return False
    schema = stage.get("contract_schema")
    if not isinstance(schema, dict):
        return False
    try:
        schema_path = _required(release_root, schema.get("path"))
    except StageEntrypointError:
        return False
    release = _read_json(
        _required(release_root, "release-manifest.json"),
        "release manifest",
    )
    matches = [
        row
        for row in release.get("schemas", [])
        if isinstance(row, dict)
        and row.get("path") == schema.get("path")
        and row.get("sha256") == schema.get("sha256")
    ]
    return (
        len(matches) == 1
        and schema.get("sha256") == sha256_file(schema_path)
    )


def _evaluate_atomic_intake(
    root: Path,
    release_root: Path,
) -> dict[str, bool]:
    contract = _read_json(
        _required(root, "contracts/input_contract.json"),
        "Spec 01 input contract",
    )
    trace = _read_json(
        _required(root, "contracts/source_trace.json"),
        "Spec 01 source trace",
    )
    materialized = _read_json(
        _required(root, "contracts/materialized_manifest.json"),
        "Spec 01 materialized manifest",
    )
    template = _read_json(
        _required(root, "contracts/template_intake.json"),
        "Spec 01 template intake",
    )
    validation = _read_json(
        _required(root, "reports/input_validation_report.json"),
        "Spec 01 validation report",
    )
    geometry = _read_json(
        _required(root, "evidence/pdf_page_geometry.json"),
        "Spec 01 PDF geometry",
    )
    units = _read_jsonl(
        _required(root, "source/popo_source_units.jsonl"),
        "Spec 01 Popo source units",
    )
    media = _read_jsonl(
        _required(root, "source/mineru_media_atoms.jsonl"),
        "Spec 01 MinerU media atoms",
    )
    identity = contract.get("material_identity")
    inputs = contract.get("inputs")
    source = trace.get("source_pdf")
    pages = geometry.get("pages")
    source_ok = (
        isinstance(identity, dict)
        and isinstance(inputs, dict)
        and isinstance(source, dict)
        and isinstance(pages, list)
        and isinstance(identity.get("page_count"), int)
        and not isinstance(identity.get("page_count"), bool)
        and identity["page_count"] > 0
        and trace.get("material_id") == identity.get("material_id")
        and source.get("sha256") == identity.get("source_pdf_sha256")
        and source.get("size_bytes") == identity.get("source_pdf_size_bytes")
        and source.get("page_count") == identity.get("page_count")
        and geometry.get("source_pdf_sha256") == identity.get("source_pdf_sha256")
        and sorted(
            row.get("physical_page")
            for row in pages
            if isinstance(row, dict)
        )
        == list(range(1, identity["page_count"] + 1))
        and isinstance(inputs.get("source_pdf"), dict)
        and inputs["source_pdf"].get("sha256") == identity.get("source_pdf_sha256")
        and inputs["source_pdf"].get("size_bytes")
        == identity.get("source_pdf_size_bytes")
        and inputs["source_pdf"].get("storage") == "external_frozen_input"
        and all(
            isinstance(row.get("physical_page"), int)
            and 1 <= row["physical_page"] <= identity["page_count"]
            for row in [*units, *media]
        )
    )
    popo_trace = trace.get("popo")
    mineru_trace = trace.get("mineru")
    source_archives = materialized.get("source_archive_identities")
    popo_ok = (
        isinstance(identity, dict)
        and isinstance(inputs, dict)
        and isinstance(popo_trace, dict)
        and isinstance(mineru_trace, dict)
        and isinstance(source_archives, dict)
        and popo_trace.get("run_id") == identity.get("popo_run_id")
        and mineru_trace.get("run_id") == identity.get("mineru_run_id")
        and popo_trace.get("upstream_mineru_run_id") == identity.get("mineru_run_id")
        and _identity_fields_equal(
            popo_trace.get("manifest"),
            inputs.get("popo_manifest"),
            ("sha256", "size_bytes"),
        )
        and _identity_fields_equal(
            mineru_trace.get("manifest"),
            inputs.get("mineru_manifest"),
            ("sha256", "size_bytes"),
        )
        and _identity_fields_equal(
            popo_trace.get("archive"),
            inputs.get("popo_archive"),
            ("bucket", "object", "sha256", "size_bytes"),
        )
        and _identity_fields_equal(
            mineru_trace.get("archive"),
            inputs.get("mineru_archive"),
            ("bucket", "object", "sha256", "size_bytes"),
        )
        and _identity_fields_equal(
            source_archives.get("popo"),
            inputs.get("popo_archive"),
            ("bucket", "object", "sha256", "size_bytes"),
        )
        and _identity_fields_equal(
            source_archives.get("mineru"),
            inputs.get("mineru_archive"),
            ("bucket", "object", "sha256", "size_bytes"),
        )
        and all(row.get("popo_run_id") == identity.get("popo_run_id") for row in units)
        and all(row.get("mineru_run_id") == identity.get("mineru_run_id") for row in media)
        and materialized.get("external_frozen_inputs_are_not_materialized") is True
        and _bound_inventory_valid(root, materialized.get("entries"))
    )
    release = _read_json(
        _required(release_root, "release-manifest.json"),
        "release manifest",
    )
    release_template = release.get("template")
    template_input = inputs.get("template_archive") if isinstance(inputs, dict) else None
    template_ok = (
        isinstance(release_template, dict)
        and isinstance(template_input, dict)
        and template.get("schema_version") == "template-intake/1.0"
        and template.get("archive_sha256") == release_template.get("archive_sha256")
        and template.get("archive_sha256") == template_input.get("sha256")
        and template.get("archive_size_bytes") == template_input.get("size_bytes")
        and template.get("main_member") == release_template.get("main_member")
        and template.get("class_member") == release_template.get("class_member")
        and template.get("fixed_asset_members")
        == list(release_template.get("fixed_asset_members") or [])
        and template.get("candidate_configuration_points") == []
    )
    release_ok = (
        contract.get("schema_version")
        == "luceon.worker-v3-spec01-intake-contract/v1"
        and contract.get("spec_status") == "passed"
        and contract.get("open_reviews") == []
        and validation.get("spec_status") == "passed"
        and validation.get("failure_codes") == []
        and validation.get("open_reviews") == []
        and _decision_index_closed(root)
    )
    return {
        "source_pdf_identity_verified": source_ok,
        "popo_manifest_identity_verified": popo_ok,
        "skill_release_identity_verified": release_ok,
        "template_identity_verified": template_ok,
    }


def _evaluate_atomic_scope_order(root: Path) -> dict[str, bool]:
    contract = _read_json(
        _required(root, "contracts/input_contract.json"),
        "Spec 02 inherited input contract",
    )
    scope = _read_json(
        _required(root, "ledgers/source_scope_ledger.json"),
        "Spec 02 source scope ledger",
    )
    order = _read_json(
        _required(root, "ledgers/reading_order_ledger.json"),
        "Spec 02 reading order ledger",
    )
    relationships = _read_json(
        _required(root, "contracts/composite_reading_relationships.json"),
        "Spec 02 relationships",
    )
    report = _read_json(
        _required(root, "reports/scope_order_validation_report.json"),
        "Spec 02 validation report",
    )
    render_rows = _read_jsonl(
        _required(root, "ledgers/source_page_render_ledger.jsonl"),
        "Spec 02 source page render ledger",
    )
    identity = contract.get("material_identity")
    pages = scope.get("pages")
    units = scope.get("source_units")
    ordered = order.get("ordered_source_units")
    page_count = identity.get("page_count") if isinstance(identity, dict) else None
    page_numbers = (
        sorted(row.get("physical_page") for row in pages if isinstance(row, dict))
        if isinstance(pages, list)
        else []
    )
    rendered_pages = sorted(
        row.get("physical_page") for row in render_rows if isinstance(row, dict)
    )
    every_page = (
        isinstance(page_count, int)
        and page_count > 0
        and page_numbers == list(range(1, page_count + 1))
        and rendered_pages == list(range(1, page_count + 1))
        and all(
            row.get("source_pdf_sha256") == identity.get("source_pdf_sha256")
            for row in render_rows
        )
    )
    unit_rows = units if isinstance(units, list) else []
    body_closed = (
        isinstance(pages, list)
        and isinstance(units, list)
        and all(
            isinstance(row, dict)
            and row.get("scope_status") in {"included", "excluded"}
            and isinstance(row.get("reason"), str)
            and bool(row["reason"])
            for row in pages
        )
        and all(
            isinstance(row, dict)
            and row.get("scope_status") in {"included", "excluded"}
            and isinstance(row.get("scope_reason"), str)
            and bool(row["scope_reason"])
            and isinstance(row.get("source_id"), str)
            and bool(row["source_id"])
            for row in unit_rows
        )
        and len({row["source_id"] for row in unit_rows}) == len(unit_rows)
        and scope.get("spec_status") == "passed"
    )
    included = [row for row in unit_rows if row.get("scope_status") == "included"]
    expected_order = sorted(
        included,
        key=lambda row: row.get("candidate_final_order") or 0,
    )
    order_closed = (
        isinstance(ordered, list)
        and [row.get("candidate_final_order") for row in expected_order]
        == list(range(1, len(expected_order) + 1))
        and [row.get("source_id") for row in ordered]
        == [row.get("source_id") for row in expected_order]
        and all(
            row.get("candidate_final_order") is None
            for row in unit_rows
            if row.get("scope_status") == "excluded"
        )
        and order.get("source_scope_ledger_hash")
        == sha256_file(_required(root, "ledgers/source_scope_ledger.json"))
        and order.get("spec_status") == "passed"
    )
    scope_summary = scope.get("summary")
    order_summary = order.get("summary")
    relation_summary = relationships.get("unresolved_candidates")
    no_open = (
        isinstance(scope_summary, dict)
        and scope_summary.get("open_reviews") == 0
        and isinstance(order_summary, dict)
        and order_summary.get("open_reviews") == 0
        and relation_summary == []
        and relationships.get("spec_status") == "passed"
        and report.get("spec_status") == "passed"
        and report.get("failure_codes") == []
        and report.get("open_reviews") == []
        and _decision_index_closed(root)
    )
    return {
        "every_source_page_accounted_for": every_page,
        "body_scope_closed": body_closed,
        "reading_order_closed": order_closed,
        "open_source_ambiguities_zero": no_open,
    }


def _evaluate_atomic_ledger(root: Path) -> dict[str, bool]:
    ledger_path = _required(root, "ledgers/canonical_block_ledger.jsonl")
    rows = _read_jsonl(ledger_path, "Spec 03 canonical block ledger")
    if not rows or rows[0].get("record_type") != "ledger_header":
        raise StageEntrypointError(
            "canonical_ledger_invalid",
            "canonical ledger has no valid header",
        )
    header, blocks = rows[0], rows[1:]
    source_units = _read_jsonl(
        _required(root, "source/popo_source_units.jsonl"),
        "Spec 03 inherited source units",
    )
    scope = _read_json(
        _required(root, "ledgers/source_scope_ledger.json"),
        "Spec 03 inherited scope ledger",
    )
    coverage = _read_json(
        _required(root, "ledgers/block_coverage_ledger.json"),
        "Spec 03 coverage ledger",
    )
    media = _read_json(
        _required(root, "ledgers/media_ledger.json"),
        "Spec 03 media ledger",
    )
    evidence = _read_json(
        _required(root, "media/media_evidence_ledger.json"),
        "Spec 03 media evidence ledger",
    )
    plan = _read_json(
        _required(root, "media/media_representation_plan.json"),
        "Spec 03 media representation plan",
    )
    completeness = _read_json(
        _required(root, "reports/source_completeness_report.json"),
        "Spec 03 source completeness report",
    )
    ledger_manifest = _read_json(
        _required(root, "ledgers/ledger_manifest.json"),
        "Spec 03 ledger manifest",
    )
    block_ids = [row.get("block_id") for row in blocks]
    source_ids = [
        (row.get("upstream_block_ref") or {}).get("popo_source_id")
        if isinstance(row.get("upstream_block_ref"), dict)
        else None
        for row in blocks
    ]
    ids_unique = (
        all(isinstance(value, str) and value for value in [*block_ids, *source_ids])
        and len(set(block_ids)) == len(blocks)
        and len(set(source_ids)) == len(blocks)
        and header.get("current_ledger_hash") == _canonical_hash(blocks)
        and ledger_manifest.get("artifact_sha256") == sha256_file(ledger_path)
        and ledger_manifest.get("payload_hash") == header.get("current_ledger_hash")
        and ledger_manifest.get("immutable_after_publication") is True
    )
    source_by_id = {
        row.get("source_id"): row
        for row in source_units
        if isinstance(row.get("source_id"), str)
    }
    scope_rows = scope.get("source_units")
    scope_by_id = {
        row.get("source_id"): row
        for row in scope_rows
        if isinstance(scope_rows, list)
        and isinstance(row, dict)
        and isinstance(row.get("source_id"), str)
    }
    lineage = (
        set(source_ids) == set(source_by_id) == set(scope_by_id)
        and all(
            block.get("raw_content_sha256")
            == _raw_content_hash(source_by_id[source_id].get("raw_content"))
            and block.get("raw_content") == source_by_id[source_id].get("raw_content")
            and block.get("scope_status") == scope_by_id[source_id].get("scope_status")
            and block.get("pdf_physical_page")
            == source_by_id[source_id].get("physical_page")
            for block, source_id in zip(blocks, source_ids, strict=True)
        )
        and header.get("source_scope_ledger_hash")
        == sha256_file(_required(root, "ledgers/source_scope_ledger.json"))
        and header.get("reading_order_ledger_hash")
        == sha256_file(_required(root, "ledgers/reading_order_ledger.json"))
        and header.get("canonical_decision_index_hash")
        == sha256_file(_required(root, "decisions/canonical_decision_index.json"))
    )
    coverage_rows = coverage.get("source_units")
    coverage_by_source = {
        row.get("source_id"): row
        for row in coverage_rows
        if isinstance(coverage_rows, list)
        and isinstance(row, dict)
        and isinstance(row.get("source_id"), str)
    }
    coverage_summary = coverage.get("summary")
    conservation = (
        set(coverage_by_source) == set(source_by_id)
        and all(
            row.get("coverage_count") == 1
            and row.get("block_id") in set(block_ids)
            and row.get("raw_content_sha256")
            == source_by_id[source_id].get("raw_content_sha256")
            for source_id, row in coverage_by_source.items()
        )
        and isinstance(coverage_summary, dict)
        and coverage_summary.get("source_units") == len(source_units)
        and coverage_summary.get("covered_exactly_once") == len(source_units)
        and coverage_summary.get("missing") == 0
        and coverage_summary.get("duplicates") == 0
        and completeness.get("missing_source_units") == []
        and completeness.get("duplicate_source_units") == []
        and completeness.get("open_reviews") == []
        and completeness.get("spec_status") == "passed"
    )
    media_rows = evidence.get("atoms")
    plan_rows = plan.get("representations")
    media_summary = media.get("summary")
    evidence_summary = evidence.get("summary")
    plan_summary = plan.get("summary")

    def formal_media_contract_closed() -> bool:
        if (
            evidence.get("schema_version") != "media-evidence-ledger/1.1"
            or plan.get("schema_version") != "media-representation-plan/1.1"
            or not isinstance(media_rows, list)
            or not media_rows
            or not isinstance(plan_rows, list)
            or len(plan_rows) != len(media_rows)
            or evidence.get("payload_hash")
            != _contract_payload_hash(evidence)
            or plan.get("payload_hash") != _contract_payload_hash(plan)
        ):
            return False
        evidence_path = _required(root, "media/media_evidence_ledger.json")
        plan_path = _required(root, "media/media_representation_plan.json")
        decision_path = _required(
            root,
            "decisions/canonical_decision_index.json",
        )
        if (
            plan.get("media_evidence_ledger_sha256")
            != sha256_file(evidence_path)
            or plan.get("canonical_ledger_sha256")
            != sha256_file(ledger_path)
            or plan.get("decision_index_sha256")
            != sha256_file(decision_path)
            or (evidence.get("canonical_ledger") or {}).get("sha256")
            != sha256_file(ledger_path)
            or (evidence.get("decision_index") or {}).get("sha256")
            != sha256_file(decision_path)
            or media.get("canonical_ledger_hash") != sha256_file(ledger_path)
            or media.get("media_evidence_ledger_hash")
            != sha256_file(evidence_path)
            or media.get("media_representation_plan_hash")
            != sha256_file(plan_path)
        ):
            return False
        atoms = {
            row.get("media_id"): row
            for row in media_rows
            if isinstance(row, dict) and isinstance(row.get("media_id"), str)
        }
        reps = {
            row.get("media_id"): row
            for row in plan_rows
            if isinstance(row, dict) and isinstance(row.get("media_id"), str)
        }
        if len(atoms) != len(media_rows) or set(atoms) != set(reps):
            return False
        decision_index = _read_json(decision_path, "Spec 03 decision index")
        decisions = {
            row.get("decision_id"): row
            for row in decision_index.get("decisions", [])
            if isinstance(row, dict)
        }
        block_by_id = {
            row.get("block_id"): row
            for row in blocks
            if isinstance(row.get("block_id"), str)
        }
        owner_counts: Counter[str] = Counter()
        for media_id, atom in atoms.items():
            rep = reps[media_id]
            source_block_ids = atom.get("source_block_ids")
            candidates = atom.get("candidates")
            if (
                atom.get("review_status") != "closed"
                or atom.get("inclusion_status") != "included"
                or not isinstance(source_block_ids, list)
                or not source_block_ids
                or len(source_block_ids) != len(set(source_block_ids))
                or any(block_id not in block_by_id for block_id in source_block_ids)
                or not isinstance(candidates, list)
                or not candidates
                or rep.get("status") != "closed"
                or rep.get("source_block_ids") != source_block_ids
                or not isinstance(rep.get("representation_id"), str)
                or not rep.get("representation_id")
                or rep.get("representation_type")
                not in {
                    "source_asset_image",
                    "source_region_image",
                    "structured_formula",
                    "structured_table",
                    "structured_chart",
                    "vector_reconstruction",
                }
            ):
                return False
            selected = [
                candidate
                for candidate in candidates
                if isinstance(candidate, dict)
                and candidate.get("candidate_id")
                == rep.get("selected_candidate_id")
            ]
            if len(selected) != 1:
                return False
            candidate = selected[0]
            if (
                candidate.get("status") != "usable"
                or candidate.get("representation_type")
                != rep.get("representation_type")
                or candidate.get("artifact_sha256")
                != rep.get("artifact_sha256")
                or not _is_sha256(rep.get("artifact_sha256"))
            ):
                return False
            if rep.get("representation_type") in {
                "source_asset_image",
                "source_region_image",
            }:
                path_value = candidate.get("resolved_path") or candidate.get(
                    "crop_path"
                )
                path = Path(str(path_value))
                if not path.is_absolute():
                    path = root / path
                if (
                    not path.is_file()
                    or sha256_file(path) != rep.get("artifact_sha256")
                ):
                    return False
            refs = rep.get("decision_refs")
            if (
                not isinstance(refs, list)
                or not refs
                or any(
                    ref not in decisions
                    or decisions[ref].get("status")
                    not in {"closed", "superseded"}
                    for ref in refs
                )
            ):
                return False
            owner_counts.update(source_block_ids)
        if any(count != 1 for count in owner_counts.values()):
            return False
        fragile = {
            row["block_id"]
            for row in blocks
            if row.get("scope_status") == "included"
            and str(row.get("source_type") or "").lower()
            in {"chart", "equation", "image", "table"}
        }
        if any(owner_counts[block_id] != 1 for block_id in fragile):
            return False
        canonical_contracts: dict[str, dict[str, Any]] = {}
        for block in blocks:
            for contract in block.get("media_contracts", []):
                if not isinstance(contract, dict):
                    return False
                media_id = contract.get("media_id")
                if (
                    media_id not in atoms
                    or block.get("block_id")
                    not in contract.get("source_block_ids", [])
                    or _canonical_hash(contract) != _canonical_hash(atoms[media_id])
                ):
                    return False
                previous = canonical_contracts.get(media_id)
                if (
                    previous is not None
                    and _canonical_hash(previous) != _canonical_hash(contract)
                ):
                    return False
                canonical_contracts[media_id] = contract
        if set(canonical_contracts) != set(atoms):
            return False
        return (
            isinstance(media_summary, dict)
            and media_summary.get("open") == 0
            and media_summary.get("closed") == len(media_rows)
            and isinstance(evidence_summary, dict)
            and evidence_summary.get("needs_review") == 0
            and isinstance(plan_summary, dict)
            and plan_summary.get("needs_review") == 0
            and plan_summary.get("closed") == len(media_rows)
            and media.get("spec_status") == "passed"
            and plan.get("spec_status") == "passed"
            and plan.get("open_reviews") == 0
            and _decision_index_closed(root)
        )

    try:
        media_closed = formal_media_contract_closed()
    except (OSError, TypeError, ValueError, StageEntrypointError):
        media_closed = False
    return {
        "canonical_ids_unique": ids_unique,
        "source_lineage_complete": lineage,
        "content_conservation_passed": conservation,
        "media_relations_closed": media_closed,
    }


def _decision_index_closed(root: Path) -> bool:
    decision = _read_json(
        _required(root, "decisions/canonical_decision_index.json"),
        "canonical decision index",
    )
    summary = decision.get("summary")
    decisions = decision.get("decisions")
    return (
        decision.get("spec_status") == "passed"
        and isinstance(summary, dict)
        and summary.get("open") == 0
        and summary.get("stale") == 0
        and summary.get("invalidated") == 0
        and isinstance(decisions, list)
        and all(
            isinstance(row, dict) and row.get("status") == "closed"
            for row in decisions
        )
    )


def _bound_inventory_valid(root: Path, raw: Any) -> bool:
    if not isinstance(raw, list) or not raw:
        return False
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            return False
        if row["path"] in seen:
            return False
        seen.add(row["path"])
        try:
            path = _required(root, row["path"])
        except StageEntrypointError:
            return False
        if (
            row.get("sha256") != sha256_file(path)
            or row.get("size_bytes") != path.stat().st_size
            or row.get("read_only_source") is not True
        ):
            return False
    return True


def _identity_fields_equal(
    left: Any,
    right: Any,
    fields: tuple[str, ...],
) -> bool:
    return (
        isinstance(left, dict)
        and isinstance(right, dict)
        and all(left.get(field) == right.get(field) for field in fields)
    )


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise StageEntrypointError(
            "candidate_json_invalid",
            f"{label} is not valid UTF-8 JSONL",
        ) from exc
    for number, line in enumerate(lines, 1):
        if not line:
            raise StageEntrypointError(
                "candidate_json_invalid",
                f"{label} contains an empty line at {number}",
            )
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StageEntrypointError(
                "candidate_json_invalid",
                f"{label} contains invalid JSON at line {number}",
            ) from exc
        if not isinstance(row, dict):
            raise StageEntrypointError(
                "candidate_json_invalid",
                f"{label} line {number} is not an object",
            )
        rows.append(row)
    return rows


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _contract_payload_hash(value: Mapping[str, Any]) -> str:
    return _canonical_hash(
        {
            key: item
            for key, item in value.items()
            if key not in {"generated_at", "payload_hash"}
        }
    )


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _raw_content_hash(value: Any) -> str:
    if isinstance(value, str):
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    return _canonical_hash(value)


def _evaluate_native_spec04(
    request: StageEvaluationRequest,
    candidate: EvaluationInput,
    release_root: Path,
) -> StageEvaluation:
    execution = run_release_python_kernel(
        release_root=release_root,
        kernel_relative=_NATIVE_VALIDATORS[request.stage_key],
        args=("validate-run", "--run-dir", str(candidate.bundle_root)),
        cwd=request.workdir,
        timeout_seconds=86_400,
        accepted_returncodes=(0, 1, 2),
    )
    passed = execution.returncode == 0
    findings: list[Mapping[str, Any]] = []
    if not passed:
        findings.append(
            {
                "code": "independent_native_validation_failed",
                "stage": request.stage_key,
                "blocking": True,
                "stderr": execution.stderr[-2000:],
            }
        )
    gate_results = {gate: passed for gate in request.required_gates}
    if request.stage_key == "outline_reconstruction" and passed:
        accuracy = _outline_accuracy_evidence(candidate.bundle_root)
        target_met = accuracy["accuracy_basis_points"] >= 9_900
        gate_results["outline_accuracy_at_least_99_percent"] = target_met
        findings.append(
            {
                "code": "outline_accuracy_measurement",
                "stage": request.stage_key,
                "blocking": not target_met,
                **accuracy,
            }
        )
    return StageEvaluation(
        gate_results=gate_results,
        findings=tuple(findings),
    )


def _outline_accuracy_evidence(root: Path) -> dict[str, Any]:
    """Recompute the source-evidenced outline score without trusting summaries."""

    outline = _read_json(
        _required(root, "structure/source_outline_ledger.json"),
        "source outline ledger",
    )
    final_toc = _read_json(
        _required(root, "structure/final_toc_plan.json"),
        "final TOC plan",
    )
    evidence_rows = outline.get("source_outline_evidence")
    source_entries = outline.get("source_toc_entries")
    nodes = outline.get("body_hierarchy")
    final_entries = final_toc.get("entries")
    if (
        not isinstance(evidence_rows, list)
        or not evidence_rows
        or not isinstance(source_entries, list)
        or not isinstance(nodes, list)
        or not nodes
        or not isinstance(final_entries, list)
    ):
        raise StageEntrypointError(
            "outline_accuracy_evidence_invalid",
            "outline accuracy evidence is missing or malformed",
            exit_code=3,
        )
    evidence_ids = {
        row.get("evidence_id")
        for row in evidence_rows
        if isinstance(row, dict)
        and isinstance(row.get("evidence_id"), str)
        and row.get("evidence_id")
    }
    if len(evidence_ids) != len(evidence_rows):
        raise StageEntrypointError(
            "outline_accuracy_evidence_invalid",
            "source outline evidence IDs are missing or duplicated",
            exit_code=3,
        )
    node_by_id = {
        row.get("node_id"): row
        for row in nodes
        if isinstance(row, dict)
        and isinstance(row.get("node_id"), str)
        and row.get("node_id")
    }
    if len(node_by_id) != len(nodes):
        raise StageEntrypointError(
            "outline_accuracy_evidence_invalid",
            "outline node IDs are missing or duplicated",
            exit_code=3,
        )
    final_by_node = {
        row.get("node_id"): row
        for row in final_entries
        if isinstance(row, dict)
        and isinstance(row.get("node_id"), str)
        and row.get("node_id")
    }
    if len(final_by_node) != len(final_entries):
        raise StageEntrypointError(
            "outline_accuracy_evidence_invalid",
            "final TOC node IDs are missing or duplicated",
            exit_code=3,
        )

    total_units = 0
    correct_units = 0
    covered_node_ids: set[str] = set()
    for entry in source_entries:
        if not isinstance(entry, dict) or entry.get("scope_status") != "included":
            continue
        total_units += 1
        target = entry.get("target_node_id")
        node = node_by_id.get(target)
        if isinstance(node, dict):
            covered_node_ids.add(str(target))
        entry_id = entry.get("entry_id")
        entry_evidence = entry.get("source_outline_evidence_ids")
        node_entries = node.get("source_toc_entry_ids") if isinstance(node, dict) else None
        final = final_by_node.get(target)
        valid = (
            isinstance(entry_id, str)
            and bool(entry_id)
            and isinstance(node, dict)
            and entry.get("match_status")
            in {
                "exact",
                "approved_normalization",
                "source_supported_structural_title",
            }
            and isinstance(entry_evidence, list)
            and bool(entry_evidence)
            and all(item in evidence_ids for item in entry_evidence)
            and isinstance(node_entries, list)
            and entry_id in node_entries
            and isinstance(final, dict)
            and final.get("title") == (node.get("final_toc") or {}).get("title")
            and final.get("level") == node.get("level")
        )
        if valid:
            correct_units += 1

    for node_id, node in node_by_id.items():
        if node_id in covered_node_ids:
            continue
        total_units += 1
        node_evidence = node.get("source_outline_evidence_ids")
        heading_evidence = node.get("heading_evidence_block_ids")
        final = final_by_node.get(node_id)
        level = node.get("level")
        valid = (
            isinstance(level, int)
            and not isinstance(level, bool)
            and level >= 0
            and isinstance(node.get("title"), str)
            and bool(node.get("title"))
            and isinstance(node_evidence, list)
            and bool(node_evidence)
            and all(item in evidence_ids for item in node_evidence)
            and isinstance(heading_evidence, list)
            and bool(heading_evidence)
            and isinstance(final, dict)
            and final.get("title") == (node.get("final_toc") or {}).get("title")
            and final.get("level") == level
        )
        if valid:
            correct_units += 1

    if total_units < 1:
        raise StageEntrypointError(
            "outline_accuracy_evidence_empty",
            "outline accuracy denominator is empty",
            exit_code=3,
        )
    accuracy_basis_points = correct_units * 10_000 // total_units
    return {
        "correct_units": correct_units,
        "total_units": total_units,
        "accuracy_basis_points": accuracy_basis_points,
        "threshold_basis_points": 9_900,
        "measurement_contract": "source-evidenced-outline-units/v1",
    }


def _evaluate_spec05(
    request: StageEvaluationRequest,
    candidate: EvaluationInput,
    release_root: Path,
) -> StageEvaluation:
    root = candidate.bundle_root
    review_state_path = root / "spec05/reports/needs_review.json"
    if review_state_path.is_file():
        return _evaluate_spec05_review_candidate(root)
    stage = _read_json(
        _required(root, "spec05/manifests/spec05_native_stage_manifest.json"),
        "Spec 05 stage manifest",
    )
    delivery_set = _read_json(
        _required(root, "spec05/manifests/delivery_set_manifest.json"),
        "delivery set manifest",
    )
    volumes = _delivery_volumes(root / "spec05", delivery_set)
    release = _read_json(
        _required(release_root, "release-manifest.json"),
        "release manifest",
    )
    expected_class_hash = ((release.get("template") or {}).get("class_sha256"))
    limits_pass = True
    template_pass = isinstance(expected_class_hash, str)
    profile_pass = True
    compile_pass = True
    findings: list[Mapping[str, Any]] = []
    for row in volumes:
        limits = _inspect_delivery_zip(row["zip"])
        try:
            profile = _validate_spec05_delivery_profile(
                release_root=release_root,
                spec05_root=root / "spec05",
                volume_row=row["manifest_row"],
                delivery_zip=row["zip"],
                release_manifest=release,
            )
        except (StageEntrypointError, OSError, ValueError, KeyError) as exc:
            profile = {
                "passed": False,
                "template_passed": False,
                "code": (
                    exc.code
                    if isinstance(exc, StageEntrypointError)
                    else "delivery_profile_validation_failed"
                ),
                "message": str(exc),
            }
        limits_pass = limits_pass and limits["passed"] and profile["passed"]
        profile_pass = profile_pass and profile["passed"]
        template_pass = template_pass and (
            limits.get("class_sha256") == expected_class_hash
            and profile["template_passed"]
        )
        if not profile["passed"]:
            findings.append(
                {
                    "code": profile.get(
                        "code",
                        "delivery_profile_validation_failed",
                    ),
                    "volume_id": row["volume_id"],
                    "blocking": True,
                    "message": profile.get(
                        "message",
                        "formal delivery profile validation failed",
                    ),
                }
            )
        try:
            _independent_compile(
                row["zip"],
                request.workdir / "independent-spec05" / row["volume_id"],
            )
        except StageEntrypointError as exc:
            compile_pass = False
            findings.append(
                {
                    "code": exc.code,
                    "volume_id": row["volume_id"],
                    "blocking": True,
                    "message": str(exc),
                }
            )
    formal = (
        stage.get("status") == "passed"
        and stage.get("spec_status") == "passed"
        and stage.get("promotion_class") == "formal_native"
        and delivery_set.get("schema_version")
        == "spec05-delivery-set-manifest/1.2"
        and delivery_set.get("spec_status") == "passed"
        and profile_pass
    )
    return StageEvaluation(
        gate_results={
            "formal_native_renderer_used": formal,
            "protected_template_unchanged": template_pass,
            "delivery_limits_passed": limits_pass,
            "xelatex_recompile_passed": compile_pass,
        },
        findings=tuple(findings),
    )


def _evaluate_spec05_review_candidate(root: Path) -> StageEvaluation:
    review_path = _required(root, "spec05/reports/needs_review.json")
    warning_path = _required(root, "spec05/reports/compile_warnings.json")
    render_path = _required(root, "spec05/final_render_pack/manifest.json")
    provenance_path = _required(
        root,
        "spec05/reports/final_pdf_page_provenance.json",
    )
    compile_log = _required(root, "spec05/build/final/main.log")
    review = _read_json(review_path, "Spec 05 review state")
    warnings = _read_json(warning_path, "Spec 05 compile warnings")
    bound_warning = _bound_file(root / "spec05", review, "warning_report")
    events = warnings.get("events")
    open_events = [
        row
        for row in events
        if isinstance(row, dict)
        and row.get("classification") == "C2_REVIEW_REQUIRED_OPEN"
    ] if isinstance(events, list) else []
    open_fingerprints = [row.get("fingerprint") for row in open_events]
    if (
        bound_warning != warning_path
        or review.get("schema_version") != "spec05-review-state/1.0"
        or review.get("failure_code") != "COMPILE_REVIEW_OPEN"
        or review.get("spec_status") != "needs_review"
        or warnings.get("schema_version") != "compile-warnings/3.0"
        or warnings.get("status") != "needs_review"
        or warnings.get("blocking_findings") != []
        or not open_events
        or len(set(open_fingerprints)) != len(open_fingerprints)
        or not all(_is_sha256(value) for value in open_fingerprints)
    ):
        raise StageEntrypointError(
            "spec05_review_candidate_invalid",
            "Spec 05 review candidate is not an evidence-bound warning-only handoff",
        )
    evidence_paths = (
        review_path,
        warning_path,
        render_path,
        provenance_path,
        compile_log,
    )
    evidence_refs = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in evidence_paths
    ]
    return StageEvaluation(
        gate_results={
            "formal_native_renderer_used": False,
            "protected_template_unchanged": False,
            "delivery_limits_passed": False,
            "xelatex_recompile_passed": True,
        },
        findings=(
            {
                "code": "spec05_compile_warning_review_open",
                "blocking": True,
                "responsible_stage": "deterministic_elegantbook",
                "recovery_stage": "deterministic_elegantbook",
                "warning_fingerprints": [
                    str(fingerprint) for fingerprint in open_fingerprints
                ],
                "evidence_refs": evidence_refs,
                "handoff": {
                    "summary": (
                        "XeLaTeX completed and produced a full render pack, but "
                        "non-blocking compile warnings require exact visual closure."
                    ),
                    "required_action": (
                        "Inspect the hash-bound rendered pages, submit an approved "
                        "spec05-warning-review/1.0 payload for every fingerprint, "
                        "then resume only deterministic_elegantbook."
                    ),
                    "resume_stage": "deterministic_elegantbook",
                },
            },
        ),
        disposition="needs_review",
    )


def _evaluate_latex_audit(
    request: StageEvaluationRequest,
    candidate: EvaluationInput,
) -> StageEvaluation:
    root = candidate.bundle_root
    manifest = _read_json(
        _required(root, "manifests/readonly_latex_audit.json"),
        "read-only LaTeX audit manifest",
    )
    volumes = manifest.get("volumes")
    if not isinstance(volumes, list) or not volumes:
        raise StageEntrypointError(
            "latex_audit_manifest_invalid",
            "read-only audit manifest has no volumes",
        )
    readonly = manifest.get("input_bytes_unchanged") is True
    compile_errors_zero = True
    missing_glyphs_zero = True
    overflow_zero = True
    findings: list[Mapping[str, Any]] = []
    for row in volumes:
        if not isinstance(row, dict):
            raise StageEntrypointError(
                "latex_audit_manifest_invalid",
                "audit volume row is invalid",
            )
        zip_path = _bound_file(root, row, "delivery_zip")
        audit_path = _bound_file(root, row, "audit_report")
        audit = _read_json(audit_path, "LaTeX audit report")
        readonly = readonly and (
            audit.get("mode") == "audit"
            and not audit.get("changes")
            and not audit.get("replacement_zip")
        )
        try:
            compile_evidence = _independent_compile(
                zip_path,
                request.workdir / "independent-audit" / str(row.get("volume_id")),
            )
        except StageEntrypointError as exc:
            compile_errors_zero = False
            findings.append(
                {
                    "code": exc.code,
                    "volume_id": row.get("volume_id"),
                    "blocking": True,
                    "message": str(exc),
                }
            )
            continue
        log = compile_evidence.log
        missing_glyphs_zero = missing_glyphs_zero and "Missing character:" not in log
        overflow_zero = overflow_zero and _large_overflow_count(log) == 0
    return StageEvaluation(
        gate_results={
            "audit_is_readonly": readonly,
            "compile_errors_zero": compile_errors_zero,
            "missing_glyphs_zero": missing_glyphs_zero,
            "obvious_overflow_zero": overflow_zero,
        },
        findings=tuple(findings),
    )


def _evaluate_full_page_review_contract(
    request: StageEvaluationRequest,
    candidate: EvaluationInput,
    release_root: Path,
) -> StageEvaluation:
    root = candidate.bundle_root
    review = _read_json(
        _required(root, "reports/page_review.json"),
        "full-page review",
    )
    try:
        contract = validate_page_review_contract(
            candidate_root=root,
            review=review,
            release_root=release_root,
            expected_release_sha256=request.release_manifest_sha256,
        )
    except PageReviewContractError as exc:
        return StageEvaluation(
            gate_results={
                "review_pdf_hash_bound": False,
                "every_page_reviewed": False,
                "source_fidelity_reviewed": False,
                "blocking_findings_zero": False,
            },
            findings=(
                {
                    "code": exc.code,
                    "blocking": True,
                    "message": str(exc),
                },
            ),
            disposition="failed",
        )
    blockers_zero = contract.blockers == 0
    return StageEvaluation(
        gate_results={
            "review_pdf_hash_bound": True,
            "every_page_reviewed": True,
            "source_fidelity_reviewed": True,
            "blocking_findings_zero": blockers_zero,
        },
        findings=contract.findings,
        disposition=None if blockers_zero else "needs_review",
    )


def _evaluate_full_page_review(
    request: StageEvaluationRequest,
    candidate: EvaluationInput,
) -> StageEvaluation:
    root = candidate.bundle_root
    review = _read_json(
        _required(root, "reports/page_review.json"),
        "full-page review",
    )
    source = _bound_file(root, review, "source_pdf")
    source_bound = (
        review.get("source_pdf_sha256") == sha256_file(source)
        and _pdf_page_count(source) == review.get("source_page_count")
    )
    source_page_count = review.get("source_page_count")
    if (
        not isinstance(source_page_count, int)
        or isinstance(source_page_count, bool)
        or source_page_count < 1
    ):
        raise StageEntrypointError(
            "full_page_review_invalid",
            "source page count must be a positive integer",
        )
    source_raster_hashes = _pdf_page_raster_sha256(source)
    source_bound = source_bound and len(source_raster_hashes) == source_page_count
    input_contract = _read_json(
        _required(root, "contracts/input_contract.json"),
        "Spec 01 input contract",
    )
    source_trace = _read_json(
        _required(root, "contracts/source_trace.json"),
        "Spec 01 source trace",
    )
    material_identity = input_contract.get("material_identity")
    traced_source = source_trace.get("source_pdf")
    source_bound = source_bound and (
        isinstance(material_identity, dict)
        and isinstance(traced_source, dict)
        and material_identity.get("source_pdf_sha256") == sha256_file(source)
        and material_identity.get("source_pdf_size_bytes") == source.stat().st_size
        and material_identity.get("page_count") == source_page_count
        and traced_source.get("sha256") == sha256_file(source)
        and traced_source.get("size_bytes") == source.stat().st_size
        and traced_source.get("page_count") == source_page_count
    )

    delivery = _read_json(
        _required(root, "spec05/manifests/delivery_set_manifest.json"),
        "Stage 8 delivery set manifest",
    )
    delivery_rows = delivery.get("volumes")
    if (
        delivery.get("schema_version") != "spec05-delivery-set-manifest/1.2"
        or delivery.get("spec_status") != "passed"
        or not isinstance(delivery_rows, list)
        or len(delivery_rows) not in {1, 2}
        or delivery.get("volume_count") != len(delivery_rows)
    ):
        raise StageEntrypointError(
            "delivery_set_manifest_invalid",
            "Stage 10 must review the exact closed Stage 8 delivery set",
        )
    delivery_by_volume: dict[str, tuple[Path, str]] = {}
    delivery_sequence: list[str] = []
    for row in delivery_rows:
        if not isinstance(row, dict):
            raise StageEntrypointError(
                "delivery_set_manifest_invalid",
                "Stage 8 delivery volume is invalid",
            )
        volume_id = str(row.get("volume_id") or "")
        if not volume_id or volume_id in delivery_by_volume:
            raise StageEntrypointError(
                "delivery_set_manifest_invalid",
                "Stage 8 volume IDs are missing or duplicated",
            )
        final_pdf = _bound_file(root / "spec05", row, "final_pdf")
        delivery_sequence.append(volume_id)
        delivery_by_volume[volume_id] = (final_pdf, sha256_file(final_pdf))

    volumes = review.get("volumes")
    if not isinstance(volumes, list) or not volumes:
        raise StageEntrypointError(
            "full_page_review_invalid",
            "full-page review has no delivery volumes",
        )
    reviewer = review.get("reviewer")
    response_ids = reviewer.get("response_ids") if isinstance(reviewer, dict) else None
    calls = reviewer.get("calls") if isinstance(reviewer, dict) else None
    call_chain_bound = (
        isinstance(response_ids, list)
        and bool(response_ids)
        and len(response_ids) == len(set(response_ids))
        and all(isinstance(value, str) and bool(value.strip()) for value in response_ids)
        and isinstance(calls, list)
        and len(calls) == len(response_ids)
        and reviewer.get("call_audit_sha256") == _canonical_hash(calls)
        and [
            row.get("response_id")
            for row in calls
            if isinstance(row, dict)
        ]
        == response_ids
        and all(
            isinstance(row, dict)
            and row.get("release_manifest_sha256")
            == request.release_manifest_sha256
            and row.get("prompt_sha256") == reviewer.get("prompt_sha256")
            and row.get("schema_sha256") == reviewer.get("schema_sha256")
            and all(
                _is_sha256(row.get(field))
                for field in ("call_id", "input_sha256", "output_sha256")
            )
            for row in calls
        )
    )
    reviewer_bound = (
        isinstance(reviewer, dict)
        and reviewer.get("schema_version")
        == "luceon.worker-v3-visual-review-provider/v1"
        and reviewer.get("purpose") == "full_page_source_fidelity_review"
        and isinstance(reviewer.get("provider"), str)
        and bool(reviewer["provider"].strip())
        and isinstance(reviewer.get("model"), str)
        and bool(reviewer["model"].strip())
        and isinstance(reviewer.get("response_id"), str)
        and bool(reviewer["response_id"].strip())
        and reviewer.get("release_manifest_sha256")
        == request.release_manifest_sha256
        and _is_sha256(reviewer.get("prompt_sha256"))
        and _is_sha256(reviewer.get("schema_sha256"))
        and _is_sha256(reviewer.get("input_manifest_sha256"))
        and _is_sha256(reviewer.get("call_audit_sha256"))
        and call_chain_bound
    )
    pdf_bound = source_bound
    every_page = True
    fidelity = (
        review.get("review_scope") == "all_pages_source_fidelity"
        and reviewer_bound
    )
    blockers = 0
    review_sequence: list[str] = []
    review_input_volumes: list[dict[str, Any]] = []
    for volume in volumes:
        if not isinstance(volume, dict):
            raise StageEntrypointError(
                "full_page_review_invalid",
                "full-page review volume is invalid",
            )
        volume_id = str(volume.get("volume_id") or "")
        expected_delivery = delivery_by_volume.get(volume_id)
        if expected_delivery is None or volume_id in review_sequence:
            raise StageEntrypointError(
                "full_page_review_invalid",
                "Stage 10 volume mapping differs from Stage 8",
            )
        pdf = _bound_file(root, volume, "candidate_pdf")
        page_count = _pdf_page_count(pdf)
        rendered_review_images = _pdf_page_review_jpeg_sha256(pdf)
        pdf_bound = pdf_bound and (
            volume.get("candidate_pdf_sha256") == sha256_file(pdf)
            and volume.get("page_count") == page_count
            and pdf == expected_delivery[0]
            and sha256_file(pdf) == expected_delivery[1]
            and len(rendered_review_images) == page_count
        )
        review_sequence.append(volume_id)
        review_input_volumes.append(
            {
                "volume_id": volume_id,
                "candidate_pdf_sha256": sha256_file(pdf),
                "page_count": page_count,
            }
        )
        pages = volume.get("pages")
        if not isinstance(pages, list):
            every_page = False
            fidelity = False
            continue
        page_numbers = [row.get("page") for row in pages if isinstance(row, dict)]
        every_page = every_page and page_numbers == list(range(1, page_count + 1))
        for page in pages:
            if not isinstance(page, dict):
                every_page = False
                fidelity = False
                continue
            raster = _bound_file(root, page, "image")
            page_number = page.get("page")
            every_page = every_page and (
                isinstance(page_number, int)
                and not isinstance(page_number, bool)
                and 1 <= page_number <= len(rendered_review_images)
                and page.get("image_sha256") == sha256_file(raster)
                and page.get("image_sha256")
                == rendered_review_images[page_number - 1]
            )
            source_evidence = page.get("source_evidence")
            source_pages: list[int] = []
            evidence_valid = isinstance(source_evidence, list) and bool(source_evidence)
            if evidence_valid:
                for evidence in source_evidence:
                    source_page = (
                        evidence.get("source_page")
                        if isinstance(evidence, dict)
                        else None
                    )
                    if (
                        not isinstance(evidence, dict)
                        or not isinstance(source_page, int)
                        or isinstance(source_page, bool)
                        or source_page < 1
                        or source_page > source_page_count
                        or evidence.get("source_pdf_sha256")
                        != review.get("source_pdf_sha256")
                        or evidence.get("source_page_raster_sha256")
                        != source_raster_hashes[source_page - 1]
                        or evidence.get("evidence_kind") != "full_source_page"
                    ):
                        evidence_valid = False
                        break
                    source_pages.append(source_page)
                evidence_valid = evidence_valid and len(source_pages) == len(
                    set(source_pages)
                )
            page_findings = page.get("findings", [])
            if not isinstance(page_findings, list):
                blockers += 1
                page_blockers = 1
            else:
                page_blockers = sum(
                    isinstance(item, dict) and item.get("blocking") is True
                    for item in page_findings
                )
                blockers += page_blockers
            status = page.get("status")
            status_consistent = (
                status == "reviewed_passed" and page_blockers == 0
            ) or (
                status == "reviewed_failed" and page_blockers > 0
            )
            fidelity = fidelity and evidence_valid and status_consistent
    every_page = every_page and review_sequence == delivery_sequence
    expected_review_input = {
        "source_pdf_sha256": review.get("source_pdf_sha256"),
        "source_page_count": source_page_count,
        "volumes": review_input_volumes,
    }
    fidelity = fidelity and (
        isinstance(reviewer, dict)
        and reviewer.get("input_manifest_sha256")
        == _canonical_hash(expected_review_input)
    )
    declared_blockers = review.get("blocking_findings")
    blocker_summary_valid = (
        isinstance(declared_blockers, int)
        and not isinstance(declared_blockers, bool)
        and declared_blockers >= 0
        and declared_blockers == blockers
    )
    return StageEvaluation(
        gate_results={
            "review_pdf_hash_bound": pdf_bound,
            "every_page_reviewed": every_page,
            "source_fidelity_reviewed": fidelity,
            "blocking_findings_zero": blocker_summary_valid and blockers == 0,
        },
    )


def _evaluate_delivery_recompile(
    request: StageEvaluationRequest,
    candidate: EvaluationInput,
    release_root: Path,
) -> StageEvaluation:
    root = candidate.bundle_root
    manifest = _read_json(
        _required(root, "manifests/delivery_recompile.json"),
        "delivery recompile manifest",
    )
    delivery = _read_json(
        _required(root, "spec05/manifests/delivery_set_manifest.json"),
        "Stage 8 delivery set manifest",
    )
    delivery_rows = delivery.get("volumes")
    if (
        delivery.get("schema_version") != "spec05-delivery-set-manifest/1.2"
        or delivery.get("spec_status") != "passed"
        or not isinstance(delivery_rows, list)
        or len(delivery_rows) not in {1, 2}
        or delivery.get("volume_count") != len(delivery_rows)
    ):
        raise StageEntrypointError(
            "delivery_set_manifest_invalid",
            "Stage 8 delivery set is not a closed one/two-volume set",
        )
    stage8_sequence: list[str] = []
    stage8_zips: dict[str, Path] = {}
    for row in delivery_rows:
        if not isinstance(row, dict):
            raise StageEntrypointError(
                "delivery_set_manifest_invalid",
                "Stage 8 delivery volume is invalid",
            )
        volume_id = str(row.get("volume_id") or "")
        if not volume_id or volume_id in stage8_zips:
            raise StageEntrypointError(
                "delivery_set_manifest_invalid",
                "Stage 8 delivery volume IDs are missing or duplicated",
            )
        stage8_sequence.append(volume_id)
        stage8_zips[volume_id] = _bound_file(
            root / "spec05",
            row,
            "delivery_zip",
        )
    volumes = manifest.get("volumes")
    if not isinstance(volumes, list) or not volumes:
        raise StageEntrypointError(
            "delivery_recompile_manifest_invalid",
            "delivery recompile manifest has no volumes",
        )
    target_path = _bound_file(root, manifest, "target_environment")
    target_environment = validate_target_environment(
        _read_json(target_path, "Overleaf target environment")
    )
    release_target = load_release_target_environment(release_root)
    target_bound = (
        target_environment == release_target
        and manifest.get("target_environment_sha256") == sha256_file(target_path)
    )
    zip_bound = True
    compile_pass = True
    pdf_recorded = True
    complete = (
        manifest.get("schema_version")
        == "luceon.worker-v3-delivery-recompile/v1"
        and manifest.get("compiler") == "overleaf-adapter-latexmk-xelatex"
        and manifest.get("adapter_protocol") == ADAPTER_PROTOCOL
        and target_bound
    )
    findings: list[Mapping[str, Any]] = []
    stage11_sequence: list[str] = []
    for volume in volumes:
        if not isinstance(volume, dict):
            complete = False
            continue
        volume_id = str(volume.get("volume_id") or "")
        stage11_sequence.append(volume_id)
        zip_path = _bound_file(root, volume, "delivery_zip")
        zip_bound = zip_bound and (
            volume_id in stage8_zips
            and zip_path == stage8_zips[volume_id]
            and volume.get("delivery_zip_sha256") == sha256_file(zip_path)
        )
        try:
            evidence = compile_overleaf_delivery(
                zip_path,
                request.workdir / "independent-delivery" / str(volume.get("volume_id")),
                target_environment=release_target,
                role="independent_evaluator",
            )
        except StageEntrypointError as exc:
            compile_pass = False
            findings.append(
                {
                    "code": exc.code,
                    "volume_id": volume.get("volume_id"),
                    "blocking": True,
                    "message": str(exc),
                }
            )
            continue
        zip_bound = zip_bound and evidence.zip_sha256 == sha256_file(zip_path)
        recorded_pdf = _bound_file(root, volume, "compiled_pdf")
        reviewed_pdf = _bound_file(root, volume, "reviewed_pdf")
        recorded_log = _bound_file(root, volume, "compile_log")
        recorded_adapter_manifest = _bound_file(
            root,
            volume,
            "overleaf_result_manifest",
        )
        reviewed_rasters = _pdf_page_raster_sha256(reviewed_pdf)
        recorded_rasters = _pdf_page_raster_sha256(recorded_pdf)
        independent_rasters = _pdf_page_raster_sha256(evidence.pdf_path)
        pdf_recorded = pdf_recorded and (
            volume.get("compiled_pdf_sha256") == sha256_file(recorded_pdf)
            and volume.get("reviewed_pdf_sha256") == sha256_file(reviewed_pdf)
            and volume.get("compiled_page_count") == _pdf_page_count(recorded_pdf)
            and evidence.page_count == volume.get("compiled_page_count")
            and volume.get("raster_profile") == dict(PDF_RASTER_PROFILE)
            and volume.get("reviewed_page_raster_sha256") == reviewed_rasters
            and volume.get("compiled_page_raster_sha256") == recorded_rasters
            and reviewed_rasters == recorded_rasters == independent_rasters
            and volume.get("visual_equivalent") is True
        )
        complete = complete and (
            volume.get("compile_log_sha256") == sha256_file(recorded_log)
            and volume.get("overleaf_result_manifest_sha256")
            == sha256_file(recorded_adapter_manifest)
            and volume.get("overleaf_runtime_identity_sha256")
            == release_target["adapter_runtime_identity_sha256"]
            and volume.get("overleaf_adapter_image_digest")
            == release_target["adapter_image_digest"]
            and evidence.runtime_identity_sha256
            == release_target["adapter_runtime_identity_sha256"]
            and evidence.adapter_image_digest
            == release_target["adapter_image_digest"]
        )
        complete = complete and all(
            isinstance(volume.get(name), str) and volume[name]
            for name in (
                "volume_id",
                "delivery_zip_sha256",
                "compiled_pdf_sha256",
                "reviewed_pdf_sha256",
                "compile_log_sha256",
                "overleaf_result_manifest_sha256",
                "overleaf_request_id",
            )
        )
    zip_bound = zip_bound and stage11_sequence == stage8_sequence
    return StageEvaluation(
        gate_results={
            "downloaded_zip_hash_verified": zip_bound,
            "independent_xelatex_recompile_passed": compile_pass,
            "compiled_pdf_hash_recorded": pdf_recorded,
            "delivery_manifest_complete": complete,
        },
        findings=tuple(findings),
    )


def _pdf_page_raster_sha256(path: Path) -> list[str]:
    try:
        import fitz
    except ImportError as exc:
        raise StageEntrypointError(
            "pymupdf_unavailable",
            "deterministic PDF raster verification requires PyMuPDF",
        ) from exc
    hashes: list[str] = []
    try:
        with fitz.open(path) as document:
            for page in document:
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(
                        PDF_RASTER_PROFILE["scale"],
                        PDF_RASTER_PROFILE["scale"],
                    ),
                    colorspace=fitz.csRGB,
                    alpha=PDF_RASTER_PROFILE["alpha"],
                    annots=PDF_RASTER_PROFILE["annots"],
                )
                digest = hashlib.sha256()
                digest.update(
                    (
                        f"{pixmap.width}\n{pixmap.height}\n"
                        f"{pixmap.n}\n{pixmap.alpha}\n"
                    ).encode("ascii")
                )
                digest.update(pixmap.samples)
                hashes.append(digest.hexdigest())
    except Exception as exc:
        if isinstance(exc, StageEntrypointError):
            raise
        raise StageEntrypointError(
            "pdf_raster_failed",
            f"cannot render PDF deterministically: {path.name}",
        ) from exc
    if not hashes:
        raise StageEntrypointError(
            "pdf_raster_empty",
            f"PDF has no renderable pages: {path.name}",
        )
    return hashes


def _review_page_jpeg_bytes(page: Any) -> bytes:
    try:
        from PIL import Image
        import fitz
    except ImportError as exc:
        raise StageEntrypointError(
            "page_review_renderer_unavailable",
            "page review requires PyMuPDF and Pillow",
        ) from exc
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(
            PDF_REVIEW_IMAGE_PROFILE["scale"],
            PDF_REVIEW_IMAGE_PROFILE["scale"],
        ),
        colorspace=fitz.csRGB,
        alpha=PDF_REVIEW_IMAGE_PROFILE["alpha"],
        annots=True,
    )
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    try:
        for quality in (74, 66, 58, 50, 42):
            output = io.BytesIO()
            image.save(
                output,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=False,
            )
            payload = output.getvalue()
            if len(payload) <= PDF_REVIEW_IMAGE_PROFILE["max_bytes"]:
                return payload
        width, height = image.size
        reduced = image.resize(
            (max(600, int(width * 0.8)), max(800, int(height * 0.8))),
            Image.Resampling.LANCZOS,
        )
        try:
            output = io.BytesIO()
            reduced.save(output, format="JPEG", quality=50, optimize=True)
            payload = output.getvalue()
        finally:
            reduced.close()
    finally:
        image.close()
    if len(payload) > PDF_REVIEW_IMAGE_PROFILE["max_bytes"]:
        raise StageEntrypointError(
            "page_review_image_too_large",
            "review page image exceeds the immutable payload budget",
        )
    return payload


def _pdf_page_review_jpeg_sha256(path: Path) -> list[str]:
    try:
        import fitz
    except ImportError as exc:
        raise StageEntrypointError(
            "pymupdf_unavailable",
            "page review requires PyMuPDF",
        ) from exc
    try:
        with fitz.open(path) as document:
            return [
                hashlib.sha256(_review_page_jpeg_bytes(page)).hexdigest()
                for page in document
            ]
    except Exception as exc:
        if isinstance(exc, StageEntrypointError):
            raise
        raise StageEntrypointError(
            "page_review_render_failed",
            f"cannot render page-review images for {path.name}",
        ) from exc


def _evaluate_readiness(
    request: StageEvaluationRequest,
    candidate: EvaluationInput,
) -> StageEvaluation:
    if request.control_plane_chain is None:
        raise StageEntrypointError(
            "control_plane_chain_missing",
            "Stage 12 requires an evaluator-owned control-plane snapshot",
            exit_code=3,
        )
    control_plane = request.control_plane_chain.payload
    control_rows = control_plane.get("promotions")
    if not isinstance(control_rows, list):
        raise StageEntrypointError(
            "control_plane_chain_invalid",
            "control-plane promotion chain is invalid",
            exit_code=3,
        )
    root = candidate.bundle_root
    readiness = _read_json(
        _required(root, "manifests/ready_for_user_acceptance.json"),
        "readiness manifest",
    )
    chain_path = _bound_file(root, readiness, "promotion_chain")
    chain = _read_json(chain_path, "promotion chain")
    promotions = chain.get("promotions")
    prior_stages = list(STAGE_GATES)[:-1]
    expected_promotions = [
        {
            "stage_key": row["stage_key"],
            "stage_version": row["stage_version"],
            "stage_run_id": row["stage_run_id"],
            "candidate_id": row["artifact_version"]["candidate_id"],
            "evaluation_id": row["evaluation"]["evaluation_id"],
            "promotion_id": row["promotion"]["promotion_id"],
            "artifact_sha256": row["promotion"]["artifact_sha256"],
            "evaluation_record_sha256": row["evaluation"]["record_sha256"],
            "promotion_record_sha256": row["promotion"]["record_sha256"],
            "evaluation_decision": "passed",
            "promotion_status": "promoted",
        }
        for row in control_rows
    ]
    all_promoted = (
        chain.get("schema_version") == "luceon.worker-v3-promotion-chain/v2"
        and chain.get("job_id") == control_plane.get("job_id")
        and chain.get("workflow_version")
        == control_plane.get("workflow_version")
        and chain.get("release_manifest_sha256")
        == request.release_manifest_sha256
        and chain.get("source_popo_manifest_sha256")
        == control_plane.get("source_popo_manifest_sha256")
        and isinstance(promotions, list)
        and promotions == expected_promotions
        and [row["stage_key"] for row in expected_promotions] == prior_stages
    )
    lineage_path = _bound_file(root, readiness, "lineage_attestation")
    lineage_attestation = _read_json(
        lineage_path,
        "page/database/MinIO lineage attestation",
    )
    lineage = (
        readiness.get("schema_version")
        == "luceon.worker-v3-ready-for-user-acceptance/v1"
        and readiness.get("machine_status") == "succeeded"
        and readiness.get("spec_status") == "passed"
        and readiness.get("readiness") == "ready_for_user_acceptance"
        and readiness.get("lineage_consistent") is True
        and readiness.get("promotion_chain_sha256") == sha256_file(chain_path)
        and readiness.get("lineage_attestation_sha256")
        == sha256_file(lineage_path)
        and lineage_attestation.get("schema_version")
        == "luceon.worker-v3-page-db-minio-lineage/v1"
        and lineage_attestation.get("job_id") == control_plane.get("job_id")
        and lineage_attestation.get("release_manifest_sha256")
        == request.release_manifest_sha256
        and lineage_attestation.get("source_popo_manifest_sha256")
        == control_plane.get("source_popo_manifest_sha256")
        and lineage_attestation.get("promotion_chain_sha256")
        == sha256_file(chain_path)
        and lineage_attestation.get("consistent") is True
        and lineage_attestation.get("open_blockers") == []
    )
    blockers = readiness.get("open_blockers")
    no_blockers = isinstance(blockers, list) and not blockers
    boundary = (
        readiness.get("human_accepted") is False
        and readiness.get("user_acceptance_record") is None
    )
    return StageEvaluation(
        gate_results={
            "all_prior_promotions_verified": all_promoted,
            "page_db_minio_lineage_consistent": lineage,
            "open_blockers_zero": no_blockers,
            "human_acceptance_not_self_attested": boundary,
        },
    )


def _delivery_volumes(root: Path, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = manifest.get("volumes")
    if (
        not isinstance(rows, list)
        or len(rows) not in {1, 2}
        or manifest.get("volume_count") != len(rows)
    ):
        raise StageEntrypointError(
            "delivery_set_invalid",
            "delivery set must contain exactly one or two volumes",
        )
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise StageEntrypointError(
                "delivery_set_invalid",
                f"delivery volume {index} is invalid",
            )
        zip_info = row.get("delivery_zip")
        if not isinstance(zip_info, dict):
            raise StageEntrypointError(
                "delivery_set_invalid",
                f"delivery volume {index} has no ZIP binding",
            )
        path = _bound_file(root, zip_info, None)
        result.append(
            {
                "volume_id": str(row.get("volume_id") or f"volume-{index + 1}"),
                "zip": path,
                "manifest_row": row,
            }
        )
    return result


def _load_release_module(
    release_root: Path,
    relative_path: str,
    module_name: str,
) -> Any:
    path = _required(release_root, relative_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise StageEntrypointError(
            "release_validator_unavailable",
            f"cannot load immutable validator {relative_path!r}",
            exit_code=3,
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _template_archive_members(
    archive_path: Path,
) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for info, name in _validated_zip_members(archive):
                if name in members:
                    raise StageEntrypointError(
                        "template_archive_duplicate_member",
                        f"template archive repeats {name!r}",
                    )
                members[name] = archive.read(info)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise StageEntrypointError(
            "template_archive_invalid",
            f"immutable template archive cannot be read: {exc}",
            exit_code=3,
        ) from exc
    return members


def _materialized_assets(
    materialization: Mapping[str, Any],
) -> dict[str, str]:
    assets: dict[str, str] = {}
    for key in ("copied_assets", "source_region_crops"):
        raw = materialization.get(key)
        rows = raw.values() if isinstance(raw, dict) else raw
        if not isinstance(rows, (list, tuple)) and not hasattr(rows, "__iter__"):
            raise ValueError(f"materialization {key!r} is invalid")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"materialization {key!r} row is invalid")
            path = row.get("project_path")
            digest = row.get("sha256")
            if not isinstance(path, str) or not _is_sha256(digest):
                raise ValueError(f"materialization {key!r} row is unbound")
            if path in assets and assets[path] != digest:
                raise ValueError("materialization path has conflicting hashes")
            assets[path] = digest
    presentation = materialization.get("presentation_assets")
    if not isinstance(presentation, list):
        raise ValueError("presentation asset materialization is invalid")
    for row in presentation:
        if not isinstance(row, dict):
            raise ValueError("presentation asset row is invalid")
        path = row.get("path")
        digest = row.get("sha256")
        if not isinstance(path, str) or not _is_sha256(digest):
            raise ValueError("presentation asset row is unbound")
        if path in assets and assets[path] != digest:
            raise ValueError("presentation path has conflicting hashes")
        assets[path] = digest
    return assets


def _canonical_source_tree(records: list[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda item: str(item["path"])):
        digest.update(
            json.dumps(
                {
                    "bytes": record["bytes"],
                    "path": record["path"],
                    "sha256": record["sha256"],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _validate_spec05_delivery_profile(
    *,
    release_root: Path,
    spec05_root: Path,
    volume_row: Mapping[str, Any],
    delivery_zip: Path,
    release_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    template = release_manifest.get("template")
    if not isinstance(template, dict):
        raise ValueError("release template identity is missing")
    identity = _read_json(
        _required(release_root, "references/template-identity.json"),
        "release template identity",
    )
    if (
        identity.get("schema_version")
        != "luceon.worker-v3-template-identity/v1"
        or identity.get("archive_sha256") != template.get("archive_sha256")
        or identity.get("tree_sha256") != template.get("tree_sha256")
        or identity.get("main_sha256") != template.get("main_sha256")
        or identity.get("class_sha256") != template.get("class_sha256")
        or identity.get("fixed_assets_sha256")
        != template.get("fixed_assets_sha256")
    ):
        raise ValueError("release template identity is internally inconsistent")
    template_archive = _required(
        release_root,
        str(template.get("archive_path") or ""),
    )
    if sha256_file(template_archive) != template.get("archive_sha256"):
        raise ValueError("release template archive hash drifted")
    template_members = _template_archive_members(template_archive)
    main_member = str(identity.get("main_member") or "")
    class_member = str(identity.get("class_member") or "")
    if (
        not main_member
        or not class_member
        or main_member not in template_members
        or class_member not in template_members
    ):
        raise ValueError("release template main/class members are absent")

    build_manifest_path = _bound_file(
        spec05_root,
        volume_row,
        "child_build_manifest",
    )
    child_root = build_manifest_path.parent.parent
    build = _read_json(build_manifest_path, "Spec 05 child build manifest")
    artifacts = build.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("Spec 05 child build artifacts are missing")
    contract_path = _bound_file(child_root, artifacts, "template_contract")
    rendered_body = _bound_file(child_root, artifacts, "rendered_body")
    capability = _bound_file(
        child_root,
        artifacts,
        "template_capability_manifest",
    )
    materialization_path = _required(
        child_root,
        "reports/asset_materialization_report.json",
    )
    contract = _read_json(contract_path, "template contract")
    materialization = _read_json(
        materialization_path,
        "asset materialization report",
    )
    immutable_rows = contract.get("immutable_files")
    if not isinstance(immutable_rows, list):
        raise ValueError("template contract immutable inventory is missing")
    contract_immutable: dict[str, str] = {}
    for row in immutable_rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("path"), str)
            or not _is_sha256(row.get("sha256"))
            or row["path"] in contract_immutable
        ):
            raise ValueError("template contract immutable inventory is invalid")
        contract_immutable[row["path"]] = row["sha256"]
    expected_immutable = {
        name: hashlib.sha256(payload).hexdigest()
        for name, payload in template_members.items()
        if name != main_member
    }
    main_contract = contract.get("main_template")
    template_contract_bound = (
        isinstance(contract.get("template_zip"), dict)
        and contract["template_zip"].get("sha256")
        == template.get("archive_sha256")
        and isinstance(main_contract, dict)
        and main_contract.get("sha256") == template.get("main_sha256")
        and contract_immutable == expected_immutable
    )

    compatibility_module = _load_release_module(
        release_root,
        "skills/cleanlatex-to-elegantbook/scripts/delivery_compatibility.py",
        "worker_v3_release_delivery_compatibility",
    )
    asset_module = _load_release_module(
        release_root,
        "skills/cleanlatex-to-elegantbook/scripts/delivery_asset_policy.py",
        "worker_v3_release_delivery_asset_policy",
    )
    api_module = _load_release_module(
        release_root,
        "skills/cleanlatex-to-elegantbook/scripts/template_local_api_usage.py",
        "worker_v3_release_template_local_api_usage",
    )
    contract_module = _load_release_module(
        release_root,
        (
            "skills/luceon-popo-to-refined-elegantbook/scripts/"
            "validate_intermediate_contracts.py"
        ),
        "worker_v3_release_intermediate_contracts",
    )
    compatibility = compatibility_module.audit_zip_transport(
        delivery_zip,
        rendered_body,
    )
    asset_report = asset_module.audit(
        delivery_zip,
        materialization_path,
        contract_path,
    )
    api_report = api_module.audit_template_local_api_usage(
        capability,
        rendered_body,
    )
    if (
        compatibility.get("spec_status") != "passed"
        or asset_report.get("spec_status") != "passed"
        or api_report.get("spec_status") != "passed"
    ):
        raise ValueError("release-native delivery policy recomputation failed")

    generated_body = compatibility.get("generated_body")
    parts = generated_body.get("parts") if isinstance(generated_body, dict) else None
    if not isinstance(parts, list) or not parts:
        raise ValueError("formal semantic body transport has no parts")
    part_names = {
        row.get("path")
        for row in parts
        if isinstance(row, dict) and isinstance(row.get("path"), str)
    }
    if len(part_names) != len(parts):
        raise ValueError("formal semantic body transport has duplicate parts")
    generated_assets = _materialized_assets(materialization)

    with zipfile.ZipFile(delivery_zip) as archive:
        members = _validated_zip_members(archive)
        info_by_name = {name: info for info, name in members}
        bytes_by_name = {
            name: archive.read(info)
            for info, name in members
        }
    for path, digest in contract_immutable.items():
        if (
            path not in bytes_by_name
            or hashlib.sha256(bytes_by_name[path]).hexdigest() != digest
        ):
            raise ValueError(f"frozen template member drifted: {path}")
    for path, digest in generated_assets.items():
        if (
            path not in bytes_by_name
            or hashlib.sha256(bytes_by_name[path]).hexdigest() != digest
        ):
            raise ValueError(f"materialized asset differs from its lineage: {path}")
    if (
        class_member not in bytes_by_name
        or hashlib.sha256(bytes_by_name[class_member]).hexdigest()
        != template.get("class_sha256")
    ):
        raise ValueError("elegantbook class differs from the release")

    main_bytes = bytes_by_name.get(main_member)
    if main_bytes is None:
        raise ValueError("formal delivery lacks the release-bound main template")
    try:
        main_text = main_bytes.decode("utf-8")
        rendered_text = rendered_body.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("formal TeX payload is not UTF-8") from exc
    if _BODY_DEFINITION_RE.search(rendered_text):
        raise ValueError("generated body defines a custom command or environment")
    if (
        not isinstance(main_contract, dict)
        or hashlib.sha256(
            contract_module.mask_main(main_text, contract).encode("utf-8")
        ).hexdigest()
        != main_contract.get("masked_main_sha256")
        or contract_module.api_inventory(main_text)
        != contract.get("custom_api_inventory")
        or contract_module.package_inventory(main_text)
        != contract.get("package_inventory")
        or contract_module.documentclass_inventory(main_text)
        != contract.get("documentclass")
    ):
        raise ValueError("root main.tex or template API drifted")

    bib_refs: set[str] = set()
    for name, payload in bytes_by_name.items():
        if PurePosixPath(name).suffix.lower() not in {".tex", ".cls"}:
            continue
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"template text is not UTF-8: {name}") from exc
        for match in re.finditer(
            r"\\addbibresource(?:\[[^]]*\])?\{([^{}]+\.bib)\}",
            text,
        ):
            bib_refs.add(
                (PurePosixPath(name).parent / match.group(1)).as_posix().lstrip("./")
            )
    allowed = {
        main_member,
        *contract_immutable,
        "body/generated-body.tex",
        *part_names,
        *generated_assets,
    }
    for name in bib_refs:
        if name in bytes_by_name and bytes_by_name[name] == b"":
            allowed.add(name)
    unexpected = sorted(set(bytes_by_name) - allowed)
    missing = sorted(allowed - set(bytes_by_name))
    if unexpected or missing:
        raise ValueError(
            f"formal delivery member set differs; unexpected={unexpected[:8]}, "
            f"missing={missing[:8]}"
        )

    fixed_members = identity.get("fixed_asset_members")
    if not isinstance(fixed_members, list):
        raise ValueError("release fixed-asset inventory is invalid")
    fixed_rows = [
        {
            "path": name,
            "bytes": len(bytes_by_name[name]),
            "sha256": hashlib.sha256(bytes_by_name[name]).hexdigest(),
        }
        for name in fixed_members
        if name in bytes_by_name
    ]
    fixed_passed = (
        len(fixed_rows) == len(fixed_members)
        and _canonical_source_tree(fixed_rows)
        == template.get("fixed_assets_sha256")
    )
    return {
        "passed": template_contract_bound and fixed_passed,
        "template_passed": template_contract_bound and fixed_passed,
        "code": None,
        "message": "",
        "ordinary_files": len(info_by_name),
    }


def _inspect_delivery_zip(path: Path) -> dict[str, Any]:
    if path.stat().st_size >= _MAX_ZIP_BYTES:
        return {
            "passed": False,
            "class_sha256": None,
            "reason": "delivery_zip_size_limit",
        }
    class_sha: str | None = None
    try:
        with zipfile.ZipFile(path) as archive:
            members = _validated_zip_members(archive)
            names = {name for _, name in members}
            for info, name in members:
                if PurePosixPath(name).suffix.lower() in _IMAGE_SUFFIXES:
                    if info.file_size > _MAX_IMAGE_BYTES:
                        return {
                            "passed": False,
                            "class_sha256": class_sha,
                            "reason": "delivery_raster_size_limit",
                        }
                if PurePosixPath(name).suffix.lower() == ".tex":
                    if info.file_size > _MAX_TEX_BYTES:
                        return {
                            "passed": False,
                            "class_sha256": class_sha,
                            "reason": "delivery_tex_size_limit",
                        }
                if name == "elegantbook.cls":
                    class_sha = hashlib.sha256(archive.read(info)).hexdigest()
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise StageEntrypointError(
            "delivery_zip_invalid",
            f"delivery ZIP cannot be read: {exc}",
        ) from exc
    return {
        "passed": (
            "main.tex" in names
            and "elegantbook.cls" in names
            and "body/generated-body.tex" in names
        ),
        "class_sha256": class_sha,
        "reason": None,
    }


def _validated_zip_members(
    archive: zipfile.ZipFile,
) -> list[tuple[zipfile.ZipInfo, str]]:
    members: list[tuple[zipfile.ZipInfo, str]] = []
    names: set[str] = set()
    total_bytes = 0
    for info in archive.infolist():
        name = _zip_member(info)
        if name in names:
            raise StageEntrypointError(
                "delivery_zip_duplicate_member",
                f"delivery ZIP repeats {name!r}",
            )
        names.add(name)
        if info.is_dir():
            continue
        unix_type = (info.external_attr >> 16) & 0o170000
        if unix_type not in {0, stat.S_IFREG}:
            raise StageEntrypointError(
                "delivery_zip_unsafe_member",
                f"delivery ZIP member {name!r} is not a regular file",
            )
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise StageEntrypointError(
                "delivery_zip_unsupported_compression",
                f"delivery ZIP member {name!r} uses an unapproved compression method",
            )
        if info.file_size > _MAX_ZIP_MEMBER_BYTES:
            raise StageEntrypointError(
                "delivery_zip_member_too_large",
                f"delivery ZIP member {name!r} exceeds the extraction limit",
            )
        ratio = info.file_size / max(info.compress_size, 1)
        if ratio > _MAX_ZIP_COMPRESSION_RATIO:
            raise StageEntrypointError(
                "delivery_zip_compression_ratio_exceeded",
                f"delivery ZIP member {name!r} has an unsafe compression ratio",
            )
        total_bytes += info.file_size
        if total_bytes > _MAX_ZIP_UNCOMPRESSED_BYTES:
            raise StageEntrypointError(
                "delivery_zip_uncompressed_size_exceeded",
                "delivery ZIP exceeds the total uncompressed-size limit",
            )
        members.append((info, name))
        if len(members) > _MAX_ZIP_FILES:
            raise StageEntrypointError(
                "delivery_zip_file_count_exceeded",
                "delivery ZIP exceeds the ordinary-file entity limit",
            )
    return members


def _copy_zip_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    target: Path,
) -> None:
    copied = 0
    with archive.open(info) as source, target.open("xb") as output:
        while True:
            chunk = source.read(min(1024 * 1024, info.file_size - copied + 1))
            if not chunk:
                break
            copied += len(chunk)
            if copied > info.file_size or copied > _MAX_ZIP_MEMBER_BYTES:
                raise StageEntrypointError(
                    "delivery_zip_member_size_mismatch",
                    f"delivery ZIP member {info.filename!r} expanded beyond its bound",
                )
            output.write(chunk)
    if copied != info.file_size:
        raise StageEntrypointError(
            "delivery_zip_member_size_mismatch",
            f"delivery ZIP member {info.filename!r} size differs from its header",
        )


def _independent_compile(zip_path: Path, workdir: Path) -> CompileEvidence:
    if workdir.exists() or workdir.is_symlink():
        raise StageEntrypointError(
            "independent_compile_workspace_exists",
            "independent compile workspace already exists",
            exit_code=3,
        )
    workdir.mkdir(parents=True, mode=0o700)
    project = workdir / "project"
    project.mkdir(mode=0o700)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = _validated_zip_members(archive)
            total_bytes = sum(info.file_size for info, _ in members)
            required_free = (
                total_bytes * 2
                + _MIN_EXTRACTION_HEADROOM_BYTES
            )
            if shutil.disk_usage(workdir).free < required_free:
                raise StageEntrypointError(
                    "delivery_compile_disk_headroom_insufficient",
                    "independent compile workspace lacks safe extraction headroom",
                    exit_code=3,
                )
            for info, name in members:
                target = (project / name).resolve()
                if project.resolve() not in target.parents:
                    raise StageEntrypointError(
                        "delivery_zip_path_escape",
                        f"delivery ZIP member {name!r} escapes extraction",
                    )
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                _copy_zip_member(archive, info, target)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise StageEntrypointError(
            "delivery_zip_invalid",
            f"delivery ZIP cannot be extracted: {exc}",
        ) from exc
    _required(project, "main.tex")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(workdir / "home"),
    }
    (workdir / "home").mkdir(mode=0o700)
    versions = {}
    for command, key in ((["xelatex", "--version"], "xelatex"), (["latexmk", "-v"], "latexmk")):
        try:
            result = subprocess.run(
                command,
                cwd=project,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StageEntrypointError(
                "xelatex_runtime_unavailable",
                f"required compiler command {command[0]!r} is unavailable",
                exit_code=3,
            ) from exc
        if result.returncode != 0:
            raise StageEntrypointError(
                "xelatex_runtime_unavailable",
                f"required compiler command {command[0]!r} failed",
                exit_code=3,
            )
        versions[key] = (result.stdout or result.stderr).splitlines()[0]
    try:
        compiled = subprocess.run(
            [
                "latexmk",
                "-xelatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                "main.tex",
            ],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=86_400,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StageEntrypointError(
            "independent_xelatex_failed",
            "independent XeLaTeX execution failed",
            exit_code=3,
        ) from exc
    log_path = project / "main.log"
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    if compiled.returncode != 0 or not (project / "main.pdf").is_file():
        raise StageEntrypointError(
            "independent_xelatex_failed",
            (compiled.stderr or compiled.stdout or log)[-2000:],
            exit_code=3,
        )
    pdf = project / "main.pdf"
    return CompileEvidence(
        zip_sha256=sha256_file(zip_path),
        pdf_path=pdf,
        pdf_sha256=sha256_file(pdf),
        page_count=_pdf_page_count(pdf),
        log=log,
        xelatex_version=versions["xelatex"],
        latexmk_version=versions["latexmk"],
    )


def _pdf_page_count(path: Path) -> int:
    try:
        import fitz

        with fitz.open(path) as document:
            count = int(document.page_count)
    except Exception:
        try:
            from pypdf import PdfReader

            count = len(PdfReader(str(path)).pages)
        except Exception as exc:
            raise StageEntrypointError(
                "pdf_unreadable",
                f"PDF cannot be opened for independent page counting: {path.name}",
            ) from exc
    if count < 1:
        raise StageEntrypointError("pdf_unreadable", "PDF contains no pages")
    return count


def _large_overflow_count(log: str) -> int:
    return sum(float(match.group(1)) > 10 for match in _OVERFULL_RE.finditer(log))


def _bound_file(
    root: Path,
    row: Mapping[str, Any],
    field: str | None,
) -> Path:
    value: Any = row.get(field) if field else row
    if not isinstance(value, dict):
        raise StageEntrypointError(
            "artifact_binding_invalid",
            f"artifact binding {field!r} is missing",
        )
    path = _required(root, value.get("path"))
    expected = value.get("sha256")
    if not isinstance(expected, str) or expected != sha256_file(path):
        raise StageEntrypointError(
            "artifact_binding_mismatch",
            f"artifact binding {field!r} differs from live bytes",
        )
    return path


def _required(root: Path, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw or raw.startswith("/") or "\\" in raw:
        raise StageEntrypointError("candidate_path_invalid", "candidate path is invalid")
    relative = PurePosixPath(raw)
    if str(relative) != raw or any(part in {"", ".", ".."} for part in relative.parts):
        raise StageEntrypointError("candidate_path_invalid", "candidate path is not normalized")
    current = root.resolve()
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise StageEntrypointError(
                "candidate_path_invalid",
                "candidate path cannot contain symlinks",
            )
    path = (root / raw).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        raise StageEntrypointError(
            "candidate_artifact_missing",
            f"required candidate artifact {raw!r} is missing",
        )
    return path


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StageEntrypointError(
            "candidate_json_invalid",
            f"{label} is not valid UTF-8 JSON",
        ) from exc
    if not isinstance(value, dict):
        raise StageEntrypointError(
            "candidate_json_invalid",
            f"{label} must be a JSON object",
        )
    return value


def _zip_member(info: zipfile.ZipInfo) -> str:
    name = info.filename[:-1] if info.filename.endswith("/") else info.filename
    if not name or name.startswith("/") or "\\" in name:
        raise StageEntrypointError("delivery_zip_unsafe_member", "delivery ZIP path is unsafe")
    path = PurePosixPath(name)
    if str(path) != name or any(part in {"", ".", ".."} for part in path.parts):
        raise StageEntrypointError("delivery_zip_unsafe_member", "delivery ZIP path is unsafe")
    unix_type = (info.external_attr >> 16) & 0o170000
    if unix_type == 0o120000:
        raise StageEntrypointError(
            "delivery_zip_unsafe_member",
            "delivery ZIP symlinks are forbidden",
        )
    return name


__all__ = ["STAGE_GATES", "evaluate_stage"]
