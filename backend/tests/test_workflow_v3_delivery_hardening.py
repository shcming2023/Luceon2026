from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.workflow_v3 import stage_evaluators
from app.workflow_v3.stage_entrypoint import StageEntrypointError


def _delivery_zip(path: Path, *, tex_bytes: int = 32) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("main.tex", "\\input{body/generated-body.tex}\n")
        archive.writestr("elegantbook.cls", "class\n")
        archive.writestr(
            "body/generated-body.tex",
            "\\input{body/units/unit-0001/part-0001.tex}\n",
        )
        archive.writestr(
            "body/units/unit-0001/part-0001.tex",
            "x" * tex_bytes,
        )
    return path


def test_delivery_tex_limit_is_strictly_less_than_900k(tmp_path: Path) -> None:
    below = _delivery_zip(tmp_path / "below.zip", tex_bytes=899_999)
    equal = _delivery_zip(tmp_path / "equal.zip", tex_bytes=900_000)

    assert stage_evaluators._inspect_delivery_zip(below)["passed"] is True
    result = stage_evaluators._inspect_delivery_zip(equal)
    assert result["passed"] is False
    assert result["reason"] == "delivery_tex_size_limit"


def test_delivery_zip_rejects_unsafe_compression_ratio(tmp_path: Path) -> None:
    path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("main.tex", b"0" * 10_000_000)

    with zipfile.ZipFile(path) as archive:
        with pytest.raises(
            StageEntrypointError,
            match="compression ratio",
        ):
            stage_evaluators._validated_zip_members(archive)


def test_generated_body_definition_patterns_cover_commands_and_environments() -> None:
    forbidden = (
        r"\newcommand{\foo}{bar}",
        r"\NewDocumentCommand{\foo}{}{bar}",
        r"\newenvironment{answer}{}{}",
        r"\NewDocumentEnvironment{answer}{}{}{}",
        r"\def\foo{bar}",
    )
    assert all(
        stage_evaluators._BODY_DEFINITION_RE.search(value)
        for value in forbidden
    )


def test_independent_compile_checks_disk_before_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery = _delivery_zip(tmp_path / "book.zip")
    monkeypatch.setattr(
        stage_evaluators.shutil,
        "disk_usage",
        lambda path: type("Usage", (), {"free": 1})(),
    )
    with pytest.raises(
        StageEntrypointError,
        match="headroom",
    ):
        stage_evaluators._independent_compile(
            delivery,
            tmp_path / "compile",
        )
