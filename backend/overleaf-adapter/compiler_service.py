#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import re
import resource
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


ADAPTER_PROTOCOL = "luceon.worker-v3-overleaf-compiler/v1"
RESULT_SCHEMA = "luceon.worker-v3-overleaf-compile-result/v1"
RUNTIME_SCHEMA = "luceon.worker-v3-overleaf-runtime-identity/v1"
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
MAX_FILE_COUNT = 1_999
MAX_MEMBER_BYTES = 100_000_000
MAX_UNCOMPRESSED_BYTES = 500_000_000
MAX_COMPRESSION_RATIO = 1_000
MAX_LOG_BYTES = 20_000_000
MAX_PDF_BYTES = 1_000_000_000
COMPILE_TIMEOUT_SECONDS = 1_200
BIBER_CACHE_ROOT = "/biber-cache"
_ALLOWED_ROOT_FILES = frozenset(
    {"main.tex", "elegantbook.cls", "reference.bib"}
)
_ALLOWED_ASSET_DIRS = frozenset({"images", "figure"})
_ALLOWED_BODY_FILE = PurePosixPath("body/generated-body.tex")
_ALLOWED_BODY_PREFIX = ("body", "units")
_ALLOWED_ASSET_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg"}
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ROLE_VALUES = frozenset({"producer", "independent_evaluator"})
_COMPILE_SLOT = threading.BoundedSemaphore(1)


class AdapterError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class RuntimeIdentity:
    payload: Mapping[str, Any]
    sha256: str


@dataclass(frozen=True)
class RequestContract:
    request_id: str
    role: str
    input_sha256: str
    input_size: int
    image_inventory_sha256: str
    expected_runtime_sha256: str


def runtime_identity() -> RuntimeIdentity:
    source = Path(__file__).resolve()
    source_sha256 = _sha256_file(source)
    declared_source = os.environ.get("OVERLEAF_ADAPTER_SOURCE_SHA256", "")
    image_digest = os.environ.get("OVERLEAF_ADAPTER_IMAGE_DIGEST", "")
    if source_sha256 != declared_source:
        raise AdapterError(
            "adapter_source_identity_mismatch",
            "compiler service source differs from its image binding",
            status=503,
        )
    if not _image_digest(image_digest):
        raise AdapterError(
            "adapter_image_identity_missing",
            "compiler service image digest is not bound",
            status=503,
        )
    xelatex_version = _version(("xelatex", "--version"))
    latexmk_version = _version(("latexmk", "-v"))
    payload = {
        "schema_version": RUNTIME_SCHEMA,
        "base_image": PINNED_OVERLEAF_BASE_IMAGE,
        "adapter_image_digest": image_digest,
        "adapter_source_sha256": source_sha256,
        "command": list(COMPILE_COMMAND),
        "xelatex_version": xelatex_version,
        "latexmk_version": latexmk_version,
        "effective_uid": os.geteuid(),
        "architecture": platform.machine(),
    }
    return RuntimeIdentity(payload=payload, sha256=_canonical_sha256(payload))


