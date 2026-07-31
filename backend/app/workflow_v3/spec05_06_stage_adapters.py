from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

try:
    from .page_review_contract import (
        PageReviewContractError,
        PageReviewContractResult,
        validate_page_review_contract,
    )
    from .overleaf_compiler import (
        ADAPTER_PROTOCOL,
        compile_overleaf_delivery,
        validate_target_environment,
    )
    from .stage_entrypoint import (
        StageEntrypointError,
        StageInputRoot,
        StageProduction,
        StageRequest,
        require_parameter_keys,
        run_release_python_kernel,
        sha256_file,
        write_json,
    )
    from .stage_evaluators import (
        PDF_RASTER_PROFILE,
        _pdf_page_count,
        _pdf_page_raster_sha256,
        _pdf_page_review_jpeg_sha256,
    )
    from .spec01_04_stage_adapters import _materialize_native_lineage_bridge
except ImportError:  # Release-local scripts import this module directly.
    from page_review_contract import (  # type: ignore[no-redef]
        PageReviewContractError,
        PageReviewContractResult,
        validate_page_review_contract,
    )
    from overleaf_compiler import (  # type: ignore[no-redef]
        ADAPTER_PROTOCOL,
        compile_overleaf_delivery,
        validate_target_environment,
    )
    from stage_entrypoint import (  # type: ignore[no-redef]
        StageEntrypointError,
        StageInputRoot,
        StageProduction,
        StageRequest,
        require_parameter_keys,
        run_release_python_kernel,
        sha256_file,
        write_json,
    )
    from stage_evaluators import (  # type: ignore[no-redef]
        _pdf_page_count,
        _pdf_page_raster_sha256,
        _pdf_page_review_jpeg_sha256,
        PDF_RASTER_PROFILE,
    )
    from spec01_04_stage_adapters import (  # type: ignore[no-redef]
        _materialize_native_lineage_bridge,
    )


_PREDECESSOR = {
    "deterministic_elegantbook": "frozen_render_plan",
    "readonly_latex_audit": "deterministic_elegantbook",
    "independent_full_page_review": "readonly_latex_audit",
    "delivery_recompile": "independent_full_page_review",
    "ready_for_user_acceptance": "delivery_recompile",
}
_SPEC05_KERNEL = "skills/cleanlatex-to-elegantbook/scripts/produce_native_spec05.py"
_AUDIT_KERNEL = "skills/refine-elegantbook-latex/scripts/refine_elegantbook_latex.py"


def produce_stage(
    request: StageRequest,
    inputs: StageInputRoot,
    candidate_root: Path,
    release_root: Path,
) -> StageProduction:
    expected = _PREDECESSOR.get(request.stage_key)
    if expected is None:
        raise StageEntrypointError(
            "stage_adapter_unknown",
            f"no Spec 05/06 adapter is registered for {request.stage_key!r}",
        )
    if (
        request.predecessor_promotion is None
        or request.predecessor_promotion.stage_key != expected
    ):
        raise StageEntrypointError(
            "predecessor_stage_mismatch",
            f"{request.stage_key} must consume promoted {expected} evidence",
        )
    if request.stage_key == "deterministic_elegantbook":
        return _produce_spec05(request, inputs, candidate_root, release_root)
    if request.stage_key == "readonly_latex_audit":
        return _produce_audit(request, inputs, candidate_root, release_root)
    if request.stage_key == "independent_full_page_review":
        return _produce_page_review(request, inputs, candidate_root, release_root)
    if request.stage_key == "delivery_recompile":
        return _produce_recompile(request, inputs, candidate_root, release_root)
    return _produce_readiness(request, inputs, candidate_root)


