#!/usr/bin/env python3
"""Audit the exact Spec 05 ZIP for entity count and native raster media use."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image


SCHEMA = "spec05-delivery-asset-report/1.2"
MAX_FILE_ENTITIES_EXCLUSIVE = 2_000
MAX_RASTER_IMAGE_BYTES_EXCLUSIVE = 1_000_000
RASTER_SUFFIXES = {".jpg", ".jpeg", ".png"}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tex_media_refs(text: str) -> set[str]:
    refs = {
        match.group(1).strip()
        for match in re.finditer(r"\\includegraphics(?:\[[^]]*\])?\{([^{}]+)\}", text)
    }
    for macro in ("cover", "logo"):
        match = re.search(rf"\\{macro}\{{([^{{}}]+)\}}", text)
        if match:
            refs.add(match.group(1).strip())
    return refs


def _tex_input_refs(text: str) -> set[str]:
    return {
        match.group(1).strip()
        for match in re.finditer(r"\\(?:input|include)\{([^{}]+)\}", text)
    }


def _resolve_tex_ref(ref: str, current: str, names: set[str]) -> str:
    raw = PurePosixPath(ref)
    candidate = PurePosixPath(current).parent / raw
    if raw.is_absolute() or candidate.is_absolute() or ".." in raw.parts or ".." in candidate.parts:
        raise ValueError(f"unsafe TeX input reference: {ref}")
    roots = [raw.as_posix().lstrip("./"), candidate.as_posix().lstrip("./")]
    candidates = []
    for normalized in roots:
        candidates.extend([normalized] if PurePosixPath(normalized).suffix else [normalized + ".tex", normalized])
    candidates = list(dict.fromkeys(candidates))
    matches = [item for item in candidates if item in names]
    if len(matches) != 1:
        raise ValueError(f"TeX input reference must resolve exactly once: {current} -> {ref}")
    return matches[0]


def _tex_closure(archive: zipfile.ZipFile, names: set[str]) -> tuple[list[str], set[str]]:
    pending = ["main.tex"]
    visited: list[str] = []
    media_refs: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.append(current)
        text = archive.read(current).decode("utf-8")
        media_refs.update(_tex_media_refs(text))
        for ref in sorted(_tex_input_refs(text)):
            resolved = _resolve_tex_ref(ref, current, names)
            if resolved not in visited:
                pending.append(resolved)
    return visited, media_refs


def _resolve_refs(refs: set[str], names: set[str]) -> tuple[set[str], list[str]]:
    resolved: set[str] = set()
    missing: list[str] = []
    by_basename: dict[str, list[str]] = {}
    for name in names:
        by_basename.setdefault(PurePosixPath(name).name, []).append(name)
    for ref in refs:
        normalized = PurePosixPath(ref).as_posix().lstrip("./")
        candidates = [normalized]
        if not PurePosixPath(normalized).suffix:
            candidates.extend(normalized + suffix for suffix in sorted(RASTER_SUFFIXES))
        direct = [candidate for candidate in candidates if candidate in names]
        if len(direct) == 1:
            resolved.add(direct[0])
            continue
        basename_matches = []
        for candidate in candidates:
            basename_matches.extend(by_basename.get(PurePosixPath(candidate).name, []))
        basename_matches = sorted(set(basename_matches))
        if len(basename_matches) == 1:
            resolved.add(basename_matches[0])
        else:
            missing.append(ref)
    return resolved, sorted(missing)


def _generated_paths(materialization: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for key in ("copied_assets", "source_region_crops"):
        value = materialization.get(key, {})
        items = value.values() if isinstance(value, dict) else value
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("project_path"), str):
                paths.add(item["project_path"])
    for item in materialization.get("presentation_assets", []):
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            paths.add(item["path"])
    return paths


def audit(
    delivery_zip: Path,
    materialization_report: Path,
    template_contract: Path | None = None,
) -> dict[str, Any]:
    delivery_zip = delivery_zip.resolve()
    materialization_report = materialization_report.resolve()
    materialization = json.loads(materialization_report.read_text(encoding="utf-8"))
    immutable_template_media: dict[str, str] = {}
    if template_contract is not None:
        contract = json.loads(template_contract.resolve().read_text(encoding="utf-8"))
        immutable_template_media = {
            item["path"]: item["sha256"]
            for item in contract.get("immutable_files", [])
            if PurePosixPath(item.get("path", "")).suffix.lower() in RASTER_SUFFIXES
        }
    with zipfile.ZipFile(delivery_zip) as archive:
        infos = [item for item in archive.infolist() if not item.is_dir()]
        names = {item.filename for item in infos}
        if "main.tex" not in names:
            raise ValueError("delivery ZIP lacks root main.tex")
        tex_files, requested_refs = _tex_closure(archive, names)
        resolved_refs, missing_refs = _resolve_refs(requested_refs, names)
        generated = _generated_paths(materialization)
        media_files = sorted(
            name for name in names
            if PurePosixPath(name).suffix.lower() in RASTER_SUFFIXES | {".pdf"}
            and (name.startswith("images/") or name.startswith("figure/") or name.startswith("transport/"))
        )
        assets = []
        for name in media_files:
            data = archive.read(name)
            suffix = PurePosixPath(name).suffix.lower()
            dimensions = None
            if suffix in RASTER_SUFFIXES:
                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
                with Image.open(io.BytesIO(data)) as image:
                    dimensions = [image.width, image.height]
            assets.append({
                "path": name,
                "sha256": sha256_bytes(data),
                "size_bytes": len(data),
                "suffix": suffix,
                "dimensions_px": dimensions,
                "referenced": name in resolved_refs,
                "generated": name in generated,
            })
    preserved_unreferenced_template_media = sorted(
        name for name in set(media_files) - resolved_refs
        if name in immutable_template_media
        and next(item["sha256"] for item in assets if item["path"] == name) == immutable_template_media[name]
    )
    unreferenced_generated = sorted(generated - resolved_refs)
    unreferenced_media = sorted(set(media_files) - resolved_refs - set(preserved_unreferenced_template_media))
    forbidden_pdf_media = sorted(
        name for name in media_files
        if PurePosixPath(name).suffix.lower() == ".pdf"
        and (name.startswith("transport/") or name in resolved_refs or name in generated)
    )
    oversized_raster_images = sorted(
        item["path"] for item in assets
        if item["suffix"] in RASTER_SUFFIXES and item["size_bytes"] >= MAX_RASTER_IMAGE_BYTES_EXCLUSIVE
    )
    file_count = len(infos)
    checks = {
        "file_entities_strictly_under_2000": file_count < MAX_FILE_ENTITIES_EXCLUSIVE,
        "all_tex_media_references_resolve": not missing_refs,
        "no_unreferenced_generated_media": not unreferenced_generated,
        "no_unreferenced_project_media": not unreferenced_media,
        "native_raster_image_representation_preserved": not forbidden_pdf_media,
        "each_raster_image_strictly_under_1mb": not oversized_raster_images,
        "unreferenced_template_media_matches_frozen_bytes": all(
            name in preserved_unreferenced_template_media
            for name in set(media_files) - resolved_refs
            if name in immutable_template_media
        ),
    }
    passed = all(checks.values())
    failure_codes = []
    if not checks["file_entities_strictly_under_2000"]:
        failure_codes.append("COMPILE_DELIVERY_FILE_ENTITY_LIMIT_EXCEEDED")
    if not checks["native_raster_image_representation_preserved"]:
        failure_codes.append("COMPILE_IMAGE_REPRESENTATION_CHANGED")
    if not checks["each_raster_image_strictly_under_1mb"]:
        failure_codes.append("COMPILE_RASTER_IMAGE_SIZE_LIMIT_EXCEEDED")
    if not all(checks[key] for key in ("all_tex_media_references_resolve", "no_unreferenced_generated_media", "no_unreferenced_project_media")):
        failure_codes.append("COMPILE_DELIVERY_ASSET_REPORT_INVALID")
    return {
        "schema_version": SCHEMA,
        "spec_status": "passed" if passed else "failed",
        "delivery_zip": {
            "path": str(delivery_zip),
            "sha256": sha256_bytes(delivery_zip.read_bytes()),
            "file_entities": file_count,
        },
        "constraints": {
            "file_entities": {"operator": "strictly_less_than", "max_exclusive": MAX_FILE_ENTITIES_EXCLUSIVE},
            "image_output_formats": sorted(RASTER_SUFFIXES),
            "image_to_pdf_transport": "forbidden",
            "raster_image_bytes": {"operator": "strictly_less_than", "max_exclusive": MAX_RASTER_IMAGE_BYTES_EXCLUSIVE},
        },
        "checks": checks,
        "requested_media_refs": sorted(requested_refs),
        "scanned_tex_files": tex_files,
        "resolved_media_refs": sorted(resolved_refs),
        "missing_media_refs": missing_refs,
        "unreferenced_generated_media": unreferenced_generated,
        "unreferenced_project_media": unreferenced_media,
        "preserved_unreferenced_template_media": preserved_unreferenced_template_media,
        "forbidden_pdf_media": forbidden_pdf_media,
        "oversized_raster_images": oversized_raster_images,
        "assets": assets,
        "summary": {
            "file_entities": file_count,
            "media_files": len(media_files),
            "referenced_media_files": len(resolved_refs),
            "generated_media_files": len(generated),
            "scanned_tex_files": len(tex_files),
        },
        "failure_codes": failure_codes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit an exact ElegantBook delivery ZIP asset policy")
    parser.add_argument("--delivery-zip", type=Path, required=True)
    parser.add_argument("--materialization-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.delivery_zip, args.materialization_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "spec_status": report["spec_status"],
        "failure_codes": report["failure_codes"],
        "file_entities": report["delivery_zip"]["file_entities"],
        "output": str(args.output.resolve()),
    }, ensure_ascii=False))
    return 0 if report["spec_status"] == "passed" else 4


if __name__ == "__main__":
    raise SystemExit(main())
