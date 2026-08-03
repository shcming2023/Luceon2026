#!/usr/bin/env python3
import argparse
import html
import json
import re
from pathlib import Path
from html.parser import HTMLParser


FRONT_TITLE_RE = re.compile(
    r"^(contents|table of contents|目录|目次|本期导读|introduction|how to use|how this book|"
    r"how the book|structure of the book|your course|copyright|credits|acknowledg|reviewers|"
    r"advisory|图书在版编目|cip|preface|welcome)\b",
    re.I,
)
HARD_FRONT_TITLE_RE = re.compile(r"^(contents|table of contents|目录|目次|本期导读|copyright|credits|图书在版编目|cip)\b", re.I)
BACK_TITLE_RE = re.compile(
    r"^(appendix|glossary|index|answers|list of terms|grammar reference|grammar bank|"
    r"language reference|irregular verbs|答案|索引|词汇表|术语表|acknowledg\w*|credits)\b",
    re.I,
)
LOCAL_LABEL_RE = re.compile(
    r"^(key term|key terms|key words|tip|top tip|study tip|vocabulary|link|extension|quick recap|"
    r"objectives|activities|active learning|explore the skills|build the skills|develop the skills|"
    r"apply the skills|checklist for success|check your progress|worked example|exercise|question|questions|"
    r"response\s*\d+|text\s+[A-Z]\b|day\s+\d+|challenge|self-check|review and reflection|"
    r"一、|二、|三、|四、|选择题|填空题|解答题|思维与拓展)\b",
    re.I,
)
TOC_UNNUMBERED_TOPIC_PATTERN = (
    r"check(?:\s+your)?\s+progress|progress\s+check|self[-\s]?check|"
    r"(?:unit|chapter|section)?\s*review|summary|practice\s+test"
)
TOC_UNNUMBERED_TOPIC_RE = re.compile(rf"^({TOC_UNNUMBERED_TOPIC_PATTERN})\b", re.I)
TOC_UNNUMBERED_TOPIC_ANY_RE = re.compile(rf"\b({TOC_UNNUMBERED_TOPIC_PATTERN})\b", re.I)
TOC_TRAILING_SELF_CHECK_RE = re.compile(
    r"\b(check(?:\s+your)?\s+progress|progress\s+check|self[-\s]?check)\b",
    re.I,
)
TOC_CATEGORY_TITLES = ("Number", "Algebra", "Shape and Space", "Probability and Statistics")
TOC_CATEGORY_TITLE_KEYS = {title.lower() for title in TOC_CATEGORY_TITLES}
GENERIC_REPEATED_TOC_TOPIC_RE = re.compile(r"^(examination-style questions|review questions|unit review|chapter review|summary|practice test)\b", re.I)
INTER_UNIT_MODULE_RE = re.compile(
    r"^(review\s+\d+|(?:[A-Z][A-Za-z]+\s+)*spotlight\b|vocabulary\s+building(?:\s+\d+)?)\b",
    re.I,
)
LESSON_CODE_RE = re.compile(r"^(?:lesson\s+)?(\d{1,2})(?:(?:([A-Z])\b)|(?:[-–—.](\d{1,2})\b))(?:\s+(.+))?$", re.I)


def clean_text(value):
    value = html.unescape(str(value or ""))
    value = value.replace("<|txt_split|>", "\n")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def strip_inline_note_markers(value):
    value = str(value or "")
    value = re.sub(r"\\\(\s*\^\s*\{?\s*\d+\s*\}?\s*\\\)", " ", value)
    value = re.sub(r"\$\s*\^\s*\{?\s*\d+\s*\}?\s*\$", " ", value)
    value = re.sub(r"\^\s*\{?\s*\d+\s*\}?", " ", value)
    return value


def is_toc_category_title(title):
    return clean_text(title).lower() in TOC_CATEGORY_TITLE_KEYS


def clean_display_title(title):
    title = clean_text(strip_inline_note_markers(title))
    title = re.sub(r"^[>□■▲▶►•●\-\s]+", "", title).strip()
    return title


def lesson_code_match(title):
    return LESSON_CODE_RE.match(clean_display_title(title))


def lesson_unit_number(title):
    match = lesson_code_match(title)
    return int(match.group(1)) if match else None


def lesson_code_label(title):
    match = lesson_code_match(title)
    if not match:
        return ""
    if match.group(2):
        return f"{int(match.group(1))}{match.group(2).upper()}"
    return f"{int(match.group(1))}-{int(match.group(3))}"


def is_inter_unit_module_title(title):
    return bool(INTER_UNIT_MODULE_RE.match(clean_display_title(title)))


def normalize(value):
    value = clean_text(strip_inline_note_markers(value)).lower()
    value = re.sub(r"^exercise\s+[a-z]\d+(?:\.\d+)+\b[:：.\s-]*", "", value, flags=re.I)
    value = re.sub(r"^(chapter|unit)\s+[a-z]?\d+\b[:：.\s-]*", "", value, flags=re.I)
    value = re.sub(r"^[a-z]\d+(?:\.\d+)+\b[:：.\s-]*", "", value, flags=re.I)
    value = re.sub(r"^[a-z]\d+\b[:：.\s-]*", "", value, flags=re.I)
    value = re.sub(r"^topic\s+\d+\b[:：.\s-]*", "", value, flags=re.I)
    value = re.sub(r"^part\s+\d+\b[:：.\s-]*", "", value, flags=re.I)
    value = re.sub(r"^section\s+\d+\b[:：.\s-]*", "", value, flags=re.I)
    value = re.sub(r"^第\s*[一二三四五六七八九十百千万0-9]+\s*[章节课单元篇]\s*", "", value)
    value = re.sub(r"^\d+(?:\.\d+)+\s*", "", value)
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalized_contains(a, b, min_len=4):
    a = clean_text(a)
    b = clean_text(b)
    if len(a) < min_len or len(b) < min_len:
        return False
    return a in b or b in a


class TocTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self._row = []
        self._cell = []
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"}:
            self._cell = []
            self._in_cell = True

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self._in_cell:
            self._row.append(clean_text("".join(self._cell)))
            self._cell = []
            self._in_cell = False
        elif tag == "tr":
            if any(clean_text(cell) for cell in self._row):
                self.rows.append([clean_text(cell) for cell in self._row])


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def tree_path(root):
    for name in ("popo_document_tree.json", "popo_build_tree.json", "document_tree.json", "build_tree.json"):
        path = root / name
        if path.exists():
            return path
    return None


def node_pages(node):
    pages = []
    for item in node.get("location") or []:
        if isinstance(item, dict) and isinstance(item.get("page"), int):
            pages.append(item["page"])
    return sorted(set(pages))


def node_bbox(node):
    for item in node.get("location") or []:
        if isinstance(item, dict) and isinstance(item.get("bbox"), list):
            return item.get("bbox")
    return None


def walk_tree(node, depth=0, path=None):
    path = path or []
    for child in node.get("children") or []:
        title = clean_text(child.get("title"))
        first_child_title = ""
        for grandchild in child.get("children") or []:
            first_child_title = clean_text(grandchild.get("title"))
            if first_child_title:
                break
        current_path = path + ([title] if title else [])
        row = {
            "title": title,
            "content": clean_text(child.get("content")),
            "type": str(child.get("type") or ""),
            "level": child.get("level"),
            "depth": depth + 1,
            "path": current_path,
            "path_text": " / ".join(current_path),
            "pages": node_pages(child),
            "bbox": node_bbox(child),
            "block_ids": child.get("block_ids") or [],
            "children_count": len(child.get("children") or []),
            "first_child_title": first_child_title,
        }
        yield row
        yield from walk_tree(child, depth + 1, current_path)


def first_page(row):
    pages = row.get("pages") or []
    return min(pages) if pages else None


def row_match_title(row):
    title = clean_text(row.get("title"))
    content = clean_text(row.get("content"))
    if row.get("type") in {"header", "footer"} and content:
        return content
    return title


def split_toc_items(row):
    items = []
    values = []
    title = clean_text(row.get("title"))
    if title:
        values.append(title)
    raw_content = str(row.get("content") or "")
    if raw_content:
        values.extend(clean_text(x) for x in re.split(r"<\|txt_split\|>|\n", raw_content) if clean_text(x))
    for value in values:
        extracted = extract_inline_toc_items(value)
        if extracted:
            items.extend(extracted)
            continue
        chunks = re.split(r"\s{2,}", value)
        for chunk in chunks:
            chunk = clean_text(chunk)
            if chunk:
                items.append(chunk)
    return items


def extract_inline_toc_items(value):
    value = clean_text(value)
    start = (
        r"(?:Chapter\s+[A-Z]?\d+|Unit\s+\d+|Part\s+\d+|Section\s+\d+|[A-Z]\d+(?:\.\d+)*|第\s*[一二三四五六七八九十百千万0-9]+\s*[章节课单元篇]|"
        r"\d+(?:\.\d+)+|习题\s*\d+(?:\.\d+)?|第\s*\d+\s*章\s*复习|挑战压轴题\s*\d+|名校考题精选|各区考题精选)"
    )
    pattern = re.compile(rf"({start}.+?)(?=\s+{start}\b|$)", re.I)
    matches = [clean_text(m.group(1)) for m in pattern.finditer(value)]
    if len(matches) <= 1:
        return []
    return matches


def classify_title(title):
    title = clean_text(title)
    if not title or LOCAL_LABEL_RE.match(title):
        return None
    if re.match(r"^part\s+\d+\b", title, re.I):
        return "part"
    if re.match(r"^section\s+\d+\s*[-–—]?\s+review\b", title, re.I):
        return "unit"
    if re.match(r"^section\s+\d+\b", title, re.I):
        return "part"
    if re.match(r"^chapter\s+[A-Z]?\d+\.\s*topic\s+\d+\b", title, re.I):
        return "topic"
    if re.match(r"^topic\s+\d+\b", title, re.I):
        return "topic"
    if re.match(r"^exercise\s+[A-Z]\d+(?:\.\d+)+\b", title, re.I):
        return "topic"
    if re.match(r"^[A-Z]\d+(?:\.\d+)+\b", title, re.I):
        return "topic"
    if re.match(r"^[A-Z]\d+\b", title, re.I):
        return "chapter"
    if re.match(r"^习题\s*\d+(?:\.\d+)?\b", title) or re.match(r"^第\s*\d+\s*章\s*复习", title) or re.match(r"^挑战压轴题\s*\d+", title):
        return "topic"
    if re.match(r"^chapter\s+[A-Z]?\d+\b", title, re.I):
        return "chapter"
    if re.match(r"^unit\s+\d+\b", title, re.I):
        return "unit"
    if re.match(r"^第\s*[一二三四五六七八九十百千万0-9]+\s*[章节课单元篇]\b", title):
        return "chapter"
    if re.match(r"^\d+(?:\.\d+)+\b", title):
        return "topic"
    return None


def is_instruction_like_title(title):
    title = clean_text(title)
    if re.match(r"^\d+\s+\b(make|write|read|copy|complete|answer|discuss|look|use|identify|explain)\b", title, re.I):
        return True
    if re.match(r"^(for|to)\s+\w+", title, re.I) and title.endswith(":"):
        return True
    if len(title.split()) > 10 and not classify_title(title):
        return True
    return False


def parse_toc_entry(text, allow_bare_numbered_chapter=False):
    text = clean_text(text)
    if len(text) > 180:
        return None
    if re.search(r"\b(glossary|acknowledgements?|index|answers?)\b", text, re.I):
        return None
    printed_page = None
    bare_numbered_parent = re.fullmatch(r"(?:section|part|chapter|unit)\s+[A-Z]?\d+", text, re.I)
    m = None if bare_numbered_parent else re.search(r"(?:\.{2,}|…+|\s)(\d{1,4})(?:\s*[-–—]\s*\d{1,4})?\s*$", text)
    if m:
        candidate_text = text[: m.start(1)].rstrip(".… \t-–—")
        if not re.fullmatch(r"(?:section|part|chapter|unit)", candidate_text, re.I):
            printed_page = m.group(1)
            text = candidate_text
    section_review = re.match(r"^(Unit\s+\d+\s+.+?)\s+(\d{1,4})\s+Section\s+\d+\s+Review\b", text, re.I)
    if section_review:
        text = section_review.group(1)
        printed_page = section_review.group(2)
    kind = classify_title(text)
    if not kind and allow_bare_numbered_chapter:
        bare_chapter = re.match(r"^(\d{1,2})\s+(.+?)$", text)
        if bare_chapter and clean_text(bare_chapter.group(2)):
            text = f"Chapter {int(bare_chapter.group(1))} {clean_text(bare_chapter.group(2))}"
            kind = "chapter"
    if not kind:
        return None
    if kind == "part" and len(text) > 120:
        return None
    if kind == "unit" and len(text) > 180:
        return None
    entry = {"title": text, "kind": kind, "printed_page": printed_page, "source": "contents"}
    bare_number = re.match(r"^chapter\s+(\d{1,2})\b", text, re.I)
    prefixed_number = re.match(r"^[A-Z]\d+\s+(\d{1,2})\b", text, re.I)
    if bare_number:
        entry["chapter_number"] = bare_number.group(1)
    elif prefixed_number:
        entry["chapter_number"] = prefixed_number.group(1)
    return entry


def parse_toc_child_entry(text, parent_title=None):
    text = clean_text(text)
    parent_title = clean_text(parent_title)
    if not text or not parent_title or len(text) > 140:
        return None
    if BACK_TITLE_RE.match(text) or FRONT_TITLE_RE.match(text):
        return None
    if classify_title(text):
        return None
    match = re.search(r"(?:\.{2,}|…+|\s)(\d{1,4})(?:\s*[-–—]\s*\d{1,4})?\s*$", text)
    if not match:
        return None
    title = text[: match.start(1)].rstrip(".… \t-–—")
    title = clean_text(title)
    if not title or len(title) < 3 or re.fullmatch(r"\d{1,4}", title):
        return None
    if BACK_TITLE_RE.match(title) or FRONT_TITLE_RE.match(title):
        return None
    if is_instruction_like_title(title):
        return None
    return {
        "title": title,
        "kind": "topic",
        "printed_page": match.group(1),
        "source": "contents_detail",
        "parent_title": parent_title,
    }


