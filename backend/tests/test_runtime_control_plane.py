from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models.base import Base
from app.services import runtime_settings
from app.utils.user_dep import auth_disabled, public_raw_asset_downloads_allowed
from main import app


def _client() -> TestClient:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _login(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "secret123"},
    )
    assert response.status_code == 200


def test_security_defaults_fail_closed(monkeypatch):
    monkeypatch.delenv("LUCEON_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("LUCEON_ALLOW_PUBLIC_RAW_ASSET_DOWNLOADS", raising=False)

    assert auth_disabled() is False
    assert public_raw_asset_downloads_allowed() is False


def test_runtime_settings_requires_runtime_admin(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCEON_AUTH_DISABLED", "false")
    monkeypatch.setenv("LUCEON_PIPELINE_ADMIN_EMAILS", "ops@example.com")
    monkeypatch.setattr(runtime_settings, "CONFIG_PATH", tmp_path / "runtime_config.json")
    client = _client()
    try:
        assert client.get("/api/runtime/settings").status_code == 401
        assert client.post("/api/runtime/backup/jobs").status_code == 401

        _login(client, "reader@example.com")
        assert client.get("/api/runtime/settings").status_code == 403
        assert client.post("/api/runtime/backup/jobs").status_code == 403
        client.post("/api/auth/logout")

        _login(client, "ops@example.com")
        response = client.get("/api/runtime/settings")
        assert response.status_code == 200
        assert response.json()["schema_version"] == 2
    finally:
        app.dependency_overrides.clear()


def test_runtime_config_v2_is_explicit_and_drops_dead_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_settings, "CONFIG_PATH", tmp_path / "runtime_config.json")
    monkeypatch.setenv("MINIO_INTERNAL_ENDPOINT", "host.docker.internal:9000")
    monkeypatch.setenv("MINIO_PUBLIC_ENDPOINT", "http://public.example:19000")
    monkeypatch.setenv("LUCEON_EXTERNAL_BACKUP_ROOT", "/external-backups")

    config = runtime_settings.load_runtime_config(include_secrets=False)

    assert config["schema_version"] == 2
    assert config["minio"]["endpoint"] == "host.docker.internal:9000"
    assert config["minio"]["public_endpoint"] == "http://public.example:19000"
    assert config["minio"]["contract_buckets"] == list(runtime_settings.CURRENT_ASSET_BUCKETS)
    assert config["gpu"]["mode"] == "on_demand"
    assert set(config["gpu"]) == {"mode", "wrapper_url", "api_key", "api_key_configured", "lifecycle"}
    assert config["gpu"]["lifecycle"]["public_key"] == ""
    assert config["gpu"]["lifecycle"]["private_key"] == ""
    assert config["gpu"]["lifecycle"]["project_id"] == ""
    assert set(config["models"]) == {"llm", "vision"}
    assert [target["id"] for target in config["backup"]["targets"]] == ["snapshot", "external"]
    assert config["backup"]["include_legacy"] is True
    assert config["backup"]["max_objects"] == 2_000_000
    assert "last_backup" not in config["backup"]


def test_minio_internal_host_port_is_not_misread_as_url_scheme():
    assert runtime_settings._parse_minio_endpoint("host.docker.internal:9000", False) == (
        "host.docker.internal:9000",
        False,
    )
    assert runtime_settings._parse_minio_endpoint("https://assets.example.com", False) == (
        "assets.example.com",
        True,
    )


def test_legacy_runtime_config_is_normalized_without_preserving_dead_fields(monkeypatch, tmp_path):
    path = tmp_path / "runtime_config.json"
    path.write_text(
        """{
          "minio": {"contract_buckets": ["eduassets-input"], "public_endpoint": ""},
          "gpu": {"ssh_password": "old", "service_root": "/old", "ssh_key_path": "~/.ssh/old"},
          "models": {"asr": {"model": "old"}, "tts": {"model": "old"}, "image_generation": {"model": "old"}},
          "backup": {"include_auxiliary": true, "targets": [{"id": "onedrive", "provider": "onedrive", "path": ""}]}
        }""",
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_settings, "CONFIG_PATH", path)
    monkeypatch.setenv("MINIO_PUBLIC_ENDPOINT", "http://public.example:19000")

    config = runtime_settings.load_runtime_config(include_secrets=False)

    assert config["schema_version"] == 2
    assert config["minio"]["contract_buckets"] == list(runtime_settings.CURRENT_ASSET_BUCKETS)
    assert config["minio"]["public_endpoint"] == "http://public.example:19000"
    assert "ssh_password" not in config["gpu"]
    assert "service_root" not in config["gpu"]
    assert set(config["models"]) == {"llm", "vision"}
    assert {row["id"] for row in config["backup"]["targets"]} == {"snapshot", "external"}


def test_runtime_status_treats_offline_on_demand_gpu_as_expected_off(monkeypatch):
    monkeypatch.setattr(
        runtime_settings,
        "check_minio_contract",
        lambda create_missing=False: {"contract_ok": True, "missing": []},
    )
    monkeypatch.setattr(
        runtime_settings,
        "check_gpu_runtime",
        lambda: {"wrapper_ok": False, "staged_api_ok": False, "state": "offline", "errors": []},
    )
    monkeypatch.setattr(runtime_settings, "check_backup_targets", lambda: {"ready_count": 1, "external_ready_count": 1, "targets": []})
    monkeypatch.setattr(runtime_settings, "check_model_runtime", lambda: {"llm": {"enabled": False}, "vision": {"enabled": False}})
    monkeypatch.setattr(runtime_settings, "check_runtime_dependencies", lambda: {"ready": True, "checks": {}})
    monkeypatch.setattr(runtime_settings, "active_gpu_task_count", lambda: 0)
    monkeypatch.setattr(runtime_settings, "runtime_config_validation", lambda: {"ok": True, "errors": [], "warnings": []})

    result = runtime_settings.runtime_status()

    assert result["status"] == "ready"
    assert result["gpu"]["state"] == "expected_off"
    assert "gpu_wrapper" not in result["warnings"]


def test_runtime_status_blocks_active_gpu_work_when_staged_api_is_down(monkeypatch):
    monkeypatch.setattr(
        runtime_settings,
        "check_minio_contract",
        lambda create_missing=False: {"contract_ok": True, "missing": []},
    )
    monkeypatch.setattr(
        runtime_settings,
        "check_gpu_runtime",
        lambda: {"wrapper_ok": True, "staged_api_ok": False, "state": "degraded", "errors": []},
    )
    monkeypatch.setattr(runtime_settings, "check_backup_targets", lambda: {"ready_count": 1, "external_ready_count": 1, "targets": []})
    monkeypatch.setattr(runtime_settings, "check_model_runtime", lambda: {"llm": {"enabled": False}, "vision": {"enabled": False}})
    monkeypatch.setattr(runtime_settings, "check_runtime_dependencies", lambda: {"ready": True, "checks": {}})
    monkeypatch.setattr(runtime_settings, "active_gpu_task_count", lambda: 1)
    monkeypatch.setattr(runtime_settings, "runtime_config_validation", lambda: {"ok": True, "errors": [], "warnings": []})

    result = runtime_settings.runtime_status()

    assert result["status"] == "blocked"
    assert "gpu_staged_api" in result["blockers"]


def test_runtime_status_blocks_unhealthy_core_dependency(monkeypatch):
    monkeypatch.setattr(
        runtime_settings,
        "check_minio_contract",
        lambda create_missing=False: {"contract_ok": True, "missing": []},
    )
    monkeypatch.setattr(
        runtime_settings,
        "check_gpu_runtime",
        lambda: {"wrapper_ok": False, "staged_api_ok": False, "state": "offline", "errors": []},
    )
    monkeypatch.setattr(runtime_settings, "check_backup_targets", lambda: {"ready_count": 1, "external_ready_count": 1, "targets": []})
    monkeypatch.setattr(runtime_settings, "check_model_runtime", lambda: {"llm": {"enabled": False}, "vision": {"enabled": False}})
    monkeypatch.setattr(
        runtime_settings,
        "check_runtime_dependencies",
        lambda: {"ready": False, "checks": {"workflow_database": {"ready": False}}},
    )
    monkeypatch.setattr(runtime_settings, "active_gpu_task_count", lambda: 0)
    monkeypatch.setattr(runtime_settings, "runtime_config_validation", lambda: {"ok": True, "errors": [], "warnings": []})

    result = runtime_settings.runtime_status()

    assert result["status"] == "blocked"
    assert "runtime_dependencies" in result["blockers"]
