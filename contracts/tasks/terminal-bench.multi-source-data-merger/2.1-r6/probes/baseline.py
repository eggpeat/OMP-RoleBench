#!/usr/bin/env python3
"""Deterministic baseline probe for the multi-source merger task."""

from __future__ import annotations

import sys

PAYLOAD = '{"conflict_report":{"conflicts":[],"total_conflicts":0},"merged_users":[],"schema_version":"rolebench.multi-source-submission/v1"}'

def main() -> None:
    sys.stdout.write(PAYLOAD)

if __name__ == "__main__":
    main()
