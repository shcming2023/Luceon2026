#!/usr/bin/env python3
import argparse
import json
import re
import shlex
import subprocess
from pathlib import Path


DEFAULT_BUCKETS = {
    "input": "eduassets-input",
    "mineru": "eduassets-mineru",
    "minerupopo": "eduassets-minerupopo",
    "raw": "eduassets-raw",
}


def run_mc(script, container="minio"):
    wrapped = (
        'tmp="/tmp/mc-codex-$$"; mkdir -p "$tmp"; '
        'MC_CONFIG_DIR="$tmp" mc alias set local http://127.0.0.1:9000 '
        '"$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null; '
        f"{script}; "
        'rc=$?; rm -rf "$tmp"; exit $rc'
    )
    proc = subprocess.run(
        ["docker", "exec", container, "sh", "-lc", wrapped],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout


def mc_ls(path, container="minio"):
    return run_mc(f"MC_CONFIG_DIR=\"$tmp\" mc ls {shlex.quote(path)}", container=container)


def prefix_names(path, container="minio"):
    names = []
    for line in mc_ls(path, container=container).splitlines():
        match = re.search(r"\s(\S+/)$", line)
        if match:
            names.append(match.group(1).rstrip("/"))
    return names


def object_names(path, recursive=False, container="minio"):
    flag = "--recursive " if recursive else ""
    out = run_mc(f"MC_CONFIG_DIR=\"$tmp\" mc ls {flag}{shlex.quote(path)}", container=container)
    names = []
    for line in out.splitlines():
        parts = line.split()
        if parts:
            names.append(parts[-1])
    return names


def safe_cat_json(path, container="minio"):
    try:
        out = run_mc(f"MC_CONFIG_DIR=\"$tmp\" mc cat {shlex.quote(path)}", container=container)
        return json.loads(out)
    except (RuntimeError, json.JSONDecodeError):
        return {}


def safe_ls(path, container="minio"):
    try:
        return mc_ls(path, container=container)
    except RuntimeError:
        return ""


def safe_object_names(path, recursive=False, container="minio"):
    try:
        return object_names(path, recursive=recursive, container=container)
    except RuntimeError:
        return []


def safe_prefix_names(path, container="minio"):
    try:
        return prefix_names(path, container=container)
    except RuntimeError:
        return []


def mineru_run_id_from_manifest_object(pdf_id, manifest_object):
    match = re.search(r"(?:^|/)mineru/%s/([^/]+)/manifest\.json$" % re.escape(pdf_id), str(manifest_object or ""))
    return match.group(1) if match else ""


def resolve_mineru_for_popo_task(pdf_id, popo_job_id, buckets, container):
    popo_manifest_path = f"local/{buckets['minerupopo']}/minerupopo/{pdf_id}/{popo_job_id}/manifest.json"
    popo_manifest = safe_cat_json(popo_manifest_path, container=container)
    upstream = popo_manifest.get("upstream_mineru") if isinstance(popo_manifest.get("upstream_mineru"), dict) else {}
    lineage = popo_manifest.get("lineage") if isinstance(popo_manifest.get("lineage"), dict) else {}
    stage_run_ids = popo_manifest.get("stage_run_ids") if isinstance(popo_manifest.get("stage_run_ids"), dict) else {}
    upstream_manifest = upstream.get("manifest") if isinstance(upstream.get("manifest"), dict) else {}
    lineage_manifest = lineage.get("upstream_manifest") if isinstance(lineage.get("upstream_manifest"), dict) else {}
    manifest_object = str(upstream_manifest.get("object") or lineage_manifest.get("object") or "")
    candidates = []
    inferred_run_id = mineru_run_id_from_manifest_object(pdf_id, manifest_object)
    for run_id, strategy in [
        (str(upstream.get("run_id") or ""), "popo_manifest_upstream_mineru.run_id"),
        (str(stage_run_ids.get("mineru") or ""), "popo_manifest_stage_run_ids.mineru"),
        (inferred_run_id, "popo_manifest_upstream_manifest_object"),
        (popo_job_id, "legacy_same_run_id"),
    ]:
        if run_id and run_id not in [item["job_id"] for item in candidates]:
            candidates.append({"job_id": run_id, "strategy": strategy})

    for candidate in candidates:
        mineru_prefix = f"local/{buckets['mineru']}/mineru/{pdf_id}/{candidate['job_id']}"
        if safe_ls(mineru_prefix, container=container).strip():
            return {
                "job_id": candidate["job_id"],
                "prefix": mineru_prefix,
                "link_strategy": candidate["strategy"],
                "upstream_manifest_object": manifest_object,
                "popo_manifest_path": popo_manifest_path.replace("local/", ""),
            }

    mineru_root = f"local/{buckets['mineru']}/mineru/{pdf_id}"
    for run_id in safe_prefix_names(mineru_root, container=container):
        mineru_prefix = f"{mineru_root}/{run_id}"
        listing = safe_ls(mineru_prefix, container=container)
        if listing.strip():
            return {
                "job_id": run_id,
                "prefix": mineru_prefix,
                "link_strategy": "material_id_fallback_first_available_mineru",
                "upstream_manifest_object": manifest_object,
                "popo_manifest_path": popo_manifest_path.replace("local/", ""),
            }

    return {
        "job_id": "",
        "prefix": f"local/{buckets['mineru']}/mineru/{pdf_id}/{popo_job_id}",
        "link_strategy": "unresolved",
        "upstream_manifest_object": manifest_object,
        "popo_manifest_path": popo_manifest_path.replace("local/", ""),
    }


def summarize_task(pdf_id, job_id, buckets, container):
    mineru = resolve_mineru_for_popo_task(pdf_id, job_id, buckets, container)
    mineru_job_id = mineru.get("job_id") or ""
    mineru_prefix = mineru.get("prefix") or f"local/{buckets['mineru']}/mineru/{pdf_id}/{mineru_job_id or job_id}"
    popo_prefix = f"local/{buckets['minerupopo']}/minerupopo/{pdf_id}/{job_id}"
    raw_prefix = f"local/{buckets['raw']}/raw/{pdf_id}/{job_id}"
    status_prefix = f"local/{buckets['input']}/_status/{pdf_id}"
    status_files = safe_object_names(status_prefix, recursive=True, container=container)
    status_for_job = [name for name in status_files if f"{job_id}." in name or f"{job_id}/" in name]
    status_json = {}
    if status_for_job:
        status_json = safe_cat_json(f"{status_prefix}/{status_for_job[0]}", container=container)
    status_kind = "unknown"
    if any(name.endswith(".done.json") for name in status_for_job):
        status_kind = "done"
    elif any(name.endswith(".error.json") for name in status_for_job):
        status_kind = "error"
    has_mineru = bool(safe_ls(mineru_prefix, container=container).strip())
    has_minerupopo = bool(safe_ls(popo_prefix, container=container).strip())
    has_raw = bool(safe_ls(raw_prefix, container=container).strip())
    source_ready = status_kind == "done" and has_mineru and has_minerupopo
    if not source_ready:
        rebuild_state = "blocked"
    elif has_raw:
        rebuild_state = "published"
    else:
        rebuild_state = "not_started"
    return {
        "pdf_id": pdf_id,
        "job_id": job_id,
        "popo_job_id": job_id,
        "mineru_job_id": mineru_job_id,
        "pdf_name": status_json.get("object") or status_json.get("wrapper_status", {}).get("filename") or "",
        "source_hash": status_json.get("source_hash") or "",
        "source_pdf_sha256": status_json.get("source_pdf_sha256") or status_json.get("wrapper_status", {}).get("source", {}).get("sha256") or "",
        "source_pdf_size_bytes": status_json.get("source_pdf_size_bytes") or status_json.get("wrapper_status", {}).get("source", {}).get("size_bytes"),
        "status": status_kind,
        "source_ready": source_ready,
        "rebuild_state": rebuild_state,
        "minerupopo_prefix": popo_prefix.replace("local/", ""),
        "mineru_prefix": mineru_prefix.replace("local/", ""),
        "mineru_link_strategy": mineru.get("link_strategy") or "unknown",
        "upstream_mineru_manifest": mineru.get("upstream_manifest_object") or "",
        "popo_manifest_path": mineru.get("popo_manifest_path") or "",
        "raw_prefix": raw_prefix.replace("local/", ""),
        "input_status_prefix": status_prefix.replace("local/", ""),
        "has_mineru": has_mineru,
        "has_minerupopo": has_minerupopo,
        "has_raw": has_raw,
        "status_files": status_for_job,
    }


def build_discovery(args):
    buckets = DEFAULT_BUCKETS | {
        "input": args.input_bucket,
        "mineru": args.mineru_bucket,
        "minerupopo": args.minerupopo_bucket,
        "raw": args.raw_bucket,
    }
    popo_root = f"local/{buckets['minerupopo']}/minerupopo"
    pdf_ids = prefix_names(popo_root, container=args.container)
    tasks = []
    for pdf_id in pdf_ids:
        if args.pdf_id and pdf_id != args.pdf_id:
            continue
        job_ids = prefix_names(f"{popo_root}/{pdf_id}", container=args.container)
        for job_id in job_ids:
            if args.job_id and job_id != args.job_id:
                continue
            tasks.append(summarize_task(pdf_id, job_id, buckets, args.container))
    listed_tasks = tasks[: args.limit] if args.limit else tasks
    result = {
        "container": args.container,
        "buckets": buckets,
        "total_task_count": len(tasks),
        "listed_task_count": len(listed_tasks),
        "tasks": listed_tasks,
    }
    return result


def discover(args):
    result = build_discovery(args)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Discover rebuildable tasks from local MinIO eduassets buckets.")
    parser.add_argument("--container", default="minio")
    parser.add_argument("--input-bucket", default=DEFAULT_BUCKETS["input"])
    parser.add_argument("--mineru-bucket", default=DEFAULT_BUCKETS["mineru"])
    parser.add_argument("--minerupopo-bucket", default=DEFAULT_BUCKETS["minerupopo"])
    parser.add_argument("--raw-bucket", default=DEFAULT_BUCKETS["raw"])
    parser.add_argument("--pdf-id")
    parser.add_argument("--job-id")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--limit", type=int, help="Limit the number of tasks printed/written for sampling.")
    args = parser.parse_args()
    discover(args)


if __name__ == "__main__":
    main()
