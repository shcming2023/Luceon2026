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
                "main_member": "main.tex", "class_member": "elegantbook.cls",
                "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            }), encoding="utf-8")
            manifest = MODULE.extract_template_capabilities(intake, archive)
            self.assertEqual(set(manifest["constructs"]["tcolorbox_styles"]), {"featurebox", "notebox"})
            self.assertIn("subsubsection*", manifest["constructs"]["sectioning"])
            self.assertIn("paragraph*", manifest["constructs"]["sectioning"])
            self.assertIn("paragraph", manifest["constructs"]["standard_serialization"])
            self.assertIn("response_list", manifest["constructs"]["standard_serialization"])
            self.assertEqual(manifest["visibility_constraints"]["hidden_by_default_environments"], ["answershow"])
            self.assertEqual(manifest["toc_capability"]["effective_tocdepth"], 1)
            self.assertEqual(manifest["toc_capability"]["native_visible_entry_types"], ["chapter", "section"])
            self.assertEqual(
                manifest["body_insertion"],
                {
                    "after_exact_marker": r"\tableofcontents",
                    "before_exact_token": r"\end{document}",
                },
            )
            self.assertEqual(
                manifest["toc_capability"]["entry_type_depths"]["paragraph"],
                4,
            )
            self.assertTrue(manifest["toc_capability"]["serialization_strategies"]["localized_depth_override"]["preserves_pdf_outline_level"])

    def test_template_capability_payload_hash_ignores_runtime_paths(self):
        manifests = []
        for dirname in ("first-run", "second-run"):
            with tempfile.TemporaryDirectory(prefix=dirname) as tmp:
                root = Path(tmp)
                archive = root / "template.zip"
                with zipfile.ZipFile(archive, "w") as zf:
                    zf.writestr(
                        "main.tex",
                        "\\documentclass[11pt]{elegantbook}\n"
                        "\\usepackage{tcolorbox}\n"
                        "\\tcbset{notebox/.style={breakable,colback=gray}}\n"
                        "\\begin{document}\n"
                        "\\tableofcontents\n"
                        "\\end{document}\n",
                    )
                    zf.writestr("elegantbook.cls", "\\NeedsTeXFormat{LaTeX2e}\n")
                intake = root / "template_intake.json"
                intake.write_text(
                    json.dumps(
                        {
                            "main_member": "main.tex",
                            "class_member": "elegantbook.cls",
                            "archive_sha256": hashlib.sha256(
                                archive.read_bytes()
                            ).hexdigest(),
                        }
                    ),
                    encoding="utf-8",
                )
                manifests.append(
                    MODULE.extract_template_capabilities(intake, archive)
                )

        self.assertNotEqual(
            manifests[0]["template_intake"]["path"],
            manifests[1]["template_intake"]["path"],
        )
        self.assertEqual(
            manifests[0]["capability_payload_hash"],
            manifests[1]["capability_payload_hash"],
        )

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

    def test_compact_task_exposes_only_frozen_construct_choices(self):
        task = MODULE.construct_review_task(
            groups=self.groups(),
            template=self.template(),
            predecessor_sha256="b" * 64,
        )

        self.assertEqual(
            task["schema_version"],
            "luceon.worker-v3-spec04c-compact-task/v1",
        )
        self.assertEqual(task["semantic_object_count"], 2)
        self.assertEqual(task["candidate_count"], 2)
        self.assertEqual(
            [
                (row["object_kind"], row["semantic_role"])
                for row in task["review_tasks"]
            ],
            [
                ("standalone_label", "teaching_column_label"),
                ("teaching_group", "summary"),
            ],
        )
        standalone, teaching = task["review_tasks"]
        self.assertEqual(
            [row["target_construct"] for row in standalone["options"]],
            ["subsubsection*"],
        )
        self.assertEqual(
            [row["construct_parameters"]["style"] for row in teaching["options"]],
            ["featurebox", "notebox", "vocabbox"],
        )
        self.assertLess(
            len(MODULE.canonical_bytes(task)),
            50_000,
            "Spec 04-C model task must not echo the full canonical ledger",
        )
        self.assertLessEqual(
            task["capacity"]["minimum_response_bytes"],
            task["capacity"]["maximum_response_bytes"],
        )

    def test_compact_projection_is_total_and_deterministic(self):
        task = MODULE.construct_review_task(
            groups=self.groups(),
            template=self.template(),
            predecessor_sha256="b" * 64,
        )
        compact = {
            "schema_version": "luceon.worker-v3-spec04c-compact-review/v1",
            "task_id": task["task_id"],
            "review_status": "closed",
            "decisions": [
                {
                    "task_id": task["review_tasks"][0]["task_id"],
                    "selected_option_id": "option-0000",
                },
                {
                    "task_id": task["review_tasks"][1]["task_id"],
                    "selected_option_id": "option-0001",
                },
            ],
            "open_reviews": [],
        }

        projected = MODULE.project_construct_review(task, compact)

        self.assertEqual(projected["parent_binding"], {})
        self.assertEqual(
            projected["semantic_object_inventory_hash"],
            MODULE.canonical_hash(MODULE.semantic_inventory(self.groups())),
        )
        self.assertEqual(len(projected["construct_rules"]), 2)
        self.assertEqual(
            projected["construct_rules"][0]["target_construct"],
            "subsubsection*",
        )
        self.assertEqual(
            projected["construct_rules"][1]["construct_parameters"]["style"],
            "notebox",
        )
        self.assertEqual(projected["review"]["status"], "closed")
        self.assertEqual(projected["review"]["open_items"], 0)

    def test_compact_projection_accepts_empty_inventory_without_model_invention(self):
        groups = {"groups": [], "standalone_labels": []}
        task = MODULE.construct_review_task(
            groups=groups,
            template=self.template(),
            predecessor_sha256="b" * 64,
        )
        compact = {
            "schema_version": "luceon.worker-v3-spec04c-compact-review/v1",
            "task_id": task["task_id"],
            "review_status": "closed",
            "decisions": [],
            "open_reviews": [],
        }

        projected = MODULE.project_construct_review(task, compact)

        self.assertEqual(task["candidate_count"], 0)
        self.assertEqual(projected["construct_rules"], [])
        self.assertEqual(
            projected["semantic_object_inventory_hash"],
            MODULE.canonical_hash([]),
        )

    def test_compact_projection_rejects_missing_or_out_of_set_decision(self):
        task = MODULE.construct_review_task(
            groups=self.groups(),
            template=self.template(),
            predecessor_sha256="b" * 64,
        )
        compact = {
            "schema_version": "luceon.worker-v3-spec04c-compact-review/v1",
            "task_id": task["task_id"],
            "review_status": "closed",
            "decisions": [
                {
                    "task_id": task["review_tasks"][0]["task_id"],
                    "selected_option_id": "invented",
                }
            ],
            "open_reviews": [],
        }

        with self.assertRaisesRegex(ValueError, "complete, ordered"):
            MODULE.project_construct_review(task, compact)

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