def compile_zip(
    zip_path: Path,
    result_root: Path,
    *,
    contract: RequestContract,
    runtime: RuntimeIdentity,
) -> Path:
    if runtime.sha256 != contract.expected_runtime_sha256:
        raise AdapterError(
            "runtime_identity_mismatch",
            "request targets another compiler runtime",
            status=409,
        )
    if zip_path.stat().st_size != contract.input_size:
        raise AdapterError("input_size_mismatch", "request body size differs")
    if _sha256_file(zip_path) != contract.input_sha256:
        raise AdapterError("input_hash_mismatch", "request body hash differs")
    project = result_root / "project"
    project.mkdir(parents=True, mode=0o700)
    inventory = _extract_project(zip_path, project)
    inventory_sha256 = _canonical_sha256(inventory)
    if inventory_sha256 != contract.image_inventory_sha256:
        raise AdapterError(
            "image_inventory_mismatch",
            "ZIP image inventory differs from the request binding",
        )
    started = time.monotonic()
    log_path = project / "main.log"
    compiled = _run_compile(project)
    duration_ms = int((time.monotonic() - started) * 1000)
    pdf_path = project / "main.pdf"
    if compiled != 0 or not pdf_path.is_file() or pdf_path.is_symlink():
        raise AdapterError(
            "xelatex_compile_failed",
            "latexmk-xelatex did not produce a successful PDF",
        )
    if not log_path.is_file() or log_path.is_symlink():
        raise AdapterError("xelatex_log_missing", "XeLaTeX log is missing")
    if pdf_path.stat().st_size > MAX_PDF_BYTES:
        raise AdapterError("compiled_pdf_too_large", "compiled PDF exceeds limit")
    if log_path.stat().st_size > MAX_LOG_BYTES:
        raise AdapterError("compiled_log_too_large", "compile log exceeds limit")
    if not pdf_path.read_bytes()[:5] == b"%PDF-":
        raise AdapterError("compiled_pdf_invalid", "compiled output is not a PDF")
    page_count = _pdf_page_count(pdf_path)
    manifest = {
        "schema_version": RESULT_SCHEMA,
        "protocol": ADAPTER_PROTOCOL,
        "status": "passed",
        "request_id": contract.request_id,
        "role": contract.role,
        "input": {
            "sha256": contract.input_sha256,
            "size_bytes": contract.input_size,
            "image_inventory_sha256": inventory_sha256,
            "image_count": len(inventory),
            "images": inventory,
        },
        "runtime": {
            **runtime.payload,
            "runtime_identity_sha256": runtime.sha256,
        },
        "command": list(COMPILE_COMMAND),
        "exit_status": 0,
        "duration_ms": duration_ms,
        "output": {
            "pdf": {
                "path": "main.pdf",
                "sha256": _sha256_file(pdf_path),
                "size_bytes": pdf_path.stat().st_size,
                "page_count": page_count,
            },
            "log": {
                "path": "main.log",
                "sha256": _sha256_file(log_path),
                "size_bytes": log_path.stat().st_size,
            },
        },
    }
    evidence = result_root / "evidence"
    evidence.mkdir(mode=0o700)
    manifest_path = evidence / "result-manifest.json"
    _write_json(manifest_path, manifest)
    shutil.copyfile(pdf_path, evidence / "main.pdf")
    shutil.copyfile(log_path, evidence / "main.log")
    archive = result_root / "result.tar.gz"
    _write_result_archive(archive, evidence)
    return archive


