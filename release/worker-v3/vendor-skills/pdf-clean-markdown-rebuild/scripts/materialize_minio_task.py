#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path


DEFAULT_BUCKETS = {
    "input": "eduassets-input",
    "mineru": "eduassets-mineru",
    "minerupopo": "eduassets-minerupopo",
    "raw": "eduassets-raw",
}


def run(cmd):
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        details = "\n".join(
            part for part in [
                f"command failed rc={proc.returncode}: {' '.join(shlex.quote(str(x)) for x in cmd)}",
                f"stderr: {proc.stderr.strip()}" if proc.stderr.strip() else "",
                f"stdout: {proc.stdout.strip()}" if proc.stdout.strip() else "",
            ]
            if part
        )
        raise RuntimeError(details)
    return proc.stdout


def run_mc(container, script):
    wrapped = (
        'tmp_cfg="/tmp/mc-codex-$$"; mkdir -p "$tmp_cfg"; '
        'MC_CONFIG_DIR="$tmp_cfg" mc alias set local http://127.0.0.1:9000 '
        '"$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null; '
        f"{script}; "
        'rc=$?; rm -rf "$tmp_cfg"; exit $rc'
    )
    return run(["docker", "exec", container, "sh", "-lc", wrapped])


def mc_ls(container, path):
    return run_mc(container, f"MC_CONFIG_DIR=\"$tmp_cfg\" mc ls {shlex.quote(path)}")


def safe_ls(container, path):
    try:
        return mc_ls(container, path)
    except RuntimeError:
        return ""


def prefix_names(container, path):
    names = []
    for line in safe_ls(container, path).splitlines():
        match = re.search(r"\s(\S+/)$", line)
        if match:
            names.append(match.group(1).rstrip("/"))
    return names


def safe_cat_json(container, path):
    try:
        out = run_mc(container, f"MC_CONFIG_DIR=\"$tmp_cfg\" mc cat {shlex.quote(path)}")
        return json.loads(out)
    except (RuntimeError, json.JSONDecodeError):
        return {}


def mineru_run_id_from_manifest_object(pdf_id, manifest_object):
    match = re.search(r"(?:^|/)mineru/%s/([^/]+)/manifest\.json$" % re.escape(pdf_id), str(manifest_object or ""))
    return match.group(1) if match else ""


def resolve_mineru_for_popo_task(container, buckets, pdf_id, popo_job_id, explicit_mineru_job_id=""):
    if explicit_mineru_job_id:
        mineru_prefix = f"local/{buckets['mineru']}/mineru/{pdf_id}/{explicit_mineru_job_id}"
        return {
            "job_id": explicit_mineru_job_id,
            "prefix": mineru_prefix,
            "link_strategy": "explicit_cli_mineru_job_id",
            "upstream_manifest_object": "",
            "popo_manifest_path": "",
        }

    popo_manifest_path = f"local/{buckets['minerupopo']}/minerupopo/{pdf_id}/{popo_job_id}/manifest.json"
    popo_manifest = safe_cat_json(container, popo_manifest_path)
    upstream = popo_manifest.get("upstream_mineru") if isinstance(popo_manifest.get("upstream_mineru"), dict) else {}
    lineage = popo_manifest.get("lineage") if isinstance(popo_manifest.get("lineage"), dict) else {}
    stage_run_ids = popo_manifest.get("stage_run_ids") if isinstance(popo_manifest.get("stage_run_ids"), dict) else {}
    upstream_manifest = upstream.get("manifest") if isinstance(upstream.get("manifest"), dict) else {}
    lineage_manifest = lineage.get("upstream_manifest") if isinstance(lineage.get("upstream_manifest"), dict) else {}
    manifest_object = str(upstream_manifest.get("object") or lineage_manifest.get("object") or "")
    inferred_run_id = mineru_run_id_from_manifest_object(pdf_id, manifest_object)
    candidates = []
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
        if safe_ls(container, mineru_prefix).strip():
            return {
                "job_id": candidate["job_id"],
                "prefix": mineru_prefix,
                "link_strategy": candidate["strategy"],
                "upstream_manifest_object": manifest_object,
                "popo_manifest_path": popo_manifest_path.replace("local/", ""),
            }

    mineru_root = f"local/{buckets['mineru']}/mineru/{pdf_id}"
    for run_id in prefix_names(container, mineru_root):
        mineru_prefix = f"{mineru_root}/{run_id}"
        if safe_ls(container, mineru_prefix).strip():
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


