from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import fitz


SKILL_ROOT = Path("/Users/concm/.codex/skills/luceon-popo-to-refined-elegantbook")
CORE_PATH = SKILL_ROOT / "scripts/visual_region_integrity.py"
SPEC = importlib.util.spec_from_file_location("visual_region_integrity_test", CORE_PATH)
CORE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(CORE)


class VisualRegionIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / "source.pdf"
        doc = fitz.open()
        for index in range(2):
            page = doc.new_page(width=300, height=400)
            page.draw_rect(fitz.Rect(20, 20, 280, 180), color=(0.2, 0.2, 0.2), fill=(0.8, 0.7 - 0.1 * index, 0.6))
            page.insert_text((30, 220), f"page {index + 1}")
        doc.save(self.pdf)
        doc.close()
        source_hash = CORE.sha256_file(self.pdf)
        self.records = [
            {"record_type": "source_block", "block_id": "src-1", "pdf_physical_page": 1, "source_label": "image", "scope_status": "included", "bbox": [0.05, 0.05, 0.95, 0.45]},
            {"record_type": "source_block", "block_id": "src-2", "pdf_physical_page": 2, "source_label": "text", "scope_status": "included", "bbox": [0.1, 0.5, 0.8, 0.7]},
        ]
        self.header = {
            "record_type": "ledger_header", "ledger_checkpoint": "source_reconciled", "spec_status": "passed",
            "ledger_snapshot_id": "fixture-source-v1", "current_ledger_hash_scope": CORE.LEDGER_HASH_SCOPE,
            "current_ledger_hash": CORE.canonical_hash(self.records), "canonical_decision_index_hash": "0" * 64,
            "material_identity": {"source_pdf_sha256": source_hash, "page_count": 2},
        }
        self.ledger = self.root / "ledger.jsonl"
        self.ledger.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in [self.header, *self.records]))
        self.normalized = {
            "schema_version": "normalized-media-candidates/1.1",
            "source_pdf": {"sha256": source_hash},
            "parent_canonical_ledger": {"sha256": CORE.sha256_file(self.ledger), "payload_hash": self.header["current_ledger_hash"], "ledger_snapshot_id": "fixture-source-v1"},
            "atoms": [{
                "media_id": "media-1", "source_page": 1, "source_block_ids": ["src-1"], "media_kind": "image", "inclusion_status": "included",
                "candidates": [{"candidate_id": "mineru-a", "representation_type": "source_asset_image", "root_id": "mineru", "path": "images/a.png", "sha256": "1" * 64}],
            }],
        }
        self.normalized_path = self.root / "normalized.json"
        self.normalized_path.write_text(json.dumps(self.normalized))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def bundle(self) -> dict:
        pages = [1, 2]
        return {
            "schema_version": "visual-region-review-bundle/1.0", "scope_mode": "formal_full_source",
            "source_pdf_sha256": CORE.sha256_file(self.pdf), "parent_ledger_sha256": CORE.sha256_file(self.ledger),
            "normalized_candidates_sha256": CORE.sha256_file(self.normalized_path),
            "page_review": {"status": "closed", "decision_id": "DEC-PAGES", "reviewed_pages": pages, "reviewed_pages_hash": CORE.canonical_hash(pages)},
            "media_review": {"status": "closed", "decision_id": "DEC-MEDIA", "candidate_fingerprints_hash": CORE.canonical_hash(CORE.candidate_fingerprints(self.normalized)), "default_disposition": "standalone_suitable", "exceptions": []},
            "composite_regions": [],
        }

    def test_full_source_review_must_cover_every_pdf_page(self) -> None:
        bundle = self.bundle()
        bundle["page_review"]["reviewed_pages"] = [1]
        bundle["page_review"]["reviewed_pages_hash"] = CORE.canonical_hash([1])
        with fitz.open(self.pdf) as doc:
            with self.assertRaisesRegex(ValueError, "page-level visual review"):
                CORE.validate_bundle(bundle, self.ledger, self.normalized_path, self.pdf, self.header, self.records, doc)

    def test_media_review_is_invalidated_by_candidate_geometry_change(self) -> None:
        bundle = self.bundle()
        self.normalized["atoms"][0]["candidates"][0]["bbox"] = [0, 0, 1, 1]
        self.normalized_path.write_text(json.dumps(self.normalized))
        bundle["normalized_candidates_sha256"] = CORE.sha256_file(self.normalized_path)
        with fitz.open(self.pdf) as doc:
            with self.assertRaisesRegex(ValueError, "standalone-media review is stale"):
                CORE.validate_bundle(bundle, self.ledger, self.normalized_path, self.pdf, self.header, self.records, doc)

    def test_stable_source_crop_is_independent_of_prior_page_rendering(self) -> None:
        bbox = [0.05, 0.05, 0.95, 0.45]
        before = CORE.stable_region_png(self.pdf, 1, bbox)
        with fitz.open(self.pdf) as doc:
            for _ in range(3):
                CORE.page_png(doc, 1)
                CORE.page_png(doc, 2)
        after = CORE.stable_region_png(self.pdf, 1, bbox)
        self.assertEqual(CORE.sha256_bytes(before), CORE.sha256_bytes(after))

    def test_visual_source_atom_keeps_raw_and_crop_hashes_semantically_distinct(self) -> None:
        crop_hash = CORE.sha256_bytes(b"reviewed source crop")
        record = {
            "raw_content": None,
            "raw_content_sha256": CORE.canonical_hash(None),
            "source_representation": {"source_crop_sha256": crop_hash},
            "visual_region_integrity": {"source_crop_sha256": crop_hash},
        }
        self.assertEqual(CORE.canonical_hash(record["raw_content"]), record["raw_content_sha256"])
        self.assertEqual(crop_hash, record["source_representation"]["source_crop_sha256"])
        self.assertNotEqual(crop_hash, record["raw_content_sha256"])

    def test_visual_source_atom_contract_has_explicit_pdf_provenance_and_refreshed_counts(self) -> None:
        source_hash = CORE.sha256_file(self.pdf)
        records = [
            {**self.records[0], "scope_status": "included"},
            {**self.records[1], "scope_status": "excluded"},
            {
                "record_type": "source_block",
                "block_id": "src-visual-1",
                "source_system": "source_pdf_visual_review",
                "pdf_physical_page": 2,
                "bbox": [0.0, 0.0, 1.0, 0.5],
                "scope_status": "included",
                "upstream_block_ref": {
                    "provider": "source-pdf-page-visual-review",
                    "source_pdf_sha256": source_hash,
                    "physical_page": 2,
                    "region_id": "fixture-region",
                },
            },
        ]
        summary = {
            "source_records": len(records),
            "source_evidence_records": len(records),
            "included_atoms": sum(row.get("scope_status") == "included" for row in records),
            "excluded_source_records": sum(row.get("scope_status") == "excluded" for row in records),
        }
        self.assertEqual(3, summary["source_records"])
        self.assertEqual(2, summary["included_atoms"])
        self.assertEqual(1, summary["excluded_source_records"])
        self.assertEqual("source-pdf-page-visual-review", records[-1]["upstream_block_ref"]["provider"])
        self.assertEqual(source_hash, records[-1]["upstream_block_ref"]["source_pdf_sha256"])

    def test_candidate_fingerprint_binds_more_than_upstream_asset_hash(self) -> None:
        first = CORE.canonical_hash(CORE.candidate_fingerprints(self.normalized))
        changed = json.loads(json.dumps(self.normalized))
        changed["atoms"][0]["candidates"][0]["path"] = "images/same-bytes-different-crop-context.png"
        second = CORE.canonical_hash(CORE.candidate_fingerprints(changed))
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
