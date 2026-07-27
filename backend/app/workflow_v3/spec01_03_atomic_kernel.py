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


KERNEL_VERSION = "luceon-worker-v3-spec01-03-atomic/1.1.0"
INTAKE_SCHEMA = "luceon.worker-v3-spec01-intake-contract/v1"
SCOPE_REVIEW_SCHEMA = "luceon.worker-v3-spec02-scope-order-review/v1"
MEDIA_REVIEW_SCHEMA = "luceon.worker-v3-spec03-media-review/v1"
SCOPE_REVIEW_TASK_SCHEMA = "luceon.worker-v3-spec02-review-task/v1"
MEDIA_REVIEW_TASK_SCHEMA = "luceon.worker-v3-spec03-review-task/v1"
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
            if image_ref:
                basename = PurePosixPath(image_ref.replace("\\", "/")).name
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
        if _require_positive_int(row.get("size_bytes"), f"{relative}.size_bytes") != path.stat().st_size:
            _fail("parent_materialization_drift", f"{relative} size drifted")
    return contract


def _scope_review_task(parent: Path) -> dict[str, Any]:
    contract = _verify_materialized_parent(parent)
    identity = contract["material_identity"]
    source_units = _read_jsonl(parent / "source/popo_source_units.jsonl", "Popo source units")
    media_atoms = _read_jsonl(parent / "source/mineru_media_atoms.jsonl", "MinerU media atoms")
    geometry = _read_json(parent / "evidence/pdf_page_geometry.json", "PDF geometry")
    pages_by_number = {
        int(item["physical_page"]): {
            "physical_page": int(item["physical_page"]),
            "geometry": item,
            "source_units": [],
            "mineru_media_atoms": [],
        }
        for item in geometry.get("pages") or []
        if isinstance(item, Mapping)
    }
    expected_pages = set(range(1, int(identity["page_count"]) + 1))
    if set(pages_by_number) != expected_pages:
        _fail("review_task_geometry_invalid", "PDF geometry does not cover every page")
    for unit in source_units:
        page = int(unit["physical_page"])
        if page not in pages_by_number:
            _fail("review_task_source_invalid", f"source unit references unknown page {page}")
        pages_by_number[page]["source_units"].append(
            {
                "source_id": unit["source_id"],
                "source_type": unit["source_type"],
                "source_label": unit["source_label"],
                "bbox": unit["bbox"],
                "bbox_basis": unit["bbox_basis"],
                "raw_content": unit["raw_content"],
                "raw_content_sha256": unit["raw_content_sha256"],
                "popo_tree_rank": unit.get("popo_tree_rank"),
                "tree_context": unit.get("tree_context"),
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
                "bbox_basis": atom["bbox_basis"],
                "candidate_ids": [
                    item["candidate_id"]
                    for item in atom.get("candidates") or []
                    if isinstance(item, Mapping) and item.get("candidate_id")
                ],
            }
        )
    for page in pages_by_number.values():
        page["source_units"].sort(
            key=lambda item: (
                item["popo_tree_rank"] is None,
                item["popo_tree_rank"] or 0,
                item["source_id"],
            )
        )
        page["mineru_media_atoms"].sort(key=lambda item: item["media_id"])
    task = {
        "schema_version": SCOPE_REVIEW_TASK_SCHEMA,
        "stage_key": "source_scope_and_order",
        "material_id": identity["material_id"],
        "source_pdf_sha256": identity["source_pdf_sha256"],
        "page_count": identity["page_count"],
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
            "Classify every enumerated Popo source_id exactly once.",
            "Assign explicit contiguous final_order 1..N to all and only included units.",
            "Do not use source array order as final reading order.",
            "Close complex, multi-column, cross-page, and composite relationships with evidence.",
            "Do not infer scope from filename, title keywords, language, or sample identity.",
        ],
        "pages": [pages_by_number[index] for index in sorted(pages_by_number)],
    }
    task["task_id"] = "scope-review-" + _canonical_hash(task)[:24]
    return task


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
        "source_units": sum(len(page["source_units"]) for page in task["pages"]),
    }


