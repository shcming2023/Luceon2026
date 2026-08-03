# ADR-0003: Worker-only production runtime

Status: accepted
Date: 2026-07-26

## Context

Worker V3 must be a product capability that LuceonWeb can operate, observe,
reproduce and deploy without transferring a failed job to Codex. A Codex
runtime fallback would create a second execution dependency with different
credentials, permissions, latency and observability, defeating the purpose of
the code plus bounded-LLM Worker path.

The earlier Expert Lane prototype proved useful architecture questions but did
not establish a reason to make Codex part of the production data plane.

## Decision

The production Worker V3 runtime consists only of:

- deterministic stage code from the immutable skill release;
- schema-bounded, release-bound LLM calls declared by `model_policy`;
- independent Evaluator, Promotion and Projector roles;
- MinIO immutable artifacts and lineage;
- the isolated Overleaf-equivalent compilation adapter;
- database-backed leases, retries, costs and operational evidence.

Codex is not a runtime fallback. A difficult sample has exactly two acceptable
formal terminal outcomes:

- `succeeded`, after the ordinary Producer/Evaluator/Promotion chain passes; or
- `needs_review`, with complete blocking findings, immutable evidence, the
  earliest recovery stage and an actionable human handoff.

`needs_review` is an explicit product result, not an instruction to invoke
another automatic agent. After a human supplies an immutable resolution, the
ordinary Worker restarts from the earliest failed stage and re-enters the same
Evaluator and Promotion gates.

The production API, UI and Compose files expose no Expert trigger, policy,
cancel, resume, broker or runner surface. The ordinary image and release
assembly reject Codex Expert runtime material. RC qualification requires
full-page visual-provider evidence and final-image Spec 05 real-material
evidence; `expert_live_broker` is not a recognized qualification type.

## Experiment retention

The earlier Broker, App Server, SDK, cross-UID and online-proof source may
remain in repository history or explicitly labelled experiment files for
architectural reference. It is not deployed, packaged, imported by production
routes, admitted by release recipes, or used as RC evidence. No production
credential is mounted or copied for that experiment.

## Consequences

- Worker quality and failure semantics must stand on their own.
- Difficult samples may require human work; the system must make that handoff
  complete and unambiguous rather than hiding it behind automatic fallback.
- Codex may still be used by engineers outside the product runtime to diagnose
  defects, review evidence and evolve a future skill release.
- Worker twelve-stage alignment, immutable skill releases, Producer/Evaluator/
  Promotion separation, MinIO, Overleaf, cost accounting and concurrency
  hardening remain mandatory.
