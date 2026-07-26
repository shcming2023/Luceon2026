from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("WORKFLOW_V3_MINIO_INTEGRATION", "").strip().lower()
    not in {"1", "true", "yes", "on"},
    reason="set WORKFLOW_V3_MINIO_INTEGRATION=1 for live MinIO role probes",
)


def test_live_minio_role_policy_matrix():
    runtime_env = os.getenv("WORKFLOW_V3_MINIO_RUNTIME_ENV", "").strip()
    source_probe = os.getenv("WORKFLOW_V3_MINIO_SOURCE_PROBE", "").strip()
    if not runtime_env or not source_probe:
        pytest.skip(
            "WORKFLOW_V3_MINIO_RUNTIME_ENV and WORKFLOW_V3_MINIO_SOURCE_PROBE "
            "are required"
        )
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "workflow_v3_minio_admin.py"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--verify-only",
            "--require-versioning",
            "--runtime-env",
            runtime_env,
            "--source-probe",
            source_probe,
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
    assert '"ok": true' in result.stdout
