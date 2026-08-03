# Worker V3 Skill-Native acceptance matrix

Status: approved implementation baseline
Date: 2026-07-26
Scope: LuceonWeb2026 Worker V3 only. Worker V2.3 remains readable and runnable.

## 1. Product boundary

Worker V3 is a LuceonWeb-controlled production workflow. LuceonWeb owns job
intent, authorization, state, retries, lineage, immutable artifacts, independent
evaluation, promotion, review, acceptance, and the user interface.

The skill release is the only executable and normative content-production
baseline. A run is invalid unless it binds an installed, verified, immutable
release by release ID and package SHA-256. The live `~/.codex/skills` tree,
host Codex login state, historical Worker V2.3 scripts, and mutable repository
files are not production dependencies.

Codex is not part of the production data plane or a runtime fallback. Engineers
may use it outside the deployed product to diagnose evidence and evolve a
future immutable skill release.

The deployed ordinary control-plane workers are four distinct roles:
`workflow-v3-executor`, `workflow-v3-evaluator`,
`workflow-v3-promoter`, and `workflow-v3-projector`. They run the same
dedicated immutable Worker V3 image but have separate identities, work roots,
and capabilities. The projector is the only role that materializes a promoted
final output into the material-output namespace; it is not part of producer
success.

## 2. Required states

The following concepts must never be collapsed into one status:

| Concept | Allowed values | Owner |
|---|---|---|
| Run | `queued`, `running`, `needs_review`, `failed`, `cancelled`, `succeeded` | control plane |
| Stage execution | `queued`, `running`, `produced`, `failed`, `cancelled`, `timed_out` | producer |
| Stage evaluation | `pending`, `running`, `passed`, `failed`, `needs_review` | independent evaluator |
| Promotion | `pending`, `promoted`, `rejected`, `superseded` | promotion service |
| Spec | `blocked`, `failed`, `needs_review`, `passed` | spec evaluator |
| Product acceptance | `not_ready`, `ready_for_user_acceptance`, `human_accepted`, `human_rejected`, `invalidated` | control plane/user |

`needs_review`, `handoff_ready`, or `ready_for_user_acceptance` is not
`succeeded`, `passed`, or `human_accepted`.

## 3. Twelve-stage workflow

Every stage persists its own execution, candidate artifact set, independent
evaluation, and promotion decision.

1. `intake_snapshot`
2. `source_scope_and_order`
3. `canonical_block_ledger`
4. `outline_reconstruction`
5. `semantic_annotation`
6. `template_construct_binding`
7. `frozen_render_plan`
8. `deterministic_elegantbook`
9. `readonly_latex_audit`
10. `independent_full_page_review`
11. `delivery_recompile`
12. `ready_for_user_acceptance`

The next stage may consume only the exact artifact SHA values named by a
`promoted` decision for the immediately preceding stage. A producer's own
success flag is never sufficient.

## 4. Hard acceptance gates

### Release and runtime

- A release manifest is schema-valid and contains file hashes, archive hash,
  entrypoint allowlist, schemas, prompts, validators, template identity,
  dependency/runtime identity, tests, and known gaps.
- The package is extracted read-only and verified before a run is admitted.
- Unknown workflow, release, stage, entrypoint, schema, prompt, model policy, or
  runtime identity fails closed.
- Production execution does not read active `~/.codex/skills`.
- All four ordinary roles run the release-qualified dedicated Worker V3 image;
  none silently falls back to the backend image.
- Formal, migration, legacy, diagnostic, and prohibited entrypoints are
  explicitly classified. Only formal entrypoints are admitted on the ordinary
  path.
- Each registered stage has exactly one formal candidate-only Producer and one
  distinct formal read-only Evaluator executable. A single-entrypoint release,
  duplicate role, or role/permission/success-semantic mismatch is not
  executable.

### Producer, evaluator, and promotion

- Producer writes only a new candidate prefix and cannot create a promotion.
- The default isolated candidate namespace is
  `worker-v3-candidates/v3/candidates/...`.
- Evaluator has read-only access to input and candidate evidence.
- Producer, Evaluator, Promoter, and Projector use four distinct MinIO
  credential pairs. RC/production startup rejects missing role credentials,
  duplicate credential fingerprints, a credential that does not match its
  declared role, and reuse of a visible global MinIO access or secret key.
- Evaluator and Promoter can read only the candidate prefix. Projector can read
  candidate/formal evidence and write only the formal V3 prefix; it cannot read
  frozen source buckets or write candidates. Every role is denied object and
  object-version deletion.
- Candidate and formal writers use an actual conditional create
  (`If-None-Match: *`). Idempotent identical retries are accepted only after an
  exact re-read; conflicting or concurrent same-name writes fail closed.
- RC evidence includes a live role-policy matrix probe and enabled
  candidate/formal bucket versioning. Object-lock retention is a separate,
  explicitly verified optional gate and is never inferred from versioning.
- Promotion is unique for `(run, stage, candidate SHA set)` and requires the
  exact passing evaluation.
- A failed or review-required evaluation cannot be promoted.
- Duplicate messages, stale leases, retries, and worker restarts do not create
  duplicate active promotion decisions or change immutable artifacts.
- The material-output projection is written only after final promotion through
  an idempotent outbox/reconciler.
