#!/usr/bin/env python3
import argparse
import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


SCHEMA_VERSION = "0.1.0"
SKILL_DIR = Path(__file__).resolve().parents[1]
PROFILE_DIR = SKILL_DIR / "profiles"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def slug(text, fallback):
    asciiish = re.sub(r"[^0-9A-Za-z]+", "-", text).strip("-").lower()
    if asciiish:
        return asciiish[:64]
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return f"{fallback}-{digest}"


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def load_profile(profile_name):
    if profile_name.endswith(".json") or "/" in profile_name:
        return read_json(profile_name)
    return read_json(PROFILE_DIR / f"{profile_name}.json")


def detect_profile(markdown):
    scores = {}
    strong_hits = {}
    heading_text = "\n".join(
        match.group(2)
        for match in re.finditer(r"^(#{1,6})\s+(.+?)\s*$", markdown, re.M)
    )
    for profile_path in sorted(PROFILE_DIR.glob("*.json")):
        profile = read_json(profile_path)
        score = 0
        auto_rules = profile.get("auto_detect_rules") or []
        if auto_rules:
            for rule in auto_rules:
                pattern = rule.get("pattern")
                if not pattern:
                    continue
                target = heading_text if rule.get("scope", "heading") == "heading" else markdown
                matches = len(re.findall(pattern, target, re.M | re.I))
                score += matches * int(rule.get("weight", 1))
                if matches and int(rule.get("weight", 1)) >= 6:
                    strong_hits[profile["id"]] = strong_hits.get(profile["id"], 0) + matches
            scores[profile["id"]] = score
            continue
        for rule in profile.get("section_role_rules", []):
            pattern = rule.get("pattern")
            if pattern in {".+", ".*", "^.*$", "^.+$"}:
                continue
            if pattern:
                heading_pattern = pattern[1:] if pattern.startswith("^") else pattern
                score += len(re.findall(rf"^#{{1,6}}\s+{heading_pattern}", markdown, re.M | re.I))
        scores[profile["id"]] = score
    if not scores or max(scores.values()) == 0:
        return "general_textbook"
    max_score = max(scores.values())
    tied = [profile for profile, score in scores.items() if score == max_score]
    if "general_textbook" in tied and not strong_hits.get("english_grammar_workbook"):
        return "general_textbook"
    return max(scores, key=scores.get)


def parse_markdown(markdown):
    blocks = []
    page = None
    for lineno, raw in enumerate(markdown.splitlines(), 1):
        line = raw.rstrip()
        stripped = line.strip()
        page_match = re.match(r"<!--\s*page_idx:\s*([^>]+?)\s*-->", stripped)
        if page_match:
            page = page_match.group(1)
            blocks.append({"type": "page", "line": lineno, "page_idx": page, "text": stripped})
            continue
        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", stripped)
        if heading_match:
            blocks.append({
                "type": "heading",
                "line": lineno,
                "level": len(heading_match.group(1)),
                "text": heading_match.group(2),
                "page_idx": page,
            })
            continue
        image_match = re.match(r"^!\[(.*?)\]\((.*?)\)\s*$", stripped)
        if image_match:
            blocks.append({
                "type": "image",
                "line": lineno,
                "alt": image_match.group(1),
                "src": image_match.group(2),
                "text": stripped,
                "page_idx": page,
            })
            continue
        if stripped.startswith("<table"):
            blocks.append({"type": "table", "line": lineno, "text": stripped, "page_idx": page})
            continue
        if stripped:
            blocks.append({"type": "text", "line": lineno, "text": stripped, "page_idx": page})
    return blocks


def match_rule(text, rules):
    for rule in rules:
        if re.search(rule.get("pattern", "$.^"), text, re.I):
            return rule
    return {}


