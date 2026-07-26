# Worker V3 skill release contract

## Required layout

```text
worker-v3-skill-release/
├── release-manifest.json
├── skills/
├── contracts/
├── schemas/
├── prompts/
├── validators/
├── references/
├── templates/
├── evals/
└── runtime/
```

The archive is deterministic: member order is lexical, timestamps and owners
are normalized, duplicate paths and links are forbidden, and the manifest
defines the canonical tree-hash algorithm. The archive SHA is stored outside
the archive in the release registry because an archive cannot contain its own
final hash without recursion.

## Manifest minimum

- schema version, release ID, SemVer, channel, status, creation time;
- source Git SHA/tag and dirty-state evidence;
- file path, bytes, SHA-256, mode, and role;
- canonical tree SHA-256 and external archive SHA-256;
- formal/legacy/migration/diagnostic/prohibited entrypoint allowlists;
- per-entrypoint stage, explicit execution role, input/output schema,
  permission envelope, timeout, and exit semantics;
- dynamic import/resource closure;
- prompt ID/version/SHA and output schema;
- model policy, limits, retry, and budget;
- approved template archive/tree/main/class/fixed-asset/capability hashes;
- Python, application dependencies, system tools, fonts, TeX, Poppler,
  container image digest, SBOM, and attestations;
- unit/contract/eval/UAT evidence and known gaps;
- V2.3 compatibility and rollback rules.

## Admission rules

- `status` must be `rc` or `stable`; `incomplete` is never executable.
- `rc_eligible` or `stable_eligible` must be true as appropriate.
- Every declared file and tree hash is recomputed at install and at job start.
- The release root becomes read-only before a worker can claim a run.
- Undeclared files, links, path traversal, duplicate paths, or path escape fail
  installation.
- Entrypoints are invoked by argv from the manifest, never by arbitrary shell.
- Every one of the 12 stages has exactly two formal entrypoints: one
  `producer` with `candidate-only` permissions and `candidate_ready` success,
  and one separate `evaluator` executable with `read-only-evaluator`
  permissions and `evaluation_ready` success. Missing, duplicate, role-swapped,
  or shared-executable pairs fail closed.
- Any dependency on an absolute user path, active skill tree, host login, or
  mutable repository path fails release validation.
- `executable_baseline.policy=sole-authority` enumerates the only sources that
  may populate the release execution surface. Every formal entrypoint and every
  file under `scripts/` or a packaged skill's `scripts/` must come from that
  exact set.
- Historical packages may be retained only as unextracted
  `provenance_only` files under `references/provenance/`. Such files cannot be
  executable and cannot satisfy entrypoint, dynamic-resource, runtime,
  identity, attestation, prompt, schema, spec, skill, or template fields.

## Current baseline qualification

The audit recipe now packages 24 formal entrypoints: one Producer and one
independent Evaluator for each of the 12 stages. The six versioned skill
snapshots, Worker V3 adapters/kernels, schemas, prompts, validators, and frozen
template are the sole executable baseline. Historical EBC packages are retained
only as non-executable provenance, and the executable surface has no active
skill or user-home path dependency.

The current audit recipe nevertheless remains `status=incomplete`; source
coverage is not an RC. At the time of this revision its unresolved live
qualification categories are:

1. clean-image full-page provider/reviewer evidence;
2. Spec 05 real-material qualification in the final Worker V3 image;
3. release-level regression, five-material shadow UAT, and final sealed
   no-code/no-build/no-redeploy smoke evidence.

The exact current `--verify-only` output is authoritative. A code change, unit
test, or manually changed status cannot close a live qualification gap; a newly
verified RC recipe must bind the evidence and final image digest.

The deployed object namespaces are also part of the contract:

- candidates: `worker-v3-candidates/v3/candidates/...`;
- formal V3 projection: `eduassets-elegantbook/elegantbook/v3/...`.

Neither namespace may be silently changed to a V2.3 prefix or replaced by a
local directory for release qualification.
