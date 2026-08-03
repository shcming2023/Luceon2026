import importlib.util
import zipfile
from pathlib import Path


ROOT = Path(__file__).parents[1]
STAGED = ROOT / "refactor/overleaf-delivery-capacity-contract-v2/staged-skills"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


COMPAT = load(
    STAGED / "cleanlatex-to-elegantbook/scripts/delivery_compatibility.py",
    "capacity_v2_compat",
)
PARTITION = load(
    STAGED / "luceon-popo-to-refined-elegantbook/scripts/spec04d_render_plan_contract.py",
    "capacity_v2_partition",
)


def write_delivery(path: Path, rendered: Path, body: bytes) -> dict:
    transport = COMPAT.build_body_transport(body)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("main.tex", "\\input{body/generated-body.tex}\n")
        archive.writestr("elegantbook.cls", "% frozen template\n")
        archive.writestr("body/generated-body.tex", transport["loader_bytes"])
        for part in transport["parts"]:
            archive.writestr(part["path"], part["bytes"])
    return COMPAT.audit_zip_transport(path, rendered)


def test_single_volume_direct_transport(tmp_path: Path) -> None:
    body = b"source-backed short body\n"
    rendered = tmp_path / "rendered_body.tex"
    rendered.write_bytes(body)
    report = write_delivery(tmp_path / "single.zip", rendered, body)
    assert report["spec_status"] == "passed"
    assert report["generated_body"]["transport_mode"] == "direct_payload"


def test_single_volume_sharded_transport_without_semantic_split(tmp_path: Path) -> None:
    body = b"source-backed line\n" * 190_000
    rendered = tmp_path / "rendered_body.tex"
    rendered.write_bytes(body)
    report = write_delivery(tmp_path / "sharded-single.zip", rendered, body)
    assert report["spec_status"] == "passed"
    assert report["generated_body"]["transport_mode"] == "sharded_payload"
    assert len(report["generated_body"]["parts"]) >= 2
    assert report["checks"]["generated_body_reconstructs_rendered_body"] is True


def test_sharding_does_not_bypass_project_editable_text_limit(tmp_path: Path) -> None:
    body = b"source-backed line\n" * 390_000
    rendered = tmp_path / "rendered_body.tex"
    rendered.write_bytes(body)
    report = write_delivery(tmp_path / "too-large-single.zip", rendered, body)
    assert report["spec_status"] == "failed"
    assert report["checks"]["each_tex_file_strictly_under_2mb"] is True
    assert report["checks"]["editable_text_strictly_under_7mb"] is False


def test_two_volume_plan_and_each_volume_transport_pass(tmp_path: Path) -> None:
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
    policy = {
        "volume_partition": {
            "mode": "two_volume",
            "decision_refs": ["CAPACITY-V2-DECISION"],
            "trigger_evidence": [{"kind": "editable_text_limit", "measured_bytes": 7_100_000}],
            "non_media_file_entity_allowance": 3,
            "non_media_zip_bytes_allowance": 1_000,
            "boundary": {"before_render_node_id": "render::4", "semantic_boundary_type": "chapter"},
            "volumes": [
                {"volume_id": "volume-01", "ordinal": 1, "label": "Volume I", "filename_suffix": "volume-i", "metadata_overrides": {"subtitle": "Volume I"}, "delivery_capacity_preflight": {"estimated_generated_body_bytes_upper_bound": 3_500_000, "estimated_editable_text_bytes_upper_bound": 3_600_000, "largest_atomic_tex_line_bytes_upper_bound": 256, "evidence_refs": ["CAP-01"]}},
                {"volume_id": "volume-02", "ordinal": 2, "label": "Volume II", "filename_suffix": "volume-ii", "metadata_overrides": {"subtitle": "Volume II"}, "delivery_capacity_preflight": {"estimated_generated_body_bytes_upper_bound": 3_500_000, "estimated_editable_text_bytes_upper_bound": 3_600_000, "largest_atomic_tex_line_bytes_upper_bound": 256, "evidence_refs": ["CAP-02"]}},
            ],
        }
    }
    plan = PARTITION.build_volume_partition_plan(nodes, policy)
    assert plan["mode"] == "two_volume"
    assert [item["render_node_ids"] for item in plan["volumes"]] == [
        ["render::1", "render::2", "render::3"],
        ["render::4", "render::5", "render::6"],
    ]
    for ordinal in (1, 2):
        body = (f"volume {ordinal} source line\n".encode()) * 170_000
        rendered = tmp_path / f"rendered-{ordinal}.tex"
        rendered.write_bytes(body)
        report = write_delivery(tmp_path / f"volume-{ordinal}.zip", rendered, body)
        assert report["spec_status"] == "passed"
        assert report["generated_body"]["transport_mode"] == "sharded_payload"
