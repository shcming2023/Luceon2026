from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from app.workflow_v3.release import (
    REQUIRED_FORMAL_STAGES,
    ReleaseValidationError,
    admit_entrypoint,
    build_release_archive,
    enforce_delivery_limits,
    install_release_archive,
    verify_release_directory,
)


REQUIRED_DIRECTORIES = (
    "skills",
    "contracts",
    "schemas",
    "validators",
    "prompts",
    "scripts",
    "references",
    "templates",
    "evals",
    "runtime",
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write(root: Path, relative: str, payload: bytes, *, executable: bool = False) -> str:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    target.chmod(0o755 if executable else 0o644)
    return _sha(payload)


def _entry(
    classification: str,
    script: str,
    *,
    stage: str = "intake_snapshot",
    execution_role: str = "utility",
) -> dict:
    producer = execution_role == "producer"
    evaluator = execution_role == "evaluator"
    return {
        "classification": classification,
        "execution_role": execution_role,
        "stage": stage,
        "argv": [script, "--input", "request.json"],
        "input_schema": "schemas/input.json",
        "output_schema": "schemas/output.json",
        "permission_envelope": (
            "candidate-only"
            if producer
            else "read-only-evaluator" if evaluator else "diagnostic-readonly"
        ),
        "timeout_seconds": 60,
        "exit_semantics": {
            "0": (
                "candidate_ready"
                if producer
                else "evaluation_ready" if evaluator else "completed"
            ),
            "other": "failed",
        },
    }


def _release_source(tmp_path: Path, *, status: str = "rc") -> Path:
    root = tmp_path / "release-source"
    for directory in REQUIRED_DIRECTORIES:
        (root / directory).mkdir(parents=True, exist_ok=True)
    skill_hash = _write(root, "skills/orchestrator/SKILL.md", b"# orchestrator\n")
    spec_hash = _write(root, "contracts/spec-01.md", b"# spec 01\n")
    input_schema_hash = _write(root, "schemas/input.json", b'{"type":"object"}\n')
    output_schema_hash = _write(root, "schemas/output.json", b'{"type":"object"}\n')
    prompt_schema_hash = _write(root, "schemas/prompt-output.json", b'{"type":"object"}\n')
    prompt_hash = _write(root, "prompts/semantic.txt", b"Return bounded JSON.\n")
    _write(root, "validators/validate.py", b"def validate(value): return value\n")
    _write(root, "references/source.md", b"# reference\n")
    template_hash = _write(root, "templates/elegantbook.zip", b"immutable-template")
    _write(root, "evals/case.json", b"{}\n")
    _write(root, "runtime/sbom.json", b'{"bomFormat":"CycloneDX"}\n')
    _write(root, "runtime/attestation.json", b'{"verified":true}\n')
    scripts = {
        "legacy.run": ("legacy", "scripts/legacy.py"),
        "migration.run": ("migration", "scripts/migration.py"),
        "diagnostic.run": ("diagnostic", "scripts/diagnostic.py"),
        "prohibited.run": ("prohibited", "scripts/prohibited.py"),
    }
    for _, script in scripts.values():
        _write(root, script, b"#!/usr/bin/env python3\n", executable=True)
    _write(root, "scripts/produce.py", b"#!/usr/bin/env python3\n", executable=True)
    _write(root, "scripts/evaluate.py", b"#!/usr/bin/env python3\n", executable=True)
    formal_ids: list[str] = []
    formal_definitions: dict[str, dict] = {}
    for stage in REQUIRED_FORMAL_STAGES:
        producer_id = f"formal.{stage}.produce"
        evaluator_id = f"formal.{stage}.evaluate"
        formal_ids.extend((producer_id, evaluator_id))
        formal_definitions[producer_id] = _entry(
            "formal",
            "scripts/produce.py",
            stage=stage,
            execution_role="producer",
        )
        formal_definitions[evaluator_id] = _entry(
            "formal",
            "scripts/evaluate.py",
            stage=stage,
            execution_role="evaluator",
        )
    zeros = "0" * 64
    manifest = {
        "schema_version": "luceon.worker-v3-skill-release/v1",
        "release_id": "worker-v3-test-rc1",
        "version": "3.0.0-rc.1",
        "channel": "rc",
        "status": status,
        "created_at": "2026-07-26T00:00:00Z",
        "source": {"git_sha": "a" * 40, "git_tag": "worker-v3-test-rc1", "dirty": False},
        "eligibility": {"rc_eligible": status == "rc", "stable_eligible": False},
        "tree_hash": {"algorithm": "sha256-canonical-file-records-v1", "sha256": zeros},
        "archive_hash_location": "external-release-registry",
        "files": [],
        "skills": [
            {
                "id": "luceon-popo-to-refined-elegantbook",
                "version": "1.0.0",
                "path": "skills/orchestrator/SKILL.md",
                "sha256": skill_hash,
            }
        ],
        "specs": [
            {
                "id": "spec-01",
                "version": "0.2",
                "path": "contracts/spec-01.md",
                "sha256": spec_hash,
            }
        ],
        "schemas": [
            {"id": "input", "version": "1", "path": "schemas/input.json", "sha256": input_schema_hash},
            {"id": "output", "version": "1", "path": "schemas/output.json", "sha256": output_schema_hash},
            {
                "id": "prompt-output",
                "version": "1",
                "path": "schemas/prompt-output.json",
                "sha256": prompt_schema_hash,
            },
        ],
        "entrypoints": {
            "formal": formal_ids,
            "legacy": ["legacy.run"],
            "migration": ["migration.run"],
            "diagnostic": ["diagnostic.run"],
            "prohibited": ["prohibited.run"],
            "definitions": {
                **formal_definitions,
                **{
                    identifier: _entry(classification, script)
                    for identifier, (classification, script) in scripts.items()
                },
            },
        },
        "dynamic_closure": {
            "modules": ["json"],
            "resources": ["references/source.md", "validators/validate.py"],
        },
        "prompts": [
            {
                "id": "semantic",
                "version": "1",
                "path": "prompts/semantic.txt",
                "sha256": prompt_hash,
                "output_schema": "schemas/prompt-output.json",
            }
        ],
        "model_policy": {"mode": "bounded-json"},
        "template": {
            "id": "approved-elegantbook",
            "version": "2025",
            "archive_path": "templates/elegantbook.zip",
            "archive_sha256": template_hash,
            "tree_sha256": "1" * 64,
            "main_member": "main.tex",
            "main_sha256": "2" * 64,
            "class_member": "elegantbook.cls",
            "class_sha256": "3" * 64,
            "fixed_asset_members": [
                "figure/cover.jpg",
                "figure/logo.jpg",
            ],
            "fixed_assets_sha256": "4" * 64,
            "capabilities_sha256": "5" * 64,
        },
        "runtime": {
            "python": "CPython 3.12",
            "application_dependencies_sha256": "6" * 64,
            "system_tools": {"xelatex": "TeX Live 2025"},
            "fonts_sha256": "7" * 64,
            "tex_sha256": "8" * 64,
            "poppler_sha256": "9" * 64,
            "container_image_digest": f"sha256:{'b' * 64}",
            "sbom_path": "runtime/sbom.json",
            "attestations": ["runtime/attestation.json"],
        },
        "limits": {
            "delivery_zip_bytes_exclusive_max": 50_000_000,
            "raster_bytes_exclusive_max": 1_000_000,
            "file_count_exclusive_max": 2_000,
            "tex_leaf_bytes_exclusive_max": 900_000,
        },
        "evidence": {"unit": [], "contract": [], "eval": [], "uat": [], "known_gaps": []},
        "compatibility": {"v2_3": "isolated", "rollback": "disable V3 admission"},
    }
    (root / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _build(tmp_path: Path, *, status: str = "rc") -> tuple[Path, dict[str, str]]:
    source = _release_source(tmp_path, status=status)
    archive = tmp_path / "release.tar.gz"
    return archive, build_release_archive(source, archive)


def test_build_install_verify_and_admit_only_explicit_class(tmp_path):
    archive, built = _build(tmp_path)
    installed = install_release_archive(
        archive,
        tmp_path / "installed",
        expected_archive_sha256=built["archive_sha256"],
    )

    assert installed.archive_sha256 == built["archive_sha256"]
    assert installed.tree_sha256 == built["tree_sha256"]
    assert verify_release_directory(installed.root).release_id == "worker-v3-test-rc1"
    assert (
        admit_entrypoint(
            installed,
            "formal.intake_snapshot.produce",
            requested_role="producer",
        )["execution_role"]
        == "producer"
    )
    assert (
        admit_entrypoint(
            installed,
            "formal.intake_snapshot.evaluate",
            requested_role="evaluator",
        )["execution_role"]
        == "evaluator"
    )
    assert (
        admit_entrypoint(installed, "diagnostic.run", requested_class="diagnostic")["classification"]
        == "diagnostic"
    )
    with pytest.raises(ReleaseValidationError, match="not requested class"):
        admit_entrypoint(installed, "diagnostic.run")
    with pytest.raises(ReleaseValidationError, match="not requested role"):
        admit_entrypoint(
            installed,
            "formal.intake_snapshot.produce",
            requested_role="evaluator",
        )


def test_deterministic_build_has_identical_archive_hash(tmp_path):
    source = _release_source(tmp_path)
    first = build_release_archive(source, tmp_path / "first.tar.gz")
    second = build_release_archive(source, tmp_path / "second.tar.gz")

    assert first == second
    assert (tmp_path / "first.tar.gz").read_bytes() == (tmp_path / "second.tar.gz").read_bytes()


def test_installed_release_detects_payload_tamper(tmp_path):
    archive, built = _build(tmp_path)
    installed = install_release_archive(
        archive,
        tmp_path / "installed",
        expected_archive_sha256=built["archive_sha256"],
    )
    script = installed.root / "scripts/produce.py"
    script.chmod(0o755)
    script.write_bytes(b"tampered")

    with pytest.raises(ReleaseValidationError, match="mismatch"):
        verify_release_directory(installed.root)


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("skills/orchestrator/SKILL.md", "skills\\[0\\]"),
        ("contracts/spec-01.md", "specs\\[0\\]"),
        ("schemas/input.json", "schemas\\[0\\]"),
        ("prompts/semantic.txt", "prompt hash"),
        ("templates/elegantbook.zip", "template archive hash"),
    ],
)
def test_identity_hash_drift_is_rejected_during_build(tmp_path, path, error):
    source = _release_source(tmp_path)
    (source / path).write_bytes(b"drift")

    with pytest.raises(ReleaseValidationError, match=error):
        build_release_archive(source, tmp_path / "release.tar.gz")


