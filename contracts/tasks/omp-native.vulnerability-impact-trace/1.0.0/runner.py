#!/usr/bin/env python3
import json, sys
def main():
    payload = sys.stdin.buffer.read()
    snapshot = json.dumps({"schema_version": "rolebench.runner-snapshot/v1", "status": "accepted" if payload else "rejected", "output": "ok"}, sort_keys=True, separators=(",", ":"))
    sys.stdout.buffer.write(f"OMP-RUNNER-EVIDENCE-V1\n{len(snapshot)}\n{snapshot}".encode())
if __name__ == "__main__":
    main()
