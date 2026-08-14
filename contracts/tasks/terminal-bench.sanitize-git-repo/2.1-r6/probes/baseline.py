#!/usr/bin/env python3
"""Baseline probe: emits an empty diff artifact (no changes made)."""
import sys

def main() -> None:
    # No changes to contaminated files
    sys.stdout.write("")

if __name__ == "__main__":
    main()