def build_sections(blocks, profile):
    heading_blocks = [b for b in blocks if b["type"] == "heading"]
    sections = []
    stack = []
    role_rules = profile.get("section_role_rules", [])
    for index, block in enumerate(heading_blocks):
        while stack and stack[-1]["level"] >= block["level"]:
            stack.pop()
        parent = stack[-1] if stack else None
        rule = match_rule(block["text"], role_rules)
        if not rule:
            rule = fallback_section_rule(block["text"], parent)
        section_id = f"sec-{index + 1:04d}-{slug(block['text'], 'section')}"
        path = (parent["path"] if parent else []) + [block["text"]]
        section = {
            "id": section_id,
            "parent_id": parent["id"] if parent else None,
            "title": block["text"],
            "level": block["level"],
            "role": rule.get("role", "unknown"),
            "section_type": rule.get("section_type", "unknown"),
            "path": path,
            "source_span": {"start_line": block["line"], "end_line": None},
            "page_span": {"start_page_idx": block.get("page_idx"), "end_page_idx": None},
            "metadata": {"heading_index": index},
        }
        sections.append(section)
        stack.append(section)

    total_lines = max((b["line"] for b in blocks), default=0)
    for i, section in enumerate(sections):
        start = section["source_span"]["start_line"]
        end = total_lines
        for later in sections[i + 1:]:
            if later["level"] <= section["level"]:
                end = later["source_span"]["start_line"] - 1
                break
        section["source_span"]["end_line"] = end
        pages = [b.get("page_idx") for b in blocks if start <= b["line"] <= end and b.get("page_idx") is not None]
        if pages:
            section["page_span"] = {"start_page_idx": pages[0], "end_page_idx": pages[-1]}
    return sections


def fallback_section_rule(title, parent):
    if re.match(r"^(About this book|How to use this book|for Cambridge\b)", title, re.I):
        return {"role": "source_note", "section_type": "front_matter"}
    if re.match(r"^Project\s+\d+\b", title, re.I):
        return {"role": "project_activity", "section_type": "activity"}
    if re.match(r"^\d+\s+\S", title):
        return {"role": "unit_lesson", "section_type": "lesson"}
    if re.match(r"^（?第?\d+题）?$", title) or re.match(r"^（第\d+题）$", title):
        return {"role": "figure_label", "section_type": "caption"}
    if re.match(r"^Footnotes$", title, re.I) or re.match(r"^Page\s+\d+$", title, re.I):
        return {"role": "source_note", "section_type": "note"}
    parent_role = parent.get("role") if parent else ""
    if parent_role in {"concept_explanation", "concept_detail", "key_points"}:
        return {"role": "concept_detail", "section_type": "concept_detail"}
    if parent_role in {"worked_examples"}:
        return {"role": "example_detail", "section_type": "example_detail"}
    if parent_role in {"grammar_practice", "extended_practice", "activity_prompt", "contextual_practice", "production_task", "passage_task"}:
        return {"role": "activity_detail", "section_type": "activity_detail"}
    return {}


def section_blocks(blocks, section):
    start = section["source_span"]["start_line"]
    end = section["source_span"]["end_line"]
    return [b for b in blocks if start < b["line"] <= end]


def direct_child_sections(sections, section):
    start = section["source_span"]["start_line"]
    end = section["source_span"]["end_line"]
    return [
        s for s in sections
        if s.get("parent_id") == section["id"] and start < s["source_span"]["start_line"] <= end
    ]


def content_text(blocks):
    return "\n".join(b.get("text", "") for b in blocks if b["type"] in {"text", "table"})


def first_page(blocks):
    for block in blocks:
        if block.get("page_idx") is not None:
            return block.get("page_idx")
    return None


def last_page(blocks):
    for block in reversed(blocks):
        if block.get("page_idx") is not None:
            return block.get("page_idx")
    return None


def asset_rule_for(section_role, profile):
    for rule in profile.get("asset_role_rules", []):
        if rule.get("section_role") == section_role:
            return rule
    return {"asset_type": "note", "role": section_role or "unknown"}


def infer_task_type(text, profile):
    matches = []
    for rule in profile.get("task_type_rules", []):
        if re.search(rule.get("pattern", "$.^"), text, re.I):
            matches.append(rule.get("task_type"))
    return matches[0] if matches else "unclassified"


def line_text(block):
    if block["type"] == "image":
        return block.get("text", "")
    return block.get("text", "")


