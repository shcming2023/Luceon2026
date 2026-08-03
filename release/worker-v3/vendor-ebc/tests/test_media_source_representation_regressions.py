import json
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = Path("/Users/concm/.codex/skills/luceon-popo-to-refined-elegantbook/scripts/media_source_representation.py")
SPEC = importlib.util.spec_from_file_location("media_source_representation_workspace", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)

BINDER_SCRIPT = ROOT / "scripts/bind_normalized_media_to_source_ledger.py"
BINDER_SPEC = importlib.util.spec_from_file_location("bind_normalized_media_to_source_ledger_test", BINDER_SCRIPT)
BINDER = importlib.util.module_from_spec(BINDER_SPEC)
assert BINDER_SPEC.loader
BINDER_SPEC.loader.exec_module(BINDER)


class MediaRepresentationWorkspaceRegressions(unittest.TestCase):
    def test_provider_formula_label_is_compatible_with_canonical_equation_label(self):
        self.assertEqual({"formula", "equation"}, BINDER.compatible_source_labels("formula"))
        self.assertEqual({"table"}, BINDER.compatible_source_labels("table"))

    def test_promotion_gate_rejects_self_report_only_and_selects_verified_runs(self):
        rejected = json.loads((ROOT / "promotions/golden-sample-001/source-media-v1.promotion.json").read_text(encoding="utf-8"))
        promoted = json.loads((ROOT / "promotions/golden-sample-001/source-media-v2.promotion.json").read_text(encoding="utf-8"))
        native = json.loads((ROOT / "promotions/amc8-solutions/native-media-v1.promotion.json").read_text(encoding="utf-8"))
        registry = json.loads((ROOT / "promotions/registry-v1.json").read_text(encoding="utf-8"))
        self.assertEqual("rejected", rejected["disposition"])
        self.assertTrue(any(row["check_id"] == "PG-H03-decision-closure" and row["status"] == "failed" for row in rejected["checks"]))
        self.assertEqual("promoted", promoted["disposition"])
        self.assertEqual("migration_compatibility", promoted["promotion_class"])
        self.assertEqual("promoted", native["disposition"])
        self.assertEqual("formal_native", native["promotion_class"])
        self.assertEqual("pg-golden-source-media-v2", registry["active_promotions"]["golden-sample-001/spec03-media"]["promotion_id"])
        self.assertEqual("pg-amc8-native-media-v1", registry["active_promotions"]["amc8-solutions/spec03-media-regression"]["promotion_id"])

    def test_amc8_native_spec03_producer_has_no_historical_render_dependency(self):
        run = ROOT / "regression_samples/media-source-representation/sample-002-amc8-solutions/runs/native-media-v1"
        manifest = json.loads((run / "manifests/spec03_media_contract_manifest.json").read_text(encoding="utf-8"))
        plan = json.loads((run / "media/media_representation_plan.json").read_text(encoding="utf-8"))
        self.assertEqual("formal_native", manifest["producer_mode"])
        self.assertFalse(manifest["render_plan_dependency"])
        self.assertEqual(23, plan["summary"]["representations"])
        self.assertEqual({"source_asset_image": 23}, plan["summary"]["types"])
        ledger_text = (run / "ledgers/canonical_block_ledger.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("render_plan.json", ledger_text)
        records = [json.loads(line) for line in ledger_text.splitlines()[1:]]
        contracts = {item["media_id"]: item for record in records for item in record.get("media_contracts", [])}
        self.assertEqual(23, len(contracts))
        self.assertTrue(all(item["media_contract_schema_version"] == "canonical-media-atom/1.1" for item in contracts.values()))
        self.assertTrue(all(item["frozen_representation"]["representation_type"] == "source_asset_image" for item in contracts.values()))

    def test_first_golden_native_media_contract_and_build_snapshot(self):
        runs = ROOT / "golden_samples/golden-sample-001/runs"
        source_run = runs / "source-media-v2"
        semantic_run = runs / "semantic-media-v1"
        compile_run = runs / "compile-media-v1"
        source_manifest = json.loads((source_run / "manifests/spec03_media_contract_manifest.json").read_text(encoding="utf-8"))
        media_plan = json.loads((source_run / "media/media_representation_plan.json").read_text(encoding="utf-8"))
        binding = json.loads((semantic_run / "reports/media_render_binding_validation.json").read_text(encoding="utf-8"))
        drift = json.loads((semantic_run / "reports/media_binding_drift_report.json").read_text(encoding="utf-8"))
        compile_report = json.loads((compile_run / "reports/compile_report.json").read_text(encoding="utf-8"))
        build_manifest = json.loads((compile_run / "manifests/build_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("passed", source_manifest["status"])
        self.assertEqual(1184, media_plan["summary"]["representations"])
        self.assertEqual({"source_asset_image": 442, "source_region_image": 204, "structured_formula": 538}, media_plan["summary"]["types"])
        self.assertEqual("passed", binding["status"])
        self.assertEqual(0, drift["semantic_construct_changes"])
        self.assertEqual(0, drift["construct_parameter_changes"])
        self.assertEqual("passed", compile_report["spec_status"])
        self.assertEqual(287, compile_report["visual_equivalence"]["pixel_identical_pages"])
        self.assertEqual("passed", build_manifest["status"])
        self.assertEqual(
            MODULE.sha256_file(runs / "compile-v8/delivery/elegantbook-project.zip"),
            MODULE.sha256_file(compile_run / "delivery/elegantbook-project.zip"),
        )

    def test_golden_sample_defects_are_bound_to_corrected_evidence(self):
        path = ROOT / "refactor/media-source-representation-v1/golden_sample_001_regression_inventory.json"
        inventory = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("passed", inventory["status"])
        self.assertEqual(8, inventory["summary"]["crop_regressions"])
        self.assertEqual(4, inventory["summary"]["chart_regressions"])
        for row in inventory["blank_or_truncated_crop_regressions"]:
            self.assertEqual(row["historical_failure"]["render_node_id"], row["corrected_evidence"]["render_node_id"])
            self.assertEqual(64, len(row["corrected_evidence"]["crop_sha256"]))
        for row in inventory["chart_residue_regressions"]:
            self.assertEqual("source_asset_image", row["corrected_evidence"]["after"]["target_construct"])

    def test_golden_wrong_bbox_interpretation_and_chart_residue_exercise_new_core(self):
        inventory = json.loads((ROOT / "refactor/media-source-representation-v1/golden_sample_001_regression_inventory.json").read_text(encoding="utf-8"))
        source_pdf = ROOT / "新教材全解 五上 数学.pdf"
        source_hash = MODULE.sha256_file(source_pdf)
        wrong_bbox_atoms = []
        for index, row in enumerate(inventory["blank_or_truncated_crop_regressions"], 1):
            failure = row["historical_failure"]
            wrong_bbox_atoms.append({
                "media_id": f"historical-crop-{index}",
                "source_block_ids": [failure["block_id"]],
                "inclusion_status": "included",
                "media_kind": "formula",
                "source_page": failure["source_pdf_page"],
                "requested_candidate_id": "wrong-coordinate-region",
                "candidates": [{
                    "candidate_id": "wrong-coordinate-region",
                    "representation_type": "source_region_image",
                    "bbox": failure["source_bbox"],
                    "bbox_coordinate_space": "pdf_mediabox_normalized_0_1_top_left",
                }],
            })
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            input_path = temporary / "wrong-bbox.json"
            input_path.write_text(json.dumps({"schema_version": "normalized-media-candidates/1.0", "source_pdf": {"path": str(source_pdf), "sha256": source_hash}, "atoms": wrong_bbox_atoms}), encoding="utf-8")
            ledger, plan, queue = MODULE.build_contracts(input_path, source_pdf, {}, temporary / "wrong-bbox")
            self.assertEqual("needs_review", plan["spec_status"])
            self.assertEqual(8, queue["open_items"])
            self.assertTrue(all(atom["candidates"][0]["status"] == "invalid" for atom in ledger["atoms"]))

        mineru_roots = [path for path in (ROOT / "golden_samples/golden-sample-001/runs/intake-v2/inputs/mineru").iterdir() if path.is_dir()]
        self.assertEqual(1, len(mineru_roots))
        chart_atoms = []
        for index, row in enumerate(inventory["chart_residue_regressions"], 1):
            failure, correction = row["historical_failure"], row["corrected_evidence"]
            transcription = {"format": "markdown", "value": failure["visible_excerpt"]}
            chart_atoms.append({
                "media_id": f"historical-chart-{index}",
                "source_block_ids": [failure["block_id"]],
                "inclusion_status": "included",
                "media_kind": "chart",
                "source_page": failure["source_pdf_page"],
                "candidates": [
                    {"candidate_id": "corrected-source-asset", "representation_type": "source_asset_image", "root_id": "mineru", "path": correction["asset_ref"], "sha256": correction["asset_sha256"]},
                    {"candidate_id": "historical-markdown-residue", "representation_type": "structured_chart", "payload": transcription, "payload_sha256": MODULE.canonical_hash(transcription), "verification_status": "candidate_unverified", "verification_refs": []},
                ],
            })
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            input_path = temporary / "charts.json"
            input_path.write_text(json.dumps({"schema_version": "normalized-media-candidates/1.0", "source_pdf": {"path": str(source_pdf), "sha256": source_hash}, "atoms": chart_atoms}), encoding="utf-8")
            ledger, plan, queue = MODULE.build_contracts(input_path, source_pdf, {"mineru": mineru_roots[0]}, temporary / "charts")
            self.assertEqual("passed", plan["spec_status"])
            self.assertEqual(0, queue["open_items"])
            self.assertTrue(all(row["representation_type"] == "source_asset_image" for row in plan["representations"]))
            self.assertTrue(all(next(candidate for candidate in atom["candidates"] if candidate["candidate_id"] == "historical-markdown-residue")["status"] == "needs_review" for atom in ledger["atoms"]))

    def test_different_material_contract_passes_without_promoting_residue(self):
        base = ROOT / "regression_samples/media-source-representation/sample-002-amc8-solutions/runs/intake-v1"
        intake = json.loads((base / "contracts/input_contract.json").read_text(encoding="utf-8"))
        validation = json.loads((base / "media-contract-v2/media_representation_validation.json").read_text(encoding="utf-8"))
        ledger = json.loads((base / "media-contract-v2/media_evidence_ledger.json").read_text(encoding="utf-8"))
        plan = json.loads((base / "media-contract-v2/media_representation_plan.json").read_text(encoding="utf-8"))
        self.assertEqual("passed", intake["status"])
        self.assertEqual("passed", validation["status"])
        self.assertEqual(23, ledger["summary"]["atoms"])
        self.assertEqual({"chart": 4, "formula": 3, "image": 15, "table": 1}, ledger["summary"]["media_kinds"])
        selected = {row["media_id"]: row["representation_type"] for row in plan["representations"]}
        for atom in ledger["atoms"]:
            if atom["media_kind"] in {"chart", "formula", "table"}:
                unverified = [candidate for candidate in atom["candidates"] if candidate["representation_type"].startswith("structured_")]
                self.assertTrue(unverified)
                self.assertTrue(all(candidate["status"] == "needs_review" for candidate in unverified))
                self.assertEqual("source_asset_image", selected[atom["media_id"]])


if __name__ == "__main__":
    unittest.main()
