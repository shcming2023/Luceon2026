#!/usr/bin/env python3
"""Print only non-secret Compshare runtime readiness.

Load secrets through the short-lived COMPSHARE_CREDENTIALS_FILE contract.
This command never prints, copies, or persists credential values.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.compshare_lifecycle import CompShareConfig, CompShareLifecycleError


def main() -> int:
    try:
        config = CompShareConfig.from_env()
    except CompShareLifecycleError as exc:
        print(
            json.dumps(
                {
                    "schema": "luceon.compshare-runtime-preflight/v1",
                    "ready": False,
                    "credential_source": "invalid_or_unavailable",
                    "error_code": exc.code,
                    "secret_returned": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    missing = config.missing_fields()
    payload = {
        "schema": "luceon.compshare-runtime-preflight/v1",
        "ready": not missing,
        "present": {
            "public_key": bool(config.public_key),
            "private_key": bool(config.private_key),
            "region": bool(config.region),
            "zone": bool(config.zone),
            "project_id": bool(config.project_id),
            "uhost_id": bool(config.uhost_id),
        },
        "missing": missing,
        "public_identity": config.public_identity(),
        "credential_source": config.credential_source,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