- The default formal V3 namespace is
  `eduassets-elegantbook/elegantbook/v3/...`; it must not overlap the V2.3
  formal prefix.

### Lineage and evidence

- Original PDF, MinerU frozen manifest/marker, Popo frozen manifest/marker,
  material identity, input snapshot, release, template, code, prompt, runtime,
  model calls, candidate, evaluation, promotion, ZIP, PDF, page
  ledger, and acceptance commit form a verifiable acyclic lineage.
- No historical V2.3 output is relabelled as V3.
- A changed upstream hash invalidates descendants; frozen ancestors are not
  rerun.
- Every source atom is included, source-backed excluded, or open and blocking.
- The canonical decision sequence is evidence E -> decision index D -> ledger
  or build L/B -> commit manifest M. D cannot reference a future descendant.

### LLM

- All ordinary LLM calls use one gateway and a release-bound prompt and output
  JSON Schema.
- Calls persist provider, model, request parameters, input evidence/hash,
  response ID/raw response hash, parsed result hash, usage, latency, retry,
  error, prompt ID/version/hash, release ID, and stage.
- The model returns bounded decisions or candidates only. Deterministic
  validators own acceptance.
- Malformed JSON, schema mismatch, timeout, 401/403/429/5xx, budget exhaustion,
  conflicting decisions, or missing usage/evidence fails closed.
- Model fallback cannot silently change model, prompt, skill release, or
  acceptance policy.

### Difficult-sample closure

- The formal terminal state is `succeeded` or evidence-complete
  `needs_review`.
- `needs_review` binds every blocking finding, its evidence and handoff, and
  the earliest permitted recovery stage.
- A human resolution starts the ordinary Worker at that stage. It does not
  inject an automatically generated candidate or bypass the Evaluator.
- Production API, UI, Compose, image and release contain no Codex Expert
  trigger, Broker, App Server, Runner or credential.

### ElegantBook delivery

- The approved template ZIP and `elegantbook.cls` hashes match the release.
- Generated body does not define or call template-local custom APIs.
- Formal V3 uses exactly one or two delivery volumes frozen before rendering.
- Each volume contains one root `main.tex`, one
  `body/generated-body.tex`, semantic unit/part leaves, approved template files,
  and referenced assets only.
- Root `main.tex`, loader, and every body leaf are `< 900,000` bytes.
- ZIP is `< 50,000,000` bytes; ordinary file entities are `< 2,000`; each
  raster image is `< 1,000,000` bytes. Equality fails.
- Producer and evaluator independently recompute these limits.
- Final ZIP is the exact clean-build input. XeLaTeX/Overleaf target compilation
  exits zero, converges, has no TeX errors, missing assets, missing glyphs,
  unapproved font substitution, or blocking warnings.
- Every final PDF page is rendered and bound to the exact PDF SHA-256.

### User experience and semantics

- UI shows workflow/release/stage versions, candidate/evaluation/promotion,
  attempts, heartbeat, last progress, lineage IDs/hashes, bounded model calls,
  findings, recovery point, and acceptance state.
- UI never describes a producer result as independently passed.
- UI never describes `needs_review` as completed.
- Source PDF, MinerU, MinerU+Popo, candidate/final ZIP, and PDF downloads are
  available according to permission and actual frozen/promoted state.
- Review pages render the correct source and compiled PDFs without 401, 404,
  502, timeout, or cross-material mismatch.

## 5. Fault-injection matrix

At minimum, tests must cover:

- package/file/template/prompt/schema hash drift;
- unknown version, prohibited entrypoint, missing evaluator, duplicate stage
  role, and producer/evaluator role swap;
- producer crash before/after candidate upload;
- evaluator crash, disagreement, failure, and review-required;
- duplicate event, duplicate queue message, stale lease, and worker restart;
- unpromoted or wrong-lineage downstream input;
- MinIO partial write, missing marker/manifest, and hash mismatch;
- database/outbox second-commit failure and reconciliation;
- running cancellation and timeout;
- strict limit equality for ZIP, files, image, and TeX shard;
- malformed/timeout/rate-limited LLM response and budget exhaustion;
- human-resolution hash drift, incomplete handoff, wrong recovery stage, and
  attempted automated-candidate injection;
- V2.3 and V3 isolation, shadow mode with zero material-output side effects;
- semantic state mapping across backend API and frontend labels.

## 6. Release and UAT exit criteria

Technical RC is allowed only when:

1. the package and container image are immutable and independently verified;
2. migrations have backup, forward verification, and rollback evidence;
3. focused and full regression suites pass;
4. at least five diverse, already frozen Popo materials run in shadow/batch
   mode with one-sample failure isolation;
5. the final sealed image completes one no-code-change, no-rebuild,
   no-redeploy batch smoke;
6. page, database, MinIO, queue, bounded model, review, download, and independent
   recompile evidence agree;
7. no OOM/restart or orphaned running/lease marker remains;
8. all Major blockers are closed or the conclusion is `生产阻断`.

The automated conclusion may be `技术 RC 通过，等待用户接受`.
Only an explicit user action may create `human_accepted`.

The canonical cross-layer evidence report is collected by
`backend/scripts/workflow_v3_uat_evidence.py`. For a release verdict it must
receive both a browser-derived UI snapshot and a runtime snapshot containing
restart deltas/OOM state; permissive missing-snapshot flags are diagnostic only.
