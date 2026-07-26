from __future__ import annotations

import hashlib
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, Iterator, Mapping, Protocol

from app.workflow_v3.executor import ArtifactIntegrityError, ArtifactRef


__all__ = [
    "MinioArtifactPolicyError",
    "MinioArtifactTransportError",
    "MinioCandidateArtifactStore",
    "MinioFormalArtifactStore",
    "MinioReadonlyArtifactStore",
]


_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MISSING_OBJECT_CODES = frozenset({"NoSuchKey", "NoSuchObject"})
_CHUNK_BYTES = 1024 * 1024


class MinioArtifactPolicyError(ArtifactIntegrityError):
    """The requested object is outside this adapter's explicit capability."""


class MinioArtifactTransportError(ArtifactIntegrityError):
    """MinIO did not provide a complete, verifiable object operation."""


class _ObjectMissing(Exception):
    pass


class _MinioClient(Protocol):
    def stat_object(self, bucket_name: str, object_name: str):
        ...

    def get_object(self, bucket_name: str, object_name: str):
        ...

    def put_object_if_absent(
        self,
        bucket_name: str,
        object_name: str,
        data: BinaryIO,
        length: int,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
    ):
        ...


class MinioReadonlyArtifactStore:
    """Exact-hash MinIO reader for evaluators and promotion controllers.

    The public surface intentionally contains only ``materialize`` and
    ``stat``. It has no list, delete, write, or promotion operation.
    """

    def __init__(self, client: _MinioClient, *, readable_buckets: Iterable[str]):
        self._client = client
        self._readable_buckets = _bucket_allowlist(readable_buckets, field="readable_buckets")

    def materialize(self, artifact: ArtifactRef, destination: Path) -> ArtifactRef:
        expected = self._validated_ref(artifact)
        target = Path(destination)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise MinioArtifactPolicyError("materialization target must be a regular file")
        if target.exists():
            actual = _hash_local_file(target, expected.bucket, expected.object_name)
            _assert_identity(expected, actual)
            return actual

        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = target.with_name(f".{target.name}.partial-{uuid.uuid4().hex}")
        try:
            with temporary.open("xb") as output:
                actual = self._stream_object(expected.bucket, expected.object_name, output)
                output.flush()
                os.fsync(output.fileno())
            _assert_identity(expected, actual)
            temporary.replace(target)
            target.chmod(0o444)
            return actual
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def stat(self, artifact: ArtifactRef) -> ArtifactRef:
        expected = self._validated_ref(artifact)
        try:
            actual = self._verify_remote(expected.bucket, expected.object_name)
        except _ObjectMissing as exc:
            raise ArtifactIntegrityError("artifact object is missing") from exc
        _assert_identity(expected, actual)
        return actual

    def _validated_ref(self, artifact: ArtifactRef) -> ArtifactRef:
        if not isinstance(artifact, ArtifactRef):
            raise MinioArtifactPolicyError("artifact reference has an invalid type")
        bucket = _safe_bucket(artifact.bucket)
        object_name = _safe_object_name(artifact.object_name)
        if bucket not in self._readable_buckets:
            raise MinioArtifactPolicyError(f"bucket is not readable by this adapter: {bucket!r}")
        sha256 = _safe_sha256(artifact.sha256, field="artifact SHA-256")
        if (
            not isinstance(artifact.size_bytes, int)
            or isinstance(artifact.size_bytes, bool)
            or artifact.size_bytes < -1
        ):
            raise MinioArtifactPolicyError("artifact size must be -1 or a non-negative integer")
        return ArtifactRef(
            bucket=bucket,
            object_name=object_name,
            sha256=sha256,
            size_bytes=artifact.size_bytes,
        )

    def _verify_remote(self, bucket: str, object_name: str) -> ArtifactRef:
        declared_size = self._stat_size(bucket, object_name)
        actual = self._stream_object(bucket, object_name, None)
        if actual.size_bytes != declared_size:
            raise ArtifactIntegrityError(
                "MinIO stat size differs from the streamed object size"
            )
        return actual

    def _stat_size(self, bucket: str, object_name: str) -> int:
        try:
            stat_result = self._client.stat_object(bucket, object_name)
        except Exception as exc:
            if _is_missing_object(exc):
                raise _ObjectMissing from exc
            raise MinioArtifactTransportError("MinIO stat_object failed") from exc
        size = getattr(stat_result, "size", None)
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise MinioArtifactTransportError("MinIO stat_object returned an invalid size")
        return size

    def _stream_object(
        self,
        bucket: str,
        object_name: str,
        output: BinaryIO | None,
    ) -> ArtifactRef:
        try:
            response = self._client.get_object(bucket, object_name)
        except Exception as exc:
            if _is_missing_object(exc):
                raise _ObjectMissing from exc
            raise MinioArtifactTransportError("MinIO get_object failed") from exc

        digest = hashlib.sha256()
        size = 0
        try:
            while True:
                chunk = response.read(_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise MinioArtifactTransportError("MinIO response returned a non-byte chunk")
                digest.update(chunk)
                size += len(chunk)
                if output is not None:
                    output.write(chunk)
        except Exception as exc:
            _release_response(response, suppress_errors=True)
            if isinstance(exc, ArtifactIntegrityError):
                raise
            raise MinioArtifactTransportError("MinIO object stream failed") from exc
        _release_response(response, suppress_errors=False)
        return ArtifactRef(
            bucket=bucket,
            object_name=object_name,
            sha256=digest.hexdigest(),
            size_bytes=size,
        )


class MinioCandidateArtifactStore:
    """Candidate-only writer plus exact-hash reads needed by a Producer.

    Candidate keys must be under an explicit bucket/prefix capability and must
    contain their lowercase SHA-256 as a complete path component. Existing
    matching bytes are idempotent; existing different bytes are never
    overwritten.
    """

    def __init__(
        self,
        client: _MinioClient,
        *,
        readable_buckets: Iterable[str],
        candidate_scopes: Mapping[str, Iterable[str]],
    ):
        if not isinstance(candidate_scopes, Mapping) or not candidate_scopes:
            raise MinioArtifactPolicyError("candidate_scopes must not be empty")
        normalized_scopes: dict[str, tuple[str, ...]] = {}
        for raw_bucket, raw_prefixes in candidate_scopes.items():
            bucket = _safe_bucket(raw_bucket)
            if isinstance(raw_prefixes, (str, bytes)):
                raise MinioArtifactPolicyError(
                    f"candidate bucket {bucket!r} prefixes must be an iterable"
                )
            prefixes = tuple(
                sorted(
                    {
                        _safe_candidate_prefix(prefix)
                        for prefix in raw_prefixes
                    }
                )
            )
            if not prefixes:
                raise MinioArtifactPolicyError(
                    f"candidate bucket {bucket!r} has no allowed prefixes"
                )
            normalized_scopes[bucket] = prefixes
        readable = set(_bucket_allowlist(readable_buckets, field="readable_buckets"))
        readable.update(normalized_scopes)
        self._reader = MinioReadonlyArtifactStore(
            client,
            readable_buckets=readable,
        )
        self._client = client
        self._candidate_scopes = normalized_scopes

    def materialize(self, artifact: ArtifactRef, destination: Path) -> ArtifactRef:
        return self._reader.materialize(artifact, destination)

    def stat(self, artifact: ArtifactRef) -> ArtifactRef:
        return self._reader.stat(artifact)

    def put_candidate(
        self,
        source: Path,
        *,
        bucket: str,
        object_name: str,
        expected_sha256: str,
    ) -> ArtifactRef:
        bucket = _safe_bucket(bucket)
        object_name = _safe_object_name(object_name)
        expected_sha256 = _safe_sha256(expected_sha256, field="candidate SHA-256")
        self._assert_candidate_scope(bucket, object_name, expected_sha256)

        source_path = Path(source)
        if source_path.is_symlink() or not source_path.is_file():
            raise MinioArtifactPolicyError("candidate source must be a regular file")
        with _verified_local_snapshot(
            source_path,
            bucket=bucket,
            object_name=object_name,
            expected_sha256=expected_sha256,
        ) as (source_handle, source_ref):
            try:
                existing = self._reader._verify_remote(bucket, object_name)
            except _ObjectMissing:
                existing = None
            if existing is not None:
                _assert_identity(source_ref, existing)
                return existing

            try:
                self._client.put_object_if_absent(
                    bucket,
                    object_name,
                    source_handle,
                    length=source_ref.size_bytes,
                    content_type="application/octet-stream",
                    metadata={"luceon-sha256": expected_sha256},
                )
            except Exception as exc:
                return self._recover_or_fail_upload(source_ref, exc)

        try:
            uploaded = self._reader._verify_remote(bucket, object_name)
        except _ObjectMissing as exc:
            raise MinioArtifactTransportError(
                "MinIO acknowledged candidate upload but the object is missing"
            ) from exc
        _assert_identity(source_ref, uploaded)
        return uploaded

    def _assert_candidate_scope(
        self,
        bucket: str,
        object_name: str,
        expected_sha256: str,
    ) -> None:
        prefixes = self._candidate_scopes.get(bucket)
        if prefixes is None or not any(object_name.startswith(prefix) for prefix in prefixes):
            raise MinioArtifactPolicyError(
                "candidate object is outside the configured bucket/prefix allowlist"
            )
        if expected_sha256 not in PurePosixPath(object_name).parts:
            raise MinioArtifactPolicyError(
                "candidate object name must contain its SHA-256 path component"
            )

    def _recover_or_fail_upload(
        self,
        expected: ArtifactRef,
        upload_error: Exception,
    ) -> ArtifactRef:
        try:
            actual = self._reader._verify_remote(expected.bucket, expected.object_name)
        except _ObjectMissing:
            raise MinioArtifactTransportError(
                "candidate upload failed before a verifiable object was stored"
            ) from upload_error
        except ArtifactIntegrityError:
            raise ArtifactIntegrityError(
                "candidate upload failed and left non-matching object bytes"
            ) from upload_error
        try:
            _assert_identity(expected, actual)
        except ArtifactIntegrityError:
            raise ArtifactIntegrityError(
                "candidate upload failed and left non-matching object bytes"
            ) from upload_error
        return actual


class MinioFormalArtifactStore:
    """Immutable writer restricted to formal output scopes.

    This adapter is intended only for the projection worker.  It has no
    candidate-write, list, delete, or current-output promotion operation.
    """

    def __init__(
        self,
        client: _MinioClient,
        *,
        readable_buckets: Iterable[str],
        formal_scopes: Mapping[str, Iterable[str]],
    ):
        if not isinstance(formal_scopes, Mapping) or not formal_scopes:
            raise MinioArtifactPolicyError("formal_scopes must not be empty")
        normalized_scopes: dict[str, tuple[str, ...]] = {}
        for raw_bucket, raw_prefixes in formal_scopes.items():
            bucket = _safe_bucket(raw_bucket)
            if isinstance(raw_prefixes, (str, bytes)):
                raise MinioArtifactPolicyError(
                    f"formal bucket {bucket!r} prefixes must be an iterable"
                )
            prefixes = tuple(
                sorted({_safe_candidate_prefix(prefix) for prefix in raw_prefixes})
            )
            if not prefixes:
                raise MinioArtifactPolicyError(
                    f"formal bucket {bucket!r} has no allowed prefixes"
                )
            normalized_scopes[bucket] = prefixes
        readable = set(_bucket_allowlist(readable_buckets, field="readable_buckets"))
        readable.update(normalized_scopes)
        self._reader = MinioReadonlyArtifactStore(
            client,
            readable_buckets=readable,
        )
        self._client = client
        self._formal_scopes = normalized_scopes

    def materialize(self, artifact: ArtifactRef, destination: Path) -> ArtifactRef:
        return self._reader.materialize(artifact, destination)

    def stat(self, artifact: ArtifactRef) -> ArtifactRef:
        return self._reader.stat(artifact)

    def put_formal(
        self,
        source: Path,
        *,
        bucket: str,
        object_name: str,
        expected_sha256: str,
        content_type: str = "application/octet-stream",
    ) -> ArtifactRef:
        bucket = _safe_bucket(bucket)
        object_name = _safe_object_name(object_name)
        expected_sha256 = _safe_sha256(expected_sha256, field="formal SHA-256")
        prefixes = self._formal_scopes.get(bucket)
        if prefixes is None or not any(
            object_name.startswith(prefix) for prefix in prefixes
        ):
            raise MinioArtifactPolicyError(
                "formal object is outside the configured bucket/prefix allowlist"
            )
        if not isinstance(content_type, str) or not content_type.strip():
            raise MinioArtifactPolicyError("formal content type is required")

        source_path = Path(source)
        if source_path.is_symlink() or not source_path.is_file():
            raise MinioArtifactPolicyError("formal source must be a regular file")
        with _verified_local_snapshot(
            source_path,
            bucket=bucket,
            object_name=object_name,
            expected_sha256=expected_sha256,
        ) as (source_handle, source_ref):
            try:
                existing = self._reader._verify_remote(bucket, object_name)
            except _ObjectMissing:
                existing = None
            if existing is not None:
                _assert_identity(source_ref, existing)
                return existing
            try:
                self._client.put_object_if_absent(
                    bucket,
                    object_name,
                    source_handle,
                    length=source_ref.size_bytes,
                    content_type=content_type,
                    metadata={"luceon-sha256": expected_sha256},
                )
            except Exception as exc:
                try:
                    actual = self._reader._verify_remote(bucket, object_name)
                except _ObjectMissing:
                    raise MinioArtifactTransportError(
                        "formal upload failed before a verifiable object was stored"
                    ) from exc
                try:
                    _assert_identity(source_ref, actual)
                except ArtifactIntegrityError:
                    raise ArtifactIntegrityError(
                        "formal upload failed and left non-matching object bytes"
                    ) from exc
                return actual

        try:
            uploaded = self._reader._verify_remote(bucket, object_name)
        except _ObjectMissing as exc:
            raise MinioArtifactTransportError(
                "MinIO acknowledged formal upload but the object is missing"
            ) from exc
        _assert_identity(source_ref, uploaded)
        return uploaded


def _bucket_allowlist(values: Iterable[str], *, field: str) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise MinioArtifactPolicyError(f"{field} must be an iterable of bucket names")
    normalized = frozenset(_safe_bucket(value) for value in values)
    if not normalized:
        raise MinioArtifactPolicyError(f"{field} must not be empty")
    return normalized


def _safe_bucket(value: object) -> str:
    if not isinstance(value, str) or not _BUCKET_RE.fullmatch(value):
        raise MinioArtifactPolicyError(f"unsafe MinIO bucket name: {value!r}")
    if ".." in value or ".-" in value or "-." in value:
        raise MinioArtifactPolicyError(f"unsafe MinIO bucket name: {value!r}")
    return value


def _safe_object_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or len(value.encode("utf-8")) > 1024
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise MinioArtifactPolicyError(f"unsafe MinIO object name: {value!r}")
    parsed = PurePosixPath(value)
    if str(parsed) != value or any(part in {"", ".", ".."} for part in parsed.parts):
        raise MinioArtifactPolicyError(f"unsafe MinIO object name: {value!r}")
    return value


def _safe_candidate_prefix(value: object) -> str:
    if not isinstance(value, str):
        raise MinioArtifactPolicyError("candidate prefix must be a string")
    normalized = _safe_object_name(value[:-1] if value.endswith("/") else value)
    if not normalized:
        raise MinioArtifactPolicyError("candidate prefix must not be empty")
    return f"{normalized}/"


def _safe_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise MinioArtifactPolicyError(f"{field} must be a lowercase SHA-256")
    return value


def _hash_local_file(path: Path, bucket: str, object_name: str) -> ArtifactRef:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
            size += len(chunk)
    return ArtifactRef(
        bucket=bucket,
        object_name=object_name,
        sha256=digest.hexdigest(),
        size_bytes=size,
    )


@contextmanager
def _verified_local_snapshot(
    path: Path,
    *,
    bucket: str,
    object_name: str,
    expected_sha256: str,
) -> Iterator[tuple[BinaryIO, ArtifactRef]]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source, tempfile.TemporaryFile(mode="w+b") as snapshot:
        while True:
            chunk = source.read(_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            snapshot.write(chunk)
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise ArtifactIntegrityError(
                "candidate bytes do not match their declared SHA-256"
            )
        snapshot.flush()
        snapshot.seek(0)
        yield snapshot, ArtifactRef(
            bucket=bucket,
            object_name=object_name,
            sha256=actual_sha256,
            size_bytes=size,
        )


def _assert_identity(expected: ArtifactRef, actual: ArtifactRef) -> None:
    if (
        expected.bucket != actual.bucket
        or expected.object_name != actual.object_name
        or expected.sha256 != actual.sha256
        or (expected.size_bytes >= 0 and expected.size_bytes != actual.size_bytes)
    ):
        raise ArtifactIntegrityError(
            "artifact bytes do not match the immutable reference"
        )


def _is_missing_object(exc: Exception) -> bool:
    return getattr(exc, "code", None) in _MISSING_OBJECT_CODES


def _release_response(response, *, suppress_errors: bool) -> None:
    errors: list[Exception] = []
    try:
        response.close()
    except Exception as exc:
        errors.append(exc)
    try:
        response.release_conn()
    except Exception as exc:
        errors.append(exc)
    if errors and not suppress_errors:
        raise MinioArtifactTransportError(
            "MinIO response cleanup failed"
        ) from errors[0]
