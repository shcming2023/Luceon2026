from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.workflow_v3.release import build_release_archive
from app.workflow_v3.release_recipe import (
    ENTRYPOINT_PROTOCOL,
    EXECUTABLE_BASELINE_HASH_ALGORITHM,
    QUALIFICATION_EVIDENCE_SCHEMA_VERSIONS,
    REQUIRED_SKILLS,
    REQUIRED_STAGES,
    ReleaseRecipeError,
    assemble_release_source,
    audit_release_recipe,
    verify_release_recipe,
)
from app.workflow_v3.pricing import sha256_json as pricing_sha256_json


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _priced_model_policy() -> dict:
    snapshot = {
        "schema_version": "luceon.worker-v3-pricing-snapshot/v1",
        "snapshot_id": "release-recipe-fixture",
        "retrieved_at": "2026-07-26",
        "currency": "CNY",
        "micro_unit_exponent": 6,
        "token_rate_denominator": 1_000_000,
        "rounding": "ceil_each_component_to_micro_unit",
        "sources": [
            {
                "provider": "fixture",
                "url": "https://example.test/pricing",
                "retrieved_at": "2026-07-26",
            }
        ],
        "models": [
            {
                "provider": "fixture",
                "model": "fixture-model-v1",
                "service_region": "fixture-region",
                "billing_mode": "realtime",
                "inference_mode": "bounded-json",
                "promotional_rates_excluded": True,
                "cache_pricing_policy": "provider_breakdown_else_all_miss",
                "tiers": [
                    {
                        "id": "all",
                        "input_tokens_min_exclusive": 0,
                        "input_tokens_max_inclusive": 1_000_000,
                        "input_cache_hit_micro_per_million": 1,
                        "input_cache_miss_micro_per_million": 2,
                        "output_micro_per_million": 3,
                    }
                ],
            }
        ],
    }
    return {
        "mode": "release-scoped-schema-bounded-json",
        "network_calls_allowed": True,
        "provider": "fixture",
        "model": "fixture-model-v1",
        "pricing_snapshot": snapshot,
        "pricing_snapshot_sha256": pricing_sha256_json(snapshot),
    }


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _tree_hash(root: Path) -> str:
    records = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if any(
            part.startswith(".")
            or part in {"__pycache__", ".pytest_cache", ".DS_Store", "Thumbs.db"}
            or part.endswith((".pyc", ".pyo", ".swp", ".swo", "~"))
            for part in Path(relative).parts
        ):
            continue
        payload = path.read_bytes()
        records.append({"path": relative, "bytes": len(payload), "sha256": _sha(payload)})
    digest = hashlib.sha256()
    for record in records:
        digest.update(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _zip_selection_hash(path: Path, prefix: str) -> str:
    records = []
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if name.endswith("/") or not name.startswith(prefix):
                continue
            relative = name[len(prefix) :]
            payload = archive.read(name)
            records.append({"path": relative, "bytes": len(payload), "sha256": _sha(payload)})
    digest = hashlib.sha256()
    for record in records:
        digest.update(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _fixture_recipe(tmp_path: Path) -> tuple[dict, Path]:
    root = tmp_path / "inputs"
    skills_root = root / "skills"
    for skill in REQUIRED_SKILLS:
        _write(skills_root / skill / "SKILL.md", f"# {skill}\n".encode())
        _write(skills_root / skill / "scripts/tool.py", b"print('tool')\n")
    _write(skills_root / REQUIRED_SKILLS[0] / "__pycache__/ignored.pyc", b"noise")
    _write(skills_root / REQUIRED_SKILLS[0] / ".DS_Store", b"noise")

    specs_root = root / "ebc/specs"
    for number in range(1, 7):
        _write(specs_root / f"0{number}-spec.md", f"# spec {number}\n".encode())
    schemas_root = root / "ebc/schemas"
    _write(schemas_root / "contract.schema.json", b'{"type":"object"}\n')
    _write(schemas_root / "prompt.schema.json", b'{"type":"object"}\n')

    prompt_path = root / "release-inputs/prompts/semantic-v1.txt"
    capability_path = root / "release-inputs/template-capabilities.json"
    requirements_path = root / "release-inputs/requirements.lock"
    sbom_path = root / "release-inputs/sbom.json"
    fonts_path = root / "release-inputs/fonts.json"
    tex_path = root / "release-inputs/tex.json"
    poppler_path = root / "release-inputs/poppler.json"
    attestation_path = root / "release-inputs/attestation.json"
    provenance_path = root / "release-inputs/historical-dependency-lock.json"
    for path, payload in (
        (prompt_path, b"Return schema-bounded JSON only.\n"),
        (capability_path, b'{"schema_version":"template-capabilities/1"}\n'),
        (requirements_path, b"pypdf==6.10.0\n"),
        (sbom_path, b'{"bomFormat":"CycloneDX","components":[]}\n'),
        (fonts_path, b'{"fonts":[]}\n'),
        (tex_path, b'{"xelatex":"TeX Live 2025"}\n'),
        (poppler_path, b'{"pdfinfo":"26.05.0"}\n'),
        (attestation_path, b'{"verified":true}\n'),
        (
            provenance_path,
            b'{"historical_path":"/Users/example/.codex/skills/legacy"}\n',
        ),
    ):
        _write(path, payload)

    tools_zip = root / "ebc/stable.zip"
    tools_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(tools_zip, "w") as archive:
        for stage in REQUIRED_STAGES:
            for execution_role in ("producer", "evaluator"):
                payload = (
                    "#!/usr/bin/env python3\n"
                    f'WORKER_V3_ENTRYPOINT_PROTOCOL = "{ENTRYPOINT_PROTOCOL}"\n'
                    f'WORKER_V3_STAGE = "{stage}"\n'
                    f'WORKER_V3_ENTRYPOINT_ROLE = "{execution_role}"\n'
                    'REQUEST_FLAG = "--request"\n'
                    'RESULT_FLAG = "--result"\n'
                ).encode()
                archive.writestr(
                    f"workspace/scripts/{stage}.{execution_role}.py",
                    payload,
                )

    template = root / "ebc/template.zip"
    with zipfile.ZipFile(template, "w") as archive:
        archive.writestr("main.tex", "\\documentclass{elegantbook}\n")
        archive.writestr("elegantbook.cls", "\\NeedsTeXFormat{LaTeX2e}\n")
        archive.writestr("figure/logo.jpg", b"logo")
        archive.writestr("figure/cover.jpg", b"cover")

    common_qualification = {
        "status": "passed",
        "qualified_at": "2026-07-26T00:00:00Z",
        "identity": {
            "runner": "fixture-runner",
            "verifier": "fixture-independent-verifier",
            "run_id": "fixture-qualification-run",
        },
        "release_binding": {
            "release_id": "worker-v3-fixture-rc1",
            "release_source_git_sha": "a" * 40,
            "executable_baseline_hash_algorithm": (
                EXECUTABLE_BASELINE_HASH_ALGORITHM
            ),
            "executable_baseline_sha256": None,
            "container_image_digest": f"sha256:{'b' * 64}",
        },
    }
    qualification_documents = {
        "visual_full_page_provider": {
            **common_qualification,
            "schema_version": QUALIFICATION_EVIDENCE_SCHEMA_VERSIONS[
                "visual_full_page_provider"
            ],
            "qualification_type": "visual_full_page_provider",
            "evidence_id": "fixture-visual-provider",
            "proof": {
                "provider": {
                    "name": "fixture-vision",
                    "model": "fixture-vision-1",
                    "endpoint_origin_sha256": "1" * 64,
                },
                "reviewer": {
                    "entrypoint_id": "worker-v3.independent_full_page_review.evaluate",
                    "prompt_sha256": "2" * 64,
                    "output_schema_sha256": "3" * 64,
                },
                "coverage": {
                    "mode": "all_pages",
                    "source_page_count": 2,
                    "candidate_page_count": 2,
                    "reviewed_source_page_count": 2,
                    "reviewed_candidate_page_count": 2,
                    "failed_page_count": 0,
                },
                "result": {
                    "decision": "passed",
                    "schema_valid": True,
                    "raw_response_hashes_bound": True,
                },
            },
        },
        "spec05_final_image_real_material": {
            **common_qualification,
            "schema_version": QUALIFICATION_EVIDENCE_SCHEMA_VERSIONS[
                "spec05_final_image_real_material"
            ],
            "qualification_type": "spec05_final_image_real_material",
            "evidence_id": "fixture-spec05-final-image",
            "proof": {
                "material": {
                    "material_identity_sha256": "4" * 64,
                    "source_pdf_sha256": "5" * 64,
                    "popo_manifest_sha256": "6" * 64,
                },
                "execution": {
                    "entrypoint_id": "worker-v3.deterministic_elegantbook.produce",
                    "exact_final_image": True,
                    "unchanged_code": True,
                },
                "delivery": {
                    "zip_sha256": "7" * 64,
                    "pdf_sha256": "8" * 64,
                    "page_count": 2,
                    "xelatex_status": "passed",
                    "overleaf_status": "passed",
                    "template_archive_sha256": _sha(template.read_bytes()),
                },
                "result": {
                    "decision": "passed",
                    "spec05_gates_passed": True,
                },
            },
        },
    }
    qualification_paths: dict[str, Path] = {}
    for qualification_type, document in qualification_documents.items():
        path = (
            root
            / "release-inputs"
            / "qualification"
            / f"{qualification_type}.json"
        )
        _write(
            path,
            (json.dumps(document, sort_keys=True) + "\n").encode("utf-8"),
        )
        qualification_paths[qualification_type] = path

    sources = [
        {
            "id": "ebc-stable-entrypoints",
            "kind": "zip_tree",
            "source_role": "executable_baseline",
            "root": "fixture",
            "path": "ebc/stable.zip",
            "destination": "scripts/stages",
            "member_prefix": "workspace/scripts",
            "include": ["*.py"],
            "expected_sha256": _sha(tools_zip.read_bytes()),
            "expected_tree_sha256": _zip_selection_hash(tools_zip, "workspace/scripts/"),
        },
        {
            "id": "ebc-current-specs",
            "kind": "tree",
            "root": "fixture",
            "path": "ebc/specs",
            "destination": "contracts/ebc",
            "expected_tree_sha256": _tree_hash(specs_root),
        },
        {
            "id": "historical-dependency-lock-provenance",
            "kind": "file",
            "source_role": "provenance_only",
            "root": "fixture",
            "path": "release-inputs/historical-dependency-lock.json",
            "destination": "references/provenance/historical-dependency-lock.json",
            "expected_sha256": _sha(provenance_path.read_bytes()),
        },
        {
            "id": "ebc-current-schemas",
            "kind": "tree",
            "root": "fixture",
            "path": "ebc/schemas",
            "destination": "schemas/ebc",
            "expected_tree_sha256": _tree_hash(schemas_root),
        },
    ]
    for skill in REQUIRED_SKILLS:
        sources.append(
            {
                "id": f"skill-{skill}",
                "kind": "tree",
                "source_role": "executable_baseline",
                "root": "fixture",
                "path": f"skills/{skill}",
                "destination": f"skills/{skill}",
                "expected_tree_sha256": _tree_hash(skills_root / skill),
            }
        )
    for source_id, relative, destination in (
        ("prompt", "release-inputs/prompts/semantic-v1.txt", "prompts/semantic-v1.txt"),
        (
            "template-capabilities",
            "release-inputs/template-capabilities.json",
            "references/template-capabilities.json",
        ),
        ("requirements", "release-inputs/requirements.lock", "runtime/requirements.lock"),
        ("sbom", "release-inputs/sbom.json", "runtime/sbom.json"),
        ("fonts", "release-inputs/fonts.json", "runtime/fonts.json"),
        ("tex", "release-inputs/tex.json", "runtime/tex.json"),
        ("poppler", "release-inputs/poppler.json", "runtime/poppler.json"),
        ("attestation", "release-inputs/attestation.json", "runtime/attestation.json"),
        ("template", "ebc/template.zip", "templates/elegantbook.zip"),
    ):
        source_path = root / relative
        sources.append(
            {
                "id": source_id,
                "kind": "file",
                "root": "fixture",
                "path": relative,
                "destination": destination,
                "expected_sha256": _sha(source_path.read_bytes()),
            }
        )
    for qualification_type, path in qualification_paths.items():
        relative = path.relative_to(root).as_posix()
        sources.append(
            {
                "id": f"qualification-{qualification_type}",
                "kind": "file",
                "source_role": "runtime_evidence",
                "root": "fixture",
                "path": relative,
                "destination": (
                    f"evals/qualification/{qualification_type}.json"
                ),
                "expected_sha256": _sha(path.read_bytes()),
            }
        )
    recipe = {
        "schema_version": "luceon.worker-v3-release-recipe/v1",
        "roots": {"fixture": str(root)},
        "release": {
            "release_id": "worker-v3-fixture-rc1",
            "version": "3.0.0-rc.1",
            "channel": "rc",
            "requested_status": "rc",
            "created_at": "2026-07-26T00:00:00Z",
            "source": {"git_sha": "a" * 40, "git_tag": "worker-v3-fixture-rc1", "dirty": False},
            "stable_eligible": False,
        },
        "sources": sources,
        "executable_baseline": {
            "policy": "sole-authority",
            "source_ids": [
                "ebc-stable-entrypoints",
                *[f"skill-{skill}" for skill in REQUIRED_SKILLS],
            ],
        },
        "identities": {
            "skills": [
                {
                    "id": skill,
                    "version": "fixture-1",
                    "path": f"skills/{skill}/SKILL.md",
                }
                for skill in REQUIRED_SKILLS
            ],
            "specs": [
                {
                    "id": f"spec-0{number}",
                    "version": "fixture-1",
                    "path": f"contracts/ebc/0{number}-spec.md",
                }
                for number in range(1, 7)
            ],
            "schemas": [
                {
                    "id": "contract",
                    "version": "fixture-1",
                    "path": "schemas/ebc/contract.schema.json",
                },
                {
                    "id": "prompt-output",
                    "version": "fixture-1",
                    "path": "schemas/ebc/prompt.schema.json",
                },
            ],
        },
        "stage_entrypoints": [
            {
                "stage": stage,
                "producer": {
                    "id": f"worker-v3.{stage}.produce",
                    "classification": "formal",
                    "tool_path": f"scripts/stages/{stage}.producer.py",
                    "timeout_seconds": 60,
                },
                "evaluator": {
                    "id": f"worker-v3.{stage}.evaluate",
                    "classification": "formal",
                    "tool_path": f"scripts/stages/{stage}.evaluator.py",
                    "timeout_seconds": 60,
                },
            }
            for stage in REQUIRED_STAGES
        ],
        "prompts": [
            {
                "id": "semantic",
                "version": "1",
                "path": "prompts/semantic-v1.txt",
                "output_schema": "schemas/ebc/prompt.schema.json",
            }
        ],
        "model_policy": {"mode": "bounded-json"},
        "template": {
            "id": "fixture-template",
            "version": "1",
            "archive_path": "templates/elegantbook.zip",
            "main_member": "main.tex",
            "class_member": "elegantbook.cls",
            "fixed_asset_members": ["figure/logo.jpg", "figure/cover.jpg"],
            "capabilities_path": "references/template-capabilities.json",
        },
        "runtime": {
            "python": "CPython 3.13",
            "application_dependencies_path": "runtime/requirements.lock",
            "system_tools": {"xelatex": "TeX Live 2025"},
            "fonts_identity_path": "runtime/fonts.json",
            "tex_identity_path": "runtime/tex.json",
            "poppler_identity_path": "runtime/poppler.json",
            "container_image_digest": f"sha256:{'b' * 64}",
            "sbom_path": "runtime/sbom.json",
            "attestations": ["runtime/attestation.json"],
        },
        "dynamic_closure": {"modules": ["json"], "resources": []},
        "qualification_evidence": {
            "visual_full_page_provider": {
                "required": True,
                "source_id": "qualification-visual_full_page_provider",
            },
            "spec05_final_image_real_material": {
                "required": True,
                "source_id": "qualification-spec05_final_image_real_material",
            },
        },
        "known_gaps": [],
        "compatibility": {
            "v2_3": "isolated parallel lane",
            "rollback": "disable Worker V3 admission",
        },
    }
    return recipe, root


def test_hash_bound_recipe_assembles_standard_release_source_and_builds(tmp_path):
    recipe, _ = _fixture_recipe(tmp_path)
    audit = audit_release_recipe(recipe)

    assert audit.status == "rc"
    assert audit.known_gaps == ()
    assert all(row["static_contract_verified"] for row in audit.entrypoint_evidence)
    output = tmp_path / "assembled"
    result = assemble_release_source(audit, output)

    assert result["status"] == "rc"
    assert not (output / "skills" / REQUIRED_SKILLS[0] / "__pycache__").exists()
    assert not (output / "skills" / REQUIRED_SKILLS[0] / ".DS_Store").exists()
    source_report = json.loads((output / "references/source-qualification.json").read_text())
    assert source_report["host_paths_omitted"] is True
    assert source_report["runtime_host_path_references_absent"] is True
    assert source_report["historical_host_paths_confined_to_provenance"] is True
    assert str(tmp_path) not in (output / "references/source-qualification.json").read_text()
    assert source_report["executable_baseline"]["policy"] == "sole-authority"
    assert (
        source_report["executable_baseline"]["hash_algorithm"]
        == EXECUTABLE_BASELINE_HASH_ALGORITHM
    )
    assert len(source_report["executable_baseline"]["sha256"]) == 64
    assert source_report["executable_baseline"]["provenance_only_source_ids"] == [
        "historical-dependency-lock-provenance"
    ]
    manifest = json.loads((output / "release-manifest.json").read_text())
    assert manifest["status"] == "rc"
    assert manifest["eligibility"]["rc_eligible"] is True
    assert len(manifest["entrypoints"]["formal"]) == 24
    assert {
        definition["execution_role"]
        for definition in manifest["entrypoints"]["definitions"].values()
    } == {"producer", "evaluator"}
    assert [
        row["qualification_type"] for row in manifest["evidence"]["eval"]
    ] == ["visual_full_page_provider"]
    assert [
        row["qualification_type"] for row in manifest["evidence"]["uat"]
    ] == ["spec05_final_image_real_material"]
    assert manifest["evidence"]["contract"] == []
    for category in ("eval", "uat"):
        row = manifest["evidence"][category][0]
        assert row["status"] == "passed"
        assert row["path"].startswith("evals/qualification/")
        assert len(row["sha256"]) == 64

    built = build_release_archive(output, tmp_path / "worker-v3.tar.gz")
    assert built["release_id"] == "worker-v3-fixture-rc1"


def test_deleting_known_gaps_cannot_promote_missing_required_qualification(tmp_path):
    recipe, _ = _fixture_recipe(tmp_path)
    recipe["known_gaps"] = []
    recipe["qualification_evidence"]["visual_full_page_provider"][
        "source_id"
    ] = None

    audit = audit_release_recipe(recipe)

    assert audit.status == "incomplete"
    assert any(
        row["code"] == "full_page_review_evidence_provider_unqualified"
        for row in audit.known_gaps
    )
    assert audit.recipe["_generated_manifest"]["evidence"]["eval"] == []


def test_expert_qualification_type_is_rejected_from_production_recipe(tmp_path):
    recipe, _ = _fixture_recipe(tmp_path)
    recipe["qualification_evidence"]["expert_live_broker"] = {
        "required": True,
        "source_id": None,
    }

    with pytest.raises(
        ReleaseRecipeError,
        match="qualification_evidence contains unknown types",
    ):
        audit_release_recipe(recipe)


def test_expert_model_policy_is_rejected_from_production_recipe(tmp_path):
    recipe, _ = _fixture_recipe(tmp_path)
    recipe["model_policy"]["expert_capability"] = {"enabled": True}

    with pytest.raises(
        ReleaseRecipeError,
        match="production-forbidden Codex Expert keys",
    ):
        audit_release_recipe(recipe)


def test_qualification_container_mismatch_is_rejected(tmp_path):
    recipe, root = _fixture_recipe(tmp_path)
    path = (
        root
        / "release-inputs/qualification/visual_full_page_provider.json"
    )
    document = json.loads(path.read_text())
    document["release_binding"]["container_image_digest"] = f"sha256:{'c' * 64}"
    path.write_text(json.dumps(document), encoding="utf-8")
    source = next(
        row
        for row in recipe["sources"]
        if row["id"] == "qualification-visual_full_page_provider"
    )
    source["expected_sha256"] = _sha(path.read_bytes())

    with pytest.raises(ReleaseRecipeError, match="container image mismatch"):
        audit_release_recipe(recipe)


def test_shallow_qualification_json_is_rejected_even_when_hash_bound(tmp_path):
    recipe, root = _fixture_recipe(tmp_path)
    path = (
        root
        / "release-inputs/qualification/spec05_final_image_real_material.json"
    )
    document = json.loads(path.read_text())
    del document["proof"]["delivery"]["overleaf_status"]
    path.write_text(json.dumps(document), encoding="utf-8")
    source = next(
        row
        for row in recipe["sources"]
        if row["id"] == "qualification-spec05_final_image_real_material"
    )
    source["expected_sha256"] = _sha(path.read_bytes())

    with pytest.raises(ReleaseRecipeError, match="keys mismatch"):
        audit_release_recipe(recipe)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda document: document.update({"status": "failed"}),
            "status must be 'passed'",
        ),
        (
            lambda document: document.update({"schema_version": "forged/v1"}),
            "schema_version mismatch",
        ),
        (
            lambda document: document["identity"].pop("verifier"),
            "keys mismatch",
        ),
        (
            lambda document: document.update(
                {"qualified_at": "2026-07-25T23:59:00"}
            ),
            "explicit timezone",
        ),
    ],
)
def test_qualification_status_schema_identity_and_time_are_strict(
    tmp_path,
    mutation,
    error,
):
    recipe, root = _fixture_recipe(tmp_path)
    path = (
        root
        / "release-inputs/qualification/visual_full_page_provider.json"
    )
    document = json.loads(path.read_text())
    mutation(document)
    path.write_text(json.dumps(document), encoding="utf-8")
    source = next(
        row
        for row in recipe["sources"]
        if row["id"] == "qualification-visual_full_page_provider"
    )
    source["expected_sha256"] = _sha(path.read_bytes())

    with pytest.raises(ReleaseRecipeError, match=error):
        audit_release_recipe(recipe)


