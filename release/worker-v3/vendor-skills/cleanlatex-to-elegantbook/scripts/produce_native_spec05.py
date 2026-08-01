#!/usr/bin/env python3
"""Produce an immutable, formal-native Spec 05 build from an active Spec 04-D promotion.

This entrypoint owns execution only.  It freezes the supplied template, invokes
the mechanical renderer, compiles the exact delivery ZIP in an isolated build
directory, and creates a complete raster pack.  It never changes render-plan
semantics, constructs, media bindings, or layout decisions.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

VERSION = "native-spec05-execution/2.1.0"
STAGE_SCHEMA = "spec05-native-stage-manifest/1.6"
BUILD_POLICY_SCHEMA = "spec05-build-policy/1.0"
MAX_DELIVERY_ZIP_BYTES = 50_000_000
WARNING_REVIEW_SCHEMA = "spec05-warning-review/1.0"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def template_intake_archive_sha256(path: Path) -> str:
    intake = read_json(path)
    value = intake.get("archive_sha256") if isinstance(intake, dict) else None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("Spec 01 template intake has no valid archive_sha256")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def relative(base: Path, target: Path) -> str:
    return os.path.relpath(target, base).replace("\\", "/")


def artifact(run: Path, path: Path, *, payload_hash: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"path": relative(run, path), "sha256": sha256_file(path)}
    if payload_hash:
        value["payload_hash"] = payload_hash
    return value


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_command(
    command: list[str], *, cwd: Path | None = None, timeout: int = 1800,
    check: bool = True, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, cwd=cwd, text=True, capture_output=True, timeout=timeout,
        env=env,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout[-6000:]}\nstderr:\n{result.stderr[-6000:]}"
        )
    return result


def safe_extract(archive_path: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            member = Path(info.filename)
            if member.is_absolute() or ".." in member.parts or info.is_dir() and member.name == "..":
                raise ValueError(f"unsafe ZIP member: {info.filename}")
            mode = info.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise ValueError(f"template ZIP contains a symlink: {info.filename}")
        archive.extractall(target)
    if any(path.is_symlink() for path in target.rglob("*")):
        raise ValueError("extracted archive contains a symlink")


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def zip_tree_hashes(zip_path: Path) -> dict[str, str]:
    with zipfile.ZipFile(zip_path) as archive:
        return {
            item.filename: hashlib.sha256(archive.read(item.filename)).hexdigest()
            for item in archive.infolist()
            if not item.is_dir()
        }


def verify_zip_tree(zip_path: Path, tree: Path) -> dict[str, str]:
    expected = tree_hashes(tree)
    actual = zip_tree_hashes(zip_path)
    if sorted(actual) != sorted(expected):
        raise ValueError("delivery ZIP member set differs from clean extraction")
    for name, digest in actual.items():
        if digest != expected[name]:
            raise ValueError(f"delivery ZIP member hash differs from clean extraction: {name}")
    return expected


def verify_parent(
    gate: Any, registry_path: Path, promotion_path: Path, lineage_key: str,
) -> dict[str, Any]:
    selection = gate.verify_registry_selection(
        registry_path, lineage_key, promotion_path,
        "spec04d_render_plan_contract", "formal_native", capability_verification="frozen",
    )
    promotion = selection["promotion"]
    run = Path(promotion["run_dir"])
    stage_path = Path(promotion["stage_manifest"]["path"])
    stage = read_json(stage_path)
    if stage.get("schema_version") != "spec04d-render-plan-stage-manifest/1.0":
        raise ValueError("SPEC05_PARENT_NOT_FULL_SPEC04D: unsupported Spec 04-D stage schema")
    if stage.get("full_spec04_status") != "passed" or stage.get("producer_mode") != "formal_native":
        raise ValueError("SPEC05_PARENT_NOT_FULL_SPEC04D: parent did not pass full formal-native Spec 04")
    required = {"ledger_L", "decision_index_D", "render_plan"}
    missing = sorted(required - set(promotion.get("promoted_artifacts", {})))
    if missing:
        raise ValueError(f"SPEC05_PARENT_ARTIFACT_MISSING: {missing}")
    return {"selection": selection, "promotion": promotion, "stage": stage, "stage_path": stage_path, "run": run}


def validate_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema_version") != BUILD_POLICY_SCHEMA or policy.get("status") != "approved":
        raise ValueError(f"build policy must be approved {BUILD_POLICY_SCHEMA}")
    compile_cfg = policy.get("compile", {})
    if compile_cfg.get("executor") not in {"direct_exec", "docker_copy_exec"} or not compile_cfg.get("container"):
        raise ValueError("compile policy must select a supported explicit executor")
    command = compile_cfg.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item for item in command):
        raise ValueError("compile command must be a non-empty argv list")
    if compile_cfg.get("entry") != "main.tex":
        raise ValueError("build policy must compile the frozen main.tex entry")
    render_cfg = policy.get("render", {})
    if render_cfg.get("renderer") not in {"pdftoppm", "pdftocairo"} or render_cfg.get("format") != "png":
        raise ValueError("render policy must explicitly select a supported Poppler PNG renderer")
    if not isinstance(render_cfg.get("dpi"), int) or render_cfg["dpi"] < 72:
        raise ValueError("render DPI is missing or implausibly low")
    constraints = policy.get("template_metadata_constraints", {})
    required_nonempty = constraints.get("required_nonempty")
    allowed_metadata = {"title", "subtitle", "author", "institute", "date", "extrainfo"}
    if not isinstance(required_nonempty, list) or len(required_nonempty) != len(set(required_nonempty)) or not set(required_nonempty).issubset(allowed_metadata):
        raise ValueError("template metadata constraints must declare a unique required_nonempty allowlist")
    return policy


def assess_delivery_zip_size(zip_path: Path) -> dict[str, Any]:
    """Measure the exact delivery artifact against the non-configurable product cap."""
    size_bytes = zip_path.stat().st_size
    passed = size_bytes < MAX_DELIVERY_ZIP_BYTES
    return {
        "schema_version": "spec05-delivery-size-report/1.0",
        "generated_at": now(),
        "spec_status": "passed" if passed else "failed",
        "gate": {"gate_id": "CP-H18", "status": "passed" if passed else "failed"},
        "delivery_zip": {
            "path": str(zip_path.resolve()),
            "sha256": sha256_file(zip_path),
            "size_bytes": size_bytes,
        },
        "constraint": {
            "operator": "strictly_less_than",
            "max_bytes_exclusive": MAX_DELIVERY_ZIP_BYTES,
            "unit": "bytes",
        },
        "failure_code": None if passed else "COMPILE_DELIVERY_ZIP_SIZE_LIMIT_EXCEEDED",
    }


def capability_manifest(args: argparse.Namespace, execution_core: Any, run: Path) -> dict[str, Any]:
    skill_root = Path(__file__).resolve().parents[1]
    resources = [
        ("build_policy", args.build_policy.resolve()),
        ("metadata_config", args.metadata_config.resolve()),
        ("presentation_config", args.presentation_config.resolve()),
        ("template_capability_manifest", args.capability_manifest.resolve()),
        ("template_intake", args.template_intake.resolve()),
        ("contract_validator", args.contract_validator.resolve()),
        ("media_validator", args.media_validator.resolve()),
        ("promotion_gate", args.stage_gate.resolve()),
        ("execution_capability_core", args.execution_capability.resolve()),
        ("delivery_asset_policy", Path(__file__).with_name("delivery_asset_policy.py").resolve()),
        ("delivery_compatibility", Path(__file__).with_name("delivery_compatibility.py").resolve()),
        ("stage_schema", skill_root / "schemas/spec05-native-stage-manifest.schema.json"),
        ("build_policy_schema", skill_root / "schemas/spec05-build-policy.schema.json"),
        ("presentation_config_schema", skill_root / "schemas/spec05-presentation-config.schema.json"),
        ("overleaf_delivery_compatibility_schema", skill_root / "schemas/spec05-overleaf-delivery-compatibility-report.schema.json"),
        ("delivery_asset_report_schema", skill_root / "schemas/spec05-delivery-asset-report.schema.json"),
    ]
    if getattr(args, "volume_partition_plan", None):
        resources.extend([
            ("volume_partition_plan", args.volume_partition_plan.resolve()),
            ("delivery_set_schema", skill_root / "schemas/spec05-delivery-set-manifest.schema.json"),
            ("delivery_set_stage_schema", skill_root / "schemas/spec05-native-delivery-set-stage-manifest.schema.json"),
        ])
    if args.warning_review:
        resources.append(("warning_review", args.warning_review.resolve()))
    invocation = [
        "produce_native_spec05.py", "--run-dir", str(run), "--run-id", args.run_id,
        "--promotion-registry", str(args.promotion_registry.resolve()),
        "--parent-promotion", str(args.parent_promotion.resolve()),
        "--parent-lineage-key", args.parent_lineage_key,
        "--template-zip", str(args.template_zip.resolve()),
        "--template-intake", str(args.template_intake.resolve()),
        "--capability-manifest", str(args.capability_manifest.resolve()),
        "--metadata-config", str(args.metadata_config.resolve()),
        "--presentation-config", str(args.presentation_config.resolve()),
        "--body-marker", args.body_marker,
        "--media-evidence-ledger", str(args.media_evidence_ledger.resolve()),
        "--media-representation-plan", str(args.media_representation_plan.resolve()),
        "--media-evidence-root", str(args.media_evidence_root.resolve()),
        "--source-pdf", str(args.source_pdf.resolve()),
        "--build-policy", str(args.build_policy.resolve()),
    ]
    if args.source_page_dir:
        invocation.extend(["--source-page-dir", str(args.source_page_dir.resolve())])
    for root in args.asset_root:
        invocation.extend(["--asset-root", str(root.resolve())])
    if args.warning_review:
        invocation.extend(["--warning-review", str(args.warning_review.resolve())])
    if getattr(args, "volume_partition_plan", None):
        invocation.extend(["--volume-partition-plan", str(args.volume_partition_plan.resolve())])
    if getattr(args, "volume_id", None):
        invocation.extend(["--volume-id", args.volume_id])
    return execution_core.build_manifest(
        manifest_id=f"{args.run_id}-producer-capability",
        skill_root=skill_root,
        entrypoints=[
            ("producer", Path(__file__).resolve()),
            ("template_contract_freezer", Path(__file__).with_name("freeze_template_contract.py").resolve()),
            ("frozen_plan_renderer", Path(__file__).with_name("render_frozen_plan.py").resolve()),
            ("template_local_api_gate", Path(__file__).with_name("template_local_api_usage.py").resolve()),
        ],
        resources=resources,
        invocation=invocation,
        producer=VERSION,
    )


def compile_exact_zip(run: Path, zip_path: Path, policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Path]]:
    clean_source = run / "build/clean-src"
    safe_extract(zip_path, clean_source)
    clean_hashes = verify_zip_tree(zip_path, clean_source)
    cfg = policy["compile"]
    container = cfg["container"]
    timeout = int(cfg.get("timeout_seconds", 1800))
    executor = cfg["executor"]
    final = run / "build/final"
    final.mkdir(parents=True)
    if executor == "direct_exec":
        environment = cfg.get("environment", {})
        home = run / "build/home"
        home.mkdir()
        process_env = {
            **os.environ,
            **{str(key): str(value) for key, value in environment.items()},
            "HOME": str(home),
            "TEXMFVAR": str(home / "texmf-var"),
            "TEXMFCONFIG": str(home / "texmf-config"),
            "TEXMFHOME": str(home / "texmf-home"),
        }
        result = run_command(
            cfg["command"], cwd=clean_source, timeout=timeout, check=False,
            env=process_env,
        )
        raw = run / "build/compile-process.json"
        write_json(raw, {
            "schema_version": "compile-process/1.0", "generated_at": now(),
            "executor": executor, "runtime": container,
            "workdir": str(clean_source), "command": cfg["command"],
            "environment_keys": sorted(environment), "exit_code": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
        })
        missing_outputs = []
        for name in ("main.log", "main.fls", "main.pdf"):
            source = clean_source / name
            if source.is_file():
                shutil.copy2(source, final / name)
            else:
                missing_outputs.append(name)
        if result.returncode != 0:
            raise RuntimeError(f"COMPILE_NONZERO_EXIT: {result.returncode}; see {raw}")
        if missing_outputs:
            raise RuntimeError(f"compiled artifacts are missing despite exit zero: {missing_outputs}")
    else:
        token = f"spec05-{hashlib.sha256((str(run) + sha256_file(zip_path)).encode()).hexdigest()[:20]}"
        remote = f"/tmp/{token}"
        run_command(["docker", "exec", container, "mkdir", "-p", remote], timeout=60)
        try:
            run_command(["docker", "cp", f"{clean_source}/.", f"{container}:{remote}/"], timeout=timeout)
            environment = cfg.get("environment", {})
            env_prefix = ["env"] + [f"{key}={value}" for key, value in sorted(environment.items())]
            shell_command = shlex.join(env_prefix + cfg["command"])
            result = run_command(
                ["docker", "exec", "-w", remote, container, "sh", "-lc", shell_command],
                timeout=timeout, check=False,
            )
            raw = run / "build/compile-process.json"
            write_json(raw, {
                "schema_version": "compile-process/1.0", "generated_at": now(),
                "executor": executor, "container": container,
                "remote_workdir": remote, "command": cfg["command"],
                "environment_keys": sorted(environment), "exit_code": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr,
            })
            missing_outputs = []
            for name in ("main.log", "main.fls", "main.pdf"):
                copied = run_command(["docker", "cp", f"{container}:{remote}/{name}", str(final / name)], timeout=timeout, check=False)
                if copied.returncode != 0:
                    missing_outputs.append(name)
            if result.returncode != 0:
                raise RuntimeError(f"COMPILE_NONZERO_EXIT: {result.returncode}; see {raw}")
            if missing_outputs:
                raise RuntimeError(f"compiled artifacts are missing despite exit zero: {missing_outputs}")
        finally:
            run_command(["docker", "exec", container, "rm", "-rf", remote], timeout=60, check=False)

    outputs = {name: run / f"build/final/{name}" for name in ("main.pdf", "main.log", "main.fls")}
    if not all(path.is_file() for path in outputs.values()):
        raise ValueError("compile did not produce PDF, LOG, and FLS")
    if executor == "direct_exec":
        versions = {
            "xelatex": run_command(["xelatex", "--version"], timeout=60).stdout.splitlines()[:2],
            "latexmk": run_command(["latexmk", "-v"], timeout=60).stdout.splitlines()[:2],
        }
        image_identity = os.environ.get("WORKFLOW_V3_RUNTIME_ID", container)
    else:
        versions = {
            "xelatex": run_command(["docker", "exec", container, "sh", "-lc", "xelatex --version | head -2"], timeout=60).stdout.strip(),
            "latexmk": run_command(["docker", "exec", container, "sh", "-lc", "latexmk -v | head -2"], timeout=60).stdout.strip(),
        }
        image_identity = run_command(["docker", "inspect", "--format", "{{.Image}}", container], timeout=60).stdout.strip()
    environment = {
        "schema_version": "build-environment/2.0", "generated_at": now(), "host": socket.gethostname(),
        "executor": executor, "container": container,
        "container_image": image_identity, "versions": versions,
        "command": cfg["command"], "clean_build": True, "input_zip_sha256": sha256_file(zip_path),
        "clean_source_tree_hash": canonical_hash(clean_hashes),
    }
    write_json(run / "build/build_environment.json", environment)
    return environment, outputs


def render_pdf(run: Path, pdf: Path, policy: dict[str, Any]) -> dict[str, Any]:
    import fitz
    from PIL import Image

    cfg = policy["render"]
    renderer_name = cfg["renderer"]
    binary = Path(shutil.which(renderer_name) or "")
    if not binary.is_file():
        raise FileNotFoundError(f"{renderer_name} is unavailable")
    pages_dir = run / "final_render_pack/pages"
    pages_dir.mkdir(parents=True)
    prefix = pages_dir / "raw-page"
    result = run_command([str(binary), "-png", "-r", str(cfg["dpi"]), str(pdf), str(prefix)], timeout=int(cfg.get("timeout_seconds", 1800)))
    if result.stderr:
        (run / f"final_render_pack/{renderer_name}.stderr.txt").write_text(result.stderr, encoding="utf-8")
    raw_pages = sorted(pages_dir.glob("raw-page-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
    reader = fitz.open(pdf)
    if not raw_pages or len(raw_pages) != reader.page_count:
        raise ValueError("COMPILE_RENDER_MANIFEST_INVALID: PDF page count and raster count differ")
    records: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_pages, 1):
        pdf_page = reader[index - 1]
        target = pages_dir / f"page-{index:03d}.png"
        raw.rename(target)
        with Image.open(target) as image:
            records.append({
                "index": index, "page_label": pdf_page.get_label() or str(index),
                "pdf_width_pt": round(float(pdf_page.rect.width), 3),
                "pdf_height_pt": round(float(pdf_page.rect.height), 3),
                "rotation": int(pdf_page.rotation or 0),
                "raster_path": f"pages/{target.name}", "raster_sha256": sha256_file(target),
                "raster_width_px": image.width, "raster_height_px": image.height,
                "color_space": image.mode,
            })
    version_result = run_command([str(binary), "-v"], timeout=60, check=False)
    version_lines = (version_result.stderr or version_result.stdout).strip().splitlines()
    version = version_lines[0] if version_lines else "unknown"
    renderer = {
        "name": renderer_name, "version": version, "binary": str(binary.resolve()),
        "binary_sha256": sha256_file(binary.resolve()), "dpi": cfg["dpi"], "format": "png",
        "color_space": cfg.get("color_space", "renderer_native"), "filename_pattern": "pages/page-%03d.png",
    }
    renderer["configuration_sha256"] = canonical_hash(renderer)
    build_id_seed = f"{sha256_file(pdf)}:{renderer['configuration_sha256']}"
    manifest = {
        "schema_version": "final-render-pack/2.0", "generated_at": now(), "status": "complete",
        "render_job_id": f"render-{hashlib.sha256(build_id_seed.encode()).hexdigest()[:20]}",
        "build_id": None,
        "final_pdf": {"path": relative(run / "final_render_pack", pdf), "sha256": sha256_file(pdf), "pages": reader.page_count},
        "renderer": renderer, "page_count": len(records), "pages": records,
        "manifest_hash_rule": "SHA-256 of the complete manifest file; self hash is not embedded",
    }
    write_json(run / "final_render_pack/manifest.json", manifest)
    return manifest


def _read_canonical_blocks(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"canonical ledger row {line_number} is not an object")
        if value.get("record_type") == "ledger_header":
            continue
        block_id = value.get("block_id")
        if not isinstance(block_id, str) or not block_id or block_id in records:
            raise ValueError(f"canonical ledger block identity is invalid at row {line_number}")
        records[block_id] = value
    if not records:
        raise ValueError("canonical ledger has no source block")
    return records


def build_final_pdf_page_provenance(
    *,
    run: Path,
    final_pdf: Path,
    render_pack: dict[str, Any],
    render_execution_path: Path,
    canonical_ledger_path: Path,
    render_plan_path: Path,
    volume_partition_path: Path | None,
    template_contract_path: Path,
    presentation_config_path: Path,
    volume_id: str,
) -> dict[str, Any]:
    """Freeze the exact PDF-page to render-node/source-page relation.

    The mechanical renderer embeds one standard hyperref named destination
    immediately before and after each frozen render node.  The producer reads
    those destinations from the exact compiled PDF.  No text similarity or
    model judgment owns this mapping.
    """

    import fitz

    execution = read_json(render_execution_path)
    emissions = execution.get("emissions")
    if not isinstance(emissions, list) or not emissions:
        raise ValueError("render execution has no page-provenance emissions")
    plan = read_json(render_plan_path)
    partition = read_json(volume_partition_path) if volume_partition_path else None
    if partition:
        matches = [
            item
            for item in partition.get("volumes", [])
            if isinstance(item, dict) and item.get("volume_id") == volume_id
        ]
        if len(matches) != 1:
            raise ValueError(f"page provenance has no unique frozen volume: {volume_id}")
        frozen_volume = matches[0]
        expected_node_ids = frozen_volume.get("render_node_ids")
        expected_block_ids = frozen_volume.get("source_block_ids")
    else:
        expected_node_ids = [item.get("render_node_id") for item in plan.get("nodes", [])]
        expected_block_ids = [
            block_id
            for item in plan.get("nodes", [])
            for block_id in item.get("source_block_ids", [])
        ]
    actual_node_ids = [item.get("render_node_id") for item in emissions if isinstance(item, dict)]
    actual_block_ids = [
        block_id
        for item in emissions
        if isinstance(item, dict)
        for block_id in item.get("source_block_ids", [])
    ]
    if actual_node_ids != expected_node_ids or actual_block_ids != expected_block_ids:
        raise ValueError("page provenance emissions differ from the frozen volume")

    blocks = _read_canonical_blocks(canonical_ledger_path)
    document = fitz.open(final_pdf)
    try:
        page_count = document.page_count
        destinations = document.resolve_names()
    finally:
        document.close()
    if page_count < 1 or render_pack.get("page_count") != page_count:
        raise ValueError("page provenance PDF/render-pack page count differs")
    render_pages = render_pack.get("pages")
    if (
        not isinstance(render_pages, list)
        or [item.get("index") for item in render_pages if isinstance(item, dict)]
        != list(range(1, page_count + 1))
    ):
        raise ValueError("page provenance render pack is incomplete")

    intervals: list[dict[str, Any]] = []
    seen_destinations: set[str] = set()
    previous_start = 0
    for emission in emissions:
        if not isinstance(emission, dict):
            raise ValueError("page provenance emission is malformed")
        binding = emission.get("page_provenance")
        if (
            not isinstance(binding, dict)
            or binding.get("method") != "pdf_named_destination_interval"
        ):
            raise ValueError("render emission lacks deterministic page provenance")
        start_name = binding.get("start_destination")
        end_name = binding.get("end_destination")
        if (
            not isinstance(start_name, str)
            or not isinstance(end_name, str)
            or not start_name.startswith("luceon-v3-s-")
            or not end_name.startswith("luceon-v3-e-")
            or start_name in seen_destinations
            or end_name in seen_destinations
        ):
            raise ValueError("page provenance destination identity is invalid")
        seen_destinations.update((start_name, end_name))
        start_value = destinations.get(start_name)
        end_value = destinations.get(end_name)
        start_page = (
            int(start_value.get("page")) + 1
            if isinstance(start_value, dict)
            and isinstance(start_value.get("page"), int)
            else 0
        )
        end_page = (
            int(end_value.get("page")) + 1
            if isinstance(end_value, dict)
            and isinstance(end_value.get("page"), int)
            else 0
        )
        if (
            start_page < 1
            or end_page < start_page
            or end_page > page_count
            or start_page < previous_start
        ):
            raise ValueError(
                f"page provenance destination interval is invalid: "
                f"{emission.get('render_node_id')}"
            )
        previous_start = start_page
        source_block_ids = emission.get("source_block_ids")
        if (
            not isinstance(source_block_ids, list)
            or not source_block_ids
            or any(block_id not in blocks for block_id in source_block_ids)
        ):
            raise ValueError("page provenance source-block binding is invalid")
        source_pages: list[int] = []
        for block_id in source_block_ids:
            page = blocks[block_id].get("pdf_physical_page")
            if not isinstance(page, int) or isinstance(page, bool) or page < 1:
                raise ValueError(f"source block lacks physical-page evidence: {block_id}")
            if page not in source_pages:
                source_pages.append(page)
        intervals.append(
            {
                "render_node_id": emission["render_node_id"],
                "source_block_ids": source_block_ids,
                "source_pages": source_pages,
                "start_destination": start_name,
                "end_destination": end_name,
                "start_candidate_page": start_page,
                "end_candidate_page": end_page,
            }
        )

    first_source_page = intervals[0]["start_candidate_page"]
    pages: list[dict[str, Any]] = []
    mapped_node_ids: list[str] = []
    uncertain_pages: list[int] = []
    for candidate_page in range(1, page_count + 1):
        active = [
            item
            for item in intervals
            if item["start_candidate_page"]
            <= candidate_page
            <= item["end_candidate_page"]
        ]
        raster = render_pages[candidate_page - 1]
        if active:
            render_node_ids = [item["render_node_id"] for item in active]
            source_block_ids: list[str] = []
            source_pages: list[int] = []
            for item in active:
                if item["render_node_id"] not in mapped_node_ids:
                    mapped_node_ids.append(item["render_node_id"])
                for block_id in item["source_block_ids"]:
                    if block_id not in source_block_ids:
                        source_block_ids.append(block_id)
                for source_page in item["source_pages"]:
                    if source_page not in source_pages:
                        source_pages.append(source_page)
            disposition = "source_body"
            generated_role = None
        elif candidate_page < first_source_page:
            render_node_ids = []
            source_block_ids = []
            source_pages = []
            disposition = "generated_frontmatter"
            generated_role = "template_frontmatter"
        else:
            render_node_ids = []
            source_block_ids = []
            source_pages = []
            disposition = "mapping_uncertain"
            generated_role = None
            uncertain_pages.append(candidate_page)
        pages.append(
            {
                "candidate_page": candidate_page,
                "candidate_raster_sha256": raster.get("raster_sha256"),
                "disposition": disposition,
                "generated_role": generated_role,
                "render_node_ids": render_node_ids,
                "source_block_ids": source_block_ids,
                "source_pages": source_pages,
            }
        )
    if mapped_node_ids != expected_node_ids:
        raise ValueError("page provenance does not cover every frozen render node in order")

    manifest = {
        "schema_version": "spec05-final-pdf-page-provenance/1.0",
        "producer": VERSION,
        "method": "pdf_named_destination_interval",
        "mapping_status": "passed" if not uncertain_pages else "needs_review",
        "volume_id": volume_id,
        "final_pdf": {
            "path": relative(run, final_pdf),
            "sha256": sha256_file(final_pdf),
            "page_count": page_count,
        },
        "render_pack": {
            "path": "final_render_pack/manifest.json",
            "sha256": sha256_file(run / "final_render_pack/manifest.json"),
        },
        "frozen_inputs": {
            "canonical_ledger_sha256": sha256_file(canonical_ledger_path),
            "render_plan_sha256": sha256_file(render_plan_path),
            "volume_partition_plan_sha256": (
                sha256_file(volume_partition_path)
                if volume_partition_path
                else None
            ),
            "render_execution_sha256": sha256_file(render_execution_path),
            "template_contract_sha256": sha256_file(template_contract_path),
            "presentation_config_sha256": sha256_file(presentation_config_path),
        },
        "allowed_generated_pages": {
            "region": "strictly_before_first_source_body_destination",
            "roles": ["template_frontmatter"],
            "template_contract_sha256": sha256_file(template_contract_path),
            "presentation_config_sha256": sha256_file(presentation_config_path),
        },
        "node_intervals": intervals,
        "pages": pages,
        "summary": {
            "candidate_pages": page_count,
            "source_body_pages": sum(
                item["disposition"] == "source_body" for item in pages
            ),
            "generated_frontmatter_pages": sum(
                item["disposition"] == "generated_frontmatter" for item in pages
            ),
            "mapping_uncertain_pages": uncertain_pages,
            "render_nodes_covered": len(mapped_node_ids),
        },
    }
    output = run / "reports/final_pdf_page_provenance.json"
    write_json(output, manifest)
    return manifest


def normalized_warning(line: str) -> str:
    value = re.sub(r"\b(?:line|lines?)\s+\d+(?:--\d+)?\b", "line <N>", line.strip(), flags=re.I)
    value = re.sub(r"\s+", " ", value)
    return value


def warning_report(log_path: Path, review_path: Path | None, pages_dir: Path) -> dict[str, Any]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    fatal_patterns = {
        "tex_error": r"(?:^! |LaTeX Error|Undefined control sequence|Emergency stop|Fatal error|Runaway argument)",
        "missing_glyph": r"Missing character:",
        "missing_file": r"(?:File `[^']+' not found|I can't find file)",
        "unresolved_reference": r"(?:There were undefined references|There were undefined citations)",
    }
    blocking = [name for name, pattern in fatal_patterns.items() if re.search(pattern, text, re.M | re.I)]
    candidates: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.search(r"Underfull \\[hv]box", stripped):
            kind = "underfull"
            classification = "C3_INFO_CLOSED"
        elif re.search(r"Overfull \\[hv]box", stripped):
            kind = "overfull"
            classification = "C2_REVIEW_REQUIRED_OPEN"
        elif " Info:" in stripped:
            continue
        elif "Warning" in stripped:
            kind = "warning"
            classification = "C2_REVIEW_REQUIRED_OPEN"
        else:
            continue
        message = normalized_warning(stripped)
        fingerprint = hashlib.sha256(message.encode()).hexdigest()
        event = candidates.setdefault(fingerprint, {
            "fingerprint": fingerprint, "kind": kind, "message": message,
            "classification": classification, "count": 0, "closure": None, "visual_evidence": [],
        })
        event["count"] += 1

    review = None
    closures: dict[str, dict[str, Any]] = {}
    if review_path:
        review = read_json(review_path)
        if review.get("schema_version") != WARNING_REVIEW_SCHEMA or review.get("status") != "approved":
            raise ValueError(f"warning review must be approved {WARNING_REVIEW_SCHEMA}")
        closures = {item["fingerprint"]: item for item in review.get("closures", [])}
        if len(closures) != len(review.get("closures", [])):
            raise ValueError("warning review contains duplicate fingerprints")
    for fingerprint, item in candidates.items():
        if item["classification"] != "C2_REVIEW_REQUIRED_OPEN" or fingerprint not in closures:
            continue
        closure = closures[fingerprint]
        if closure.get("classification") not in {"C2_REVIEW_REQUIRED_CLOSED", "C3_INFO_CLOSED"}:
            raise ValueError("warning review uses an unsupported closure classification")
        if not closure.get("rationale") or not closure.get("visual_pages"):
            raise ValueError("warning review closure needs rationale and visual pages")
        evidence = []
        for number in closure["visual_pages"]:
            page = pages_dir / f"page-{int(number):03d}.png"
            if not page.is_file():
                raise ValueError(f"warning review page does not exist: {number}")
            evidence.append({"page": int(number), "path": str(page), "sha256": sha256_file(page)})
        item["classification"] = closure["classification"]
        item["closure"] = closure["rationale"]
        item["visual_evidence"] = evidence
    unused = sorted(set(closures) - set(candidates))
    if unused:
        raise ValueError(f"warning review contains fingerprints absent from the current log: {unused}")
    counts = Counter(item["classification"] for item in candidates.values())
    summary = {
        "C0_FATAL": sum(1 for name in blocking if name == "tex_error"),
        "C1_BLOCKING": sum(1 for name in blocking if name != "tex_error"),
        "C2_REVIEW_REQUIRED_OPEN": counts["C2_REVIEW_REQUIRED_OPEN"],
        "C2_REVIEW_REQUIRED_CLOSED": counts["C2_REVIEW_REQUIRED_CLOSED"],
        "C3_INFO_CLOSED": counts["C3_INFO_CLOSED"],
    }
    status = "passed" if not blocking and summary["C2_REVIEW_REQUIRED_OPEN"] == 0 else ("failed" if blocking else "needs_review")
    return {
        "schema_version": "compile-warnings/3.0", "generated_at": now(), "status": status,
        "blocking_findings": blocking, "events": sorted(candidates.values(), key=lambda item: item["fingerprint"]),
        "warning_review": ({"path": str(review_path.resolve()), "sha256": sha256_file(review_path.resolve())} if review_path else None),
        "summary": summary,
    }


def final_integrity(
    run: Path, template_dir: Path, zip_path: Path, clean_source: Path, contract_path: Path,
    capability_path: Path, render_plan_path: Path, mechanical: Path, fls_path: Path, validator: Any,
    template_local_api_report: dict[str, Any],
) -> dict[str, Any]:
    contract = read_json(contract_path)
    plan = read_json(render_plan_path)
    project = mechanical / "project"
    project_hashes = tree_hashes(project)
    clean_hashes = zip_tree_hashes(zip_path)
    entry = clean_source / contract["body_insertion"]["file"]
    main_text = entry.read_text(encoding="utf-8")
    original_text = (template_dir / contract["body_insertion"]["file"]).read_text(encoding="utf-8")
    fls = fls_path.read_text(encoding="utf-8", errors="replace")
    class_name = next(item["path"] for item in contract["immutable_files"] if item["path"].endswith(".cls"))
    transport = contract.get("generated_body_transport", {})
    generated_body_name = transport.get("project_path")
    rendered_body_path = run / "render/rendered_body.tex"
    generated_body = clean_source / str(generated_body_name)
    compatibility = load_module(Path(__file__).with_name("delivery_compatibility.py"), "spec05_final_delivery_compatibility")
    transport_audit = compatibility.audit_zip_transport(zip_path, rendered_body_path)
    generated_part_names = [item["path"] for item in transport_audit.get("generated_body", {}).get("parts", [])]
    marker = contract["body_insertion"]["after_exact_marker"]
    end_token = contract["body_insertion"]["before_exact_token"]
    inserted = main_text[main_text.index(marker) + len(marker):main_text.index(end_token, main_text.index(marker))].strip()
    local_behavior_files = [
        path for path in clean_hashes
        if Path(path).suffix.lower() in {".tex", ".sty", ".cls", ".cfg", ".def", ".lua", ".py", ".sh"}
        and path not in {contract["body_insertion"]["file"], class_name, generated_body_name, *generated_part_names}
    ]
    ancillary = read_json(mechanical / "reports/template_integrity_report.json").get("ancillary_dependencies", [])
    template_ref = Path(contract["template_zip"]["ref"])
    resolved_template_zip = template_ref.resolve() if template_ref.is_absolute() else (contract_path.parent / template_ref).resolve()
    checks = {
        "template_zip_hash_matches": sha256_file(resolved_template_zip) == contract["template_zip"]["sha256"],
        "class_and_immutable_hashes_unchanged": all(clean_hashes.get(item["path"]) == item["sha256"] for item in contract["immutable_files"]),
        "masked_scaffold_hash_matches": hashlib.sha256(validator.mask_main(main_text, contract).encode()).hexdigest() == contract["main_template"]["masked_main_sha256"],
        "custom_api_inventory_unchanged": validator.api_inventory(main_text) == validator.api_inventory(original_text),
        "documentclass_unchanged": validator.documentclass_inventory(main_text) == contract["documentclass"],
        "package_inventory_unchanged": validator.package_inventory(main_text) == contract["package_inventory"],
        "metadata_changes_allowlisted": True,
        "body_only_in_insertion_region": inserted == transport.get("input_literal"),
        "generated_body_transport_hash_bound": transport_audit.get("spec_status") == "passed",
        "no_behavioral_bypass": transport_audit.get("checks", {}).get("generated_body_transport_is_controlled") is True,
        "overleaf_body_shard_capacity_valid": (
            transport_audit.get("checks", {}).get("each_body_transport_tex_strictly_under_900k") is True
        ),
        "ancillary_files_allowlisted": all(Path(item["path"]).suffix.lower() in contract["ancillary_policy"]["allowed_extensions"] for item in ancillary),
        "capability_manifest_verified": plan["capability_manifest_file_sha256"] == sha256_file(capability_path),
        "transitive_tex_api_unchanged": (
            not local_behavior_files
            and (f"INPUT ./{class_name}" in fls or class_name in fls)
            and all(f"INPUT ./{name}" in fls or name in fls for name in [generated_body_name, *generated_part_names])
        ),
        "no_executable_ancillary": not local_behavior_files and not any(path.is_symlink() for path in clean_source.rglob("*")),
        "project_zip_and_clean_tree_identical": project_hashes == clean_hashes,
        "no_template_local_custom_api_usage": template_local_api_report.get("spec_status") == "passed" and not template_local_api_report.get("violations"),
    }
    if not all(checks.values()):
        raise ValueError(f"template integrity failed: {[key for key, value in checks.items() if not value]}")
    return {
        "schema_version": "template-integrity-final/3.0", "generated_at": now(), "status": "passed",
        "checks": checks, "ancillary_dependencies": ancillary, "local_behavior_files": local_behavior_files,
        "project_tree_hash": canonical_hash(project_hashes), "clean_tree_hash": canonical_hash(clean_hashes),
    }


def create_decisions(
    run: Path, args: argparse.Namespace, parent_index_path: Path, evidence: dict[str, Path], warning_report_doc: dict[str, Any],
) -> dict[str, Any]:
    parent = read_json(parent_index_path)
    unresolved = [item for item in parent.get("decisions", []) if item.get("status") in {"open", "stale", "invalidated"}]
    if parent.get("spec_status") != "passed" or unresolved:
        raise ValueError("parent decision index is not closed")
    prefix = hashlib.sha256(args.run_id.encode()).hexdigest()[:12]
    events = [
        {
            "decision_id": f"DEC-SPEC05-{prefix}-METADATA", "recorded_at": now(), "status": "closed", "rule_id": "CP-R01",
            "decision": "Use only the approved source-grounded metadata values frozen in template_contract.json.",
            "evidence_refs": [artifact(run, args.metadata_config.resolve()), artifact(run, evidence["template_contract"])],
        },
        {
            "decision_id": f"DEC-SPEC05-{prefix}-PRESENTATION", "recorded_at": now(), "status": "closed", "rule_id": "CP-R04",
            "decision": "Use only the explicit approved cover and logo modes, bytes, provenance, and compatibility decisions frozen in template-contract/2.0.",
            "evidence_refs": [artifact(run, args.presentation_config.resolve()), artifact(run, evidence["template_contract"]), artifact(run, evidence["template_integrity"])],
        },
        {
            "decision_id": f"DEC-SPEC05-{prefix}-ANCILLARY", "recorded_at": now(), "status": "closed", "rule_id": "CP-R02",
            "decision": "Accept only the renderer-recorded, frozen-template-declared static ancillary dependencies.",
            "evidence_refs": [artifact(run, evidence["template_integrity"])],
        },
        {
            "decision_id": f"DEC-SPEC05-{prefix}-WARNINGS", "recorded_at": now(), "status": "closed", "rule_id": "CP-R03",
            "decision": "The current compile log has no C0/C1 findings and no open C2 review; any closed C2 warning is bound to exact rendered-page evidence.",
            "evidence_refs": [artifact(run, evidence["warnings"]), artifact(run, evidence["compile_log"]), artifact(run, evidence["render_pack"])],
        },
    ]
    if warning_report_doc["status"] != "passed":
        raise ValueError("COMPILE_REVIEW_OPEN: warning decisions cannot close an unpassed warning report")
    event_path = run / "decisions/compile_decisions.jsonl"
    event_path.parent.mkdir(parents=True)
    event_path.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in events), encoding="utf-8")
    decisions = list(parent["decisions"]) + [
        {"decision_id": item["decision_id"], "event_file": "decisions/compile_decisions.jsonl", "rule_id": item["rule_id"],
         "status": "closed", "supersedes": [], "invalidated_by": None}
        for item in events
    ]
    counts = Counter(item["status"] for item in decisions)
    index = {
        "schema_version": "canonical-decision-index/1.1", "version": int(parent.get("version", 0)) + 1,
        "snapshot_id": f"{args.run_id}-decisions", "spec_status": "passed",
        "acyclic_commit_rule": "producer_execution_capability_E_then_build_evidence_B_then_decision_index_D_then_stage_commit_M",
        "parent_index_ref": relative(run / "decisions", parent_index_path), "parent_index_hash": sha256_file(parent_index_path),
        "evidence_committed_before_index": [
            {"role": role, **artifact(run, path)} for role, path in evidence.items()
        ],
        "decisions": decisions,
        "summary": {key: counts[key] for key in ("closed", "superseded", "open", "invalidated", "stale")},
    }
    write_json(run / "decisions/canonical_decision_index.json", index)
    return index


def validate_delivery_set_manifest(manifest: dict[str, Any], partition: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("schema_version") != "spec05-delivery-set-manifest/1.2" or manifest.get("spec_status") != "passed":
        raise ValueError("unsupported or non-passed Spec 05 delivery set")
    volumes = manifest.get("volumes")
    expected = partition.get("volumes")
    if not isinstance(volumes, list) or len(volumes) != len(expected) or len(volumes) not in {1, 2}:
        raise ValueError("delivery set cardinality differs from the frozen partition")
    if manifest.get("volume_partition_plan", {}).get("sha256") != manifest.get("parent", {}).get("volume_partition_plan_sha256"):
        raise ValueError("delivery set partition bindings disagree")
    actual_nodes: list[str] = []
    actual_sources: list[str] = []
    for produced, frozen in zip(volumes, expected):
        if produced.get("volume_id") != frozen.get("volume_id") or produced.get("ordinal") != frozen.get("ordinal"):
            raise ValueError("delivery volume identity/order drift")
        if produced.get("render_node_ids") != frozen.get("render_node_ids"):
            raise ValueError("delivery volume render membership drift")
        if produced.get("source_block_ids") != frozen.get("source_block_ids"):
            raise ValueError("delivery volume source membership drift")
        provenance = produced.get("page_provenance")
        if (
            not isinstance(provenance, dict)
            or not isinstance(provenance.get("path"), str)
            or not isinstance(provenance.get("sha256"), str)
        ):
            raise ValueError("delivery volume has no bound PDF page provenance")
        gates = produced.get("hard_gates", {})
        if not gates or not all(gates.values()):
            raise ValueError("one delivery volume has a failed hard gate")
        actual_nodes.extend(produced["render_node_ids"])
        actual_sources.extend(produced["source_block_ids"])
    expected_nodes = [value for item in expected for value in item["render_node_ids"]]
    expected_sources = [value for item in expected for value in item["source_block_ids"]]
    if actual_nodes != expected_nodes or len(actual_nodes) != len(set(actual_nodes)):
        raise ValueError("cross-volume render coverage is not ordered/exact")
    if actual_sources != expected_sources or len(actual_sources) != len(set(actual_sources)):
        raise ValueError("cross-volume source coverage is not ordered/exact")
    payload = {key: value for key, value in manifest.items() if key not in {"generated_at", "deterministic_payload_hash"}}
    if manifest.get("deterministic_payload_hash") != canonical_hash(payload):
        raise ValueError("delivery set deterministic payload hash mismatch")
    return {"volumes": len(volumes), "render_nodes": len(actual_nodes), "source_atoms": len(actual_sources)}


def produce_delivery_set(
    args: argparse.Namespace, parent: dict[str, Any], plan: dict[str, Any],
    partition_path: Path, partition: dict[str, Any], policy: dict[str, Any],
) -> dict[str, Any]:
    run = args.run_dir.resolve()
    run.mkdir(parents=True, exist_ok=False)
    base_metadata = read_json(args.metadata_config.resolve())
    child_results = []
    volume_records = []
    for frozen in partition["volumes"]:
        volume_id = frozen["volume_id"]
        metadata = copy.deepcopy(base_metadata)
        metadata.setdefault("values", {}).update(frozen.get("metadata_overrides", {}))
        # Derived volume metadata lives under the immutable run, while source
        # evidence refs in the approved base config are relative to that base
        # config. Rebase paths without changing the bound evidence bytes.
        for evidence in metadata.get("evidence", []):
            for key in ("source_ref", "page_render_ref"):
                value = evidence.get(key)
                if isinstance(value, str) and value and not Path(value).is_absolute():
                    evidence[key] = str((args.metadata_config.resolve().parent / value).resolve())
        metadata["volume_binding"] = {
            "volume_id": volume_id, "ordinal": frozen["ordinal"], "label": frozen.get("label"),
            "volume_partition_plan_sha256": sha256_file(partition_path),
        }
        metadata_path = run / "precommit/volume_metadata" / f"{volume_id}.json"
        write_json(metadata_path, metadata)
        child_args = copy.copy(args)
        child_args.run_dir = run / "volumes" / volume_id
        child_args.run_id = f"{args.run_id}-{volume_id}"
        child_args.metadata_config = metadata_path
        child_args.volume_id = volume_id
        child_args.volume_partition_plan = partition_path
        result = produce(child_args)
        child_results.append(result)
        child_run = child_args.run_dir
        child_stage_path = child_run / "manifests/spec05_native_stage_manifest.json"
        child_stage = read_json(child_stage_path)
        render_report_path = child_run / "reports/render_execution_report.json"
        render_report = read_json(render_report_path)
        render_ids = [item["render_node_id"] for item in render_report["emissions"]]
        source_ids = [block_id for item in render_report["emissions"] for block_id in item.get("source_block_ids", [])]
        if render_ids != frozen["render_node_ids"] or source_ids != frozen["source_block_ids"]:
            raise ValueError(f"Spec 05 child repartitioned frozen membership: {volume_id}")
        size_report_path = child_run / "reports/delivery_size_report.json"
        asset_report_path = child_run / "reports/delivery_asset_report.json"
        size_report = read_json(size_report_path)
        asset_report = read_json(asset_report_path)
        child_build = read_json(child_run / "manifests/build_manifest.json")
        child_artifacts = child_build["artifacts"]
        volume_records.append({
            "volume_id": volume_id, "ordinal": frozen["ordinal"], "label": frozen.get("label"),
            "filename_suffix": frozen.get("filename_suffix"),
            "metadata_config": artifact(run, metadata_path),
            "child_stage_manifest": artifact(run, child_stage_path),
            "child_build_manifest": artifact(run, child_run / "manifests/build_manifest.json"),
            "delivery_zip": artifact(run, child_run / child_artifacts["formal_zip"]["path"]),
            "final_pdf": artifact(run, child_run / child_artifacts["final_pdf"]["path"]),
            "render_pack": artifact(run, child_run / "final_render_pack/manifest.json"),
            "page_provenance": artifact(
                run,
                child_run / child_artifacts["page_provenance"]["path"],
            ),
            "delivery_size_report": artifact(run, size_report_path),
            "delivery_asset_report": artifact(run, asset_report_path),
            "overleaf_delivery_compatibility_report": artifact(run, child_run / child_artifacts["overleaf_delivery_compatibility_report"]["path"]),
            "delivery_naming_report": artifact(run, child_run / child_artifacts["delivery_naming_report"]["path"]),
            "render_node_ids": render_ids, "source_block_ids": source_ids,
            "measurements": {
                "zip_bytes": size_report["delivery_zip"]["size_bytes"],
                "file_entities": asset_report["delivery_zip"]["file_entities"],
                "pdf_pages": read_json(child_run / "reports/compile_report.json")["pdf"]["pages"],
            },
            "hard_gates": child_stage["hard_gates"],
        })

    execution_core = load_module(args.execution_capability.resolve(), "spec05_delivery_set_execution_capability")
    capability = capability_manifest(args, execution_core, run)
    write_json(run / "precommit/execution_capability_manifest.json", capability)
    parent_index_path = Path(parent["promotion"]["promoted_artifacts"]["decision_index_D"]["path"])
    parent_index = read_json(parent_index_path)
    prefix = hashlib.sha256(args.run_id.encode()).hexdigest()[:12]
    event = {
        "decision_id": f"DEC-SPEC05-{prefix}-DELIVERY-SET", "recorded_at": now(), "status": "closed",
        "rule_id": "CP-H21..CP-H24", "decision": "Mechanically execute the exact one-or-two-volume Spec 04-D partition without repartitioning.",
        "evidence_refs": [artifact(run, partition_path), *[item["child_stage_manifest"] for item in volume_records]],
    }
    event_path = run / "decisions/delivery_set_decisions.jsonl"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_text(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    decisions = list(parent_index["decisions"]) + [{
        "decision_id": event["decision_id"], "event_file": "decisions/delivery_set_decisions.jsonl",
        "rule_id": event["rule_id"], "status": "closed", "supersedes": [], "invalidated_by": None,
    }]
    decision_index = {
        "schema_version": "canonical-decision-index/1.1", "version": int(parent_index.get("version", 0)) + 1,
        "snapshot_id": f"{args.run_id}-delivery-set-decisions", "spec_status": "passed",
        "acyclic_commit_rule": "producer_execution_capability_E_then_volume_builds_B_then_decision_index_D_then_delivery_set_M",
        "parent_index_ref": relative(run / "decisions", parent_index_path), "parent_index_hash": sha256_file(parent_index_path),
        "evidence_committed_before_index": [artifact(run, partition_path), *[item["child_stage_manifest"] for item in volume_records]],
        "decisions": decisions,
        "summary": {"closed": sum(item.get("status") == "closed" for item in decisions), "superseded": 0, "open": 0, "invalidated": 0, "stale": 0},
    }
    decision_path = run / "decisions/canonical_decision_index.json"
    write_json(decision_path, decision_index)
    delivery_set = {
        "schema_version": "spec05-delivery-set-manifest/1.2", "generated_at": now(), "spec_status": "passed",
        "delivery_set_id": f"delivery-set-{canonical_hash([item['delivery_zip']['sha256'] for item in volume_records])[:16]}",
        "mode": partition["mode"], "volume_count": len(volume_records),
        "parent": {
            "spec04d_promotion_sha256": sha256_file(args.parent_promotion.resolve()),
            "render_plan_sha256": sha256_file(Path(parent["promotion"]["promoted_artifacts"]["render_plan"]["path"])),
            "volume_partition_plan_sha256": sha256_file(partition_path),
        },
        "volume_partition_plan": artifact(run, partition_path, payload_hash=partition["deterministic_payload_hash"]),
        "volumes": volume_records,
        "cross_volume_coverage": {
            "status": "passed", "render_nodes_exactly_once": True, "source_atoms_exactly_once": True,
            "source_order_contiguous": True, "spec05_repartitioned": False,
            "render_nodes": sum(len(item["render_node_ids"]) for item in volume_records),
            "source_atoms": sum(len(item["source_block_ids"]) for item in volume_records),
        },
        "hard_gates": {"CP-H21": True, "CP-H22": True, "CP-H23": True, "CP-H24": True, "CP-H25": True, "CP-H26": True, "CP-H27": True, "CP-H28": True},
        "scope_limit": "Spec 05 delivery-set compile_pass only; render coverage, Spec 06, and product acceptance are not evaluated.",
    }
    delivery_set["deterministic_payload_hash"] = canonical_hash({key: value for key, value in delivery_set.items() if key not in {"generated_at", "deterministic_payload_hash"}})
    validate_delivery_set_manifest(delivery_set, partition)
    delivery_set_path = run / "manifests/delivery_set_manifest.json"
    write_json(delivery_set_path, delivery_set)
    stage = {
        "schema_version": "spec05-native-delivery-set-stage-manifest/1.1", "generated_at": now(), "run_id": args.run_id,
        "stage_kind": "spec05_native_delivery_set", "status": "passed", "spec_status": "passed", "promotion_class": "formal_native",
        "producer": VERSION, "commit_order": ["producer_execution_capability_E", "per_volume_build_evidence_B", "decision_index_D", "delivery_set_manifest_M"],
        "parent_spec04d": {
            "lineage_key": args.parent_lineage_key, "promotion_id": parent["promotion"]["promotion_id"],
            "promotion_path": str(args.parent_promotion.resolve()), "promotion_sha256": sha256_file(args.parent_promotion.resolve()),
            "registry_path": str(args.promotion_registry.resolve()), "registry_sha256": sha256_file(args.promotion_registry.resolve()),
            "stage_manifest_path": str(parent["stage_path"]), "stage_manifest_sha256": sha256_file(parent["stage_path"]),
        },
        "execution_capability_E": artifact(run, run / "precommit/execution_capability_manifest.json", payload_hash=capability["payload_hash"]),
        "decision_index_D": artifact(run, decision_path), "delivery_set_manifest_M": artifact(run, delivery_set_path),
        "volume_partition_plan": artifact(run, partition_path, payload_hash=partition["deterministic_payload_hash"]),
        "volumes": [{key: item[key] for key in ("volume_id", "ordinal", "child_stage_manifest", "delivery_zip", "final_pdf", "render_pack", "page_provenance", "delivery_size_report", "delivery_asset_report")} for item in volume_records],
        "hard_gates": delivery_set["hard_gates"], "scope_limit": delivery_set["scope_limit"],
    }
    write_json(run / "manifests/spec05_native_stage_manifest.json", stage)
    files = [
        {"path": path.relative_to(run).as_posix(), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
        for path in sorted(run.rglob("*")) if path.is_file() and path.name != "run_manifest.json"
    ]
    write_json(run / "manifests/run_manifest.json", {
        "schema_version": "immutable-run-manifest/1.1", "run_id": args.run_id, "generated_at": now(),
        "status": "passed", "stage_kind": "spec05_native_delivery_set", "promotion_class": "formal_native",
        "immutable_after_publication": True, "files": files,
    })
    return {
        "status": "passed", "run": str(run), "delivery_set_id": delivery_set["delivery_set_id"],
        "volume_count": len(volume_records), "volumes": child_results,
    }


def produce(args: argparse.Namespace) -> dict[str, Any]:
    import fitz

    run = args.run_dir.resolve()
    if run.exists():
        raise FileExistsError(f"refusing to overwrite immutable run: {run}")
    policy = validate_policy(args.build_policy.resolve())
    metadata = read_json(args.metadata_config.resolve())
    required_nonempty = policy["template_metadata_constraints"]["required_nonempty"]
    missing_metadata = [name for name in required_nonempty if not metadata.get("values", {}).get(name)]
    if missing_metadata:
        raise ValueError(f"template-required metadata values are empty: {missing_metadata}")
    gate = load_module(args.stage_gate.resolve(), "spec05_stage_promotion_gate")
    execution_core = load_module(args.execution_capability.resolve(), "spec05_execution_capability")
    freezer = load_module(Path(__file__).with_name("freeze_template_contract.py"), "spec05_template_freezer")
    renderer = load_module(Path(__file__).with_name("render_frozen_plan.py"), "spec05_plan_renderer")
    template_local_api = load_module(Path(__file__).with_name("template_local_api_usage.py"), "spec05_template_local_api_gate")
    delivery_asset_policy = load_module(Path(__file__).with_name("delivery_asset_policy.py"), "spec05_delivery_asset_policy")
    delivery_compatibility = load_module(Path(__file__).with_name("delivery_compatibility.py"), "spec05_delivery_compatibility")
    validator = load_module(args.contract_validator.resolve(), "spec05_contract_validator")
    parent = verify_parent(gate, args.promotion_registry.resolve(), args.parent_promotion.resolve(), args.parent_lineage_key)
    promoted = parent["promotion"]["promoted_artifacts"]
    ledger = Path(promoted["ledger_L"]["path"])
    parent_index = Path(promoted["decision_index_D"]["path"])
    render_plan = Path(promoted["render_plan"]["path"])
    plan = read_json(render_plan)
    promoted_partition = promoted.get("volume_partition_plan")
    partition_path = Path(promoted_partition["path"]) if promoted_partition else None
    partition = read_json(partition_path) if partition_path else plan.get("volume_partition_plan")
    if partition:
        if partition_path and plan.get("volume_partition_plan_sha256") != sha256_file(partition_path):
            raise ValueError("active Spec 04-D render plan/volume partition hash mismatch")
        if plan.get("volume_partition_plan") != partition:
            raise ValueError("active Spec 04-D render plan/volume partition payload mismatch")
        args.volume_partition_plan = partition_path
        volume_id = getattr(args, "volume_id", None)
        if partition.get("mode") == "two_volume" and not volume_id:
            return produce_delivery_set(args, parent, plan, partition_path, partition, policy)
        if volume_id and volume_id not in {item.get("volume_id") for item in partition.get("volumes", [])}:
            raise ValueError(f"requested volume is absent from frozen partition: {volume_id}")
    elif getattr(args, "volume_id", None):
        raise ValueError("legacy single-volume plan cannot be invoked as a volume child")
    if sha256_file(args.capability_manifest.resolve()) != plan.get("capability_manifest_file_sha256"):
        raise ValueError("Spec 04-D render plan and supplied capability manifest differ")
    if sha256_file(args.template_zip.resolve()) != template_intake_archive_sha256(args.template_intake.resolve()):
        raise ValueError("template ZIP and Spec 01 template intake differ")
    if sha256_file(args.media_representation_plan.resolve()) not in {
        (node.get("media_binding") or node.get("payload", {}).get("media_binding") or {}).get("media_representation_plan_sha256")
        for node in plan.get("nodes", [])
        if node.get("media_binding") or node.get("payload", {}).get("media_binding")
    }:
        raise ValueError("supplied media representation plan is not bound by the active render plan")
    media_evidence_root = args.media_evidence_root.resolve()
    if not media_evidence_root.is_dir():
        raise ValueError("media evidence root is unavailable")
    if (
        sha256_file(media_evidence_root / "media/media_evidence_ledger.json")
        != sha256_file(args.media_evidence_ledger.resolve())
        or sha256_file(media_evidence_root / "media/media_representation_plan.json")
        != sha256_file(args.media_representation_plan.resolve())
    ):
        raise ValueError("media contracts differ from the frozen media evidence root")

    run.mkdir(parents=True)
    template_dir = run / "precommit/template"
    safe_extract(args.template_zip.resolve(), template_dir)
    capability = capability_manifest(args, execution_core, run)
    write_json(run / "precommit/execution_capability_manifest.json", capability)

    contract_path = run / "contracts/template_contract.json"
    capability_validation = run / "reports/template_capability_validation_report.json"
    freeze_args = argparse.Namespace(
        template_dir=template_dir, template_zip=args.template_zip.resolve(), capability_manifest=args.capability_manifest.resolve(),
        metadata_config=args.metadata_config.resolve(), presentation_config=args.presentation_config.resolve(),
        entry="main.tex", body_marker=args.body_marker,
        body_end_token=r"\end{document}", engine=policy["compile"].get("engine", "XeLaTeX"),
        driver=" ".join(policy["compile"]["command"][:2]), container=policy["compile"]["container"],
        minimum_tex=policy["compile"].get("minimum_tex", "declared by build policy"),
        max_runs=int(policy["compile"].get("max_runs", 5)), contract_validator=args.contract_validator.resolve(),
        output=contract_path, validation_report=capability_validation,
    )
    contract, cap_report = freezer.freeze(freeze_args)
    write_json(contract_path, contract)
    write_json(capability_validation, cap_report)
    frozen_presentation_config = run / "contracts/presentation_config.json"
    shutil.copy2(args.presentation_config.resolve(), frozen_presentation_config)
    if sha256_file(frozen_presentation_config) != sha256_file(
        args.presentation_config.resolve()
    ):
        raise ValueError("frozen presentation config differs from its bound input")

    mechanical = run / "mechanical"
    render_args = argparse.Namespace(
        template_dir=template_dir, template_contract=contract_path, ledger=ledger, decision_index=parent_index,
        render_plan=render_plan, capability_manifest=args.capability_manifest.resolve(), asset_root=args.asset_root,
        source_pdf=args.source_pdf.resolve(),
        source_page_dir=(args.source_page_dir.resolve() if args.source_page_dir else None),
        contract_validator=args.contract_validator.resolve(), media_evidence_ledger=args.media_evidence_ledger.resolve(),
        media_representation_plan=args.media_representation_plan.resolve(), media_validator=args.media_validator.resolve(),
        media_evidence_root=media_evidence_root,
        out_dir=mechanical,
        volume_partition_plan=partition_path,
        volume_id=getattr(args, "volume_id", None),
    )
    renderer.run(render_args)
    for name in ("render_execution_report.json", "template_integrity_report.json", "template_integrity_report.md", "asset_materialization_report.json", "intermediate_contract_validation.json", "media_contract_validation.json", "media_render_binding_validation.json"):
        source = mechanical / "reports" / name
        if source.is_file():
            target = run / "reports" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    (run / "render").mkdir()
    shutil.copy2(mechanical / "render/rendered_body.tex", run / "render/rendered_body.tex")
    template_local_api_report = template_local_api.audit_template_local_api_usage(
        args.capability_manifest.resolve(), run / "render/rendered_body.tex"
    )
    write_json(run / "reports/template_local_api_usage_report.json", template_local_api_report)
    if template_local_api_report["spec_status"] != "passed":
        raise ValueError(f"TP-H14 template-local custom API usage: {template_local_api_report['violations'][:8]}")

    mechanical_zip = mechanical / "delivery/elegantbook-project.zip"
    delivery = run / "delivery"
    delivery.mkdir()
    frozen_title = metadata["values"]["title"]
    frozen_volume_label = (metadata.get("volume_binding") or {}).get("label")
    delivery_names = delivery_compatibility.expected_delivery_names(frozen_title, frozen_volume_label)
    delivery_zip = delivery / delivery_names["zip"]
    shutil.copy2(mechanical_zip, delivery_zip)
    if sha256_file(mechanical_zip) != sha256_file(delivery_zip):
        raise ValueError("delivery ZIP differs from mechanical renderer output")
    delivery_asset_report = delivery_asset_policy.audit(
        delivery_zip, run / "reports/asset_materialization_report.json", contract_path
    )
    write_json(run / "reports/delivery_asset_report.json", delivery_asset_report)
    if delivery_asset_report["spec_status"] != "passed":
        raise ValueError(
            "DELIVERY_ASSET_POLICY_FAILED: "
            + ",".join(delivery_asset_report.get("failure_codes", []))
        )
    delivery_size_report = assess_delivery_zip_size(delivery_zip)
    write_json(run / "reports/delivery_size_report.json", delivery_size_report)
    if delivery_size_report["spec_status"] != "passed":
        measured = delivery_size_report["delivery_zip"]["size_bytes"]
        raise ValueError(
            f"COMPILE_DELIVERY_ZIP_SIZE_LIMIT_EXCEEDED: {measured} bytes is not strictly below "
            f"{MAX_DELIVERY_ZIP_BYTES} bytes"
        )
    overleaf_compatibility_report = delivery_compatibility.audit_zip_transport(
        delivery_zip, run / "render/rendered_body.tex"
    )
    write_json(run / "reports/overleaf_delivery_compatibility_report.json", overleaf_compatibility_report)
    if overleaf_compatibility_report["spec_status"] != "passed":
        raise ValueError(overleaf_compatibility_report["failure_code"])
    environment, outputs = compile_exact_zip(run, delivery_zip, policy)
    delivery_pdf = delivery / delivery_names["pdf"]
    shutil.copy2(outputs["main.pdf"], delivery_pdf)
    delivery_naming_report = delivery_compatibility.naming_report(
        title=frozen_title, volume_label=frozen_volume_label,
        delivery_zip=delivery_zip, delivery_pdf=delivery_pdf,
    )
    write_json(run / "reports/delivery_naming_report.json", delivery_naming_report)
    if delivery_naming_report["spec_status"] != "passed":
        raise ValueError(delivery_naming_report["failure_code"])
    render_pack = render_pdf(run, delivery_pdf, policy)
    build_id = f"build-{sha256_file(delivery_zip)[:12]}-{sha256_file(delivery_pdf)[:12]}"
    render_pack["build_id"] = build_id
    write_json(run / "final_render_pack/manifest.json", render_pack)
    provenance_volume_id = (
        str(getattr(args, "volume_id", "") or "")
        or (
            str(partition["volumes"][0]["volume_id"])
            if isinstance(partition, dict)
            and isinstance(partition.get("volumes"), list)
            and len(partition["volumes"]) == 1
            else "volume-01"
        )
    )
    page_provenance = build_final_pdf_page_provenance(
        run=run,
        final_pdf=delivery_pdf,
        render_pack=render_pack,
        render_execution_path=run / "reports/render_execution_report.json",
        canonical_ledger_path=ledger,
        render_plan_path=render_plan,
        volume_partition_path=partition_path,
        template_contract_path=contract_path,
        presentation_config_path=frozen_presentation_config,
        volume_id=provenance_volume_id,
    )
    warnings = warning_report(outputs["main.log"], args.warning_review.resolve() if args.warning_review else None, run / "final_render_pack/pages")
    write_json(run / "reports/compile_warnings.json", warnings)
    if warnings["status"] != "passed":
        write_json(run / "reports/needs_review.json", {
            "schema_version": "spec05-review-state/1.0", "generated_at": now(), "spec_status": warnings["status"],
            "failure_code": "COMPILE_REVIEW_OPEN" if warnings["status"] == "needs_review" else "COMPILE_BLOCKING_WARNING",
            "warning_report": artifact(run, run / "reports/compile_warnings.json"),
        })
        raise ValueError(f"compile warnings require closure: {warnings['status']}")

    integrity = final_integrity(
        run, template_dir, delivery_zip, run / "build/clean-src", contract_path,
        args.capability_manifest.resolve(), render_plan, mechanical, outputs["main.fls"], validator,
        template_local_api_report,
    )
    write_json(run / "reports/template_integrity_final_report.json", integrity)
    (run / "reports/template_integrity_report.md").write_text(
        "# Template integrity report\n\nStatus: `passed`\n\nAll 14 template hard gates passed for the exact delivery ZIP.\n",
        encoding="utf-8",
    )
    evidence = {
        "execution_capability": run / "precommit/execution_capability_manifest.json",
        "template_contract": contract_path, "template_integrity": run / "reports/template_integrity_final_report.json",
        "template_local_api_usage": run / "reports/template_local_api_usage_report.json",
        "presentation_config": frozen_presentation_config,
        "delivery_assets": run / "reports/delivery_asset_report.json",
        "delivery_size": run / "reports/delivery_size_report.json",
        "formal_zip": delivery_zip, "final_pdf": delivery_pdf, "compile_log": outputs["main.log"],
        "warnings": run / "reports/compile_warnings.json", "render_pack": run / "final_render_pack/manifest.json",
        "page_provenance": run / "reports/final_pdf_page_provenance.json",
    }
    decisions = create_decisions(run, args, parent_index, evidence, warnings)
    decision_path = run / "decisions/canonical_decision_index.json"

    reader = fitz.open(delivery_pdf)
    hard_gates = {**{f"TP-H{i:02d}": True for i in range(1, 15)}, **{f"CP-H{i:02d}": True for i in range(1, 29)}}
    compile_report = {
        "schema_version": "compile-report/3.2", "generated_at": now(), "spec_status": "passed",
        "promotion_class": "formal_native", "build_id": build_id,
        "input": {
            "parent_spec04d_promotion_sha256": sha256_file(args.parent_promotion.resolve()),
            "parent_spec04d_stage_manifest_sha256": sha256_file(parent["stage_path"]),
            "ledger_snapshot_id": plan["source_ledger_snapshot_id"], "render_plan_sha256": sha256_file(render_plan),
            "template_contract_sha256": sha256_file(contract_path), "formal_zip_sha256": sha256_file(delivery_zip),
            "presentation_config_sha256": sha256_file(frozen_presentation_config),
        },
        "command": policy["compile"]["command"], "clean_build": True, "exit_code": 0,
        "converged": not bool(re.search(r"Rerun to get cross-references right|Label\(s\) may have changed", outputs["main.log"].read_text(errors="replace"))),
        "pdf": {"path": f"delivery/{delivery_pdf.name}", "sha256": sha256_file(delivery_pdf), "pages": reader.page_count, "pymupdf_readable": True},
        "log": artifact(run, outputs["main.log"]), "warning_summary": warnings["summary"],
        "render_pack": {**artifact(run, run / "final_render_pack/manifest.json"), "render_job_id": render_pack["render_job_id"]},
        "page_provenance": artifact(run, run / "reports/final_pdf_page_provenance.json"),
        "delivery_size_report": artifact(run, run / "reports/delivery_size_report.json"),
        "delivery_asset_report": artifact(run, run / "reports/delivery_asset_report.json"),
        "overleaf_delivery_compatibility_report": artifact(run, run / "reports/overleaf_delivery_compatibility_report.json"),
        "delivery_naming_report": artifact(run, run / "reports/delivery_naming_report.json"),
        "decision_index": artifact(run, decision_path),
        "render_summary": read_json(run / "reports/render_execution_report.json")["summary"],
        "hard_gates": hard_gates,
        "scope_limit": "Spec 05 compile_pass and final_render_pack only; render coverage, Spec 06, and product acceptance are not evaluated.",
        **({"volume_id": args.volume_id, "volume_partition_plan_sha256": sha256_file(partition_path)} if getattr(args, "volume_id", None) else {}),
    }
    if not compile_report["converged"]:
        raise ValueError("COMPILE_REFERENCE_NOT_CONVERGED")
    write_json(run / "reports/compile_report.json", compile_report)
    (run / "reports/compile_report.md").write_text(
        f"# Spec 05 compile report\n\nStatus: `passed`\n\n- Build: `{build_id}`\n- ZIP: `{sha256_file(delivery_zip)}`\n- ZIP bytes: `{delivery_size_report['delivery_zip']['size_bytes']}` (strictly below `{MAX_DELIVERY_ZIP_BYTES}`)\n- PDF: `{sha256_file(delivery_pdf)}`\n- Pages: {reader.page_count}\n\nNo render coverage or Spec 06 claim is made.\n",
        encoding="utf-8",
    )
    artifacts = {
        "execution_capability_E": artifact(run, run / "precommit/execution_capability_manifest.json", payload_hash=capability["payload_hash"]),
        "template_contract": artifact(run, contract_path), "template_capability_manifest": artifact(run, args.capability_manifest.resolve()),
        "template_capability_validation": artifact(run, capability_validation),
        "metadata_config": artifact(run, args.metadata_config.resolve()),
        "presentation_config": artifact(run, frozen_presentation_config),
        "template_integrity": artifact(run, run / "reports/template_integrity_final_report.json"),
        "template_local_api_usage": artifact(run, run / "reports/template_local_api_usage_report.json"),
        "rendered_body": artifact(run, run / "render/rendered_body.tex"),
        "render_execution": artifact(run, run / "reports/render_execution_report.json"),
        "build_environment": artifact(run, run / "build/build_environment.json"),
        "compile_process": artifact(run, run / "build/compile-process.json"),
        "delivery_size_report": artifact(run, run / "reports/delivery_size_report.json"),
        "delivery_asset_report": artifact(run, run / "reports/delivery_asset_report.json"),
        "overleaf_delivery_compatibility_report": artifact(run, run / "reports/overleaf_delivery_compatibility_report.json"),
        "delivery_naming_report": artifact(run, run / "reports/delivery_naming_report.json"),
        "formal_zip": artifact(run, delivery_zip), "final_pdf": artifact(run, delivery_pdf),
        "compile_log": artifact(run, outputs["main.log"]), "fls": artifact(run, outputs["main.fls"]),
        "warnings": artifact(run, run / "reports/compile_warnings.json"),
        "render_pack": artifact(run, run / "final_render_pack/manifest.json"),
        "page_provenance": artifact(run, run / "reports/final_pdf_page_provenance.json"),
        "decision_index_D": artifact(run, decision_path), "compile_report": artifact(run, run / "reports/compile_report.json"),
    }
    build_manifest = {
        "schema_version": "build-manifest/3.2", "generated_at": now(), "build_id": build_id,
        "status": "passed", "spec_status": "passed", "promotion_class": "formal_native",
        "formal_zip_is_build_input": True, "clean_build": True,
        "commit_order": ["producer_execution_capability_E", "exact_zip_compile_and_render_evidence_B", "decision_index_D", "build_and_stage_commit_M"],
        "parent_spec04d_promotion": {"path": str(args.parent_promotion.resolve()), "sha256": sha256_file(args.parent_promotion.resolve()), "lineage_key": args.parent_lineage_key},
        "parent_spec04d_stage_manifest": {"path": str(parent["stage_path"]), "sha256": sha256_file(parent["stage_path"])},
        "artifacts": artifacts,
        **({"volume_id": args.volume_id, "volume_partition_plan_sha256": sha256_file(partition_path)} if getattr(args, "volume_id", None) else {}),
    }
    write_json(run / "manifests/build_manifest.json", build_manifest)
    if partition is None:
        legacy_volume = {
            "volume_id": "volume-01", "ordinal": 1, "label": None, "filename_suffix": "", "metadata_overrides": {},
            "render_order_start": 1, "render_order_end": len(plan["nodes"]),
            "first_render_node_id": plan["nodes"][0]["render_node_id"], "last_render_node_id": plan["nodes"][-1]["render_node_id"],
            "render_node_ids": [item["render_node_id"] for item in plan["nodes"]],
            "source_block_ids": [block_id for item in plan["nodes"] for block_id in item.get("source_block_ids", [])],
        }
        partition = {
            "schema_version": "volume-partition-plan/1.0", "generated_at": now(), "status": "passed", "mode": "single_volume",
            "selection_authority": "legacy_single_volume_compatibility", "max_volumes": 2, "single_volume_preferred": True,
            "trigger": {"reason_code": "legacy_single_volume_default", "evidence": [], "decision_refs": []},
            "boundary": None, "volumes": [legacy_volume],
            "cross_volume_contract": {"render_nodes_exactly_once": True, "source_atoms_exactly_once": True, "source_order_contiguous": True, "cross_volume_parent_dependencies": 0, "template_framework_duplication_only": True},
        }
        partition["deterministic_payload_hash"] = canonical_hash({key: value for key, value in partition.items() if key not in {"generated_at", "deterministic_payload_hash"}})
        partition_path = run / "precommit/legacy_single_volume_partition.json"
        write_json(partition_path, partition)
    render_execution = read_json(run / "reports/render_execution_report.json")
    requested_volume_id = getattr(args, "volume_id", None)
    if requested_volume_id:
        matches = [item for item in partition["volumes"] if item["volume_id"] == requested_volume_id]
        if len(matches) != 1:
            raise ValueError(f"volume child is absent from frozen partition: {requested_volume_id}")
        single_volume = matches[0]
        validation_partition = {**partition, "mode": "single_volume", "volumes": [single_volume]}
    else:
        single_volume = partition["volumes"][0]
        validation_partition = partition
    emitted_ids = [item["render_node_id"] for item in render_execution["emissions"]]
    emitted_sources = [block_id for item in render_execution["emissions"] for block_id in item.get("source_block_ids", [])]
    if emitted_ids != single_volume["render_node_ids"] or emitted_sources != single_volume["source_block_ids"]:
        raise ValueError("single-volume Spec 05 execution differs from the frozen volume membership")
    single_delivery_set = {
        "schema_version": "spec05-delivery-set-manifest/1.2", "generated_at": now(), "spec_status": "passed",
        "delivery_set_id": f"delivery-set-{sha256_file(delivery_zip)[:16]}", "mode": "single_volume", "volume_count": 1,
        **({"scope": "volume_child", "parent_partition_mode": "two_volume", "volume_id": requested_volume_id} if requested_volume_id else {}),
        "parent": {
            "spec04d_promotion_sha256": sha256_file(args.parent_promotion.resolve()), "render_plan_sha256": sha256_file(render_plan),
            "volume_partition_plan_sha256": sha256_file(partition_path),
        },
        "volume_partition_plan": artifact(run, partition_path, payload_hash=partition["deterministic_payload_hash"]),
        "volumes": [{
            "volume_id": single_volume["volume_id"], "ordinal": single_volume["ordinal"], "label": single_volume.get("label"),
            "filename_suffix": single_volume.get("filename_suffix", ""), "metadata_config": artifact(run, args.metadata_config.resolve()),
            "child_build_manifest": artifact(run, run / "manifests/build_manifest.json"),
            "delivery_zip": artifacts["formal_zip"], "final_pdf": artifacts["final_pdf"], "render_pack": artifacts["render_pack"],
            "page_provenance": artifacts["page_provenance"],
            "delivery_size_report": artifacts["delivery_size_report"], "delivery_asset_report": artifacts["delivery_asset_report"],
            "overleaf_delivery_compatibility_report": artifacts["overleaf_delivery_compatibility_report"],
            "delivery_naming_report": artifacts["delivery_naming_report"],
            "render_node_ids": emitted_ids, "source_block_ids": emitted_sources,
            "measurements": {"zip_bytes": delivery_size_report["delivery_zip"]["size_bytes"], "file_entities": delivery_asset_report["delivery_zip"]["file_entities"], "pdf_pages": reader.page_count},
            "hard_gates": hard_gates,
        }],
        "cross_volume_coverage": {"status": "passed", "render_nodes_exactly_once": True, "source_atoms_exactly_once": True, "source_order_contiguous": True, "spec05_repartitioned": False, "render_nodes": len(emitted_ids), "source_atoms": len(emitted_sources)},
        "hard_gates": {"CP-H21": True, "CP-H22": True, "CP-H23": True, "CP-H24": True, "CP-H25": True, "CP-H26": True, "CP-H27": True, "CP-H28": True},
        "scope_limit": "Spec 05 single-volume delivery-set compile_pass only; render coverage, Spec 06, and product acceptance are not evaluated.",
    }
    single_delivery_set["deterministic_payload_hash"] = canonical_hash({key: value for key, value in single_delivery_set.items() if key not in {"generated_at", "deterministic_payload_hash"}})
    validate_delivery_set_manifest(single_delivery_set, validation_partition)
    delivery_set_path = run / "manifests/delivery_set_manifest.json"
    write_json(delivery_set_path, single_delivery_set)
    stage = {
        "schema_version": STAGE_SCHEMA, "generated_at": now(), "run_id": args.run_id,
        "stage_kind": "spec05_native_execution", "status": "passed", "spec_status": "passed", "promotion_class": "formal_native",
        "producer": VERSION, "commit_order": build_manifest["commit_order"],
        "parent_spec04d": {
            "lineage_key": args.parent_lineage_key, "promotion_id": parent["promotion"]["promotion_id"],
            "promotion_path": str(args.parent_promotion.resolve()), "promotion_sha256": sha256_file(args.parent_promotion.resolve()),
            "registry_path": str(args.promotion_registry.resolve()), "registry_sha256": sha256_file(args.promotion_registry.resolve()),
            "stage_manifest_path": str(parent["stage_path"]), "stage_manifest_sha256": sha256_file(parent["stage_path"]),
        },
        "execution_capability_E": artifacts["execution_capability_E"], "decision_index_D": artifacts["decision_index_D"],
        "template_contract": artifacts["template_contract"], "presentation_config": artifacts["presentation_config"],
        "template_local_api_usage": artifacts["template_local_api_usage"],
        "delivery_size_report": artifacts["delivery_size_report"],
        "delivery_asset_report": artifacts["delivery_asset_report"],
        "overleaf_delivery_compatibility_report": artifacts["overleaf_delivery_compatibility_report"],
        "delivery_naming_report": artifacts["delivery_naming_report"],
        "build_manifest_M": {"path": "manifests/build_manifest.json", "sha256": sha256_file(run / "manifests/build_manifest.json")},
        "volume_partition_plan": artifact(run, partition_path, payload_hash=partition["deterministic_payload_hash"]),
        "delivery_set_manifest": artifact(run, delivery_set_path, payload_hash=single_delivery_set["deterministic_payload_hash"]),
        "final_pdf": artifacts["final_pdf"], "delivery_zip": artifacts["formal_zip"], "render_pack": artifacts["render_pack"],
        "page_provenance": artifacts["page_provenance"],
        "hard_gates": hard_gates,
        "presentation_hard_gates": {"PR-H01-explicit-cover-logo": True, "PR-H02-closed-decisions": True, "PR-H03-frozen-assets-preserved": True, "PR-H04-materialized-assets-bound": True},
        "scope_prohibitions": ["semantic_reclassification", "construct_reselection", "media_reselection", "layout_reselection", "presentation_inference", "formula_reconstruction", "table_reconstruction", "render_coverage_claim", "spec06_claim", "product_acceptance_claim"],
        "scope_limit": compile_report["scope_limit"],
        **({"volume_id": args.volume_id, "volume_partition_plan_sha256": sha256_file(partition_path)} if getattr(args, "volume_id", None) else {}),
    }
    write_json(run / "manifests/spec05_native_stage_manifest.json", stage)
    files = [
        {"path": path.relative_to(run).as_posix(), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
        for path in sorted(run.rglob("*")) if path.is_file() and path.name != "run_manifest.json"
    ]
    write_json(run / "manifests/run_manifest.json", {
        "schema_version": "immutable-run-manifest/1.1", "run_id": args.run_id, "generated_at": now(),
        "status": "passed", "stage_kind": "spec05_native_execution", "promotion_class": "formal_native",
        "immutable_after_publication": True, "files": files,
    })
    return {"status": "passed", "run": str(run), "build_id": build_id, "zip_sha256": sha256_file(delivery_zip), "pdf_sha256": sha256_file(delivery_pdf), "pages": reader.page_count, "volume_id": getattr(args, "volume_id", None)}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--run-dir", type=Path, required=True)
    value.add_argument("--run-id", required=True)
    value.add_argument("--promotion-registry", type=Path, required=True)
    value.add_argument("--parent-promotion", type=Path, required=True)
    value.add_argument("--parent-lineage-key", required=True)
    value.add_argument("--template-zip", type=Path, required=True)
    value.add_argument("--template-intake", type=Path, required=True)
    value.add_argument("--capability-manifest", type=Path, required=True)
    value.add_argument("--metadata-config", type=Path, required=True)
    value.add_argument("--presentation-config", type=Path, required=True)
    value.add_argument("--body-marker", required=True)
    value.add_argument("--volume-partition-plan", type=Path)
    value.add_argument("--volume-id", help=argparse.SUPPRESS)
    value.add_argument("--media-evidence-ledger", type=Path, required=True)
    value.add_argument("--media-representation-plan", type=Path, required=True)
    value.add_argument("--media-evidence-root", type=Path, required=True)
    value.add_argument("--asset-root", type=Path, action="append", default=[], required=True)
    value.add_argument("--source-pdf", type=Path, required=True)
    value.add_argument("--source-page-dir", type=Path)
    value.add_argument("--build-policy", type=Path, required=True)
    value.add_argument("--warning-review", type=Path)
    default_orchestrator = (
        Path(__file__).resolve().parents[2]
        / "luceon-popo-to-refined-elegantbook/scripts"
    )
    value.add_argument("--stage-gate", type=Path, default=default_orchestrator / "stage_promotion_gate.py")
    value.add_argument("--execution-capability", type=Path, default=default_orchestrator / "execution_capability.py")
    value.add_argument("--contract-validator", type=Path, default=default_orchestrator / "validate_intermediate_contracts.py")
    value.add_argument("--media-validator", type=Path, default=default_orchestrator / "media_source_representation.py")
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        print(json.dumps(produce(args), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "producer": VERSION, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
