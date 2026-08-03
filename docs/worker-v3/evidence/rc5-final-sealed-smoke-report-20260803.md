# Worker V3 RC5 final sealed smoke

## Verdict

**Technical RC passed; waiting for user acceptance.**

The five-material sealed smoke was created from the authenticated public page
after the final RC5 images were deployed. No code, image, release package or
service was rebuilt or redeployed between browser submission and the last job
terminal state. All five jobs ended in evidence-complete `needs_review`, which
is an allowed hard-sample terminal; none was counted as machine success,
readiness, formal delivery or human acceptance.

## Immutable bindings

- Source revision: `5d315142fdfd6eebaffe1dfe4ebb9e0fc51e2a76`
- Release ID: `worker-v3-skill-source-audit-20260803-5d31514`
- Release version / manifest: `3.0.0-rc.5` / `3516dce85d97a70f0ad42f1f2779c155006764d5b0f64645f018a03274175488`
- Worker image: `sha256:4b3894462fef3f3333dcc1d1ec1b4accea8b9e4922a3a5716eec3f9e585722d7`
- Backend image: `sha256:b672ded14233a9e1068e7b1e612b68fec79b0b1bb12940c50738516584095035`
- Runtime identity: `9260368b83c45e805e7b9b10861d6126ed1661108469d04a1bb6b33263e5685a`
- Template tree: `777d871d2c832734ea1aea0434fb9e5dcae1ac354e39b245ed82943fcceb4932`
- Immutable package archive SHA-256: `6aed25835d273a1863625bba53807d31a0de8fbc5d2c7cc9c0c5855c1d2bf045`
- MinIO package object: `worker-v3-releases/worker-v3/3.0.0-rc.5/worker-v3-skill-source-audit-20260803-5d31514-6aed25835d273a18.tar.gz`

The immutable RC5 package retains its pre-smoke admission ledger. This report
and the canonical collector output are post-package external qualification
evidence; the already registered package was not overwritten.

## Five independent terminals

| Material | Job | Terminal responsibility | LLM calls | Cost (micro CNY) | UAT |
|---|---|---|---:|---:|---|
| #1339 Learners Book 7 | `c6f01dd4-63de-4dae-acae-a6f7aa42bcb5` | `canonical_block_ledger` / `spec03_source_region_review_open` | 2 | 46,051 | passed |
| #1340 Learners Book 8 | `4016c3b9-b18f-4cc7-9bad-fac7e788bc08` | `deterministic_elegantbook` / `spec05_compile_blocking_review_open` | 6 | 39,414 | passed |
| #1341 Learners Book 9 | `33c99617-5a86-4ad4-92ad-4c09612d3c67` | `canonical_block_ledger` / `spec03_source_region_review_open` | 2 | 45,606 | passed |
| #1342 Workbook 7 | `fc306a4f-dcd9-494d-a386-58106fcfbdad` | `deterministic_elegantbook` / `spec05_compile_blocking_review_open` | 6 | 39,868 | passed |
| #1343 Workbook 9 | `78d680b3-1bbb-4a1d-8555-74982f6043c5` | `deterministic_elegantbook` / `spec05_compile_warning_review_open` | 6 | 41,015 | passed |

- Browser submission: `2026-08-03T10:02:16Z`–`10:02:17Z`
- Last terminal: `2026-08-03T10:31:37Z`
- Batch elapsed: about 29 minutes 21 seconds
- Model calls: 22 succeeded, 0 failed, 0 left running
- Model cost: 211,954 micro CNY
- Operations: 55 succeeded, 0 active or failed
- Projection outbox: 0; no formal output or acceptance was fabricated
- Workers after the run: producer, evaluator, promoter and projector all idle

For every terminal, the independent evaluation names a blocking finding,
binds SHA-addressed evidence, identifies the responsible stage, supplies a
candidate and handoff, and resumes only from that same minimum stage. Every
downstream stage remained untouched.

## Canonical evidence and runtime

- Canonical read-only collector: 5/5 passed, 0 defect blockers, 0 evidence-gap
  blockers, 0 warnings.
- Exact MinIO verification used immutable SHA metadata plus size or streamed
  SHA-256. Page, DB, material lineage, release and object state were consistent.
- Backend and four ordinary Worker roles remained healthy with restart delta 0
  and no OOM.
- Exact-image regression, with network disabled and UID 10003: 426 passed,
  2 skipped, 0 failed.
- The locked template smoke had already passed XeLaTeX, qpdf and pdfinfo in the
  exact image. The sealed five samples stopped earlier by design, so they did
  not create formal ZIP/PDF delivery or a human acceptance decision.
- The temporary Worker-to-Overleaf Adapter network attachment was removed after
  the smoke. The adapter remains isolated for a future explicitly connected
  compile.

## Storage cleanup

Before cleanup, the host had about 39 GiB free and historical Worker V3 local
work directories occupied roughly 63 GiB. Nine older terminal jobs were
eligible for cleanup only after all 47 recorded immutable candidate objects
were re-stat'd in MinIO with matching sizes. Only those local reproducible work
directories were deleted. The current five jobs, DB, MinIO, historical formal
outputs and release package were preserved; free space rose to about 74 GiB
during the run and settled near 70 GiB after the final sample completed.

## Browser evidence

- [Batch selection](rc5-five-material-browser-submit-20260803.png)
- [Running list](rc5-five-material-browser-running-20260803.png)
- [Terminal list](rc5-five-material-browser-terminal-20260803.png)
- [#1339 detail](rc5-browser-detail-1339-20260803.png)
- [#1340 detail](rc5-browser-detail-1340-20260803.png)
- [#1341 detail](rc5-browser-detail-1341-20260803.png)
- [#1342 detail](rc5-browser-detail-1342-20260803.png)
- [#1343 detail](rc5-browser-detail-1343-20260803.png)

Machine-readable sources:

- `rc5-five-material-ui-snapshot-20260803.json`
- `rc5-five-material-runtime-snapshot-20260803.json`
- `rc5-five-material-uat-20260803.json`
- `rc5-exact-image-regression-20260803.json`

## Remaining boundary

There is no Major production blocker in the Worker V3 technical RC. The five
source-specific findings remain intentional human work, not unresolved system
defects. `human_accepted` remains pending and must be created only by a real
user decision; this report does not promote RC5 to a final production release.
