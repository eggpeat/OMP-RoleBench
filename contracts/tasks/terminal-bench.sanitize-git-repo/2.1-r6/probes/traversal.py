#!/usr/bin/env python3
"""Traversal probe: attempts directory traversal escape outside workspace."""
import sys

PATCH_DATA = "--- a/dclm/../clean_sentinel.txt\n+++ b/dclm/../clean_sentinel.txt\n@@ -1,1 +1,1 @@\n-clean-sentinel-v1\n+escaped\n"

def main() -> None:
    sys.stdout.write(PATCH_DATA)

if __name__ == "__main__":
    main()
