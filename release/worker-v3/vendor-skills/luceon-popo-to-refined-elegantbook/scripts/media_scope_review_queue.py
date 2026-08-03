#!/usr/bin/env python3
"""Build a fail-open review queue for possible non-body media.

This scanner never changes scope. It uses only generic geometric and raster
signals to make human review cheaper; every exclusion still requires a closed
source-backed Spec 02/03 decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


VERSION = "media-scope-review-queue/1.0.0"
SCHEMA = "media-scope-review-queue/1.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def raster_features(path: Path) -> dict[str, Any]:
    with Image.open(path) as source:
        image = source.convert("RGB")
        width, height = image.size
        sample = image.resize((64, 64), Image.Resampling.BILINEAR)
        colored = dark = nonwhite = 0
        pixels_view = sample.load()
        for y in range(64):
            for x in range(64):
                red, green, blue = pixels_view[x, y]
                maximum = max(red, green, blue)
                minimum = min(red, green, blue)
                colored += maximum > 90 and maximum - minimum > 45
                dark += maximum < 96
                nonwhite += minimum < 242
    pixels = 64 * 64
    return {
        "width_px": width,
        "height_px": height,
        "aspect_ratio": round(width / max(height, 1), 6),
        "sample_saturated_ratio": round(colored / pixels, 6),
        "sample_dark_ratio": round(dark / pixels, 6),
        "sample_nonwhite_ratio": round(nonwhite / pixels, 6),
    }


def candidate_reasons(atom: dict[str, Any], features: dict[str, Any]) -> list[str]:
    bbox = atom.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return []
    x0, y0, x1, y1 = bbox
    area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    near_edge = x0 <= 0.12 or x1 >= 0.88 or y0 <= 0.12 or y1 >= 0.88
    compact = area <= 0.01
    very_compact = area <= 0.006
    saturated = features["sample_saturated_ratio"] >= 0.08
    aspect = features["aspect_ratio"]
    reasons: list[str] = []
    if compact and near_edge:
        reasons.append("edge_adjacent_compact_visual")
    if very_compact and saturated:
        reasons.append("compact_colored_ui_or_marker")
    if very_compact and (aspect >= 4.0 or aspect <= 0.25):
        reasons.append("thin_compact_fragment")
    return reasons


def build_queue(ledger_path: Path, plan_path: Path) -> dict[str, Any]:
    ledger_path = ledger_path.resolve()
    plan_path = plan_path.resolve()
    ledger = read_json(ledger_path)
    plan = read_json(plan_path)
    if ledger.get("schema_version") != "media-evidence-ledger/1.0":
        raise ValueError("unsupported media evidence ledger")
    if plan.get("schema_version") != "media-representation-plan/1.0":
        raise ValueError("unsupported media representation plan")
    if plan.get("media_evidence_ledger_sha256") != sha256_file(ledger_path):
        raise ValueError("media plan is not bound to the supplied evidence ledger")
    atoms = {item["media_id"]: item for item in ledger.get("atoms", [])}
    items: list[dict[str, Any]] = []
    for representation in plan.get("representations", []):
        if representation.get("status") != "closed" or representation.get("representation_type") != "source_asset_image":
            continue
        atom = atoms.get(representation.get("media_id"))
        if not atom or atom.get("inclusion_status") != "included" or atom.get("media_kind") != "image":
            continue
        selected = next(
            (candidate for candidate in atom.get("candidates", []) if candidate.get("candidate_id") == representation.get("selected_candidate_id")),
            None,
        )
        if not selected:
            raise ValueError(f"selected candidate missing: {representation.get('media_id')}")
        path = Path(selected.get("resolved_path", ""))
        if not path.is_file() or sha256_file(path) != representation.get("artifact_sha256"):
            raise ValueError(f"selected asset missing or drifted: {representation.get('media_id')}")
        features = raster_features(path)
        reasons = candidate_reasons(atom, features)
        if not reasons:
            continue
        bbox = atom["bbox"]
        items.append({
            "review_id": f"scope-review::{atom['media_id']}",
            "status": "open",
            "media_id": atom["media_id"],
            "source_block_ids": atom.get("source_block_ids", []),
            "source_page": atom.get("source_page"),
            "bbox": bbox,
            "bbox_coordinate_space": atom.get("bbox_coordinate_space"),
            "bbox_area_fraction": round((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 8),
            "selected_candidate_id": representation["selected_candidate_id"],
            "artifact_path": str(path),
            "artifact_sha256": representation["artifact_sha256"],
            "features": features,
            "candidate_reasons": reasons,
            "allowed_decisions": ["retain_body_media", "exclude_navigation_or_page_decoration", "exclude_ocr_noise_fragment"],
            "decision_rule": "no exclusion without direct source-page review and a closed immutable decision",
        })
    items.sort(key=lambda item: (item["source_page"], item["bbox"][1], item["bbox"][0], item["media_id"]))
    return {
        "schema_version": SCHEMA,
        "generated_by": VERSION,
        "status": "needs_review" if items else "closed",
        "inputs": {
            "media_evidence_ledger": {"path": str(ledger_path), "sha256": sha256_file(ledger_path)},
            "media_representation_plan": {"path": str(plan_path), "sha256": sha256_file(plan_path)},
        },
        "selection_policy": {
            "automatic_exclusion": False,
            "signals": ["bbox_area", "page_edge_adjacency", "raster_saturation", "aspect_ratio"],
            "sample_identity_inputs": "forbidden",
        },
        "open_items": len(items),
        "items": items,
    }


def write_contact_sheets(queue: dict[str, Any], output_dir: Path, per_sheet: int = 40) -> list[dict[str, Any]]:
    sheets_dir = output_dir / "contact_sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    manifests: list[dict[str, Any]] = []
    columns, tile_width, tile_height = 5, 300, 210
    for start in range(0, len(queue["items"]), per_sheet):
        batch = queue["items"][start:start + per_sheet]
        rows = (len(batch) + columns - 1) // columns
        sheet = Image.new("RGB", (columns * tile_width, rows * tile_height), "white")
        draw = ImageDraw.Draw(sheet)
        for offset, item in enumerate(batch):
            column, row = offset % columns, offset // columns
            left, top = column * tile_width, row * tile_height
            draw.rectangle((left, top, left + tile_width - 1, top + tile_height - 1), outline="#777777")
            label = f"p{item['source_page']} {item['media_id'][-10:]}\n" + ",".join(item["candidate_reasons"])
            draw.multiline_text((left + 5, top + 5), label, fill="black", font=font, spacing=2)
            with Image.open(item["artifact_path"]) as source:
                image = source.convert("RGB")
                image.thumbnail((tile_width - 12, tile_height - 50), Image.Resampling.LANCZOS)
                x = left + (tile_width - image.width) // 2
                y = top + 45 + (tile_height - 50 - image.height) // 2
                sheet.paste(image, (x, y))
        name = f"scope-candidates-{start + 1:04d}-{start + len(batch):04d}.jpg"
        path = sheets_dir / name
        sheet.save(path, "JPEG", quality=90, optimize=True)
        manifests.append({"path": f"contact_sheets/{name}", "sha256": sha256_file(path), "items": len(batch)})
    return manifests


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a non-destructive media scope review queue")
    parser.add_argument("--media-evidence-ledger", type=Path, required=True)
    parser.add_argument("--media-representation-plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite review run: {output}")
    output.mkdir(parents=True)
    queue = build_queue(args.media_evidence_ledger, args.media_representation_plan)
    queue["contact_sheets"] = write_contact_sheets(queue, output)
    (output / "media_scope_review_queue.json").write_text(
        json.dumps(queue, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": queue["status"], "open_items": queue["open_items"], "output_dir": str(output)}, ensure_ascii=False))
    return 0 if queue["status"] == "closed" else 3


if __name__ == "__main__":
    raise SystemExit(main())
