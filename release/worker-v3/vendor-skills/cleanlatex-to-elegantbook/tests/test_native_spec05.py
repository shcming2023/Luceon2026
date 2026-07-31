import importlib.util
import json
import shutil
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/produce_native_spec05.py"
SPEC = importlib.util.spec_from_file_location("native_spec05", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)

API_SCRIPT = Path(__file__).parents[1] / "scripts/template_local_api_usage.py"
API_SPEC = importlib.util.spec_from_file_location("template_local_api_usage", API_SCRIPT)
API = importlib.util.module_from_spec(API_SPEC)
assert API_SPEC.loader
API_SPEC.loader.exec_module(API)

COMPAT_SCRIPT = Path(__file__).parents[1] / "scripts/delivery_compatibility.py"
COMPAT_SPEC = importlib.util.spec_from_file_location("delivery_compatibility", COMPAT_SCRIPT)
COMPAT = importlib.util.module_from_spec(COMPAT_SPEC)
assert COMPAT_SPEC.loader
COMPAT_SPEC.loader.exec_module(COMPAT)


def test_orchestrator_defaults_relocate_with_vendored_skill_tree(
    tmp_path: Path,
) -> None:
    vendored = tmp_path / "arbitrary-release/vendor-skills"
    relocated_script = (
        vendored / "cleanlatex-to-elegantbook/scripts/produce_native_spec05.py"
    )
    relocated_script.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, relocated_script)
    relocated_orchestrator = (
        vendored / "luceon-popo-to-refined-elegantbook/scripts"
    )
    relocated_orchestrator.mkdir(parents=True)

    spec = importlib.util.spec_from_file_location(
        "relocated_native_spec05",
        relocated_script,
    )
    relocated = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(relocated)
    defaults = {
        action.dest: action.default for action in relocated.parser()._actions
    }

    assert defaults["stage_gate"] == (
        relocated_orchestrator / "stage_promotion_gate.py"
    )
    assert defaults["execution_capability"] == (
        relocated_orchestrator / "execution_capability.py"
    )
    assert defaults["contract_validator"] == (
        relocated_orchestrator / "validate_intermediate_contracts.py"
    )
    assert defaults["media_validator"] == (
        relocated_orchestrator / "media_source_representation.py"
    )


def test_warning_report_requires_exact_c2_closure(tmp_path: Path) -> None:
    log = tmp_path / "main.log"
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "page-001.png").write_bytes(b"visual-evidence")
    log.write_text("Overfull \\hbox (3.0pt too wide) in paragraph at lines 10--11\n", encoding="utf-8")
    first = MODULE.warning_report(log, None, pages)
    assert first["status"] == "needs_review"
    event = first["events"][0]
    review = tmp_path / "review.json"
    review.write_text(json.dumps({
        "schema_version": "spec05-warning-review/1.0",
        "status": "approved",
        "closures": [{
            "fingerprint": event["fingerprint"],
            "classification": "C2_REVIEW_REQUIRED_CLOSED",
            "rationale": "Exact rendered page inspection shows no clipping or lost content.",
            "visual_pages": [1],
        }],
    }), encoding="utf-8")
    closed = MODULE.warning_report(log, review, pages)
    assert closed["status"] == "passed"
    assert closed["summary"]["C2_REVIEW_REQUIRED_CLOSED"] == 1
    assert closed["events"][0]["visual_evidence"][0]["sha256"] == MODULE.sha256_file(pages / "page-001.png")


def test_warning_report_never_allows_missing_glyph_review_override(tmp_path: Path) -> None:
    log = tmp_path / "main.log"
    pages = tmp_path / "pages"
    pages.mkdir()
    log.write_text("Missing character: There is no X in font nullfont!\n", encoding="utf-8")
    report = MODULE.warning_report(log, None, pages)
    assert report["status"] == "failed"
    assert report["summary"]["C1_BLOCKING"] == 1


