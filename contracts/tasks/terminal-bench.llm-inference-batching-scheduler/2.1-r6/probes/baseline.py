#!/usr/bin/env python3
"""Baseline admission probe for llm-inference-batching-scheduler: emits empty JSON."""

from __future__ import annotations

import sys


def main() -> None:
    sys.stdout.write("{}\n")


if __name__ == "__main__":
    main()