def find_markdown_toc_window(text):
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if re.match(r"^\s*#*\s*(contents|table of contents|目录|目次)\s*$", clean_text(line), re.I):
            start = idx
            break
    if start is None:
        return ""
    end = min(len(lines), start + 180)
    for idx in range(start + 1, min(len(lines), start + 220)):
        line = clean_text(lines[idx])
        if idx > start + 8 and re.match(r"^#\s+(introduction|preface|chapter\s+[A-Z]?\d+|unit\s+\d+|section\s+\d+|[A-Z]\d+)\b", line, re.I):
            if re.search(r"(?:\.{2,}|…+|\s)\d{1,4}(?:\s*[-–—]\s*\d{1,4})?\s*$", line):
                continue
            lookahead = "\n".join(clean_text(item) for item in lines[idx + 1: idx + 8])
            if re.search(r"\b(?:unit|chapter|section|topic)\s+[A-Z]?\d+\b.+\b\d{1,4}\b|\b[A-Z]\d+(?:\.\d+)*\b.+\b\d{1,4}\b", lookahead, re.I):
                continue
            end = idx
            break
    return "\n".join(lines[start:end])


def parse_toc_tables(markdown):
    rows = []
    for match in re.finditer(r"<table\b.*?</table>", markdown, re.I | re.S):
        parser = TocTableParser()
        parser.feed(match.group(0))
        rows.extend(parser.rows)
    return rows


def with_adjacent_page(cells, idx):
    value = clean_text(cells[idx])
    if idx + 1 < len(cells) and re.fullmatch(r"\d{1,4}", clean_text(cells[idx + 1])):
        return f"{value} {clean_text(cells[idx + 1])}"
    return value


def split_toc_detail_topics(text):
    text = clean_text(text)
    if not text:
        return []
    text = re.sub(r"(?<=[a-z\)])(?=[A-Z][a-z])", "\n", text)
    parts = re.split(r"\s*;\s*|\n+", text)
    out = []
    seen = set()
    for part in parts:
        title = clean_text(part)
        if not title or len(title) < 3 or len(title) > 120:
            continue
        if re.fullmatch(r"\d{1,4}", title):
            continue
        if BACK_TITLE_RE.match(title) or FRONT_TITLE_RE.match(title):
            continue
        if is_instruction_like_title(title) or LOCAL_LABEL_RE.match(title):
            continue
        key = normalize(title)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(title)
    return out


def parse_numbered_unit_toc_table_row(cells):
    cleaned = [clean_text(cell) for cell in cells]
    nonempty = [cell for cell in cleaned if cell]
    if len(nonempty) < 3:
        return []
    page = nonempty[-1]
    if not re.fullmatch(r"\d{1,4}", page):
        return []
    row = nonempty[:-1]

    if len(row) >= 2 and re.fullmatch(r"\d{1,3}", row[0]):
        number = row[0]
        title = clean_text(row[1])
        if not title or re.fullmatch(r"\d{1,4}", title):
            return []
        if BACK_TITLE_RE.match(title) or FRONT_TITLE_RE.match(title):
            return []
        if is_instruction_like_title(title) or LOCAL_LABEL_RE.match(title):
            return []
        parent_title = f"{number} {title}"
        entries = [{
            "title": f"{number} {title}",
            "kind": "unit",
            "printed_page": page,
            "source": "contents",
        }]
        for topic in split_toc_detail_topics(" ".join(row[2:])):
            entries.append({
                "title": topic,
                "kind": "topic",
                "printed_page": page,
                "source": "contents_detail",
                "parent_title": parent_title,
            })
        return entries

    title = clean_text(row[0])
    if (
        title
        and not TOC_UNNUMBERED_TOPIC_RE.match(title)
        and not BACK_TITLE_RE.match(title)
        and not FRONT_TITLE_RE.match(title)
        and not is_instruction_like_title(title)
        and not LOCAL_LABEL_RE.match(title)
    ):
        entries = [{
            "title": title,
            "kind": "primary",
            "printed_page": page,
            "source": "contents",
        }]
        for topic in split_toc_detail_topics(" ".join(row[1:])):
            entries.append({
                "title": topic,
                "kind": "topic",
                "printed_page": page,
                "source": "contents_detail",
                "parent_title": title,
            })
        return entries
    return []


def parse_toc_table_row(cells, implied_blank_label=None):
    entries = []
    cells = [clean_text(cell) for cell in cells]
    nonempty = [cell for cell in cells if cell]
    if not nonempty:
        return entries

    leading_page_entries = parse_leading_page_toc_row(cells)
    if leading_page_entries:
        return leading_page_entries

    numbered_unit_entries = parse_numbered_unit_toc_table_row(cells)
    if numbered_unit_entries:
        return numbered_unit_entries

    for idx, cell in enumerate(cells):
        if not cell:
            continue
        if re.match(r"^(section|part)\s+\d+\b", cell, re.I):
            entry = parse_toc_entry(with_adjacent_page(cells, idx))
            if entry:
                entries.append(entry)
            continue
        if re.match(r"^chapter\s+[A-Z]?\d+\b", cell, re.I):
            entry = parse_toc_entry(with_adjacent_page(cells, idx))
            if entry:
                entries.append(entry)

    idx = 0
    while idx < len(cells):
        cell = clean_text(cells[idx])
        if not cell:
            idx += 1
            continue
        number = None
        title = None
        page = None
        if re.fullmatch(r"\d+(?:\.\d+)+", cell) and idx + 1 < len(cells):
            number = cell
            title = clean_text(cells[idx + 1])
            if idx + 2 < len(cells) and re.fullmatch(r"\d{1,4}", clean_text(cells[idx + 2])):
                page = clean_text(cells[idx + 2])
                idx += 3
            else:
                idx += 2
        else:
            match = re.match(r"^(\d+(?:\.\d+)+)\s+(.+)$", cell)
            if match:
                number = match.group(1)
                title = clean_text(match.group(2))
                if idx + 1 < len(cells) and re.fullmatch(r"\d{1,4}", clean_text(cells[idx + 1])):
                    page = clean_text(cells[idx + 1])
                    idx += 2
                else:
                    idx += 1
            else:
                idx += 1
        if not number or not title:
            continue
        title, trailing = split_trailing_toc_label(title)
        if not title or re.fullmatch(r"\d{1,4}", title):
            continue
        if BACK_TITLE_RE.match(title) or FRONT_TITLE_RE.match(title):
            continue
        entry = {
            "title": f"{number} {title}",
            "kind": "topic",
            "printed_page": page,
            "source": "contents_detail",
        }
        entries.append(entry)
        if trailing:
            entries.append({
                "title": trailing,
                "kind": "topic",
                "printed_page": None,
                "source": "contents_detail",
                "toc_unnumbered": True,
            })

    for idx, cell in enumerate(cells):
        title = clean_text(cell)
        if not TOC_UNNUMBERED_TOPIC_RE.match(title):
            continue
        page = None
        if idx + 1 < len(cells) and re.fullmatch(r"\d{1,4}", clean_text(cells[idx + 1])):
            page = clean_text(cells[idx + 1])
        entries.append({
            "title": title,
            "kind": "topic",
            "printed_page": page,
            "source": "contents_detail",
            "toc_unnumbered": True,
        })
    for idx, cell in enumerate(cells[:-1]):
        if clean_text(cell):
            continue
        next_cell = clean_text(cells[idx + 1])
        if not re.fullmatch(r"\d{1,4}", next_cell):
            continue
        if idx + 2 != len(cells):
            continue
        previous_text = " ".join(clean_text(value) for value in cells[:idx] if clean_text(value))
        if not re.search(r"\d+(?:\.\d+)+|chapter\s+[A-Z]?\d+", previous_text, re.I):
            continue
        if not implied_blank_label:
            continue
        entries.append({
            "title": implied_blank_label,
            "kind": "topic",
            "printed_page": next_cell,
            "source": "contents_detail",
            "toc_unnumbered": True,
            "inferred_from_blank_toc_cell": True,
        })
    return entries


def split_compact_numbered_toc_titles(text):
    text = clean_text(text)
    if not text:
        return []
    # Popo sometimes collapses TOC cells like
    # "1 Numbers to 101.1 Counting sets..." without separators.
    parent_match = re.match(r"^(\d{1,2})\s+", text)
    if parent_match:
        parent_no = parent_match.group(1)
        text = re.sub(rf"(?<!^)(?={re.escape(parent_no)}\.\d+\s+)", "\n", text)
    text = re.sub(r"(?<!^)(?<!\d)(?=\d{1,2}\.\d+\s+)", "\n", text)
    return [clean_text(part) for part in text.splitlines() if clean_text(part)]


def parse_bare_numbered_toc_parent(text, printed_page):
    text = clean_text(text)
    match = re.match(r"^(\d{1,2})\s+(.+)$", text)
    if not match:
        return None
    title = clean_text(text)
    tail = clean_text(match.group(2))
    if not tail or len(tail) > 120:
        return None
    if BACK_TITLE_RE.match(tail) or FRONT_TITLE_RE.match(tail):
        return None
    if is_instruction_like_title(tail) or LOCAL_LABEL_RE.match(tail):
        return None
    return {
        "title": title,
        "kind": "unit",
        "printed_page": printed_page,
        "source": "contents",
    }


def parse_project_toc_parent(text, printed_page):
    text = clean_text(text)
    if not re.match(r"^project\s+\d+\s*:", text, re.I):
        return None
    if len(text) > 120:
        return None
    return {
        "title": text,
        "kind": "unit",
        "printed_page": printed_page,
        "source": "contents",
    }


def parse_leading_page_toc_row(cells):
    if len(cells) < 2:
        return []
    page = clean_text(cells[0])
    if not re.fullmatch(r"\d{1,4}", page):
        return []
    title_cell = clean_text(cells[1])
    if not title_cell:
        return []
    if re.fullmatch(r"(unit|chapter|section|part|pages?|maths strand|strand)", title_cell, re.I):
        return []
    if re.search(r"\b(glossary|acknowledgements?|index|answers?)\b", title_cell, re.I):
        return []
    chunks = split_compact_numbered_toc_titles(title_cell)
    if not chunks:
        return []
    entries = []
    parent_title = ""
    first = chunks[0]
    parent = parse_bare_numbered_toc_parent(first, page) or parse_project_toc_parent(first, page)
    if parent:
        entries.append(parent)
        parent_title = parent["title"]
        chunks = chunks[1:]
    elif re.match(r"^(?:chapter|unit|part|section)\s+\d+\b", first, re.I):
        parent = parse_toc_entry(f"{first} {page}")
        if parent:
            entries.append(parent)
            parent_title = parent["title"]
            chunks = chunks[1:]
    for chunk in chunks:
        if not re.match(r"^\d{1,2}\.\d+\b", chunk):
            continue
        if len(chunk) > 140:
            continue
        if BACK_TITLE_RE.match(chunk) or FRONT_TITLE_RE.match(chunk):
            continue
        entries.append({
            "title": chunk,
            "kind": "topic",
            "printed_page": None,
            "source": "contents_detail",
            "parent_title": parent_title,
        })
    return entries


def normalize_toc_label_key(label):
    label = clean_text(label)
    return re.sub(r"\s+", " ", label.lower()).strip()


def discover_toc_unnumbered_labels(text):
    labels = {}
    for match in TOC_UNNUMBERED_TOPIC_ANY_RE.finditer(text):
        label = clean_text(match.group(0))
        key = normalize_toc_label_key(label)
        if key not in labels:
            labels[key] = {"label": label, "count": 0}
        labels[key]["count"] += 1
    return labels


def choose_implied_blank_label(text):
    labels = discover_toc_unnumbered_labels(text)
    if not labels:
        return None
    return sorted(labels.values(), key=lambda item: (-item["count"], item["label"].lower()))[0]["label"]


def split_trailing_toc_label(title):
    title = clean_text(title)
    match = None
    for candidate in TOC_TRAILING_SELF_CHECK_RE.finditer(title):
        if candidate.start() > 0:
            match = candidate
            break
    if not match:
        return title, None
    main = title[: match.start()].strip()
    trailing = clean_text(match.group(0))
    return main, trailing


def attach_toc_parents(entries):
    out = []
    current_parent = None
    for entry in entries:
        item = dict(entry)
        if item.get("kind") in {"part", "chapter", "unit"}:
            current_parent = item.get("title")
        elif item.get("kind") == "topic" and current_parent and not item.get("parent_title"):
            item["parent_title"] = current_parent
        out.append(item)
    return out


def promote_inter_unit_modules(entries):
    out = []
    for entry in entries:
        item = dict(entry)
        if (
            item.get("kind") == "topic"
            and item.get("source") == "contents_detail"
            and item.get("parent_title")
            and is_inter_unit_module_title(item.get("title"))
        ):
            item["kind"] = "primary"
            item["detached_from_parent_title"] = item.pop("parent_title", "")
            item["inter_unit_module"] = True
        out.append(item)
    return out


def infer_missing_toc_unnumbered_pages(entries):
    out = [dict(entry) for entry in entries]
    for idx, entry in enumerate(out):
        if not entry.get("toc_unnumbered") or str(entry.get("printed_page") or "").isdigit():
            continue
        for later in out[idx + 1:]:
            if later.get("kind") in {"part", "chapter", "unit"} and str(later.get("printed_page") or "").isdigit():
                inferred = max(1, int(later["printed_page"]) - 1)
                entry["printed_page"] = str(inferred)
                entry["printed_page_inferred"] = True
                break
    return out