def test_build_policy_accepts_direct_runtime_and_both_supported_poppler_renderers(tmp_path: Path) -> None:
    base = {
        "schema_version": "spec05-build-policy/1.0",
        "status": "approved",
        "template_metadata_constraints": {"required_nonempty": ["title"]},
        "compile": {
            "executor": "direct_exec", "container": "worker-v3-runtime", "entry": "main.tex",
            "command": ["latexmk", "main.tex"],
        },
        "render": {"renderer": "pdftoppm", "format": "png", "dpi": 110},
    }
    policy = tmp_path / "policy.json"
    for renderer in ("pdftoppm", "pdftocairo"):
        base["render"]["renderer"] = renderer
        policy.write_text(json.dumps(base), encoding="utf-8")
        assert MODULE.validate_policy(policy)["render"]["renderer"] == renderer


def test_build_policy_rejects_unknown_compile_executor(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "schema_version": "spec05-build-policy/1.0",
        "status": "approved",
        "template_metadata_constraints": {"required_nonempty": ["title"]},
        "compile": {"executor": "shell_eval", "container": "runtime", "entry": "main.tex", "command": ["latexmk"]},
        "render": {"renderer": "pdftoppm", "format": "png", "dpi": 110},
    }), encoding="utf-8")
    try:
        MODULE.validate_policy(policy)
    except ValueError as exc:
        assert "supported explicit executor" in str(exc)
    else:
        raise AssertionError("unknown compile executor must be rejected")


def test_build_policy_rejects_unknown_renderer(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "schema_version": "spec05-build-policy/1.0",
        "status": "approved",
        "template_metadata_constraints": {"required_nonempty": ["title"]},
        "compile": {"executor": "docker_copy_exec", "container": "tex", "entry": "main.tex", "command": ["latexmk"]},
        "render": {"renderer": "sample-specific-renderer", "format": "png", "dpi": 110},
    }), encoding="utf-8")
    try:
        MODULE.validate_policy(policy)
    except ValueError as exc:
        assert "supported Poppler PNG renderer" in str(exc)
    else:
        raise AssertionError("unknown renderer must be rejected")


def test_delivery_zip_size_gate_is_strict_at_50mb(tmp_path: Path) -> None:
    below = tmp_path / "below.zip"
    below.write_bytes(b"x")
    with below.open("r+b") as stream:
        stream.truncate(MODULE.MAX_DELIVERY_ZIP_BYTES - 1)
    at_limit = tmp_path / "at-limit.zip"
    at_limit.write_bytes(b"x")
    with at_limit.open("r+b") as stream:
        stream.truncate(MODULE.MAX_DELIVERY_ZIP_BYTES)

    accepted = MODULE.assess_delivery_zip_size(below)
    rejected = MODULE.assess_delivery_zip_size(at_limit)

    assert accepted["spec_status"] == "passed"
    assert accepted["gate"] == {"gate_id": "CP-H18", "status": "passed"}
    assert rejected["spec_status"] == "failed"
    assert rejected["failure_code"] == "COMPILE_DELIVERY_ZIP_SIZE_LIMIT_EXCEEDED"
    assert rejected["constraint"] == {
        "operator": "strictly_less_than",
        "max_bytes_exclusive": 50_000_000,
        "unit": "bytes",
    }


def test_package_info_substitution_is_not_misclassified_as_warning(tmp_path: Path) -> None:
    log = tmp_path / "main.log"
    pages = tmp_path / "pages"
    pages.mkdir()
    log.write_text("Package xcolor Info: Model `HTML' substituted by `rgb' on input line 9.\n", encoding="utf-8")
    report = MODULE.warning_report(log, None, pages)
    assert report["status"] == "passed"
    assert report["events"] == []


def test_tp_h14_allows_standard_tcolorbox_style_usage() -> None:
    body = "\\begin{tcolorbox}[featurebox,title={Source-backed}]\nBody\n\\end{tcolorbox}\n"
    assert API.scan_text(["activitynum"], ["answershow", "internalanswerbox"], body) == []


def test_tp_h14_rejects_template_local_command_and_environment() -> None:
    body = "\\activitynum{1}\n\\begin{answershow}x\\end{answershow}\n"
    violations = API.scan_text(["activitynum"], ["answershow"], body)
    assert {item["kind"] for item in violations} == {
        "template_local_command_call", "template_local_environment_call"
    }


