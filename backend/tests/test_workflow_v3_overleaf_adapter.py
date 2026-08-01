from __future__ import annotations

import gzip
import hashlib
import http.client
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.workflow_v3.overleaf_compiler import (
    ADAPTER_PROTOCOL,
    COMPILE_COMMAND,
    MAX_IMAGE_BYTES,
    MAX_ZIP_BYTES,
    PINNED_OVERLEAF_BASE_IMAGE,
    RESULT_SCHEMA,
    TARGET_ENVIRONMENT_SCHEMA,
    compile_overleaf_delivery,
    validate_target_environment,
)
from app.workflow_v3.stage_entrypoint import StageEntrypointError


ROOT = Path(__file__).resolve().parents[2]
SERVICE_PATH = ROOT / "backend/overleaf-adapter/compiler_service.py"
DOCKERFILE = ROOT / "backend/Dockerfile.overleaf-adapter"
TARGET_PROFILE_PATH = (
    ROOT / "release/worker-v3/runtime/overleaf-target-environment.json"
)
RELEASE_RECIPE_PATH = ROOT / "release/worker-v3/recipe.current-audit.json"
SERVICE_SPEC = importlib.util.spec_from_file_location(
    "workflow_v3_overleaf_compiler_service",
    SERVICE_PATH,
)
assert SERVICE_SPEC and SERVICE_SPEC.loader
SERVICE = importlib.util.module_from_spec(SERVICE_SPEC)
sys.modules[SERVICE_SPEC.name] = SERVICE
SERVICE_SPEC.loader.exec_module(SERVICE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _target(
    *,
    image_digest: str = "sha256:" + "1" * 64,
    runtime_sha256: str = "2" * 64,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": TARGET_ENVIRONMENT_SCHEMA,
        "status": "approved",
        "provider": "luceon-overleaf-compiler-adapter",
        "protocol": ADAPTER_PROTOCOL,
        "engine": "xelatex",
        "endpoint": "http://workflow-v3-overleaf-compiler:8080/compile",
        "base_image": PINNED_OVERLEAF_BASE_IMAGE,
        "adapter_image_digest": image_digest,
        "adapter_runtime_identity_sha256": runtime_sha256,
        "adapter_source_sha256": source_sha256 or _sha(SERVICE_PATH),
        "compiler_command": list(COMPILE_COMMAND),
        "limits": {
            "max_zip_bytes": MAX_ZIP_BYTES,
            "max_image_bytes": MAX_IMAGE_BYTES,
            "allowed_root_files": [
                "main.tex",
                "elegantbook.cls",
                "reference.bib",
            ],
            "allowed_asset_directories": ["figure", "images"],
            "allowed_body_files": ["body/generated-body.tex"],
            "allowed_body_directories": ["body/units"],
        },
    }


def _delivery(path: Path, *, unsafe: str | None = None) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "main.tex",
            "\\documentclass{elegantbook}\\begin{document}Bound\\end{document}\n",
        )
        archive.writestr(
            "elegantbook.cls",
            "\\NeedsTeXFormat{LaTeX2e}\\ProvidesClass{elegantbook}"
            "\\LoadClass{article}\n",
        )
        archive.writestr("figure/logo.png", b"image")
        if unsafe:
            archive.writestr(unsafe, b"unsafe")


def _formal_delivery(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "main.tex",
            "\\documentclass{elegantbook}\\begin{document}"
            "\\input{body/generated-body.tex}\\end{document}\n",
        )
        archive.writestr(
            "elegantbook.cls",
            "\\NeedsTeXFormat{LaTeX2e}\\ProvidesClass{elegantbook}"
            "\\LoadClass{article}\n",
        )
        archive.writestr("reference.bib", "")
        archive.writestr(
            "body/generated-body.tex",
            "\\input{body/units/unit-0001/part-0001.tex}\n",
        )
        archive.writestr(
            "body/units/unit-0001/part-0001.tex",
            "Source-faithful formal body.\n",
        )
        archive.writestr("figure/logo.png", b"image")


