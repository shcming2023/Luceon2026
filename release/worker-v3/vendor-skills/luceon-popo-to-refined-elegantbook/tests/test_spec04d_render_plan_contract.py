import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/spec04d_render_plan_contract.py"
SPEC = importlib.util.spec_from_file_location("spec04d_contract", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class Spec04DRenderPlanTests(unittest.TestCase):
    def policy(self):
        return {
            "schema_version": "spec04d-render-policy/1.1", "policy_id": "test", "ownership_layer": "profile",
            "review": {"status": "closed", "decision_refs": ["R1"], "basis": "test"},
            "structure_level_constructs": {"0": "chapter*"}, "local_heading_construct": "subsubsection*",
            "toc_representation": {
                "ownership_layer": "core", "semantic_level_to_entry_type": {"0": "chapter", "1": "section", "2": "subsection"},
                "overflow_strategy": "localized_depth_override", "decision_refs": ["R-TOC"],
            },
            "plain_body_construct": "paragraph",
            "safe_textual_fragile_types": ["image_caption", "image_footnote", "page_footnote", "table_caption"],
            "fragile_types_requiring_media_representation": ["chart", "equation", "image", "table"],
            "media_constructs": {"source_asset_image": "source_asset_image", "source_region_image": "source_region_image", "structured_formula": "display_math"},
            "source_image_layout": {"minimum_width_fraction": 0.25, "maximum_width_fraction": 0.9, "max_height_fraction": 0.72, "alignment": "center"},
            "unsupported_representation_types": ["structured_chart", "structured_table", "structured_vector"],
            "structure_media_collision_rule": "virtual_source_supported_structure_node_and_single_media_atom_output",
            "structure_source_role_overrides": [],
            "prohibitions": ["semantic_reclassification", "construct_reselection", "content_rewriting", "formula_reconstruction", "table_reconstruction", "latex_generation"],
        }

    def record(self, block_id, order, source_type="text", disposition="plain_body", raw=None):
        raw = raw or block_id
        return {
            "block_id": block_id, "scope_status": "included", "candidate_final_order": order,
            "pdf_physical_page": 1, "page_local_order": order, "source_type": source_type,
            "semantic_disposition": disposition, "raw_content": raw,
            "raw_content_sha256": MODULE.canonical_hash(raw), "bbox": [0.1, 0.1, 0.5, 0.2],
            "semantic_disposition_decision_refs": ["S4B"],
        }

    def toc_capability(self, effective_depth=1):
        entry_type_depths = {
            "chapter": 0,
            "section": 1,
            "subsection": 2,
            "subsubsection": 3,
            "paragraph": 4,
        }
        return {
            "entry_type_depths": entry_type_depths,
            "effective_tocdepth": effective_depth,
            "effective_tocdepth_status": "explicitly_declared",
            "native_visible_entry_types": [
                name
                for name, depth in entry_type_depths.items()
                if depth <= effective_depth
            ],
            "serialization_strategies": {
                "native": {"supported": True, "preserves_entry_type": True},
                "localized_depth_override": {
                    "supported": True, "preserves_entry_type": True, "preserves_pdf_outline_level": True,
                    "adds_template_api": False, "modifies_template_preamble_or_class": False,
                },
            },
        }

    def compact_inputs(self, *, source_type="title", raw="Unit One"):
        record = self.record(
            "s1",
            1,
            source_type=source_type,
            disposition="book_structure",
            raw=raw,
        )
        hierarchy = [{
            "node_id": "unit-1",
            "anchor_block_id": "s1",
            "heading_evidence_block_ids": ["s1"],
            "level": 0,
            "parent_node_id": None,
            "role": "unit",
            "source_order_start": 1,
            "source_order_end": 1,
            "pdf_physical_page_start": 1,
            "source_outline_evidence_ids": ["page-1"],
            "source_toc_entry_ids": [],
            "final_toc": {
                "title": "Unit One",
                "include": True,
                "level": 0,
            },
        }]
        outline = {
            "schema_version": "spec04a-structure-contract/1.0",
            "slice_status": "passed",
            "summary": {"open_reviews": 0},
            "body_hierarchy": hierarchy,
        }
        final_toc = {
            "schema_version": "final-toc-plan/1.0",
            "status": "passed",
            "open_reviews": 0,
            "entries": [{
                "level": 0,
                "node_id": "unit-1",
                "source_order": 1,
                "source_toc_entry_ids": [],
                "title": "Unit One",
                "toc_entry_id": "toc::unit-1",
            }],
        }
        template = {
            "capability_payload_hash": "a" * 64,
            "constructs": {
                "sectioning": [
                    "chapter*",
                    "section*",
                    "subsection*",
                    "subsubsection*",
                    "paragraph*",
                ],
                "standard_serialization": {
                    "paragraph": {},
                    "source_asset_image": {},
                    "source_region_image": {},
                    "display_math": {},
                },
            },
            "toc_capability": self.toc_capability(),
        }
        construct_binding = {
            "schema_version": "spec04c-construct-binding-contract/1.0",
            "slice_status": "passed",
            "bindings": [],
            "summary": {"open_reviews": 0},
            "template_capability_payload_hash": "a" * 64,
            "prohibitions": [],
        }
        media_plan = {
            "schema_version": "media-representation-plan/1.0",
            "spec_status": "passed",
            "open_reviews": 0,
            "summary": {
                "closed": 0,
                "excluded": 0,
                "needs_review": 0,
                "representations": 0,
            },
            "representations": [],
        }
        return {
            "records": [record],
            "ledger_payload_hash": "b" * 64,
            "outline": outline,
            "final_toc": final_toc,
            "construct_binding": construct_binding,
            "template": template,
            "media_plan": media_plan,
        }

    def test_compact_task_omits_full_ledger_and_is_runtime_path_independent(self):
        inputs = self.compact_inputs(
            source_type="text",
            raw="Question body " + ("x" * 100_000),
        )
        inputs["outline"]["generated_at"] = "2026-01-01T00:00:00Z"
        inputs["outline"]["parent"] = {"manifest_path": "/run/one/manifest.json"}
        first = MODULE.render_policy_review_task(**inputs)
        inputs["outline"]["generated_at"] = "2026-02-02T00:00:00Z"
        inputs["outline"]["parent"] = {"manifest_path": "/run/two/manifest.json"}
        second = MODULE.render_policy_review_task(**inputs)

        self.assertEqual(first, second)
        self.assertEqual(first["candidate_count"], 1)
        self.assertLess(len(MODULE.canonical_bytes(first)), 50_000)
        self.assertNotIn("x" * 10_000, MODULE.canonical_bytes(first).decode())

    def test_zero_candidate_review_projects_complete_deterministic_policy(self):
        task = MODULE.render_policy_review_task(**self.compact_inputs())
        review = {
            "schema_version": MODULE.COMPACT_REVIEW_SCHEMA,
            "task_id": task["task_id"],
            "review_status": "closed",
            "decisions": [],
            "open_reviews": [],
        }

        policy = MODULE.project_policy_review(task, review)

        self.assertEqual(policy["structure_level_constructs"], {"0": "chapter*"})
        self.assertEqual(
            policy["toc_representation"]["semantic_level_to_entry_type"],
            {"0": "chapter"},
        )
        self.assertEqual(policy["structure_source_role_overrides"], [])
        MODULE.validate_policy(policy)

    def test_standard_paragraph_preserves_fifth_frozen_structure_level(self):
        inputs = self.compact_inputs()
        records = []
        hierarchy = []
        entries = []
        for level in range(5):
            block_id = f"s{level}"
            node_id = f"node-{level}"
            title = f"Level {level}"
            records.append(
                self.record(
                    block_id,
                    level + 1,
                    source_type="title",
                    disposition="book_structure",
                    raw=title,
                )
            )
            hierarchy.append({
                "node_id": node_id,
                "anchor_block_id": block_id,
                "heading_evidence_block_ids": [block_id],
                "level": level,
                "parent_node_id": f"node-{level - 1}" if level else None,
                "role": "source_structure",
                "source_order_start": level + 1,
                "source_order_end": level + 1,
                "pdf_physical_page_start": 1,
                "source_outline_evidence_ids": ["page-1"],
                "source_toc_entry_ids": [],
                "final_toc": {
                    "title": title,
                    "include": True,
                    "level": level,
                },
            })
            entries.append({
                "level": level,
                "node_id": node_id,
                "source_order": level + 1,
                "source_toc_entry_ids": [],
                "title": title,
                "toc_entry_id": f"toc::{node_id}",
            })
        inputs["records"] = records
        inputs["outline"]["body_hierarchy"] = hierarchy
        inputs["final_toc"]["entries"] = entries

        task = MODULE.render_policy_review_task(**inputs)
        review = {
            "schema_version": MODULE.COMPACT_REVIEW_SCHEMA,
            "task_id": task["task_id"],
            "review_status": "closed",
            "decisions": [],
            "open_reviews": [],
        }
        policy = MODULE.project_policy_review(task, review)

        self.assertEqual(policy["structure_level_constructs"]["4"], "paragraph*")
        self.assertEqual(
            policy["toc_representation"]["semantic_level_to_entry_type"]["4"],
            "paragraph",
        )
        MODULE.validate_policy(policy)

    def test_non_title_candidate_projects_only_selected_source_role(self):
        task = MODULE.render_policy_review_task(
            **self.compact_inputs(
                source_type="text",
                raw="1. What is the value?",
            )
        )
        review = {
            "schema_version": MODULE.COMPACT_REVIEW_SCHEMA,
            "task_id": task["task_id"],
            "review_status": "closed",
            "decisions": [{
                "task_id": task["review_tasks"][0]["task_id"],
                "selected_option_id": "option-0001",
            }],
            "open_reviews": [],
        }

        policy = MODULE.project_policy_review(task, review)

        self.assertEqual(
            policy["structure_source_role_overrides"][0]["role"],
            "post_heading_body",
        )
        self.assertEqual(policy["plain_body_construct"], "paragraph")

    def test_compact_projection_rejects_out_of_set_or_missing_decision(self):
        task = MODULE.render_policy_review_task(
            **self.compact_inputs(source_type="text")
        )
        review = {
            "schema_version": MODULE.COMPACT_REVIEW_SCHEMA,
            "task_id": task["task_id"],
            "review_status": "closed",
            "decisions": [{
                "task_id": task["review_tasks"][0]["task_id"],
                "selected_option_id": "option-9999",
            }],
            "open_reviews": [],
        }
        with self.assertRaisesRegex(ValueError, "in-set"):
            MODULE.project_policy_review(task, review)
        review["decisions"] = []
        with self.assertRaisesRegex(ValueError, "complete"):
            MODULE.project_policy_review(task, review)

    def test_preflight_rejects_unrepresented_fragile_atom(self):
        records = [self.record("e1", 1, "equation", "fragile_or_media")]
        report = MODULE.preflight_data(records, {"representations": []}, self.policy())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["unsafe_unrepresented_fragile_atoms"], 1)
        self.assertEqual(report["issues"][0]["issue_code"], "UNSAFE_FRAGILE_ATOM_LACKS_CLOSED_SPEC03_REPRESENTATION")

    def test_builds_exact_partition_and_preserves_multifragment_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = root / "asset.bin"
            asset.write_bytes(b"source-image")
            artifact_hash = MODULE.sha256_file(asset)
            records = [
                self.record("s1", 1, source_type="title", disposition="book_structure", raw="Unit One"),
                self.record("m1", 2, disposition="teaching_column", raw="NOTE"),
                self.record("b1", 3, disposition="teaching_column", raw="Body"),
                self.record("i1", 4, "image", "fragile_or_media"),
                self.record("i2", 5, "image", "fragile_or_media"),
                self.record("p1", 6, disposition="plain_body", raw="Paragraph"),
            ]
            outline = {"body_hierarchy": [{
                "node_id": "unit-1", "anchor_block_id": "s1", "heading_evidence_block_ids": ["s1"],
                "level": 0, "parent_node_id": None, "role": "unit", "source_order_start": 1,
                "source_order_end": 6, "pdf_physical_page_start": 1, "source_outline_evidence_ids": ["page-1"],
                "final_toc": {"title": "Unit One", "include": True, "level": 0},
            }]}
            bindings = {"bindings": [{
                "binding_id": "binding-1", "object_kind": "teaching_group", "semantic_role": "note",
                "source_block_ids": ["m1", "b1"], "target_construct": "tcolorbox",
                "construct_parameters": {"style": "notebox", "title_source_block_id": "m1"},
                "source_evidence_ids": ["page-1"],
            }]}
            rep = {
                "media_id": "media-1", "representation_id": "rep-1", "status": "closed",
                "representation_type": "source_asset_image", "selected_candidate_id": "candidate-1",
                "artifact_sha256": artifact_hash, "source_block_ids": ["i1", "i2"], "decision_refs": ["M1"],
            }
            media_path = root / "media_plan.json"
            media_path.write_text(json.dumps({"representations": [rep]}), encoding="utf-8")
            media_plan = {"representations": [rep], "_path": str(media_path)}
            evidence = {"atoms": [{"media_id": "media-1", "candidates": [{
                "candidate_id": "candidate-1", "artifact_sha256": artifact_hash,
                "representation_type": "source_asset_image", "resolved_path": str(asset),
            }]}]}
            capability = {
                "capability_payload_hash": "a" * 64,
                "constructs": {"sectioning": ["chapter*", "subsubsection*"], "standard_serialization": {"paragraph": {}, "source_asset_image": {}, "source_region_image": {}, "display_math": {}}},
                "toc_capability": self.toc_capability(),
            }
            nodes = MODULE.build_render_nodes(records, outline, bindings, evidence, media_plan, capability, self.policy(), ["D1"])
            covered = [block_id for node in nodes for block_id in node["source_block_ids"]]
            self.assertEqual(set(covered), {item["block_id"] for item in records})
            self.assertEqual(len(covered), len(set(covered)))
            media = next(node for node in nodes if node["node_kind"] == "media")
            self.assertEqual(media["source_block_ids"], ["i1", "i2"])
            self.assertNotIn("source_path", media["payload"])
            self.assertEqual(media["payload"]["asset_ref"], asset.name)
            self.assertEqual(media["payload"]["asset_size_bytes"], asset.stat().st_size)
            box = next(node for node in nodes if node["target_construct"] == "tcolorbox")
            self.assertEqual(box["payload"]["title"], "NOTE")
            self.assertEqual(box["payload"]["body"][0]["raw_content"], "Body")

            partition = MODULE.build_volume_partition_plan(nodes, self.policy())
            budget = partition["volumes"][0]["budget_estimate"]
            self.assertEqual(budget["unique_media_assets"], 1)
            self.assertEqual(budget["source_media_bytes"], asset.stat().st_size)

    def test_structure_media_collision_uses_virtual_structure_and_one_media_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = root / "image.bin"
            asset.write_bytes(b"image")
            digest = MODULE.sha256_file(asset)
            records = [self.record("i1", 1, "image", "book_structure")]
            outline = {"body_hierarchy": [{
                "node_id": "unit-image", "anchor_block_id": "i1", "heading_evidence_block_ids": ["i1"],
                "level": 0, "parent_node_id": None, "role": "unit", "source_order_start": 1,
                "source_order_end": 1, "pdf_physical_page_start": 1, "source_outline_evidence_ids": ["toc-1"],
                "final_toc": {"title": "Unit From Source", "include": True, "level": 0},
            }]}
            rep = {"media_id": "m", "representation_id": "r", "status": "closed", "representation_type": "source_asset_image", "selected_candidate_id": "c", "artifact_sha256": digest, "source_block_ids": ["i1"], "decision_refs": []}
            media_path = root / "plan.json"
            media_path.write_text(json.dumps({"representations": [rep]}), encoding="utf-8")
            evidence = {"atoms": [{"media_id": "m", "candidates": [{"candidate_id": "c", "artifact_sha256": digest, "resolved_path": str(asset)}]}]}
            capability = {"capability_payload_hash": "b" * 64, "constructs": {"sectioning": ["chapter*"], "standard_serialization": {"paragraph": {}, "source_asset_image": {}}}, "toc_capability": self.toc_capability()}
            nodes = MODULE.build_render_nodes(records, outline, {"bindings": []}, evidence, {"representations": [rep], "_path": str(media_path)}, capability, self.policy(), ["D"])
            structure = next(node for node in nodes if node["node_kind"] == "book_structure")
            media = next(node for node in nodes if node["node_kind"] == "media")
            self.assertTrue(structure["virtual_source_supported"])
            self.assertEqual(structure["source_block_ids"], [])
            self.assertEqual(media["source_block_ids"], ["i1"])

    def test_level_above_template_tocdepth_uses_localized_preserving_strategy(self):
        records = [self.record("s1", 1, source_type="title", disposition="book_structure", raw="Sublesson")]
        outline = {"body_hierarchy": [{
            "node_id": "sublesson-1", "anchor_block_id": "s1", "heading_evidence_block_ids": ["s1"],
            "level": 2, "parent_node_id": "lesson-1", "role": "sublesson", "source_order_start": 1,
            "source_order_end": 1, "pdf_physical_page_start": 1, "source_outline_evidence_ids": ["toc-1"],
            "final_toc": {"title": "Sublesson", "include": True, "level": 2},
        }]}
        policy = self.policy()
        policy["structure_level_constructs"]["2"] = "subsection*"
        capability = {
            "capability_payload_hash": "c" * 64,
            "constructs": {"sectioning": ["subsection*"], "standard_serialization": {"paragraph": {}}},
            "toc_capability": self.toc_capability(effective_depth=1),
        }
        nodes = MODULE.build_render_nodes(records, outline, {"bindings": []}, {"atoms": []}, {"representations": [], "_path": __file__}, capability, policy, ["D"])
        params = nodes[0]["construct_parameters"]
        self.assertEqual(params["toc_entry_level"], "subsection")
        self.assertEqual(params["toc_visibility_strategy"], "localized_depth_override")
        self.assertEqual(params["toc_depth_override"], 2)

    def test_unpreserved_toc_overflow_fails_closed(self):
        binding = MODULE.toc_capability_binding({"capability_payload_hash": "d" * 64, "toc_capability": self.toc_capability(1)})
        node = {
            "render_node_id": "render::bad", "node_kind": "book_structure", "target_construct": "subsection*",
            "construct_parameters": {"toc": True, "level": 2, "toc_entry_level": "section", "toc_visibility_strategy": "native"},
        }
        with self.assertRaisesRegex(ValueError, "MAPPING_TOC_LEVEL_UNRENDERABLE"):
            MODULE.validate_toc_renderability([node], binding)

    def test_non_title_structure_anchor_requires_explicit_source_role(self):
        records = [self.record("q1", 1, disposition="book_structure", raw="1. What is the value?")]
        outline = {"body_hierarchy": [{
            "node_id": "assessment-1", "anchor_block_id": "q1", "heading_evidence_block_ids": ["q1"],
            "level": 0, "parent_node_id": None, "role": "assessment", "source_order_start": 1,
            "source_order_end": 1, "pdf_physical_page_start": 1, "source_outline_evidence_ids": ["page-1"],
            "final_toc": {"title": "Assessment", "include": True, "level": 0},
        }]}
        capability = {
            "capability_payload_hash": "e" * 64,
            "constructs": {"sectioning": ["chapter*"], "standard_serialization": {"paragraph": {}}},
            "toc_capability": self.toc_capability(),
        }
        with self.assertRaisesRegex(ValueError, "AMBIGUOUS_STRUCTURE_SOURCE_ROLE"):
            MODULE.build_render_nodes(records, outline, {"bindings": []}, {"atoms": []}, {"representations": [], "_path": __file__}, capability, self.policy(), ["D"])

    def test_reviewed_non_title_anchor_is_emitted_once_as_separate_body(self):
        records = [self.record("q1", 1, disposition="book_structure", raw="1. What is the value?")]
        outline = {"body_hierarchy": [{
            "node_id": "assessment-1", "anchor_block_id": "q1", "heading_evidence_block_ids": ["q1"],
            "level": 0, "parent_node_id": None, "role": "assessment", "source_order_start": 1,
            "source_order_end": 1, "pdf_physical_page_start": 1, "source_outline_evidence_ids": ["page-1"],
            "final_toc": {"title": "Assessment", "include": True, "level": 0},
        }]}
        capability = {
            "capability_payload_hash": "f" * 64,
            "constructs": {"sectioning": ["chapter*"], "standard_serialization": {"paragraph": {}}},
            "toc_capability": self.toc_capability(),
        }
        policy = self.policy()
        policy["structure_source_role_overrides"] = [{
            "structure_node_id": "assessment-1", "block_id": "q1", "role": "post_heading_body",
            "decision_refs": ["REVIEW-Q1"], "reason": "The atom is the first problem body, not title text.",
        }]
        nodes = MODULE.build_render_nodes(records, outline, {"bindings": []}, {"atoms": []}, {"representations": [], "_path": __file__}, capability, policy, ["D"])
        structure = next(node for node in nodes if node["node_kind"] == "book_structure")
        body = next(node for node in nodes if node["node_kind"] == "structure_body")
        self.assertTrue(structure["virtual_source_supported"])
        self.assertEqual(structure["source_block_ids"], [])
        self.assertEqual(structure["payload"]["title_source_fragments"], [])
        self.assertEqual(structure["payload"]["separate_body_block_ids"], ["q1"])
        self.assertEqual(body["source_block_ids"], ["q1"])
        self.assertEqual(body["target_construct"], "paragraph")
        self.assertEqual(body["payload"]["raw_content"], "1. What is the value?")
        self.assertEqual(MODULE.validate_structure_source_integrity(nodes, outline, records, policy)["post_heading_body_atoms"], 1)

    def test_pedagogical_layout_groups_questions_and_freezes_local_heading_level(self):
        records = [
            self.record("h1", 1, source_type="title", disposition="local_heading", raw="Exercise 2.2A"),
            self.record("q1", 2, raw=r"1. \(x+1\)"),
            self.record("q2", 3, raw=r"2. \(x+2\)"),
        ]
        policy = self.policy()
        items = [
            {"block_id": record["block_id"], "source_text": record["raw_content"], "source_text_sha256": record["raw_content_sha256"], "source_label": str(index)}
            for index, record in enumerate(records[1:], 1)
        ]
        policy["pedagogical_layout"] = {
            "heading_presentations": [{
                "block_id": "h1", "semantic_role": "exercise_heading", "source_title": "Exercise 2.2A",
                "display_title": "Exercise A", "target_construct": "subsubsection*", "decision_refs": ["PED-1"],
            }],
            "response_groups": [{
                "group_id": "g1", "topic_id": "t1", "exercise_heading_block_id": "h1",
                "source_block_ids": ["q1", "q2"], "items": items, "columns": 2,
                "answer_space": {"mode": "inline_rule", "rule_width_fraction": 0.45, "vertical_space_baselines": 2},
                "decision_refs": ["PED-1"],
            }],
        }
        capability = {
            "capability_payload_hash": "a" * 64,
            "constructs": {
                "sectioning": ["subsubsection*"],
                "standard_serialization": {"paragraph": {}, "response_list": {}},
            },
            "toc_capability": self.toc_capability(),
        }
        nodes = MODULE.build_render_nodes(records, {"body_hierarchy": []}, {"bindings": []}, {"atoms": []}, {"representations": [], "_path": __file__}, capability, policy, ["D"])
        heading = next(node for node in nodes if node["node_kind"] == "local_heading")
        responses = next(node for node in nodes if node["node_kind"] == "response_list")
        self.assertEqual("Exercise A", heading["payload"]["title"])
        self.assertEqual(["q1", "q2"], responses["source_block_ids"])
        self.assertEqual(2, responses["construct_parameters"]["columns"])
        self.assertEqual(1, MODULE.validate_pedagogical_render_nodes(nodes, policy["pedagogical_layout"])["response_groups"])

    def test_response_group_owns_non_title_structure_anchor_body_once(self):
        records = [self.record("q1", 1, disposition="book_structure", raw=r"1. \(x^3\)")]
        outline = {"body_hierarchy": [{
            "node_id": "topic-1", "anchor_block_id": "q1", "heading_evidence_block_ids": ["q1"],
            "level": 0, "parent_node_id": None, "role": "topic", "source_order_start": 1,
            "source_order_end": 1, "pdf_physical_page_start": 1, "source_outline_evidence_ids": ["outline-1"],
            "final_toc": {"title": "Topic 1", "include": True, "level": 0},
        }]}
        policy = self.policy()
        policy["structure_source_role_overrides"] = [{
            "structure_node_id": "topic-1", "block_id": "q1", "role": "post_heading_body",
            "decision_refs": ["REVIEW-Q1"], "reason": "The anchor is the first response item, not title text.",
        }]
        item = {
            "block_id": "q1", "source_text": records[0]["raw_content"],
            "source_text_sha256": records[0]["raw_content_sha256"], "source_label": "1",
        }
        policy["pedagogical_layout"] = {
            "heading_presentations": [],
            "response_groups": [{
                "group_id": "g1", "topic_id": "topic-1", "exercise_heading_block_id": "h1",
                "source_block_ids": ["q1"], "items": [item], "columns": 1,
                "answer_space": {"mode": "inline_rule", "rule_width_fraction": 0.45, "vertical_space_baselines": 2},
                "decision_refs": ["PED-1"],
            }],
        }
        capability = {
            "capability_payload_hash": "f" * 64,
            "constructs": {
                "sectioning": ["chapter*"],
                "standard_serialization": {"paragraph": {}, "response_list": {}},
            },
            "toc_capability": self.toc_capability(),
        }
        nodes = MODULE.build_render_nodes(
            records, outline, {"bindings": []}, {"atoms": []},
            {"representations": [], "_path": __file__}, capability, policy, ["D"],
        )
        self.assertFalse(any(node["node_kind"] == "structure_body" for node in nodes))
        response = next(node for node in nodes if node["node_kind"] == "response_list")
        self.assertEqual(["q1"], response["source_block_ids"])
        self.assertEqual(
            1,
            MODULE.validate_structure_source_integrity(nodes, outline, records, policy)["post_heading_body_atoms"],
        )

    def test_single_response_atom_cannot_be_frozen_as_two_columns(self):
        contract = {
            "heading_presentations": [],
            "response_groups": [{
                "group_id": "g-single",
                "source_block_ids": ["q1"],
                "items": [{
                    "block_id": "q1",
                    "source_text": "1. A question?",
                    "source_text_sha256": "a" * 64,
                }],
                "columns": 2,
                "answer_space": {
                    "mode": "inline_rule",
                    "rule_width_fraction": 0.45,
                    "vertical_space_baselines": 2,
                },
            }],
        }
        node = {
            "render_node_id": "render::single",
            "node_kind": "response_list",
            "target_construct": "response_list",
            "source_block_ids": ["q1"],
            "construct_parameters": {
                "columns": 2,
                "answer_space": contract["response_groups"][0]["answer_space"],
            },
            "payload": {
                "group_id": "g-single",
                "items": contract["response_groups"][0]["items"],
            },
        }
        with self.assertRaisesRegex(ValueError, "at least two independently ordered atoms"):
            MODULE.validate_pedagogical_render_nodes([node], contract)

    def media_parent_fixture(self, root, representations=None, nodes=None):
        evidence_path = root / "media_evidence_ledger.json"
        plan_path = root / "media_representation_plan.json"
        evidence_path.write_text(json.dumps({"atoms": []}), encoding="utf-8")
        plan_path.write_text(
            json.dumps({"representations": representations or []}),
            encoding="utf-8",
        )
        evidence_sha = MODULE.sha256_file(evidence_path)
        representation_sha = MODULE.sha256_file(plan_path)
        promotion = {
            "promoted_artifacts": {
                "media_evidence_ledger": {
                    "path": str(evidence_path),
                    "sha256": evidence_sha,
                },
                "media_representation_plan": {
                    "path": str(plan_path),
                    "sha256": representation_sha,
                },
            },
        }
        render_plan = {
            "media_evidence_ledger_sha256": evidence_sha,
            "media_representation_plan_sha256": representation_sha,
            "nodes": nodes or [],
        }
        return render_plan, promotion

    def test_media_less_render_plan_binds_exact_active_spec03_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, promotion = self.media_parent_fixture(Path(tmp))
            result = MODULE.validate_media_parent_binding(plan, promotion)
            self.assertEqual(result, {"media_nodes": 0, "closed_representations": 0})

    def test_media_parent_binding_rejects_top_level_plan_hash_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, promotion = self.media_parent_fixture(Path(tmp))
            plan["media_representation_plan_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "exact active media representation plan"):
                MODULE.validate_media_parent_binding(plan, promotion)

    def test_media_parent_binding_rejects_per_node_plan_hash_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            representation = {
                "representation_id": "rep-1",
                "status": "closed",
                "artifact_sha256": "a" * 64,
                "source_block_ids": ["image-1"],
            }
            node = {
                "node_kind": "media",
                "source_block_ids": ["image-1"],
                "payload": {
                    "artifact_sha256": "a" * 64,
                    "media_binding": {
                        "representation_id": "rep-1",
                        "artifact_sha256": "a" * 64,
                        "media_representation_plan_sha256": "0" * 64,
                    },
                },
            }
            plan, promotion = self.media_parent_fixture(
                Path(tmp), representations=[representation], nodes=[node]
            )
            with self.assertRaisesRegex(ValueError, "media plan hash drift"):
                MODULE.validate_media_parent_binding(plan, promotion)

    def volume_nodes(self):
        result = []
        for order in range(1, 7):
            top = order in {1, 4}
            result.append({
                "render_node_id": f"render::{order}", "render_order": order,
                "node_kind": "book_structure" if top else "plain_body",
                "source_block_ids": [f"s{order}"], "output_anchor_id": f"anchor::{order}",
                "parent_output_anchor_id": None if top else ("anchor::1" if order < 4 else "anchor::4"),
                "payload": {"raw_content": str(order)}, "payload_hash": MODULE.canonical_hash({"raw_content": str(order)}),
            })
        return result

    def test_single_volume_is_backward_compatible_default(self):
        nodes = self.volume_nodes()
        partition = MODULE.build_volume_partition_plan(nodes, self.policy())
        self.assertEqual(partition["mode"], "single_volume")
        self.assertEqual(len(partition["volumes"]), 1)
        self.assertEqual(partition["volumes"][0]["render_node_ids"], [item["render_node_id"] for item in nodes])
        self.assertEqual(MODULE.validate_volume_partition_plan(partition, nodes)["source_atoms"], 6)

    def test_two_volume_partition_freezes_top_level_boundary_and_exact_coverage(self):
        nodes = self.volume_nodes()
        policy = self.policy()
        policy["volume_partition"] = {
            "mode": "two_volume", "decision_refs": ["VOL-D1"],
            "trigger_evidence": [{"kind": "delivery_asset_report", "file_entities": 2001}],
            "non_media_file_entity_allowance": 3, "non_media_zip_bytes_allowance": 1000,
            "boundary": {"before_render_node_id": "render::4", "semantic_boundary_type": "chapter", "source_evidence": ["outline-2"]},
            "volumes": [
                {"volume_id": "volume-01", "ordinal": 1, "label": "Volume I", "filename_suffix": "volume-i", "metadata_overrides": {"subtitle": "Volume I"}, "delivery_capacity_preflight": {"estimated_generated_body_bytes_upper_bound": 3000000, "estimated_editable_text_bytes_upper_bound": 3500000, "largest_atomic_tex_line_bytes_upper_bound": 1000, "evidence_refs": ["CAP-1"]}},
                {"volume_id": "volume-02", "ordinal": 2, "label": "Volume II", "filename_suffix": "volume-ii", "metadata_overrides": {"subtitle": "Volume II"}, "delivery_capacity_preflight": {"estimated_generated_body_bytes_upper_bound": 2500000, "estimated_editable_text_bytes_upper_bound": 3000000, "largest_atomic_tex_line_bytes_upper_bound": 1000, "evidence_refs": ["CAP-2"]}},
            ],
        }
        MODULE.validate_policy(policy)
        partition = MODULE.build_volume_partition_plan(nodes, policy)
        self.assertEqual([item["render_order_end"] for item in partition["volumes"]], [3, 6])
        self.assertEqual(partition["boundary"]["before_render_node_id"], "render::4")
        self.assertEqual(partition["schema_version"], "volume-partition-plan/1.2")
        self.assertEqual(partition["volumes"][0]["budget_estimate"]["estimated_tex_shard_count"], 4)
        self.assertEqual(partition["volumes"][0]["body_units"][0]["render_node_ids"], ["render::1", "render::2", "render::3"])
        self.assertEqual(MODULE.validate_volume_partition_plan(partition, nodes)["volumes"], 2)

    def test_two_volume_partition_rejects_cut_inside_parent_group(self):
        nodes = self.volume_nodes()
        policy = self.policy()
        policy["volume_partition"] = {
            "mode": "two_volume", "decision_refs": ["VOL-D1"],
            "trigger_evidence": [{"kind": "delivery_asset_report", "file_entities": 2001}],
            "non_media_file_entity_allowance": 3, "non_media_zip_bytes_allowance": 1000,
            "boundary": {"before_render_node_id": "render::3", "semantic_boundary_type": "paragraph", "source_evidence": ["page-1"]},
            "volumes": [
                {"volume_id": "volume-01", "ordinal": 1, "label": "Volume I", "filename_suffix": "volume-i", "metadata_overrides": {"subtitle": "Volume I"}, "delivery_capacity_preflight": {"estimated_generated_body_bytes_upper_bound": 3000, "estimated_editable_text_bytes_upper_bound": 5000, "largest_atomic_tex_line_bytes_upper_bound": 100, "evidence_refs": ["CAP-1"]}},
                {"volume_id": "volume-02", "ordinal": 2, "label": "Volume II", "filename_suffix": "volume-ii", "metadata_overrides": {"subtitle": "Volume II"}, "delivery_capacity_preflight": {"estimated_generated_body_bytes_upper_bound": 3000, "estimated_editable_text_bytes_upper_bound": 5000, "largest_atomic_tex_line_bytes_upper_bound": 100, "evidence_refs": ["CAP-2"]}},
            ],
        }
        with self.assertRaisesRegex(ValueError, "parent-anchor dependencies|top-level source-supported structure boundary"):
            MODULE.build_volume_partition_plan(nodes, policy)

    def test_two_volume_partition_rejects_atomic_tex_line_at_900k(self):
        nodes = self.volume_nodes()
        policy = self.policy()
        policy["volume_partition"] = {
            "mode": "two_volume", "decision_refs": ["VOL-D1"],
            "trigger_evidence": [{"kind": "body_part_limit", "bytes": 900000}],
            "non_media_file_entity_allowance": 3, "non_media_zip_bytes_allowance": 1000,
            "boundary": {"before_render_node_id": "render::4", "semantic_boundary_type": "chapter", "source_evidence": ["outline-2"]},
            "volumes": [
                {"volume_id": "volume-01", "ordinal": 1, "label": "Volume I", "filename_suffix": "volume-i", "metadata_overrides": {"subtitle": "Volume I"}, "delivery_capacity_preflight": {"estimated_generated_body_bytes_upper_bound": 6000000, "estimated_editable_text_bytes_upper_bound": 7000000, "largest_atomic_tex_line_bytes_upper_bound": 900000, "evidence_refs": ["CAP-1"]}},
                {"volume_id": "volume-02", "ordinal": 2, "label": "Volume II", "filename_suffix": "volume-ii", "metadata_overrides": {"subtitle": "Volume II"}, "delivery_capacity_preflight": {"estimated_generated_body_bytes_upper_bound": 1000, "estimated_editable_text_bytes_upper_bound": 2000, "largest_atomic_tex_line_bytes_upper_bound": 100, "evidence_refs": ["CAP-2"]}},
            ],
        }
        with self.assertRaisesRegex(ValueError, "atomic TeX line"):
            MODULE.build_volume_partition_plan(nodes, policy)


if __name__ == "__main__":
    unittest.main()
