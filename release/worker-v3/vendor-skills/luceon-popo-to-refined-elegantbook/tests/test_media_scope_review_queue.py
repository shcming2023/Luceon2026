import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/media_scope_review_queue.py"
SPEC = importlib.util.spec_from_file_location("media_scope_review_queue", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_compact_edge_visual_is_reviewed_not_excluded() -> None:
    atom = {"bbox": [0.02, 0.03, 0.07, 0.08]}
    features = {"sample_saturated_ratio": 0.4, "aspect_ratio": 1.0}
    reasons = MODULE.candidate_reasons(atom, features)
    assert "edge_adjacent_compact_visual" in reasons
    assert "compact_colored_ui_or_marker" in reasons


def test_large_body_visual_is_not_scope_candidate() -> None:
    atom = {"bbox": [0.2, 0.2, 0.8, 0.8]}
    features = {"sample_saturated_ratio": 0.9, "aspect_ratio": 1.0}
    assert MODULE.candidate_reasons(atom, features) == []


def test_filename_and_sample_identity_are_not_inputs() -> None:
    atom = {"bbox": [0.3, 0.3, 0.35, 0.35], "material_id": "special", "title": "special"}
    features = {"sample_saturated_ratio": 0.0, "aspect_ratio": 1.0}
    assert MODULE.candidate_reasons(atom, features) == []
