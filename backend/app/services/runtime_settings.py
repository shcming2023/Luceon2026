import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from minio import Minio
from sqlalchemy import text


CONFIG_PATH = Path(os.getenv("LUCEON_RUNTIME_CONFIG", "/data/runtime_config.json"))
BACKUP_ROOT = Path(os.getenv("LUCEON_BACKUP_ROOT", "/backups"))
EXTERNAL_BACKUP_ROOT = Path(os.getenv("LUCEON_EXTERNAL_BACKUP_ROOT", "/external-backups"))
RUNTIME_CONFIG_SCHEMA_VERSION = 2
CURRENT_ASSET_BUCKETS = (
    "eduassets-input",
    "eduassets-mineru",
    "eduassets-minerupopo",
    "eduassets-raw",
    "eduassets-clean",
    "eduassets-standard",
    "eduassets-parsed",
    "eduassets-elegantbook",
    "eduassets-review",
)
LEGACY_ASSET_BUCKETS = ("eduassets-latex",)
CONTRACT_BUCKETS = list(CURRENT_ASSET_BUCKETS)
SECRET_FIELDS = {"access_key", "secret_key", "api_key", "public_key", "private_key"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def default_runtime_config() -> dict[str, Any]:
    credential_file = os.getenv("COMPSHARE_CREDENTIALS_FILE", "").strip()
    credential_source = "secret_file" if credential_file else (
        "legacy_environment"
        if os.getenv("COMPSHARE_PUBLIC_KEY") or os.getenv("COMPSHARE_PRIVATE_KEY")
        else "missing"
    )
    return {
        "schema_version": RUNTIME_CONFIG_SCHEMA_VERSION,
        "environment": {
            "name": os.getenv("LUCEON_ENVIRONMENT", "development"),
            "public_app_url": os.getenv("LUCEON_PUBLIC_APP_URL", ""),
        },
        "minio": {
            "endpoint": os.getenv("MINIO_INTERNAL_ENDPOINT") or os.getenv("MINIO_ENDPOINT", "host.docker.internal:9000"),
            "public_endpoint": os.getenv("MINIO_PUBLIC_ENDPOINT", ""),
            "secure": _env_bool("MINIO_SECURE", False),
            "access_key": os.getenv("MINIO_ACCESS_KEY", ""),
            "secret_key": os.getenv("MINIO_SECRET_KEY", ""),
            "region": os.getenv("MINIO_REGION", "us-east-1"),
            "contract_buckets": list(CURRENT_ASSET_BUCKETS),
        },
        "gpu": {
            "mode": "on_demand",
            "wrapper_url": os.getenv("GPU_WRAPPER_URL", os.getenv("MINERU_API_URL", "")),
            "api_key": os.getenv("GPU_WRAPPER_API_KEY", ""),
            "lifecycle": {
                "enabled": _env_bool("COMPSHARE_LIFECYCLE_ENABLED", False),
                "endpoint": os.getenv("COMPSHARE_API_ENDPOINT", "https://api.compshare.cn"),
                "public_key": os.getenv("COMPSHARE_PUBLIC_KEY", ""),
                "private_key": os.getenv("COMPSHARE_PRIVATE_KEY", ""),
                "credential_source": credential_source,
                "credential_file_configured": bool(credential_file),
                "region": os.getenv("COMPSHARE_REGION", ""),
                "zone": os.getenv("COMPSHARE_ZONE", ""),
                "project_id": os.getenv("COMPSHARE_PROJECT_ID", ""),
                "uhost_id": os.getenv("COMPSHARE_UHOST_ID", ""),
                "ssh_host": os.getenv("GPU_SSH_HOST", ""),
                "ssh_port": int(os.getenv("GPU_SSH_PORT", "22") or "22"),
                "auto_stop": _env_bool("COMPSHARE_AUTO_STOP", True),
                "stop_grace_seconds": int(os.getenv("COMPSHARE_STOP_GRACE_SECONDS", "60") or "60"),
            },
        },
        "backup": {
            "enabled": False,
            "mode": "manifest",
            "schedule_enabled": False,
            "interval_hours": 24,
            "include_legacy": True,
            "max_objects": 2000000,
            "targets": [
                {
                    "id": "snapshot",
                    "label": "本机快照",
                    "kind": "filesystem",
                    "path": str(BACKUP_ROOT / "snapshots"),
                    "enabled": True,
                    "external": False,
                },
                {
                    "id": "external",
                    "label": "外部备份",
                    "kind": "filesystem",
                    "path": str(EXTERNAL_BACKUP_ROOT),
                    "enabled": False,
                    "external": True,
                },
            ],
        },
        "models": {
            "llm": {
                "enabled": _env_bool("LUCEON_RAW_OUTLINE_LLM", True),
                "provider": "deepseek",
                "default_model": (
                    os.getenv("LLM_DEFAULT_MODEL")
                    or os.getenv("DEEPSEEK_DEFAULT_MODEL")
                    or os.getenv("DEEPSEEK_FLASH_MODEL")
                    or os.getenv("DEEPSEEK_MODEL")
                    or "deepseek-v4-flash"
                ),
                "reasoning_model": (
                    os.getenv("LLM_REASONING_MODEL")
                    or os.getenv("DEEPSEEK_REASONING_MODEL")
                    or "deepseek-v4-pro"
                ),
                "deepseek": {
                    "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                    "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
                },
                "outline_decision_max_tokens": int(os.getenv("DEEPSEEK_OUTLINE_DECISION_MAX_TOKENS", "16000") or "16000"),
                "outline_global_max_candidates": int(os.getenv("DEEPSEEK_OUTLINE_GLOBAL_MAX_CANDIDATES", "500") or "500"),
                "outline_max_risk_candidates": int(os.getenv("LUCEON_RAW_OUTLINE_LLM_MAX_RISK_CANDIDATES", "120") or "120"),
            },
            "vision": {
                "enabled": _env_bool("LUCEON_RAW_OUTLINE_VISION", False),
                "provider": "dashscope",
                "model": (
                    os.getenv("VISION_MODEL")
                    or os.getenv("DASHSCOPE_VISION_MODEL")
                    or "qwen3.7-plus-2026-05-26"
                ),
                "dashscope": {
                    "base_url": os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                    "api_key": os.getenv("DASHSCOPE_API_KEY", ""),
                },
                "outline_visual_max_candidates": int(os.getenv("LUCEON_RAW_OUTLINE_VISUAL_MAX_CANDIDATES", "40") or "40"),
            },
        },
    }


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _normalize_runtime_config(value: dict[str, Any] | None) -> dict[str, Any]:
    defaults = default_runtime_config()
    value = value if isinstance(value, dict) else {}

    raw_minio = value.get("minio") if isinstance(value.get("minio"), dict) else {}
    minio = _deep_merge(defaults["minio"], raw_minio)
    minio["public_endpoint"] = str(minio.get("public_endpoint") or defaults["minio"]["public_endpoint"] or "")
    minio["contract_buckets"] = list(CURRENT_ASSET_BUCKETS)

    raw_gpu = value.get("gpu") if isinstance(value.get("gpu"), dict) else {}
    raw_lifecycle = raw_gpu.get("lifecycle") if isinstance(raw_gpu.get("lifecycle"), dict) else {}
    lifecycle = _deep_merge(defaults["gpu"]["lifecycle"], raw_lifecycle)
    # Account credentials and ProjectId are process-injected only.  Ignore any
    # browser/config-file attempt to replace them.
    for protected in ("public_key", "private_key", "project_id"):
        lifecycle[protected] = defaults["gpu"]["lifecycle"][protected]
    lifecycle["enabled"] = bool(lifecycle.get("enabled"))
    lifecycle["auto_stop"] = bool(lifecycle.get("auto_stop"))
    lifecycle["ssh_port"] = max(1, min(65535, int(lifecycle.get("ssh_port") or 22)))
    lifecycle["stop_grace_seconds"] = max(0, int(lifecycle.get("stop_grace_seconds") or 0))
    gpu = {
        "mode": "on_demand",
        "wrapper_url": str(raw_gpu.get("wrapper_url") or defaults["gpu"]["wrapper_url"] or ""),
        "api_key": str(raw_gpu.get("api_key") or defaults["gpu"]["api_key"] or ""),
        "lifecycle": lifecycle,
    }

    raw_models = value.get("models") if isinstance(value.get("models"), dict) else {}
    models = {
        "llm": _deep_merge(defaults["models"]["llm"], raw_models.get("llm") if isinstance(raw_models.get("llm"), dict) else {}),
        "vision": _deep_merge(
            defaults["models"]["vision"],
            raw_models.get("vision") if isinstance(raw_models.get("vision"), dict) else {},
        ),
    }

    raw_backup = value.get("backup") if isinstance(value.get("backup"), dict) else {}
    backup = {
        key: raw_backup.get(key, defaults["backup"][key])
        for key in ("enabled", "mode", "schedule_enabled", "interval_hours", "include_legacy", "max_objects")
    }
    if backup["mode"] not in {"manifest", "copy"}:
        backup["mode"] = "manifest"
    incoming_targets = {
        str(row.get("id")): row
        for row in raw_backup.get("targets", [])
        if isinstance(row, dict) and row.get("id") in {"snapshot", "external"}
    }
    backup["targets"] = []
    for target in defaults["backup"]["targets"]:
        incoming = incoming_targets.get(target["id"], {})
        backup["targets"].append({**target, "enabled": bool(incoming.get("enabled", target["enabled"]))})

    raw_environment = value.get("environment") if isinstance(value.get("environment"), dict) else {}
    environment = _deep_merge(defaults["environment"], raw_environment)

    return {
        "schema_version": RUNTIME_CONFIG_SCHEMA_VERSION,
        "environment": environment,
        "minio": minio,
        "gpu": gpu,
        "backup": backup,
        "models": models,
    }


def load_runtime_config(include_secrets: bool = True) -> dict[str, Any]:
    stored: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            decoded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(decoded, dict):
                stored = decoded
        except json.JSONDecodeError:
            pass
    config = _normalize_runtime_config(stored)
    return config if include_secrets else sanitize_config(config)


def _strip_blank_secrets(incoming: Any, existing: Any) -> Any:
    if isinstance(incoming, dict):
        cleaned = {}
        for key, value in incoming.items():
            if key in SECRET_FIELDS and not str(value or "").strip():
                continue
            cleaned[key] = _strip_blank_secrets(value, existing.get(key) if isinstance(existing, dict) else None)
        return cleaned
    if isinstance(incoming, list):
        return [_strip_blank_secrets(item, {}) for item in incoming]
    return incoming


def save_runtime_config(payload: dict[str, Any]) -> dict[str, Any]:
    current = load_runtime_config(include_secrets=True)
    cleaned = _strip_blank_secrets(payload, current)
    merged = _normalize_runtime_config(_deep_merge(current, cleaned))
    merged.get("minio", {}).pop("access_key_configured", None)
    merged.get("minio", {}).pop("secret_key_configured", None)
    merged.get("gpu", {}).pop("api_key_configured", None)
    merged.get("gpu", {}).get("lifecycle", {}).pop("public_key_configured", None)
    merged.get("gpu", {}).get("lifecycle", {}).pop("private_key_configured", None)
    merged.get("gpu", {}).get("lifecycle", {}).pop("project_id_configured", None)
    merged.get("models", {}).get("llm", {}).get("deepseek", {}).pop("api_key_configured", None)
    merged.get("models", {}).get("vision", {}).get("dashscope", {}).pop("api_key_configured", None)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CONFIG_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(CONFIG_PATH)
    return sanitize_config(merged)


def sanitize_config(config: dict[str, Any]) -> dict[str, Any]:
    safe = json.loads(json.dumps(config, ensure_ascii=False))
    minio = safe.get("minio", {})
    minio["access_key_configured"] = bool(config.get("minio", {}).get("access_key"))
    minio["secret_key_configured"] = bool(config.get("minio", {}).get("secret_key"))
    minio["access_key"] = ""
    minio["secret_key"] = ""
    gpu = safe.get("gpu", {})
    gpu["api_key_configured"] = bool(config.get("gpu", {}).get("api_key"))
    gpu["api_key"] = ""
    lifecycle = gpu.get("lifecycle", {}) if isinstance(gpu.get("lifecycle"), dict) else {}
    source_lifecycle = config.get("gpu", {}).get("lifecycle", {})
    lifecycle["public_key_configured"] = bool(source_lifecycle.get("public_key"))
    lifecycle["private_key_configured"] = bool(source_lifecycle.get("private_key"))
    lifecycle["project_id_configured"] = bool(source_lifecycle.get("project_id"))
    lifecycle["public_key"] = ""
    lifecycle["private_key"] = ""
    # ProjectId is an account-scoped identifier and is not returned to browsers.
    lifecycle["project_id"] = ""
    models = safe.get("models", {})
    llm = models.get("llm", {}) if isinstance(models.get("llm"), dict) else {}
    deepseek = llm.get("deepseek", {}) if isinstance(llm.get("deepseek"), dict) else {}
    deepseek["api_key_configured"] = bool(
        config.get("models", {}).get("llm", {}).get("deepseek", {}).get("api_key")
    )
    deepseek["api_key"] = ""
    vision = models.get("vision", {}) if isinstance(models.get("vision"), dict) else {}
    dashscope = vision.get("dashscope", {}) if isinstance(vision.get("dashscope"), dict) else {}
    dashscope["api_key_configured"] = bool(
        config.get("models", {}).get("vision", {}).get("dashscope", {}).get("api_key")
    )
    dashscope["api_key"] = ""
    return safe


def _parse_minio_endpoint(endpoint: str, secure: bool) -> tuple[str, bool]:
    parsed = urlparse(endpoint)
    if "://" in endpoint:
        return parsed.netloc, parsed.scheme == "https"
    return endpoint, secure


def minio_client_from_config(config: dict[str, Any] | None = None) -> Minio:
    config = config or load_runtime_config(include_secrets=True)
    minio = config.get("minio", {})
    endpoint, secure = _parse_minio_endpoint(str(minio.get("endpoint") or ""), bool(minio.get("secure")))
    return Minio(
        endpoint,
        access_key=str(minio.get("access_key") or ""),
        secret_key=str(minio.get("secret_key") or ""),
        secure=secure,
        region=str(minio.get("region") or "us-east-1"),
    )


def pipeline_env() -> dict[str, str]:
    config = load_runtime_config(include_secrets=True)
    env = os.environ.copy()
    minio = config.get("minio", {})
    gpu = config.get("gpu", {})
    models = config.get("models", {})
    llm = models.get("llm", {}) if isinstance(models.get("llm"), dict) else {}
    deepseek = llm.get("deepseek", {}) if isinstance(llm.get("deepseek"), dict) else {}
    vision = models.get("vision", {}) if isinstance(models.get("vision"), dict) else {}
    dashscope = vision.get("dashscope", {}) if isinstance(vision.get("dashscope"), dict) else {}
    if minio.get("endpoint"):
        env["MINIO_ENDPOINT"] = str(minio["endpoint"])
    if minio.get("public_endpoint"):
        env["MINIO_PUBLIC_ENDPOINT"] = str(minio["public_endpoint"])
    env["MINIO_SECURE"] = "true" if minio.get("secure") else "false"
    if minio.get("access_key"):
        env["MINIO_ACCESS_KEY"] = str(minio["access_key"])
    if minio.get("secret_key"):
        env["MINIO_SECRET_KEY"] = str(minio["secret_key"])
    if gpu.get("wrapper_url"):
        env["GPU_WRAPPER_URL"] = str(gpu["wrapper_url"])
    if gpu.get("api_key"):
        env["GPU_WRAPPER_API_KEY"] = str(gpu["api_key"])
    env["LUCEON_RAW_OUTLINE_LLM"] = "1" if llm.get("enabled", True) else "0"
    env["LUCEON_RAW_OUTLINE_VISION"] = "1" if vision.get("enabled") else "0"
    env["LUCEON_RAW_OUTLINE_LLM_MAX_RISK_CANDIDATES"] = str(llm.get("outline_max_risk_candidates") or 120)
    env["LUCEON_RAW_OUTLINE_VISUAL_MAX_CANDIDATES"] = str(vision.get("outline_visual_max_candidates") or 40)
    env["DEEPSEEK_OUTLINE_DECISION_MAX_TOKENS"] = str(llm.get("outline_decision_max_tokens") or 16000)
    env["DEEPSEEK_OUTLINE_GLOBAL_MAX_CANDIDATES"] = str(llm.get("outline_global_max_candidates") or 500)
    if llm.get("default_model"):
        env["LLM_DEFAULT_MODEL"] = str(llm["default_model"])
        env["DEEPSEEK_DEFAULT_MODEL"] = str(llm["default_model"])
        env["DEEPSEEK_FLASH_MODEL"] = str(llm["default_model"])
        env["DEEPSEEK_MODEL"] = str(llm["default_model"])
    if llm.get("reasoning_model"):
        env["LLM_REASONING_MODEL"] = str(llm["reasoning_model"])
        env["DEEPSEEK_REASONING_MODEL"] = str(llm["reasoning_model"])
    if deepseek.get("base_url"):
        env["DEEPSEEK_BASE_URL"] = str(deepseek["base_url"])
        env["DEEPSEEK_API_BASE"] = _chat_completions_url(str(deepseek["base_url"]))
    if deepseek.get("api_key"):
        env["DEEPSEEK_API_KEY"] = str(deepseek["api_key"])
    if vision.get("model"):
        env["VISION_MODEL"] = str(vision["model"])
        env["DASHSCOPE_VISION_MODEL"] = str(vision["model"])
    if dashscope.get("base_url"):
        env["DASHSCOPE_BASE_URL"] = str(dashscope["base_url"])
    if dashscope.get("api_key"):
        env["DASHSCOPE_API_KEY"] = str(dashscope["api_key"])
    return env


def check_model_runtime() -> dict[str, Any]:
    env = pipeline_env()
    llm_enabled = env.get("LUCEON_RAW_OUTLINE_LLM", "1").lower() in {"1", "true", "yes", "on"}
    vision_enabled = env.get("LUCEON_RAW_OUTLINE_VISION", "").lower() in {"1", "true", "yes", "on"}
    return {
        "llm": {
            "enabled": llm_enabled,
            "provider": "deepseek",
            "model": env.get("LLM_DEFAULT_MODEL") or env.get("DEEPSEEK_MODEL") or "",
            "reasoning_model": env.get("LLM_REASONING_MODEL") or env.get("DEEPSEEK_REASONING_MODEL") or "",
            "api_key_configured": bool(env.get("DEEPSEEK_API_KEY", "").strip()),
            "base_url": env.get("DEEPSEEK_BASE_URL", ""),
        },
        "vision": {
            "enabled": vision_enabled,
            "provider": "dashscope",
            "model": env.get("VISION_MODEL") or env.get("DASHSCOPE_VISION_MODEL") or "",
            "api_key_configured": bool(env.get("DASHSCOPE_API_KEY", "").strip()),
            "base_url": env.get("DASHSCOPE_BASE_URL", ""),
        },
    }


def _chat_completions_url(base_url: str) -> str:
    base = str(base_url or "").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _probe_chat_model(provider: str, base_url: str, api_key: str, model: str) -> dict[str, Any]:
    started = time.monotonic()
    result = {
        "provider": provider,
        "model": model,
        "ok": False,
        "skipped": False,
        "status_code": None,
        "latency_ms": 0,
        "error": "",
    }
    if not api_key:
        result.update({"skipped": True, "error": "missing_api_key"})
        return result
    if not base_url:
        result.update({"skipped": True, "error": "missing_base_url"})
        return result
    if not model:
        result.update({"skipped": True, "error": "missing_model"})
        return result
    try:
        response = httpx.post(
            _chat_completions_url(base_url),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 8,
                "temperature": 0,
            },
            timeout=20,
        )
        result["status_code"] = response.status_code
        result["ok"] = 200 <= response.status_code < 300
        if not result["ok"]:
            result["error"] = _model_error_summary(response)
    except Exception as exc:
        result["error"] = str(exc)[:300]
    finally:
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
    return result


