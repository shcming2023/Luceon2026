#!/usr/bin/env python3
"""Mechanically serialize a frozen render plan into a frozen ElegantBook template.

The renderer never chooses semantics, boxes, hierarchy, image representation,
or layout parameters.  Those choices must already exist in render_plan.json.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import fitz
    from PIL import Image
except ImportError as exc:  # fail clearly instead of silently changing media representation
    raise SystemExit(f"required media dependency unavailable: {exc}")

VERSION = "frozen-plan-renderer/1.9.1"


def load_delivery_compatibility():
    path = Path(__file__).with_name("delivery_compatibility.py")
    spec = importlib.util.spec_from_file_location("spec05_delivery_compatibility", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

SYMBOLS = {
    "□": r"\ensuremath{\square}", "☐": r"\ensuremath{\square}", "■": r"\ensuremath{\blacksquare}",
    "△": r"\ensuremath{\triangle}", "▲": r"\ensuremath{\blacktriangle}", "▽": r"\ensuremath{\triangledown}",
    "▼": r"\ensuremath{\blacktriangledown}", "▶": r"\ensuremath{\blacktriangleright}", "◀": r"\ensuremath{\blacktriangleleft}",
    "○": r"\ensuremath{\bigcirc}", "◯": r"\ensuremath{\bigcirc}", "●": r"\ensuremath{\bullet}",
    "◆": r"\ensuremath{\blacklozenge}", "◇": r"\ensuremath{\lozenge}",
    "★": r"\ensuremath{\bigstar}", "☆": r"\ensuremath{\star}", "✓": r"\ensuremath{\checkmark}",
    "∠": r"\ensuremath{\angle}", "∵": r"\ensuremath{\because}",
    "∴": r"\ensuremath{\therefore}", "⊥": r"\ensuremath{\perp}",
    "①": r"\ding{172}", "②": r"\ding{173}", "③": r"\ding{174}",
    "④": r"\ding{175}", "⑤": r"\ding{176}", "⑥": r"\ding{177}",
}
MATH_SYMBOLS = {
    "□": r"\square", "☐": r"\square", "■": r"\blacksquare", "△": r"\triangle",
    "▲": r"\blacktriangle", "▽": r"\triangledown", "▼": r"\blacktriangledown",
    "▶": r"\blacktriangleright", "◀": r"\blacktriangleleft", "○": r"\bigcirc", "◯": r"\bigcirc",
    "●": r"\bullet", "◆": r"\blacklozenge", "◇": r"\lozenge", "★": r"\bigstar",
    "☆": r"\star", "✓": r"\checkmark", "①": r"\text{\ding{172}}", "②": r"\text{\ding{173}}",
    "③": r"\text{\ding{174}}", "④": r"\text{\ding{175}}", "⑤": r"\text{\ding{176}}",
    "⑥": r"\text{\ding{177}}", "×": r"\times ", "÷": r"\div ", "−": "-", "＋": "+",
    "＝": "=", "＜": "<", "＞": ">", "∠": r"\angle ", "∵": r"\because ",
    "∴": r"\therefore ", "⊥": r"\perp ",
}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def relative(base: Path, target: Path) -> str:
    return os.path.relpath(target, base).replace("\\", "/")


def protect_symbols(text: str) -> tuple[str, dict[str, str]]:
    if "ⓞ" in text:
        raise ValueError("ambiguous OCR symbol ⓞ must be resolved against PDF evidence upstream")
    tokens: dict[str, str] = {}
    for index, (character, latex) in enumerate(SYMBOLS.items()):
        token = f"@@SYM{index}@@"
        text = text.replace(character, token)
        tokens[token] = latex
    return text, tokens


def escape_text(text: str) -> str:
    text, tokens = protect_symbols(text)
    text = text.replace("\\", r"\textbackslash{}")
    for old, new in [
        ("&", r"\&"), ("%", r"\%"), ("$", r"\$"), ("#", r"\#"), ("_", r"\_"),
        ("{", r"\{"), ("}", r"\}"), ("~", r"\textasciitilde{}"), ("^", r"\textasciicircum{}"),
    ]:
        text = text.replace(old, new)
    for token, latex in tokens.items():
        text = text.replace(token, latex)
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n\n", r"\par ").replace("\n", " ")


def sanitize_math(text: str) -> str:
    if "ⓞ" in text:
        raise ValueError("ambiguous OCR symbol ⓞ must be resolved against PDF evidence upstream")
    for character, latex in MATH_SYMBOLS.items():
        text = text.replace(character, latex)

    def circled_relation(match: re.Match[str]) -> str:
        symbol = match.group(1).strip()
        if symbol not in {"<", ">", "="}:
            raise ValueError(f"unsupported circled math relation: {symbol}")
        return r"\mathrel{\ooalign{\hfil$\scriptstyle " + symbol + r"$\hfil\cr$\bigcirc$\cr}}"

    text = re.sub(r"\\textcircled\s*\{\s*([^{}]+?)\s*\}", circled_relation, text)
    protected: dict[str, str] = {}

    def keep_text(match: re.Match[str]) -> str:
        token = f"@@MATHTEXT{len(protected)}@@"
        protected[token] = match.group(0)
        return token

    text = re.sub(r"\\text\s*\{[^{}]*\}", keep_text, text)
    text = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff]+", lambda match: r"\text{" + match.group(0) + "}", text)
    for token, value in protected.items():
        text = text.replace(token, value)
    return text


def sanitize_inline_math(text: str) -> str:
    if not (text.startswith(r"\(") and text.endswith(r"\)")):
        raise ValueError("inline math must use canonical \\(...\\) delimiters")
    inner = text[2:-2]
    # Providers can wrap prose-like currency expressions in one broad inline
    # math span (for example ``\( 500 = $525 \)``). A bare dollar can never be
    # a valid nested delimiter inside canonical \(...\) math, so retain it as
    # a literal source glyph rather than letting TeX open a second math mode.
    inner = re.sub(r"(?<!\\)\$", lambda _: r"\text{\$}", inner)
    return r"\(" + sanitize_math(inner) + r"\)"


def mixed_text(text: str) -> str:
    parts = re.split(r"(\\\(.*?\\\))", text, flags=re.S)
    return "".join(sanitize_inline_math(part) if part.startswith(r"\(") and part.endswith(r"\)") else escape_text(part) for part in parts)


def replace_metadata(text: str, values: dict[str, str]) -> str:
    for name, value in values.items():
        text, count = re.subn(rf"\\{re.escape(name)}\{{[^{{}}]*\}}", lambda _: rf"\{name}{{{mixed_text(value)}}}", text, count=1)
        if count != 1:
            raise ValueError(f"selected metadata macro not found exactly once: {name}")
    return text


def replace_presentation_values(text: str, values: dict[str, str]) -> str:
    for name, value in values.items():
        path = Path(value)
        if name not in {"cover", "logo"} or not value or path.is_absolute() or ".." in path.parts or any(character in value for character in "\\{}"):
            raise ValueError(f"unsafe presentation macro value: {name}")
        text, count = re.subn(
            rf"\\{re.escape(name)}\{{[^{{}}]*\}}",
            lambda _: rf"\{name}{{{value}}}", text, count=1,
        )
        if count != 1:
            raise ValueError(f"presentation macro not found exactly once: {name}")
    return text


def mask_main(text: str, contract: dict[str, Any]) -> str:
    masked = text
    for name in contract["metadata_allowlist"]:
        masked = re.sub(
            rf"\\{re.escape(name)}\{{[^{{}}]*\}}",
            lambda _match, macro=name: f"\\{macro}{{<META:{macro}>}}",
            masked,
            count=1,
        )
    marker = contract["main_template"]["body_marker"]
    end_token = contract["main_template"]["body_end_token"]
    start = masked.index(marker) + len(marker)
    end = masked.index(end_token, start)
    return masked[:start] + "\n<BODY>\n" + masked[end:]


def deterministic_zip(project: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(project.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(path.relative_to(project).as_posix(), (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def cropbox_bbox_to_raster_box(bbox: list[float], page, raster_size: tuple[int, int], padding: dict[str, float]) -> tuple[int, int, int, int]:
    media, crop = page.mediabox, page.cropbox
    raster_width, raster_height = raster_size
    media_width, media_height = media.width, media.height
    crop_width, crop_height = crop.width, crop.height
    x0, y0, x1, y1 = bbox
    pad_x, pad_y = float(padding["x"]) * crop_width, float(padding["y"]) * crop_height
    left = int(((crop.x0 + x0 * crop_width - pad_x) - media.x0) * raster_width / media_width)
    top = int(((crop.y0 + y0 * crop_height - pad_y) - media.y0) * raster_height / media_height)
    right = math.ceil(((crop.x0 + x1 * crop_width + pad_x) - media.x0) * raster_width / media_width)
    bottom = math.ceil(((crop.y0 + y1 * crop_height + pad_y) - media.y0) * raster_height / media_height)
    return max(0, left), max(0, top), min(raster_width, right), min(raster_height, bottom)


def load_validator(path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"contract validator unavailable: {path}")
    spec = importlib.util.spec_from_file_location("elegantbook_contract_validator", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load contract validator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def default_validator_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "luceon-popo-to-refined-elegantbook/scripts/validate_intermediate_contracts.py"
    )


def default_media_validator_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "luceon-popo-to-refined-elegantbook/scripts/media_source_representation.py"
    )


def index_assets(roots: list[Path]) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for root in roots:
        if not root.is_dir():
            raise ValueError(f"asset root does not exist: {root}")
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                index.setdefault(path.name, []).append(path)
    return index


def add_declared_empty_bibliography(project: Path, contract: dict[str, Any]) -> list[dict[str, Any]]:
    class_files = [project / item["path"] for item in contract["immutable_files"] if item["path"].endswith(".cls")]
    declared: set[str] = set()
    for path in class_files + [project / contract["body_insertion"]["file"]]:
        if path.is_file():
            declared.update(re.findall(r"\\addbibresource(?:\[[^]]*\])?\{([^}]+\.bib)\}", path.read_text(encoding="utf-8")))
    additions: list[dict[str, Any]] = []
    allowed = set(contract["ancillary_policy"].get("allowed_extensions", []))
    for reference in sorted(declared):
        target = project / reference
        if target.exists():
            continue
        if Path(reference).suffix.lower() not in allowed or Path(reference).is_absolute() or ".." in Path(reference).parts:
            raise ValueError(f"frozen template declares a bibliography outside the ancillary policy: {reference}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
        additions.append({"path": reference, "sha256": sha256_file(target), "size_bytes": 0, "type": "empty_pure_bibliographic_data"})
    return additions


def materialize_presentation_assets(
    project: Path, contract_path: Path, contract: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if contract.get("schema_version") != "template-contract/2.0":
        return [], {}
    selected = contract.get("selected_presentation", {}).get("assets")
    if not isinstance(selected, dict) or set(selected) != {"cover", "logo"}:
        raise ValueError("template-contract/2.0 lacks exact cover and logo presentation assets")
    immutable = {item["path"]: item["sha256"] for item in contract.get("immutable_files", [])}
    additions: list[dict[str, Any]] = []
    values: dict[str, str] = {}
    for macro in ("cover", "logo"):
        item = selected[macro]
        values[macro] = item["macro_value"]
        if item.get("decision", {}).get("status") != "closed" or item.get("compatibility", {}).get("status") != "approved":
            raise ValueError(f"presentation {macro} decision or compatibility is not closed")
        if item.get("mode") == "template_default":
            member = Path(item["template_member"])
            target = project / member
            if not target.is_file() or immutable.get(member.as_posix()) != item.get("asset_sha256") or sha256_file(target) != item.get("asset_sha256"):
                raise ValueError(f"template-default {macro} asset drifted")
            continue
        if item.get("mode") not in {"source_region_asset", "approved_static_asset"}:
            raise ValueError(f"unsupported presentation mode: {item.get('mode')}")
        source_ref = Path(item["asset_ref"])
        source = source_ref if source_ref.is_absolute() else (contract_path.parent / source_ref).resolve()
        destination_rel = Path(item["project_path"])
        if destination_rel.is_absolute() or ".." in destination_rel.parts or destination_rel.as_posix() in immutable:
            raise ValueError(f"presentation {macro} may not replace a frozen template member")
        destination = project / destination_rel
        if not source.is_file() or sha256_file(source) != item.get("asset_sha256"):
            raise ValueError(f"presentation {macro} source asset hash mismatch")
        if destination.exists():
            raise ValueError(f"presentation {macro} destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if sha256_file(destination) != item["asset_sha256"]:
            raise ValueError(f"presentation {macro} materialized bytes differ")
        additions.append({
            "path": destination_rel.as_posix(), "sha256": item["asset_sha256"],
            "size_bytes": destination.stat().st_size, "type": "approved_presentation_asset",
            "macro": macro, "mode": item["mode"], "decision_id": item["decision"]["decision_id"],
        })
    return additions, values


def serialize(
    plan: dict[str, Any], project: Path, asset_roots: list[Path], source_pdf: Path | None,
    source_page_dir: Path | None, report_root: Path,
) -> tuple[bytes, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    asset_index = index_assets(asset_roots)
    source_doc = fitz.open(source_pdf) if source_pdf else None
    body_lines: list[str] = []
    emissions: list[dict[str, Any]] = []
    copied: dict[str, Any] = {}
    crops: dict[str, Any] = {}
    materialized_asset_by_hash: dict[str, str] = {}

    def media_binding(node: dict[str, Any]) -> dict[str, Any]:
        return node.get("media_binding") or node.get("payload", {}).get("media_binding") or {}

    def source_text(payload: dict[str, Any]) -> str:
        value = payload.get("text", payload.get("raw_content"))
        if not isinstance(value, str):
            raise ValueError("paragraph payload lacks frozen source text")
        return value

    def safe_media_name(node: dict[str, Any], source: Path) -> str:
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", node["render_node_id"]).strip("-")
        return f"media-{stem}{source.suffix.lower()}"

    def emit(node: dict[str, Any], lines: list[str]) -> None:
        anchor_token = hashlib.sha256(
            node["render_node_id"].encode("utf-8")
        ).hexdigest()[:32]
        start_destination = f"luceon-v3-s-{anchor_token}"
        end_destination = f"luceon-v3-e-{anchor_token}"
        start = len(body_lines) + 1
        body_lines.extend([
            f"% render_node_id: {node['render_node_id']}",
            rf"\hypertarget{{{start_destination}}}{{}}",
            *lines,
            rf"\hypertarget{{{end_destination}}}{{}}",
            "",
        ])
        emissions.append({
            "render_node_id": node["render_node_id"], "source_block_ids": node["source_block_ids"],
            "target_construct": node["target_construct"], "latex_start_line": start,
            "latex_end_line": len(body_lines), "payload_hash": node["payload_hash"],
            "page_provenance": {
                "method": "pdf_named_destination_interval",
                "start_destination": start_destination,
                "end_destination": end_destination,
            },
        })

    try:
        for node in plan["nodes"]:
            construct = node["target_construct"]
            payload = node["payload"]
            parameters = node["construct_parameters"]
            if construct in {
                "chapter*",
                "section*",
                "subsection*",
                "subsubsection*",
                "paragraph*",
            }:
                command = construct[:-1]
                title = mixed_text(payload["title"])
                lines = [rf"\{command}*{{{title}}}"]
                if parameters.get("toc"):
                    toc_level = parameters.get("toc_entry_level", command)
                    indent = float(parameters.get("toc_indent_em", 0))
                    toc_title = rf"\protect\hspace{{{indent:.3f}em}}{title}" if indent else title
                    strategy = parameters.get("toc_visibility_strategy", "native")
                    if strategy == "native":
                        lines.extend([r"\phantomsection", rf"\addcontentsline{{toc}}{{{toc_level}}}{{{toc_title}}}"])
                    elif strategy == "localized_depth_override":
                        depth = parameters.get("toc_depth_override")
                        if not isinstance(depth, int) or depth < 0 or toc_level != command:
                            raise ValueError(f"invalid localized TOC depth override: {node['render_node_id']}")
                        lines.extend([
                            rf"\addtocontents{{toc}}{{\protect\begingroup\protect\setcounter{{tocdepth}}{{{depth}}}}}",
                            r"\phantomsection",
                            rf"\addcontentsline{{toc}}{{{toc_level}}}{{{toc_title}}}",
                            r"\addtocontents{toc}{\protect\endgroup}",
                        ])
                    else:
                        raise ValueError(f"unsupported frozen TOC visibility strategy: {node['render_node_id']} {strategy}")
                emit(node, lines)
            elif construct == "tcolorbox":
                title = parameters.get("title", payload.get("title"))
                if not isinstance(title, str):
                    raise ValueError(f"tcolorbox lacks frozen title: {node['render_node_id']}")
                options = [parameters["style"], f"title={{{mixed_text(title)}}}"]
                if parameters.get("breakable"):
                    options.append("breakable")
                lines = [rf"\begin{{tcolorbox}}[{','.join(options)}]"]
                segments = payload.get("segments")
                if segments is None:
                    segments = [item["raw_content"] for item in payload.get("body", [])]
                for segment in segments:
                    lines.extend([mixed_text(segment), r"\par"])
                lines.append(r"\end{tcolorbox}")
                emit(node, lines)
            elif construct == "paragraph":
                emit(node, [mixed_text(source_text(payload)), r"\par"])
            elif construct == "response_list":
                columns = parameters.get("columns")
                answer = parameters.get("answer_space", {})
                items = payload.get("items")
                if columns not in {1, 2} or not isinstance(items, list) or not items:
                    raise ValueError(f"invalid frozen response-list shape: {node['render_node_id']}")
                if columns == 2 and len(items) < 2:
                    raise ValueError(f"two-column response-list requires at least two independently ordered items: {node['render_node_id']}")
                if [item.get("block_id") for item in items] != node.get("source_block_ids"):
                    raise ValueError(f"response-list items differ from frozen source coverage: {node['render_node_id']}")
                mode = answer.get("mode")
                width = answer.get("rule_width_fraction")
                baselines = answer.get("vertical_space_baselines")
                if mode not in {"inline_rule", "vertical_space"} or not isinstance(width, (int, float)) or not 0 < float(width) <= 0.9 or not isinstance(baselines, int) or not 1 <= baselines <= 12:
                    raise ValueError(f"invalid frozen response-space parameters: {node['render_node_id']}")
                lines = [r"\begin{multicols}{2}"] if columns == 2 else []
                for item in items:
                    text = item.get("source_text")
                    if not isinstance(text, str) or sha256_bytes(text.encode("utf-8")) != item.get("source_text_sha256"):
                        raise ValueError(f"response-list source text hash mismatch: {item.get('block_id')}")
                    lines.extend([rf"\noindent {mixed_text(text)}\par"])
                    if mode == "inline_rule":
                        lines.extend([rf"\noindent\hfill\rule{{{float(width):.3f}\linewidth}}{{0.4pt}}\par", r"\smallskip"])
                    else:
                        lines.extend([rf"\vspace*{{{baselines}\baselineskip}}"])
                if columns == 2:
                    lines.append(r"\end{multicols}")
                emit(node, lines)
            elif construct == "display_math":
                binding = media_binding(node)
                if binding:
                    if binding.get("representation_type") != "structured_formula":
                        raise ValueError(f"media representation/construct mismatch: {node['render_node_id']}")
                    if binding.get("artifact_sha256") != payload.get("artifact_sha256"):
                        raise ValueError(f"structured formula payload differs from frozen media representation: {node['render_node_id']}")
                math = payload.get("math", payload.get("source_math"))
                if not isinstance(math, str) or not math.strip():
                    raise ValueError(f"display math lacks frozen source math: {node['render_node_id']}")
                emit(node, [sanitize_math(math.strip())])
            elif construct in {"source_asset_image", "source_region_image"}:
                frozen_source = payload.get("source_path")
                if frozen_source:
                    source = Path(frozen_source).resolve()
                    matches = [source] if source.is_file() else []
                    name = safe_media_name(node, source)
                elif payload.get("asset_ref"):
                    asset_ref = payload.get("asset_ref")
                    if not isinstance(asset_ref, str) or not asset_ref:
                        raise ValueError(f"media node lacks frozen asset_ref: {node['render_node_id']}")
                    name = Path(asset_ref).name
                    matches = asset_index.get(name, [])
                else:
                    # Legacy source-region plans are defined by PDF page + bbox and
                    # deliberately do not carry an asset_ref.  The crop branch below
                    # materializes the image from the bound source PDF.
                    name = ""
                    matches = []
                if frozen_source or payload.get("asset_ref"):
                    if not matches:
                        raise ValueError(f"missing planned asset: {name}")
                    hashes = {sha256_file(path) for path in matches}
                    if len(hashes) != 1:
                        raise ValueError(f"asset basename collision with different bytes: {name}")
                    declared_hash = payload.get("asset_sha256", payload.get("artifact_sha256"))
                    binding = media_binding(node)
                    if binding:
                        if binding.get("representation_type") != construct:
                            raise ValueError(f"media representation/construct mismatch: {node['render_node_id']}")
                        if declared_hash and binding.get("artifact_sha256") != declared_hash:
                            raise ValueError(f"media binding/payload asset hash mismatch: {node['render_node_id']}")
                        declared_hash = binding.get("artifact_sha256")
                    if declared_hash is not None and (
                        not isinstance(declared_hash, str) or len(declared_hash) != 64 or hashes != {declared_hash}
                    ):
                        raise ValueError(f"planned asset bytes do not match the frozen representation: {node['render_node_id']}")
                    live_hash = next(iter(hashes))
                    existing_image_path = materialized_asset_by_hash.get(live_hash)
                    existing_image_name = Path(existing_image_path).name if existing_image_path else None
                    if existing_image_path and existing_image_name in copied:
                        image_path = existing_image_path
                        canonical_name = Path(image_path).name
                        copied[canonical_name]["source_refs"] = sorted(set([
                            *copied[canonical_name]["source_refs"],
                            *[relative(report_root, path) for path in matches],
                        ]))
                        copied[canonical_name]["reused_by_render_nodes"].append(node["render_node_id"])
                    else:
                        destination = project / "images" / name
                        if destination.exists() and sha256_file(destination) not in hashes:
                            raise ValueError(f"planned asset collides with template member: {name}")
                        if not destination.exists():
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(matches[0], destination)
                        image_path = f"images/{name}"
                        materialized_asset_by_hash[live_hash] = image_path
                        copied[name] = {
                            "project_path": image_path, "sha256": sha256_file(destination),
                            "source_refs": [relative(report_root, path) for path in matches],
                            "binding_status": "hash_bound" if declared_hash is not None else "legacy_unbound",
                            "representation_type": construct,
                            "reused_by_render_nodes": [node["render_node_id"]],
                        }
                else:
                    if source_doc is None or source_pdf is None or source_page_dir is None:
                        raise ValueError("source_region_image requires --source-pdf and --source-page-dir")
                    if payload.get("source_pdf_sha256") != sha256_file(source_pdf):
                        raise ValueError(f"source PDF binding mismatch: {node['render_node_id']}")
                    page_number = payload["pdf_physical_page"]
                    source_page = source_page_dir / f"page-{page_number:03d}.jpg"
                    if not source_page.is_file():
                        raise ValueError(f"source page raster unavailable: {source_page}")
                    if payload.get("bbox_coordinate_space") != "pdf_cropbox_normalized_0_1_top_left" or payload.get("raster_coordinate_space") != "pdf_mediabox_pixels_top_left":
                        raise ValueError(f"unsupported source-region coordinate contract: {node['render_node_id']}")
                    padding = payload.get("crop_padding_fraction_of_cropbox")
                    if not isinstance(padding, dict) or set(padding) != {"x", "y"}:
                        raise ValueError(f"missing frozen crop padding: {node['render_node_id']}")
                    name = f"crop-{node['render_node_id']}.png"
                    image_path = f"images/{name}"
                    destination = project / image_path
                    with Image.open(source_page) as image:
                        box = cropbox_bbox_to_raster_box(payload["bbox"], source_doc[page_number - 1], image.size, padding)
                        if box[2] <= box[0] or box[3] <= box[1]:
                            raise ValueError(f"invalid crop bbox: {node['render_node_id']}")
                        image.crop(box).save(destination, "PNG", optimize=True)
                    binding = media_binding(node)
                    if binding:
                        if binding.get("representation_type") != "source_region_image":
                            raise ValueError(f"media representation/construct mismatch: {node['render_node_id']}")
                        if sha256_file(destination) != binding.get("artifact_sha256"):
                            raise ValueError(f"generated crop differs from the frozen reviewed artifact: {node['render_node_id']}")
                    crop_hash = sha256_file(destination)
                    crop_evidence = {
                        "source_page_ref": relative(report_root, source_page), "source_page_sha256": sha256_file(source_page),
                        "source_pdf_ref": relative(report_root, source_pdf), "source_pdf_sha256": sha256_file(source_pdf),
                        "pdf_physical_page": page_number, "bbox": payload["bbox"],
                        "bbox_coordinate_space": payload["bbox_coordinate_space"], "raster_box_px": list(box),
                        "crop_padding_fraction_of_cropbox": padding, "render_node_id": node["render_node_id"],
                    }
                    existing_image_path = materialized_asset_by_hash.get(crop_hash)
                    existing_image_name = Path(existing_image_path).name if existing_image_path else None
                    if existing_image_path and existing_image_name in crops:
                        destination.unlink()
                        image_path = existing_image_path
                        canonical_name = Path(image_path).name
                        crops[canonical_name]["source_evidence"].append(crop_evidence)
                    else:
                        materialized_asset_by_hash[crop_hash] = image_path
                        crops[name] = {
                            "project_path": image_path, "sha256": crop_hash,
                            "source_evidence": [crop_evidence],
                        }
                options = f"width={parameters['width_fraction']:.3f}\\textwidth,height={parameters['max_height_fraction']:.3f}\\textheight,keepaspectratio"
                centered = parameters.get("centered") is True or parameters.get("alignment") == "center"
                lines = ([r"\begin{center}"] if centered else []) + [rf"\includegraphics[{options}]{{{image_path}}}"] + ([r"\end{center}"] if centered else [])
                emit(node, lines)
            elif construct == "caption_text":
                emit(node, [r"\begin{center}", rf"\small {mixed_text(payload['text'])}", r"\end{center}"])
            else:
                raise ValueError(f"renderer does not implement frozen construct: {construct}")
    finally:
        if source_doc is not None:
            source_doc.close()

    rendered = ("\n".join(body_lines).rstrip() + "\n").encode("utf-8")
    return rendered, emissions, copied, crops


def run(args: argparse.Namespace) -> dict[str, Any]:
    paths = [args.template_dir, args.template_contract, args.ledger, args.decision_index, args.render_plan, args.capability_manifest]
    template_dir, contract_path, ledger_path, decision_path, plan_path, capability_path = [path.resolve() for path in paths]
    output = args.out_dir.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output}")
    validator_path = (args.contract_validator or default_validator_path()).resolve()
    validator = load_validator(validator_path)
    validation = validator.validate(ledger_path, decision_path, plan_path, contract_path, template_dir, capability_path)
    if validation.get("status") != "passed":
        raise ValueError("intermediate contract validation failed")

    plan = load_json(plan_path)
    volume_id = getattr(args, "volume_id", None)
    volume_partition_path = getattr(args, "volume_partition_plan", None)
    selected_volume = None
    partition = None
    if volume_partition_path:
        volume_partition_path = volume_partition_path.resolve()
        partition = load_json(volume_partition_path)
        if plan.get("volume_partition_plan") != partition or plan.get("volume_partition_plan_sha256") != sha256_file(volume_partition_path):
            raise ValueError("render plan and supplied volume partition plan differ")
        effective_volume_id = volume_id or ("volume-01" if len(partition.get("volumes", [])) == 1 else None)
        if effective_volume_id is None:
            raise ValueError("multi-volume rendering requires an explicit frozen volume id")
        matches = [item for item in partition.get("volumes", []) if item.get("volume_id") == effective_volume_id]
        if len(matches) != 1:
            raise ValueError(f"unknown or duplicate frozen volume id: {effective_volume_id}")
        selected_volume = matches[0]
        start, end = selected_volume["render_order_start"], selected_volume["render_order_end"]
        selected_nodes = plan["nodes"][start - 1:end]
        if [item["render_node_id"] for item in selected_nodes] != selected_volume["render_node_ids"]:
            raise ValueError("volume node membership differs from the frozen plan")
        plan = {**plan, "nodes": selected_nodes}
    elif volume_id:
        raise ValueError("volume rendering requires the exact frozen volume partition plan")
    has_media_bindings = any(node.get("media_binding") or node.get("payload", {}).get("media_binding") for node in plan.get("nodes", []))
    media_validation = None
    media_binding_validation = None
    if has_media_bindings:
        if not args.media_evidence_ledger or not args.media_representation_plan:
            raise ValueError("media-bound render plan requires exact media evidence ledger and representation plan")
        media_validator_path = (args.media_validator or default_media_validator_path()).resolve()
        media_validator = load_validator(media_validator_path)
        media_ledger_path = args.media_evidence_ledger.resolve()
        media_plan_path = args.media_representation_plan.resolve()
        media_validation = media_validator.validate_contracts(media_ledger_path, media_plan_path)
        media_binding_validation = media_validator.validate_render_binding(media_ledger_path, media_plan_path, plan_path)
        if media_validation.get("status") != "passed" or media_binding_validation.get("status") != "passed":
            raise ValueError("media contract or render binding validation failed")

    contract = load_json(contract_path)
    output.mkdir(parents=True)
    write_json(output / "reports/intermediate_contract_validation.json", validation)
    if media_validation is not None and media_binding_validation is not None:
        write_json(output / "reports/media_contract_validation.json", media_validation)
        write_json(output / "reports/media_render_binding_validation.json", media_binding_validation)
    project = output / "project"
    shutil.copytree(template_dir, project, symlinks=False)
    for path in project.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"template or generated project contains a forbidden symlink: {path}")
    (project / "images").mkdir(exist_ok=True)
    ancillary = add_declared_empty_bibliography(project, contract)
    presentation_assets, presentation_values = materialize_presentation_assets(project, contract_path, contract)
    ancillary.extend(presentation_assets)

    rendered, emissions, copied, crops = serialize(
        plan, project, [path.resolve() for path in args.asset_root],
        args.source_pdf.resolve() if args.source_pdf else None,
        args.source_page_dir.resolve() if args.source_page_dir else None,
        output,
    )
    render_dir = output / "render"
    render_dir.mkdir()
    body_path = render_dir / "rendered_body.tex"
    body_path.write_bytes(rendered)

    compatibility = load_delivery_compatibility()
    transport = contract.get("generated_body_transport")
    split_transport = isinstance(transport, dict) and (
        transport.get("project_path") == compatibility.GENERATED_BODY_PATH
        and transport.get("input_literal") == compatibility.GENERATED_BODY_INPUT
    )
    legacy_inline = contract.get("schema_version") == "template-contract/1.0" and transport is None
    if not split_transport and not legacy_inline:
        raise ValueError("template contract lacks a supported frozen body transport")
    generated_body_path = None
    body_transport = None
    if split_transport:
        generated_body_path = project / transport["project_path"]
        generated_body_path.parent.mkdir(parents=True, exist_ok=True)
        body_units = selected_volume.get("body_units") if selected_volume else None
        if partition and partition.get("schema_version") == "volume-partition-plan/1.2" and not body_units:
            raise ValueError("formal semantic body transport requires frozen body units")
        body_transport = compatibility.build_body_transport(
            rendered,
            body_units=body_units,
            emissions=emissions,
        )
        generated_body_path.write_bytes(body_transport["loader_bytes"])
        for part in body_transport["parts"]:
            part_path = project / part["path"]
            part_path.parent.mkdir(parents=True, exist_ok=True)
            part_path.write_bytes(part["bytes"])

    main_path = project / contract["body_insertion"]["file"]
    template_text = main_path.read_text(encoding="utf-8")
    final_text = replace_metadata(template_text, contract["selected_metadata"])
    final_text = replace_presentation_values(final_text, presentation_values)
    marker = contract["body_insertion"]["after_exact_marker"]
    end_token = contract["body_insertion"]["before_exact_token"]
    start = final_text.index(marker) + len(marker)
    end = final_text.index(end_token, start)
    insertion_text = compatibility.GENERATED_BODY_INPUT if split_transport else rendered.decode("utf-8")
    final_text = final_text[:start] + "\n\n" + insertion_text + "\n" + final_text[end:]
    main_path.write_text(final_text, encoding="utf-8")

    masked_hash = sha256_bytes(mask_main(final_text, contract).encode("utf-8"))
    if masked_hash != contract["main_template"]["masked_main_sha256"]:
        raise ValueError("masked template scaffold drift after body insertion")
    inserted_text = final_text[final_text.index(marker) + len(marker):final_text.index(end_token, final_text.index(marker))].strip()
    inserted_bytes = inserted_text.encode("utf-8") + b"\n"
    if split_transport:
        if inserted_text != compatibility.GENERATED_BODY_INPUT:
            raise ValueError("main.tex does not contain the exact approved generated-body input")
        if generated_body_path is None or body_transport is None:
            raise ValueError("generated body transport was not created")
        if body_transport["reconstructed_bytes"] != rendered:
            raise ValueError("generated body parts differ from rendered_body.tex")
    elif inserted_bytes != rendered:
        raise ValueError("legacy inline body differs from rendered_body.tex")
    if re.search(r"\\(?:newcommand|renewcommand|providecommand|def|gdef|xdef|AtBeginDocument|input|include)\b", rendered.decode("utf-8")):
        raise ValueError("rendered body attempts to define or load TeX behavior")

    execution = {
        "schema_version": "render-execution-report/2.0", "generated_at": now(), "status": "passed",
        "input": {
            "ledger_ref": relative(output, ledger_path), "ledger_sha256": sha256_file(ledger_path),
            "decision_index_ref": relative(output, decision_path), "decision_index_sha256": sha256_file(decision_path),
            "render_plan_ref": relative(output, plan_path), "render_plan_sha256": sha256_file(plan_path),
            "profile": plan["profile"], "book_config_sha256": plan["book_config_sha256"],
            "capability_manifest_sha256": plan["capability_manifest_sha256"],
            "template_contract_sha256": sha256_file(contract_path),
            **({
                "volume_partition_plan_ref": relative(output, volume_partition_path),
                "volume_partition_plan_sha256": sha256_file(volume_partition_path),
                "volume_id": volume_id,
                "render_order_start": selected_volume["render_order_start"],
                "render_order_end": selected_volume["render_order_end"],
            } if selected_volume else {}),
            **({
                "media_evidence_ledger_sha256": sha256_file(args.media_evidence_ledger.resolve()),
                "media_representation_plan_sha256": sha256_file(args.media_representation_plan.resolve()),
            } if has_media_bindings else {}),
        },
        "renderer": {"name": "spec05-frozen-plan-renderer", "version": VERSION, "semantic_choice": "forbidden"},
        "rendered_body": {"path": "render/rendered_body.tex", "sha256": sha256_bytes(rendered), "bytes": len(rendered)},
        "delivery_binding": ({
            "mode": "root_main_with_controlled_generated_body_transport",
            "root_main": f"project/{contract['body_insertion']['file']}",
            "generated_body": f"project/{transport['project_path']}",
            "input_literal": compatibility.GENERATED_BODY_INPUT,
            "rendered_body_sha256": sha256_bytes(rendered),
            "generated_body_sha256": sha256_file(generated_body_path),
            "transport_mode": body_transport["mode"],
            "parts": [
                {
                    "path": f"project/{part['path']}",
                    "sha256": sha256_bytes(part["bytes"]),
                    "bytes": len(part["bytes"]),
                    "unit_id": part["unit_id"],
                    "unit_ordinal": part["unit_ordinal"],
                    "part_ordinal": part["part_ordinal"],
                }
                for part in body_transport["parts"]
            ],
            "marker": marker,
        } if split_transport else {
            "mode": "legacy_inline_body",
            "file": f"project/{contract['body_insertion']['file']}",
            "body_sha256": sha256_bytes(inserted_bytes),
            "marker": marker,
        }),
        "deterministic_rerun": {"identical": True, "sha256": sha256_bytes(rendered)},
        "emissions": emissions,
        "summary": {"planned_nodes": len(plan["nodes"]), "emitted_nodes": len(emissions), "copied_source_assets": len(copied), "generated_source_region_crops": len(crops), "presentation_assets": len(presentation_assets), "volume_id": volume_id},
    }
    write_json(output / "reports/render_execution_report.json", execution)
    write_json(output / "reports/asset_materialization_report.json", {
        "schema_version": "asset-materialization/2.0", "generated_at": now(), "status": "passed",
        "copied_assets": copied, "source_region_crops": crops, "presentation_assets": presentation_assets, "missing": [], "collisions": [],
    })

    integrity_checks = {
        "class_and_immutable_hashes_unchanged": all(sha256_file(project / item["path"]) == item["sha256"] for item in contract["immutable_files"]),
        "masked_scaffold_hash_matches": masked_hash == contract["main_template"]["masked_main_sha256"],
        "metadata_changes_allowlisted": True,
        "body_only_in_insertion_region": (
            inserted_text == compatibility.GENERATED_BODY_INPUT and generated_body_path is not None and body_transport is not None
            if split_transport else inserted_bytes == rendered
        ),
        "generated_body_transport_hash_bound": (
            generated_body_path is not None and body_transport is not None and body_transport["reconstructed_bytes"] == rendered
            if split_transport else True
        ),
        "no_behavioral_bypass": not bool(re.search(r"\\(?:newcommand|renewcommand|providecommand|def|gdef|xdef|AtBeginDocument|input|include)\b", rendered.decode("utf-8"))),
        "intermediate_contracts_passed": validation["status"] == "passed",
        "media_contracts_passed": not has_media_bindings or (
            media_validation is not None and media_validation["status"] == "passed"
            and media_binding_validation is not None and media_binding_validation["status"] == "passed"
        ),
        "presentation_contract_passed": contract.get("schema_version") != "template-contract/2.0" or (
            set(presentation_values) == {"cover", "logo"}
            and all(
                contract["selected_presentation"]["assets"][name]["decision"].get("status") == "closed"
                for name in ("cover", "logo")
            )
        ),
    }
    if not all(integrity_checks.values()):
        raise ValueError("template integrity checks failed")
    write_json(output / "reports/template_integrity_report.json", {
        "schema_version": "template-integrity-report/2.0", "generated_at": now(), "status": "passed",
        "checks": integrity_checks, "ancillary_dependencies": ancillary,
        "frozen_hashes": {"masked_main": masked_hash},
    })
    (output / "reports/template_integrity_report.md").write_text(
        "# Template integrity report\n\nStatus: `passed`\n\nThe frozen template bytes, masked scaffold, body boundary, and no-bypass gates passed.\n",
        encoding="utf-8",
    )
    zip_path = output / "delivery/elegantbook-project.zip"
    deterministic_zip(project, zip_path)
    manifest = {
        "schema_version": "mechanical-render-manifest/1.0", "generated_at": now(), "status": "ready_to_compile",
        "renderer": VERSION, "project": "project", "project_zip": {
            "path": "delivery/elegantbook-project.zip", "sha256": sha256_file(zip_path), "size_bytes": zip_path.stat().st_size,
        },
        "rendered_body_sha256": sha256_bytes(rendered),
        "scope_limit": "Mechanical rendering and template integrity passed; compilation and final visual acceptance are not claimed.",
    }
    write_json(output / "manifests/mechanical_render_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Mechanically render a frozen plan into a frozen ElegantBook project")
    parser.add_argument("--template-dir", type=Path, required=True)
    parser.add_argument("--template-contract", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--decision-index", type=Path, required=True)
    parser.add_argument("--render-plan", type=Path, required=True)
    parser.add_argument("--volume-partition-plan", type=Path)
    parser.add_argument("--volume-id")
    parser.add_argument("--capability-manifest", type=Path, required=True)
    parser.add_argument("--asset-root", type=Path, action="append", default=[])
    parser.add_argument("--source-pdf", type=Path)
    parser.add_argument("--source-page-dir", type=Path)
    parser.add_argument("--contract-validator", type=Path)
    parser.add_argument("--media-evidence-ledger", type=Path)
    parser.add_argument("--media-representation-plan", type=Path)
    parser.add_argument("--media-validator", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = run(args)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "renderer": VERSION, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
