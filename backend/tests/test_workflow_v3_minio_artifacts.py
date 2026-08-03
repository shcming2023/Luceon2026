from __future__ import annotations

import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.workflow_v3.executor import ArtifactIntegrityError, ArtifactRef
from app.workflow_v3.minio_artifacts import (
    MinioArtifactPolicyError,
    MinioArtifactTransportError,
    MinioCandidateArtifactStore,
    MinioFormalArtifactStore,
    MinioReadonlyArtifactStore,
)


class MissingObject(Exception):
    code = "NoSuchKey"


class PreconditionFailed(Exception):
    code = "PreconditionFailed"


class FakeResponse:
    def __init__(self, payload: bytes, *, fail_on_read: bool = False):
        self._stream = io.BytesIO(payload)
        self.fail_on_read = fail_on_read
        self.closed = False
        self.released = False

    def read(self, amount: int) -> bytes:
        if self.fail_on_read:
            raise OSError("stream interrupted")
        return self._stream.read(amount)

    def close(self) -> None:
        self.closed = True
        self._stream.close()

    def release_conn(self) -> None:
        self.released = True


class FakeMinio:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.responses: list[FakeResponse] = []
        self.stat_calls: list[tuple[str, str]] = []
        self.get_calls: list[tuple[str, str]] = []
        self.put_calls: list[tuple[str, str, int, str, dict | None]] = []
        self.put_failure: str | None = None
        self.race_payload: bytes | None = None
        self.fail_reads = False
        self.list_calls = 0
        self.remove_calls = 0

    def stat_object(self, bucket: str, object_name: str):
        self.stat_calls.append((bucket, object_name))
        key = (bucket, object_name)
        if key not in self.objects:
            raise MissingObject()
        return SimpleNamespace(size=len(self.objects[key]))

    def get_object(self, bucket: str, object_name: str):
        self.get_calls.append((bucket, object_name))
        key = (bucket, object_name)
        if key not in self.objects:
            raise MissingObject()
        response = FakeResponse(self.objects[key], fail_on_read=self.fail_reads)
        self.responses.append(response)
        return response

    def put_object_if_absent(
        self,
        bucket: str,
        object_name: str,
        data,
        length: int,
        content_type: str = "application/octet-stream",
        metadata=None,
    ):
        payload = data.read()
        assert len(payload) == length
        self.put_calls.append((bucket, object_name, length, content_type, metadata))
        key = (bucket, object_name)
        if self.race_payload is not None:
            self.objects[key] = self.race_payload
            raise PreconditionFailed()
        if key in self.objects:
            raise PreconditionFailed()
        if self.put_failure == "before":
            raise OSError("upload failed before persistence")
        if self.put_failure == "partial":
            self.objects[key] = payload[: max(1, len(payload) // 2)]
            raise OSError("multipart upload interrupted")
        self.objects[key] = payload
        if self.put_failure == "after":
            raise OSError("response lost after persistence")
        return SimpleNamespace(bucket_name=bucket, object_name=object_name)

    def list_objects(self, *_args, **_kwargs):
        self.list_calls += 1
        raise AssertionError("artifact adapters must not list objects")

    def remove_object(self, *_args, **_kwargs):
        self.remove_calls += 1
        raise AssertionError("artifact adapters must not delete objects")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _candidate_name(digest: str) -> str:
    return f"jobs/job-1/stage-1/{digest}/artifact"


def _writer(client: FakeMinio) -> MinioCandidateArtifactStore:
    return MinioCandidateArtifactStore(
        client,
        readable_buckets={"eduassets-minerupopo"},
        candidate_scopes={"worker-v3-candidates": {"jobs/job-1"}},
    )


def test_readonly_materialize_streams_exact_bytes_and_releases_response(tmp_path):
    payload = b"frozen-popo-manifest" * 100
    reference = ArtifactRef(
        bucket="eduassets-minerupopo",
        object_name="minerupopo/material/popo-run/manifest.json",
        sha256=_sha(payload),
        size_bytes=len(payload),
    )
    client = FakeMinio({(reference.bucket, reference.object_name): payload})
    store = MinioReadonlyArtifactStore(
        client,
        readable_buckets={"eduassets-minerupopo"},
    )

    destination = tmp_path / "input" / "artifact"
    actual = store.materialize(reference, destination)

    assert actual == reference
    assert destination.read_bytes() == payload
    assert destination.stat().st_mode & 0o777 == 0o444
    assert client.responses[-1].closed is True
    assert client.responses[-1].released is True
    assert not hasattr(store, "put_candidate")
    assert not hasattr(store, "remove_object")
    assert not hasattr(store, "list_objects")
    assert not hasattr(store, "promote")


def test_read_failure_and_hash_mismatch_fail_closed_and_remove_local_partial(tmp_path):
    payload = b"remote-object"
    object_key = ("eduassets-minerupopo", "frozen/input.json")
    client = FakeMinio({object_key: payload})
    store = MinioReadonlyArtifactStore(
        client,
        readable_buckets={object_key[0]},
    )
    destination = tmp_path / "input" / "artifact"
    wrong = ArtifactRef(object_key[0], object_key[1], "f" * 64, len(payload))

    with pytest.raises(ArtifactIntegrityError, match="immutable reference"):
        store.materialize(wrong, destination)

    assert not destination.exists()
    assert list(destination.parent.glob(".*.partial-*")) == []
    assert client.responses[-1].closed is True
    assert client.responses[-1].released is True

    client.fail_reads = True
    good = ArtifactRef(object_key[0], object_key[1], _sha(payload), len(payload))
    with pytest.raises(MinioArtifactTransportError, match="stream failed"):
        store.materialize(good, destination)
    assert client.responses[-1].closed is True
    assert client.responses[-1].released is True
    assert not destination.exists()


def test_candidate_put_is_content_addressed_immutable_and_idempotent(tmp_path):
    payload = b"candidate-output" * 100
    digest = _sha(payload)
    source = tmp_path / "artifact.tar"
    source.write_bytes(payload)
    object_name = _candidate_name(digest)
    client = FakeMinio()
    store = _writer(client)

    first = store.put_candidate(
        source,
        bucket="worker-v3-candidates",
        object_name=object_name,
        expected_sha256=digest,
    )
    second = store.put_candidate(
        source,
        bucket="worker-v3-candidates",
        object_name=object_name,
        expected_sha256=digest,
    )

    assert first == second == ArtifactRef(
        "worker-v3-candidates",
        object_name,
        digest,
        len(payload),
    )
    assert len(client.put_calls) == 1
    assert client.put_calls[0][4] == {"luceon-sha256": digest}
    assert all(response.closed and response.released for response in client.responses)
    assert not hasattr(store, "remove_object")
    assert not hasattr(store, "list_objects")
    assert not hasattr(store, "promote")

    client.objects[("worker-v3-candidates", object_name)] = b"different"
    with pytest.raises(ArtifactIntegrityError, match="immutable reference"):
        store.put_candidate(
            source,
            bucket="worker-v3-candidates",
            object_name=object_name,
            expected_sha256=digest,
        )
    assert len(client.put_calls) == 1


def test_candidate_scope_hash_component_and_safe_names_are_enforced_before_io(tmp_path):
    payload = b"candidate"
    digest = _sha(payload)
    source = tmp_path / "artifact"
    source.write_bytes(payload)
    client = FakeMinio()
    store = _writer(client)

    rejected = [
        ("other-bucket", _candidate_name(digest)),
        ("worker-v3-candidates", f"other/job/{digest}/artifact"),
        ("worker-v3-candidates", "jobs/job-1/stage-1/no-digest/artifact"),
        ("worker-v3-candidates", f"jobs/job-1/../{digest}/artifact"),
        ("Bad_Bucket", _candidate_name(digest)),
    ]
    for bucket, object_name in rejected:
        with pytest.raises(MinioArtifactPolicyError):
            store.put_candidate(
                source,
                bucket=bucket,
                object_name=object_name,
                expected_sha256=digest,
            )

    assert client.stat_calls == []
    assert client.get_calls == []
    assert client.put_calls == []


def test_partial_upload_fails_closed_without_list_or_delete(tmp_path):
    payload = b"candidate-that-will-be-partial" * 100
    digest = _sha(payload)
    source = tmp_path / "artifact"
    source.write_bytes(payload)
    object_name = _candidate_name(digest)
    client = FakeMinio()
    client.put_failure = "partial"
    store = _writer(client)

    with pytest.raises(ArtifactIntegrityError, match="non-matching object bytes"):
        store.put_candidate(
            source,
            bucket="worker-v3-candidates",
            object_name=object_name,
            expected_sha256=digest,
        )

    assert client.objects[("worker-v3-candidates", object_name)] != payload
    assert client.list_calls == 0
    assert client.remove_calls == 0
    assert client.responses[-1].closed is True
    assert client.responses[-1].released is True


def test_lost_put_response_recovers_only_after_exact_remote_verification(tmp_path):
    payload = b"complete-despite-lost-response"
    digest = _sha(payload)
    source = tmp_path / "artifact"
    source.write_bytes(payload)
    object_name = _candidate_name(digest)
    client = FakeMinio()
    client.put_failure = "after"
    store = _writer(client)

    result = store.put_candidate(
        source,
        bucket="worker-v3-candidates",
        object_name=object_name,
        expected_sha256=digest,
    )

    assert result.sha256 == digest
    assert result.size_bytes == len(payload)
    assert client.responses[-1].closed is True
    assert client.responses[-1].released is True


def test_conditional_create_detects_a_conflicting_concurrent_writer(tmp_path):
    payload = b"intended-candidate"
    digest = _sha(payload)
    source = tmp_path / "artifact"
    source.write_bytes(payload)
    object_name = _candidate_name(digest)
    client = FakeMinio()
    client.race_payload = b"conflicting-concurrent-bytes"
    store = _writer(client)

    with pytest.raises(
        ArtifactIntegrityError,
        match="left non-matching object bytes",
    ):
        store.put_candidate(
            source,
            bucket="worker-v3-candidates",
            object_name=object_name,
            expected_sha256=digest,
        )

    assert client.objects[("worker-v3-candidates", object_name)] != payload


def test_missing_or_disallowed_read_objects_fail_without_fallback():
    client = FakeMinio()
    store = MinioReadonlyArtifactStore(
        client,
        readable_buckets={"worker-v3-candidates"},
    )
    missing = ArtifactRef(
        "worker-v3-candidates",
        f"jobs/job-1/{'a' * 64}/artifact",
        "a" * 64,
        1,
    )

    with pytest.raises(ArtifactIntegrityError, match="missing"):
        store.stat(missing)
    with pytest.raises(MinioArtifactPolicyError, match="not readable"):
        store.stat(
            ArtifactRef(
                "eduassets-input",
                "book.pdf",
                "b" * 64,
                10,
            )
        )


def test_formal_writer_is_scope_limited_and_never_overwrites(tmp_path):
    payload = b"formal-delivery"
    digest = _sha(payload)
    source = tmp_path / "main.pdf"
    source.write_bytes(payload)
    object_name = "elegantbook/pdf-material/popo-run/job-v3/files/main.pdf"
    client = FakeMinio()
    store = MinioFormalArtifactStore(
        client,
        readable_buckets={"worker-v3-candidates"},
        formal_scopes={"eduassets-elegantbook": {"elegantbook/pdf-material"}},
    )

    first = store.put_formal(
        source,
        bucket="eduassets-elegantbook",
        object_name=object_name,
        expected_sha256=digest,
        content_type="application/pdf",
    )
    second = store.put_formal(
        source,
        bucket="eduassets-elegantbook",
        object_name=object_name,
        expected_sha256=digest,
        content_type="application/pdf",
    )

    assert first == second
    assert len(client.put_calls) == 1
    assert client.put_calls[0][3] == "application/pdf"
    assert not hasattr(store, "put_candidate")
    assert not hasattr(store, "remove_object")
    assert not hasattr(store, "list_objects")
    assert not hasattr(store, "promote")

    client.objects[("eduassets-elegantbook", object_name)] = b"conflict"
    with pytest.raises(ArtifactIntegrityError, match="immutable reference"):
        store.put_formal(
            source,
            bucket="eduassets-elegantbook",
            object_name=object_name,
            expected_sha256=digest,
            content_type="application/pdf",
        )


def test_formal_writer_rejects_out_of_scope_keys(tmp_path):
    payload = b"formal-delivery"
    digest = _sha(payload)
    source = tmp_path / "artifact"
    source.write_bytes(payload)
    client = FakeMinio()
    store = MinioFormalArtifactStore(
        client,
        readable_buckets={"worker-v3-candidates"},
        formal_scopes={"eduassets-elegantbook": {"elegantbook/pdf-material"}},
    )

    for bucket, object_name in (
        (
            "eduassets-elegantbook",
            "elegantbook/other/popo-run/job/files/main.pdf",
        ),
        (
            "other-bucket",
            "elegantbook/pdf-material/popo-run/job/files/main.pdf",
        ),
    ):
        with pytest.raises(MinioArtifactPolicyError):
            store.put_formal(
                source,
                bucket=bucket,
                object_name=object_name,
                expected_sha256=digest,
            )

    assert client.stat_calls == []
    assert client.get_calls == []
    assert client.put_calls == []


def test_formal_conditional_create_detects_concurrent_same_name_conflict(tmp_path):
    payload = b"formal-delivery"
    digest = _sha(payload)
    source = tmp_path / "main.pdf"
    source.write_bytes(payload)
    object_name = "elegantbook/v3/material/run/files/main.pdf"
    client = FakeMinio()
    client.race_payload = b"conflicting-formal-bytes"
    store = MinioFormalArtifactStore(
        client,
        readable_buckets={"eduassets-elegantbook"},
        formal_scopes={"eduassets-elegantbook": {"elegantbook/v3"}},
    )

    with pytest.raises(
        ArtifactIntegrityError,
        match="left non-matching object bytes",
    ):
        store.put_formal(
            source,
            bucket="eduassets-elegantbook",
            object_name=object_name,
            expected_sha256=digest,
            content_type="application/pdf",
        )

    assert client.objects[("eduassets-elegantbook", object_name)] != payload
