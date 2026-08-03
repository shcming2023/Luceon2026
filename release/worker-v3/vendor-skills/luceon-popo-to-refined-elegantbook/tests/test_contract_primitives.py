import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/validate_intermediate_contracts.py"
SPEC = importlib.util.spec_from_file_location("validator", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ContractPrimitiveTests(unittest.TestCase):
    def test_canonical_hash_is_key_order_independent(self):
        self.assertEqual(MODULE.canonical_hash({"b": 2, "a": 1}), MODULE.canonical_hash({"a": 1, "b": 2}))

    def test_documentclass_inventory_preserves_options(self):
        self.assertEqual({"name": "elegantbook", "options": ["11pt", "a4paper"]}, MODULE.documentclass_inventory(r"\documentclass[11pt,a4paper]{elegantbook}"))

    def test_ledger_closure_accepts_both_versioned_summary_field_names(self):
        for field in ("open_source_review_blocks", "open_reviews"):
            result = MODULE._ledger_closure(
                {"spec_status": "passed", "summary": {field: 0}},
                [{"block_id": "b1", "scope_status": "included", "review_required": False}],
            )
            self.assertEqual(0, result["open_source_review_blocks"])
            self.assertEqual([field], result["accepted_summary_fields"])

    def test_ledger_closure_rejects_missing_review_summary(self):
        with self.assertRaisesRegex(ValueError, "declares neither"):
            MODULE._ledger_closure(
                {"spec_status": "passed", "summary": {}},
                [{"block_id": "b1", "scope_status": "included", "review_required": False}],
            )


if __name__ == "__main__":
    unittest.main()