def _produce_spec05(
    request: StageRequest,
    inputs: StageInputRoot,
    output: Path,
    release: Path,
) -> StageProduction:
    _require_roles(
        request,
        {
            "promoted_predecessor",
            "predecessor_promotion_manifest",
            "promotion_registry",
            "source_pdf",
            "template_archive",
            "template_intake",
            "template_capability_manifest",
            "media_evidence_ledger",
            "media_representation_plan",
            "metadata_config",
            "metadata_page_render",
            "presentation_config",
            "build_policy",
            "source_scope_ledger",
            "source_asset_bundle",
        },
        bundle_roles={
            "promoted_predecessor",
            "source_asset_bundle",
        },
    )
    parameters = require_parameter_keys(
        request,
        required=("parent_lineage_key", "run_id", "body_marker"),
    )
    for name in ("parent_lineage_key", "run_id", "body_marker"):
        if not isinstance(parameters[name], str) or not parameters[name]:
            raise StageEntrypointError(
                "stage_parameters_invalid",
                f"parameter {name!r} must be non-empty text",
            )
    registry, promotions = _materialize_native_lineage_bridge(
        request=request,
        inputs=inputs,
        bindings={
            "predecessor_promotion_manifest": (
                "promoted_predecessor",
                str(parameters["parent_lineage_key"]),
                "spec04d_render_plan_contract",
            ),
        },
    )
    parent = inputs.extracted("promoted_predecessor")
    assets = inputs.extracted("source_asset_bundle")
    run = output / "spec05"
    args = [
        "--run-dir",
        str(run),
        "--run-id",
        parameters["run_id"],
        "--promotion-registry",
        str(registry),
        "--parent-promotion",
        str(promotions["predecessor_promotion_manifest"]),
        "--parent-lineage-key",
        parameters["parent_lineage_key"],
        "--template-zip",
        str(inputs.file("template_archive")),
        "--template-intake",
        str(inputs.file("template_intake")),
        "--capability-manifest",
        str(inputs.file("template_capability_manifest")),
        "--metadata-config",
        str(inputs.file("metadata_config")),
        "--presentation-config",
        str(inputs.file("presentation_config")),
        "--body-marker",
        parameters["body_marker"],
        "--volume-partition-plan",
        str(_required(parent, "render/volume_partition_plan.json")),
        "--media-evidence-ledger",
        str(inputs.file("media_evidence_ledger")),
        "--media-representation-plan",
        str(inputs.file("media_representation_plan")),
        "--asset-root",
        str(assets),
        "--source-pdf",
        str(inputs.file("source_pdf")),
        "--build-policy",
        str(inputs.file("build_policy")),
        "--stage-gate",
        str(
            release
            / "skills/luceon-popo-to-refined-elegantbook/scripts/"
            "stage_promotion_gate.py"
        ),
        "--execution-capability",
        str(
            release
            / "skills/luceon-popo-to-refined-elegantbook/scripts/"
            "execution_capability.py"
        ),
        "--contract-validator",
        str(
            release
            / "skills/luceon-popo-to-refined-elegantbook/scripts/"
            "validate_intermediate_contracts.py"
        ),
        "--media-validator",
        str(
            release
            / "skills/luceon-popo-to-refined-elegantbook/scripts/"
            "media_source_representation.py"
        ),
    ]
    execution = run_release_python_kernel(
        release_root=release,
        kernel_relative=_SPEC05_KERNEL,
        args=args,
        cwd=request.workdir,
        timeout_seconds=86_400,
    )
    stage = _read_json(
        _required(run, "manifests/spec05_native_stage_manifest.json"),
        "Spec 05 stage manifest",
    )
    if (
        stage.get("status") != "passed"
        or stage.get("spec_status") != "passed"
        or stage.get("promotion_class") != "formal_native"
    ):
        raise StageEntrypointError(
            "spec05_candidate_not_closed",
            "formal-native Spec 05 producer did not create a passed candidate",
            exit_code=3,
        )
    delivery = _read_json(
        _required(run, "manifests/delivery_set_manifest.json"),
        "delivery set manifest",
    )
    if (
        delivery.get("schema_version") != "spec05-delivery-set-manifest/1.2"
        or delivery.get("spec_status") != "passed"
        or delivery.get("volume_count") not in {1, 2}
    ):
        raise StageEntrypointError(
            "spec05_delivery_set_invalid",
            "Spec 05 delivery set is not a closed one/two-volume candidate",
            exit_code=3,
        )
    lineage = output / "lineage"
    lineage.mkdir()
    shutil.copyfile(inputs.file("source_pdf"), lineage / "source.pdf")
    return StageProduction(
        artifact_kind="worker-v3-deterministic-elegantbook-candidate",
        metrics={
            "native_kernel_returncode": execution.returncode,
            "volume_count": delivery["volume_count"],
            "promotion_status": "not_evaluated",
        },
        findings=(_candidate_only_finding(),),
        artifact_roles={
            "spec05/manifests/spec05_native_stage_manifest.json": "stage_manifest",
            "spec05/manifests/delivery_set_manifest.json": "delivery_set_manifest",
            "lineage/source.pdf": "source_pdf",
        },
    )


