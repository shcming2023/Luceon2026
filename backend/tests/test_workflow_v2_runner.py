import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.material import Material, MaterialOutput
from app.workflow_v2.models import ArtifactVersion, GoldenRegressionCase, ModelCall, QaFinding, RepairAttempt, StageRun, WorkflowBase, WorkflowJob
from app.workflow_v2.artifacts import ArtifactIntegrityError
from app.workflow_v2.runner import _evaluate_core_acceptance, _latex_delivery_integrity_blocker, _manual_review_handoff_blocker, _material_access_allowed, _materialize_frozen_popo_images, _preserve_main_documentclass, _preserve_source_page_markers, _preserve_source_page_markers_from_tex, _project_core_output, _read_xelatex_log, _record_golden_pass, _record_independent_findings, _render_deterministic_precheck, _restore_locked_empty_directories, _write_failure_evidence, run_one_stage
from app.workflow_v2.service import create_workflow_job
from test_workflow_v2_artifacts import FakeMinio
from test_workflow_v2 import make_sessions


def test_material_access_requires_owner_for_normal_jobs():
    job = SimpleNamespace(user_id="2")
    material = SimpleNamespace(user_id="5")

    assert not _material_access_allowed(job, {}, material)
    assert not _material_access_allowed(job, {"golden_set": True}, material)


def test_material_access_allows_recorded_golden_source_owner():
    job = SimpleNamespace(user_id="2")
    material = SimpleNamespace(user_id="5")

    assert _material_access_allowed(
        job,
        {"golden_set": True, "source_material_user_id": "5"},
        material,
    )


def test_core_acceptance_is_decided_only_by_project_gate_evidence(tmp_path):
    validations = {}
    for name in ("canonical", "outline", "semantic", "elegantbook"):
        path = tmp_path / f"{name}.json"
        path.write_text(f'{{"schema":"test.{name}/v1","status":"passed"}}', encoding="utf-8")
        validations[name] = path
    compile_path = tmp_path / "compile.json"
    compile_path.write_text(
        '{"byte_identical_final_passes":true,"pdf_sha256_by_pass":["a","b","b"]}',
        encoding="utf-8",
    )

    report = _evaluate_core_acceptance(
        validations,
        compile_path,
        workflow_version="worker-v2-test",
        output_schema="luceon.worker-core-acceptance/v2",
        artifact_lineage={},
    )

    assert report["status"] == "passed"
    assert report["schema"] == "luceon.worker-core-acceptance/v2"
    assert all(report["gates"].values())
    assert report["bounded_deepseek"]["call_count"] == 0
    assert report["sidecar_used"] is False


def test_core_acceptance_blocks_missing_or_nonreproducible_evidence(tmp_path):
    failed = tmp_path / "outline.json"
    failed.write_text('{"schema":"test","status":"review"}', encoding="utf-8")

    report = _evaluate_core_acceptance(
        {"canonical": tmp_path / "missing.json", "outline": failed},
        tmp_path / "missing-compile.json",
        workflow_version="worker-v2-test",
        output_schema="luceon.worker-core-acceptance/v2",
        artifact_lineage={},
    )

    assert report["status"] == "review"
    assert {row["code"] for row in report["blockers"]} == {
        "canonical_validation_missing",
        "outline_gate_not_passed",
        "elegantbook_compile_not_reproducible",
    }


def test_delivery_integrity_blockers_distinguish_size_from_structure():
    too_large = _latex_delivery_integrity_blocker(
        ArtifactIntegrityError("delivery ZIP exceeds size limit: 50000001 > 50000000 bytes")
    )
    missing = _latex_delivery_integrity_blocker(
        ArtifactIntegrityError("LaTeX delivery is missing required paths: images/")
    )

    assert too_large["code"] == "latex_project_zip_too_large"
    assert missing["code"] == "latex_project_structure_invalid"


