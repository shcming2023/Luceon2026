#!/usr/bin/env python3
"""Independent promotion evaluator for formal-native Spec 05 execution runs."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable


VERSION = "spec05-native-promotion-gate/1.8.1"
STAGE_SCHEMA = "spec05-native-stage-manifest/1.6"
MAX_DELIVERY_ZIP_BYTES = 50_000_000
MAX_FILE_ENTITIES_EXCLUSIVE = 2_000
RASTER_SUFFIXES = {".jpg", ".jpeg", ".png"}
GENERATED_BODY_PATH = "body/generated-body.tex"
GENERATED_BODY_INPUT = r"\input{body/generated-body.tex}"
GENERATED_PART_RE = re.compile(r"^body/units/unit-(\d{4})/part-(\d{4})\.tex$")
LOADER_LINE_RE = re.compile(r"^\\input\{(body/units/unit-\d{4}/part-\d{4}\.tex)\}$")
MAX_BODY_PART_BYTES_EXCLUSIVE = 900_000
MAX_RASTER_IMAGE_BYTES_EXCLUSIVE = 1_000_000
EDITABLE_TEXT_EXTENSIONS = {".tex", ".bib", ".cls", ".sty", ".bst", ".cfg", ".def"}
MAX_BASENAME_UTF8_BYTES = 240
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

COMMAND_DEFINITION_PATTERNS = (
    re.compile(r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand|NewDocumentCommand|RenewDocumentCommand|ProvideDocumentCommand)\s*\{?\s*\\([A-Za-z@]+)"),
    re.compile(r"\\(?:def|gdef|edef|xdef)\s*\\([A-Za-z@]+)"),
)
ENVIRONMENT_DEFINITION_PATTERN = re.compile(
    r"\\(?:newenvironment|renewenvironment|provideenvironment|NewDocumentEnvironment|RenewDocumentEnvironment|ProvideDocumentEnvironment|DeclareDocumentEnvironment)\s*\{\s*([^{}\s]+)\s*\}"
)


def cleanlatex_skill_root() -> Path:
    return Path(__file__).resolve().parents[2] / "cleanlatex-to-elegantbook"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def independent_delivery_zip_size_scan(zip_path: Path) -> dict[str, Any]:
    size_bytes = zip_path.stat().st_size
    return {
        "sha256": sha256_file(zip_path),
        "size_bytes": size_bytes,
        "operator": "strictly_less_than",
        "max_bytes_exclusive": MAX_DELIVERY_ZIP_BYTES,
        "passed": size_bytes < MAX_DELIVERY_ZIP_BYTES,
    }


def validate_delivery_size_report(zip_path: Path, report_path: Path) -> dict[str, Any]:
    report = read_json(report_path)
    measured = independent_delivery_zip_size_scan(zip_path)
    declared = report.get("delivery_zip", {})
    constraint = report.get("constraint", {})
    if report.get("schema_version") != "spec05-delivery-size-report/1.0":
        raise ValueError("unsupported delivery size report schema")
    if report.get("spec_status") != "passed" or report.get("gate") != {"gate_id": "CP-H18", "status": "passed"}:
        raise ValueError("producer delivery size report did not pass CP-H18")
    declared_path = Path(declared.get("path", ""))
    if declared_path.resolve() != zip_path.resolve():
        raise ValueError("producer delivery size report is bound to a different ZIP path")
    if declared.get("sha256") != measured["sha256"] or declared.get("size_bytes") != measured["size_bytes"]:
        raise ValueError("producer delivery size report differs from independent ZIP measurement")
    if constraint != {
        "operator": "strictly_less_than",
        "max_bytes_exclusive": MAX_DELIVERY_ZIP_BYTES,
        "unit": "bytes",
    }:
        raise ValueError("producer delivery size constraint is missing or was relaxed")
    if report.get("failure_code") is not None:
        raise ValueError("passed producer delivery size report contains a failure code")
    if not measured["passed"]:
        raise ValueError(
            f"COMPILE_DELIVERY_ZIP_SIZE_LIMIT_EXCEEDED: {measured['size_bytes']} bytes is not strictly below "
            f"{MAX_DELIVERY_ZIP_BYTES} bytes"
        )
    return measured


def tex_media_refs(text: str) -> set[str]:
    refs = {
        match.group(1).strip()
        for match in re.finditer(r"\\includegraphics(?:\[[^]]*\])?\{([^{}]+)\}", text)
    }
    for macro in ("cover", "logo"):
        match = re.search(rf"\\{macro}\{{([^{{}}]+)\}}", text)
        if match:
            refs.add(match.group(1).strip())
    return refs


def tex_input_refs(text: str) -> set[str]:
    return {match.group(1).strip() for match in re.finditer(r"\\(?:input|include)\{([^{}]+)\}", text)}


def resolve_tex_ref(ref: str, current: str, names: set[str]) -> str:
    raw = PurePosixPath(ref)
    candidate = PurePosixPath(current).parent / raw
    if raw.is_absolute() or candidate.is_absolute() or ".." in raw.parts or ".." in candidate.parts:
        raise ValueError(f"unsafe TeX input reference: {ref}")
    roots = [raw.as_posix().lstrip("./"), candidate.as_posix().lstrip("./")]
    candidates = []
    for normalized in roots:
        candidates.extend([normalized] if PurePosixPath(normalized).suffix else [normalized + ".tex", normalized])
    candidates = list(dict.fromkeys(candidates))
    matches = [item for item in candidates if item in names]
    if len(matches) != 1:
        raise ValueError(f"TeX input reference must resolve exactly once: {current} -> {ref}")
    return matches[0]


def tex_closure(archive: zipfile.ZipFile, names: set[str]) -> tuple[list[str], set[str]]:
    pending = ["main.tex"]
    visited: list[str] = []
    refs: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.append(current)
        text = archive.read(current).decode("utf-8")
        refs.update(tex_media_refs(text))
        for ref in sorted(tex_input_refs(text)):
            resolved = resolve_tex_ref(ref, current, names)
            if resolved not in visited:
                pending.append(resolved)
    return visited, refs


def resolve_media_refs(refs: set[str], names: set[str]) -> tuple[set[str], list[str]]:
    resolved: set[str] = set()
    missing: list[str] = []
    by_basename: dict[str, list[str]] = {}
    for name in names:
        by_basename.setdefault(PurePosixPath(name).name, []).append(name)
    for ref in refs:
        normalized = PurePosixPath(ref).as_posix().lstrip("./")
        candidates = [normalized]
        if not PurePosixPath(normalized).suffix:
            candidates.extend(normalized + suffix for suffix in sorted(RASTER_SUFFIXES))
        direct = [candidate for candidate in candidates if candidate in names]
        if len(direct) == 1:
            resolved.add(direct[0])
            continue
        basename_matches = sorted({
            name
            for candidate in candidates
            for name in by_basename.get(PurePosixPath(candidate).name, [])
        })
        if len(basename_matches) == 1:
            resolved.add(basename_matches[0])
        else:
            missing.append(ref)
    return resolved, sorted(missing)


def independent_delivery_asset_scan(zip_path: Path, template_contract_path: Path | None = None) -> dict[str, Any]:
    immutable_template_media: dict[str, str] = {}
    if template_contract_path is not None:
        contract = read_json(template_contract_path)
        immutable_template_media = {
            item["path"]: item["sha256"]
            for item in contract.get("immutable_files", [])
            if PurePosixPath(item.get("path", "")).suffix.lower() in RASTER_SUFFIXES
        }
    with zipfile.ZipFile(zip_path) as archive:
        infos = [item for item in archive.infolist() if not item.is_dir()]
        names = {item.filename for item in infos}
        if "main.tex" not in names:
            raise ValueError("delivery ZIP lacks root main.tex")
        tex_files, refs = tex_closure(archive, names)
        resolved, missing = resolve_media_refs(refs, names)
        project_media = sorted(
            name for name in names
            if (name.startswith("images/") or name.startswith("figure/") or name.startswith("transport/"))
            and PurePosixPath(name).suffix.lower() in RASTER_SUFFIXES | {".pdf"}
        )
        forbidden_pdf = sorted(
            name for name in project_media
            if PurePosixPath(name).suffix.lower() == ".pdf"
            and (name.startswith("transport/") or name in resolved)
        )
        preserved_unreferenced_template_media = sorted(
            name for name in set(project_media) - resolved
            if name in immutable_template_media
            and hashlib.sha256(archive.read(name)).hexdigest() == immutable_template_media[name]
        )
        unreferenced = sorted(set(project_media) - resolved - set(preserved_unreferenced_template_media))
        oversized_raster = sorted(
            name for name in project_media
            if PurePosixPath(name).suffix.lower() in RASTER_SUFFIXES
            and archive.getinfo(name).file_size >= MAX_RASTER_IMAGE_BYTES_EXCLUSIVE
        )
    checks = {
        "file_entities_strictly_under_2000": len(infos) < MAX_FILE_ENTITIES_EXCLUSIVE,
        "all_tex_media_references_resolve": not missing,
        "no_unreferenced_project_media": not unreferenced,
        "native_raster_image_representation_preserved": not forbidden_pdf,
        "each_raster_image_strictly_under_1mb": not oversized_raster,
        "unreferenced_template_media_matches_frozen_bytes": all(
            name in preserved_unreferenced_template_media
            for name in set(project_media) - resolved
            if name in immutable_template_media
        ),
    }
    if not all(checks.values()):
        raise ValueError(
            "independent delivery asset scan failed: "
            + json.dumps({
                "checks": checks,
                "file_entities": len(infos),
                "missing": missing[:8],
                "unreferenced": unreferenced[:8],
                "forbidden_pdf": forbidden_pdf[:8],
                "oversized_raster": oversized_raster[:8],
            }, ensure_ascii=False)
        )
    return {
        "zip_sha256": sha256_file(zip_path),
        "file_entities": len(infos),
        "resolved_media_refs": len(resolved),
        "project_media_files": len(project_media),
        "scanned_tex_files": tex_files,
        "oversized_raster_images": oversized_raster,
        "preserved_unreferenced_template_media": preserved_unreferenced_template_media,
        "checks": checks,
    }


def validate_delivery_asset_report(
    zip_path: Path,
    report_path: Path,
    template_contract_path: Path | None = None,
) -> dict[str, Any]:
    report = read_json(report_path)
    measured = independent_delivery_asset_scan(zip_path, template_contract_path)
    if report.get("schema_version") != "spec05-delivery-asset-report/1.2":
        raise ValueError("unsupported delivery asset report schema")
    if report.get("spec_status") != "passed" or report.get("failure_codes"):
        raise ValueError("producer delivery asset report did not pass")
    declared = report.get("delivery_zip", {})
    if declared.get("sha256") != measured["zip_sha256"] or declared.get("file_entities") != measured["file_entities"]:
        raise ValueError("producer delivery asset report differs from independent ZIP measurement")
    constraints = report.get("constraints", {})
    if constraints.get("file_entities") != {"operator": "strictly_less_than", "max_exclusive": MAX_FILE_ENTITIES_EXCLUSIVE}:
        raise ValueError("producer file entity constraint is missing or was relaxed")
    if constraints.get("image_output_formats") != sorted(RASTER_SUFFIXES) or constraints.get("image_to_pdf_transport") != "forbidden":
        raise ValueError("producer native image representation constraint is missing or was relaxed")
    if constraints.get("raster_image_bytes") != {"operator": "strictly_less_than", "max_exclusive": MAX_RASTER_IMAGE_BYTES_EXCLUSIVE}:
        raise ValueError("producer raster image byte constraint is missing or was relaxed")
    required_checks = {
        "file_entities_strictly_under_2000",
        "all_tex_media_references_resolve",
        "no_unreferenced_generated_media",
        "no_unreferenced_project_media",
        "native_raster_image_representation_preserved",
        "each_raster_image_strictly_under_1mb",
        "unreferenced_template_media_matches_frozen_bytes",
    }
    checks = report.get("checks", {})
    if not required_checks.issubset(checks) or not all(checks[name] is True for name in required_checks):
        raise ValueError("producer delivery asset checks are incomplete or failed")
    if report.get("missing_media_refs") or report.get("unreferenced_generated_media") or report.get("unreferenced_project_media") or report.get("forbidden_pdf_media") or report.get("oversized_raster_images"):
        raise ValueError("producer delivery asset report contains unresolved media")
    if report.get("preserved_unreferenced_template_media") != measured["preserved_unreferenced_template_media"]:
        raise ValueError("producer preserved-template-media inventory differs from independent scan")
    return measured


def independent_delivery_stem(title: str, volume_label: str | None = None) -> str:
    if not isinstance(title, str) or not title.strip():
        raise ValueError("frozen title must be non-empty")
    parts = [title]
    if volume_label is not None:
        if not isinstance(volume_label, str) or not volume_label.strip():
            raise ValueError("frozen volume label must be non-empty when present")
        parts.append(volume_label)
    value = unicodedata.normalize("NFC", " - ".join(parts))
    value = "".join("_" if ord(ch) < 32 or ch in '<>:"/\\|?*' else ch for ch in value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value or value in {".", ".."}:
        raise ValueError("unsafe delivery stem")
    if value.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        value = "_" + value
    max_bytes = MAX_BASENAME_UTF8_BYTES - len(".zip")
    encoded = value.encode("utf-8")
    if len(encoded) > max_bytes:
        suffix = "-" + hashlib.sha256(encoded).hexdigest()[:12]
        prefix = encoded[:max_bytes - len(suffix)]
        while True:
            try:
                value = prefix.decode("utf-8").rstrip(" .") + suffix
                break
            except UnicodeDecodeError:
                prefix = prefix[:-1]
    return value


def independent_overleaf_transport_scan(zip_path: Path, rendered_body_path: Path) -> dict[str, Any]:
    rendered = rendered_body_path.read_bytes()
    with zipfile.ZipFile(zip_path) as archive:
        infos = [item for item in archive.infolist() if not item.is_dir()]
        names = [item.filename for item in infos]
        if names.count("main.tex") != 1 or names.count(GENERATED_BODY_PATH) != 1:
            raise ValueError("delivery must contain exact root main.tex and generated body payload")
        main = archive.read("main.tex").decode("utf-8")
        body = archive.read(GENERATED_BODY_PATH)
        body_text = body.decode("utf-8")
        loader_lines = [line.strip() for line in body_text.splitlines() if line.strip()]
        part_names: list[str] = []
        controlled_loader = bool(loader_lines)
        for line in loader_lines:
            match = LOADER_LINE_RE.fullmatch(line)
            if match is None:
                controlled_loader = False
                part_names = []
                break
            part_names.append(match.group(1))
        previous_unit = previous_part = 0
        for path in part_names:
            match = GENERATED_PART_RE.fullmatch(path)
            if match is None:
                controlled_loader = False
                break
            unit, part = map(int, match.groups())
            if unit == previous_unit:
                controlled_loader = controlled_loader and part == previous_part + 1
            elif unit == previous_unit + 1:
                controlled_loader = controlled_loader and part == 1
            else:
                controlled_loader = False
            previous_unit, previous_part = unit, part
        transport_mode = "semantic_unit_payload" if controlled_loader else "invalid_or_legacy_payload"
        part_set = set(part_names)
        missing_parts = [name for name in part_names if name not in names]
        payloads = [archive.read(name) for name in part_names if name in names]
        reconstructed = b"".join(payloads) if transport_mode == "semantic_unit_payload" else body
        unexpected_tex = sorted(
            name for name in names
            if PurePosixPath(name).suffix.lower() == ".tex"
            and name not in {"main.tex", GENERATED_BODY_PATH}
            and name not in part_set
        )
        behavior_pattern = re.compile(
            r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand|def|gdef|xdef|AtBeginDocument|input|include)\b"
        )
        definition_pattern = re.compile(
            r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand|def|gdef|xdef|AtBeginDocument)\b"
        )
        controlled_transport = (
            transport_mode == "semantic_unit_payload"
            and not missing_parts and definition_pattern.search(body_text) is None
            and all(behavior_pattern.search(payload.decode("utf-8")) is None for payload in payloads)
        )
        tex_members = sorted(name for name in names if PurePosixPath(name).suffix.lower() == ".tex")
        oversized_tex = [name for name in tex_members if archive.getinfo(name).file_size >= MAX_BODY_PART_BYTES_EXCLUSIVE]
        editable_members = sorted(name for name in names if PurePosixPath(name).suffix.lower() in EDITABLE_TEXT_EXTENSIONS)
        editable_text_bytes = sum(archive.getinfo(name).file_size for name in editable_members)
        checks = {
            "root_main_tex_present_once": names.count("main.tex") == 1,
            "generated_body_present_once": names.count(GENERATED_BODY_PATH) == 1,
            "root_main_uses_exact_generated_body_input_once": main.count(GENERATED_BODY_INPUT) == 1,
            "generated_body_reconstructs_rendered_body": reconstructed == rendered,
            "generated_body_transport_is_controlled": controlled_transport,
            "no_additional_tex_payloads": not unexpected_tex,
            "each_body_transport_tex_strictly_under_900k": not oversized_tex,
            "regular_file_modes": all(((item.external_attr >> 16) & 0o170000) != 0o120000 for item in infos),
        }
    if not all(checks.values()):
        raise ValueError(f"independent Overleaf transport scan failed: {checks}")
    return {
        "zip_sha256": sha256_file(zip_path), "rendered_body_sha256": sha256_file(rendered_body_path),
        "transport_mode": transport_mode, "part_names": part_names, "editable_text_bytes": editable_text_bytes,
        "checks": checks,
    }


def validate_overleaf_compatibility_report(zip_path: Path, rendered_body_path: Path, report_path: Path) -> dict[str, Any]:
    measured = independent_overleaf_transport_scan(zip_path, rendered_body_path)
    report = read_json(report_path)
    if report.get("schema_version") != "spec05-overleaf-delivery-compatibility-report/3.0":
        raise ValueError("unsupported Overleaf compatibility report schema")
    if report.get("spec_status") != "passed" or report.get("gate") != {"gate_id": "CP-H25", "status": "passed"}:
        raise ValueError("producer Overleaf compatibility report did not pass CP-H25")
    if report.get("capacity_gate") != {"gate_id": "CP-H27", "status": "passed"}:
        raise ValueError("producer Overleaf compatibility report did not pass CP-H27")
    if report.get("delivery_zip", {}).get("sha256") != measured["zip_sha256"]:
        raise ValueError("producer Overleaf compatibility report ZIP binding drift")
    if report.get("rendered_body", {}).get("sha256") != measured["rendered_body_sha256"]:
        raise ValueError("producer Overleaf compatibility report body binding drift")
    if report.get("checks") != measured["checks"] or report.get("failure_code") is not None:
        raise ValueError("producer Overleaf compatibility report differs from independent scan")
    declared_parts = [item.get("path") for item in report.get("generated_body", {}).get("parts", [])]
    if declared_parts != measured["part_names"]:
        raise ValueError("producer body-shard inventory differs from independent scan")
    if report.get("capacity", {}).get("editable_text_bytes") != measured["editable_text_bytes"]:
        raise ValueError("producer editable-text byte count differs from independent scan")
    if report.get("capacity", {}).get("max_body_transport_tex_bytes_exclusive") != MAX_BODY_PART_BYTES_EXCLUSIVE:
        raise ValueError("producer body-part byte limit is missing or was relaxed")
    return measured


def validate_delivery_naming_report(zip_path: Path, pdf_path: Path, metadata_path: Path, report_path: Path) -> dict[str, Any]:
    metadata = read_json(metadata_path)
    title = metadata.get("values", {}).get("title")
    volume_label = (metadata.get("volume_binding") or {}).get("label")
    stem = independent_delivery_stem(title, volume_label)
    expected = {"stem": stem, "zip": f"{stem}.zip", "pdf": f"{stem}.pdf"}
    if zip_path.name != expected["zip"] or pdf_path.name != expected["pdf"]:
        raise ValueError("delivery artifact names differ from frozen cover identity")
    report = read_json(report_path)
    if report.get("schema_version") != "spec05-delivery-naming-report/1.0":
        raise ValueError("unsupported delivery naming report schema")
    if report.get("spec_status") != "passed" or report.get("gate") != {"gate_id": "CP-H26", "status": "passed"}:
        raise ValueError("producer delivery naming report did not pass CP-H26")
    if report.get("frozen_identity") != {"title": title, "volume_label": volume_label} or report.get("expected") != expected:
        raise ValueError("producer naming report frozen identity drift")
    if report.get("actual") != {"zip": zip_path.name, "pdf": pdf_path.name} or not all(report.get("checks", {}).values()):
        raise ValueError("producer naming report differs from independent evaluation")
    return {"title": title, "volume_label": volume_label, "expected": expected}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve(run: Path, item: dict[str, Any]) -> Path:
    path = (run / item["path"]).resolve()
    if not path.is_file() or sha256_file(path) != item.get("sha256"):
        raise ValueError(f"missing or drifted Spec 05 artifact: {item.get('path')}")
    return path


def strip_tex_comments(text: str) -> str:
    cleaned: list[str] = []
    for line in text.splitlines(keepends=True):
        cut = len(line)
        for index, char in enumerate(line):
            if char != "%":
                continue
            cursor = index - 1
            backslashes = 0
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


def independent_template_local_api_scan(capability_path: Path, body_path: Path) -> dict[str, Any]:
    capability = read_json(capability_path)
    constructs = capability.get("constructs")
    if not isinstance(constructs, dict):
        constructs = capability if {"custom_commands", "custom_environments"}.issubset(capability) else None
    if not isinstance(constructs, dict):
        raise ValueError("capability manifest lacks constructs inventory")
    commands = constructs.get("custom_commands")
    environments = constructs.get("custom_environments")
    if not isinstance(commands, list) or not isinstance(environments, list):
        raise ValueError("capability manifest lacks explicit template-local API inventories")
    inventory = [*commands, *environments]
    if any(not isinstance(name, str) or not name for name in inventory):
        raise ValueError("template-local API inventory contains an invalid name")
    if len(commands) != len(set(commands)) or len(environments) != len(set(environments)):
        raise ValueError("template-local API inventory contains duplicate names")
    scanned = strip_tex_comments(body_path.read_text(encoding="utf-8"))
    violations: list[dict[str, Any]] = []
    for pattern in COMMAND_DEFINITION_PATTERNS:
        for match in pattern.finditer(scanned):
            name = match.group(1)
            if name in commands:
                violations.append({"kind": "template_local_command_definition", "name": name, "line": line_number(scanned, match.start())})
    for match in ENVIRONMENT_DEFINITION_PATTERN.finditer(scanned):
        name = match.group(1)
        if name in environments:
            violations.append({"kind": "template_local_environment_definition", "name": name, "line": line_number(scanned, match.start())})
    for name in sorted(commands):
        for match in re.finditer(rf"\\{re.escape(name)}(?![A-Za-z@])", scanned):
            violations.append({"kind": "template_local_command_call", "name": name, "line": line_number(scanned, match.start())})
    for name in sorted(environments):
        for match in re.finditer(rf"\\(begin|end)\s*\{{\s*{re.escape(name)}\s*\}}", scanned):
            violations.append({"kind": "template_local_environment_call", "name": name, "line": line_number(scanned, match.start())})
    violations.sort(key=lambda item: (item["line"] or 0, item["kind"], item["name"]))
    return {
        "inventory": {
            "template_local_custom_commands": sorted(commands),
            "template_local_custom_environments": sorted(environments),
        },
        "violations": violations,
    }


class Gate:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(self, check_id: str, fn: Callable[[], Any]) -> None:
        try:
            self.checks.append({"check_id": check_id, "status": "passed", "evidence": fn()})
        except Exception as exc:
            self.checks.append({"check_id": check_id, "status": "failed", "detail": str(exc)})

    @property
    def passed(self) -> bool:
        return all(item["status"] == "passed" for item in self.checks)


def preflight_parent(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Emit evidence for Spec 05 parent eligibility without creating a build."""
    stage_gate = load_module(Path(__file__).with_name("stage_promotion_gate.py"), "spec05_parent_preflight_gate")
    registry_path = args.promotion_registry.resolve()
    promotion_path = args.parent_promotion.resolve()
    registry = read_json(registry_path)
    promotion = read_json(promotion_path)
    gate = Gate()

    def active_selection() -> dict[str, Any]:
        if registry.get("schema_version") != "promotion-registry/1.0":
            raise ValueError("unsupported promotion registry")
        computed = stage_gate.canonical_hash({key: value for key, value in registry.items() if key not in {"generated_at", "payload_hash"}})
        if computed != registry.get("payload_hash"):
            raise ValueError("promotion registry payload hash mismatch")
        active = registry.get("active_promotions", {}).get(args.parent_lineage_key)
        if not active:
            raise ValueError("registry has no active selection for the supplied lineage")
        if Path(active["manifest_path"]).resolve() != promotion_path or active["manifest_sha256"] != sha256_file(promotion_path):
            raise ValueError("supplied promotion is not the registry active selection")
        if active["promotion_id"] != promotion.get("promotion_id"):
            raise ValueError("registry and promotion identities differ")
        return {"promotion_id": active["promotion_id"], "lineage_key": args.parent_lineage_key}

    def full_formal_spec04d() -> dict[str, Any]:
        if promotion.get("disposition") != "promoted" or promotion.get("promotion_class") != "formal_native":
            raise ValueError("SPEC05_PARENT_NOT_FORMAL_NATIVE")
        if promotion.get("stage_kind") != "spec04d_render_plan_contract":
            raise ValueError(f"SPEC05_PARENT_NOT_FULL_SPEC04D: got {promotion.get('stage_kind')}")
        verified = stage_gate.verify_promotion_manifest(
            promotion_path, "spec04d_render_plan_contract", "formal_native", capability_verification="frozen"
        )
        stage = read_json(Path(verified["stage_manifest"]["path"]))
        if stage.get("full_spec04_status") != "passed" or stage.get("producer_mode") != "formal_native":
            raise ValueError("SPEC05_PARENT_NOT_FULL_SPEC04D: full Spec 04 status is not passed")
        return {"full_spec04_status": "passed", "producer_mode": "formal_native"}

    gate.check("S5-ELIG-H01-active-registry-selection", active_selection)
    gate.check("S5-ELIG-H02-full-formal-spec04d", full_formal_spec04d)
    eligible = gate.passed
    report = {
        "schema_version": "spec05-parent-eligibility/1.0", "evaluated_at": stage_gate.now(),
        "eligible": eligible, "promotion_registry": {"path": str(registry_path), "sha256": sha256_file(registry_path)},
        "parent_promotion": {"path": str(promotion_path), "sha256": sha256_file(promotion_path)},
        "parent_lineage_key": args.parent_lineage_key, "checks": gate.checks,
        "failure_code": None if eligible else "SPEC05_PARENT_NOT_FULL_SPEC04D",
        "scope_limit": "Parent eligibility only; no Spec 05 build or compile claim.",
    }
    stage_gate.write_json(args.output.resolve(), report)
    return report, 0 if eligible else 4


