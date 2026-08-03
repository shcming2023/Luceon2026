# ADR-0002: Codex Expert Lane

Status: superseded for production; retained as architecture experiment record
Date: 2026-07-26

> Superseded by ADR-0003. None of the broker, App Server, cross-UID runner,
> credential, online proof, API, UI, Compose, image, release, or qualification
> mechanisms described below are admitted to the Worker V3 production
> baseline. This document records the evaluated alternative only.

## Context

The current Codex job path invokes a host CLI with broad sandbox bypass,
captures output only after completion, and combines production, validation, and
promotion. It does not provide a production-grade event, cancellation, resume,
or permission ledger.

Codex is valuable for difficult samples, anomaly diagnosis, candidate repair,
independent review, and skill evolution. It must not become a second,
unobservable control plane.

## Decision

Use a dedicated, credential-owning `codex-expert-broker` identity with the
pinned Codex App Server stdio JSON-RPC runtime. App Server is the only
production Expert adapter because it exposes the thread/turn event stream,
approval requests, cancellation, resume, and terminal reconciliation required
by Luceon's existing control plane. The Python Codex SDK adapter is retained
only as a compatibility/test harness and is not an admitted production path.

The service:

- consumes only Luceon-created, release-bound ExpertRun operations;
- verifies the release and input manifest before starting;
- mounts the release and evidence read-only;
- writes only to one candidate attempt directory;
- uses a release-bound named permission profile with `workspace_write`, never
  full access or approval bypass;
- persists provider events and usage with idempotent event IDs;
- returns a schema-bound candidate manifest;
- cannot access promotion credentials or write workflow/material state.

Luceon remains the only control plane. A kernel-identified control UID may
issue a short-lived, operation-specific capability; only the bound runner UID
may consume it. The broker stores only capability digests, burns a token on
first presentation (including mismatch), and constructs the small allowlisted
App Server method set itself. Raw App Server methods are never accepted from
the control plane or runner. WebSocket transport is not admitted.

The PoC must also prove that prompt-influenced command subprocesses cannot read
the long-lived Codex credential. Mounting `auth.json` into the runner or copying
it into a same-UID `CODEX_HOME` is prohibited. Admission requires a
different-UID scoped one-time broker with bounded token lifetime. Until that
boundary exists and is release-attested, the isolated runner has no project
network and no credential, and Expert policy remains disabled.

Ordinary bounded LLM work remains in the unified Responses gateway and does not
route through the Expert Lane.

## Current implementation/admission status

The repository contains the pinned App Server adapter, exact-method protocol,
digest-only one-time capability broker, kernel peer-UID Unix IPC, capability
schemas, isolated spool protocol, dispatcher, one-shot image, and fail-closed
tests. The dispatcher has Worker V3 DB and candidate-MinIO capability but no
Codex credential. The isolated runner has no raw credential or upstream
network.

Two local proofs have narrowed, but not closed, the gate:

- the pinned `0.144.4` CLI generated its own versioned JSON Schema, and a real
  no-auth, network-disabled App Server accepted the strict-config
  initialize/thread-start sequence without starting a model turn;
- a real permission-profile probe allowed candidate writes while blocking a
  zero-byte credential open, release/input writes, and command network;
- an offline, no-auth, network-disabled Linux proof used three distinct
  non-root UIDs, accepted one bound capability, and rejected its replay.

Neither proof is a sealed-image, release-bound live App Server execution. The
current Compose service deliberately has no credential mount and
`network_mode: none`; it therefore cannot be treated as a working Expert
runtime.

That implementation is not itself a production capability proof. The current
audit release remains `incomplete`, and no `passed` Expert claim is admitted
until a release-bound image digest and real broker/credential, event,
cancellation, resume, terminal-reconciliation, and unauthorized-access proofs
are verified together. A later live proof changes this status only through a
new immutable RC attestation; this ADR must not be read as such an attestation.

## Consequences

- Codex outages do not stop normal deterministic/LLM-bounded work.
- Expert attempts are slower and costlier but fully attributable.
- Expert output follows the same Evaluator and Promotion protocol as every
  other candidate.
- The first RC may ship the lane disabled if the App Server capability gate is
  not met; it may not claim Expert Lane production readiness in that state.

## Rejected alternatives

- Existing `codex exec --dangerously-bypass-approvals-and-sandbox`: rejected
  for privilege, observability, and reproducibility reasons.
- Python Codex SDK as the production runtime: rejected because the required
  observable approval/event/resume/reconciliation contract is an App Server
  integration contract; SDK remains compatibility/test-only.
- Agents SDK/MCP as a second orchestrator: rejected because Luceon already owns
  orchestration, authorization, state, and promotion.
- Responses Skills as the Expert Lane: rejected as the primary expert runtime;
  it remains appropriate for ordinary bounded calls.
