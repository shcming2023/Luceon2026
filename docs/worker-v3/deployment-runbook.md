# Worker V3 local review deployment runbook

This runbook activates Worker V3 beside V2.3. It does not replace, migrate, or
rewrite a V2.3 job or output.

## Safety boundary

- `WORKFLOW_V3_ENABLED` defaults to `false`.
- `worker-v3` is the opt-in Compose profile.
- API and worker startup only validate the V3 schema. They never create tables.
- Installed releases are bind-mounted read-only. No V3 worker role mounts the
  active `~/.codex/skills` tree. The shared backend still retains legacy V2.3
  skill mounts, but the V3 API/release resolver may use only the read-only
  installed release.
- All four ordinary roles—producer, independent evaluator, promotion
  controller, and material-output projector—use the dedicated
  `${LUCEON_WORKER_V3_IMAGE}` image. They do not reuse the backend image.
- The four roles have distinct identities and work roots. None mounts
  `/var/run/docker.sock`.
- The Compose profile fixes `WORKFLOW_V3_ARTIFACT_BACKEND=minio`. Candidate
  objects are restricted to
  `worker-v3-candidates/v3/candidates/...`; final projected objects are
  restricted to `eduassets-elegantbook/elegantbook/v3/...`. These namespaces
  do not overlap V2.3 output.
- `DirectoryArtifactStore` remains a development/test adapter admitted only
  outside this Compose profile with
  `WORKFLOW_V3_ALLOW_DIRECTORY_ARTIFACTS=true`. It is not the deployed shadow
  or production artifact path.
- Production deployment contains no Codex Expert dispatcher, runner, Broker,
  App Server, credential mount, API route or UI entry.

## Prepare directories

Run from the LuceonWeb2026 checkout:

```sh
mkdir -p \
  runtime/worker-v3/releases \
  runtime/worker-v3/work/producer \
  runtime/worker-v3/work/evaluator \
  runtime/worker-v3/work/promoter \
  runtime/worker-v3/work/projector
```

When deploying Compose from a clean release worktree while preserving the
existing runtime, set one absolute root rather than copying runtime state into
the worktree:

```sh
LUCEON_RUNTIME_ROOT=/absolute/path/to/original-luceonweb2026/runtime \
docker compose -f docker-compose.luceon-review.yml config
```

All Compose runtime bind sources derive from `LUCEON_RUNTIME_ROOT`; omitting it
preserves the historical `./runtime` default.

Install a verified, immutable release under
`runtime/worker-v3/releases/<release-id>/`. The registered manifest is the
authoritative mapping from release version to release ID; the executor also
accepts the historical `<release-version>/` layout for compatibility. The
release must be complete and pass `verify_release_directory`; an incomplete
release audit is not executable. A missing or drifted installed directory is
recorded as a failed job after claim, rather than leaving queued jobs behind
while the worker restarts.

Set `LUCEON_WORKER_V3_IMAGE` to the immutable registry digest qualified for the
same release. A mutable `:local` tag is only the Compose development default
and is not release evidence:

```sh
export LUCEON_WORKER_V3_IMAGE='registry.example/luceon-worker-v3@sha256:<digest>'
export WORKFLOW_V3_CANDIDATE_BUCKET='worker-v3-candidates'
export WORKFLOW_V3_CANDIDATE_PREFIX='v3/candidates'
export WORKFLOW_V3_FORMAL_BUCKET='eduassets-elegantbook'
export WORKFLOW_V3_FORMAL_PREFIX='elegantbook/v3'
```

The candidate bucket must already exist. The services do not create buckets
and must not receive permission to delete or overwrite frozen objects.

## MinIO role bootstrap and production gate

The four ordinary roles must not inherit the backend/root
`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` pair or the secret-bearing
`runtime_config.json` MinIO block. They use four distinct built-in MinIO users
as service identities:

| Role | Allowed object operations |
|---|---|
| Producer | exact `GetObject` on the four frozen source buckets and candidate prefix; conditional create in the candidate prefix |
| Evaluator | exact `GetObject` in the candidate prefix only |
| Promoter | exact `GetObject` in the candidate prefix only |
| Projector | exact `GetObject` in the candidate/formal prefixes; conditional create in the formal prefix |

Every role has an explicit `DeleteObject`/`DeleteObjectVersion` deny. The
supported MinIO policy parser does not admit IAM `NotResource`, so reads and
writes outside each exact allowlist are denied by the normal default-deny rule
and verified with real negative probes. The application uses
`If-None-Match: *` for the two allowed create
operations, then re-reads and hashes the object. An existing identical object
is an idempotent retry; an existing different object or a concurrent
same-name writer fails closed.