def collect_markdown_contents_entries(root):
    root = Path(root)
    entries = []
    for name in ("popo_input.md", "full.md"):
        path = root / name
        if not path.exists():
            continue
        window = find_markdown_toc_window(path.read_text(encoding="utf-8", errors="ignore"))
        if not window:
            continue
        implied_blank_label = choose_implied_blank_label(window)
        table_entry_count = 0
        pos = 0
        current_parent = None
        for match in re.finditer(r"<table\b.*?</table>", window, re.I | re.S):
            if table_entry_count == 0:
                for raw in window[pos:match.start()].splitlines():
                    is_heading_line = bool(re.match(r"^\s*#+\s+", raw))
                    line = clean_text(raw.lstrip("#").strip())
                    entry = parse_toc_entry(line, allow_bare_numbered_chapter=is_heading_line)
                    if entry:
                        entries.append(entry)
                        if entry.get("kind") in {"part", "chapter", "unit"}:
                            current_parent = entry.get("title")
                        continue
                    child = parse_toc_child_entry(line, current_parent)
                    if child:
                        entries.append(child)
            for row in parse_toc_tables(match.group(0)):
                row_entries = parse_toc_table_row(row, implied_blank_label=implied_blank_label)
                if row_entries:
                    table_entry_count += len(row_entries)
                    entries.extend(row_entries)
            pos = match.end()
        if table_entry_count == 0:
            for raw in window[pos:].splitlines():
                is_heading_line = bool(re.match(r"^\s*#+\s+", raw))
                line = clean_text(raw.lstrip("#").strip())
                entry = parse_toc_entry(line, allow_bare_numbered_chapter=is_heading_line)
                if entry:
                    entries.append(entry)
                    if entry.get("kind") in {"part", "chapter", "unit"}:
                        current_parent = entry.get("title")
                    continue
                child = parse_toc_child_entry(line, current_parent)
                if child:
                    entries.append(child)
        if entries:
            break
    return dedupe_entries(promote_inter_unit_modules(infer_missing_toc_unnumbered_pages(attach_toc_parents(entries))))


def content_topic_entries(parent):
    entries = []
    raw_content = str(parent.get("content") or "")
    for raw in re.split(r"<\|txt_split\|>|\n", raw_content):
        raw = clean_text(re.sub(r"(?:\.{2,}|…+)", " ", raw))
        if not raw:
            continue
        matches = list(re.finditer(r"(.+?)\s+(\d{1,4})(?=\s+(?:[A-Z][A-Za-z'’\-]|[●•\-]|\d+(?:\.\d+)+|习题|第|挑战)|$)", raw))
        if not matches:
            m = re.match(r"^(.+?)\s+(\d{1,4})(?:\s*[-–—]\s*\d{1,4})?\s*$", raw)
            matches = [m] if m else []
        for m in matches:
            title = clean_text(m.group(1))
            if title.lower() in {"unit", "chapter", "part", "topic"}:
                continue
            if BACK_TITLE_RE.match(title) or FRONT_TITLE_RE.match(title):
                continue
            if classify_title(title) or LOCAL_LABEL_RE.match(title) or is_instruction_like_title(title):
                continue
            if len(title) < 4 or len(title) > 120:
                continue
            entries.append({"title": title, "kind": "topic", "printed_page": m.group(2), "source": "contents_detail"})
    return entries


def flat_content_path(root):
    for path in sorted(Path(root).glob("*_content_list.json")):
        try:
            data = load_json(path)
        except Exception:
            continue
        if isinstance(data, list) and data and isinstance(data[0], dict) and "type" in data[0]:
            return path
    return None


def flat_lesson_blocks(root):
    path = flat_content_path(root)
    if not path:
        return []
    try:
        blocks = load_json(path)
    except Exception:
        return []
    return blocks if isinstance(blocks, list) else []


def bbox_left(block):
    bbox = block.get("bbox")
    if isinstance(bbox, list) and len(bbox) >= 4:
        return float(bbox[0])
    return 0.0


def bbox_top(block):
    bbox = block.get("bbox")
    if isinstance(bbox, list) and len(bbox) >= 4:
        return float(bbox[1])
    return 0.0


def extract_flat_toc_topics(text):
    raw = clean_text(re.sub(r"(?:\.{2,}|…+)", " ", text))
    if not raw:
        return []
    if len(raw) > 500 and (raw.count(";") >= 4 or re.search(r"/GI\b|Getty|Shutterstock|Photo Library", raw, re.I)):
        return []
    if raw.count(";") >= 8:
        return []
    matches = list(re.finditer(r"(.+?)\s+(\d{1,4})(?=\s+(?:[A-Z][A-Za-z'’\-]|[●•\-]|\d+(?:\.\d+)+|习题|第|挑战)|$)", raw))
    if not matches:
        match = re.match(r"^(.+?)\s+(\d{1,4})(?:\s*[-–—]\s*\d{1,4})?\s*$", raw)
        matches = [match] if match else []
    topics = []
    for match in matches:
        title = clean_text(match.group(1))
        if title.lower() in {"unit", "chapter", "part", "topic"}:
            continue
        if not title or len(title) < 3 or len(title) > 140:
            continue
        if is_toc_category_title(title) or BACK_TITLE_RE.match(title) or FRONT_TITLE_RE.match(title):
            continue
        if classify_title(title) or LOCAL_LABEL_RE.match(title) or is_instruction_like_title(title):
            continue
        topics.append({"title": title, "printed_page": match.group(2)})
    return topics


def nearest_toc_unit(block, unit_blocks):
    if not unit_blocks:
        return None
    left = bbox_left(block)
    top = bbox_top(block)
    candidates = [unit for unit in unit_blocks if unit["top"] <= top + 0.03]
    if not candidates:
        return None
    return min(candidates, key=lambda unit: (abs(left - unit["left"]), -unit["top"]))


def category_for_explicit_toc_block(block, category_blocks):
    top = bbox_top(block)
    candidates = [category for category in category_blocks if category["top"] <= top + 0.03]
    if not candidates:
        return None
    return max(candidates, key=lambda category: category["top"])["title"]


def collect_flat_contents_entries(root):
    path = flat_content_path(root)
    if not path:
        return []
    blocks = load_json(path)
    by_page = {}
    global_category_titles = set()
    for block in blocks:
        page = block.get("page_idx")
        if isinstance(page, int) and page <= 12 and block.get("type") == "text":
            by_page.setdefault(page, []).append(block)
            text = clean_text(block.get("text"))
            if block.get("text_level") is not None and is_toc_category_title(text):
                global_category_titles.add(text.lower())
    allow_implicit_category_sequence = len(global_category_titles) >= 3

    entries = []
    active_unit = None
    for page_idx in sorted(by_page):
        page_blocks = by_page[page_idx]
        unit_blocks = []
        category_blocks = []
        for block in page_blocks:
            text = clean_text(block.get("text"))
            unit = unit_number(text)
            if unit is not None and block.get("text_level") is not None:
                unit_blocks.append({
                    "title": f"Unit {unit}",
                    "left": bbox_left(block),
                    "top": bbox_top(block),
                    "source_page": page_idx + 1,
                })
            elif block.get("text_level") is not None and is_toc_category_title(text):
                category_blocks.append({
                    "title": clean_text(text),
                    "left": bbox_left(block),
                    "top": bbox_top(block),
                    "source_page": page_idx + 1,
                })
        if not unit_blocks:
            if not active_unit:
                continue
        unit_blocks = sorted(unit_blocks, key=lambda item: (item["top"], item["left"]))
        category_blocks = sorted(category_blocks, key=lambda item: (item["top"], item["left"]))
        topic_items = []
        if category_blocks:
            for block in page_blocks:
                text = clean_text(block.get("text"))
                unit_no = unit_number(text)
                if unit_no is not None and block.get("text_level") is not None:
                    active_unit = {
                        "title": f"Unit {unit_no}",
                        "left": bbox_left(block),
                        "top": bbox_top(block),
                        "source_page": page_idx + 1,
                    }
                if not text or block.get("text_level") is not None:
                    continue
                topics = extract_flat_toc_topics(text)
                if not topics:
                    continue
                unit = nearest_toc_unit(block, unit_blocks)
                if not unit:
                    continue
                topic_items.append({
                    "block": block,
                    "unit": unit,
                    "topics": topics,
                    "category": category_for_explicit_toc_block(block, category_blocks),
                    "seq": len(topic_items),
                })
        else:
            active_category = None
            for block in page_blocks:
                text = clean_text(block.get("text"))
                if not text:
                    continue
                unit_no = unit_number(text)
                if unit_no is not None and block.get("text_level") is not None:
                    active_unit = {
                        "title": f"Unit {unit_no}",
                        "left": bbox_left(block),
                        "top": bbox_top(block),
                        "source_page": page_idx + 1,
                    }
                    active_category = None
                    continue
                if block.get("text_level") is not None and is_toc_category_title(text):
                    active_category = clean_text(text)
                    continue
                topics = extract_flat_toc_topics(text)
                if not topics or not active_unit:
                    continue
                topic_items.append({
                    "block": block,
                    "unit": active_unit,
                    "topics": topics,
                    "category": active_category,
                    "seq": len(topic_items),
                })

        if not category_blocks and allow_implicit_category_sequence:
            by_unit_title = {}
            for item in topic_items:
                by_unit_title.setdefault(item["unit"]["title"], []).append(item)
            for unit_title, items in by_unit_title.items():
                for idx, item in enumerate(sorted(items, key=lambda value: bbox_top(value["block"]))):
                    item["category"] = TOC_CATEGORY_TITLES[min(idx, len(TOC_CATEGORY_TITLES) - 1)]

        emitted_categories = set()
        emitted_units = set()
        if category_blocks:
            ordered_topic_items = sorted(topic_items, key=lambda value: (value["unit"]["top"], value["unit"]["left"], bbox_top(value["block"]), bbox_left(value["block"])))
        else:
            ordered_topic_items = sorted(topic_items, key=lambda value: value.get("seq", 0))
        for item in ordered_topic_items:
            unit_title = item["unit"]["title"]
            if unit_title not in emitted_units:
                entries.append({
                    "title": unit_title,
                    "kind": "unit",
                    "printed_page": None,
                    "source": "contents",
                    "source_page": item["unit"]["source_page"],
                })
                emitted_units.add(unit_title)
            category = item.get("category")
            for topic in item["topics"]:
                if category:
                    category_key = (unit_title, category)
                    if category_key not in emitted_categories:
                        entries.append({
                            "title": category,
                            "kind": "category",
                            "printed_page": topic.get("printed_page"),
                            "source": "contents_category",
                            "parent_title": unit_title,
                            "source_page": item["unit"]["source_page"],
                        })
                        emitted_categories.add(category_key)
                entry = {
                    "title": topic["title"],
                    "kind": "topic",
                    "printed_page": topic.get("printed_page"),
                    "source": "contents_detail",
                    "parent_title": unit_title,
                    "source_page": item["unit"]["source_page"],
                }
                if category:
                    entry["category_title"] = category
                entries.append(entry)
    return dedupe_entries(entries)


def flat_contents_back_start_page(root, printed_page_offset):
    if not isinstance(printed_page_offset, int):
        return None
    path = flat_content_path(root)
    if not path:
        return None
    blocks = load_json(path)
    candidates = []
    for block in blocks:
        page = block.get("page_idx")
        if not isinstance(page, int) or page > 12 or block.get("type") != "text":
            continue
        text = clean_text(block.get("text"))
        if not text:
            continue
        for match in re.finditer(r"\b(answers?|appendix|glossary|index)\b\s+(\d{1,4})", text, re.I):
            printed = int(match.group(2))
            if printed > 20:
                candidates.append(printed + printed_page_offset)
    return min(candidates) if candidates else None


def is_toc_row(row):
    title = clean_text(row.get("title"))
    path = clean_text(row.get("path_text"))
    return bool(re.search(r"(^| / )(contents|table of contents|目录|目次)( / |$)", path, re.I) or re.match(r"^(contents|table of contents|目录|目次)$", title, re.I))


def is_contents_title(row):
    title = clean_text(row.get("title"))
    return bool(re.match(r"^(contents|table of contents|目录|目次)$", title, re.I))


def content_has_page_anchors(row):
    content = clean_text(row.get("content"))
    return bool(re.search(r"(?:\.{2,}|…+|\s)\d{1,4}(?:\s+|$)", content))


def is_early_toc_entry(row, scan_limit=12):
    page = first_page(row)
    if page is None or page > scan_limit:
        return False
    title = clean_text(row.get("title"))
    if not classify_title(title):
        return False
    if row.get("children_count") and not is_toc_row(row):
        return False
    return content_has_page_anchors(row) or (is_toc_row(row) and page <= 6)


def collect_contents_entries(rows):
    entries = []
    for row in rows:
        page = first_page(row)
        if is_toc_row(row) and str(row.get("content") or "").lstrip().lower().startswith("<table"):
            for table_row in parse_toc_tables(str(row.get("content") or "")):
                for entry in parse_toc_table_row(table_row):
                    if page is not None:
                        entry["source_page"] = page
                    entries.append(entry)
        if page is None or page > 12:
            continue
        in_contents_page = is_toc_row(row) and page <= 6
        if not (is_contents_title(row) or is_early_toc_entry(row) or in_contents_page):
            continue
        title_entry = parse_toc_entry(row.get("title"))
        if title_entry and classify_title(row.get("title")):
            title_entry["source_page"] = page
            entries.append(title_entry)
        if not (content_has_page_anchors(row) or in_contents_page):
            continue
        for raw in split_toc_items(row):
            entry = parse_toc_entry(raw)
            if entry:
                entry["source_page"] = page
                entries.append(entry)
        parent = title_entry if title_entry and title_entry.get("kind") in {"part", "chapter", "unit"} else None
        if parent:
            for child in content_topic_entries(row):
                child["parent_title"] = parent["title"]
                child["source_page"] = page
                entries.append(child)
    return dedupe_entries(entries)


def dedupe_entries(entries):
    out = []
    seen = set()
    for entry in entries:
        key = entry_dedupe_key(entry)
        if not key[1] or key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def remove_duplicate_detail_entries(primary_entries, secondary_entries):
    detail_keys = {
        (normalize(entry.get("title")), str(entry.get("printed_page") or ""))
        for entry in primary_entries
        if entry.get("kind") in {"topic", "category"} and normalize(entry.get("title"))
    }
    out = []
    for entry in secondary_entries:
        if entry.get("kind") in {"part", "chapter", "unit"}:
            out.append(entry)
            continue
        key = (normalize(entry.get("title")), str(entry.get("printed_page") or ""))
        if key in detail_keys:
            continue
        out.append(entry)
    return out


def entry_dedupe_key(entry):
    title = clean_text(entry.get("title"))
    identity = entry_identity(entry)
    if identity:
        return (entry.get("kind"), identity, entry.get("parent_title") or "")
    numbered = re.match(r"^(\d+(?:\.\d+)+)\b", title)
    if numbered:
        return (entry.get("kind"), numbered.group(1), entry.get("parent_title") or "")
    if entry.get("toc_unnumbered"):
        return (entry.get("kind"), normalize(title), entry.get("parent_title") or entry.get("printed_page") or "")
    return (entry.get("kind"), normalize(title), entry.get("parent_title") or "")