def _produce_audit(
    request: StageRequest,
    inputs: StageInputRoot,
    output: Path,
    release: Path,
) -> StageProduction:
    _require_roles(
        request,
        {"promoted_predecessor", "predecessor_promotion_manifest"},
        bundle_roles={"promoted_predecessor"},
    )
    require_parameter_keys(request)
    parent = inputs.extracted("promoted_predecessor")
    _copy_bundle(parent, output)
    delivery = _read_json(
        _required(output, "spec05/manifests/delivery_set_manifest.json"),
        "delivery set manifest",
    )
    volumes = _volume_artifacts(output / "spec05", delivery)
    audit_rows: list[dict[str, Any]] = []
    for row in volumes:
        before = sha256_file(row["zip"])
        audit_dir = output / "audit" / row["volume_id"]
        execution = run_release_python_kernel(
            release_root=release,
            kernel_relative=_AUDIT_KERNEL,
            args=(
                "--zip",
                str(row["zip"]),
                "--out-dir",
                str(audit_dir),
                "--mode",
                "audit",
            ),
            cwd=request.workdir,
            timeout_seconds=86_400,
        )
        after = sha256_file(row["zip"])
        if before != after:
            raise StageEntrypointError(
                "audit_mutated_input",
                "read-only audit changed the delivery ZIP",
                exit_code=3,
            )
        report = _required(audit_dir, "latex_polish_report.json")
        payload = _read_json(report, "LaTeX audit report")
        if (
            payload.get("mode") != "audit"
            or payload.get("changes")
            or any(path.suffix.lower() == ".zip" for path in audit_dir.rglob("*"))
        ):
            raise StageEntrypointError(
                "audit_write_boundary_violated",
                "LaTeX audit attempted to modify or replace the product",
                exit_code=3,
            )
        audit_rows.append(
            {
                "volume_id": row["volume_id"],
                "delivery_zip": _artifact(output, row["zip"]),
                "audit_report": _artifact(output, report),
                "kernel_returncode": execution.returncode,
            }
        )
    manifest = {
        "schema_version": "luceon.worker-v3-readonly-latex-audit/v1",
        "stage": request.stage_key,
        "input_candidate_sha256": request.primary_input.sha256,
        "input_bytes_unchanged": True,
        "replacement_product_created": False,
        "volumes": audit_rows,
        "promotion_status": "not_evaluated",
    }
    write_json(output / "manifests/readonly_latex_audit.json", manifest)
    return StageProduction(
        artifact_kind="worker-v3-readonly-latex-audit-candidate",
        metrics={"volume_count": len(audit_rows), "promotion_status": "not_evaluated"},
        findings=(_candidate_only_finding(),),
        artifact_roles={
            "manifests/readonly_latex_audit.json": "stage_manifest",
        },
    )


def _produce_page_review(
    request: StageRequest,
    inputs: StageInputRoot,
    output: Path,
    release: Path,
) -> StageProduction:
    _require_roles(
        request,
        {
            "promoted_predecessor",
            "predecessor_promotion_manifest",
            "page_review_evidence",
            "page_render_bundle",
        },
        bundle_roles={"promoted_predecessor", "page_render_bundle"},
    )
    require_parameter_keys(request)
    parent = inputs.extracted("promoted_predecessor")
    _copy_bundle(parent, output)
    review_pages = output / "review" / "pages"
    shutil.copytree(
        inputs.extracted("page_render_bundle"),
        review_pages,
        copy_function=shutil.copyfile,
    )
    _make_writable(review_pages)
    (review_pages / "candidate-content-manifest.json").unlink()
    evidence = _read_json(inputs.file("page_review_evidence"), "page review evidence")
    if (
        evidence.get("schema_version")
        != "luceon.worker-v3-full-page-review-evidence/v1"
        or evidence.get("review_scope") != "all_pages_source_fidelity"
        or evidence.get("human_accepted") not in {None, False}
    ):
        raise StageEntrypointError(
            "page_review_evidence_invalid",
            "full-page review evidence has an invalid scope or acceptance claim",
        )
    write_json(output / "reports/page_review.json", evidence)
    contract = _validate_page_review_contract(
        output,
        evidence,
        release_root=release,
        expected_release_sha256=request.release.manifest_sha256,
    )
    write_json(
        output / "manifests/full_page_review.json",
        {
            "schema_version": "luceon.worker-v3-full-page-review/v1",
            "page_review": _artifact(output, output / "reports/page_review.json"),
            "source_pdf_sha256": evidence["source_pdf_sha256"],
            "volume_count": len(evidence["volumes"]),
            "blocking_findings": contract.blockers,
            "promotion_status": "not_evaluated",
        },
    )
    return StageProduction(
        artifact_kind="worker-v3-full-page-review-candidate",
        metrics={
            "volume_count": len(evidence["volumes"]),
            "blocking_findings": contract.blockers,
            "promotion_status": "not_evaluated",
        },
        findings=(_candidate_only_finding(),),
        artifact_roles={
            "reports/page_review.json": "page_review",
            "manifests/full_page_review.json": "stage_manifest",
        },
    )


