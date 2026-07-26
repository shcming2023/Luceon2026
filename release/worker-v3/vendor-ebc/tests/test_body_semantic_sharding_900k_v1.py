import importlib.util
import io
import json
import zipfile
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).parents[1]
STAGED = ROOT / "refactor/body-semantic-sharding-900k-v1/staged-skills"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


COMPAT = load(
    STAGED / "cleanlatex-to-elegantbook/scripts/delivery_compatibility.py",
    "body_semantic_sharding_compat",
)
ASSETS = load(
    STAGED / "cleanlatex-to-elegantbook/scripts/delivery_asset_policy.py",
    "body_semantic_sharding_assets",
)
PARTITION = load(
    STAGED / "luceon-popo-to-refined-elegantbook/scripts/spec04d_render_plan_contract.py",
    "body_semantic_sharding_partition",
)


def write_transport(path: Path, rendered: Path, transport: dict, extra: dict[str, bytes] | None = None) -> dict:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("main.tex", "\\input{body/generated-body.tex}\n")
        archive.writestr("body/generated-body.tex", transport["loader_bytes"])
        for part in transport["parts"]:
            archive.writestr(part["path"], part["bytes"])
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)
    return COMPAT.audit_zip_transport(path, rendered)


def test_small_single_volume_still_uses_one_semantic_leaf(tmp_path: Path) -> None:
    body = b"source-backed short body\n"
    rendered = tmp_path / "rendered.tex"
    rendered.write_bytes(body)
    transport = COMPAT.build_body_transport(body)
    report = write_transport(tmp_path / "single.zip", rendered, transport)
    assert report["spec_status"] == "passed"
    assert [item["path"] for item in report["generated_body"]["parts"]] == [
        "body/units/unit-0001/part-0001.tex"
    ]


def test_large_body_is_split_strictly_below_900k(tmp_path: Path) -> None:
    body = b"source-backed line\n" * 100_000
    rendered = tmp_path / "rendered.tex"
    rendered.write_bytes(body)
    transport = COMPAT.build_body_transport(body)
    report = write_transport(tmp_path / "sharded.zip", rendered, transport)
    assert report["spec_status"] == "passed"
    assert len(report["generated_body"]["parts"]) >= 2
    assert max(item["bytes"] for item in report["generated_body"]["parts"]) < 900_000
    assert report["checks"]["generated_body_reconstructs_rendered_body"] is True


def test_semantic_units_are_preserved_before_byte_sharding() -> None:
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


def test_editable_total_over_7mb_is_observed_not_rejected(tmp_path: Path) -> None:
    body = b"x\n"
    rendered = tmp_path / "rendered.tex"
    rendered.write_bytes(body)
    transport = COMPAT.build_body_transport(body)
    report = write_transport(
        tmp_path / "large-editable-total.zip",
        rendered,
        transport,
        {f"data-{index}.bib": b"%" * 1_750_000 for index in range(4)},
    )
    assert report["spec_status"] == "passed"
    assert report["capacity"]["editable_text_bytes"] > 7_000_000


def test_raster_image_at_1mb_is_rejected(tmp_path: Path) -> None:
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, "PNG")
    image = stream.getvalue() + b"\0" * (1_000_000 - len(stream.getvalue()))
    archive = tmp_path / "image-limit.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("main.tex", "\\includegraphics{images/a.png}\n")
        output.writestr("images/a.png", image)
    materialization = tmp_path / "materialization.json"
    materialization.write_text(json.dumps({
        "copied_assets": {"a": {"project_path": "images/a.png"}},
        "source_region_crops": {},
        "presentation_assets": [],
    }), encoding="utf-8")
    report = ASSETS.audit(archive, materialization)
    assert report["checks"]["each_raster_image_strictly_under_1mb"] is False
    assert "COMPILE_RASTER_IMAGE_SIZE_LIMIT_EXCEEDED" in report["failure_codes"]


def test_two_volume_plan_freezes_body_units_per_volume() -> None:
    nodes = []
    for order in range(1, 7):
        top = order in {1, 4}
        nodes.append({
            "render_node_id": f"render::{order}",
            "render_order": order,
            "node_kind": "book_structure" if top else "plain_body",
            "source_block_ids": [f"source::{order}"],
            "output_anchor_id": f"anchor::{order}",
            "parent_output_anchor_id": None if top else ("anchor::1" if order < 4 else "anchor::4"),
            "payload": {"raw_content": str(order)},
        })
    capacity = {
        "estimated_generated_body_bytes_upper_bound": 1_500_000,
        "estimated_editable_text_bytes_upper_bound": 1_600_000,
        "largest_atomic_tex_line_bytes_upper_bound": 256,
        "evidence_refs": ["CAP"],
    }
    policy = {"volume_partition": {
        "mode": "two_volume",
        "decision_refs": ["D"],
        "trigger_evidence": [{"kind": "file_entity_limit", "count": 2000}],
        "boundary": {"before_render_node_id": "render::4", "semantic_boundary_type": "chapter"},
        "volumes": [
            {"volume_id": "volume-01", "ordinal": 1, "label": "Volume I", "filename_suffix": "volume-i", "metadata_overrides": {"subtitle": "Volume I"}, "delivery_capacity_preflight": capacity},
            {"volume_id": "volume-02", "ordinal": 2, "label": "Volume II", "filename_suffix": "volume-ii", "metadata_overrides": {"subtitle": "Volume II"}, "delivery_capacity_preflight": capacity},
        ],
    }}
    plan = PARTITION.build_volume_partition_plan(nodes, policy)
    assert plan["schema_version"] == "volume-partition-plan/1.2"
    assert [unit["render_node_ids"] for volume in plan["volumes"] for unit in volume["body_units"]] == [
        ["render::1", "render::2", "render::3"],
        ["render::4", "render::5", "render::6"],
    ]