def test_dirty_release_qualification_must_bind_exact_executable_baseline(tmp_path):
    recipe, root = _fixture_recipe(tmp_path)
    clean_audit = audit_release_recipe(recipe)
    source_report_file = next(
        item
        for item in clean_audit.planned_files
        if item.destination == "references/source-qualification.json"
    )
    baseline_sha256 = json.loads(source_report_file.payload)[
        "executable_baseline"
    ]["sha256"]
    for qualification_type in (
        "visual_full_page_provider",
        "spec05_final_image_real_material",
    ):
        path = (
            root
            / "release-inputs"
            / "qualification"
            / f"{qualification_type}.json"
        )
        document = json.loads(path.read_text())
        document["release_binding"][
            "executable_baseline_sha256"
        ] = baseline_sha256
        path.write_text(json.dumps(document), encoding="utf-8")
        source = next(
            row
            for row in recipe["sources"]
            if row["id"] == f"qualification-{qualification_type}"
        )
        source["expected_sha256"] = _sha(path.read_bytes())
    recipe["release"]["source"]["dirty"] = True

    audit = audit_release_recipe(recipe)

    assert audit.status == "rc"


def test_forged_executable_baseline_binding_is_rejected(tmp_path):
    recipe, root = _fixture_recipe(tmp_path)
    recipe["release"]["source"]["dirty"] = True
    path = (
        root
        / "release-inputs/qualification/visual_full_page_provider.json"
    )
    document = json.loads(path.read_text())
    document["release_binding"]["executable_baseline_sha256"] = "f" * 64
    path.write_text(json.dumps(document), encoding="utf-8")
    source = next(
        row
        for row in recipe["sources"]
        if row["id"] == "qualification-visual_full_page_provider"
    )
    source["expected_sha256"] = _sha(path.read_bytes())

    with pytest.raises(ReleaseRecipeError, match="executable baseline mismatch"):
        audit_release_recipe(recipe)