def split_items(blocks, item_pattern):
    item_re = re.compile(item_pattern)
    items = []
    current = None
    prefix = []
    for block in blocks:
        text = block.get("text", "")
        if block["type"] == "text" and item_re.match(text):
            if current:
                items.append(current)
            label = item_re.match(text).group(0).strip()
            current = {"label": label.rstrip(".．、"), "blocks": [block]}
        elif current:
            current["blocks"].append(block)
        else:
            prefix.append(block)
    if current:
        items.append(current)
    return prefix, items


def split_examples(blocks, example_pattern):
    example_re = re.compile(example_pattern)
    examples = []
    current = None
    prefix = []
    for block in blocks:
        text = block.get("text", "")
        if block["type"] == "text" and example_re.match(text):
            if current:
                examples.append(current)
            label_match = re.match(r"^(例\s*\d+)", text)
            label = norm(label_match.group(1)) if label_match else "example"
            current = {"label": label, "blocks": [block]}
        elif current:
            current["blocks"].append(block)
        else:
            prefix.append(block)
    if current:
        examples.append(current)
    return prefix, examples


def parse_choices(blocks, choice_pattern):
    choice_re = re.compile(choice_pattern)
    choices = []
    body = []
    current = None
    for block in blocks:
        text = block.get("text", "")
        if block["type"] == "text" and choice_re.match(text):
            label = choice_re.match(text).group(0).strip().rstrip(".．、")
            item_text = text[choice_re.match(text).end():].strip()
            current = {"label": label, "md": item_text, "media": []}
            choices.append(current)
        elif block["type"] == "image" and current and not body:
            current["media"].append(block.get("src"))
        else:
            body.append(block)
    return body, choices


SPACED_DIGIT_NUMBER_RE = r"(?<![\dA-Za-z])\d(?:[ \t]+\d){2,}(?![\dA-Za-z])"
ARITHMETIC_OPERATOR_RE = r"(?:[+\-−×÷]|\\div|\\times|\\cdot)"


def has_spaced_digit_arithmetic(text):
    """Detect OCR-split arithmetic like `5 1 2 + 1 0 3`, not normal `60^{\\circ}`."""
    left_operand = rf"{SPACED_DIGIT_NUMBER_RE}\s*{ARITHMETIC_OPERATOR_RE}"
    right_operand = rf"{ARITHMETIC_OPERATOR_RE}\s*{SPACED_DIGIT_NUMBER_RE}"
    for match in re.finditer(left_operand + "|" + right_operand, text):
        snippet = match.group(0)
        digit_runs = re.findall(SPACED_DIGIT_NUMBER_RE, snippet)
        compacted = [re.sub(r"\s+", "", run) for run in digit_runs]
        if any(len(set(run)) > 1 for run in compacted):
            return True
    return False


def quality_flags_for(text, blocks, task_type, choices):
    flags = []
    if has_spaced_digit_arithmetic(text):
        flags.append("formula_spacing_suspect")
    if re.search(r"(?:=|为|是)\s*$", text) or "______" in text or "\\_\\_" in text:
        flags.append("blank_present")
    if task_type == "multiple_choice" and choices and len(choices) not in {2, 3, 4, 5}:
        flags.append("choice_count_mismatch")
    pages = [b.get("page_idx") for b in blocks if b.get("page_idx") is not None]
    if len(set(pages)) > 1:
        flags.append("page_break_inside_asset")
    if not norm(text) and not any(b["type"] in {"image", "table"} for b in blocks):
        flags.append("empty_asset")
    return flags


