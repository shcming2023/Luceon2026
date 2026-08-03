from __future__ import annotations

import re


MISSING_CHARACTER_RE = re.compile(
    r'^Missing character: There is no (?P<char>.*?) \((?:U\+|")(?P<codepoint>[0-9A-Fa-f]+)\) in font (?P<font>.+?)(?:!)?$',
    re.MULTILINE,
)
OVERFULL_HBOX_RE = re.compile(
    r"^Overfull \\hbox \((?P<width>[0-9.]+)pt too wide\) in paragraph at lines (?P<start>\d+)--(?P<end>\d+)",
    re.MULTILINE,
)
UNRESOLVED_RE = re.compile(
    r"undefined references|Reference .* undefined|File `[^']+' not found",
    re.IGNORECASE,
)
PDF_PAGE_TOKEN_RE = re.compile(r"\[(?P<page>\d+)\]")


def _page_hint(log: str, offset: int) -> int | None:
    page = None
    for match in PDF_PAGE_TOKEN_RE.finditer(log, 0, offset):
        page = int(match.group("page"))
    return page


def parse_latex_diagnostics(log: str, *, obvious_overflow_pt: float = 10.0) -> dict:
    missing = []
    seen_missing = set()
    missing_occurrence_count = 0
    for match in MISSING_CHARACTER_RE.finditer(log):
        missing_occurrence_count += 1
        row = {
            "character": match.group("char"),
            "codepoint": f"U+{match.group('codepoint').upper()}",
            "font": match.group("font").strip(),
            "pdf_page_hint": _page_hint(log, match.start()),
        }
        key = (row["character"], row["codepoint"], row["font"])
        if key in seen_missing:
            continue
        seen_missing.add(key)
        missing.append(row)
    overfull = [
        {
            "width_pt": float(match.group("width")),
            "line_start": int(match.group("start")),
            "line_end": int(match.group("end")),
            "pdf_page_hint": _page_hint(log, match.start()),
        }
        for match in OVERFULL_HBOX_RE.finditer(log)
    ]
    obvious = [row for row in overfull if row["width_pt"] >= obvious_overflow_pt]
    return {
        "schema": "luceon.latex-diagnostics/v1",
        "obvious_overflow_threshold_pt": obvious_overflow_pt,
        "missing_character_count": len(missing),
        "missing_character_occurrence_count": missing_occurrence_count,
        "missing_characters": missing,
        "overfull_hbox_count": len(overfull),
        "overfull_hboxes": overfull,
        "obvious_overflow_count": len(obvious),
        "max_overfull_pt": max((row["width_pt"] for row in overfull), default=0.0),
        "unresolved_reference_or_resource_count": len(UNRESOLVED_RE.findall(log)),
    }