def front_last_page(rows):
    front_pages = []
    for row in rows:
        title = clean_text(row.get("title"))
        page = first_page(row)
        if page is None:
            continue
        front_title = HARD_FRONT_TITLE_RE.match(title) or (page <= 6 and FRONT_TITLE_RE.match(title))
        if front_title or is_contents_title(row) or is_early_toc_entry(row):
            front_pages.extend(p for p in row.get("pages") or [] if p <= 20)
    return max(front_pages) if front_pages else 0


def fill_unanchored_parent_pages(entries):
    out = [dict(entry) for entry in entries]
    for idx, entry in enumerate(out):
        if isinstance(entry.get("start_page"), int):
            continue
        level = entry.get("level")
        if not isinstance(level, int):
            continue
        for child in out[idx + 1:]:
            child_level = child.get("level")
            if isinstance(child_level, int) and child_level <= level:
                break
            if isinstance(child.get("start_page"), int):
                entry["start_page"] = child["start_page"]
                entry["start_page_idx"] = child.get("start_page_idx")
                entry["anchor_title"] = entry.get("anchor_title") or entry.get("title")
                entry["anchor_method"] = "first_child_anchor"
                break
    return out


def fill_parent_pages_from_printed(entries):
    offsets = []
    for entry in entries:
        if not isinstance(entry.get("start_page"), int):
            continue
        printed = entry.get("printed_page")
        if not str(printed or "").isdigit():
            continue
        offsets.append(entry["start_page"] - int(printed))
    if not offsets:
        return entries
    offsets = sorted(offsets)
    offset = offsets[len(offsets) // 2]
    out = []
    for entry in entries:
        item = dict(entry)
        if (
            item.get("kind") in {"part", "chapter", "unit"}
            and not isinstance(item.get("start_page"), int)
            and str(item.get("printed_page") or "").isdigit()
        ):
            start_page = int(item["printed_page"]) + offset
            item["start_page"] = start_page
            item["start_page_idx"] = start_page - 1
            item["anchor_title"] = item.get("anchor_title") or item.get("title")
            item["anchor_method"] = "printed_page_offset"
        out.append(item)
    return out


def body_candidate_rows(rows, last_front):
    candidates = []
    for row in rows:
        if row.get("type") != "text":
            continue
        title = clean_text(row.get("title"))
        page = first_page(row)
        if page is None or page <= last_front:
            continue
        if BACK_TITLE_RE.match(title) or FRONT_TITLE_RE.match(title):
            continue
        kind = classify_title(title)
        if kind or row.get("children_count"):
            candidates.append(row)
    return candidates


def infer_printed_page_offset(entries, rows, last_front):
    offsets = {}
    for entry in entries:
        if not str(entry.get("printed_page") or "").isdigit():
            continue
        printed_page = int(entry["printed_page"])
        target = normalize(entry.get("title"))
        if not target:
            continue
        for row in rows:
            page = first_page(row)
            if page is None or page <= last_front:
                continue
            title = row_match_title(row)
            key = normalize(title)
            if not key:
                continue
            if key != target and clean_text(title).lower() != clean_text(entry.get("title")).lower():
                continue
            offset = page - printed_page
            if -80 <= offset <= 80:
                offsets[offset] = offsets.get(offset, 0) + 1
    if not offsets:
        return None
    return sorted(offsets.items(), key=lambda item: (-item[1], abs(item[0])))[0][0]


def match_anchor(entry, rows, last_front, printed_page_offset=None):
    target = normalize(entry.get("title"))
    if not target:
        return None
    if (
        entry.get("kind") == "part"
        and re.match(r"^section\s+\d+\b", clean_text(entry.get("title")), re.I)
        and not str(entry.get("printed_page") or "").isdigit()
    ):
        return None
    best = None
    printed_page = None
    if str(entry.get("printed_page") or "").isdigit():
        printed_page = int(entry.get("printed_page"))
    expected_page = None
    if printed_page is not None and isinstance(printed_page_offset, int):
        expected_page = printed_page + printed_page_offset
    entry_unit_number = unit_number(entry.get("title")) if entry.get("kind") == "unit" else None
    if entry_unit_number is not None:
        numbered_best = None
        for row in rows:
            page = first_page(row)
            if page is None or page <= last_front:
                continue
            title = row_match_title(row)
            if unit_number(title) != entry_unit_number:
                continue
            if BACK_TITLE_RE.match(title) or FRONT_TITLE_RE.match(title):
                continue
            children_count = row.get("children_count") or 0
            exact_text = 1 if title.lower() == clean_text(entry.get("title")).lower() else 0
            key = normalize(title)
            direct = 1 if key == target else 0
            contains = 1 if target and normalized_contains(target, key) else 0
            if expected_page is not None:
                page_score = -abs(page - expected_page)
            elif printed_page is not None:
                page_score = -abs(page - printed_page)
            else:
                page_score = -page
            score = (children_count, exact_text, direct, contains, page_score, -row.get("depth", 0))
            if numbered_best is None or score > numbered_best[0]:
                numbered_best = (score, row)
        if numbered_best is not None:
            row = numbered_best[1]
            page = first_page(row)
            if expected_page is not None and isinstance(page, int) and abs(page - expected_page) > 12:
                return None
            return row
    entry_chapter_number = entry.get("chapter_number") or (chapter_number(entry.get("title")) if entry.get("kind") == "chapter" else None)
    if entry.get("kind") == "chapter" and entry_chapter_number:
        chapter_best = None
        for row in rows:
            page = first_page(row)
            if page is None or page <= last_front:
                continue
            title = row_match_title(row)
            if chapter_number(title) != str(entry_chapter_number).upper():
                continue
            if BACK_TITLE_RE.match(title) or FRONT_TITLE_RE.match(title):
                continue
            if expected_page is not None:
                page_score = -abs(page - expected_page)
            elif printed_page is not None:
                page_score = -abs(page - printed_page)
            else:
                page_score = -page
            score = (
                1 if re.match(r"^chapter\s+", clean_text(title), re.I) else 0,
                row.get("children_count") or 0,
                page_score,
                -row.get("depth", 0),
            )
            if chapter_best is None or score > chapter_best[0]:
                chapter_best = (score, row)
        if chapter_best is not None:
            row = chapter_best[1]
            page = first_page(row)
            if expected_page is not None and isinstance(page, int) and abs(page - expected_page) > 12:
                return None
            return row
    for row in rows:
        page = first_page(row)
        if page is None or page <= last_front:
            continue
        if (
            entry.get("kind") == "topic"
            and entry.get("parent_title")
            and not row_matches_topic_parent(entry, row)
        ):
            continue
        if entry.get("toc_unnumbered") and not row_matches_topic_parent(entry, row):
            continue
        title = row_match_title(row)
        key = normalize(title)
        if not key:
            continue
        direct = key == target
        contains = bool(target and normalized_contains(target, key))
        if not direct and not contains:
            continue
        exact_text = 1 if clean_text(title).lower() == clean_text(entry.get("title")).lower() else 0
        kind_score = 1 if classify_title(title) == entry.get("kind") else 0
        if entry.get("kind") in {"part", "chapter", "unit"} and not (direct or exact_text or kind_score):
            continue
        if entry.get("kind") in {"part", "chapter"} and not (direct or exact_text):
            continue
        has_children = 1 if row.get("children_count") else 0
        if expected_page is not None:
            page_score = -abs(page - expected_page)
        elif printed_page is not None:
            page_score = -abs(page - printed_page)
        else:
            page_score = -page
        parent_unit = unit_number(entry.get("parent_title")) if entry.get("kind") == "topic" else None
        title_unit = unit_number(title)
        parent_specific = 1 if parent_unit is not None and title_unit == parent_unit else 0
        if (
            entry.get("kind") == "topic"
            and expected_page is not None
            and GENERIC_REPEATED_TOC_TOPIC_RE.match(clean_text(entry.get("title")))
        ):
            score = (parent_specific, page_score, exact_text, kind_score, 2 if direct else 1, has_children, -row.get("depth", 0))
        else:
            score = (exact_text, kind_score, 2 if direct else 1, has_children, page_score, parent_specific, -row.get("depth", 0))
        if best is None or score > best[0]:
            best = (score, row)
    if not best:
        return None
    row = best[1]
    if printed_page is not None and entry.get("source") in {"contents", "contents_detail"}:
        page = first_page(row)
        selected_title = row_match_title(row)
        exact_selected_title = selected_title.lower() == clean_text(entry.get("title")).lower()
        direct_selected_title = normalize(selected_title) == target
        parent_matched = row_matches_topic_parent(entry, row)
        if (
            expected_page is not None
            and isinstance(page, int)
            and abs(page - expected_page) > 12
            and not (
                entry.get("kind") == "topic"
                and entry.get("parent_title")
                and (exact_selected_title or direct_selected_title)
                and parent_matched
            )
        ):
            return None
        if expected_page is None and isinstance(page, int) and abs(page - printed_page) > 35 and not (exact_selected_title or direct_selected_title):
            return None
    return row


def infer_hierarchy(entries):
    has_part = any(e.get("kind") == "part" for e in entries)
    has_chapter = any(e.get("kind") == "chapter" for e in entries)
    current_part = None
    current_parent = None
    out = []
    for entry in entries:
        entry = dict(entry)
        kind = entry.get("kind")
        if kind == "part":
            entry["level"] = 1
            current_part = entry
            current_parent = entry
        elif kind == "primary":
            entry["level"] = 1
            current_parent = entry
        elif kind == "chapter":
            entry["level"] = 2 if has_part else 1
            current_parent = entry
        elif kind == "unit":
            entry["level"] = 2 if has_part else (2 if has_chapter else 1)
            current_parent = entry
        elif kind == "category":
            entry["level"] = min(3, (current_parent or current_part or {}).get("level", 1) + 1)
        else:
            entry["level"] = min(3, (current_parent or current_part or {}).get("level", 1) + 1)
        entry["level"] = max(1, min(3, int(entry["level"])))
        out.append(entry)
    return out


def parent_title_for_topic(entry):
    parent = clean_text(entry.get("parent_title"))
    if parent:
        return parent
    title = clean_text(entry.get("title"))
    match = re.match(r"^(Chapter\s+[A-Z]?\d+)\.\s*Topic\s+\d+\b", title, re.I)
    if match:
        return match.group(1)
    match = re.match(r"^(\d+)\.\d+\b", title)
    if match:
        return None
    return None


def ensure_topic_parents(entries):
    out = []
    parent_by_key = {}
    for entry in entries:
        if entry.get("kind") in {"chapter", "unit", "part", "lesson", "primary"}:
            parent_by_key[entry_identity(entry)] = entry
        out.append(dict(entry))
    additions = []
    for entry in out:
        if entry.get("kind") != "topic":
            continue
        parent_title = parent_title_for_topic(entry)
        if not parent_title:
            continue
        lesson_parent = bool(lesson_code_match(parent_title))
        if lesson_parent:
            parent_code = lesson_code_label(parent_title)
            if any(
                candidate.get("kind") == "lesson"
                and lesson_code_label(candidate.get("title")) == parent_code
                for candidate in parent_by_key.values()
            ):
                continue
        kind = "lesson" if lesson_parent else (classify_title(parent_title) or "chapter")
        parent = {
            "title": parent_title,
            "kind": kind,
            "source": "synthetic_parent_from_topic",
            "anchor_title": parent_title,
            "start_page": entry.get("start_page"),
            "start_page_idx": entry.get("start_page_idx"),
            "anchor_method": "first_child_anchor",
            "level": max(1, int(entry.get("level") or 2) - 1),
        }
        key = entry_identity(parent)
        existing = parent_by_key.get(key)
        if existing:
            continue
        existing_title = clean_text(existing.get("title")) if existing else ""
        if key and (not existing or re.search(r"\bknowledge\s+test\b|section\s+\d+\s+review", existing_title, re.I)):
            parent_by_key[key] = parent
            additions.append(parent)
    return additions + out


def fallback_primary_entry(rows, last_front):
    for row in rows:
        if row.get("type") != "text":
            continue
        page = first_page(row)
        title = clean_text(row.get("title"))
        if page is None or page <= last_front or not title:
            continue
        if FRONT_TITLE_RE.match(title) or BACK_TITLE_RE.match(title) or LOCAL_LABEL_RE.match(title):
            continue
        if len(title) > 120:
            continue
        return {
            "title": title,
            "kind": "primary",
            "source": "popo_primary_title_fallback",
            "anchor_title": title,
            "start_page": page,
            "start_page_idx": page - 1,
            "depth": row.get("depth"),
            "popo_level": row.get("level"),
            "children_count": row.get("children_count") or 0,
            "block_ids": row.get("block_ids") or [],
            "level": 1,
        }
    return None


def is_meaningful_tree_heading(title):
    title = clean_text(title)
    if not title or FRONT_TITLE_RE.match(title) or BACK_TITLE_RE.match(title):
        return False
    if is_instruction_like_title(title):
        return False
    if re.match(r"^[一二三四五六七八九十]+[、.．]\s*(选择题|填空题|解答题|简答题|思维与拓展)\b", title):
        return False
    if re.match(r"^[一二三四五六七八九十]+[、.．]\s*\S+", title):
        return True
    if re.match(r"^\d+[.．]\s*\S+", title) and not re.match(r"^\d+[.．]\s*(选择题|填空题|解答题|简答题)\b", title):
        return True
    if classify_title(title):
        return True
    return False


def tree_hierarchy_fallback(rows, last_front):
    root = None
    for row in rows:
        if row.get("type") != "text":
            continue
        page = first_page(row)
        title = clean_text(row.get("title"))
        if page is None or page <= last_front or not title:
            continue
        if FRONT_TITLE_RE.match(title) or BACK_TITLE_RE.match(title) or LOCAL_LABEL_RE.match(title):
            continue
        if row.get("children_count"):
            root = row
            break
    if not root:
        return []
    root_title = clean_text(root.get("title"))
    root_depth = root.get("depth") if isinstance(root.get("depth"), int) else 1
    out = [{
        "title": root_title,
        "kind": "primary",
        "source": "popo_tree_hierarchy_fallback",
        "anchor_title": root_title,
        "start_page": first_page(root),
        "start_page_idx": first_page(root) - 1 if first_page(root) is not None else None,
        "depth": root.get("depth"),
        "popo_level": root.get("level"),
        "children_count": root.get("children_count") or 0,
        "block_ids": root.get("block_ids") or [],
        "level": 1,
    }]
    root_path = root.get("path") or [root_title]
    for row in rows:
        if row is root or row.get("type") != "text":
            continue
        path = row.get("path") or []
        if not path[: len(root_path)] == root_path:
            continue
        depth = row.get("depth")
        if not isinstance(depth, int) or depth < 2 or depth > 3:
            continue
        title = clean_text(row.get("title"))
        if not is_meaningful_tree_heading(title):
            continue
        page = first_page(row)
        out.append({
            "title": title,
            "kind": classify_title(title) or "topic",
            "source": "popo_tree_hierarchy_fallback",
            "anchor_title": title,
            "start_page": page,
            "start_page_idx": page - 1 if page is not None else None,
            "depth": depth,
            "popo_level": row.get("level"),
            "children_count": row.get("children_count") or 0,
            "block_ids": row.get("block_ids") or [],
            "level": max(1, min(3, depth - root_depth + 1)),
        })
    return out if len(out) > 1 else []


def fallback_entries(rows, last_front):
    entries = []
    for row in body_candidate_rows(rows, last_front):
        title = clean_text(row.get("title"))
        kind = classify_title(title)
        if not kind:
            continue
        first_child_title = clean_text(row.get("first_child_title"))
        if re.match(r"^chapter\s+[A-Z]?\d+\.\s*topic\s+\d+\b", title, re.I):
            if first_child_title and not LOCAL_LABEL_RE.match(first_child_title) and not FRONT_TITLE_RE.match(first_child_title) and not BACK_TITLE_RE.match(first_child_title) and not is_instruction_like_title(first_child_title):
                title = first_child_title
                validation_required = "generic_topic_title_replaced_from_child"
            else:
                validation_required = "generic_topic_title_unresolved"
        else:
            validation_required = None
        item = {
            "title": title,
            "kind": kind,
            "source": "popo_body_heading",
            "anchor_title": title,
            "start_page": first_page(row),
            "start_page_idx": first_page(row) - 1 if first_page(row) is not None else None,
            "depth": row.get("depth"),
            "popo_level": row.get("level"),
            "children_count": row.get("children_count") or 0,
            "block_ids": row.get("block_ids") or [],
        }
        if validation_required:
            item["validation_required"] = validation_required
        entries.append(item)
    entries = sorted(entries, key=lambda item: (-(item.get("children_count") or 0), item.get("start_page") or 10**9))
    entries.extend(structural_fallback_entries(rows, last_front))
    return infer_hierarchy(dedupe_entries(entries))


def structural_heading_title(rows, idx):
    row = rows[idx]
    title = clean_text(row.get("title"))
    page = first_page(row)
    depth = row.get("depth")
    pieces = [title]
    for prev in reversed(rows[max(0, idx - 3):idx]):
        if prev.get("type") != "text" or first_page(prev) != page or prev.get("depth") != depth:
            continue
        if prev.get("children_count"):
            continue
        prev_title = clean_text(prev.get("title"))
        if not prev_title or LOCAL_LABEL_RE.match(prev_title) or FRONT_TITLE_RE.match(prev_title) or BACK_TITLE_RE.match(prev_title):
            continue
        if len(prev_title) > 80:
            continue
        pieces.insert(0, prev_title)
    title = clean_text(" ".join(pieces))
    title = re.sub(r"^>\s*", "", title)
    return clean_text(title)


def structural_fallback_entries(rows, last_front):
    entries = []
    for idx, row in enumerate(rows):
        if row.get("type") != "text":
            continue
        page = first_page(row)
        title = structural_heading_title(rows, idx)
        if page is None or page <= last_front or not title:
            continue
        if row.get("depth", 9) > 2:
            continue
        if re.fullmatch(r"\d+", title):
            continue
        if classify_title(title) or LOCAL_LABEL_RE.match(title) or FRONT_TITLE_RE.match(title) or BACK_TITLE_RE.match(title) or is_instruction_like_title(title):
            continue
        children = row.get("children_count") or 0
        strong_module_title = bool(re.search(r"\b(section|skills)\b|技能|方法", title, re.I))
        if children < 8 and not (row.get("depth") == 1 and strong_module_title and children >= 1):
            continue
        entries.append({
            "title": title,
            "kind": "chapter",
            "source": "popo_structural_heading",
            "anchor_title": title,
            "start_page": page,
            "start_page_idx": page - 1,
            "depth": row.get("depth"),
            "popo_level": row.get("level"),
            "children_count": children,
            "block_ids": row.get("block_ids") or [],
        })
    return entries


def good_lesson_label(title):
    title = clean_display_title(title)
    if not title or lesson_code_match(title):
        return False
    if re.fullmatch(r"\d{1,4}", title):
        return False
    if LOCAL_LABEL_RE.match(title) or FRONT_TITLE_RE.match(title) or BACK_TITLE_RE.match(title):
        return False
    if is_instruction_like_title(title):
        return False
    if len(title) > 90:
        return False
    return True


def direct_text_children(rows, parent_row):
    parent_path = parent_row.get("path") or []
    parent_depth = parent_row.get("depth")
    if not parent_path or not isinstance(parent_depth, int):
        return []
    children = []
    for row in rows:
        if row is parent_row or row.get("type") != "text":
            continue
        path = row.get("path") or []
        if len(path) != len(parent_path) + 1 or path[: len(parent_path)] != parent_path:
            continue
        if row.get("depth") != parent_depth + 1:
            continue
        children.append(row)
    return children


def unit_titles_by_number(entries):
    titles = {}
    for entry in entries:
        if entry.get("kind") != "unit":
            continue
        unit = unit_number(entry.get("title"))
        if unit is not None:
            titles.setdefault(unit, clean_text(entry.get("title")))
    return titles


def unit_page_ranges_by_number(entries):
    units = []
    for entry in entries:
        if entry.get("kind") != "unit":
            continue
        unit = unit_number(entry.get("title"))
        start = entry.get("start_page")
        if unit is not None and isinstance(start, int):
            units.append((unit, start))
    ranges = {}
    for idx, (unit, start) in enumerate(sorted(units, key=lambda item: item[1])):
        end = None
        for _, next_start in sorted(units, key=lambda item: item[1])[idx + 1:]:
            if next_start > start:
                end = next_start
                break
        ranges[unit] = (start, end)
    return ranges


def collect_lesson_entries(rows, parent_entries, last_front):
    parent_by_unit = unit_titles_by_number(parent_entries)
    unit_ranges = unit_page_ranges_by_number(parent_entries)
    if not parent_by_unit:
        return []
    entries = []
    seen_lessons = set()
    for row in rows:
        row_type = row.get("type")
        if row_type not in {"text", "header", "footer"}:
            continue
        page = first_page(row)
        if page is None or page <= last_front:
            continue
        row_title = row_match_title(row) if row_type in {"header", "footer"} else row.get("title")
        code = lesson_code_label(row_title)
        unit = lesson_unit_number(row_title)
        if not code or unit not in parent_by_unit:
            continue
        unit_start, unit_end = unit_ranges.get(unit, (None, None))
        if isinstance(unit_start, int) and page < unit_start:
            continue
        if isinstance(unit_end, int) and page >= unit_end:
            continue
        children = direct_text_children(rows, row) if row_type == "text" and row.get("children_count") else []
        lesson_label = ""
        for child in children:
            child_title = clean_display_title(child.get("title"))
            if good_lesson_label(child_title):
                lesson_label = child_title
                break
        if row_type == "text" and not row.get("children_count") and not lesson_label:
            continue
        display_title = f"{code} {lesson_label}".strip()
        lesson_key = (unit, code)
        if lesson_key in seen_lessons:
            continue
        seen_lessons.add(lesson_key)
        lesson_entry = {
            "title": display_title,
            "kind": "lesson",
            "source": "popo_body_lesson",
            "parent_title": parent_by_unit[unit],
            "anchor_title": code,
            "match_titles": [code] + ([lesson_label] if lesson_label else []),
            "start_page": page,
            "start_page_idx": page - 1,
            "depth": row.get("depth"),
            "popo_level": row.get("level"),
            "children_count": row.get("children_count") or 0,
            "block_ids": row.get("block_ids") or [],
            "level": 2,
        }
        entries.append(lesson_entry)
        for child in children:
            child_title = clean_display_title(child.get("title"))
            if not good_lesson_label(child_title):
                continue
            if lesson_label and normalize(child_title) == normalize(lesson_label):
                continue
            if not child.get("children_count"):
                continue
            child_page = first_page(child)
            if child_page is None:
                continue
            if isinstance(unit_start, int) and child_page < unit_start:
                continue
            if isinstance(unit_end, int) and child_page >= unit_end:
                continue
            entries.append({
                "title": child_title,
                "kind": "topic",
                "source": "popo_body_lesson_child",
                "parent_title": display_title,
                "anchor_title": child_title,
                "start_page": child_page,
                "start_page_idx": child_page - 1,
                "depth": child.get("depth"),
                "popo_level": child.get("level"),
                "children_count": child.get("children_count") or 0,
                "block_ids": child.get("block_ids") or [],
                "level": 3,
            })
    return entries


def lesson_parent_key(title):
    code = lesson_code_label(title)
    return f"lesson:{code}" if code else normalize(title)


def row_has_lesson_code(row, code):
    if not code:
        return False
    for part in row.get("path") or []:
        if lesson_code_label(part) == code or clean_display_title(part).upper() == code:
            return True
    return lesson_code_label(row.get("title")) == code


SUPPORT_TOKEN_STOPWORDS = {"a", "an", "and", "of", "the"}


def support_tokens(value):
    normalized = normalize(clean_text(value).replace("&", " and "))
    return [token for token in normalized.split() if token and token not in SUPPORT_TOKEN_STOPWORDS]


def tokens_in_order(needles, haystack):
    if not needles:
        return False
    pos = 0
    for needle in needles:
        try:
            found = haystack.index(needle, pos)
        except ValueError:
            return False
        pos = found + 1
    return True


def title_supported_by_reading_cell(title, reading_cell):
    title_key = normalize(clean_text(title).replace("&", " and "))
    support_key = normalize(clean_text(reading_cell).replace("&", " and "))
    if not title_key or not support_key:
        return False
    title_tokens = support_tokens(title)
    support = support_tokens(reading_cell)
    if len(title_tokens) >= 2 and tokens_in_order(title_tokens, support):
        return True
    if len(title_tokens) == 1 and len(title_tokens[0]) >= 7 and title_tokens[0] in support:
        return True
    if len(title_tokens) < 2:
        return False
    return title_key.replace(" ", "") in support_key.replace(" ", "")


def expanded_reading_cell_text(reading_cell):
    text = clean_text(reading_cell)
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[a-z])(?=\d)", " ", text)
    return clean_text(text)


