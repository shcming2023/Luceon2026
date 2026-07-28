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

- source revision: `521fee15ee857cf07115eb77d369c7597c2887ab`;
- runtime ID: `worker-v3-runtime-rc-521fee1`;
- local image digest:
  `sha256:6f622548b136c302256cc7174cb2cb889e1790c06f42dbd2528d285711325b11`;
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
| Bounded LLM schema, telemetry and cost accounting | two strict rejections proved; schema transport fix passed offline; live requalification pending | v1 exposed an impossible monolithic capacity; v2 reduced material 1343 to 820,827 request bytes and 25,910 minimum response bytes, then exposed that the provider payload omitted the already-bound output schema. The transport now sends canonical input plus output_schema and still fails closed locally |
| Full-page visual review | contract and evaluator passed; provider qualification pending | all-page binding tests pass; clean-image provider/reviewer proof is missing |
| Deterministic ElegantBook on real material | adapter and regression tests passed; final-image qualification pending | locked-template smoke passed; unchanged-image real-book Spec 05 proof is missing |
| Independent Overleaf/XeLaTeX recompile | adapter image health passed; real ZIP compile pending | adapter digest and non-root health exist; Worker-to-adapter compile requires one temporary internal network |
| Database forward/backup/rollback | implementation and tests passed; deployment evidence pending | explicit admin CLI and rollback tests; current development V3 DB backup/bootstrap/restore evidence not yet captured |
| MinIO role isolation and immutable writes | implementation/tests passed; live probe pending | role policy and conditional-create tests; final candidate/formal bucket probe and versioning evidence missing |
| UI semantics and browser path | implementation present; browser UAT pending | V3 page distinguishes machine/spec/readiness/human state; final public browser screenshots and interactions missing |
| Five-material final batch smoke | not achieved | only frozen-input and Stage 1 offline preflight are complete; full 12-stage final-image run is required |
| Failure isolation and minimum-stage recovery | passed in tests; cohort proof pending | executor/control-plane fault tests; final five-material run must demonstrate independent outcomes and no rerun of promoted stages |
| No OOM/restart/orphan lease | not yet measured for final smoke | final runtime snapshot and UAT evidence collector output missing |
| Downloads, review rendering and clean recompile | not achieved for V3 cohort | final browser, MinIO, ZIP/PDF and compile evidence missing |
| Performance, latency, tokens and cost comparison | not achieved | final cohort telemetry and comparison report missing |
| Current development site remains usable | not yet proven after V3 RC deployment | final deployment and browser regression pending |
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

## Next evidence sequence

1. Rebuild the exact Worker V3 image with the compact Spec 02 v2 contract and
   the schema-visible provider transport. After separate authorization,
   execute one new v2 live call and replay its exact response through isolated
   qualification.
2. Produce a real Worker V3 ZIP and complete the approved temporary-network
   Worker-to-Overleaf compile,
   then remove the network.
3. Produce and qualify the release-scoped all-page provider/reviewer evidence.
4. Reassemble an immutable RC release with no known admission gaps.
5. Bootstrap the dedicated V3 database with backup/rollback evidence and run
   the live MinIO role-policy/versioning probe.
6. Deploy that exact image/release to the current development environment.
7. Run materials 1339-1343 without code change, rebuild or redeploy through all
   12 stages; collect page, DB, MinIO, queue, model, runtime, download and
   independent recompile evidence.
8. Close all Major findings, create the annotated RC tag, push the dedicated
   branch/tag, publish the GitHub Release Candidate and issue one unambiguous
   verdict.

Until those steps are complete, the correct conclusion is:
`技术 RC 尚未通过，目标继续执行`.