def _produce_recompile(
    request: StageRequest,
    inputs: StageInputRoot,
    output: Path,
    release: Path,
) -> StageProduction:
    _require_roles(
        request,
        {
            "promoted_predecessor",
            "predecessor_promotion_manifest",
            "target_environment",
        },
        bundle_roles={"promoted_predecessor"},
    )
    require_parameter_keys(request)
    parent = inputs.extracted("promoted_predecessor")
    _copy_bundle(parent, output)
    environment = validate_target_environment(
        _read_json(inputs.file("target_environment"), "target environment")
    )
    delivery = _read_json(
        _required(output, "spec05/manifests/delivery_set_manifest.json"),
        "delivery set manifest",
    )
    review = _read_json(
        _required(output, "reports/page_review.json"),
        "full-page review report",
    )
    _validate_page_review_contract(
        output,
        review,
        release_root=release,
        expected_release_sha256=request.release.manifest_sha256,
    )
    review_rows = review.get("volumes")
    reviewed_by_volume = {
        str(row.get("volume_id") or ""): row
        for row in review_rows
        if isinstance(row, dict)
    }
    delivery_volumes = _volume_artifacts(output / "spec05", delivery)
    delivery_sequence = [row["volume_id"] for row in delivery_volumes]
    review_sequence = [
        str(row.get("volume_id") or "")
        for row in review_rows
        if isinstance(row, dict)
    ]
    if review_sequence != delivery_sequence:
        raise StageEntrypointError(
            "delivery_volume_order_mismatch",
            "Stage 10 review volume order differs from the Stage 8 delivery set",
        )
    rows: list[dict[str, Any]] = []
    for volume in delivery_volumes:
        review_row = reviewed_by_volume[volume["volume_id"]]
        reviewed_pdf = _bound_file(
            output,
            review_row.get("candidate_pdf"),
            "Stage 10 reviewed PDF",
        )
        compile_root = request.workdir / "producer-recompile" / volume["volume_id"]
        result = compile_overleaf_delivery(
            volume["zip"],
            compile_root,
            target_environment=environment,
            role="producer",
        )
        evidence_root = output / "recompile" / volume["volume_id"]
        evidence_root.mkdir(parents=True)
        pdf = evidence_root / "main.pdf"
        log = evidence_root / "main.log"
        adapter_manifest = evidence_root / "overleaf-result-manifest.json"
        shutil.copyfile(result.pdf_path, pdf)
        shutil.copyfile(result.log_path, log)
        shutil.copyfile(result.result_manifest_path, adapter_manifest)
        reviewed_rasters = _pdf_page_raster_sha256(reviewed_pdf)
        compiled_rasters = _pdf_page_raster_sha256(pdf)
        rows.append(
            {
                "volume_id": volume["volume_id"],
                "delivery_zip": _artifact(output, volume["zip"]),
                "delivery_zip_sha256": result.zip_sha256,
                "reviewed_pdf": _artifact(output, reviewed_pdf),
                "reviewed_pdf_sha256": sha256_file(reviewed_pdf),
                "compiled_pdf": _artifact(output, pdf),
                "compiled_pdf_sha256": result.pdf_sha256,
                "compiled_page_count": result.page_count,
                "raster_profile": dict(PDF_RASTER_PROFILE),
                "reviewed_page_raster_sha256": reviewed_rasters,
                "compiled_page_raster_sha256": compiled_rasters,
                "visual_equivalent": reviewed_rasters == compiled_rasters,
                "compile_log": _artifact(output, log),
                "compile_log_sha256": sha256_file(log),
                "overleaf_result_manifest": _artifact(output, adapter_manifest),
                "overleaf_result_manifest_sha256": sha256_file(adapter_manifest),
                "overleaf_runtime_identity_sha256": result.runtime_identity_sha256,
                "overleaf_adapter_image_digest": result.adapter_image_digest,
                "overleaf_request_id": result.request_id,
                "xelatex_version": result.xelatex_version,
                "latexmk_version": result.latexmk_version,
            }
        )
    target_copy = output / "recompile" / "target-environment.json"
    shutil.copyfile(inputs.file("target_environment"), target_copy)
    manifest = {
        "schema_version": "luceon.worker-v3-delivery-recompile/v1",
        "compiler": "overleaf-adapter-latexmk-xelatex",
        "adapter_protocol": ADAPTER_PROTOCOL,
        "target_environment_sha256": request.artifact("target_environment").sha256,
        "target_environment": _artifact(output, target_copy),
        "volumes": rows,
        "promotion_status": "not_evaluated",
    }
    write_json(output / "manifests/delivery_recompile.json", manifest)
    return StageProduction(
        artifact_kind="worker-v3-delivery-recompile-candidate",
        metrics={"volume_count": len(rows), "promotion_status": "not_evaluated"},
        findings=(_candidate_only_finding(),),
        artifact_roles={
            "manifests/delivery_recompile.json": "stage_manifest",
        },
    )