def token_matches_at(tokens, needle, start):
    if start + len(needle) > len(tokens):
        return False
    return tokens[start:start + len(needle)] == needle


def infer_missing_reading_title(reading_cell, known_titles):
    expanded = expanded_reading_cell_text(reading_cell)
    raw_tokens = re.findall(r"[A-Za-z0-9]+(?:['’:-][A-Za-z0-9]+)*|[\u4e00-\u9fff]+", expanded)
    if not raw_tokens:
        return ""
    normalized_tokens = [normalize(token) for token in raw_tokens]
    keep = [True] * len(raw_tokens)
    for title in known_titles:
        needle = support_tokens(title)
        if not needle:
            continue
        for idx in range(len(normalized_tokens)):
            if token_matches_at(normalized_tokens, needle, idx):
                for offset in range(len(needle)):
                    keep[idx + offset] = False
                break
    remaining = [token for token, use in zip(raw_tokens, keep) if use]
    title = clean_text(" ".join(remaining))
    if not title or not title_supported_by_reading_cell(title, reading_cell):
        return ""
    return title


def split_lesson_labels(cell, unit):
    labels = {}
    if unit is None:
        return labels
    for match in re.finditer(r"([A-Z])\s*:\s*(.*?)(?=[A-Z]\s*:|$)", clean_text(cell), re.I):
        label = clean_text(match.group(2))
        if label:
                labels[f"{int(unit)}{match.group(1).upper()}"] = label
    return labels


def split_reading_titles(cell, expected_count):
    text = clean_text(cell)
    if not text or expected_count <= 0:
        return []
    if expected_count == 1:
        return [text]
    marked = re.sub(r"(?<=[a-z?])(?=[A-Z])", "\n", text)
    chunks = [clean_text(item) for item in marked.splitlines() if clean_text(item)]
    if len(chunks) == expected_count:
        return chunks
    chunks = [clean_text(item) for item in re.split(r"\s{2,}|[;；]\s*", text) if clean_text(item)]
    if len(chunks) == expected_count:
        return chunks
    return []


def collect_toc_reading_cells(rows, last_front):
    specs = {}
    for row in rows:
        page = first_page(row)
        if row.get("type") != "table" or page is None or page > max(12, last_front):
            continue
        table_rows = parse_toc_tables(row.get("content") or "")
        header = None
        header_map = {}
        for cells in table_rows:
            labels = [clean_text(cell).lower() for cell in cells]
            if "lesson" in labels and "reading" in labels and "unit" in labels:
                header = labels
                header_map = {label: idx for idx, label in enumerate(header)}
                continue
            if not header:
                continue
            unit_idx = header_map.get("unit")
            lesson_idx = header_map.get("lesson")
            reading_idx = header_map.get("reading")
            if unit_idx is None or lesson_idx is None or reading_idx is None:
                continue
            if max(unit_idx, lesson_idx, reading_idx) >= len(cells):
                continue
            unit_match = re.match(r"^\s*(\d{1,2})\b", clean_text(cells[unit_idx]))
            if not unit_match:
                continue
            unit = int(unit_match.group(1))
            reading_cell = clean_text(cells[reading_idx])
            if not reading_cell:
                continue
            lesson_labels = split_lesson_labels(cells[lesson_idx], unit)
            reading_titles = split_reading_titles(reading_cell, len(lesson_labels))
            for position, (code, lesson_label) in enumerate(lesson_labels.items()):
                specs[code] = {
                    "unit": unit,
                    "lesson_label": lesson_label,
                    "reading_cell": reading_cell,
                    "reading_title": reading_titles[position] if position < len(reading_titles) else "",
                    "lesson_position": position,
                    "lesson_count": len(lesson_labels),
                }
    return specs