def make_asset(asset_id, section, blocks, profile, asset_type, role, label=None, metadata=None):
    metadata = metadata or {}
    text = content_text(blocks)
    task_type = infer_task_type((label or "") + "\n" + metadata.get("context_hint", "") + "\n" + text + "\n" + section["title"], profile)
    body_blocks, choices = parse_choices(blocks, profile.get("segmentation", {}).get("choice_pattern", "$.^"))
    source_lines = [b["line"] for b in blocks]
    source_span = {
        "start_line": min(source_lines) if source_lines else section["source_span"]["start_line"],
        "end_line": max(source_lines) if source_lines else section["source_span"]["end_line"],
        "start_page_idx": first_page(blocks),
        "end_page_idx": last_page(blocks),
    }
    flags = quality_flags_for(text, blocks, task_type, choices)
    return {
        "id": asset_id,
        "section_id": section["id"],
        "asset_type": asset_type,
        "role": role,
        "task_type": task_type,
        "label": label or "",
        "content": {
            "md": text,
            "stem_md": text,
            "choices": choices,
            "tables": [b["text"] for b in blocks if b["type"] == "table"],
        },
        "answers": {"status": profile.get("answer_policy", "manual_review_required"), "items": []},
        "media_ids": [],
        "source_span": source_span,
        "quality": {"flags": flags, "confidence": {"rule": 0.72 if task_type != "unclassified" else 0.45}},
        "metadata": metadata,
    }


def split_activity_chunks(blocks, activity_pattern):
    if not activity_pattern:
        return [], []
    activity_re = re.compile(activity_pattern)
    prefix = []
    chunks = []
    current = None
    for block in blocks:
        text = block.get("text", "")
        if block["type"] == "text" and activity_re.match(text):
            if current:
                chunks.append(current)
            label_match = re.match(r"^([A-Z])\.\s*(.*)$", text)
            label = label_match.group(1) if label_match else text[:12]
            instruction = label_match.group(2) if label_match else text
            current = {"label": label, "instruction": instruction, "blocks": [block]}
        elif current:
            current["blocks"].append(block)
        else:
            prefix.append(block)
    if current:
        chunks.append(current)
    return prefix, chunks


def annotate_assets(blocks, sections, profile):
    assets = []
    asset_counter = 0
    covered_ranges = []
    segmentation = profile.get("segmentation", {})
    item_pattern = segmentation.get("item_start_pattern", "$.^")
    example_pattern = profile.get("example_start_pattern")

    for section in sections:
        children = direct_child_sections(sections, section)
        if children:
            continue
        sb = [b for b in section_blocks(blocks, section) if b["type"] != "heading"]
        if not sb:
            continue
        role_rule = asset_rule_for(section.get("role"), profile)
        asset_type = role_rule.get("asset_type", "note")
        role = role_rule.get("role", section.get("role", "unknown"))

        if asset_type == "example" and example_pattern:
            prefix, examples = split_examples(sb, example_pattern)
            if prefix and norm(content_text(prefix)):
                asset_counter += 1
                assets.append(make_asset(f"asset-{asset_counter:05d}", section, prefix, profile, "note", "example_intro"))
            for item in examples:
                asset_counter += 1
                assets.append(make_asset(f"asset-{asset_counter:05d}", section, item["blocks"], profile, "example", role, label=item["label"]))
            continue

        if asset_type in {"exercise", "assessment"} or section.get("section_type") in {"problem_group", "activity"}:
            activity_pattern = profile.get("heading_patterns", {}).get("activity")
            leading, chunks = split_activity_chunks(sb, activity_pattern)
            if chunks:
                if leading and norm(content_text(leading)):
                    asset_counter += 1
                    assets.append(make_asset(f"asset-{asset_counter:05d}", section, leading, profile, "activity", role, label=section["title"]))
                for chunk in chunks:
                    header = chunk["blocks"][0]
                    rest = chunk["blocks"][1:]
                    asset_counter += 1
                    assets.append(make_asset(
                        f"asset-{asset_counter:05d}",
                        section,
                        [header],
                        profile,
                        "activity",
                        role,
                        label=chunk["label"],
                        metadata={"instruction": chunk["instruction"], "context_hint": chunk["instruction"]},
                    ))
                    _, chunk_items = split_items(rest, item_pattern)
                    if chunk_items:
                        for item in chunk_items:
                            asset_counter += 1
                            assets.append(make_asset(
                                f"asset-{asset_counter:05d}",
                                section,
                                item["blocks"],
                                profile,
                                "exercise" if asset_type != "assessment" else "assessment",
                                role,
                                label=item["label"],
                                metadata={"activity_label": chunk["label"], "activity_instruction": chunk["instruction"], "context_hint": chunk["instruction"]},
                            ))
                    elif rest and norm(content_text(rest)):
                        asset_counter += 1
                        assets.append(make_asset(
                            f"asset-{asset_counter:05d}",
                            section,
                            rest,
                            profile,
                            "exercise" if asset_type != "assessment" else "assessment",
                            role,
                            label=chunk["label"],
                            metadata={"activity_instruction": chunk["instruction"], "context_hint": chunk["instruction"]},
                        ))
                continue

            prefix, items = split_items(sb, item_pattern)
            if items:
                if prefix and norm(content_text(prefix)):
                    asset_counter += 1
                    assets.append(make_asset(f"asset-{asset_counter:05d}", section, prefix, profile, "activity", role, label=section["title"]))
                for item in items:
                    asset_counter += 1
                    assets.append(make_asset(
                        f"asset-{asset_counter:05d}",
                        section,
                        item["blocks"],
                        profile,
                        "exercise" if asset_type != "assessment" else "assessment",
                        role,
                        label=item["label"],
                    ))
                continue

        asset_counter += 1
        assets.append(make_asset(f"asset-{asset_counter:05d}", section, sb, profile, asset_type, role, label=section["title"]))

    for asset in assets:
        covered_ranges.append((asset["source_span"]["start_line"], asset["source_span"]["end_line"]))
    return assets


