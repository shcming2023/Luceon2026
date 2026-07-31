from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.workflow_v3 import spec05_06_stage_adapters as adapters, stage_evaluators
from app.workflow_v3.overleaf_compiler import (
    ADAPTER_PROTOCOL,
    COMPILE_COMMAND,
    MAX_IMAGE_BYTES,
    MAX_ZIP_BYTES,
    PINNED_OVERLEAF_BASE_IMAGE,
    TARGET_ENVIRONMENT_SCHEMA,
    OverleafCompileEvidence,
)
from app.workflow_v3.stage_evaluation_entrypoint import (
    ControlPlaneChainSnapshot,
    EvaluationInput,
    StageEvaluationRequest,
)
from app.workflow_v3.stage_evaluators import STAGE_GATES


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha(path),
        "size_bytes": path.stat().st_size,
    }


def _request(stage: str, root: Path) -> StageEvaluationRequest:
    return StageEvaluationRequest(
        job_id="job",
        stage_key=stage,
        stage_version="test",
        attempt=1,
        candidate=None,  # type: ignore[arg-type]
        release_manifest_sha256="1" * 64,
        policy_sha256="2" * 64,
        required_gates=STAGE_GATES[stage],
        output_manifest="evaluation-manifest.json",
        workdir=root,
    )