def _model_error_summary(response: httpx.Response) -> str:
    try:
        data = response.json()
    except Exception:
        return response.text[:300]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or error)[:300]
        if error:
            return str(error)[:300]
        message = data.get("message")
        if message:
            return str(message)[:300]
    return str(data)[:300]


def check_model_connectivity() -> dict[str, Any]:
    env = pipeline_env()
    llm_model = env.get("LLM_DEFAULT_MODEL") or env.get("DEEPSEEK_MODEL") or ""
    vision_model = env.get("VISION_MODEL") or env.get("DASHSCOPE_VISION_MODEL") or ""
    checks = {
        "llm": _probe_chat_model(
            "deepseek",
            env.get("DEEPSEEK_BASE_URL", ""),
            env.get("DEEPSEEK_API_KEY", ""),
            llm_model,
        ),
        "vision": _probe_chat_model(
            "dashscope",
            env.get("DASHSCOPE_BASE_URL", ""),
            env.get("DASHSCOPE_API_KEY", ""),
            vision_model,
        ),
    }
    return {"ok": all(item.get("ok") or item.get("skipped") for item in checks.values()), "checks": checks}


def check_minio_contract(create_missing: bool = False) -> dict[str, Any]:
    config = load_runtime_config(include_secrets=True)
    client = minio_client_from_config(config)
    buckets = config.get("minio", {}).get("contract_buckets") or CONTRACT_BUCKETS
    rows = []
    connected = True
    error = ""
    for bucket in buckets:
        bucket_name = str(bucket)
        try:
            exists = client.bucket_exists(bucket_name)
            created = False
            if create_missing and not exists:
                client.make_bucket(bucket_name)
                exists = True
                created = True
            rows.append({"bucket": bucket_name, "exists": exists, "created": created, "role": bucket_role(bucket_name)})
        except Exception as exc:
            connected = False
            error = str(exc)
            rows.append({"bucket": bucket_name, "exists": False, "created": False, "role": bucket_role(bucket_name), "error": str(exc)})
    missing = [row["bucket"] for row in rows if not row.get("exists")]
    return {
        "connected": connected,
        "endpoint": sanitize_config(config).get("minio", {}).get("endpoint"),
        "buckets": rows,
        "missing": missing,
        "contract_ok": connected and not missing,
        "error": error,
    }


