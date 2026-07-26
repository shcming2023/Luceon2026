#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.workflow_v3.release_recipe import (  # noqa: E402
    RECIPE_SCHEMA_VERSION,
    ReleaseRecipeError,
    _canonical_source_tree,
    _sha256_file,
    _tree_records,
    _walk_tree,
    _walk_zip,
)


def _roots(recipe: dict[str, Any], recipe_path: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    raw_roots = recipe.get("roots")
    if not isinstance(raw_roots, dict):
        raise ReleaseRecipeError("roots must be an object")
    for name, raw in raw_roots.items():
        if isinstance(raw, str):
            candidate = Path(raw).expanduser()
        elif (
            isinstance(raw, dict)
            and set(raw) == {"relative_to_recipe"}
            and isinstance(raw["relative_to_recipe"], str)
            and raw["relative_to_recipe"]
            and not Path(raw["relative_to_recipe"]).is_absolute()
        ):
            candidate = recipe_path.parent / raw["relative_to_recipe"]
        else:
            raise ReleaseRecipeError(f"root {name!r} is invalid")
        resolved = candidate.resolve()
        if not resolved.is_dir() or resolved.is_symlink():
            raise ReleaseRecipeError(f"root {name!r} is unavailable: {resolved}")
        result[str(name)] = resolved
    return result


def _patterns(source: dict[str, Any], key: str) -> list[str]:
    value = source.get(key, [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ReleaseRecipeError(f"{source.get('id')}.{key} is invalid")
    return value


def refreshed(recipe_path: Path) -> tuple[dict[str, Any], list[str]]:
    try:
        recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseRecipeError(f"cannot read recipe: {exc}") from exc
    if (
        not isinstance(recipe, dict)
        or recipe.get("schema_version") != RECIPE_SCHEMA_VERSION
    ):
        raise ReleaseRecipeError("recipe schema_version is invalid")
    roots = _roots(recipe, recipe_path)
    sources = recipe.get("sources")
    if not isinstance(sources, list):
        raise ReleaseRecipeError("sources must be an array")
    changed: list[str] = []
    for raw in sources:
        if not isinstance(raw, dict):
            raise ReleaseRecipeError("source is not an object")
        source_id = str(raw.get("id") or "")
        kind = raw.get("kind")
        root = roots.get(str(raw.get("root") or ""))
        relative = raw.get("path")
        if (
            not source_id
            or root is None
            or not isinstance(relative, str)
            or not relative
        ):
            raise ReleaseRecipeError(f"source {source_id!r} is incomplete")
        path = root / relative
        include = _patterns(raw, "include")
        exclude = _patterns(raw, "exclude")
        before = (
            raw.get("expected_sha256"),
            raw.get("expected_tree_sha256"),
        )
        if kind == "file":
            raw["expected_sha256"] = _sha256_file(path)
        elif kind == "tree":
            files = _walk_tree(path, include=include, exclude=exclude)
            raw["expected_tree_sha256"] = _canonical_source_tree(
                _tree_records(files)
            )
        elif kind == "zip_tree":
            raw["expected_sha256"] = _sha256_file(path)
            files = _walk_zip(
                path,
                member_prefix=str(raw.get("member_prefix") or ""),
                include=include,
                exclude=exclude,
            )
            raw["expected_tree_sha256"] = _canonical_source_tree(
                _tree_records(files)
            )
        else:
            raise ReleaseRecipeError(
                f"source {source_id!r} has unsupported kind {kind!r}"
            )
        after = (
            raw.get("expected_sha256"),
            raw.get("expected_tree_sha256"),
        )
        if before != after:
            changed.append(source_id)
    return recipe, changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh only source qualification hashes in a Worker V3 build recipe. "
            "The resulting recipe must still pass the independent release audit."
        )
    )
    parser.add_argument("--recipe", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        recipe_path = args.recipe.resolve()
        value, changed = refreshed(recipe_path)
        if args.check:
            print(
                json.dumps(
                    {"status": "passed" if not changed else "drifted", "changed": changed},
                    sort_keys=True,
                )
            )
            return 0 if not changed else 1
        temporary = recipe_path.with_name(f".{recipe_path.name}.refresh-{os.getpid()}")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(recipe_path)
        print(json.dumps({"status": "updated", "changed": changed}, sort_keys=True))
        return 0
    except ReleaseRecipeError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
