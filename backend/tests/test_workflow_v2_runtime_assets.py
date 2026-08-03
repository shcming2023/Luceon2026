import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.workflow_v2.runtime.canonical.scripts.bootstrap_clean_markdown import (
    build_block_assignment_reports,
    emit_source_bound_lines,
    extract_direct_output_references,
    apply_popo_body_bounds,
    infer_body_range,
    is_noise_block,
    matches_recently_emitted_popo_heading,
    markdown_image_refs,
    normalize_math_digit_spacing,
    normalize_markdown_image_alt,
    page_is_chapter_opener,
    recover_outline_free_table_headings,
)
from app.workflow_v2.runtime.elegantbook.scripts.clean_to_elegantbook import (
    BUNDLED_MAIN_TEMPLATE,
    NOTO_SERIF_CJK_FILENAME,
    TEMPLATE_BODY_MARKER,
    TEMPLATE_METADATA_DEFAULTS,
    apply_locked_template_body_contract,
    allow_table_token_breaks,
    allow_long_inline_math_breaks,
    allow_long_prose_breaks,
    bundle_cjk_font,
    common_preamble,
    drop_unmatched_closing_braces_in_inline_math,
    drop_unmatched_closing_braces_on_single_line_arrays,
    fit_image_dense_tabularx,
    fit_short_label_tabularx,
    fit_long_unbreakable_display_arrays,
    fit_ultrawide_tabularx,
    merge_sparse_longtable_rows,
    normalize_full_width_block_indentation,
    normalize_array_column_counts,
    normalize_nested_outer_array_columns,
    normalize_locked_body_unicode,
    normalize_table_cell_layout,
    remove_dollar_only_table_cells_final,
    remove_stray_label_braces_after_array_specs,
    repair_rowbreaks_inside_text_commands,
    repair_malformed_array_endings,
    repair_resizebox_math_array_closures,
    scale_extreme_inline_math_spans,
    split_long_set_definition_lines,
    split_dense_formula_choice_lines,
    split_dense_answer_branches,
    split_multi_set_assignment_math,
    strip_inline_delimiters_inside_display_math,
    replace_circled_equation_tags,
    repair_textcircled_math_symbols,
    repair_bare_boxed_before_rowbreak,
    repair_bare_display_math_blanks,
    repair_underline_placeholders,
    remove_breaks_after_sizing_commands,
    repair_final_nested_math_and_text_delimiters,
    render_locked_template_main,
    split_multiple_tags_in_equation_blocks,
    wrap_bare_urls,
    wrap_prose_set_descriptions,
    repair_missing_rowbreak_before_hline,
    repair_misplaced_hline_alignment_tokens,
    repair_orphan_relation_superscripts_final,
    repair_literal_set_fraction_numerators,
    repair_math_commands_inside_text_commands,
    replace_array_equation_tags_with_visible_labels,
    replace_bare_equation_tags_with_visible_annotations,
    strip_nested_math_dollars_from_array_text,
    split_joined_math_spacing_commands,
    move_array_end_out_of_text_command,
    separate_inline_table_images,
    wrap_cjk_text_in_final_inline_math,
    wrap_cjk_text_in_final_arrays,
    wrap_arabic_runs,
    wrap_ipa_runs,
    remove_qr_ocr_captions,
)
from app.workflow_v2.runtime.elegantbook.scripts.semantic_markdown_to_cleanlatex import escape_text


RUNTIME = Path(__file__).parents[1] / "app" / "workflow_v2" / "runtime"


def test_canonical_image_markdown_is_single_line_and_accepts_bracketed_alt() -> None:
    assert normalize_markdown_image_alt("CALCULUS\nDEMO") == "CALCULUS DEMO"
    markdown = "![Join edges [AB] and [CB]](images/net.jpg)"
    assert markdown_image_refs(markdown) == {"images/net.jpg"}


def test_outline_free_table_recovers_source_evidenced_two_level_headings() -> None:
    markdown = "<!-- page_idx: 0 -->\n\n<table><tr><td>A</td></tr></table>\n\n<!-- page_idx: 1 -->\n\n**Reference List – Alphabetical**\n"
    blocks = [
        {"type": "header", "text": "A Source-Evidenced Reference List", "page_idx": 0},
        {"type": "table", "table_body": "<table><tr><td>A</td></tr></table>", "page_idx": 0},
        {"type": "header", "text": "Reference List – Alphabetical", "page_idx": 1},
    ]

    recovered, report = recover_outline_free_table_headings(markdown, blocks, 0)

    assert report["changed"] is True
    assert "# A Source-Evidenced Reference List" in recovered
    assert "## Reference List – Alphabetical" in recovered


def test_outline_free_table_recovery_does_not_invent_missing_caption() -> None:
    markdown = "<!-- page_idx: 0 -->\n\n<table><tr><td>A</td></tr></table>\n"
    blocks = [
        {"type": "header", "text": "A Source-Evidenced Reference List", "page_idx": 0},
        {"type": "table", "table_body": "<table><tr><td>A</td></tr></table>", "page_idx": 0},
    ]

    recovered, report = recover_outline_free_table_headings(markdown, blocks, 0)

    assert recovered == markdown
    assert report["changed"] is False
    assert report["reason"] == "source_table_caption_missing"


def test_array_rowbreak_closes_text_that_swallowed_balanced_math() -> None:
    source = (
        r"\begin{array}{l} = 3[x^2] \quad \text {\{taking out \frac{1}{3}\} "
        r"\\ = 3[(x-h)^2] \\ \end{array}"
    )

    repaired = repair_rowbreaks_inside_text_commands(source)

    assert r"\text {\{taking out \frac{1}{3}\}} \\ = 3" in repaired


def test_resizebox_math_array_closes_math_before_outer_argument() -> None:
    source = r"\resizebox{\linewidth}{!}{$\displaystyle \begin{array}{l}x \\ \end{array}}}$}"

    repaired = repair_resizebox_math_array_closures(source)

    assert repaired == r"\resizebox{\linewidth}{!}{$\displaystyle \begin{array}{l}x \\ \end{array}$}"
    centered = r"\begin{center}\resizebox{\linewidth}{!}{$\begin{array}{l}x\end{array}\right.}$\end{center}"
    assert repair_resizebox_math_array_closures(centered) == (
        r"\begin{center}\resizebox{\linewidth}{!}{$\begin{array}{l}x\end{array}\right.$}\end{center}"
    )


def test_array_end_does_not_swallow_right_delimiter() -> None:
    source = r"\left(\begin{array}{c c}1 & 2 \end{array \right)}"

    assert repair_malformed_array_endings(source) == r"\left(\begin{array}{c c}1 & 2 \end{array} \right)"


def test_long_urls_and_extreme_inline_math_get_print_safe_layout() -> None:
    url = "Details: http://tidesandcurrents.noaa.gov and more."
    assert wrap_bare_urls(url) == r"Details: \url{http://tidesandcurrents.noaa.gov} and more."
    assert wrap_bare_urls("Source: www.example.com/path.") == r"Source: \url{www.example.com/path}."
    assert wrap_bare_urls("Visit elt.heinle.com/explorer") == r"Visit \url{elt.heinle.com/explorer}"

    body = r"\xleftarrow{" + (r"+\quad" * 50) + "k}"
    scaled = scale_extreme_inline_math_spans(f"e ${body}$")
    assert scaled.startswith("e\n")
    assert r"\resizebox{0.96\linewidth}{!}{$\xleftarrow{" in scaled


def test_english_set_descriptions_become_breakable_math_prose() -> None:
    source = r"$M = \{multiples of 4 between 30 and 60\}$"

    wrapped = wrap_prose_set_descriptions(source)
    repaired = allow_long_inline_math_breaks("Suppose " + wrapped + " in the exercise text." * 8)

    assert r"\{\text{multiples of 4}" in repaired
    assert r"\allowbreak{}\text{ between 30 and 60}\}" in repaired

    exact_line = (
        r"3 Suppose $E = Z^{+}$ , $M = \{multiples of 4 between 30 and 60\}$ , "
        r"and $N = \{multiples of 6 between 30 and 60\}$ ."
    )
    exact_repaired = allow_long_inline_math_breaks(wrap_prose_set_descriptions(exact_line))
    assert exact_repaired.count(r"\allowbreak{}\text{") == 2


def test_long_set_definitions_split_and_hline_gets_row_boundary() -> None:
    line = (
        r"$E = \{\text{letters in the alphabet}\}$\allowbreak{} "
        r"$P = \{\text{letters in physics}\}$\allowbreak{} "
        r"$C = \{\text{letters in chemistry}\}$" + " extra" * 20
    )
    assert split_long_set_definition_lines(line).count(r"\par\noindent") == 2

    array = r"\begin{array}{c c} & 0 \\ 18 \hline & 5 \end{array}"
    assert r"18 \\ \hline" in repair_missing_rowbreak_before_hline(array)


