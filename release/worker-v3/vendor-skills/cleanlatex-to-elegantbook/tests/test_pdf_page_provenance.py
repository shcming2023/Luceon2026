import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest


fitz = pytest.importorskip("fitz")

SKILL_ROOT = Path(__file__).parents[1]

PRODUCER_SPEC = importlib.util.spec_from_file_location(
    "native_spec05_provenance",
    SKILL_ROOT / "scripts/produce_native_spec05.py",
)
PRODUCER = importlib.util.module_from_spec(PRODUCER_SPEC)
assert PRODUCER_SPEC.loader
PRODUCER_SPEC.loader.exec_module(PRODUCER)

RENDERER_SPEC = importlib.util.spec_from_file_location(
    "frozen_plan_renderer_provenance",
    SKILL_ROOT / "scripts/render_frozen_plan.py",
)
RENDERER = importlib.util.module_from_spec(RENDERER_SPEC)
assert RENDERER_SPEC.loader
RENDERER_SPEC.loader.exec_module(RENDERER)


def _destination_pair(render_node_id: str) -> tuple[str, str]:
    token = hashlib.sha256(render_node_id.encode("utf-8")).hexdigest()[:32]
    return f"luceon-v3-s-{token}", f"luceon-v3-e-{token}"


def _compile_pdf(tmp_path: Path, body: str) -> Path:
    xelatex = shutil.which("xelatex")
    if not xelatex:
        pytest.skip("XeLaTeX is required for named-destination integration tests")
    source = tmp_path / "main.tex"
    source.write_text(
        "\n".join(
            [
                r"\documentclass{article}",
                r"\usepackage[paperwidth=6in,paperheight=8in,margin=0.75in]{geometry}",
                r"\usepackage{hyperref}",
                r"\pagestyle{empty}",
                r"\begin{document}",
                body,
                r"\end{document}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    source_sha256 = PRODUCER.sha256_file(source)
    subprocess.run(
        [
            xelatex,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "main.tex",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert PRODUCER.sha256_file(source) == source_sha256
    return tmp_path / "main.pdf"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _provenance_fixture(
    tmp_path: Path,
    *,
    final_pdf: Path,
    render_node_ids: list[str],
) -> dict[str, object]:
    run = tmp_path / "run"
    run.mkdir()
    blocks = []
    emissions = []
    nodes = []
    for index, render_node_id in enumerate(render_node_ids, 1):
        block_id = f"source-{index}"
        start_destination, end_destination = _destination_pair(render_node_id)
        blocks.append(
            {
                "record_type": "canonical_block",
                "block_id": block_id,
                "pdf_physical_page": index,
            }
        )
        emissions.append(
            {
                "render_node_id": render_node_id,
                "source_block_ids": [block_id],
                "page_provenance": {
                    "method": "pdf_named_destination_interval",
                    "start_destination": start_destination,
                    "end_destination": end_destination,
                },
            }
        )
        nodes.append(
            {
                "render_node_id": render_node_id,
                "source_block_ids": [block_id],
            }
        )

    ledger = run / "canonical_block_ledger.jsonl"
    ledger.write_text(
        "".join(json.dumps(item) + "\n" for item in blocks),
        encoding="utf-8",
    )
    execution = run / "render_execution.json"
    plan = run / "render_plan.json"
    contract = run / "template_contract.json"
    presentation = run / "presentation_config.json"
    _write_json(execution, {"emissions": emissions})
    _write_json(plan, {"nodes": nodes})
    _write_json(contract, {"schema_version": "template-contract/2.0"})
    _write_json(presentation, {"schema_version": "spec05-presentation-config/1.0"})

    with fitz.open(final_pdf) as document:
        page_count = document.page_count
    render_pack = {
        "page_count": page_count,
        "pages": [
            {
                "index": index,
                "raster_sha256": hashlib.sha256(
                    f"generic-raster-{index}".encode("utf-8")
                ).hexdigest(),
            }
            for index in range(1, page_count + 1)
        ],
    }
    _write_json(run / "final_render_pack/manifest.json", render_pack)
    return {
        "run": run,
        "final_pdf": final_pdf,
        "render_pack": render_pack,
        "render_execution_path": execution,
        "canonical_ledger_path": ledger,
        "render_plan_path": plan,
        "volume_partition_path": None,
        "template_contract_path": contract,
        "presentation_config_path": presentation,
        "volume_id": "volume-01",
    }


def test_renderer_emits_only_standard_named_destinations(tmp_path: Path) -> None:
    render_node_id = "generic-node"
    payload = {"text": "Generic body text."}
    plan = {
        "nodes": [
            {
                "render_node_id": render_node_id,
                "source_block_ids": ["source-1"],
                "target_construct": "paragraph",
                "construct_parameters": {},
                "payload": payload,
                "payload_hash": RENDERER.canonical_hash(payload),
            }
        ]
    }
    project = tmp_path / "project"
    project.mkdir()
    rendered, emissions, _, _ = RENDERER.serialize(
        plan,
        project,
        [],
        None,
        None,
        tmp_path,
    )

    start_destination, end_destination = _destination_pair(render_node_id)
    rendered_text = rendered.decode("utf-8")
    assert rf"\hypertarget{{{start_destination}}}{{}}" in rendered_text
    assert rf"\hypertarget{{{end_destination}}}{{}}" in rendered_text
    assert emissions[0]["page_provenance"] == {
        "method": "pdf_named_destination_interval",
        "start_destination": start_destination,
        "end_destination": end_destination,
    }
    assert r"\newcommand" not in rendered_text
    assert r"\newenvironment" not in rendered_text


def test_one_page_named_destination_provenance(tmp_path: Path) -> None:
    start, end = _destination_pair("node-1")
    pdf = _compile_pdf(
        tmp_path,
        rf"\hypertarget{{{start}}}{{}}Generic body.\hypertarget{{{end}}}{{}}",
    )
    manifest = PRODUCER.build_final_pdf_page_provenance(
        **_provenance_fixture(tmp_path, final_pdf=pdf, render_node_ids=["node-1"])
    )

    assert manifest["mapping_status"] == "passed"
    assert manifest["node_intervals"][0]["start_candidate_page"] == 1
    assert manifest["node_intervals"][0]["end_candidate_page"] == 1
    assert manifest["pages"] == [
        {
            "candidate_page": 1,
            "candidate_raster_sha256": manifest["pages"][0][
                "candidate_raster_sha256"
            ],
            "disposition": "source_body",
            "generated_role": None,
            "render_node_ids": ["node-1"],
            "source_block_ids": ["source-1"],
            "source_pages": [1],
        }
    ]


def test_multi_page_named_destination_interval(tmp_path: Path) -> None:
    start, end = _destination_pair("node-1")
    pdf = _compile_pdf(
        tmp_path,
        "\n".join(
            [
                rf"\hypertarget{{{start}}}{{}}First body page.",
                r"\newpage",
                rf"Second body page.\hypertarget{{{end}}}{{}}",
            ]
        ),
    )
    manifest = PRODUCER.build_final_pdf_page_provenance(
        **_provenance_fixture(tmp_path, final_pdf=pdf, render_node_ids=["node-1"])
    )

    assert manifest["mapping_status"] == "passed"
    assert [
        (page["candidate_page"], page["render_node_ids"])
        for page in manifest["pages"]
    ] == [(1, ["node-1"]), (2, ["node-1"])]


def test_pages_before_first_named_destination_are_frontmatter(tmp_path: Path) -> None:
    start, end = _destination_pair("node-1")
    pdf = _compile_pdf(
        tmp_path,
        "\n".join(
            [
                "Generated frontmatter.",
                r"\newpage",
                rf"\hypertarget{{{start}}}{{}}Generic body."
                rf"\hypertarget{{{end}}}{{}}",
            ]
        ),
    )
    manifest = PRODUCER.build_final_pdf_page_provenance(
        **_provenance_fixture(tmp_path, final_pdf=pdf, render_node_ids=["node-1"])
    )

    assert [
        (page["candidate_page"], page["disposition"], page["generated_role"])
        for page in manifest["pages"]
    ] == [
        (1, "generated_frontmatter", "template_frontmatter"),
        (2, "source_body", None),
    ]


def test_missing_named_destination_fails_closed(tmp_path: Path) -> None:
    start, _ = _destination_pair("node-1")
    pdf = _compile_pdf(
        tmp_path,
        rf"\hypertarget{{{start}}}{{}}Body with no end destination.",
    )
    fixture = _provenance_fixture(
        tmp_path,
        final_pdf=pdf,
        render_node_ids=["node-1"],
    )

    with pytest.raises(
        ValueError,
        match="page provenance destination interval is invalid: node-1",
    ):
        PRODUCER.build_final_pdf_page_provenance(**fixture)


def test_conflicting_named_destination_interval_fails_closed(
    tmp_path: Path,
) -> None:
    start, end = _destination_pair("node-1")
    pdf = _compile_pdf(
        tmp_path,
        "\n".join(
            [
                rf"\hypertarget{{{end}}}{{}}Premature end destination.",
                r"\newpage",
                rf"\hypertarget{{{start}}}{{}}Body starts after its end.",
            ]
        ),
    )
    fixture = _provenance_fixture(
        tmp_path,
        final_pdf=pdf,
        render_node_ids=["node-1"],
    )

    with pytest.raises(
        ValueError,
        match="page provenance destination interval is invalid: node-1",
    ):
        PRODUCER.build_final_pdf_page_provenance(**fixture)


def test_reused_named_destination_identity_fails_closed(tmp_path: Path) -> None:
    start, end = _destination_pair("node-1")
    pdf = _compile_pdf(
        tmp_path,
        rf"\hypertarget{{{start}}}{{}}Generic body.\hypertarget{{{end}}}{{}}",
    )
    fixture = _provenance_fixture(
        tmp_path,
        final_pdf=pdf,
        render_node_ids=["node-1", "node-2"],
    )
    execution_path = fixture["render_execution_path"]
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    execution["emissions"][1]["page_provenance"] = {
        "method": "pdf_named_destination_interval",
        "start_destination": start,
        "end_destination": end,
    }
    _write_json(execution_path, execution)

    with pytest.raises(
        ValueError,
        match="page provenance destination identity is invalid",
    ):
        PRODUCER.build_final_pdf_page_provenance(**fixture)