def reading_candidate_title(rows, idx, reading_cell=""):
    row = rows[idx]
    title = clean_display_title(row.get("title"))
    if not good_lesson_label(title):
        return None, []
    page = first_page(row)
    depth = row.get("depth")
    pieces = [(title, idx)]
    content = clean_text(row.get("content"))
    last_token = (normalize(title).split() or [""])[-1]
    can_extend = title.endswith(":") or not content or last_token in SUPPORT_TOKEN_STOPWORDS
    if can_extend:
        for next_idx in range(idx + 1, min(len(rows), idx + 6)):
            if len(pieces) >= 4:
                break
            nxt = rows[next_idx]
            next_type = nxt.get("type")
            if nxt.get("type") == "text":
                next_title = clean_display_title(nxt.get("title"))
            elif nxt.get("type") == "image":
                next_title = clean_display_title(nxt.get("content"))
            else:
                continue
            if page is not None and first_page(nxt) != page:
                continue
            next_depth = nxt.get("depth")
            if isinstance(depth, int) and isinstance(next_depth, int) and abs(next_depth - depth) > 1:
                continue
            if not good_lesson_label(next_title):
                if next_type == "image":
                    continue
                break
            if next_type == "image":
                combined = clean_text(" ".join(piece for piece, _ in pieces + [(next_title, next_idx)]))
                if not title_supported_by_reading_cell(combined, reading_cell):
                    continue
            pieces.append((next_title, next_idx))

    best = None
    for end in range(1, len(pieces) + 1):
        candidate = clean_text(" ".join(piece for piece, _ in pieces[:end]))
        if len(normalize(candidate).split()) < 2 and end == 1:
            continue
        if title_supported_by_reading_cell(candidate, reading_cell):
            best = (candidate, [item_idx for _, item_idx in pieces[:end]])
    if best:
        return best
    if not title.endswith(":"):
        return None, []
    for next_idx in range(idx + 1, min(len(rows), idx + 5)):
        nxt = rows[next_idx]
        if nxt.get("type") != "text":
            continue
        if page is not None and first_page(nxt) != page:
            continue
        next_depth = nxt.get("depth")
        if isinstance(depth, int) and isinstance(next_depth, int) and abs(next_depth - depth) > 1:
            continue
        next_title = clean_display_title(nxt.get("title"))
        if not good_lesson_label(next_title):
            continue
        combined = clean_text(f"{title} {next_title}")
        if title_supported_by_reading_cell(combined, reading_cell):
            return combined, [idx, next_idx]
        break
    return title, [idx]


def reading_anchor_row(rows, lesson_start, lesson_end, last_front, title):
    title_key = normalize(title)
    if not title_key:
        return None, []
    for idx, row in enumerate(rows):
        if row.get("type") != "text":
            continue
        page = first_page(row)
        if page is None or page <= last_front:
            continue
        if isinstance(lesson_start, int):
            if page < lesson_start:
                continue
            if isinstance(lesson_end, int) and page >= lesson_end:
                continue
        row_title = clean_display_title(row.get("title"))
        if not row_title:
            continue
        if normalize(row_title) == title_key or title_supported_by_reading_cell(row_title, title):
            return row, [idx]
    return None, []


def collect_toc_reading_topic_entries(rows, lesson_entries, last_front):
    reading_specs = collect_toc_reading_cells(rows, last_front)
    if not reading_specs:
        return []
    lesson_by_code = {}
    lesson_starts = []
    for entry in lesson_entries:
        code = lesson_code_label(entry.get("title")) or lesson_code_label(entry.get("anchor_title"))
        if code and code in reading_specs:
            current = lesson_by_code.get(code)
            if current is None or better_lesson_title(entry.get("title"), current.get("title")):
                lesson_by_code[code] = entry
            if isinstance(entry.get("start_page"), int):
                lesson_starts.append((entry["start_page"], code))
    lesson_bounds = {}
    lesson_starts = sorted(set(lesson_starts), key=lambda item: (item[0], item[1]))
    for idx, (start, code) in enumerate(lesson_starts):
        end = None
        for next_start, next_code in lesson_starts[idx + 1:]:
            if next_start > start and next_code != code:
                end = next_start
                break
        lesson_bounds[code] = (start, end)
    entries = []
    used_row_indexes = set()
    seen = set()
    found_titles_by_reading_cell = {}
    pending_fallbacks = []
    for code, lesson in sorted(lesson_by_code.items(), key=lambda item: (item[1].get("start_page") or 10**9, item[0])):
        spec = reading_specs.get(code) or {}
        reading_cell = spec.get("reading_cell") or ""
        lesson_label_key = normalize(spec.get("lesson_label") or "")
        lesson_start, lesson_end = lesson_bounds.get(code, (lesson.get("start_page"), None))
        found_for_lesson = False
        for idx, row in enumerate(rows):
            if found_for_lesson:
                break
            if idx in used_row_indexes or row.get("type") != "text":
                continue
            page = first_page(row)
            if page is None or page <= last_front:
                continue
            in_lesson_page_range = (
                isinstance(lesson_start, int)
                and page >= lesson_start
                and (not isinstance(lesson_end, int) or page < lesson_end)
            )
            if isinstance(lesson_start, int):
                if not in_lesson_page_range:
                    continue
            elif not row_has_lesson_code(row, code):
                continue
            title, consumed = reading_candidate_title(rows, idx, reading_cell=reading_cell)
            if not title:
                continue
            title_key = normalize(title)
            if not title_key or title_key == lesson_label_key:
                continue
            if lesson_code_label(title):
                continue
            if not title_supported_by_reading_cell(title, reading_cell):
                continue
            key = (lesson_parent_key(lesson.get("title")), title_key)
            if key in seen:
                continue
            seen.add(key)
            used_row_indexes.update(consumed)
            entries.append({
                "title": title,
                "kind": "topic",
                "source": "toc_reading_body_heading",
                "parent_title": clean_text(lesson.get("title")),
                "anchor_title": clean_display_title(row.get("title")),
                "match_titles": [clean_display_title(rows[item].get("title")) for item in consumed if clean_display_title(rows[item].get("title"))],
                "start_page": page,
                "start_page_idx": page - 1,
                "depth": row.get("depth"),
                "popo_level": row.get("level"),
                "children_count": row.get("children_count") or 0,
                "block_ids": row.get("block_ids") or [],
                "level": 3,
                "toc_reading_cell": reading_cell,
            })
            found_titles_by_reading_cell.setdefault(reading_cell, []).append(title)
            found_for_lesson = True
        if not found_for_lesson:
            pending_fallbacks.append({
                "code": code,
                "lesson": lesson,
                "spec": spec,
                "lesson_start": lesson_start,
                "lesson_end": lesson_end,
            })
    for item in pending_fallbacks:
        lesson = item["lesson"]
        spec = item["spec"]
        reading_cell = spec.get("reading_cell") or ""
        known_titles = found_titles_by_reading_cell.get(reading_cell) or []
        title = clean_display_title(spec.get("reading_title") or "") or infer_missing_reading_title(reading_cell, known_titles)
        if not title:
            continue
        title_key = normalize(title)
        lesson_label_key = normalize(spec.get("lesson_label") or "")
        if not title_key or title_key == lesson_label_key:
            continue
        key = (lesson_parent_key(lesson.get("title")), title_key)
        if key in seen:
            continue
        row, consumed = reading_anchor_row(rows, item["lesson_start"], item["lesson_end"], last_front, title)
        page = first_page(row) if row else item["lesson_start"]
        if page is None:
            continue
        seen.add(key)
        used_row_indexes.update(consumed)
        entries.append({
            "title": title,
            "kind": "topic",
            "source": "toc_reading_declared_topic",
            "parent_title": clean_text(lesson.get("title")),
            "anchor_title": clean_display_title(row.get("title")) if row else title,
            "match_titles": [clean_display_title(rows[idx].get("title")) for idx in consumed if clean_display_title(rows[idx].get("title"))] if consumed else [title],
            "start_page": page,
            "start_page_idx": page - 1 if isinstance(page, int) else None,
            "depth": row.get("depth") if row else None,
            "popo_level": row.get("level") if row else None,
            "children_count": row.get("children_count") if row else 0,
            "block_ids": row.get("block_ids") if row else [],
            "level": 3,
            "toc_reading_cell": reading_cell,
            "declared_from_toc_reading_cell": True,
        })
    return entries


def collect_flat_lesson_entries(root, parent_entries, last_front):
    parent_by_unit = unit_titles_by_number(parent_entries)
    unit_ranges = unit_page_ranges_by_number(parent_entries)
    if not parent_by_unit:
        return []
    blocks = flat_lesson_blocks(root)
    if not blocks:
        return []
    title_by_code = {}
    candidate_pages = {}
    for block in blocks:
        page_idx = block.get("page_idx")
        if not isinstance(page_idx, int):
            continue
        page = page_idx + 1
        text = clean_display_title(block.get("text"))
        if not text:
            continue
        match = lesson_code_match(text)
        if match:
            code = lesson_code_label(text)
            unit = int(match.group(1))
            if unit not in parent_by_unit:
                continue
            unit_start, unit_end = unit_ranges.get(unit, (None, None))
            if isinstance(unit_start, int) and page < unit_start:
                continue
            if isinstance(unit_end, int) and page >= unit_end:
                continue
            if block.get("text_level") is not None and re.fullmatch(r"\d{1,2}[A-Z]", text, re.I):
                candidate_pages.setdefault(code, page)
            if block.get("type") in {"header", "footer", "text"}:
                rest = clean_display_title(match.group(3) or "")
                if rest and good_lesson_label(rest):
                    title_by_code.setdefault(code, f"{code} {rest}")
                    candidate_pages.setdefault(code, page)
    entries = []
    for code, page in sorted(candidate_pages.items(), key=lambda item: (item[1], item[0])):
        unit_match = re.match(r"^(\d{1,2})[A-Z]$", code, re.I)
        if not unit_match:
            continue
        unit = int(unit_match.group(1))
        parent_title = parent_by_unit.get(unit)
        if not parent_title:
            continue
        title = title_by_code.get(code) or code
        entries.append({
            "title": title,
            "kind": "lesson",
            "source": "flat_lesson_heading",
            "parent_title": parent_title,
            "anchor_title": code,
            "match_titles": [code],
            "start_page": page,
            "start_page_idx": page - 1,
            "children_count": 0,
            "level": 2,
        })
    return entries


def merge_match_titles(existing, replacement):
    values = []
    for entry in (existing, replacement):
        for value in entry.get("match_titles") or []:
            value = clean_text(value)
            if value and value not in values:
                values.append(value)
    return values


def better_lesson_title(candidate, current):
    candidate = clean_display_title(candidate)
    current = clean_display_title(current)
    if not candidate:
        return False
    if not current:
        return True
    if lesson_code_label(candidate) != lesson_code_label(current):
        return False
    return len(normalize(candidate)) > len(normalize(current)) + 3