def test_locked_body_unicode_and_ipa_use_existing_latex_capabilities() -> None:
    source = "x⁵ ∝ y, x ∈ A, x ∉ B, ∅, ●, ∠A, ∵ x, △ABC, 100°C, ⏻ and /ˈmɑːθs/"

    normalized = wrap_ipa_runs(normalize_locked_body_unicode(source))

    assert r"x\ensuremath{^{5}}" in normalized
    assert r"\ensuremath{\propto}" in normalized
    assert r"\ensuremath{\in}" in normalized
    assert r"\ensuremath{\notin}" in normalized
    assert r"\ensuremath{\varnothing}" in normalized
    assert r"\ensuremath{\bullet}" in normalized
    assert r"\ensuremath{\angle}A" in normalized
    assert r"\ensuremath{\because} x" in normalized
    assert r"\ensuremath{\triangle}ABC" in normalized
    assert r"100\ensuremath{{}^{\circ}}C" in normalized
    assert r"\fbox{\scriptsize POWER}" in normalized
    assert all(symbol not in normalized for symbol in ("∠", "∵", "△", "°", "⏻"))
    assert r"{\fontspec{Charis SIL}ˈ}" in normalized
    assert r"{\fontspec{Charis SIL}ɑː}" in normalized


def test_semantic_converter_normalizes_unicode_subscript_and_superscript_digits() -> None:
    rendered = escape_text("H₂O, CO₃, x⁵ and $a₂+b³$")

    assert r"H\ensuremath{_{2}}O" in rendered
    assert r"CO\ensuremath{_{3}}" in rendered
    assert r"x\ensuremath{^{5}}" in rendered
    assert "$a_{2}+b^{3}$" in rendered
    assert all(symbol not in rendered for symbol in "₂₃⁵")


def test_dense_answer_branches_split_without_touching_normal_prose() -> None:
    dense = (
        r"a Mean rainfall = $\frac{10+20+30}{3}=20$ mm "
        r"b Mean number of hats sold = $\frac{12+15+18}{3}=15$ hats " + "explanation " * 8
    )
    split = split_dense_answer_branches(dense)
    assert r"\par\noindent b Mean number" in split
    assert split_dense_answer_branches(split) == split
    ordinary = "a Read the passage and b choose the best answer."
    assert split_dense_answer_branches(ordinary).strip() == ordinary