def _result_archive(
    *,
    headers: dict[str, str],
    target: dict[str, Any],
    runtime_override: str | None = None,
) -> bytes:
    pdf = b"%PDF-1.4\nfixture\n"
    log = b"compile log\n"
    manifest = {
        "schema_version": RESULT_SCHEMA,
        "protocol": ADAPTER_PROTOCOL,
        "status": "passed",
        "request_id": headers["X-Luceon-Request-Id"],
        "role": headers["X-Luceon-Role"],
        "input": {
            "sha256": headers["X-Luceon-Input-Sha256"],
            "size_bytes": int(headers["X-Luceon-Input-Size"]),
            "image_inventory_sha256": headers[
                "X-Luceon-Image-Inventory-Sha256"
            ],
            "image_count": 1,
            "images": [
                {
                    "path": "figure/logo.png",
                    "size_bytes": 5,
                    "sha256": hashlib.sha256(b"image").hexdigest(),
                }
            ],
        },
        "runtime": {
            "schema_version": "luceon.worker-v3-overleaf-runtime-identity/v1",
            "base_image": PINNED_OVERLEAF_BASE_IMAGE,
            "adapter_image_digest": target["adapter_image_digest"],
            "adapter_source_sha256": target["adapter_source_sha256"],
            "command": list(COMPILE_COMMAND),
            "xelatex_version": "XeTeX 0.999997 (TeX Live 2025)",
            "latexmk_version": "Latexmk, 4.86a",
            "effective_uid": 10004,
            "architecture": "aarch64",
            "runtime_identity_sha256": runtime_override
            or target["adapter_runtime_identity_sha256"],
        },
        "command": list(COMPILE_COMMAND),
        "exit_status": 0,
        "duration_ms": 1,
        "output": {
            "pdf": {
                "path": "main.pdf",
                "sha256": hashlib.sha256(pdf).hexdigest(),
                "size_bytes": len(pdf),
                "page_count": 1,
            },
            "log": {
                "path": "main.log",
                "sha256": hashlib.sha256(log).hexdigest(),
                "size_bytes": len(log),
            },
        },
    }
    payloads = {
        "result-manifest.json": (
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
        "main.pdf": pdf,
        "main.log": log,
    }
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as archive:
            for name, payload in payloads.items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


class _Response:
    def __init__(self, status: int, headers: dict[str, str], body: bytes):
        self.status_code = status
        self.headers = headers
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self):
        yield self._body


class _Client:
    def __init__(
        self,
        *,
        target: dict[str, Any],
        status: int = 200,
        runtime_override: str | None = None,
        **_kwargs,
    ):
        self.target = target
        self.status = status
        self.runtime_override = runtime_override

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stream(self, _method, _url, *, headers, content):
        assert isinstance(content, bytes)
        body = _result_archive(
            headers=headers,
            target=self.target,
            runtime_override=self.runtime_override,
        )
        return _Response(
            self.status,
            {
                "X-Luceon-Runtime-Identity-Sha256": self.target[
                    "adapter_runtime_identity_sha256"
                ],
                "X-Luceon-Result-Sha256": hashlib.sha256(body).hexdigest(),
            },
            body,
        )


def test_target_profile_is_exact_and_rejects_mutable_or_local_runtime() -> None:
    assert validate_target_environment(_target())["engine"] == "xelatex"
    for field, value in (
        ("status", "unqualified"),
        ("base_image", "ghcr.io/lcpu-club/sharelatex:latest"),
        ("adapter_image_digest", "luceon-overleaf:latest"),
        ("endpoint", "http://sharelatex:80/project"),
    ):
        drifted = _target()
        drifted[field] = value
        with pytest.raises(StageEntrypointError, match="target environment"):
            validate_target_environment(drifted)


def test_release_recipe_binds_current_overleaf_target_profile_bytes() -> None:
    profile_bytes = TARGET_PROFILE_PATH.read_bytes()
    profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
    profile = json.loads(profile_bytes)
    recipe = json.loads(RELEASE_RECIPE_PATH.read_text(encoding="utf-8"))
    source = next(
        row
        for row in recipe["sources"]
        if row["id"] == "worker-v3-overleaf-target-environment"
    )

    assert source["expected_sha256"] == profile_sha256
    assert (
        recipe["runtime"]["system_tools"]["overleaf_compiler"][
            "profile_sha256"
        ]
        == profile_sha256
    )
    assert profile["adapter_source_sha256"] == hashlib.sha256(
        SERVICE_PATH.read_bytes()
    ).hexdigest()
    assert profile["adapter_image_digest"].startswith("sha256:")
    assert profile["adapter_runtime_identity_sha256"] != "0" * 64
    assert profile["status"] in {"unqualified", "approved"}


def test_client_binds_zip_runtime_and_result_manifest(tmp_path: Path) -> None:
    delivery = tmp_path / "delivery.zip"
    _delivery(delivery)
    target = _target()
    evidence = compile_overleaf_delivery(
        delivery,
        tmp_path / "compile",
        target_environment=target,
        role="producer",
        client_factory=lambda **kwargs: _Client(target=target, **kwargs),
    )
    assert evidence.zip_sha256 == _sha(delivery)
    assert evidence.runtime_identity_sha256 == target[
        "adapter_runtime_identity_sha256"
    ]
    assert evidence.adapter_image_digest == target["adapter_image_digest"]
    assert evidence.pdf_path.read_bytes().startswith(b"%PDF-")
    assert evidence.page_count == 1