def test_formal_label_without_protocol_is_downgraded_and_release_is_incomplete(tmp_path):
    recipe, root = _fixture_recipe(tmp_path)
    stable = root / "ebc/stable.zip"
    with zipfile.ZipFile(stable, "w") as archive:
        for stage in REQUIRED_STAGES:
            for execution_role in ("producer", "evaluator"):
                archive.writestr(
                    f"workspace/scripts/{stage}.{execution_role}.py",
                    "#!/usr/bin/env python3\n",
                )
    source = recipe["sources"][0]
    source["expected_sha256"] = _sha(stable.read_bytes())
    source["expected_tree_sha256"] = _zip_selection_hash(stable, "workspace/scripts/")

    audit = audit_release_recipe(recipe)

    assert audit.status == "incomplete"
    assert len(audit.recipe["_generated_manifest"]["entrypoints"]["formal"]) == 0
    assert len(audit.recipe["_generated_manifest"]["entrypoints"]["prohibited"]) == 24
    assert {gap["code"] for gap in audit.known_gaps} == {
        "formal_producer_entrypoint_contract_unverified",
        "formal_evaluator_entrypoint_contract_unverified",
    }


def test_missing_independent_evaluator_keeps_recipe_incomplete(tmp_path):
    recipe, _ = _fixture_recipe(tmp_path)
    del recipe["stage_entrypoints"][0]["evaluator"]

    audit = audit_release_recipe(recipe)

    assert audit.status == "incomplete"
    assert any(
        gap["code"] == "formal_evaluator_entrypoint_missing"
        and gap["stage"] == REQUIRED_STAGES[0]
        for gap in audit.known_gaps
    )
    manifest = audit.recipe["_generated_manifest"]
    stage_definitions = [
        definition
        for definition in manifest["entrypoints"]["definitions"].values()
        if definition["stage"] == REQUIRED_STAGES[0]
    ]
    assert [definition["execution_role"] for definition in stage_definitions] == [
        "producer"
    ]