def merge_body_fallback(resolved, rows, last_front, root=None):
    merged = []
    for idx, entry in enumerate(resolved):
        item = dict(entry)
        item["_toc_seq"] = idx
        merged.append(item)
    fallback = fallback_entries(rows, last_front)
    lesson_entries = collect_lesson_entries(rows, merged, last_front)
    flat_lessons = collect_flat_lesson_entries(root, merged, last_front) if root else []
    reading_topics = collect_toc_reading_topic_entries(rows, lesson_entries + flat_lessons, last_front)
    reading_topic_keys_by_parent = {}
    for topic in reading_topics:
        parent_key = lesson_parent_key(topic.get("parent_title"))
        if parent_key:
            reading_topic_keys_by_parent.setdefault(parent_key, set()).add(entry_identity(topic) or normalize(topic.get("title")))
    has_reliable_contents = any(entry.get("source") in {"contents", "contents_detail"} for entry in merged)
    reliable_top_pages = [
        entry.get("start_page")
        for entry in merged
        if entry.get("source") in {"contents", "contents_detail"}
        and entry.get("kind") in {"part", "chapter", "unit", "primary"}
        and isinstance(entry.get("start_page"), int)
    ]
    first_reliable_top_page = min(reliable_top_pages) if reliable_top_pages else None
    fallback_by_id = {}
    for entry in fallback:
        key = entry_identity(entry)
        if not key:
            continue
        old = fallback_by_id.get(key)
        if old is None or (entry.get("children_count") or 0, -(entry.get("start_page") or 10**9)) > (old.get("children_count") or 0, -(old.get("start_page") or 10**9)):
            fallback_by_id[key] = entry

    for idx, entry in enumerate(merged):
        key = entry_identity(entry)
        replacement = fallback_by_id.get(key)
        if not replacement or not replacement.get("children_count"):
            continue
        start = entry.get("start_page")
        replacement_start = replacement.get("start_page")
        replace_anchor = (
            not isinstance(start, int)
            or start <= last_front + 5
            or (isinstance(replacement_start, int) and replacement_start > start + 20 and (replacement.get("children_count") or 0))
        )
        replace_title = len(clean_text(entry.get("title"))) > len(clean_text(replacement.get("title"))) + 20
        if replace_anchor or replace_title:
            if (
                entry.get("source") in {"contents", "contents_detail"}
                and replacement.get("source") == "popo_body_heading"
                and isinstance(start, int)
                and isinstance(replacement_start, int)
                and abs(replacement_start - start) > 12
            ):
                continue
            if (
                entry.get("source") in {"contents", "contents_detail"}
                and replacement.get("source") == "popo_structural_heading"
            ):
                continue
            if (
                entry.get("source") in {"contents", "contents_detail"}
                and entry.get("kind") == "unit"
                and replacement.get("source") == "popo_body_heading"
                and re.search(r"\bknowledge\s+test\b", clean_text(replacement.get("title")), re.I)
            ):
                continue
            updated = dict(entry)
            for field in ("title", "anchor_title", "start_page", "start_page_idx", "depth", "popo_level", "children_count", "block_ids"):
                if field == "title" and entry.get("kind") in {"part", "chapter", "unit"} and replacement.get("source") == "popo_structural_heading":
                    continue
                if (
                    field == "title"
                    and entry.get("kind") in {"part", "chapter", "unit"}
                    and (
                        replacement.get("kind") == "topic"
                        or re.match(r"^chapter\s+[A-Z]?\d+\.\s*topic\s+\d+\b", clean_text(replacement.get("title")), re.I)
                    )
                ):
                    continue
                if field == "title" and entry.get("source") in {"contents", "contents_detail"} and replacement.get("source") == "popo_body_heading":
                    continue
                if field in replacement:
                    updated[field] = replacement[field]
            updated["source"] = replacement.get("source") or updated.get("source")
            updated["anchor_method"] = "body_structure_replacement"
            merged[idx] = updated

    seen_index = {entry_identity(entry): idx for idx, entry in enumerate(merged) if entry_identity(entry)}
    seen = set(seen_index)
    has_part = any(entry.get("kind") == "part" for entry in merged)
    for entry in fallback + lesson_entries + reading_topics + flat_lessons:
        key = entry_identity(entry)
        if not key:
            continue
        if entry.get("source") == "popo_body_lesson_child":
            parent_key = lesson_parent_key(entry.get("parent_title"))
            supported_keys = reading_topic_keys_by_parent.get(parent_key)
            if supported_keys and key not in supported_keys:
                continue
        if key in seen:
            existing_idx = seen_index.get(key)
            if (
                existing_idx is not None
                and entry.get("kind") == "lesson"
                and better_lesson_title(entry.get("title"), merged[existing_idx].get("title"))
            ):
                updated = dict(merged[existing_idx])
                old_title = clean_text(updated.get("title"))
                updated["title"] = entry.get("title")
                updated["match_titles"] = merge_match_titles(updated, entry)
                if old_title and old_title not in updated["match_titles"]:
                    updated["match_titles"].append(old_title)
                updated["title_completed_from"] = entry.get("source")
                merged[existing_idx] = updated
            continue
        if has_reliable_contents and entry.get("source") == "popo_body_heading":
            continue
        if (
            has_reliable_contents
            and entry.get("source") == "popo_structural_heading"
            and isinstance(first_reliable_top_page, int)
            and isinstance(entry.get("start_page"), int)
            and entry["start_page"] < first_reliable_top_page
        ):
            continue
        if has_part and entry.get("kind") == "unit":
            entry["level"] = 2
        if has_part and entry.get("kind") == "part" and entry.get("source") == "popo_body_heading":
            entry["kind"] = "topic"
            entry["level"] = 3
        merged.append(entry)
        seen_index[key] = len(merged) - 1
        seen.add(key)
    merged = fill_unanchored_parent_pages(ensure_topic_parents(merged))
    parent_seq_by_title = {
        clean_text(entry.get("title")): entry.get("_toc_seq")
        for entry in merged
        if isinstance(entry.get("_toc_seq"), int)
    }
    child_counts_by_parent = {}
    for entry in sorted(
        merged,
        key=lambda item: (
            item.get("start_page") if isinstance(item.get("start_page"), int) else 10**9,
            item.get("title") or "",
        ),
    ):
        if isinstance(entry.get("_toc_seq"), (int, float)):
            continue
        parent_title = clean_text(entry.get("parent_title"))
        parent_seq = parent_seq_by_title.get(parent_title)
        if not isinstance(parent_seq, int):
            continue
        child_counts_by_parent[parent_title] = child_counts_by_parent.get(parent_title, 0) + 1
        entry["_toc_seq"] = parent_seq + child_counts_by_parent[parent_title] / 1000.0
    kind_order = {"part": 0, "chapter": 0, "unit": 0, "primary": 0, "lesson": 1, "category": 1, "topic": 2}

    def merged_order_key(entry):
        if isinstance(entry.get("_toc_seq"), (int, float)):
            return (0, entry["_toc_seq"])
        return (
            1,
            entry.get("start_page") if isinstance(entry.get("start_page"), int) else 10**9,
            kind_order.get(entry.get("kind"), 3),
            entry.get("level", 3),
        )

    merged = sorted(
        merged,
        key=merged_order_key,
    )
    for entry in merged:
        entry.pop("_toc_seq", None)
    return merged


def entry_identity(entry):
    title = clean_text(entry.get("title"))
    if not title:
        return ""
    if entry.get("kind") == "lesson":
        lesson_code = lesson_code_label(title) or lesson_code_label(entry.get("anchor_title"))
        parent = clean_text(entry.get("parent_title"))
        if lesson_code:
            return f"lesson:{parent or lesson_unit_number(title) or ''}:{lesson_code}"
    chapter_no = clean_text(entry.get("chapter_number"))
    if chapter_no:
        return f"chapter:{chapter_no.upper()}"
    match = re.match(r"^第\s*([一二三四五六七八九十百千万0-9]+)\s*章\s*复习\s*[\(（]?\s*([0-9一二三四五六七八九十]+)\s*[\)）]?", title)
    if match:
        return f"cn-chapter-review:{match.group(1)}:{match.group(2)}"
    match = re.match(r"^section\s+(\d+)\s*[-–—]?\s+review\b", title, re.I)
    if match:
        return f"section-review:{int(match.group(1))}"
    match = re.match(r"^chapter\s+([A-Z]?\d+)\.\s*topic\s+(\d+)\b", title, re.I)
    if match:
        return f"chapter-topic:{match.group(1).upper()}:{int(match.group(2))}"
    match = re.match(r"^topic\s+(\d+)\b", title, re.I)
    if match:
        parent = clean_text(entry.get("parent_title"))
        if parent:
            return f"topic:{entry_identity({'title': parent}) or normalize(parent)}:{int(match.group(1))}"
        return f"topic:{int(match.group(1))}:{normalize(title)}"
    match = re.match(r"^unit\s+(\d+)\b", title, re.I)
    if match:
        return f"unit:{int(match.group(1))}"
    match = re.match(r"^(?:exercise\s+)?([A-Z]\d+(?:\.\d+)+)\b", title, re.I)
    if match:
        prefix = match.group(1).split(".")[0].upper()
        return f"letter-topic:{prefix}:{match.group(1).upper()}"
    match = re.match(r"^(?:chapter\s+)?([A-Z]\d+)\b", title, re.I)
    if match:
        return f"chapter:{match.group(1).upper()}"
    match = re.match(r"^chapter\s+([A-Z]?\d+)\b", title, re.I)
    if match:
        return f"chapter:{match.group(1).upper()}"
    match = re.match(r"^第\s*([一二三四五六七八九十百千万0-9]+)\s*([章节课单元篇])", title)
    if match:
        return f"cn:{match.group(2)}:{match.group(1)}"
    match = re.match(r"^part\s+(\d+)\b", title, re.I)
    if match:
        return f"part:{int(match.group(1))}"
    match = re.match(r"^section\s+(\d+)\b", title, re.I)
    if match:
        return f"section:{int(match.group(1))}"
    return normalize(title)


def numbered_topic_unit(title):
    title = clean_text(title)
    match = re.match(r"^(\d+)\.\d+\b", title)
    if match:
        return int(match.group(1))
    match = lesson_code_match(title)
    return int(match.group(1)) if match else None


def bare_numbered_parent_number(title):
    match = re.match(r"^(\d{1,2})\s+\S+", clean_text(title))
    return int(match.group(1)) if match else None


def letter_topic_parent_key(title):
    match = re.match(r"^(?:exercise\s+)?([A-Z]\d+)(?:\.\d+)+\b", clean_text(title), re.I)
    return match.group(1).upper() if match else None


def unit_number(title):
    match = re.match(r"^unit\s*(\d+)\b", clean_text(title), re.I)
    return int(match.group(1)) if match else None


def chapter_number(title):
    match = re.match(r"^chapter\s+([A-Z]?\d+)\b", clean_text(title), re.I)
    if not match:
        match = re.match(r"^[A-Z]\d+\s+(\d+)\b", clean_text(title), re.I)
    if not match:
        return None
    value = match.group(1).upper()
    return value


def topic_parent_matches(entry, parent):
    if parent.get("kind") == "lesson":
        parent_key = entry_identity(parent)
        wanted = entry_identity({"kind": "lesson", "title": entry.get("parent_title"), "parent_title": parent.get("parent_title")})
        return bool(parent_key and wanted and parent_key == wanted) or normalize(entry.get("parent_title")) == normalize(parent.get("title"))
    topic_letter_parent = letter_topic_parent_key(entry.get("title"))
    if topic_letter_parent:
        parent_ident = entry_identity(parent)
        return parent_ident == f"chapter:{topic_letter_parent}" or topic_letter_parent in clean_text(parent.get("title")).upper()
    topic_unit = numbered_topic_unit(entry.get("title"))
    if topic_unit is None:
        return True
    parent_unit = unit_number(parent.get("title"))
    if parent_unit is not None:
        return parent_unit == topic_unit
    parent_chapter = chapter_number(parent.get("title"))
    if parent_chapter is not None and parent_chapter.isdigit():
        return int(parent_chapter) == topic_unit
    return True


def topic_category_matches(entry, category):
    wanted = normalize(entry.get("category_title"))
    if not wanted:
        return False
    if wanted != normalize(category.get("title")):
        return False
    category_parent = clean_text(category.get("parent_title"))
    entry_parent = clean_text(entry.get("parent_title"))
    return not category_parent or not entry_parent or category_parent == entry_parent


def parent_chapter_number(parent_title):
    match = re.match(r"^chapter\s+([A-Z]?\d+)\b", clean_text(parent_title), re.I)
    return match.group(1).upper() if match else None


def row_matches_topic_parent(entry, row):
    parent = clean_text(entry.get("parent_title"))
    if not parent:
        return True
    path_text = clean_text(row.get("path_text"))
    parent_no = bare_numbered_parent_number(parent)
    topic_no = numbered_topic_unit(entry.get("title"))
    if parent_no is not None and topic_no == parent_no:
        row_title_no = numbered_topic_unit(row.get("title")) or numbered_topic_unit(row.get("content"))
        if row_title_no == parent_no:
            return True
        parent_without_number = clean_text(re.sub(r"^\d{1,2}\s+", "", parent))
        parent_key = normalize(parent_without_number)
        if parent_key and parent_key in normalize(path_text):
            return True
    parent_unit = unit_number(parent)
    if parent_unit is not None:
        if unit_number(row.get("title")) == parent_unit or unit_number(row.get("content")) == parent_unit:
            return True
        return bool(re.search(rf"\bunit\s+{parent_unit}\b", path_text, re.I))
    chapter = parent_chapter_number(parent)
    if chapter:
        return bool(re.search(rf"\bchapter\s+{re.escape(chapter)}\b", path_text, re.I))
    parent_key = normalize(parent)
    return not parent_key or parent_key in normalize(path_text)


def display_title_from_anchor(entry, anchor_title):
    title = clean_display_title(entry.get("title"))
    anchor_title = clean_display_title(anchor_title)
    if not title or not anchor_title:
        return title
    if entry.get("kind") == "primary" and entry.get("inter_unit_module"):
        title_key = normalize(title)
        anchor_key = normalize(anchor_title)
        if anchor_key and (anchor_key == title_key or anchor_key.startswith(title_key + " ") or title_key.endswith(anchor_key)):
            return anchor_title
        if ":" in title and normalize(title.split(":", 1)[1]) == anchor_key:
            return anchor_title
    return title


def is_duplicate_unit_detail(entry, previous):
    if entry.get("source") != "contents_detail" or entry.get("kind") != "topic":
        return False
    if not previous or previous.get("kind") != "unit":
        return False
    return normalize(entry.get("title")) == normalize(previous.get("title"))


