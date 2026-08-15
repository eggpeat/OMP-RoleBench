#!/usr/bin/env python3
"""Empty baseline probe submitting no changes."""

from __future__ import annotations

import sys


def main() -> int:
    # Submits empty payload, leaving workspace run.py untouched as NotImplementedError stub
    sys.stdout.write("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
