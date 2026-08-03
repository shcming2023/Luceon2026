#!/usr/bin/env python3
"""Freeze an authoritative Spec-05 template contract without editing the template."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

VERSION = "template-contract-freezer/1.2.0"
SAFE_METADATA_MACROS = ("title", "subtitle", "author", "institute", "date", "extrainfo", "logo", "cover")
PRESENTATION_MACROS = ("cover", "logo")
PRESENTATION_MODES = {"template_default", "source_region_asset", "approved_static_asset"}
PRESENTATION_EXTENSIONS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(base: Path, target: Path) -> str:
    return os.path.relpath(target, base).replace("\\", "/")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def default_validator_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "luceon-popo-to-refined-elegantbook/scripts/validate_intermediate_contracts.py"
    )


def load_validator(path: Path):
    spec = importlib.util.spec_from_file_location("elegantbook_contract_validator", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load contract validator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_metadata(metadata_path: Path, candidates: list[str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    document = load_json(metadata_path)
    if document.get("schema_version") != "spec05-metadata/1.0" or document.get("status") != "approved_source_grounded":
        raise ValueError("metadata must be an approved source-grounded spec05-metadata/1.0 document")
    values = document.get("values")
    if not isinstance(values, dict) or not values or not set(values).issubset(candidates):
        raise ValueError("selected metadata is empty or outside template capability candidates")
    for name, value in values.items():
        if not isinstance(value, str) or any(character in value for character in "\\{}"):
            raise ValueError(f"metadata must be plain Unicode text, not TeX: {name}")
    evidence_checks = []
    supported: set[str] = set()
    for item in document.get("evidence", []):
        source = (metadata_path.parent / item["source_ref"]).resolve()
        page = (metadata_path.parent / item["page_render_ref"]).resolve()
        source_ok = source.is_file() and sha256_file(source) == item["source_sha256"]
        page_ok = page.is_file() and sha256_file(page) == item["page_render_sha256"]
        if not source_ok or not page_ok:
            raise ValueError(f"metadata evidence hash mismatch: {item.get('source_ref')}")
        supports = item.get("supports", [])
        if not set(supports).issubset(values):
            raise ValueError("metadata evidence claims a field outside selected metadata")
        supported.update(supports)
        evidence_checks.append({
            "source_ref": str(source), "source_sha256": item["source_sha256"], "source_hash_matches": True,
            "pdf_physical_page": item["pdf_physical_page"], "page_render_ref": str(page),
            "page_render_sha256": item["page_render_sha256"], "page_render_hash_matches": True, "supports": supports,
        })
    nonempty = {name for name, value in values.items() if value}
    unsupported = sorted(nonempty - supported)
    if unsupported:
        raise ValueError(f"non-empty metadata lacks source evidence: {unsupported}")
    return values, evidence_checks


def safe_relative_path(value: str, field: str) -> Path:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or any(character in value for character in "\\{}"):
        raise ValueError(f"presentation {field} must be a safe relative path")
    return path


def _verified_file(config_dir: Path, ref: str, expected_hash: str, field: str) -> Path:
    path = (config_dir / ref).resolve()
    if not path.is_file() or sha256_file(path) != expected_hash:
        raise ValueError(f"presentation {field} hash mismatch: {ref}")
    return path


def verify_presentation(
    presentation_path: Path, template_dir: Path, template_zip_sha256: str,
    entry_text: str, output_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate explicit cover/logo choices without inferring product identity."""
    from PIL import Image, ImageChops

    document = load_json(presentation_path)
    if document.get("schema_version") != "spec05-presentation-config/1.0" or document.get("status") != "approved":
        raise ValueError("presentation config must be an approved spec05-presentation-config/1.0 document")
    if document.get("template_zip_sha256") != template_zip_sha256:
        raise ValueError("presentation config is bound to a different template ZIP")
    assets = document.get("assets")
    if not isinstance(assets, dict) or set(assets) != set(PRESENTATION_MACROS):
        raise ValueError("presentation config must declare exactly cover and logo")
    config_dir = presentation_path.parent
    scope_pages: dict[int, dict[str, Any]] = {}
    scope_binding = document.get("source_scope_binding")
    normalized_scope = None
    if scope_binding is not None:
        if not isinstance(scope_binding, dict):
            raise ValueError("presentation source_scope_binding must be an object")
        scope_path = _verified_file(config_dir, scope_binding.get("ledger_ref", ""), scope_binding.get("ledger_sha256", ""), "scope ledger")
        scope = load_json(scope_path)
        scope_pages = {int(item["physical_page"]): item for item in scope.get("pages", []) if "physical_page" in item}
        normalized_scope = {
            "ledger_ref": relative(output_dir, scope_path),
            "ledger_sha256": sha256_file(scope_path),
        }

    normalized_assets: dict[str, Any] = {}
    evidence_checks: list[dict[str, Any]] = []
    decision_ids: set[str] = set()
    for macro in PRESENTATION_MACROS:
        item = assets[macro]
        if not isinstance(item, dict) or item.get("mode") not in PRESENTATION_MODES:
            raise ValueError(f"unsupported presentation mode for {macro}")
        macro_value = item.get("macro_value")
        macro_path = safe_relative_path(macro_value, f"{macro}.macro_value")
        if macro_path.suffix.lower() not in PRESENTATION_EXTENSIONS:
            raise ValueError(f"presentation {macro} macro value has an unsupported image extension")
        decision = item.get("decision", {})
        decision_id = decision.get("decision_id")
        if not decision_id or decision_id in decision_ids or decision.get("status") != "closed" or not decision.get("rationale"):
            raise ValueError(f"presentation {macro} requires one unique closed decision")
        refs = decision.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref for ref in refs):
            raise ValueError(f"presentation {macro} decision lacks evidence refs")
        decision_ids.add(decision_id)
        compatibility = item.get("compatibility", {})
        if compatibility.get("status") != "approved" or compatibility.get("assertion") not in {"source_identity", "source_compatible", "output_brand", "neutral"}:
            raise ValueError(f"presentation {macro} compatibility is not approved")
        occurrences = re.findall(rf"\\{re.escape(macro)}\{{([^{{}}]*)\}}", entry_text)
        if len(occurrences) != 1:
            raise ValueError(f"template must expose exactly one {macro} macro")

        normalized = {
            "mode": item["mode"], "macro_value": macro_value,
            "decision": decision, "compatibility": compatibility,
        }
        if item["mode"] == "template_default":
            member = safe_relative_path(item.get("template_member", ""), f"{macro}.template_member")
            template_asset = template_dir / member
            if occurrences[0] != macro_value:
                raise ValueError(f"template_default {macro} macro value differs from the frozen template")
            if not template_asset.is_file() or sha256_file(template_asset) != item.get("asset_sha256"):
                raise ValueError(f"template_default {macro} asset hash mismatch")
            normalized.update({
                "template_member": member.as_posix(), "asset_sha256": sha256_file(template_asset),
                "media_type": PRESENTATION_EXTENSIONS[template_asset.suffix.lower()],
            })
            evidence_checks.append({"macro": macro, "mode": item["mode"], "asset_sha256": sha256_file(template_asset), "template_member_verified": True})
        else:
            project_path = safe_relative_path(item.get("project_path", ""), f"{macro}.project_path")
            if project_path.suffix.lower() not in PRESENTATION_EXTENSIONS or project_path.name != macro_path.name:
                raise ValueError(f"presentation {macro} project path and macro value must name the same image")
            asset_path = _verified_file(config_dir, item.get("asset_ref", ""), item.get("asset_sha256", ""), f"{macro} asset")
            media_type = PRESENTATION_EXTENSIONS.get(asset_path.suffix.lower())
            if media_type != item.get("media_type") or project_path.suffix.lower() != asset_path.suffix.lower():
                raise ValueError(f"presentation {macro} media type or extension mismatch")
            with Image.open(asset_path) as image:
                dimensions = {"width": image.width, "height": image.height}
                if dimensions != item.get("pixel_dimensions"):
                    raise ValueError(f"presentation {macro} pixel dimensions mismatch")
            normalized.update({
                "project_path": project_path.as_posix(), "asset_ref": relative(output_dir, asset_path),
                "asset_sha256": sha256_file(asset_path), "media_type": media_type, "pixel_dimensions": dimensions,
            })
            check: dict[str, Any] = {"macro": macro, "mode": item["mode"], "asset_sha256": sha256_file(asset_path), "asset_hash_verified": True}
            if item["mode"] == "source_region_asset":
                provenance = item.get("provenance", {})
                source_path = _verified_file(config_dir, provenance.get("source_ref", ""), provenance.get("source_sha256", ""), f"{macro} source PDF")
                page_path = _verified_file(config_dir, provenance.get("page_render_ref", ""), provenance.get("page_render_sha256", ""), f"{macro} source page")
                box = provenance.get("bbox_px")
                if provenance.get("coordinate_space") != "source_page_pixels" or provenance.get("fit_policy") != "template_native_width_no_additional_crop":
                    raise ValueError(f"presentation {macro} has an unsupported crop or fit policy")
                if not isinstance(box, list) or len(box) != 4 or any(not isinstance(value, int) for value in box):
                    raise ValueError(f"presentation {macro} bbox_px is invalid")
                with Image.open(page_path) as source_image, Image.open(asset_path) as asset_image:
                    x0, y0, x1, y1 = box
                    if not (0 <= x0 < x1 <= source_image.width and 0 <= y0 < y1 <= source_image.height):
                        raise ValueError(f"presentation {macro} bbox is outside the source page")
                    expected = source_image.crop((x0, y0, x1, y1))
                    if expected.size != asset_image.size or ImageChops.difference(expected.convert("RGB"), asset_image.convert("RGB")).getbbox() is not None:
                        raise ValueError(f"presentation {macro} asset pixels differ from the declared source region")
                page_number = provenance.get("pdf_physical_page")
                scope_status = provenance.get("body_scope_status")
                if not isinstance(page_number, int) or page_number < 1:
                    raise ValueError(f"presentation {macro} physical page is invalid")
                if scope_pages and (page_number not in scope_pages or scope_pages[page_number].get("status") != scope_status):
                    raise ValueError(f"presentation {macro} body-scope status differs from the bound ledger")
                normalized["provenance"] = {
                    **provenance,
                    "source_ref": relative(output_dir, source_path),
                    "page_render_ref": relative(output_dir, page_path),
                }
                check.update({"source_hash_verified": True, "page_hash_verified": True, "source_region_pixels_verified": True, "body_scope_status_verified": bool(scope_pages)})
            evidence_checks.append(check)
        normalized_assets[macro] = normalized
    return {
        "schema_version": document["schema_version"], "status": document["status"],
        "template_zip_sha256": document["template_zip_sha256"],
        "source_scope_binding": normalized_scope, "assets": normalized_assets,
    }, evidence_checks