The Worker V3 production gate requires a MinIO release that enforces
`If-None-Match: *` on `PutObject`. The development baseline is
`minio/minio:RELEASE.2024-12-18T13-15-44Z` (image digest
`sha256:1dce27c494a16bae114774f1cec295493f3613142713130c2d22dd5696be6ad3`).
The live role probe is authoritative: an older server that accepts the
same-name conditional probe is rejected even if its health endpoint is green.

`backend/scripts/workflow_v3_minio_admin.py` is the only repository bootstrap
entrypoint. It reads the existing administrator connection from
`MINIO_ADMIN_ENDPOINT`, `MINIO_ADMIN_ACCESS_KEY`, and
`MINIO_ADMIN_SECRET_KEY`; the historical `MINIO_ENDPOINT`,
`MINIO_ACCESS_KEY`, and `MINIO_SECRET_KEY` names are accepted only by this
administrative command as an explicit compatibility input. Ordinary workers
never use those global credentials.

Inspect the intended users and policy hashes without changing MinIO or writing
a credential file:

```sh
python backend/scripts/workflow_v3_minio_admin.py \
  --dry-run \
  --runtime-env runtime/worker-v3/minio-roles.env
```

The actual bootstrap is idempotent for an existing private runtime env and
existing users. It creates or refreshes four custom policies, attaches each to
its dedicated user, writes the role credential file atomically with mode
`0600`, and runs real allow/deny probes. `--source-probe` must point to an
already-existing frozen source object; the script never seeds or changes a
source bucket.

```sh
MINIO_ADMIN_ENDPOINT='https://minio.internal.example' \
MINIO_ADMIN_ACCESS_KEY='<existing-admin-access-key>' \
MINIO_ADMIN_SECRET_KEY='<existing-admin-secret-key>' \
python backend/scripts/workflow_v3_minio_admin.py \
  --runtime-env runtime/worker-v3/minio-roles.env \
  --source-probe eduassets-minerupopo/<existing-frozen-object>
```

No secret is printed. Do not pass `--runtime-env` inside the repository unless
the path is covered by `runtime/`; never commit or copy this file into an
image. If a user already exists but the saved private credential does not
authenticate, bootstrap fails during the probes instead of rotating or
guessing the credential.

The probe matrix verifies:

- allowed Producer source read and candidate conditional create;
- read-only Evaluator/Promoter candidate reads and denied writes;
- allowed Projector candidate read and formal conditional create;
- denied source/formal/candidate cross-role access as applicable;
- denied delete for every role;
- rejected conditional same-name writes with original bytes preserved.

The probe objects are content-addressed under
`_policy-probes/` in the candidate/formal prefixes and are intentionally not
deleted by a role credential.

Run a non-admin-change verification again before every RC:

```sh
MINIO_ADMIN_ENDPOINT='https://minio.internal.example' \
MINIO_ADMIN_ACCESS_KEY='<existing-admin-access-key>' \
MINIO_ADMIN_SECRET_KEY='<existing-admin-secret-key>' \
python backend/scripts/workflow_v3_minio_admin.py \
  --verify-only \
  --require-versioning \
  --runtime-env runtime/worker-v3/minio-roles.env \
  --source-probe eduassets-minerupopo/<existing-frozen-object>
```

`--verify-only` still writes the two idempotent probe objects; it makes no IAM,
policy, credential, bucket, versioning, or retention change. The script reports
candidate/formal bucket versioning and default object-lock retention
separately. Add `--require-object-lock` only where both buckets are deliberately
configured with a default retention policy.

Versioning and object lock are not silently enabled or claimed. Versioning
preserves older versions but a normal same-name PUT still creates a new latest
version. Object lock protects retained versions and does not replace the
application's conditional create. An RC is blocked when
`--require-versioning` (or an explicitly selected `--require-object-lock`)
does not pass.

Load the generated role variables only for Compose interpolation:

```sh
set -a
. runtime/worker-v3/minio-roles.env
set +a

docker compose \
  -f docker-compose.luceon-review.yml \
  -f docker-compose.worker-v3-rc.yml \
  --profile worker-v3 \
  config
```

The RC overlay requires all four credential pairs and the four-role credential
fingerprint matrix, sets `LUCEON_ENVIRONMENT=rc`, and fails Compose
interpolation when any is absent. Runtime startup also rejects a missing or
duplicate fingerprint matrix and rejects reuse of a visible global access or
secret key. The base development file keeps empty role variables only so a
disabled V3 profile does not break the existing V2.3 development stack.

## Explicit database bootstrap

The review default is the dedicated SQLite database
`runtime/backend/workflow-v3.db`. The administration script refuses a SQLite
database containing non-V3 tables.

