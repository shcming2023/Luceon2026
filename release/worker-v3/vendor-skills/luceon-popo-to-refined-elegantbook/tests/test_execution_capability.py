import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).parents[1]
SCRIPT = SKILL / "scripts/execution_capability.py"
SPEC = importlib.util.spec_from_file_location("execution_capability_test", SCRIPT)
CAPABILITY = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(CAPABILITY)


class ExecutionCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "test-skill"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "schemas").mkdir()
        (self.root / "SKILL.md").write_text("---\nname: test-skill\ndescription: test\n---\n", encoding="utf-8")
        self.helper = self.root / "scripts/helper.py"
        self.helper.write_text("VALUE = 1\n", encoding="utf-8")
        self.entry = self.root / "scripts/main.py"
        self.entry.write_text("import helper\nVERSION = 'same-version'\n", encoding="utf-8")
        self.schema = self.root / "schemas/input.schema.json"
        self.schema.write_text("{}\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def build(self):
        return CAPABILITY.build_manifest(
            manifest_id="test-capability-v1", skill_root=self.root,
            entrypoints=[("producer", self.entry)], resources=[("machine_schema", self.schema)],
            invocation=["main.py", "--token", "secret-value", "--input", "source.json"],
            producer="test-producer/1.0.0",
        )

    def write(self, value, name="capability.json"):
        path = self.root / name
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def resign(self, value):
        value["payload_hash"] = CAPABILITY.canonical_hash(CAPABILITY.manifest_payload(value))

    def test_capture_and_live_validation_pass_with_secret_redaction(self):
        manifest = self.build()
        self.assertNotIn("secret-value", json.dumps(manifest))
        self.assertEqual(["main.py", "--token", "<redacted>", "--input", "source.json"], manifest["invocation"]["argv"])
        self.assertEqual(1, manifest["summary"]["local_modules"])
        report = CAPABILITY.validate_manifest(self.write(manifest))
        self.assertEqual("passed", report["status"])
        self.assertTrue(report["live_rehash"])

    def test_same_version_different_entrypoint_bytes_are_rejected(self):
        manifest = self.build()
        path = self.write(manifest)
        self.entry.write_text("import helper\nVERSION = 'same-version'\n# byte drift\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "execution capability drift"):
            CAPABILITY.validate_manifest(path)

    def test_unlisted_local_import_is_rejected_even_with_valid_payload_hash(self):
        manifest = self.build()
        manifest["local_code_closure"] = []
        manifest["summary"]["local_modules"] = 0
        self.resign(manifest)
        with self.assertRaisesRegex(ValueError, "local_code_closure"):
            CAPABILITY.validate_manifest(self.write(manifest))

    def test_runtime_drift_is_rejected_even_with_valid_payload_hash(self):
        manifest = self.build()
        manifest["runtime"]["python"]["version"] = "0.0.0-drift"
        self.resign(manifest)
        with self.assertRaisesRegex(ValueError, "runtime"):
            CAPABILITY.validate_manifest(self.write(manifest))

    def test_missing_capability_input_is_rejected(self):
        manifest = self.build()
        path = self.write(manifest)
        self.helper.unlink()
        with self.assertRaisesRegex(ValueError, "execution capability drift"):
            CAPABILITY.validate_manifest(path)


if __name__ == "__main__":
    unittest.main()
