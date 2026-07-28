from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

try:
    from .stage_entrypoint import (
        StageEntrypointError,
        StageInputRoot,
        StageProduction,
        StageRequest,
        require_parameter_keys,
        run_release_python_kernel,
        sha256_file,
    )
except ImportError:  # Release-local scripts are imported outside the backend package.
    from stage_entrypoint import (  # type: ignore[no-redef]
        StageEntrypointError,
        StageInputRoot,
        StageProduction,
        StageRequest,
        require_parameter_keys,
        run_release_python_kernel,
        sha256_file,
    )


ORCHESTRATOR_SKILL = "skills/luceon-popo-to-refined-elegantbook"
KERNEL_ROOT = f"{ORCHESTRATOR_SKILL}/scripts"
ATOMIC_KERNEL = "scripts/worker-v3/spec01_03_atomic_kernel.py"
PREDECESSOR_STAGE = {
    "source_scope_and_order": "intake_snapshot",
    "canonical_block_ledger": "source_scope_and_order",
    "outline_reconstruction": "canonical_block_ledger",
    "semantic_annotation": "outline_reconstruction",
    "template_construct_binding": "semantic_annotation",
    "frozen_render_plan": "template_construct_binding",
}

P0_ADAPTER_GAPS: Mapping[str, Mapping[str, str]] = {}


@dataclass(frozen=True)
class _ReviewBinding:
    prompt_id: str
    prompt_version: str
    prompt_sha256: str
    schema_id: str
    schema_version: str
    schema_sha256: str
    input_canonical_sha256: str
    result_canonical_sha256: str
    audit_sha256: str


def produce_stage(
    request: StageRequest,
    inputs: StageInputRoot,
    candidate_root: Path,
    release_root: Path,
) -> StageProduction:
    if request.stage_key == "intake_snapshot":
        return _produce_intake(request, inputs, candidate_root, release_root)
    if request.stage_key == "source_scope_and_order":
        return _produce_scope_order(request, inputs, candidate_root, release_root)
    if request.stage_key == "canonical_block_ledger":
        return _produce_canonical_ledger(request, inputs, candidate_root, release_root)
    if request.stage_key == "outline_reconstruction":
        return _produce_outline(request, inputs, candidate_root, release_root)
    if request.stage_key == "semantic_annotation":
        return _produce_semantic(request, inputs, candidate_root, release_root)
    if request.stage_key == "template_construct_binding":
        return _produce_construct(request, inputs, candidate_root, release_root)
    if request.stage_key == "frozen_render_plan":
        return _produce_render_plan(request, inputs, candidate_root, release_root)
    raise StageEntrypointError(
        "stage_adapter_unknown",
        f"no Spec 01-04 adapter is registered for {request.stage_key!r}",
    )


def _produce_intake(
    request: StageRequest,
    inputs: StageInputRoot,
    output: Path,
    release: Path,
) -> StageProduction:
    parameters = require_parameter_keys(
        request,
        required=(
            "run_id",
            "decision_index_id",
            "decision_snapshot_id",
            "stage_decision_id",
        ),
    )
    _require_identifiers(
        parameters,
        ("run_id", "decision_index_id", "decision_snapshot_id", "stage_decision_id"),
    )
    _require_roles(
        request,
        {
            "frozen_source",
            "source_pdf",
            "mineru_manifest",
            "mineru_frozen_marker",
            "mineru_archive",
            "popo_frozen_marker",
            "popo_archive",
            "template_archive",
        },
    )
    if request.artifact("frozen_source").kind != "popo-manifest":
        raise StageEntrypointError(
            "intake_primary_kind_invalid",
            "intake primary input must be the exact frozen Popo manifest",
        )
    _verify_template_archive(request, inputs, release)
    schema_path, schema_sha = _release_schema_binding(
        release,
        schema_id="worker-v3.spec01-intake-contract",
        schema_version="1.0.0",
    )
    execution = run_release_python_kernel(
        release_root=release,
        kernel_relative=ATOMIC_KERNEL,
        args=(
            "intake",
            "--job-id",
            request.job_id,
            "--run-id",
            str(parameters["run_id"]),
            "--decision-index-id",
            str(parameters["decision_index_id"]),
            "--decision-snapshot-id",
            str(parameters["decision_snapshot_id"]),
            "--stage-decision-id",
            str(parameters["stage_decision_id"]),
            "--source-pdf",
            str(inputs.file("source_pdf")),
            "--mineru-manifest",
            str(inputs.file("mineru_manifest")),
            "--mineru-marker",
            str(inputs.file("mineru_frozen_marker")),
            "--mineru-archive",
            str(inputs.file("mineru_archive")),
            "--popo-manifest",
            str(inputs.file("frozen_source")),
            "--popo-marker",
            str(inputs.file("popo_frozen_marker")),
            "--popo-archive",
            str(inputs.file("popo_archive")),
            "--template-archive",
            str(inputs.file("template_archive")),
            "--release-manifest",
            str(release / "release-manifest.json"),
            "--contract-schema-path",
            schema_path,
            "--contract-schema-sha256",
            schema_sha,
            "--output-dir",
            str(output),
        ),
        cwd=request.workdir,
        timeout_seconds=86_400,
    )
    return _completed_atomic_production(
        output,
        stage="intake_snapshot",
        artifact_kind="worker-v3-intake-snapshot-candidate",
        execution=execution.returncode,
        roles={
            "manifests/intake_snapshot_candidate_stage_manifest.json": "stage_manifest",
            "contracts/input_contract.json": "input_contract",
            "contracts/source_trace.json": "source_trace",
            "contracts/materialized_manifest.json": "materialized_manifest",
            "contracts/template_intake.json": "template_intake",
            "reports/input_validation_report.json": "validation_report",
            "decisions/canonical_decision_index.json": "decision_index",
            "source/popo_source_units.jsonl": "normalized_source_units",
            "source/mineru_media_atoms.jsonl": "normalized_media_atoms",
        },
    )


