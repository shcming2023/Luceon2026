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

- source revision: `9447aedb688dd7d0f4c78978a4e5d95892554675`;
- runtime ID: `worker-v3-runtime-rc-9447aed`;
- local image digest:
  `sha256:e35e466e0e7b1f7a82404b577683617d264aba7b23bf8d6fbaea92fe407fa9a9`;
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
| Bounded LLM schema, telemetry and cost accounting | passed for final frozen executable candidate; final archive attestation reassembly pending | v1 exposed an impossible monolithic capacity; v2 made the schema provider-visible but repeated full fields and truncated after page 185/200. v3 retains one disposition per physical page, moves full fields to exact overrides, projects the deterministic baseline locally, and reduces the 200-page minimum complete response from 25,910 to 11,530 bytes. Separately authorized requests `907c4dac...d350` and final-release binding `82de64d1...7f89` were each called exactly once. Final-image no-network replay passed Spec 02 Producer, Evaluator and Promotion, then failed closed before any Spec 03 provider call |
| Full-page visual review | contract and evaluator passed; exact runtime model binding corrected; provider qualification pending | all-page binding tests pass; the current runtime now binds `qwen3.7-plus-2026-05-26`, but no clean-image provider/reviewer request has been authorized or sent |
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

The currently frozen incomplete archive still reports exactly these
package-admission gaps:

1. `spec02_bounded_review_capacity_unqualified`;
2. `full_page_review_evidence_provider_unqualified`;
3. `spec05_worker_v3_runtime_qualification_pending`;
4. `overleaf_adapter_image_qualification_pending`.

The first declaration is now resolved by external final-image qualification
evidence but has deliberately not been edited inside the already frozen
archive. It will be removed when the final evidence-bearing RC source is
reassembled; rewriting it now would create a new release manifest and
invalidate the continuation request sequence. The other three declarations
remain unresolved.

These are package-admission declarations, not the complete project completion list.
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

Request `907c4dac...d350` was explicitly authorized and called exactly once.
DeepSeek returned HTTP 200 with `finish_reason=stop`, response ID
`531b98ca-93c6-477c-b95d-a4b365c54832`, and 289,715 total tokens; there was no
retry. The external one-shot harness initially rejected the complete response
because it reconstructed an `allowed_choices` policy not used by the production
Executor. Immutable no-network replay with the actual production policy passed
with parsed-result SHA-256 `6934b38a...652b`.

The subsequent exact qualification exposed two real release-binding defects:
the adapter conflated a raw schema-file hash with the gateway's canonical JSON
hash, and the first correction then propagated the canonical hash into
non-LLM candidate lineage where the raw file hash is required. Commits
`38e8a60` and `9447aed` fix those responsibilities separately and add regression
coverage. Runtime `worker-v3-runtime-rc-9447aed` passed runtime identity, 335
Worker V3 tests (two skipped), and the locked-template
XeLaTeX/qpdf/pdfinfo smoke.

The rebuilt incomplete release archive has SHA-256 `e92eaa13...23c0`, tree
SHA-256 `1e8fc01d...3714`, and manifest SHA-256 `249412df...55db`. A second clean
assembly produced the same archive and tree hashes. Its isolated
recapture proves Stage 1 Producer, Evaluator and Promotion all pass. Because
the fixed release identity and deterministic call ID are part of the request,
the final request is `82de64d1...7f89`, not the previously authorized
`907c4dac...d350`. The model-visible prompt, input, schema, provider, model and
parameters are equal, but the exact-response protocol forbids rebinding across
release identities. The final request hash is deliberately kept only in
external audit evidence, not inside its own release recipe, to avoid recursive
manifest drift.

Request `82de64d1...7f89` was separately authorized and called exactly once.
It returned HTTP 200 with `finish_reason=stop`, response ID
`66742af4-b33e-4c7b-bdd4-b264998b6d96`, and 289,715 total tokens; there was no
retry. The response used 285,824 cache-hit input tokens, 74 cache-miss input
tokens and 3,817 output tokens. Its raw-response SHA-256 is
`70be002f...c726`; its parsed-result SHA-256 remains
`6934b38a...652b`.

Final-image replay used Docker network `none`, consumed that one exact fixture
once, and passed `intake_snapshot` and `source_scope_and_order` through
Producer, independent Evaluator and Promotion. The latter produced promoted
candidate SHA-256 `0004210c...3b35`. Replay then failed closed at
`canonical_block_ledger` with `qualification_fixture_missing`; it made no
Spec 03 provider call, wrote no production state and promoted no release. The
qualification report SHA-256 is `e474a807...aff9`. Evidence is recorded in
`docs/worker-v3/evidence/deepseek-1343-spec02-v3-live-and-release-rebind-20260729.json`.

The same preflight found a separate future Stage 10 blocker: the current
development runtime selected the alias `qwen3.7-plus`, while the immutable
release requires `qwen3.7-plus-2026-05-26`. A 0600 hash-equal backup was made,
only `models.vision.model` was changed, and the running backend reloaded the
exact release model while both credentials remained configured. No visual
request has been sent; provider qualification remains a separate authorized
step.

## Next evidence sequence

1. After a new exact-request authorization, execute Spec 03 request
   `9e679330...8b79` exactly once and replay only from the smallest failed
   stage. The consumed authorizations for `907c4dac...d350` and
   `82de64d1...7f89` must not be reused.
2. Continue the exact-request/fail-closed qualification sequence until the
   unchanged final image produces a real Worker V3 ZIP.
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

The one-shot Overleaf qualification client and lifecycle wrapper are prepared
outside the release source. They bind the exact Worker and adapter image
digests, reject a missing or hash-drifted ZIP before creating Docker objects,
use one isolated internal network with no published port, and remove the
adapter container, network and temporary volume on exit. The missing-ZIP guard
was exercised without creating any network, container or volume. It has not
been run against a substitute ZIP: the authorized connection remains
conditional on a real Worker-produced ZIP from the strict qualification chain.
