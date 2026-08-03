import argparse
import hashlib
import importlib.util
import json
import shutil
import zipfile
from pathlib import Path


ROOT = Path(__file__).parents[1]
CORE_PATH = ROOT / "scripts/spec05_native_execution_gate.py"
SPEC = importlib.util.spec_from_file_location("spec05_gate", CORE_PATH)
CORE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(CORE)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def test_stage_schema_matches_active_producer_contract() -> None:
    schema_path = (
        ROOT.parent
        / "cleanlatex-to-elegantbook/schemas/spec05-native-stage-manifest.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert CORE.STAGE_SCHEMA == schema["properties"]["schema_version"]["const"]


def test_cleanlatex_schemas_are_resolved_from_release_local_sibling() -> None:
    expected = ROOT.parent / "cleanlatex-to-elegantbook"
    assert CORE.cleanlatex_skill_root() == expected
    assert (
        CORE.cleanlatex_skill_root()
        / "schemas/spec05-native-stage-manifest.schema.json"
    ).is_file()


def test_cleanlatex_schema_root_relocates_with_vendored_skill_tree(
    tmp_path: Path,
) -> None:
    vendored = tmp_path / "arbitrary-release/vendor-skills"
    relocated_script = (
        vendored
        / "luceon-popo-to-refined-elegantbook/scripts/spec05_native_execution_gate.py"
    )
    relocated_script.parent.mkdir(parents=True)
    shutil.copy2(CORE_PATH, relocated_script)

    spec = importlib.util.spec_from_file_location(
        "relocated_spec05_gate",
        relocated_script,
    )
    relocated = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(relocated)

    assert relocated.cleanlatex_skill_root() == (
        vendored / "cleanlatex-to-elegantbook"
    )


def registry(path: Path, lineage: str, promotion: Path, promotion_id: str) -> None:
    gate_spec = importlib.util.spec_from_file_location("router", ROOT / "scripts/stage_promotion_gate.py")
    gate = importlib.util.module_from_spec(gate_spec)
    assert gate_spec.loader
    gate_spec.loader.exec_module(gate)
    document = {
        "schema_version": "promotion-registry/1.0", "registry_id": "test", "snapshot_id": "test-v1",
        "version": 1, "generated_at": "ignored", "parent_registry_ref": None, "parent_registry_sha256": None,
        "entries": [],
        "active_promotions": {lineage: {"promotion_id": promotion_id, "manifest_path": str(promotion), "manifest_sha256": sha(promotion), "promotion_class": "formal_native"}},
        "selection_rule": "test", "payload_hash": "",
    }
    document["payload_hash"] = gate.canonical_hash({key: value for key, value in document.items() if key not in {"generated_at", "payload_hash"}})
    write(path, document)


def promotion_fixture(tmp_path: Path, stage_kind: str) -> tuple[Path, str, Path]:
    stage = tmp_path / "run/stage.json"
    write(stage, {"full_spec04_status": "passed", "producer_mode": "formal_native"})
    evaluator = tmp_path / "evaluator.json"
    producer = tmp_path / "producer.json"
    write(evaluator, {"frozen": True})
    write(producer, {"frozen": True})
    promotion_id = "promotion-v1"
    promotion = tmp_path / "promotion.json"
    write(promotion, {
        "schema_version": "stage-promotion-manifest/1.1", "promotion_id": promotion_id,
        "lineage_key": "material/spec04d", "disposition": "promoted", "promotion_class": "formal_native",
        "producer_execution_provenance": "live_verified", "stage_kind": stage_kind,
        "stage_manifest": {"path": str(stage), "sha256": sha(stage)},
        "evaluator_capability": {"path": str(evaluator), "sha256": sha(evaluator)},
        "promoted_artifacts": {"producer_execution_capability": {"path": str(producer), "sha256": sha(producer)}},
    })
    registry_path = tmp_path / "registry.json"
    registry(registry_path, "material/spec04d", promotion, promotion_id)
    return registry_path, promotion_id, promotion


def test_full_formal_spec04d_is_eligible(tmp_path: Path) -> None:
    registry_path, _, promotion = promotion_fixture(tmp_path, "spec04d_render_plan_contract")
    output = tmp_path / "eligible.json"
    report, code = CORE.preflight_parent(argparse.Namespace(
        promotion_registry=registry_path, parent_promotion=promotion,
        parent_lineage_key="material/spec04d", output=output,
    ))
    assert code == 0
    assert report["eligible"] is True


def test_spec03_media_promotion_is_exact_negative_control(tmp_path: Path) -> None:
    registry_path, _, promotion = promotion_fixture(tmp_path, "spec03_media_contract")
    output = tmp_path / "rejected.json"
    report, code = CORE.preflight_parent(argparse.Namespace(
        promotion_registry=registry_path, parent_promotion=promotion,
        parent_lineage_key="material/spec04d", output=output,
    ))
    assert code == 4
    assert report["eligible"] is False
    assert report["failure_code"] == "SPEC05_PARENT_NOT_FULL_SPEC04D"


def test_independent_tp_h14_allows_declared_tcolorbox_style(tmp_path: Path) -> None:
    capability = tmp_path / "capability.json"
    body = tmp_path / "body.tex"
    write(capability, {"constructs": {"custom_commands": ["activitynum"], "custom_environments": ["answershow"]}})
    body.write_text("\\begin{tcolorbox}[featurebox]\nText\n\\end{tcolorbox}\n", encoding="utf-8")
    result = CORE.independent_template_local_api_scan(capability, body)
    assert result["violations"] == []


def test_independent_tp_h14_rejects_custom_api_calls(tmp_path: Path) -> None:
    capability = tmp_path / "capability.json"
    body = tmp_path / "body.tex"
    write(capability, {"constructs": {"custom_commands": ["activitynum"], "custom_environments": ["answershow"]}})
    body.write_text("\\activitynum{2}\n\\begin{answershow}x\\end{answershow}\n", encoding="utf-8")
    result = CORE.independent_template_local_api_scan(capability, body)
    assert {item["kind"] for item in result["violations"]} == {
        "template_local_command_call", "template_local_environment_call"
    }


def test_independent_delivery_zip_size_gate_is_strict(tmp_path: Path) -> None:
    below = tmp_path / "below.zip"
    below.write_bytes(b"x")
    with below.open("r+b") as stream:
        stream.truncate(CORE.MAX_DELIVERY_ZIP_BYTES - 1)
    at_limit = tmp_path / "at-limit.zip"
    at_limit.write_bytes(b"x")
    with at_limit.open("r+b") as stream:
        stream.truncate(CORE.MAX_DELIVERY_ZIP_BYTES)

    assert CORE.independent_delivery_zip_size_scan(below)["passed"] is True
    result = CORE.independent_delivery_zip_size_scan(at_limit)
    assert result["passed"] is False
    assert result["size_bytes"] == 50_000_000
    assert result["max_bytes_exclusive"] == 50_000_000


def test_independent_delivery_size_report_rejects_relaxed_constraint(tmp_path: Path) -> None:
    archive = tmp_path / "delivery.zip"
    archive.write_bytes(b"delivery")
    report = tmp_path / "delivery_size_report.json"
    write(report, {
        "schema_version": "spec05-delivery-size-report/1.0",
        "spec_status": "passed",
        "gate": {"gate_id": "CP-H18", "status": "passed"},
        "delivery_zip": {"path": str(archive.resolve()), "sha256": sha(archive), "size_bytes": archive.stat().st_size},
        "constraint": {"operator": "strictly_less_than", "max_bytes_exclusive": 50_000_001, "unit": "bytes"},
        "failure_code": None,
    })
    try:
        CORE.validate_delivery_size_report(archive, report)
    except ValueError as exc:
        assert "missing or was relaxed" in str(exc)
    else:
        raise AssertionError("a relaxed producer size constraint must be rejected")


def test_independent_delivery_size_report_accepts_exact_binding(tmp_path: Path) -> None:
    archive = tmp_path / "delivery.zip"
    archive.write_bytes(b"delivery")
    report = tmp_path / "delivery_size_report.json"
    write(report, {
        "schema_version": "spec05-delivery-size-report/1.0",
        "spec_status": "passed",
        "gate": {"gate_id": "CP-H18", "status": "passed"},
        "delivery_zip": {"path": str(archive.resolve()), "sha256": sha(archive), "size_bytes": archive.stat().st_size},
        "constraint": {"operator": "strictly_less_than", "max_bytes_exclusive": 50_000_000, "unit": "bytes"},
        "failure_code": None,
    })
    result = CORE.validate_delivery_size_report(archive, report)
    assert result["passed"] is True
    assert result["size_bytes"] == archive.stat().st_size


def test_independent_delivery_asset_scan_accepts_native_referenced_image(tmp_path: Path) -> None:
    archive = tmp_path / "delivery.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("main.tex", "\\includegraphics{images/a.png}\n")
        output.writestr("images/a.png", b"native-raster-evidence")
    result = CORE.independent_delivery_asset_scan(archive)
    assert result["file_entities"] == 2
    assert result["checks"]["native_raster_image_representation_preserved"] is True


def test_independent_delivery_asset_scan_rejects_pdf_pack(tmp_path: Path) -> None:
    archive = tmp_path / "delivery.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("main.tex", "\\includegraphics[page=1]{transport/media-pack.pdf}\n")
        output.writestr("transport/media-pack.pdf", b"%PDF-1.4\n")
    try:
        CORE.independent_delivery_asset_scan(archive)
    except ValueError as exc:
        assert "native_raster_image_representation_preserved" in str(exc)
    else:
        raise AssertionError("PDF image transport must be rejected")


def test_independent_scan_follows_generated_body_and_naming(tmp_path: Path) -> None:
    rendered = tmp_path / "rendered_body.tex"
    rendered.write_text("\\includegraphics{images/a.png}\n", encoding="utf-8")
    stem = "新教材全解 - 上册"
    archive = tmp_path / f"{stem}.zip"
    pdf = tmp_path / f"{stem}.pdf"
    pdf.write_bytes(b"pdf")
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("main.tex", "\\input{body/generated-body.tex}\n")
        output.writestr("body/generated-body.tex", "\\input{body/units/unit-0001/part-0001.tex}\n")
        output.writestr("body/units/unit-0001/part-0001.tex", rendered.read_bytes())
        output.writestr("images/a.png", b"native-raster-evidence")
    assert CORE.independent_delivery_asset_scan(archive)["scanned_tex_files"] == [
        "main.tex", "body/generated-body.tex", "body/units/unit-0001/part-0001.tex",
    ]
    assert CORE.independent_overleaf_transport_scan(archive, rendered)["checks"]["generated_body_reconstructs_rendered_body"] is True
    metadata = tmp_path / "metadata.json"
    write(metadata, {"values": {"title": "新教材全解"}, "volume_binding": {"label": "上册"}})
    report = tmp_path / "naming.json"
    write(report, {
        "schema_version": "spec05-delivery-naming-report/1.0", "spec_status": "passed",
        "gate": {"gate_id": "CP-H26", "status": "passed"},
        "frozen_identity": {"title": "新教材全解", "volume_label": "上册"},
        "expected": {"stem": stem, "zip": f"{stem}.zip", "pdf": f"{stem}.pdf"},
        "actual": {"zip": archive.name, "pdf": pdf.name},
        "checks": {"all": True}, "failure_code": None,
    })
    assert CORE.validate_delivery_naming_report(archive, pdf, metadata, report)["expected"]["stem"] == stem


def test_independent_overleaf_scan_accepts_controlled_body_shards(tmp_path: Path) -> None:
    rendered = tmp_path / "rendered_body.tex"
    rendered.write_bytes(b"alpha\nbeta\n")
    archive = tmp_path / "sharded.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("main.tex", "\\input{body/generated-body.tex}\n")
        output.writestr(
            "body/generated-body.tex",
            "\\input{body/units/unit-0001/part-0001.tex}\n"
            "\\input{body/units/unit-0002/part-0001.tex}\n",
        )
        output.writestr("body/units/unit-0001/part-0001.tex", b"alpha\n")
        output.writestr("body/units/unit-0002/part-0001.tex", b"beta\n")
    result = CORE.independent_overleaf_transport_scan(archive, rendered)
    assert result["transport_mode"] == "semantic_unit_payload"
    assert result["checks"]["generated_body_reconstructs_rendered_body"] is True
