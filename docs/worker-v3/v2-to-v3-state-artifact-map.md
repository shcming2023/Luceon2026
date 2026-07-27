# Worker V2.3 to V3 state and artifact map

Status: normative migration boundary  
Date: 2026-07-27

Worker V3 is not an in-place upgrade of a V2.3 job. This document describes
coexistence and user-visible mapping; it does not authorize relabelling,
copying or promoting a historical V2.3 result as V3.

## Identity boundary

| Concern | V2.3 | Worker V3 |
|---|---|---|
| Job identity | historical Worker job ID | `workflow_v3_jobs.public_id` |
| Workflow version | `worker-v2.3` and historical variants | release-bound V3 workflow version |
| Execution baseline | historical Worker implementation | one verified immutable skill release |
| State owner | historical Worker/job tables | dedicated `workflow_v3_*` tables |
| Candidate namespace | historical output/work prefixes | `worker-v3-candidates/v3/candidates/...` |
| Formal namespace | historical ElegantBook prefixes | `eduassets-elegantbook/elegantbook/v3/...` |
| Output registry origin | `worker_v2` | `worker_v3` |
| Current-output write | historical publish path | final Promotion plus Projector outbox only |

The same `material_pk`, `material_id` and frozen Popo manifest may be referenced
by both versions, but their job, release, stage, candidate, promotion and
output identities remain different.

## State mapping

V2.3 labels are contextual hints only. They do not migrate into V3 state.

| Historical/user concept | V3 representation | Rule |
|---|---|---|
| queued | job `machine_status=queued` plus Stage 1 queued/pending | A release and frozen-input binding must already exist |
| running | job `machine_status=running` and one owned operation lease | A UI label without a live lease/heartbeat is insufficient |
| stage succeeded | Producer candidate, passing Evaluation and Promotion | Producer exit zero alone is not stage success |
| failed | job/stage `machine_status=failed` with error and earliest retry stage | Previously promoted ancestors remain immutable |
| needs review | `machine_status=needs_review`, `spec_status=needs_review`, bound findings and handoff | Never map to completed, passed or published |
| completed/succeeded | job `machine_status=succeeded` and all twelve stage promotions | Does not imply human acceptance |
| quality passed | `spec_status=passed` | Separate from execution success |
| ready for acceptance | `readiness_status=ready` | Produced only by the promoted Stage 12 result |
| accepted/published | explicit `human_acceptance_status=accepted` plus effective projection | Only a real user decision can create this state |
| rejected | explicit `human_acceptance_status=rejected` | Does not rewrite machine or spec history |

The API exposes the four dimensions separately:

- `machine_status`;
- `spec_status`;
- `readiness_status`;
- `human_acceptance_status`.

No compatibility mapper may collapse them into one green badge.

## Artifact mapping

| Artifact | Reuse policy |
|---|---|
| Source PDF | Reuse read-only by exact bucket/object/SHA |
| MinerU manifest, archive and frozen marker | Reuse read-only; never regenerate as part of V3 retry |
| Popo manifest, archive and frozen marker | Reuse read-only as the V3 intake source |
| V2.3 intermediate work | Historical evidence only; never a V3 promoted input |
| V2.3 ZIP/PDF | Comparison or baseline evidence only |
| V2.3 `MaterialOutput` | Preserve unchanged with `origin=worker_v2` or its historical origin |
| V3 candidate | New immutable object under the V3 candidate prefix |
| V3 promoted stage artifact | Exact candidate SHA named by a unique V3 Promotion |
| V3 formal ZIP/PDF/manifest | New object under the V3 formal prefix |
| V3 `MaterialOutput` | New registry row with `origin=worker_v3`, created through Projector |

V3 may compare its result with V2.3, but the comparison does not create a
promotion and cannot make either output current.

## Shadow and cutover behavior

1. A V3 shadow job reads the already frozen source assets and writes only V3
   candidate objects.
2. Shadow execution must have zero `MaterialOutput` and material-stage side
   effects.
3. A final V3 promotion creates a Projector outbox record. Only the Projector
   may materialize the formal manifest and register a V3 output.
4. `human_accepted` remains false until a user accepts the ready V3 delivery.
5. Selecting a V3 output as current does not delete or mutate a V2.3 output.
6. Disabling the V3 feature flag stops new V3 claims without changing V2.3
   jobs, outputs or routes.

## Retry and recovery boundary

- A V3 retry starts at the earliest failed or resolved-review stage.
- The retry consumes the exact preceding promoted SHA and a new generation
  when a human resolution is involved.
- Promoted V3 ancestors and all frozen PDF/MinerU/Popo assets are not rerun.
- A V2.3 job ID, candidate or output cannot be supplied as a V3 recovery
  generation.
- Rollback means disabling V3 and restoring its dedicated control-plane
  database if necessary; it never deletes V3 MinIO evidence or rewrites V2.3.

## Acceptance probes

The final cohort must prove:

- creating and running V3 jobs does not change V2.3 rows or objects;
- V2.3 and V3 outputs appear as distinct lineage entries;
- a V3 candidate is not shown as published or current;
- `needs_review` does not advance material status to completed;
- accepting one V3 output preserves all historical V2.3 output rows;
- disabling the V3 profile leaves the existing development site and V2.3 path
  usable.
