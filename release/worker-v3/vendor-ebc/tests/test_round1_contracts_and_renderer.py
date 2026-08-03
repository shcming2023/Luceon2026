from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
SKILLS = Path.home() / ".codex/skills"
VALIDATOR_PATH = SKILLS / "luceon-popo-to-refined-elegantbook/scripts/validate_intermediate_contracts.py"
RENDERER_PATH = SKILLS / "cleanlatex-to-elegantbook/scripts/render_frozen_plan.py"
FREEZER_PATH = SKILLS / "cleanlatex-to-elegantbook/scripts/freeze_template_contract.py"
AUDITOR_PATH = SKILLS / "refine-elegantbook-latex/scripts/refine_elegantbook_latex.py"
SAMPLE = WORKSPACE / "golden_samples/golden-sample-001"
SEMANTIC = SAMPLE / "runs/semantic-review-v8"
INTAKE = SAMPLE / "runs/intake-v2"
COMPILE = SAMPLE / "runs/compile-v8"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RoundOneGoldenRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = load_module(VALIDATOR_PATH, "round1_validator_test")
        cls.ledger = SEMANTIC / "ledgers/canonical_block_ledger.jsonl"
        cls.decision = SEMANTIC / "decisions/canonical_decision_index.json"
        cls.plan = SEMANTIC / "render/render_plan.json"
        cls.contract = COMPILE / "contracts/template_contract.json"
        cls.template = INTAKE / "inputs/template/unpacked"
        cls.capability = SEMANTIC / "template/template_capability_manifest.json"

    def validate(self, ledger=None, decision=None, plan=None, template=None):
        return self.validator.validate(
            ledger or self.ledger,
            decision or self.decision,
            plan or self.plan,
            self.contract,
            template or self.template,
            self.capability,
        )

    def test_current_golden_contracts_pass(self) -> None:
        report = self.validate()
        self.assertEqual("passed", report["status"])
        self.assertEqual(11, report["summary"]["passed"])

    def test_tampered_ledger_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "ledger.jsonl"
            lines = self.ledger.read_text(encoding="utf-8").splitlines()
            record = json.loads(lines[1])
            record["raw_content"] = str(record["raw_content"]) + "tamper"
            lines[1] = json.dumps(record, ensure_ascii=False)
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
            report = self.validate(ledger=target)
            self.assertEqual("failed", report["status"])
            self.assertEqual("failed", next(item for item in report["checks"] if item["check_id"] == "IC-H02-ledger-identity")["status"])

    def test_open_decision_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "decision.json"
            document = json.loads(self.decision.read_text(encoding="utf-8"))
            document["decisions"][0]["status"] = "open"
            document["summary"]["open"] = 1
            target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            report = self.validate(decision=target)
            self.assertEqual("failed", report["status"])
            self.assertEqual("failed", next(item for item in report["checks"] if item["check_id"] == "IC-H04-decision-closure")["status"])

    def test_tampered_render_payload_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "render_plan.json"
            document = json.loads(self.plan.read_text(encoding="utf-8"))
            document["nodes"][0]["payload"]["title"] += " tamper"
            target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            report = self.validate(plan=target)
            self.assertEqual("failed", report["status"])
            self.assertEqual("failed", next(item for item in report["checks"] if item["check_id"] == "IC-H06-render-freeze")["status"])

    def test_template_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            template = Path(temporary) / "template"
            shutil.copytree(self.template, template)
            with (template / "elegantbook.cls").open("a", encoding="utf-8") as stream:
                stream.write("\n% tamper\n")
            report = self.validate(template=template)
            self.assertEqual("failed", report["status"])
            self.assertEqual("failed", next(item for item in report["checks"] if item["check_id"] == "IC-H10-template-bytes")["status"])

    def test_mechanical_renderer_reproduces_accepted_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "render"
            command = [
                sys.executable, str(RENDERER_PATH),
                "--template-dir", str(self.template),
                "--template-contract", str(self.contract),
                "--ledger", str(self.ledger),
                "--decision-index", str(self.decision),
                "--render-plan", str(self.plan),
                "--capability-manifest", str(self.capability),
                "--asset-root", str(INTAKE / "inputs/mineru"),
                "--asset-root", str(INTAKE / "inputs/minerupopo"),
                "--source-pdf", str(INTAKE / "inputs/source/新教材全解 五上 数学.pdf"),
                "--source-page-dir", str(INTAKE / "evidence/source_pdf/pages"),
                "--out-dir", str(output),
            ]
            completed = subprocess.run(command, text=True, capture_output=True, timeout=180)
            self.assertEqual(0, completed.returncode, completed.stderr)
            actual = output / "delivery/elegantbook-project.zip"
            expected = COMPILE / "delivery/elegantbook-project.zip"
            self.assertEqual(sha256(expected), sha256(actual))

    def test_template_contract_freezer_produces_a_valid_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frozen = root / "template_contract.json"
            capability_report = root / "capability_validation.json"
            completed = subprocess.run([
                sys.executable, str(FREEZER_PATH),
                "--template-dir", str(self.template),
                "--template-zip", str(INTAKE / "inputs/template/2025教材模版新版.zip"),
                "--capability-manifest", str(self.capability),
                "--metadata-config", str(SAMPLE / "configs/spec05_metadata.json"),
                "--presentation-config", str(SAMPLE / "configs/spec05_presentation_config.json"),
                "--body-marker", "% ———————————————————— 正文内容从这里开始 ————————————————————",
                "--output", str(frozen),
                "--validation-report", str(capability_report),
            ], text=True, capture_output=True, timeout=30)
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual("template-contract/2.0", json.loads(frozen.read_text(encoding="utf-8"))["schema_version"])
            report = self.validator.validate(self.ledger, self.decision, self.plan, frozen, self.template, self.capability)
            self.assertEqual("passed", report["status"])
            self.assertEqual("passed", json.loads(capability_report.read_text(encoding="utf-8"))["status"])

    def test_refine_skill_rejects_polish_and_audit_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rejected = subprocess.run([
                sys.executable, str(AUDITOR_PATH), "--zip", str(COMPILE / "delivery/elegantbook-project.zip"),
                "--out-dir", str(root / "rejected"), "--mode", "polish",
            ], text=True, capture_output=True, timeout=30)
            self.assertNotEqual(0, rejected.returncode)
            self.assertIn("invalid choice", rejected.stderr)
            audited = subprocess.run([
                sys.executable, str(AUDITOR_PATH), "--zip", str(COMPILE / "delivery/elegantbook-project.zip"),
                "--out-dir", str(root / "audit"), "--mode", "audit",
            ], text=True, capture_output=True, timeout=30)
            self.assertEqual(0, audited.returncode, audited.stderr)
            report = json.loads((root / "audit/latex_polish_report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["read_only_guarantee"]["input_unchanged"])
            self.assertEqual([], report["changes"])
            self.assertFalse((root / "audit/refined-overleaf.zip").exists())


if __name__ == "__main__":
    unittest.main()
