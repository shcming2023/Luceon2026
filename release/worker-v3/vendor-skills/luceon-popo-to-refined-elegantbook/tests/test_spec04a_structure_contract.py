import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


STRUCTURE = load("spec04a_structure_test", "spec04a_structure_contract.py")
PROMOTION = load("spec04a_promotion_test", "stage_promotion_gate.py")


class Spec04AStructureContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / "source.pdf"
        self.pdf.write_bytes(b"source-pdf-evidence")
        self.evidence = self.root / "toc-page.png"
        self.evidence.write_bytes(b"rendered-source-page")
        self.parent_index = self.root / "parent-index.json"
        parent_index = {
            "schema_version": "canonical-decision-index/1.1", "decision_index_id": "decisions-test",
            "snapshot_id": "decisions-parent", "version": 1, "spec_status": "passed",
            "decisions": [{"decision_id": "DEC-PARENT", "event_file": "parent.jsonl", "rule_id": "TEST", "status": "closed"}],
            "summary": {"closed": 1, "open": 0, "stale": 0, "invalidated": 0},
        }
        self.parent_index.write_text(json.dumps(parent_index), encoding="utf-8")
        self.records = [
            self.record("title-unit", 1, "Unit One", "title"),
            self.record("title-local", 2, "Learning Goal", "title"),
            self.record("title-lesson", 3, "Lesson A", "title"),
            self.record("body", 4, "Body paragraph", "text"),
        ]
        header = {
            "record_type": "ledger_header", "schema_version": "canonical-block-ledger/2.0",
            "ledger_id": "ledger-test", "ledger_snapshot_id": "source-media-parent", "ledger_version": 2,
            "ledger_checkpoint": "source_reconciled", "spec_status": "passed", "run_mode": "formal",
            "current_ledger_hash": STRUCTURE.canonical_hash(self.records),
            "current_ledger_hash_scope": "canonical JSON hash of ordered source_block records including native media_contracts",
            "canonical_decision_index_hash": STRUCTURE.sha256_file(self.parent_index),
            "material_identity": {"source_pdf_sha256": STRUCTURE.sha256_file(self.pdf), "page_count": 1},
            "summary": {"included_atoms": 4, "open_reviews": 0},
        }
        self.parent_ledger = self.root / "parent-ledger.jsonl"
        STRUCTURE.write_jsonl(self.parent_ledger, [header, *self.records])
        stage = self.root / "parent-stage.json"
        stage.write_text(json.dumps({"stage": "spec03"}), encoding="utf-8")
        self.parent_promotion = self.root / "parent-promotion.json"
        promotion = {
            "schema_version": "stage-promotion-manifest/1.0", "promotion_id": "parent-promotion",
            "lineage_key": "test/spec03-media", "stage_kind": "spec03_media_contract",
            "disposition": "promoted", "promotion_class": "migration_compatibility",
            "run_dir": str(self.root),
            "stage_manifest": {"path": str(stage), "sha256": STRUCTURE.sha256_file(stage)},
            "promoted_artifacts": {"ledger_L": {"path": str(self.parent_ledger), "sha256": STRUCTURE.sha256_file(self.parent_ledger)}},
            "checks": [{"check_id": "legacy", "status": "passed"}],
        }
        self.parent_promotion.write_text(json.dumps(promotion), encoding="utf-8")
        self.registry = self.root / "registry.json"
        registry = {
            "schema_version": "promotion-registry/1.0", "registry_id": "registry", "snapshot_id": "registry-v1",
            "version": 1, "generated_at": "test", "parent_registry_ref": None, "parent_registry_sha256": None,
            "entries": [{
                "promotion_id": "parent-promotion", "lineage_key": "test/spec03-media", "disposition": "promoted",
                "promotion_class": "migration_compatibility", "manifest_path": str(self.parent_promotion),
                "manifest_sha256": STRUCTURE.sha256_file(self.parent_promotion), "run_dir": str(self.root),
                "stage_manifest_sha256": STRUCTURE.sha256_file(stage),
            }],
            "active_promotions": {"test/spec03-media": {
                "promotion_id": "parent-promotion", "manifest_path": str(self.parent_promotion),
                "manifest_sha256": STRUCTURE.sha256_file(self.parent_promotion), "promotion_class": "migration_compatibility",
            }},
            "selection_rule": "test", "payload_hash": "",
        }
        registry["payload_hash"] = STRUCTURE.canonical_hash({key: value for key, value in registry.items() if key not in {"generated_at", "payload_hash"}})
        self.registry.write_text(json.dumps(registry), encoding="utf-8")
        inventory = STRUCTURE.title_candidate_inventory(self.records)
        self.bundle = self.root / "outline-review.json"
        bundle = {
            "schema_version": "spec04a-outline-review-bundle/1.0", "review_id": "outline-test-v1",
            "parent_binding": {
                "ledger_snapshot_id": "source-media-parent", "ledger_payload_hash": header["current_ledger_hash"],
                "source_pdf_sha256": STRUCTURE.sha256_file(self.pdf), "promotion_id": "parent-promotion",
                "promotion_manifest_sha256": STRUCTURE.sha256_file(self.parent_promotion),
            },
            "source_outline_evidence": [{
                "evidence_id": "toc-page", "kind": "source_toc_page", "pdf_physical_page": 1,
                "path": str(self.evidence), "sha256": STRUCTURE.sha256_file(self.evidence),
            }],
            "source_toc_entries": [
                {"entry_id": "toc-unit", "title": "Unit One", "source_order": 1, "scope_status": "included", "source_outline_evidence_ids": ["toc-page"], "target_node_id": "unit", "match_status": "exact"},
                {"entry_id": "toc-lesson", "title": "Lesson A", "source_order": 2, "scope_status": "included", "source_outline_evidence_ids": ["toc-page"], "target_node_id": "lesson", "match_status": "exact"},
            ],
            "nodes": [
                self.node("unit", "Unit One", "unit", None, 0, "title-unit", ["title-unit"], ["toc-unit"]),
                self.node("lesson", "Lesson A", "lesson", "unit", 1, "title-lesson", ["title-lesson"], ["toc-lesson"]),
            ],
            "title_candidate_disposition": {"candidate_inventory_payload_hash": inventory["payload_hash"], "all_unassigned": "local_heading", "review_status": "closed"},
            "review": {"status": "closed", "open_items": 0, "decision_refs": ["REVIEW-TEST"]},
        }
        self.bundle.write_text(json.dumps(bundle), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def record(self, block_id, order, content, kind):
        return {
            "record_type": "source_block", "block_id": block_id, "pdf_physical_page": 1,
            "candidate_final_order": order, "scope_status": "included", "source_type": kind,
            "source_label": kind, "raw_content": content, "raw_content_sha256": STRUCTURE.canonical_hash(content),
            "review_required": False, "terminal_state": "source_reconciled",
        }

    def node(self, node_id, title, role, parent, level, anchor, evidence_blocks, toc_ids):
        return {
            "node_id": node_id, "title": title, "role": role, "parent_node_id": parent, "level": level,
            "anchor_block_id": anchor, "heading_evidence_block_ids": evidence_blocks,
            "source_outline_evidence_ids": ["toc-page"], "source_toc_entry_ids": toc_ids,
            "final_toc": {"include": True, "level": level, "title": title}, "review_status": "closed",
        }

    def args(self, output):
        return argparse.Namespace(
            parent_ledger=self.parent_ledger, parent_decision_index=self.parent_index, source_pdf=self.pdf,
            promotion_registry=self.registry, parent_promotion=self.parent_promotion,
            parent_lineage_key="test/spec03-media", review_bundle=self.bundle,
            ledger_snapshot_id="structure-v1", ledger_version=3, decision_snapshot_id="decisions-structure-v1",
            stage_decision_id="DEC-STRUCTURE", run_id="structure-run", output_dir=output,
        )

    def test_produces_closed_structure_and_promotes(self):
        run = self.root / "run"
        stage, code = STRUCTURE.produce(self.args(run))
        self.assertEqual(0, code)
        self.assertEqual("not_evaluated", stage["full_spec04_status"])
        live = STRUCTURE.validate_run(run)
        self.assertEqual(2, live["structure_nodes"])
        self.assertEqual(1, live["local_headings"])
        promotion_path = self.root / "structure-promotion.json"
        promotion, promotion_code = PROMOTION.evaluate_spec04a_structure(argparse.Namespace(
            run_dir=run, output=promotion_path, promotion_id="structure-promotion",
            lineage_key="test/spec04a-structure", evaluator_capability_output=None,
        ))
        self.assertEqual(0, promotion_code)
        self.assertEqual("promoted", promotion["disposition"])
        self.assertEqual(10, promotion["summary"]["passed"])

    def test_rejects_media_only_fixture_as_structure_truth(self):
        rows = [json.loads(line) for line in self.parent_ledger.read_text().splitlines()]
        rows[0]["run_mode"] = "regression_fixture"
        rows[0]["capability_status"] = "media_contract_test_only"
        STRUCTURE.write_jsonl(self.parent_ledger, rows)
        promotion = json.loads(self.parent_promotion.read_text())
        promotion["promoted_artifacts"]["ledger_L"]["sha256"] = STRUCTURE.sha256_file(self.parent_ledger)
        self.parent_promotion.write_text(json.dumps(promotion))
        registry = json.loads(self.registry.read_text())
        new_hash = STRUCTURE.sha256_file(self.parent_promotion)
        registry["entries"][0]["manifest_sha256"] = new_hash
        registry["active_promotions"]["test/spec03-media"]["manifest_sha256"] = new_hash
        registry["payload_hash"] = STRUCTURE.canonical_hash({key: value for key, value in registry.items() if key not in {"generated_at", "payload_hash"}})
        self.registry.write_text(json.dumps(registry))
        bundle = json.loads(self.bundle.read_text())
        bundle["parent_binding"]["promotion_manifest_sha256"] = new_hash
        self.bundle.write_text(json.dumps(bundle))
        with self.assertRaisesRegex(ValueError, "STRUCTURE_INPUT_SCOPE_INSUFFICIENT"):
            STRUCTURE.produce(self.args(self.root / "fixture-run"))

    def test_rejects_hierarchy_level_jump(self):
        bundle = json.loads(self.bundle.read_text())
        bundle["nodes"][1]["level"] = 2
        bundle["nodes"][1]["final_toc"]["level"] = 2
        self.bundle.write_text(json.dumps(bundle))
        with self.assertRaisesRegex(ValueError, "level jump"):
            STRUCTURE.produce(self.args(self.root / "bad-hierarchy"))

    def test_rejects_title_inventory_drift(self):
        rows = [json.loads(line) for line in self.parent_ledger.read_text().splitlines()]
        rows.insert(-1, self.record("new-title", 4, "New Structural Candidate", "title"))
        rows[0]["current_ledger_hash"] = STRUCTURE.canonical_hash(rows[1:])
        STRUCTURE.write_jsonl(self.parent_ledger, rows)
        promotion = json.loads(self.parent_promotion.read_text())
        promotion["promoted_artifacts"]["ledger_L"]["sha256"] = STRUCTURE.sha256_file(self.parent_ledger)
        self.parent_promotion.write_text(json.dumps(promotion))
        registry = json.loads(self.registry.read_text())
        new_hash = STRUCTURE.sha256_file(self.parent_promotion)
        registry["entries"][0]["manifest_sha256"] = new_hash
        registry["active_promotions"]["test/spec03-media"]["manifest_sha256"] = new_hash
        registry["payload_hash"] = STRUCTURE.canonical_hash({key: value for key, value in registry.items() if key not in {"generated_at", "payload_hash"}})
        self.registry.write_text(json.dumps(registry))
        bundle = json.loads(self.bundle.read_text())
        bundle["parent_binding"]["ledger_payload_hash"] = rows[0]["current_ledger_hash"]
        bundle["parent_binding"]["promotion_manifest_sha256"] = new_hash
        self.bundle.write_text(json.dumps(bundle))
        with self.assertRaisesRegex(ValueError, "title candidate inventory changed"):
            STRUCTURE.produce(self.args(self.root / "inventory-drift"))

    def test_frozen_ancestor_policy_preserves_hashes_without_rehashing_old_code_inputs(self):
        execution = PROMOTION.load_execution_core()
        resource = self.root / "book-config.json"
        resource.write_text('{"version": 1}', encoding="utf-8")
        capability_path = self.root / "historical-capability.json"
        capability = execution.build_manifest(
            manifest_id="historical-capability",
            skill_root=SKILL,
            entrypoints=[("producer", SKILL / "scripts/spec04a_structure_contract.py")],
            resources=[("book_configuration", resource)],
            invocation=["producer.py"],
            producer="historical-test",
        )
        STRUCTURE.write_json(capability_path, capability)
        resource.write_text('{"version": 2}', encoding="utf-8")
        artifact = self.root / "artifact.json"
        artifact.write_text('{"frozen": true}', encoding="utf-8")
        stage = self.root / "stage-v11.json"
        stage.write_text('{"stage": "spec03"}', encoding="utf-8")
        promotion_path = self.root / "promotion-v11.json"
        STRUCTURE.write_json(promotion_path, {
            "schema_version": "stage-promotion-manifest/1.1",
            "promotion_id": "ancestor-v1",
            "lineage_key": "test/spec03-media",
            "stage_kind": "spec03_media_contract",
            "disposition": "promoted",
            "promotion_class": "formal_native",
            "producer_execution_provenance": "live_verified",
            "stage_manifest": {"path": str(stage), "sha256": STRUCTURE.sha256_file(stage)},
            "evaluator_capability": {"path": str(capability_path), "sha256": STRUCTURE.sha256_file(capability_path)},
            "promoted_artifacts": {
                "producer_execution_capability": {"path": str(capability_path), "sha256": STRUCTURE.sha256_file(capability_path)},
                "ledger_L": {"path": str(artifact), "sha256": STRUCTURE.sha256_file(artifact)},
            },
        })
        with self.assertRaisesRegex(ValueError, "execution capability drift"):
            PROMOTION.verify_promotion_manifest(promotion_path, "spec03_media_contract", capability_verification="live")
        frozen = PROMOTION.verify_promotion_manifest(
            promotion_path, "spec03_media_contract", capability_verification="frozen"
        )
        self.assertEqual("ancestor-v1", frozen["promotion_id"])


if __name__ == "__main__":
    unittest.main()
