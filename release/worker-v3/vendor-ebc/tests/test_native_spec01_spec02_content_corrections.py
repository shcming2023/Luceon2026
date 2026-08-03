import hashlib

import pytest

from scripts.build_native_spec01_spec02 import block_content_corrections


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_exact_closed_content_correction_is_accepted():
    raw = [{"source_id": "provider:1", "page": 8, "content": "OCR candidate"}]
    config = {"block_content_corrections": [{
        "correction_id": "CORR-1",
        "source_id": "provider:1",
        "physical_page": 8,
        "status": "closed",
        "original_content_sha256": digest("OCR candidate"),
        "corrected_content": "Source-visible text",
        "rationale": "Reviewed against the source page.",
        "evidence_refs": ["evidence/source_pages/page-008.png"],
    }]}
    result = block_content_corrections(config, raw)
    assert result["provider:1"]["corrected_content"] == "Source-visible text"


@pytest.mark.parametrize("field,value", [
    ("status", "open"),
    ("original_content_sha256", "0" * 64),
    ("physical_page", 9),
    ("corrected_content", "OCR candidate"),
])
def test_unclosed_or_unbound_content_correction_is_rejected(field, value):
    raw = [{"source_id": "provider:1", "page": 8, "content": "OCR candidate"}]
    item = {
        "correction_id": "CORR-1",
        "source_id": "provider:1",
        "physical_page": 8,
        "status": "closed",
        "original_content_sha256": digest("OCR candidate"),
        "corrected_content": "Source-visible text",
        "rationale": "Reviewed against the source page.",
        "evidence_refs": ["evidence/source_pages/page-008.png"],
    }
    item[field] = value
    with pytest.raises(ValueError):
        block_content_corrections({"block_content_corrections": [item]}, raw)