def test_core_materialization_restores_only_empty_directories_proven_by_candidate_zip(tmp_path):
    import zipfile

    project = tmp_path / "project"
    project.mkdir()
    with zipfile.ZipFile(project / "latex-project.zip", "w") as archive:
        archive.writestr("images/", b"")
        archive.writestr("main.tex", b"body")

    assert _restore_locked_empty_directories(project) == ["images/"]
    assert (project / "images").is_dir()
    assert not (project / "figure").exists()
    assert _restore_locked_empty_directories(project) == []


def test_frozen_popo_image_inventory_is_restored_without_renaming(monkeypatch, tmp_path):
    manifest = {
        "material_id": "pdf-test",
        "objects": {
            "images": [
                {"bucket": "popo", "object": "run/images/b.jpg", "sha256": hashlib.sha256(b"b").hexdigest()},
                {"bucket": "popo", "object": "run/images/a.jpg", "sha256": hashlib.sha256(b"a").hexdigest()},
            ]
        },
    }
    objects = {
        ("popo", "manifest.json"): json.dumps(manifest).encode(),
        ("popo", "run/images/a.jpg"): b"a",
        ("popo", "run/images/b.jpg"): b"b",
    }
    monkeypatch.setattr("app.workflow_v2.runner.read_object", lambda bucket, name: objects[(bucket, name)])
    material = SimpleNamespace(
        material_id="pdf-test",
        popo_manifest_bucket="popo",
        popo_manifest_object="manifest.json",
    )

    names = _materialize_frozen_popo_images(material, tmp_path)

    assert names == ["a.jpg", "b.jpg"]
    assert sorted(path.name for path in (tmp_path / "images").iterdir()) == names
    assert (tmp_path / "images" / "a.jpg").read_bytes() == b"a"


def test_core_gate_requires_handoff_only_after_reviewable_candidate_exists():
    _old_db, db, material = make_sessions()
    job, _ = create_workflow_job(db, user_id="u1", material=material)
    started = datetime.utcnow()
    outline_stage = db.query(StageRun).filter_by(workflow_job_id=job.id, stage_key="outline_reconstruction").one()
    outline_stage.status = "needs_review"
    outline_stage.finished_at = started
    candidate_stage = db.query(StageRun).filter_by(workflow_job_id=job.id, stage_key="deterministic_elegantbook").one()
    db.add(
        ArtifactVersion(
            workflow_job_id=job.id,
            stage_run_id=candidate_stage.id,
            artifact_kind="elegantbook-candidate",
            bucket="test",
            object_name="candidate/manifest.json",
            object_identity_hash="c" * 64,
            sha256="d" * 64,
            size_bytes=1,
            metadata_json="{}",
            created_at=started + timedelta(seconds=1),
        )
    )
    db.commit()

    assert _manual_review_handoff_blocker(db, job) is None

    review_stage = db.query(StageRun).filter_by(workflow_job_id=job.id, stage_key="bounded_deepseek_polish_qa").one()
    review_stage.status = "needs_review"
    review_stage.finished_at = started + timedelta(seconds=2)
    db.commit()
    blocker = _manual_review_handoff_blocker(db, job)
    assert blocker == {
        "code": "manual_review_handoff_missing",
        "count": 1,
        "detail": "A prior needs_review attempt has no recorded manual handoff.",
    }

    db.add(RepairAttempt(workflow_job_id=job.id, repair_kind="manual_handoff", status="running"))
    db.commit()
    assert _manual_review_handoff_blocker(db, job) is None


