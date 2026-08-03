from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_spec05_template_local_api import audit, strip_tex_comments


def write_fixture(tmp_path: Path, body: str, *, cp_h14: bool = True) -> tuple[Path, Path, Path]:
    capability = tmp_path / "capability.json"
    rendered_body = tmp_path / "rendered_body.tex"
    compile_report = tmp_path / "compile_report.json"
    capability.write_text(json.dumps({"constructs": {"custom_commands": ["activitynum"], "custom_environments": ["answershow"]}}), encoding="utf-8")
    rendered_body.write_text(body, encoding="utf-8")
    compile_report.write_text(json.dumps({"hard_gates": {"CP-H14": cp_h14}}), encoding="utf-8")
    return capability, rendered_body, compile_report


def test_standard_tcolorbox_style_usage_is_not_a_template_local_api_call(tmp_path: Path) -> None:
    paths = write_fixture(tmp_path, "\\begin{tcolorbox}[featurebox]\nSource text.\n\\end{tcolorbox}\n")
    report = audit(*paths)
    assert report["spec_status"] == "passed"
    assert report["violations"] == []


def test_template_local_command_call_fails(tmp_path: Path) -> None:
    paths = write_fixture(tmp_path, "\\activitynum{3}\n")
    report = audit(*paths)
    assert report["spec_status"] == "failed"
    assert report["violations"] == [{"kind": "template_local_command_call", "name": "activitynum", "line": 1}]


def test_template_local_environment_call_fails(tmp_path: Path) -> None:
    paths = write_fixture(tmp_path, "\\begin{answershow}\nA\n\\end{answershow}\n")
    report = audit(*paths)
    assert report["spec_status"] == "failed"
    assert {item["kind"] for item in report["violations"]} == {"template_local_environment_call"}


def test_commented_api_name_is_ignored_but_escaped_percent_is_preserved() -> None:
    assert strip_tex_comments("% \\activitynum{1}\nText \\% value\n") == "\nText \\% value\n"


def test_unbound_rendered_body_fails_even_without_api_usage(tmp_path: Path) -> None:
    paths = write_fixture(tmp_path, "Plain body.\n", cp_h14=False)
    report = audit(*paths)
    assert report["spec_status"] == "failed"
    assert report["violations"] == [{"kind": "rendered_body_not_bound_to_delivery", "name": "CP-H14", "line": None}]
