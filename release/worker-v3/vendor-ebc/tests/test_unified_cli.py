from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "elegantbookcompiler.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(tmp_path: Path, stages: list[dict]) -> Path:
    template = tmp_path / "template.zip"
    template.write_bytes(b"fixed-template")
    manifest = {
        "schema_version": "elegantbookcompiler-pipeline/1.0",
        "pipeline_id": "cli-test",
        "fixed_template": {"path": str(template), "sha256": digest(template)},
        "stages": stages,
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], text=True, capture_output=True, check=False)


def test_run_and_resume_without_reexecution(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    output = tmp_path / "out.txt"
    counter = tmp_path / "counter.txt"
    code = (
        "from pathlib import Path; "
        f"p=Path({str(counter)!r}); n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n)); "
        f"Path({str(output)!r}).write_text('done')"
    )
    stage = {
        "stage_id": "stage-1",
        "owner": "test-owner",
        "execution_mode": "execute",
        "requires": [],
        "command_argv": [sys.executable, "-c", code],
        "inputs": [{"path": str(source), "sha256": digest(source)}],
        "outputs": [{"path": str(output)}],
        "resources": {"llm": False, "gpu": False, "external_write": False},
    }
    manifest = write_manifest(tmp_path, [stage])
    state = tmp_path / "state.json"
    first = run_cli("run", "--manifest", str(manifest), "--workspace", str(tmp_path), "--state", str(state))
    assert first.returncode == 0, first.stderr
    assert counter.read_text() == "1"
    resumed = run_cli("run", "--resume", "--manifest", str(manifest), "--workspace", str(tmp_path), "--state", str(state))
    assert resumed.returncode == 0, resumed.stderr
    assert counter.read_text() == "1"
    assert json.loads(state.read_text())["stages"]["stage-1"]["resume_action"] == "verified_and_skipped"


def test_resume_rejects_output_drift(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    output = tmp_path / "out.txt"
    stage = {
        "stage_id": "stage-1",
        "owner": "test-owner",
        "execution_mode": "execute",
        "command_argv": [sys.executable, "-c", f"from pathlib import Path; Path({str(output)!r}).write_text('done')"],
        "inputs": [{"path": str(source), "sha256": digest(source)}],
        "outputs": [{"path": str(output)}],
        "resources": {"external_write": False},
    }
    manifest = write_manifest(tmp_path, [stage])
    state = tmp_path / "state.json"
    assert run_cli("run", "--manifest", str(manifest), "--workspace", str(tmp_path), "--state", str(state)).returncode == 0
    output.write_text("drift", encoding="utf-8")
    resumed = run_cli("run", "--resume", "--manifest", str(manifest), "--workspace", str(tmp_path), "--state", str(state))
    assert resumed.returncode == 2
    assert "output missing or drifted" in resumed.stderr


def test_failed_stage_stops_dependents(tmp_path: Path) -> None:
    marker = tmp_path / "should-not-exist"
    stages = [
        {
            "stage_id": "fail",
            "owner": "test-owner",
            "execution_mode": "verify_only",
            "command_argv": [sys.executable, "-c", "raise SystemExit(7)"],
            "resources": {"external_write": False},
        },
        {
            "stage_id": "dependent",
            "owner": "test-owner",
            "execution_mode": "execute",
            "requires": ["fail"],
            "command_argv": [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
            "outputs": [{"path": str(marker)}],
            "resources": {"external_write": False},
        },
    ]
    manifest = write_manifest(tmp_path, stages)
    state = tmp_path / "state.json"
    result = run_cli("run", "--manifest", str(manifest), "--workspace", str(tmp_path), "--state", str(state))
    assert result.returncode == 7
    assert not marker.exists()
    assert json.loads(state.read_text())["status"] == "failed"


def test_manifest_rejects_external_write_and_cycle(tmp_path: Path) -> None:
    stages = [
        {
            "stage_id": "a",
            "owner": "owner",
            "execution_mode": "verify_only",
            "requires": ["b"],
            "command_argv": [sys.executable, "-c", "pass"],
            "resources": {"external_write": True},
        },
        {
            "stage_id": "b",
            "owner": "owner",
            "execution_mode": "verify_only",
            "requires": ["a"],
            "command_argv": [sys.executable, "-c", "pass"],
        },
    ]
    manifest = write_manifest(tmp_path, stages)
    result = run_cli("validate", "--manifest", str(manifest), "--workspace", str(tmp_path))
    assert result.returncode == 2
    assert "external_writes_forbidden" in result.stderr
    assert "acyclic" in result.stderr


def test_llm_and_gpu_require_explicit_authority(tmp_path: Path) -> None:
    stages = [
        {
            "stage_id": "gated",
            "owner": "owner",
            "execution_mode": "verify_only",
            "command_argv": [sys.executable, "-c", "pass"],
            "resources": {"llm": True, "gpu": True, "external_write": False},
        }
    ]
    manifest = write_manifest(tmp_path, stages)
    state = tmp_path / "state.json"
    result = run_cli("run", "--manifest", str(manifest), "--workspace", str(tmp_path), "--state", str(state))
    assert result.returncode == 2
    assert "requires LLM" in result.stderr


def test_downstream_input_binds_recorded_parent_output(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    stages = [
        {
            "stage_id": "producer",
            "owner": "owner-a",
            "execution_mode": "execute",
            "command_argv": [sys.executable, "-c", f"from pathlib import Path; Path({str(first)!r}).write_text('one')"],
            "outputs": [{"path": str(first)}],
            "resources": {"external_write": False},
        },
        {
            "stage_id": "consumer",
            "owner": "owner-b",
            "execution_mode": "execute",
            "requires": ["producer"],
            "command_argv": [sys.executable, "-c", f"from pathlib import Path; Path({str(second)!r}).write_text(Path({str(first)!r}).read_text() + '-two')"],
            "inputs": [{"path": str(first), "from_stage": "producer"}],
            "outputs": [{"path": str(second)}],
            "resources": {"external_write": False},
        },
    ]
    manifest = write_manifest(tmp_path, stages)
    state = tmp_path / "state.json"
    result = run_cli("run", "--manifest", str(manifest), "--workspace", str(tmp_path), "--state", str(state))
    assert result.returncode == 0, result.stderr
    assert second.read_text() == "one-two"
    payload = json.loads(state.read_text())
    assert payload["stages"]["consumer"]["inputs"][0]["sha256"] == payload["stages"]["producer"]["outputs"][0]["sha256"]