def evaluate_delivery_set_promotion(
    args: argparse.Namespace, run: Path, output: Path, stage: dict[str, Any], stage_path: Path,
    stage_gate: Any, execution: Any,
) -> tuple[dict[str, Any], int]:
    skill_root = Path(__file__).resolve().parents[1]
    cleanlatex_root = cleanlatex_skill_root()
    evaluator_path = (
        args.evaluator_capability_output.resolve()
        if getattr(args, "evaluator_capability_output", None)
        else output.with_suffix(".evaluator-capability.json")
    )
    evaluator = execution.build_manifest(
        manifest_id=f"{args.promotion_id}-delivery-set-evaluator-capability", skill_root=skill_root,
        entrypoints=[
            ("promotion_router", Path(__file__).with_name("stage_promotion_gate.py").resolve()),
            ("spec05_delivery_set_evaluator", Path(__file__).resolve()),
            ("execution_capability_core", Path(__file__).with_name("execution_capability.py").resolve()),
        ],
        resources=[
            ("delivery_set_stage_schema", cleanlatex_root / "schemas/spec05-native-delivery-set-stage-manifest.schema.json"),
            ("delivery_set_schema", cleanlatex_root / "schemas/spec05-delivery-set-manifest.schema.json"),
            ("overleaf_delivery_compatibility_schema", cleanlatex_root / "schemas/spec05-overleaf-delivery-compatibility-report.schema.json"),
            ("delivery_asset_report_schema", cleanlatex_root / "schemas/spec05-delivery-asset-report.schema.json"),
            ("promotion_schema", skill_root / "schemas/stage-promotion-manifest.schema.json"),
        ],
        invocation=[
            "stage_promotion_gate.py", "evaluate-spec05-build", "--run-dir", str(run),
            "--promotion-id", args.promotion_id, "--lineage-key", args.lineage_key,
            "--evaluator-capability-output", str(evaluator_path), "--output", str(output),
        ], producer=VERSION,
    )
    stage_gate.write_json(evaluator_path, evaluator)
    gate = Gate()
    artifacts: dict[str, dict[str, Any]] = {}
    context: dict[str, Any] = {}

    def stage_shape() -> dict[str, Any]:
        if stage.get("schema_version") != "spec05-native-delivery-set-stage-manifest/1.1" or stage.get("stage_kind") != "spec05_native_delivery_set":
            raise ValueError("unsupported Spec 05 delivery-set stage")
        if stage.get("status") != "passed" or stage.get("spec_status") != "passed" or stage.get("promotion_class") != "formal_native":
            raise ValueError("delivery-set stage is not a formal-native pass")
        if len(stage.get("volumes", [])) != 2:
            raise ValueError("two-volume stage must bind exactly two volume records")
        return {"run_id": stage["run_id"], "volumes": 2}

    def root_hashes() -> dict[str, Any]:
        for name in ("execution_capability_E", "decision_index_D", "delivery_set_manifest_M", "volume_partition_plan"):
            path = resolve(run, stage[name])
            artifacts[name] = {"path": str(path), "sha256": sha256_file(path)}
        artifacts["producer_execution_capability"] = dict(artifacts["execution_capability_E"])
        context["delivery_set"] = read_json(Path(artifacts["delivery_set_manifest_M"]["path"]))
        context["partition"] = read_json(Path(artifacts["volume_partition_plan"]["path"]))
        return {"root_artifacts": len(artifacts)}

    def active_parent() -> dict[str, Any]:
        parent = stage["parent_spec04d"]
        registry, promotion = Path(parent["registry_path"]), Path(parent["promotion_path"])
        if sha256_file(registry) != parent["registry_sha256"] or sha256_file(promotion) != parent["promotion_sha256"]:
            raise ValueError("Spec 04-D parent registry/promotion drift")
        selected = stage_gate.verify_registry_selection(
            registry, parent["lineage_key"], promotion,
            "spec04d_render_plan_contract", "formal_native", capability_verification="frozen",
        )
        if selected["promotion"]["promotion_id"] != parent["promotion_id"]:
            raise ValueError("Spec 04-D active promotion identity drift")
        promoted_partition = selected["promotion"]["promoted_artifacts"].get("volume_partition_plan")
        if not promoted_partition or sha256_file(Path(promoted_partition["path"])) != artifacts["volume_partition_plan"]["sha256"]:
            raise ValueError("delivery set does not consume the active frozen partition")
        return {"promotion_id": parent["promotion_id"]}

    def independent_per_volume() -> dict[str, Any]:
        delivery_set = context["delivery_set"]
        partition = context["partition"]
        if delivery_set.get("volume_count") != 2 or delivery_set.get("mode") != "two_volume":
            raise ValueError("delivery set does not declare exactly two volumes")
        if delivery_set.get("volume_partition_plan", {}).get("sha256") != artifacts["volume_partition_plan"]["sha256"]:
            raise ValueError("delivery set partition hash drift")
        all_nodes: list[str] = []
        all_sources: list[str] = []
        measured = []
        for produced, frozen in zip(delivery_set["volumes"], partition["volumes"]):
            if produced.get("volume_id") != frozen.get("volume_id") or produced.get("render_node_ids") != frozen.get("render_node_ids") or produced.get("source_block_ids") != frozen.get("source_block_ids"):
                raise ValueError("producer volume membership differs from Spec 04-D")
            zip_path = resolve(run, produced["delivery_zip"])
            size_path = resolve(run, produced["delivery_size_report"])
            asset_path = resolve(run, produced["delivery_asset_report"])
            compatibility_path = resolve(run, produced["overleaf_delivery_compatibility_report"])
            naming_path = resolve(run, produced["delivery_naming_report"])
            metadata_path = resolve(run, produced["metadata_config"])
            child_stage_path = resolve(run, produced["child_stage_manifest"])
            child_stage = read_json(child_stage_path)
            if child_stage.get("stage_kind") != "spec05_native_execution" or child_stage.get("volume_id") != frozen["volume_id"]:
                raise ValueError("child stage volume identity drift")
            if any(child_stage.get("hard_gates", {}).get(f"CP-H{index:02d}") is not True for index in range(1, 29)):
                raise ValueError("child stage has a failed Spec 05 hard gate")
            build_path = resolve(run, produced["child_build_manifest"])
            build = read_json(build_path)
            child_run = build_path.parent.parent
            child_contract_path = resolve(child_run, build["artifacts"]["template_contract"])
            child_contract = read_json(child_contract_path)
            size_scan = validate_delivery_size_report(zip_path, size_path)
            asset_scan = validate_delivery_asset_report(zip_path, asset_path, child_contract_path)
            child_transport = child_contract.get("generated_body_transport", {})
            if child_transport.get("project_path") != GENERATED_BODY_PATH or child_transport.get("input_literal") != GENERATED_BODY_INPUT:
                raise ValueError("child template contract does not freeze the approved generated-body transport")
            render_execution_path = resolve(child_run, build["artifacts"]["render_execution"])
            rendered_body_path = resolve(child_run, build["artifacts"]["rendered_body"])
            validate_overleaf_compatibility_report(zip_path, rendered_body_path, compatibility_path)
            validate_delivery_naming_report(zip_path, resolve(run, produced["final_pdf"]), metadata_path, naming_path)
            emissions = read_json(render_execution_path)["emissions"]
            emitted_nodes = [item["render_node_id"] for item in emissions]
            emitted_sources = [block_id for item in emissions for block_id in item.get("source_block_ids", [])]
            if emitted_nodes != frozen["render_node_ids"] or emitted_sources != frozen["source_block_ids"]:
                raise ValueError("independent render emission scan differs from frozen membership")
            render_pack_path = resolve(run, produced["render_pack"])
            final_pdf_path = resolve(run, produced["final_pdf"])
            render_pack = read_json(render_pack_path)
            if render_pack.get("status") != "complete" or render_pack.get("final_pdf", {}).get("sha256") != sha256_file(final_pdf_path):
                raise ValueError("child render pack/final PDF binding drift")
            all_nodes.extend(emitted_nodes)
            all_sources.extend(emitted_sources)
            measured.append({"volume_id": frozen["volume_id"], "zip_bytes": size_scan["size_bytes"], "file_entities": asset_scan["file_entities"], "pages": render_pack["page_count"]})
            artifacts[f"{frozen['volume_id']}_delivery_zip"] = {"path": str(zip_path), "sha256": sha256_file(zip_path)}
            artifacts[f"{frozen['volume_id']}_final_pdf"] = {"path": str(final_pdf_path), "sha256": sha256_file(final_pdf_path)}
            artifacts[f"{frozen['volume_id']}_render_pack"] = {"path": str(render_pack_path), "sha256": sha256_file(render_pack_path)}
        expected_nodes = [value for item in partition["volumes"] for value in item["render_node_ids"]]
        expected_sources = [value for item in partition["volumes"] for value in item["source_block_ids"]]
        if all_nodes != expected_nodes or len(all_nodes) != len(set(all_nodes)):
            raise ValueError("cross-volume render-node coverage is not exact")
        if all_sources != expected_sources or len(all_sources) != len(set(all_sources)):
            raise ValueError("cross-volume source-atom coverage is not exact")
        if delivery_set.get("cross_volume_coverage", {}).get("spec05_repartitioned") is not False:
            raise ValueError("delivery set reports Spec 05 repartitioning")
        return {"volumes": measured, "render_nodes": len(all_nodes), "source_atoms": len(all_sources)}

    def decision_closure() -> dict[str, Any]:
        index = read_json(Path(artifacts["decision_index_D"]["path"]))
        unresolved = [item for item in index.get("decisions", []) if item.get("status") in {"open", "stale", "invalidated"}]
        if index.get("spec_status") != "passed" or unresolved or index.get("summary", {}).get("open") != 0:
            raise ValueError("delivery-set decision index is unresolved")
        if index.get("acyclic_commit_rule") != "producer_execution_capability_E_then_volume_builds_B_then_decision_index_D_then_delivery_set_M":
            raise ValueError("delivery-set commit order is not E-B-D-M")
        return {"decisions": len(index.get("decisions", [])), "open": 0}

    def deterministic_aggregate() -> dict[str, Any]:
        delivery_set = context["delivery_set"]
        payload = {key: value for key, value in delivery_set.items() if key not in {"generated_at", "deterministic_payload_hash"}}
        if stage_gate.canonical_hash(payload) != delivery_set.get("deterministic_payload_hash"):
            raise ValueError("delivery-set payload hash mismatch")
        if delivery_set.get("hard_gates") != {"CP-H21": True, "CP-H22": True, "CP-H23": True, "CP-H24": True, "CP-H25": True, "CP-H26": True, "CP-H27": True, "CP-H28": True}:
            raise ValueError("delivery-set aggregate hard gates are incomplete")
        return delivery_set["cross_volume_coverage"]

    def capabilities() -> dict[str, Any]:
        producer = execution.validate_manifest(Path(artifacts["execution_capability_E"]["path"]))
        evaluator_result = execution.validate_manifest(evaluator_path)
        artifacts["evaluator_execution_capability"] = {"path": str(evaluator_path), "sha256": sha256_file(evaluator_path)}
        return {"producer": producer, "evaluator": evaluator_result}

    def immutable_run() -> dict[str, Any]:
        path = run / "manifests/run_manifest.json"
        manifest = read_json(path)
        if manifest.get("stage_kind") != "spec05_native_delivery_set" or manifest.get("immutable_after_publication") is not True:
            raise ValueError("delivery-set run is not immutable")
        for item in manifest.get("files", []):
            target = run / item["path"]
            if not target.is_file() or sha256_file(target) != item["sha256"]:
                raise ValueError(f"immutable delivery-set file drift: {item.get('path')}")
        artifacts["run_manifest"] = {"path": str(path), "sha256": sha256_file(path)}
        return {"files": len(manifest.get("files", []))}

    gate.check("S5-DS-PG-H01-formal-native-delivery-set-shape", stage_shape)
    gate.check("S5-DS-PG-H02-root-artifact-hashes", root_hashes)
    gate.check("S5-DS-PG-H03-active-spec04d-partition-parent", active_parent)
    gate.check("S5-DS-PG-H04-independent-per-volume-gates", independent_per_volume)
    gate.check("S5-DS-PG-H05-cross-volume-exact-coverage", deterministic_aggregate)
    gate.check("S5-DS-PG-H06-decision-closure-and-acyclicity", decision_closure)
    gate.check("S5-DS-PG-H07-live-producer-and-evaluator-capabilities", capabilities)
    gate.check("S5-DS-PG-H08-immutable-run-files", immutable_run)
    disposition = "promoted" if gate.passed else "rejected"
    manifest = {
        "schema_version": "stage-promotion-manifest/1.1", "promotion_id": args.promotion_id,
        "lineage_key": args.lineage_key, "evaluated_at": stage_gate.now(), "evaluator": VERSION,
        "stage_kind": "spec05_native_delivery_set", "run_dir": str(run),
        "stage_manifest": {"path": str(stage_path), "sha256": sha256_file(stage_path)},
        "disposition": disposition, "promotion_class": "formal_native",
        "producer_execution_provenance": "live_verified" if gate.passed else "unverified",
        "evaluator_capability": {"path": str(evaluator_path), "sha256": sha256_file(evaluator_path), "payload_hash": evaluator["payload_hash"]},
        "checks": gate.checks,
        "summary": {"checks": len(gate.checks), "passed": sum(item["status"] == "passed" for item in gate.checks), "failed": sum(item["status"] == "failed" for item in gate.checks)},
        "promoted_artifacts": artifacts if disposition == "promoted" else {},
        "consumer_rule": "Render coverage and Spec 06 must consume the exact delivery set and every promoted volume; no volume may be sampled or omitted.",
        "scope_limit": "Spec 05 two-volume compile_pass only; render coverage, Spec 06, and product acceptance are not evaluated.",
    }
    stage_gate.write_json(output, manifest)
    return manifest, 0 if disposition == "promoted" else 4