def _produce_readiness(
    request: StageRequest,
    inputs: StageInputRoot,
    output: Path,
) -> StageProduction:
    _require_roles(
        request,
        {
            "promoted_predecessor",
            "predecessor_promotion_manifest",
            "promotion_chain",
            "lineage_attestation",
        },
        bundle_roles={"promoted_predecessor"},
    )
    require_parameter_keys(request)
    parent = inputs.extracted("promoted_predecessor")
    _copy_bundle(parent, output)
    chain = _read_json(inputs.file("promotion_chain"), "promotion chain")
    expected_stages = [
        "intake_snapshot",
        "source_scope_and_order",
        "canonical_block_ledger",
        "outline_reconstruction",
        "semantic_annotation",
        "template_construct_binding",
        "frozen_render_plan",
        "deterministic_elegantbook",
        "readonly_latex_audit",
        "independent_full_page_review",
        "delivery_recompile",
    ]
    promotions = chain.get("promotions")
    if (
        chain.get("schema_version") != "luceon.worker-v3-promotion-chain/v2"
        or chain.get("job_id") != request.job_id
        or chain.get("release_manifest_sha256")
        != request.release.manifest_sha256
        or not isinstance(chain.get("workflow_version"), str)
        or not chain.get("workflow_version")
        or not _is_sha256(chain.get("source_popo_manifest_sha256"))
        or not isinstance(promotions, list)
        or [row.get("stage_key") for row in promotions if isinstance(row, dict)]
        != expected_stages
        or any(
            not isinstance(row, dict)
            or set(row)
            != {
                "stage_key",
                "stage_version",
                "stage_run_id",
                "candidate_id",
                "evaluation_id",
                "promotion_id",
                "artifact_sha256",
                "evaluation_record_sha256",
                "promotion_record_sha256",
                "evaluation_decision",
                "promotion_status",
            }
            or not isinstance(row.get("stage_version"), str)
            or not row.get("stage_version")
            or not isinstance(row.get("stage_run_id"), str)
            or not row.get("stage_run_id")
            or not isinstance(row.get("candidate_id"), str)
            or not row.get("candidate_id")
            or not isinstance(row.get("evaluation_id"), str)
            or not row.get("evaluation_id")
            or not isinstance(row.get("promotion_id"), str)
            or not row.get("promotion_id")
            or not _is_sha256(row.get("artifact_sha256"))
            or not _is_sha256(row.get("evaluation_record_sha256"))
            or not _is_sha256(row.get("promotion_record_sha256"))
            or row.get("evaluation_decision") != "passed"
            or row.get("promotion_status") != "promoted"
            for row in promotions
        )
    ):
        raise StageEntrypointError(
            "promotion_chain_invalid",
            "readiness requires exactly eleven independently passed promotions",
        )
    lineage = _read_json(inputs.file("lineage_attestation"), "lineage attestation")
    if (
        lineage.get("schema_version")
        != "luceon.worker-v3-page-db-minio-lineage/v1"
        or lineage.get("job_id") != request.job_id
        or lineage.get("release_manifest_sha256")
        != request.release.manifest_sha256
        or lineage.get("source_popo_manifest_sha256")
        != chain.get("source_popo_manifest_sha256")
        or lineage.get("promotion_chain_sha256")
        != sha256_file(inputs.file("promotion_chain"))
        or lineage.get("consistent") is not True
        or lineage.get("open_blockers") != []
    ):
        raise StageEntrypointError(
            "lineage_attestation_invalid",
            "page, database, MinIO, and Worker lineage is not closed",
        )
    chain_target = output / "lineage" / "promotion_chain.json"
    chain_target.parent.mkdir(exist_ok=True)
    shutil.copyfile(inputs.file("promotion_chain"), chain_target)
    lineage_target = output / "lineage" / "lineage_attestation.json"
    shutil.copyfile(inputs.file("lineage_attestation"), lineage_target)
    readiness = {
        "schema_version": "luceon.worker-v3-ready-for-user-acceptance/v1",
        "machine_status": "succeeded",
        "spec_status": "passed",
        "readiness": "ready_for_user_acceptance",
        "promotion_chain": _artifact(output, chain_target),
        "promotion_chain_sha256": sha256_file(chain_target),
        "lineage_attestation": _artifact(output, lineage_target),
        "lineage_attestation_sha256": sha256_file(lineage_target),
        "lineage_consistent": True,
        "open_blockers": [],
        "human_accepted": False,
        "user_acceptance_record": None,
        "promotion_status": "not_evaluated",
    }
    write_json(output / "manifests/ready_for_user_acceptance.json", readiness)
    return StageProduction(
        artifact_kind="worker-v3-ready-for-user-acceptance-candidate",
        metrics={
            "prior_promotions": len(promotions),
            "human_accepted": False,
            "promotion_status": "not_evaluated",
        },
        findings=(_candidate_only_finding(),),
        artifact_roles={
            "manifests/ready_for_user_acceptance.json": "stage_manifest",
        },
    )


