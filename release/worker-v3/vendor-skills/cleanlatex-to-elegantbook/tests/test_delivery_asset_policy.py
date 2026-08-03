import importlib.util
import io
import json
import zipfile
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).parents[1] / "scripts/delivery_asset_policy.py"
SPEC = importlib.util.spec_from_file_location("delivery_asset_policy", SCRIPT)
POLICY = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(POLICY)


def png_bytes() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, "PNG")
    return stream.getvalue()


def materialization(path: Path, project_path: str = "images/a.png") -> Path:
    report = path / "asset_materialization_report.json"
    report.write_text(json.dumps({
        "copied_assets": {"a.png": {"project_path": project_path}},
        "source_region_crops": {},
        "presentation_assets": [],
    }), encoding="utf-8")
    return report


def test_native_referenced_raster_passes(tmp_path: Path) -> None:
    archive = tmp_path / "project.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("main.tex", "\\includegraphics{images/a.png}\n")
        output.writestr("images/a.png", png_bytes())
    report = POLICY.audit(archive, materialization(tmp_path))
    assert report["spec_status"] == "passed"
    assert report["summary"]["file_entities"] == 2


def test_scans_media_references_from_generated_body(tmp_path: Path) -> None:
    archive = tmp_path / "project.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("main.tex", "\\input{body/generated-body.tex}\n")
        output.writestr("body/generated-body.tex", "\\includegraphics{images/a.png}\n")
        output.writestr("images/a.png", png_bytes())
    report = POLICY.audit(archive, materialization(tmp_path))
    assert report["spec_status"] == "passed"
    assert report["scanned_tex_files"] == ["main.tex", "body/generated-body.tex"]


def test_unreferenced_generated_image_fails(tmp_path: Path) -> None:
    archive = tmp_path / "project.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("main.tex", "Body\n")
        output.writestr("images/a.png", png_bytes())
    report = POLICY.audit(archive, materialization(tmp_path))
    assert report["spec_status"] == "failed"
    assert report["unreferenced_generated_media"] == ["images/a.png"]
    assert "COMPILE_DELIVERY_ASSET_REPORT_INVALID" in report["failure_codes"]


def test_pdf_image_transport_fails(tmp_path: Path) -> None:
    archive = tmp_path / "project.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("main.tex", "\\includegraphics[page=1]{transport/media-pack.pdf}\n")
        output.writestr("transport/media-pack.pdf", b"%PDF-1.4\n")
    report = POLICY.audit(archive, materialization(tmp_path, "transport/media-pack.pdf"))
    assert report["spec_status"] == "failed"
    assert report["forbidden_pdf_media"] == ["transport/media-pack.pdf"]
    assert "COMPILE_IMAGE_REPRESENTATION_CHANGED" in report["failure_codes"]


def test_file_entity_limit_is_strict(tmp_path: Path) -> None:
    archive = tmp_path / "project.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("main.tex", "Body\n")
        for index in range(1_999):
            output.writestr(f"data/item-{index:04d}.txt", b"")
    report = POLICY.audit(archive, materialization(tmp_path, "unused.png"))
    assert report["delivery_zip"]["file_entities"] == 2_000
    assert report["checks"]["file_entities_strictly_under_2000"] is False
    assert "COMPILE_DELIVERY_FILE_ENTITY_LIMIT_EXCEEDED" in report["failure_codes"]


def test_raster_image_byte_limit_is_strict(tmp_path: Path) -> None:
    archive = tmp_path / "project.zip"
    oversized = png_bytes() + b"\0" * (1_000_000 - len(png_bytes()))
    assert len(oversized) == 1_000_000
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("main.tex", "\\includegraphics{images/a.png}\n")
        output.writestr("images/a.png", oversized)
    report = POLICY.audit(archive, materialization(tmp_path))
    assert report["spec_status"] == "failed"
    assert report["oversized_raster_images"] == ["images/a.png"]
    assert "COMPILE_RASTER_IMAGE_SIZE_LIMIT_EXCEEDED" in report["failure_codes"]
