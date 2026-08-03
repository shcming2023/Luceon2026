from __future__ import annotations

import hashlib
import json
import re
import tarfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

import httpx

try:
    from .stage_entrypoint import StageEntrypointError, sha256_file
except ImportError:  # Release-local scripts import this module directly.
    from stage_entrypoint import StageEntrypointError, sha256_file  # type: ignore[no-redef]


TARGET_ENVIRONMENT_SCHEMA = "luceon.worker-v3-overleaf-target-environment/v1"
ADAPTER_PROTOCOL = "luceon.worker-v3-overleaf-compiler/v1"
RESULT_SCHEMA = "luceon.worker-v3-overleaf-compile-result/v1"
PINNED_OVERLEAF_BASE_IMAGE = (
    "ghcr.io/lcpu-club/sharelatex@"
    "sha256:633e180fa9357c2b00e2fa9234b63460033fd2d4e4c441a5bb91c9697a08e145"
)
COMPILE_COMMAND = (
    "latexmk",
    "-xelatex",
    "-interaction=nonstopmode",
    "-halt-on-error",
    "-file-line-error",
    "-no-shell-escape",
    "main.tex",
)
MAX_ZIP_BYTES = 50_000_000
MAX_IMAGE_BYTES = 1_000_000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RESULT_MEMBERS = frozenset({"result-manifest.json", "main.pdf", "main.log"})


@dataclass(frozen=True)
class OverleafCompileEvidence:
    zip_sha256: str
    pdf_path: Path
    pdf_sha256: str
    page_count: int
    log_path: Path
    log: str
    xelatex_version: str
    latexmk_version: str
    runtime_identity_sha256: str
    adapter_image_digest: str
    result_manifest_path: Path
    result_manifest_sha256: str
    request_id: str


