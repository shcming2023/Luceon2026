#!/usr/bin/env python3
"""Convert CleanLaTeX educational material into an ElegantBook project."""

from __future__ import annotations

import argparse
import os
import re
import shutil
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
BUNDLED_ELEGANTBOOK_CLASS = SKILL_DIR / "assets" / "elegantbook.cls"
BUNDLED_MAIN_TEMPLATE = SKILL_DIR / "assets" / "template-main.tex"
BUNDLED_FIGURE_DIR = SKILL_DIR / "assets" / "figure"
TEMPLATE_BODY_MARKER = "% ———————————————————— 正文内容从这里开始 ————————————————————"
TEMPLATE_METADATA_DEFAULTS = {
    "title": "讲义模版",
    "subtitle": "高阶班",
    "author": "Emily&Sunny&Kuma",
    "institute": "橡心国际",
    "date": "June, 2025",
}
LOCKED_TEMPLATE_SYMBOL_REPLACEMENTS = {
    "℃": r"\ensuremath{^\circ\mathrm{C}}",
    "○": r"\ensuremath{\bigcirc}",
    "◯": r"\ensuremath{\bigcirc}",
    "✓": r"\ding{51}",
    "↘": r"\ensuremath{\searrow}",
    "★": r"\ensuremath{\bigstar}",
    "☆": r"\ensuremath{\star}",
    "▲": r"\ensuremath{\blacktriangle}",
    "■": r"\ensuremath{\blacksquare}",
    "◇": r"\ensuremath{\diamond}",
    "◎": r"\ensuremath{\circledcirc}",
    "▽": r"\ensuremath{\bigtriangledown}",
    "ⓞ": r"\ensuremath{\circledcirc}",
}
NOTO_SERIF_CJK_FILENAME = "NotoSerifCJK-Regular.ttc"
NOTO_SYMBOLS_FILENAME = "NotoSansSymbols2-Regular.ttf"
NOTO_SERIF_CJK_CANDIDATES = (
    Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSerifCJK.ttc"),
)
NOTO_SYMBOLS_CANDIDATES = (
    Path("/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf"),
)
TEXT_SYMBOLS = "◀▲▼⬆⬇✗☑☒☆○△◎●◇◯★◦♕♣♠♥♦✉☺⏱✿⭐"
MATH_SYMBOLS = {"ν": r"\nu", "Λ": r"\Lambda", "Φ": r"\Phi", "Σ": r"\Sigma", "Χ": r"\mathrm{X}", "∈": r"\in", "∉": r"\notin", "⊆": r"\subseteq", "∞": r"\infty", "∽": r"\backsim", "∝": r"\propto", "∠": r"\angle", "∀": r"\forall", "⊕": r"\oplus", "■": r"\blacksquare", "†": r"\dagger", "ℝ": r"\mathbb{R}", "↦": r"\mapsto", "→": r"\rightarrow", "⇌": r"\rightleftharpoons", "⋮": r"\vdots", "≫": r"\gg"}
TEXT_COMMAND_SYMBOLS = {
    "£": r"\pounds",
    "¥": r"\ifmmode\mathrm{YEN}\else YEN\fi",
    "§": r"\S",
    "📄": r"\ifmmode\mbox{\fbox{\scriptsize DOC}}\else\fbox{\scriptsize DOC}\fi",
    "📌": r"\ifmmode\mbox{\fbox{\scriptsize PIN}}\else\fbox{\scriptsize PIN}\fi",
    "⚠": r"\ifmmode\mbox{\fbox{\scriptsize !}}\else\fbox{\scriptsize !}\fi",
    "ⓞ": r"\ifmmode\mbox{\textcircled{\scriptsize o}}\else\textcircled{\scriptsize o}\fi",
    "✅": r"\ifmmode\mbox{\fbox{\scriptsize OK}}\else\fbox{\scriptsize OK}\fi",
    "😊": r"\ifmmode\mbox{\fbox{\scriptsize SMILE}}\else\fbox{\scriptsize SMILE}\fi",
    "😎": r"\ifmmode\mbox{\fbox{\scriptsize COOL}}\else\fbox{\scriptsize COOL}\fi",
    "😂": r"\ifmmode\mbox{\fbox{\scriptsize LAUGH}}\else\fbox{\scriptsize LAUGH}\fi",
    "📍": r"\ifmmode\mbox{\fbox{\scriptsize PIN}}\else\fbox{\scriptsize PIN}\fi",
    "🎧": r"\ifmmode\mbox{\fbox{\scriptsize AUDIO}}\else\fbox{\scriptsize AUDIO}\fi",
    "🎦": r"\ifmmode\mbox{\fbox{\scriptsize VIDEO}}\else\fbox{\scriptsize VIDEO}\fi",
    "🌐": r"\ifmmode\mbox{\fbox{\scriptsize WEB}}\else\fbox{\scriptsize WEB}\fi",
    "🎯": r"\ifmmode\mbox{\fbox{\scriptsize TARGET}}\else\fbox{\scriptsize TARGET}\fi",
    "📊": r"\ifmmode\mbox{\fbox{\scriptsize CHART}}\else\fbox{\scriptsize CHART}\fi",
    "⚙": r"\ifmmode\mbox{\fbox{\scriptsize SETTING}}\else\fbox{\scriptsize SETTING}\fi",
    "⏻": r"\ifmmode\mbox{\fbox{\scriptsize POWER}}\else\fbox{\scriptsize POWER}\fi",
    "🏠": r"\ifmmode\mbox{\fbox{\scriptsize HOME}}\else\fbox{\scriptsize HOME}\fi",
    "🐎": r"\ifmmode\mbox{\fbox{\scriptsize HORSE}}\else\fbox{\scriptsize HORSE}\fi",
    "🛒": r"\ifmmode\mbox{\fbox{\scriptsize CART}}\else\fbox{\scriptsize CART}\fi",
    "🍨": r"\ifmmode\mbox{\fbox{\scriptsize ICE CREAM}}\else\fbox{\scriptsize ICE CREAM}\fi",
    "🔒": r"\ifmmode\mbox{\fbox{\scriptsize LOCK}}\else\fbox{\scriptsize LOCK}\fi",
    "🌱": r"\ifmmode\mbox{\fbox{\scriptsize PLANT}}\else\fbox{\scriptsize PLANT}\fi",
    "🎬": r"\ifmmode\mbox{\fbox{\scriptsize VIDEO}}\else\fbox{\scriptsize VIDEO}\fi",
    "📞": r"\ifmmode\mbox{\fbox{\scriptsize PHONE}}\else\fbox{\scriptsize PHONE}\fi",
    "🎶": r"\ifmmode\mbox{\fbox{\scriptsize MUSIC}}\else\fbox{\scriptsize MUSIC}\fi",
    "🛇": r"\ifmmode\mbox{\fbox{\scriptsize NO ENTRY}}\else\fbox{\scriptsize NO ENTRY}\fi",
    "🖱": r"\ifmmode\mbox{\fbox{\scriptsize MOUSE}}\else\fbox{\scriptsize MOUSE}\fi",
    "🍸": r"\ifmmode\mbox{\fbox{\scriptsize DRINK}}\else\fbox{\scriptsize DRINK}\fi",
    "📁": r"\ifmmode\mbox{\fbox{\scriptsize FOLDER}}\else\fbox{\scriptsize FOLDER}\fi",
    "🌊": r"\ifmmode\mbox{\fbox{\scriptsize WAVE}}\else\fbox{\scriptsize WAVE}\fi",
    "❶": r"\ifmmode\mbox{\textcircled{\scriptsize 1}}\else\textcircled{\scriptsize 1}\fi",
    "🎛": r"\ifmmode\mbox{\fbox{\scriptsize CONTROL}}\else\fbox{\scriptsize CONTROL}\fi",
    "🎥": r"\ifmmode\mbox{\fbox{\scriptsize VIDEO}}\else\fbox{\scriptsize VIDEO}\fi",
    "🇦": r"A",
    "⏠": r"\ifmmode\mbox{\fbox{\scriptsize HOME}}\else\fbox{\scriptsize HOME}\fi",
    "⚼": r"\ifmmode\mbox{\fbox{\scriptsize SYMBOL}}\else\fbox{\scriptsize SYMBOL}\fi",
    "⭕": r"\ifmmode\mbox{\textcircled{\scriptsize O}}\else\textcircled{\scriptsize O}\fi",
    "➤": r"\ensuremath{\blacktriangleright}",
    "◆": r"\ensuremath{\blacklozenge}",
}
TEXT_COMMAND_SYMBOLS.update(
    {
        chr(codepoint): rf"\ifmmode\mbox{{\textcircled{{\scriptsize {letter}}}}}\else\textcircled{{\scriptsize {letter}}}\fi"
        for start, letters in ((0x24B6, "ABCDEFGHIJKLMNOPQRSTUVWXYZ"), (0x24D0, "abcdefghijklmnopqrstuvwxyz"))
        for codepoint, letter in enumerate(letters, start=start)
    }
)

METADATA_LABELS = (
    "语篇类型",
    "词数",
    "词段",
    "词置",
    "难度",
    "范畴",
    "范围",
    "教材链接",
)

ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X")

BOX_TITLES = {
    "Word power": "vocabbox",
    "Language tips": "tipbox",
}


def blank_rule_for(length: int) -> str:
    width = max(2.0, min(8.0, length * 0.45))
    return rf"\rule{{{width:.1f}em}}{{0.4pt}}"


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def write_text_lf(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def latex_escape_text(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in str(value or ""))


def strip_document_wrapper(text: str) -> str:
    if r"\begin{document}" in text:
        text = re.sub(r"(?s)^.*?\\begin\{document\}", "", text, count=1)
    text = re.sub(r"\\end\{document\}\s*$", "", text)
    text = drop_source_frontmatter(text)
    return text.strip() + "\n"


def drop_source_frontmatter(text: str) -> str:
    """Remove wrapper frontmatter when the input is a full exported LaTeX project."""
    text = re.sub(r"(?m)^\s*\\maketitle\s*\n?", "", text, count=1)
    text = re.sub(r"(?s)^\s*(?:%[^\n]*\n\s*)?\\begin\{titlepage\}.*?\\end\{titlepage\}\s*", "", text, count=1)
    text = re.sub(
        r"(?s)^\s*(?:\\newpage\s*)?(?:%[^\n]*\n\s*)?\\tableofcontents\s*(?:\\newpage\s*)?",
        "",
        text,
        count=1,
    )
    text = re.sub(r"(?m)^% Generated from MinerU-Popo markdown\.\s*\n?", "", text)
    text = re.sub(r"(?m)^% Export package is intentionally Overleaf-ready:.*\n?", "", text)
    return text


def normalize_heading_text(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip())


def format_heading_title(title: str) -> str:
    title = normalize_heading_text(title)
    title = title.replace(r"$\star$", "*")
    title = title.replace(r"$\blacktriangleright$", ">")
    match = re.match(r"^([IVXLCDM]+\.\s+)(.+)$", title)
    if match and re.search(r"\\(?:angle|wedge|leq|geq|neq|perp|parallel|cong|sim|sqrt)(?![A-Za-z])", match.group(2)):
        title = match.group(1) + r"\ensuremath{" + match.group(2) + "}"
    return title


def is_metadata_itemize(block: str) -> bool:
    labels = re.findall(r"\\item\[\\textbf\{([^{}]+)\}\]", block)
    if not labels:
        return False
    return all(any(key in label for key in METADATA_LABELS) for label in labels)


def wrap_metadata_blocks(text: str) -> str:
    pattern = re.compile(r"(?ms)^\\begin\{itemize\}.*?^\\end\{itemize\}")

    def repl(match: re.Match[str]) -> str:
        block = match.group(0)
        if is_metadata_itemize(block):
            block = block.replace(r"\begin{itemize}", READING_PROFILE_LIST_BEGIN, 1)
            block = block.replace(r"\end{itemize}", r"\end{description}", 1)
            return "\\begin{infobox}\n" + block + "\n\\end{infobox}"
        return block

    return pattern.sub(repl, text)


def is_exercise_title(title: str) -> bool:
    normalized = normalize_heading_text(title)
    plain = re.sub(r"\\[a-zA-Z]+|[{}$]", "", normalized).strip()
    if plain.rstrip("-").strip().lower() == "quiz":
        return True
    if re.match(r"(?i)^task\s*\d*\s*:", plain):
        return True
    if re.match(r"(?i)^[ivxlcdm]+\.\s+", plain):
        return True
    return False


def close_box(out: list[str], open_box: str | None) -> str | None:
    if open_box:
        out.append(f"\\end{{{open_box}}}")
    return None


def transform_content(text: str, *, trusted_cleanlatex: bool = False) -> str:
    text = strip_document_wrapper(text)
    text = wrap_metadata_blocks(text)

    out: list[str] = []
    open_box: str | None = None

    heading_re = re.compile(r"^\\(section|subsection|subsubsection)\*?\{(.+)\}\s*$")
    plain_task_re = re.compile(r"^(Task\s+\d+:)\s*$", re.IGNORECASE)

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        heading = heading_re.match(stripped)
        if heading:
            open_box = close_box(out, open_box)
            command, title = heading.group(1), format_heading_title(heading.group(2))

            if command == "section":
                out.append(f"\\chapter{{{title}}}")
            elif command == "subsection" and title in BOX_TITLES:
                open_box = BOX_TITLES[title]
                out.append(f"\\begin{{{open_box}}}")
            elif command == "subsection" and is_exercise_title(title):
                out.append(f"\\exerciseheading{{{title}}}")
            elif command == "subsection":
                out.append(f"\\section{{{title}}}")
            elif is_exercise_title(title):
                out.append(f"\\exerciseheading{{{title}}}")
            else:
                out.append(f"\\subsection{{{title}}}")
            continue

        plain_task = plain_task_re.match(stripped)
        if plain_task:
            open_box = close_box(out, open_box)
            out.append(f"\\exerciseheading{{{plain_task.group(1)}}}")
            continue

        if stripped.startswith(r"\begin{longtable}") or stripped.startswith(r"\begin{tabular}"):
            open_box = close_box(out, open_box)

        out.append(line)

    close_box(out, open_box)
    content = "\n".join(out).strip() + "\n"
    if not trusted_cleanlatex:
        content = normalize_obvious_ocr_artifacts(content)
    content = split_dense_numbered_exercise_runs(content)
    content = repair_fused_numbered_arithmetic_items(content)
    content = split_exercise_material_out_of_tipboxes(content)
    content = restore_singleton_exercise_headings(content)
    content = convert_plain_exercise_headings(content)
    content = merge_chapter_header_metadata(content)
    content = remove_source_page_numbers_before_chapters(content)
    content = normalize_long_text_tables(content)
    content = convert_oversized_tabularx_to_longtable(content)
    content = normalize_full_width_block_indentation(content)
    if trusted_cleanlatex:
        content = normalize_trusted_display_math(content)
        content = normalize_trusted_inline_tags(content)
        content = normalize_trusted_print_residue(content)
        content = normalize_systematic_gamma_y_ocr(content)
        content = collapse_repeated_example_labels(content)
        content = relocate_chapter_out_of_solution_continuation(content)
        content = relocate_chapter_out_of_example(content)
        content = relocate_prechapter_figure_to_question(content)
        content = ensure_trusted_chapter_page_breaks(content)
        content = ensure_nested_source_chapter_page_breaks(content)
        content = bind_trusted_choice_groups(content)
    if not trusted_cleanlatex:
        content = final_tex_safety_pass(content)
    if not trusted_cleanlatex:
        content = repair_parenthesized_frac_arguments(content)
        content = repair_exercise_heading_math_mode(content)
    return content


def normalize_full_width_block_indentation(text: str) -> str:
    """Prevent CJK paragraph indentation from pushing full-width blocks off-page."""
    pattern = re.compile(r"(?m)^(?P<indent>\s*)(?P<begin>\\begin\{(?:tabularx?|longtable|minipage)\})")
    return pattern.sub(lambda match: f"{match.group('indent')}\\noindent{match.group('begin')}", text)


def normalize_trusted_display_math(text: str) -> str:
    """Use amsmath environments for canonical display blocks carrying equation tags."""
    def replace(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        body = re.sub(
            r"(\\begin\{array\}\{[^{}]+\})\s*([A-Za-z]+(?:\s+[A-Za-z]+){0,5})\s*\}",
            lambda words: words.group(1) + rf"\text{{{' '.join(words.group(2).split())} }}",
            body,
        )
        body = re.sub(
            r"(\\begin\{array\}\{[^{}]+\})\s*([A-Za-z]\))\s*\}",
            lambda label: label.group(1) + rf"\text{{{label.group(2)} }}",
            body,
        )
        body = re.sub(
            r"(\\begin\{array\}\{[^{}]+\})\s*([A-Za-z][A-Za-z0-9 :,'()\-]{1,79})\s*\}",
            lambda prose: prose.group(1) + rf"\text{{{' '.join(prose.group(2).split())} }}",
            body,
        )
        body = re.sub(
            r"\\text\s*\{([^{}\n]*?\S)\s+(sin|cos|tan)\s+(-?\d+(?:\.\d+)?)\^\{\\circ\}\}",
            lambda angle: (
                rf"\text{{{angle.group(1)} }} \{angle.group(2)} "
                rf"{angle.group(3)}^{{\circ}}"
            ),
            body,
        )
        body = re.sub(r"\\\\\s+\[", r"\\\\ {}[", body)
        if not re.search(r"\\tag\s*\{", body):
            return "$$\n" + body + "\n$$"
        if r"\begin{array}" in body and r"\end{array}" in body:
            tags = re.findall(r"\\tag\s*\{[^{}]+\}", body)
            if len(tags) == 1:
                body = body.replace(tags[0], "", 1).rstrip()
                body = body + "\n" + tags[0]
        return "\\begin{equation}\n" + body + "\n\\end{equation}"

    return re.sub(r"(?s)\$\$\s*(.*?)\s*\$\$", replace, text)


def normalize_trusted_inline_tags(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        body = re.sub(
            r"\\tag\s*\{([^{}]+)\}",
            lambda tag: rf"\quad\text{{({tag.group(1)})}}",
            match.group(1),
        )
        return "$" + body + "$"

    return re.sub(r"\$([^$\n]+)\$", replace, text)


def normalize_trusted_print_residue(text: str) -> str:
    text = text.replace(
        "We introduce in this section a two-step method. This method can be used to solve any similar problems. One side. Other s",
        "We introduce in this section a two-step method. This method can be used to solve similar problems.",
    )
    text = re.sub(r"(?m)^One side Other side\s*$", r"\\textbf{One side / Other side}", text)
    text = re.sub(r"(?m)^€\s+", "(E) ", text)
    for symbol, command in {
        "⊥": r"\perp",
        "∪": r"\cup",
        "∩": r"\cap",
        "∧": r"\wedge",
        "∨": r"\vee",
    }.items():
        text = text.replace(symbol, rf"\ensuremath{{{command}}}")
    text = re.sub(
        r"(?i)(cm(?:\^\{?[23]\}?|[²³])?)\s*\(\s*to\s*3\s*s\.?\s*f\.?\s*\)",
        lambda match: f"{match.group(1)} (to 3 s.f.)",
        text,
    )
    text = re.sub(
        r"\\text\s*\{\s*(and|or|So|can be written|which is[^{}]*|is a prime\.?)\s*\}",
        lambda match: rf"\quad\text{{{match.group(1)}}}\quad",
        text,
        flags=re.IGNORECASE,
    )
    text = text.replace(
        "Grandfather Wen's ticket costs $6, which is 3/4 of the full price, so each ticket at full price costs (4/3) × 6 = 8 dollars, and each child's ticket costs (1/2) × 8 = 4 dollars. The cost of all the tickets is 2(\\$6 + \\$8 + $4) = \\$36.",
        "Grandfather Wen's ticket costs \\$6, which is 3/4 of the full price, so each ticket at full price costs (4/3) × 6 = 8 dollars, and each child's ticket costs (1/2) × 8 = 4 dollars. The cost of all the tickets is 2(\\$6 + \\$8 + \\$4) = \\$36.",
    )
    text = text.replace(
        "(A) $4\\sqrt{3} - \\frac{11}{6}\\pi$\n\nB. $9 = \\frac{11}{2}\\pi$\n\nC. $4\\pi$\n\nD. $5 \\pi \\mathrm{E} 4 \\sqrt{2} - \\frac{11}{8} \\pi$",
        "(A) $4\\sqrt{3} - \\frac{11}{6}\\pi$\n\n(B) $9 - \\frac{11}{2}\\pi$\n\n(C) $4\\pi$\n\n(D) $5\\pi$\n\n(E) $4\\sqrt{2} - \\frac{11}{8}\\pi$",
    )
    return re.sub(r"(?m)^\\caption\{image\}\s*$\n?", "", text)


def normalize_systematic_gamma_y_ocr(text: str) -> str:
    gamma_count = text.count(r"\gamma")
    straight_line_evidence = len(
        re.findall(r"(?:y-intercept|\\gamma\s*-?intercept|\\gamma\s*=\s*m\s*x\s*\+\s*c)", text, re.IGNORECASE)
    )
    if gamma_count < 20 or straight_line_evidence < 2:
        return text
    return text.replace(r"\gamma", "y")


def collapse_repeated_example_labels(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() != "EXAMPLE":
            output.append(lines[index])
            index += 1
            continue
        cursor = index
        labels = 0
        markers: list[str] = []
        while cursor < len(lines):
            stripped = lines[cursor].strip()
            if stripped == "EXAMPLE":
                labels += 1
            elif stripped.startswith("% source_page_idx:"):
                markers.append(lines[cursor])
            elif stripped:
                break
            cursor += 1
        if labels < 2:
            output.append(lines[index])
            index += 1
            continue
        output.extend(markers)
        output.append("EXAMPLE")
        output.append("")
        index = cursor
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(output).rstrip() + suffix


def bind_trusted_choice_groups(text: str) -> str:
    pattern = re.compile(
        r"(?P<heading>^(?:Example|Problem)\s+\d+\.[^\n]*\n(?:\s*\n)?)?"
        r"(?P<group>^\(A\)[^\n]*\n(?:\s*\n)?"
        r"^\(B\)[^\n]*\n(?:\s*\n)?"
        r"^\(C\)[^\n]*\n(?:\s*\n)?"
        r"^\(D\)[^\n]*\n(?:\s*\n)?"
        r"^\(E\)[^\n]*(?:\n|\Z))",
        re.MULTILINE,
    )
    return pattern.sub(
        lambda match: "\\begin{minipage}{\\linewidth}\n" + (match.group("heading") or "") + match.group("group").rstrip() + "\n\\end{minipage}\n",
        text,
    )


def relocate_prechapter_figure_to_question(text: str) -> str:
    chapter_re = re.compile(r"(?m)^\\chapter\{[^{}]+\}")
    figure_re = re.compile(r"\\begin\{figure\}\[H\][\s\S]*?\\end\{figure\}")
    heading_re = re.compile(r"(?m)^(?:Example|Problem)\s+\d+\.[^\n]*\n")
    for chapter in reversed(list(chapter_re.finditer(text))):
        search_start = max(0, chapter.start() - 6000)
        figures = list(figure_re.finditer(text, search_start, chapter.start()))
        if not figures:
            continue
        figure = figures[-1]
        if text[figure.end():chapter.start()].strip():
            continue
        headings = list(heading_re.finditer(text, search_start, figure.start()))
        if not headings:
            continue
        heading = headings[-1]
        figure_block = figure.group(0).strip()
        text = text[:figure.start()] + text[figure.end():]
        insert_at = heading.end()
        text = text[:insert_at] + "\n" + figure_block + "\n" + text[insert_at:]
    return text


def remove_small_chapter_ornament_figures(text: str) -> str:
    """Drop source chapter-number ornaments that would otherwise become blank float pages."""
    pattern = re.compile(
        r"(?P<marker>% source_page_idx:\s*\d+\s*\n)"
        r"\s*\\begin\{figure\}\[H\]\s*\\centering\s*"
        r"\\includegraphics\[width=(?:0\.04|0\.10|0\.18|0\.20)\\textwidth\]\{images/[^}\n]+\}\s*"
        r"\\end\{figure\}\s*"
        r"(?P<chapter>\\chapter\{[^{}]+\})"
    )
    return pattern.sub(lambda match: match.group("marker") + match.group("chapter"), text)


def relocate_chapter_out_of_example(text: str) -> str:
    chapter_re = re.compile(r"(?m)^\\chapter\{[^{}]+\}\s*\n")
    example_re = re.compile(r"(?m)^Example\s+\d+\.")
    marker_re = re.compile(r"%\s*source_page_idx:\s*\d+\s*\n")
    for chapter in reversed(list(chapter_re.finditer(text))):
        search_start = max(0, chapter.start() - 6000)
        previous = list(example_re.finditer(text, search_start, chapter.start()))
        if not previous:
            continue
        removal_end = chapter.end()
        marker = marker_re.match(text, removal_end)
        if marker:
            removal_end = marker.end()
        following = example_re.search(text, removal_end, min(len(text), removal_end + 6000))
        if not following:
            continue
        example_start = previous[-1].start()
        if "Solution" not in text[example_start:following.start()]:
            continue
        chapter_block = text[chapter.start():removal_end].strip()
        text = text[:chapter.start()] + text[removal_end:]
        insert_at = following.start() - (removal_end - chapter.start())
        text = text[:insert_at].rstrip() + "\n\n" + chapter_block + "\n" + text[insert_at:]
    return text


def ensure_trusted_chapter_page_breaks(text: str) -> str:
    chapters = list(re.finditer(r"(?m)^\\chapter\{", text))
    for chapter in reversed(chapters[1:]):
        prefix = text[:chapter.start()].rstrip()
        if prefix.endswith(r"\clearpage"):
            continue
        text = prefix + "\n\n\\clearpage\n" + text[chapter.start():]
    return text


def ensure_nested_source_chapter_page_breaks(text: str) -> str:
    """Start source-visible Chapter N sections on a fresh page under structural containers."""
    headings = list(re.finditer(r"(?mi)^\\section\{Chapter\s+\d+\b[^{}]*\}", text))
    for heading in reversed(headings):
        prefix = text[:heading.start()].rstrip()
        if not prefix or prefix.endswith(r"\clearpage"):
            continue
        text = prefix + "\n\n\\clearpage\n" + text[heading.start():]
    return text


def relocate_chapter_out_of_solution_continuation(text: str) -> str:
    pattern = re.compile(
        r"(?P<method>Method\s+\d+[^\n]*:\s*\n(?:(?!\\chapter\{).){1,900}?)"
        r"(?P<chapter>\\chapter\{[^{}]+\})\s*\n"
        r"(?P<marker>%\s*source_page_idx:\s*\d+\s*\n)?"
        r"(?P<continuation>(?:(?!\n[A-Z][A-Z ]{2,}\s*\n).){1,1200}?)"
        r"(?P<boundary>\n[A-Z][A-Z ]{2,}\s*\n)",
        re.S,
    )

    def replace(match: re.Match[str]) -> str:
        continuation = match.group("continuation")
        if not re.search(r"\$|\\begin\{equation\}|\\frac|\\times|=", continuation):
            return match.group(0)
        return (
            match.group("method")
            + (match.group("marker") or "")
            + continuation.rstrip()
            + "\n\n"
            + match.group("chapter")
            + match.group("boundary")
        )

    return pattern.sub(replace, text)


TINY_FIGURE_RE = re.compile(
    r"\\begin\{figure\}\[H\]\s*\\centering\s*"
    r"\\includegraphics\[width=0\.(?:0?\d|1[0-8])\\textwidth[^]]*\]\{(?P<path>[^{}]+)\}\s*"
    r"(?:\\caption\{image\}\s*)?\\end\{figure\}\s*",
    re.S,
)


def compact_consecutive_tiny_figures(text: str) -> str:
    run_re = re.compile(r"(?:(?:" + TINY_FIGURE_RE.pattern + r")\s*){3,}", re.S)

    def replace(match: re.Match[str]) -> str:
        paths = [row.group("path") for row in TINY_FIGURE_RE.finditer(match.group(0))]
        if len(paths) < 3:
            return match.group(0)
        cells = [rf"\includegraphics[width=0.15\textwidth]{{{path}}}" for path in paths]
        rows = [" & ".join(cells[index:index + 3]) for index in range(0, len(cells), 3)]
        return "\\begin{center}\n\\begin{tabular}{ccc}\n" + " \\\\[0.6em]\n".join(rows) + "\n\\end{tabular}\n\\end{center}\n"

    return run_re.sub(replace, text)


def relocate_terminal_problem_figure(text: str) -> str:
    figure_re = re.compile(r"\\begin\{figure\}\[H\][\s\S]*?\\end\{figure\}")
    figures = list(figure_re.finditer(text))
    if not figures or text[figures[-1].end():].strip():
        return text
    figure = figures[-1]
    search_start = max(0, figure.start() - 6000)
    heading_re = re.compile(r"(?m)^Problem\s+\d+\.\s*Solution:[^\n]*\n")
    headings = list(heading_re.finditer(text, search_start, figure.start()))
    if not headings:
        return text
    heading = headings[-1]
    if figure_re.search(text, heading.end(), figure.start()):
        return text
    figure_block = figure.group(0).strip()
    text = text[:figure.start()] + text[figure.end():]
    return text[:heading.end()] + "\n" + figure_block + "\n" + text[heading.end():]


def bind_terminal_figure_to_conclusion(text: str) -> str:
    figure_re = re.compile(r"\\begin\{figure\}\[H\][\s\S]*?\\end\{figure\}")
    figures = list(figure_re.finditer(text))
    if not figures or text[figures[-1].end():].strip():
        return text
    figure = figures[-1]
    prefix = text[:figure.start()].rstrip()
    paragraph_start = prefix.rfind("\n") + 1
    paragraph = prefix[paragraph_start:].strip()
    if len(paragraph) < 40 or len(paragraph) > 500 or "\\" in paragraph or "\n" in paragraph:
        return text
    return (
        prefix[:paragraph_start]
        + "\\begin{samepage}\n"
        + paragraph
        + "\n\n"
        + figure.group(0).strip()
        + "\n\\end{samepage}\n"
    )


def repair_exercise_heading_math_mode(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(r"\exerciseheading{") and stripped.endswith("}"):
            prefix = line[: len(line) - len(line.lstrip())]
            body = stripped[len(r"\exerciseheading{") : -1]
            line = prefix + r"\exerciseheading{" + format_heading_title(body) + "}"
        lines.append(line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def normalize_obvious_ocr_artifacts(text: str) -> str:
    """Fix recurring structural OCR artifacts without rewriting prose."""
    text = text.replace("词段", "词数").replace("词置", "词数")
    text = text.replace("©", "C")
    text = text.replace(r"\exerciseheading{Quiz-}", r"\exerciseheading{Quiz}")
    text = restore_escaped_latex_array_dumps(text)
    text = unwrap_text_wrapped_array_bodies(text)
    text = normalize_math_text_commands(text)
    text = normalize_unicode_math_symbols(text)
    text = repair_logic_symbol_dollar_noise(text)
    text = repair_numeric_dollar_artifacts(text)
    text = normalize_math_syntax_artifacts(text)
    text = repair_display_math_currency_dollars(text)
    text = strip_inline_dollars_in_display_math(text)
    text = remove_blank_lines_in_display_math(text)
    text = remove_empty_display_math_blocks(text)
    text = unwrap_suspicious_display_math_blocks(text)
    text = collapse_inline_double_dollar_markers(text)
    text = repair_math_environment_delimiters(text)
    text = repair_malformed_array_endings(text)
    text = repair_option_label_math_boundaries(text)
    text = repair_text_connectors_inside_math(text)
    text = repair_choice_marker_math_boundaries(text)
    text = repair_orphan_hat_math_closers(text)
    text = repair_orphan_fraction_math_closers(text)
    text = repair_orphan_parenthesized_math_closers(text)
    text = repair_common_inline_dollar_boundaries(text)
    text = repair_where_inside_math_boundaries(text)
    text = repair_orphan_assignment_math_closers(text)
    text = repair_orphan_math_expression_closers(text)
    text = repair_adjacent_orphan_math_runs(text)
    text = repair_interrupted_inline_math_text(text)
    text = repair_option_label_math_boundaries(text)
    text = repair_numeric_dollar_artifacts(text)
    text = repair_unbalanced_dollar_lines(text)
    text = strip_dollars_inside_text_commands(text)
    text = re.sub(r"\\framebox\b(?!\s*\{)", r"\\boxed{\\quad}", text)
    text = re.sub(r"\\underline\s*\{\s*_\s*\}", r"\\underline{\\hspace{1.5em}}", text)
    text = re.sub(r"\\boxed\s*\{\s*\}", r"\\boxed{\\quad}", text)
    text = re.sub(r"\\boxed\b(?!\s*\{)(?=\s*(?:\}|\\\\|&|\\end|\n|$))", r"\\boxed{\\quad}", text)
    text = re.sub(r"\\boxed(?!\s*\{)(?=\s*(?:\\\\|&|\\end|\n|$))", r"\\boxed{\\quad}", text)
    text = re.sub(r"\\boxed\{\\quad\}\}(?!\s*\{)", r"\\boxed{\\quad}", text)
    text = re.sub(r"(\\\\)\s*&\s*\\hline\s*&", r"\1 \\hline {} &", text)
    text = re.sub(r"\s*&\s*\\hline\s*&", " & ", text)
    text = re.sub(r"\\\\\s+(?=\[)", r"\\\\ {}", text)
    text = re.sub(r"(?m)^(\s*)\\\((?!.*\\\))", r"\1", text)
    text = re.sub(r"(\\text\s*\{\s*\\\{[^{}\n]*\\\})\s*\\\\", r"\1} \\\\", text)
    text = re.sub(r"\\text\{([^{}\n]*)\}\}(?=[)\],+\-*/=]|\\\\bullet\b|\\\\[)\]])", r"\\text{\1}", text)
    text = re.sub(r"\\text\s*\{([^{}\n]*?)\s*\\\\\s*=\s*\}", r"\\text{\1} \\\\ =", text)
    text = re.sub(r"\\text\s*\{([^{}\n]*?)\s*\\\\\s*(?=\\end\s*\{array\})", r"\\text{\1} \\\\ ", text)
    text = re.sub(r"\\text\s*\{\\\{([^{}\\\n]+)\}\}", r"\\text{\1}", text)
    text = re.sub(r"\\text\s*\{\\\}\s*(?=\\end\{array\})", r"\\text{\\}} ", text)
    text = re.sub(r"\\left\s*\\text\s*\{\s*([()\[\]|.])\s*\}", r"\\left\1", text)
    text = re.sub(r"\\right\s*\\text\s*\{\s*([()\[\]|.])\s*\}", r"\\right\1", text)
    text = re.sub(r"\\right\s*\\text\s*\{\s*\)([^{}\n]*)\}", r"\\right) \\text{\1}", text)
    text = re.sub(r"\\left\s*\\text\s*\{\s*\(([^{}\n]*)\}", r"\\left( \\text{\1}", text)
    text = re.sub(r"(\\left\(\s*[^{}\n]+)\}(?=\s*[_^])", r"\1", text)
    text = re.sub(r"\\sqrt\s*\{\s*(\\boxed\s*\{[^{}\n]*\})(?=\s*(?:=|\\\\|\\end|\$))", r"\\sqrt{\1}", text)
    text = re.sub(r"(?<=& )\${1,3}(?:\s+\${1,3})*(?=\s*\\\\)", r"\\(\\square\\)", text)
    text = re.sub(r"(?<=& )\${1,3}(?:\s+\${1,3})*(?=\s*&)", r"\\(\\square\\)", text)
    text = re.sub(
        r"\\begin\{array\}\{([^{}]*:[^{}]*)\}",
        lambda match: r"\begin{array}{" + match.group(1).replace(":", "|") + "}",
        text,
    )
    text = re.sub(r"\s*\\\]\s*\\\[\s*", r" \\quad ", text)
    text = re.sub(r"\\\]\s*\\\(", r"\\quad ", text)
    text = re.sub(
        r"\\tag\s*\{\\text\s*\{\\textcircled\{([0-9]+)\}\}\}",
        lambda match: rf"\quad\text{{(\textcircled{{{match.group(1)}}})}}",
        text,
    )
    text = re.sub(
        r"\\tag\s*\{((?:[^{}]|\{[^{}]*\})+)\}",
        lambda match: rf"\quad\text{{({match.group(1).strip()})}}",
        text,
    )
    text = re.sub(
        r"\\tag\s+([^\\$&\n]+?)(?:\\\})?(?=\s*(?:\\\\|\\end|\$|&|\n|$))",
        lambda match: rf"\quad\text{{({match.group(1).strip()})}}",
        text,
    )
    text = re.sub(r"([0-9A-Za-z])\s+\\hline", r"\1 \\\\ \\hline", text)
    text = re.sub(r"\\\\\s*(?:\\\}|\})\s*(?=\\end\{array\})", r"\\\\ ", text)
    text = remove_orphan_brace_after_row_equal(text)
    text = re.sub(r"\\end\{array\}\}", r"\\end{array}", text)
    text = re.sub(r"\\begin\{array\}\{([lcr])\}(?=[^\n]*&)", r"\\begin{array}{\1 \1}", text)
    text = normalize_array_column_counts(text)
    text = drop_unmatched_closing_braces_in_arrays(text)
    text = ensure_tabularx_row_terminators(text)
    text = repair_tabular_row_math_wrappers(text)
    text = normalize_inline_math_spans(text)
    text = repair_rowbreaks_inside_text_commands(text)
    text = close_text_before_array_end(text)
    text = wrap_bare_latex_math_runs(text)
    text = wrap_bare_high_risk_math_lines(text)
    text = merge_adjacent_inline_math_fragments(text)
    text = ensure_inline_array_math_closure(text)
    text = collapse_inline_double_dollar_markers(text)
    text = repair_table_cell_dollar_boundaries(text)
    text = repair_option_label_math_boundaries(text)
    text = repair_common_inline_dollar_boundaries(text)
    text = repair_where_inside_math_boundaries(text)
    text = repair_double_dollar_choice_markers(text)
    text = repair_fraction_list_dollar_splits(text)
    text = repair_orphan_assignment_math_closers(text)
    text = repair_orphan_math_expression_closers(text)
    text = repair_adjacent_orphan_math_runs(text)
    text = normalize_inline_math_spans(text)
    text = repair_rowbreaks_inside_text_commands(text)
    text = repair_common_inline_dollar_boundaries(text)
    text = repair_where_inside_math_boundaries(text)
    text = repair_double_dollar_choice_markers(text)
    text = collapse_inline_double_dollar_markers(text)
    text = repair_common_inline_dollar_boundaries(text)
    text = repair_orphan_math_expression_closers(text)
    text = repair_adjacent_orphan_math_runs(text)
    text = repair_redundant_inline_double_closers(text)
    text = repair_table_cell_dollar_boundaries(text)
    text = remove_empty_hline_arrays(text)
    text = remove_orphan_operator_closing_dollars(text)
    text = remove_blank_lines_in_display_math(text)
    text = remove_empty_display_math_blocks(text)
    text = unwrap_suspicious_display_math_blocks(text)
    text = remove_empty_display_math_blocks(text)
    text = wrap_bare_array_lines(text)
    text = normalize_array_column_counts(text)
    text = wrap_mixed_math_dominant_lines(text)
    text = repair_math_wrapped_items(text)
    text = repair_tabular_row_math_wrappers(text)
    text = repair_broken_tabular_row_fragments(text)
    text = wrap_bare_high_risk_math_lines(text)
    text = remove_trailing_prose_dollar_lines(text)
    text = strip_inline_dollars_in_display_math(text)
    text = repair_common_inline_dollar_boundaries(text)
    text = repair_logic_symbol_dollar_noise(text)
    text = repair_broken_tabular_row_fragments(text)
    text = repair_split_operator_variable_math(text)
    text = repair_orphan_ensuremath_closing_dollars(text)
    text = repair_orphan_math_expression_closers(text)
    text = wrap_bare_script_math_fragments_in_text(text)
    text = strip_inline_dollars_in_display_math(text)
    text = repair_mixed_operator_fraction_splits(text)
    text = repair_parenthesized_inline_math_scripts(text)
    text = repair_degree_unit_fragments(text)
    text = repair_unbalanced_dollar_lines(text)
    text = repair_redundant_inline_double_closers(text)
    text = wrap_math_dense_text_lines(text)
    text = repair_broken_tabular_row_fragments(text)
    text = merge_following_unit_math_commands(text)
    text = repair_math_wrapped_items(text)
    text = strip_nested_inline_math_delimiters(text)
    text = strip_inline_dollars_in_display_math(text)
    text = unwrap_suspicious_display_math_blocks(text)
    text = strip_inline_dollars_in_display_math(text)
    text = wrap_bare_scientific_notation(text)
    text = repair_split_mathbb_sets(text)
    text = repair_orphan_logic_power_markers(text)
    text = repair_fragmented_celsius_math(text)
    text = repair_fragmented_scientific_notation(text)
    text = repair_fragmented_mathbb_sets(text)
    text = repair_parenthesized_frac_arguments(text)
    return text


def split_dense_numbered_exercise_runs(text: str) -> str:
    """Give OCR-flattened calculation lists one printable line per item."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if len(line) < 120:
            continue
        context = "\n".join(lines[max(0, index - 4) : index]).lower()
        if "work out:" not in context:
            continue
        markers = list(re.finditer(r"(?<!\S)(\d{1,2})\s+(?=\$|\(|\{)", line))
        if len(markers) < 8:
            continue
        pieces: list[str] = []
        for marker_index, marker in enumerate(markers):
            end = markers[marker_index + 1].start() if marker_index + 1 < len(markers) else len(line)
            pieces.append(line[marker.start() : end].strip())
        prefix = line[: markers[0].start()].strip()
        lines[index] = "\n\n".join(([prefix] if prefix else []) + pieces)
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + suffix


def repair_fused_numbered_arithmetic_items(text: str) -> str:
    """Restore items where OCR fused the item number to a plain arithmetic expression."""
    pattern = re.compile(
        r"(?<!\S)(?P<label>[1-9])(?P<expr>\d+(?:\s*[×÷+\-=]\s*\d+)+)(?=\s|$)"
    )

    def replace(match: re.Match[str]) -> str:
        expression = match.group("expr").replace("×", r" \times ").replace("÷", r" \div ")
        expression = re.sub(r"\s+", " ", expression).strip()
        return f"\n\n{match.group('label')} ${expression}$"

    lines = text.splitlines()
    in_exercise = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.fullmatch(r"EXERCISE\s+\d+(?:\.\d+)?", stripped, re.I):
            in_exercise = True
        elif stripped.startswith((r"\chapter{", r"\section{", r"\subsection{")):
            in_exercise = False
        if in_exercise and ("×" in line or "÷" in line):
            lines[index] = pattern.sub(replace, line)
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + suffix


def repair_parenthesized_frac_arguments(text: str) -> str:
    """Restore OCR/Pandoc ``\frac(num)(den)`` forms with balanced arguments."""
    output: list[str] = []
    cursor = 0
    while cursor < len(text):
        found = text.find(r"\frac", cursor)
        if found < 0:
            output.append(text[cursor:])
            break
        output.append(text[cursor:found])
        command_end = found + len(r"\frac")
        first_start = command_end
        while first_start < len(text) and text[first_start].isspace():
            first_start += 1
        first = _balanced_parenthesis_span(text, first_start)
        if first is None:
            output.append(text[found:command_end])
            cursor = command_end
            continue
        second_start = first[1]
        while second_start < len(text) and text[second_start].isspace():
            second_start += 1
        second = _balanced_parenthesis_span(text, second_start)
        if second is None:
            output.append(text[found:command_end])
            cursor = command_end
            continue
        numerator = repair_parenthesized_frac_arguments(text[first[0] + 1 : first[1] - 1].strip())
        denominator = repair_parenthesized_frac_arguments(text[second[0] + 1 : second[1] - 1].strip())
        output.append(rf"\frac{{{numerator}}}{{{denominator}}}")
        cursor = second[1]
    return "".join(output)


def _balanced_parenthesis_span(value: str, start: int) -> tuple[int, int] | None:
    if start >= len(value) or value[start] != "(":
        return None
    depth = 0
    for index in range(start, len(value)):
        if value[index] == "(":
            depth += 1
        elif value[index] == ")":
            depth -= 1
            if depth == 0:
                return start, index + 1
    return None


def normalize_math_syntax_artifacts(text: str) -> str:
    """Repair OCR math fragments that are syntactically invalid but structurally clear."""
    text = text.replace(r"\textbackslash{},", ",")
    text = re.sub(r"\\(wedge|vee)([A-Za-z])\b", r"\\\1 \2", text)
    text = restore_escaped_open_math_delimiters(text)
    text = re.sub(r"(?m)^(\s*)\$\s+(?=[^\n]*(?:\\div|\\times|\\cdot|=))", r"\1", text)
    text = re.sub(r"(?<![\\}\w])\$[ \t]+(?=\d)", r"\\$ ", text)
    text = re.sub(r"(?<!\\)\$(?=\d[\d,.]*\s+(?:for|[A-Za-z]))", r"\\$", text)
    text = re.sub(r"(?<!\\)(?<=\d)%", r"\\%", text)
    text = re.sub(
        r"(?i)\b(?P<context>amount\s+above|above|over|under|below)\s+\$(?P<num>\d[\d,.]*(?:\s+\d{3})*)(?![\d,.]|[ \t]*\\)",
        lambda match: f"{match.group('context')} " + r"\$" + match.group("num"),
        text,
    )
    text = re.sub(r"(?<!\\)\$(?=\d[\d,.]*\s*&)", r"\\$", text)
    text = remove_spurious_numeric_closing_dollars(text)
    text = re.sub(r"\^\{\s*\^\s*\}", r"^{\\wedge}", text)
    text = re.sub(r"\\overline\{\s*\^\s*\}", r"\\overline{\\wedge}", text)
    text = re.sub(r"\{\}\s*\^\\(notin|neq|in|leq|geq|leqslant|geqslant)", r"\\\1", text)
    text = repair_matrix_digit_rowbreaks(text)
    text = re.sub(r"\\frac\s*\{\s*_\s*\}", r"\\frac{\\square}", text)
    text = re.sub(r"(?<!\\)(?:_\s*)+(?=\\rule\{)", "", text)
    text = re.sub(
        r"(?<!\\)(?:_\s*){2,}",
        lambda match: blank_rule_for(match.group(0).count("_")),
        text,
    )
    text = re.sub(
        r"\\frac\{\}\$([^$\n]+?)\\\}\\\{\\\}\$([^$\n]+?)\}",
        r"\\frac{\1}{\2}",
        text,
    )
    text = re.sub(
        r"\\frac\{\}\{\\square\}\$([^$\\\n]+?)\\\}\\\{\\textbackslash\{\}\$([^$}\n]+)\}\$",
        r"\\frac{\\$\1}{\\$\2}",
        text,
    )
    text = re.sub(r"(?<=[A-Za-z0-9}\)])\\\$(?=\s+\d)", "$", text)
    text = restore_fully_escaped_math_delimiters(text)
    text = restore_escaped_close_math_delimiters(text)
    text = re.sub(
        r"\\frac\s*\{\\\}\$?(?P<num>[^{}$\n]+)\}\s*\{(?P<den>[^{}$\n]+)\}",
        lambda match: rf"\frac{{{match.group('num').strip()}}}{{{match.group('den').strip()}}}",
        text,
    )
    text = re.sub(r"\\\}\$(?=\d)", r"\\$", text)
    text = repair_parenthesized_fraction_denominators(text)
    text = close_superscript_group_before_relation(text)
    text = repair_text_fraction_denominator_closures(text)
    text = repair_frac_boxed_missing_numerator_close(text)
    text = repair_missing_frac_denominators(text)
    text = repair_table_cell_dollar_boundaries(text)
    text = close_unbalanced_inline_math_in_table_rows(text)
    text = repair_unbalanced_dollar_lines(text)
    text = re.sub(r"\$(\\[A-Za-z]+)\s*\^\$", r"$\1$", text)
    text = re.sub(r"\$(\\[A-Za-z]+)\s*_\$", r"$\1$", text)
    text = re.sub(r"\$([^$\n]*\^\{[^{}]+\})'\$", r"$\1$'", text)
    return text


def unwrap_text_wrapped_array_bodies(text: str) -> str:
    """Unwrap OCR cases where an entire array body was placed inside \text{...}."""
    out: list[str] = []
    opener_re = re.compile(r"(\\begin\{array\}\{[^{}\n]*\})\s*\\text\s*\{\s*")
    for line in text.splitlines():
        match = opener_re.search(line)
        if match and not line[: match.start()].strip() and r"\end{array}" in line[match.end() :]:
            end_array = line.rfind(r"\end{array}")
            final_brace = line.rfind("}")
            if end_array != -1 and final_brace > end_array:
                line = line[: match.start()] + match.group(1) + " " + line[match.end() : final_brace] + line[final_brace + 1 :]
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_display_math_currency_dollars(text: str) -> str:
    """Escape stray currency dollars that OCR left inside display math blocks."""
    display_re = re.compile(r"\$\$(?P<body>.*?)\$\$", re.S)

    def repl(match: re.Match[str]) -> str:
        body = match.group("body")
        body = re.sub(r"(?<!\\)\$(\d+)\$(\.\d+)", r"\1\2", body)
        body = re.sub(r"(?<!\\)(\d+)\$(\.\d+)", r"\1\2", body)
        body = re.sub(r"(?<!\\)\$(?=\s*\d)", r"\\$", body)
        return "$$" + body + "$$"

    return display_re.sub(repl, text)


def strip_inline_dollars_in_display_math(text: str) -> str:
    """Remove single-dollar inline delimiters that OCR inserted inside display math."""
    display_re = re.compile(r"\$\$(?P<body>.*?)\$\$", re.S)

    def repl(match: re.Match[str]) -> str:
        body = re.sub(r"(?<!\\)\$", "", match.group("body"))
        return "$$" + body + "$$"

    return display_re.sub(repl, text)


def remove_blank_lines_in_display_math(text: str) -> str:
    """TeX display math cannot contain blank paragraph lines."""
    display_re = re.compile(r"\$\$(?P<body>.*?)\$\$", re.S)

    def repl(match: re.Match[str]) -> str:
        lines = [line for line in match.group("body").splitlines() if line.strip()]
        return "$$\n" + "\n".join(lines) + "\n$$"

    return display_re.sub(repl, text)


def remove_empty_display_math_blocks(text: str) -> str:
    """Drop empty display math pairs produced by page-layout OCR noise."""
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"(?m)^[ \t]*\$\$[ \t]*\n(?:[ \t]*\n)*[ \t]*\$\$[ \t]*\n?", "", text)
    return text


def display_body_line_is_prose(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("%"):
        return False
    stripped = re.sub(r"\\text\s*\{[^{}\n]*\}", "", stripped)
    stripped = re.sub(r"\\[A-Za-z]+(?:\s*\{[^{}\n]*\})?", " ", stripped)
    words = re.findall(r"[A-Za-z]{4,}", stripped)
    math_words = {
        "array",
        "begin",
        "boxed",
        "frac",
        "left",
        "quad",
        "right",
        "sqrt",
        "text",
        "times",
    }
    return any(word.lower() not in math_words for word in words)


def display_body_should_unwrap(lines: list[str]) -> bool:
    meaningful = [line for line in lines if line.strip()]
    if not meaningful:
        return False
    if any(re.search(r"(?<!\\)\$", line) for line in meaningful):
        return True
    structural_re = re.compile(
        r"^\s*(?:% source_page_idx:|\\(?:begin|end)\{(?:figure|enumerate|itemize|tabularx?|longtable|center)\}|"
        r"\\(?:includegraphics|caption|centering|chapter|section|subsection|exerciseheading)\b)"
    )
    if any(structural_re.search(line) for line in meaningful):
        return True
    prose_count = sum(1 for line in meaningful if display_body_line_is_prose(line))
    if prose_count and len(meaningful) > 1:
        return True
    return len(meaningful) > 6 and prose_count > 0


def unwrap_suspicious_display_math_blocks(text: str) -> str:
    """Unwrap display delimiters that accidentally surround prose, figures, or whole pages."""
    lines = text.splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() != "$$":
            out.append(lines[index])
            index += 1
            continue

        end = index + 1
        while end < len(lines) and lines[end].strip() != "$$":
            end += 1
        if end >= len(lines):
            body = lines[index + 1 :]
            if display_body_should_unwrap(body):
                out.extend(body)
                index = len(lines)
            else:
                out.append(lines[index])
                index += 1
            continue

        body = lines[index + 1 : end]
        if display_body_should_unwrap(body):
            out.extend(body)
        else:
            out.append(lines[index])
            out.extend(body)
            out.append(lines[end])
        index = end + 1

    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def collapse_inline_double_dollar_markers(text: str) -> str:
    """Collapse accidental inline $$ markers without touching standalone display delimiters."""
    out: list[str] = []
    for line in text.splitlines():
        if line.strip() != "$$":
            pieces: list[str] = []
            cursor = 0
            for match in re.finditer(r"\$\$(?=\s*(?:\\[A-Za-z]|[-+]?\d|\(|\{))", line):
                pieces.append(line[cursor : match.start()])
                prior = line[: match.start()]
                prior_dollars = len(re.findall(r"(?<!\\)\$", prior))
                pieces.append("$ $" if prior_dollars % 2 == 1 else "$")
                cursor = match.end()
            pieces.append(line[cursor:])
            line = "".join(pieces)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_math_environment_delimiters(text: str) -> str:
    """Remove OCR-inserted dollar delimiters inside math environments."""
    envs = r"(?:aligned|alignedat|gathered|split)"
    text = re.sub(
        rf"(\\begin\{{{envs}\}}(?:\{{[^{{}}\n]*\}})?)\s*\$(?=\s*(?:&|\\\\|\\[A-Za-z]|[A-Za-z0-9{{(]))",
        r"\1",
        text,
    )
    text = re.sub(
        rf"(?<=\$)(\\begin\{{{envs}\}}(?:\{{[^{{}}\n]*\}})?)\s+\$(?=\s*(?:&|\\\\|\\[A-Za-z]|[A-Za-z0-9{{(]))",
        r"\1",
        text,
    )
    return text


def repair_option_label_math_boundaries(text: str) -> str:
    """Move short exercise option labels out of a preceding math span."""
    text = re.sub(
        r"\$(?P<body>[^$\n]*?)\\text\s*\{\s*(?P<label>[a-z])\s*\}\$(?=\s*[\(\[]?[A-Za-z0-9\\])",
        lambda match: f"${match.group('body')}$ {match.group('label')} $",
        text,
    )
    text = re.sub(
        r"\$(?P<body>[^$\n]*?[=+\-*/]\s*)(?P<label>[a-z])\s*\$(?=\s*[\(\[]?[A-Za-z0-9\\])",
        lambda match: f"${match.group('body')}$ {match.group('label')} $",
        text,
    )
    return text


def repair_text_connectors_inside_math(text: str) -> str:
    """Move prose connectors such as ', then' out of a broken math span."""
    return re.sub(
        r"\$(?P<body>[^$\n]{1,240}?)\s*,\s*(?P<connector>then|where|if)\s*\$",
        lambda match: f"${match.group('body')}$, {match.group('connector')} $",
        text,
        flags=re.I,
    )


def repair_choice_marker_math_boundaries(text: str) -> str:
    """Keep choice markers out of math while reopening math for the following expression."""
    text = re.sub(
        r"\$\$\s*(?P<marker>\\textcircled\{\d+\})\s*\$?",
        lambda match: f"$ {match.group('marker')} $",
        text,
    )
    text = re.sub(
        r"(?<!\\)\$\s*(?P<marker>\\textcircled\{\d+\})\s*\$?\s*",
        lambda match: f" {match.group('marker')} $",
        text,
    )
    frac = r"(?:\d+\s*)?\\frac\s*\{\s*[^{}\n]+?\s*\}\s*\{\s*[^{}\n]+?\s*\}"
    text = re.sub(
        rf"(?P<left>{frac})\s+©\s+(?=(?:\d+\s*)?\\frac|\d)",
        lambda match: f"{match.group('left')}$ C $",
        text,
    )
    text = re.sub(r"\s+©\s+\$", " C $", text)
    return text


def repair_common_inline_dollar_boundaries(text: str) -> str:
    """Repair OCR dollar boundaries around short variables, labels, and prose connectors."""
    text = re.sub(
        r"\$(?P<body>[^$\n]{1,180}?)\s*\$\s*(?P<cmd>\\(?:mathrm|mathbf|mathit|text)\s*\{[^{}\n]+\})(?=\s*(?:[.,;:)\]]|$))",
        lambda match: f"${match.group('body').rstrip()} {match.group('cmd')}$"
        if line_math_like(match.group("body")) and not display_body_line_is_prose(match.group("body"))
        else match.group(0),
        text,
    )
    text = re.sub(
        r"\$\\(?P<style>mathbf|mathrm|mathit)\{(?P<label>[A-Za-z0-9]+)\}\$(?=\s*(?:[-+]?\d|\\frac|\\sqrt|[A-Za-z]\s*=))",
        lambda match: rf"\textbf{{{match.group('label')}}} $",
        text,
    )
    text = re.sub(
        r"\$(?P<body>[^$\n]*?=\s*)\$(?=\s*(?:\\frac|\\sqrt|[-+]?\d|[A-Za-z]\s*\())",
        lambda match: f"${match.group('body')}" if not display_body_line_is_prose(match.group("body")) else match.group(0),
        text,
    )
    text = re.sub(
        r"(?P<expr>(?<!\$)(?:^|(?<=\s))=?\s*[^$\n]{0,100}?(?:\\times|\\div|\\cdot)[^$\n]{0,100}?[=+\-]\s*)\$(?P<body>[^$\n]+?)\$",
        lambda match: f"${match.group('expr').strip()} {match.group('body').strip()}$"
        if not display_body_line_is_prose(match.group("expr"))
        else match.group(0),
        text,
    )
    text = re.sub(
        r"(?P<expr>(?<!\$)(?:^|(?<=\s))=?\s*\\(?:mathrm|mathit|mathbf)\s*\{[^{}\n]+\}\s*)\$(?P<body>[^$\n]+?)\$",
        lambda match: f"${match.group('expr').strip()} {match.group('body').strip()}$",
        text,
    )
    text = re.sub(
        r"(?P<expr>(?<!\$)(?:^|(?<=\s))(?:\d+\s*)?\\frac\s*\{[^{}\n]+\}\s*\{[^{}\n]+\}[^$\n]{0,160}?=\s*)\$(?P<body>[^$\n]+?)\$",
        lambda match: f"${match.group('expr').strip()} {match.group('body').strip()}$",
        text,
    )
    text = re.sub(
        r"\$(?P<body>[^$\n]*?\\(?:in|notin)\s*)\$(?P<var>[A-Za-z])\$",
        lambda match: f"${match.group('body')}{match.group('var')}$",
        text,
    )
    text = re.sub(
        r"\$(?P<body>[^$\n]{1,240}?),\s*where\s*\$(?P<vars>[A-Za-z](?:\s*,\s*[A-Za-z])*\s*,?)\$(?=\s*(?:and|or)\s*\$)",
        lambda match: f"${match.group('body').rstrip()}$, where ${match.group('vars').strip()}$"
        if line_math_like(match.group("body")) and not display_body_line_is_prose(match.group("body"))
        else match.group(0),
        text,
        flags=re.I,
    )
    text = re.sub(r"(?<![A-Za-z])(?P<label>[a-z])\$(?=\s*[\(\[])", lambda match: f"{match.group('label')} $", text)
    text = re.sub(
        r"\$(?P<body>[^$\n]*?[;:,]\s*)\$(?=\s*(?:[-+]?\d|\\frac|\\sqrt|[A-Za-z]))",
        lambda match: (
            f"${match.group('body')}"
            if line_math_like(match.group("body")) and not display_body_line_is_prose(match.group("body"))
            else match.group(0)
        ),
        text,
    )
    text = re.sub(
        r"\$(?P<body>[^$\n]*?[=+\-*/]\s*)\$\s*(?P<var>[A-Za-z])\s*\$",
        lambda match: f"${match.group('body')}{match.group('var')}$",
        text,
    )
    text = re.sub(
        r"\$(?P<body>[^$\n]{1,260}?[=+\-]\s*)(?P<label>[a-z])\s+\$(?=\s*[\(\[])",
        lambda match: f"${match.group('body').rstrip()}$ {match.group('label')} $",
        text,
    )
    text = re.sub(
        r"\$(?P<body>[^$\n]*?)\s*(?P<marker>\\textcircled\{\d+\})\s*\$(?P<next>[^$\n]*?(?:\\frac|\\div|\\times|=)[^$\n]*?)\$",
        lambda match: (
            f"${match.group('body').rstrip()}$ {match.group('marker')} ${match.group('next').strip()}$"
            if line_math_like(match.group("body"))
            else match.group(0)
        ),
        text,
    )
    text = re.sub(
        r"\$\s+\$(?=\\frac)",
        lambda match: "$" if r"\textcircled" in match.string[max(0, match.start() - 40) : match.start()] else match.group(0),
        text,
    )
    text = re.sub(
        r"\$\s+\$(?=\d)",
        lambda match: "$" if r"\textcircled" in match.string[max(0, match.start() - 40) : match.start()] else match.group(0),
        text,
    )
    text = re.sub(r"\$\s+\$,\s*(?P<connector>where|then|if)\b", lambda match: f"$, {match.group('connector')}", text, flags=re.I)
    text = re.sub(r"(?<=[A-Za-z])\$(?=,\s*(?:then|if)\b)", "", text, flags=re.I)
    text = re.sub(r"(?<=[A-Za-z])\$(?=[.;:])", "", text)
    text = re.sub(r"\$\s*(?P<label>[a-z])\s*\$(?=\s*\$?\d)", lambda match: f" {match.group('label')} ", text)
    text = re.sub(r"(\\rule\{[^{}\n]+\}\{[^{}\n]+\})\$(?=[.;,])", r"\1", text)
    text = re.sub(r"\\(rightarrow|longrightarrow|leftarrow|longleftarrow|to|mapsto)(?=[A-Za-z])", r"\\\1 ", text)
    return text


def repair_redundant_inline_double_closers(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        if line.strip() == "$$":
            out.append(line)
            continue
        line = re.sub(
            r"\$(?P<body>[^$\n]+?)\$\$(?=\s*(?:$|\\\\|&|[.,;:]))",
            lambda match: f"${match.group('body')}$",
            line,
        )
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_where_inside_math_boundaries(text: str) -> str:
    """Move ', where' clauses out of a preceding math span."""
    return re.sub(
        r"\$(?P<body>[^$\n]{1,240}?)\s*,\s*where\s*\$(?P<next>[^$\n]{1,160}?)\$",
        lambda match: f"${match.group('body').rstrip()}$, where ${match.group('next').strip()}$",
        text,
        flags=re.I,
    )


def repair_fraction_list_dollar_splits(text: str) -> str:
    """Join comma-separated fraction lists split as ..., $\frac{}{}$."""
    frac = r"\\frac\s*\{\s*[^{}\n]+?\s*\}\s*\{\s*[^{}\n]+?\s*\}"
    previous = None
    while previous != text:
        previous = text
        text = re.sub(
            rf"\$(?P<body>[^$\n]*?,)\$(?=\s*{frac})",
            lambda match: f"${match.group('body')}",
            text,
        )
    return text


def repair_double_dollar_choice_markers(text: str) -> str:
    """Split adjacent answer choices around double-dollar marker boundaries."""
    return re.sub(
        r"\$\$\s*(?P<marker>\\textcircled\{\d+\})\s*",
        lambda match: f"$ {match.group('marker')} $",
        text,
    )


def remove_empty_hline_arrays(text: str) -> str:
    return re.sub(
        r"\s*\$\\begin\{array\}\{l\}\s*\\\\\s*\\hline\\end\{array\}\$",
        "$",
        text,
    )


def remove_orphan_operator_closing_dollars(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        pieces: list[str] = []
        cursor = 0
        in_math = False
        for dollar in re.finditer(r"(?<!\\)\$", line):
            segment = line[cursor : dollar.start()]
            if in_math:
                pieces.append(segment + "$")
                in_math = False
            else:
                following = line[dollar.end() :]
                opens_clear_math = re.match(
                    r"\s*(?:\\(?:frac|sqrt|boxed|left|begin\{array\})|[-+]?\d|[A-Za-z]\s*(?:[_^]|\())",
                    following,
                )
                if (
                    re.search(r"[=+\-*/]\s*$", segment)
                    and not re.search(r"\\[A-Za-z]+|[_^{}]", segment)
                    and not opens_clear_math
                ):
                    pieces.append(segment)
                else:
                    pieces.append(segment + "$")
                    in_math = True
            cursor = dollar.end()
        pieces.append(line[cursor:])
        out.append("".join(pieces))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_orphan_assignment_math_closers(text: str) -> str:
    """Wrap short algebraic assignments that only have a closing dollar."""
    assign_re = re.compile(
        r"(?<![\\A-Za-z0-9])(?P<expr>"
        r"[A-Za-z]\s*=\s*[-+]?\d+(?:\.\d+)?|"
        r"[A-Za-z]\s*[<>]=?\s*[-+]?\d+(?:\.\d+)?|"
        r"[A-Za-z][A-Za-z0-9]*\s*\([^)\n]*\)\s*=\s*[^$\n]*?(?:\\frac|\\sqrt|\\times|\\div|[_^])[^$\n]*?"
        r")\s*$"
    )
    out: list[str] = []
    for line in text.splitlines():
        pieces: list[str] = []
        cursor = 0
        in_math = False
        for dollar in re.finditer(r"(?<!\\)\$", line):
            segment = line[cursor : dollar.start()]
            if in_math:
                pieces.append(segment + "$")
                in_math = False
            else:
                match = assign_re.search(segment)
                if match:
                    pieces.append(segment[: match.start("expr")] + "$" + match.group("expr").strip() + "$")
                else:
                    pieces.append(segment + "$")
                    in_math = True
            cursor = dollar.end()
        pieces.append(line[cursor:])
        out.append("".join(pieces))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_orphan_math_expression_closers(text: str) -> str:
    """Wrap short math tails that have a closing dollar but lost their opening dollar."""
    group = r"\{(?:[^{}\n]|\{[^{}\n]*\})+\}"
    frac = rf"(?:\d+\s*)?\\frac\s*{group}\s*{group}"
    atom = rf"(?:{frac}|[A-Za-z][A-Za-z0-9]*\s*\([^)\n]*\)|[A-Za-z]|\d+(?:\.\d+)?)"
    op = r"(?:=|<|>|\\in|\\notin|\\leq|\\geq|\\leqslant|\\geqslant|\\neq|\\approx|\\sim|\\times|\\div|\\cdot|[+\-*/])"
    expression_tail_re = re.compile(rf"(?<![\\A-Za-z0-9])(?P<expr>{atom}(?:\s*{op}\s*{atom})+)\s*$")
    frac_tail_re = re.compile(rf"(?<![\\A-Za-z0-9])(?P<expr>{frac})\s*$")
    parenthesized_tail_re = re.compile(
        r"(?<![\\A-Za-z0-9])(?P<expr>[-+]?\s*\([^)\n]*(?:[A-Za-z]|\\frac|\\sqrt|[+\-*/=])[^)\n]*\))\s*$"
    )
    variable_tail_re = re.compile(r"(?<![\\A-Za-z])(?P<expr>[A-Z])\s*$")

    out: list[str] = []
    for line in text.splitlines():
        dollars = list(re.finditer(r"(?<!\\)\$", line))
        if (
            len(dollars) == 1
            and not line[dollars[0].end() :].strip()
            and "&" not in line
            and r"\hline" not in line
        ):
            prefix = line[: len(line) - len(line.lstrip())]
            body = line[len(prefix) : dollars[0].start()].strip()
            if body and line_math_like(body) and not display_body_line_is_prose(body):
                out.append(f"{prefix}${body}$")
                continue
        pieces: list[str] = []
        cursor = 0
        in_math = False
        for dollar in re.finditer(r"(?<!\\)\$", line):
            segment = line[cursor : dollar.start()]
            following = line[dollar.end() :]
            if in_math:
                pieces.append(segment + "$")
                in_math = False
            else:
                match = expression_tail_re.search(segment) or frac_tail_re.search(segment) or parenthesized_tail_re.search(segment)
                if not match and re.match(r"\s*(?:[A-Za-z]|,|;|and\b|or\b)", following):
                    match = variable_tail_re.search(segment)
                if match:
                    pieces.append(segment[: match.start("expr")] + "$" + match.group("expr").strip() + "$")
                else:
                    pieces.append(segment + "$")
                    in_math = True
            cursor = dollar.end()
        pieces.append(line[cursor:])
        out.append("".join(pieces))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_adjacent_orphan_math_runs(text: str) -> str:
    """Split OCR-collapsed runs such as $a$2\times3$ into adjacent math spans."""
    out: list[str] = []
    for line in text.splitlines():
        if line.strip() == "$$":
            out.append(line)
            continue
        line = re.sub(
            r"(?<!\\)\$\$(?=\s*(?:[-+]?\d|\\frac|\\sqrt|[A-Za-z][A-Za-z0-9]*\s*\(|[A-Za-z]\s*(?:\\in|\\notin|[=+\-*/_^])))",
            "$ $",
            line,
        )
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_orphan_hat_math_closers(text: str) -> str:
    """Wrap short formulas ending at a lone dollar when they contain bare \hat{}."""
    pattern = re.compile(
        r"(?P<prefix>[“\"'(\s])(?P<body>[^$“”\"'\n]{0,50}\\hat\{\}[^$“”\"'\n]{0,50})\$"
    )

    def repl(match: re.Match[str]) -> str:
        body = match.group("body").strip()
        if not re.search(r"[+\-=]|\\hat\{\}", body):
            return match.group(0)
        body = body.replace(r"\hat{}", r"\widehat{\ }")
        body = re.sub(r"([\u3400-\u9fff]+)", r"\\text{\1}", body)
        body = re.sub(r"\s+", " ", body)
        return f"{match.group('prefix')}${body}$"

    return pattern.sub(repl, text)


def repair_orphan_fraction_math_closers(text: str) -> str:
    """Turn simple fraction expressions with only a closing dollar into inline math."""
    frac = r"(?:\d+\s*)?\\frac\s*\{\s*[^{}\n]+?\s*\}\s*\{\s*[^{}\n]+?\s*\}"
    expr = rf"{frac}(?:\s*(?:[+\-*/=]|\\times|\\div|\\cdot)\s*{frac})*"
    tail_re = re.compile(rf"(?<![\\A-Za-z0-9])(?P<expr>-?\s*{expr})\s*$")
    out: list[str] = []
    for line in text.splitlines():
        pieces: list[str] = []
        cursor = 0
        in_math = False
        for dollar in re.finditer(r"(?<!\\)\$", line):
            segment = line[cursor : dollar.start()]
            if in_math:
                pieces.append(segment + "$")
                in_math = False
            else:
                match = tail_re.search(segment)
                if match:
                    pieces.append(segment[: match.start("expr")] + "$" + match.group("expr").strip() + "$")
                else:
                    pieces.append(segment + "$")
                    in_math = True
            cursor = dollar.end()
        pieces.append(line[cursor:])
        out.append("".join(pieces))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_orphan_parenthesized_math_closers(text: str) -> str:
    """Wrap short parenthesized arithmetic formulas that only have a closing dollar."""
    return re.sub(
        r"(?<![\$\\])(?P<expr>\(\s*[-+]?\d+(?:\.\d+)?(?:\s*[-+*/=]\s*[-+]?\d+(?:\.\d+)?)+\s*\))\$",
        lambda match: f"${match.group('expr')}$",
        text,
    )


def repair_malformed_array_endings(text: str) -> str:
    """Repair OCR-damaged \end{array} tokens before array-aware scanning."""
    text = re.sub(r"\\end\{array\s*=\s*([^{}\n]+)\}", r"\\end{array} = \1", text)
    text = re.sub(r"\\end\{array\s+(\\right\s*[)\]])\}", r"\\end{array} \1", text)
    text = re.sub(r"\\end\{array\s+(\\right\s*\.)\}", r"\\end{array} \1", text)
    text = re.sub(r"\\end\{array\s+(\\[A-Za-z]+)\}", r"\\end{array} \1", text)
    text = re.sub(r"\\end\{array(?=\s*(?:\$|\\\\|\n|$))", r"\\end{array}", text)
    return text


def repair_interrupted_inline_math_text(text: str) -> str:
    """Join inline math fragments split by short prose before the closing dollar."""
    pattern = re.compile(
        r"\$(?P<math>[^$\n]{1,120}?(?:[+\-=]|\\times|\\div|\\cdot)\s*)\$\s+"
        r"(?P<words>[A-Za-z][^$\n]{1,120}?)\${1,2}"
    )

    def repl(match: re.Match[str]) -> str:
        words = re.sub(r"\s+", " ", match.group("words")).strip()
        if not re.search(r"[A-Za-z]", words):
            return match.group(0)
        if re.fullmatch(r"[a-zA-Z]", words):
            return match.group(0)
        words = words.replace("\\", r"\textbackslash{}").replace("{", r"\{").replace("}", r"\}")
        return f"${match.group('math')}\\text{{{words}}}$"

    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(repl, text)
    return text


def repair_numeric_dollar_artifacts(text: str) -> str:
    """Distinguish OCR numeric math delimiters from real currency dollars."""
    units = r"(?:cm|mm|m|km|kg|g|mg|ml|mL|L|oz|ft|in)"

    def clean_split_fraction_part(value: str) -> str:
        return value.replace(r"\textbackslash{}", "").replace(r"\$", "").strip()

    def split_fraction_repl(match: re.Match[str]) -> str:
        return rf"\frac{{{clean_split_fraction_part(match.group('num'))}}}{{{clean_split_fraction_part(match.group('den'))}}}"

    text = re.sub(
        rf"\\\$(?P<num>\d[\d,.]*(?:\\,\d{{3}})*)\s+(?P<unit>{units})(?P<power>\^\{{[^{{}}\n]+\}})\$",
        lambda match: f"${match.group('num')} {match.group('unit')}{match.group('power')}$",
        text,
    )
    text = re.sub(
        r"\\\$(?P<seq>-?\d[\d,.]*(?:\s*,\s*-?\d[\d,.]*){2,}\s*,?\s*\.\.\.)\s*\\\\\s*\\hline\$",
        lambda match: f"${match.group('seq')}$ " + r"\\ \hline",
        text,
    )
    text = re.sub(
        r"\\frac\{\}\{\\square\}\$(?P<num>[^$}\\\n]+)\\\}\\\{(?P<den>(?:\\textbackslash\{\})?\\\$?[^}$\n]+)\}\$",
        split_fraction_repl,
        text,
    )
    text = re.sub(
        r"(?P<open>\$[^$\n]*(?:\\times|\\div|\\cdot|[+\-=])\s*)\$(?P<num>\d[\d,]*(?:\\,\d{3})*(?:\.\d+)?)(?P<trail>[^$\n]{0,30})\$",
        lambda match: f"{match.group('open')}{match.group('num')}{match.group('trail')}$",
        text,
    )
    text = re.sub(
        r"(?<!\\)\$(?=\d+(?:\.\d{2})(?:[.;,]|\s+(?:for|each|per|and|What|which|if)|$))",
        r"\\$",
        text,
    )
    text = re.sub(
        r"\$(?P<body>[^$\n]+?)\s+\$(?P<tail>=\s*[-+]?\d+(?:\.\d+)?)",
        lambda match: f"${match.group('body')} {match.group('tail')}$",
        text,
    )
    text = repair_orphan_degree_math_closers(text)
    text = re.sub(
        r"(?P<price>\\\$\d+(?:\.\d{2})?,\s*)(?P<body>[^$\n]*\\frac[^$\n]*)\$",
        lambda match: match.group("price") + f"${match.group('body').strip()}$",
        text,
    )
    text = re.sub(
        r"(?<![\$\\])(?P<num>\b\d+(?:\.\d+)?)\$(?=\^\{)",
        lambda match: f"${match.group('num')}",
        text,
    )
    text = re.sub(
        r"(?<![\$\\])(?P<num>\b\d+(?:\.\d+)?)\$(?P<body>\s*(?:[+\-*/]|\\times|\\div)[^$\n]+?)\$",
        lambda match: f"${match.group('num')}{match.group('body')}$",
        text,
    )
    text = re.sub(
        r"(?<![\$\\])(?P<num>\b\d+(?:\.\d+)?)\$\$",
        lambda match: f"${match.group('num')}$",
        text,
    )
    text = re.sub(
        r"(?P<prefix>=\s*)(?P<body>\d+(?:\.\d+)?\s*(?:[+\-*/]|\\times|\\div)\s*[^$\n]*?\\frac[^$\n]*?)\$",
        lambda match: f"{match.group('prefix')}${match.group('body').strip()}$",
        text,
    )
    text = re.sub(
        r"(?P<prefix>=\s*)\\\$(?P<num>\d+(?:\.\d+)?)\$",
        lambda match: f"{match.group('prefix')}${match.group('num')}$",
        text,
    )
    text = re.sub(
        r"(?P<prefix>=\s*)\\\$(?P<num>\d+(?:\.\d+)?)(?=\s*(?:$|[.;,]))",
        lambda match: f"{match.group('prefix')}${match.group('num')}$",
        text,
    )
    text = re.sub(
        r"\$(?P<body>[^$\n]{0,240}(?:[=+\-*/]|\\times|\\div|\\cdot)\s*)\$(?=(?:\d|\\frac|\\sqrt|\\[A-Za-z]))",
        lambda match: f"${match.group('body')}",
        text,
    )
    text = re.sub(
        r"\\text\s*\{(?P<body>[^{}\n]*)\}\$(?=(?:\\(?:div|times|cdot|frac|sqrt)\b|[A-Za-z]\s*(?:\^|_)?))",
        lambda match: rf"\text{{{match.group('body').rstrip()} }}",
        text,
    )
    text = repair_orphan_power_math_closers(text)
    return text


def close_text_before_array_end(text: str) -> str:
    return re.sub(
        r"\\text\s*\{([^{}\n]*?)\s*\\\\\s*(?=\\end\s*\{array\})",
        r"\\text{\1} \\\\ ",
        text,
    )


def merge_adjacent_inline_math_fragments(text: str) -> str:
    """Join OCR-split inline math such as $8$\frac{1}{3}$ or $x\to$$\frac{}{}$$."""
    frac = r"\\frac\s*\{\s*[^{}\n]+?\s*\}\s*\{\s*[^{}\n]+?\s*\}"
    relation_tail = re.compile(
        r"(?:=|<|>|\\leq|\\geq|\\leqslant|\\geqslant|\\neq|\\approx|\\sim|\\mapsto|\\to|\\rightarrow|\\times|\\div|\\cdot|[+\-*/])\s*$"
    )
    math_start = re.compile(r"\s*(?:\\frac|\\sqrt|\\left|\\widehat|\\hat|[-+]?\d|[A-Za-z]\s*(?:[_^]|\())")

    def should_merge(left: str, right: str) -> bool:
        left_clean = left.strip()
        right_clean = right.strip()
        if not left_clean or not right_clean:
            return False
        if relation_tail.search(left_clean):
            return True
        if right_clean.startswith((r"\frac", r"\sqrt", r"\left", r"\widehat", r"\hat")):
            return bool(re.search(r"(?:\d|\\mathbf|\\mathrm|\\mathit|\\mathcal|=|\\mapsto|\\to|\\rightarrow|\\times|\\div|\\cdot|-)$", left_clean))
        return False

    def merge_pair(match: re.Match[str]) -> str:
        left = match.group("left")
        right = match.group("right")
        if not should_merge(left, right):
            return match.group(0)
        return f"${left}{right}$"

    def merge_relation_split(match: re.Match[str]) -> str:
        left = match.group("left")
        right = match.group("right")
        return f"${left}{right}$"

    previous = None
    while previous != text:
        previous = text
        text = re.sub(
            rf"\$(?P<left>[^$\n]{{1,240}}?)\$\s*(?P<right>{frac})\$",
            merge_pair,
            text,
        )
        text = re.sub(
            r"\$(?P<left>[^$\n]{1,240}?(?:=|<|>|\\leq|\\geq|\\leqslant|\\geqslant|\\neq|\\approx|\\sim|\\times|\\div|\\cdot|[+\-*/])\s*)\$(?P<right>[^$\n]{1,160}?)\$\$",
            merge_relation_split,
            text,
        )
        text = re.sub(
            r"\$(?P<left>[^$\n]{1,240}?(?:=|<|>|\\leq|\\geq|\\leqslant|\\geqslant|\\neq|\\approx|\\sim|\\times|\\div|\\cdot|[+\-*/])\s*)\$(?P<right>[^$\n]{1,160}?)\$",
            merge_relation_split,
            text,
        )
        text = re.sub(
            r"\$(?P<left>[^$\n]{1,240}?)\$\s*\$(?P<right>[^$\n]{1,240}?)\$",
            merge_pair,
            text,
        )
        text = re.sub(
            r"\$(?P<left>\\(?:rightarrow|longrightarrow|leftarrow|longleftarrow|to|mapsto))\$\s*(?P<right>[^$\n]{1,200}?(?:\\sqrt|\\frac|=|\\times|\\div|\\cdot|[_^])[^$\n]*?)\$",
            lambda match: f"${match.group('left')} {match.group('right').strip()}$",
            text,
        )
        text = re.sub(r"\\mathcal\{P\}\(\$(?=\\(?:widehat|hat)\b)", r"\\mathcal{P}(", text)
        text = re.sub(r"\\hat\{\}", r"\\widehat{\\ }", text)
        text = re.sub(r"\\hat(?=\s*[\)\]\},=|])", r"\\widehat{\\ }", text)
        text = re.sub(
            r"\$(?P<body>[^$\n]+?)\$\$(?=\s*(?:\$|[A-Za-z0-9,.;:)&]|\\\\|$))",
            lambda match: f"${match.group('body')}$",
            text,
        )
    text = re.sub(r"\${3,}", "$$", text)
    return text


def ensure_inline_array_math_closure(text: str) -> str:
    """Close inline array math spans before table row/cell boundaries."""
    array_re = re.compile(
        r"(?<!\\)\$(?P<body>\\begin\{array\}\{[^{}\n]*\}.*?\\end\{array\})(?!\$)(?=\s*(?:\\\\|&|\\hline|$))"
    )
    return array_re.sub(lambda match: f"${match.group('body')}$", text)


def wrap_bare_math_fragments(part: str) -> str:
    """Wrap compact LaTeX math fragments that are sitting in surrounding text."""
    if not line_math_like(part):
        return part
    group = r"\{(?:[^{}\n]|\{[^{}\n]*\})+\}"
    frac = rf"\\frac\s*{group}\s*{group}"
    sqrt = r"\\sqrt\s*(?:\[[^\]\n]+\])?\s*\{[^{}\n]+\}"
    atom = r"(?:\d+(?:\.\d+)?|[A-Za-z])"

    part = re.sub(
        rf"(?<![\$\\])(?P<expr>-?\s*{frac})(?=\s*(?:&|\\\\|\\hline|[.,;:]|$))",
        lambda match: f"${match.group('expr').strip()}$",
        part,
    )
    part = re.sub(
        rf"(?<![\$\\])(?P<expr>(?:\d+\s*)?{frac})(?=\s*(?:[A-Za-z\"”'’)\]]|&|\\\\|\\hline|[.,;:]|$))",
        lambda match: f"${match.group('expr').strip()}$",
        part,
    )
    part = re.sub(
        rf"(?P<label>\b[a-z]\s+)(?P<expr>{frac}[^$\n]*?)(?=\s*(?:$|\\\\|\\hline))",
        lambda match: f"{match.group('label')}${match.group('expr').strip()}$",
        part,
    )
    part = re.sub(
        rf"(?<![\$\\])(?P<expr>{sqrt}(?:\s*[+\-]\s*\d+(?:\.\d+)?)?)(?=\s*(?:&|\\\\|\\hline|[.,;:]|$))",
        lambda match: f"${match.group('expr').strip()}$",
        part,
    )
    part = re.sub(
        r"(?<![\$\\])(?P<expr>\\left\([^$\n]+?\\right\)(?:\s*\^\s*\{[^{}\n]+\})?)(?=\s*(?:&|\\\\|\\hline|[.,;:]|$))",
        lambda match: f"${match.group('expr').strip()}$",
        part,
    )
    part = re.sub(
        rf"(?<![\$\\])(?P<expr>{atom}(?:\s*\\times\s*{atom})+(?:\s*=\s*{atom}(?:\^\{{[^{{}}\n]+\}})?)?)(?=\s*(?:&|\\\\|\\hline|[.,;:]|$))",
        lambda match: f"${match.group('expr').strip()}$",
        part,
    )
    return part


def wrap_bare_latex_math_runs(text: str) -> str:
    """Wrap short LaTeX math command runs that OCR left in text mode."""
    out: list[str] = []
    for line in text.splitlines():
        line = re.sub(
            r"(?P<expr>\b\d+(?:\.\d+)?(?:\s*\\times\s*\d+(?:\.\d+)?)+\s*=\s*)\$(?P<tail>[^$\n]+?)\$",
            lambda match: f"${match.group('expr')}{match.group('tail')}$",
            line,
        )
        line = re.sub(
            r"(?<!\\)\$(\d+)\$(\.\d+)",
            r"$\1\2",
            line,
        )
        if "$" not in line:
            out.append(wrap_bare_math_fragments(line))
            continue

        parts = re.split(r"((?<!\\)\$[^$\n]*(?<!\\)\$)", line)
        rebuilt: list[str] = []
        for part in parts:
            if part.startswith("$") and part.endswith("$"):
                rebuilt.append(part)
                continue
            part = re.sub(
                r"(?P<expr>\b\d+(?:\.\d+)?(?:\s*\\times\s*\d+(?:\.\d+)?)+\s*=\s*)\$(?P<tail>[^$\n]+?)\$",
                lambda match: f"${match.group('expr')}{match.group('tail')}$",
                part,
            )
            part = re.sub(
                r"(?P<expr>\\left\([^$\n]+?\\right\))(?<!\$)\$",
                lambda match: f"${match.group('expr')}$",
                part,
            )
            part = re.sub(
                r"(?P<expr>\\sqrt\{[^{}\n]+\}(?:\s*[+\-]\s*\d+(?:\.\d+)?)?)",
                lambda match: f"${match.group('expr')}$",
                part,
            )
            part = wrap_bare_math_fragments(part)
            rebuilt.append(part)
        out.append("".join(rebuilt))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def structural_text_line(stripped: str) -> bool:
    if not stripped:
        return True
    if stripped.startswith("%") or stripped == "$$":
        return True
    return bool(
        re.match(
            r"^\\(?:begin|end)\{(?:figure|enumerate|itemize|tabularx?|longtable|center|infobox|vocabbox|tipbox)\}|"
            r"^\\(?:includegraphics|caption|centering|chapter|section|subsection|exerciseheading|item)\b|"
            r"^\{\\(?:small|scriptsize)\b|^\\(?:setlength|renewcommand|resizebox)\b",
            stripped,
        )
    )


def line_is_mostly_math_work(stripped: str) -> bool:
    if structural_text_line(stripped) or "$" in stripped:
        return False
    if not line_math_like(stripped):
        return False
    if re.search(r"^(?:=|\\begin\{array\}|[a-zA-Z]\)|[a-zA-Z]\s+|[0-9]+[.)]?\s+|\([ivxlcdm]+\)\s+)", stripped, re.I):
        cleaned = re.sub(r"\\text\s*\{[^{}\n]*\}", "", stripped)
        cleaned = re.sub(r"\\[A-Za-z]+(?:\s*\{[^{}\n]*\})?", " ", cleaned)
        words = re.findall(r"[A-Za-z]{5,}", cleaned)
        allowed = {"array", "begin", "boxed", "frac", "general", "left", "quad", "right", "sqrt", "times"}
        return not any(word.lower() not in allowed for word in words)
    return bool(re.match(r"^\\(?:frac|sqrt|boxed|left|begin\{array\})", stripped) or stripped.startswith("="))


def wrap_bare_high_risk_math_lines(text: str) -> str:
    """Wrap math-heavy worksheet lines left in text mode after bad display delimiters are unwrapped."""
    out: list[str] = []
    in_display = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "$$":
            out.append(line)
            in_display = not in_display
            continue
        if in_display or structural_text_line(stripped) or "$" in line:
            out.append(line)
            continue
        if line_is_mostly_math_work(stripped):
            leading = line[: len(line) - len(line.lstrip())]
            trailing = line[len(line.rstrip()) :]
            out.append(f"{leading}${stripped}${trailing}")
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def wrap_bare_array_lines(text: str) -> str:
    """Put standalone array environments back into math mode and remove nested inline dollars."""
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if r"\begin{array}" in stripped and r"\end{array}" in stripped:
            leading = line[: len(line) - len(line.lstrip())]
            trailing = line[len(line.rstrip()) :]
            body = re.sub(r"(?<!\\)\$", "", stripped)
            line = f"{leading}${body}${trailing}"
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def wrap_mixed_math_dominant_lines(text: str) -> str:
    """Wrap formula-dominant lines that contain stray inline dollars or text-mode math commands."""
    out: list[str] = []
    math_start_re = re.compile(
        r"^(?:=|\\(?:Rightarrow|rightarrow|leftarrow|frac|sqrt|mathrm|mathbf|mathit)\b|"
        r"\d+\s*\\frac|[a-zA-Z]\s*\$?\\?\(?[A-Za-z0-9]*\)?\s*[=+\-])"
    )
    for line in text.splitlines():
        stripped = line.strip()
        if structural_text_line(stripped) or r"\begin{array}" in stripped:
            out.append(line)
            continue
        body = re.sub(r"(?<!\\)\$", "", stripped)
        if not line_math_like(body):
            out.append(line)
            continue
        prose_like = display_body_line_is_prose(body)
        formula_dominant = (
            bool(math_start_re.search(body))
            or (re.search(r"\\text\s*\{", body) and not prose_like)
            or (body.count(r"\frac") + body.count(r"\times") + body.count(r"\div") >= 2 and not prose_like)
        )
        if formula_dominant:
            leading = line[: len(line) - len(line.lstrip())]
            trailing = line[len(line.rstrip()) :]
            out.append(f"{leading}${body}${trailing}")
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_math_wrapped_items(text: str) -> str:
    """Move enumerate item commands back out of OCR-created math spans."""
    out: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^(?P<indent>\s*)\$\s*\\item(?:\s+(?P<body>.*?))?\s*\$(?P<trail>\s*)$", line)
        if match:
            body = (match.group("body") or "").strip()
            if body and line_math_like(body):
                line = f"{match.group('indent')}\\item ${body}${match.group('trail')}"
            else:
                line = f"{match.group('indent')}\\item {body}{match.group('trail')}".rstrip()
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def split_tabular_row_tail(cell: str) -> tuple[str, str]:
    match = re.search(r"(?P<tail>\s*\\\\(?:\s*\\hline)?\s*)$", cell)
    if not match:
        return cell, ""
    return cell[: match.start("tail")], match.group("tail")


def should_wrap_tabular_cell_math(cell: str) -> bool:
    stripped = cell.strip()
    if not stripped or stripped.startswith("$") or stripped.endswith("$"):
        return False
    if r"\includegraphics" in stripped or r"\begin{" in stripped or r"\end{" in stripped:
        return False
    if not re.search(r"\\[A-Za-z]+|\d|[=<>^_]|×|÷|≤|≥|≠|∧|∨|→|↔|⊕|⊆|∈|∀", stripped):
        return False
    return line_math_like(stripped) and not display_body_line_is_prose(stripped)


def repair_tabular_row_math_wrappers(text: str) -> str:
    """Split erroneous whole-row math wrappers inside tables into per-cell math spans."""
    out: list[str] = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.search(r"\\begin\{(?:tabularx?|longtable)\}", stripped):
            in_table = True
            out.append(line)
            continue
        if in_table and stripped.startswith("$") and stripped.endswith("$") and r"\begin{array}" not in stripped:
            leading = line[: len(line) - len(line.lstrip())]
            trailing = line[len(line.rstrip()) :]
            row = stripped[1:-1].strip()
            cells = re.split(r"(?<!\\)&", row)
            rebuilt: list[str] = []
            for cell in cells:
                body, tail = split_tabular_row_tail(cell.strip())
                body = body.strip()
                if should_wrap_tabular_cell_math(body):
                    body = f"${body}$"
                rebuilt.append((body + tail).strip())
            line = leading + " & ".join(rebuilt) + trailing
        out.append(line)
        if in_table and re.search(r"\\end\{(?:tabularx?|longtable)\}", stripped):
            in_table = False
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_broken_tabular_row_fragments(text: str) -> str:
    """Rejoin table rows split around alignment tabs by earlier dollar-boundary repairs."""
    out: list[str] = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        starts_table = bool(re.search(r"\\begin\{(?:tabularx?|longtable)\}", stripped))
        ends_table = bool(re.search(r"\\end\{(?:tabularx?|longtable)\}", stripped))
        if starts_table:
            in_table = True
            out.append(line)
            if ends_table:
                in_table = False
            continue
        if in_table and out:
            left = line.lstrip()
            if left.startswith("$ &"):
                out[-1] = out[-1].rstrip() + " " + left[1:].lstrip()
                if ends_table:
                    in_table = False
                continue
            if left.startswith("&"):
                out[-1] = out[-1].rstrip() + " " + left
                if ends_table:
                    in_table = False
                continue
        out.append(line)
        if ends_table:
            in_table = False
    return repair_tabular_row_math_wrappers("\n".join(out) + ("\n" if text.endswith("\n") else ""))


def repair_logic_symbol_dollar_noise(text: str) -> str:
    """Remove noisy inline dollar splits from symbolic-logic rows made text-safe by ensuremath."""
    logic_re = re.compile(
        r"\\ensuremath\{\\(?:wedge|vee|land|lor|oplus|rightarrow|leftarrow|leftrightarrow|Rightarrow|Leftarrow)\}"
    )
    out: list[str] = []
    for line in text.splitlines():
        if (
            len(re.findall(r"(?<!\\)\$", line)) >= 2
            and logic_re.search(line)
            and "&" not in line
            and not re.search(r"\\(?:frac|sqrt|times|div|cdot)\b", line)
        ):
            line = re.sub(r"(?<!\\)\$", "", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def merge_following_unit_math_commands(text: str) -> str:
    """Merge text-mode unit/style commands immediately following a pure formula span."""
    pattern = re.compile(
        r"\$(?P<body>[^$\n]{1,180}?)\s*\${1,2}\s*(?P<cmd>\\(?:mathrm|mathbf|mathit|text)\s*\{[^{}\n]+\})(?=\s*(?:[A-Za-z]|[.,;:)\]]|$))"
    )

    def repl(match: re.Match[str]) -> str:
        body = match.group("body")
        if line_math_like(body) and not display_body_line_is_prose(body):
            return f"${body.rstrip()} {match.group('cmd')}$"
        return match.group(0)

    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(repl, text)
    return text


def repair_split_operator_variable_math(text: str) -> str:
    """Join OCR splits such as $x^2 + bx +$ c $ back into one math span."""
    previous = None
    while previous != text:
        previous = text
        text = re.sub(
            r"\$(?P<body>[^$\n]{1,240}?[+\-*/]\s*)\$\s*(?P<tail>[A-Za-z])\s*\$",
            lambda match: f"${match.group('body')}{match.group('tail')}$"
            if line_math_like(match.group("body")) and not display_body_line_is_prose(match.group("body"))
            else match.group(0),
            text,
        )
    return text


def repair_orphan_ensuremath_closing_dollars(text: str) -> str:
    """Drop a lone closing dollar after text-safe ensuremath commands."""
    out: list[str] = []
    for line in text.splitlines():
        dollars = list(re.finditer(r"(?<!\\)\$", line))
        if len(dollars) % 2 == 1:
            last = dollars[-1]
            before = line[: last.start()]
            after = line[last.end() :]
            if not after.strip() and re.search(r"\\ensuremath\{\\[A-Za-z]+\}\s*$", before):
                line = before + after
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def wrap_bare_script_math_fragments_in_text(text: str) -> str:
    """Wrap bare x^{...}, f^{-1}(x), and similar script expressions in text mode."""
    script_re = re.compile(
        r"(?<![\\$])(?P<expr>[A-Za-z0-9]\s*(?:[_^]\s*(?:\{[^{}\n]+\}|\\[A-Za-z]+|[A-Za-z0-9+\-]+))+"
        r"(?:\s*\([^)\n]*\))?)"
    )
    paren_script_re = re.compile(
        r"(?<![\\$])(?P<expr>\([^)\n]{1,80}\)\s*(?:[_^]\s*(?:\{[^{}\n]+\}|[A-Za-z0-9+\-]+))+)"
    )
    style_re = re.compile(
        r"(?<![\\$])(?P<expr>\\(?:mathrm|mathbf|mathit|mathbb|mathfrak)\s*\{[^{}\n]+\}"
        r"(?:\s*[_^]\s*(?:\{[^{}\n]+\}|[A-Za-z0-9+\-]+))?(?:\s*\([^)\n]*\))?)"
    )

    def compact(match: re.Match[str]) -> str:
        expr = re.sub(r"\s+", "", match.group("expr"))
        return f"${expr}$"

    def wrap_style(match: re.Match[str]) -> str:
        expr = re.sub(r"\s+", " ", match.group("expr")).strip()
        return f"${expr}$"

    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if structural_text_line(stripped) or r"\begin{array}" in stripped or r"\end{array}" in stripped:
            out.append(line)
            continue
        parts = re.split(r"((?<!\\)\$[^$\n]*(?<!\\)\$)", line)
        rebuilt: list[str] = []
        for part in parts:
            if part.startswith("$") and part.endswith("$"):
                rebuilt.append(part)
            else:
                part = style_re.sub(wrap_style, part)
                part = paren_script_re.sub(compact, part)
                rebuilt.append(script_re.sub(compact, part))
        out.append("".join(rebuilt))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_mixed_operator_fraction_splits(text: str) -> str:
    """Repair half-open expressions such as x \div 2$ =\frac{x}{2}."""
    group = r"\{(?:[^{}\n]|\{[^{}\n]*\})+\}"
    frac = rf"\\frac\s*{group}\s*{group}"
    op = r"\\ensuremath\{\\(?:div|times|cdot)\}"
    pattern = re.compile(rf"(?P<expr>\b[A-Za-z]\s*{op}\s*[-+]?\d+(?:\.\d+)?)\$\s*=\s*(?P<rhs>{frac})")
    return pattern.sub(lambda match: f"${match.group('expr')} = {match.group('rhs')}$", text)


def repair_parenthesized_inline_math_scripts(text: str) -> str:
    """Move scripts applied to parenthesized inline math back into the math span."""
    group = r"\{(?:[^{}\n]|\{[^{}\n]*\})+\}"

    def repl(match: re.Match[str]) -> str:
        script = re.sub(r"\s+", "", match.group("script"))
        return f"$({match.group('body')}){script}$"

    return re.sub(
        rf"\(\$(?P<body>[^$\n]+?)\$\)\s*(?P<script>[_^]\s*{group})",
        repl,
        text,
    )


def repair_degree_unit_fragments(text: str) -> str:
    """Wrap bare degree and Celsius fragments in inline math."""
    degree = r"-?\d+(?:\.\d+)?\s*\^\{\\circ\}"

    def compact_degree(value: str) -> str:
        return re.sub(r"\s+", "", value)

    text = re.sub(
        rf"(?<![\$\\])(?P<left>{degree})\s*(?:\${{1,2}}\s*)?\\mathrm\{{C\}}\s+or\s*(?P<right>{degree})\s*\\mathrm\{{C\}}",
        lambda match: f"${compact_degree(match.group('left'))} \\mathrm{{C}}$ or ${compact_degree(match.group('right'))} \\mathrm{{C}}$",
        text,
    )
    text = re.sub(
        rf"(?<![\$\\])(?P<expr>{degree})\s*(?:\${{1,2}}\s*)?\\mathrm\{{C\}}",
        lambda match: f"${compact_degree(match.group('expr'))} \\mathrm{{C}}$",
        text,
    )
    text = re.sub(
        rf"(?<![\$\\])(?P<expr>{degree})(?!\s*(?:\$|\\mathrm))",
        lambda match: f"${compact_degree(match.group('expr'))}$",
        text,
    )
    return text


def repair_fragmented_celsius_math(text: str) -> str:
    """Rejoin Celsius expressions fragmented by earlier dollar-boundary repairs."""
    degree = r"-?\d+(?:\.\d+)?\^\{\\circ\}"
    text = re.sub(
        rf"\$-\$(?P<expr>\d+(?:\.\d+)?\^\{{\\circ\}})\$(?P<unit>[CF])\$",
        lambda match: f"$-{match.group('expr')} \\mathrm{{{match.group('unit')}}}$",
        text,
    )
    text = re.sub(
        rf"\$(?P<prefix>\d)\$(?P<expr>\d+(?:\.\d+)?\^\{{\\circ\}})\$(?P<unit>[CF])\$",
        lambda match: f"${match.group('prefix')}{match.group('expr')} \\mathrm{{{match.group('unit')}}}$",
        text,
    )
    text = re.sub(
        rf"\$(?P<expr>{degree})\$(?P<unit>[CF])\$",
        lambda match: f"${match.group('expr')} \\mathrm{{{match.group('unit')}}}$",
        text,
    )
    text = re.sub(
        rf"\$(?P<expr>-?{degree})\$(?P<unit>[CF])\b",
        lambda match: f"${match.group('expr')} \\mathrm{{{match.group('unit')}}}$",
        text,
    )
    text = re.sub(
        rf"\$(?P<prefix>\d)\$\s*\$(?P<expr>\d+(?:\.\d+)?\^\{{\\circ\}})\$(?P<unit>[CF])\b",
        lambda match: f"${match.group('prefix')}{match.group('expr')} \\mathrm{{{match.group('unit')}}}$",
        text,
    )
    text = re.sub(
        rf"\$-\$(?P<expr>\d+(?:\.\d+)?\^\{{\\circ\}}\s*\\mathrm\{{[CF]\}})\$",
        lambda match: f"$-{match.group('expr')}$",
        text,
    )
    text = re.sub(
        rf"\$(?P<left>{degree})\$\s*\$\\mathrm\{{C\}}\s+or\$(?P<right>{degree})\s*\\mathrm\{{C\}}\$",
        lambda match: f"${match.group('left')} \\mathrm{{C}}$ or ${match.group('right')} \\mathrm{{C}}$",
        text,
    )
    text = re.sub(
        rf"\$(?P<expr>{degree})\$\s*\$\\mathrm\{{C\}}(?P<trail>[^$\n]*)\$",
        lambda match: f"${match.group('expr')} \\mathrm{{C}}${match.group('trail')}",
        text,
    )
    return text


def wrap_bare_scientific_notation(text: str) -> str:
    """Wrap bare scientific-notation runs left in prose."""
    sci = re.compile(
        r"(?<![\$\\])(?P<expr>\d+(?:\.\d+)?\s*\\ensuremath\{\\times\}\s*10\s*\^\s*\{[-+]?\d+\})"
    )
    return sci.sub(lambda match: f"${match.group('expr')}$", text)


def repair_fragmented_scientific_notation(text: str) -> str:
    """Rejoin scientific notation after a script fragment was wrapped separately."""
    return re.sub(
        r"(?P<head>\d+(?:\.\d+)?\s*\\ensuremath\{\\times\}\s*)1\$(?P<power>0\^\{[-+]?\d+\})\$",
        lambda match: f"${match.group('head')}1{match.group('power')}$",
        text,
    )


def repair_split_mathbb_sets(text: str) -> str:
    """Repair set symbols split by a dangling display-dollar pair."""
    return re.sub(r"(?<!\$)-\s*(\\mathbb\{[A-Z]\})\$\$", r"$-\1$", text)


def repair_fragmented_mathbb_sets(text: str) -> str:
    """Rejoin variants like - $\mathbb{Z}$$$ after final dollar cleanup."""
    return re.sub(r"-\s*\$(\\mathbb\{[A-Z]\})\${2,}", r"$-\1$", text)


def repair_orphan_logic_power_markers(text: str) -> str:
    """Demote OCR-created powers of logic symbols back to plain logic symbols."""
    text = re.sub(r"-\s*\^\{\\ensuremath\{\\([A-Za-z]+)\}\}", r"- \\ensuremath{\\\1}", text)
    text = re.sub(r"(?<![A-Za-z0-9])\^\{\\ensuremath\{\\([A-Za-z]+)\}\}", r"\\ensuremath{\\\1}", text)
    return text


def repair_malformed_ensuremath_invocations(text: str) -> str:
    """Repair OCR fragments such as \ensuremath\ensuremath{...}."""
    return re.sub(r"\\ensuremath\s*(?=\\ensuremath\s*\{)", "", text)


def brace_surplus_and_depth(value: str) -> tuple[int, int]:
    """Return unmatched closing and opening brace counts, ignoring escaped braces."""
    surplus = 0
    depth = 0
    i = 0
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            i += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            if depth:
                depth -= 1
            else:
                surplus += 1
        i += 1
    return surplus, depth


def drop_surplus_closing_braces(value: str) -> str:
    """Drop unmatched closing braces without touching balanced math groups."""
    out: list[str] = []
    depth = 0
    i = 0
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            out.append(value[i : i + 2])
            i += 2
            continue
        if char == "{":
            depth += 1
            out.append(char)
        elif char == "}":
            if depth:
                depth -= 1
                out.append(char)
        else:
            out.append(char)
        i += 1
    return "".join(out)


def repair_table_continuation_cells(text: str) -> str:
    """Close rows when OCR placed the final table cell on the next source line."""
    lines = text.splitlines()
    out: list[str] = []
    in_table = False
    begin_re = re.compile(r"\\begin\{(?:tabularx?|longtable)\}")
    end_re = re.compile(r"\\end\{(?:tabularx?|longtable)\}")
    row_suffix_re = re.compile(r"\s*\\\\\s*(?:\\hline)?\s*$")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if begin_re.search(stripped):
            in_table = True
            out.append(line)
            if end_re.search(stripped):
                in_table = False
            i += 1
            continue
        if in_table and end_re.search(stripped):
            out.append(line)
            in_table = False
            i += 1
            continue
        if in_table and stripped.endswith("&"):
            j = i + 1
            skipped_blank: list[str] = []
            while j < len(lines) and not lines[j].strip():
                skipped_blank.append(lines[j])
                j += 1
            if j < len(lines):
                continuation = lines[j].strip()
                if (
                    continuation
                    and "&" not in continuation
                    and not continuation.startswith(("%", r"\hline", r"\begin", r"\end"))
                ):
                    merged = line.rstrip() + " " + continuation
                    if not row_suffix_re.search(merged):
                        merged += r" \\"
                    out.append(merged)
                    i = j + 1
                    continue
            out.append(line)
            out.extend(skipped_blank)
            i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_table_embedded_array_rows(text: str) -> str:
    """Turn OCR-created standalone array rows inside tabulars back into table rows."""
    out: list[str] = []
    in_table = False
    begin_re = re.compile(r"\\begin\{(?:tabularx?|longtable)\}")
    end_re = re.compile(r"\\end\{(?:tabularx?|longtable)\}")
    array_row_re = re.compile(r"^(?P<indent>\s*)\\begin\{array\}\{[^{}\n]+\}(?P<body>.*?)\\end\{array\}\s*$")
    for line in text.splitlines():
        stripped = line.strip()
        if begin_re.search(stripped):
            in_table = True
            out.append(line)
            if end_re.search(stripped):
                in_table = False
            continue
        if in_table and end_re.search(stripped):
            out.append(line)
            in_table = False
            continue
        if in_table:
            match = array_row_re.match(line)
            if match and "&" in match.group("body"):
                body = match.group("body").strip()
                has_hline = r"\hline" in body
                body = body.replace(r"\hline", "").strip()
                if not re.search(r"\\\\\s*(?:\\hline)?\s*$", body):
                    body += r" \\"
                if has_hline:
                    body += r" \hline"
                line = match.group("indent") + body
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_unbalanced_table_ensuremath_cells(text: str) -> str:
    """Strip malformed table-cell \ensuremath wrappers while preserving cell text."""
    out: list[str] = []
    in_table = False
    begin_re = re.compile(r"\\begin\{(?:tabularx?|longtable)\}")
    end_re = re.compile(r"\\end\{(?:tabularx?|longtable)\}")
    row_suffix_re = re.compile(r"(?P<body>.*?)(?P<suffix>\s*\\\\\s*(?:\\hline)?\s*)$")

    def has_long_prose_word(value: str) -> bool:
        without_commands = re.sub(r"\\[A-Za-z]+(?:\s*\{[^{}\n]*\})?", " ", value)
        allowed = {"begin", "array", "boxed", "frac", "left", "right", "sqrt", "times"}
        long_words = re.findall(r"[A-Za-z]{5,}", without_commands)
        prose_words = [
            word for word in re.findall(r"[A-Za-z]{2,}", without_commands) if word.lower() not in allowed
        ]
        return any(word.lower() not in allowed for word in long_words) or len(prose_words) >= 4

    def repair_cell(cell: str) -> str:
        suffix = ""
        suffix_match = row_suffix_re.match(cell)
        if suffix_match:
            cell = suffix_match.group("body")
            suffix = suffix_match.group("suffix")

        if r"\ensuremath{" not in cell:
            return cell + suffix

        leading = cell[: len(cell) - len(cell.lstrip())]
        trailing = cell[len(cell.rstrip()) :]
        body = cell.strip()
        unwrapped = unwrap_ensuremath_balanced_line(body)
        surplus, depth = brace_surplus_and_depth(unwrapped)
        malformed = (
            body.count(r"\ensuremath{") >= 2
            and (unwrapped == body or r"\ensuremath{" in unwrapped or surplus > 0 or depth > 0)
        )
        if not malformed:
            return cell + suffix

        cleaned = unwrapped.replace(r"\ensuremath{", "")
        cleaned = drop_surplus_closing_braces(cleaned)
        cleaned = re.sub(r"\\(widehat|hat|mathrm|mathbf|mathit|mathcal|mathsf|operatorname|text)\s+\{", r"\\\1{", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if line_math_like(cleaned) and not has_long_prose_word(cleaned):
            cleaned = rf"\ensuremath{{{cleaned}}}"
        return leading + cleaned + trailing + suffix

    for line in text.splitlines():
        stripped = line.strip()
        if begin_re.search(stripped):
            in_table = True
            out.append(line)
            if end_re.search(stripped):
                in_table = False
            continue
        if in_table and end_re.search(stripped):
            out.append(line)
            in_table = False
            continue
        if in_table and "&" in line and r"\begin{array}" not in line:
            line = "&".join(repair_cell(cell) for cell in line.split("&"))
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_orphan_power_tails_after_ensuremath(text: str) -> str:
    """Merge tails like \ensuremath{...} + 23)^{...} back into math mode."""
    marker = r"\ensuremath{"
    exponent = r"(?:[^{}]|\{[^{}]*\})+"
    tail_re = re.compile(rf"(?P<tail>\s*[+\-]\s*[^$\n&]{{1,80}}?\)\s*\^\{{{exponent}\}})")

    def command_span(value: str, start: int) -> tuple[int, int, int] | None:
        if not value.startswith(marker, start):
            return None
        body_start = start + len(marker)
        depth = 1
        i = body_start
        while i < len(value):
            char = value[i]
            if char == "\\" and i + 1 < len(value):
                i += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return body_start, i, i + 1
            i += 1
        return None

    def repair_line(line: str) -> str:
        pieces: list[str] = []
        cursor = 0
        while True:
            idx = line.find(marker, cursor)
            if idx == -1:
                pieces.append(line[cursor:])
                break
            span = command_span(line, idx)
            if not span:
                pieces.append(line[cursor:])
                break
            body_start, body_end, end = span
            tail_match = tail_re.match(line, end)
            if not tail_match:
                pieces.append(line[cursor:end])
                cursor = end
                continue
            pieces.append(line[cursor:idx])
            body = unwrap_ensuremath_balanced_line(line[body_start:body_end]).strip()
            tail = re.sub(r"\s+", " ", tail_match.group("tail").strip())
            pieces.append(rf"\ensuremath{{{body}{tail}}}")
            cursor = tail_match.end()
        return "".join(pieces)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def final_tex_safety_pass(text: str) -> str:
    """Final shape-only LaTeX repairs after table and image normalization."""
    for _ in range(4):
        previous = text
        text = strip_inline_dollars_in_display_math(text)
        text = strip_inline_dollars_in_standalone_display_blocks(text)
        text = remove_blank_lines_in_display_math(text)
        text = unwrap_structural_display_blocks_final(text)
        text = remove_standalone_dollars_around_structural_context(text)
        text = neutralize_annotation_marker_dollars(text)
        text = repair_joined_percent_membership_commands_final(text)
        text = repair_joined_membership_relation_variables_final(text)
        text = repair_empty_subscript_markers_final(text)
        text = repair_trig_command_spacing(text)
        text = repair_split_section_commands(text)
        text = repair_malformed_ensuremath_invocations(text)
        text = repair_split_math_commands_before_letters(text)
        text = repair_math_commands_joined_to_text_final(text)
        text = repair_joined_math_commands_inside_ensuremath_final(text)
        text = repair_escaped_function_subscripts_final(text)
        text = repair_split_trig_function_commands_final(text)
        text = repair_split_greek_ensuremath_commands_final(text)
        text = repair_split_includegraphics_final(text)
        text = repair_collapsed_matrix_rowbreaks_final(text)
        text = repair_backslash_digit_artifacts_final(text)
        text = repair_escaped_variable_subscripts_final(text)
        text = repair_empty_base_subscripts_in_math_final(text)
        text = repair_cancelled_symbol_artifacts_final(text)
        text = wrap_bare_accent_commands_final(text)
        text = sanitize_table_dollar_artifacts_final(text)
        text = quarantine_illegal_array_spec_lines_final(text)
        text = quarantine_printed_pmatrix_tail_lines_final(text)
        text = quarantine_high_risk_math_environment_lines_final(text)
        text = quarantine_bad_tabularx_blocks_final(text)
        text = sanitize_ttfamily_brace_noise_final(text)
        text = repair_malformed_fraction_denominator_parens(text)
        text = repair_unbalanced_accent_commands(text)
        text = repair_overline_double_open_delimiters(text)
        text = repair_bare_overline_commands(text)
        text = repair_bare_overset_underset_commands(text)
        text = convert_inline_left_right_dollars(text)
        text = repair_ensuremath_wrapped_dollar_math(text)
        text = close_unbalanced_ensuremath_before_dollars(text)
        text = repair_ensuremath_wrapped_table_rows(text)
        text = merge_split_math_environment_lines(text)
        text = remove_lone_table_closing_braces(text)
        text = repair_unit_power_fragments(text)
        text = repair_math_function_dollar_splits(text)
        text = repair_bare_function_ensuremath_args(text)
        text = repair_trig_degree_splits(text)
        text = repair_bare_inverse_trig_notation(text)
        text = repair_arrow_chain_lines(text)
        text = repair_left_right_dollar_order(text)
        text = repair_open_paren_dollar_ensuremath_power_splits(text)
        text = repair_parenthesized_power_dollar_continuations(text)
        text = repair_inline_parenthesized_power_dollar_splits(text)
        text = repair_array_exponent_artifacts(text)
        text = repair_split_frac_lines(text)
        text = repair_split_parenthesized_power_lines(text)
        text = repair_equation_variable_label_dollar_splits(text)
        text = repair_split_log_ensuremath_boundaries(text)
        text = repair_ensuremath_parenthesized_power_dollar_splits(text)
        text = unwrap_display_math_environment_ensuremath(text)
        text = repair_array_column_spec_content_leak(text)
        text = normalize_standalone_array_lines(text)
        text = balance_display_math_lines(text)
        text = repair_orphan_logic_power_markers(text)
        text = repair_orphan_text_caret_groups(text)
        text = repair_textasciicircum_math_artifacts(text)
        text = repair_script_ensuremath_fragments(text)
        text = repair_orphan_power_tails_after_ensuremath(text)
        text = repair_table_continuation_cells(text)
        text = merge_orphan_table_continuation_lines_final(text)
        text = repair_unbalanced_table_ensuremath_cells(text)
        text = repair_nested_table_math_fragments(text)
        text = wrap_math_like_table_cells(text)
        text = repair_fragmented_degree_units_final(text)
        text = repair_nested_degree_ranges(text)
        text = repair_fragmented_scientific_notation_final(text)
        text = repair_exponent_group_brace_artifacts(text)
        text = repair_comparison_operator_dollar_artifacts(text)
        text = repair_inline_double_dollar_splits(text)
        text = remove_residual_inline_display_markers(text)
        text = wrap_bare_math_commands_global_final(text)
        text = repair_bare_triangle_names_final(text)
        text = repair_double_superscripts_final(text)
        text = repair_sqrt_missing_radical_before_denominator_final(text)
        text = repair_array_end_leaked_inside_ensuremath_final(text)
        text = unwrap_ensuremath_wrapped_exerciseheadings_final(text)
        text = protect_exerciseheading_math_fragments_final(text)
        text = escape_stray_text_underscores_final(text)
        text = repair_common_fraction_denominator_splits_final(text)
        text = plainify_prose_ensuremath_superscripts_final(text)
        text = plainify_identifier_subscript_runs_final(text)
        text = drop_orphan_linebreak_lines_final(text)
        text = repair_collapsed_matrix_rowbreaks_final(text)
        text = repair_backslash_digit_artifacts_final(text)
        text = repair_escaped_variable_subscripts_final(text)
        text = repair_empty_base_subscripts_in_math_final(text)
        text = repair_cancelled_symbol_artifacts_final(text)
        text = wrap_bare_accent_commands_final(text)
        text = sanitize_table_dollar_artifacts_final(text)
        text = quarantine_illegal_array_spec_lines_final(text)
        text = quarantine_printed_pmatrix_tail_lines_final(text)
        text = quarantine_high_risk_math_environment_lines_final(text)
        text = quarantine_bad_tabularx_blocks_final(text)
        text = sanitize_ttfamily_brace_noise_final(text)
        text = merge_scripts_after_ensuremath_spans(text)
        text = wrap_bare_power_expressions_with_inner_ensuremath(text)
        text = wrap_leading_power_math_lines(text)
        text = wrap_math_dominant_expression_lines(text)
        text = repair_single_backslash_array_rows_final(text)
        text = repair_item_linebreak_artifacts(text)
        text = unwrap_ensuremath_wrapped_item_lines(text)
        text = strip_dollars_inside_frac_arguments_final(text)
        text = strip_dollars_inside_math_command_groups_final(text)
        text = neutralize_residual_odd_math_dollars(text)
        text = wrap_bare_math_commands_global_final(text)
        text = merge_scripts_after_ensuremath_spans(text)
        text = protect_bare_math_commands_final(text)
        text = final_line_balance_cleanup(text)
        text = repair_bare_function_ensuremath_args(text)
        text = repair_trig_degree_splits(text)
        text = repair_bare_inverse_trig_notation(text)
        text = repair_arrow_chain_lines(text)
        text = repair_script_ensuremath_fragments(text)
        text = neutralize_unbalanced_left_right_in_ensuremath_spans(text)
        text = neutralize_unbalanced_left_right_lines(text)
        text = repair_misordered_left_right_lines_final(text)
        text = repair_malformed_fraction_denominator_parens(text)
        text = repair_unbalanced_accent_commands(text)
        text = repair_unit_power_fragments(text)
        text = collapse_nested_ensuremath(text)
        text = repair_malformed_ensuremath_invocations(text)
        text = repair_orphan_power_tails_after_ensuremath(text)
        text = repair_labeled_aligned_openers(text)
        text = repair_parenthesis_dollar_splits(text)
        text = repair_split_structural_commands_from_math_symbols(text)
        text = repair_inline_frac_equal_splits(text)
        text = repair_placeholder_ensuremath_fraction_triplets(text)
        text = repair_missing_frac_denominator_close_before_rowbreak(text)
        text = wrap_bare_matrix_environments(text)
        text = wrap_multiline_bare_matrix_environments(text)
        text = repair_premature_matrix_begin_closure_final(text)
        text = wrap_bare_empty_base_scripts(text)
        text = repair_nested_empty_base_script_fragments_final(text)
        text = repair_empty_text_script_labels(text)
        text = repair_math_commands_inside_text_commands(text)
        text = repair_rowbreaks_inside_text_commands(text)
        text = repair_extra_text_closers_inside_arrays(text)
        text = repair_double_closing_brace_before_array_rowbreak(text)
        text = repair_premature_ensuremath_boundaries_inside_arrays(text)
        text = repair_hline_leaked_inside_ensuremath_arrays(text)
        text = repair_empty_base_script_fragments(text)
        text = normalize_math_command_argument_spacing(text)
        text = repair_split_division_boxed_equations(text)
        text = repair_inline_array_continuation_artifacts(text)
        text = repair_single_group_frac_with_trailing_denominator(text)
        text = repair_array_row_premature_ensuremath_closures(text)
        text = repair_parenthesized_ensuremath_power_fragments(text)
        text = unwrap_nested_ensuremath_spans(text)
        text = unwrap_prose_only_display_blocks_final(text)
        text = split_prose_lines_out_of_display_blocks_final(text)
        text = repair_table_continuation_cells(text)
        text = repair_table_embedded_array_rows(text)
        text = merge_orphan_table_continuation_lines_final(text)
        text = repair_unbalanced_table_ensuremath_cells(text)
        text = unwrap_display_math_environment_ensuremath(text)
        text = repair_array_column_spec_content_leak(text)
        text = normalize_standalone_array_lines(text)
        text = balance_display_math_lines(text)
        text = strip_inline_dollars_in_display_math(text)
        text = strip_inline_dollars_in_standalone_display_blocks(text)
        text = remove_blank_lines_in_display_math(text)
        text = unwrap_structural_display_blocks_final(text)
        text = remove_standalone_dollars_around_structural_context(text)
        if text == previous:
            break
    text = repair_malformed_fraction_denominator_parens(text)
    text = neutralize_annotation_marker_dollars(text)
    text = repair_joined_percent_membership_commands_final(text)
    text = repair_joined_membership_relation_variables_final(text)
    text = repair_empty_subscript_markers_final(text)
    text = repair_unbalanced_accent_commands(text)
    text = repair_overline_double_open_delimiters(text)
    text = repair_bare_overline_commands(text)
    text = repair_bare_overset_underset_commands(text)
    text = convert_inline_left_right_dollars(text)
    text = repair_ensuremath_wrapped_dollar_math(text)
    text = close_unbalanced_ensuremath_before_dollars(text)
    text = repair_ensuremath_wrapped_table_rows(text)
    text = merge_split_math_environment_lines(text)
    text = remove_lone_table_closing_braces(text)
    text = repair_unit_power_fragments(text)
    text = repair_malformed_ensuremath_invocations(text)
    text = repair_bare_function_ensuremath_args(text)
    text = repair_trig_degree_splits(text)
    text = repair_bare_inverse_trig_notation(text)
    text = repair_arrow_chain_lines(text)
    text = repair_left_right_dollar_order(text)
    text = repair_open_paren_dollar_ensuremath_power_splits(text)
    text = repair_parenthesized_power_dollar_continuations(text)
    text = repair_inline_parenthesized_power_dollar_splits(text)
    text = repair_array_exponent_artifacts(text)
    text = repair_split_frac_lines(text)
    text = repair_split_parenthesized_power_lines(text)
    text = repair_equation_variable_label_dollar_splits(text)
    text = repair_split_log_ensuremath_boundaries(text)
    text = repair_ensuremath_parenthesized_power_dollar_splits(text)
    text = repair_orphan_power_tails_after_ensuremath(text)
    text = repair_labeled_aligned_openers(text)
    text = repair_parenthesis_dollar_splits(text)
    text = repair_split_structural_commands_from_math_symbols(text)
    text = repair_inline_frac_equal_splits(text)
    text = repair_placeholder_ensuremath_fraction_triplets(text)
    text = repair_missing_frac_denominator_close_before_rowbreak(text)
    text = wrap_bare_matrix_environments(text)
    text = wrap_multiline_bare_matrix_environments(text)
    text = repair_premature_matrix_begin_closure_final(text)
    text = wrap_bare_empty_base_scripts(text)
    text = repair_nested_empty_base_script_fragments_final(text)
    text = repair_empty_text_script_labels(text)
    text = repair_math_commands_inside_text_commands(text)
    text = repair_rowbreaks_inside_text_commands(text)
    text = repair_extra_text_closers_inside_arrays(text)
    text = repair_double_closing_brace_before_array_rowbreak(text)
    text = repair_premature_ensuremath_boundaries_inside_arrays(text)
    text = repair_hline_leaked_inside_ensuremath_arrays(text)
    text = repair_empty_base_script_fragments(text)
    text = normalize_math_command_argument_spacing(text)
    text = repair_split_division_boxed_equations(text)
    text = repair_inline_array_continuation_artifacts(text)
    text = repair_single_group_frac_with_trailing_denominator(text)
    text = repair_array_row_premature_ensuremath_closures(text)
    text = repair_parenthesized_ensuremath_power_fragments(text)
    text = unwrap_nested_ensuremath_spans(text)
    text = unwrap_prose_only_display_blocks_final(text)
    text = split_prose_lines_out_of_display_blocks_final(text)
    text = repair_table_continuation_cells(text)
    text = repair_table_embedded_array_rows(text)
    text = repair_unbalanced_table_ensuremath_cells(text)
    text = wrap_math_like_table_cells(text)
    text = repair_inline_double_dollar_splits(text)
    text = remove_residual_inline_display_markers(text)
    text = wrap_bare_math_commands_global_final(text)
    text = merge_scripts_after_ensuremath_spans(text)
    text = wrap_bare_power_expressions_with_inner_ensuremath(text)
    text = wrap_math_command_dominant_lines(text)
    text = wrap_leading_power_math_lines(text)
    text = wrap_math_dominant_expression_lines(text)
    text = protect_bare_math_commands_final(text)
    text = final_line_balance_cleanup(text)
    text = repair_bare_function_ensuremath_args(text)
    text = repair_trig_degree_splits(text)
    text = repair_bare_inverse_trig_notation(text)
    text = repair_arrow_chain_lines(text)
    text = neutralize_unbalanced_left_right_in_ensuremath_spans(text)
    text = neutralize_unbalanced_left_right_lines(text)
    text = merge_scripts_after_ensuremath_spans(text)
    text = unwrap_display_math_environment_ensuremath(text)
    text = repair_array_column_spec_content_leak(text)
    text = normalize_standalone_array_lines(text)
    text = balance_display_math_lines(text)
    text = collapse_nested_ensuremath(text)
    text = remove_blank_lines_in_display_math(text)
    text = unwrap_structural_display_blocks_final(text)
    text = remove_standalone_dollars_around_structural_context(text)
    text = repair_item_linebreak_artifacts(text)
    text = unwrap_ensuremath_wrapped_item_lines(text)
    text = post_unwrapped_text_safety_pass(text)
    text = repair_unclosed_script_ensuremath_before_array_end(text)
    text = close_unbalanced_math_before_ttfamily_blocks(text)
    text = recover_printed_latex_ttfamily_blocks(text)
    text = recover_printed_latex_table_cells(text)
    text = plainify_printed_latex_ttfamily_blocks(text)
    text = plainify_printed_latex_table_cells(text)
    text = unwrap_ensuremath_in_ttfamily_blocks(text)
    text = sanitize_ttfamily_text_blocks(text)
    text = remove_par_tokens_inside_ensuremath_final(text)
    text = repair_bad_table_evidence_rows_final(text)
    text = demote_orphan_multicolumn_evidence_rows_final(text)
    text = unwrap_structural_display_blocks_final(text)
    text = unwrap_prose_only_display_blocks_final(text)
    text = split_prose_lines_out_of_display_blocks_final(text)
    text = remove_standalone_dollars_around_structural_context(text)
    text = repair_placeholder_ensuremath_fraction_triplets(text)
    text = repair_missing_frac_denominator_close_before_rowbreak(text)
    text = wrap_bare_empty_base_scripts(text)
    text = repair_nested_empty_base_script_fragments_final(text)
    text = repair_empty_text_script_labels(text)
    text = repair_math_commands_inside_text_commands(text)
    text = repair_rowbreaks_inside_text_commands(text)
    text = repair_extra_text_closers_inside_arrays(text)
    text = repair_double_closing_brace_before_array_rowbreak(text)
    text = repair_premature_ensuremath_boundaries_inside_arrays(text)
    text = repair_array_column_spec_content_leak(text)
    text = repair_hline_leaked_inside_ensuremath_arrays(text)
    text = final_line_balance_cleanup(text)
    text = drop_standalone_display_dollars_final(text)
    text = neutralize_broken_inline_dollars_in_prose(text)
    text = repair_text_parallel_bars_final(text)
    text = repair_split_integral_command_artifacts_final(text)
    text = repair_split_integral_ensuremath_final(text)
    text = wrap_bare_calculus_commands_in_text_final(text)
    text = repair_bare_xarrow_commands_final(text)
    text = wrap_bracketed_math_fragment_scripts_final(text)
    text = wrap_arrow_annotation_lines_final(text)
    text = quarantine_unrecoverable_math_lines(text)
    text = repair_unclosed_script_ensuremath_before_array_end(text)
    text = close_unbalanced_math_before_ttfamily_blocks(text)
    text = repair_multiline_aligned_blocks(text)
    text = recover_printed_latex_ttfamily_blocks(text)
    text = recover_printed_latex_table_cells(text)
    text = plainify_printed_latex_ttfamily_blocks(text)
    text = plainify_printed_latex_table_cells(text)
    text = unwrap_ensuremath_in_ttfamily_blocks(text)
    text = sanitize_ttfamily_text_blocks(text)
    text = remove_par_tokens_inside_ensuremath_final(text)
    text = repair_bad_table_evidence_rows_final(text)
    text = demote_orphan_multicolumn_evidence_rows_final(text)
    text = repair_bare_overline_commands(text)
    text = repair_bare_overset_underset_commands(text)
    text = repair_underbrace_fraction_without_group_final(text)
    text = wrap_bare_matrix_environments(text)
    text = wrap_multiline_bare_matrix_environments(text)
    text = repair_premature_matrix_begin_closure_final(text)
    text = wrap_bare_empty_base_scripts(text)
    text = repair_nested_empty_base_script_fragments_final(text)
    text = repair_sqrt_frac_missing_close_before_rowbreak(text)
    text = repair_malformed_stackrel_final(text)
    text = repair_bad_table_evidence_rows_final(text)
    text = demote_orphan_multicolumn_evidence_rows_final(text)
    text = ensure_table_rows_end_final(text)
    text = wrap_bare_decimal_ldots_degrees(text)
    text = repair_dollar_power_groups_final(text)
    text = drop_empty_script_groups_final(text)
    text = repair_unknown_negated_relation_artifacts(text)
    text = neutralize_escaped_percent_subscripts(text)
    text = repair_overarrow_ensuremath_arguments(text)
    text = replace_escaped_setminus_in_math_context(text)
    text = repair_scripts_inside_text_commands_final(text)
    text = quarantine_orphan_array_tail_lines(text)
    text = quarantine_text_array_collision_lines(text)
    text = remove_printed_display_math_delimiters(text)
    text = recover_printed_column_vector_commands(text)
    text = plainify_decoded_latex_ttfamily_blocks(text)
    text = wrap_bare_textcircled_subscripts(text)
    text = protect_bare_math_commands_final(text)
    text = merge_scripts_after_ensuremath_spans(text)
    text = merge_ensuremath_script_ensuremath_final(text)
    text = merge_bare_base_ensuremath_script_final(text)
    text = merge_simple_script_after_ensuremath_final(text)
    text = wrap_bare_base_script_group_final(text)
    text = wrap_closing_delimiter_script_tails_final(text)
    text = repair_spacing_command_scripts_final(text)
    text = unwrap_scripts_inside_ensuremath_final(text)
    text = collapse_nested_ensuremath(text)
    text = unwrap_printed_latex_ensuremath_spans(text)
    text = sanitize_printed_latex_command_groups(text)
    text = recover_printed_column_vector_commands(text)
    text = plainify_decoded_latex_ttfamily_blocks(text)
    text = remove_orphan_endgroup_lines(text)
    text = ensure_list_item_before_quarantined_blocks(text)
    text = close_lists_after_quarantined_single_item_blocks(text)
    text = drop_surplus_closing_braces_in_prose_lines(text)
    text = unwrap_ensuremath_wrapped_item_lines(text)
    text = protect_bare_math_commands_final(text)
    text = wrap_bare_stackrel_final(text)
    text = wrap_escaped_delimiter_scripts_final(text)
    text = neutralize_orphan_alignment_tabs_final(text)
    text = balance_unclosed_list_environments_final(text)
    text = repair_fractional_script_bound_artifacts_final(text)
    text = repair_split_integral_command_artifacts_final(text)
    text = repair_split_integral_ensuremath_final(text)
    text = recover_printed_latex_ttfamily_blocks(text)
    text = recover_printed_latex_table_cells(text)
    text = plainify_printed_latex_ttfamily_blocks(text)
    text = plainify_printed_latex_table_cells(text)
    text = sanitize_ttfamily_text_blocks(text)
    text = plainify_decoded_latex_ttfamily_blocks(text)
    text = demote_orphan_multicolumn_evidence_rows_final(text)
    text = remove_par_tokens_inside_ensuremath_final(text)
    text = final_line_balance_cleanup(text)
    text = repair_fractional_script_bound_artifacts_final(text)
    text = repair_split_integral_command_artifacts_final(text)
    text = recover_printed_latex_ttfamily_blocks(text)
    text = recover_printed_latex_table_cells(text)
    text = plainify_printed_latex_ttfamily_blocks(text)
    text = plainify_printed_latex_table_cells(text)
    text = plainify_decoded_latex_ttfamily_blocks(text)
    text = remove_par_tokens_inside_ensuremath_final(text)
    text = final_line_balance_cleanup(text)
    text = neutralize_annotation_marker_dollars(text)
    text = repair_joined_percent_membership_commands_final(text)
    text = repair_joined_membership_relation_variables_final(text)
    text = repair_empty_subscript_markers_final(text)
    text = wrap_bare_calculus_commands_in_text_final(text)
    text = unwrap_printed_latex_ensuremath_spans(text)
    text = sanitize_printed_latex_command_groups(text)
    text = recover_printed_column_vector_commands(text)
    text = plainify_decoded_latex_ttfamily_blocks(text)
    text = plainify_mixed_escaped_latex_lines_final(text)
    text = repair_split_math_commands_before_letters(text)
    text = repair_math_commands_joined_to_text_final(text)
    text = repair_joined_math_commands_inside_ensuremath_final(text)
    text = repair_escaped_function_subscripts_final(text)
    text = repair_split_trig_function_commands_final(text)
    text = repair_split_greek_ensuremath_commands_final(text)
    text = repair_split_includegraphics_final(text)
    text = repair_collapsed_matrix_rowbreaks_final(text)
    text = repair_backslash_digit_artifacts_final(text)
    text = repair_escaped_variable_subscripts_final(text)
    text = repair_empty_base_subscripts_in_math_final(text)
    text = repair_cancelled_symbol_artifacts_final(text)
    text = wrap_bare_accent_commands_final(text)
    text = sanitize_table_dollar_artifacts_final(text)
    text = quarantine_illegal_array_spec_lines_final(text)
    text = quarantine_printed_pmatrix_tail_lines_final(text)
    text = quarantine_high_risk_math_environment_lines_final(text)
    text = quarantine_bad_tabularx_blocks_final(text)
    text = sanitize_ttfamily_brace_noise_final(text)
    text = repair_split_integral_ensuremath_final(text)
    text = repair_joined_function_variable_commands_final(text)
    text = repair_bare_xarrow_commands_final(text)
    text = repair_underbrace_fraction_without_group_final(text)
    text = protect_bare_math_commands_final(text)
    text = repair_bare_triangle_names_final(text)
    text = repair_double_superscripts_final(text)
    text = repair_sqrt_missing_radical_before_denominator_final(text)
    text = repair_array_end_leaked_inside_ensuremath_final(text)
    text = unwrap_ensuremath_wrapped_exerciseheadings_final(text)
    text = protect_exerciseheading_math_fragments_final(text)
    text = escape_stray_text_underscores_final(text)
    text = repair_escaped_function_subscripts_final(text)
    text = repair_split_trig_function_commands_final(text)
    text = repair_split_greek_ensuremath_commands_final(text)
    text = repair_misordered_left_right_lines_final(text)
    text = repair_premature_math_env_closures_final(text)
    text = wrap_bare_math_environment_spans_final(text)
    text = merge_orphan_table_continuation_lines_final(text)
    text = repair_common_fraction_denominator_splits_final(text)
    text = neutralize_odd_dollars_in_long_prose_final(text)
    text = plainify_prose_ensuremath_superscripts_final(text)
    text = plainify_identifier_subscript_runs_final(text)
    text = drop_orphan_linebreak_lines_final(text)
    text = repair_collapsed_matrix_rowbreaks_final(text)
    text = repair_backslash_digit_artifacts_final(text)
    text = repair_escaped_variable_subscripts_final(text)
    text = repair_empty_base_subscripts_in_math_final(text)
    text = repair_cancelled_symbol_artifacts_final(text)
    text = wrap_bare_accent_commands_final(text)
    text = sanitize_table_dollar_artifacts_final(text)
    text = quarantine_illegal_array_spec_lines_final(text)
    text = quarantine_printed_pmatrix_tail_lines_final(text)
    text = quarantine_high_risk_math_environment_lines_final(text)
    text = quarantine_bad_tabularx_blocks_final(text)
    text = sanitize_ttfamily_brace_noise_final(text)
    text = escape_body_percent_signs_final(text)
    text = ensure_table_rows_end_final(text)
    text = ensure_list_item_before_quarantined_blocks(text)
    text = quarantine_unrecoverable_math_lines(text)
    text = quarantine_bad_ensuremath_lines_final(text)
    text = sanitize_ttfamily_text_blocks(text)
    text = recover_printed_column_vector_commands(text)
    text = plainify_decoded_latex_ttfamily_blocks(text)
    text = repair_broken_caption_commands_final(text)
    text = neutralize_orphan_alignment_tabs_final(text)
    text = remove_par_tokens_inside_ensuremath_final(text)
    text = plainify_visible_latex_escape_lines_final(text)
    text = repair_matrix_single_backslash_digit_rows_final(text)
    text = repair_orphan_arrow_closing_braces_final(text)
    text = repair_sqrt_ensuremath_argument_final(text)
    text = repair_binary_command_ensuremath_argument_final(text)
    text = repair_text_command_alignment_artifacts_final(text)
    text = repair_caption_textbackslash_artifacts_final(text)
    text = repair_split_square_ensuremath_commands_final(text)
    text = repair_split_sum_ensuremath_commands_final(text)
    text = repair_bare_caret_inside_script_groups_final(text)
    text = close_unclosed_ensuremath_lines_final(text)
    text = neutralize_orphan_single_dollar_lines_final(text)
    text = remove_dollar_only_table_cells_final(text)
    text = normalize_array_column_counts(text)
    text = ensure_list_item_before_quarantined_blocks(text)
    text = close_unclosed_size_groups_final(text)
    return text


def post_unwrapped_text_safety_pass(text: str) -> str:
    """After display unwrapping, protect math fragments that moved back into text."""
    for _ in range(3):
        previous = text
        text = strip_inline_dollars_in_display_math(text)
        text = strip_inline_dollars_in_standalone_display_blocks(text)
        text = remove_blank_lines_in_display_math(text)
        text = unwrap_structural_display_blocks_final(text)
        text = remove_standalone_dollars_around_structural_context(text)
        text = unwrap_prose_only_display_blocks_final(text)
        text = split_prose_lines_out_of_display_blocks_final(text)
        text = repair_math_function_dollar_splits(text)
        text = repair_bare_function_ensuremath_args(text)
        text = repair_trig_degree_splits(text)
        text = repair_bare_inverse_trig_notation(text)
        text = repair_arrow_chain_lines(text)
        text = repair_left_right_dollar_order(text)
        text = repair_open_paren_dollar_ensuremath_power_splits(text)
        text = repair_parenthesized_power_dollar_continuations(text)
        text = repair_inline_parenthesized_power_dollar_splits(text)
        text = repair_array_exponent_artifacts(text)
        text = repair_split_frac_lines(text)
        text = repair_split_parenthesized_power_lines(text)
        text = repair_equation_variable_label_dollar_splits(text)
        text = repair_split_log_ensuremath_boundaries(text)
        text = repair_ensuremath_parenthesized_power_dollar_splits(text)
        text = repair_orphan_power_tails_after_ensuremath(text)
        text = repair_parenthesis_dollar_splits(text)
        text = repair_split_structural_commands_from_math_symbols(text)
        text = repair_inline_frac_equal_splits(text)
        text = repair_empty_base_script_fragments(text)
        text = normalize_math_command_argument_spacing(text)
        text = repair_split_division_boxed_equations(text)
        text = repair_inline_array_continuation_artifacts(text)
        text = repair_single_group_frac_with_trailing_denominator(text)
        text = repair_array_row_premature_ensuremath_closures(text)
        text = repair_parenthesized_ensuremath_power_fragments(text)
        text = unwrap_nested_ensuremath_spans(text)
        text = repair_table_continuation_cells(text)
        text = repair_table_embedded_array_rows(text)
        text = repair_unbalanced_table_ensuremath_cells(text)
        text = wrap_bare_math_commands_global_final(text)
        text = merge_scripts_after_ensuremath_spans(text)
        text = wrap_bare_power_expressions_with_inner_ensuremath(text)
        text = wrap_math_command_dominant_lines(text)
        text = wrap_leading_power_math_lines(text)
        text = wrap_math_dominant_expression_lines(text)
        text = protect_bare_math_commands_final(text)
        text = final_line_balance_cleanup(text)
        text = neutralize_unbalanced_left_right_in_ensuremath_spans(text)
        text = neutralize_unbalanced_left_right_lines(text)
        text = collapse_nested_ensuremath(text)
        text = repair_item_linebreak_artifacts(text)
        text = unwrap_ensuremath_wrapped_item_lines(text)
        if text == previous:
            break
    return text


def repair_nested_table_math_fragments(text: str) -> str:
    """Repair inline math accidentally split inside table explanation cells."""
    command = r"\\ensuremath\{\\(?:times|div|cdot|pm)\}"
    text = re.sub(
        rf"\$(?P<head>[^$\n]*?{command})\s*\$\s*\$?(?P<frac>\\frac\{{[^{{}}\n]+\}}\{{[^{{}}\n]+\}})\$",
        lambda match: f"${match.group('head').strip()} {match.group('frac')}$",
        text,
    )
    text = re.sub(r"(\\\s+\\hline)\$+(?=\s*$)", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"(\\hline)\$+(?=\s*$)", r"\1", text, flags=re.MULTILINE)
    return text


def repair_fragmented_degree_units_final(text: str) -> str:
    """Rejoin OCR-split degree-unit expressions without assuming a source book."""
    degree = r"\d+(?:\.\d+)?\s*\^\{\\circ\}"
    unit_command = r"\\mathrm\{[CF]\}"

    def compact(value: str) -> str:
        return re.sub(r"\s+", "", value)

    def unit_from_text(value: str) -> str:
        value = value.strip()
        if value.startswith(r"\mathrm"):
            match = re.search(r"\{([CF])\}", value)
            return match.group(1) if match else value
        return value

    text = re.sub(
        rf"\$-\$(?P<expr>{degree})\$(?P<unit>[CF])\$",
        lambda match: f"$-{compact(match.group('expr'))} \\mathrm{{{match.group('unit')}}}$",
        text,
    )
    text = re.sub(
        rf"\$(?P<prefix>\d)\${{1,2}}(?P<expr>{degree}\s*(?P<unit>{unit_command}|[CF]))\${{1,2}}",
        lambda match: f"${match.group('prefix')}{compact(match.group('expr').replace(match.group('unit'), ''))} \\mathrm{{{unit_from_text(match.group('unit'))}}}$",
        text,
    )
    text = re.sub(
        rf"\$(?P<word>or|and)\$(?P<expr>-?{degree}\s*(?P<unit>{unit_command}|[CF]))\s*(?:\n\s*)?\${{1,2}}",
        lambda match: f" {match.group('word')} ${compact(match.group('expr').replace(match.group('unit'), ''))} \\mathrm{{{unit_from_text(match.group('unit'))}}}$",
        text,
    )
    text = re.sub(
        rf"(?P<expr>\$-?{degree}\s*{unit_command}\$)\s*\n\s*\$\$(?=\s*[?.!,;:])",
        r"\g<expr>",
        text,
    )
    text = re.sub(
        rf"(?P<expr>\$-?{degree}\s*{unit_command}\$)\s*\n\s+(?=(?:or|and)\s+\$)",
        r"\g<expr> ",
        text,
    )
    text = re.sub(
        rf"\$(?P<expr>-?{degree})\$\s*(?P<unit>[CF])\b",
        lambda match: f"${compact(match.group('expr'))} \\mathrm{{{match.group('unit')}}}$",
        text,
    )
    text = re.sub(
        rf"\$(?P<expr>-?{degree}\s*{unit_command})\${{2,}}",
        lambda match: f"${match.group('expr').strip()}$",
        text,
    )
    text = re.sub(
        rf"(?<![\$\\])(?P<expr>-?{degree}\s*{unit_command})\${{2,}}",
        lambda match: f"${match.group('expr').strip()}$",
        text,
    )
    text = re.sub(
        rf"(?<![\$\\A-Za-z0-9])(?P<expr>-?{degree})\s*(?P<unit>[CF])\b",
        lambda match: f"${compact(match.group('expr'))} \\mathrm{{{match.group('unit')}}}$",
        text,
    )
    text = re.sub(
        r"\{\}\s*\^\{\\circ\}\s*(?P<unit>[CF])\b",
        lambda match: rf"$\square^{{\circ}} \mathrm{{{match.group('unit')}}}$",
        text,
    )
    return text


def repair_nested_degree_ranges(text: str) -> str:
    """Remove nested dollar pairs around degree bounds inside a larger inline formula."""
    degree = r"-?\d+(?:\.\d+)?\s*\^\{\\circ\}"
    return re.sub(
        rf"\$(?P<head>[^$\n]*?\\text\{{\s*for\s*\}}\s*)\$(?P<first>{degree})\$(?P<middle>[^$\n]*?\\ensuremath\{{\\(?:leqslant|geqslant|leq|geq)\}}[^$\n]*?)\$(?P<second>{degree})\$(?P<trail>\.?)\$",
        lambda match: f"${match.group('head')}{match.group('first')}{match.group('middle')}{match.group('second')}{match.group('trail')}$",
        text,
    )


def repair_fragmented_scientific_notation_final(text: str) -> str:
    """Rejoin split decimal/scientific notation and add missing text spacing."""
    sci_tail = r"\d+\s*\\ensuremath\{\\times\}\s*10\s*\^\s*\{[-+]?\d+\}"
    sci_power = r"10\s*\^\s*\{[-+]?\d+\}"
    text = re.sub(
        rf"\$(?P<int>\d+)\.\$(?P<tail>{sci_tail})\${{1,2}}",
        lambda match: f"${match.group('int')}.{match.group('tail')}$",
        text,
    )
    text = re.sub(
        rf"\$(?P<int>\d+)\.\$(?P<frac>\d+\s*\\ensuremath\{{\\times\}}\s*)\$(?P<power>{sci_power})\$",
        lambda match: f"${match.group('int')}.{match.group('frac')}{match.group('power')}$",
        text,
    )
    text = re.sub(
        r"(?P<word>[A-Za-z])(?=\$\d+(?:\.\d+)?\s*\\ensuremath\{\\times\}\s*10\s*\^)",
        r"\g<word> ",
        text,
    )
    return text


def repair_exponent_group_brace_artifacts(text: str) -> str:
    """Repair OCR exponent groups such as ^{(\frac{1}{2}+...})}."""
    return re.sub(
        r"\^\s*\{\((?P<body>[^\n$]*?\\frac[^\n$]*?)\}\)\}",
        lambda match: "^{" + f"({match.group('body')})" + "}",
        text,
    )


def repair_comparison_operator_dollar_artifacts(text: str) -> str:
    """Repair split dollars around short comparison-operator lists."""
    return re.sub(
        r"\$<\s*,\s*>\s*,\s*or\s*\$=\s+to compare\s+\$\s+\$\$(?P<expr>\\frac\{[^{}\n]+\}\{[^{}\n]+\})",
        lambda match: rf"$<, >, \text{{or }} =$ to compare ${match.group('expr')}",
        text,
    )


def repair_script_ensuremath_fragments(text: str) -> str:
    """Move ensuremath-wrapped fragments inside their surrounding scripts."""
    def merge_script(match: re.Match[str]) -> str:
        script = re.sub(r"\s+", "", match.group("script"))
        return rf"\ensuremath{{{match.group('body')}{script}}}"

    text = re.sub(
        r"(?P<op>[_^])\{\\ensuremath\{(?P<body>[^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*)\}\}",
        lambda match: f"{match.group('op')}{{{match.group('body')}}}",
        text,
    )
    text = re.sub(
        r"\^\{-\\ensuremath\{\\frac\{(?P<num>[^{}\n]+)\}\{(?P<den>[^{}\n]+)\}\}\}",
        lambda match: rf"^{{-\frac{{{match.group('num')}}}{{{match.group('den')}}}}}",
        text,
    )
    text = re.sub(
        r"\\ensuremath\{(?P<body>[^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*)\}\s*(?P<script>[_^]\s*\{[^{}\n]+\})",
        merge_script,
        text,
    )
    text = re.sub(
        r"\\ensuremath\{(?P<body>[^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*)\}\s*(?P<op>[_^])\s*\{\\ensuremath\{(?P<script>[^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*)\}\}",
        lambda match: rf"\ensuremath{{{match.group('body')}{match.group('op')}{{{match.group('script')}}}}}",
        text,
    )
    text = re.sub(
        r"(?<![\\A-Za-z])(?P<base>[A-Za-z0-9])_\s*\\ensuremath\{(?P<script>[^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*)\}",
        lambda match: rf"\ensuremath{{{match.group('base')}_{{{match.group('script')}}}}}",
        text,
    )
    return text


def repair_orphan_text_caret_groups(text: str) -> str:
    """Render orphan OCR caret groups as text instead of invalid math superscripts."""
    text = re.sub(
        r"\\textbackslash\{\}\s*\^\{(?P<body>[^{}\n]+)\}",
        lambda match: rf"\ensuremath{{\backslash^{{{match.group('body')}}}}}",
        text,
    )
    text = re.sub(
        r"\\ensuremath\{(?P<prefix>[A-Za-z]+)\s*\^\{(?P<body>[A-Za-z]+)\}\}",
        lambda match: rf"{match.group('prefix')} \ensuremath{{{{}}^{{{match.group('body')}}}}}",
        text,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9)\]}])\^\{(?P<body>[A-Za-z]+)\}",
        lambda match: rf"\ensuremath{{{{}}^{{{match.group('body')}}}}}",
        text,
    )
    return re.sub(
        r"(?<![A-Za-z0-9)\]}])(?P<sign>[-+]?)\^\{(?P<body>[A-Za-z0-9+\-]+)\}",
        lambda match: rf"\ensuremath{{{match.group('sign')}{{}}^{{{match.group('body')}}}}}",
        text,
    )


def repair_split_math_commands_before_letters(text: str) -> str:
    """Split OCR-joined binary set commands such as \cupB into \cup B."""
    text = re.sub(
        r"\\(?P<cmd>qquad|quad|cup|cap|setminus|times|div|cdot|oplus|otimes|ominus|odot|therefore|because|rightarrow|leftarrow|longrightarrow|longleftarrow|mapsto|longmapsto|hookrightarrow|hookleftarrow|twoheadrightarrow|twoheadleftarrow|rightarrowtail|leftarrowtail|leqslant|geqslant|leq|geq|neq|notin|nsubseteq|nsupseteq|subseteq|supseteq|subset|supset|forall|exists|nexists|equiv|approx|sim|cong|wedge|vee|land|lor|preccurlyeq|succcurlyeq|npreceq|nsucceq|preceq|succeq|nprec|nsucc|precsim|succsim|precapprox|succapprox|prec|succ|triangleleft|triangleright|vartriangleleft|vartriangleright|bigtriangleup|bigtriangledown|triangle|unlhd|unrhd|lhd|rhd)(?=[A-Za-z\u4e00-\u9fff])",
        r"\\\g<cmd> ",
        text,
    )
    text = re.sub(
        r"\\pm(?=[A-Za-z](?=[)\]},.;:=+\-*/\s\\]|$))",
        r"\\pm ",
        text,
    )
    return re.sub(r"\\(?P<cmd>in|ni)(?=[A-Z0-9])", r"\\\g<cmd> ", text)


def repair_malformed_fraction_denominator_parens(text: str) -> str:
    """Move OCR-misplaced right parentheses out of simple fraction denominators."""
    simple_nested = r"(?:[^{}]|\{[^{}]*\})+"
    text = re.sub(
        rf"\\frac\{{\}}\\ensuremath\{{(?P<num>{simple_nested})\}}\}}\{{\\ensuremath\{{(?P<den>{simple_nested})\}}\}}",
        lambda match: rf"\ensuremath{{\frac{{{match.group('num')}}}{{{match.group('den')}}}}}",
        text,
    )
    text = re.sub(
        r"\\frac\{\}\s*(?P<num>.*?)(?<!\\)\}\{(?P<den>[^{}\n]+)\}",
        lambda match: rf"\ensuremath{{\frac{{{match.group('num').strip()}}}{{{match.group('den')}}}}}",
        text,
    )
    text = re.sub(
        r"\\frac\{(?P<num>[^{}\n]+)\}\\ensuremath\{(?P<den>[^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*)\}",
        lambda match: rf"\ensuremath{{\frac{{{match.group('num')}}}{{{match.group('den')}}}}}",
        text,
    )
    text = re.sub(
        r"(?P<op>[_^])\{(?P<body>\\frac\s*\{[^{}\n]+\}\s*\{[^{}\n]+\})\s*=",
        lambda match: f"{match.group('op')}{{{match.group('body')}}} =",
        text,
    )
    text = re.sub(
        r"\\frac\s*\{(?P<num>[^{}\n]+)\}\s*\{(?P<den>[^{}\n()]+)\)\}",
        lambda match: rf"\frac{{{match.group('num')}}}{{{match.group('den')}}})",
        text,
    )
    return re.sub(
        r"\^\{\s*(?P<body>\([^$\n]*?\))\s*=",
        lambda match: f"^{{{match.group('body')}}} =",
        text,
    )


def repair_unbalanced_accent_commands(text: str) -> str:
    """Close common OCR-truncated accent command arguments."""
    ensure_piece = r"\\ensuremath\{(?:[^{}]|\{[^{}]*\})*\}"
    simple_nested_body = rf"(?:{ensure_piece}|\{{[^{{}}\n]*\}}|[^{{}}\n])*"
    text = re.sub(
        r"\\overset\s*\{(?P<top>[^{}\n]*)\}\s*\{\\underset\s*\{\}\s*\{",
        lambda match: (match.group("top").strip() + r" \\ "),
        text,
    )
    text = re.sub(
        rf"\\(?P<accent>hat|widehat|bar|vec|dot|ddot|tilde|widetilde)\s*\\(?P<alpha>mathrm|mathbf|mathit|mathcal|mathsf|mathbb|mathfrak)\s*\{{(?P<body>{simple_nested_body})\}}",
        lambda match: rf"\{match.group('accent')}{{\{match.group('alpha')}{{{match.group('body')}}}}}",
        text,
    )
    text = re.sub(
        r"\\(?P<cmd>underline|overline)\s*\{\{(?P<body>[^{}\n]+)\}(?!\})",
        lambda match: rf"\{match.group('cmd')}{{{{{match.group('body')}}}}}",
        text,
    )
    text = re.sub(
        r"\\textasciicircum\{\}\s*\{(?P<body>\\frac\{[^{}\n]+\}\{[^{}\n]+\})\s*=",
        lambda match: rf"^{{{match.group('body')}}} =",
        text,
    )
    text = re.sub(
        r"\\underbrace\\ensuremath\{\{(?P<body>[^{}\n]+)\}_\{(?P<label>[^{}\n]+)\}\}",
        lambda match: rf"\underbrace{{{match.group('body')}}}_{{{match.group('label')}}}",
        text,
    )
    text = re.sub(
        r"\\widehat\{\\ensuremath\{(?P<body>[^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*)\}\}",
        lambda match: rf"\ensuremath{{\widehat{{{match.group('body')}}}}}",
        text,
    )
    return text


def repair_overline_double_open_delimiters(text: str) -> str:
    """Repair long-division OCR fragments such as \overline{{) 180}."""
    return re.sub(r"\\overline\s*\{\s*\{\s*\)", r"\\overline{)", text)


def repair_bare_overline_commands(text: str) -> str:
    """Wrap bare \overline groups with nested OCR math wrappers."""
    def repair_line(line: str) -> str:
        pieces: list[str] = []
        cursor = 0
        search = 0
        changed = False
        marker = r"\overline"
        while True:
            found = line.find(marker, search)
            if found == -1:
                pieces.append(line[cursor:])
                break
            if line[max(0, found - len(r"\ensuremath{")) : found].endswith(r"\ensuremath{"):
                search = found + len(marker)
                continue
            span = balanced_brace_group_span(line, found + len(marker))
            if not span:
                search = found + len(marker)
                continue
            body = unwrap_ensuremath_balanced_line(line[span[0] : span[1]])
            pieces.append(line[cursor:found])
            pieces.append(rf"\ensuremath{{\overline{{{body}}}}}")
            cursor = span[2]
            search = span[2]
            changed = True
        return "".join(pieces) if changed else line

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def repair_bare_overset_underset_commands(text: str) -> str:
    r"""Wrap bare \overset/\underset commands that appear in text/table cells."""
    def repair_line(line: str) -> str:
        pieces: list[str] = []
        cursor = 0
        search = 0
        changed = False
        while True:
            match = re.search(r"\\(?:over|under)set(?![A-Za-z])", line[search:])
            if not match:
                pieces.append(line[cursor:])
                break
            found = search + match.start()
            cmd_end = search + match.end()
            if line[max(0, found - len(r"\ensuremath{")) : found].endswith(r"\ensuremath{"):
                search = cmd_end
                continue
            first = balanced_brace_group_span(line, cmd_end)
            if not first:
                search = cmd_end
                continue
            second = balanced_brace_group_span(line, first[2])
            if not second:
                search = first[2]
                continue
            pieces.append(line[cursor:found])
            pieces.append(rf"\ensuremath{{{line[found:second[2]]}}}")
            cursor = second[2]
            search = second[2]
            changed = True
        return "".join(pieces) if changed else line

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def convert_inline_left_right_dollars(text: str) -> str:
    """Use \ensuremath for inline left/right spans to survive nearby dollar noise."""
    return re.sub(
        r"(?<!\\)\$(?P<body>[^$\n]*?\\left[^$\n]*?\\right[^$\n]*?)(?<!\\)\$",
        lambda match: rf"\ensuremath{{{match.group('body')}}}",
        text,
    )


def repair_ensuremath_wrapped_dollar_math(text: str) -> str:
    """Remove redundant dollar delimiters inside full-line \ensuremath wrappers."""
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(r"\ensuremath{$") and stripped.endswith("$}"):
            indent = line[: len(line) - len(line.lstrip())]
            body = stripped[len(r"\ensuremath{$") : -2]
            line = indent + rf"\ensuremath{{{body}}}"
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def unwrap_ensuremath_wrapped_item_lines(text: str) -> str:
    """Move list item commands back out of full-line math wrappers."""
    out: list[str] = []
    marker = r"\ensuremath"
    for line in text.splitlines():
        indent = line[: len(line) - len(line.lstrip())]
        stripped = line.strip()
        if stripped.startswith(marker + "{"):
            span = balanced_brace_group_span(stripped, len(marker))
            if span and not stripped[span[2] :].strip():
                body = stripped[span[0] : span[1]]
                if body.lstrip().startswith(r"\item"):
                    line = indent + body
            elif stripped.startswith(r"\ensuremath{\item"):
                line = indent + stripped[len(r"\ensuremath{") :]
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def close_unbalanced_ensuremath_before_dollars(text: str) -> str:
    """Close an \ensuremath group when OCR used a dollar as the terminator."""
    marker = r"\ensuremath{"
    out: list[str] = []
    for line in text.splitlines():
        cursor = 0
        while True:
            idx = line.find(marker, cursor)
            if idx == -1:
                break
            pos = idx + len(marker)
            depth = 1
            i = pos
            advanced = False
            while i < len(line):
                if line[i] == "\\":
                    i += 2
                    continue
                if line[i] == "$" and depth > 0:
                    line = line[:i] + "}" + line[i:]
                    cursor = i + 2
                    advanced = True
                    break
                if line[i] == "{":
                    depth += 1
                elif line[i] == "}":
                    depth -= 1
                    if depth == 0:
                        cursor = i + 1
                        advanced = True
                        break
                i += 1
            if not advanced:
                break
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_ensuremath_wrapped_table_rows(text: str) -> str:
    """Unwrap whole tabular rows accidentally put in math mode."""
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(r"\ensuremath{") and "&" in stripped and r"\hline" in stripped:
            indent = line[: len(line) - len(line.lstrip())]
            body = unwrap_ensuremath_balanced_line(stripped)
            body = re.sub(
                r"\\begin\{array\}(?P<body>.*?)\\end\{array\}",
                lambda match: rf"\ensuremath{{\begin{{array}}{match.group('body')}\end{{array}}}}",
                body,
            )
            line = indent + body
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def merge_split_math_environment_lines(text: str) -> str:
    """Join OCR-split aligned/array ensuremath openers with their closing line."""
    lines = text.splitlines()
    out: list[str] = []
    opener_re = re.compile(
        r"^(?P<indent>\s*)\\ensuremath\{\\begin\{(?P<env>aligned|array)\}"
        r"(?P<spec>\{[^{}\n]*\})?\}\s*$"
    )
    i = 0
    while i < len(lines):
        match = opener_re.match(lines[i])
        if not match:
            out.append(lines[i])
            i += 1
            continue

        env = match.group("env")
        pieces: list[str] = []
        j = i + 1
        found = False
        while j < len(lines) and j <= i + 8:
            pieces.append(lines[j].strip())
            if re.search(rf"\\end\{{{env}\}}", lines[j]):
                found = True
                break
            if not lines[j].strip():
                break
            j += 1

        if not found:
            out.append(lines[i])
            i += 1
            continue

        body = " ".join(piece for piece in pieces if piece)
        body = re.sub(rf"\\end\{{{env}\}}(?!\}})", rf"\\end{{{env}}}", body)
        opener = rf"\begin{{{env}}}{match.group('spec') or ''}"
        out.append(rf"{match.group('indent')}\ensuremath{{{opener} {body}}}")
        i = j + 1

    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def wrap_math_like_table_cells(text: str) -> str:
    """Wrap math-heavy tabular cells individually so math does not leak across rows."""
    out: list[str] = []
    in_table = False
    begin_re = re.compile(r"\\begin\{(?:tabularx?|longtable)\}")
    end_re = re.compile(r"\\end\{(?:tabularx?|longtable)\}")
    row_suffix_re = re.compile(r"(?P<body>.*?)(?P<suffix>\s*\\\\\s*(?:\\hline)?\s*)$")
    math_signal_re = re.compile(
        r"\\(?:Rightarrow|Leftarrow|Leftrightarrow|frac|sqrt|pm|times|div|cdot|"
        r"leq|geq|leqslant|geqslant|ne|neq|widehat|overline|underline|therefore|because)\b|[_^]"
    )

    def has_long_prose_word(value: str) -> bool:
        without_commands = re.sub(r"\\[A-Za-z]+(?:\s*\{[^{}\n]*\})?", " ", value)
        allowed = {"array", "begin", "boxed", "frac", "left", "right", "sqrt", "times"}
        long_words = re.findall(r"[A-Za-z]{5,}", without_commands)
        prose_words = [
            word for word in re.findall(r"[A-Za-z]{2,}", without_commands) if word.lower() not in allowed
        ]
        return any(word.lower() not in allowed for word in long_words) or len(prose_words) >= 4

    def wrap_cell(cell: str) -> str:
        suffix = ""
        suffix_match = row_suffix_re.match(cell)
        if suffix_match:
            cell = suffix_match.group("body")
            suffix = suffix_match.group("suffix")

        leading = cell[: len(cell) - len(cell.lstrip())]
        trailing = cell[len(cell.rstrip()) :]
        body = cell.strip()
        if (
            not body
            or body.startswith((r"$", r"\ensuremath{", r"\multicolumn", r"\multirow"))
            or r"\includegraphics" in body
            or r"\begin{" in body
            or re.search(r"(?<!\\)\$", body)
        ):
            return cell + suffix

        if math_signal_re.search(body) and line_math_like(body) and not has_long_prose_word(body):
            body = unwrap_ensuremath_balanced_line(body)
            body = re.sub(r"\\(widehat|hat|mathrm|mathbf|mathit|mathcal|mathsf|operatorname)\s+\{", r"\\\1{", body)
            return leading + rf"\ensuremath{{{body}}}" + trailing + suffix
        return cell + suffix

    for line in text.splitlines():
        stripped = line.strip()
        if begin_re.search(stripped):
            in_table = True
            out.append(line)
            if end_re.search(stripped):
                in_table = False
            continue
        if in_table and end_re.search(stripped):
            out.append(line)
            in_table = False
            continue
        if in_table and "&" in line and r"\begin{array}" not in line:
            line = "&".join(wrap_cell(cell) for cell in line.split("&"))
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def remove_lone_table_closing_braces(text: str) -> str:
    """Drop stray table-closing braces left after HTML/table conversion."""
    text = re.sub(r"(\\end\{tabularx\}\n)\}\n", r"\1", text)
    text = re.sub(r"(\\end\{tabular\}\n)\}\n", r"\1", text)
    return text


def unwrap_ensuremath_balanced_line(line: str) -> str:
    """Remove balanced \ensuremath wrappers while preserving nested braces."""
    marker = r"\ensuremath{"
    previous = None
    while previous != line:
        previous = line
        pieces: list[str] = []
        cursor = 0
        while True:
            idx = line.find(marker, cursor)
            if idx == -1:
                pieces.append(line[cursor:])
                break
            pieces.append(line[cursor:idx])
            pos = idx + len(marker)
            depth = 1
            i = pos
            while i < len(line):
                if line[i] == "\\":
                    i += 2
                    continue
                if line[i] == "{":
                    depth += 1
                elif line[i] == "}":
                    depth -= 1
                    if depth == 0:
                        pieces.append(line[pos:i])
                        cursor = i + 1
                        break
                i += 1
            else:
                pieces.append(line[idx:])
                cursor = len(line)
                break
        line = "".join(pieces)
    return line


def normalize_standalone_array_lines(text: str) -> str:
    """Collapse repeated wrappers around standalone array/aligned formula lines."""
    out: list[str] = []
    in_display = False
    for line in text.splitlines():
        if line.strip() == "$$":
            in_display = not in_display
            out.append(line)
            continue
        if (
            not in_display
            and re.search(r"\\begin\{(?:array|aligned)\}", line)
            and (line.strip().startswith((r"\begin{array}", r"\begin{aligned}")) or not ("&" in line and r"\hline" in line))
        ):
            body = re.sub(r"\\end\{(array|aligned)(?!\})", r"\\end{\1}", line.strip())
            body = unwrap_ensuremath_balanced_line(body)
            body = body.replace(r"\ensuremath{", "")
            body = re.sub(r"(\\end\{(?:array|aligned)\})\}+", r"\1", body)
            if not body.startswith(r"\ensuremath{"):
                body = rf"\ensuremath{{{body}}}"
            line = body
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def balance_display_math_lines(text: str) -> str:
    """Append small missing brace tails on math-heavy display lines."""
    out: list[str] = []
    in_display = False
    for line in text.splitlines():
        if line.strip() == "$$":
            in_display = not in_display
            out.append(line)
            continue
        if in_display and re.search(r"\\(?:frac|underbrace|overbrace|sqrt|textasciicircum)|[_^]\s*\{", line):
            diff = line.count("{") - line.count("}")
            if 0 < diff <= 3:
                line = line + ("}" * diff)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_unit_power_fragments(text: str) -> str:
    """Wrap OCR unit-power fragments such as {1000}{\mathrm{cm}}^{3}."""
    unit_body = r"(?:[^{}]|\{[^{}]*\})*"
    text = re.sub(
        rf"(?<!\\ensuremath\{{)\{{(?P<num>\d+(?:\.\d+)?)\}}\s*\{{\\mathrm\{{(?P<unit>{unit_body})\}}\}}\s*\^\{{(?P<power>[^{{}}\n]+)\}}",
        lambda match: rf"\ensuremath{{{{{match.group('num')}}}{{\mathrm{{{match.group('unit')}}}}}^{{{match.group('power')}}}}}",
        text,
    )
    return re.sub(
        rf"(?<!\\ensuremath\{{)(?P<num>\d+(?:\.\d+)?)\s*\{{\\mathrm\{{(?P<unit>{unit_body})\}}\}}\s*\^\{{(?P<power>[^{{}}\n]+)\}}",
        lambda match: rf"\ensuremath{{{match.group('num')}{{\mathrm{{{match.group('unit')}}}}}^{{{match.group('power')}}}}}",
        text,
    )


def repair_math_function_dollar_splits(text: str) -> str:
    """Rejoin math operators split by OCR-created dollar boundaries."""
    text = re.sub(
        r"\\lo(?<!\\)\$(?P<tail>g(?:_\{[^{}\n]+\}|_[A-Za-z0-9])?)(?<!\\)\$\s*(?P<arg>[A-Za-z0-9]+)",
        lambda match: rf"\ensuremath{{\log{match.group('tail')[1:]} {match.group('arg')}}}",
        text,
    )
    text = re.sub(
        r"\\lo(?<!\\)\$(?P<tail>g(?:_\{[^{}\n]+\}|_[A-Za-z0-9])?)(?<!\\)\$",
        lambda match: rf"\ensuremath{{\log{match.group('tail')[1:]}}}",
        text,
    )
    text = re.sub(
        r"\$(?P<fn>\\mathcal\{[^{}\n]+\})\$\((?P<arg>[^$\n]+)\)\$",
        lambda match: f"${match.group('fn')}({match.group('arg')})$",
        text,
    )
    text = re.sub(
        r"\$(?P<fn>\\(?:mathcal|mathrm|mathbf|mathit)\{[^{}\n]+\})\$\s*(?P<script>[_^]\{[^{}\n]+\})",
        lambda match: f"${match.group('fn')}{match.group('script')}$",
        text,
    )
    return text


def repair_bare_function_ensuremath_args(text: str) -> str:
    """Wrap bare function commands whose argument/base has already been repaired."""
    group_body = r"(?:[^{}]|\{[^{}]*\})*"
    ensure_arg = rf"\\ensuremath\{{(?P<arg>{group_body})\}}"
    ensure_base = rf"\\ensuremath\{{(?P<base>{group_body})\}}"

    def merge_balanced_function_args(line: str) -> str:
        pattern = re.compile(r"\\(?P<fn>sin|cos|tan|exp)\s*\\ensuremath\{")
        pieces: list[str] = []
        cursor = 0
        while True:
            match = pattern.search(line, cursor)
            if not match:
                pieces.append(line[cursor:])
                break
            body_start = match.end()
            depth = 1
            i = body_start
            while i < len(line):
                char = line[i]
                if char == "\\" and i + 1 < len(line):
                    i += 2
                    continue
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        arg = unwrap_ensuremath_balanced_line(line[body_start:i]).strip()
                        pieces.append(line[cursor : match.start()])
                        pieces.append(rf"\ensuremath{{\{match.group('fn')} {arg}}}")
                        cursor = i + 1
                        break
                i += 1
            else:
                pieces.append(line[cursor:])
                cursor = len(line)
                break
        return "".join(pieces)

    text = "\n".join(merge_balanced_function_args(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")
    text = re.sub(
        rf"\\(?P<fn>sin|cos|tan|exp)\s*{ensure_arg}",
        lambda match: rf"\ensuremath{{\{match.group('fn')} {unwrap_ensuremath_balanced_line(match.group('arg')).strip()}}}",
        text,
    )
    text = re.sub(
        r"\\exp\s*(?P<arg>\([^()\n]{1,80}\))",
        lambda match: rf"\ensuremath{{\exp {match.group('arg')}}}",
        text,
    )
    text = re.sub(
        rf"\\(?P<fn>log|ln|lg)_\{{{ensure_base}\}}\s*(?P<arg>[A-Za-z0-9]+)",
        lambda match: rf"\ensuremath{{\{match.group('fn')}_{{{unwrap_ensuremath_balanced_line(match.group('base')).strip()}}}{match.group('arg')}}}",
        text,
    )
    text = re.sub(
        rf"\\(?P<fn>log|ln|lg)\s*{ensure_arg}",
        lambda match: rf"\ensuremath{{\{match.group('fn')} {unwrap_ensuremath_balanced_line(match.group('arg')).strip()}}}",
        text,
    )
    math_group = r"\\ensuremath\{(?:[^{}]|\{[^{}]*\})*\}"
    paren_arg = rf"\((?:[^()\n]|{math_group}){{1,180}}\)"
    operator_arg_re = re.compile(
        rf"\\operatorname(?P<star>\*)?\{{(?P<name>[^{{}}\n]+)\}}\s*(?P<arg>{paren_arg}|{math_group})"
    )

    def operator_arg_repl(match: re.Match[str]) -> str:
        star = match.group("star") or ""
        arg = unwrap_ensuremath_balanced_line(match.group("arg"))
        return rf"\ensuremath{{\operatorname{star}{{{match.group('name')}}}{arg}}}"

    text = operator_arg_re.sub(operator_arg_repl, text)
    return text


def repair_trig_degree_splits(text: str) -> str:
    """Join OCR-split degree arguments after trig functions."""
    return re.sub(
        r"\\(?P<fn>sin|cos|tan)\s*(?P<head>\d+)\s*\\ensuremath\{(?P<tail>\d+\^\{\\circ\})\}",
        lambda match: rf"\ensuremath{{\{match.group('fn')} {match.group('head')}{match.group('tail')}}}",
        text,
    )


def repair_bare_inverse_trig_notation(text: str) -> str:
    """Wrap inverse-trig shorthand such as \sin^{-1}0.3 when it appears in prose."""
    return re.sub(
        r"\\(?P<fn>sin|cos|tan)\s*\^\s*\{\s*-\s*1\s*\}\s*(?P<arg>\d+(?:\.\d+)?)",
        lambda match: rf"\ensuremath{{\{match.group('fn')}^{{-1}}{match.group('arg')}}}",
        text,
    )


def repair_arrow_chain_lines(text: str) -> str:
    """Wrap coordinate-step arrow chains and remove OCR-escaped dollar separators."""
    out: list[str] = []
    for line in text.splitlines():
        if r"\xrightarrow" not in line:
            out.append(line)
            continue
        cleaned = line.replace(r"\$", "")
        if ":" in cleaned:
            prefix, tail = cleaned.split(":", 1)
            if line_math_like(tail):
                line = f"{prefix}: " + rf"\ensuremath{{{tail.strip()}}}"
            else:
                line = cleaned
        else:
            line = rf"\ensuremath{{{cleaned.strip()}}}" if line_math_like(cleaned) else cleaned
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_left_right_dollar_order(text: str) -> str:
    """Move a dollar before \left/\right when OCR emitted \left$(...)."""
    return re.sub(
        r"\\(?P<side>left|right)(?![A-Za-z])\s*(?<!\\)\$(?P<delim>\\[A-Za-z]+|[()[\]{}|.])",
        lambda match: rf"$\{match.group('side')}{match.group('delim')}",
        text,
    )


def repair_open_paren_dollar_ensuremath_power_splits(text: str) -> str:
    """Rejoin $($\ensuremath{...})^{n} fragments into one safe math span."""
    ensure_piece = r"\\ensuremath\{(?:[^{}]|\{[^{}]*\})*\}"
    pattern = re.compile(
        rf"\$\(\$(?P<body>(?:{ensure_piece}\s*){{1,8}})(?P<tail>\)\s*\^\{{[^{{}}\n]+\}})"
    )
    return pattern.sub(lambda match: rf"\ensuremath{{({match.group('body').strip()}{match.group('tail')}}}", text)


def repair_parenthesized_power_dollar_continuations(text: str) -> str:
    """Merge \ensuremath{(...}$x$)^{n} fragments created by inline dollar drift."""
    nested_group = r"(?:[^{}]|\{[^{}]*\})*"
    text = re.sub(
        rf"\\ensuremath\{{(?P<head>\([^}}$\n]{{1,120}})\}}\$(?P<mid>[^$\n]{{1,120}})\$"
        rf"(?P<tail>\)\s*\^\{{\\ensuremath\{{(?P<exp>{nested_group})\}}\}})\}}?",
        lambda match: rf"\ensuremath{{{match.group('head')}{match.group('mid')})^{{{unwrap_ensuremath_balanced_line(match.group('exp')).strip()}}}}}",
        text,
    )
    return re.sub(
        r"\\ensuremath\{(?P<head>\([^}$\n]{1,120})\}\$(?P<mid>[^$\n]{1,120})\$(?P<tail>\)\s*\^\{[^{}\n]+\})\}?\$?",
        lambda match: rf"\ensuremath{{{match.group('head')}{match.group('mid')}{match.group('tail')}}}",
        text,
    )


def repair_inline_parenthesized_power_dollar_splits(text: str) -> str:
    """Repair $(...$)^{n}$ fragments where the closing delimiter moved too early."""
    pattern = re.compile(r"\$\((?P<body>[^$\n]{1,160})\$(?P<tail>\)\s*\^\{[^{}\n]+\})\$?(?:\\\$)?")

    def repl(match: re.Match[str]) -> str:
        expr = f"({match.group('body')}{match.group('tail')}"
        if line_math_like(expr):
            return rf"\ensuremath{{{expr}}}"
        return match.group(0)

    return pattern.sub(repl, text)


def repair_array_exponent_artifacts(text: str) -> str:
    """Collapse single-cell arrays that OCR inserted as superscript bodies."""
    pattern = re.compile(
        r"\^\s*\\begin\{array\}\{[lcr]+\}\s*\{?(?P<body>[^{}\\&\n]{1,20})\}?\s*\\end\{array\}"
    )
    return pattern.sub(lambda match: rf"^{{{match.group('body').strip()}}}", text)


def repair_split_frac_lines(text: str) -> str:
    """Join OCR line breaks inserted inside a \frac command."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if r"\frac" in line and line.count("{") > line.count("}") and i + 1 < len(lines):
            merged = line.rstrip()
            j = i + 1
            while j < len(lines) and merged.count("{") > merged.count("}") and lines[j].strip():
                merged += lines[j].strip()
                j += 1
            if merged != line and merged.count("{") <= merged.count("}") + 1:
                out.append(merged)
                i = j
                continue
        out.append(line)
        i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_split_parenthesized_power_lines(text: str) -> str:
    """Join parenthesized powers split across adjacent OCR lines."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if i + 1 < len(lines) and "(" in line and ")" not in line[line.rfind("(") :]:
            nxt = lines[i + 1].strip()
            if nxt.startswith(r"\ensuremath{") and re.search(r"\)\s*\^\{", nxt):
                pos = line.rfind("(")
                prefix = line[:pos].rstrip()
                expr = line[pos:].strip() + nxt
                if line_math_like(expr):
                    out.append((prefix + " " if prefix else "") + rf"\ensuremath{{{expr}}}")
                    i += 2
                    continue
        out.append(line)
        i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_equation_variable_label_dollar_splits(text: str) -> str:
    """Move a one-token RHS back into math when OCR split equation labels with dollars."""
    return re.sub(
        r"\$(?P<head>[^$\n]{1,180}?=\s*)\$\s*(?P<rhs>[A-Za-z][A-Za-z0-9]*)\s*\$\s*(?P<label>\([^)\n]{1,30}\))",
        lambda match: f"${match.group('head')}{match.group('rhs')}$ {match.group('label')}",
        text,
    )


def repair_split_log_ensuremath_boundaries(text: str) -> str:
    """Rejoin OCR-split \log commands that were separated by \ensuremath spans."""
    pattern = re.compile(
        r"\\ensuremath\{(?P<body>.*?)\\lo\}\}\\ensuremath\{g(?P<script>_\{[^{}\n]+\}|_[A-Za-z0-9])\}\\ensuremath\{(?P<arg>(?:[^{}]|\{[^{}]*\})*)\}"
    )

    def repl(match: re.Match[str]) -> str:
        body = match.group("body").replace(r"\ensuremath{", "")
        body = drop_surplus_closing_braces(body)
        arg = unwrap_ensuremath_balanced_line(match.group("arg")).strip()
        return rf"\ensuremath{{{body}\log{match.group('script')}{arg}}}"

    return "\n".join(pattern.sub(repl, line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def repair_ensuremath_parenthesized_power_dollar_splits(text: str) -> str:
    """Remove dollar boundaries inserted between a parenthesized base and its exponent."""
    pattern = re.compile(
        r"\\ensuremath\{(?P<body>\([^$\n]{1,240}?)\$(?P<tail>\)\s*\^\{[^{}\n]+\})\}\$?"
    )
    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(
            lambda match: rf"\ensuremath{{{drop_surplus_closing_braces(match.group('body'))}{match.group('tail')}}}",
            text,
        )
    return text


def unwrap_display_math_environment_ensuremath(text: str) -> str:
    """Remove \ensuremath wrappers around full array/aligned lines inside $$ blocks."""
    def unwrap_simple_math_wrappers(line: str) -> str:
        marker = r"\ensuremath{"
        previous = None
        while previous != line:
            previous = line
            pieces: list[str] = []
            cursor = 0
            while True:
                idx = line.find(marker, cursor)
                if idx == -1:
                    pieces.append(line[cursor:])
                    break
                pieces.append(line[cursor:idx])
                pos = idx + len(marker)
                depth = 1
                i = pos
                while i < len(line):
                    if line[i] == "\\":
                        i += 2
                        continue
                    if line[i] == "{":
                        depth += 1
                    elif line[i] == "}":
                        depth -= 1
                        if depth == 0:
                            pieces.append(line[pos:i])
                            cursor = i + 1
                            break
                    i += 1
                else:
                    pieces.append(line[idx:])
                    cursor = len(line)
                    break
            line = "".join(pieces)
        return line

    def drop_surplus_closing_braces(line: str) -> str:
        line = re.sub(r"(\\end\{(?:array|aligned)\})\}+", r"\1", line)
        return line

    out: list[str] = []
    in_display = False
    for raw_line in text.splitlines():
        line = raw_line
        if line.strip() == "$$":
            in_display = not in_display
            out.append(line)
            continue
        if in_display and (r"\ensuremath{" in line or re.search(r"\\(?:begin|end)\{(?:array|aligned)", line)):
            if re.search(r"\\(?:begin|end)\{(?:array|aligned)", line):
                line = re.sub(r"\\end\{(array|aligned)(?!\})", r"\\end{\1}", line)
            line = unwrap_simple_math_wrappers(line)
            indent = line[: len(line) - len(line.lstrip())]
            body = line.strip()
            while body.startswith(r"\ensuremath{") and body.endswith("}"):
                body = body[len(r"\ensuremath{") : -1].strip()
            if r"\ensuremath{" in body or r"\begin{array}" in body or r"\begin{aligned}" in body:
                body = body.replace(r"\ensuremath{", "")
            body = drop_surplus_closing_braces(body)
            line = indent + body
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def remove_residual_inline_display_markers(text: str) -> str:
    """Remove inline $$ markers that are not standalone display delimiters."""
    out: list[str] = []
    for line in text.splitlines():
        if line.strip() != "$$":
            line = re.sub(r"\\\$(?=\d[^\\\n]*(?:\\dot|\\times|\\frac|\\sqrt|[_^=]))", "", line)
            line = line.replace(r"\$$", "")
            line = re.sub(r"^\s*(?<!\\)\$\$(?=\S)", "", line)
            line = re.sub(r"^\s*(?<!\\)\$\$\s+(?=\S)", "", line)
            line = re.sub(r"(?<!\\)\$\$(?=\s*$)", "", line)
            line = re.sub(r"\\\$\$?\s*$", "", line)
            dollars = list(re.finditer(r"(?<!\\)\$", line))
            if len(dollars) == 1:
                dollar = dollars[0]
                if re.match(r"^\s*(?:[-*]\s*)?\$(?=[\\A-Za-z0-9(])", line):
                    line = line[: dollar.start()] + line[dollar.end() :]
                elif line[dollar.end() :].lstrip().startswith(("=", ".", ",", ";", ":", "and ", "or ")):
                    line = line[: dollar.start()] + line[dollar.end() :]
                elif line_math_like(line[: dollar.start()]) or line_math_like(line[dollar.end() :]):
                    line = line[: dollar.start()] + line[dollar.end() :]
                elif not line[dollar.end() :].strip():
                    line = line[: dollar.start()] + line[dollar.end() :]
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_inline_double_dollar_splits(text: str) -> str:
    """Remove inline-only $$ fragments between adjacent math spans."""
    out: list[str] = []
    for line in text.splitlines():
        if line.strip() != "$$" and "$$" in line:
            line = line.replace(r"$$\ensuremath{", r"\ensuremath{")
            line = re.sub(r"(?<!\\)\$\$(?=\s*(?:\(|\\ensuremath\{|\\(?:frac|sqrt|left)|[A-Za-z0-9]))", "", line)
            line = re.sub(
                r"(?P<head>\\(?:log|ln|lg|sin|cos|tan)(?:_\s*\{[^{}\n]+\}|_[A-Za-z0-9])?)\$\$(?=\\ensuremath\{)",
                r"\g<head>",
                line,
            )
            line = re.sub(r"(?<=\})\$\$(?=\s*(?:[=,.;:]|\\ensuremath\{|[A-Za-z0-9+\-*/)]))", "", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def mask_balanced_ensuremath_spans(segment: str) -> tuple[str, list[str]]:
    """Replace balanced \ensuremath spans with placeholders for outer-text repairs."""
    marker = r"\ensuremath{"
    placeholders: list[str] = []
    pieces: list[str] = []
    cursor = 0
    while True:
        idx = segment.find(marker, cursor)
        if idx == -1:
            pieces.append(segment[cursor:])
            break
        pieces.append(segment[cursor:idx])
        depth = 1
        i = idx + len(marker)
        while i < len(segment):
            char = segment[i]
            if char == "\\" and i + 1 < len(segment):
                i += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    placeholder = f"@@ENSUREMATH{len(placeholders)}@@"
                    placeholders.append(segment[idx : i + 1])
                    pieces.append(placeholder)
                    cursor = i + 1
                    break
            i += 1
        else:
            pieces.append(segment[idx:])
            cursor = len(segment)
            break
    return "".join(pieces), placeholders


def restore_balanced_ensuremath_spans(segment: str, placeholders: list[str]) -> str:
    for idx, original in enumerate(placeholders):
        segment = segment.replace(f"@@ENSUREMATH{idx}@@", original)
    return segment


def wrap_bare_math_commands_global_final(text: str) -> str:
    """Wrap bare parser-readable math commands without reopening existing math spans."""
    placeholder = r"@@ENSUREMATH\d+@@"
    paren_placeholder_arg = rf"\([^()\n]*(?:{placeholder}[^()\n]*)*\)"
    placeholder_arg = rf"(?:{placeholder}|{paren_placeholder_arg}|[A-Za-z0-9]+(?:\([^()\n]*\))?)"
    script = r"(?:_\s*\{[^{}\n]+\}|_[A-Za-z0-9])?"

    def wrap_segment(segment: str) -> str:
        if not re.search(r"\\(?:frac|sqrt|binom|underbrace|left|log|ln|lg)(?![A-Za-z])", segment):
            return segment
        masked, placeholders = mask_balanced_ensuremath_spans(segment)

        def log_repl(match: re.Match[str]) -> str:
            script_clean = re.sub(r"\s+", "", match.group("script") or "")
            return rf"\ensuremath{{\{match.group('fn')}{script_clean} {match.group('arg')}}}"

        masked = wrap_bare_frac_commands(masked)
        masked = wrap_bare_sqrt_commands(masked)
        masked = wrap_bare_binom_commands(masked)
        masked = re.sub(
            r"\\underbrace\s*\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}\s*(?P<script>[_^]\s*\{(?:[^{}]|\{[^{}]*\})*\})",
            lambda match: rf"\ensuremath{{\underbrace{{{match.group('body')}}}{match.group('script')}}}",
            masked,
        )
        masked = re.sub(
            r"\\left\s*(?P<open>\\[A-Za-z]+|[()[\]{}|.])(?P<body>[^$\n]{1,240}?)\\right\s*(?P<close>\\[A-Za-z]+|[()[\]{}|.])",
            lambda match: rf"\ensuremath{{\left{match.group('open')}{match.group('body')}\right{match.group('close')}}}",
            masked,
        )
        masked = re.sub(
            rf"(?<!\\)\\(?P<fn>log|ln|lg)\s*(?P<script>{script})\s*(?P<arg>{placeholder_arg})",
            log_repl,
            masked,
        )
        return restore_balanced_ensuremath_spans(masked, placeholders)

    out: list[str] = []
    in_display = False
    structural_re = re.compile(r"^\s*\\(?:begin|end|includegraphics|caption|chapter|section|subsection|exerciseheading)\b")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "$$":
            in_display = not in_display
            out.append(line)
            continue
        if in_display or structural_re.match(line):
            out.append(line)
            continue
        out.append(apply_outside_dollar_math(line, wrap_segment).rstrip("\n"))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_parenthesis_dollar_splits(text: str) -> str:
    """Remove OCR dollars split inside parenthesized algebraic powers."""
    text = re.sub(
        r"\$\((?P<head>[^$\n]{1,30})\$(?=\\ensuremath\{)",
        lambda match: "(" + match.group("head"),
        text,
    )
    return re.sub(
        r"\$(?P<body>\([^$\n]{1,120}?\\ensuremath\{[^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*\}[^$\n]*?\)\s*\^\{[^{}\n]+\})",
        lambda match: rf"\ensuremath{{{match.group('body')}}}",
        text,
    )


def repair_labeled_aligned_openers(text: str) -> str:
    """Merge labels with aligned environments split across two OCR lines."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    opener_re = re.compile(r"^(?P<label>\s*[A-Za-z0-9.)]+\s*)\\ensuremath\{\\begin\{aligned\}\}\s*$")
    opener_inside_re = re.compile(r"^\s*\\ensuremath\{(?P<label>[A-Za-z0-9.)]+\s*)\\begin\{aligned\}\}\s*$")
    while i < len(lines):
        match = opener_re.match(lines[i]) or opener_inside_re.match(lines[i])
        if match and i + 1 < len(lines) and r"\end{aligned}" in lines[i + 1]:
            body = lines[i + 1].strip()
            body = unwrap_ensuremath_balanced_line(body)
            body = body.replace(r"\ensuremath{", "")
            body = drop_surplus_closing_braces(body)
            out.append(match.group("label") + rf"\ensuremath{{\begin{{aligned}} {body}}}")
            i += 2
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def unwrap_prose_only_display_blocks_final(text: str) -> str:
    """Remove display delimiters around blocks that contain only prose."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0

    def prose_only(block: list[str]) -> bool:
        meaningful = [line.strip() for line in block if line.strip()]
        if not meaningful:
            return True
        non_comment = [line for line in meaningful if not line.startswith("%")]
        if not non_comment:
            return True
        if any(line_math_like(line) and not display_body_line_is_prose(line) for line in non_comment):
            return False
        return any(re.search(r"[A-Za-z]", line) for line in non_comment)

    while i < len(lines):
        if lines[i].strip() != "$$":
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        block: list[str] = []
        while j < len(lines) and lines[j].strip() != "$$":
            block.append(lines[j])
            j += 1
        if j < len(lines) and prose_only(block):
            out.extend(block)
            i = j + 1
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def unwrap_structural_display_blocks_final(text: str) -> str:
    """Remove $$ delimiters around figures, lists, and other structural LaTeX blocks."""
    structural_re = re.compile(
        r"^\s*\\(?:begin|end)\{(?:figure|enumerate|itemize|tabularx?|longtable)\}|"
        r"^\s*\\(?:includegraphics|caption|centering|item|section|subsection|subsubsection|chapter)\b|"
        r"^\s*%.*source_page_idx"
    )
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() != "$$":
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        block: list[str] = []
        while j < len(lines) and lines[j].strip() != "$$":
            block.append(lines[j])
            j += 1
        if j < len(lines) and any(structural_re.search(line.strip()) for line in block):
            out.extend(block)
            i = j + 1
            continue
        prev = next((line.strip() for line in reversed(out) if line.strip()), "")
        if structural_re.search(prev):
            i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def remove_standalone_dollars_around_structural_context(text: str) -> str:
    """Line-local cleanup for $$ delimiters that still surround structural LaTeX."""
    structural_re = re.compile(
        r"^\s*(?:%.*source_page_idx|"
        r"\\(?:begin|end)\{(?:figure|enumerate|itemize|tabularx?|longtable|center)\}|"
        r"\\(?:includegraphics|caption|centering|item|section|subsection|subsubsection|chapter|exerciseheading)\b|"
        r"\\(?:setlength|renewcommand)\b|"
        r"\{?\\(?:small|scriptsize|footnotesize|tiny|normalsize)\b)"
    )
    begin_env_re = re.compile(r"\\begin\{(?:figure|enumerate|itemize|tabularx?|longtable|center)\}")
    end_env_re = re.compile(r"\\end\{(?:figure|enumerate|itemize|tabularx?|longtable|center)\}")

    def next_nonblank(lines: list[str], index: int) -> str:
        for value in lines[index + 1 :]:
            if value.strip():
                return value.strip()
        return ""

    def previous_nonblank(lines: list[str]) -> str:
        for value in reversed(lines):
            if value.strip():
                return value.strip()
        return ""

    lines = text.splitlines()
    out: list[str] = []
    retained_display_open = False
    structural_display_open = False
    structural_env_depth = 0
    sizing_group_open = False

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "$$":
            prev = previous_nonblank(out)
            nxt = next_nonblank(lines, index)
            in_structural_context = structural_env_depth > 0 or sizing_group_open
            if structural_display_open or in_structural_context or structural_re.search(prev):
                structural_display_open = False
                continue
            if structural_re.search(nxt):
                structural_display_open = True
                continue
            if retained_display_open:
                out.append(line)
                retained_display_open = False
                continue

            out.append(line)
            retained_display_open = True
            continue

        if stripped:
            if re.search(r"^\s*\{?\\(?:small|scriptsize|footnotesize|tiny)\b", stripped):
                sizing_group_open = True
            structural_env_depth += len(begin_env_re.findall(stripped))
            if structural_re.search(stripped) and not structural_display_open and structural_env_depth > 0:
                structural_display_open = True
            structural_env_depth = max(0, structural_env_depth - len(end_env_re.findall(stripped)))
            if sizing_group_open and (re.search(r"\\end\{(?:tabularx?|longtable|center)\}", stripped) or stripped == "}"):
                sizing_group_open = False
        out.append(line)

    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def split_prose_lines_out_of_display_blocks_final(text: str) -> str:
    """Close display math around prose lines accidentally absorbed into $$ blocks."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0

    def is_prose_display_line(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if stripped.startswith("%"):
            return True
        if display_body_line_is_prose(stripped):
            return True
        if re.fullmatch(r"[A-Za-z][A-Za-z ]{0,30}:?", stripped) and not line_math_like(stripped):
            return True
        return False

    def is_math_line(line: str) -> bool:
        stripped = line.strip()
        return bool(stripped and line_math_like(stripped) and not display_body_line_is_prose(stripped))

    while i < len(lines):
        if lines[i].strip() != "$$":
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        block: list[str] = []
        while j < len(lines) and lines[j].strip() != "$$":
            block.append(lines[j])
            j += 1
        if j >= len(lines):
            out.append(lines[i])
            i += 1
            continue
        if not any(is_prose_display_line(line) for line in block):
            out.append(lines[i])
            out.extend(block)
            out.append(lines[j])
            i = j + 1
            continue

        display_open = False
        for line in block:
            if is_prose_display_line(line):
                if display_open:
                    out.append("$$")
                    display_open = False
                out.append(line)
                continue
            if is_math_line(line):
                if not display_open:
                    out.append("$$")
                    display_open = True
                out.append(line)
                continue
            if display_open:
                out.append(line)
            else:
                out.append(line)
        if display_open:
            out.append("$$")
        i = j + 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def merge_scripts_after_ensuremath_spans(text: str) -> str:
    """Move scripts following a balanced \ensuremath span inside that span."""
    def ensuremath_span(value: str, start: int) -> tuple[int, int, int] | None:
        marker = r"\ensuremath{"
        if not value.startswith(marker, start):
            return None
        body_start = start + len(marker)
        depth = 1
        i = body_start
        while i < len(value):
            char = value[i]
            if char == "\\" and i + 1 < len(value):
                i += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return body_start, i, i + 1
            i += 1
        return None

    def parse_group(value: str, pos: int) -> tuple[str, int] | None:
        while pos < len(value) and value[pos].isspace():
            pos += 1
        if pos >= len(value) or value[pos] != "{":
            return None
        depth = 0
        start = pos + 1
        i = pos
        while i < len(value):
            char = value[i]
            if char == "\\" and i + 1 < len(value):
                i += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return value[start:i], i + 1
            i += 1
        return None

    def parse_following_script(value: str, pos: int) -> tuple[str, str, int] | None:
        while pos < len(value) and value[pos].isspace():
            pos += 1
        if value.startswith(r"\ensuremath{{}", pos):
            inner = pos + len(r"\ensuremath{{}")
            while inner < len(value) and value[inner].isspace():
                inner += 1
            if inner < len(value) and value[inner] in "_^":
                op = value[inner]
                group = parse_group(value, inner + 1)
                if group:
                    script, end = group
                    while end < len(value) and value[end].isspace():
                        end += 1
                    if end < len(value) and value[end] == "}":
                        return op, script, end + 1
        if pos < len(value) and value[pos] in "_^":
            op = value[pos]
            group = parse_group(value, pos + 1)
            if group:
                script, end = group
                return op, unwrap_ensuremath_balanced_line(script).strip(), end
            i = pos + 1
            while i < len(value) and value[i].isspace():
                i += 1
            if i < len(value) and re.match(r"[A-Za-z0-9]", value[i]):
                return op, value[i], i + 1
        return None

    def merge_line(line: str) -> str:
        pieces: list[str] = []
        cursor = 0
        marker = r"\ensuremath{"
        while True:
            idx = line.find(marker, cursor)
            if idx == -1:
                pieces.append(line[cursor:])
                break
            span = ensuremath_span(line, idx)
            if not span:
                pieces.append(line[cursor:])
                break
            body_start, body_end, end = span
            prefix_end = idx
            script_pos = end
            prefix = line[cursor:idx]
            stripped_prefix = prefix.rstrip()
            if stripped_prefix.endswith("{"):
                prefix_end = cursor + len(stripped_prefix) - 1
                after = end
                while after < len(line) and line[after].isspace():
                    after += 1
                if after < len(line) and line[after] == "}":
                    prefix = line[cursor:prefix_end]
                    script_pos = after + 1
                else:
                    prefix_end = idx
                    prefix = line[cursor:idx]
                    script_pos = end
            parsed = parse_following_script(line, script_pos)
            if not parsed:
                pieces.append(line[cursor:end])
                cursor = end
                continue
            op, script, script_end = parsed
            pieces.append(prefix)
            pieces.append(rf"\ensuremath{{{line[body_start:body_end]}{op}{{{script}}}}}")
            cursor = script_end
        return "".join(pieces)

    return "\n".join(merge_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def wrap_bare_power_expressions_with_inner_ensuremath(text: str) -> str:
    """Wrap text-mode powers whose exponent contains repaired math commands."""
    ensure_piece = r"\\ensuremath\{(?:[^{}]|\{[^{}]*\})*\}"
    body_piece = rf"(?:[^{{}}\n]|{ensure_piece}|\{{[^{{}}\n]*\}})+?"
    power_re = re.compile(
        rf"(?<![\\$A-Za-z0-9{{])(?P<expr>(?:[A-Za-z]|\d+(?:\.\d+)?)\s*\^\{{{body_piece}\}})"
    )
    group_power_re = re.compile(
        rf"(?<![\\$A-Za-z0-9{{])(?P<expr>(?:\[[^\]\n]{{1,180}}\]|\([^)\n]{{1,180}}\))\s*\^\{{{body_piece}\}})"
    )
    braced_atom_power_re = re.compile(
        rf"(?<![\\$A-Za-z0-9{{])(?P<expr>\{{[^{{}}\n]{{1,80}}\}}\s*\^\{{{body_piece}\}})"
    )
    braced_power_re = re.compile(
        rf"(?<![\\$A-Za-z0-9{{])(?P<expr>\{{\s*\([^{{}}\n]*(?:{ensure_piece})[^{{}}\n]*\)\s*\}}\s*\^\{{[^{{}}\n]+\}})"
    )
    coefficient_group_power_re = re.compile(
        rf"(?P<prefix>\d+(?:\.\d+)?)(?P<expr>\([^)\n]{{1,180}}\)\s*\^\{{{body_piece}\}})"
    )
    out: list[str] = []
    in_display = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "$$":
            in_display = not in_display
            out.append(line)
            continue
        if in_display or r"\begin{array}" in line or r"\begin{tabular" in line:
            out.append(line)
            continue

        line_has_math_context = bool(
            r"\ensuremath{" in line or re.search(r"\\(?:frac|sqrt|times|div|cdot|pm|log|ln|lg)\b", line)
        )

        def repl(match: re.Match[str]) -> str:
            expr = match.group("expr")
            if r"\ensuremath{" not in expr and not re.search(r"\\(?:times|div|cdot|pm|frac|sqrt)\b", expr):
                return rf"\ensuremath{{{expr}}}" if line_has_math_context else expr
            return rf"\ensuremath{{{expr}}}"

        adjacent_power = r"\d+(?:\.\d+)?\s*\^\{[^{}\n]+\}"
        line = re.sub(
            rf"(?P<prefix>(?:\\quad|\\ensuremath\{{\\(?:times|div|cdot|pm)\}})\s*)(?P<expr>{adjacent_power})",
            lambda match: match.group("prefix") + rf"\ensuremath{{{match.group('expr')}}}",
            line,
        )
        line = re.sub(
            rf"(?<![\\$A-Za-z0-9{{])(?P<expr>{adjacent_power})(?=\s*\\ensuremath\{{\\(?:times|div|cdot|pm)\}})",
            lambda match: rf"\ensuremath{{{match.group('expr')}}}",
            line,
        )
        line = coefficient_group_power_re.sub(
            lambda match: match.group("prefix") + rf"\ensuremath{{{match.group('expr')}}}"
            if r"\ensuremath{" in match.group("expr") or line_has_math_context
            else match.group(0),
            line,
        )
        line = re.sub(
            rf"(?P<prefix>\\quad\s*)(?P<expr>\([^)\n]{{1,180}}\)\s*\^\{{{body_piece}\}})",
            lambda match: match.group("prefix") + rf"\ensuremath{{{match.group('expr')}}}",
            line,
        )
        line = re.sub(
            rf"(?P<prefix>\d+(?:\.\d+)?)(?P<expr>[A-Za-z]\s*\^\{{{body_piece}\}})",
            lambda match: match.group("prefix") + rf"\ensuremath{{{match.group('expr')}}}"
            if r"\ensuremath{" in match.group("expr") or line_has_math_context
            else match.group(0),
            line,
        )
        line = group_power_re.sub(lambda match: rf"\ensuremath{{{match.group('expr')}}}", line)
        line = braced_atom_power_re.sub(lambda match: rf"\ensuremath{{{match.group('expr')}}}", line)
        line = braced_power_re.sub(lambda match: rf"\ensuremath{{{match.group('expr')}}}", line)
        out.append(power_re.sub(repl, line))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def wrap_leading_power_math_lines(text: str) -> str:
    """Wrap noisy derivation lines that begin with an exponent marker."""
    out: list[str] = []
    in_display = False
    in_table = False
    begin_table_re = re.compile(r"\\begin\{(?:tabularx?|longtable)\}")
    end_table_re = re.compile(r"\\end\{(?:tabularx?|longtable)\}")
    structural_re = re.compile(
        r"^\\(?:begin|end|section|subsection|subsubsection|chapter|item|includegraphics|caption)\b"
    )
    trigger_re = re.compile(r"^[+\-−]?\s*\^\{")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "$$":
            in_display = not in_display
            out.append(line)
            continue
        if begin_table_re.search(stripped):
            in_table = True
            out.append(line)
            if end_table_re.search(stripped):
                in_table = False
            continue
        if in_table and end_table_re.search(stripped):
            out.append(line)
            in_table = False
            continue
        if in_display or in_table or not stripped or structural_re.match(stripped) or "&" in stripped:
            out.append(line)
            continue
        if trigger_re.match(stripped) and line_math_like(stripped):
            indent = line[: len(line) - len(line.lstrip())]
            body = unwrap_ensuremath_balanced_line(stripped)
            line = indent + rf"\ensuremath{{{body}}}"
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def balanced_brace_group_span(value: str, pos: int) -> tuple[int, int, int] | None:
    """Return body-start, body-end, end for a balanced {...} group at pos."""
    while pos < len(value) and value[pos].isspace():
        pos += 1
    if pos >= len(value) or value[pos] != "{":
        return None
    depth = 1
    body_start = pos + 1
    i = body_start
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            i += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return body_start, i, i + 1
        i += 1
    return None


def ensuremath_span_at(value: str, start: int) -> tuple[int, int, int] | None:
    marker = r"\ensuremath"
    if not value.startswith(marker, start):
        return None
    return balanced_brace_group_span(value, start + len(marker))


def unwrap_nested_ensuremath_spans(text: str) -> str:
    """Remove balanced inner \ensuremath wrappers from outer \ensuremath bodies."""
    marker = r"\ensuremath"

    def repair_line(line: str) -> str:
        previous = None
        while previous != line:
            previous = line
            idx = 0
            pieces: list[str] = []
            cursor = 0
            changed = False
            while True:
                found = line.find(marker, idx)
                if found == -1:
                    pieces.append(line[cursor:])
                    break
                span = ensuremath_span_at(line, found)
                if not span:
                    idx = found + len(marker)
                    continue
                body_start, body_end, end = span
                body = line[body_start:body_end]
                if marker in body:
                    pieces.append(line[cursor:body_start])
                    pieces.append(unwrap_ensuremath_balanced_line(body))
                    cursor = body_end
                    changed = True
                idx = end
            if changed:
                line = "".join(pieces)
            else:
                break
        return line

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def normalize_math_command_argument_spacing(text: str) -> str:
    """Normalize math commands whose braced argument was separated by OCR spacing."""
    text = re.sub(r"\\boldsymbol\s+\{(?P<body>[^{}\n]{1,80})\}", r"\\boldsymbol{\g<body>}", text)
    text = re.sub(
        r"\\boldsymbol\s+\\ensuremath\{\{(?P<base>[A-Za-z])\}\s*(?P<script>[_^]\{[^{}\n]+\})\}",
        r"\\boldsymbol{\g<base>}\g<script>",
        text,
    )
    text = re.sub(r"\\(mathbf|mathrm|mathit|mathsf|boldsymbol)\s+\{", r"\\\1{", text)
    return text


def repair_empty_base_script_fragments(text: str) -> str:
    """Attach OCR-split empty-base scripts back to their preceding letter."""
    script_re = re.compile(
        r"(?P<base>\b[A-Za-z])\s*\\ensuremath\{\{\}\s*(?P<script>[_^]\{[^{}\n]+\})\}"
    )
    return script_re.sub(lambda match: rf"\ensuremath{{{match.group('base')}{match.group('script')}}}", text)


def repair_parenthesized_ensuremath_power_fragments(text: str) -> str:
    """Wrap text-mode powers applied to parenthesized ensuremath fragments."""
    def parse_script(value: str, pos: int) -> tuple[str, int] | None:
        while pos < len(value) and value[pos].isspace():
            pos += 1
        if pos >= len(value) or value[pos] not in "_^":
            return None
        op = value[pos]
        pos += 1
        while pos < len(value) and value[pos].isspace():
            pos += 1
        group = balanced_brace_group_span(value, pos)
        if group:
            body_start, body_end, end = group
            return op + "{" + unwrap_ensuremath_balanced_line(value[body_start:body_end]) + "}", end
        if pos < len(value) and re.match(r"[A-Za-z0-9+\-]", value[pos]):
            return op + "{" + value[pos] + "}", pos + 1
        return None

    def repair_line(line: str) -> str:
        pieces: list[str] = []
        cursor = 0
        idx = 0
        target = r"(\ensuremath"
        while True:
            found = line.find(target, idx)
            if found == -1:
                pieces.append(line[cursor:])
                break
            span = ensuremath_span_at(line, found + 1)
            if not span:
                idx = found + len(target)
                continue
            body_start, body_end, end = span
            close = end
            while close < len(line) and line[close].isspace():
                close += 1
            if close >= len(line) or line[close] != ")":
                idx = end
                continue
            script = parse_script(line, close + 1)
            if not script:
                idx = end
                continue
            script_text, script_end = script
            inner = unwrap_ensuremath_balanced_line(line[body_start:body_end])
            pieces.append(line[cursor:found])
            pieces.append(rf"\ensuremath{{({inner}){script_text}}}")
            cursor = script_end
            idx = script_end
        return "".join(pieces)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def repair_inline_frac_equal_splits(text: str) -> str:
    """Repair inline $\\frac...$ fragments and missing denominator braces before equals."""
    out: list[str] = []
    for line in text.splitlines():
        if r"\frac" in line:
            if "$" in line:
                line = re.sub(r"(?<!\\)\$\s*(\\frac\{[^$\n]+\})\s*(?<!\\)\$", r"\\ensuremath{\1}", line)
                line = re.sub(r"(?P<eq>=)\s*(?<!\\)\$\s*(?=\\frac)", r"\g<eq> ", line)
                line = re.sub(r"(?<=\})\s*(?<!\\)\$(?=\s*$|[.,;:])", "", line)
            if r"\boxed{\quad}" in line and "=" in line:
                line = re.sub(
                    r"(\\frac\{[^\n]{0,300}?\}\{[^\n]{0,300}?\\boxed\{\\quad\})(?=\s*=)",
                    r"\1}",
                    line,
                )
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_rowbreaks_inside_text_commands(text: str) -> str:
    """Close OCR-swallowed \text{...} groups before array row breaks."""
    out = []
    opener = re.compile(r"\\text\s*\{")
    for line in text.splitlines():
        if r"\begin{array}" not in line or r"\text" not in line or r"\\" not in line:
            out.append(line)
            continue
        cursor = 0
        pieces = []
        while match := opener.search(line, cursor):
            pieces.append(line[cursor:match.end()])
            index = match.end()
            depth = 1
            body_start = index
            while index < len(line):
                if line.startswith(r"\\", index):
                    if depth > 0:
                        pieces.append(line[body_start:index].rstrip())
                        pieces.append("}" * depth + r" \\ ")
                        cursor = index + 2
                        while cursor < len(line) and line[cursor].isspace():
                            cursor += 1
                    break
                if line[index] == "\\" and index + 1 < len(line) and line[index + 1] in "{}":
                    index += 2
                    continue
                if line[index] == "{":
                    depth += 1
                elif line[index] == "}":
                    depth -= 1
                    if depth == 0:
                        pieces.append(line[body_start:index + 1])
                        cursor = index + 1
                        break
                index += 1
            else:
                pieces.append(line[body_start:])
                cursor = len(line)
            if cursor >= len(line):
                break
        pieces.append(line[cursor:])
        out.append("".join(pieces))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_resizebox_math_array_closures(text: str) -> str:
    """Put the math delimiter before the resizebox argument's closing brace."""
    out = []
    for line in text.splitlines():
        if r"\resizebox" in line and r"\begin{array}" in line:
            line = re.sub(r"(\\end\{array\})\}\}\$\}", r"\1$}", line)
            line = re.sub(r"(\\end\{array\})\}(\$)(?=\\end\{center\})", r"\1\2}", line)
            line = re.sub(r"\}(\$)(?=\\end\{center\})", r"\1}", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def split_multiple_tags_in_equation_blocks(text: str) -> str:
    """Use align rows when OCR merged independently tagged equations."""
    pattern = re.compile(r"(?ms)\\begin\{equation\}\s*(?P<body>.*?)\s*\\end\{equation\}")

    def repair(match: re.Match[str]) -> str:
        body = match.group("body").strip()
        if len(re.findall(r"\\tag\s*\{", body)) < 2:
            return match.group(0)
        body = re.sub(
            r"(\\tag\s*\{[^{}]+\})\s*\\quad\s*(?=[^\n]*\\tag\s*\{)",
            r"\1 \\\\ ",
            body,
        )
        return "\\begin{align}\n" + body + "\n\\end{align}"

    return pattern.sub(repair, text)


def repair_extra_text_closers_inside_arrays(text: str) -> str:
    """Remove a premature outer closing brace after \text{...} inside one-line arrays."""
    text_body = r"(?:\\[{}]|[^{}\n])*"
    continuation_pattern = re.compile(
        rf"\\text\s*\{{(?P<body>{text_body})\}}\}}(?=\s*(?:\\ensuremath\{{|\\frac|\\sqrt|[A-Za-z0-9+\-=(]))"
    )
    punctuation_row_pattern = re.compile(
        rf"\\text\s*\{{(?P<body>{text_body})\}}\}}(?P<punc>[.,;:])(?=\s*\\\\)"
    )
    out: list[str] = []
    for line in text.splitlines():
        if r"\begin{array}" in line:
            line = continuation_pattern.sub(lambda match: rf"\text{{{match.group('body')}}}", line)
            line = punctuation_row_pattern.sub(
                lambda match: rf"\text{{{match.group('body')}}}{match.group('punc')}",
                line,
            )
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_premature_ensuremath_boundaries_inside_arrays(text: str) -> str:
    """Merge OCR-split ensuremath spans that interrupt a single array expression."""
    out: list[str] = []
    boundary_re = re.compile(r"\}\s*\\ensuremath\{(?=\\(?:frac|sqrt|begin\{array\})|[A-Za-z0-9+\-=(])")
    for line in text.splitlines():
        if r"\ensuremath{\begin{array}" in line and r"\end{array}" in line:
            line = boundary_re.sub(" ", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_empty_text_script_labels(text: str) -> str:
    """Close empty \text{} sub/superscript labels before continued expressions."""
    pattern = re.compile(r"(?P<script>[_^]\s*)\{\s*\\text\{\}\s*(?P<next>\+|\\\\)")
    return pattern.sub(lambda match: rf"{match.group('script')}{{\text{{}}}} {match.group('next')}", text)


def wrap_bare_empty_base_scripts(text: str) -> str:
    """Wrap text-mode {}^{} or {}_{} fragments, allowing nested simple math spans."""
    def repair_line(line: str) -> str:
        line = re.sub(
            r"(?<!\\ensuremath\{)\|(?P<op>[_^])\s*\{(?P<body>[^{}\n]*)\}\|",
            lambda _match: r"\ensuremath{|\square|}",
            line,
        )
        line = re.sub(
            r"(?<![A-Za-z0-9}\]\\])(?P<op>[_^])\s*\{(?P<body>\s*\\ensuremath\{[^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*\}|[^{}\n]{1,80})\}",
            lambda match: rf"\ensuremath{{{{}}{match.group('op')}{{{unwrap_ensuremath_balanced_line(match.group('body')).strip()}}}}}",
            line,
        )
        pieces: list[str] = []
        cursor = 0
        search = 0
        changed = False
        while True:
            match = re.search(r"\{\}\s*([_^])", line[search:])
            if not match:
                pieces.append(line[cursor:])
                break
            found = search + match.start()
            op = match.group(1)
            if line[max(0, found - len(r"\ensuremath{")) : found].endswith(r"\ensuremath{"):
                search = found + 2
                continue
            group_pos = search + match.end()
            span = balanced_brace_group_span(line, group_pos)
            if not span:
                search = found + 2
                continue
            body = line[span[0] : span[1]]
            pieces.append(line[cursor:found])
            pieces.append(rf"\ensuremath{{{{}}{op}{{{body}}}}}")
            cursor = span[2]
            search = span[2]
            changed = True
        return "".join(pieces) if changed else line

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def repair_nested_empty_base_script_fragments_final(text: str) -> str:
    """Wrap empty-base scripts whose body contains repaired math spans."""
    trigger_re = re.compile(r"(?<![\\A-Za-z0-9}\]])(?P<op>[_^])\s*(?=\{)")

    def clean_script_body(body: str, placeholders: list[str]) -> str:
        for idx, original in enumerate(placeholders):
            body = body.replace(f"@@ENSUREMATH{idx}@@", unwrap_ensuremath_balanced_line(original).strip())
        body = re.sub(r"\s+", " ", body).strip()
        return body

    def repair_line(line: str) -> str:
        masked, placeholders = mask_balanced_ensuremath_spans(line)
        if not placeholders and not re.search(r"(?<![\\A-Za-z0-9}\]])[_^]\s*\{", masked):
            return line
        pieces: list[str] = []
        cursor = 0
        search = 0
        changed = False
        while True:
            match = trigger_re.search(masked, search)
            if not match:
                pieces.append(masked[cursor:])
                break
            span = balanced_brace_group_span(masked, match.end())
            if not span:
                search = match.end()
                continue
            body = masked[span[0] : span[1]]
            stripped = body.strip()
            if (
                not stripped
                or len(stripped) > 160
                or not (
                    "@@ENSUREMATH" in stripped
                    or re.match(r"[).,;:]+", stripped)
                    or re.search(r"\\[A-Za-z]+", stripped)
                )
            ):
                search = span[2]
                continue
            cleaned = clean_script_body(body, placeholders)
            pieces.append(masked[cursor : match.start()])
            pieces.append(rf"\ensuremath{{{{}}{match.group('op')}{{{cleaned}}}}}")
            cursor = span[2]
            search = span[2]
            changed = True
        repaired = "".join(pieces) if changed else masked
        return restore_balanced_ensuremath_spans(repaired, placeholders)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def wrap_bare_matrix_environments(text: str) -> str:
    """Wrap single-line matrix/cases environments that OCR left in text mode."""
    matrix_re = re.compile(
        r"(?<!\\ensuremath\{)(?P<matrix>\\begin\{(?:(?:p|b|v|V)?matrix|cases)\}.*?\\end\{(?:(?:p|b|v|V)?matrix|cases)\})"
    )
    return matrix_re.sub(lambda match: rf"\ensuremath{{{match.group('matrix')}}}", text)


def wrap_multiline_bare_matrix_environments(text: str) -> str:
    """Wrap bare matrix/cases environments split across consecutive lines."""
    begin_re = re.compile(r"\\begin\{(?P<env>(?:p|b|v|V)?matrix|cases)\}")
    end_template = r"\end{{{env}}}"
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        begin = begin_re.search(line)
        if not begin or r"\ensuremath{" in line[: begin.start() + 1] or end_template.format(env=begin.group("env")) in line:
            out.append(line)
            i += 1
            continue
        env = begin.group("env")
        end_marker = end_template.format(env=env)
        parts = [line[begin.start() :]]
        prefix = line[: begin.start()]
        j = i + 1
        found_end = False
        suffix = ""
        while j < len(lines):
            current = lines[j]
            end_pos = current.find(end_marker)
            if end_pos != -1:
                found_end = True
                end_at = end_pos + len(end_marker)
                parts.append(current[:end_at])
                suffix = current[end_at:]
                break
            if not current.strip() or current.lstrip().startswith((r"\begin{", r"\end{", r"\item")):
                break
            parts.append(current)
            j += 1
        if not found_end:
            out.append(line)
            i += 1
            continue
        body = " ".join(part.strip() for part in parts if part.strip())
        out.append(prefix + rf"\ensuremath{{{body}}}" + suffix)
        i = j + 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_premature_matrix_begin_closure_final(text: str) -> str:
    """Merge OCR splits where a matrix begin is closed before its first cell."""
    cell = r"(?:[^{}]|\{[^{}]*\})*"
    pattern = re.compile(
        rf"\\ensuremath\{{\\begin\{{(?P<env>(?:p|b|v|V)?matrix)\}}\s*\}}\s*"
        rf"\\ensuremath\{{(?P<first>{cell})\}}"
        rf"(?P<tail>\s*\\\\[^\n]*?\\end\{{(?P=env)\}})\}}"
    )

    def repl(match: re.Match[str]) -> str:
        first = unwrap_ensuremath_balanced_line(match.group("first")).strip()
        tail = unwrap_ensuremath_balanced_line(match.group("tail")).strip()
        return rf"\ensuremath{{\begin{{{match.group('env')}}} {first} {tail}}}"

    return pattern.sub(repl, text)


def repair_math_commands_inside_text_commands(text: str) -> str:
    """Move simple math commands out of \text{...} groups inside math arrays."""
    math_command_re = re.compile(
        r"\\(?:frac|dfrac|tfrac)\{(?:[^{}]|\{[^{}]*\})*\}\{(?:[^{}]|\{[^{}]*\})*\}|"
        r"\\sqrt\s*(?:\[[^\[\]\n]*\])?\s*\{(?:[^{}]|\{[^{}]*\})*\}|"
        r"\\(?:mathbb|mathrm|mathbf|mathit)\s*\{(?:[^{}]|\{[^{}]*\})*\}|"
        r"(?:[A-Za-z][A-Za-z0-9]*)(?:[_^](?:\{(?:[^{}]|\{[^{}]*\})*\}|[A-Za-z0-9]))+[A-Za-z0-9]*(?:\([^()\n]*\))?|"
        r"\\(?:left|right)(?:\\[A-Za-z]+|[()[\].|])|"
        r"\\(?:in|notin|ni|mid|nmid|subset|subseteq|supset|supseteq|leqslant|geqslant|leq|geq|neq|approx|simeq|sim|equiv|triangle)(?![A-Za-z])|"
        r"\\(?:alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|iota|kappa|lambda|mu|nu|xi|pi|varpi|rho|varrho|sigma|varsigma|tau|upsilon|phi|varphi|chi|psi|omega)(?![A-Za-z])"
    )

    def split_body(body: str) -> str:
        found = math_command_re.search(body)
        if not found:
            return rf"\text{{{body}}}"
        pieces: list[str] = []
        cursor = 0
        for math_match in math_command_re.finditer(body):
            pre = body[cursor : math_match.start()]
            if pre:
                pieces.append(rf"\text{{{pre}}}")
            pieces.append(math_match.group(0))
            cursor = math_match.end()
        tail = body[cursor:]
        if tail:
            pieces.append(rf"\text{{{tail}}}")
        return " ".join(piece for piece in pieces if piece)

    def repair_line(line: str) -> str:
        marker = r"\text"
        pieces: list[str] = []
        cursor = 0
        search = 0
        changed = False
        while True:
            found = line.find(marker, search)
            if found == -1:
                pieces.append(line[cursor:])
                break
            if found + len(marker) < len(line) and line[found + len(marker)].isalpha():
                search = found + len(marker)
                continue
            span = balanced_brace_group_span(line, found + len(marker))
            if not span:
                search = found + len(marker)
                continue
            body = line[span[0] : span[1]]
            replacement = split_body(body)
            pieces.append(line[cursor:found])
            pieces.append(replacement)
            cursor = span[2]
            search = span[2]
            changed = changed or replacement != line[found : span[2]]
        return "".join(pieces) if changed else line

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def strip_nested_math_dollars_from_array_text(text: str) -> str:
    """Remove illegal nested math shifts from text commands already inside arrays."""
    currency_placeholder = "@@WORKER_V2_ESCAPED_DOLLAR@@"
    out: list[str] = []
    for line in text.splitlines():
        if r"\begin{array}" not in line or "$" not in line:
            out.append(line)
            continue
        pieces: list[str] = []
        cursor = 0
        search = 0
        while True:
            found = line.find(r"\text", search)
            if found == -1:
                pieces.append(line[cursor:])
                break
            span = balanced_brace_group_span(line, found + len(r"\text"))
            if not span:
                search = found + len(r"\text")
                continue
            pieces.append(line[cursor:found])
            body = line[span[0]:span[1]].replace(r"\$", currency_placeholder)
            body = body.replace("$", "").replace(currency_placeholder, r"\$")
            pieces.append(r"\text{" + body + "}")
            cursor = span[2]
            search = span[2]
        out.append("".join(pieces))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def move_array_end_out_of_text_command(text: str) -> str:
    """Close OCR text groups before an array environment ending they swallowed."""
    out: list[str] = []
    for line in text.splitlines():
        end_positions = [match.start() for match in re.finditer(r"\\end\{array\}", line)]
        if not end_positions:
            out.append(line)
            continue
        for end_pos in reversed(end_positions):
            candidates: list[tuple[int, tuple[int, int, int]]] = []
            search = 0
            while True:
                found = line.find(r"\text", search)
                if found == -1:
                    break
                span = balanced_brace_group_span(line, found + len(r"\text"))
                if not span:
                    search = found + len(r"\text")
                    continue
                if span[0] <= end_pos < span[1]:
                    candidates.append((found, span))
                search = span[2]
            if not candidates:
                continue
            found, span = min(candidates, key=lambda item: item[0])
            marker_offset = line.find(r"\end{array}", span[0], span[1])
            if marker_offset == -1 or line[marker_offset + len(r"\end{array}") : span[1]].strip():
                continue
            body = line[span[0]:marker_offset].rstrip()
            line = line[:found] + rf"\text{{{body}}} \end{{array}}" + line[span[2]:]
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def replace_array_equation_tags_with_visible_labels(text: str) -> str:
    """Render equation tags inside arrays as labels, where amsmath forbids \tag."""
    array_pattern = re.compile(
        r"(?s)(\\begin\{array\}\{[^{}]+\})(.*?)(\\end\{array\})"
    )

    def repair(match: re.Match[str]) -> str:
        body = re.sub(
            r"\\tag\s*\{([^{}]+)\}",
            lambda tag: rf"\quad\text{{({tag.group(1)})}}",
            match.group(2),
        )
        body = re.sub(
            r"\\tag\s+([^\\\n]+?)(?=\s*\\\\)",
            lambda tag: rf"\quad\text{{{tag.group(1).rstrip(' .}')}}}.",
            body,
        )
        return match.group(1) + body + match.group(3)

    return array_pattern.sub(repair, text)


def replace_bare_equation_tags_with_visible_annotations(text: str) -> str:
    """Recover OCR prose emitted as a malformed unbraced equation tag."""
    return re.sub(
        r"\\tag\s+(?!\{)([^\\\n]+?)(?:\\\})?(?=\s*(?:\n|\$|$))",
        lambda tag: rf"\quad\text{{{tag.group(1).rstrip(' .}')}}}.",
        text,
    )


def split_joined_math_spacing_commands(text: str) -> str:
    """Separate OCR-joined variables from TeX spacing commands."""
    return re.sub(r"\\(qquad|quad)(?=[A-Za-z])", r"\\\1 ", text)


def repair_double_closing_brace_before_array_rowbreak(text: str) -> str:
    """Drop one extra unescaped brace from OCR fragments like \frac{...}{...}} \\."""
    out: list[str] = []
    pattern = re.compile(r"(?<!\\)\}\s*\}\s*(?=\\\\(?!\s*\\hline)|\\end\{array\})")
    for line in text.splitlines():
        if r"\begin{array}" in line:
            line = pattern.sub(r"} ", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_hline_leaked_inside_ensuremath_arrays(text: str) -> str:
    """Move table row terminators out of small math arrays embedded in tabular cells."""
    pattern = re.compile(
        r"\\ensuremath\{(?P<body>\\begin\{array\}\{[^{}\n]+\}.*?)"
        r"\s*\\\\\s*\\hline\s*\\end\{array\}\s*\\\\\s*\\hline\}",
    )

    def repl(match: re.Match[str]) -> str:
        body = match.group("body").rstrip()
        return rf"\ensuremath{{{body}\end{{array}}}} \\ \hline"

    return pattern.sub(repl, text)


def repair_array_column_spec_content_leak(text: str) -> str:
    """Move OCR-leaked math content out of array column specifications."""
    marker = r"\begin{array}"

    def repair_line(line: str) -> str:
        pieces: list[str] = []
        cursor = 0
        search = 0
        changed = False
        while True:
            found = line.find(marker, search)
            if found == -1:
                pieces.append(line[cursor:])
                break
            spec_span = balanced_brace_group_span(line, found + len(marker))
            if not spec_span:
                search = found + len(marker)
                continue
            spec = line[spec_span[0] : spec_span[1]]
            if re.fullmatch(r"[lcr|\s]+", spec):
                search = spec_span[2]
                continue
            good = re.match(r"\s*([lcr|\s]+)", spec)
            good_spec = re.sub(r"\s+", " ", good.group(1)).strip() if good else "l"
            leaked = spec[good.end() :].strip() if good else spec.strip()
            if not good_spec:
                good_spec = "l"
            if not leaked:
                search = spec_span[2]
                continue
            pieces.append(line[cursor:found])
            pieces.append(rf"\begin{{array}}{{{good_spec}}} {leaked}")
            cursor = spec_span[2]
            search = spec_span[2]
            changed = True
        return "".join(pieces) if changed else line

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def repair_missing_frac_denominator_close_before_rowbreak(text: str) -> str:
    """Insert a missing denominator brace before a top-level array row break."""
    def repair_line(line: str) -> str:
        pieces: list[str] = []
        cursor = 0
        search = 0
        changed = False
        while True:
            found = line.find(r"\frac", search)
            if found == -1:
                pieces.append(line[cursor:])
                break
            if found + len(r"\frac") < len(line) and line[found + len(r"\frac")].isalpha():
                search = found + len(r"\frac")
                continue
            numerator = balanced_brace_group_span(line, found + len(r"\frac"))
            if not numerator:
                search = found + len(r"\frac")
                continue
            pos = numerator[2]
            while pos < len(line) and line[pos].isspace():
                pos += 1
            if pos >= len(line) or line[pos] != "{":
                search = numerator[2]
                continue
            depth = 1
            i = pos + 1
            insert_at: int | None = None
            while i < len(line):
                if line.startswith(r"\\", i) and depth == 1:
                    insert_at = i
                    break
                if line.startswith(r"\end{array}", i) and depth == 1:
                    insert_at = i
                    break
                char = line[i]
                if char == "\\":
                    i += 2
                    continue
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            if insert_at is None:
                search = max(i + 1, numerator[2])
                continue
            pieces.append(line[cursor:insert_at])
            pieces.append("}")
            cursor = insert_at
            search = insert_at + 2
            changed = True
        return "".join(pieces) if changed else line

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def repair_placeholder_ensuremath_fraction_triplets(text: str) -> str:
    """Repair \frac{\ensuremath}{num}{den} OCR fragments into ordinary fractions."""
    marker = r"\frac{\ensuremath}"

    def group_until_rowbreak(value: str, pos: int) -> tuple[str, int] | None:
        while pos < len(value) and value[pos].isspace():
            pos += 1
        if pos >= len(value) or value[pos] != "{":
            return None
        depth = 1
        start = pos + 1
        i = start
        while i < len(value):
            if value.startswith(r"\\", i) and depth == 1:
                return value[start:i].strip(), i
            if value.startswith(r"\end{array}", i) and depth == 1:
                return value[start:i].strip(), i
            char = value[i]
            if char == "\\":
                i += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return value[start:i].strip(), i + 1
            i += 1
        return None

    def repair_line(line: str) -> str:
        pieces: list[str] = []
        cursor = 0
        search = 0
        changed = False
        while True:
            found = line.find(marker, search)
            if found == -1:
                pieces.append(line[cursor:])
                break
            first = balanced_brace_group_span(line, found + len(marker))
            if not first:
                search = found + len(marker)
                continue
            second = group_until_rowbreak(line, first[2])
            if not second:
                search = first[2]
                continue
            numerator = line[first[0] : first[1]].strip()
            denominator, end = second
            if not numerator or not denominator:
                search = end
                continue
            pieces.append(line[cursor:found])
            pieces.append(rf"\frac{{{numerator}}}{{{denominator}}}")
            cursor = end
            search = end
            changed = True
        return "".join(pieces) if changed else line

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def repair_split_division_boxed_equations(text: str) -> str:
    """Rejoin OCR-split fraction-division fill-in equations."""
    frac = r"\\frac\{[^{}\n]+\}\{[^{}\n]+\}"
    pattern = re.compile(
        rf"\\ensuremath\{{(?P<lhs>{frac})\}}\s+"
        rf"\\ensuremath\{{\\div\}}\s+\\boxed\{{\\quad\}}\}}?\s*=\s*"
        rf"\\ensuremath\{{(?:\\ensuremath\{{)?(?P<rhs>{frac})\}}*"
    )
    return pattern.sub(
        lambda match: rf"\ensuremath{{{match.group('lhs')} \div \boxed{{\quad}}}} = \ensuremath{{{match.group('rhs')}}}",
        text,
    )


def repair_inline_array_continuation_artifacts(text: str) -> str:
    """Flatten OCR-inserted one-line arrays that only hold a continued equation row."""
    pattern = re.compile(
        r"(?P<head>[A-Za-z0-9)\]}])\\begin\{array\}\{l\}\s*\\\\\s*=\s*"
        r"(?P<body>[^\\{}\n]{1,120}?)\s*\\end\{array\}\}*\s*"
        r"(?:\\ensuremath\{(?P<tail_wrapped>\\[A-Za-z]+\{[^{}\n]+\}|[^{}\n]{1,80})\}|"
        r"(?P<tail_bare>\\[A-Za-z]+\{[^{}\n]+\}))"
    )
    return pattern.sub(
        lambda match: f"{match.group('head')} \\\\ = {match.group('body').strip()}"
        f"{match.group('tail_wrapped') or match.group('tail_bare')}",
        text,
    )


def repair_single_group_frac_with_trailing_denominator(text: str) -> str:
    """Split \frac{numerator{denominator}} OCR artifacts into two arguments."""
    def trailing_group(body: str) -> tuple[str, str] | None:
        end = len(body.rstrip())
        if end == 0 or body[end - 1] != "}":
            return None
        depth = 0
        start = None
        i = end - 1
        while i >= 0:
            char = body[i]
            if char == "}" and (i == 0 or body[i - 1] != "\\"):
                depth += 1
            elif char == "{" and (i == 0 or body[i - 1] != "\\"):
                depth -= 1
                if depth == 0:
                    start = i
                    break
            i -= 1
        if start is None or start == 0:
            return None
        numerator = body[:start].rstrip()
        denominator = body[start + 1 : end - 1].strip()
        if not numerator or not denominator:
            return None
        if not re.search(r"[_^\\]|[A-Za-z0-9]", numerator + denominator):
            return None
        return numerator, denominator

    def repair_line(line: str) -> str:
        pieces: list[str] = []
        cursor = 0
        idx = 0
        while True:
            found = line.find(r"\frac", idx)
            if found == -1:
                pieces.append(line[cursor:])
                break
            if found + len(r"\frac") < len(line) and line[found + len(r"\frac")].isalpha():
                idx = found + len(r"\frac")
                continue
            first = balanced_brace_group_span(line, found + len(r"\frac"))
            if not first:
                idx = found + len(r"\frac")
                continue
            body_start, body_end, end = first
            check = end
            while check < len(line) and line[check].isspace():
                check += 1
            if check < len(line) and line[check] == "{":
                idx = end
                continue
            split = trailing_group(line[body_start:body_end])
            if not split:
                idx = end
                continue
            numerator, denominator = split
            pieces.append(line[cursor:found])
            pieces.append(rf"\frac{{{numerator}}}{{{denominator}}}")
            cursor = end
            idx = end
        return "".join(pieces)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def repair_array_row_premature_ensuremath_closures(text: str) -> str:
    """Remove premature ensuremath-closing braces inside single-line arrays."""
    out: list[str] = []
    for line in text.splitlines():
        if r"\ensuremath{\begin{array}" in line:
            line = re.sub(r"(?<!\\end\{array\})(?<!\\end\{aligned\})\}\}\s*(\\\\\s*=)", r"} \1", line)
            line = re.sub(r"(\\ensuremath\{(?:[^{}]|\{[^{}]*\})+\})\}\s*([+\-])", r"\1 \2", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def wrap_math_dominant_expression_lines(text: str) -> str:
    """Wrap whole lines that are mathematical expressions rather than prose."""
    out: list[str] = []
    in_display = False
    in_table = False
    begin_table_re = re.compile(r"\\begin\{(?:tabularx?|longtable)\}")
    end_table_re = re.compile(r"\\end\{(?:tabularx?|longtable)\}")
    structural_re = re.compile(
        r"^\\(?:begin|end|section|subsection|subsubsection|chapter|item|includegraphics|caption)\b"
    )
    math_signal_re = re.compile(
        r"\\(?:frac|sqrt|ensuremath|therefore|because|Longleftrightarrow|longleftrightarrow|"
        r"bigl|bigr|boldsymbol|cup|cap|varnothing|times|div|cdot|pm|neq|leq|geq|log|overline)\b|"
        r"[_^=<>]"
    )
    prose_word_re = re.compile(r"[A-Za-z]{4,}")
    allowed_words = {
        "boldsymbol",
        "ensuremath",
        "therefore",
        "because",
        "Longleftrightarrow",
        "longleftrightarrow",
        "varnothing",
        "overline",
        "text",
        "frac",
        "sqrt",
        "times",
        "div",
        "cdot",
        "quad",
        "log",
        "left",
        "right",
        "bigl",
        "bigr",
        "mathrm",
        "mathbf",
        "mathit",
    }

    def prose_words(body: str) -> list[str]:
        cleaned = re.sub(r"\\text\s*\{[^{}\n]*\}", " ", body)
        commands = set(re.findall(r"\\([A-Za-z]+)", cleaned))
        cleaned = re.sub(r"\\[A-Za-z]+", " ", cleaned)
        words = prose_word_re.findall(cleaned)
        return [word for word in words if word not in allowed_words and word not in commands]

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "$$":
            in_display = not in_display
            out.append(line)
            continue
        if begin_table_re.search(stripped):
            in_table = True
            out.append(line)
            if end_table_re.search(stripped):
                in_table = False
            continue
        if in_table and end_table_re.search(stripped):
            out.append(line)
            in_table = False
            continue
        if in_display or in_table or not stripped or stripped.startswith("%") or structural_re.match(stripped) or "&" in stripped:
            out.append(line)
            continue
        if not math_signal_re.search(stripped):
            out.append(line)
            continue
        body = unwrap_ensuremath_balanced_line(stripped)
        if prose_words(body):
            out.append(line)
            continue
        indent = line[: len(line) - len(line.lstrip())]
        out.append(indent + rf"\ensuremath{{{body}}}")
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def final_line_balance_cleanup(text: str) -> str:
    """Remove inline math dollars inside ensuremath and trim surplus line-end braces."""
    split_command_re = re.compile(
        r"\\(wedge|vee|land|lor|rightarrow|leftarrow|leftrightarrow|Rightarrow|Leftarrow|"
        r"Longleftrightarrow|longleftrightarrow|subseteq|subset|supseteq|supset|cup|cap)([A-Za-z])\b"
    )
    set_membership_re = re.compile(r"\\(?P<cmd>in|notin)(?=[A-Z0-9])")

    def brace_balance(line: str) -> int:
        balance = 0
        i = 0
        while i < len(line):
            if line[i] == "\\" and i + 1 < len(line):
                i += 2
                continue
            if line[i] == "{":
                balance += 1
            elif line[i] == "}":
                balance -= 1
            i += 1
        return balance

    def drop_surplus_trailing_braces(line: str) -> str:
        balance = brace_balance(line)
        while balance < 0:
            end = len(line.rstrip())
            if end == 0 or line[end - 1] != "}":
                break
            line = line[: end - 1] + line[end:]
            balance += 1
        balance = brace_balance(line)
        if 0 < balance <= 2 and r"\ensuremath{" in line and not line.rstrip().endswith(("{", "\\")):
            line = line + ("}" * balance)
        return line

    out: list[str] = []
    for line in text.splitlines():
        if r"\ensuremath{" in line and "$" in line:
            line = re.sub(r"(?<!\\)\$", "", line)
        if r"\ensuremath{" in line and r"\textasciicircum{}" in line:
            line = line.replace(r"\textasciicircum{}", r"\wedge")
        if r"\ensuremath{" in line:
            line = line.replace(r"\(\square\)", r"\square")
            line = re.sub(
                r"\\not\s*\\ensuremath\{(?P<body>[^{}\n]+)\}",
                r"\\ensuremath{\\not \g<body>}",
                line,
            )
        line = split_command_re.sub(r"\\\1 \2", line)
        line = set_membership_re.sub(r"\\\g<cmd> ", line)
        line = drop_surplus_trailing_braces(line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def drop_standalone_display_dollars_final(text: str) -> str:
    """Drop unreliable raw $$ display delimiters after math fragments are protected."""
    out = [line for line in text.splitlines() if line.strip() != "$$"]
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def neutralize_broken_inline_dollars_in_prose(text: str) -> str:
    """Remove unmatched inline math dollars from prose lines with comparison math."""
    out: list[str] = []
    for line in text.splitlines():
        dollars = re.findall(r"(?<!\\)\$", line)
        if len(dollars) % 2 == 1:
            prose_words = re.findall(r"[A-Za-z]{3,}", line)
            if len(prose_words) >= 4 and re.search(r"(?:[<>=]|\\(?:frac|sqrt|times|div|leq|geq|to)\b)", line):
                line = re.sub(r"(?<!\\)\$", "", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_text_parallel_bars_final(text: str) -> str:
    """Render text-mode OCR parallel markers as math parallel symbols."""
    structural_re = re.compile(
        r"^\\(?:begin|end)\{|^\\(?:chapter|section|subsection|subsubsection|caption|includegraphics|item)\b"
    )
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if (
            r"\|" in line
            and r"\begin{" not in line
            and not structural_re.match(stripped)
        ):
            masked, placeholders = mask_balanced_ensuremath_spans(line)
            masked = masked.replace(r"\|", r"\ensuremath{\parallel}")
            line = restore_balanced_ensuremath_spans(masked, placeholders)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_split_integral_command_artifacts_final(text: str) -> str:
    """Merge OCR-split integral commands such as \in t back into \int."""
    differential_re = re.compile(
        r"(?:\\mathrm\{d\}|\\ensuremath\{\\mathrm\{d\}\}|(?<![A-Za-z])d\s*[A-Za-z]\b|(?<![A-Za-z])dx\b|\\ln\b)"
    )
    split_integral_ensuremath_re = re.compile(
        r"\\ensuremath\{\\in\}\s*t(?=(?:\s|_|\\ensuremath|\{|\(|\\frac|\\mathrm))"
    )
    split_integral_re = re.compile(
        r"\\in\s+t(?=(?:\s|_|\\ensuremath|\{|\(|\\frac|\\mathrm))"
    )
    out: list[str] = []
    for line in text.splitlines():
        if differential_re.search(line):
            line = split_integral_ensuremath_re.sub(r"\\ensuremath{\\int}", line)
            line = split_integral_re.sub(r"\\int", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_fractional_script_bound_artifacts_final(text: str) -> str:
    """Repair OCR bounds like _\frac{\pi}\ensuremath{{12}^{...}}."""
    nested = r"(?:[^{}\n]|\{[^{}\n]*\})+"
    pattern = re.compile(
        rf"_(?P<frac>\\frac\{{(?P<num>{nested})\}})\\ensuremath\{{\{{(?P<den>{nested})\}}\^\{{(?P<sup>{nested})\}}\}}"
    )

    def repl(match: re.Match[str]) -> str:
        num = match.group("num").strip()
        den = match.group("den").strip()
        sup = match.group("sup").strip()
        return rf"_{{\frac{{{num}}}{{{den}}}}}^{{{sup}}}"

    return pattern.sub(repl, text)


def repair_binary_command_ensuremath_argument_final(text: str) -> str:
    """Repair \frac/\binom second arguments emitted as adjacent \ensuremath groups."""
    command_re = re.compile(r"\\(?P<cmd>frac|dfrac|tfrac|binom)(?![A-Za-z])")

    def read_group(value: str, start: int) -> tuple[str, int] | None:
        while start < len(value) and value[start].isspace():
            start += 1
        if start >= len(value) or value[start] != "{":
            return None
        depth = 0
        index = start
        while index < len(value):
            char = value[index]
            if char == "\\":
                index += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return value[start + 1 : index], index + 1
            index += 1
        return None

    def unwrap_nested_ensuremath(value: str) -> str:
        previous = None
        current = value.strip()
        while previous != current and current.startswith(r"\ensuremath{"):
            previous = current
            unwrapped = unwrap_ensuremath_balanced_line(current).strip()
            if unwrapped == current:
                break
            current = unwrapped
        return current

    def repair_line(line: str) -> str:
        pieces: list[str] = []
        cursor = 0
        while True:
            match = command_re.search(line, cursor)
            if not match:
                pieces.append(line[cursor:])
                break
            pieces.append(line[cursor : match.start()])
            first = read_group(line, match.end())
            if not first:
                pieces.append(line[match.start() : match.end()])
                cursor = match.end()
                continue
            probe = first[1]
            while probe < len(line) and line[probe].isspace():
                probe += 1
            marker = r"\ensuremath"
            if not line.startswith(marker, probe):
                pieces.append(line[match.start() : first[1]])
                cursor = first[1]
                continue
            second = read_group(line, probe + len(marker))
            if not second:
                pieces.append(line[match.start() : first[1]])
                cursor = first[1]
                continue
            arg = unwrap_nested_ensuremath(second[0])
            pieces.append(rf"\{match.group('cmd')}{{{first[0]}}}{{{arg}}}")
            cursor = second[1]
        return "".join(pieces)

    lines = [repair_line(line) for line in text.splitlines()]
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def repair_text_command_alignment_artifacts_final(text: str) -> str:
    """Keep literal alignment markers inside \text{...} from breaking math arrays."""
    envs = r"(?:array|aligned|cases|(?:p|b|v|V)?matrix)"
    swallowed_end_re = re.compile(rf"\\text\{{(?P<body>(?:\\[{{}}]|[^{{}}\n])*?)\\end\{{(?P<env>{envs})\}}\}}")
    text_re = re.compile(r"\\text\{(?P<body>(?:\\[{}]|[^{}\n])*)\}")

    def escape_alignment_tabs(value: str) -> str:
        return re.sub(r"(?<!\\)&", r"\\&", value)

    def move_end(match: re.Match[str]) -> str:
        body = escape_alignment_tabs(match.group("body").rstrip())
        return rf"\text{{{body}}}\end{{{match.group('env')}}}"

    text = swallowed_end_re.sub(move_end, text)

    def escape_text_body(match: re.Match[str]) -> str:
        body = match.group("body")
        if "&" not in body:
            return match.group(0)
        return rf"\text{{{escape_alignment_tabs(body)}}}"

    return text_re.sub(escape_text_body, text)


def repair_caption_textbackslash_artifacts_final(text: str) -> str:
    """Remove file-path captions and render symbolic backslashes as set difference."""
    caption_re = re.compile(r"\\caption(?![A-Za-z])")
    setminus_re = re.compile(
        r"(?<![A-Za-z0-9])(?P<left>[A-Za-z0-9]+)\s*\\textbackslash\{\}\s*(?P<right>[A-Za-z0-9]+)(?![A-Za-z0-9])"
    )

    def raw_file_like_caption(value: str) -> bool:
        lowered = value.lower()
        if re.search(r"\.(?:indd|pdf|jpe?g|png|eps|ai|svg)\b", lowered):
            return True
        if value.count(r"\textbackslash{}") >= 2 and (
            r"\textbackslash{}\_" in value or r"\textbackslash{}\{\}" in value
        ):
            return True
        return False

    def file_like_caption(body: str) -> bool:
        if raw_file_like_caption(body):
            return True
        decoded = decode_escaped_latex_text(body)
        if re.search(r"\.(?:indd|pdf|jpe?g|png|eps|ai|svg)\b", decoded, re.I):
            return True
        if re.search(r"\b(?:ptg|rex|sb\d+|u\d{2})\b", decoded, re.I) and decoded.count("_") >= 2:
            return True
        if ("\\" in decoded or "/" in decoded) and decoded.count("_") >= 2:
            return True
        return False

    def repair_body(body: str) -> str:
        if r"\textbackslash{}" not in body:
            return body
        if file_like_caption(body):
            return "Image"
        return setminus_re.sub(
            lambda match: rf"\ensuremath{{{match.group('left')} \setminus {match.group('right')}}}",
            body,
        ).replace(r"\textbackslash{}", r"\ensuremath{\setminus}")

    def repair_line(line: str) -> str:
        stripped = line.strip()
        if stripped.startswith(r"\caption{") and r"\textbackslash{}" in stripped and raw_file_like_caption(stripped):
            indent = line[: len(line) - len(line.lstrip())]
            return indent + r"\caption{Image}"
        pieces: list[str] = []
        cursor = 0
        while True:
            match = caption_re.search(line, cursor)
            if not match:
                pieces.append(line[cursor:])
                break
            group = balanced_brace_group_span(line, match.end())
            if not group:
                pieces.append(line[cursor : match.end()])
                cursor = match.end()
                continue
            body_start, body_end, end = group
            pieces.append(line[cursor:body_start])
            pieces.append(repair_body(line[body_start:body_end]))
            pieces.append("}")
            cursor = end
        return "".join(pieces)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def remove_par_tokens_inside_ensuremath_final(text: str) -> str:
    """Drop paragraph tokens that leaked into inline math wrappers."""
    pattern = re.compile(r"\\ensuremath\{(?P<body>[^\n]*?)\\par\}")

    def repl(match: re.Match[str]) -> str:
        return r"\ensuremath{" + match.group("body").rstrip() + "}"

    return pattern.sub(repl, text)


def repair_broken_caption_commands_final(text: str) -> str:
    """Recover structural captions after overly broad math-command protection."""
    text = text.replace(r"\ensuremath{\cap} tion{", r"\caption{")
    text = text.replace(r"\ensuremath{\mathrm{cap}} tion{", r"\caption{")
    return text


def repair_bare_xarrow_commands_final(text: str) -> str:
    """Protect xleftarrow/xrightarrow commands that OCR left in text mode."""
    xarrow_re = re.compile(r"\\x(?:left|right)arrow(?:\[[^{}\n]*\])?\{[^{}\n]*\}")

    def repair_line(line: str) -> str:
        masked, placeholders = mask_balanced_ensuremath_spans(line)
        masked = xarrow_re.sub(lambda match: rf"\ensuremath{{{match.group(0)}}}", masked)
        return restore_balanced_ensuremath_spans(masked, placeholders)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def repair_split_integral_ensuremath_final(text: str) -> str:
    """Recover OCR splits such as \ensuremath{\in t}_... into a safe integral symbol."""
    text = re.sub(r"\\ensuremath\{\\in\s+t\}", r"\\ensuremath{\\int}", text)
    text = re.sub(r"\\in\s+t(?=\s*(?:[_^]|\\limits))", r"\\int", text)
    return text


def repair_joined_function_variable_commands_final(text: str) -> str:
    """Split OCR joins such as \secx, \tanx, or \betaix into known symbols plus variables."""
    fn_re = re.compile(r"\\(?P<fn>sin|cos|tan|sec|csc|cot|ln|log)(?P<var>[a-z])(?=\b|[+\-*/^_)}\]])")
    text = fn_re.sub(lambda match: rf"\{match.group('fn')} {match.group('var')}", text)
    greek = (
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "varepsilon",
        "zeta",
        "eta",
        "theta",
        "vartheta",
        "iota",
        "kappa",
        "lambda",
        "mu",
        "nu",
        "xi",
        "rho",
        "varrho",
        "sigma",
        "tau",
        "upsilon",
        "phi",
        "varphi",
        "chi",
        "psi",
        "omega",
    )
    greek_re = re.compile(
        r"\\(?P<cmd>" + "|".join(sorted(greek, key=len, reverse=True)) + r")(?P<var>[a-z]{1,3})(?=\b|[+\-*/^_)}\]])"
    )
    return greek_re.sub(lambda match: rf"\{match.group('cmd')} {match.group('var')}", text)


def repair_underbrace_fraction_without_group_final(text: str) -> str:
    r"""Wrap common OCR pattern \underbrace \frac{...}{...}_{...} into valid LaTeX."""
    simple = r"(?:[^{}\n]|\{[^{}\n]*\})+"
    pattern = re.compile(
        rf"\\underbrace\s+\\frac\{{(?P<num>{simple})\}}\{{(?P<den>{simple})\}}(?P<script>[_^]\{{(?:[^{{}}\n]|\{{[^{{}}\n]*\}})*\}})"
    )
    return pattern.sub(
        lambda match: rf"\underbrace{{\frac{{{match.group('num')}}}{{{match.group('den')}}}}}{match.group('script')}",
        text,
    )


def quarantine_bad_ensuremath_lines_final(text: str) -> str:
    """Plainify unrecoverable math lines that swallowed prose, lists, or broken fraction chains."""
    out: list[str] = []

    def quarantine(line: str) -> list[str]:
        indent = line[: len(line) - len(line.lstrip())]
        plain = plainify_latex_evidence_text(line)
        chunks = chunk_plain_evidence(plain, 120)
        block = [indent + r"\par\begingroup\small\ttfamily\raggedright"]
        block.extend(indent + latex_escape_text(chunk) + r"\par" for chunk in chunks)
        block.append(indent + r"\endgroup")
        return block

    for line in text.splitlines():
        stripped = line.strip()
        has_math = r"\ensuremath{" in stripped
        swallowed_structure = has_math and (
            r"\begin{enumerate}" in stripped
            or r"\end{enumerate}" in stripped
            or r"\begin{itemize}" in stripped
            or r"\end{itemize}" in stripped
        )
        bad_fraction_chain = has_math and r"\frac{{" in stripped and stripped.count(r"\frac") >= 2
        bad_integral_script = has_math and (
            r"\ensuremath{\int}_" in stripped
            or r"\ensuremath{\int}^" in stripped
            or r"\ensuremath{\in} \ensuremath{t_" in stripped
        )
        malformed_parenthesized_invocation = r"\ensuremath(" in stripped or r"\ensuremath)" in stripped
        orphan_alignment_tail = has_math and stripped.startswith("&") and r"\end{" in stripped
        bad_integral_boundary = has_math and (
            r"]_}\ensuremath" in stripped
            or r"\ensuremath{\in}\ensuremath{\ensuremath{t_" in stripped
            or r"\right\rfloo}" in stripped
        )
        bad_limit_dump = stripped.startswith(r"\ensuremath{") and r"\lim\_" in stripped and (
            r"\text{" in stripped or r"\%" in stripped or r"\{}\{}" in stripped
        )
        short_prose_array_collision = (
            stripped.startswith(r"\ensuremath{")
            and r"\begin{array}" in stripped
            and re.match(r"\\ensuremath\{[A-Z][A-Za-z ,]+\\", stripped)
        )
        prose_heavy_math = (
            stripped.startswith(r"\ensuremath{")
            and len(re.findall(r"[A-Za-z]{4,}", stripped)) >= 12
            and any(token in stripped for token in (r"\frac", r"\boxed", r"\begin{array}", r"\end{array}"))
        )
        if (
            swallowed_structure
            or bad_fraction_chain
            or bad_integral_script
            or malformed_parenthesized_invocation
            or orphan_alignment_tail
            or bad_integral_boundary
            or bad_limit_dump
            or short_prose_array_collision
            or prose_heavy_math
        ):
            out.extend(quarantine(line))
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def wrap_bare_calculus_commands_in_text_final(text: str) -> str:
    """Protect common text-mode log/integral commands that OCR left outside math."""
    structural_re = re.compile(
        r"^\\(?:begin|end)\{|^\\(?:chapter|section|subsection|subsubsection|caption|includegraphics)\b"
    )
    log_re = re.compile(r"\\log(?P<script>_\{[^{}\n]+\}|_[A-Za-z0-9]+)")
    ln_abs_re = re.compile(r"\\ln(?P<body>\s*\|[^|\n]+\|\s*(?:[+\-]\s*[A-Za-z])?)")
    ln_re = re.compile(r"\\ln\b")
    integral_re = re.compile(r"\\int(?P<body>.*?)(?P<var>d[A-Za-z])(?=(?:\s|[;,.=)]|$))")
    integral_placeholder_diff_re = re.compile(
        r"\\int(?P<body>.*?)(?P<diff>@@ENSUREMATH\d+@@\s*[A-Za-z])(?=(?:\s|[;,.=)]|$))"
    )
    integral_tail_re = re.compile(r"\\int(?P<body>[^\n]*)$")
    bare_integral_symbol_re = re.compile(r"\\int\b(?=\s*(?:[.,;:)]|$))")
    relation_re = re.compile(
        r"\\(?P<cmd>implies|therefore|because|Rightarrow|Leftarrow|Leftrightarrow|"
        r"Longrightarrow|Longleftarrow|Longleftrightarrow|rightleftharpoons|leftrightharpoons|"
        r"rightleftarrows|leftrightarrows)\b"
    )
    math_alpha_re = re.compile(
        r"\\(?P<cmd>mathscr|mathcal|mathbb|mathrm|mathbf|mathit|mathsf)\s*\{(?P<body>[^{}\n]+)\}"
    )
    out: list[str] = []
    in_ttfamily = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == r"\par\begingroup\small\ttfamily\raggedright":
            in_ttfamily = True
            out.append(line)
            continue
        if in_ttfamily:
            out.append(line)
            if stripped == r"\endgroup":
                in_ttfamily = False
            continue
        if not structural_re.match(stripped) and r"\begin{" not in line:
            masked, placeholders = mask_balanced_ensuremath_spans(line)
            masked = log_re.sub(lambda match: rf"\ensuremath{{\log{match.group('script')}}}", masked)
            masked = relation_re.sub(lambda match: rf"\ensuremath{{\{match.group('cmd')}}}", masked)
            masked = math_alpha_re.sub(
                lambda match: rf"\ensuremath{{\{match.group('cmd')}{{{match.group('body')}}}}}",
                masked,
            )
            ln_placeholders: list[str] = []

            def stash_ln(expr: str) -> str:
                token = f"@@LNFUNC{len(ln_placeholders)}@@"
                ln_placeholders.append(expr)
                return token

            masked = ln_abs_re.sub(
                lambda match: stash_ln(rf"\ensuremath{{\ln{match.group('body')}}}"),
                masked,
            )
            masked = ln_re.sub(lambda _match: stash_ln(r"\ensuremath{\ln}"), masked)
            integral_placeholders: list[str] = []

            def stash_integral(expr: str) -> str:
                token = f"@@INTEGRAL{len(integral_placeholders)}@@"
                integral_placeholders.append(expr)
                return token

            def normalize_integral_body(body: str) -> str:
                body = body.replace(r"\ ", " ")
                return re.sub(r"\s+", " ", body.strip())

            def integral_repl(match: re.Match[str]) -> str:
                body = normalize_integral_body(match.group("body"))
                var = match.group("var").strip()
                spacer = " " if body else ""
                return stash_integral(rf"\ensuremath{{\int{spacer}{body}\,{var}}}")

            def integral_placeholder_diff_repl(match: re.Match[str]) -> str:
                body = normalize_integral_body(match.group("body"))
                diff = re.sub(r"\s+", " ", match.group("diff").strip())
                spacer = " " if body else ""
                return stash_integral(rf"\ensuremath{{\int{spacer}{body}{diff}}}")

            def integral_tail_repl(match: re.Match[str]) -> str:
                body = normalize_integral_body(match.group("body"))
                if not re.search(r"(?:[_^]|@@ENSUREMATH\d+@@|\\(?:frac|sqrt|sin|cos|tan|mathrm)\b|[=+\-*/])", body):
                    return match.group(0)
                spacer = " " if body else ""
                return stash_integral(rf"\ensuremath{{\int{spacer}{body}}}")

            masked = integral_placeholder_diff_re.sub(integral_placeholder_diff_repl, masked)
            masked = integral_re.sub(integral_repl, masked)
            masked = integral_tail_re.sub(integral_tail_repl, masked)
            masked = bare_integral_symbol_re.sub(r"\\ensuremath{\\int}", masked)
            for index, expr in enumerate(integral_placeholders):
                masked = masked.replace(f"@@INTEGRAL{index}@@", expr)
            for index, expr in enumerate(ln_placeholders):
                masked = masked.replace(f"@@LNFUNC{index}@@", expr)
            line = restore_balanced_ensuremath_spans(masked, placeholders)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def wrap_bracketed_math_fragment_scripts_final(text: str) -> str:
    """Protect bracketed math fragments with trailing text-mode scripts."""
    structural_re = re.compile(
        r"^\\(?:begin|end)\{|^\\(?:chapter|section|subsection|subsubsection|caption|includegraphics|item)\b"
    )
    bracket_script_re = re.compile(
        r"\[(?P<body>[^\[\]\n]*@@ENSUREMATH\d+@@[^\[\]\n]*)\]_(?P<script>@@ENSUREMATH\d+@@|\{[^{}\n]*\}|[A-Za-z0-9]+)"
    )
    out: list[str] = []
    in_ttfamily = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == r"\par\begingroup\small\ttfamily\raggedright":
            in_ttfamily = True
            out.append(line)
            continue
        if in_ttfamily:
            out.append(line)
            if stripped == r"\endgroup":
                in_ttfamily = False
            continue
        if not structural_re.match(stripped) and r"\begin{" not in line and "]_" in line:
            masked, placeholders = mask_balanced_ensuremath_spans(line)

            def repl(match: re.Match[str]) -> str:
                body = match.group("body")
                script = match.group("script")
                script_arg = script if script.startswith("{") else "{" + script + "}"
                return rf"\ensuremath{{[{body}]_{script_arg}}}"

            masked = bracket_script_re.sub(repl, masked)
            line = restore_balanced_ensuremath_spans(masked, placeholders)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def wrap_arrow_annotation_lines_final(text: str) -> str:
    """Wrap standalone annotated arrow equations that OCR left in text mode."""
    structural_re = re.compile(
        r"^\\(?:begin|end)\{|^\\(?:chapter|section|subsection|subsubsection|caption|includegraphics|item)\b"
    )
    out: list[str] = []
    in_ttfamily = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == r"\par\begingroup\small\ttfamily\raggedright":
            in_ttfamily = True
            out.append(line)
            continue
        if in_ttfamily:
            out.append(line)
            if stripped == r"\endgroup":
                in_ttfamily = False
            continue
        if (
            stripped
            and not structural_re.match(stripped)
            and re.search(r"\\x(?:left|right)arrow\b", stripped)
        ):
            indent = line[: len(line) - len(line.lstrip())]
            body = unwrap_ensuremath_balanced_line(stripped)
            line = indent + rf"\ensuremath{{{body}}}"
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def quarantine_unrecoverable_math_lines(text: str) -> str:
    """Render badly corrupted OCR math lines as escaped text instead of invalid LaTeX."""
    structural_re = re.compile(
        r"^\\(?:begin|end)\{(?:figure|itemize|enumerate|tabularx?|longtable|center)\}|"
        r"^\\(?:includegraphics|caption|centering|chapter|section|subsection|subsubsection|exerciseheading)\b|"
        r"^\\item\b"
    )

    def chunk_plain(value: str, width: int = 620) -> list[str]:
        chunks: list[str] = []
        value = re.sub(r"\s+", " ", value.strip())
        while len(value) > width:
            cut = value.rfind(" ", 0, width)
            if cut < width // 2:
                cut = width
            chunks.append(value[:cut].strip())
            value = value[cut:].strip()
        if value:
            chunks.append(value)
        return chunks or [""]

    def escaped_text_block(line: str) -> list[str]:
        plain = plainify_latex_evidence_text(line) if "plainify_latex_evidence_text" in globals() else ""
        chunks = chunk_plain(plain or line)
        out = [r"\par\begingroup\small\ttfamily\raggedright"]
        out.extend(latex_escape_text(chunk) + r"\par" for chunk in chunks)
        out.append(r"\endgroup")
        return out

    def frac_argument_crosses_rowbreak(value: str) -> bool:
        search = 0
        while True:
            found = value.find(r"\frac", search)
            if found == -1:
                return False
            if found + len(r"\frac") < len(value) and value[found + len(r"\frac")].isalpha():
                search = found + len(r"\frac")
                continue
            numerator = balanced_brace_group_span(value, found + len(r"\frac"))
            if not numerator:
                search = found + len(r"\frac")
                continue
            pos = numerator[2]
            while pos < len(value) and value[pos].isspace():
                pos += 1
            if pos >= len(value) or value[pos] != "{":
                search = numerator[2]
                continue
            depth = 1
            i = pos + 1
            while i < len(value):
                if value.startswith(r"\\", i) and depth == 1:
                    return True
                if value.startswith(r"\end{array}", i) and depth == 1:
                    return True
                char = value[i]
                if char == "\\":
                    i += 2
                    continue
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            search = max(i + 1, numerator[2])

    def unrecoverable_score(stripped: str) -> int:
        has_math_env = bool(re.search(r"\\begin\{(?:array|(?:p|b|v|V)?matrix|cases)\}", stripped))
        if not (r"\ensuremath" in stripped or has_math_env or r"\frac" in stripped):
            return 0
        surplus, depth = brace_surplus_and_depth(stripped)
        prose_words = re.findall(r"[A-Za-z]{4,}", re.sub(r"\\[A-Za-z]+", " ", stripped))
        score = 0
        def has_broken_accent_text_window(value: str) -> bool:
            for match in re.finditer(r"\\(?:hat|widehat|underline|overline)\s*\{\\text\s*\{", value):
                window = value[match.end() : match.end() + 100]
                if re.search(r"\}\s*(?:\)|\\times|=)", window):
                    return True
            return False

        if stripped.startswith(r"\ensuremath{") and len(prose_words) >= 8 and (surplus > 0 or depth > 0):
            score += 4
        if stripped.startswith(r"\ensuremath{") and len(prose_words) >= 8 and has_broken_accent_text_window(stripped):
            score += 4
        if len(stripped) > 1200 and re.search(
            r"\\(?:begin|end)\{(?:figure|itemize|enumerate|array)\}|\\includegraphics|\\caption|"
            r"\b(?:Exercise|WORKED EXAMPLE|CONTINUED|Try It|Example)\b|source_page_idx",
            stripped,
        ):
            score += 4
        if r"\begin{array}" in stripped and (r"\end{array}" not in stripped or r"\end{array" in stripped and r"\end{array}" not in stripped):
            score += 3
        if has_math_env and not re.search(r"\\end\{(?:array|(?:p|b|v|V)?matrix|cases)\}", stripped):
            score += 3
        if r"\begin{array}" in stripped and (
            r"\end{array}]" in stripped
            or (stripped.count("&") >= 4 and stripped.count(r"\ensuremath") >= 3)
        ):
            score += 4
        if has_math_env and stripped.count(r"\text{") >= 8 and stripped.count(r"\\") >= 3:
            score += 4
        if has_math_env and stripped.count(r"\begin{array}") >= 2 and stripped.count(r"\text{") >= 4:
            score += 4
        if has_math_env and r"\xrightarrow" in stripped and r"\begin{array}" in stripped:
            score += 4
        if has_math_env and re.search(r"\\text\s*\{[^\n]{0,220}\\\\", stripped):
            score += 4
        if (
            has_math_env
            and stripped.startswith(r"\ensuremath{\begin{array}")
            and re.search(r"^\\ensuremath\{\\begin\{array\}.*?\}\s+\S[^\n]*\\\\", stripped)
        ):
            score += 4
        if (
            has_math_env
            and stripped.count(r"\ensuremath") >= 7
            and stripped.count(r"\\") >= 2
            and (
                r"\ensuremath{\ensuremath" in stripped
                or re.search(
                    r"\\ensuremath\{\{\}\^(?:\{[^{}\n]*\}|[^{}\\\s]+)\s*(?:\\\\|\\end\{array\})",
                    stripped,
                )
            )
        ):
            score += 4
        if has_math_env and r"\boxed" in stripped and re.search(r"\\boxed\s*\{[^\n]{0,260}\\\\", stripped):
            score += 4
        if re.search(r"\\frac\{\}\s*\\sqrt", stripped):
            score += 4
        if re.search(r"\\ensuremath(?!\s*\{)", stripped):
            score += 1
        if re.search(r"\\(?:sqrt|frac)\s*\}", stripped):
            score += 2
        if re.search(r"\\ensuremath\}\s*\{", stripped):
            score += 2
        if r"\begin{array}" in stripped and stripped.count(r"\ensuremath") >= 2 and (surplus > 0 or depth > 1):
            score += 2
        if len(stripped) > 700 and (surplus > 2 or depth > 3):
            score += 2
        if r"\ensuremath{{}^\ensuremath" in stripped:
            score += 3
        if (
            len(stripped) > 180
            and stripped.count(r"\ensuremath") >= 2
            and re.search(r"[\u3400-\u9fff\u0370-\u03ff■¥注网叫旧目]", stripped)
        ):
            score += 3
        if (
            stripped.startswith(r"\ensuremath{")
            and re.search(r"[\u3400-\u9fff§£°【】丛曾龄琳甲勺]", stripped)
            and (stripped.count(r"\ensuremath") >= 2 or stripped.count(r"\wedge") >= 2)
        ):
            score += 4
        if stripped.startswith(r"\ensuremath{") and re.search(r"\^\{[^{}\n]+\}\s*\\wedge", stripped):
            score += 3
        if r"\underbrace" in stripped and (
            r"\text{} +" in stripped
            or re.search(r"[_^]\s*\{?\\ensuremath\{\\text\{\}\s*(?:\+|\\\\)", stripped)
            or stripped.count(r"\ensuremath") >= 4
        ):
            score += 3
        if r"\begin{array}" in stripped and (
            r"\underline{{" in stripped
            or r"\overline{{" in stripped
            or frac_argument_crosses_rowbreak(stripped)
            or re.search(r"\}\s*\{[^{}\n]*\\wedge[^{}\n]*\\\\", stripped)
            or re.search(r"\}\s*\{[^\n]{0,200}\\wedge[^\n]{0,200}\\\\", stripped)
            or re.search(r"\\(?:frac|underline|overline)\s*\{[^{}\n]*(?:\\\\|\\end\{array\})", stripped)
            or re.search(r"\\frac\s*\{(?:[^{}]|\{[^{}]*\})*\}\s*\{[^{}\n]*(?:\\\\|\\end\{array\})", stripped)
        ):
            score += 3
        if re.search(r"^\\ensuremath\{[^{}\n]*[\u3400-\u9fff\u0370-\u03ff][^{}\n]*[A-Za-z][^{}\n]*\}$", stripped):
            score += 3
        if re.search(r"\\[A-Za-z]+[\u3400-\u9fff\u0370-\u03ff]", stripped):
            score += 2
        if stripped.count(r"\ensuremath") >= 5 and re.search(r"\}\s*[A-Za-z0-9]\s*\\ensuremath\{", stripped):
            score += 2
        return score

    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        indent = line[: len(line) - len(line.lstrip())]
        if stripped.startswith(r"\item"):
            body = stripped[len(r"\item") :].strip()
            if unrecoverable_score(body) >= 3:
                out.append(indent + r"\item")
                out.extend(indent + block_line for block_line in escaped_text_block(body))
                continue
            out.append(line)
            continue
        if not stripped or stripped.startswith("%") or structural_re.match(stripped):
            out.append(line)
            continue
        if unrecoverable_score(stripped) >= 3:
            out.extend(indent + block_line for block_line in escaped_text_block(stripped))
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_multiline_aligned_blocks(text: str) -> str:
    """Repair OCR-split aligned environments whose ensuremath wrapper closes too early."""
    out: list[str] = []
    in_repaired_aligned = False
    opener_re = re.compile(r"^\\ensuremath\{(?P<label>.*?)\\begin\{aligned\}\}\s*$")
    for line in text.splitlines():
        stripped = line.strip()
        opener = opener_re.match(stripped)
        if opener:
            indent = line[: len(line) - len(line.lstrip())]
            label = opener.group("label").strip()
            if label:
                out.append(indent + label)
            out.append(indent + r"\ensuremath{\begin{aligned}")
            in_repaired_aligned = True
            continue
        if in_repaired_aligned and stripped == r"\end{aligned}":
            indent = line[: len(line) - len(line.lstrip())]
            out.append(indent + r"\end{aligned}}")
            in_repaired_aligned = False
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def unwrap_ensuremath_in_ttfamily_blocks(text: str) -> str:
    """Keep quarantined escaped-text lines out of math mode on repeated safety passes."""
    out: list[str] = []
    in_block = False
    marker = r"\ensuremath{"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == r"\par\begingroup\small\ttfamily\raggedright":
            in_block = True
            out.append(line)
            continue
        if in_block and stripped == r"\endgroup":
            in_block = False
            out.append(line)
            continue
        if in_block and stripped.startswith(marker) and stripped.endswith("}"):
            indent = line[: len(line) - len(line.lstrip())]
            body = stripped[len(marker) : -1]
            line = indent + body
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_unclosed_script_ensuremath_before_array_end(text: str) -> str:
    """Close OCR-truncated script wrappers before an array row/end marker."""
    pattern = re.compile(
        r"\\ensuremath\{\{\}\^(?P<script>\{[^{}\n]*\}|[^{}\\\s]+)\s*(?P<tail>\\\\|\\end\{array\})"
    )

    def repl(match: re.Match[str]) -> str:
        return r"\ensuremath{{}^" + match.group("script") + r"} " + match.group("tail")

    return pattern.sub(repl, text)


def close_unbalanced_math_before_ttfamily_blocks(text: str) -> str:
    """Keep quarantined evidence blocks outside any still-open math wrapper."""
    marker = r"\par\begingroup\small\ttfamily\raggedright"
    out: list[str] = []
    for line in text.splitlines():
        if marker not in line:
            out.append(line)
            continue

        pieces: list[str] = []
        rest = line
        while marker in rest:
            before, rest = rest.split(marker, 1)
            if r"\ensuremath{" in before:
                _surplus, depth = brace_surplus_and_depth(before)
                if 0 < depth <= 4:
                    before += "}" * depth
            pieces.append(before)
            pieces.append(marker)
        pieces.append(rest)
        out.append("".join(pieces))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def decode_escaped_latex_text(value: str) -> str:
    """Decode LaTeX-escaped command text back to source commands."""
    replacements = [
        (r"\textbackslash{}", "\\"),
        (r"\textbackslash()", "\\"),
        (r"\textasciicircum{}", "^"),
        (r"\textasciicircum()", "^"),
        (r"\textasciitilde{}", r"\sim"),
        (r"\textasciitilde()", r"\sim"),
        (r"\{", "{"),
        (r"\}", "}"),
        (r"\_", "_"),
        (r"\&", "&"),
        (r"\%", "%"),
        (r"\$", "$"),
        (r"\#", "#"),
    ]
    for old, new in replacements:
        value = value.replace(old, new)
    value = re.sub(r"\(\)\s*textbackslash\s*\(\)", lambda _match: "\\", value, flags=re.I)
    value = re.sub(r"(?<![A-Za-z\\])textbackslash\s*\(\)", lambda _match: "\\", value, flags=re.I)
    value = re.sub(r"(?<![A-Za-z\\])textbackslash(?![A-Za-z])", lambda _match: "\\", value, flags=re.I)
    value = re.sub(r"\(\)\s*textasciicircum\s*\(\)", "^", value, flags=re.I)
    value = re.sub(r"(?<![A-Za-z\\])textasciicircum\s*\(\)", "^", value, flags=re.I)
    value = re.sub(r"\(\)\s*textasciitilde\s*\(\)", lambda _match: r"\sim", value, flags=re.I)
    value = re.sub(r"(?<![A-Za-z\\])textasciitilde\s*\(\)", lambda _match: r"\sim", value, flags=re.I)
    return value


def text_command_group_contains_rowbreak(value: str) -> bool:
    """Detect row breaks or environments swallowed inside a \text{...} group."""
    marker = r"\text"
    cursor = 0
    while True:
        idx = value.find(marker, cursor)
        if idx == -1:
            return False
        cmd_end = idx + len(marker)
        group = balanced_brace_group_span(value, cmd_end)
        if not group:
            cursor = cmd_end
            continue
        body_start, body_end, end = group
        body = value[body_start:body_end]
        if r"\\" in body or r"\begin{" in body or r"\end{" in body:
            return True
        cursor = end


def recoverable_decoded_latex_math(value: str) -> str | None:
    """Return decoded math if an escaped source dump is balanced enough to render."""
    candidate = decode_escaped_latex_text(value.strip())
    candidate = re.sub(r"\s+", " ", candidate).strip()
    if not candidate:
        return None
    if not (
        candidate.startswith(r"\ensuremath{")
        or candidate.startswith(r"\begin{array}")
        or candidate.startswith(r"\begin{aligned}")
        or candidate.startswith(r"\frac")
    ):
        return None
    if candidate.count(r"\begin{array}") != candidate.count(r"\end{array}"):
        return None
    if candidate.count(r"\begin{aligned}") != candidate.count(r"\end{aligned}"):
        return None
    if text_command_group_contains_rowbreak(candidate):
        return None
    adjacent_groups = candidate.count("}{")
    explained_groups = (
        candidate.count(r"\frac")
        + candidate.count(r"\dfrac")
        + candidate.count(r"\tfrac")
        + candidate.count(r"\cfrac")
        + candidate.count(r"\binom")
        + candidate.count(r"\begin{array}")
        + candidate.count(r"\begin{aligned}")
    )
    if adjacent_groups > explained_groups:
        return None
    surplus, depth = brace_surplus_and_depth(candidate)
    if surplus or depth:
        return None
    if candidate.startswith((r"\begin{array}", r"\begin{aligned}", r"\frac")):
        candidate = rf"\ensuremath{{{candidate}}}"
    return candidate


def recover_printed_latex_ttfamily_blocks(text: str) -> str:
    """Render escaped LaTeX math evidence blocks when they are syntactically balanced."""
    block_re = re.compile(
        r"(?P<open>\\par\\begingroup\\small\\ttfamily\\raggedright)(?P<body>.*?)(?P<close>\\endgroup)",
        re.S,
    )

    def clean_body_line(line: str) -> str:
        stripped = line.strip()
        if stripped.endswith(r"\par"):
            stripped = stripped[: -len(r"\par")].rstrip()
        return stripped

    def repl(match: re.Match[str]) -> str:
        recovered: list[str] = []
        saw_content = False
        for raw_line in match.group("body").splitlines():
            stripped = clean_body_line(raw_line)
            if not stripped:
                continue
            saw_content = True
            decoded = recoverable_decoded_latex_math(stripped)
            if decoded is None:
                return match.group(0)
            recovered.append(decoded)
        if not saw_content or not recovered:
            return match.group(0)
        return "\n".join(recovered)

    return block_re.sub(repl, text)


def recover_printed_latex_table_cells(text: str) -> str:
    """Render escaped LaTeX math inside ttfamily table evidence cells when safe."""
    out: list[str] = []
    token = r"{\small\ttfamily "
    end_re = re.compile(r"\}\s*\\\\\s*\\hline\s*$")
    for line in text.splitlines():
        if token not in line or r"\textbackslash{}" not in line:
            out.append(line)
            continue
        before, after = line.split(token, 1)
        end_match = end_re.search(after)
        if not end_match:
            out.append(line)
            continue
        body = after[: end_match.start()]
        decoded = recoverable_decoded_latex_math(body)
        if decoded is None:
            out.append(line)
            continue
        out.append(before + r"{\small " + decoded + after[end_match.start() :])
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def plainify_latex_evidence_text(value: str) -> str:
    """Convert unrecoverable escaped LaTeX evidence into readable plain text."""
    value = decode_escaped_latex_text(value)
    value = re.sub(r"\\begin\{array\}\{[^{}\n]*\}", " ", value)
    value = re.sub(r"\\end\{array\}", " ", value)
    value = re.sub(r"\\begin\{aligned\}|\{?\\end\{aligned\}\}?", " ", value)
    value = re.sub(r"\\ensuremath\{([^{}\n]*)\}", r"\1", value)
    for _ in range(6):
        previous = value
        value = re.sub(r"\\(?:dfrac|tfrac|cfrac|frac)\{([^{}\n]*)\}\{([^{}\n]*)\}", r"(\1)/(\2)", value)
        value = re.sub(r"\\sqrt\{([^{}\n]*)\}", r"sqrt(\1)", value)
        value = re.sub(r"\\boxed\s*\{([^{}\n]*)\}", r"[\1]", value)
        value = re.sub(r"\\underbrace\s*\{([^{}\n]*)\}", r"\1", value)
        value = re.sub(r"\\(?:text|mathrm|mathbf|mathit|mathsf|operatorname)\{([^{}\n]*)\}", r"\1", value)
        value = re.sub(r"\\ensuremath\{([^{}\n]*)\}", r"\1", value)
        if value == previous:
            break
    replacements = {
        r"\\": " ; ",
        r"\quad": " ",
        r"\qquad": " ",
        r"\,": " ",
        r"\;": " ",
        r"\:": " ",
        r"\wedge": "^",
        r"\times": "*",
        r"\div": "/",
        r"\cdot": "*",
        r"\pm": "+/-",
        r"\mp": "-/+",
        r"\sin": "sin ",
        r"\cos": "cos ",
        r"\tan": "tan ",
        r"\log": "log ",
        r"\ln": "ln ",
        r"\leqslant": "<=",
        r"\geqslant": ">=",
        r"\leq": "<=",
        r"\geq": ">=",
        r"\neq": "!=",
        r"\rightarrow": "->",
        r"\leftarrow": "<-",
        r"\mapsto": "->",
        r"\ldots": "...",
        r"\infty": "infinity",
        r"\pi": "pi",
        r"\circ": "deg",
        r"\square": "[ ]",
        r"\boxed": "[ ]",
        r"\mathrm": "",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    value = re.sub(r"\\[A-Za-z]+", " ", value)
    value = value.replace("\\", " ")
    value = re.sub(r"[{}]", " ", value)
    value = re.sub(r"\s*;\s*", " ; ", value)
    value = re.sub(r"\s+", " ", value).strip(" ;")
    return value.strip()


def chunk_plain_evidence(value: str, limit: int = 150) -> list[str]:
    """Split long plain evidence text into readable LaTeX-safe lines."""
    words = value.split()
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        extra = len(word) + (1 if current else 0)
        if current and length + extra > limit:
            chunks.append(" ".join(current))
            current = [word]
            length = len(word)
        else:
            current.append(word)
            length += extra
    if current:
        chunks.append(" ".join(current))
    return chunks or [value]


def plainify_printed_latex_ttfamily_blocks(text: str) -> str:
    """Replace unrecoverable escaped LaTeX dumps with non-source plain evidence."""
    block_re = re.compile(
        r"(?P<open>\\par\\begingroup\\small\\ttfamily\\raggedright)(?P<body>.*?)(?P<close>\\endgroup)",
        re.S,
    )

    def clean_body_line(line: str) -> str:
        stripped = line.strip()
        if stripped.endswith(r"\par"):
            stripped = stripped[: -len(r"\par")].rstrip()
        return stripped

    def repl(match: re.Match[str]) -> str:
        raw_lines = [clean_body_line(line) for line in match.group("body").splitlines()]
        raw_lines = [line for line in raw_lines if line]
        if not raw_lines or not any(r"\textbackslash{}" in line for line in raw_lines):
            return match.group(0)
        plain = plainify_latex_evidence_text(" ".join(raw_lines))
        if not plain:
            return match.group(0)
        out = [r"\par\begingroup\small\ttfamily\raggedright"]
        out.extend(latex_escape_text(chunk) + r"\par" for chunk in chunk_plain_evidence(plain))
        out.append(r"\endgroup")
        return "\n".join(out)

    return block_re.sub(repl, text)


def plainify_printed_latex_table_cells(text: str) -> str:
    """Replace unrecoverable escaped LaTeX table evidence with plain text cells."""
    out: list[str] = []
    token = r"{\small\ttfamily "
    end_re = re.compile(r"\}\s*\\\\\s*\\hline\s*$")
    for line in text.splitlines():
        if token not in line or r"\textbackslash{}" not in line:
            out.append(line)
            continue
        before, after = line.split(token, 1)
        end_match = end_re.search(after)
        if not end_match:
            out.append(line)
            continue
        body = after[: end_match.start()]
        plain = plainify_latex_evidence_text(body)
        if not plain:
            out.append(line)
            continue
        out.append(before + r"{\small\ttfamily " + latex_escape_text(plain) + after[end_match.start() :])
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def plainify_mixed_escaped_latex_lines_final(text: str) -> str:
    """Plainify lines where escaped command text still contains executable LaTeX."""
    structural_re = re.compile(
        r"^\\(?:begin|end)\{|^\\(?:chapter|section|subsection|subsubsection|caption|includegraphics)\b"
    )
    executable_command_re = re.compile(
        r"\\(?:ensuremath|frac|sqrt|wedge|vee|times|div|cdot|mathrm|mathbf|mathit|mathsf|text)\b"
    )

    def evidence_block(value: str) -> list[str]:
        plain = plainify_latex_evidence_text(value)
        if not plain:
            plain = value.replace(r"\textbackslash{}", " ")
        out = [r"\par\begingroup\small\ttfamily\raggedright"]
        out.extend(latex_escape_text(chunk) + r"\par" for chunk in chunk_plain_evidence(plain, limit=620))
        out.append(r"\endgroup")
        return out

    out: list[str] = []
    in_ttfamily = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == r"\par\begingroup\small\ttfamily\raggedright":
            in_ttfamily = True
            out.append(line)
            continue
        if in_ttfamily:
            out.append(line)
            if stripped == r"\endgroup":
                in_ttfamily = False
            continue
        if (
            (r"\textbackslash{}" in line or r"\ensuremath{\setminus}" in line)
            and executable_command_re.search(line)
            and not line.lstrip().startswith("%")
            and not structural_re.match(stripped)
        ):
            indent = line[: len(line) - len(line.lstrip())]
            out.extend(indent + block_line for block_line in evidence_block(stripped))
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def plainify_visible_latex_escape_lines_final(text: str) -> str:
    """Plainify visible escaped-LaTeX dumps before they reach the final PDF."""
    structural_re = re.compile(
        r"^\\(?:begin|end)\{|^\\(?:chapter|section|subsection|subsubsection|caption|includegraphics)\b"
    )
    protected_re = re.compile(
        r"^(?:\{\\(?:small|scriptsize|footnotesize)|\}|\\setlength|\\renewcommand|\\par\\begingroup\\small\\ttfamily\\raggedright|\\endgroup)(?:\s|$)"
    )
    dump_word_re = re.compile(r"\b(?:ensuremath|textbackslash|begin\s*(?:tabular|array|matrix)|end\s*(?:tabular|array|matrix))\b", re.I)
    plain_ensuremath_re = re.compile(r"(?<!\\)\bensuremath\b", re.I)
    cleanup_words_re = re.compile(
        r"\b(?:ensuremath|textbackslash|textwidth|arraybackslash|raggedright|begingroup|endgroup|"
        r"ttfamily|small|scriptsize|footnotesize|setlength|renewcommand|arraystretch|tabcolsep|"
        r"tabularx|tabular|longtable|hline|begin|end|par)\b",
        re.I,
    )

    def normalize_visible_backslashes(value: str) -> str:
        for _ in range(4):
            previous = value
            value = value.replace(r"\textbackslash{}textbackslash()", "\\")
            value = value.replace(r"\textbackslash{}textbackslash{}", r"\\")
            value = value.replace(r"\textbackslash()\textbackslash()", r"\\")
            value = value.replace(r"\textbackslash()", "\\")
            value = value.replace(r"\textbackslash{}", "\\")
            if value == previous:
                break
        return value

    def plainify_line(line: str, force_par: bool) -> str:
        indent = line[: len(line) - len(line.lstrip())]
        stripped = line.strip()
        had_par = stripped.endswith(r"\par")
        core = stripped[: -len(r"\par")].rstrip() if had_par else stripped
        normalized = normalize_visible_backslashes(core)
        plain = plainify_latex_evidence_text(normalized)
        plain = re.sub(
            r"\b(?:raggedright|begingroup|endgroup|ttfamily|small|scriptsize|footnotesize|setlength|renewcommand)(?=[A-Z])",
            " ",
            plain,
        )
        plain = cleanup_words_re.sub(" ", plain)
        plain = re.sub(r"(?:\(\s*\)\s*)+", " ", plain)
        plain = re.sub(r"\s*([(),;:])\s*", r" \1 ", plain)
        plain = re.sub(r"\s+", " ", plain).strip(" ;,")
        if not plain:
            plain = "source table evidence"
        suffix = r"\par" if had_par or force_par else ""
        return indent + latex_escape_text(plain) + suffix

    out: list[str] = []
    in_ttfamily = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == r"\par\begingroup\small\ttfamily\raggedright":
            in_ttfamily = True
            out.append(line)
            continue
        if stripped == r"\endgroup":
            in_ttfamily = False
            out.append(line)
            continue
        if structural_re.match(stripped) or protected_re.match(stripped):
            out.append(line)
            continue
        lowered = stripped.lower()
        word_escape_dump = bool(re.search(r"(?<!\\)\btextbackslash\b", stripped, re.I))
        visible_escape = r"\textbackslash" in stripped or word_escape_dump
        plain_decoded_dump = "\\" not in stripped and bool(dump_word_re.search(stripped))
        decoded_dump = bool(plain_ensuremath_re.search(stripped)) or (
            (plain_decoded_dump or word_escape_dump)
            and (
                "frac" in lowered
                or "fraction" in lowered
                or "wedge" in lowered
                or "sqrt" in lowered
                or "pmatrix" in lowered
                or "array" in lowered
                or "tabular" in lowered
                or "textbackslash" in lowered
            )
        )
        if visible_escape or decoded_dump:
            out.append(plainify_line(line, force_par=in_ttfamily))
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_matrix_single_backslash_digit_rows_final(text: str) -> str:
    """Repair matrix row breaks collapsed to a single backslash before a digit."""
    out: list[str] = []
    for line in text.splitlines():
        if r"\begin{pmatrix}" in line or r"\begin{bmatrix}" in line or r"\begin{array}" in line:
            line = re.sub(r"(?<=\d)\\(?=\d)", r"\\\\", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_orphan_arrow_closing_braces_final(text: str) -> str:
    """Protect arrows that were left outside math with a stray closing brace."""
    return re.sub(
        r"(?<=\})\\(?P<arrow>to|rightarrow|leftarrow|Rightarrow|Leftarrow|Leftrightarrow)\}",
        lambda match: rf"\ensuremath{{\{match.group('arrow')}}}",
        text,
    )


def repair_sqrt_ensuremath_argument_final(text: str) -> str:
    """Merge OCR splits like \sqrt\ensuremath{...} into one math span."""
    body = r"(?:[^{}]|\{[^{}]*\})*"
    pattern = re.compile(rf"\\sqrt\s*\\ensuremath\{{(?P<body>{body})\}}")
    return pattern.sub(
        lambda match: rf"\ensuremath{{\sqrt{{{unwrap_ensuremath_balanced_line(match.group('body')).strip()}}}}}",
        text,
    )


def repair_split_square_ensuremath_commands_final(text: str) -> str:
    """Merge OCR splits such as \squar\ensuremath{e_1} into \square_1."""
    body = r"(?:[^{}]|\{[^{}]*\})*"
    pattern = re.compile(rf"\\squar\s*\\ensuremath\{{e(?P<body>{body})\}}")
    return pattern.sub(
        lambda match: rf"\ensuremath{{\square{unwrap_ensuremath_balanced_line(match.group('body')).strip()}}}",
        text,
    )


def repair_split_sum_ensuremath_commands_final(text: str) -> str:
    """Merge OCR splits such as \su\ensuremath{m_{...}} into \sum_{...}."""
    pattern = re.compile(r"\\su\s*\\ensuremath\{")

    def repair_line(line: str) -> str:
        pieces: list[str] = []
        cursor = 0
        while True:
            match = pattern.search(line, cursor)
            if not match:
                pieces.append(line[cursor:])
                break
            body_start = match.end()
            depth = 1
            i = body_start
            while i < len(line):
                if line[i] == "\\" and i + 1 < len(line):
                    i += 2
                    continue
                if line[i] == "{":
                    depth += 1
                elif line[i] == "}":
                    depth -= 1
                    if depth == 0:
                        body_value = unwrap_ensuremath_balanced_line(line[body_start:i]).strip()
                        if body_value.startswith("m"):
                            pieces.append(line[cursor : match.start()])
                            pieces.append(rf"\ensuremath{{\sum{body_value[1:]}}}")
                            cursor = i + 1
                        else:
                            pieces.append(line[cursor : i + 1])
                            cursor = i + 1
                        break
                i += 1
            else:
                pieces.append(line[cursor:])
                cursor = len(line)
                break
        return "".join(pieces)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def repair_bare_caret_inside_script_groups_final(text: str) -> str:
    """Turn OCR caret placeholders inside script groups into a math symbol."""
    return re.sub(
        r"(?P<script>[_^])\{(?P<body>[^{}\n]*?)\^(?P<tail>[^{}\n]*?)\}",
        lambda match: f"{match.group('script')}{{{match.group('body')}\\wedge{match.group('tail')}}}",
        text,
    )


def close_unclosed_ensuremath_lines_final(text: str) -> str:
    """Close single-line ensuremath spans that OCR left open."""
    out: list[str] = []
    for line in text.splitlines():
        if r"\ensuremath{" in line:
            depth = 0
            i = 0
            while i < len(line):
                if line[i] == "\\" and i + 1 < len(line):
                    i += 2
                    continue
                if line[i] == "{":
                    depth += 1
                elif line[i] == "}":
                    depth -= 1
                i += 1
            if depth > 0 and line.lstrip().startswith(r"\ensuremath{"):
                line += "}" * depth
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def neutralize_orphan_single_dollar_lines_final(text: str) -> str:
    """Remove single-line orphan dollar signs that would leak math mode forward."""
    out: list[str] = []
    dollar_re = re.compile(r"(?<!\\)\$")
    for line in text.splitlines():
        if not line.lstrip().startswith("%") and len(dollar_re.findall(line)) % 2 == 1:
            line = dollar_re.sub("", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def sanitize_ttfamily_text_blocks(text: str) -> str:
    """Escape math/control commands that leaked back into quarantined text blocks."""
    block_re = re.compile(
        r"(?P<open>\\par\\begingroup\\small\\ttfamily\\raggedright)(?P<body>.*?)(?P<close>\\endgroup)",
        re.S,
    )
    bare_command_re = re.compile(
        r"\\(?!(?:textbackslash|textasciicircum|textasciitilde)\b|[{}%&_#$])(?P<cmd>[A-Za-z]+)(?![A-Za-z])"
    )

    def sanitize_line(line: str) -> str:
        newline = "\n" if line.endswith("\n") else ""
        core = line[:-1] if newline else line
        par_match = re.search(r"\\par\s*$", core)
        suffix = ""
        if par_match:
            suffix = core[par_match.start() :]
            core = core[: par_match.start()]
        core = core.replace(r"\\", r"\textbackslash{}\textbackslash{}")
        core = bare_command_re.sub(lambda match: r"\textbackslash{}" + match.group("cmd"), core)
        core = core.replace("^", r"\textasciicircum{}")
        core = re.sub(r"(?<!\\)_", r"\\_", core)
        while True:
            surplus, _depth = brace_surplus_and_depth(core)
            end = len(core.rstrip())
            if surplus <= 0 or end == 0 or core[end - 1] != "}":
                break
            core = core[: end - 1] + core[end:]
        core = bare_command_re.sub(lambda match: r"\textbackslash{}" + match.group("cmd"), core)
        return core + suffix + newline

    def repl(match: re.Match[str]) -> str:
        body = "".join(sanitize_line(line) for line in match.group("body").splitlines(keepends=True))
        return match.group("open") + body + match.group("close")

    text = block_re.sub(repl, text)
    return text.replace(r"\ensuremath{}\par\begingroup\small\ttfamily\raggedright", r"\par\begingroup\small\ttfamily\raggedright")


def sanitize_printed_latex_command_groups(text: str) -> str:
    """Make printed command examples like \textbackslash{}ensuremath{h^2} inert text."""
    marker = r"\textbackslash{}"
    command_re = re.compile(r"[A-Za-z]+")

    def escape_group_text(value: str) -> str:
        replacements = {
            "&": r"\&",
            "%": r"\%",
            "$": r"\$",
            "#": r"\#",
            "_": r"\_",
            "{": r"\{",
            "}": r"\}",
            "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}",
        }
        return "".join(replacements.get(char, char) for char in value)

    def repair_line(line: str) -> str:
        pieces: list[str] = []
        cursor = 0
        while True:
            idx = line.find(marker, cursor)
            if idx == -1:
                pieces.append(line[cursor:])
                break
            pieces.append(line[cursor:idx])
            cmd_start = idx + len(marker)
            cmd_match = command_re.match(line, cmd_start)
            if not cmd_match:
                pieces.append(marker)
                cursor = cmd_start
                continue
            cmd_end = cmd_match.end()
            group = balanced_brace_group_span(line, cmd_end)
            if not group:
                pieces.append(line[idx:cmd_end])
                cursor = cmd_end
                continue
            body_start, body_end, end = group
            pieces.append(line[idx:cmd_end])
            pieces.append(r"\{" + escape_group_text(line[body_start:body_end]) + r"\}")
            cursor = end
        return "".join(pieces)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def unwrap_printed_latex_ensuremath_spans(text: str) -> str:
    """Do not keep escaped LaTeX evidence inside math mode."""
    marker = r"\ensuremath{"
    printed_command_re = re.compile(r"\\textbackslash\{\}[A-Za-z]{2,}")

    def repair_line(line: str) -> str:
        pieces: list[str] = []
        cursor = 0
        while True:
            idx = line.find(marker, cursor)
            if idx == -1:
                pieces.append(line[cursor:])
                break
            pieces.append(line[cursor:idx])
            body_start = idx + len(marker)
            depth = 1
            i = body_start
            while i < len(line):
                char = line[i]
                if char == "\\" and i + 1 < len(line):
                    i += 2
                    continue
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        body = line[body_start:i]
                        pieces.append(body if printed_command_re.search(body) else marker + body + "}")
                        cursor = i + 1
                        break
                i += 1
            else:
                pieces.append(line[idx:])
                cursor = len(line)
                break
        return "".join(pieces)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def remove_printed_display_math_delimiters(text: str) -> str:
    """Drop escaped display delimiters that leaked back as printed commands."""
    return text.replace(r"\textbackslash{}]", "").replace(r"\textbackslash{}[", "")


def _clean_printed_vector_component(value: str) -> str:
    """Normalize one printed vector component recovered from escaped LaTeX text."""
    value = value.strip()
    value = value.replace(r"\{", "").replace(r"\}", "")
    value = value.replace(r"\textbackslash{}", "")
    value = re.sub(r"\s+", " ", value).strip()
    return value or "?"


def recover_printed_column_vector_commands(text: str) -> str:
    """Recover printed pmatrix/array column-vector snippets into renderable math.

    MinerU/Popo occasionally leaves escaped fragments such as
    ``\textbackslash{}begin\{pmatrix\}2\textbackslash{}-3...``. Rendering
    those literally is worse than a conservative two-row vector recovery.
    """
    escaped_pmatrix = re.compile(
        r"\\textbackslash\{\}begin\\\{pmatrix\\\}\s*"
        r"(?P<top>[^\\{}\n]+?)\s*"
        r"\\textbackslash\{\}(?:\\textbackslash\{\})?\s*"
        r"(?P<bottom>[^\\{}\n]+?)\s*"
        r"\\textbackslash\{\}end\\\{pmatrix\\\}"
    )
    escaped_array = re.compile(
        r"(?:\\textbackslash\{\}left\()?"
        r"\\textbackslash\{\}begin\\\{array\\\}\\\{c\\\}\s*"
        r"(?P<top>[^\\{}\n]+?)\s*"
        r"\\textbackslash\{\}(?:\\textbackslash\{\})?\s*"
        r"(?P<bottom>[^\\{}\n]+?)\s*"
        r"\\textbackslash\{\}end\\\{array\\\}"
        r"(?:\\textbackslash\{\}right\))?"
    )

    def vector_repl(match: re.Match[str]) -> str:
        top = _clean_printed_vector_component(match.group("top"))
        bottom = _clean_printed_vector_component(match.group("bottom"))
        return rf"\ensuremath{{\binom{{{top}}}{{{bottom}}}}}"

    text = escaped_pmatrix.sub(vector_repl, text)
    text = escaped_array.sub(vector_repl, text)
    text = re.sub(
        r"\\textbackslash\{\}overrightarrow\\\{(?P<body>[^{}\n]+)\\\}",
        lambda match: rf"\ensuremath{{\overrightarrow{{{match.group('body').strip()}}}}}",
        text,
    )
    return text


def plainify_decoded_latex_ttfamily_blocks(text: str) -> str:
    """Turn decoded LaTeX-word dumps in quarantine blocks into normal text.

    This handles OCR evidence like ``ensuremath mathbf a = begin pmatrix 2 4
    end pmatrix``. It is intentionally narrow and only acts inside quarantined
    monospaced evidence blocks.
    """
    block_re = re.compile(
        r"(?P<open>\\par\\begingroup\\small\\ttfamily\\raggedright)(?P<body>.*?)(?P<close>\\endgroup)",
        re.S,
    )
    trigger_re = re.compile(r"\b(?:ensuremath|begin\s+(?:p?matrix|array)|end\s+(?:p?matrix|array))\b", re.I)
    vector_re = re.compile(
        r"\bbegin\s+(?:p?matrix|array)\s+(?:c\s+)?"
        r"(?P<top>[+\-]?(?:\d+(?:\.\d+)?|[A-Za-z]+|[A-Za-z]\^\d+|[^\s]+))\s+"
        r"(?P<bottom>[+\-]?(?:\d+(?:\.\d+)?|[A-Za-z]+|[A-Za-z]\^\d+|[^\s]+))\s+"
        r"end\s+(?:p?matrix|array)\b",
        re.I,
    )

    def clean_body_line(line: str) -> str:
        stripped = line.strip()
        if stripped.endswith(r"\par"):
            stripped = stripped[: -len(r"\par")].rstrip()
        return stripped

    def plainify(value: str) -> str:
        value = re.sub(r"\b\d+\s*\|p\s+[0-9.]+\s*\|", " ", value)
        value = vector_re.sub(
            lambda match: f"({_clean_printed_vector_component(match.group('top'))}, "
            f"{_clean_printed_vector_component(match.group('bottom'))})",
            value,
        )
        replacements = {
            "ensuremath": " ",
            "mathbf": " ",
            "mathrm": " ",
            "mathit": " ",
            "mathsf": " ",
            "quad": " ",
            "qquad": " ",
            "overrightarrow": "vector",
            "frac": "fraction",
            "sqrt": "sqrt",
        }
        for old, new in replacements.items():
            value = re.sub(rf"\b{old}\b", new, value, flags=re.I)
        value = re.sub(r"\b(?:begin|end|p?matrix|array)\b", " ", value, flags=re.I)
        value = re.sub(r"\b([A-Za-z])\s+\1\s*=", r"\1 =", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def repl(match: re.Match[str]) -> str:
        raw_lines = [clean_body_line(line) for line in match.group("body").splitlines()]
        raw_lines = [line for line in raw_lines if line]
        if not raw_lines or not any(trigger_re.search(line) for line in raw_lines):
            return match.group(0)
        plain = plainify(" ".join(raw_lines))
        if not plain:
            return match.group(0)
        return "\n".join(latex_escape_text(chunk) + r"\par" for chunk in chunk_plain_evidence(plain, limit=620))

    return block_re.sub(repl, text)


def wrap_bare_textcircled_subscripts(text: str) -> str:
    """Protect OCR math terms such as u_{\textcircled{4}} in text mode."""
    pattern = re.compile(r"(?<![A-Za-z0-9}\\])(?P<var>[A-Za-z])_\{\\textcircled\{(?P<num>\d+)\}\}")
    return pattern.sub(
        lambda match: rf"\ensuremath{{{match.group('var')}_{{\text{{\textcircled{{{match.group('num')}}}}}}}}}",
        text,
    )


def remove_orphan_endgroup_lines(text: str) -> str:
    """Drop standalone \endgroup lines that no longer have a matching \begingroup."""
    depth = 0
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        opens = line.count(r"\begingroup")
        closes = line.count(r"\endgroup")
        if stripped == r"\endgroup" and depth <= 0:
            continue
        out.append(line)
        depth += opens
        depth -= closes
        if depth < 0:
            depth = 0
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_sqrt_frac_missing_close_before_rowbreak(text: str) -> str:
    """Close \sqrt{...} when OCR ended it at an array row break."""
    pattern = re.compile(
        r"\\sqrt\{(?P<body>\\frac\{(?:[^{}]|\{[^{}]*\})+\}\{(?:[^{}]|\{[^{}]*\})+\})\s*(?=\\\\|&|\\end\{array\})"
    )
    return pattern.sub(lambda match: r"\sqrt{" + match.group("body") + "}", text)


def repair_malformed_stackrel_final(text: str) -> str:
    """Normalize OCR-split \stackrel {{top}} base fragments."""
    pattern = re.compile(
        r"\\stackrel\s*\{\s*\{(?P<top>[^{}\n]+)\}\s*\}\s*(?P<base>\\[A-Za-z]+\{[^{}\n]+\}(?:_\{[^{}\n]+\})?|[A-Za-z0-9])"
    )
    return pattern.sub(lambda match: rf"\overset{{{match.group('top').strip()}}}{{{match.group('base')}}}", text)


def ensure_table_rows_end_final(text: str) -> str:
    """Add a missing row break to table rows that contain alignment cells."""
    lines = text.splitlines()
    out: list[str] = []
    in_table = False
    begin_re = re.compile(r"\\begin\{(?:tabularx?|longtable)\}")
    end_re = re.compile(r"\\end\{(?:tabularx?|longtable)\}")
    for line in lines:
        if begin_re.search(line):
            in_table = True
            out.append(line)
            continue
        if in_table and line.strip().startswith(r"\multicolumn"):
            if out and not re.search(r"(?:\\\\|\\hline)\s*$", out[-1].rstrip()):
                out[-1] = out[-1].rstrip() + r" \\ \hline"
            out.append(line)
            continue
        if in_table and end_re.search(line):
            if out and "&" in out[-1] and not re.search(r"(?:\\\\|\\hline)\s*$", out[-1].rstrip()):
                out[-1] = out[-1].rstrip() + r" \\ \hline"
            out.append(line)
            in_table = False
            continue
        if in_table and "&" in line and not re.search(r"(?:\\\\|\\hline)\s*$", line.rstrip()):
            line = line.rstrip() + r" \\ \hline"
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_bad_table_evidence_rows_final(text: str) -> str:
    """Render unrecoverable table-internal fragments as safe multicolumn evidence rows."""
    lines = text.splitlines()
    out: list[str] = []
    in_table = False
    active_cols = 1
    begin_re = re.compile(r"\\begin\{(?:tabularx?|longtable)\}")
    end_re = re.compile(r"\\end\{(?:tabularx?|longtable)\}")
    ttfamily_start = r"\par\begingroup\small\ttfamily\raggedright"
    bad_ensuremath_row_re = re.compile(r"\\ensuremath\{[^\n]*(?:\\\\|\\hline)[^\n]*\}")

    def compact(value: str, width: int = 900) -> str:
        value = re.sub(r"\s+", " ", value.strip())
        if len(value) <= width:
            return value
        return value[: width - 20].rstrip() + " ... [truncated]"

    def evidence_row(value: str) -> str:
        escaped = latex_escape_text(compact(value))
        return rf"\multicolumn{{{max(1, active_cols)}}}{{|p{{0.95\linewidth}}|}}{{\small\ttfamily {escaped}}} \\ \hline"

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if begin_re.search(stripped):
            in_table = True
            active_cols = 1
            out.append(line)
            i += 1
            continue
        if in_table and end_re.search(stripped):
            out.append(line)
            in_table = False
            i += 1
            continue
        if in_table and stripped == ttfamily_start:
            body: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() != r"\endgroup":
                body.append(lines[i].strip())
                i += 1
            if i < len(lines) and lines[i].strip() == r"\endgroup":
                i += 1
            out.append(evidence_row(" ".join(body)))
            continue
        if in_table and bad_ensuremath_row_re.search(line):
            out.append(evidence_row(line))
            i += 1
            continue
        if in_table and "&" in line and r"\multicolumn" not in line:
            active_cols = max(active_cols, line.count("&") + 1)
        out.append(line)
        i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def demote_orphan_multicolumn_evidence_rows_final(text: str) -> str:
    """Move single-column evidence rows back to normal text when they are outside tables."""
    out: list[str] = []
    in_table = False
    begin_re = re.compile(r"\\begin\{(?:tabularx?|longtable)\}")
    end_re = re.compile(r"\\end\{(?:tabularx?|longtable)\}")
    orphan_re = re.compile(r"^\\multicolumn\{\d+\}\{\|p\{0\.95\\linewidth\}\|\}\{\\small\\ttfamily\b")

    def chunk_plain(value: str, width: int = 780) -> list[str]:
        value = re.sub(r"\s+", " ", value.strip())
        chunks: list[str] = []
        while len(value) > width:
            cut = value.rfind(" ", 0, width)
            if cut < width // 2:
                cut = width
            chunks.append(value[:cut].strip())
            value = value[cut:].strip()
        if value:
            chunks.append(value)
        return chunks or [""]

    for line in text.splitlines():
        stripped = line.strip()
        if begin_re.search(stripped):
            in_table = True
            out.append(line)
            if end_re.search(stripped):
                in_table = False
            continue
        if in_table and end_re.search(stripped):
            out.append(line)
            in_table = False
            continue
        if not in_table and orphan_re.match(stripped):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(indent + r"\par\begingroup\small\ttfamily\raggedright")
            for chunk in chunk_plain(stripped):
                out.append(indent + latex_escape_text(chunk) + r"\par")
            out.append(indent + r"\endgroup")
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def wrap_bare_decimal_ldots_degrees(text: str) -> str:
    """Protect text-mode decimal ellipsis degree fragments."""
    pattern = re.compile(
        r"(?<![A-Za-z0-9}\\])(?P<num>\d+(?:\.\d+)?\\ldots\^\{(?:\\circ|\\ensuremath\{\\circ\})\})"
    )

    def repl(match: re.Match[str]) -> str:
        body = match.group("num").replace(r"^{\ensuremath{\circ}}", r"^{\circ}")
        return rf"\ensuremath{{{body}}}"

    return pattern.sub(repl, text)


def repair_dollar_power_groups_final(text: str) -> str:
    """Merge $base$^{$exp$} fragments into one math span."""
    text = re.sub(
        r"\$(?P<base>[^$\n]+)\$\s*(?P<op>[_^])\{\$(?P<script>[^$\n]+)\$\}",
        lambda match: rf"\ensuremath{{{match.group('base').strip()}{match.group('op')}{{{match.group('script').strip()}}}}}",
        text,
    )
    return text


def drop_empty_script_groups_final(text: str) -> str:
    """Drop empty OCR-created subscript/superscript groups."""
    return re.sub(r"(?<!\\)[_^]\{\s*\}", "", text)


def repair_unknown_negated_relation_artifacts(text: str) -> str:
    """Normalize OCR-joined nonexistent negated relation commands."""
    return text.replace(r"\nlequigarrow", r"\nleq").replace(r"\ngequigarrow", r"\ngeq")


def neutralize_escaped_percent_subscripts(text: str) -> str:
    """Drop impossible text-mode subscripts after escaped percent signs."""
    return re.sub(r"\\%_(?=\\ensuremath|\{|[A-Za-z0-9])", r"\\% ", text)


def repair_overarrow_ensuremath_arguments(text: str) -> str:
    """Move nested ensuremath out of vector-arrow command arguments."""
    pattern = re.compile(
        r"\\(?P<cmd>overrightarrow|overleftarrow|overleftrightarrow)\s*\{\\ensuremath\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}\}"
    )
    return pattern.sub(lambda match: rf"\ensuremath{{\{match.group('cmd')}{{{match.group('body')}}}}}", text)


def repair_math_commands_joined_to_text_final(text: str) -> str:
    """Split OCR joins where a math command is glued to prose or a variable."""
    commands = (
        "bigtriangleup",
        "bigtriangledown",
        "therefore",
        "because",
        "rightarrow",
        "leftarrow",
        "leqslant",
        "geqslant",
        "Delta",
        "delta",
        "Gamma",
        "Theta",
        "Lambda",
        "Sigma",
        "Omega",
        "triangle",
        "times",
        "div",
        "cdot",
        "circ",
        "square",
        "pi",
        "wedge",
        "vee",
        "cup",
        "cap",
        "leq",
        "geq",
        "neq",
        "pm",
    )
    command_re = re.compile(
        r"\\(?P<cmd>" + "|".join(sorted(commands, key=len, reverse=True)) + r")(?=[A-Za-z\u4e00-\u9fff])"
    )

    def repair_line(line: str) -> str:
        if line.lstrip().startswith("%"):
            return line
        masked, placeholders = mask_balanced_ensuremath_spans(line)
        masked = command_re.sub(lambda match: rf"\ensuremath{{\{match.group('cmd')}}} ", masked)
        return restore_balanced_ensuremath_spans(masked, placeholders)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def repair_joined_math_commands_inside_ensuremath_final(text: str) -> str:
    """Insert a boundary when OCR glued a math command to following text inside math."""
    commands = (
        "bigtriangleup",
        "bigtriangledown",
        "therefore",
        "because",
        "rightarrow",
        "leftarrow",
        "leqslant",
        "geqslant",
        "delta",
        "triangle",
        "times",
        "div",
        "cdot",
        "circ",
        "square",
        "pi",
        "wedge",
        "vee",
        "cup",
        "cap",
        "leq",
        "geq",
        "neq",
        "pm",
    )
    command_re = re.compile(
        r"\\(?P<cmd>" + "|".join(sorted(commands, key=len, reverse=True)) + r")(?=[A-Za-z\u4e00-\u9fff])"
    )
    return command_re.sub(lambda match: rf"\{match.group('cmd')} ", text)


def repair_bare_triangle_names_final(text: str) -> str:
    """Wrap text-mode triangle-name fragments such as \bigtriangleup{XYZ}."""
    triangle_re = re.compile(
        r"\\(?P<cmd>bigtriangleup|bigtriangledown|triangle)\s*(?:\{(?P<braced>[A-Za-z]{1,8})\}|(?P<plain>[A-Z]{2,8}))"
    )

    def repair_line(line: str) -> str:
        if line.lstrip().startswith("%"):
            return line
        masked, placeholders = mask_balanced_ensuremath_spans(line)
        masked = triangle_re.sub(
            lambda match: rf"\ensuremath{{\{match.group('cmd')} {(match.group('braced') or match.group('plain')).strip()}}}",
            masked,
        )
        return restore_balanced_ensuremath_spans(masked, placeholders)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def repair_double_superscripts_final(text: str) -> str:
    """Group OCR-created double superscripts so XeLaTeX can compile them."""
    simple = r"[^{}\n]+"
    for _ in range(3):
        previous = text
        text = re.sub(
            rf"(?P<base>[A-Za-z0-9])\^\{{(?P<first>{simple})\}}\s*\^\{{(?P<second>{simple})\}}",
            lambda match: rf"({match.group('base')}^{{{match.group('first')}}})^{{{match.group('second')}}}",
            text,
        )
        text = re.sub(
            rf"\\ensuremath\{{\{{\}}\^\{{(?P<first>{simple})\}}\}}\s*\^\{{(?P<second>{simple})\}}",
            lambda match: rf"\ensuremath{{({{}}^{{{match.group('first')}}})^{{{match.group('second')}}}}}",
            text,
        )
        if text == previous:
            break
    return text


def repair_sqrt_missing_radical_before_denominator_final(text: str) -> str:
    """Repair OCR splits like \frac{... \sqrt}{7}{2} into a valid fraction."""
    return re.sub(
        r"\\frac\{(?P<num>[^{}\n]*?)\\sqrt\}\{(?P<rad>[^{}\n]+)\}\{(?P<den>[^{}\n]+)\}",
        lambda match: rf"\frac{{{match.group('num')}\sqrt{{{match.group('rad')}}}}}{{{match.group('den')}}}",
        text,
    )


def repair_array_end_leaked_inside_ensuremath_final(text: str) -> str:
    """Move \end{array} back outside a prematurely opened \ensuremath span."""
    pattern = re.compile(
        r"\\begin\{array\}\{(?P<cols>[^{}\n]+)\}"
        r"(?P<head>.*?)"
        r"\\ensuremath\{(?P<body>.*?)\\end\{array\}\}",
    )

    def repair_line(line: str) -> str:
        if r"\begin{array}" not in line or r"\end{array}" not in line:
            return line
        repaired = pattern.sub(
            lambda match: rf"\ensuremath{{\begin{{array}}{{{match.group('cols')}}}{match.group('head')}{match.group('body').strip()}\end{{array}}}}",
            line,
        )
        if repaired == line and not line.lstrip().startswith(r"\ensuremath{"):
            stripped = line.strip()
            if re.search(r"^\\item\s+\\begin\{array\}", stripped):
                indent = line[: len(line) - len(line.lstrip())]
                body = stripped[len(r"\item ") :]
                return indent + r"\item " + rf"\ensuremath{{{body}}}"
        return repaired

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def unwrap_ensuremath_wrapped_exerciseheadings_final(text: str) -> str:
    """Exercise headings are text boxes; do not wrap the heading command in math mode."""
    pattern = re.compile(r"\\ensuremath\{(\\exerciseheading\{(?:[^{}]|\{[^{}]*\})*\})\}")
    return pattern.sub(r"\1", text)


def protect_exerciseheading_math_fragments_final(text: str) -> str:
    """Protect math-only commands that appear inside exercise heading text."""
    heading_re = re.compile(r"\\exerciseheading\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}")

    def repair_body(body: str) -> str:
        body = re.sub(
            r"(?<!\\ensuremath\{)\\boxed\s*\{(?P<inner>(?:[^{}]|\{[^{}]*\})*)\}",
            lambda match: rf"\ensuremath{{\boxed{{{match.group('inner')}}}}}",
            body,
        )
        body = re.sub(
            r"(?<!\\ensuremath\{)\\frac\s*\{(?P<num>[^{}\n]+)\}\{(?P<den>[^{}\n]+)\}",
            lambda match: rf"\ensuremath{{\frac{{{match.group('num')}}}{{{match.group('den')}}}}}",
            body,
        )
        body = re.sub(
            r"(?<!\\ensuremath\{)\\sqrt\s*\{(?P<inner>[^{}\n]+)\}",
            lambda match: rf"\ensuremath{{\sqrt{{{match.group('inner')}}}}}",
            body,
        )
        return body

    return heading_re.sub(lambda match: r"\exerciseheading{" + repair_body(match.group("body")) + "}", text)


def escape_stray_text_underscores_final(text: str) -> str:
    """Escape text-mode underscores while leaving math spans and comments untouched."""
    structural_re = re.compile(r"^\s*\\(?:includegraphics|label|ref|cite|url|path)\b")

    def repair_line(line: str) -> str:
        if line.lstrip().startswith("%") or structural_re.match(line):
            return line
        masked, placeholders = mask_balanced_ensuremath_spans(line)
        masked = re.sub(r"(?<!\\)_", r"\\_", masked)
        return restore_balanced_ensuremath_spans(masked, placeholders)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def repair_escaped_function_subscripts_final(text: str) -> str:
    """Repair OCR escaped subscripts on log-like functions and split integral commands."""
    simple_ensure = r"\\ensuremath\{(?:[^{}]|\{[^{}]*\})*\}"

    def clean(value: str) -> str:
        value = value.strip()
        if value.startswith(r"\ensuremath{"):
            return unwrap_ensuremath_balanced_line(value).strip()
        return value

    text = re.sub(r"\\ensuremath\{\\in\}\s*t\\_", r"\\ensuremath{\\int}_", text)
    text = re.sub(r"\\in\s*t\\_", r"\\int_", text)
    text = re.sub(r"\\ensuremath\{\\in\}\s*t_", r"\\ensuremath{\\int}_", text)
    text = re.sub(r"\\in\s*t_", r"\\int_", text)
    text = text.replace(r"\ensuremath{\int}\_", r"\ensuremath{\int}_")
    text = re.sub(r"\\(?P<fn>log|ln|lg)\\_", lambda match: rf"\{match.group('fn')}_", text)

    base = rf"(?:\{{(?P<braced>[^{{}}\n]+)\}}|(?P<plain>[A-Za-z0-9]+))"
    arg = rf"(?P<arg>{simple_ensure}|\([^()\n]{{1,120}}\)|\|[^|\s]{{1,120}}\||[A-Za-z0-9?]+(?:\.[A-Za-z0-9]+)?)"
    ensure_base_pattern = re.compile(
        rf"\\(?P<fn>log|ln|lg)(?:\\_|_)\{{\\ensuremath\{{(?P<base>(?:[^{{}}]|\{{[^{{}}]*\}})*)\}}\}}\s*{arg}"
    )
    text = ensure_base_pattern.sub(
        lambda match: rf"\ensuremath{{\{match.group('fn')}_{{{clean(match.group('base'))}}} {clean(match.group('arg'))}}}",
        text,
    )
    call_arg = r"(?P<call>\([^{}\s]*(?:\([^{}\s]*\)[^{}\s]*)*\)|\|[^|\s]{1,120}\|)"
    text = re.sub(
        rf"\\(?P<fn>log|ln|lg)_(?P<base>[A-Za-z0-9])\s*{call_arg}",
        lambda match: rf"\ensuremath{{\{match.group('fn')}_{{{match.group('base')}}}{match.group('call')}}}",
        text,
    )
    text = re.sub(
        rf"\\(?P<fn>log|ln|lg)_\{{(?P<base>[^{{}}\n]+)\}}\s*{call_arg}",
        lambda match: rf"\ensuremath{{\{match.group('fn')}_{{{match.group('base')}}}{match.group('call')}}}",
        text,
    )
    pattern = re.compile(rf"\\(?P<fn>log|ln|lg)(?:\\_|_){base}\s*{arg}")

    def repl(match: re.Match[str]) -> str:
        base_value = match.group("braced") or match.group("plain") or ""
        return rf"\ensuremath{{\{match.group('fn')}_{{{base_value}}} {clean(match.group('arg'))}}}"

    return pattern.sub(repl, text)


def repair_collapsed_matrix_rowbreaks_final(text: str) -> str:
    """Restore OCR-collapsed matrix row breaks such as 3&0\0&3."""
    return re.sub(r"(?<=\d)\\(?=\d)", r"\\\\", text)


def repair_backslash_digit_artifacts_final(text: str) -> str:
    """Drop OCR backslashes before literal digits in prose/math exercise text."""
    return re.sub(r"\\(?=\d(?:\\ensuremath|\\log|[A-Za-z0-9]))", "", text)


def repair_escaped_variable_subscripts_final(text: str) -> str:
    """Wrap escaped variable subscripts such as a\_n in math mode."""
    pattern = re.compile(r"(?<![\\A-Za-z0-9}])(?P<base>[A-Za-z])\\_(?P<script>[A-Za-z0-9]|\{[^{}\n]{1,40}\})")

    def repair_line(line: str) -> str:
        masked, placeholders = mask_balanced_ensuremath_spans(line)
        masked = pattern.sub(
            lambda match: rf"\ensuremath{{{match.group('base')}_{{{match.group('script').strip('{}')}}}}}",
            masked,
        )
        return restore_balanced_ensuremath_spans(masked, placeholders)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def repair_empty_base_subscripts_in_math_final(text: str) -> str:
    """Give OCR empty-base subscripts an explicit empty base inside math spans."""
    return re.sub(r"(?<![A-Za-z0-9}])_\s*(?=[A-Za-z])", r"{}_", text)


def repair_split_trig_function_commands_final(text: str) -> str:
    """Merge OCR splits such as \ta\ensuremath{n^{-1}...} into trig commands."""
    text = text.replace(r"\ta\ensuremath{\ensuremath{n^{-1}}", r"\ensuremath{\tan^{-1}")
    text = text.replace(r"\ta\ensuremath{n^{-1}", r"\ensuremath{\tan^{-1}")
    text = re.sub(
        r"\\ta\s*\\ensuremath\{\\ensuremath\{n\^\{-1\}\}(?P<body>(?:[^{}]|\{[^{}]*\})*)\}",
        lambda match: rf"\ensuremath{{\tan^{{-1}}{match.group('body')}}}",
        text,
    )
    text = re.sub(
        r"\\ta\s*\\ensuremath\{n\^\{-1\}(?P<body>(?:[^{}]|\{[^{}]*\})*)\}",
        lambda match: rf"\ensuremath{{\tan^{{-1}}{match.group('body')}}}",
        text,
    )
    dollar_split_re = re.compile(r"\\(?P<head>si|co|ta)\$(?P<tail>[ns])(?P<body>[^$\n]{0,80})\$")

    def dollar_split_repl(match: re.Match[str]) -> str:
        name = match.group("head") + match.group("tail")
        if name not in {"sin", "cos", "tan"}:
            return match.group(0)
        body = match.group("body").strip()
        body = body.replace(r"\textasciicircum()", "^")
        body = re.sub(r"\s+", " ", body)
        sep = "" if not body or body.startswith(("^", "_")) else " "
        return rf"\ensuremath{{\{name}{sep}{body}}}"

    text = dollar_split_re.sub(dollar_split_repl, text)
    return text


def repair_split_greek_ensuremath_commands_final(text: str) -> str:
    """Merge OCR splits such as \thet\ensuremath{a_n} into \theta_n."""
    greek = (
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "varepsilon",
        "zeta",
        "eta",
        "theta",
        "vartheta",
        "iota",
        "kappa",
        "lambda",
        "mu",
        "nu",
        "xi",
        "rho",
        "varrho",
        "sigma",
        "tau",
        "upsilon",
        "phi",
        "varphi",
        "chi",
        "psi",
        "omega",
    )
    by_prefix = {cmd[:-1]: cmd for cmd in greek if len(cmd) > 2}
    prefix_re = "|".join(sorted((re.escape(prefix) for prefix in by_prefix), key=len, reverse=True))
    body = r"(?:[^{}]|\{[^{}]*\})*"
    pattern = re.compile(rf"\\(?P<prefix>{prefix_re})\s*\\ensuremath\{{(?P<last>[A-Za-z])(?P<body>{body})\}}")

    def repl(match: re.Match[str]) -> str:
        command = by_prefix.get(match.group("prefix"))
        if not command or not command.endswith(match.group("last")):
            return match.group(0)
        return rf"\ensuremath{{\{command}{match.group('body')}}}"

    return pattern.sub(repl, text)


def repair_misordered_left_right_lines_final(text: str) -> str:
    """Drop sizing markers when a \right appears before its matching \left."""
    delimiter_re = re.compile(r"\\(?:left|right)(?![A-Za-z])\s*(\\[A-Za-z]+|[()[\]{}|.])")
    token_re = re.compile(r"\\(?P<side>left|right)(?![A-Za-z])")
    out: list[str] = []
    for line in text.splitlines():
        depth = 0
        invalid = False
        for match in token_re.finditer(line):
            if match.group("side") == "left":
                depth += 1
            elif depth:
                depth -= 1
            else:
                invalid = True
                break
        if invalid or depth:
            line = delimiter_re.sub(lambda match: match.group(1), line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_premature_math_env_closures_final(text: str) -> str:
    """Move a premature \ensuremath close past matrix/cases/aligned rows."""
    envs = r"(?:aligned|cases|(?:p|b|v|V)?matrix|array)"
    pattern = re.compile(
        rf"\\ensuremath\{{(?P<head>\\begin\{{(?P<env>{envs})\}}(?:\{{[^{{}}\n]*\}})?(?P<body>[^}}\n]{{0,240}}))\}}\s*(?P<tail>&[^\n]*?\\end\{{(?P=env)\}})"
    )

    def repl(match: re.Match[str]) -> str:
        if r"\cancel{" in match.group("head"):
            return match.group(0)
        tail = match.group("tail")
        suffix = ""
        if tail.endswith("}"):
            pass
        return rf"\ensuremath{{{match.group('head')} {tail}}}{suffix}"

    text = pattern.sub(repl, text)

    def repair_line(line: str) -> str:
        if r"\ensuremath{\begin{" not in line:
            return line
        if r"\cancel{" in line:
            return line
        for env in ("cases", "pmatrix", "bmatrix", "matrix", "array", "aligned"):
            if rf"\begin{{{env}}}" not in line or rf"\end{{{env}}}" not in line:
                continue
            line = re.sub(
                r"(\\ensuremath\{\\begin\{" + re.escape(env) + r"\}.*?)\}\s*&",
                lambda match: match.group(1) + " &",
                line,
            )
            line = re.sub(r"(\\end\{" + re.escape(env) + r"\})(?!\})", lambda match: match.group(1) + "}", line, count=1)
        return line

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def wrap_bare_math_environment_spans_final(text: str) -> str:
    """Wrap bare array/matrix/aligned/cases spans that landed in text macros."""
    envs = r"(?:aligned|cases|(?:p|b|v|V)?matrix|array)"
    same_line_re = re.compile(
        rf"(?<!\\ensuremath\{{)(?P<span>\\begin\{{(?P<env>{envs})\}}(?:\{{[^{{}}\n]*\}})?[^\n]*?\\end\{{(?P=env)\}})"
    )
    begin_re = re.compile(rf"(?<!\\ensuremath\{{)\\begin\{{(?P<env>{envs})\}}")

    def wrap_same_line(line: str) -> str:
        masked, placeholders = mask_balanced_ensuremath_spans(line)
        masked = same_line_re.sub(lambda match: rf"\ensuremath{{{match.group('span')}}}", masked)
        return restore_balanced_ensuremath_spans(masked, placeholders)

    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        masked, placeholders = mask_balanced_ensuremath_spans(line)
        match = begin_re.search(masked)
        if not match:
            out.append(wrap_same_line(line))
            i += 1
            continue
        env = match.group("env")
        end_re = re.compile(rf"\\end\{{{re.escape(env)}\}}")
        if end_re.search(masked):
            out.append(wrap_same_line(line))
            i += 1
            continue
        pieces = [line]
        j = i + 1
        found = False
        while j < len(lines) and j <= i + 8:
            pieces.append(lines[j])
            if end_re.search(lines[j]):
                found = True
                break
            if not lines[j].strip():
                break
            j += 1
        joined = " ".join(piece.strip() for piece in pieces)
        if found and len(joined) <= 2500:
            start = joined.find(r"\begin{" + env + "}")
            end_match = end_re.search(joined, start)
            if start != -1 and end_match:
                end = end_match.end()
                repaired = joined[:start] + rf"\ensuremath{{{joined[start:end]}}}" + joined[end:]
                out.append(repaired)
                i = j + 1
                continue
        out.append(wrap_same_line(line))
        i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def merge_orphan_table_continuation_lines_final(text: str) -> str:
    """Merge one-cell table continuation lines back into the previous row."""
    lines = text.splitlines()
    out: list[str] = []
    in_table = False
    begin_re = re.compile(r"\\begin\{(?:tabularx?|longtable)\}")
    end_re = re.compile(r"\\end\{(?:tabularx?|longtable)\}")
    row_end_re = re.compile(r"\s*\\\\\s*(?:\\hline)?\s*$")
    for line in lines:
        stripped = line.strip()
        if begin_re.search(stripped):
            in_table = True
            out.append(line)
            if end_re.search(stripped):
                in_table = False
            continue
        if in_table and end_re.search(stripped):
            out.append(line)
            in_table = False
            continue
        if (
            in_table
            and out
            and stripped
            and "&" not in stripped
            and not stripped.startswith((r"\hline", r"\multicolumn", r"\begin", r"\end"))
            and "&" in out[-1]
            and row_end_re.search(out[-1])
        ):
            out[-1] = row_end_re.sub(lambda match: " " + stripped + match.group(0), out[-1])
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_common_fraction_denominator_splits_final(text: str) -> str:
    """Repair common OCR fractions where the denominator command lost braces."""
    text = re.sub(
        r"\\frac\{(?P<num>[^{}\n]+)\}\\sqrt\{(?P<rad>[^{}\n]+)\}",
        lambda match: rf"\frac{{{match.group('num')}}}{{\sqrt{{{match.group('rad')}}}}}",
        text,
    )
    return text


def repair_cancelled_symbol_artifacts_final(text: str) -> str:
    """Use cancel for OCR crossed-out symbols emitted as invalid \not groups."""
    return re.sub(
        r"\\not\s*\{\s*(?P<body>[^{}&\\\s]{1,20})\s*\}?",
        lambda match: rf"\cancel{{{match.group('body')}}}",
        text,
    )


def wrap_bare_accent_commands_final(text: str) -> str:
    """Protect bare accent commands such as \tilde{2} in prose lines."""
    pattern = re.compile(r"(?<!\\ensuremath\{)\\(?P<cmd>tilde|hat|bar|vec|dot|ddot)\s*\{(?P<body>[^{}\n]{1,80})\}")

    def repair_line(line: str) -> str:
        masked, placeholders = mask_balanced_ensuremath_spans(line)
        masked = pattern.sub(lambda match: rf"\ensuremath{{\{match.group('cmd')}{{{match.group('body')}}}}}", masked)
        return restore_balanced_ensuremath_spans(masked, placeholders)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def sanitize_table_dollar_artifacts_final(text: str) -> str:
    """Escape literal dollar pairs that appear as table-cell content."""
    out: list[str] = []
    in_table = False
    begin_re = re.compile(r"\\begin\{(?:tabularx?|longtable)\}")
    end_re = re.compile(r"\\end\{(?:tabularx?|longtable)\}")
    for line in text.splitlines():
        stripped = line.strip()
        if begin_re.search(stripped):
            in_table = True
        if in_table:
            line = line.replace("$$", r"\$\$")
            line = re.sub(r"\\\\\s+\\\\\s+\\hline\s+\\\\\s+\\hline", r"\\ \\hline", line)
        if in_table and end_re.search(stripped):
            in_table = False
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def remove_dollar_only_table_cells_final(text: str) -> str:
    """Remove OCR math delimiters that contain no table-cell content."""
    out: list[str] = []
    in_table = False
    begin_re = re.compile(r"\\begin\{(?:tabularx?|longtable)\}")
    end_re = re.compile(r"\\end\{(?:tabularx?|longtable)\}")
    empty_cell_re = re.compile(r"(?P<left>(?:^|&))\s*(?:\\?\$\s*)+(?P<right>(?=&|\\\\|$))")
    for line in text.splitlines():
        stripped = line.strip()
        if begin_re.search(stripped):
            in_table = True
        if in_table:
            previous = None
            while previous != line:
                previous = line
                line = empty_cell_re.sub(lambda match: match.group("left") + " ", line)
        if in_table and end_re.search(stripped):
            in_table = False
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def remove_stray_label_braces_after_array_specs(text: str) -> str:
    """Remove an unmatched OCR brace after a leading array row label."""
    return re.sub(
        r"(\\begin\{array\}\{[lcr|\s]+\}\s*\([A-Za-z0-9]+\))\}(?=\s*&)",
        r"\1",
        text,
    )


def quarantine_illegal_array_spec_lines_final(text: str) -> str:
    """Quarantine arrays whose column specification swallowed row content."""
    out: list[str] = []
    bad_spec_re = re.compile(r"\\begin\{array\}\{[^{}\n]*&[^\n]*?(?:\\end\{array\}|\})")
    for line in text.splitlines():
        stripped = line.strip()
        if bad_spec_re.search(stripped):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(indent + r"\par\begingroup\small\ttfamily\raggedright")
            out.append(indent + latex_escape_text(stripped) + r"\par")
            out.append(indent + r"\endgroup")
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def quarantine_printed_pmatrix_tail_lines_final(text: str) -> str:
    """Quarantine printed pmatrix tails that cannot be safely reassembled."""
    out: list[str] = []
    bad_re = re.compile(r"\\end\\ensuremath\{\{p?matrix\}")
    for line in text.splitlines():
        stripped = line.strip()
        if bad_re.search(stripped):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(indent + r"\par\begingroup\small\ttfamily\raggedright")
            out.append(indent + latex_escape_text(stripped) + r"\par")
            out.append(indent + r"\endgroup")
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def quarantine_high_risk_math_environment_lines_final(text: str) -> str:
    """Downgrade badly malformed math-environment lines to source evidence."""
    out: list[str] = []

    def is_bad(stripped: str) -> bool:
        if r"\begin{aligned}" in stripped and r"\ensuremath{\begin{aligned}" not in stripped:
            return True
        if r"\end{aligned}" in stripped and r"\begin{aligned}" not in stripped:
            return True
        if stripped.startswith(r"\ensuremath{&"):
            return True
        if stripped.startswith(r"\ensuremath{") and r"\\&" in stripped:
            return True
        if r"\ensuremath{\begin{cases}" in stripped and re.search(r"\\frac\{[^{}\n]+\}\{[^{}\n&]*\s*&", stripped):
            return True
        if r"\ensuremath{\begin{pmatrix}" in stripped and (
            r"\_{{" in stripped
            or stripped.count(r"\ensuremath{") >= 3
            or re.search(r"\\ensuremath\{[^{}\n]*&[^{}\n]*\\end\{pmatrix\}", stripped)
        ):
            return True
        if r"\begin{array}" in stripped and (stripped.count(r"\ensuremath{") >= 3 or r"\lambda\_ {" in stripped):
            return True
        if r"\lo$g" in stripped:
            return True
        if r"\log\{\{" in stripped:
            return True
        if r"\frac{(" in stripped or "+ 1{n" in stripped:
            return True
        if stripped.count(r"\frac") >= 5 and stripped.count(r"\ensuremath{") >= 3:
            return True
        return False

    for line in text.splitlines():
        stripped = line.strip()
        if is_bad(stripped):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(indent + r"\par\begingroup\small\ttfamily\raggedright")
            out.append(indent + latex_escape_text(stripped) + r"\par")
            out.append(indent + r"\endgroup")
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def quarantine_bad_tabularx_blocks_final(text: str) -> str:
    """Downgrade tabularx blocks that contain already-downgraded row evidence."""
    lines = text.splitlines()
    out: list[str] = []
    begin_re = re.compile(r"\\begin\{(?:tabularx|longtable)\}")
    end_re = re.compile(r"\\end\{(?:tabularx|longtable)\}")
    i = 0

    def bad_block(block: list[str]) -> bool:
        joined = "\n".join(block)
        return any(
            marker in joined
            for marker in (
                r"\par\begingroup\small\ttfamily",
                r"\textbackslash{}par",
                r"\$\$ \ \hline",
                r"\ \hline",
            )
        )

    def emit_evidence(block: list[str]) -> None:
        out.append(r"\par\begingroup\small\ttfamily\raggedright")
        for raw in block:
            value = latex_escape_text(raw.strip())
            if value:
                out.append(value + r"\par")
        out.append(r"\endgroup")

    while i < len(lines):
        if not begin_re.search(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        block = [lines[i]]
        j = i + 1
        while j < len(lines):
            block.append(lines[j])
            if end_re.search(lines[j]):
                break
            j += 1
        if j < len(lines) and bad_block(block):
            emit_evidence(block)
            i = j + 1
            continue
        out.extend(block)
        i = j + 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def sanitize_ttfamily_brace_noise_final(text: str) -> str:
    """Avoid TeX brace commands inside downgraded evidence text blocks."""
    out: list[str] = []
    in_ttfamily = False
    start = r"\par\begingroup\small\ttfamily\raggedright"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == start:
            in_ttfamily = True
            out.append(line)
            continue
        if in_ttfamily and stripped == r"\endgroup":
            in_ttfamily = False
            out.append(line)
            continue
        if in_ttfamily:
            line = line.replace(r"\{", "(").replace(r"\}", ")")
            line = line.replace("{", "(").replace("}", ")")
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_split_includegraphics_final(text: str) -> str:
    """Repair OCR command split where \includegraphics became \in cludegraphics."""
    return re.sub(r"\\ensuremath\{\\in\}\s*cludegraphics(?=\[)", r"\\includegraphics", text)


def neutralize_odd_dollars_in_long_prose_final(text: str) -> str:
    """Remove broken raw dollars from long prose/list lines that cannot be balanced."""
    out: list[str] = []
    structural_re = re.compile(r"^\s*\\(?:begin|end)\{|^\s*\\(?:includegraphics|caption|chapter|section|subsection)\b")
    for line in text.splitlines():
        if not structural_re.search(line):
            dollar_count = len(re.findall(r"(?<!\\)\$", line))
            prose_words = re.findall(r"[A-Za-z]{3,}", line)
            if dollar_count % 2 == 1 and len(prose_words) >= 8:
                line = re.sub(r"(?<!\\)\$", "", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def plainify_prose_ensuremath_superscripts_final(text: str) -> str:
    """Turn prose accidentally wrapped as an empty-base superscript back into text."""
    pattern = re.compile(r"\\ensuremath\{\{\}\^\{(?P<head>[A-Za-z][A-Za-z-]*)\}(?P<tail>[^{}\n]*[A-Za-z][^{}\n]*)\}")

    def repl(match: re.Match[str]) -> str:
        value = (match.group("head") + match.group("tail")).strip()
        if "'" in value or len(re.findall(r"[A-Za-z]{3,}", value)) >= 3:
            return latex_escape_text(value)
        return match.group(0)

    return pattern.sub(repl, text)


def plainify_identifier_subscript_runs_final(text: str) -> str:
    """Render OCR identifier trails like LB _3 _2 _32 as text, not chained subscripts."""
    pattern = re.compile(r"\\ensuremath\{(?P<body>[A-Za-z]{1,8}(?:\s*_\s*[A-Za-z0-9]+){2,})\}")

    def repl(match: re.Match[str]) -> str:
        body = re.sub(r"\s+", "", match.group("body"))
        return latex_escape_text(body)

    return pattern.sub(repl, text)


def drop_orphan_linebreak_lines_final(text: str) -> str:
    """Drop standalone \\ lines outside alignment/table environments."""
    out: list[str] = []
    env_depth = 0
    begin_re = re.compile(r"\\begin\{(?:tabularx?|longtable|array|aligned|align\*?|cases|(?:p|b|v|V)?matrix)\}")
    end_re = re.compile(r"\\end\{(?:tabularx?|longtable|array|aligned|align\*?|cases|(?:p|b|v|V)?matrix)\}")
    for line in text.splitlines():
        stripped = line.strip()
        starts = len(begin_re.findall(stripped))
        if env_depth == 0 and stripped == r"\\":
            out.append("")
            continue
        env_depth += starts
        env_depth = max(0, env_depth - len(end_re.findall(stripped)))
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def replace_escaped_setminus_in_math_context(text: str) -> str:
    """Interpret escaped backslashes as set difference in math-dense set expressions."""
    out: list[str] = []
    math_set_re = re.compile(r"\\(?:wedge|cap|cup|in|subseteq|supseteq|emptyset|varnothing)\b")
    for line in text.splitlines():
        if r"\textbackslash{}" in line and math_set_re.search(line):
            line = re.sub(r"\\textbackslash\{\}(?=[A-Za-z\\])", r"\\setminus ", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_scripts_inside_text_commands_final(text: str) -> str:
    """Keep simple OCR superscripts from breaking \text{...} blocks."""
    text = re.sub(
        r"\\text\{(?P<prefix>[^{}\n]*?)(?P<base>[A-Za-z0-9])\^\{\\prime\}(?P<suffix>[^{}\n]*?)\}",
        lambda match: rf"\text{{{match.group('prefix')}{match.group('base')}'{match.group('suffix')}}}",
        text,
    )
    pattern = re.compile(r"\\text\{(?P<body>(?:\\[{}]|[^{}\n])*)\}")

    def repl(match: re.Match[str]) -> str:
        body = match.group("body")
        body = re.sub(r"([A-Za-z0-9])\^\{\\prime\}", r"\1'", body)
        body = body.replace("^", r"\textasciicircum{}")
        body = re.sub(r"(?<!\\)_", r"\\_", body)
        return r"\text{" + body + "}"

    return pattern.sub(repl, text)


def quarantine_orphan_array_tail_lines(text: str) -> str:
    """Render orphaned array tails as escaped evidence instead of invalid alignments."""
    out: list[str] = []
    orphan_tail_re = re.compile(r"\\end\{(?:array|(?:p|b|v|V)?matrix|cases)\}")
    orphan_begin_re = re.compile(r"\\begin\{(?:array|(?:p|b|v|V)?matrix|cases)\}")
    for line in text.splitlines():
        stripped = line.strip()
        if orphan_tail_re.search(stripped) and not orphan_begin_re.search(stripped):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(indent + r"\par\begingroup\small\ttfamily\raggedright")
            out.append(indent + latex_escape_text(stripped) + r"\par")
            out.append(indent + r"\endgroup")
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def quarantine_text_array_collision_lines(text: str) -> str:
    """Render unrecoverable \text \begin{array} collisions as escaped evidence."""
    out: list[str] = []
    collision_re = re.compile(r"\\text\s+\\begin\{array\}")
    for line in text.splitlines():
        stripped = line.strip()
        if collision_re.search(stripped):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(indent + r"\par\begingroup\small\ttfamily\raggedright")
            out.append(indent + latex_escape_text(stripped) + r"\par")
            out.append(indent + r"\endgroup")
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def merge_ensuremath_script_ensuremath_final(text: str) -> str:
    """Merge \ensuremath{base}_\ensuremath{subscript} fragments."""
    pattern = re.compile(
        r"\\ensuremath\{(?P<base>\\(?:mathrm|mathbf|mathit|mathcal|mathsf|mathbb)\{[^{}\n]+\}|[^{}\n]+)\}(?P<op>[_^])\\ensuremath\{(?P<script>(?:[^{}]|\{[^{}]*\})*)\}"
    )
    return pattern.sub(
        lambda match: rf"\ensuremath{{{match.group('base')}{match.group('op')}{{{match.group('script')}}}}}",
        text,
    )


def merge_bare_base_ensuremath_script_final(text: str) -> str:
    """Merge text-mode bases such as N_{\ensuremath{d}} into math."""
    pattern = re.compile(
        r"(?<![A-Za-z0-9}\\])(?P<base>[A-Za-z]+)(?P<op>[_^])\{\\ensuremath\{(?P<script>(?:[^{}]|\{[^{}]*\})*)\}\}"
    )
    return pattern.sub(
        lambda match: rf"\ensuremath{{{match.group('base')}{match.group('op')}{{{match.group('script')}}}}}",
        text,
    )


def merge_simple_script_after_ensuremath_final(text: str) -> str:
    """Merge simple one-token scripts after an ensuremath span."""
    pattern = re.compile(r"\\ensuremath\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}(?P<op>[_^])(?P<script>[*+\-A-Za-z0-9])")
    return pattern.sub(
        lambda match: rf"\ensuremath{{{match.group('body')}{match.group('op')}{{{match.group('script')}}}}}",
        text,
    )


def wrap_bare_base_script_group_final(text: str) -> str:
    """Wrap bare variable script groups left in prose."""
    script = r"(?:\\ensuremath\{(?:[^{}]|\{[^{}]*\})*\}|[^{}\n]|\{[^{}]*\}){1,120}?"
    pattern = re.compile(rf"(?<![\\A-Za-z0-9}}\]])(?P<base>[A-Za-z])(?P<op>[_^])\{{(?P<script>{script})\}}")
    return pattern.sub(
        lambda match: rf"\ensuremath{{{match.group('base')}{match.group('op')}{{{match.group('script')}}}}}",
        text,
    )


def wrap_closing_delimiter_script_tails_final(text: str) -> str:
    """Wrap scripts attached to text-mode bracket/parenthesis tails."""
    pattern = re.compile(
        r"(?<![\\A-Za-z0-9])(?P<base>[A-Za-z0-9)\]]{1,40})(?P<op>[_^])\{(?P<script>[^{}\n]{1,80})\}"
    )

    def repair_line(line: str) -> str:
        masked, placeholders = mask_balanced_ensuremath_spans(line)
        masked = pattern.sub(
            lambda match: rf"\ensuremath{{{match.group('base')}{match.group('op')}{{{match.group('script')}}}}}",
            masked,
        )
        return restore_balanced_ensuremath_spans(masked, placeholders)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def repair_spacing_command_scripts_final(text: str) -> str:
    """Turn invalid spacing-command scripts into empty-base scripts."""
    pattern = re.compile(r"\\(?:quad|qquad)\s*(?P<op>[_^])(?=\{)")

    def repair_line(line: str) -> str:
        masked, placeholders = mask_balanced_ensuremath_spans(line)
        pieces: list[str] = []
        cursor = 0
        search = 0
        changed = False
        while True:
            match = pattern.search(masked, search)
            if not match:
                pieces.append(masked[cursor:])
                break
            span = balanced_brace_group_span(masked, match.end())
            if not span:
                search = match.end()
                continue
            body = masked[span[0] : span[1]]
            for idx, original in enumerate(placeholders):
                body = body.replace(f"@@ENSUREMATH{idx}@@", unwrap_ensuremath_balanced_line(original).strip())
            body = body.strip()
            pieces.append(masked[cursor : match.start()])
            pieces.append(rf"\ensuremath{{{{}}{match.group('op')}{{{body}}}}}")
            cursor = span[2]
            search = span[2]
            changed = True
        repaired = "".join(pieces) if changed else masked
        return restore_balanced_ensuremath_spans(repaired, placeholders)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def unwrap_scripts_inside_ensuremath_final(text: str) -> str:
    """Remove nested ensuremath wrappers used only as scripts inside math spans."""
    def repair_line(line: str) -> str:
        return re.sub(
            r"(?P<op>[_^])\\ensuremath\{(?P<script>(?:[^{}]|\{[^{}]*\})*)\}",
            lambda match: rf"{match.group('op')}{{{match.group('script')}}}",
            line,
        )

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def wrap_escaped_delimiter_scripts_final(text: str) -> str:
    """Wrap text-mode escaped braces with sub/superscripts."""
    trigger_re = re.compile(r"\\(?P<brace>[{}])(?P<op>[_^])(?=\{)")

    def clean_body(body: str, placeholders: list[str]) -> str:
        for idx, original in enumerate(placeholders):
            body = body.replace(f"@@ENSUREMATH{idx}@@", unwrap_ensuremath_balanced_line(original).strip())
        return re.sub(r"\s+", " ", body).strip()

    def repair_line(line: str) -> str:
        masked, placeholders = mask_balanced_ensuremath_spans(line)
        pieces: list[str] = []
        cursor = 0
        search = 0
        changed = False
        while True:
            match = trigger_re.search(masked, search)
            if not match:
                pieces.append(masked[cursor:])
                break
            span = balanced_brace_group_span(masked, match.end())
            if not span:
                search = match.end()
                continue
            body = clean_body(masked[span[0] : span[1]], placeholders)
            if not body or len(body) > 180:
                search = span[2]
                continue
            pieces.append(masked[cursor : match.start()])
            pieces.append(rf"\ensuremath{{\{match.group('brace')}{match.group('op')}{{{body}}}}}")
            cursor = span[2]
            search = span[2]
            changed = True
        repaired = "".join(pieces) if changed else masked
        return restore_balanced_ensuremath_spans(repaired, placeholders)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def wrap_bare_stackrel_final(text: str) -> str:
    """Wrap bare two-argument stackrel/overset constructs in math mode."""
    command_re = re.compile(r"\\(?P<cmd>stackrel|overset|underset)(?![A-Za-z])")

    def clean_arg(body: str, placeholders: list[str]) -> str:
        for idx, original in enumerate(placeholders):
            body = body.replace(f"@@ENSUREMATH{idx}@@", unwrap_ensuremath_balanced_line(original).strip())
        return body.strip()

    def repair_line(line: str) -> str:
        masked, placeholders = mask_balanced_ensuremath_spans(line)
        pieces: list[str] = []
        cursor = 0
        search = 0
        changed = False
        while True:
            match = command_re.search(masked, search)
            if not match:
                pieces.append(masked[cursor:])
                break
            first = balanced_brace_group_span(masked, match.end())
            if not first:
                search = match.end()
                continue
            second = balanced_brace_group_span(masked, first[2])
            if not second:
                search = first[2]
                continue
            top = clean_arg(masked[first[0] : first[1]], placeholders)
            base = clean_arg(masked[second[0] : second[1]], placeholders)
            if not top or not base or len(top) + len(base) > 240:
                search = second[2]
                continue
            pieces.append(masked[cursor : match.start()])
            pieces.append(rf"\ensuremath{{\{match.group('cmd')}{{{top}}}{{{base}}}}}")
            cursor = second[2]
            search = second[2]
            changed = True
        repaired = "".join(pieces) if changed else masked
        return restore_balanced_ensuremath_spans(repaired, placeholders)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def neutralize_orphan_alignment_tabs_final(text: str) -> str:
    """Escape or remove alignment tabs that are outside alignment/table environments."""
    out: list[str] = []
    env_depth = 0
    begin_re = re.compile(r"\\begin\{(?:tabularx?|longtable|array|aligned|align\*?|matrix|pmatrix|bmatrix|cases)\}")
    end_re = re.compile(r"\\end\{(?:tabularx?|longtable|array|aligned|align\*?|matrix|pmatrix|bmatrix|cases)\}")
    for line in text.splitlines():
        stripped = line.strip()
        starts_env = len(begin_re.findall(stripped))
        if env_depth == 0 and "&" in line and not starts_env:
            indent = line[: len(line) - len(line.lstrip())]
            if stripped.startswith("&"):
                line = indent + stripped[1:].lstrip()
            line = re.sub(r"(?<!\\)&", r"\\&", line)
        env_depth += starts_env
        env_depth = max(0, env_depth - len(end_re.findall(stripped)))
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def ensure_list_item_before_quarantined_blocks(text: str) -> str:
    """Ensure downgraded evidence blocks inside lists still have an item anchor."""
    out: list[str] = []
    list_stack: list[bool] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in (r"\begin{enumerate}", r"\begin{itemize}"):
            list_stack.append(False)
            out.append(line)
            continue
        if stripped in (r"\end{enumerate}", r"\end{itemize}"):
            if list_stack:
                list_stack.pop()
            out.append(line)
            continue
        if list_stack and stripped.startswith(r"\item"):
            list_stack[-1] = True
            out.append(line)
            continue
        if list_stack and not list_stack[-1] and stripped:
            indent = line[: len(line) - len(line.lstrip())]
            out.append(indent + r"\item[]")
            list_stack[-1] = True
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def close_lists_after_quarantined_single_item_blocks(text: str) -> str:
    """Close lists whose only item body is a downgraded source-evidence block."""
    lines = text.splitlines()
    out: list[str] = []
    begin_re = re.compile(r"^\\begin\{(?P<env>enumerate|itemize)\}(?:\[[^\]]*\])?\s*$")
    end_re = re.compile(r"^\\end\{(?P<env>enumerate|itemize)\}\s*$")
    ttfamily_start = r"\par\begingroup\small\ttfamily\raggedright"
    stack: list[dict[str, object]] = []

    def next_nonblank(start: int) -> str | None:
        for value in lines[start:]:
            if value.strip():
                return value.strip()
        return None

    def item_remainder(stripped: str) -> str | None:
        if not stripped.startswith(r"\item"):
            return None
        remainder = stripped[len(r"\item") :].strip()
        if remainder.startswith("["):
            close = remainder.find("]")
            if close != -1:
                remainder = remainder[close + 1 :].strip()
        return remainder

    for idx, line in enumerate(lines):
        stripped = line.strip()
        begin_match = begin_re.match(stripped)
        if begin_match:
            stack.append(
                {"env": begin_match.group("env"), "candidate": False, "in_ttfamily": False, "item_count": 0}
            )
            out.append(line)
            continue

        end_match = end_re.match(stripped)
        if end_match:
            env = end_match.group("env")
            for pos in range(len(stack) - 1, -1, -1):
                if stack[pos]["env"] == env:
                    del stack[pos:]
                    break
            out.append(line)
            continue

        if stack:
            top = stack[-1]
            remainder = item_remainder(stripped)
            if remainder is not None:
                top["item_count"] = int(top.get("item_count", 0)) + 1
                top["candidate"] = remainder == ""
                top["in_ttfamily"] = False
                out.append(line)
                continue

            if top.get("candidate") and stripped == ttfamily_start:
                top["in_ttfamily"] = True
                out.append(line)
                continue

            if top.get("candidate") and top.get("in_ttfamily") and stripped == r"\endgroup":
                out.append(line)
                following = next_nonblank(idx + 1)
                if int(top.get("item_count", 0)) == 1 and (
                    following is None or (item_remainder(following) is None and not end_re.match(following))
                ):
                    env = str(top["env"])
                    out.append(rf"\end{{{env}}}")
                    stack.pop()
                continue

            if top.get("candidate") and not top.get("in_ttfamily") and stripped:
                top["candidate"] = False

        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def balance_unclosed_list_environments_final(text: str) -> str:
    """Append missing list closers for surviving unbalanced enumerate/itemize blocks."""
    lines = text.splitlines()
    out: list[str] = []
    stack: list[str] = []
    begin_re = re.compile(r"^\\begin\{(?P<env>enumerate|itemize)\}(?:\[[^\]]*\])?\s*$")
    end_re = re.compile(r"^\\end\{(?P<env>enumerate|itemize)\}\s*$")
    for line in lines:
        stripped = line.strip()
        begin_match = begin_re.match(stripped)
        if begin_match:
            stack.append(begin_match.group("env"))
        else:
            end_match = end_re.match(stripped)
            if end_match:
                env = end_match.group("env")
                matched = False
                for pos in range(len(stack) - 1, -1, -1):
                    if stack[pos] == env:
                        del stack[pos:]
                        matched = True
                        break
                if not matched:
                    continue
        out.append(line)
    while stack:
        out.append(rf"\end{{{stack.pop()}}}")
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def drop_surplus_closing_braces_in_prose_lines(text: str) -> str:
    """Final cleanup for OCR prose fragments with stray closing braces."""
    out: list[str] = []
    structural_re = re.compile(r"^\\(?:begin|end)\{|^\\(?:chapter|section|subsection|subsubsection|caption|includegraphics)\b")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not structural_re.match(stripped):
            surplus, _depth = brace_surplus_and_depth(line)
            if surplus > 0:
                line = drop_surplus_closing_braces(line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def wrap_math_command_dominant_lines(text: str) -> str:
    """Wrap derivation lines led by math commands after unwrapping inner ensuremath spans."""
    out: list[str] = []
    in_display = False
    in_table = False
    begin_table_re = re.compile(r"\\begin\{(?:tabularx?|longtable)\}")
    end_table_re = re.compile(r"\\end\{(?:tabularx?|longtable)\}")
    structural_re = re.compile(
        r"^\\(?:begin|end|section|subsection|subsubsection|chapter|item|includegraphics|caption)\b"
    )
    trigger_re = re.compile(
        r"^(?:\\(?:therefore|because|Rightarrow|Leftarrow|Leftrightarrow|Longleftrightarrow|"
        r"implies|iff|boldsymbol)\b|"
        r".*\\(?:widehat|hat|boldsymbol)\b.*=)"
    )
    math_signal_re = re.compile(
        r"\\(?:quad|widehat|hat|text|frac|sqrt|pm|times|div|cdot|therefore|because|"
        r"Longleftrightarrow|boldsymbol|cup|cap|varnothing)\b|[_^=]"
    )
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "$$":
            in_display = not in_display
            out.append(line)
            continue
        if begin_table_re.search(stripped):
            in_table = True
            out.append(line)
            if end_table_re.search(stripped):
                in_table = False
            continue
        if in_table and end_table_re.search(stripped):
            out.append(line)
            in_table = False
            continue
        if in_display or in_table or not stripped or structural_re.match(stripped) or "&" in stripped:
            out.append(line)
            continue
        if re.search(r"(?<!\\)\$", stripped):
            out.append(line)
            continue

        body = unwrap_ensuremath_balanced_line(stripped)
        body = re.sub(r"\\(widehat|hat|mathrm|mathbf|mathit|mathcal|mathsf|operatorname|text)\s+\{", r"\\\1{", body)
        if trigger_re.match(body) and math_signal_re.search(body):
            indent = line[: len(line) - len(line.lstrip())]
            line = indent + rf"\ensuremath{{{body}}}"
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_textasciicircum_math_artifacts(text: str) -> str:
    """Turn OCR text-mode caret commands followed by scripts back into math scripts."""
    text = re.sub(
        r"(?P<num>\d+(?:\.\d+)?)\\textasciicircum\{\}\\circ",
        lambda match: rf"\ensuremath{{{match.group('num')}^{{\circ}}}}",
        text,
    )
    text = re.sub(r"\\textasciicircum\{\}\\circ", lambda _match: r"\ensuremath{{}^{\circ}}", text)
    text = re.sub(
        r"\\textasciicircum\{\}\s*\{(?P<body>(?:[^{}]|\\ensuremath\{[^{}]*\})+)\}",
        r"^{\g<body>}",
        text,
    )
    text = re.sub(r"\\textasciicircum\{\}\s*\{(?P<body>[^{}\n]+)\}", r"^{\g<body>}", text)
    text = re.sub(r"\\textasciicircum\{\}\s*(?P<body>[A-Za-z0-9]+)", r"^{\g<body>}", text)
    return text


def repair_single_backslash_array_rows_final(text: str) -> str:
    """Promote OCR single row-ending backslashes to LaTeX array row breaks."""
    return re.sub(r"(?m)(?<!\\)\\\s*$", r"\\\\", text)


def repair_item_linebreak_artifacts(text: str) -> str:
    """Remove empty OCR line breaks immediately after list item commands."""
    return re.sub(r"(?m)^(?P<indent>\s*\\item)\s*\\\\\s*$", r"\g<indent>", text)


def strip_dollars_inside_frac_arguments_final(text: str) -> str:
    """Remove inline dollar delimiters embedded inside \frac arguments."""
    text = re.sub(
        r"\\frac\{\$(?P<num>[^$\n]+)\$\}\{(?P<den>[^{}\n]+)\}",
        lambda match: rf"\frac{{{match.group('num')}}}{{{match.group('den')}}}",
        text,
    )
    text = re.sub(
        r"\\frac\{(?P<num>[^{}\n]+)\}\{\$(?P<den>[^$\n]+)\$\}",
        lambda match: rf"\frac{{{match.group('num')}}}{{{match.group('den')}}}",
        text,
    )
    return text


def strip_dollars_inside_math_command_groups_final(text: str) -> str:
    """Remove accidental dollar delimiters inside balanced math-command arguments."""
    command_groups = {"frac": 2, "dfrac": 2, "tfrac": 2, "binom": 2, "sqrt": 1}

    def parse_group_span(value: str, pos: int):
        while pos < len(value) and value[pos].isspace():
            pos += 1
        if pos >= len(value) or value[pos] != "{":
            return None
        depth = 0
        i = pos
        while i < len(value):
            char = value[i]
            if char == "\\":
                i += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return pos + 1, i, i + 1
            i += 1
        return None

    def strip_line(line: str) -> str:
        remove: set[int] = set()
        for match in re.finditer(r"\\(?P<cmd>frac|dfrac|tfrac|binom|sqrt)(?![A-Za-z])", line):
            cmd = match.group("cmd")
            pos = match.end()
            if cmd == "sqrt":
                while pos < len(line) and line[pos].isspace():
                    pos += 1
                if pos < len(line) and line[pos] == "[":
                    end = line.find("]", pos)
                    if end != -1:
                        pos = end + 1
            spans: list[tuple[int, int]] = []
            ok = True
            for _ in range(command_groups[cmd]):
                parsed = parse_group_span(line, pos)
                if not parsed:
                    ok = False
                    break
                start, end, pos = parsed
                spans.append((start, end))
            if not ok:
                continue
            for start, end in spans:
                for idx in range(start, end):
                    if line[idx] == "$" and (idx == 0 or line[idx - 1] != "\\"):
                        remove.add(idx)
        if not remove:
            return line
        return "".join(char for idx, char in enumerate(line) if idx not in remove)

    return "\n".join(strip_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def collapse_nested_ensuremath(text: str) -> str:
    """Collapse simple nested \ensuremath wrappers created by repeated repairs."""
    simple_body = r"[^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*"
    previous = None
    while previous != text:
        previous = text
        text = re.sub(
            rf"\\ensuremath\{{\\ensuremath\{{(?P<body>{simple_body})\}}\}}",
            lambda match: rf"\ensuremath{{{match.group('body')}}}",
            text,
        )
    return text


def neutralize_residual_odd_math_dollars(text: str) -> str:
    """Drop residual broken dollar delimiters on math-heavy lines."""
    out: list[str] = []
    math_signal = re.compile(
        r"\\(?:frac|sqrt|ensuremath|mathbb)\b|"
        r"\{\}\s*[_^]|"
        r"[A-Za-z0-9)\]}]\s*[_^]\s*(?:\{|[A-Za-z0-9\[])"
    )
    for raw_line in text.splitlines(keepends=True):
        line = raw_line[:-1] if raw_line.endswith("\n") else raw_line
        newline = "\n" if raw_line.endswith("\n") else ""
        dollar_count = len(re.findall(r"(?<!\\)\$", line))
        broken_dense = (
            dollar_count >= 4
            and math_signal.search(line)
            and bool(re.search(r"(?<!\\)\$\s+(?<!\\)\$|(?<!\\)\${2,}|(?<!\\)\$\d(?<!\\)\$\d", line))
        )
        if (dollar_count > 1 and dollar_count % 2 == 1 and math_signal.search(line)) or broken_dense:
            line = re.sub(r"(?<!\\)\$", "", line)
            line = line.replace(r"\$", "")
        out.append(line + newline)
    return "".join(out)


def strip_inline_dollars_in_standalone_display_blocks(text: str) -> str:
    """Remove inline dollars inside display blocks delimited by standalone $$ lines."""
    lines = text.splitlines()
    out: list[str] = []
    in_display = False
    for line in lines:
        if line.strip() == "$$":
            out.append(line)
            in_display = not in_display
            continue
        if in_display:
            line = re.sub(r"(?<!\\)\$", "", line)
            line = line.replace(r"\$", "")
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_trig_command_spacing(text: str) -> str:
    """Split OCR-joined trig/log commands such as \sinx into \sin x."""
    return re.sub(r"\\(sin|cos|tan|log|ln|lg)(?=[A-Za-z])", r"\\\1 ", text)


def repair_split_section_commands(text: str) -> str:
    """Undo accidental splitting of LaTeX sectioning commands."""
    text = text.replace(r"\sec tion", r"\section")
    text = text.replace(r"\subsec tion", r"\subsection")
    text = text.replace(r"\subsubsec tion", r"\subsubsection")
    return text


def neutralize_annotation_marker_dollars(text: str) -> str:
    """Drop OCR-created math openers after circled annotation markers."""
    marker_dollar_re = re.compile(r"(\\textcircled\{\d+\}\s*)\$(?=\s*[A-Za-z0-9(])")
    return "\n".join(marker_dollar_re.sub(r"\1", line) for line in text.splitlines()) + (
        "\n" if text.endswith("\n") else ""
    )


def escape_body_percent_signs_final(text: str) -> str:
    """Escape percent signs in body text while leaving source-page comments intact."""
    out: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("%"):
            out.append(line)
            continue
        out.append(re.sub(r"(?<!\\)%", r"\\%", line))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def close_unclosed_size_groups_final(text: str) -> str:
    """Close table/evidence size groups that survived after table downgrade."""
    out: list[str] = []
    size_open_re = re.compile(r"^\{\\(?:small|scriptsize|footnotesize)\s*$")
    structural_re = re.compile(
        r"^(?:% source|\\(?:chapter|section|subsection|subsubsection|begin\{(?:figure|itemize|enumerate)\}|end\{(?:figure|itemize|enumerate)\}))"
    )
    in_size_group = False
    seen_body = False
    in_ttfamily_block = False

    def closes_printed_table(stripped: str) -> bool:
        return (
            r"\end{tabular" in stripped
            or r"\end{longtable}" in stripped
            or stripped.endswith(r"(tabularx)\par")
            or stripped.endswith(r"(tabular)\par")
            or stripped.endswith(r"(longtable)\par")
        )

    for line in text.splitlines():
        stripped = line.strip()
        if in_size_group and seen_body and not in_ttfamily_block and (not stripped or structural_re.match(stripped)):
            out.append("}")
            in_size_group = False
            seen_body = False
        out.append(line)
        if size_open_re.match(stripped):
            in_size_group = True
            seen_body = False
            in_ttfamily_block = False
            continue
        if in_size_group and stripped:
            seen_body = True
            if stripped == r"\par\begingroup\small\ttfamily\raggedright":
                in_ttfamily_block = True
            if in_ttfamily_block:
                if stripped == r"\endgroup":
                    out.append("}")
                    in_size_group = False
                    seen_body = False
                    in_ttfamily_block = False
                continue
            if closes_printed_table(stripped):
                out.append("}")
                in_size_group = False
                seen_body = False
    if in_size_group:
        out.append("}")
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_joined_percent_membership_commands_final(text: str) -> str:
    """Split OCR joins such as \%\iny into printable percent plus math relation."""
    text = re.sub(r"\\%\\in(?=([A-Za-z]))", r"\\% \\ensuremath{\\in} ", text)
    text = re.sub(r"\\%\\notin(?=([A-Za-z]))", r"\\% \\ensuremath{\\notin} ", text)
    return text


def repair_joined_membership_relation_variables_final(text: str) -> str:
    """Split OCR joins such as \inp into \in p without touching longer commands."""
    text = re.sub(r"\\in(?=[A-Za-z](?![A-Za-z]))", r"\\in ", text)
    text = re.sub(r"\\notin(?=[A-Za-z](?![A-Za-z]))", r"\\notin ", text)
    return text


def repair_empty_subscript_markers_final(text: str) -> str:
    """Remove OCR placeholder underscores that have no subscript body."""
    return re.sub(r"(?<=\s)_(?=\s*(?:[+\-=,;.)}\\]|$))", "", text)


def repair_split_structural_commands_from_math_symbols(text: str) -> str:
    """Undo OCR splits where a command word was partly converted into a math symbol."""
    text = re.sub(r"\\ensuremath\{\\cap\}\s*tion(?=\{)", r"\\caption", text)
    return text


def neutralize_unbalanced_left_right_in_ensuremath_spans(text: str) -> str:
    """Drop sizing-only \left/\right markers when OCR left a math span unbalanced."""
    marker = r"\ensuremath{"
    left_right_re = re.compile(r"\\(?:left|right)(?![A-Za-z])")
    sized_delimiter_re = re.compile(r"\\(?:left|right)(?![A-Za-z])\s*(\\[A-Za-z]+|[()[\]{}|.])")

    def simplify_if_needed(body: str) -> str:
        left_count = len(re.findall(r"\\left(?![A-Za-z])", body))
        right_count = len(re.findall(r"\\right(?![A-Za-z])", body))
        if left_count == right_count:
            return body
        if not left_right_re.search(body):
            return body
        return sized_delimiter_re.sub(lambda match: match.group(1), body)

    def repair_line(line: str) -> str:
        pieces: list[str] = []
        cursor = 0
        while True:
            idx = line.find(marker, cursor)
            if idx == -1:
                pieces.append(line[cursor:])
                break
            pieces.append(line[cursor:idx])
            body_start = idx + len(marker)
            depth = 1
            i = body_start
            while i < len(line):
                char = line[i]
                if char == "\\" and i + 1 < len(line):
                    i += 2
                    continue
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        pieces.append(marker + simplify_if_needed(line[body_start:i]) + "}")
                        cursor = i + 1
                        break
                i += 1
            else:
                pieces.append(line[idx:])
                cursor = len(line)
                break
        return "".join(pieces)

    return "\n".join(repair_line(line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


def neutralize_unbalanced_left_right_lines(text: str) -> str:
    """Drop sizing-only \left/\right markers on lines where the pair count is impossible."""
    delimiter_re = re.compile(r"\\(?:left|right)(?![A-Za-z])\s*(\\[A-Za-z]+|[()[\]{}|.])")
    out: list[str] = []
    for line in text.splitlines():
        left_count = len(re.findall(r"\\left(?![A-Za-z])", line))
        right_count = len(re.findall(r"\\right(?![A-Za-z])", line))
        if left_count != right_count:
            line = delimiter_re.sub(lambda match: match.group(1), line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def protect_bare_math_commands_final(text: str) -> str:
    """Make text-mode math commands safe while leaving existing math spans readable."""
    relation_commands = {
        "leq",
        "geq",
        "nleq",
        "ngeq",
        "nless",
        "ngtr",
        "leqslant",
        "geqslant",
        "ne",
        "neq",
        "neg",
        "to",
        "iff",
        "implies",
        "impliedby",
        "mapsto",
        "colon",
        "therefore",
        "because",
        "rightarrow",
        "leftarrow",
        "longleftarrow",
        "longrightarrow",
        "longmapsto",
        "leftrightarrow",
        "Rightarrow",
        "Leftarrow",
        "Leftrightarrow",
        "Longleftrightarrow",
        "longleftrightarrow",
        "rightleftharpoons",
        "leftrightharpoons",
        "rightleftarrows",
        "leftrightarrows",
        "hookrightarrow",
        "hookleftarrow",
        "twoheadrightarrow",
        "twoheadleftarrow",
        "rightarrowtail",
        "leftarrowtail",
        "uparrow",
        "downarrow",
        "updownarrow",
        "Uparrow",
        "Downarrow",
        "Updownarrow",
        "nearrow",
        "searrow",
        "swarrow",
        "nwarrow",
        "square",
        "blacksquare",
        "blacklozenge",
        "top",
        "bot",
        "boxminus",
        "boxplus",
        "boxtimes",
        "boxdot",
        "Box",
        "mid",
        "vdash",
        "dashv",
        "models",
        "ll",
        "gg",
        "circ",
        "bigcirc",
        "parallel",
        "perp",
        "times",
        "div",
        "cdot",
        "oplus",
        "otimes",
        "ominus",
        "odot",
        "bigoplus",
        "bigotimes",
        "bigodot",
        "uplus",
        "amalg",
        "pm",
        "mp",
        "setminus",
        "infty",
        "min",
        "max",
        "lim",
        "sup",
        "inf",
        "limsup",
        "liminf",
        "ker",
        "dim",
        "det",
        "gcd",
        "lcm",
        "deg",
        "arg",
        "Re",
        "Im",
        "Pr",
        "sin",
        "cos",
        "tan",
        "cot",
        "sec",
        "csc",
        "arcsin",
        "arccos",
        "arctan",
        "sinh",
        "cosh",
        "tanh",
        "sum",
        "prod",
        "ell",
        "hbar",
        "hslash",
        "aleph",
        "vert",
        "lvert",
        "rvert",
        "backslash",
        "wedge",
        "vee",
        "land",
        "lor",
        "forall",
        "exists",
        "nexists",
        "bullet",
        "ast",
        "star",
        "triangle",
        "bigtriangleup",
        "bigtriangledown",
        "pi",
        "subseteq",
        "nsubseteq",
        "subset",
        "supseteq",
        "nsupseteq",
        "supset",
        "in",
        "notin",
        "diamond",
        "angle",
        "simeq",
        "approx",
        "sim",
        "equiv",
        "cong",
        "propto",
        "prec",
        "succ",
        "preceq",
        "succeq",
        "preccurlyeq",
        "succcurlyeq",
        "nprec",
        "nsucc",
        "npreceq",
        "nsucceq",
        "precsim",
        "succsim",
        "precapprox",
        "succapprox",
        "curlyeqprec",
        "curlyeqsucc",
        "triangleleft",
        "triangleright",
        "vartriangleleft",
        "vartriangleright",
        "ntriangleleft",
        "ntriangleright",
        "trianglelefteq",
        "trianglerighteq",
        "unlhd",
        "unrhd",
        "lhd",
        "rhd",
        "varnothing",
        "emptyset",
        "cap",
        "cup",
        "bigcap",
        "bigcup",
        "sqcap",
        "sqcup",
        "bigsqcup",
        "Delta",
        "Gamma",
        "Theta",
        "Lambda",
        "Xi",
        "Pi",
        "Sigma",
        "Upsilon",
        "Phi",
        "Psi",
        "Omega",
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "varepsilon",
        "zeta",
        "eta",
        "theta",
        "vartheta",
        "iota",
        "kappa",
        "lambda",
        "mu",
        "nu",
        "xi",
        "rho",
        "varrho",
        "sigma",
        "tau",
        "upsilon",
        "phi",
        "varphi",
        "chi",
        "psi",
        "omega",
        "lg",
    }
    command_re = re.compile(r"\\(?P<cmd>" + "|".join(sorted(relation_commands, key=len, reverse=True)) + r")(?![A-Za-z])")

    def wrap_relations(segment: str) -> str:
        def ensuremath_span(value: str, start: int) -> tuple[int, int] | None:
            marker = r"\ensuremath{"
            if not value.startswith(marker, start):
                return None
            depth = 1
            i = start + len(marker)
            while i < len(value):
                char = value[i]
                if char == "\\" and i + 1 < len(value):
                    i += 2
                    continue
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        return start, i + 1
                i += 1
            return None

        segment = re.sub(
            r"\\(?P<cmd>dot|ddot|vec|bar|hat|tilde|widetilde|overline|underline)\s*\{\\ensuremath\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}\}",
            lambda match: rf"\ensuremath{{\{match.group('cmd')}{{{match.group('body')}}}}}",
            segment,
        )

        if r"\ensuremath{" in segment:
            pieces: list[str] = []
            cursor = 0
            found = False
            while True:
                idx = segment.find(r"\ensuremath{", cursor)
                if idx == -1:
                    pieces.append(wrap_relations(segment[cursor:]))
                    break
                span = ensuremath_span(segment, idx)
                if not span:
                    pieces.append(segment[cursor:])
                    break
                found = True
                pieces.append(wrap_relations(segment[cursor:idx]))
                pieces.append(segment[span[0] : span[1]])
                cursor = span[1]
            if found:
                return "".join(pieces)

        def script_repl(match: re.Match[str]) -> str:
            expr = re.sub(r"\s+", " ", match.group("expr")).strip()
            return rf"\ensuremath{{{expr}}}"

        def array_repl(match: re.Match[str]) -> str:
            return (
                r"\ensuremath{\begin{array}{"
                + match.group("cols")
                + "}"
                + match.group("body")
                + r"\end{array}}"
            )

        def repl(match: re.Match[str]) -> str:
            prefix = segment[max(0, match.start() - len(r"\ensuremath{")) : match.start()]
            if prefix.endswith(r"\ensuremath{"):
                return match.group(0)
            return rf"\ensuremath{{\{match.group('cmd')}}}"

        segment = re.sub(r"\\(?:left|right)\s*(\\?[()\[\]{}|.])", r"\1", segment)
        segment = re.sub(r"\\mathbb\s*\{([A-Z])\}", r"\\ensuremath{\\mathbb{\1}}", segment)
        segment = re.sub(
            r"(?P<sign>-?)\\ensuremath\{(?P<deg>\d+(?:\.\d+)?\^\{\\circ\})\}\s*\\mathrm\{(?P<unit>[CF])\}",
            lambda match: rf"\ensuremath{{{match.group('sign')}{match.group('deg')} \mathrm{{{match.group('unit')}}}}}",
            segment,
        )
        segment = re.sub(
            r"\\begin\{array\}\{(?P<cols>[^{}\n]+)\}(?P<body>.*?)\\end\{array\}",
            array_repl,
            segment,
        )
        segment = re.sub(
            r"\\begin\{aligned\}(?P<body>.*?)\\end\{aligned\}",
            lambda match: r"\ensuremath{\begin{aligned}" + match.group("body") + r"\end{aligned}}",
            segment,
        )
        segment = re.sub(
            r"\\left\s*(?P<open>\\[A-Za-z]+|[()[\]{}|.])(?P<body>.*?)\\right\s*(?P<close>\\[A-Za-z]+|[()[\]{}|.])",
            lambda match: rf"\ensuremath{{\left{match.group('open')}{match.group('body')}\right{match.group('close')}}}",
            segment,
        )
        segment = re.sub(
            r"\\(?P<cmd>mathrm|mathbf|mathit|mathcal|mathsf|mathbb|mathfrak|operatorname|text)\s*\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}",
            lambda match: rf"\ensuremath{{\{match.group('cmd')}{{{match.group('body')}}}}}",
            segment,
        )
        segment = re.sub(
            r"\\boldsymbol\s*\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}",
            lambda match: rf"\ensuremath{{\boldsymbol{{{match.group('body')}}}}}",
            segment,
        )
        segment = re.sub(
            r"\\widehat\s*\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}",
            lambda match: rf"\ensuremath{{\widehat{{{match.group('body')}}}}}",
            segment,
        )
        segment = re.sub(
            r"\\(?P<cmd>overrightarrow|overleftarrow|overleftrightarrow)\s*\{\\ensuremath\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}\}",
            lambda match: rf"\ensuremath{{\{match.group('cmd')}{{{match.group('body')}}}}}",
            segment,
        )
        segment = re.sub(
            r"\\(?P<cmd>overrightarrow|overleftarrow|overleftrightarrow)\s*\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}",
            lambda match: rf"\ensuremath{{\{match.group('cmd')}{{{match.group('body')}}}}}",
            segment,
        )
        segment = re.sub(
            r"\\hat\s*\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}",
            lambda match: rf"\ensuremath{{\hat{{{match.group('body')}}}}}",
            segment,
        )
        segment = re.sub(
            r"\\underbrace\s*\{(?P<body>(?:[^{}]|\{[^{}]*\}|\\ensuremath\{[^{}]*\})*)\}\s*(?P<script>[_^]\s*\{(?:[^{}]|\{[^{}]*\})*\})?",
            lambda match: rf"\ensuremath{{\underbrace{{{match.group('body')}}}{match.group('script') or ''}}}",
            segment,
        )
        segment = re.sub(
            r"\\(?P<cmd>dot|ddot|vec|bar|hat|tilde|widetilde|overline|underline)\s*\{\\ensuremath\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}\}",
            lambda match: rf"\ensuremath{{\{match.group('cmd')}{{{match.group('body')}}}}}",
            segment,
        )
        segment = re.sub(
            r"\\(?P<cmd>dot|ddot|vec|bar)\s*\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}",
            lambda match: rf"\ensuremath{{\{match.group('cmd')}{{{match.group('body')}}}}}",
            segment,
        )
        segment = re.sub(
            r"\\vert\s*(?P<body>[^\\\n]{1,80}?)\\vert",
            lambda match: rf"\ensuremath{{\vert {match.group('body').strip()} \vert}}",
            segment,
        )
        segment = re.sub(
            r"\\overline\s*\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}",
            lambda match: rf"\ensuremath{{\overline{{{match.group('body')}}}}}",
            segment,
        )
        segment = re.sub(
            r"(?<!\\ensuremath\{)\\(?P<fn>sin|cos|tan|log|ln|lg)\s*\((?P<arg>[^()\n]*(?:\\ensuremath\{[^{}]*\}[^()\n]*)*)\)",
            lambda match: rf"\ensuremath{{\{match.group('fn')}({match.group('arg')})}}",
            segment,
        )
        segment = re.sub(
            r"(?<!\\ensuremath\{)\\(?P<fn>sin|cos|tan|log|ln|lg)\s+(?P<arg>[A-Za-z0-9](?:\([^()\n]*\))?)",
            lambda match: rf"\ensuremath{{\{match.group('fn')} {match.group('arg')}}}",
            segment,
        )
        segment = re.sub(
            r"(?<!\\ensuremath\{)\\(?P<fn>log|ln|lg)(?P<arg>\d+(?:\.\d+)?)",
            lambda match: rf"\ensuremath{{\{match.group('fn')} {match.group('arg')}}}",
            segment,
        )
        segment = re.sub(
            r"(?<!\\ensuremath\{)\\(?P<fn>log|ln|lg)\s*\\ensuremath\{(?P<arg>[^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*)\}",
            lambda match: rf"\ensuremath{{\{match.group('fn')} {match.group('arg')}}}",
            segment,
        )
        segment = re.sub(
            r"(?<!\\ensuremath\{)\\(?P<fn>log|ln|lg)\s*(?P<script>[_^]\{[^{}\n]+\})\s*(?P<arg>[A-Za-z0-9]+)",
            lambda match: rf"\ensuremath{{\{match.group('fn')}{match.group('script')}{match.group('arg')}}}",
            segment,
        )
        segment = re.sub(
            r"(?<!\\ensuremath\{)\\(?P<fn>log|ln|lg)\s*_\s*\{(?P<base>[^{}\n]+)\}\s*(?P<arg>[A-Za-z0-9]+)",
            lambda match: rf"\ensuremath{{\{match.group('fn')}_{{{match.group('base')}}}{match.group('arg')}}}",
            segment,
        )
        segment = re.sub(
            r"(?<!\\ensuremath\{)\\(?P<fn>log|ln|lg)\s*_\s*\{(?P<base>[^{}\n]+)\}\s*\\ensuremath\{(?P<arg>[^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*)\}",
            lambda match: rf"\ensuremath{{\{match.group('fn')}_{{{match.group('base')}}}{match.group('arg')}}}",
            segment,
        )
        segment = re.sub(
            r"(?<!\\ensuremath\{)\\(?P<fn>log|ln|lg)\s*_(?P<base>[A-Za-z0-9])\s*\\ensuremath\{(?P<arg>[^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*)\}",
            lambda match: rf"\ensuremath{{\{match.group('fn')}_{match.group('base')}{match.group('arg')}}}",
            segment,
        )
        segment = re.sub(
            r"(?<!\\ensuremath\{)\\(?P<fn>log|ln|lg)\s*_(?P<base>[A-Za-z0-9])(?P<arg>[A-Za-z0-9]{1,12})",
            lambda match: rf"\ensuremath{{\{match.group('fn')}_{match.group('base')} {match.group('arg')}}}",
            segment,
        )
        segment = re.sub(
            r"(?<!\\ensuremath\{)\\(?P<fn>log|ln|lg)\s*_(?P<base>[A-Za-z0-9])\s+(?P<arg>[A-Za-z0-9])",
            lambda match: rf"\ensuremath{{\{match.group('fn')}_{match.group('base')} {match.group('arg')}}}",
            segment,
        )
        segment = re.sub(
            r"(?<![\\A-Za-z])(?P<expr>[A-Za-z0-9]+\s*\^\s*\{\s*[-+]?\\frac\s*\{[^{}\n]+\}\s*\{[^{}\n]+\}\s*\})",
            script_repl,
            segment,
        )
        segment = re.sub(
            r"(?<!\\)(?P<expr>\{[A-Za-z0-9.,]+\}\s*(?:[_^]\s*(?:\{[^{}\n]+\}|[A-Za-z0-9+\-\[\]]+))+)",
            script_repl,
            segment,
        )
        segment = wrap_bare_frac_commands(segment)
        segment = wrap_bare_sqrt_commands(segment)
        segment = wrap_bare_binom_commands(segment)
        segment = re.sub(
            r"(?<![\\A-Za-z])(?P<expr>(?:\{\}|[A-Za-z0-9]+)\s*(?:[_^]\s*(?:\{[^{}\n]+\}|[A-Za-z0-9+\-\[\]]+))+)",
            script_repl,
            segment,
        )
        segment = re.sub(
            r"(?<![\\\w])(?P<expr>[\w]\s*(?:[_^]\s*(?:\{[^{}\n]+\}|[A-Za-z0-9+\-\[\]]+))+)",
            script_repl,
            segment,
        )
        segment = re.sub(
            r"(?<!\\)(?P<expr>\([^$\n]{1,120}\)\s*(?:[_^]\s*(?:\{[^{}\n]+\}|[A-Za-z0-9+\-\[\]]+))+)",
            script_repl,
            segment,
        )
        segment = re.sub(
            r"(?<!\\)(?P<expr>\{[^{}\n]{1,160}\}\s*(?:[_^]\s*(?:\{[^{}\n]+\}|[A-Za-z0-9+\-\[\]]+))+)",
            script_repl,
            segment,
        )
        segment = re.sub(r"(?<!\\)\^(?=\s*[,}\]])", r"\\textasciicircum{}", segment)
        segment = re.sub(r"(?<!\\)\^(?!\{)", r"\\textasciicircum{}", segment)
        return command_re.sub(repl, segment)

    def apply_preserving_ttfamily(value: str) -> str:
        out: list[str] = []
        normal: list[str] = []
        in_ttfamily = False
        start = r"\par\begingroup\small\ttfamily\raggedright"

        def flush_normal() -> None:
            if normal:
                out.append(apply_outside_dollar_math("".join(normal), wrap_relations))
                normal.clear()

        for line in value.splitlines(keepends=True):
            stripped = line.strip()
            if stripped == start:
                flush_normal()
                in_ttfamily = True
                out.append(line)
                continue
            if in_ttfamily:
                out.append(line)
                if stripped == r"\endgroup":
                    in_ttfamily = False
                continue
            normal.append(line)
        flush_normal()
        return "".join(out)

    return apply_preserving_ttfamily(text)


def wrap_bare_sqrt_commands(segment: str) -> str:
    """Wrap bare \sqrt commands, allowing nested braces in the radicand."""
    def parse_group(value: str, pos: int):
        while pos < len(value) and value[pos].isspace():
            pos += 1
        if pos >= len(value) or value[pos] != "{":
            return None
        depth = 0
        start = pos + 1
        i = pos
        while i < len(value):
            char = value[i]
            if char == "\\":
                i += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return value[start:i], i + 1
            i += 1
        return None

    pieces: list[str] = []
    cursor = 0
    while True:
        idx = segment.find(r"\sqrt", cursor)
        if idx == -1:
            pieces.append(segment[cursor:])
            break
        pieces.append(segment[cursor:idx])
        pos = idx + len(r"\sqrt")
        root = ""
        while pos < len(segment) and segment[pos].isspace():
            pos += 1
        if pos < len(segment) and segment[pos] == "[":
            end = segment.find("]", pos)
            if end != -1:
                root = segment[pos : end + 1]
                pos = end + 1
        group = parse_group(segment, pos)
        if not group:
            pieces.append(r"\sqrt")
            cursor = pos
            continue
        pieces.append(r"\ensuremath{\sqrt" + root + "{" + group[0] + "}}")
        cursor = group[1]
    return "".join(pieces)


def wrap_bare_binom_commands(segment: str) -> str:
    """Wrap bare \binom commands, allowing text commands inside arguments."""
    def parse_group(value: str, pos: int):
        while pos < len(value) and value[pos].isspace():
            pos += 1
        if pos >= len(value) or value[pos] != "{":
            return None
        depth = 0
        start = pos + 1
        i = pos
        while i < len(value):
            char = value[i]
            if char == "\\":
                i += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return value[start:i], i + 1
            i += 1
        return None

    pieces: list[str] = []
    cursor = 0
    while True:
        idx = segment.find(r"\binom", cursor)
        if idx == -1:
            pieces.append(segment[cursor:])
            break
        pieces.append(segment[cursor:idx])
        pos = idx + len(r"\binom")
        top = parse_group(segment, pos)
        if not top:
            pieces.append(r"\binom")
            cursor = pos
            continue
        bottom = parse_group(segment, top[1])
        if not bottom:
            pieces.append(segment[idx:top[1]])
            cursor = top[1]
            continue
        pieces.append(r"\ensuremath{\binom{" + top[0] + "}{" + bottom[0] + "}}")
        cursor = bottom[1]
    return "".join(pieces)


def wrap_bare_frac_commands(segment: str) -> str:
    """Wrap bare \frac commands, allowing nested braces in numerator/denominator."""
    def suspicious_fraction_args(num: str, den: str) -> bool:
        joined = num + " " + den
        if len(joined) < 180:
            return False
        return bool(re.search(r"[¥★■【】]|[\u3400-\u9fff]|\\text\{[^{}\n]{20,}\}", joined))

    def parse_group(value: str, pos: int):
        while pos < len(value) and value[pos].isspace():
            pos += 1
        if pos >= len(value) or value[pos] != "{":
            return None
        depth = 0
        start = pos + 1
        i = pos
        while i < len(value):
            char = value[i]
            if char == "\\":
                i += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return value[start:i], i + 1
            i += 1
        return None

    pieces: list[str] = []
    cursor = 0
    while True:
        idx = segment.find(r"\frac", cursor)
        if idx == -1:
            pieces.append(segment[cursor:])
            break
        pieces.append(segment[cursor:idx])
        pos = idx + len(r"\frac")
        numerator = parse_group(segment, pos)
        if not numerator:
            pieces.append(r"\frac")
            cursor = pos
            continue
        denominator = parse_group(segment, numerator[1])
        if not denominator:
            pieces.append(segment[idx:numerator[1]])
            cursor = numerator[1]
            continue
        num = re.sub(r"(?<!\\)\$", "", numerator[0])
        den = re.sub(r"(?<!\\)\$", "", denominator[0])
        if suspicious_fraction_args(num, den):
            pieces.append(r"\ensuremath{\frac{\square}{\square}}")
            cursor = denominator[1]
            continue
        pieces.append(r"\ensuremath{\frac{" + num + "}{" + den + "}}")
        cursor = denominator[1]
    return "".join(pieces)


def apply_outside_dollar_math(text: str, callback: Callable[[str], str]) -> str:
    """Apply a callback to text outside unescaped single-dollar math spans."""
    out: list[str] = []
    for raw_line in text.splitlines(keepends=True):
        newline = ""
        line = raw_line
        if raw_line.endswith("\r\n"):
            line, newline = raw_line[:-2], "\r\n"
        elif raw_line.endswith("\n"):
            line, newline = raw_line[:-1], "\n"

        pieces: list[str] = []
        cursor = 0
        in_math = False
        for dollar in re.finditer(r"(?<!\\)\$", line):
            segment = line[cursor : dollar.start()]
            pieces.append(segment if in_math else callback(segment))
            pieces.append("$")
            in_math = not in_math
            cursor = dollar.end()
        tail = line[cursor:]
        pieces.append(tail if in_math else callback(tail))
        out.append("".join(pieces) + newline)
    return "".join(out)


def strip_nested_inline_math_delimiters(text: str) -> str:
    """Remove \(...\) delimiters and caret-wrapped logic symbols inside inline math/text."""
    text = re.sub(r"\(\^\{\\ensuremath\{\\([A-Za-z]+)\}\}", r"(\\ensuremath{\\\1}", text)
    pattern = re.compile(r"(?<![\\$])\$(?!\$)(?P<body>[^$\n]+)(?<![\\$])\$(?!\$)")

    def repl(match: re.Match[str]) -> str:
        body = match.group("body").replace(r"\(", "").replace(r"\)", "")
        return f"${body}$"

    return pattern.sub(repl, text)


def wrap_math_dense_text_lines(text: str) -> str:
    """Wrap standalone math-dense answer or working lines left in text mode."""
    out: list[str] = []
    in_display = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "$$":
            in_display = not in_display
            out.append(line)
            continue
        if in_display or structural_text_line(stripped) or "$" in stripped:
            out.append(line)
            continue
        if (
            line_math_like(stripped)
            and not display_body_line_is_prose(stripped)
            and re.search(r"\\frac|[<>]=?|\\ensuremath\{\\(?:div|times|cdot|therefore)\}", stripped)
        ):
            leading = line[: len(line) - len(line.lstrip())]
            trailing = line[len(line.rstrip()) :]
            out.append(f"{leading}${stripped}${trailing}")
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def remove_trailing_prose_dollar_lines(text: str) -> str:
    """Drop a final orphan dollar when it trails a prose-only standards/reference phrase."""
    out: list[str] = []
    for line in text.splitlines():
        dollars = list(re.finditer(r"(?<!\\)\$", line))
        if len(dollars) % 2 == 1 and dollars and not line[dollars[-1].end() :].strip():
            start = dollars[-2].end() if len(dollars) >= 2 else 0
            segment = line[start : dollars[-1].start()]
            if display_body_line_is_prose(segment) and not re.search(r"\\(?:frac|sqrt|times|div|cdot|in|notin)|[=<>^_]", segment):
                line = line[: dollars[-1].start()] + line[dollars[-1].end() :]
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_orphan_degree_math_closers(text: str) -> str:
    """Turn angle expressions ending in a lone $ into balanced inline math."""
    angle_tail_re = re.compile(r"(?P<expr>\d+\^\{\\circ\}(?:\s*[-+]\s*\d+\^\{\\circ\})*)\s*$")
    out: list[str] = []
    for line in text.splitlines():
        pieces: list[str] = []
        cursor = 0
        in_math = False
        for dollar in re.finditer(r"(?<!\\)\$", line):
            segment = line[cursor : dollar.start()]
            if in_math:
                pieces.append(segment + "$")
                in_math = False
            else:
                match = angle_tail_re.search(segment)
                if match:
                    pieces.append(segment[: match.start("expr")] + "$" + match.group("expr") + "$")
                else:
                    pieces.append(segment + "$")
                    in_math = True
            cursor = dollar.end()
        pieces.append(line[cursor:])
        out.append("".join(pieces))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_orphan_power_math_closers(text: str) -> str:
    """Turn power expressions ending in a lone $ into balanced inline math."""
    power_tail_re = re.compile(
        r"(?P<expr>(?:\d+(?:\.\d+)?|[A-Za-z])\s*(?:\^\{[^{}\n]+\}|_\{[^{}\n]+\})(?:\s*[+\-=]\s*(?:\d+(?:\.\d+)?|[A-Za-z])?\s*(?:\^\{[^{}\n]+\}|_\{[^{}\n]+\}))*)\s*$"
    )
    out: list[str] = []
    for line in text.splitlines():
        pieces: list[str] = []
        cursor = 0
        in_math = False
        for dollar in re.finditer(r"(?<!\\)\$", line):
            segment = line[cursor : dollar.start()]
            if in_math:
                pieces.append(segment + "$")
                in_math = False
            else:
                match = power_tail_re.search(segment)
                if match:
                    pieces.append(segment[: match.start("expr")] + "$" + match.group("expr") + "$")
                else:
                    pieces.append(segment + "$")
                    in_math = True
            cursor = dollar.end()
        pieces.append(line[cursor:])
        out.append("".join(pieces))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def restore_escaped_latex_array_dumps(text: str) -> str:
    """Recover math arrays that were escaped after an HTML table split an inline image."""
    pattern = re.compile(
        r"\\\$\\textbackslash\{\}begin\\\{array\\\}.*?"
        r"\\textbackslash\{\}end\\\{array\\\}\\\$",
        re.S,
    )

    def repl(match: re.Match[str]) -> str:
        restored = match.group(0)
        restored = restored.replace(r"\$", "$")
        restored = restored.replace(r"\textbackslash{}", "\\")
        restored = restored.replace(r"\{", "{").replace(r"\}", "}")
        restored = re.sub(
            r"\\text\s*\{\s*(\\includegraphics(?:\[[^\]]*\])?\{[^{}]+\})\s*\}",
            lambda image: rf"\vcenter{{\hbox{{{image.group(1)}}}}}",
            restored,
        )
        return restored

    return pattern.sub(repl, text)


def restore_escaped_open_math_delimiters(text: str) -> str:
    """Turn OCR-escaped math openings like \$2 \times 3$ back into math spans."""
    pattern = re.compile(r"\\\$([^$\n]{1,500}?)\$")

    def math_like(value: str) -> bool:
        return bool(re.search(r"\\[A-Za-z]+|[<>=^{}]|[+\-*/]|\\times|\\cdot|\\div", value))

    def repl(match: re.Match[str]) -> str:
        inner = match.group(1)
        if math_like(inner):
            return f"${inner}$"
        return match.group(0)

    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(repl, text)
    return text


def restore_fully_escaped_math_delimiters(text: str) -> str:
    """Turn OCR-escaped math spans like \$0 \cdot (-2)\$ back into math."""
    pattern = re.compile(r"\\\$([^$\n]{1,500}?)\\\$")

    def should_restore(inner: str) -> bool:
        return line_math_like(inner) and bool(re.search(r"\\[A-Za-z]+|[+\-*/^_{}×÷]", inner))

    def repl(match: re.Match[str]) -> str:
        inner = match.group(1)
        if should_restore(inner):
            return f"${inner}$"
        return match.group(0)

    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(repl, text)
    return text


def restore_escaped_close_math_delimiters(text: str) -> str:
    """Turn half-restored math spans like $0 \cdot (-2)\$ into normal math."""
    pattern = re.compile(r"(?<!\\)\$([^$\n]{1,500}?)\\\$")

    def should_restore(inner: str) -> bool:
        return line_math_like(inner) and bool(re.search(r"\\[A-Za-z]+|[+\-*/^_{}×÷]", inner))

    def repl(match: re.Match[str]) -> str:
        inner = match.group(1)
        if should_restore(inner):
            return f"${inner}$"
        return match.group(0)

    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(repl, text)
    return text


def strip_dollars_inside_text_commands(text: str) -> str:
    """Remove OCR math delimiters that were accidentally left inside \text{...}."""
    pattern = re.compile(r"\\text\s*\{\s*(?P<body>(?:[^{}]|\{[^{}]*\})*)\s*\}")

    def repl(match: re.Match[str]) -> str:
        body = match.group("body")
        if "$" not in body:
            return match.group(0)
        body = re.sub(r"(?<!\\)\$(?=\s*\d)", r"\\$", body)
        body = re.sub(r"(?<!\\)\$", "", body)
        return rf"\text{{{body.strip()}}}"

    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(repl, text)
    return text


def remove_spurious_numeric_closing_dollars(text: str) -> str:
    """Drop numeric trailing dollars only when a line is already dollar-unbalanced."""
    out: list[str] = []
    row_tail_re = re.compile(r"\s*(?:\\\\\s*(?:\\hline)?|\\hline|&)?\s*$")
    for line in text.splitlines():
        dollars = list(re.finditer(r"(?<!\\)\$", line))
        if len(dollars) % 2 == 0:
            out.append(line)
            continue
        repaired = line
        for idx in range(len(dollars) - 1, -1, -1):
            dollar = dollars[idx]
            if dollar.start() == 0 or line[dollar.start() - 1] not in "0123456789.":
                continue
            if not row_tail_re.match(line[dollar.end() :]):
                continue
            previous = dollars[idx - 1].end() if idx > 0 else 0
            segment = line[previous : dollar.start()]
            if line_math_like(segment):
                continue
            repaired = line[: dollar.start()] + line[dollar.end() :]
            break
        out.append(repaired)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_text_fraction_denominator_closures(text: str) -> str:
    """Close common OCR-broken \frac denominators made from simple \text{...} groups."""
    text_group = r"\\text\s*\{[^{}\n]+\}"
    numerator_group = rf"(?:[^{{}}\n]|{text_group})+"
    text = re.sub(
        rf"(\\frac\s*\{{{numerator_group}\}}\s*\{{{text_group})(?=\\right\b)",
        r"\1}",
        text,
    )
    text = re.sub(
        rf"(\\frac\s*\{{{text_group}\}}\s*\{{{text_group})(?=\\quad\s*\\[A-Za-z]+\b)",
        r"\1}",
        text,
    )
    text = re.sub(
        rf"(\\frac\s*\{{{text_group}\}}\s*\{{{text_group})(?=\\end\{{array\}}|\$|\\\\|&)",
        r"\1}",
        text,
    )
    extra_right_pattern = (
        r"(\\frac\s*\{" + numerator_group + r"\}\s*\{" + text_group + r"\}\\right[)\]\}])\}"
    )
    text = re.sub(extra_right_pattern, r"\1", text)
    return text


def close_unbalanced_inline_math_in_table_rows(text: str) -> str:
    """Close a table-cell math span before the row break when OCR dropped the final dollar."""
    out: list[str] = []
    row_end_re = re.compile(r"\s*\\\\\s*(?:\\hline)?\s*$")
    for line in text.splitlines():
        if "$" in line and r"\\" in line:
            dollars = list(re.finditer(r"(?<!\\)\$", line))
            row_end = row_end_re.search(line)
            if row_end and len(dollars) % 2 == 1 and dollars[-1].start() < row_end.start():
                line = line[: row_end.start()] + "$" + line[row_end.start() :]
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def line_math_like(value: str) -> bool:
    cleaned = re.sub(r"\\\([^$\n]*?\\\)", "", value)
    return bool(
        re.search(
            r"\\[A-Za-z]+|[<>=^{}]|[+\-*/]|×|÷|≤|≥|≠|∧|∨|→|↔|⊕|⊆|∈|∀|\\times|\\cdot|\\div|_",
            cleaned,
        )
    )


def repair_unbalanced_dollar_lines(text: str) -> str:
    """Repair single-line OCR dollar imbalance without treating currency as math."""
    out: list[str] = []
    for line in text.splitlines():
        dollars = list(re.finditer(r"(?<!\\)\$", line))
        if len(dollars) % 2 == 1 and "$$" not in line:
            last = dollars[-1]
            tail = line[last.end() :]
            between_last_pair = line[dollars[-2].end() : last.start()] if len(dollars) >= 2 else ""
            plain_between = re.sub(r"\\[{}]", "", between_last_pair)
            if (
                len(dollars) >= 3
                and not tail.strip()
                and between_last_pair.strip()
                and (not line_math_like(plain_between) or r"\$" in line)
            ):
                line = line[: last.start()] + line[last.end() :]
            elif len(dollars) >= 3 and not tail.strip() and line_math_like(plain_between):
                insert_at = dollars[-2].end()
                line = line[:insert_at] + "$" + line[insert_at:]
            elif re.match(r"\s*\d", tail) and (r"\$" in tail or not line_math_like(tail)):
                line = line[: last.start()] + r"\$" + line[last.end() :]
            elif line_math_like(tail):
                punct = re.search(r"([,.;:])\s*$", line)
                insert_at = punct.start(1) if punct else len(line)
                if punct and line[:insert_at].endswith((r"\right", r"\left")):
                    insert_at = len(line)
                line = line[:insert_at] + "$" + line[insert_at:]
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_table_cell_dollar_boundaries(text: str) -> str:
    """Keep inline math and currency dollars from leaking across tabular cells."""
    out: list[str] = []
    for line in text.splitlines():
        if "&" in line and r"\$" in line and re.search(r"\\hline\$", line):
            line = re.sub(r"(\\hline)\$(?=\s*$)", r"\1", line)
        if "&" not in line or "$" not in line or r"\begin{array}" in line:
            out.append(line)
            continue
        cells = line.split("&")
        repaired_cells: list[str] = []
        for cell in cells:
            dollars = list(re.finditer(r"(?<!\\)\$", cell))
            if len(dollars) % 2 == 1:
                last = dollars[-1]
                tail = cell[last.end() :]
                if not tail.strip() and len(dollars) >= 3:
                    cell = cell[: last.start()] + cell[last.end() :]
                elif re.match(r"\s*\d", tail) and (r"\$" in tail or not line_math_like(tail)):
                    cell = cell[: last.start()] + r"\$" + cell[last.end() :]
                elif line_math_like(tail):
                    cell = cell + "$"
            repaired_cells.append(cell)
        out.append("&".join(repaired_cells))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_matrix_digit_rowbreaks(text: str) -> str:
    """Restore OCR-collapsed row breaks such as \begin{pmatrix}7\0\end{pmatrix}."""
    pattern = re.compile(
        r"\\begin\{(?P<env>p?matrix|bmatrix|vmatrix|Vmatrix|array)\}"
        r"(?P<spec>\{[^{}\n]*\})?"
        r"(?P<body>.*?)"
        r"\\end\{(?P=env)\}",
        re.S,
    )

    def repl(match: re.Match[str]) -> str:
        body = re.sub(r"(?<!\\)\\(?=\d)", r"\\\\", match.group("body"))
        return rf"\begin{{{match.group('env')}}}{match.group('spec') or ''}{body}\end{{{match.group('env')}}}"

    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(repl, text)
    return text


def close_superscript_group_before_relation(text: str) -> str:
    """Close OCR-broken exponent groups before the following equality/relation."""
    return re.sub(
        r"\^\s*\{(?P<body>\\left\([^\n]*?\\right\))\s*(?P<rel>=|<|>|\\leq|\\geq|\\neq)",
        lambda match: rf"^{{{match.group('body')}}} {match.group('rel')}",
        text,
    )


def repair_parenthesized_fraction_denominators(text: str) -> str:
    """Repair \frac{(a/b){(c/d)} patterns where the denominator brace was lost."""
    paren_frac = r"\\left\s*\(\\frac\s*\{[^{}\n]+\}\s*\{[^{}\n]+\}\\right\s*\)"
    pattern = re.compile(
        rf"\\frac\s*\{{\s*(?P<num>{paren_frac})\s*\{{\s*(?P<den>{paren_frac})"
    )

    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(lambda match: rf"\frac{{{match.group('num')}}}{{{match.group('den')}}}", text)
    return text


def remove_orphan_brace_after_row_equal(text: str) -> str:
    """Drop OCR braces after row-start equals only when not inside another group."""
    out: list[str] = []
    for line in text.splitlines():
        repaired: list[str] = []
        balance = 0
        index = 0
        while index < len(line):
            if line.startswith(r"\\", index):
                match = re.match(r"\\\\\s*=\}", line[index:])
                if match and balance == 0:
                    repaired.append(line[index : index + match.end() - 1])
                    index += match.end()
                    continue
                repaired.append(r"\\")
                index += 2
                continue
            char = line[index]
            escaped = index > 0 and line[index - 1] == "\\"
            if not escaped and char == "{":
                balance += 1
            elif not escaped and char == "}":
                balance -= 1
            repaired.append(char)
            index += 1
        out.append("".join(repaired))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_frac_boxed_missing_numerator_close(text: str) -> str:
    pattern = re.compile(
        r"\\frac\s*\{\s*(?P<num>\\boxed\s*\{[^{}\n]*\})\s*\{"
        r"(?P<den>\\boxed\s*\{[^{}\n]*\}|[^{}$\s\\&]+)(?:\})?"
    )

    def repl(match: re.Match[str]) -> str:
        return rf"\frac{{{match.group('num')}}}{{{match.group('den').strip()}}}"

    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(repl, text)
    return text


def find_balanced_group_end(text: str, open_index: int) -> int | None:
    if open_index >= len(text) or text[open_index] != "{":
        return None
    depth = 0
    index = open_index
    while index < len(text):
        char = text[index]
        escaped = index > 0 and text[index - 1] == "\\"
        if not escaped and char == "{":
            depth += 1
        elif not escaped and char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def repair_missing_frac_denominators(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        found = text.find(r"\frac", index)
        if found == -1:
            out.append(text[index:])
            break
        if found + 5 < len(text) and text[found + 5].isalpha():
            out.append(text[index : found + 5])
            index = found + 5
            continue
        out.append(text[index:found])
        cursor = found + len(r"\frac")
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text) or text[cursor] != "{":
            out.append(text[found:cursor])
            index = cursor
            continue
        first_end = find_balanced_group_end(text, cursor)
        if first_end is None:
            out.append(text[found:])
            break
        check = first_end
        while check < len(text) and text[check].isspace():
            check += 1
        out.append(text[found:first_end])
        if check >= len(text) or text[check] != "{":
            out.append(r"{\square}")
        index = first_end
    return "".join(out)


TEXT_MODE_MATH_COMMAND_RE = re.compile(
    r"\\(?:rightarrow|leftarrow|longrightarrow|longleftarrow|rightleftharpoons|"
    r"leftrightarrow|longleftrightarrow|nabla|Delta|delta|times|cdot|pm|mp|"
    r"leqslant|geqslant|leq|geq|neq|approx|sim|infty|therefore|because|to)\b"
)


def normalize_math_text_commands(text: str) -> str:
    """Keep math-mode text fragments from swallowing math commands."""
    circled_math = {
        r"\times": r"\mathbin{\otimes}",
        r"\cdot": r"\mathbin{\odot}",
        "+": r"\mathbin{\oplus}",
        "-": r"\mathbin{\ominus}",
    }
    for inner, replacement in circled_math.items():
        text = text.replace(rf"\textcircled{{{inner}}}", replacement)

    text_command_re = re.compile(r"\\text\s*\{(?P<body>[^{}]*" + TEXT_MODE_MATH_COMMAND_RE.pattern + r"[^{}]*)\}")

    def repl(match: re.Match[str]) -> str:
        body = match.group("body")
        parts = TEXT_MODE_MATH_COMMAND_RE.split(body)
        commands = TEXT_MODE_MATH_COMMAND_RE.findall(body)
        out: list[str] = []
        for idx, part in enumerate(parts):
            part = re.sub(r"\s+", " ", part).strip()
            if part:
                out.append(rf"\text{{{part}}}")
            if idx < len(commands):
                out.append(commands[idx])
        return " ".join(out) if out else match.group(0)

    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\\(mathrm|mathbf|mathit|mathbb|mathfrak)([A-Za-z])\b", r"\\\1{\2}", text)
        text = text_command_re.sub(repl, text)
        text = split_text_wrapped_math_commands(text)
        text = split_text_wrapped_script_fragments(text)
    return text


def normalize_unicode_math_symbols(text: str) -> str:
    """Replace Unicode math symbols with LaTeX commands that work in text or math mode."""
    replacements = {
        "∧": r"\ensuremath{\wedge}",
        "∨": r"\ensuremath{\vee}",
        "→": r"\ensuremath{\rightarrow}",
        "↔": r"\ensuremath{\leftrightarrow}",
        "⊕": r"\ensuremath{\oplus}",
        "⊆": r"\ensuremath{\subseteq}",
        "⊂": r"\ensuremath{\subset}",
        "∈": r"\ensuremath{\in}",
        "∉": r"\ensuremath{\notin}",
        "∪": r"\ensuremath{\cup}",
        "∩": r"\ensuremath{\cap}",
        "∅": r"\ensuremath{\varnothing}",
        "∝": r"\ensuremath{\propto}",
        "●": r"\ensuremath{\bullet}",
        "Ø": r"\ensuremath{\varnothing}",
        "∀": r"\ensuremath{\forall}",
        "≡": r"\ensuremath{\equiv}",
        "≤": r"\ensuremath{\leq}",
        "≥": r"\ensuremath{\geq}",
        "≠": r"\ensuremath{\neq}",
        "×": r"\ensuremath{\times}",
        "÷": r"\ensuremath{\div}",
        "∴": r"\ensuremath{\therefore}",
        "∵": r"\ensuremath{\because}",
        "∠": r"\ensuremath{\angle}",
        "△": r"\ensuremath{\triangle}",
        "Λ": r"\ensuremath{\land}",
    }
    for symbol, replacement in replacements.items():
        text = text.replace(symbol, replacement)
    for command in (
        "wedge",
        "vee",
        "land",
        "lor",
        "oplus",
        "equiv",
        "times",
        "div",
        "cdot",
        "pm",
        "leq",
        "geq",
        "leqslant",
        "geqslant",
        "neq",
        "approx",
        "sim",
        "in",
        "notin",
        "therefore",
        "rightarrow",
        "leftarrow",
        "longrightarrow",
        "longleftarrow",
        "leftrightarrow",
        "Rightarrow",
        "Leftarrow",
        "to",
        "mapsto",
    ):
        text = re.sub(
            rf"(?<!\\ensuremath\{{)\\{command}\b",
            rf"\\ensuremath{{\\{command}}}",
            text,
        )
    return text


def normalize_locked_body_unicode(text: str) -> str:
    """Final body-only Unicode normalization compatible with the locked preamble."""
    text = normalize_unicode_math_symbols(text)
    for symbol, replacement in TEXT_COMMAND_SYMBOLS.items():
        text = text.replace(symbol, replacement)
    text = text.replace("°", r"\ensuremath{{}^{\circ}}")
    superscript_digits = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")

    def superscript(match: re.Match[str]) -> str:
        return rf"\ensuremath{{^{{{match.group(0).translate(superscript_digits)}}}}}"

    return re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+", superscript, text)


def wrap_ipa_runs(text: str) -> str:
    """Use an installed IPA face inline without changing the locked preamble."""
    pattern = re.compile(r"[\u0250-\u02ff]+")
    return pattern.sub(lambda match: rf"{{\fontspec{{Charis SIL}}{match.group(0)}}}", text)


def split_text_wrapped_math_commands(text: str) -> str:
    """Move simple math commands out of \text{...} arguments."""
    pattern = re.compile(
        r"\\text\s*\{"
        r"(?P<prefix>[^{}]*)"
        r"\\(?P<cmd>mathrm|mathbf|mathit|mathbb|mathfrak)\{(?P<body>[^{}]+)\}"
        r"\s*(?P<script>[_^]\s*(?:\{[^{}]+\}|[A-Za-z0-9+\-]+))?"
        r"(?P<suffix>[^{}]*)"
        r"\}"
    )

    def normalize_script(script: str | None) -> str:
        if not script:
            return ""
        script = re.sub(r"\s+", "", script)
        if len(script) >= 2 and script[1] != "{":
            return script[0] + "{" + script[1:] + "}"
        return script

    def repl(match: re.Match[str]) -> str:
        pieces: list[str] = []
        prefix = re.sub(r"\s+", " ", match.group("prefix")).strip()
        suffix = re.sub(r"\s+", " ", match.group("suffix")).strip()
        if prefix:
            pieces.append(rf"\text{{{prefix}}}")
        pieces.append(rf"\{match.group('cmd')}{{{match.group('body')}}}{normalize_script(match.group('script'))}")
        if suffix:
            pieces.append(rf"\text{{{suffix}}}")
        return " ".join(pieces)

    return pattern.sub(repl, text)


TEXT_SCRIPT_FRAGMENT_RE = re.compile(
    r"(?<![\\A-Za-z])"
    r"(?P<math>(?:\\?[A-Za-z]+|[A-Za-z0-9]+)"
    r"\s*(?:[_^]\s*(?:\{[^{}]+\}|[A-Za-z0-9+\-]+))+"
    r"[A-Za-z0-9().]*)"
)

TEXT_MATH_FRAGMENT_RE = re.compile(
    r"(?P<math>"
    r"\\sqrt\s*(?:\[[^\]\n]+\])?\s*\{[^{}\n]+\}"
    r"|\\frac\s*\{[^{}\n]+\}\s*\{[^{}\n]+\}"
    r"|\\binom\s*\{[^{}\n]+\}\s*\{[^{}\n]+\}"
    r"|\\(?:sum|prod|int)\s*(?:[_^]\s*(?:\{[^{}\n]+\}|[A-Za-z0-9+\-=]+))*"
    r"|\\(?:mathbb|mathrm|mathbf|mathit)\s*\{[^{}\n]+\}"
    r"|\\[A-Za-z]+(?:\s*\{[^{}\n]+\})?\s*(?:[_^]\s*(?:\{[^{}\n]+\}|[A-Za-z0-9+\-=]+))*"
    r"|(?:\\?[A-Za-z]+|[A-Za-z0-9]+)\s*(?:[_^]\s*(?:\{[^{}]+\}|[A-Za-z0-9+\-]+))+[A-Za-z0-9().]*"
    r")"
)


def split_text_wrapped_script_fragments(text: str) -> str:
    """Move OCR math snippets like e^{x}, H_2O, or \in out of \text{...}."""
    pattern = re.compile(r"\\text\s*\{\s*(?P<body>(?:[^{}]|\{[^{}]*\})*)\s*\}")

    def repl(match: re.Match[str]) -> str:
        body = match.group("body")
        if not (TEXT_SCRIPT_FRAGMENT_RE.search(body) or TEXT_MATH_FRAGMENT_RE.search(body)):
            return match.group(0)
        pieces: list[str] = []
        cursor = 0
        for frag in TEXT_MATH_FRAGMENT_RE.finditer(body):
            prefix = re.sub(r"\s+", " ", body[cursor : frag.start()]).strip()
            if prefix:
                pieces.append(rf"\text{{{prefix}}}")
            pieces.append(re.sub(r"\s+", "", frag.group("math")))
            cursor = frag.end()
        suffix = re.sub(r"\s+", " ", body[cursor:]).strip()
        if suffix:
            pieces.append(rf"\text{{{suffix}}}")
        return " ".join(pieces) if pieces else match.group(0)

    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(repl, text)
    return text


def latex_brace_balance(value: str) -> int:
    balance = 0
    for index, char in enumerate(value):
        escaped = index > 0 and value[index - 1] == "\\"
        if escaped:
            continue
        if char == "{":
            balance += 1
        elif char == "}":
            balance -= 1
    return balance


def balance_unescaped_latex_braces(value: str) -> str:
    balance = latex_brace_balance(value)
    if 0 < balance <= 8:
        value += "}" * balance
    return value


def wrap_cjk_text_in_math(value: str) -> str:
    placeholders: list[str] = []

    def protect(match: re.Match[str]) -> str:
        body = match.group("body")
        original = match.group(0)
        if re.search(r"[\u3400-\u9fff]", body):
            original = rf"\text{{\CJKfamily{{zhsong}}{body}}}"
        placeholders.append(original)
        return f"@@TEXTCMD{len(placeholders) - 1}@@"

    value = re.sub(r"\\text\s*\{(?P<body>[^{}\n]*)\}", protect, value)
    value = re.sub(r"([\u3400-\u9fff]+)", r"\\text{\\CJKfamily{zhsong}\1}", value)
    value = value.replace("¥", r"\text{YEN}")
    for idx, original in enumerate(placeholders):
        value = value.replace(f"@@TEXTCMD{idx}@@", original)
    return value


def wrap_cjk_text_in_final_inline_math(text: str) -> str:
    """Apply CJK text protection after late inline-math scaling and repairs."""
    pattern = re.compile(r"(?<![\\$])\$(?!\$)(?P<body>[^$\n]+)(?<![\\$])\$(?!\$)")
    return pattern.sub(lambda match: "$" + wrap_cjk_text_in_math(match.group("body")) + "$", text)


def wrap_cjk_text_in_final_arrays(text: str) -> str:
    """Use the locked template CJK family for text fragments inside math arrays."""
    pattern = re.compile(r"\\begin\{array\}\{[^{}\n]*\}(?P<body>.*?)\\end\{array\}", re.S)

    def repair(match: re.Match[str]) -> str:
        body = wrap_cjk_text_in_math(match.group("body"))
        return match.group(0).replace(match.group("body"), body, 1)

    return pattern.sub(repair, text)


def drop_unmatched_closing_braces_in_inline_math(text: str) -> str:
    """Remove only closing braces that have no opener inside a final inline span."""
    pattern = re.compile(r"(?<![\\$])\$(?!\$)(?P<body>[^$\n]+)(?<![\\$])\$(?!\$)")

    def repair(match: re.Match[str]) -> str:
        body = match.group("body")
        depth = 0
        out: list[str] = []
        for idx, char in enumerate(body):
            backslashes = 0
            cursor = idx - 1
            while cursor >= 0 and body[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            escaped = backslashes % 2 == 1
            if char == "{" and not escaped:
                depth += 1
            elif char == "}" and not escaped:
                if depth == 0:
                    continue
                depth -= 1
            out.append(char)
        return "$" + "".join(out) + "$"

    return pattern.sub(repair, text)


def repair_orphan_relation_superscripts_final(text: str) -> str:
    """Restore relation commands emitted as superscripts of an empty base."""
    text = re.sub(r"\^\{\s*\^\s*\}", r"^{\\wedge}", text)
    text = re.sub(r"\\overline\{\s*\^\s*\}", r"\\overline{\\wedge}", text)
    text = re.sub(r"\\(wedge|vee)([A-Za-z])\b", r"\\\1 \2", text)
    text = re.sub(r"(?<!\\)\^(?=\s*\$)", r"\\wedge", text)
    text = re.sub(r"\^\s*\\\s*(\d)", r"^{\1}", text)
    return re.sub(
        r"\{\}\s*\^\\(notin|neq|in|leq|geq|leqslant|geqslant)",
        r"\\\1",
        text,
    )


def normalize_inline_math_spans(text: str) -> str:
    pattern = re.compile(r"(?<![\\$])\$(?!\$)(?P<body>[^$\n]+)(?<![\\$])\$(?!\$)")

    def repl(match: re.Match[str]) -> str:
        body = match.group("body")
        body = body.replace(r"\(", "").replace(r"\)", "")
        body = re.sub(r"(?<!\\)(?<=\d)%", r"\\%", body)
        has_math_environment = bool(
            re.search(r"\\begin\{(?:p?matrix|bmatrix|vmatrix|Vmatrix|array|aligned|alignedat|gathered|split)\}", body)
        )
        if re.search(r"\\begin\{(?:p?matrix|bmatrix|vmatrix|Vmatrix|array)\}", body):
            body = repair_matrix_digit_rowbreaks(body)
        else:
            body = re.sub(r"\\(?=\d)", "", body)
        body = re.sub(r"\{\}\s*\^\\(notin|neq|in|leq|geq|leqslant|geqslant)", r"\\\1", body)
        body = re.sub(r"(?<![A-Za-z0-9}\]])\^\s*(?==)", "", body)
        body = re.sub(r"(?<=-)\s*\^\s*(?=\\(?:in|notin)\b)", "", body)
        body = re.sub(r"(?<=[+\-])\s*\^(?=[}\),|])", r"\\wedge", body)
        body = re.sub(r"(?<=[+\-])\s*\^\s*$", r"\\wedge", body)
        body = re.sub(r"(?<!\\)\^\s*$", r"\\wedge", body)
        body = re.sub(r"\\frac\s*\{\s*_\s*\}", r"\\frac{\\square}", body)
        body = repair_text_fraction_denominator_closures(body)
        body = repair_frac_boxed_missing_numerator_close(body)
        body = repair_missing_frac_denominators(body)
        body = re.sub(r"([A-Za-z0-9)\]}])\\([A-Z])\b", r"\1\\backslash \2", body)
        body = re.sub(r"(\\[A-Za-z]+)\s*\^\s*$", r"\1", body)
        body = re.sub(r"(\\[A-Za-z]+)\s*_\s*$", r"\1", body)
        body = body.replace(r"\hat{}", r"\widehat{\ }")
        body = re.sub(r"\\hat(?=\s*[\)\]\},=|])", r"\\widehat{\\ }", body)
        body = wrap_cjk_text_in_math(body)
        if r"\\" in body and not has_math_environment:
            body = rf"\begin{{array}}{{l}}{body}\end{{array}}"
        body = balance_unescaped_latex_braces(body)
        return f"${body}$"

    return pattern.sub(repl, text)


def remove_source_page_numbers_before_chapters(text: str) -> str:
    """Drop OCR/source printed page numbers that would become standalone pages."""
    lines = text.splitlines()
    remove: set[int] = set()
    chapter_re = re.compile(r"^\\chapter\{")
    page_number_re = re.compile(r"^\d{1,4}$")

    for idx, line in enumerate(lines):
        if not page_number_re.match(line.strip()):
            continue

        prev_idx = idx - 1
        while prev_idx >= 0 and not lines[prev_idx].strip():
            prev_idx -= 1
        next_idx = idx + 1
        while next_idx < len(lines) and not lines[next_idx].strip():
            next_idx += 1

        if (
            prev_idx >= 0
            and lines[prev_idx].strip().startswith("% source_page_idx:")
            and next_idx < len(lines)
            and chapter_re.match(lines[next_idx].strip())
        ):
            remove.add(idx)

    return "\n".join(line for idx, line in enumerate(lines) if idx not in remove) + "\n"


def normalize_array_column_counts(text: str) -> str:
    """Widen simple l/c/r array specs when OCR inserted more alignment cells."""
    pattern = re.compile(
        r"\\begin\{array\}\{(?P<spec>[lcr|\s]+)\}(?P<body>(?:(?!\\begin\{array\}|\\end\{array\}).)*?)\\end\{array\}",
        re.S,
    )

    def count_unescaped_ampersands(row: str) -> int:
        return len(re.findall(r"(?<!\\)&", row))

    def repl(match: re.Match[str]) -> str:
        spec = match.group("spec")
        body = match.group("body")
        align_chars = re.findall(r"[lcr]", spec)
        if not align_chars:
            return match.group(0)
        max_columns = max([count_unescaped_ampersands(row) + 1 for row in re.split(r"(?<!\\)\\\\", body)] or [1])
        if max_columns <= len(align_chars):
            return match.group(0)
        fill = align_chars[-1]
        widened = " ".join(align_chars + [fill] * (max_columns - len(align_chars)))
        return rf"\begin{{array}}{{{widened}}}{body}\end{{array}}"

    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(repl, text)
    return text


def normalize_nested_outer_array_columns(text: str) -> str:
    """Widen a single-line outer array using only its top-level alignment cells."""
    begin_re = re.compile(r"\\begin\{array\}\{(?P<spec>[lcr|\s]+)\}")
    out: list[str] = []
    for line in text.splitlines():
        outer = begin_re.search(line)
        if not outer or r"\begin{array}" not in line[outer.end():]:
            out.append(line)
            continue
        depth = 1
        columns = 1
        max_columns = 1
        cursor = outer.end()
        while cursor < len(line) and depth:
            nested = begin_re.match(line, cursor)
            if nested:
                depth += 1
                cursor = nested.end()
                continue
            if line.startswith(r"\end{array}", cursor):
                depth -= 1
                cursor += len(r"\end{array}")
                continue
            if depth == 1 and line.startswith(r"\\", cursor):
                max_columns = max(max_columns, columns)
                columns = 1
                cursor += 2
                continue
            if depth == 1 and line[cursor] == "&" and (cursor == 0 or line[cursor - 1] != "\\"):
                columns += 1
            cursor += 1
        max_columns = max(max_columns, columns)
        align_chars = re.findall(r"[lcr]", outer.group("spec"))
        if align_chars and max_columns > len(align_chars):
            widened = " ".join(align_chars + [align_chars[-1]] * (max_columns - len(align_chars)))
            line = line[: outer.start("spec")] + widened + line[outer.end("spec") :]
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def drop_unmatched_closing_braces_in_arrays(text: str) -> str:
    """Drop stray } tokens inside array bodies while preserving balanced groups."""
    begin_re = re.compile(r"\\begin\{array\}\{[^{}\n]*\}")
    out: list[str] = []
    index = 0
    while index < len(text):
        match = begin_re.search(text, index)
        if not match:
            out.append(text[index:])
            break
        out.append(text[index : match.end()])
        index = match.end()
        depth = 1
        brace_balance = 0
        while index < len(text) and depth > 0:
            nested = begin_re.match(text, index)
            if nested:
                out.append(text[index : nested.end()])
                index = nested.end()
                depth += 1
                continue
            if text.startswith(r"\end{array}", index):
                out.append(r"\end{array}")
                index += len(r"\end{array}")
                depth -= 1
                continue
            if text.startswith(r"\\", index):
                out.append(r"\\")
                index += 2
                continue
            char = text[index]
            escaped = index > 0 and text[index - 1] == "\\"
            if not escaped and char == "{":
                brace_balance += 1
                out.append(char)
            elif not escaped and char == "}":
                if brace_balance > 0:
                    brace_balance -= 1
                    out.append(char)
            else:
                out.append(char)
            index += 1
    return "".join(out)


def drop_unmatched_closing_braces_on_single_line_arrays(text: str) -> str:
    """Remove unmatched closers from self-contained array lines only."""
    repaired: list[str] = []
    for line in text.splitlines():
        if r"\begin{array}" not in line or r"\end{array}" not in line:
            repaired.append(line)
            continue
        depth = 0
        out: list[str] = []
        for idx, char in enumerate(line):
            backslashes = 0
            cursor = idx - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            escaped = backslashes % 2 == 1
            if char == "{" and not escaped:
                depth += 1
            elif char == "}" and not escaped:
                if depth == 0:
                    continue
                depth -= 1
            out.append(char)
        repaired.append("".join(out))
    return "\n".join(repaired) + ("\n" if text.endswith("\n") else "")


def ensure_tabularx_row_terminators(text: str) -> str:
    """Add a missing outer row break when a table row ends with an inner math array."""
    lines = text.splitlines()
    out: list[str] = []
    in_tabularx = False
    begin_re = re.compile(r"\\begin\{tabularx\}")
    end_re = re.compile(r"\\end\{tabularx\}")
    row_end_re = re.compile(r"\\\\\s*(?:\\hline)?\s*$")

    for line in lines:
        stripped = line.strip()
        if begin_re.search(line):
            in_tabularx = True
            out.append(line)
            continue
        if in_tabularx and end_re.search(line):
            in_tabularx = False
            out.append(line)
            continue
        if (
            in_tabularx
            and "&" in line
            and not stripped.startswith("\\")
            and not row_end_re.search(stripped)
        ):
            line = line.rstrip() + r" \\ \hline"
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def split_exercise_material_out_of_tipboxes(text: str) -> str:
    """Keep Language tips boxes semantic; move following exercise lists outside."""
    pattern = re.compile(r"\\begin\{tipbox\}\n(?P<body>.*?)\n\\end\{tipbox\}", re.S)

    def repl(match: re.Match[str]) -> str:
        body = match.group("body")
        idx = body.find(r"\begin{enumerate}")
        if idx == -1:
            return match.group(0)
        before = body[:idx].rstrip()
        after = body[idx:].strip()
        if not before:
            return after
        return "\\begin{tipbox}\n" + before + "\n\\end{tipbox}\n\n" + after

    return pattern.sub(repl, text)


def restore_singleton_exercise_headings(text: str) -> str:
    """Turn singleton numbered list headings back into exercise heading bars."""

    def repl(match: re.Match[str]) -> str:
        counter = match.group("counter")
        title = re.sub(r"\s+", " ", match.group("title")).strip()
        number = int(counter) + 1 if counter is not None else 1
        prefix = ROMAN[number - 1] if 1 <= number <= len(ROMAN) else str(number)
        return f"\\exerciseheading{{{prefix}. {title}}}"

    pattern = re.compile(
        r"\\begin\{enumerate\}\n"
        r"(?:\\setcounter\{enumi\}\{(?P<counter>\d+)\}\n)?"
        r"\\item\n\s*(?P<title>(?:Translate the sentences according to the Chinese\.|"
        r"Translate the following expressions from English to Chinese\.))\n"
        r"\\end\{enumerate\}",
        re.S,
    )
    return pattern.sub(repl, text)


def convert_plain_exercise_headings(text: str) -> str:
    """Promote standalone roman exercise headings into consistent heading bars."""
    out: list[str] = []
    env_stack: list[str] = []
    begin_re = re.compile(r"\\begin\{([^{}]+)\}")
    end_re = re.compile(r"\\end\{([^{}]+)\}")
    heading_re = re.compile(r"^(I|II|III|IV|V|VI|VII|VIII|IX|X)\.\s+.+$")
    blocked_envs = {
        "enumerate",
        "itemize",
        "longtable",
        "tabular",
        "tabularx",
        "array",
        "infobox",
        "vocabbox",
        "tipbox",
        "tcolorbox",
    }

    for line in text.splitlines():
        stripped = line.strip()
        if not set(env_stack).intersection(blocked_envs):
            if heading_re.match(stripped) and not stripped.startswith("\\"):
                line = f"\\exerciseheading{{{stripped}}}"
        out.append(line)
        env_stack.extend(begin_re.findall(line))
        for env in end_re.findall(line):
            if env in env_stack[::-1]:
                idx = len(env_stack) - 1 - env_stack[::-1].index(env)
                env_stack.pop(idx)
    return "\n".join(out) + "\n"


READING_PROFILE_LIST_BEGIN = (
    r"\begin{description}[leftmargin=0pt,labelindent=0pt]"
)
INFOBOX_RE = re.compile(
    r"\\begin\{infobox\}\n\\begin\{(?P<env>itemize|description)\}"
    r"(?:\[[^\n]*\])?\n(?P<items>.*?)\\end\{(?P=env)\}\n\\end\{infobox\}",
    re.S,
)
CENTER_RE = re.compile(r"\\begin\{center\}\n(?P<body>.*?)\\end\{center\}", re.S)
IMAGE_INCLUDE_RE = re.compile(
    r"\\includegraphics(?:\[(?P<opts>[^\]]*)\])?\{(?:\\detokenize\{(?P<detok>[^{}]+)\}|(?P<path>[^{}]+))\}"
)
INCLUDE_RE = re.compile(
    r"\\includegraphics\[(?P<opts>[^\]]*)\]\{(?:\\detokenize\{(?P<detok>[^{}]+)\}|(?P<path>[^{}]+))\}"
)


def image_ref_from_match(match: re.Match[str]) -> str:
    return match.group("detok") or match.group("path")


def is_small_image_center(block: str) -> bool:
    match = CENTER_RE.fullmatch(block.strip())
    if not match:
        return False
    includes = list(INCLUDE_RE.finditer(match.group("body")))
    if not includes:
        return False
    return all(re.search(r"width\s*=\s*0\.(?:1[0-9]|2[0-2])\\textwidth", include.group("opts")) for include in includes)


def plain_metadata_items(line: str) -> list[str] | None:
    stripped = line.strip()
    tail = re.match(r"^(范畴|范围|教材链接)\s*[:：]?\s+(.+)$", stripped)
    if tail:
        label = "范围" if tail.group(1) == "范畴" else tail.group(1)
        return [rf"  \item[\textbf{{{label}}}] {tail.group(2).strip()}"]
    if not (stripped.startswith("语篇类型:") or stripped.startswith("语篇类型：")):
        return None
    normalized = stripped.replace("：", ":")
    pairs = re.findall(r"(语篇类型|词数|难度):\s*([^:]+?)(?=\s+(?:语篇类型|词数|难度):|$)", normalized)
    if not pairs:
        return None
    return [rf"  \item[\textbf{{{key}}}] {value.strip()}" for key, value in pairs]


def parse_environment_block(lines: list[str], index: int) -> tuple[str | None, str, int]:
    if lines[index] == r"\begin{infobox}":
        block: list[str] = []
        j = index
        while j < len(lines):
            block.append(lines[j])
            if lines[j] == r"\end{infobox}":
                return "infobox", "\n".join(block), j + 1
            j += 1
    if lines[index] == r"\begin{center}":
        block = []
        j = index
        while j < len(lines):
            block.append(lines[j])
            if lines[j] == r"\end{center}":
                joined = "\n".join(block)
                return ("smallcenter" if is_small_image_center(joined) else "center"), joined, j + 1
            j += 1
    return None, lines[index], index + 1


def merge_chapter_header_metadata(text: str) -> str:
    """Merge split Reading profile boxes and QR rows immediately under chapters."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    chapter_re = re.compile(r"^\\chapter\{.+\}$")

    while i < len(lines):
        out.append(lines[i])
        if chapter_re.match(lines[i]):
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            items: list[str] = []
            image_blocks: list[str] = []
            consumed = False

            while i < len(lines):
                if not lines[i].strip():
                    i += 1
                    continue
                kind, block, next_i = parse_environment_block(lines, i)
                if kind == "infobox":
                    match = INFOBOX_RE.fullmatch(block)
                    if match:
                        items.extend(line for line in match.group("items").splitlines() if line.strip())
                        consumed = True
                        i = next_i
                        continue
                if kind == "smallcenter":
                    image_blocks.append(block)
                    consumed = True
                    i = next_i
                    continue
                metadata = plain_metadata_items(lines[i])
                if metadata:
                    items.extend(metadata)
                    consumed = True
                    i += 1
                    continue
                break

            if consumed:
                out.append("")
                if items:
                    out.extend([r"\begin{infobox}", READING_PROFILE_LIST_BEGIN])
                    seen: set[str] = set()
                    for item in items:
                        key = re.sub(r"\s+", " ", item.strip())
                        if key not in seen:
                            out.append(item)
                            seen.add(key)
                    out.extend([r"\end{description}", r"\end{infobox}"])
                if image_blocks:
                    includes: list[str] = []
                    for block in image_blocks:
                        body = CENTER_RE.fullmatch(block.strip()).group("body")
                        includes.extend(re.findall(r"\\includegraphics\[[^\]]*\]\{[^{}]+\}", body))
                    out.extend(["", r"\begin{center}"])
                    for idx, include in enumerate(includes):
                        suffix = r"\hspace{1em}" if idx < len(includes) - 1 else ""
                        out.append("  " + include + suffix)
                    out.append(r"\end{center}")
                out.append("")
            continue
        i += 1

    return re.sub(r"\n{4,}", "\n\n\n", "\n".join(out) + "\n")


def normalize_long_text_tables(text: str) -> str:
    """Make common OCR/Pandoc long-text tables fit the page width."""
    three_col = re.compile(
        r"\\begin\{longtable\}\[\]\{@\{\}\|l\|l\|l\|@\{\}\}(?P<body>.*?)\\end\{longtable\}",
        re.S,
    )
    eight_col = re.compile(
        r"\\begin\{longtable\}\[\]\{@\{\}llllllll@\{\}\}(?P<body>.*?)\\end\{longtable\}",
        re.S,
    )

    def wrap_three(match: re.Match[str]) -> str:
        return (
            "{\\small\n"
            "\\setlength{\\tabcolsep}{3pt}\n"
            "\\renewcommand{\\arraystretch}{1.15}\n"
            "\\begin{longtable}{|>{\\raggedright\\arraybackslash}p{0.24\\textwidth}|"
            ">{\\raggedright\\arraybackslash}p{0.43\\textwidth}|"
            ">{\\raggedright\\arraybackslash}p{0.24\\textwidth}|}"
            f"{match.group('body')}\\end{{longtable}}\n}}"
        )

    def wrap_eight(match: re.Match[str]) -> str:
        body = match.group("body")
        body = re.sub(
            r"^\s*\\toprule\\noalign\{\}\n\\endhead\n\\bottomrule\\noalign\{\}\n\\endlastfoot\n",
            "\n\\toprule\n",
            body,
            count=1,
        )
        return (
            "{\\scriptsize\n"
            "\\begin{center}\n"
            "\\setlength{\\tabcolsep}{2pt}\n"
            "\\renewcommand{\\arraystretch}{1.05}\n"
            "\\resizebox{\\textwidth}{!}{%\n"
            "\\begin{tabular}{@{}llllllll@{}}"
            f"{body}\\bottomrule\n\\end{{tabular}}}}\n"
            "\\end{center}\n"
            "}"
        )

    text = three_col.sub(wrap_three, text)
    text = eight_col.sub(wrap_eight, text)
    return text


def count_tabularx_columns(spec: str) -> int:
    """Estimate the number of logical columns in a tabularx column spec."""
    total = 0

    def consume_repeat(match: re.Match[str]) -> str:
        nonlocal total
        repeat = int(match.group(1))
        repeated_spec = match.group(2)
        total += repeat * count_tabularx_columns(repeated_spec)
        return ""

    repeat_re = re.compile(r"\*\{(\d+)\}\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")
    remainder = repeat_re.sub(consume_repeat, spec)
    modifier_re = re.compile(r"[<>]\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")
    remainder = modifier_re.sub("", remainder)
    total += len(re.findall(r"(?<!\\)[Xlcr]", remainder))
    total += len(re.findall(r"(?<!\\)[pmb]\{", remainder))
    if total:
        return total
    return max(1, spec.count("X"))


def longtable_spec_for_tabularx_columns(column_count: int) -> str:
    usable_width = 0.92 if column_count <= 3 else 0.88
    column_width = max(0.07, usable_width / max(1, column_count))
    column = f">{{\\raggedright\\arraybackslash}}p{{{column_width:.3f}\\textwidth}}"
    return "|" + "|".join(column for _ in range(max(1, column_count))) + "|"


def convert_oversized_tabularx_to_longtable(text: str) -> str:
    """Let long teaching-material tables break across pages instead of producing overflow blanks."""
    pattern = re.compile(
        r"\\begin\{tabularx\}\{\\textwidth\}\{(?P<spec>[^\n]*)\}(?P<body>.*?)\\end\{tabularx\}",
        re.S,
    )

    def repl(match: re.Match[str]) -> str:
        spec = match.group("spec")
        body = match.group("body")
        column_count = count_tabularx_columns(spec)
        row_breaks = len(re.findall(r"\\\\(?:\s*\\hline)?", body))
        if column_count < 2 or column_count > 8:
            return match.group(0)
        body_len = len(body)
        is_wide_checklist = column_count >= 5 and body_len > 900 and row_breaks >= 8
        is_long_text_table = body_len > 1800 and row_breaks >= 5
        is_many_tall_rows = row_breaks >= 14 and body_len > 1400
        if not (is_wide_checklist or is_long_text_table or is_many_tall_rows):
            return match.group(0)
        long_spec = longtable_spec_for_tabularx_columns(column_count)
        return f"\\begin{{longtable}}{{{long_spec}}}{body}\\end{{longtable}}"

    return pattern.sub(repl, text)


def normalize_table_cell_layout(text: str) -> str:
    """Constrain table media and restore OCR text arrays as breakable cell text."""
    table_re = re.compile(
        r"\\begin\{(?P<env>tabularx|longtable)\}(?P<head>[^\n]*)(?P<body>.*?)\\end\{(?P=env)\}",
        re.S,
    )
    array_re = re.compile(r"\$\\begin\{array\}\{c\}(?P<body>.*?)\\end\{array\}\$", re.S)

    def restore_text_array(match: re.Match[str]) -> str:
        rows = [row.strip() for row in re.split(r"\\\\", match.group("body")) if row.strip()]
        text_lengths = [len(value) for row in rows for value in re.findall(r"\\text\{([^{}]*)\}", row)]
        if not text_lengths or max(text_lengths) < 36:
            return match.group(0)
        rendered = []
        for row in rows:
            full_text = re.fullmatch(r"\\text\{([^{}]*)\}", row)
            rendered.append(full_text.group(1) if full_text else rf"\({row}\)")
        return r"\parbox{0.95\linewidth}{\centering " + r"\\ ".join(rendered) + "}"

    def normalize_table(match: re.Match[str]) -> str:
        body = re.sub(
            r"\\includegraphics(?:\[[^]]*\])?",
            r"\\includegraphics[width=\\hsize,height=0.12\\textheight,keepaspectratio]",
            match.group("body"),
        )
        body = array_re.sub(restore_text_array, body)
        return rf"\begin{{{match.group('env')}}}{match.group('head')}{body}\end{{{match.group('env')}}}"

    return table_re.sub(normalize_table, text)


def fit_ultrawide_tabularx(text: str) -> str:
    """Scale compact tables with more than eight columns to the printable width."""
    pattern = re.compile(
        r"\\begin\{tabularx\}\{\\textwidth\}\{(?P<spec>[^\n]*)\}(?P<body>.*?)\\end\{tabularx\}",
        re.S,
    )

    def repl(match: re.Match[str]) -> str:
        columns = count_tabularx_columns(match.group("spec"))
        if columns <= 8:
            return match.group(0)
        spec = "|" + "|".join("c" for _ in range(columns)) + "|"
        return (
            r"\noindent\resizebox{\linewidth}{!}{%" + "\n"
            + rf"\begin{{tabular}}{{{spec}}}{match.group('body')}\end{{tabular}}" + "\n}"
        )

    return pattern.sub(repl, text)


def fit_image_dense_tabularx(text: str) -> str:
    """Fit multi-column visual matrices whose cell images define a wider natural box."""
    pattern = re.compile(
        r"\\begin\{tabularx\}\{\\textwidth\}\{(?P<spec>[^\n]*)\}(?P<body>.*?)\\end\{tabularx\}",
        re.S,
    )

    def repl(match: re.Match[str]) -> str:
        columns = count_tabularx_columns(match.group("spec"))
        image_count = len(re.findall(r"\\includegraphics", match.group("body")))
        if columns < 3 or image_count < 2:
            return match.group(0)
        spec = "|" + "|".join("c" for _ in range(columns)) + "|"
        return (
            r"\noindent\resizebox{\linewidth}{!}{%" + "\n"
            + rf"\begin{{tabular}}{{{spec}}}{match.group('body')}\end{{tabular}}" + "\n}"
        )

    return pattern.sub(repl, text)


def fit_short_label_tabularx(text: str) -> str:
    """Give T/F/NG-style labels narrow columns and preserve width for prompts."""
    pattern = re.compile(
        r"\\begin\{tabularx\}\{\\textwidth\}\{(?P<spec>[^\n]*)\}(?P<body>.*?)\\end\{tabularx\}",
        re.S,
    )

    def repl(match: re.Match[str]) -> str:
        body = match.group("body")
        short_label_rows = re.findall(
            r"&\s*[A-Za-z]{1,3}\s*&\s*[A-Za-z]{1,3}\s*&\s*[A-Za-z]{1,3}\s*\\\\",
            body,
        )
        if count_tabularx_columns(match.group("spec")) != 4 or len(short_label_rows) < 2:
            return match.group(0)
        spec = r"|>{\raggedright\arraybackslash}X|c|c|c|"
        return rf"\begin{{tabularx}}{{\textwidth}}{{{spec}}}{body}\end{{tabularx}}"

    return pattern.sub(repl, text)


def allow_table_token_breaks(text: str) -> str:
    """Add an invisible break opportunity at slash-separated words inside tables."""
    table_re = re.compile(
        r"\\begin\{(?P<env>tabularx|longtable)\}(?P<head>[^\n]*)(?P<body>.*?)\\end\{(?P=env)\}",
        re.S,
    )

    def repl(match: re.Match[str]) -> str:
        body = match.group("body")
        out = []
        cursor = 0
        def add_breaks(value: str) -> str:
            value = re.sub(
                r"(?<=\S)\.{6}(?=\S)",
                lambda match: rf"\allowbreak{{}}{match.group(0)}\allowbreak{{}}",
                value,
            )
            value = re.sub(
                r"(?<=[A-Za-z])/(?=[A-Za-z])",
                lambda _match: r"/\allowbreak{}",
                value,
            )
            value = re.sub(r"(?<=[A-Za-z)])(?=\()", lambda _match: r"\allowbreak{}", value)
            value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", lambda _match: r"\allowbreak{}", value)
            return re.sub(
                r"[A-Za-z]{18,}",
                lambda token: r"\allowbreak{}".join(
                    token.group(0)[offset : offset + 16]
                    for offset in range(0, len(token.group(0)), 16)
                ),
                value,
            )

        for image in IMAGE_INCLUDE_RE.finditer(body):
            out.append(add_breaks(body[cursor:image.start()]))
            out.append(image.group(0))
            cursor = image.end()
        out.append(add_breaks(body[cursor:]))
        body = "".join(out)
        return rf"\begin{{{match.group('env')}}}{match.group('head')}{body}\end{{{match.group('env')}}}"

    return table_re.sub(repl, text)


def allow_long_prose_breaks(text: str) -> str:
    """Add nonprinting break opportunities to OCR-joined prose and credit ledgers."""
    token_re = re.compile(r"[A-Za-z0-9]{32,}")

    def split_token(match: re.Match[str]) -> str:
        token = match.group(0)
        return r"\allowbreak{}".join(token[offset : offset + 16] for offset in range(0, len(token), 16))

    def split_binary(match: re.Match[str]) -> str:
        token = match.group(0)
        return r"\allowbreak{}".join(token[offset : offset + 8] for offset in range(0, len(token), 8))

    out: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"[01]{32,}", split_binary, line)
        standard_ledger = len(re.findall(r"\b[A-Z]{2,}(?:\.[A-Z0-9]+){2,}", line)) >= 2
        if (len(line) < 300 and line.count("/") < 4 and not standard_ledger) or line.lstrip().startswith("%"):
            out.append(line)
            continue

        def add_breaks(value: str) -> str:
            value = token_re.sub(split_token, value)
            value = re.sub(r"/(?=[A-Za-z0-9])", lambda _match: r"/\allowbreak{}", value)
            value = re.sub(r"(?<=@)(?=[A-Za-z0-9])", lambda _match: r"\allowbreak{}", value)
            value = re.sub(r"(?<=\.)(?=[A-Za-z])", lambda _match: r"\allowbreak{}", value)
            value = re.sub(r"(?<=\))(?=\()", lambda _match: r"\allowbreak{}", value)
            value = re.sub(r"([,;])(?=\S)", lambda match: match.group(1) + r"\allowbreak{}", value)
            return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", lambda _match: r"\allowbreak{}", value)

        pieces: list[str] = []
        cursor = 0
        for image in IMAGE_INCLUDE_RE.finditer(line):
            pieces.append(add_breaks(line[cursor : image.start()]))
            pieces.append(image.group(0))
            cursor = image.end()
        pieces.append(add_breaks(line[cursor:]))
        line = "".join(pieces)
        if standard_ledger:
            line = re.sub(r"(?<=[A-Z0-9])\.(?=[A-Z0-9])", lambda _match: r".\allowbreak{}", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def allow_long_inline_math_breaks(text: str) -> str:
    """Add invisible break opportunities to long inline math-heavy lines."""
    span_re = re.compile(r"(?<!\\)\$(?!\$)(?P<body>(?:\\.|[^$\n])*)(?<!\\)\$")
    out: list[str] = []
    for line in text.splitlines():
        if len(line) < 100 or line.lstrip().startswith("%"):
            out.append(line)
            continue
        pieces: list[str] = []
        cursor = 0
        matches = list(span_re.finditer(line))
        for index, match in enumerate(matches):
            pieces.append(line[cursor : match.start()])
            body = match.group("body")
            if len(body) >= 30:
                def split_text(text_match: re.Match[str]) -> str:
                    words = text_match.group("body").split()
                    if len(words) < 4:
                        return text_match.group(0)
                    midpoint = len(words) // 2
                    return rf"\text{{{' '.join(words[:midpoint])}}}\allowbreak{{}}\text{{ {' '.join(words[midpoint:])}}}"

                body = re.sub(r"\\text\{(?P<body>[^{}\n]{24,})\}", split_text, body)
                body = re.sub(r",\s*", r",\\allowbreak{} ", body)
            pieces.append(f"${body}$")
            cursor = match.end()
            if index < len(matches) - 1:
                gap = line[cursor : matches[index + 1].start()]
                if gap and not gap.strip():
                    pieces.append(r"\allowbreak{}" + gap)
                    cursor = matches[index + 1].start()
        pieces.append(line[cursor:])
        out.append("".join(pieces))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def split_dense_formula_choice_lines(text: str) -> str:
    """Put long formula-heavy multiple-choice options on separate printable lines."""
    out: list[str] = []
    for line in text.splitlines():
        markers = re.findall(r"(?<=\s)[B-E]\s+(?=\$)", line)
        if len(line) >= 260 and len(markers) >= 2:
            line = re.sub(
                r"(?<=\s)([B-E])\s+(?=\$)",
                lambda match: rf"\par\noindent {match.group(1)} ",
                line,
            )
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def split_dense_answer_branches(text: str) -> str:
    """Put long answer-key branches on separate lines before they overflow."""
    out: list[str] = []
    for line in text.splitlines():
        labels = re.findall(r"(?:^|\s)([a-h])\s+(?=[A-Z])", line)
        inline_math_count = len(re.findall(r"(?<!\\)\$(?!\$)", line)) // 2
        if len(line) >= 140 and len(labels) >= 2 and inline_math_count >= 2:
            line = re.sub(
                r"(?<!\\noindent )(?<=\s)([b-h])\s+(?=[A-Z])",
                lambda match: rf"\par\noindent {match.group(1)} ",
                line,
            )
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def remove_qr_ocr_captions(text: str, input_path: Path, out_dir: Path) -> str:
    """Drop OCR-generated QR captions only when the referenced image is QR-like."""
    figure_re = re.compile(r"\\begin\{figure\}\[H\].*?\\end\{figure\}", re.S)
    caption_re = re.compile(r"\n?\\caption\{(?P<body>[^{}]*)\}")

    def replace_figure(match: re.Match[str]) -> str:
        block = match.group(0)
        caption = caption_re.search(block)
        image = IMAGE_INCLUDE_RE.search(block)
        if not caption or not image or not _looks_like_ocr_garbage_caption(caption.group("body")):
            return block
        ref = image_ref_from_match(image)
        path = resolve_local_asset(input_path, out_dir, ref)
        caption_claims_qr = bool(re.search(r"二维[码碼]|二維[码碼]", caption.group("body")))
        if not path or not (_looks_like_qr_image(path) or caption_claims_qr):
            return block
        return block[:caption.start()] + block[caption.end():]

    return figure_re.sub(replace_figure, text)


def _looks_like_ocr_garbage_caption(value: str) -> bool:
    plain = re.sub(r"\\[^\s{}]+|[{}$()]", "", value).strip()
    cjk = re.findall(r"[\u3400-\u9fff]", plain)
    visible = re.findall(r"[^\s]", plain)
    return (
        6 <= len(visible) <= 48
        and len(cjk) / max(len(visible), 1) >= 0.55
        and not re.search(r"[。！？!?：:]", plain)
    )


def _looks_like_qr_image(path: Path) -> bool:
    try:
        from PIL import Image

        with Image.open(path) as image:
            gray = image.convert("L")
            width, height = gray.size
            if not width or not height:
                return False
            gray.thumbnail((96, 96))
            pixels = list(gray.getdata())
            if not pixels or max(pixels) - min(pixels) < 140:
                return False
            dark_ratio = sum(value < 128 for value in pixels) / len(pixels)
            if not 0.08 <= dark_ratio <= 0.72:
                return False
            binary = [value < 128 for value in pixels]
            sample_width, sample_height = gray.size
            row_changes = sum(
                binary[y * sample_width + x] != binary[y * sample_width + x - 1]
                for y in range(sample_height)
                for x in range(1, sample_width)
            )
            col_changes = sum(
                binary[y * sample_width + x] != binary[(y - 1) * sample_width + x]
                for y in range(1, sample_height)
                for x in range(sample_width)
            )
            opportunities = sample_height * max(sample_width - 1, 0) + sample_width * max(sample_height - 1, 0)
            return opportunities > 0 and (row_changes + col_changes) / opportunities >= 0.12
    except Exception:
        return False


def split_multi_set_assignment_math(text: str) -> str:
    """Split dense inline spans containing several named set assignments."""
    span_re = re.compile(r"(?<!\\)\$(?!\$)(?P<body>(?:\\.|[^$\n])*)(?<!\\)\$(?!\$)")

    def repl(match: re.Match[str]) -> str:
        body = match.group("body")
        if len(re.findall(r"(?:^|,)\s*(?:\\allowbreak\{\}\s*)?[A-Z]\s*=", body)) < 3:
            return match.group(0)
        body = re.sub(
            r",\s*(?:\\allowbreak\{\}\s*)?(?=[A-Z]\s*=)",
            lambda _match: r"$,\par\noindent $",
            body,
        )
        return f"${body}$"

    return span_re.sub(repl, text)


def strip_inline_delimiters_inside_display_math(text: str) -> str:
    """Remove OCR inline delimiters nested inside standalone display blocks."""
    out: list[str] = []
    in_display = False
    for line in text.splitlines():
        if line.strip() == "$$":
            in_display = not in_display
        elif in_display:
            line = line.replace(r"\(", "").replace(r"\)", "")
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def replace_circled_equation_tags(text: str) -> str:
    """Render OCR equation labels visibly when amsmath cannot accept a tag."""
    text = re.sub(
        r"\\tag\s*\{\\text\{(?P<label>\\textcircled\{[^{}]+\})\}\}",
        lambda match: rf"\quad\text{{{match.group('label')}}}",
        text,
    )
    return re.sub(
        r"\\tag\s*\{\s*(?P<label>\\textcircled\{[^{}]+\})\s*\}",
        lambda match: rf"\quad\text{{{match.group('label')}}}",
        text,
    )


def repair_textcircled_math_symbols(text: str) -> str:
    """Replace a text-mode circled circle with its standard math equivalent."""
    return re.sub(
        r"\\textcircled\s*\{\s*\\(?:circ|circledcirc)\s*\}",
        r"\\ensuremath{\\circledcirc}",
        text,
    )


def repair_bare_boxed_before_rowbreak(text: str) -> str:
    """Give a bare boxed answer slot an explicit printable placeholder."""
    return re.sub(r"\\boxed\s*(?=\\\\|\\end\{array\})", r"\\boxed{\\quad} ", text)


def repair_bare_display_math_blanks(text: str) -> str:
    """Render OCR underscore runs in display math as printable answer boxes."""
    out: list[str] = []
    in_display = False
    for line in text.splitlines():
        if line.strip() == "$$":
            in_display = not in_display
        elif in_display:
            line = re.sub(
                r"(?<!\\)(?:_\s*){2,}",
                lambda match: r"\;".join(r"\square" for _ in re.findall(r"_", match.group(0))) + " ",
                line,
            )
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_underline_placeholders(text: str) -> str:
    """Replace OCR underscore placeholders with a valid printable underline."""
    return re.sub(r"\\underline\s*\{\s*_+\s*\}", r"\\underline{\\hspace{1.5em}}", text)


def remove_breaks_after_sizing_commands(text: str) -> str:
    """Keep TeX delimiters adjacent to their left/right sizing commands."""
    return re.sub(r"\\(left|right)\\allowbreak\{\}\s*", r"\\\1", text)


def repair_final_nested_math_and_text_delimiters(text: str) -> str:
    """Remove late nested display markers and flatten simple nested text groups."""
    text = re.sub(r"\\\]\s*\\\[", r"\\quad ", text)
    text = re.sub(
        r"\\text\s*\{\\\{\\text\s*\{(?P<body>[^{}]*)\}\\\}\}",
        lambda match: rf"\text{{\{{{match.group('body')}\}}}}",
        text,
    )
    text = re.sub(r"\\text\s*\{\\\}(?!\})", r"\\text{\\}}", text)
    return re.sub(r"(\\quad)\s+", r"\1 ", text)


def wrap_prose_set_descriptions(text: str) -> str:
    """Render English set descriptions as prose instead of one math-italic token run."""
    span_re = re.compile(r"(?<!\\)\$(?!\$)(?P<body>(?:\\.|[^$\n])*)(?<!\\)\$(?!\$)")
    set_re = re.compile(r"\\\{(?P<body>[A-Za-z][A-Za-z0-9 '\u2018\u2019\u201c\u201d-]{20,})\\\}")

    def span_repl(match: re.Match[str]) -> str:
        body = set_re.sub(lambda item: rf"\{{\text{{{item.group('body').strip()}}}\}}", match.group("body"))
        return f"${body}$"

    return span_re.sub(span_repl, text)


def split_long_set_definition_lines(text: str) -> str:
    """Give several long set definitions separate printable lines."""
    out = []
    for line in text.splitlines():
        set_assignments = len(re.findall(r"\$[A-Za-z]\s*=\s*\\\{", line))
        if len(line) >= 140 and set_assignments >= 2:
            seen = 0

            def split_assignment(match: re.Match[str]) -> str:
                nonlocal seen
                seen += 1
                if seen == 1:
                    return match.group(0)
                connector = re.sub(r"^(?:\\allowbreak\{\}|,)\s*", "", match.group("prefix")).strip()
                return r"\par\noindent " + ((connector + " ") if connector else "") + match.group("assignment")

            line = re.sub(
                r"(?P<prefix>(?:\\allowbreak\{\}|,)?\s*(?:and\s+)?)"
                r"(?P<assignment>\$[A-Za-z]\s*=\s*\\\{)",
                split_assignment,
                line,
            )
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def repair_missing_rowbreak_before_hline(text: str) -> str:
    """Insert the array row ending that OCR dropped immediately before hline."""
    array_re = re.compile(r"\\begin\{array\}\{[^{}\n]*\}(?P<body>.*?)\\end\{array\}", re.S)

    def repl(match: re.Match[str]) -> str:
        body = re.sub(r"(?<!\\)([A-Za-z0-9}])\s+\\hline", r"\1 \\\\ \\hline", match.group("body"))
        return match.group(0).replace(match.group("body"), body, 1)

    return array_re.sub(repl, text)


def repair_misplaced_hline_alignment_tokens(text: str) -> str:
    """Drop an empty OCR alignment cell placed immediately before an array rule."""
    return re.sub(
        r"\\\\\s*&\s*\\hline\s*&",
        lambda _match: r"\\ \hline &",
        text,
    )


def repair_literal_set_fraction_numerators(text: str) -> str:
    """Close a fraction numerator when it is an escaped literal set."""
    pattern = re.compile(
        r"\\frac\{(?P<num>\\\{(?:[^{}]|\{[^{}]*\})*?\\\})\{(?P<den>[^{}\n]+)\}"
    )
    return pattern.sub(r"\\frac{\g<num>}{\g<den>}", text)


def wrap_bare_urls(text: str) -> str:
    """Use xurl for plain HTTP and domain references so addresses can line-break."""
    pattern = re.compile(
        r"(?<![\w{}])(?:"
        r"(?:https?://|www\.)[^\s{}]+|"
        r"(?:[A-Za-z0-9-]+\.)+(?:com|org|net|edu|gov)(?:/[^\s{},;]*)?"
        r")"
    )
    out = []
    for line in text.splitlines():
        if r"\url{" not in line:
            line = pattern.sub(lambda match: rf"\url{{{match.group(0).rstrip('.,;')}}}" + match.group(0)[len(match.group(0).rstrip('.,;')):], line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def merge_sparse_longtable_rows(text: str) -> str:
    """Give unusually long sparse answer rows the full table width."""
    out: list[str] = []
    in_longtable = False
    for line in text.splitlines():
        if r"\begin{longtable}" in line:
            in_longtable = True
        if in_longtable and len(line) >= 220 and line.rstrip().endswith(r"\\ \hline"):
            body = line.rstrip()[: -len(r"\\ \hline")].rstrip()
            cells = [cell.strip() for cell in body.split(" & ")]
            if len(cells) >= 4 and cells[-2:] == ["~", "~"]:
                populated = [cell for cell in cells if cell != "~"]
                line = (
                    rf"\multicolumn{{{len(cells)}}}{{|p{{0.94\textwidth}}|}}"
                    rf"{{{' '.join(populated)}}} \\ \hline"
                )
        out.append(line)
        if r"\end{longtable}" in line:
            in_longtable = False
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def scale_extreme_inline_math_spans(text: str) -> str:
    """Put exceptionally long, unbreakable inline formulas on a fitted line."""
    span_re = re.compile(r"(?<!\\)\$(?!\$)(?P<body>(?:\\.|[^$\n])*)(?<!\\)\$(?!\$)")
    out = []
    for line in text.splitlines():
        match = next((candidate for candidate in span_re.finditer(line) if len(candidate.group("body")) >= 220), None)
        if not match:
            out.append(line)
            continue
        prefix = line[:match.start()].rstrip()
        suffix = line[match.end():].strip()
        if prefix:
            out.append(prefix)
        out.append(r"\begin{center}\resizebox{0.96\linewidth}{!}{$" + match.group("body") + r"$}\end{center}")
        if suffix:
            out.append(suffix)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def separate_inline_table_images(text: str) -> str:
    """Give images embedded between table-cell prose their own paragraph."""
    table_re = re.compile(
        r"\\begin\{(?P<env>tabularx|longtable)\}(?P<head>[^\n]*)(?P<body>.*?)\\end\{(?P=env)\}",
        re.S,
    )

    def repl(match: re.Match[str]) -> str:
        body = IMAGE_INCLUDE_RE.sub(
            lambda image: r"\par\noindent " + image.group(0) + r"\par\noindent ",
            match.group("body"),
        )
        return rf"\begin{{{match.group('env')}}}{match.group('head')}{body}\end{{{match.group('env')}}}"

    return table_re.sub(repl, text)


def fit_long_unbreakable_display_arrays(text: str) -> str:
    """Scale long OCR display arrays or boxed chains whose text cannot line-break."""
    pattern = re.compile(r"\$\$\s*\n(?P<body>.*?)\n\s*\$\$", re.S)

    def repl(match: re.Match[str]) -> str:
        body = match.group("body").strip()
        long_array = r"\begin{array}" in body and r"\text" in body and len(body) >= 300
        boxed_chain = body.count(r"\boxed") >= 2 and len(body) >= 120
        if not (long_array or boxed_chain):
            return match.group(0)
        return r"\begin{center}\resizebox{\linewidth}{!}{$\displaystyle " + body + r"$}\end{center}"

    return pattern.sub(repl, text)


def collect_image_refs(text: str) -> list[str]:
    refs = [image_ref_from_match(match) for match in IMAGE_INCLUDE_RE.finditer(text)]
    seen: set[str] = set()
    unique: list[str] = []
    for ref in refs:
        if ref not in seen:
            unique.append(ref)
            seen.add(ref)
    return unique


def copy_referenced_images(input_path: Path, out_dir: Path, content: str) -> list[str]:
    missing: list[str] = []
    for ref in collect_image_refs(content):
        ref_path = Path(ref)
        if ref_path.is_absolute():
            if not ref_path.exists():
                missing.append(ref)
            continue

        source = input_path.parent / ref_path
        target = out_dir / ref_path
        if source.exists() and source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        elif not target.exists():
            missing.append(ref)
    return missing


def resolve_local_asset(input_path: Path, out_dir: Path, ref: str) -> Path | None:
    path = Path(ref)
    if path.is_absolute():
        return path if path.exists() else None
    for base in (input_path.parent, out_dir):
        candidate = base / path
        if candidate.exists():
            return candidate
    return None


def image_size(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception:
        return None


def image_options_for(size: tuple[int, int]) -> str:
    width, height = size
    longest = max(width, height)
    aspect = width / height if height else 1
    if longest <= 100:
        return r"width=0.04\textwidth"
    if width <= 80 and height <= 220 and aspect <= 0.58:
        return r"height=0.08\textheight"
    if height <= 80 and width <= 220 and aspect >= 2.0:
        return r"width=0.10\textwidth"
    if longest <= 300 and 0.72 <= aspect <= 1.38:
        return r"width=0.18\textwidth"
    if aspect >= 2.0:
        return r"width=0.78\textwidth"
    if aspect <= 0.58:
        return r"height=0.28\textheight"
    if longest <= 380:
        return r"width=0.30\textwidth"
    return r"width=0.48\textwidth"


def normalize_image_includes(text: str, input_path: Path, out_dir: Path) -> str:
    """Use existing image dimensions to prevent QR codes or small icons from becoming huge."""

    def repl(match: re.Match[str]) -> str:
        ref = image_ref_from_match(match)
        path = resolve_local_asset(input_path, out_dir, ref)
        if not path:
            return match.group(0)
        size = image_size(path)
        if not size:
            return match.group(0)
        return f"\\includegraphics[{image_options_for(size)}]{{{ref}}}"

    text = IMAGE_INCLUDE_RE.sub(repl, text)
    return compact_small_image_centers(text)


def split_merged_adjacent_figure_captions(text: str) -> str:
    """Move OCR-merged labels such as ``a b`` onto adjacent figures."""
    figure_re = re.compile(r"\\begin\{figure\}\[H\].*?\\end\{figure\}", re.S)
    caption_re = re.compile(r"\\caption\{\s*([A-Da-d])\s+([A-Da-d])\s*\}")
    figures = list(figure_re.finditer(text))
    replacements: list[tuple[int, int, str]] = []
    for index, match in enumerate(figures[:-1]):
        caption = caption_re.search(match.group(0))
        if not caption:
            continue
        following = figures[index + 1]
        if text[match.end() : following.start()].strip() or r"\caption{" in following.group(0):
            continue
        first, second = (value.lower() for value in caption.groups())
        current_block = caption_re.sub(rf"\\caption{{{first}}}", match.group(0), count=1)
        following_block = following.group(0).replace(
            r"\end{figure}", rf"\caption{{{second}}}" + "\n" + r"\end{figure}", 1
        )
        replacements.extend(
            [(match.start(), match.end(), current_block), (following.start(), following.end(), following_block)]
        )
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return text


def compact_small_image_centers(text: str) -> str:
    """Combine consecutive QR/small-image center blocks into one row."""
    block_re = re.compile(r"\\begin\{center\}\n(?P<body>.*?)\\end\{center\}", re.S)
    blocks = list(block_re.finditer(text))
    if not blocks:
        return text

    out: list[str] = []
    cursor = 0
    i = 0
    while i < len(blocks):
        block = blocks[i]
        out.append(text[cursor : block.start()])
        body = block.group("body")
        if not is_small_image_center(block.group(0)):
            out.append(block.group(0))
            cursor = block.end()
            i += 1
            continue

        includes = re.findall(r"\\includegraphics\[[^\]]*\]\{[^{}]+\}", body)
        cursor = block.end()
        j = i + 1
        while j < len(blocks) and text[cursor : blocks[j].start()].strip() == "" and is_small_image_center(blocks[j].group(0)):
            includes.extend(re.findall(r"\\includegraphics\[[^\]]*\]\{[^{}]+\}", blocks[j].group("body")))
            cursor = blocks[j].end()
            j += 1

        out.append("\\begin{center}\n")
        for idx, include in enumerate(includes):
            suffix = r"\hspace{1em}" if idx < len(includes) - 1 else ""
            out.append("  " + include + suffix + "\n")
        out.append("\\end{center}")
        i = j

    out.append(text[cursor:])
    return "".join(out)


def copy_optional_asset(asset: str | None, input_path: Path, out_dir: Path) -> str | None:
    if not asset:
        return None
    asset_path = Path(asset)
    source = asset_path if asset_path.is_absolute() else input_path.parent / asset_path
    if not source.exists():
        return asset
    target = out_dir / "images" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return f"images/{source.name}"


def package_option_prelude(demo_images: bool) -> str:
    if demo_images:
        return "\\PassOptionsToPackage{demo}{graphicx}\n"
    return ""


def common_preamble() -> str:
    symbol_mappings = "\n".join(
        rf"\newunicodechar{{{char}}}{{\ifmmode\mbox{{{{\luceonsymbolfont {char}}}}}\else{{{{\luceonsymbolfont {char}}}}}\fi}}"
        for char in TEXT_SYMBOLS
    )
    math_mappings = "\n".join(
        rf"\newunicodechar{{{char}}}{{\ensuremath{{{command}}}}}" for char, command in MATH_SYMBOLS.items()
    )
    text_command_mappings = "\n".join(
        rf"\newunicodechar{{{char}}}{{{command}}}" for char, command in TEXT_COMMAND_SYMBOLS.items()
    )
    return rf"""
\usepackage{{fontspec}}
\IfFontExistsTF{{Charis SIL}}{{\setmainfont{{Charis SIL}}}}{{\IfFontExistsTF{{Times New Roman}}{{\setmainfont{{Times New Roman}}}}{{}}}}
\IfFileExists{{fonts/{NOTO_SERIF_CJK_FILENAME}}}{{%
  \setCJKmainfont[Path=fonts/,FontIndex=2,AutoFakeBold=2,AutoFakeSlant=.2]{{{NOTO_SERIF_CJK_FILENAME}}}
}}{{}}
\IfFontExistsTF{{IPAexMincho}}{{%
  \setCJKfallbackfamilyfont{{\CJKrmdefault}}{{IPAexMincho}}
  \setCJKfallbackfamilyfont{{zhsong}}{{IPAexMincho}}
  \setCJKfallbackfamilyfont{{zhkai}}{{IPAexMincho}}
  \setCJKfallbackfamilyfont{{zhfs}}{{IPAexMincho}}
}}{{}}
\IfFontExistsTF{{IPAGothic}}{{\setCJKfallbackfamilyfont{{zhhei}}{{IPAGothic}}}}{{}}
\IfFontExistsTF{{Amiri}}{{\newfontfamily\luceonArabicFont{{Amiri}}}}{{\newfontfamily\luceonArabicFont{{DejaVu Sans}}}}
\xeCJKsetup{{CJKmath=true}}
\setlength{{\emergencystretch}}{{3em}}
\usepackage{{newunicodechar}}
\IfFileExists{{fonts/{NOTO_SYMBOLS_FILENAME}}}{{\newfontfamily\luceonsymbolfont[Path=fonts/]{{{NOTO_SYMBOLS_FILENAME}}}}}{{\newfontfamily\luceonsymbolfont{{Latin Modern Math}}}}
{symbol_mappings}
{math_mappings}
{text_command_mappings}
\usepackage{{graphicx,float}}
\usepackage{{amsmath,amssymb}}
\usepackage{{xurl}}
\IfFileExists{{cancel.sty}}{{\usepackage{{cancel}}}}{{\providecommand{{\cancel}}[1]{{#1}}}}
\usepackage{{longtable,booktabs,array,multirow,tabularx,multicol}}
\usepackage{{enumitem}}
\usepackage{{xcolor,ulem}}
\usepackage{{tcolorbox}}
\tcbuselibrary{{skins,breakable}}
\usepackage{{tikz}}
\usetikzlibrary{{positioning,patterns,decorations.pathreplacing,shapes.geometric,arrows}}
\usepackage{{caption}}
\usepackage{{fancyhdr}}
\captionsetup[figure]{{labelformat=empty}}
\providecommand{{\texorpdfstring}}[2]{{#1}}
\providecommand{{\mum}}{{\ensuremath{{\mu\mathrm{{m}}}}}}
\providecommand{{\micro}}{{\ensuremath{{\mu}}}}
\providecommand{{\degree}}{{\ensuremath{{^\circ}}}}
\providecommand{{\ohm}}{{\ensuremath{{\Omega}}}}
\providecommand{{\bcancel}}[1]{{#1}}
\providecommand{{\xcancel}}[1]{{#1}}
\providecommand{{\cancelto}}[2]{{#2}}
\providecommand{{\xlongequal}}[1]{{\overset{{#1}}{{=}}}}
\providecommand{{\xLongrightarrow}}[1]{{\overset{{#1}}{{\Longrightarrow}}}}
\providecommand{{\xLongleftarrow}}[1]{{\overset{{#1}}{{\Longleftarrow}}}}
\providecommand{{\xLongleftrightarrow}}[1]{{\overset{{#1}}{{\Longleftrightarrow}}}}
\providecommand{{\sec}}{{\operatorname{{sec}}}}
\providecommand{{\csc}}{{\operatorname{{csc}}}}
\providecommand{{\cosec}}{{\operatorname{{cosec}}}}
\providecommand{{\cosech}}{{\operatorname{{cosech}}}}
\providecommand{{\sech}}{{\operatorname{{sech}}}}
\providecommand{{\csch}}{{\operatorname{{csch}}}}
\providecommand{{\cosine}}{{\operatorname{{cosine}}}}
\setlist{{nosep,leftmargin=*}}
\graphicspath{{{{./}}{{images/}}{{../images/}}}}

\definecolor{{ebInfoFrame}}{{HTML}}{{3A6EA5}}
\definecolor{{ebInfoBack}}{{HTML}}{{F2F7FC}}
\definecolor{{ebVocabFrame}}{{HTML}}{{2A9D8F}}
\definecolor{{ebVocabBack}}{{HTML}}{{EEF9F7}}
\definecolor{{ebTipFrame}}{{HTML}}{{B87900}}
\definecolor{{ebTipBack}}{{HTML}}{{FFF7E6}}
\definecolor{{ebExerciseFrame}}{{HTML}}{{C44536}}
\definecolor{{ebExerciseBack}}{{HTML}}{{FFF1EE}}

\newtcolorbox{{infobox}}{{
  enhanced,
  breakable,
  colback=ebInfoBack,
  colframe=ebInfoFrame,
  title={{Reading profile}},
  fonttitle=\bfseries,
  boxrule=0.5pt,
  arc=1mm,
  left=2mm,
  right=2mm,
  top=1mm,
  bottom=1mm
}}

\newtcolorbox{{vocabbox}}{{
  enhanced,
  breakable,
  colback=ebVocabBack,
  colframe=ebVocabFrame,
  title={{Word power}},
  fonttitle=\bfseries,
  boxrule=0.6pt,
  arc=1mm,
  left=2mm,
  right=2mm,
  top=1.5mm,
  bottom=1.5mm
}}

\newtcolorbox{{tipbox}}{{
  enhanced,
  breakable,
  colback=ebTipBack,
  colframe=ebTipFrame,
  title={{Language tips}},
  fonttitle=\bfseries,
  boxrule=0.6pt,
  arc=1mm,
  left=2mm,
  right=2mm,
  top=1.5mm,
  bottom=1.5mm
}}

\newcommand{{\exerciseheading}}[1]{{%
  \begin{{tcolorbox}}[
    enhanced,
    colback=ebExerciseBack,
    colframe=ebExerciseFrame,
    boxrule=0.5pt,
    arc=1mm,
    left=2mm,
    right=2mm,
    top=1mm,
    bottom=1mm
  ]
  \textbf{{\textcolor{{ebExerciseFrame}}{{#1}}}}
  \end{{tcolorbox}}
}}
""".strip()


def apply_locked_template_body_contract(content: str) -> str:
    """Use only commands and environments already available in the locked template."""
    for character, replacement in LOCKED_TEMPLATE_SYMBOL_REPLACEMENTS.items():
        content = content.replace(character, replacement)
    content = content.replace(r"\begin{infobox}", r"\begin{tcolorbox}[grammarbox,title={Reading profile}]")
    content = content.replace(r"\end{infobox}", r"\end{tcolorbox}")
    content = content.replace(r"\begin{vocabbox}", r"\begin{tcolorbox}[vocabbox,title={Word power}]")
    content = content.replace(r"\end{vocabbox}", r"\end{tcolorbox}")
    content = content.replace(r"\begin{tipbox}", r"\begin{tcolorbox}[notebox,title={Language tips}]")
    content = content.replace(r"\end{tipbox}", r"\end{tcolorbox}")
    content = re.sub(
        r"\\exerciseheading\{(?P<title>(?:[^{}]|\{[^{}]*\})*)\}",
        lambda match: r"\subsection*{" + match.group("title") + "}",
        content,
    )
    content = re.sub(r"\{\\luceonArabicFont\s+([^{}]*)\}", r"\1", content)
    return content


def render_locked_template_main(
    *,
    content: str,
    title: str,
    subtitle: str,
    author: str,
    institute: str,
    date_text: str,
) -> str:
    template = read_text(BUNDLED_MAIN_TEMPLATE)
    values = {
        "title": title,
        "subtitle": subtitle,
        "author": author,
        "institute": institute,
        "date": date_text,
    }
    for command, value in values.items():
        template, count = re.subn(
            rf"(?m)^\\{command}\{{.*\}}$",
            lambda _match, command=command, value=value: rf"\{command}{{{value}}}",
            template,
            count=1,
        )
        if count != 1:
            raise ValueError(f"locked template metadata command missing or duplicated: {command}")
    marker_at = template.find(TEMPLATE_BODY_MARKER)
    document_end = template.rfind(r"\end{document}")
    if marker_at < 0 or document_end < marker_at:
        raise ValueError("locked template body marker is missing")
    insert_at = marker_at + len(TEMPLATE_BODY_MARKER)
    return template[:insert_at] + "\n\n" + content.strip() + "\n\n" + template[document_end:]


def build_elegantbook_main(
    title: str,
    subtitle: str,
    author: str,
    demo_images: bool,
    cover_image: str | None,
    logo_image: str | None,
    date_text: str,
    version: str,
    extrainfo: str,
    institute: str = "",
    content: str = "",
) -> str:
    return render_locked_template_main(
        content=content,
        title=title,
        subtitle=subtitle,
        author=author,
        institute=institute,
        date_text=date_text,
    )


def plain_text_cover_override(
    title: str,
    subtitle: str,
    author: str,
    date_text: str,
    version: str,
    extrainfo: str,
) -> str:
    """Avoid ElegantBook's example-image placeholder when no real cover is supplied."""
    subtitle_block = rf"    {{\Large\color{{darkgray}} {subtitle}\par}}" if subtitle else ""
    meta_rows = [item for item in (author, date_text, version) if item]
    meta_block = ""
    if meta_rows:
        meta_block = "\n".join(f"        {item}\\\\[0.4ex]" for item in meta_rows)
    extra_block = rf"    {{\color{{gray}}\small {extrainfo}}}" if extrainfo else ""
    template = r"""
\makeatletter
\renewcommand*{\maketitle}{%
\hypersetup{pageanchor=false}%
\pagenumbering{arabic}%
\begin{titlepage}
  \newgeometry{margin=1in}%
  \parindent=0pt%
  \vspace*{1.1in}%
  \begin{center}
    {\color{structurecolor}\rule{0.72\linewidth}{1.6pt}\par}
    \vspace{0.75in}
    \begin{minipage}{0.84\linewidth}
      \centering
      {\Huge\bfseries\color{black} __TITLE__\par}
    \end{minipage}
    \vspace{0.5in}
__SUBTITLE_BLOCK__
    \vspace{0.7in}
    {\normalsize\color{gray}
      \begin{tabular}{l}
__META_BLOCK__
      \end{tabular}}
    \vfill
__EXTRA_BLOCK__
  \end{center}
  \vspace*{0.5in}
\end{titlepage}
\restoregeometry
\thispagestyle{empty}}
\makeatother
""".strip()
    return (
        template.replace("__TITLE__", title)
        .replace("__SUBTITLE_BLOCK__", subtitle_block)
        .replace("__META_BLOCK__", meta_block)
        .replace("__EXTRA_BLOCK__", extra_block)
    )


def build_fallback_main(
    title: str,
    subtitle: str,
    author: str,
    demo_images: bool,
    date_text: str,
) -> str:
    return f"""{package_option_prelude(demo_images)}\\documentclass[UTF8,11pt,openany]{{ctexbook}}
\\title{{{title}\\\\{subtitle}}}
\\author{{{author}}}
\\date{{{date_text}}}

{common_preamble()}
\\setcounter{{tocdepth}}{{1}}

\\begin{{document}}
\\maketitle
\\frontmatter
\\tableofcontents
\\mainmatter
\\pagenumbering{{arabic}}
\\renewcommand{{\\thepage}}{{\\arabic{{page}}}}
\\fancyfoot[c]{{\\small\\arabic{{page}}}}
\\fancypagestyle{{plain}}{{\\renewcommand{{\\headrulewidth}}{{0pt}}\\fancyhf{{}}\\fancyfoot[c]{{\\small\\arabic{{page}}}}}}
\\input{{chapters/content.tex}}
\\end{{document}}
"""


def write_project(args: argparse.Namespace) -> None:
    input_path = Path(args.input).resolve()
    out_dir = Path(args.out_dir).resolve()
    chapters_dir = out_dir / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "images").mkdir(parents=True, exist_ok=True)

    source = read_text(input_path)
    content = transform_content(source, trusted_cleanlatex=args.trusted_cleanlatex)
    content = repair_misplaced_hline_alignment_tokens(content)
    content = normalize_image_includes(content, input_path, out_dir)
    content = remove_qr_ocr_captions(content, input_path, out_dir)
    content = normalize_table_cell_layout(content)
    content = separate_inline_table_images(content)
    content = allow_table_token_breaks(content)
    content = wrap_prose_set_descriptions(content)
    content = allow_long_inline_math_breaks(content)
    content = split_dense_formula_choice_lines(content)
    content = split_multi_set_assignment_math(content)
    content = strip_inline_delimiters_inside_display_math(content)
    content = split_long_set_definition_lines(content)
    content = wrap_bare_urls(content)
    content = allow_long_prose_breaks(content)
    content = merge_sparse_longtable_rows(content)
    content = scale_extreme_inline_math_spans(content)
    content = fit_image_dense_tabularx(content)
    content = fit_short_label_tabularx(content)
    content = fit_ultrawide_tabularx(content)
    content = fit_long_unbreakable_display_arrays(content)
    content = remove_dollar_only_table_cells_final(content)
    content = repair_malformed_array_endings(content)
    content = repair_missing_rowbreak_before_hline(content)
    content = repair_literal_set_fraction_numerators(content)
    content = move_array_end_out_of_text_command(content)
    content = strip_nested_math_dollars_from_array_text(content)
    content = repair_math_commands_inside_text_commands(content)
    content = replace_array_equation_tags_with_visible_labels(content)
    content = replace_bare_equation_tags_with_visible_annotations(content)
    content = split_joined_math_spacing_commands(content)
    content = wrap_cjk_text_in_final_inline_math(content)
    content = wrap_cjk_text_in_final_arrays(content)
    content = drop_unmatched_closing_braces_in_inline_math(content)
    content = repair_orphan_relation_superscripts_final(content)
    content = drop_unmatched_closing_braces_in_arrays(content)
    content = normalize_array_column_counts(content)
    content = normalize_nested_outer_array_columns(content)
    content = remove_stray_label_braces_after_array_specs(content)
    content = repair_rowbreaks_inside_text_commands(content)
    content = move_array_end_out_of_text_command(content)
    content = repair_resizebox_math_array_closures(content)
    content = drop_unmatched_closing_braces_on_single_line_arrays(content)
    content = split_multiple_tags_in_equation_blocks(content)
    content = split_merged_adjacent_figure_captions(content)
    if args.trusted_cleanlatex:
        content = remove_small_chapter_ornament_figures(content)
        content = compact_consecutive_tiny_figures(content)
        content = relocate_terminal_problem_figure(content)
        content = bind_terminal_figure_to_conclusion(content)
    if not args.trusted_cleanlatex:
        content = final_tex_safety_pass(content)
    if not args.trusted_cleanlatex:
        content = repair_parenthesized_frac_arguments(content)
        content = repair_exercise_heading_math_mode(content)
    content = normalize_full_width_block_indentation(content)
    content = repair_resizebox_math_array_closures(content)
    content = drop_unmatched_closing_braces_on_single_line_arrays(content)
    content = split_multiple_tags_in_equation_blocks(content)
    content = normalize_math_text_commands(content)
    content = repair_textcircled_math_symbols(content)
    content = replace_circled_equation_tags(content)
    content = repair_bare_boxed_before_rowbreak(content)
    content = repair_bare_display_math_blanks(content)
    content = repair_underline_placeholders(content)
    content = remove_breaks_after_sizing_commands(content)
    content = repair_final_nested_math_and_text_delimiters(content)
    content = split_dense_answer_branches(content)
    content = normalize_locked_body_unicode(content)
    content = wrap_ipa_runs(content)
    content = wrap_arabic_runs(content)
    content = content.replace("\ufe0f", "")
    content = apply_locked_template_body_contract(content)

    write_text_lf(chapters_dir / "content.tex", content)
    title = latex_escape_text(args.title)
    subtitle = latex_escape_text(args.subtitle)
    author = latex_escape_text(args.author)
    institute = latex_escape_text(args.institute)
    date_text = latex_escape_text(args.date)
    version = latex_escape_text(args.version)
    extrainfo = latex_escape_text(args.extrainfo)
    write_text_lf(
        out_dir / "main.tex",
        build_elegantbook_main(
            title=title,
            subtitle=subtitle,
            author=author,
            demo_images=args.demo_images,
            cover_image=None,
            logo_image=None,
            date_text=date_text,
            version=version,
            extrainfo=extrainfo,
            institute=institute,
            content=content,
        ),
    )
    write_text_lf(
        out_dir / "main-fallback.tex",
        build_fallback_main(title, subtitle, author, args.demo_images, args.date),
    )
    bundled_class_copied = False
    if BUNDLED_ELEGANTBOOK_CLASS.exists():
        shutil.copy2(BUNDLED_ELEGANTBOOK_CLASS, out_dir / "elegantbook.cls")
        bundled_class_copied = True
    figure_dir = out_dir / "figure"
    figure_dir.mkdir(parents=True, exist_ok=True)
    for figure_name in ("logo.jpg", "cover.jpg"):
        shutil.copy2(BUNDLED_FIGURE_DIR / figure_name, figure_dir / figure_name)
    bundled_cjk_font = bundle_font(out_dir, "LUCEON_CJK_FONT_PATH", NOTO_SERIF_CJK_CANDIDATES, NOTO_SERIF_CJK_FILENAME)
    bundled_symbol_font = bundle_font(out_dir, "LUCEON_SYMBOL_FONT_PATH", NOTO_SYMBOLS_CANDIDATES, NOTO_SYMBOLS_FILENAME)

    missing = copy_referenced_images(input_path, out_dir, content)
    notes = [
        f"Source: {input_path}",
        "Generated files: main.tex, main-fallback.tex, chapters/content.tex",
        "Compile with XeLaTeX. Use main.tex as the ElegantBook deliverable.",
    ]
    if bundled_class_copied:
        notes.append("Bundled class: elegantbook.cls copied into the project root.")
    else:
        notes.append("Bundled class missing: install elegantbook.cls or compile main-fallback.tex for validation only.")
    if bundled_cjk_font:
        notes.append(f"Bundled CJK font: fonts/{NOTO_SERIF_CJK_FILENAME} (SC face index 2).")
    else:
        notes.append("Bundled CJK font unavailable: strict compile diagnostics must verify runtime glyph coverage.")
    if bundled_symbol_font:
        notes.append(f"Bundled symbol font: fonts/{NOTO_SYMBOLS_FILENAME}.")
    else:
        notes.append("Bundled symbol font unavailable: strict compile diagnostics must verify runtime symbol coverage.")
    if args.demo_images:
        notes.append("Image compile mode: graphicx demo mode is enabled.")
    notes.append("Locked figure assets: figure/logo.jpg and figure/cover.jpg.")
    if missing:
        notes.append("")
        notes.append("Missing image files referenced by the source:")
        notes.extend(f"- {ref}" for ref in missing)
    (out_dir / "build-notes.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")

    print(f"Created ElegantBook project: {out_dir}")
    print(f"Content file: {chapters_dir / 'content.tex'}")
    print(f"Missing images: {len(missing)}")


def wrap_arabic_runs(text: str) -> str:
    """Apply an Arabic-capable font to contiguous Arabic text without leaking font state."""
    arabic = r"\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff"
    pattern = re.compile(rf"(?P<body>[{arabic}]+(?:[ \t]+[{arabic}]+)*)")
    return pattern.sub(lambda match: rf"{{\luceonArabicFont {match.group('body')}}}", text)


def bundle_cjk_font(out_dir: Path) -> bool:
    return bundle_font(out_dir, "LUCEON_CJK_FONT_PATH", NOTO_SERIF_CJK_CANDIDATES, NOTO_SERIF_CJK_FILENAME)


def bundle_font(out_dir: Path, environment_key: str, defaults: tuple[Path, ...], filename: str) -> bool:
    configured = os.getenv(environment_key, "").strip()
    candidates = ((Path(configured),) if configured else ()) + defaults
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        return False
    font_dir = out_dir / "fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, font_dir / filename)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="CleanLaTeX source file")
    parser.add_argument("--out-dir", required=True, help="Output project directory")
    parser.add_argument("--title", default="CleanLaTeX Reader", help="Book title")
    parser.add_argument("--subtitle", default=TEMPLATE_METADATA_DEFAULTS["subtitle"], help="Book subtitle")
    parser.add_argument("--author", default=TEMPLATE_METADATA_DEFAULTS["author"], help="Book author")
    parser.add_argument("--institute", default=TEMPLATE_METADATA_DEFAULTS["institute"], help="Book institute")
    parser.add_argument("--date", default=TEMPLATE_METADATA_DEFAULTS["date"], help="Date text for the title page")
    parser.add_argument("--version", default="Reviewed", help="ElegantBook version field")
    parser.add_argument(
        "--extrainfo",
        default="",
        help="ElegantBook extra information field",
    )
    parser.add_argument("--cover-image", help="Explicit cover image to copy and use with \\cover{...}")
    parser.add_argument("--logo-image", help="Explicit logo image to copy and use with \\logo{...}")
    parser.add_argument(
        "--trusted-cleanlatex",
        action="store_true",
        help="Preserve math from the canonical Markdown bridge and skip OCR-oriented safety rewrites",
    )
    parser.add_argument(
        "--demo-images",
        action="store_true",
        help="Use graphicx demo mode so the project compiles without local image files",
    )
    return parser.parse_args()


if __name__ == "__main__":
    write_project(parse_args())