def test_new_candidate_supersedes_old_unhanded_review():
    _old_db, db, material = make_sessions()
    job, _ = create_workflow_job(db, user_id="u1", material=material)
    started = datetime.utcnow()
    candidate_stage = db.query(StageRun).filter_by(
        workflow_job_id=job.id,
        stage_key="deterministic_elegantbook",
    ).one()
    db.add(
        ArtifactVersion(
            workflow_job_id=job.id,
            stage_run_id=candidate_stage.id,
            artifact_kind="elegantbook-candidate",
            bucket="test",
            object_name="candidate/old/manifest.json",
            object_identity_hash="a" * 64,
            sha256="b" * 64,
            size_bytes=1,
            metadata_json="{}",
            created_at=started,
        )
    )
    review_stage = db.query(StageRun).filter_by(
        workflow_job_id=job.id,
        stage_key="bounded_deepseek_polish_qa",
    ).one()
    review_stage.status = "needs_review"
    review_stage.finished_at = started + timedelta(seconds=1)
    db.add(
        ArtifactVersion(
            workflow_job_id=job.id,
            stage_run_id=candidate_stage.id,
            artifact_kind="elegantbook-candidate",
            bucket="test",
            object_name="candidate/new/manifest.json",
            object_identity_hash="c" * 64,
            sha256="d" * 64,
            size_bytes=1,
            metadata_json="{}",
            created_at=started + timedelta(seconds=2),
        )
    )
    db.commit()

    assert _manual_review_handoff_blocker(db, job) is None


