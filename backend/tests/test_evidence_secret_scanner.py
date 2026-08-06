from __future__ import annotations

from pathlib import Path

from scripts.scan_evidence_secrets import scan_paths


def test_scanner_detects_httponly_netscape_cookie_without_exposing_value(tmp_path: Path):
    cookie = tmp_path / "admin.cookies"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n"
        "#HttpOnly_127.0.0.1\tFALSE\t/\tFALSE\t1999999999\tmineru_session\treplayable-secret-value\n"
    )
    findings = scan_paths([tmp_path])
    assert findings == [{"path": str(cookie), "line": 2, "kind": "netscape_cookie"}]
    assert "replayable-secret-value" not in str(findings)


def test_scanner_detects_headers_jwt_signed_url_and_credentials(tmp_path: Path):
    evidence = tmp_path / "evidence.log"
    evidence.write_text(
        "Set-Cookie: mineru_session=session-value-123; HttpOnly\n"
        "Authorization: Bearer bearer-value-123\n"
        "token=eyJabcdefghijk.abcdefghijkl.abcdefghijkl\n"
        "https://example.test/object?X-Amz-Signature=abcdef0123456789\n"
        "COMPSHARE_PRIVATE_KEY=private-value-123\n"
        "Password=cloud-console-password\n"
        "Signature=control-plane-signature\n"
    )
    kinds = {row["kind"] for row in scan_paths([tmp_path])}
    assert {"set_cookie", "bearer", "jwt", "signed_url", "credential_assignment"} <= kinds


def test_scanner_accepts_sanitized_receipt_and_binary_fixture(tmp_path: Path):
    (tmp_path / "cookie-sanitized.json").write_text(
        '{"cookie_name":"mineru_session","value_present":false,"raw_file_present":false}\n'
    )
    (tmp_path / "source.pdf").write_bytes(b"%PDF-1.4\n\x00binary")
    assert scan_paths([tmp_path]) == []


def test_scanner_accepts_shell_secret_references_but_rejects_literal_values(tmp_path: Path):
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "MINIO_ACCESS_KEY: ${TASK_ACCESS:?runtime value required}\n"
        "MINIO_SECRET_KEY: $TASK_SECRET\n"
        "GPU_WRAPPER_API_KEY: ${WRAPPER_KEY}\n"
        "COMPSHARE_PRIVATE_KEY: literal-secret-value\n"
    )
    assert scan_paths([compose]) == [
        {"path": str(compose), "line": 4, "kind": "credential_assignment"}
    ]
