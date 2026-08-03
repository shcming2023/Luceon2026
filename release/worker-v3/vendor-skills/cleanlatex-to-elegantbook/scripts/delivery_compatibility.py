#!/usr/bin/env python3
"""Portable Overleaf delivery transport and artifact naming primitives.

The root entry remains ``main.tex`` and loads one controlled body loader.  The
loader directly includes leaf parts grouped by Spec 04-D's frozen semantic body
units.  A large unit is split only inside that unit, at complete render-node or
line boundaries.  Concatenating all leaf parts must reproduce the immutable
``rendered_body.tex`` bytes exactly.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA = "spec05-overleaf-delivery-compatibility-report/3.0"
GENERATED_BODY_PATH = "body/generated-body.tex"
GENERATED_BODY_INPUT = r"\input{body/generated-body.tex}"
GENERATED_UNIT_DIR = "body/units"
GENERATED_PART_RE = re.compile(r"^body/units/unit-(\d{4})/part-(\d{4})\.tex$")
LOADER_LINE_RE = re.compile(r"^\\input\{(body/units/unit-\d{4}/part-\d{4}\.tex)\}$")
MAX_BODY_PART_BYTES_EXCLUSIVE = 900_000
EDITABLE_TEXT_EXTENSIONS = {".tex", ".bib", ".cls", ".sty", ".bst", ".cfg", ".def"}
MAX_BASENAME_UTF8_BYTES = 240
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_BEHAVIOUR_PATTERN = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand|def|gdef|xdef|"
    r"AtBeginDocument|input|include)\b"
)
_DEFINITION_PATTERN = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand|def|gdef|xdef|AtBeginDocument)\b"
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    allowance = max_bytes - len(("-" + digest).encode("ascii"))
    prefix = encoded[:allowance]
    while True:
        try:
            return prefix.decode("utf-8").rstrip(" .") + "-" + digest
        except UnicodeDecodeError:
            prefix = prefix[:-1]


def portable_delivery_stem(title: str, volume_label: str | None = None) -> str:
    """Derive a stable, Unicode-preserving outer artifact stem from frozen metadata."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("frozen title must be a non-empty string")
    parts = [title]
    if volume_label is not None:
        if not isinstance(volume_label, str) or not volume_label.strip():
            raise ValueError("frozen volume label must be non-empty when present")
        parts.append(volume_label)
    value = unicodedata.normalize("NFC", " - ".join(parts))
    value = "".join("_" if ord(ch) < 32 or ch in '<>:"/\\|?*' else ch for ch in value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value or value in {".", ".."}:
        raise ValueError("frozen metadata produces an empty or unsafe delivery name")
    if value.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        value = "_" + value
    return _truncate_utf8(value, MAX_BASENAME_UTF8_BYTES - len(".zip"))


def expected_delivery_names(title: str, volume_label: str | None = None) -> dict[str, str]:
    stem = portable_delivery_stem(title, volume_label)
    return {"stem": stem, "zip": f"{stem}.zip", "pdf": f"{stem}.pdf"}


def build_body_transport(
    rendered_body: bytes,
    *,
    body_units: list[dict[str, Any]] | None = None,
    emissions: list[dict[str, Any]] | None = None,
    max_body_part_bytes_exclusive: int = MAX_BODY_PART_BYTES_EXCLUSIVE,
) -> dict[str, Any]:
    """Return a deterministic semantic-unit body transport."""
    if max_body_part_bytes_exclusive <= 1:
        raise ValueError("max body-part bytes must leave room for content")

    lines = rendered_body.splitlines(keepends=True)
    if body_units is None:
        body_units = [{
            "unit_id": "unit-0001",
            "ordinal": 1,
            "render_node_ids": [item["render_node_id"] for item in emissions or []],
        }]
    if emissions is None:
        if len(body_units) != 1:
            raise ValueError("semantic body units require render emissions")
        unit_payloads = [(body_units[0], [rendered_body])]
    else:
        emission_map = {item["render_node_id"]: item for item in emissions}
        if len(emission_map) != len(emissions):
            raise ValueError("render emissions contain duplicate node ids")
        unit_payloads = []
        consumed: list[str] = []
        for expected_ordinal, unit in enumerate(body_units, 1):
            if unit.get("ordinal") != expected_ordinal or unit.get("unit_id") != f"unit-{expected_ordinal:04d}":
                raise ValueError("semantic body unit ids/ordinals are unstable")
            chunks: list[bytes] = []
            for node_id in unit.get("render_node_ids", []):
                emission = emission_map.get(node_id)
                if emission is None:
                    raise ValueError(f"semantic body unit references an absent emission: {node_id}")
                start, end = emission["latex_start_line"], emission["latex_end_line"]
                chunks.append(b"".join(lines[start - 1:end]))
                consumed.append(node_id)
            if not chunks:
                raise ValueError("semantic body unit is empty")
            unit_payloads.append((unit, chunks))
        if consumed != [item["render_node_id"] for item in emissions]:
            raise ValueError("semantic body units do not exactly cover render emissions")

    named_parts: list[dict[str, Any]] = []
    for unit, node_chunks in unit_payloads:
        part_payloads: list[bytes] = []
        current = bytearray()
        for node_chunk in node_chunks:
            atomic_chunks = [node_chunk]
            if len(node_chunk) >= max_body_part_bytes_exclusive:
                atomic_chunks = node_chunk.splitlines(keepends=True)
            for chunk in atomic_chunks:
                if len(chunk) >= max_body_part_bytes_exclusive:
                    raise ValueError("one rendered-body line exceeds the 900K body-part limit")
                if current and len(current) + len(chunk) >= max_body_part_bytes_exclusive:
                    part_payloads.append(bytes(current))
                    current.clear()
                current.extend(chunk)
        if current:
            part_payloads.append(bytes(current))
        for part_ordinal, payload in enumerate(part_payloads, 1):
            named_parts.append({
                "unit_id": unit["unit_id"],
                "unit_ordinal": unit["ordinal"],
                "part_ordinal": part_ordinal,
                "path": f"{GENERATED_UNIT_DIR}/{unit['unit_id']}/part-{part_ordinal:04d}.tex",
                "bytes": payload,
            })
    loader = "".join(f"\\input{{{item['path']}}}\n" for item in named_parts).encode("utf-8")
    if len(loader) >= max_body_part_bytes_exclusive:
        raise ValueError("generated-body loader exceeds the 900K limit")
    reconstructed = b"".join(item["bytes"] for item in named_parts)
    if reconstructed != rendered_body:
        raise AssertionError("body sharding changed rendered bytes")
    return {
        "mode": "semantic_unit_payload",
        "loader_bytes": loader,
        "parts": named_parts,
        "reconstructed_bytes": reconstructed,
    }


def _loader_part_paths(loader_text: str) -> list[str] | None:
    lines = [line.strip() for line in loader_text.splitlines() if line.strip()]
    paths: list[str] = []
    for line in lines:
        match = LOADER_LINE_RE.fullmatch(line)
        if match is None:
            return None
        paths.append(match.group(1))
    previous_unit = previous_part = 0
    for path in paths:
        match = GENERATED_PART_RE.fullmatch(path)
        if match is None:
            return None
        unit, part = map(int, match.groups())
        if unit == previous_unit:
            if part != previous_part + 1:
                return None
        elif unit == previous_unit + 1:
            if part != 1:
                return None
        else:
            return None
        previous_unit, previous_part = unit, part
    return paths


def audit_zip_transport(
    delivery_zip: Path,
    rendered_body: Path,
    *,
    main_path: str = "main.tex",
    generated_body_path: str = GENERATED_BODY_PATH,
) -> dict[str, Any]:
    delivery_zip = delivery_zip.resolve()
    rendered_body = rendered_body.resolve()
    expected_body = rendered_body.read_bytes()
    with zipfile.ZipFile(delivery_zip) as archive:
        infos = [item for item in archive.infolist() if not item.is_dir()]
        names = [item.filename for item in infos]
        if names.count(main_path) != 1 or names.count(generated_body_path) != 1:
            raise ValueError("delivery ZIP must contain exactly one root main.tex and one generated body payload")
        for name in (main_path, generated_body_path):
            member = PurePosixPath(name)
            if member.is_absolute() or ".." in member.parts:
                raise ValueError(f"unsafe delivery member path: {name}")
        main_bytes = archive.read(main_path)
        body_bytes = archive.read(generated_body_path)
        main_text = main_bytes.decode("utf-8")
        body_text = body_bytes.decode("utf-8")
        wrapper_count = main_text.count(GENERATED_BODY_INPUT)
        loader_parts = _loader_part_paths(body_text)
        transport_mode = "semantic_unit_payload" if loader_parts is not None and loader_parts else "invalid_or_legacy_payload"
        part_names = loader_parts or []
        part_name_set = set(part_names)
        missing_parts = [name for name in part_names if name not in names]
        part_bytes = [archive.read(name) for name in part_names if name in names]
        reconstructed_body = b"".join(part_bytes) if transport_mode == "semantic_unit_payload" else body_bytes
        unexpected_tex = sorted(
            name for name in names
            if PurePosixPath(name).suffix.lower() == ".tex"
            and name not in {main_path, generated_body_path}
            and name not in part_name_set
        )
        tex_members = sorted(name for name in names if PurePosixPath(name).suffix.lower() == ".tex")
        oversized_tex = [name for name in tex_members if archive.getinfo(name).file_size >= MAX_BODY_PART_BYTES_EXCLUSIVE]
        editable_members = sorted(
            name for name in names if PurePosixPath(name).suffix.lower() in EDITABLE_TEXT_EXTENSIONS
        )
        editable_text_bytes = sum(archive.getinfo(name).file_size for name in editable_members)
        parts_safe = all(
            not _BEHAVIOUR_PATTERN.search(payload.decode("utf-8")) for payload in part_bytes
        )
        checks = {
            "root_main_tex_present_once": names.count(main_path) == 1,
            "generated_body_present_once": names.count(generated_body_path) == 1,
            "root_main_uses_exact_generated_body_input_once": wrapper_count == 1,
            "generated_body_reconstructs_rendered_body": reconstructed_body == expected_body,
            "generated_body_transport_is_controlled": (
                transport_mode == "semantic_unit_payload"
                and loader_parts is not None and not missing_parts
                and not bool(_DEFINITION_PATTERN.search(body_text)) and parts_safe
            ),
            "no_additional_tex_payloads": not unexpected_tex,
            "each_body_transport_tex_strictly_under_900k": not oversized_tex,
            "regular_file_modes": all(((item.external_attr >> 16) & 0o170000) != 0o120000 for item in infos),
        }
    status = "passed" if all(checks.values()) else "failed"
    return {
        "schema_version": SCHEMA,
        "spec_status": status,
        "gate": {"gate_id": "CP-H25", "status": status},
        "capacity_gate": {"gate_id": "CP-H27", "status": status},
        "delivery_zip": {"path": str(delivery_zip), "sha256": sha256_bytes(delivery_zip.read_bytes())},
        "root_main": {"path": main_path, "sha256": sha256_bytes(main_bytes), "input_literal": GENERATED_BODY_INPUT},
        "generated_body": {
            "path": generated_body_path, "sha256": sha256_bytes(body_bytes), "bytes": len(body_bytes),
            "transport_mode": transport_mode,
            "parts": [
                {"path": name, "sha256": sha256_bytes(payload), "bytes": len(payload)}
                for name, payload in zip(part_names, part_bytes)
            ],
        },
        "rendered_body": {"path": str(rendered_body), "sha256": sha256_bytes(expected_body), "bytes": len(expected_body)},
        "capacity": {
            "max_body_transport_tex_bytes_exclusive": MAX_BODY_PART_BYTES_EXCLUSIVE,
            "tex_members": tex_members,
            "oversized_tex_members": oversized_tex,
            "editable_text_members": editable_members,
            "editable_text_bytes": editable_text_bytes,
        },
        "checks": checks,
        "unexpected_tex_payloads": unexpected_tex,
        "missing_body_parts": missing_parts,
        "failure_code": None if status == "passed" else "COMPILE_BODY_SEMANTIC_SHARD_OR_TRANSPORT_INVALID",
    }


def naming_report(
    *,
    title: str,
    volume_label: str | None,
    delivery_zip: Path,
    delivery_pdf: Path,
) -> dict[str, Any]:
    names = expected_delivery_names(title, volume_label)
    checks = {
        "zip_name_matches_frozen_cover_identity": delivery_zip.name == names["zip"],
        "pdf_name_matches_frozen_cover_identity": delivery_pdf.name == names["pdf"],
        "zip_and_pdf_share_stem": delivery_zip.stem == delivery_pdf.stem == names["stem"],
        "artifact_names_are_leaf_names": Path(delivery_zip.name).name == delivery_zip.name and Path(delivery_pdf.name).name == delivery_pdf.name,
    }
    status = "passed" if all(checks.values()) else "failed"
    return {
        "schema_version": "spec05-delivery-naming-report/1.0",
        "spec_status": status,
        "gate": {"gate_id": "CP-H26", "status": status},
        "frozen_identity": {"title": title, "volume_label": volume_label},
        "expected": names,
        "actual": {"zip": delivery_zip.name, "pdf": delivery_pdf.name},
        "checks": checks,
        "failure_code": None if status == "passed" else "COMPILE_DELIVERY_NAME_MISMATCH",
    }
