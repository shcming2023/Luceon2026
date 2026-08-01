from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import fitz
from PIL import Image


VISUAL_PROMPT_ID = "worker-v3.spec06-full-page-source-fidelity-review"
VISUAL_POLICY_MODE = "release-scoped-schema-bounded-vision"
VISUAL_REVIEW_PROTOCOL = "luceon.worker-v3-full-page-review-evidence/v1"
VISUAL_PROVIDER_PROTOCOL = "luceon.worker-v3-visual-review-provider/v1"
PAGE_PROVENANCE_PROTOCOL = "spec05-final-pdf-page-provenance/1.0"
_SHA256 = frozenset("0123456789abcdef")


class PageReviewContractError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PageReviewContractResult:
    blockers: int
    findings: tuple[Mapping[str, Any], ...]
    review_input: Mapping[str, Any]
    volume_sequence: tuple[str, ...]


def validate_page_review_contract(
    *,
    candidate_root: Path,
    review: Mapping[str, Any],
    release_root: Path,
    expected_release_sha256: str,
) -> PageReviewContractResult:
    root = candidate_root.resolve()
    release = release_root.resolve()
    if review.get("schema_version") != VISUAL_REVIEW_PROTOCOL:
        _fail("page_review_evidence_invalid", "unsupported page-review evidence")
    if (
        review.get("review_scope") != "all_pages_source_fidelity"
        or review.get("human_accepted") not in {None, False}
    ):
        _fail(
            "page_review_evidence_invalid",
            "page review has an invalid scope or acceptance claim",
        )

    source = _bound_file(root, review.get("source_pdf"), "source PDF")
    source_sha = _sha256_file(source)
    source_count = _pdf_page_count(source)
    source_rasters = _pdf_page_raster_sha256(source)
    source_review_jpegs = _pdf_page_review_jpeg_sha256(source)
    if (
        review.get("source_pdf_sha256") != source_sha
        or review.get("source_page_count") != source_count
        or len(source_rasters) != source_count
        or len(source_review_jpegs) != source_count
    ):
        _fail(
            "page_review_source_binding_mismatch",
            "page review does not bind every exact source PDF page",
        )
    _validate_source_lineage(root, source, source_count)

    release_binding = _release_visual_binding(
        release,
        expected_release_sha256=expected_release_sha256,
    )
    reviewer = review.get("reviewer")
    if not isinstance(reviewer, Mapping):
        _fail(
            "page_review_provider_binding_invalid",
            "page review has no provider binding",
        )
    expected_reviewer = {
        "schema_version": VISUAL_PROVIDER_PROTOCOL,
        "purpose": "full_page_source_fidelity_review",
        "provider": release_binding["provider"],
        "model": release_binding["model"],
        "endpoint_origin_sha256": release_binding["endpoint_origin_sha256"],
        "release_manifest_sha256": expected_release_sha256,
        "visual_policy_sha256": release_binding["visual_policy_sha256"],
        "prompt_id": VISUAL_PROMPT_ID,
        "prompt_version": release_binding["prompt_version"],
        "prompt_sha256": release_binding["prompt_sha256"],
        "schema_id": release_binding["schema_id"],
        "output_schema_version": release_binding["schema_version"],
        "schema_sha256": release_binding["schema_sha256"],
    }
    if any(reviewer.get(key) != value for key, value in expected_reviewer.items()):
        _fail(
            "page_review_provider_binding_invalid",
            "page review differs from the immutable release visual policy",
        )

    provider_pages, calls = _validate_call_chain(
        reviewer,
        release_binding=release_binding,
        source_sha256=source_sha,
        source_page_count=source_count,
    )
    delivery = _read_json(
        _required(root, "spec05/manifests/delivery_set_manifest.json"),
        "Stage 8 delivery set",
    )
    delivery_rows = delivery.get("volumes")
    if (
        delivery.get("schema_version") != "spec05-delivery-set-manifest/1.2"
        or delivery.get("spec_status") != "passed"
        or not isinstance(delivery_rows, list)
        or len(delivery_rows) not in {1, 2}
        or delivery.get("volume_count") != len(delivery_rows)
    ):
        _fail(
            "delivery_set_manifest_invalid",
            "Stage 10 requires one exact passed Stage 8 delivery set",
        )
    review_rows = review.get("volumes")
    if not isinstance(review_rows, list) or len(review_rows) != len(delivery_rows):
        _fail(
            "page_review_evidence_invalid",
            "page-review volume set differs from Stage 8",
        )

    findings: list[Mapping[str, Any]] = []
    blockers = 0
    volume_sequence: list[str] = []
    review_input_volumes: list[dict[str, Any]] = []
    provider_keys_seen: set[str] = set()
    node_owner: dict[str, str] = {}
    block_owner: dict[str, str] = {}
    source_pages_by_volume: list[list[int]] = []
    for delivery_row, volume in zip(delivery_rows, review_rows):
        if not isinstance(delivery_row, Mapping) or not isinstance(volume, Mapping):
            _fail("page_review_evidence_invalid", "page-review volume is malformed")
        volume_id = str(delivery_row.get("volume_id") or "")
        if (
            not volume_id
            or volume.get("volume_id") != volume_id
            or volume_id in volume_sequence
        ):
            _fail(
                "page_review_pdf_binding_mismatch",
                "page-review volume identity/order differs from Stage 8",
            )
        volume_sequence.append(volume_id)
        pdf = _bound_file(root, volume.get("candidate_pdf"), "candidate PDF")
        expected_pdf = _bound_file(
            root / "spec05",
            delivery_row.get("final_pdf"),
            "Stage 8 PDF",
        )
        pdf_sha = _sha256_file(pdf)
        page_count = _pdf_page_count(pdf)
        review_jpegs = _pdf_page_review_jpeg_sha256(pdf)
        if (
            pdf != expected_pdf
            or pdf_sha != _sha256_file(expected_pdf)
            or volume.get("candidate_pdf_sha256") != pdf_sha
            or volume.get("page_count") != page_count
            or len(review_jpegs) != page_count
        ):
            _fail(
                "page_review_pdf_binding_mismatch",
                "page review differs from the exact Stage 8 PDF",
            )
        provenance_path = _bound_file(
            root,
            volume.get("page_provenance"),
            "Stage 5 page provenance",
        )
        expected_provenance = _bound_file(
            root / "spec05",
            delivery_row.get("page_provenance"),
            "Stage 8 page provenance",
        )
        if provenance_path != expected_provenance:
            _fail(
                "page_review_mapping_binding_mismatch",
                "page review references a different page provenance",
            )
        provenance_pages, provenance_status = _validate_page_provenance(
            root=root,
            provenance_path=provenance_path,
            delivery_row=delivery_row,
            candidate_pdf=pdf,
            source_page_count=source_count,
        )
        if volume.get("mapping_status") != provenance_status:
            _fail(
                "page_review_mapping_binding_mismatch",
                "page-review mapping status differs from Stage 5 provenance",
            )
        pages = volume.get("pages")
        if (
            not isinstance(pages, list)
            or len(pages) != page_count
            or [row.get("page") for row in pages if isinstance(row, Mapping)]
            != list(range(1, page_count + 1))
        ):
            _fail(
                "page_review_page_evidence_invalid",
                "page review does not cover every candidate page exactly once",
            )
        duplicate_images: dict[str, int] = {}
        source_pages_seen: list[int] = []
        for index, (page, mapped) in enumerate(zip(pages, provenance_pages), 1):
            if not isinstance(page, Mapping):
                _fail("page_review_page_evidence_invalid", "page row is malformed")
            image = _bound_file(root, page.get("image"), "page review raster")
            image_sha = _sha256_file(image)
            if (
                page.get("image_sha256") != image_sha
                or image_sha != review_jpegs[index - 1]
                or page.get("disposition") != mapped["disposition"]
                or page.get("generated_role") != mapped["generated_role"]
                or page.get("mapping_authority") != PAGE_PROVENANCE_PROTOCOL
                or page.get("render_node_ids") != mapped["render_node_ids"]
                or page.get("source_block_ids") != mapped["source_block_ids"]
            ):
                _fail(
                    "page_review_page_evidence_invalid",
                    f"page review raster or mapping drifted at {volume_id}:{index}",
                )
            expected_source_pages = list(mapped["source_pages"])
            _validate_source_evidence(
                page.get("source_evidence"),
                expected_pages=expected_source_pages,
                source_sha256=source_sha,
                source_rasters=source_rasters,
            )
            for source_page in expected_source_pages:
                if source_page not in source_pages_seen:
                    source_pages_seen.append(source_page)
            for node_id in mapped["render_node_ids"]:
                owner = node_owner.setdefault(node_id, volume_id)
                if owner != volume_id:
                    _fail(
                        "page_review_cross_volume_mapping_invalid",
                        "render node crosses frozen volumes",
                    )
            for block_id in mapped["source_block_ids"]:
                owner = block_owner.setdefault(block_id, volume_id)
                if owner != volume_id:
                    _fail(
                        "page_review_cross_volume_mapping_invalid",
                        "source block crosses frozen volumes",
                    )

            provider_result = page.get("provider_result")
            provider_call_id = str(page.get("provider_call_id") or "")
            provider_result_sha = str(page.get("provider_result_sha256") or "")
            provider_row = provider_pages.get(_page_key(provider_result))
            if (
                not isinstance(provider_result, Mapping)
                or not _is_sha256(provider_call_id)
                or provider_result_sha != _canonical_hash(provider_result)
                or provider_row is None
                or provider_row["call_id"] != provider_call_id
                or provider_row["result"] != provider_result
                or provider_row["page"] != index
                or provider_row["disposition"] != mapped["disposition"]
                or provider_row["candidate_pdf_sha256"] != pdf_sha
                or provider_row["candidate_image_sha256"] != image_sha
                or provider_row["allowed_source_pages"] != expected_source_pages
            ):
                _fail(
                    "page_review_provider_binding_invalid",
                    f"provider transcript drifted at {volume_id}:{index}",
                )
            provider_key = _page_key(provider_result)
            if provider_key in provider_keys_seen:
                _fail(
                    "page_review_provider_binding_invalid",
                    f"provider page result was reused: {provider_key}",
                )
            provider_keys_seen.add(provider_key)
            _validate_allowed_sources(
                provider_row["allowed_sources"],
                expected_pages=expected_source_pages,
                source_rasters=source_rasters,
                source_review_jpegs=source_review_jpegs,
            )

            deterministic = _deterministic_findings(
                page_number=index,
                disposition=str(mapped["disposition"]),
                image_sha256=image_sha,
                duplicate_images=duplicate_images,
            )
            if page.get("deterministic_findings") != deterministic:
                _fail(
                    "page_review_deterministic_finding_invalid",
                    f"deterministic findings drifted at {volume_id}:{index}",
                )
            provider_findings = provider_result.get("findings")
            if not isinstance(provider_findings, list):
                _fail(
                    "page_review_provider_binding_invalid",
                    f"provider findings are invalid at {volume_id}:{index}",
                )
            combined = [*deterministic, *provider_findings]
            page_blockers = sum(
                isinstance(item, Mapping) and item.get("blocking") is True
                for item in combined
            )
            expected_status = (
                "reviewed_passed" if page_blockers == 0 else "reviewed_failed"
            )
            if (
                page.get("findings") != combined
                or page.get("status") != expected_status
            ):
                _fail(
                    "page_review_page_evidence_invalid",
                    f"page status/findings conflict at {volume_id}:{index}",
                )
            blockers += page_blockers
            for finding in combined:
                findings.append(
                    _formal_finding(
                        root=root,
                        finding=finding,
                        volume_id=volume_id,
                        page_number=index,
                        image_binding=page["image"],
                        provenance_binding=volume["page_provenance"],
                    )
                )
        source_pages_by_volume.append(source_pages_seen)
        review_input_volumes.append(
            {
                "volume_id": volume_id,
                "candidate_pdf_sha256": pdf_sha,
                "page_count": page_count,
                "page_provenance_sha256": _sha256_file(provenance_path),
                "mapping_status": provenance_status,
            }
        )

    if set(provider_pages) != provider_keys_seen:
        _fail(
            "page_review_provider_binding_invalid",
            "provider transcript page set differs from the review page set",
        )
    _validate_cross_volume_pages(source_pages_by_volume)
    review_input = {
        "source_pdf_sha256": source_sha,
        "source_page_count": source_count,
        "volumes": review_input_volumes,
    }
    if reviewer.get("input_manifest_sha256") != _canonical_hash(review_input):
        _fail(
            "page_review_provider_binding_invalid",
            "review input manifest hash drifted",
        )
    expected_mapping = {
        "authority": PAGE_PROVENANCE_PROTOCOL,
        "status": (
            "passed"
            if all(row["mapping_status"] == "passed" for row in review_input_volumes)
            else "needs_review"
        ),
        "volume_provenance_sha256": [
            row["page_provenance_sha256"] for row in review_input_volumes
        ],
    }
    if review.get("mapping_gate") != expected_mapping:
        _fail(
            "page_review_mapping_binding_mismatch",
            "mapping gate summary differs from exact Stage 5 provenance",
        )
    if review.get("blocking_findings") != blockers:
        _fail(
            "page_review_blocker_summary_invalid",
            "blocking finding summary differs from page evidence",
        )
    if reviewer.get("call_audit_sha256") != _canonical_hash(calls):
        _fail(
            "page_review_provider_binding_invalid",
            "provider call-audit hash drifted",
        )
    expected_response_set = "batch-set:" + _stable_id(
        *[str(call["response_id"]) for call in calls]
    )
    if reviewer.get("response_id") != expected_response_set:
        _fail(
            "page_review_provider_binding_invalid",
            "provider response-set identity drifted",
        )
    return PageReviewContractResult(
        blockers=blockers,
        findings=tuple(findings),
        review_input=review_input,
        volume_sequence=tuple(volume_sequence),
    )


