#!/usr/bin/env python3
"""Luceon staged PDF -> MinerU -> MinerU-Popo -> MinIO scheduler.

This script intentionally bypasses Dify for the first-stage batch loop. The
target contract is staged: freeze MinerU assets per PDF before running or
resuming MinerU-Popo. The current /api/v1/batches wrapper path is a legacy
compatibility mode that runs MinerU+Popo together and can only collect results
after terminal wrapper completion.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import shlex
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


STATUS_PREFIX = "_status/"
BATCH_PREFIX = "_status/_batches/"
RAW_VERSION = "v1"
DEFAULT_LANG = "ch"
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_BATCH_PDFS = 5
USER_AGENT = "Luceon-PDF-MinerU-Popo-Pipeline/0.1"
ERROR_STATUSES = {
    "error",
    "mineru_error",
    "popo_error",
    "mineru_freeze_error",
    "popo_freeze_error",
    "mineru_asset_invalid",
}
DONE_STATUSES = {"done", "popo_done", "popo_done_frozen"}
MINERU_DONE_STATUSES = {"mineru_done", "mineru_done_frozen"}
ACTIVE_STATUSES = {"processing", "submitted", "mineru_submitted", "mineru_running", "popo_submitted", "popo_running"}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    return str(value)


def dumps(value: Any, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(json_safe(value), ensure_ascii=False, indent=2)
    return json.dumps(json_safe(value), ensure_ascii=False, separators=(",", ":"))


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def source_key_hash(bucket: str, obj: str) -> str:
    return sha256_hex(f"{bucket}/{obj}")[:24]


def material_id_from_sha(source_sha256: str) -> str:
    return "pdf-" + source_sha256[:16]


def content_type_for(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def normalize_endpoint(endpoint: str) -> str:
    endpoint = str(endpoint or "").strip().rstrip("/")
    if not endpoint:
        raise ValueError("endpoint is required")
    if not endpoint.startswith(("http://", "https://")):
        endpoint = "http://" + endpoint
    return endpoint


def load_dotenv_values(path: str) -> dict[str, str]:
    """Parse simple KEY=VALUE dotenv lines without executing shell code."""
    values: dict[str, str] = {}
    if not path:
        return values
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"dotenv file not found: {p}")
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def cfg_value(args: argparse.Namespace, dotenv: dict[str, str], attr: str, env_name: str, default: str = "") -> str:
    value = str(getattr(args, attr, "") or "")
    if value:
        return value
    value = os.getenv(env_name, "")
    if value:
        return value
    return dotenv.get(env_name, default)


def redact_presigned_urls(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"source_url", "url"} and isinstance(item, str) and (
                "X-Amz-" in item or "Signature=" in item
            ):
                out[key] = "<redacted-presigned-url>"
            else:
                out[key] = redact_presigned_urls(item)
        return out
    if isinstance(value, list):
        return [redact_presigned_urls(item) for item in value]
    if isinstance(value, str) and ("X-Amz-" in value or "X-Amz-Signature" in value):
        return "<redacted-presigned-url>"
    return value


_NO_PROXY_OPENERS: dict[bool, urllib.request.OpenerDirector] = {}


def no_proxy_opener(url: str):
    uses_https = url.lower().startswith("https://")
    opener = _NO_PROXY_OPENERS.get(uses_https)
    if opener is not None:
        return opener
    context = ssl._create_unverified_context() if uses_https else None
    handlers: list[Any] = [urllib.request.ProxyHandler({})]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    opener = urllib.request.build_opener(*handlers)
    _NO_PROXY_OPENERS[uses_https] = opener
    return opener


def urlopen_no_proxy(req: urllib.request.Request, timeout: int = 180):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    return no_proxy_opener(url).open(req, timeout=timeout)


def urlopen_default(req: urllib.request.Request, timeout: int = 180):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    context = ssl._create_unverified_context() if url.lower().startswith("https://") else None
    return urllib.request.urlopen(req, timeout=timeout, context=context)


def sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    k_date = sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = sign(k_date, region)
    k_service = sign(k_region, service)
    return sign(k_service, "aws4_request")


def canonical_query(params: dict[str, Any] | None) -> str:
    if not params:
        return ""
    pairs = []
    for key in sorted(params):
        value = params[key]
        pairs.append(
            urllib.parse.quote(str(key), safe="-_.~")
            + "="
            + urllib.parse.quote(str(value), safe="-_.~")
        )
    return "&".join(pairs)


@dataclass
class S3Ref:
    bucket: str
    object: str
    sha256: str
    size_bytes: int
    content_type: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "object": self.object,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
        }


class S3Client:
    def __init__(self, endpoint: str, access_key: str, secret_key: str):
        self.endpoint = normalize_endpoint(endpoint)
        self.access_key = access_key
        self.secret_key = secret_key
        if not self.access_key or not self.secret_key:
            raise ValueError("MINIO_ACCESS_KEY and MINIO_SECRET_KEY are required")

    def request(
        self,
        method: str,
        bucket: str,
        obj: str = "",
        query: dict[str, Any] | None = None,
        data: bytes = b"",
        content_type: str = "application/octet-stream",
        timeout: int = 240,
    ):
        parsed = urllib.parse.urlparse(self.endpoint)
        host = parsed.netloc
        path = "/" + urllib.parse.quote(bucket, safe="")
        if obj:
            path += "/" + urllib.parse.quote(obj, safe="/")
        query_string = canonical_query(query)
        url = self.endpoint + path + (("?" + query_string) if query_string else "")
        now = dt.datetime.now(dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = sha256_hex(data)
        canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            [method, path, query_string, canonical_headers, signed_headers, payload_hash]
        )
        region = "us-east-1"
        scope = f"{date_stamp}/{region}/s3/aws4_request"
        string_to_sign = "AWS4-HMAC-SHA256\n%s\n%s\n%s" % (
            amz_date,
            scope,
            sha256_hex(canonical_request),
        )
        signature = hmac.new(
            signing_key(self.secret_key, date_stamp, region, "s3"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "Authorization": (
                "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
                % (self.access_key, scope, signed_headers, signature)
            ),
            "Host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if method in ("PUT", "POST"):
            headers["Content-Type"] = content_type
        req = urllib.request.Request(
            url,
            data=data if method in ("PUT", "POST") else None,
            headers=headers,
            method=method,
        )
        return urlopen_no_proxy(req, timeout=timeout)

    def list_objects(self, bucket: str, max_keys: int = 1000, prefix: str = "") -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        token = ""
        while True:
            query: dict[str, Any] = {"list-type": "2", "max-keys": max_keys}
            if prefix:
                query["prefix"] = prefix
            if token:
                query["continuation-token"] = token
            with self.request("GET", bucket, query=query, timeout=240) as resp:
                raw = resp.read()
            root = ET.fromstring(raw)
            for elem in root.iter():
                if elem.tag.endswith("Contents"):
                    item: dict[str, str] = {}
                    for child in list(elem):
                        tag = child.tag.rsplit("}", 1)[-1]
                        item[tag] = child.text or ""
                    if item.get("Key"):
                        rows.append(item)
            next_token = ""
            for elem in root.iter():
                if elem.tag.endswith("NextContinuationToken"):
                    next_token = elem.text or ""
                    break
            if not next_token:
                break
            token = next_token
        return rows

    def get_bytes(self, bucket: str, obj: str, max_bytes: int | None = None) -> bytes:
        with self.request("GET", bucket, obj, timeout=240) as resp:
            if max_bytes is None:
                return resp.read()
            data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"s3 object exceeds max bytes: {bucket}/{obj}")
        return data

    def get_json(self, bucket: str, obj: str) -> dict[str, Any]:
        return json.loads(self.get_bytes(bucket, obj).decode("utf-8", errors="replace"))

    def put_bytes(self, bucket: str, obj: str, data: bytes, content_type: str) -> dict[str, Any]:
        with self.request("PUT", bucket, obj, data=data, content_type=content_type, timeout=240):
            pass
        return S3Ref(bucket, obj, sha256_hex(data), len(data), content_type).as_dict()

    def put_json(self, bucket: str, obj: str, value: Any) -> dict[str, Any]:
        data = dumps(value, pretty=True).encode("utf-8")
        return self.put_bytes(bucket, obj, data, "application/json")

    def put_text(self, bucket: str, obj: str, value: str, content_type: str = "text/plain; charset=utf-8"):
        return self.put_bytes(bucket, obj, value.encode("utf-8"), content_type)

    def delete(self, bucket: str, obj: str) -> dict[str, Any]:
        if not obj:
            return {"bucket": bucket, "object": obj, "deleted": False, "skipped": True}
        with self.request("DELETE", bucket, obj, timeout=120):
            pass
        return {"bucket": bucket, "object": obj, "deleted": True}

    def presign_get(self, public_endpoint: str, bucket: str, obj: str, expires: int = 86400) -> str:
        endpoint = normalize_endpoint(public_endpoint)
        parsed = urllib.parse.urlparse(endpoint)
        host = parsed.netloc
        path = "/%s/%s" % (urllib.parse.quote(bucket, safe=""), urllib.parse.quote(obj, safe="/"))
        now = dt.datetime.now(dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        region = "us-east-1"
        scope = f"{date_stamp}/{region}/s3/aws4_request"
        params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.access_key}/{scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": int(expires),
            "X-Amz-SignedHeaders": "host",
        }
        query = canonical_query(params)
        canonical_headers = f"host:{host}\n"
        canonical_request = "GET\n%s\n%s\n%s\nhost\nUNSIGNED-PAYLOAD" % (
            path,
            query,
            canonical_headers,
        )
        string_to_sign = "AWS4-HMAC-SHA256\n%s\n%s\n%s" % (
            amz_date,
            scope,
            sha256_hex(canonical_request),
        )
        signature = hmac.new(
            signing_key(self.secret_key, date_stamp, region, "s3"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return endpoint + path + "?" + query + "&X-Amz-Signature=" + signature


class WrapperClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = api_key
        if not self.base_url:
            raise ValueError("GPU_WRAPPER_URL is required")

    def url(self, value: str) -> str:
        value = str(value or "")
        if value.startswith(("http://", "https://")):
            return value
        return self.base_url + "/" + value.lstrip("/")

    def request(self, method: str, path_or_url: str, payload: Any | None = None, timeout: int = 180) -> bytes:
        url = self.url(path_or_url)
        headers = {"User-Agent": USER_AGENT}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        data = None
        if payload is not None:
            data = dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urlopen_default(req, timeout=timeout) as resp:
            return resp.read()

    def request_json(self, method: str, path_or_url: str, payload: Any | None = None, timeout: int = 180) -> dict[str, Any]:
        raw = self.request(method, path_or_url, payload=payload, timeout=timeout)
        return json.loads(raw.decode("utf-8", errors="replace")) if raw.strip() else {}

    def request_bytes(self, method: str, path_or_url: str, timeout: int = 3600) -> bytes:
        return self.request(method, path_or_url, timeout=timeout)

    def health(self, timeout: int = 30) -> dict[str, Any]:
        return self.request_json("GET", "/api/v1/health", timeout=timeout)

    def status_code(self, method: str, path_or_url: str, timeout: int = 30) -> int:
        url = self.url(path_or_url)
        headers = {"User-Agent": USER_AGENT}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urlopen_default(req, timeout=timeout) as resp:
                return int(resp.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)


@dataclass
class Config:
    minio_endpoint: str
    minio_public_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    wrapper_url: str
    wrapper_api_key: str
    input_bucket: str
    mineru_bucket: str
    minerupopo_bucket: str
    archive_bucket: str
    lang: str
    max_file_bytes: int
    presign_expires: int


def cfg_from_args(args: argparse.Namespace) -> Config:
    dotenv = load_dotenv_values(getattr(args, "dotenv", ""))
    return Config(
        minio_endpoint=cfg_value(args, dotenv, "minio_endpoint", "MINIO_ENDPOINT", "http://127.0.0.1:9000"),
        minio_public_endpoint=cfg_value(
            args,
            dotenv,
            "minio_public_endpoint",
            "MINIO_PUBLIC_ENDPOINT",
            "http://152.136.183.144:19000",
        ),
        minio_access_key=cfg_value(args, dotenv, "minio_access_key", "MINIO_ACCESS_KEY"),
        minio_secret_key=cfg_value(args, dotenv, "minio_secret_key", "MINIO_SECRET_KEY"),
        wrapper_url=cfg_value(args, dotenv, "wrapper_url", "GPU_WRAPPER_URL"),
        wrapper_api_key=cfg_value(args, dotenv, "wrapper_api_key", "GPU_WRAPPER_API_KEY"),
        input_bucket=args.input_bucket,
        mineru_bucket=args.mineru_bucket,
        minerupopo_bucket=args.minerupopo_bucket,
        archive_bucket=args.archive_bucket,
        lang=args.lang,
        max_file_bytes=args.max_file_bytes,
        presign_expires=args.presign_expires,
    )


def terminal_marker(key: str) -> bool:
    terminal_suffixes = (
        ".done.json",
        ".mineru_done.json",
        ".mineru_done_frozen.json",
        ".popo_done.json",
        ".popo_done_frozen.json",
        ".mineru_error.json",
        ".popo_error.json",
        ".mineru_freeze_error.json",
        ".popo_freeze_error.json",
        ".mineru_asset_invalid.json",
        ".error.json",
    )
    return key.endswith(terminal_suffixes)


def active_marker(key: str) -> bool:
    return (
        (key.startswith(BATCH_PREFIX) and (key.endswith(".submitted.json") or key.endswith(".collecting.json")))
        or key.endswith(".processing.json")
        or key.endswith(".mineru_submitted.json")
        or key.endswith(".mineru_running.json")
        or key.endswith(".popo_submitted.json")
        or key.endswith(".popo_running.json")
    )


def status_marker_object(material_id: str, run_id: str, status: str) -> str:
    return f"{STATUS_PREFIX}{material_id}/{run_id}.{status}.json"


def source_status_marker_object(source_hash: str, run_id: str, status: str) -> str:
    return f"{STATUS_PREFIX}by-source/{source_hash}/{run_id}.{status}.json"


def write_status_marker(
    s3: S3Client,
    cfg: Config,
    doc: dict[str, Any],
    run_id: str,
    status: str,
    value: dict[str, Any],
) -> dict[str, Any]:
    bucket = str(doc.get("bucket") or cfg.input_bucket)
    obj = str(doc.get("object") or "")
    source_hash = str(doc.get("source_hash") or source_key_hash(bucket, obj))
    material_id = str(doc.get("material_id") or "")
    marker = dict(value or {})
    marker.update(
        {
            "schema": "luceon-input-status-marker/v1",
            "status": status,
            "bucket": bucket,
            "object": obj,
            "source_hash": source_hash,
            "material_id": material_id,
            "run_id": run_id,
            "updated_at": now_iso(),
        }
    )
    return {
        "marker": s3.put_json(cfg.input_bucket, status_marker_object(material_id, run_id, status), marker),
        "source_marker": s3.put_json(cfg.input_bucket, source_status_marker_object(source_hash, run_id, status), marker),
    }


def cleanup_processing(s3: S3Client, cfg: Config, doc: dict[str, Any]) -> dict[str, Any]:
    bucket = str(doc.get("bucket") or cfg.input_bucket)
    obj = str(doc.get("object") or "")
    source_hash = str(doc.get("source_hash") or source_key_hash(bucket, obj))
    material_id = str(doc.get("material_id") or "")
    lock_id = str(doc.get("lock_id") or doc.get("processing_marker_lock_id") or "")
    objects = []
    if lock_id:
        objects.append(status_marker_object(material_id, lock_id, "processing"))
        objects.append(source_status_marker_object(source_hash, lock_id, "processing"))
    for field in ("processing_marker", "source_processing_marker"):
        ref = doc.get(field)
        if isinstance(ref, dict) and ref.get("object"):
            objects.append(str(ref["object"]))
    deleted = []
    errors = []
    for marker in sorted(set(x for x in objects if x)):
        try:
            deleted.append(s3.delete(cfg.input_bucket, marker))
        except Exception as exc:
            errors.append({"object": marker, "error": str(exc)})
    return {"attempted": len(set(objects)), "deleted": deleted, "errors": errors}


def cleanup_stage_markers(
    s3: S3Client,
    cfg: Config,
    doc: dict[str, Any],
    run_id: str,
    statuses: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    bucket = str(doc.get("bucket") or cfg.input_bucket)
    obj = str(doc.get("object") or "")
    source_hash = str(doc.get("source_hash") or source_key_hash(bucket, obj))
    material_id = str(doc.get("material_id") or "")
    deleted = []
    errors = []
    for status in statuses:
        for marker in (
            status_marker_object(material_id, run_id, status),
            source_status_marker_object(source_hash, run_id, status),
        ):
            try:
                deleted.append(s3.delete(cfg.input_bucket, marker))
            except Exception as exc:
                errors.append({"object": marker, "error": str(exc)})
    return {"attempted": len(statuses) * 2, "deleted": deleted, "errors": errors}


def list_state(s3: S3Client, cfg: Config) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    rows = s3.list_objects(cfg.input_bucket)
    markers = [r for r in rows if str(r.get("Key", "")).startswith(STATUS_PREFIX)]
    return rows, markers


def active_markers(markers: list[dict[str, str]]) -> list[str]:
    return [str(r.get("Key", "")) for r in markers if active_marker(str(r.get("Key", "")))]


def has_terminal_or_lock(markers: list[dict[str, str]], material_id: str, source_hash: str) -> bool:
    prefixes = (STATUS_PREFIX + material_id + "/", STATUS_PREFIX + "by-source/" + source_hash + "/")
    for row in markers:
        key = str(row.get("Key", ""))
        if key.startswith(prefixes) and (terminal_marker(key) or key.endswith(".processing.json")):
            return True
    return False


def has_source_terminal_or_lock(markers: list[dict[str, str]], source_hash: str) -> bool:
    prefix = STATUS_PREFIX + "by-source/" + source_hash + "/"
    for row in markers:
        key = str(row.get("Key", ""))
        if key.startswith(prefix) and (terminal_marker(key) or key.endswith(".processing.json")):
            return True
    return False


def marker_status_from_key(key: str) -> str:
    for status in (
        "mineru_submitted",
        "mineru_running",
        "mineru_done_frozen",
        "mineru_done",
        "popo_submitted",
        "popo_running",
        "popo_done_frozen",
        "popo_done",
        "mineru_error",
        "popo_error",
        "mineru_freeze_error",
        "popo_freeze_error",
        "mineru_asset_invalid",
    ):
        if key.endswith(f".{status}.json"):
            return status
    if key.endswith(".done.json"):
        return "done"
    if key.endswith(".error.json"):
        return "error"
    if key.endswith(".processing.json"):
        return "processing"
    if key.endswith(".submitted.json"):
        return "submitted"
    if key.endswith(".collecting.json"):
        return "collecting"
    if key.endswith(".collected.json"):
        return "collected"
    return "other"


def marker_row_sort_key(row: dict[str, str]) -> tuple[str, str]:
    return str(row.get("LastModified") or ""), str(row.get("Key") or "")


def latest_source_status(markers: list[dict[str, str]]) -> str | None:
    terminal_statuses = {
        "done",
        "popo_done",
        "popo_done_frozen",
        "mineru_done",
        "mineru_done_frozen",
        "mineru_error",
        "popo_error",
        "mineru_freeze_error",
        "popo_freeze_error",
        "mineru_asset_invalid",
        "error",
    }
    terminal = [row for row in markers if marker_status_from_key(str(row.get("Key") or "")) in terminal_statuses]
    if terminal:
        return marker_status_from_key(str(sorted(terminal, key=marker_row_sort_key)[-1].get("Key") or ""))
    active = [
        row
        for row in markers
        if marker_status_from_key(str(row.get("Key") or "")) in ACTIVE_STATUSES
    ]
    if active:
        return marker_status_from_key(str(sorted(active, key=marker_row_sort_key)[-1].get("Key") or ""))
    return None


def latest_status_marker_payloads_by_source(
    s3: S3Client,
    cfg: Config,
    markers: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """Read small by-source status marker payloads to recover material identity.

    Older runs may have a by-source error marker while later by-material done
    markers and official manifests exist. Reading the marker payload lets
    lineage/audit join those two indexes by material_id without streaming the
    source PDF.
    """
    candidates: dict[str, list[tuple[tuple[str, str], dict[str, Any]]]] = {}
    for row in markers:
        key = str(row.get("Key") or "")
        if not key.startswith(STATUS_PREFIX + "by-source/"):
            continue
        parts = key.split("/")
        if len(parts) < 4:
            continue
        source_hash = parts[2]
        try:
            payload = s3.get_json(cfg.input_bucket, key)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("material_id") or payload.get("object") or payload.get("source_pdf_sha256"):
            candidates.setdefault(source_hash, []).append((marker_row_sort_key(row), payload))
    return {
        source_hash: sorted(items, key=lambda item: item[0])[-1][1]
        for source_hash, items in candidates.items()
        if items
    }


def marker_reconciliation(markers: list[dict[str, str]], latest_status: str | None) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for row in markers:
        status = marker_status_from_key(str(row.get("Key") or ""))
        status_counts[status] = status_counts.get(status, 0) + 1
    raw_error_count = sum(status_counts.get(status, 0) for status in ERROR_STATUSES)
    latest = latest_status or ""
    return {
        "latest_status": latest,
        "status_counts": status_counts,
        "raw_error_marker_count": raw_error_count,
        "active_error": latest in ERROR_STATUSES,
        "superseded_error_marker_count": 0 if latest in ERROR_STATUSES else raw_error_count,
    }


def scheduler_state_from_lineage(state: str, next_action: str) -> tuple[str, str]:
    if state == "gpu_done":
        return "gpu_done", "handoff_to_raw_rebuild"
    if next_action == "eligible_for_submit":
        return "eligible_for_submit", "run_mineru_then_popo_staged"
    if next_action == "recover_or_rerun_minerupopo":
        return "mineru_only_resume_popo", "resume_popo_from_frozen_mineru"
    if next_action == "review_error_marker_before_retry":
        return "error_review", "review_error_marker_before_retry"
    if next_action == "use_canonical_input_output_do_not_submit_duplicate":
        return "duplicate_skip", "use_canonical_asset_do_not_submit_duplicate"
    if next_action == "choose_canonical_input_before_submit":
        return "duplicate_review", "choose_canonical_input_before_submit"
    if next_action == "collect_or_clear_stale_processing_marker":
        return "active_or_stale", "collect_or_clear_stale_processing_marker"
    return state, next_action


def add_scheduler_fields(item: dict[str, Any]) -> dict[str, Any]:
    scheduler_state, scheduler_next_action = scheduler_state_from_lineage(
        str(item.get("state") or ""),
        str(item.get("next_action") or ""),
    )
    item["scheduler_state"] = scheduler_state
    item["scheduler_next_action"] = scheduler_next_action
    return item


def apply_run_mode(args: argparse.Namespace, command: str) -> None:
    mode = getattr(args, "mode", "batch")
    if mode == "single":
        args.limit = 1
    elif mode == "batch":
        if getattr(args, "limit", MAX_BATCH_PDFS) > MAX_BATCH_PDFS:
            raise ValueError(f"{command} batch mode supports limit <= {MAX_BATCH_PDFS}; use full mode with chunking after staged wrapper support")
    elif mode == "full":
        if command in {"submit", "daemon"}:
            raise ValueError("full mode requires staged batch chunking and staged wrapper APIs; legacy /api/v1/batches submit is limited to batch mode")
        args.limit = min(getattr(args, "limit", MAX_BATCH_PDFS), MAX_BATCH_PDFS)
    else:
        raise ValueError(f"unknown mode: {mode}")


def marker_rows_by_source(markers: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    by_source: dict[str, list[dict[str, str]]] = {}
    for row in markers:
        key = str(row.get("Key") or "")
        if key.startswith(STATUS_PREFIX + "by-source/"):
            parts = key.split("/")
            if len(parts) >= 4:
                by_source.setdefault(parts[2], []).append(row)
    return by_source


def marker_rows_by_material(markers: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    by_material: dict[str, list[dict[str, str]]] = {}
    for row in markers:
        key = str(row.get("Key") or "")
        if not key.startswith(STATUS_PREFIX) or key.startswith(STATUS_PREFIX + "by-source/"):
            continue
        parts = key.split("/")
        if len(parts) >= 3:
            by_material.setdefault(parts[1], []).append(row)
    return by_material


def marker_status_counts(markers: list[dict[str, str]]) -> dict[str, int]:
    counts = {"done": 0, "error": 0, "processing": 0, "submitted": 0, "collecting": 0, "collected": 0}
    for row in markers:
        status = marker_status_from_key(str(row.get("Key") or ""))
        if status in counts:
            counts[status] += 1
    return counts


def audit_input_bucket(s3: S3Client, cfg: Config, sample_limit: int) -> dict[str, Any]:
    rows = s3.list_objects(cfg.input_bucket)
    marker_rows = [row for row in rows if str(row.get("Key") or "").startswith(STATUS_PREFIX)]
    pdf_rows = [
        row
        for row in rows
        if str(row.get("Key") or "").lower().endswith(".pdf")
        and not str(row.get("Key") or "").startswith(STATUS_PREFIX)
    ]
    by_source: dict[str, list[dict[str, str]]] = {}
    by_material = marker_rows_by_material(marker_rows)
    active_batch_markers = []
    active_batch_docs: dict[str, list[dict[str, Any]]] = {}
    batch_marker_errors = []
    for row in marker_rows:
        key = str(row.get("Key") or "")
        if key.startswith(STATUS_PREFIX + "by-source/"):
            parts = key.split("/")
            if len(parts) >= 4:
                by_source.setdefault(parts[2], []).append(row)
        if key.startswith(BATCH_PREFIX) and marker_status_from_key(key) in ("submitted", "collecting"):
            active_batch_markers.append(row)
            try:
                payload = s3.get_json(cfg.input_bucket, key)
            except Exception as exc:
                batch_marker_errors.append({"object": key, "error": str(exc)})
                continue
            docs = payload.get("documents") if isinstance(payload, dict) else []
            if not isinstance(docs, list):
                continue
            for doc in docs:
                if isinstance(doc, dict) and doc.get("object"):
                    active_batch_docs.setdefault(str(doc["object"]), []).append(
                        {
                            "batch_marker": key,
                            "batch_id": payload.get("batch_id"),
                            "created_at": payload.get("created_at"),
                            "doc_id": doc.get("doc_id"),
                            "material_id": doc.get("material_id"),
                        }
                    )
    source_marker_payloads = latest_status_marker_payloads_by_source(s3, cfg, marker_rows)

    counts = {
        "done": 0,
        "mineru_done_frozen": 0,
        "error": 0,
        "processing_marker": 0,
        "active_submitted_batch": 0,
        "unprocessed": 0,
    }
    samples: dict[str, list[str]] = {key: [] for key in counts}
    total_bytes = 0
    for row in sorted(pdf_rows, key=lambda item: str(item.get("Key") or "")):
        obj = str(row.get("Key") or "")
        total_bytes += int(row.get("Size") or 0)
        source_hash = source_key_hash(cfg.input_bucket, obj)
        source_markers = by_source.get(source_hash, [])
        marker_payload = source_marker_payloads.get(source_hash, {})
        material_id = str(marker_payload.get("material_id") or "")
        material_markers = by_material.get(material_id, []) if material_id else []
        status = latest_source_status(source_markers + material_markers)
        if status in ("done", "popo_done", "popo_done_frozen"):
            state = "done"
        elif status in ("mineru_done", "mineru_done_frozen"):
            state = "mineru_done_frozen"
        elif status in ("error", "mineru_error", "popo_error", "mineru_freeze_error", "popo_freeze_error", "mineru_asset_invalid"):
            state = "error"
        elif status == "processing":
            state = "processing_marker"
        elif obj in active_batch_docs:
            state = "active_submitted_batch"
        else:
            state = "unprocessed"
        counts[state] += 1
        if len(samples[state]) < sample_limit:
            samples[state].append(obj)

    return {
        "command": "audit",
        "applied": False,
        "bucket": cfg.input_bucket,
        "pdf_total": len(pdf_rows),
        "pdf_total_bytes": total_bytes,
        "processed_total_terminal": counts["done"] + counts["mineru_done_frozen"] + counts["error"],
        "processed_done": counts["done"],
        "processed_mineru_done_frozen": counts["mineru_done_frozen"],
        "processed_error": counts["error"],
        "in_progress_or_pending_collect": counts["processing_marker"] + counts["active_submitted_batch"],
        "processing_marker": counts["processing_marker"],
        "active_submitted_batch": counts["active_submitted_batch"],
        "unprocessed_no_status": counts["unprocessed"],
        "status_marker_total": len(marker_rows),
        "active_batch_marker_count": len(active_batch_markers),
        "active_batch_markers": [str(row.get("Key") or "") for row in active_batch_markers[:20]],
        "active_batch_doc_count_raw": sum(len(value) for value in active_batch_docs.values()),
        "batch_marker_read_errors": batch_marker_errors[:20],
        "state_counts": counts,
        "samples": samples,
    }


def discover_stage_jobs(rows: list[dict[str, str]], stage_prefix: str) -> dict[tuple[str, str], dict[str, Any]]:
    jobs: dict[tuple[str, str], dict[str, Any]] = {}
    prefix = stage_prefix.rstrip("/") + "/"
    for row in rows:
        key = str(row.get("Key") or "")
        if not key.startswith(prefix):
            continue
        rel = key[len(prefix) :]
        parts = rel.split("/")
        if len(parts) < 3:
            continue
        material_id, run_id = parts[0], parts[1]
        if not material_id.startswith("pdf-") or not run_id.startswith(
            ("job-", "scan-", "batch-", "mineru-", "popo-", "raw-", "clean-")
        ):
            continue
        job = jobs.setdefault(
            (material_id, run_id),
            {
                "material_id": material_id,
                "run_id": run_id,
                "prefix": f"{prefix}{material_id}/{run_id}/",
                "object_count": 0,
                "files": set(),
                "image_count": 0,
                "official_object_count": 0,
            },
        )
        job["object_count"] += 1
        rest = key[len(job["prefix"]) :]
        top = rest.split("/", 1)[0] if rest else ""
        if top:
            job["files"].add(top)
        if "/images/" in key:
            job["image_count"] += 1
        if "/official/" in key:
            job["official_object_count"] += 1
    return jobs


def stage_complete(stage: str, job: dict[str, Any]) -> bool:
    files = set(job.get("files") or [])
    if stage == "mineru":
        return "manifest.json" in files and ("content_list_v2.json" in files or "content_list.json" in files)
    if stage == "minerupopo":
        return "manifest.json" in files and ("document_tree.json" in files or "popo_raw.json" in files)
    if stage in {"raw", "clean"}:
        return "manifest.json" in files and "clean.md" in files
    return False


def mineru_asset_usable(job: dict[str, Any]) -> bool:
    files = set(job.get("files") or [])
    return (
        stage_complete("mineru", job)
        or "content_list_v2.json" in files
        or "content_list.json" in files
        or int(job.get("official_object_count") or 0) > 0
    )


def complete_stage_pairs(jobs: dict[tuple[str, str], dict[str, Any]], stage: str) -> set[tuple[str, str]]:
    return {(material_id, run_id) for (material_id, run_id), job in jobs.items() if stage_complete(stage, job)}


def usable_mineru_pairs(jobs: dict[tuple[str, str], dict[str, Any]]) -> set[tuple[str, str]]:
    return {(material_id, run_id) for (material_id, run_id), job in jobs.items() if mineru_asset_usable(job)}


def material_ids_from_pairs(pairs: set[tuple[str, str]]) -> set[str]:
    return {material_id for material_id, _ in pairs}


def sample_ids(values: set[str], sample_limit: int) -> list[str]:
    return sorted(values)[: max(0, sample_limit)]


def sample_pairs(values: set[tuple[str, str]], sample_limit: int) -> list[dict[str, str]]:
    return [
        {"material_id": material_id, "run_id": run_id}
        for material_id, run_id in sorted(values)[: max(0, sample_limit)]
    ]


def source_object_from_popo_manifest(s3: S3Client, cfg: Config, material_id: str, run_id: str) -> dict[str, Any]:
    obj = f"minerupopo/{material_id}/{run_id}/manifest.json"
    try:
        manifest = s3.get_json(cfg.minerupopo_bucket, obj)
    except Exception as exc:
        return {"manifest_object": obj, "manifest_error": str(exc)}
    source = manifest.get("source_pdf") if isinstance(manifest.get("source_pdf"), dict) else {}
    return {
        "manifest_object": obj,
        "input_object": str(source.get("input_object") or ""),
        "source_sha256": str(source.get("sha256") or ""),
        "source_size_bytes": source.get("size_bytes"),
    }


def source_object_from_mineru_manifest(s3: S3Client, cfg: Config, material_id: str, run_id: str) -> dict[str, Any]:
    obj = f"mineru/{material_id}/{run_id}/manifest.json"
    try:
        manifest = s3.get_json(cfg.mineru_bucket, obj)
    except Exception as exc:
        return {"manifest_object": obj, "manifest_error": str(exc)}
    source = manifest.get("source_pdf") if isinstance(manifest.get("source_pdf"), dict) else {}
    return {
        "manifest_object": obj,
        "input_object": str(source.get("input_object") or ""),
        "source_sha256": str(source.get("sha256") or ""),
        "source_size_bytes": source.get("size_bytes"),
    }


def latest_mineru_manifest_for_material(s3: S3Client, cfg: Config, material_id: str) -> tuple[str, dict[str, Any]]:
    prefix = f"mineru/{material_id}/"
    rows = [
        row
        for row in s3.list_objects(cfg.mineru_bucket, prefix=prefix)
        if str(row.get("Key") or "").endswith("/manifest.json")
    ]
    if not rows:
        raise ValueError(f"no frozen MinerU manifest found for {material_id}")
    rows.sort(key=lambda row: (str(row.get("LastModified") or ""), str(row.get("Key") or "")), reverse=True)
    obj = str(rows[0].get("Key") or "")
    return obj, s3.get_json(cfg.mineru_bucket, obj)


def first_stage_lineage_audit(s3: S3Client, cfg: Config, sample_limit: int, with_sha: bool) -> dict[str, Any]:
    input_rows, markers = list_state(s3, cfg)
    mineru_rows = s3.list_objects(cfg.mineru_bucket, prefix="mineru/")
    popo_rows = s3.list_objects(cfg.minerupopo_bucket, prefix="minerupopo/")
    pdf_rows = [
        row
        for row in input_rows
        if str(row.get("Key") or "").lower().endswith(".pdf")
        and not str(row.get("Key") or "").startswith(STATUS_PREFIX)
    ]
    by_source = marker_rows_by_source(markers)
    by_material = marker_rows_by_material(markers)
    source_marker_payloads = latest_status_marker_payloads_by_source(s3, cfg, markers)
    mineru_jobs = discover_stage_jobs(mineru_rows, "mineru")
    popo_jobs = discover_stage_jobs(popo_rows, "minerupopo")

    material_to_input: dict[str, str] = {}
    popo_source_by_material: dict[str, dict[str, Any]] = {}
    mineru_source_by_material: dict[str, dict[str, Any]] = {}
    for (material_id, run_id), job in mineru_jobs.items():
        source = source_object_from_mineru_manifest(s3, cfg, material_id, run_id)
        job["source"] = source
        if source.get("input_object"):
            material_to_input.setdefault(material_id, str(source["input_object"]))
        mineru_source_by_material.setdefault(material_id, source)
    for (material_id, run_id), job in popo_jobs.items():
        source = source_object_from_popo_manifest(s3, cfg, material_id, run_id)
        job["source"] = source
        if source.get("input_object"):
            material_to_input[material_id] = str(source["input_object"])
        popo_source_by_material.setdefault(material_id, source)

    rows = []
    sha_to_inputs: dict[str, list[str]] = {}
    for row in sorted(pdf_rows, key=lambda item: str(item.get("Key") or "")):
        obj = str(row.get("Key") or "")
        source_hash = source_key_hash(cfg.input_bucket, obj)
        source_status = latest_source_status(by_source.get(source_hash, []))
        source_marker_payload = source_marker_payloads.get(source_hash, {})
        source_sha = ""
        material_id = ""
        if with_sha:
            data = s3.get_bytes(cfg.input_bucket, obj, max_bytes=cfg.max_file_bytes)
            source_sha = sha256_hex(data)
            material_id = material_id_from_sha(source_sha)
            sha_to_inputs.setdefault(source_sha, []).append(obj)
        else:
            for known_material_id, input_object in material_to_input.items():
                if input_object == obj:
                    material_id = known_material_id
                    source_sha = str(
                        popo_source_by_material.get(known_material_id, {}).get("source_sha256")
                        or mineru_source_by_material.get(known_material_id, {}).get("source_sha256")
                        or ""
                    )
                    break
        if not material_id:
            material_id = str(source_marker_payload.get("material_id") or "")
        if not source_sha:
            source_sha = str(source_marker_payload.get("source_pdf_sha256") or "")
        source_markers = by_source.get(source_hash, [])
        material_markers = by_material.get(material_id, []) if material_id else []
        material_status = latest_source_status(material_markers) if material_id else None
        combined_status = latest_source_status(source_markers + material_markers)
        linked_mineru = [job for (mid, _), job in mineru_jobs.items() if mid == material_id]
        linked_popo = [job for (mid, _), job in popo_jobs.items() if mid == material_id]
        mineru_complete = [job for job in linked_mineru if stage_complete("mineru", job)]
        popo_complete = [job for job in linked_popo if stage_complete("minerupopo", job)]
        if popo_complete or source_status in ("popo_done", "popo_done_frozen", "done") or material_status in (
            "popo_done",
            "popo_done_frozen",
            "done",
        ):
            state = "gpu_done"
            next_action = "handoff_to_raw_rebuild"
        elif mineru_complete or source_status in ("mineru_done", "mineru_done_frozen") or material_status in (
            "mineru_done",
            "mineru_done_frozen",
        ):
            state = "mineru_only"
            next_action = "recover_or_rerun_minerupopo"
        elif source_status in ("error", "mineru_error", "popo_error", "mineru_freeze_error", "popo_freeze_error", "mineru_asset_invalid") or material_status in (
            "error",
            "mineru_error",
            "popo_error",
            "mineru_freeze_error",
            "popo_freeze_error",
            "mineru_asset_invalid",
        ):
            state = "error_marked"
            next_action = "review_error_marker_before_retry"
        elif source_status in ACTIVE_STATUSES or material_status in ACTIVE_STATUSES:
            state = "processing_marker"
            next_action = "collect_or_clear_stale_processing_marker"
        else:
            state = "unprocessed"
            next_action = "eligible_for_submit"
        rows.append(
            {
                "input_object": obj,
                "input_size_bytes": int(row.get("Size") or 0),
                "source_hash": source_hash,
                "source_sha256": source_sha,
                "material_id": material_id,
                "canonical_input_object": material_to_input.get(material_id, ""),
                "source_status": source_status or "",
                "material_status": material_status or "",
                "marker_reconciliation": marker_reconciliation(source_markers + material_markers, combined_status),
                "mineru_jobs": sorted(str(job.get("run_id") or "") for job in linked_mineru),
                "mineru_complete_count": len(mineru_complete),
                "minerupopo_jobs": sorted(str(job.get("run_id") or "") for job in linked_popo),
                "minerupopo_complete_count": len(popo_complete),
                "state": state,
                "next_action": next_action,
                "duplicate_input_objects": [],
            }
        )

    if with_sha:
        duplicate_by_sha = {sha: objs for sha, objs in sha_to_inputs.items() if len(objs) > 1}
        for item in rows:
            sha = str(item.get("source_sha256") or "")
            duplicates = [obj for obj in duplicate_by_sha.get(sha, []) if obj != item["input_object"]]
            item["duplicate_input_objects"] = duplicates
            canonical_input = str(item.get("canonical_input_object") or "")
            if duplicates and canonical_input and canonical_input != item["input_object"]:
                item["state"] = "duplicate_noncanonical"
                item["next_action"] = "use_canonical_input_output_do_not_submit_duplicate"
            elif duplicates and item["state"] == "unprocessed":
                item["state"] = "duplicate_unprocessed"
                item["next_action"] = "choose_canonical_input_before_submit"
    else:
        duplicate_by_sha = {}
    for item in rows:
        add_scheduler_fields(item)

    active = active_markers(markers)
    counts: dict[str, int] = {}
    scheduler_counts: dict[str, int] = {}
    for item in rows:
        counts[str(item["state"])] = counts.get(str(item["state"]), 0) + 1
        scheduler_counts[str(item["scheduler_state"])] = scheduler_counts.get(str(item["scheduler_state"]), 0) + 1
    scheduler_queues = {
        "gpu_done": [item for item in rows if item["scheduler_state"] == "gpu_done"][:sample_limit],
        "eligible_for_submit": [item for item in rows if item["scheduler_state"] == "eligible_for_submit"][:sample_limit],
        "error_review": [item for item in rows if item["scheduler_state"] == "error_review"][:sample_limit],
        "duplicate_skip": [item for item in rows if item["scheduler_state"] == "duplicate_skip"][:sample_limit],
        "duplicate_review": [item for item in rows if item["scheduler_state"] == "duplicate_review"][:sample_limit],
        "mineru_only_resume_popo": [
            item for item in rows if item["scheduler_state"] == "mineru_only_resume_popo"
        ][:sample_limit],
        "active_or_stale": [item for item in rows if item["scheduler_state"] == "active_or_stale"][:sample_limit],
    }
    queues = {
        "eligible_for_submit": [item for item in rows if item["next_action"] == "eligible_for_submit"][:sample_limit],
        "review_error_marker_before_retry": [
            item for item in rows if item["next_action"] == "review_error_marker_before_retry"
        ][:sample_limit],
        "choose_canonical_input_before_submit": [
            item for item in rows if item["next_action"] == "choose_canonical_input_before_submit"
        ][:sample_limit],
        "use_canonical_input_output_do_not_submit_duplicate": [
            item for item in rows if item["next_action"] == "use_canonical_input_output_do_not_submit_duplicate"
        ][:sample_limit],
        "recover_or_rerun_minerupopo": [item for item in rows if item["next_action"] == "recover_or_rerun_minerupopo"][
            :sample_limit
        ],
    }
    complete_mineru_pairs = complete_stage_pairs(mineru_jobs, "mineru")
    usable_mineru_job_pairs = usable_mineru_pairs(mineru_jobs)
    complete_popo_pairs = complete_stage_pairs(popo_jobs, "minerupopo")
    formal_mineru_material_ids = material_ids_from_pairs(complete_mineru_pairs)
    usable_mineru_material_ids = material_ids_from_pairs(usable_mineru_job_pairs)
    popo_material_ids = material_ids_from_pairs(complete_popo_pairs)
    input_material_ids = {str(item.get("material_id") or "") for item in rows if item.get("material_id")}
    pdf_without_mineru_items = [
        item
        for item in rows
        if item.get("material_id")
        and str(item.get("material_id")) not in usable_mineru_material_ids
        and item.get("scheduler_state") != "duplicate_skip"
    ]
    no_material_id_count = sum(1 for item in rows if not item.get("material_id"))
    strict_run_id_mineru_without_popo = complete_mineru_pairs - complete_popo_pairs
    return {
        "command": "lineage-audit",
        "applied": False,
        "scope": "first_stage_only",
        "with_sha": with_sha,
        "input_pdf_count": len(rows),
        "mineru_job_count": len(mineru_jobs),
        "minerupopo_job_count": len(popo_jobs),
        "state_counts": counts,
        "scheduler_state_counts": scheduler_counts,
        "duplicate_sha_group_count": len(duplicate_by_sha),
        "active_marker_count": len(active),
        "active_marker_samples": active[:20],
        "marker_counts": {
            "by_source": sum(len(value) for value in by_source.values()),
            "by_material": sum(len(value) for value in by_material.values()),
            "all_status": len(markers),
        },
        "queues": queues,
        "scheduler_queues": scheduler_queues,
        "marker_reconciliation_summary": {
            "raw_error_marker_count": sum(
                int(item.get("marker_reconciliation", {}).get("raw_error_marker_count") or 0) for item in rows
            ),
            "superseded_error_marker_count": sum(
                int(item.get("marker_reconciliation", {}).get("superseded_error_marker_count") or 0) for item in rows
            ),
            "active_error_item_count": sum(
                1 for item in rows if item.get("marker_reconciliation", {}).get("active_error")
            ),
        },
        "stage_gap_summary": {
            "schema": "luceon-stage-gap-summary/v1",
            "join_key": "material_id",
            "reason": (
                "run_id is a per-stage execution id; staged/recovery Popo often has a different run_id from "
                "the upstream MinerU run. Asset gaps must be counted by material_id."
            ),
            "counts": {
                "input_pdf_count": len(rows),
                "input_material_id_count": len(input_material_ids),
                "input_without_material_id_count": no_material_id_count,
                "pdf_without_usable_mineru": None if no_material_id_count else len(pdf_without_mineru_items),
                "pdf_without_usable_mineru_known_material_id": len(pdf_without_mineru_items),
                "usable_mineru_material_count": len(usable_mineru_material_ids),
                "formal_mineru_manifest_material_count": len(formal_mineru_material_ids),
                "complete_popo_material_count": len(popo_material_ids),
                "usable_mineru_material_without_complete_popo": len(usable_mineru_material_ids - popo_material_ids),
                "formal_mineru_manifest_material_without_complete_popo": len(
                    formal_mineru_material_ids - popo_material_ids
                ),
            },
            "samples": {
                "pdf_without_usable_mineru": [
                    compact_lineage_item(item) for item in pdf_without_mineru_items[:sample_limit]
                ],
                "usable_mineru_material_without_complete_popo": sample_ids(
                    usable_mineru_material_ids - popo_material_ids, sample_limit
                ),
                "formal_mineru_manifest_material_without_complete_popo": sample_ids(
                    formal_mineru_material_ids - popo_material_ids, sample_limit
                ),
            },
            "run_id_pair_diagnostic": {
                "strict_mineru_run_without_same_run_popo": len(strict_run_id_mineru_without_popo),
                "sample": sample_pairs(strict_run_id_mineru_without_popo, sample_limit),
                "interpretation": (
                    "diagnostic_only_not_asset_gap; staged Popo run_ids are expected to differ from upstream "
                    "MinerU run_ids"
                ),
            },
            "policy_notes": {
                "duplicate_skip": "not counted as pdf_without_usable_mineru when the canonical material asset exists",
                "usable_mineru": "legacy monolithic assets may have content_list/official objects without a MinerU manifest",
                "formal_mineru_manifest": "new staged contract target: mineru/{material_id}/{run_id}/manifest.json plus content list",
                "with_sha": "pdf_without_usable_mineru is exact only when with_sha is true or every input is linked by manifests",
            },
        },
        "items_sample": rows[:sample_limit],
        "items": rows,
    }


def compact_lineage_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "input_object": item.get("input_object"),
        "input_size_bytes": item.get("input_size_bytes"),
        "material_id": item.get("material_id"),
        "source_hash": item.get("source_hash"),
        "source_sha256": item.get("source_sha256"),
        "state": item.get("state"),
        "next_action": item.get("next_action"),
        "scheduler_state": item.get("scheduler_state"),
        "scheduler_next_action": item.get("scheduler_next_action"),
        "source_status": item.get("source_status"),
        "material_status": item.get("material_status"),
        "mineru_complete_count": item.get("mineru_complete_count"),
        "minerupopo_complete_count": item.get("minerupopo_complete_count"),
        "mineru_jobs": item.get("mineru_jobs") or [],
        "minerupopo_jobs": item.get("minerupopo_jobs") or [],
        "duplicate_input_objects": item.get("duplicate_input_objects") or [],
        "canonical_input_object": item.get("canonical_input_object"),
    }


def input_status_lineage_audit(s3: S3Client, cfg: Config, sample_limit: int) -> dict[str, Any]:
    input_rows, markers = list_state(s3, cfg)
    pdf_rows = [
        row
        for row in input_rows
        if str(row.get("Key") or "").lower().endswith(".pdf")
        and not str(row.get("Key") or "").startswith(STATUS_PREFIX)
    ]
    by_source = marker_rows_by_source(markers)
    by_material = marker_rows_by_material(markers)
    source_marker_payloads = latest_status_marker_payloads_by_source(s3, cfg, markers)
    rows: list[dict[str, Any]] = []
    for row in sorted(pdf_rows, key=lambda item: str(item.get("Key") or "")):
        obj = str(row.get("Key") or "")
        source_hash = source_key_hash(cfg.input_bucket, obj)
        source_markers = by_source.get(source_hash, [])
        source_status = latest_source_status(source_markers)
        marker_payload = source_marker_payloads.get(source_hash, {})
        material_id = str(marker_payload.get("material_id") or "")
        source_sha = str(marker_payload.get("source_pdf_sha256") or "")
        material_markers = by_material.get(material_id, []) if material_id else []
        material_status = latest_source_status(material_markers) if material_id else None
        combined_status = latest_source_status(source_markers + material_markers)
        if combined_status in ("popo_done", "popo_done_frozen", "done"):
            state = "gpu_done"
            next_action = "handoff_to_raw_rebuild"
        elif combined_status in ("mineru_done", "mineru_done_frozen"):
            state = "mineru_only"
            next_action = "recover_or_rerun_minerupopo"
        elif combined_status in ERROR_STATUSES:
            state = "error_marked"
            next_action = "review_error_marker_before_retry"
        elif combined_status in ACTIVE_STATUSES:
            state = "processing_marker"
            next_action = "collect_or_clear_stale_processing_marker"
        else:
            state = "unprocessed"
            next_action = "eligible_for_submit"
        rows.append(
            add_scheduler_fields(
                {
                    "input_object": obj,
                    "input_size_bytes": int(row.get("Size") or 0),
                    "source_hash": source_hash,
                    "source_sha256": source_sha,
                    "material_id": material_id,
                    "canonical_input_object": "",
                    "source_status": source_status or "",
                    "material_status": material_status or "",
                    "marker_reconciliation": marker_reconciliation(source_markers + material_markers, combined_status),
                    "mineru_jobs": [],
                    "mineru_complete_count": 0,
                    "minerupopo_jobs": [],
                    "minerupopo_complete_count": 0,
                    "state": state,
                    "next_action": next_action,
                    "duplicate_input_objects": [],
                }
            )
        )
    active = active_markers(markers)
    counts: dict[str, int] = {}
    scheduler_counts: dict[str, int] = {}
    for item in rows:
        counts[str(item["state"])] = counts.get(str(item["state"]), 0) + 1
        scheduler_counts[str(item["scheduler_state"])] = scheduler_counts.get(str(item["scheduler_state"]), 0) + 1
    return {
        "command": "lineage-audit",
        "applied": False,
        "scope": "first_stage_input_status_only",
        "with_sha": False,
        "input_pdf_count": len(rows),
        "mineru_job_count": None,
        "minerupopo_job_count": None,
        "state_counts": counts,
        "scheduler_state_counts": scheduler_counts,
        "duplicate_sha_group_count": None,
        "active_marker_count": len(active),
        "active_marker_samples": active[:20],
        "marker_counts": {
            "by_source": sum(len(value) for value in by_source.values()),
            "by_material": sum(len(value) for value in by_material.values()),
            "all_status": len(markers),
        },
        "queues": {
            "eligible_for_submit": [item for item in rows if item["next_action"] == "eligible_for_submit"][:sample_limit],
            "review_error_marker_before_retry": [
                item for item in rows if item["next_action"] == "review_error_marker_before_retry"
            ][:sample_limit],
            "choose_canonical_input_before_submit": [],
            "use_canonical_input_output_do_not_submit_duplicate": [],
            "recover_or_rerun_minerupopo": [
                item for item in rows if item["next_action"] == "recover_or_rerun_minerupopo"
            ][:sample_limit],
        },
        "scheduler_queues": {
            "gpu_done": [item for item in rows if item["scheduler_state"] == "gpu_done"][:sample_limit],
            "eligible_for_submit": [item for item in rows if item["scheduler_state"] == "eligible_for_submit"][
                :sample_limit
            ],
            "error_review": [item for item in rows if item["scheduler_state"] == "error_review"][:sample_limit],
            "duplicate_skip": [],
            "duplicate_review": [],
            "mineru_only_resume_popo": [
                item for item in rows if item["scheduler_state"] == "mineru_only_resume_popo"
            ][:sample_limit],
            "active_or_stale": [item for item in rows if item["scheduler_state"] == "active_or_stale"][:sample_limit],
        },
        "marker_reconciliation_summary": {
            "raw_error_marker_count": sum(
                int(item.get("marker_reconciliation", {}).get("raw_error_marker_count") or 0) for item in rows
            ),
            "superseded_error_marker_count": sum(
                int(item.get("marker_reconciliation", {}).get("superseded_error_marker_count") or 0) for item in rows
            ),
            "active_error_item_count": sum(
                1 for item in rows if item.get("marker_reconciliation", {}).get("active_error")
            ),
        },
        "stage_gap_summary": {
            "schema": "luceon-stage-gap-summary/v1",
            "join_key": "material_id",
            "counts": {
                "input_pdf_count": len(rows),
                "input_material_id_count": sum(1 for item in rows if item.get("material_id")),
                "input_without_material_id_count": sum(1 for item in rows if not item.get("material_id")),
            },
            "policy_notes": {
                "scope": "input status markers only; use full lineage-audit before declaring cross-bucket completion",
                "purpose": "fast error-retry planning without scanning large stage buckets",
            },
        },
        "items_sample": rows[:sample_limit],
        "items": rows,
    }


def sort_plan_items(items: list[dict[str, Any]], sort_by: str) -> list[dict[str, Any]]:
    if sort_by == "name":
        return sorted(items, key=lambda item: str(item.get("input_object") or ""))
    if sort_by == "largest":
        return sorted(items, key=lambda item: (-int(item.get("input_size_bytes") or 0), str(item.get("input_object") or "")))
    return sorted(items, key=lambda item: (int(item.get("input_size_bytes") or 0), str(item.get("input_object") or "")))


def normalize_filter_values(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values or []:
        value = str(raw or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def target_filters_from_args(args: argparse.Namespace) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return (
        normalize_filter_values(getattr(args, "input_object", []) or []),
        normalize_filter_values(getattr(args, "material_id", []) or []),
    )


def item_matches_target_filters(
    item: dict[str, Any],
    input_objects: tuple[str, ...],
    material_ids: tuple[str, ...],
) -> bool:
    item_input_object = str(item.get("input_object") or item.get("object") or "")
    if input_objects and item_input_object not in set(input_objects):
        return False
    if material_ids:
        item_material_id = str(item.get("material_id") or "")
        # Newly uploaded inputs can be targeted by object before a status marker
        # has materialized its derived material_id.
        if item_material_id and item_material_id not in set(material_ids):
            return False
        if not item_material_id and not input_objects:
            return False
    return True


def load_lineage_file(path: str) -> dict[str, Any]:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(str(p))
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("command") != "lineage-audit":
        raise ValueError("--lineage-file must point to a lineage-audit JSON")
    return data


def normalize_lineage_scheduler_fields(item: dict[str, Any]) -> dict[str, Any]:
    source_status = str(item.get("source_status") or "")
    material_status = str(item.get("material_status") or "")
    if source_status in ("popo_done", "popo_done_frozen", "done") or material_status in (
        "popo_done",
        "popo_done_frozen",
        "done",
    ):
        item = dict(item)
        item["state"] = "gpu_done"
        item["next_action"] = "handoff_to_raw_rebuild"
        return add_scheduler_fields(item)
    return item


def build_next_batch_plan(
    s3: S3Client,
    cfg: Config,
    limit: int,
    sort_by: str,
    with_sha: bool,
    lineage_file: str = "",
    include_error_review: bool = False,
    input_status_only: bool = False,
    input_objects: list[str] | tuple[str, ...] | None = None,
    material_ids: list[str] | tuple[str, ...] | None = None,
    allow_active: bool = False,
    candidate_states: set[str] | None = None,
) -> dict[str, Any]:
    limit = min(max(1, int(limit or MAX_BATCH_PDFS)), MAX_BATCH_PDFS)
    input_object_filters = normalize_filter_values(input_objects)
    material_id_filters = normalize_filter_values(material_ids)
    if lineage_file:
        lineage = load_lineage_file(lineage_file)
    elif input_status_only:
        lineage = input_status_lineage_audit(s3, cfg, sample_limit=max(100, limit))
    else:
        lineage = first_stage_lineage_audit(s3, cfg, sample_limit=max(100, limit), with_sha=with_sha)
    selected: list[dict[str, Any]] = []
    normalized_items = [normalize_lineage_scheduler_fields(item) for item in lineage["items"]]
    target_items = [
        item
        for item in normalized_items
        if item_matches_target_filters(item, input_object_filters, material_id_filters)
    ]
    selected_states = set(candidate_states or {"eligible_for_submit"})
    if include_error_review:
        selected_states.add("error_review")
    if lineage.get("active_marker_count") and not allow_active:
        status = "BLOCKED_ACTIVE_MARKERS"
    else:
        candidates = [item for item in target_items if item.get("scheduler_state") in selected_states]
        selected = sort_plan_items(candidates, sort_by)[:limit]
        if selected:
            status = "READY"
        elif input_object_filters or material_id_filters:
            status = "TARGET_NOT_ELIGIBLE"
        else:
            status = "IDLE"
    return {
        "command": "plan-next",
        "applied": False,
        "status": status,
        "mode": "batch",
        "limit": limit,
        "sort_by": sort_by,
        "with_sha": with_sha,
        "include_error_review": include_error_review,
        "input_status_only": input_status_only,
        "allow_active": allow_active,
        "candidate_states": sorted(selected_states),
        "target_filters": {
            "input_objects": list(input_object_filters),
            "material_ids": list(material_id_filters),
            "matched_count": len(target_items),
        },
        "lineage_scope": lineage.get("scope"),
        "selected_count": len(selected),
        "selected": [compact_lineage_item(item) for item in selected],
        "scheduler_state_counts": lineage.get("scheduler_state_counts", {}),
        "state_counts": lineage.get("state_counts", {}),
        "duplicate_sha_group_count": lineage.get("duplicate_sha_group_count"),
        "active_marker_count": lineage.get("active_marker_count"),
        "active_marker_samples": lineage.get("active_marker_samples", []),
        "marker_reconciliation_summary": lineage.get("marker_reconciliation_summary", {}),
        "skipped_policy": {
            "error_review": (
                "included for explicit recovery retry" if include_error_review else "not included in ordinary next batch"
            ),
            "duplicate_skip": "not included; use canonical completed asset",
            "mineru_only_resume_popo": "not included in MinerU batch; resume Popo from frozen MinerU",
            "gpu_done": "not included; hand off to downstream raw rebuild",
        },
    }


def summarize_top_prefix(rows: list[dict[str, str]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for row in rows:
        key = str(row.get("Key") or "")
        size = int(row.get("Size") or 0)
        prefix = key.split("/", 1)[0] if "/" in key else "(root)"
        slot = summary.setdefault(prefix, {"object_count": 0, "size_bytes": 0})
        slot["object_count"] += 1
        slot["size_bytes"] += size
    return dict(sorted(summary.items(), key=lambda item: (-item[1]["size_bytes"], item[0])))


def bucket_contract_audit(s3: S3Client, cfg: Config) -> dict[str, Any]:
    mineru_rows = s3.list_objects(cfg.mineru_bucket)
    popo_rows = s3.list_objects(cfg.minerupopo_bucket)
    archive_rows = s3.list_objects(cfg.archive_bucket)
    mineru_prefixes = summarize_top_prefix(mineru_rows)
    popo_prefixes = summarize_top_prefix(popo_rows)
    archive_prefixes = summarize_top_prefix(archive_rows)
    auxiliary_mineru_prefixes = {
        prefix: value for prefix, value in mineru_prefixes.items() if prefix not in {"mineru"}
    }
    mineru_jobs = discover_stage_jobs(mineru_rows, "mineru")
    popo_jobs = discover_stage_jobs(popo_rows, "minerupopo")
    formal_mineru_complete = [job for job in mineru_jobs.values() if stage_complete("mineru", job)]
    formal_popo_complete = [job for job in popo_jobs.values() if stage_complete("minerupopo", job)]
    return {
        "schema": "luceon-first-stage-bucket-contract-audit/v1",
        "command": "bucket-contract-audit",
        "applied": False,
        "official_prefix_policy": {
            cfg.mineru_bucket: ["mineru/{material_id}/{run_id}/"],
            cfg.minerupopo_bucket: ["minerupopo/{material_id}/{run_id}/"],
            cfg.archive_bucket: ["gpu-wrapper/", "source-pdf/", "popo-input/"],
        },
        "mineru_bucket": {
            "bucket": cfg.mineru_bucket,
            "top_prefixes": mineru_prefixes,
            "formal_job_count": len(mineru_jobs),
            "formal_complete_job_count": len(formal_mineru_complete),
            "auxiliary_prefixes": auxiliary_mineru_prefixes,
        },
        "minerupopo_bucket": {
            "bucket": cfg.minerupopo_bucket,
            "top_prefixes": popo_prefixes,
            "formal_job_count": len(popo_jobs),
            "formal_complete_job_count": len(formal_popo_complete),
            "auxiliary_prefixes": {prefix: value for prefix, value in popo_prefixes.items() if prefix != "minerupopo"},
        },
        "archive_bucket": {
            "bucket": cfg.archive_bucket,
            "top_prefixes": archive_prefixes,
        },
        "classification": {
            "eduassets-mineru/popo-input": "auxiliary Popo resume input package; not an official MinerU completion asset",
            "eduassets-mineru/popo-input-lite": "auxiliary failed/experimental Popo resume package; not an official MinerU completion asset",
            "official_mineru_completion_rule": "count only mineru/{material_id}/{run_id}/ with manifest.json and content list",
            "official_popo_completion_rule": "count only minerupopo/{material_id}/{run_id}/manifest.json plus document tree or popo raw output",
        },
    }


def build_safe_report(s3: S3Client, cfg: Config, sample_limit: int, with_sha: bool) -> dict[str, Any]:
    lineage = first_stage_lineage_audit(s3, cfg, sample_limit=sample_limit, with_sha=with_sha)
    contract = bucket_contract_audit(s3, cfg)
    return redact_presigned_urls(
        {
            "command": "report",
            "applied": False,
            "scope": "first_stage_only",
            "with_sha": with_sha,
            "summary": {
                "input_pdf_count": lineage.get("input_pdf_count"),
                "mineru_job_count": lineage.get("mineru_job_count"),
                "minerupopo_job_count": lineage.get("minerupopo_job_count"),
                "state_counts": lineage.get("state_counts", {}),
                "scheduler_state_counts": lineage.get("scheduler_state_counts", {}),
                "duplicate_sha_group_count": lineage.get("duplicate_sha_group_count"),
                "active_marker_count": lineage.get("active_marker_count"),
                "marker_reconciliation_summary": lineage.get("marker_reconciliation_summary", {}),
                "stage_gap_summary": lineage.get("stage_gap_summary", {}),
            },
            "scheduler_queues": {
                key: [compact_lineage_item(item) for item in value]
                for key, value in (lineage.get("scheduler_queues") or {}).items()
            },
            "bucket_contract": contract,
        }
    )


def safe_list_objects_for_report(s3: S3Client, bucket: str) -> tuple[list[dict[str, str]], str]:
    try:
        return s3.list_objects(bucket), ""
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"


def build_pipeline_asset_gap_report(
    s3: S3Client,
    cfg: Config,
    raw_bucket: str,
    clean_bucket: str,
    sample_limit: int,
    with_sha: bool,
) -> dict[str, Any]:
    lineage = first_stage_lineage_audit(s3, cfg, sample_limit=sample_limit, with_sha=with_sha)
    mineru_rows, mineru_error = safe_list_objects_for_report(s3, cfg.mineru_bucket)
    popo_rows, popo_error = safe_list_objects_for_report(s3, cfg.minerupopo_bucket)
    raw_rows, raw_error = safe_list_objects_for_report(s3, raw_bucket)
    clean_rows, clean_error = safe_list_objects_for_report(s3, clean_bucket)

    mineru_jobs = discover_stage_jobs(mineru_rows, "mineru")
    popo_jobs = discover_stage_jobs(popo_rows, "minerupopo")
    raw_jobs = discover_stage_jobs(raw_rows, "raw")
    clean_jobs = discover_stage_jobs(clean_rows, "clean")

    mineru_pairs = complete_stage_pairs(mineru_jobs, "mineru")
    usable_mineru_job_pairs = usable_mineru_pairs(mineru_jobs)
    popo_pairs = complete_stage_pairs(popo_jobs, "minerupopo")
    raw_pairs = complete_stage_pairs(raw_jobs, "raw")
    clean_pairs = complete_stage_pairs(clean_jobs, "clean")
    formal_mineru_material_ids = material_ids_from_pairs(mineru_pairs)
    usable_mineru_material_ids = material_ids_from_pairs(usable_mineru_job_pairs)
    popo_material_ids = material_ids_from_pairs(popo_pairs)
    raw_material_ids = material_ids_from_pairs(raw_pairs)
    clean_material_ids = material_ids_from_pairs(clean_pairs)

    pdf_without_mineru_items = [
        item
        for item in lineage.get("items", [])
        if item.get("material_id")
        and str(item.get("material_id")) not in usable_mineru_material_ids
        and item.get("scheduler_state") != "duplicate_skip"
    ]
    no_material_id_count = sum(1 for item in lineage.get("items", []) if not item.get("material_id"))
    usable_mineru_without_popo = usable_mineru_material_ids - popo_material_ids
    formal_mineru_without_popo = formal_mineru_material_ids - popo_material_ids
    popo_without_raw = popo_material_ids - raw_material_ids
    raw_without_clean = raw_material_ids - clean_material_ids
    strict_run_id_mineru_without_popo = mineru_pairs - popo_pairs
    strict_run_id_popo_without_raw = popo_pairs - raw_pairs
    strict_run_id_raw_without_clean = raw_pairs - clean_pairs

    return redact_presigned_urls(
        {
            "command": "asset-gap",
            "applied": False,
            "scope": "input_to_clean_read_only_inventory",
            "with_sha": with_sha,
            "join_key": "material_id",
            "join_policy": {
                "asset_identity": "material_id",
                "stage_execution_identity": "run_id",
                "rule": "Count downstream availability by material_id. Keep run_id pair mismatches as diagnostics only.",
                "why": (
                    "Staged and recovery runs intentionally create independent MinerU and Popo run_ids for the same "
                    "PDF material."
                ),
            },
            "buckets": {
                "input": cfg.input_bucket,
                "mineru": cfg.mineru_bucket,
                "minerupopo": cfg.minerupopo_bucket,
                "raw": raw_bucket,
                "clean": clean_bucket,
            },
            "bucket_errors": {
                "mineru": mineru_error,
                "minerupopo": popo_error,
                "raw": raw_error,
                "clean": clean_error,
            },
            "counts": {
                "input_pdf_count": lineage.get("input_pdf_count"),
                "input_without_material_id_count": no_material_id_count,
                "usable_mineru_material_count": len(usable_mineru_material_ids),
                "formal_mineru_manifest_material_count": len(formal_mineru_material_ids),
                "complete_popo_material_count": len(popo_material_ids),
                "complete_raw_material_count": len(raw_material_ids),
                "complete_clean_material_count": len(clean_material_ids),
                "pdf_without_usable_mineru": None if no_material_id_count else len(pdf_without_mineru_items),
                "pdf_without_usable_mineru_known_material_id": len(pdf_without_mineru_items),
                "usable_mineru_material_without_complete_popo": len(usable_mineru_without_popo),
                "formal_mineru_manifest_material_without_complete_popo": len(formal_mineru_without_popo),
                "complete_popo_material_without_complete_raw": len(popo_without_raw),
                "complete_raw_material_without_complete_clean": len(raw_without_clean),
            },
            "stage_job_counts": {
                "mineru_jobs": len(mineru_jobs),
                "usable_mineru_jobs": len(usable_mineru_job_pairs),
                "complete_mineru_jobs": len(mineru_pairs),
                "popo_jobs": len(popo_jobs),
                "complete_popo_jobs": len(popo_pairs),
                "raw_jobs": len(raw_jobs),
                "complete_raw_jobs": len(raw_pairs),
                "clean_jobs": len(clean_jobs),
                "complete_clean_jobs": len(clean_pairs),
            },
            "scheduler_state_counts": lineage.get("scheduler_state_counts", {}),
            "samples": {
                "pdf_without_usable_mineru": [
                    compact_lineage_item(item) for item in pdf_without_mineru_items[:sample_limit]
                ],
                "usable_mineru_material_without_complete_popo": sample_ids(
                    usable_mineru_without_popo, sample_limit
                ),
                "formal_mineru_manifest_material_without_complete_popo": sample_ids(
                    formal_mineru_without_popo, sample_limit
                ),
                "complete_popo_material_without_complete_raw": sample_ids(popo_without_raw, sample_limit),
                "complete_raw_material_without_complete_clean": sample_ids(raw_without_clean, sample_limit),
            },
            "run_id_pair_diagnostic": {
                "strict_mineru_run_without_same_run_popo": len(strict_run_id_mineru_without_popo),
                "strict_popo_run_without_same_run_raw": len(strict_run_id_popo_without_raw),
                "strict_raw_run_without_same_run_clean": len(strict_run_id_raw_without_clean),
                "sample_mineru_run_without_same_run_popo": sample_pairs(strict_run_id_mineru_without_popo, sample_limit),
                "interpretation": "diagnostic_only_not_asset_gap_unless_the_stage_contract_requires_same_run_id",
            },
        }
    )


def pdf_candidates(s3: S3Client, cfg: Config, limit: int, allow_active: bool = False) -> dict[str, Any]:
    rows, markers = list_state(s3, cfg)
    active = active_markers(markers)
    if active and not allow_active:
        return {
            "status": "BUSY",
            "reason": "active_batch_or_processing_marker_exists",
            "active_marker_count": len(active),
            "active_marker_samples": active[:20],
            "items": [],
        }
    pdf_rows = []
    for row in rows:
        key = str(row.get("Key", ""))
        if not key or key.endswith("/") or key.startswith(STATUS_PREFIX):
            continue
        if key.lower().endswith(".pdf"):
            pdf_rows.append(row)
    pdf_rows.sort(key=lambda r: (str(r.get("LastModified", "")), str(r.get("Key", ""))))
    docs = []
    skipped = []
    selected_material_ids: set[str] = set()
    for row in pdf_rows:
        if len(docs) >= limit:
            break
        obj = str(row.get("Key") or "")
        src_hash = source_key_hash(cfg.input_bucket, obj)
        if has_source_terminal_or_lock(markers, src_hash):
            skipped.append({"object": obj, "reason": "source_has_terminal_or_lock"})
            continue
        pdf = s3.get_bytes(cfg.input_bucket, obj, max_bytes=cfg.max_file_bytes)
        source_sha = sha256_hex(pdf)
        material_id = material_id_from_sha(source_sha)
        if material_id in selected_material_ids:
            skipped.append({"object": obj, "reason": "duplicate_material_in_current_scan", "material_id": material_id})
            continue
        if has_terminal_or_lock(markers, material_id, src_hash):
            skipped.append({"object": obj, "reason": "material_has_terminal_or_lock"})
            continue
        doc_id = "dify_%s_%03d" % (source_sha[:16], len(docs) + 1)
        docs.append(
            {
                "schema": "luceon-minio-input-scan/v2",
                "status": "SELECTED",
                "selected": True,
                "bucket": cfg.input_bucket,
                "object": obj,
                "filename": obj.rsplit("/", 1)[-1] or "input.pdf",
                "source_hash": src_hash,
                "material_id": material_id,
                "doc_id": doc_id,
                "source_pdf_sha256": source_sha,
                "source_pdf_size_bytes": len(pdf),
                "source_url": s3.presign_get(
                    cfg.minio_public_endpoint, cfg.input_bucket, obj, expires=cfg.presign_expires
                ),
                "source_url_expires_seconds": cfg.presign_expires,
            }
        )
        selected_material_ids.add(material_id)
    return {
        "status": "READY" if docs else "IDLE",
        "input_pdf_count": len(pdf_rows),
        "selected_count": len(docs),
        "items": docs,
        "skipped": skipped[:50],
    }


def wrapper_ready(wrapper: WrapperClient, require_mineru: bool = True, timeout: int = 30) -> dict[str, Any]:
    health = wrapper.health(timeout=timeout)
    status = str(health.get("status") or "").lower()
    mineru_health = health.get("mineru_health")
    if isinstance(mineru_health, str):
        try:
            mineru_health = json.loads(mineru_health)
        except Exception:
            mineru_health = {"raw": mineru_health}
    mineru_ok = isinstance(mineru_health, dict) and str(mineru_health.get("status") or "").lower() in (
        "ok",
        "healthy",
    )
    return {
        "ok": status in ("ok", "healthy") and (mineru_ok or not require_mineru),
        "health": json_safe(health),
        "mineru_health": mineru_health,
        "require_mineru": require_mineru,
    }


def wrapper_offline_payload(exc: BaseException) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "GPU_OFFLINE",
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


def make_processing_marker(s3: S3Client, cfg: Config, doc: dict[str, Any], lock_id: str) -> dict[str, Any]:
    doc = dict(doc)
    doc["lock_id"] = lock_id
    marker = {
        "schema": "luceon-input-status-marker/v1",
        "status": "processing",
        "bucket": cfg.input_bucket,
        "object": doc["object"],
        "source_hash": doc["source_hash"],
        "material_id": doc["material_id"],
        "source_pdf_sha256": doc["source_pdf_sha256"],
        "size_bytes": doc["source_pdf_size_bytes"],
        "lock_id": lock_id,
        "created_at": now_iso(),
        "workflow_stage": "code_submit_gpu_wrapper_batch",
        "batch_mode": True,
    }
    doc["processing_marker"] = s3.put_json(
        cfg.input_bucket, status_marker_object(doc["material_id"], lock_id, "processing"), marker
    )
    doc["source_processing_marker"] = s3.put_json(
        cfg.input_bucket, source_status_marker_object(doc["source_hash"], lock_id, "processing"), marker
    )
    return doc


def submit_batch(args: argparse.Namespace) -> dict[str, Any]:
    apply_run_mode(args, "submit")
    cfg = cfg_from_args(args)
    s3 = S3Client(cfg.minio_endpoint, cfg.minio_access_key, cfg.minio_secret_key)
    scan = pdf_candidates(s3, cfg, args.limit, allow_active=args.allow_active)
    if scan["status"] != "READY":
        return {"command": "submit", "applied": False, "scan": scan}
    wrapper = WrapperClient(cfg.wrapper_url, cfg.wrapper_api_key)
    try:
        health = wrapper_ready(wrapper)
    except Exception as exc:
        return {
            "command": "submit",
            "applied": False,
            "status": "GPU_OFFLINE",
            "health": wrapper_offline_payload(exc),
        }
    if not health["ok"]:
        return {"command": "submit", "applied": False, "status": "GPU_OFFLINE", "health": health}
    docs = scan["items"]
    client_batch_id = "dify_batch_%s" % utc_stamp()
    lock_id = "scan-%s" % utc_stamp()
    payload_docs = [
        {
            "doc_id": d["doc_id"],
            "lang": cfg.lang,
            "source": {
                "type": "url",
                "url": d["source_url"],
                "filename": d["filename"],
                "sha256": d["source_pdf_sha256"],
                "size_bytes": d["source_pdf_size_bytes"],
            },
        }
        for d in docs
    ]
    if not args.apply:
        return {
            "command": "submit",
            "applied": False,
            "status": "DRY_RUN",
            "would_submit_count": len(docs),
            "client_batch_id": client_batch_id,
            "docs": [{k: d[k] for k in ("object", "material_id", "source_pdf_size_bytes")} for d in docs],
            "health": health,
        }
    marked_docs = [make_processing_marker(s3, cfg, d, lock_id) for d in docs]
    submit = wrapper.request_json(
        "POST",
        "/api/v1/batches",
        payload={"batch_id": client_batch_id, "lang": cfg.lang, "documents": payload_docs},
        timeout=600,
    )
    batch_id = str(submit.get("batch_id") or client_batch_id)
    batch_marker = {
        "schema": "luceon-wrapper-batch-submitted/v1",
        "status": "submitted",
        "batch_id": batch_id,
        "client_batch_id": client_batch_id,
        "lock_id": lock_id,
        "created_at": now_iso(),
        "document_count": len(marked_docs),
        "documents": [
            {
                "bucket": d["bucket"],
                "object": d["object"],
                "filename": d["filename"],
                "source_hash": d["source_hash"],
                "material_id": d["material_id"],
                "doc_id": d["doc_id"],
                "source_pdf_sha256": d["source_pdf_sha256"],
                "source_pdf_size_bytes": d["source_pdf_size_bytes"],
                "processing_marker_lock_id": lock_id,
            }
            for d in marked_docs
        ],
        "submit_response": json_safe(submit),
        "collector_hint": {
            "wrapper_status_url": "/api/v1/batches/" + batch_id,
            "terminal_statuses": ["succeeded", "failed", "partial"],
        },
    }
    ref = s3.put_json(cfg.input_bucket, BATCH_PREFIX + f"{batch_id}.submitted.json", batch_marker)
    return {
        "command": "submit",
        "applied": True,
        "status": "SUBMITTED",
        "batch_id": batch_id,
        "selected_count": len(marked_docs),
        "submitted_marker": ref,
    }


def safe_tar_path(path: str) -> str:
    rel = str(path or "").replace("\\", "/").lstrip("./")
    parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
    return "/".join(parts)


def tar_members(tf: tarfile.TarFile) -> list[tarfile.TarInfo]:
    return [m for m in tf.getmembers() if m.isfile()]


def read_member(tf: tarfile.TarFile, member: tarfile.TarInfo | None) -> bytes:
    if member is None:
        raise ValueError("tar member is missing")
    fh = tf.extractfile(member)
    if fh is None:
        raise ValueError("cannot read tar member: " + member.name)
    return fh.read()


def find_member(tf: tarfile.TarFile, predicate, required: bool, label: str) -> tarfile.TarInfo | None:
    for member in tar_members(tf):
        rel = safe_tar_path(member.name)
        if predicate(rel):
            return member
    if required:
        raise ValueError("required result member missing: " + label)
    return None


def put_full_tar_tree(s3: S3Client, cfg: Config, tf: tarfile.TarFile, material_id: str, run_id: str) -> dict[str, Any]:
    refs: dict[str, list[dict[str, Any]]] = {"mineru": [], "minerupopo": [], "metadata": [], "logs": [], "other": []}
    counts = {k: 0 for k in refs}
    for member in tar_members(tf):
        rel = safe_tar_path(member.name)
        if not rel:
            continue
        data = read_member(tf, member)
        ctype = content_type_for(rel)
        if rel.startswith("mineru/"):
            key = rel[len("mineru/") :]
            obj = f"mineru/{material_id}/{run_id}/official/{key}"
            refs["mineru"].append(s3.put_bytes(cfg.mineru_bucket, obj, data, ctype))
            counts["mineru"] += 1
        elif rel.startswith("minerupopo/") or rel.startswith("enhanced/"):
            obj = f"minerupopo/{material_id}/{run_id}/official/{rel}"
            refs["minerupopo"].append(s3.put_bytes(cfg.minerupopo_bucket, obj, data, ctype))
            counts["minerupopo"] += 1
        elif rel.startswith("metadata/"):
            obj = f"gpu-wrapper/{material_id}/{run_id}/extracted/{rel}"
            refs["metadata"].append(s3.put_bytes(cfg.archive_bucket, obj, data, ctype))
            counts["metadata"] += 1
        elif rel.startswith("logs/"):
            obj = f"gpu-wrapper/{material_id}/{run_id}/extracted/{rel}"
            refs["logs"].append(s3.put_bytes(cfg.archive_bucket, obj, data, ctype))
            counts["logs"] += 1
        else:
            obj = f"gpu-wrapper/{material_id}/{run_id}/extracted/{rel}"
            refs["other"].append(s3.put_bytes(cfg.archive_bucket, obj, data, ctype))
            counts["other"] += 1
    return {"counts": counts, "refs": refs}


def freeze_success_doc(
    s3: S3Client,
    cfg: Config,
    wrapper: WrapperClient,
    doc: dict[str, Any],
    doc_status: dict[str, Any],
    batch_status: dict[str, Any],
) -> dict[str, Any]:
    input_object = str(doc.get("object") or "")
    source_hash = str(doc.get("source_hash") or source_key_hash(cfg.input_bucket, input_object))
    material_id = str(doc.get("material_id") or "")
    run_id = str(doc_status.get("run_id") or doc_status.get("job_id") or doc.get("doc_id") or "")
    if not run_id:
        raise ValueError("succeeded document missing run_id/job_id")
    result_url = doc_status.get("result_url") or f"/api/v1/batches/{batch_status.get('batch_id')}/documents/{run_id}/result"
    tar_bytes = wrapper.request_bytes("GET", str(result_url), timeout=3600)
    tar_sha = sha256_hex(tar_bytes)
    pdf_bytes = s3.get_bytes(cfg.input_bucket, input_object, max_bytes=cfg.max_file_bytes)
    source_sha = sha256_hex(pdf_bytes)
    mineru_prefix = f"mineru/{material_id}/{run_id}/"
    popo_prefix = f"minerupopo/{material_id}/{run_id}/"
    archive_object = f"gpu-wrapper/{material_id}/{run_id}/result.tar.gz"
    source_pdf_object = f"source-pdf/{material_id}/{run_id}/source.pdf"
    objects: dict[str, Any] = {
        "archive": s3.put_bytes(cfg.archive_bucket, archive_object, tar_bytes, "application/gzip"),
        "source_pdf": s3.put_bytes(cfg.archive_bucket, source_pdf_object, pdf_bytes, "application/pdf"),
    }
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
        content_v2 = find_member(
            tf,
            lambda p: p.endswith("_content_list_v2.json") or p.endswith("content_list_v2.json"),
            True,
            "content_list_v2.json",
        )
        content = find_member(
            tf,
            lambda p: p.endswith("_content_list.json") and not p.endswith("_content_list_v2.json"),
            False,
            "content_list.json",
        )
        middle = find_member(tf, lambda p: p.endswith("_middle.json"), False, "middle.json")
        model = find_member(tf, lambda p: p.endswith("_model.json"), False, "model.json")
        full_md = find_member(tf, lambda p: p.startswith("mineru/") and p.endswith(".md"), False, "full.md")
        tree = find_member(
            tf,
            lambda p: p.endswith("enhanced/document_tree.json") or p.endswith("document_tree.json"),
            True,
            "document_tree.json",
        )
        tree_txt = find_member(
            tf,
            lambda p: p.endswith("enhanced/document_tree.txt") or p.endswith("document_tree.txt"),
            False,
            "document_tree.txt",
        )
        popo_raw = find_member(
            tf, lambda p: p.endswith("enhanced/popo_raw.json") or p.endswith("popo_raw.json"), False, "popo_raw.json"
        )
        label = find_member(tf, lambda p: "/label_normalization/" in p and p.endswith(".json"), False, "label.json")
        inference = find_member(tf, lambda p: "/inference/" in p and p.endswith(".json"), False, "inference.json")
        build_tree = find_member(tf, lambda p: "/build_tree/" in p and p.endswith(".json"), False, "build_tree.json")
        objects["content_list_v2"] = s3.put_bytes(
            cfg.mineru_bucket, mineru_prefix + "content_list_v2.json", read_member(tf, content_v2), "application/json"
        )
        if content:
            objects["content_list"] = s3.put_bytes(
                cfg.mineru_bucket, mineru_prefix + "content_list.json", read_member(tf, content), "application/json"
            )
        if middle:
            objects["middle"] = s3.put_bytes(
                cfg.mineru_bucket, mineru_prefix + "middle.json", read_member(tf, middle), "application/json"
            )
        if model:
            objects["model"] = s3.put_bytes(
                cfg.mineru_bucket, mineru_prefix + "model.json", read_member(tf, model), "application/json"
            )
        if full_md:
            objects["full_md"] = s3.put_bytes(
                cfg.mineru_bucket, mineru_prefix + "full.md", read_member(tf, full_md), "text/markdown"
            )
        image_refs = []
        for member in tar_members(tf):
            rel = safe_tar_path(member.name)
            lower = rel.lower()
            if "/images/" not in lower:
                continue
            image_name = rel.split("/images/", 1)[-1]
            data = read_member(tf, member)
            if rel.startswith("minerupopo/"):
                ref = s3.put_bytes(cfg.minerupopo_bucket, popo_prefix + "images/" + image_name, data, content_type_for(rel))
            else:
                ref = s3.put_bytes(cfg.mineru_bucket, mineru_prefix + "images/" + image_name, data, content_type_for(rel))
            ref["source_member"] = rel
            image_refs.append(ref)
        objects["images"] = image_refs
        objects["document_tree"] = s3.put_bytes(
            cfg.minerupopo_bucket, popo_prefix + "document_tree.json", read_member(tf, tree), "application/json"
        )
        if tree_txt:
            objects["document_tree_txt"] = s3.put_bytes(
                cfg.minerupopo_bucket, popo_prefix + "document_tree.txt", read_member(tf, tree_txt), "text/plain"
            )
        if popo_raw:
            objects["popo_raw"] = s3.put_bytes(
                cfg.minerupopo_bucket, popo_prefix + "popo_raw.json", read_member(tf, popo_raw), "application/json"
            )
        if label:
            objects["label_normalization"] = s3.put_bytes(
                cfg.minerupopo_bucket, popo_prefix + "label_normalization.json", read_member(tf, label), "application/json"
            )
        if inference:
            objects["inference"] = s3.put_bytes(
                cfg.minerupopo_bucket, popo_prefix + "inference.json", read_member(tf, inference), "application/json"
            )
        if build_tree:
            objects["build_tree"] = s3.put_bytes(
                cfg.minerupopo_bucket, popo_prefix + "build_tree.json", read_member(tf, build_tree), "application/json"
            )
        objects["full_tree"] = put_full_tar_tree(s3, cfg, tf, material_id, run_id)
    manifest = {
        "schema": "luceon-gpu-wrapper-mineru-minerupopo-manifest/v2",
        "material_id": material_id,
        "run_id": run_id,
        "stage_run_ids": {"mineru": run_id, "minerupopo": run_id},
        "lineage": {
            "join_key": "material_id",
            "stage_run_ids_can_differ": False,
            "mode": "legacy_monolithic_mineru_and_popo",
        },
        "batch_id": str(batch_status.get("batch_id") or ""),
        "doc_id": str(doc.get("doc_id") or doc_status.get("doc_id") or ""),
        "source_pdf": {
            "filename": str(doc.get("filename") or input_object.rsplit("/", 1)[-1]),
            "sha256": source_sha,
            "size_bytes": len(pdf_bytes),
            "submit_mode": "minio_presigned_url",
            "input_bucket": cfg.input_bucket,
            "input_object": input_object,
            "bucket": cfg.archive_bucket,
            "object": source_pdf_object,
        },
        "wrapper": {"batch_status": batch_status, "document_status": doc_status},
        "stage_prefixes": {
            "mineru": {"bucket": cfg.mineru_bucket, "prefix": mineru_prefix, "official_prefix": mineru_prefix + "official/"},
            "minerupopo": {
                "bucket": cfg.minerupopo_bucket,
                "prefix": popo_prefix,
                "official_prefix": popo_prefix + "official/",
            },
            "archive": {
                "bucket": cfg.archive_bucket,
                "object": archive_object,
                "extracted_prefix": f"gpu-wrapper/{material_id}/{run_id}/extracted/",
            },
        },
        "archive_sha256": tar_sha,
        "full_tree_counts": objects.get("full_tree", {}).get("counts", {}),
        "objects": objects,
        "created_at": now_iso(),
    }
    manifest_ref = s3.put_json(cfg.minerupopo_bucket, popo_prefix + "manifest.json", manifest)
    status_payload = {
        "workflow_stage": "code_collect_gpu_wrapper_batch",
        "manifest": manifest_ref,
        "wrapper_status": doc_status,
        "batch_id": str(batch_status.get("batch_id") or ""),
        "source_pdf_sha256": source_sha,
        "source_pdf_size_bytes": len(pdf_bytes),
    }
    popo_done_markers = write_status_marker(
        s3,
        cfg,
        doc,
        run_id,
        "popo_done_frozen",
        status_payload,
    )
    done_markers = write_status_marker(
        s3,
        cfg,
        doc,
        run_id,
        "done",
        status_payload,
    )
    cleanup = cleanup_processing(s3, cfg, doc)
    return {
        "schema": "luceon-code-gpu-wrapper-minio-store/v1",
        "status": "PASS",
        "material_id": material_id,
        "run_id": run_id,
        "manifest": manifest_ref,
        "objects": objects,
        "status_markers": {"popo_done_frozen": popo_done_markers, "done": done_markers},
        "processing_marker_cleanup": cleanup,
    }


def stage_batch_terminal(status: dict[str, Any]) -> bool:
    return str(status.get("status") or "").lower() in {
        "succeeded",
        "success",
        "partial",
        "failed",
        "error",
        "canceled",
        "cancelled",
        "timeout",
    }


def stage_doc_success(stage: str, doc_status: dict[str, Any]) -> bool:
    status = str(doc_status.get("status") or "").lower()
    if status in {
        "succeeded",
        "success",
        "completed",
        "done",
        f"{stage}_succeeded",
        f"{stage}_completed",
        f"{stage}_done",
        f"{stage}_done_frozen",
    }:
        return True
    if stage == "mineru" and status in {"mineru_done_frozen"}:
        return True
    if stage == "popo" and status in {"popo_done_frozen", "popo_succeeded"}:
        return True
    return False


def stage_doc_terminal(stage: str, doc_status: dict[str, Any]) -> bool:
    status = str(doc_status.get("status") or "").lower()
    if status in {
        "succeeded",
        "success",
        "completed",
        "done",
        "failed",
        "error",
        "canceled",
        "cancelled",
        "timeout",
    }:
        return True
    if stage == "mineru" and status in {
        "mineru_succeeded",
        "mineru_completed",
        "mineru_failed",
        "mineru_done_frozen",
    }:
        return True
    if stage == "popo" and status in {
        "popo_succeeded",
        "popo_completed",
        "popo_failed",
        "popo_done_frozen",
    }:
        return True
    return False


def match_doc_status(statuses: list[Any], doc: dict[str, Any], fallback_index: int = 0) -> dict[str, Any]:
    doc_id = str(doc.get("doc_id") or "")
    source_sha = str(doc.get("source_pdf_sha256") or "")
    run_id = str(doc.get("run_id") or "")
    for item in statuses:
        if not isinstance(item, dict):
            continue
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        if doc_id and str(item.get("doc_id") or "") == doc_id:
            return item
        if run_id and str(item.get("run_id") or item.get("job_id") or "") == run_id:
            return item
        if source_sha and str(source.get("sha256") or "") == source_sha:
            return item
    if 0 <= fallback_index < len(statuses) and isinstance(statuses[fallback_index], dict):
        return statuses[fallback_index]
    return {}


def run_id_from_doc_status(doc_status: dict[str, Any], label: str) -> str:
    run_id = str(doc_status.get("run_id") or doc_status.get("job_id") or "")
    if not run_id:
        raise ValueError(f"{label} submit response missing run_id/job_id")
    return run_id


def lineage_item_to_submit_doc(s3: S3Client, cfg: Config, item: dict[str, Any], index: int) -> dict[str, Any]:
    obj = str(item.get("input_object") or "")
    if not obj:
        raise ValueError("lineage item missing input_object")
    source_sha = str(item.get("source_sha256") or "")
    material_id = str(item.get("material_id") or "")
    if not source_sha or not material_id:
        data = s3.get_bytes(cfg.input_bucket, obj, max_bytes=cfg.max_file_bytes)
        source_sha = sha256_hex(data)
        material_id = material_id_from_sha(source_sha)
        size_bytes = len(data)
    else:
        size_bytes = int(item.get("input_size_bytes") or 0)
    return {
        "bucket": cfg.input_bucket,
        "object": obj,
        "filename": obj.rsplit("/", 1)[-1] or "input.pdf",
        "source_hash": str(item.get("source_hash") or source_key_hash(cfg.input_bucket, obj)),
        "material_id": material_id,
        "doc_id": "staged_%s_%03d" % (source_sha[:16], index + 1),
        "source_pdf_sha256": source_sha,
        "source_pdf_size_bytes": size_bytes,
        "source_url": s3.presign_get(cfg.minio_public_endpoint, cfg.input_bucket, obj, expires=cfg.presign_expires),
        "source_url_expires_seconds": cfg.presign_expires,
    }


def int_or_none(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def input_object_from_url(url: str, input_bucket: str) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    path = urllib.parse.unquote(parsed.path or "").lstrip("/")
    if not path:
        return ""
    prefix = input_bucket.rstrip("/") + "/"
    if path.startswith(prefix):
        return path[len(prefix) :]
    return ""


def input_pdf_lookup_rows(s3: S3Client, cfg: Config) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for row in s3.list_objects(cfg.input_bucket):
        key = str(row.get("Key") or "")
        if key.lower().endswith(".pdf"):
            rows[key] = row
    return rows


def resolve_existing_stage_input_object(
    s3: S3Client,
    cfg: Config,
    doc_status: dict[str, Any],
) -> tuple[str, str, int]:
    source = doc_status.get("source") if isinstance(doc_status.get("source"), dict) else {}
    declared_sha = str(
        source.get("sha256")
        or source.get("source_sha256")
        or doc_status.get("source_pdf_sha256")
        or doc_status.get("sha256")
        or ""
    )
    declared_size = int_or_none(
        source.get("size_bytes") or source.get("source_pdf_size_bytes") or doc_status.get("source_pdf_size_bytes")
    )
    candidate_objects = normalize_filter_values(
        [
            str(source.get("input_object") or ""),
            str(source.get("object") or ""),
            input_object_from_url(str(source.get("url") or ""), cfg.input_bucket),
            input_object_from_url(str(source.get("source_url") or ""), cfg.input_bucket),
            str(doc_status.get("input_object") or ""),
            str(source.get("filename") or ""),
            str(doc_status.get("filename") or ""),
        ]
    )
    rows_by_key = input_pdf_lookup_rows(s3, cfg)

    def matches_declared(obj: str) -> tuple[str, str, int] | None:
        row = rows_by_key.get(obj)
        if not row:
            return None
        row_size = int_or_none(row.get("Size"))
        if declared_size is not None and row_size is not None and row_size != declared_size:
            return None
        data = s3.get_bytes(cfg.input_bucket, obj, max_bytes=cfg.max_file_bytes)
        source_sha = sha256_hex(data)
        if declared_sha and source_sha != declared_sha:
            return None
        return obj, source_sha, len(data)

    for obj in candidate_objects:
        match = matches_declared(obj)
        if match:
            return match

    if declared_sha:
        for obj, row in rows_by_key.items():
            row_size = int_or_none(row.get("Size"))
            if declared_size is not None and row_size is not None and row_size != declared_size:
                continue
            data = s3.get_bytes(cfg.input_bucket, obj, max_bytes=cfg.max_file_bytes)
            if sha256_hex(data) == declared_sha:
                return obj, declared_sha, len(data)

    detail = {
        "doc_id": doc_status.get("doc_id"),
        "source_filename": source.get("filename"),
        "sha256": declared_sha,
        "size_bytes": declared_size,
    }
    raise ValueError("cannot resolve existing staged document to eduassets-input object: %s" % json_safe(detail))


def existing_stage_status_to_submit_doc(
    s3: S3Client,
    cfg: Config,
    doc_status: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    source = doc_status.get("source") if isinstance(doc_status.get("source"), dict) else {}
    obj, source_sha, size_bytes = resolve_existing_stage_input_object(s3, cfg, doc_status)
    material_id = str(source.get("material_id") or doc_status.get("material_id") or material_id_from_sha(source_sha))
    filename = str(source.get("filename") or doc_status.get("filename") or obj.rsplit("/", 1)[-1] or "input.pdf")
    doc_id = str(doc_status.get("doc_id") or "staged_%s_%03d" % (source_sha[:16], index + 1))
    return {
        "bucket": cfg.input_bucket,
        "object": obj,
        "filename": filename,
        "source_hash": source_key_hash(cfg.input_bucket, obj),
        "material_id": material_id,
        "doc_id": doc_id,
        "source_pdf_sha256": source_sha,
        "source_pdf_size_bytes": size_bytes,
        "source_url": s3.presign_get(cfg.minio_public_endpoint, cfg.input_bucket, obj, expires=cfg.presign_expires),
        "source_url_expires_seconds": cfg.presign_expires,
    }


def existing_stage_docs_from_batch_status(
    s3: S3Client,
    cfg: Config,
    batch_status: dict[str, Any],
    input_objects: tuple[str, ...],
    material_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for idx, status in enumerate(stage_status_documents(batch_status)):
        if not isinstance(status, dict):
            continue
        doc = existing_stage_status_to_submit_doc(s3, cfg, status, idx)
        if item_matches_target_filters(doc, input_objects, material_ids):
            docs.append(doc)
    return docs


def freeze_mineru_only_result(
    s3: S3Client,
    cfg: Config,
    wrapper: WrapperClient,
    doc: dict[str, Any],
    doc_status: dict[str, Any],
    batch_status: dict[str, Any],
) -> dict[str, Any]:
    input_object = str(doc.get("object") or "")
    material_id = str(doc.get("material_id") or "")
    run_id = run_id_from_doc_status(doc_status, "MinerU result")
    result_url = doc_status.get("result_url") or f"/api/v1/mineru/results/{run_id}"
    tar_bytes = wrapper.request_bytes("GET", str(result_url), timeout=3600)
    tar_sha = sha256_hex(tar_bytes)
    pdf_bytes = s3.get_bytes(cfg.input_bucket, input_object, max_bytes=cfg.max_file_bytes)
    source_sha = sha256_hex(pdf_bytes)
    mineru_prefix = f"mineru/{material_id}/{run_id}/"
    archive_object = f"gpu-wrapper/{material_id}/{run_id}/mineru-result.tar.gz"
    source_pdf_object = f"source-pdf/{material_id}/{run_id}/source.pdf"
    objects: dict[str, Any] = {
        "archive": s3.put_bytes(cfg.archive_bucket, archive_object, tar_bytes, "application/gzip"),
        "source_pdf": s3.put_bytes(cfg.archive_bucket, source_pdf_object, pdf_bytes, "application/pdf"),
    }
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
        content_v2 = find_member(
            tf,
            lambda p: p.endswith("_content_list_v2.json") or p.endswith("content_list_v2.json"),
            True,
            "content_list_v2.json",
        )
        content = find_member(
            tf,
            lambda p: p.endswith("_content_list.json") and not p.endswith("_content_list_v2.json"),
            False,
            "content_list.json",
        )
        middle = find_member(tf, lambda p: p.endswith("_middle.json") or p.endswith("middle.json"), False, "middle.json")
        model = find_member(tf, lambda p: p.endswith("_model.json") or p.endswith("model.json"), False, "model.json")
        full_md = find_member(tf, lambda p: p.startswith("mineru/") and p.endswith(".md"), False, "full.md")
        objects["content_list_v2"] = s3.put_bytes(
            cfg.mineru_bucket, mineru_prefix + "content_list_v2.json", read_member(tf, content_v2), "application/json"
        )
        if content:
            objects["content_list"] = s3.put_bytes(
                cfg.mineru_bucket, mineru_prefix + "content_list.json", read_member(tf, content), "application/json"
            )
        if middle:
            objects["middle"] = s3.put_bytes(
                cfg.mineru_bucket, mineru_prefix + "middle.json", read_member(tf, middle), "application/json"
            )
        if model:
            objects["model"] = s3.put_bytes(
                cfg.mineru_bucket, mineru_prefix + "model.json", read_member(tf, model), "application/json"
            )
        if full_md:
            objects["full_md"] = s3.put_bytes(
                cfg.mineru_bucket, mineru_prefix + "full.md", read_member(tf, full_md), "text/markdown"
            )
        image_refs = []
        for member in tar_members(tf):
            rel = safe_tar_path(member.name)
            if "/images/" not in rel.lower():
                continue
            image_name = rel.split("/images/", 1)[-1]
            ref = s3.put_bytes(
                cfg.mineru_bucket,
                mineru_prefix + "images/" + image_name,
                read_member(tf, member),
                content_type_for(rel),
            )
            ref["source_member"] = rel
            image_refs.append(ref)
        objects["images"] = image_refs
        objects["full_tree"] = put_full_tar_tree(s3, cfg, tf, material_id, run_id)
    manifest = {
        "schema": "luceon-gpu-wrapper-mineru-only-manifest/v1",
        "status": "mineru_done_frozen",
        "material_id": material_id,
        "run_id": run_id,
        "stage_run_ids": {"mineru": run_id},
        "lineage": {
            "join_key": "material_id",
            "stage_run_ids_can_differ": True,
            "mode": "staged_mineru_freeze",
        },
        "batch_id": str(batch_status.get("batch_id") or ""),
        "doc_id": str(doc.get("doc_id") or doc_status.get("doc_id") or ""),
        "source_pdf": {
            "filename": str(doc.get("filename") or input_object.rsplit("/", 1)[-1]),
            "sha256": source_sha,
            "size_bytes": len(pdf_bytes),
            "submit_mode": "minio_presigned_url",
            "input_bucket": cfg.input_bucket,
            "input_object": input_object,
            "bucket": cfg.archive_bucket,
            "object": source_pdf_object,
        },
        "wrapper": {"batch_status": batch_status, "document_status": doc_status},
        "stage_prefixes": {
            "mineru": {
                "bucket": cfg.mineru_bucket,
                "prefix": mineru_prefix,
                "official_prefix": mineru_prefix + "official/",
            },
            "archive": {
                "bucket": cfg.archive_bucket,
                "object": archive_object,
                "extracted_prefix": f"gpu-wrapper/{material_id}/{run_id}/extracted/",
            },
        },
        "archive_sha256": tar_sha,
        "archive_size_bytes": len(tar_bytes),
        "full_tree_counts": objects.get("full_tree", {}).get("counts", {}),
        "objects": objects,
        "created_at": now_iso(),
    }
    manifest_ref = s3.put_json(cfg.mineru_bucket, mineru_prefix + "manifest.json", manifest)
    status_payload = {
        "workflow_stage": "run_staged_mineru",
        "manifest": manifest_ref,
        "wrapper_status": doc_status,
        "batch_id": str(batch_status.get("batch_id") or ""),
        "source_pdf_sha256": source_sha,
        "source_pdf_size_bytes": len(pdf_bytes),
    }
    markers = write_status_marker(s3, cfg, doc, run_id, "mineru_done_frozen", status_payload)
    cleanup = cleanup_stage_markers(s3, cfg, doc, run_id, ("mineru_submitted", "mineru_running"))
    return {
        "schema": "luceon-staged-mineru-freeze/v1",
        "status": "PASS",
        "material_id": material_id,
        "run_id": run_id,
        "manifest": manifest_ref,
        "objects": objects,
        "status_markers": {"mineru_done_frozen": markers},
        "active_marker_cleanup": cleanup,
    }


def reuse_frozen_mineru_result(
    s3: S3Client,
    cfg: Config,
    doc: dict[str, Any],
    doc_status: dict[str, Any],
    mineru_batch_id: str,
) -> dict[str, Any]:
    material_id = str(doc.get("material_id") or "")
    run_id = run_id_from_doc_status(doc_status, "Frozen MinerU reuse")
    manifest_object, manifest = latest_mineru_manifest_for_material(s3, cfg, material_id)
    source = manifest.get("source_pdf") if isinstance(manifest.get("source_pdf"), dict) else {}
    expected = {
        "status": "mineru_done_frozen",
        "material_id": material_id,
        "run_id": run_id,
        "batch_id": str(mineru_batch_id),
        "input_object": str(doc.get("object") or ""),
        "source_sha256": str(doc.get("source_pdf_sha256") or ""),
    }
    actual = {
        "status": str(manifest.get("status") or ""),
        "material_id": str(manifest.get("material_id") or ""),
        "run_id": str(manifest.get("run_id") or ""),
        "batch_id": str(manifest.get("batch_id") or ""),
        "input_object": str(source.get("input_object") or ""),
        "source_sha256": str(source.get("sha256") or ""),
    }
    mismatches = {key: {"expected": value, "actual": actual[key]} for key, value in expected.items() if value != actual[key]}
    expected_size = doc.get("source_pdf_size_bytes")
    if expected_size is not None and int(expected_size) != int(source.get("size_bytes") or -1):
        mismatches["source_size_bytes"] = {"expected": int(expected_size), "actual": source.get("size_bytes")}
    if mismatches:
        raise ValueError(f"frozen MinerU manifest identity mismatch: {json.dumps(mismatches, ensure_ascii=False)}")
    marker_object = status_marker_object(material_id, run_id, "mineru_done_frozen")
    marker = s3.get_json(cfg.input_bucket, marker_object)
    if str(marker.get("status") or "") != "mineru_done_frozen":
        raise ValueError(f"missing frozen MinerU marker: {marker_object}")
    return {
        "schema": "luceon-staged-mineru-freeze-reuse/v1",
        "status": "PASS",
        "reused": True,
        "material_id": material_id,
        "run_id": run_id,
        "batch_id": str(mineru_batch_id),
        "manifest": {"bucket": cfg.mineru_bucket, "object": manifest_object},
        "status_marker": {"bucket": cfg.input_bucket, "object": marker_object},
    }


def list_prefix_rows(s3: S3Client, bucket: str, prefix: str) -> list[dict[str, str]]:
    return sorted(
        [row for row in s3.list_objects(bucket, prefix=prefix) if str(row.get("Key") or "").startswith(prefix)],
        key=lambda row: str(row.get("Key") or ""),
    )


def package_frozen_mineru_for_popo(
    s3: S3Client,
    manifest: dict[str, Any],
    package_path: Path,
    exclude_rels: set[str] | None = None,
) -> dict[str, Any]:
    stage = manifest.get("stage_prefixes", {}).get("mineru", {})
    bucket = str(stage.get("bucket") or "")
    official_prefix = str(stage.get("official_prefix") or "")
    if not bucket or not official_prefix:
        raise ValueError("manifest stage_prefixes.mineru.official_prefix is required")
    rows = list_prefix_rows(s3, bucket, official_prefix)
    if not rows:
        raise ValueError("no frozen MinerU official objects found under " + official_prefix)
    package_path.parent.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    excluded: list[dict[str, Any]] = []
    exclude_rels = exclude_rels or set()
    with tarfile.open(package_path, "w:gz") as tf:
        for row in rows:
            obj = str(row.get("Key") or "")
            rel = obj[len(official_prefix) :]
            if not rel or rel.endswith("/"):
                continue
            if rel in exclude_rels:
                excluded.append({"object": obj, "rel": rel, "size_bytes": int(row.get("Size") or 0)})
                continue
            data = s3.get_bytes(bucket, obj)
            info = tarfile.TarInfo("mineru/" + rel)
            info.size = len(data)
            info.mtime = int(dt.datetime.now(dt.timezone.utc).timestamp())
            tf.addfile(info, io.BytesIO(data))
            total_bytes += len(data)
    package_bytes = package_path.read_bytes()
    return {
        "path": str(package_path),
        "sha256": sha256_hex(package_bytes),
        "size_bytes": len(package_bytes),
        "source_object_count": len(rows),
        "packaged_object_count": len(rows) - len(excluded),
        "excluded_object_count": len(excluded),
        "excluded_objects": excluded,
        "source_uncompressed_bytes": total_bytes,
    }


def copy_staged_popo_package_to_remote(package_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    if not args.remote_ssh_host:
        raise ValueError("--remote-ssh-host is required when Popo submit mode resolves to server_path")
    remote_dir = str(args.remote_server_path_dir or "").rstrip("/")
    if not remote_dir.startswith("/root/mineru-popo-service/"):
        raise ValueError("--remote-server-path-dir must stay under /root/mineru-popo-service")
    remote_path = remote_dir + "/" + package_path.name
    remote_extract_dir = remote_dir + "/" + package_path.name + ".extracted"
    ssh_base = [
        "ssh",
        "-p",
        str(args.remote_ssh_port),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=6",
    ]
    scp_base = [
        "scp",
        "-P",
        str(args.remote_ssh_port),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=6",
    ]
    if args.remote_ssh_key:
        ssh_key = str(Path(args.remote_ssh_key).expanduser())
        ssh_base.extend(["-i", ssh_key])
        scp_base.extend(["-i", ssh_key])

    def run_retry(cmd: list[str], attempts: int = 3) -> subprocess.CompletedProcess[str]:
        last_exc: subprocess.CalledProcessError | None = None
        for attempt in range(attempts):
            try:
                return subprocess.run(cmd, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as exc:
                last_exc = exc
                if exc.returncode != 255 or attempt + 1 >= attempts:
                    raise
                time.sleep(5 * (attempt + 1))
        raise last_exc or RuntimeError("subprocess retry failed unexpectedly")

    def remote_file_size(path: str) -> int | None:
        quoted = shlex.quote(path)
        proc = run_retry(
            ssh_base + [args.remote_ssh_host, f"if test -f -- {quoted}; then stat -c %s -- {quoted}; fi"],
            attempts=2,
        )
        out = (proc.stdout or "").strip()
        return int(out) if out else None

    run_retry(ssh_base + [args.remote_ssh_host, "mkdir -p -- " + shlex.quote(remote_dir)])
    local_size = package_path.stat().st_size
    reused_remote_package = remote_file_size(remote_path) == local_size
    if not reused_remote_package:
        run_retry(scp_base + [str(package_path), f"{args.remote_ssh_host}:{remote_path}"])
    run_retry(
        ssh_base
        + [
            args.remote_ssh_host,
            "rm -rf -- "
            + shlex.quote(remote_extract_dir)
            + " && mkdir -p -- "
            + shlex.quote(remote_extract_dir)
            + " && tar -xzf "
            + shlex.quote(remote_path)
            + " -C "
            + shlex.quote(remote_extract_dir),
        ]
    )
    return {
        "host": args.remote_ssh_host,
        "path": remote_path,
        "extracted_path": remote_extract_dir,
        "size_bytes": local_size,
        "reused_remote_package": reused_remote_package,
    }


def resolve_popo_submit_mode(package: dict[str, Any], args: argparse.Namespace) -> str:
    requested = str(args.popo_submit_mode or "auto")
    if requested != "auto":
        return requested
    max_url_bytes = int(args.popo_url_max_bytes or 0)
    if max_url_bytes > 0 and int(package.get("size_bytes") or 0) > max_url_bytes:
        return "server_path"
    return "url"


def put_popo_only_tar_tree(
    s3: S3Client,
    cfg: Config,
    tf: tarfile.TarFile,
    material_id: str,
    run_id: str,
) -> dict[str, Any]:
    counts = {"minerupopo": 0, "enhanced": 0, "metadata": 0, "logs": 0, "other": 0, "skipped_mineru": 0}
    sample_refs: dict[str, list[dict[str, Any]]] = {key: [] for key in counts}
    popo_prefix = f"minerupopo/{material_id}/{run_id}/"
    archive_prefix = f"gpu-wrapper/{material_id}/{run_id}/extracted/"
    for member in tar_members(tf):
        rel = safe_tar_path(member.name)
        if not rel:
            continue
        if rel.startswith("mineru/"):
            counts["skipped_mineru"] += 1
            if len(sample_refs["skipped_mineru"]) < 20:
                sample_refs["skipped_mineru"].append({"source_member": rel})
            continue
        data = read_member(tf, member)
        ctype = content_type_for(rel)
        if rel.startswith("minerupopo/"):
            bucket = cfg.minerupopo_bucket
            obj = popo_prefix + "official/" + rel
            group = "minerupopo"
        elif rel.startswith("enhanced/"):
            bucket = cfg.minerupopo_bucket
            obj = popo_prefix + "official/" + rel
            group = "enhanced"
        elif rel.startswith("metadata/"):
            bucket = cfg.archive_bucket
            obj = archive_prefix + rel
            group = "metadata"
        elif rel.startswith("logs/"):
            bucket = cfg.archive_bucket
            obj = archive_prefix + rel
            group = "logs"
        else:
            bucket = cfg.archive_bucket
            obj = archive_prefix + rel
            group = "other"
        ref = s3.put_bytes(bucket, obj, data, ctype)
        counts[group] += 1
        if len(sample_refs[group]) < 20:
            ref = dict(ref)
            ref["source_member"] = rel
            sample_refs[group].append(ref)
    return {
        "counts": counts,
        "sample_refs": sample_refs,
        "policy": "mineru_members_skipped_because_upstream_mineru_manifest_is_the_asset_of_record",
    }


def freeze_popo_only_result(
    s3: S3Client,
    cfg: Config,
    wrapper: WrapperClient,
    doc: dict[str, Any],
    doc_status: dict[str, Any],
    batch_status: dict[str, Any],
    mineru_manifest_object: str,
    mineru_manifest: dict[str, Any],
) -> dict[str, Any]:
    input_object = str(doc.get("object") or "")
    material_id = str(doc.get("material_id") or "")
    run_id = run_id_from_doc_status(doc_status, "Popo result")
    result_url = doc_status.get("result_url") or f"/api/v1/popo/results/{run_id}"
    tar_bytes = wrapper.request_bytes("GET", str(result_url), timeout=3600)
    tar_sha = sha256_hex(tar_bytes)
    popo_prefix = f"minerupopo/{material_id}/{run_id}/"
    archive_object = f"gpu-wrapper/{material_id}/{run_id}/popo-result.tar.gz"
    objects: dict[str, Any] = {
        "archive": s3.put_bytes(cfg.archive_bucket, archive_object, tar_bytes, "application/gzip"),
    }
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
        tree = find_member(
            tf,
            lambda p: p.endswith("enhanced/document_tree.json") or p.endswith("document_tree.json"),
            True,
            "document_tree.json",
        )
        tree_txt = find_member(
            tf,
            lambda p: p.endswith("enhanced/document_tree.txt") or p.endswith("document_tree.txt"),
            False,
            "document_tree.txt",
        )
        popo_raw = find_member(
            tf, lambda p: p.endswith("enhanced/popo_raw.json") or p.endswith("popo_raw.json"), False, "popo_raw.json"
        )
        label = find_member(tf, lambda p: "/label_normalization/" in p and p.endswith(".json"), False, "label.json")
        inference = find_member(tf, lambda p: "/inference/" in p and p.endswith(".json"), False, "inference.json")
        build_tree = find_member(tf, lambda p: "/build_tree/" in p and p.endswith(".json"), False, "build_tree.json")
        objects["document_tree"] = s3.put_bytes(
            cfg.minerupopo_bucket, popo_prefix + "document_tree.json", read_member(tf, tree), "application/json"
        )
        if tree_txt:
            objects["document_tree_txt"] = s3.put_bytes(
                cfg.minerupopo_bucket, popo_prefix + "document_tree.txt", read_member(tf, tree_txt), "text/plain"
            )
        if popo_raw:
            objects["popo_raw"] = s3.put_bytes(
                cfg.minerupopo_bucket, popo_prefix + "popo_raw.json", read_member(tf, popo_raw), "application/json"
            )
        if label:
            objects["label_normalization"] = s3.put_bytes(
                cfg.minerupopo_bucket, popo_prefix + "label_normalization.json", read_member(tf, label), "application/json"
            )
        if inference:
            objects["inference"] = s3.put_bytes(
                cfg.minerupopo_bucket, popo_prefix + "inference.json", read_member(tf, inference), "application/json"
            )
        if build_tree:
            objects["build_tree"] = s3.put_bytes(
                cfg.minerupopo_bucket, popo_prefix + "build_tree.json", read_member(tf, build_tree), "application/json"
            )
        image_refs = []
        for member in tar_members(tf):
            rel = safe_tar_path(member.name)
            if "/images/" not in rel.lower() or rel.startswith("mineru/"):
                continue
            image_name = rel.split("/images/", 1)[-1]
            ref = s3.put_bytes(
                cfg.minerupopo_bucket,
                popo_prefix + "images/" + image_name,
                read_member(tf, member),
                content_type_for(rel),
            )
            ref["source_member"] = rel
            image_refs.append(ref)
        objects["images"] = image_refs
        objects["full_tree"] = put_popo_only_tar_tree(s3, cfg, tf, material_id, run_id)
    source = mineru_manifest.get("source_pdf") if isinstance(mineru_manifest.get("source_pdf"), dict) else {}
    source_sha = str(source.get("sha256") or doc.get("source_pdf_sha256") or "")
    source_size = int(source.get("size_bytes") or doc.get("source_pdf_size_bytes") or 0)
    mineru_stage = mineru_manifest.get("stage_prefixes", {}).get("mineru", {})
    upstream_mineru_run_id = str(mineru_manifest.get("run_id") or "")
    manifest = {
        "schema": "luceon-gpu-wrapper-popo-from-frozen-mineru-manifest/v1",
        "material_id": material_id,
        "run_id": run_id,
        "stage_run_ids": {"mineru": upstream_mineru_run_id, "minerupopo": run_id},
        "lineage": {
            "join_key": "material_id",
            "stage_run_ids_can_differ": True,
            "mode": "staged_popo_from_frozen_mineru",
            "upstream_stage": "mineru",
            "upstream_manifest": {"bucket": cfg.mineru_bucket, "object": mineru_manifest_object},
        },
        "batch_id": str(batch_status.get("batch_id") or ""),
        "doc_id": str(doc.get("doc_id") or doc_status.get("doc_id") or ""),
        "source_pdf": {
            "filename": str(source.get("filename") or doc.get("filename") or input_object.rsplit("/", 1)[-1]),
            "sha256": source_sha,
            "size_bytes": source_size,
            "input_bucket": str(source.get("input_bucket") or cfg.input_bucket),
            "input_object": input_object,
        },
        "upstream_mineru": {
            "manifest": {"bucket": cfg.mineru_bucket, "object": mineru_manifest_object},
            "run_id": upstream_mineru_run_id,
            "stage_prefix": mineru_stage,
        },
        "wrapper": {"batch_status": batch_status, "document_status": doc_status},
        "stage_prefixes": {
            "minerupopo": {
                "bucket": cfg.minerupopo_bucket,
                "prefix": popo_prefix,
                "official_prefix": popo_prefix + "official/",
            },
            "archive": {
                "bucket": cfg.archive_bucket,
                "object": archive_object,
                "extracted_prefix": f"gpu-wrapper/{material_id}/{run_id}/extracted/",
            },
        },
        "archive_sha256": tar_sha,
        "archive_size_bytes": len(tar_bytes),
        "full_tree_counts": objects.get("full_tree", {}).get("counts", {}),
        "objects": objects,
        "created_at": now_iso(),
    }
    manifest_ref = s3.put_json(cfg.minerupopo_bucket, popo_prefix + "manifest.json", manifest)
    status_payload = {
        "workflow_stage": "run_staged_popo_from_frozen_mineru",
        "manifest": manifest_ref,
        "wrapper_status": doc_status,
        "batch_id": str(batch_status.get("batch_id") or ""),
        "mineru_manifest": {"bucket": cfg.mineru_bucket, "object": mineru_manifest_object},
        "upstream_mineru_run_id": upstream_mineru_run_id,
        "source_pdf_sha256": source_sha,
        "source_pdf_size_bytes": source_size,
    }
    popo_done_markers = write_status_marker(s3, cfg, doc, run_id, "popo_done_frozen", status_payload)
    done_markers = write_status_marker(s3, cfg, doc, run_id, "done", status_payload)
    cleanup = cleanup_stage_markers(s3, cfg, doc, run_id, ("popo_submitted", "popo_running"))
    return {
        "schema": "luceon-staged-popo-freeze/v1",
        "status": "PASS",
        "material_id": material_id,
        "run_id": run_id,
        "manifest": manifest_ref,
        "objects": objects,
        "status_markers": {"popo_done_frozen": popo_done_markers, "done": done_markers},
        "active_marker_cleanup": cleanup,
    }


def write_doc_error(
    s3: S3Client, cfg: Config, doc: dict[str, Any], run_id: str, doc_status: dict[str, Any], reason: str
) -> dict[str, Any]:
    refs = write_status_marker(
        s3,
        cfg,
        doc,
        run_id or str(doc.get("lock_id") or "error-" + utc_stamp()),
        "error",
        {
            "workflow_stage": "code_collect_gpu_wrapper_batch",
            "reason": reason,
            "error": json_safe(doc_status),
            "failed_stage": str(doc_status.get("failed_stage") or ""),
            "error_type": str(doc_status.get("error_type") or ""),
            "batch_id": str(doc_status.get("batch_id") or ""),
        },
    )
    refs["processing_marker_cleanup"] = cleanup_processing(s3, cfg, doc)
    return refs


def submitted_batch_markers(s3: S3Client, cfg: Config) -> list[dict[str, Any]]:
    rows = s3.list_objects(cfg.input_bucket)
    markers = []
    for row in rows:
        key = str(row.get("Key", ""))
        if key.startswith(BATCH_PREFIX) and key.endswith(".submitted.json"):
            value = s3.get_json(cfg.input_bucket, key)
            value["batch_marker_object"] = key
            markers.append(value)
    markers.sort(key=lambda x: str(x.get("created_at") or x.get("batch_id") or ""))
    return markers


def collect_batches(args: argparse.Namespace) -> dict[str, Any]:
    cfg = cfg_from_args(args)
    s3 = S3Client(cfg.minio_endpoint, cfg.minio_access_key, cfg.minio_secret_key)
    wrapper = WrapperClient(cfg.wrapper_url, cfg.wrapper_api_key)
    markers = submitted_batch_markers(s3, cfg)[: args.max_batches]
    results = []
    did_apply = False
    terminal = {"succeeded", "partial", "failed", "error", "canceled", "cancelled", "timeout"}
    if not markers:
        return {"command": "collect", "status": "IDLE", "applied": False, "batches": []}
    for marker in markers:
        batch_id = str(marker.get("batch_id") or "")
        try:
            batch_status = wrapper.request_json(
                "GET", "/api/v1/batches/" + urllib.parse.quote(batch_id, safe=""), timeout=180
            )
        except Exception as exc:
            results.append(
                {
                    "batch_id": batch_id,
                    "status": "GPU_OFFLINE",
                    "wrapper_status": "unreachable",
                    "document_count": len([d for d in marker.get("documents", []) if isinstance(d, dict)]),
                    "applied": False,
                    "error": wrapper_offline_payload(exc),
                }
            )
            continue
        state = str(batch_status.get("status") or "").lower()
        docs = [dict(d, selected=True) for d in marker.get("documents", []) if isinstance(d, dict)]
        for doc in docs:
            if doc.get("processing_marker_lock_id") and not doc.get("lock_id"):
                doc["lock_id"] = doc["processing_marker_lock_id"]
        if state not in terminal:
            results.append(
                {
                    "batch_id": batch_id,
                    "status": "WAITING",
                    "wrapper_status": state,
                    "document_count": len(docs),
                }
            )
            continue
        if not args.apply:
            results.append(
                {
                    "batch_id": batch_id,
                    "status": "DRY_RUN_TERMINAL",
                    "wrapper_status": state,
                    "document_count": len(docs),
                    "would_collect": True,
                }
            )
            continue
        did_apply = True
        by_doc = {str(d.get("doc_id") or ""): d for d in docs}
        by_sha = {str(d.get("source_pdf_sha256") or ""): d for d in docs}
        success_items = []
        failed_items = []
        seen: set[str] = set()
        statuses = batch_status.get("documents") if isinstance(batch_status.get("documents"), list) else []
        for doc_status in statuses:
            if not isinstance(doc_status, dict):
                continue
            source = doc_status.get("source") if isinstance(doc_status.get("source"), dict) else {}
            doc = by_doc.get(str(doc_status.get("doc_id") or "")) or by_sha.get(str(source.get("sha256") or ""))
            if not doc:
                continue
            seen.add(str(doc.get("doc_id") or doc.get("source_pdf_sha256") or doc.get("object")))
            doc_status = dict(doc_status)
            doc_status["batch_id"] = batch_id
            if str(doc_status.get("status") or "").lower() == "succeeded":
                success_items.append(freeze_success_doc(s3, cfg, wrapper, doc, doc_status, batch_status))
            else:
                failed_items.append(
                    {
                        "material_id": doc.get("material_id"),
                        "error_marker": write_doc_error(
                            s3,
                            cfg,
                            doc,
                            str(doc_status.get("run_id") or doc_status.get("job_id") or doc.get("lock_id") or ""),
                            doc_status,
                            "batch_document_failed",
                        ),
                    }
                )
        for doc in docs:
            key = str(doc.get("doc_id") or doc.get("source_pdf_sha256") or doc.get("object"))
            if key in seen:
                continue
            failed_items.append(
                {
                    "material_id": doc.get("material_id"),
                    "error_marker": write_doc_error(
                        s3,
                        cfg,
                        doc,
                        str(doc.get("lock_id") or ""),
                        {"batch_id": batch_id, "status": "failed", "error_type": "missing_document_status"},
                        "missing_document_status",
                    ),
                }
            )
        submitted_obj = str(marker.get("batch_marker_object") or (BATCH_PREFIX + batch_id + ".submitted.json"))
        deleted = s3.delete(cfg.input_bucket, submitted_obj)
        collected = s3.put_json(
            cfg.input_bucket,
            BATCH_PREFIX + batch_id + ".collected.json",
            {
                "schema": "luceon-wrapper-batch-collected/v1",
                "status": "collected",
                "batch_id": batch_id,
                "terminal_status": state,
                "collected_at": now_iso(),
                "success_count": len(success_items),
                "failed_count": len(failed_items),
            },
        )
        results.append(
            {
                "batch_id": batch_id,
                "status": "PASS" if len(success_items) == len(docs) else ("PARTIAL" if success_items else "FAIL"),
                "wrapper_status": state,
                "selected_count": len(docs),
                "pass_count": len(success_items),
                "failed_count": len(failed_items),
                "submitted_marker_deleted": deleted,
                "collected_marker": collected,
                "manifest_refs": [x.get("manifest") for x in success_items if isinstance(x.get("manifest"), dict)],
            }
        )
    return {"command": "collect", "applied": did_apply, "batch_count": len(results), "batches": results}


def scan_command(args: argparse.Namespace) -> dict[str, Any]:
    apply_run_mode(args, "scan")
    cfg = cfg_from_args(args)
    s3 = S3Client(cfg.minio_endpoint, cfg.minio_access_key, cfg.minio_secret_key)
    result = {"command": "scan", "applied": False, "scan": pdf_candidates(s3, cfg, args.limit, allow_active=args.allow_active)}
    if not args.include_source_urls:
        result = redact_presigned_urls(result)
    return result


def audit_command(args: argparse.Namespace) -> dict[str, Any]:
    cfg = cfg_from_args(args)
    s3 = S3Client(cfg.minio_endpoint, cfg.minio_access_key, cfg.minio_secret_key)
    return audit_input_bucket(s3, cfg, args.sample_limit)


def lineage_audit_command(args: argparse.Namespace) -> dict[str, Any]:
    cfg = cfg_from_args(args)
    s3 = S3Client(cfg.minio_endpoint, cfg.minio_access_key, cfg.minio_secret_key)
    if args.input_status_only:
        return input_status_lineage_audit(s3, cfg, args.sample_limit)
    return first_stage_lineage_audit(s3, cfg, args.sample_limit, with_sha=not args.skip_sha)


def plan_next_command(args: argparse.Namespace) -> dict[str, Any]:
    cfg = cfg_from_args(args)
    s3 = S3Client(cfg.minio_endpoint, cfg.minio_access_key, cfg.minio_secret_key)
    input_objects, material_ids = target_filters_from_args(args)
    return build_next_batch_plan(
        s3,
        cfg,
        args.limit,
        args.sort_by,
        with_sha=not args.skip_sha,
        lineage_file=args.lineage_file,
        include_error_review=args.include_error_review,
        input_status_only=args.input_status_only,
        input_objects=input_objects,
        material_ids=material_ids,
        allow_active=args.allow_active,
    )


def preflight_command(args: argparse.Namespace) -> dict[str, Any]:
    apply_run_mode(args, "preflight")
    cfg = cfg_from_args(args)

    try:
        s3 = S3Client(cfg.minio_endpoint, cfg.minio_access_key, cfg.minio_secret_key)
        input_objects, material_ids = target_filters_from_args(args)
        plan = build_next_batch_plan(
            s3,
            cfg,
            args.limit,
            args.sort_by,
            with_sha=not args.skip_sha,
            lineage_file=args.lineage_file,
            include_error_review=args.include_error_review,
            input_status_only=args.input_status_only,
            input_objects=input_objects,
            material_ids=material_ids,
            allow_active=args.allow_active,
            candidate_states={"eligible_for_submit", "gpu_done"} if args.reprocess_completed else None,
        )
    except Exception as exc:
        plan = {"status": "ERROR", "error": str(exc), "type": type(exc).__name__}

    try:
        wrapper = WrapperClient(cfg.wrapper_url, cfg.wrapper_api_key)
        health = wrapper_ready(wrapper, require_mineru=True, timeout=5)
        staged_probe = staged_api_probe(wrapper, timeout=5)
    except Exception as exc:
        health = wrapper_offline_payload(exc)
        staged_probe = {"available": False, "codes": {}, "error": str(exc), "type": type(exc).__name__}

    plan_status = str(plan.get("status") or "ERROR")
    gpu_ok = bool(health.get("ok"))
    staged_api_ok = bool(staged_probe.get("available"))
    ready = gpu_ok and staged_api_ok and plan_status == "READY"
    if plan_status == "ERROR":
        status = "PLAN_ERROR"
    elif not gpu_ok:
        status = "GPU_OFFLINE"
    elif not staged_api_ok:
        status = "STAGED_API_UNAVAILABLE"
    else:
        status = plan_status
    return {
        "command": "preflight",
        "applied": False,
        "ready": ready,
        "status": status,
        "checked_at": now_iso(),
        "limit": min(max(1, int(args.limit or MAX_BATCH_PDFS)), MAX_BATCH_PDFS),
        "gpu_ok": gpu_ok,
        "staged_api_ok": staged_api_ok,
        "plan_status": plan_status,
        "selected_count": int(plan.get("selected_count") or 0),
        "active_marker_count": int(plan.get("active_marker_count") or 0),
        "reprocess_completed": bool(args.reprocess_completed),
        "selected": plan.get("selected") or [],
        "active_marker_samples": plan.get("active_marker_samples") or [],
        "scheduler_state_counts": plan.get("scheduler_state_counts") or {},
        "health": json_safe(health),
        "staged_api_probe": json_safe(staged_probe),
        "plan": json_safe(plan),
    }


def report_command(args: argparse.Namespace) -> dict[str, Any]:
    cfg = cfg_from_args(args)
    s3 = S3Client(cfg.minio_endpoint, cfg.minio_access_key, cfg.minio_secret_key)
    return build_safe_report(s3, cfg, args.sample_limit, with_sha=not args.skip_sha)


def asset_gap_command(args: argparse.Namespace) -> dict[str, Any]:
    cfg = cfg_from_args(args)
    s3 = S3Client(cfg.minio_endpoint, cfg.minio_access_key, cfg.minio_secret_key)
    return build_pipeline_asset_gap_report(
        s3,
        cfg,
        raw_bucket=args.raw_bucket,
        clean_bucket=args.clean_bucket,
        sample_limit=args.sample_limit,
        with_sha=not args.skip_sha,
    )


def contract_note_command(args: argparse.Namespace) -> dict[str, Any]:
    cfg = cfg_from_args(args)
    s3 = S3Client(cfg.minio_endpoint, cfg.minio_access_key, cfg.minio_secret_key)
    contract = bucket_contract_audit(s3, cfg)
    note = {
        "schema": "luceon-first-stage-bucket-contract-note/v1",
        "status": "active",
        "created_at": now_iso(),
        "purpose": "Classify auxiliary first-stage objects without deleting or moving existing assets.",
        "contract": contract,
        "non_destructive": True,
    }
    object_name = args.note_object
    result = {
        "command": "contract-note",
        "applied": bool(args.apply),
        "bucket": cfg.mineru_bucket,
        "object": object_name,
        "note": note,
    }
    if args.apply:
        result["written"] = s3.put_json(cfg.mineru_bucket, object_name, note)
    return result


def staged_api_probe(wrapper: WrapperClient, timeout: int = 30) -> dict[str, Any]:
    paths = {
        "mineru_batches": "/api/v1/mineru/batches",
        "mineru_results_probe": "/api/v1/mineru/results/__probe__",
        "popo_batches": "/api/v1/popo/batches",
        "popo_results_probe": "/api/v1/popo/results/__probe__",
    }
    codes = {name: wrapper.status_code("GET", path, timeout=timeout) for name, path in paths.items()}
    available = (
        codes["mineru_batches"] in (200, 405)
        and codes["popo_batches"] in (200, 405)
        and codes["mineru_results_probe"] in (200, 404)
        and codes["popo_results_probe"] in (200, 404)
    )
    return {"available": available, "codes": codes}


def stage_status_documents(batch_status: dict[str, Any]) -> list[Any]:
    docs = batch_status.get("documents") if isinstance(batch_status.get("documents"), list) else []
    return docs


def emit_pipeline_progress(**payload: Any) -> None:
    print(
        "__LUCEON_PROGRESS__"
        + json.dumps({"at": now_iso(), **json_safe(payload)}, ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )


def wait_stage_batch(
    s3: S3Client,
    cfg: Config,
    wrapper: WrapperClient,
    stage: str,
    batch_id: str,
    docs: list[dict[str, Any]],
    run_ids_by_doc_id: dict[str, str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    started = time.monotonic()
    last_status: dict[str, Any] = {}
    poll_errors = []
    last_doc_states: dict[str, str] = {}
    while True:
        try:
            last_status = wrapper.request_json("GET", f"/api/v1/{stage}/batches/{batch_id}", timeout=60)
        except Exception as exc:
            poll_errors.append({"at": now_iso(), "type": type(exc).__name__, "error": str(exc)})
            if time.monotonic() - started > args.timeout_seconds:
                return {"wait_status": "TIMEOUT", "poll_errors": poll_errors, "last_status": last_status}
            time.sleep(args.poll_interval)
            continue
        statuses = stage_status_documents(last_status)
        all_terminal = bool(docs)
        for idx, doc in enumerate(docs):
            doc_status = dict(match_doc_status(statuses, doc, idx))
            run_id = str(doc_status.get("run_id") or doc_status.get("job_id") or run_ids_by_doc_id.get(str(doc.get("doc_id") or "")) or "")
            if run_id:
                doc_status.setdefault("run_id", run_id)
            material_id = str(doc.get("material_id") or "")
            status_value = str(doc_status.get("status") or "pending").lower()
            state_key = material_id or str(doc.get("doc_id") or idx)
            if last_doc_states.get(state_key) != status_value:
                last_doc_states[state_key] = status_value
                emit_pipeline_progress(
                    stage=stage,
                    phase="remote",
                    status=status_value,
                    material_id=material_id,
                    run_id=run_id,
                    batch_id=batch_id,
                    ordinal=idx + 1,
                    total=len(docs),
                )
            if not stage_doc_terminal(stage, doc_status):
                all_terminal = False
                if run_id:
                    write_status_marker(
                        s3,
                        cfg,
                        doc,
                        run_id,
                        f"{stage}_running",
                        {
                            "workflow_stage": f"run_staged_{stage}",
                            "batch_id": batch_id,
                            "wrapper_status": doc_status,
                        },
                    )
        if stage_batch_terminal(last_status) or all_terminal:
            return {"wait_status": "TERMINAL", "poll_errors": poll_errors, "last_status": last_status}
        if time.monotonic() - started > args.timeout_seconds:
            return {"wait_status": "TIMEOUT", "poll_errors": poll_errors, "last_status": last_status}
        time.sleep(args.poll_interval)


def write_stage_error(
    s3: S3Client,
    cfg: Config,
    doc: dict[str, Any],
    run_id: str,
    stage: str,
    batch_id: str,
    doc_status: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    markers = write_status_marker(
        s3,
        cfg,
        doc,
        run_id or f"{stage}-error-{utc_stamp()}",
        f"{stage}_error",
        {
            "workflow_stage": f"run_staged_{stage}",
            "reason": reason,
            "batch_id": batch_id,
            "wrapper_status": json_safe(doc_status),
        },
    )
    cleanup = cleanup_stage_markers(s3, cfg, doc, run_id, (f"{stage}_submitted", f"{stage}_running")) if run_id else {}
    return {
        "material_id": str(doc.get("material_id") or ""),
        "run_id": run_id,
        "stage": stage,
        "reason": reason,
        "markers": markers,
        "active_marker_cleanup": cleanup,
    }


def write_freeze_error(
    s3: S3Client,
    cfg: Config,
    doc: dict[str, Any],
    run_id: str,
    stage: str,
    batch_id: str,
    doc_status: dict[str, Any],
    exc: Exception,
) -> dict[str, Any]:
    reason = f"{stage}_freeze_failed"
    marker_run_id = run_id or f"{stage}-freeze-error-{utc_stamp()}"
    marker_value = {
        "workflow_stage": f"run_staged_{stage}_freeze",
        "reason": reason,
        "batch_id": batch_id,
        "wrapper_status": json_safe(doc_status),
        "error": {"type": type(exc).__name__, "message": str(exc)},
    }
    try:
        markers = write_status_marker(s3, cfg, doc, marker_run_id, f"{stage}_freeze_error", marker_value)
    except Exception as marker_exc:
        markers = {"error": {"type": type(marker_exc).__name__, "message": str(marker_exc)}}
    try:
        cleanup = (
            cleanup_stage_markers(s3, cfg, doc, run_id, (f"{stage}_submitted", f"{stage}_running"))
            if run_id
            else {}
        )
    except Exception as cleanup_exc:
        cleanup = {"error": {"type": type(cleanup_exc).__name__, "message": str(cleanup_exc)}}
    return {
        "material_id": str(doc.get("material_id") or ""),
        "stage": stage,
        "reason": reason,
        "run_id": run_id,
        "error": {"type": type(exc).__name__, "message": str(exc)},
        "markers": markers,
        "active_marker_cleanup": cleanup,
    }


def staged_completion_status(result: dict[str, Any]) -> str:
    selected_count = int(result.get("selected_count") or 0)
    mineru_freezes = len(result.get("mineru_freezes") or [])
    mineru_errors = len(result.get("mineru_errors") or [])
    popo = result.get("popo") if isinstance(result.get("popo"), dict) else {}
    popo_freezes = len(popo.get("freezes") or [])
    popo_errors = len(popo.get("errors") or [])
    if (
        selected_count > 0
        and mineru_freezes == selected_count
        and popo_freezes == selected_count
        and mineru_errors == 0
        and popo_errors == 0
    ):
        return "DONE"
    if mineru_freezes or popo_freezes:
        return "PARTIAL"
    return "FAILED"


def cli_result_exit_code(result: dict[str, Any]) -> int:
    status = str(result.get("status") or "").upper()
    if status in {"ERROR", "FAILED", "PARTIAL"}:
        return 2
    if status.startswith("BLOCKED") or status.endswith("INCOMPLETE") or status.endswith("NO_FREEZES"):
        return 3
    return 0


def run_staged_command(args: argparse.Namespace) -> dict[str, Any]:
    apply_run_mode(args, "run-staged")
    cfg = cfg_from_args(args)
    s3 = S3Client(cfg.minio_endpoint, cfg.minio_access_key, cfg.minio_secret_key)
    input_objects, material_ids = target_filters_from_args(args)
    if args.reuse_frozen_mineru and not args.existing_mineru_batch_id:
        return {
            "command": "run-staged",
            "applied": False,
            "status": "BLOCKED_REUSE_REQUIRES_EXISTING_MINERU_BATCH",
            "reason": "--reuse-frozen-mineru requires --existing-mineru-batch-id",
        }
    if args.apply and args.input_status_only and not (input_objects or material_ids or args.existing_mineru_batch_id or args.existing_popo_batch_id):
        return {
            "command": "run-staged",
            "applied": False,
            "status": "BLOCKED_UNTARGETED_INPUT_STATUS_ONLY_APPLY",
            "reason": "--input-status-only apply cannot prove content-level duplicates; pass --input-object/--material-id or run full SHA planning",
        }
    wrapper: WrapperClient | None = None
    health: dict[str, Any] = {}
    staged_probe: dict[str, Any] = {}
    mineru_submit: dict[str, Any] | None = None
    existing_popo_submit: dict[str, Any] | None = None
    if args.existing_mineru_batch_id:
        wrapper = WrapperClient(cfg.wrapper_url, cfg.wrapper_api_key)
        try:
            health = wrapper_ready(wrapper, require_mineru=True)
            staged_probe = staged_api_probe(wrapper)
        except Exception as exc:
            return {
                "command": "run-staged",
                "applied": False,
                "status": "GPU_OFFLINE",
                "health": wrapper_offline_payload(exc),
            }
        if not health["ok"]:
            return {"command": "run-staged", "applied": False, "status": "GPU_OFFLINE", "health": health}
        if not staged_probe["available"]:
            return {
                "command": "run-staged",
                "applied": False,
                "status": "STAGED_API_UNAVAILABLE",
                "health": health,
                "staged_api_probe": staged_probe,
            }
        mineru_submit = wrapper.request_json("GET", f"/api/v1/mineru/batches/{args.existing_mineru_batch_id}", timeout=60)
        docs = existing_stage_docs_from_batch_status(s3, cfg, mineru_submit, input_objects, material_ids)
        plan = {
            "command": "run-staged",
            "status": "READY" if docs else "TARGET_NOT_FOUND_IN_EXISTING_MINERU_BATCH",
            "mode": "existing_mineru_recovery",
            "existing_mineru_batch_id": str(args.existing_mineru_batch_id),
            "selected_count": len(docs),
            "target_filters": {"input_objects": list(input_objects), "material_ids": list(material_ids)},
        }
    else:
        if args.existing_popo_batch_id:
            wrapper = WrapperClient(cfg.wrapper_url, cfg.wrapper_api_key)
            try:
                health = wrapper_ready(wrapper, require_mineru=False)
                staged_probe = staged_api_probe(wrapper)
                existing_popo_submit = wrapper.request_json(
                    "GET", f"/api/v1/popo/batches/{args.existing_popo_batch_id}", timeout=60
                )
            except Exception as exc:
                return {
                    "command": "run-staged",
                    "applied": False,
                    "status": "GPU_OFFLINE",
                    "health": wrapper_offline_payload(exc),
                }
            if not health["ok"]:
                return {"command": "run-staged", "applied": False, "status": "GPU_OFFLINE", "health": health}
            if not staged_probe["available"]:
                return {
                    "command": "run-staged",
                    "applied": False,
                    "status": "STAGED_API_UNAVAILABLE",
                    "health": health,
                    "staged_api_probe": staged_probe,
                }
        candidate_states = {"eligible_for_submit"}
        if args.existing_popo_batch_id:
            candidate_states = {"mineru_only_resume_popo"}
        elif args.reprocess_completed:
            candidate_states.add("gpu_done")
        plan = build_next_batch_plan(
            s3,
            cfg,
            args.limit,
            args.sort_by,
            with_sha=not args.skip_sha,
            lineage_file=args.lineage_file,
            include_error_review=args.include_error_review,
            input_status_only=args.input_status_only,
            input_objects=input_objects,
            material_ids=material_ids,
            allow_active=args.allow_active or bool(args.existing_popo_batch_id),
            candidate_states=candidate_states,
        )
        selected = plan.get("selected") or []
        docs = [lineage_item_to_submit_doc(s3, cfg, item, idx) for idx, item in enumerate(selected)]
    if plan.get("status") != "READY":
        return {"command": "run-staged", "applied": False, "status": plan.get("status"), "plan": plan}
    dry_docs = [
        {k: d[k] for k in ("object", "material_id", "source_pdf_size_bytes", "source_pdf_sha256")}
        for d in docs
    ]
    if not args.apply:
        return {
            "command": "run-staged",
            "applied": False,
            "status": "DRY_RUN",
            "would_run": (
                "reuse_frozen_mineru_then_popo_then_freeze_popo"
                if args.reuse_frozen_mineru
                else "mineru_then_freeze_per_pdf_then_popo_from_frozen_mineru_then_freeze_popo"
            ),
            "selected_count": len(docs),
            "docs": dry_docs,
            "health": health,
            "staged_api_probe": staged_probe,
            "plan_summary": {
                "scheduler_state_counts": plan.get("scheduler_state_counts", {}),
                "marker_reconciliation_summary": plan.get("marker_reconciliation_summary", {}),
            },
        }
    if not args.wait:
        return {
            "command": "run-staged",
            "applied": False,
            "status": "BLOCKED_REQUIRES_WAIT",
            "reason": "staged apply currently requires --wait so MinerU can be frozen before Popo is submitted",
            "selected_count": len(docs),
            "docs": dry_docs,
        }
    if wrapper is None:
        wrapper = WrapperClient(cfg.wrapper_url, cfg.wrapper_api_key)
        try:
            health = wrapper_ready(wrapper, require_mineru=not bool(args.existing_popo_batch_id))
            staged_probe = staged_api_probe(wrapper)
        except Exception as exc:
            return {
                "command": "run-staged",
                "applied": False,
                "status": "GPU_OFFLINE",
                "health": wrapper_offline_payload(exc),
            }
        if not health["ok"]:
            return {"command": "run-staged", "applied": False, "status": "GPU_OFFLINE", "health": health}
        if not staged_probe["available"]:
            return {
                "command": "run-staged",
                "applied": False,
                "status": "STAGED_API_UNAVAILABLE",
                "health": health,
                "staged_api_probe": staged_probe,
            }

    if args.existing_popo_batch_id:
        popo_batch_id = str(args.existing_popo_batch_id)
        popo_submit = existing_popo_submit or wrapper.request_json(
            "GET", f"/api/v1/popo/batches/{popo_batch_id}", timeout=60
        )
        popo_submit_statuses = stage_status_documents(popo_submit)
        popo_docs: list[dict[str, Any]] = []
        popo_run_ids: dict[str, str] = {}
        popo_submitted_markers = []
        for idx, doc in enumerate(docs):
            doc = dict(doc)
            doc_status = dict(match_doc_status(popo_submit_statuses, doc, idx))
            status_doc_id = str(doc_status.get("doc_id") or "")
            if status_doc_id:
                doc["doc_id"] = status_doc_id
            run_id = run_id_from_doc_status(doc_status, "Existing Popo batch")
            doc["run_id"] = run_id
            popo_run_ids[str(doc["doc_id"])] = run_id
            manifest_object, mineru_manifest = latest_mineru_manifest_for_material(s3, cfg, str(doc["material_id"]))
            source = doc_status.get("source") if isinstance(doc_status.get("source"), dict) else {}
            mineru_result = source.get("mineru_result") if isinstance(source.get("mineru_result"), dict) else {}
            popo_docs.append(
                {
                    "doc": doc,
                    "mineru_manifest_object": manifest_object,
                    "mineru_manifest": mineru_manifest,
                    "package": {},
                    "package_ref": json_safe(mineru_result),
                }
            )
            popo_submitted_markers.append(
                write_status_marker(
                    s3,
                    cfg,
                    doc,
                    run_id,
                    "popo_submitted",
                    {
                        "workflow_stage": "run_staged_existing_popo_recovery",
                        "batch_id": popo_batch_id,
                        "existing_popo_batch_id": popo_batch_id,
                        "mineru_manifest": {"bucket": cfg.mineru_bucket, "object": manifest_object},
                        "submitted_at": now_iso(),
                    },
                )
            )
        popo_wait = wait_stage_batch(
            s3,
            cfg,
            wrapper,
            "popo",
            popo_batch_id,
            [row["doc"] for row in popo_docs],
            popo_run_ids,
            args,
        )
        popo_result: dict[str, Any] = {
            "batch_id": popo_batch_id,
            "existing_popo_batch_id": popo_batch_id,
            "package_refs": [],
            "submitted_markers": popo_submitted_markers,
            "wait": popo_wait,
            "freezes": [],
            "errors": [],
        }
        if popo_wait.get("wait_status") == "TERMINAL":
            popo_final_status = popo_wait.get("last_status") if isinstance(popo_wait.get("last_status"), dict) else {}
            popo_final_docs = stage_status_documents(popo_final_status)
            for idx, row in enumerate(popo_docs):
                doc = row["doc"]
                doc_status = dict(match_doc_status(popo_final_docs, doc, idx))
                run_id = str(doc_status.get("run_id") or doc_status.get("job_id") or doc.get("run_id") or "")
                if run_id:
                    doc_status.setdefault("run_id", run_id)
                if stage_doc_success("popo", doc_status):
                    popo_result["freezes"].append(
                        freeze_popo_only_result(
                            s3,
                            cfg,
                            wrapper,
                            doc,
                            doc_status,
                            popo_final_status,
                            row["mineru_manifest_object"],
                            row["mineru_manifest"],
                        )
                    )
                else:
                    popo_result["errors"].append(
                        write_stage_error(s3, cfg, doc, run_id, "popo", popo_batch_id, doc_status, "popo_stage_failed")
                    )
        else:
            for row in popo_docs:
                doc = row["doc"]
                popo_result["errors"].append(
                    {
                        "material_id": str(doc.get("material_id") or ""),
                        "input_object": str(doc.get("input_object") or doc.get("object") or ""),
                        "stage": "popo",
                        "reason": "popo_wait_timeout",
                        "run_id": str(doc.get("run_id") or ""),
                        "error": {"message": "Popo batch remained non-terminal before the local wait deadline"},
                    }
                )
        result: dict[str, Any] = {
            "command": "run-staged",
            "applied": True,
            "status": "DONE" if len(popo_result.get("freezes") or []) == len(popo_docs) else "PARTIAL",
            "selected_count": len(docs),
            "docs": dry_docs,
            "health": health,
            "staged_api_probe": staged_probe,
            "mineru_batch_id": str(args.existing_mineru_batch_id or ""),
            "existing_mineru_batch_id": str(args.existing_mineru_batch_id or ""),
            "mineru_wait": {"wait_status": "SKIPPED_EXISTING_POPO"},
            "mineru_freezes": [],
            "mineru_errors": [],
            "popo": popo_result,
        }
        return redact_presigned_urls(result)

    if args.existing_mineru_batch_id:
        mineru_batch_id = str(args.existing_mineru_batch_id)
        if mineru_submit is None:
            mineru_submit = wrapper.request_json("GET", f"/api/v1/mineru/batches/{mineru_batch_id}", timeout=60)
    else:
        mineru_client_batch_id = args.client_batch_id or "staged_mineru_%s" % utc_stamp()
        mineru_payload_docs = [
            {
                "doc_id": d["doc_id"],
                "lang": cfg.lang,
                "source": {
                    "type": "url",
                    "url": d["source_url"],
                    "filename": d["filename"],
                    "sha256": d["source_pdf_sha256"],
                    "size_bytes": d["source_pdf_size_bytes"],
                },
            }
            for d in docs
        ]
        mineru_submit = wrapper.request_json(
            "POST",
            "/api/v1/mineru/batches",
            payload={"batch_id": mineru_client_batch_id, "lang": cfg.lang, "documents": mineru_payload_docs},
            timeout=600,
        )
        mineru_batch_id = str(mineru_submit.get("batch_id") or mineru_client_batch_id)
    mineru_submit_statuses = stage_status_documents(mineru_submit)
    mineru_run_ids: dict[str, str] = {}
    mineru_submitted_markers = []
    for idx, doc in enumerate(docs):
        doc_status = dict(match_doc_status(mineru_submit_statuses, doc, idx))
        run_id = run_id_from_doc_status(doc_status, "MinerU existing batch" if args.existing_mineru_batch_id else "MinerU submit")
        doc["run_id"] = run_id
        mineru_run_ids[str(doc["doc_id"])] = run_id
        if not args.reuse_frozen_mineru:
            mineru_submitted_markers.append(
                write_status_marker(
                    s3,
                    cfg,
                    doc,
                    run_id,
                    "mineru_submitted",
                    {
                        "workflow_stage": "run_staged_mineru",
                        "batch_id": mineru_batch_id,
                        "wrapper_status": doc_status,
                        "submitted_at": now_iso(),
                    },
                )
            )

    mineru_wait = wait_stage_batch(s3, cfg, wrapper, "mineru", mineru_batch_id, docs, mineru_run_ids, args)
    result: dict[str, Any] = {
        "command": "run-staged",
        "applied": True,
        "status": "MINERU_SUBMITTED",
        "selected_count": len(docs),
        "docs": dry_docs,
        "health": health,
        "staged_api_probe": staged_probe,
        "mineru_batch_id": mineru_batch_id,
        "existing_mineru_batch_id": str(args.existing_mineru_batch_id or ""),
        "mineru_submitted_markers": mineru_submitted_markers,
        "mineru_wait": mineru_wait,
        "mineru_freezes": [],
        "mineru_errors": [],
        "popo": {},
    }
    if mineru_wait.get("wait_status") != "TERMINAL":
        result["status"] = "MINERU_WAIT_INCOMPLETE"
        return redact_presigned_urls(result)

    mineru_final_status = mineru_wait.get("last_status") if isinstance(mineru_wait.get("last_status"), dict) else {}
    mineru_final_docs = stage_status_documents(mineru_final_status)
    frozen_docs: list[dict[str, Any]] = []
    for idx, doc in enumerate(docs):
        doc_status = dict(match_doc_status(mineru_final_docs, doc, idx))
        run_id = str(doc_status.get("run_id") or doc_status.get("job_id") or doc.get("run_id") or "")
        if run_id:
            doc_status.setdefault("run_id", run_id)
        if stage_doc_success("mineru", doc_status):
            try:
                emit_pipeline_progress(stage="mineru", phase="freeze", status="started", material_id=doc.get("material_id"), run_id=run_id, batch_id=mineru_batch_id, ordinal=idx + 1, total=len(docs))
                freeze_result = (
                    reuse_frozen_mineru_result(s3, cfg, doc, doc_status, mineru_batch_id)
                    if args.reuse_frozen_mineru
                    else freeze_mineru_only_result(s3, cfg, wrapper, doc, doc_status, mineru_final_status)
                )
            except Exception as exc:
                result["mineru_errors"].append(
                    write_freeze_error(s3, cfg, doc, run_id, "mineru", mineru_batch_id, doc_status, exc)
                )
                continue
            result["mineru_freezes"].append(freeze_result)
            emit_pipeline_progress(stage="mineru", phase="freeze", status="frozen", material_id=doc.get("material_id"), run_id=run_id, batch_id=mineru_batch_id, ordinal=idx + 1, total=len(docs), manifest=freeze_result.get("manifest"))
            frozen_docs.append({"doc": doc, "freeze": freeze_result})
        else:
            result["mineru_errors"].append(
                write_stage_error(s3, cfg, doc, run_id, "mineru", mineru_batch_id, doc_status, "mineru_stage_failed")
            )

    if not frozen_docs:
        result["status"] = "MINERU_DONE_NO_FREEZES"
        return redact_presigned_urls(result)
    if args.mineru_only:
        result["status"] = "MINERU_DONE_FROZEN"
        return redact_presigned_urls(result)

    popo_client_batch_id = args.popo_client_batch_id or "staged_popo_%s" % utc_stamp()
    emit_pipeline_progress(stage="popo", phase="model_switch", status="loading", ordinal=1, total=len(frozen_docs))
    popo_docs: list[dict[str, Any]] = []
    popo_prepare_errors: list[dict[str, Any]] = []
    package_refs = []
    if args.existing_popo_batch_id:
        for idx, row in enumerate(frozen_docs):
            doc = dict(row["doc"])
            freeze_result = row["freeze"]
            manifest_ref = freeze_result.get("manifest") if isinstance(freeze_result.get("manifest"), dict) else {}
            manifest_object = str(manifest_ref.get("object") or "")
            mineru_manifest = s3.get_json(cfg.mineru_bucket, manifest_object)
            material_id = str(mineru_manifest.get("material_id") or doc.get("material_id") or "")
            popo_doc_id = "popo_%s_%03d" % (material_id.replace("pdf-", "")[:16], idx + 1)
            doc["doc_id"] = popo_doc_id
            popo_docs.append(
                {
                    "doc": doc,
                    "mineru_manifest_object": manifest_object,
                    "mineru_manifest": mineru_manifest,
                    "package": {},
                    "package_ref": {},
                }
            )
        popo_batch_id = str(args.existing_popo_batch_id)
        popo_submit = wrapper.request_json("GET", f"/api/v1/popo/batches/{popo_batch_id}", timeout=60)
    else:
        popo_payload_docs = []
        package_root = Path(args.package_dir).expanduser()
        package_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="staged-popo-", dir=str(package_root)) as tmpdir:
            for idx, row in enumerate(frozen_docs):
                doc = dict(row["doc"])
                freeze_result = row["freeze"]
                manifest_ref = freeze_result.get("manifest") if isinstance(freeze_result.get("manifest"), dict) else {}
                manifest_object = str(manifest_ref.get("object") or "")
                mineru_manifest = s3.get_json(cfg.mineru_bucket, manifest_object)
                mineru_run_id = str(mineru_manifest.get("run_id") or freeze_result.get("run_id") or "")
                material_id = str(mineru_manifest.get("material_id") or doc.get("material_id") or "")
                package_object = f"{args.package_prefix.rstrip('/')}/{material_id}/{mineru_run_id}/mineru_result.tar.gz"
                package_path = Path(tmpdir) / f"{material_id}-{mineru_run_id}.mineru.tar.gz"
                package = package_frozen_mineru_for_popo(
                    s3,
                    mineru_manifest,
                    package_path,
                    set(args.exclude_mineru_rel or []),
                )
                package_bytes = package_path.read_bytes()
                package_ref = s3.put_bytes(cfg.mineru_bucket, package_object, package_bytes, "application/gzip")
                package_url = s3.presign_get(
                    cfg.minio_public_endpoint, cfg.mineru_bucket, package_object, expires=cfg.presign_expires
                )
                submit_mode = resolve_popo_submit_mode(package, args)
                remote_package = None
                if submit_mode == "server_path":
                    try:
                        remote_package = copy_staged_popo_package_to_remote(package_path, args)
                    except Exception as exc:
                        popo_prepare_errors.append(
                            {
                                "material_id": material_id,
                                "input_object": str(doc.get("input_object") or ""),
                                "stage": "popo",
                                "reason": "popo_submit_preparation_failed",
                                "run_id": mineru_run_id,
                                "error": {"type": type(exc).__name__, "message": str(exc)},
                            }
                        )
                        continue
                    mineru_result = {
                        "type": "server_path",
                        "path": remote_package["extracted_path"],
                        "filename": Path(package_object).name,
                        "sha256": package["sha256"],
                        "size_bytes": package["size_bytes"],
                    }
                else:
                    mineru_result = {
                        "type": "url",
                        "url": package_url,
                        "filename": Path(package_object).name,
                        "sha256": package["sha256"],
                        "size_bytes": package["size_bytes"],
                    }
                popo_doc_id = "popo_%s_%03d" % (material_id.replace("pdf-", "")[:16], idx + 1)
                doc["doc_id"] = popo_doc_id
                popo_docs.append(
                    {
                        "doc": doc,
                        "mineru_manifest_object": manifest_object,
                        "mineru_manifest": mineru_manifest,
                        "package": package,
                        "package_ref": package_ref,
                        "popo_submit_mode": submit_mode,
                        "remote_package": remote_package,
                    }
                )
                package_refs.append(package_ref)
                popo_payload_docs.append(
                    {
                        "doc_id": popo_doc_id,
                        "filename": doc["filename"],
                        "lang": cfg.lang,
                        "mineru_result": mineru_result,
                    }
                )
        if not popo_payload_docs:
            result["popo"] = {
                "batch_id": "",
                "existing_popo_batch_id": "",
                "package_refs": package_refs,
                "submitted_markers": [],
                "wait": {"wait_status": "NOT_SUBMITTED"},
                "freezes": [],
                "errors": popo_prepare_errors,
            }
            result["status"] = staged_completion_status(result)
            return redact_presigned_urls(result)
        try:
            popo_submit = wrapper.request_json(
                "POST",
                "/api/v1/popo/batches",
                payload={"batch_id": popo_client_batch_id, "lang": cfg.lang, "documents": popo_payload_docs},
                timeout=600,
            )
        except Exception as exc:
            for row in popo_docs:
                doc = row["doc"]
                popo_prepare_errors.append(
                    {
                        "material_id": str(doc.get("material_id") or ""),
                        "input_object": str(doc.get("input_object") or ""),
                        "stage": "popo",
                        "reason": "popo_submit_failed",
                        "run_id": "",
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                    }
                )
            result["popo"] = {
                "batch_id": "",
                "existing_popo_batch_id": "",
                "package_refs": package_refs,
                "submitted_markers": [],
                "wait": {"wait_status": "SUBMIT_FAILED"},
                "freezes": [],
                "errors": popo_prepare_errors,
            }
            result["status"] = staged_completion_status(result)
            return redact_presigned_urls(result)
        popo_batch_id = str(popo_submit.get("batch_id") or popo_client_batch_id)
        emit_pipeline_progress(stage="popo", phase="model_switch", status="ready", batch_id=popo_batch_id, ordinal=1, total=len(frozen_docs))
    popo_submit_statuses = stage_status_documents(popo_submit)
    popo_run_ids: dict[str, str] = {}
    popo_submitted_markers = []
    for idx, row in enumerate(popo_docs):
        doc = row["doc"]
        doc_status = dict(match_doc_status(popo_submit_statuses, doc, idx))
        run_id = run_id_from_doc_status(doc_status, "Popo submit")
        doc["run_id"] = run_id
        popo_run_ids[str(doc["doc_id"])] = run_id
        popo_submitted_markers.append(
            write_status_marker(
                s3,
                cfg,
                doc,
                run_id,
                "popo_submitted",
                {
                    "workflow_stage": "run_staged_popo_from_frozen_mineru",
                    "batch_id": popo_batch_id,
                    "existing_popo_batch_id": str(args.existing_popo_batch_id or ""),
                    "mineru_manifest": {"bucket": cfg.mineru_bucket, "object": row["mineru_manifest_object"]},
                    "mineru_package": row["package_ref"],
                    "popo_submit_mode": row.get("popo_submit_mode"),
                    "remote_package": row.get("remote_package"),
                    "submitted_at": now_iso(),
                },
            )
        )

    popo_wait = wait_stage_batch(
        s3,
        cfg,
        wrapper,
        "popo",
        popo_batch_id,
        [row["doc"] for row in popo_docs],
        popo_run_ids,
        args,
    )
    popo_result: dict[str, Any] = {
        "batch_id": popo_batch_id,
        "existing_popo_batch_id": str(args.existing_popo_batch_id or ""),
        "package_refs": package_refs,
        "submitted_markers": popo_submitted_markers,
        "wait": popo_wait,
        "freezes": [],
        "errors": list(popo_prepare_errors),
    }
    if popo_wait.get("wait_status") == "TERMINAL":
        popo_final_status = popo_wait.get("last_status") if isinstance(popo_wait.get("last_status"), dict) else {}
        popo_final_docs = stage_status_documents(popo_final_status)
        for idx, row in enumerate(popo_docs):
            doc = row["doc"]
            doc_status = dict(match_doc_status(popo_final_docs, doc, idx))
            run_id = str(doc_status.get("run_id") or doc_status.get("job_id") or doc.get("run_id") or "")
            if run_id:
                doc_status.setdefault("run_id", run_id)
            if stage_doc_success("popo", doc_status):
                try:
                    emit_pipeline_progress(stage="popo", phase="freeze", status="started", material_id=doc.get("material_id"), run_id=run_id, batch_id=popo_batch_id, ordinal=idx + 1, total=len(popo_docs))
                    freeze_result = freeze_popo_only_result(
                        s3,
                        cfg,
                        wrapper,
                        doc,
                        doc_status,
                        popo_final_status,
                        row["mineru_manifest_object"],
                        row["mineru_manifest"],
                    )
                except Exception as exc:
                    popo_result["errors"].append(
                        write_freeze_error(s3, cfg, doc, run_id, "popo", popo_batch_id, doc_status, exc)
                    )
                    continue
                popo_result["freezes"].append(freeze_result)
                emit_pipeline_progress(stage="popo", phase="freeze", status="frozen", material_id=doc.get("material_id"), run_id=run_id, batch_id=popo_batch_id, ordinal=idx + 1, total=len(popo_docs), manifest=freeze_result.get("manifest"))
            else:
                popo_result["errors"].append(
                    write_stage_error(s3, cfg, doc, run_id, "popo", popo_batch_id, doc_status, "popo_stage_failed")
                )
    else:
        for row in popo_docs:
            doc = row["doc"]
            popo_result["errors"].append(
                {
                    "material_id": str(doc.get("material_id") or ""),
                    "input_object": str(doc.get("input_object") or doc.get("object") or ""),
                    "stage": "popo",
                    "reason": "popo_wait_timeout",
                    "run_id": str(doc.get("run_id") or ""),
                    "error": {"message": "Popo batch remained non-terminal before the local wait deadline"},
                }
            )
    result["popo"] = popo_result
    result["status"] = staged_completion_status(result)
    return redact_presigned_urls(result)


def wrapper_capabilities_command(args: argparse.Namespace) -> dict[str, Any]:
    cfg = cfg_from_args(args)
    wrapper = WrapperClient(cfg.wrapper_url, cfg.wrapper_api_key)
    health = wrapper.health()
    legacy = {
        "/api/v1/batches": wrapper.status_code("GET", "/api/v1/batches"),
        "/api/v1/jobs": wrapper.status_code("GET", "/api/v1/jobs"),
    }
    staged_paths = [
        "/api/v1/mineru/batches",
        "/api/v1/mineru/results/__probe__",
        "/api/v1/popo/batches",
        "/api/v1/popo/results/__probe__",
    ]
    staged = {path: wrapper.status_code("GET", path) for path in staged_paths}
    staged_available = (
        staged.get("/api/v1/mineru/batches") == 200
        and staged.get("/api/v1/popo/batches") == 200
        and staged.get("/api/v1/mineru/results/__probe__") in (200, 404)
        and staged.get("/api/v1/popo/results/__probe__") in (200, 404)
    )
    return {
        "command": "wrapper-capabilities",
        "health": health,
        "legacy_batch_api": legacy,
        "staged_api_probe": staged,
        "staged_api_available": staged_available,
        "conclusion": (
            "staged_wrapper_available"
            if staged_available
            else "legacy_monolithic_wrapper_only; true mineru_done_frozen -> popo resume requires wrapper API work"
        ),
    }


def daemon_command(args: argparse.Namespace) -> dict[str, Any]:
    apply_run_mode(args, "daemon")
    summaries = []
    for idx in range(args.cycles):
        collect_ns = argparse.Namespace(**vars(args))
        collect_ns.max_batches = args.max_batches
        summaries.append(collect_batches(collect_ns))
        submit_ns = argparse.Namespace(**vars(args))
        summaries.append(submit_batch(submit_ns))
        if idx + 1 < args.cycles:
            time.sleep(args.interval)
    return {"command": "daemon", "cycles": args.cycles, "summaries": summaries}


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dotenv", default="", help="Optional dotenv file parsed without executing shell code.")
    parser.add_argument("--minio-endpoint", default="")
    parser.add_argument("--minio-public-endpoint", default="")
    parser.add_argument("--minio-access-key", default="")
    parser.add_argument("--minio-secret-key", default="")
    parser.add_argument("--wrapper-url", default="")
    parser.add_argument("--wrapper-api-key", default="")
    parser.add_argument("--input-bucket", default="eduassets-input")
    parser.add_argument("--mineru-bucket", default="eduassets-mineru")
    parser.add_argument("--minerupopo-bucket", default="eduassets-minerupopo")
    parser.add_argument("--archive-bucket", default="eduassets-parsed")
    parser.add_argument("--lang", default=DEFAULT_LANG)
    parser.add_argument("--max-file-bytes", type=int, default=MAX_FILE_BYTES)
    parser.add_argument("--presign-expires", type=int, default=86400)
    parser.add_argument("--mode", choices=["single", "batch", "full"], default="batch")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-active", action="store_true")
    parser.add_argument("--out", default="")


def add_target_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input-object",
        action="append",
        default=[],
        help="Limit planning/recovery to this exact eduassets-input object. Repeat for multiple objects.",
    )
    parser.add_argument(
        "--material-id",
        action="append",
        default=[],
        help="Limit planning/recovery to this exact material_id, for example pdf-<sha16>. Repeat for multiple materials.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("scan", help="Preview eligible PDFs without submitting.")
    add_common(p)
    p.add_argument("--limit", type=int, default=MAX_BATCH_PDFS)
    p.add_argument("--include-source-urls", action="store_true", help="Include presigned source URLs in scan output.")
    p.set_defaults(func=scan_command)
    p = sub.add_parser("audit", help="Count processed, active, and unprocessed input PDFs.")
    add_common(p)
    p.add_argument("--sample-limit", type=int, default=20)
    p.set_defaults(func=audit_command)
    p = sub.add_parser("lineage-audit", help="Read-only first-stage lineage audit across input, MinerU, and MinerU-Popo.")
    add_common(p)
    p.add_argument("--sample-limit", type=int, default=20)
    p.add_argument("--skip-sha", action="store_true", help="Do not stream input PDFs to compute content SHA/duplicate groups.")
    p.add_argument(
        "--input-status-only",
        action="store_true",
        help="Use eduassets-input status markers only; faster for monitoring and explicit error retry review.",
    )
    p.set_defaults(func=lineage_audit_command)
    p = sub.add_parser("plan-next", help="Sanitized read-only next batch planner. Never includes presigned URLs.")
    add_common(p)
    p.add_argument("--limit", type=int, default=MAX_BATCH_PDFS)
    p.add_argument("--sort-by", choices=["smallest", "largest", "name"], default="smallest")
    p.add_argument("--skip-sha", action="store_true", help="Skip content SHA scan; faster but less duplicate-aware.")
    p.add_argument("--lineage-file", default="", help="Reuse a previously generated lineage-audit JSON instead of scanning MinIO again.")
    p.add_argument("--include-error-review", action="store_true", help="Explicitly include error_review items for a controlled retry batch.")
    p.add_argument(
        "--input-status-only",
        action="store_true",
        help="Use only eduassets-input status markers for fast planning; intended for error retry planning, not completion audit.",
    )
    add_target_filters(p)
    p.set_defaults(func=plan_next_command)
    p = sub.add_parser("preflight", help="Check GPU wrapper, staged APIs, active markers, and the next eligible batch.")
    add_common(p)
    p.add_argument("--limit", type=int, default=MAX_BATCH_PDFS)
    p.add_argument("--sort-by", choices=["smallest", "largest", "name"], default="smallest")
    p.add_argument("--skip-sha", action="store_true", help="Skip content SHA scan; faster but less duplicate-aware.")
    p.add_argument("--lineage-file", default="", help="Reuse a previously generated lineage-audit JSON instead of scanning MinIO again.")
    p.add_argument("--include-error-review", action="store_true", help="Explicitly include error_review items for a controlled retry batch.")
    p.add_argument(
        "--input-status-only",
        action="store_true",
        help="Use only eduassets-input status markers for fast planning; intended for quick UI preflight.",
    )
    p.add_argument(
        "--reprocess-completed",
        action="store_true",
        help="Explicitly include gpu_done targets in a new immutable MinerU/Popo version. Reserved for administrator-controlled validation.",
    )
    add_target_filters(p)
    p.set_defaults(func=preflight_command)
    p = sub.add_parser("report", help="Sanitized read-only first-stage report with queues and bucket contract audit.")
    add_common(p)
    p.add_argument("--sample-limit", type=int, default=20)
    p.add_argument("--skip-sha", action="store_true", help="Skip content SHA scan; faster but less duplicate-aware.")
    p.set_defaults(func=report_command)
    p = sub.add_parser("asset-gap", help="Read-only material_id inventory from input through clean buckets.")
    add_common(p)
    p.add_argument("--raw-bucket", default="eduassets-raw")
    p.add_argument("--clean-bucket", default="eduassets-clean")
    p.add_argument("--sample-limit", type=int, default=20)
    p.add_argument("--skip-sha", action="store_true", help="Skip content SHA scan; faster but less duplicate-aware.")
    p.set_defaults(func=asset_gap_command)
    p = sub.add_parser("contract-note", help="Write a non-destructive bucket contract note only when --apply is set.")
    add_common(p)
    p.add_argument(
        "--note-object",
        default="_contract/first-stage-auxiliary-prefix-classification.json",
        help="Object to write in eduassets-mineru when --apply is set.",
    )
    p.set_defaults(func=contract_note_command)
    p = sub.add_parser("run-staged", help="Run staged MinerU -> freeze -> Popo -> freeze for one batch.")
    add_common(p)
    p.add_argument("--limit", type=int, default=MAX_BATCH_PDFS)
    p.add_argument("--sort-by", choices=["smallest", "largest", "name"], default="smallest")
    p.add_argument("--skip-sha", action="store_true", help="Skip content SHA scan; faster but less duplicate-aware.")
    p.add_argument("--lineage-file", default="", help="Reuse a previously generated lineage-audit JSON instead of scanning MinIO again.")
    p.add_argument("--include-error-review", action="store_true", help="Explicitly retry error_review items through staged MinerU/Popo.")
    p.add_argument(
        "--input-status-only",
        action="store_true",
        help="Use only eduassets-input status markers for fast retry selection; final completion still requires lineage audit.",
    )
    p.add_argument(
        "--reprocess-completed",
        action="store_true",
        help="Explicitly include gpu_done targets in a new immutable MinerU/Popo version. Reserved for administrator-controlled validation.",
    )
    add_target_filters(p)
    p.add_argument("--wait", action="store_true", help="Wait for MinerU and Popo completion and freeze outputs.")
    p.add_argument("--poll-interval", type=int, default=30)
    p.add_argument("--timeout-seconds", type=int, default=7200)
    p.add_argument("--client-batch-id", default="", help="Optional client batch id for the MinerU stage.")
    p.add_argument("--existing-mineru-batch-id", default="", help="Recover an already submitted staged MinerU batch instead of submitting a new one.")
    p.add_argument(
        "--reuse-frozen-mineru",
        action="store_true",
        help="Reuse the matching formal MinerU manifest and frozen marker without downloading or freezing MinerU again.",
    )
    p.add_argument("--popo-client-batch-id", default="", help="Optional client batch id for the Popo stage.")
    p.add_argument("--existing-popo-batch-id", default="", help="Recover an already submitted staged Popo batch instead of submitting a new one.")
    p.add_argument("--mineru-only", action="store_true", help="Stop after MinerU is frozen; do not submit Popo.")
    p.add_argument("--package-dir", default="work/staged_popo_packages")
    p.add_argument("--package-prefix", default="popo-input")
    p.add_argument(
        "--popo-submit-mode",
        choices=["auto", "url", "server_path"],
        default="auto",
        help="How to pass frozen MinerU packages to staged Popo. auto uses server_path when the package exceeds --popo-url-max-bytes.",
    )
    p.add_argument(
        "--popo-url-max-bytes",
        type=int,
        default=MAX_FILE_BYTES,
        help="URL download size ceiling for auto Popo submit mode. Use 0 to always keep URL mode unless --popo-submit-mode says otherwise.",
    )
    p.add_argument("--remote-ssh-host", default="root@113.31.105.253")
    p.add_argument("--remote-ssh-port", type=int, default=23)
    p.add_argument("--remote-ssh-key", default="~/.ssh/id_ed25519_trae_dev")
    p.add_argument(
        "--remote-server-path-dir",
        default="/root/mineru-popo-service/work/package/staged_popo",
        help="Remote package directory for Popo server_path mode; must stay under /root/mineru-popo-service.",
    )
    p.add_argument(
        "--exclude-mineru-rel",
        action="append",
        default=[],
        help="Exclude a frozen MinerU object by path relative to the MinerU official prefix.",
    )
    p.set_defaults(func=run_staged_command)
    p = sub.add_parser("wrapper-capabilities", help="Probe whether the GPU wrapper exposes staged MinerU/Popo APIs.")
    add_common(p)
    p.set_defaults(func=wrapper_capabilities_command)
    p = sub.add_parser("submit", help="Submit one legacy wrapper batch. True staged execution requires staged wrapper APIs.")
    add_common(p)
    p.add_argument("--limit", type=int, default=MAX_BATCH_PDFS)
    p.set_defaults(func=submit_batch)
    p = sub.add_parser("collect", help="Collect terminal submitted wrapper batches.")
    add_common(p)
    p.add_argument("--max-batches", type=int, default=1)
    p.add_argument("--limit", type=int, default=MAX_BATCH_PDFS)
    p.set_defaults(func=collect_batches)
    p = sub.add_parser("daemon", help="Legacy loop: collect terminal batches, then submit if idle.")
    add_common(p)
    p.add_argument("--limit", type=int, default=MAX_BATCH_PDFS)
    p.add_argument("--max-batches", type=int, default=1)
    p.add_argument("--interval", type=int, default=300)
    p.add_argument("--cycles", type=int, default=999999)
    p.set_defaults(func=daemon_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        result = {"status": "ERROR", "error": str(exc), "response": body[:1000]}
        print(dumps(result, pretty=True))
        return 2
    except Exception as exc:
        result = {"status": "ERROR", "error": str(exc), "type": type(exc).__name__}
        print(dumps(result, pretty=True))
        return 2
    exit_code = cli_result_exit_code(result)
    if getattr(args, "out", ""):
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(dumps(result, pretty=True))
            f.write("\n")
        summary: dict[str, Any] = {
            "status": result.get("status"),
            "command": result.get("command"),
            "applied": result.get("applied"),
            "out": args.out,
        }
        for key in ("selected_count", "mineru_batch_id", "existing_mineru_batch_id"):
            if key in result:
                summary[key] = result.get(key)
        if isinstance(result.get("mineru_freezes"), list):
            summary["mineru_freeze_count"] = len(result["mineru_freezes"])
        if isinstance(result.get("mineru_errors"), list):
            summary["mineru_error_count"] = len(result["mineru_errors"])
        popo = result.get("popo")
        if isinstance(popo, dict):
            summary["popo_batch_id"] = popo.get("batch_id")
            if isinstance(popo.get("freezes"), list):
                summary["popo_freeze_count"] = len(popo["freezes"])
            if isinstance(popo.get("errors"), list):
                summary["popo_error_count"] = len(popo["errors"])
            wait = popo.get("wait")
            if isinstance(wait, dict):
                summary["popo_wait_status"] = wait.get("wait_status")
        print(dumps(summary, pretty=True))
        return exit_code
    print(dumps(result, pretty=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
