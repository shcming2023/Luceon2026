import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import fitz
from PIL import Image, ImageDraw


SCRIPT = Path(__file__).parents[1] / "scripts" / "media_source_representation.py"
SPEC = importlib.util.spec_from_file_location("media_source_representation", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class MediaSourceRepresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / "source.pdf"
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        page.insert_text((60, 120), "source evidence 123", fontsize=18)
        doc.save(self.pdf)
        doc.close()

    def tearDown(self):
        self.temp.cleanup()

    def write_input(self, atoms, name="input.json"):
        path = self.root / name
        payload = {
            "schema_version": "normalized-media-candidates/1.0",
            "source_pdf": {"path": str(self.pdf), "sha256": MODULE.sha256_file(self.pdf)},
            "atoms": atoms,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def region_atom(self, coordinate="pdf_cropbox_normalized_0_1_top_left", review=None):
        candidate = {
            "candidate_id": "region",
            "representation_type": "source_region_image",
            "bbox": [0.15, 0.2, 0.85, 0.4],
            "bbox_coordinate_space": coordinate,
        }
        if review:
            candidate["human_review"] = review
        return {
            "media_id": "m-region",
            "source_block_ids": ["b-1"],
            "inclusion_status": "included",
            "media_kind": "formula",
            "source_page": 1,
            "candidates": [candidate],
            "requested_candidate_id": "region",
        }

    def test_source_region_requires_review_bound_to_exact_crop_hash(self):
        first_input = self.write_input([self.region_atom()])
        first_output = self.root / "first"
        _, first_plan, _ = MODULE.build_contracts(first_input, self.pdf, {}, first_output)
        self.assertEqual("needs_review", first_plan["spec_status"])
        first_ledger = json.loads((first_output / "media_evidence_ledger.json").read_text())
        crop_hash = first_ledger["atoms"][0]["candidates"][0]["artifact_sha256"]

        review = {"status": "closed", "decision_id": "DEC-1", "observed_artifact_sha256": crop_hash}
        second_input = self.write_input([self.region_atom(review=review)], "reviewed.json")
        second_output = self.root / "second"
        _, second_plan, _ = MODULE.build_contracts(second_input, self.pdf, {}, second_output)
        self.assertEqual("passed", second_plan["spec_status"])
        report = MODULE.validate_contracts(second_output / "media_evidence_ledger.json", second_output / "media_representation_plan.json")
        self.assertEqual("passed", report["status"])

    def test_unknown_coordinate_interpretation_blocks_wrong_bbox(self):
        input_path = self.write_input([self.region_atom("pdf_mediabox_normalized_0_1_top_left")])
        output = self.root / "bad-coordinate"
        ledger, plan, queue = MODULE.build_contracts(input_path, self.pdf, {}, output)
        self.assertEqual("needs_review", plan["spec_status"])
        self.assertEqual(1, queue["open_items"])
        self.assertEqual("invalid", ledger["atoms"][0]["candidates"][0]["status"])
        self.assertIn("candidate_assessment_failed", ledger["atoms"][0]["candidates"][0]["anomaly_flags"])

    def test_source_page_raster_recipe_reproduces_reviewed_artifact(self):
        pages = self.root / "pages"
        pages.mkdir()
        page_path = pages / "page-001.jpg"
        with fitz.open(self.pdf) as document:
            pix = document[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            pix.save(page_path)
        bbox = [0.15, 0.2, 0.85, 0.4]
        padding = {"x": 0.008, "y": 0.006}
        expected = self.root / "expected.png"
        with fitz.open(self.pdf) as document, Image.open(page_path) as page_image:
            box = MODULE.cropbox_bbox_to_raster_box(bbox, document[0], page_image.size, padding)
            page_image.crop(box).save(expected, "PNG", optimize=True)
        artifact_hash = MODULE.sha256_file(expected)
        atom = {
            "media_id": "m-raster-region",
            "source_block_ids": ["b-raster"],
            "inclusion_status": "included",
            "media_kind": "visual_region",
            "source_page": 1,
            "requested_candidate_id": "region",
            "candidates": [{
                "candidate_id": "region",
                "representation_type": "source_region_image",
                "source_page": 1,
                "bbox": bbox,
                "bbox_coordinate_space": "pdf_cropbox_normalized_0_1_top_left",
                "crop_recipe": "source_page_raster_cropbox_to_mediabox",
                "raster_coordinate_space": "pdf_mediabox_pixels_top_left",
                "crop_padding_fraction_of_cropbox": padding,
                "source_raster_root_id": "pages",
                "source_raster_path": "page-001.jpg",
                "source_raster_sha256": MODULE.sha256_file(page_path),
                "artifact_sha256": artifact_hash,
                "human_review": {"status": "closed", "decision_id": "DEC-CROP", "observed_artifact_sha256": artifact_hash},
            }],
        }
        output = self.root / "raster-output"
        _, plan, _ = MODULE.build_contracts(self.write_input([atom]), self.pdf, {"pages": pages}, output)
        self.assertEqual("passed", plan["spec_status"])
        crop = json.loads((output / "media_evidence_ledger.json").read_text())["atoms"][0]["candidates"][0]
        self.assertEqual(artifact_hash, crop["artifact_sha256"])
        self.assertEqual("source_page_raster_cropbox_to_mediabox", crop["crop_recipe"])

    def test_native_canonical_projection_binds_exact_decision_index(self):
        assets = self.root / "native-assets"
        assets.mkdir()
        image_path = assets / "figure.png"
        Image.new("RGB", (20, 20), "navy").save(image_path)
        decision = {
            "schema_version": "canonical-decision-index/1.0",
            "spec_status": "passed",
            "decisions": [{"decision_id": "DEC-NATIVE", "status": "closed"}],
        }
        decision_path = self.root / "decision.json"
        decision_path.write_text(json.dumps(decision), encoding="utf-8")
        contract = {
            "media_contract_schema_version": "canonical-media-atom/1.0",
            "media_id": "m-native",
            "source_block_ids": ["b-native"],
            "source_page": 1,
            "media_kind": "image",
            "inclusion_status": "included",
            "requested_candidate_id": "asset",
            "candidates": [{
                "candidate_id": "asset", "representation_type": "source_asset_image",
                "root_id": "assets", "path": "figure.png", "sha256": MODULE.sha256_file(image_path),
                "decision_refs": ["DEC-NATIVE"],
            }],
        }
        records = [{"record_type": "source_block", "block_id": "b-native", "media_contract": contract}]
        header = {
            "record_type": "ledger_header", "spec_status": "passed", "ledger_id": "test-ledger",
            "ledger_snapshot_id": "snapshot-1", "current_ledger_hash": MODULE.canonical_hash(records),
            "canonical_decision_index_hash": MODULE.sha256_file(decision_path),
            "material_identity": {"source_pdf_sha256": MODULE.sha256_file(self.pdf)},
        }
        ledger_path = self.root / "canonical.jsonl"
        ledger_path.write_text("\n".join(json.dumps(item) for item in [header, *records]) + "\n", encoding="utf-8")
        output = self.root / "native-output"
        output.mkdir()
        normalized = MODULE.normalized_from_canonical(ledger_path, decision_path, self.pdf, output)
        _, plan, _ = MODULE.build_contracts(normalized, self.pdf, {"assets": assets}, output)
        self.assertEqual("passed", plan["spec_status"])
        report = MODULE.validate_contracts(output / "media_evidence_ledger.json", output / "media_representation_plan.json")
        self.assertEqual("passed", report["status"])
        self.assertEqual(MODULE.sha256_file(decision_path), plan["decision_index_sha256"])

    def test_unverified_chart_transcription_cannot_override_source_asset(self):
        assets = self.root / "assets"
        assets.mkdir()
        image_path = assets / "chart.png"
        image = Image.new("RGB", (180, 120), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 60, 100), fill="navy")
        draw.rectangle((80, 45, 120, 100), fill="purple")
        image.save(image_path)
        transcription = {"format": "markdown", "value": "| Category | Value |"}
        atom = {
            "media_id": "m-chart",
            "source_block_ids": ["b-chart"],
            "inclusion_status": "included",
            "media_kind": "chart",
            "source_page": 1,
            "candidates": [
                {"candidate_id": "asset", "representation_type": "source_asset_image", "root_id": "upstream", "path": "chart.png", "sha256": MODULE.sha256_file(image_path)},
                {"candidate_id": "ocr", "representation_type": "structured_chart", "payload": transcription, "payload_sha256": MODULE.canonical_hash(transcription), "verification_status": "candidate_unverified", "verification_refs": []},
            ],
        }
        input_path = self.write_input([atom])
        output = self.root / "chart"
        ledger, plan, _ = MODULE.build_contracts(input_path, self.pdf, {"upstream": assets}, output)
        self.assertEqual("passed", plan["spec_status"])
        self.assertEqual("source_asset_image", plan["representations"][0]["representation_type"])
        structured = next(item for item in ledger["atoms"][0]["candidates"] if item["candidate_id"] == "ocr")
        self.assertEqual("needs_review", structured["status"])
        self.assertIn("structured_transformation_unverified", structured["anomaly_flags"])

        media_plan_path = output / "media_representation_plan.json"
        representation = plan["representations"][0]
        payload = {"kind": "source_asset_image", "asset_ref": "chart.png", "asset_sha256": representation["artifact_sha256"]}
        render_plan = {
            "nodes": [{
                "render_node_id": "render-1",
                "source_block_ids": ["b-chart"],
                "target_construct": "source_asset_image",
                "payload": payload,
                "payload_hash": MODULE.canonical_hash(payload),
                "media_binding": {
                    "media_id": "m-chart",
                    "representation_id": representation["representation_id"],
                    "representation_type": representation["representation_type"],
                    "selected_candidate_id": representation["selected_candidate_id"],
                    "artifact_sha256": representation["artifact_sha256"],
                    "media_representation_plan_sha256": MODULE.sha256_file(media_plan_path),
                },
            }]
        }
        render_plan_path = output / "render_plan.json"
        render_plan_path.write_text(json.dumps(render_plan), encoding="utf-8")
        binding = MODULE.validate_render_binding(output / "media_evidence_ledger.json", media_plan_path, render_plan_path)
        self.assertEqual("passed", binding["status"])

        render_plan["nodes"][0]["media_binding"]["artifact_sha256"] = "0" * 64
        render_plan_path.write_text(json.dumps(render_plan), encoding="utf-8")
        tampered = MODULE.validate_render_binding(output / "media_evidence_ledger.json", media_plan_path, render_plan_path)
        self.assertEqual("failed", tampered["status"])

    def test_relocated_media_contract_uses_explicit_evidence_root(self):
        evidence_root = self.root / "portable"
        selected = evidence_root / "media/selected/figure.png"
        selected.parent.mkdir(parents=True)
        Image.new("RGB", (20, 20), "navy").save(selected)
        external_pdf = self.root / "external-source.pdf"
        external_pdf.write_bytes(self.pdf.read_bytes())
        candidate = {
            "candidate_id": "asset",
            "representation_type": "source_asset_image",
            "status": "usable",
            "resolved_path": "media/selected/figure.png",
            "artifact_sha256": MODULE.sha256_file(selected),
        }
        ledger = {
            "schema_version": "media-evidence-ledger/1.0",
            "ledger_id": "portable-ledger",
            "source_pdf": {
                "path": "external/source.pdf",
                "sha256": MODULE.sha256_file(self.pdf),
                "page_count": 1,
            },
            "atoms": [{
                "media_id": "m-portable",
                "source_block_ids": ["b-portable"],
                "inclusion_status": "included",
                "candidates": [candidate],
            }],
            "summary": {"atoms": 1, "included": 1, "excluded": 0, "needs_review": 0},
        }
        ledger["payload_hash"] = MODULE.payload_hash(ledger)
        ledger_path = self.root / "portable-ledger.json"
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        representation = {
            "representation_id": "representation::m-portable",
            "media_id": "m-portable",
            "source_block_ids": ["b-portable"],
            "status": "closed",
            "selected_candidate_id": "asset",
            "representation_type": "source_asset_image",
            "artifact_sha256": candidate["artifact_sha256"],
        }
        plan = {
            "schema_version": "media-representation-plan/1.0",
            "media_evidence_ledger_sha256": MODULE.sha256_file(ledger_path),
            "spec_status": "passed",
            "open_reviews": 0,
            "representations": [representation],
            "summary": {"representations": 1, "closed": 1, "excluded": 0, "needs_review": 0},
        }
        plan["payload_hash"] = MODULE.payload_hash(plan)
        plan_path = self.root / "portable-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        report = MODULE.validate_contracts(
            ledger_path,
            plan_path,
            evidence_root=evidence_root,
            source_pdf_path=external_pdf,
        )

        self.assertEqual("passed", report["status"])


if __name__ == "__main__":
    unittest.main()
