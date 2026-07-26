#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "luceon.worker-v3-template-capability-qualification/v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _load_extractor(path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(
        "worker_v3_template_capability_extractor",
        path,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("template capability extractor cannot be loaded")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def qualify(
    *,
    archive: Path,
    extractor: Path,
    entry: str,
    class_member: str,
) -> dict[str, Any]:
    archive = archive.resolve()
    extractor = extractor.resolve()
    if not archive.is_file() or not extractor.is_file():
        raise FileNotFoundError("template archive or extractor is missing")
    with tempfile.TemporaryDirectory(prefix="worker-v3-template-qualification-") as raw:
        intake = Path(raw) / "template-intake.json"
        intake.write_text(
            json.dumps(
                {
                    "entry": entry,
                    "class": class_member,
                    "template_zip_sha256": _sha256(archive),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        extracted = _load_extractor(extractor).extract_template_capabilities(
            intake,
            archive,
        )
    portable = {
        key: value
        for key, value in extracted.items()
        if key
        not in {
            "generated_at",
            "template_intake",
            "template_archive",
            "capability_payload_hash",
        }
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "passed",
        "archive_sha256": _sha256(archive),
        "extractor_sha256": _sha256(extractor),
        "entry_member": entry,
        "class_member": class_member,
        "capabilities": portable,
    }
    payload["qualification_sha256"] = _canonical_hash(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create host-path-free capability evidence for the exact Worker V3 "
            "ElegantBook template and deterministic extractor."
        )
    )
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--extractor", required=True, type=Path)
    parser.add_argument("--entry", default="main.tex")
    parser.add_argument("--class-member", default="elegantbook.cls")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error(f"refusing to overwrite output: {args.output}")
    try:
        payload = qualify(
            archive=args.archive,
            extractor=args.extractor,
            entry=args.entry,
            class_member=args.class_member,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": payload["status"],
                "archive_sha256": payload["archive_sha256"],
                "qualification_sha256": payload["qualification_sha256"],
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
