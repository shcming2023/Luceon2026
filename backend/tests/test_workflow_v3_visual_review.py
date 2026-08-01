from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import fitz
import pytest

from app.workflow_v3.llm_gateway import LlmCallResult, LlmGatewayError, sha256_json
from app.workflow_v3.visual_review import (
    VISUAL_PROVIDER_PROTOCOL,
    VISUAL_PROMPT_ID,
    VisualReviewError,
    _assistant_json_content,
    _visual_model_call_id,
    build_full_page_review_inputs,
)


def test_visual_model_call_id_is_stable_for_the_same_job_scope() -> None:
    values = {
        "call_scope_id": "stable-job-idempotency-key",
        "stage_key": "independent_full_page_review",
        "stage_attempt": 1,
        "batch_offset": 0,
        "evidence_sha256": "a" * 64,
        "prompt_sha256": "b" * 64,
    }

    first = _visual_model_call_id(**values)
    assert first == _visual_model_call_id(**values)
    assert first != _visual_model_call_id(
        **{**values, "call_scope_id": "different-job-scope"}
    )


@pytest.mark.parametrize("block_type", [None, "text", "output_text"])
def test_visual_transport_accepts_one_multimodal_assistant_text_block(
    block_type: str | None,
) -> None:
    block = {"text": '{"pages": []}'}
    if block_type is not None:
        block["type"] = block_type

    assert _assistant_json_content([block]) == '{"pages": []}'


@pytest.mark.parametrize(
    "content",
    [
        [],
        [{"text": "{}"}, {"text": "{}"}],
        [{"type": "image_url", "text": "{}"}],
        [{"type": "text", "text": ""}],
    ],
)
def test_visual_transport_rejects_ambiguous_assistant_content(content) -> None:
    with pytest.raises(LlmGatewayError, match="visual review assistant content"):
        _assistant_json_content(content)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pdf(path: Path, pages: list[str]) -> None:
    document = fitz.open()
    for text in pages:
        page = document.new_page(width=420, height=594)
        page.insert_text((40, 80), text, fontsize=14)
    document.save(path)
    document.close()


def _release(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "release"
    prompt_path = root / "prompts/spec06.txt"
    schema_path = root / "schemas/spec06.json"
    prompt_path.parent.mkdir(parents=True)
    schema_path.parent.mkdir(parents=True)
    prompt_path.write_text("Compare every candidate page with allowed sources.\n")
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["pages"],
        "properties": {
            "pages": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "page_key",
                        "page",
                        "source_pages",
                        "status",
                        "findings",
                    ],
                    "properties": {
                        "page_key": {"type": "string", "minLength": 1},
                        "page": {"type": "integer", "minimum": 1},
                        "source_pages": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 6,
                            "uniqueItems": True,
                            "items": {"type": "integer", "minimum": 1},
                        },
                        "status": {
                            "type": "string",
                            "enum": ["passed", "failed"],
                        },
                        "findings": {
                            "type": "array",
                            "maxItems": 32,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "code",
                                    "detail",
                                    "blocking",
                                    "responsibility_stage",
                                ],
                                "properties": {
                                    "code": {
                                        "type": "string",
                                        "enum": ["MAPPING_UNCERTAIN"],
                                    },
                                    "detail": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 2000,
                                    },
                                    "blocking": {"type": "boolean"},
                                    "responsibility_stage": {
                                        "type": "string",
                                        "enum": [
                                            "independent_full_page_review"
                                        ],
                                    },
                                },
                            },
                        },
                    },
                },
            }
        },
    }
    # Pretty bytes deliberately differ from the canonical JSON hash. The
    # release binds file bytes; the model-call contract binds parsed schema.
    schema_path.write_text(json.dumps(schema, indent=2) + "\n")
    release_sha = "a" * 64
    manifest = {
        "release_id": "worker-v3-test",
        "prompts": [
            {
                "id": VISUAL_PROMPT_ID,
                "version": "1.0.0",
                "path": "prompts/spec06.txt",
                "sha256": _sha(prompt_path),
                "output_schema": "schemas/spec06.json",
            }
        ],
        "schemas": [
            {
                "id": "spec06",
                "version": "1.0.0",
                "path": "schemas/spec06.json",
                "sha256": _sha(schema_path),
            }
        ],
        "model_policy": {
            "visual_review": {
                "mode": "release-scoped-schema-bounded-vision",
                "provider": "dashscope",
                "model": "vision-test",
                "endpoint_origin_sha256": hashlib.sha256(
                    b"https://example.test"
                ).hexdigest(),
                "batch_size": 2,
                "max_output_tokens": 1000,
                "timeout_seconds": 30,
                "max_stage_calls": 10,
                "max_stage_input_tokens": 100_000,
                "max_stage_output_tokens": 10_000,
                "max_stage_seconds": 300,
                "max_source_pages": 100,
                "max_candidate_pages": 100,
                "min_free_bytes": 1,
                "max_render_bundle_bytes": 50_000_000,
            }
        },
    }
    (root / "release-manifest.json").write_text(json.dumps(manifest))
    assert _sha(schema_path) != sha256_json(schema)
    return root, release_sha