def _produce_scope_order(
    request: StageRequest,
    inputs: StageInputRoot,
    output: Path,
    release: Path,
) -> StageProduction:
    parameters = require_parameter_keys(
        request,
        required=(
            "run_id",
            "decision_snapshot_id",
            "stage_decision_id",
            "review_binding",
        ),
    )
    _require_identifiers(
        parameters,
        ("run_id", "decision_snapshot_id", "stage_decision_id"),
    )
    _require_roles(
        request,
        {
            "promoted_predecessor",
            "predecessor_promotion_manifest",
            "source_pdf",
            "mineru_archive",
            "popo_archive",
            "template_archive",
            "scope_order_review_bundle",
            "llm_call_audit",
        },
    )
    _require_predecessor(request, "intake_snapshot")
    parent = inputs.extracted("promoted_predecessor")
    _verify_compact_external_bindings(request, parent)
    task_path = _prepare_review_task(
        request,
        release,
        parent=parent,
        command="prepare-scope-review-task",
        filename="spec02-scope-order-review-task.json",
    )
    task_hash = _canonical_hash(_read_json(task_path, "scope/order review task"))
    _verify_bounded_review(
        request,
        inputs,
        release,
        review_role="scope_order_review_bundle",
        expected_prompt_id="worker-v3.spec02-scope-order-review",
        expected_input_canonical_sha256=task_hash,
    )
    binding = _review_binding(parameters["review_binding"])
    schema_path, schema_sha = _release_schema_binding(
        release,
        schema_id="worker-v3.spec02-scope-order-review",
        schema_version="3.0.0",
    )
    if binding.schema_sha256 != schema_sha:
        raise StageEntrypointError(
            "review_schema_release_binding_missing",
            "scope/order review schema differs from the release",
        )
    execution = run_release_python_kernel(
        release_root=release,
        kernel_relative=ATOMIC_KERNEL,
        args=(
            "scope",
            "--job-id",
            request.job_id,
            "--run-id",
            str(parameters["run_id"]),
            "--decision-snapshot-id",
            str(parameters["decision_snapshot_id"]),
            "--stage-decision-id",
            str(parameters["stage_decision_id"]),
            "--parent",
            str(parent),
            "--parent-promotion",
            str(inputs.file("predecessor_promotion_manifest")),
            "--source-pdf",
            str(inputs.file("source_pdf")),
            "--mineru-archive",
            str(inputs.file("mineru_archive")),
            "--popo-archive",
            str(inputs.file("popo_archive")),
            "--template-archive",
            str(inputs.file("template_archive")),
            "--review-task",
            str(task_path),
            "--review",
            str(inputs.file("scope_order_review_bundle")),
            "--contract-schema-path",
            schema_path,
            "--contract-schema-sha256",
            schema_sha,
            "--output-dir",
            str(output),
        ),
        cwd=request.workdir,
        timeout_seconds=86_400,
    )
    return _completed_atomic_production(
        output,
        stage="source_scope_and_order",
        artifact_kind="worker-v3-source-scope-order-candidate",
        execution=execution.returncode,
        roles={
            "manifests/source_scope_and_order_candidate_stage_manifest.json": "stage_manifest",
            "ledgers/source_scope_ledger.json": "source_scope_ledger",
            "ledgers/reading_order_ledger.json": "reading_order_ledger",
            "ledgers/source_page_render_ledger.jsonl": "source_page_render_ledger",
            "contracts/composite_reading_relationships.json": "reading_relationships",
            "decisions/canonical_decision_index.json": "decision_index",
            "reviews/spec02_scope_order_review_task.json": "review_task",
            "reviews/spec02_scope_order_review.json": "review_result",
        },
    )