def bucket_role(bucket: str) -> str:
    return {
        "eduassets-input": "source_pdf",
        "eduassets-mineru": "mineru_official",
        "eduassets-minerupopo": "popo_official",
        "eduassets-raw": "raw_master",
        "eduassets-clean": "clean_candidate",
        "eduassets-standard": "standard_master",
        "eduassets-parsed": "archive_optional",
        "eduassets-elegantbook": "elegantbook_output",
        "eduassets-review": "review_evidence",
        "eduassets-latex": "legacy_latex",
    }.get(bucket, "unknown")


def check_gpu_runtime() -> dict[str, Any]:
    config = load_runtime_config(include_secrets=True)
    gpu = config.get("gpu", {})
    wrapper_url = str(gpu.get("wrapper_url") or "").rstrip("/")
    result: dict[str, Any] = {
        "wrapper_url": wrapper_url,
        "wrapper_ok": False,
        "staged_api_ok": False,
        "state": "offline",
        "health": {},
        "staged_api": {},
        "errors": [],
    }
    headers = {"Authorization": "Bearer " + str(gpu.get("api_key"))} if gpu.get("api_key") else {}
    if not wrapper_url:
        result["errors"].append("wrapper_url is empty")
        return result

    paths = {
        "health": "/api/v1/health",
        "mineru_batches": "/api/v1/mineru/batches",
        "mineru_results_probe": "/api/v1/mineru/results/__probe__",
        "popo_batches": "/api/v1/popo/batches",
        "popo_results_probe": "/api/v1/popo/results/__probe__",
    }

    def probe(name: str, path: str) -> tuple[str, int | None, Any, str]:
        try:
            response = httpx.get(f"{wrapper_url}{path}", headers=headers if name != "health" else {}, timeout=5)
            return name, response.status_code, _safe_json(response) if name == "health" else None, ""
        except Exception as exc:
            return name, None, None, str(exc)

    with ThreadPoolExecutor(max_workers=len(paths)) as pool:
        rows = list(pool.map(lambda item: probe(*item), paths.items()))
    codes = {}
    for name, status_code, body, error in rows:
        if name == "health":
            result["health"] = {"status_code": status_code, "body": body}
            result["wrapper_ok"] = status_code == 200
        else:
            codes[name] = status_code
        if error:
            result["errors"].append(f"{name}: {error}")
    result["staged_api"] = codes
    result["staged_api_ok"] = (
        codes.get("mineru_batches") in (200, 405)
        and codes.get("popo_batches") in (200, 405)
        and codes.get("mineru_results_probe") in (200, 404)
        and codes.get("popo_results_probe") in (200, 404)
    )
    result["state"] = "ready" if result["wrapper_ok"] and result["staged_api_ok"] else "degraded" if result["wrapper_ok"] else "offline"
    return result


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text[:500]