def _predecessor(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "predecessor"
    spec05 = root / "spec05"
    pdf = spec05 / "delivery/book.pdf"
    pdf.parent.mkdir(parents=True)
    _pdf(
        pdf,
        [
            "Unit one: plants need sunlight and water.",
            "Unit two: forces can push or pull an object.",
        ],
    )
    canonical = root / "ledgers/canonical_block_ledger.jsonl"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "record_type": "source_block",
                    "block_id": "block-1",
                    "pdf_physical_page": 1,
                },
                {
                    "record_type": "source_block",
                    "block_id": "block-2",
                    "pdf_physical_page": 2,
                },
            )
        )
        + "\n"
    )
    render_plan = root / "render/render_plan.json"
    render_plan.parent.mkdir(parents=True)
    render_plan.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "render_node_id": "node-1",
                        "source_block_ids": ["block-1"],
                    },
                    {
                        "render_node_id": "node-2",
                        "source_block_ids": ["block-2"],
                    },
                ]
            }
        )
    )
    partition = root / "render/volume_partition_plan.json"
    partition.write_text(
        json.dumps(
            {
                "volumes": [
                    {
                        "volume_id": "volume-1",
                        "render_node_ids": ["node-1", "node-2"],
                        "source_block_ids": ["block-1", "block-2"],
                    }
                ]
            }
        )
    )
    child = spec05 / "runs/volume-1"
    render_execution = child / "reports/render_execution_report.json"
    template_contract = child / "contracts/template_contract.json"
    presentation_config = child / "contracts/presentation_config.json"
    render_pack = child / "final_render_pack/manifest.json"
    for path, payload in (
        (render_execution, {"status": "passed"}),
        (template_contract, {"template": "frozen"}),
        (presentation_config, {"presentation": "frozen"}),
        (
            render_pack,
            {
                "pages": [
                    {"raster_sha256": "1" * 64},
                    {"raster_sha256": "2" * 64},
                ]
            },
        ),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    provenance = {
        "schema_version": "spec05-final-pdf-page-provenance/1.0",
        "method": "pdf_named_destination_interval",
        "volume_id": "volume-1",
        "mapping_status": "passed",
        "final_pdf": {
            "path": "delivery/book.pdf",
            "sha256": _sha(pdf),
            "page_count": 2,
        },
        "frozen_inputs": {
            "canonical_ledger_sha256": _sha(canonical),
            "render_plan_sha256": _sha(render_plan),
            "volume_partition_plan_sha256": _sha(partition),
            "render_execution_sha256": _sha(render_execution),
            "template_contract_sha256": _sha(template_contract),
            "presentation_config_sha256": _sha(presentation_config),
        },
        "render_pack": {
            "path": "final_render_pack/manifest.json",
            "sha256": _sha(render_pack),
        },
        "allowed_generated_pages": {
            "region": "strictly_before_first_source_body_destination",
            "roles": ["template_frontmatter"],
            "template_contract_sha256": _sha(template_contract),
            "presentation_config_sha256": _sha(presentation_config),
        },
        "node_intervals": [
            {
                "render_node_id": "node-1",
                "source_block_ids": ["block-1"],
                "source_pages": [1],
                "start_candidate_page": 1,
                "end_candidate_page": 1,
                "start_destination": "luceon-v3-s-node-1",
                "end_destination": "luceon-v3-e-node-1",
            },
            {
                "render_node_id": "node-2",
                "source_block_ids": ["block-2"],
                "source_pages": [2],
                "start_candidate_page": 2,
                "end_candidate_page": 2,
                "start_destination": "luceon-v3-s-node-2",
                "end_destination": "luceon-v3-e-node-2",
            },
        ],
        "pages": [
            {
                "candidate_page": 1,
                "candidate_raster_sha256": "1" * 64,
                "disposition": "source_body",
                "generated_role": None,
                "render_node_ids": ["node-1"],
                "source_block_ids": ["block-1"],
                "source_pages": [1],
            },
            {
                "candidate_page": 2,
                "candidate_raster_sha256": "2" * 64,
                "disposition": "source_body",
                "generated_role": None,
                "render_node_ids": ["node-2"],
                "source_block_ids": ["block-2"],
                "source_pages": [2],
            },
        ],
        "summary": {
            "candidate_pages": 2,
            "mapping_uncertain_pages": [],
            "render_nodes_covered": 2,
        },
    }
    provenance_path = child / "reports/final_pdf_page_provenance.json"
    provenance_path.write_text(json.dumps(provenance))
    manifest = {
        "schema_version": "spec05-delivery-set-manifest/1.2",
        "spec_status": "passed",
        "volume_count": 1,
        "volumes": [
            {
                "volume_id": "volume-1",
                "final_pdf": {
                    "path": "delivery/book.pdf",
                    "sha256": _sha(pdf),
                },
                "page_provenance": {
                    "path": "runs/volume-1/reports/final_pdf_page_provenance.json",
                    "sha256": _sha(provenance_path),
                },
                "render_node_ids": ["node-1", "node-2"],
                "source_block_ids": ["block-1", "block-2"],
            }
        ],
    }
    manifest_path = spec05 / "manifests/delivery_set_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest))
    return root, "b" * 64


def _runtime() -> dict:
    return {
        "models": {
            "vision": {
                "enabled": True,
                "provider": "dashscope",
                "model": "vision-test",
                "dashscope": {
                    "base_url": "https://example.test/v1",
                    "api_key": "secret-for-fake-transport",
                },
            }
        }
    }


def test_dynamic_visual_review_covers_every_candidate_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, release_sha = _release(tmp_path)
    predecessor, predecessor_sha = _predecessor(tmp_path)
    source = tmp_path / "source.pdf"
    _pdf(
        source,
        [
            "Unit one: plants need sunlight and water.",
            "Unit two: forces can push or pull an object.",
        ],
    )
    monkeypatch.setattr(
        "app.workflow_v3.visual_review._pdf_page_raster_sha256",
        lambda _path: ["1" * 64, "2" * 64],
    )
    calls = []

    def run(call, _transport):
        calls.append(call)
        rows = [
            {
                "page_key": row["page_key"],
                "page": row["page"],
                "source_pages": [row["allowed_source_pages"][0]],
                "status": "passed",
                "findings": [],
            }
            for row in call.input_evidence["pages"]
        ]
        raw_response = {
            "id": f"response-{len(calls)}",
            "model": "vision-test",
            "pages": rows,
        }
        return LlmCallResult(
            parsed_result={"pages": rows},
            audit={
                "response_id": f"response-{len(calls)}",
                "parsed_result_sha256": sha256_json({"pages": rows}),
                "request_sha256": "3" * 64,
                "raw_response": raw_response,
                "raw_response_sha256": sha256_json(raw_response),
                "actual_provider": "dashscope",
                "actual_model": "vision-test",
                "usage": {"input_tokens": 10, "output_tokens": 10},
                "latency_ms": 1,
            },
        )

    result = build_full_page_review_inputs(
        job_id="job-1",
        call_scope_id="stable-job-scope-1",
        stage_key="independent_full_page_review",
        stage_version="spec06.v1",
        stage_attempt=1,
        release_id="worker-v3-test",
        release_manifest_sha256=release_sha,
        release_root=release,
        source_pdf=source,
        predecessor_root=predecessor,
        predecessor_sha256=predecessor_sha,
        predecessor_promotion_sha256="c" * 64,
        output_root=tmp_path / "visual",
        runtime_config=_runtime(),
        call_runner=run,
        batch_size=2,
    )

    assert result.reviewed_page_count == 2
    assert result.blocking_findings == 0
    assert len(calls) == 1
    assert calls[0].schema_sha256 == sha256_json(calls[0].output_schema)
    evidence = json.loads(result.evidence_path.read_text())
    assert evidence["source_page_count"] == 2
    assert [row["page"] for row in evidence["volumes"][0]["pages"]] == [1, 2]
    assert all(row["source_evidence"] for row in evidence["volumes"][0]["pages"])
    assert evidence["reviewer"]["response_ids"] == ["response-1"]
    assert evidence["reviewer"]["schema_version"] == VISUAL_PROVIDER_PROTOCOL
    assert evidence["reviewer"]["output_schema_version"] == "1.0.0"
    with tarfile.open(result.render_bundle_path, "r:gz") as archive:
        names = archive.getnames()
    assert "candidate-content-manifest.json" in names
    assert "source/source.pdf" in names
    assert any(
        name.startswith("volumes/01-volume-1-")
        and name.endswith("/final.pdf")
        for name in names
    )


def test_visual_review_rejects_source_page_outside_allowed_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, release_sha = _release(tmp_path)
    predecessor, predecessor_sha = _predecessor(tmp_path)
    source = tmp_path / "source.pdf"
    _pdf(source, ["source page one", "source page two"])
    monkeypatch.setattr(
        "app.workflow_v3.visual_review._pdf_page_raster_sha256",
        lambda _path: ["1" * 64, "2" * 64],
    )

    def run(call, _transport):
        rows = [
            {
                "page_key": row["page_key"],
                "page": row["page"],
                "source_pages": [999],
                "status": "passed",
                "findings": [],
            }
            for row in call.input_evidence["pages"]
        ]
        raw_response = {
            "id": "forged",
            "model": "vision-test",
            "pages": rows,
        }
        return LlmCallResult(
            parsed_result={"pages": rows},
            audit={
                "response_id": "forged",
                "parsed_result_sha256": sha256_json({"pages": rows}),
                "request_sha256": "3" * 64,
                "raw_response": raw_response,
                "raw_response_sha256": sha256_json(raw_response),
                "actual_provider": "dashscope",
                "actual_model": "vision-test",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    with pytest.raises(VisualReviewError, match="source mapping is invalid"):
        build_full_page_review_inputs(
            job_id="job-1",
            call_scope_id="stable-job-scope-1",
            stage_key="independent_full_page_review",
            stage_version="spec06.v1",
            stage_attempt=1,
            release_id="worker-v3-test",
            release_manifest_sha256=release_sha,
            release_root=release,
            source_pdf=source,
            predecessor_root=predecessor,
            predecessor_sha256=predecessor_sha,
            predecessor_promotion_sha256="c" * 64,
            output_root=tmp_path / "visual",
            runtime_config=_runtime(),
            call_runner=run,
        )


def test_visual_review_fails_closed_when_runtime_model_drifts(
    tmp_path: Path,
) -> None:
    release, release_sha = _release(tmp_path)
    predecessor, predecessor_sha = _predecessor(tmp_path)
    source = tmp_path / "source.pdf"
    _pdf(source, ["source"])
    runtime = _runtime()
    runtime["models"]["vision"]["model"] = "other-model"
    with pytest.raises(VisualReviewError, match="differs"):
        build_full_page_review_inputs(
            job_id="job-1",
            call_scope_id="stable-job-scope-1",
            stage_key="independent_full_page_review",
            stage_version="spec06.v1",
            stage_attempt=1,
            release_id="worker-v3-test",
            release_manifest_sha256=release_sha,
            release_root=release,
            source_pdf=source,
            predecessor_root=predecessor,
            predecessor_sha256=predecessor_sha,
            predecessor_promotion_sha256="c" * 64,
            output_root=tmp_path / "visual",
            runtime_config=runtime,
            call_runner=lambda _call, _transport: (_ for _ in ()).throw(
                AssertionError("provider must not run")
            ),
        )


@pytest.mark.parametrize(
    ("policy_field", "policy_value", "message"),
    [
        ("max_source_pages", 1, "source PDF exceeds"),
        ("max_candidate_pages", 1, "candidate PDFs exceed"),
        ("max_render_bundle_bytes", 1, "byte budget"),
    ],
)
def test_visual_review_resource_gates_run_before_provider(
    tmp_path: Path,
    policy_field: str,
    policy_value: int,
    message: str,
) -> None:
    release, release_sha = _release(tmp_path)
    manifest_path = release / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["model_policy"]["visual_review"][policy_field] = policy_value
    manifest_path.write_text(json.dumps(manifest))
    predecessor, predecessor_sha = _predecessor(tmp_path)
    source = tmp_path / "source.pdf"
    _pdf(source, ["source page one", "source page two"])

    with pytest.raises(VisualReviewError, match=message):
        build_full_page_review_inputs(
            job_id="job-1",
            call_scope_id="stable-job-scope-1",
            stage_key="independent_full_page_review",
            stage_version="spec06.v1",
            stage_attempt=1,
            release_id="worker-v3-test",
            release_manifest_sha256=release_sha,
            release_root=release,
            source_pdf=source,
            predecessor_root=predecessor,
            predecessor_sha256=predecessor_sha,
            predecessor_promotion_sha256="c" * 64,
            output_root=tmp_path / "visual",
            runtime_config=_runtime(),
            call_runner=lambda _call, _transport: (_ for _ in ()).throw(
                AssertionError("resource gate must run before provider")
            ),
        )