def _produce_canonical_ledger(
    request: StageRequest,
    inputs: StageInputRoot,
    output: Path,
    release: Path,
) -> StageProduction:
    parameters = require_parameter_keys(
        request,
        required=(
            "run_id",
            "decision_snapshot_id",
            "stage_decision_id",
            "ledger_id",
            "ledger_snapshot_id",
            "ledger_version",
            "review_binding",
        ),
    )
    _require_identifiers(
        parameters,
        (
            "run_id",
            "decision_snapshot_id",
            "stage_decision_id",
            "ledger_id",
            "ledger_snapshot_id",
        ),
    )
    if (
        not isinstance(parameters["ledger_version"], int)
        or isinstance(parameters["ledger_version"], bool)
        or parameters["ledger_version"] < 1
    ):
        raise StageEntrypointError(
            "stage_parameters_invalid",
            "ledger_version must be a positive integer",
        )
    _require_roles(
        request,
        {
            "promoted_predecessor",
            "predecessor_promotion_manifest",
            "source_pdf",
            "mineru_archive",
            "popo_archive",
            "template_archive",
            "media_review_bundle",
            "llm_call_audit",
        },
    )
    _require_predecessor(request, "source_scope_and_order")
    parent = inputs.extracted("promoted_predecessor")
    _verify_compact_external_bindings(request, parent)
    task_path = _prepare_review_task(
        request,
        release,
        parent=parent,
        command="prepare-media-review-task",
        filename="spec03-media-review-task.json",
    )
    task_hash = _canonical_hash(_read_json(task_path, "media review task"))
    _verify_bounded_review(
        request,
        inputs,
        release,
        review_role="media_review_bundle",
        expected_prompt_id="worker-v3.spec03-media-review",
        expected_input_canonical_sha256=task_hash,
    )
    binding = _review_binding(parameters["review_binding"])
    schema_path, schema_sha = _release_schema_binding(
        release,
        schema_id="worker-v3.spec03-media-review",
        schema_version="1.0.0",
    )
    if binding.schema_sha256 != schema_sha:
        raise StageEntrypointError(
            "review_schema_release_binding_missing",
            "media review schema differs from the release",
        )
    execution = run_release_python_kernel(
        release_root=release,
        kernel_relative=ATOMIC_KERNEL,
        args=(
            "ledger",
            "--job-id",
            request.job_id,
            "--run-id",
            str(parameters["run_id"]),
            "--decision-snapshot-id",
            str(parameters["decision_snapshot_id"]),
            "--stage-decision-id",
            str(parameters["stage_decision_id"]),
            "--ledger-id",
            str(parameters["ledger_id"]),
            "--ledger-snapshot-id",
            str(parameters["ledger_snapshot_id"]),
            "--ledger-version",
            str(parameters["ledger_version"]),
            "--parent",
            str(parent),
            "--parent-promotion",
            str(inputs.file("predecessor_promotion_manifest")),
            "--source-pdf",
            str(inputs.file("source_pdf")),
            "--mineru-archive",
            str(inputs.file("mineru_archive")),
            "--popo-archive",
            str(inputs.file("popo_archive")),
            "--template-archive",
            str(inputs.file("template_archive")),
            "--review-task",
            str(task_path),
            "--review",
            str(inputs.file("media_review_bundle")),
            "--contract-schema-path",
            schema_path,
            "--contract-schema-sha256",
            schema_sha,
            "--output-dir",
            str(output),
        ),
        cwd=request.workdir,
        timeout_seconds=86_400,
    )
    return _completed_atomic_production(
        output,
        stage="canonical_block_ledger",
        artifact_kind="worker-v3-source-reconciled-ledger-candidate",
        execution=execution.returncode,
        roles={
            "manifests/canonical_block_ledger_candidate_stage_manifest.json": "stage_manifest",
            "manifests/source_reconciled_commit.json": "source_reconciled_commit",
            "ledgers/canonical_block_ledger.jsonl": "canonical_ledger",
            "ledgers/ledger_manifest.json": "ledger_manifest",
            "ledgers/block_coverage_ledger.json": "block_coverage_ledger",
            "ledgers/media_ledger.json": "media_ledger",
            "media/media_evidence_ledger.json": "media_evidence_ledger",
            "media/media_representation_plan.json": "media_representation_plan",
            "decisions/canonical_decision_index.json": "decision_index",
            "reports/source_completeness_report.json": "completeness_report",
            "reviews/spec03_media_review_task.json": "review_task",
            "reviews/spec03_media_review.json": "review_result",
        },
    )


def _produce_outline(
    request: StageRequest,
    inputs: StageInputRoot,
    output: Path,
    release: Path,
) -> StageProduction:
    parameters = _stage_parameters(request, lineage_keys=("parent_lineage_key",))
    _require_roles(
        request,
        {
            "promoted_predecessor",
            "predecessor_promotion_manifest",
            "promotion_registry",
            "source_pdf",
            "outline_review_bundle",
            "llm_call_audit",
        },
    )
    _require_predecessor(request, "canonical_block_ledger")
    parent = inputs.extracted("promoted_predecessor")
    _verify_bounded_review(
        request,
        inputs,
        release,
        review_role="outline_review_bundle",
        expected_prompt_id="worker-v3.spec04a-outline-review",
    )
    execution = run_release_python_kernel(
        release_root=release,
        kernel_relative=f"{KERNEL_ROOT}/spec04a_structure_contract.py",
        args=(
            "produce",
            "--parent-ledger",
            str(_required_file(parent, "ledgers/canonical_block_ledger.jsonl")),
            "--parent-decision-index",
            str(_required_file(parent, "decisions/canonical_decision_index.json")),
            "--source-pdf",
            str(inputs.file("source_pdf")),
            "--promotion-registry",
            str(inputs.file("promotion_registry")),
            "--parent-promotion",
            str(inputs.file("predecessor_promotion_manifest")),
            "--parent-lineage-key",
            str(parameters["parent_lineage_key"]),
            "--review-bundle",
            str(inputs.file("outline_review_bundle")),
            *_identity_args(parameters),
            "--output-dir",
            str(output),
        ),
        cwd=request.workdir,
        timeout_seconds=86_400,
    )
    return _completed_production(
        output,
        stage_manifest="manifests/spec04a_structure_stage_manifest.json",
        stage_schema="spec04a-structure-stage-manifest/1.0",
        artifact_kind="worker-v3-outline-candidate",
        execution=execution.returncode,
        roles={
            "manifests/spec04a_structure_stage_manifest.json": "stage_manifest",
            "structure/source_outline_ledger.json": "source_outline_ledger",
            "structure/final_toc_plan.json": "final_toc_plan",
            "ledgers/canonical_block_ledger.jsonl": "canonical_ledger",
            "decisions/canonical_decision_index.json": "decision_index",
        },
    )


def _produce_semantic(
    request: StageRequest,
    inputs: StageInputRoot,
    output: Path,
    release: Path,
) -> StageProduction:
    parameters = _stage_parameters(request, lineage_keys=("parent_lineage_key",))
    _require_roles(
        request,
        {
            "promoted_predecessor",
            "predecessor_promotion_manifest",
            "promotion_registry",
            "source_pdf",
            "semantic_review_bundle",
            "llm_call_audit",
        },
    )
    _require_predecessor(request, "outline_reconstruction")
    parent = inputs.extracted("promoted_predecessor")
    _verify_bounded_review(
        request,
        inputs,
        release,
        review_role="semantic_review_bundle",
        expected_prompt_id="worker-v3.spec04b-semantic-review",
    )
    execution = run_release_python_kernel(
        release_root=release,
        kernel_relative=f"{KERNEL_ROOT}/spec04b_semantic_span_contract.py",
        args=(
            "produce",
            "--parent-ledger",
            str(_required_file(parent, "ledgers/canonical_block_ledger.jsonl")),
            "--parent-decision-index",
            str(_required_file(parent, "decisions/canonical_decision_index.json")),
            "--source-pdf",
            str(inputs.file("source_pdf")),
            "--promotion-registry",
            str(inputs.file("promotion_registry")),
            "--parent-promotion",
            str(inputs.file("predecessor_promotion_manifest")),
            "--parent-lineage-key",
            str(parameters["parent_lineage_key"]),
            "--review-bundle",
            str(inputs.file("semantic_review_bundle")),
            *_identity_args(parameters),
            "--output-dir",
            str(output),
        ),
        cwd=request.workdir,
        timeout_seconds=86_400,
    )
    return _completed_production(
        output,
        stage_manifest="manifests/spec04b_semantic_stage_manifest.json",
        stage_schema="spec04b-semantic-stage-manifest/1.0",
        artifact_kind="worker-v3-semantic-annotation-candidate",
        execution=execution.returncode,
        roles={
            "manifests/spec04b_semantic_stage_manifest.json": "stage_manifest",
            "semantic/semantic_span_ledger.json": "semantic_span_ledger",
            "semantic/teaching_column_group_ledger.json": "teaching_group_ledger",
            "ledgers/canonical_block_ledger.jsonl": "canonical_ledger",
            "decisions/canonical_decision_index.json": "decision_index",
        },
    )


