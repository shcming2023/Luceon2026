from types import SimpleNamespace

from scripts import material_task_worker


def test_resume_popo_worker_preserves_existing_batch_and_large_pdf_timeout(monkeypatch):
    snapshot = [
        {
            "material_id": "pdf-large",
            "input_object": "large.pdf",
            "size_bytes": 300 * 1024 * 1024,
            "page_count": 1200,
        }
    ]
    request = {
        "apply": True,
        "limit": 1,
        "snapshot": snapshot,
        "resume_context": {
            "mineru_batch_id": "mineru-batch-1",
            "material_id": "pdf-large",
            "input_object": "large.pdf",
        },
        "existing_popo_batch_id": "popo-batch-1",
    }
    run = SimpleNamespace(id=97, mode="resume_popo", request=lambda: request)
    db = SimpleNamespace(close=lambda: None)
    observed = {}

    monkeypatch.setattr(material_task_worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(material_task_worker, "claim_next_pipeline_run", lambda _db, _worker_id: run)

    def fake_run_pipeline_subprocess(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs

    monkeypatch.setattr(material_task_worker, "run_pipeline_subprocess", fake_run_pipeline_subprocess)

    assert material_task_worker.consume_once("worker-1") == {"kind": "pipeline_run", "id": "97"}
    command = observed["kwargs"]["command_override"]
    assert command[command.index("--existing-popo-batch-id") + 1] == "popo-batch-1"
    assert command[command.index("--timeout-seconds") + 1] == str(6 * 60 * 60)
    assert "--existing-mineru-batch-id" not in command
