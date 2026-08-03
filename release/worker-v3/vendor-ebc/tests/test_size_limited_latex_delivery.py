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
BUILDER_PATH = ROOT / "scripts/build_size_limited_latex_zip.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("size_limited_builder", BUILDER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builder_transcodes_only_referenced_body_png(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "images").mkdir(parents=True)
    (source / "figure").mkdir()
    noisy = Image.effect_noise((360, 360), 80).convert("RGB")
    noisy.save(source / "images/media.png", "PNG")
    Image.new("RGB", (20, 20), "blue").save(source / "figure/cover.png", "PNG")
    marker = "% BODY MARKER"
    main = (
        "\\documentclass{book}\n"
        "\\cover{cover.png}\n"
        f"{marker}\n"
        "\\includegraphics[width=.5\\textwidth]{images/media.png}\n"
        "\\end{document}\n"
    )
    (source / "main.tex").write_text(main, encoding="utf-8")
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            bundle.write(path, path.relative_to(source).as_posix())

    output = tmp_path / "output"
    completed = subprocess.run(
        [
            sys.executable,
            str(BUILDER_PATH),
            "--input-zip",
            str(archive),
            "--output-dir",
            str(output),
            "--body-marker",
            marker,
            "--max-zip-bytes",
            "1000000",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert not (output / "project/images/media.png").exists()
    assert (output / "project/images/media.jpg").is_file()
    assert (output / "project/figure/cover.png").is_file()
    after = (output / "project/main.tex").read_text(encoding="utf-8")
    assert after.split(marker, 1)[0] == main.split(marker, 1)[0]
    assert "{images/media.jpg}" in after
    report = json.loads((output / "reports/package_size_optimization.json").read_text())
    assert report["status"] == "passed"
    assert report["summary"]["transcoded_assets"] == 1


def test_safe_extract_rejects_parent_traversal(tmp_path: Path) -> None:
    builder = load_builder()
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape.txt", b"no")
    with pytest.raises(ValueError, match="unsafe ZIP member"):
        builder.safe_extract(archive, tmp_path / "out")


def test_normalized_reference_rejects_parent_path() -> None:
    builder = load_builder()
    with pytest.raises(ValueError, match="unsafe graphics reference"):
        builder.normalized_relpath("../outside.png")