def _produce_construct(
    request: StageRequest,
    inputs: StageInputRoot,
    output: Path,
    release: Path,
) -> StageProduction:
    parameters = _stage_parameters(request, lineage_keys=("parent_lineage_key",))
    _require_roles(
        request,
        {
            "promoted_predecessor",
            "predecessor_promotion_manifest",
            "promotion_registry",
            "source_pdf",
            "template_intake",
            "template_archive",
            "construct_review_bundle",
            "llm_call_audit",
        },
    )
    _require_predecessor(request, "semantic_annotation")
    parent = inputs.extracted("promoted_predecessor")
    _verify_bounded_review(
        request,
        inputs,
        release,
        review_role="construct_review_bundle",
        expected_prompt_id="worker-v3.spec04c-construct-review",
    )
    _verify_template_archive(request, inputs, release)
    execution = run_release_python_kernel(
        release_root=release,
        kernel_relative=f"{KERNEL_ROOT}/spec04c_construct_binding_contract.py",
        args=(
            "produce",
            "--parent-ledger",
            str(_required_file(parent, "ledgers/canonical_block_ledger.jsonl")),
            "--parent-decision-index",
            str(_required_file(parent, "decisions/canonical_decision_index.json")),
            "--parent-semantic-span-ledger",
            str(_required_file(parent, "semantic/semantic_span_ledger.json")),
            "--parent-teaching-group-ledger",
            str(_required_file(parent, "semantic/teaching_column_group_ledger.json")),
            "--source-pdf",
            str(inputs.file("source_pdf")),
            "--template-intake",
            str(inputs.file("template_intake")),
            "--template-zip",
            str(inputs.file("template_archive")),
            "--promotion-registry",
            str(inputs.file("promotion_registry")),
            "--parent-promotion",
            str(inputs.file("predecessor_promotion_manifest")),
            "--parent-lineage-key",
            str(parameters["parent_lineage_key"]),
            "--review-bundle",
            str(inputs.file("construct_review_bundle")),
            *_identity_args(parameters),
            "--output-dir",
            str(output),
        ),
        cwd=request.workdir,
        timeout_seconds=86_400,
    )
    return _completed_production(
        output,
        stage_manifest="manifests/spec04c_construct_stage_manifest.json",
        stage_schema="spec04c-construct-stage-manifest/1.0",
        artifact_kind="worker-v3-template-binding-candidate",
        execution=execution.returncode,
        roles={
            "manifests/spec04c_construct_stage_manifest.json": "stage_manifest",
            "template/template_capability_manifest.json": "template_capability_manifest",
            "semantic/construct_binding_ledger.json": "construct_binding_ledger",
            "ledgers/canonical_block_ledger.jsonl": "canonical_ledger",
            "decisions/canonical_decision_index.json": "decision_index",
        },
    )


def _produce_render_plan(
    request: StageRequest,
    inputs: StageInputRoot,
    output: Path,
    release: Path,
) -> StageProduction:
    parameters = _stage_parameters(
        request,
        lineage_keys=("parent_04c_lineage", "structure_lineage", "media_lineage"),
    )
    _require_roles(
        request,
        {
            "promoted_predecessor",
            "predecessor_promotion_manifest",
            "promotion_registry",
            "source_pdf",
            "render_policy",
            "llm_call_audit",
            "structure_candidate",
            "structure_promotion_manifest",
            "media_candidate",
            "media_promotion_manifest",
        },
    )
    _require_predecessor(request, "template_construct_binding")
    parent = inputs.extracted("promoted_predecessor")
    structure = inputs.extracted("structure_candidate")
    media = inputs.extracted("media_candidate")
    _verify_bounded_review(
        request,
        inputs,
        release,
        review_role="render_policy",
        expected_prompt_id="worker-v3.spec04d-render-policy",
    )
    execution = run_release_python_kernel(
        release_root=release,
        kernel_relative=f"{KERNEL_ROOT}/spec04d_render_plan_contract.py",
        args=(
            "produce",
            "--parent-ledger",
            str(_required_file(parent, "ledgers/canonical_block_ledger.jsonl")),
            "--parent-decision-index",
            str(_required_file(parent, "decisions/canonical_decision_index.json")),
            "--construct-binding-ledger",
            str(_required_file(parent, "semantic/construct_binding_ledger.json")),
            "--template-capability-manifest",
            str(_required_file(parent, "template/template_capability_manifest.json")),
            "--source-outline-ledger",
            str(_required_file(structure, "structure/source_outline_ledger.json")),
            "--final-toc-plan",
            str(_required_file(structure, "structure/final_toc_plan.json")),
            "--media-evidence-ledger",
            str(_required_file(media, "media/media_evidence_ledger.json")),
            "--media-representation-plan",
            str(_required_file(media, "media/media_representation_plan.json")),
            "--source-pdf",
            str(inputs.file("source_pdf")),
            "--promotion-registry",
            str(inputs.file("promotion_registry")),
            "--parent-04c-promotion",
            str(inputs.file("predecessor_promotion_manifest")),
            "--parent-04c-lineage",
            str(parameters["parent_04c_lineage"]),
            "--structure-promotion",
            str(inputs.file("structure_promotion_manifest")),
            "--structure-lineage",
            str(parameters["structure_lineage"]),
            "--media-promotion",
            str(inputs.file("media_promotion_manifest")),
            "--media-lineage",
            str(parameters["media_lineage"]),
            "--render-policy",
            str(inputs.file("render_policy")),
            *_identity_args(parameters),
            "--output-dir",
            str(output),
        ),
        cwd=request.workdir,
        timeout_seconds=86_400,
    )
    return _completed_production(
        output,
        stage_manifest="manifests/spec04d_render_plan_stage_manifest.json",
        stage_schema="spec04d-render-plan-stage-manifest/1.0",
        artifact_kind="worker-v3-frozen-render-plan-candidate",
        execution=execution.returncode,
        roles={
            "manifests/spec04d_render_plan_stage_manifest.json": "stage_manifest",
            "render/render_plan.json": "frozen_render_plan",
            "render/volume_partition_plan.json": "volume_partition_plan",
            "semantic/semantic_mapping_ledger.json": "semantic_mapping_ledger",
            "ledgers/canonical_block_ledger.jsonl": "canonical_ledger",
            "decisions/canonical_decision_index.json": "decision_index",
        },
    )


