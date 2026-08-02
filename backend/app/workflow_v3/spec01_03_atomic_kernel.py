#!/usr/bin/env python3
"""Release-local, stage-atomic deterministic producers for EBC Specs 01-03.

This module deliberately does not import or call the historical combined
``build_native_spec01_spec02.py`` producer.  Each subcommand consumes only the
inputs allowed for its formal stage and emits candidate evidence.  Evaluation
and promotion are separate Worker V3 responsibilities.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


KERNEL_VERSION = "luceon-worker-v3-spec01-03-atomic/1.8.0"
INTAKE_SCHEMA = "luceon.worker-v3-spec01-intake-contract/v1"
SCOPE_REVIEW_SCHEMA = "luceon.worker-v3-spec02-scope-order-review/v3"
MEDIA_REVIEW_SCHEMA = "luceon.worker-v3-spec03-media-review/v2"
SCOPE_REVIEW_TASK_SCHEMA = "luceon.worker-v3-spec02-review-task/v2"
MEDIA_REVIEW_TASK_SCHEMA = "luceon.worker-v3-spec03-review-task/v3"
OUTLINE_REVIEW_TASK_SCHEMA = "luceon.worker-v3-spec04a-review-task/v1"
OUTLINE_COMPACT_REVIEW_SCHEMA = (
    "luceon.worker-v3-spec04a-compact-review/v1"
)
SEMANTIC_REVIEW_TASK_SCHEMA = "luceon.worker-v3-spec04b-review-task/v2"
SEMANTIC_COMPACT_REVIEW_SCHEMA = (
    "luceon.worker-v3-spec04b-compact-review/v2"
)
SEMANTIC_OPTION_PROTOCOL_SCHEMA = (
    "luceon.worker-v3-spec04b-total-option-index/v1"
)
ATOMIC_STAGE_MANIFEST_SCHEMA = "luceon.worker-v3-atomic-stage-manifest/v1"
RUN_MANIFEST_SCHEMA = "luceon.worker-v3-atomic-run-manifest/v1"
DECISION_INDEX_SCHEMA = "canonical-decision-index/1.1"
CANONICAL_LEDGER_SCHEMA = "canonical-block-ledger/2.0"

SUPPORTED_MINERU_SCHEMAS = {
    "luceon-gpu-wrapper-mineru-only-manifest/v1",
}
SUPPORTED_POPO_SCHEMAS = {
    "luceon-gpu-wrapper-popo-from-frozen-mineru-manifest/v1",
}
MEDIA_TYPES = {"image", "table", "chart", "equation_interline"}
MAX_ARCHIVE_MEMBERS = 40_000
MAX_ARCHIVE_BYTES = 4_000_000_000
MAX_JSON_BYTES = 300_000_000
SCOPE_BASELINE_ALGORITHM = "popo-evidence-scope-order-baseline/1.0"
MEDIA_BASELINE_ALGORITHM = "spatial-source-media-ownership-baseline/2.0"
FRAGILE_SOURCE_TYPES = frozenset({"chart", "equation", "image", "table"})
FRAGILE_MEDIA_CONTAINMENT_MIN = 0.75
FALLBACK_MEDIA_CONTAINMENT_MIN = 0.90
SCOPE_BASELINE_EXCLUDED_LABELS = frozenset(
    {"footer", "header", "page_number", "watermark"}
)
SCOPE_REVIEW_EXCERPT_CHARS = 240
OUTLINE_CONTEXT_EXCERPT_CHARS = 240
OUTLINE_CONTEXT_RADIUS = 1
SEMANTIC_CONTEXT_EXCERPT_CHARS = 240
SEMANTIC_MARKER_TYPES = frozenset({"title", "text", "aside_text"})
SEMANTIC_BODY_TYPES = frozenset({"text", "aside_text", "list"})
SEMANTIC_FRAGILE_LABELS = frozenset(
    {
        "chart",
        "equation",
        "equation_interline",
        "formula",
        "image",
        "image_caption",
        "image_footnote",
        "page_footnote",
        "table",
        "table_caption",
    }
)
SEMANTIC_ROLE_CHOICES = (
    "activity",
    "answer",
    "assessment",
    "definition",
    "example",
    "exercise",
    "experiment",
    "investigation",
    "key_point",
    "method_note",
    "note",
    "practice",
    "prompt",
    "source_label",
    "summary",
    "tip",
    "vocabulary",
    "worked_example",
)

SPEC01_COMPACT_PARENT_FILES = (
    "contracts/input_contract.json",
    "contracts/source_trace.json",
    "contracts/materialized_manifest.json",
    "contracts/template_intake.json",
    "decisions/input_decisions.jsonl",
    "decisions/canonical_decision_index.json",
    "evidence/pdf_page_geometry.json",
    "source/archive_entry_evidence.jsonl",
    "source/media_asset_inventory.json",
    "source/mineru_media_atoms.jsonl",
    "source/popo_source_units.jsonl",
)
SPEC02_COMPACT_PARENT_FILES = (
    *SPEC01_COMPACT_PARENT_FILES,
    "contracts/composite_reading_relationships.json",
    "decisions/scope_order_decisions.jsonl",
    "ledgers/reading_order_ledger.json",
    "ledgers/source_page_render_ledger.jsonl",
    "ledgers/source_scope_ledger.json",
)


class KernelContractError(ValueError):
    """A fail-closed formal stage contract violation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise KernelContractError(code, message)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail("json_not_canonical", f"value cannot be canonicalized: {exc}")


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _contract_payload_hash(value: Mapping[str, Any]) -> str:
    return _canonical_hash(
        {
            key: item
            for key, item in value.items()
            if key not in {"generated_at", "payload_hash"}
        }
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return _sha256(path)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(_canonical_bytes(dict(row)).decode("utf-8") + "\n")
    return _sha256(path)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        _fail("required_file_missing", f"{label} is missing or linked")
    if path.stat().st_size > MAX_JSON_BYTES:
        _fail("json_too_large", f"{label} exceeds the JSON size budget")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("json_invalid", f"{label} is not valid JSON: {exc}")
    if not isinstance(value, dict):
        _fail("json_shape_invalid", f"{label} must be a JSON object")
    return value


def _read_json_value(path: Path, label: str) -> Any:
    if not path.is_file() or path.is_symlink():
        _fail("required_file_missing", f"{label} is missing or linked")
    if path.stat().st_size > MAX_JSON_BYTES:
        _fail("json_too_large", f"{label} exceeds the JSON size budget")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("json_invalid", f"{label} is not valid JSON: {exc}")


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        _fail("required_file_missing", f"{label} is missing or linked")
    rows: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            1,
        ):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                _fail(
                    "jsonl_shape_invalid",
                    f"{label} line {line_number} must be an object",
                )
            rows.append(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("jsonl_invalid", f"{label} is not valid JSONL: {exc}")
    return rows


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("field_invalid", f"{label} must be a non-empty string")
    return value.strip()


def _require_sha(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        _fail("sha256_invalid", f"{label} must be a lowercase SHA-256")
    return text


def _require_positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        _fail("field_invalid", f"{label} must be a positive integer")
    return value


def _require_nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _fail("field_invalid", f"{label} must be a non-negative integer")
    return value


def _safe_relative(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if text.startswith("/") or "\\" in text:
        _fail("path_invalid", f"{label} must be a relative POSIX path")
    parsed = PurePosixPath(text)
    if str(parsed) != text or any(part in {"", ".", ".."} for part in parsed.parts):
        _fail("path_invalid", f"{label} is not normalized")
    return text


def _contained_file(root: Path, relative: str, label: str) -> Path:
    relative = _safe_relative(relative, label)
    root = root.resolve()
    current = root
    for part in PurePosixPath(relative).parts:
        current /= part
        if current.is_symlink():
            _fail("path_link_forbidden", f"{label} traverses a symlink")
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        _fail("required_file_missing", f"{label} is unavailable")
    return path


def _copy_file(source: Path, destination: Path) -> str:
    if not source.is_file() or source.is_symlink():
        _fail("required_file_missing", f"cannot materialize {source.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return _sha256(destination)


def _copy_compact_parent(
    parent: Path,
    output: Path,
    *,
    allowlist: Sequence[str],
) -> None:
    """Copy only hash-bound, small contracts required by the next stage.

    Promoted candidates are immutable evidence bundles, not recursive working
    directories.  In particular, frozen PDFs, archives, templates, full-page
    rasters, and unselected media must never be inherited into another bundle.
    """

    parent = parent.resolve()
    output = output.resolve()
    for relative in sorted(set(allowlist)):
        source = _contained_file(parent, relative, f"compact predecessor {relative}")
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _prepare_stage_output(output: Path) -> Path:
    """Use the entrypoint-created candidate directory, but require it empty."""

    output = output.resolve()
    if output.is_symlink():
        _fail("isolated_workspace_violation", "stage output cannot be a symlink")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        _fail("isolated_workspace_violation", "stage output must be empty")
    return output


def _safe_extract_tar(source: Path, destination: Path, label: str) -> dict[str, Path]:
    destination.mkdir(parents=True, exist_ok=False)
    seen: set[str] = set()
    files: dict[str, Path] = {}
    total = 0
    try:
        with tarfile.open(source, "r:*") as archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                _fail("archive_member_budget_exceeded", f"{label} has too many members")
            for member in members:
                if (
                    member.issym()
                    or member.islnk()
                    or member.isdev()
                    or member.isfifo()
                ):
                    _fail(
                        "archive_unsafe_member",
                        f"{label} contains unsafe member {member.name!r}",
                    )
                if member.isdir() and member.name in {".", "./"}:
                    if member.size != 0:
                        _fail(
                            "archive_unsafe_member",
                            f"{label} root directory member has content",
                        )
                    continue
                raw_name = member.name[2:] if member.name.startswith("./") else member.name
                if member.isdir() and raw_name.endswith("/"):
                    raw_name = raw_name[:-1]
                name = _safe_relative(raw_name, f"{label} member")
                if name in seen:
                    _fail("archive_duplicate_member", f"{label} repeats {name!r}")
                seen.add(name)
                total += max(0, int(member.size))
                if total > MAX_ARCHIVE_BYTES:
                    _fail("archive_byte_budget_exceeded", f"{label} is too large")
                target = (destination / name).resolve()
                if destination.resolve() not in target.parents:
                    _fail("archive_path_escape", f"{label} member {name!r} escapes")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    _fail("archive_unsafe_member", f"{label} member {name!r} is unsupported")
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = archive.extractfile(member)
                if extracted is None:
                    _fail("archive_read_failed", f"{label} member {name!r} is unreadable")
                with target.open("xb") as handle:
                    shutil.copyfileobj(extracted, handle)
                files[name] = target
    except (OSError, tarfile.TarError) as exc:
        _fail("archive_invalid", f"{label} is invalid: {exc}")
    if not files:
        _fail("archive_empty", f"{label} contains no files")
    return files


def _safe_zip_inventory(source: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    try:
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                name = info.filename.rstrip("/")
                if not name:
                    continue
                name = _safe_relative(name, "template member")
                if name in seen:
                    _fail("archive_duplicate_member", f"template repeats {name!r}")
                seen.add(name)
                unix_type = (info.external_attr >> 16) & 0o170000
                if unix_type == stat.S_IFLNK:
                    _fail("archive_unsafe_member", f"template member {name!r} is linked")
                if info.is_dir():
                    continue
                total += int(info.file_size)
                if total > MAX_ARCHIVE_BYTES:
                    _fail("archive_byte_budget_exceeded", "template is too large")
                payload = archive.read(info)
                if len(payload) != info.file_size:
                    _fail("archive_read_failed", f"template member {name!r} is truncated")
                rows.append(
                    {
                        "member": name,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "size_bytes": len(payload),
                    }
                )
    except (OSError, zipfile.BadZipFile) as exc:
        _fail("archive_invalid", f"template archive is invalid: {exc}")
    if not rows:
        _fail("archive_empty", "template archive is empty")
    return sorted(rows, key=lambda item: item["member"])


def _object_ref(manifest: Mapping[str, Any], key: str, label: str) -> Mapping[str, Any]:
    objects = manifest.get("objects")
    value = objects.get(key) if isinstance(objects, Mapping) else None
    if not isinstance(value, Mapping):
        _fail("manifest_object_missing", f"{label} has no objects.{key}")
    _require_text(value.get("bucket"), f"{label}.objects.{key}.bucket")
    _safe_relative(value.get("object"), f"{label}.objects.{key}.object")
    _require_sha(value.get("sha256"), f"{label}.objects.{key}.sha256")
    _require_positive_int(value.get("size_bytes"), f"{label}.objects.{key}.size_bytes")
    return value


def _assert_object_bytes(
    reference: Mapping[str, Any],
    path: Path,
    label: str,
) -> None:
    if _sha256(path) != reference["sha256"]:
        _fail("manifest_object_hash_mismatch", f"{label} hash differs from manifest")
    if path.stat().st_size != reference["size_bytes"]:
        _fail("manifest_object_size_mismatch", f"{label} size differs from manifest")


def _find_unique_member(
    files: Mapping[str, Path],
    *,
    endings: Sequence[str],
    expected: Mapping[str, Any],
    label: str,
) -> tuple[str, Path]:
    candidates = [
        (name, path)
        for name, path in files.items()
        if any(name.endswith(ending) for ending in endings)
        and path.stat().st_size == expected["size_bytes"]
        and _sha256(path) == expected["sha256"]
    ]
    if len(candidates) != 1:
        _fail(
            "archive_manifest_member_mismatch",
            f"{label} has {len(candidates)} archive members matching its manifest identity",
        )
    return candidates[0]


def _archive_entry_identity(
    *,
    provider: str,
    archive_ref: Mapping[str, Any],
    member: str,
    path: Path,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "archive": {
            "bucket": archive_ref["bucket"],
            "object": archive_ref["object"],
            "sha256": archive_ref["sha256"],
            "size_bytes": archive_ref["size_bytes"],
        },
        "member": _safe_relative(member, f"{provider} archive member"),
        "member_sha256": _sha256(path),
        "member_size_bytes": path.stat().st_size,
    }


def _verify_external_inputs(
    contract: Mapping[str, Any],
    *,
    source_pdf: Path,
    mineru_archive: Path,
    popo_archive: Path,
    template_archive: Path,
) -> None:
    inputs = contract.get("inputs")
    if not isinstance(inputs, Mapping):
        _fail("input_reference_contract_invalid", "input contract has no immutable inputs")
    actual = {
        "source_pdf": source_pdf,
        "mineru_archive": mineru_archive,
        "popo_archive": popo_archive,
        "template_archive": template_archive,
    }
    for role, path in actual.items():
        reference = inputs.get(role)
        if not isinstance(reference, Mapping):
            _fail("input_reference_contract_invalid", f"input contract lacks {role}")
        if _require_sha(reference.get("sha256"), f"inputs.{role}.sha256") != _sha256(path):
            _fail("input_reference_drift", f"{role} differs from the Spec 01 identity")
        if _require_positive_int(
            reference.get("size_bytes"),
            f"inputs.{role}.size_bytes",
        ) != path.stat().st_size:
            _fail("input_reference_drift", f"{role} size differs from the Spec 01 identity")


def _materialize_selected_members(
    archive_path: Path,
    *,
    archive_identity: Mapping[str, Any],
    selections: Sequence[Mapping[str, Any]],
    output: Path,
) -> dict[str, dict[str, Any]]:
    """Validate a tar and materialize only selected immutable members."""

    expected_archive_sha = _require_sha(
        archive_identity.get("sha256"),
        "selected archive.sha256",
    )
    expected_archive_size = _require_positive_int(
        archive_identity.get("size_bytes"),
        "selected archive.size_bytes",
    )
    if _sha256(archive_path) != expected_archive_sha or archive_path.stat().st_size != expected_archive_size:
        _fail("input_reference_drift", "selected media archive differs from Spec 01")
    by_member: dict[str, Mapping[str, Any]] = {}
    for selection in selections:
        member = _safe_relative(selection.get("archive_member"), "selected archive_member")
        if member in by_member:
            existing = by_member[member]
            if (
                existing.get("sha256") != selection.get("sha256")
                or existing.get("size_bytes") != selection.get("size_bytes")
            ):
                _fail("media_asset_drift", f"selected member {member!r} has conflicting identities")
        by_member[member] = selection
    if not by_member:
        return {}

    materialized: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    total = 0
    try:
        with tarfile.open(archive_path, "r:*") as archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                _fail("archive_member_budget_exceeded", "selected media archive has too many members")
            for info in members:
                name = _safe_relative(info.name, "selected media archive member")
                if name in seen:
                    _fail("archive_duplicate_member", f"archive repeats {name!r}")
                seen.add(name)
                if info.issym() or info.islnk() or info.isdev() or info.isfifo():
                    _fail("archive_special_member", f"archive member {name!r} is unsafe")
                if info.isdir():
                    continue
                if not info.isfile():
                    _fail("archive_special_member", f"archive member {name!r} is unsupported")
                total += int(info.size)
                if total > MAX_ARCHIVE_BYTES:
                    _fail("archive_byte_budget_exceeded", "selected media archive is too large")
                selection = by_member.get(name)
                if selection is None:
                    continue
                extracted = archive.extractfile(info)
                if extracted is None:
                    _fail("archive_read_failed", f"cannot read selected member {name!r}")
                payload = extracted.read()
                expected_sha = _require_sha(selection.get("sha256"), f"{name}.sha256")
                expected_size = _require_positive_int(selection.get("size_bytes"), f"{name}.size_bytes")
                if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_sha:
                    _fail("media_asset_drift", f"selected member {name!r} differs from Spec 01")
                suffix = PurePosixPath(name).suffix.lower()
                destination_relative = f"media/selected/{expected_sha}{suffix}"
                destination = output / destination_relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    if _sha256(destination) != expected_sha:
                        _fail("media_asset_drift", "selected media destination hash collided")
                else:
                    destination.write_bytes(payload)
                materialized[name] = {
                    "path": destination_relative,
                    "sha256": expected_sha,
                    "size_bytes": expected_size,
                }
    except (OSError, tarfile.TarError) as exc:
        _fail("archive_invalid", f"selected media archive is invalid: {exc}")
    missing = sorted(set(by_member) - set(materialized))
    if missing:
        _fail("media_asset_unresolved", f"selected archive members are absent: {missing[:10]}")
    return materialized


def _verified_selected_media_candidate(
    candidate: Mapping[str, Any],
    *,
    output: Path,
) -> dict[str, Any]:
    representation_type = str(candidate.get("representation_type") or "")
    if representation_type != "source_asset_image":
        _fail(
            "media_candidate_not_formally_executable",
            "the current atomic Spec 03 path only freezes hash-verified source assets",
        )
    evidence = candidate.get("materialized_evidence")
    if not isinstance(evidence, Mapping):
        _fail(
            "media_asset_unresolved",
            "selected source asset was not materialized",
        )
    relative = _safe_relative(
        evidence.get("path"),
        "selected media materialized path",
    )
    path = _contained_file(output, relative, "selected media asset")
    expected_sha = _require_sha(
        evidence.get("sha256"),
        "selected media artifact sha256",
    )
    expected_size = _require_positive_int(
        evidence.get("size_bytes"),
        "selected media artifact size",
    )
    if _sha256(path) != expected_sha or path.stat().st_size != expected_size:
        _fail("media_asset_drift", "selected media artifact differs after materialization")
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            image_format = image.format
    except Exception as exc:
        _fail("media_asset_invalid", f"selected media asset is not decodable: {exc}")
    if width < 2 or height < 2:
        _fail("media_asset_invalid", "selected media asset is too small")
    return {
        **{
            key: item
            for key, item in candidate.items()
            if key != "materialized_evidence"
        },
        "status": "usable",
        "resolved_path": relative,
        "artifact_sha256": expected_sha,
        "size_bytes": expected_size,
        "metrics": {
            "format": image_format,
            "width_px": width,
            "height_px": height,
        },
        "anomaly_flags": [],
    }


def _materialized_source_region_candidate(
    candidate: Mapping[str, Any],
    *,
    source_pdf: Path,
    output: Path,
    page_count: int,
) -> dict[str, Any]:
    page_number = _require_positive_int(
        candidate.get("source_page"),
        "source region page",
    )
    if page_number > page_count:
        _fail("source_region_page_invalid", "source region page is outside the PDF")
    bbox = candidate.get("bbox")
    if (
        candidate.get("bbox_coordinate_space")
        != "pdf_cropbox_normalized_0_1_top_left"
        or not isinstance(bbox, list)
        or len(bbox) != 4
        or any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            for value in bbox
        )
    ):
        _fail("source_region_bbox_invalid", "source region bbox is not normalized")
    x0, y0, x1, y1 = (float(value) for value in bbox)
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        _fail("source_region_bbox_invalid", "source region bbox is outside the page")
    try:
        import fitz  # type: ignore

        document = fitz.open(source_pdf)
        if len(document) != page_count:
            _fail("source_pdf_page_count_drift", "renderer sees a different page count")
        page = document[page_number - 1]
        page_rect = page.rect
        clip = fitz.Rect(
            page_rect.x0 + x0 * page_rect.width,
            page_rect.y0 + y0 * page_rect.height,
            page_rect.x0 + x1 * page_rect.width,
            page_rect.y0 + y1 * page_rect.height,
        )
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(2, 2),
            clip=clip,
            colorspace=fitz.csRGB,
            alpha=False,
        )
        selected_dir = output / "media/selected"
        selected_dir.mkdir(parents=True, exist_ok=True)
        token = _canonical_hash(
            {
                "source_page": page_number,
                "bbox": [x0, y0, x1, y1],
                "bbox_coordinate_space": candidate["bbox_coordinate_space"],
            }
        )
        temporary = selected_dir / f".{token}.png"
        pixmap.save(temporary)
        document.close()
    except ImportError:
        _fail("pdf_renderer_unavailable", "PyMuPDF is required for exact source-region crops")
    except Exception as exc:
        if isinstance(exc, KernelContractError):
            raise
        _fail("source_region_render_failed", f"source region render failed: {exc}")
    artifact_sha256 = _sha256(temporary)
    destination = selected_dir / f"{artifact_sha256}.png"
    if destination.exists():
        if _sha256(destination) != artifact_sha256:
            _fail("media_asset_drift", "source region destination hash collided")
        temporary.unlink()
    else:
        temporary.replace(destination)
    try:
        from PIL import Image

        with Image.open(destination) as image:
            image.verify()
        with Image.open(destination) as image:
            width, height = image.size
            image_format = image.format
    except Exception as exc:
        _fail("source_region_render_invalid", f"source region crop is invalid: {exc}")
    relative = destination.relative_to(output).as_posix()
    return {
        **dict(candidate),
        "status": "needs_review",
        "crop_path": relative,
        "artifact_sha256": artifact_sha256,
        "size_bytes": destination.stat().st_size,
        "metrics": {
            "format": image_format,
            "width_px": width,
            "height_px": height,
            "render_scale": 2,
        },
        "anomaly_flags": ["source_region_review_not_bound_to_artifact"],
    }


def _verify_marker(
    marker_path: Path,
    *,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    status: str,
    source_sha256: str,
    source_size_bytes: int,
) -> dict[str, Any]:
    marker = _read_json(marker_path, f"{status} marker")
    if marker.get("schema") != "luceon-input-status-marker/v1":
        _fail("frozen_marker_schema_invalid", f"{status} marker schema is unsupported")
    if marker.get("status") != status:
        _fail("frozen_marker_status_invalid", f"{status} marker status differs")
    for field in ("material_id", "run_id"):
        if str(marker.get(field) or "") != str(manifest.get(field) or ""):
            _fail("frozen_marker_identity_mismatch", f"{status} marker {field} differs")
    marker_manifest = marker.get("manifest")
    if not isinstance(marker_manifest, Mapping):
        _fail("frozen_marker_manifest_missing", f"{status} marker has no manifest identity")
    if _require_sha(marker_manifest.get("sha256"), f"{status}.manifest.sha256") != _sha256(
        manifest_path
    ):
        _fail("frozen_marker_manifest_mismatch", f"{status} marker points to other bytes")
    if _require_positive_int(
        marker_manifest.get("size_bytes"), f"{status}.manifest.size_bytes"
    ) != manifest_path.stat().st_size:
        _fail("frozen_marker_manifest_mismatch", f"{status} marker manifest size differs")
    if _require_sha(
        marker.get("source_pdf_sha256"), f"{status}.source_pdf_sha256"
    ) != source_sha256:
        _fail("frozen_marker_source_mismatch", f"{status} marker source hash differs")
    if _require_positive_int(
        marker.get("source_pdf_size_bytes"), f"{status}.source_pdf_size_bytes"
    ) != source_size_bytes:
        _fail("frozen_marker_source_mismatch", f"{status} marker source size differs")
    return marker


def _pdf_geometry(source: Path) -> list[dict[str, Any]]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(source))
        if reader.is_encrypted and reader.decrypt("") == 0:
            _fail("source_pdf_encrypted", "source PDF cannot be opened without a password")
        pages = []
        for index, page in enumerate(reader.pages, 1):
            crop = page.cropbox
            width = float(crop.width)
            height = float(crop.height)
            if width <= 0 or height <= 0:
                _fail("source_pdf_geometry_invalid", f"PDF page {index} has invalid CropBox")
            pages.append(
                {
                    "physical_page": index,
                    "upstream_page_idx": index - 1,
                    "width_points": round(width, 6),
                    "height_points": round(height, 6),
                    "rotation_degrees": int(page.get("/Rotate", 0) or 0) % 360,
                    "cropbox_points": [
                        round(float(crop.left), 6),
                        round(float(crop.bottom), 6),
                        round(float(crop.right), 6),
                        round(float(crop.top), 6),
                    ],
                    "popo_bbox_basis": "pdf_cropbox_normalized_0_1_top_left",
                    "mineru_bbox_basis": "pdf_cropbox_normalized_0_1000_top_left",
                }
            )
    except KernelContractError:
        raise
    except Exception as exc:
        _fail("source_pdf_unparseable", f"source PDF cannot be fully parsed: {exc}")
    if not pages:
        _fail("source_pdf_empty", "source PDF has no pages")
    return pages


def _normalized_bbox(
    raw: Any,
    *,
    scale: float,
    label: str,
) -> list[float] | None:
    if raw is None:
        return None
    if (
        not isinstance(raw, list)
        or len(raw) != 4
        or any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in raw)
    ):
        _fail("bbox_invalid", f"{label} must contain four numeric coordinates")
    values = [float(item) / scale for item in raw]
    x0, y0, x1, y1 = values
    if min(values) < 0 or max(values) > 1.000001 or x1 < x0 or y1 < y0:
        _fail("bbox_invalid", f"{label} is outside its declared coordinate basis")
    return [round(item, 8) for item in values]


def _raw_content_hash(value: Any) -> str:
    if isinstance(value, str):
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    return _canonical_hash(value)


def _flatten_tree(root: Mapping[str, Any]) -> tuple[dict[str, int], dict[str, dict[str, Any]]]:
    ranks: dict[str, int] = {}
    contexts: dict[str, dict[str, Any]] = {}
    cursor = 0

    def walk(node: Mapping[str, Any], path: tuple[int, ...]) -> None:
        nonlocal cursor
        context = {
            "node_path": list(path),
            "node_type": node.get("type"),
            "node_title": node.get("title"),
            "node_level": node.get("level"),
        }
        block_ids = node.get("block_ids") or []
        if not isinstance(block_ids, list):
            _fail("popo_tree_invalid", "document_tree.block_ids must be arrays")
        for block_id in block_ids:
            key = str(block_id)
            if key not in ranks:
                ranks[key] = cursor
                contexts[key] = context
                cursor += 1
        children = node.get("children") or []
        if not isinstance(children, list):
            _fail("popo_tree_invalid", "document_tree.children must be arrays")
        for child_index, child in enumerate(children):
            if not isinstance(child, Mapping):
                _fail("popo_tree_invalid", "document_tree child must be an object")
            walk(child, path + (child_index,))

    walk(root, ())
    return ranks, contexts


def _normalize_popo_units(
    popo_raw: Any,
    document_tree: Mapping[str, Any],
    *,
    page_count: int,
    popo_run_id: str,
    popo_raw_entry: Mapping[str, Any],
    document_tree_entry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(popo_raw, list) or not popo_raw:
        _fail("popo_raw_invalid", "Popo raw evidence must be a non-empty array")
    ranks, contexts = _flatten_tree(document_tree)
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(popo_raw, 1):
        if not isinstance(raw, Mapping):
            _fail("popo_raw_invalid", f"Popo row {ordinal} must be an object")
        page = _require_positive_int(raw.get("page"), f"Popo row {ordinal}.page")
        if page > page_count:
            _fail("popo_page_out_of_range", f"Popo row {ordinal} references page {page}")
        source_id = str(raw.get("source_id") or "").strip()
        if not source_id:
            source_id = f"popo::{popo_run_id}::p{page:06d}::n{ordinal:09d}"
        if source_id in seen:
            _fail("popo_source_id_duplicate", f"Popo source_id {source_id!r} is duplicated")
        seen.add(source_id)
        raw_id = raw.get("id")
        raw_content = raw.get("content", "")
        rows.append(
            {
                "source_id": source_id,
                "popo_run_id": popo_run_id,
                "popo_raw_id": raw_id,
                "physical_page": page,
                "upstream_page_idx": page - 1,
                "bbox": _normalized_bbox(
                    raw.get("bbox"),
                    scale=1.0,
                    label=f"Popo row {ordinal}.bbox",
                ),
                "bbox_basis": "pdf_cropbox_normalized_0_1_top_left",
                "source_type": str(raw.get("type") or "unknown"),
                "source_label": str(raw.get("source_label") or raw.get("type") or "unknown"),
                "raw_content": raw_content,
                "raw_content_sha256": _raw_content_hash(raw_content),
                "popo_tree_rank": ranks.get(str(raw_id)),
                "tree_context": contexts.get(str(raw_id)),
                "archive_entry_evidence": {
                    "popo_raw": dict(popo_raw_entry),
                    "document_tree": dict(document_tree_entry),
                },
            }
        )
    return rows


def _content_payload(item: Mapping[str, Any]) -> Any:
    return item.get("content")


def _normalize_mineru_media(
    content_list: Any,
    *,
    page_count: int,
    mineru_run_id: str,
    assets_by_basename: Mapping[str, Sequence[Mapping[str, Any]]],
    content_list_entry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(content_list, list) or not content_list:
        _fail("mineru_content_list_invalid", "MinerU content_list_v2 must be pages")
    if len(content_list) > page_count:
        _fail("mineru_page_out_of_range", "MinerU evidence has more pages than source PDF")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page_number, page in enumerate(content_list, 1):
        if not isinstance(page, list):
            _fail("mineru_content_list_invalid", f"MinerU page {page_number} must be an array")
        for ordinal, item in enumerate(page, 1):
            if not isinstance(item, Mapping):
                _fail("mineru_content_list_invalid", "MinerU atom must be an object")
            item_type = str(item.get("type") or "")
            if item_type not in MEDIA_TYPES:
                continue
            bbox = _normalized_bbox(
                item.get("bbox"),
                scale=1000.0,
                label=f"MinerU page {page_number} atom {ordinal}.bbox",
            )
            content = _content_payload(item)
            image_source = content.get("image_source") if isinstance(content, Mapping) else None
            image_ref = (
                str(image_source.get("path") or "")
                if isinstance(image_source, Mapping)
                else ""
            )
            stable = {
                "run_id": mineru_run_id,
                "page": page_number,
                "ordinal": ordinal,
                "type": item_type,
                "bbox": bbox,
                "content_sha256": _raw_content_hash(content),
                "image_ref": image_ref,
            }
            media_id = "media-" + _canonical_hash(stable)[:24]
            if media_id in seen:
                _fail("mineru_media_id_duplicate", f"media identity {media_id} collided")
            seen.add(media_id)
            candidates: list[dict[str, Any]] = [
                {
                    "candidate_id": "source-pdf-region",
                    "representation_type": "source_region_image",
                    "source_page": page_number,
                    "bbox": bbox,
                    "bbox_coordinate_space": "pdf_cropbox_normalized_0_1_top_left",
                }
            ]
            normalized_image_ref = image_ref.replace("\\", "/").strip()
            if normalized_image_ref and not normalized_image_ref.endswith("/"):
                basename = PurePosixPath(normalized_image_ref).name
                matches = list(assets_by_basename.get(basename, ()))
                if len(matches) != 1:
                    _fail(
                        "mineru_media_asset_ambiguous",
                        f"MinerU media {media_id} has {len(matches)} declared assets named {basename!r}",
                    )
                asset = matches[0]
                candidates.insert(
                    0,
                    {
                        "candidate_id": f"mineru-asset::{asset['sha256'][:16]}::{basename}",
                        "representation_type": "source_asset_image",
                        "archive_provider": asset["provider"],
                        "archive": dict(asset["archive"]),
                        "archive_member": asset["source_member"],
                        "sha256": asset["sha256"],
                        "size_bytes": asset["size_bytes"],
                    },
                )
            if item_type in {"table", "chart", "equation_interline"}:
                candidates.append(
                    {
                        "candidate_id": "mineru-structured-transcription",
                        "representation_type": {
                            "table": "structured_table",
                            "chart": "structured_chart",
                            "equation_interline": "structured_formula",
                        }[item_type],
                        "payload": content,
                        "payload_sha256": _raw_content_hash(content),
                    }
                )
            rows.append(
                {
                    "media_id": media_id,
                    "mineru_run_id": mineru_run_id,
                    "source_atom_id": f"mineru::{mineru_run_id}::p{page_number:06d}::n{ordinal:09d}",
                    "physical_page": page_number,
                    "bbox": bbox,
                    "bbox_basis": "pdf_cropbox_normalized_0_1_top_left",
                    "media_kind": {
                        "equation_interline": "formula",
                    }.get(item_type, item_type),
                    "upstream_type": item_type,
                    "raw_content_sha256": _raw_content_hash(content),
                    "archive_entry_evidence": {
                        "content_list_v2": dict(content_list_entry),
                    },
                    "candidates": candidates,
                }
            )
    return rows


def _manifest_images(
    manifest: Mapping[str, Any],
    files: Mapping[str, Path],
    *,
    provider: str,
    archive_ref: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    objects = manifest.get("objects")
    raw_images = objects.get("images") if isinstance(objects, Mapping) else None
    if raw_images is None:
        raw_images = []
    if not isinstance(raw_images, list):
        _fail("manifest_images_invalid", f"{provider} objects.images must be an array")
    inventory: list[dict[str, Any]] = []
    by_basename: dict[str, list[dict[str, Any]]] = {}
    seen_members: set[str] = set()
    for index, raw in enumerate(raw_images, 1):
        if not isinstance(raw, Mapping):
            _fail("manifest_images_invalid", f"{provider} image {index} must be an object")
        member = _safe_relative(
            raw.get("source_member"),
            f"{provider} image {index}.source_member",
        )
        if member in seen_members:
            _fail("manifest_images_duplicate", f"{provider} repeats image member {member!r}")
        seen_members.add(member)
        source = files.get(member)
        if source is None:
            _fail("manifest_image_missing", f"{provider} image {member!r} is absent from archive")
        expected_sha = _require_sha(raw.get("sha256"), f"{provider} image {index}.sha256")
        expected_size = _require_positive_int(
            raw.get("size_bytes"), f"{provider} image {index}.size_bytes"
        )
        if _sha256(source) != expected_sha or source.stat().st_size != expected_size:
            _fail("manifest_image_mismatch", f"{provider} image {member!r} drifted")
        basename = PurePosixPath(member).name
        item = {
            "provider": provider,
            "archive": {
                "bucket": archive_ref["bucket"],
                "object": archive_ref["object"],
                "sha256": archive_ref["sha256"],
                "size_bytes": archive_ref["size_bytes"],
            },
            "source_member": member,
            "sha256": expected_sha,
            "size_bytes": expected_size,
        }
        inventory.append(item)
        by_basename.setdefault(basename, []).append(item)
    return inventory, by_basename


def _archive_group_counts(files: Mapping[str, Path], *, popo_only: bool) -> dict[str, int]:
    if popo_only:
        counts = {
            "minerupopo": 0,
            "enhanced": 0,
            "metadata": 0,
            "logs": 0,
            "other": 0,
            "skipped_mineru": 0,
        }
        for name in files:
            if name.startswith("mineru/"):
                counts["skipped_mineru"] += 1
            elif name.startswith("minerupopo/"):
                counts["minerupopo"] += 1
            elif name.startswith("enhanced/"):
                counts["enhanced"] += 1
            elif name.startswith("metadata/"):
                counts["metadata"] += 1
            elif name.startswith("logs/"):
                counts["logs"] += 1
            else:
                counts["other"] += 1
        return counts
    counts = {"mineru": 0, "minerupopo": 0, "metadata": 0, "logs": 0, "other": 0}
    for name in files:
        if name.startswith("mineru/"):
            counts["mineru"] += 1
        elif name.startswith("minerupopo/") or name.startswith("enhanced/"):
            counts["minerupopo"] += 1
        elif name.startswith("metadata/"):
            counts["metadata"] += 1
        elif name.startswith("logs/"):
            counts["logs"] += 1
        else:
            counts["other"] += 1
    return counts


def _verify_full_tree_counts(
    manifest: Mapping[str, Any],
    files: Mapping[str, Path],
    *,
    popo_only: bool,
    label: str,
) -> None:
    expected = manifest.get("full_tree_counts")
    if not isinstance(expected, Mapping):
        _fail("manifest_tree_counts_missing", f"{label} has no full_tree_counts")
    actual = _archive_group_counts(files, popo_only=popo_only)
    normalized = {key: int(expected.get(key, -1)) for key in actual}
    if normalized != actual:
        _fail(
            "archive_tree_count_mismatch",
            f"{label} archive groups differ from manifest: expected {normalized}, actual {actual}",
        )


def _materialized_inventory(root: Path, paths: Sequence[str]) -> list[dict[str, Any]]:
    rows = []
    for relative in sorted(paths):
        path = _contained_file(root, relative, f"materialized {relative}")
        rows.append(
            {
                "path": relative,
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
                "read_only_source": True,
            }
        )
    return rows


def _stage_manifest(
    output: Path,
    *,
    stage: str,
    run_id: str,
    schema_path: str,
    schema_sha256: str,
    gate_status: Mapping[str, str],
    artifacts: Mapping[str, str],
    metrics: Mapping[str, Any],
) -> str:
    manifest = {
        "schema_version": ATOMIC_STAGE_MANIFEST_SCHEMA,
        "producer": KERNEL_VERSION,
        "stage": stage,
        "run_id": run_id,
        "candidate_status": "complete",
        "spec_status": "not_evaluated",
        "promotion_status": "not_evaluated",
        "producer_gate_status": dict(gate_status),
        "contract_schema": {
            "path": schema_path,
            "sha256": schema_sha256,
        },
        "artifacts": {
            name: {"path": path, "sha256": _sha256(output / path)}
            for name, path in artifacts.items()
        },
        "metrics": dict(metrics),
        "scope_limit": (
            "Producer evidence only. Independent evaluation and promotion are not claimed."
        ),
    }
    return _write_json(output / f"manifests/{stage}_candidate_stage_manifest.json", manifest)


def _run_manifest(
    output: Path,
    *,
    job_id: str,
    run_id: str,
    stage: str,
    stage_manifest_hash: str,
) -> None:
    _write_json(
        output / "manifests/run_manifest.json",
        {
            "schema_version": RUN_MANIFEST_SCHEMA,
            "producer": KERNEL_VERSION,
            "job_id": job_id,
            "run_id": run_id,
            "stage": stage,
            "candidate_status": "complete",
            "spec_status": "not_evaluated",
            "promotion_status": "not_evaluated",
            "stage_manifest": {
                "path": f"manifests/{stage}_candidate_stage_manifest.json",
                "sha256": stage_manifest_hash,
            },
        },
    )


def _release_template_contract(
    release_manifest_path: Path,
    template_archive: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    release = _read_json(release_manifest_path, "release manifest")
    raw = release.get("template")
    if not isinstance(raw, Mapping):
        _fail("release_template_missing", "release has no fixed template contract")
    expected_sha = _require_sha(raw.get("archive_sha256"), "release.template.archive_sha256")
    if _sha256(template_archive) != expected_sha:
        _fail("template_release_binding_mismatch", "template archive differs from release")
    inventory = _safe_zip_inventory(template_archive)
    members = {item["member"] for item in inventory}
    required = [
        _safe_relative(raw.get("main_member"), "release.template.main_member"),
        _safe_relative(raw.get("class_member"), "release.template.class_member"),
    ]
    fixed_assets = raw.get("fixed_asset_members") or []
    if not isinstance(fixed_assets, list):
        _fail("release_template_invalid", "fixed_asset_members must be an array")
    required.extend(
        _safe_relative(item, "release.template.fixed_asset_members[]")
        for item in fixed_assets
    )
    missing = sorted(set(required) - members)
    if missing:
        _fail("template_member_missing", f"template lacks required members: {missing}")
    return dict(raw), inventory


def produce_intake(args: argparse.Namespace) -> dict[str, Any]:
    output = _prepare_stage_output(args.output_dir)
    source = args.source_pdf.resolve()
    mineru_manifest_path = args.mineru_manifest.resolve()
    popo_manifest_path = args.popo_manifest.resolve()
    mineru_archive = args.mineru_archive.resolve()
    popo_archive = args.popo_archive.resolve()
    template_archive = args.template_archive.resolve()
    source_sha = _sha256(source)
    source_size = source.stat().st_size
    geometry = _pdf_geometry(source)
    page_count = len(geometry)

    mineru_manifest = _read_json(mineru_manifest_path, "MinerU manifest")
    popo_manifest = _read_json(popo_manifest_path, "Popo manifest")
    if mineru_manifest.get("schema") not in SUPPORTED_MINERU_SCHEMAS:
        _fail("mineru_manifest_schema_unsupported", "MinerU manifest schema is unsupported")
    if popo_manifest.get("schema") not in SUPPORTED_POPO_SCHEMAS:
        _fail("popo_manifest_schema_unsupported", "Popo manifest schema is unsupported")
    material_id = _require_text(popo_manifest.get("material_id"), "Popo material_id")
    if _require_text(mineru_manifest.get("material_id"), "MinerU material_id") != material_id:
        _fail("material_identity_mismatch", "MinerU and Popo material_id differ")
    mineru_run_id = _require_text(mineru_manifest.get("run_id"), "MinerU run_id")
    popo_run_id = _require_text(popo_manifest.get("run_id"), "Popo run_id")
    for manifest, label in ((mineru_manifest, "MinerU"), (popo_manifest, "Popo")):
        manifest_source = manifest.get("source_pdf")
        if not isinstance(manifest_source, Mapping):
            _fail("source_identity_missing", f"{label} manifest has no source_pdf")
        if _require_sha(manifest_source.get("sha256"), f"{label}.source_pdf.sha256") != source_sha:
            _fail("source_identity_mismatch", f"{label} source PDF hash differs")
        if _require_positive_int(
            manifest_source.get("size_bytes"), f"{label}.source_pdf.size_bytes"
        ) != source_size:
            _fail("source_identity_mismatch", f"{label} source PDF size differs")

    upstream = popo_manifest.get("upstream_mineru")
    if not isinstance(upstream, Mapping):
        _fail("popo_mineru_lineage_missing", "Popo manifest has no upstream_mineru")
    if _require_text(upstream.get("run_id"), "Popo upstream MinerU run_id") != mineru_run_id:
        _fail("popo_mineru_lineage_mismatch", "Popo points to another MinerU run")
    upstream_manifest = upstream.get("manifest")
    mineru_marker = _verify_marker(
        args.mineru_marker.resolve(),
        manifest_path=mineru_manifest_path,
        manifest=mineru_manifest,
        status="mineru_done_frozen",
        source_sha256=source_sha,
        source_size_bytes=source_size,
    )
    popo_marker = _verify_marker(
        args.popo_marker.resolve(),
        manifest_path=popo_manifest_path,
        manifest=popo_manifest,
        status="popo_done_frozen",
        source_sha256=source_sha,
        source_size_bytes=source_size,
    )
    if not isinstance(upstream_manifest, Mapping):
        _fail("popo_mineru_lineage_missing", "Popo upstream manifest identity is absent")
    mineru_marker_manifest = mineru_marker.get("manifest")
    if not isinstance(mineru_marker_manifest, Mapping):
        _fail("popo_mineru_lineage_missing", "MinerU marker has no manifest identity")
    if any(
        str(upstream_manifest.get(field) or "")
        != str(mineru_marker_manifest.get(field) or "")
        for field in ("bucket", "object")
    ):
        _fail("popo_mineru_lineage_mismatch", "Popo points to another MinerU manifest")
    marker_upstream = popo_marker.get("mineru_manifest")
    if isinstance(marker_upstream, Mapping) and any(
        str(marker_upstream.get(field) or "")
        != str(mineru_marker_manifest.get(field) or "")
        for field in ("bucket", "object")
    ):
        _fail("popo_mineru_lineage_mismatch", "Popo marker points to another MinerU")

    mineru_archive_ref = _object_ref(mineru_manifest, "archive", "MinerU manifest")
    popo_archive_ref = _object_ref(popo_manifest, "archive", "Popo manifest")
    _assert_object_bytes(mineru_archive_ref, mineru_archive, "MinerU archive")
    _assert_object_bytes(popo_archive_ref, popo_archive, "Popo archive")

    scratch = output.parent / f".{output.name}-archive-work"
    if scratch.exists() or scratch.is_symlink():
        _fail("isolated_workspace_violation", "archive work directory already exists")
    scratch.mkdir(parents=True)
    try:
        mineru_files = _safe_extract_tar(
            mineru_archive,
            scratch / "mineru",
            "MinerU archive",
        )
        popo_files = _safe_extract_tar(
            popo_archive,
            scratch / "popo",
            "Popo archive",
        )
        _verify_full_tree_counts(
            mineru_manifest,
            mineru_files,
            popo_only=False,
            label="MinerU manifest",
        )
        _verify_full_tree_counts(
            popo_manifest,
            popo_files,
            popo_only=True,
            label="Popo manifest",
        )
        mineru_content_ref = _object_ref(
            mineru_manifest,
            "content_list_v2",
            "MinerU manifest",
        )
        mineru_content_member, mineru_content_path = _find_unique_member(
            mineru_files,
            endings=("_content_list_v2.json", "content_list_v2.json"),
            expected=mineru_content_ref,
            label="MinerU content_list_v2",
        )
        popo_raw_ref = _object_ref(popo_manifest, "popo_raw", "Popo manifest")
        popo_raw_member, popo_raw_path = _find_unique_member(
            popo_files,
            endings=("enhanced/popo_raw.json", "popo_raw.json"),
            expected=popo_raw_ref,
            label="Popo raw evidence",
        )
        popo_tree_ref = _object_ref(popo_manifest, "document_tree", "Popo manifest")
        popo_tree_member, popo_tree_path = _find_unique_member(
            popo_files,
            endings=("enhanced/document_tree.json", "document_tree.json"),
            expected=popo_tree_ref,
            label="Popo document tree",
        )

        mineru_assets, mineru_assets_by_basename = _manifest_images(
            mineru_manifest,
            mineru_files,
            provider="mineru",
            archive_ref=mineru_archive_ref,
        )
        popo_assets, _ = _manifest_images(
            popo_manifest,
            popo_files,
            provider="popo",
            archive_ref=popo_archive_ref,
        )
        media_asset_inventory = [*mineru_assets, *popo_assets]
        _write_json(
            output / "source/media_asset_inventory.json",
            {
                "schema_version": "luceon.worker-v3-media-asset-inventory/v1",
                "assets": media_asset_inventory,
                "summary": {
                    "assets": len(media_asset_inventory),
                    "providers": dict(Counter(item["provider"] for item in media_asset_inventory)),
                },
            },
        )

        mineru_content_entry = _archive_entry_identity(
            provider="mineru",
            archive_ref=mineru_archive_ref,
            member=mineru_content_member,
            path=mineru_content_path,
        )
        popo_raw_entry = _archive_entry_identity(
            provider="popo",
            archive_ref=popo_archive_ref,
            member=popo_raw_member,
            path=popo_raw_path,
        )
        popo_tree_entry = _archive_entry_identity(
            provider="popo",
            archive_ref=popo_archive_ref,
            member=popo_tree_member,
            path=popo_tree_path,
        )
        archive_entry_rows = [
            mineru_content_entry,
            popo_raw_entry,
            popo_tree_entry,
            *[
                {
                    "provider": item["provider"],
                    "archive": item["archive"],
                    "member": item["source_member"],
                    "member_sha256": item["sha256"],
                    "member_size_bytes": item["size_bytes"],
                }
                for item in media_asset_inventory
            ],
        ]
        archive_entry_hash = _write_jsonl(
            output / "source/archive_entry_evidence.jsonl",
            sorted(
                archive_entry_rows,
                key=lambda item: (item["provider"], item["member"]),
            ),
        )

        popo_units = _normalize_popo_units(
            _read_json_value(popo_raw_path, "Popo raw evidence"),
            _read_json(popo_tree_path, "Popo document tree"),
            page_count=page_count,
            popo_run_id=popo_run_id,
            popo_raw_entry=popo_raw_entry,
            document_tree_entry=popo_tree_entry,
        )
        popo_units_hash = _write_jsonl(output / "source/popo_source_units.jsonl", popo_units)
        mineru_media = _normalize_mineru_media(
            _read_json_value(mineru_content_path, "MinerU content_list_v2"),
            page_count=page_count,
            mineru_run_id=mineru_run_id,
            assets_by_basename=mineru_assets_by_basename,
            content_list_entry=mineru_content_entry,
        )
        mineru_media_hash = _write_jsonl(
            output / "source/mineru_media_atoms.jsonl",
            mineru_media,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    materialized_paths = [
        "source/archive_entry_evidence.jsonl",
        "source/media_asset_inventory.json",
        "source/mineru_media_atoms.jsonl",
        "source/popo_source_units.jsonl",
    ]

    template_contract, template_inventory = _release_template_contract(
        args.release_manifest.resolve(),
        template_archive,
    )
    geometry_hash = _write_json(
        output / "evidence/pdf_page_geometry.json",
        {
            "schema_version": "luceon.worker-v3-pdf-page-geometry/v1",
            "source_pdf_sha256": source_sha,
            "pages": geometry,
        },
    )
    template_intake_hash = _write_json(
        output / "contracts/template_intake.json",
        {
            "schema_version": "template-intake/1.0",
            "archive_sha256": _sha256(template_archive),
            "archive_size_bytes": template_archive.stat().st_size,
            "main_member": template_contract["main_member"],
            "class_member": template_contract["class_member"],
            "fixed_asset_members": list(template_contract.get("fixed_asset_members") or []),
            "inventory": template_inventory,
            "candidate_configuration_points": [],
            "freeze_status": "identified_not_yet_spec05_verified",
        },
    )
    source_trace_hash = _write_json(
        output / "contracts/source_trace.json",
        {
            "schema_version": "source-trace/1.0",
            "material_id": material_id,
            "source_pdf": {
                "sha256": source_sha,
                "size_bytes": source_size,
                "page_count": page_count,
                "storage": "external_frozen_input",
            },
            "mineru": {
                "run_id": mineru_run_id,
                "manifest": {
                    "sha256": _sha256(mineru_manifest_path),
                    "size_bytes": mineru_manifest_path.stat().st_size,
                },
                "archive": dict(mineru_archive_ref),
                "content_entry": mineru_content_entry,
            },
            "popo": {
                "run_id": popo_run_id,
                "manifest": {
                    "sha256": _sha256(popo_manifest_path),
                    "size_bytes": popo_manifest_path.stat().st_size,
                },
                "archive": dict(popo_archive_ref),
                "popo_raw_entry": popo_raw_entry,
                "document_tree_entry": popo_tree_entry,
                "upstream_mineru_run_id": mineru_run_id,
            },
        },
    )
    materialized_manifest_hash = _write_json(
        output / "contracts/materialized_manifest.json",
        {
            "schema_version": "compact-reference-manifest/1.0",
            "material_id": material_id,
            "entries": _materialized_inventory(output, materialized_paths),
            "external_frozen_inputs_are_not_materialized": True,
            "source_archive_identities": {
                "mineru": dict(mineru_archive_ref),
                "popo": dict(popo_archive_ref),
            },
        },
    )
    decision_event = {
        "schema_version": "decision-event/1.0",
        "decision_id": args.stage_decision_id,
        "rule_id": "IN-H01..IN-H17",
        "status": "closed",
        "decision": "Freeze the exact source PDF, staged MinerU and Popo lineage, and release-approved template as the immutable Spec 01 candidate baseline.",
        "evidence_refs": [
            "contracts/source_trace.json",
            "contracts/materialized_manifest.json",
            "evidence/pdf_page_geometry.json",
            "contracts/template_intake.json",
        ],
    }
    decisions_hash = _write_jsonl(
        output / "decisions/input_decisions.jsonl",
        [decision_event],
    )
    decision_index = {
        "schema_version": DECISION_INDEX_SCHEMA,
        "decision_index_id": args.decision_index_id,
        "snapshot_id": args.decision_snapshot_id,
        "version": 1,
        "parent_index_ref": None,
        "parent_index_hash": None,
        "acyclic_commit_rule": "evidence_E1_then_decision_index_D1_then_input_contract",
        "evidence_committed_before_index": [
            {"ref": "contracts/source_trace.json", "sha256": source_trace_hash},
            {
                "ref": "contracts/materialized_manifest.json",
                "sha256": materialized_manifest_hash,
            },
            {"ref": "evidence/pdf_page_geometry.json", "sha256": geometry_hash},
            {"ref": "contracts/template_intake.json", "sha256": template_intake_hash},
        ],
        "decision_event_files": [
            {"path": "decisions/input_decisions.jsonl", "sha256": decisions_hash}
        ],
        "decisions": [
            {
                "decision_id": args.stage_decision_id,
                "status": "closed",
                "rule_id": "IN-H01..IN-H17",
                "event_file": "decisions/input_decisions.jsonl",
            }
        ],
        "summary": {
            "total": 1,
            "closed": 1,
            "open": 0,
            "stale": 0,
            "invalidated": 0,
        },
        "spec_status": "passed",
    }
    decision_index_hash = _write_json(
        output / "decisions/canonical_decision_index.json",
        decision_index,
    )
    gates = {f"IN-H{index:02d}": "passed" for index in range(1, 18)}
    input_contract_hash = _write_json(
        output / "contracts/input_contract.json",
        {
            "schema_version": INTAKE_SCHEMA,
            "producer": KERNEL_VERSION,
            "run_id": args.run_id,
            "material_identity": {
                "material_id": material_id,
                "source_pdf_sha256": source_sha,
                "source_pdf_size_bytes": source_size,
                "page_count": page_count,
                "mineru_run_id": mineru_run_id,
                "popo_run_id": popo_run_id,
            },
            "inputs": {
                "source_pdf": {
                    "sha256": source_sha,
                    "size_bytes": source_size,
                    "storage": "external_frozen_input",
                },
                "mineru_manifest": {
                    "sha256": _sha256(mineru_manifest_path),
                    "size_bytes": mineru_manifest_path.stat().st_size,
                    "storage": "external_frozen_input",
                },
                "mineru_archive": {
                    **dict(mineru_archive_ref),
                    "storage": "external_frozen_input",
                },
                "popo_manifest": {
                    "sha256": _sha256(popo_manifest_path),
                    "size_bytes": popo_manifest_path.stat().st_size,
                    "storage": "external_frozen_input",
                },
                "popo_archive": {
                    **dict(popo_archive_ref),
                    "storage": "external_frozen_input",
                },
                "template_archive": {
                    "sha256": _sha256(template_archive),
                    "size_bytes": template_archive.stat().st_size,
                    "storage": "external_frozen_input",
                },
            },
            "canonical_decision_index_ref": "decisions/canonical_decision_index.json",
            "canonical_decision_index_hash": decision_index_hash,
            "gate_status": gates,
            "spec_status": "passed",
            "open_reviews": [],
        },
    )
    validation_hash = _write_json(
        output / "reports/input_validation_report.json",
        {
            "schema_version": "input-validation-report/1.0",
            "run_id": args.run_id,
            "gate_status": gates,
            "failure_codes": [],
            "open_reviews": [],
            "spec_status": "passed",
            "counts": {
                "pages": page_count,
                "popo_source_units": len(popo_units),
                "mineru_media_atoms": len(mineru_media),
                "compact_candidate_files": len(materialized_paths),
                "external_frozen_files_copied": 0,
            },
        },
    )
    stage_manifest_hash = _stage_manifest(
        output,
        stage="intake_snapshot",
        run_id=args.run_id,
        schema_path=args.contract_schema_path,
        schema_sha256=args.contract_schema_sha256,
        gate_status=gates,
        artifacts={
            "input_contract": "contracts/input_contract.json",
            "source_trace": "contracts/source_trace.json",
            "materialized_manifest": "contracts/materialized_manifest.json",
            "input_validation_report": "reports/input_validation_report.json",
            "template_intake": "contracts/template_intake.json",
            "decision_index": "decisions/canonical_decision_index.json",
        },
        metrics={
            "page_count": page_count,
            "popo_source_units": len(popo_units),
            "mineru_media_atoms": len(mineru_media),
            "input_contract_sha256": input_contract_hash,
            "validation_report_sha256": validation_hash,
            "popo_source_units_sha256": popo_units_hash,
            "mineru_media_atoms_sha256": mineru_media_hash,
            "archive_entry_evidence_sha256": archive_entry_hash,
            "external_frozen_files_copied": 0,
        },
    )
    _run_manifest(
        output,
        job_id=args.job_id,
        run_id=args.run_id,
        stage="intake_snapshot",
        stage_manifest_hash=stage_manifest_hash,
    )
    return {
        "candidate_status": "complete",
        "spec_status": "not_evaluated",
        "pages": page_count,
        "source_units": len(popo_units),
        "media_atoms": len(mineru_media),
    }


def _verify_materialized_parent(parent: Path) -> dict[str, Any]:
    contract = _read_json(parent / "contracts/input_contract.json", "input contract")
    if contract.get("schema_version") != INTAKE_SCHEMA or contract.get("spec_status") != "passed":
        _fail("parent_intake_contract_invalid", "promoted intake contract is not closed")
    manifest = _read_json(
        parent / "contracts/materialized_manifest.json",
        "materialized manifest",
    )
    if (
        manifest.get("schema_version") != "compact-reference-manifest/1.0"
        or manifest.get("external_frozen_inputs_are_not_materialized") is not True
    ):
        _fail("parent_materialization_invalid", "parent is not a compact reference bundle")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        _fail("parent_materialization_invalid", "materialized manifest is empty")
    seen: set[str] = set()
    for index, row in enumerate(entries, 1):
        if not isinstance(row, Mapping):
            _fail("parent_materialization_invalid", f"entry {index} must be an object")
        relative = _safe_relative(row.get("path"), f"materialized entry {index}.path")
        if relative in seen:
            _fail("parent_materialization_invalid", f"duplicate entry {relative!r}")
        if PurePosixPath(relative).suffix.lower() in {
            ".pdf",
            ".zip",
            ".tar",
            ".tgz",
            ".gz",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
        }:
            _fail("parent_materialization_invalid", f"forbidden frozen binary {relative!r}")
        seen.add(relative)
        path = _contained_file(parent, relative, f"materialized entry {relative}")
        if _require_sha(row.get("sha256"), f"{relative}.sha256") != _sha256(path):
            _fail("parent_materialization_drift", f"{relative} hash drifted")
        size_parser = (
            _require_nonnegative_int
            if relative == "source/mineru_media_atoms.jsonl"
            else _require_positive_int
        )
        if size_parser(row.get("size_bytes"), f"{relative}.size_bytes") != path.stat().st_size:
            _fail("parent_materialization_drift", f"{relative} size drifted")
    return contract


def _scope_content_excerpt(value: object) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= SCOPE_REVIEW_EXCERPT_CHARS:
        return text
    head = SCOPE_REVIEW_EXCERPT_CHARS * 2 // 3
    tail = SCOPE_REVIEW_EXCERPT_CHARS - head - 1
    return text[:head] + "…" + text[-tail:]


def _scope_unit_sort_key(unit: Mapping[str, Any]) -> tuple[object, ...]:
    bbox = unit.get("bbox")
    normalized_bbox = (
        tuple(float(item) for item in bbox)
        if isinstance(bbox, list) and len(bbox) == 4
        else (0.0, 0.0, 0.0, 0.0)
    )
    tree = unit.get("tree_context")
    node_path = (
        tuple(int(item) for item in tree.get("node_path") or [])
        if isinstance(tree, Mapping)
        else ()
    )
    rank = unit.get("popo_tree_rank")
    return (
        rank is None,
        int(rank) if isinstance(rank, int) and not isinstance(rank, bool) else 0,
        node_path,
        normalized_bbox[1],
        normalized_bbox[0],
        str(unit.get("source_id") or ""),
    )


def _scope_baseline(
    *,
    page_count: int,
    source_units: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    source_by_page: dict[int, list[Mapping[str, Any]]] = {
        page: [] for page in range(1, page_count + 1)
    }
    for unit in source_units:
        page = int(unit["physical_page"])
        if page not in source_by_page:
            _fail("review_task_source_invalid", f"source unit references unknown page {page}")
        source_by_page[page].append(unit)

    baseline_pages: list[dict[str, Any]] = []
    baseline_units: list[dict[str, Any]] = []
    for page in range(1, page_count + 1):
        ordered = sorted(source_by_page[page], key=_scope_unit_sort_key)
        page_units: list[dict[str, Any]] = []
        for page_order, unit in enumerate(ordered, 1):
            label = str(unit.get("source_label") or "").strip().lower()
            source_type = str(unit.get("source_type") or "").strip().lower()
            excluded = (
                label in SCOPE_BASELINE_EXCLUDED_LABELS
                or source_type in SCOPE_BASELINE_EXCLUDED_LABELS
            )
            row = {
                "source_id": str(unit["source_id"]),
                "physical_page": page,
                "scope_status": "excluded" if excluded else "included",
                "scope_reason": (
                    f"deterministic non-body label:{label or source_type}"
                    if excluded
                    else "deterministic Popo source unit"
                ),
                "baseline_page_order": page_order,
                "evidence_refs": [f"source:{unit['source_id']}"],
            }
            page_units.append(row)
            baseline_units.append(row)
        included = [row for row in page_units if row["scope_status"] == "included"]
        if not page_units:
            category = "blank"
        elif not included:
            category = "non_body"
        elif any(
            str(unit.get("source_type") or "").lower() in MEDIA_TYPES
            or str(unit.get("source_label") or "").lower() in MEDIA_TYPES
            for unit in ordered
        ):
            category = "body_with_media"
        else:
            category = "body"
        baseline_pages.append(
            {
                "physical_page": page,
                "scope_status": "included" if included else "excluded",
                "page_category": category,
                "reason": (
                    "contains deterministic body source units"
                    if included
                    else "contains no deterministic body source units"
                ),
                "evidence_refs": [
                    f"source:{row['source_id']}" for row in page_units[:4]
                ]
                or [f"physical-page:{page}"],
            }
        )
    baseline = {
        "schema_version": "luceon.worker-v3-spec02-deterministic-baseline/v1",
        "algorithm": SCOPE_BASELINE_ALGORITHM,
        "pages": baseline_pages,
        "source_units": baseline_units,
    }
    baseline["sha256"] = _canonical_hash(baseline)
    return baseline


def _scope_page_complexity(
    source_units: Sequence[Mapping[str, Any]],
    media_atoms: Sequence[Mapping[str, Any]],
) -> list[str]:
    flags: list[str] = []
    if len(source_units) > 30:
        flags.append("dense_page")
    if any(unit.get("popo_tree_rank") is None for unit in source_units):
        flags.append("missing_popo_tree_rank")
    if media_atoms and any(
        str(unit.get("source_type") or "").lower() not in MEDIA_TYPES
        for unit in source_units
    ):
        flags.append("mixed_media_and_text")
    x_centers = sorted(
        (float(unit["bbox"][0]) + float(unit["bbox"][2])) / 2
        for unit in source_units
        if isinstance(unit.get("bbox"), list) and len(unit["bbox"]) == 4
    )
    if (
        len(x_centers) >= 6
        and sum(center < 0.45 for center in x_centers) >= 2
        and sum(center > 0.55 for center in x_centers) >= 2
    ):
        flags.append("possible_multi_column")
    return flags


def _minimum_scope_review_bytes(
    *,
    material_id: str,
    source_pdf_sha256: str,
    baseline_sha256: str,
    page_count: int,
) -> int:
    minimal = {
        "schema_version": SCOPE_REVIEW_SCHEMA,
        "review_id": "x",
        "material_id": material_id,
        "source_pdf_sha256": source_pdf_sha256,
        "baseline_sha256": baseline_sha256,
        "review_status": "closed",
        "pages": [
            {
                "physical_page": page,
                "baseline_disposition": "accepted",
            }
            for page in range(1, page_count + 1)
        ],
        "page_overrides": [],
        "unit_scope_overrides": [],
        "reading_order_overrides": [],
        "relationships": [],
        "open_reviews": [],
    }
    return len(_canonical_bytes(minimal))


def _scope_review_task(parent: Path) -> dict[str, Any]:
    contract = _verify_materialized_parent(parent)
    identity = contract["material_identity"]
    source_units = _read_jsonl(parent / "source/popo_source_units.jsonl", "Popo source units")
    media_atoms = _read_jsonl(parent / "source/mineru_media_atoms.jsonl", "MinerU media atoms")
    geometry = _read_json(parent / "evidence/pdf_page_geometry.json", "PDF geometry")
    page_count = int(identity["page_count"])
    baseline = _scope_baseline(
        page_count=page_count,
        source_units=source_units,
    )
    baseline_units = {
        row["source_id"]: row for row in baseline["source_units"]
    }
    baseline_pages = {
        int(row["physical_page"]): row for row in baseline["pages"]
    }
    pages_by_number = {
        int(item["physical_page"]): {
            "physical_page": int(item["physical_page"]),
            "geometry": {
                "physical_page": int(item["physical_page"]),
                "width_points": item["width_points"],
                "height_points": item["height_points"],
                "rotation_degrees": item["rotation_degrees"],
            },
            "source_units": [],
            "mineru_media_atoms": [],
        }
        for item in geometry.get("pages") or []
        if isinstance(item, Mapping)
    }
    expected_pages = set(range(1, page_count + 1))
    if set(pages_by_number) != expected_pages:
        _fail("review_task_geometry_invalid", "PDF geometry does not cover every page")
    for unit in source_units:
        page = int(unit["physical_page"])
        if page not in pages_by_number:
            _fail("review_task_source_invalid", f"source unit references unknown page {page}")
        pages_by_number[page]["source_units"].append(
            {
                "source_id": unit["source_id"],
                "source_label": unit["source_label"],
                "bbox": unit["bbox"],
                "content_excerpt": _scope_content_excerpt(unit["raw_content"]),
                "popo_tree_rank": unit.get("popo_tree_rank"),
                "baseline_scope_status": baseline_units[unit["source_id"]][
                    "scope_status"
                ],
                "baseline_page_order": baseline_units[unit["source_id"]][
                    "baseline_page_order"
                ],
            }
        )
    for atom in media_atoms:
        page = int(atom["physical_page"])
        if page not in pages_by_number:
            _fail("review_task_source_invalid", f"media atom references unknown page {page}")
        pages_by_number[page]["mineru_media_atoms"].append(
            {
                "media_id": atom["media_id"],
                "media_kind": atom["media_kind"],
                "bbox": atom["bbox"],
            }
        )
    for page in pages_by_number.values():
        page["source_units"].sort(key=_scope_unit_sort_key)
        page["mineru_media_atoms"].sort(key=lambda item: item["media_id"])
        page_number = int(page["physical_page"])
        page["baseline_scope_status"] = baseline_pages[page_number]["scope_status"]
        page["baseline_page_category"] = baseline_pages[page_number]["page_category"]
        page["complexity_flags"] = _scope_page_complexity(
            page["source_units"],
            page["mineru_media_atoms"],
        )
    task = {
        "schema_version": SCOPE_REVIEW_TASK_SCHEMA,
        "stage_key": "source_scope_and_order",
        "material_id": identity["material_id"],
        "source_pdf_sha256": identity["source_pdf_sha256"],
        "page_count": page_count,
        "source_unit_count": len(source_units),
        "bbox_basis": "pdf_cropbox_normalized_0_1_top_left",
        "baseline_algorithm": SCOPE_BASELINE_ALGORITHM,
        "baseline_sha256": baseline["sha256"],
        "required_output_schema": SCOPE_REVIEW_SCHEMA,
        "allowed_choices": {
            "page_scope_status": ["included", "excluded"],
            "unit_scope_status": ["included", "excluded"],
            "relationship_type": [
                "semantic_group",
                "cross_page_group",
                "stem_media_options",
            ],
        },
        "constraints": [
            "Classify every physical page exactly once.",
            "Accept the deterministic page baseline compactly and return full page fields only for overrides.",
            "Return only source-unit scope or page-order overrides that differ from the deterministic baseline.",
            "A reading-order override must enumerate every included source_id on that page exactly once.",
            "Close complex, multi-column, cross-page, and composite relationships with evidence.",
            "Do not infer scope from filename, title keywords, language, or sample identity.",
        ],
        "pages": [pages_by_number[index] for index in sorted(pages_by_number)],
    }
    task["capacity"] = {
        "minimum_response_bytes": _minimum_scope_review_bytes(
            material_id=identity["material_id"],
            source_pdf_sha256=identity["source_pdf_sha256"],
            baseline_sha256=baseline["sha256"],
            page_count=page_count,
        )
    }
    task["task_id"] = "scope-review-" + _canonical_hash(task)[:24]
    return task


def _compact_identifier(value: str, delimiter: str) -> tuple[str, str]:
    prefix, separator, suffix = value.rpartition(delimiter)
    if not separator:
        return "", value
    return prefix + separator, suffix


def scope_model_evidence(task: Mapping[str, Any]) -> dict[str, Any]:
    """Project the exhaustive Spec 02 task without discarding evidence.

    Large books repeat the same JSON field names and identifier prefixes many
    thousands of times.  The release-bound model request has a hard byte
    budget, so transmit a field table plus positional rows while retaining all
    page, geometry, unit, excerpt, ordering, and media evidence.
    """

    pages = task.get("pages")
    if not isinstance(pages, list) or not pages:
        _fail("review_task_pages_invalid", "scope review task pages are invalid")

    geometry_profiles: list[list[Any]] = []
    geometry_indexes: dict[tuple[Any, Any, Any], int] = {}
    source_prefixes: list[str] = []
    source_prefix_indexes: dict[str, int] = {}
    media_prefixes: list[str] = []
    media_prefix_indexes: dict[str, int] = {}
    compact_pages: list[list[Any]] = []

    def prefix_index(
        value: str,
        delimiter: str,
        rows: list[str],
        indexes: dict[str, int],
    ) -> tuple[int, str]:
        prefix, suffix = _compact_identifier(value, delimiter)
        if prefix not in indexes:
            indexes[prefix] = len(rows)
            rows.append(prefix)
        return indexes[prefix], suffix

    for page in pages:
        if not isinstance(page, Mapping):
            _fail("review_task_page_invalid", "scope review task page is invalid")
        geometry = page.get("geometry")
        units = page.get("source_units")
        media_atoms = page.get("mineru_media_atoms")
        if (
            not isinstance(geometry, Mapping)
            or not isinstance(units, list)
            or not isinstance(media_atoms, list)
        ):
            _fail("review_task_page_invalid", "scope review task page evidence is invalid")
        profile = (
            geometry.get("width_points"),
            geometry.get("height_points"),
            geometry.get("rotation_degrees"),
        )
        if profile not in geometry_indexes:
            geometry_indexes[profile] = len(geometry_profiles)
            geometry_profiles.append(list(profile))
        compact_units: list[list[Any]] = []
        for unit in units:
            if not isinstance(unit, Mapping) or not isinstance(
                unit.get("source_id"), str
            ):
                _fail("review_task_source_invalid", "scope review source unit is invalid")
            prefix, suffix = prefix_index(
                unit["source_id"], ":", source_prefixes, source_prefix_indexes
            )
            compact_units.append(
                [
                    prefix,
                    suffix,
                    unit.get("source_label"),
                    unit.get("bbox"),
                    unit.get("content_excerpt"),
                    unit.get("popo_tree_rank"),
                    unit.get("baseline_scope_status"),
                    unit.get("baseline_page_order"),
                ]
            )
        compact_media: list[list[Any]] = []
        for atom in media_atoms:
            if not isinstance(atom, Mapping) or not isinstance(
                atom.get("media_id"), str
            ):
                _fail("review_task_source_invalid", "scope review media atom is invalid")
            prefix, suffix = prefix_index(
                atom["media_id"], "-", media_prefixes, media_prefix_indexes
            )
            compact_media.append(
                [
                    prefix,
                    suffix,
                    atom.get("media_kind"),
                    atom.get("bbox"),
                ]
            )
        compact_pages.append(
            [
                page.get("physical_page"),
                geometry_indexes[profile],
                page.get("baseline_scope_status"),
                page.get("baseline_page_category"),
                page.get("complexity_flags"),
                compact_units,
                compact_media,
            ]
        )

    projected = {
        key: task[key]
        for key in (
            "stage_key",
            "material_id",
            "source_pdf_sha256",
            "page_count",
            "source_unit_count",
            "bbox_basis",
            "baseline_algorithm",
            "baseline_sha256",
            "required_output_schema",
            "allowed_choices",
            "constraints",
            "capacity",
        )
    }
    projected.update(
        {
            "schema_version": "luceon.worker-v3-spec02-model-evidence/v1",
            "encoding": {
                "geometry_profile_fields": [
                    "width_points",
                    "height_points",
                    "rotation_degrees",
                ],
                "page_fields": [
                    "physical_page",
                    "geometry_profile_index",
                    "baseline_scope_status",
                    "baseline_page_category",
                    "complexity_flags",
                    "source_units",
                    "mineru_media_atoms",
                ],
                "source_unit_fields": [
                    "source_id_prefix_index",
                    "source_id_suffix",
                    "source_label",
                    "bbox",
                    "content_excerpt",
                    "popo_tree_rank",
                    "baseline_scope_status",
                    "baseline_page_order",
                ],
                "media_atom_fields": [
                    "media_id_prefix_index",
                    "media_id_suffix",
                    "media_kind",
                    "bbox",
                ],
                "identifier_rule": (
                    "Reconstruct every full identifier by concatenating the "
                    "indexed prefix and suffix exactly. Return only full identifiers."
                ),
            },
            "geometry_profiles": geometry_profiles,
            "source_id_prefixes": source_prefixes,
            "media_id_prefixes": media_prefixes,
            "pages": compact_pages,
        }
    )
    projected["task_id"] = "scope-model-" + _canonical_hash(projected)[:24]
    return projected


def prepare_scope_review_task(args: argparse.Namespace) -> dict[str, Any]:
    task = _scope_review_task(args.parent.resolve())
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        _fail("review_task_output_exists", "scope review task output already exists")
    task_hash = _write_json(output, task)
    return {
        "status": "prepared",
        "task_sha256": task_hash,
        "task_canonical_sha256": _canonical_hash(task),
        "pages": task["page_count"],
        "source_units": task["source_unit_count"],
        "baseline_sha256": task["baseline_sha256"],
        "minimum_response_bytes": task["capacity"]["minimum_response_bytes"],
    }


def _minimum_media_review_bytes(
    *,
    material_id: str,
    source_pdf_sha256: str,
    baseline_sha256: str,
    media_count: int,
) -> int:
    minimal = {
        "schema_version": MEDIA_REVIEW_SCHEMA,
        "review_id": "x",
        "material_id": material_id,
        "source_pdf_sha256": source_pdf_sha256,
        "baseline_sha256": baseline_sha256,
        "review_status": "closed",
        "media": [
            {
                "media_index": media_index,
                "baseline_disposition": "accepted",
            }
            for media_index in range(1, media_count + 1)
        ],
        "media_overrides": [],
        "open_reviews": [],
    }
    return len(_canonical_bytes(minimal))


def _bbox_overlap_metrics(
    left: object,
    right: object,
    *,
    label: str,
) -> tuple[float, float]:
    left_box = _normalized_bbox(left, scale=1.0, label=f"{label}.left")
    right_box = _normalized_bbox(right, scale=1.0, label=f"{label}.right")
    if left_box is None or right_box is None:
        _fail("media_source_ownership_invalid", f"{label} has no bounding box")
    x0 = max(left_box[0], right_box[0])
    y0 = max(left_box[1], right_box[1])
    x1 = min(left_box[2], right_box[2])
    y1 = min(left_box[3], right_box[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    left_area = max(0.0, left_box[2] - left_box[0]) * max(
        0.0,
        left_box[3] - left_box[1],
    )
    right_area = max(0.0, right_box[2] - right_box[0]) * max(
        0.0,
        right_box[3] - right_box[1],
    )
    if left_area == 0 or right_area == 0:
        _fail("media_source_ownership_invalid", f"{label} has an empty bounding box")
    return (
        intersection / max(left_area, right_area),
        intersection / min(left_area, right_area),
    )


def _deterministic_media_source_ownership(
    source_units: Sequence[Mapping[str, Any]],
    media_atoms: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """Bind each media atom to unique, same-page source evidence.

    The balanced-overlap score avoids assigning a large Popo image to a small
    nested MinerU atom merely because that atom is fully contained.  The
    containment score permits a larger media atom to own several smaller
    fragile source fragments.  Fragile blocks are assigned first; otherwise
    unused media atoms may bind one strongly overlapping non-fragile block.
    """

    source_by_id: dict[str, Mapping[str, Any]] = {}
    source_by_page: dict[int, list[Mapping[str, Any]]] = {}
    for raw in source_units:
        source_id = _require_text(raw.get("source_id"), "media source_id")
        if source_id in source_by_id:
            _fail(
                "media_source_ownership_invalid",
                f"source unit {source_id!r} is duplicated",
            )
        page = _require_positive_int(
            raw.get("physical_page"),
            f"{source_id}.physical_page",
        )
        _bbox_overlap_metrics(
            raw.get("bbox"),
            raw.get("bbox"),
            label=f"source:{source_id}",
        )
        source_by_id[source_id] = raw
        source_by_page.setdefault(page, []).append(raw)

    media_by_id: dict[str, Mapping[str, Any]] = {}
    media_by_page: dict[int, list[Mapping[str, Any]]] = {}
    for raw in media_atoms:
        media_id = _require_text(raw.get("media_id"), "media_id")
        if media_id in media_by_id:
            _fail(
                "media_source_ownership_invalid",
                f"media atom {media_id!r} is duplicated",
            )
        page = _require_positive_int(
            raw.get("physical_page"),
            f"{media_id}.physical_page",
        )
        _bbox_overlap_metrics(
            raw.get("bbox"),
            raw.get("bbox"),
            label=f"media:{media_id}",
        )
        media_by_id[media_id] = raw
        media_by_page.setdefault(page, []).append(raw)

    ownership: dict[str, list[str]] = {}
    assigned_sources: set[str] = set()

    def ranked_matches(
        source: Mapping[str, Any],
        candidates: Sequence[Mapping[str, Any]],
        *,
        minimum_containment: float,
    ) -> list[tuple[float, float, str]]:
        source_id = str(source["source_id"])
        matches: list[tuple[float, float, str]] = []
        for media in candidates:
            media_id = str(media["media_id"])
            balanced, containment = _bbox_overlap_metrics(
                source.get("bbox"),
                media.get("bbox"),
                label=f"{source_id}:{media_id}",
            )
            if containment >= minimum_containment:
                matches.append((balanced, containment, media_id))
        matches.sort(key=lambda row: (-row[0], -row[1], row[2]))
        if (
            len(matches) > 1
            and round(matches[0][0], 12) == round(matches[1][0], 12)
            and round(matches[0][1], 12) == round(matches[1][1], 12)
        ):
            _fail(
                "media_source_ownership_ambiguous",
                f"source unit {source_id!r} has indistinguishable media matches",
            )
        return matches

    included = [
        row
        for row in source_units
        if row.get("scope_status") == "included"
    ]
    fragile = sorted(
        (
            row
            for row in included
            if str(row.get("source_type") or "").strip().lower()
            in FRAGILE_SOURCE_TYPES
        ),
        key=lambda row: (
            row.get("candidate_final_order") is None,
            row.get("candidate_final_order") or 0,
            str(row["source_id"]),
        ),
    )
    for source in fragile:
        source_id = str(source["source_id"])
        page = int(source["physical_page"])
        matches = ranked_matches(
            source,
            media_by_page.get(page, ()),
            minimum_containment=FRAGILE_MEDIA_CONTAINMENT_MIN,
        )
        if not matches:
            _fail(
                "media_source_ownership_unresolved",
                f"fragile source unit {source_id!r} has no strong same-page media match",
            )
        media_id = matches[0][2]
        ownership.setdefault(media_id, []).append(source_id)
        assigned_sources.add(source_id)

    for media_id in sorted(set(media_by_id) - set(ownership)):
        media = media_by_id[media_id]
        page = int(media["physical_page"])
        matches: list[tuple[float, float, str]] = []
        for source in source_by_page.get(page, ()):
            source_id = str(source["source_id"])
            if (
                source.get("scope_status") != "included"
                or source_id in assigned_sources
                or str(source.get("source_type") or "").strip().lower()
                in FRAGILE_SOURCE_TYPES
            ):
                continue
            balanced, containment = _bbox_overlap_metrics(
                source.get("bbox"),
                media.get("bbox"),
                label=f"{source_id}:{media_id}",
            )
            if containment >= FALLBACK_MEDIA_CONTAINMENT_MIN:
                matches.append((balanced, containment, source_id))
        matches.sort(key=lambda row: (-row[0], -row[1], row[2]))
        if (
            len(matches) > 1
            and round(matches[0][0], 12) == round(matches[1][0], 12)
            and round(matches[0][1], 12) == round(matches[1][1], 12)
        ):
            _fail(
                "media_source_ownership_ambiguous",
                f"media atom {media_id!r} has indistinguishable source matches",
            )
        if not matches:
            _fail(
                "media_source_ownership_unresolved",
                f"media atom {media_id!r} has no unique strong source owner",
            )
        source_id = matches[0][2]
        ownership[media_id] = [source_id]
        assigned_sources.add(source_id)

    if set(ownership) != set(media_by_id):
        _fail(
            "media_source_ownership_unresolved",
            "not every media atom has source ownership",
        )
    flattened = [
        source_id
        for media_id in sorted(ownership)
        for source_id in ownership[media_id]
    ]
    if len(flattened) != len(set(flattened)):
        _fail(
            "media_source_ownership_duplicate",
            "one source unit is owned by multiple media atoms",
        )
    fragile_ids = {str(row["source_id"]) for row in fragile}
    if not fragile_ids.issubset(flattened):
        _fail(
            "media_source_ownership_unresolved",
            "not every included fragile source unit has media ownership",
        )
    source_order = {
        str(row["source_id"]): (
            row.get("candidate_final_order") is None,
            row.get("candidate_final_order") or 0,
            str(row["source_id"]),
        )
        for row in source_units
    }
    return {
        media_id: sorted(source_ids, key=source_order.__getitem__)
        for media_id, source_ids in sorted(ownership.items())
    }


def _media_review_task(parent: Path) -> dict[str, Any]:
    contract = _verify_materialized_parent(parent)
    identity = contract["material_identity"]
    media_atoms = _read_jsonl(parent / "source/mineru_media_atoms.jsonl", "MinerU media atoms")
    source_evidence = _read_jsonl(
        parent / "source/popo_source_units.jsonl",
        "Popo source units",
    )
    scope = _read_json(parent / "ledgers/source_scope_ledger.json", "source scope ledger")
    if scope.get("spec_status") != "passed":
        _fail("review_task_scope_invalid", "Spec 02 scope ledger is not closed")
    source_units = scope.get("source_units")
    if not isinstance(source_units, list):
        _fail("review_task_scope_invalid", "Spec 02 scope ledger has no source units")
    source_evidence_by_id = {
        str(unit["source_id"]): unit
        for unit in source_evidence
        if isinstance(unit, Mapping) and unit.get("source_id")
    }
    if (
        len(source_evidence_by_id) != len(source_evidence)
        or {
            str(unit.get("source_id"))
            for unit in source_units
            if isinstance(unit, Mapping)
        }
        != set(source_evidence_by_id)
    ):
        _fail(
            "review_task_scope_invalid",
            "Spec 02 scope and Popo source evidence partitions differ",
        )
    source_by_page: dict[int, list[dict[str, Any]]] = {}
    enriched_source_units: list[dict[str, Any]] = []
    for unit in source_units:
        if not isinstance(unit, Mapping):
            _fail("review_task_scope_invalid", "scope source unit must be an object")
        evidence = source_evidence_by_id[str(unit["source_id"])]
        enriched = {
            "source_id": unit["source_id"],
            "scope_status": unit["scope_status"],
            "candidate_final_order": unit["candidate_final_order"],
            "physical_page": unit["physical_page"],
            "bbox": evidence["bbox"],
            "bbox_basis": evidence["bbox_basis"],
            "source_type": evidence["source_type"],
            "source_label": evidence["source_label"],
            "raw_content_sha256": evidence["raw_content_sha256"],
            "content_excerpt": _scope_content_excerpt(evidence.get("raw_content")),
        }
        enriched_source_units.append(enriched)
        source_by_page.setdefault(int(unit["physical_page"]), []).append(enriched)
    ownership = _deterministic_media_source_ownership(
        enriched_source_units,
        media_atoms,
    )
    media_pages = {int(atom["physical_page"]) for atom in media_atoms}
    page_source_units: list[dict[str, Any]] = []
    indexed_source_by_page: dict[int, list[dict[str, Any]]] = {}
    for page in sorted(media_pages):
        rows = sorted(
            source_by_page.get(page, []),
            key=lambda item: (
                item["candidate_final_order"] is None,
                item["candidate_final_order"] or 0,
                item["source_id"],
            ),
        )
        indexed = [
            {"source_unit_index": index, **row}
            for index, row in enumerate(rows, 1)
        ]
        indexed_source_by_page[page] = indexed
        page_source_units.append(
            {
                "physical_page": page,
                "source_units": indexed,
            }
        )

    task_atoms: list[dict[str, Any]] = []
    baseline_rows: list[dict[str, Any]] = []
    for media_index, atom in enumerate(
        sorted(media_atoms, key=lambda item: item["media_id"]),
        1,
    ):
        candidates = sorted(
            (
                item
                for item in atom.get("candidates") or []
                if isinstance(item, Mapping) and item.get("candidate_id")
            ),
            key=lambda item: (
                {
                    "source_asset_image": 0,
                    "source_region_image": 1,
                    "structured_table": 2,
                    "structured_chart": 2,
                    "structured_formula": 2,
                }.get(str(item.get("representation_type")), 9),
                str(item["candidate_id"]),
            ),
        )
        if not candidates:
            _fail(
                "review_task_media_invalid",
                f"media atom {atom['media_id']!r} has no representation candidate",
            )
        compact_candidates: list[dict[str, Any]] = []
        for candidate_index, candidate in enumerate(candidates, 1):
            compact: dict[str, Any] = {
                "candidate_index": candidate_index,
                "candidate_id": candidate["candidate_id"],
                "representation_type": candidate["representation_type"],
            }
            for key in (
                "sha256",
                "size_bytes",
                "source_page",
                "bbox",
                "bbox_coordinate_space",
                "payload_sha256",
                "payload",
            ):
                if key in candidate:
                    compact[key] = candidate[key]
            compact_candidates.append(compact)
        baseline_candidate = compact_candidates[0]
        baseline_disposition = {
            "source_region_image": "source_region",
            "source_asset_image": "source_asset",
            "structured_table": "structured_transcription",
            "structured_chart": "structured_transcription",
            "structured_formula": "structured_transcription",
        }.get(str(baseline_candidate["representation_type"]))
        if baseline_disposition is None:
            _fail(
                "review_task_media_invalid",
                f"media atom {atom['media_id']!r} candidate type is unsupported",
            )
        baseline_row = {
            "media_index": media_index,
            "media_id": atom["media_id"],
            "disposition": baseline_disposition,
            "selected_candidate_index": baseline_candidate["candidate_index"],
            "source_unit_indexes": [
                next(
                    row["source_unit_index"]
                    for row in indexed_source_by_page[int(atom["physical_page"])]
                    if row["source_id"] == source_id
                )
                for source_id in ownership[str(atom["media_id"])]
            ],
        }
        baseline_rows.append(baseline_row)
        task_atoms.append(
            {
                "media_index": media_index,
                "media_id": atom["media_id"],
                "physical_page": atom["physical_page"],
                "bbox": atom["bbox"],
                "bbox_basis": atom["bbox_basis"],
                "media_kind": atom["media_kind"],
                "raw_content_sha256": atom["raw_content_sha256"],
                "baseline_disposition": baseline_disposition,
                "baseline_candidate_index": baseline_candidate[
                    "candidate_index"
                ],
                "baseline_source_unit_indexes": baseline_row[
                    "source_unit_indexes"
                ],
                "candidates": compact_candidates,
            }
        )
    baseline = {
        "schema_version": "luceon.worker-v3-spec03-deterministic-baseline/v1",
        "algorithm": MEDIA_BASELINE_ALGORITHM,
        "media": baseline_rows,
    }
    baseline["sha256"] = _canonical_hash(baseline)
    task = {
        "schema_version": MEDIA_REVIEW_TASK_SCHEMA,
        "stage_key": "canonical_block_ledger",
        "material_id": identity["material_id"],
        "source_pdf_sha256": identity["source_pdf_sha256"],
        "media_atom_count": len(task_atoms),
        "baseline_algorithm": MEDIA_BASELINE_ALGORITHM,
        "baseline_sha256": baseline["sha256"],
        "required_output_schema": MEDIA_REVIEW_SCHEMA,
        "allowed_choices": {
            "disposition": [
                "source_asset",
                "source_region",
                "structured_transcription",
                "excluded_noninstructional",
            ]
        },
        "constraints": [
            "Classify every media_index exactly once.",
            "Accept the deterministic source-region baseline compactly and return full fields only for overrides.",
            "Select only one candidate_index enumerated for that media atom.",
            "Preserve at least one exact same-page source_unit_index for every nonexcluded media atom.",
            "Every included image, table, chart, or equation source unit must be owned exactly once.",
            "An exclusion requires source evidence and cannot discard instructional content.",
            "Do not invent replacement teaching content or an unenumerated asset.",
        ],
        "page_source_units": page_source_units,
        "media_atoms": task_atoms,
    }
    task["capacity"] = {
        "minimum_response_bytes": _minimum_media_review_bytes(
            material_id=identity["material_id"],
            source_pdf_sha256=identity["source_pdf_sha256"],
            baseline_sha256=baseline["sha256"],
            media_count=len(baseline_rows),
        ),
    }
    task["task_id"] = "media-review-" + _canonical_hash(task)[:24]
    return task


def media_model_evidence(task: Mapping[str, Any]) -> dict[str, Any]:
    """Project the frozen Spec 03 task into its bounded model view.

    The full task remains the audit and index-expansion authority.  Long source
    and candidate identities are deliberately excluded from the model request:
    the compact response can select only the stable indexes retained here, and
    the deterministic kernel expands those indexes against the frozen task.
    """

    if task.get("schema_version") != MEDIA_REVIEW_TASK_SCHEMA:
        _fail(
            "media_review_task_invalid",
            "Spec 03 media review task is unsupported",
        )
    normalized = json.loads(_canonical_bytes(task))
    page_groups = normalized.get("page_source_units")
    media_atoms = normalized.get("media_atoms")
    if not isinstance(page_groups, list) or not isinstance(media_atoms, list):
        _fail(
            "media_review_task_invalid",
            "Spec 03 media review task has no complete evidence inventories",
        )
    source_unit_fields = (
        "source_unit_index",
        "scope_status",
        "bbox",
        "source_type",
        "source_label",
        "content_excerpt",
    )
    candidate_fields = (
        "candidate_index",
        "representation_type",
        "source_page",
        "bbox",
        "bbox_coordinate_space",
        "payload",
    )
    media_atom_fields = (
        "media_index",
        "physical_page",
        "bbox",
        "bbox_basis",
        "media_kind",
        "baseline_disposition",
        "baseline_candidate_index",
        "baseline_source_unit_indexes",
        "candidates",
    )
    projected = {
        key: normalized[key]
        for key in (
            "stage_key",
            "material_id",
            "source_pdf_sha256",
            "media_atom_count",
            "baseline_algorithm",
            "baseline_sha256",
            "required_output_schema",
            "allowed_choices",
            "constraints",
            "capacity",
        )
    }
    projected.update(
        {
            "schema_version": "luceon.worker-v3-spec03-model-evidence/v2",
            "encoding": {
                "page_group_fields": ["physical_page", "source_units"],
                "source_unit_fields": list(source_unit_fields),
                "media_atom_fields": list(media_atom_fields),
                "candidate_fields": list(candidate_fields),
                "positional_rule": (
                    "Read every array position against its declared field table. "
                    "A null candidate value means that optional field is absent."
                ),
            },
            "page_source_units": [
                [
                    group["physical_page"],
                    [
                        [unit.get(key) for key in source_unit_fields]
                        for unit in group["source_units"]
                    ],
                ]
                for group in page_groups
            ],
            "media_atoms": [
                [
                    *[
                        atom.get(key)
                        for key in media_atom_fields
                        if key != "candidates"
                    ],
                    [
                        [candidate.get(key) for key in candidate_fields]
                        for candidate in atom["candidates"]
                    ],
                ]
                for atom in media_atoms
            ],
        }
    )
    projected["task_id"] = normalized["task_id"]
    return projected


def prepare_media_review_task(args: argparse.Namespace) -> dict[str, Any]:
    task = _media_review_task(args.parent.resolve())
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        _fail("review_task_output_exists", "media review task output already exists")
    task_hash = _write_json(output, task)
    return {
        "status": "prepared",
        "task_sha256": task_hash,
        "task_canonical_sha256": _canonical_hash(task),
        "media_atoms": len(task["media_atoms"]),
        "baseline_sha256": task["baseline_sha256"],
        "minimum_response_bytes": task["capacity"]["minimum_response_bytes"],
    }


def _outline_ledger(parent: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ledger_path = _contained_file(
        parent,
        "ledgers/canonical_block_ledger.jsonl",
        "Spec 04-A parent canonical ledger",
    )
    rows = _read_jsonl(ledger_path, "Spec 04-A parent canonical ledger")
    if not rows or rows[0].get("record_type") != "ledger_header":
        _fail(
            "outline_review_parent_invalid",
            "Spec 04-A parent canonical ledger has no ledger header",
        )
    header = rows[0]
    records = rows[1:]
    if any(row.get("record_type") != "source_block" for row in records):
        _fail(
            "outline_review_parent_invalid",
            "Spec 04-A parent ledger is not source-block-only",
        )
    if header.get("ledger_checkpoint") != "source_reconciled":
        _fail(
            "outline_review_parent_invalid",
            "Spec 04-A requires a source_reconciled parent ledger",
        )
    if header.get("spec_status") != "passed":
        _fail(
            "outline_review_parent_invalid",
            "Spec 04-A parent ledger is not passed",
        )
    if header.get("current_ledger_hash") != _canonical_hash(records):
        _fail(
            "outline_review_parent_invalid",
            "Spec 04-A parent ledger payload hash is invalid",
        )
    return header, records


def _outline_title_inventory(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    candidates = [
        {
            "block_id": record["block_id"],
            "pdf_physical_page": record.get("pdf_physical_page"),
            "candidate_final_order": record.get("candidate_final_order"),
            "raw_content_sha256": (
                record.get("raw_content_sha256")
                or _canonical_hash(record.get("raw_content"))
            ),
        }
        for record in records
        if record.get("scope_status") == "included"
        and (
            record.get("source_type") == "title"
            or record.get("source_label") == "title"
        )
    ]
    candidates.sort(
        key=lambda item: (
            item.get("pdf_physical_page") or 0,
            item.get("candidate_final_order") or 0,
            item["block_id"],
        )
    )
    return {
        "schema_version": "structure-title-candidate-inventory/1.0",
        "selection_rule": "all included source blocks labelled or typed as title",
        "candidates": candidates,
        "candidate_count": len(candidates),
        "payload_hash": _canonical_hash(candidates),
    }


def _outline_content(value: Any, *, excerpt: bool) -> Any:
    if not excerpt or not isinstance(value, str):
        return value
    normalized = " ".join(value.split())
    if len(normalized) <= OUTLINE_CONTEXT_EXCERPT_CHARS:
        return normalized
    return normalized[: OUTLINE_CONTEXT_EXCERPT_CHARS - 1] + "…"


def _compact_outline_review(
    task_id: str,
    candidate_indexes: Sequence[int],
) -> dict[str, Any]:
    return {
        "schema_version": OUTLINE_COMPACT_REVIEW_SCHEMA,
        "task_id": task_id,
        "review_status": "closed",
        "selected_nodes": [
            {
                "candidate_index": candidate_index,
                "level": 0,
                "include_in_toc": candidate_index == candidate_indexes[0],
            }
            for candidate_index in candidate_indexes
        ],
        "open_reviews": [],
    }


def _outline_compact_response_capacity(
    task_id: str,
    candidate_count: int,
) -> dict[str, int]:
    indexes = list(range(candidate_count))
    return {
        "minimum_response_bytes": len(
            _canonical_bytes(_compact_outline_review(task_id, indexes[:1]))
        ),
        "maximum_response_bytes": len(
            _canonical_bytes(_compact_outline_review(task_id, indexes))
        ),
        "maximum_structural_nodes": candidate_count,
    }


def outline_model_evidence(task: Mapping[str, Any]) -> dict[str, Any]:
    """Project a stable LLM view while retaining the full audit binding on disk."""

    if task.get("schema_version") != OUTLINE_REVIEW_TASK_SCHEMA:
        _fail(
            "outline_review_task_invalid",
            "Spec 04-A compact review task is unsupported",
        )
    normalized = json.loads(_canonical_bytes(task))
    if not isinstance(normalized.get("parent_binding"), dict):
        _fail(
            "outline_review_task_invalid",
            "Spec 04-A compact review task has no parent binding",
        )
    normalized.pop("parent_binding")
    normalized.pop("allowed_source_outline_evidence", None)
    return normalized


def _outline_review_task(
    parent: Path,
    *,
    source_pdf: Path,
    source_pdf_ref: str,
    parent_promotion: Path,
) -> dict[str, Any]:
    header, records = _outline_ledger(parent)
    identity = header.get("material_identity")
    if not isinstance(identity, Mapping):
        _fail(
            "outline_review_parent_invalid",
            "Spec 04-A parent ledger has no material identity",
        )
    source_pdf = source_pdf.resolve()
    if not source_pdf.is_file() or source_pdf.is_symlink():
        _fail("source_pdf_missing", "Spec 04-A source PDF is unavailable")
    source_pdf_sha256 = _sha256(source_pdf)
    if source_pdf_sha256 != identity.get("source_pdf_sha256"):
        _fail(
            "source_pdf_identity_mismatch",
            "Spec 04-A source PDF differs from the parent ledger",
        )
    source_pdf_ref = _safe_relative(
        source_pdf_ref,
        "Spec 04-A source PDF reference",
    )
    page_count = _require_positive_int(
        identity.get("page_count"),
        "material_identity.page_count",
    )
    promotion = _read_json(parent_promotion.resolve(), "parent promotion manifest")
    if promotion.get("disposition") != "promoted":
        _fail(
            "outline_review_promotion_invalid",
            "Spec 04-A parent promotion is not promoted",
        )
    promotion_id = _require_text(
        promotion.get("promotion_id"),
        "parent promotion id",
    )
    promotion_sha256 = _sha256(parent_promotion.resolve())
    parent_binding = {
        "ledger_snapshot_id": _require_text(
            header.get("ledger_snapshot_id"),
            "parent ledger snapshot id",
        ),
        "ledger_payload_hash": _require_sha(
            header.get("current_ledger_hash"),
            "parent ledger payload hash",
        ),
        "source_pdf_sha256": source_pdf_sha256,
        "promotion_id": promotion_id,
        "promotion_manifest_sha256": promotion_sha256,
    }
    inventory = _outline_title_inventory(records)
    if not inventory["candidates"]:
        _fail(
            "outline_review_candidates_empty",
            "Spec 04-A parent ledger has no included title candidates",
        )

    included_by_page: dict[int, list[Mapping[str, Any]]] = {}
    for record in records:
        if record.get("scope_status") != "included":
            continue
        page = record.get("pdf_physical_page")
        order = record.get("candidate_final_order")
        if (
            not isinstance(page, int)
            or isinstance(page, bool)
            or page < 1
            or page > page_count
            or not isinstance(order, int)
            or isinstance(order, bool)
        ):
            _fail(
                "outline_review_parent_invalid",
                f"included source block has invalid page/order: {record.get('block_id')}",
            )
        included_by_page.setdefault(page, []).append(record)
    for page_records in included_by_page.values():
        page_records.sort(
            key=lambda record: (
                int(record["candidate_final_order"]),
                str(record["block_id"]),
            )
        )

    title_ids = {
        str(item["block_id"])
        for item in inventory["candidates"]
    }
    context_ids: set[str] = set()
    for page_records in included_by_page.values():
        title_positions = [
            index
            for index, record in enumerate(page_records)
            if str(record["block_id"]) in title_ids
        ]
        for index in title_positions:
            start = max(0, index - OUTLINE_CONTEXT_RADIUS)
            end = min(len(page_records), index + OUTLINE_CONTEXT_RADIUS + 1)
            context_ids.update(
                str(record["block_id"])
                for record in page_records[start:end]
            )

    compact_records: list[dict[str, Any]] = []
    title_candidates: list[dict[str, Any]] = []
    next_candidate_index = 0
    for page in sorted(included_by_page):
        for record in included_by_page[page]:
            block_id = str(record["block_id"])
            if block_id not in context_ids:
                continue
            is_title = block_id in title_ids
            compact = {
                "block_id": block_id,
                "pdf_physical_page": page,
                "candidate_final_order": record["candidate_final_order"],
                "source_type": record.get("source_type"),
                "source_label": record.get("source_label"),
                "raw_content": _outline_content(
                    record.get("raw_content"),
                    excerpt=not is_title,
                ),
                "raw_content_sha256": (
                    record.get("raw_content_sha256")
                    or _canonical_hash(record.get("raw_content"))
                ),
                "bbox": record.get("bbox"),
                "bbox_basis": record.get("bbox_basis"),
                "tree_context": record.get("tree_context"),
                "title_candidate": is_title,
            }
            compact_records.append(compact)
            if is_title:
                compact["candidate_index"] = next_candidate_index
                next_candidate_index += 1
                title_candidates.append(compact)

    allowed_evidence = [
        {
            "evidence_id": f"source-pdf-page-{page:06d}",
            "kind": "source_pdf_page",
            "pdf_physical_page": page,
            "path": source_pdf_ref,
            "sha256": source_pdf_sha256,
        }
        for page in range(1, page_count + 1)
    ]
    task = {
        "schema_version": OUTLINE_REVIEW_TASK_SCHEMA,
        "stage_key": "outline_reconstruction",
        "material_id": identity.get("material_id"),
        "page_count": page_count,
        "parent_binding": parent_binding,
        "required_output_schema": OUTLINE_COMPACT_REVIEW_SCHEMA,
        "title_candidate_inventory_payload_hash": inventory["payload_hash"],
        "title_candidate_count": inventory["candidate_count"],
        "title_candidates": title_candidates,
        "context_blocks": compact_records,
        "allowed_source_outline_evidence": allowed_evidence,
        "constraints": [
            "Return only candidate_index, hierarchy level, and final-TOC inclusion decisions; deterministic code expands all titles, block IDs, evidence IDs, paths, hashes, and parent IDs.",
            "Use only enumerated candidate_index values in strictly increasing source order.",
            "The first selected node is level 0; later levels may stay equal, decrease, or increase by at most one.",
            "A title label is only a candidate; repetitive local, exercise, difficulty, and running-page labels remain local headings unless explicit source-outline evidence establishes structure.",
            "Every title candidate not selected by a node is disposed mechanically as local_heading.",
            "Do not emit teaching roles, template constructs, render nodes, LaTeX, or invented content.",
        ],
    }
    task["task_id"] = (
        "outline-review-" + _canonical_hash(outline_model_evidence(task))[:24]
    )
    task["capacity"] = _outline_compact_response_capacity(
        task["task_id"],
        len(title_candidates),
    )
    return task


def _project_outline_review(
    task: Mapping[str, Any],
    compact_review: Mapping[str, Any],
) -> dict[str, Any]:
    expected_fields = {
        "schema_version",
        "task_id",
        "review_status",
        "selected_nodes",
        "open_reviews",
    }
    if (
        set(compact_review) != expected_fields
        or compact_review.get("schema_version")
        != OUTLINE_COMPACT_REVIEW_SCHEMA
        or compact_review.get("task_id") != task.get("task_id")
        or compact_review.get("review_status") != "closed"
        or compact_review.get("open_reviews") != []
    ):
        _fail(
            "outline_compact_review_invalid",
            "Spec 04-A compact review is open, drifted, or has unknown fields",
        )
    if (
        task.get("schema_version") != OUTLINE_REVIEW_TASK_SCHEMA
        or task.get("required_output_schema")
        != OUTLINE_COMPACT_REVIEW_SCHEMA
    ):
        _fail(
            "outline_review_task_invalid",
            "Spec 04-A compact review task is unsupported",
        )
    candidates = task.get("title_candidates")
    if not isinstance(candidates, list) or not candidates:
        _fail(
            "outline_review_task_invalid",
            "Spec 04-A compact review task has no title candidates",
        )
    candidates_by_index: dict[int, Mapping[str, Any]] = {}
    for expected_index, candidate in enumerate(candidates):
        if (
            not isinstance(candidate, Mapping)
            or candidate.get("candidate_index") != expected_index
            or not isinstance(candidate.get("block_id"), str)
            or not candidate["block_id"]
            or not isinstance(candidate.get("raw_content"), str)
            or not candidate["raw_content"].strip()
            or not isinstance(candidate.get("pdf_physical_page"), int)
            or isinstance(candidate.get("pdf_physical_page"), bool)
            or int(candidate["pdf_physical_page"]) < 1
        ):
            _fail(
                "outline_review_task_invalid",
                f"Spec 04-A candidate index {expected_index} is invalid",
            )
        candidates_by_index[expected_index] = candidate

    allowed_evidence = task.get("allowed_source_outline_evidence")
    if not isinstance(allowed_evidence, list) or not allowed_evidence:
        _fail(
            "outline_review_task_invalid",
            "Spec 04-A task has no allowed source evidence",
        )
    evidence_by_page: dict[int, Mapping[str, Any]] = {}
    for evidence in allowed_evidence:
        if not isinstance(evidence, Mapping):
            _fail(
                "outline_review_task_invalid",
                "Spec 04-A allowed source evidence is invalid",
            )
        page = evidence.get("pdf_physical_page")
        expected_id = (
            f"source-pdf-page-{page:06d}"
            if isinstance(page, int) and not isinstance(page, bool)
            else ""
        )
        if (
            not expected_id
            or page in evidence_by_page
            or evidence.get("evidence_id") != expected_id
            or evidence.get("kind") != "source_pdf_page"
            or not isinstance(evidence.get("path"), str)
            or not evidence["path"]
            or not isinstance(evidence.get("sha256"), str)
            or len(evidence["sha256"]) != 64
        ):
            _fail(
                "outline_review_task_invalid",
                "Spec 04-A allowed source evidence is drifted or duplicated",
            )
        evidence_by_page[int(page)] = evidence

    selected = compact_review.get("selected_nodes")
    if not isinstance(selected, list) or not selected:
        _fail(
            "outline_compact_review_invalid",
            "Spec 04-A compact review must select at least one structure node",
        )
    if len(selected) > len(candidates_by_index):
        _fail(
            "outline_compact_review_invalid",
            "Spec 04-A compact review selects too many structure nodes",
        )
    previous_index = -1
    stack: list[str] = []
    nodes: list[dict[str, Any]] = []
    selected_pages: set[int] = set()
    toc_included = 0
    for ordinal, raw_node in enumerate(selected, start=1):
        if not isinstance(raw_node, Mapping) or set(raw_node) != {
            "candidate_index",
            "level",
            "include_in_toc",
        }:
            _fail(
                "outline_compact_review_invalid",
                "Spec 04-A compact node has missing or unknown fields",
            )
        candidate_index = raw_node.get("candidate_index")
        level = raw_node.get("level")
        include_in_toc = raw_node.get("include_in_toc")
        if (
            not isinstance(candidate_index, int)
            or isinstance(candidate_index, bool)
            or candidate_index <= previous_index
            or candidate_index not in candidates_by_index
        ):
            _fail(
                "outline_compact_review_invalid",
                "Spec 04-A candidate indexes must be unique and strictly increasing",
            )
        if (
            not isinstance(level, int)
            or isinstance(level, bool)
            or level < 0
            or level > 8
            or (ordinal == 1 and level != 0)
            or level > len(stack)
        ):
            _fail(
                "outline_compact_review_invalid",
                "Spec 04-A hierarchy has an invalid level or a level jump",
            )
        if not isinstance(include_in_toc, bool):
            _fail(
                "outline_compact_review_invalid",
                "Spec 04-A final-TOC disposition must be boolean",
            )
        while len(stack) > level:
            stack.pop()
        parent_node_id = stack[-1] if stack else None
        node_id = f"structure-node-{ordinal:06d}"
        stack.append(node_id)
        candidate = candidates_by_index[candidate_index]
        page = int(candidate["pdf_physical_page"])
        evidence = evidence_by_page.get(page)
        if evidence is None:
            _fail(
                "outline_review_task_invalid",
                f"Spec 04-A candidate page {page} has no allowed source evidence",
            )
        block_id = str(candidate["block_id"])
        title = str(candidate["raw_content"])
        evidence_id = str(evidence["evidence_id"])
        nodes.append(
            {
                "node_id": node_id,
                "title": title,
                "role": "source_structure",
                "parent_node_id": parent_node_id,
                "level": level,
                "anchor_block_id": block_id,
                "heading_evidence_block_ids": [block_id],
                "source_outline_evidence_ids": [evidence_id],
                "source_toc_entry_ids": [],
                "final_toc": {
                    "include": include_in_toc,
                    "level": level,
                    "title": title,
                },
                "review_status": "closed",
            }
        )
        selected_pages.add(page)
        toc_included += int(include_in_toc)
        previous_index = candidate_index
    if toc_included < 1:
        _fail(
            "outline_compact_review_invalid",
            "Spec 04-A compact review must retain at least one final TOC node",
        )

    parent_binding = task.get("parent_binding")
    inventory_hash = task.get("title_candidate_inventory_payload_hash")
    if not isinstance(parent_binding, Mapping) or not isinstance(
        inventory_hash,
        str,
    ):
        _fail(
            "outline_review_task_invalid",
            "Spec 04-A task lacks its immutable parent binding",
        )
    return {
        "schema_version": "spec04a-outline-review-bundle/1.0",
        "review_id": str(task["task_id"]),
        "parent_binding": dict(parent_binding),
        "source_outline_evidence": [
            dict(evidence_by_page[page])
            for page in sorted(selected_pages)
        ],
        "source_toc_entries": [],
        "nodes": nodes,
        "title_candidate_disposition": {
            "candidate_inventory_payload_hash": inventory_hash,
            "all_unassigned": "local_heading",
            "review_status": "closed",
        },
        "review": {
            "status": "closed",
            "open_items": 0,
            "decision_refs": [
                "compact-review::" + _canonical_hash(compact_review)
            ],
        },
    }


def project_outline_review(args: argparse.Namespace) -> dict[str, Any]:
    task = _read_json(args.task.resolve(), "Spec 04-A compact review task")
    compact_review = _read_json(
        args.compact_review.resolve(),
        "Spec 04-A compact review result",
    )
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        _fail(
            "review_projection_output_exists",
            "Spec 04-A projected review output already exists",
        )
    bundle = _project_outline_review(task, compact_review)
    output_hash = _write_json(output, bundle)
    return {
        "status": "projected",
        "output_sha256": output_hash,
        "output_canonical_sha256": _canonical_hash(bundle),
        "selected_nodes": len(bundle["nodes"]),
        "source_evidence_pages": len(bundle["source_outline_evidence"]),
    }


def prepare_outline_review_task(args: argparse.Namespace) -> dict[str, Any]:
    task = _outline_review_task(
        args.parent.resolve(),
        source_pdf=args.source_pdf.resolve(),
        source_pdf_ref=args.source_pdf_ref,
        parent_promotion=args.parent_promotion.resolve(),
    )
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        _fail("review_task_output_exists", "outline review task output already exists")
    task_hash = _write_json(output, task)
    return {
        "status": "prepared",
        "task_sha256": task_hash,
        "task_canonical_sha256": _canonical_hash(task),
        "title_candidates": task["title_candidate_count"],
        "context_blocks": len(task["context_blocks"]),
        "pages": task["page_count"],
        "minimum_response_bytes": task["capacity"]["minimum_response_bytes"],
    }


def semantic_model_evidence(task: Mapping[str, Any]) -> dict[str, Any]:
    """Project the stable Spec 04-B model view from the full audit task."""

    if task.get("schema_version") != SEMANTIC_REVIEW_TASK_SCHEMA:
        _fail(
            "semantic_review_task_invalid",
            "Spec 04-B compact review task is unsupported",
        )
    normalized = json.loads(_canonical_bytes(task))
    if not isinstance(normalized.get("parent_binding"), dict):
        _fail(
            "semantic_review_task_invalid",
            "Spec 04-B compact review task has no parent binding",
        )
    normalized.pop("parent_binding")
    normalized.pop("allowed_source_evidence", None)
    return normalized


def _semantic_content(value: Any) -> str:
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= SEMANTIC_CONTEXT_EXCERPT_CHARS:
        return normalized
    return normalized[: SEMANTIC_CONTEXT_EXCERPT_CHARS - 1] + "…"


def _is_semantic_fragile_or_media(record: Mapping[str, Any]) -> bool:
    """Recognize media authority even when an upstream compatibility type is text."""

    return bool(
        record.get("source_type") in FRAGILE_SOURCE_TYPES
        or record.get("source_label") in SEMANTIC_FRAGILE_LABELS
        or record.get("asset_ref")
        or record.get("media_contracts")
    )


def _semantic_option_protocol() -> dict[str, Any]:
    role_count = len(SEMANTIC_ROLE_CHOICES)
    return {
        "schema_version": SEMANTIC_OPTION_PROTOCOL_SCHEMA,
        "plain_body_index": 0,
        "standalone_label_role_offset": 1,
        "teaching_group_role_offset": 1 + role_count,
        "option_count": 1 + (2 * role_count),
        "unavailable_teaching_resolution": (
            "standalone_label_then_plain_body"
        ),
    }


def _validate_semantic_option_protocol(
    value: Any,
    roles: Sequence[str],
) -> dict[str, Any]:
    expected = _semantic_option_protocol()
    if (
        list(roles) != list(SEMANTIC_ROLE_CHOICES)
        or not isinstance(value, Mapping)
        or dict(value) != expected
    ):
        _fail(
            "semantic_review_task_invalid",
            "Spec 04-B total option-index protocol drifted",
        )
    return expected


def _resolve_semantic_option(
    candidate: Mapping[str, Any],
    option_index: Any,
    roles: Sequence[str],
) -> tuple[str, str, bool]:
    protocol = _validate_semantic_option_protocol(
        _semantic_option_protocol(),
        roles,
    )
    if (
        not isinstance(option_index, int)
        or isinstance(option_index, bool)
        or option_index < 0
        or option_index >= protocol["option_count"]
    ):
        _fail(
            "semantic_compact_review_invalid",
            "Spec 04-B option index is outside the frozen protocol",
        )
    allowed = candidate.get("allowed_dispositions")
    body_options = candidate.get("body_options")
    expected_allowed = (
        ["plain_body", "standalone_label", "teaching_group"]
        if isinstance(body_options, list) and body_options
        else ["plain_body", "standalone_label"]
    )
    if not isinstance(body_options, list) or allowed != expected_allowed:
        _fail(
            "semantic_review_task_invalid",
            "Spec 04-B candidate has invalid local option evidence",
        )
    if option_index == protocol["plain_body_index"]:
        return "plain_body", "plain_body", False
    teaching_offset = protocol["teaching_group_role_offset"]
    if option_index < teaching_offset:
        disposition = "standalone_label"
        role_index = option_index - protocol["standalone_label_role_offset"]
    else:
        disposition = "teaching_group"
        role_index = option_index - teaching_offset
    semantic_role = roles[role_index]
    if disposition in allowed:
        return disposition, semantic_role, False
    if disposition == "teaching_group" and "standalone_label" in allowed:
        return "standalone_label", semantic_role, True
    return "plain_body", "plain_body", True


def _semantic_tree_path(record: Mapping[str, Any]) -> tuple[int, ...]:
    tree = record.get("tree_context")
    raw_path = tree.get("node_path") if isinstance(tree, Mapping) else []
    if not isinstance(raw_path, list) or any(
        not isinstance(item, int) or isinstance(item, bool) or item < 0
        for item in raw_path
    ):
        _fail(
            "semantic_review_parent_invalid",
            f"source block has an invalid tree path: {record.get('block_id')}",
        )
    return tuple(raw_path)


def _semantic_compact_review(
    task_id: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    maximum: bool,
) -> dict[str, Any]:
    protocol = _semantic_option_protocol()
    decisions: list[dict[str, Any]] = []
    for candidate_index, candidate in enumerate(candidates):
        body_options = candidate.get("body_options")
        if not isinstance(body_options, list):
            _fail(
                "semantic_review_task_invalid",
                f"Spec 04-B candidate {candidate_index} has invalid body options",
            )
        decisions.append(
            {
                "candidate_index": candidate_index,
                "option_index": (
                    protocol["option_count"] - 1 if maximum else 0
                ),
            }
        )
    return {
        "schema_version": SEMANTIC_COMPACT_REVIEW_SCHEMA,
        "task_id": task_id,
        "review_status": "closed",
        "decisions": decisions,
        "open_reviews": [],
    }


def _semantic_response_capacity(
    task_id: str,
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    return {
        "minimum_response_bytes": len(
            _canonical_bytes(
                _semantic_compact_review(task_id, candidates, maximum=False)
            )
        ),
        "maximum_response_bytes": len(
            _canonical_bytes(
                _semantic_compact_review(task_id, candidates, maximum=True)
            )
        ),
        "maximum_semantic_decisions": len(candidates),
    }


def _semantic_review_task(
    parent: Path,
    *,
    source_pdf: Path,
    source_pdf_ref: str,
    parent_promotion: Path,
) -> dict[str, Any]:
    header, records = _outline_ledger(parent)
    structure = header.get("spec04a_structure")
    if (
        not isinstance(structure, Mapping)
        or structure.get("status") != "passed"
        or structure.get("full_spec04_status") != "not_evaluated"
        or structure.get("open_reviews") != 0
    ):
        _fail(
            "semantic_review_parent_invalid",
            "Spec 04-B requires a closed Spec 04-A parent ledger",
        )
    identity = header.get("material_identity")
    if not isinstance(identity, Mapping):
        _fail(
            "semantic_review_parent_invalid",
            "Spec 04-B parent ledger has no material identity",
        )
    source_pdf = source_pdf.resolve()
    if not source_pdf.is_file() or source_pdf.is_symlink():
        _fail("source_pdf_missing", "Spec 04-B source PDF is unavailable")
    source_pdf_sha256 = _sha256(source_pdf)
    if source_pdf_sha256 != identity.get("source_pdf_sha256"):
        _fail(
            "source_pdf_identity_mismatch",
            "Spec 04-B source PDF differs from the parent ledger",
        )
    source_pdf_ref = _safe_relative(
        source_pdf_ref,
        "Spec 04-B source PDF reference",
    )
    page_count = _require_positive_int(
        identity.get("page_count"),
        "material_identity.page_count",
    )

    promotion_path = parent_promotion.resolve()
    promotion = _read_json(promotion_path, "Spec 04-A parent promotion manifest")
    if (
        promotion.get("disposition") != "promoted"
        or promotion.get("stage_kind") != "spec04a_structure_contract"
    ):
        _fail(
            "semantic_review_promotion_invalid",
            "Spec 04-B parent promotion is not a promoted Spec 04-A candidate",
        )
    promoted_artifacts = promotion.get("promoted_artifacts")
    if not isinstance(promoted_artifacts, Mapping):
        _fail(
            "semantic_review_promotion_invalid",
            "Spec 04-B parent promotion has no promoted artifacts",
        )
    promoted_files = {
        "ledger_L": "ledgers/canonical_block_ledger.jsonl",
        "source_outline_ledger": "structure/source_outline_ledger.json",
        "final_toc_plan": "structure/final_toc_plan.json",
    }
    promoted_hashes: dict[str, str] = {}
    for role, relative in promoted_files.items():
        artifact = promoted_artifacts.get(role)
        path = _contained_file(parent, relative, f"Spec 04-B parent {role}")
        if (
            not isinstance(artifact, Mapping)
            or _require_sha(
                artifact.get("sha256"),
                f"Spec 04-B parent promotion {role}.sha256",
            )
            != _sha256(path)
        ):
            _fail(
                "semantic_review_promotion_invalid",
                f"Spec 04-B parent promotion does not bind {role}",
            )
        promoted_hashes[role] = _sha256(path)

    included = [
        record
        for record in records
        if record.get("scope_status") == "included"
    ]
    included.sort(
        key=lambda record: (
            _require_nonnegative_int(
                record.get("candidate_final_order"),
                f"{record.get('block_id')}.candidate_final_order",
            ),
            _require_text(record.get("block_id"), "included block id"),
        )
    )
    marker_indexes = {
        index
        for index, record in enumerate(included)
        if not record.get("structure_memberships")
        and not _is_semantic_fragile_or_media(record)
        and record.get("source_type") in SEMANTIC_MARKER_TYPES
        and (
            record.get("heading_disposition") == "local_heading"
            or (
                record.get("source_type") == "title"
                and record.get("heading_disposition") is None
            )
        )
    }
    candidates: list[dict[str, Any]] = []
    for record_index in sorted(marker_indexes):
        marker = included[record_index]
        marker_page = _require_positive_int(
            marker.get("pdf_physical_page"),
            f"{marker.get('block_id')}.pdf_physical_page",
        )
        if marker_page > page_count:
            _fail(
                "semantic_review_parent_invalid",
                f"source block page exceeds the source PDF: {marker.get('block_id')}",
            )
        marker_path = _semantic_tree_path(marker)
        body_options: list[dict[str, Any]] = []
        for body_index in range(record_index + 1, len(included)):
            body = included[body_index]
            if (
                body_index in marker_indexes
                or body.get("structure_memberships")
                or body.get("pdf_physical_page") != marker_page
                or _semantic_tree_path(body) != marker_path
                or body.get("source_type") not in SEMANTIC_BODY_TYPES
                or _is_semantic_fragile_or_media(body)
            ):
                break
            body_options.append(
                {
                    "block_id": body["block_id"],
                    "source_type": body["source_type"],
                    "raw_content": _semantic_content(body.get("raw_content")),
                    "raw_content_sha256": body.get("raw_content_sha256"),
                    "candidate_final_order": body.get("candidate_final_order"),
                    "bbox": body.get("bbox"),
                }
            )
        candidate = {
            "candidate_index": len(candidates),
            "marker": {
                "block_id": marker["block_id"],
                "source_type": marker.get("source_type"),
                "raw_content": _semantic_content(marker.get("raw_content")),
                "raw_content_sha256": marker.get("raw_content_sha256"),
                "pdf_physical_page": marker_page,
                "candidate_final_order": marker.get("candidate_final_order"),
                "bbox": marker.get("bbox"),
                "tree_path": list(marker_path),
            },
            "body_options": body_options,
            "allowed_dispositions": (
                ["plain_body", "standalone_label", "teaching_group"]
                if body_options
                else ["plain_body", "standalone_label"]
            ),
        }
        candidates.append(candidate)

    parent_binding = {
        "ledger_snapshot_id": _require_text(
            header.get("ledger_snapshot_id"),
            "Spec 04-B parent ledger snapshot id",
        ),
        "ledger_payload_hash": _require_sha(
            header.get("current_ledger_hash"),
            "Spec 04-B parent ledger payload hash",
        ),
        "source_pdf_sha256": source_pdf_sha256,
        "promotion_id": _require_text(
            promotion.get("promotion_id"),
            "Spec 04-B parent promotion id",
        ),
        "promotion_manifest_sha256": _sha256(promotion_path),
        "source_outline_ledger_sha256": promoted_hashes[
            "source_outline_ledger"
        ],
        "final_toc_plan_sha256": promoted_hashes["final_toc_plan"],
    }
    allowed_evidence = [
        {
            "evidence_id": f"source-pdf-page-{page:06d}",
            "kind": "source_pdf_page",
            "pdf_physical_page": page,
            "path": source_pdf_ref,
            "sha256": source_pdf_sha256,
        }
        for page in range(1, page_count + 1)
    ]
    task = {
        "schema_version": SEMANTIC_REVIEW_TASK_SCHEMA,
        "stage_key": "semantic_annotation",
        "material_id": identity.get("material_id"),
        "page_count": page_count,
        "parent_binding": parent_binding,
        "required_output_schema": SEMANTIC_COMPACT_REVIEW_SCHEMA,
        "semantic_role_choices": list(SEMANTIC_ROLE_CHOICES),
        "option_protocol": _semantic_option_protocol(),
        "candidates": candidates,
        "allowed_source_evidence": allowed_evidence,
        "constraints": [
            "Return exactly one option_index for every candidate_index in ascending order; deterministic code disposes every non-candidate source atom.",
            "option_index 0 is the conservative plain-body default; all other indices are decoded only by the frozen option_protocol and semantic_role_choices.",
            "A teaching-group preference for a candidate without body_options deterministically totalizes to the same-role standalone label, then plain body if unavailable.",
            "teaching_group deterministically consumes the complete enumerated body_options array; the model does not choose or count source atoms.",
            "No source text, block identity, order, formula, media, or hash may be changed.",
            "Do not choose template constructs, render policy, LaTeX, boxes, or promotion outcomes.",
        ],
    }
    task["task_id"] = (
        "semantic-review-" + _canonical_hash(semantic_model_evidence(task))[:24]
    )
    task["capacity"] = _semantic_response_capacity(
        task["task_id"],
        candidates,
    )
    return task


def _project_semantic_review(
    task: Mapping[str, Any],
    compact_review: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        set(compact_review)
        != {
            "schema_version",
            "task_id",
            "review_status",
            "decisions",
            "open_reviews",
        }
        or compact_review.get("schema_version")
        != SEMANTIC_COMPACT_REVIEW_SCHEMA
        or compact_review.get("task_id") != task.get("task_id")
        or compact_review.get("review_status") != "closed"
        or compact_review.get("open_reviews") != []
    ):
        _fail(
            "semantic_compact_review_invalid",
            "Spec 04-B compact review is open, drifted, or has unknown fields",
        )
    if (
        task.get("schema_version") != SEMANTIC_REVIEW_TASK_SCHEMA
        or task.get("required_output_schema")
        != SEMANTIC_COMPACT_REVIEW_SCHEMA
    ):
        _fail(
            "semantic_review_task_invalid",
            "Spec 04-B compact review task is unsupported",
        )
    candidates = task.get("candidates")
    decisions = compact_review.get("decisions")
    if (
        not isinstance(candidates, list)
        or not isinstance(decisions, list)
        or len(decisions) != len(candidates)
    ):
        _fail(
            "semantic_compact_review_invalid",
            "Spec 04-B compact review must dispose every candidate exactly once",
        )
    allowed_roles = set(task.get("semantic_role_choices") or [])
    if allowed_roles != set(SEMANTIC_ROLE_CHOICES):
        _fail(
            "semantic_review_task_invalid",
            "Spec 04-B semantic role choices drifted",
        )
    option_protocol = _validate_semantic_option_protocol(
        task.get("option_protocol"),
        list(task.get("semantic_role_choices") or []),
    )
    parent_binding = task.get("parent_binding")
    if not isinstance(parent_binding, Mapping):
        _fail(
            "semantic_review_task_invalid",
            "Spec 04-B task lacks its immutable parent binding",
        )
    source_pdf_sha256 = _require_sha(
        parent_binding.get("source_pdf_sha256"),
        "Spec 04-B task source PDF SHA-256",
    )
    evidence = task.get("allowed_source_evidence")
    if not isinstance(evidence, list) or not evidence:
        _fail(
            "semantic_review_task_invalid",
            "Spec 04-B task has no allowed source evidence",
        )
    evidence_by_page: dict[int, Mapping[str, Any]] = {}
    for row in evidence:
        if not isinstance(row, Mapping):
            _fail(
                "semantic_review_task_invalid",
                "Spec 04-B source evidence is invalid",
            )
        page = row.get("pdf_physical_page")
        expected_id = (
            f"source-pdf-page-{page:06d}"
            if isinstance(page, int) and not isinstance(page, bool)
            else ""
        )
        if (
            not expected_id
            or page in evidence_by_page
            or row.get("evidence_id") != expected_id
            or row.get("kind") != "source_pdf_page"
            or not isinstance(row.get("path"), str)
            or not row["path"]
            or _require_sha(row.get("sha256"), "Spec 04-B evidence SHA-256")
            != source_pdf_sha256
        ):
            _fail(
                "semantic_review_task_invalid",
                "Spec 04-B source evidence is drifted or duplicated",
            )
        evidence_by_page[int(page)] = row

    teaching_groups: list[dict[str, Any]] = []
    standalone_labels: list[dict[str, Any]] = []
    selected_pages: set[int] = set()
    totalized_decisions = 0
    for candidate_index, (candidate, decision) in enumerate(
        zip(candidates, decisions, strict=True)
    ):
        if (
            not isinstance(candidate, Mapping)
            or candidate.get("candidate_index") != candidate_index
            or not isinstance(decision, Mapping)
            or set(decision)
            != {
                "candidate_index",
                "option_index",
            }
            or decision.get("candidate_index") != candidate_index
        ):
            _fail(
                "semantic_compact_review_invalid",
                f"Spec 04-B candidate decision {candidate_index} is missing, reordered, or has unknown fields",
            )
        marker = candidate.get("marker")
        body_options = candidate.get("body_options")
        allowed_dispositions = candidate.get("allowed_dispositions")
        if (
            not isinstance(marker, Mapping)
            or not isinstance(body_options, list)
            or not isinstance(allowed_dispositions, list)
        ):
            _fail(
                "semantic_review_task_invalid",
                f"Spec 04-B candidate {candidate_index} is invalid",
            )
        disposition, semantic_role, totalized = _resolve_semantic_option(
            candidate,
            decision.get("option_index"),
            list(task["semantic_role_choices"]),
        )
        totalized_decisions += int(totalized)
        if disposition == "plain_body":
            continue
        page = _require_positive_int(
            marker.get("pdf_physical_page"),
            f"Spec 04-B candidate {candidate_index} page",
        )
        evidence_row = evidence_by_page.get(page)
        if evidence_row is None:
            _fail(
                "semantic_review_task_invalid",
                f"Spec 04-B candidate {candidate_index} lacks source-page evidence",
            )
        evidence_id = str(evidence_row["evidence_id"])
        marker_id = _require_text(
            marker.get("block_id"),
            f"Spec 04-B candidate {candidate_index} marker",
        )
        selected_pages.add(page)
        if disposition == "standalone_label":
            standalone_labels.append(
                {
                    "block_id": marker_id,
                    "semantic_role": semantic_role,
                    "source_evidence_ids": [evidence_id],
                    "review_status": "closed",
                }
            )
            continue
        if disposition != "teaching_group" or not body_options:
            _fail(
                "semantic_compact_review_invalid",
                f"Spec 04-B group candidate {candidate_index} has no deterministic body",
            )
        body_ids = [
            _require_text(
                row.get("block_id") if isinstance(row, Mapping) else None,
                f"Spec 04-B candidate {candidate_index} body block",
            )
            for row in body_options
        ]
        body_types = sorted(
            {
                _require_text(
                    row.get("source_type") if isinstance(row, Mapping) else None,
                    f"Spec 04-B candidate {candidate_index} body type",
                )
                for row in body_options
            }
        )
        if not set(body_types) <= SEMANTIC_BODY_TYPES:
            _fail(
                "semantic_review_task_invalid",
                f"Spec 04-B candidate {candidate_index} contains an unsafe body type",
            )
        teaching_groups.append(
            {
                "group_id": f"semantic-group-{candidate_index:06d}",
                "marker_block_id": marker_id,
                "body_block_ids": body_ids,
                "semantic_role": semantic_role,
                "source_evidence_ids": [evidence_id],
                "relation_rule": {
                    "same_physical_page": True,
                    "same_tree_path": True,
                    "allowed_body_source_types": body_types,
                    "basis": "same_tree_path_and_spatial_proximity",
                },
                "review_status": "closed",
            }
        )

    if not selected_pages:
        selected_pages.add(1)
    if any(page not in evidence_by_page for page in selected_pages):
        _fail(
            "semantic_review_task_invalid",
            "Spec 04-B projected evidence page is unavailable",
        )
    return {
        "schema_version": "spec04b-semantic-review-bundle/1.0",
        "review_id": str(task["task_id"]),
        "parent_binding": dict(parent_binding),
        "review": {
            "status": "closed",
            "open_items": 0,
            "decision_refs": [
                "compact-review::" + _canonical_hash(compact_review)
            ],
            "option_protocol": {
                "schema_version": option_protocol["schema_version"],
                "compact_review_sha256": _canonical_hash(compact_review),
                "decision_count": len(decisions),
                "totalized_decision_count": totalized_decisions,
            },
        },
        "source_evidence": [
            {
                "evidence_id": evidence_by_page[page]["evidence_id"],
                "path": evidence_by_page[page]["path"],
                "sha256": evidence_by_page[page]["sha256"],
                "pdf_physical_page": page,
            }
            for page in sorted(selected_pages)
        ],
        "teaching_groups": teaching_groups,
        "standalone_labels": standalone_labels,
    }


def prepare_semantic_review_task(args: argparse.Namespace) -> dict[str, Any]:
    task = _semantic_review_task(
        args.parent.resolve(),
        source_pdf=args.source_pdf.resolve(),
        source_pdf_ref=args.source_pdf_ref,
        parent_promotion=args.parent_promotion.resolve(),
    )
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        _fail(
            "review_task_output_exists",
            "semantic review task output already exists",
        )
    task_hash = _write_json(output, task)
    return {
        "status": "prepared",
        "task_sha256": task_hash,
        "task_canonical_sha256": _canonical_hash(task),
        "candidates": len(task["candidates"]),
        "pages": task["page_count"],
        "minimum_response_bytes": task["capacity"]["minimum_response_bytes"],
        "maximum_response_bytes": task["capacity"]["maximum_response_bytes"],
    }


def project_semantic_review(args: argparse.Namespace) -> dict[str, Any]:
    task = _read_json(args.task.resolve(), "Spec 04-B compact review task")
    compact_review = _read_json(
        args.compact_review.resolve(),
        "Spec 04-B compact review result",
    )
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        _fail(
            "review_projection_output_exists",
            "Spec 04-B projected review output already exists",
        )
    bundle = _project_semantic_review(task, compact_review)
    output_hash = _write_json(output, bundle)
    return {
        "status": "projected",
        "output_sha256": output_hash,
        "output_canonical_sha256": _canonical_hash(bundle),
        "teaching_groups": len(bundle["teaching_groups"]),
        "standalone_labels": len(bundle["standalone_labels"]),
        "source_evidence_pages": len(bundle["source_evidence"]),
    }


def _verify_review_task(path: Path, expected: Mapping[str, Any], label: str) -> str:
    actual = _read_json(path, label)
    if _canonical_hash(actual) != _canonical_hash(expected):
        _fail("review_task_binding_mismatch", f"{label} differs from deterministic preparation")
    return _canonical_hash(actual)


def _render_pdf_pages(
    source: Path,
    output: Path,
    *,
    page_count: int,
    page_numbers: Sequence[int],
    dpi: int = 72,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected_pages = sorted(set(page_numbers))
    if any(page < 1 or page > page_count for page in selected_pages):
        _fail("source_pdf_page_out_of_range", "selected render page is outside source PDF")
    output.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, Any]] = []
    try:
        import fitz  # type: ignore

        document = fitz.open(source)
        if len(document) != page_count:
            _fail("source_pdf_page_count_drift", "renderer sees a different page count")
        scale = dpi / 72
        matrix = fitz.Matrix(scale, scale)
        for page_number in selected_pages:
            page_index = page_number - 1
            page = document[page_index]
            pixmap = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
            destination = output / f"page-{page_number:06d}.png"
            pixmap.save(destination)
            rows.append(
                {
                    "physical_page": page_number,
                    "filename": destination.name,
                    "sha256": _sha256(destination),
                    "size_bytes": destination.stat().st_size,
                    "pixel_width": pixmap.width,
                    "pixel_height": pixmap.height,
                }
            )
        document.close()
        renderer = {
            "id": "PyMuPDF",
            "version": str(getattr(fitz, "VersionBind", "unknown")),
            "configuration": {
                "dpi": dpi,
                "scale": scale,
                "colorspace": "RGB",
                "alpha": False,
            },
        }
    except ImportError:
        binary = shutil.which("pdftoppm")
        if not binary:
            _fail("pdf_renderer_unavailable", "neither PyMuPDF nor pdftoppm is available")
        binary_sha = _sha256(Path(binary))
        for page_number in selected_pages:
            prefix = output / f"page-{page_number:06d}"
            completed = subprocess.run(
                [
                    binary,
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    "-r",
                    str(dpi),
                    "-png",
                    "-singlefile",
                    str(source),
                    str(prefix),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=300,
            )
            destination = prefix.with_suffix(".png")
            if completed.returncode != 0 or not destination.is_file():
                _fail(
                    "pdf_render_failed",
                    f"pdftoppm failed on page {page_number}: "
                    f"{completed.stderr[-1000:].decode(errors='replace')}",
                )
            try:
                from PIL import Image

                with Image.open(destination) as image:
                    width, height = image.size
            except Exception as exc:
                _fail("pdf_render_invalid", f"rendered page {page_number} is invalid: {exc}")
            rows.append(
                {
                    "physical_page": page_number,
                    "filename": destination.name,
                    "sha256": _sha256(destination),
                    "size_bytes": destination.stat().st_size,
                    "pixel_width": width,
                    "pixel_height": height,
                }
            )
        renderer = {
            "id": "pdftoppm",
            "binary_sha256": binary_sha,
            "configuration": {"dpi": dpi, "format": "png", "singlefile": True},
        }
    return rows, renderer


def _closed_parent_decision_index(parent: Path) -> tuple[dict[str, Any], str]:
    path = parent / "decisions/canonical_decision_index.json"
    index = _read_json(path, "parent decision index")
    summary = index.get("summary")
    if (
        index.get("spec_status") != "passed"
        or not isinstance(summary, Mapping)
        or any(int(summary.get(key, -1)) != 0 for key in ("open", "stale", "invalidated"))
    ):
        _fail("parent_decision_index_open", "parent decision index is not closed")
    return index, _sha256(path)


def _validate_scope_review(
    review: Mapping[str, Any],
    *,
    material_id: str,
    source_sha256: str,
    review_task: Mapping[str, Any],
    baseline: Mapping[str, Any],
    source_units: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    expected_fields = {
        "schema_version",
        "review_id",
        "material_id",
        "source_pdf_sha256",
        "baseline_sha256",
        "review_status",
        "pages",
        "page_overrides",
        "unit_scope_overrides",
        "reading_order_overrides",
        "relationships",
        "open_reviews",
    }
    if set(review) != expected_fields or review.get("schema_version") != SCOPE_REVIEW_SCHEMA:
        _fail("scope_review_shape_invalid", "scope/order review has missing or unknown fields")
    if review.get("material_id") != material_id or review.get("source_pdf_sha256") != source_sha256:
        _fail("scope_review_identity_mismatch", "scope/order review names another source")
    if review.get("baseline_sha256") != baseline.get("sha256"):
        _fail(
            "scope_review_baseline_mismatch",
            "scope/order review names another deterministic baseline",
        )
    if review.get("review_status") != "closed" or review.get("open_reviews") != []:
        _fail("scope_review_open", "scope/order review is not closed")
    page_count = int(review_task["page_count"])
    pages = review.get("pages")
    if not isinstance(pages, list) or len(pages) != page_count:
        _fail("scope_page_partition_invalid", "review must classify every physical page")
    baseline_pages = {
        int(item["physical_page"]): item
        for item in baseline.get("pages") or []
        if isinstance(item, Mapping)
    }
    baseline_units = {
        str(item["source_id"]): item
        for item in baseline.get("source_units") or []
        if isinstance(item, Mapping)
    }
    source_ids = {str(item["source_id"]) for item in source_units}
    if set(baseline_pages) != set(range(1, page_count + 1)):
        _fail("scope_baseline_invalid", "deterministic baseline page partition is not exact")
    if set(baseline_units) != source_ids:
        _fail("scope_baseline_invalid", "deterministic baseline source-unit partition is not exact")

    seen_pages: set[int] = set()
    page_dispositions: dict[int, str] = {}
    for raw_page in pages:
        if not isinstance(raw_page, Mapping):
            _fail("scope_page_partition_invalid", "review page must be an object")
        required = {"physical_page", "baseline_disposition"}
        if set(raw_page) != required:
            _fail("scope_page_shape_invalid", "review page has missing or unknown fields")
        page_number = _require_positive_int(raw_page.get("physical_page"), "physical_page")
        if page_number > page_count or page_number in seen_pages:
            _fail("scope_page_partition_invalid", f"page {page_number} is invalid or duplicated")
        seen_pages.add(page_number)
        disposition = raw_page.get("baseline_disposition")
        if disposition not in {"accepted", "overridden"}:
            _fail(
                "scope_page_disposition_invalid",
                f"page {page_number} has invalid baseline disposition",
            )
        page_dispositions[page_number] = str(disposition)
    if seen_pages != set(range(1, page_count + 1)):
        _fail("scope_page_partition_invalid", "review page partition is not exact")

    raw_page_overrides = review.get("page_overrides")
    if not isinstance(raw_page_overrides, list):
        _fail("scope_page_override_invalid", "page_overrides must be an array")
    page_overrides: dict[int, Mapping[str, Any]] = {}
    for raw_page in raw_page_overrides:
        required = {
            "physical_page",
            "scope_status",
            "page_category",
            "reason",
            "evidence_refs",
            "review_status",
        }
        if not isinstance(raw_page, Mapping) or set(raw_page) != required:
            _fail(
                "scope_page_override_invalid",
                "page override has missing or unknown fields",
            )
        page_number = _require_positive_int(
            raw_page.get("physical_page"),
            "page override physical_page",
        )
        if (
            page_number > page_count
            or page_number in page_overrides
            or page_dispositions.get(page_number) != "overridden"
        ):
            _fail(
                "scope_page_override_invalid",
                f"page override {page_number} is unknown, duplicated, or not declared",
            )
        if raw_page.get("scope_status") not in {"included", "excluded"}:
            _fail("scope_status_invalid", f"page {page_number} has invalid scope status")
        _require_text(raw_page.get("page_category"), f"page {page_number}.page_category")
        _require_text(raw_page.get("reason"), f"page {page_number}.reason")
        if raw_page.get("review_status") != "closed":
            _fail("scope_review_open", f"page {page_number} is not closed")
        evidence_refs = raw_page.get("evidence_refs")
        if (
            not isinstance(evidence_refs, list)
            or not evidence_refs
            or any(not isinstance(item, str) or not item for item in evidence_refs)
        ):
            _fail("scope_evidence_invalid", f"page {page_number} review evidence is invalid")
        page_overrides[page_number] = raw_page
    expected_override_pages = {
        page
        for page, disposition in page_dispositions.items()
        if disposition == "overridden"
    }
    if set(page_overrides) != expected_override_pages:
        _fail(
            "scope_page_override_invalid",
            "page overrides do not exactly match overridden page dispositions",
        )

    normalized_pages: list[dict[str, Any]] = []
    page_decisions: dict[int, Mapping[str, Any]] = {}
    for page_number in range(1, page_count + 1):
        decision = page_overrides.get(page_number) or baseline_pages[page_number]
        page_decisions[page_number] = decision
        normalized_pages.append(
            {
                "physical_page": page_number,
                "scope_status": decision["scope_status"],
                "page_category": decision["page_category"],
                "reason": decision["reason"],
                "complexity_flags": [],
                "cross_page_relationship_ids": [],
                "evidence_refs": list(decision["evidence_refs"]),
                "review_status": "closed",
            }
        )

    unit_overrides = review.get("unit_scope_overrides")
    if not isinstance(unit_overrides, list):
        _fail("scope_unit_override_invalid", "unit_scope_overrides must be an array")
    overrides_by_id: dict[str, Mapping[str, Any]] = {}
    for raw in unit_overrides:
        if not isinstance(raw, Mapping) or set(raw) != {
            "source_id",
            "scope_status",
            "reason",
            "evidence_refs",
            "review_status",
        }:
            _fail("scope_unit_override_invalid", "unit scope override shape is invalid")
        source_id = _require_text(raw.get("source_id"), "unit scope override source_id")
        if source_id not in baseline_units or source_id in overrides_by_id:
            _fail(
                "scope_unit_override_invalid",
                f"unit scope override {source_id!r} is unknown or duplicated",
            )
        if raw.get("scope_status") not in {"included", "excluded"}:
            _fail("scope_status_invalid", f"source unit {source_id!r} status is invalid")
        _require_text(raw.get("reason"), f"{source_id}.reason")
        evidence = raw.get("evidence_refs")
        if (
            not isinstance(evidence, list)
            or not evidence
            or any(not isinstance(item, str) or not item for item in evidence)
        ):
            _fail("scope_evidence_invalid", f"{source_id} override lacks evidence")
        if raw.get("review_status") != "closed":
            _fail("scope_review_open", f"source unit {source_id!r} override is not closed")
        overrides_by_id[source_id] = raw

    normalized_units: list[dict[str, Any]] = []
    for source_id, baseline_unit in sorted(
        baseline_units.items(),
        key=lambda item: (
            int(item[1]["physical_page"]),
            int(item[1]["baseline_page_order"]),
            item[0],
        ),
    ):
        page_number = int(baseline_unit["physical_page"])
        page_decision = page_decisions[page_number]
        override = overrides_by_id.get(source_id)
        status = (
            str(override["scope_status"])
            if override is not None
            else str(baseline_unit["scope_status"])
        )
        reason = (
            str(override["reason"])
            if override is not None
            else str(baseline_unit["scope_reason"])
        )
        evidence_refs = (
            list(override["evidence_refs"])
            if override is not None
            else list(baseline_unit["evidence_refs"])
        )
        if page_decision["scope_status"] == "excluded":
            status = "excluded"
            reason = f"page excluded: {page_decision['reason']}"
            evidence_refs = list(page_decision["evidence_refs"])
        normalized_units.append(
            {
                "source_id": source_id,
                "physical_page": page_number,
                "scope_status": status,
                "scope_reason": reason,
                "candidate_final_order": None,
                "baseline_page_order": int(baseline_unit["baseline_page_order"]),
                "semantic_group_id": None,
                "composite_relationship_ids": [],
                "evidence_refs": evidence_refs,
                "review_status": "closed",
            }
        )
    units_by_page: dict[int, list[dict[str, Any]]] = {
        page: [] for page in range(1, page_count + 1)
    }
    for unit in normalized_units:
        units_by_page[int(unit["physical_page"])].append(unit)
    for page_number, page_decision in page_decisions.items():
        if (
            page_decision["scope_status"] == "included"
            and not any(
                unit["scope_status"] == "included"
                for unit in units_by_page[page_number]
            )
        ):
            _fail(
                "included_page_without_body",
                f"included page {page_number} has no included source unit",
            )

    order_overrides = review.get("reading_order_overrides")
    if not isinstance(order_overrides, list):
        _fail(
            "reading_order_override_invalid",
            "reading_order_overrides must be an array",
        )
    order_by_page: dict[int, list[str]] = {}
    for raw in order_overrides:
        if not isinstance(raw, Mapping) or set(raw) != {
            "physical_page",
            "ordered_source_ids",
            "reason",
            "evidence_refs",
            "review_status",
        }:
            _fail(
                "reading_order_override_invalid",
                "reading order override shape is invalid",
            )
        page_number = _require_positive_int(
            raw.get("physical_page"),
            "reading order override physical_page",
        )
        if page_number > page_count or page_number in order_by_page:
            _fail(
                "reading_order_override_invalid",
                f"reading order override page {page_number} is invalid or duplicated",
            )
        ordered_ids = raw.get("ordered_source_ids")
        expected_ids = {
            unit["source_id"]
            for unit in units_by_page[page_number]
            if unit["scope_status"] == "included"
        }
        if (
            not isinstance(ordered_ids, list)
            or len(ordered_ids) != len(set(ordered_ids))
            or set(ordered_ids) != expected_ids
        ):
            _fail(
                "reading_order_override_invalid",
                f"reading order override page {page_number} is not an exact included-unit partition",
            )
        _require_text(raw.get("reason"), f"page {page_number} order reason")
        evidence = raw.get("evidence_refs")
        if (
            not isinstance(evidence, list)
            or not evidence
            or any(not isinstance(item, str) or not item for item in evidence)
            or raw.get("review_status") != "closed"
        ):
            _fail(
                "reading_order_override_invalid",
                f"reading order override page {page_number} lacks closed evidence",
            )
        order_by_page[page_number] = list(ordered_ids)

    next_order = 1
    for page_number in range(1, page_count + 1):
        included = [
            unit
            for unit in sorted(
                units_by_page[page_number],
                key=lambda item: (
                    int(item["baseline_page_order"]),
                    item["source_id"],
                ),
            )
            if unit["scope_status"] == "included"
        ]
        if page_number in order_by_page:
            included_by_id = {unit["source_id"]: unit for unit in included}
            included = [
                included_by_id[source_id]
                for source_id in order_by_page[page_number]
            ]
        for unit in included:
            unit["candidate_final_order"] = next_order
            next_order += 1

    relationships = review.get("relationships")
    if not isinstance(relationships, list):
        _fail("scope_relationship_invalid", "relationships must be an array")
    normalized_relationships: list[dict[str, Any]] = []
    relationship_ids: set[str] = set()
    units_by_id = {item["source_id"]: item for item in normalized_units}
    page_relationships: dict[int, list[str]] = {
        page: [] for page in range(1, page_count + 1)
    }
    for raw in relationships:
        if not isinstance(raw, Mapping):
            _fail("scope_relationship_invalid", "relationship must be an object")
        fields = {
            "relationship_id",
            "relationship_type",
            "member_source_ids",
            "roles",
            "physical_pages",
            "evidence_refs",
            "review_status",
        }
        if set(raw) != fields:
            _fail("scope_relationship_invalid", "relationship has missing or unknown fields")
        relationship_id = _require_text(raw.get("relationship_id"), "relationship_id")
        if relationship_id in relationship_ids:
            _fail("scope_relationship_invalid", f"relationship {relationship_id!r} is duplicated")
        relationship_ids.add(relationship_id)
        if raw.get("review_status") != "closed":
            _fail("scope_review_open", f"relationship {relationship_id!r} is not closed")
        relationship_type = raw.get("relationship_type")
        if relationship_type not in {
            "semantic_group",
            "cross_page_group",
            "stem_media_options",
        }:
            _fail("scope_relationship_invalid", f"relationship {relationship_id!r} type is invalid")
        members = raw.get("member_source_ids")
        if (
            not isinstance(members, list)
            or not members
            or len(members) != len(set(members))
            or any(item not in units_by_id for item in members)
        ):
            _fail("scope_relationship_invalid", f"relationship {relationship_id!r} members are invalid")
        if any(units_by_id[item]["scope_status"] != "included" for item in members):
            _fail("scope_relationship_invalid", f"relationship {relationship_id!r} includes excluded units")
        pages_set = sorted({units_by_id[item]["physical_page"] for item in members})
        if raw.get("physical_pages") != pages_set:
            _fail("scope_relationship_invalid", f"relationship {relationship_id!r} page set differs")
        evidence = raw.get("evidence_refs")
        if not isinstance(evidence, list) or not evidence:
            _fail("scope_relationship_invalid", f"relationship {relationship_id!r} lacks evidence")
        roles = raw.get("roles")
        if relationship_type == "stem_media_options":
            if not isinstance(roles, Mapping) or set(roles) != {"stem", "media", "options"}:
                _fail("composite_relationship_invalid", f"{relationship_id} roles are incomplete")
            role_members = [
                item
                for role in ("stem", "media", "options")
                for item in (roles.get(role) or [])
            ]
            if (
                len(pages_set) != 1
                or role_members != members
                or len(role_members) != len(set(role_members))
                or any(not roles.get(role) for role in ("stem", "media", "options"))
            ):
                _fail("composite_relationship_invalid", f"{relationship_id} role partition is invalid")
            orders = [units_by_id[item]["candidate_final_order"] for item in role_members]
            if orders != list(range(min(orders), max(orders) + 1)):
                _fail("composite_relationship_invalid", f"{relationship_id} is not contiguous")
        elif (
            not isinstance(roles, Mapping)
            or set(roles) != {"stem", "media", "options"}
            or any(roles.get(role) != [] for role in ("stem", "media", "options"))
        ):
            _fail(
                "scope_relationship_invalid",
                f"{relationship_id} must use an empty fixed roles object",
            )
        if relationship_type == "semantic_group":
            for member in members:
                existing = units_by_id[member]["semantic_group_id"]
                if existing not in {None, relationship_id}:
                    _fail(
                        "scope_relationship_invalid",
                        f"source unit {member!r} has conflicting semantic groups",
                    )
                units_by_id[member]["semantic_group_id"] = relationship_id
        else:
            for member in members:
                units_by_id[member]["composite_relationship_ids"].append(
                    relationship_id
                )
        for page_number in pages_set:
            page_relationships[page_number].append(relationship_id)
        normalized_relationships.append(dict(raw))
    complexity_by_page = {
        int(item["physical_page"]): list(item.get("complexity_flags") or [])
        for item in review_task.get("pages") or []
        if isinstance(item, Mapping)
    }
    for page in normalized_pages:
        page_number = int(page["physical_page"])
        page["complexity_flags"] = complexity_by_page.get(page_number, [])
        page["cross_page_relationship_ids"] = sorted(
            relationship_id
            for relationship_id in page_relationships[page_number]
            if len(
                next(
                    item["physical_pages"]
                    for item in normalized_relationships
                    if item["relationship_id"] == relationship_id
                )
            )
            > 1
        )
    for unit in normalized_units:
        unit.pop("baseline_page_order", None)
    return normalized_pages, normalized_units, normalized_relationships


def produce_scope(args: argparse.Namespace) -> dict[str, Any]:
    parent = args.parent.resolve()
    output = _prepare_stage_output(args.output_dir)
    contract = _verify_materialized_parent(parent)
    _verify_external_inputs(
        contract,
        source_pdf=args.source_pdf.resolve(),
        mineru_archive=args.mineru_archive.resolve(),
        popo_archive=args.popo_archive.resolve(),
        template_archive=args.template_archive.resolve(),
    )
    parent_index, parent_index_hash = _closed_parent_decision_index(parent)
    _copy_compact_parent(
        parent,
        output,
        allowlist=SPEC01_COMPACT_PARENT_FILES,
    )
    shutil.copyfile(
        output / "decisions/canonical_decision_index.json",
        output / "decisions/input_decision_index.json",
    )
    identity = contract["material_identity"]
    material_id = identity["material_id"]
    source_sha = identity["source_pdf_sha256"]
    page_count = int(identity["page_count"])
    source_units = _read_jsonl(parent / "source/popo_source_units.jsonl", "Popo source units")
    expected_review_task = _scope_review_task(parent)
    review_task_hash = _verify_review_task(
        args.review_task.resolve(),
        expected_review_task,
        "scope review task",
    )
    review = _read_json(args.review.resolve(), "scope/order review")
    baseline = _scope_baseline(
        page_count=page_count,
        source_units=source_units,
    )
    pages, units, relationships = _validate_scope_review(
        review,
        material_id=material_id,
        source_sha256=source_sha,
        review_task=expected_review_task,
        baseline=baseline,
        source_units=source_units,
    )

    risk_pages = [
        int(item["physical_page"])
        for item in sorted(
            (page for page in pages if page["complexity_flags"]),
            key=lambda page: (-len(page["complexity_flags"]), int(page["physical_page"])),
        )[:12]
    ]
    rendered: list[dict[str, Any]] = []
    renderer: dict[str, Any] = {
        "id": "deferred_to_stage10",
        "configuration": {
            "full_page_visual_evidence": "stage10_only",
            "stage2_risk_page_limit": 12,
            "stage2_thumbnail_dpi": 72,
        },
    }
    if risk_pages:
        rendered, renderer = _render_pdf_pages(
            args.source_pdf.resolve(),
            output / "evidence/risk-page-thumbnails",
            page_count=page_count,
            page_numbers=risk_pages,
            dpi=72,
        )
    geometry = _read_json(parent / "evidence/pdf_page_geometry.json", "PDF geometry")
    geometry_by_page = {int(row["physical_page"]): row for row in geometry["pages"]}
    rendered_by_page = {int(row["physical_page"]): row for row in rendered}
    render_rows = []
    for page in range(1, page_count + 1):
        row = rendered_by_page.get(page)
        ledger_row: dict[str, Any] = {
            "schema_version": "source-page-render-ledger-row/2.0",
            "source_pdf_sha256": source_sha,
            "physical_page": page,
            "page_geometry": geometry_by_page[page],
            "visual_evidence_status": (
                "risk_thumbnail_materialized" if row else "deferred_to_stage10"
            ),
            "full_page_visual_evidence_stage": "stage10",
            "risk_thumbnail": None,
        }
        if row:
            ledger_row["risk_thumbnail"] = {
                "render_configuration": renderer,
                "stage2_candidate_path": (
                    f"evidence/risk-page-thumbnails/{row['filename']}"
                ),
                "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
                "pixel_width": row["pixel_width"],
                "pixel_height": row["pixel_height"],
            }
        render_rows.append(ledger_row)
    render_ledger_hash = _write_jsonl(
        output / "ledgers/source_page_render_ledger.jsonl",
        render_rows,
    )
    _copy_file(args.review.resolve(), output / "reviews/spec02_scope_order_review.json")
    _copy_file(args.review_task.resolve(), output / "reviews/spec02_scope_order_review_task.json")
    review_hash = _sha256(output / "reviews/spec02_scope_order_review.json")
    decision_event = {
        "schema_version": "decision-event/1.0",
        "decision_id": args.stage_decision_id,
        "rule_id": "SC-H01..SC-H09/RO-H01..RO-H08",
        "status": "closed",
        "decision": "Freeze the explicit all-page review over the deterministic exhaustive source-unit baseline and its bounded deltas; no flat-array order is inferred.",
        "evidence_refs": [
            "reviews/spec02_scope_order_review.json",
            "ledgers/source_page_render_ledger.jsonl",
        ],
    }
    decision_hash = _write_jsonl(
        output / "decisions/scope_order_decisions.jsonl",
        [decision_event],
    )
    inherited = list(parent_index.get("decisions") or [])
    child_index = {
        "schema_version": DECISION_INDEX_SCHEMA,
        "decision_index_id": parent_index["decision_index_id"],
        "snapshot_id": args.decision_snapshot_id,
        "version": int(parent_index["version"]) + 1,
        "parent_index_ref": "decisions/input_decision_index.json",
        "parent_index_hash": parent_index_hash,
        "acyclic_commit_rule": "parent_D1_then_render_and_review_E2_then_child_D2_then_scope_order_ledgers",
        "evidence_committed_before_index": [
            {
                "ref": "reviews/spec02_scope_order_review_task.json",
                "sha256": _sha256(output / "reviews/spec02_scope_order_review_task.json"),
            },
            {
                "ref": "reviews/spec02_scope_order_review.json",
                "sha256": review_hash,
            },
            {
                "ref": "ledgers/source_page_render_ledger.jsonl",
                "sha256": render_ledger_hash,
            },
        ],
        "decision_event_files": [
            {
                "path": "decisions/input_decisions.jsonl",
                "sha256": _sha256(output / "decisions/input_decisions.jsonl"),
            },
            {
                "path": "decisions/scope_order_decisions.jsonl",
                "sha256": decision_hash,
            },
        ],
        "decisions": [
            *inherited,
            {
                "decision_id": args.stage_decision_id,
                "status": "closed",
                "rule_id": "SC-H01..SC-H09/RO-H01..RO-H08",
                "event_file": "decisions/scope_order_decisions.jsonl",
            },
        ],
        "summary": {
            "total": len(inherited) + 1,
            "closed": len(inherited) + 1,
            "open": 0,
            "stale": 0,
            "invalidated": 0,
        },
        "spec_status": "passed",
    }
    decision_index_hash = _write_json(
        output / "decisions/canonical_decision_index.json",
        child_index,
    )
    scope_ledger_hash = _write_json(
        output / "ledgers/source_scope_ledger.json",
        {
            "schema_version": "source-scope-ledger/2.0",
            "material_id": material_id,
            "source_pdf_sha256": source_sha,
            "decision_index_ref": "decisions/canonical_decision_index.json",
            "decision_index_hash": decision_index_hash,
            "pages": sorted(pages, key=lambda item: item["physical_page"]),
            "source_units": sorted(
                units,
                key=lambda item: (
                    item["physical_page"],
                    item["candidate_final_order"] is None,
                    item["candidate_final_order"] or 0,
                    item["source_id"],
                ),
            ),
            "summary": {
                "pages": len(pages),
                "included_pages": sum(item["scope_status"] == "included" for item in pages),
                "excluded_pages": sum(item["scope_status"] == "excluded" for item in pages),
                "source_units": len(units),
                "included_units": sum(item["scope_status"] == "included" for item in units),
                "excluded_units": sum(item["scope_status"] == "excluded" for item in units),
                "open_reviews": 0,
            },
            "spec_status": "passed",
        },
    )
    reading_order_hash = _write_json(
        output / "ledgers/reading_order_ledger.json",
        {
            "schema_version": "reading-order-ledger/2.0",
            "source_scope_ledger_ref": "ledgers/source_scope_ledger.json",
            "source_scope_ledger_hash": scope_ledger_hash,
            "decision_index_ref": "decisions/canonical_decision_index.json",
            "decision_index_hash": decision_index_hash,
            "ordered_source_units": sorted(
                (
                    {
                        "source_id": item["source_id"],
                        "physical_page": item["physical_page"],
                        "candidate_final_order": item["candidate_final_order"],
                        "evidence_refs": item["evidence_refs"],
                        "semantic_group_id": item["semantic_group_id"],
                        "composite_relationship_ids": item["composite_relationship_ids"],
                    }
                    for item in units
                    if item["scope_status"] == "included"
                ),
                key=lambda item: item["candidate_final_order"],
            ),
            "relationships": relationships,
            "summary": {
                "ordered_units": sum(item["scope_status"] == "included" for item in units),
                "relationships": len(relationships),
                "open_reviews": 0,
            },
            "spec_status": "passed",
        },
    )
    relationships_hash = _write_json(
        output / "contracts/composite_reading_relationships.json",
        {
            "schema_version": "composite-reading-relationship/1.0",
            "relationships": relationships,
            "unresolved_candidates": [],
            "spec_status": "passed",
        },
    )
    gates = {
        **{f"SC-H{index:02d}": "passed" for index in range(1, 10)},
        **{f"RO-H{index:02d}": "passed" for index in range(1, 9)},
    }
    report_hash = _write_json(
        output / "reports/scope_order_validation_report.json",
        {
            "schema_version": "scope-order-validation-report/1.0",
            "gate_status": gates,
            "failure_codes": [],
            "open_reviews": [],
            "spec_status": "passed",
            "renderer": renderer,
        },
    )
    stage_manifest_hash = _stage_manifest(
        output,
        stage="source_scope_and_order",
        run_id=args.run_id,
        schema_path=args.contract_schema_path,
        schema_sha256=args.contract_schema_sha256,
        gate_status=gates,
        artifacts={
            "source_scope_ledger": "ledgers/source_scope_ledger.json",
            "reading_order_ledger": "ledgers/reading_order_ledger.json",
            "source_page_render_ledger": "ledgers/source_page_render_ledger.jsonl",
            "composite_relationships": "contracts/composite_reading_relationships.json",
            "decision_index": "decisions/canonical_decision_index.json",
            "validation_report": "reports/scope_order_validation_report.json",
        },
        metrics={
            "pages_indexed": len(render_rows),
            "risk_page_thumbnails": len(rendered),
            "full_page_rasters_materialized": 0,
            "source_units": len(units),
            "included_units": sum(item["scope_status"] == "included" for item in units),
            "relationships": len(relationships),
            "review_task_canonical_sha256": review_task_hash,
            "scope_ledger_sha256": scope_ledger_hash,
            "reading_order_sha256": reading_order_hash,
            "relationships_sha256": relationships_hash,
            "report_sha256": report_hash,
        },
    )
    _run_manifest(
        output,
        job_id=args.job_id,
        run_id=args.run_id,
        stage="source_scope_and_order",
        stage_manifest_hash=stage_manifest_hash,
    )
    return {
        "candidate_status": "complete",
        "spec_status": "not_evaluated",
        "pages_rendered": len(render_rows),
        "included_units": sum(item["scope_status"] == "included" for item in units),
    }


def _validate_media_review(
    review: Mapping[str, Any],
    *,
    material_id: str,
    source_sha256: str,
    media_atoms: Sequence[Mapping[str, Any]],
    source_ids: set[str],
    review_task: Mapping[str, Any],
) -> list[dict[str, Any]]:
    expected_fields = {
        "schema_version",
        "review_id",
        "material_id",
        "source_pdf_sha256",
        "baseline_sha256",
        "review_status",
        "media",
        "media_overrides",
        "open_reviews",
    }
    if set(review) != expected_fields or review.get("schema_version") != MEDIA_REVIEW_SCHEMA:
        _fail("media_review_shape_invalid", "media review has missing or unknown fields")
    if review.get("material_id") != material_id or review.get("source_pdf_sha256") != source_sha256:
        _fail("media_review_identity_mismatch", "media review names another source")
    if review.get("baseline_sha256") != review_task.get("baseline_sha256"):
        _fail(
            "media_review_baseline_mismatch",
            "media review names another deterministic baseline",
        )
    if review.get("review_status") != "closed" or review.get("open_reviews") != []:
        _fail("media_review_open", "media review is not closed")
    rows = review.get("media")
    if not isinstance(rows, list):
        _fail("media_review_shape_invalid", "media must be an array")
    task_atoms = review_task.get("media_atoms")
    if not isinstance(task_atoms, list) or len(task_atoms) != len(media_atoms):
        _fail("media_review_task_invalid", "media review task partition is invalid")
    atoms = {str(item["media_id"]): item for item in media_atoms}
    task_by_index = {
        int(item["media_index"]): item
        for item in task_atoms
        if isinstance(item, Mapping)
        and isinstance(item.get("media_index"), int)
        and not isinstance(item.get("media_index"), bool)
    }
    if set(task_by_index) != set(range(1, len(media_atoms) + 1)):
        _fail("media_review_task_invalid", "media review task indexes are not exact")
    page_source_units = review_task.get("page_source_units")
    if not isinstance(page_source_units, list):
        _fail("media_review_task_invalid", "media review task source units are invalid")
    source_indexes_by_page: dict[int, dict[int, str]] = {}
    source_metadata_by_id: dict[str, Mapping[str, Any]] = {}
    for page in page_source_units:
        if not isinstance(page, Mapping) or not isinstance(
            page.get("source_units"), list
        ):
            _fail("media_review_task_invalid", "page source unit group is invalid")
        page_number = int(page["physical_page"])
        indexed: dict[int, str] = {}
        for unit in page["source_units"]:
            if not isinstance(unit, Mapping):
                _fail("media_review_task_invalid", "page source unit is invalid")
            index = int(unit["source_unit_index"])
            source_id = str(unit["source_id"])
            if index in indexed or source_id not in source_ids:
                _fail("media_review_task_invalid", "page source unit index is invalid")
            indexed[index] = source_id
            if source_id in source_metadata_by_id:
                _fail(
                    "media_review_task_invalid",
                    "page source unit is repeated across pages",
                )
            source_metadata_by_id[source_id] = unit
        source_indexes_by_page[page_number] = indexed

    seen: set[int] = set()
    dispositions: dict[int, str] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            _fail("media_review_shape_invalid", "media disposition must be an object")
        fields = {"media_index", "baseline_disposition"}
        if set(raw) != fields:
            _fail("media_review_shape_invalid", "media disposition has missing or unknown fields")
        media_index = _require_positive_int(raw.get("media_index"), "media_index")
        if media_index in seen or media_index not in task_by_index:
            _fail("media_partition_invalid", f"media_index {media_index} is unknown or duplicated")
        seen.add(media_index)
        baseline_disposition = raw.get("baseline_disposition")
        if baseline_disposition not in {"accepted", "overridden"}:
            _fail(
                "media_disposition_invalid",
                f"media_index {media_index} baseline disposition is invalid",
            )
        dispositions[media_index] = str(baseline_disposition)
    if seen != set(task_by_index):
        missing = sorted(set(task_by_index) - seen)
        _fail("media_partition_invalid", f"media review omits indexes {missing[:10]}")

    raw_overrides = review.get("media_overrides")
    if not isinstance(raw_overrides, list):
        _fail("media_review_shape_invalid", "media_overrides must be an array")
    overrides: dict[int, Mapping[str, Any]] = {}
    for raw in raw_overrides:
        fields = {
            "media_index",
            "disposition",
            "selected_candidate_index",
            "source_unit_indexes",
            "reason",
            "review_status",
        }
        if not isinstance(raw, Mapping) or set(raw) != fields:
            _fail("media_review_shape_invalid", "media override shape is invalid")
        media_index = _require_positive_int(
            raw.get("media_index"),
            "media override index",
        )
        if (
            media_index in overrides
            or dispositions.get(media_index) != "overridden"
        ):
            _fail(
                "media_override_invalid",
                f"media override {media_index} is unknown, duplicated, or not declared",
            )
        if raw.get("review_status") != "closed":
            _fail("media_review_open", f"media index {media_index} is not closed")
        _require_text(raw.get("reason"), f"media index {media_index} reason")
        overrides[media_index] = raw
    expected_overrides = {
        index for index, value in dispositions.items() if value == "overridden"
    }
    if set(overrides) != expected_overrides:
        _fail(
            "media_override_invalid",
            "media overrides do not exactly match overridden dispositions",
        )

    normalized: list[dict[str, Any]] = []
    for media_index in range(1, len(media_atoms) + 1):
        task_atom = task_by_index[media_index]
        media_id = str(task_atom["media_id"])
        atom = atoms.get(media_id)
        if atom is None:
            _fail("media_review_task_invalid", f"task media {media_id!r} is unknown")
        candidates_by_index = {
            int(item["candidate_index"]): item
            for item in task_atom.get("candidates") or []
            if isinstance(item, Mapping)
            and isinstance(item.get("candidate_index"), int)
            and not isinstance(item.get("candidate_index"), bool)
        }
        full_candidates = {
            str(item["candidate_id"]): item
            for item in atom.get("candidates") or []
            if isinstance(item, Mapping) and item.get("candidate_id")
        }
        override = overrides.get(media_index)
        if override is None:
            disposition = str(task_atom["baseline_disposition"])
            selected_index = int(task_atom["baseline_candidate_index"])
            raw_baseline_indexes = task_atom.get(
                "baseline_source_unit_indexes"
            )
            if not isinstance(raw_baseline_indexes, list):
                _fail(
                    "media_review_task_invalid",
                    f"{media_id} has no deterministic source ownership",
                )
            linked_indexes = list(raw_baseline_indexes)
        else:
            disposition = str(override["disposition"])
            selected_index = _require_nonnegative_int(
                override.get("selected_candidate_index"),
                f"{media_id} selected_candidate_index",
            )
            raw_linked_indexes = override.get("source_unit_indexes")
            if not isinstance(raw_linked_indexes, list):
                _fail(
                    "media_source_binding_invalid",
                    f"{media_id} source indexes must be an array",
                )
            linked_indexes = list(raw_linked_indexes)
        if disposition not in {
            "source_asset",
            "source_region",
            "structured_transcription",
            "excluded_noninstructional",
        }:
            _fail("media_disposition_invalid", f"{media_id} disposition is invalid")
        if (
            len(linked_indexes) != len(set(linked_indexes))
            or any(
                not isinstance(index, int) or isinstance(index, bool)
                for index in linked_indexes
            )
        ):
            _fail("media_source_binding_invalid", f"{media_id} source indexes are invalid")
        page_number = int(task_atom["physical_page"])
        page_indexes = source_indexes_by_page.get(page_number, {})
        if any(index not in page_indexes for index in linked_indexes):
            _fail("media_source_binding_invalid", f"{media_id} source indexes are unknown")
        linked_source_ids = [page_indexes[index] for index in linked_indexes]
        if disposition == "excluded_noninstructional":
            if selected_index != 0 or not linked_source_ids:
                _fail("media_disposition_invalid", f"{media_id} excluded media selects a candidate")
            selected_candidate = None
            selected = None
            evidence = [f"media:{media_id}"] + [
                f"source:{source_id}" for source_id in linked_source_ids
            ]
        else:
            compact_candidate = candidates_by_index.get(selected_index)
            if compact_candidate is None:
                _fail("media_candidate_invalid", f"{media_id} selects an unknown candidate")
            selected = str(compact_candidate["candidate_id"])
            selected_candidate = full_candidates.get(selected)
            if selected_candidate is None:
                _fail("media_candidate_invalid", f"{media_id} candidate binding drifted")
            expected_type = {
                "source_asset": "source_asset_image",
                "source_region": "source_region_image",
                "structured_transcription": {
                    "structured_table",
                    "structured_chart",
                    "structured_formula",
                },
            }[disposition]
            actual_type = selected_candidate.get("representation_type")
            if isinstance(expected_type, set):
                valid_type = actual_type in expected_type
            else:
                valid_type = actual_type == expected_type
            if not valid_type:
                _fail("media_candidate_invalid", f"{media_id} candidate type differs")
            evidence = [f"media:{media_id}", f"candidate:{selected}"]
        normalized.append(
            {
                "media_id": media_id,
                "disposition": disposition,
                "selected_candidate_id": selected,
                "selected_candidate": selected_candidate,
                "source_ids": list(linked_source_ids),
                "evidence_refs": list(evidence),
                "review_status": "closed",
            }
        )
    ownership_counts = Counter(
        source_id
        for row in normalized
        if row["disposition"] != "excluded_noninstructional"
        for source_id in row["source_ids"]
    )
    if any(
        row["disposition"] != "excluded_noninstructional"
        and not row["source_ids"]
        for row in normalized
    ):
        _fail(
            "media_source_ownership_unresolved",
            "a nonexcluded media atom has no source ownership",
        )
    if any(count > 1 for count in ownership_counts.values()):
        _fail(
            "media_source_ownership_duplicate",
            "one source unit is owned by multiple media atoms",
        )
    fragile_ids = {
        source_id
        for source_id, metadata in source_metadata_by_id.items()
        if metadata.get("scope_status") == "included"
        and str(metadata.get("source_type") or "").strip().lower()
        in FRAGILE_SOURCE_TYPES
    }
    missing_fragile = sorted(
        source_id
        for source_id in fragile_ids
        if ownership_counts[source_id] != 1
    )
    if missing_fragile:
        _fail(
            "media_source_ownership_unresolved",
            f"included fragile source ownership is not exact: {missing_fragile[:10]}",
        )
    return normalized


def _block_id(material_id: str, source_id: str) -> str:
    return "src-" + hashlib.sha256(f"{material_id}\0{source_id}".encode("utf-8")).hexdigest()[:24]


def produce_ledger(args: argparse.Namespace) -> dict[str, Any]:
    parent = args.parent.resolve()
    output = _prepare_stage_output(args.output_dir)
    contract = _verify_materialized_parent(parent)
    _verify_external_inputs(
        contract,
        source_pdf=args.source_pdf.resolve(),
        mineru_archive=args.mineru_archive.resolve(),
        popo_archive=args.popo_archive.resolve(),
        template_archive=args.template_archive.resolve(),
    )
    parent_index, parent_index_hash = _closed_parent_decision_index(parent)
    _copy_compact_parent(
        parent,
        output,
        allowlist=SPEC02_COMPACT_PARENT_FILES,
    )
    shutil.copyfile(
        output / "decisions/canonical_decision_index.json",
        output / "decisions/scope_order_decision_index.json",
    )
    identity = contract["material_identity"]
    material_id = identity["material_id"]
    source_sha = identity["source_pdf_sha256"]
    source_units = _read_jsonl(parent / "source/popo_source_units.jsonl", "Popo source units")
    media_atoms = _read_jsonl(parent / "source/mineru_media_atoms.jsonl", "MinerU media atoms")
    scope = _read_json(parent / "ledgers/source_scope_ledger.json", "source scope ledger")
    order = _read_json(parent / "ledgers/reading_order_ledger.json", "reading order ledger")
    if scope.get("spec_status") != "passed" or order.get("spec_status") != "passed":
        _fail("parent_scope_order_not_passed", "promoted Spec 02 ledgers are not closed")
    scope_units = scope.get("source_units")
    if not isinstance(scope_units, list):
        _fail("parent_scope_order_invalid", "source scope ledger has no source_units")
    scope_by_id = {
        str(item["source_id"]): item
        for item in scope_units
        if isinstance(item, Mapping) and item.get("source_id")
    }
    source_by_id = {str(item["source_id"]): item for item in source_units}
    if set(scope_by_id) != set(source_by_id) or len(scope_units) != len(source_by_id):
        _fail("source_partition_invalid", "Spec 02 does not classify every Popo source unit exactly once")
    included = [item for item in scope_units if item.get("scope_status") == "included"]
    orders = [item.get("candidate_final_order") for item in included]
    if sorted(orders) != list(range(1, len(orders) + 1)):
        _fail("reading_order_not_contiguous", "Spec 02 included order is not exact 1..N")

    review_task_hash = _verify_review_task(
        args.review_task.resolve(),
        _media_review_task(parent),
        "media review task",
    )
    review = _read_json(args.review.resolve(), "media review")
    media_dispositions = _validate_media_review(
        review,
        material_id=material_id,
        source_sha256=source_sha,
        media_atoms=media_atoms,
        source_ids=set(source_by_id),
        review_task=_read_json(args.review_task.resolve(), "media review task"),
    )
    _copy_file(args.review.resolve(), output / "reviews/spec03_media_review.json")
    _copy_file(args.review_task.resolve(), output / "reviews/spec03_media_review_task.json")
    review_hash = _sha256(output / "reviews/spec03_media_review.json")
    asset_inventory = _read_json(
        parent / "source/media_asset_inventory.json",
        "media asset inventory",
    )
    inventory_assets = asset_inventory.get("assets")
    if not isinstance(inventory_assets, list):
        _fail("media_asset_inventory_invalid", "media asset inventory has no assets")
    inventory_by_member: dict[tuple[str, str], Mapping[str, Any]] = {}
    for raw in inventory_assets:
        if not isinstance(raw, Mapping):
            _fail("media_asset_inventory_invalid", "media asset entry must be an object")
        provider = _require_text(raw.get("provider"), "media asset provider")
        member = _safe_relative(raw.get("source_member"), "media asset source_member")
        key = (provider, member)
        if key in inventory_by_member:
            _fail("media_asset_inventory_invalid", f"duplicate media asset {key!r}")
        _require_sha(raw.get("sha256"), f"{provider}:{member}.sha256")
        _require_positive_int(raw.get("size_bytes"), f"{provider}:{member}.size_bytes")
        inventory_by_member[key] = raw
    selected_mineru_assets: list[Mapping[str, Any]] = []
    for disposition in media_dispositions:
        selected = disposition["selected_candidate"]
        if selected and selected.get("representation_type") == "source_asset_image":
            provider = _require_text(
                selected.get("archive_provider"),
                f"{disposition['media_id']}.archive_provider",
            )
            member = _safe_relative(
                selected.get("archive_member"),
                f"{disposition['media_id']}.archive_member",
            )
            inventory = inventory_by_member.get((provider, member))
            if inventory is None:
                _fail("media_asset_unresolved", f"{disposition['media_id']} asset is unresolved")
            if (
                selected.get("sha256") != inventory.get("sha256")
                or selected.get("size_bytes") != inventory.get("size_bytes")
                or selected.get("archive") != inventory.get("archive")
            ):
                _fail("media_asset_drift", f"{disposition['media_id']} asset hash differs")
            if provider != "mineru":
                _fail("media_asset_unresolved", "current media atoms may only select MinerU assets")
            selected_mineru_assets.append(selected)
    archive_inputs = contract["inputs"]
    materialized_selected = _materialize_selected_members(
        args.mineru_archive.resolve(),
        archive_identity=archive_inputs["mineru_archive"],
        selections=selected_mineru_assets,
        output=output,
    )
    for disposition in media_dispositions:
        selected = disposition["selected_candidate"]
        if selected and selected.get("representation_type") == "source_asset_image":
            member = str(selected["archive_member"])
            disposition["selected_candidate"] = {
                **dict(selected),
                "materialized_evidence": materialized_selected[member],
            }

    verified_by_media: dict[str, dict[str, Any]] = {}
    source_region_reviews: list[dict[str, Any]] = []
    for disposition in media_dispositions:
        selected = disposition["selected_candidate"]
        if not isinstance(selected, Mapping):
            _fail(
                "media_candidate_not_formally_executable",
                f"{disposition['media_id']} has no selected executable candidate",
            )
        if selected.get("representation_type") == "source_region_image":
            verified = _materialized_source_region_candidate(
                selected,
                source_pdf=args.source_pdf.resolve(),
                output=output,
                page_count=identity["page_count"],
            )
            source_region_reviews.append(
                {
                    "media_id": disposition["media_id"],
                    "selected_candidate_id": disposition["selected_candidate_id"],
                    "source_page": verified["source_page"],
                    "bbox": verified["bbox"],
                    "bbox_coordinate_space": verified["bbox_coordinate_space"],
                    "crop_path": verified["crop_path"],
                    "crop_sha256": verified["artifact_sha256"],
                    "crop_size_bytes": verified["size_bytes"],
                    "review_status": "pending",
                    "required_action": (
                        "Inspect this exact hash-bound crop against the source PDF "
                        "and record an explicit accept or reject decision."
                    ),
                }
            )
        else:
            verified = _verified_selected_media_candidate(
                selected,
                output=output,
            )
        verified_by_media[str(disposition["media_id"])] = verified
    open_review_count = len(source_region_reviews)
    candidate_spec_status = "needs_review" if open_review_count else "passed"
    media_review_queue = {
        "schema_version": "spec03-source-region-review-queue/1.0",
        "material_id": material_id,
        "source_pdf_sha256": source_sha,
        "spec_status": candidate_spec_status,
        "open_reviews": open_review_count,
        "items": source_region_reviews,
    }
    media_review_queue["payload_hash"] = _contract_payload_hash(media_review_queue)
    media_review_queue_hash = _write_json(
        output / "media/media_review_queue.json",
        media_review_queue,
    )

    decision_event = {
        "schema_version": "decision-event/1.0",
        "decision_id": args.stage_decision_id,
        "rule_id": "CV-H01..CV-H07",
        "status": "open" if open_review_count else "closed",
        "decision": (
            "Freeze the exact Popo source-unit dispositions and every MinerU media "
            "representation decision at source_reconciled checkpoint; source-region "
            "crops remain open until their exact artifact hashes are reviewed."
        ),
        "evidence_refs": [
            "reviews/spec03_media_review.json",
            "ledgers/source_scope_ledger.json",
            "ledgers/reading_order_ledger.json",
            "media/media_review_queue.json",
        ],
    }
    decision_hash = _write_jsonl(
        output / "decisions/evidence_decisions.jsonl",
        [decision_event],
    )
    inherited = list(parent_index.get("decisions") or [])
    child_index = {
        "schema_version": DECISION_INDEX_SCHEMA,
        "decision_index_id": parent_index["decision_index_id"],
        "snapshot_id": args.decision_snapshot_id,
        "version": int(parent_index["version"]) + 1,
        "parent_index_ref": "decisions/scope_order_decision_index.json",
        "parent_index_hash": parent_index_hash,
        "acyclic_commit_rule": "parent_D2_then_media_review_E3_then_child_D3_then_source_reconciled_ledger_L3",
        "evidence_committed_before_index": [
            {
                "ref": "reviews/spec03_media_review_task.json",
                "sha256": _sha256(output / "reviews/spec03_media_review_task.json"),
            },
            {"ref": "reviews/spec03_media_review.json", "sha256": review_hash},
            {
                "ref": "media/media_review_queue.json",
                "sha256": media_review_queue_hash,
            },
            {
                "ref": "ledgers/source_scope_ledger.json",
                "sha256": _sha256(output / "ledgers/source_scope_ledger.json"),
            },
            {
                "ref": "ledgers/reading_order_ledger.json",
                "sha256": _sha256(output / "ledgers/reading_order_ledger.json"),
            },
        ],
        "decision_event_files": [
            {
                "path": "decisions/input_decisions.jsonl",
                "sha256": _sha256(output / "decisions/input_decisions.jsonl"),
            },
            {
                "path": "decisions/scope_order_decisions.jsonl",
                "sha256": _sha256(output / "decisions/scope_order_decisions.jsonl"),
            },
            {
                "path": "decisions/evidence_decisions.jsonl",
                "sha256": decision_hash,
            },
        ],
        "decisions": [
            *inherited,
            {
                "decision_id": args.stage_decision_id,
                "status": "open" if open_review_count else "closed",
                "rule_id": "CV-H01..CV-H07",
                "event_file": "decisions/evidence_decisions.jsonl",
            },
        ],
        "summary": {
            "total": len(inherited) + 1,
            "closed": len(inherited) + (0 if open_review_count else 1),
            "open": 1 if open_review_count else 0,
            "stale": 0,
            "invalidated": 0,
        },
        "spec_status": candidate_spec_status,
    }
    decision_index_hash = _write_json(
        output / "decisions/canonical_decision_index.json",
        child_index,
    )

    block_ids_by_source = {
        source_id: _block_id(material_id, source_id)
        for source_id in source_by_id
    }
    base_media_atoms: list[dict[str, Any]] = []
    representation_rows: list[dict[str, Any]] = []
    for disposition in media_dispositions:
        atom = next(
            item
            for item in media_atoms
            if item["media_id"] == disposition["media_id"]
        )
        verified_candidate = verified_by_media[str(disposition["media_id"])]
        source_block_ids = [
            block_ids_by_source[source_id]
            for source_id in disposition["source_ids"]
        ]
        if not source_block_ids:
            _fail(
                "media_source_ownership_unresolved",
                f"{disposition['media_id']} has no canonical source blocks",
            )
        representation_id = f"representation::{disposition['media_id']}"
        review_status = (
            "needs_review"
            if verified_candidate["status"] == "needs_review"
            else "closed"
        )
        representation = {
            "representation_id": representation_id,
            "media_id": disposition["media_id"],
            "source_block_ids": source_block_ids,
            "status": review_status,
            "selected_candidate_id": disposition["selected_candidate_id"],
            "representation_type": verified_candidate[
                "representation_type"
            ],
            "artifact_sha256": verified_candidate["artifact_sha256"],
            "rule_id": "MEDIA-EXPLICIT-REVIEW-SELECTION",
            "reason": "the compact Spec 03 review selected exact hash-verified source evidence",
            "decision_refs": [args.stage_decision_id],
            "disposition": disposition["disposition"],
        }
        representation_rows.append(representation)
        base_media_atoms.append(
            {
                "media_id": disposition["media_id"],
                "source_atom_id": atom["source_atom_id"],
                "source_block_ids": source_block_ids,
                "source_page": atom["physical_page"],
                "bbox": atom["bbox"],
                "bbox_coordinate_space": atom["bbox_basis"],
                "media_kind": atom["media_kind"],
                "inclusion_status": "included",
                "candidates": [verified_candidate],
                "review_status": review_status,
                "source_ids": list(disposition["source_ids"]),
                "evidence_refs": list(disposition["evidence_refs"]),
            }
        )

    precommit_evidence = {
        "schema_version": "media-evidence-ledger/1.0",
        "ledger_id": f"media::{args.ledger_id}",
        "source_pdf": {
            "path": "external/source.pdf",
            "sha256": source_sha,
            "page_count": identity["page_count"],
        },
        "atoms": base_media_atoms,
        "summary": {
            "atoms": len(base_media_atoms),
            "included": len(base_media_atoms),
            "excluded": 0,
            "needs_review": open_review_count,
        },
    }
    precommit_evidence["payload_hash"] = _contract_payload_hash(
        precommit_evidence
    )
    precommit_evidence_path = (
        output / "media/precommit/media_evidence_ledger.json"
    )
    precommit_evidence_hash = _write_json(
        precommit_evidence_path,
        precommit_evidence,
    )
    precommit_plan = {
        "schema_version": "media-representation-plan/1.0",
        "media_evidence_ledger_sha256": precommit_evidence_hash,
        "spec_status": candidate_spec_status,
        "open_reviews": open_review_count,
        "representations": representation_rows,
        "summary": {
            "representations": len(representation_rows),
            "closed": len(representation_rows) - open_review_count,
            "excluded": 0,
            "needs_review": open_review_count,
        },
    }
    precommit_plan["payload_hash"] = _contract_payload_hash(precommit_plan)
    precommit_plan_path = (
        output / "media/precommit/media_representation_plan.json"
    )
    precommit_plan_hash = _write_json(
        precommit_plan_path,
        precommit_plan,
    )
    precommit_validation = {
        "schema_version": "media-representation-validation/1.0",
        "status": candidate_spec_status,
        "inputs": {
            "media_evidence_ledger": {
                "path": "media/precommit/media_evidence_ledger.json",
                "sha256": precommit_evidence_hash,
            },
            "media_representation_plan": {
                "path": "media/precommit/media_representation_plan.json",
                "sha256": precommit_plan_hash,
            },
        },
        "checks": [
            {
                "check_id": "MSR-H01-ledger-identities",
                "status": "passed",
            },
            {
                "check_id": "MSR-H03-candidate-evidence-live",
                "status": "passed",
            },
            {
                "check_id": "MSR-H04-representation-coverage-and-closure",
                "status": candidate_spec_status,
            },
        ],
    }
    precommit_validation_hash = _write_json(
        output / "reports/precommit_media_validation.json",
        precommit_validation,
    )
    canonical_media_atoms: list[dict[str, Any]] = []
    media_by_source: dict[str, list[dict[str, Any]]] = {}
    reps_by_media = {
        row["media_id"]: row for row in representation_rows
    }
    for atom in base_media_atoms:
        canonical_atom = {
            **atom,
            "media_contract_schema_version": "canonical-media-atom/1.1",
            "frozen_representation": reps_by_media[atom["media_id"]],
            "contract_decision_refs": [args.stage_decision_id],
            "precommit_evidence": {
                "media_evidence_ledger_sha256": precommit_evidence_hash,
                "media_representation_plan_sha256": precommit_plan_hash,
                "media_validation_sha256": precommit_validation_hash,
            },
        }
        canonical_media_atoms.append(canonical_atom)
        for source_id in atom["source_ids"]:
            media_by_source.setdefault(source_id, []).append(canonical_atom)

    blocks: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    for source_id, source_unit in source_by_id.items():
        scope_unit = scope_by_id[source_id]
        status = scope_unit["scope_status"]
        block_id = block_ids_by_source[source_id]
        block_media = media_by_source.get(source_id, [])
        block_needs_review = any(
            item.get("review_status") == "needs_review"
            for item in block_media
        )
        block = {
            "record_type": "source_block",
            "block_id": block_id,
            "upstream_block_ref": {
                "popo_source_id": source_id,
                "popo_raw_id": source_unit.get("popo_raw_id"),
                "popo_run_id": source_unit["popo_run_id"],
            },
            "source_system": "minerupopo",
            "source_label": source_unit["source_label"],
            "source_type": source_unit["source_type"],
            "pdf_physical_page": source_unit["physical_page"],
            "upstream_page_idx": source_unit["upstream_page_idx"],
            "bbox": source_unit["bbox"],
            "bbox_basis": source_unit["bbox_basis"],
            "raw_content": source_unit["raw_content"],
            "raw_content_sha256": source_unit["raw_content_sha256"],
            "scope_status": status,
            "scope_reason": scope_unit["scope_reason"],
            "candidate_final_order": scope_unit["candidate_final_order"],
            "popo_tree_rank": source_unit.get("popo_tree_rank"),
            "tree_context": source_unit.get("tree_context"),
            "order_evidence": scope_unit["evidence_refs"] if status == "included" else [],
            "semantic_group_id": scope_unit.get("semantic_group_id"),
            "reading_relationship_refs": scope_unit.get("composite_relationship_ids") or [],
            "human_decision_refs": [
                args.stage_decision_id,
            ],
            "source_representation": {
                "authority": "source_pdf_visual_region",
                "physical_page": source_unit["physical_page"],
                "bbox": source_unit["bbox"],
                "bbox_coordinate_space": source_unit["bbox_basis"],
                "extracted_value_status": "candidate_transcription",
            },
            "media_contracts": block_media,
            "terminal_state": (
                "source_region_review_pending"
                if block_needs_review
                else "source_reconciled"
                if status == "included"
                else "scope_excluded"
            ),
            "review_required": block_needs_review,
        }
        blocks.append(block)
        coverage_rows.append(
            {
                "source_id": source_id,
                "block_id": block_id,
                "scope_status": status,
                "candidate_final_order": scope_unit["candidate_final_order"],
                "terminal_state": block["terminal_state"],
                "raw_content_sha256": block["raw_content_sha256"],
                "coverage_count": 1,
            }
        )
    blocks.sort(
        key=lambda item: (
            item["scope_status"] != "included",
            item["candidate_final_order"] or 10**18,
            item["pdf_physical_page"],
            item["block_id"],
        )
    )
    if len({item["block_id"] for item in blocks}) != len(source_units):
        _fail("canonical_block_identity_collision", "canonical block identities are not unique")
    current_ledger_hash = _canonical_hash(blocks)
    header = {
        "record_type": "ledger_header",
        "schema_version": CANONICAL_LEDGER_SCHEMA,
        "ledger_id": args.ledger_id,
        "ledger_snapshot_id": args.ledger_snapshot_id,
        "ledger_version": args.ledger_version,
        "parent_ledger_hash": None,
        "ledger_checkpoint": "source_reconciled",
        "current_ledger_hash": current_ledger_hash,
        "current_ledger_hash_scope": (
            "canonical JSON hash of ordered source_block records including native media_contracts"
        ),
        "spec_status": candidate_spec_status,
        "run_mode": "formal_candidate",
        "material_identity": {
            "material_id": material_id,
            "source_pdf_sha256": source_sha,
            "page_count": identity["page_count"],
            "mineru_run_id": identity["mineru_run_id"],
            "popo_run_id": identity["popo_run_id"],
        },
        "input_contract_ref": "contracts/input_contract.json",
        "input_contract_hash": _sha256(output / "contracts/input_contract.json"),
        "source_scope_ledger_ref": "ledgers/source_scope_ledger.json",
        "source_scope_ledger_hash": _sha256(output / "ledgers/source_scope_ledger.json"),
        "reading_order_ledger_ref": "ledgers/reading_order_ledger.json",
        "reading_order_ledger_hash": _sha256(output / "ledgers/reading_order_ledger.json"),
        "canonical_decision_index_ref": "decisions/canonical_decision_index.json",
        "canonical_decision_index_hash": decision_index_hash,
        "source_page_render_ledger_ref": "ledgers/source_page_render_ledger.jsonl",
        "source_page_render_ledger_hash": _sha256(
            output / "ledgers/source_page_render_ledger.jsonl"
        ),
        "summary": {
            "source_records": len(blocks),
            "included_atoms": sum(item["scope_status"] == "included" for item in blocks),
            "excluded_source_records": sum(item["scope_status"] == "excluded" for item in blocks),
            "media_atoms": len(representation_rows),
            "open_reviews": open_review_count,
        },
        "generated_by": KERNEL_VERSION,
    }
    ledger_hash = _write_jsonl(
        output / "ledgers/canonical_block_ledger.jsonl",
        [header, *blocks],
    )
    coverage_hash = _write_json(
        output / "ledgers/block_coverage_ledger.json",
        {
            "schema_version": "block-coverage-ledger/2.0",
            "canonical_ledger_ref": "ledgers/canonical_block_ledger.jsonl",
            "canonical_ledger_hash": ledger_hash,
            "source_units": sorted(coverage_rows, key=lambda item: item["source_id"]),
            "summary": {
                "source_units": len(source_units),
                "covered_exactly_once": len(coverage_rows),
                "missing": 0,
                "duplicates": 0,
            },
            "spec_status": "passed",
        },
    )
    media_evidence = {
        "schema_version": "media-evidence-ledger/1.1",
        "ledger_id": f"media::{args.ledger_id}",
        "canonical_ledger": {
            "path": "ledgers/canonical_block_ledger.jsonl",
            "sha256": ledger_hash,
            "snapshot_id": args.ledger_snapshot_id,
            "payload_hash": current_ledger_hash,
        },
        "decision_index": {
            "path": "decisions/canonical_decision_index.json",
            "sha256": decision_index_hash,
        },
        "source_pdf": {
            "path": "external/source.pdf",
            "sha256": source_sha,
            "page_count": identity["page_count"],
        },
        "atoms": canonical_media_atoms,
        "summary": {
            "atoms": len(canonical_media_atoms),
            "included": len(canonical_media_atoms),
            "excluded": 0,
            "needs_review": open_review_count,
        },
    }
    media_evidence["payload_hash"] = _contract_payload_hash(media_evidence)
    media_evidence_hash = _write_json(
        output / "media/media_evidence_ledger.json",
        media_evidence,
    )
    media_plan = {
        "schema_version": "media-representation-plan/1.1",
        "canonical_ledger_sha256": ledger_hash,
        "decision_index_sha256": decision_index_hash,
        "media_evidence_ledger_sha256": media_evidence_hash,
        "spec_status": candidate_spec_status,
        "open_reviews": open_review_count,
        "representations": representation_rows,
        "summary": {
            "representations": len(representation_rows),
            "closed": len(representation_rows) - open_review_count,
            "excluded": 0,
            "needs_review": open_review_count,
        },
    }
    media_plan["payload_hash"] = _contract_payload_hash(media_plan)
    media_plan_hash = _write_json(
        output / "media/media_representation_plan.json",
        media_plan,
    )
    media_ledger_hash = _write_json(
        output / "ledgers/media_ledger.json",
        {
            "schema_version": "media-ledger/2.0",
            "canonical_ledger_ref": "ledgers/canonical_block_ledger.jsonl",
            "canonical_ledger_hash": ledger_hash,
            "media_evidence_ledger_ref": "media/media_evidence_ledger.json",
            "media_evidence_ledger_hash": media_evidence_hash,
            "media_representation_plan_ref": "media/media_representation_plan.json",
            "media_representation_plan_hash": media_plan_hash,
            "asset_inventory_ref": "source/media_asset_inventory.json",
            "asset_inventory_hash": _sha256(output / "source/media_asset_inventory.json"),
            "summary": {
                "media_atoms": len(canonical_media_atoms),
                "closed": len(representation_rows) - open_review_count,
                "open": open_review_count,
            },
            "spec_status": candidate_spec_status,
        },
    )
    gates = {f"CV-H{index:02d}": "passed" for index in range(1, 8)}
    completeness = {
        "schema_version": "source-completeness-report/2.0",
        "checkpoint": "source_reconciled",
        "gate_status": gates,
        "source_units": len(source_units),
        "canonical_blocks": len(blocks),
        "included_units": sum(item["scope_status"] == "included" for item in blocks),
        "excluded_units": sum(item["scope_status"] == "excluded" for item in blocks),
        "media_atoms": len(media_atoms),
        "media_dispositions": len(media_dispositions),
        "missing_source_units": [],
        "duplicate_source_units": [],
        "open_reviews": [
            item["media_id"] for item in source_region_reviews
        ],
        "spec_status": candidate_spec_status,
    }
    completeness_hash = _write_json(
        output / "reports/source_completeness_report.json",
        completeness,
    )
    (output / "reports").mkdir(parents=True, exist_ok=True)
    (output / "reports/source_completeness_report.md").write_text(
        "\n".join(
            [
                "# Source completeness candidate",
                "",
                f"- Source units: {len(source_units)}",
                f"- Canonical blocks: {len(blocks)}",
                f"- MinerU media atoms: {len(media_atoms)}",
                (
                    "- Closed media dispositions: "
                    f"{len(media_dispositions) - open_review_count}"
                ),
                "- Missing source units: 0",
                "- Duplicate source coverage: 0",
                f"- Open source/media reviews: {open_review_count}",
                "",
                "This is producer evidence only; independent evaluation and promotion remain pending.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    ledger_manifest_hash = _write_json(
        output / "ledgers/ledger_manifest.json",
        {
            "schema_version": "ledger-manifest/2.0",
            "ledger_id": args.ledger_id,
            "ledger_version": args.ledger_version,
            "snapshot_id": args.ledger_snapshot_id,
            "artifact_path": "ledgers/canonical_block_ledger.jsonl",
            "artifact_sha256": ledger_hash,
            "payload_hash": current_ledger_hash,
            "decision_index_ref": "decisions/canonical_decision_index.json",
            "decision_index_hash": decision_index_hash,
            "spec_status": candidate_spec_status,
            "immutable_after_publication": True,
        },
    )
    source_commit_hash = _write_json(
        output / "manifests/source_reconciled_commit.json",
        {
            "schema_version": "source-reconciled-stage-manifest/2.0",
            "producer": KERNEL_VERSION,
            "checkpoint": "source_reconciled",
            "status": candidate_spec_status,
            "promotion_status": "not_evaluated",
            "canonical_ledger": {
                "path": "ledgers/canonical_block_ledger.jsonl",
                "sha256": ledger_hash,
                "payload_hash": current_ledger_hash,
            },
            "decision_index": {
                "path": "decisions/canonical_decision_index.json",
                "sha256": decision_index_hash,
            },
            "scope_limits": (
                "Source reconciliation only. No semantic mapping, render coverage, "
                "final PDF verification, acceptance, or promotion is claimed."
            ),
        },
    )
    stage_manifest_hash = _stage_manifest(
        output,
        stage="canonical_block_ledger",
        run_id=args.run_id,
        schema_path=args.contract_schema_path,
        schema_sha256=args.contract_schema_sha256,
        gate_status=gates,
        artifacts={
            "canonical_ledger": "ledgers/canonical_block_ledger.jsonl",
            "ledger_manifest": "ledgers/ledger_manifest.json",
            "block_coverage_ledger": "ledgers/block_coverage_ledger.json",
            "media_ledger": "ledgers/media_ledger.json",
            "media_evidence_ledger": "media/media_evidence_ledger.json",
            "media_representation_plan": "media/media_representation_plan.json",
            "media_review_queue": "media/media_review_queue.json",
            "decision_index": "decisions/canonical_decision_index.json",
            "source_completeness_report": "reports/source_completeness_report.json",
            "source_reconciled_commit": "manifests/source_reconciled_commit.json",
        },
        metrics={
            "source_units": len(source_units),
            "canonical_blocks": len(blocks),
            "media_atoms": len(media_atoms),
            "ledger_sha256": ledger_hash,
            "ledger_manifest_sha256": ledger_manifest_hash,
            "coverage_sha256": coverage_hash,
            "media_ledger_sha256": media_ledger_hash,
            "completeness_sha256": completeness_hash,
            "source_commit_sha256": source_commit_hash,
            "review_task_canonical_sha256": review_task_hash,
            "media_review_queue_sha256": media_review_queue_hash,
            "open_media_reviews": open_review_count,
        },
    )
    _run_manifest(
        output,
        job_id=args.job_id,
        run_id=args.run_id,
        stage="canonical_block_ledger",
        stage_manifest_hash=stage_manifest_hash,
    )
    return {
        "candidate_status": "complete",
        "spec_status": "not_evaluated",
        "source_units": len(source_units),
        "media_atoms": len(media_atoms),
        "ledger_sha256": ledger_hash,
    }


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--decision-snapshot-id", required=True)
    parser.add_argument("--stage-decision-id", required=True)
    parser.add_argument("--contract-schema-path", required=True)
    parser.add_argument("--contract-schema-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    intake = subparsers.add_parser("intake")
    _add_common(intake)
    intake.add_argument("--decision-index-id", required=True)
    intake.add_argument("--source-pdf", type=Path, required=True)
    intake.add_argument("--mineru-manifest", type=Path, required=True)
    intake.add_argument("--mineru-marker", type=Path, required=True)
    intake.add_argument("--mineru-archive", type=Path, required=True)
    intake.add_argument("--popo-manifest", type=Path, required=True)
    intake.add_argument("--popo-marker", type=Path, required=True)
    intake.add_argument("--popo-archive", type=Path, required=True)
    intake.add_argument("--template-archive", type=Path, required=True)
    intake.add_argument("--release-manifest", type=Path, required=True)
    intake.set_defaults(producer=produce_intake)

    scope = subparsers.add_parser("scope")
    _add_common(scope)
    scope.add_argument("--parent", type=Path, required=True)
    scope.add_argument("--parent-promotion", type=Path, required=True)
    scope.add_argument("--source-pdf", type=Path, required=True)
    scope.add_argument("--mineru-archive", type=Path, required=True)
    scope.add_argument("--popo-archive", type=Path, required=True)
    scope.add_argument("--template-archive", type=Path, required=True)
    scope.add_argument("--review-task", type=Path, required=True)
    scope.add_argument("--review", type=Path, required=True)
    scope.set_defaults(producer=produce_scope)

    ledger = subparsers.add_parser("ledger")
    _add_common(ledger)
    ledger.add_argument("--parent", type=Path, required=True)
    ledger.add_argument("--parent-promotion", type=Path, required=True)
    ledger.add_argument("--source-pdf", type=Path, required=True)
    ledger.add_argument("--mineru-archive", type=Path, required=True)
    ledger.add_argument("--popo-archive", type=Path, required=True)
    ledger.add_argument("--template-archive", type=Path, required=True)
    ledger.add_argument("--review-task", type=Path, required=True)
    ledger.add_argument("--review", type=Path, required=True)
    ledger.add_argument("--ledger-id", required=True)
    ledger.add_argument("--ledger-snapshot-id", required=True)
    ledger.add_argument("--ledger-version", type=int, required=True)
    ledger.set_defaults(producer=produce_ledger)

    prepare_scope = subparsers.add_parser("prepare-scope-review-task")
    prepare_scope.add_argument("--parent", type=Path, required=True)
    prepare_scope.add_argument("--output", type=Path, required=True)
    prepare_scope.set_defaults(producer=prepare_scope_review_task)

    prepare_media = subparsers.add_parser("prepare-media-review-task")
    prepare_media.add_argument("--parent", type=Path, required=True)
    prepare_media.add_argument("--output", type=Path, required=True)
    prepare_media.set_defaults(producer=prepare_media_review_task)

    prepare_outline = subparsers.add_parser("prepare-outline-review-task")
    prepare_outline.add_argument("--parent", type=Path, required=True)
    prepare_outline.add_argument("--source-pdf", type=Path, required=True)
    prepare_outline.add_argument("--source-pdf-ref", required=True)
    prepare_outline.add_argument("--parent-promotion", type=Path, required=True)
    prepare_outline.add_argument("--output", type=Path, required=True)
    prepare_outline.set_defaults(producer=prepare_outline_review_task)

    project_outline = subparsers.add_parser("project-outline-review")
    project_outline.add_argument("--task", type=Path, required=True)
    project_outline.add_argument("--compact-review", type=Path, required=True)
    project_outline.add_argument("--output", type=Path, required=True)
    project_outline.set_defaults(producer=project_outline_review)

    prepare_semantic = subparsers.add_parser("prepare-semantic-review-task")
    prepare_semantic.add_argument("--parent", type=Path, required=True)
    prepare_semantic.add_argument("--source-pdf", type=Path, required=True)
    prepare_semantic.add_argument("--source-pdf-ref", required=True)
    prepare_semantic.add_argument("--parent-promotion", type=Path, required=True)
    prepare_semantic.add_argument("--output", type=Path, required=True)
    prepare_semantic.set_defaults(producer=prepare_semantic_review_task)

    project_semantic = subparsers.add_parser("project-semantic-review")
    project_semantic.add_argument("--task", type=Path, required=True)
    project_semantic.add_argument("--compact-review", type=Path, required=True)
    project_semantic.add_argument("--output", type=Path, required=True)
    project_semantic.set_defaults(producer=project_semantic_review)
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    try:
        if hasattr(args, "contract_schema_sha256"):
            _require_sha(args.contract_schema_sha256, "contract_schema_sha256")
        result = args.producer(args)
    except KernelContractError as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": exc.code,
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=os.sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
