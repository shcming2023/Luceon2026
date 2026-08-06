# GPU MinerU + Popo first-stage release candidate

## Scope

This branch isolates the Task 31–38 upload and GPU first-stage work. It does not include the unfinished Workflow V3 / Phase A / Stage 3 changes from Task 1–30. Task 30 remains interrupted.

The release candidate covers:

- a shared, configurable PDF upload policy with internal defaults of 2 GiB and 2,000 pages;
- request and GPU batch envelopes of at most five files and 3 GiB;
- parser-before-body file-count and temporary-disk gates, streamed hashing, PDF validation, immutable MinIO input freezing, and duplicate verification;
- official Compshare POST lifecycle operations, Describe-first state handling, durable leases, authenticated complete inventory checks, and fail-closed safe-stop authority;
- host-only macOS Keychain credentials and short-lived read-only Compose secret files;
- serial staged MinerU then Popo processing, independent freezes, byte-level readback, and Popo-only recovery;
- a guarded wrapper pagination transformation candidate.

## Qualification boundary

- The 2 GiB / 2,000-page values are a configuration and resource-gate contract. An exact 2 GiB upload, an actual 2,000-page educational PDF, and the production public proxy are not verified.
- The prior Task 32 four-page PDF run is only a controlled small vertical qualification. It does not establish multi-material robustness or production throughput.
- Task 36 Gate A observed approximately 14.98 GB free remote disk, below the 50 GiB submission gate. Gate B remained blocked/not started and PDF submissions remained zero.
- The Compshare instance was last independently confirmed Stopped. This release preparation performs no cloud call, SSH operation, GPU start, upload, resize, or paid action.
- The CNY 20 hard ceiling and CNY 16 stop-new-material threshold remain runtime policy boundaries; they are not exercised by this branch preparation.
- The wrapper pagination transformer is a local release candidate. Remote source alignment and deployment remain not verified.
- No production DB/MinIO write, merge, push, deploy, Stage 3+, ElegantBook build, readiness decision, or user acceptance is included.

## Credential operation

Use `backend/scripts/compshare_keychain.py` as described in [Task 37 Keychain runtime](worker-v3/task37-keychain-runtime.md). Public/private keys are never committed or passed in Docker environment variables. Compose consumes only the task-owned read-only `/run/secrets/compshare_credentials` file created by the host launcher.

## Safe remote execution order

1. Describe the exact instance and require the authorized prior state.
2. Freeze pricing and budget deadlines; Start at most once.
3. Wait for Running, then verify SSH host identity, GPU, disk, wrapper/MinerU health, and complete authenticated inventories.
4. Require at least 50 GiB free on the actual work filesystem before submitting a PDF.
5. Process at most five PDFs serially, freeze MinerU before Popo, and resume Popo from the frozen MinerU checkpoint.
6. Verify local DB/MinIO bytes and complete remote idle denominators.
7. Stop through the official API and Describe until Stopped. SSH poweroff is never stop evidence.

## Evidence discipline

`.uat/`, runtime volumes, SQLite databases, MinIO data, cookies, browser sessions, Keychain values, host identity configuration, downloaded archives, and provider response bodies are excluded from Git. Tests use only synthetic credentials and sanitized fixtures. Current validation evidence is generated from this clean branch rather than binding long-lived claims to mutable files in the interrupted source worktree.
