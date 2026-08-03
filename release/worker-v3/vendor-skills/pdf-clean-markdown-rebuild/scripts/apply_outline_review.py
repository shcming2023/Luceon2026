#!/usr/bin/env python3
import argparse
import html
import json
import re
from pathlib import Path

try:
    from outline_anchor_check import write_outline_anchor_check
except Exception:
    write_outline_anchor_check = None


SAFE_DEMOTE_PROMOTE = True


LOCAL_LABEL_HEADING_RE = re.compile(
    r"^(?:"
    r"top\s+tip|study\s+tip[s]?|tip|hint|key\s+term[s]?|vocabulary|"
    r"get\s+started|do\s+you\s+remember\??|practi[cs]e|challenge|self-check|"
    r"review\s+and\s+reflection|unit\s+summary|summary|"
    r"in\s+this\s+(?:topic|unit|chapter),?\s+you\s+will:?|"
    r"you\s+will\s+learn\s+how\s+to|links\s+to\s+other\s+chapters:?|"
    r"explore\s+the\s+skills|build\s+the\s+skills|develop\s+the\s+skills|apply\s+the\s+skills|"
    r"checklist\s+for\s+success|sound\s+progress|excellent\s+progress|"
    r"speaking(?:\s+and\s+listening)?|listening|reading|writing|"
    r"develop\s+language\s+skills|did\s+you\s+know\??|"
    r"reading\s+and\s+writing|pronunciation(?:\s+and\s+spelling)?|"
    r"adjectives:?|nouns:?|question\s+tags|key\s+words?|for\s+example:?|blog|speaker\s+(?:\d+|[A-Z])\b.*|step\s+\d+:?|"
    r"formal:?|informal:?|direct\s+speech:?|reported\s+speech:?|"
    r"[A-E]|word/phrase|explicit\s+meaning|connotations|effect|"
    r"let'?s\s+talk|key\s+information|useful\s+equations|teacher'?s\s+advice|"
    r"notes?|question\s+\d+|section\s+[A-Z](?::.*)?|either|or"
    r")$",
    re.I,
)
INSTRUCTION_HEADING_RE = re.compile(
    r"^\d+\s+(?:copy|write|read|look|listen|complete|choose|match|answer|discuss|explain|identify|find|make)\b",
    re.I,
)
NUMBERED_LIST_HEADING_RE = re.compile(r"^\d+\s+\S")
DECIMAL_LABEL_HEADING_RE = re.compile(r"^\d+\.\d+$")


def render_inline(text):
    rendered = html.escape(text, quote=False)
    for tag in ("sup", "sub", "strong", "em", "b", "i"):
        rendered = rendered.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        rendered = rendered.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return rendered


def markdown_to_html(markdown, title="PDF Clean Markdown Preview"):
    html_lines = [
        "<!doctype html>",
        "<html lang=\"en\">",
        "<head>",
        "<meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        f"<title>{html.escape(title)}</title>",
        "<script>",
        "window.MathJax={tex:{inlineMath:[['$','$'],['\\\\(','\\\\)']],displayMath:[['$$','$$'],['\\\\[','\\\\]']],processEscapes:true},options:{skipHtmlTags:['script','noscript','style','textarea','pre','code']}};",
        "</script>",
        "<script defer src=\"https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js\"></script>",
        "<style>",
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.55;margin:0;background:#f7f7f5;color:#1f2933}",
        "main{max-width:920px;margin:0 auto;padding:32px 20px 72px;background:#fff}",
        "h1{font-size:2rem;margin:2.2rem 0 1rem;border-bottom:2px solid #222;padding-bottom:.35rem}",
        "h2{font-size:1.45rem;margin:1.8rem 0 .8rem}h3{font-size:1.15rem;margin:1.35rem 0 .5rem}h4{font-size:1rem;margin:1rem 0 .35rem}",
        "p{margin:.65rem 0}.page{font-size:.78rem;color:#6b7280;margin:1.2rem 0 .4rem}",
        "img{max-width:100%;height:auto;display:block;margin:1rem 0}.caption{color:#4b5563;font-size:.92rem;margin-top:-.6rem}",
        ".table-wrap{overflow-x:auto;margin:1rem 0}table{border-collapse:collapse;width:100%;font-size:.92rem}td,th{border:1px solid #d0d7de;padding:.35rem .5rem;vertical-align:top}",
        "code{background:#f2f4f7;padding:.1rem .25rem;border-radius:3px}",
        "</style>",
        "</head><body><main>",
    ]
    in_paragraph = False

    def close_paragraph():
        nonlocal in_paragraph
        if in_paragraph:
            html_lines.append("</p>")
            in_paragraph = False

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            close_paragraph()
            continue
        page_match = re.match(r"<!--\s*page_idx:\s*([^>]+?)\s*-->", line)
        if page_match:
            close_paragraph()
            html_lines.append(f"<div class=\"page\">page_idx: {html.escape(page_match.group(1))}</div>")
            continue
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            close_paragraph()
            level = len(heading_match.group(1))
            html_lines.append(f"<h{level}>{render_inline(heading_match.group(2))}</h{level}>")
            continue
        image_match = re.match(r"^!\[(.*?)\]\((.*?)\)$", line)
        if image_match:
            close_paragraph()
            alt, src = image_match.groups()
            html_lines.append(f"<img src=\"{html.escape(src, quote=True)}\" alt=\"{html.escape(alt, quote=True)}\">")
            continue
        if line.startswith("<table"):
            close_paragraph()
            html_lines.append(f"<div class=\"table-wrap\">{line}</div>")
            continue
        caption_match = re.match(r"^\*(.+)\*$", line)
        if caption_match:
            close_paragraph()
            html_lines.append(f"<p class=\"caption\">{render_inline(caption_match.group(1))}</p>")
            continue
        if not in_paragraph:
            html_lines.append("<p>")
            in_paragraph = True
        else:
            html_lines.append("<br>")
        html_lines.append(render_inline(line))

    close_paragraph()
    html_lines.append("</main></body></html>")
    return "\n".join(html_lines) + "\n"