def caption_after(blocks, image_index):
    if image_index + 1 >= len(blocks):
        return ""
    nxt = blocks[image_index + 1]
    text = nxt.get("text", "")
    cap = re.match(r"^\*(.+)\*$", text)
    return cap.group(1) if cap else ""


def link_media(blocks, assets):
    media = []
    relations = []
    image_blocks = [b for b in blocks if b["type"] == "image"]
    block_positions = {id(b): i for i, b in enumerate(blocks)}
    for idx, block in enumerate(image_blocks, 1):
        linked = None
        best_distance = None
        for asset in assets:
            start = asset["source_span"]["start_line"]
            end = asset["source_span"]["end_line"]
            if start <= block["line"] <= end:
                linked = asset
                best_distance = 0
                break
            distance = min(abs(block["line"] - start), abs(block["line"] - end))
            if best_distance is None or distance < best_distance:
                best_distance = distance
                linked = asset
        confidence = 0.95 if best_distance == 0 else max(0.35, 0.8 - (best_distance or 0) * 0.04)
        block_i = block_positions.get(id(block), 0)
        media_id = f"media-{idx:05d}"
        item = {
            "id": media_id,
            "src": block.get("src", ""),
            "alt": block.get("alt", ""),
            "caption": caption_after(blocks, block_i),
            "linked_asset_id": linked["id"] if linked else None,
            "role": infer_media_role(linked, block),
            "confidence": round(confidence, 2),
            "source_line": block["line"],
            "page_idx": block.get("page_idx"),
        }
        media.append(item)
        if linked:
            linked.setdefault("media_ids", []).append(media_id)
            relations.append({
                "id": f"rel-{len(relations) + 1:05d}",
                "relation_type": "asset_has_media",
                "from_id": linked["id"],
                "to_id": media_id,
                "confidence": item["confidence"],
                "metadata": {"source_line": block["line"]},
            })
    return media, relations


def infer_media_role(asset, block):
    if not asset:
        return "unlinked"
    if asset["asset_type"] in {"exercise", "assessment"}:
        return "problem_figure"
    if asset["asset_type"] == "example":
        return "worked_example_figure"
    if asset["asset_type"] == "concept":
        return "concept_illustration"
    return "supporting_figure"


