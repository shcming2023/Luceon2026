from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from app.workflow_v3.models import WorkflowV3Candidate, WorkflowV3Evaluation


REVIEW_RESOLUTION_SCHEMA = "luceon.worker-v3.review-resolution/v1"


class ReviewResolutionManifestError(ValueError):
    pass


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def finding_fingerprint(finding: Mapping[str, Any]) -> str:
    return canonical_sha256(dict(finding))


def evaluation_fingerprint(
    evaluation: WorkflowV3Evaluation,
    candidate: WorkflowV3Candidate,
) -> str:
    return canonical_sha256(
        {
            "schema_version": "luceon.worker-v3.review-evaluation-binding/v1",
            "evaluation_id": str(evaluation.id),
            "stage_run_id": str(evaluation.stage_run_id),
            "candidate": {
                "id": str(candidate.id),
                "sha256": candidate.sha256,
            },
            "evaluator_identity": evaluation.evaluator_identity,
            "evaluator_version": evaluation.evaluator_version,
            "policy_sha256": evaluation.policy_sha256,
            "decision": evaluation.decision,
            "spec_passed": bool(evaluation.spec_passed),
            "gate_results": evaluation.load(evaluation.gate_results_json, {}),
            "findings": evaluation.load(evaluation.findings_json, []),
        }
    )


def validate_review_resolution_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ReviewResolutionManifestError(
            "review resolution manifest must be an object"
        )
    required = {
        "schema_version",
        "job_id",
        "evaluation",
        "authorization",
        "blocker_resolutions",
        "recovery_stage",
        "created_at",
    }
    _exact_keys(
        manifest,
        required=required,
        allowed=required | {"stage_payload"},
        label="manifest",
    )
    if manifest["schema_version"] != REVIEW_RESOLUTION_SCHEMA:
        raise ReviewResolutionManifestError(
            "review resolution manifest schema_version is not supported"
        )
    _nonempty_string(manifest["job_id"], "manifest.job_id")
    _nonempty_string(manifest["recovery_stage"], "manifest.recovery_stage")
    _nonempty_string(manifest["created_at"], "manifest.created_at")

    evaluation = manifest["evaluation"]
    _exact_keys(
        evaluation,
        required={
            "id",
            "sha256",
            "candidate_id",
            "candidate_sha256",
            "finding_fingerprints",
        },
        label="manifest.evaluation",
    )
    _nonempty_string(evaluation["id"], "manifest.evaluation.id")
    _sha256(evaluation["sha256"], "manifest.evaluation.sha256")
    _nonempty_string(
        evaluation["candidate_id"],
        "manifest.evaluation.candidate_id",
    )
    _sha256(
        evaluation["candidate_sha256"],
        "manifest.evaluation.candidate_sha256",
    )
    fingerprints = evaluation["finding_fingerprints"]
    if (
        not isinstance(fingerprints, list)
        or not fingerprints
        or len(set(fingerprints)) != len(fingerprints)
    ):
        raise ReviewResolutionManifestError(
            "manifest.evaluation.finding_fingerprints must be a non-empty unique array"
        )
    for index, fingerprint in enumerate(fingerprints):
        _sha256(
            fingerprint,
            f"manifest.evaluation.finding_fingerprints[{index}]",
        )

    authorization = manifest["authorization"]
    _exact_keys(
        authorization,
        required={"authorized_by", "decision"},
        label="manifest.authorization",
    )
    _nonempty_string(
        authorization["authorized_by"],
        "manifest.authorization.authorized_by",
    )
    if authorization["decision"] != "revise":
        raise ReviewResolutionManifestError(
            "manifest.authorization.decision must be revise"
        )

    blockers = manifest["blocker_resolutions"]
    if not isinstance(blockers, list) or not blockers:
        raise ReviewResolutionManifestError(
            "manifest.blocker_resolutions must be a non-empty array"
        )
    blocker_fingerprints: list[str] = []
    for index, blocker in enumerate(blockers):
        label = f"manifest.blocker_resolutions[{index}]"
        _exact_keys(
            blocker,
            required={"finding_fingerprint", "disposition", "rationale"},
            label=label,
        )
        _sha256(blocker["finding_fingerprint"], f"{label}.finding_fingerprint")
        if blocker["disposition"] != "resolved_for_revision":
            raise ReviewResolutionManifestError(
                f"{label}.disposition must be resolved_for_revision"
            )
        rationale = _nonempty_string(blocker["rationale"], f"{label}.rationale")
        if len(rationale) < 3 or len(rationale) > 4000:
            raise ReviewResolutionManifestError(
                f"{label}.rationale must contain 3..4000 characters"
            )
        blocker_fingerprints.append(blocker["finding_fingerprint"])
    if len(set(blocker_fingerprints)) != len(blocker_fingerprints):
        raise ReviewResolutionManifestError(
            "manifest.blocker_resolutions contains duplicate findings"
        )

    stage_payload = manifest.get("stage_payload")
    if stage_payload is not None:
        _validate_stage_payload(stage_payload)

    return manifest