def _stage_parameters(
    request: StageRequest,
    *,
    lineage_keys: Sequence[str],
) -> Mapping[str, Any]:
    required = (
        *lineage_keys,
        "ledger_snapshot_id",
        "ledger_version",
        "decision_snapshot_id",
        "stage_decision_id",
        "run_id",
        "review_binding",
    )
    values = require_parameter_keys(request, required=required)
    for name in (*lineage_keys, "ledger_snapshot_id", "decision_snapshot_id", "stage_decision_id", "run_id"):
        value = values[name]
        if not isinstance(value, str) or not value.strip():
            raise StageEntrypointError(
                "stage_parameters_invalid",
                f"parameter {name!r} must be a non-empty identifier",
            )
    if (
        not isinstance(values["ledger_version"], int)
        or isinstance(values["ledger_version"], bool)
        or values["ledger_version"] < 1
    ):
        raise StageEntrypointError(
            "stage_parameters_invalid",
            "ledger_version must be a positive integer",
        )
    _review_binding(values["review_binding"])
    return values


def _identity_args(parameters: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        "--ledger-snapshot-id",
        str(parameters["ledger_snapshot_id"]),
        "--ledger-version",
        str(parameters["ledger_version"]),
        "--decision-snapshot-id",
        str(parameters["decision_snapshot_id"]),
        "--stage-decision-id",
        str(parameters["stage_decision_id"]),
        "--run-id",
        str(parameters["run_id"]),
    )


def _require_identifiers(
    parameters: Mapping[str, Any],
    names: Sequence[str],
) -> None:
    for name in names:
        if not isinstance(parameters.get(name), str) or not str(parameters[name]).strip():
            raise StageEntrypointError(
                "stage_parameters_invalid",
                f"parameter {name!r} must be a non-empty identifier",
            )


def _prepare_review_task(
    request: StageRequest,
    release: Path,
    *,
    parent: Path,
    command: str,
    filename: str,
) -> Path:
    preparation = request.workdir / "prepared-review"
    if preparation.exists() or preparation.is_symlink():
        raise StageEntrypointError(
            "review_preparation_exists",
            "review preparation directory must not already exist",
            exit_code=3,
        )
    task_path = preparation / filename
    execution = run_release_python_kernel(
        release_root=release,
        kernel_relative=ATOMIC_KERNEL,
        args=(
            command,
            "--parent",
            str(parent),
            "--output",
            str(task_path),
        ),
        cwd=request.workdir,
        timeout_seconds=86_400,
    )
    if execution.returncode != 0 or not task_path.is_file() or task_path.is_symlink():
        raise StageEntrypointError(
            "review_task_preparation_failed",
            "deterministic review task was not produced",
            exit_code=3,
        )
    return task_path


def _release_schema_binding(
    release_root: Path,
    *,
    schema_id: str,
    schema_version: str,
) -> tuple[str, str]:
    manifest = _read_json(release_root / "release-manifest.json", "release manifest")
    matches = [
        row
        for row in manifest.get("schemas", [])
        if isinstance(row, dict)
        and row.get("id") == schema_id
        and row.get("version") == schema_version
    ]
    if len(matches) != 1:
        raise StageEntrypointError(
            "stage_schema_release_binding_missing",
            f"release must bind exactly one {schema_id!r} schema",
            exit_code=3,
        )
    path = matches[0].get("path")
    sha = matches[0].get("sha256")
    if not isinstance(path, str) or not isinstance(sha, str):
        raise StageEntrypointError(
            "stage_schema_release_binding_missing",
            f"release schema {schema_id!r} has incomplete identity",
            exit_code=3,
        )
    schema_path = _release_bound_file(
        release_root,
        path,
        sha,
        f"stage schema {schema_id}",
    )
    schema_canonical_sha = _canonical_hash(
        _read_json(schema_path, f"stage schema {schema_id}")
    )
    return path, schema_canonical_sha


