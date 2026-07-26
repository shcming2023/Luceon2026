from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
VALIDATOR = WORKSPACE / "scripts/validate_spec06_failed_review.py"
READING = WORKSPACE / "regression_samples/native-source-reconciliation/sample-003-reading-explorer-1"
GOLDEN = WORKSPACE / "golden_samples/golden-sample-001"


class Spec06FailureContracts(unittest.TestCase):
    def run_validator(self, sample: Path, review_run: str, compile_run: str) -> dict:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "validation.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATOR),
                    "--sample-dir",
                    str(sample),
                    "--review-run",
                    review_run,
                    "--compile-run",
                    compile_run,
                    "--output",
                    str(output),
                ],
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            return json.loads(output.read_text(encoding="utf-8"))

    def test_source_fidelity_failure_is_independently_validated(self) -> None:
        report = self.run_validator(READING, "final-review-failed-v1-cover", "spec05-native-v6-cover")
        self.assertEqual("passed", report["spec_status"])
        self.assertEqual("failed", report["validated_review_status"])
        self.assertTrue(report["checks"]["confirmed_failure_audit_matches_type"])
        self.assertFalse(report["summary"]["final_verified_ledger_created"])
        self.assertFalse(report["summary"]["acceptance_commit_created"])

    def test_existing_toc_failure_remains_supported(self) -> None:
        report = self.run_validator(GOLDEN, "final-review-failed-v3-migration", "compile-v9-migration")
        self.assertEqual("passed", report["spec_status"])
        self.assertTrue(report["checks"]["confirmed_failure_audit_matches_type"])

    def test_structure_board_uses_frozen_outline_evidence(self) -> None:
        manifest = json.loads(
            (READING / "runs/spec06-visual-v3-cover/manifest.json").read_text(encoding="utf-8")
        )
        pages = [item["pdf_physical_page"] for item in manifest["source_outline_evidence"]]
        self.assertEqual([3, 6, 7], pages)
        self.assertNotIn(8, pages)
        self.assertEqual(
            ["READING-V1-COMPOSITE-MEDIA-001", "READING-V1-COMPOSITE-CROP-001"],
            manifest["confirmed_visual_defect_ids"],
        )


if __name__ == "__main__":
    unittest.main()