def _media_review_task(parent: Path) -> dict[str, Any]:
    contract = _verify_materialized_parent(parent)
    identity = contract["material_identity"]
    media_atoms = _read_jsonl(parent / "source/mineru_media_atoms.jsonl", "MinerU media atoms")
    scope = _read_json(parent / "ledgers/source_scope_ledger.json", "source scope ledger")
    if scope.get("spec_status") != "passed":
        _fail("review_task_scope_invalid", "Spec 02 scope ledger is not closed")
    source_units = scope.get("source_units")
    if not isinstance(source_units, list):
        _fail("review_task_scope_invalid", "Spec 02 scope ledger has no source units")
    source_by_page: dict[int, list[dict[str, Any]]] = {}
    for unit in source_units:
        if not isinstance(unit, Mapping):
            _fail("review_task_scope_invalid", "scope source unit must be an object")
        source_by_page.setdefault(int(unit["physical_page"]), []).append(
            {
                "source_id": unit["source_id"],
                "scope_status": unit["scope_status"],
                "candidate_final_order": unit["candidate_final_order"],
            }
        )
    task_atoms = []
    for atom in sorted(media_atoms, key=lambda item: item["media_id"]):
        task_atoms.append(
            {
                "media_id": atom["media_id"],
                "source_atom_id": atom["source_atom_id"],
                "physical_page": atom["physical_page"],
                "bbox": atom["bbox"],
                "bbox_basis": atom["bbox_basis"],
                "media_kind": atom["media_kind"],
                "raw_content_sha256": atom["raw_content_sha256"],
                "source_units_on_page": sorted(
                    source_by_page.get(int(atom["physical_page"]), []),
                    key=lambda item: (
                        item["candidate_final_order"] is None,
                        item["candidate_final_order"] or 0,
                        item["source_id"],
                    ),
                ),
                "candidates": atom.get("candidates") or [],
            }
        )
    task = {
        "schema_version": MEDIA_REVIEW_TASK_SCHEMA,
        "stage_key": "canonical_block_ledger",
        "material_id": identity["material_id"],
        "source_pdf_sha256": identity["source_pdf_sha256"],
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
            "Disposition every enumerated media_id exactly once.",
            "Select only one candidate_id enumerated for that media atom.",
            "Bind zero or more exact source_ids enumerated on the same source page.",
            "An exclusion requires source evidence and cannot discard instructional content.",
            "Do not invent replacement teaching content or an unenumerated asset.",
        ],
        "media_atoms": task_atoms,
    }
    task["task_id"] = "media-review-" + _canonical_hash(task)[:24]
    return task


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
    page_count: int,
    source_units: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    expected_fields = {
        "schema_version",
        "review_id",
        "material_id",
        "source_pdf_sha256",
        "review_status",
        "pages",
        "relationships",
        "open_reviews",
    }
    if set(review) != expected_fields or review.get("schema_version") != SCOPE_REVIEW_SCHEMA:
        _fail("scope_review_shape_invalid", "scope/order review has missing or unknown fields")
    if review.get("material_id") != material_id or review.get("source_pdf_sha256") != source_sha256:
        _fail("scope_review_identity_mismatch", "scope/order review names another source")
    if review.get("review_status") != "closed" or review.get("open_reviews") != []:
        _fail("scope_review_open", "scope/order review is not closed")
    pages = review.get("pages")
    if not isinstance(pages, list) or len(pages) != page_count:
        _fail("scope_page_partition_invalid", "review must classify every physical page")
    source_by_page: dict[int, set[str]] = {}
    for unit in source_units:
        source_by_page.setdefault(int(unit["physical_page"]), set()).add(str(unit["source_id"]))
    normalized_units: list[dict[str, Any]] = []
    normalized_pages: list[dict[str, Any]] = []
    seen_pages: set[int] = set()
    seen_units: set[str] = set()
    included_orders: list[int] = []
    for raw_page in pages:
        if not isinstance(raw_page, Mapping):
            _fail("scope_page_partition_invalid", "review page must be an object")
        required = {
            "physical_page",
            "scope_status",
            "page_category",
            "reason",
            "complexity_flags",
            "cross_page_relationship_ids",
            "evidence_refs",
            "review_status",
            "units",
        }
        if set(raw_page) != required:
            _fail("scope_page_shape_invalid", "review page has missing or unknown fields")
        page_number = _require_positive_int(raw_page.get("physical_page"), "physical_page")
        if page_number > page_count or page_number in seen_pages:
            _fail("scope_page_partition_invalid", f"page {page_number} is invalid or duplicated")
        seen_pages.add(page_number)
        if raw_page.get("scope_status") not in {"included", "excluded"}:
            _fail("scope_status_invalid", f"page {page_number} has invalid scope status")
        _require_text(raw_page.get("page_category"), f"page {page_number}.page_category")
        _require_text(raw_page.get("reason"), f"page {page_number}.reason")
        if raw_page.get("review_status") != "closed":
            _fail("scope_review_open", f"page {page_number} is not closed")
        evidence_refs = raw_page.get("evidence_refs")
        flags = raw_page.get("complexity_flags")
        if (
            not isinstance(evidence_refs, list)
            or any(not isinstance(item, str) or not item for item in evidence_refs)
            or not isinstance(flags, list)
            or any(not isinstance(item, str) or not item for item in flags)
        ):
            _fail("scope_evidence_invalid", f"page {page_number} review evidence is invalid")
        if flags and not evidence_refs:
            _fail("complex_page_evidence_missing", f"page {page_number} is complex without evidence")
        units = raw_page.get("units")
        if not isinstance(units, list):
            _fail("scope_unit_partition_invalid", f"page {page_number}.units must be an array")
        local_seen: set[str] = set()
        for raw_unit in units:
            if not isinstance(raw_unit, Mapping):
                _fail("scope_unit_partition_invalid", "review unit must be an object")
            fields = {
                "source_id",
                "scope_status",
                "reason",
                "final_order",
                "semantic_group_id",
                "composite_relationship_ids",
                "evidence_refs",
                "review_status",
            }
            if set(raw_unit) != fields:
                _fail("scope_unit_shape_invalid", "review unit has missing or unknown fields")
            source_id = _require_text(raw_unit.get("source_id"), "review unit source_id")
            if source_id in local_seen or source_id in seen_units:
                _fail("scope_unit_partition_invalid", f"source unit {source_id!r} is duplicated")
            if source_id not in source_by_page.get(page_number, set()):
                _fail("scope_unit_partition_invalid", f"source unit {source_id!r} is on another page")
            local_seen.add(source_id)
            seen_units.add(source_id)
            status = raw_unit.get("scope_status")
            if status not in {"included", "excluded"}:
                _fail("scope_status_invalid", f"source unit {source_id!r} status is invalid")
            reason = _require_text(raw_unit.get("reason"), f"{source_id}.reason")
            unit_evidence = raw_unit.get("evidence_refs")
            if (
                not isinstance(unit_evidence, list)
                or not unit_evidence
                or any(not isinstance(item, str) or not item for item in unit_evidence)
            ):
                _fail("scope_evidence_invalid", f"{source_id} lacks evidence")
            if raw_unit.get("review_status") != "closed":
                _fail("scope_review_open", f"source unit {source_id!r} is not closed")
            final_order = raw_unit.get("final_order")
            if status == "included":
                final_order = _require_positive_int(final_order, f"{source_id}.final_order")
                included_orders.append(final_order)
            elif final_order != 0:
                _fail(
                    "excluded_unit_ordered",
                    f"excluded source unit {source_id!r} must use final_order 0",
                )
            else:
                final_order = None
            if raw_page.get("scope_status") == "excluded" and status != "excluded":
                _fail("excluded_page_contains_body", f"excluded page {page_number} includes a source unit")
            composite_refs = raw_unit.get("composite_relationship_ids")
            if not isinstance(composite_refs, list) or any(
                not isinstance(item, str) or not item for item in composite_refs
            ):
                _fail("scope_relationship_invalid", f"{source_id} relationship refs are invalid")
            semantic_group_id = raw_unit.get("semantic_group_id")
            if not isinstance(semantic_group_id, str):
                _fail("scope_relationship_invalid", f"{source_id} semantic_group_id is invalid")
            semantic_group_id = semantic_group_id or None
            normalized_units.append(
                {
                    "source_id": source_id,
                    "physical_page": page_number,
                    "scope_status": status,
                    "scope_reason": reason,
                    "candidate_final_order": final_order,
                    "semantic_group_id": semantic_group_id,
                    "composite_relationship_ids": list(composite_refs),
                    "evidence_refs": list(unit_evidence),
                    "review_status": "closed",
                }
            )
        if local_seen != source_by_page.get(page_number, set()):
            missing = sorted(source_by_page.get(page_number, set()) - local_seen)
            _fail("scope_unit_partition_invalid", f"page {page_number} omits units {missing[:10]}")
        normalized_pages.append(
            {
                "physical_page": page_number,
                "scope_status": raw_page["scope_status"],
                "page_category": raw_page["page_category"],
                "reason": raw_page["reason"],
                "complexity_flags": list(flags),
                "cross_page_relationship_ids": list(raw_page["cross_page_relationship_ids"]),
                "evidence_refs": list(evidence_refs),
                "review_status": "closed",
            }
        )
    if seen_pages != set(range(1, page_count + 1)):
        _fail("scope_page_partition_invalid", "review page partition is not exact")
    if seen_units != {str(item["source_id"]) for item in source_units}:
        _fail("scope_unit_partition_invalid", "review source-unit partition is not exact")
    if sorted(included_orders) != list(range(1, len(included_orders) + 1)):
        _fail("reading_order_not_contiguous", "included final_order must be exactly 1..N")

    relationships = review.get("relationships")
    if not isinstance(relationships, list):
        _fail("scope_relationship_invalid", "relationships must be an array")
    normalized_relationships: list[dict[str, Any]] = []
    relationship_ids: set[str] = set()
    units_by_id = {item["source_id"]: item for item in normalized_units}
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
        normalized_relationships.append(dict(raw))
    referenced = {
        relationship_id
        for unit in normalized_units
        for relationship_id in unit["composite_relationship_ids"]
    }
    if referenced - relationship_ids:
        _fail("scope_relationship_invalid", "source units reference unknown relationships")
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
    review_task_hash = _verify_review_task(
        args.review_task.resolve(),
        _scope_review_task(parent),
        "scope review task",
    )
    review = _read_json(args.review.resolve(), "scope/order review")
    pages, units, relationships = _validate_scope_review(
        review,
        material_id=material_id,
        source_sha256=source_sha,
        page_count=page_count,
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
        "decision": "Freeze the explicit all-page and all-source-unit scope/order review; no flat-array order is inferred.",
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
) -> list[dict[str, Any]]:
    expected_fields = {
        "schema_version",
        "review_id",
        "material_id",
        "source_pdf_sha256",
        "review_status",
        "media",
        "open_reviews",
    }
    if set(review) != expected_fields or review.get("schema_version") != MEDIA_REVIEW_SCHEMA:
        _fail("media_review_shape_invalid", "media review has missing or unknown fields")
    if review.get("material_id") != material_id or review.get("source_pdf_sha256") != source_sha256:
        _fail("media_review_identity_mismatch", "media review names another source")
    if review.get("review_status") != "closed" or review.get("open_reviews") != []:
        _fail("media_review_open", "media review is not closed")
    rows = review.get("media")
    if not isinstance(rows, list):
        _fail("media_review_shape_invalid", "media must be an array")
    atoms = {str(item["media_id"]): item for item in media_atoms}
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            _fail("media_review_shape_invalid", "media disposition must be an object")
        fields = {
            "media_id",
            "disposition",
            "selected_candidate_id",
            "source_ids",
            "evidence_refs",
            "review_status",
        }
        if set(raw) != fields:
            _fail("media_review_shape_invalid", "media disposition has missing or unknown fields")
        media_id = _require_text(raw.get("media_id"), "media_id")
        if media_id in seen or media_id not in atoms:
            _fail("media_partition_invalid", f"media_id {media_id!r} is unknown or duplicated")
        seen.add(media_id)
        disposition = raw.get("disposition")
        if disposition not in {
            "source_asset",
            "source_region",
            "structured_transcription",
            "excluded_noninstructional",
        }:
            _fail("media_disposition_invalid", f"{media_id} disposition is invalid")
        if raw.get("review_status") != "closed":
            _fail("media_review_open", f"{media_id} is not closed")
        evidence = raw.get("evidence_refs")
        if not isinstance(evidence, list) or not evidence:
            _fail("media_evidence_missing", f"{media_id} lacks review evidence")
        linked_source_ids = raw.get("source_ids")
        if (
            not isinstance(linked_source_ids, list)
            or len(linked_source_ids) != len(set(linked_source_ids))
            or any(item not in source_ids for item in linked_source_ids)
        ):
            _fail("media_source_binding_invalid", f"{media_id} source bindings are invalid")
        selected = raw.get("selected_candidate_id")
        candidates = {
            str(item["candidate_id"]): item
            for item in atoms[media_id].get("candidates") or []
            if isinstance(item, Mapping) and item.get("candidate_id")
        }
        if disposition == "excluded_noninstructional":
            if selected != "":
                _fail("media_disposition_invalid", f"{media_id} excluded media selects a candidate")
            selected_candidate = None
            selected = None
        else:
            if not isinstance(selected, str) or selected not in candidates:
                _fail("media_candidate_invalid", f"{media_id} selects an unknown candidate")
            selected_candidate = candidates[selected]
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
    if seen != set(atoms):
        missing = sorted(set(atoms) - seen)
        _fail("media_partition_invalid", f"media review omits {missing[:10]}")
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

    decision_event = {
        "schema_version": "decision-event/1.0",
        "decision_id": args.stage_decision_id,
        "rule_id": "CV-H01..CV-H07",
        "status": "closed",
        "decision": "Freeze the exact Popo source-unit dispositions and every MinerU media representation decision at source_reconciled checkpoint.",
        "evidence_refs": [
            "reviews/spec03_media_review.json",
            "ledgers/source_scope_ledger.json",
            "ledgers/reading_order_ledger.json",
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
                "status": "closed",
                "rule_id": "CV-H01..CV-H07",
                "event_file": "decisions/evidence_decisions.jsonl",
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

    media_by_source: dict[str, list[dict[str, Any]]] = {}
    representation_rows: list[dict[str, Any]] = []
    for disposition in media_dispositions:
        atom = next(item for item in media_atoms if item["media_id"] == disposition["media_id"])
        representation = {
            "media_id": disposition["media_id"],
            "source_atom_id": atom["source_atom_id"],
            "physical_page": atom["physical_page"],
            "bbox": atom["bbox"],
            "media_kind": atom["media_kind"],
            "disposition": disposition["disposition"],
            "selected_candidate_id": disposition["selected_candidate_id"],
            "selected_candidate": disposition["selected_candidate"],
            "source_ids": disposition["source_ids"],
            "evidence_refs": disposition["evidence_refs"],
            "review_status": "closed",
        }
        representation_rows.append(representation)
        for source_id in disposition["source_ids"]:
            media_by_source.setdefault(source_id, []).append(representation)

    blocks: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    for source_id, source_unit in source_by_id.items():
        scope_unit = scope_by_id[source_id]
        status = scope_unit["scope_status"]
        block_id = _block_id(material_id, source_id)
        block_media = media_by_source.get(source_id, [])
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
            "terminal_state": "source_reconciled" if status == "included" else "scope_excluded",
            "review_required": False,
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
        "spec_status": "passed",
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
            "open_reviews": 0,
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
    media_evidence_hash = _write_json(
        output / "media/media_evidence_ledger.json",
        {
            "schema_version": "media-evidence-ledger/1.0",
            "source_pdf_sha256": source_sha,
            "media_atoms": representation_rows,
            "summary": {
                "media_atoms": len(representation_rows),
                "open_reviews": 0,
            },
            "spec_status": "passed",
        },
    )
    media_plan_hash = _write_json(
        output / "media/media_representation_plan.json",
        {
            "schema_version": "media-representation-plan/1.0",
            "media_evidence_ledger_ref": "media/media_evidence_ledger.json",
            "media_evidence_ledger_hash": media_evidence_hash,
            "representations": representation_rows,
            "summary": {
                "media_atoms": len(representation_rows),
                "resolved": len(representation_rows),
                "open": 0,
            },
            "spec_status": "passed",
        },
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
                "media_atoms": len(representation_rows),
                "closed": len(representation_rows),
                "open": 0,
            },
            "spec_status": "passed",
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
        "open_reviews": [],
        "spec_status": "passed",
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
                f"- Closed media dispositions: {len(media_dispositions)}",
                "- Missing source units: 0",
                "- Duplicate source coverage: 0",
                "- Open source/media reviews: 0",
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
            "spec_status": "passed",
            "immutable_after_publication": True,
        },
    )
    source_commit_hash = _write_json(
        output / "manifests/source_reconciled_commit.json",
        {
            "schema_version": "source-reconciled-stage-manifest/2.0",
            "producer": KERNEL_VERSION,
            "checkpoint": "source_reconciled",
            "status": "passed",
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