def _require_roles(request: StageRequest, expected: set[str]) -> None:
    actual = {item.role for item in request.input_artifacts}
    if actual != expected:
        raise StageEntrypointError(
            "stage_input_roles_invalid",
            "stage input roles are missing or unknown",
            findings=(
                {
                    "code": "stage_input_roles_invalid",
                    "missing": sorted(expected - actual),
                    "unknown": sorted(actual - expected),
                },
            ),
        )
    bundles = {
        item.role
        for item in request.input_artifacts
        if item.kind == "worker-v3-candidate-bundle"
    }
    expected_bundles = expected & {
        "promoted_predecessor",
        "structure_candidate",
        "media_candidate",
    }
    if bundles != expected_bundles:
        raise StageEntrypointError(
            "stage_bundle_roles_invalid",
            "candidate bundle roles do not match the stage contract",
        )


def _require_predecessor(request: StageRequest, expected_stage: str) -> None:
    predecessor = request.predecessor_promotion
    if predecessor is None or predecessor.stage_key != expected_stage:
        raise StageEntrypointError(
            "predecessor_stage_mismatch",
            f"{request.stage_key} must consume a promoted {expected_stage} candidate",
        )


def _verify_bounded_review(
    request: StageRequest,
    inputs: StageInputRoot,
    release_root: Path,
    *,
    review_role: str,
    expected_prompt_id: str,
    expected_input_canonical_sha256: str | None = None,
) -> None:
    binding = _review_binding(request.parameters["review_binding"])
    if binding.prompt_id != expected_prompt_id:
        raise StageEntrypointError(
            "review_prompt_mismatch",
            f"stage requires release prompt {expected_prompt_id!r}",
        )
    if (
        expected_input_canonical_sha256 is not None
        and binding.input_canonical_sha256 != expected_input_canonical_sha256
    ):
        raise StageEntrypointError(
            "bounded_review_input_mismatch",
            "bounded review input differs from the deterministic review task",
        )
    review_path = inputs.file(review_role)
    audit_path = inputs.file("llm_call_audit")
    if sha256_file(audit_path) != binding.audit_sha256:
        raise StageEntrypointError(
            "llm_audit_hash_mismatch",
            "bounded LLM audit does not match its request binding",
        )
    review = _read_json(review_path, review_role)
    audit = _read_json(audit_path, "llm_call_audit")
    result_hash = _canonical_hash(review)
    if result_hash != binding.result_canonical_sha256:
        raise StageEntrypointError(
            "bounded_review_result_mismatch",
            "review bundle canonical hash does not match the bounded LLM binding",
        )
    manifest = _read_json(release_root / "release-manifest.json", "release manifest")
    prompts = [
        row
        for row in manifest.get("prompts", [])
        if isinstance(row, dict)
        and row.get("id") == binding.prompt_id
        and row.get("version") == binding.prompt_version
    ]
    if len(prompts) != 1 or prompts[0].get("sha256") != binding.prompt_sha256:
        raise StageEntrypointError(
            "review_prompt_release_binding_missing",
            "bounded review prompt is not uniquely hash-bound by the release",
        )
    _release_bound_file(
        release_root,
        prompts[0].get("path"),
        binding.prompt_sha256,
        "bounded review prompt",
    )
    output_schema = prompts[0].get("output_schema")
    schemas = [
        row
        for row in manifest.get("schemas", [])
        if isinstance(row, dict)
        and row.get("id") == binding.schema_id
        and row.get("version") == binding.schema_version
        and row.get("path") == output_schema
    ]
    if len(schemas) != 1:
        raise StageEntrypointError(
            "review_schema_release_binding_missing",
            "bounded review output schema is not uniquely hash-bound by the release",
        )
    schema_file_sha = schemas[0].get("sha256")
    if not isinstance(schema_file_sha, str):
        raise StageEntrypointError(
            "review_schema_release_binding_missing",
            "bounded review output schema has no release file hash",
        )
    schema_path = _release_bound_file(
        release_root,
        schemas[0].get("path"),
        schema_file_sha,
        "bounded review schema",
    )
    schema_canonical_sha = _canonical_hash(
        _read_json(schema_path, "bounded review schema")
    )
    if schema_canonical_sha != binding.schema_sha256:
        raise StageEntrypointError(
            "review_schema_release_binding_missing",
            "bounded review output schema canonical hash differs from the release",
        )
    model_policy = manifest.get("model_policy")
    if (
        not isinstance(model_policy, dict)
        or model_policy.get("mode") != "release-scoped-schema-bounded-json"
        or not isinstance(model_policy.get("provider"), str)
        or not model_policy["provider"]
        or not isinstance(model_policy.get("model"), str)
        or not model_policy["model"]
        or not isinstance(model_policy.get("request_parameters"), dict)
        or model_policy["request_parameters"].get("temperature") != 0
    ):
        raise StageEntrypointError(
            "llm_model_policy_unqualified",
            "release has no qualified deterministic bounded-JSON model policy",
        )
    expected_audit = {
        "status": "succeeded",
        "stage_key": request.stage_key,
        "release_id": request.release.release_id,
        "release_sha256": request.release.manifest_sha256,
        "prompt_id": binding.prompt_id,
        "prompt_version": binding.prompt_version,
        "prompt_sha256": binding.prompt_sha256,
        "schema_id": binding.schema_id,
        "schema_version": binding.schema_version,
        "schema_sha256": binding.schema_sha256,
        "input_sha256": binding.input_canonical_sha256,
        "parsed_result_sha256": binding.result_canonical_sha256,
        "provider": model_policy["provider"],
        "model": model_policy["model"],
        "actual_provider": model_policy["provider"],
        "actual_model": model_policy["model"],
        "request_parameters_sha256": _canonical_hash(
            model_policy["request_parameters"]
        ),
    }
    if any(audit.get(name) != value for name, value in expected_audit.items()):
        raise StageEntrypointError(
            "llm_audit_binding_mismatch",
            "bounded LLM audit does not match stage, release, prompt, schema, or result",
        )
    raw_response = audit.get("raw_response")
    if (
        not isinstance(raw_response, dict)
        or audit.get("raw_response_sha256") != _canonical_hash(raw_response)
    ):
        raise StageEntrypointError(
            "llm_raw_response_binding_mismatch",
            "bounded LLM raw response is missing or cannot reproduce its hash",
        )
    usage = audit.get("usage")
    if (
        not isinstance(usage, dict)
        or not isinstance(usage.get("input_tokens"), int)
        or not isinstance(usage.get("output_tokens"), int)
    ):
        raise StageEntrypointError(
            "llm_usage_missing",
            "bounded LLM audit has no attributable usage",
        )