def _require_roles(
    request: StageRequest,
    expected: set[str],
    *,
    bundle_roles: set[str],
) -> None:
    actual = {item.role for item in request.input_artifacts}
    if actual != expected:
        raise StageEntrypointError(
            "stage_input_roles_invalid",
            "stage inputs are missing or unknown",
            findings=(
                {
                    "missing": sorted(expected - actual),
                    "unknown": sorted(actual - expected),
                },
            ),
        )
    actual_bundles = {
        item.role
        for item in request.input_artifacts
        if item.kind == "worker-v3-candidate-bundle"
    }
    if actual_bundles != bundle_roles:
        raise StageEntrypointError(
            "stage_bundle_roles_invalid",
            "stage bundle roles differ from the immutable contract",
        )


def _copy_bundle(source: Path, output: Path) -> None:
    for item in source.iterdir():
        if item.name == "candidate-content-manifest.json":
            continue
        target = output / item.name
        if item.is_dir():
            shutil.copytree(item, target, copy_function=shutil.copyfile)
            _make_writable(target)
        elif item.is_file():
            shutil.copyfile(item, target)
            target.chmod(0o600)
        else:
            raise StageEntrypointError(
                "predecessor_artifact_invalid",
                "predecessor contains a non-regular artifact",
            )


def _make_writable(root: Path) -> None:
    for path in root.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
    root.chmod(0o700)


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _volume_artifacts(root: Path, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    volumes = manifest.get("volumes")
    if (
        not isinstance(volumes, list)
        or len(volumes) not in {1, 2}
        or manifest.get("volume_count") != len(volumes)
    ):
        raise StageEntrypointError(
            "delivery_set_invalid",
            "delivery set must contain exactly one or two volumes",
        )
    result: list[dict[str, Any]] = []
    for index, volume in enumerate(volumes):
        if not isinstance(volume, dict) or not isinstance(volume.get("delivery_zip"), dict):
            raise StageEntrypointError(
                "delivery_set_invalid",
                "delivery volume has no ZIP artifact",
            )
        artifact = volume["delivery_zip"]
        path = _required(root, artifact.get("path"))
        if artifact.get("sha256") != sha256_file(path):
            raise StageEntrypointError(
                "delivery_zip_hash_mismatch",
                "delivery ZIP differs from the delivery-set manifest",
            )
        result.append(
            {
                "volume_id": str(volume.get("volume_id") or f"volume-{index + 1}"),
                "zip": path,
            }
        )
    return result


def _validate_page_review_contract(
    root: Path,
    review: Mapping[str, Any],
    *,
    release_root: Path,
    expected_release_sha256: str,
) -> PageReviewContractResult:
    try:
        return validate_page_review_contract(
            candidate_root=root,
            review=review,
            release_root=release_root,
            expected_release_sha256=expected_release_sha256,
        )
    except PageReviewContractError as exc:
        raise StageEntrypointError(exc.code, str(exc)) from exc


def _validate_page_review(
    root: Path,
    review: Mapping[str, Any],
    *,
    expected_release_sha256: str,
) -> None:
    source = _bound_file(root, review.get("source_pdf"), "source PDF")
    source_page_count = _pdf_page_count(source)
    if (
        review.get("source_pdf_sha256") != sha256_file(source)
        or review.get("source_page_count") != source_page_count
    ):
        raise StageEntrypointError(
            "page_review_source_binding_mismatch",
            "page review does not bind the exact source PDF and page count",
        )
    source_rasters = _pdf_page_raster_sha256(source)
    if len(source_rasters) != source_page_count:
        raise StageEntrypointError(
            "page_review_source_binding_mismatch",
            "source PDF raster evidence does not cover every source page",
        )
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
    if (
        not isinstance(material_identity, dict)
        or not isinstance(traced_source, dict)
        or material_identity.get("source_pdf_sha256") != sha256_file(source)
        or material_identity.get("source_pdf_size_bytes") != source.stat().st_size
        or material_identity.get("page_count") != source_page_count
        or traced_source.get("sha256") != sha256_file(source)
        or traced_source.get("size_bytes") != source.stat().st_size
        or traced_source.get("page_count") != source_page_count
    ):
        raise StageEntrypointError(
            "page_review_source_lineage_mismatch",
            "page review source differs from the promoted Spec 01 identity",
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
            and row.get("release_manifest_sha256") == expected_release_sha256
            and row.get("prompt_sha256") == reviewer.get("prompt_sha256")
            and row.get("schema_sha256") == reviewer.get("schema_sha256")
            and all(
                _is_sha256(row.get(field))
                for field in ("call_id", "input_sha256", "output_sha256")
            )
            for row in calls
        )
    )
    if (
        not isinstance(reviewer, dict)
        or reviewer.get("schema_version")
        != "luceon.worker-v3-visual-review-provider/v1"
        or reviewer.get("purpose") != "full_page_source_fidelity_review"
        or not isinstance(reviewer.get("provider"), str)
        or not reviewer["provider"].strip()
        or not isinstance(reviewer.get("model"), str)
        or not reviewer["model"].strip()
        or not isinstance(reviewer.get("response_id"), str)
        or not reviewer["response_id"].strip()
        or reviewer.get("release_manifest_sha256") != expected_release_sha256
        or any(
            not _is_sha256(reviewer.get(field))
            for field in (
                "prompt_sha256",
                "schema_sha256",
                "input_manifest_sha256",
                "call_audit_sha256",
            )
        )
        or not call_chain_bound
    ):
        raise StageEntrypointError(
            "page_review_provider_binding_invalid",
            "page review lacks an immutable visual-provider binding",
        )
    volumes = review.get("volumes")
    if not isinstance(volumes, list) or not volumes:
        raise StageEntrypointError(
            "page_review_evidence_invalid",
            "page review has no delivery volume",
        )
    delivery = _read_json(
        _required(root, "spec05/manifests/delivery_set_manifest.json"),
        "delivery set manifest",
    )
    delivery_rows = delivery.get("volumes")
    if (
        delivery.get("schema_version") != "spec05-delivery-set-manifest/1.2"
        or delivery.get("spec_status") != "passed"
        or not isinstance(delivery_rows, list)
        or delivery.get("volume_count") != len(delivery_rows)
    ):
        raise StageEntrypointError(
            "page_review_evidence_invalid",
            "page review requires the closed Stage 8 delivery set",
        )
    delivery_by_volume: dict[str, tuple[Path, str]] = {}
    delivery_sequence: list[str] = []
    for row in delivery_rows:
        if not isinstance(row, dict):
            raise StageEntrypointError(
                "page_review_evidence_invalid",
                "Stage 8 delivery volume is invalid",
            )
        volume_id = str(row.get("volume_id") or "")
        if not volume_id or volume_id in delivery_by_volume:
            raise StageEntrypointError(
                "page_review_evidence_invalid",
                "Stage 8 volume IDs are missing or duplicated",
            )
        pdf = _bound_file(root / "spec05", row.get("final_pdf"), "Stage 8 PDF")
        delivery_by_volume[volume_id] = (pdf, sha256_file(pdf))
        delivery_sequence.append(volume_id)

    blockers = 0
    review_sequence: list[str] = []
    review_inputs: list[dict[str, Any]] = []
    for volume in volumes:
        if not isinstance(volume, dict):
            raise StageEntrypointError(
                "page_review_evidence_invalid",
                "page review volume is invalid",
            )
        volume_id = str(volume.get("volume_id") or "")
        expected = delivery_by_volume.get(volume_id)
        if expected is None or volume_id in review_sequence:
            raise StageEntrypointError(
                "page_review_pdf_binding_mismatch",
                "page review volume mapping differs from Stage 8",
            )
        pdf = _bound_file(root, volume.get("candidate_pdf"), "candidate PDF")
        pages = volume.get("pages")
        count = _pdf_page_count(pdf)
        rendered_review_images = _pdf_page_review_jpeg_sha256(pdf)
        if (
            volume.get("candidate_pdf_sha256") != sha256_file(pdf)
            or volume.get("page_count") != count
            or pdf != expected[0]
            or sha256_file(pdf) != expected[1]
            or not isinstance(pages, list)
            or len(rendered_review_images) != count
            or [row.get("page") for row in pages if isinstance(row, dict)]
            != list(range(1, count + 1))
        ):
            raise StageEntrypointError(
                "page_review_pdf_binding_mismatch",
                "page review does not bind every exact candidate PDF page",
            )
        review_sequence.append(volume_id)
        review_inputs.append(
            {
                "volume_id": volume_id,
                "candidate_pdf_sha256": sha256_file(pdf),
                "page_count": count,
            }
        )
        for page in pages:
            image = _bound_file(root, page.get("image"), "page render")
            source_evidence = page.get("source_evidence")
            source_pages: list[int] = []
            evidence_valid = isinstance(source_evidence, list) and bool(source_evidence)
            if evidence_valid:
                for item in source_evidence:
                    source_page = (
                        item.get("source_page")
                        if isinstance(item, dict)
                        else None
                    )
                    if (
                        not isinstance(item, dict)
                        or not isinstance(source_page, int)
                        or isinstance(source_page, bool)
                        or source_page < 1
                        or source_page > source_page_count
                        or item.get("source_pdf_sha256")
                        != review.get("source_pdf_sha256")
                        or item.get("source_page_raster_sha256")
                        != source_rasters[source_page - 1]
                        or item.get("evidence_kind") != "full_source_page"
                    ):
                        evidence_valid = False
                        break
                    source_pages.append(source_page)
                evidence_valid = evidence_valid and len(source_pages) == len(
                    set(source_pages)
                )
            findings = page.get("findings")
            page_blockers = (
                sum(
                    isinstance(item, dict) and item.get("blocking") is True
                    for item in findings
                )
                if isinstance(findings, list)
                else -1
            )
            if (
                page.get("image_sha256") != sha256_file(image)
                or page.get("image_sha256")
                != rendered_review_images[int(page.get("page") or 0) - 1]
                or not evidence_valid
                or not isinstance(findings, list)
                or not (
                    (
                        page.get("status") == "reviewed_passed"
                        and page_blockers == 0
                    )
                    or (
                        page.get("status") == "reviewed_failed"
                        and page_blockers > 0
                    )
                )
            ):
                raise StageEntrypointError(
                    "page_review_page_evidence_invalid",
                    "each page requires exact raster, source evidence, status, and findings",
                )
            blockers += page_blockers
    if review_sequence != delivery_sequence:
        raise StageEntrypointError(
            "page_review_pdf_binding_mismatch",
            "page review volume order differs from Stage 8",
        )
    review_input = {
        "source_pdf_sha256": review.get("source_pdf_sha256"),
        "source_page_count": source_page_count,
        "volumes": review_inputs,
    }
    if reviewer.get("input_manifest_sha256") != _canonical_hash(review_input):
        raise StageEntrypointError(
            "page_review_provider_binding_invalid",
            "visual-provider input hash differs from the exact review set",
        )
    declared_blockers = review.get("blocking_findings")
    if (
        not isinstance(declared_blockers, int)
        or isinstance(declared_blockers, bool)
        or declared_blockers < 0
        or declared_blockers != blockers
    ):
        raise StageEntrypointError(
            "page_review_blocker_summary_invalid",
            "page review blocker summary must exactly match per-page findings",
        )