def test_client_fails_closed_on_http_or_runtime_drift(tmp_path: Path) -> None:
    delivery = tmp_path / "delivery.zip"
    _delivery(delivery)
    target = _target()
    with pytest.raises(StageEntrypointError, match="HTTP 502"):
        compile_overleaf_delivery(
            delivery,
            tmp_path / "http-failed",
            target_environment=target,
            role="producer",
            client_factory=lambda **kwargs: _Client(
                target=target,
                status=502,
                **kwargs,
            ),
        )
    with pytest.raises(StageEntrypointError, match="invalid or drifted"):
        compile_overleaf_delivery(
            delivery,
            tmp_path / "runtime-drift",
            target_environment=target,
            role="producer",
            client_factory=lambda **kwargs: _Client(
                target=target,
                runtime_override="3" * 64,
                **kwargs,
            ),
        )


@pytest.mark.parametrize(
    "unsafe",
    (
        "../escape.tex",
        "body/unapproved.tex",
        "images/not-an-image.txt",
        "other.bib",
    ),
)
def test_service_secure_extraction_rejects_unapproved_members(
    tmp_path: Path,
    unsafe: str,
) -> None:
    delivery = tmp_path / "delivery.zip"
    _delivery(delivery, unsafe=unsafe)
    with pytest.raises(SERVICE.AdapterError):
        SERVICE._extract_project(delivery, tmp_path / "project")


def test_service_accepts_formal_semantic_body_profile(tmp_path: Path) -> None:
    delivery = tmp_path / "formal-delivery.zip"
    _delivery(delivery)
    with zipfile.ZipFile(delivery, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("reference.bib", "")
        archive.writestr(
            "body/generated-body.tex",
            "\\input{body/units/unit-0001/part-0001.tex}\n",
        )
        archive.writestr(
            "body/units/unit-0001/part-0001.tex",
            "Source-faithful body.\n",
        )
    inventory = SERVICE._extract_project(delivery, tmp_path / "formal-project")
    assert [row["path"] for row in inventory] == ["figure/logo.png"]
    assert (
        tmp_path
        / "formal-project/body/units/unit-0001/part-0001.tex"
    ).read_text(encoding="utf-8") == "Source-faithful body.\n"
    assert (tmp_path / "formal-project/reference.bib").read_bytes() == b""


def test_service_rejects_symlink_and_oversized_image(tmp_path: Path) -> None:
    symlink = tmp_path / "symlink.zip"
    with zipfile.ZipFile(symlink, "w") as archive:
        archive.writestr("main.tex", "main")
        archive.writestr("elegantbook.cls", "class")
        info = zipfile.ZipInfo("images/link.png")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "../../etc/passwd")
    with pytest.raises(SERVICE.AdapterError):
        SERVICE._extract_project(symlink, tmp_path / "symlink-project")

    oversized = tmp_path / "oversized.zip"
    with zipfile.ZipFile(oversized, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("main.tex", "main")
        archive.writestr("elegantbook.cls", "class")
        archive.writestr("images/large.png", b"x" * (MAX_IMAGE_BYTES + 1))
    with pytest.raises(SERVICE.AdapterError):
        SERVICE._extract_project(oversized, tmp_path / "oversized-project")


def test_compiler_image_and_compose_are_isolated_from_existing_overleaf() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.luceon-review.yml").read_text(
        encoding="utf-8"
    )
    assert f"FROM {PINNED_OVERLEAF_BASE_IMAGE}" in dockerfile
    assert 'user: "10004:10004"' in compose
    assert "workflow-v3-overleaf-net:" in compose
    assert "internal: true" in compose
    section = compose.split("  workflow-v3-overleaf-compiler:", 1)[1].split(
        "\n  workflow-v3-promoter:", 1
    )[0]
    assert "/var/run/docker.sock" not in section
    assert "/data" not in section
    assert "MINIO_" not in section
    assert "DATABASE_" not in section
    assert "cap_drop:\n      - ALL" in section
    assert "no-new-privileges:true" in section
    assert "read_only: true" in section
    assert "PAR_GLOBAL_TEMP=/biber-cache" in dockerfile
    assert (
        "/biber-cache:uid=10004,gid=10004,mode=0700,exec,nosuid,nodev,"
        "size=268435456"
    ) in section