def _review_binding(raw: Any) -> _ReviewBinding:
    fields = {
        "prompt_id",
        "prompt_version",
        "prompt_sha256",
        "schema_id",
        "schema_version",
        "schema_sha256",
        "input_canonical_sha256",
        "result_canonical_sha256",
        "audit_sha256",
    }
    if not isinstance(raw, dict) or set(raw) != fields:
        raise StageEntrypointError(
            "review_binding_invalid",
            "review_binding has missing or unknown fields",
        )
    for name in fields:
        if not isinstance(raw[name], str) or not raw[name]:
            raise StageEntrypointError(
                "review_binding_invalid",
                f"review_binding.{name} must be a non-empty string",
            )
    for name in (
        "prompt_sha256",
        "schema_sha256",
        "input_canonical_sha256",
        "result_canonical_sha256",
        "audit_sha256",
    ):
        if len(raw[name]) != 64 or any(character not in "0123456789abcdef" for character in raw[name]):
            raise StageEntrypointError(
                "review_binding_invalid",
                f"review_binding.{name} must be a lowercase SHA-256",
            )
    return _ReviewBinding(**raw)


def _verify_template_archive(
    request: StageRequest,
    inputs: StageInputRoot,
    release_root: Path,
) -> None:
    manifest = _read_json(release_root / "release-manifest.json", "release manifest")
    expected = (manifest.get("template") or {}).get("archive_sha256")
    actual = request.artifact("template_archive").sha256
    if not isinstance(expected, str) or expected != actual:
        raise StageEntrypointError(
            "template_release_binding_mismatch",
            "template archive does not match the release-approved template",
        )


def _verify_compact_external_bindings(
    request: StageRequest,
    predecessor: Path,
) -> None:
    contract = _read_json(
        _required_file(predecessor, "contracts/input_contract.json"),
        "Spec 01 compact input contract",
    )
    inputs = contract.get("inputs")
    if not isinstance(inputs, dict):
        raise StageEntrypointError(
            "input_reference_contract_invalid",
            "Spec 01 compact input contract has no immutable input identities",
        )
    for role in (
        "source_pdf",
        "mineru_archive",
        "popo_archive",
        "template_archive",
    ):
        expected = inputs.get(role)
        artifact = request.artifact(role)
        if (
            not isinstance(expected, dict)
            or expected.get("sha256") != artifact.sha256
            or expected.get("size_bytes") != artifact.size_bytes
        ):
            raise StageEntrypointError(
                "input_reference_drift",
                f"{role} differs from the promoted Spec 01 identity",
            )


def _release_bound_file(
    release_root: Path,
    raw_relative: Any,
    expected_sha256: str,
    label: str,
) -> Path:
    if (
        not isinstance(raw_relative, str)
        or not raw_relative
        or raw_relative.startswith("/")
        or "\\" in raw_relative
    ):
        raise StageEntrypointError(
            "release_resource_path_invalid",
            f"{label} path is not release-relative",
        )
    relative = PurePosixPath(raw_relative)
    if str(relative) != raw_relative or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise StageEntrypointError(
            "release_resource_path_invalid",
            f"{label} path is not normalized",
        )
    path = _contained_regular_file(
        release_root,
        raw_relative,
        code="release_resource_missing",
        label=label,
    )
    if sha256_file(path) != expected_sha256:
        raise StageEntrypointError(
            "release_resource_hash_mismatch",
            f"{label} bytes drifted from the release manifest",
        )
    return path


def _contained_regular_file(
    root: Path,
    relative: str,
    *,
    code: str,
    label: str,
) -> Path:
    root = root.resolve()
    raw = root / relative
    current = root
    for part in PurePosixPath(relative).parts:
        current /= part
        if current.is_symlink():
            raise StageEntrypointError(code, f"{label} is linked or unavailable")
    path = raw.resolve()
    if root not in path.parents or not path.is_file():
        raise StageEntrypointError(
            code,
            f"{label} is linked or unavailable",
        )
    return path


def _required_file(root: Path, relative: str) -> Path:
    return _contained_regular_file(
        root,
        relative,
        code="predecessor_artifact_missing",
        label=f"required artifact {relative!r}",
    )


