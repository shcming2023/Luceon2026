import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import fitz
from PIL import Image


SKILL = Path(__file__).parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


PRODUCER = load("native_spec03_producer_test", "produce_native_spec03_media.py")
PROMOTION = load("promotion_gate_test", "stage_promotion_gate.py")
INTEGRITY = load("source_lineage_integrity_test", "build_source_lineage_integrity.py")
VISUAL = load("visual_region_integrity_test", "visual_region_integrity.py")


class NativeProducerAndPromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / "source.pdf"
        document = fitz.open()
        page = document.new_page(width=300, height=400)
        page.insert_text((50, 100), "media evidence")
        document.save(self.pdf)
        document.close()
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.image = self.assets / "figure.png"
        Image.new("RGB", (40, 30), "navy").save(self.image)
        self.parent_index = self.root / "parent-index.json"
        parent_index_doc = {
            "schema_version": "canonical-decision-index/1.1", "decision_index_id": "decision-index-test",
            "snapshot_id": "decision-parent", "version": 1, "spec_status": "passed",
            "acyclic_commit_rule": "evidence_or_parent_then_decision_index_D_then_child_artifact_L",
            "decisions": [{"decision_id": "DEC-PARENT", "event_file": "parent.jsonl", "rule_id": "TEST", "status": "closed"}],
            "summary": {"closed": 1, "open": 0, "stale": 0, "invalidated": 0},
        }
        self.parent_index.write_text(json.dumps(parent_index_doc), encoding="utf-8")
        raw = "source media"
        records = [{
            "record_type": "source_block", "block_id": "block-1", "pdf_physical_page": 1,
            "bbox": [0.1, 0.1, 0.4, 0.3], "raw_content": raw,
            "raw_content_sha256": PRODUCER.canonical_hash(raw), "scope_status": "included",
            "source_label": "image", "terminal_state": "source_reconciled", "review_required": False,
        }]
        header = {
            "record_type": "ledger_header", "schema_version": "canonical-block-ledger/2.0",
            "ledger_id": "ledger-test", "ledger_snapshot_id": "source-parent", "ledger_version": 1,
            "ledger_checkpoint": "source_reconciled", "spec_status": "passed",
            "current_ledger_hash": PRODUCER.canonical_hash(records), "summary": {"open_source_review_blocks": 0},
            "current_ledger_hash_scope": "canonical JSON hash of ordered source_block records",
            "canonical_decision_index_hash": PRODUCER.sha256_file(self.parent_index),
            "material_identity": {"source_pdf_sha256": PRODUCER.sha256_file(self.pdf), "page_count": 1},
        }
        self.parent_ledger = self.root / "parent-ledger.jsonl"
        self.parent_ledger.write_text("\n".join(json.dumps(item) for item in [header, *records]) + "\n", encoding="utf-8")
        self.normalized = self.root / "normalized.json"
        normalized_doc = {
            "schema_version": "normalized-media-candidates/1.0",
            "source_pdf": {"path": str(self.pdf), "sha256": PRODUCER.sha256_file(self.pdf)},
            "parent_canonical_ledger": {
                "path": str(self.parent_ledger), "sha256": PRODUCER.sha256_file(self.parent_ledger),
                "ledger_snapshot_id": "source-parent", "payload_hash": header["current_ledger_hash"],
            },
            "atoms": [{
                "media_id": "media-1", "source_block_ids": ["block-1"], "source_page": 1,
                "media_kind": "image", "inclusion_status": "included",
                "candidates": [{
                    "candidate_id": "asset-1", "representation_type": "source_asset_image",
                    "root_id": "assets", "path": "figure.png", "sha256": PRODUCER.sha256_file(self.image),
                }],
            }],
        }
        self.normalized.write_text(json.dumps(normalized_doc), encoding="utf-8")
        self.visual_review = self.root / "visual-review.json"
        reviewed_pages = [1]
        visual_review_doc = {
            "schema_version": "visual-region-review-bundle/1.0",
            "scope_mode": "formal_full_source",
            "source_pdf_sha256": PRODUCER.sha256_file(self.pdf),
            "parent_ledger_sha256": PRODUCER.sha256_file(self.parent_ledger),
            "normalized_candidates_sha256": PRODUCER.sha256_file(self.normalized),
            "page_review": {
                "status": "closed", "decision_id": "DEC-VISUAL-PAGES",
                "reviewed_pages": reviewed_pages,
                "reviewed_pages_hash": VISUAL.canonical_hash(reviewed_pages),
                "review_basis": "Complete source-page review for the unit-test fixture.",
            },
            "media_review": {
                "status": "closed", "decision_id": "DEC-VISUAL-MEDIA",
                "candidate_fingerprints_hash": VISUAL.canonical_hash(VISUAL.candidate_fingerprints(normalized_doc)),
                "default_disposition": "standalone_suitable", "exceptions": [],
            },
            "composite_regions": [],
        }
        self.visual_review.write_text(json.dumps(visual_review_doc), encoding="utf-8")
        self.visual_run = self.root / "visual-run"
        VISUAL.produce(argparse.Namespace(
            parent_ledger=self.parent_ledger, parent_decision_index=self.parent_index,
            normalized_candidates=self.normalized, source_pdf=self.pdf,
            review_bundle=self.visual_review, ledger_snapshot_id="source-visual-v2",
            ledger_version=2, decision_snapshot_id="decision-visual-v2",
            stage_decision_id="DEC-VISUAL-COMMIT", run_id="visual-run",
            report_id="visual-integrity-test", output_dir=self.visual_run,
        ))
        self.visual_ledger = self.visual_run / "ledgers/canonical_block_ledger.jsonl"
        self.visual_index = self.visual_run / "decisions/canonical_decision_index.json"
        self.visual_normalized = self.visual_run / "contracts/normalized_media_candidates.json"
        self.visual_report = self.visual_run / "reports/visual_region_integrity_report.json"
        self.audit = self.root / "source-order-audit.json"
        self.audit.write_text(json.dumps({
            "schema_version": "source-order-audit/2.0", "risk_pages": [], "risk_events": [],
            "pages": [{"physical_page": 1, "risk_event_ids": [], "ordered_block_ids": ["block-1"]}],
        }), encoding="utf-8")
        self.review = self.root / "source-review-closure.json"
        self.review.write_text(json.dumps({
            "schema_version": "source-review-closure/1.1", "status": "closed", "reviewed_pages": [1],
            "risk_pages": [], "closed_risk_event_ids": [], "evidence_refs": [str(self.audit)],
        }), encoding="utf-8")
        self.integrity_report = self.root / "source-integrity.json"
        report = INTEGRITY.evaluate(
            parent_ledger=self.visual_ledger, parent_decision_index=self.visual_index,
            normalized_candidates=self.visual_normalized, source_order_audit=self.audit,
            source_review_closure=self.review, scope_mode="formal_full_source",
            media_inventory_rule="source_label_compatible", report_id="source-integrity-test",
            visual_integrity_report=self.visual_report,
        )
        self.integrity_report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def producer_args(self, output, normalized=None, visual=False):
        parent_ledger = self.visual_ledger if visual else self.parent_ledger
        parent_index = self.visual_index if visual else self.parent_index
        normalized_candidates = normalized or (self.visual_normalized if visual else self.normalized)
        return argparse.Namespace(
            parent_ledger=parent_ledger, parent_decision_index=parent_index,
            normalized_candidates=normalized_candidates, source_pdf=self.pdf,
            asset_root=[f"assets={self.assets}"], ledger_snapshot_id="source-media-native-v2",
            ledger_version=2, decision_snapshot_id="decision-native-v2", stage_decision_id="DEC-NATIVE",
            run_id="native-run", output_dir=output,
            visual_integrity_report=self.visual_report if visual else None,
        )

    def test_native_producer_commits_without_render_plan_and_promotes(self):
        run = self.root / "native-run"
        manifest, code = PRODUCER.produce(self.producer_args(run, visual=True))
        self.assertEqual(0, code)
        self.assertEqual("formal_native", manifest["producer_mode"])
        self.assertFalse(manifest["render_plan_dependency"])
        ledger_lines = (run / "ledgers/canonical_block_ledger.jsonl").read_text().splitlines()
        record = json.loads(ledger_lines[1])
        self.assertEqual("canonical-media-atom/1.1", record["media_contracts"][0]["media_contract_schema_version"])
        self.assertEqual("source_asset_image", record["media_contracts"][0]["frozen_representation"]["representation_type"])
        promotion_path = self.root / "promotion.json"
        promotion, promotion_code = PROMOTION.evaluate_spec03_media(argparse.Namespace(
            run_dir=run, output=promotion_path, promotion_id="promotion-native", lineage_key="test/native",
            source_integrity_report=self.integrity_report,
        ))
        self.assertEqual(0, promotion_code)
        self.assertEqual("promoted", promotion["disposition"])
        self.assertEqual("formal_native", promotion["promotion_class"])

    def test_native_producer_rejects_historical_render_dependency(self):
        bad = json.loads(self.normalized.read_text())
        bad["atoms"][0]["imported_verified_render_node_id"] = "render-00001"
        bad_path = self.root / "bad-normalized.json"
        bad_path.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "downstream render keys"):
            PRODUCER.produce(self.producer_args(self.root / "bad-run", bad_path))

    def test_registry_does_not_activate_rejected_entry(self):
        run = self.root / "native-run-registry"
        PRODUCER.produce(self.producer_args(run, visual=True))
        promoted_path = self.root / "promoted.json"
        PROMOTION.evaluate_spec03_media(argparse.Namespace(
            run_dir=run, output=promoted_path, promotion_id="promotion-good", lineage_key="test/lineage",
            source_integrity_report=self.integrity_report,
        ))
        rejected = json.loads(promoted_path.read_text())
        rejected["promotion_id"] = "promotion-rejected"
        rejected["disposition"] = "rejected"
        rejected["promoted_artifacts"] = {}
        rejected["checks"][0] = {"check_id": "PG-H01-stage-shape", "status": "failed", "detail": "test rejection"}
        rejected_path = self.root / "rejected.json"
        rejected_path.write_text(json.dumps(rejected), encoding="utf-8")
        registry_path = self.root / "registry.json"
        registry, _ = PROMOTION.compose_registry(argparse.Namespace(
            output=registry_path, parent_registry=None,
            promotion_manifest=[str(promoted_path), str(rejected_path)],
            registry_id="registry-test", snapshot_id="registry-v1",
        ))
        self.assertEqual("promotion-good", registry["active_promotions"]["test/lineage"]["promotion_id"])
        selection = PROMOTION.verify_registry_selection(
            registry_path, "test/lineage", promoted_path, "spec03_media_contract"
        )
        self.assertEqual("promotion-good", selection["promotion"]["promotion_id"])
        with self.assertRaisesRegex(ValueError, "not the registry's active"):
            PROMOTION.verify_registry_selection(
                registry_path, "test/lineage", rejected_path, "spec03_media_contract"
            )
        with self.assertRaisesRegex(ValueError, "not a promoted"):
            PROMOTION.verify_promotion_manifest(rejected_path, "spec03_media_contract")

    def test_native_producer_accepts_one_atom_with_multiple_source_fragments(self):
        parent_index_doc = json.loads(self.parent_index.read_text())
        raw = "second fragment"
        rows = [json.loads(line) for line in self.parent_ledger.read_text().splitlines()]
        rows.append({
            "record_type": "source_block", "block_id": "block-2", "pdf_physical_page": 1,
            "bbox": [0.4, 0.1, 0.7, 0.3], "raw_content": raw,
            "raw_content_sha256": PRODUCER.canonical_hash(raw), "scope_status": "included",
            "source_label": "image", "terminal_state": "source_reconciled", "review_required": False,
        })
        rows[0]["current_ledger_hash"] = PRODUCER.canonical_hash(rows[1:])
        self.parent_ledger.write_text("\n".join(json.dumps(item) for item in rows) + "\n")
        normalized = json.loads(self.normalized.read_text())
        normalized["parent_canonical_ledger"].update({
            "sha256": PRODUCER.sha256_file(self.parent_ledger), "payload_hash": rows[0]["current_ledger_hash"],
        })
        normalized["atoms"][0]["source_block_ids"] = ["block-1", "block-2"]
        multi_path = self.root / "normalized-multi.json"
        multi_path.write_text(json.dumps(normalized))
        report = INTEGRITY.evaluate(
            parent_ledger=self.parent_ledger, parent_decision_index=self.parent_index,
            normalized_candidates=multi_path, source_order_audit=self.audit, source_review_closure=self.review,
            scope_mode="formal_full_source", media_inventory_rule="source_label_compatible", report_id="multi-fragment",
        )
        self.assertEqual(1, report["media_fragment_binding"]["multi_fragment_atoms"])
        manifest, code = PRODUCER.produce(self.producer_args(self.root / "multi-run", multi_path))
        self.assertEqual(0, code)
        self.assertEqual("passed", manifest["status"])

    def test_integrity_rejects_duplicate_fragment_assignment(self):
        normalized = json.loads(self.normalized.read_text())
        duplicate = json.loads(json.dumps(normalized["atoms"][0]))
        duplicate["media_id"] = "media-2"
        duplicate["candidates"][0]["candidate_id"] = "asset-2"
        normalized["atoms"].append(duplicate)
        path = self.root / "normalized-duplicate.json"
        path.write_text(json.dumps(normalized))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            INTEGRITY.evaluate(
                parent_ledger=self.parent_ledger, parent_decision_index=self.parent_index,
                normalized_candidates=path, source_order_audit=self.audit, source_review_closure=self.review,
                scope_mode="formal_full_source", media_inventory_rule="source_label_compatible", report_id="duplicate",
            )

    def test_integrity_rejects_signal_only_review_queue_item(self):
        audit = json.loads(self.audit.read_text())
        audit["risk_pages"] = [1]
        audit["risk_events"] = [{
            "event_id": "broad-layout-signal", "physical_page": 1, "trigger_kind": "reading_order_reanchor",
            "trigger_code": "MULTI_COLUMN_DETECTED", "action_kind": "queue_manual_review", "signal_only": True,
            "requires_human_review": True, "affected_source_refs": ["block-1"], "before_position": None,
            "after_position": None, "evidence_refs": [str(self.pdf)],
        }]
        path = self.root / "audit-signal.json"
        path.write_text(json.dumps(audit))
        with self.assertRaisesRegex(ValueError, "broad layout signal"):
            INTEGRITY.evaluate(
                parent_ledger=self.parent_ledger, parent_decision_index=self.parent_index,
                normalized_candidates=self.normalized, source_order_audit=path, source_review_closure=self.review,
                scope_mode="formal_full_source", media_inventory_rule="source_label_compatible", report_id="signal-only",
            )

    def test_native_producer_rejects_nonstandard_parent_ledger_identity(self):
        rows = [json.loads(line) for line in self.parent_ledger.read_text().splitlines()]
        rows[0]["current_ledger_hash_scope"] = "whole JSONL bytes"
        self.parent_ledger.write_text("\n".join(json.dumps(item) for item in rows) + "\n")
        with self.assertRaisesRegex(ValueError, "standard source_block-only"):
            PRODUCER.produce(self.producer_args(self.root / "bad-identity-run"))

    def test_formal_promotion_rejects_missing_source_integrity_report(self):
        run = self.root / "missing-integrity-run"
        PRODUCER.produce(self.producer_args(run, visual=True))
        promotion, code = PROMOTION.evaluate_spec03_media(argparse.Namespace(
            run_dir=run, output=self.root / "missing-integrity-promotion.json",
            promotion_id="missing-integrity", lineage_key="test/missing-integrity", source_integrity_report=None,
        ))
        self.assertEqual(4, code)
        failed = {item["check_id"] for item in promotion["checks"] if item["status"] == "failed"}
        self.assertIn("PG-H08-live-source-lineage-integrity", failed)

    def test_formal_promotion_rejects_missing_producer_capability(self):
        run = self.root / "missing-capability-run"
        PRODUCER.produce(self.producer_args(run, visual=True))
        (run / "precommit/execution_capability_manifest.json").unlink()
        promotion, code = PROMOTION.evaluate_spec03_media(argparse.Namespace(
            run_dir=run, output=self.root / "missing-capability-promotion.json",
            promotion_id="missing-capability", lineage_key="test/missing-capability",
            source_integrity_report=self.integrity_report,
        ))
        self.assertEqual(4, code)
        failed = {item["check_id"] for item in promotion["checks"] if item["status"] == "failed"}
        self.assertIn("PG-H12-live-producer-execution-capability", failed)

    def test_formal_promotion_rejects_missing_visual_integrity_contract(self):
        run = self.root / "missing-visual-run"
        PRODUCER.produce(self.producer_args(run))
        base_integrity_path = self.root / "base-source-integrity.json"
        base_integrity = INTEGRITY.evaluate(
            parent_ledger=self.parent_ledger, parent_decision_index=self.parent_index,
            normalized_candidates=self.normalized, source_order_audit=self.audit,
            source_review_closure=self.review, scope_mode="formal_full_source",
            media_inventory_rule="source_label_compatible", report_id="base-source-integrity",
        )
        base_integrity_path.write_text(json.dumps(base_integrity), encoding="utf-8")
        promotion, code = PROMOTION.evaluate_spec03_media(argparse.Namespace(
            run_dir=run, output=self.root / "missing-visual-promotion.json",
            promotion_id="missing-visual", lineage_key="test/missing-visual",
            source_integrity_report=base_integrity_path,
        ))
        self.assertEqual(4, code)
        failed = {item["check_id"] for item in promotion["checks"] if item["status"] == "failed"}
        self.assertIn("PG-H14-page-visual-and-composite-integrity", failed)

    def test_promotion_verification_rejects_evaluator_capability_drift(self):
        run = self.root / "evaluator-drift-run"
        PRODUCER.produce(self.producer_args(run, visual=True))
        promotion_path = self.root / "evaluator-drift-promotion.json"
        promotion, code = PROMOTION.evaluate_spec03_media(argparse.Namespace(
            run_dir=run, output=promotion_path, promotion_id="evaluator-drift",
            lineage_key="test/evaluator-drift", source_integrity_report=self.integrity_report,
        ))
        self.assertEqual(0, code)
        evaluator_path = Path(promotion["evaluator_capability"]["path"])
        evaluator_path.write_text(evaluator_path.read_text() + " ", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "evaluator capability"):
            PROMOTION.verify_promotion_manifest(promotion_path, "spec03_media_contract", "formal_native")

    def test_promotion_rejects_missing_inherited_parent_decision(self):
        run = self.root / "inheritance-run"
        PRODUCER.produce(self.producer_args(run, visual=True))
        child_path = run / "decisions/canonical_decision_index.json"
        child = json.loads(child_path.read_text())
        child["decisions"] = [item for item in child["decisions"] if item["decision_id"] != "DEC-PARENT"]
        child_path.write_text(json.dumps(child))
        stage_path = run / "manifests/spec03_media_contract_manifest.json"
        stage = json.loads(stage_path.read_text())
        stage["decision_index_D"]["sha256"] = PRODUCER.sha256_file(child_path)
        stage_path.write_text(json.dumps(stage))
        promotion, code = PROMOTION.evaluate_spec03_media(argparse.Namespace(
            run_dir=run, output=self.root / "inheritance-rejected.json", promotion_id="inheritance-rejected",
            lineage_key="test/inheritance", source_integrity_report=self.integrity_report,
        ))
        self.assertEqual(4, code)
        failed = {item["check_id"] for item in promotion["checks"] if item["status"] == "failed"}
        self.assertIn("PG-H09-cumulative-child-decision-inheritance", failed)


if __name__ == "__main__":
    unittest.main()
