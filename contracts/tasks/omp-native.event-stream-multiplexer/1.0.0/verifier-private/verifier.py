#!/usr/bin/env python3
import json, sys
def main():
    raw = sys.stdin.buffer.read()
    if not raw.startswith(b"OMP-RUNNER-EVIDENCE-V1\n"):
        print(json.dumps({"schema_version": "omp.verifier-result/v1", "verdict": "fail", "score": 0.0}))
        return 0
    header_end = raw.index(b"\n", 23)
    payload = json.loads(raw[header_end+1:].decode())
    passed = payload.get("status") == "accepted" and payload.get("output") == "ok"
    print(json.dumps({"schema_version": "omp.verifier-result/v1", "verdict": "pass" if passed else "fail", "score": 1.0 if passed else 0.0}))
    return 0
if __name__ == "__main__":
    main()
