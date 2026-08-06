from __future__ import annotations

import importlib.util
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


FIXTURE = Path(__file__).parent / "uat" / "task31_fake_services.py"


def _module():
    spec = importlib.util.spec_from_file_location("task31_fake_services_contract", FIXTURE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _post(url: str, values: dict[str, str]) -> int:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(values).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _get_json(url: str, *, bearer: str = "") -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_fake_control_plane_strictly_rejects_get_and_wrong_scheduler_field(tmp_path):
    module = _module()
    module.EVIDENCE_PATH = tmp_path / "events.jsonl"
    module.STATE["instance"] = "Stopped"
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/"
    common = {
        "PublicKey": "fake-public",
        "Region": "cn-test",
        "Zone": "cn-test-01",
        "ProjectId": "org-test",
        "Signature": "fake-signature",
    }
    try:
        with urllib.request.urlopen(base + "?Action=DescribeCompShareInstance", timeout=2):
            raise AssertionError("legacy GET must be rejected")
    except urllib.error.HTTPError as exc:
        assert exc.code == 405
    try:
        assert _post(
            base,
            {**common, "Action": "DescribeCompShareInstance", "UHostIds.0": module.UHOST_ID},
        ) == 200
        assert _post(
            base,
            {**common, "Action": "StartCompShareInstance", "UHostId": module.UHOST_ID, "WithoutGpuSpec": "A"},
        ) == 400
        assert _post(
            base,
            {
                **common,
                "Action": "UpdateCompShareStopScheduler",
                "UHostId": module.UHOST_ID,
                "StopTime": str(int(time.time()) + 600),
            },
        ) == 400
        assert _post(
            base,
            {
                **common,
                "Action": "UpdateCompShareStopScheduler",
                "UHostId": module.UHOST_ID,
                "SchedulerStopTime": str(int(time.time()) + 600),
            },
        ) == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_fake_wrapper_inventory_requires_auth_and_explicit_complete_denominator(tmp_path):
    module = _module()
    module.EVIDENCE_PATH = tmp_path / "events.jsonl"
    module.STATE["jobs"] = {}
    module.STATE["mineru"] = {}
    module.STATE["popo"] = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert _get_json(base + "/api/v1/jobs?limit=100")[0] == 401
        for path in ("/api/v1/jobs", "/api/v1/mineru/batches", "/api/v1/popo/batches"):
            status, payload = _get_json(base + path + "?limit=100", bearer=module.WRAPPER_KEY)
            assert status == 200
            assert payload["total"] == 0
            assert payload["next_cursor"] == ""
            assert payload["has_more"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_fake_wrapper_inventory_paginates_to_explicit_eof_and_preserves_legacy_row_key(tmp_path):
    module = _module()
    module.EVIDENCE_PATH = tmp_path / "events.jsonl"
    module.STATE["jobs"] = {
        f"job-{index}": {"status": "succeeded"}
        for index in range(5)
    }
    module.STATE["mineru"] = {}
    module.STATE["popo"] = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        status, first = _get_json(base + "/api/v1/jobs?limit=2", bearer=module.WRAPPER_KEY)
        assert status == 200
        assert len(first["jobs"]) == 2
        assert first["total"] == 5
        assert first["next_cursor"] == "2"
        assert first["has_more"] is True
        status, second = _get_json(
            base + "/api/v1/jobs?limit=2&cursor=2",
            bearer=module.WRAPPER_KEY,
        )
        assert status == 200
        assert len(second["jobs"]) == 2
        assert second["next_cursor"] == "4"
        status, final = _get_json(
            base + "/api/v1/jobs?limit=2&cursor=4",
            bearer=module.WRAPPER_KEY,
        )
        assert status == 200
        assert len(final["jobs"]) == 1
        assert final["total"] == 5
        assert final["next_cursor"] == ""
        assert final["has_more"] is False

        # A legacy client that only consumes the historical top-level row key
        # continues to receive the same list shape without sending a cursor.
        status, legacy = _get_json(base + "/api/v1/jobs", bearer=module.WRAPPER_KEY)
        assert status == 200
        assert isinstance(legacy["jobs"], list)
        assert len(legacy["jobs"]) == 5
        assert _get_json(base + "/api/v1/jobs?cursor=bad", bearer=module.WRAPPER_KEY)[0] == 422
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
