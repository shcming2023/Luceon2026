#!/usr/bin/env python3
import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from discover_minio_tasks import DEFAULT_BUCKETS, build_discovery


SCRIPT_DIR = Path(__file__).resolve().parent


def run(cmd, *, cwd=None, env=None, timeout=None):
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "cmd": cmd,
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": f"Command timed out after {timeout} seconds.",
            "elapsed_seconds": round(time.time() - started, 3),
        }
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "elapsed_seconds": round(time.time() - started, 3),
    }


def require_ok(result):
    if result["returncode"] != 0:
        raise RuntimeError((result["stderr"] or result["stdout"] or "command failed").strip())


def discover_tasks(args):
    discovery_args = SimpleNamespace(
        container=args.container,
        input_bucket=args.input_bucket,
        mineru_bucket=args.mineru_bucket,
        minerupopo_bucket=args.minerupopo_bucket,
        raw_bucket=args.raw_bucket,
        pdf_id=args.pdf_id,
        job_id=args.job_id,
        limit=None,
    )
    discovery = build_discovery(discovery_args)
    tasks = [
        task for task in discovery["tasks"]
        if task.get("source_ready") and task.get("rebuild_state") in {"not_started", "published"}
    ]
    if args.limit:
        tasks = tasks[: args.limit]
    return discovery, tasks


def extract_refs(markdown):
    markdown_refs = re.findall(r"!\[[^\]]*\]\((images/[^)]+)\)", markdown)
    html_refs = re.findall(r"<img[^>]+src=[\"'](images/[^\"']+)", markdown, re.I)
    return markdown_refs + html_refs


def heading_summary(markdown):
    headings = []
    for line_no, line in enumerate(markdown.splitlines(), 1):
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            headings.append({
                "line": line_no,
                "level": len(match.group(1)),
                "text": match.group(2).strip(),
            })
    return {
        "count": len(headings),
        "levels": {str(level): sum(1 for h in headings if h["level"] == level) for level in range(1, 7)},
        "h1": [h["text"] for h in headings if h["level"] == 1],
        "h2": [h["text"] for h in headings if h["level"] == 2],
        "all": headings,
        "first": headings[:20],
        "last": headings[-12:],
    }


def normalize_title(text):
    text = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    text = re.sub(r"^[\"'“”‘’]+|[\"'“”‘’]+$", "", text)
    return text