def check_backup_targets() -> dict[str, Any]:
    config = load_runtime_config(include_secrets=True)
    targets = []
    for target in config.get("backup", {}).get("targets", []):
        row = dict(target)
        path = Path(str(row.get("path") or ""))
        enabled = bool(row.get("enabled"))
        row["exists"] = path.exists() if row.get("path") else False
        row["writable"] = _is_writable(path) if enabled and row.get("path") else False
        row["status"] = "ready" if enabled and row["writable"] else "disabled" if not enabled else "unavailable"
        targets.append(row)
    return {
        "targets": targets,
        "ready_count": sum(1 for item in targets if item["status"] == "ready"),
        "external_ready_count": sum(1 for item in targets if item["status"] == "ready" and item.get("external")),
    }


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".luceon-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def runtime_config_validation() -> dict[str, Any]:
    config = load_runtime_config(include_secrets=True)
    errors: list[str] = []
    warnings: list[str] = []
    minio = config.get("minio", {})
    gpu = config.get("gpu", {})
    backup = config.get("backup", {})
    if int(config.get("schema_version") or 0) != RUNTIME_CONFIG_SCHEMA_VERSION:
        errors.append("runtime_config_schema")
    if not str(minio.get("endpoint") or "").strip():
        errors.append("minio_internal_endpoint")
    public_endpoint = str(minio.get("public_endpoint") or "").strip()
    if not public_endpoint or urlparse(public_endpoint).scheme not in {"http", "https"}:
        errors.append("minio_public_endpoint")
    if not minio.get("access_key") or not minio.get("secret_key"):
        errors.append("minio_credentials")
    if list(minio.get("contract_buckets") or []) != list(CURRENT_ASSET_BUCKETS):
        errors.append("minio_contract_schema")
    if not str(gpu.get("wrapper_url") or "").strip():
        warnings.append("gpu_wrapper_url")
    if backup.get("enabled"):
        if str(backup.get("mode") or "") == "manifest":
            warnings.append("backup_manifest_only")
        if not any(row.get("enabled") and row.get("external") for row in backup.get("targets", [])):
            warnings.append("external_backup_disabled")
    else:
        warnings.append("backup_disabled")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "schema_version": RUNTIME_CONFIG_SCHEMA_VERSION}