def test_spec05_consumes_explicit_promoted_template_capability_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    assets = tmp_path / "assets"
    output = tmp_path / "output"
    source_pdf = tmp_path / "source.pdf"
    capability = tmp_path / "template-capability.json"
    for path, payload in (
        (parent / "render/volume_partition_plan.json", b"{}\n"),
        (parent / "media/media_evidence_ledger.json", b"{}\n"),
        (parent / "media/media_representation_plan.json", b"{}\n"),
        (source_pdf, b"source"),
        (capability, b"{}\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    paths = {
        role: tmp_path / f"{role}.json"
        for role in (
            "promotion_registry",
            "predecessor_promotion_manifest",
            "template_archive",
            "template_intake",
            "metadata_config",
            "presentation_config",
            "build_policy",
        )
    }
    paths.update(
        source_pdf=source_pdf,
        template_capability_manifest=capability,
    )
    for path in paths.values():
        if not path.exists():
            path.write_bytes(b"{}\n")

    class Inputs:
        def extracted(self, role: str) -> Path:
            return parent if role == "promoted_predecessor" else assets

        def file(self, role: str) -> Path:
            return paths[role]

    monkeypatch.setattr(adapters, "_require_roles", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        adapters,
        "require_parameter_keys",
        lambda *_args, **_kwargs: {
            "parent_lineage_key": "job:frozen_render_plan",
            "run_id": "run-1",
            "body_marker": "LUCEON_GENERATED_BODY",
        },
    )

    def run_kernel(*, args: list[str], **_kwargs: Any) -> SimpleNamespace:
        index = args.index("--capability-manifest")
        assert Path(args[index + 1]) == capability
        run = output / "spec05/manifests"
        _json(
            run / "spec05_native_stage_manifest.json",
            {
                "status": "passed",
                "spec_status": "passed",
                "promotion_class": "formal_native",
            },
        )
        _json(
            run / "delivery_set_manifest.json",
            {
                "schema_version": "spec05-delivery-set-manifest/1.2",
                "spec_status": "passed",
                "volume_count": 1,
            },
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(adapters, "run_release_python_kernel", run_kernel)
    request = SimpleNamespace(
        stage_key="deterministic_elegantbook",
        predecessor_promotion=SimpleNamespace(stage_key="frozen_render_plan"),
        workdir=tmp_path,
    )
    produced = adapters._produce_spec05(
        request,
        Inputs(),
        output,
        tmp_path,
    )

    assert produced.metrics["native_kernel_returncode"] == 0
    assert not (parent / "template/template_capability_manifest.json").exists()


def _readiness_fixture(
    bundle: Path,
    root: Path,
) -> tuple[StageEvaluationRequest, dict[str, Any], dict[str, Any]]:
    prior_stages = list(STAGE_GATES)[:-1]
    control_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    for index, stage in enumerate(prior_stages, start=1):
        stage_version = f"{stage}.v1"
        stage_run_id = f"stage-{index}"
        candidate_id = f"candidate-{index}"
        evaluation_id = f"evaluation-{index}"
        promotion_id = f"promotion-{index}"
        artifact_sha256 = f"{index:064x}"
        evaluation_record_sha256 = f"{index + 20:064x}"
        promotion_record_sha256 = f"{index + 40:064x}"
        control_rows.append(
            {
                "stage_key": stage,
                "stage_version": stage_version,
                "stage_run_id": stage_run_id,
                "artifact_version": {"candidate_id": candidate_id},
                "evaluation": {
                    "evaluation_id": evaluation_id,
                    "record_sha256": evaluation_record_sha256,
                },
                "promotion": {
                    "promotion_id": promotion_id,
                    "artifact_sha256": artifact_sha256,
                    "record_sha256": promotion_record_sha256,
                },
            }
        )
        candidate_rows.append(
            {
                "stage_key": stage,
                "stage_version": stage_version,
                "stage_run_id": stage_run_id,
                "candidate_id": candidate_id,
                "evaluation_id": evaluation_id,
                "promotion_id": promotion_id,
                "artifact_sha256": artifact_sha256,
                "evaluation_record_sha256": evaluation_record_sha256,
                "promotion_record_sha256": promotion_record_sha256,
                "evaluation_decision": "passed",
                "promotion_status": "promoted",
            }
        )
    control_payload = {
        "schema_version": "luceon.worker-v3-control-plane-chain/v1",
        "job_id": "job",
        "workflow_version": "worker-v3.0",
        "stage_key": "ready_for_user_acceptance",
        "stage_version": "test",
        "stage_run_id": "stage-12",
        "stage_attempt": 1,
        "release_manifest_sha256": "1" * 64,
        "source_popo_manifest_sha256": "9" * 64,
        "promotions": control_rows,
    }
    control_path = root / "control-plane/promotion-chain.json"
    _json(control_path, control_payload)
    control_path.chmod(0o444)
    control_json = json.dumps(
        control_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    request = StageEvaluationRequest(
        job_id="job",
        stage_key="ready_for_user_acceptance",
        stage_version="test",
        attempt=1,
        candidate=None,  # type: ignore[arg-type]
        release_manifest_sha256="1" * 64,
        policy_sha256="2" * 64,
        required_gates=STAGE_GATES["ready_for_user_acceptance"],
        output_manifest="evaluation-manifest.json",
        workdir=root,
        control_plane_chain=ControlPlaneChainSnapshot(
            path=control_path,
            sha256=_sha(control_path),
            size_bytes=control_path.stat().st_size,
            _canonical_json=control_json,
        ),
    )
    chain = {
        "schema_version": "luceon.worker-v3-promotion-chain/v2",
        "job_id": "job",
        "workflow_version": "worker-v3.0",
        "release_manifest_sha256": "1" * 64,
        "source_popo_manifest_sha256": "9" * 64,
        "promotions": candidate_rows,
    }
    return request, control_payload, chain


def test_legacy_page_review_without_release_or_provenance_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "bundle"
    source = bundle / "lineage/source.pdf"
    candidate = bundle / "spec05/delivery/book.pdf"
    raster1 = bundle / "review/pages/page-0001.png"
    raster2 = bundle / "review/pages/page-0002.png"
    for path, payload in (
        (source, b"source-pdf"),
        (candidate, b"candidate-pdf"),
        (raster1, b"page-one"),
        (raster2, b"page-two"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    monkeypatch.setattr(stage_evaluators, "_pdf_page_count", lambda path: 3 if path == source else 2)
    source_rasters = ["a" * 64, "b" * 64, "c" * 64]
    monkeypatch.setattr(
        stage_evaluators,
        "_pdf_page_raster_sha256",
        lambda path: source_rasters if path == source else ["d" * 64, "e" * 64],
    )
    monkeypatch.setattr(
        stage_evaluators,
        "_pdf_page_review_jpeg_sha256",
        lambda _path: [_sha(raster1), _sha(raster2)],
    )
    _json(
        bundle / "contracts/input_contract.json",
        {
            "material_identity": {
                "source_pdf_sha256": _sha(source),
                "source_pdf_size_bytes": source.stat().st_size,
                "page_count": 3,
            }
        },
    )
    _json(
        bundle / "contracts/source_trace.json",
        {
            "source_pdf": {
                "sha256": _sha(source),
                "size_bytes": source.stat().st_size,
                "page_count": 3,
            }
        },
    )
    _json(
        bundle / "spec05/manifests/delivery_set_manifest.json",
        {
            "schema_version": "spec05-delivery-set-manifest/1.2",
            "spec_status": "passed",
            "volume_count": 1,
            "volumes": [
                {
                    "volume_id": "v1",
                    "final_pdf": _artifact(bundle / "spec05", candidate),
                }
            ],
        },
    )
    review_input = {
        "source_pdf_sha256": _sha(source),
        "source_page_count": 3,
        "volumes": [
            {
                "volume_id": "v1",
                "candidate_pdf_sha256": _sha(candidate),
                "page_count": 2,
            }
        ],
    }
    review = {
        "schema_version": "luceon.worker-v3-full-page-review-evidence/v1",
        "review_scope": "all_pages_source_fidelity",
        "source_pdf": _artifact(bundle, source),
        "source_pdf_sha256": _sha(source),
        "source_page_count": 3,
        "blocking_findings": 0,
        "human_accepted": False,
        "reviewer": {
            "schema_version": "luceon.worker-v3-visual-review-provider/v1",
            "purpose": "full_page_source_fidelity_review",
            "provider": "test-provider",
            "model": "test-model",
            "response_id": "response-1",
            "response_ids": ["response-1"],
            "release_manifest_sha256": "1" * 64,
            "prompt_sha256": "2" * 64,
            "schema_sha256": "3" * 64,
            "input_manifest_sha256": stage_evaluators._canonical_hash(review_input),
            "calls": [
                {
                    "call_id": "4" * 64,
                    "release_manifest_sha256": "1" * 64,
                    "prompt_sha256": "2" * 64,
                    "schema_sha256": "3" * 64,
                    "input_sha256": "5" * 64,
                    "response_id": "response-1",
                    "output_sha256": "6" * 64,
                    "usage": {},
                    "latency_ms": 1,
                }
            ],
        },
        "volumes": [
            {
                "volume_id": "v1",
                "candidate_pdf": _artifact(bundle, candidate),
                "candidate_pdf_sha256": _sha(candidate),
                "page_count": 2,
                "pages": [
                    {
                        "page": 1,
                        "image": _artifact(bundle, raster1),
                        "image_sha256": _sha(raster1),
                        "status": "reviewed_passed",
                        "source_evidence": [
                            {
                                "source_page": 1,
                                "source_pdf_sha256": _sha(source),
                                "source_page_raster_sha256": source_rasters[0],
                                "evidence_kind": "full_source_page",
                            }
                        ],
                        "findings": [],
                    },
                    {
                        "page": 2,
                        "image": _artifact(bundle, raster2),
                        "image_sha256": _sha(raster2),
                        "status": "reviewed_passed",
                        "source_evidence": [
                            {
                                "source_page": 2,
                                "source_pdf_sha256": _sha(source),
                                "source_page_raster_sha256": source_rasters[1],
                                "evidence_kind": "full_source_page",
                            }
                        ],
                        "findings": [],
                    },
                ],
            }
        ],
    }
    review["reviewer"]["call_audit_sha256"] = stage_evaluators._canonical_hash(
        review["reviewer"]["calls"]
    )
    _json(bundle / "reports/page_review.json", review)
    result = stage_evaluators.evaluate_stage(
        _request("independent_full_page_review", tmp_path),
        EvaluationInput(bundle, {}),
        tmp_path,
    )
    # This is the historical pre-V3 fixture: it has no release-bound prompt,
    # deterministic page provenance, or real PDFs.  The strict shared Stage 10
    # contract must fail it closed rather than preserving the former weak pass.
    assert result.disposition == "failed"
    assert not any(result.gate_results.values())


def test_delivery_evaluator_recompiles_exact_zip_instead_of_trusting_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "bundle"
    delivery = bundle / "spec05/delivery/book.zip"
    recorded_pdf = bundle / "recompile/v1/main.pdf"
    log = bundle / "recompile/v1/main.log"
    adapter_result = bundle / "recompile/v1/overleaf-result-manifest.json"
    for path, payload in (
        (delivery, b"zip-bytes"),
        (recorded_pdf, b"pdf-bytes"),
        (log, b"compile log"),
        (adapter_result, b'{"status":"passed"}\n'),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    _json(
        bundle / "spec05/manifests/delivery_set_manifest.json",
        {
            "schema_version": "spec05-delivery-set-manifest/1.2",
            "spec_status": "passed",
            "volume_count": 1,
            "volumes": [
                {
                    "volume_id": "v1",
                    "delivery_zip": _artifact(bundle / "spec05", delivery),
                }
            ],
        },
    )
    target = {
        "schema_version": TARGET_ENVIRONMENT_SCHEMA,
        "status": "approved",
        "provider": "luceon-overleaf-compiler-adapter",
        "protocol": ADAPTER_PROTOCOL,
        "engine": "xelatex",
        "endpoint": "http://workflow-v3-overleaf-compiler:8080/compile",
        "base_image": PINNED_OVERLEAF_BASE_IMAGE,
        "adapter_image_digest": "sha256:" + "7" * 64,
        "adapter_runtime_identity_sha256": "8" * 64,
        "adapter_source_sha256": "9" * 64,
        "compiler_command": list(COMPILE_COMMAND),
        "limits": {
            "max_zip_bytes": MAX_ZIP_BYTES,
                "max_image_bytes": MAX_IMAGE_BYTES,
                "allowed_root_files": ["main.tex", "elegantbook.cls"],
                "allowed_asset_directories": ["figure", "images"],
                "allowed_body_files": ["body/generated-body.tex"],
                "allowed_body_directories": ["body/units"],
            },
    }
    target_path = bundle / "recompile/target-environment.json"
    _json(target_path, target)
    release_target_path = tmp_path / "runtime/overleaf-target-environment.json"
    _json(release_target_path, target)
    _json(
        tmp_path / "release-manifest.json",
        {
            "runtime": {
                "system_tools": {
                    "overleaf_compiler": {
                        "profile_path": "runtime/overleaf-target-environment.json",
                        "profile_sha256": _sha(release_target_path),
                    }
                }
            }
        },
    )
    _json(
        bundle / "manifests/delivery_recompile.json",
        {
            "schema_version": "luceon.worker-v3-delivery-recompile/v1",
            "compiler": "overleaf-adapter-latexmk-xelatex",
            "adapter_protocol": ADAPTER_PROTOCOL,
            "target_environment_sha256": _sha(target_path),
            "target_environment": _artifact(bundle, target_path),
            "volumes": [
                {
                    "volume_id": "v1",
                    "delivery_zip": _artifact(bundle, delivery),
                    "delivery_zip_sha256": _sha(delivery),
                    "reviewed_pdf": _artifact(bundle, recorded_pdf),
                    "reviewed_pdf_sha256": _sha(recorded_pdf),
                    "compiled_pdf": _artifact(bundle, recorded_pdf),
                    "compiled_pdf_sha256": _sha(recorded_pdf),
                    "compiled_page_count": 7,
                    "raster_profile": dict(
                        stage_evaluators.PDF_RASTER_PROFILE
                    ),
                    "reviewed_page_raster_sha256": ["4" * 64],
                    "compiled_page_raster_sha256": ["4" * 64],
                    "visual_equivalent": True,
                    "compile_log": _artifact(bundle, log),
                    "compile_log_sha256": _sha(log),
                    "overleaf_result_manifest": _artifact(bundle, adapter_result),
                    "overleaf_result_manifest_sha256": _sha(adapter_result),
                    "overleaf_runtime_identity_sha256": "8" * 64,
                    "overleaf_adapter_image_digest": "sha256:" + "7" * 64,
                    "overleaf_request_id": "a" * 32,
                }
            ],
            "promotion_status": "not_evaluated",
        },
    )
    calls: list[Path] = []

    def compile_again(
        zip_path: Path,
        workdir: Path,
        *,
        target_environment,
        role,
    ) -> OverleafCompileEvidence:
        calls.append(zip_path)
        assert target_environment == target
        assert role == "independent_evaluator"
        return OverleafCompileEvidence(
            zip_sha256=_sha(zip_path),
            pdf_path=recorded_pdf,
            pdf_sha256=_sha(recorded_pdf),
            page_count=7,
            log_path=log,
            log="",
            xelatex_version="XeTeX test",
            latexmk_version="latexmk test",
            runtime_identity_sha256="8" * 64,
            adapter_image_digest="sha256:" + "7" * 64,
            result_manifest_path=adapter_result,
            result_manifest_sha256=_sha(adapter_result),
            request_id="b" * 32,
        )

    monkeypatch.setattr(
        stage_evaluators,
        "compile_overleaf_delivery",
        compile_again,
    )
    monkeypatch.setattr(stage_evaluators, "_pdf_page_count", lambda path: 7)
    monkeypatch.setattr(
        stage_evaluators,
        "_pdf_page_raster_sha256",
        lambda path: ["4" * 64],
    )
    result = stage_evaluators.evaluate_stage(
        _request("delivery_recompile", tmp_path),
        EvaluationInput(bundle, {}),
        tmp_path,
    )
    assert calls == [delivery]
    assert all(result.gate_results.values())

    monkeypatch.setattr(
        stage_evaluators,
        "_pdf_page_raster_sha256",
        lambda path: ["5" * 64] if "independent-delivery" in str(path) else ["4" * 64],
    )
    independent_pdf = tmp_path / "independent-delivery/main.pdf"
    independent_pdf.parent.mkdir()
    independent_pdf.write_bytes(b"independent")

    def visually_different_compile(
        zip_path: Path,
        workdir: Path,
        *,
        target_environment,
        role,
    ) -> OverleafCompileEvidence:
        return OverleafCompileEvidence(
            zip_sha256=_sha(zip_path),
            pdf_path=independent_pdf,
            pdf_sha256=_sha(independent_pdf),
            page_count=7,
            log_path=log,
            log="",
            xelatex_version="XeTeX test",
            latexmk_version="latexmk test",
            runtime_identity_sha256="8" * 64,
            adapter_image_digest="sha256:" + "7" * 64,
            result_manifest_path=adapter_result,
            result_manifest_sha256=_sha(adapter_result),
            request_id="c" * 32,
        )

    monkeypatch.setattr(
        stage_evaluators,
        "compile_overleaf_delivery",
        visually_different_compile,
    )
    result = stage_evaluators.evaluate_stage(
        _request("delivery_recompile", tmp_path),
        EvaluationInput(bundle, {}),
        tmp_path,
    )
    assert result.gate_results["compiled_pdf_hash_recorded"] is False


def test_readiness_never_self_attests_human_acceptance(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    chain_path = bundle / "lineage/promotion_chain.json"
    request, _control, chain = _readiness_fixture(bundle, tmp_path)
    _json(chain_path, chain)
    lineage_path = bundle / "lineage/lineage_attestation.json"
    _json(
        lineage_path,
        {
            "schema_version": "luceon.worker-v3-page-db-minio-lineage/v1",
            "job_id": "job",
            "release_manifest_sha256": "1" * 64,
            "source_popo_manifest_sha256": "9" * 64,
            "promotion_chain_sha256": _sha(chain_path),
            "consistent": True,
            "open_blockers": [],
        },
    )
    readiness = {
        "schema_version": "luceon.worker-v3-ready-for-user-acceptance/v1",
        "machine_status": "succeeded",
        "spec_status": "passed",
        "readiness": "ready_for_user_acceptance",
        "promotion_chain": _artifact(bundle, chain_path),
        "promotion_chain_sha256": _sha(chain_path),
        "lineage_attestation": _artifact(bundle, lineage_path),
        "lineage_attestation_sha256": _sha(lineage_path),
        "lineage_consistent": True,
        "open_blockers": [],
        "human_accepted": False,
        "user_acceptance_record": None,
    }
    _json(bundle / "manifests/ready_for_user_acceptance.json", readiness)
    result = stage_evaluators.evaluate_stage(
        request,
        EvaluationInput(bundle, {}),
        tmp_path,
    )
    assert all(result.gate_results.values())

    readiness["human_accepted"] = True
    _json(bundle / "manifests/ready_for_user_acceptance.json", readiness)
    result = stage_evaluators.evaluate_stage(
        request,
        EvaluationInput(bundle, {}),
        tmp_path,
    )
    assert result.gate_results["human_acceptance_not_self_attested"] is False


def test_readiness_rejects_candidate_forged_promotion_chain(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    chain_path = bundle / "lineage/promotion_chain.json"
    request, _control, chain = _readiness_fixture(bundle, tmp_path)
    chain["promotions"][4]["artifact_sha256"] = "f" * 64
    _json(chain_path, chain)
    lineage_path = bundle / "lineage/lineage_attestation.json"
    _json(
        lineage_path,
        {
            "schema_version": "luceon.worker-v3-page-db-minio-lineage/v1",
            "job_id": "job",
            "release_manifest_sha256": "1" * 64,
            "source_popo_manifest_sha256": "9" * 64,
            "promotion_chain_sha256": _sha(chain_path),
            "consistent": True,
            "open_blockers": [],
        },
    )
    _json(
        bundle / "manifests/ready_for_user_acceptance.json",
        {
            "schema_version": "luceon.worker-v3-ready-for-user-acceptance/v1",
            "machine_status": "succeeded",
            "spec_status": "passed",
            "readiness": "ready_for_user_acceptance",
            "promotion_chain": _artifact(bundle, chain_path),
            "promotion_chain_sha256": _sha(chain_path),
            "lineage_attestation": _artifact(bundle, lineage_path),
            "lineage_attestation_sha256": _sha(lineage_path),
            "lineage_consistent": True,
            "open_blockers": [],
            "human_accepted": False,
            "user_acceptance_record": None,
        },
    )
    result = stage_evaluators.evaluate_stage(
        request,
        EvaluationInput(bundle, {}),
        tmp_path,
    )
    assert result.gate_results["all_prior_promotions_verified"] is False
