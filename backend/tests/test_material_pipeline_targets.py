from types import SimpleNamespace

from app.api import materials as materials_api
from app.services.material_inventory import (
    LARGE_PDF_PIPELINE_WAIT_TIMEOUT_SECONDS,
    pipeline_command,
    pipeline_preflight_command,
    pipeline_target_args,
    pipeline_wait_timeout_seconds,
    popo_resume_command,
)


def test_pipeline_target_args_preserve_multiple_selected_materials():
    args = pipeline_target_args(
        material_ids=["pdf-first", "pdf-second"],
        input_objects=["first.pdf", "second.pdf"],
    )

    assert args == [
        "--material-id",
        "pdf-first",
        "--material-id",
        "pdf-second",
        "--input-object",
        "first.pdf",
        "--input-object",
        "second.pdf",
    ]


def test_pipeline_command_targets_every_selected_material():
    command = pipeline_command(
        apply=True,
        limit=2,
        material_ids=["pdf-first", "pdf-second"],
        input_objects=["first.pdf", "second.pdf"],
    )

    assert command.count("--material-id") == 2
    assert command.count("--input-object") == 2
    assert command[-2:] == ["--apply", "--wait"]
    assert "--input-status-only" in command


def test_completed_reprocess_requires_explicit_cli_flag():
    ordinary = pipeline_preflight_command(1, material_ids=["pdf-first"])
    versioned = pipeline_preflight_command(1, material_ids=["pdf-first"], reprocess_completed=True)
    apply_versioned = pipeline_command(
        apply=True,
        limit=1,
        material_ids=["pdf-first"],
        reprocess_completed=True,
    )

    assert "--reprocess-completed" not in ordinary
    assert "--reprocess-completed" in versioned
    assert "--reprocess-completed" in apply_versioned


def test_popo_resume_command_reuses_frozen_mineru_without_resubmitting_it():
    command = popo_resume_command(
        existing_mineru_batch_id="mineru-batch-1",
        material_id="pdf-first",
        input_object="first.pdf",
        apply=True,
    )

    assert command[:3] == ["python3", command[1], "run-staged"]
    assert command[command.index("--existing-mineru-batch-id") + 1] == "mineru-batch-1"
    assert "--reuse-frozen-mineru" in command
    assert command[-2:] == ["--apply", "--wait"]


def test_large_pdf_uses_extended_wait_timeout_and_existing_popo_is_reused():
    timeout = pipeline_wait_timeout_seconds([{"size_bytes": 300 * 1024 * 1024, "page_count": 1200}])
    command = popo_resume_command(
        existing_mineru_batch_id="mineru-batch-1",
        material_id="pdf-first",
        input_object="first.pdf",
        apply=True,
        existing_popo_batch_id="popo-batch-1",
        timeout_seconds=timeout,
    )

    assert timeout == LARGE_PDF_PIPELINE_WAIT_TIMEOUT_SECONDS
    assert command[command.index("--existing-popo-batch-id") + 1] == "popo-batch-1"
    assert "--existing-mineru-batch-id" not in command
    assert command[command.index("--timeout-seconds") + 1] == str(6 * 60 * 60)


def test_resume_popo_preflight_reuses_latest_timed_out_remote_batch(monkeypatch):
    material = SimpleNamespace(id=9, user_id="owner-1")
    observed = {}
    monkeypatch.setattr(materials_api, "_admin_material_or_404", lambda material_pk, db: material)
    monkeypatch.setattr(
        materials_api,
        "latest_timed_out_popo_batch_id",
        lambda db, user_id, selected: "popo-batch-1",
    )

    def fake_preflight(selected, *, existing_popo_batch_id=""):
        observed["material"] = selected
        observed["existing_popo_batch_id"] = existing_popo_batch_id
        return {"ready": True}

    monkeypatch.setattr(materials_api, "run_popo_resume_preflight", fake_preflight)

    result = materials_api.preflight_resume_popo(
        material_pk=9,
        admin_user=SimpleNamespace(id=1),
        db=object(),
    )

    assert result == {"ready": True}
    assert observed == {"material": material, "existing_popo_batch_id": "popo-batch-1"}