def canonicalize_outline(entries):
    protected_parent_titles = {
        normalize(entry.get("title"))
        for entry in entries
        if entry.get("kind") in {"part", "chapter", "unit"} and entry.get("source") != "popo_structural_heading"
    }
    protected_nonstructural_titles = {
        normalize(entry.get("title"))
        for entry in entries
        if entry.get("source") != "popo_structural_heading"
    }
    top_pages = [
        entry.get("start_page")
        for entry in entries
        if entry.get("source") != "popo_structural_heading"
        and entry.get("kind") in {"part", "chapter", "unit"}
        and isinstance(entry.get("start_page"), int)
    ]
    first_nonstructural_top_page = min(top_pages) if top_pages else None
    sequenced = []
    for seq, entry in enumerate(entries):
        if entry.get("source") == "popo_structural_heading" and normalize(entry.get("title")) in protected_parent_titles:
            continue
        if entry.get("source") == "popo_structural_heading" and normalize(entry.get("title")) in protected_nonstructural_titles:
            continue
        if (
            entry.get("source") == "popo_structural_heading"
            and first_nonstructural_top_page is not None
            and isinstance(entry.get("start_page"), int)
            and entry.get("start_page") > first_nonstructural_top_page
        ):
            continue
        item = dict(entry)
        item["_seq"] = seq
        sequenced.append(item)
    best_by_identity = {}
    for item in sequenced:
        if item.get("kind") not in {"part", "chapter", "unit"}:
            continue
        ident = entry_identity(item)
        if not ident:
            continue
        old = best_by_identity.get(ident)
        if old is None:
            best_by_identity[ident] = item
            continue
        def rank(entry):
            title = clean_text(entry.get("title"))
            source = entry.get("source") or ""
            return (
                3 if source == "contents" else 2 if source == "synthetic_parent_from_topic" else 1,
                0 if re.search(r"\bknowledge\s+test\b|section\s+\d+\s+review", title, re.I) else 1,
                1 if isinstance(entry.get("start_page"), int) else 0,
                -len(title),
            )
        if rank(item) > rank(old):
            best_by_identity[ident] = item
    sequenced = [
        item for item in sequenced
        if item.get("kind") not in {"part", "chapter", "unit"}
        or best_by_identity.get(entry_identity(item)) is item
    ]
    top_kinds = {entry.get("kind") for entry in sequenced if entry.get("kind") in {"part", "chapter", "unit"}}
    unit_only_outline = "unit" in top_kinds and "part" not in top_kinds and "chapter" not in top_kinds
    parent_seq_by_title = {
        clean_text(entry.get("title")): entry.get("_seq")
        for entry in sequenced
        if entry.get("kind") in {"part", "chapter", "unit", "primary", "lesson", "category"}
    }
    child_counts_by_parent = {}
    for entry in sorted(
        sequenced,
        key=lambda item: (
            item.get("start_page") if isinstance(item.get("start_page"), int) else 10**9,
            item.get("_seq", 0),
        ),
    ):
        parent_title = clean_text(entry.get("parent_title"))
        parent_seq = parent_seq_by_title.get(parent_title)
        if not isinstance(parent_seq, int):
            continue
        child_counts_by_parent[parent_title] = child_counts_by_parent.get(parent_title, 0) + 1
        entry["_parent_seq"] = parent_seq + child_counts_by_parent[parent_title] / 1000.0

    def outline_order_key(entry):
        if isinstance(entry.get("_parent_seq"), float):
            return (0, entry["_parent_seq"])
        if entry.get("source") in {"contents", "contents_detail", "contents_category"}:
            return (0, entry.get("_seq", 0))
        return (
            1,
            entry.get("start_page") if isinstance(entry.get("start_page"), int) else 10**9,
            entry.get("level", 3),
            entry.get("_seq", 0),
        )

    ordered = sorted(sequenced, key=outline_order_key)
    out = []
    current_h1 = None
    current_h2 = None
    seen = set()
    for entry in ordered:
        if is_duplicate_unit_detail(entry, out[-1] if out else None):
            continue
        title = clean_text(entry.get("title"))
        key = (entry.get("kind"), normalize(title), entry.get("start_page"), clean_text(entry.get("parent_title")))
        if key in seen:
            continue
        seen.add(key)

        kind = entry.get("kind")
        level = int(entry.get("level") or 3)
        if entry.get("source") == "popo_tree_hierarchy_fallback":
            level = max(1, min(3, level))
            if level == 1:
                current_h1 = entry
                current_h2 = None
            elif level == 2:
                current_h2 = entry
            entry["level"] = level
            entry.pop("_seq", None)
            out.append(entry)
            continue
        if kind == "primary":
            level = 1
            current_h1 = entry
            current_h2 = None
        elif kind == "part":
            level = 1
            current_h1 = entry
            current_h2 = None
        elif kind == "chapter":
            if "part" in top_kinds and current_h1:
                level = 2
                current_h2 = entry
            else:
                level = 1
                current_h1 = entry
                current_h2 = None
        elif kind == "unit":
            if unit_only_outline:
                level = 1
                current_h1 = entry
                current_h2 = None
            elif current_h1:
                level = 2
                current_h2 = entry
            else:
                level = 1
                current_h1 = entry
                current_h2 = None
        elif kind == "lesson":
            if current_h1:
                level = 2
                current_h2 = entry
            else:
                level = 1
                current_h1 = entry
                current_h2 = None
        elif kind == "category":
            if current_h1:
                level = 2
                current_h2 = entry
            else:
                level = 1
                current_h1 = entry
                current_h2 = None
        elif kind == "topic":
            if unit_only_outline:
                parent = current_h2 if current_h2 and (
                    (current_h2.get("kind") == "category" and topic_category_matches(entry, current_h2))
                    or (current_h2.get("kind") == "lesson" and topic_parent_matches(entry, current_h2))
                ) else current_h1
            else:
                parent = current_h2 if current_h2 and topic_parent_matches(entry, current_h2) else current_h1
            if parent is None:
                level = 1
                current_h1 = entry
                current_h2 = None
            elif parent is current_h2:
                level = 3
            else:
                level = min(3, int(parent.get("level") or 1) + 1)
        else:
            if level > 1 and current_h1 is None:
                level = 1
                current_h1 = entry

        entry["level"] = max(1, min(3, level))
        entry.pop("_seq", None)
        out.append(entry)
    return out


def matching_section_intro_page(rows, section_title, child_page, last_front):
    match = re.match(r"^section\s+(\d+)\b", clean_text(section_title), re.I)
    if not match or not isinstance(child_page, int):
        return None
    section_no = match.group(1)
    candidates = []
    for row in rows:
        page = first_page(row)
        if page is None or page <= last_front or page >= child_page:
            continue
        if str(row.get("type") or "").lower() != "header":
            continue
        content = clean_text(row.get("content"))
        title = clean_text(row.get("title"))
        if re.fullmatch(rf"section\s+{re.escape(section_no)}", content, re.I) or re.fullmatch(rf"section\s+{re.escape(section_no)}", title, re.I):
            candidates.append(page)
    return max(candidates) if candidates else None


def adjust_section_parent_start_pages(entries, rows, last_front):
    out = [dict(entry) for entry in entries]
    for idx, entry in enumerate(out):
        if entry.get("kind") != "part" or not re.match(r"^section\s+\d+\b", clean_text(entry.get("title")), re.I):
            continue
        current_start = entry.get("start_page")
        next_child_page = None
        entry_level = int(entry.get("level") or 1)
        for child in out[idx + 1:]:
            child_level = int(child.get("level") or 3)
            if child_level <= entry_level:
                break
            if isinstance(child.get("start_page"), int):
                next_child_page = child["start_page"]
                break
        if not isinstance(next_child_page, int):
            continue
        intro_page = matching_section_intro_page(rows, entry.get("title"), next_child_page, last_front)
        if isinstance(intro_page, int) and (not isinstance(current_start, int) or intro_page < current_start):
            entry["start_page"] = intro_page
            entry["start_page_idx"] = intro_page - 1
            entry["anchor_title"] = entry.get("title")
            entry["anchor_method"] = "section_intro_header_anchor"
    return out


def adjust_child_anchors_to_parent_ranges(entries, rows, last_front):
    out = [dict(entry) for entry in entries]
    parents = []
    for idx, entry in enumerate(out):
        if entry.get("kind") not in {"part", "chapter", "unit", "primary", "lesson"}:
            continue
        start = entry.get("start_page")
        level = entry.get("level")
        if not isinstance(start, int) or not isinstance(level, int):
            continue
        parents.append((idx, entry, start, level))

    def find_parent(child):
        parent_title = clean_text(child.get("parent_title"))
        if not parent_title:
            return None
        parent_key = entry_identity({"title": parent_title}) or normalize(parent_title)
        best = None
        for idx, entry, start, level in parents:
            key = entry_identity(entry) or normalize(entry.get("title"))
            if key and parent_key:
                parent_matches = key == parent_key
            else:
                parent_matches = normalize(entry.get("title")) == normalize(parent_title)
            if not parent_matches:
                continue
            end = None
            for other_idx, other, other_start, other_level in parents:
                if other_idx <= idx or other_start <= start or other_level > level:
                    continue
                if end is None or other_start < end:
                    end = other_start
            best = (entry, start, end)
            break
        return best

    drop_indexes = set()
    for idx, child in enumerate(out):
        if child.get("kind") != "topic" or not child.get("parent_title"):
            continue
        parent = find_parent(child)
        if not parent:
            continue
        parent_entry, parent_start, parent_end = parent
        start = child.get("start_page")
        in_parent_range = (
            isinstance(start, int)
            and start >= parent_start
            and (parent_end is None or start < parent_end)
        )
        needs_precise_anchor = (
            child.get("source") == "contents_detail"
            and not child.get("block_ids")
            and child.get("anchor_method") in {"printed_page_offset", "contents_unanchored"}
        )
        if in_parent_range and not needs_precise_anchor:
            continue
        target = normalize(child.get("title"))
        if not target:
            continue
        candidates = []
        for row in rows:
            page = first_page(row)
            if page is None or page <= last_front or page < parent_start:
                continue
            if parent_end is not None and page >= parent_end:
                continue
            title = row_match_title(row)
            key = normalize(title)
            if not key:
                continue
            exact = title.lower() == clean_text(child.get("title")).lower()
            direct = key == target
            if not exact and not direct:
                continue
            candidates.append((
                (
                    1 if exact else 0,
                    1 if classify_title(title) == child.get("kind") else 0,
                    row.get("children_count") or 0,
                    -abs(page - parent_start),
                    -row.get("depth", 0),
                ),
                row,
            ))
        if not candidates:
            if in_parent_range and needs_precise_anchor:
                continue
            drop_indexes.add(idx)
            continue
        row = max(candidates, key=lambda item: item[0])[1]
        page = first_page(row)
        updated = dict(child)
        updated["anchor_title"] = row_match_title(row)
        updated["start_page"] = page
        updated["start_page_idx"] = page - 1 if page is not None else None
        updated["depth"] = row.get("depth")
        updated["popo_level"] = row.get("level")
        updated["block_ids"] = row.get("block_ids") or []
        updated["anchor_method"] = "parent_range_title_match"
        updated["anchor_parent_title"] = clean_text(parent_entry.get("title"))
        out[idx] = updated
    if drop_indexes:
        out = [entry for idx, entry in enumerate(out) if idx not in drop_indexes]
    return out


def build_outline(root):
    root = Path(root)
    path = tree_path(root)
    if not path:
        return {"available": False, "reason": "missing_popo_document_tree"}
    tree = load_json(path)
    rows = list(walk_tree(tree))
    last_front = front_last_page(rows)
    flat_contents_entries = collect_flat_contents_entries(root)
    markdown_contents_entries = collect_markdown_contents_entries(root)
    row_contents_entries = collect_contents_entries(rows)
    if flat_contents_entries:
        markdown_contents_entries = remove_duplicate_detail_entries(flat_contents_entries, markdown_contents_entries)
    if flat_contents_entries or markdown_contents_entries:
        row_contents_entries = [
            entry for entry in row_contents_entries
            if entry.get("kind") in {"part", "chapter", "unit"}
        ]
    contents_entries = dedupe_entries(flat_contents_entries + markdown_contents_entries + row_contents_entries)
    printed_page_offset = infer_printed_page_offset(contents_entries, rows, last_front)
    resolved = []
    if contents_entries:
        for entry in contents_entries:
            container_without_direct_anchor = (
                entry.get("kind") == "category"
                or (
                    entry.get("kind") in {"part", "chapter", "unit"}
                    and entry.get("source") == "contents"
                    and not str(entry.get("printed_page") or "").isdigit()
                )
            )
            anchor = None if container_without_direct_anchor else match_anchor(entry, rows, last_front, printed_page_offset=printed_page_offset)
            item = dict(entry)
            if anchor:
                anchor_title = row_match_title(anchor)
                item["title"] = display_title_from_anchor(item, anchor_title)
                item["anchor_title"] = anchor_title
                item["start_page"] = first_page(anchor)
                item["start_page_idx"] = first_page(anchor) - 1 if first_page(anchor) is not None else None
                item["depth"] = anchor.get("depth")
                item["popo_level"] = anchor.get("level")
                item["block_ids"] = anchor.get("block_ids") or []
                item["anchor_method"] = "title_match_after_front"
            elif str(entry.get("printed_page") or "").isdigit() and isinstance(printed_page_offset, int):
                start_page = int(entry["printed_page"]) + printed_page_offset
                item["anchor_title"] = item["title"]
                item["start_page"] = start_page
                item["start_page_idx"] = start_page - 1
                item["anchor_method"] = "printed_page_offset"
            elif entry.get("kind") in {"part", "chapter", "unit", "category"}:
                item["anchor_title"] = item["title"]
                item["start_page"] = None
                item["start_page_idx"] = None
                item["anchor_method"] = "contents_unanchored"
            else:
                continue
            resolved.append(item)
        resolved = merge_body_fallback(fill_parent_pages_from_printed(fill_unanchored_parent_pages(infer_hierarchy(resolved))), rows, last_front, root=root)
        resolved = adjust_child_anchors_to_parent_ranges(resolved, rows, last_front)
    else:
        resolved = tree_hierarchy_fallback(rows, last_front) or fill_unanchored_parent_pages(fallback_entries(rows, last_front))

    resolved = adjust_section_parent_start_pages(canonicalize_outline([e for e in resolved if e.get("level", 4) <= 3]), rows, last_front)
    if not resolved:
        primary = fallback_primary_entry(rows, last_front)
        if primary:
            resolved = [primary]
    anchored_pages = [e["start_page"] for e in resolved if isinstance(e.get("start_page"), int)]
    body_start = min(anchored_pages) if anchored_pages else None
    max_outline_start_page = max(anchored_pages) if anchored_pages else None
    body_end = flat_contents_back_start_page(root, printed_page_offset)
    if isinstance(body_end, int):
        body_end -= 1
    for row in rows:
        title = clean_text(row.get("title"))
        content = clean_text(row.get("content"))
        page = first_page(row)
        if body_start and page and page > body_start and (BACK_TITLE_RE.match(title) or (row.get("type") in {"header", "footer"} and BACK_TITLE_RE.match(content))):
            candidate_end = page - 1
            if isinstance(max_outline_start_page, int) and candidate_end < max_outline_start_page:
                continue
            if body_end is None or candidate_end < body_end:
                body_end = candidate_end
            break
    if resolved:
        body_end_idx = body_end - 1 if isinstance(body_end, int) else None
        filtered = []
        for entry in resolved:
            start_idx = entry.get("start_page_idx")
            if not isinstance(start_idx, int):
                continue
            if isinstance(body_end_idx, int) and start_idx > body_end_idx:
                continue
            filtered.append(entry)
        if filtered:
            resolved = filtered
            anchored_pages = [e["start_page"] for e in resolved if isinstance(e.get("start_page"), int)]
            body_start = min(anchored_pages) if anchored_pages else body_start
    return {
        "available": True,
        "tree_file": path.name,
        "row_count": len(rows),
        "last_front_page": last_front,
        "body_start_page": body_start,
        "body_start_page_idx": body_start - 1 if body_start else None,
        "body_end_page": body_end,
        "body_end_page_idx": body_end - 1 if body_end else None,
        "contents_entry_count": len(contents_entries),
        "printed_page_offset": printed_page_offset,
        "outline": resolved,
    }


def main():
    parser = argparse.ArgumentParser(description="Build a max-3-level canonical outline from MinerU-Popo document_tree evidence.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    outline = build_outline(args.root.expanduser().resolve())
    text = json.dumps(outline, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        args.out.expanduser().resolve().write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
