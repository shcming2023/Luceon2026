from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "validate_spec05_run.py"
SPEC = importlib.util.spec_from_file_location("validate_spec05_run", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def event(message: str, *, kind: str, count: int, classification: str) -> dict:
    return {
        "fingerprint": hashlib.sha256(message.encode()).hexdigest(),
        "kind": kind,
        "message": message,
        "count": count,
        "classification": classification,
    }


def test_closed_overfull_warning_is_verified_not_reclassified_as_fatal() -> None:
    message = r"Overfull \vbox (11.20638pt too high) has occurred while \output is active []"
    log = f"{message}\n{message}\n"
    warnings = {"events": [event(message, kind="overfull", count=2, classification="C2_REVIEW_REQUIRED_CLOSED")]}
    assert MODULE.validate_compile_log_evidence(log, warnings) == []


def test_fatal_tex_pattern_still_blocks_even_when_warning_inventory_matches() -> None:
    errors = MODULE.validate_compile_log_evidence("! Undefined control sequence.\n", {"events": []})
    assert errors == ["fatal compile log patterns remain: ['tex_error']"]


def test_log_warning_cannot_be_hidden_by_a_green_report() -> None:
    message = r"Overfull \hbox (4.0pt too wide) in paragraph at lines 10--11"
    errors = MODULE.validate_compile_log_evidence(message + "\n", {"events": []})
    assert any("fingerprint mismatch" in item for item in errors)


def test_warning_count_must_match_the_raw_log() -> None:
    message = r"Underfull \hbox (badness 10000) in paragraph at lines 1--2"
    normalized = r"Underfull \hbox (badness 10000) in paragraph at line <N>"
    warnings = {"events": [event(normalized, kind="underfull", count=2, classification="C3_INFO_CLOSED")]}
    errors = MODULE.validate_compile_log_evidence(message + "\n", warnings)
    assert any("event mismatch" in item for item in errors)


def test_semantic_unit_loader_reconstructs_rendered_body(tmp_path: Path) -> None:
    run = tmp_path / "run"
    clean = run / "build" / "clean-src"
    (clean / "body" / "units" / "unit-0001").mkdir(parents=True)
    (clean / "body" / "units" / "unit-0002").mkdir(parents=True)
    (run / "render").mkdir(parents=True)
    parts = [
        ("body/units/unit-0001/part-0001.tex", b"chapter one\n"),
        ("body/units/unit-0002/part-0001.tex", b"chapter two\n"),
    ]
    rendered = b"".join(payload for _, payload in parts)
    (run / "render" / "rendered_body.tex").write_bytes(rendered)
    loader = b"".join(f"\\input{{{path}}}\n".encode() for path, _ in parts)
    (clean / "body" / "generated-body.tex").parent.mkdir(parents=True, exist_ok=True)
    (clean / "body" / "generated-body.tex").write_bytes(loader)
    (clean / "main.tex").write_text("\\input{body/generated-body.tex}\n", encoding="utf-8")
    (clean / "elegantbook.cls").write_text("% frozen\n", encoding="utf-8")
    part_meta = []
    for path, payload in parts:
        target = clean / path
        target.write_bytes(payload)
        part_meta.append({
            "path": path,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    zmap = {
        path.relative_to(clean).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in clean.rglob("*") if path.is_file()
    }
    compatibility = {
        "spec_status": "passed",
        "checks": {"generated_body_reconstructs_rendered_body": True},
        "capacity": {"tex_members": sorted(path for path in zmap if path.endswith(".tex"))},
        "generated_body": {
            "path": "body/generated-body.tex",
            "bytes": len(loader),
            "sha256": hashlib.sha256(loader).hexdigest(),
            "transport_mode": "semantic_unit_payload",
            "parts": part_meta,
        },
        "rendered_body": {
            "bytes": len(rendered),
            "sha256": hashlib.sha256(rendered).hexdigest(),
        },
    }
    execution = {"rendered_body": {"sha256": hashlib.sha256(rendered).hexdigest()}}
    errors = MODULE.validate_delivery_body_transport(
        compatibility=compatibility,
        execution=execution,
        run=run,
        clean=clean,
        zmap=zmap,
        tex_members=compatibility["capacity"]["tex_members"],
        class_members=["elegantbook.cls"],
    )
    assert errors == []


def test_semantic_unit_loader_cannot_hide_reordered_parts(tmp_path: Path) -> None:
    run = tmp_path / "run"
    clean = run / "build" / "clean-src"
    part_path = "body/units/unit-0001/part-0001.tex"
    (clean / part_path).parent.mkdir(parents=True)
    (run / "render").mkdir(parents=True)
    (clean / part_path).write_bytes(b"source order\n")
    loader = b"\\input{body/units/unit-0001/part-0001.tex}\n"
    (clean / "body" / "generated-body.tex").write_bytes(loader)
    (run / "render" / "rendered_body.tex").write_bytes(b"different order\n")
    payload_sha = hashlib.sha256(b"source order\n").hexdigest()
    loader_sha = hashlib.sha256(loader).hexdigest()
    zmap = {
        part_path: payload_sha,
        "body/generated-body.tex": loader_sha,
        "main.tex": hashlib.sha256(b"main").hexdigest(),
        "elegantbook.cls": hashlib.sha256(b"class").hexdigest(),
    }
    compatibility = {
        "spec_status": "passed",
        "checks": {"generated_body_reconstructs_rendered_body": True},
        "capacity": {"tex_members": ["main.tex", "body/generated-body.tex", part_path]},
        "generated_body": {
            "path": "body/generated-body.tex",
            "bytes": len(loader),
            "sha256": loader_sha,
            "parts": [{"path": part_path, "bytes": len(b"source order\n"), "sha256": payload_sha}],
        },
        "rendered_body": {
            "bytes": len(b"different order\n"),
            "sha256": hashlib.sha256(b"different order\n").hexdigest(),
        },
    }
    execution = {"rendered_body": compatibility["rendered_body"]}
    errors = MODULE.validate_delivery_body_transport(
        compatibility=compatibility,
        execution=execution,
        run=run,
        clean=clean,
        zmap=zmap,
        tex_members=compatibility["capacity"]["tex_members"],
        class_members=["elegantbook.cls"],
    )
    assert errors == ["rendered body/delivery binding mismatch"]