def _extract_project(zip_path: Path, project: Path) -> list[dict[str, Any]]:
    if zip_path.stat().st_size >= MAX_ZIP_BYTES:
        raise AdapterError(
            "delivery_zip_too_large",
            "delivery ZIP must be strictly smaller than 50 MB",
        )
    image_inventory: list[dict[str, Any]] = []
    seen: set[str] = set()
    member_count = 0
    total_bytes = 0
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                name = _safe_zip_member(info)
                if name in seen:
                    raise AdapterError(
                        "delivery_zip_duplicate_member",
                        "delivery ZIP contains duplicate members",
                    )
                seen.add(name)
                if info.is_dir():
                    continue
                member_count += 1
                if member_count > MAX_FILE_COUNT:
                    raise AdapterError(
                        "delivery_zip_file_count_exceeded",
                        "delivery ZIP contains too many files",
                    )
                unix_type = (info.external_attr >> 16) & 0o170000
                if unix_type not in {0, stat.S_IFREG}:
                    raise AdapterError(
                        "delivery_zip_unsafe_member",
                        "delivery ZIP contains a non-regular file",
                    )
                if info.compress_type not in {
                    zipfile.ZIP_STORED,
                    zipfile.ZIP_DEFLATED,
                }:
                    raise AdapterError(
                        "delivery_zip_compression_unapproved",
                        "delivery ZIP uses an unapproved compression method",
                    )
                if info.file_size > MAX_MEMBER_BYTES:
                    raise AdapterError(
                        "delivery_zip_member_too_large",
                        "delivery ZIP member exceeds its limit",
                    )
                if info.file_size / max(1, info.compress_size) > MAX_COMPRESSION_RATIO:
                    raise AdapterError(
                        "delivery_zip_compression_ratio_exceeded",
                        "delivery ZIP member has an unsafe compression ratio",
                    )
                total_bytes += info.file_size
                if total_bytes > MAX_UNCOMPRESSED_BYTES:
                    raise AdapterError(
                        "delivery_zip_uncompressed_size_exceeded",
                        "delivery ZIP expands beyond its limit",
                    )
                relative = PurePosixPath(name)
                if len(relative.parts) == 1:
                    if name not in _ALLOWED_ROOT_FILES:
                        raise AdapterError(
                            "delivery_zip_member_not_allowed",
                            "delivery ZIP has an unapproved root member",
                        )
                else:
                    is_asset = (
                        relative.parts[0] in _ALLOWED_ASSET_DIRS
                        and relative.suffix.lower() in _ALLOWED_ASSET_SUFFIXES
                    )
                    is_body_tex = (
                        relative == _ALLOWED_BODY_FILE
                        or (
                            len(relative.parts) >= 4
                            and relative.parts[:2] == _ALLOWED_BODY_PREFIX
                            and relative.suffix.lower() == ".tex"
                        )
                    )
                    if not is_asset and not is_body_tex:
                        raise AdapterError(
                            "delivery_zip_member_not_allowed",
                            "delivery ZIP has an unapproved nested member",
                        )
                    if is_asset and info.file_size > MAX_IMAGE_BYTES:
                        raise AdapterError(
                            "delivery_image_too_large",
                            "delivery image must not exceed 1 MB",
                        )
                target = project.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                digest = hashlib.sha256()
                copied = 0
                with archive.open(info) as source, target.open("xb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > info.file_size:
                            raise AdapterError(
                                "delivery_zip_member_size_mismatch",
                                "delivery ZIP member expanded beyond its header",
                            )
                        digest.update(chunk)
                        output.write(chunk)
                if copied != info.file_size:
                    raise AdapterError(
                        "delivery_zip_member_size_mismatch",
                        "delivery ZIP member size differs from its header",
                    )
                if (
                    len(relative.parts) > 1
                    and relative.parts[0] in _ALLOWED_ASSET_DIRS
                ):
                    image_inventory.append(
                        {
                            "path": name,
                            "size_bytes": copied,
                            "sha256": digest.hexdigest(),
                        }
                    )
    except AdapterError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise AdapterError(
            "delivery_zip_invalid",
            "delivery ZIP is unreadable",
        ) from exc
    if not _ALLOWED_ROOT_FILES.issubset(seen):
        raise AdapterError(
            "delivery_zip_incomplete",
            "delivery ZIP must contain root main.tex and elegantbook.cls",
        )
    return sorted(image_inventory, key=lambda row: str(row["path"]))


def _run_compile(project: Path) -> int:
    environment = {
        "PATH": os.environ.get(
            "PATH",
            "/usr/local/texlive/2025/bin/aarch64-linux:/usr/local/bin:/usr/bin:/bin",
        ),
        "HOME": str(project),
        "TMPDIR": str(project / ".tmp"),
        "PAR_GLOBAL_TEMP": BIBER_CACHE_ROOT,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "SOURCE_DATE_EPOCH": "0",
        "FORCE_SOURCE_DATE": "1",
        "openin_any": "p",
        "openout_any": "p",
    }
    (project / ".tmp").mkdir(mode=0o700)
    stdout_path = project / "latexmk.stdout.log"
    stderr_path = project / "latexmk.stderr.log"
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            list(COMPILE_COMMAND),
            cwd=project,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            shell=False,
            start_new_session=True,
            preexec_fn=_set_compile_limits,
        )
        try:
            return process.wait(timeout=COMPILE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise AdapterError(
                "xelatex_compile_timeout",
                "latexmk-xelatex exceeded its time limit",
            ) from exc


def _set_compile_limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (COMPILE_TIMEOUT_SECONDS, COMPILE_TIMEOUT_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_PDF_BYTES, MAX_PDF_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (192, 192))
    except (ValueError, OSError):
        pass


def _pdf_page_count(path: Path) -> int:
    try:
        result = subprocess.run(
            ["qpdf", "--show-npages", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env={
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdapterError(
            "pdf_page_count_failed",
            "compiled PDF cannot be inspected",
        ) from exc
    pages = result.stdout.strip()
    if (
        result.returncode != 0
        or not pages.isdigit()
        or int(pages) < 1
    ):
        raise AdapterError(
            "pdf_page_count_failed",
            "compiled PDF has no valid page count",
        )
    return int(pages)


def _write_result_archive(path: Path, evidence: Path) -> None:
    with path.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name in ("result-manifest.json", "main.pdf", "main.log"):
                    source = evidence / name
                    info = tarfile.TarInfo(name)
                    info.size = source.stat().st_size
                    info.mode = 0o600
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    with source.open("rb") as handle:
                        archive.addfile(info, handle)


def _safe_zip_member(info: zipfile.ZipInfo) -> str:
    name = info.filename[:-1] if info.filename.endswith("/") else info.filename
    if not name or name.startswith("/") or "\\" in name:
        raise AdapterError("delivery_zip_unsafe_member", "delivery ZIP path is unsafe")
    path = PurePosixPath(name)
    if str(path) != name or any(part in {"", ".", ".."} for part in path.parts):
        raise AdapterError("delivery_zip_unsafe_member", "delivery ZIP path is unsafe")
    unix_type = (info.external_attr >> 16) & 0o170000
    if unix_type in {
        stat.S_IFLNK,
        stat.S_IFCHR,
        stat.S_IFBLK,
        stat.S_IFIFO,
        stat.S_IFSOCK,
    }:
        raise AdapterError("delivery_zip_unsafe_member", "delivery ZIP type is unsafe")
    return name


class CompilerHandler(BaseHTTPRequestHandler):
    server_version = "LuceonOverleafCompiler/1"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path != "/healthz":
            self._error(HTTPStatus.NOT_FOUND, "not_found")
            return
        runtime = self.server.runtime  # type: ignore[attr-defined]
        self._json_response(
            HTTPStatus.OK,
            {
                "schema_version": "luceon.worker-v3-overleaf-health/v1",
                "status": "ready",
                "runtime": runtime.payload,
                "runtime_identity_sha256": runtime.sha256,
            },
            extra_headers={
                "X-Luceon-Runtime-Identity-Sha256": runtime.sha256,
            },
        )

    def do_POST(self) -> None:
        if self.path != "/compile":
            self._error(HTTPStatus.NOT_FOUND, "not_found")
            return
        if not _COMPILE_SLOT.acquire(blocking=False):
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "compiler_busy")
            return
        request_root: Path | None = None
        try:
            contract = self._request_contract()
            work_root = Path(os.environ.get("OVERLEAF_ADAPTER_WORK_ROOT", "/work"))
            work_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            request_root = Path(
                tempfile.mkdtemp(prefix="compile-", dir=work_root)
            )
            input_path = request_root / "input.zip"
            self._read_body(input_path, contract.input_size)
            result = compile_zip(
                input_path,
                request_root,
                contract=contract,
                runtime=self.server.runtime,  # type: ignore[attr-defined]
            )
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Length", str(result.stat().st_size))
            self.send_header(
                "X-Luceon-Runtime-Identity-Sha256",
                self.server.runtime.sha256,  # type: ignore[attr-defined]
            )
            self.send_header("X-Luceon-Result-Sha256", _sha256_file(result))
            self.send_header("Connection", "close")
            self.end_headers()
            with result.open("rb") as source:
                shutil.copyfileobj(source, self.wfile, length=1024 * 1024)
        except AdapterError as exc:
            self._error(exc.status, exc.code)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "compiler_internal_error",
            )
        finally:
            if request_root is not None:
                shutil.rmtree(request_root, ignore_errors=True)
            _COMPILE_SLOT.release()

    def _request_contract(self) -> RequestContract:
        if self.headers.get("Content-Type") != "application/zip":
            raise AdapterError("content_type_invalid", "request is not a ZIP")
        if self.headers.get("Transfer-Encoding"):
            raise AdapterError(
                "chunked_request_forbidden",
                "compile request requires a fixed Content-Length",
            )
        try:
            size = int(self.headers.get("Content-Length", ""))
        except ValueError as exc:
            raise AdapterError("content_length_invalid", "content length is invalid") from exc
        request_id = self.headers.get("X-Luceon-Request-Id", "")
        role = self.headers.get("X-Luceon-Role", "")
        protocol = self.headers.get("X-Luceon-Protocol", "")
        input_sha256 = self.headers.get("X-Luceon-Input-Sha256", "")
        image_inventory = self.headers.get(
            "X-Luceon-Image-Inventory-Sha256", ""
        )
        runtime_sha256 = self.headers.get(
            "X-Luceon-Expected-Runtime-Sha256", ""
        )
        declared_size = self.headers.get("X-Luceon-Input-Size", "")
        if (
            protocol != ADAPTER_PROTOCOL
            or not re.fullmatch(r"[0-9a-f]{32}", request_id)
            or role not in _ROLE_VALUES
            or not _sha256(input_sha256)
            or not _sha256(image_inventory)
            or not _sha256(runtime_sha256)
            or declared_size != str(size)
            or size < 1
            or size >= MAX_ZIP_BYTES
        ):
            raise AdapterError(
                "request_contract_invalid",
                "compile request contract is invalid",
            )
        return RequestContract(
            request_id=request_id,
            role=role,
            input_sha256=input_sha256,
            input_size=size,
            image_inventory_sha256=image_inventory,
            expected_runtime_sha256=runtime_sha256,
        )

    def _read_body(self, target: Path, size: int) -> None:
        remaining = size
        digest = hashlib.sha256()
        with target.open("xb") as output:
            while remaining:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise AdapterError(
                        "request_body_incomplete",
                        "compile request body is incomplete",
                    )
                remaining -= len(chunk)
                digest.update(chunk)
                output.write(chunk)
        if self.headers.get("X-Luceon-Input-Sha256") != digest.hexdigest():
            raise AdapterError(
                "input_hash_mismatch",
                "compile request body hash differs",
            )

    def _error(self, status: int, code: str) -> None:
        try:
            self._json_response(
                status,
                {
                    "schema_version": "luceon.worker-v3-overleaf-error/v1",
                    "status": "failed",
                    "code": code,
                },
            )
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json_response(
        self,
        status: int,
        payload: Mapping[str, Any],
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        body = _canonical_json(payload) + b"\n"
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def serve(host: str, port: int) -> None:
    runtime = runtime_identity()
    server = ThreadingHTTPServer((host, port), CompilerHandler)
    server.runtime = runtime  # type: ignore[attr-defined]
    server.daemon_threads = True
    server.serve_forever()


def _version(command: tuple[str, ...]) -> str:
    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env={
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdapterError(
            "compiler_runtime_unavailable",
            f"{command[0]} is unavailable",
            status=503,
        ) from exc
    lines = (result.stdout or result.stderr).splitlines()
    if result.returncode != 0 or not lines:
        raise AdapterError(
            "compiler_runtime_unavailable",
            f"{command[0]} version check failed",
            status=503,
        )
    return lines[0]


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_bytes(_canonical_json(value) + b"\n")
    path.chmod(0o600)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _image_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and _sha256(value.removeprefix("sha256:"))
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Isolated Overleaf-equivalent XeLaTeX compiler adapter"
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