Check status without mutation:

```sh
docker compose \
  -f docker-compose.luceon-review.yml \
  --profile worker-v3 \
  run --no-deps --rm --entrypoint python workflow-v3-executor \
  scripts/workflow_v3_database_admin.py status
```

Create or verify tables explicitly. If an existing database is present, the
script first creates an integrity-checked backup and manifest:

```sh
docker compose \
  -f docker-compose.luceon-review.yml \
  --profile worker-v3 \
  run --no-deps --rm --entrypoint python workflow-v3-executor \
  scripts/workflow_v3_database_admin.py bootstrap \
  --confirm bootstrap-worker-v3 \
  --backup-dir /data/worker-v3-db-backups
```

For non-SQLite deployments, take and verify a vendor-native snapshot first,
then set `WORKFLOW_V3_EXTERNAL_BACKUP_EVIDENCE` to the immutable evidence path
for the explicit bootstrap. The script never attempts an unsafe generic
database dump.

## Enable the local shadow path

Recreate the backend with the feature flag, then start only the V3 profile:

```sh
WORKFLOW_V3_ENABLED=true docker compose \
  -f docker-compose.luceon-review.yml \
  up -d --force-recreate backend

WORKFLOW_V3_ENABLED=true docker compose \
  -f docker-compose.luceon-review.yml \
  --profile worker-v3 \
  up -d \
  workflow-v3-executor \
  workflow-v3-evaluator \
  workflow-v3-promoter \
  workflow-v3-projector
```

Turning off V3 does not stop V2.3:

```sh
docker compose \
  -f docker-compose.luceon-review.yml \
  --profile worker-v3 \
  stop \
  workflow-v3-executor \
  workflow-v3-evaluator \
  workflow-v3-promoter \
  workflow-v3-projector

WORKFLOW_V3_ENABLED=false docker compose \
  -f docker-compose.luceon-review.yml \
  up -d --force-recreate backend
```

## Database backup and rollback

Create a live SQLite backup:

```sh
docker compose \
  -f docker-compose.luceon-review.yml \
  --profile worker-v3 \
  run --no-deps --rm --entrypoint python workflow-v3-executor \
  scripts/workflow_v3_database_admin.py backup \
  --backup-dir /data/worker-v3-db-backups
```

Rollback is an offline restore. Stop only the V3 workers and backend, restore,
then immediately restart the backend so the development site is not left
unavailable:

```sh
docker compose -f docker-compose.luceon-review.yml --profile worker-v3 stop \
  workflow-v3-executor workflow-v3-evaluator workflow-v3-promoter \
  workflow-v3-projector backend

docker compose \
  -f docker-compose.luceon-review.yml \
  --profile worker-v3 \
  run --no-deps --rm --entrypoint python workflow-v3-executor \
  scripts/workflow_v3_database_admin.py rollback \
  --backup /data/worker-v3-db-backups/WORKER-V3-BACKUP.db \
  --safety-backup-dir /data/worker-v3-db-backups \
  --confirm restore-worker-v3 \
  --services-stopped

docker compose -f docker-compose.luceon-review.yml up -d backend
```

Never restore the V3 database over `mineru.db` or another shared database.

## Read-only UAT evidence

The UAT verdict must include browser-visible state, dedicated V3 DB state,
legacy material identity, exact MinIO objects, and runtime restart/OOM deltas.
The collector is read-only and returns exit code `2` for `incomplete` or
`failed`:

```sh
python backend/scripts/workflow_v3_uat_evidence.py \
  --cohort-id <cohort> \
  --cohort-field cohort_id \
  --workflow-db-url 'sqlite:////absolute/path/runtime/backend/workflow-v3.db' \
  --material-db-url 'sqlite:////absolute/path/runtime/backend/mineru.db' \
  --ui-snapshot evidence/ui.json \
  --runtime-snapshot evidence/runtime.json \
  --json-out evidence/worker-v3-uat.json \
  --markdown-out evidence/worker-v3-uat.md
```

Do not use `--allow-missing-ui` or `--allow-missing-runtime` for release
qualification. See `docs/worker-v3/uat-evidence-cli.md` for snapshot schemas.

## Compose verification

Default config must omit all profile services:

```sh
docker compose -f docker-compose.luceon-review.yml config
```

Review the opt-in definitions before each release:

```sh
docker compose \
  -f docker-compose.luceon-review.yml \
  --profile worker-v3 \
  config
```

Also run `backend/tests/test_workflow_v3_compose_security.py`; it statically
guards the profile, dedicated image, MinIO namespace, role, mount, credential,
and Worker-only runtime contracts.
