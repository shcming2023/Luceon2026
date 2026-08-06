from __future__ import annotations

import os
import tempfile
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path


GIB = 1024**3
MIB = 1024**2


def _integer(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class PdfUploadPolicy:
    schema_version: str
    max_file_bytes: int
    max_file_pages: int
    max_request_bytes: int
    max_request_files: int
    max_request_fields: int
    max_gpu_batch_input_bytes: int
    max_gpu_batch_files: int
    large_pdf_threshold_bytes: int
    large_pdf_page_threshold: int
    expansion_factor: int
    min_local_temp_free_bytes: int
    min_gpu_headroom_bytes: int
    default_stage_timeout_seconds: int
    large_stage_timeout_seconds: int
    upload_chunk_bytes: int
    multipart_overhead_bytes: int
    temp_dir: str

    def identity_sha256(self) -> str:
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def as_capabilities(self) -> dict:
        payload = asdict(self)
        internal_profile_requirements = {
            "max_file_bytes": 2 * GIB,
            "max_file_pages": 2000,
            "max_request_bytes": 3 * GIB,
            "max_request_files": 5,
            "max_gpu_batch_input_bytes": 3 * GIB,
            "max_gpu_batch_files": 5,
        }
        internal_profile_gap = [
            f"{name}={getattr(self, name)} is below {minimum}"
            for name, minimum in internal_profile_requirements.items()
            if getattr(self, name) < minimum
        ]
        internal_profile_qualified = not internal_profile_gap
        payload.update(
            {
                "policy_sha256": self.identity_sha256(),
                "bytes_unit": "binary",
                "max_file_label": "2 GiB" if self.max_file_bytes == 2 * GIB else f"{self.max_file_bytes} bytes",
                "multipart_spools_before_endpoint": True,
                "proxy_request_buffering_required": False,
                "actual_2gib_transfer_qualified": False,
                "internal_2gib_2000_profile_qualified": internal_profile_qualified,
                "internal_profile_gap": internal_profile_gap,
                "eligibility_states": [
                    "upload_allowed",
                    "uploaded_but_gpu_resource_review",
                    "gpu_eligible",
                    "rejected_by_config",
                ],
            }
        )
        return payload


def load_pdf_upload_policy() -> PdfUploadPolicy:
    max_file_bytes = _integer("LUCEON_MAX_UPLOAD_PDF_BYTES", 2 * GIB, MIB)
    max_file_pages = _integer("LUCEON_MAX_UPLOAD_PDF_PAGES", 2000, 10)
    max_request_bytes = _integer("LUCEON_MAX_UPLOAD_REQUEST_BYTES", 3 * GIB, max_file_bytes)
    max_request_files = _integer("LUCEON_MAX_UPLOAD_REQUEST_FILES", 5, 1)
    max_request_fields = _integer("LUCEON_MAX_UPLOAD_REQUEST_FIELDS", 16, 1)
    max_gpu_batch_input_bytes = _integer("LUCEON_MAX_GPU_BATCH_INPUT_BYTES", 3 * GIB, max_file_bytes)
    max_gpu_batch_files = _integer("LUCEON_MAX_GPU_BATCH_FILES", 5, 1)
    large_bytes = _integer("LUCEON_LARGE_PDF_THRESHOLD_BYTES", 256 * MIB, MIB)
    large_pages = _integer("LUCEON_LARGE_PDF_PAGE_THRESHOLD", 1000, 10)
    if large_bytes > max_file_bytes or large_pages > max_file_pages:
        raise RuntimeError("large PDF thresholds must remain inside the upload envelope")
    default_timeout = _integer("LUCEON_PDF_STAGE_TIMEOUT_SECONDS", 2 * 60 * 60, 15 * 60)
    large_timeout = _integer("LUCEON_LARGE_PDF_STAGE_TIMEOUT_SECONDS", 6 * 60 * 60, default_timeout)
    temp_dir = os.getenv("LUCEON_UPLOAD_TEMP_DIR", tempfile.gettempdir()).strip()
    if not temp_dir or not Path(temp_dir).is_absolute():
        raise RuntimeError("LUCEON_UPLOAD_TEMP_DIR must be an absolute path")
    return PdfUploadPolicy(
        schema_version="luceon-pdf-upload-policy-v3",
        max_file_bytes=max_file_bytes,
        max_file_pages=max_file_pages,
        max_request_bytes=max_request_bytes,
        max_request_files=max_request_files,
        max_request_fields=max_request_fields,
        max_gpu_batch_input_bytes=max_gpu_batch_input_bytes,
        max_gpu_batch_files=max_gpu_batch_files,
        large_pdf_threshold_bytes=large_bytes,
        large_pdf_page_threshold=large_pages,
        expansion_factor=_integer("LUCEON_LARGE_PDF_EXPANSION_FACTOR", 12, 2),
        min_local_temp_free_bytes=_integer("LUCEON_MIN_UPLOAD_TEMP_FREE_BYTES", 5 * GIB, GIB),
        min_gpu_headroom_bytes=_integer("LUCEON_MIN_GPU_HEADROOM_BYTES", 50 * GIB, 4 * GIB),
        default_stage_timeout_seconds=default_timeout,
        large_stage_timeout_seconds=large_timeout,
        upload_chunk_bytes=_integer("LUCEON_PDF_UPLOAD_CHUNK_BYTES", MIB, 64 * 1024),
        multipart_overhead_bytes=_integer("LUCEON_MULTIPART_OVERHEAD_BYTES", 16 * MIB, MIB),
        temp_dir=temp_dir,
    )


def pdf_upload_capabilities() -> dict:
    return load_pdf_upload_policy().as_capabilities()
