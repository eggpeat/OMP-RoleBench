#!/usr/bin/env python3
"""Baseline admission probe for db-wal-recovery: emits unrecovered baseline submission."""

import sys

SUBMISSION = '{\n  "schema_version": "rolebench.db-wal-recovery-submission/v1",\n  "diagnosis": {\n    "sidecar_file": "main.db-wal",\n    "observed_magic_hex": "753d44c0",\n    "expected_magic_hex": "377f0682",\n    "corruption_mechanism": "header-truncation"\n  },\n  "hypothesis_testing": [\n    {\n      "hypothesis_id": "hyp-truncate-header",\n      "transform_kind": "shift",\n      "parameter_int": 0,\n      "resulting_magic_hex": "753d44c0",\n      "wal_magic_valid": false\n    },\n    {\n      "hypothesis_id": "hyp-no-op",\n      "transform_kind": "xor",\n      "parameter_int": 0,\n      "resulting_magic_hex": "753d44c0",\n      "wal_magic_valid": false\n    }\n  ],\n  "selected_transform": {\n    "transform_kind": "xor",\n    "parameter_int": 0,\n    "target_magic_hex": "753d44c0"\n  },\n  "recovery_verification": {\n    "base_record_count": 5,\n    "recovered_record_count": 5,\n    "checkpoint_applied": false\n  },\n  "recovered_records": [\n    {\n      "id": 1,\n      "name": "apple",\n      "value": 100\n    },\n    {\n      "id": 2,\n      "name": "banana",\n      "value": 200\n    },\n    {\n      "id": 3,\n      "name": "cherry",\n      "value": 300\n    },\n    {\n      "id": 4,\n      "name": "date",\n      "value": 400\n    },\n    {\n      "id": 5,\n      "name": "elderberry",\n      "value": 500\n    }\n  ]\n}'

def main() -> None:
    sys.stdout.write(SUBMISSION)

if __name__ == "__main__":
    main()
