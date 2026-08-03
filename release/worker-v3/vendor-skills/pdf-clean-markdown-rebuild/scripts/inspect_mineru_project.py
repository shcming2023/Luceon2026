#!/usr/bin/env python3
import argparse
import json
import re
from collections import Counter
from pathlib import Path


IMAGE_RE = re.compile(r"images/[^)\s]+?\.(?:jpg|jpeg|png|webp)", re.I)


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_flat_content(root):
    candidates = sorted(root.glob("*_content_list.json"))
    for path in candidates:
        try:
            data = load_json(path)
        except Exception:
            continue
        if isinstance(data, list) and data and isinstance(data[0], dict) and "type" in data[0]:
            return path, data
    return None, []


def main():
    parser = argparse.ArgumentParser(description="Inspect a MinerU-style PDF extraction folder.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--out", type=Path, help="Write JSON report to this path.")
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    report = {"root": str(root)}

    files = {p.name: p for p in root.iterdir() if p.is_file()}
    report["files"] = {name: path.stat().st_size for name, path in sorted(files.items())}
    report["pdfs"] = [p.name for p in sorted(root.glob("*.pdf"))]

    image_dir = root / "images"
    source_images = sorted(image_dir.glob("*")) if image_dir.exists() else []
    source_image_rel = {f"images/{p.name}" for p in source_images if p.is_file()}
    report["source_image_count"] = len(source_image_rel)

    full_md = root / "full.md"
    md_images = set()
    if full_md.exists():
        text = full_md.read_text(encoding="utf-8", errors="replace")
        md_images = set(IMAGE_RE.findall(text))
        report["full_md"] = {
            "lines": text.count("\n") + 1,
            "image_refs": len(md_images),
            "unique_image_refs": len(md_images),
            "heading_lines": len(re.findall(r"(?m)^#{1,6}\s+", text)),
        }
    else:
        report["full_md"] = None

    report["markdown_missing_images"] = sorted(md_images - source_image_rel)
    report["source_images_not_in_markdown_count"] = len(source_image_rel - md_images)
    report["source_images_not_in_markdown_sample"] = sorted(source_image_rel - md_images)[:30]

    flat_path, flat = find_flat_content(root)
    report["flat_content_file"] = flat_path.name if flat_path else None
    if flat:
        type_counts = Counter(str(block.get("type", "")) for block in flat)
        report["flat_block_count"] = len(flat)
        report["flat_type_counts"] = dict(sorted(type_counts.items()))
        report["text_level_counts"] = dict(sorted(Counter(
            str(block.get("text_level")) for block in flat if block.get("text_level") is not None
        ).items()))
        report["table_count"] = type_counts.get("table", 0)
        report["image_block_count"] = type_counts.get("image", 0)
        report["footnote_count"] = type_counts.get("page_footnote", 0)
        report["heading_samples"] = [
            {"page_idx": b.get("page_idx"), "text": str(b.get("text", ""))[:160]}
            for b in flat
            if b.get("type") == "text" and b.get("text_level") is not None
        ][:80]

    layout = root / "layout.json"
    if layout.exists():
        try:
            data = load_json(layout)
            pages = data.get("pdf_info", []) if isinstance(data, dict) else []
            para_counts = Counter()
            discarded_counts = Counter()
            for page in pages:
                for block in page.get("para_blocks", []) or []:
                    para_counts[str(block.get("type", ""))] += 1
                for block in page.get("discarded_blocks", []) or []:
                    discarded_counts[str(block.get("type", ""))] += 1
            report["layout"] = {
                "pages": len(pages),
                "para_type_counts": dict(sorted(para_counts.items())),
                "discarded_type_counts": dict(sorted(discarded_counts.items())),
            }
        except Exception as exc:
            report["layout"] = {"error": str(exc)}

    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
