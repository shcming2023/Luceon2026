import importlib.util
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).parents[1] / "scripts/render_frozen_plan.py"
SPEC = importlib.util.spec_from_file_location("renderer", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

FREEZER_SCRIPT = Path(__file__).parents[1] / "scripts/freeze_template_contract.py"
FREEZER_SPEC = importlib.util.spec_from_file_location("freezer", FREEZER_SCRIPT)
FREEZER = importlib.util.module_from_spec(FREEZER_SPEC)
assert FREEZER_SPEC and FREEZER_SPEC.loader
FREEZER_SPEC.loader.exec_module(FREEZER)


class RendererPrimitiveTests(unittest.TestCase):
    def test_contract_validators_are_release_local_siblings(self):
        orchestrator_scripts = (
            SCRIPT.parents[2]
            / "luceon-popo-to-refined-elegantbook/scripts"
        )
        self.assertEqual(
            orchestrator_scripts / "validate_intermediate_contracts.py",
            MODULE.default_validator_path(),
        )
        self.assertEqual(
            orchestrator_scripts / "media_source_representation.py",
            MODULE.default_media_validator_path(),
        )
        self.assertEqual(
            orchestrator_scripts / "validate_intermediate_contracts.py",
            FREEZER.default_validator_path(),
        )

    def test_text_escape_is_deterministic(self):
        self.assertEqual(r"A\&B \ensuremath{\square}", MODULE.escape_text("A&B □"))

    def test_directional_triangles_use_existing_math_glyphs(self):
        self.assertEqual(
            r"\ensuremath{\blacktriangledown} \ensuremath{\blacktriangleright} \ensuremath{\blacktriangleleft}",
            MODULE.escape_text("▼ ▶ ◀"),
        )

    def test_source_visible_math_relations_are_serialized_with_existing_math_commands(self):
        self.assertEqual(
            r"\ensuremath{\angle} \ensuremath{\because} \ensuremath{\therefore} \ensuremath{\perp}",
            MODULE.escape_text("∠ ∵ ∴ ⊥"),
        )
        self.assertEqual(r"\(\angle  A \perp  B \therefore  C\)", MODULE.sanitize_math(r"\(∠ A ⊥ B ∴ C\)"))

    def test_portable_region_asset_is_resolved_from_bound_asset_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = root / "assets/media/selected"
            assets.mkdir(parents=True)
            source = assets / "frozen-crop.png"
            Image.new("RGB", (20, 10), "white").save(source)
            digest = MODULE.sha256_file(source)
            payload = {
                "asset_ref": source.name,
                "asset_size_bytes": source.stat().st_size,
                "artifact_sha256": digest,
                "media_binding": {
                    "representation_type": "source_region_image",
                    "artifact_sha256": digest,
                },
            }
            plan = {"nodes": [{
                "render_node_id": "render::portable-region",
                "node_kind": "media",
                "source_block_ids": ["s1"],
                "target_construct": "source_region_image",
                "construct_parameters": {
                    "width_fraction": 0.5,
                    "max_height_fraction": 0.5,
                    "alignment": "center",
                },
                "payload": payload,
                "payload_hash": MODULE.canonical_hash(payload),
            }]}
            project = root / "project"
            project.mkdir()

            rendered, emissions, copied, crops = MODULE.serialize(
                plan, project, [root / "assets"], None, None, root,
            )

            self.assertIn(b"frozen-crop.png", rendered)
            self.assertEqual(emissions[0]["render_node_id"], "render::portable-region")
            self.assertEqual(copied["frozen-crop.png"]["sha256"], digest)
            self.assertEqual(crops, {})

    def test_ambiguous_ocr_symbol_fails(self):
        with self.assertRaises(ValueError):
            MODULE.escape_text("ⓞ")

    def test_literal_dollar_in_plain_text_is_escaped(self):
        self.assertEqual(r"A car costs \$525.", MODULE.mixed_text("A car costs $525."))

    def test_literal_dollar_inside_inline_math_is_not_a_nested_delimiter(self):
        self.assertEqual(
            r"After one year \( 500 = \text{\$}525 \)",
            MODULE.mixed_text(r"After one year \( 500 = $525 \)"),
        )

    def test_currency_variable_inside_inline_math_is_preserved_as_a_glyph(self):
        self.assertEqual(
            r"\( \text{\$}P + \text{\$}x \)",
            MODULE.mixed_text(r"\( $P + $x \)"),
        )

    def test_inline_math_without_currency_is_unchanged(self):
        self.assertEqual(r"\( 500 = 525 \)", MODULE.mixed_text(r"\( 500 = 525 \)"))

    def test_spec04c_v2_capability_is_normalized(self):
        manifest = {
            "schema_version": "template-capability-manifest/2.0",
            "template_archive": {"sha256": "a" * 64},
            "entry": {"sha256": "b" * 64},
            "class": {"member": "elegantbook.cls", "sha256": "c" * 64},
            "documentclass": {"name": "elegantbook", "options": ["11pt"]},
            "constructs": {
                "tcolorbox_styles": {"notebox": {}},
                "custom_commands": ["activitynum"],
                "custom_environments": ["answershow"],
            },
        }
        view = FREEZER.capability_view(manifest, r"\title{Demo}\author{A}")
        self.assertEqual(["title", "author"], view["metadata_candidates"])
        self.assertEqual(["notebox"], view["tcolorbox_styles"])
        self.assertEqual("elegantbook.cls", view["class_ref"])

    def test_spec04d_v2_paragraph_text_is_escaped(self):
        self.assertEqual(r"A\&B", MODULE.mixed_text("A&B"))

    def test_frozen_two_column_response_list_preserves_source_items_and_answer_space(self):
        items = [
            {"block_id": "q1", "source_text": r"1. \(x+1\)", "source_text_sha256": MODULE.sha256_bytes(r"1. \(x+1\)".encode("utf-8"))},
            {"block_id": "q2", "source_text": r"2. \(x+2\)", "source_text_sha256": MODULE.sha256_bytes(r"2. \(x+2\)".encode("utf-8"))},
        ]
        payload = {"group_id": "g1", "items": items}
        plan = {"nodes": [{
            "render_node_id": "render::responses", "node_kind": "response_list",
            "source_block_ids": ["q1", "q2"], "target_construct": "response_list",
            "construct_parameters": {
                "columns": 2,
                "answer_space": {"mode": "inline_rule", "rule_width_fraction": 0.45, "vertical_space_baselines": 2},
            },
            "payload": payload, "payload_hash": MODULE.canonical_hash(payload),
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            rendered, emissions, copied, crops = MODULE.serialize(plan, project, [], None, None, root)
        text = rendered.decode("utf-8")
        self.assertIn(r"\begin{multicols}{2}", text)
        self.assertIn(r"1. \(x+1\)", text)
        self.assertIn(r"\rule{0.450\linewidth}{0.4pt}", text)
        self.assertIn(r"\end{multicols}", text)
        self.assertEqual(["q1", "q2"], emissions[0]["source_block_ids"])
        self.assertEqual({}, copied)
        self.assertEqual({}, crops)

    def test_single_response_item_cannot_be_balanced_across_two_columns(self):
        text = "4. A long question whose final word must not move to a second column?"
        payload = {
            "group_id": "g-single",
            "items": [{
                "block_id": "q1",
                "source_text": text,
                "source_text_sha256": MODULE.sha256_bytes(text.encode("utf-8")),
            }],
        }
        plan = {"nodes": [{
            "render_node_id": "render::single-response",
            "node_kind": "response_list",
            "source_block_ids": ["q1"],
            "target_construct": "response_list",
            "construct_parameters": {
                "columns": 2,
                "answer_space": {"mode": "inline_rule", "rule_width_fraction": 0.45, "vertical_space_baselines": 2},
            },
            "payload": payload,
            "payload_hash": MODULE.canonical_hash(payload),
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            with self.assertRaisesRegex(ValueError, "at least two independently ordered items"):
                MODULE.serialize(plan, project, [], None, None, root)

    def test_localized_toc_depth_override_is_serialized_without_template_mutation(self):
        plan = {"nodes": [{
            "render_node_id": "render::toc", "source_block_ids": ["s1"],
            "target_construct": "subsection*",
            "construct_parameters": {
                "toc": True, "level": 2, "toc_entry_level": "subsection",
                "toc_visibility_strategy": "localized_depth_override", "toc_depth_override": 2,
            },
            "payload": {"title": "Sublesson"}, "payload_hash": "a" * 64,
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            rendered, emissions, copied, crops = MODULE.serialize(plan, project, [], None, None, root)
        text = rendered.decode("utf-8")
        self.assertIn(r"\addtocontents{toc}{\protect\begingroup\protect\setcounter{tocdepth}{2}}", text)
        self.assertIn(r"\addcontentsline{toc}{subsection}{Sublesson}", text)
        self.assertIn(r"\addtocontents{toc}{\protect\endgroup}", text)
        self.assertEqual(len(emissions), 1)
        self.assertEqual(copied, {})
        self.assertEqual(crops, {})

    def test_standard_paragraph_fifth_level_toc_is_serialized(self):
        plan = {"nodes": [{
            "render_node_id": "render::paragraph-toc",
            "source_block_ids": ["s1"],
            "target_construct": "paragraph*",
            "construct_parameters": {
                "toc": True,
                "level": 4,
                "toc_entry_level": "paragraph",
                "toc_visibility_strategy": "localized_depth_override",
                "toc_depth_override": 4,
            },
            "payload": {"title": "Exercise 1.1A"},
            "payload_hash": "a" * 64,
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            rendered, emissions, copied, crops = MODULE.serialize(
                plan,
                project,
                [],
                None,
                None,
                root,
            )
        text = rendered.decode("utf-8")
        self.assertIn(r"\paragraph*{Exercise 1.1A}", text)
        self.assertIn(
            r"\addtocontents{toc}{\protect\begingroup\protect\setcounter{tocdepth}{4}}",
            text,
        )
        self.assertIn(
            r"\addcontentsline{toc}{paragraph}{Exercise 1.1A}",
            text,
        )
        self.assertEqual(len(emissions), 1)
        self.assertEqual(copied, {})
        self.assertEqual(crops, {})

    def test_source_region_presentation_is_pixel_and_scope_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "template"
            template.mkdir()
            (template / "figure").mkdir()
            (template / "main.tex").write_text(r"\cover{cover.jpg}\logo{logo.jpg}", encoding="utf-8")
            Image.new("RGB", (20, 10), "blue").save(template / "figure/cover.jpg")
            Image.new("RGB", (10, 10), "green").save(template / "figure/logo.jpg")
            page = root / "page.png"
            source_image = Image.new("RGB", (20, 20), "white")
            for y in range(5, 15):
                for x in range(2, 18):
                    source_image.putpixel((x, y), (120, 20, 10))
            source_image.save(page)
            crop = root / "crop.png"
            source_image.crop((2, 5, 18, 15)).save(crop)
            source_pdf = root / "source.pdf"
            source_pdf.write_bytes(b"source-evidence")
            scope = root / "scope.json"
            scope.write_text(json.dumps({"pages": [{"physical_page": 1, "status": "excluded"}]}), encoding="utf-8")

            sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            config = root / "presentation.json"
            config.write_text(json.dumps({
                "schema_version": "spec05-presentation-config/1.0", "status": "approved",
                "template_zip_sha256": "a" * 64,
                "source_scope_binding": {"ledger_ref": "scope.json", "ledger_sha256": sha(scope)},
                "assets": {
                    "cover": {
                        "mode": "source_region_asset", "macro_value": "source-cover.png",
                        "project_path": "figure/source-cover.png", "asset_ref": "crop.png",
                        "asset_sha256": sha(crop), "media_type": "image/png",
                        "pixel_dimensions": {"width": 16, "height": 10},
                        "provenance": {
                            "source_ref": "source.pdf", "source_sha256": sha(source_pdf),
                            "pdf_physical_page": 1, "page_render_ref": "page.png", "page_render_sha256": sha(page),
                            "bbox_px": [2, 5, 18, 15], "coordinate_space": "source_page_pixels",
                            "fit_policy": "template_native_width_no_additional_crop", "body_scope_status": "excluded"
                        },
                        "decision": {"decision_id": "D-COVER", "status": "closed", "rationale": "source identity", "evidence_refs": ["page.png"]},
                        "compatibility": {"status": "approved", "assertion": "source_identity"}
                    },
                    "logo": {
                        "mode": "template_default", "macro_value": "logo.jpg", "template_member": "figure/logo.jpg",
                        "asset_sha256": sha(template / "figure/logo.jpg"),
                        "decision": {"decision_id": "D-LOGO", "status": "closed", "rationale": "output brand", "evidence_refs": ["template"]},
                        "compatibility": {"status": "approved", "assertion": "output_brand"}
                    }
                }
            }), encoding="utf-8")
            normalized, checks = FREEZER.verify_presentation(config, template, "a" * 64, (template / "main.tex").read_text(), root)
            self.assertEqual("source_region_asset", normalized["assets"]["cover"]["mode"])
            self.assertTrue(next(item for item in checks if item["macro"] == "cover")["source_region_pixels_verified"])

            document = json.loads(config.read_text())
            document["assets"]["cover"]["decision"]["status"] = "open"
            config.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "closed decision"):
                FREEZER.verify_presentation(config, template, "a" * 64, (template / "main.tex").read_text(), root)

    def test_presentation_materialization_never_overwrites_frozen_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            (project / "figure").mkdir(parents=True)
            original = project / "figure/cover.jpg"
            original.write_bytes(b"frozen-cover")
            logo = project / "figure/logo.jpg"
            logo.write_bytes(b"frozen-logo")
            source = root / "new-cover.png"
            source.write_bytes(b"new-cover")
            sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            contract_path = root / "contract.json"
            contract = {
                "schema_version": "template-contract/2.0",
                "immutable_files": [
                    {"path": "figure/cover.jpg", "sha256": sha(original)},
                    {"path": "figure/logo.jpg", "sha256": sha(logo)},
                ],
                "selected_presentation": {"assets": {
                    "cover": {
                        "mode": "approved_static_asset", "macro_value": "new-cover.png", "project_path": "figure/new-cover.png",
                        "asset_ref": "new-cover.png", "asset_sha256": sha(source),
                        "decision": {"decision_id": "D1", "status": "closed"}, "compatibility": {"status": "approved"},
                    },
                    "logo": {
                        "mode": "template_default", "macro_value": "logo.jpg", "template_member": "figure/logo.jpg",
                        "asset_sha256": sha(logo), "decision": {"decision_id": "D2", "status": "closed"},
                        "compatibility": {"status": "approved"},
                    },
                }},
            }
            additions, values = MODULE.materialize_presentation_assets(project, contract_path, contract)
            self.assertEqual(b"frozen-cover", original.read_bytes())
            self.assertEqual(b"new-cover", (project / "figure/new-cover.png").read_bytes())
            self.assertEqual({"cover": "new-cover.png", "logo": "logo.jpg"}, values)
            self.assertEqual(1, len(additions))

    def test_presentation_macro_rejects_path_traversal(self):
        with self.assertRaisesRegex(ValueError, "unsafe presentation"):
            MODULE.replace_presentation_values(r"\cover{cover.jpg}", {"cover": "../cover.jpg"})


if __name__ == "__main__":
    unittest.main()