def test_source_hash_drift_fails_before_any_output_is_written(tmp_path):
    recipe, root = _fixture_recipe(tmp_path)
    recipe["sources"][0]["expected_sha256"] = "0" * 64
    output = tmp_path / "assembled"

    with pytest.raises(ReleaseRecipeError, match="SHA-256 mismatch"):
        audit = audit_release_recipe(recipe)
        assemble_release_source(audit, output)

    assert not output.exists()
    assert (root / "ebc/stable.zip").exists()


def test_symlink_in_source_tree_is_rejected_even_when_it_would_be_filtered(tmp_path):
    recipe, root = _fixture_recipe(tmp_path)
    skill = root / "skills" / REQUIRED_SKILLS[0]
    link = skill / ".hidden-link"
    try:
        link.symlink_to(skill / "SKILL.md")
    except OSError:
        pytest.skip("fixture filesystem does not permit symlinks")

    with pytest.raises(ReleaseRecipeError, match="symlinks are forbidden"):
        audit_release_recipe(recipe)


def test_verify_only_cli_writes_no_release_directory(tmp_path):
    recipe, _ = _fixture_recipe(tmp_path)
    recipe_path = tmp_path / "recipe.json"
    recipe_path.write_text(json.dumps(recipe), encoding="utf-8")
    output = tmp_path / "must-not-exist"
    script = Path(__file__).parents[1] / "scripts/assemble_worker_v3_release_source.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--recipe", str(recipe_path), "--verify-only"],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["status"] == "rc"
    assert result["output_written"] is False
    assert not output.exists()


