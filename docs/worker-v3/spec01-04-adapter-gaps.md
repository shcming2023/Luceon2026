# Worker V3 Spec 01–04 adapter audit

Status: **implemented; release qualification still fail closed**

This document supersedes the earlier 4-of-7 producer-gap snapshot. The first
seven Worker V3 stages now have stage-atomic, release-local Producer adapters
and distinct read-only Evaluator entrypoints:

1. `intake_snapshot`
2. `source_scope_and_order`
3. `canonical_block_ledger`
4. `outline_reconstruction`
5. `semantic_annotation`
6. `template_construct_binding`
7. `frozen_render_plan`

All entrypoints use the same JSON CLI:

```text
ENTRYPOINT --request request.json --result result.json
```

The request protocol is `luceon.worker-v3-stage-request/v1`; the result protocol
is `luceon.worker-v3-stage-result/v1`. A successful Producer emits only an
immutable candidate plus candidate evidence. It cannot evaluate, promote,
project a material output, publish, or accept that candidate.

## Current coverage

| Stage | Producer boundary | Current disposition |
|---|---|---|
| `intake_snapshot` | deterministic immutable inventory over exact PDF/MinerU/Popo/template evidence | formal Producer implemented |
| `source_scope_and_order` | deterministic commit over one release-bound bounded review | formal Producer implemented |
| `canonical_block_ledger` | deterministic canonical ledger commit over one release-bound bounded review | formal Producer implemented |
| `outline_reconstruction` | bounded structural review, then deterministic Spec 04-A kernel | formal Producer implemented |
| `semantic_annotation` | bounded semantic review, then deterministic Spec 04-B kernel | formal Producer implemented |
| `template_construct_binding` | bounded construct review, then deterministic Spec 04-C kernel | formal Producer implemented |
| `frozen_render_plan` | bounded render policy, then deterministic Spec 04-D kernel | formal Producer implemented |

Formal Producer coverage in this scope is **7 of 7**. This is implementation
coverage, not a release or UAT verdict. Each stage still requires its packaged
Evaluator, verified immutable release, dedicated runtime image, independent
evaluation/promotion, and downstream shadow evidence.

## Required request bindings

Every input artifact is explicitly enumerated by role, kind, relative attempt
path, size, SHA-256, and `read_only=true`. A non-initial stage also binds:

- the exact promoted predecessor artifact SHA-256 values;
- predecessor evaluation SHA-256;
- predecessor promotion-manifest SHA-256;
- a separately materialized promotion manifest with that exact SHA-256.

The Executor now materializes this complete frozen `input_artifacts` set rather
than a single generic input. It also materializes predecessor promotion
evidence and the stage-specific bounded-decision bundle when required. Adapters
must reject an incomplete shape; they may not infer missing evidence from a
database, MinIO listing, host path, or mutable skill installation.

Stages with bounded LLM decisions additionally bind the release prompt identity
and hash, output-schema identity and hash, raw response and canonical result
hashes, attributable usage, and LLM call-audit hash. The release declares the
single admitted provider/model and deterministic request parameters. Drift,
malformed output, absent raw response/usage, timeout, transport error, or
budget exhaustion fails closed before the deterministic kernel commits a
candidate.

The protocol accepts no request-selected executable, shell, arbitrary argv,
secret, absolute host path, active `~/.codex/skills` path, DB connection, MinIO
connection, or Docker socket. Producer kernels are selected by code from fixed
release-relative paths and their bytes are release-hash bound.

## Remaining qualification boundary

The earlier missing Spec 01/02/03 atomic Producer blockers are closed in source
and regression coverage. They must not be reintroduced as “known current
gaps.” The current executable verdict remains controlled by
`release/worker-v3/recipe.current-audit.json` and its live `--verify-only`
findings. In particular, source-level adapter completion cannot close unrelated
full-page provider, final-image Spec 05, five-material UAT, or sealed-smoke
evidence.