def run_mc_copy(container, copies, tmp_root):
    setup = [
        'tmp_cfg="/tmp/mc-codex-$$"',
        'mkdir -p "$tmp_cfg"',
        'MC_CONFIG_DIR="$tmp_cfg" mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null',
        f"rm -rf {shlex.quote(tmp_root)}",
        f"mkdir -p {shlex.quote(tmp_root)}",
    ]
    run(["docker", "exec", container, "sh", "-lc", "; ".join(setup) + '; rm -rf "$tmp_cfg"'])
    for src, dest in copies:
        errors = []
        for attempt in range(1, 4):
            script = "; ".join([
                'tmp_cfg="/tmp/mc-codex-$$"',
                'mkdir -p "$tmp_cfg"',
                'MC_CONFIG_DIR="$tmp_cfg" mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null',
                f"mkdir -p {shlex.quote(str(Path(dest).parent))}",
                f"rm -rf {shlex.quote(dest)}",
                (
                    f"if MC_CONFIG_DIR=\"$tmp_cfg\" mc ls {shlex.quote(src)} >/dev/null 2>&1; then "
                    f"MC_CONFIG_DIR=\"$tmp_cfg\" mc cp --recursive {shlex.quote(src)} {shlex.quote(dest)} >/dev/null; "
                    f"else echo 'missing optional MinIO source: {src}'; fi"
                ),
                'rc=$?',
                'rm -rf "$tmp_cfg"',
                'exit $rc',
            ])
            try:
                run(["docker", "exec", container, "sh", "-lc", script])
                break
            except RuntimeError as exc:
                errors.append(f"attempt {attempt}: {exc}")
                if attempt >= 3:
                    raise RuntimeError(
                        f"mc copy failed from {src} to {dest} after {attempt} attempts\n"
                        + "\n\n".join(errors)
                    ) from exc
                time.sleep(3 * attempt)


def docker_cp_from(container, src, dest):
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(["docker", "cp", f"{container}:{src}", str(dest)])