def test_tp_h14_ignores_commented_calls() -> None:
    body = "% \\activitynum{1}\nText with escaped \\% sign.\n"
    assert API.scan_text(["activitynum"], ["answershow"], body) == []


def test_tp_h14_accepts_legacy_top_level_capability_inventory(tmp_path: Path) -> None:
    capability = tmp_path / "capability.json"
    body = tmp_path / "body.tex"
    capability.write_text(json.dumps({"custom_commands": ["activitynum"], "custom_environments": ["answershow"]}), encoding="utf-8")
    body.write_text("Ordinary text.\n", encoding="utf-8")
    assert API.audit_template_local_api_usage(capability, body)["spec_status"] == "passed"


def _delivery_fixture():
    partition = {
        "volumes": [
            {"volume_id": "volume-01", "ordinal": 1, "render_node_ids": ["r1", "r2"], "source_block_ids": ["s1", "s2"]},
            {"volume_id": "volume-02", "ordinal": 2, "render_node_ids": ["r3"], "source_block_ids": ["s3"]},
        ]
    }
    volume_records = [
        {
            **item,
            "page_provenance": {
                "path": f"volumes/{item['volume_id']}/reports/final_pdf_page_provenance.json",
                "sha256": "b" * 64,
            },
            "hard_gates": {"CP-H01": True, "CP-H20": True},
        }
        for item in partition["volumes"]
    ]
    manifest = {
        "schema_version": "spec05-delivery-set-manifest/1.2", "spec_status": "passed",
        "parent": {"volume_partition_plan_sha256": "a" * 64},
        "volume_partition_plan": {"sha256": "a" * 64}, "volumes": volume_records,
    }
    manifest["deterministic_payload_hash"] = MODULE.canonical_hash(manifest)
    return partition, manifest


def test_delivery_names_follow_frozen_unicode_title_and_volume() -> None:
    assert COMPAT.expected_delivery_names("新教材全解 五上 数学", "上册") == {
        "stem": "新教材全解 五上 数学 - 上册",
        "zip": "新教材全解 五上 数学 - 上册.zip",
        "pdf": "新教材全解 五上 数学 - 上册.pdf",
    }
    assert COMPAT.expected_delivery_names("Math: Practice/Review")["zip"] == "Math_ Practice_Review.zip"


def test_overleaf_transport_requires_exact_root_and_hash_bound_body(tmp_path: Path) -> None:
    rendered = tmp_path / "rendered_body.tex"
    rendered.write_text("Source-backed body.\n", encoding="utf-8")
    archive = tmp_path / "Book - Volume I.zip"
    transport = COMPAT.build_body_transport(rendered.read_bytes())
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("main.tex", "\\documentclass{elegantbook}\n\\begin{document}\n\\input{body/generated-body.tex}\n\\end{document}\n")
        output.writestr("body/generated-body.tex", transport["loader_bytes"])
        for item in transport["parts"]:
            output.writestr(item["path"], item["bytes"])
    report = COMPAT.audit_zip_transport(archive, rendered)
    assert report["spec_status"] == "passed"
    assert report["gate"] == {"gate_id": "CP-H25", "status": "passed"}


def test_overleaf_transport_rejects_nested_behavior(tmp_path: Path) -> None:
    rendered = tmp_path / "rendered_body.tex"
    rendered.write_text("\\input{another.tex}\n", encoding="utf-8")
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("main.tex", "\\input{body/generated-body.tex}\n")
        output.writestr("body/generated-body.tex", rendered.read_bytes())
    assert COMPAT.audit_zip_transport(archive, rendered)["spec_status"] == "failed"


