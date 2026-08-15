#!/usr/bin/env python3
"""Baseline probe for custom-memory-heap-crash: emits empty/invalid JSON."""

from __future__ import annotations

import sys


def main() -> None:
    # Emits empty JSON object, which fails schema validation
    sys.stdout.write("{}\n")


if __name__ == "__main__":
    main()