def heading_at(lines, lineno):
    if lineno < 1 or lineno > len(lines):
        return None
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", lines[lineno - 1])
    if not match:
        return None
    return len(match.group(1)), match.group(2)


def normalize_title(text):
    text = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    text = re.sub(r"^[\"'“”‘’]+|[\"'“”‘’]+$", "", text)
    return text


def collect_chapter_unit_titles(lines):
    titles = set()
    for line in lines:
        stripped = re.sub(r"^#{1,6}\s+", "", line.strip())
        match = re.match(r"^Unit\s+\d+\s+(.+?)\s*$", stripped, re.I)
        if match:
            title = normalize_title(match.group(1))
            if title:
                titles.add(title)
    return titles


def is_chapter_heading(text):
    return bool(re.match(r"^CHAPTER\b", text.strip(), re.I))


def has_section_h1(lines):
    return any(re.match(r"^#\s+Section\s+\d+\b", line, re.I) for line in lines)


def is_unit_opener(lines, idx, text, unit_titles):
    text = text.strip()
    unit_match = re.match(r"^Unit\s+\d+\s+(.+?)\s*$", text, re.I)
    text_key = normalize_title(unit_match.group(1) if unit_match else text)
    if text_key not in unit_titles:
        return False
    if unit_match:
        return True
    window_before = lines[max(0, idx - 5) : idx]
    window_after = lines[idx + 1 : min(len(lines), idx + 7)]
    for near in window_before:
        stripped = near.strip()
        if re.fullmatch(r"\d{1,3}", stripped):
            return True
        unit_match = re.match(r"^Unit\s+\d+\s+(.+?)\s*$", stripped, re.I)
        if unit_match and normalize_title(unit_match.group(1)) == normalize_title(text):
            return True
    for near in window_after:
        stripped = near.strip()
        if re.match(r"^In this unit,\s*you will:?\s*$", stripped, re.I):
            return True
    return False


def protect_chapter_unit_hierarchy(lines):
    unit_titles = collect_chapter_unit_titles(lines)
    if not unit_titles:
        return lines
    has_chapter_or_part = any(
        re.match(r"^#{1,6}\s+(?:CHAPTER\b|Chapter\b|Part\s+\d+\b|Section\s+\d+\b)", line, re.I)
        for line in lines
    )
    protected = []
    in_chapter = False
    for idx, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            protected.append(line)
            continue
        level = len(match.group(1))
        text = match.group(2)
        if is_chapter_heading(text):
            in_chapter = True
            protected.append(f"# {text}")
        elif is_unit_opener(lines, idx, text, unit_titles):
            protected.append(f"## {text}" if has_chapter_or_part else f"# {text}")
        elif re.match(r"^\d+\.\d+\b", text):
            protected.append(f"### {text}")
        elif has_chapter_or_part and in_chapter and level <= 2:
            protected.append(f"### {text}")
        else:
            protected.append(line)
    return protected


def flatten_deep_headings(lines):
    flattened = []
    for line in lines:
        match = re.match(r"^#{4,6}\s+(.+?)\s*$", line)
        if match:
            flattened.append(match.group(1))
        else:
            flattened.append(line)
    return flattened