def evaluate_promotion(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    run = args.run_dir.resolve()
    output = args.output.resolve()
    stage_gate = load_module(Path(__file__).with_name("stage_promotion_gate.py"), "spec05_parent_gate")
    execution = load_module(Path(__file__).with_name("execution_capability.py"), "spec05_execution_capability_gate")
    stage_path = run / "manifests/spec05_native_stage_manifest.json"
    if not stage_path.is_file():
        raise FileNotFoundError(stage_path)
    stage = read_json(stage_path)
    if stage.get("stage_kind") == "spec05_native_delivery_set":
        return evaluate_delivery_set_promotion(args, run, output, stage, stage_path, stage_gate, execution)
    skill_root = Path(__file__).resolve().parents[1]
    cleanlatex_root = cleanlatex_skill_root()
    evaluator_path = (
        args.evaluator_capability_output.resolve()
        if getattr(args, "evaluator_capability_output", None)
        else output.with_suffix(".evaluator-capability.json")
    )
    evaluator = execution.build_manifest(
        manifest_id=f"{args.promotion_id}-evaluator-capability",
        skill_root=skill_root,
        entrypoints=[
            ("promotion_router", Path(__file__).with_name("stage_promotion_gate.py").resolve()),
            ("spec05_evaluator", Path(__file__).resolve()),
            ("execution_capability_core", Path(__file__).with_name("execution_capability.py").resolve()),
        ],
        resources=[
            ("stage_schema", cleanlatex_root / "schemas/spec05-native-stage-manifest.schema.json"),
            ("promotion_schema", skill_root / "schemas/stage-promotion-manifest.schema.json"),
            ("registry_schema", skill_root / "schemas/promotion-registry.schema.json"),
            ("overleaf_delivery_compatibility_schema", cleanlatex_root / "schemas/spec05-overleaf-delivery-compatibility-report.schema.json"),
            ("delivery_asset_report_schema", cleanlatex_root / "schemas/spec05-delivery-asset-report.schema.json"),
        ],
        invocation=[
            "stage_promotion_gate.py", "evaluate-spec05-build", "--run-dir", str(run),
            "--promotion-id", args.promotion_id, "--lineage-key", args.lineage_key,
            "--evaluator-capability-output", str(evaluator_path), "--output", str(output),
        ],
        producer=VERSION,
    )
    stage_gate.write_json(evaluator_path, evaluator)
    gate = Gate()
    context: dict[str, Any] = {}
    artifacts: dict[str, dict[str, Any]] = {}

    def stage_shape() -> dict[str, Any]:
        if stage.get("schema_version") != STAGE_SCHEMA or stage.get("stage_kind") != "spec05_native_execution":
            raise ValueError("unsupported Spec 05 native stage manifest")
        if stage.get("status") != "passed" or stage.get("spec_status") != "passed" or stage.get("promotion_class") != "formal_native":
            raise ValueError("Spec 05 run did not self-report a formal-native pass")
        required = {"execution_capability_E", "decision_index_D", "build_manifest_M", "final_pdf", "delivery_zip", "render_pack", "parent_spec04d", "template_contract", "presentation_config", "presentation_hard_gates", "template_local_api_usage", "delivery_size_report", "delivery_asset_report", "overleaf_delivery_compatibility_report", "delivery_naming_report"}
        missing = sorted(required - set(stage))
        if missing:
            raise ValueError(f"stage manifest lacks required fields: {missing}")
        return {"run_id": stage["run_id"], "promotion_class": stage["promotion_class"]}

    def artifact_hashes() -> dict[str, Any]:
        for name in ("execution_capability_E", "decision_index_D", "build_manifest_M", "final_pdf", "delivery_zip", "render_pack", "template_contract", "presentation_config", "template_local_api_usage", "delivery_size_report", "delivery_asset_report", "overleaf_delivery_compatibility_report", "delivery_naming_report"):
            path = resolve(run, stage[name])
            artifacts[name] = {"path": str(path), "sha256": sha256_file(path)}
        artifacts["producer_execution_capability"] = dict(artifacts["execution_capability_E"])
        build = read_json(Path(artifacts["build_manifest_M"]["path"]))
        for name, item in build.get("artifacts", {}).items():
            path = resolve(run, item)
            resolved = {"path": str(path), "sha256": sha256_file(path)}
            if name in artifacts and artifacts[name] != resolved:
                raise ValueError(f"stage and build manifest bind different {name} artifacts")
            artifacts.setdefault(name, resolved)
        required_build = {"template_capability_manifest", "rendered_body", "template_local_api_usage", "template_integrity", "compile_report", "delivery_size_report", "delivery_asset_report", "overleaf_delivery_compatibility_report", "delivery_naming_report", "metadata_config"}
        missing = sorted(required_build - set(artifacts))
        if missing:
            raise ValueError(f"build manifest lacks TP-H14 evidence: {missing}")
        context["build"] = build
        return {"artifacts": len(artifacts)}

    def active_parent() -> dict[str, Any]:
        parent = stage["parent_spec04d"]
        registry = Path(parent["registry_path"])
        promotion = Path(parent["promotion_path"])
        if sha256_file(registry) != parent["registry_sha256"] or sha256_file(promotion) != parent["promotion_sha256"]:
            raise ValueError("Spec 04-D registry or promotion binding drifted")
        selected = stage_gate.verify_registry_selection(
            registry, parent["lineage_key"], promotion,
            "spec04d_render_plan_contract", "formal_native", capability_verification="frozen",
        )
        if selected["promotion"]["promotion_id"] != parent["promotion_id"]:
            raise ValueError("active Spec 04-D promotion identity differs")
        return {"promotion_id": parent["promotion_id"], "lineage_key": parent["lineage_key"]}

    def template_gates() -> dict[str, Any]:
        report = read_json(Path(artifacts["template_integrity"]["path"]))
        hard = stage.get("hard_gates", {})
        failed = [f"TP-H{index:02d}" for index in range(1, 15) if hard.get(f"TP-H{index:02d}") is not True]
        if report.get("status") != "passed" or failed:
            raise ValueError(f"template gates failed: {failed}")
        checks = report.get("checks", {})
        if not checks or not all(checks.values()):
            raise ValueError("template integrity report contains a failed check")
        return {"hard_gates": 14, "integrity_checks": len(checks)}

    def template_local_api_usage() -> dict[str, Any]:
        capability_path = Path(artifacts["template_capability_manifest"]["path"])
        body_path = Path(artifacts["rendered_body"]["path"])
        report_path = Path(artifacts["template_local_api_usage"]["path"])
        report = read_json(report_path)
        independent = independent_template_local_api_scan(capability_path, body_path)
        expected_inputs = report.get("inputs", {})
        if expected_inputs.get("capability_manifest", {}).get("sha256") != sha256_file(capability_path):
            raise ValueError("producer TP-H14 report is not bound to the promoted capability manifest")
        if expected_inputs.get("rendered_body", {}).get("sha256") != sha256_file(body_path):
            raise ValueError("producer TP-H14 report is not bound to the promoted rendered body")
        if report.get("schema_version") != "spec05-template-local-api-usage/1.0":
            raise ValueError("unsupported producer TP-H14 report schema")
        if report.get("inventory") != independent["inventory"] or report.get("violations") != independent["violations"]:
            raise ValueError("producer TP-H14 report differs from independent evaluator rescan")
        if report.get("spec_status") != "passed" or report.get("gate") != {"gate_id": "TP-H14", "status": "passed"}:
            raise ValueError("producer TP-H14 report did not pass")
        if independent["violations"]:
            raise ValueError(f"template-local custom API usage found: {independent['violations'][:8]}")
        return {
            "capability_manifest_sha256": sha256_file(capability_path),
            "rendered_body_sha256": sha256_file(body_path),
            "producer_report_sha256": sha256_file(report_path),
            "inventory": independent["inventory"],
            "violations": 0,
        }

    def compile_gates() -> dict[str, Any]:
        report = read_json(Path(artifacts["compile_report"]["path"]))
        hard = report.get("hard_gates", {})
        failed = [f"CP-H{index:02d}" for index in range(1, 29) if hard.get(f"CP-H{index:02d}") is not True]
        if report.get("spec_status") != "passed" or report.get("exit_code") != 0 or failed:
            raise ValueError(f"compile gates failed: {failed}")
        if report.get("scope_limit") != "Spec 05 compile_pass and final_render_pack only; render coverage, Spec 06, and product acceptance are not evaluated.":
            raise ValueError("Spec 05 compile report claims an unapproved scope")
        return {"hard_gates": 27, "pages": report["pdf"]["pages"]}

    def delivery_zip_size_limit() -> dict[str, Any]:
        zip_path = Path(artifacts["delivery_zip"]["path"])
        report_path = Path(artifacts["delivery_size_report"]["path"])
        measured = validate_delivery_size_report(zip_path, report_path)
        compile_report = read_json(Path(artifacts["compile_report"]["path"]))
        compile_binding = compile_report.get("delivery_size_report")
        if not isinstance(compile_binding, dict):
            raise ValueError("compile report does not bind delivery_size_report.json")
        bound_path = resolve(run, compile_binding)
        if bound_path != report_path.resolve():
            raise ValueError("compile report binds a different delivery size report")
        return measured

    def delivery_asset_policy() -> dict[str, Any]:
        zip_path = Path(artifacts["delivery_zip"]["path"])
        report_path = Path(artifacts["delivery_asset_report"]["path"])
        measured = validate_delivery_asset_report(
            zip_path, report_path, Path(artifacts["template_contract"]["path"])
        )
        compile_report = read_json(Path(artifacts["compile_report"]["path"]))
        compile_binding = compile_report.get("delivery_asset_report")
        if not isinstance(compile_binding, dict):
            raise ValueError("compile report does not bind delivery_asset_report.json")
        bound_path = resolve(run, compile_binding)
        if bound_path != report_path.resolve():
            raise ValueError("compile report binds a different delivery asset report")
        return measured

    def overleaf_delivery_compatibility() -> dict[str, Any]:
        zip_path = Path(artifacts["delivery_zip"]["path"])
        body_path = Path(artifacts["rendered_body"]["path"])
        report_path = Path(artifacts["overleaf_delivery_compatibility_report"]["path"])
        measured = validate_overleaf_compatibility_report(zip_path, body_path, report_path)
        contract = read_json(Path(artifacts["template_contract"]["path"]))
        if contract.get("generated_body_transport") != {
            "mode": "single_hash_bound_tex_payload",
            "project_path": GENERATED_BODY_PATH,
            "input_literal": GENERATED_BODY_INPUT,
            "root_entry": "main.tex",
            "nested_input_include_forbidden": True,
            "tex_definitions_forbidden": True,
            "payload_must_equal_rendered_body": True,
        }:
            raise ValueError("template contract does not freeze the exact approved generated-body transport")
        compile_report = read_json(Path(artifacts["compile_report"]["path"]))
        bound = resolve(run, compile_report["overleaf_delivery_compatibility_report"])
        if bound != report_path.resolve():
            raise ValueError("compile report binds a different Overleaf compatibility report")
        return measured

    def delivery_naming() -> dict[str, Any]:
        zip_path = Path(artifacts["delivery_zip"]["path"])
        pdf_path = Path(artifacts["final_pdf"]["path"])
        metadata_path = Path(artifacts["metadata_config"]["path"])
        report_path = Path(artifacts["delivery_naming_report"]["path"])
        measured = validate_delivery_naming_report(zip_path, pdf_path, metadata_path, report_path)
        compile_report = read_json(Path(artifacts["compile_report"]["path"]))
        bound = resolve(run, compile_report["delivery_naming_report"])
        if bound != report_path.resolve():
            raise ValueError("compile report binds a different delivery naming report")
        return measured

    def decision_closure() -> dict[str, Any]:
        index = read_json(Path(artifacts["decision_index_D"]["path"]))
        unresolved = [item.get("decision_id") for item in index.get("decisions", []) if item.get("status") in {"open", "stale", "invalidated"}]
        if index.get("spec_status") != "passed" or unresolved or index.get("summary", {}).get("open") != 0:
            raise ValueError(f"Spec 05 decision index is unresolved: {unresolved[:8]}")
        return {"decisions": len(index.get("decisions", [])), "open": 0}

    def presentation_contract() -> dict[str, Any]:
        contract_path = Path(artifacts["template_contract"]["path"])
        config_path = Path(artifacts["presentation_config"]["path"])
        contract = read_json(contract_path)
        config = read_json(config_path)
        if contract.get("schema_version") != "template-contract/2.0" or contract.get("status") != "frozen":
            raise ValueError("formal-native Spec 05 requires template-contract/2.0")
        binding = contract.get("presentation_config", {})
        bound_ref = Path(binding.get("ref", ""))
        bound_path = bound_ref if bound_ref.is_absolute() else (contract_path.parent / bound_ref).resolve()
        if bound_path != config_path.resolve() or binding.get("sha256") != sha256_file(config_path):
            raise ValueError("template contract and presentation config binding differ")
        if config.get("schema_version") != "spec05-presentation-config/1.0" or config.get("status") != "approved":
            raise ValueError("presentation config is not approved")
        if config.get("template_zip_sha256") != contract.get("template_zip", {}).get("sha256"):
            raise ValueError("presentation config and template ZIP binding differ")
        assets = contract.get("selected_presentation", {}).get("assets")
        if not isinstance(assets, dict) or set(assets) != {"cover", "logo"}:
            raise ValueError("presentation contract does not bind exact cover and logo")
        hard = stage.get("presentation_hard_gates", {})
        if set(hard) != {"PR-H01-explicit-cover-logo", "PR-H02-closed-decisions", "PR-H03-frozen-assets-preserved", "PR-H04-materialized-assets-bound"} or not all(hard.values()):
            raise ValueError("presentation hard gates are incomplete")
        index = read_json(Path(artifacts["decision_index_D"]["path"]))
        presentation_decisions = [item for item in index.get("decisions", []) if item.get("rule_id") == "CP-R04"]
        if len(presentation_decisions) != 1 or presentation_decisions[0].get("status") != "closed":
            raise ValueError("Spec 05 presentation decision is missing or unresolved")
        with zipfile.ZipFile(Path(artifacts["delivery_zip"]["path"])) as archive:
            names = set(archive.namelist())
            for name, item in assets.items():
                if item.get("decision", {}).get("status") != "closed" or item.get("compatibility", {}).get("status") != "approved":
                    raise ValueError(f"presentation {name} is not closed and approved")
                member = item.get("template_member") if item.get("mode") == "template_default" else item.get("project_path")
                if member not in names or hashlib.sha256(archive.read(member)).hexdigest() != item.get("asset_sha256"):
                    raise ValueError(f"presentation {name} asset is absent or drifted in the delivery ZIP")
        return {"assets": {name: assets[name]["mode"] for name in sorted(assets)}, "hard_gates": len(hard)}

    def commit_order() -> dict[str, Any]:
        expected = ["producer_execution_capability_E", "exact_zip_compile_and_render_evidence_B", "decision_index_D", "build_and_stage_commit_M"]
        if stage.get("commit_order") != expected or context["build"].get("commit_order") != expected:
            raise ValueError("Spec 05 E-to-B-to-D-to-M commit order is invalid")
        index = read_json(Path(artifacts["decision_index_D"]["path"]))
        if index.get("acyclic_commit_rule") != "producer_execution_capability_E_then_build_evidence_B_then_decision_index_D_then_stage_commit_M":
            raise ValueError("decision index has an invalid acyclic commit rule")
        return {"commit_order": expected}

    def exact_build_and_render_pack() -> dict[str, Any]:
        build = context["build"]
        if build.get("formal_zip_is_build_input") is not True or build.get("clean_build") is not True:
            raise ValueError("final ZIP was not the clean build input")
        pdf = Path(artifacts["final_pdf"]["path"])
        manifest = read_json(Path(artifacts["render_pack"]["path"]))
        if manifest.get("status") != "complete" or manifest.get("final_pdf", {}).get("sha256") != sha256_file(pdf):
            raise ValueError("final render pack is not bound to the final PDF")
        pages = manifest.get("pages", [])
        if len(pages) != manifest.get("page_count") or [item.get("index") for item in pages] != list(range(1, len(pages) + 1)):
            raise ValueError("final render pack page sequence is incomplete")
        for item in pages:
            path = Path(artifacts["render_pack"]["path"]).parent / item["raster_path"]
            if not path.is_file() or sha256_file(path) != item["raster_sha256"]:
                raise ValueError(f"rendered page is missing or drifted: {item.get('index')}")
        return {"pages": len(pages), "pdf_sha256": sha256_file(pdf)}

    def producer_capability() -> dict[str, Any]:
        path = Path(artifacts["execution_capability_E"]["path"])
        result = execution.validate_manifest(path)
        stored = read_json(path)
        if stage["execution_capability_E"].get("payload_hash") != stored.get("payload_hash"):
            raise ValueError("stage and producer capability payload hashes differ")
        context["producer_capability"] = result
        return result

    def evaluator_capability() -> dict[str, Any]:
        result = execution.validate_manifest(evaluator_path)
        artifacts["evaluator_execution_capability"] = {"path": str(evaluator_path), "sha256": sha256_file(evaluator_path)}
        return result

    def immutable_run() -> dict[str, Any]:
        path = run / "manifests/run_manifest.json"
        manifest = read_json(path)
        if manifest.get("status") != "passed" or manifest.get("stage_kind") != "spec05_native_execution" or manifest.get("immutable_after_publication") is not True:
            raise ValueError("run manifest is not an immutable Spec 05 pass")
        for item in manifest.get("files", []):
            target = run / item["path"]
            if not target.is_file() or sha256_file(target) != item["sha256"]:
                raise ValueError(f"immutable run file drifted: {item.get('path')}")
        artifacts["run_manifest"] = {"path": str(path), "sha256": sha256_file(path)}
        return {"files": len(manifest.get("files", []))}

    def scope_boundary() -> dict[str, Any]:
        prohibited = set(stage.get("scope_prohibitions", []))
        required = {"semantic_reclassification", "construct_reselection", "media_reselection", "layout_reselection", "presentation_inference", "render_coverage_claim", "spec06_claim", "product_acceptance_claim"}
        if not required.issubset(prohibited):
            raise ValueError("Spec 05 scope prohibitions are incomplete")
        return {"prohibitions": len(prohibited)}

    gate.check("S5-PG-H01-formal-native-stage-shape", stage_shape)
    gate.check("S5-PG-H02-stage-and-build-artifact-hashes", artifact_hashes)
    gate.check("S5-PG-H03-active-full-formal-spec04d-parent", active_parent)
    gate.check("S5-PG-H04-template-hard-gates", template_gates)
    gate.check("S5-PG-H14-template-local-custom-api-usage", template_local_api_usage)
    gate.check("S5-PG-H05-compile-hard-gates", compile_gates)
    gate.check("S5-PG-H15-delivery-zip-size-limit", delivery_zip_size_limit)
    gate.check("S5-PG-H16-delivery-file-and-native-image-policy", delivery_asset_policy)
    gate.check("S5-PG-H17-overleaf-root-main-and-body-binding", overleaf_delivery_compatibility)
    gate.check("S5-PG-H18-cover-identity-delivery-naming", delivery_naming)
    gate.check("S5-PG-H06-decision-closure", decision_closure)
    gate.check("S5-PG-H13-presentation-contract", presentation_contract)
    gate.check("S5-PG-H07-E-to-B-to-D-to-M-acyclicity", commit_order)
    gate.check("S5-PG-H08-exact-zip-build-and-render-pack", exact_build_and_render_pack)
    gate.check("S5-PG-H09-live-producer-execution-capability", producer_capability)
    gate.check("S5-PG-H10-live-evaluator-execution-capability", evaluator_capability)
    gate.check("S5-PG-H11-immutable-run-files", immutable_run)
    gate.check("S5-PG-H12-scope-boundary", scope_boundary)
    disposition = "promoted" if gate.passed else "rejected"
    manifest = {
        "schema_version": "stage-promotion-manifest/1.1", "promotion_id": args.promotion_id,
        "lineage_key": args.lineage_key, "evaluated_at": stage_gate.now(), "evaluator": VERSION,
        "stage_kind": "spec05_native_execution", "run_dir": str(run),
        "stage_manifest": {"path": str(stage_path), "sha256": sha256_file(stage_path)},
        "disposition": disposition, "promotion_class": "formal_native",
        "producer_execution_provenance": "live_verified" if gate.passed else "unverified",
        "evaluator_capability": {"path": str(evaluator_path), "sha256": sha256_file(evaluator_path), "payload_hash": evaluator["payload_hash"]},
        "checks": gate.checks,
        "summary": {"checks": len(gate.checks), "passed": sum(item["status"] == "passed" for item in gate.checks), "failed": sum(item["status"] == "failed" for item in gate.checks)},
        "promoted_artifacts": artifacts if disposition == "promoted" else {},
        "consumer_rule": "Spec 03 render coverage may consume only this exact promoted ZIP, PDF, decision index, compile report, and final render pack.",
        "scope_limit": "Spec 05 compile_pass and final_render_pack only; render coverage, Spec 06, reusable capability maturity, and product acceptance are not evaluated.",
    }
    stage_gate.write_json(output, manifest)
    return manifest, 0 if disposition == "promoted" else 4
