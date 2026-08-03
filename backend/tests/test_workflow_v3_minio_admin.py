from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
from pathlib import Path

import pytest

from app.workflow_v3.minio_role_policy import (
    MINIO_ROLES,
    credential_fingerprint,
    parse_credential_fingerprints,
    role_policy_documents,
)


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "workflow_v3_minio_admin.py"
)


def _script_module():
    spec = importlib.util.spec_from_file_location("workflow_v3_minio_admin", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _policies():
    return role_policy_documents(
        candidate_bucket="worker-v3-candidates",
        candidate_prefix="v3/candidates",
        formal_bucket="eduassets-elegantbook",
        formal_prefix="elegantbook/v3",
        source_buckets=(
            "eduassets-input",
            "eduassets-mineru",
            "eduassets-minerupopo",
        ),
    )


def test_default_source_buckets_cover_material_source_pdf_and_frozen_parse_assets():
    module = _script_module()

    assert module._SOURCE_BUCKETS == (
        "eduassets-input",
        "eduassets-parsed",
        "eduassets-mineru",
        "eduassets-minerupopo",
    )
    policies = role_policy_documents(
        candidate_bucket="worker-v3-candidates",
        candidate_prefix="v3/candidates",
        formal_bucket="eduassets-elegantbook",
        formal_prefix="elegantbook/v3",
        source_buckets=module._SOURCE_BUCKETS,
    )
    producer_reads = _statements(
        policies["producer"],
        effect="Allow",
        action="s3:GetObject",
    )
    assert "arn:aws:s3:::eduassets-parsed/*" in producer_reads[0]["Resource"]


def _statements(policy, *, effect: str, action: str):
    return [
        statement
        for statement in policy["Statement"]
        if statement["Effect"] == effect and action in statement["Action"]
    ]


def test_role_policies_are_prefix_scoped_and_compatible_with_deployed_minio():
    policies = _policies()
    assert set(policies) == set(MINIO_ROLES)
    candidate = "arn:aws:s3:::worker-v3-candidates/v3/candidates/*"
    formal = "arn:aws:s3:::eduassets-elegantbook/elegantbook/v3/*"

    producer_put = _statements(
        policies["producer"],
        effect="Allow",
        action="s3:PutObject",
    )
    assert producer_put == [
        {
            "Sid": "WriteCandidatePrefix",
            "Effect": "Allow",
            "Action": ["s3:PutObject"],
            "Resource": [candidate],
        }
    ]
    assert formal not in producer_put[0]["Resource"]
    for role in ("evaluator", "promoter"):
        assert not _statements(
            policies[role],
            effect="Allow",
            action="s3:PutObject",
        )
        assert _statements(
            policies[role],
            effect="Deny",
            action="s3:PutObject",
        )
    projector_put = _statements(
        policies["projector"],
        effect="Allow",
        action="s3:PutObject",
    )
    assert projector_put[0]["Resource"] == [formal]

    for policy in policies.values():
        assert all(
            "Resource" in statement and "NotResource" not in statement
            for statement in policy["Statement"]
        )
        delete_denies = _statements(
            policy,
            effect="Deny",
            action="s3:DeleteObject",
        )
        assert delete_denies
        assert delete_denies[0]["Resource"] == ["arn:aws:s3:::*/*"]


def test_fingerprint_matrix_requires_all_distinct_roles():
    credentials = {
        role: (f"access-{role}", f"secret-{role}")
        for role in MINIO_ROLES
    }
    raw = ",".join(
        f"{role}:{credential_fingerprint(*credentials[role])}"
        for role in MINIO_ROLES
    )
    assert parse_credential_fingerprints(raw) == {
        role: credential_fingerprint(*credentials[role])
        for role in MINIO_ROLES
    }
    with pytest.raises(ValueError, match="four distinct"):
        parse_credential_fingerprints(
            raw.replace(
                credential_fingerprint(*credentials["promoter"]),
                credential_fingerprint(*credentials["evaluator"]),
            )
        )
    with pytest.raises(ValueError, match="four distinct"):
        parse_credential_fingerprints(
            raw.replace(
                credential_fingerprint(*credentials["promoter"]),
                credential_fingerprint(
                    credentials["promoter"][0],
                    credentials["evaluator"][1],
                ),
            )
        )


def test_dry_run_is_non_mutating_and_never_prints_credentials(
    tmp_path,
    capsys,
    monkeypatch,
):
    module = _script_module()
    target = tmp_path / "minio-roles.env"
    monkeypatch.setenv("MINIO_ADMIN_ACCESS_KEY", "admin-access-sensitive")
    monkeypatch.setenv("MINIO_ADMIN_SECRET_KEY", "admin-secret-sensitive")

    assert module.main(["--dry-run", "--runtime-env", str(target)]) == 0

    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["ok"] is True
    assert payload["mutated"] is False
    assert not target.exists()
    assert "admin-access-sensitive" not in output.out + output.err
    assert "admin-secret-sensitive" not in output.out + output.err


def test_runtime_env_is_0600_and_contains_only_role_runtime_credentials(tmp_path):
    module = _script_module()
    target = tmp_path / "minio-roles.env"
    credentials = {
        role: (f"access-{role}", f"secret-{role}")
        for role in MINIO_ROLES
    }
    content = module._runtime_env_text(
        endpoint="minio:9000",
        secure=False,
        region="us-east-1",
        credentials=credentials,
    )

    module._write_runtime_env(target, content)

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    text = target.read_text(encoding="utf-8")
    assert "MINIO_ADMIN" not in text
    assert not any(
        line.startswith(("MINIO_ACCESS_KEY=", "MINIO_SECRET_KEY="))
        for line in text.splitlines()
    )
    for role in MINIO_ROLES:
        assert f"WORKFLOW_V3_{role.upper()}_MINIO_ACCESS_KEY=" in text
        assert f"WORKFLOW_V3_{role.upper()}_MINIO_SECRET_KEY=" in text
    matrix = next(
        line.split("=", 1)[1]
        for line in text.splitlines()
        if line.startswith("WORKFLOW_V3_MINIO_CREDENTIAL_FINGERPRINTS=")
    )
    parsed = parse_credential_fingerprints(matrix)
    assert parsed["producer"] == (
        hashlib.sha256(b"access-producer").hexdigest()
        + "."
        + hashlib.sha256(b"secret-producer").hexdigest()
    )


def test_verify_only_requires_existing_private_runtime_env(tmp_path, capsys):
    module = _script_module()
    result = module.main(
        [
            "--verify-only",
            "--runtime-env",
            str(tmp_path / "missing.env"),
        ]
    )

    assert result == 2
    payload = json.loads(capsys.readouterr().err)
    assert "existing 0600 runtime env" in payload["error"]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {
                "status": "success",
                "versioning": {"status": "", "MFADelete": ""},
            },
            "disabled",
        ),
        (
            {
                "status": "success",
                "versioning": {"status": "Enabled", "MFADelete": ""},
            },
            "enabled",
        ),
        ({"versioning": "Suspended"}, "suspended"),
    ],
)
def test_versioning_status_uses_bucket_state_not_mc_command_status(
    payload,
    expected,
):
    module = _script_module()
    assert module._parse_versioning_status(payload) == expected


def test_admin_command_errors_redact_all_credentials():
    module = _script_module()
    with pytest.raises(module.MinioBootstrapError) as captured:
        module._run_mc(
            "/bin/sh",
            ["-c", "echo admin-secret role-secret >&2; exit 1"],
            environment={},
            secrets_to_hide=("admin-secret", "role-secret"),
        )

    assert "admin-secret" not in str(captured.value)
    assert "role-secret" not in str(captured.value)
    assert str(captured.value).count("<redacted>") >= 2
