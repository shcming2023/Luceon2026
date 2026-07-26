from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any


MINIO_ROLES = ("producer", "evaluator", "promoter", "projector")


def credential_fingerprint(access_key: str, secret_key: str) -> str:
    return (
        hashlib.sha256(access_key.encode("utf-8")).hexdigest()
        + "."
        + hashlib.sha256(secret_key.encode("utf-8")).hexdigest()
    )


def parse_credential_fingerprints(value: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for item in value.split(","):
        role, separator, digest = item.strip().partition(":")
        access_digest, digest_separator, secret_digest = digest.partition(".")
        if (
            not separator
            or role not in MINIO_ROLES
            or role in rows
            or not digest_separator
            or any(
                len(part) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in part
                )
                for part in (access_digest, secret_digest)
            )
        ):
            raise ValueError("invalid Worker V3 MinIO credential fingerprint matrix")
        rows[role] = digest
    access_digests = {value.split(".", 1)[0] for value in rows.values()}
    secret_digests = {value.split(".", 1)[1] for value in rows.values()}
    if (
        set(rows) != set(MINIO_ROLES)
        or len(access_digests) != len(MINIO_ROLES)
        or len(secret_digests) != len(MINIO_ROLES)
    ):
        raise ValueError(
            "Worker V3 MinIO credentials must declare four distinct role "
            "access and secret fingerprints"
        )
    return rows


def role_policy_documents(
    *,
    candidate_bucket: str,
    candidate_prefix: str,
    formal_bucket: str,
    formal_prefix: str,
    source_buckets: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    candidate_objects = (
        f"arn:aws:s3:::{candidate_bucket}/{candidate_prefix.strip('/')}/*"
    )
    formal_objects = (
        f"arn:aws:s3:::{formal_bucket}/{formal_prefix.strip('/')}/*"
    )
    source_objects = [f"arn:aws:s3:::{bucket}/*" for bucket in source_buckets]
    all_objects = "arn:aws:s3:::*/*"
    source_locations = [f"arn:aws:s3:::{bucket}" for bucket in source_buckets]
    candidate_location = f"arn:aws:s3:::{candidate_bucket}"
    formal_location = f"arn:aws:s3:::{formal_bucket}"

    def document(*statements: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "Version": "2012-10-17",
            "Statement": [dict(statement) for statement in statements],
        }

    def location(*resources: str) -> dict[str, Any]:
        return {
            "Sid": "ReadBucketLocation",
            "Effect": "Allow",
            "Action": ["s3:GetBucketLocation"],
            "Resource": list(resources),
        }

    deny_delete = {
        "Sid": "DenyObjectDeletion",
        "Effect": "Deny",
        "Action": ["s3:DeleteObject", "s3:DeleteObjectVersion"],
        "Resource": [all_objects],
    }
    deny_all_writes = {
        "Sid": "DenyAllObjectWrites",
        "Effect": "Deny",
        "Action": ["s3:PutObject"],
        "Resource": [all_objects],
    }

    return {
        "producer": document(
            location(*source_locations, candidate_location),
            {
                "Sid": "ReadFrozenSourceAndCandidate",
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": [*source_objects, candidate_objects],
            },
            {
                "Sid": "WriteCandidatePrefix",
                "Effect": "Allow",
                "Action": ["s3:PutObject"],
                "Resource": [candidate_objects],
            },
            deny_delete,
        ),
        "evaluator": document(
            location(candidate_location),
            {
                "Sid": "ReadCandidatePrefix",
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": [candidate_objects],
            },
            deny_all_writes,
            deny_delete,
        ),
        "promoter": document(
            location(candidate_location),
            {
                "Sid": "ReadCandidatePrefix",
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": [candidate_objects],
            },
            deny_all_writes,
            deny_delete,
        ),
        "projector": document(
            location(candidate_location, formal_location),
            {
                "Sid": "ReadCandidateAndFormalPrefix",
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": [candidate_objects, formal_objects],
            },
            {
                "Sid": "WriteFormalPrefix",
                "Effect": "Allow",
                "Action": ["s3:PutObject"],
                "Resource": [formal_objects],
            },
            deny_delete,
        ),
    }
