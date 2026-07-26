import sqlite3

from sqlalchemy import create_engine, inspect

from app.workflow_v3.database import WORKFLOW_V3_SCHEMA_REVISION
from scripts import workflow_v3_database_admin as admin


def test_explicit_bootstrap_backup_and_offline_rollback(monkeypatch, tmp_path):
    database = tmp_path / "workflow-v3.db"
    backup_dir = tmp_path / "backups"
    safety_dir = tmp_path / "safety"
    monkeypatch.setenv("WORKFLOW_V3_DATABASE_URL", f"sqlite:///{database}")

    initial = admin.status()
    assert initial["ready"] is False

    bootstrapped = admin.bootstrap(
        confirmation=admin.BOOTSTRAP_CONFIRMATION,
        backup_dir=backup_dir,
    )
    assert bootstrapped["database"]["ready"] is True
    assert admin.status()["ready"] is True
    assert not {
        "workflow_v3_expert_runs",
        "workflow_v3_expert_events",
    } & admin._sqlite_tables(database)
    engine = create_engine(f"sqlite:///{database}")
    columns = {
        row["name"]
        for row in inspect(engine).get_columns("workflow_v3_projection_outbox")
    }
    assert {
        "formal_target_bucket",
        "formal_target_prefix",
        "formal_target_manifest_object",
    } <= columns
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT revision FROM workflow_v3_schema_revisions WHERE revision = ?",
            (WORKFLOW_V3_SCHEMA_REVISION,),
        ).fetchone() == (WORKFLOW_V3_SCHEMA_REVISION,)
    engine.dispose()

    backed_up = admin.backup(backup_dir=backup_dir)
    backup_path = tmp_path / backed_up["backup"]["backup"]
    assert backup_path.is_file()
    assert backup_path.with_suffix(".manifest.json").is_file()

    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE workflow_v3_restore_probe (id INTEGER)")
    assert "workflow_v3_restore_probe" in admin._sqlite_tables(database)

    restored = admin.rollback(
        backup_path=backup_path,
        safety_backup_dir=safety_dir,
        confirmation=admin.ROLLBACK_CONFIRMATION,
        services_stopped=True,
    )
    assert restored["ok"] is True
    assert "workflow_v3_restore_probe" not in admin._sqlite_tables(database)
    assert admin.status()["ready"] is True


def test_bootstrap_refuses_shared_database(monkeypatch, tmp_path):
    database = tmp_path / "shared.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE historical_materials (id INTEGER)")
    monkeypatch.setenv("WORKFLOW_V3_DATABASE_URL", f"sqlite:///{database}")

    try:
        admin.bootstrap(
            confirmation=admin.BOOTSTRAP_CONFIRMATION,
            backup_dir=tmp_path / "backups",
        )
    except RuntimeError as exc:
        assert "non-dedicated" in str(exc)
    else:
        raise AssertionError("shared database bootstrap must fail closed")


def test_bootstrap_upgrades_existing_projection_target_columns(monkeypatch, tmp_path):
    database = tmp_path / "workflow-v3-old.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE workflow_v3_projection_outbox (
                id INTEGER PRIMARY KEY,
                workflow_job_id INTEGER NOT NULL,
                final_promotion_id INTEGER NOT NULL,
                idempotency_key VARCHAR(128) NOT NULL,
                event_kind VARCHAR(32) NOT NULL,
                status VARCHAR(32) NOT NULL,
                payload_json TEXT NOT NULL,
                projected_output_id INTEGER,
                projected_manifest_sha256 VARCHAR(64) NOT NULL DEFAULT ''
            )
            """
        )
    monkeypatch.setenv("WORKFLOW_V3_DATABASE_URL", f"sqlite:///{database}")
    result = admin.bootstrap(
        confirmation=admin.BOOTSTRAP_CONFIRMATION,
        backup_dir=tmp_path / "backups",
    )
    assert result["database"]["ready"] is True
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(workflow_v3_projection_outbox)"
            )
        }
    assert {
        "formal_target_bucket",
        "formal_target_prefix",
        "formal_target_manifest_object",
    } <= columns


def test_bootstrap_and_rollback_require_exact_confirmation(monkeypatch, tmp_path):
    database = tmp_path / "workflow-v3.db"
    monkeypatch.setenv("WORKFLOW_V3_DATABASE_URL", f"sqlite:///{database}")
    try:
        admin.bootstrap(confirmation="yes", backup_dir=tmp_path / "backups")
    except RuntimeError as exc:
        assert admin.BOOTSTRAP_CONFIRMATION in str(exc)
    else:
        raise AssertionError("bootstrap without exact confirmation must fail")