def test_recipe_relative_roots_are_portable_and_require_recipe_context(tmp_path):
    recipe, root = _fixture_recipe(tmp_path)
    recipe_dir = tmp_path / "release" / "worker-v3"
    recipe_dir.mkdir(parents=True)
    recipe["roots"] = {
        "fixture": {
            "relative_to_recipe": "../../inputs"
        }
    }
    recipe_path = recipe_dir / "recipe.json"
    recipe_path.write_text(json.dumps(recipe), encoding="utf-8")

    audit = verify_release_recipe(recipe_path)

    assert audit.status == "rc"
    assert audit.source_roots == (root.resolve(),)
    with pytest.raises(ReleaseRecipeError, match="loaded recipe file"):
        audit_release_recipe(recipe)


def test_assembly_refuses_to_write_inside_any_source_root(tmp_path):
    recipe, root = _fixture_recipe(tmp_path)
    audit = audit_release_recipe(recipe)

    with pytest.raises(ReleaseRecipeError, match="inside a source root"):
        assemble_release_source(audit, root / "assembled")


def test_provenance_only_source_cannot_be_runtime_or_executable(tmp_path):
    recipe, _ = _fixture_recipe(tmp_path)
    provenance = next(
        row
        for row in recipe["sources"]
        if row["id"] == "historical-dependency-lock-provenance"
    )
    provenance["destination"] = "runtime/historical-dependency-lock.json"

    with pytest.raises(ReleaseRecipeError, match="references/provenance"):
        audit_release_recipe(recipe)

    provenance["destination"] = (
        "references/provenance/historical-dependency-lock.json"
    )
    provenance["executable"] = True
    with pytest.raises(ReleaseRecipeError, match="cannot be executable"):
        audit_release_recipe(recipe)


