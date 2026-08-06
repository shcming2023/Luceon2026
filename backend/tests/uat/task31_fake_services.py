#!/usr/bin/env python3
"""Deterministic fake Compshare control plane and staged wrapper for Task31 UAT."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


STATE = {"instance": "Stopped", "jobs": {}, "mineru": {}, "popo": {}, "events": []}
LOCK = threading.Lock()
EVIDENCE_PATH = Path(os.getenv("TASK31_FAKE_EVIDENCE", "/evidence/fake-services.jsonl"))
UHOST_ID = os.getenv("COMPSHARE_UHOST_ID", "uhost-task31-fake")
FAIL_STAGE_ONCE = os.getenv("TASK31_FAKE_FAIL_STAGE_ONCE", "").strip().lower()
FAILURE_USED = {"value": False}
WRAPPER_KEY = os.getenv("GPU_WRAPPER_API_KEY", "task31-fake-wrapper-key")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record(kind: str, payload: dict) -> None:
    row = {"at": now(), "kind": kind, **payload}
    with LOCK:
        STATE["events"].append(row)
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with EVIDENCE_PATH.open("a", encoding="utf-8") as target:
            target.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def archive_bytes(stage: str, run_id: str) -> bytes:
    output = io.BytesIO()
    members = (
        {
            f"mineru/{run_id}_content_list_v2.json": b"[]",
            f"mineru/{run_id}_content_list.json": b"[]",
            f"mineru/{run_id}.md": b"# Task31 isolated UAT\n",
        }
        if stage == "mineru"
        else {
            "enhanced/document_tree.json": json.dumps(
                {"schema": "task31-fake-tree/v1", "title": "Task31 isolated UAT", "children": []},
                sort_keys=True,
            ).encode(),
            "enhanced/document_tree.txt": b"Task31 isolated UAT\n",
            "enhanced/popo_raw.json": b"{}",
        }
    )
    with tarfile.open(fileobj=output, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            tf.addfile(info, io.BytesIO(data))
    return output.getvalue()


def paginated_inventory(rows: list[dict], query: dict[str, list[str]], *, key: str) -> tuple[int, dict]:
    try:
        limit = max(1, min(int((query.get("limit") or ["20"])[0]), 100))
        cursor_text = (query.get("cursor") or [""])[0]
        offset = 0 if cursor_text == "" else int(cursor_text)
    except (TypeError, ValueError):
        return 422, {"error": "invalid_pagination"}
    if offset < 0 or offset > len(rows):
        return 422, {"error": "invalid_pagination"}
    page = rows[offset : offset + limit]
    next_offset = offset + len(page)
    has_more = next_offset < len(rows)
    return 200, {
        key: page,
        "total": len(rows),
        "next_cursor": str(next_offset) if has_more else "",
        "has_more": has_more,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "Task31Fake/1.0"

    def log_message(self, _format: str, *_args) -> None:
        return

    def send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def wrapper_authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {WRAPPER_KEY}"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        action = (query.get("Action") or [""])[0]
        if action:
            self.send_json(405, {"RetCode": 405, "ErrCode": "MethodNotAllowed", "Message": "cloud control requires POST"})
            return
        path = parsed.path.rstrip("/")
        if path == "/api/v1/health":
            self.send_json(
                200,
                {
                    "status": "healthy",
                    "queued_jobs": 0,
                    "queued_batches": 0,
                    "queued_mineru_batches": 0,
                    "queued_popo_batches": 0,
                    "mineru_health": json.dumps({"status": "healthy", "queued_tasks": 0, "processing_tasks": 0}),
                    "artifact_limit_bytes": 20 * 1024**3,
                    "artifact_used_bytes": 0,
                    "disk_available_bytes": 20 * 1024**3,
                    "fake": True,
                },
            )
            return
        if path == "/api/v1/jobs":
            if not self.wrapper_authorized():
                self.send_json(401, {"error": "unauthorized"})
                return
            rows = [dict(value, id=key) for key, value in sorted(STATE["jobs"].items())]
            status, payload = paginated_inventory(rows, query, key="jobs")
            self.send_json(status, payload)
            return
        for stage in ("mineru", "popo"):
            prefix = f"/api/v1/{stage}/batches"
            result_prefix = f"/api/v1/{stage}/results/"
            if path == prefix:
                if not self.wrapper_authorized():
                    self.send_json(401, {"error": "unauthorized"})
                    return
                rows = [dict(value, id=key) for key, value in sorted(STATE[stage].items())]
                status, payload = paginated_inventory(rows, query, key="batches")
                self.send_json(status, payload)
                return
            if path.startswith(prefix + "/"):
                batch_id = path.rsplit("/", 1)[-1]
                payload = STATE[stage].get(batch_id)
                self.send_json(200 if payload else 404, payload or {"error": "not_found"})
                return
            if path.startswith(result_prefix):
                run_id = path[len(result_prefix) :]
                if run_id == "__probe__":
                    self.send_json(404, {"error": "probe"})
                    return
                data = archive_bytes(stage, run_id)
                record("result_download", {"stage": stage, "run_id": run_id, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
                self.send_response(200)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        self.send_json(404, {"error": "not_found", "path": path})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        content_type = self.headers.get("Content-Type", "")
        if path == "" and content_type.startswith("application/x-www-form-urlencoded"):
            length = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            action = (form.get("Action") or [""])[0]
            self.control(action, form)
            return
        for stage in ("mineru", "popo"):
            if path != f"/api/v1/{stage}/batches":
                continue
            if FAIL_STAGE_ONCE == stage and not FAILURE_USED["value"]:
                FAILURE_USED["value"] = True
                record("batch_submit_failed", {"stage": stage, "failure": "injected_once"})
                self.send_json(503, {"error": "injected_test_failure", "stage": stage})
                return
            request = self.read_json()
            batch_id = str(request.get("batch_id") or f"task31-{stage}-batch")
            documents = []
            for index, doc in enumerate(request.get("documents") or []):
                doc_id = str(doc.get("doc_id") or f"doc-{index + 1}")
                run_id = f"{stage}-{hashlib.sha256(doc_id.encode()).hexdigest()[:16]}"
                documents.append(
                    {
                        "doc_id": doc_id,
                        "run_id": run_id,
                        "status": "succeeded",
                        "result_url": f"/api/v1/{stage}/results/{run_id}",
                        "source": doc.get("source") or {},
                    }
                )
            response = {"batch_id": batch_id, "status": "succeeded", "documents": documents}
            STATE[stage][batch_id] = response
            record("batch_submit", {"stage": stage, "batch_id": batch_id, "document_count": len(documents)})
            self.send_json(200, response)
            return
        self.send_json(404, {"error": "not_found"})

    def control(self, action: str, query: dict[str, list[str]]) -> None:
        common = {"Action", "PublicKey", "Region", "Zone", "ProjectId", "Signature"}
        action_fields = {
            "DescribeCompShareInstance": {"UHostIds.0"},
            "StartCompShareInstance": {"UHostId"},
            "StopCompShareInstance": {"UHostId"},
            "UpdateCompShareStopScheduler": {"UHostId", "SchedulerStopTime"},
        }
        expected = common | action_fields.get(action, set())
        actual = set(query)
        if not action or actual != expected or any(len(values) != 1 for values in query.values()):
            self.send_json(
                400,
                {
                    "RetCode": 400,
                    "ErrCode": "InvalidControlContract",
                    "Message": "control fields do not match the official contract",
                },
            )
            return
        if action == "StartCompShareInstance" and "WithoutGpuSpec" in actual:
            self.send_json(400, {"RetCode": 400, "ErrCode": "WithoutGpuSpecForbidden"})
            return
        if action == "DescribeCompShareInstance":
            response = {"RetCode": 0, "UHostSet": [{"UHostId": UHOST_ID, "State": STATE["instance"], "IPSet": []}]}
        elif action == "StartCompShareInstance":
            if STATE["instance"] != "Stopped":
                response = {"RetCode": 191, "ErrCode": "InstanceOperationInProgress", "Message": "not stopped"}
            else:
                STATE["instance"] = "Running"
                response = {"RetCode": 0, "UHostId": UHOST_ID}
        elif action == "StopCompShareInstance":
            if STATE["instance"] != "Running":
                response = {"RetCode": 191, "ErrCode": "InstanceOperationInProgress", "Message": "not running"}
            else:
                STATE["instance"] = "Stopped"
                response = {"RetCode": 0, "UHostId": UHOST_ID}
        elif action == "UpdateCompShareStopScheduler":
            scheduler_stop_time = int((query.get("SchedulerStopTime") or ["0"])[0])
            if scheduler_stop_time < int(datetime.now(timezone.utc).timestamp()) + 300:
                response = {"RetCode": 400, "ErrCode": "SchedulerStopTimeTooSoon", "Message": "must be >= now+300"}
            else:
                response = {"RetCode": 0, "UHostId": UHOST_ID, "SchedulerStopTime": scheduler_stop_time}
        else:
            response = {"RetCode": 404, "ErrCode": "UnknownAction", "Message": action}
        record("cloud_control", {"action": action, "state": STATE["instance"], "ret_code": response.get("RetCode")})
        self.send_json(200, response)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), Handler).serve_forever()