def test_archive_external_hash_mismatch_fails_before_install(tmp_path):
    archive, _ = _build(tmp_path)

    with pytest.raises(ReleaseValidationError, match="archive SHA-256 mismatch"):
        install_release_archive(archive, tmp_path / "installed", expected_archive_sha256="0" * 64)
    assert not (tmp_path / "installed").exists()


def test_unknown_and_prohibited_entrypoints_fail_closed(tmp_path):
    archive, built = _build(tmp_path)
    installed = install_release_archive(
        archive,
        tmp_path / "installed",
        expected_archive_sha256=built["archive_sha256"],
    )

    with pytest.raises(ReleaseValidationError, match="unknown entrypoint"):
        admit_entrypoint(installed, "not-declared")
    with pytest.raises(ReleaseValidationError, match="prohibited"):
        admit_entrypoint(installed, "prohibited.run", requested_class="formal")
    with pytest.raises(ReleaseValidationError, match="not executable"):
        admit_entrypoint(installed, "prohibited.run", requested_class="prohibited")


def test_executable_release_requires_one_producer_and_evaluator_per_stage(tmp_path):
    source = _release_source(tmp_path)
    manifest_path = source / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    missing_id = "formal.intake_snapshot.evaluate"
    manifest["entrypoints"]["formal"].remove(missing_id)
    del manifest["entrypoints"]["definitions"][missing_id]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReleaseValidationError, match="producer and evaluator"):
        build_release_archive(source, tmp_path / "release.tar.gz")