def capability_view(capability: dict[str, Any], entry_text: str) -> dict[str, Any]:
    """Normalize the historical flat and current Spec 04-C capability schemas."""
    if capability.get("schema_version") == "template-capability-manifest/2.0":
        constructs = capability.get("constructs", {})
        candidates = [
            name for name in SAFE_METADATA_MACROS
            if len(re.findall(rf"\\{re.escape(name)}\{{[^{{}}]*\}}", entry_text)) == 1
        ]
        return {
            "template_zip_sha256": capability.get("template_archive", {}).get("sha256"),
            "entry_sha256": capability.get("entry", {}).get("sha256"),
            "class_ref": capability.get("class", {}).get("member", "elegantbook.cls"),
            "class_sha256": capability.get("class", {}).get("sha256"),
            "documentclass": capability.get("documentclass", {}),
            "tcolorbox_styles": sorted(constructs.get("tcolorbox_styles", {})),
            "custom_commands": sorted(constructs.get("custom_commands", [])),
            "custom_environments": sorted(constructs.get("custom_environments", [])),
            "custom_environment_inventory_mode": "all_declared_environments",
            "metadata_candidates": candidates,
        }
    return {
        "template_zip_sha256": capability.get("template_zip_sha256"),
        "entry_sha256": capability.get("entry", {}).get("sha256"),
        "class_ref": capability.get("class", {}).get("ref", "elegantbook.cls"),
        "class_sha256": capability.get("class", {}).get("sha256"),
        "documentclass": capability.get("documentclass", {}),
        "tcolorbox_styles": sorted(capability.get("tcolorbox_styles", [])),
        "custom_commands": sorted(capability.get("custom_commands", [])),
        "custom_environments": sorted(capability.get("custom_environments", [])),
        # Historical 1.x manifests inventoried explicit tcolorbox wrappers but
        # did not include helper environments declared with NewEnviron.  Preserve
        # that documented scope without weakening the exact 2.0 contract.
        "custom_environment_inventory_mode": "newtcolorboxes_only",
        "metadata_candidates": capability.get("metadata_candidates", []),
    }