def validate_target_environment(raw: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "status",
        "provider",
        "protocol",
        "engine",
        "endpoint",
        "base_image",
        "adapter_image_digest",
        "adapter_runtime_identity_sha256",
        "adapter_source_sha256",
        "compiler_command",
        "limits",
    }
    if set(raw) != required:
        raise StageEntrypointError(
            "overleaf_target_environment_invalid",
            "Overleaf target environment has missing or unknown fields",
            exit_code=3,
        )
    endpoint = raw.get("endpoint")
    parsed = urlparse(endpoint if isinstance(endpoint, str) else "")
    limits = raw.get("limits")
    command = raw.get("compiler_command")
    if (
        raw.get("schema_version") != TARGET_ENVIRONMENT_SCHEMA
        or raw.get("status") != "approved"
        or raw.get("provider") != "luceon-overleaf-compiler-adapter"
        or raw.get("protocol") != ADAPTER_PROTOCOL
        or raw.get("engine") != "xelatex"
        or raw.get("base_image") != PINNED_OVERLEAF_BASE_IMAGE
        or not _image_digest(raw.get("adapter_image_digest"))
        or not _sha256(raw.get("adapter_runtime_identity_sha256"))
        or not _sha256(raw.get("adapter_source_sha256"))
        or command != list(COMPILE_COMMAND)
        or parsed.scheme != "http"
        or parsed.hostname != "workflow-v3-overleaf-compiler"
        or parsed.port != 8080
        or parsed.path != "/compile"
        or parsed.params
        or parsed.query
        or parsed.fragment
        or not isinstance(limits, dict)
        or set(limits)
        != {
            "max_zip_bytes",
            "max_image_bytes",
            "allowed_root_files",
            "allowed_asset_directories",
            "allowed_body_files",
            "allowed_body_directories",
        }
        or limits.get("max_zip_bytes") != MAX_ZIP_BYTES
        or limits.get("max_image_bytes") != MAX_IMAGE_BYTES
        or limits.get("allowed_root_files")
        != ["main.tex", "elegantbook.cls", "reference.bib"]
        or limits.get("allowed_asset_directories") != ["figure", "images"]
        or limits.get("allowed_body_files") != ["body/generated-body.tex"]
        or limits.get("allowed_body_directories") != ["body/units"]
    ):
        raise StageEntrypointError(
            "overleaf_target_environment_invalid",
            "Overleaf target environment is not the approved immutable profile",
            exit_code=3,
        )
    return json.loads(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def load_release_target_environment(release_root: Path) -> dict[str, Any]:
    manifest_path = _contained_file(release_root, "release-manifest.json")
    manifest = _json_object(manifest_path, "release manifest")
    runtime = manifest.get("runtime")
    system_tools = runtime.get("system_tools") if isinstance(runtime, dict) else None
    binding = (
        system_tools.get("overleaf_compiler")
        if isinstance(system_tools, dict)
        else None
    )
    if not isinstance(binding, dict) or set(binding) != {
        "profile_path",
        "profile_sha256",
    }:
        raise StageEntrypointError(
            "overleaf_release_binding_missing",
            "release has no immutable Overleaf compiler profile",
            exit_code=3,
        )
    profile_path = _contained_file(release_root, binding.get("profile_path"))
    if binding.get("profile_sha256") != sha256_file(profile_path):
        raise StageEntrypointError(
            "overleaf_release_binding_mismatch",
            "release Overleaf compiler profile differs from its manifest binding",
            exit_code=3,
        )
    return validate_target_environment(
        _json_object(profile_path, "Overleaf target environment")
    )


def compile_overleaf_delivery(
    zip_path: Path,
    workdir: Path,
    *,
    target_environment: Mapping[str, Any],
    role: str,
    client_factory: Callable[..., Any] = httpx.Client,
) -> OverleafCompileEvidence:
    target = validate_target_environment(target_environment)
    if role not in {"producer", "independent_evaluator"}:
        raise StageEntrypointError(
            "overleaf_compile_role_invalid",
            "Overleaf compile role is not admitted",
            exit_code=3,
        )
    if not zip_path.is_file() or zip_path.is_symlink():
        raise StageEntrypointError(
            "overleaf_delivery_zip_missing",
            "Overleaf compile input is not a regular ZIP",
            exit_code=3,
        )
    size_bytes = zip_path.stat().st_size
    if size_bytes >= MAX_ZIP_BYTES:
        raise StageEntrypointError(
            "overleaf_delivery_zip_too_large",
            "Overleaf compile input must be strictly smaller than 50 MB",
            exit_code=3,
        )
    zip_sha256 = sha256_file(zip_path)
    image_inventory = _zip_image_inventory(zip_path)
    inventory_sha256 = _canonical_sha256(image_inventory)
    if workdir.exists() or workdir.is_symlink():
        raise StageEntrypointError(
            "overleaf_compile_workspace_exists",
            "Overleaf compile workspace already exists",
            exit_code=3,
        )
    workdir.mkdir(parents=True, mode=0o700)
    response_archive = workdir / "adapter-result.tar.gz"
    request_id = uuid.uuid4().hex
    headers = {
        "Content-Type": "application/zip",
        "Content-Length": str(size_bytes),
        "X-Luceon-Protocol": ADAPTER_PROTOCOL,
        "X-Luceon-Request-Id": request_id,
        "X-Luceon-Role": role,
        "X-Luceon-Input-Sha256": zip_sha256,
        "X-Luceon-Input-Size": str(size_bytes),
        "X-Luceon-Image-Inventory-Sha256": inventory_sha256,
        "X-Luceon-Expected-Runtime-Sha256": str(
            target["adapter_runtime_identity_sha256"]
        ),
    }
    timeout = httpx.Timeout(connect=10.0, read=1_260.0, write=120.0, pool=10.0)
    try:
        payload = zip_path.read_bytes()
        with client_factory(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream(
                "POST",
                str(target["endpoint"]),
                headers=headers,
                content=payload,
            ) as response:
                if response.status_code != 200:
                    raise StageEntrypointError(
                        "overleaf_adapter_http_error",
                        f"Overleaf compiler returned HTTP {response.status_code}",
                        exit_code=3,
                    )
                runtime_header = response.headers.get(
                    "X-Luceon-Runtime-Identity-Sha256"
                )
                if runtime_header != target["adapter_runtime_identity_sha256"]:
                    raise StageEntrypointError(
                        "overleaf_runtime_identity_mismatch",
                        "Overleaf compiler runtime differs from the release binding",
                        exit_code=3,
                    )
                declared_body_sha256 = response.headers.get(
                    "X-Luceon-Result-Sha256"
                )
                digest = hashlib.sha256()
                bytes_written = 0
                with response_archive.open("xb") as output:
                    for chunk in response.iter_bytes():
                        bytes_written += len(chunk)
                        if bytes_written > 1_500_000_000:
                            raise StageEntrypointError(
                                "overleaf_result_too_large",
                                "Overleaf compiler result exceeds its response limit",
                                exit_code=3,
                            )
                        digest.update(chunk)
                        output.write(chunk)
                if (
                    not _sha256(declared_body_sha256)
                    or digest.hexdigest() != declared_body_sha256
                ):
                    raise StageEntrypointError(
                        "overleaf_result_hash_mismatch",
                        "Overleaf compiler response differs from its declared hash",
                        exit_code=3,
                    )
    except StageEntrypointError:
        raise
    except httpx.TimeoutException as exc:
        raise StageEntrypointError(
            "overleaf_adapter_timeout",
            "Overleaf compiler timed out",
            exit_code=3,
        ) from exc
    except httpx.HTTPError as exc:
        raise StageEntrypointError(
            "overleaf_adapter_unavailable",
            "Overleaf compiler is unavailable",
            exit_code=3,
        ) from exc
    except OSError as exc:
        raise StageEntrypointError(
            "overleaf_adapter_io_failed",
            "Overleaf compiler request or response I/O failed",
            exit_code=3,
        ) from exc
    result_root = workdir / "result"
    _extract_result_archive(response_archive, result_root)
    manifest_path = result_root / "result-manifest.json"
    manifest = _json_object(manifest_path, "Overleaf result manifest")
    _validate_result_manifest(
        manifest,
        result_root=result_root,
        target=target,
        request_id=request_id,
        role=role,
        zip_sha256=zip_sha256,
        size_bytes=size_bytes,
        inventory_sha256=inventory_sha256,
        image_inventory=image_inventory,
    )
    log_path = result_root / "main.log"
    return OverleafCompileEvidence(
        zip_sha256=zip_sha256,
        pdf_path=result_root / "main.pdf",
        pdf_sha256=sha256_file(result_root / "main.pdf"),
        page_count=int(manifest["output"]["pdf"]["page_count"]),
        log_path=log_path,
        log=log_path.read_text(encoding="utf-8", errors="replace"),
        xelatex_version=str(manifest["runtime"]["xelatex_version"]),
        latexmk_version=str(manifest["runtime"]["latexmk_version"]),
        runtime_identity_sha256=str(
            manifest["runtime"]["runtime_identity_sha256"]
        ),
        adapter_image_digest=str(manifest["runtime"]["adapter_image_digest"]),
        result_manifest_path=manifest_path,
        result_manifest_sha256=sha256_file(manifest_path),
        request_id=request_id,
    )


def _validate_result_manifest(
    manifest: Mapping[str, Any],
    *,
    result_root: Path,
    target: Mapping[str, Any],
    request_id: str,
    role: str,
    zip_sha256: str,
    size_bytes: int,
    inventory_sha256: str,
    image_inventory: list[dict[str, Any]],
) -> None:
    runtime = manifest.get("runtime")
    input_row = manifest.get("input")
    output = manifest.get("output")
    pdf = output.get("pdf") if isinstance(output, dict) else None
    log = output.get("log") if isinstance(output, dict) else None
    if (
        manifest.get("schema_version") != RESULT_SCHEMA
        or manifest.get("protocol") != ADAPTER_PROTOCOL
        or manifest.get("status") != "passed"
        or manifest.get("request_id") != request_id
        or manifest.get("role") != role
        or manifest.get("command") != list(COMPILE_COMMAND)
        or manifest.get("exit_status") != 0
        or not isinstance(input_row, dict)
        or input_row.get("sha256") != zip_sha256
        or input_row.get("size_bytes") != size_bytes
        or input_row.get("image_inventory_sha256") != inventory_sha256
        or input_row.get("image_count") != len(image_inventory)
        or input_row.get("images") != image_inventory
        or not isinstance(runtime, dict)
        or runtime.get("base_image") != PINNED_OVERLEAF_BASE_IMAGE
        or runtime.get("adapter_image_digest") != target["adapter_image_digest"]
        or runtime.get("runtime_identity_sha256")
        != target["adapter_runtime_identity_sha256"]
        or runtime.get("adapter_source_sha256") != target["adapter_source_sha256"]
        or not isinstance(runtime.get("xelatex_version"), str)
        or not runtime["xelatex_version"]
        or not isinstance(runtime.get("latexmk_version"), str)
        or not runtime["latexmk_version"]
        or not isinstance(pdf, dict)
        or not isinstance(log, dict)
    ):
        raise StageEntrypointError(
            "overleaf_result_manifest_invalid",
            "Overleaf compiler returned an invalid or drifted result manifest",
            exit_code=3,
        )
    pdf_path = _contained_file(result_root, pdf.get("path"))
    log_path = _contained_file(result_root, log.get("path"))
    if (
        pdf_path.name != "main.pdf"
        or log_path.name != "main.log"
        or pdf.get("sha256") != sha256_file(pdf_path)
        or pdf.get("size_bytes") != pdf_path.stat().st_size
        or not isinstance(pdf.get("page_count"), int)
        or isinstance(pdf.get("page_count"), bool)
        or pdf.get("page_count") < 1
        or log.get("sha256") != sha256_file(log_path)
        or log.get("size_bytes") != log_path.stat().st_size
        or not pdf_path.read_bytes().startswith(b"%PDF-")
    ):
        raise StageEntrypointError(
            "overleaf_result_artifact_invalid",
            "Overleaf compiler output artifacts differ from their manifest",
            exit_code=3,
        )


def _zip_image_inventory(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                name = info.filename[:-1] if info.filename.endswith("/") else info.filename
                if not name or info.is_dir():
                    continue
                relative = PurePosixPath(name)
                if (
                    relative.parts
                    and relative.parts[0] in {"images", "figure"}
                ):
                    rows.append(
                        {
                            "path": name,
                            "size_bytes": info.file_size,
                            "sha256": hashlib.sha256(archive.read(info)).hexdigest(),
                        }
                    )
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise StageEntrypointError(
            "overleaf_delivery_zip_invalid",
            "Overleaf compile input is not a readable ZIP",
            exit_code=3,
        ) from exc
    return sorted(rows, key=lambda row: str(row["path"]))


def _extract_result_archive(path: Path, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    seen: set[str] = set()
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                if (
                    member.name not in _RESULT_MEMBERS
                    or member.name in seen
                    or not member.isfile()
                    or member.issym()
                    or member.islnk()
                ):
                    raise StageEntrypointError(
                        "overleaf_result_archive_invalid",
                        "Overleaf result archive contains an unsafe member",
                        exit_code=3,
                    )
                seen.add(member.name)
                source = archive.extractfile(member)
                if source is None:
                    raise StageEntrypointError(
                        "overleaf_result_archive_invalid",
                        "Overleaf result archive member is unreadable",
                        exit_code=3,
                    )
                target = destination / member.name
                payload = source.read(1_500_000_001)
                if len(payload) > 1_500_000_000:
                    raise StageEntrypointError(
                        "overleaf_result_too_large",
                        "Overleaf result artifact exceeds its size limit",
                        exit_code=3,
                    )
                target.write_bytes(payload)
                target.chmod(0o600)
    except StageEntrypointError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise StageEntrypointError(
            "overleaf_result_archive_invalid",
            "Overleaf result archive is unreadable",
            exit_code=3,
        ) from exc
    if seen != _RESULT_MEMBERS:
        raise StageEntrypointError(
            "overleaf_result_archive_invalid",
            "Overleaf result archive is incomplete",
            exit_code=3,
        )


def _contained_file(root: Path, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw or raw.startswith("/") or "\\" in raw:
        raise StageEntrypointError(
            "overleaf_path_invalid",
            "Overleaf evidence path is invalid",
            exit_code=3,
        )
    relative = PurePosixPath(raw)
    if str(relative) != raw or any(part in {"", ".", ".."} for part in relative.parts):
        raise StageEntrypointError(
            "overleaf_path_invalid",
            "Overleaf evidence path is not normalized",
            exit_code=3,
        )
    path = (root / relative).resolve()
    if root.resolve() not in path.parents or not path.is_file() or path.is_symlink():
        raise StageEntrypointError(
            "overleaf_path_invalid",
            "Overleaf evidence path is missing or unsafe",
            exit_code=3,
        )
    return path


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StageEntrypointError(
            "overleaf_json_invalid",
            f"{label} is not valid UTF-8 JSON",
            exit_code=3,
        ) from exc
    if not isinstance(value, dict):
        raise StageEntrypointError(
            "overleaf_json_invalid",
            f"{label} must be a JSON object",
            exit_code=3,
        )
    return value


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _image_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and _sha256(value.removeprefix("sha256:"))
    )


__all__ = [
    "ADAPTER_PROTOCOL",
    "COMPILE_COMMAND",
    "MAX_IMAGE_BYTES",
    "MAX_ZIP_BYTES",
    "OverleafCompileEvidence",
    "PINNED_OVERLEAF_BASE_IMAGE",
    "RESULT_SCHEMA",
    "TARGET_ENVIRONMENT_SCHEMA",
    "compile_overleaf_delivery",
    "load_release_target_environment",
    "validate_target_environment",
]
