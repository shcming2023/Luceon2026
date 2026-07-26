#!/usr/bin/env python3
"""Read-only diagnostic audit for generated ElegantBook LaTeX projects.

Historical mutation helpers remain in this module for regression archaeology,
but the public CLI accepts only audit mode and never calls them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import time
import zipfile
from collections import Counter
from itertools import count
from pathlib import Path


SKILL_SCHEMA = "refine-elegantbook-latex/report/v1"
BLOCK_MODEL_SCHEMA = "refine-elegantbook-latex/block-model/v1"
EDITORIAL_DECISIONS_SCHEMA = "refine-elegantbook-latex/editorial-decisions/v1"
DEFAULT_RENDER_DPI = 110
RUNNING_HEADER_RE = re.compile(
    r"^(?:CAMBRIDGE\s+IGCSE.*COURSEBOOK|CONTINUED|[IVX]+\s+Part\s+\d+\b)\s*$",
    re.I,
)
TIP_HEADING_RE = re.compile(r"^(READING|LANGUAGE|SPEAKING|WRITING|LISTENING|EXAM|STUDY)\s+TIP\b(?::\s*(.*))?$", re.I)
FOCUS_HEADING_RE = re.compile(
    r"^(LANGUAGE|GRAMMAR|VOCABULARY|READING|WRITING|SPEAKING|LISTENING)\s+FOCUS\b(?::\s*(.*))?$",
    re.I,
)
ACTIVITY_HEADING_RE = re.compile(
    r"^[A-H]\s+(?:Watch|Speaking|Reading|Writing|Listening|Vocabulary|Grammar|Pronunciation|Project|Practice|Before|After)\b.{0,80}$",
    re.I,
)
SPECIAL_HEADING_COMMANDS = {
    "LEARNING INTENTIONS": "learningheading",
    "CHECK YOUR PROGRESS": "progressheading",
    "REFLECTION": "reflectionheading",
    "SELF-CHECK": "progressheading",
    "$SELF-CHECK$": "progressheading",
    "BEFORE YOU START": "learningheading",
    "BEFORE READING": "readingheading",
    "WHILE READING": "readingheading",
    "AFTER READING": "readingheading",
    "READING": "readingheading",
    "BEFORE LISTENING": "stageheading",
    "WHILE LISTENING": "stageheading",
    "AFTER LISTENING": "stageheading",
    "LISTENING": "stageheading",
    "BEFORE SPEAKING": "stageheading",
    "WHILE SPEAKING": "stageheading",
    "AFTER SPEAKING": "stageheading",
    "SPEAKING": "stageheading",
    "SPEAKING AND LISTENING": "stageheading",
    "BEFORE WRITING": "stageheading",
    "WHILE WRITING": "stageheading",
    "AFTER WRITING": "stageheading",
    "WRITING": "stageheading",
    "VOCABULARY": "stageheading",
    "GRAMMAR": "stageheading",
    "PRACTISE": "stageheading",
    "PRACTICE": "stageheading",
    "CHALLENGE": "examheading",
    "REVIEW AND REFLECTION": "reflectionheading",
    "DO YOU REMEMBER?": "tipheading",
    "HINT": "tipheading",
    "KEY TERMS": "focusheading",
    "DEVELOP LANGUAGE SKILLS": "focusheading",
    "STUDENTS SPEAK": "activityheading",
    "$EXAM-STYLE QUESTION$": "examheading",
    "EXAM-STYLE QUESTION": "examheading",
}
ACTIVITY_BOX_RE = re.compile(r"^\\activityheading\{[^{}]+\}\s*$")
FOCUS_BOX_RE = re.compile(r"^\\focusheading\{[^{}]+\}\s*$")
ACTIVITY_STAGE_RE = re.compile(
    r"^\\(?:learningheading|tipheading|focusheading|reflectionheading|progressheading|readingheading|stageheading|examheading)\{[^{}]+\}\s*$"
)
TASK_SCOPE_RE = re.compile(
    r"^\\(?:activityheading|learningheading|tipheading|focusheading|reflectionheading|progressheading|readingheading|stageheading|examheading)\{[^{}]+\}\s*$"
)
MAJOR_BOUNDARY_RE = re.compile(r"^\\(?:part|chapter|section|subsection)(?:\*|\[[^\]]*\])?\{")
SOURCE_NUMBERED_HEADING_RE = re.compile(
    r"^\\(?P<level>section|subsection)\{(?P<title>\d{1,2}\.\d{1,2}\s+[^{}]{2,180})\}\s*$"
)
SOURCE_NUMBERED_STAR_HEADING_RE = re.compile(
    r"^\\(?P<level>section|subsection)\*\{(?P<title>\d{1,2}\.\d{1,2}\s+[^{}]{2,180})\}\s*$"
)
SOURCE_STRUCTURAL_HEADING_RE = re.compile(
    r"^\\(?P<level>section|subsection)\{"
    r"(?P<title>(?:Unit\s+\d{1,3}(?:\s*:\s*[^{}]{1,120})?|"
    r"Part\s+\d{1,3}(?:\s+[^{}]{1,120})?|"
    r"Appendix\s*\d{0,3}(?:\s*:\s*[^{}]{1,120})?))"
    r"\}\s*$",
    re.I,
)
SOURCE_STRUCTURAL_STAR_HEADING_RE = re.compile(
    r"^\\(?P<level>section|subsection)\*\{"
    r"(?P<title>(?:Unit\s+\d{1,3}(?:\s*:\s*[^{}]{1,120})?|"
    r"Part\s+\d{1,3}(?:\s+[^{}]{1,120})?|"
    r"Appendix\s*\d{0,3}(?:\s*:\s*[^{}]{1,120})?))"
    r"\}\s*$",
    re.I,
)
SOURCE_STRUCTURAL_CHAPTER_TITLE_RE = re.compile(
    r"^\\chapter(?P<opt>\[[^\]]*\])?\{"
    r"(?P<title>Chapter\s+(?P<number>\d{1,3})(?:\s*[:.\-]\s*|\s+)(?P<rest>[^{}]{2,180}))"
    r"\}\s*$",
    re.I,
)
TOP_ITEM_RE = re.compile(r"^(\d{1,2})\s+(.+)$")
LETTER_ITEM_RE = re.compile(r"^([a-j])\s+(.+)$")
CHOICE_ITEM_RE = re.compile(r"^([A-D])\s+(.+)$")
READING_TEXT_LABEL_RE = re.compile(r"^Text\s+\d+\.\d+\s*$", re.I)
READING_PARAGRAPH_RE = re.compile(r"^\[(\d{1,2})\]\s+(.+)$")
READING_PARAGRAPH_MARKER_RE = re.compile(r"(?:^|\s)\[(\d{1,2})\]\s+")
DIGITAL_TEXT_COPY_RE = re.compile(
    r"^You can download a copy of Text\s+\d+\.\d+\s+from the Digital Coursebook\.?$",
    re.I,
)
VISIBLE_LITERAL_FIGURE_RE = re.compile(
    r"\(\s*figure\s*\)\s*"
    r"\[H\]\s*"
    r"\[width=[^\]]+\]\s*"
    r"\(\s*images/[^)\s]+\s*\)\s*"
    r"(?:\\par\b)?",
    re.I,
)
VISIBLE_LITERAL_FIGURE_TAIL_RE = re.compile(
    r"(?:\\par\b\s*)?"
    r"tion\s*\(\s*[^)]{0,120}\s*\)\s*"
    r"\(\s*figure\s*\)",
    re.I,
)
VISIBLE_GENERIC_IMAGE_CAPTION_RESIDUE_RE = re.compile(
    r"\btion\s*(?:\(\s*image\s*\)|image\b)\s*",
    re.I,
)
UNSAFE_PAGE_FLUSH_RE = re.compile(
    r"(?m)^\s*(?:"
    r"\\let\s*\\(?:clearpage|cleardoublepage)\s*\\relax|"
    r"\\renewcommand\*?\s*\{?\\(?:clearpage|cleardoublepage)\}?\s*\{\s*\}|"
    r"\\def\s*\\(?:clearpage|cleardoublepage)\s*\{\s*\}"
    r")\s*(?:%[^\n]*)?\n?"
)
INCOMPLETE_EDITORIAL_MARKDOWN_IMAGE_RE = re.compile(
    r"(?mi)^\s*!\[\s*Illustration\b(?![^\n]*\]\s*\()[^\n]*\n?"
)
LONG_ENSUREMATH_LINE_RE = re.compile(r"^(?P<indent>\s*)\\ensuremath\{(?P<body>.+)\}(?P<tail>\s*)$")
LONG_ENSUREMATH_BREAK_RE = re.compile(r"\\allowbreak\{\}")
INLINE_ENSUREMATH_SIMPLE_RE = re.compile(r"\\ensuremath\{(?P<body>[^{}\n]{80,520})\}")
VISIBLE_LITERAL_IMAGE_REF_RE = re.compile(
    r"\[width=[^\]]+\]\s*"
    r"\(\s*images/[^)\s]+(?:\s*\))?",
    re.I,
)
VISIBLE_LITERAL_STANDALONE_FIGURE_RE = re.compile(r"\(\s*figure\s*\)", re.I)
VISIBLE_BROKEN_FIGURE_METADATA_RE = re.compile(
    r"(?i:\bfigure)\s*"
    r"\[H\]\s*"
    r"(?:\\par\b\s*)?"
    r"\[(?:[^\]\n]|\\par\b\s*){1,180}\]\s*"
    r"(?:\\par\b\s*)?"
    r"images/[^\s{}]+?\.(?:png|jpe?g|jpeg|pdf)",
    re.S,
)
VISIBLE_RESTORABLE_FIGURE_METADATA_RE = re.compile(
    r"(?i:\bfigure)\s*"
    r"\[H\]\s*"
    r"(?:\\par\b\s*)?"
    r"\[(?P<option>(?:[^\]\n]|\\par\b\s*){1,180})\]\s*"
    r"(?:\\par\b\s*)?"
    r"(?P<path>images/[^\s{}]+?\.(?:png|jpe?g|jpeg|pdf))"
    r"(?P<caption>.{0,1000}?)(?:\s+figure\b)",
    re.S,
)
VISIBLE_LITERAL_SOURCE_RE = re.compile(
    r"\\?%\s*source\b[^\n]*?idx\}?\s*:\s*\d+",
    re.I,
)
VISIBLE_CORRUPTED_SOURCE_METADATA_RE = re.compile(
    r"(?:\\%|%)?\s*source(?:\\?_|\b)"
    r"(?=[^\n:]{0,420}(?:\\?_?page|_page|page))"
    r"(?=[^\n:]{0,420}(?:\\?_?idx|_idx|idx))"
    r"[^\n:]{0,420}\)*\s*:\s*"
    r"(?P<index>\d{1,4}(?:\.\d+)?)?",
    re.I,
)
VISIBLE_SOURCE_TABLE_EVIDENCE_BLOCK_RE = re.compile(
    r"\{\\(?:small|scriptsize)\s*\n"
    r"\\setlength\{\\tabcolsep\}\{[^{}\n]+\}\s*\n"
    r"\\renewcommand\{\\arraystretch\}\{[^{}\n]+\}\s*\n"
    r"(?:\\par\\begingroup\\small\\ttfamily\\raggedright\s*\n)?"
    r"(?:(?!\n\}).)*?\bsource\s+table\s+evidence\b"
    r"(?:(?!\n\}).)*?"
    r"(?:\\endgroup\s*\n)?"
    r"\}",
    re.I | re.S,
)
VISIBLE_SOURCE_TABLE_EVIDENCE_LINE_RE = re.compile(r"(?m)^.*\bsource\s+table\s+evidence\b.*(?:\n|$)", re.I)
ORPHAN_SOURCE_PERCENT_BEFORE_METADATA_RE = re.compile(r"\\%\s*(\\par\b)?\s*$")
PAR_COMMAND_STUCK_TO_TEXT_RE = re.compile(r"\\par(?=[A-Za-z])")
VISIBLE_HTML_COMMENT_RE = re.compile(r"<!--.*?-->|<!–.*?–>", re.S)
ORDINAL_EDITION_WORDS = (
    "FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH|"
    "ELEVENTH|TWELFTH|THIRTEENTH|FOURTEENTH|FIFTEENTH|SIXTEENTH|"
    "SEVENTEENTH|EIGHTEENTH|NINETEENTH|TWENTIETH"
)
SOURCE_FRONTMATTER_EDITION_LINE_RE = re.compile(
    rf"^(?:{ORDINAL_EDITION_WORDS}|\d{{1,2}}(?:ST|ND|RD|TH))\s+EDITION$",
    re.I,
)
PLAIN_UNIT_LABEL_RE = re.compile(r"^UNIT$", re.I)
PLAIN_UNIT_NUMBER_RE = re.compile(r"^\d{1,3}$")
SOURCE_FRONTMATTER_PUBLICATION_RE = re.compile(
    r"\b(?:ALL RIGHTS RESERVED|ISBN|Publisher:|Executive Editor:|Development Editor:|"
    r"Art Director:|Manufacturing Planner|Product Marketing|permission|permissions|"
    r"registered trademarks|National Geographic Learning|Cengage|corporate website)\b",
    re.I,
)
SOURCE_FRONTMATTER_CONTENTS_RE = re.compile(
    r"^(?:READING\s+\d+\b|UNIT SUMMARY\b|FROM GRAMMAR TO WRITING\b|APPENDICES\b|"
    r"GLOSSARY\b|INDEX\b|CREDITS\b|GRAMMAR\b).*\b\d{1,3}$",
    re.I,
)
SOURCE_FRONTMATTER_MARKETING_RE = re.compile(
    r"^(?:WELCOME TO\b|ENHANCED IN\b|ADDITIONAL RESOURCES\b|FOR STUDENTS\b|"
    r"FOR TEACHERS\b|New\b|Online Practice\b|GO TO\b|ELTNGL\.COM\b)",
    re.I,
)
SOURCE_FRONTMATTER_ACK_RE = re.compile(
    r"^(?:ACKNOWLEDGMENTS\b|A WORD FROM THE AUTHOR\b|ADVISORY BOARD\b|REVIEWERS\b)",
    re.I,
)
TEST_NAVIGATION_GO_ON_RE = re.compile(r"^\s*GO\s+ON\s*$")
TEST_NAVIGATION_STOP_RE = re.compile(r"^\s*STOP\s*$")
EXTERNAL_RESOURCE_NOISE_LINE_RE = re.compile(
    r"^\s*(?:"
    r"https?://\S+|www\.\S+|\\url\{[^{}]+\}|"
    r"(?:Adapted from|Source:|Retrieved from|Available at)\s+(?:https?://|www\.)\S+|"
    r".*\bdownload\b.*\b(?:available\s+from|available\s+at|from)\s+"
    r"(?:https?://|www\.|[A-Za-z0-9.-]+\.(?:com|org|net))\b.*|"
    r".*\bavailable\s+(?:from|at)\s+(?:https?://|www\.|[A-Za-z0-9.-]+\.(?:com|org|net))\b.*|"
    r"(?:Typeset by|Copyright|All rights reserved|ISBN|Published by)\b.*|"
    r".*\b(?:©|Reproduced with permission)\b.*|"
    r".*\b(?:QR\s*code|scan\s+(?:the\s+)?(?:QR\s*)?code)\b.*|"
    r".*\b(?:is|are)\s+available\s+on\s+.*\b(?:website|web\s+site|online)\b.*|"
    r".*\b(?:refer to|consult|see)\b.*\b(?:syllabus document|website|web\s+site|online resource)\b.*"
    r"\b(?:for more information|for further information|for details)\b.*|"
    r".*\b(?:Watch\s+LIVE\s+Solve|for more information\s+(?:visit|go to)|"
    r"visit\s+(?:our|the)\s+(?:website|web\s+site)|"
    r"go\s+to\s+(?:https?://|www\.|[A-Za-z0-9.-]+\.(?:com|org|net)))\b.*"
    r")\s*$",
    re.I,
)
INLINE_TOP_CUE_RE = (
    r"(?:Look|Do|Does|Did|Would|Which|What|Why|How|Who|Where|When|Have|Make|Listen|Read|Write|"
    r"Work|Copy|Choose|Complete|Match|Decide|Discuss|Use|First|Then|Find|Think|Talk|Compare|"
    r"Explain|Give|Note|Answer|Put|Add|Circle|Underline|Watch|Can|Are|Is|Should|Using|In|Picture)\b"
    r"|(?:[A-Z][a-z]{2,})"
)
INLINE_TOP_ITEM_RE = re.compile(r"\s+(\d{1,2})\s+(?=" + INLINE_TOP_CUE_RE + r")")
INLINE_LETTER_ITEM_RE = re.compile(r"\s+([b-j])\s+(?=\S)")
SPEAKING_CARD_LINE_RE = re.compile(r"^Card\s+(\d{1,3}|[A-Z])\s*$", re.I)
SPEAKING_CARDS_CONTEXT_RE = re.compile(r"\bSpeaking\s+cards?\b", re.I)
SPEAKING_CARDS_HEADING_RE = re.compile(r"^(?:Appendix\s+\d+\s*:?\s*)?Speaking\s+cards?$", re.I)
ANSWER_SPACE_MARKERS = [
    r"\printshortanswer",
    r"\printmediumanswer",
    r"\printlonganswer",
    r"\printlistanswer",
    r"\printwritingbox",
    r"\printchapterendwritingbox",
]
BLOCK_BOUNDARY_ENVIRONMENTS = {
    "figure": "figure",
    "tabular": "table",
    "tabularx": "table",
    "longtable": "table",
    "array": "table",
    "align": "math",
    "align*": "math",
    "equation": "math",
    "equation*": "math",
    "gather": "math",
    "gather*": "math",
}


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 1800) -> dict:
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return {
            "cmd": cmd,
            "cwd": str(cwd) if cwd else "",
            "returncode": proc.returncode,
            "elapsed_seconds": round(time.time() - start, 3),
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except Exception as exc:  # pragma: no cover - defensive reporting
        return {
            "cmd": cmd,
            "cwd": str(cwd) if cwd else "",
            "returncode": 999,
            "elapsed_seconds": round(time.time() - start, 3),
            "stdout": "",
            "stderr": repr(exc),
        }


def latex_escape(value: str) -> str:
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
    return "".join(replacements.get(ch, ch) for ch in str(value or ""))


def clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def latex_text_to_plain(value: str) -> str:
    value = str(value or "")
    value = value.replace(r"\textbackslash{}", "")
    replacements = {
        r"\_": "_",
        r"\&": "&",
        r"\%": "%",
        r"\#": "#",
        r"\$": "$",
        r"\{": "{",
        r"\}": "}",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    return clean_spaces(value)


def unwrap_textual_inline_math_list(stripped: str) -> str:
    if not (stripped.startswith("$") and stripped.endswith("$") and len(stripped) >= 50):
        return ""
    inner = stripped[1:-1].strip()
    if not inner or "\\" in inner or re.search(r"[=^_{}&<>]", inner):
        return ""
    labels = re.findall(r"(?:^|\s)([a-j])\s+(?=[A-Za-z])", inner)
    words = re.findall(r"[A-Za-z]{2,}", inner)
    if len(labels) >= 4 and len(words) >= 8:
        return inner
    if len(inner) >= 80 and len(words) >= 12:
        return inner
    return ""


def normalize_title_words(value: str) -> str:
    value = re.sub(r"\s*\(tm\)", "", value, flags=re.I)
    value = re.sub(r"\s+with\s+Digital\s+Access(?:\s*\([^)]*\))?", "", value, flags=re.I)
    value = re.sub(r"\s+Digital\s+Access(?:\s*\([^)]*\))?", "", value, flags=re.I)
    value = re.sub(r"\bIgcse\s*TM\b", "IGCSE", value, flags=re.I)
    value = re.sub(r"\bIgcse\b", "IGCSE", value, flags=re.I)
    value = re.sub(r"\bO Level\b", "O Level", value, flags=re.I)
    value = value.replace("Coursebook With", "Coursebook with")
    value = value.replace("Digital Access", "Digital Access")
    return clean_spaces(value)


def title_part_has_latin_title_signal(value: str) -> bool:
    return len(re.findall(r"[A-Za-z][A-Za-z'-]{1,}", value or "")) >= 3


def is_leading_title_noise_part(value: str, later_text: str) -> bool:
    text = clean_spaces(value)
    if not text or not title_part_has_latin_title_signal(later_text):
        return False
    if re.fullmatch(r"[\d\W_]+", text):
        return True
    has_latin = bool(re.search(r"[A-Za-z]", text))
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", text))
    if has_cjk and not has_latin:
        return True
    return False


def collapse_adjacent_duplicate_title_words(value: str) -> str:
    words = clean_spaces(value).split()
    out: list[str] = []
    for word in words:
        normalized = re.sub(r"[^A-Za-z0-9]+", "", word).lower()
        previous = re.sub(r"[^A-Za-z0-9]+", "", out[-1]).lower() if out else ""
        if normalized and normalized == previous and len(normalized) > 2:
            continue
        out.append(word)
    return clean_spaces(" ".join(out))


def clean_mechanical_title_noise(value: str) -> str:
    title = clean_spaces(value)
    if "_" in title:
        parts = [clean_spaces(part) for part in re.split(r"_+", title) if clean_spaces(part)]
        if parts:
            kept: list[str] = []
            for index, part in enumerate(parts):
                later = " ".join(parts[index + 1:])
                if not kept and is_leading_title_noise_part(part, later):
                    continue
                kept.append(part)
            title = " ".join(kept or parts)
    title = re.sub(r"^(?:\d{1,4}\s+){1,4}(?=[A-Za-z])", "", title).strip()
    title = collapse_adjacent_duplicate_title_words(title)
    return clean_spaces(title)


def human_author(value: str) -> str:
    value = clean_spaces(value)
    if "," in value:
        parts = [part.strip() for part in value.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            return f"{parts[1]} {parts[0]}"
    return value


def infer_metadata(raw_title: str) -> dict:
    title = re.sub(r"\.pdf\s*$", "", latex_text_to_plain(raw_title), flags=re.I)
    title = clean_mechanical_title_noise(title)
    title = normalize_title_words(title)
    author = ""
    subtitle = ""

    def repl_parenthetical(match: re.Match[str]) -> str:
        nonlocal author, subtitle
        inner = clean_spaces(match.group(1))
        low = inner.lower()
        if "z-library" in low or "z-lib" in low or "1lib" in low:
            return ""
        if "cambridge international" in low:
            subtitle = normalize_title_words(inner)
            return ""
        if "," in inner and not author:
            author = human_author(inner)
            return ""
        return f" ({inner})"

    title = re.sub(r"\(([^()]*)\)", repl_parenthetical, title)
    title = normalize_title_words(title)
    return {
        "title": title or normalize_title_words(raw_title),
        "subtitle": subtitle,
        "author": author,
    }


def extract_latex_macro(text: str, name: str) -> str:
    marker = rf"\{name}" + "{"
    start = text.find(marker)
    if start < 0:
        return ""
    index = start + len(marker)
    depth = 1
    out: list[str] = []
    while index < len(text):
        char = text[index]
        if char == "\n":
            return ""
        if char == "\\" and index + 1 < len(text):
            out.append(char)
            index += 1
            out.append(text[index])
            index += 1
            continue
        if char == "{":
            depth += 1
            out.append(char)
        elif char == "}":
            depth -= 1
            if depth == 0:
                return "".join(out).strip()
            out.append(char)
        else:
            out.append(char)
        index += 1
    return ""


def replace_latex_macro(text: str, name: str, value: str) -> tuple[str, bool]:
    pattern = re.compile(rf"\\{re.escape(name)}\{{[^{{}}\n]*\}}")
    repl = rf"\{name}" + "{" + latex_escape(value) + "}"
    new_text, count = pattern.subn(lambda _match: repl, text, count=1)
    return new_text, bool(count)


def language_from_content(main_text: str, content_text: str) -> str:
    combined = main_text + "\n" + content_text[:20000]
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", combined))
    latin_count = len(re.findall(r"[A-Za-z]", combined))
    return "cn" if cjk_count > max(20, latin_count // 8) else "en"


def set_document_language(text: str, lang: str) -> tuple[str, bool]:
    doc_re = re.compile(r"\\documentclass\[(?P<opts>[^\]]*)\]\{elegantbook\}")

    def repl(match: re.Match[str]) -> str:
        opts = match.group("opts")
        if "lang=" in opts:
            opts = re.sub(r"lang=[^,\]]+", f"lang={lang}", opts)
        else:
            opts = f"lang={lang}," + opts
        return rf"\documentclass[{opts}]{{elegantbook}}"

    new_text, count = doc_re.subn(repl, text, count=1)
    return new_text, bool(count and new_text != text)


def ensure_preamble_looseners(text: str, print_layout: str = "classroom") -> tuple[str, bool]:
    additions = [
        r"\emergencystretch=3em",
        r"\tolerance=1600",
        r"\hfuzz=1.5pt",
        r"\setlength{\cftchapnumwidth}{6.8em}",
    ]
    changed = False
    insert_after = r"\graphicspath{{./}{images/}{../images/}}"
    block = "\n".join(line for line in additions if line not in text)
    if block and insert_after in text:
        text = text.replace(insert_after, insert_after + "\n" + block, 1)
        changed = True
    if r"\IfFileExists{xurl.sty}" not in text and r"\usepackage{caption}" in text:
        text = text.replace(
            r"\usepackage{caption}",
            r"\usepackage{caption}" + "\n" + r"\IfFileExists{xurl.sty}{\usepackage{xurl}}{\usepackage{url}}",
            1,
        )
        changed = True
    if r"\IfFileExists{needspace.sty}" not in text and r"\usepackage{fancyhdr}" in text:
        text = text.replace(
            r"\usepackage{fancyhdr}",
            r"\IfFileExists{needspace.sty}{\usepackage{needspace}}{\providecommand{\Needspace}[1]{}}"
            + "\n"
            + r"\usepackage{fancyhdr}",
            1,
        )
        changed = True
    if print_layout == "classroom":
        classroom_setlist = (
            r"\setlist{itemsep=0.32\baselineskip,topsep=0.38\baselineskip,"
            r"parsep=0.12\baselineskip,leftmargin=*}"
        )
        if r"\setlist{nosep,leftmargin=*}" in text:
            text = text.replace(r"\setlist{nosep,leftmargin=*}", classroom_setlist, 1)
            changed = True
    return text, changed


def ensure_editorial_commands(text: str) -> tuple[str, bool]:
    if r"\newcommand{\learningheading}" in text:
        changed = False
        if r"before skip=0.65\baselineskip" not in text:
            text = text.replace(
                "    top=1mm,\n    bottom=1mm",
                "    top=1.5mm,\n    bottom=1.4mm,\n    before skip=0.65\\baselineskip,\n    after skip=0.45\\baselineskip",
                1,
            )
            text = text.replace(
                "\\newcommand{\\polishheadingbox}[4]{%\n  \\begin{tcolorbox}",
                "\\newcommand{\\polishheadingbox}[4]{%\n  \\par\\Needspace{4\\baselineskip}\n  \\begin{tcolorbox}",
                1,
            )
            changed = True
        if r"\definecolor{ebFocusFrame}" not in text and r"\definecolor{ebExamFrame}" in text:
            text = text.replace(
                r"\definecolor{ebExamFrame}{HTML}{9A3412}",
                r"\definecolor{ebFocusFrame}{HTML}{375A7F}"
                + "\n"
                + r"\definecolor{ebFocusBack}{HTML}{F1F6FA}"
                + "\n"
                + r"\definecolor{ebExamFrame}{HTML}{9A3412}",
                1,
            )
            changed = True
        if r"\newcommand{\focusheading}" not in text and r"\newcommand{\tipheading}" in text:
            text = text.replace(
                r"\newcommand{\tipheading}[1]{\polishheadingbox{Tip}{ebTipBack}{ebTipFrame}{#1}}",
                r"\newcommand{\tipheading}[1]{\polishheadingbox{Tip}{ebTipBack}{ebTipFrame}{#1}}"
                + "\n"
                + r"\newcommand{\focusheading}[1]{\polishheadingbox{Focus}{ebFocusBack}{ebFocusFrame}{#1}}",
                1,
            )
            changed = True
        if r"\newcommand{\stageheading}" not in text and r"\newcommand{\readingheading}" in text:
            text = text.replace(
                r"\newcommand{\readingheading}[1]{\polishheadingbox{Reading stage}{ebReadingBack}{ebReadingFrame}{#1}}",
                r"\newcommand{\readingheading}[1]{\polishheadingbox{Reading stage}{ebReadingBack}{ebReadingFrame}{#1}}"
                + "\n"
                + r"\newcommand{\stageheading}[1]{\polishheadingbox{Stage}{ebReadingBack}{ebReadingFrame}{#1}}",
                1,
            )
            changed = True
        if r"\newcommand{\readingtextheading}" not in text and r"\newcommand{\stageheading}" in text:
            reading_text_block = "\n".join([
                r"\newcommand{\readingtextheading}[3]{%",
                r"  \par\Needspace{5\baselineskip}",
                r"  \begin{tcolorbox}[enhanced,breakable,colback=ebReadingBack,colframe=ebReadingFrame,boxrule=0.55pt,arc=1mm,left=2mm,right=2mm,top=1.4mm,bottom=1.3mm,before skip=0.65\baselineskip,after skip=0.45\baselineskip]",
                r"  {\large\bfseries\textcolor{ebReadingFrame}{#1}}%",
                r"  \if\relax\detokenize{#2}\relax\else\par\smallskip\textbf{#2}\fi",
                r"  \if\relax\detokenize{#3}\relax\else\par{\itshape #3}\fi",
                r"  \end{tcolorbox}",
                r"}",
                r"\newcommand{\readingparagraph}[1]{\par\smallskip\noindent\textbf{\textcolor{ebReadingFrame}{[#1]}}\quad}",
            ])
            text = text.replace(
                r"\newcommand{\stageheading}[1]{\polishheadingbox{Stage}{ebReadingBack}{ebReadingFrame}{#1}}",
                r"\newcommand{\stageheading}[1]{\polishheadingbox{Stage}{ebReadingBack}{ebReadingFrame}{#1}}"
                + "\n"
                + reading_text_block,
                1,
            )
            changed = True
        if r"\newcommand{\activitytask}" not in text and r"\newcommand{\activityheading}" in text:
            text = text.replace(
                r"\newcommand{\activityheading}[1]{\polishheadingbox{Activity}{ebExerciseBack}{ebExerciseFrame}{#1}}",
                r"\newcommand{\activityheading}[1]{\polishheadingbox{Activity}{ebExerciseBack}{ebExerciseFrame}{#1}}"
                + "\n"
                + r"\newcommand{\activitytask}[1]{\par\Needspace{3\baselineskip}\medskip\noindent\textbf{#1}\quad}",
                1,
            )
            changed = True
        elif r"\newcommand{\activitytask}[1]{\par\smallskip\noindent\textbf{#1}\quad}" in text:
            text = text.replace(
                r"\newcommand{\activitytask}[1]{\par\smallskip\noindent\textbf{#1}\quad}",
                r"\newcommand{\activitytask}[1]{\par\Needspace{3\baselineskip}\medskip\noindent\textbf{#1}\quad}",
                1,
            )
            changed = True
        if r"\newcommand{\printshortanswer}" not in text and r"\newcommand{\activitytask}" in text:
            answer_block = "\n".join([
                r"\newcommand{\printanswerline}{\par\vspace{0.16\baselineskip}\noindent{\color{gray!58}\rule{\linewidth}{0.35pt}}\par}",
                r"\newcommand{\printshortanswer}{\par\smallskip\Needspace{3\baselineskip}\printanswerline}",
                r"\newcommand{\printmediumanswer}{\par\smallskip\Needspace{4\baselineskip}\printanswerline\printanswerline}",
                r"\newcommand{\printlonganswer}{\par\smallskip\Needspace{6\baselineskip}\printanswerline\printanswerline\printanswerline\printanswerline}",
                r"\newcommand{\printlistanswer}{\par\smallskip\Needspace{5\baselineskip}\printanswerline\printanswerline\printanswerline}",
                r"\newcommand{\printwritingbox}{\par\smallskip\Needspace{8\baselineskip}\begin{tcolorbox}[enhanced,colback=white,colframe=gray!45,boxrule=0.4pt,arc=0.8mm,height=0.16\textheight,before skip=0.25\baselineskip,after skip=0.35\baselineskip]\end{tcolorbox}\par}",
                r"\newcommand{\printchapterendwritingbox}{\par\smallskip\Needspace{12\baselineskip}\begin{tcolorbox}[enhanced,colback=white,colframe=gray!45,boxrule=0.4pt,arc=0.8mm,before skip=0.25\baselineskip,after skip=0.35\baselineskip]\printanswerline\printanswerline\printanswerline\printanswerline\printanswerline\printanswerline\printanswerline\printanswerline\end{tcolorbox}\par}",
            ])
            text = text.replace(
                r"\newcommand{\activitytask}[1]{\par\Needspace{3\baselineskip}\medskip\noindent\textbf{#1}\quad}",
                r"\newcommand{\activitytask}[1]{\par\Needspace{3\baselineskip}\medskip\noindent\textbf{#1}\quad}"
                + "\n"
                + answer_block,
                1,
            )
            changed = True
        elif r"\newcommand{\printchapterendwritingbox}" not in text and r"\newcommand{\printwritingbox}" in text:
            text = text.replace(
                r"\newcommand{\printwritingbox}{\par\smallskip\Needspace{8\baselineskip}\begin{tcolorbox}[enhanced,colback=white,colframe=gray!45,boxrule=0.4pt,arc=0.8mm,height=0.16\textheight,before skip=0.25\baselineskip,after skip=0.35\baselineskip]\end{tcolorbox}\par}",
                r"\newcommand{\printwritingbox}{\par\smallskip\Needspace{8\baselineskip}\begin{tcolorbox}[enhanced,colback=white,colframe=gray!45,boxrule=0.4pt,arc=0.8mm,height=0.16\textheight,before skip=0.25\baselineskip,after skip=0.35\baselineskip]\end{tcolorbox}\par}"
                + "\n"
                + r"\newcommand{\printchapterendwritingbox}{\par\smallskip\Needspace{12\baselineskip}\begin{tcolorbox}[enhanced,colback=white,colframe=gray!45,boxrule=0.4pt,arc=0.8mm,before skip=0.25\baselineskip,after skip=0.35\baselineskip]\printanswerline\printanswerline\printanswerline\printanswerline\printanswerline\printanswerline\printanswerline\printanswerline\end{tcolorbox}\par}",
                1,
            )
            changed = True
        if r"\newcommand{\speakingcardheading}" not in text and r"\newcommand{\readingparagraph}" in text:
            text = text.replace(
                r"\newcommand{\readingparagraph}[1]{\par\smallskip\noindent\textbf{\textcolor{ebReadingFrame}{[#1]}}\quad}",
                r"\newcommand{\readingparagraph}[1]{\par\smallskip\noindent\textbf{\textcolor{ebReadingFrame}{[#1]}}\quad}"
                + "\n"
                + r"\newcommand{\speakingcardheading}[1]{\par\Needspace{13\baselineskip}\medskip\noindent\textbf{\textcolor{ebReadingFrame}{#1}}\par\smallskip}",
                1,
            )
            changed = True
        return text, changed
    block = r"""
