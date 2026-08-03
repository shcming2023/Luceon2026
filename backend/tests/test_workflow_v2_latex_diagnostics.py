from app.workflow_v2.latex_diagnostics import parse_latex_diagnostics


def test_parse_latex_diagnostics_extracts_glyphs_and_overflow_threshold():
    report = parse_latex_diagnostics(
        "[12]\nMissing character: There is no 囲 (U+56F2) in font FandolSong/OT:script=hani;\n"
        "Overfull \\hbox (2.5pt too wide) in paragraph at lines 10--11\n"
        "Overfull \\hbox (21.9pt too wide) in paragraph at lines 20--22\n"
        "LaTeX Warning: There were undefined references.\n"
    )

    assert report["missing_character_count"] == 1
    assert report["missing_characters"][0]["codepoint"] == "U+56F2"
    assert report["missing_characters"][0]["pdf_page_hint"] == 12
    assert report["overfull_hbox_count"] == 2
    assert report["obvious_overflow_count"] == 1
    assert report["max_overfull_pt"] == 21.9
    assert report["unresolved_reference_or_resource_count"] == 1


def test_parse_latex_diagnostics_keeps_subthreshold_overflow_nonblocking():
    report = parse_latex_diagnostics(
        "Overfull \\hbox (9.99pt too wide) in paragraph at lines 1--2\n"
    )

    assert report["overfull_hbox_count"] == 1
    assert report["obvious_overflow_count"] == 0


def test_parse_latex_diagnostics_accepts_xetex_quoted_hex_codepoints():
    report = parse_latex_diagnostics(
        '[50]\nMissing character: There is no 验 ("9A8C) in font cmr10!\n'
    )

    assert report["missing_character_count"] == 1
    assert report["missing_characters"][0] == {
        "character": "验",
        "codepoint": "U+9A8C",
        "font": "cmr10",
        "pdf_page_hint": 50,
    }


def test_parse_latex_diagnostics_deduplicates_same_glyph_across_compile_passes():
    report = parse_latex_diagnostics(
        "[12]\nMissing character: There is no ° (U+B0) in font cmr10!\n"
        "[48]\nMissing character: There is no ° (U+B0) in font cmr10!\n"
    )

    assert report["missing_character_count"] == 1
    assert report["missing_character_occurrence_count"] == 2
    assert report["missing_characters"] == [{
        "character": "°",
        "codepoint": "U+B0",
        "font": "cmr10",
        "pdf_page_hint": 12,
    }]