def _bound_file(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, dict):
        raise StageEntrypointError(
            "artifact_binding_invalid",
            f"{label} binding is missing",
        )
    path = _required(root, value.get("path"))
    if value.get("sha256") != sha256_file(path):
        raise StageEntrypointError(
            "artifact_binding_mismatch",
            f"{label} binding differs from live bytes",
        )
    return path


def _required(root: Path, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw or raw.startswith("/") or "\\" in raw:
        raise StageEntrypointError("candidate_path_invalid", "candidate path is invalid")
    path = PurePosixPath(raw)
    if str(path) != raw or any(part in {"", ".", ".."} for part in path.parts):
        raise StageEntrypointError("candidate_path_invalid", "candidate path is not normalized")
    current = root.resolve()
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise StageEntrypointError(
                "candidate_path_invalid",
                "candidate path cannot contain symlinks",
            )
    candidate = (root / raw).resolve()
    if root.resolve() not in candidate.parents or not candidate.is_file():
        raise StageEntrypointError(
            "candidate_artifact_missing",
            f"required candidate artifact {raw!r} is missing",
        )
    return candidate


def _artifact(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


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


def _candidate_only_finding() -> Mapping[str, Any]:
    return {
        "code": "producer_candidate_not_acceptance",
        "severity": "info",
        "detail": (
            "Producer output awaits independent evaluation and control-plane promotion."
        ),
    }


__all__ = ["produce_stage"]
