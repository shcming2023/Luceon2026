#!/usr/bin/env python3
"""Guard and reproduce the Task36 live wrapper inventory patch.

This is a release candidate, not a deployment command. It never opens SSH or
calls Compshare. A future controlled deployment may use ``transform_source``
only after the exact remote source bytes match ``BEFORE_SHA256``; the resulting
bytes must then match ``AFTER_SHA256``. Already-patched bytes are accepted as a
verified no-op, while every other source revision fails closed as drift.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


BEFORE_SHA256 = "3c0be8255cd6e6bef37900413cea496f14a0af253aa37e0e7763c0511923310f"
AFTER_SHA256 = "cad0dbfe2e783c625d22c95931bf9495d577784de2eba9384118d1ee6e163673"
REMOTE_SOURCE_PATH = "/root/mineru-popo-service/wrapper_app.py"
REMOTE_ROLLBACK_BACKUP = "/root/mineru-popo-service/wrapper_app.py.task36-backup-20260806T093300Z"


OLD_BLOCK = '''@app.get("/api/v1/jobs", dependencies=[Depends(require_auth)])
def list_jobs(limit: int = 20) -> dict[str, Any]:
    rows = []
    for path in sorted(JOBS_ROOT.glob("*/status.json"), reverse=True)[: max(1, min(limit, 100))]:
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    return {"jobs": rows}


@app.get("/api/v1/batches", dependencies=[Depends(require_auth)])
def list_batches(limit: int = 20) -> dict[str, Any]:
    rows = []
    for path in sorted(BATCHES_ROOT.glob("*/status.json"), reverse=True)[: max(1, min(limit, 100))]:
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    return {"batches": rows}


@app.get("/api/v1/mineru/batches", dependencies=[Depends(require_auth)])
def list_mineru_batches(limit: int = 20) -> dict[str, Any]:
    rows = []
    for path in sorted(BATCHES_ROOT.glob("*/status.json"), reverse=True):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("batch_kind") == "mineru":
            rows.append(data)
        if len(rows) >= max(1, min(limit, 100)):
            break
    return {"batches": rows}


@app.get("/api/v1/popo/batches", dependencies=[Depends(require_auth)])
def list_popo_batches(limit: int = 20) -> dict[str, Any]:
    rows = []
    for path in sorted(BATCHES_ROOT.glob("*/status.json"), reverse=True):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("batch_kind") == "popo":
            rows.append(data)
        if len(rows) >= max(1, min(limit, 100)):
            break
    return {"batches": rows}
'''


NEW_BLOCK = '''def _paginated_inventory(rows: list[dict[str, Any]], *, limit: int, cursor: str, key: str) -> dict[str, Any]:
    page_limit = max(1, min(limit, 100))
    if cursor == "":
        offset = 0
    elif cursor.isdecimal():
        offset = int(cursor)
    else:
        raise HTTPException(status_code=422, detail="cursor must be a non-negative integer offset")
    total = len(rows)
    if offset > total:
        raise HTTPException(status_code=422, detail="cursor exceeds inventory total")
    page = rows[offset : offset + page_limit]
    next_offset = offset + len(page)
    has_more = next_offset < total
    return {
        key: page,
        "total": total,
        "next_cursor": str(next_offset) if has_more else "",
        "has_more": has_more,
    }


@app.get("/api/v1/jobs", dependencies=[Depends(require_auth)])
def list_jobs(limit: int = 20, cursor: str = "") -> dict[str, Any]:
    rows = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(JOBS_ROOT.glob("*/status.json"), reverse=True)
    ]
    return _paginated_inventory(rows, limit=limit, cursor=cursor, key="jobs")


@app.get("/api/v1/batches", dependencies=[Depends(require_auth)])
def list_batches(limit: int = 20, cursor: str = "") -> dict[str, Any]:
    rows = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(BATCHES_ROOT.glob("*/status.json"), reverse=True)
    ]
    return _paginated_inventory(rows, limit=limit, cursor=cursor, key="batches")


@app.get("/api/v1/mineru/batches", dependencies=[Depends(require_auth)])
def list_mineru_batches(limit: int = 20, cursor: str = "") -> dict[str, Any]:
    rows = []
    for path in sorted(BATCHES_ROOT.glob("*/status.json"), reverse=True):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("batch_kind") == "mineru":
            rows.append(data)
    return _paginated_inventory(rows, limit=limit, cursor=cursor, key="batches")


@app.get("/api/v1/popo/batches", dependencies=[Depends(require_auth)])
def list_popo_batches(limit: int = 20, cursor: str = "") -> dict[str, Any]:
    rows = []
    for path in sorted(BATCHES_ROOT.glob("*/status.json"), reverse=True):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("batch_kind") == "popo":
            rows.append(data)
    return _paginated_inventory(rows, limit=limit, cursor=cursor, key="batches")
'''


class WrapperSourceDrift(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transform_source(source: bytes, *, enforce_remote_identity: bool = True) -> tuple[bytes, str]:
    identity = sha256(source)
    if identity == AFTER_SHA256:
        return source, "already_patched"
    if enforce_remote_identity and identity != BEFORE_SHA256:
        raise WrapperSourceDrift(f"unexpected wrapper source sha256: {identity}")
    text = source.decode("utf-8")
    if text.count(OLD_BLOCK) != 1:
        raise WrapperSourceDrift("expected inventory block is not present exactly once")
    updated = text.replace(OLD_BLOCK, NEW_BLOCK).encode("utf-8")
    if enforce_remote_identity and sha256(updated) != AFTER_SHA256:
        raise WrapperSourceDrift("transformed wrapper source does not match frozen after identity")
    return updated, "transformed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.source.read_bytes()
    updated, status = transform_source(source)
    if args.output:
        args.output.write_bytes(updated)
    print(f"status={status} sha256={sha256(updated)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
