from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "audit_generic_hardcoding.py"


def run_audit(tmp_path: Path, source: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    target = tmp_path / "runtime.py"
    target.write_text(source, encoding="utf-8")
    surface = {
        "schema_version": "elegantbookcompiler-release-code-surface/1.0",
        "workspace_files": ["runtime.py"],
        "shared_skill_script_roots": [],
        "shared_skill_files": [],
        "forbidden_sample_literals": ["known-sample-title"],
        "forbidden_sample_hashes": [],
        "page_constant_review_policy": "test",
    }
    surface_path = tmp_path / "surface.json"
    output = tmp_path / "report.json"
    surface_path.write_text(json.dumps(surface), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(AUDIT), "--workspace", str(tmp_path), "--surface", str(surface_path), "--output", str(output)],
        text=True,
        capture_output=True,
        check=False,
    )
    return result, json.loads(output.read_text(encoding="utf-8"))


def test_hardcoding_audit_rejects_sample_literal_and_fixed_page(tmp_path: Path) -> None:
    result, report = run_audit(
        tmp_path,
        "TITLE = 'known-sample-title'\n"
        "def bad(page_number):\n"
        "    return page_number == 29\n",
    )
    assert result.returncode == 1
    assert report["status"] == "failed"
    kinds = {item["kind"] for item in report["violations"]}
    assert {"sample_literal", "fixed_page_constant"} <= kinds


def test_hardcoding_audit_allows_generic_page_sentinel(tmp_path: Path) -> None:
    result, report = run_audit(
        tmp_path,
        "def valid(page_number):\n"
        "    return page_number >= 1\n",
    )
    assert result.returncode == 0
    assert report["status"] == "passed"
    assert report["page_constant_review"]["status"] == "passed_generic_sentinels_only"


def test_release_recompile_safe_extract_rejects_traversal(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("recompile_release_products", ROOT / "scripts/recompile_release_products.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape.txt", "bad")
    destination = tmp_path / "out"
    destination.mkdir()
    try:
        module.safe_extract(archive, destination)
    except ValueError as exc:
        assert "unsafe ZIP path" in str(exc)
    else:
        raise AssertionError("unsafe archive was accepted")
