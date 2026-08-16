#!/usr/bin/env python3
import json, sys
SNAPSHOT_VERSION = "rolebench.executable-runner-snapshot/v1"
def main():
    payload = sys.stdin.buffer.read()
    status = "accepted" if payload else "rejected"
    snapshot = json.dumps({"schema_version": SNAPSHOT_VERSION, "status": status, "output": "ok"}, sort_keys=True, separators=(",", ":"))
    sys.stdout.buffer.write(f"OMP-RUNNER-EVIDENCE-V1\n{len(snapshot)}\n{snapshot}".encode())
if __name__ == "__main__":
    main()