def active_gpu_task_count() -> int:
    from app.database import SessionLocal
    from app.models.material import PipelineRun

    db = SessionLocal()
    try:
        return int(db.query(PipelineRun).filter(PipelineRun.status.in_(["queued", "running"])).count())
    finally:
        db.close()


def _overleaf_health() -> dict[str, Any]:
    container = os.getenv("WORKFLOW_V2_TEX_CONTAINER", "sharelatex").strip()
    if not container:
        return {"ready": False, "detail": "WORKFLOW_V2_TEX_CONTAINER is not configured"}
    try:
        completed = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container],
            text=True,
            capture_output=True,
            timeout=4,
        )
        ready = completed.returncode == 0 and completed.stdout.strip().lower() == "true"
        return {"ready": ready, "container": container, "detail": "running" if ready else "not_running"}
    except Exception as exc:
        return {"ready": False, "container": container, "detail": str(exc)[:200]}


def check_runtime_dependencies() -> dict[str, Any]:
    from app.database import engine
    from app.services.runtime_health import runtime_worker_health
    from app.utils.redis_client import redis_client
    from app.workflow_v2.database import workflow_database_health

    checks: dict[str, Any] = {}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["sqlite"] = {"ready": True}
    except Exception as exc:
        checks["sqlite"] = {"ready": False, "detail": str(exc)[:200]}
    try:
        checks["redis"] = {"ready": bool(redis_client.client and redis_client.client.ping())}
    except Exception as exc:
        checks["redis"] = {"ready": False, "detail": str(exc)[:200]}
    workflow = workflow_database_health()
    checks["workflow_database"] = {**workflow, "ready": bool(workflow.get("ready"))}
    checks["material_worker"] = runtime_worker_health("material_task")
    checks["workflow_worker"] = runtime_worker_health("workflow_v2")
    checks["backup_worker"] = runtime_worker_health("backup")
    checks["overleaf"] = _overleaf_health()
    return {"ready": all(bool(item.get("ready")) for item in checks.values()), "checks": checks}