def make_review_items(sections, assets, media):
    items = []
    for section in sections:
        if section.get("role") == "unknown":
            items.append({
                "id": f"review-{len(items) + 1:05d}",
                "severity": "P3",
                "reason": "heading_role_unknown",
                "source_span": section["source_span"],
                "suggested_action": "profile_rule_or_manual_check",
                "context": {"section_id": section["id"], "title": section["title"]},
            })
    for asset in assets:
        for flag in asset.get("quality", {}).get("flags", []):
            # Formula spacing remains a non-blocking asset flag; visible formula quality
            # is gated by clean-stage OCR repair and rendered final review.
            if flag in {"choice_count_mismatch", "empty_asset"}:
                items.append({
                    "id": f"review-{len(items) + 1:05d}",
                    "severity": "P2" if flag != "empty_asset" else "P1",
                    "reason": flag,
                    "source_span": asset["source_span"],
                    "suggested_action": "manual_check",
                    "context": {"asset_id": asset["id"], "label": asset.get("label"), "task_type": asset.get("task_type")},
                })
    for item in media:
        if not item.get("linked_asset_id"):
            items.append({
                "id": f"review-{len(items) + 1:05d}",
                "severity": "P2",
                "reason": "media_unlinked",
                "source_span": {"start_line": item["source_line"], "end_line": item["source_line"], "start_page_idx": item.get("page_idx"), "end_page_idx": item.get("page_idx")},
                "suggested_action": "manual_media_link_check",
                "context": {"media_id": item["id"], "src": item["src"], "linked_asset_id": item.get("linked_asset_id")},
            })
    return items


def render_preview(document, sections, assets, media, review_items, out_path):
    media_by_asset = defaultdict(list)
    for item in media:
        media_by_asset[item.get("linked_asset_id")].append(item)
    reviews_by_asset = defaultdict(list)
    for item in review_items:
        aid = item.get("context", {}).get("asset_id")
        if aid:
            reviews_by_asset[aid].append(item)
    html_parts = [
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{html.escape(document.get('book_id') or 'annotation')}</title>",
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#f6f7f9;color:#18202a}main{max-width:1100px;margin:0 auto;padding:24px;background:white}.asset{border:1px solid #d0d7de;border-left:5px solid #6b7280;margin:14px 0;padding:12px}.asset.exercise{border-left-color:#2563eb}.asset.example{border-left-color:#16a34a}.asset.concept{border-left-color:#9333ea}.meta{font-size:12px;color:#667085}.flag{display:inline-block;background:#fff4cc;border:1px solid #f5c451;border-radius:3px;padding:1px 5px;margin:2px}.review{background:#fee2e2;border:1px solid #ef4444;padding:4px;margin:4px 0}img{max-width:240px;max-height:180px;object-fit:contain;margin:4px;border:1px solid #ddd}pre{white-space:pre-wrap;font-family:inherit}</style>",
        "</head><body><main>",
        f"<h1>Annotation Preview</h1><p class='meta'>profile: {html.escape(document.get('profile',''))} | assets: {document['counts'].get('assets')} | media: {document['counts'].get('media')} | review: {document['counts'].get('review_items')}</p>",
        "<h2>Assets</h2>",
    ]
    section_by_id = {s["id"]: s for s in sections}
    for asset in assets:
        section = section_by_id.get(asset["section_id"], {})
        flags = asset.get("quality", {}).get("flags", [])
        html_parts.append(f"<article class='asset {html.escape(asset['asset_type'])}' id='{asset['id']}'>")
        html_parts.append(f"<div class='meta'>{html.escape(asset['id'])} | {html.escape(asset['asset_type'])} | {html.escape(asset.get('role',''))} | {html.escape(asset.get('task_type',''))} | lines {asset['source_span'].get('start_line')}-{asset['source_span'].get('end_line')}</div>")
        html_parts.append(f"<h3>{html.escape(' / '.join(section.get('path', [])))} {html.escape(asset.get('label',''))}</h3>")
        if flags:
            html_parts.append("<div>" + "".join(f"<span class='flag'>{html.escape(flag)}</span>" for flag in flags) + "</div>")
        for review in reviews_by_asset.get(asset["id"], []):
            html_parts.append(f"<div class='review'>{html.escape(review['severity'])}: {html.escape(review['reason'])}</div>")
        md = asset.get("content", {}).get("md", "")
        html_parts.append(f"<pre>{html.escape(md[:1200])}</pre>")
        if asset.get("content", {}).get("choices"):
            html_parts.append("<ol>")
            for choice in asset["content"]["choices"]:
                html_parts.append(f"<li>{html.escape(choice.get('label',''))}: {html.escape(choice.get('md',''))}</li>")
            html_parts.append("</ol>")
        for m in media_by_asset.get(asset["id"], []):
            html_parts.append(f"<figure><img src='../{html.escape(m['src'])}' alt='{html.escape(m.get('alt',''))}'><figcaption>{html.escape(m.get('caption') or m.get('role',''))} ({m.get('confidence')})</figcaption></figure>")
        html_parts.append("</article>")
    html_parts.append("</main></body></html>")
    out_path.write_text("\n".join(html_parts) + "\n", encoding="utf-8")