def test_qr_ocr_caption_filter_requires_both_image_and_text_evidence(tmp_path) -> None:
    from PIL import Image

    image_dir = tmp_path / "images"
    image_dir.mkdir()
    qr = Image.new("L", (90, 90), 255)
    pixels = qr.load()
    for y in range(10, 80):
        for x in range(10, 80):
            if ((x // 4) + (y // 4)) % 2 == 0:
                pixels[x, y] = 0
    qr.save(image_dir / "qr.png")
    source = (
        "\\begin{figure}[H]\n\\centering\n"
        "\\includegraphics{images/qr.png}\n"
        "\\caption{日星上方二维码订购本品程量请微信}\n\\end{figure}\n"
    )

    filtered = remove_qr_ocr_captions(source, tmp_path / "input.tex", tmp_path)
    assert r"\includegraphics{images/qr.png}" in filtered
    assert r"\caption" not in filtered

    legitimate = source.replace("日星上方二维码订购本品程量请微信", "Scan to hear the audio")
    assert r"\caption{Scan to hear the audio}" in remove_qr_ocr_captions(
        legitimate, tmp_path / "input.tex", tmp_path
    )

    wide_qr = qr.resize((180, 90))
    wide_qr.save(image_dir / "wide-qr.png")
    wide = source.replace("qr.png", "wide-qr.png").replace("二维码", "二重码")
    assert r"\caption" not in remove_qr_ocr_captions(wide, tmp_path / "input.tex", tmp_path)

    photo = Image.new("L", (180, 100), 180)
    photo.save(image_dir / "photo.png")
    mismatched = source.replace("qr.png", "photo.png")
    assert r"\caption" not in remove_qr_ocr_captions(mismatched, tmp_path / "input.tex", tmp_path)


def test_literal_set_fraction_numerator_gets_missing_close() -> None:
    source = r"\frac{\{c_{1}^{\prime}\}{3})^{3}"

    assert repair_literal_set_fraction_numerators(source) == r"\frac{\{c_{1}^{\prime}\}}{3})^{3}"


def test_math_relations_are_moved_out_of_text_commands() -> None:
    source = r"\{y \mid \text{there is y\in x with y\in y}\}"

    assert repair_math_commands_inside_text_commands(source) == (
        r"\{y \mid \text{there is y} \in \text{ x with y} \in \text{ y}\}"
    )


def test_final_inline_math_wraps_cjk_without_nesting_existing_text() -> None:
    source = r"\resizebox{0.96\linewidth}{!}{$x + \text{正文} + ¥ + 曾$}"

    assert wrap_cjk_text_in_final_inline_math(source) == (
        r"\resizebox{0.96\linewidth}{!}{$x + \text{\CJKfamily{zhsong}正文} + \text{YEN} + \text{\CJKfamily{zhsong}曾}$}"
    )


def test_final_arrays_use_locked_cjk_family_only_inside_math() -> None:
    source = (
        r"正文验算 \begin{array}{r l} 验算: & 1.83 \\ "
        r"(\text{亿元}) \\ (\mathrm{亿元}) \end{array}"
    )

    repaired = wrap_cjk_text_in_final_arrays(source)

    assert repaired.startswith("正文验算 ")
    assert r"\text{\CJKfamily{zhsong}验算}" in repaired
    assert r"\text{\CJKfamily{zhsong}亿元}" in repaired
    assert r"\mathrm{\text{\CJKfamily{zhsong}亿元}}" in repaired


def test_final_inline_math_drops_only_unmatched_closing_braces() -> None:
    source = r"\resizebox{0.96\linewidth}{!}{$\frac{a}{b} + \{1, 2\}]}$}"

    assert drop_unmatched_closing_braces_in_inline_math(source) == (
        r"\resizebox{0.96\linewidth}{!}{$\frac{a}{b} + \{1, 2\}]$}"
    )


def test_single_line_array_drops_only_trailing_unmatched_closer() -> None:
    broken = r"\begin{array}{l} \text{\{difference\}} \\ \end{array}}"
    wrapped = r"\ensuremath{\begin{array}{l} x \\ \end{array}}"

    assert drop_unmatched_closing_braces_on_single_line_arrays(broken) == (
        r"\begin{array}{l} \text{\{difference\}} \\ \end{array}"
    )
    assert drop_unmatched_closing_braces_on_single_line_arrays(wrapped) == wrapped


def test_multiple_tagged_equations_become_align_rows() -> None:
    source = "\\begin{equation}\na=b \\tag{1} \\quad c=d \\tag{2}\n\\end{equation}"

    assert split_multiple_tags_in_equation_blocks(source) == (
        "\\begin{align}\na=b \\tag{1} \\\\ c=d \\tag{2}\n\\end{align}"
    )


def test_two_long_set_assignments_get_printable_line_break() -> None:
    source = (
        r"Suppose $U = \{\text{letters of the English alphabet}\}$ and "
        r"$V = \{\text{letters of the English alphabet which are vowels}\}$." + " extra" * 12
    )

    assert r"\par\noindent and $V =" in split_long_set_definition_lines(source)


def test_short_label_table_reserves_width_for_prompt() -> None:
    source = (
        r"\begin{tabularx}{\textwidth}{|*{4}{>{\raggedright\arraybackslash}X|}}"
        "\n" + r"1. Prompt & T & F & NG \\ \hline" + "\n"
        + r"2. Prompt & T & F & NG \\ \hline" + "\n"
        r"\end{tabularx}"
    )

    assert r"{ |>{\raggedright" not in fit_short_label_tabularx(source)
    assert r"{|>{\raggedright\arraybackslash}X|c|c|c|}" in fit_short_label_tabularx(source)


def test_sparse_longtable_answer_row_uses_full_width() -> None:
    row = "33. a) answer & b) answer & c) " + "value, " * 35 + r" & ~ & ~ \\ \hline"
    source = (
        r"\begin{longtable}{|p{.2\textwidth}|p{.2\textwidth}|p{.2\textwidth}|p{.2\textwidth}|p{.2\textwidth}|}"
        + "\n" + row + "\n" + r"\end{longtable}"
    )

    merged = merge_sparse_longtable_rows(source)
    assert r"\multicolumn{5}{|p{0.94\textwidth}|}" in merged
    assert " & ~ & ~ " not in merged


def test_nested_outer_array_uses_only_top_level_alignment_cells() -> None:
    source = (
        r"\begin{array}{c} head \\ \left.\begin{array}{c c}a & b\end{array}\right) value & +4"
        r" \\ under & \\ \end{array}"
    )

    normalized = normalize_nested_outer_array_columns(source)
    assert normalized.startswith(r"\begin{array}{c c}")
    assert r"\begin{array}{c c}a & b\end{array}" in normalized


def test_malformed_array_right_dot_closure_is_restored() -> None:
    source = r"\begin{array}{l}x \\ \end{array \right.}"

    assert repair_malformed_array_endings(source) == r"\begin{array}{l}x \\ \end{array} \right."


def test_malformed_array_closure_that_swallowed_command_is_restored() -> None:
    source = r"\begin{array}{l}x \\ \end{array \longrightarrow}"

    assert repair_malformed_array_endings(source) == (
        r"\begin{array}{l}x \\ \end{array} \longrightarrow"
    )


def test_cleanlatex_bridge_preserves_currency_inside_valid_math() -> None:
    source = r"Ratio $\frac{\$200\,000}{\$100\,000} = 2$."

    assert escape_text(source) == source


def test_final_orphan_relation_superscripts_are_restored() -> None:
    source = r"assuming ${}^\notin P_Q$, ${}^{^}\in S$, $x<^{^}y$, $\overline{^}$, $\wedgeb=b$, $c=\%-^$, and $x^\ 2$"

    assert repair_orphan_relation_superscripts_final(source) == (
        r"assuming $\notin P_Q$, ${}^{\wedge}\in S$, $x<^{\wedge}y$, $\overline{\wedge}$, $\wedge b=b$, $c=\%-\wedge$, and $x^{2}$"
    )


def test_elegantbook_full_width_blocks_do_not_inherit_cjk_paragraph_indent() -> None:
    source = "\\begin{tabularx}{\\textwidth}{X}\nA\\\\\n\\end{tabularx}\n\\begin{minipage}{\\linewidth}\nB\n\\end{minipage}\n"

    normalized = normalize_full_width_block_indentation(source)

    assert "\\noindent\\begin{tabularx}" in normalized
    assert "\\noindent\\begin{minipage}" in normalized
    assert normalize_full_width_block_indentation(normalized) == normalized


def test_elegantbook_preamble_has_bounded_line_break_and_cjk_fallback() -> None:
    preamble = common_preamble()

    assert "\\setlength{\\emergencystretch}{3em}" in preamble
    assert "\\xeCJKsetup{CJKmath=true}" in preamble
    assert f"fonts/{NOTO_SERIF_CJK_FILENAME}" in preamble
    assert "FontIndex=2" in preamble
    assert "\\setCJKfallbackfamilyfont{\\CJKrmdefault}{IPAexMincho}" in preamble
    assert "\\setCJKfallbackfamilyfont{zhsong}{IPAexMincho}" in preamble
    assert "\\setCJKfallbackfamilyfont{zhhei}{IPAGothic}" in preamble
    assert "fonts/NotoSansSymbols2-Regular.ttf" in preamble
    assert "\\newunicodechar{◀}{\\ifmmode\\mbox{{\\luceonsymbolfont ◀}}" in preamble
    assert "\\newunicodechar{📄}{\\ifmmode\\mbox{\\fbox{\\scriptsize DOC}}" in preamble
    assert "\\newunicodechar{📌}{\\ifmmode\\mbox{\\fbox{\\scriptsize PIN}}" in preamble
    assert "\\newunicodechar{ν}{\\ensuremath{\\nu}}" in preamble
    assert "\\newunicodechar{☒}{\\ifmmode\\mbox{{\\luceonsymbolfont ☒}}" in preamble
    assert "\\newunicodechar{Ⓐ}{\\ifmmode\\mbox{\\textcircled{\\scriptsize A}}" in preamble
    assert "\\newunicodechar{⇌}{\\ensuremath{\\rightleftharpoons}}" in preamble
    assert "\\newunicodechar{∞}{\\ensuremath{\\infty}}" in preamble
    assert "\\newunicodechar{🎧}{\\ifmmode\\mbox{\\fbox{\\scriptsize AUDIO}}" in preamble
    assert "\\newunicodechar{😎}{\\ifmmode\\mbox{\\fbox{\\scriptsize COOL}}" in preamble
    assert "\\newunicodechar{♦}{\\ifmmode\\mbox{{\\luceonsymbolfont ♦}}" in preamble
    assert "\\newunicodechar{🐎}{\\ifmmode\\mbox{\\fbox{\\scriptsize HORSE}}" in preamble
    assert "\\newunicodechar{⏠}{\\ifmmode\\mbox{\\fbox{\\scriptsize HOME}}" in preamble
    assert "\\newunicodechar{➤}{\\ensuremath{\\blacktriangleright}}" in preamble
    assert "\\newfontfamily\\luceonArabicFont{Amiri}" in preamble


def test_worker_v23_main_uses_locked_template_and_only_changes_metadata_values() -> None:
    template = BUNDLED_MAIN_TEMPLATE.read_text(encoding="utf-8")
    content = "\\chapter{正文}\n正文内容\n"
    main = render_locked_template_main(
        content=content,
        title="新标题",
        subtitle="新副标题",
        author=r"A\&B",
        institute="新机构",
        date_text="July, 2026",
    )

    normalized = main
    replacements = {
        "title": TEMPLATE_METADATA_DEFAULTS["title"],
        "subtitle": TEMPLATE_METADATA_DEFAULTS["subtitle"],
        "author": r"Emily\&Sunny\&Kuma",
        "institute": TEMPLATE_METADATA_DEFAULTS["institute"],
        "date": TEMPLATE_METADATA_DEFAULTS["date"],
    }
    for command, value in replacements.items():
        normalized = re.sub(
            rf"(?m)^\\{command}\{{.*\}}$",
            lambda _match, command=command, value=value: rf"\{command}{{{value}}}",
            normalized,
            count=1,
        )
    generated_prefix = normalized.split(TEMPLATE_BODY_MARKER, 1)[0]
    template_prefix = template.split(TEMPLATE_BODY_MARKER, 1)[0]
    assert generated_prefix == template_prefix
    assert content.strip() in main
    assert r"\input{chapters/content.tex}" not in main


def test_locked_template_body_contract_uses_no_new_custom_definitions() -> None:
    source = "℃ ○ ◯ ✓ ↘ ★ ☆ ▲ ■ ◇ ◎ ▽ ⓞ\n\\begin{vocabbox}\nword\n\\end{vocabbox}\n\\exerciseheading{Practice}\n"

    normalized = apply_locked_template_body_contract(source)

    assert "℃" not in normalized
    assert r"\ensuremath{^\circ\mathrm{C}}" in normalized
    assert r"\ensuremath{\bigcirc}" in normalized
    assert r"\ding{51}" in normalized
    assert r"\ensuremath{\searrow}" in normalized
    assert r"\ensuremath{\bigstar}" in normalized
    assert r"\ensuremath{\blacktriangle}" in normalized
    assert r"\ensuremath{\blacksquare}" in normalized
    assert r"\ensuremath{\diamond}" in normalized
    assert r"\ensuremath{\circledcirc}" in normalized
    assert r"\ensuremath{\bigtriangledown}" in normalized
    assert not any(symbol in normalized for symbol in "■◇◎▽ⓞ")
    assert r"\begin{tcolorbox}[vocabbox,title={Word power}]" in normalized
    assert r"\subsection*{Practice}" in normalized
    assert not re.search(r"\\(?:newcommand|newenvironment|newtcolorbox|providecommand)\b", normalized)


def test_elegantbook_arabic_font_scope_does_not_leak_to_following_text() -> None:
    source = "English الطرف الصناعي 中文"

    wrapped = wrap_arabic_runs(source)

    assert wrapped == "English {\\luceonArabicFont الطرف الصناعي} 中文"


def test_elegantbook_long_prose_adds_breaks_without_dropping_content() -> None:
    token = "011100111000010101110010001100111000101100"
    image = r"\includegraphics[width=\hsize,height=0.12\textheight]{images/longfilename12345678901234567890.jpg}"
    dimension = r"\rule{2.0em}{0.4pt}"
    source = ("Photo credits " + token + "/stock.adobe.com;Crl@Name(tn)(tn)") * 8 + image + dimension

    wrapped = allow_long_prose_breaks(source)

    assert r"\allowbreak{}" in wrapped
    assert "\x07" not in wrapped
    assert wrapped.replace(r"\allowbreak{}", "") == source
    assert image in wrapped
    assert dimension in wrapped


def test_elegantbook_short_binary_and_slash_ledgers_can_break() -> None:
    binary = "011100111000010101110010001100111000101100"
    source = binary + " vision/mysteriously/treasure/amazement/marvel/dream"

    wrapped = allow_long_prose_breaks(source)

    assert wrapped.replace(r"\allowbreak{}", "") == source
    assert wrapped.count(r"\allowbreak{}") >= 6


def test_elegantbook_standard_code_ledger_can_break() -> None:
    source = "MAFS.4.NF.3.6 Use decimals. MAFS.K12.MP.3.1, MP.4.1"

    wrapped = allow_long_prose_breaks(source)

    assert wrapped.replace(r"\allowbreak{}", "") == source
    assert r".\allowbreak{}" in wrapped


def test_elegantbook_dense_formula_choices_split_without_content_loss() -> None:
    source = "A $x=1$ " + " B $y=2$ C $z=3$ D $w=4$ E $q=5$" * 8

    split = split_dense_formula_choice_lines(source)

    assert r"\par\noindent B $y=2$" in split
    assert split.replace(r"\par\noindent ", "") == source


def test_elegantbook_multi_set_assignment_math_splits_into_lines() -> None:
    source = r"$E=\{1,2\},\allowbreak{} A=\{2\},\allowbreak{} B=\{3\}$"

    split = split_multi_set_assignment_math(source)

    assert split.count(r"\par\noindent") == 2
    normalize = lambda value: value.replace(r"\par\noindent", "").replace(r"\allowbreak{}", "").replace("$", "").replace(" ", "")
    assert normalize(split) == normalize(source)


def test_elegantbook_final_display_math_repairs_are_compile_safe() -> None:
    source = "$$\n" + r"\(x \times y\) \tag{\textcircled{2}}" + "\n$$\n"

    repaired = strip_inline_delimiters_inside_display_math(source)
    repaired = replace_circled_equation_tags(repaired)

    assert r"\(" not in repaired
    assert r"\tag" not in repaired
    assert r"\quad\text{\textcircled{2}}" in repaired


def test_elegantbook_textcircled_math_symbol_is_compile_safe() -> None:
    source = (
        r"\begin{array}{r}4 \textcircled {\circ} \\ "
        r"5 \textcircled {\circledcirc} \\ 6 \textcircled{2}\end{array}"
    )

    repaired = repair_textcircled_math_symbols(source)

    assert r"4 \ensuremath{\circledcirc}" in repaired
    assert r"5 \ensuremath{\circledcirc}" in repaired
    assert r"6 \textcircled{2}" in repaired


def test_elegantbook_hline_drops_ocr_alignment_token_before_rule() -> None:
    source = r"\begin{array}{r l} & 4.2 \\ & \times 20 \\ & \hline & 84.0 \end{array}"

    repaired = repair_misplaced_hline_alignment_tokens(source)

    assert r"\\ \hline & 84.0" in repaired
    assert r"\\ & \hline" not in repaired


def test_elegantbook_final_bare_boxed_slot_gets_argument() -> None:
    assert repair_bare_boxed_before_rowbreak(r"\begin{array}{r}7 \\ + \boxed \\ \hline") == (
        r"\begin{array}{r}7 \\ + \boxed{\quad} \\ \hline"
    )
    assert repair_bare_boxed_before_rowbreak(r"\boxed \end{array}") == r"\boxed{\quad} \end{array}"


def test_elegantbook_display_underscore_blanks_become_boxes() -> None:
    source = "$$\n7 - 3 = 2 + _ _ _ \\rule{2em}{0.4pt}\n$$\n"

    repaired = repair_bare_display_math_blanks(source)

    assert r"\square\;\square\;\square" in repaired
    assert "_ _ _" not in repaired


def test_elegantbook_underline_placeholder_is_valid_in_math() -> None:
    assert repair_underline_placeholders(r"$30 + \underline{_} = 60$") == (
        r"$30 + \underline{\hspace{1.5em}} = 60$"
    )


def test_elegantbook_sizing_commands_keep_their_delimiters() -> None:
    assert remove_breaks_after_sizing_commands(r"\left\allowbreak{}(x\right\allowbreak{})") == r"\left(x\right)"


def test_elegantbook_final_nested_delimiters_are_flattened() -> None:
    source = r"x \]\[ y \text{\{\text{equal angles}\}} \text{\}"

    repaired = repair_final_nested_math_and_text_delimiters(source)

    assert repaired == r"x \quad y \text{\{equal angles\}} \text{\}}"


def test_final_array_column_normalization_handles_multiple_inline_arrays() -> None:
    source = (
        r"$\begin{array}{l} 2 \\ 3 \end{array} \quad "
        r"\begin{array}{c c} \hline & 2 & 3 \\ \hline & E & O \end{array}$"
    )

    normalized = normalize_array_column_counts(source)

    assert r"\begin{array}{c c c}" in normalized


def test_final_array_cleanup_removes_only_unmatched_leading_label_brace() -> None:
    source = r"\begin{array}{r l} (a)} & y = 2 \\ & x + y = 6 \end{array}"

    cleaned = remove_stray_label_braces_after_array_specs(source)

    assert cleaned == r"\begin{array}{r l} (a) & y = 2 \\ & x + y = 6 \end{array}"


def test_final_table_cleanup_removes_dollar_only_ocr_cells() -> None:
    source = (
        "\\begin{tabularx}{\\textwidth}{|X|X|}\n"
        "Reena & $ $ \\\\ \\hline\n"
        "Sharon & $$\n$\n$$$ \\\\ \\hline\n"
        "\\end{tabularx}\n"
    )

    cleaned = remove_dollar_only_table_cells_final(source)

    assert "$" not in cleaned
    assert r"Reena & \\ \hline" in cleaned


def test_table_layout_breaks_concatenated_tokens_and_separates_inline_images() -> None:
    source = (
        "\\begin{tabularx}{\\textwidth}{|X|}\n"
        r"terms:pointvertexlineparallelperpendicularbearingright "
        r"\includegraphics[width=\hsize]{images/a.jpg}Remember"
        r" \\ \hline\end{tabularx}"
    )

    normalized = allow_table_token_breaks(separate_inline_table_images(source))

    assert r"pointvertexlinep\allowbreak{}arallelperpendic\allowbreak{}ularbearingright" in normalized
    assert r"\par\noindent \includegraphics[width=\hsize]{images/a.jpg}\par\noindent Remember" in normalized


def test_long_inline_set_math_gets_invisible_break_opportunities() -> None:
    source = (
        "3 $\\mathcal{E} = \\{\\text{whole numbers from 1 to 20}\\}, "
        "A = \\{2, 4, 6, 8, 10, 12\\}, B = \\{1, 3, 5, 7, 9, 11, 13, 15\\}$ "
        "and $C = \\{3, 6, 9, 12, 15, 18\\}$."
    )

    normalized = allow_long_inline_math_breaks(source)

    assert r",\allowbreak{} " in normalized


def test_math_command_is_moved_out_of_text_inside_array() -> None:
    source = r"\begin{array}{l}\text{functions sin \theta and}\end{array}"

    normalized = repair_math_commands_inside_text_commands(source)

    assert normalized == r"\begin{array}{l}\text{functions sin } \theta \text{ and}\end{array}"


def test_scripted_variable_is_moved_out_of_text_inside_array() -> None:
    source = r"\begin{array}{l l} \text{Since e^{x} = u} \\ \Rightarrow u > 0 \end{array}"

    normalized = repair_math_commands_inside_text_commands(source)

    assert normalized == (
        r"\begin{array}{l l} \text{Since } e^{x} \text{ = u} \\ "
        r"\Rightarrow u > 0 \end{array}"
    )


def test_chemical_formula_with_bare_subscript_is_moved_out_of_text() -> None:
    source = r"\begin{array}{l}\text{+ H_2O(l)}\end{array}"

    assert repair_math_commands_inside_text_commands(source) == (
        r"\begin{array}{l}\text{+ } H_2O(l)\end{array}"
    )


def test_math_alphabet_command_is_moved_out_of_text_inside_array() -> None:
    source = r"\begin{array}{l}\text{for a \mathbb {R}, where x > 0}\end{array}"

    assert repair_math_commands_inside_text_commands(source) == (
        r"\begin{array}{l}\text{for a } \mathbb {R} \text{, where x > 0}\end{array}"
    )


def test_slanted_relation_is_moved_out_of_text_inside_array() -> None:
    source = r"\begin{array}{l}\text{where x \geqslant 0}\end{array}"

    assert repair_math_commands_inside_text_commands(source) == (
        r"\begin{array}{l}\text{where x } \geqslant \text{ 0}\end{array}"
    )


def test_approximation_relation_is_moved_out_of_text_inside_array() -> None:
    source = r"\begin{array}{l}\text{x \approx 11.7}\end{array}"

    assert repair_math_commands_inside_text_commands(source) == (
        r"\begin{array}{l}\text{x } \approx \text{ 11.7}\end{array}"
    )


def test_triangle_command_is_moved_out_of_text_inside_array() -> None:
    source = r"\begin{array}{l}\text{angles in a \triangle}\end{array}"

    assert repair_math_commands_inside_text_commands(source) == (
        r"\begin{array}{l}\text{angles in a } \triangle\end{array}"
    )


def test_array_end_swallowed_by_nested_text_is_moved_out() -> None:
    source = (
        r"\begin{array}{l}\text{\{angles in a \text{triangle}\} "
        r"\end{array}}"
    )

    assert move_array_end_out_of_text_command(source) == (
        r"\begin{array}{l}\text{\{angles in a \text{triangle}\}} \end{array}"
    )


def test_second_array_end_swallowed_by_text_is_moved_out() -> None:
    source = (
        r"\begin{array}{r}x\end{array}\quad "
        r"\begin{array}{l}\text{angles \end{array}}"
    )

    assert move_array_end_out_of_text_command(source) == (
        r"\begin{array}{r}x\end{array}\quad "
        r"\begin{array}{l}\text{angles} \end{array}"
    )


def test_array_end_is_repaired_after_ocr_text_rows_are_split() -> None:
    source = (
        r"\begin{array}{l}\text{first row\\ "
        r"\text{second row \end{array}}}"
    )

    split = repair_rowbreaks_inside_text_commands(source)
    repaired = move_array_end_out_of_text_command(split)
    repaired = drop_unmatched_closing_braces_on_single_line_arrays(repaired)

    assert repaired == (
        r"\begin{array}{l}\text{first row} \\ "
        r"\text{second row} \end{array}"
    )


def test_equation_tag_inside_array_becomes_visible_label() -> None:
    source = r"\begin{array}{l} x=1 \tag{A} \\ \end{array}"

    assert replace_array_equation_tags_with_visible_labels(source) == (
        r"\begin{array}{l} x=1 \quad\text{(A)} \\ \end{array}"
    )


def test_bare_ocr_tag_inside_array_becomes_visible_annotation() -> None:
    source = r"\begin{array}{l} x=1 \tag Evaluate at the limits. \\ \end{array}"

    assert replace_array_equation_tags_with_visible_labels(source) == (
        r"\begin{array}{l} x=1 \quad\text{Evaluate at the limits}. \\ \end{array}"
    )


def test_nested_math_dollars_are_removed_from_text_inside_array() -> None:
    source = r"$\begin{array}{l}\text{Midpoint of $AB = } \frac{1}{2}\end{array}$"

    assert strip_nested_math_dollars_from_array_text(source) == (
        r"$\begin{array}{l}\text{Midpoint of AB = } \frac{1}{2}\end{array}$"
    )


def test_nested_math_dollars_are_removed_from_text_with_nested_braces() -> None:
    source = (
        r"$\begin{array}{l}\text{Result = (-1,\allowbreak{} 1)$} "
        r"\\ \end{array}$"
    )
    assert strip_nested_math_dollars_from_array_text(source) == (
        r"$\begin{array}{l}\text{Result = (-1,\allowbreak{} 1)} "
        r"\\ \end{array}$"
    )


def test_escaped_currency_dollar_is_preserved_inside_array_text() -> None:
    source = r"$\begin{array}{l}\text{of \$14500}\end{array}$"

    assert strip_nested_math_dollars_from_array_text(source) == source


def test_bare_equation_tag_becomes_visible_annotation() -> None:
    source = "$$\n\\cos \\theta \\tag Solve for theta.\\}\n$$"

    assert replace_bare_equation_tags_with_visible_annotations(source) == (
        "$$\n\\cos \\theta \\quad\\text{Solve for theta}.\n$$"
    )


def test_joined_math_spacing_command_is_split_from_variable() -> None:
    source = r"x<0\quad\text{or}\quadx>1\qquady>2"

    assert split_joined_math_spacing_commands(source) == (
        r"x<0\quad\text{or}\quad x>1\qquad y>2"
    )


def test_elegantbook_bundles_configured_cjk_font_for_self_contained_compile(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.ttc"
    source.write_bytes(b"font")
    project = tmp_path / "project"
    monkeypatch.setenv("LUCEON_CJK_FONT_PATH", str(source))

    assert bundle_cjk_font(project) is True
    assert (project / "fonts" / NOTO_SERIF_CJK_FILENAME).read_bytes() == b"font"


def test_table_cell_layout_constrains_images_and_restores_long_text_arrays() -> None:
    source = (
        r"\begin{tabularx}{\textwidth}{|X|X|}" "\n"
        r"\includegraphics[width=0.30\textwidth]{images/a.png} & "
        r"$\begin{array}{c} S \\ \text{A sentence long enough to require ordinary line wrapping in a table cell.} \end{array}$ \\ \hline" "\n"
        r"\end{tabularx}"
    )

    normalized = normalize_table_cell_layout(source)

    assert r"\includegraphics[width=\hsize,height=0.12\textheight,keepaspectratio]{images/a.png}" in normalized
    assert r"\parbox{0.95\linewidth}" in normalized
    assert "A sentence long enough" in normalized
    assert normalize_table_cell_layout(normalized) == normalized


def test_ultrawide_table_scaling_is_column_driven_and_leaves_eight_columns_unchanged() -> None:
    wide = r"\begin{tabularx}{\textwidth}{|*{9}{X|}}A & B & C & D & E & F & G & H & I \\ \hline\end{tabularx}"
    boundary = r"\begin{tabularx}{\textwidth}{|*{8}{X|}}A & B & C & D & E & F & G & H \\ \hline\end{tabularx}"

    fitted = fit_ultrawide_tabularx(wide)

    assert r"\resizebox{\linewidth}{!}" in fitted
    assert r"\begin{tabular}{|c|c|c|c|c|c|c|c|c|}" in fitted
    assert fit_ultrawide_tabularx(boundary) == boundary


def test_image_dense_table_scaling_requires_multiple_images_and_three_columns() -> None:
    visual = (
        "\\begin{tabularx}{\\textwidth}{|*{4}{X|}}\n"
        r"A & \includegraphics{a.png} & \includegraphics{b.png} & D \\ \hline"
        r"\end{tabularx}"
    )
    ordinary = "\\begin{tabularx}{\\textwidth}{|*{4}{X|}}\nA & B & C & D \\\\ \\hline\n\\end{tabularx}"

    assert r"\resizebox{\linewidth}{!}" in fit_image_dense_tabularx(visual)
    assert fit_image_dense_tabularx(ordinary) == ordinary


def test_table_token_breaks_are_scoped_to_letter_slashes_inside_tables() -> None:
    source = (
        "\\begin{tabularx}{\\textwidth}{X}\n"
        "participation/participant & 24÷5=4(组)......4(人) & \\includegraphics{images/face.jpg} \\\\ \\hline\n"
        "\\end{tabularx}\n"
        "outside/unchanged\n"
    )

    normalized = allow_table_token_breaks(source)

    assert r"participation/\allowbreak{}participant" in normalized
    assert r"\includegraphics{images/face.jpg}" in normalized
    assert "images/\\allowbreak{}face.jpg" not in normalized
    assert r"4(组)\allowbreak{}......\allowbreak{}4(人)" in normalized
    assert "outside/unchanged" in normalized


def test_long_unbreakable_display_arrays_are_fitted_without_touching_short_math() -> None:
    long_body = r"\begin{array}{ll}\text{A long explanation} & x=1\end{array}" + (r"\quad\text{detail}" * 30)
    source = "$$\n" + long_body + "\n$$\n$$\n\\begin{array}{c}x=1\\end{array}\n$$"

    fitted = fit_long_unbreakable_display_arrays(source)

    assert fitted.count(r"\resizebox{\linewidth}{!}") == 1
    assert "$$\n\\begin{array}{c}x=1\\end{array}\n$$" in fitted


def test_long_boxed_display_chain_is_fitted_but_single_box_is_unchanged() -> None:
    chain = "$$\n" + (r"\boxed{\text{A long step in a reasoning chain.}}\Rightarrow" * 4) + "\n$$"
    single = "$$\n\\boxed{x=1}\n$$"

    assert r"\resizebox{\linewidth}{!}" in fit_long_unbreakable_display_arrays(chain)
    assert fit_long_unbreakable_display_arrays(single) == single


def test_canonical_body_range_uses_popo_bounds_without_importing_outline_hierarchy() -> None:
    start, end, first = apply_popo_body_bounds(
        130,
        161,
        130,
        {"available": True, "body_start_page_idx": 11, "body_end_page_idx": 161, "outline": [{"start_page_idx": 11}]},
        scope="body",
    )

    assert (start, end, first) == (11, 161, 11)


def test_explicit_body_range_overrides_popo_bounds() -> None:
    start, end, first = apply_popo_body_bounds(
        20,
        40,
        20,
        {"available": True, "body_start_page_idx": 11, "body_end_page_idx": 161},
        scope="body",
        explicit_start=20,
        explicit_end=40,
    )

    assert (start, end, first) == (20, 40, 20)


def test_later_popo_start_cannot_trim_an_earlier_evidenced_body_start() -> None:
    start, end, first = apply_popo_body_bounds(
        9,
        410,
        9,
        {"available": True, "body_start_page_idx": 14, "body_end_page_idx": 405},
        scope="body",
    )

    assert (start, end, first) == (9, 410, 9)


def test_canonical_block_assignments_use_zero_based_content_list_identity() -> None:
    blocks = [
        {"type": "text", "text": "First source block", "page_idx": 3},
        {"type": "text", "text": "Second source block", "page_idx": 3},
    ]
    units = [{
        "unit_id": "unit-0001",
        "order": 0,
        "title": "Lesson",
        "level": 2,
        "page_start": 3,
        "page_end": 3,
    }]

    assignments, *_ = build_block_assignment_reports(
        blocks,
        units,
        "<!-- page_idx: 3 -->\nFirst source block\nSecond source block\n",
        3,
        3,
        set(),
        set(),
    )

    assert [row["block_ref"] for row in assignments] == [
        "content-list-000000",
        "content-list-000001",
    ]
    assert [row["source_order"] for row in assignments] == [0, 1]
    assert [row["text_preview"] for row in assignments] == [
        "First source block",
        "Second source block",
    ]


def test_generation_time_lineage_binds_duplicate_short_text_to_exact_clean_lines() -> None:
    lines = ["<!-- page_idx: 3 -->"]
    emit_source_bound_lines(lines, "content-list-000000", ["Method"])
    emit_source_bound_lines(lines, "content-list-000001", ["Method"])
    clean_md, direct_refs = extract_direct_output_references("\n".join(lines) + "\n")
    blocks = [
        {"type": "text", "text": "Method", "page_idx": 3, "bbox": [20, 180, 300, 220]},
        {"type": "text", "text": "Method", "page_idx": 3, "bbox": [20, 420, 300, 460]},
    ]
    units = [{
        "unit_id": "unit-0001",
        "order": 0,
        "title": "Lesson",
        "level": 2,
        "page_start": 3,
        "page_end": 3,
    }]

    assignments, *_ = build_block_assignment_reports(
        blocks,
        units,
        clean_md,
        3,
        3,
        set(),
        set(),
        direct_output_refs=direct_refs,
    )

    assert "luceon-source-" not in clean_md
    assert direct_refs == {
        "content-list-000000": ["clean:clean-line-000002"],
        "content-list-000001": ["clean:clean-line-000003"],
    }
    assert [row["output_refs"] for row in assignments] == [
        ["clean:clean-line-000002"],
        ["clean:clean-line-000003"],
    ]
    assert len({row["source_locator"]["locator_sha256"] for row in assignments}) == 2
    assert [row["source_locator"]["source_order"] for row in assignments] == [0, 1]


def test_generation_time_lineage_rejects_unbalanced_markers() -> None:
    with pytest.raises(ValueError, match="unclosed"):
        extract_direct_output_references(
            "<!-- luceon-source-start: content-list-000000 -->\nMethod\n"
        )


def test_adjacent_source_heading_matches_only_an_emitted_outline_node() -> None:
    item = {
        "id": 7,
        "entry": {"start_page_idx": 20},
        "title": "Classification Essays",
        "level": 2,
    }
    by_key = {"classification essays": [item]}

    assert matches_recently_emitted_popo_heading(
        {"classification essays"}, 21, by_key, {7}
    )
    assert not matches_recently_emitted_popo_heading(
        {"classification essays"}, 22, by_key, {7}
    )
    assert not matches_recently_emitted_popo_heading(
        {"classification essays"}, 21, by_key, set()
    )
    assert not matches_recently_emitted_popo_heading(
        {"ordinary paragraph"}, 21, by_key, {7}
    )


def test_page_number_is_noise_only_at_a_real_page_edge() -> None:
    assert is_noise_block({
        "type": "page_number",
        "text": "10",
        "bbox": [700, 850, 720, 870],
    })
    assert not is_noise_block({
        "type": "page_number",
        "text": "10",
        "bbox": [660, 239, 700, 258],
    })
    assert not is_noise_block({"type": "page_number", "text": "10"})


def test_solution_heading_and_answer_numbers_do_not_move_body_start() -> None:
    blocks = [
        {"type": "text", "text": "1. First problem", "page_idx": 0},
        {"type": "text", "text": "Solution:", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "18. Later problem", "page_idx": 14},
        {"type": "text", "text": "1", "page_idx": 14},
        {"type": "text", "text": "Solution:", "text_level": 1, "page_idx": 14},
    ]

    start, end, first_text, _last_front = infer_body_range(blocks)

    assert (start, first_text) == (0, 0)
    assert end == 14


def test_canonical_range_preserves_trailing_answer_section_until_true_back_matter() -> None:
    blocks = [
        {"type": "text", "text": "Chapter 1", "text_level": 1, "page_idx": 1},
        {"type": "text", "text": "Teaching content", "page_idx": 2},
        {"type": "header", "text": "Answers", "page_idx": 10},
        {"type": "text", "text": "1. Answer one", "page_idx": 10},
        {"type": "header", "text": "Answers", "page_idx": 11},
        {"type": "text", "text": "2. Answer two", "page_idx": 11},
        {"type": "text", "text": "Index", "page_idx": 12},
    ]

    assert infer_body_range(blocks)[1] == 9
    assert infer_body_range(blocks, preserve_answer_sections=True)[1] == 11


def test_numbered_heading_with_substantive_body_is_a_chapter_opener() -> None:
    opener = [
        {"type": "text", "text": "Operations", "text_level": 1},
        {"type": "text", "text": "1 Number relationships", "text_level": 1},
        {"type": "text", "text": "A substantive teaching paragraph that introduces the first concept."},
        {"type": "text", "text": "A second substantive paragraph with worked instructional content."},
        {"type": "text", "text": "A third substantive paragraph explaining the method and its use."},
    ]
    toc_continuation = [
        {"type": "text", "text": f"{number} Chapter title", "text_level": 1}
        for number in range(5, 10)
    ]

    assert page_is_chapter_opener(opener)
    assert not page_is_chapter_opener(toc_continuation)


def test_math_digit_spacing_does_not_treat_escaped_currency_as_math() -> None:
    source = (
        r"Earns \$186 000 a week and was offered \$530 000 a week. "
        r"The actual calculation is $1 234 + 5 678$."
    )

    result, report = normalize_math_digit_spacing(source)

    assert r"\$186 000" in result
    assert r"\$530 000" in result
    assert "$1234 + 5678$" in result
    assert report["substitution_count"] == 2


def test_math_digit_spacing_preserves_script_boundaries() -> None:
    source = r"$\log_2 1 = 0$ and $\int_0^2 10\mathrm{e}^{-2x}\mathrm{d}x$"

    result, report = normalize_math_digit_spacing(source)

    assert result == source
    assert report["substitution_count"] == 0


def test_math_digit_spacing_repairs_ocr_numbers_but_preserves_bracket_vectors() -> None:
    source = r"$[1 2 3 4] \cdot 1 2 = [12 24 36 48]$"

    result, report = normalize_math_digit_spacing(source)

    assert result == r"$[1 2 3 4] \cdot 12 = [12 24 36 48]$"
    assert report["substitution_count"] == 1


def test_math_repair_operations_keep_full_audit_values() -> None:
    source = "$" + " + ".join(["1 234"] * 80) + "$"

    result, report = normalize_math_digit_spacing(source)

    assert report["operations"][0]["before"] == source
    assert report["operations"][0]["after"] == result
    assert len(report["operations"][0]["before"]) > 240
    assert len(report["examples"][0]["before"]) == 240


def test_elegantbook_runtime_is_self_contained() -> None:
    class_file = RUNTIME / "elegantbook" / "assets" / "elegantbook.cls"
    builder = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"

    assert class_file.stat().st_size > 40_000
    assert "BUNDLED_ELEGANTBOOK_CLASS" in builder.read_text(encoding="utf-8")


def test_elegantbook_runtime_keeps_empty_images_directory_for_text_only_projects(tmp_path: Path) -> None:
    builder = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_text_only_builder", builder)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    source = tmp_path / "input.tex"
    source.write_text("\\section{Text only}\nNo images.\n", encoding="utf-8")
    output = tmp_path / "output"

    module.write_project(SimpleNamespace(
        input=str(source),
        out_dir=str(output),
        title="Text only",
        subtitle="",
        author="",
        institute="",
        date="",
        version="Reviewed",
        extrainfo="",
        demo_images=False,
        trusted_cleanlatex=True,
    ))

    assert (output / "images").is_dir()
    assert list((output / "images").iterdir()) == []


def test_semantic_runtime_is_self_contained() -> None:
    profiles = RUNTIME / "semantic" / "profiles"
    for name in ("general_textbook", "english_grammar_workbook", "math_workbook"):
        assert (profiles / f"{name}.json").is_file()


def test_math_semantic_runtime_uses_outline_hierarchy_and_splits_problem_groups() -> None:
    script = RUNTIME / "semantic" / "scripts" / "annotate_material.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_math_semantic", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    profile = module.load_profile("math_workbook")
    markdown = """# Mathematics Holiday Workbook
## Lines and Angles
### 一、单选题
1. First question
A. One
B. Two
C. Three
D. Four
2. Second question
A. One
B. Two
C. Three
D. Four
"""

    blocks = module.parse_markdown(markdown)
    sections = module.build_sections(blocks, profile)
    assets = module.annotate_assets(blocks, sections, profile)
    review_items = module.make_review_items(sections, assets, [])

    assert [section["role"] for section in sections] == ["document_root", "content_section", "problem_group"]
    assert [asset["label"] for asset in assets] == ["1", "2"]
    assert all(asset["asset_type"] == "exercise" for asset in assets)
    assert all(len(asset["content"]["choices"]) == 4 for asset in assets)
    assert review_items == []


def test_cleanlatex_bridge_hides_internal_html_markers_from_print() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "semantic_markdown_to_cleanlatex.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_cleanlatex_bridge", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    result = module.convert_markdown(
        "<!-- source_empty_chunk: no OCR body content detected -->\n\nVisible lesson text.\n"
    )

    assert "source_empty_chunk" not in result
    assert "Visible lesson text." in result


def test_cleanlatex_bridge_keeps_blank_separated_numbered_items_in_one_list() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "semantic_markdown_to_cleanlatex.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_cleanlatex_numbering", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    result = module.convert_markdown("1. First question.\n\n2. Second question.\n\nExplanation.\n")

    assert result.count(r"\begin{enumerate}") == 1
    assert result.count(r"\item[") == 2
    assert r"\item[1.] First question." in result
    assert r"\item[2.] Second question." in result
    assert result.index(r"\end{enumerate}") < result.index("Explanation.")


def test_cleanlatex_bridge_restores_adjacent_numbered_blank_placeholders_without_private_use_leaks() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "semantic_markdown_to_cleanlatex.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_cleanlatex_blanks", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    result = module.escape_text(r"lived \_\_\_\_3\_\_\_\_ and began to \_\_\_\_4\_\_\_\_")

    assert result == r"lived \rule{2.0em}{0.4pt}3\rule{2.0em}{0.4pt} and began to \rule{2.0em}{0.4pt}4\rule{2.0em}{0.4pt}"
    assert not any(0xE000 <= ord(char) <= 0xF8FF for char in result)
    assert "LUCEONPLACEHOLDER" not in result


def test_cleanlatex_bridge_repairs_array_example_label_with_stray_brace() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "semantic_markdown_to_cleanlatex.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_cleanlatex_math_label", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    result = module.normalize_math_inline_artifacts(
        r"\begin{array}{l} 如:} a+b+c+d= \\ a+(b+c)+d= \\ \end{array}"
    )

    assert r"\begin{array}{l} \text{如:} a+b+c+d=" in result
    assert r"{l} 如:}" not in result


def test_elegantbook_runtime_keeps_micro_icons_and_labels_at_source_like_scale() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_image_scaling_builder", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.image_options_for((57, 59)) == r"width=0.04\textwidth"
    assert module.image_options_for((51, 148)) == r"height=0.08\textheight"
    assert module.image_options_for((148, 51)) == r"width=0.10\textwidth"
    assert module.image_options_for((900, 600)) == r"width=0.48\textwidth"


def test_elegantbook_runtime_removes_small_source_ornament_before_chapter() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_chapter_ornament", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    source = (
        "% source_page_idx: 6\n"
        "\\begin{figure}[H]\n\\centering\n"
        "\\includegraphics[width=0.18\\textwidth]{images/chapter-one.jpg}\n"
        "\\end{figure}\n\n\\chapter{Chapter 1 运算关系}\n"
    )

    result = module.remove_small_chapter_ornament_figures(source)

    assert "chapter-one.jpg" not in result
    assert result.startswith("% source_page_idx: 6\n\\chapter{Chapter 1 运算关系}")


def test_elegantbook_runtime_splits_ocr_merged_adjacent_figure_labels() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_figure_label_builder", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    source = (
        "\\begin{figure}[H]\n\\includegraphics{x.jpg}\n\\caption{C d}\n\\end{figure}\n\n"
        "\\begin{figure}[H]\n\\includegraphics{y.jpg}\n\\end{figure}\n"
    )

    result = module.split_merged_adjacent_figure_captions(source)

    assert result.count(r"\caption{c}") == 1
    assert result.count(r"\caption{d}") == 1
    assert r"\caption{C d}" not in result


def test_elegantbook_runtime_splits_only_dense_work_out_runs() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_dense_exercise_builder", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    run = " ".join(f"{number} $x+{number}$" for number in range(1, 21))

    result = module.split_dense_numbered_exercise_runs("EXERCISE 1.1\n\nWork out:\n\n" + run + "\n")
    ordinary = module.split_dense_numbered_exercise_runs("A prose heading\n\n" + run + "\n")

    assert result.count("\n\n") >= 20
    assert ordinary.endswith(run + "\n")
    trusted = module.transform_content("Work out:\n\n" + run + "\n", trusted_cleanlatex=True)
    assert trusted.count("\n\n") >= 20


def test_elegantbook_runtime_restores_fused_numbered_arithmetic_items() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_fused_item_builder", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    source = "EXERCISE 1.1\n\n2 $8 \\div 2 + 4$ 33×4-5×2 45×6-8÷2\n"

    result = module.repair_fused_numbered_arithmetic_items(source)

    assert r"3 $3 \times 4-5 \times 2$" in result
    assert r"4 $5 \times 6-8 \div 2$" in result


def test_deterministic_candidate_contract_includes_preliminary_qa() -> None:
    runner = (Path(__file__).parents[1] / "app" / "workflow_v2" / "runner.py").read_text(encoding="utf-8")
    assert 'project_dir / "preliminary-layout-qa.json"' in runner
    assert 'qa_artifact = _artifact_by_kind(db, job.id, "preliminary-layout-qa") or candidate' in runner


def test_elegantbook_runtime_restores_parenthesized_fraction_arguments() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_elegantbook_builder", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    source = r"\frac(\frac (x^(2))(y) + \frac (y^(2))(x))(y^(2) - x y + x^(2)) = \frac(x + y)(x y)"
    repaired = module.repair_parenthesized_frac_arguments(source)

    assert r"\frac{\frac{x^(2)}{y} + \frac{y^(2)}{x}}{y^(2) - x y + x^(2)}" in repaired
    assert repaired.endswith(r"\frac{x + y}{x y}")

    transformed = module.transform_content("\\section{Solutions}\n$$\n" + source + "\n$$\n")
    assert r"\frac(" not in transformed
    assert r"\frac{" in transformed

    heading = module.transform_content(r"\subsection{II. \angle A + \angle B = \angle C}" + "\n")
    assert r"\exerciseheading{II. \ensuremath{\angle A + \angle B = \angle C}}" in heading
    second_pass = module.final_tex_safety_pass(heading)
    final_output = module.repair_exercise_heading_math_mode(module.repair_parenthesized_frac_arguments(second_pass))
    assert r"\exerciseheading{II. \ensuremath{\angle A + \angle B = \angle C}}" in final_output

    wedge = module.repair_exercise_heading_math_mode(r"\exerciseheading{III. a\wedge2 + b\wedge2 = c\wedge2}" + "\n")
    assert r"\exerciseheading{III. \ensuremath{a\wedge2 + b\wedge2 = c\wedge2}}" in wedge


def test_trusted_cleanlatex_preserves_canonical_math() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_trusted_elegantbook_builder", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    source = r"\section{Algebra}" + "\n" + r"Find $m^2 + \frac{1}{m^2}$ if $m + \frac{1}{m}=4$." + "\n"

    transformed = module.transform_content(source, trusted_cleanlatex=True)

    assert r"$m^2 + \frac{1}{m^2}$" in transformed
    assert r"\wedge" not in transformed
    assert r"\textasciicircum" not in transformed
    prose = r'"If the number $n$ is divisible by 7, then $n$ is valid."'
    assert module.transform_content(prose + "\n", trusted_cleanlatex=True).strip() == prose


def test_worker_v2_invokes_trusted_cleanlatex_builder() -> None:
    runner = (Path(__file__).parents[1] / "app" / "workflow_v2" / "runner.py").read_text(encoding="utf-8")

    assert '"--trusted-cleanlatex"' in runner


def test_trusted_cleanlatex_uses_equation_environment_for_tags() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_trusted_tag_builder", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    plain = module.normalize_trusted_display_math("$$\nx^2=1 \\tag{1}\n$$")
    array = module.normalize_trusted_display_math(
        "$$\n\\begin{array}{l}x=1 \\tag{2} \\\\ \\end{array}\n$$"
    )

    assert plain == "\\begin{equation}\nx^2=1 \\tag{1}\n\\end{equation}"
    assert r"\end{array}" in array
    assert array.index(r"\end{array}") < array.index(r"\tag{2}")
    inline = module.normalize_trusted_inline_tags(r"Result: $x^2=1 \tag{3}$.")
    assert inline == r"Result: $x^2=1 \quad\text{(3)}$."
    prose_array = module.normalize_trusted_display_math(
        "$$\n\\begin{array}{l} Let} x=1 \\\\ \\end{array}\n$$"
    )
    assert r"\begin{array}{l}\text{Let } x=1" in prose_array
    phrase_array = module.normalize_trusted_display_math(
        "$$\n\\begin{array}{l} mass   of } x=15 \\\\ \\end{array}\n$$"
    )
    assert r"\begin{array}{l}\text{mass of } x=15" in phrase_array
    option_array = module.normalize_trusted_display_math(
        "$$\n\\begin{array}{l} a) } 6x+12y \\\\ \\end{array}\n$$"
    )
    assert r"\begin{array}{l}\text{a) } 6x+12y" in option_array
    angle_text = module.normalize_trusted_display_math(
        "$$\n\\begin{array}{l}\\text {divide both sides by cos 65^{\\circ}}\\end{array}\n$$"
    )
    assert r"\text{divide both sides by } \cos 65^{\circ}" in angle_text
    step_array = module.normalize_trusted_display_math(
        "$$\n\\begin{array}{l} Step 1: Write the function as } \\gamma=x \\\\ \\end{array}\n$$"
    )
    assert r"\begin{array}{l}\text{Step 1: Write the function as } \gamma=x" in step_array
    bracket_array = module.normalize_trusted_display_math(
        "$$\n\\begin{array}{l}[a]=1 \\\\ [b]=2 \\\\ \\end{array}\n$$"
    )
    assert r"\\ {}[b]=2" in bracket_array


def test_trusted_print_residue_normalizes_visible_placeholders_and_math_spacing() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_trusted_print_builder", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    source = (
        "We introduce in this section a two-step method. This method can be used to solve any similar problems. One side. Other s\n"
        "One side Other side\n"
        r"$x=3 \text {and} y=23 \text {which is a prime.}$" + "\n"
        "CE⊥ BD\n"
        "A∪B and 42cm(to3s.f.)\n"
        "€ none of these\n"
        r"\caption{image}" + "\n"
    )

    repaired = module.normalize_trusted_print_residue(source)

    assert "problems. One side. Other s" not in repaired
    assert r"\textbf{One side / Other side}" in repaired
    assert r"\quad\text{and}\quad" in repaired
    assert r"CE\ensuremath{\perp} BD" in repaired
    assert r"A\ensuremath{\cup}B" in repaired
    assert "42cm (to 3 s.f.)" in repaired
    assert "(E) none of these" in repaired
    assert r"\caption{image}" not in repaired


