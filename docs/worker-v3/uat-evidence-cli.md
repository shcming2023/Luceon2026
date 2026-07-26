# Worker V3 UAT evidence CLI

`backend/scripts/workflow_v3_uat_evidence.py` is a read-only evidence collector.
It queries the dedicated Worker V3 database, the legacy/material database, and
exact MinIO object identities. It does not call workflow APIs, update state,
promote candidates, or write objects.

Select either explicit jobs:

```bash
python backend/scripts/workflow_v3_uat_evidence.py \
  --job-id 00000000-0000-4000-8000-000000000001 \
  --job-id 00000000-0000-4000-8000-000000000002 \
  --ui-snapshot evidence/ui.json \
  --runtime-snapshot evidence/runtime.json \
  --json-out evidence/worker-v3-uat.json \
  --markdown-out evidence/worker-v3-uat.md
```

or a generic cohort value stored in the job payload:

```bash
python backend/scripts/workflow_v3_uat_evidence.py \
  --cohort-id uat-rc1 \
  --cohort-field uat.cohort_id \
  --ui-snapshot evidence/ui.json \
  --runtime-snapshot evidence/runtime.json \
  --json-out evidence/worker-v3-uat.json \
  --markdown-out evidence/worker-v3-uat.md
```

The default verdict is fail-closed. Missing UI/container evidence produces
`incomplete`; an observed state, lineage, MinIO, lease, OOM/restart, or delivery
contradiction produces `failed`.

For RC/UAT qualification, do not use `--allow-missing-ui` or
`--allow-missing-runtime`; those switches are diagnostic only. A report file
can still be written when the command returns exit code `2`, so the caller must
check both the process exit code and `summary.status`.

## UI snapshot

The browser operator captures canonical values from the rendered task rows:

```json
{
  "schema": "luceon.worker-v3-ui-snapshot/v1",
  "jobs": [
    {
      "id": "00000000-0000-4000-8000-000000000001",
      "material_pk": "42",
      "material_id": "pdf-example",
      "filename": "example.pdf",
      "popo_run_id": "popo-example-run",
      "skill_release_version": "worker-v3.0.0",
      "machine_status": "succeeded",
      "spec_status": "passed",
      "readiness_status": "ready",
      "human_acceptance_status": "pending",
      "current_stage_key": "ready_for_user_acceptance"
    }
  ]
}
```

The identity and status fields are all required for exact UI/DB lineage
comparison. An API response is not a substitute for this browser-visible
snapshot.

## Runtime snapshot

Capture restart deltas over the UAT window, not only lifetime restart totals:

```json
{
  "schema": "luceon.worker-v3-runtime-snapshot/v1",
  "containers": [
    {
      "name": "workflow-v3-executor",
      "status": "running",
      "health": "healthy",
      "restart_count": 0,
      "restart_delta": 0,
      "oom_killed": false
    }
  ]
}
```

`restart_delta > 0`, `oom_killed=true`, or an unhealthy/dead state is blocking.
The snapshot must cover all running ordinary roles:
`workflow-v3-executor`, `workflow-v3-evaluator`,
`workflow-v3-promoter`, and `workflow-v3-projector`. Worker role heartbeats are
read independently from the Worker V3 database. No Codex/Expert runtime is part
of production evidence.

## Exact-object verification

Worker V3 candidate and formal writers attach immutable
`x-amz-meta-luceon-sha256` metadata after verifying uploaded bytes. The
collector accepts matching SHA metadata plus exact size. Objects without that
metadata, and every JSON object whose contents must be inspected, are streamed
and hashed. The report records the verification method for each object.

The formal manifest must expose per-volume LaTeX ZIP, compiled PDF, compile log,
and successful `latexmk-xelatex` recompile report. Missing delivery evidence
does not pass.

The deployed default namespaces are
`worker-v3-candidates/v3/candidates/...` for candidates and
`eduassets-elegantbook/elegantbook/v3/...` for projected formal outputs. The
collector verifies the exact object names carried by lineage; it must not infer
an object by listing a bucket or by falling back to a V2.3 prefix.
