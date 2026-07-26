#!/usr/bin/env python3
"""Enforce TP-H14 against the exact generated body.

The template capability manifest is the only authority for which commands and
environments are template-local. Standard LaTeX constructs and declared
tcolorbox styles are intentionally outside this gate.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "spec05-template-local-api-usage/1.0"
GATE_ID = "TP-H14"

COMMAND_DEFINITION_PATTERNS = (
    re.compile(r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand|NewDocumentCommand|RenewDocumentCommand|ProvideDocumentCommand)\s*\{?\s*\\([A-Za-z@]+)"),
    re.compile(r"\\(?:def|gdef|edef|xdef)\s*\\([A-Za-z@]+)"),
)
ENVIRONMENT_DEFINITION_PATTERN = re.compile(
    r"\\(?:newenvironment|renewenvironment|provideenvironment|NewDocumentEnvironment|RenewDocumentEnvironment|ProvideDocumentEnvironment|DeclareDocumentEnvironment)\s*\{\s*([^{}\s]+)\s*\}"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strip_tex_comments(text: str) -> str:
    cleaned: list[str] = []
    for line in text.splitlines(keepends=True):
        cut = len(line)
        for index, char in enumerate(line):
            if char != "%":
                continue
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                cut = index
                break
        suffix = "\n" if line.endswith("\n") else ""
        cleaned.append(line[:cut].rstrip("\r\n") + suffix)
    return "".join(cleaned)


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def scan_text(custom_commands: list[str], custom_environments: list[str], body: str) -> list[dict[str, Any]]:
    scanned = strip_tex_comments(body)
    violations: list[dict[str, Any]] = []
    for pattern in COMMAND_DEFINITION_PATTERNS:
        for match in pattern.finditer(scanned):
            name = match.group(1)
            if name in custom_commands:
                violations.append({"kind": "template_local_command_definition", "name": name, "line": line_number(scanned, match.start())})
    for match in ENVIRONMENT_DEFINITION_PATTERN.finditer(scanned):
        name = match.group(1)
        if name in custom_environments:
            violations.append({"kind": "template_local_environment_definition", "name": name, "line": line_number(scanned, match.start())})
    for name in sorted(custom_commands):
        pattern = re.compile(rf"\\{re.escape(name)}(?![A-Za-z@])")
        for match in pattern.finditer(scanned):
            violations.append({"kind": "template_local_command_call", "name": name, "line": line_number(scanned, match.start())})
    for name in sorted(custom_environments):
        pattern = re.compile(rf"\\(begin|end)\s*\{{\s*{re.escape(name)}\s*\}}")
        for match in pattern.finditer(scanned):
            violations.append({"kind": "template_local_environment_call", "name": name, "line": line_number(scanned, match.start())})
    return sorted(violations, key=lambda item: (item["line"] or 0, item["kind"], item["name"]))


def audit_template_local_api_usage(capability_manifest_path: Path, rendered_body_path: Path) -> dict[str, Any]:
    capability_manifest_path = capability_manifest_path.resolve()
    rendered_body_path = rendered_body_path.resolve()
    capability = json.loads(capability_manifest_path.read_text(encoding="utf-8"))
    constructs = capability.get("constructs")
    if not isinstance(constructs, dict):
        constructs = capability if {"custom_commands", "custom_environments"}.issubset(capability) else None
    if not isinstance(constructs, dict):
        raise ValueError("capability manifest lacks constructs inventory")
    custom_commands = constructs.get("custom_commands")
    custom_environments = constructs.get("custom_environments")
    if not isinstance(custom_commands, list) or not isinstance(custom_environments, list):
        raise ValueError("capability manifest lacks explicit template-local API inventories")
    inventory = [*custom_commands, *custom_environments]
    if any(not isinstance(name, str) or not name for name in inventory):
        raise ValueError("template-local API inventory contains an invalid name")
    if len(custom_commands) != len(set(custom_commands)) or len(custom_environments) != len(set(custom_environments)):
        raise ValueError("template-local API inventory contains duplicate names")
    violations = scan_text(custom_commands, custom_environments, rendered_body_path.read_text(encoding="utf-8"))
    status = "passed" if not violations else "failed"
    return {
        "schema_version": SCHEMA_VERSION,
        "spec_status": status,
        "gate": {"gate_id": GATE_ID, "status": status},
        "inputs": {
            "capability_manifest": {"path": str(capability_manifest_path), "sha256": sha256_file(capability_manifest_path)},
            "rendered_body": {"path": str(rendered_body_path), "sha256": sha256_file(rendered_body_path)},
        },
        "inventory": {
            "template_local_custom_commands": sorted(custom_commands),
            "template_local_custom_environments": sorted(custom_environments),
        },
        "violations": violations,
        "summary": {"violations": len(violations)},
        "scope_limit": "Producer TP-H14 evidence for the exact generated body; promotion requires an independent evaluator rescan.",
    }