def _validate_call_chain(
    reviewer: Mapping[str, Any],
    *,
    release_binding: Mapping[str, Any],
    source_sha256: str,
    source_page_count: int,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    response_ids = reviewer.get("response_ids")
    calls = reviewer.get("calls")
    if (
        not isinstance(response_ids, list)
        or not response_ids
        or len(response_ids) != len(set(response_ids))
        or any(not isinstance(value, str) or not value.strip() for value in response_ids)
        or not isinstance(calls, list)
        or len(calls) != len(response_ids)
    ):
        _fail(
            "page_review_provider_binding_invalid",
            "page review has no unique provider call chain",
        )
    provider_pages: dict[str, dict[str, Any]] = {}
    normalized_calls: list[dict[str, Any]] = []
    expected_parameters = {
        "temperature": 0,
        "max_output_tokens": release_binding["max_output_tokens"],
    }
    expected_call_fields = {
        "call_id",
        "release_id",
        "release_manifest_sha256",
        "stage_key",
        "visual_policy_sha256",
        "prompt_id",
        "prompt_version",
        "prompt_sha256",
        "schema_id",
        "schema_version",
        "schema_sha256",
        "input_sha256",
        "input_evidence",
        "request_parameters",
        "request_sha256",
        "raw_response",
        "raw_response_sha256",
        "response_id",
        "output_sha256",
        "parsed_result",
        "actual_provider",
        "actual_model",
        "endpoint_origin_sha256",
        "usage",
        "latency_ms",
    }
    for index, raw in enumerate(calls):
        if not isinstance(raw, Mapping):
            _fail("page_review_provider_binding_invalid", "provider call is malformed")
        call = dict(raw)
        input_evidence = call.get("input_evidence")
        parsed_result = call.get("parsed_result")
        parameters = call.get("request_parameters")
        if (
            set(call) != expected_call_fields
            or call.get("response_id") != response_ids[index]
            or not _is_sha256(call.get("call_id"))
            or call.get("release_id") != release_binding["release_id"]
            or call.get("release_manifest_sha256")
            != release_binding["release_manifest_sha256"]
            or call.get("visual_policy_sha256")
            != release_binding["visual_policy_sha256"]
            or call.get("stage_key") != "independent_full_page_review"
            or call.get("prompt_id") != VISUAL_PROMPT_ID
            or call.get("prompt_version") != release_binding["prompt_version"]
            or call.get("prompt_sha256") != release_binding["prompt_sha256"]
            or call.get("schema_id") != release_binding["schema_id"]
            or call.get("schema_version") != release_binding["schema_version"]
            or call.get("schema_sha256") != release_binding["schema_sha256"]
            or call.get("actual_provider") != release_binding["provider"]
            or call.get("actual_model") != release_binding["model"]
            or call.get("endpoint_origin_sha256")
            != release_binding["endpoint_origin_sha256"]
            or not isinstance(input_evidence, Mapping)
            or not isinstance(parsed_result, Mapping)
            or not isinstance(parameters, Mapping)
            or dict(parameters) != expected_parameters
            or call.get("input_sha256") != _canonical_hash(input_evidence)
            or call.get("output_sha256") != _canonical_hash(parsed_result)
            or not isinstance(call.get("raw_response"), Mapping)
            or call.get("raw_response_sha256")
            != _canonical_hash(call["raw_response"])
            or not isinstance(call.get("latency_ms"), (int, float))
            or float(call["latency_ms"]) < 0
            or not _valid_usage(call.get("usage"))
        ):
            _fail(
                "page_review_provider_binding_invalid",
                f"provider call {index} differs from its immutable release binding",
            )
        request = {
            "call_id": call["call_id"],
            "binding": {
                "release_id": call["release_id"],
                "release_sha256": call["release_manifest_sha256"],
                "stage_key": call["stage_key"],
                "prompt_id": call["prompt_id"],
                "prompt_version": call["prompt_version"],
                "prompt_sha256": call["prompt_sha256"],
                "schema_id": call["schema_id"],
                "schema_version": call["schema_version"],
                "schema_sha256": call["schema_sha256"],
                "input_sha256": call["input_sha256"],
            },
            "provider": release_binding["provider"],
            "model": release_binding["model"],
            "parameters": dict(parameters),
            "prompt": release_binding["prompt_text"],
            "input": input_evidence,
            "output_schema": release_binding["schema"],
        }
        if call.get("request_sha256") != _canonical_hash(request):
            _fail(
                "page_review_provider_binding_invalid",
                f"provider request hash drifted at call {index}",
            )
        input_pages = input_evidence.get("pages")
        result_pages = parsed_result.get("pages")
        if (
            set(parsed_result) != {"pages"}
            or set(input_evidence)
            != {
                "schema_version",
                "source_pdf_sha256",
                "source_page_count",
                "pages",
            }
            or input_evidence.get("schema_version")
            != "luceon.worker-v3-visual-review-batch/v1"
            or input_evidence.get("source_pdf_sha256") != source_sha256
            or input_evidence.get("source_page_count") != source_page_count
            or not isinstance(input_pages, list)
            or not input_pages
            or not isinstance(result_pages, list)
            or len(input_pages) != len(result_pages)
        ):
            _fail(
                "page_review_provider_binding_invalid",
                f"provider batch shape is invalid at call {index}",
            )
        for input_page, result_page in zip(input_pages, result_pages):
            if not isinstance(input_page, Mapping) or not isinstance(result_page, Mapping):
                _fail(
                    "page_review_provider_binding_invalid",
                    "provider batch page is malformed",
                )
            page_key = str(input_page.get("page_key") or "")
            allowed_pages = input_page.get("allowed_source_pages")
            allowed_sources = input_page.get("allowed_sources")
            disposition = str(input_page.get("disposition") or "")
            if (
                not page_key
                or page_key in provider_pages
                or set(input_page)
                != {
                    "page_key",
                    "page",
                    "disposition",
                    "candidate_pdf_sha256",
                    "candidate_image_sha256",
                    "allowed_source_pages",
                    "allowed_sources",
                }
                or not isinstance(input_page.get("page"), int)
                or isinstance(input_page.get("page"), bool)
                or input_page["page"] < 1
                or not _is_sha256(input_page.get("candidate_pdf_sha256"))
                or not _is_sha256(input_page.get("candidate_image_sha256"))
                or result_page.get("page_key") != page_key
                or result_page.get("page") != input_page.get("page")
                or disposition
                not in {"source_body", "generated_frontmatter", "mapping_uncertain"}
                or not isinstance(allowed_pages, list)
                or any(
                    not isinstance(page, int)
                    or isinstance(page, bool)
                    or not 1 <= page <= source_page_count
                    for page in allowed_pages
                )
                or len(allowed_pages) != len(set(allowed_pages))
                or not isinstance(allowed_sources, list)
                or len(allowed_sources) != len(allowed_pages)
                or (disposition == "source_body" and not allowed_pages)
                or (disposition != "source_body" and allowed_pages)
                or not _valid_model_result(result_page)
            ):
                _fail(
                    "page_review_provider_binding_invalid",
                    f"provider result differs from deterministic mapping at {page_key}",
                )
            normalized_result = {
                **dict(result_page),
                "source_pages": list(allowed_pages),
            }
            provider_pages[page_key] = {
                "call_id": call["call_id"],
                "result": normalized_result,
                "page": input_page["page"],
                "disposition": disposition,
                "candidate_pdf_sha256": input_page.get("candidate_pdf_sha256"),
                "candidate_image_sha256": input_page.get("candidate_image_sha256"),
                "allowed_source_pages": list(allowed_pages),
                "allowed_sources": allowed_sources,
            }
        normalized_calls.append(call)
    if [call["response_id"] for call in normalized_calls] != response_ids:
        _fail(
            "page_review_provider_binding_invalid",
            "provider response order differs from the call chain",
        )
    return provider_pages, normalized_calls


def _validate_page_provenance(
    *,
    root: Path,
    provenance_path: Path,
    delivery_row: Mapping[str, Any],
    candidate_pdf: Path,
    source_page_count: int,
) -> tuple[list[dict[str, Any]], str]:
    value = _read_json(provenance_path, "Stage 5 page provenance")
    volume_id = str(delivery_row.get("volume_id") or "")
    final_pdf = value.get("final_pdf")
    render_pack_binding = value.get("render_pack")
    frozen = value.get("frozen_inputs")
    child = provenance_path.parent.parent
    page_count = _pdf_page_count(candidate_pdf)
    if (
        value.get("schema_version") != PAGE_PROVENANCE_PROTOCOL
        or value.get("method") != "pdf_named_destination_interval"
        or value.get("volume_id") != volume_id
        or value.get("mapping_status") not in {"passed", "needs_review"}
        or not isinstance(final_pdf, Mapping)
        or final_pdf.get("sha256") != _sha256_file(candidate_pdf)
        or final_pdf.get("page_count") != page_count
        or _required(child, final_pdf.get("path")) != candidate_pdf
        or not isinstance(render_pack_binding, Mapping)
        or not isinstance(frozen, Mapping)
    ):
        _fail(
            "page_review_mapping_binding_mismatch",
            f"Stage 5 page provenance header drifted for {volume_id}",
        )
    exact = {
        "canonical_ledger_sha256": root / "ledgers/canonical_block_ledger.jsonl",
        "render_plan_sha256": root / "render/render_plan.json",
        "volume_partition_plan_sha256": root / "render/volume_partition_plan.json",
        "render_execution_sha256": child / "reports/render_execution_report.json",
        "template_contract_sha256": child / "contracts/template_contract.json",
        "presentation_config_sha256": child / "contracts/presentation_config.json",
    }
    for field, path in exact.items():
        if not path.is_file() or path.is_symlink() or frozen.get(field) != _sha256_file(path):
            _fail(
                "page_review_mapping_binding_mismatch",
                f"Stage 5 provenance {field} drifted for {volume_id}",
            )
    render_pack_path = _bound_file(
        child,
        render_pack_binding,
        "Stage 5 render pack",
    )
    expected_render_pack = _bound_file(
        root / "spec05",
        delivery_row.get("render_pack"),
        "Stage 8 render pack",
    )
    if render_pack_path != expected_render_pack:
        _fail(
            "page_review_mapping_binding_mismatch",
            f"render-pack binding drifted for {volume_id}",
        )
    render_pack = _read_json(render_pack_path, "Stage 5 render pack")
    render_pages = render_pack.get("pages")
    if (
        render_pack.get("page_count") != page_count
        or not isinstance(render_pages, list)
        or [
            row.get("index")
            for row in render_pages
            if isinstance(row, Mapping)
        ]
        != list(range(1, page_count + 1))
    ):
        _fail(
            "page_review_mapping_binding_mismatch",
            f"render-pack page coverage drifted for {volume_id}",
        )
    generated = value.get("allowed_generated_pages")
    if (
        not isinstance(generated, Mapping)
        or generated.get("region")
        != "strictly_before_first_source_body_destination"
        or generated.get("roles") != ["template_frontmatter"]
        or generated.get("template_contract_sha256")
        != frozen.get("template_contract_sha256")
        or generated.get("presentation_config_sha256")
        != frozen.get("presentation_config_sha256")
    ):
        _fail(
            "page_review_mapping_binding_mismatch",
            f"generated-page authority drifted for {volume_id}",
        )

    ledger = _read_jsonl(exact["canonical_ledger_sha256"], "canonical ledger")
    blocks: dict[str, Mapping[str, Any]] = {}
    for row in ledger:
        if row.get("record_type") == "ledger_header":
            continue
        block_id = str(row.get("block_id") or "")
        if not block_id or block_id in blocks:
            _fail(
                "page_review_mapping_binding_mismatch",
                "canonical ledger has missing or duplicate block identities",
            )
        blocks[block_id] = row
    plan = _read_json(exact["render_plan_sha256"], "render plan")
    nodes: dict[str, Mapping[str, Any]] = {}
    plan_nodes = plan.get("nodes")
    if not isinstance(plan_nodes, list):
        _fail("page_review_mapping_binding_mismatch", "render plan has no nodes")
    for row in plan_nodes:
        node_id = (
            str(row.get("render_node_id") or "")
            if isinstance(row, Mapping)
            else ""
        )
        if not node_id or node_id in nodes:
            _fail(
                "page_review_mapping_binding_mismatch",
                "render plan has missing or duplicate node identities",
            )
        nodes[node_id] = row
    partition = _read_json(exact["volume_partition_plan_sha256"], "volume partition")
    matches = [
        row
        for row in partition.get("volumes", [])
        if isinstance(row, Mapping) and row.get("volume_id") == volume_id
    ]
    if len(matches) != 1:
        _fail(
            "page_review_mapping_binding_mismatch",
            f"frozen partition has no unique {volume_id}",
        )
    frozen_volume = matches[0]
    node_ids = list(frozen_volume.get("render_node_ids") or [])
    block_ids = list(frozen_volume.get("source_block_ids") or [])
    if (
        not node_ids
        or not block_ids
        or len(node_ids) != len(set(node_ids))
        or len(block_ids) != len(set(block_ids))
        or delivery_row.get("render_node_ids") != node_ids
        or delivery_row.get("source_block_ids") != block_ids
        or any(node_id not in nodes for node_id in node_ids)
        or any(block_id not in blocks for block_id in block_ids)
    ):
        _fail(
            "page_review_mapping_binding_mismatch",
            f"delivery membership drifted for {volume_id}",
        )
    intervals = value.get("node_intervals")
    if (
        not isinstance(intervals, list)
        or [row.get("render_node_id") for row in intervals if isinstance(row, Mapping)]
        != node_ids
    ):
        _fail(
            "page_review_mapping_binding_mismatch",
            f"node intervals drifted for {volume_id}",
        )
    interval_by_id: dict[str, Mapping[str, Any]] = {}
    destinations: set[str] = set()
    previous_start = 0
    for row in intervals:
        if not isinstance(row, Mapping):
            _fail("page_review_mapping_binding_mismatch", "node interval is malformed")
        node_id = str(row["render_node_id"])
        expected_blocks = list(nodes[node_id].get("source_block_ids") or [])
        expected_pages = _ordered_unique(
            [int(blocks[block_id].get("pdf_physical_page") or 0) for block_id in expected_blocks]
        )
        start = row.get("start_candidate_page")
        end = row.get("end_candidate_page")
        start_destination = str(row.get("start_destination") or "")
        end_destination = str(row.get("end_destination") or "")
        if (
            row.get("source_block_ids") != expected_blocks
            or row.get("source_pages") != expected_pages
            or any(page < 1 or page > source_page_count for page in expected_pages)
            or not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or not 1 <= start <= end <= page_count
            or start < previous_start
            or not start_destination.startswith("luceon-v3-s-")
            or not end_destination.startswith("luceon-v3-e-")
            or start_destination in destinations
            or end_destination in destinations
        ):
            _fail(
                "page_review_mapping_binding_mismatch",
                f"node interval is invalid for {node_id}",
            )
        previous_start = start
        destinations.update((start_destination, end_destination))
        interval_by_id[node_id] = row
    pages = value.get("pages")
    if not isinstance(pages, list) or len(pages) != page_count:
        _fail(
            "page_review_mapping_binding_mismatch",
            f"provenance page coverage is incomplete for {volume_id}",
        )
    validated: list[dict[str, Any]] = []
    first_nodes: list[str] = []
    first_blocks: list[str] = []
    first_sources: list[int] = []
    body_started = False
    uncertain: list[int] = []
    for index, row in enumerate(pages, 1):
        render_page = render_pages[index - 1]
        render_raster = (
            _required(render_pack_path.parent, render_page.get("raster_path"))
            if isinstance(render_page, Mapping)
            else None
        )
        if (
            not isinstance(row, Mapping)
            or not isinstance(render_page, Mapping)
            or render_raster is None
            or render_page.get("index") != index
            or render_page.get("raster_sha256") != _sha256_file(render_raster)
            or row.get("candidate_page") != index
            or row.get("candidate_raster_sha256")
            != render_page.get("raster_sha256")
        ):
            _fail("page_review_mapping_binding_mismatch", "provenance page is malformed")
        active = [
            node_id
            for node_id in node_ids
            if int(interval_by_id[node_id]["start_candidate_page"])
            <= index
            <= int(interval_by_id[node_id]["end_candidate_page"])
        ]
        expected_blocks = _ordered_unique(
            [
                block_id
                for node_id in active
                for block_id in nodes[node_id].get("source_block_ids", [])
            ]
        )
        expected_sources = _ordered_unique(
            [int(blocks[block_id].get("pdf_physical_page") or 0) for block_id in expected_blocks]
        )
        disposition = str(row.get("disposition") or "")
        if active:
            if (
                disposition != "source_body"
                or row.get("generated_role") is not None
                or row.get("render_node_ids") != active
                or row.get("source_block_ids") != expected_blocks
                or row.get("source_pages") != expected_sources
            ):
                _fail(
                    "page_review_mapping_binding_mismatch",
                    f"source-body mapping drifted at {volume_id}:{index}",
                )
            body_started = True
            for item in active:
                if item not in first_nodes:
                    first_nodes.append(item)
            for item in expected_blocks:
                if item not in first_blocks:
                    first_blocks.append(item)
            for item in expected_sources:
                if item not in first_sources:
                    first_sources.append(item)
        elif disposition == "generated_frontmatter":
            if (
                body_started
                or row.get("generated_role") != "template_frontmatter"
                or row.get("render_node_ids") != []
                or row.get("source_block_ids") != []
                or row.get("source_pages") != []
            ):
                _fail(
                    "page_review_mapping_binding_mismatch",
                    f"frontmatter disposition drifted at {volume_id}:{index}",
                )
        elif disposition == "mapping_uncertain":
            if (
                row.get("render_node_ids") != []
                or row.get("source_block_ids") != []
                or row.get("source_pages") != []
            ):
                _fail(
                    "page_review_mapping_binding_mismatch",
                    f"uncertain mapping invents lineage at {volume_id}:{index}",
                )
            uncertain.append(index)
        else:
            _fail(
                "page_review_mapping_binding_mismatch",
                f"unknown provenance disposition at {volume_id}:{index}",
            )
        validated.append(
            {
                "candidate_page": index,
                "disposition": disposition,
                "generated_role": row.get("generated_role"),
                "render_node_ids": list(row.get("render_node_ids") or []),
                "source_block_ids": list(row.get("source_block_ids") or []),
                "source_pages": list(row.get("source_pages") or []),
            }
        )
    expected_sources = _ordered_unique(
        [int(blocks[block_id].get("pdf_physical_page") or 0) for block_id in block_ids]
    )
    summary = value.get("summary")
    if (
        first_nodes != node_ids
        or first_blocks != block_ids
        or first_sources != expected_sources
        or (value["mapping_status"] == "passed" and uncertain)
        or (value["mapping_status"] == "needs_review" and not uncertain)
        or not isinstance(summary, Mapping)
        or summary.get("candidate_pages") != page_count
        or summary.get("source_body_pages")
        != sum(row["disposition"] == "source_body" for row in validated)
        or summary.get("generated_frontmatter_pages")
        != sum(
            row["disposition"] == "generated_frontmatter"
            for row in validated
        )
        or summary.get("mapping_uncertain_pages") != uncertain
        or summary.get("render_nodes_covered") != len(first_nodes)
    ):
        _fail(
            "page_review_mapping_binding_mismatch",
            f"global provenance coverage/order failed for {volume_id}",
        )
    return validated, str(value["mapping_status"])


def _release_visual_binding(
    release_root: Path,
    *,
    expected_release_sha256: str,
) -> dict[str, Any]:
    manifest_path = _required(release_root, "release-manifest.json")
    if _sha256_file(manifest_path) != expected_release_sha256:
        _fail(
            "page_review_provider_binding_invalid",
            "installed release differs from the page-review release",
        )
    manifest = _read_json(manifest_path, "release manifest")
    prompt_rows = [
        row
        for row in manifest.get("prompts", [])
        if isinstance(row, Mapping) and row.get("id") == VISUAL_PROMPT_ID
    ]
    if len(prompt_rows) != 1:
        _fail(
            "page_review_provider_binding_invalid",
            "release has no unique visual prompt",
        )
    prompt = prompt_rows[0]
    prompt_path = _required(release_root, str(prompt.get("path") or ""))
    schema_path_value = str(prompt.get("output_schema") or "")
    schema_rows = [
        row
        for row in manifest.get("schemas", [])
        if isinstance(row, Mapping) and row.get("path") == schema_path_value
    ]
    if len(schema_rows) != 1:
        _fail(
            "page_review_provider_binding_invalid",
            "release has no unique visual schema",
        )
    schema_row = schema_rows[0]
    schema_path = _required(release_root, schema_path_value)
    schema = _read_json(schema_path, "visual output schema")
    policy_root = manifest.get("model_policy")
    policy = (
        policy_root.get("visual_review")
        if isinstance(policy_root, Mapping)
        else None
    )
    if (
        not isinstance(policy, Mapping)
        or policy.get("mode") != VISUAL_POLICY_MODE
        or not isinstance(policy.get("provider"), str)
        or not policy["provider"]
        or not isinstance(policy.get("model"), str)
        or not policy["model"]
        or not _is_sha256(policy.get("endpoint_origin_sha256"))
        or not isinstance(policy.get("max_output_tokens"), int)
        or isinstance(policy.get("max_output_tokens"), bool)
        or policy["max_output_tokens"] < 1
        or prompt.get("sha256") != _sha256_file(prompt_path)
        or schema_row.get("sha256") != _sha256_file(schema_path)
    ):
        _fail(
            "page_review_provider_binding_invalid",
            "release visual policy or bytes are invalid",
        )
    return {
        "release_id": str(manifest.get("release_id") or ""),
        "release_manifest_sha256": expected_release_sha256,
        "prompt_version": str(prompt.get("version") or ""),
        "prompt_sha256": str(prompt["sha256"]),
        "prompt_text": prompt_path.read_text(encoding="utf-8"),
        "schema_id": str(schema_row.get("id") or schema.get("$id") or ""),
        "schema_version": str(schema_row.get("version") or ""),
        "schema_sha256": _canonical_hash(schema),
        "schema": schema,
        "provider": str(policy["provider"]),
        "model": str(policy["model"]),
        "max_output_tokens": int(policy["max_output_tokens"]),
        "endpoint_origin_sha256": str(policy["endpoint_origin_sha256"]),
        "visual_policy_sha256": _canonical_hash(policy),
    }


def _validate_source_lineage(root: Path, source: Path, page_count: int) -> None:
    contract = _read_json(
        _required(root, "contracts/input_contract.json"),
        "input contract",
    )
    trace = _read_json(
        _required(root, "contracts/source_trace.json"),
        "source trace",
    )
    identity = contract.get("material_identity")
    traced = trace.get("source_pdf")
    if (
        not isinstance(identity, Mapping)
        or not isinstance(traced, Mapping)
        or identity.get("source_pdf_sha256") != _sha256_file(source)
        or identity.get("source_pdf_size_bytes") != source.stat().st_size
        or identity.get("page_count") != page_count
        or traced.get("sha256") != _sha256_file(source)
        or traced.get("size_bytes") != source.stat().st_size
        or traced.get("page_count") != page_count
    ):
        _fail(
            "page_review_source_lineage_mismatch",
            "page review source differs from the frozen source identity",
        )


def _validate_source_evidence(
    value: Any,
    *,
    expected_pages: Sequence[int],
    source_sha256: str,
    source_rasters: Sequence[str],
) -> None:
    if not isinstance(value, list) or len(value) != len(expected_pages):
        _fail(
            "page_review_page_evidence_invalid",
            "page source-evidence cardinality differs from provenance",
        )
    actual: list[int] = []
    for row in value:
        page = row.get("source_page") if isinstance(row, Mapping) else None
        if (
            not isinstance(row, Mapping)
            or not isinstance(page, int)
            or isinstance(page, bool)
            or not 1 <= page <= len(source_rasters)
            or row.get("source_pdf_sha256") != source_sha256
            or row.get("source_page_raster_sha256") != source_rasters[page - 1]
            or row.get("evidence_kind") != "full_source_page"
        ):
            _fail(
                "page_review_page_evidence_invalid",
                "page source evidence is invalid",
            )
        actual.append(page)
    if actual != list(expected_pages):
        _fail(
            "page_review_page_evidence_invalid",
            "page source evidence differs from deterministic provenance",
        )


def _validate_allowed_sources(
    value: Any,
    *,
    expected_pages: Sequence[int],
    source_rasters: Sequence[str],
    source_review_jpegs: Sequence[str],
) -> None:
    if not isinstance(value, list) or len(value) != len(expected_pages):
        _fail(
            "page_review_provider_binding_invalid",
            "provider allowed-source set differs from provenance",
        )
    for row, expected_page in zip(value, expected_pages):
        if (
            not isinstance(row, Mapping)
            or set(row)
            != {
                "source_page",
                "image_sha256",
                "source_page_raster_sha256",
            }
            or row.get("source_page") != expected_page
            or row.get("source_page_raster_sha256")
            != source_rasters[expected_page - 1]
            or row.get("image_sha256")
            != source_review_jpegs[expected_page - 1]
        ):
            _fail(
                "page_review_provider_binding_invalid",
                "provider allowed-source evidence is invalid",
            )


def _deterministic_findings(
    *,
    page_number: int,
    disposition: str,
    image_sha256: str,
    duplicate_images: dict[str, int],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if disposition == "mapping_uncertain":
        findings.append(
            {
                "code": "MAPPING_UNCERTAIN",
                "detail": (
                    "Stage 5 could not bind this candidate page to a frozen "
                    "render-node/source-page interval."
                ),
                "blocking": True,
                "responsibility_stage": "deterministic_elegantbook",
            }
        )
    duplicate = duplicate_images.get(image_sha256)
    if duplicate is not None:
        findings.append(
            {
                "code": "ORDER_OR_DUPLICATION",
                "detail": (
                    f"candidate raster exactly duplicates page {duplicate} "
                    "in the same volume"
                ),
                "blocking": True,
                "responsibility_stage": "deterministic_elegantbook",
            }
        )
    else:
        duplicate_images[image_sha256] = page_number
    return findings


def _formal_finding(
    *,
    root: Path,
    finding: Any,
    volume_id: str,
    page_number: int,
    image_binding: Mapping[str, Any],
    provenance_binding: Mapping[str, Any],
) -> Mapping[str, Any]:
    if (
        not isinstance(finding, Mapping)
        or finding.get("blocking") is not True
        or not isinstance(finding.get("code"), str)
        or not isinstance(finding.get("detail"), str)
        or not isinstance(finding.get("responsibility_stage"), str)
    ):
        _fail("page_review_finding_invalid", "page finding is malformed")
    image = _bound_file(root, image_binding, "finding page image")
    provenance = _bound_file(root, provenance_binding, "finding provenance")
    responsible = str(finding["responsibility_stage"])
    return {
        "code": str(finding["code"]),
        "blocking": True,
        "message": str(finding["detail"]),
        "volume_id": volume_id,
        "page": page_number,
        "responsible_stage": responsible,
        "recovery_stage": responsible,
        "evidence_refs": [
            {
                "path": str(image_binding["path"]),
                "sha256": _sha256_file(image),
                "size_bytes": image.stat().st_size,
            },
            {
                "path": str(provenance_binding["path"]),
                "sha256": _sha256_file(provenance),
                "size_bytes": provenance.stat().st_size,
            },
        ],
        "handoff": {
            "summary": str(finding["detail"]),
            "required_action": (
                f"Resolve {finding['code']} using the bound page and "
                "page-provenance evidence, then resume from the responsible stage."
            ),
            "resume_stage": responsible,
        },
    }


def _validate_cross_volume_pages(values: Sequence[Sequence[int]]) -> None:
    for pages in values:
        if any(
            not isinstance(page, int)
            or isinstance(page, bool)
            or page < 1
            for page in pages
        ) or list(pages) != sorted(set(pages)):
            _fail(
                "page_review_cross_volume_mapping_invalid",
                "source-page order inside a frozen volume is not strictly increasing",
            )
    if len(values) == 1:
        return
    if len(values) != 2:
        _fail(
            "page_review_cross_volume_mapping_invalid",
            "page review supports one or two volumes",
        )
    left, right = values
    overlap = set(left).intersection(right)
    allowed = {left[-1]} if left and right and left[-1] == right[0] else set()
    if (
        overlap != allowed
        or (left and right and left[-1] > right[0])
    ):
        _fail(
            "page_review_cross_volume_mapping_invalid",
            "source pages overlap volumes outside one exact boundary page",
        )


def _valid_provider_result(value: Mapping[str, Any]) -> bool:
    if set(value) != {"page_key", "page", "source_pages", "status", "findings"}:
        return False
    if (
        not isinstance(value.get("page_key"), str)
        or not value["page_key"]
        or not isinstance(value.get("page"), int)
        or isinstance(value.get("page"), bool)
        or value["page"] < 1
        or not isinstance(value.get("source_pages"), list)
    ):
        return False
    findings = value.get("findings")
    if not isinstance(findings, list) or any(
        not isinstance(row, Mapping)
        or set(row)
        != {"code", "detail", "blocking", "responsibility_stage"}
        or not isinstance(row.get("code"), str)
        or not row["code"]
        or not isinstance(row.get("detail"), str)
        or not row["detail"]
        or row.get("blocking") is not True
        or not isinstance(row.get("responsibility_stage"), str)
        or not row["responsibility_stage"]
        for row in findings
    ):
        return False
    blockers = len(findings)
    return (
        value.get("status") == "passed" and blockers == 0
    ) or (
        value.get("status") == "failed" and blockers > 0
    )


def _valid_model_result(value: Mapping[str, Any]) -> bool:
    if set(value) != {"page_key", "page", "status", "findings"}:
        return False
    normalized = {**dict(value), "source_pages": []}
    return _valid_provider_result(normalized)


def _valid_usage(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("input_tokens"), int)
        and not isinstance(value.get("input_tokens"), bool)
        and value["input_tokens"] >= 0
        and isinstance(value.get("output_tokens"), int)
        and not isinstance(value.get("output_tokens"), bool)
        and value["output_tokens"] >= 0
    )


def _page_key(value: Any) -> str:
    return str(value.get("page_key") or "") if isinstance(value, Mapping) else ""


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _ordered_unique(values: Sequence[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _pdf_page_count(path: Path) -> int:
    try:
        with fitz.open(path) as document:
            if document.page_count < 1:
                _fail("pdf_evidence_invalid", f"PDF has no page: {path.name}")
            return document.page_count
    except PageReviewContractError:
        raise
    except Exception as exc:
        _fail("pdf_evidence_invalid", f"cannot inspect PDF {path.name}: {exc}")


def _pdf_page_raster_sha256(path: Path) -> list[str]:
    try:
        with fitz.open(path) as document:
            return [
                hashlib.sha256(
                    document.load_page(index).get_pixmap(
                        matrix=fitz.Matrix(2, 2),
                        colorspace=fitz.csRGB,
                        alpha=False,
                        annots=True,
                    ).samples
                ).hexdigest()
                for index in range(document.page_count)
            ]
    except Exception as exc:
        _fail("pdf_evidence_invalid", f"cannot rasterize PDF {path.name}: {exc}")


def _review_page_jpeg_bytes(page: fitz.Page) -> bytes:
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(1.25, 1.25),
        colorspace=fitz.csRGB,
        alpha=False,
        annots=True,
    )
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    width, height = image.size
    quality = 74
    while True:
        buffer = io.BytesIO()
        image.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=False,
        )
        payload = buffer.getvalue()
        if len(payload) <= 1_500_000:
            return payload
        if quality > 48:
            quality -= 8
            continue
        if width <= 600 or height <= 800:
            raise PageReviewContractError(
                "page_review_raster_too_large",
                "page review raster cannot fit the release image budget",
            )
        width = max(600, int(width * 0.82))
        height = max(800, int(height * 0.82))
        image = image.resize((width, height), Image.Resampling.LANCZOS)


def _pdf_page_review_jpeg_sha256(path: Path) -> list[str]:
    try:
        with fitz.open(path) as document:
            return [
                hashlib.sha256(
                    _review_page_jpeg_bytes(document.load_page(index))
                ).hexdigest()
                for index in range(document.page_count)
            ]
    except PageReviewContractError:
        raise
    except Exception as exc:
        _fail(
            "pdf_evidence_invalid",
            f"cannot render review JPEGs for {path.name}: {exc}",
        )


def _bound_file(root: Path, binding: Any, label: str) -> Path:
    if not isinstance(binding, Mapping):
        _fail("artifact_binding_invalid", f"{label} binding is missing")
    path = _required(root, binding.get("path"))
    if binding.get("sha256") != _sha256_file(path):
        _fail("artifact_binding_mismatch", f"{label} hash differs from live bytes")
    return path


def _required(root: Path, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw or raw.startswith("/") or "\\" in raw:
        _fail("artifact_path_invalid", f"unsafe artifact path: {raw!r}")
    parsed = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in parsed.parts):
        _fail("artifact_path_invalid", f"unsafe artifact path: {raw!r}")
    base = root.resolve()
    lexical = base / parsed
    cursor = base
    for part in parsed.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            _fail("artifact_path_invalid", f"symlink artifact path: {raw!r}")
    path = lexical.resolve()
    if base not in path.parents or not path.is_file():
        _fail("artifact_missing", f"artifact is unavailable: {raw!r}")
    return path


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("json_evidence_invalid", f"{label} is invalid: {exc}")
    if not isinstance(value, dict):
        _fail("json_evidence_invalid", f"{label} must be an object")
    return value


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        _fail("json_evidence_invalid", f"{label} is invalid: {exc}")
    if not rows or any(not isinstance(row, dict) for row in rows):
        _fail("json_evidence_invalid", f"{label} must contain JSON objects")
    return rows


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value).issubset(_SHA256)
    )


def _fail(code: str, message: str) -> None:
    raise PageReviewContractError(code, message)