def runtime_status() -> dict[str, Any]:
    probes = {
        "minio": lambda: check_minio_contract(create_missing=False),
        "gpu": check_gpu_runtime,
        "backup": check_backup_targets,
        "models": check_model_runtime,
        "dependencies": check_runtime_dependencies,
        "config": runtime_config_validation,
        "active_gpu_tasks": active_gpu_task_count,
    }
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        futures = {name: pool.submit(callback) for name, callback in probes.items()}
        results = {name: future.result() for name, future in futures.items()}
    minio = results["minio"]
    gpu = results["gpu"]
    tunnel_status_path = Path(os.getenv("GPU_TUNNEL_STATUS_FILE", "/data/gpu-wrapper-tunnel-status.json"))
    tunnel_status: dict[str, Any] = {"auto_recovery": False, "last_recovery_at": None}
    if tunnel_status_path.is_file():
        try:
            value = json.loads(tunnel_status_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                tunnel_status = value
        except (OSError, json.JSONDecodeError):
            pass
    backup = results["backup"]
    models = results["models"]
    dependencies = results["dependencies"]
    config = results["config"]
    active_tasks = int(results["active_gpu_tasks"] or 0)
    blockers = []
    warnings = list(config.get("warnings") or [])
    if not minio.get("contract_ok"):
        blockers.append("minio_contract")
    if not config.get("ok"):
        blockers.extend(str(item) for item in config.get("errors") or [])
    if not dependencies.get("ready"):
        blockers.append("runtime_dependencies")
    if active_tasks > 0:
        if not gpu.get("wrapper_ok"):
            blockers.append("gpu_wrapper")
        if not gpu.get("staged_api_ok"):
            blockers.append("gpu_staged_api")
    elif not gpu.get("wrapper_ok"):
        gpu["state"] = "expected_off"
    elif not gpu.get("staged_api_ok"):
        warnings.append("gpu_staged_api")
    if models.get("llm", {}).get("enabled") and not models.get("llm", {}).get("api_key_configured"):
        blockers.append("llm_model_key")
    if models.get("vision", {}).get("enabled") and not models.get("vision", {}).get("api_key_configured"):
        blockers.append("vision_model_key")
    if backup.get("ready_count", 0) <= 0:
        warnings.append("backup_target")
    status = "blocked" if blockers else "warning" if warnings else "ready"
    return {
        "status": status,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "minio": minio,
        "gpu": {**gpu, "active_task_count": active_tasks, "tunnel": tunnel_status},
        "backup": backup,
        "models": models,
        "dependencies": dependencies,
        "config": config,
        "active_gpu_tasks": active_tasks,
    }