def test_trusted_builder_repairs_systematic_gamma_ocr_and_duplicate_example_labels() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_trusted_ocr_profile_builder", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    gamma_text = (r"\gamma = mx + c; \gamma-intercept" + "\n") * 20
    assert r"\gamma" not in module.normalize_systematic_gamma_y_ocr(gamma_text)
    assert module.normalize_systematic_gamma_y_ocr(r"\gamma + 1") == r"\gamma + 1"
    repeated = "EXAMPLE\n\nEXAMPLE\n\n% source_page_idx: 66\nEXAMPLE\n\nBody\n"
    collapsed = module.collapse_repeated_example_labels(repeated)
    assert collapsed.count("EXAMPLE") == 1
    assert "% source_page_idx: 66\nEXAMPLE\n\nBody" in collapsed


def test_trusted_builder_moves_chapter_after_continued_solution() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_trusted_chapter_builder", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    source = r"""Method 2 (our solution):
Let $x+y=30$.
\chapter{Percentages}
% source_page_idx: 63
The score is $0.6(x+y)=18$.

OTHERS
Example 17.
"""

    repaired = module.relocate_chapter_out_of_solution_continuation(source)

    assert repaired.index("% source_page_idx: 63") < repaired.index("The score")
    assert repaired.index("The score") < repaired.index(r"\chapter{Percentages}")
    assert repaired.index(r"\chapter{Percentages}") < repaired.index("OTHERS")


def test_trusted_builder_binds_choice_group_and_moves_prechapter_figure() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_trusted_components", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    choices = "Example 14. A short question.\n\n(A) 1\n\n(B) 2\n\n(C) 3\n\n(D) 4\n\n(E) 5\n"
    figure = "\\begin{figure}[H]\n\\centering\n\\includegraphics[width=0.3\\textwidth]{images/a.jpg}\n\\end{figure}\n"
    source = "Example 12. A geometry question.\nSolution text.\n" + figure + "\\chapter{Next}\n"

    bound = module.bind_trusted_choice_groups(choices)
    relocated = module.relocate_prechapter_figure_to_question(source)

    assert bound.startswith(r"\begin{minipage}{\linewidth}")
    assert "Example 14. A short question." in bound
    assert bound.rstrip().endswith(r"\end{minipage}")
    assert relocated.index(r"\begin{figure}") < relocated.index("Solution text")
    assert relocated.index("Solution text") < relocated.index(r"\chapter{Next}")


def test_trusted_builder_moves_chapter_after_complete_example() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_trusted_example_chapter", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    source = (
        "Example 12. Geometry question.\nSolution: (B).\nFirst half.\n"
        "\\chapter{Pythagorean Theorem}\n% source_page_idx: 136\n"
        "Second half and conclusion.\nExample 13. Next question.\n"
    )

    repaired = module.relocate_chapter_out_of_example(source)

    assert repaired.index("Second half") < repaired.index(r"\chapter{Pythagorean Theorem}")
    assert repaired.index(r"\chapter{Pythagorean Theorem}") < repaired.index("Example 13")


def test_trusted_builder_starts_later_chapters_on_new_pages() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_trusted_chapter_pages", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    repaired = module.ensure_trusted_chapter_page_breaks("\\chapter{One}\nBody\n\\chapter{Two}\nMore\n")

    assert repaired.startswith(r"\chapter{One}")
    assert "Body\n\n\\clearpage\n\\chapter{Two}" in repaired


def test_trusted_builder_starts_nested_source_chapters_on_new_pages() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_nested_source_chapter_pages", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    repaired = module.ensure_nested_source_chapter_page_breaks(
        "\\chapter{Section 2}\nPrevious body\n\\section{Chapter 6: Summary writing}\nNew body\n"
    )

    assert "Previous body\n\n\\clearpage\n\\section{Chapter 6: Summary writing}" in repaired


def test_large_outline_accepts_compact_llm_review_without_echoing_every_node() -> None:
    script = RUNTIME / "canonical" / "scripts" / "build_outline_decision.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_outline_decision", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    selected = [
        {
            "order": index,
            "title": f"Chapter {index + 1}",
            "level": 1,
            "parent_title": "",
            "page": index + 1,
            "source": "contents",
            "candidate_ids": [f"candidate-{index}"],
        }
        for index in range(151)
    ]
    decision = {"selected_outline": selected, "rejected_candidates": []}

    reviewed = module.apply_llm_review(
        decision,
        [],
        {"mode": "accept_bootstrap", "verdict": "ok", "review_notes": ["Outline is coherent."]},
        {"prompt_tokens": 100, "completion_tokens": 20},
        "deepseek",
        "test-model",
    )

    assert reviewed["decision_method"] == "llm_reviewed_candidate_outline"
    assert reviewed["final_outline_count"] == 151
    assert reviewed["llm"]["verdict"] == "ok"


def test_outline_review_rejects_implicit_empty_compact_response() -> None:
    script = RUNTIME / "canonical" / "scripts" / "build_outline_decision.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_outline_reject_empty", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    decision = {"selected_outline": [], "rejected_candidates": []}

    reviewed = module.apply_llm_review(decision, [], {"verdict": "ok"}, {}, "deepseek", "test-model")

    assert reviewed.get("decision_method") != "llm_reviewed_candidate_outline"
    assert reviewed["llm"]["verdict"] == "needs_fix"


def test_outline_review_preserves_only_source_root_when_model_tries_to_empty_outline() -> None:
    script = RUNTIME / "canonical" / "scripts" / "build_outline_decision.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_outline_single_root", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    candidate = {"candidate_id": "root-1", "title_text": "2026 AMC 8 Solutions", "page": 1}
    selected = {"order": 0, "title": "2026 AMC 8 Solutions", "level": 1, "page": 1, "candidate_ids": ["root-1"]}
    decision = {"selected_outline": [selected], "rejected_candidates": []}
    review = {"mode": "replace_outline", "final_outline": [], "rejected_candidate_ids": ["root-1"], "review_notes": ["cover"]}

    reviewed = module.apply_llm_review(decision, [candidate], review, {}, "deepseek", "test-model")

    assert reviewed["decision_method"] == "llm_reviewed_candidate_outline"
    assert reviewed["final_outline_count"] == 1
    assert "Deterministic policy override" in reviewed["llm"]["review_notes"][-1]


def test_trusted_builder_compacts_tiny_figure_run_and_binds_terminal_figure() -> None:
    script = RUNTIME / "elegantbook" / "scripts" / "clean_to_elegantbook.py"
    spec = importlib.util.spec_from_file_location("workflow_v2_trusted_image_builder", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    figure = lambda name, width="0.18": (
        "\\begin{figure}[H]\n\\centering\n"
        f"\\includegraphics[width={width}\\textwidth]{{images/{name}.jpg}}\n"
        "\\caption{image}\n\\end{figure}\n"
    )

    compacted = module.compact_consecutive_tiny_figures(figure("a") + figure("b") + figure("c"))
    terminal = module.bind_terminal_figure_to_conclusion(
        figure("earlier", "0.30")
        + "This final conclusion explains the diagram and belongs with it on the same printed page.\n"
        + figure("final", "0.30")
    )
    relocated = module.relocate_terminal_problem_figure(
        "Problem 24. Solution: answer.\n"
        "The solution begins here and continues with enough explanatory content.\n"
        + figure("problem", "0.30")
    )

    assert compacted.count(r"\begin{figure}") == 0
    assert compacted.count(r"\includegraphics") == 3
    assert r"\begin{tabular}{ccc}" in compacted
    assert terminal.startswith(r"\begin{figure}")
    assert terminal.count(r"\begin{samepage}") == 1
    assert terminal.rstrip().endswith(r"\end{samepage}")
    assert relocated.index(r"\begin{figure}") < relocated.index("The solution begins")