def freeze(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    template_dir = args.template_dir.resolve()
    template_zip = args.template_zip.resolve()
    capability_path = args.capability_manifest.resolve()
    metadata_path = args.metadata_config.resolve()
    output = args.output.resolve()
    validator = load_validator((args.contract_validator or default_validator_path()).resolve())
    capability = load_json(capability_path)
    entry = template_dir / args.entry
    if not template_dir.is_dir() or not template_zip.is_file() or not entry.is_file():
        raise FileNotFoundError("template directory, source ZIP, or entry file is missing")
    if any(path.is_symlink() for path in template_dir.rglob("*")):
        raise ValueError("template contains a forbidden symlink")
    text = entry.read_text(encoding="utf-8")
    if text.count(args.body_marker) != 1 or text.count(args.body_end_token) != 1:
        raise ValueError("body marker and end token must each occur exactly once")
    if text.index(args.body_marker) >= text.index(args.body_end_token):
        raise ValueError("body marker must precede the body end token")

    normalized = capability_view(capability, text)
    candidates = normalized["metadata_candidates"]
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("capability manifest has no metadata candidates")
    selected, evidence_checks = verify_metadata(metadata_path, candidates)
    for name in candidates:
        occurrences = len(re.findall(rf"\\{re.escape(name)}\{{[^{{}}]*\}}", text))
        if name in selected and occurrences != 1:
            raise ValueError(f"selected metadata macro must occur exactly once: {name}; found {occurrences}")

    zip_hash = sha256_file(template_zip)
    if normalized["template_zip_sha256"] != zip_hash:
        raise ValueError("capability manifest template ZIP binding mismatch")
    if normalized["entry_sha256"] != sha256_file(entry):
        raise ValueError("capability manifest entry binding mismatch")
    class_path = template_dir / Path(normalized["class_ref"]).name
    if not class_path.is_file() or normalized["class_sha256"] != sha256_file(class_path):
        raise ValueError("capability manifest class binding mismatch")

    presentation = None
    presentation_checks: list[dict[str, Any]] = []
    if getattr(args, "presentation_config", None):
        presentation_path = args.presentation_config.resolve()
        presentation, presentation_checks = verify_presentation(
            presentation_path, template_dir, zip_hash, text, output.parent,
        )

    immutable = [
        {"path": path.relative_to(template_dir).as_posix(), "sha256": sha256_file(path)}
        for path in sorted(template_dir.rglob("*")) if path.is_file() and path != entry
    ]
    allowlist = {
        name: ("static resource path; value unchanged unless separately approved" if name in {"logo", "cover"} else "plain Unicode text or empty")
        for name in candidates
    }
    contract: dict[str, Any] = {
        "schema_version": "template-contract/2.0" if presentation else "template-contract/1.0", "generated_at": now(), "generator": VERSION, "status": "frozen",
        "template_zip": {"ref": relative(output.parent, template_zip), "sha256": zip_hash},
        "immutable_files": immutable,
        "main_template": {
            "ref": relative(output.parent, entry), "sha256": sha256_file(entry), "masked_main_sha256": "",
            "body_marker": args.body_marker, "body_marker_line": text[:text.index(args.body_marker)].count("\n") + 1,
            "body_end_token": args.body_end_token, "body_end_line": text[:text.index(args.body_end_token)].count("\n") + 1,
        },
        "documentclass": validator.documentclass_inventory(text),
        "package_inventory": validator.package_inventory(text),
        "custom_api_inventory": validator.api_inventory(text),
        "metadata_allowlist": allowlist,
        "selected_metadata": selected,
        "metadata_config": {"ref": relative(output.parent, metadata_path), "sha256": sha256_file(metadata_path), "evidence_checks": evidence_checks},
        "body_insertion": {"file": args.entry, "after_exact_marker": args.body_marker, "before_exact_token": args.body_end_token},
        "generated_body_transport": {
            "mode": "single_hash_bound_tex_payload",
            "project_path": "body/generated-body.tex",
            "input_literal": r"\input{body/generated-body.tex}",
            "root_entry": args.entry,
            "nested_input_include_forbidden": True,
            "tex_definitions_forbidden": True,
            "payload_must_equal_rendered_body": True,
        },
        "ancillary_policy": {
            "allowed_extensions": [".jpg", ".jpeg", ".png", ".bib"],
            "allowed_purposes": ["frozen template resource", "approved presentation asset", "ledger-confirmed source media", "render-plan source-region crop", "empty bibliographic data file explicitly referenced by the frozen template"],
            "forbidden_extensions": [".sty", ".cls", ".tex", ".cfg", ".def", ".lua", ".py", ".sh"],
            "symlinks_forbidden": True,
        },
        "compile_contract": {
            "engine": args.engine, "driver": args.driver, "container": args.container,
            "minimum": args.minimum_tex, "clean_build": True, "max_runs": args.max_runs,
        },
    }
    if presentation:
        contract["presentation_config"] = {
            "ref": relative(output.parent, args.presentation_config.resolve()),
            "sha256": sha256_file(args.presentation_config.resolve()),
            "evidence_checks": presentation_checks,
        }
        contract["selected_presentation"] = presentation
    contract["main_template"]["masked_main_sha256"] = hashlib.sha256(validator.mask_main(text, contract).encode("utf-8")).hexdigest()

    api = contract["custom_api_inventory"]
    checks = {
        "template_zip_hash_matches": normalized["template_zip_sha256"] == zip_hash,
        "entry_hash_matches": normalized["entry_sha256"] == sha256_file(entry),
        "class_hash_matches": normalized["class_sha256"] == sha256_file(class_path),
        "documentclass_matches": normalized["documentclass"]["name"] == contract["documentclass"]["name"] and normalized["documentclass"]["options"] == contract["documentclass"]["options"],
        "tcolorbox_styles_match": set(normalized["tcolorbox_styles"]) == set(api["tcolorbox_styles"]),
        "custom_commands_match": set(normalized["custom_commands"]) == set(api["newcommands"]),
        "custom_environments_match": set(normalized["custom_environments"]) == (
            set(api["newtcolorboxes"]) | set(api["newenvirons"])
            if normalized["custom_environment_inventory_mode"] == "all_declared_environments"
            else set(api["newtcolorboxes"])
        ),
        "metadata_evidence_verified": all(item["source_hash_matches"] and item["page_render_hash_matches"] for item in evidence_checks),
        "presentation_config_verified": presentation is None or all(
            item.get("template_member_verified") is True or item.get("asset_hash_verified") is True
            for item in presentation_checks
        ),
    }
    if not all(checks.values()):
        raise ValueError(f"template capability validation failed: {[key for key, value in checks.items() if not value]}")
    report = {
        "schema_version": "template-capability-validation/2.0", "generated_at": now(), "generator": VERSION,
        "status": "passed", "capability_manifest": {"path": str(capability_path), "sha256": sha256_file(capability_path)},
        "template_contract_output": str(output), "checks": checks,
    }
    return contract, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze an authoritative template_contract.json from a read-only template")
    parser.add_argument("--template-dir", type=Path, required=True)
    parser.add_argument("--template-zip", type=Path, required=True)
    parser.add_argument("--capability-manifest", type=Path, required=True)
    parser.add_argument("--metadata-config", type=Path, required=True)
    parser.add_argument("--presentation-config", type=Path)
    parser.add_argument("--entry", default="main.tex")
    parser.add_argument("--body-marker", required=True)
    parser.add_argument("--body-end-token", default=r"\end{document}")
    parser.add_argument("--engine", default="XeLaTeX")
    parser.add_argument("--driver", default="latexmk -xelatex")
    parser.add_argument("--container", default="sharelatex")
    parser.add_argument("--minimum-tex", default="TeX Live 2025")
    parser.add_argument("--max-runs", type=int, default=5)
    parser.add_argument("--contract-validator", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-report", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.validation_report.exists():
        print(json.dumps({"status": "failed", "error": "refusing to overwrite contract or validation report"}), file=sys.stderr)
        return 1
    try:
        contract, report = freeze(args)
        write_json(args.output.resolve(), contract)
        write_json(args.validation_report.resolve(), report)
        print(json.dumps({"status": "passed", "contract": str(args.output.resolve()), "contract_sha256": sha256_file(args.output.resolve())}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "generator": VERSION, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