def test_core_output_projection_is_idempotent_and_preserves_history(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    material = Material(
        user_id="u1", material_id="pdf-projection", title="Projection", filename="projection.pdf", source_type="uploaded",
        stage_status="latex_done", pipeline_status="idle", input_bucket="input", input_object="projection.pdf",
        popo_manifest_bucket="popo", popo_manifest_object="popo/manifest.json", popo_run_id="popo-1",
    )
    db.add(material)
    db.flush()
    legacy = MaterialOutput(
        user_id="u1", material_pk=material.id, material_id=material.material_id,
        output_type="elegantbook", origin="legacy_selfloop", status="promoted", quality_status="passed",
        is_current=True, manifest_bucket="legacy", manifest_object="legacy/manifest.json", output_run_id="legacy-1",
    )
    db.add(legacy)
    db.commit()
    manifest = {
        "schema": "luceon.workflow.artifact-manifest/v1", "workflow_job_id": "workflow-1",
        "material_id": material.material_id,
        "files": [{"path": "main.pdf"}, {"path": "latex-project.zip"}],
    }
    monkeypatch.setattr("app.workflow_v2.runner.read_object", lambda *_args: __import__("json").dumps(manifest).encode())
    job = SimpleNamespace(public_id="workflow-1", user_id="u1", material_id=material.material_id)
    artifact = SimpleNamespace(bucket="artifacts", object_name="worker-v2/final/manifest.json")

    _project_core_output(db, material, job, artifact)
    db.commit()
    _project_core_output(db, material, job, artifact)
    db.commit()

    rows = db.query(MaterialOutput).order_by(MaterialOutput.id).all()
    assert len(rows) == 2
    assert rows[0].origin == "legacy_selfloop"
    assert rows[0].is_current is False
    assert rows[1].origin == "worker_v2"
    assert rows[1].is_current is True
    assert rows[1].quality_status == "passed"
    assert material.latex_manifest_object == artifact.object_name


def test_deterministic_precheck_renders_only_evidenced_pages(tmp_path):
    import fitz

    pdf = tmp_path / "candidate.pdf"
    document = fitz.open()
    for number in range(1, 5):
        page = document.new_page()
        page.insert_text((36, 54), f"Page {number}")
    document.save(pdf)
    document.close()

    ledger = _render_deterministic_precheck(
        pdf,
        tmp_path / "rendered",
        [
            {"code": "ocr_placeholder_run", "pages": [2, 4]},
            {"code": "low_text_or_orphan_pages", "pages": [4]},
        ],
    )

    assert ledger["review_owner"] == "deterministic_precheck"
    assert [row["page"] for row in ledger["pages"]] == [2, 4]
    assert len(ledger["pages"][1]["findings"]) == 2
    assert all(Path(row["image"]).is_file() for row in ledger["pages"])


def test_xelatex_diagnostics_prefer_complete_file_log(monkeypatch):
    completed = SimpleNamespace(returncode=0, stdout="Missing character: There is no 验 (U+9A8C) in font cmr10!\n")
    monkeypatch.setattr("app.workflow_v2.runner.subprocess.run", lambda *_args, **_kwargs: completed)

    assert "U+9A8C" in _read_xelatex_log("/tmp/project", "console omitted glyph diagnostics")


def setup_runner(monkeypatch, tmp_path: Path, *, authorized: bool):
    old_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    workflow_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(old_engine)
    WorkflowBase.metadata.create_all(workflow_engine)
    old_factory = sessionmaker(bind=old_engine, expire_on_commit=False)
    workflow_factory = sessionmaker(bind=workflow_engine, expire_on_commit=False)
    old_db = old_factory()
    workflow_db = workflow_factory()
    material = Material(
        user_id="u1",
        material_id="pdf-runner",
        title="Runner",
        filename="runner.pdf",
        source_type="uploaded",
        stage_status="popo_done",
        pipeline_status="idle",
        input_bucket="eduassets-input",
        input_object="runner.pdf",
        popo_manifest_bucket="eduassets-minerupopo",
        popo_manifest_object="minerupopo/pdf-runner/popo-run/manifest.json",
    )
    old_db.add(material)
    old_db.commit()
    job, _ = create_workflow_job(workflow_db, user_id="u1", material=material, payload={"execution_authorized": authorized})
    workflow_db.commit()
    output = tmp_path / "body-final"
    output.mkdir()
    (output / "clean.md").write_text("# Runner\n", encoding="utf-8")
    (output / "outline_decision.json").write_text(
        '{"llm":{"enabled":true,"provider":"test","model":"bounded","call_count":1,"raw_usage":{"total_tokens":12},"estimated_cost":0.01,"verdict":"ok"}}',
        encoding="utf-8",
    )

    import app.workflow_v2.runner as runner
    monkeypatch.setattr(runner, "workflow_session_factory", lambda: workflow_factory)
    monkeypatch.setattr(runner, "LegacySessionLocal", old_factory)
    monkeypatch.setattr(runner, "minio_client", FakeMinio())
    monkeypatch.setattr(runner, "_run_canonical", lambda *_args, **_kwargs: {"body_final": str(output)})
    monkeypatch.setattr(
        runner,
        "build_canonical_conservation",
        lambda _path: {"status": "passed", "source_block_count": 1, "unexplained_block_count": 0, "blockers": []},
    )
    old_db.close()
    workflow_db.close()
    return workflow_factory, job.public_id


def test_unauthorized_runner_has_no_state_side_effect(monkeypatch, tmp_path):
    factory, public_id = setup_runner(monkeypatch, tmp_path, authorized=False)
    result = run_one_stage(public_id, worker_id="test")
    db = factory()
    job = db.query(WorkflowJob).filter(WorkflowJob.public_id == public_id).one()
    assert result["ok"] is False
    assert job.status == "queued"
    assert db.query(ArtifactVersion).count() == 0


def test_authorized_runner_publishes_and_advances(monkeypatch, tmp_path):
    factory, public_id = setup_runner(monkeypatch, tmp_path, authorized=True)
    result = run_one_stage(public_id, worker_id="test")
    db = factory()
    job = db.query(WorkflowJob).filter(WorkflowJob.public_id == public_id).one()
    assert result["ok"] is True
    assert job.status == "queued"
    assert job.current_stage_key == "outline_reconstruction"
    assert db.query(ArtifactVersion).count() == 1
    assert db.query(ModelCall).one().usage_json == '{"total_tokens": 12}'


def test_quality_gate_block_publishes_candidate_instead_of_technical_failure(monkeypatch, tmp_path):
    factory, public_id = setup_runner(monkeypatch, tmp_path, authorized=True)
    import app.workflow_v2.runner as runner

    monkeypatch.setattr(
        runner,
        "build_canonical_conservation",
        lambda _path: {
            "status": "review",
            "source_block_count": 1,
            "unexplained_block_count": 1,
            "blockers": [{"code": "unexplained_source_block"}],
        },
    )

    result = run_one_stage(public_id, worker_id="test")
    db = factory()
    job = db.query(WorkflowJob).filter(WorkflowJob.public_id == public_id).one()
    stage = db.query(StageRun).filter_by(workflow_job_id=job.id, stage_key="canonical_clean_material").one()
    artifact = db.query(ArtifactVersion).filter_by(stage_run_id=stage.id).one()

    assert result["ok"] is False
    assert job.status == "blocked"
    assert job.error_code == "quality_blocked"
    assert stage.status == "blocked"
    assert stage.output_manifest_sha256 == artifact.sha256
    assert artifact.status == "blocked"


def test_failed_runner_persists_status_and_failure_artifact(monkeypatch, tmp_path):
    factory, public_id = setup_runner(monkeypatch, tmp_path, authorized=True)
    import app.workflow_v2.runner as runner

    monkeypatch.setattr(runner, "WORK_ROOT", tmp_path / "workflow-v2")
    monkeypatch.setattr(runner, "_run_canonical", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("gate failed")))

    result = run_one_stage(public_id, worker_id="test")
    db = factory()
    job = db.query(WorkflowJob).filter(WorkflowJob.public_id == public_id).one()
    stage = db.query(StageRun).filter(
        StageRun.workflow_job_id == job.id,
        StageRun.stage_key == job.current_stage_key,
    ).order_by(StageRun.attempt.desc()).first()
    assert result["failure_evidence_error"] == ""
    artifact = db.query(ArtifactVersion).filter(ArtifactVersion.stage_run_id == stage.id).one()

    assert result["ok"] is False
    assert job.status == "failed"
    assert stage.status == "failed"
    assert stage.output_manifest_object == artifact.object_name
    assert artifact.artifact_kind == "canonical-clean-failure"


