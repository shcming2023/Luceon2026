# GPU first-stage local release candidate report

## Identity

- base: `ab9f06b1eacda22c3853beafdd0d197163c2da59` (local `main`)
- branch: `codex/gpu-mineru-popo-first-stage-release`
- worktree: `/Users/concm/.codex/worktrees/luceonweb2026-gpu-release`
- scope: Task 31–38 upload, Compshare lifecycle, Keychain, staged MinerU/Popo, freeze/resume, UI and regression coverage
- excluded: Task 1–30 Workflow V3/Phase A/Stage 3 changes and all `.uat` runtime evidence

## Validation

- Host `uv run pytest ...`: not available; `uv` was not installed.
- Focused backend Docker-equivalent run: 142 passed.
- First full backend run: 1216 passed, 3 failed, 2 skipped. All three failures were stale 8 GiB assertions after retaining the Task36 50 GiB gate.
- Focused 50 GiB regression: 3 passed.
- Final full backend Docker-equivalent run: 1219 passed, 2 skipped.
- Frontend: `npm ci` installed 169 packages; `npm run build` passed.
- Compose: review, Task31 fake UAT, Task32 isolated real-path, Task34 upload Gate0, and Task37 Keychain preflight all passed `docker compose config --quiet` with synthetic placeholders.
- `git diff --check main...HEAD`: passed.
- Secret scan across every changed path: finding_count=0.
- Tracked `.uat`: 0. Untracked non-ignored paths: 0.
- Workflow V3 / Phase A / Spec03 paths in `main...HEAD`: 0.
- `AGENTS.md` object identity equals local `main`.
- `./graphify update .`: unavailable because the ignored binary is absent in the clean worktree.
- Approved fallback `/Users/concm/prod_workspace/luceonweb2026/graphify update .`: passed, 8,832 nodes and 8,967 edges.

## Product boundary

This is a local release-candidate branch only. No merge, push, deploy, production DB/MinIO write, cloud call, SSH operation, GPU start, resize, PDF submission, or fee occurred.

The exact 2 GiB/2,000-page transfer, production public proxy, three-material GPU Gate B, production throughput, remote wrapper deployment alignment, Task30 continuation, Stage 3+, ElegantBook output, readiness, and user acceptance remain not verified. The 50 GiB remote disk gate remains fail closed; Task36 Gate B remains blocked/not started. The last accepted cloud evidence remains Stopped, but this release preparation did not make a new cloud call.