def test_execution_surface_rejects_sources_outside_declared_baseline(tmp_path):
    recipe, _ = _fixture_recipe(tmp_path)
    source = next(
        row for row in recipe["sources"] if row["id"] == "ebc-stable-entrypoints"
    )
    source["source_role"] = "supporting_evidence"
    recipe["executable_baseline"]["source_ids"].remove("ebc-stable-entrypoints")

    with pytest.raises(
        ReleaseRecipeError,
        match="outside the sole executable baseline",
    ):
        audit_release_recipe(recipe)


def test_provenance_only_source_cannot_satisfy_runtime_attestation(tmp_path):
    recipe, _ = _fixture_recipe(tmp_path)
    recipe["runtime"]["attestations"].append(
        "references/provenance/historical-dependency-lock.json"
    )

    with pytest.raises(
        ReleaseRecipeError,
        match="cannot satisfy runtime identity",
    ):
        audit_release_recipe(recipe)


def test_mutable_host_reference_in_executable_baseline_remains_a_gap(tmp_path):
    recipe, root = _fixture_recipe(tmp_path)
    skill_id = REQUIRED_SKILLS[0]
    skill_root = root / "skills" / skill_id
    _write(
        skill_root / "SKILL.md",
        b"Run /Users/example/.codex/skills/live/scripts/tool.py\n",
    )
    source = next(
        row for row in recipe["sources"] if row["id"] == f"skill-{skill_id}"
    )
    source["expected_tree_sha256"] = _tree_hash(skill_root)

    audit = audit_release_recipe(recipe)

    assert audit.status == "incomplete"
    assert any(
        row["code"] == "mutable_host_path_references_present"
        and f"skills/{skill_id}/SKILL.md" in row["detail"]
        for row in audit.known_gaps
    )