def test_runner_writes_immutable_failure_evidence_payload(monkeypatch, tmp_path):
    import app.workflow_v2.runner as runner

    monkeypatch.setattr(runner, "WORK_ROOT", tmp_path)
    job = SimpleNamespace(public_id="job-1", material_id="pdf-1", workflow_version="worker-v2")
    stage = SimpleNamespace(stage_key="canonical_clean_material", stage_version="v2", attempt=3, input_manifest_sha256="abc")

    evidence_dir = _write_failure_evidence(job, stage, RuntimeError("gate failed"))
    payload = __import__("json").loads((evidence_dir / "failure.json").read_text(encoding="utf-8"))

    assert payload["schema"] == "luceon.workflow.failure-evidence/v1"
    assert payload["stage"]["attempt"] == 3
    assert payload["error"] == {"code": "RuntimeError", "message": "gate failed"}


def test_bounded_refiner_cannot_change_document_class_options(tmp_path):
    source = tmp_path / "source"
    refined = tmp_path / "refined"
    source.mkdir()
    refined.mkdir()
    source.joinpath("main.tex").write_text(r"\documentclass[lang=cn,11pt,openany]{elegantbook}\nBody", encoding="utf-8")
    refined.joinpath("main.tex").write_text(r"\documentclass[lang=en,11pt,openany]{elegantbook}\nBody", encoding="utf-8")
    _preserve_main_documentclass(source, refined)
    assert refined.joinpath("main.tex").read_text(encoding="utf-8").startswith(
        r"\documentclass[lang=cn,11pt,openany]{elegantbook}"
    )


def test_bounded_refiner_cannot_remove_source_page_lineage(tmp_path):
    source = tmp_path / "source" / "chapters"
    refined = tmp_path / "refined" / "chapters"
    source.mkdir(parents=True)
    refined.mkdir(parents=True)
    source.joinpath("content.tex").write_text(
        "% source_page_idx: 8\n\\chapter{Pine for Water}\nBody\n",
        encoding="utf-8",
    )
    refined.joinpath("content.tex").write_text("\\chapter{Pine for Water}\nBody\n", encoding="utf-8")
    _preserve_source_page_markers(source.parent, refined.parent)
    assert refined.joinpath("content.tex").read_text(encoding="utf-8").startswith(
        "% source_page_idx: 8\n\\chapter{Pine for Water}"
    )


