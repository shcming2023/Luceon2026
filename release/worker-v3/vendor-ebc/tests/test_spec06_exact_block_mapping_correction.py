import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/finalize_spec06_pass.py"
SPEC = importlib.util.spec_from_file_location("finalize_spec06_pass", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_exact_block_mapping_correction_does_not_move_sibling_atoms():
    source_pages = {"pages": [{"source_pdf_page": 22}]}
    source_blocks = {
        "blocks": [
            {"block_id": "stem", "source_pdf_page": 22, "final_pdf_page": 14},
            {"block_id": "media", "source_pdf_page": 22, "final_pdf_page": 14},
            {"block_id": "option-a", "source_pdf_page": 22, "final_pdf_page": 14},
            {"block_id": "option-b", "source_pdf_page": 22, "final_pdf_page": 14},
        ]
    }
    page_review = {
        "pages": [
            {"physical_page": 14, "page_label": "12"},
            {"physical_page": 15, "page_label": "13"},
        ]
    }
    correction = {
        "source_pdf_page": 22,
        "move_final_pages": {"14": 15},
        "source_block_ids": ["option-a", "option-b"],
        "expected_moved_block_count": 2,
        "final_pdf_pages": [14, 15],
        "reason": "short options continue on the next page",
        "evidence_refs": ["source.png", "final-015.png"],
    }

    MODULE.apply_source_page_mapping_corrections(
        source_pages, source_blocks, page_review, [correction]
    )

    mapped = {item["block_id"]: item["final_pdf_page"] for item in source_blocks["blocks"]}
    assert mapped == {"stem": 14, "media": 14, "option-a": 15, "option-b": 15}
    assert source_pages["pages"][0]["final_pdf_pages"] == [14, 15]
    assert page_review["pages"][1]["mapped_source_pages"] == [22]