def test_overleaf_transport_shards_large_body_without_byte_drift(tmp_path: Path) -> None:
    body = (b"source-backed line\n" * 180_000)
    rendered = tmp_path / "rendered_body.tex"
    rendered.write_bytes(body)
    transport = COMPAT.build_body_transport(body)
    assert transport["mode"] == "semantic_unit_payload"
    assert b"".join(item["bytes"] for item in transport["parts"]) == body
    assert all(len(item["bytes"]) < 900_000 for item in transport["parts"])

    archive = tmp_path / "sharded.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("main.tex", "\\input{body/generated-body.tex}\n")
        output.writestr("body/generated-body.tex", transport["loader_bytes"])
        for item in transport["parts"]:
            output.writestr(item["path"], item["bytes"])
    report = COMPAT.audit_zip_transport(archive, rendered)
    assert report["spec_status"] == "passed"
    assert report["generated_body"]["transport_mode"] == "semantic_unit_payload"
    assert report["checks"]["generated_body_reconstructs_rendered_body"] is True


def test_overleaf_transport_rejects_oversized_tex_member(tmp_path: Path) -> None:
    rendered = tmp_path / "rendered_body.tex"
    rendered.write_bytes(b"x\n")
    archive = tmp_path / "oversized.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("main.tex", b"%" * 900_000)
        output.writestr("body/generated-body.tex", b"\\input{body/units/unit-0001/part-0001.tex}\n")
        output.writestr("body/units/unit-0001/part-0001.tex", b"x\n")
    report = COMPAT.audit_zip_transport(archive, rendered)
    assert report["spec_status"] == "failed"
    assert report["checks"]["each_body_transport_tex_strictly_under_900k"] is False


def test_overleaf_transport_records_but_does_not_reject_total_editable_text_over_7mb(tmp_path: Path) -> None:
    rendered = tmp_path / "rendered_body.tex"
    rendered.write_bytes(b"x\n")
    archive = tmp_path / "editable-total.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("main.tex", "\\input{body/generated-body.tex}\n")
        output.writestr("body/generated-body.tex", b"\\input{body/units/unit-0001/part-0001.tex}\n")
        output.writestr("body/units/unit-0001/part-0001.tex", b"x\n")
        for index in range(4):
            output.writestr(f"data-{index}.bib", b"%" * 1_750_000)
    report = COMPAT.audit_zip_transport(archive, rendered)
    assert report["spec_status"] == "passed"
    assert report["capacity"]["editable_text_bytes"] > 7_000_000


def test_body_transport_preserves_frozen_semantic_units(tmp_path: Path) -> None:
    body = b"% n1\nalpha\n\n% n2\nbeta\n\n"
    emissions = [
        {"render_node_id": "n1", "latex_start_line": 1, "latex_end_line": 3},
        {"render_node_id": "n2", "latex_start_line": 4, "latex_end_line": 6},
    ]
    units = [
        {"unit_id": "unit-0001", "ordinal": 1, "render_node_ids": ["n1"]},
        {"unit_id": "unit-0002", "ordinal": 2, "render_node_ids": ["n2"]},
    ]
    transport = COMPAT.build_body_transport(body, body_units=units, emissions=emissions)
    assert [item["path"] for item in transport["parts"]] == [
        "body/units/unit-0001/part-0001.tex",
        "body/units/unit-0002/part-0001.tex",
    ]
    assert transport["reconstructed_bytes"] == body


def test_delivery_set_requires_exact_ordered_cross_volume_coverage() -> None:
    partition, manifest = _delivery_fixture()
    assert MODULE.validate_delivery_set_manifest(manifest, partition) == {"volumes": 2, "render_nodes": 3, "source_atoms": 3}


def test_delivery_set_rejects_duplicate_source_atom_across_volumes() -> None:
    partition, manifest = _delivery_fixture()
    partition["volumes"][1]["source_block_ids"] = ["s2"]
    manifest["volumes"][1]["source_block_ids"] = ["s2"]
    manifest["deterministic_payload_hash"] = MODULE.canonical_hash({key: value for key, value in manifest.items() if key != "deterministic_payload_hash"})
    try:
        MODULE.validate_delivery_set_manifest(manifest, partition)
    except ValueError as exc:
        assert "source coverage" in str(exc)
    else:
        raise AssertionError("duplicate cross-volume source atom was accepted")