def test_builder_restores_bridge_section_marker_to_elegantbook_chapter(tmp_path):
    source = tmp_path / "input.tex"
    refined = tmp_path / "project" / "chapters"
    refined.mkdir(parents=True)
    source.write_text("% source_page_idx: 8\n\\section{Pine for Water}\n", encoding="utf-8")
    refined.joinpath("content.tex").write_text("\\chapter{Pine for Water}\n", encoding="utf-8")
    _preserve_source_page_markers_from_tex(source, refined.parent)
    assert "% source_page_idx: 8" in refined.joinpath("content.tex").read_text(encoding="utf-8")


def test_builder_restores_heading_marker_when_same_page_marker_exists_elsewhere(tmp_path):
    source = tmp_path / "input.tex"
    refined = tmp_path / "project" / "chapters"
    refined.mkdir(parents=True)
    source.write_text("% source_page_idx: 8\n\\section{Pine for Water}\n", encoding="utf-8")
    refined.joinpath("content.tex").write_text(
        "% source_page_idx: 8\nOrdinary page content.\n\\chapter{Pine for Water}\n",
        encoding="utf-8",
    )

    _preserve_source_page_markers_from_tex(source, refined.parent)

    result = refined.joinpath("content.tex").read_text(encoding="utf-8")
    assert "% source_page_idx: 8\n\\chapter{Pine for Water}" in result


def test_successful_independent_review_resolves_prior_open_findings(monkeypatch, tmp_path):
    factory, public_id = setup_runner(monkeypatch, tmp_path, authorized=True)
    db = factory()
    job = db.query(WorkflowJob).filter(WorkflowJob.public_id == public_id).one()
    stage = db.query(StageRun).filter(StageRun.workflow_job_id == job.id).order_by(StageRun.id.desc()).first()
    finding = QaFinding(
        workflow_job_id=job.id,
        stage_run_id=stage.id,
        code="visual_page_failure",
        severity="P1",
        page_number=3,
        status="open",
        details_json=QaFinding.dump({"pages": [3]}),
    )
    db.add(finding)
    db.flush()

    _record_independent_findings(db, job, stage, {"status": "passed", "hard_blockers": []}, [])

    assert finding.status == "resolved"
    assert finding.resolved_at is not None
    assert finding.load(finding.details_json, {})["resolution"]["stage_run_id"] == str(stage.id)


def test_final_review_records_golden_pass_evidence(monkeypatch, tmp_path):
    factory, public_id = setup_runner(monkeypatch, tmp_path, authorized=True)
    db = factory()
    job = db.query(WorkflowJob).filter(WorkflowJob.public_id == public_id).one()
    stage = db.query(StageRun).filter(StageRun.workflow_job_id == job.id).order_by(StageRun.id.desc()).first()
    case = GoldenRegressionCase(
        cohort_version="test",
        case_key="runner",
        material_pk=job.material_pk,
        material_id=job.material_id,
        title="Runner",
        dimensions_json="[]",
        selection_reason="test",
        baseline_json=GoldenRegressionCase.dump({"legacy_preserved": True}),
        status="selected",
    )
    artifact = ArtifactVersion(
        workflow_job_id=job.id,
        stage_run_id=stage.id,
        artifact_kind="final-review",
        bucket="test",
        object_name="review",
        object_identity_hash="b" * 64,
        sha256="a" * 64,
        size_bytes=1,
        metadata_json="{}",
    )
    db.add_all([case, artifact])
    db.flush()
    output = tmp_path / "review"
    output.mkdir()
    output.joinpath("page_review.json").write_text(
        '{"pdf_sha256":"pdf-hash","page_count":2,"pages":[{"status":"passed"},{"status":"passed"}]}',
        encoding="utf-8",
    )

    _record_golden_pass(db, job, stage, artifact, output)

    evidence = case.load(case.baseline_json, {})["latest_v2_pass"]
    assert case.status == "passed"
    assert evidence["final_review_artifact_id"] == str(artifact.id)
    assert evidence["failed_page_count"] == 0