def test_service_routes_biber_unpacking_to_bounded_exec_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Process:
        pid = 1234

        def wait(self, *, timeout: int) -> int:
            captured["timeout"] = timeout
            return 0

    def popen(command, *, cwd, env, **_kwargs):
        captured.update(command=command, cwd=cwd, env=env)
        return Process()

    monkeypatch.setattr(SERVICE.subprocess, "Popen", popen)
    project = tmp_path / "project"
    project.mkdir()

    assert SERVICE._run_compile(project) == 0
    assert captured["command"] == list(SERVICE.COMPILE_COMMAND)
    assert captured["cwd"] == project
    assert captured["env"]["TMPDIR"] == str(project / ".tmp")
    assert captured["env"]["PAR_GLOBAL_TEMP"] == SERVICE.BIBER_CACHE_ROOT
    assert captured["timeout"] == SERVICE.COMPILE_TIMEOUT_SECONDS


def test_real_pinned_overleaf_image_compiles_through_adapter(
    tmp_path: Path,
) -> None:
    image = os.environ.get("WORKFLOW_V3_OVERLEAF_ADAPTER_TEST_IMAGE")
    if not image:
        pytest.skip(
            "set WORKFLOW_V3_OVERLEAF_ADAPTER_TEST_IMAGE to the built exact "
            "adapter image to run the real TeX Live 2025 integration"
        )
    inspected = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert inspected.startswith("sha256:") and len(inspected) == 71
    name = f"worker-v3-overleaf-test-{os.getpid()}"
    source_sha256 = _sha(SERVICE_PATH)
    container = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            name,
            "--read-only",
            "--user",
            "10004:10004",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "256",
            "--memory",
            "4g",
            "--cpus",
            "4",
            "--tmpfs",
            "/work:rw,nosuid,nodev,size=4294967296,uid=10004,gid=10004,mode=0700",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=268435456,uid=10004,gid=10004,mode=0700",
            "--tmpfs",
            "/work/home:rw,noexec,nosuid,nodev,size=16777216,uid=10004,gid=10004,mode=0700",
            "--tmpfs",
            "/biber-cache:rw,exec,nosuid,nodev,size=268435456,uid=10004,gid=10004,mode=0700",
            "-e",
            f"OVERLEAF_ADAPTER_IMAGE_DIGEST={inspected}",
            "-p",
            "127.0.0.1::8080",
            image,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    try:
        port = ""
        for _ in range(60):
            port = subprocess.run(
                ["docker", "port", name, "8080/tcp"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip().rsplit(":", 1)[-1]
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/healthz",
                    timeout=2,
                ) as response:
                    health = json.loads(response.read())
                break
            except (
                urllib.error.URLError,
                http.client.RemoteDisconnected,
                TimeoutError,
                json.JSONDecodeError,
            ):
                time.sleep(1)
        else:
            raise AssertionError("adapter did not become healthy")
        runtime_sha256 = health["runtime_identity_sha256"]
        assert health["runtime"]["base_image"] == PINNED_OVERLEAF_BASE_IMAGE
        assert health["runtime"]["adapter_source_sha256"] == source_sha256
        delivery = tmp_path / "delivery.zip"
        _formal_delivery(delivery)
        inventory = [
            {
                "path": "figure/logo.png",
                "size_bytes": 5,
                "sha256": hashlib.sha256(b"image").hexdigest(),
            }
        ]
        request_id = "a" * 32
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/compile",
            data=delivery.read_bytes(),
            method="POST",
            headers={
                "Content-Type": "application/zip",
                "X-Luceon-Protocol": ADAPTER_PROTOCOL,
                "X-Luceon-Request-Id": request_id,
                "X-Luceon-Role": "producer",
                "X-Luceon-Input-Sha256": _sha(delivery),
                "X-Luceon-Input-Size": str(delivery.stat().st_size),
                "X-Luceon-Image-Inventory-Sha256": _canonical_hash(inventory),
                "X-Luceon-Expected-Runtime-Sha256": runtime_sha256,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=1_260) as response:
                result = response.read()
                assert response.headers[
                    "X-Luceon-Runtime-Identity-Sha256"
                ] == runtime_sha256
                assert response.headers[
                    "X-Luceon-Result-Sha256"
                ] == hashlib.sha256(result).hexdigest()
        except urllib.error.HTTPError as exc:
            pytest.fail(
                f"adapter compile returned HTTP {exc.code}: "
                f"{exc.read().decode('utf-8', errors='replace')}"
            )
        with tarfile.open(fileobj=io.BytesIO(result), mode="r:gz") as archive:
            names = {item.name for item in archive.getmembers()}
            manifest = json.loads(
                archive.extractfile("result-manifest.json").read()
            )
            pdf = archive.extractfile("main.pdf").read()
        assert names == {"result-manifest.json", "main.pdf", "main.log"}
        assert manifest["status"] == "passed"
        assert manifest["runtime"]["adapter_image_digest"] == inspected
        assert manifest["input"]["sha256"] == _sha(delivery)
        assert pdf.startswith(b"%PDF-")
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container],
            capture_output=True,
            check=False,
        )