def test_formal_evaluator_cannot_claim_candidate_permissions_or_success(tmp_path):
    source = _release_source(tmp_path)
    manifest_path = source / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    evaluator = manifest["entrypoints"]["definitions"]["formal.intake_snapshot.evaluate"]
    evaluator["permission_envelope"] = "candidate-only"
    evaluator["exit_semantics"]["0"] = "candidate_ready"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReleaseValidationError, match="read-only-evaluator"):
        build_release_archive(source, tmp_path / "release.tar.gz")


def test_formal_producer_and_evaluator_require_separate_executables(tmp_path):
    source = _release_source(tmp_path)
    manifest_path = source / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    evaluator = manifest["entrypoints"]["definitions"]["formal.intake_snapshot.evaluate"]
    evaluator["argv"][0] = "scripts/produce.py"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReleaseValidationError, match="separate executable"):
        build_release_archive(source, tmp_path / "release.tar.gz")


def _unsafe_tar(path: Path, members: list[tarfile.TarInfo]) -> str:
    with tarfile.open(path, "w") as archive:
        for member in members:
            member.mtime = 0
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            payload = io.BytesIO(b"{}") if member.isfile() else None
            if member.isfile():
                member.size = 2
            archive.addfile(member, payload)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_archive_path_escape_is_rejected_without_writing_outside(tmp_path):
    archive = tmp_path / "escape.tar"
    member = tarfile.TarInfo("../escaped")
    member.mode = 0o444
    digest = _unsafe_tar(archive, [member])

    with pytest.raises(ReleaseValidationError, match="not normalized"):
        install_release_archive(archive, tmp_path / "installed", expected_archive_sha256=digest)
    assert not (tmp_path / "escaped").exists()


