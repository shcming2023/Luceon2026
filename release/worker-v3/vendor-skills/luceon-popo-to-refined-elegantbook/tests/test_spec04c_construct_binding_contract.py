import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/spec04c_construct_binding_contract.py"
SPEC = importlib.util.spec_from_file_location("spec04c_contract", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class Spec04CConstructBindingTests(unittest.TestCase):
    def template(self):
        return {
            "capability_payload_hash": "a" * 64,
            "constructs": {
                "sectioning": ["subsubsection", "subsubsection*"],
                "tcolorbox_styles": {"featurebox": {}, "notebox": {}, "vocabbox": {}},
            },
            "supported_parameters": {"tcolorbox": {"breakable": True}},
        }

    def groups(self):
        return {
            "groups": [{
                "group_id": "g1", "semantic_role": "summary", "marker_block_id": "m1",
                "body_block_ids": ["b1"], "source_block_ids": ["m1", "b1"],
                "source_evidence_ids": ["page-1"],
            }],
            "standalone_labels": [{
                "block_id": "s1", "semantic_role": "teaching_column_label",
                "source_evidence_ids": ["page-2"],
            }],
        }

    def bundle(self, groups=None):
        groups = groups or self.groups()
        return {
            "schema_version": "spec04c-construct-review-bundle/1.0", "review_id": "review-1",
            "parent_binding": {"parent": "exact"},
            "semantic_object_inventory_hash": MODULE.canonical_hash(MODULE.semantic_inventory(groups)),
            "construct_rules": [
                {
                    "rule_id": "R-SUMMARY", "rule_version": "1.0", "object_kind": "teaching_group",
                    "semantic_role": "summary", "target_construct": "tcolorbox",
                    "construct_parameters": {"style": "featurebox", "breakable": True},
                    "layer": "book_config", "selection_reason": "Reviewed summary panel.",
                    "why_box_or_not": "Non-empty source-supported group may use a box.",
                },
                {
                    "rule_id": "R-STANDALONE", "rule_version": "1.0", "object_kind": "standalone_label",
                    "semantic_role": "teaching_column_label", "target_construct": "subsubsection*",
                    "construct_parameters": {}, "layer": "core",
                    "selection_reason": "Standalone label has no confirmed body.",
                    "why_box_or_not": "Empty boxes are forbidden; preserve as an unnumbered local heading.",
                },
            ],
            "review": {"status": "closed", "open_items": 0, "decision_refs": ["DEC-1"]},
        }

    def test_extracts_actual_template_styles_and_visibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "template.zip"
            entry = r"""\documentclass[11pt,a4paper]{elegantbook}
\usepackage{tcolorbox,multicol}
\tcbset{featurebox/.style={title=FEATURE,colback=blue!5},notebox/.style={breakable,colback=gray}}
\titleformat{\subsubsection}[block]{}{}{}{}
\excludecomment{answershow}
\begin{document}
\tableofcontents
\end{document}
"""
            cls = "tcolorbox breakable\n\\setcounter{tocdepth}{1}\n"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("main.tex", entry)
                zf.writestr("elegantbook.cls", cls)
            intake = root / "template_intake.json"
            intake.write_text(json.dumps({
                "entry": "main.tex", "class": "elegantbook.cls",
                "template_zip_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            }), encoding="utf-8")
            manifest = MODULE.extract_template_capabilities(intake, archive)
            self.assertEqual(set(manifest["constructs"]["tcolorbox_styles"]), {"featurebox", "notebox"})
            self.assertIn("subsubsection*", manifest["constructs"]["sectioning"])
            self.assertIn("paragraph", manifest["constructs"]["standard_serialization"])
            self.assertIn("response_list", manifest["constructs"]["standard_serialization"])
            self.assertEqual(manifest["visibility_constraints"]["hidden_by_default_environments"], ["answershow"])
            self.assertEqual(manifest["toc_capability"]["effective_tocdepth"], 1)
            self.assertEqual(manifest["toc_capability"]["native_visible_entry_types"], ["chapter", "section"])
            self.assertTrue(manifest["toc_capability"]["serialization_strategies"]["localized_depth_override"]["preserves_pdf_outline_level"])

    def test_valid_rules_bind_every_semantic_object_once(self):
        groups = self.groups()
        contract, queue = MODULE.build_bindings(self.bundle(groups), groups, self.template(), {"parent": "exact"})
        self.assertEqual(contract["summary"]["construct_bindings"], 2)
        self.assertEqual(contract["summary"]["boxed_bindings"], 1)
        self.assertEqual(queue["open_items"], 0)

    def test_empty_semantic_inventory_requires_zero_rules_and_bindings(self):
        groups = {"groups": [], "standalone_labels": []}
        bundle = self.bundle(groups)
        bundle["construct_rules"] = []
        contract, queue = MODULE.build_bindings(bundle, groups, self.template(), {"parent": "exact"})
        self.assertEqual(contract["summary"]["semantic_objects"], 0)
        self.assertEqual(contract["summary"]["construct_bindings"], 0)
        self.assertEqual(contract["summary"]["boxed_bindings"], 0)
        self.assertEqual(contract["summary"]["constructs"], {})
        self.assertEqual(queue["open_items"], 0)

        schema = json.loads(
            (SCRIPT.parents[1] / "schemas/spec04c-construct-review-bundle.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["construct_rules"]["minItems"], 0)

    def test_rejects_unknown_template_style(self):
        bundle = self.bundle()
        bundle["construct_rules"][0]["construct_parameters"]["style"] = "inventedbox"
        with self.assertRaisesRegex(ValueError, "does not expose"):
            MODULE.build_bindings(bundle, self.groups(), self.template(), {"parent": "exact"})

    def test_rejects_box_for_standalone_label(self):
        bundle = self.bundle()
        rule = bundle["construct_rules"][1]
        rule["target_construct"] = "tcolorbox"
        rule["construct_parameters"] = {"style": "notebox", "breakable": True}
        with self.assertRaisesRegex(ValueError, "EMPTY_BOX_FORBIDDEN"):
            MODULE.build_bindings(bundle, self.groups(), self.template(), {"parent": "exact"})

    def test_rejects_missing_role_rule(self):
        bundle = self.bundle()
        bundle["construct_rules"] = bundle["construct_rules"][:1]
        with self.assertRaisesRegex(ValueError, "coverage differs"):
            MODULE.build_bindings(bundle, self.groups(), self.template(), {"parent": "exact"})

    def test_rejects_open_review(self):
        bundle = self.bundle()
        bundle["review"]["status"] = "needs_review"
        bundle["review"]["open_items"] = 1
        with self.assertRaisesRegex(ValueError, "not closed"):
            MODULE.build_bindings(bundle, self.groups(), self.template(), {"parent": "exact"})

    def test_rejects_render_payload_decision(self):
        bundle = self.bundle()
        bundle["construct_rules"][0]["payload"] = "forbidden"
        with self.assertRaisesRegex(ValueError, "downstream keys"):
            MODULE.build_bindings(bundle, self.groups(), self.template(), {"parent": "exact"})


if __name__ == "__main__":
    unittest.main()
