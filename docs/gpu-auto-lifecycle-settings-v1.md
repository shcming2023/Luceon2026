# GPU automatic lifecycle settings v2

This contract makes the runtime settings page the current development entry
point for Compshare lifecycle configuration. It does not qualify real cloud
start/stop behavior and it does not enable automatic lifecycle management on
upgrade.

## Persistence and defaults

- `gpu_runtime_settings` is a singleton, versioned database row. The migration
  creates it with `automatic_enabled=false` and `auto_stop=true`.
- Non-secret identity, the CNY 20 hard ceiling, disk policy and lifecycle
  switches live in that row. Every pipeline run freezes the public snapshot
  and its SHA.
- The current development credential provider is
  `project_secret_file`. The JSON file is outside Git and ordinary backups,
  with a 0700 parent and a 0600 regular, owner-matched, non-symlink file.
- `macos_keychain_secret_file` remains an explicit compatibility provider. No
  provider silently falls back to environment variables, old cookie state or
  another provider.
- The environment lifecycle flag is only a fail-safe kill switch. It cannot
  turn a database-disabled lifecycle on.

## Runtime semantics

- Automatic off never invokes the cloud client. An offline wrapper reports
  `GPU_OFFLINE`; a manually running GPU can still be used and is not stopped.
- Automatic on makes API preflight return `CLOUD_LIFECYCLE_DEFERRED`. The
  material worker then performs Describe-first, qualifies the versioned hourly
  billing contract, receives the official scheduler acknowledgement, then
  independently confirms the exact deadline with a bounded sequence of
  read-only Describe calls before and only then accepts at most one Start.
  A first stale read may be followed by a matching read; missing/drifted
  deadlines, a state change away from `Stopped`, or exhausting the bounded
  read budget fail closed without another scheduler update or Start,
  persists ownership, verifies SSH/GPU/wrapper/inventory/disk, and only then
  submits a PDF.
- Only an instance started from Stopped by the frozen run is lifecycle-owned.
  Successful local MinerU and Popo freezes, empty local/remote denominators and
  the grace period are required before official Stop and Describe-to-Stopped.
- Crash recovery consumes the same frozen snapshot and the same safe-stop
  authority. A persisted `guard_accepted_before_start` checkpoint is never
  trusted by itself: recovery re-Describes the exact instance and reuses the
  guard only when the cloud deadline still matches and has at least 300 seconds
  remaining. Missing, expired or drifted guards are re-armed and re-verified
  before the single Start boundary.
- The page exposes one automatic-management switch. Automatic mode always
  includes safe auto-stop; manual mode never takes lifecycle ownership of an
  instance that a user started.
- Enabling automatic mode is rejected without a complete Region/Zone/Project/
  UHost/SSH identity, valid credential, valid port, auto-stop policy, and an
  inactive host kill switch. Blockers are returned as non-secret codes.

## Disk and budget

The development default is 12 GiB free with an absolute 8 GiB floor, a 2 GiB
reserve and expansion factor 12. Required bytes are recomputed for every
selected batch:

`max(configured_minimum, selected_input_bytes * expansion_factor + reserve)`

The value is deliberately not tied to the observed 14.98 GB instance. Large
inputs remain blocked when the dynamic requirement exceeds actual capacity.
The lifecycle hard ceiling is CNY 20. A Start is forbidden until the exact
hourly price and billing unit produce a cap-derived scheduled stop that stays
within that ceiling.

Billing normalization is `luceon.compshare-billing-normalization/v3`. It
supports explicit Hour/Hourly responses and the official 2026-08-04
`ChargeType=Postpay` plus positive numeric `InstancePrice` (CNY/hour) shape. A
normalized historical receipt is not raw-field evidence, so Postpay does not
fall back to `Price`. Any contradictory unit
field, unknown charge type, or missing/non-positive price fails before the
scheduler mutation and Start.

## Migration and rollback

Before production migration, take and verify a SQLite backup. Applying the
migration only creates the singleton table and an automatic-off row; it does
not call Compshare. Rollback requires automatic management to be off and no
owned lifecycle lease to be active, then downgrades one revision to drop only
`gpu_runtime_settings`. Project secret files are not removed by database
rollback and must be deleted through the exact-confirm API if desired.

## Qualification boundary

The fake-cloud browser chain covers settings, Describe-only connection test,
deferred preflight, Stopped-to-Running, readiness, MinerU/Popo freezes and
safe Stop-to-Stopped. Real Compshare automatic lifecycle, the real 14.98 GB
data path, production deployment and 2 GiB end-to-end upload remain unverified.
The fake UAT runtime also pins `COMPSHARE_ALLOWED_ENDPOINT_ORIGINS` to its local
fake service; an official or arbitrary external origin fails locally before a
transport can be created.