def _validate_stage_payload(value: Any) -> None:
    _exact_keys(
        value,
        required={"stage_key", "kind", "payload"},
        label="manifest.stage_payload",
    )
    if value["stage_key"] != "deterministic_elegantbook":
        raise ReviewResolutionManifestError(
            "manifest.stage_payload.stage_key is unsupported"
        )
    if value["kind"] != "spec05_warning_review":
        raise ReviewResolutionManifestError(
            "manifest.stage_payload.kind is unsupported"
        )
    review = value["payload"]
    _exact_keys(
        review,
        required={"schema_version", "status", "closures"},
        label="manifest.stage_payload.payload",
    )
    if (
        review["schema_version"] != "spec05-warning-review/1.0"
        or review["status"] != "approved"
    ):
        raise ReviewResolutionManifestError(
            "manifest.stage_payload.payload is not an approved Spec 05 warning review"
        )
    closures = review["closures"]
    if not isinstance(closures, list) or not closures:
        raise ReviewResolutionManifestError(
            "manifest.stage_payload.payload.closures must be non-empty"
        )
    fingerprints: list[str] = []
    for index, closure in enumerate(closures):
        label = f"manifest.stage_payload.payload.closures[{index}]"
        _exact_keys(
            closure,
            required={
                "fingerprint",
                "classification",
                "rationale",
                "visual_pages",
            },
            label=label,
        )
        _sha256(closure["fingerprint"], f"{label}.fingerprint")
        if closure["classification"] not in {
            "C2_REVIEW_REQUIRED_CLOSED",
            "C3_INFO_CLOSED",
        }:
            raise ReviewResolutionManifestError(
                f"{label}.classification is unsupported"
            )
        rationale = _nonempty_string(closure["rationale"], f"{label}.rationale")
        if len(rationale) > 4000:
            raise ReviewResolutionManifestError(
                f"{label}.rationale must contain at most 4000 characters"
            )
        pages = closure["visual_pages"]
        if (
            not isinstance(pages, list)
            or not pages
            or len(set(pages)) != len(pages)
            or any(
                not isinstance(page, int)
                or isinstance(page, bool)
                or page < 1
                for page in pages
            )
        ):
            raise ReviewResolutionManifestError(
                f"{label}.visual_pages must be a non-empty unique positive integer array"
            )
        fingerprints.append(closure["fingerprint"])
    if len(set(fingerprints)) != len(fingerprints):
        raise ReviewResolutionManifestError(
            "manifest.stage_payload.payload contains duplicate warning fingerprints"
        )


def _exact_keys(
    value: Any,
    *,
    required: set[str],
    label: str,
    allowed: set[str] | None = None,
) -> None:
    if not isinstance(value, dict):
        raise ReviewResolutionManifestError(f"{label} must be an object")
    actual = set(value)
    permitted = allowed if allowed is not None else required
    if not required.issubset(actual) or not actual.issubset(permitted):
        raise ReviewResolutionManifestError(
            f"{label} has missing or unknown fields"
        )


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewResolutionManifestError(f"{label} must be a non-empty string")
    return value.strip()


def _sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ReviewResolutionManifestError(
            f"{label} must be a lowercase SHA-256"
        )
    return value
