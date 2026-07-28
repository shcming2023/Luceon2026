# Worker V3 RC completion audit

Status: active; technical RC not yet admitted  
Audit date: 2026-07-28  
Production boundary: pure Worker V3; no Codex runtime fallback

This is a requirement-to-evidence ledger, not an RC verdict. A row is `passed`
only when the cited evidence covers the same scope as the requirement.
Static tests, an incomplete release archive, or a successful API response do
not substitute for browser-visible and runtime UAT.

## Governing decision

ADR-0003 supersedes the production parts of ADR-0002. The final production
image, release, Compose profile, API and UI must not contain the Expert Broker,
Codex credentials, App Server, cross-UID Runner, an Expert trigger, or an
automatic Codex fallback. A difficult sample terminates as `succeeded` or an
evidence-complete `needs_review`.

The immutable Worker image currently qualified for code execution is:

- source revision: `ab9806ead64e7441b0425eeca6ab98ddd9868f53`;
- runtime ID: `worker-v3-runtime-rc-ab9806e`;
- local image digest:
  `sha256:deb794e8b0f575367696b885c626e12c2cb90279e82c618e485e41f9c28dd0d4`;
- runtime identity evidence:
  `release/worker-v3/runtime/ordinary-runtime-identity.json`;
- clean-build and regression evidence:
  `release/worker-v3/runtime/ordinary-runtime-build-proof.json`.

The current release recipe now binds those exact three identities. The recipe
remains deliberately `incomplete` and is not installable as RC.

## Requirement ledger

| Requirement | Current state | Authoritative evidence or missing proof |
|---|---|---|
| Pure Worker production boundary | passed for source and image | ADR-0003; production-forbidden runtime scan; Compose/API/UI source tests |
| Worker V2.3 and V3 code/data isolation | passed in contracts and tests; live proof pending | isolated tables, prefixes and services; final cohort must prove zero V2.3 mutation |
| Immutable skill release as sole executable baseline | passed for source coverage; RC admission pending | 37 explicit sources, 215 files, 24 formal entrypoints; current recipe has four live qualification gaps |
| Twelve persisted stages | passed in code and tests | 12 Producer and 12 distinct Evaluator entrypoints; state-machine and API tests |
| Producer/Evaluator/Promotion/Projector separation | passed in code and tests; live role probe pending | four services and identities; control-plane, operation-attempt and projection tests |
| Frozen-input lineage and fail-closed admission | passed for five input packages and Stage 1 preflight | materials 1339-1343 seven-object sets and isolated qualification reports; full downstream lineage pending |
| Bounded LLM schema, telemetry and cost accounting | non-thinking live requalification strictly rejected; compact response remediation pending | v1 exposed an impossible monolithic capacity; v2 reduced material 1343 to an 820,858-byte request and made the schema provider-visible. The rebuilt non-thinking image sent the exact authorized request, but the strict page-complete response still reached 16,000 output tokens and truncated after page 185/200. The gateway rejected it as `output_truncated`; no fixture, candidate or promotion exists. The next contract must retain exhaustive coverage while avoiding repeated baseline decisions |
| Full-page visual review | contract and evaluator passed; provider qualification pending | all-page binding tests pass; clean-image provider/reviewer proof is missing |
| Deterministic ElegantBook on real material | adapter and regression tests passed; final-image qualification pending | locked-template smoke passed; unchanged-image real-book Spec 05 proof is missing |
| Independent Overleaf/XeLaTeX recompile | adapter image health passed; real ZIP compile pending | adapter digest and non-root health exist; Worker-to-adapter compile requires one temporary internal network |
| Database forward/backup/rollback | current-development SQLite evidence passed; final deployment snapshot pending | dedicated 14-table schema is ready; integrity-checked 0600 backup and isolated hash-equal rollback drill passed without touching the development DB |
| MinIO role isolation and immutable writes | current-development live probe passed; final-smoke delta pending | four-role allow/deny matrix, conditional-create rejection, delete denial and candidate/formal versioning passed against the pinned MinIO release |
| UI semantics and browser path | implementation present; browser UAT pending | V3 page distinguishes machine/spec/readiness/human state; final public browser screenshots and interactions missing |
| Five-material final batch smoke | not achieved | only frozen-input and Stage 1 offline preflight are complete; full 12-stage final-image run is required |
| Failure isolation and minimum-stage recovery | passed in tests; cohort proof pending | executor/control-plane fault tests; final five-material run must demonstrate independent outcomes and no rerun of promoted stages |
| No OOM/restart/orphan lease | not yet measured for final smoke | final runtime snapshot and UAT evidence collector output missing |
| Downloads, review rendering and clean recompile | not achieved for V3 cohort | final browser, MinIO, ZIP/PDF and compile evidence missing |
| Performance, latency, tokens and cost comparison | not achieved | final cohort telemetry and comparison report missing |
| Current development site remains usable | not yet proven after V3 RC deployment | final deployment and browser regression pending |
| RC Compose exact-image and capability wiring | static gate passed; deployment pending | four ordinary roles bind the exact Worker image, the adapter binds its exact image, role credentials interpolate, no V3 service mounts mutable skills or exposes Codex/Expert runtime material |
| Annotated tag, push and GitHub RC | not started | only after final recipe, image and smoke are sealed |

## Current release gaps

The release verifier currently reports exactly these package-admission gaps:

1. `spec02_bounded_review_capacity_unqualified`;
2. `full_page_review_evidence_provider_unqualified`;
3. `spec05_worker_v3_runtime_qualification_pending`;
4. `overleaf_adapter_image_qualification_pending`.

These are package-admission gaps, not the complete project completion list.
The live database, MinIO, browser, five-material, runtime-health, deployment and
publication proofs in the table above remain separately required.

The previous schema-visible request for material 1343 belonged to runtime
`worker-v3-runtime-rc-90ccb9a`. It failed closed after the provider consumed
part of the output budget for reasoning. Runtime
`worker-v3-runtime-rc-ab9806e` then generated canonical request
`f3d45d43...74b1f`; the separately authorized non-thinking call was executed
once. Non-thinking mode worked, but the 200-page response itself still reached
the 16,000-token output limit and truncated after page 185. The strict gateway
rejected it as `output_truncated`. It cost CNY `0.037731` using the provider's
actual cache-hit/miss breakdown. No response fixture, stage candidate,
promotion, Worker ZIP or temporary Overleaf network was created.

## Next evidence sequence

1. Replace repeated per-page baseline decisions with a compact, strict,
   exhaustive Spec 02 review contract; independently test its projection,
   capacity admission and failure modes; rebuild the exact image and request.
2. After a new exact-request authorization, execute one live call and replay
   its exact response through isolated qualification.
3. Produce a real Worker V3 ZIP and complete the approved temporary-network
   Worker-to-Overleaf compile,
   then remove the network.
4. Produce and qualify the release-scoped all-page provider/reviewer evidence.
5. Reassemble an immutable RC release with no known admission gaps.
6. Recheck the already-passed dedicated V3 database recovery and MinIO
   role-policy/versioning probes after final deployment, and capture their
   final-smoke deltas.
7. Deploy that exact image/release to the current development environment.
8. Run materials 1339-1343 without code change, rebuild or redeploy through all
   12 stages; collect page, DB, MinIO, queue, model, runtime, download and
   independent recompile evidence.
9. Close all Major findings, create the annotated RC tag, push the dedicated
   branch/tag, publish the GitHub Release Candidate and issue one unambiguous
   verdict.

Until those steps are complete, the correct conclusion is:
`技术 RC 尚未通过，目标继续执行`.
