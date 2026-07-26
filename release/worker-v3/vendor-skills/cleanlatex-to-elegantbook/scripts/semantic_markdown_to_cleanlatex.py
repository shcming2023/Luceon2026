#!/usr/bin/env python3
"""Convert clean Markdown plus optional semantic annotation into CleanLaTeX."""

import argparse
import json
import re
import shutil
from html import unescape
from html.parser import HTMLParser
from pathlib import Path


SPECIALS = {
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

CIRCLED_DIGIT_MAP = {chr(0x2460 + index): str(index + 1) for index in range(20)}
CIRCLED_DIGIT_RE = re.compile(
    "[" + "".join(re.escape(ch) for ch in CIRCLED_DIGIT_MAP) + "]"
)
BOX_SYMBOL_MAP = {
    chr(0x25A1): r"\(\square\)",
    chr(0x2610): r"\(\square\)",
}
BOX_SYMBOL_RE = re.compile("[" + "".join(re.escape(ch) for ch in BOX_SYMBOL_MAP) + "]")
SUPERSCRIPT_GROUP_PATTERN = r"\^\{(?:[^{}]|\{[^{}]*\})*\}"


def blank_rule_for(length):
    width = max(2.0, min(8.0, length * 0.45))
    return rf"\rule{{{width:.1f}em}}{{0.4pt}}"


def protect_blank_runs(text):
    placeholders = []

    def store(command):
        placeholders.append(command)
        return f"\uE000{len(placeholders) - 1}\uE000"

    def escaped_repl(match):
        return store(blank_rule_for(match.group(0).count(r"\_")))

    def plain_repl(match):
        return store(blank_rule_for(len(match.group(0))))

    def circled_repl(match):
        return store(rf"\textcircled{{{CIRCLED_DIGIT_MAP[match.group(0)]}}}")

    protected = CIRCLED_DIGIT_RE.sub(circled_repl, text)
    protected = BOX_SYMBOL_RE.sub(lambda match: store(BOX_SYMBOL_MAP[match.group(0)]), protected)
    protected = re.sub(r"(?:\\_){2,}", escaped_repl, protected)
    protected = re.sub(r"_{3,}", plain_repl, protected)
    return protected, placeholders


def normalize_math_inline_artifacts(text):
    text = unwrap_text_wrapped_array_bodies(text)
    text = text.replace(r"\_", "_")
    text = text.replace(r"\}}", r"\}")
    text = text.replace(r"\{{", r"\{")
    text = re.sub(r"(?<!\\)\$(?=\s*\d)", r"\\$", text)
    text = re.sub(r"(\\[A-Za-z]+)\s*\^\s*$", r"\1", text)
    text = re.sub(r"(\\[A-Za-z]+)\s*_\s*$", r"\1", text)
    if re.match(r"^\s*\^\s*$", text) or re.match(r"^\s*\^\{\s*\^\s*\}\s*$", text):
        return r"\text{\textasciicircum{}}"
    text = re.sub(r"^\s*\^\{\\wedge\}", r"\\wedge", text)
    text = re.sub(r"^\s*\^\s+(?=\\?[A-Za-z])", "", text)
    text = CIRCLED_DIGIT_RE.sub(
        lambda match: rf"\text{{\textcircled{{{CIRCLED_DIGIT_MAP[match.group(0)]}}}}}",
        text,
    )
    text = BOX_SYMBOL_RE.sub(r"\\square", text)
    if r"\begin{array}" not in text:
        text = re.sub(r"\\hline\b", r"\\quad", text)
    text = re.sub(r"([A-Za-z0-9)\]}])\\([A-Z])\b", r"\1\\backslash \2", text)
    text = re.sub(
        r"(^|(?<=[\s+\-=,(]))_(?=\s*(?:$|\\|[+\-=,)&]))",
        lambda match: match.group(1) + blank_rule_for(3),
        text,
    )
    text = re.sub(
        r"\\frac\s*\{(\\boxed\s*\{[^{}\n]*\})\s*\{",
        r"\\frac{\1}{",
        text,
    )
    text = re.sub(
        r"\\frac\s*\{\s*(\\boxed\s*\{[^{}\n]*\})\s*\{(\\boxed\s*\{[^{}\n]*\}|[^{}$\s\\&]+)",
        r"\\frac{\1}{\2}",
        text,
    )
    text = re.sub(
        r"\\frac\s*\{(\\frac\s*\{[^{}\n]+\}\s*\{[^{}\n]+\})\}(?!\s*\{)",
        r"\\frac{\1}{\\square}",
        text,
    )
    text = re.sub(r"\\frac\s*\{([^{}\n]+)\}(?!\s*\{)", r"\\frac{\1}{\\square}", text)
    text = re.sub(r"(^|(?<=[\s+\-=,(]))_\{([A-Za-z][A-Za-z0-9]*)\}", r"\1\2", text)
    text = re.sub(r"(^|(?<=[\s+\-=,(]))_([A-Za-z])", r"\1\2", text)
    text = re.sub(r"(^|(?<=[\s+\-=,(]))_(?=\{?\d)", r"\1{}_", text)
    text = re.sub(r"_{2,}", lambda match: blank_rule_for(len(match.group(0))), text)
    text = re.sub(r"^(\s*)\^", r"\1{}^", text)
    while True:
        repaired = re.sub(
            rf"(\^(?:\{{(?:[^{{}}]|\{{[^{{}}]*\}})*\}}|[A-Za-z0-9]))\s*(?=\^)",
            r"\1{}",
            text,
        )
        if repaired == text:
            break
        text = repaired
    while True:
        repaired = re.sub(
            r"(_(?:\{(?:[^{}]|\{[^{}]*\})*\}|[A-Za-z0-9]))\s*(?=_)",
            r"\1{}",
            text,
        )
        if repaired == text:
            return balance_unescaped_math_braces(text)
        text = repaired


def unwrap_text_wrapped_array_bodies(text):
    opener_re = re.compile(r"(\\begin\{array\}\{[^{}\n]*\})\s*\\text\s*\{\s*")
    match = opener_re.search(text)
    if match and not text[: match.start()].strip() and r"\end{array}" in text[match.end() :]:
        end_array = text.rfind(r"\end{array}")
        final_brace = text.rfind("}")
        if end_array != -1 and final_brace > end_array:
            return text[: match.start()] + match.group(1) + " " + text[match.end() : final_brace] + text[final_brace + 1 :]
    return text


def balance_unescaped_math_braces(text):
    balance = 0
    for index, char in enumerate(text):
        escaped = index > 0 and text[index - 1] == "\\"
        if escaped:
            continue
        if char == "{":
            balance += 1
        elif char == "}" and balance > 0:
            balance -= 1
    if 0 < balance <= 4:
        text += "}" * balance
    return text


def restore_escaped_math_delimiters(text):
    """Turn OCR-style escaped math dollars back into math only when the payload is math-like."""

    simple_command = (
        r"(?:\\frac\{[^{}\n]+\}\{[^{}\n]+\}"
        r"|\\sqrt\{[^{}\n]+\}"
        r"|\\underbrace\s*\{[^{}\n]+\}\s*(?:_|\\_)\s*\{[^{}\n]+\}"
        r"|\\[A-Za-z]+\{[^{}\n]+\})"
    )

    def math_like(value):
        return bool(re.search(r"\\[A-Za-z]+|[<>=^{}]", value))

    def clean_math_inner(value):
        return normalize_math_inline_artifacts(value)

    def latex_delimited_repl(match):
        inner = match.group(1)
        if not math_like(inner):
            return match.group(0)
        inner = inner.replace(r"\(", "").replace(r"\)", "")
        inner = inner.replace(r"\[", "").replace(r"\]", "")
        return f"${clean_math_inner(inner)}$"

    def mixed_close_repl(match):
        inner = match.group(1)
        if math_like(inner):
            return f"${clean_math_inner(inner)}$"
        return match.group(0)

    def missing_close_repl(match):
        return f"${clean_math_inner(match.group(1))}${match.group(2)}"

    def dangling_close_repl(match):
        return f"${clean_math_inner(match.group(1))}$"

    def repl(match):
        inner = match.group(1)
        if inner.strip().endswith((",", ";", ":")):
            return match.group(0)
        if math_like(inner):
            return f"${clean_math_inner(inner)}$"
        return match.group(0)

    text = re.sub(r"\\\((.+?)\\\)", latex_delimited_repl, text)
    text = re.sub(r"\\\[(.+?)\\\]", latex_delimited_repl, text)
    text = re.sub(r"\\\$([^$\n]{1,240}?)\\\)", mixed_close_repl, text)
    text = re.sub(r"\\\$([^$\n]{1,240}?)\$", repl, text)
    text = re.sub(r"\\\$([^$\n]{1,240}?)\\\$", repl, text)
    text = re.sub(rf"\\\$({simple_command})([,.;:])", missing_close_repl, text)
    text = re.sub(r"\\\$([^$\n]{1,240}?)\\\$", repl, text)
    return re.sub(rf"({simple_command})\\[)\]]", dangling_close_repl, text)


def escape_text(text):
    text = str(text or "")
    text = restore_escaped_math_delimiters(text)
    dollar_placeholders = []
    literal_placeholders = []

    def store_dollar(match):
        dollar_placeholders.append(r"\$")
        return f"\uE100{len(dollar_placeholders) - 1}\uE100"

    def store_literal(match):
        literal_placeholders.append(match.group(1))
        return f"\uE101{len(literal_placeholders) - 1}\uE101"

    text = re.sub(r"\\([*-])", store_literal, text)
    text = re.sub(r"\\\$", store_dollar, text)
    parts = re.split(r"(\$[^$\n]+\$)", text)
    out = []
    for part in parts:
        if part.startswith("$") and part.endswith("$"):
            out.append("$" + normalize_math_inline_artifacts(part[1:-1]) + "$")
            continue
        protected, placeholders = protect_blank_runs(part)
        escaped = "".join(SPECIALS.get(ch, ch) for ch in protected)
        for index, command in enumerate(placeholders):
            escaped = escaped.replace(f"\uE000{index}\uE000", command)
        for index, literal in enumerate(literal_placeholders):
            escaped = escaped.replace(f"\uE101{index}\uE101", literal)
        for index, command in enumerate(dollar_placeholders):
            escaped = escaped.replace(f"\uE100{index}\uE100", command)
        out.append(escaped)
    return "".join(out)


def escape_markdown_segments(text):
    parts = re.split(r"(\$[^$\n]+\$)", text)
    out = []
    for part in parts:
        if not part:
            continue
        if part.startswith("$") and part.endswith("$"):
            out.append("$" + normalize_math_inline_artifacts(part[1:-1]) + "$")
            continue
        out.append(escape_markdown_plain_text(part))
    return "".join(out)


def escape_markdown_text(text):
    """Escape text while preserving simple Markdown emphasis as LaTeX markup."""
    text = str(text or "")
    text = restore_escaped_math_delimiters(text)
    dollar_placeholders = []

    def store_dollar(match):
        dollar_placeholders.append(r"\$")
        return f"\uE300{len(dollar_placeholders) - 1}\uE300"

    text = re.sub(r"\\\$", store_dollar, text)
    strong = re.match(r"^\*\*(.{1,3000})\*\*$", text)
    escaped_strong = re.match(r"^\\\*\\\*(.{1,3000})\\\*\\\*$", text)
    if strong or escaped_strong:
        content = (strong or escaped_strong).group(1).strip()
        escaped = r"\textbf{" + escape_markdown_segments(content) + "}"
    else:
        escaped = escape_markdown_segments(text)
    for index, command in enumerate(dollar_placeholders):
        escaped = escaped.replace(f"\uE300{index}\uE300", command)
    return escaped


def escape_markdown_plain_text(text):
    placeholders = []

    def store(command):
        placeholders.append(command)
        return f"\uE200{len(placeholders) - 1}\uE200"

    def strong_repl(match):
        content = match.group(1).strip()
        return store(r"\textbf{" + escape_text(content) + "}")

    def emph_repl(match):
        content = match.group(1).strip()
        return store(r"\emph{" + escape_text(content) + "}")

    protected = re.sub(r"\\\*\\\*([^*\n]{1,3000})\\\*\\\*", strong_repl, text)
    protected = re.sub(r"\\\*([^*\n]{1,400})\\\*", emph_repl, protected)
    protected = re.sub(r"\\([*-])", lambda match: store(match.group(1)), protected)
    protected = re.sub(r"\*\*([^*\n]{1,3000})\*\*", strong_repl, protected)
    protected = re.sub(r"(?<!\*)\*([^*\n]{1,400})\*(?!\*)", emph_repl, protected)
    escaped = escape_text(protected)
    for index, command in enumerate(placeholders):
        escaped = escaped.replace(f"\uE200{index}\uE200", command)
    return escaped


def markdown_emphasis_payload(line):
    match = re.match(r"^\*{1,2}(.{1,200}?)\*{1,2}$", line.strip())
    if not match:
        return ""
    return match.group(1).strip()


def normalize_wrapped_inline_blocks(markdown):
    """Collapse MinerU-wrapped image syntax and caption emphasis into one line."""
    lines = markdown.splitlines()
    out = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("![") and "](images/" not in stripped:
            collected = [stripped]
            next_index = index + 1
            while next_index < len(lines) and next_index <= index + 8:
                collected.append(lines[next_index].strip())
                if "](images/" in lines[next_index]:
                    out.append(re.sub(r"\s+", " ", " ".join(part for part in collected if part)).strip())
                    index = next_index + 1
                    break
                next_index += 1
            else:
                out.append(lines[index])
                index += 1
            continue

        marker = "**" if stripped.startswith("**") else "*" if stripped.startswith("*") else ""
        if marker and not stripped.startswith(marker + " ") and not stripped.endswith(marker):
            collected = [stripped]
            next_index = index + 1
            while next_index < len(lines) and next_index <= index + 8:
                collected.append(lines[next_index].strip())
                if lines[next_index].strip().endswith(marker):
                    out.append(re.sub(r"\s+", " ", " ".join(part for part in collected if part)).strip())
                    index = next_index + 1
                    break
                next_index += 1
            else:
                out.append(lines[index])
                index += 1
            continue

        out.append(lines[index])
        index += 1
    return "\n".join(out)


def clean_heading(text):
    text = re.sub(r"<[^>]+>", "", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return escape_text(text)


def image_width(src):
    lower = src.lower()
    if re.search(r"(qr|code|icon|logo)", lower):
        return "0.22\\textwidth"
    return "0.78\\textwidth"


class HTMLTableParser(HTMLParser):
    """Small tolerant table parser for MinerU/Popo inline HTML tables."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.current_row = None
        self.current_cell = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attr = dict(attrs)
        if tag == "tr":
            self.current_row = []
        elif tag in {"td", "th"} and self.current_row is not None:
            self.current_cell = []
        elif tag == "img" and self.current_cell is not None:
            src = attr.get("src", "")
            if src:
                self.current_cell.append(("latex", rf"\includegraphics[width=0.95\linewidth]{{{src}}}"))
        elif tag == "br" and self.current_cell is not None:
            self.current_cell.append(("latex", r"\newline "))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self.current_cell is not None and self.current_row is not None:
            cell = re.sub(r"\s+", " ", self.render_cell(self.current_cell)).strip()
            self.current_row.append(cell)
            self.current_cell = None
        elif tag == "tr" and self.current_row is not None:
            self.rows.append(self.current_row)
            self.current_row = None

    def handle_data(self, data):
        if self.current_cell is not None:
            text = unescape(data)
            if text.strip():
                self.current_cell.append(("text", text))

    @staticmethod
    def render_cell(parts):
        rendered = []
        text_buffer = []

        def flush_text():
            if text_buffer:
                rendered.append(escape_text("".join(text_buffer)))
                text_buffer.clear()

        for kind, value in parts:
            if kind == "text":
                text_buffer.append(value)
            else:
                flush_text()
                rendered.append(value)
        flush_text()
        return "".join(rendered)


def html_table_to_latex(table_html):
    parser = HTMLTableParser()
    try:
        parser.feed(table_html)
        parser.close()
    except Exception:
        return "\n".join([r"\begin{verbatim}", table_html, r"\end{verbatim}", ""])

    rows = [[cell for cell in row] for row in parser.rows if any(cell.strip() for cell in row)]
    if not rows:
        return ""
    max_cols = max(len(row) for row in rows)
    rows = [row + [""] * (max_cols - len(row)) for row in rows]
    size = r"\scriptsize" if max_cols > 5 else r"\small"
    tabcolsep = "2pt" if max_cols > 5 else "4pt"
    out = [
        "{" + size,
        rf"\setlength{{\tabcolsep}}{{{tabcolsep}}}",
        r"\renewcommand{\arraystretch}{1.15}",
        rf"\begin{{tabularx}}{{\textwidth}}{{|*{{{max_cols}}}{{>{{\raggedright\arraybackslash}}X|}}}}",
        r"\hline",
    ]
    for row in rows:
        out.append(" & ".join(cell if cell else r"~" for cell in row) + r" \\ \hline")
    out.extend([r"\end{tabularx}", "}", ""])
    return "\n".join(out)


def flush_list(lines, list_state):
    if not list_state:
        return
    kind = list_state["kind"]
    lines.append(rf"\end{{{kind}}}")
    lines.append("")
    list_state.clear()


def emit_paragraph(lines, paragraph):
    if not paragraph:
        return
    text = " ".join(p.strip() for p in paragraph if p.strip())
    if text:
        lines.append(escape_markdown_text(text))
        lines.append("")
    paragraph.clear()


def convert_markdown(markdown):
    markdown = normalize_wrapped_inline_blocks(markdown)
    out = []
    paragraph = []
    list_state = {}
    in_fenced = False
    in_display_math = False
    html_table = []
    skip_next_image_caption = False

    for raw in markdown.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if skip_next_image_caption:
            caption = markdown_emphasis_payload(stripped)
            if caption:
                skip_next_image_caption = False
                continue
            if stripped:
                skip_next_image_caption = False

        if stripped == "$$":
            emit_paragraph(out, paragraph)
            flush_list(out, list_state)
            out.append("$$")
            in_display_math = not in_display_math
            continue
        if in_display_math:
            out.append(normalize_math_inline_artifacts(line))
            continue
        if stripped.startswith("$$") and stripped.endswith("$$") and len(stripped) > 4:
            emit_paragraph(out, paragraph)
            flush_list(out, list_state)
            out.append("$$" + normalize_math_inline_artifacts(stripped[2:-2]) + "$$")
            out.append("")
            continue

        if stripped.startswith("```"):
            emit_paragraph(out, paragraph)
            flush_list(out, list_state)
            if in_fenced:
                out.append(r"\end{verbatim}")
                out.append("")
                in_fenced = False
            else:
                out.append(r"\begin{verbatim}")
                in_fenced = True
            continue
        if in_fenced:
            out.append(line)
            continue

        if html_table:
            html_table.append(line)
            if "</table>" in stripped.lower():
                out.append(html_table_to_latex("\n".join(html_table)))
                html_table = []
            continue
        if stripped.lower().startswith("<table"):
            emit_paragraph(out, paragraph)
            flush_list(out, list_state)
            html_table = [line]
            if "</table>" in stripped.lower():
                out.append(html_table_to_latex("\n".join(html_table)))
                html_table = []
            continue

        page_comment = re.match(r"<!--\s*page_idx:\s*([^>]+?)\s*-->", stripped)
        if page_comment:
            emit_paragraph(out, paragraph)
            flush_list(out, list_state)
            out.append(f"% source_page_idx: {page_comment.group(1)}")
            continue

        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", stripped)
        if heading:
            emit_paragraph(out, paragraph)
            flush_list(out, list_state)
            level = len(heading.group(1))
            title = clean_heading(heading.group(2))
            if level == 1:
                out.append(rf"\section{{{title}}}")
            elif level == 2:
                out.append(rf"\subsection{{{title}}}")
            else:
                out.append(rf"\subsubsection{{{title}}}")
            out.append("")
            continue

        image = re.match(
            r"^!\[(.*?)\]\((images/[^)]+)\)\s*(?:(?P<marker>\*{1,2})(?P<caption>.{1,300}?)(?P=marker))?\s*$",
            stripped,
        )
        if image:
            emit_paragraph(out, paragraph)
            flush_list(out, list_state)
            caption = (image.group("caption") or image.group(1)).strip()
            alt = clean_heading(caption)
            src = image.group(2)
            out.extend([
                r"\begin{figure}[H]",
                r"\centering",
                rf"\includegraphics[width={image_width(src)}]{{{src}}}",
            ])
            if alt:
                out.append(rf"\caption{{{alt}}}")
            out.extend([r"\end{figure}", ""])
            skip_next_image_caption = True
            continue

        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        ordered = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        if bullet or ordered:
            emit_paragraph(out, paragraph)
            kind = "enumerate" if ordered else "itemize"
            if not list_state or list_state.get("kind") != kind:
                flush_list(out, list_state)
                out.append(rf"\begin{{{kind}}}")
                list_state["kind"] = kind
            out.append(rf"\item {escape_markdown_text((ordered or bullet).group(1))}")
            continue

        if not stripped:
            emit_paragraph(out, paragraph)
            flush_list(out, list_state)
            continue

        paragraph.append(stripped)

    emit_paragraph(out, paragraph)
    flush_list(out, list_state)
    if html_table:
        out.append(html_table_to_latex("\n".join(html_table)))
    return "\n".join(out).rstrip() + "\n"


def read_document_title(annotation_dir, fallback):
    if not annotation_dir:
        return fallback
    doc_path = Path(annotation_dir) / "document.json"
    if not doc_path.exists():
        return fallback
    try:
        data = json.loads(doc_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback
    return data.get("book_id") or fallback


def copy_images(markdown_path, out_path):
    src_dir = markdown_path.parent / "images"
    dst_dir = out_path.parent / "images"
    if not src_dir.exists():
        return 0
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    shutil.copytree(src_dir, dst_dir)
    return sum(1 for p in dst_dir.iterdir() if p.is_file())


def main():
    parser = argparse.ArgumentParser(description="Convert clean Markdown/semantic assets into CleanLaTeX.")
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--annotation-dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--copy-images", action="store_true")
    args = parser.parse_args()

    markdown = args.markdown.read_text(encoding="utf-8")
    title = args.title or read_document_title(args.annotation_dir, args.markdown.parent.name)
    body = convert_markdown(markdown)
    tex = "\n".join([
        r"\documentclass[11pt]{article}",
        r"\usepackage[margin=1in]{geometry}",
        r"\usepackage{graphicx}",
        r"\usepackage{float}",
        r"\usepackage{longtable}",
        r"\usepackage{array}",
        r"\usepackage{booktabs}",
        r"\usepackage{amsmath,amssymb}",
        r"\usepackage{hyperref}",
        rf"\title{{{clean_heading(title)}}}",
        r"\author{}",
        r"\date{}",
        r"\begin{document}",
        r"\maketitle",
        "",
        body,
        r"\end{document}",
        "",
    ])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(tex, encoding="utf-8")
    copied = copy_images(args.markdown, args.out) if args.copy_images else 0
    print(json.dumps({
        "out": str(args.out),
        "title": title,
        "copied_images": copied,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