\definecolor{ebLearningFrame}{HTML}{276FBF}
\definecolor{ebLearningBack}{HTML}{EEF5FF}
\definecolor{ebReflectionFrame}{HTML}{7A4EAB}
\definecolor{ebReflectionBack}{HTML}{F6F0FF}
\definecolor{ebProgressFrame}{HTML}{247B5B}
\definecolor{ebProgressBack}{HTML}{EFFAF5}
\definecolor{ebReadingFrame}{HTML}{4F6D7A}
\definecolor{ebReadingBack}{HTML}{F2F7F8}
\definecolor{ebFocusFrame}{HTML}{375A7F}
\definecolor{ebFocusBack}{HTML}{F1F6FA}
\definecolor{ebExamFrame}{HTML}{9A3412}
\definecolor{ebExamBack}{HTML}{FFF4ED}

\newcommand{\polishheadingbox}[4]{%
  \par\Needspace{4\baselineskip}
  \begin{tcolorbox}[
    enhanced,
    breakable,
    colback=#2,
    colframe=#3,
    boxrule=0.55pt,
    arc=1mm,
    left=2mm,
    right=2mm,
    top=1.5mm,
    bottom=1.4mm,
    before skip=0.65\baselineskip,
    after skip=0.45\baselineskip
  ]
  \textbf{\textcolor{#3}{#1:}} #4
  \end{tcolorbox}
}
\newcommand{\learningheading}[1]{\polishheadingbox{Learning focus}{ebLearningBack}{ebLearningFrame}{#1}}
\newcommand{\tipheading}[1]{\polishheadingbox{Tip}{ebTipBack}{ebTipFrame}{#1}}
\newcommand{\focusheading}[1]{\polishheadingbox{Focus}{ebFocusBack}{ebFocusFrame}{#1}}
\newcommand{\reflectionheading}[1]{\polishheadingbox{Reflection}{ebReflectionBack}{ebReflectionFrame}{#1}}
\newcommand{\progressheading}[1]{\polishheadingbox{Progress check}{ebProgressBack}{ebProgressFrame}{#1}}
\newcommand{\readingheading}[1]{\polishheadingbox{Reading stage}{ebReadingBack}{ebReadingFrame}{#1}}
\newcommand{\stageheading}[1]{\polishheadingbox{Stage}{ebReadingBack}{ebReadingFrame}{#1}}
\newcommand{\readingtextheading}[3]{%
  \par\Needspace{5\baselineskip}
  \begin{tcolorbox}[enhanced,breakable,colback=ebReadingBack,colframe=ebReadingFrame,boxrule=0.55pt,arc=1mm,left=2mm,right=2mm,top=1.4mm,bottom=1.3mm,before skip=0.65\baselineskip,after skip=0.45\baselineskip]
  {\large\bfseries\textcolor{ebReadingFrame}{#1}}%
  \if\relax\detokenize{#2}\relax\else\par\smallskip\textbf{#2}\fi
  \if\relax\detokenize{#3}\relax\else\par{\itshape #3}\fi
  \end{tcolorbox}
}
\newcommand{\readingparagraph}[1]{\par\smallskip\noindent\textbf{\textcolor{ebReadingFrame}{[#1]}}\quad}
\newcommand{\speakingcardheading}[1]{\par\Needspace{13\baselineskip}\medskip\noindent\textbf{\textcolor{ebReadingFrame}{#1}}\par\smallskip}
\newcommand{\activityheading}[1]{\polishheadingbox{Activity}{ebExerciseBack}{ebExerciseFrame}{#1}}
\newcommand{\activitytask}[1]{\par\Needspace{3\baselineskip}\medskip\noindent\textbf{#1}\quad}
\newcommand{\printanswerline}{\par\vspace{0.16\baselineskip}\noindent{\color{gray!58}\rule{\linewidth}{0.35pt}}\par}
\newcommand{\printshortanswer}{\par\smallskip\Needspace{3\baselineskip}\printanswerline}
\newcommand{\printmediumanswer}{\par\smallskip\Needspace{4\baselineskip}\printanswerline\printanswerline}
\newcommand{\printlonganswer}{\par\smallskip\Needspace{6\baselineskip}\printanswerline\printanswerline\printanswerline\printanswerline}
\newcommand{\printlistanswer}{\par\smallskip\Needspace{5\baselineskip}\printanswerline\printanswerline\printanswerline}
\newcommand{\printwritingbox}{\par\smallskip\Needspace{8\baselineskip}\begin{tcolorbox}[enhanced,colback=white,colframe=gray!45,boxrule=0.4pt,arc=0.8mm,height=0.16\textheight,before skip=0.25\baselineskip,after skip=0.35\baselineskip]\end{tcolorbox}\par}
\newcommand{\printchapterendwritingbox}{\par\smallskip\Needspace{12\baselineskip}\begin{tcolorbox}[enhanced,colback=white,colframe=gray!45,boxrule=0.4pt,arc=0.8mm,before skip=0.25\baselineskip,after skip=0.35\baselineskip]\printanswerline\printanswerline\printanswerline\printanswerline\printanswerline\printanswerline\printanswerline\printanswerline\end{tcolorbox}\par}
\newcommand{\examheading}[1]{\polishheadingbox{Exam practice}{ebExamBack}{ebExamFrame}{#1}}
"""
    insert_before = r"\setcounter{tocdepth}"
    if insert_before in text:
        return text.replace(insert_before, block.strip() + "\n" + insert_before, 1), True
    begin_doc = r"\begin{document}"
    if begin_doc in text:
        return text.replace(begin_doc, block.strip() + "\n" + begin_doc, 1), True
    return text + "\n" + block.strip() + "\n", True


def sync_plain_cover_override(
    text: str,
    title: str,
    subtitle: str,
    author: str,
    date_value: str,
    version: str,
) -> tuple[str, bool]:
    if r"\renewcommand*{\maketitle}" not in text:
        return text, False

    changed = False
    title_line = rf"      {{\Huge\bfseries\color{{black}} {latex_escape(title)}\par}}"
    text, count = re.subn(
        r"(?m)^\s*\{\\Huge\\bfseries\\color\{black\}\s+.*?\\par\}\s*$",
        lambda _match: title_line,
        text,
        count=1,
    )
    changed = changed or bool(count)

    subtitle_block = ""
    if subtitle:
        subtitle_block = rf"    {{\Large\color{{structurecolor}} {latex_escape(subtitle)}\par}}" + "\n"
    text, count = re.subn(
        r"(\s*\\end\{minipage\}\n\s*\\vspace\{0\.5in\}\n)(?:\s*\{\\Large\\color\{structurecolor\}[^\n]*\\par\}\n)?(\s*\\vspace\{0\.7in\})",
        lambda match: match.group(1) + ("\n" + subtitle_block if subtitle_block else "\n") + match.group(2),
        text,
        count=1,
    )
    changed = changed or bool(count)

    rows = [value for value in [author, date_value, version] if clean_spaces(value)]
    if rows:
        tabular_lines = [r"\begin{tabular}{l}"]
        tabular_lines.extend(rf"        {latex_escape(row)}\\[0.4ex]" for row in rows)
        tabular_lines.append(r"      \end{tabular}")
        tabular = "\n".join(tabular_lines)
        text, count = re.subn(
            r"\\begin\{tabular\}\{l\}.*?\\end\{tabular\}",
            lambda _match: tabular,
            text,
            count=1,
            flags=re.S,
        )
        changed = changed or bool(count)
    return text, changed


def set_toc_depth(text: str, depth: int = 2) -> tuple[str, bool]:
    new_text, count = re.subn(r"\\setcounter\{tocdepth\}\{\d+\}", rf"\\setcounter{{tocdepth}}{{{depth}}}", text, count=1)
    return new_text, bool(count and new_text != text)


def normalize_visible_text(value: str) -> str:
    value = re.sub(r"%.*$", "", value)
    value = re.sub(r"\\(?:chapter|section|subsection)(?:\[[^\]]*\])?\{([^{}]+)\}", r"\1", value)
    value = re.sub(r"\\[A-Za-z]+\{([^{}]+)\}", r"\1", value)
    value = re.sub(r"[{}\\]", "", value)
    return clean_spaces(value)


def is_running_header_noise(line: str) -> bool:
    text = normalize_visible_text(line)
    return bool(RUNNING_HEADER_RE.match(text))


def is_source_frontmatter_edition_line(line: str) -> bool:
    return bool(SOURCE_FRONTMATTER_EDITION_LINE_RE.match(normalize_visible_text(line)))


def source_frontmatter_edition_indices(content_text: str) -> list[int]:
    """Return 0-based lines for an orphan edition label before the first body heading."""
    lines = content_text.splitlines()
    first_structure_index = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("%"):
            continue
        if re.match(r"\\(?:part|chapter|section)(?:\*|\[[^\]]*\])?\{", stripped):
            first_structure_index = index
            break
    if first_structure_index is None:
        return []

    visible_before: list[int] = []
    for index, line in enumerate(lines[:first_structure_index]):
        stripped = line.strip()
        if not stripped or stripped.startswith("%"):
            continue
        visible_before.append(index)
    if len(visible_before) != 1:
        return []
    index = visible_before[0]
    if is_source_frontmatter_edition_line(lines[index]):
        return [index]
    return []


def count_source_frontmatter_edition_residue(content_text: str) -> int:
    return len(source_frontmatter_edition_indices(content_text))


def visible_line_records(lines: list[str]) -> list[tuple[int, str]]:
    records: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        text = normalize_visible_text(line)
        if text:
            records.append((index, text))
    return records


def source_frontmatter_marker_categories(records: list[tuple[int, str]]) -> set[str]:
    categories: set[str] = set()
    for _index, text in records:
        if SOURCE_FRONTMATTER_PUBLICATION_RE.search(text):
            categories.add("publication")
        if SOURCE_FRONTMATTER_CONTENTS_RE.search(text):
            categories.add("source_contents")
        if SOURCE_FRONTMATTER_MARKETING_RE.search(text):
            categories.add("marketing")
        if SOURCE_FRONTMATTER_ACK_RE.search(text):
            categories.add("acknowledgments")
    return categories


def find_plain_unit_heading(records: list[tuple[int, str]]) -> tuple[int, int, int, str]:
    for offset in range(len(records) - 2):
        unit_index, unit_text = records[offset]
        number_index, number_text = records[offset + 1]
        title_index, title_text = records[offset + 2]
        if not PLAIN_UNIT_LABEL_RE.match(unit_text):
            continue
        if not PLAIN_UNIT_NUMBER_RE.match(number_text):
            continue
        if len(title_text) > 80 or title_text.startswith("\\"):
            continue
        if re.fullmatch(r"[\d\W_]+", title_text):
            continue
        return unit_index, number_index, title_index, f"Unit {number_text}: {title_text}"
    return -1, -1, -1, ""


def source_frontmatter_block_candidate(content_text: str) -> dict:
    """Detect a source frontmatter block that precedes the first real Unit body."""
    lines = content_text.splitlines()
    records = visible_line_records(lines)
    unit_index, number_index, title_index, unit_title = find_plain_unit_heading(records)
    if unit_index < 0:
        return {}
    if unit_index > 1500:
        return {}
    before_records = [(index, text) for index, text in records if index < unit_index]
    if len(before_records) < 50:
        return {}
    categories = source_frontmatter_marker_categories(before_records)
    if len(categories) < 3:
        return {}
    return {
        "start_line": 1,
        "end_line": unit_index,
        "unit_line": unit_index + 1,
        "number_line": number_index + 1,
        "title_line": title_index + 1,
        "unit_title": unit_title,
        "visible_lines_before": len(before_records),
        "marker_categories": sorted(categories),
    }


def count_source_frontmatter_block_residue(content_text: str) -> int:
    return 1 if source_frontmatter_block_candidate(content_text) else 0


def count_external_resource_noise_lines(content_text: str) -> int:
    return sum(1 for line in content_text.splitlines() if EXTERNAL_RESOURCE_NOISE_LINE_RE.match(line.strip()))


def count_source_numbered_section_titles(content_text: str) -> int:
    return sum(1 for line in content_text.splitlines() if SOURCE_NUMBERED_HEADING_RE.match(line.strip()))


def count_source_numbered_star_section_titles(content_text: str) -> int:
    return sum(1 for line in content_text.splitlines() if SOURCE_NUMBERED_STAR_HEADING_RE.match(line.strip()))


def count_source_structural_section_titles(content_text: str) -> int:
    return sum(1 for line in content_text.splitlines() if SOURCE_STRUCTURAL_HEADING_RE.match(line.strip()))


def count_source_structural_star_section_titles(content_text: str) -> int:
    return sum(1 for line in content_text.splitlines() if SOURCE_STRUCTURAL_STAR_HEADING_RE.match(line.strip()))


def count_source_structural_chapter_titles(content_text: str) -> int:
    return sum(1 for line in content_text.splitlines() if SOURCE_STRUCTURAL_CHAPTER_TITLE_RE.match(line.strip()))


def normalize_source_labeled_headings(content_text: str) -> tuple[str, list[str], list[dict]]:
    """Avoid double numbering when source headings already carry visible structure."""
    lines = content_text.splitlines()
    out: list[str] = []
    changes: list[str] = []
    decisions: list[dict] = []
    numbered_chapter_index = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        chapter_line = bool(re.match(r"^\\chapter(?!\*)(?:\[[^\]]*\])?\{", stripped))
        if chapter_line:
            numbered_chapter_index += 1
        chapter_match = SOURCE_STRUCTURAL_CHAPTER_TITLE_RE.match(stripped)
        if chapter_match:
            source_number = int(chapter_match.group("number"))
            title = chapter_match.group("title").strip()
            rest = chapter_match.group("rest").strip()
            if source_number == numbered_chapter_index and rest:
                new_title = rest
                out.append(rf"\chapter{{{new_title}}}")
                changes.append("source_structural_chapter_title_normalized")
                decisions.append(make_decision(
                    None,
                    "rewrite",
                    "source_structural_chapter_title_normalized",
                    {
                        "line": index + 1,
                        "source_chapter_number": source_number,
                        "latex_chapter_index": numbered_chapter_index,
                        "before": title,
                        "after": new_title,
                        "evidence": (
                            "chapter title visibly repeats the same source chapter label that "
                            "ElegantBook will print as the automatic chapter label"
                        ),
                    },
                ))
                continue
            if source_number != numbered_chapter_index:
                out.append(rf"\chapter*{{{title}}}")
                out.append(rf"\addcontentsline{{toc}}{{chapter}}{{{title}}}")
                changes.append("source_structural_chapter_title_to_starred")
                decisions.append(make_decision(
                    None,
                    "rewrite",
                    "source_structural_chapter_title_to_starred",
                    {
                        "line": index + 1,
                        "source_chapter_number": source_number,
                        "latex_chapter_index": numbered_chapter_index,
                        "title": title,
                        "evidence": (
                            "source chapter label does not match the generated chapter index, "
                            "so automatic ElegantBook numbering would be misleading"
                        ),
                    },
                ))
                continue
        match = SOURCE_NUMBERED_HEADING_RE.match(stripped)
        reason = "source_numbered_heading_to_starred"
        evidence = "heading title visibly starts with a source section number, so automatic LaTeX numbering would duplicate it"
        change = "source_numbered_heading_to_starred"
        if not match:
            match = SOURCE_STRUCTURAL_HEADING_RE.match(stripped)
            reason = "source_structural_heading_to_starred"
            evidence = "heading title is a visible source structural label, so automatic LaTeX numbering would add a misleading prefix"
            change = "source_structural_heading_to_starred"
        if not match:
            out.append(line)
            continue
        level = match.group("level")
        title = match.group("title").strip()
        next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
        already_has_toc = next_line.startswith(rf"\addcontentsline{{toc}}{{{level}}}")
        out.append(rf"\{level}*{{{title}}}")
        if not already_has_toc:
            out.append(rf"\addcontentsline{{toc}}{{{level}}}{{{title}}}")
        changes.append(change)
        decisions.append(make_decision(
            None,
            "rewrite",
            reason,
            {
                "line": index + 1,
                "level": level,
                "title": title,
                "toc_entry_added": not already_has_toc,
                "evidence": evidence,
            },
        ))
    if not changes:
        return content_text, [], []
    return "\n".join(out) + ("\n" if content_text.endswith("\n") else ""), changes, decisions


def nearby_nonempty(lines: list[str], start: int, stop: int) -> list[str]:
    out = []
    for index in range(max(0, start), min(len(lines), stop)):
        text = lines[index].strip()
        if text:
            out.append(text)
    return out


def strip_latex_comments(line: str) -> str:
    if "%" not in line:
        return line
    return re.sub(r"(?<!\\)%.*$", "", line)


def visible_summary(lines: list[str], limit: int = 140) -> str:
    text = normalize_visible_text(" ".join(line.strip() for line in lines if line.strip()))
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "..."
    return text


def reading_text_label(line: str) -> str:
    text = normalize_visible_text(line)
    return text if READING_TEXT_LABEL_RE.match(text) else ""


def is_digital_text_copy_prompt(line: str) -> bool:
    return bool(DIGITAL_TEXT_COPY_RE.match(normalize_visible_text(line)))


def is_blank_block(block: dict) -> bool:
    lines = block.get("lines") or []
    return bool(block.get("meta", {}).get("blank")) or not any(line.strip() for line in lines)


def is_reading_byline(line: str) -> bool:
    text = normalize_visible_text(line)
    return bool(re.match(r"^by\s+.{2,90}$", text, re.I))


def is_reading_metadata_line(line: str) -> bool:
    text = normalize_visible_text(line)
    if not text or len(text) > 95:
        return False
    if text.startswith(("\\", ">", "[", "%")):
        return False
    if reading_text_label(text) or is_digital_text_copy_prompt(text):
        return False
    if READING_PARAGRAPH_RE.match(text) or looks_like_top_item_line(text) or looks_like_letter_item_line(text):
        return False
    if is_activity_boundary_line(text) or semantic_heading_command(text):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    return bool(words) and len(words) <= 10


def split_reading_paragraph_markers(line: str) -> list[tuple[str, str]]:
    matches = list(READING_PARAGRAPH_MARKER_RE.finditer(line))
    if not matches or matches[0].start() != 0:
        return []
    parts: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
        paragraph_text = line[start:end].strip()
        if paragraph_text:
            parts.append((match.group(1), paragraph_text))
    return parts


def preserved_text_after_source_page_index(raw_index: str | None) -> tuple[str, str]:
    """Return likely content digits fused after an upstream source page index.

    The source marker itself is always noise here, but the OCR sometimes fuses a
    page number with a following exercise number or section label, such as
    `9616 Square` (page 96 + question 16) or `756.5 Low-energy` (page 75 +
    section 6.5). This helper removes only the likely page index prefix.
    """
    if not raw_index:
        return "", ""
    match = re.match(r"^(\d{1,4})(?:\.(\d+))?$", raw_index.strip())
    if not match:
        return "", raw_index
    digits = match.group(1)
    decimal = match.group(2) or ""
    preserved = ""
    removed = digits + (f".{decimal}" if decimal else "")
    if len(digits) >= 4:
        first_three = int(digits[:3])
        if 100 <= first_three <= 650:
            preserved = digits[3:]
            removed = digits[:3]
        else:
            preserved = digits[2:]
            removed = digits[:2]
    elif len(digits) == 3 and decimal and 10 <= int(digits[:2]) <= 99:
        preserved = digits[2:] + f".{decimal}"
        removed = digits[:2]
    if preserved:
        return preserved, removed
    return "", removed


def count_visible_literal_source_metadata(content_text: str) -> int:
    return (
        len(VISIBLE_LITERAL_SOURCE_RE.findall(content_text))
        + sum(1 for _ in VISIBLE_CORRUPTED_SOURCE_METADATA_RE.finditer(content_text))
    )


def count_visible_source_table_evidence(content_text: str) -> int:
    return len(VISIBLE_SOURCE_TABLE_EVIDENCE_LINE_RE.findall(content_text))


def count_visible_broken_figure_metadata(content_text: str) -> int:
    return len(VISIBLE_BROKEN_FIGURE_METADATA_RE.findall(content_text))


def count_visible_generic_image_caption_residue(content_text: str) -> int:
    return len(VISIBLE_GENERIC_IMAGE_CAPTION_RESIDUE_RE.findall(content_text))


def long_ensuremath_line_info(line: str) -> dict | None:
    match = LONG_ENSUREMATH_LINE_RE.match(line)
    if not match:
        return None
    body = match.group("body")
    if len(body) < 180:
        return None
    if LONG_ENSUREMATH_BREAK_RE.search(body):
        return None
    if r"\begin" in body or r"\end" in body or r"\\" in body:
        return None
    separator_count = (
        body.count(",")
        + body.count(";")
        + len(re.findall(r"\)\s+(?=[A-Za-z0-9\\(])", body))
    )
    if separator_count < 6:
        return None
    return {
        "body": body,
        "body_chars": len(body),
        "separator_count": separator_count,
    }


def count_long_unbreakable_ensuremath_lines(content_text: str) -> int:
    return sum(1 for line in content_text.splitlines() if long_ensuremath_line_info(line))


def inline_ensuremath_prose_info(body: str) -> dict | None:
    if LONG_ENSUREMATH_BREAK_RE.search(body):
        return None
    if r"\begin" in body or r"\end" in body or r"\\" in body:
        return None
    words = re.findall(r"\b[A-Za-z]{3,}\b", body)
    if len(words) < 5:
        return None
    if not re.search(r"[.!?]\s+[A-Za-z]", body):
        return None
    math_markers = len(re.findall(r"\\[A-Za-z]+|[\^_=<>+\-*/]", body))
    return {
        "body_chars": len(body),
        "word_count": len(words),
        "math_marker_count": math_markers,
    }


def count_inline_ensuremath_prose_fragments(content_text: str) -> int:
    return sum(
        1
        for match in INLINE_ENSUREMATH_SIMPLE_RE.finditer(content_text)
        if inline_ensuremath_prose_info(match.group("body"))
    )


def soften_inline_ensuremath_prose_fragments(content: str) -> tuple[str, list[str], list[dict]]:
    """Improve line breaking when prose was accidentally swallowed by math mode."""
    changes: list[str] = []
    decisions: list[dict] = []

    def line_number_at(offset: int) -> int:
        return content.count("\n", 0, offset) + 1

    def repl(match: re.Match[str]) -> str:
        body = match.group("body")
        info = inline_ensuremath_prose_info(body)
        if not info:
            return match.group(0)
        softened = re.sub(r"\s+", r"\\allowbreak{}\\ ", body)
        inserted = softened.count(r"\allowbreak{}")
        if inserted == 0 or softened == body:
            return match.group(0)
        changes.append("added_inline_ensuremath_prose_breakpoints")
        decisions.append(make_decision(
            None,
            "rewrite",
            "inline_ensuremath_prose_layout_breakpoints",
            {
                "line": line_number_at(match.start()),
                "body_chars": info["body_chars"],
                "word_count": info["word_count"],
                "math_marker_count": info["math_marker_count"],
                "inserted_breakpoints": inserted,
                "text": clean_spaces(match.group(0))[:240],
            },
        ))
        return rf"\ensuremath{{{softened}}}"

    return INLINE_ENSUREMATH_SIMPLE_RE.sub(repl, content), changes, decisions


def soften_long_ensuremath_lines(content: str) -> tuple[str, list[str], list[dict]]:
    """Add math-mode breakpoints to very long generated one-line sequences.

    This is deliberately layout-only: it preserves every visible token and only
    adds TeX break opportunities at punctuation/parenthesis separators.
    """
    out: list[str] = []
    changes: list[str] = []
    decisions: list[dict] = []
    for line_no, line in enumerate(content.splitlines(), 1):
        info = long_ensuremath_line_info(line)
        if not info:
            out.append(line)
            continue
        match = LONG_ENSUREMATH_LINE_RE.match(line)
        if not match:
            out.append(line)
            continue
        body = info["body"]
        softened = re.sub(r",\s*", r",\\allowbreak{} ", body)
        softened = re.sub(r";\s*", r";\\allowbreak{} ", softened)
        softened = re.sub(r"\)\s+(?=[A-Za-z0-9\\(])", r")\\allowbreak{} ", softened)
        inserted = softened.count(r"\allowbreak{}")
        if inserted == 0 or softened == body:
            out.append(line)
            continue
        out.append(f"{match.group('indent')}\\ensuremath{{{softened}}}{match.group('tail')}")
        changes.append("added_long_ensuremath_breakpoints")
        decisions.append(make_decision(
            None,
            "rewrite",
            "long_ensuremath_layout_breakpoints",
            {
                "line": line_no,
                "body_chars": info["body_chars"],
                "separator_count": info["separator_count"],
                "inserted_breakpoints": inserted,
                "text": clean_spaces(line)[:240],
            },
        ))
    suffix = "\n" if content.endswith("\n") else ""
    return "\n".join(out) + suffix, changes, decisions


def visible_figure_include_option(raw_option: str) -> str:
    option = clean_spaces(str(raw_option or "").replace(r"\par", " "))
    match = re.search(r"\b(width|height)\s*=\s*([0-9.]+|\\?[A-Za-z0-9.{}]+)", option)
    if not match:
        return r"width=0.78\textwidth"
    key = match.group(1).lower()
    value = match.group(2).strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        unit = r"\textwidth" if key == "width" else r"\textheight"
        return f"{key}={value}{unit}"
    return f"{key}={value}"


def visible_figure_caption_text(raw_caption: str) -> str:
    caption = str(raw_caption or "").replace(r"\par", " ")
    caption = clean_spaces(caption)
    caption = re.sub(r"^tion\s+(?=Figure\b)", "", caption)
    caption = re.sub(r"^(?:tion\s+)?image\s*$", "", caption, flags=re.I)
    caption = re.sub(r"^image\s+", "", caption, flags=re.I)
    caption = clean_spaces(caption)
    if not caption or len(caption) < 8:
        return ""
    if re.fullmatch(r"[a-z](?:\s+[a-z])?", caption, flags=re.I):
        return ""
    if caption.upper().startswith(("QUESTION", "QUESTIONS", "ACTIVITY", "CHAPTER")):
        return ""
    if len(caption) > 260:
        trimmed = caption[:260].rsplit(" ", 1)[0]
        caption = (trimmed or caption[:260]).rstrip(" ,.;:") + "..."
    return latex_escape(latex_text_to_plain(caption))


def separate_stuck_par_commands(content: str) -> tuple[str, list[str], list[dict]]:
    r"""Ensure `\par` remains a command when text is later merged onto its line."""
    changes: list[str] = []
    decisions: list[dict] = []

    def line_number_at(offset: int) -> int:
        return content.count("\n", 0, offset) + 1

    def repl(match: re.Match[str]) -> str:
        changes.append("separated_par_command_from_following_text")
        decisions.append(make_decision(
            None,
            "rewrite",
            "separate_par_command_from_following_text",
            {
                "line": line_number_at(match.start()),
                "text": clean_spaces(content[match.start(): match.start() + 80])[:120],
            },
        ))
        return r"\par "

    return PAR_COMMAND_STUCK_TO_TEXT_RE.sub(repl, content), changes, decisions


def promote_chapter_end_writing_boxes(content: str) -> tuple[str, list[str], list[dict]]:
    """Make writing answer surfaces useful when they sit at a chapter boundary."""
    changes: list[str] = []
    decisions: list[dict] = []
    pattern = re.compile(
        r"\\printwritingbox(?P<space>(?:\s|%[^\n]*(?:\n|$))*)"
        r"(?=\\chapter(?:\[[^\]]*\])?\{)"
    )

    def line_number_at(offset: int) -> int:
        return content.count("\n", 0, offset) + 1

    def repl(match: re.Match[str]) -> str:
        changes.append("promoted_chapter_end_writing_box")
        decisions.append(make_decision(
            None,
            "rewrite",
            "chapter_end_writing_answer_surface",
            {
                "line": line_number_at(match.start()),
                "evidence": "writing answer surface is immediately followed by a chapter boundary",
            },
        ))
        return r"\printchapterendwritingbox" + match.group("space")

    return pattern.sub(repl, content), changes, decisions


def add_personal_goal_answer_spaces(content: str) -> tuple[str, list[str], list[dict]]:
    """Add a useful writing area for explicit personal-goal reflection prompts."""
    changes: list[str] = []
    decisions: list[dict] = []
    lines = content.splitlines()
    out: list[str] = []
    table_depth = 0

    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.search(r"\\begin\{(?:tabularx|tabular|longtable|array)\}", stripped):
            table_depth += 1
        in_table = table_depth > 0
        out.append(line)
        if re.search(r"\\end\{(?:tabularx|tabular|longtable|array)\}", stripped) and table_depth:
            table_depth -= 1
        if in_table:
            continue
        visible = clean_spaces(normalize_visible_text(stripped))
        if not re.search(r"\bset yourself a personal goal\b", visible, re.I):
            continue
        lookahead = lines[index + 1:index + 8]
        if contains_answer_space(lookahead) or existing_answer_surface("\n".join(lookahead)):
            continue
        out.append(r"\printchapterendwritingbox")
        changes.append("added_personal_goal_answer_space")
        decisions.append(make_decision(
            None,
            "add_answer_space",
            "personal_goal_reflection:conservative",
            {
                "line": index + 1,
                "nearby_prompt": visible[:240],
                "evidence": "explicit personal-goal reflection prompt",
            },
        ))

    return "\n".join(out).strip() + "\n", changes, decisions


def prune_misaligned_answer_spaces(content: str) -> tuple[str, list[str], list[dict]]:
    """Remove answer surfaces that are locally duplicated or clearly oral-only."""
    lines = content.splitlines()
    out: list[str] = []
    changes: list[str] = []
    decisions: list[dict] = []
    answer_line_re = re.compile(r"^\\print(?:short|medium|long|list)answer\b")
    any_answer_line_re = re.compile(r"^\\print(?:short|medium|long|list)answer\b|^\\printwritingbox\b|^\\printchapterwritingarea\b")
    written_re = re.compile(
        r"\b(?:write|rewrite|write down|note down|make a list|record|prepare .*notes?|"
        r"in your notebook|write sentences?|explain|give reasons?|personal goal|set yourself)\b",
        re.I,
    )
    oral_re = re.compile(
        r"\b(?:speaking tip|speaking lesson|discuss each|discuss .* partner|talk to your partner|"
        r"with a partner|in pairs|tell me about|ask and answer|listen|watch|check your answers|"
        r"read the audioscript)\b",
        re.I,
    )

    def previous_nonempty_is_answer() -> bool:
        for previous in reversed(out):
            if previous.strip():
                return bool(any_answer_line_re.match(previous.strip()))
        return False

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not any_answer_line_re.match(stripped):
            out.append(line)
            continue
        context = "\n".join(out[-18:])
        visible_context = clean_spaces(normalize_visible_text(context))
        if previous_nonempty_is_answer():
            changes.append("removed_duplicate_answer_space")
            decisions.append(make_decision(
                None,
                "drop_noise",
                "duplicate_answer_surface",
                {"line": index + 1, "marker": stripped, "nearby_prompt": visible_context[-240:]},
            ))
            continue
        if answer_line_re.match(stripped) and oral_re.search(visible_context) and not written_re.search(visible_context):
            changes.append("removed_oral_only_answer_space")
            decisions.append(make_decision(
                None,
                "drop_noise",
                "oral_or_receptive_prompt_without_written_product",
                {"line": index + 1, "marker": stripped, "nearby_prompt": visible_context[-240:]},
            ))
            continue
        out.append(line)
    if not changes:
        return content, [], []
    return "\n".join(out).strip() + "\n", changes, decisions


def clean_visible_literal_metadata(content: str) -> tuple[str, list[str], list[dict]]:
    """Remove LaTeX/image metadata that upstream escaped into visible正文.

    The patterns here are syntax-class based. They intentionally require a
    visible `( figure ) ... ( images/... ) ... ( figure )` wrapper or a visible
    `% source ... idx` wrapper, not a book title, page number, or image hash.
    """
    changes: list[str] = []
    decisions: list[dict] = []
    text = content

    def line_number_at(offset: int) -> int:
        return content.count("\n", 0, offset) + 1

    def drop_with_decision(pattern: re.Pattern[str], change: str, reason: str) -> None:
        nonlocal text

        def repl(match: re.Match[str]) -> str:
            changes.append(change)
            decisions.append(make_decision(
                None,
                "drop_noise",
                reason,
                {
                    "line": line_number_at(match.start()),
                    "text": clean_spaces(match.group(0))[:240],
                },
            ))
            return " "

        text = pattern.sub(repl, text)

    def drop_orphan_percent_before_source_metadata() -> None:
        nonlocal text
        lines = text.splitlines()
        changed = False
        for index in range(len(lines) - 1):
            line = lines[index]
            next_line = lines[index + 1].lstrip()
            if not ORPHAN_SOURCE_PERCENT_BEFORE_METADATA_RE.search(line):
                continue
            if not VISIBLE_CORRUPTED_SOURCE_METADATA_RE.match(next_line):
                continue

            def repl_percent(match: re.Match[str]) -> str:
                return match.group(1) or ""

            lines[index] = ORPHAN_SOURCE_PERCENT_BEFORE_METADATA_RE.sub(repl_percent, line)
            changed = True
            changes.append("removed_visible_literal_source_metadata")
            decisions.append(make_decision(
                None,
                "drop_noise",
                "visible_source_metadata_orphan_percent",
                {
                    "line": index + 1,
                    "text": clean_spaces(line)[-160:],
                    "evidence": "line ends with escaped percent immediately before source/page/idx metadata line",
                },
            ))
        if changed:
            text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")

    def drop_corrupted_source_metadata() -> None:
        nonlocal text

        def repl(match: re.Match[str]) -> str:
            raw_index = match.group("index") or ""
            preserved, removed = preserved_text_after_source_page_index(raw_index)
            following = match.string[match.end(): match.end() + 1]
            replacement = preserved
            if replacement and following and not (following.isspace() or following in r"\,.;:!?)]}"):
                replacement += " "
            changes.append("removed_visible_literal_source_metadata")
            decisions.append(make_decision(
                None,
                "drop_noise",
                "visible_corrupted_source_page_idx_metadata",
                {
                    "line": line_number_at(match.start()),
                    "text": clean_spaces(match.group(0))[:240],
                    "removed_page_index": removed,
                    "preserved_fused_text": preserved,
                },
            ))
            return replacement

        text = VISIBLE_CORRUPTED_SOURCE_METADATA_RE.sub(repl, text)

    def drop_generic_image_caption_residue() -> None:
        nonlocal text

        def repl(match: re.Match[str]) -> str:
            changes.append("removed_visible_generic_image_caption_residue")
            decisions.append(make_decision(
                None,
                "drop_noise",
                "visible_generic_image_caption_residue",
                {
                    "line": line_number_at(match.start()),
                    "text": clean_spaces(match.group(0))[:120],
                    "evidence": "standalone OCR/caption tail before generic image residue",
                },
            ))
            return " "

        text = VISIBLE_GENERIC_IMAGE_CAPTION_RESIDUE_RE.sub(repl, text)

    def restore_visible_figure_metadata() -> None:
        nonlocal text

        def repl(match: re.Match[str]) -> str:
            image_path = match.group("path")
            option = visible_figure_include_option(match.group("option"))
            caption = visible_figure_caption_text(match.group("caption"))
            lines = [
                "",
                r"\begin{figure}[htbp]",
                r"\centering",
                rf"\includegraphics[{option}]{{{image_path}}}",
            ]
            if caption:
                lines.append(rf"\caption{{{caption}}}")
            lines.extend([r"\end{figure}", ""])
            changes.append("restored_visible_figure_metadata")
            decisions.append(make_decision(
                None,
                "rewrite",
                "visible_figure_metadata_restored",
                {
                    "line": line_number_at(match.start()),
                    "image_path": image_path,
                    "include_option": option,
                    "caption": caption,
                    "text": clean_spaces(match.group(0))[:240],
                },
            ))
            return "\n".join(lines)

        text = VISIBLE_RESTORABLE_FIGURE_METADATA_RE.sub(repl, text)

    def drop_external_resource_noise_lines() -> None:
        nonlocal text
        lines = text.splitlines()
        kept_lines: list[str] = []
        changed = False
        for index, line in enumerate(lines):
            visible = clean_spaces(normalize_visible_text(line.strip()))
            if visible and EXTERNAL_RESOURCE_NOISE_LINE_RE.match(visible):
                changed = True
                changes.append("removed_external_resource_noise_line")
                decisions.append(make_decision(
                    None,
                    "drop_noise",
                    "external_resource_or_copyright_noise",
                    {
                        "line": index + 1,
                        "text": visible[:240],
                        "evidence": "standalone external access, QR, URL, copyright, or permission line",
                    },
                ))
                continue
            kept_lines.append(line)
        if changed:
            text = "\n".join(kept_lines) + ("\n" if text.endswith("\n") else "")

    def drop_source_frontmatter_edition_residue() -> None:
        nonlocal text
        lines = text.splitlines()
        drop_indices = set(source_frontmatter_edition_indices(text))
        if not drop_indices:
            return
        kept_lines: list[str] = []
        for index, line in enumerate(lines):
            if index in drop_indices:
                changes.append("removed_source_frontmatter_edition_residue")
                decisions.append(make_decision(
                    None,
                    "drop_noise",
                    "source_frontmatter_edition_residue",
                    {
                        "line": index + 1,
                        "text": clean_spaces(line)[:120],
                        "evidence": "standalone ordinal edition line before the first structural heading",
                    },
                ))
                continue
            kept_lines.append(line)
        text = "\n".join(kept_lines) + ("\n" if text.endswith("\n") else "")

    def drop_source_frontmatter_block_before_plain_unit() -> None:
        nonlocal text
        candidate = source_frontmatter_block_candidate(text)
        if not candidate:
            return
        lines = text.splitlines()
        unit_index = int(candidate["unit_line"]) - 1
        number_index = int(candidate["number_line"]) - 1
        title_index = int(candidate["title_line"]) - 1
        body_lines = lines[unit_index:]
        skipped_body_indexes = {number_index - unit_index, title_index - unit_index}
        kept_lines: list[str] = []
        for rel_index, line in enumerate(body_lines):
            if rel_index == 0:
                kept_lines.append(rf"\chapter{{{latex_escape(str(candidate['unit_title']))}}}")
                continue
            if rel_index in skipped_body_indexes:
                continue
            kept_lines.append(line)
        changes.append("removed_source_frontmatter_block")
        decisions.append(make_decision(
            None,
            "drop_noise",
            "source_frontmatter_block_before_plain_unit",
            {
                "dropped_line_range": {"start": 1, "end": candidate["end_line"]},
                "visible_lines_before": candidate["visible_lines_before"],
                "marker_categories": candidate["marker_categories"],
                "unit_title": candidate["unit_title"],
            },
        ))
        changes.append("promoted_plain_unit_heading")
        decisions.append(make_decision(
            None,
            "style",
            "plain_unit_heading_promoted",
            {
                "line": candidate["unit_line"],
                "unit_title": candidate["unit_title"],
                "evidence": "plain UNIT/number/title heading follows a source-frontmatter block",
            },
        ))
        text = "\n".join(kept_lines) + ("\n" if text.endswith("\n") else "")

    drop_orphan_percent_before_source_metadata()
    drop_corrupted_source_metadata()
    restore_visible_figure_metadata()
    drop_generic_image_caption_residue()
    drop_external_resource_noise_lines()
    drop_source_frontmatter_edition_residue()
    drop_source_frontmatter_block_before_plain_unit()
    drop_with_decision(
        VISIBLE_LITERAL_FIGURE_RE,
        "removed_visible_literal_figure_metadata",
        "visible_literal_figure_metadata_head",
    )
    drop_with_decision(
        VISIBLE_LITERAL_FIGURE_TAIL_RE,
        "removed_visible_literal_figure_metadata",
        "visible_literal_figure_metadata_tail",
    )
    drop_with_decision(
        VISIBLE_BROKEN_FIGURE_METADATA_RE,
        "removed_visible_broken_figure_metadata",
        "visible_broken_figure_metadata",
    )
    drop_with_decision(
        VISIBLE_LITERAL_IMAGE_REF_RE,
        "removed_visible_literal_image_reference_metadata",
        "visible_literal_image_reference_metadata",
    )
    drop_with_decision(
        VISIBLE_LITERAL_STANDALONE_FIGURE_RE,
        "removed_visible_literal_figure_metadata",
        "visible_literal_figure_metadata_standalone",
    )
    drop_with_decision(
        VISIBLE_LITERAL_SOURCE_RE,
        "removed_visible_literal_source_metadata",
        "visible_literal_source_metadata",
    )
    drop_with_decision(
        VISIBLE_SOURCE_TABLE_EVIDENCE_BLOCK_RE,
        "removed_visible_source_table_evidence",
        "visible_source_table_evidence_block",
    )
    drop_with_decision(
        VISIBLE_SOURCE_TABLE_EVIDENCE_LINE_RE,
        "removed_visible_source_table_evidence",
        "visible_source_table_evidence_line",
    )
    drop_with_decision(
        VISIBLE_HTML_COMMENT_RE,
        "removed_visible_html_comment",
        "visible_html_comment_residue",
    )

    go_on_lines = sum(1 for line in text.splitlines() if TEST_NAVIGATION_GO_ON_RE.match(line))
    if go_on_lines >= 3:
        kept_lines: list[str] = []
        for line_no, line in enumerate(text.splitlines(), 1):
            if TEST_NAVIGATION_GO_ON_RE.match(line) or TEST_NAVIGATION_STOP_RE.match(line):
                changes.append("removed_test_navigation_noise")
                decisions.append(make_decision(
                    None,
                    "drop_noise",
                    "test_navigation_noise",
                    {
                        "line": line_no,
                        "text": clean_spaces(line)[:80],
                        "evidence": "standalone navigation line repeated in generated project",
                    },
                ))
                continue
            kept_lines.append(line)
        text = "\n".join(kept_lines)

    cleaned_lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in text.splitlines()]
    return "\n".join(cleaned_lines) + ("\n" if content.endswith("\n") else ""), changes, decisions


def count_test_navigation_noise_lines(content_text: str) -> int:
    """Count repeated standalone test-navigation lines without matching normal prose."""
    lines = content_text.splitlines()
    go_on_lines = sum(1 for line in lines if TEST_NAVIGATION_GO_ON_RE.match(line))
    if go_on_lines < 3:
        return 0
    return sum(
        1
        for line in lines
        if TEST_NAVIGATION_GO_ON_RE.match(line) or TEST_NAVIGATION_STOP_RE.match(line)
    )


def has_repeated_test_navigation_context(content_text: str) -> bool:
    return sum(1 for line in content_text.splitlines() if TEST_NAVIGATION_GO_ON_RE.match(line)) >= 3


def count_test_navigation_arrow_figures(content_text: str, project_dir: Path) -> int:
    figure_re = re.compile(r"\\begin\{figure\}(?:\[[^\]]*\])?.*?\\end\{figure\}", re.S)
    count_value = 0
    for match in figure_re.finditer(content_text):
        info = figure_info_from_block(match.group(0).splitlines(), project_dir)
        if figure_looks_like_test_navigation_arrow(info):
            count_value += 1
    return count_value


def count_repeated_decorative_icon_figures(content_text: str, project_dir: Path) -> int:
    figure_re = re.compile(r"\\begin\{figure\}(?:\[[^\]]*\])?.*?\\end\{figure\}", re.S)
    count_value = 0
    for match in figure_re.finditer(content_text):
        info = figure_info_from_block(match.group(0).splitlines(), project_dir)
        if figure_looks_like_repeated_decorative_icon(info):
            count_value += 1
    return count_value


def has_repeated_decorative_icon_context(content_text: str, project_dir: Path) -> bool:
    return count_repeated_decorative_icon_figures(content_text, project_dir) >= 5


def block_command_text(stripped: str, command: str) -> str:
    match = re.match(rf"\\{re.escape(command)}(?:\*|\[[^\]]*\])?\{{(.+)\}}\s*$", stripped)
    return match.group(1).strip() if match else ""


def latex_begin_environment(stripped: str) -> str:
    match = re.match(r"\\begin\{([^{}]+)\}", stripped)
    return match.group(1) if match else ""


def latex_end_environment(stripped: str, env: str) -> bool:
    return bool(re.match(rf"\\end\{{{re.escape(env)}\}}\s*$", stripped))


def block_type_for_lines(lines: list[str]) -> tuple[str, dict]:
    first = lines[0].strip() if lines else ""
    meta: dict = {}
    env = latex_begin_environment(first)
    if env:
        meta["latex_environment"] = env
        return BLOCK_BOUNDARY_ENVIRONMENTS.get(env, "raw_latex"), meta
    if first.startswith("%") and "source_page_idx" in first:
        meta["noise_reason"] = "source_page_comment"
        return "noise_candidate", meta
    if is_running_header_noise(first):
        meta["noise_reason"] = "running_header"
        return "noise_candidate", meta
    text_label = reading_text_label(first)
    if text_label:
        meta["text_label"] = text_label
        return "reading_text", meta
    if first.startswith(r"\readingtextheading{"):
        meta["text_label"] = "rendered_reading_text_heading"
        return "reading_text", meta
    if first.startswith(r"\readingparagraph{"):
        return "paragraph", meta
    if first.startswith(">"):
        raw = clean_spaces(first.lstrip(">").strip())
        if re.match(r"(?:Part|Unit|Appendix)\s+\d+|Introduction|Overview", raw, re.I):
            meta["heading_source"] = "markdown_blockquote"
            return "chapter", meta
        meta["heading_source"] = "markdown_blockquote"
        return "semantic_heading", meta
    if block_command_text(first, "chapter") or block_command_text(first, "part"):
        meta["heading_source"] = "latex_command"
        return "chapter", meta
    if block_command_text(first, "section") or block_command_text(first, "subsection"):
        meta["heading_source"] = "latex_command"
        return "semantic_heading", meta
    if ACTIVITY_BOX_RE.match(first) or ACTIVITY_HEADING_RE.match(normalize_visible_text(first)):
        return "activity", meta
    if ACTIVITY_STAGE_RE.match(first) or semantic_heading_command(first):
        text = normalize_visible_text(first)
        if re.search(r"\b(?:before|while|after)\s+(?:listening|reading|speaking|writing)\b", text, re.I):
            return "stage", meta
        return "semantic_heading", meta
    if looks_like_top_item_line(first):
        return "task", meta
    if looks_like_letter_item_line(first):
        return "subtask", meta
    if CHOICE_ITEM_RE.match(first):
        return "choice_definition", meta
    if existing_answer_surface("\n".join(lines)) or contains_answer_space(lines):
        return "answer_surface", meta
    if first.startswith("$") or first.startswith(r"\[") or first.startswith(r"\("):
        return "math", meta
    if first.startswith("\\"):
        return "raw_latex", meta
    return "paragraph", meta


def make_block(
    block_id: str,
    block_type: str,
    lines: list[str],
    start_line: int,
    end_line: int,
    parent_id: str = "",
    depth: int = 0,
    meta: dict | None = None,
) -> dict:
    meta = dict(meta or {})
    return {
        "id": block_id,
        "type": block_type,
        "parent_id": parent_id,
        "children": [],
        "depth": depth,
        "source_range": {"start": start_line, "end": end_line},
        "line_count": len(lines),
        "summary": visible_summary(lines),
        "lines": lines,
        "meta": meta,
    }


def attach_child(blocks_by_id: dict[str, dict], parent_id: str, child_id: str) -> None:
    if parent_id and parent_id in blocks_by_id:
        blocks_by_id[parent_id].setdefault("children", []).append(child_id)


def parse_content_blocks(content_text: str, project_dir: Path | None = None) -> list[dict]:
    """Create a conservative line-scan block tree from content.tex.

    The parser deliberately keeps ambiguous LaTeX as raw blocks. It is an
    evidence surface for policy decisions, not an OCR or AST recovery engine.
    """
    lines = content_text.splitlines()
    blocks: list[dict] = []
    blocks_by_id: dict[str, dict] = {}
    next_id = count(1)
    current_activity = ""
    current_stage = ""
    current_task = ""
    i = 0

    def add_block(block_type: str, block_lines: list[str], start: int, end: int, meta: dict | None = None) -> dict:
        nonlocal current_activity, current_stage, current_task
        parent_id = ""
        depth = 0
        if block_type in {
            "stage",
            "task",
            "subtask",
            "choice_definition",
            "reading_text",
            "figure",
            "table",
            "math",
            "answer_surface",
            "paragraph",
            "raw_latex",
        }:
            parent_id = current_task or current_stage or current_activity
            depth = 1 if parent_id == current_activity else 2 if parent_id == current_stage else 3 if parent_id == current_task else 0
        if block_type == "activity":
            current_activity = ""
            current_stage = ""
            current_task = ""
        elif block_type == "chapter":
            current_activity = ""
            current_stage = ""
            current_task = ""
        elif block_type == "semantic_heading" and not ACTIVITY_STAGE_RE.match(block_lines[0].strip()):
            if current_activity and is_activity_boundary_line(block_lines[0].strip()):
                current_activity = ""
                current_stage = ""
                current_task = ""
            parent_id = current_activity if current_activity else ""
            depth = 1 if parent_id else 0

        block = make_block(
            f"b{next(next_id):05d}",
            block_type,
            block_lines,
            start,
            end,
            parent_id=parent_id,
            depth=depth,
            meta=meta,
        )
        if block_type == "figure" and project_dir is not None:
            block["meta"].update(figure_info_from_block(block_lines, project_dir))
        blocks.append(block)
        blocks_by_id[block["id"]] = block
        attach_child(blocks_by_id, parent_id, block["id"])

        if block_type == "activity":
            current_activity = block["id"]
            current_stage = ""
            current_task = ""
        elif block_type == "stage":
            current_stage = block["id"]
            current_task = ""
        elif block_type == "task":
            current_task = block["id"]
        elif block_type in {"chapter", "semantic_heading"} and not parent_id:
            current_stage = ""
            current_task = ""
        return block

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        start = i + 1
        if not stripped:
            add_block("paragraph", [line], start, start, {"blank": True})
            i += 1
            continue

        env = latex_begin_environment(stripped)
        if env and env in BLOCK_BOUNDARY_ENVIRONMENTS:
            block_lines = [line]
            j = i + 1
            while j < len(lines):
                block_lines.append(lines[j])
                if latex_end_environment(lines[j].strip(), env):
                    break
                j += 1
            block_type, meta = block_type_for_lines(block_lines)
            add_block(block_type, block_lines, start, min(j + 1, len(lines)), meta)
            i = j + 1
            continue

        expanded = split_inline_numbered_items(stripped) if looks_like_top_item_line(stripped) else [line]
        if len(expanded) > 1:
            for item in expanded:
                block_type, meta = block_type_for_lines([item])
                add_block(block_type, [item], start, start, meta)
            i += 1
            continue

        block_type, meta = block_type_for_lines([line])
        add_block(block_type, [line], start, start, meta)
        i += 1
    return blocks


def public_block(block: dict) -> dict:
    meta = {
        key: value
        for key, value in (block.get("meta") or {}).items()
        if key in {
            "latex_environment",
            "heading_source",
            "noise_reason",
            "text_label",
            "image_path",
            "caption",
            "width_arg",
            "width_px",
            "height_px",
            "file_size",
            "exists",
        }
    }
    return {
        "id": block["id"],
        "type": block["type"],
        "parent_id": block.get("parent_id", ""),
        "children": block.get("children", []),
        "depth": block.get("depth", 0),
        "source_range": block.get("source_range", {}),
        "line_count": block.get("line_count", 0),
        "summary": block.get("summary", ""),
        "meta": meta,
    }


def block_counts(blocks: list[dict]) -> dict:
    return dict(Counter(block.get("type", "unknown") for block in blocks))


def make_decision(
    block: dict | None,
    action: str,
    reason: str,
    evidence: dict | None = None,
) -> dict:
    evidence = dict(evidence or {})
    decision = {
        "action": action,
        "reason": reason,
        "block_id": block.get("id", "") if block else "",
        "block_type": block.get("type", "") if block else "",
        "source_range": block.get("source_range", {}) if block else {},
        "summary": block.get("summary", "") if block else "",
        "evidence": evidence,
    }
    return decision


def block_model_document(
    before_blocks: list[dict],
    after_blocks: list[dict],
    engine: str,
    answer_density: str,
) -> dict:
    return {
        "schema": BLOCK_MODEL_SCHEMA,
        "engine": engine,
        "answer_density": answer_density,
        "before_counts": block_counts(before_blocks),
        "after_counts": block_counts(after_blocks),
        "blocks": [public_block(block) for block in after_blocks],
        "source_blocks_sample": [public_block(block) for block in before_blocks[:250]],
        "truncated_source_blocks": max(0, len(before_blocks) - 250),
    }


def editorial_decisions_document(decisions: list[dict], engine: str, answer_density: str) -> dict:
    return {
        "schema": EDITORIAL_DECISIONS_SCHEMA,
        "engine": engine,
        "answer_density": answer_density,
        "decision_counts": dict(Counter(item.get("action", "unknown") for item in decisions)),
        "reason_counts": dict(Counter(item.get("reason", "unknown") for item in decisions)),
        "decisions": decisions,
    }


def image_info(project_dir: Path, image_path: str) -> dict:
    path = project_dir / image_path
    info = {"path": image_path, "exists": path.exists()}
    if not path.exists():
        return info
    info["file_size"] = path.stat().st_size
    try:
        from PIL import Image

        with Image.open(path) as image:
            info.update({"width_px": image.width, "height_px": image.height})
            gray = image.convert("L").resize((80, 80))
            pixels = list(gray.getdata())
            total = max(1, len(pixels))
            qr_grid = image.convert("L").resize((41, 41))
            binary = [
                [1 if qr_grid.getpixel((x, y)) < 128 else 0 for x in range(41)]
                for y in range(41)
            ]
            horizontal = sum(
                binary[y][x] != binary[y][x + 1]
                for y in range(41)
                for x in range(40)
            ) / (41 * 40)
            vertical = sum(
                binary[y][x] != binary[y + 1][x]
                for y in range(40)
                for x in range(41)
            ) / (40 * 41)
            info.update({
                "aspect_ratio": round(image.width / image.height, 3) if image.height else 0,
                "black_fraction": round(sum(value < 45 for value in pixels) / total, 4),
                "dark_fraction": round(sum(value < 90 for value in pixels) / total, 4),
                "white_fraction": round(sum(value > 235 for value in pixels) / total, 4),
                "qr_transition_fraction": round((horizontal + vertical) / 2, 4),
            })
    except Exception:
        pass
    return info


def figure_info_from_block(block: list[str], project_dir: Path) -> dict:
    text = "\n".join(block)
    include = re.search(r"\\includegraphics(?:\[width=([^\]]+)\])?\{([^}]+)\}", text)
    caption = re.search(r"\\caption\{([^}]*)\}", text)
    info = {
        "has_includegraphics": bool(include),
        "width_arg": include.group(1) if include and include.group(1) else "",
        "image_path": include.group(2) if include else "",
        "caption": caption.group(1).strip() if caption else "",
    }
    if include:
        info.update(image_info(project_dir, include.group(2)))
    return info


def figure_looks_like_test_navigation_arrow(info: dict) -> bool:
    caption = clean_spaces(info.get("caption", ""))
    caption_generic = not caption or caption.lower() == "image"
    width_px = int(info.get("width_px") or 0)
    height_px = int(info.get("height_px") or 0)
    file_size = int(info.get("file_size") or 999999)
    aspect = float(info.get("aspect_ratio") or 0)
    black = float(info.get("black_fraction") or 0)
    dark = float(info.get("dark_fraction") or 0)
    white = float(info.get("white_fraction") or 0)
    return (
        caption_generic
        and 100 <= width_px <= 220
        and 45 <= height_px <= 115
        and file_size <= 10000
        and 1.65 <= aspect <= 2.35
        and 0.25 <= black <= 0.55
        and dark >= 0.32
        and 0.35 <= white <= 0.75
    )


def figure_looks_like_repeated_decorative_icon(info: dict) -> bool:
    caption = clean_spaces(info.get("caption", ""))
    caption_generic = not caption or caption.lower() == "image"
    width_px = int(info.get("width_px") or 0)
    height_px = int(info.get("height_px") or 0)
    file_size = int(info.get("file_size") or 999999)
    aspect = float(info.get("aspect_ratio") or (width_px / height_px if height_px else 0))
    black = float(info.get("black_fraction") or 0)
    white = float(info.get("white_fraction") or 0)
    return (
        caption_generic
        and 80 <= width_px <= 130
        and 80 <= height_px <= 130
        and file_size <= 5500
        and 0.75 <= aspect <= 1.25
        and 0.18 <= black <= 0.36
        and 0.55 <= white <= 0.78
    )


def figure_looks_like_qr_code(info: dict) -> bool:
    width_px = int(info.get("width_px") or 0)
    height_px = int(info.get("height_px") or 0)
    if not width_px or not height_px:
        return False
    file_size = int(info.get("file_size") or 999999)
    aspect = float(info.get("aspect_ratio") or (width_px / height_px if height_px else 0))
    black = float(info.get("black_fraction") or 0)
    dark = float(info.get("dark_fraction") or 0)
    white = float(info.get("white_fraction") or 0)
    transitions = float(info.get("qr_transition_fraction") or 0)
    return (
        120 <= width_px <= 700
        and 120 <= height_px <= 700
        and file_size <= 120000
        and 0.78 <= aspect <= 1.22
        and 0.24 <= black <= 0.58
        and 0.30 <= dark <= 0.68
        and 0.24 <= white <= 0.62
        and transitions >= 0.26
    )


def count_qr_code_figures(content_text: str, project_dir: Path) -> int:
    count_qr = 0
    for match in re.finditer(r"\\begin\{figure\}(?:\[H\])?.*?\\end\{figure\}", content_text, re.S):
        info = figure_info_from_block(match.group(0).splitlines(), project_dir)
        if figure_looks_like_qr_code(info):
            count_qr += 1
    return count_qr


def figure_is_decorative_candidate(
    info: dict,
    before: list[str],
    after: list[str],
    test_navigation_context: bool = False,
    repeated_icon_context: bool = False,
) -> tuple[bool, str]:
    caption = clean_spaces(info.get("caption", ""))
    caption_generic = not caption or caption.lower() == "image"
    width_px = int(info.get("width_px") or 99999)
    height_px = int(info.get("height_px") or 99999)
    file_size = int(info.get("file_size") or 999999)
    max_dim = max(width_px, height_px)
    context = "\n".join(before[-4:] + after[:6])
    context_has_running_header = any(is_running_header_noise(line) for line in before[-4:] + after[:6])
    context_has_digital_cue = bool(
        re.search(r"Digital Coursebook|download a copy|Watch and talk|STUDENTS SPEAK", context, re.I)
    )
    if caption_generic and max_dim <= 90 and file_size <= 2500:
        return True, "tiny_generic_decorative_image"
    if caption_generic and max_dim <= 150 and file_size <= 5000 and context_has_running_header:
        return True, "tiny_image_next_to_running_header"
    if caption_generic and max_dim <= 150 and file_size <= 5000 and context_has_digital_cue:
        return True, "small_digital_resource_icon"
    if figure_looks_like_qr_code(info):
        return True, "qr_code_external_resource_image"
    if test_navigation_context and figure_looks_like_test_navigation_arrow(info):
        return True, "test_navigation_arrow_image"
    if repeated_icon_context and figure_looks_like_repeated_decorative_icon(info):
        return True, "repeated_generic_decorative_icon"
    return False, ""


def rewrite_figure_block(
    block: list[str],
    project_dir: Path,
    before: list[str],
    after: list[str],
    test_navigation_context: bool = False,
    repeated_icon_context: bool = False,
) -> tuple[list[str], str, dict]:
    info = figure_info_from_block(block, project_dir)
    should_drop, reason = figure_is_decorative_candidate(
        info,
        before,
        after,
        test_navigation_context,
        repeated_icon_context,
    )
    if should_drop:
        info["decision"] = "drop"
        info["reason"] = reason
        return [], reason, info
    rewritten = []
    removed_generic = False
    for line in block:
        if re.match(r"\s*\\caption\{\s*(?:image|Image)?\s*\}\s*$", line.strip()):
            removed_generic = True
            continue
        rewritten.append(line)
    info["decision"] = "keep"
    info["reason"] = "removed_generic_caption" if removed_generic else "kept"
    return rewritten, info["reason"], info


def semantic_heading_command(line: str) -> str:
    text = clean_spaces(line)
    upper = text.upper()
    if upper in SPECIAL_HEADING_COMMANDS:
        command = SPECIAL_HEADING_COMMANDS[upper]
        title = text.strip("$")
        return rf"\{command}{{{latex_escape(title)}}}"
    tip = TIP_HEADING_RE.match(text)
    if tip:
        label = text
        return rf"\tipheading{{{latex_escape(label)}}}"
    focus = FOCUS_HEADING_RE.match(text)
    if focus:
        return rf"\focusheading{{{latex_escape(text)}}}"
    if ACTIVITY_HEADING_RE.match(text):
        return rf"\activityheading{{{latex_escape(text)}}}"
    return ""


def is_activity_start_line(stripped: str) -> bool:
    return bool(ACTIVITY_BOX_RE.match(stripped) or ACTIVITY_HEADING_RE.match(stripped))


def is_activity_boundary_line(stripped: str) -> bool:
    return bool(
        ACTIVITY_BOX_RE.match(stripped)
        or FOCUS_BOX_RE.match(stripped)
        or FOCUS_HEADING_RE.match(stripped)
        or MAJOR_BOUNDARY_RE.match(stripped)
    )


def is_task_scope_start_line(stripped: str) -> bool:
    return bool(TASK_SCOPE_RE.match(stripped))


def is_task_scope_boundary_line(stripped: str) -> bool:
    return bool(
        is_task_scope_start_line(stripped)
        or MAJOR_BOUNDARY_RE.match(stripped)
        or stripped.startswith((r"\readingtextheading", r"\speakingcardheading"))
    )


def looks_like_top_item_line(stripped: str) -> bool:
    if not stripped or stripped.startswith(("\\", "{", "}", "%", "$")):
        return False
    match = TOP_ITEM_RE.match(stripped)
    if not match:
        return False
    body = match.group(2).strip()
    if len(body) < 3 or body.startswith("&") or "&" in body[:18]:
        return False
    return bool(re.match(r"[A-Z\"']", body))


def looks_like_letter_item_line(stripped: str) -> bool:
    if not stripped or stripped.startswith(("\\", "{", "}", "%", "$")):
        return False
    match = LETTER_ITEM_RE.match(stripped)
    if not match:
        return False
    body = match.group(2).strip()
    if len(body) < 2 or body.startswith("&") or "&" in body[:14]:
        return False
    return True


def split_inline_numbered_items(line: str) -> list[str]:
    stripped = line.strip()
    match = TOP_ITEM_RE.match(stripped)
    if not match:
        return [line]
    expected = int(match.group(1)) + 1
    split_positions: list[int] = []
    for item_match in INLINE_TOP_ITEM_RE.finditer(stripped):
        number = int(item_match.group(1))
        if number == expected:
            split_positions.append(item_match.start(1))
            expected += 1
    if not split_positions:
        return [line]
    positions = [0] + split_positions + [len(stripped)]
    return [stripped[positions[index]:positions[index + 1]].strip() for index in range(len(positions) - 1)]


def split_inline_letter_items(line: str) -> list[str]:
    stripped = line.strip()
    if not LETTER_ITEM_RE.match(stripped):
        return [line]
    split_positions = [match.start(1) for match in INLINE_LETTER_ITEM_RE.finditer(stripped)]
    if not split_positions:
        return [line]
    positions = [0] + split_positions + [len(stripped)]
    return [stripped[positions[index]:positions[index + 1]].strip() for index in range(len(positions) - 1)]


def top_list_begin(start_number: int, sequential: bool) -> str:
    if sequential:
        options = [r"label=\arabic*.", "leftmargin=*"]
        if start_number > 1:
            options.append(f"start={start_number}")
        return r"\begin{enumerate}[" + ",".join(options) + "]"
    return r"\begin{description}[leftmargin=*,style=nextline]"


def letter_list_begin(first_label: str, sequential: bool) -> str:
    if sequential:
        options = [r"label=\alph*.", "leftmargin=*"]
        start = ord(first_label) - ord("a") + 1
        if start > 1:
            options.append(f"start={start}")
        return r"\begin{enumerate}[" + ",".join(options) + "]"
    return r"\begin{description}[leftmargin=*,style=nextline]"


def labels_are_sequential(labels: list[str]) -> bool:
    if not labels:
        return False
    first = ord(labels[0])
    return labels == [chr(first + index) for index in range(len(labels))]


def render_choice_lines(lines: list[str]) -> tuple[list[str], int]:
    rendered: list[str] = []
    choice_count = 0
    in_description = False
    for line in lines:
        stripped = line.strip()
        match = CHOICE_ITEM_RE.match(stripped)
        if match and "&" not in match.group(2)[:14]:
            if not in_description:
                rendered.append(r"\begin{description}[leftmargin=*,style=nextline]")
                in_description = True
            rendered.append(rf"\item[{match.group(1)}] {match.group(2).strip()}")
            choice_count += 1
            continue
        if in_description and not stripped:
            continue
        if in_description:
            rendered.append(r"\end{description}")
            in_description = False
        rendered.append(line)
    if in_description:
        rendered.append(r"\end{description}")
    return rendered, choice_count


def format_letter_head(head: str) -> str:
    head = head.strip()
    if "/" in head and len(head) <= 120 and "\\" not in head:
        return rf"\textbf{{{head}}}"
    return head


def render_letter_substructure_as_paragraphs(
    lines: list[str],
    print_layout: str = "classroom",
    answer_density: str = "conservative",
) -> tuple[list[str], list[str]]:
    rendered: list[str] = []
    changes: list[str] = []
    structured_count = 0
    table_env = ""
    for line in lines:
        stripped = line.strip()
        begin_env = latex_begin_environment(stripped)
        if begin_env in {"tabularx", "tabular", "longtable", "array"}:
            table_env = begin_env
            rendered.append(line)
            continue
        if table_env:
            rendered.append(line)
            if latex_end_environment(stripped, table_env):
                table_env = ""
            continue
        if looks_like_letter_item_line(stripped):
            for item in split_inline_letter_items(stripped):
                item_stripped = item.strip()
                match = LETTER_ITEM_RE.match(item_stripped)
                if not match:
                    rendered.append(item)
                    continue
                label = match.group(1)
                text = match.group(2).strip()
                rendered.append(rf"\activitytask{{{label}.}} {text}" if text else rf"\activitytask{{{label}.}}")
                structured_count += 1
                if print_layout == "classroom":
                    space = answer_space_command(
                        text,
                        [],
                        is_subtask=True,
                        answer_density=answer_density,
                    )
                    if space:
                        rendered.append(space)
                        changes.append("added_print_answer_space")
            continue
        rendered.append(line)
    if structured_count >= 2:
        changes.insert(0, "structured_activity_lettered_subtasks_as_paragraphs")
        return rendered, changes
    return lines, []


def lines_contain_table_environment(lines: list[str]) -> bool:
    table_re = re.compile(r"\\begin\{(?:tabularx|tabular|longtable|array)\}")
    return any(table_re.search(line) for line in lines)


def apply_table_layout_safety(content_text: str) -> tuple[str, list[str], list[dict]]:
    """Reduce table overfull pressure without changing table content.

    Generated projects often put full-width `tabularx` boxes in the same
    paragraph as a bold "Table N.N" label, or scale images inside narrow cells
    by a page-relative `\textwidth` fraction that is wider than the column.
    Both are LaTeX box mechanics, not source-content edits.
    """
    lines = content_text.splitlines()
    out: list[str] = []
    changes: list[str] = []
    decisions: list[dict] = []
    table_stack: list[str] = []
    table_width_cap_stack: list[float] = []

    def next_nonempty(index: int, window: int = 4) -> list[str]:
        found: list[str] = []
        for candidate in lines[index + 1:index + 1 + window]:
            if candidate.strip():
                found.append(candidate.strip())
        return found

    def upcoming_table(index: int) -> bool:
        upcoming = next_nonempty(index, 5)
        return any(re.match(r"\\begin\{(?:tabularx|tabular|longtable|array)\}", item) for item in upcoming)

    def column_count_from_begin_line(stripped: str) -> int:
        repeat = re.search(r"\*\{(\d{1,2})\}\{", stripped)
        if repeat:
            return max(1, int(repeat.group(1)))
        match = re.match(r"\\begin\{(?:tabularx|tabular|longtable|array)\}(?:\{[^{}]*\})?\{(.+)\}\s*$", stripped)
        spec = match.group(1) if match else ""
        spec = re.sub(r">\{[^{}]*\}", "", spec)
        spec = re.sub(r"\|+|@\{[^{}]*\}", "", spec)
        columns = re.findall(r"[lcrX]", spec)
        columns.extend(re.findall(r"[pmb]\{", spec))
        return max(1, len(columns))

    def table_image_textwidth_cap(columns: int) -> float:
        return min(0.30, max(0.08, 0.90 / max(1, columns)))

    def cap_table_graphics(line: str, line_no: int) -> str:
        cap = table_width_cap_stack[-1] if table_width_cap_stack else 0.24

        def repl(match: re.Match[str]) -> str:
            options = match.group(1)
            path = match.group(2)
            width_match = re.search(r"width\s*=\s*([0-9.]+)\s*\\textwidth", options)
            if not width_match:
                return match.group(0)
            old_width = float(width_match.group(1))
            if old_width <= cap + 0.001:
                return match.group(0)
            new_width = f"{cap:.2f}".rstrip("0").rstrip(".")
            new_options = (
                options[:width_match.start()]
                + f"width={new_width}\\textwidth"
                + options[width_match.end():]
            )
            if new_options == options:
                return match.group(0)
            changes.append("capped_table_cell_graphic_width")
            decisions.append(make_decision(
                None,
                "rewrite",
                "table_cell_graphic_width_capped",
                {
                    "line": line_no,
                    "image_path": path,
                    "old_options": options,
                    "new_options": new_options,
                    "column_width_cap": cap,
                    "evidence": "includegraphics appears inside tabular-like environment",
                },
            ))
            return rf"\includegraphics[{new_options}]{{{path}}}"

        return re.sub(r"\\includegraphics\[([^\]]+)\]\{([^{}]+)\}", repl, line)

    for index, line in enumerate(lines):
        stripped = line.strip()
        begin_env = latex_begin_environment(stripped)
        if begin_env in {"tabularx", "tabular", "longtable", "array"}:
            table_stack.append(begin_env)
            table_width_cap_stack.append(table_image_textwidth_cap(column_count_from_begin_line(stripped)))

        if stripped.startswith(r"\textbf{Table ") and upcoming_table(index) and r"\par" not in stripped:
            out.append(r"\noindent " + stripped + r"\par\smallskip")
            changes.append("separated_table_caption_from_full_width_table")
            decisions.append(make_decision(
                None,
                "rewrite",
                "table_caption_paragraph_break_before_table",
                {
                    "line": index + 1,
                    "caption": visible_summary([stripped]),
                    "evidence": "bold Table caption immediately precedes tabular-like environment",
                },
            ))
            continue

        rewritten = cap_table_graphics(line, index + 1) if table_stack else line
        out.append(rewritten)

        if table_stack and latex_end_environment(stripped, table_stack[-1]):
            table_stack.pop()
            if table_width_cap_stack:
                table_width_cap_stack.pop()

    if not changes:
        return content_text, [], []
    return "\n".join(out).strip() + "\n", changes, decisions


def lines_contain_list_environment(lines: list[str]) -> bool:
    list_re = re.compile(r"\\(?:begin|end)\{(?:itemize|enumerate|description)\}")
    return any(list_re.search(line) for line in lines)


def contains_answer_space(lines: list[str]) -> bool:
    return any(any(marker in line for marker in ANSWER_SPACE_MARKERS) for line in lines)


def existing_answer_surface(text: str) -> bool:
    return bool(
        re.search(r"\.\s*\.\s*\.|_{3,}|\\rule|\\underline|\\fillin|\\blank", text, re.I)
        or re.search(r"\\begin\{(?:tabularx|tabular|longtable|array)\}", text)
    )


def answer_space_command(
    prompt: str,
    lines: list[str] | None = None,
    *,
    is_subtask: bool = False,
    answer_density: str = "conservative",
) -> str:
    lines = lines or []
    if contains_answer_space(lines):
        return ""
    joined = "\n".join(lines)
    visible = clean_spaces(normalize_visible_text(prompt + " " + joined))
    lower = visible.lower()
    raw = prompt + "\n" + joined
    if not lower:
        return ""
    explicit_written_product = bool(re.search(
        r"\b(?:write|rewrite|write down|note down|make a list|record|prepare .*notes?|"
        r"in your notebook|write sentences?|explain|give reasons?|answer these questions)\b",
        lower,
    ))
    explicit_written_surface = bool(re.search(
        r"\b(?:write|rewrite|write down|note down|make a list|record|prepare .*notes?|"
        r"in your notebook|write sentences?|explain|give reasons?)\b",
        lower,
    ))
    if re.search(r"\b(?:listen|listening|watch|check)\b", lower) and not explicit_written_surface:
        return ""
    if re.search(
        r"\b(?:discuss|talk to your partner|compare with your partner|in pairs|with a partner|"
        r"work in pairs|work with a partner|work in small groups|say sentences)\b",
        lower,
    ) and not explicit_written_surface:
        return ""
    if re.search(
        r"\b(?:complete|copy|add|put|sort|enter)\b.{0,60}\b(?:table|chart|column|columns)\b",
        lower,
    ):
        return ""
    if re.search(r"\b(?:complete the sentences|fill (?:in )?(?:the )?gaps?|which word .* gap|gap)\b", lower):
        if not re.search(r"\b(?:write sentences?|explain|give reasons?)\b", lower):
            return ""
    if re.search(r"\\begin\{description\}|\\item\[[A-Da-d]\]", raw):
        if not re.search(r"\b(?:write sentences?|explain|give reasons?|answer these questions)\b", lower):
            return ""
    if re.search(
        r"\b(?:choose|circle|tick|underline|match|listen|listening|watch|read|check)\b",
        lower,
    ):
        if not explicit_written_product:
            return ""
    if re.search(r"\b(?:ask and answer|practise asking and answering|practice asking and answering)\b", lower):
        if not re.search(r"\b(?:write|write down|note down|make a list|record|prepare .*notes?|in your notebook)\b", lower):
            return ""
    if re.search(r"\banswer (?:the )?questions? (?:that follow|below|above)\b", lower):
        return ""
    if re.search(r"\b(?:watch the video|listen again and check|check your answers|your teacher will give you)\b", lower):
        return ""
    if re.search(r"\b(?:discuss|talk to your partner|compare with your partner|in pairs|with a partner|work in pairs|work with a partner|work in small groups|say sentences)\b", lower):
        if not re.search(r"\b(?:write|write down|note down|make a list|record|prepare .*notes?|in your notebook)\b", lower):
            return ""
    if existing_answer_surface(raw) and not re.search(r"\b(?:write|write down|note down|make a list|record|answer these questions|explain|give reasons?)\b", lower):
        return ""
    if re.search(
        r"\b(?:rewrite|redraft|write)\b.*\b(?:email|letter|article|paragraph|summary|report|essay|story|account)\b",
        lower,
    ) and re.search(r"\b(?:formal|informal|style|notebook|frame|draft|final)\b", lower):
        return r"\printwritingbox"
    if re.search(r"\bwrite (?:an?|your|a short|the) (?:article|email|letter|paragraph|summary|report|essay|description|story|account|talk)\b", lower):
        return r"\printwritingbox"
    if re.search(r"\bset yourself a personal goal\b", lower):
        return r"\printwritingbox"
    if re.search(r"\b(?:make a list|write a list|write down|note down|record your answers|prepare .*notes?)\b", lower):
        return r"\printlistanswer"
    if re.search(r"\b(?:answer these questions|answer the questions|give reasons?|explain)\b", lower):
        return r"\printmediumanswer" if not is_subtask else r"\printshortanswer"
    if answer_density == "workbook" and re.search(r"\b(?:why do you think|what do you think|how far|to what extent)\b", lower):
        return r"\printmediumanswer" if not is_subtask else r"\printshortanswer"
    if answer_density == "workbook" and is_subtask and re.search(r"\?\s*$", prompt.strip()):
        return r"\printshortanswer"
    if re.search(r"\b(?:match each)\b", lower):
        return ""
    if re.match(r"^(?:\d{1,2}\s+)?(?:write|describe|summari[sz]e)\b", lower) and not re.search(r"\b(?:discuss|listen|read)\b", lower):
        return r"\printshortanswer"
    return ""


GLOBAL_PRODUCTION_RE = re.compile(
    r"\b(?:write|rewrite|write down|answer these questions|answer the questions|make a list|make notes|"
    r"take notes|record|explain|give reasons?|summari[sz]e|describe|prepare .*notes?|set yourself a personal goal)\b",
    re.I,
)
GLOBAL_PRODUCT_RE = re.compile(
    r"\b(?:in your notebook|sentences?|paragraph|email|article|report|summary|answers?|questions?|"
    r"list|notes?|reasons?|description|story|account|blog|review|quotes?|goal)\b",
    re.I,
)


def next_nonempty_line(lines: list[str], start: int, window: int = 4) -> str:
    for line in lines[start + 1:start + 1 + window]:
        if line.strip():
            return line.strip()
    return ""


def continuation_material_line(stripped: str) -> bool:
    if not stripped:
        return False
    if stripped.startswith(("\\begin{enumerate}", "\\begin{itemize}", "\\item")):
        return True
    if looks_like_top_item_line(stripped):
        return True
    return bool(re.search(r"^\d{1,2}\s+.+\s+\d{1,2}\s+", stripped))


def global_answer_prompt_candidate(visible: str) -> bool:
    lower = visible.lower()
    if re.search(r"\b(?:work in pairs|ask and answer .* together|orally|discuss|talk to your partner)\b", lower):
        if not re.search(r"\b(?:write|rewrite|write down|make a list|note down|record|prepare .*notes?)\b", lower):
            return False
    if not (
        re.match(r"^\d{1,2}\s+", visible)
        or re.search(r"\b(?:try writing|you are going to .*write|write|rewrite|write down|answer these questions|"
                     r"answer the questions|make a list|make notes|take notes|record|prepare .*notes?|"
                     r"set yourself a personal goal)\b", lower)
    ):
        return False
    return bool(GLOBAL_PRODUCTION_RE.search(visible) and GLOBAL_PRODUCT_RE.search(visible))


def support_material_prompt(visible: str) -> bool:
    lower = visible.lower()
    if not re.search(r"\b(?:write|rewrite|redraft|answer|explain|summari[sz]e|describe)\b", lower):
        return False
    return bool(re.search(
        r"\b(?:look at|read|study|use|refer to)\b.*\b(?:email|letter|article|text|blog|report|story|"
        r"picture|photograph|image|extract|frame|table|chart|diagram)\b",
        lower,
    ))


def deferred_answer_boundary(stripped: str, visible: str) -> bool:
    if not stripped:
        return False
    if stripped.startswith((
        r"\chapter",
        r"\section",
        r"\subsection",
        r"\activityheading",
        r"\stageheading",
        r"\learningheading",
        r"\tipheading",
        r"\focusheading",
        r"\reflectionheading",
        r"\progressheading",
        r"\examheading",
        r"\readingtextheading",
    )):
        return True
    if is_activity_start_line(stripped) or is_activity_boundary_line(stripped):
        return True
    if re.match(
        r"^(?:Review and reflection|Unit review questions|Check your progress|Reflection|Exam-style question|"
        r"Language focus|Reading tip|Writing tip|Speaking tip)\b",
        visible,
        re.I,
    ):
        return True
    return bool(looks_like_top_item_line(stripped))


def add_global_answer_spaces(content_text: str, answer_density: str) -> tuple[str, list[str], list[dict]]:
    lines = content_text.splitlines()
    out: list[str] = []
    changes: list[str] = []
    decisions: list[dict] = []
    pending: dict | None = None
    table_depth = 0

    def emit_pending(after_line_no: int) -> None:
        nonlocal pending
        if not pending:
            return
        out.append(pending["command"])
        changes.append("added_global_print_answer_space")
        command_name = pending["command"].replace("\\", "")
        decisions.append(make_decision(
            None,
            "add_answer_space",
            f"global_{command_name}:{answer_density}",
            {
                "line": pending["line"],
                "inserted_after_line": after_line_no,
                "nearby_prompt": pending["prompt"],
            },
        ))
        pending = None

    for index, line in enumerate(lines):
        stripped = line.strip()
        begins_table = bool(re.match(r"\\begin\{(?:tabularx|tabular|longtable|array)\}", stripped))
        ends_table = bool(re.match(r"\\end\{(?:tabularx|tabular|longtable|array)\}", stripped))
        if begins_table:
            table_depth += 1
        in_table = table_depth > 0
        if pending:
            visible = clean_spaces(normalize_visible_text(stripped))
            current_is_new_prompt = (
                bool(stripped)
                and not stripped.startswith(("\\", "%"))
                and not in_table
                and global_answer_prompt_candidate(visible)
            )
            emit_before_current_line = (
                pending.get("wait_boundary")
                and deferred_answer_boundary(stripped, visible)
                and index + 1 - int(pending.get("line", index + 1)) >= 5
            )
            if emit_before_current_line or current_is_new_prompt:
                emit_pending(index)
            else:
                out.append(line)
                wait_env = pending.get("wait_env")
                if wait_env:
                    if stripped == rf"\end{{{wait_env}}}":
                        emit_pending(index + 1)
                elif stripped and not pending.get("wait_boundary"):
                    emit_pending(index + 1)
                continue

        out.append(line)
        if ends_table and table_depth:
            table_depth -= 1
        if not stripped or stripped.startswith("\\") or stripped.startswith("%") or in_table:
            continue
        visible = clean_spaces(normalize_visible_text(stripped))
        if not global_answer_prompt_candidate(visible):
            continue
        lookahead = lines[index:index + 7]
        if contains_answer_space(lookahead) or existing_answer_surface("\n".join(lookahead)):
            continue
        if lines_contain_table_environment(lookahead):
            continue
        command = answer_space_command(stripped, [], answer_density=answer_density)
        if not command:
            continue
        next_line = next_nonempty_line(lines, index)
        begin_env = re.match(r"\\begin\{(itemize|enumerate|description)\}", next_line)
        if support_material_prompt(visible):
            pending = {
                "command": command,
                "line": index + 1,
                "prompt": visible[:240],
                "wait_boundary": True,
            }
        elif continuation_material_line(next_line):
            pending = {"command": command, "line": index + 1, "prompt": visible[:240]}
            if begin_env:
                pending["wait_env"] = begin_env.group(1)
        else:
            pending = {"command": command, "line": index + 1, "prompt": visible[:240]}
            emit_pending(index + 1)

    if pending:
        emit_pending(len(lines))
    return "\n".join(out).strip() + "\n", changes, decisions


def add_speaking_card_page_guards(content_text: str) -> tuple[str, list[str], list[dict]]:
    """Style visible speaking-card labels and keep each short card together.

    The trigger is intentionally structural: a visible chapter/section/plain
    heading mentioning "Speaking cards" plus at least three standalone `Card N`
    lines. It does not use title, sample id, or page number.
    """
    lines = content_text.splitlines()
    if sum(1 for line in lines if SPEAKING_CARD_LINE_RE.match(normalize_visible_text(line.strip()))) < 3:
        return content_text, [], []

    out: list[str] = []
    changes: list[str] = []
    decisions: list[dict] = []
    in_speaking_cards = False

    for index, line in enumerate(lines):
        stripped = line.strip()
        visible = normalize_visible_text(stripped)
        chapterish = bool(re.match(r"\\(?:chapter|section|subsection)(?:\[[^\]]*\])?\{", stripped))
        if chapterish:
            in_speaking_cards = bool(SPEAKING_CARDS_CONTEXT_RE.search(visible))
        elif SPEAKING_CARDS_HEADING_RE.match(visible):
            in_speaking_cards = True

        if in_speaking_cards and SPEAKING_CARD_LINE_RE.match(visible):
            out.append(rf"\speakingcardheading{{{latex_escape(visible)}}}")
            changes.append("styled_speaking_card_heading")
            decisions.append(make_decision(
                None,
                "style",
                "speaking_card_heading_page_guard",
                {"line": index + 1, "text": visible, "needspace": r"13\baselineskip"},
            ))
            continue
        out.append(line)

    if not changes:
        return content_text, [], []
    return "\n".join(out).strip() + "\n", changes, decisions


def render_letter_substructure(
    lines: list[str],
    print_layout: str = "classroom",
    answer_density: str = "conservative",
) -> tuple[list[str], list[str]]:
    if lines_contain_list_environment(lines):
        return lines, []
    if lines_contain_table_environment(lines):
        return render_letter_substructure_as_paragraphs(
            lines,
            print_layout=print_layout,
            answer_density=answer_density,
        )

    expanded: list[str] = []
    for line in lines:
        stripped = line.strip()
        if looks_like_letter_item_line(stripped):
            expanded.extend(split_inline_letter_items(stripped))
        else:
            expanded.append(line)

    prefix: list[str] = []
    items: list[dict] = []
    current: dict | None = None
    for line in expanded:
        stripped = line.strip()
        if looks_like_letter_item_line(stripped):
            match = LETTER_ITEM_RE.match(stripped)
            if current:
                items.append(current)
            current = {"label": match.group(1), "head": match.group(2).strip(), "lines": []}
            continue
        if current:
            current["lines"].append(line)
        else:
            prefix.append(line)
    if current:
        items.append(current)

    choice_total = 0
    for item in items:
        choice_total += sum(1 for line in item["lines"] if CHOICE_ITEM_RE.match(line.strip()))
    if len(items) < 2 and choice_total == 0:
        return lines, []
    if len(items) > 6 and choice_total == 0:
        return render_letter_substructure_as_paragraphs(
            lines,
            print_layout=print_layout,
            answer_density=answer_density,
        )

    labels = [item["label"] for item in items]
    sequential = labels_are_sequential(labels)
    rendered = list(prefix)
    rendered.append(letter_list_begin(labels[0], sequential))
    changes = ["structured_activity_lettered_subtasks"]
    for item in items:
        head = format_letter_head(item["head"])
        if sequential:
            rendered.append(rf"\item {head}" if head else r"\item")
        else:
            rendered.append(rf"\item[{item['label']}.] {head}" if head else rf"\item[{item['label']}.]")
        child_lines, child_choices = render_choice_lines(item["lines"])
        if child_choices:
            changes.append("structured_activity_choice_definitions")
        rendered.extend(child_lines)
        if print_layout == "classroom":
            space = answer_space_command(
                item["head"],
                child_lines,
                is_subtask=True,
                answer_density=answer_density,
            )
            if space:
                rendered.append(space)
                changes.append("added_print_answer_space")
    rendered.append(r"\end{enumerate}" if sequential else r"\end{description}")
    return rendered, changes


def structure_activity_item_body(
    lines: list[str],
    print_layout: str = "classroom",
    answer_density: str = "conservative",
) -> tuple[list[str], list[str]]:
    return render_letter_substructure(lines, print_layout=print_layout, answer_density=answer_density)


def render_top_items(
    items: list[dict],
    print_layout: str = "classroom",
    answer_density: str = "conservative",
) -> tuple[list[str], list[str]]:
    if not items:
        return [], []
    if any(lines_contain_table_environment(item["lines"]) for item in items):
        rendered: list[str] = []
        changes = ["structured_activity_numbered_tasks_as_paragraphs"]
        for item in items:
            text = item["text"].strip()
            rendered.append(rf"\activitytask{{{item['number']}.}} {text}" if text else rf"\activitytask{{{item['number']}.}}")
            body_lines, body_changes = structure_activity_item_body(
                item["lines"],
                print_layout=print_layout,
                answer_density=answer_density,
            )
            rendered.extend(body_lines)
            if print_layout == "classroom":
                space = answer_space_command(text, body_lines, answer_density=answer_density)
                if space:
                    rendered.append(space)
                    body_changes.append("added_print_answer_space")
            changes.extend(body_changes)
        return rendered, changes
    numbers = [item["number"] for item in items]
    sequential = numbers == list(range(numbers[0], numbers[0] + len(numbers)))
    rendered = [top_list_begin(numbers[0], sequential)]
    changes = ["structured_activity_numbered_tasks"]
    for item in items:
        text = item["text"].strip()
        if sequential:
            rendered.append(rf"\item {text}" if text else r"\item")
        else:
            rendered.append(rf"\item[{item['number']}.] {text}" if text else rf"\item[{item['number']}.]")
        body_lines, body_changes = structure_activity_item_body(
            item["lines"],
            print_layout=print_layout,
            answer_density=answer_density,
        )
        rendered.extend(body_lines)
        if print_layout == "classroom":
            space = answer_space_command(text, body_lines, answer_density=answer_density)
            if space:
                rendered.append(space)
                body_changes.append("added_print_answer_space")
        changes.extend(body_changes)
    rendered.append(r"\end{enumerate}" if sequential else r"\end{description}")
    return rendered, changes


def structure_task_sequence_lines(
    block: list[str],
    print_layout: str = "classroom",
    answer_density: str = "conservative",
) -> tuple[list[str], list[str], list[str]]:
    expanded: list[str] = []
    for line in block:
        stripped = line.strip()
        if looks_like_top_item_line(stripped):
            expanded.extend(split_inline_numbered_items(stripped))
        else:
            expanded.append(line)

    rendered: list[str] = []
    pending_items: list[dict] = []
    current_item: dict | None = None
    changes: list[str] = []

    def flush_items() -> None:
        nonlocal pending_items, current_item, changes
        if current_item:
            pending_items.append(current_item)
            current_item = None
        if not pending_items:
            return
        item_lines, item_changes = render_top_items(
            pending_items,
            print_layout=print_layout,
            answer_density=answer_density,
        )
        rendered.extend(item_lines)
        changes.extend(item_changes)
        pending_items = []

    for line in expanded:
        stripped = line.strip()
        if ACTIVITY_STAGE_RE.match(stripped):
            flush_items()
            rendered.append(line)
            continue
        if looks_like_top_item_line(stripped):
            if not current_item and not pending_items and rendered:
                prefix_lines, prefix_changes = render_letter_substructure(
                    rendered,
                    print_layout=print_layout,
                    answer_density=answer_density,
                )
                if prefix_changes:
                    rendered = prefix_lines
                    changes.extend(prefix_changes)
            if current_item:
                pending_items.append(current_item)
            match = TOP_ITEM_RE.match(stripped)
            current_item = {
                "number": int(match.group(1)),
                "text": match.group(2).strip(),
                "lines": [],
            }
            continue
        if current_item:
            current_item["lines"].append(line)
        else:
            rendered.append(line)

    flush_items()
    if not changes:
        letter_lines, letter_changes = render_letter_substructure(
            rendered,
            print_layout=print_layout,
            answer_density=answer_density,
        )
        if letter_changes:
            rendered = letter_lines
            changes.extend(letter_changes)
    return rendered, changes, []


def structure_single_activity_block(
    block: list[str],
    print_layout: str = "classroom",
    answer_density: str = "conservative",
) -> tuple[list[str], list[str], list[str]]:
    if not lines_contain_list_environment(block):
        return structure_task_sequence_lines(
            block,
            print_layout=print_layout,
            answer_density=answer_density,
        )

    rendered: list[str] = []
    changes: list[str] = []
    warnings: list[str] = []
    segment: list[str] = []
    list_stack: list[str] = []

    def flush_segment() -> None:
        nonlocal segment, changes, warnings
        if not segment:
            return
        structured, segment_changes, segment_warnings = structure_task_sequence_lines(
            segment,
            print_layout=print_layout,
            answer_density=answer_density,
        )
        rendered.extend(structured)
        changes.extend(segment_changes)
        warnings.extend(segment_warnings)
        segment = []

    for line in block:
        stripped = line.strip()
        begin_env = latex_begin_environment(stripped)
        end_env = None
        for env in reversed(list_stack):
            if latex_end_environment(stripped, env):
                end_env = env
                break

        if begin_env in {"itemize", "enumerate", "description"}:
            flush_segment()
            list_stack.append(begin_env)
            rendered.append(line)
            continue

        if list_stack:
            rendered.append(line)
            if end_env:
                while list_stack:
                    popped = list_stack.pop()
                    if popped == end_env:
                        break
            continue

        segment.append(line)

    flush_segment()
    if not changes:
        warnings.append("activity_block_with_existing_list_preserved")
    return rendered, changes, warnings


def structure_activity_blocks(
    content_text: str,
    print_layout: str = "classroom",
    answer_density: str = "conservative",
) -> tuple[str, list[str], list[str]]:
    lines = content_text.splitlines()
    rendered: list[str] = []
    changes: list[str] = []
    warnings: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        rendered.append(line)
        if not is_task_scope_start_line(stripped):
            i += 1
            continue
        is_activity_scope = bool(ACTIVITY_BOX_RE.match(stripped))
        block: list[str] = []
        i += 1
        while i < len(lines):
            next_stripped = lines[i].strip()
            if is_task_scope_boundary_line(next_stripped):
                break
            block.append(lines[i])
            i += 1
        structured, block_changes, block_warnings = structure_single_activity_block(
            block,
            print_layout=print_layout,
            answer_density=answer_density,
        )
        rendered.extend(structured)
        if is_activity_scope:
            changes.extend(block_changes)
        else:
            changes.extend([
                change.replace("structured_activity", "structured_semantic_scope", 1)
                if change.startswith("structured_activity")
                else change
                for change in block_changes
            ])
        warnings.extend(block_warnings)
    return "\n".join(rendered).strip() + "\n", changes, warnings


def activity_structure_metrics(content_text: str) -> dict:
    raw_top_lines = 0
    raw_letter_lines = 0
    semantic_scope_raw_top_lines = 0
    semantic_scope_raw_letter_lines = 0
    current_scope = ""
    for line in content_text.splitlines():
        stripped = line.strip()
        if MAJOR_BOUNDARY_RE.match(stripped):
            current_scope = ""
        if is_task_scope_start_line(stripped):
            current_scope = "activity" if ACTIVITY_BOX_RE.match(stripped) else "semantic"
            continue
        if not current_scope:
            continue
        if looks_like_top_item_line(stripped):
            count_value = len(split_inline_numbered_items(stripped))
            if current_scope == "activity":
                raw_top_lines += count_value
            else:
                semantic_scope_raw_top_lines += count_value
        if looks_like_letter_item_line(stripped):
            count_value = len(split_inline_letter_items(stripped))
            if current_scope == "activity":
                raw_letter_lines += count_value
            else:
                semantic_scope_raw_letter_lines += count_value
    return {
        "activity_plain_numbered_task_lines": raw_top_lines,
        "activity_plain_lettered_subtask_lines": raw_letter_lines,
        "semantic_scope_plain_numbered_task_lines": semantic_scope_raw_top_lines,
        "semantic_scope_plain_lettered_subtask_lines": semantic_scope_raw_letter_lines,
        "activity_numbered_lists": content_text.count(r"\begin{enumerate}[label=\arabic*."),
        "activity_paragraph_tasks": content_text.count(r"\activitytask{"),
        "activity_lettered_lists": content_text.count(r"\begin{enumerate}[label=\alph*."),
        "activity_description_lists": content_text.count(r"\begin{description}[leftmargin=*,style=nextline]"),
        "reading_text_headings": content_text.count(r"\readingtextheading{"),
        "reading_paragraph_labels": content_text.count(r"\readingparagraph{"),
        "speaking_card_headings": content_text.count(r"\speakingcardheading{"),
        "plain_speaking_card_lines": len(re.findall(r"(?mi)^Card\s+(?:\d{1,3}|[A-Z])\s*$", content_text)),
        "plain_reading_text_labels": len(re.findall(r"(?m)^Text\s+\d+\.\d+\s*$", content_text, re.I)),
        "digital_text_copy_prompts": len(re.findall(
            r"(?mi)^You can download a copy of Text\s+\d+\.\d+\s+from the Digital Coursebook\.?\s*$",
            content_text,
        )),
        "print_answer_space_blocks": sum(content_text.count(marker) for marker in ANSWER_SPACE_MARKERS),
        "print_short_answers": content_text.count(r"\printshortanswer"),
        "print_medium_answers": content_text.count(r"\printmediumanswer"),
        "print_long_answers": content_text.count(r"\printlonganswer"),
        "print_list_answers": content_text.count(r"\printlistanswer"),
        "print_writing_boxes": (
            content_text.count(r"\printwritingbox")
            + content_text.count(r"\printchapterendwritingbox")
        ),
        "print_chapter_end_writing_boxes": content_text.count(r"\printchapterendwritingbox"),
    }


def content_model_metrics(project_dir: Path, content_text: str) -> dict:
    figure_re = re.compile(r"\\begin\{figure\}(?:\[[^\]]*\])?.*?\\end\{figure\}", re.S)
    figures = []
    for match in figure_re.finditer(content_text):
        block = match.group(0).splitlines()
        figures.append(figure_info_from_block(block, project_dir))
    tiny = [
        item for item in figures
        if max(int(item.get("width_px") or 99999), int(item.get("height_px") or 99999)) <= 150
        and int(item.get("file_size") or 999999) <= 5000
    ]
    semantic_markers = [
        r"\learningheading{",
        r"\tipheading{",
        r"\focusheading{",
        r"\reflectionheading{",
        r"\progressheading{",
        r"\readingheading{",
        r"\stageheading{",
        r"\activityheading{",
        r"\examheading{",
        r"\exerciseheading{",
    ]
    plain_semantic_candidates = 0
    for line in content_text.splitlines():
        stripped = line.strip()
        if stripped and semantic_heading_command(stripped):
            plain_semantic_candidates += 1
    return {
        "figures": len(figures),
        "tiny_or_icon_like_figures": len(tiny),
        "generic_image_captions": sum(1 for item in figures if clean_spaces(item.get("caption", "")).lower() == "image"),
        "running_header_noise_lines": sum(1 for line in content_text.splitlines() if is_running_header_noise(line)),
        "semantic_heading_candidates": plain_semantic_candidates,
        "semantic_heading_boxes": sum(content_text.count(marker) for marker in semantic_markers),
        **activity_structure_metrics(content_text),
    }


def audit_project(project_dir: Path) -> dict:
    main_path = project_dir / "main.tex"
    content_path = project_dir / "chapters" / "content.tex"
    log_path = project_dir / "main.log"
    main_text = read_text(main_path) if main_path.exists() else ""
    content_text = read_text(content_path) if content_path.exists() else ""
    log_text = read_text(log_path) if log_path.exists() else ""
    overfull_values = [
        float(value)
        for value in re.findall(r"Overfull \\hbox \(([0-9.]+)pt too wide\)", log_text)
    ]
    source_comments = re.findall(r"source_page_idx", content_text)
    model = content_model_metrics(project_dir, content_text)
    metrics = {
        "project_dir": str(project_dir),
        "has_main_tex": main_path.exists(),
        "has_content_tex": content_path.exists(),
        "title": extract_latex_macro(main_text, "title"),
        "subtitle": extract_latex_macro(main_text, "subtitle"),
        "author": extract_latex_macro(main_text, "author"),
        "documentclass": (re.search(r"\\documentclass(?:\[[^\]]*\])?\{[^}]+\}", main_text) or [""])[0],
        "language_guess": language_from_content(main_text, content_text) if main_text or content_text else "",
        "zlib_title_noise": bool(re.search(r"z-library|z-lib|1lib", main_text, re.I)),
        "mechanical_title_noise": (
            clean_mechanical_title_noise(latex_text_to_plain(extract_latex_macro(main_text, "title")))
            != clean_spaces(latex_text_to_plain(extract_latex_macro(main_text, "title")))
        ),
        "blockquote_lines": len(re.findall(r"(?m)^>\s*", content_text)),
        "unit_blockquote_lines": len(re.findall(r"(?m)^>\s*Unit\s+\d+", content_text, re.I)),
        "part_blockquote_lines": len(re.findall(r"(?m)^>\s*Part\s+\d+", content_text, re.I)),
        "appendix_blockquote_lines": len(re.findall(r"(?m)^>\s*Appendix\b", content_text, re.I)),
        "chapters": len(re.findall(r"\\chapter(?:\[[^\]]*\])?\{", content_text)),
        "star_chapters": len(re.findall(r"\\chapter\*\{", content_text)),
        "sections": len(re.findall(r"\\section\{", content_text)),
        "star_sections": len(re.findall(r"\\section\*\{", content_text)),
        "subsections": len(re.findall(r"\\subsection\{", content_text)),
        "star_subsections": len(re.findall(r"\\subsection\*\{", content_text)),
        "source_numbered_section_titles": count_source_numbered_section_titles(content_text),
        "source_numbered_star_section_titles": count_source_numbered_star_section_titles(content_text),
        "source_structural_section_titles": count_source_structural_section_titles(content_text),
        "source_structural_star_section_titles": count_source_structural_star_section_titles(content_text),
        "source_structural_chapter_titles": count_source_structural_chapter_titles(content_text),
        "generic_image_captions": model["generic_image_captions"],
        "figure_H": len(re.findall(r"\\begin\{figure\}\[H\]", content_text)),
        "line_start_bullet_glyphs": len(re.findall(r"(?m)^•\s+", content_text)),
        "textual_inline_math_list_lines": sum(
            1 for line in content_text.splitlines() if unwrap_textual_inline_math_list(line.strip())
        ),
        "inline_ensuremath_prose_fragments": count_inline_ensuremath_prose_fragments(content_text),
        "long_unbreakable_ensuremath_lines": count_long_unbreakable_ensuremath_lines(content_text),
        "raw_markdown_images": len(re.findall(r"!\[[^\]]*\]\(", content_text)),
        "editorial_markdown_image_residue": len(re.findall(r"!\s*\[\s*Illustration\b", content_text, re.I)),
        "unsafe_page_flush_suppression": len(UNSAFE_PAGE_FLUSH_RE.findall(main_text)),
        "visible_literal_figure_metadata": (
            len(VISIBLE_LITERAL_FIGURE_RE.findall(content_text))
            + len(VISIBLE_LITERAL_FIGURE_TAIL_RE.findall(content_text))
            + len(VISIBLE_LITERAL_STANDALONE_FIGURE_RE.findall(content_text))
        ),
        "visible_broken_figure_metadata": count_visible_broken_figure_metadata(content_text),
        "visible_generic_image_caption_residue": count_visible_generic_image_caption_residue(content_text),
        "visible_literal_image_reference_metadata": len(VISIBLE_LITERAL_IMAGE_REF_RE.findall(content_text)),
        "visible_literal_source_metadata": count_visible_literal_source_metadata(content_text),
        "visible_source_table_evidence": count_visible_source_table_evidence(content_text),
        "external_resource_noise_lines": count_external_resource_noise_lines(content_text),
        "source_frontmatter_edition_residue": count_source_frontmatter_edition_residue(content_text),
        "source_frontmatter_block_residue": count_source_frontmatter_block_residue(content_text),
        "visible_html_comments": len(VISIBLE_HTML_COMMENT_RE.findall(content_text)),
        "test_navigation_noise_lines": count_test_navigation_noise_lines(content_text),
        "test_navigation_arrow_figures": count_test_navigation_arrow_figures(content_text, project_dir),
        "qr_code_figures": count_qr_code_figures(content_text, project_dir),
        "repeated_decorative_icon_figures": count_repeated_decorative_icon_figures(content_text, project_dir),
        "visible_markdown_heading_examples": re.findall(r"(?m)^>\s*.{1,120}", content_text)[:20],
        "source_page_comments": len(source_comments),
        "local_paths_visible": len(
            re.findall(
                r"/(?:home|Users)/[^/\s]+/|/tmp/selfloop|work/selfloop",
                main_text + "\n" + content_text,
            )
        ),
        "todo_debug_markers": len(re.findall(r"\bTODO\b|\bFIXME\b|\bDEBUG\b", main_text + "\n" + content_text)),
        "overfull_count": len(overfull_values),
        "overfull_max_pt": max(overfull_values) if overfull_values else 0,
        "overfull_gt_50": sum(1 for value in overfull_values if value > 50),
        "overfull_gt_100": sum(1 for value in overfull_values if value > 100),
        "missing_character_count": len(re.findall(r"Missing character:", log_text)),
    }
    metrics.update(model)
    return metrics


def heading_command_for_blockquote(title: str, next_title: str | None) -> tuple[list[str], bool]:
    raw = clean_spaces(title.lstrip(">").strip())
    raw = normalize_title_words(raw)
    lower = raw.lower()
    if re.match(r"part\s+\d+\b", lower):
        part_title = raw
        if next_title:
            part_title = f"{raw}: {normalize_title_words(next_title)}"
        return [rf"\part{{{latex_escape(part_title)}}}"], bool(next_title)
    unit_match = re.match(r"Unit\s+\d+\s*:\s*(.+)$", raw, re.I)
    if unit_match:
        short_title = normalize_title_words(unit_match.group(1))
        return [rf"\chapter[{latex_escape(short_title)}]{{{latex_escape(raw)}}}"], False
    appendix_match = re.match(r"Appendix\s+\d*\s*:?\s*(.*)$", raw, re.I)
    if appendix_match:
        short_title = normalize_title_words(appendix_match.group(1)) or raw
        return [rf"\chapter[{latex_escape(short_title)}]{{{latex_escape(raw)}}}"], False
    if lower.startswith("introduction") or lower.startswith("overview"):
        escaped = latex_escape(raw)
        return [rf"\chapter*{{{escaped}}}", rf"\addcontentsline{{toc}}{{chapter}}{{{escaped}}}"], False
    return [latex_escape(raw)], False


def polish_content_legacy(
    content: str,
    project_dir: Path,
    print_layout: str = "classroom",
    answer_density: str = "conservative",
    test_navigation_context: bool = False,
) -> tuple[str, list[str], list[str], list[dict]]:
    changes: list[str] = []
    warnings: list[str] = []
    image_ledger: list[dict] = []
    image_prune_report: dict = {}
    lines = content.splitlines()
    out: list[str] = []
    in_bullet_group = False
    seen_unit_chapter = False
    repeated_icon_context = has_repeated_decorative_icon_context(content, project_dir)
    i = 0

    def close_bullets() -> None:
        nonlocal in_bullet_group
        if in_bullet_group:
            out.append(r"\end{itemize}")
            in_bullet_group = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        source_comment = stripped.startswith("%") and "source_page_idx" in stripped
        if source_comment:
            close_bullets()
            changes.append("removed_source_page_comment")
            i += 1
            continue

        if is_running_header_noise(stripped):
            close_bullets()
            changes.append("removed_running_header_noise")
            i += 1
            continue

        unwrapped_math_list = unwrap_textual_inline_math_list(stripped)
        if unwrapped_math_list:
            close_bullets()
            out.append(unwrapped_math_list)
            changes.append("unwrapped_textual_inline_math_list")
            i += 1
            continue

        chapter_noise = re.match(r"\\(?:chapter|section)(?:\[[^\]]*\])?\{(.+)\}\s*$", stripped)
        if chapter_noise and is_running_header_noise(chapter_noise.group(1)):
            close_bullets()
            changes.append("removed_running_header_heading")
            i += 1
            continue

        if stripped.startswith(r"\begin{figure}"):
            close_bullets()
            block = [line]
            j = i + 1
            while j < len(lines):
                block.append(lines[j])
                if lines[j].strip() == r"\end{figure}":
                    break
                j += 1
            before = nearby_nonempty(out, max(0, len(out) - 8), len(out))
            after = nearby_nonempty(lines, j + 1, j + 10)
            rewritten, reason, figure_record = rewrite_figure_block(
                block,
                project_dir,
                before,
                after,
                test_navigation_context,
                repeated_icon_context,
            )
            figure_record["source_line"] = i + 1
            image_ledger.append(figure_record)
            if rewritten:
                out.extend(rewritten)
                if reason == "removed_generic_caption":
                    changes.append("removed_generic_image_caption")
            else:
                changes.append(f"dropped_figure_{reason}")
            i = j + 1
            continue

        if stripped.startswith(">"):
            close_bullets()
            next_title = None
            is_unit_heading = bool(re.match(r"^>\s*Unit\s+\d+", stripped, re.I))
            if re.match(r"^>\s*Part\s+\d+\s*$", stripped, re.I):
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines):
                    candidate = lines[j].strip()
                    if candidate and not candidate.startswith("\\") and not candidate.startswith(">") and len(candidate) <= 80:
                        next_title = candidate
            commands, consumed_next = heading_command_for_blockquote(stripped, next_title)
            if is_unit_heading:
                seen_unit_chapter = True
            out.extend(commands)
            changes.append("converted_markdown_blockquote_heading")
            i += 2 if consumed_next else 1
            continue

        semantic_command = semantic_heading_command(stripped)
        if semantic_command:
            close_bullets()
            out.append(semantic_command)
            changes.append("styled_semantic_heading")
            i += 1
            continue

        chapter = re.match(r"\\chapter(?:\[[^\]]*\])?\{(.+)\}\s*$", stripped)
        if chapter:
            title = chapter.group(1)
            plain = re.sub(r"\\[A-Za-z]+\{([^{}]+)\}", r"\1", title)
            if re.match(r"Unit\s+\d+\b", plain, re.I):
                seen_unit_chapter = True
            if not re.match(r"Unit\s+\d+\b|Appendix\b", plain, re.I):
                if seen_unit_chapter:
                    close_bullets()
                    out.append(rf"\section{{{title}}}")
                    changes.append("demoted_non_unit_chapter_inside_unit_flow")
                    i += 1
                    continue
                if plain.lower() in {"reading and writing", "speaking"}:
                    close_bullets()
                    out.append(rf"\section*{{{title}}}")
                    changes.append("demoted_preface_chapter_to_unnumbered_section")
                    i += 1
                    continue

        if re.match(r"\\caption\{\s*(?:image|Image)\s*\}\s*$", stripped):
            changes.append("removed_generic_image_caption")
            i += 1
            continue

        bullet = re.match(r"^(?:•|-)\s+(.+)$", stripped)
        if bullet:
            if not in_bullet_group:
                close_bullets()
                out.append(r"\begin{itemize}")
                in_bullet_group = True
                changes.append("converted_line_start_bullet_glyphs")
            parts = [part.strip() for part in re.split(r"\s+•\s+", bullet.group(1).strip()) if part.strip()]
            for part in parts:
                out.append(rf"\item {part}")
            i += 1
            continue

        if in_bullet_group and stripped:
            close_bullets()

        out.append(line)
        i += 1

    close_bullets()
    polished = "\n".join(out).strip() + "\n"
    polished, activity_changes, activity_warnings = structure_activity_blocks(
        polished,
        print_layout=print_layout,
        answer_density=answer_density,
    )
    changes.extend(activity_changes)
    warnings.extend(activity_warnings)
    if re.search(r"(?m)^>\s*", polished):
        warnings.append("markdown_blockquote_residue_remains")
    return polished, changes, warnings, image_ledger


def figure_ambiguous_review_reason(info: dict) -> str:
    caption = clean_spaces(info.get("caption", ""))
    caption_generic = not caption or caption.lower() == "image"
    width_px = int(info.get("width_px") or 99999)
    height_px = int(info.get("height_px") or 99999)
    file_size = int(info.get("file_size") or 999999)
    if caption_generic and max(width_px, height_px) <= 180 and file_size <= 9000:
        return "ambiguous_tiny_generic_figure_kept_for_review"
    return ""


def collect_answer_space_decisions(content_text: str, answer_density: str) -> list[dict]:
    decisions: list[dict] = []
    lines = content_text.splitlines()
    for index, line in enumerate(lines, 1):
        marker = next((item for item in ANSWER_SPACE_MARKERS if item in line), "")
        if not marker:
            continue
        marker_name = marker.replace("\\", "")
        previous = nearby_nonempty(lines, max(0, index - 7), index - 1)
        decisions.append(make_decision(
            None,
            "add_answer_space",
            f"{marker_name}:{answer_density}",
            {
                "line": index,
                "marker": marker,
                "nearby_prompt": visible_summary(previous[-4:]),
            },
        ))
    return decisions


def collect_activity_structure_decisions(changes: list[str]) -> list[dict]:
    decisions: list[dict] = []
    for reason, amount in sorted(Counter(changes).items()):
        if not (
            reason.startswith("structured_activity")
            or reason.startswith("structured_semantic_scope")
        ):
            continue
        decisions.append(make_decision(None, "structure", reason, {"count": amount}))
    return decisions


def collect_rendered_structure_decisions(content_text: str) -> list[dict]:
    decisions: list[dict] = []
    markers = [
        (r"\activityheading{", "style", "activity_heading_box"),
        (r"\stageheading{", "style", "stage_heading_box"),
        (r"\focusheading{", "style", "focus_heading_box"),
        (r"\readingtextheading{", "style", "reading_text_heading_box"),
        (r"\readingparagraph{", "structure", "reading_paragraph_number_label"),
        (r"\activitytask{", "structure", "table_safe_task_label"),
        (r"\begin{enumerate}[label=\arabic*.", "structure", "numbered_task_list"),
        (r"\begin{enumerate}[label=\alph*.", "structure", "lettered_subtask_list"),
        (r"\begin{description}[leftmargin=*,style=nextline]", "structure", "description_or_choice_list"),
    ]
    for line_no, line in enumerate(content_text.splitlines(), 1):
        for marker, action, reason in markers:
            if marker in line:
                decisions.append(make_decision(
                    None,
                    action,
                    reason,
                    {"line": line_no, "marker": marker, "summary": visible_summary([line])},
                ))
                break
    return decisions


def legacy_decisions_from_changes(changes: list[str]) -> list[dict]:
    decisions: list[dict] = []
    for reason, amount in sorted(Counter(changes).items()):
        action = "rewrite"
        if reason.startswith("dropped_figure"):
            action = "drop"
        elif "answer_space" in reason:
            action = "add_answer_space"
        elif reason.startswith("structured_activity"):
            action = "structure"
        elif reason.startswith("removed"):
            action = "drop_noise"
        decisions.append(make_decision(None, action, reason, {"count": amount, "engine": "legacy"}))
    return decisions


def polish_content_block_engine(
    content: str,
    project_dir: Path,
    print_layout: str = "classroom",
    answer_density: str = "conservative",
    test_navigation_context: bool = False,
) -> tuple[str, list[str], list[str], list[dict], list[dict], list[dict]]:
    changes: list[str] = []
    warnings: list[str] = []
    decisions: list[dict] = []
    image_ledger: list[dict] = []
    blocks = parse_content_blocks(content, project_dir)
    out: list[str] = []
    in_bullet_group = False
    seen_unit_chapter = False
    skip_block_ids: set[str] = set()
    reading_passage_active = False
    repeated_icon_context = has_repeated_decorative_icon_context(content, project_dir)

    def close_bullets() -> None:
        nonlocal in_bullet_group
        if in_bullet_group:
            out.append(r"\end{itemize}")
            in_bullet_group = False

    def lookahead_title(index: int) -> tuple[str | None, str]:
        j = index + 1
        while j < len(blocks):
            candidate = blocks[j]
            candidate_lines = candidate.get("lines") or []
            stripped = candidate_lines[0].strip() if candidate_lines else ""
            if not stripped:
                j += 1
                continue
            if candidate["type"] == "paragraph" and not stripped.startswith(("\\", ">")) and len(stripped) <= 80:
                return stripped, candidate["id"]
            return None, ""
        return None, ""

    def reading_metadata_after(index: int) -> tuple[str, str, list[str]]:
        title = ""
        byline = ""
        consumed_ids: list[str] = []
        j = index + 1
        inspected = 0
        while j < len(blocks) and inspected < 8:
            inspected += 1
            candidate = blocks[j]
            candidate_lines = candidate.get("lines") or []
            stripped_candidate = candidate_lines[0].strip() if candidate_lines else ""
            if is_blank_block(candidate):
                consumed_ids.append(candidate["id"])
                j += 1
                continue
            if candidate["type"] != "paragraph":
                break
            if (
                stripped_candidate.startswith(("\\", ">"))
                or reading_text_label(stripped_candidate)
                or READING_PARAGRAPH_RE.match(stripped_candidate)
                or is_digital_text_copy_prompt(stripped_candidate)
                or is_activity_boundary_line(stripped_candidate)
            ):
                break
            if not title and is_reading_byline(stripped_candidate):
                byline = normalize_visible_text(stripped_candidate)
                consumed_ids.append(candidate["id"])
                j += 1
                continue
            if not title and is_reading_metadata_line(stripped_candidate):
                title = normalize_visible_text(stripped_candidate)
                consumed_ids.append(candidate["id"])
                j += 1
                continue
            if title and not byline and is_reading_byline(stripped_candidate):
                byline = normalize_visible_text(stripped_candidate)
                consumed_ids.append(candidate["id"])
                j += 1
                continue
            break
        return title, byline, consumed_ids

    for index, block in enumerate(blocks):
        if block["id"] in skip_block_ids:
            continue
        block_lines = block.get("lines") or []
        if not block_lines:
            continue
        line = block_lines[0]
        stripped = line.strip()

        source_comment = re.match(r"%\s*\\ensuremath\{source_page_idx\}:\s*(.+)$", stripped)
        if source_comment or block.get("meta", {}).get("noise_reason") == "source_page_comment":
            close_bullets()
            changes.append("removed_source_page_comment")
            decisions.append(make_decision(block, "drop_noise", "source_page_comment", {"line": block["source_range"]}))
            continue

        if is_running_header_noise(stripped):
            close_bullets()
            changes.append("removed_running_header_noise")
            decisions.append(make_decision(block, "drop_noise", "running_header_noise", {"text": normalize_visible_text(stripped)}))
            continue

        if is_digital_text_copy_prompt(stripped):
            close_bullets()
            changes.append("removed_digital_text_copy_prompt")
            decisions.append(make_decision(
                block,
                "drop_noise",
                "digital_coursebook_text_copy_prompt",
                {"text": normalize_visible_text(stripped)},
            ))
            continue

        if block["type"] == "reading_text":
            close_bullets()
            title, byline, consumed_ids = reading_metadata_after(index)
            skip_block_ids.update(consumed_ids)
            label = block.get("meta", {}).get("text_label") or reading_text_label(stripped)
            out.append(
                rf"\readingtextheading{{{latex_escape(label)}}}{{{latex_escape(title)}}}{{{latex_escape(byline)}}}"
            )
            changes.append("styled_reading_text_heading")
            decisions.append(make_decision(
                block,
                "style",
                "reading_text_heading_boxed",
                {"label": label, "title": title, "byline": byline, "consumed_block_ids": consumed_ids},
            ))
            reading_passage_active = True
            continue

        if reading_passage_active:
            reading_parts = split_reading_paragraph_markers(stripped)
            if reading_parts:
                close_bullets()
                for paragraph_number, paragraph_text in reading_parts:
                    out.append(rf"\readingparagraph{{{paragraph_number}}} {paragraph_text}")
                    changes.append("structured_reading_paragraph_label")
                    decisions.append(make_decision(
                        block,
                        "structure",
                        "reading_paragraph_number_label",
                        {"paragraph_number": paragraph_number},
                    ))
                continue
            if (
                block["type"] in {"activity", "stage", "task", "chapter", "semantic_heading"}
                or stripped.startswith(r"\activitytask")
                or ACTIVITY_STAGE_RE.match(stripped)
                or is_activity_boundary_line(stripped)
            ):
                reading_passage_active = False

        unwrapped_math_list = unwrap_textual_inline_math_list(stripped)
        if unwrapped_math_list:
            close_bullets()
            out.append(unwrapped_math_list)
            changes.append("unwrapped_textual_inline_math_list")
            decisions.append(make_decision(block, "rewrite", "natural_language_line_unwrapped_from_math"))
            continue

        chapter_noise = re.match(r"\\(?:chapter|section)(?:\[[^\]]*\])?\{(.+)\}\s*$", stripped)
        if chapter_noise and is_running_header_noise(chapter_noise.group(1)):
            close_bullets()
            changes.append("removed_running_header_heading")
            decisions.append(make_decision(block, "drop_noise", "running_header_heading"))
            continue

        if block["type"] == "figure":
            close_bullets()
            before = nearby_nonempty(out, max(0, len(out) - 8), len(out))
            after_lines: list[str] = []
            for future in blocks[index + 1:index + 10]:
                after_lines.extend(future.get("lines") or [])
            after = [item.strip() for item in after_lines if item.strip()][:8]
            rewritten, reason, figure_record = rewrite_figure_block(
                block_lines,
                project_dir,
                before,
                after,
                test_navigation_context,
                repeated_icon_context,
            )
            figure_record["source_line"] = block["source_range"].get("start")
            figure_record["block_id"] = block["id"]
            image_ledger.append(figure_record)
            if rewritten:
                out.extend(rewritten)
                if reason == "removed_generic_caption":
                    changes.append("removed_generic_image_caption")
                    decisions.append(make_decision(block, "rewrite", "removed_generic_image_caption", figure_record))
                review_reason = figure_ambiguous_review_reason(figure_record)
                if review_reason:
                    warnings.append(review_reason)
                    decisions.append(make_decision(block, "needs_review", review_reason, figure_record))
                else:
                    decisions.append(make_decision(block, "keep", figure_record.get("reason", "figure_kept"), figure_record))
            else:
                changes.append(f"dropped_figure_{reason}")
                decisions.append(make_decision(block, "drop", reason, figure_record))
            continue

        if stripped.startswith(">"):
            close_bullets()
            next_title = None
            skip_id = ""
            is_part_heading = bool(re.match(r"^>\s*Part\s+\d+\s*$", stripped, re.I))
            is_unit_heading = bool(re.match(r"^>\s*Unit\s+\d+", stripped, re.I))
            if is_part_heading:
                next_title, skip_id = lookahead_title(index)
            commands, consumed_next = heading_command_for_blockquote(stripped, next_title)
            if consumed_next and skip_id:
                skip_block_ids.add(skip_id)
            if is_unit_heading:
                seen_unit_chapter = True
            out.extend(commands)
            changes.append("converted_markdown_blockquote_heading")
            decisions.append(make_decision(
                block,
                "style",
                "visible_blockquote_heading_promoted",
                {"next_title_consumed": bool(consumed_next), "commands": commands},
            ))
            continue

        semantic_command = semantic_heading_command(stripped)
        if semantic_command:
            close_bullets()
            out.append(semantic_command)
            changes.append("styled_semantic_heading")
            decisions.append(make_decision(block, "style", "semantic_heading_boxed", {"command": semantic_command}))
            continue

        chapter = re.match(r"\\chapter(?:\[[^\]]*\])?\{(.+)\}\s*$", stripped)
        if chapter:
            title = chapter.group(1)
            plain = re.sub(r"\\[A-Za-z]+\{([^{}]+)\}", r"\1", title)
            if re.match(r"Unit\s+\d+\b", plain, re.I):
                seen_unit_chapter = True
            if not re.match(r"Unit\s+\d+\b|Appendix\b", plain, re.I):
                if seen_unit_chapter:
                    close_bullets()
                    out.append(rf"\section{{{title}}}")
                    changes.append("demoted_non_unit_chapter_inside_unit_flow")
                    decisions.append(make_decision(block, "rewrite", "demoted_non_unit_chapter_inside_unit_flow"))
                    continue
                if plain.lower() in {"reading and writing", "speaking"}:
                    close_bullets()
                    out.append(rf"\section*{{{title}}}")
                    changes.append("demoted_preface_chapter_to_unnumbered_section")
                    decisions.append(make_decision(block, "rewrite", "demoted_preface_chapter_to_unnumbered_section"))
                    continue

        if re.match(r"\\caption\{\s*(?:image|Image)\s*\}\s*$", stripped):
            changes.append("removed_generic_image_caption")
            decisions.append(make_decision(block, "drop_noise", "standalone_generic_image_caption"))
            continue

        bullet = re.match(r"^(?:•|-)\s+(.+)$", stripped)
        if bullet:
            if not in_bullet_group:
                close_bullets()
                out.append(r"\begin{itemize}")
                in_bullet_group = True
                changes.append("converted_line_start_bullet_glyphs")
                decisions.append(make_decision(block, "structure", "line_start_bullet_glyphs_to_itemize"))
            parts = [part.strip() for part in re.split(r"\s+•\s+", bullet.group(1).strip()) if part.strip()]
            for part in parts:
                out.append(rf"\item {part}")
            continue

        if in_bullet_group and stripped:
            close_bullets()
        out.extend(block_lines)

    close_bullets()
    base_polished = "\n".join(out).strip() + "\n"
    polished, activity_changes, activity_warnings = structure_activity_blocks(
        base_polished,
        print_layout=print_layout,
        answer_density=answer_density,
    )
    changes.extend(activity_changes)
    warnings.extend(activity_warnings)
    decisions.extend(collect_activity_structure_decisions(activity_changes))
    decisions.extend(collect_rendered_structure_decisions(polished))
    decisions.extend(collect_answer_space_decisions(polished, answer_density))
    if re.search(r"(?m)^>\s*", polished):
        warnings.append("markdown_blockquote_residue_remains")
        decisions.append(make_decision(None, "needs_review", "markdown_blockquote_residue_remains"))
    return polished, changes, warnings, image_ledger, blocks, decisions


def polish_content(
    content: str,
    project_dir: Path,
    print_layout: str = "classroom",
    answer_density: str = "conservative",
    engine: str = "block",
) -> tuple[str, list[str], list[str], list[dict], dict, list[dict]]:
    source_blocks = parse_content_blocks(content, project_dir)
    test_navigation_context = has_repeated_test_navigation_context(content)
    cleaned_content, dropped_editorial_images = INCOMPLETE_EDITORIAL_MARKDOWN_IMAGE_RE.subn("", content)
    cleaned_content, metadata_changes, metadata_decisions = clean_visible_literal_metadata(cleaned_content)
    if dropped_editorial_images:
        metadata_changes.insert(0, "removed_incomplete_editorial_markdown_image")
        metadata_decisions.insert(
            0,
            make_decision(None, "drop_noise", "removed_incomplete_editorial_markdown_image", {"count": dropped_editorial_images}),
        )
    if engine == "legacy":
        polished, changes, warnings, image_ledger = polish_content_legacy(
            cleaned_content,
            project_dir,
            print_layout=print_layout,
            answer_density=answer_density,
            test_navigation_context=test_navigation_context,
        )
        decisions = legacy_decisions_from_changes(changes)
    else:
        polished, changes, warnings, image_ledger, _cleaned_blocks, decisions = polish_content_block_engine(
            cleaned_content,
            project_dir,
            print_layout=print_layout,
            answer_density=answer_density,
            test_navigation_context=test_navigation_context,
        )
    changes = metadata_changes + changes
    decisions = metadata_decisions + decisions
    polished, heading_changes, heading_decisions = normalize_source_labeled_headings(polished)
    changes.extend(heading_changes)
    decisions.extend(heading_decisions)
    if print_layout == "classroom":
        polished, global_answer_changes, global_answer_decisions = add_global_answer_spaces(polished, answer_density)
        changes.extend(global_answer_changes)
        decisions.extend(global_answer_decisions)
        polished, personal_goal_changes, personal_goal_decisions = add_personal_goal_answer_spaces(polished)
        changes.extend(personal_goal_changes)
        decisions.extend(personal_goal_decisions)
        polished, prune_answer_changes, prune_answer_decisions = prune_misaligned_answer_spaces(polished)
        changes.extend(prune_answer_changes)
        decisions.extend(prune_answer_decisions)
        polished, speaking_card_changes, speaking_card_decisions = add_speaking_card_page_guards(polished)
        changes.extend(speaking_card_changes)
        decisions.extend(speaking_card_decisions)
    polished, table_safety_changes, table_safety_decisions = apply_table_layout_safety(polished)
    changes.extend(table_safety_changes)
    decisions.extend(table_safety_decisions)
    polished, chapter_writing_changes, chapter_writing_decisions = promote_chapter_end_writing_boxes(polished)
    changes.extend(chapter_writing_changes)
    decisions.extend(chapter_writing_decisions)
    polished, par_spacing_changes, par_spacing_decisions = separate_stuck_par_commands(polished)
    changes.extend(par_spacing_changes)
    decisions.extend(par_spacing_decisions)
    polished, inline_math_changes, inline_math_decisions = soften_inline_ensuremath_prose_fragments(polished)
    changes.extend(inline_math_changes)
    decisions.extend(inline_math_decisions)
    polished, ensuremath_changes, ensuremath_decisions = soften_long_ensuremath_lines(polished)
    changes.extend(ensuremath_changes)
    decisions.extend(ensuremath_decisions)
    rendered_blocks = parse_content_blocks(polished, project_dir)
    model = block_model_document(source_blocks, rendered_blocks, engine, answer_density)
    return polished, changes, warnings, image_ledger, model, decisions


def polish_main(main_text: str, content_text: str, overrides: dict, print_layout: str = "classroom") -> tuple[str, list[str]]:
    changes: list[str] = []
    main_text, restored_page_flushes = UNSAFE_PAGE_FLUSH_RE.subn("", main_text)
    if restored_page_flushes:
        changes.append("restored_standard_page_flush_commands")
    raw_title = overrides.get("title") or extract_latex_macro(main_text, "title")
    inferred = infer_metadata(raw_title)
    metadata: dict[str, str] = {}
    for key in ("title", "subtitle", "author"):
        value = overrides.get(key)
        if not value:
            value = inferred.get(key, "")
        metadata[key] = value
        main_text, changed = replace_latex_macro(main_text, key, value)
        if changed:
            changes.append(f"updated_{key}")
    lang = language_from_content(main_text, content_text)
    main_text, changed = set_document_language(main_text, lang)
    if changed:
        changes.append(f"set_document_language_{lang}")
    main_text, changed = set_toc_depth(main_text, 2)
    if changed:
        changes.append("set_tocdepth_2")
    date_override = overrides.get("date")
    current_date = extract_latex_macro(main_text, "date")
    if date_override is not None:
        main_text, changed = replace_latex_macro(main_text, "date", date_override)
        if changed:
            changes.append("updated_date")
    elif lang == "en" and current_date == r"\today":
        main_text, changed = replace_latex_macro(main_text, "date", "")
        if changed:
            changes.append("cleared_english_today_date")
    main_text, changed = ensure_preamble_looseners(main_text, print_layout=print_layout)
    if changed:
        changes.append("added_layout_safety_preamble")
    main_text, changed = ensure_editorial_commands(main_text)
    if changed:
        changes.append("added_editorial_heading_commands")
    cover_date = extract_latex_macro(main_text, "date")
    if cover_date.startswith("\\"):
        cover_date = ""
    version = extract_latex_macro(main_text, "version")
    main_text, changed = sync_plain_cover_override(
        main_text,
        metadata.get("title", ""),
        metadata.get("subtitle", ""),
        metadata.get("author", ""),
        cover_date,
        version,
    )
    if changed:
        changes.append("synced_plain_cover_override")
    return main_text, changes


def prepare_project(args: argparse.Namespace) -> Path:
    out_dir = args.out_dir
    project_out = out_dir / "project"
    if out_dir.exists():
        if not args.force:
            raise SystemExit(f"Output exists; pass --force to replace: {out_dir}")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.project_dir:
        shutil.copytree(args.project_dir, project_out)
    elif args.zip:
        with zipfile.ZipFile(args.zip) as archive:
            archive.extractall(project_out)
    else:
        raise SystemExit("Pass --project-dir or --zip")
    return project_out


def project_tree_hash(project_dir: Path) -> str:
    """Hash file names and bytes to prove that audit did not mutate its copy."""
    digest = hashlib.sha256()
    for path in sorted(item for item in project_dir.rglob("*") if item.is_file()):
        digest.update(path.relative_to(project_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def compile_in_sharelatex(project_dir: Path, compile_dir: Path) -> dict:
    compile_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", project_dir.name + "-" + str(int(time.time())))
    container_dir = f"/tmp/refine-elegantbook-latex-{safe}"
    report = {
        "container": "sharelatex",
        "container_dir": container_dir,
        "success": False,
        "copied_outputs": [],
    }
    availability = run(["docker", "exec", "sharelatex", "bash", "-lc", "command -v latexmk >/dev/null && command -v xelatex >/dev/null"], timeout=20)
    report["available"] = availability["returncode"] == 0
    report["availability"] = availability
    if not report["available"]:
        return report
    steps = {
        "prep": run(["docker", "exec", "sharelatex", "bash", "-lc", f"rm -rf {container_dir} && mkdir -p {container_dir}"], timeout=120),
        "copy_in": run(["docker", "cp", f"{project_dir}/.", f"sharelatex:{container_dir}/"], timeout=600),
        "latexmk": run([
            "docker",
            "exec",
            "sharelatex",
            "bash",
            "-lc",
            f"cd {container_dir} && latexmk -xelatex -halt-on-error -interaction=nonstopmode -file-line-error main.tex",
        ], timeout=1800),
    }
    report["steps"] = {key: value["returncode"] for key, value in steps.items()}
    (compile_dir / "overleaf_compile_latexmk.json").write_text(
        json.dumps(steps["latexmk"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for name in ["main.pdf", "main.log", "main.toc", "main.aux", "main.fls", "main.fdb_latexmk"]:
        target = compile_dir / name
        copy = run(["docker", "cp", f"sharelatex:{container_dir}/{name}", str(target)], timeout=120)
        if copy["returncode"] == 0 and target.exists():
            report["copied_outputs"].append(str(target))
            if name in {"main.pdf", "main.log"}:
                shutil.copy2(target, project_dir / name)
    cleanup = run(["docker", "exec", "sharelatex", "bash", "-lc", f"rm -rf {container_dir}"], timeout=120)
    report["cleanup_returncode"] = cleanup["returncode"]
    report["success"] = steps["latexmk"]["returncode"] == 0 and (compile_dir / "main.pdf").exists()
    return report


def parse_pdfinfo(stdout: str) -> dict:
    out = {}
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip()] = value.strip()
    try:
        pages = int(out.get("Pages", "0"))
    except ValueError:
        pages = 0
    return {"raw": out, "pages": pages}


def image_contact_sheet(image_paths: list[Path], out_path: Path) -> bool:
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return False
    if not image_paths:
        return False
    thumbs = []
    for path in image_paths:
        image = Image.open(path).convert("RGB")
        image.thumbnail((260, 370))
        canvas = Image.new("RGB", (280, 410), "white")
        canvas.paste(image, ((280 - image.width) // 2, 28))
        draw = ImageDraw.Draw(canvas)
        draw.text((12, 8), path.stem, fill=(0, 0, 0))
        thumbs.append(canvas)
    columns = min(3, len(thumbs))
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * 280, rows * 410), "white")
    for index, thumb in enumerate(thumbs):
        sheet.paste(thumb, ((index % columns) * 280, (index // columns) * 410))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return True


def render_review(pdf_path: Path, render_dir: Path) -> dict:
    render_dir.mkdir(parents=True, exist_ok=True)
    info_log = run(["pdfinfo", str(pdf_path)], timeout=120)
    info = parse_pdfinfo(info_log["stdout"]) if info_log["returncode"] == 0 else {"pages": 0, "raw": {}}
    pages = info.get("pages", 0)
    sample_pages = sorted({page for page in [1, 2, 3, max(1, pages // 2), pages] if 1 <= page <= pages})
    rendered = []
    for page in sample_pages:
        prefix = render_dir / f"page-{page:04d}"
        result = run([
            "pdftoppm",
            "-r",
            str(DEFAULT_RENDER_DPI),
            "-f",
            str(page),
            "-l",
            str(page),
            "-png",
            str(pdf_path),
            str(prefix),
        ], timeout=180)
        candidates = sorted(render_dir.glob(f"page-{page:04d}-*.png"))
        if result["returncode"] == 0 and candidates:
            normalized = render_dir / f"page-{page:04d}.png"
            if normalized.exists():
                normalized.unlink()
            candidates[0].rename(normalized)
            rendered.append(normalized)
    contact_sheet = render_dir / "contact_sheet.png"
    has_contact = image_contact_sheet(rendered, contact_sheet)
    return {
        "pdf": str(pdf_path),
        "pdfinfo": info,
        "sample_pages": sample_pages,
        "rendered_pages": [str(path) for path in rendered],
        "contact_sheet": str(contact_sheet) if has_contact else "",
        "success": bool(rendered) and len(rendered) == len(sample_pages),
    }


def write_zip(project_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    excluded_names = {"main.pdf", "main.log", "main.aux", "main.toc", "main.fls", "main.fdb_latexmk"}
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(project_dir.rglob("*")):
            if path.is_file():
                if path.name in excluded_names:
                    continue
                archive.write(path, path.relative_to(project_dir))


def prune_unreferenced_images(project_dir: Path) -> dict:
    tex_text = "\n".join(read_text(path) for path in project_dir.rglob("*.tex") if path.is_file())
    refs = {
        ref.replace("\\", "/").lstrip("./")
        for ref in re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", tex_text)
    }
    image_dir = project_dir / "images"
    removed = []
    kept = []
    if not image_dir.exists():
        return {"removed_count": 0, "removed_bytes": 0, "kept_count": 0, "kept_bytes": 0, "removed": []}
    for path in sorted(image_dir.rglob("*")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(project_dir).as_posix()
        size = path.stat().st_size
        if rel_path in refs:
            kept.append((rel_path, size))
            continue
        removed.append((rel_path, size))
        path.unlink()
    return {
        "removed_count": len(removed),
        "removed_bytes": sum(size for _, size in removed),
        "kept_count": len(kept),
        "kept_bytes": sum(size for _, size in kept),
        "removed": [path for path, _ in removed[:100]],
    }


def markdown_report(report: dict) -> str:
    before = report.get("before", {})
    after = report.get("after", {})
    decision_counts = report.get("editorial_decision_counts") or {}
    block_counts = report.get("block_model_counts") or {}
    lines = [
        "# ElegantBook LaTeX Polish Report",
        "",
        f"- Mode: `{report.get('mode')}`",
        f"- Engine: `{report.get('engine', 'block')}`",
        f"- Print layout: `{report.get('print_layout', 'classroom')}`",
        f"- Answer density: `{report.get('answer_density', 'conservative')}`",
        f"- Input: `{report.get('input')}`",
        f"- Project copy: `{report.get('project_dir')}`",
        f"- Block model: `{report.get('block_model_path', '')}`",
        f"- Editorial decisions: `{report.get('editorial_decisions_path', '')}`",
        f"- Compile success: `{report.get('compile', {}).get('success', 'not_run')}`",
        f"- Render success: `{report.get('render', {}).get('success', 'not_run')}`",
        "",
        "## Key Metrics",
        "",
        "| Metric | Before | After |",
        "|---|---:|---:|",
    ]
    for key in [
        "zlib_title_noise",
        "mechanical_title_noise",
        "blockquote_lines",
        "unit_blockquote_lines",
        "part_blockquote_lines",
        "appendix_blockquote_lines",
        "chapters",
        "sections",
        "star_chapters",
        "star_sections",
        "subsections",
        "star_subsections",
        "source_numbered_section_titles",
        "source_numbered_star_section_titles",
        "source_structural_section_titles",
        "source_structural_star_section_titles",
        "source_structural_chapter_titles",
        "generic_image_captions",
        "figures",
        "tiny_or_icon_like_figures",
        "running_header_noise_lines",
        "semantic_heading_candidates",
        "semantic_heading_boxes",
        "activity_plain_numbered_task_lines",
        "activity_plain_lettered_subtask_lines",
        "activity_numbered_lists",
        "activity_paragraph_tasks",
        "activity_lettered_lists",
        "activity_description_lists",
        "reading_text_headings",
        "reading_paragraph_labels",
        "speaking_card_headings",
        "plain_speaking_card_lines",
        "plain_reading_text_labels",
        "digital_text_copy_prompts",
        "print_answer_space_blocks",
        "print_short_answers",
        "print_medium_answers",
        "print_long_answers",
        "print_list_answers",
        "print_writing_boxes",
        "line_start_bullet_glyphs",
        "textual_inline_math_list_lines",
        "inline_ensuremath_prose_fragments",
        "long_unbreakable_ensuremath_lines",
        "visible_literal_figure_metadata",
        "visible_broken_figure_metadata",
        "visible_generic_image_caption_residue",
        "editorial_markdown_image_residue",
        "unsafe_page_flush_suppression",
        "visible_literal_image_reference_metadata",
        "visible_literal_source_metadata",
        "visible_source_table_evidence",
        "external_resource_noise_lines",
        "visible_html_comments",
        "test_navigation_noise_lines",
        "test_navigation_arrow_figures",
        "qr_code_figures",
        "repeated_decorative_icon_figures",
        "overfull_count",
        "overfull_gt_50",
        "overfull_gt_100",
        "overfull_max_pt",
        "missing_character_count",
    ]:
        lines.append(f"| `{key}` | `{before.get(key, '')}` | `{after.get(key, '')}` |")
    lines.extend(["", "## Changes", ""])
    changes = report.get("changes") or []
    if changes:
        for item, count in sorted(Counter(changes).items()):
            lines.append(f"- `{item}`: `{count}`")
    else:
        lines.append("- none")
    warnings = report.get("warnings") or []
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- `{item}`" for item in warnings) if warnings else lines.append("- none")
    image_reasons = report.get("image_decision_reasons") or {}
    if image_reasons:
        lines.extend(["", "## Image Decisions", ""])
        for item, count in sorted(image_reasons.items()):
            lines.append(f"- `{item}`: `{count}`")
    prune = report.get("image_prune") or {}
    if prune:
        lines.extend(["", "## Image File Prune", ""])
        lines.append(f"- Removed files: `{prune.get('removed_count', 0)}`")
        lines.append(f"- Kept files: `{prune.get('kept_count', 0)}`")
        lines.append(f"- Removed bytes: `{prune.get('removed_bytes', 0)}`")
    if decision_counts:
        lines.extend(["", "## Editorial Decision Counts", ""])
        for item, count_value in sorted(decision_counts.items()):
            lines.append(f"- `{item}`: `{count_value}`")
    if block_counts:
        lines.extend(["", "## Block Model Counts", ""])
        for item, count_value in sorted(block_counts.items()):
            lines.append(f"- `{item}`: `{count_value}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only diagnostic audit of an ElegantBook LaTeX project copy.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--project-dir", type=Path)
    source.add_argument("--zip", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=["audit"],
        default="audit",
        help="Only audit is supported. Content or template mutation belongs to the owning upstream stage.",
    )
    parser.add_argument("--title", default="")
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--date", default=None)
    parser.add_argument("--print-layout", choices=["classroom", "none"], default="classroom")
    parser.add_argument("--engine", choices=["block", "legacy"], default="block")
    parser.add_argument("--answer-density", choices=["conservative", "workbook"], default="conservative")
    parser.add_argument("--compile", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    project_dir = prepare_project(args)
    audit_input_tree_hash = project_tree_hash(project_dir)
    before = audit_project(project_dir)
    changes: list[str] = []
    warnings: list[str] = []
    image_ledger: list[dict] = []
    image_prune_report: dict = {}
    content_path = project_dir / "chapters" / "content.tex"
    initial_content = read_text(content_path) if content_path.exists() else ""
    initial_blocks = parse_content_blocks(initial_content, project_dir) if initial_content else []
    block_model = block_model_document(initial_blocks, initial_blocks, args.engine, args.answer_density)
    editorial_decisions: list[dict] = []

    if args.mode == "polish":
        main_path = project_dir / "main.tex"
        main_text = read_text(main_path)
        content_text = read_text(content_path)
        content_text, content_changes, content_warnings, image_ledger, block_model, editorial_decisions = polish_content(
            content_text,
            project_dir,
            print_layout=args.print_layout,
            answer_density=args.answer_density,
            engine=args.engine,
        )
        main_text, main_changes = polish_main(
            main_text,
            content_text,
            {"title": args.title, "subtitle": args.subtitle, "author": args.author, "date": args.date},
            print_layout=args.print_layout,
        )
        write_text(content_path, content_text)
        write_text(main_path, main_text)
        changes.extend(main_changes)
        changes.extend(content_changes)
        warnings.extend(content_warnings)
        image_prune_report = prune_unreferenced_images(project_dir)
        if image_prune_report.get("removed_count"):
            changes.append("pruned_unreferenced_image_files")
        write_zip(project_dir, args.out_dir / "refined-overleaf.zip")
    elif args.engine == "block":
        editorial_decisions.append(make_decision(None, "audit", "block_model_generated_without_polish"))

    compile_report = {}
    if args.compile:
        compile_report = compile_in_sharelatex(project_dir, args.out_dir / "compile")
    render_report = {}
    if args.render:
        pdf_path = args.out_dir / "compile" / "main.pdf"
        if not pdf_path.exists():
            pdf_path = project_dir / "main.pdf"
        if pdf_path.exists():
            render_report = render_review(pdf_path, args.out_dir / "rendered")
        else:
            render_report = {"success": False, "reason": "no_pdf_available"}

    after = audit_project(project_dir)
    audit_output_tree_hash = project_tree_hash(project_dir)
    if args.mode == "audit" and audit_input_tree_hash != audit_output_tree_hash:
        raise SystemExit("audit-only invariant failed: copied project bytes changed")
    decisions_doc = editorial_decisions_document(editorial_decisions, args.engine, args.answer_density)
    block_model_path = args.out_dir / "block_model.json"
    editorial_decisions_path = args.out_dir / "editorial_decisions.json"
    write_text(block_model_path, json.dumps(block_model, ensure_ascii=False, indent=2) + "\n")
    write_text(editorial_decisions_path, json.dumps(decisions_doc, ensure_ascii=False, indent=2) + "\n")
    report = {
        "schema": SKILL_SCHEMA,
        "mode": args.mode,
        "engine": args.engine,
        "print_layout": args.print_layout,
        "answer_density": args.answer_density,
        "input": str(args.project_dir or args.zip),
        "out_dir": str(args.out_dir),
        "project_dir": str(project_dir),
        "block_model_path": str(block_model_path),
        "editorial_decisions_path": str(editorial_decisions_path),
        "block_model_counts": block_model.get("after_counts", {}),
        "editorial_decision_counts": decisions_doc.get("decision_counts", {}),
        "before": before,
        "after": after,
        "changes": changes,
        "warnings": warnings,
        "image_ledger": image_ledger,
        "image_decisions": dict(Counter(item.get("decision", "unknown") for item in image_ledger)),
        "image_decision_reasons": dict(Counter(item.get("reason", "unknown") for item in image_ledger)),
        "image_prune": image_prune_report,
        "compile": compile_report,
        "render": render_report,
        "read_only_guarantee": {
            "input_tree_sha256": audit_input_tree_hash,
            "output_tree_sha256": audit_output_tree_hash,
            "input_unchanged": audit_input_tree_hash == audit_output_tree_hash,
            "replacement_zip_created": False,
        },
    }
    write_text(args.out_dir / "latex_polish_report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    write_text(args.out_dir / "latex_polish_report.md", markdown_report(report))
    print(json.dumps({
        "report": str(args.out_dir / "latex_polish_report.json"),
        "project_dir": str(project_dir),
        "engine": args.engine,
        "answer_density": args.answer_density,
        "block_model": str(block_model_path),
        "editorial_decisions": str(editorial_decisions_path),
        "compile_success": compile_report.get("success") if compile_report else None,
        "render_success": render_report.get("success") if render_report else None,
        "before": before,
        "after": after,
    }, ensure_ascii=False, indent=2))
    if args.compile and not compile_report.get("success"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
