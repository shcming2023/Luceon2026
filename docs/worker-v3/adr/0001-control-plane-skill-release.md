# ADR-0001: Luceon control plane with immutable skill release

Status: accepted for implementation
Date: 2026-07-26

## Context

Worker V2.3 already has useful queue, heartbeat, retry, artifact, model-call, and
visual-QA primitives. It also allows a stage producer to mark itself successful,
passes same-kind artifacts without a formal promotion record, projects a final
output before independent full-page review, and maps some handoff states to
passed. The active skills tree is mutable and is not a release artifact.

The ElegantBookCompiler standard requires immutable evidence, explicit
Spec 01–06 boundaries, deterministic execution of a frozen render plan,
independent evaluation, and separate machine/spec/user acceptance states.

## Decision

1. Worker V3 is a separate, versioned control-plane namespace. V2.3 tables,
   APIs, jobs, and outputs remain intact.
2. Every V3 run binds one verified `SkillRelease` by package SHA-256.
3. A stage is a four-part protocol:

   `Execution -> Candidate -> Independent Evaluation -> Promotion`

4. A next stage accepts only the exact hashes referenced by a promotion.
5. Final MaterialOutput projection uses a transactional outbox/reconciler and
   occurs only after final delivery promotion.
6. Twelve persisted V3 stages implement the Spec 01–06 handoffs without
   presenting a lower checkpoint as final completion.
7. V3 artifacts use isolated prefixes and never overwrite V2.3 or historical
   artifacts. The deployed defaults are
   `worker-v3-candidates/v3/candidates/...` for candidates and
   `eduassets-elegantbook/elegantbook/v3/...` for formal projection.
8. Ordinary production runs use formal release entrypoints only. Legacy,
   migration, diagnostic, and prohibited entrypoints remain visible but are
   not selectable by the normal path.
9. Each stage binds two distinct formal executables: a candidate-only Producer
   and a read-only independent Evaluator. The release cannot become executable
   if either role is absent, duplicated, or permission/exit semantics are
   swapped.
10. Four ordinary services—Executor, Evaluator, Promoter, and Projector—run the
    same release-qualified dedicated Worker V3 image with different identities
    and capabilities. Projector is the only material-output writer and acts
    only after final promotion.

## Consequences

- The initial V3 slice prioritizes protocol invariants before full content
  execution.
- A producer that created valid-looking files can still be rejected.
- There is additional storage for immutable attempts and evaluation evidence.
- Release construction and runtime verification become explicit deployment
  responsibilities.
- Existing V2.3 jobs can continue while V3 runs in shadow/cohort mode.

## Rejected alternatives

- Rename/extend the five V2.3 stages: rejected because it preserves producer
  self-attestation and ambiguous historical semantics.
- Run directly from `~/.codex/skills`: rejected because bytes can change after
  task creation and runtime closure is unverifiable.
- Let the model or Codex set final status: rejected because it collapses
  candidate generation with independent acceptance.
