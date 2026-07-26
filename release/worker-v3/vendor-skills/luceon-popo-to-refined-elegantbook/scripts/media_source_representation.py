#!/usr/bin/env python3
"""Build and validate fail-closed media evidence and representation contracts.

The implementation is deliberately independent of material identity.  It
accepts normalized media atoms, verifies live PDF/asset evidence, creates
reproducible source-region crops, and freezes one representation or a blocking
review item per included atom.  It never selects an ElegantBook construct.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import mimetypes
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import fitz
from PIL import Image, ImageOps

VERSION = "media-source-representation/1.3.1"
REPRESENTATIONS = {
    "source_asset_image",
    "source_region_image",
    "structured_formula",
    "structured_table",
    "structured_chart",
    "vector_reconstruction",
}
STRUCTURED = {
    "structured_formula",
    "structured_table",
    "structured_chart",
    "vector_reconstruction",
}
COORDINATE_SPACES = {
    "pdf_cropbox_normalized_0_1_top_left",
    "pdf_cropbox_points_top_left",
}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def payload_hash(value: dict[str, Any]) -> str:
    return canonical_hash({key: item for key, item in value.items() if key not in {"generated_at", "payload_hash"}})


def safe_relative(value: str) -> Path:
    posix = PurePosixPath(value)
    if posix.is_absolute() or ".." in posix.parts:
        raise ValueError(f"unsafe relative path: {value}")
    return Path(*posix.parts)


def parse_roots(values: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"asset root must be NAME=PATH: {value}")
        name, raw_path = value.split("=", 1)
        if not name or name in roots:
            raise ValueError(f"empty or duplicate asset root: {name}")
        roots[name] = Path(raw_path).resolve()
    return roots


def image_metrics(path: Path) -> dict[str, Any]:
    with Image.open(path) as source:
        image = source.convert("RGB")
        width, height = image.size
        fmt = source.format
    gray = ImageOps.grayscale(image)
    sample = gray.copy()
    sample.thumbnail((500, 500), Image.Resampling.LANCZOS)
    histogram = sample.histogram()
    total = max(1, sample.width * sample.height)
    nonwhite = sum(histogram[:245]) / total
    dark = sum(histogram[:200]) / total
    band = max(1, min(width, height) // 50)
    edge = Image.new("L", (width, height), 255)
    edge.paste(gray.crop((0, 0, width, band)), (0, 0))
    edge.paste(gray.crop((0, height - band, width, height)), (0, height - band))
    edge.paste(gray.crop((0, 0, band, height)), (0, 0))
    edge.paste(gray.crop((width - band, 0, width, height)), (width - band, 0))
    edge_hist = edge.histogram()
    edge_pixels = max(1, 2 * band * width + 2 * band * max(0, height - 2 * band))
    edge_dark = sum(edge_hist[:200]) / edge_pixels
    return {
        "format": fmt,
        "width_px": width,
        "height_px": height,
        "nonwhite_ratio": round(nonwhite, 8),
        "dark_ratio": round(dark, 8),
        "edge_dark_ratio": round(edge_dark, 8),
    }


def bbox_rect(page: fitz.Page, bbox: list[float], coordinate_space: str) -> fitz.Rect:
    if len(bbox) != 4 or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in bbox):
        raise ValueError("bbox must contain four finite numbers")
    x0, y0, x1, y1 = map(float, bbox)
    if coordinate_space == "pdf_cropbox_normalized_0_1_top_left":
        if min(x0, y0) < 0 or max(x1, y1) > 1:
            raise ValueError("normalized bbox is outside 0..1")
        rect = fitz.Rect(x0 * page.rect.width, y0 * page.rect.height, x1 * page.rect.width, y1 * page.rect.height)
    elif coordinate_space == "pdf_cropbox_points_top_left":
        rect = fitz.Rect(x0, y0, x1, y1)
    else:
        raise ValueError(f"unsupported coordinate space: {coordinate_space}")
    if rect.is_empty or rect.is_infinite or not page.rect.contains(rect):
        raise ValueError(f"bbox is empty or outside CropBox page rect: {list(rect)}")
    return rect


def cropbox_bbox_to_raster_box(
    bbox: list[float], page: fitz.Page, raster_size: tuple[int, int], padding: dict[str, float]
) -> tuple[int, int, int, int]:
    """Map a CropBox-normalized top-left bbox into a full-MediaBox page raster."""
    if len(bbox) != 4 or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in bbox):
        raise ValueError("bbox must contain four finite numbers")
    if set(padding) != {"x", "y"}:
        raise ValueError("crop padding must contain exactly x and y")
    x0, y0, x1, y1 = map(float, bbox)
    if min(x0, y0) < 0 or max(x1, y1) > 1 or x1 <= x0 or y1 <= y0:
        raise ValueError("normalized bbox is empty or outside 0..1")
    media, crop = page.mediabox, page.cropbox
    raster_width, raster_height = raster_size
    pad_x = float(padding["x"]) * crop.width
    pad_y = float(padding["y"]) * crop.height
    left = int(((crop.x0 + x0 * crop.width - pad_x) - media.x0) * raster_width / media.width)
    top = int(((crop.y0 + y0 * crop.height - pad_y) - media.y0) * raster_height / media.height)
    right = math.ceil(((crop.x0 + x1 * crop.width + pad_x) - media.x0) * raster_width / media.width)
    bottom = math.ceil(((crop.y0 + y1 * crop.height + pad_y) - media.y0) * raster_height / media.height)
    return max(0, left), max(0, top), min(raster_width, right), min(raster_height, bottom)


def render_region(
    doc: fitz.Document,
    source_pdf: Path,
    atom: dict[str, Any],
    candidate: dict[str, Any],
    crop_dir: Path,
    roots: dict[str, Path],
) -> dict[str, Any]:
    page_number = int(candidate.get("source_page", atom.get("source_page", 0)))
    if page_number < 1 or page_number > len(doc):
        raise ValueError(f"source page outside PDF: {page_number}")
    bbox = candidate.get("bbox", atom.get("bbox"))
    coordinate_space = candidate.get("bbox_coordinate_space", atom.get("bbox_coordinate_space"))
    if coordinate_space not in COORDINATE_SPACES:
        raise ValueError(f"coordinate space must be explicit and supported: {coordinate_space}")
    page = doc[page_number - 1]
    crop_path = crop_dir / f"{atom['media_id']}--{candidate['candidate_id']}.png"
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    crop_recipe = candidate.get("crop_recipe", "direct_pdf_clip")
    raster_evidence: dict[str, Any] | None = None
    if crop_recipe == "source_page_raster_cropbox_to_mediabox":
        if coordinate_space != "pdf_cropbox_normalized_0_1_top_left":
            raise ValueError("source-page raster recipe requires CropBox-normalized coordinates")
        if candidate.get("raster_coordinate_space") != "pdf_mediabox_pixels_top_left":
            raise ValueError("source-page raster recipe requires MediaBox pixel coordinates")
        root_id = candidate.get("source_raster_root_id")
        if root_id not in roots:
            raise ValueError(f"unknown source-raster root: {root_id}")
        raster_path = roots[root_id] / safe_relative(candidate["source_raster_path"])
        if not raster_path.is_file():
            raise ValueError(f"source page raster missing: {raster_path}")
        raster_hash = sha256_file(raster_path)
        if candidate.get("source_raster_sha256") != raster_hash:
            raise ValueError("source page raster hash mismatch")
        padding = candidate.get("crop_padding_fraction_of_cropbox")
        if not isinstance(padding, dict):
            raise ValueError("source-page raster recipe requires explicit crop padding")
        with Image.open(raster_path) as image:
            box = cropbox_bbox_to_raster_box(bbox, page, image.size, padding)
            if box[2] <= box[0] or box[3] <= box[1]:
                raise ValueError("source-page raster recipe produced an empty crop")
            image.crop(box).save(crop_path, "PNG", optimize=True)
        raster_evidence = {
            "path": str(raster_path.resolve()),
            "sha256": raster_hash,
            "raster_coordinate_space": candidate["raster_coordinate_space"],
            "raster_box_px": list(box),
            "crop_padding_fraction_of_cropbox": padding,
        }
    elif crop_recipe == "direct_pdf_clip":
        scale = float(candidate.get("render_scale", 2.0))
        if scale < 1 or scale > 6:
            raise ValueError("render_scale must be between 1 and 6")
        # Isolate every direct clip from the long-lived document cache.  Some
        # scanned PDFs otherwise produce byte-different PNGs after unrelated
        # pages were rasterized, which invalidates exact review-hash binding.
        with fitz.open(source_pdf) as isolated_doc:
            isolated_page = isolated_doc[page_number - 1]
            rect = bbox_rect(isolated_page, bbox, coordinate_space)
            pix = isolated_page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
            pix.save(crop_path)
    else:
        raise ValueError(f"unsupported crop recipe: {crop_recipe}")
    metrics = image_metrics(crop_path)
    flags: list[str] = []
    if metrics["width_px"] < 4 or metrics["height_px"] < 4:
        flags.append("crop_too_small")
    if metrics["nonwhite_ratio"] < float(candidate.get("blank_nonwhite_threshold", 0.002)):
        flags.append("suspicious_blank_crop")
    if metrics["edge_dark_ratio"] > float(candidate.get("edge_dark_threshold", 0.02)):
        flags.append("possible_boundary_truncation")
    artifact_hash = sha256_file(crop_path)
    if candidate.get("artifact_sha256") and candidate["artifact_sha256"] != artifact_hash:
        flags.append("source_region_artifact_hash_mismatch")
    review = candidate.get("human_review") or {}
    bound_review = bool(
        review.get("status") == "closed"
        and review.get("decision_id")
        and review.get("observed_artifact_sha256") == artifact_hash
    )
    if not bound_review:
        flags.append("source_region_review_not_bound_to_artifact")
    return {
        "candidate_id": candidate["candidate_id"],
        "representation_type": "source_region_image",
        "status": "usable" if not flags else "needs_review",
        "source_pdf_path": str(source_pdf),
        "source_pdf_sha256": sha256_file(source_pdf),
        "source_page": page_number,
        "bbox": [round(float(value), 8) for value in bbox],
        "bbox_coordinate_space": coordinate_space,
        "crop_recipe": crop_recipe,
        "page_geometry": {
            "cropbox_rect_pt": [round(value, 6) for value in page.rect],
            "cropbox_pdf_rect_pt": [round(value, 6) for value in page.cropbox],
            "mediabox_pdf_rect_pt": [round(value, 6) for value in page.mediabox],
            "rotation": page.rotation,
        },
        "crop_path": str(crop_path.resolve()),
        "artifact_sha256": artifact_hash,
        "metrics": metrics,
        "anomaly_flags": sorted(set(flags)),
        "human_review": review,
        "upstream_refs": candidate.get("upstream_refs", []),
        **({"source_raster": raster_evidence} if raster_evidence else {}),
    }


def assess_asset(candidate: dict[str, Any], roots: dict[str, Path]) -> dict[str, Any]:
    root_id = candidate.get("root_id")
    if root_id not in roots:
        raise ValueError(f"unknown asset root: {root_id}")
    path = roots[root_id] / safe_relative(candidate["path"])
    flags: list[str] = []
    if not path.is_file():
        return {**candidate, "status": "invalid", "resolved_path": str(path), "anomaly_flags": ["asset_missing"]}
    actual_hash = sha256_file(path)
    if candidate.get("sha256") and candidate["sha256"] != actual_hash:
        flags.append("asset_hash_mismatch")
    try:
        metrics = image_metrics(path)
    except Exception as exc:
        return {
            **candidate,
            "status": "invalid",
            "resolved_path": str(path.resolve()),
            "artifact_sha256": actual_hash,
            "anomaly_flags": ["asset_not_decodable_image"],
            "diagnostic": str(exc),
        }
    if metrics["width_px"] < 2 or metrics["height_px"] < 2:
        flags.append("asset_too_small")
    return {
        **candidate,
        "status": "usable" if not flags else "invalid",
        "resolved_path": str(path.resolve()),
        "artifact_sha256": actual_hash,
        "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "size_bytes": path.stat().st_size,
        "metrics": metrics,
        "anomaly_flags": flags,
    }


def assess_structured(candidate: dict[str, Any]) -> dict[str, Any]:
    payload = candidate.get("payload")
    actual_hash = canonical_hash(payload)
    flags: list[str] = []
    if candidate.get("payload_sha256") and candidate["payload_sha256"] != actual_hash:
        flags.append("structured_payload_hash_mismatch")
    if candidate.get("verification_status") != "verified" or not candidate.get("verification_refs"):
        flags.append("structured_transformation_unverified")
    return {
        **candidate,
        "status": "usable" if not flags else "needs_review",
        "artifact_sha256": actual_hash,
        "anomaly_flags": flags,
    }


def choose_representation(atom: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    base = {
        "representation_id": f"representation::{atom['media_id']}",
        "media_id": atom["media_id"],
        "source_block_ids": atom["source_block_ids"],
        "decision_refs": [],
    }
    if atom["inclusion_status"] == "excluded":
        return {**base, "status": "excluded", "selected_candidate_id": None, "representation_type": None, "rule_id": "MEDIA-SCOPE-EXCLUDED", "reason": "Spec 02 excluded this atom"}
    if atom["inclusion_status"] != "included":
        return {**base, "status": "needs_review", "selected_candidate_id": None, "representation_type": None, "rule_id": "MEDIA-SCOPE-OPEN", "reason": "media scope is not closed"}

    by_id = {item["candidate_id"]: item for item in candidates}
    requested = atom.get("requested_candidate_id")
    if requested:
        candidate = by_id.get(requested)
        if not candidate or candidate.get("status") != "usable":
            return {**base, "status": "needs_review", "selected_candidate_id": None, "representation_type": None, "rule_id": "MEDIA-REQUESTED-CANDIDATE-BLOCKED", "reason": "requested candidate is missing, invalid, or not reviewed"}
        review = candidate.get("human_review") or {}
        decisions = list(candidate.get("decision_refs", []))
        if review.get("decision_id"):
            decisions.append(review["decision_id"])
        return {**base, "status": "closed", "selected_candidate_id": requested, "representation_type": candidate["representation_type"], "artifact_sha256": candidate["artifact_sha256"], "rule_id": "MEDIA-EXPLICIT-VERIFIED-SELECTION", "decision_refs": decisions, "reason": "explicit candidate satisfies its evidence and review contract"}

    assets = [item for item in candidates if item["representation_type"] == "source_asset_image" and item.get("status") == "usable"]
    asset_hashes = {item["artifact_sha256"] for item in assets}
    if assets and len(asset_hashes) == 1:
        selected = sorted(assets, key=lambda item: item["candidate_id"])[0]
        return {**base, "status": "closed", "selected_candidate_id": selected["candidate_id"], "representation_type": "source_asset_image", "artifact_sha256": selected["artifact_sha256"], "equivalent_candidate_ids": sorted(item["candidate_id"] for item in assets), "rule_id": "MEDIA-UNIQUE-HASH-VERIFIED-ASSET", "reason": "all viable upstream asset candidates are byte-identical"}
    if len(asset_hashes) > 1:
        return {**base, "status": "needs_review", "selected_candidate_id": None, "representation_type": None, "rule_id": "MEDIA-ASSET-COLLISION", "reason": "multiple viable upstream assets disagree by hash"}

    structured = [item for item in candidates if item["representation_type"] in STRUCTURED and item.get("status") == "usable"]
    if len(structured) == 1:
        selected = structured[0]
        return {**base, "status": "closed", "selected_candidate_id": selected["candidate_id"], "representation_type": selected["representation_type"], "artifact_sha256": selected["artifact_sha256"], "rule_id": "MEDIA-VERIFIED-STRUCTURED-TRANSFORMATION", "reason": "the sole structured candidate has explicit verification evidence"}
    return {**base, "status": "needs_review", "selected_candidate_id": None, "representation_type": None, "rule_id": "MEDIA-NO-SAFE-AUTOMATIC-SELECTION", "reason": "no unique hash-verified asset or verified structured representation is available"}


def build_contracts(input_path: Path, source_pdf: Path, roots: dict[str, Path], output_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    normalized = read_json(input_path)
    if normalized.get("schema_version") not in {"normalized-media-candidates/1.0", "normalized-media-candidates/1.1", "normalized-media-candidates/1.2"}:
        raise ValueError("unsupported normalized input schema")
    source_pdf = source_pdf.resolve()
    if not source_pdf.is_file() or sha256_file(source_pdf) != normalized["source_pdf"]["sha256"]:
        raise ValueError("source PDF is missing or its hash differs from normalized evidence")
    doc = fitz.open(source_pdf)
    atoms_out: list[dict[str, Any]] = []
    representations: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
    crop_dir = output_dir / "crops"
    identifiers: set[str] = set()
    for atom in normalized["atoms"]:
        media_id = atom.get("media_id")
        if not media_id or media_id in identifiers:
            raise ValueError(f"missing or duplicate media_id: {media_id}")
        identifiers.add(media_id)
        if not atom.get("source_block_ids") or not isinstance(atom.get("source_page"), int):
            raise ValueError(f"atom lacks source identity: {media_id}")
        candidates_out: list[dict[str, Any]] = []
        candidate_ids: set[str] = set()
        for candidate in atom.get("candidates", []):
            candidate_id = candidate.get("candidate_id")
            representation = candidate.get("representation_type")
            if not candidate_id or candidate_id in candidate_ids or representation not in REPRESENTATIONS:
                raise ValueError(f"invalid candidate identity/type on {media_id}: {candidate_id}/{representation}")
            candidate_ids.add(candidate_id)
            try:
                if representation == "source_asset_image":
                    assessed = assess_asset(candidate, roots)
                elif representation == "source_region_image":
                    assessed = render_region(doc, source_pdf, atom, candidate, crop_dir, roots)
                else:
                    assessed = assess_structured(candidate)
            except Exception as exc:
                assessed = {**candidate, "status": "invalid", "anomaly_flags": ["candidate_assessment_failed"], "diagnostic": str(exc)}
            candidates_out.append(assessed)
        if not candidates_out:
            raise ValueError(f"media atom has no candidates: {media_id}")
        atom_flags = sorted({flag for item in candidates_out for flag in item.get("anomaly_flags", [])})
        atom_out = {
            **{key: value for key, value in atom.items() if key != "candidates"},
            "candidates": candidates_out,
            "anomaly_flags": atom_flags,
            "review_status": "needs_review" if atom["inclusion_status"] == "needs_review" else "closed",
        }
        representation_out = choose_representation(atom_out, candidates_out)
        if representation_out["status"] == "needs_review":
            atom_out["review_status"] = "needs_review"
            review_items.append({
                "review_id": f"review::{media_id}",
                "media_id": media_id,
                "status": "open",
                "reason": representation_out["reason"],
                "candidate_ids": sorted(candidate_ids),
                "anomaly_flags": atom_flags,
            })
        atoms_out.append(atom_out)
        representations.append(representation_out)
    doc.close()

    ledger = {
        "schema_version": "media-evidence-ledger/1.1" if normalized.get("canonical_ledger") else "media-evidence-ledger/1.0",
        "generator": VERSION,
        "generated_at": now(),
        "ledger_id": normalized.get("ledger_id", f"media::{canonical_hash(normalized['source_pdf'])[:16]}"),
        "normalized_input": {"path": str(input_path.resolve()), "sha256": sha256_file(input_path)},
        "source_pdf": {"path": str(source_pdf), "sha256": sha256_file(source_pdf), "page_count": len(fitz.open(source_pdf))},
        **({"canonical_ledger": normalized["canonical_ledger"]} if normalized.get("canonical_ledger") else {}),
        **({"decision_index": normalized["decision_index"]} if normalized.get("decision_index") else {}),
        "atoms": atoms_out,
        "summary": {
            "atoms": len(atoms_out),
            "included": sum(item["inclusion_status"] == "included" for item in atoms_out),
            "excluded": sum(item["inclusion_status"] == "excluded" for item in atoms_out),
            "needs_review": sum(item["review_status"] == "needs_review" for item in atoms_out),
            "media_kinds": dict(sorted(Counter(item["media_kind"] for item in atoms_out).items())),
        },
    }
    ledger["payload_hash"] = payload_hash(ledger)
    ledger_path = output_dir / "media_evidence_ledger.json"
    write_json(ledger_path, ledger)
    open_reviews = len(review_items)
    plan = {
        "schema_version": "media-representation-plan/1.1" if normalized.get("canonical_ledger") else "media-representation-plan/1.0",
        "generator": VERSION,
        "generated_at": now(),
        "media_evidence_ledger_sha256": sha256_file(ledger_path),
        **({"canonical_ledger_sha256": normalized["canonical_ledger"]["sha256"]} if normalized.get("canonical_ledger") else {}),
        **({"decision_index_sha256": normalized["decision_index"]["sha256"]} if normalized.get("decision_index") else {}),
        "spec_status": "passed" if open_reviews == 0 else "needs_review",
        "open_reviews": open_reviews,
        "representations": representations,
        "summary": {
            "representations": len(representations),
            "closed": sum(item["status"] == "closed" for item in representations),
            "excluded": sum(item["status"] == "excluded" for item in representations),
            "needs_review": open_reviews,
            "types": dict(sorted(Counter(item.get("representation_type") or "none" for item in representations).items())),
        },
    }
    plan["payload_hash"] = payload_hash(plan)
    write_json(output_dir / "media_representation_plan.json", plan)
    queue = {
        "schema_version": "media-review-queue/1.0",
        "generated_at": now(),
        "status": "closed" if not review_items else "open",
        "open_items": len(review_items),
        "items": review_items,
    }
    write_json(output_dir / "media_review_queue.json", queue)
    return ledger, plan, queue


def normalized_from_canonical(
    ledger_path: Path, decision_index_path: Path, source_pdf: Path, output_dir: Path
) -> Path:
    """Project native per-record media contracts from an immutable canonical ledger."""
    ledger_path = ledger_path.resolve()
    decision_index_path = decision_index_path.resolve()
    source_pdf = source_pdf.resolve()
    if not ledger_path.is_file() or not decision_index_path.is_file() or not source_pdf.is_file():
        raise FileNotFoundError("canonical ledger, decision index, and source PDF must all exist")
    with ledger_path.open(encoding="utf-8") as stream:
        header = json.loads(next(stream))
        records = [json.loads(line) for line in stream if line.strip()]
    if header.get("record_type") != "ledger_header" or header.get("spec_status") != "passed":
        raise ValueError("canonical ledger is not a passed immutable snapshot")
    if header.get("canonical_decision_index_hash") != sha256_file(decision_index_path):
        raise ValueError("canonical ledger is not bound to the supplied decision index")
    source_hash = sha256_file(source_pdf)
    if header.get("material_identity", {}).get("source_pdf_sha256") != source_hash:
        raise ValueError("canonical ledger source PDF binding mismatch")
    atoms: dict[str, dict[str, Any]] = {}
    for record in records:
        contracts = list(record.get("media_contracts", []))
        if record.get("media_contract"):
            contracts.append(record["media_contract"])
        for contract in contracts:
            media_id = contract.get("media_id")
            if not media_id:
                raise ValueError(f"native media contract lacks media_id: {record.get('block_id')}")
            if record.get("block_id") not in contract.get("source_block_ids", []):
                raise ValueError(f"native media contract does not include its owning block: {record.get('block_id')}")
            previous = atoms.get(media_id)
            if previous is not None and canonical_hash(previous) != canonical_hash(contract):
                raise ValueError(f"conflicting native media contracts: {media_id}")
            atoms[media_id] = contract
    if not atoms:
        raise ValueError("canonical ledger contains no native media contracts")
    normalized = {
        "schema_version": "normalized-media-candidates/1.1",
        "generator": VERSION,
        "generated_at": now(),
        "ledger_id": f"media::{header.get('ledger_id', canonical_hash(header)[:16])}",
        "canonical_ledger": {
            "path": str(ledger_path),
            "sha256": sha256_file(ledger_path),
            "snapshot_id": header.get("ledger_snapshot_id"),
            "payload_hash": header.get("current_ledger_hash"),
        },
        "decision_index": {"path": str(decision_index_path), "sha256": sha256_file(decision_index_path)},
        "source_pdf": {"path": str(source_pdf), "sha256": source_hash},
        "atoms": [atoms[key] for key in sorted(atoms)],
    }
    output_path = output_dir / "normalized_media_candidates.json"
    write_json(output_path, normalized)
    return output_path


class Checks:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, check_id: str, fn) -> None:
        try:
            evidence = fn() or {}
            self.rows.append({"check_id": check_id, "status": "passed", "evidence": evidence})
        except Exception as exc:
            self.rows.append({"check_id": check_id, "status": "failed", "detail": str(exc)})

    @property
    def passed(self) -> bool:
        return all(row["status"] == "passed" for row in self.rows)


def validate_contracts(ledger_path: Path, plan_path: Path) -> dict[str, Any]:
    ledger = read_json(ledger_path)
    plan = read_json(plan_path)
    checks = Checks()

    def identities() -> dict[str, Any]:
        atoms = ledger.get("atoms", [])
        ids = [item.get("media_id") for item in atoms]
        if ledger.get("schema_version") not in {"media-evidence-ledger/1.0", "media-evidence-ledger/1.1"} or not atoms or None in ids or len(ids) != len(set(ids)):
            raise ValueError("invalid ledger schema or media identities")
        if ledger.get("payload_hash") != payload_hash(ledger):
            raise ValueError("media ledger payload hash mismatch")
        return {"atoms": len(atoms)}

    def source_pdf_live() -> dict[str, Any]:
        source = ledger["source_pdf"]
        path = Path(source["path"])
        if not path.is_file() or sha256_file(path) != source["sha256"]:
            raise ValueError("source PDF bytes drifted")
        with fitz.open(path) as doc:
            if len(doc) != source["page_count"]:
                raise ValueError("source PDF page count drifted")
        return {"sha256": source["sha256"], "pages": source["page_count"]}

    def candidates_live() -> dict[str, Any]:
        failures: list[str] = []
        count = 0
        for atom in ledger["atoms"]:
            candidate_ids: set[str] = set()
            for candidate in atom["candidates"]:
                count += 1
                cid = candidate.get("candidate_id")
                if not cid or cid in candidate_ids:
                    failures.append(f"{atom['media_id']}:duplicate-candidate")
                candidate_ids.add(cid)
                representation = candidate.get("representation_type")
                if representation == "source_asset_image":
                    path = Path(candidate.get("resolved_path", ""))
                    if not path.is_file() or sha256_file(path) != candidate.get("artifact_sha256"):
                        failures.append(f"{atom['media_id']}:{cid}:asset-drift")
                elif representation == "source_region_image":
                    path = Path(candidate.get("crop_path", ""))
                    if not path.is_file() or sha256_file(path) != candidate.get("artifact_sha256"):
                        failures.append(f"{atom['media_id']}:{cid}:crop-drift")
                elif representation in STRUCTURED and canonical_hash(candidate.get("payload")) != candidate.get("artifact_sha256"):
                    failures.append(f"{atom['media_id']}:{cid}:structured-drift")
            if not candidate_ids:
                failures.append(f"{atom['media_id']}:no-candidates")
        if failures:
            raise ValueError(f"candidate evidence failures: {failures[:8]}")
        return {"candidates": count}

    def plan_binding() -> dict[str, Any]:
        if plan.get("schema_version") not in {"media-representation-plan/1.0", "media-representation-plan/1.1"} or plan.get("payload_hash") != payload_hash(plan):
            raise ValueError("invalid plan schema or payload hash")
        if plan.get("media_evidence_ledger_sha256") != sha256_file(ledger_path):
            raise ValueError("plan is not bound to exact media ledger bytes")
        atoms = {item["media_id"]: item for item in ledger["atoms"]}
        reps = plan.get("representations", [])
        rep_ids = [item.get("representation_id") for item in reps]
        media_ids = [item.get("media_id") for item in reps]
        if None in rep_ids or len(rep_ids) != len(set(rep_ids)) or Counter(media_ids) != Counter(atoms.keys()):
            raise ValueError("plan must contain one representation row per media atom")
        open_count = 0
        for rep in reps:
            atom = atoms[rep["media_id"]]
            candidates = {item["candidate_id"]: item for item in atom["candidates"]}
            if atom["inclusion_status"] == "excluded":
                if rep["status"] != "excluded" or rep["selected_candidate_id"] is not None:
                    raise ValueError(f"excluded atom has a representation: {rep['media_id']}")
                continue
            if rep["status"] == "needs_review":
                open_count += 1
                continue
            selected = candidates.get(rep.get("selected_candidate_id"))
            if rep["status"] != "closed" or not selected or selected.get("status") != "usable":
                raise ValueError(f"closed representation lacks usable evidence: {rep['media_id']}")
            if selected["representation_type"] != rep["representation_type"] or selected["artifact_sha256"] != rep.get("artifact_sha256"):
                raise ValueError(f"selected candidate binding mismatch: {rep['media_id']}")
            if rep["representation_type"] == "source_region_image":
                review = selected.get("human_review") or {}
                if review.get("status") != "closed" or review.get("observed_artifact_sha256") != selected["artifact_sha256"] or not review.get("decision_id"):
                    raise ValueError(f"source-region review not bound to crop: {rep['media_id']}")
            if rep["representation_type"] in STRUCTURED and selected.get("verification_status") != "verified":
                raise ValueError(f"structured representation is unverified: {rep['media_id']}")
        if open_count != plan.get("open_reviews"):
            raise ValueError("open review count mismatch")
        expected_status = "passed" if open_count == 0 else "needs_review"
        if plan.get("spec_status") != expected_status:
            raise ValueError("plan status does not match review closure")
        return {"representations": len(reps), "open_reviews": open_count}

    def canonical_binding() -> dict[str, Any]:
        if ledger.get("schema_version") == "media-evidence-ledger/1.0":
            return {"mode": "legacy_normalized_input"}
        canonical = ledger.get("canonical_ledger") or {}
        decision = ledger.get("decision_index") or {}
        canonical_path = Path(canonical.get("path", ""))
        decision_path = Path(decision.get("path", ""))
        if not canonical_path.is_file() or sha256_file(canonical_path) != canonical.get("sha256"):
            raise ValueError("canonical ledger bytes drifted")
        if not decision_path.is_file() or sha256_file(decision_path) != decision.get("sha256"):
            raise ValueError("canonical decision index bytes drifted")
        if plan.get("canonical_ledger_sha256") != canonical["sha256"] or plan.get("decision_index_sha256") != decision["sha256"]:
            raise ValueError("media representation plan is not bound to canonical ledger and decision index")
        decision_doc = read_json(decision_path)
        if decision_doc.get("spec_status") != "passed":
            raise ValueError("canonical decision index is not passed")
        decisions = {item.get("decision_id"): item for item in decision_doc.get("decisions", [])}
        open_decisions = [key for key, item in decisions.items() if item.get("status") in {"open", "stale", "invalidated"}]
        if open_decisions:
            raise ValueError(f"canonical decision index has unresolved decisions: {open_decisions[:8]}")
        referenced = sorted({ref for rep in plan.get("representations", []) for ref in rep.get("decision_refs", [])})
        invalid = [ref for ref in referenced if ref not in decisions or decisions[ref].get("status") not in {"closed", "superseded"}]
        if invalid:
            raise ValueError(f"media representation references invalid decisions: {invalid[:8]}")
        with canonical_path.open(encoding="utf-8") as stream:
            next(stream)
            canonical_records = [json.loads(line) for line in stream if line.strip()]
        native: dict[str, dict[str, Any]] = {}
        formal_native = 0
        for record in canonical_records:
            contracts = list(record.get("media_contracts", []))
            if record.get("media_contract"):
                contracts.append(record["media_contract"])
            for contract in contracts:
                media_id = contract.get("media_id")
                if not media_id:
                    continue
                previous = native.get(media_id)
                if previous is not None and canonical_hash(previous) != canonical_hash(contract):
                    raise ValueError(f"conflicting canonical media contracts: {media_id}")
                native[media_id] = contract
        reps = {item["media_id"]: item for item in plan.get("representations", [])}
        if set(native) != set(reps):
            raise ValueError("canonical media atom inventory differs from the representation plan")
        for media_id, contract in native.items():
            schema = contract.get("media_contract_schema_version")
            frozen = contract.get("frozen_representation")
            if schema == "canonical-media-atom/1.1":
                formal_native += 1
                if not frozen:
                    raise ValueError(f"formal native media atom lacks frozen representation: {media_id}")
                rep = reps[media_id]
                fields = ("status", "selected_candidate_id", "representation_type", "artifact_sha256", "rule_id")
                if any(frozen.get(field) != rep.get(field) for field in fields):
                    raise ValueError(f"formal native representation drifted after canonical commit: {media_id}")
        return {
            "canonical_ledger_sha256": canonical["sha256"],
            "decision_index_sha256": decision["sha256"],
            "decision_refs": len(referenced),
            "formal_native_atoms": formal_native,
        }

    checks.add("MSR-H01-ledger-identities", identities)
    checks.add("MSR-H02-source-pdf-live", source_pdf_live)
    checks.add("MSR-H03-candidate-evidence-live", candidates_live)
    checks.add("MSR-H04-representation-coverage-and-closure", plan_binding)
    checks.add("MSR-H05-canonical-ledger-and-decision-binding", canonical_binding)
    return {
        "schema_version": "media-representation-validation/1.0",
        "validator": VERSION,
        "generated_at": now(),
        "status": "passed" if checks.passed else "failed",
        "inputs": {
            "media_evidence_ledger": {"path": str(ledger_path.resolve()), "sha256": sha256_file(ledger_path)},
            "media_representation_plan": {"path": str(plan_path.resolve()), "sha256": sha256_file(plan_path)},
        },
        "summary": {
            "checks": len(checks.rows),
            "passed": sum(item["status"] == "passed" for item in checks.rows),
            "failed": sum(item["status"] == "failed" for item in checks.rows),
        },
        "checks": checks.rows,
    }


def validate_render_binding(ledger_path: Path, plan_path: Path, render_plan_path: Path) -> dict[str, Any]:
    contract_report = validate_contracts(ledger_path, plan_path)
    ledger = read_json(ledger_path)
    plan = read_json(plan_path)
    render_plan = read_json(render_plan_path)
    checks = Checks()

    def node_binding(node: dict[str, Any]) -> dict[str, Any] | None:
        return node.get("media_binding") or node.get("payload", {}).get("media_binding")

    def upstream_contract() -> dict[str, Any]:
        if contract_report["status"] != "passed":
            raise ValueError("media evidence/representation contract is not passed")
        if plan.get("spec_status") != "passed" or plan.get("open_reviews") != 0:
            raise ValueError("media representation plan is not closed")
        return {"media_contract_checks": contract_report["summary"]["checks"]}

    def binding_coverage() -> dict[str, Any]:
        plan_hash = sha256_file(plan_path)
        expected = {item["representation_id"]: item for item in plan["representations"] if item["status"] == "closed"}
        excluded = {item["representation_id"] for item in plan["representations"] if item["status"] == "excluded"}
        atoms = {item["media_id"]: item for item in ledger["atoms"]}
        observed: dict[str, list[dict[str, Any]]] = {}
        for node in render_plan.get("nodes", []):
            binding = node_binding(node)
            if not binding:
                continue
            if binding.get("media_representation_plan_sha256") != plan_hash:
                raise ValueError(f"render node media plan hash mismatch: {node.get('render_node_id')}")
            representation_id = binding.get("representation_id")
            observed.setdefault(representation_id, []).append(node)
        missing = sorted(set(expected) - set(observed))
        unexpected = sorted(set(observed) - set(expected))
        duplicated = sorted(key for key, value in observed.items() if len(value) != 1)
        if missing or unexpected or duplicated or excluded & set(observed):
            raise ValueError(f"media render coverage mismatch missing={missing[:4]} unexpected={unexpected[:4]} duplicated={duplicated[:4]}")
        for representation_id, representation in expected.items():
            node = observed[representation_id][0]
            binding = node_binding(node)
            if not binding:
                raise ValueError(f"render node lost media binding: {node.get('render_node_id')}")
            atom = atoms[representation["media_id"]]
            fields = {
                "media_id": representation["media_id"],
                "representation_id": representation_id,
                "representation_type": representation["representation_type"],
                "selected_candidate_id": representation["selected_candidate_id"],
                "artifact_sha256": representation["artifact_sha256"],
            }
            if any(binding.get(key) != value for key, value in fields.items()):
                raise ValueError(f"render binding differs from frozen media plan: {node.get('render_node_id')}")
            if not set(atom["source_block_ids"]).issubset(set(node.get("source_block_ids", []))):
                raise ValueError(f"render node loses media source blocks: {node.get('render_node_id')}")
            if representation["representation_type"] in {"source_asset_image", "source_region_image"} and node.get("target_construct") != representation["representation_type"]:
                raise ValueError(f"image representation changed at construct binding: {node.get('render_node_id')}")
            if node.get("payload_hash") and node["payload_hash"] != canonical_hash(node.get("payload")):
                raise ValueError(f"render payload hash mismatch: {node.get('render_node_id')}")
        return {"closed_media_representations": len(expected), "bound_render_nodes": len(observed)}

    checks.add("MSR-RB-H01-media-contract-passed", upstream_contract)
    checks.add("MSR-RB-H02-exact-render-binding", binding_coverage)
    return {
        "schema_version": "media-render-binding-validation/1.0",
        "validator": VERSION,
        "generated_at": now(),
        "status": "passed" if checks.passed else "failed",
        "inputs": {
            "media_evidence_ledger": {"path": str(ledger_path.resolve()), "sha256": sha256_file(ledger_path)},
            "media_representation_plan": {"path": str(plan_path.resolve()), "sha256": sha256_file(plan_path)},
            "render_plan": {"path": str(render_plan_path.resolve()), "sha256": sha256_file(render_plan_path)},
        },
        "summary": {"checks": len(checks.rows), "passed": sum(item["status"] == "passed" for item in checks.rows), "failed": sum(item["status"] == "failed" for item in checks.rows)},
        "checks": checks.rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--input", type=Path, required=True)
    build.add_argument("--source-pdf", type=Path, required=True)
    build.add_argument("--asset-root", action="append", default=[])
    build.add_argument("--output-dir", type=Path, required=True)
    canonical = sub.add_parser("build-from-canonical")
    canonical.add_argument("--canonical-ledger", type=Path, required=True)
    canonical.add_argument("--decision-index", type=Path, required=True)
    canonical.add_argument("--source-pdf", type=Path, required=True)
    canonical.add_argument("--asset-root", action="append", default=[])
    canonical.add_argument("--output-dir", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--ledger", type=Path, required=True)
    validate.add_argument("--plan", type=Path, required=True)
    validate.add_argument("--report", type=Path, required=True)
    binding = sub.add_parser("validate-render-binding")
    binding.add_argument("--ledger", type=Path, required=True)
    binding.add_argument("--plan", type=Path, required=True)
    binding.add_argument("--render-plan", type=Path, required=True)
    binding.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.command in {"build", "build-from-canonical"}:
        output = args.output_dir.resolve()
        if output.exists() and any(output.iterdir()):
            raise FileExistsError(f"refusing to overwrite nonempty output: {output}")
        output.mkdir(parents=True, exist_ok=True)
        input_path = (
            normalized_from_canonical(
                args.canonical_ledger.resolve(), args.decision_index.resolve(), args.source_pdf.resolve(), output
            )
            if args.command == "build-from-canonical"
            else args.input.resolve()
        )
        ledger, plan, queue = build_contracts(input_path, args.source_pdf.resolve(), parse_roots(args.asset_root), output)
        result = {"status": plan["spec_status"], "atoms": len(ledger["atoms"]), "open_reviews": queue["open_items"], "output_dir": str(output)}
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if plan["spec_status"] == "passed" else 3
    report = (
        validate_render_binding(args.ledger.resolve(), args.plan.resolve(), args.render_plan.resolve())
        if args.command == "validate-render-binding"
        else validate_contracts(args.ledger.resolve(), args.plan.resolve())
    )
    write_json(args.report.resolve(), report)
    print(json.dumps(report["summary"] | {"status": report["status"]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    sys.exit(main())