def test_duplicate_and_link_archive_members_are_rejected(tmp_path):
    duplicate = tmp_path / "duplicate.tar"
    first = tarfile.TarInfo("release-manifest.json")
    first.mode = 0o444
    second = tarfile.TarInfo("release-manifest.json")
    second.mode = 0o444
    duplicate_digest = _unsafe_tar(duplicate, [first, second])
    with pytest.raises(ReleaseValidationError, match="duplicate archive member"):
        install_release_archive(
            duplicate,
            tmp_path / "duplicate-install",
            expected_archive_sha256=duplicate_digest,
        )

    linked = tmp_path / "linked.tar"
    link = tarfile.TarInfo("skills/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "/tmp/target"
    link.mode = 0o555
    link_digest = _unsafe_tar(linked, [link])
    with pytest.raises(ReleaseValidationError, match="links are forbidden"):
        install_release_archive(linked, tmp_path / "link-install", expected_archive_sha256=link_digest)


def test_incomplete_release_builds_for_qualification_but_cannot_install(tmp_path):
    archive, built = _build(tmp_path, status="incomplete")

    with pytest.raises(ReleaseValidationError, match="incomplete is not executable"):
        install_release_archive(
            archive,
            tmp_path / "installed",
            expected_archive_sha256=built["archive_sha256"],
        )


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("ZIP", {"delivery_zip_bytes": 50_000_000}),
        ("file count", {"file_count": 2_000}),
        ("raster", {"raster_bytes": [1_000_000]}),
        ("TeX leaf", {"tex_leaf_bytes": [900_000]}),
    ],
)
def test_delivery_limit_equality_fails(tmp_path, field, kwargs):
    archive, built = _build(tmp_path)
    installed = install_release_archive(
        archive,
        tmp_path / "installed",
        expected_archive_sha256=built["archive_sha256"],
    )
    values = {
        "delivery_zip_bytes": 49_999_999,
        "raster_bytes": [999_999],
        "file_count": 1_999,
        "tex_leaf_bytes": [899_999],
        **kwargs,
    }

    with pytest.raises(ReleaseValidationError, match=field):
        enforce_delivery_limits(installed, **values)


def test_schema_file_is_valid_json():
    schema = Path(__file__).parents[1] / "app/workflow_v3/schemas/release-manifest.schema.json"
    payload = json.loads(schema.read_text())

    assert payload["$schema"].endswith("2020-12/schema")
    assert payload["properties"]["limits"]["properties"]["file_count_exclusive_max"]["const"] == 2_000
