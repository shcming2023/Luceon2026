#!/usr/bin/env python3
import argparse
import json
import shlex
import shutil
import subprocess
import time
from pathlib import Path


RAW_BUCKET = "eduassets-raw"
RAW_PREFIX_ROOT = "raw/"


def run(cmd):
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout


def ensure_required(local_dir):
    required = ["clean.md", "preview.html", "manifest.json", "qa_report.md", "images"]
    missing = [name for name in required if not (local_dir / name).exists()]
    if not (local_dir / "outline-view.html").exists() and not (local_dir / "outline-anchor-check.html").exists():
        missing.append("outline-view.html or outline-anchor-check.html")
    if missing:
        raise RuntimeError(f"Missing required raw deliverables: {', '.join(missing)}")


def ensure_safe_target(raw_bucket, raw_prefix):
    if raw_bucket != RAW_BUCKET:
        raise RuntimeError(
            f"Unsafe publish target bucket: {raw_bucket}. "
            f"This skill may only write to {RAW_BUCKET}; upstream and downstream buckets are read-only."
        )
    if not raw_prefix.startswith(RAW_PREFIX_ROOT):
        raise RuntimeError(
            f"Unsafe publish target prefix: {raw_prefix}. "
            f"Semantic rebuild outputs must be stored under {RAW_BUCKET}/{RAW_PREFIX_ROOT}."
        )


def count_images(local_dir):
    images = local_dir / "images"
    return len([p for p in images.rglob("*") if p.is_file()]) if images.exists() else 0


def write_publish_manifest(local_dir, pdf_id, job_id, raw_bucket, raw_prefix):
    manifest_path = local_dir / "raw_publish_manifest.json"
    data = {
        "pdf_id": pdf_id,
        "job_id": job_id,
        "raw_bucket": raw_bucket,
        "raw_prefix": raw_prefix,
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "required_files": {
            "clean_md": (local_dir / "clean.md").exists(),
            "preview_html": (local_dir / "preview.html").exists(),
            "manifest_json": (local_dir / "manifest.json").exists(),
            "qa_report_md": (local_dir / "qa_report.md").exists(),
            "images_count": count_images(local_dir),
        },
        "role": "eduassets-raw stores semantic-rebuild clean master outputs and is the input layer for eduassets-clean.",
    }
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def publish(args):
    local_dir = args.local_dir.expanduser().resolve()
    ensure_required(local_dir)
    raw_prefix = args.prefix or f"raw/{args.pdf_id}/{args.job_id}/"
    ensure_safe_target(args.raw_bucket, raw_prefix)
    write_publish_manifest(local_dir, args.pdf_id, args.job_id, args.raw_bucket, raw_prefix)

    tmp_root = f"/tmp/pdf-clean-md-raw-publish-{int(time.time())}-{args.pdf_id}-{args.job_id}"
    container_src = f"{tmp_root}/payload"
    run(["docker", "exec", args.container, "rm", "-rf", tmp_root])
    run(["docker", "exec", args.container, "mkdir", "-p", tmp_root])
    run(["docker", "cp", str(local_dir), f"{args.container}:{container_src}"])
    script = (
        'tmp_cfg="/tmp/mc-codex-$$"; mkdir -p "$tmp_cfg"; '
        'MC_CONFIG_DIR="$tmp_cfg" mc alias set local http://127.0.0.1:9000 '
        '"$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null; '
        f"MC_CONFIG_DIR=\"$tmp_cfg\" mc mirror --overwrite {shlex.quote(container_src)}/ "
        f"{shlex.quote('local/' + args.raw_bucket + '/' + raw_prefix)}; "
        'rc=$?; rm -rf "$tmp_cfg"; exit $rc'
    )
    run(["docker", "exec", args.container, "sh", "-lc", script])
    run(["docker", "exec", args.container, "rm", "-rf", tmp_root])
    print(json.dumps({
        "published": True,
        "raw_bucket": args.raw_bucket,
        "raw_prefix": raw_prefix,
        "local_dir": str(local_dir),
        "images_count": count_images(local_dir),
    }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Publish semantic rebuild outputs to eduassets-raw in local MinIO.")
    parser.add_argument("local_dir", type=Path, help="Directory containing clean.md, images/, preview.html, manifest.json, qa_report.md.")
    parser.add_argument("--pdf-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--container", default="minio")
    parser.add_argument("--raw-bucket", default=RAW_BUCKET, help="Must remain eduassets-raw; other buckets are refused.")
    parser.add_argument("--prefix", help="Override target prefix. Must stay under raw/. Default: raw/<pdf-id>/<job-id>/")
    args = parser.parse_args()
    publish(args)


if __name__ == "__main__":
    main()