def quality_report(document, sections, assets, media, review_items):
    counter = Counter(a["asset_type"] for a in assets)
    role_counter = Counter(a["role"] for a in assets)
    task_counter = Counter(a["task_type"] for a in assets)
    severity_counter = Counter(r["severity"] for r in review_items)
    linked = sum(1 for m in media if m.get("linked_asset_id"))
    linked_low_confidence = sum(1 for m in media if m.get("linked_asset_id") and m.get("confidence", 0) < 0.5)
    lines = [
        "# Annotation Quality Report",
        "",
        f"- Source: `{document['source_markdown']}`",
        f"- Profile: `{document['profile']}`",
        f"- Answer policy: `{document.get('answer_policy','')}`",
        f"- Sections: {len(sections)}",
        f"- Assets: {len(assets)}",
        f"- Media: {len(media)}; linked: {linked}; unlinked: {len(media) - linked}",
        f"- Linked media below 0.50 confidence: {linked_low_confidence}",
        f"- Review items: {len(review_items)} {dict(severity_counter)}",
        "",
        "## Asset Types",
        "",
    ]
    for key, value in counter.most_common():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Top Roles", ""])
    for key, value in role_counter.most_common(20):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Top Task Types", ""])
    for key, value in task_counter.most_common(20):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Review Items", ""])
    for item in review_items[:80]:
        span = item.get("source_span", {})
        lines.append(f"- [{item['severity']}] {item['reason']} lines {span.get('start_line')}-{span.get('end_line')} action={item.get('suggested_action')}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Annotate clean teaching-material Markdown into semantic assets.")
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--profile", default="auto", help="Profile id, profile json path, or auto.")
    parser.add_argument("--book-id", default="")
    parser.add_argument("--book-config", type=Path)
    args = parser.parse_args()

    markdown = args.markdown.read_text(encoding="utf-8")
    profile_id = detect_profile(markdown) if args.profile == "auto" else args.profile
    profile = load_profile(profile_id)
    if args.book_config:
        config = read_json(args.book_config)
        profile = {**profile, **{k: v for k, v in config.items() if k not in {"section_role_rules", "asset_role_rules"}}}
        profile.setdefault("book_config", config)

    blocks = parse_markdown(markdown)
    sections = build_sections(blocks, profile)
    assets = annotate_assets(blocks, sections, profile)
    media, relations = link_media(blocks, assets)
    review_items = make_review_items(sections, assets, media)

    document = {
        "schema_version": SCHEMA_VERSION,
        "source_markdown": str(args.markdown),
        "profile": profile["id"],
        "book_id": args.book_id or args.markdown.parent.name,
        "answer_policy": profile.get("answer_policy", "manual_review_required"),
        "inputs": {
            "markdown": str(args.markdown),
            "profile": profile_id,
            "book_config": str(args.book_config) if args.book_config else None,
        },
        "counts": {
            "blocks": len(blocks),
            "sections": len(sections),
            "assets": len(assets),
            "media": len(media),
            "relations": len(relations),
            "review_items": len(review_items),
        },
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.out_dir / "document.json", document)
    write_json(args.out_dir / "sections.json", sections)
    write_json(args.out_dir / "assets.json", assets)
    write_json(args.out_dir / "media.json", media)
    write_json(args.out_dir / "relations.json", relations)
    write_json(args.out_dir / "review_items.json", review_items)
    (args.out_dir / "quality_report.md").write_text(quality_report(document, sections, assets, media, review_items), encoding="utf-8")
    render_preview(document, sections, assets, media, review_items, args.out_dir / "preview.html")
    print(f"Wrote {args.out_dir}")
    print(json.dumps(document["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