def test_execution_baseline_cannot_open_provenance_payload(tmp_path):
    recipe, root = _fixture_recipe(tmp_path)
    skill_id = REQUIRED_SKILLS[0]
    skill_root = root / "skills" / skill_id
    _write(
        skill_root / "scripts/tool.py",
        b'open("references/provenance/historical-dependency-lock.json")\n',
    )
    source = next(
        row for row in recipe["sources"] if row["id"] == f"skill-{skill_id}"
    )
    source["expected_tree_sha256"] = _tree_hash(skill_root)

    with pytest.raises(
        ReleaseRecipeError,
        match="cannot reference provenance-only payloads",
    ):
        audit_release_recipe(recipe)


def test_pricing_snapshot_is_copied_unchanged_into_generated_manifest(tmp_path):
    recipe, _ = _fixture_recipe(tmp_path)
    recipe["model_policy"] = _priced_model_policy()

    audit = audit_release_recipe(recipe)

    generated = audit.recipe["_generated_manifest"]["model_policy"]
    assert generated["pricing_snapshot_sha256"] == (
        recipe["model_policy"]["pricing_snapshot_sha256"]
    )
    assert generated["pricing_snapshot"] == (
        recipe["model_policy"]["pricing_snapshot"]
    )


def test_pricing_snapshot_hash_drift_blocks_release_recipe(tmp_path):
    recipe, _ = _fixture_recipe(tmp_path)
    recipe["model_policy"] = _priced_model_policy()
    recipe["model_policy"]["pricing_snapshot"]["retrieved_at"] = "2026-07-27"

    with pytest.raises(
        ReleaseRecipeError,
        match="pricing_snapshot_hash_mismatch",
    ):
        audit_release_recipe(recipe)
