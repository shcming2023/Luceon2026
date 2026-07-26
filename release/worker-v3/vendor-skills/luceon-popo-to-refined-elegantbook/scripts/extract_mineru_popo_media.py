#!/usr/bin/env python3
"""Project MinerU v2 and MinerU-Popo media evidence into normalized atoms.

This is a provider adapter, not a semantic classifier.  It preserves upstream
labels/transcriptions as unverified candidates and never promotes them merely
because they parse as LaTeX, HTML, Markdown, or Mermaid.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

import fitz

TYPE_MAP = {
    "image": "image",
    "equation_interline": "formula",
    "table": "table",
    "chart": "chart",
}
STRUCTURED_MAP = {
    "equation_interline": "structured_formula",
    "table": "structured_table",
    "chart": "structured_chart",
}


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def manifest_images(manifest: dict[str, Any], prefix: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in manifest.get("objects", {}).get("images", []):
        name = PurePosixPath(item["object"]).name
        relative = item["object"][len(prefix):] if item["object"].startswith(prefix) else f"images/{name}"
        normalized = {"path": relative, "sha256": item["sha256"], "size_bytes": item["size_bytes"], "object": item["object"], "bucket": item["bucket"]}
        previous = result.get(name)
        if previous and previous["sha256"] != normalized["sha256"]:
            raise ValueError(f"manifest has filename collision with different bytes: {name}")
        result[name] = normalized
    return result


def normalize_bbox(raw: list[float], bbox_space: str, page_size: tuple[float, float]) -> list[float]:
    if len(raw) != 4:
        raise ValueError("MinerU v2 bbox must have four values")
    if bbox_space == "normalized_0_1":
        divisors = (1.0, 1.0, 1.0, 1.0)
    elif bbox_space == "normalized_0_1000":
        divisors = (1000.0, 1000.0, 1000.0, 1000.0)
    elif bbox_space == "cropbox_points_top_left":
        divisors = (page_size[0], page_size[1], page_size[0], page_size[1])
    else:
        raise ValueError(f"unsupported explicit MinerU bbox space: {bbox_space}")
    bbox = [round(float(value) / divisors[index], 8) for index, value in enumerate(raw)]
    if min(bbox) < 0 or max(bbox) > 1 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise ValueError(f"invalid normalized bbox: {raw}")
    return bbox


def iou(left: list[float], right: list[float]) -> float:
    x0, y0 = max(left[0], right[0]), max(left[1], right[1])
    x1, y1 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (left[2] - left[0]) * (left[3] - left[1]) + (right[2] - right[0]) * (right[3] - right[1]) - intersection
    return intersection / union if union else 0.0


def structured_payload(item: dict[str, Any]) -> Any | None:
    content = item.get("content") or {}
    if item["type"] == "equation_interline":
        return {"format": content.get("math_type"), "value": content.get("math_content")}
    if item["type"] == "table":
        return {"format": "html", "value": content.get("html"), "table_type": content.get("table_type")}
    if item["type"] == "chart":
        return {"format": "upstream_chart_transcription", "value": content.get("content"), "sub_type": item.get("sub_type")}
    return None


def extract(args: argparse.Namespace) -> dict[str, Any]:
    source_pdf = args.source_pdf.resolve()
    content_path = args.content_list_v2.resolve()
    popo_path = args.popo_raw.resolve()
    mineru_manifest_path = args.mineru_manifest.resolve()
    popo_manifest_path = args.popo_manifest.resolve()
    mineru_manifest = read(mineru_manifest_path)
    popo_manifest = read(popo_manifest_path)
    source_hash = sha(source_pdf)
    for manifest, label in ((mineru_manifest, "MinerU"), (popo_manifest, "Popo")):
        if manifest.get("source_pdf", {}).get("sha256") != source_hash:
            raise ValueError(f"{label} manifest source hash mismatch")
    mineru_prefix = mineru_manifest["stage_prefixes"]["mineru"]["prefix"]
    popo_prefix = popo_manifest["stage_prefixes"]["minerupopo"]["prefix"]
    mineru_images = manifest_images(mineru_manifest, mineru_prefix)
    popo_images = manifest_images(popo_manifest, popo_prefix)
    popo_rows = read(popo_path)
    popo_by_page: dict[int, list[dict[str, Any]]] = {}
    for row in popo_rows:
        if row.get("source_label") in {"image", "equation", "table", "chart"} and row.get("bbox"):
            popo_by_page.setdefault(int(row["page"]), []).append(row)

    source_doc = fitz.open(source_pdf)
    atoms: list[dict[str, Any]] = []
    for page_number, page in enumerate(read(content_path), 1):
        if page_number > len(source_doc):
            raise ValueError("content_list_v2 has more pages than the source PDF")
        page_size = (float(source_doc[page_number - 1].rect.width), float(source_doc[page_number - 1].rect.height))
        for ordinal, item in enumerate(page, 1):
            if item.get("type") not in TYPE_MAP:
                continue
            bbox = normalize_bbox(item["bbox"], args.bbox_space, page_size)
            content = item.get("content") or {}
            image_ref = (content.get("image_source") or {}).get("path")
            stable_source = {"provider": "mineru-content-list-v2", "page": page_number, "ordinal": ordinal, "type": item["type"], "bbox": bbox, "image_ref": image_ref}
            media_id = f"media-{canonical_hash(stable_source)[:24]}"
            candidates: list[dict[str, Any]] = []
            if image_ref:
                name = PurePosixPath(image_ref).name
                declared = mineru_images.get(name)
                if declared:
                    candidates.append({
                        "candidate_id": f"mineru-asset::{name}",
                        "representation_type": "source_asset_image",
                        "root_id": "mineru",
                        "path": declared["path"],
                        "sha256": declared["sha256"],
                        "upstream_refs": [{"provider": "mineru", "manifest": str(mineru_manifest_path), "bucket": declared["bucket"], "object": declared["object"]}],
                    })
                equivalent = popo_images.get(name)
                if equivalent:
                    candidates.append({
                        "candidate_id": f"popo-asset::{name}",
                        "representation_type": "source_asset_image",
                        "root_id": "popo",
                        "path": equivalent["path"],
                        "sha256": equivalent["sha256"],
                        "upstream_refs": [{"provider": "mineru-popo", "manifest": str(popo_manifest_path), "bucket": equivalent["bucket"], "object": equivalent["object"]}],
                    })
            candidates.append({
                "candidate_id": "source-pdf-region",
                "representation_type": "source_region_image",
                "source_page": page_number,
                "bbox": bbox,
                "bbox_coordinate_space": "pdf_cropbox_normalized_0_1_top_left",
                "upstream_refs": [{"provider": "mineru-content-list-v2", "path": str(content_path), "page": page_number, "ordinal": ordinal}],
            })
            payload = structured_payload(item)
            if payload is not None:
                candidates.append({
                    "candidate_id": "mineru-structured-transcription",
                    "representation_type": STRUCTURED_MAP[item["type"]],
                    "payload": payload,
                    "payload_sha256": canonical_hash(payload),
                    "verification_status": "candidate_unverified",
                    "verification_refs": [],
                    "upstream_refs": [{"provider": "mineru-content-list-v2", "path": str(content_path), "page": page_number, "ordinal": ordinal}],
                })
            label = "equation" if item["type"] == "equation_interline" else item["type"]
            matches = [row for row in popo_by_page.get(page_number, []) if row.get("source_label") == label]
            if matches:
                best = max(matches, key=lambda row: iou(bbox, row["bbox"]))
                overlap = iou(bbox, best["bbox"])
                if overlap >= 0.5 and best.get("content"):
                    representation = STRUCTURED_MAP.get(item["type"])
                    if representation:
                        popo_payload = {"format": f"popo_{label}_transcription", "value": best["content"]}
                        candidates.append({
                            "candidate_id": "popo-structured-transcription",
                            "representation_type": representation,
                            "payload": popo_payload,
                            "payload_sha256": canonical_hash(popo_payload),
                            "verification_status": "candidate_unverified",
                            "verification_refs": [],
                            "match_iou": round(overlap, 6),
                            "upstream_refs": [{"provider": "mineru-popo", "path": str(popo_path), "source_id": best.get("source_id")}],
                        })
            atoms.append({
                "media_id": media_id,
                "source_block_ids": [f"mineru-v2::p{page_number}::n{ordinal}"],
                "inclusion_status": "included",
                "media_kind": TYPE_MAP[item["type"]],
                "source_page": page_number,
                "bbox": bbox,
                "bbox_coordinate_space": "pdf_cropbox_normalized_0_1_top_left",
                "upstream_type": item["type"],
                "upstream_sub_type": item.get("sub_type"),
                "candidates": candidates,
            })
    source_doc.close()
    if not atoms:
        raise ValueError("adapter found no media atoms")
    return {
        "schema_version": "normalized-media-candidates/1.0",
        "adapter": "extract-mineru-popo-media/1.1.0",
        "input_bbox_space": args.bbox_space,
        "ledger_id": f"media::{source_hash[:24]}",
        "source_pdf": {"path": str(source_pdf), "sha256": source_hash},
        "inputs": {
            "mineru_content_list_v2": {"path": str(content_path), "sha256": sha(content_path)},
            "mineru_manifest": {"path": str(mineru_manifest_path), "sha256": sha(mineru_manifest_path)},
            "popo_raw": {"path": str(popo_path), "sha256": sha(popo_path)},
            "popo_manifest": {"path": str(popo_manifest_path), "sha256": sha(popo_manifest_path)},
        },
        "atoms": atoms,
        "summary": {"atoms": len(atoms), "kinds": {kind: sum(atom["media_kind"] == kind for atom in atoms) for kind in sorted(set(atom["media_kind"] for atom in atoms))}},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-pdf", type=Path, required=True)
    parser.add_argument("--content-list-v2", type=Path, required=True)
    parser.add_argument("--popo-raw", type=Path, required=True)
    parser.add_argument("--mineru-manifest", type=Path, required=True)
    parser.add_argument("--popo-manifest", type=Path, required=True)
    parser.add_argument("--bbox-space", choices=["normalized_0_1", "normalized_0_1000", "cropbox_points_top_left"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = extract(args)
    write(args.output.resolve(), result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