def normalize_space(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def outline_heading_mismatches(popo_entries, heading_items):
    expected = [
        {"level": int(entry.get("level") or 0), "text": normalize_title(entry.get("title"))}
        for entry in popo_entries
        if isinstance(entry.get("level"), int) and normalize_title(entry.get("title"))
    ]
    actual = [
        {"level": int(item.get("level") or 0), "text": normalize_title(item.get("text"))}
        for item in heading_items
        if normalize_title(item.get("text"))
    ]
    mismatches = []
    if len(expected) != len(actual):
        mismatches.append({"kind": "count", "expected": len(expected), "actual": len(actual)})
    for idx, (want, got) in enumerate(zip(expected, actual), 1):
        if want != got:
            mismatches.append({"kind": "item", "index": idx, "expected": want, "actual": got})
            if len(mismatches) >= 20:
                break
    return mismatches


def outline_entry_signature(entry):
    return (
        int(entry.get("level") or 0),
        normalize_title(entry.get("title")),
        normalize_title(entry.get("parent_title")),
        normalize_title(entry.get("category_title")),
    )


def canonical_outline_emission_gaps(expected_entries, emitted_entries):
    expected = [
        (outline_entry_signature(entry), entry)
        for entry in expected_entries
        if normalize_title(entry.get("title"))
    ]
    emitted = {
        outline_entry_signature(entry)
        for entry in emitted_entries
        if normalize_title(entry.get("title"))
    }
    gaps = [entry for signature, entry in expected if signature not in emitted]
    if len(emitted_entries) < len(expected_entries):
        return gaps or expected_entries[len(emitted_entries):]
    return gaps


def collect_unit_titles(markdown):
    titles = set()
    for line in markdown.splitlines():
        match = re.match(r"^Unit\s+\d+\s+(.+?)\s*$", line.strip(), re.I)
        if match:
            title = normalize_title(match.group(1))
            if title:
                titles.add(title)
    return titles


def chapter_h2_pollution(markdown):
    unit_titles = collect_unit_titles(markdown)
    if not unit_titles:
        return []
    polluted = []
    in_chapter = False
    for line_no, line in enumerate(markdown.splitlines(), 1):
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if not match:
            continue
        level = len(match.group(1))
        text = match.group(2).strip()
        if level == 1 and re.match(r"^CHAPTER\b", text, re.I):
            in_chapter = True
            continue
        if not in_chapter or level != 2:
            continue
        allowed_structure = (
            re.match(r"^Unit\s+\d+\b", text, re.I)
            or re.match(r"^\d+\.\d+\b", text)
            or re.match(r"^Chapter\s+[A-Z]?\d+\.\s*Topic\s+\d+\b", text, re.I)
        )
        if allowed_structure:
            continue
        if normalize_title(text) not in unit_titles:
            polluted.append({"line": line_no, "text": text})
    return polluted


def floating_headings_before_h1(markdown):
    floating = []
    for line_no, line in enumerate(markdown.splitlines(), 1):
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if not match:
            continue
        level = len(match.group(1))
        text = match.group(2).strip()
        if level == 1:
            return floating
        floating.append({"line": line_no, "level": level, "text": text})
    return floating


def duplicate_h1_titles(headings):
    seen = set()
    duplicates = []
    for text in headings.get("h1") or []:
        norm = normalize_title(text)
        if norm in seen and text not in duplicates:
            duplicates.append(text)
        seen.add(norm)
    return duplicates


def duplicate_topic_numbers_by_parent(markdown):
    duplicates = []
    current_parent = ""
    seen_by_parent = {}
    for line_no, line in enumerate(markdown.splitlines(), 1):
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if not match:
            continue
        level = len(match.group(1))
        text = normalize_space(match.group(2))
        if level == 1:
            current_parent = text
            seen_by_parent.setdefault(current_parent, {})
            continue
        topic = re.match(r"^Topic\s+(\d+)\b", text, re.I)
        if level != 2 or not topic or not current_parent:
            continue
        number = int(topic.group(1))
        parent_seen = seen_by_parent.setdefault(current_parent, {})
        previous = parent_seen.get(number)
        if previous:
            duplicates.append({
                "parent": current_parent,
                "topic_number": number,
                "first": previous,
                "duplicate": {"line": line_no, "text": text},
            })
        else:
            parent_seen[number] = {"line": line_no, "text": text}
    return duplicates


def generic_chapter_topic_headings(markdown):
    result = []
    for line_no, line in enumerate(markdown.splitlines(), 1):
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if not match:
            continue
        text = normalize_space(match.group(2))
        if re.fullmatch(r"Chapter\s+\d+\s*\.\s*Topic\s+\d+", text, re.I):
            result.append({"line": line_no, "level": len(match.group(1)), "text": text})
    return result


def back_matter_headings(markdown):
    result = []
    pattern = re.compile(
        r"^(?:"
        r"appendix(?:\s+[A-Z0-9]+)?|"
        r"glossary(?:\s+of\b.*)?|"
        r"index|"
        r"answers?|"
        r"list\s+of\s+terms|"
        r"索引|词汇表|术语表|答案"
        r")$",
        re.I,
    )
    for line_no, line in enumerate(markdown.splitlines(), 1):
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if not match:
            continue
        text = normalize_space(match.group(2))
        if pattern.fullmatch(text):
            result.append({"line": line_no, "level": len(match.group(1)), "text": text})
    return result


def safe_relpath(path, start):
    try:
        return os.path.relpath(path, start)
    except ValueError:
        return str(path)


def read_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def read_markdown_headings(path):
    if not path.exists():
        return []
    return heading_summary(path.read_text(encoding="utf-8")).get("all") or []


def reconcile_outline_file(body_dir):
    popo_outline = read_json(body_dir / "popo_outline.json", {})
    expected = [
        {
            "level": int(entry.get("level") or 0),
            "text": normalize_space(entry.get("title")),
            "norm": normalize_title(entry.get("title")),
        }
        for entry in popo_outline.get("outline") or []
        if normalize_title(entry.get("title"))
    ]
    actual = [
        {
            "level": int(item.get("level") or 0),
            "text": normalize_space(item.get("text")),
            "norm": normalize_title(item.get("text")),
            "line": item.get("line"),
        }
        for item in read_markdown_headings(body_dir / "clean.md")
        if normalize_title(item.get("text"))
    ]
    missing = []
    level_mismatch = []
    for idx, want in enumerate(expected):
        got = actual[idx] if idx < len(actual) else None
        if not got:
            missing.append(want)
            continue
        if want["norm"] != got["norm"]:
            missing.append(want)
        elif want["level"] != got["level"]:
            level_mismatch.append({"expected": want, "actual": got})
    extra = actual[len(expected):] if len(actual) > len(expected) else []
    return {
        "expected_count": len(expected),
        "actual_count": len(actual),
        "missing": missing,
        "level_mismatch": level_mismatch,
        "extra": extra,
    }


def strip_html_tags(value):
    return re.sub(r"<[^>]+>", "", str(value or ""))


def span_text(fragment, class_name):
    pattern = re.compile(
        rf'<span class="{re.escape(class_name)}">(.*?)</span>',
        re.S,
    )
    match = pattern.search(fragment)
    if not match:
        return ""
    return normalize_space(html.unescape(strip_html_tags(match.group(1))))


def audit_outline_anchor_integrity(body_dir):
    clean_md = body_dir / "clean.md"
    outline_path = body_dir / "outline-view.html"
    if not outline_path.exists():
        outline_path = body_dir / "outline-anchor-check.html"
    if not clean_md.exists():
        return {"ok": False, "errors": [{"kind": "missing_clean_md"}], "warnings": [], "nav_count": 0}
    if not outline_path.exists():
        return {"ok": False, "errors": [{"kind": "missing_outline_view"}], "warnings": [], "nav_count": 0}

    markdown = clean_md.read_text(encoding="utf-8")
    lines = markdown.splitlines()
    headings = heading_summary(markdown).get("all") or []
    heading_by_line = {int(item["line"]): item for item in headings}
    expected_ranges = {}
    for idx, heading in enumerate(headings):
        start = int(heading["line"])
        end = int(headings[idx + 1]["line"]) - 1 if idx + 1 < len(headings) else len(lines)
        expected_ranges[start] = end

    outline_html = outline_path.read_text(encoding="utf-8")
    section_ids = set(re.findall(r'<section class="slice" id="([^"]+)"', outline_html))
    nav_pattern = re.compile(
        r'<a class="nav-item level-(\d+)"[^>]*href="#([^"]+)"[^>]*>(.*?)</a>',
        re.S,
    )
    nav_items = []
    errors = []
    warnings = []
    seen_ids = set()
    for match in nav_pattern.finditer(outline_html):
        level = int(match.group(1))
        target = html.unescape(match.group(2))
        inner = match.group(3)
        title = span_text(inner, "nav-title")
        line_span = span_text(inner, "nav-lines")
        line_match = re.fullmatch(r"(\d+)-(\d+)", line_span)
        if not line_match:
            errors.append({"kind": "invalid_nav_line_span", "target": target, "line_span": line_span})
            continue
        start = int(line_match.group(1))
        end = int(line_match.group(2))
        nav_items.append({"target": target, "level": level, "title": title, "start": start, "end": end})
        if target in seen_ids:
            errors.append({"kind": "duplicate_nav_target", "target": target})
        seen_ids.add(target)
        if target != f"h-{start}":
            errors.append({"kind": "target_line_mismatch", "target": target, "start": start})
        if target not in section_ids:
            errors.append({"kind": "missing_slice_target", "target": target, "title": title})
        heading = heading_by_line.get(start)
        if not heading:
            errors.append({"kind": "target_not_markdown_heading", "target": target, "start": start, "title": title})
            continue
        if int(heading.get("level") or 0) != level:
            errors.append({
                "kind": "target_level_mismatch",
                "target": target,
                "nav_level": level,
                "markdown_level": heading.get("level"),
                "title": title,
            })
        if normalize_title(heading.get("text")) != normalize_title(title):
            errors.append({
                "kind": "target_title_mismatch",
                "target": target,
                "nav_title": title,
                "markdown_title": heading.get("text"),
            })
        expected_end = expected_ranges.get(start)
        if expected_end != end:
            errors.append({
                "kind": "line_range_mismatch",
                "target": target,
                "nav_range": f"{start}-{end}",
                "expected_range": f"{start}-{expected_end}",
            })
        if end < start:
            errors.append({"kind": "negative_line_range", "target": target, "range": f"{start}-{end}"})

    if len(nav_items) != len(headings):
        errors.append({"kind": "nav_heading_count_mismatch", "nav_count": len(nav_items), "heading_count": len(headings)})
    missing_nav_targets = [f"h-{heading['line']}" for heading in headings if f"h-{heading['line']}" not in seen_ids]
    if missing_nav_targets:
        errors.append({"kind": "missing_nav_targets", "targets": missing_nav_targets[:30], "count": len(missing_nav_targets)})
    orphan_sections = sorted(section_ids - seen_ids, key=lambda value: int(value[2:]) if value.startswith("h-") and value[2:].isdigit() else 10**9)
    if orphan_sections:
        warnings.append({"kind": "orphan_slice_sections", "targets": orphan_sections[:30], "count": len(orphan_sections)})

    parent_intro_items = []
    for idx, item in enumerate(nav_items):
        next_item = nav_items[idx + 1] if idx + 1 < len(nav_items) else None
        if not next_item or next_item["level"] <= item["level"]:
            continue
        intro_start = item["start"] + 1
        intro_end = next_item["start"] - 1
        nonempty = sum(1 for line in lines[intro_start - 1:intro_end] if line.strip())
        parent_intro_items.append({
            "target": item["target"],
            "title": item["title"],
            "level": item["level"],
            "intro_line_count": nonempty,
            "intro_range": f"{intro_start}-{intro_end}" if intro_end >= intro_start else "",
        })

    return {
        "ok": not errors,
        "outline_file": str(outline_path),
        "nav_count": len(nav_items),
        "heading_count": len(headings),
        "slice_count": len(section_ids),
        "errors": errors[:50],
        "warnings": warnings[:20],
        "parent_intro_items": parent_intro_items[:80],
    }


def body_scope_flags(item):
    qa = item.get("qa") or {}
    headings = qa.get("headings") or {}
    flags = []
    first_heading = None
    if headings.get("all"):
        first_heading = headings["all"][0].get("text")
    if first_heading and re.search(r"附录", first_heading):
        flags.append("appendix_as_primary_title_manual_decision")
    if qa.get("back_matter_headings"):
        flags.append("back_matter_heading_present")
    if qa.get("review_flags"):
        flags.extend(flag for flag in qa.get("review_flags") if flag not in flags)
    return flags


def load_manual_review_status(path):
    if not path:
        return {}
    data = read_json(Path(path), {})
    entries = []
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        if isinstance(data.get("reviews"), list):
            entries = data["reviews"]
        else:
            for key, value in data.items():
                if isinstance(value, dict):
                    entry = dict(value)
                    entry.setdefault("key", key)
                    entries.append(entry)
    status = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pdf_id = normalize_space(entry.get("pdf_id"))
        job_id = normalize_space(entry.get("job_id"))
        key = normalize_space(entry.get("key"))
        if pdf_id and job_id:
            status[(pdf_id, job_id)] = entry
        if pdf_id:
            status[(pdf_id, "")] = entry
        if key:
            status[(key, "")] = entry
    return status


def manual_review_for(item, status_map):
    if not status_map:
        return {
            "status": "pending",
            "reviewer": "",
            "reviewed_at": "",
            "notes": "not recorded",
        }
    pdf_id = item.get("pdf_id") or ""
    job_id = item.get("job_id") or ""
    entry = (
        status_map.get((pdf_id, job_id))
        or status_map.get((pdf_id, ""))
        or status_map.get((job_id, ""))
        or {}
    )
    return {
        "status": normalize_space(entry.get("status") or "pending"),
        "reviewer": normalize_space(entry.get("reviewer")),
        "reviewed_at": normalize_space(entry.get("reviewed_at") or entry.get("date")),
        "notes": normalize_space(entry.get("notes") or entry.get("note")),
    }


def false_gates(qa):
    return [gate for gate, passed in (qa.get("gates") or {}).items() if not passed]


def manual_review_counts(results):
    counts = {}
    for item in results:
        status = (item.get("manual_review") or {}).get("status") or "pending"
        counts[status] = counts.get(status, 0) + 1
    approved = counts.get("approved", 0) + counts.get("pass", 0) + counts.get("passed", 0)
    needs_fix = counts.get("needs_fix", 0) + counts.get("failed", 0) + counts.get("fail", 0)
    pending = len(results) - approved - needs_fix
    return {
        "approved": approved,
        "pending": pending,
        "needs_fix": needs_fix,
        "raw": counts,
    }


def write_acceptance_audit(out_root, report):
    manual = report.get("manual_review_counts") or manual_review_counts(report.get("results") or [])
    lines = [
        "# Acceptance Audit",
        "",
        "This regression run is local-only and does not publish to MinIO.",
        "",
        f"- Tested: {report['tested_count']}",
        f"- Passed: {report['passed_count']}",
        f"- Failed: {report['failed_count']}",
        f"- Human approved: {manual['approved']}/{report['tested_count']}",
        f"- Human pending: {manual['pending']}",
        f"- Human needs fix: {manual['needs_fix']}",
        f"- Fully accepted: {'yes' if report.get('fully_accepted') else 'no'}",
        "",
        "| PDF | Mechanical QA | False gates | Manual scope flags | Review |",
        "|---|---|---|---|---|",
    ]
    for item in report["results"]:
        qa = item.get("qa") or {}
        ok = item["status"] == "ok" and qa.get("ok")
        label = item.get("pdf_name") or item["pdf_id"]
        gates = ", ".join(false_gates(qa)) or "-"
        flags = ", ".join(body_scope_flags(item)) or "-"
        rel = safe_relpath(Path(item.get("output_dir", "")) / "outline-view.html", out_root)
        lines.append(f"| {label} | {'PASS' if ok else 'FAIL'} | {gates} | {flags} | [{item['pdf_id']}]({rel}) |")
    (out_root / "acceptance_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_outline_fact_reconciliation(out_root, report):
    lines = [
        "# Outline Fact Reconciliation",
        "",
        "| PDF | Expected outline | Markdown headings | Missing | Level mismatch | Extra headings |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    details = []
    for item in report["results"]:
        body_dir = Path(item.get("output_dir", ""))
        rec = reconcile_outline_file(body_dir)
        label = item.get("pdf_name") or item["pdf_id"]
        lines.append(
            f"| {label} | {rec['expected_count']} | {rec['actual_count']} | "
            f"{len(rec['missing'])} | {len(rec['level_mismatch'])} | {len(rec['extra'])} |"
        )
        if rec["missing"] or rec["level_mismatch"] or rec["extra"]:
            details.append(f"## {label}")
            if rec["missing"]:
                details.append("")
                details.append("Missing or shifted expected headings:")
                for entry in rec["missing"][:30]:
                    details.append(f"- H{entry['level']} {entry['text']}")
            if rec["level_mismatch"]:
                details.append("")
                details.append("Level mismatches:")
                for pair in rec["level_mismatch"][:30]:
                    details.append(
                        f"- expected H{pair['expected']['level']} {pair['expected']['text']} "
                        f"but got H{pair['actual']['level']} at line {pair['actual'].get('line')}"
                    )
            if rec["extra"]:
                details.append("")
                details.append("Extra Markdown headings:")
                for entry in rec["extra"][:30]:
                    details.append(f"- H{entry['level']} {entry['text']} at line {entry.get('line')}")
            details.append("")
    if details:
        lines.extend(["", "## Details", ""])
        lines.extend(details)
    (out_root / "outline_fact_reconciliation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_body_scope_audit(out_root, report):
    lines = [
        "# Body Scope Audit",
        "",
        "| PDF | Page span | First heading | Last heading | Scope flags |",
        "|---|---|---|---|---|",
    ]
    for item in report["results"]:
        qa = item.get("qa") or {}
        pages = qa.get("pages") or {}
        headings = qa.get("headings") or {}
        all_headings = headings.get("all") or []
        first = all_headings[0]["text"] if all_headings else "-"
        last = all_headings[-1]["text"] if all_headings else "-"
        page_span = f"{pages.get('first_page_idx')}-{pages.get('last_page_idx')}" if pages else "-"
        flags = ", ".join(body_scope_flags(item)) or "-"
        label = item.get("pdf_name") or item["pdf_id"]
        lines.append(f"| {label} | {page_span} | {first} | {last} | {flags} |")
    (out_root / "body_scope_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_anchor_integrity_audit(out_root, report):
    lines = [
        "# Anchor Integrity Audit",
        "",
        "| PDF | Anchor QA | Nav | Headings | Slices | Errors | Parent intro items |",
        "|---|---|---:|---:|---:|---|---:|",
    ]
    details = []
    for item in report["results"]:
        qa = item.get("qa") or {}
        anchor = qa.get("outline_anchor_integrity") or {}
        label = item.get("pdf_name") or item["pdf_id"]
        errors = anchor.get("errors") or []
        status = "PASS" if anchor.get("ok") else "FAIL"
        lines.append(
            f"| {label} | {status} | {anchor.get('nav_count', 0)} | "
            f"{anchor.get('heading_count', 0)} | {anchor.get('slice_count', 0)} | "
            f"{len(errors)} | {len(anchor.get('parent_intro_items') or [])} |"
        )
        if errors or anchor.get("warnings"):
            details.append(f"## {label}")
            if errors:
                details.append("")
                details.append("Errors:")
                for error in errors[:30]:
                    details.append(f"- `{error.get('kind')}` {json.dumps(error, ensure_ascii=False)}")
            if anchor.get("warnings"):
                details.append("")
                details.append("Warnings:")
                for warning in (anchor.get("warnings") or [])[:20]:
                    details.append(f"- `{warning.get('kind')}` {json.dumps(warning, ensure_ascii=False)}")
            details.append("")
    if details:
        lines.extend(["", "## Details", ""])
        lines.extend(details)
    (out_root / "anchor_integrity_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manual_review_checklist(out_root, report):
    lines = [
        "# Manual Review Checklist",
        "",
        "For each sample, open the linked outline view. Click headings on the left and verify that the right pane jumps to the inserted clean.md anchor and that the shown line range covers the intended content chunk.",
        "",
        "| Priority | PDF | What to check | Review link |",
        "|---|---|---|---|",
    ]
    for item in report["results"]:
        qa = item.get("qa") or {}
        headings = qa.get("headings") or {}
        checks = []
        if any(int(level) == 3 and count for level, count in (headings.get("levels") or {}).items()):
            checks.append("H3 placement")
        if body_scope_flags(item):
            checks.append("body boundary")
        anchor = qa.get("outline_anchor_integrity") or {}
        if anchor.get("parent_intro_items"):
            checks.append("parent intro chunks")
        if anchor and not anchor.get("ok"):
            checks.append("anchor integrity")
        if false_gates(qa):
            checks.append("failed gates")
        if not checks:
            checks.append("spot-check outline anchors")
        priority = "high" if false_gates(qa) or body_scope_flags(item) else "normal"
        rel = safe_relpath(Path(item.get("output_dir", "")) / "outline-view.html", out_root)
        label = item.get("pdf_name") or item["pdf_id"]
        lines.append(f"| {priority} | {label} | {', '.join(checks)} | [{item['pdf_id']}]({rel}) |")
    (out_root / "manual_review_checklist.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_human_review_status(out_root, report):
    results = report.get("results") or []
    counts = report.get("manual_review_counts") or manual_review_counts(results)
    approved = counts["approved"]
    needs_fix = counts["needs_fix"]
    pending = counts["pending"]
    lines = [
        "# Human Review Status",
        "",
        "This file records manual acceptance state separately from mechanical QA. A sample is not fully accepted until its outline tree and clean.md anchor placement have been manually spot-checked.",
        "",
        f"- Approved: {approved}",
        f"- Pending: {pending}",
        f"- Needs fix: {needs_fix}",
        "",
        "| Human status | Mechanical QA | PDF | Suggested check | Review link | Reviewer | Reviewed at | Notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    html_rows = []
    for item in results:
        qa = item.get("qa") or {}
        label = item.get("pdf_name") or item["pdf_id"]
        rel = safe_relpath(Path(item.get("output_dir", "")) / "outline-view.html", out_root)
        spotchecks = select_manual_spotchecks(item)
        first_spotcheck = spotchecks[0] if spotchecks else {}
        spotcheck_line = int(first_spotcheck.get("line") or 0)
        spotcheck_rel = f"{rel}#h-{spotcheck_line}" if spotcheck_line else rel
        manual = item.get("manual_review") or {}
        manual_status = manual.get("status") or "pending"
        mech = "PASS" if item.get("status") == "ok" and qa.get("ok") else "FAIL"
        checks = []
        if body_scope_flags(item):
            checks.append("body boundary")
        anchor = qa.get("outline_anchor_integrity") or {}
        if anchor.get("parent_intro_items"):
            checks.append("parent intro chunks")
        if any(int(level) == 3 and count for level, count in ((qa.get("headings") or {}).get("levels") or {}).items()):
            checks.append("H3 placement")
        if not checks:
            checks.append("outline anchors")
        check_text = ", ".join(checks)
        lines.append(
            f"| {manual_status} | {mech} | {label} | {check_text} | "
            f"[outline]({rel}) / [spotcheck]({spotcheck_rel}) | {manual.get('reviewer') or '-'} | "
            f"{manual.get('reviewed_at') or '-'} | {manual.get('notes') or '-'} |"
        )
        html_rows.append(
            "<tr>"
            f"<td class=\"human {html.escape(manual_status)}\">{html.escape(manual_status)}</td>"
            f"<td>{html.escape(mech)}</td>"
            f"<td><a href=\"{html.escape(rel, quote=True)}\">{html.escape(label)}</a><div class=\"ids\">{html.escape(item['pdf_id'])}<br>{html.escape(item['job_id'])}</div></td>"
            f"<td>{html.escape(check_text)}</td>"
            f"<td><a href=\"{html.escape(spotcheck_rel, quote=True)}\">first anchor</a></td>"
            f"<td>{html.escape(manual.get('reviewer') or '-')}</td>"
            f"<td>{html.escape(manual.get('reviewed_at') or '-')}</td>"
            f"<td>{html.escape(manual.get('notes') or '-')}</td>"
            "</tr>"
        )
    (out_root / "human_review_status.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    html_text = f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<title>Human Review Status</title>
<style>
body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif; color: #1f2933; background: #f6f7f9; }}
header {{ padding: 24px 32px 16px; background: #fff; border-bottom: 1px solid #d8dde6; }}
main {{ padding: 24px 32px 64px; }}
h1 {{ margin: 0 0 8px; font-size: 24px; }}
.summary {{ display: flex; gap: 16px; color: #52606d; flex-wrap: wrap; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8dde6; }}
th, td {{ padding: 9px 11px; border-bottom: 1px solid #e4e7eb; text-align: left; vertical-align: top; font-size: 14px; }}
th {{ background: #f0f3f7; }}
a {{ color: #0b5cad; text-decoration: none; font-weight: 600; }}
a:hover {{ text-decoration: underline; }}
.human {{ font-weight: 700; }}
.human.approved, .human.pass, .human.passed {{ color: #0f7b3f; }}
.human.needs_fix, .human.fail, .human.failed {{ color: #b42318; }}
.human.pending {{ color: #8a5a00; }}
.ids {{ color: #7b8794; font-size: 12px; margin-top: 4px; line-height: 1.4; }}
</style>
</head>
<body>
<header>
<h1>Human Review Status</h1>
<div class=\"summary\"><span>Approved: {approved}</span><span>Pending: {pending}</span><span>Needs fix: {needs_fix}</span></div>
</header>
<main>
<table>
<thead><tr><th>Human status</th><th>Mechanical QA</th><th>PDF / outline review</th><th>Suggested check</th><th>Spotcheck</th><th>Reviewer</th><th>Reviewed at</th><th>Notes</th></tr></thead>
<tbody>{''.join(html_rows)}</tbody>
</table>
</main>
</body>
</html>
"""
    (out_root / "human_review_status.html").write_text(html_text, encoding="utf-8")


def write_manual_review_status_template(out_root, report):
    template = {
        "instructions": "Set status to approved only after manually spot-checking the outline tree and clean.md anchor placement in outline-view.html. Use needs_fix with notes when a sample fails manual review.",
        "reviews": [],
    }
    for item in report.get("results") or []:
        manual = item.get("manual_review") or {}
        template["reviews"].append({
            "pdf_id": item.get("pdf_id"),
            "job_id": item.get("job_id"),
            "pdf_name": item.get("pdf_name") or item.get("pdf_id"),
            "status": manual.get("status") or "pending",
            "reviewer": manual.get("reviewer") or "",
            "reviewed_at": manual.get("reviewed_at") or "",
            "notes": manual.get("notes") or "",
        })
    (out_root / "manual_review_status_template.json").write_text(
        json.dumps(template, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def review_queue_item(item, out_root):
    qa = item.get("qa") or {}
    headings = qa.get("headings") or {}
    anchor = qa.get("outline_anchor_integrity") or {}
    manual = item.get("manual_review") or {}
    status = manual.get("status") or "pending"
    rel = safe_relpath(Path(item.get("output_dir", "")) / "outline-view.html", out_root)
    spotchecks = select_manual_spotchecks(item)
    first_spotcheck = spotchecks[0] if spotchecks else {}
    line = int(first_spotcheck.get("line") or 0)
    spotcheck_rel = f"{rel}#h-{line}" if line else rel
    flags = body_scope_flags(item)
    checks = []
    score = 0
    if false_gates(qa):
        checks.append("failed gates")
        score += 1000
    if status == "needs_fix":
        checks.append("needs fix follow-up")
        score += 800
    if flags:
        checks.append("body boundary")
        score += 500
    h3_count = int((headings.get("levels") or {}).get("3") or 0)
    if h3_count:
        checks.append("H3 placement")
        score += 100 + min(h3_count, 50)
    parent_intro_count = len(anchor.get("parent_intro_items") or [])
    if parent_intro_count:
        checks.append("parent intro chunks")
        score += min(parent_intro_count, 40)
    heading_count = int(headings.get("count") or 0)
    if heading_count >= 80:
        checks.append("large outline")
        score += 20
    if not checks:
        checks.append("outline anchors")
    return {
        "status": status,
        "score": score,
        "pdf": item.get("pdf_name") or item["pdf_id"],
        "pdf_id": item.get("pdf_id"),
        "job_id": item.get("job_id"),
        "mechanical": "PASS" if item.get("status") == "ok" and qa.get("ok") else "FAIL",
        "heading_count": heading_count,
        "anchor_count": anchor.get("nav_count", 0),
        "checks": checks,
        "outline": rel,
        "spotcheck": spotcheck_rel,
        "scope_flags": flags,
    }


def write_pending_review_queue(out_root, report):
    queue = [
        review_queue_item(item, out_root)
        for item in report.get("results") or []
        if (item.get("manual_review") or {}).get("status", "pending") not in {"approved", "pass", "passed"}
    ]
    queue.sort(key=lambda item: (-item["score"], item["pdf"].lower()))
    lines = [
        "# Pending Human Review Queue",
        "",
        "Review these pending samples in order. Each row links to the two-pane outline view and an initial anchor for quick spot-checking.",
        "",
        f"- Pending: {len(queue)}",
        "",
        "| # | Human status | Mechanical QA | PDF | Suggested checks | Headings | Anchors | Links | Scope flags |",
        "|---:|---|---|---|---|---:|---:|---|---|",
    ]
    html_rows = []
    for idx, item in enumerate(queue, 1):
        checks = ", ".join(item["checks"])
        flags = ", ".join(item["scope_flags"]) or "-"
        lines.append(
            f"| {idx} | {item['status']} | {item['mechanical']} | {item['pdf']} | {checks} | "
            f"{item['heading_count']} | {item['anchor_count']} | "
            f"[outline]({item['outline']}) / [spotcheck]({item['spotcheck']}) | {flags} |"
        )
        html_rows.append(
            "<tr>"
            f"<td>{idx}</td>"
            f"<td class=\"human {html.escape(item['status'])}\">{html.escape(item['status'])}</td>"
            f"<td>{html.escape(item['mechanical'])}</td>"
            f"<td><a href=\"{html.escape(item['outline'], quote=True)}\">{html.escape(item['pdf'])}</a><div class=\"ids\">{html.escape(item['pdf_id'] or '')}<br>{html.escape(item['job_id'] or '')}</div></td>"
            f"<td>{html.escape(checks)}</td>"
            f"<td>{item['heading_count']}</td>"
            f"<td>{item['anchor_count']}</td>"
            f"<td><a href=\"{html.escape(item['spotcheck'], quote=True)}\">spotcheck</a></td>"
            f"<td>{html.escape(flags)}</td>"
            "</tr>"
        )
    (out_root / "pending_review_queue.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    html_text = f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<title>Pending Human Review Queue</title>
<style>
body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif; color: #1f2933; background: #f6f7f9; }}
header {{ padding: 24px 32px 16px; background: #fff; border-bottom: 1px solid #d8dde6; }}
main {{ padding: 24px 32px 64px; }}
h1 {{ margin: 0 0 8px; font-size: 24px; }}
p {{ margin: 0; color: #52606d; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8dde6; }}
th, td {{ padding: 9px 11px; border-bottom: 1px solid #e4e7eb; text-align: left; vertical-align: top; font-size: 14px; }}
th {{ background: #f0f3f7; }}
a {{ color: #0b5cad; text-decoration: none; font-weight: 600; }}
a:hover {{ text-decoration: underline; }}
.human {{ font-weight: 700; }}
.human.pending {{ color: #8a5a00; }}
.human.needs_fix, .human.fail, .human.failed {{ color: #b42318; }}
.ids {{ color: #7b8794; font-size: 12px; margin-top: 4px; line-height: 1.4; }}
</style>
</head>
<body>
<header>
<h1>Pending Human Review Queue</h1>
<p>Review pending samples in order; open the outline view or jump to the first suggested anchor.</p>
</header>
<main>
<table>
<thead><tr><th>#</th><th>Human status</th><th>Mechanical QA</th><th>PDF / outline review</th><th>Suggested checks</th><th>Headings</th><th>Anchors</th><th>Spotcheck</th><th>Scope flags</th></tr></thead>
<tbody>{''.join(html_rows)}</tbody>
</table>
</main>
</body>
</html>
"""
    (out_root / "pending_review_queue.html").write_text(html_text, encoding="utf-8")


def write_pending_review_link_audit(out_root, report):
    queue = [
        review_queue_item(item, out_root)
        for item in report.get("results") or []
        if (item.get("manual_review") or {}).get("status", "pending") not in {"approved", "pass", "passed"}
    ]
    queue.sort(key=lambda item: (-item["score"], item["pdf"].lower()))
    rows = []
    broken = []
    for idx, item in enumerate(queue, 1):
        outline_rel = item.get("outline") or ""
        spotcheck_rel = item.get("spotcheck") or outline_rel
        outline_path = out_root / outline_rel
        outline_exists = outline_path.exists()
        outline_html = outline_path.read_text(encoding="utf-8") if outline_exists else ""
        section_ids = set(re.findall(r'<section class="slice" id="([^"]+)"', outline_html))
        fragment = ""
        if "#" in spotcheck_rel:
            fragment = spotcheck_rel.rsplit("#", 1)[-1]
        target_exists = (not fragment) or fragment in section_ids
        ok = outline_exists and target_exists
        row = {
            "index": idx,
            "pdf": item.get("pdf") or item.get("pdf_id") or "",
            "pdf_id": item.get("pdf_id") or "",
            "job_id": item.get("job_id") or "",
            "outline": outline_rel,
            "spotcheck": spotcheck_rel,
            "fragment": fragment,
            "outline_exists": outline_exists,
            "target_exists": target_exists,
            "ok": ok,
        }
        rows.append(row)
        if not ok:
            broken.append(row)

    lines = [
        "# Pending Review Link Audit",
        "",
        "This audit verifies that the prioritized pending-review queue points to existing outline views and valid clean.md heading anchors.",
        "",
        f"- Pending rows: {len(queue)}",
        f"- Broken rows: {len(broken)}",
        "",
        "| Status | # | PDF | Outline file | Spotcheck target | Notes |",
        "|---|---:|---|---|---|---|",
    ]
    for row in rows:
        status = "PASS" if row["ok"] else "FAIL"
        notes = []
        if not row["outline_exists"]:
            notes.append("missing outline file")
        if row["fragment"] and not row["target_exists"]:
            notes.append("missing target anchor")
        if not notes:
            notes.append("-")
        lines.append(
            f"| {status} | {row['index']} | {row['pdf']} | "
            f"[outline]({row['outline']}) | [{row['fragment'] or 'top'}]({row['spotcheck']}) | "
            f"{', '.join(notes)} |"
        )
    (out_root / "pending_review_link_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def decision_sheet_facts(item):
    qa = item.get("qa") or {}
    headings = qa.get("headings") or {}
    levels = headings.get("levels") or {}
    files = qa.get("files") or {}
    pages = qa.get("pages") or {}
    parts = [
        f"headings {headings.get('count', 0)}",
        f"H1 {levels.get('1', 0)}",
        f"H2 {levels.get('2', 0)}",
        f"H3 {levels.get('3', 0)}",
    ]
    if files:
        parts.append(f"images {files.get('image_refs', 0)}/{files.get('image_files', 0)}")
    if pages:
        parts.append(f"pages {pages.get('first_page_idx', '-')}-{pages.get('last_page_idx', '-')}")
    flags = body_scope_flags(item)
    if flags:
        parts.append("scope " + ", ".join(flags))
    return "; ".join(str(part) for part in parts)


def write_manual_review_decision_sheet(out_root, report):
    results = report.get("results") or []
    counts = report.get("manual_review_counts") or manual_review_counts(results)
    approved = []
    pending = []
    needs_fix = []
    for item in results:
        status = (item.get("manual_review") or {}).get("status") or "pending"
        if status in {"approved", "pass", "passed"}:
            approved.append(item)
        elif status in {"needs_fix", "failed", "fail"}:
            needs_fix.append(item)
        else:
            pending.append(item)

    def row_for(item):
        queue_item = review_queue_item(item, out_root)
        label = item.get("pdf_name") or item.get("pdf_id")
        checks = ", ".join(queue_item.get("checks") or ["outline anchors"])
        return (
            f"| [ ] approve / [ ] needs fix | {label} | "
            f"[outline]({queue_item['outline']}) / [spotcheck]({queue_item['spotcheck']}) | "
            f"{decision_sheet_facts(item)} | {checks} |"
        )

    lines = [
        "# Manual Review Decision Sheet",
        "",
        f"Regression root: `{out_root}`",
        "",
        "Review index: [review-index.html](review-index.html)",
        "",
        f"- Mechanical QA: {report.get('passed_count', 0)}/{report.get('tested_count', 0)} PASS",
        f"- Human approved: {counts['approved']}/{report.get('tested_count', 0)}",
        f"- Pending human review: {counts['pending']}/{report.get('tested_count', 0)}",
        f"- Needs fix: {counts['needs_fix']}/{report.get('tested_count', 0)}",
        f"- Fully accepted: {'yes' if report.get('fully_accepted') else 'no'}",
        "",
        "This sheet is for human decisions only. A sample remains pending until the user explicitly approves it after checking both the outline tree and the actual `clean.md` heading anchors in `outline-view.html`.",
        "",
        "## Already Approved",
        "",
    ]
    if approved:
        lines.extend(["| PDF | Evidence |", "|---|---|"])
        for item in approved:
            manual = item.get("manual_review") or {}
            label = item.get("pdf_name") or item.get("pdf_id")
            notes = manual.get("notes") or "approved in manual review ledger"
            lines.append(f"| {label} | {notes} |")
    else:
        lines.append("No samples have been approved in the manual review ledger.")

    if needs_fix:
        lines.extend([
            "",
            "## Needs Fix",
            "",
            "| Decision | PDF | Review link | Current facts | Suggested checks |",
            "|---|---|---|---|---|",
        ])
        for item in needs_fix:
            lines.append(row_for(item))

    lines.extend([
        "",
        "## Pending Decisions",
        "",
        "| Decision | PDF | Review link | Current facts | Suggested checks |",
        "|---|---|---|---|---|",
    ])
    if pending:
        pending_rows = [
            (review_queue_item(item, out_root), item) for item in pending
        ]
        pending_rows.sort(key=lambda pair: (-pair[0]["score"], pair[0]["pdf"].lower()))
        for _, item in pending_rows:
            lines.append(row_for(item))
    else:
        lines.append("| - | No pending samples. | - | - | - |")

    lines.extend([
        "",
        "## How To Respond",
        "",
        "Approve or reject samples by PDF name or short name, for example:",
        "",
        "- `第 2、3、4 个样本通过`",
        "- `某某教材的 Writing 空白需要修`",
        "",
        "After a decision, update `manual_review_status_template.json`, rerun regression with that status file, and verify the report still separates `passed_count` from `accepted_count`.",
        "",
        "If the user refers to the pending-review queue row number, use `update_manual_review_status.py ... --row-scope pending --regression-root <regression-root>` so numeric selectors match this review order instead of the raw ledger order.",
    ])
    (out_root / "manual_review_decision_sheet.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    pending_rows_html = []
    pending_html_items = [(review_queue_item(item, out_root), item) for item in pending]
    pending_html_items.sort(key=lambda pair: (-pair[0]["score"], pair[0]["pdf"].lower()))
    for queue_item, item in pending_html_items:
        label = item.get("pdf_name") or item.get("pdf_id")
        pending_rows_html.append(
            "<tr>"
            "<td><label><input type=\"checkbox\"> approve</label><br><label><input type=\"checkbox\"> needs fix</label></td>"
            f"<td><a href=\"{html.escape(queue_item['outline'], quote=True)}\">{html.escape(label)}</a>"
            f"<div class=\"ids\">{html.escape(item.get('pdf_id') or '')}<br>{html.escape(item.get('job_id') or '')}</div></td>"
            f"<td><a href=\"{html.escape(queue_item['spotcheck'], quote=True)}\">first anchor</a></td>"
            f"<td>{html.escape(decision_sheet_facts(item))}</td>"
            f"<td>{html.escape(', '.join(queue_item.get('checks') or ['outline anchors']))}</td>"
            "</tr>"
        )
    approved_rows_html = []
    for item in approved:
        label = item.get("pdf_name") or item.get("pdf_id")
        manual = item.get("manual_review") or {}
        approved_rows_html.append(
            "<tr>"
            f"<td>{html.escape(label)}</td>"
            f"<td>{html.escape(manual.get('notes') or 'approved in manual review ledger')}</td>"
            "</tr>"
        )
    html_text = f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<title>Manual Review Decision Sheet</title>
<style>
body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif; color: #1f2933; background: #f6f7f9; }}
header {{ padding: 24px 32px 16px; background: #fff; border-bottom: 1px solid #d8dde6; }}
main {{ padding: 24px 32px 64px; }}
h1 {{ margin: 0 0 8px; font-size: 24px; }}
h2 {{ margin-top: 26px; font-size: 18px; }}
.summary {{ display: flex; gap: 16px; color: #52606d; flex-wrap: wrap; }}
.note {{ margin-top: 12px; color: #52606d; max-width: 960px; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8dde6; margin-top: 10px; }}
th, td {{ padding: 9px 11px; border-bottom: 1px solid #e4e7eb; text-align: left; vertical-align: top; font-size: 14px; }}
th {{ background: #f0f3f7; }}
a {{ color: #0b5cad; text-decoration: none; font-weight: 600; }}
a:hover {{ text-decoration: underline; }}
.ids {{ color: #7b8794; font-size: 12px; margin-top: 4px; line-height: 1.4; }}
label {{ white-space: nowrap; color: #52606d; }}
</style>
</head>
<body>
<header>
<h1>Manual Review Decision Sheet</h1>
<div class=\"summary\">
<span>Mechanical QA: {report.get('passed_count', 0)}/{report.get('tested_count', 0)} PASS</span>
<span>Human approved: {counts['approved']}/{report.get('tested_count', 0)}</span>
<span>Pending human review: {counts['pending']}</span>
<span>Needs fix: {counts['needs_fix']}</span>
<span>Fully accepted: {'yes' if report.get('fully_accepted') else 'no'}</span>
</div>
<p class=\"note\">This sheet is for user decisions only. It does not mark any sample approved unless the manual review ledger says so.</p>
</header>
<main>
<h2>Already Approved</h2>
<table><thead><tr><th>PDF</th><th>Evidence</th></tr></thead><tbody>{''.join(approved_rows_html) or '<tr><td colspan=\"2\">No approved samples.</td></tr>'}</tbody></table>
<h2>Pending Decisions</h2>
<table><thead><tr><th>Decision</th><th>PDF / outline review</th><th>Spotcheck</th><th>Current facts</th><th>Suggested checks</th></tr></thead><tbody>{''.join(pending_rows_html) or '<tr><td colspan=\"5\">No pending samples.</td></tr>'}</tbody></table>
</main>
</body>
</html>
"""
    (out_root / "manual_review_decision_sheet.html").write_text(html_text, encoding="utf-8")


def write_pending_manual_fact_review_summary(out_root, report):
    results = report.get("results") or []
    counts = report.get("manual_review_counts") or manual_review_counts(results)
    pending_pairs = []
    for item in results:
        status = (item.get("manual_review") or {}).get("status") or "pending"
        if status in {"approved", "pass", "passed"}:
            continue
        pending_pairs.append((review_queue_item(item, out_root), item))
    pending_pairs.sort(key=lambda pair: (-pair[0]["score"], pair[0]["pdf"].lower()))

    lines = [
        "# Pending Manual Fact Review Summary",
        "",
        f"Regression root: `{out_root}`",
        "",
        "Review index: [review-index.html](review-index.html)",
        "",
        "Current acceptance state:",
        "",
        f"- Mechanical QA: {report.get('passed_count', 0)}/{report.get('tested_count', 0)} PASS",
        f"- Human approved: {counts['approved']}/{report.get('tested_count', 0)}",
        f"- Pending human review: {counts['pending']}/{report.get('tested_count', 0)}",
        f"- Needs fix: {counts['needs_fix']}/{report.get('tested_count', 0)}",
        f"- Fully accepted: {'yes' if report.get('fully_accepted') else 'no'}",
        "",
        "This file records factual review prompts for user judgment. It does not mark any sample approved.",
        "",
        "| # | PDF | Outline link | Directory facts | Mechanical/anchor QA | Suggested checks | Human status |",
        "|---:|---|---|---|---|---|---|",
    ]
    for idx, (queue_item, item) in enumerate(pending_pairs, 1):
        label = item.get("pdf_name") or item.get("pdf_id")
        qa = item.get("qa") or {}
        anchor = qa.get("outline_anchor_integrity") or {}
        anchor_status = "PASS" if anchor.get("ok") else "FAIL"
        mechanical = queue_item.get("mechanical") or ("PASS" if item.get("status") == "ok" and qa.get("ok") else "FAIL")
        checks = ", ".join(queue_item.get("checks") or ["outline anchors"])
        lines.append(
            f"| {idx} | {label} | [outline]({queue_item['outline']}) / [spotcheck]({queue_item['spotcheck']}) | "
            f"{decision_sheet_facts(item)} | {mechanical}; anchor {anchor_status}/{anchor.get('nav_count', 0)} | "
            f"{checks} | {queue_item.get('status') or 'pending'} |"
        )
    if not pending_pairs:
        lines.append("| - | No pending samples. | - | - | - | - | - |")

    lines.extend([
        "",
        "## Review Rule",
        "",
        "A sample can move from pending to approved only after the user checks both the reconstructed outline tree and the linked `clean.md` heading anchors. Mechanical PASS is necessary but not sufficient for acceptance.",
    ])
    (out_root / "pending_manual_fact_review_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manual_review_command_sheet(out_root, report):
    skill_root = SCRIPT_DIR.parent
    ledger = out_root / "manual_review_status_template.json"
    pending_pairs = []
    for item in report.get("results") or []:
        status = (item.get("manual_review") or {}).get("status") or "pending"
        if status in {"approved", "pass", "passed"}:
            continue
        pending_pairs.append((review_queue_item(item, out_root), item))
    pending_pairs.sort(key=lambda pair: (-pair[0]["score"], pair[0]["pdf"].lower()))

    lines = [
        "# Manual Review Command Sheet",
        "",
        "Use these commands after the user makes explicit manual decisions. Numeric selectors here match the pending-review queue order, not the raw ledger order.",
        "For explicit multi-sample decisions, batch numeric selectors are supported, for example `--select 1,2,3`, `--select 1-3`, or `--select 1、2、3`.",
        "",
        f"Regression root: `{out_root}`",
        f"Ledger: `{ledger}`",
        "",
        "## Finalize After Updates",
        "",
        "Run this after updating one or more statuses. Replace `<baseline-root>` with the previous accepted or working regression root when comparing after rule changes.",
        "",
        "```bash",
        f"python3 {skill_root}/scripts/finalize_regression_review.py {out_root} --baseline-root <baseline-root> --manual-review-status {ledger}",
        "```",
        "",
        "## Pending Commands",
        "",
        "| # | PDF | Review link | Approve command | Needs-fix command |",
        "|---:|---|---|---|---|",
    ]
    if not pending_pairs:
        lines.append("| - | No pending samples. | - | - | - |")
    for idx, (queue_item, item) in enumerate(pending_pairs, 1):
        label = item.get("pdf_name") or item.get("pdf_id")
        approve_cmd = (
            f"python3 {skill_root}/scripts/update_manual_review_status.py {ledger} "
            f"--status approved --select {idx} --row-scope pending --regression-root {out_root} "
            f"--notes \"User spot-checked outline tree and clean.md anchors.\""
        )
        needs_fix_cmd = (
            f"python3 {skill_root}/scripts/update_manual_review_status.py {ledger} "
            f"--status needs_fix --select {idx} --row-scope pending --regression-root {out_root} "
            f"--notes \"User found outline or anchor boundary issues.\""
        )
        lines.append(
            f"| {idx} | {label} | [outline]({queue_item['outline']}) / [spotcheck]({queue_item['spotcheck']}) | "
            f"`{approve_cmd}` | `{needs_fix_cmd}` |"
        )
    lines.extend([
        "",
        "## Safety Notes",
        "",
        "- Do not run an approve command unless the user explicitly approved that sample after checking the outline tree and clean.md anchor placement.",
        "- If a sample is rejected or uncertain, use `needs_fix` with a concrete note instead of leaving the issue only in chat.",
        "- The finalize command must still fail until every selected sample is approved and all mechanical/stability gates pass.",
    ])
    (out_root / "manual_review_command_sheet.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_stability_comparison(out_root):
    if not out_root:
        return None
    path = out_root / "regression_stability_comparison.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "path": str(path),
            "failure_count": 1,
            "warning_count": 0,
            "failures": [{"check": "stability_comparison_parse_error", "detail": str(exc)}],
            "warnings": [],
        }
    data["path"] = str(path)
    return data


def final_acceptance_checks(report, out_root=None):
    results = report.get("results") or []
    tested = int(report.get("tested_count") or len(results))
    passed = int(report.get("passed_count") or 0)
    failed = int(report.get("failed_count") or 0)
    accepted = int(report.get("accepted_count") or 0)
    pending = int(report.get("pending_manual_review_count") or 0)
    needs_fix = int(report.get("needs_fix_count") or 0)

    false_gate_items = []
    anchor_fail_items = []
    manual_pending_items = []
    manual_needs_fix_items = []
    for item in results:
        label = item.get("pdf_name") or item.get("pdf_id")
        gates = false_gates(item.get("qa") or {})
        if gates:
            false_gate_items.append((label, gates))
        anchor = ((item.get("qa") or {}).get("outline_anchor_integrity") or {})
        if not anchor.get("ok"):
            anchor_fail_items.append((label, anchor))
        status = ((item.get("manual_review") or {}).get("status") or "pending").lower()
        if status in {"needs_fix", "failed", "fail"}:
            manual_needs_fix_items.append(label)
        elif status not in {"approved", "pass", "passed"}:
            manual_pending_items.append(label)

    inventory_link_audit = outline_inventory_link_audit_result(out_root, report) if out_root else None
    manual_anchor_link_audit = manual_anchor_link_audit_result(out_root, report) if out_root else None
    stability_comparison = load_stability_comparison(out_root) if out_root else None

    checks = [
        {
            "name": "mechanical_qa_all_passed",
            "ok": tested > 0 and passed == tested and failed == 0,
            "detail": f"{passed}/{tested} mechanical PASS; failed {failed}",
        },
        {
            "name": "no_false_gates",
            "ok": not false_gate_items,
            "detail": f"{len(false_gate_items)} samples with false gates",
        },
        {
            "name": "outline_anchor_integrity_all_passed",
            "ok": not anchor_fail_items,
            "detail": f"{len(anchor_fail_items)} samples with outline anchor failures",
        },
        {
            "name": "outline_inventory_links_all_valid",
            "ok": (inventory_link_audit is None) or inventory_link_audit["broken_count"] == 0,
            "detail": "not checked" if inventory_link_audit is None else f"{inventory_link_audit['total_count']} links checked; {inventory_link_audit['broken_count']} broken",
        },
        {
            "name": "manual_anchor_spotcheck_links_all_valid",
            "ok": (manual_anchor_link_audit is None) or manual_anchor_link_audit["broken_count"] == 0,
            "detail": "not checked" if manual_anchor_link_audit is None else f"{manual_anchor_link_audit['total_count']} links checked; {manual_anchor_link_audit['broken_count']} broken",
        },
        {
            "name": "regression_stability_comparison_passed",
            "ok": bool(stability_comparison and stability_comparison.get("ok")),
            "detail": "missing regression_stability_comparison.json" if stability_comparison is None else f"{stability_comparison.get('baseline_sample_count', '-')} baseline samples; {stability_comparison.get('current_sample_count', '-')} current samples; failures {stability_comparison.get('failure_count', '-')}; warnings {stability_comparison.get('warning_count', '-')}",
        },
        {
            "name": "human_review_all_approved",
            "ok": tested > 0 and accepted == tested and pending == 0 and needs_fix == 0 and bool(report.get("fully_accepted")),
            "detail": f"{accepted}/{tested} approved; pending {pending}; needs_fix {needs_fix}; fully_accepted={bool(report.get('fully_accepted'))}",
        },
    ]
    accepted_final = all(check["ok"] for check in checks)
    return {
        "accepted": accepted_final,
        "checks": checks,
        "false_gate_items": false_gate_items,
        "anchor_fail_items": anchor_fail_items,
        "inventory_link_audit": inventory_link_audit,
        "manual_anchor_link_audit": manual_anchor_link_audit,
        "stability_comparison": stability_comparison,
        "manual_pending_items": manual_pending_items,
        "manual_needs_fix_items": manual_needs_fix_items,
    }


def regression_stability_snapshot(report):
    samples = []
    for item in report.get("results") or []:
        qa = item.get("qa") or {}
        headings = qa.get("headings") or {}
        heading_rows = headings.get("all") or []
        heading_signature_source = "\n".join(
            f"{row.get('level')}|{normalize_space(row.get('text'))}" for row in heading_rows
        )
        h1_signature_source = "\n".join(normalize_space(text) for text in headings.get("h1") or [])
        anchor = qa.get("outline_anchor_integrity") or {}
        files = qa.get("files") or {}
        pages = qa.get("pages") or {}
        manual = item.get("manual_review") or {}
        samples.append({
            "pdf_id": item.get("pdf_id"),
            "job_id": item.get("job_id"),
            "pdf_name": item.get("pdf_name") or item.get("pdf_id"),
            "status": item.get("status"),
            "qa_ok": bool(qa.get("ok")),
            "false_gates": false_gates(qa),
            "manual_status": manual.get("status") or "pending",
            "heading_count": headings.get("count", 0),
            "heading_levels": headings.get("levels") or {},
            "heading_signature_sha256": hashlib.sha256(heading_signature_source.encode("utf-8")).hexdigest(),
            "h1_signature_sha256": hashlib.sha256(h1_signature_source.encode("utf-8")).hexdigest(),
            "h1_titles": headings.get("h1") or [],
            "anchor_ok": bool(anchor.get("ok")),
            "anchor_count": anchor.get("nav_count", 0),
            "image_refs": files.get("image_refs", 0),
            "image_files": files.get("image_files", 0),
            "missing_images": files.get("missing_images") or [],
            "first_page_idx": pages.get("first_page_idx"),
            "last_page_idx": pages.get("last_page_idx"),
            "review_flags": qa.get("review_flags") or [],
        })
    return {
        "schema": "pdf-clean-markdown-rebuild.regression-stability.v1",
        "generated_at": report.get("generated_at"),
        "tested_count": report.get("tested_count"),
        "passed_count": report.get("passed_count"),
        "failed_count": report.get("failed_count"),
        "accepted_count": report.get("accepted_count"),
        "pending_manual_review_count": report.get("pending_manual_review_count"),
        "needs_fix_count": report.get("needs_fix_count"),
        "fully_accepted": bool(report.get("fully_accepted")),
        "samples": samples,
    }


def write_regression_stability_snapshot(out_root, report):
    snapshot = regression_stability_snapshot(report)
    (out_root / "regression_stability_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_final_acceptance_gate(out_root, report):
    gate = final_acceptance_checks(report, out_root)
    status = "ACCEPTED" if gate["accepted"] else "NOT ACCEPTED"
    gate_json = {
        "schema": "pdf-clean-markdown-rebuild.final-acceptance-gate.v1",
        "status": status,
        "accepted": gate["accepted"],
        "checks": gate["checks"],
        "false_gate_items": gate["false_gate_items"],
        "anchor_fail_items": gate["anchor_fail_items"],
        "outline_inventory_link_audit": gate.get("inventory_link_audit"),
        "manual_anchor_link_audit": gate.get("manual_anchor_link_audit"),
        "regression_stability_comparison": gate.get("stability_comparison"),
        "manual_pending_items": gate["manual_pending_items"],
        "manual_needs_fix_items": gate["manual_needs_fix_items"],
    }
    (out_root / "final_acceptance_gate.json").write_text(
        json.dumps(gate_json, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Final Regression Acceptance Gate",
        "",
        f"Status: **{status}**",
        "",
        "This gate is the conservative final answer for whether the selected regression set can be considered accepted. Mechanical QA alone is not enough; every sample must also be explicitly approved in the human review ledger after outline-tree and `clean.md` anchor spot-checking.",
        "",
        "| Check | Status | Evidence |",
        "|---|---|---|",
    ]
    for check in gate["checks"]:
        lines.append(f"| {check['name']} | {'PASS' if check['ok'] else 'FAIL'} | {check['detail']} |")

    if gate["manual_pending_items"]:
        lines.extend([
            "",
            "## Pending Human Review",
            "",
        ])
        for label in gate["manual_pending_items"]:
            lines.append(f"- {label}")

    if gate["manual_needs_fix_items"]:
        lines.extend([
            "",
            "## Needs Fix",
            "",
        ])
        for label in gate["manual_needs_fix_items"]:
            lines.append(f"- {label}")

    if gate["false_gate_items"]:
        lines.extend([
            "",
            "## False QA Gates",
            "",
        ])
        for label, gates in gate["false_gate_items"]:
            lines.append(f"- {label}: {', '.join(gates)}")

    if gate["anchor_fail_items"]:
        lines.extend([
            "",
            "## Anchor Failures",
            "",
        ])
        for label, anchor in gate["anchor_fail_items"]:
            lines.append(f"- {label}: {anchor}")

    inventory_link_audit = gate.get("inventory_link_audit") or {}
    if inventory_link_audit.get("broken"):
        lines.extend([
            "",
            "## Outline Inventory Link Failures",
            "",
        ])
        for row in inventory_link_audit["broken"]:
            lines.append(f"- {row['pdf']} #{row['index']} {row['title']}: {row['href']}")

    manual_anchor_link_audit = gate.get("manual_anchor_link_audit") or {}
    if manual_anchor_link_audit.get("broken"):
        lines.extend([
            "",
            "## Manual Anchor Spotcheck Link Failures",
            "",
        ])
        for row in manual_anchor_link_audit["broken"]:
            lines.append(f"- {row['pdf']} {row['reason']} {row['title']}: {row['href']}")

    stability_comparison = gate.get("stability_comparison") or {}
    if stability_comparison.get("failures"):
        lines.extend([
            "",
            "## Regression Stability Failures",
            "",
        ])
        for row in stability_comparison["failures"]:
            lines.append(f"- {row.get('pdf', '-')}: {row.get('check', '-')} - {row.get('detail', '-')}")

    lines.extend([
        "",
        "## Required Next Step",
        "",
        "If status is NOT ACCEPTED, continue manual review or rule repair, refresh artifacts, and rerun this gate. Only status ACCEPTED should be used as the final 12-sample regression acceptance signal.",
    ])
    (out_root / "final_acceptance_gate.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_objective_completion_audit(out_root, report):
    gate = final_acceptance_checks(report, out_root)
    checks = {check["name"]: check for check in gate.get("checks") or []}
    tested = int(report.get("tested_count") or 0)
    accepted = int(report.get("accepted_count") or 0)
    pending = int(report.get("pending_manual_review_count") or 0)
    needs_fix = int(report.get("needs_fix_count") or 0)
    fully_accepted = bool(report.get("fully_accepted"))

    def check_status(name):
        check = checks.get(name) or {}
        return "PROVED" if check.get("ok") else "NOT PROVED"

    def check_detail(name, fallback="-"):
        check = checks.get(name) or {}
        return check.get("detail") or fallback

    rows = [
        {
            "requirement": "12 selected samples are included in the regression set",
            "evidence": f"`regression_report.json` / final gate report {tested} tested samples.",
            "status": "PROVED" if tested > 0 and len(report.get("results") or []) == tested else "NOT PROVED",
            "remaining": "None for sample inclusion." if tested > 0 else "Run or refresh the selected regression set.",
        },
        {
            "requirement": "Mechanical QA has no obvious red flags",
            "evidence": check_detail("mechanical_qa_all_passed"),
            "status": check_status("mechanical_qa_all_passed"),
            "remaining": "Repair failed samples and rerun regression." if check_status("mechanical_qa_all_passed") != "PROVED" else "None unless a later rule change triggers rerun.",
        },
        {
            "requirement": "False gates are absent",
            "evidence": check_detail("no_false_gates"),
            "status": check_status("no_false_gates"),
            "remaining": "Inspect `acceptance_audit.md` and repair false gates." if check_status("no_false_gates") != "PROVED" else "None unless a later rule change triggers rerun.",
        },
        {
            "requirement": "Outline anchor QA is valid and inspectable",
            "evidence": f"{check_detail('outline_anchor_integrity_all_passed')}; {check_detail('outline_inventory_links_all_valid')}; {check_detail('manual_anchor_spotcheck_links_all_valid')}.",
            "status": "PROVED" if checks.get("outline_anchor_integrity_all_passed", {}).get("ok") and checks.get("outline_inventory_links_all_valid", {}).get("ok") and checks.get("manual_anchor_spotcheck_links_all_valid", {}).get("ok") else "NOT PROVED",
            "remaining": "Regenerate or repair `outline-view.html`, anchor inventories, and manual spotcheck links." if not (checks.get("outline_anchor_integrity_all_passed", {}).get("ok") and checks.get("outline_inventory_links_all_valid", {}).get("ok") and checks.get("manual_anchor_spotcheck_links_all_valid", {}).get("ok")) else "Use `manual_anchor_spotcheck.html` for human review.",
        },
        {
            "requirement": "Regression stability shows no degradation from the previous working root",
            "evidence": check_detail("regression_stability_comparison_passed"),
            "status": check_status("regression_stability_comparison_passed"),
            "remaining": "Run `compare_regression_stability.py` against the previous working root." if check_status("regression_stability_comparison_passed") != "PROVED" else "Rerun comparison after any future generalized rule change.",
        },
        {
            "requirement": "Directory trees and clean.md anchors are manually accepted across all samples",
            "evidence": f"{accepted}/{tested} approved; pending {pending}; needs_fix {needs_fix}; fully_accepted={fully_accepted}.",
            "status": "PROVED" if checks.get("human_review_all_approved", {}).get("ok") else "NOT PROVED",
            "remaining": "User must explicitly approve or reject pending samples after checking outline trees and clean.md anchors." if not checks.get("human_review_all_approved", {}).get("ok") else "None.",
        },
        {
            "requirement": "Final acceptance gate says ACCEPTED",
            "evidence": "Final gate accepted=true." if gate.get("accepted") else "Final gate accepted=false.",
            "status": "PROVED" if gate.get("accepted") else "NOT PROVED",
            "remaining": "Refresh review artifacts and rerun `assert_final_acceptance.py` after all requirements are proved." if not gate.get("accepted") else "None.",
        },
    ]

    lines = [
        "# Objective Completion Audit",
        "",
        f"Regression root: `{out_root}`",
        "",
        "This audit maps the active 12-sample acceptance objective to current evidence. It is intentionally conservative: the goal is not complete until every requirement is proved by current artifacts.",
        "",
        "## Objective",
        "",
        "能在这 12 个样本上反复回归，目录树和正文锚点经人工抽查通过，机械 QA 无明显红旗，且每次修订后旧样本不退化。",
        "",
        "## Requirement Status",
        "",
        "| Requirement | Current evidence | Status | Remaining work |",
        "|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['requirement']} | {row['evidence']} | {row['status']} | {row['remaining']} |"
        )
    lines.extend([
        "",
        "## Current Blocking Item",
        "",
        "If any row above is `NOT PROVED`, keep the goal open. In normal review runs the final missing item is explicit human approval for pending samples.",
        "",
        "## Human Review Entry Points",
        "",
        "- `manual_anchor_spotcheck.html`",
        "- `pending_review_queue.html`",
        "- `manual_review_command_sheet.md`",
        "- `final_acceptance_gate.md`",
        "",
    ])
    (out_root / "objective_completion_audit.md").write_text("\n".join(lines), encoding="utf-8")

    html_rows = []
    for row in rows:
        status_class = "ok" if row["status"] == "PROVED" else "fail"
        html_rows.append(
            "<tr>"
            f"<td>{html.escape(row['requirement'])}</td>"
            f"<td>{html.escape(row['evidence'])}</td>"
            f"<td class=\"{status_class}\">{html.escape(row['status'])}</td>"
            f"<td>{html.escape(row['remaining'])}</td>"
            "</tr>"
        )
    html_text = f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<title>Objective Completion Audit</title>
<style>
body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif; color: #1f2933; background: #f6f7f9; }}
header {{ padding: 24px 32px 16px; background: #fff; border-bottom: 1px solid #d8dde6; }}
main {{ padding: 24px 32px 64px; }}
h1 {{ margin: 0 0 8px; font-size: 24px; }}
p {{ margin: 0; color: #52606d; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8dde6; }}
th, td {{ padding: 10px 12px; border-bottom: 1px solid #e4e7eb; text-align: left; vertical-align: top; font-size: 14px; }}
th {{ background: #f0f3f7; }}
.ok {{ color: #0f7b3f; font-weight: 700; }}
.fail {{ color: #b42318; font-weight: 700; }}
a {{ color: #0b5cad; text-decoration: none; font-weight: 600; }}
a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<header>
<h1>Objective Completion Audit</h1>
<p>Conservative requirement-by-requirement evidence for the 12-sample acceptance objective.</p>
</header>
<main>
<table>
<thead><tr><th>Requirement</th><th>Current evidence</th><th>Status</th><th>Remaining work</th></tr></thead>
<tbody>
{''.join(html_rows)}
</tbody>
</table>
<p style=\"margin-top:16px\"><a href=\"manual_anchor_spotcheck.html\">Manual anchor spotcheck</a> · <a href=\"final_acceptance_gate.md\">Final acceptance gate</a></p>
</main>
</body>
</html>
"""
    (out_root / "objective_completion_audit.html").write_text(html_text, encoding="utf-8")


def outline_inventory_rows(item, out_root):
    headings = ((item.get("qa") or {}).get("headings") or {}).get("all") or []
    rel = safe_relpath(Path(item.get("output_dir", "")) / "outline-view.html", out_root)
    clean_path = Path(item.get("output_dir", "")) / "clean.md"
    if clean_path.exists():
        line_count = len(clean_path.read_text(encoding="utf-8").splitlines())
    else:
        line_count = 0
    rows = []
    for idx, heading in enumerate(headings):
        line = int(heading.get("line") or 0)
        level = int(heading.get("level") or 0)
        end_line = line_count
        for next_heading in headings[idx + 1:]:
            next_level = int(next_heading.get("level") or 0)
            next_line = int(next_heading.get("line") or 0)
            if next_level and next_level <= level and next_line:
                end_line = max(line, next_line - 1)
                break
        rows.append({
            "index": idx + 1,
            "level": level,
            "title": heading.get("text") or "",
            "line": line,
            "end_line": end_line,
            "href": f"{rel}#h-{line}" if line else rel,
        })
    return rows


def write_outline_inventory(out_root, report):
    lines = [
        "# Outline Inventory",
        "",
        "This full inventory is for manual review of directory completeness, level consistency, and clean.md anchor chunk boundaries. Each heading links to the two-pane outline view at the inserted Markdown heading anchor.",
        "",
    ]
    html_sections = []
    for item in report.get("results") or []:
        label = item.get("pdf_name") or item.get("pdf_id")
        manual = (item.get("manual_review") or {}).get("status") or "pending"
        qa = item.get("qa") or {}
        headings = qa.get("headings") or {}
        levels = headings.get("levels") or {}
        rows = outline_inventory_rows(item, out_root)
        lines.extend([
            f"## {label}",
            "",
            f"- Human status: {manual}",
            f"- Heading count: {headings.get('count', 0)}; H1 {levels.get('1', 0)}; H2 {levels.get('2', 0)}; H3 {levels.get('3', 0)}",
            "",
        ])
        if not rows:
            lines.extend(["No headings found.", ""])
            html_rows = ['<tr><td colspan="5">No headings found.</td></tr>']
        else:
            lines.extend(["| # | Level | Heading | Anchor line | Chunk range |", "|---:|---:|---|---:|---|"])
            html_rows = []
            for row in rows:
                title = row["title"]
                indent = "&nbsp;" * max(0, row["level"] - 1) * 4
                lines.append(
                    f"| {row['index']} | H{row['level']} | "
                    f"[{title}]({row['href']}) | {row['line']} | {row['line']}-{row['end_line']} |"
                )
                html_rows.append(
                    "<tr>"
                    f"<td>{row['index']}</td>"
                    f"<td>H{row['level']}</td>"
                    f"<td>{indent}<a href=\"{html.escape(row['href'], quote=True)}\">{html.escape(title)}</a></td>"
                    f"<td>{row['line']}</td>"
                    f"<td>{row['line']}-{row['end_line']}</td>"
                    "</tr>"
                )
            lines.append("")
        html_sections.append(
            f"<section><h2>{html.escape(label)}</h2>"
            f"<p>Human status: {html.escape(manual)}; headings {html.escape(str(headings.get('count', 0)))}; "
            f"H1 {html.escape(str(levels.get('1', 0)))}; H2 {html.escape(str(levels.get('2', 0)))}; H3 {html.escape(str(levels.get('3', 0)))}</p>"
            "<table><thead><tr><th>#</th><th>Level</th><th>Heading anchor</th><th>Anchor line</th><th>Chunk range</th></tr></thead>"
            f"<tbody>{''.join(html_rows)}</tbody></table></section>"
        )
    (out_root / "outline_inventory.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    html_text = f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<title>Outline Inventory</title>
<style>
body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif; color: #1f2933; background: #f6f7f9; }}
header {{ padding: 24px 32px 16px; background: #fff; border-bottom: 1px solid #d8dde6; }}
main {{ padding: 24px 32px 64px; }}
h1 {{ margin: 0 0 8px; font-size: 24px; }}
h2 {{ margin: 30px 0 8px; font-size: 18px; }}
p {{ color: #52606d; margin: 0 0 10px; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8dde6; }}
th, td {{ padding: 7px 9px; border-bottom: 1px solid #e4e7eb; text-align: left; vertical-align: top; font-size: 13px; }}
th {{ background: #f0f3f7; position: sticky; top: 0; z-index: 1; }}
a {{ color: #0b5cad; text-decoration: none; font-weight: 600; }}
a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<header>
<h1>Outline Inventory</h1>
<p>Full heading list with anchor links and clean.md chunk ranges for manual directory review.</p>
</header>
<main>
{''.join(html_sections)}
</main>
</body>
</html>
"""
    (out_root / "outline_inventory.html").write_text(html_text, encoding="utf-8")


def outline_inventory_link_audit_result(out_root, report):
    rows = []
    broken = []
    total = 0
    for item in report.get("results") or []:
        label = item.get("pdf_name") or item.get("pdf_id")
        rows_for_item = outline_inventory_rows(item, out_root)
        outline_files = {}
        for row in rows_for_item:
            total += 1
            href = row.get("href") or ""
            outline_rel, _, fragment = href.partition("#")
            outline_path = out_root / outline_rel
            if outline_rel not in outline_files:
                if outline_path.exists():
                    outline_html = outline_path.read_text(encoding="utf-8")
                    section_ids = set(re.findall(r'<section class="slice" id="([^"]+)"', outline_html))
                    outline_files[outline_rel] = (True, section_ids)
                else:
                    outline_files[outline_rel] = (False, set())
            file_exists, section_ids = outline_files[outline_rel]
            target_exists = bool(fragment) and fragment in section_ids
            ok = file_exists and target_exists
            audit_row = {
                "pdf": label,
                "index": row.get("index"),
                "level": row.get("level"),
                "title": row.get("title"),
                "href": href,
                "fragment": fragment,
                "file_exists": file_exists,
                "target_exists": target_exists,
                "ok": ok,
            }
            rows.append(audit_row)
            if not ok:
                broken.append(audit_row)
    return {
        "total_count": total,
        "broken_count": len(broken),
        "rows": rows,
        "broken": broken,
    }


def write_outline_inventory_link_audit(out_root, report):
    audit = outline_inventory_link_audit_result(out_root, report)
    rows = audit["rows"]
    broken = audit["broken"]
    total = audit["total_count"]

    lines = [
        "# Outline Inventory Link Audit",
        "",
        "This audit verifies every link emitted by `outline_inventory.md/html` points to an existing two-pane outline view and a valid clean.md heading anchor.",
        "",
        f"- Total outline links: {total}",
        f"- Broken outline links: {len(broken)}",
        "",
        "| Status | PDF | # | Level | Heading | Link | Notes |",
        "|---|---|---:|---:|---|---|---|",
    ]
    for row in rows:
        status = "PASS" if row["ok"] else "FAIL"
        notes = []
        if not row["file_exists"]:
            notes.append("missing outline file")
        if not row["target_exists"]:
            notes.append("missing target anchor")
        if not notes:
            notes.append("-")
        lines.append(
            f"| {status} | {row['pdf']} | {row['index']} | H{row['level']} | "
            f"{row['title']} | [{row['fragment'] or 'top'}]({row['href']}) | {', '.join(notes)} |"
        )
    (out_root / "outline_inventory_link_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def dedupe_spotcheck_items(items):
    seen = set()
    result = []
    for item in items:
        key = (item.get("line"), normalize_title(item.get("title")), item.get("reason"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def select_manual_spotchecks(item):
    qa = item.get("qa") or {}
    headings = (qa.get("headings") or {}).get("all") or []
    anchor = qa.get("outline_anchor_integrity") or {}
    by_line = {int(heading.get("line")): heading for heading in headings if heading.get("line")}
    selected = []

    for intro in anchor.get("parent_intro_items") or []:
        line_text = str(intro.get("target") or "")
        match = re.fullmatch(r"h-(\d+)", line_text)
        if not match:
            continue
        line = int(match.group(1))
        heading = by_line.get(line)
        selected.append({
            "line": line,
            "title": intro.get("title") or (heading or {}).get("text") or "",
            "level": intro.get("level") or (heading or {}).get("level"),
            "reason": "parent_intro_boundary",
            "note": f"intro {intro.get('intro_range') or '-'} / nonempty {intro.get('intro_line_count')}",
        })

    if headings:
        candidate_indexes = {0, len(headings) // 2, len(headings) - 1}
        h3_indexes = [idx for idx, heading in enumerate(headings) if int(heading.get("level") or 0) == 3]
        if h3_indexes:
            candidate_indexes.add(h3_indexes[0])
            candidate_indexes.add(h3_indexes[len(h3_indexes) // 2])
            candidate_indexes.add(h3_indexes[-1])
        for idx in sorted(i for i in candidate_indexes if 0 <= i < len(headings)):
            heading = headings[idx]
            selected.append({
                "line": int(heading.get("line")),
                "title": heading.get("text") or "",
                "level": int(heading.get("level") or 0),
                "reason": "first_middle_last_or_h3",
                "note": f"heading index {idx + 1}/{len(headings)}",
            })

    for heading in headings:
        text = normalize_space(heading.get("text"))
        if re.search(r"\b(?:review|check your progress|practice|coursework|assessment|examination|benchmark|测试|复习|精选)\b", text, re.I):
            selected.append({
                "line": int(heading.get("line")),
                "title": text,
                "level": int(heading.get("level") or 0),
                "reason": "assessment_or_review_boundary",
                "note": "",
            })

    return dedupe_spotcheck_items(selected)[:16]


def write_manual_anchor_spotcheck(out_root, report):
    lines = [
        "# Manual Anchor Spotcheck",
        "",
        "Use these concrete anchors for human review. Each link opens the two-pane outline view at the exact clean.md heading anchor; check that the right pane starts at the correct heading and that the surrounding content belongs to that chunk.",
        "",
    ]
    for item in report["results"]:
        label = item.get("pdf_name") or item["pdf_id"]
        rel = safe_relpath(Path(item.get("output_dir", "")) / "outline-view.html", out_root)
        lines.extend([f"## {label}", ""])
        spotchecks = select_manual_spotchecks(item)
        if not spotchecks:
            lines.extend(["- No headings available for spotcheck.", ""])
            continue
        lines.append("| Reason | Heading | Level | Link | Note |")
        lines.append("|---|---|---:|---|---|")
        for check in spotchecks:
            line = int(check.get("line") or 0)
            href = f"{rel}#h-{line}" if line else rel
            title = check.get("title") or "-"
            lines.append(
                f"| {check.get('reason') or '-'} | {title} | H{check.get('level') or '-'} | "
                f"[line {line}]({href}) | {check.get('note') or '-'} |"
            )
        lines.append("")
    (out_root / "manual_anchor_spotcheck.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    sections = []
    for item in report["results"]:
        label = html.escape(item.get("pdf_name") or item["pdf_id"])
        rel = safe_relpath(Path(item.get("output_dir", "")) / "outline-view.html", out_root)
        rows = []
        for check in select_manual_spotchecks(item):
            line = int(check.get("line") or 0)
            href = html.escape(f"{rel}#h-{line}" if line else rel, quote=True)
            rows.append(
                "<tr>"
                f"<td>{html.escape(check.get('reason') or '-')}</td>"
                f"<td>H{html.escape(str(check.get('level') or '-'))}</td>"
                f"<td><a href=\"{href}\">{html.escape(check.get('title') or '-')}</a></td>"
                f"<td>{html.escape(check.get('note') or '-')}</td>"
                "</tr>"
            )
        if not rows:
            rows.append('<tr><td colspan="4">No headings available for spotcheck.</td></tr>')
        sections.append(
            f"<section><h2>{label}</h2>"
            "<table><thead><tr><th>Reason</th><th>Level</th><th>Heading anchor</th><th>Note</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></section>"
        )
    html_text = f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<title>Manual Anchor Spotcheck</title>
<style>
body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif; color: #1f2933; background: #f6f7f9; }}
header {{ padding: 24px 32px 16px; background: #fff; border-bottom: 1px solid #d8dde6; }}
main {{ padding: 24px 32px 64px; }}
h1 {{ margin: 0 0 8px; font-size: 24px; }}
h2 {{ margin: 30px 0 10px; font-size: 18px; }}
p {{ margin: 0; color: #52606d; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8dde6; }}
th, td {{ padding: 9px 11px; border-bottom: 1px solid #e4e7eb; text-align: left; vertical-align: top; font-size: 14px; }}
th {{ background: #f0f3f7; }}
a {{ color: #0b5cad; text-decoration: none; font-weight: 600; }}
a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<header>
<h1>Manual Anchor Spotcheck</h1>
<p>Click each heading anchor, then verify the right pane starts at the correct clean.md heading and the surrounding content belongs to that chunk.</p>
</header>
<main>
{''.join(sections)}
</main>
</body>
</html>
"""
    (out_root / "manual_anchor_spotcheck.html").write_text(html_text, encoding="utf-8")


def manual_anchor_link_audit_result(out_root, report):
    rows = []
    errors = []
    total = 0
    for item in report.get("results") or []:
        label = item.get("pdf_name") or item["pdf_id"]
        rel = safe_relpath(Path(item.get("output_dir", "")) / "outline-view.html", out_root)
        outline_path = out_root / rel
        outline_html = outline_path.read_text(encoding="utf-8") if outline_path.exists() else ""
        section_ids = set(re.findall(r'<section class="slice" id="([^"]+)"', outline_html))
        for check in select_manual_spotchecks(item):
            total += 1
            line = int(check.get("line") or 0)
            target = f"h-{line}"
            href = f"{rel}#{target}" if line else rel
            ok = outline_path.exists() and target in section_ids
            row = {
                "pdf": label,
                "title": check.get("title") or "",
                "reason": check.get("reason") or "",
                "href": href,
                "target": target,
                "ok": ok,
                "file_exists": outline_path.exists(),
                "target_exists": target in section_ids,
            }
            rows.append(row)
            if not ok:
                errors.append(row)
    return {
        "total_count": total,
        "broken_count": len(errors),
        "broken": errors,
        "rows": rows,
    }


def write_manual_anchor_link_audit(out_root, report):
    audit = manual_anchor_link_audit_result(out_root, report)
    lines = [
        "# Manual Anchor Link Audit",
        "",
        f"- Total links: {audit['total_count']}",
        f"- Broken links: {audit['broken_count']}",
        "",
        "| Status | PDF | Reason | Heading | Link |",
        "|---|---|---|---|---|",
    ]
    for row in audit["rows"]:
        status = "PASS" if row["ok"] else "FAIL"
        lines.append(
            f"| {status} | {row['pdf']} | {row['reason']} | {row['title']} | [{row['target']}]({row['href']}) |"
        )
    (out_root / "manual_anchor_link_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_review_index(out_root, report):
    rows = []
    manual_counts = report.get("manual_review_counts") or manual_review_counts(report.get("results") or [])
    for item in report["results"]:
        qa = item.get("qa") or {}
        headings = qa.get("headings") or {}
        pages = qa.get("pages") or {}
        ok = item["status"] == "ok" and qa.get("ok")
        label = html.escape(item.get("pdf_name") or item["pdf_id"])
        rel = html.escape(safe_relpath(Path(item.get("output_dir", "")) / "outline-view.html", out_root))
        h_levels = headings.get("levels") or {}
        gates = html.escape(", ".join(false_gates(qa)) or "-")
        flags = html.escape(", ".join(body_scope_flags(item)) or "-")
        anchor = qa.get("outline_anchor_integrity") or {}
        anchor_status = "PASS" if anchor.get("ok") else "FAIL"
        manual = item.get("manual_review") or {}
        manual_status = html.escape(manual.get("status") or "pending")
        rows.append(
            "<tr>"
            f"<td class=\"status {'ok' if ok else 'fail'}\">{'PASS' if ok else 'FAIL'}</td>"
            f"<td><a href=\"{rel}\">{label}</a><div class=\"ids\">{html.escape(item['pdf_id'])}<br>{html.escape(item['job_id'])}</div></td>"
            f"<td>{pages.get('first_page_idx', '-')}-{pages.get('last_page_idx', '-')}</td>"
            f"<td>{headings.get('count', 0)} / H1 {h_levels.get('1', 0)} / H2 {h_levels.get('2', 0)} / H3 {h_levels.get('3', 0)}</td>"
            f"<td>{anchor_status} / {anchor.get('nav_count', 0)} anchors</td>"
            f"<td class=\"human {manual_status}\">{manual_status}</td>"
            f"<td>{gates}</td>"
            f"<td>{flags}</td>"
            "</tr>"
        )
    html_text = f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<title>PDF Clean Markdown Regression Review</title>
<style>
body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif; color: #1f2933; background: #f6f7f9; }}
header {{ padding: 24px 32px 16px; background: #fff; border-bottom: 1px solid #d8dde6; }}
h1 {{ margin: 0 0 8px; font-size: 24px; }}
.summary {{ display: flex; gap: 16px; flex-wrap: wrap; color: #52606d; }}
.links {{ margin-top: 14px; display: flex; gap: 12px; flex-wrap: wrap; }}
.links a {{ color: #0b5cad; text-decoration: none; font-weight: 600; }}
main {{ padding: 24px 32px; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8dde6; }}
th, td {{ padding: 10px 12px; border-bottom: 1px solid #e4e7eb; text-align: left; vertical-align: top; font-size: 14px; }}
th {{ background: #f0f3f7; position: sticky; top: 0; z-index: 1; }}
.status {{ font-weight: 700; }}
.status.ok {{ color: #0f7b3f; }}
.status.fail {{ color: #b42318; }}
.human {{ font-weight: 700; }}
.human.approved, .human.pass, .human.passed {{ color: #0f7b3f; }}
.human.needs_fix, .human.fail, .human.failed {{ color: #b42318; }}
.human.pending {{ color: #8a5a00; }}
.ids {{ color: #7b8794; font-size: 12px; margin-top: 4px; line-height: 1.4; }}
</style>
</head>
<body>
<header>
<h1>PDF Clean Markdown Regression Review</h1>
<div class=\"summary\">
<span>Generated: {html.escape(report['generated_at'])}</span>
<span>Tested: {report['tested_count']}</span>
<span>Passed: {report['passed_count']}</span>
<span>Failed: {report['failed_count']}</span>
<span>Human approved: {manual_counts['approved']}/{report['tested_count']}</span>
<span>Fully accepted: {'yes' if report.get('fully_accepted') else 'no'}</span>
</div>
<div class=\"links\">
<a href=\"regression_report.md\">Regression report</a>
<a href=\"final_acceptance_gate.md\">Final acceptance gate</a>
<a href=\"final_acceptance_gate.json\">Final gate JSON</a>
<a href=\"objective_completion_audit.html\">Objective audit</a>
<a href=\"regression_stability_snapshot.json\">Stability snapshot</a>
<a href=\"regression_stability_comparison.md\">Stability comparison</a>
<a href=\"acceptance_audit.md\">Acceptance audit</a>
<a href=\"outline_inventory.html\">Outline inventory</a>
<a href=\"outline_inventory_link_audit.md\">Outline inventory link audit</a>
<a href=\"outline_fact_reconciliation.md\">Outline reconciliation</a>
<a href=\"body_scope_audit.md\">Body scope audit</a>
<a href=\"anchor_integrity_audit.md\">Anchor integrity</a>
<a href=\"manual_anchor_link_audit.md\">Anchor link audit</a>
<a href=\"manual_anchor_spotcheck.html\">Anchor spotcheck</a>
<a href=\"manual_review_checklist.md\">Manual review checklist</a>
<a href=\"manual_review_decision_sheet.html\">Manual review decision sheet</a>
<a href=\"manual_review_command_sheet.md\">Manual review commands</a>
<a href=\"pending_manual_fact_review_summary.md\">Pending fact summary</a>
<a href=\"human_review_status.html\">Human review status</a>
<a href=\"pending_review_queue.html\">Pending review queue</a>
<a href=\"pending_review_link_audit.md\">Pending queue link audit</a>
<a href=\"manual_review_status_template.json\">Review status template</a>
</div>
</header>
<main>
<table>
<thead><tr><th>QA</th><th>PDF / outline review</th><th>Pages</th><th>Headings</th><th>Anchor QA</th><th>Human review</th><th>False gates</th><th>Scope flags</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
</main>
</body>
</html>
"""
    (out_root / "review-index.html").write_text(html_text, encoding="utf-8")


def write_review_artifacts(out_root, report):
    write_regression_stability_snapshot(out_root, report)
    write_final_acceptance_gate(out_root, report)
    write_objective_completion_audit(out_root, report)
    write_acceptance_audit(out_root, report)
    write_outline_inventory(out_root, report)
    write_outline_inventory_link_audit(out_root, report)
    write_outline_fact_reconciliation(out_root, report)
    write_body_scope_audit(out_root, report)
    write_anchor_integrity_audit(out_root, report)
    write_manual_anchor_spotcheck(out_root, report)
    write_manual_anchor_link_audit(out_root, report)
    write_manual_review_checklist(out_root, report)
    write_manual_review_decision_sheet(out_root, report)
    write_manual_review_command_sheet(out_root, report)
    write_pending_manual_fact_review_summary(out_root, report)
    write_human_review_status(out_root, report)
    write_manual_review_status_template(out_root, report)
    write_pending_review_queue(out_root, report)
    write_pending_review_link_audit(out_root, report)
    write_review_index(out_root, report)


def heading_level_jumps(markdown):
    jumps = []
    previous_level = 0
    previous_heading = None
    for line_no, line in enumerate(markdown.splitlines(), 1):
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if not match:
            continue
        level = len(match.group(1))
        text = normalize_space(match.group(2))
        if previous_level and level > previous_level + 1:
            jumps.append({
                "line": line_no,
                "level": level,
                "text": text,
                "previous_level": previous_level,
                "previous_text": previous_heading,
            })
        previous_level = level
        previous_heading = text
    return jumps


def unit_only_level_issues(markdown):
    headings = []
    for line_no, line in enumerate(markdown.splitlines(), 1):
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if not match:
            continue
        headings.append({"line": line_no, "level": len(match.group(1)), "text": normalize_space(match.group(2))})
    unit_headings = [h for h in headings if re.match(r"^Unit\s+\d+\b", h["text"], re.I)]
    if len(unit_headings) < 2:
        return []
    has_chapter_or_part = any(re.match(r"^(?:Chapter\b|CHAPTER\b|Part\s+\d+\b)", h["text"], re.I) for h in headings)
    if has_chapter_or_part:
        return []
    first_level = unit_headings[0]["level"]
    return [h for h in unit_headings if h["level"] != first_level]


def leaf_empty_heading_chunks(markdown):
    lines = markdown.splitlines()
    headings = []
    for line_no, line in enumerate(lines, 1):
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            headings.append({
                "line": line_no,
                "level": len(match.group(1)),
                "text": normalize_space(match.group(2)),
            })
    empty = []
    for idx, heading in enumerate(headings):
        next_heading = headings[idx + 1] if idx + 1 < len(headings) else None
        is_parent = bool(next_heading and int(next_heading["level"]) > int(heading["level"]))
        if is_parent:
            continue
        end = int(next_heading["line"]) - 1 if next_heading else len(lines)
        meaningful = []
        for raw in lines[int(heading["line"]):end]:
            text = raw.strip()
            if not text:
                continue
            if text.startswith("<!-- page_idx:"):
                continue
            if re.match(r"^#{1,6}\s+", text):
                continue
            meaningful.append(text)
        if not meaningful:
            empty.append({
                "line": heading["line"],
                "level": heading["level"],
                "text": heading["text"],
                "range": f"{heading['line']}-{end}",
            })
    return empty


def expected_heading_title(item):
    text = normalize_space(item.get("expected"))
    match = re.match(r"^#{1,6}\s+(.+)$", text)
    if match:
        return normalize_title(match.group(1))
    return normalize_title(text)


def filter_existing_missing_expected(missing_expected, headings):
    existing = {normalize_title(item.get("text")) for item in headings.get("all") or []}
    filtered = []
    ignored = []
    for item in missing_expected:
        title = expected_heading_title(item.get("item") or item)
        if title and title in existing:
            ignored.append(item)
        else:
            filtered.append(item)
    return filtered, ignored


def qa_output(task_dir):
    body_dir = task_dir / "body-final"
    clean_md = body_dir / "clean.md"
    preview_html = body_dir / "preview.html"
    outline_view = body_dir / "outline-view.html"
    outline_anchor_check = body_dir / "outline-anchor-check.html"
    manifest_path = body_dir / "manifest.json"
    popo_outline_path = body_dir / "popo_outline.json"
    apply_report_path = body_dir / "outline_apply_report.json"
    validation_path = body_dir / "outline_candidate_validation.json"
    if not clean_md.exists():
        return {"ok": False, "failure": "missing clean.md"}
    markdown = clean_md.read_text(encoding="utf-8")
    html = preview_html.read_text(encoding="utf-8") if preview_html.exists() else ""
    outline_html = ""
    if outline_view.exists():
        outline_html = outline_view.read_text(encoding="utf-8")
    elif outline_anchor_check.exists():
        outline_html = outline_anchor_check.read_text(encoding="utf-8")
    anchor_integrity = audit_outline_anchor_integrity(body_dir)
    refs = extract_refs(markdown)
    missing = [ref for ref in refs if not (body_dir / ref).exists()]
    image_files = len([p for p in (body_dir / "images").rglob("*") if p.is_file()]) if (body_dir / "images").exists() else 0
    page_indexes = [int(x) for x in re.findall(r"page_idx:\s*(\d+)", markdown)]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    popo_outline = json.loads(popo_outline_path.read_text(encoding="utf-8")) if popo_outline_path.exists() else {}
    apply_report = json.loads(apply_report_path.read_text(encoding="utf-8")) if apply_report_path.exists() else {}
    raw_missing_expected = [
        item for item in apply_report.get("skipped", [])
        if item.get("kind") == "missing_expected"
    ]
    validation_report = json.loads(validation_path.read_text(encoding="utf-8")) if validation_path.exists() else {}
    review_report_path = body_dir / "outline_llm_review.json"
    review_report = json.loads(review_report_path.read_text(encoding="utf-8")) if review_report_path.exists() else {}
    llm_review_errors = review_report.get("errors") or []
    start_excerpt = markdown[:3000]
    front_noise = any(token in start_excerpt for token in [
        "Copyright ©",
        "\n## Contents\n",
        "\n# Contents\n",
        "\n## 目录\n",
        "\n# 目录\n",
        "\n## Introduction\n",
        "\n# Introduction\n",
        "\n## How to use this book\n",
        "\n# How to use this book\n",
    ])
    tail_noise = "Benchmark and Unit Tests" in markdown[-1500:] and "Grade K" in markdown[-1500:]
    headings = heading_summary(markdown)
    missing_expected, ignored_missing_expected = filter_existing_missing_expected(raw_missing_expected, headings)
    page_count = len(set(page_indexes))
    suspect_h1 = [
        text for text in headings["h1"]
        if re.fullmatch(r"\d+|Sample|JOURNEYS|Contents|Benchmark and Unit Tests", text, re.I)
        or re.match(r"^(Exercise|Table)\s+[A-Z]?\d+(?:\.\d+)?\b", text, re.I)
        or re.match(r"^[•●\\-]\s+", text)
        or re.match(r"^['\"‘“].{8,}['\"’”]?$", text)
    ]
    too_many_h1 = page_count > 20 and len(headings["h1"]) > max(30, page_count // 4)
    review_flags = []
    if not headings["h1"] and headings["count"]:
        review_flags.append("no_h1_top_level_structure")
    if too_many_h1:
        review_flags.append("h1_density_too_high")
    if front_noise:
        review_flags.append("front_matter_like_heading_near_start")
    if page_indexes and page_indexes[0] > 50:
        review_flags.append("late_body_start_review")
    if tail_noise:
        review_flags.append("tail_matter_like_content_near_end")
    polluted_h2 = chapter_h2_pollution(markdown)
    if polluted_h2:
        review_flags.append("chapter_h2_not_unit_title")
    has_deep_headings = any(int(level) > 3 and count for level, count in headings["levels"].items())
    if has_deep_headings:
        review_flags.append("heading_depth_exceeds_3")
    h2_count = len(headings.get("h2") or [])
    if page_count >= 50 and h2_count / max(page_count, 1) > 2.0:
        review_flags.append("h2_density_high_review")
    floating_pre_h1 = floating_headings_before_h1(markdown)
    if floating_pre_h1:
        review_flags.append("floating_headings_before_h1")
    duplicate_h1 = duplicate_h1_titles(headings)
    if duplicate_h1:
        review_flags.append("duplicate_h1_titles")
    duplicate_topic_numbers = duplicate_topic_numbers_by_parent(markdown)
    if duplicate_topic_numbers:
        review_flags.append("duplicate_topic_numbers_by_parent")
    generic_topic_h = generic_chapter_topic_headings(markdown)
    if generic_topic_h:
        review_flags.append("generic_chapter_topic_heading")
    back_matter_h = back_matter_headings(markdown)
    if back_matter_h:
        review_flags.append("back_matter_heading_present")
    level_jumps = heading_level_jumps(markdown)
    if level_jumps:
        review_flags.append("heading_level_jump")
    unit_level_issues = unit_only_level_issues(markdown)
    if unit_level_issues:
        review_flags.append("unit_only_level_inconsistent")
    empty_leaf_chunks = leaf_empty_heading_chunks(markdown)
    if empty_leaf_chunks:
        review_flags.append("leaf_heading_empty_chunk")
    popo_available = bool(popo_outline.get("available"))
    popo_entries = popo_outline.get("outline") or []
    evidence_popo_outline = ((manifest.get("canonical_outline_evidence") or {}).get("popo_outline") or {})
    evidence_outline_entries = evidence_popo_outline.get("outline") or []
    if not popo_available:
        review_flags.append("popo_outline_missing_or_unavailable")
    if popo_available and not popo_entries:
        review_flags.append("popo_outline_empty")
    canonical_outline_gaps = canonical_outline_emission_gaps(evidence_outline_entries, popo_entries) if evidence_outline_entries else []
    if canonical_outline_gaps:
        review_flags.append("canonical_outline_not_fully_emitted")
    outline_heading_issues = outline_heading_mismatches(popo_entries, headings.get("all") or []) if popo_available else []
    if outline_heading_issues:
        review_flags.append("outline_headings_do_not_match_markdown")
    if not anchor_integrity.get("ok"):
        review_flags.append("outline_anchor_integrity_failed")
    validation_decisions = {
        item.get("candidate_id"): item
        for item in validation_report.get("decisions") or []
    }
    unvalidated_outline_entries = []
    rejected_outline_entries = []
    for idx, entry in enumerate(popo_entries):
        if not entry.get("validation_required"):
            continue
        cid = f"{idx}:{entry.get('start_page')}:{entry.get('level')}:{normalize_space(entry.get('title'))}"
        decision = validation_decisions.get(cid)
        item = {
            "candidate_id": cid,
            "title": entry.get("title"),
            "level": entry.get("level"),
            "start_page": entry.get("start_page"),
            "reason": entry.get("validation_required"),
            "source": entry.get("source"),
        }
        if not decision:
            unvalidated_outline_entries.append(item)
        elif decision.get("decision") not in {"accept", "revise"}:
            item["decision"] = decision
            rejected_outline_entries.append(item)
    if unvalidated_outline_entries:
        review_flags.append("popo_outline_has_unvalidated_inferred_headings")
    if rejected_outline_entries:
        review_flags.append("popo_outline_has_rejected_inferred_headings")
    if validation_report.get("errors"):
        review_flags.append("outline_candidate_validation_errors")
    visual_review_requested = (
        validation_report.get("verdict") == "needs_visual_review"
        or any(bool(item.get("needs_visual_review")) for item in validation_decisions.values())
    )
    if visual_review_requested:
        review_flags.append("outline_candidate_visual_review_requested")
    gates = {
        "has_required_files": all((body_dir / name).exists() for name in ["clean.md", "preview.html", "manifest.json", "qa_report.md", "images", "popo_outline.json"]) and bool(outline_html),
        "outline_anchor_check_present": bool(outline_html),
        "outline_anchor_check_has_links": 'class="nav-item' in outline_html and 'class="slice"' in outline_html,
        "outline_anchor_integrity_ok": bool(anchor_integrity.get("ok")),
        "popo_outline_available": popo_available,
        "popo_outline_nonempty": bool(popo_entries),
        "canonical_outline_fully_emitted": not canonical_outline_gaps,
        "outline_headings_match_markdown": not outline_heading_issues,
        "no_missing_images": not missing,
        "mathjax_present": "MathJax" in html,
        "has_headings": headings["count"] > 0,
        "top_level_structure_present": bool(headings["h1"]),
        "no_floating_headings_before_h1": not floating_pre_h1,
        "no_duplicate_h1_titles": not duplicate_h1,
        "no_duplicate_topic_numbers_by_parent": not duplicate_topic_numbers,
        "no_generic_chapter_topic_headings": not generic_topic_h,
        "no_back_matter_headings": not back_matter_h,
        "no_heading_level_jumps": not level_jumps,
        "unit_only_levels_consistent": not unit_level_issues,
        "no_empty_leaf_heading_chunks": not empty_leaf_chunks,
        "no_suspect_h1": not suspect_h1,
        "h1_density_reasonable": not too_many_h1,
        "chapter_h2_matches_unit_titles": not polluted_h2,
        "heading_depth_at_most_3": not has_deep_headings,
        "no_missing_expected_headings": not missing_expected,
        "popo_outline_inferred_headings_validated": not unvalidated_outline_entries and not rejected_outline_entries and not validation_report.get("errors") and not validation_report.get("truncated") and not visual_review_requested,
        "llm_review_no_errors": not llm_review_errors,
    }
    return {
        "ok": all(gates.values()),
        "gates": gates,
        "manifest": {
            "included_page_range": manifest.get("included_page_range"),
            "inferred_body_range": manifest.get("inferred_body_range"),
            "skipped_scope_counts": manifest.get("skipped_scope_counts"),
            "included_type_counts": manifest.get("included_type_counts"),
            "dropped_noise_counts": manifest.get("dropped_noise_counts"),
            "empty_leaf_heading_markers": manifest.get("empty_leaf_heading_markers"),
        },
        "files": {
            "clean_md_bytes": clean_md.stat().st_size,
            "preview_html_bytes": preview_html.stat().st_size if preview_html.exists() else 0,
            "image_refs": len(refs),
            "image_files": image_files,
            "missing_images": missing[:50],
        },
        "popo_outline": {
            "available": popo_available,
            "tree_file": popo_outline.get("tree_file"),
            "body_start_page_idx": popo_outline.get("body_start_page_idx"),
            "entry_count": len(popo_entries),
            "canonical_evidence_entry_count": len(evidence_outline_entries),
            "canonical_emission_gap_count": len(canonical_outline_gaps),
            "first_entries": popo_entries[:20],
        },
        "pages": {
            "first_page_idx": page_indexes[0] if page_indexes else None,
            "last_page_idx": page_indexes[-1] if page_indexes else None,
            "count": len(set(page_indexes)),
        },
        "headings": headings,
        "suspect_h1": suspect_h1,
        "chapter_h2_pollution": polluted_h2[:30],
        "floating_headings_before_h1": floating_pre_h1[:30],
        "duplicate_h1_titles": duplicate_h1[:30],
        "duplicate_topic_numbers_by_parent": duplicate_topic_numbers[:30],
        "generic_chapter_topic_headings": generic_topic_h[:30],
        "back_matter_headings": back_matter_h[:30],
        "heading_level_jumps": level_jumps[:30],
        "leaf_empty_heading_chunks": empty_leaf_chunks[:30],
        "outline_heading_mismatches": outline_heading_issues[:30],
        "canonical_outline_emission_gaps": canonical_outline_gaps[:30],
        "outline_anchor_integrity": anchor_integrity,
        "unit_only_level_issues": unit_level_issues[:30],
        "missing_expected_headings": missing_expected[:30],
        "ignored_missing_expected_headings": ignored_missing_expected[:30],
        "unvalidated_outline_entries": unvalidated_outline_entries[:50],
        "rejected_outline_entries": rejected_outline_entries[:50],
        "outline_candidate_validation": {
            "exists": validation_path.exists(),
            "verdict": validation_report.get("verdict"),
            "candidate_count": validation_report.get("candidate_count"),
            "validated_candidate_count": validation_report.get("validated_candidate_count"),
            "truncated": validation_report.get("truncated"),
            "errors": validation_report.get("errors")[:10] if validation_report.get("errors") else [],
        },
        "review_flags": review_flags,
        "llm_apply": {
            "applied_count": apply_report.get("applied_count"),
            "skipped_count": apply_report.get("skipped_count"),
        },
        "llm_review_errors": llm_review_errors[:20],
    }


def run_one(task, args):
    label = f"{task['pdf_id']}__{task['job_id']}"
    task_dir = args.out_root / label
    if task_dir.exists() and args.force:
        shutil.rmtree(task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)
    body_dir = task_dir / "body-final"
    commands = []
    warnings = []
    status = "ok"
    error = None
    try:
        cmd = [
            sys.executable, str(SCRIPT_DIR / "materialize_minio_task.py"),
            "--pdf-id", task["pdf_id"],
            "--job-id", task["job_id"],
            "--out-dir", str(task_dir),
            "--container", args.container,
            "--input-bucket", args.input_bucket,
            "--mineru-bucket", args.mineru_bucket,
            "--minerupopo-bucket", args.minerupopo_bucket,
            "--raw-bucket", args.raw_bucket,
            "--force",
        ]
        result = run(cmd, timeout=args.command_timeout)
        commands.append(result)
        require_ok(result)

        cmd = [
            sys.executable, str(SCRIPT_DIR / "bootstrap_clean_markdown.py"),
            str(task_dir / "rebuild_input"),
            "--out-dir", str(body_dir),
        ]
        result = run(cmd, timeout=args.command_timeout)
        commands.append(result)
        require_ok(result)

        if args.with_deepseek:
            validation_path = body_dir / "outline_candidate_validation.json"
            cmd = [
                sys.executable, str(SCRIPT_DIR / "deepseek_outline_candidate_validate.py"),
                str(task_dir / "rebuild_input"),
                str(body_dir / "popo_outline.json"),
                "--out", str(validation_path),
                "--timeout", str(args.deepseek_request_timeout),
                "--max-candidates", str(args.deepseek_candidate_max_candidates),
            ]
            result = run(cmd, timeout=args.deepseek_timeout)
            commands.append(result)
            if result["returncode"] != 0:
                if validation_path.exists():
                    warnings.append("deepseek_outline_candidate_validate returned non-zero but wrote validation JSON; QA will use available decisions.")
                else:
                    require_ok(result)

            if args.with_hyvision:
                cmd = [
                    sys.executable, str(SCRIPT_DIR / "hyvision_outline_candidate_validate.py"),
                    str(task_dir / "rebuild_input"),
                    str(body_dir / "popo_outline.json"),
                    str(validation_path),
                    "--timeout", str(args.hyvision_request_timeout),
                    "--max-candidates", str(args.hyvision_candidate_max_candidates),
                ]
                result = run(cmd, timeout=args.hyvision_timeout)
                commands.append(result)
                if result["returncode"] != 0:
                    if validation_path.exists():
                        warnings.append("hyvision_outline_candidate_validate returned non-zero but wrote validation JSON; QA will use available visual decisions.")
                    else:
                        require_ok(result)

            cmd = [
                sys.executable, str(SCRIPT_DIR / "apply_outline_candidate_validation.py"),
                str(body_dir),
                "--title", task.get("pdf_name") or task["pdf_id"],
            ]
            result = run(cmd, timeout=args.command_timeout)
            commands.append(result)
            require_ok(result)

            review_path = body_dir / "outline_llm_review.json"
            cmd = [
                sys.executable, str(SCRIPT_DIR / "deepseek_outline_review.py"),
                str(body_dir / "clean.md"),
                "--out", str(review_path),
                "--timeout", str(args.deepseek_request_timeout),
                "--max-headings-per-unit", str(args.deepseek_max_headings_per_unit),
                "--batch-size", str(args.deepseek_batch_size),
            ]
            if args.deepseek_batch_document:
                cmd.append("--batch-document")
            result = run(cmd, timeout=args.deepseek_timeout)
            commands.append(result)
            if result["returncode"] != 0:
                if review_path.exists():
                    warnings.append("deepseek_outline_review returned non-zero but wrote review JSON; continuing with available structured suggestions.")
                else:
                    require_ok(result)

            cmd = [
                sys.executable, str(SCRIPT_DIR / "apply_outline_review.py"),
                str(body_dir / "clean.md"),
                str(review_path),
                "--out-md", str(body_dir / "clean.md"),
                "--out-html", str(body_dir / "preview.html"),
                "--title", task.get("pdf_name") or task["pdf_id"],
            ]
            result = run(cmd, timeout=args.command_timeout)
            commands.append(result)
            require_ok(result)

        source_trace = task_dir / "source_trace.json"
        if source_trace.exists():
            shutil.copy2(source_trace, body_dir / "source_trace.json")
    except Exception as exc:
        status = "failed"
        error = str(exc)

    qa = qa_output(task_dir) if status == "ok" else {"ok": False, "failure": error}
    summary = {
        "pdf_name": task.get("pdf_name"),
        "pdf_id": task["pdf_id"],
        "job_id": task["job_id"],
        "source_hash": task.get("source_hash"),
        "source_pdf_sha256": task.get("source_pdf_sha256"),
        "status": status,
        "error": error,
        "warnings": warnings,
        "output_dir": str(body_dir),
        "qa": qa,
        "commands": [
            {
                "cmd": " ".join(str(part) for part in item["cmd"]),
                "returncode": item["returncode"],
                "elapsed_seconds": item["elapsed_seconds"],
                "stderr_tail": item["stderr"][-1200:],
                "stdout_tail": item["stdout"][-1200:],
            }
            for item in commands
        ],
    }
    (task_dir / "regression_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def write_reports(out_root, discovery, results):
    manual_counts = manual_review_counts(results)
    mechanical_passed = sum(1 for item in results if item["status"] == "ok" and item["qa"].get("ok"))
    mechanical_failed = sum(1 for item in results if item["status"] != "ok" or not item["qa"].get("ok"))
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "buckets": discovery["buckets"],
        "total_task_count": discovery["total_task_count"],
        "tested_count": len(results),
        "passed_count": mechanical_passed,
        "failed_count": mechanical_failed,
        "accepted_count": manual_counts["approved"],
        "pending_manual_review_count": manual_counts["pending"],
        "needs_fix_count": manual_counts["needs_fix"],
        "manual_review_counts": manual_counts,
        "fully_accepted": mechanical_failed == 0 and manual_counts["approved"] == len(results) and manual_counts["needs_fix"] == 0,
        "results": results,
    }
    (out_root / "regression_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# PDF Clean Markdown Rebuild Regression Report",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Total MinIO tasks: {report['total_task_count']}",
        f"- Tested: {report['tested_count']}",
        f"- Passed: {report['passed_count']}",
        f"- Failed: {report['failed_count']}",
        f"- Human approved: {report['accepted_count']}/{report['tested_count']}",
        f"- Human pending: {report['pending_manual_review_count']}",
        f"- Human needs fix: {report['needs_fix_count']}",
        f"- Fully accepted: {'yes' if report['fully_accepted'] else 'no'}",
        "",
        "| Result | PDF | Pages | Images | H1 | Notes |",
        "|---|---|---:|---:|---|---|",
    ]
    for item in results:
        qa = item.get("qa") or {}
        ok = item["status"] == "ok" and qa.get("ok")
        pages = qa.get("pages", {})
        files = qa.get("files", {})
        headings = qa.get("headings", {})
        failed_gates = []
        for gate, passed in (qa.get("gates") or {}).items():
            if not passed:
                failed_gates.append(gate)
        notes = "; ".join(failed_gates) or item.get("error") or ""
        review_flags = qa.get("review_flags") or []
        if review_flags:
            notes = (notes + "; " if notes else "") + "review: " + ",".join(review_flags)
        page_span = f"{pages.get('first_page_idx')}-{pages.get('last_page_idx')}" if pages else "-"
        image_summary = f"{files.get('image_refs', '-')}/{files.get('image_files', '-')}"
        h1 = ", ".join((headings.get("h1") or [])[:8])
        lines.append(f"| {'PASS' if ok else 'FAIL'} | {item.get('pdf_name') or item['pdf_id']} | {page_span} | {image_summary} | {h1} | {notes} |")
    (out_root / "regression_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_review_artifacts(out_root, report)
    return report


def main():
    parser = argparse.ArgumentParser(description="Run local regression rebuilds for MinIO eduassets-minerupopo tasks without publishing to MinIO.")
    parser.add_argument("--container", default="minio")
    parser.add_argument("--input-bucket", default=DEFAULT_BUCKETS["input"])
    parser.add_argument("--mineru-bucket", default=DEFAULT_BUCKETS["mineru"])
    parser.add_argument("--minerupopo-bucket", default=DEFAULT_BUCKETS["minerupopo"])
    parser.add_argument("--raw-bucket", default=DEFAULT_BUCKETS["raw"])
    parser.add_argument("--pdf-id")
    parser.add_argument("--job-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out-root", type=Path, default=Path("/tmp/pdf-clean-md-regression"))
    parser.add_argument("--manual-review-status", type=Path, help="Optional JSON ledger with human review status keyed by pdf_id and/or job_id.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--with-deepseek", action="store_true")
    parser.add_argument("--with-hyvision", action="store_true", help="Use HY Vision to resolve candidate headings marked needs_visual_review after DeepSeek candidate validation.")
    parser.add_argument("--command-timeout", type=int, default=600, help="Timeout in seconds for non-LLM helper commands.")
    parser.add_argument("--deepseek-timeout", type=int, default=900, help="Total timeout in seconds for the DeepSeek outline review subprocess.")
    parser.add_argument("--deepseek-request-timeout", type=int, default=60, help="Per-request timeout passed to deepseek_outline_review.py.")
    parser.add_argument("--deepseek-candidate-max-candidates", type=int, default=200, help="Maximum inferred outline candidates validated in one DeepSeek candidate pass.")
    parser.add_argument("--deepseek-max-headings-per-unit", type=int, default=80, help="Maximum headings sent to DeepSeek per Unit/Chapter review payload.")
    parser.add_argument("--deepseek-batch-size", type=int, default=4, help="Maximum Unit/Chapter payloads per batch-document request.")
    parser.add_argument("--hyvision-timeout", type=int, default=1800, help="Total timeout in seconds for HY Vision visual candidate validation.")
    parser.add_argument("--hyvision-request-timeout", type=int, default=90, help="Per-request timeout passed to hyvision_outline_candidate_validate.py.")
    parser.add_argument("--hyvision-candidate-max-candidates", type=int, default=40, help="Maximum visual-review candidates resolved in one HY Vision pass.")
    parser.add_argument("--no-deepseek-batch-document", dest="deepseek_batch_document", action="store_false", help="Disable one-request compacted document review and call DeepSeek per Unit/Chapter.")
    parser.set_defaults(deepseek_batch_document=True)
    args = parser.parse_args()

    if args.raw_bucket != DEFAULT_BUCKETS["raw"]:
        raise SystemExit("Unsafe configuration: regression may only target eduassets-raw as the raw bucket, and it never publishes.")
    args.out_root.mkdir(parents=True, exist_ok=True)
    manual_status = load_manual_review_status(args.manual_review_status)
    discovery, tasks = discover_tasks(args)
    results = []
    for idx, task in enumerate(tasks, 1):
        print(f"[{idx}/{len(tasks)}] {task.get('pdf_name') or task['pdf_id']} :: {task['pdf_id']} / {task['job_id']}", flush=True)
        result = run_one(task, args)
        result["manual_review"] = manual_review_for(result, manual_status)
        results.append(result)
        print(f"  -> {result['status']} qa_ok={result.get('qa', {}).get('ok')}", flush=True)
    report = write_reports(args.out_root, discovery, results)
    print(json.dumps({
        "out_root": str(args.out_root),
        "tested_count": report["tested_count"],
        "passed_count": report["passed_count"],
        "failed_count": report["failed_count"],
        "accepted_count": report["accepted_count"],
        "pending_manual_review_count": report["pending_manual_review_count"],
        "needs_fix_count": report["needs_fix_count"],
        "fully_accepted": report["fully_accepted"],
        "report_json": str(args.out_root / "regression_report.json"),
        "report_md": str(args.out_root / "regression_report.md"),
        "review_index": str(args.out_root / "review-index.html"),
        "final_acceptance_gate": str(args.out_root / "final_acceptance_gate.md"),
        "final_acceptance_gate_json": str(args.out_root / "final_acceptance_gate.json"),
        "regression_stability_snapshot": str(args.out_root / "regression_stability_snapshot.json"),
        "acceptance_audit": str(args.out_root / "acceptance_audit.md"),
        "outline_inventory": str(args.out_root / "outline_inventory.md"),
        "outline_inventory_html": str(args.out_root / "outline_inventory.html"),
        "outline_inventory_link_audit": str(args.out_root / "outline_inventory_link_audit.md"),
        "outline_fact_reconciliation": str(args.out_root / "outline_fact_reconciliation.md"),
        "body_scope_audit": str(args.out_root / "body_scope_audit.md"),
        "anchor_integrity_audit": str(args.out_root / "anchor_integrity_audit.md"),
        "manual_anchor_spotcheck": str(args.out_root / "manual_anchor_spotcheck.md"),
        "manual_anchor_spotcheck_html": str(args.out_root / "manual_anchor_spotcheck.html"),
        "manual_anchor_link_audit": str(args.out_root / "manual_anchor_link_audit.md"),
        "manual_review_checklist": str(args.out_root / "manual_review_checklist.md"),
        "manual_review_decision_sheet": str(args.out_root / "manual_review_decision_sheet.md"),
        "manual_review_decision_sheet_html": str(args.out_root / "manual_review_decision_sheet.html"),
        "manual_review_command_sheet": str(args.out_root / "manual_review_command_sheet.md"),
        "pending_manual_fact_review_summary": str(args.out_root / "pending_manual_fact_review_summary.md"),
        "human_review_status": str(args.out_root / "human_review_status.md"),
        "human_review_status_html": str(args.out_root / "human_review_status.html"),
        "pending_review_queue": str(args.out_root / "pending_review_queue.md"),
        "pending_review_queue_html": str(args.out_root / "pending_review_queue.html"),
        "pending_review_link_audit": str(args.out_root / "pending_review_link_audit.md"),
        "manual_review_status_template": str(args.out_root / "manual_review_status_template.json"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
