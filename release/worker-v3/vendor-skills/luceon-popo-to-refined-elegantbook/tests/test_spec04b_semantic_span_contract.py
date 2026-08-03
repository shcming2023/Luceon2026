import copy
import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL = Path(__file__).parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


SEMANTIC = load("spec04b_semantic_test", "spec04b_semantic_span_contract.py")


class Spec04BSemanticSpanContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / "source.pdf"
        self.pdf.write_bytes(b"source-pdf")
        self.page = self.root / "page-001.png"
        self.page.write_bytes(b"source-page-render")
        self.records = [
            self.record("chapter", 1, "title", heading="structure_node", structure=True),
            self.record("marker", 2, "title", heading="local_heading"),
            self.record("body", 3, "text"),
            self.record("local", 4, "title", heading="local_heading"),
            self.record("image", 5, "image", asset="asset.png"),
            self.record("plain", 6, "text"),
        ]
        self.header = {
            "record_type": "ledger_header", "ledger_snapshot_id": "spec04a-parent",
            "current_ledger_hash": SEMANTIC.canonical_hash(self.records),
            "material_identity": {"source_pdf_sha256": SEMANTIC.sha256_file(self.pdf)},
            "spec04a_structure": {"status": "passed", "full_spec04_status": "not_evaluated"},
        }
        self.parent = {
            "promotion_id": "spec04a-active", "manifest_sha256": "a" * 64,
            "source_outline_ledger": {"path": "outline.json", "sha256": "b" * 64},
            "final_toc_plan": {"path": "toc.json", "sha256": "c" * 64},
        }
        self.bundle = {
            "schema_version": "spec04b-semantic-review-bundle/1.0", "review_id": "semantic-test-v1",
            "parent_binding": {
                "ledger_snapshot_id": "spec04a-parent",
                "ledger_payload_hash": self.header["current_ledger_hash"],
                "source_pdf_sha256": SEMANTIC.sha256_file(self.pdf),
                "promotion_id": "spec04a-active", "promotion_manifest_sha256": "a" * 64,
                "source_outline_ledger_sha256": "b" * 64, "final_toc_plan_sha256": "c" * 64,
            },
            "review": {"status": "closed", "open_items": 0, "decision_refs": ["REVIEW-SEMANTIC"]},
            "source_evidence": [{
                "evidence_id": "page-1", "path": str(self.page),
                "sha256": SEMANTIC.sha256_file(self.page), "pdf_physical_page": 1,
            }],
            "teaching_groups": [{
                "group_id": "group-1", "marker_block_id": "marker", "body_block_ids": ["body"],
                "semantic_role": "method_note", "source_evidence_ids": ["page-1"],
                "relation_rule": {
                    "same_physical_page": True, "same_tree_path": True,
                    "allowed_body_source_types": ["text"],
                    "basis": "same_tree_path_and_spatial_proximity",
                },
                "review_status": "closed",
            }],
            "standalone_labels": [{
                "block_id": "local", "semantic_role": "standalone_prompt",
                "source_evidence_ids": ["page-1"], "review_status": "closed",
            }],
        }

    def tearDown(self):
        self.temp.cleanup()

    def record(self, block_id, order, source_type, heading=None, structure=False, asset=None):
        result = {
            "record_type": "source_block", "block_id": block_id, "scope_status": "included",
            "pdf_physical_page": 1, "candidate_final_order": order, "source_type": source_type,
            "raw_content": block_id, "raw_content_sha256": SEMANTIC.canonical_hash(block_id),
            "tree_context": {"node_path": [1, 2]}, "bbox": [0.1, order / 10, 0.9, order / 10 + 0.05],
            "bbox_basis": "normalized 0..1 top-left", "asset_ref": asset,
        }
        if heading:
            result["heading_disposition"] = heading
        if structure:
            result["structure_memberships"] = [{"node_id": "chapter-1"}]
        return result

    def validate(self, bundle=None):
        return SEMANTIC.validate_bundle(
            header=self.header, records=self.records, bundle=bundle or self.bundle,
            source_pdf=self.pdf, parent=self.parent,
        )

    def test_exact_partition_and_safe_degradation(self):
        contract, groups, queue = self.validate()
        assigned = [block_id for span in contract["spans"] for block_id in span["source_block_ids"]]
        self.assertEqual({item["block_id"] for item in self.records}, set(assigned))
        self.assertEqual(len(assigned), len(set(assigned)))
        dispositions = {span["semantic_disposition"] for span in contract["spans"]}
        self.assertTrue({"book_structure", "teaching_column", "standalone_semantic_label", "fragile_or_media", "plain_body"} <= dispositions)
        self.assertEqual(1, len(groups["groups"]))
        self.assertEqual(0, queue["open_items"])
        self.assertEqual("not_evaluated", contract["full_spec04_status"])

    def test_rejects_empty_teaching_group(self):
        bundle = copy.deepcopy(self.bundle)
        bundle["teaching_groups"][0]["body_block_ids"] = []
        with self.assertRaisesRegex(ValueError, "EMPTY_TEACHING_GROUP"):
            self.validate(bundle)

    def test_rejects_overlapping_group_membership(self):
        bundle = copy.deepcopy(self.bundle)
        duplicate = copy.deepcopy(bundle["teaching_groups"][0])
        duplicate["group_id"] = "group-2"
        bundle["teaching_groups"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "overlaps another group"):
            self.validate(bundle)

    def test_rejects_fragile_media_as_group_body(self):
        bundle = copy.deepcopy(self.bundle)
        bundle["teaching_groups"][0]["body_block_ids"] = ["image"]
        bundle["teaching_groups"][0]["relation_rule"]["allowed_body_source_types"] = ["image"]
        with self.assertRaisesRegex(ValueError, "unsafe body source types"):
            self.validate(bundle)

    def test_rejects_text_compatible_body_with_frozen_media_contract(self):
        records = copy.deepcopy(self.records)
        body = next(item for item in records if item["block_id"] == "body")
        body["source_label"] = "chart"
        body["media_contracts"] = [{"media_kind": "chart"}]
        with self.assertRaisesRegex(ValueError, "includes media"):
            SEMANTIC.validate_bundle(
                header=self.header, records=records, bundle=self.bundle,
                source_pdf=self.pdf, parent=self.parent,
            )

    def test_rejects_downstream_box_or_render_decision(self):
        bundle = copy.deepcopy(self.bundle)
        bundle["teaching_groups"][0]["target_construct"] = "example"
        with self.assertRaisesRegex(ValueError, "downstream keys"):
            self.validate(bundle)

    def test_accepts_visually_reviewed_text_marker_without_reclassifying_spec04a(self):
        records = copy.deepcopy(self.records)
        marker = next(item for item in records if item["block_id"] == "marker")
        marker["source_type"] = "text"
        marker.pop("heading_disposition")
        contract, groups, _ = SEMANTIC.validate_bundle(
            header=self.header, records=records, bundle=self.bundle,
            source_pdf=self.pdf, parent=self.parent,
        )
        self.assertEqual(1, len(groups["groups"]))
        self.assertTrue(any(span["semantic_disposition"] == "teaching_column" for span in contract["spans"]))

    def test_validate_run_cli_returns_success_for_dict_result(self):
        with patch.object(SEMANTIC, "validate_run", return_value={"status": "passed"}), patch(
            "sys.argv", ["spec04b_semantic_span_contract.py", "validate-run", "--run-dir", str(self.root)]
        ), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, SEMANTIC.main())


if __name__ == "__main__":
    unittest.main()