def load_protected_outline_titles(markdown_path):
    outline_path = Path(markdown_path).resolve().parent / "popo_outline.json"
    if not outline_path.exists():
        return set()
    try:
        data = json.loads(outline_path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    protected = set()
    for entry in data.get("outline") or []:
        title = normalize_title(entry.get("title"))
        if title:
            protected.add(title)
    return protected


def load_protected_outline_levels(markdown_path):
    outline_path = Path(markdown_path).resolve().parent / "popo_outline.json"
    if not outline_path.exists():
        return {}
    try:
        data = json.loads(outline_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    protected = {}
    for entry in data.get("outline") or []:
        title = normalize_title(entry.get("title"))
        level = entry.get("level")
        if title and isinstance(level, int):
            protected.setdefault(title, set()).add(max(1, min(3, level)))
    return protected


def load_protected_outline_line_levels(markdown_path, lines):
    outline_path = Path(markdown_path).resolve().parent / "popo_outline.json"
    if not outline_path.exists():
        return {}
    try:
        data = json.loads(outline_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    entries = [
        entry for entry in data.get("outline") or []
        if normalize_title(entry.get("title")) and isinstance(entry.get("level"), int)
    ]
    protected = {}
    entry_idx = 0
    for line_no, line in enumerate(lines, 1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        if entry_idx >= len(entries):
            break
        title = normalize_title(match.group(2))
        entry = entries[entry_idx]
        if title == normalize_title(entry.get("title")):
            protected[line_no] = max(1, min(3, int(entry["level"])))
            entry_idx += 1
            continue
        # Leave unmatched lines unprotected; the regression QA will catch any
        # final outline/Markdown divergence.
    return protected


def flatten_local_label_headings(lines, protected_titles=None):
    protected_titles = protected_titles or set()
    flattened = []
    for line in lines:
        match = re.match(r"^#{2,6}\s+(.+?)\s*$", line)
        title = normalize_title(match.group(1)) if match else ""
        if title in protected_titles:
            flattened.append(line)
            continue
        if match and (
            LOCAL_LABEL_HEADING_RE.match(title)
            or INSTRUCTION_HEADING_RE.match(match.group(1).strip())
            or NUMBERED_LIST_HEADING_RE.match(match.group(1).strip())
            or DECIMAL_LABEL_HEADING_RE.match(match.group(1).strip())
        ):
            flattened.append(match.group(1))
        else:
            flattened.append(line)
    return flattened


def ensure_top_level_heading(lines):
    if any(re.match(r"^#\s+\S", line) for line in lines):
        return lines
    promoted = []
    promoted_first = False
    for line in lines:
        if not promoted_first:
            match = re.match(r"^##\s+(.+?)\s*$", line)
            if match:
                promoted.append(f"# {match.group(1)}")
                promoted_first = True
                continue
        promoted.append(line)
    return promoted


def promote_first_floating_heading(lines):
    seen_h1 = False
    promoted_first = False
    out = []
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            out.append(line)
            continue
        level = len(match.group(1))
        text = match.group(2)
        if level == 1:
            seen_h1 = True
            out.append(line)
            continue
        if not seen_h1 and not promoted_first:
            out.append(f"# {text}")
            seen_h1 = True
            promoted_first = True
            continue
        out.append(line)
    return out


def main():
    parser = argparse.ArgumentParser(description="Apply safe DeepSeek outline review suggestions to clean.md.")
    parser.add_argument("markdown", type=Path)
    parser.add_argument("review_json", type=Path)
    parser.add_argument("--out-md", type=Path, required=True)
    parser.add_argument("--out-html", type=Path)
    parser.add_argument("--title", default="")
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--allow-nonunit-h1",
        action="store_true",
        help="Allow H1 promotion for non-Unit documents such as short handouts or topic packets.",
    )
    parser.add_argument(
        "--keep-noise-headings",
        action="store_true",
        help="Record but do not delete headings marked as noise; useful when OCR headings may be figure or exercise labels.",
    )
    args = parser.parse_args()

    lines = args.markdown.read_text(encoding="utf-8").splitlines()
    section_h1_present = has_section_h1(lines)
    review = json.loads(args.review_json.read_text(encoding="utf-8"))
    applied = []
    skipped = []
    delete_lines = set()
    replace_lines = {}
    protected_outline_levels = load_protected_outline_levels(args.markdown)
    protected_line_levels = load_protected_outline_line_levels(args.markdown, lines)

    for unit_review in review.get("reviews", []):
        for fix in unit_review.get("level_fixes", []) or []:
            lineno = int(fix.get("line", 0) or 0)
            current = heading_at(lines, lineno)
            if not current:
                skipped.append({"kind": "level_fix", "line": lineno, "reason": "line is not a heading"})
                continue
            _, text = current
            to_level = int(fix.get("to_level", 0) or 0)
            if not 1 <= to_level <= 6:
                skipped.append({"kind": "level_fix", "line": lineno, "reason": "invalid target level"})
                continue
            protected_line_level = protected_line_levels.get(lineno)
            if isinstance(protected_line_level, int) and to_level != protected_line_level:
                skipped.append({
                    "kind": "level_fix",
                    "line": lineno,
                    "reason": "conflicts with source outline line level",
                    "protected_level": protected_line_level,
                })
                continue
            protected_levels = protected_outline_levels.get(normalize_title(text), set())
            if protected_levels and to_level not in protected_levels:
                skipped.append({
                    "kind": "level_fix",
                    "line": lineno,
                    "reason": "conflicts with source outline level",
                    "protected_levels": sorted(protected_levels),
                })
                continue
            if to_level == 1 and not args.allow_nonunit_h1 and not re.fullmatch(r"Unit\s+\d+", text, re.I):
                skipped.append({"kind": "level_fix", "line": lineno, "reason": "non-Unit heading cannot be promoted to H1"})
                continue
            if to_level == 1 and section_h1_present and re.match(r"^Unit\s+\d+\b", text, re.I):
                skipped.append({"kind": "level_fix", "line": lineno, "reason": "Unit heading under Section H1 cannot be promoted to H1"})
                continue
            replace_lines[lineno] = f"{'#' * to_level} {text}"
            applied.append({"kind": "level_fix", "line": lineno, "to_level": to_level})

        for item in unit_review.get("noise_headings", []) or []:
            lineno = int(item.get("line", 0) or 0)
            if args.keep_noise_headings:
                skipped.append({"kind": "noise_heading", "line": lineno, "reason": "kept by --keep-noise-headings"})
                continue
            if heading_at(lines, lineno):
                delete_lines.add(lineno)
                applied.append({"kind": "noise_heading", "line": lineno})
            else:
                skipped.append({"kind": "noise_heading", "line": lineno, "reason": "line is not a heading"})

        for item in unit_review.get("merge_candidates", []) or []:
            merge_lines = [int(x) for x in item.get("lines", []) if str(x).isdigit()]
            merged_text = str(item.get("merged_text", "")).strip()
            if len(merge_lines) < 2 or not merged_text:
                skipped.append({"kind": "merge", "lines": merge_lines, "reason": "invalid merge candidate"})
                continue
            first = merge_lines[0]
            first_heading = heading_at(lines, first)
            if not first_heading:
                skipped.append({"kind": "merge", "lines": merge_lines, "reason": "first line is not a heading"})
                continue
            if any(not heading_at(lines, line) for line in merge_lines):
                skipped.append({"kind": "merge", "lines": merge_lines, "reason": "not all lines are headings"})
                continue
            level = first_heading[0]
            replace_lines[first] = f"{'#' * level} {merged_text}"
            for line in merge_lines[1:]:
                delete_lines.add(line)
            applied.append({"kind": "merge", "lines": merge_lines, "merged_text": merged_text})

        for key in ("ocr_corrections", "missing_expected"):
            for item in unit_review.get(key, []) or []:
                skipped.append({"kind": key, "item": item, "reason": "manual review required"})
        if unit_review.get("unit_heading_move"):
            skipped.append({"kind": "unit_heading_move", "item": unit_review["unit_heading_move"], "reason": "manual review required"})

    out_lines = []
    for lineno, line in enumerate(lines, 1):
        if lineno in delete_lines:
            continue
        out_lines.append(replace_lines.get(lineno, line))
    out_lines = protect_chapter_unit_hierarchy(out_lines)
    protected_outline_titles = load_protected_outline_titles(args.markdown)
    out_lines = flatten_local_label_headings(out_lines, protected_outline_titles)
    out_lines = flatten_deep_headings(out_lines)
    out_lines = ensure_top_level_heading(out_lines)
    out_lines = promote_first_floating_heading(out_lines)

    output = "\n".join(out_lines).rstrip() + "\n"
    args.out_md.write_text(output, encoding="utf-8")
    if args.out_html:
        args.out_html.write_text(markdown_to_html(output, title=args.title or args.out_md.parent.name), encoding="utf-8")
    if write_outline_anchor_check:
        write_outline_anchor_check(output, args.out_md.parent, title=args.title or "Outline Anchor Check")
    report = {
        "source_markdown": str(args.markdown),
        "review_json": str(args.review_json),
        "out_md": str(args.out_md),
        "out_html": str(args.out_html) if args.out_html else None,
        "applied_count": len(applied),
        "skipped_count": len(skipped),
        "applied": applied,
        "skipped": skipped,
    }
    report_path = args.report or (args.out_md.parent / "outline_apply_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.out_md}")
    if args.out_html:
        print(f"Wrote {args.out_html}")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
