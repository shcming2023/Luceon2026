# Worker V3 production permission matrix

Status: normative production boundary  
Date: 2026-07-27

Worker V3 uses four ordinary runtime identities plus a bounded model provider.
There is no Codex, Broker, App Server or Expert runtime identity.

## Runtime capabilities

| Capability | Producer | Evaluator | Promoter | Projector | Bounded LLM |
|---|---:|---:|---:|---:|---:|
| Read frozen PDF/MinerU/Popo | exact allowlist | no | no | no | no direct access |
| Read V3 candidate objects | own/retry inputs | yes | yes | yes | no direct access |
| Create V3 candidate objects | conditional create only | no | no | no | no |
| Read V3 formal objects | no | no | no | yes | no |
| Create V3 formal objects | no | no | no | conditional create only | no |
| Delete object/version | denied | denied | denied | denied | no credentials |
| Execute stage program | Producer entrypoint only | Evaluator entrypoint only | no | no | no |
| Call external model endpoint | through gateway when release policy allows | through gateway only for declared review task | no | no | provider endpoint only |
| Create Evaluation | no | yes | no | no | no |
| Create Promotion | no | no | yes | no | no |
| Project `MaterialOutput` | no | no | no | final outbox only | no |
| Record human acceptance | no | no | no | projects an existing user decision only | no |
| Read Codex credential | no | no | no | no | not present |
| Write source/frozen marker/template | denied | denied | denied | denied | no credentials |

## MinIO policy

The four roles use four distinct access-key and secret-key fingerprints. Startup
rejects a missing role, repeated access fingerprint, repeated secret
fingerprint, role mismatch or reuse of the global MinIO credential.

| Role | GetObject allowlist | PutObject allowlist |
|---|---|---|
| Producer | configured frozen source buckets and candidate prefix | candidate prefix |
| Evaluator | candidate prefix | none; explicit deny |
| Promoter | candidate prefix | none; explicit deny |
| Projector | candidate and formal prefixes | formal prefix |

Every role has an explicit deny for `DeleteObject` and
`DeleteObjectVersion`. All unlisted buckets and prefixes are denied by default.
Candidate and formal writers use `If-None-Match: *`; an identical existing
object is accepted only after exact re-read and hash verification, while
different bytes fail closed.

## Control-plane writes

| Record | Writer |
|---|---|
| Job and stage intent | LuceonWeb control plane |
| Execution/heartbeat | role owning the leased operation |
| Candidate record | control plane after Producer result validation |
| Model-call telemetry | unified bounded-LLM gateway |
| Evaluation | Evaluator operation |
| Promotion | Promotion controller after exact passing Evaluation |
| Review resolution | authorized user/admin API with immutable manifest |
| Projection outbox | final promotion/acceptance control-plane transaction |
| Formal object and `MaterialOutput` | Projector reconciler |
| Human acceptance decision | authenticated user action only |

No stage executable, model response or object upload can directly set the job
to successful, make an output current, or create `human_accepted`.

## Bounded LLM restrictions

Each permitted call is release-bound to one provider/model, prompt ID/version/
SHA, strict output JSON Schema, parameters, evidence hashes, retry policy and
budget. The gateway persists response identity, raw and parsed hashes, token
usage, latency, retry/error state, pricing snapshot and estimated cost.

The model may return only the bounded candidate or finding declared by the
stage contract. It cannot:

- freely rewrite the source body;
- select a different prompt, model or release;
- write MinIO, the database or a stage status;
- waive a deterministic gate;
- apply its own candidate;
- promote an artifact;
- accept a delivery.

Malformed output, schema drift, missing evidence/usage, timeout, authorization
failure, rate limit, server error, budget exhaustion or conflicting results
fails closed.

## Network and filesystem

- Installed skill releases are mounted read-only.
- No ordinary role mounts active `~/.codex/skills`, a Codex credential or
  `/var/run/docker.sock`.
- Each role has a separate work root and non-root identity.
- The Overleaf adapter accepts only the compile protocol and receives no
  control-plane or MinIO credential.
- Temporary inter-container connectivity used for qualification is scoped to
  the Worker and adapter and removed after the proof.

## Required live probes

Static policy inspection is not RC evidence. The final environment must prove
the complete positive/negative role matrix, conditional-create enforcement,
bucket versioning, credential separation, absence of deletes, bounded provider
egress, no unexpected mounts/environment variables and no Codex production
surface.
