# Task 37 macOS Keychain credential runtime

## Scope

This contract is for local macOS development and isolated Compose runs only. It does not qualify production secret management, cloud lifecycle behavior, or Task 36 Gate A/B.

The default credential path is:

```text
macOS Keychain
  service: com.luceonweb2026.compshare.v1
  accounts: api-public-key, api-private-key
        ↓ host launcher (foreground lease)
0700 task directory / 0600 expiring JSON file
        ↓ read-only Docker secret at /run/secrets/compshare_credentials
existing CompShareConfig / lifecycle / stale-reaper code
```

Region, Zone, ProjectId, UHostId, API endpoint and SSH endpoint are non-secret identity configuration. They are stored separately in a 0600 versioned config under `~/Library/Application Support/LuceonWeb2026/`.

## Commands

Install the host-only dependency with `python -m pip install -r backend/requirements-keychain-macos.txt`. It is deliberately separate from the Linux backend/Worker lock. The active keyring backend must be `keyring.backends.macOS.Keyring`.

Initial entry (TTY input is hidden; values are never command arguments):

```bash
python backend/scripts/compshare_keychain.py bootstrap \
  --region REGION --zone ZONE --project-id PROJECT_ID --uhost-id UHOST_ID \
  --ssh-host SSH_HOST --ssh-port SSH_PORT
```

Use `update` with the same non-secret identity flags for an explicit credential rotation. This updates only the local Keychain copies; it does not rotate or disable the control-console key.

Status and fail-closed preflight return only `present/missing/locked_or_denied`, public instance identity, and a ProjectId hash:

```bash
python backend/scripts/compshare_keychain.py status
python backend/scripts/compshare_keychain.py preflight
```

Delete requires exact confirmation and removes only the two project-specific Keychain items and local non-secret identity config:

```bash
python backend/scripts/compshare_keychain.py delete \
  --confirm DELETE_LUCEON_COMPSHARE_KEYCHAIN_ITEMS
```

Run a host process under a short credential lease:

```bash
python backend/scripts/compshare_keychain.py run -- \
  python backend/scripts/compshare_runtime_preflight.py
```

Run isolated Compose in the foreground so the secret lease covers the complete service lifetime:

```bash
python backend/scripts/compshare_keychain.py run -- \
  docker compose -f docker-compose.task37-keychain-uat.yml \
  up --abort-on-container-exit --exit-code-from preflight
```

Detached `docker compose up -d` is rejected because it could outlive the secret-file lease. For foreground `compose up`, the launcher runs `compose down --remove-orphans` against the same project/file before deleting the secret. It forwards SIGINT/SIGTERM, keeps the secret available until the child and its safe-stop path finish, and removes only its exact task-owned runtime directory afterward. Startup stale cleanup is fail closed when a matching process, file lock, or Docker mount may still consume the file.

The historical plaintext environment source exists only behind explicit `COMPSHARE_ALLOW_LEGACY_ENV=true`; it is not the default development path. Compose services consume the fixed `/run/secrets/compshare_credentials` file and do not receive public/private keys in container environment variables.

## Restart integration

A future LaunchAgent may invoke the same foreground launcher after macOS login, rebuilding the runtime file from Keychain. Task 37 does not install or enable a LaunchAgent. First-time entry and future control-console key rotation still require one trusted human bootstrap; ordinary development runs do not require another web-console login while the Keychain items remain accessible.

## Failure behavior

Missing items, locked/denied Keychain access, a non-macOS keyring backend, schema/version drift, an expired file, unsafe permissions/owner, a symlink, or an unknown stale-runtime owner all fail closed before any cloud call. Error and status output contain codes and presence flags only.
