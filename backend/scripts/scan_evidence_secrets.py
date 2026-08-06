#!/usr/bin/env python3
"""Fail closed when an evidence tree contains replayable authentication values."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


TEXT_SUFFIXES = {
    "",
    ".cookies",
    ".csv",
    ".env",
    ".html",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
}
PATTERNS = (
    ("set_cookie", re.compile(r"(?im)^\s*set-cookie\s*:\s*[^=;\s]+\s*=\s*([^;\s]+)")),
    ("bearer", re.compile(r"(?i)\bauthorization\s*[:=]\s*bearer\s+([A-Za-z0-9._~+/=-]{8,})")),
    ("jwt", re.compile(r"\b(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b")),
    ("signed_url", re.compile(r"(?i)[?&](?:X-Amz-Signature|Signature)=([A-Za-z0-9%._~-]{8,})")),
    (
        "credential_assignment",
        re.compile(
            r"(?im)^\s*(?:COMPSHARE_(?:PUBLIC|PRIVATE)_KEY|UCLOUD_(?:PUBLIC|PRIVATE)_KEY|GPU_WRAPPER_API_KEY|MINIO_(?:ACCESS|SECRET)_KEY|Password|Signature)\s*[:=]\s*['\"]?([^'\"\s]+)"
        ),
    ),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")),
    (
        "session_value",
        re.compile(r"(?i)[\"']?(?:mineru_session|sessionid|session_id|access_token|auth_token)[\"']?\s*[:=]\s*[\"']([^\"'\s]{8,})"),
    ),
)

SHELL_PLACEHOLDER = re.compile(
    r"^(?:\$[A-Za-z_][A-Za-z0-9_]*|\$\{[A-Za-z_][A-Za-z0-9_]*(?::\?[^}]*)?\})$"
)


def _credential_assignment_is_placeholder(line: str) -> bool:
    assignment = re.split(r"\s*[:=]\s*", line.strip(), maxsplit=1)
    if len(assignment) != 2:
        return False
    return bool(SHELL_PLACEHOLDER.fullmatch(assignment[1].strip().strip("'\"")))


def _text_files(roots: list[Path]):
    for root in roots:
        if root.is_file():
            rows = [root]
        elif root.is_dir():
            rows = sorted(path for path in root.rglob("*") if path.is_file())
        else:
            continue
        for path in rows:
            if path.suffix.lower() in TEXT_SUFFIXES or "cookie" in path.name.lower():
                yield path


def scan_paths(roots: list[Path]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in _text_files(roots):
        raw = path.read_bytes()
        if b"\x00" in raw[:4096]:
            continue
        text = raw.decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            cookie_line = stripped[10:] if stripped.startswith("#HttpOnly_") else stripped
            fields = cookie_line.split("\t")
            if len(fields) >= 7 and fields[0] and fields[5] and fields[6]:
                findings.append({"path": str(path), "line": number, "kind": "netscape_cookie"})
                continue
            for kind, pattern in PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                if kind != "private_key" and not str(match.group(1) or "").strip():
                    continue
                if kind == "credential_assignment" and _credential_assignment_is_placeholder(line):
                    continue
                findings.append({"path": str(path), "line": number, "kind": kind})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    findings = scan_paths(args.paths)
    report = {
        "schema": "luceon.evidence-secret-scan/v1",
        "roots": [str(path) for path in args.paths],
        "finding_count": len(findings),
        "raw_auth_value_denominator": len(findings),
        "findings": findings,
        "passed": not findings,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "finding_count": len(findings)}, sort_keys=True))
    return 0 if not findings else 2


if __name__ == "__main__":
    raise SystemExit(main())
