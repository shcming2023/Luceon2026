#!/usr/bin/env python3
"""Dedicated Worker V3 promotion-controller process."""

from __future__ import annotations

import sys

from workflow_v3_evaluator import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], forced_role="promote"))
