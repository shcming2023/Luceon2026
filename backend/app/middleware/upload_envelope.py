from __future__ import annotations

import json
import shutil
from collections.abc import Awaitable, Callable
from typing import Any
from pathlib import Path
from starlette.formparsers import MultiPartException

from app.services.upload_policy import load_pdf_upload_policy


class UploadEnvelopeExceeded(MultiPartException):
    pass


class UploadClientDisconnected(MultiPartException):
    pass


class UploadEnvelopeMiddleware:
    """Bound the authoritative PDF multipart request before endpoint processing.

    Nginx remains the first body-size gate. This counter also covers chunked
    requests that do not provide Content-Length and stops Starlette's multipart
    parser once the configured aggregate envelope is crossed.
    """

    def __init__(self, app: Callable[..., Awaitable[Any]]) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/api/materials/upload":
            await self.app(scope, receive, send)
            return
        policy = load_pdf_upload_policy()
        request_bound = policy.max_request_bytes + policy.multipart_overhead_bytes
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length", b"")
        content_length: int | None = None
        if raw_length:
            try:
                content_length = int(raw_length)
                if content_length < 0 or content_length > request_bound:
                    await self._reject(send, request_bound)
                    return
            except ValueError:
                await self._reject(send, request_bound)
                return
        temp_dir = Path(policy.temp_dir)
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            free_bytes = int(shutil.disk_usage(temp_dir).free)
        except OSError:
            await self._reject_disk(send, required_bytes=policy.min_local_temp_free_bytes, available_bytes=0)
            return
        parser_spool_bytes = content_length if content_length is not None else request_bound
        stage_copy_bytes = min(policy.max_file_bytes, parser_spool_bytes)
        required_temp_bytes = policy.min_local_temp_free_bytes + parser_spool_bytes + stage_copy_bytes
        if free_bytes < required_temp_bytes:
            await self._reject_disk(send, required_bytes=required_temp_bytes, available_bytes=free_bytes)
            return
        received = 0
        response_started = False

        async def counted_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.disconnect":
                raise UploadClientDisconnected("upload_client_disconnected")
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > request_bound:
                    raise UploadEnvelopeExceeded(f"upload_envelope_exceeded:{request_bound}")
            return message

        async def tracked_send(message: dict):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, tracked_send)
        except UploadEnvelopeExceeded:
            if not response_started:
                await self._reject(send, request_bound)

    @staticmethod
    async def _reject(send: Callable, bound: int) -> None:
        body = json.dumps({"detail": f"multipart request exceeds configured envelope {bound}"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _reject_disk(send: Callable, *, required_bytes: int, available_bytes: int) -> None:
        body = json.dumps(
            {
                "detail": "upload temporary storage preflight failed",
                "required_temp_bytes": required_bytes,
                "available_temp_bytes": available_bytes,
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 507,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})