def docker_rm(container, path):
    subprocess.run(["docker", "exec", container, "rm", "-rf", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def copy_if_exists(src, dest):
    if src.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return True
    return False


def copytree_if_exists(src, dest):
    if src.exists():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        return True
    return False


def find_first(root, pattern):
    matches = sorted(root.rglob(pattern))
    return matches[0] if matches else None


def find_best(root, pattern, preferred_parts=()):
    matches = sorted(root.rglob(pattern))
    if not matches:
        return None

    def score(path):
        parts = set(path.parts)
        return (sum(1 for part in preferred_parts if part in parts), -len(path.parts), str(path))

    return sorted(matches, key=score, reverse=True)[0]


def find_mineru_vlm(download_dir):
    candidates = []
    for content_path in download_dir.rglob("*content_list.json"):
        if content_path.name.endswith("_content_list.json") or content_path.name == "input_content_list.json":
            candidates.append(content_path.parent)
    if not candidates:
        return None
    def score(path):
        parts = set(path.parts)
        return (("official" in parts) + ("input" in parts) + ("vlm" in parts), -len(path.parts))
    return sorted(candidates, key=score, reverse=True)[0]


def find_popo_vlm(download_dir):
    candidates = [path.parent for path in download_dir.rglob("input.md") if "minerupopo" in path.parts]
    return sorted(candidates, key=lambda p: len(p.parts))[0] if candidates else None


def list_relative_files(path):
    if not path.exists():
        return []
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())


def load_status_metadata(input_status_dir, job_id):
    if not input_status_dir.exists():
        return {}
    status_paths = sorted(p for p in input_status_dir.rglob("*.json") if job_id in str(p))
    if not status_paths:
        status_paths = sorted(input_status_dir.rglob("*.done.json"))
    for path in status_paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        pdf_name = (
            data.get("object")
            or data.get("pdf_name")
            or data.get("source_pdf_name")
            or (data.get("wrapper_status") or {}).get("filename")
            or (data.get("wrapper_status") or {}).get("source", {}).get("filename")
        )
        return {
            "pdf_name": pdf_name or "",
            "source_hash": data.get("source_hash") or "",
            "source_pdf_sha256": data.get("source_pdf_sha256") or (data.get("wrapper_status") or {}).get("source", {}).get("sha256") or "",
            "source_pdf_size_bytes": data.get("source_pdf_size_bytes") or (data.get("wrapper_status") or {}).get("source", {}).get("size_bytes"),
            "status_file": str(path.relative_to(input_status_dir)),
        }
    return {}


def materialize(args):
    buckets = DEFAULT_BUCKETS | {
        "input": args.input_bucket,
        "mineru": args.mineru_bucket,
        "minerupopo": args.minerupopo_bucket,
        "raw": args.raw_bucket,
    }
    pdf_id = args.pdf_id
    job_id = args.job_id
    popo_job_id = job_id
    out_dir = args.out_dir.expanduser().resolve()
    download_dir = out_dir / "_minio_download"
    rebuild_input = out_dir / "rebuild_input"
    if out_dir.exists() and args.force:
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    task_hash = hashlib.sha1(f"{pdf_id}|{job_id}".encode("utf-8")).hexdigest()[:16]
    tmp_root = f"/tmp/pdf-clean-md-materialize-{int(time.time())}-{task_hash}"
    mineru = resolve_mineru_for_popo_task(
        args.container,
        buckets,
        pdf_id,
        popo_job_id,
        explicit_mineru_job_id=args.mineru_job_id,
    )
    mineru_job_id = mineru.get("job_id") or popo_job_id
    copies = [
        (f"local/{buckets['minerupopo']}/minerupopo/{pdf_id}/{popo_job_id}/", f"{tmp_root}/minerupopo/"),
        (f"local/{buckets['mineru']}/mineru/{pdf_id}/{mineru_job_id}/", f"{tmp_root}/mineru/"),
        (f"local/{buckets['input']}/_status/{pdf_id}/", f"{tmp_root}/input_status/"),
    ]
    run_mc_copy(args.container, copies, tmp_root)
    docker_cp_from(args.container, tmp_root, download_dir)
    docker_rm(args.container, tmp_root)

    mineru_vlm = find_mineru_vlm(download_dir)
    popo_vlm = find_popo_vlm(download_dir)
    if not mineru_vlm:
        raise RuntimeError("Could not find MinerU content_list assets in downloaded task.")

    if rebuild_input.exists():
        shutil.rmtree(rebuild_input)
    rebuild_input.mkdir(parents=True)

    copied_assets = {}
    for src_name, dest_name in [
        ("input_content_list.json", f"{pdf_id}_content_list.json"),
        ("input_content_list_v2.json", f"{pdf_id}_content_list_v2.json"),
        ("input_model.json", f"{pdf_id}_model.json"),
        ("input_middle.json", f"{pdf_id}_middle.json"),
        ("input_origin.pdf", f"{pdf_id}_origin.pdf"),
        ("input_layout.pdf", f"{pdf_id}_layout.pdf"),
        ("input.md", "full.md"),
    ]:
        copied_assets[dest_name] = copy_if_exists(mineru_vlm / src_name, rebuild_input / dest_name)
    copied_assets["images"] = copytree_if_exists(mineru_vlm / "images", rebuild_input / "images")
    if popo_vlm:
        copy_if_exists(popo_vlm / "input.md", rebuild_input / "popo_input.md")
        copytree_if_exists(popo_vlm / "images", rebuild_input / "popo_images")

    popo_artifacts = {
        "popo_raw.json": find_best(download_dir, "popo_raw.json", preferred_parts=("enhanced", "minerupopo")),
        "popo_document_tree.json": find_best(download_dir, "document_tree.json", preferred_parts=("enhanced", "minerupopo")),
        "popo_document_tree.txt": find_best(download_dir, "document_tree.txt", preferred_parts=("enhanced", "minerupopo")),
        "popo_build_tree.json": find_best(download_dir, "build_tree.json", preferred_parts=("build_tree", "minerupopo")),
        "popo_inference.json": find_best(download_dir, "inference.json", preferred_parts=("minerupopo",)),
        "popo_label_normalization.json": find_best(download_dir, "label_normalization.json", preferred_parts=("label_normalization", "minerupopo")),
    }
    copied_popo_artifacts = {}
    for dest_name, src in popo_artifacts.items():
        copied_popo_artifacts[dest_name] = copy_if_exists(src, rebuild_input / dest_name) if src else False

    input_status_dir = download_dir / "input_status"
    status_files = list_relative_files(input_status_dir)
    status_meta = load_status_metadata(input_status_dir, job_id)
    trace = {
        "pdf_id": pdf_id,
        "job_id": popo_job_id,
        "popo_job_id": popo_job_id,
        "mineru_job_id": mineru_job_id,
        "mineru_link_strategy": mineru.get("link_strategy") or "unknown",
        "upstream_mineru_manifest": mineru.get("upstream_manifest_object") or "",
        "popo_manifest_path": mineru.get("popo_manifest_path") or "",
        "lineage_join_key": "material_id",
        "pdf_name": status_meta.get("pdf_name") or "",
        "source_hash": status_meta.get("source_hash") or "",
        "source_pdf_sha256": status_meta.get("source_pdf_sha256") or "",
        "source_pdf_size_bytes": status_meta.get("source_pdf_size_bytes"),
        "buckets": buckets,
        "source_prefixes": {
            "minerupopo": f"{buckets['minerupopo']}/minerupopo/{pdf_id}/{popo_job_id}/",
            "mineru": f"{buckets['mineru']}/mineru/{pdf_id}/{mineru_job_id}/",
            "input_status": f"{buckets['input']}/_status/{pdf_id}/",
        },
        "materialized_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mineru_vlm_local": str(mineru_vlm),
        "minerupopo_vlm_local": str(popo_vlm) if popo_vlm else None,
        "rebuild_input": str(rebuild_input),
        "copied_assets": copied_assets,
        "copied_popo_artifacts": copied_popo_artifacts,
        "status_files": status_files,
        "status_metadata_file": status_meta.get("status_file") or "",
        "notes": [
            "eduassets-minerupopo is the semantic-rebuild entry point.",
            "eduassets-mineru provides canonical structured MinerU assets used by bootstrap_clean_markdown.py.",
            "eduassets-raw is the target bucket for semantic-rebuild clean master outputs.",
        ],
    }
    (out_dir / "source_trace.json").write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(trace, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Materialize a local MinIO eduassets task into a rebuild input folder.")
    parser.add_argument("--pdf-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--container", default="minio")
    parser.add_argument("--input-bucket", default=DEFAULT_BUCKETS["input"])
    parser.add_argument("--mineru-bucket", default=DEFAULT_BUCKETS["mineru"])
    parser.add_argument("--minerupopo-bucket", default=DEFAULT_BUCKETS["minerupopo"])
    parser.add_argument("--raw-bucket", default=DEFAULT_BUCKETS["raw"])
    parser.add_argument("--mineru-job-id", default="", help="Override upstream MinerU run id when it differs from Popo job id.")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    materialize(args)


if __name__ == "__main__":
    main()
