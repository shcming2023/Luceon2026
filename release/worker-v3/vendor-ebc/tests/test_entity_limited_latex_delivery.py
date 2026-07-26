from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts/build_entity_limited_latex_zip.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("entity_limited_builder", BUILDER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_source_zip(
    tmp_path: Path, *, add_unreferenced: bool = False, transparent: bool = False
) -> tuple[Path, str]:
    source = tmp_path / "source"
    (source / "images").mkdir(parents=True)
    (source / "figure").mkdir()
    Image.new("RGB", (40, 30), "red").save(source / "images/a.jpg", "JPEG")
    second_name = "b.png" if transparent else "b.jpg"
    if transparent:
        Image.new("RGBA", (24, 36), (0, 120, 255, 140)).save(
            source / f"images/{second_name}", "PNG"
        )
    else:
        Image.new("RGB", (24, 36), (0, 120, 255)).save(
            source / f"images/{second_name}", "JPEG"
        )
    if add_unreferenced:
        Image.new("RGB", (10, 10), "green").save(
            source / "images/unreferenced.png", "PNG"
        )
    Image.new("RGB", (20, 20), "blue").save(source / "figure/cover.jpg", "JPEG")
    main = (
        "\\documentclass{book}\n"
        "\\usepackage{graphicx}\n"
        "\\begin{document}\n"
        "\\includegraphics[width=.5\\textwidth]{images/a.jpg}\n"
        f"\\includegraphics[height=2cm]{{images/{second_name}}}\n"
        "\\includegraphics[width=.25\\textwidth]{images/a.jpg}\n"
        "\\includegraphics{figure/cover.jpg}\n"
        "\\end{document}\n"
    )
    (source / "main.tex").write_text(main, encoding="utf-8")
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(candidate for candidate in source.rglob("*") if candidate.is_file()):
            bundle.write(path, path.relative_to(source).as_posix())
    return archive, main


def run_builder(archive: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(BUILDER_PATH),
            "--input-zip",
            str(archive),
            "--output-dir",
            str(output),
            "--pack-size",
            "1",
            "--max-entities",
            "20",
            "--max-zip-bytes",
            "1000000",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_builder_preserves_logical_references_and_preamble(tmp_path: Path) -> None:
    archive, before = make_source_zip(tmp_path)
    output = tmp_path / "output"
    completed = run_builder(archive, output)
    assert completed.returncode == 0, completed.stderr

    after = (output / "project/main.tex").read_text(encoding="utf-8")
    marker = "\\begin{document}"
    assert after.split(marker, 1)[0] == before.split(marker, 1)[0]
    assert after.count("\\includegraphics") == before.count("\\includegraphics")
    assert "\\includegraphics[page=1,width=.5\\textwidth]{transport/media-pack-0001.pdf}" in after
    assert "\\includegraphics[page=1,height=2cm]{transport/media-pack-0002.pdf}" in after
    assert "{figure/cover.jpg}" in after
    assert not (output / "project/images/a.jpg").exists()
    assert not (output / "project/images/b.jpg").exists()

    report = json.loads((output / "reports/media_transport_contract.json").read_text())
    assert report["status"] == "passed"
    assert report["summary"]["source_assets_packed"] == 2
    assert report["summary"]["logical_reference_occurrences_preserved"] == 3
    assert report["checks"]["all_embedded_media_pixel_exact"] is True
    assert report["checks"]["all_embedded_media_byte_exact"] is True
    assert report["summary"]["source_stream_byte_exact_assets"] == 2


def test_builder_is_deterministic(tmp_path: Path) -> None:
    archive, _ = make_source_zip(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert run_builder(archive, first).returncode == 0
    assert run_builder(archive, second).returncode == 0
    builder = load_builder()
    assert builder.sha256_file(first / "delivery/elegantbook-project.zip") == builder.sha256_file(
        second / "delivery/elegantbook-project.zip"
    )


def test_builder_refuses_unreferenced_assets(tmp_path: Path) -> None:
    archive, _ = make_source_zip(tmp_path, add_unreferenced=True)
    completed = run_builder(archive, tmp_path / "output")
    assert completed.returncode != 0
    assert "unreferenced files" in completed.stderr


def test_builder_fails_closed_for_transparent_raster(tmp_path: Path) -> None:
    archive, _ = make_source_zip(tmp_path, transparent=True)
    completed = run_builder(archive, tmp_path / "output")
    assert completed.returncode != 0
    assert "transparent raster transport is unsupported" in completed.stderr


def test_safe_extract_rejects_parent_traversal(tmp_path: Path) -> None:
    builder = load_builder()
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape.txt", b"no")
    with pytest.raises(ValueError, match="unsafe ZIP member"):
        builder.safe_extract(archive, tmp_path / "out")