def _completed_atomic_production(
    output: Path,
    *,
    stage: str,
    artifact_kind: str,
    execution: int,
    roles: Mapping[str, str],
) -> StageProduction:
    manifest_path = _required_file(
        output,
        f"manifests/{stage}_candidate_stage_manifest.json",
    )
    manifest = _read_json(manifest_path, "atomic candidate stage manifest")
    if (
        manifest.get("schema_version")
        != "luceon.worker-v3-atomic-stage-manifest/v1"
        or manifest.get("stage") != stage
        or manifest.get("candidate_status") != "complete"
        or manifest.get("spec_status") != "not_evaluated"
        or manifest.get("promotion_status") != "not_evaluated"
    ):
        raise StageEntrypointError(
            "atomic_stage_candidate_invalid",
            "atomic producer did not create a complete unevaluated candidate manifest",
            exit_code=3,
        )
    gates = manifest.get("producer_gate_status")
    if not isinstance(gates, dict) or not gates or set(gates.values()) != {"passed"}:
        raise StageEntrypointError(
            "atomic_stage_gate_evidence_incomplete",
            "atomic producer did not close every deterministic hard-gate check",
            exit_code=3,
        )
    run_manifest = _read_json(
        _required_file(output, "manifests/run_manifest.json"),
        "atomic run manifest",
    )
    if (
        run_manifest.get("schema_version")
        != "luceon.worker-v3-atomic-run-manifest/v1"
        or run_manifest.get("stage") != stage
        or run_manifest.get("candidate_status") != "complete"
        or run_manifest.get("spec_status") != "not_evaluated"
        or run_manifest.get("promotion_status") != "not_evaluated"
    ):
        raise StageEntrypointError(
            "atomic_run_manifest_invalid",
            "atomic run manifest attempted to claim evaluation or promotion",
            exit_code=3,
        )
    for relative in roles:
        _required_file(output, relative)
    _verify_atomic_storage_policy(output, stage=stage)
    host_refs = _forbidden_host_references(output)
    if host_refs:
        raise StageEntrypointError(
            "candidate_host_path_reference",
            "candidate contains mutable host path references",
            findings=tuple(
                {"code": "candidate_host_path_reference", "path": path}
                for path in host_refs[:20]
            ),
            exit_code=3,
        )
    return StageProduction(
        artifact_kind=artifact_kind,
        metrics={
            "atomic_kernel_returncode": execution,
            "producer_gate_count": len(gates),
            "spec_status": "not_evaluated",
            "promotion_status": "not_evaluated",
        },
        findings=(
            {
                "code": "producer_candidate_requires_independent_evaluation",
                "severity": "info",
                "detail": (
                    "Deterministic producer gates are closed, but the candidate "
                    "has not been independently evaluated or promoted."
                ),
            },
        ),
        artifact_roles=roles,
    )


def _verify_atomic_storage_policy(output: Path, *, stage: str) -> None:
    forbidden_archives = {".pdf", ".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz"}
    raster_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
    rasters: list[str] = []
    for path in sorted(output.rglob("*")):
        if path.is_symlink():
            raise StageEntrypointError(
                "candidate_storage_policy_violation",
                "atomic candidate contains a symlink",
                exit_code=3,
            )
        if not path.is_file():
            continue
        relative = path.relative_to(output).as_posix()
        suffix = path.suffix.lower()
        if suffix in forbidden_archives:
            raise StageEntrypointError(
                "candidate_storage_policy_violation",
                f"atomic candidate repeats frozen binary {relative!r}",
                exit_code=3,
            )
        if suffix in raster_suffixes:
            rasters.append(relative)
    if stage == "intake_snapshot" and rasters:
        raise StageEntrypointError(
            "candidate_storage_policy_violation",
            "Spec 01 candidate must not materialize source rasters",
            exit_code=3,
        )
    if stage == "source_scope_and_order":
        if len(rasters) > 12 or any(
            not relative.startswith("evidence/risk-page-thumbnails/")
            for relative in rasters
        ):
            raise StageEntrypointError(
                "candidate_storage_policy_violation",
                "Spec 02 may contain at most 12 risk-page thumbnails",
                exit_code=3,
            )
    if stage == "canonical_block_ledger" and any(
        not relative.startswith("media/selected/") for relative in rasters
    ):
        raise StageEntrypointError(
            "candidate_storage_policy_violation",
            "Spec 03 may contain only explicitly selected media",
            exit_code=3,
        )


def _completed_production(
    output: Path,
    *,
    stage_manifest: str,
    stage_schema: str,
    artifact_kind: str,
    execution: int,
    roles: Mapping[str, str],
) -> StageProduction:
    manifest_path = _required_file(output, stage_manifest)
    manifest = _read_json(manifest_path, "native stage manifest")
    if manifest.get("schema_version") != stage_schema or manifest.get("status") != "passed":
        raise StageEntrypointError(
            "native_stage_candidate_not_passed",
            "native producer did not create a closed candidate-stage manifest",
            exit_code=3,
        )
    promotion_status = manifest.get("promotion_status", "not_evaluated")
    if promotion_status not in {"not_evaluated", None}:
        raise StageEntrypointError(
            "producer_self_promotion_forbidden",
            "producer stage manifest attempted to claim promotion",
            exit_code=3,
        )
    _required_file(output, "manifests/run_manifest.json")
    for relative in roles:
        _required_file(output, relative)
    host_refs = _forbidden_host_references(output)
    if host_refs:
        raise StageEntrypointError(
            "candidate_host_path_reference",
            "candidate contains mutable host path references",
            findings=tuple(
                {"code": "candidate_host_path_reference", "path": path}
                for path in host_refs[:20]
            ),
            exit_code=3,
        )
    return StageProduction(
        artifact_kind=artifact_kind,
        metrics={
            "native_kernel_returncode": execution,
            "native_stage_self_report": "passed",
            "promotion_status": "not_evaluated",
        },
        findings=(
            {
                "code": "producer_self_report_not_acceptance",
                "severity": "info",
                "detail": (
                    "Native producer self-report is candidate evidence only; "
                    "independent evaluation and promotion remain pending."
                ),
            },
        ),
        artifact_roles=roles,
    )


def _forbidden_host_references(root: Path) -> list[str]:
    mutable_skill_fragment = f".codex{os.sep}skills"
    user_home_pattern = re.compile(r"/(?:home|Users)/[^/\"'\s]+")
    findings: list[str] = []
    for path in sorted(
        item
        for item in root.rglob("*")
        if item.is_file() and not item.is_symlink()
    ):
        if path.stat().st_size > 5_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if (
            "~" + os.sep + ".codex" in text
            or mutable_skill_fragment in text
            or user_home_pattern.search(text)
        ):
            findings.append(path.relative_to(root).as_posix())
    return findings


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StageEntrypointError(
            "stage_json_invalid",
            f"{label} is not valid JSON: {exc}",
        ) from exc
    if not isinstance(value, dict):
        raise StageEntrypointError("stage_json_invalid", f"{label} must be an object")
    return value


def _canonical_hash(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StageEntrypointError(
            "stage_json_invalid",
            "review result is not canonical JSON",
        ) from exc
    return hashlib.sha256(payload).hexdigest()
